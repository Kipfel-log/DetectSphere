"""TestPage — 测试页(摄像头实时检测 + 单图/批量待补)。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import TitleLabel

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.camera_panel import CameraPanel


class TestPage(QWidget):
    """测试页 — 当前只有「摄像头」子页;单图/批量在后续 Phase C 阶段补全。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        layout.addWidget(TitleLabel("摄像头实时检测"))

        self.camera_panel = CameraPanel(project)
        layout.addWidget(self.camera_panel, 1)

    def refresh(self) -> None:
        """刷新模型列表(ModelRegistry 变更时)。"""
        self.camera_panel.refresh_models()