"""CapturePage — 电脑与手机多模式数据采集页。

功能架构:
- 顶端 SegmentedWidget 选项卡导航: [电脑摄像头采集] | [手机无线采集]
- QStackedWidget 控制模式面板切换:
  - PCWebcamWidget: 本地电脑摄像头高帧率采集 (支持 1080p/720p 比例自适应，倒计时 ProgressRing 连拍)
  - MobileCapturePanel: 局域网 H5 手机无线采集 (扫码/URL 访问，6 位验证码，5 分钟倒计时重置，无感后台上传)
- 右侧共享暂存照片列表 ("待确认照片"):
  - 无论从电脑摄像头拍照还是从手机端无线上传的照片，统一自动流入右侧暂存区
  - 提供缩略图、尺寸/拍摄时间展示与单张快速删除按钮
- 强制导入未划分数据集:
  - 一键“确认导入未划分数据集”，将暂存库所有有效照片同步存入 `data/images` 并更新 DB 与主界面广播
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QStackedWidget,
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
    ProgressRing,
    PushButton,
    SegmentedWidget,
    StrongBodyLabel,
    TitleLabel,
    TransparentToolButton,
)

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.project import Project
from yolo_studio.workers.camera_stream_worker import CameraStreamWorker
from yolo_studio.ui.widgets.aspect_video_widget import AspectVideoWidget
from yolo_studio.ui.widgets.mobile_capture_panel import MobileCapturePanel


class StagedItemWidget(QWidget):
    """暂存照片列表中单项的 Widget (透明背景融入主题，含缩略图、描述、快速删除按钮)。"""

    def __init__(
        self,
        qimg: QImage,
        index_num: int,
        timestamp_str: str,
        on_delete_cb,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.qimg = qimg
        self.timestamp_str = timestamp_str
        self.on_delete_cb = on_delete_cb

        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        # 缩略图 (100x75)
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(100, 75)
        self.thumb_label.setStyleSheet("background-color: rgba(0, 0, 0, 0.2); border-radius: 4px;")
        scaled = qimg.scaled(
            100, 75,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumb_label.setPixmap(QPixmap.fromImage(scaled))
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumb_label)

        # 文字描述
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title = StrongBodyLabel(f"照片 #{index_num}")
        info_layout.addWidget(title)

        time_lbl = CaptionLabel(f"时间: {timestamp_str}")
        time_lbl.setStyleSheet("color: #888888;")
        info_layout.addWidget(time_lbl)

        res_lbl = CaptionLabel(f"分辨率: {qimg.width()} × {qimg.height()}")
        res_lbl.setStyleSheet("color: #888888;")
        info_layout.addWidget(res_lbl)

        layout.addLayout(info_layout, 1)

        # 删除按钮
        self.del_btn = TransparentToolButton(FIF.DELETE, self)
        self.del_btn.setToolTip("删除此照片")
        self.del_btn.clicked.connect(lambda: self.on_delete_cb(self))
        layout.addWidget(self.del_btn)


class PCWebcamWidget(QWidget):
    """电脑摄像头采集控制面板。"""

    photoCaptured = Signal(QImage, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: Optional[CameraStreamWorker] = None
        self._current_qimage: Optional[QImage] = None

        # 倒计时连拍 Timer 控速
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(50)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._total_interval_ms = 0
        self._elapsed_ms = 0

        left_layout = QVBoxLayout(self)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        # 头部控制栏
        left_header = QHBoxLayout()
        left_header.setSpacing(10)
        left_header.addWidget(StrongBodyLabel("电脑摄像头画帧采集"))

        self.cam_combo = ComboBox()
        for i in range(4):
            self.cam_combo.addItem(f"摄像头 {i}", userData=i)
        left_header.addWidget(self.cam_combo)

        # 分辨率选择
        self.res_combo = ComboBox()
        self.res_combo.addItem("自动 (相机原生默认)", userData=(0, 0))
        self.res_combo.addItem("16:9 比例 (1280×720 HD)", userData=(1280, 720))
        self.res_combo.addItem("16:9 比例 (1920×1080 FHD)", userData=(1920, 1080))
        self.res_combo.addItem("4:3 比例 (640×480 VGA)", userData=(640, 480))
        self.res_combo.addItem("4:3 比例 (1024×768 XGA)", userData=(1024, 768))
        left_header.addWidget(self.res_combo)

        self.start_btn = PrimaryPushButton(FIF.PLAY, "启动摄像头")
        self.start_btn.clicked.connect(self._on_start)
        left_header.addWidget(self.start_btn)

        self.stop_btn = PushButton(FIF.CLOSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        left_header.addWidget(self.stop_btn)

        left_header.addStretch(1)
        self.fps_label = CaptionLabel("FPS: —")
        self.fps_label.setStyleSheet("color: #00A676; font-weight: bold;")
        left_header.addWidget(self.fps_label)

        left_layout.addLayout(left_header)

        # 视频画面展示框
        self.video_widget = AspectVideoWidget(self)
        left_layout.addWidget(self.video_widget, 1)

        # 底部拍照控制区
        left_bottom = QHBoxLayout()
        left_bottom.setSpacing(12)

        self.snap_btn = PrimaryPushButton(FIF.CAMERA, "拍照 (Space)")
        self.snap_btn.setEnabled(False)
        self.snap_btn.clicked.connect(self._on_snapshot)
        left_bottom.addWidget(self.snap_btn)

        left_bottom.addWidget(CaptionLabel("倒计时连拍:"))
        self.auto_combo = ComboBox()
        self.auto_combo.addItem("手动拍照", userData=0)
        self.auto_combo.addItem("每 1 秒", userData=1000)
        self.auto_combo.addItem("每 2 秒", userData=2000)
        self.auto_combo.addItem("每 3 秒", userData=3000)
        self.auto_combo.addItem("每 5 秒", userData=5000)
        self.auto_combo.currentIndexChanged.connect(self._on_auto_combo_changed)
        left_bottom.addWidget(self.auto_combo)

        # 倒计时 Fluent ProgressRing
        self.progress_ring = ProgressRing(self)
        self.progress_ring.setFixedSize(34, 34)
        self.progress_ring.setRange(0, 100)
        self.progress_ring.setValue(0)
        self.progress_ring.setTextVisible(True)
        self.progress_ring.setStrokeWidth(3)
        self.progress_ring.setVisible(False)
        left_bottom.addWidget(self.progress_ring)

        left_bottom.addStretch(1)
        left_layout.addLayout(left_bottom)

    def _on_start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        cam_id = int(self.cam_combo.currentData() or 0)
        res = self.res_combo.currentData() or (0, 0)
        w, h = int(res[0]), int(res[1])

        self._worker = CameraStreamWorker(camera_id=cam_id, width=w, height=h, parent=self)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.fps_updated.connect(self._on_fps)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._reset_buttons)
        self._worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.snap_btn.setEnabled(True)
        self.cam_combo.setEnabled(False)
        self.res_combo.setEnabled(False)
        self.video_widget.setText("正在启动摄像头...")

    def _on_stop(self) -> None:
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
            self.auto_combo.setCurrentIndex(0)
            self.progress_ring.setVisible(False)

        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(3000)
        self._reset_buttons()
        self.video_widget.setText("摄像头已停止")
        self.fps_label.setText("FPS: —")

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.snap_btn.setEnabled(False)
        self.cam_combo.setEnabled(True)
        self.res_combo.setEnabled(True)

    @Slot(QImage)
    def _on_frame(self, qimg: QImage) -> None:
        self._current_qimage = qimg
        self.video_widget.setPixmap(QPixmap.fromImage(qimg))

    @Slot(float)
    def _on_fps(self, fps: float) -> None:
        self.fps_label.setText(f"FPS: {fps:.1f}")

    @Slot(str)
    def _on_failed(self, err: str) -> None:
        self._reset_buttons()
        self.video_widget.setText("摄像头启动失败")
        first_line = err.split("\n", 1)[0] if err else ""
        InfoBar.error(
            title="摄像头启动失败",
            content=first_line,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _on_auto_combo_changed(self, idx: int) -> None:
        ms = int(self.auto_combo.currentData() or 0)
        if ms > 0:
            if self._worker is not None and self._worker.isRunning():
                self._total_interval_ms = ms
                self._elapsed_ms = 0
                self.progress_ring.setValue(0)
                self.progress_ring.setVisible(True)
                self._countdown_timer.start()
            else:
                InfoBar.warning(
                    title="无法启动连拍",
                    content="请先启动摄像头后再开启倒计时连拍",
                    parent=self,
                    position=InfoBarPosition.TOP,
                )
                self.auto_combo.setCurrentIndex(0)
        else:
            self._countdown_timer.stop()
            self.progress_ring.setVisible(False)

    def _on_countdown_tick(self) -> None:
        if self._total_interval_ms <= 0:
            return
        self._elapsed_ms += 50
        pct = int((self._elapsed_ms / self._total_interval_ms) * 100)
        remaining_sec = max(0, int((self._total_interval_ms - self._elapsed_ms + 999) / 1000))

        self.progress_ring.setValue(pct)
        self.progress_ring.setFormat(f"{remaining_sec}s")

        if self._elapsed_ms >= self._total_interval_ms:
            self._on_snapshot()
            self._elapsed_ms = 0

    def _on_snapshot(self) -> None:
        if self._current_qimage is None:
            return
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        self.photoCaptured.emit(self._current_qimage.copy(), ts_str)

    def closeEvent(self, event) -> None:
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)


class CapturePage(QWidget):
    """数据采集页 (包含电脑摄像头与手机无线双模式选项卡)。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        self._staged_items: list[tuple[QImage, str]] = []  # [(qimg, timestamp_str), ...]
        self._capture_count = 0

        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(16, 16, 16, 16)
        main_vbox.setSpacing(12)

        # 顶端模式切换 SegmentedWidget 选项卡
        main_vbox.addWidget(TitleLabel("多端数据素材采集"))

        self.pivot = SegmentedWidget(self)
        main_vbox.addWidget(self.pivot)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        main_vbox.addWidget(self.splitter, 1)

        # ── 左侧 StackedWidget (电脑面板 vs 手机面板) ──
        self.stacked_widget = QStackedWidget(self)

        self.pc_panel = PCWebcamWidget(self)
        self.pc_panel.photoCaptured.connect(self._add_staged_photo)

        self.mobile_panel = MobileCapturePanel(self)
        self.mobile_panel.photoReceived.connect(self._add_staged_photo)
        self.mobile_panel.stateChanged.connect(self._on_mobile_state_changed)

        self.stacked_widget.addWidget(self.pc_panel)
        self.stacked_widget.addWidget(self.mobile_panel)

        # 绑定 SegmentedWidget 选项卡
        self.pivot.addItem(
            routeKey="pc",
            text="电脑摄像头采集",
            onClick=lambda: self._switch_tab(0),
        )
        self.pivot.addItem(
            routeKey="mobile",
            text="手机无线采集",
            onClick=lambda: self._switch_tab(1),
        )

        self.pivot.setCurrentItem("pc")
        self.stacked_widget.setCurrentWidget(self.pc_panel)

        self.splitter.addWidget(self.stacked_widget)

        # ── 右侧: 共享待确认照片暂存列表与导入 ──
        self.right_widget = QWidget()
        self.right_widget.setMinimumWidth(340)
        right_layout = QVBoxLayout(self.right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        # 标题栏
        right_header = QHBoxLayout()
        self.staged_title = StrongBodyLabel("待确认导入照片 (0 张)")
        right_header.addWidget(self.staged_title)
        right_header.addStretch(1)

        self.clear_btn = PushButton(FIF.DELETE, "清空全部")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._on_clear_all)
        right_header.addWidget(self.clear_btn)

        right_layout.addLayout(right_header)

        # 暂存列表框 (QListWidget — 透明暗色融合主题)
        self.staged_list = QListWidget()
        self.staged_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.staged_list.setStyleSheet(
            "QListWidget { background-color: transparent; border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 8px; outline: none; }"
            "QListWidget::item { background: transparent; border-bottom: 1px solid rgba(128, 128, 128, 0.1); border-radius: 6px; margin: 2px 4px; }"
            "QListWidget::item:hover { background-color: rgba(128, 128, 128, 0.08); }"
            "QListWidget::item:selected { background-color: rgba(128, 128, 128, 0.15); }"
        )
        right_layout.addWidget(self.staged_list, 1)

        # 计数指示
        self.count_label = CaptionLabel("已抓取: 0 张")
        right_layout.addWidget(self.count_label)

        # 确认导入未划分数据集大按钮
        self.import_btn = PrimaryPushButton(FIF.DOWNLOAD, "确认导入未划分数据集")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_confirm_import)
        right_layout.addWidget(self.import_btn)

        self.splitter.addWidget(self.right_widget)

        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([700, 350])

        # 绑定键盘 Space 快捷键 (在电脑模式生效)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._on_space_pressed)

    def _switch_tab(self, index: int) -> None:
        self.stacked_widget.setCurrentIndex(index)
        if index == 1:  # 手机无线采集模式
            self.mobile_panel.activate()
            has_devices = len(self.mobile_panel.server_mgr.active_devices) > 0 or getattr(self.mobile_panel, "has_staged_photos", False)
            self._set_right_widget_visible(has_devices)
        else:
            self.mobile_panel.deactivate()
            self._set_right_widget_visible(True)

    @Slot(bool)
    def _on_mobile_state_changed(self, has_devices: bool) -> None:
        if self.stacked_widget.currentIndex() == 1:
            self._set_right_widget_visible(has_devices)

    def _set_right_widget_visible(self, visible: bool) -> None:
        if self.right_widget.isVisible() == visible:
            return
        self.right_widget.setVisible(visible)
        if visible:
            self.splitter.setSizes([700, 350])

    # ---- 照片装载入库 ----
    @Slot(QImage, str)
    def _add_staged_photo(self, qimg: QImage, ts_str: str) -> None:
        self._capture_count += 1
        self._staged_items.append((qimg, ts_str))

        item_widget = StagedItemWidget(
            qimg=qimg,
            index_num=self._capture_count,
            timestamp_str=ts_str,
            on_delete_cb=self._on_delete_item_widget,
            parent=self.staged_list,
        )

        item = QListWidgetItem(self.staged_list)
        item.setSizeHint(QSize(300, 88))
        item.setData(Qt.ItemDataRole.UserRole, len(self._staged_items) - 1)
        self.staged_list.addItem(item)
        self.staged_list.setItemWidget(item, item_widget)
        self.staged_list.scrollToItem(item)

        self._update_staged_ui()

    def _on_space_pressed(self) -> None:
        if self.stacked_widget.currentIndex() == 0 and self.pc_panel.snap_btn.isEnabled():
            self.pc_panel._on_snapshot()

    def _on_delete_item_widget(self, widget: StagedItemWidget) -> None:
        for row in range(self.staged_list.count()):
            item = self.staged_list.item(row)
            w = self.staged_list.itemWidget(item)
            if w == widget:
                idx = item.data(Qt.ItemDataRole.UserRole)
                if 0 <= idx < len(self._staged_items):
                    self._staged_items[idx] = (QImage(), "")
                self.staged_list.takeItem(row)
                break
        self._update_staged_ui()

    def _on_clear_all(self) -> None:
        self._staged_items.clear()
        self.staged_list.clear()
        self._update_staged_ui()

    def _update_staged_ui(self) -> None:
        active_count = sum(1 for q, _ in self._staged_items if not q.isNull())
        self.staged_title.setText(f"待确认导入照片 ({active_count} 张)")
        self.count_label.setText(f"已抓取: {self._capture_count} 张")
        has_items = active_count > 0
        self.clear_btn.setEnabled(has_items)
        self.import_btn.setEnabled(has_items)
        
        self.mobile_panel.has_staged_photos = has_items
        self.mobile_panel._update_layout_state()

    def _on_confirm_import(self) -> None:
        valid_photos = [qimg for qimg, _ in self._staged_items if not qimg.isNull()]
        if not valid_photos:
            return

        dst_dir = self.project.images_dir
        dst_dir.mkdir(parents=True, exist_ok=True)

        imported_count = 0
        timestamp_prefix = time.strftime("%Y%m%d_%H%M%S")

        for i, qimg in enumerate(valid_photos):
            filename = f"capture_{timestamp_prefix}_{i+1:03d}.png"
            file_path = dst_dir / filename

            if qimg.save(str(file_path), "PNG"):
                try:
                    img_id = self.db.upsert_image(str(file_path.resolve()))
                except Exception:
                    pass
                imported_count += 1

        InfoBar.success(
            title="导入数据集成功",
            content=f"已成功将 {imported_count} 张抓取照片导入至 [未划分] 数据集",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3500,
        )

        self._on_clear_all()

        parent_main = self.window()
        if hasattr(parent_main, "annotate_page"):
            parent_main.annotate_page.refresh()
        if hasattr(parent_main, "dataset_page"):
            parent_main.dataset_page.refresh()

    def closeEvent(self, event) -> None:
        self.pc_panel.closeEvent(event)
        self.mobile_panel.deactivate()
        self.mobile_panel.server_mgr.stop_server()
        super().closeEvent(event)
