"""ImageGrid — 卡片式缩略图网格。

使用自定义 ImageCard 卡片 + 自适应列数的 Flow 网格，彻底解决 QListWidget IconMode 
中文件名被图片覆盖的问题。

布局特性:
- 每张卡片宽度固定 (CARD_W)，图片区域高度固定 (IMAGE_H)，统一等比缩放。
- 文件名区域固定高度 (LABEL_H)，超长用省略号截断，hover 显示完整路径 Tooltip。
- 窗口缩放时自动重新计算列数，响应式自适应。
- 缩略图后台异步加载（QThreadPool），加载完成前显示灰色占位。
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
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yolo_studio.core.image_utils import load_rotated

# ──────────────────────────────────────────────
#  尺寸常量
# ──────────────────────────────────────────────
CARD_W = 160       # 卡片宽度 (px)
IMAGE_H = 120      # 图片区域高度 (px)
LABEL_H = 44       # 文件名标签高度 (px, 两行)
GAP = 10           # 卡片间距 (px)
CARD_H = IMAGE_H + LABEL_H + 8  # 总高度


# ──────────────────────────────────────────────
#  占位 Pixmap (懒加载)
# ──────────────────────────────────────────────
_PLACEHOLDER: QPixmap | None = None


def _placeholder() -> QPixmap:
    global _PLACEHOLDER
    if _PLACEHOLDER is None or _PLACEHOLDER.isNull():
        pix = QPixmap(CARD_W, IMAGE_H)
        pix.fill(QColor("#E8E8E8"))
        _PLACEHOLDER = pix
    return _PLACEHOLDER


# ──────────────────────────────────────────────
#  后台缩略图加载
# ──────────────────────────────────────────────
class _ThumbSignals(QObject):
    ready = Signal(str, QPixmap)  # path, pixmap


class _ThumbLoader(QRunnable):
    def __init__(self, path: Path, signals: _ThumbSignals) -> None:
        super().__init__()
        self._path = path
        self._signals = signals

    def run(self) -> None:
        try:
            qimg, _ = load_rotated(self._path)
            pix = QPixmap(qimg)
            if pix.isNull():
                pix = _placeholder()
            else:
                # 缩放到固定高度，宽度按比例，不超过 CARD_W
                pix = pix.scaled(
                    QSize(CARD_W, IMAGE_H),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        except Exception:
            pix = _placeholder()
        self._signals.ready.emit(str(self._path), pix)


# ──────────────────────────────────────────────
#  单张图片卡片
# ──────────────────────────────────────────────
class ImageCard(QFrame):
    """单张图片卡片：顶部固定高度图片区 + 底部固定高度文件名标签。"""

    clicked = Signal(str)         # path
    double_clicked = Signal(str)  # path

    def __init__(self, path: str, label_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._selected = False

        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(path)
        self._apply_style(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 图片区域：居中对齐，固定高度
        self._img_lbl = QLabel(self)
        self._img_lbl.setFixedSize(CARD_W - 8, IMAGE_H)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setPixmap(_placeholder())
        layout.addWidget(self._img_lbl)

        # 文件名标签
        self._name_lbl = QLabel(self)
        self._name_lbl.setFixedHeight(LABEL_H)
        self._name_lbl.setFixedWidth(CARD_W - 8)
        self._name_lbl.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self._name_lbl.setWordWrap(True)
        self._name_lbl.setText(label_text)
        self._name_lbl.setStyleSheet(
            "font-size: 11px; color: #333333; background: transparent;"
        )
        layout.addWidget(self._name_lbl)

    def set_pixmap(self, pix: QPixmap) -> None:
        """更新图片（缩略图加载完成后调用）。"""
        self._img_lbl.setPixmap(pix)

    def set_label(self, text: str) -> None:
        self._name_lbl.setText(text)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style(selected)

    def _apply_style(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "ImageCard { background: #E3F2FD; border: 2px solid #1976D2; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet(
                "ImageCard { background: #F5F5F5; border: 1px solid #E0E0E0; border-radius: 6px; }"
                "ImageCard:hover { background: #EEEEEE; border: 1px solid #BDBDBD; }"
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self.path)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.double_clicked.emit(self.path)
        super().mouseDoubleClickEvent(event)


# ──────────────────────────────────────────────
#  卡片网格（ScrollArea 包裹）
# ──────────────────────────────────────────────
class ImageGrid(QScrollArea):
    """自适应列数的卡片式缩略图网格。"""

    imageActivated = Signal(str)
    imageSelectionChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 明确设置白色背景，避免继承默认灰色系统主题色
        # 同时应用 Fluent 风格的细滚动条样式
        self.setStyleSheet("""
            QScrollArea {
                background: #FFFFFF;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: #FFFFFF;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 2px 2px 2px 0;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 0.2);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(0, 0, 0, 0.35);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        self._container = QWidget()
        self._container.setStyleSheet("background: #FFFFFF;")
        self._container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.setWidget(self._container)

        # 数据 & 卡片字典
        self._images: list[tuple[Path, bool]] = []
        self._cards: dict[str, ImageCard] = {}  # path → card
        self._selected_path: Optional[str] = None

        # 后台缩略图加载
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(max(2, min(8, os.cpu_count() or 4)))
        self._signals = _ThumbSignals()
        self._signals.ready.connect(self._on_thumb_ready)
        self._cache: dict[str, tuple[int, QPixmap]] = {}  # path → (mtime, pix)
        self._in_flight: set[str] = set()

    # ── 公开 API ──────────────────────────────

    def set_images(
        self,
        images: list[tuple[Path, bool]],
        name_for_image=lambda p: p.name,
    ) -> None:
        """设置网格内容，增量更新（只增/删，不重建相同 key 的卡片）。"""
        self._images = images
        new_keys = {str(p) for p, _ in images}

        # 删除已不在列表的卡片
        for key in list(self._cards.keys()):
            if key not in new_keys:
                self._cards[key].deleteLater()
                del self._cards[key]

        # 更新或新建卡片
        for path, has_boxes in images:
            key = str(path)
            label = ("● " if has_boxes else "○ ") + name_for_image(path)
            if key in self._cards:
                self._cards[key].set_label(label)
            else:
                card = ImageCard(key, label, self._container)
                card.clicked.connect(self._on_card_clicked)
                card.double_clicked.connect(self._on_card_double_clicked)
                self._cards[key] = card
                self._schedule_load(path)

        self._relayout()

    def current_image_path(self) -> Optional[str]:
        return self._selected_path

    def select_path(self, path: str) -> None:
        """以编程方式选中某张图片。"""
        if self._selected_path and self._selected_path in self._cards:
            self._cards[self._selected_path].set_selected(False)
        self._selected_path = path
        if path in self._cards:
            self._cards[path].set_selected(True)
            # 滚动到可见
            self.ensureWidgetVisible(self._cards[path])

    # ── 布局 ─────────────────────────────────

    def _relayout(self) -> None:
        """重新计算列数并摆放所有卡片。"""
        container_w = self.viewport().width()
        if container_w < CARD_W:
            container_w = CARD_W
        cols = max(1, (container_w + GAP) // (CARD_W + GAP))

        for i, key in enumerate(str(p) for p, _ in self._images):
            card = self._cards.get(key)
            if card is None:
                continue
            col = i % cols
            row = i // cols
            x = GAP + col * (CARD_W + GAP)
            y = GAP + row * (CARD_H + GAP)
            card.move(x, y)
            card.show()

        rows = (len(self._images) + cols - 1) // cols
        total_h = GAP + rows * (CARD_H + GAP)
        self._container.setMinimumHeight(total_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    # ── 缩略图后台加载 ────────────────────────

    def _schedule_load(self, path: Path) -> None:
        key = str(path)
        try:
            mtime = int(path.stat().st_mtime * 1000)
        except OSError:
            return
        if key in self._cache:
            cached_mtime, cached_pix = self._cache[key]
            if cached_mtime == mtime:
                card = self._cards.get(key)
                if card:
                    card.set_pixmap(cached_pix)
                return
        if key in self._in_flight:
            return
        self._in_flight.add(key)
        self._pool.start(_ThumbLoader(path, self._signals))

    @Slot(str, QPixmap)
    def _on_thumb_ready(self, path: str, pix: QPixmap) -> None:
        self._in_flight.discard(path)
        try:
            mtime = int(Path(path).stat().st_mtime * 1000)
        except OSError:
            mtime = 0
        self._cache[path] = (mtime, pix)
        card = self._cards.get(path)
        if card:
            card.set_pixmap(pix)

    # ── 事件处理 ──────────────────────────────

    def _on_card_clicked(self, path: str) -> None:
        self.select_path(path)
        self.imageSelectionChanged.emit(path)

    def _on_card_double_clicked(self, path: str) -> None:
        self.select_path(path)
        self.imageActivated.emit(path)
