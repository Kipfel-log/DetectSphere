"""后台训练 QThread。

信号:
  started_train(dict)  — cfg 字典
  metrics(dict)        — 每 epoch {'epoch': int, 'metrics': {...}}
  log(str)             — 日志消息
  finished_train(dict) — 训练完成 {'best_path', 'last_path', 'save_dir', 'results_csv'}
  failed(str)          — 异常消息

取消:
  request_stop() — 通过 trainer.stop = True,Ultralytics 会在下个 epoch 边界安全停止
"""
from __future__ import annotations

import logging
import threading
import time
from queue import Queue
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from yolo_studio.core.train import TrainConfig, run_training


# Ultralytics 默认 logger 触发很多 INFO
ULTRALYTICS_LOGGERS = [
    "ultralytics",
    "ultralytics.yolo",
    "ultralytics.yolo.engine",
    "ultralytics.yolo.utils",
]


class _QueueLogHandler(logging.Handler):
    """把日志记录塞进队列,主线程拉取后更新 UI。"""

    def __init__(self, q: Queue) -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._q.put(("log", msg))
        except Exception:
            pass


class TrainingWorker(QThread):
    """后台训练线程。"""

    started_train = Signal(dict)  # cfg (as dict,不含 Project 对象)
    metrics = Signal(dict)        # {'epoch': int, 'metrics': {key: value, ...}}
    log = Signal(str)
    finished_train = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg: TrainConfig, parent: QThread | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._log_queue: Queue = Queue()
        self._log_handler: Optional[_QueueLogHandler] = None
        self._trainer_ref: list = [None]  # 用 list 当 mutable ref,避免 nonlocal
        self._stop_requested = False
        self._cancel_poll_thread: Optional[threading.Thread] = None

    # ---- 公开 API ----
    def request_stop(self) -> None:
        """请求安全停止。Ultralytics 在下一个 epoch 边界停。"""
        self._stop_requested = True
        trainer = self._trainer_ref[0]
        if trainer is not None:
            try:
                trainer.stop = True
            except Exception:
                pass

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    # ---- 主循环 ----
    def run(self) -> None:
        self.started_train.emit(self._safe_cfg_dict())

        # 装上日志 handler
        self._log_handler = _QueueLogHandler(self._log_queue)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        self._log_handler.setFormatter(formatter)
        for name in ULTRALYTICS_LOGGERS:
            lg = logging.getLogger(name)
            lg.addHandler(self._log_handler)
            lg.setLevel(logging.INFO)

        try:
            result = run_training(self.cfg, callback=self._on_epoch)
            # 复制 best.pt 到 models/ 目录
            if result.get("best_path") and self.cfg.project.models_dir.exists():
                import shutil

                dst = self.cfg.project.models_dir / f"{self.cfg.effective_run_name()}.pt"
                try:
                    shutil.copy2(result["best_path"], dst)
                    result["registered_path"] = dst
                except Exception:
                    result["registered_path"] = None
            else:
                result["registered_path"] = None
            self.finished_train.emit(result)
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self.log.emit(f"\n[ERROR] {e}\n{tb}\n")
            self.failed.emit(str(e))
        finally:
            # 拆 handler
            if self._log_handler is not None:
                for name in ULTRALYTICS_LOGGERS:
                    try:
                        logging.getLogger(name).removeHandler(self._log_handler)
                    except Exception:
                        pass
            # 把队列尾巴发完
            self._drain_log_queue(timeout=0.5)

    # ---- 回调 ----
    def _on_epoch(self, epoch: int, metrics: dict, trainer=None) -> None:
        # 保存 trainer 引用,供 request_stop() 用
        if trainer is not None:
            self._trainer_ref[0] = trainer
        # 跨线程:实际 callback 在 worker 线程跑,emit 会自动用 queued connection
        self.metrics.emit({"epoch": epoch, "metrics": metrics})
        # 抽干日志队列
        self._drain_log_queue(timeout=0.01)

    def _drain_log_queue(self, *, timeout: float) -> None:
        try:
            while True:
                kind, val = self._log_queue.get_nowait()
                if kind == "log":
                    self.log.emit(val)
        except Exception:
            pass

    def _safe_cfg_dict(self) -> dict:
        """cfg 转 dict(去掉 Project 引用,安全可序列化)。"""
        d = {
            "base_model": self.cfg.base_model,
            "resume_from": self.cfg.resume_from,
            "epochs": self.cfg.epochs,
            "batch": self.cfg.batch,
            "imgsz": self.cfg.imgsz,
            "device": self.cfg.device,
            "patience": self.cfg.patience,
            "save_period": self.cfg.save_period,
            "run_name": self.cfg.effective_run_name(),
            "augment": self.cfg.augment,
            "data_yaml": self.cfg.effective_data_yaml(),
            "project_path": str(self.cfg.project.root),
        }
        return d
