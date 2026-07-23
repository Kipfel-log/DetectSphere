"""ImageGrid — 缩略图网格。

缩略图用 QThreadPool 异步加载 + 内存缓存。第一次显示时先放占位,加载完成后替换。

缓存键:文件 (size, mtime, exif_rotation) — 文件改了或 EXIF 变了才重新加载。

EXIF:用 Pillow 读取 + 转置,避免手机竖屏照片侧翻。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QStyle,
    QWidget,
)

from yolo_studio.core.image_utils import load_rotated


THUMB_SIZE = QSize(128, 128)

# 占位图必须在 QApplication 构造后才能创建(QPixmap 需要 QGuiApplication)。
# 这里只保存尺寸,首次访问时懒构造。
_PLACEHOLDER_PIXMAP: QPixmap | None = None


def _get_placeholder_pixmap() -> QPixmap:
    """懒加载占位 QPixmap(QApplication 必须在调用前构造)。"""
    global _PLACEHOLDER_PIXMAP
    if _PLACEHOLDER_PIXMAP is None or _PLACEHOLDER_PIXMAP.isNull():
        pix = QPixmap(THUMB_SIZE)
        pix.fill(QColor("#666"))
        _PLACEHOLDER_PIXMAP = pix
    return _PLACEHOLDER_PIXMAP


def _placeholder_icon() -> QIcon:
    return QIcon(_get_placeholder_pixmap())


class _ThumbSignals(QObject):
    """每个缩略图任务的信号载体。"""

    ready = Signal(str, QIcon)  # path, icon


class _ThumbLoader(QRunnable):
    """后台线程:加载图像 + 创建缩略图。"""

    def __init__(self, path: Path, signals: _ThumbSignals) -> None:
        super().__init__()
        self._path = path
        self._signals = signals

    def run(self) -> None:  # QRunnable entry
        try:
            # 用 Pillow 读图 + 自动 EXIF 旋转(避免手机竖屏照片侧翻)
            qimg, _exif_rot = load_rotated(self._path)
            pix = QPixmap(qimg)
            if pix.isNull():
                icon = _placeholder_icon()
            else:
                scaled = pix.scaled(
                    THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon = QIcon(scaled)
            self._signals.ready.emit(str(self._path), icon)
        except Exception:
            self._signals.ready.emit(str(self._path), _placeholder_icon())


class ImageGrid(QListWidget):
    """缩略图网格(异步加载)。"""

    imageActivated = Signal(str)
    imageSelectionChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(THUMB_SIZE)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(8)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.itemActivated.connect(self._on_activated)
        self.itemSelectionChanged.connect(self._on_selection_changed)

        # 后台线程池
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(max(2, min(8, os.cpu_count() or 4)))

        # 缩略图缓存:path → (mtime, exif_rotation, icon)
        self._cache: dict[str, tuple[int, int, QIcon]] = {}
        # 任务去重:path → True(已在飞)
        self._in_flight: set[str] = set()

        # ready 信号:用一个统一的 receiver
        self._signals = _ThumbSignals()
        self._signals.ready.connect(self._on_thumb_ready)

    def set_images(
        self,
        images: list[tuple[Path, bool]],
        name_for_image=lambda p: p.name,
    ) -> None:
        """设置网格内容。

        images: list of (image_path, has_boxes)

        已经存在的项不重建(只更新 has_boxes 标签)。
        """
        existing = {self.item(i).data(Qt.ItemDataRole.UserRole): self.item(i) for i in range(self.count())}
        seen: set[str] = set()

        for path, has_boxes in images:
            key = str(path)
            seen.add(key)
            label = name_for_image(path)
            label = ("● " if has_boxes else "○ ") + label

            if key in existing:
                # 仅更新 label(has_boxes 可能变了)
                existing[key].setText(label)
                continue

            # 新增项:占位图标 + 调度后台加载
            item = QListWidgetItem(_placeholder_icon(), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(key)
            self.addItem(item)
            self._schedule_load(path)

        # 移除已不存在的项
        for key in list(existing.keys()):
            if key not in seen:
                row = self.row(existing[key])
                self.takeItem(row)

    def current_image_path(self) -> Optional[str]:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def select_path(self, path: str) -> None:
        for i in range(self.count()):
            if self.item(i).data(Qt.ItemDataRole.UserRole) == path:
                self.setCurrentRow(i)
                return

    # ---- 缩略图加载 ----
    def _schedule_load(self, path: Path) -> None:
        """从缓存取缩略图,否则丢到线程池。"""
        key = str(path)
        try:
            st = path.stat()
            mtime = int(st.st_mtime * 1000)
        except OSError:
            return

        # 缓存命中(mtime + exif_rotation)
        # 预加载时还不知道 exif_rotation — 缓存项会先以 (mtime, 0) 占位,
        # _on_thumb_ready 会用真实 exif 替换。
        if key in self._cache:
            cached_mtime, _cached_exif, cached_icon = self._cache[key]
            if cached_mtime == mtime:
                item = self._find_item(key)
                if item is not None:
                    item.setIcon(cached_icon)
                return

        # 已在飞(避免重复)
        if key in self._in_flight:
            return
        self._in_flight.add(key)

        loader = _ThumbLoader(path, self._signals)
        self._pool.start(loader)

    @Slot(str, QIcon)
    def _on_thumb_ready(self, path: str, icon: QIcon) -> None:
        """后台加载完成 — 更新图标 + 缓存(包含真实 exif_rotation)。"""
        from yolo_studio.core.image_utils import load_rotated

        self._in_flight.discard(path)
        try:
            qimg, exif_rot = load_rotated(Path(path))
            mtime = int(Path(path).stat().st_mtime * 1000)
        except Exception:
            mtime, exif_rot = 0, 0
        self._cache[path] = (mtime, exif_rot, icon)
        item = self._find_item(path)
        if item is not None:
            item.setIcon(icon)

    def _find_item(self, path: str) -> Optional[QListWidgetItem]:
        for i in range(self.count()):
            if self.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return self.item(i)
        return None

    # ---- 选择事件 ----
    def _on_activated(self, item: QListWidgetItem) -> None:
        self.imageActivated.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_selection_changed(self) -> None:
        path = self.current_image_path()
        if path:
            self.imageSelectionChanged.emit(path)
