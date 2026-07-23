"""AnnotationCanvas — 标注画布。

QGraphicsView + QGraphicsScene:
  - 背景:图像 (QGraphicsPixmapItem)
  - 前景:标注框 (BoxItem = QGraphicsRectItem 子类)
  - 模式: select (默认) / draw (拖拽创建新框)

快捷键:
  D — 删除选中框
  C — 切换选中框的类(由调用方绑定到 ClassPicker)
  Esc — 退出 draw 模式

信号:
  boxesChanged(list) — boxes 变化时
  modeChanged(str) — 模式切换时
  imageChanged(str) — set_image 时
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from yolo_studio.core.image_utils import load_rotated
from yolo_studio.core.io.labels import Box


# 类颜色(7 种循环)
CLASS_COLORS = [
    QColor("#FF6B6B"),  # 红
    QColor("#4ECDC4"),  # 青
    QColor("#FFD93D"),  # 黄
    QColor("#6BCB77"),  # 绿
    QColor("#4D96FF"),  # 蓝
    QColor("#9D6B9D"),  # 紫
    QColor("#FF9F45"),  # 橙
]


def color_for_class(class_id: int) -> QColor:
    return CLASS_COLORS[class_id % len(CLASS_COLORS)]


class Mode(enum.Enum):
    SELECT = "select"
    DRAW = "draw"


class BoxItem(QGraphicsRectItem):
    """单个标注框。可移动、选中。"""

    def __init__(self, box: Box, image_w: int, image_h: int) -> None:
        x1, y1, x2, y2 = box.to_xyxy_norm()
        rect = QRectF(x1 * image_w, y1 * image_h, (x2 - x1) * image_w, (y2 - y1) * image_h)
        super().__init__(rect)
        self._box = box
        self._image_w = image_w
        self._image_h = image_h
        self._color = color_for_class(box.class_id)

        pen = QPen(self._color, 2)
        pen.setCosmetic(True)  # 不随缩放变化线宽
        self.setPen(pen)
        self.setBrush(QBrush(QColor(self._color.red(), self._color.green(), self._color.blue(), 40)))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    @property
    def box(self) -> Box:
        return self._box

    def set_class_id(self, class_id: int) -> None:
        self._box = Box(
            class_id=class_id,
            xc=self._box.xc,
            yc=self._box.yc,
            w=self._box.w,
            h=self._box.h,
        )
        self._color = color_for_class(class_id)
        pen = QPen(self._color, 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(self._color.red(), self._color.green(), self._color.blue(), 40)))

    def normalized_box(self) -> Box:
        """把当前位置/大小换算为归一化坐标。"""
        rect = self.rect()
        x1 = rect.x() / self._image_w
        y1 = rect.y() / self._image_h
        x2 = (rect.x() + rect.width()) / self._image_w
        y2 = (rect.y() + rect.height()) / self._image_h
        # 限制到 [0, 1]
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))
        x2 = max(0.0, min(1.0, x2))
        y2 = max(0.0, min(1.0, y2))
        return Box.from_xyxy_norm(self._box.class_id, x1, y1, x2, y2)

    # ---- 移动后更新 box 数据 ----
    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # 把位置变化反映到 box
            rect = self.rect()
            self._box = self.normalized_box()
        return super().itemChange(change, value)


class AnnotationCanvas(QGraphicsView):
    """标注画布。"""

    boxesChanged = Signal(list)  # list[Box]
    modeChanged = Signal(str)  # "select" | "draw"
    imageChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))

        self._image_item: Optional[QGraphicsPixmapItem] = None
        self._image_path: Optional[Path] = None
        self._image_w = 0
        self._image_h = 0
        self._box_items: list[BoxItem] = []
        self._mode: Mode = Mode.SELECT
        self._current_class_id: int = 0
        self._draw_start = None  # type: ignore
        self._draw_rect_item: Optional[QGraphicsRectItem] = None
        self._suppress_boxes_changed = False
        self._suppress_save = False  # 加载/初始化时为 True,避免触发 _on_boxes_changed 自动写盘
        self._pending_fit = False  # viewport 未就绪时延迟 fit
        self._exif_rotation = 0  # 0/1/2/3 见 image_utils 模块顶部

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---- 模式 ----
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode | str) -> None:
        if isinstance(mode, str):
            mode = Mode(mode)
        if mode == Mode.DRAW:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self._mode = mode
        self.modeChanged.emit(mode.value)

    def set_current_class_id(self, class_id: int) -> None:
        self._current_class_id = class_id

    # ---- 图像 ----
    def image_path(self) -> Optional[str]:
        return str(self._image_path) if self._image_path else None

    def set_image(self, path: Optional[Path], boxes: list[Box] | None = None) -> None:
        """设置当前图像(并替换 boxes)。

        **重要**:这次调用是"加载/显示",不是"用户修改"。期间:
        - 抑制 _on_boxes_changed 的自动写盘(_suppress_save = True)
        - 不发出 boxesChanged 信号(避免触发自动保存)
        """
        self._suppress_save = True
        self._scene.clear()
        self._image_item = None
        self._box_items = []
        self._draw_rect_item = None

        if path is None or not Path(path).exists():
            self._image_path = None
            self._image_w = 0
            self._image_h = 0
            self._scene.setSceneRect(QRectF(0, 0, 100, 100))
            self._suppress_save = False
            self.imageChanged.emit("")
            return

        self._image_path = Path(path)
        # 用 Pillow 应用 EXIF 旋转(避免手机竖屏照片在 QPixmap 里侧翻)
        qimg, exif_rot = load_rotated(path)
        pix = QPixmap(qimg)
        if pix.isNull():
            self._image_path = None
            self._suppress_save = False
            self.imageChanged.emit("")
            return
        self._exif_rotation = exif_rot

        # 注意:boxes 应当已经在旋转后的坐标空间(由 AnnotatePage 处理 EXIF + DB 标志)

        self._image_item = self._scene.addPixmap(pix)
        self._image_w = pix.width()
        self._image_h = pix.height()
        self._scene.setSceneRect(QRectF(0, 0, self._image_w, self._image_h))

        if boxes:
            for b in boxes:
                self._add_box_item(b)

        self._suppress_save = False
        # 不发出 boxesChanged — boxesChanged 用于"用户修改"事件;
        # 加载时发出会被接收方误以为是"修改"并自动写盘。
        self.imageChanged.emit(str(self._image_path))

        # 注意:此时 viewport 可能还未布局完成(0x0),fit_to_view 会算出退化变换
        # 导致图像不可见。这里只在 viewport 已就绪时才 fit;否则标记 _pending_fit,
        # 等 resizeEvent / showEvent 触发。
        self._maybe_fit_to_view()

    def fit_to_view(self) -> None:
        if self._image_item is None:
            return
        viewport_size = self.viewport().size()
        if viewport_size.width() < 50 or viewport_size.height() < 50:
            # viewport 还没布局 — 等下
            self._pending_fit = True
            return
        self._pending_fit = False
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _maybe_fit_to_view(self) -> None:
        """尝试 fit;若 viewport 未就绪则标记 _pending_fit。"""
        if self._image_item is None:
            return
        viewport_size = self.viewport().size()
        if viewport_size.width() < 50 or viewport_size.height() < 50:
            self._pending_fit = True
            return
        self._pending_fit = False
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pending_fit:
            # 第一次拿到合理尺寸 — fit 一下
            self.fit_to_view()

    def showEvent(self, event):
        super().showEvent(event)
        if self._image_item is not None:
            self.fit_to_view()

    # ---- boxes ----
    def get_boxes(self) -> list[Box]:
        return [item.normalized_box() for item in self._box_items]

    def _add_box_item(self, box: Box) -> BoxItem:
        item = BoxItem(box, self._image_w, self._image_h)
        self._scene.addItem(item)
        self._box_items.append(item)
        return item

    # ---- 鼠标事件 ----
    def mousePressEvent(self, event) -> None:
        if self._mode == Mode.DRAW and self._image_item is not None and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            # 限制在图像内
            x = max(0, min(self._image_w, scene_pos.x()))
            y = max(0, min(self._image_h, scene_pos.y()))
            self._draw_start = (x, y)
            rect = QRectF(x, y, 0, 0)
            pen = QPen(color_for_class(self._current_class_id), 2)
            pen.setCosmetic(True)
            self._draw_rect_item = self._scene.addRect(rect, pen)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._mode == Mode.DRAW and self._draw_rect_item is not None and self._draw_start is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            x = max(0, min(self._image_w, scene_pos.x()))
            y = max(0, min(self._image_h, scene_pos.y()))
            x0, y0 = self._draw_start
            x1, x2 = sorted([x0, x])
            y1, y2 = sorted([y0, y])
            self._draw_rect_item.setRect(QRectF(x1, y1, x2 - x1, y2 - y1))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (
            self._mode == Mode.DRAW
            and self._draw_rect_item is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            rect = self._draw_rect_item.rect()
            self._scene.removeItem(self._draw_rect_item)
            self._draw_rect_item = None
            self._draw_start = None

            # 忽略过小的框
            if rect.width() > 4 and rect.height() > 4:
                x1 = rect.x() / self._image_w
                y1 = rect.y() / self._image_h
                x2 = (rect.x() + rect.width()) / self._image_w
                y2 = (rect.y() + rect.height()) / self._image_h
                box = Box.from_xyxy_norm(self._current_class_id, x1, y1, x2, y2)
                self._add_box_item(box)
                if not self._suppress_save:
                    self.boxesChanged.emit(self.get_boxes())

            # 切回 select 模式
            self.set_mode(Mode.SELECT)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---- 键盘 ----
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            self._delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._mode == Mode.DRAW:
                self.set_mode(Mode.SELECT)
                if self._draw_rect_item is not None:
                    self._scene.removeItem(self._draw_rect_item)
                    self._draw_rect_item = None
                    self._draw_start = None
            event.accept()
            return
        super().keyPressEvent(event)

    def _delete_selected(self) -> None:
        to_remove = [it for it in self._box_items if it.isSelected()]
        for it in to_remove:
            self._scene.removeItem(it)
            self._box_items.remove(it)
        if to_remove and not self._suppress_save:
            self.boxesChanged.emit(self.get_boxes())

    # ---- 滚轮缩放 ----
    def wheelEvent(self, event) -> None:
        if self._image_item is None:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    # ---- 外部 API:设置 boxes(替换现有) ----
    def set_boxes(self, boxes: list[Box]) -> None:
        for it in list(self._box_items):
            self._scene.removeItem(it)
        self._box_items = []
        for b in boxes:
            self._add_box_item(b)
        if not self._suppress_save:
            self.boxesChanged.emit(self.get_boxes())

    # ---- 外部 API:改变选中框的类(快捷键 C 调用) ----
    def cycle_selected_class(self) -> None:
        selected = [it for it in self._box_items if it.isSelected()]
        for it in selected:
            it.set_class_id(self._current_class_id)
        if selected and not self._suppress_save:
            self.boxesChanged.emit(self.get_boxes())
