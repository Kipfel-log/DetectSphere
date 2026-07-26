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


class AutoLabelWorker(QThread):
    """自动 AI 预标注工作线程。

    对给定图片列表跑推理，并将推理得到的框保存为 YOLO txt 标注文件。
    """

    progress = Signal(int, int)  # (done, total)
    finished_autolabel = Signal(int)  # count of images labeled
    failed = Signal(str)

    def __init__(
        self,
        project,
        model_path: Path,
        image_paths: list[Path],
        conf: float = 0.35,
        iou: float = 0.7,
        only_unlabeled: bool = True,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._model_path = Path(model_path)
        self._image_paths = list(image_paths)
        self._conf = conf
        self._iou = iou
        self._only_unlabeled = only_unlabeled

    def run(self) -> None:
        try:
            from yolo_studio.core.inference import Predictor, results_to_boxes
            from yolo_studio.core.dataset import save_boxes_for_image
            from yolo_studio.core.io.labels import has_label_file

            predictor = Predictor(self._model_path, conf=self._conf, iou=self._iou)
            count = 0
            total = len(self._image_paths)

            for i, img_path in enumerate(self._image_paths):
                # 如果设置只对未标注执行，检查同名 txt 文件
                # dataset/unassigned 里的标注放在 dataset/unassigned/labels，其他在 train/val/test/labels
                # save_boxes_for_image 能自动确定位置
                if self._only_unlabeled:
                    from yolo_studio.core.dataset import get_split_for_image
                    split = get_split_for_image(self._project, img_path)
                    if split == "unassigned":
                        lbl_path = self._project.images_dir.parent / "labels" / (img_path.stem + ".txt")
                    else:
                        lbl_dir = getattr(self._project, f"{split}_labels")
                        lbl_path = lbl_dir / (img_path.stem + ".txt")
                    if lbl_path.exists():
                        self.progress.emit(i + 1, total)
                        continue

                results = predictor.predict_image(img_path)
                boxes = results_to_boxes(results)
                if boxes:
                    from yolo_studio.core.dataset import get_split_for_image
                    split = get_split_for_image(self._project, img_path)
                    save_boxes_for_image(self._project, split, img_path.name, boxes)
                    try:
                        from yolo_studio.core.db import ProjectDB
                        db = ProjectDB(self._project.db_path)
                        img_id = db.upsert_image(str(img_path.resolve()))
                        db.set_is_ai(img_id, True)
                    except Exception:
                        pass
                    count += 1

                self.progress.emit(i + 1, total)

            self.finished_autolabel.emit(count)
        except Exception as e:
            import traceback
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")