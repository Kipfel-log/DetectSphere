"""主窗口 — FluentWindow 壳 + 侧栏 + QStackedWidget。

每个功能页(数据集/标注/训练/测试/模型注册/项目设置)作为一个子接口注入。

Phase A:Dataset、Annotate、Project Settings
Phase B:+ Train、Model Registry
Phase C/D/E:陆续补全
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    NavigationItemPosition,
)

from yolo_studio.core.class_config import load_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.io.manifest import rebuild_from_disk
from yolo_studio.core.model_registry import scan_models
from yolo_studio.core.project import Project
from yolo_studio.ui.pages.annotate_page import AnnotatePage
from yolo_studio.ui.pages.dataset_page import DatasetPage
from yolo_studio.ui.pages.model_registry_page import ModelRegistryPage
from yolo_studio.ui.pages.project_settings_page import ProjectSettingsPage
from yolo_studio.ui.pages.train_page import TrainPage


class MainWindow(FluentWindow):
    """应用主窗口。"""

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

        # 加载类定义
        classes = load_dataset_yaml(project.dataset_yaml)
        project.set_classes(classes)

        # 打开 SQLite + 重建 manifest
        self.db = ProjectDB(project.db_path)
        rebuild_from_disk(project, self.db)

        # 扫描模型
        scan_models(project)

        self.setWindowTitle(f"YOLO Studio — {project.name}")
        self.resize(1280, 800)

        # 构造各页面
        self.dataset_page = DatasetPage(project, self.db)
        self.dataset_page.setObjectName("datasetPage")
        self.annotate_page = AnnotatePage(project, self.db)
        self.annotate_page.setObjectName("annotatePage")
        self.train_page = TrainPage(project, self.db)
        self.train_page.setObjectName("trainPage")
        self.model_registry_page = ModelRegistryPage(project, self.db)
        self.model_registry_page.setObjectName("modelRegistryPage")
        self.project_settings_page = ProjectSettingsPage(project, self.db)
        self.project_settings_page.setObjectName("projectSettingsPage")

        # 信号连接
        self.project_settings_page.classesChanged.connect(self._on_classes_changed)
        self.train_page.modelRegistered.connect(self._on_model_registered)

        # 侧栏导航
        self._add_navigation()

    # ---- 导航 ----
    def _add_navigation(self) -> None:
        self.addSubInterface(self.dataset_page, FIF.PHOTO, "数据集", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.annotate_page, FIF.EDIT, "标注", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.train_page, FIF.PLAY, "训练", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.model_registry_page, FIF.ROBOT, "模型", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.project_settings_page, FIF.SETTING, "项目设置", position=NavigationItemPosition.BOTTOM)

    # ---- 类变更广播 ----
    def _on_classes_changed(self, classes: list) -> None:
        self.dataset_page.refresh_classes(classes)
        self.annotate_page.refresh_classes(classes)
        self.train_page.refresh_models()  # class 改了可能跟预训练类不再匹配

    # ---- 模型注册广播(训练完成 / 外部导入) ----
    def _on_model_registered(self, path: str) -> None:
        self.model_registry_page.refresh()
        self.train_page.refresh_models()

    # ---- 关闭事件 ----
    def closeEvent(self, event) -> None:
        try:
            # 训练中关闭:先停
            if self.train_page._worker is not None and self.train_page._worker.isRunning():
                self.train_page._worker.request_stop()
                self.train_page._worker.wait(2000)
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
