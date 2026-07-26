"""ProjectLoaderWorker — 后台线程进行异步项目数据与索引加载。

设计目标:
- 将 YAML 解析、SQLite 数据库连接、manifest 磁盘重建、模型扫描等所有耗时的 CPU/磁盘 IO 操作放到后台 QThread 运行。
- 主线程 Qt Event Loop 100% 解绑，加载画面的 Spinner 动画维持 60 FPS 极其平滑，毫无卡顿顿挫感。
- 通过 status_changed 信号向主线程发送实时加载进度文本。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QObject, QThread, Signal

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.io.manifest import rebuild_from_disk
from yolo_studio.core.model_registry import scan_models
from yolo_studio.core.project import Project


@dataclass
class LoadedProjectData:
    project: Project
    db: ProjectDB
    classes: list[ClassDef]


class ProjectLoaderWorker(QThread):
    """后台项目加载 Worker。"""

    status_changed = Signal(str)
    finished_loading = Signal(object)  # LoadedProjectData
    failed_loading = Signal(str)

    def __init__(self, project: Project, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project = project

    def run(self) -> None:
        try:
            self.status_changed.emit("正在解析项目类别定义 (dataset.yaml)...")
            classes = load_dataset_yaml(self.project.dataset_yaml)
            self.project.set_classes(classes)

            self.status_changed.emit("正在连接 SQLite 数据库并重建磁盘文件索引...")
            db = ProjectDB(self.project.db_path)
            rebuild_from_disk(self.project, db)

            self.status_changed.emit("正在扫描项目模型与注册表...")
            scan_models(self.project)

            self.status_changed.emit("正在就绪项目核心数据引擎...")
            data = LoadedProjectData(project=self.project, db=db, classes=classes)
            self.finished_loading.emit(data)
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.failed_loading.emit(err)
