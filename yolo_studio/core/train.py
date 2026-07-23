"""Ultralytics YOLOv8 训练封装。

设计:
- TrainConfig 数据类(镜像原 scripts/train.py 的 kwargs,行为一致)
- run_training(cfg, callback) 同步执行(在 worker 线程里跑,不阻塞 UI)
- 通过 Ultralytics 的 add_callback('on_train_epoch_end', ...) 在每个 epoch
  结束时提取 metrics,跨线程通过回调参数推给主线程
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import torch

from yolo_studio.core.project import Project


@dataclass
class TrainConfig:
    """训练配置 — 字段对齐 Ultralytics 8.x train() kwargs。"""

    project: Project

    # 模型
    base_model: str = "yolov8n.pt"  # 或本地路径 / 项目内 models/*.pt
    resume_from: str = ""  # 非空表示从某个 ckpt 续训

    # 数据
    data_yaml: str = ""  # 默认 = project.dataset_yaml

    # 训练超参
    epochs: int = 100
    batch: int = 16
    imgsz: int = 640
    device: str = "auto"  # 'auto' / 'cpu' / '0' / '0,1'
    patience: int = 50
    save_period: int = 10

    # 输出
    run_name: str = ""  # 空 = 自动 pen_<timestamp>

    # 增强
    augment: bool = True

    def effective_data_yaml(self) -> str:
        return self.data_yaml or str(self.project.dataset_yaml)

    def effective_run_name(self) -> str:
        return self.run_name or f"train_{int(time.time())}"

    def effective_device(self) -> str:
        if self.device == "auto":
            return "0" if torch.cuda.is_available() else "cpu"
        return self.device

    def runs_root(self) -> Path:
        return self.project.runs_dir / "train"

    def save_dir(self) -> Path:
        return self.runs_root() / self.effective_run_name()


# 回调函数签名:接受 (epoch:int, metrics:dict, trainer:optional)
# trainer 用于在 worker 里设置 trainer.stop = True 实现安全取消
EpochCallback = Callable[..., None]


def _extract_metrics(trainer) -> dict:
    """从 Ultralytics trainer 中提取当前 epoch 的指标。

    trainer.metrics 是 Ultralytics 维护的 dict,键类似:
      train/box_loss, train/cls_loss, train/dfl_loss,
      val/box_loss, val/cls_loss, val/dfl_loss,
      metrics/precision(B), metrics/recall(B),
      metrics/mAP50(B), metrics/mAP50-95(B)

    Ultralytics CSV 列名带前导空格,我们 strip 一下。
    """
    raw = getattr(trainer, "metrics", {}) or {}
    cleaned: dict[str, float] = {}
    for k, v in raw.items():
        try:
            cleaned[k.strip()] = float(v)
        except (TypeError, ValueError):
            continue
    return cleaned


def run_training(
    cfg: TrainConfig,
    callback: Optional[EpochCallback] = None,
) -> dict:
    """同步训练(在调用方线程跑)。返回最终结果 dict。

    callback 签名:
      callback(epoch: int, metrics: dict, trainer: BaseTrainer)
    trainer 参数用于安全停止(trainer.stop = True)。
    """
    from ultralytics import YOLO

    # 加载模型
    if cfg.resume_from:
        model = YOLO(cfg.resume_from)
    else:
        model = YOLO(cfg.base_model)

    # 注册 epoch-end 回调
    def _on_epoch_end(trainer):
        epoch = getattr(trainer, "epoch", None)
        if epoch is None:
            try:
                epoch = trainer.trained_epoch + 1
            except AttributeError:
                epoch = -1
        metrics = _extract_metrics(trainer)
        if callback is not None:
            try:
                callback(int(epoch), metrics, trainer)
            except Exception:
                # 回调异常不应中断训练
                pass

    model.add_callback("on_train_epoch_end", _on_epoch_end)

    save_dir = cfg.save_dir()
    save_dir.mkdir(parents=True, exist_ok=True)

    # 调用 Ultralytics
    results = model.train(
        data=cfg.effective_data_yaml(),
        epochs=cfg.epochs,
        batch=cfg.batch,
        imgsz=cfg.imgsz,
        device=cfg.effective_device(),
        project=str(cfg.runs_root()),
        name=cfg.effective_run_name(),
        patience=cfg.patience,
        save_period=cfg.save_period,
        exist_ok=True,
        pretrained=True,
        verbose=True,
        augment=cfg.augment,
    )

    best_path = Path(results.save_dir) / "weights" / "best.pt"
    last_path = Path(results.save_dir) / "weights" / "last.pt"
    csv_path = Path(results.save_dir) / "results.csv"

    return {
        "save_dir": Path(results.save_dir),
        "best_path": best_path if best_path.exists() else None,
        "last_path": last_path if last_path.exists() else None,
        "results_csv": csv_path if csv_path.exists() else None,
    }


def parse_final_metrics_from_csv(csv_path: Path | None) -> dict:
    """从 results.csv 读最后一行作为最终指标(供模型注册用)。"""
    if not csv_path or not csv_path.exists():
        return {}
    try:
        import pandas as pd

        df = pd.read_csv(csv_path)
        df.columns = [c.strip() for c in df.columns]
        if df.empty:
            return {}
        last = df.iloc[-1].to_dict()
        # 浮点化
        return {k: float(v) if _is_num(v) else v for k, v in last.items()}
    except Exception:
        return {}


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
