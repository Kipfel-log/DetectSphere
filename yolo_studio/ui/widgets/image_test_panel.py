"""ImageTestPanel — 单图测试面板。

功能:
- 支持用户选择本地图片(支持拖拽/对话框选择)
- 选择项目模型或浏览外部模型
- 调节 conf/iou 阈值
- 展示带画框图像、预测类别列表与置信度
- 支持保存导出画框后的预测图
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from yolo_studio.core.model_registry import load_registry, scan_models
from yolo_studio.core.project import Project
from yolo_studio.workers.predict_worker import OneShotPredictWorker


class ImageTestPanel(QWidget):
    """单图测试面板。"""

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self._current_image_path: Optional[Path] = None
        self._current_qimage: Optional[QImage] = None
        self._worker: Optional[OneShotPredictWorker] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        # ---- 左: 图像画面 ----
        left = QVBoxLayout()
        self.image_label = QLabel()
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
            "border: 1px solid #555;"
        )
        self.image_label.setText("请选择图片进行推理测试")
        self.image_label.setScaledContents(True)
        left.addWidget(self.image_label, 1)

        outer.addLayout(left, 1)

        # ---- 右: 控制与结果 ----
        right = QVBoxLayout()
        right.setSpacing(8)

        right.addWidget(StrongBodyLabel("单图推理设置"))

        form = QFormLayout()

        # 图片选择
        img_row = QHBoxLayout()
        self.select_img_btn = PushButton(FIF.FOLDER, "选择图片")
        self.select_img_btn.clicked.connect(self._on_select_image)
        img_row.addWidget(self.select_img_btn)
        self.img_path_label = CaptionLabel("未选择文件")
        self.img_path_label.setWordWrap(True)
        img_row.addWidget(self.img_path_label, 1)
        form.addRow("目标图像:", img_row)

        # 模型选择
        self.model_combo = ComboBox()
        self.refresh_models()
        form.addRow("测试模型:", self.model_combo)

        right.addLayout(form)

        # conf slider
        conf_box = QVBoxLayout()
        conf_box.setSpacing(2)
        conf_box.addWidget(CaptionLabel("置信度阈值 (conf)"))
        conf_row = QHBoxLayout()
        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(5, 95)
        self.conf_slider.setValue(25)
        conf_row.addWidget(self.conf_slider, 1)
        self.conf_label = BodyLabel("0.25")
        self.conf_label.setMinimumWidth(40)
        conf_row.addWidget(self.conf_label)
        conf_box.addLayout(conf_row)
        self.conf_slider.valueChanged.connect(lambda v: self.conf_label.setText(f"{v / 100:.2f}"))
        right.addLayout(conf_box)

        # iou slider
        iou_box = QVBoxLayout()
        iou_box.setSpacing(2)
        iou_box.addWidget(CaptionLabel("IoU 阈值 (iou)"))
        iou_row = QHBoxLayout()
        self.iou_slider = QSlider(Qt.Orientation.Horizontal)
        self.iou_slider.setRange(10, 95)
        self.iou_slider.setValue(70)
        iou_row.addWidget(self.iou_slider, 1)
        self.iou_label = BodyLabel("0.70")
        self.iou_label.setMinimumWidth(40)
        iou_row.addWidget(self.iou_label)
        iou_box.addLayout(iou_row)
        self.iou_slider.valueChanged.connect(lambda v: self.iou_label.setText(f"{v / 100:.2f}"))
        right.addLayout(iou_box)

        # 推理按钮
        btn_row = QHBoxLayout()
        self.run_btn = PrimaryPushButton(FIF.PLAY, "开始推理")
        self.run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self.run_btn)

        self.save_btn = PushButton(FIF.SAVE, "保存预测图")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save_result)
        btn_row.addWidget(self.save_btn)
        right.addLayout(btn_row)

        right.addSpacing(8)
        right.addWidget(StrongBodyLabel("检测结果列表"))
        self.result_list = QListWidget()
        right.addWidget(self.result_list, 1)

        outer.addLayout(right, 0)

    def refresh_models(self) -> None:
        """刷新模型下拉。"""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        try:
            scan_models(self.project)
            reg = load_registry(self.project)
            active = reg.active_model
            for entry in reg.models:
                label = entry.name + (" ★" if entry.name == active else "")
                self.model_combo.addItem(label, userData=str(self.project.models_dir / entry.name))
        except Exception:
            pass
        self.model_combo.blockSignals(False)

    def _selected_model_path(self) -> Optional[Path]:
        p = self.model_combo.currentData()
        return Path(p) if p else None

    def _on_select_image(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            str(self.project.root_dir),
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp)",
        )
        if not path_str:
            return
        path = Path(path_str)
        self._current_image_path = path
        self.img_path_label.setText(path.name)
        pix = QPixmap(str(path))
        if not pix.isNull():
            self.image_label.setPixmap(pix)
            self._current_qimage = pix.toImage()
        self.save_btn.setEnabled(False)
        self.result_list.clear()

    def _on_run(self) -> None:
        if self._current_image_path is None or not self._current_image_path.exists():
            InfoBar.warning(
                title="未选择图片",
                content="请先点击「选择图片」按钮指定测试图像",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        model_path = self._selected_model_path()
        if model_path is None or not model_path.exists():
            InfoBar.error(
                title="未选择模型",
                content="请在下拉框中选择一个有效的 .pt 模型",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        self.run_btn.setEnabled(False)
        self.image_label.setText("正在推理中...")

        conf = self.conf_slider.value() / 100
        iou = self.iou_slider.value() / 100

        self._worker = OneShotPredictWorker(
            model_path=model_path,
            items=[self._current_image_path],
            conf=conf,
            iou=iou,
            parent=self,
        )
        self._worker.finished_batch.connect(self._on_predict_finished)
        self._worker.failed.connect(self._on_predict_failed)
        self._worker.start()

    @Slot(list)
    def _on_predict_finished(self, out: list) -> None:
        self.run_btn.setEnabled(True)
        if not out:
            return
        image_path, results = out[0]

        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        from yolo_studio.core.inference import Predictor

        try:
            pil_img = Image.open(str(image_path))
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            rgb_frame = np.array(pil_img)
            frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
        except Exception:
            frame = cv2.imread(str(image_path))

        if frame is None:
            return

        predictor_names = results[0].names if results else {}
        annotated = Predictor.draw_boxes(frame, results, predictor_names)
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

        self._current_qimage = qimg
        scaled = qimg.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(QPixmap.fromImage(scaled))
        self.save_btn.setEnabled(True)

        # 填充检测结果列表
        self.result_list.clear()
        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            names = results[0].names
            for i, box in enumerate(boxes):
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                name = names.get(cls_id, str(cls_id))
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                item_str = f"#{i+1} [{name}]  conf: {conf:.2f}  loc: ({x1},{y1})-({x2},{y2})"
                self.result_list.addItem(QListWidgetItem(item_str))
            if len(boxes) == 0:
                self.result_list.addItem(QListWidgetItem("未检测到任何目标"))
        else:
            self.result_list.addItem(QListWidgetItem("未检测到任何目标"))

    @Slot(str)
    def _on_predict_failed(self, err: str) -> None:
        self.run_btn.setEnabled(True)
        first_line = err.split("\n", 1)[0]
        InfoBar.error(
            title="推理错误",
            content=first_line,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _on_save_result(self) -> None:
        if self._current_qimage is None:
            return
        default_name = str(self.project.root_dir / f"{self._current_image_path.stem}_pred.png")
        save_path_str, _ = QFileDialog.getSaveFileName(
            self, "保存预测图", default_name, "PNG (*.png);;JPG (*.jpg)"
        )
        if not save_path_str:
            return
        if self._current_qimage.save(save_path_str):
            InfoBar.success(
                title="保存成功",
                content=f"已保存预测结果图至 {save_path_str}",
                parent=self,
                position=InfoBarPosition.TOP,
            )
