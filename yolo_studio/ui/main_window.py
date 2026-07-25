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
    MessageDialog,
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
from yolo_studio.ui.pages.test_page import TestPage
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

        self.setWindowTitle(f"DetectSphere — {project.name}")
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
        self.test_page = TestPage(project, self.db)
        self.test_page.setObjectName("testPage")
        self.project_settings_page = ProjectSettingsPage(project, self.db)
        self.project_settings_page.setObjectName("projectSettingsPage")

        # 信号连接
        self.project_settings_page.classesChanged.connect(self._on_classes_changed)
        self.project_settings_page.datasetChanged.connect(self.annotate_page.refresh)
        self.project_settings_page.datasetChanged.connect(self.dataset_page.refresh)
        self.train_page.modelRegistered.connect(self._on_model_registered)
        self.model_registry_page.modelsChanged.connect(self.test_page.refresh)
        # 数据集页面图像变化 → 标注页刷新
        self.dataset_page.imagesChanged.connect(self.annotate_page.refresh)
        # 启动时把当前项目的类推给标注页(填充 RadioButton)
        self.annotate_page.refresh_classes(project.classes)

        # 侧栏导航
        self._add_navigation()

    # ---- 导航 ----
    def _add_navigation(self) -> None:
        self.addSubInterface(self.dataset_page, FIF.PHOTO, "数据集", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.annotate_page, FIF.EDIT, "标注", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.train_page, FIF.PLAY, "训练", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.model_registry_page, FIF.ROBOT, "模型", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.test_page, FIF.CAMERA, "摄像头", position=NavigationItemPosition.TOP)
        # 「切换项目」侧栏 action(用 addItem 走 FluentWindow navigationInterface API)
        # 用 package-level icon + text 触发 self.request_switch_project
        self.navigationInterface.addItem(
            routeKey="switchProject",
            icon=FIF.RETURN,
            text="切换项目",
            onClick=self.request_switch_project,
            position=NavigationItemPosition.BOTTOM,
        )
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
        self.test_page.refresh()  # 刷新摄像头页面的模型下拉

    # ---- 切换项目(回到启动器) ----
    def request_switch_project(self) -> None:
        """用户点「切换项目」侧栏 action → 关主窗口,app.py 看到标志再开启动器。"""
        if not MessageDialog(
            "切换项目",
            "关闭当前项目并返回启动器?\n\n(未保存的标注已在修改时自动写入 .txt)",
            self,
        ).exec():
            return
        self._switching_project = True
        self.close()

    # ---- 关闭事件 ----
    def closeEvent(self, event) -> None:
        try:
            # 训练中关闭:先停
            if self.train_page._worker is not None and self.train_page._worker.isRunning():
                self.train_page._worker.request_stop()
                self.train_page._worker.wait(2000)
            # 摄像头:先停
            cam_worker = self.test_page.camera_panel._worker
            if cam_worker is not None and cam_worker.isRunning():
                cam_worker.stop()
                cam_worker.wait(2000)
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
