"""FolderTestPanel — 批量文件夹测试面板。

功能:
- 选择包含图片的文件夹进行批量推理
- 后台 QThread 异步推导，防止界面假死
- 实时展示处理进度条、已完成文件列表与检出目标数量
- 点击文件列表中的项在大图区域预览带框预测结果
- 支持一键将全套预测带框图导出至指定输出文件夹
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
    ProgressBar,
    PushButton,
    StrongBodyLabel,
)

from yolo_studio.core.model_registry import load_registry, scan_models
from yolo_studio.core.project import Project
from yolo_studio.workers.predict_worker import OneShotPredictWorker


class FolderTestPanel(QWidget):
    """批量文件夹测试面板。"""

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self._folder_path: Optional[Path] = None
        self._batch_results: list[tuple[Path, list]] = []
        self._worker: Optional[OneShotPredictWorker] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        # ---- 左: 大图预览 ----
        left = QVBoxLayout()
        self.image_label = QLabel()
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
            "border: 1px solid #555;"
        )
        self.image_label.setText("请选择图片文件夹开始批量推理\n完成后点击右侧列表可预览详细结果")
        self.image_label.setScaledContents(True)
        left.addWidget(self.image_label, 1)

        outer.addLayout(left, 1)

        # ---- 右: 控制面板与结果列表 ----
        right = QVBoxLayout()
        right.setSpacing(8)

        right.addWidget(StrongBodyLabel("批量推理设置"))

        form = QFormLayout()

        # 文件夹选择
        folder_row = QHBoxLayout()
        self.select_dir_btn = PushButton(FIF.FOLDER, "选择文件夹")
        self.select_dir_btn.clicked.connect(self._on_select_folder)
        folder_row.addWidget(self.select_dir_btn)
        self.folder_path_label = CaptionLabel("未选择文件夹")
        self.folder_path_label.setWordWrap(True)
        folder_row.addWidget(self.folder_path_label, 1)
        form.addRow("输入目录:", folder_row)

        # 模型选择
        self.model_combo = ComboBox()
        self.refresh_models()
        form.addRow("测试模型:", self.model_combo)

        right.addLayout(form)

        # conf
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

        # iou
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

        # 按钮与进度
        btn_row = QHBoxLayout()
        self.run_btn = PrimaryPushButton(FIF.PLAY, "开始批量推理")
        self.run_btn.clicked.connect(self._on_run_batch)
        btn_row.addWidget(self.run_btn)

        self.export_btn = PushButton(FIF.DOWNLOAD, "批量导出带框图")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export_batch)
        btn_row.addWidget(self.export_btn)
        right.addLayout(btn_row)

        self.progress_bar = ProgressBar()
        self.progress_bar.setVal(0)
        self.progress_bar.setVisible(False)
        right.addWidget(self.progress_bar)

        right.addSpacing(8)
        right.addWidget(StrongBodyLabel("处理结果 (点击项预览)"))
        self.result_list = QListWidget()
        self.result_list.itemClicked.connect(self._on_item_clicked)
        right.addWidget(self.result_list, 1)

        outer.addLayout(right, 0)

    def refresh_models(self) -> None:
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

    def _on_select_folder(self) -> None:
        path_str = QFileDialog.getExistingDirectory(
            self, "选择图片文件夹", str(self.project.root_dir)
        )
        if not path_str:
            return
        self._folder_path = Path(path_str)
        self.folder_path_label.setText(self._folder_path.name)
        self.result_list.clear()
        self.export_btn.setEnabled(False)
        self._batch_results.clear()

    def _on_run_batch(self) -> None:
        if self._folder_path is None or not self._folder_path.exists():
            InfoBar.warning(
                title="未选择文件夹",
                content="请先指定包含测试图片的文件夹",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        model_path = self._selected_model_path()
        if model_path is None or not model_path.exists():
            InfoBar.error(
                title="未选择模型",
                content="请选择有效的 .pt 模型",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        valid_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        items = sorted(
            p for p in self._folder_path.iterdir()
            if p.is_file() and p.suffix.lower() in valid_exts
        )
        if not items:
            InfoBar.warning(
                title="文件夹无图片",
                content=f"在 {self._folder_path.name} 中没有找到常见的图像文件",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.progress_bar.setVal(0)
        self.progress_bar.setVisible(True)
        self.result_list.clear()

        conf = self.conf_slider.value() / 100
        iou = self.iou_slider.value() / 100

        self._worker = OneShotPredictWorker(
            model_path=model_path,
            items=items,
            conf=conf,
            iou=iou,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_batch.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        pct = int(done / total * 100)
        self.progress_bar.setVal(pct)

    @Slot(list)
    def _on_finished(self, out: list) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._batch_results = out
        self.export_btn.setEnabled(True if out else False)

        for path, results in out:
            count = sum(len(r.boxes) if r.boxes is not None else 0 for r in results)
            item = QListWidgetItem(f"{path.name}  →  {count} 个目标")
            item.setData(Qt.ItemDataRole.UserRole, (path, results))
            self.result_list.addItem(item)

        InfoBar.success(
            title="批量推理完成",
            content=f"成功推理 {len(out)} 张图片",
            parent=self,
            position=InfoBarPosition.TOP,
        )
        if out:
            self._preview_item(out[0][0], out[0][1])

    @Slot(str)
    def _on_failed(self, err: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        first_line = err.split("\n", 1)[0]
        InfoBar.error(
            title="批量推理失败",
            content=first_line,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            path, results = data
            self._preview_item(path, results)

    def _preview_item(self, path: Path, results: list) -> None:
        import cv2
        from yolo_studio.core.inference import Predictor

        frame = cv2.imread(str(path))
        if frame is None:
            return
        predictor_names = results[0].names if results else {}
        annotated = Predictor.draw_boxes(frame, results, predictor_names)
        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        scaled = qimg.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(QPixmap.fromImage(scaled))

    def _on_export_batch(self) -> None:
        if not self._batch_results:
            return
        export_dir_str = QFileDialog.getExistingDirectory(self, "选择保存预测带框图的文件夹")
        if not export_dir_str:
            return
        export_dir = Path(export_dir_str)
        export_dir.mkdir(parents=True, exist_ok=True)

        import cv2
        from yolo_studio.core.inference import Predictor

        count = 0
        for path, results in self._batch_results:
            frame = cv2.imread(str(path))
            if frame is None:
                continue
            predictor_names = results[0].names if results else {}
            annotated = Predictor.draw_boxes(frame, results, predictor_names)
            out_file = export_dir / f"{path.stem}_pred{path.suffix}"
            cv2.imwrite(str(out_file), annotated)
            count += 1

        InfoBar.success(
            title="导出完成",
            content=f"已将 {count} 张预测图导出至 {export_dir.name}",
            parent=self,
            position=InfoBarPosition.TOP,
        )
