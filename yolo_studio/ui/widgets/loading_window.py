"""LoadingWindow — 独立无边框加载窗口。

设计原则:
- 无任何系统窗口按钮（关闭/最小/最大），纯 FramelessWindowHint。
- 使用实色背景（圆角卡片绘制），无 WA_TranslucentBackground（避免背景丢失）。
- 左上角大字 DetectSphere 品牌标题。
- 中央 IndeterminateProgressRing + 项目名 + 状态文字。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    IndeterminateProgressRing,
    TitleLabel,
)


class LoadingWindow(QWidget):
    """独立的无边框加载窗口。"""

    def __init__(self, project_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_name = project_name

        # 无边框 + 置顶工具窗口，无任何系统按钮
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 不透明实色背景（避免背景丢失问题）
        self.setAutoFillBackground(True)
        self.resize(500, 300)

        # 屏幕居中
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

        # 样式：白色背景 + 圆角 + 边框阴影
        self.setStyleSheet("""
            LoadingWindow {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 1px solid #E0E0E0;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 24)
        root.setSpacing(0)

        # ── 左上角品牌大字
        header = QHBoxLayout()
        brand = TitleLabel("DetectSphere", self)
        brand.setStyleSheet(
            "font-size: 26px; font-weight: 900; color: #1a1a1a; background: transparent;"
        )
        header.addWidget(brand)
        header.addStretch(1)
        root.addLayout(header)

        root.addSpacing(16)

        # ── 中央内容区
        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setSpacing(12)

        # 项目名
        self._proj_lbl = BodyLabel(project_name, self)
        self._proj_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._proj_lbl.setStyleSheet("font-size: 16px; color: #333333; background: transparent;")
        center.addWidget(self._proj_lbl)

        # Spinner
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(52, 52)
        self.ring.setStrokeWidth(4)
        center.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignCenter)

        # 状态文字
        self.status_label = CaptionLabel("正在初始化...", self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #666666; font-size: 13px; background: transparent;")
        self.status_label.setWordWrap(True)
        center.addWidget(self.status_label)

        root.addLayout(center, 1)

    def set_status(self, text: str) -> None:
        """更新状态文字并立刻刷新界面。"""
        self.status_label.setText(text)
        # 强制刷新，让 Spinner 在每次状态变化时至少能渲染一帧
        QApplication.processEvents()

    def paintEvent(self, event) -> None:
        """绘制带圆角的实色背景（配合 FramelessWindowHint 使用）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 16, 16)
        painter.fillPath(path, QColor("#FFFFFF"))

        # 边框
        painter.setPen(QColor("#D0D0D0"))
        painter.drawPath(path)

        super().paintEvent(event)
