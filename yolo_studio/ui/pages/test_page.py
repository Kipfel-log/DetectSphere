"""TestPage — 推理测试页 (整合摄像头、单图、批量文件夹三种推理测试模式)。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import SegmentedWidget, TitleLabel

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.camera_panel import CameraPanel
from yolo_studio.ui.widgets.folder_test_panel import FolderTestPanel
from yolo_studio.ui.widgets.image_test_panel import ImageTestPanel


class TestPage(QWidget):
    """测试页 — 支持 📷 摄像头、🖼️ 单图、📁 批量 3 种推理模式。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 头部标题与模式导航
        layout.addWidget(TitleLabel("模型推理测试"))

        self.pivot = SegmentedWidget(self)
        self.stacked_widget = QStackedWidget(self)

        # 子面板
        self.camera_panel = CameraPanel(project, self)
        self.image_panel = ImageTestPanel(project, self)
        self.folder_panel = FolderTestPanel(project, self)

        # 添加到 StackedWidget
        self.stacked_widget.addWidget(self.camera_panel)
        self.stacked_widget.addWidget(self.image_panel)
        self.stacked_widget.addWidget(self.folder_panel)

        # 关联 SegmentedWidget 标签项
        self.pivot.addItem(
            routeKey="camera",
            text="📷 摄像头实时检测",
            onClick=lambda: self.stacked_widget.setCurrentWidget(self.camera_panel),
        )
        self.pivot.addItem(
            routeKey="image",
            text="🖼️ 单图推理测试",
            onClick=lambda: self.stacked_widget.setCurrentWidget(self.image_panel),
        )
        self.pivot.addItem(
            routeKey="folder",
            text="📁 批量文件夹测试",
            onClick=lambda: self.stacked_widget.setCurrentWidget(self.folder_panel),
        )

        self.pivot.setCurrentItem("camera")
        self.stacked_widget.setCurrentWidget(self.camera_panel)

        layout.addWidget(self.pivot)
        layout.addWidget(self.stacked_widget, 1)

    def refresh(self) -> None:
        """刷新所有子面板的模型下拉列表(当 ModelRegistry 发生变更时)。"""
        self.camera_panel.refresh_models()
        self.image_panel.refresh_models()
        self.folder_panel.refresh_models()