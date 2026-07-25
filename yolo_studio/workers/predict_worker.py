"""预测 Worker(QThread) — 避免主线程被推理阻塞。

支持两种模式:
- 单张:predict_one(image_path) → emit finished(result_path, results)
- 摄像头流:predict_frame_loop(capture, model_path) → emit frame(QImage) 直到 stop

设计:
- 一次性 QThread,每个预测任务新建一个
- Predictor 实例在线程内构造,主线程不持有(避免跨线程竞争)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage


class _OneShotSignals(QObject):
    finished = Signal(object, object)  # (image_path, results list)


class OneShotPredictWorker(QThread):
    """单张/单批预测的工作线程。"""

    progress = Signal(int, int)  # done, total
    finished_batch = Signal(list)  # list of (path, results)
    failed = Signal(str)

    def __init__(self, model_path: Path, items: list[Path], conf: float, iou: float, parent=None) -> None:
        super().__init__(parent)
        self._model_path = Path(model_path)
        self._items = list(items)
        self._conf = conf
        self._iou = iou

    def run(self) -> None:
        try:
            from yolo_studio.core.inference import Predictor

            p = Predictor(self._model_path, conf=self._conf, iou=self._iou)
            out = []
            for i, path in enumerate(self._items):
                results = p.predict_image(path)
                out.append((path, results))
                self.progress.emit(i + 1, len(self._items))
            self.finished_batch.emit(out)
        except Exception as e:
            import traceback
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class CameraFrameWorker(QThread):
    """摄像头帧流工作线程。

    在线程内:
    - 持有 cv2.VideoCapture(自己打开)
    - 持有 Predictor 实例
    - 循环:read → predict → emit QImage
    - 主线程 stop() 后退出循环
    """

    frame_ready = Signal(QImage, list)  # (annotated_qimage, raw_results)
    fps_updated = Signal(float)
    failed = Signal(str)

    def __init__(
        self,
        model_path: Path,
        camera_id: int = 0,
        conf: float = 0.25,
        iou: float = 0.7,
        imgsz: int = 640,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._model_path = Path(model_path)
        self._camera_id = int(camera_id)
        self._conf = conf
        self._iou = iou
        self._imgsz = imgsz
        self._running = False

    def stop(self) -> None:
        self._running = False

    def set_conf(self, conf: float) -> None:
        self._conf = float(conf)

    def set_iou(self, iou: float) -> None:
        self._iou = float(iou)

    def run(self) -> None:
        import cv2
        from yolo_studio.core.inference import Predictor

        cap: Optional[cv2.VideoCapture] = None
        predictor: Optional[Predictor] = None
        try:
            predictor = Predictor(
                self._model_path,
                conf=self._conf,
                iou=self._iou,
                imgsz=self._imgsz,
            )
            cap = cv2.VideoCapture(self._camera_id)
            if not cap.isOpened():
                self.failed.emit(f"无法打开摄像头 {self._camera_id}")
                return

            self._running = True
            last_fps_t = time.perf_counter()
            frames = 0
            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue

                # 更新 conf/iou
                predictor.set_conf(self._conf)
                predictor.set_iou(self._iou)

                results = predictor.predict_array(frame)
                annotated = Predictor.draw_boxes(frame, results, predictor.class_names)

                # BGR → RGB → QImage
                rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                qimg = qimg.copy()  # detach from numpy buffer

                self.frame_ready.emit(qimg, results)

                frames += 1
                now = time.perf_counter()
                if now - last_fps_t >= 1.0:
                    fps = frames / (now - last_fps_t)
                    self.fps_updated.emit(fps)
                    last_fps_t = now
                    frames = 0
        except Exception as e:
            import traceback

            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        finally:
            if cap is not None:
                cap.release()