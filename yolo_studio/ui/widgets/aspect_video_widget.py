"""AspectVideoWidget — 自适应等比例居中视频/图片渲染控件。

用于在 QWidget 中以 100% 原始长宽比 (KeepAspectRatio Letterbox Center Fit) 渲染 QPixmap 画面，
无论容器尺寸如何改变均绝不拉伸、绝不变形。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class AspectVideoWidget(QWidget):
    """自适应等比例居中渲染视频/图片画面。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._text = "摄像头未启动\n点击上方「启动摄像头」按钮进行采集"
        self.setMinimumSize(280, 210)
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
