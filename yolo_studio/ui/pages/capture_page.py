"""CapturePage — 电脑摄像头数据采集页。

功能:
- 实时调取电脑摄像头画面 (使用 AspectVideoWidget 自适应居中渲染，绝对不拉伸变形)
- 支持分辨率选项 (自动相机默认 / 16:9 HD / 16:9 FHD / 4:3 VGA / 4:3 XGA)
- 单张拍照 (支持鼠标按钮与键盘 Space 快捷键)
- 倒计时自动连拍 (配合 Fluent ProgressRing 环形进度条与秒数倒数显示)
- 拍照后在右侧“待确认”暂存列表中呈现照片缩略图与尺寸信息
- 暂存列表背景采用透明材质，完美融入主界面 Fluent 主题风格
- 导入前可随时删除特定不满意照片或一键清空
- 强制导入至“未划分 (unassigned)”数据集
- 一键“确认导入未划分数据集”，自动复制图片、更新数据库索引，并通知标注页与数据集页刷新
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Slot, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
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
    StrongBodyLabel,
    TransparentToolButton,
)

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.project import Project
from yolo_studio.workers.camera_stream_worker import CameraStreamWorker


class AspectVideoWidget(QWidget):
    """自适应等比例居中渲染视频画面，绝对不拉伸变形。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._text = "摄像头未启动\n点击上方「启动摄像头」按钮进行采集"
        self.setMinimumSize(640, 480)
        self.setStyleSheet("background-color: #1a1a1e; border-radius: 8px; border: 1px solid #333;")

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._text = ""
        self.update()

    def setText(self, text: str) -> None:
        self._pixmap = QPixmap()
        self._text = text
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 绘制黑灰色底框
        painter.fillRect(self.rect(), QColor("#1a1a1e"))

        if not self._pixmap.isNull():
            # 计算 KeepAspectRatio 居中绘图 (KeepAspectRatio 完整展示不变形)
            target_rect = QRectF(self.rect())
            pw, ph = self._pixmap.width(), self._pixmap.height()
            tw, th = target_rect.width(), target_rect.height()

            scale = min(tw / pw, th / ph)
            dw = pw * scale
            dh = ph * scale
            dx = (tw - dw) / 2
            dy = (th - dh) / 2

            dest = QRectF(dx, dy, dw, dh)
            painter.drawPixmap(dest, self._pixmap, QRectF(self._pixmap.rect()))
        elif self._text:
            painter.setPen(QColor("#888888"))
            font = QFont("Segoe UI", 11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


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


class CapturePage(QWidget):
    """数据采集页。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        self._worker: Optional[CameraStreamWorker] = None
        self._current_qimage: Optional[QImage] = None
        self._staged_items: list[tuple[QImage, str]] = []  # [(qimg, timestamp_str), ...]
        self._capture_count = 0

        # 倒计时连拍 Timer 控速
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(50)  # 50ms 刷新一次 ProgressRing
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._total_interval_ms = 0
        self._elapsed_ms = 0

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # ── 左侧: 摄像头视频画面与控制 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        # 头部控制栏
        left_header = QHBoxLayout()
        left_header.setSpacing(10)
        left_header.addWidget(StrongBodyLabel("摄像头画帧采集"))

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

        # 视频画面展示框 (使用自适应 AspectVideoWidget 绝对不变形)
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
        self.count_label = StrongBodyLabel("已抓取: 0 张")
        left_bottom.addWidget(self.count_label)

        left_layout.addLayout(left_bottom)
        splitter.addWidget(left_widget)

        # ── 右侧: 待确认照片暂存列表与导入 ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
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

        # 确认导入未划分数据集大按钮
        self.import_btn = PrimaryPushButton(FIF.DOWNLOAD, "确认导入未划分数据集")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_confirm_import)
        right_layout.addWidget(self.import_btn)

        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # 绑定键盘 Space 快捷键
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._on_space_pressed)

    # ---- 摄像头控制 ----
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

    # ---- 倒计时连拍逻辑 ----
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

    # ---- 拍照逻辑 ----
    def _on_space_pressed(self) -> None:
        if self.snap_btn.isEnabled():
            self._on_snapshot()

    def _on_snapshot(self) -> None:
        if self._current_qimage is None:
            return

        self._capture_count += 1
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")

        qimg_copy = self._current_qimage.copy()
        self._staged_items.append((qimg_copy, ts_str))

        # 添加到右侧暂存 ListWidget
        item_widget = StagedItemWidget(
            qimg=qimg_copy,
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

    # ---- 删除 & 清空 ----
    def _on_delete_item_widget(self, widget: StagedItemWidget) -> None:
        """从 ListWidget 中找到对应的 row 并删除。"""
        for row in range(self.staged_list.count()):
            item = self.staged_list.item(row)
            w = self.staged_list.itemWidget(item)
            if w == widget:
                # 从内存列表移除
                idx = item.data(Qt.ItemDataRole.UserRole)
                if 0 <= idx < len(self._staged_items):
                    self._staged_items[idx] = (QImage(), "")  # 标记空
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

    # ---- 确认导入 (强制未划分) ----
    def _on_confirm_import(self) -> None:
        valid_photos = [qimg for qimg, _ in self._staged_items if not qimg.isNull()]
        if not valid_photos:
            return

        # 强制导入至未划分目录
        dst_dir = self.project.images_dir
        dst_dir.mkdir(parents=True, exist_ok=True)

        imported_count = 0
        timestamp_prefix = time.strftime("%Y%m%d_%H%M%S")

        for i, qimg in enumerate(valid_photos):
            filename = f"capture_{timestamp_prefix}_{i+1:03d}.png"
            file_path = dst_dir / filename

            if qimg.save(str(file_path), "PNG"):
                # 写入数据库 (存入 unassigned)
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

        # 清空暂存区
        self._on_clear_all()

        # 触发广播更新其他页面
        parent_main = self.window()
        if hasattr(parent_main, "annotate_page"):
            parent_main.annotate_page.refresh()
        if hasattr(parent_main, "dataset_page"):
            parent_main.dataset_page.refresh()

    # ---- 关闭清理 ----
    def closeEvent(self, event) -> None:
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        super().closeEvent(event)
