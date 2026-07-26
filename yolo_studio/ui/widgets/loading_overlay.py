"""LoadingOverlay — 主窗口全屏加载动画与状态提示浮层。

设计目标:
- 用户从 LauncherDialog 点击打开/新建项目后，主窗口立即全屏显示，浮层覆盖全屏。
- 窗口左上角显示醒目的大字项目名：DetectSphere。
- 窗口中央展示美观的 Fluent 环形加载动画与当前项目名称。
- 下方实时滚动显示当前加载步骤小字（例如“正在初始化数据库...”、“正在载入标注画布...”），异步加载不卡顿。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    SubtitleLabel,
    TitleLabel,
)


class LoadingOverlay(QWidget):
    """全屏沉浸式加载浮层。"""

    def __init__(self, project_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_name = project_name

        if parent:
            self.setGeometry(parent.rect())

        self.setStyleSheet("background-color: rgba(245, 246, 248, 0.98);")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ---- 1. 左上角大字项目名: DetectSphere ----
        header = QHBoxLayout()
        header.setContentsMargins(36, 28, 36, 0)
        self.brand_title = TitleLabel("DetectSphere", self)
        self.brand_title.setStyleSheet("font-size: 32px; font-weight: 900; color: #1e1e1e;")
        header.addWidget(self.brand_title)
        header.addStretch(1)

        main_layout.addLayout(header)

        # ---- 2. 居中区域 ----
        center_layout = QVBoxLayout()
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 卡片容器
        card = CardWidget(self)
        card.setFixedSize(540, 320)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(16)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = SubtitleLabel("正在载入项目", card)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.title_label)

        self.project_label = TitleLabel(project_name, card)
        self.project_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.project_label)

        # 底部状态提示
        self.status_label = CaptionLabel("正在异步初始化核心数据组件...", card)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #666666; font-size: 13px;")
        card_layout.addWidget(self.status_label)

        center_layout.addWidget(card)
        main_layout.addLayout(center_layout, 1)

    def set_status(self, text: str) -> None:
        """更新底部小字加载提示。"""
        self.status_label.setText(text)

    def resizeEvent(self, event) -> None:
        if self.parent():
            self.setGeometry(self.parent().rect())
        super().resizeEvent(event)
