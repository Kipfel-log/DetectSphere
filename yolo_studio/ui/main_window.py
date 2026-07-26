"""主窗口 — FluentWindow 壳 + 侧栏 + QStackedWidget。

每个功能页(数据集/标注/训练/测试/模型注册/项目设置)作为一个子接口注入。

使用 QTimer.singleShot 分帧异步构造每个页面，确保 LoadingWindow 的 Spinner 动画
在整个初始化过程中保持流畅 60 FPS 旋转，完全无卡顿。
当所有页面构造完成后，发送 ready 信号通知外部关闭加载窗口并显示主窗口。
"""
from __future__ import annotations

import os
from pathlib import Path
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QApplication
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
from yolo_studio.workers.project_loader_worker import LoadedProjectData


class MainWindow(FluentWindow):
    """应用主窗口。

    页面构造分帧完成后发射 ready 信号，使调用方可以在此时关闭 LoadingWindow 并显示主窗口。
    """

    ready = Signal()  # 所有页面装载完毕，可以关闭 LoadingWindow 了
    status_changed = Signal(str)  # 构造进度状态文字
    switchProjectRequested = Signal()

    def __init__(self, project: Project, loaded_data: LoadedProjectData | None = None) -> None:
        super().__init__()
        self.project = project
        self._loaded_data = loaded_data

        if loaded_data is not None:
            self.db = loaded_data.db
        else:
            self.db = ProjectDB(project.db_path)
            classes = load_dataset_yaml(project.dataset_yaml)
            project.set_classes(classes)
            rebuild_from_disk(project, self.db)
            scan_models(project)

        self.setWindowTitle(f"DetectSphere — {project.name}")
        self.resize(1280, 800)

        # 分帧异步构造各页面，第一帧留给 Event Loop 刷新 LoadingWindow 动画
        QTimer.singleShot(0, self._build_step1)

    # -------- 分帧构造步骤 --------

    def _build_step1(self) -> None:
        from yolo_studio.ui.pages.dataset_page import DatasetPage
        from yolo_studio.ui.pages.capture_page import CapturePage

        self.status_changed.emit("正在初始化数据集预览与采集页面...")
        QApplication.processEvents()
        self.dataset_page = DatasetPage(self.project, self.db)
        self.dataset_page.setObjectName("datasetPage")

        self.capture_page = CapturePage(self.project, self.db)
        self.capture_page.setObjectName("capturePage")

        QTimer.singleShot(0, self._build_step2)

    def _build_step2(self) -> None:
        from yolo_studio.ui.pages.annotate_page import AnnotatePage
        self.status_changed.emit("正在初始化标注画布页面...")
        QApplication.processEvents()
        self.annotate_page = AnnotatePage(self.project, self.db)
        self.annotate_page.setObjectName("annotatePage")
        QTimer.singleShot(0, self._build_step3)

    def _build_step3(self) -> None:
        from yolo_studio.ui.pages.train_page import TrainPage
        self.status_changed.emit("正在初始化训练控制台...")
        QApplication.processEvents()
        self.train_page = TrainPage(self.project, self.db)
        self.train_page.setObjectName("trainPage")
        QTimer.singleShot(0, self._build_step4)

    def _build_step4(self) -> None:
        from yolo_studio.ui.pages.model_registry_page import ModelRegistryPage
        from yolo_studio.ui.pages.test_page import TestPage
        self.status_changed.emit("正在初始化模型注册表与测试引擎...")
        QApplication.processEvents()
        self.model_registry_page = ModelRegistryPage(self.project, self.db)
        self.model_registry_page.setObjectName("modelRegistryPage")
        self.test_page = TestPage(self.project, self.db)
        self.test_page.setObjectName("testPage")
        QTimer.singleShot(0, self._build_step5)

    def _build_step5(self) -> None:
        from yolo_studio.ui.pages.project_settings_page import ProjectSettingsPage
        self.status_changed.emit("正在初始化项目设置页面...")
        QApplication.processEvents()
        self.project_settings_page = ProjectSettingsPage(self.project, self.db)
        self.project_settings_page.setObjectName("projectSettingsPage")
        QTimer.singleShot(0, self._build_finish)

    def _build_finish(self) -> None:
        self.status_changed.emit("正在绑定信号与侧边栏导航...")
        # 信号连接
        self.project_settings_page.classesChanged.connect(self._on_classes_changed)
        self.project_settings_page.datasetChanged.connect(self.annotate_page.refresh)
        self.project_settings_page.datasetChanged.connect(self.dataset_page.refresh)
        self.train_page.modelRegistered.connect(self._on_model_registered)
        self.model_registry_page.modelsChanged.connect(self.test_page.refresh)
        self.model_registry_page.modelsChanged.connect(self.train_page.refresh_models)
        self.dataset_page.imagesChanged.connect(self.annotate_page.refresh)

        # 填充类定义
        self.annotate_page.refresh_classes(self.project.classes)

        # 侧栏导航
        self._add_navigation()

        # 通知外部：所有页面就绪，可以展示主窗口并关闭 LoadingWindow
        self.ready.emit()

    # ---- 导航 ----
    def _add_navigation(self) -> None:
        self.addSubInterface(self.dataset_page, FIF.PHOTO, "数据集", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.capture_page, FIF.VIDEO, "采集", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.annotate_page, FIF.EDIT, "标注", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.train_page, FIF.PLAY, "训练", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.model_registry_page, FIF.ROBOT, "模型", position=NavigationItemPosition.TOP)
        self.addSubInterface(self.test_page, FIF.CAMERA, "测试", position=NavigationItemPosition.TOP)
        self.navigationInterface.addItem(
            routeKey="openFolder",
            icon=FIF.FOLDER,
            text="打开项目文件夹",
            onClick=self._open_project_folder,
            position=NavigationItemPosition.BOTTOM,
        )
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
        self.train_page.refresh_models()

    # ---- 模型注册广播 ----
    def _on_model_registered(self, path: str) -> None:
        self.model_registry_page.refresh()
        self.train_page.refresh_models()
        self.test_page.refresh()

    # ---- 切换项目 ----
    def request_switch_project(self) -> None:
        if not MessageDialog("切换项目", "确定要关闭当前项目并返回启动器吗?", self).exec():
            return
        self.switchProjectRequested.emit()
        self.close()

    def _open_project_folder(self) -> None:
        """用系统默认文件管理器打开项目根目录。"""
        os.startfile(str(self.project.root))

    # ---- 关闭事件 ----
    def closeEvent(self, event) -> None:
        try:
            if hasattr(self, "train_page") and self.train_page._worker is not None and self.train_page._worker.isRunning():
                self.train_page._worker.request_stop()
                self.train_page._worker.wait(2000)
            if hasattr(self, "test_page"):
                cam_worker = self.test_page.camera_panel._worker
                if cam_worker is not None and cam_worker.isRunning():
                    cam_worker.stop()
                    cam_worker.wait(2000)
            if hasattr(self, "capture_page"):
                cap_worker = self.capture_page._worker
                if cap_worker is not None and cap_worker.isRunning():
                    cap_worker.stop()
                    cap_worker.wait(2000)
            if hasattr(self, "db"):
                self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
