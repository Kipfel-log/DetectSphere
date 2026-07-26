"""推理封装 — Ultralytics Predictor 的薄包装。

设计目标:
- 一处构造、缓存 YOLO 模型(避免每次预测都加载)
- 接口简单:predict_image / predict_frame 返回统一的 results 列表
- 支持滑块调整 conf / iou
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


class Predictor:
    """单模型推理器。线程安全建议:**不要跨线程共享**实例。"""

    def __init__(
        self,
        model_path: Path,
        *,
        conf: float = 0.25,
        iou: float = 0.7,
        device: str = "cpu",
        imgsz: int = 640,
    ) -> None:
        from ultralytics import YOLO

        self.model_path = Path(model_path)
        self.conf = float(conf)
        self.iou = float(iou)
        self.device = device
        self.imgsz = imgsz
        self._model = YOLO(str(self.model_path))

    @property
    def class_names(self) -> dict[int, str]:
        names = self._model.names
        if isinstance(names, dict):
            return {int(k): str(v) for k, v in names.items()}
        return {i: str(n) for i, n in enumerate(names)}

    def set_conf(self, conf: float) -> None:
        self.conf = float(conf)

    def set_iou(self, iou: float) -> None:
        self.iou = float(iou)

    def predict_image(self, image_path: Path):
        """对单张图片预测(给文件路径)。"""
        results = self._model.predict(
            source=str(image_path),
            conf=self.conf,
            iou=self.iou,
            device=self.device,
            imgsz=self.imgsz,
            verbose=False,
        )
        return results

    def predict_array(self, frame: np.ndarray):
        """对一帧 ndarray 预测(BGR 图像)。"""
        results = self._model.predict(
            source=frame,
            conf=self.conf,
            iou=self.iou,
            device=self.device,
            imgsz=self.imgsz,
            verbose=False,
        )
        return results

    def predict_folder(
        self,
        folder: Path,
        *,
        extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp"),
        progress_cb=None,
    ) -> list:
        """对文件夹中所有图片预测。

        progress_cb(done, total) 在每张图完成后调用(可选)。
        返回 list of (image_path, results)。
        """
        images = sorted(
            p for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        )
        out = []
        for i, p in enumerate(images):
            res = self.predict_image(p)
            out.append((p, res))
            if progress_cb:
                progress_cb(i + 1, len(images))
        return out

    @staticmethod
    def draw_boxes(
        frame: np.ndarray,
        results,
        class_names: Optional[dict[int, str]] = None,
    ) -> np.ndarray:
        """把 Ultralytics 结果绘制到 frame 上(BGR),返回带框的图。"""
        try:
            from ultralytics.utils.plotting import Annotator
            import cv2

            names = class_names if class_names is not None else (results[0].names if results else {})
            annotator = Annotator(frame, line_width=2, font_size=1.0, example=str(names))
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    label = f"{names.get(cls_id, str(cls_id))} {conf:.2f}"
                    annotator.box_label((x1, y1, x2, y2), label=label, color=None)
            return annotator.result()
        except Exception:
            # 兜底:返回原图
            return frame


def results_to_boxes(results, class_name_mapping: dict[str, int] | None = None) -> list:
    """将 Ultralytics predict 返回的 results 列表转换为项目内部 Box 对象列表。

    如果提供 class_name_mapping ({class_name: project_class_id}),将尝试按照类别名称映射到项目 class_id。
    """
    from yolo_studio.core.io.labels import Box

    boxes: list[Box] = []
    if not results:
        return boxes
    for r in results:
        if r.boxes is None:
            continue
        xywhn = r.boxes.xywhn.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy()
        names = getattr(r, "names", {}) or {}
        for b, c in zip(xywhn, classes):
            model_cls_id = int(c)
            final_cls_id = model_cls_id
            if class_name_mapping and names:
                cls_name = names.get(model_cls_id)
                if cls_name in class_name_mapping:
                    final_cls_id = class_name_mapping[cls_name]

            boxes.append(
                Box(
                    class_id=final_cls_id,
                    xc=float(b[0]),
                    yc=float(b[1]),
                    w=float(b[2]),
                    h=float(b[3]),
                )
            )
    return boxes