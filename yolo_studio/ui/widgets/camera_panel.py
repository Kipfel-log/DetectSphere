"""CameraPanel — 摄像头实时检测面板。

布局:
  ┌─────────────────────────┬─────────────┐
  │                         │ 控制区       │
  │   视频画面 (QLabel)       │ 摄像头选择   │
  │                         │ 模型选择     │
  │                         │ conf 滑块    │
  │                         │ iou 滑块     │
  │                         │ [启动][停止] │
  │                         │ FPS         │
  │                         │ 检测统计     │
  │                         │ [截图]      │
  └─────────────────────────┴─────────────┘

后台用 CameraFrameWorker(QThread) 拉帧 + 推理,主线程只负责显示。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
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

from yolo_studio.core.model_registry import (
    get_active_entry,
    load_registry,
    scan_models,
)
from yolo_studio.core.project import Project
from yolo_studio.workers.predict_worker import CameraFrameWorker


class CameraPanel(QWidget):
    """摄像头实时检测面板。"""

    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self._worker: Optional[CameraFrameWorker] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)

        # ---- 左:视频画面 ----
        left = QVBoxLayout()
        self.video_label = QLabel()
        self.video_label.setMinimumSize(640, 480)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #2b2b2b; color: #888; font-size: 14px;"
            "border: 1px solid #555;"
        )
        self.video_label.setText("摄像头未启动\n点击右侧「启动」按钮")
        self.video_label.setScaledContents(True)
        left.addWidget(self.video_label, 1)

        outer.addLayout(left, 1)

        # ---- 右:控制 ----
        right = QVBoxLayout()
        right.setSpacing(8)

        right.addWidget(StrongBodyLabel("控制"))

        form = QFormLayout()

        # 摄像头选择
        self.camera_combo = ComboBox()
        for i in range(4):  # 0-3 cameras
            self.camera_combo.addItem(f"摄像头 {i}", userData=i)
        form.addRow("摄像头:", self.camera_combo)

        # 模型选择
        self.model_combo = ComboBox()
        self._refresh_models()
        form.addRow("模型:", self.model_combo)

        # conf
        right.addLayout(form)

        # conf slider
        conf_box = QVBoxLayout()
        conf_box.setSpacing(2)
        conf_box.addWidget(CaptionLabel("置信度阈值(conf)"))
        conf_row = QHBoxLayout()
        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(5, 95)
        self.conf_slider.setValue(25)
        conf_row.addWidget(self.conf_slider, 1)
        self.conf_label = BodyLabel("0.25")
        self.conf_label.setMinimumWidth(40)
        conf_row.addWidget(self.conf_label)
        conf_box.addLayout(conf_row)
        self.conf_slider.valueChanged.connect(self._on_conf_changed)
        right.addLayout(conf_box)

        # iou slider
        iou_box = QVBoxLayout()
        iou_box.setSpacing(2)
        iou_box.addWidget(CaptionLabel("IoU 阈值(iou)"))
        iou_row = QHBoxLayout()
        self.iou_slider = QSlider(Qt.Orientation.Horizontal)
        self.iou_slider.setRange(10, 95)
        self.iou_slider.setValue(70)
        iou_row.addWidget(self.iou_slider, 1)
        self.iou_label = BodyLabel("0.70")
        self.iou_label.setMinimumWidth(40)
        iou_row.addWidget(self.iou_label)
        iou_box.addLayout(iou_row)
        self.iou_slider.valueChanged.connect(self._on_iou_changed)
        right.addLayout(iou_box)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.PLAY, "启动")
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = PushButton(FIF.CLOSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)
        right.addLayout(btn_row)

        # 截图按钮
        snap_row = QHBoxLayout()
        self.snapshot_btn = PushButton(FIF.SAVE, "保存截图")
        self.snapshot_btn.setEnabled(False)
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        snap_row.addWidget(self.snapshot_btn)

        self.snapshot_ds_btn = PushButton(FIF.ADD, "保存并入库")
        self.snapshot_ds_btn.setEnabled(False)
        self.snapshot_ds_btn.setToolTip("将截图直接存入未划分数据集(unassigned)")
        self.snapshot_ds_btn.clicked.connect(self._on_snapshot_to_dataset)
        snap_row.addWidget(self.snapshot_ds_btn)

        right.addLayout(snap_row)

        # FPS + 检测统计
        right.addSpacing(8)
        right.addWidget(StrongBodyLabel("状态"))
        self.fps_label = BodyLabel("FPS: —")
        right.addWidget(self.fps_label)
        self.det_label = BodyLabel("检测: 0 个目标")
        right.addWidget(self.det_label)

        right.addSpacing(8)
        right.addWidget(CaptionLabel("最近检测:"))
        self.recent_list = QLabel("—")
        self.recent_list.setWordWrap(True)
        self.recent_list.setStyleSheet("color: #888;")
        right.addWidget(self.recent_list, 1)

        outer.addLayout(right, 0)

        self._current_qimage: Optional[QImage] = None

    # ---- model combo ----
    def refresh_models(self) -> None:
        """公开接口:外部调用刷新模型列表(ModelRegistry 变更时)。"""
        self._refresh_models()

    def _refresh_models(self) -> None:
        """填充项目内模型下拉。"""
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

    def selected_model_path(self) -> Optional[Path]:
        p = self.model_combo.currentData()
        return Path(p) if p else None

    # ---- sliders ----
    def _on_conf_changed(self, v: int) -> None:
        self.conf_label.setText(f"{v / 100:.2f}")
        if self._worker is not None:
            self._worker.set_conf(v / 100)

    def _on_iou_changed(self, v: int) -> None:
        self.iou_label.setText(f"{v / 100:.2f}")
        if self._worker is not None:
            self._worker.set_iou(v / 100)

    # ---- start / stop ----
    def _on_start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        model_path = self.selected_model_path()
        if model_path is None or not model_path.exists():
            InfoBar.error(
                title="无法启动",
                content="请先选择一个 .pt 模型",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        self._worker = CameraFrameWorker(
            model_path=model_path,
            camera_id=int(self.camera_combo.currentData() or 0),
            conf=self.conf_slider.value() / 100,
            iou=self.iou_slider.value() / 100,
        )
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.fps_updated.connect(self._on_fps)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._reset_buttons)
        self._worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.snapshot_btn.setEnabled(True)
        self.snapshot_ds_btn.setEnabled(True)
        self.camera_combo.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.video_label.setText("摄像头启动中…")

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(3000)
        self._reset_buttons()
        self.video_label.setText("摄像头已停止")
        self.fps_label.setText("FPS: —")
        self.det_label.setText("检测: 0 个目标")

    @Slot()
    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.snapshot_btn.setEnabled(False)
        self.snapshot_ds_btn.setEnabled(False)
        self.camera_combo.setEnabled(True)
        self.model_combo.setEnabled(True)


    # ---- 帧回调 ----
    @Slot(QImage, list)
    def _on_frame(self, qimg: QImage, results: list) -> None:
        self._current_qimage = qimg
        # 缩放到 label 大小
        scaled = qimg.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(QPixmap.fromImage(scaled))

        # 检测统计
        n = sum(len(r.boxes) if r.boxes is not None else 0 for r in results)
        self.det_label.setText(f"检测: {n} 个目标")

        # 最近检测列表(取前 5 个)
        if results and results[0].boxes is not None:
            names = results[0].names
            recent = []
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes[:5]:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    recent.append(f"{names.get(cls_id, cls_id)} {conf:.2f}")
                if len(recent) >= 5:
                    break
            self.recent_list.setText("\n".join(recent) if recent else "—")
        else:
            self.recent_list.setText("—")

    @Slot(float)
    def _on_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")

    @Slot(str)
    def _on_failed(self, err: str) -> None:
        self._reset_buttons()
        self.video_label.setText("摄像头启动失败")
        # 打印完整 err 到日志(开发可见),UI 上只显示第一行
        first_line = err.split("\n", 1)[0] if err else ""
        InfoBar.error(
            title="摄像头错误",
            content=first_line,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ---- 截图 ----
    def _on_snapshot(self) -> None:
        if self._current_qimage is None:
            return
        # 默认文件名
        ts = time.strftime("%Y%m%d_%H%M%S")
        default = str(self.project.snapshots_dir / f"snapshot_{ts}.png")
        self.project.snapshots_dir.mkdir(parents=True, exist_ok=True)
        f, _ = QFileDialog.getSaveFileName(
            self,
            "保存截图",
            default,
            "PNG (*.png)",
        )
        if not f:
            return
        if self._current_qimage.save(f, "PNG"):
            InfoBar.success(
                title="截图已保存",
                content=f,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )
        else:
            InfoBar.error(
                title="保存失败",
                content=f,
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _on_snapshot_to_dataset(self) -> None:
        if self._current_qimage is None:
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        unassigned_dir = self.project.dataset_dir / "unassigned" / "images"
        unassigned_dir.mkdir(parents=True, exist_ok=True)
        dest_path = unassigned_dir / f"cam_{ts}.png"
        if self._current_qimage.save(str(dest_path), "PNG"):
            InfoBar.success(
                title="入库成功",
                content=f"已保存并添加至未划分数据集: {dest_path.name}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
        else:
            InfoBar.error(
                title="入库失败",
                content="写入图片失败",
                parent=self,
                position=InfoBarPosition.TOP,
            )

    # ---- 关闭 ----
    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        super().closeEvent(event)