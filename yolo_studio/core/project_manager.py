"""项目注册表:打开/创建项目,维护最近项目列表。

全局状态:~/.yolo_studio/projects.json
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml, save_dataset_yaml
from yolo_studio.core.paths import (
    user_projects_json,
)
from yolo_studio.core.project import Project


@dataclass
class ProjectEntry:
    """全局注册表中的项目条目(轻量元数据)。"""

    path: str
    name: str
    last_opened: float = 0.0
    classes_count: int = 0
    image_count: int = 0


@dataclass
class ProjectsFile:
    """projects.json 文件结构。"""

    projects: list[ProjectEntry] = field(default_factory=list)
    last_project_path: str = ""

    def to_dict(self) -> dict:
        return {
            "projects": [asdict(p) for p in self.projects],
            "last_project_path": self.last_project_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectsFile":
        projects = [ProjectEntry(**p) for p in data.get("projects", [])]
        return cls(
            projects=projects,
            last_project_path=data.get("last_project_path", ""),
        )


class ProjectManager:
    """项目注册表管理。"""

    def __init__(self, json_path: Path | None = None) -> None:
        self._path = json_path or user_projects_json()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> ProjectsFile:
        if not self._path.exists():
            return ProjectsFile()
        try:
            return ProjectsFile.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError):
            # 文件损坏 → 备份并重置
            backup = self._path.with_suffix(".json.bak")
            try:
                shutil.copy2(self._path, backup)
            except OSError:
                pass
            return ProjectsFile()

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 列表 / 查询 ----
    def list_recent(self) -> list[ProjectEntry]:
        """返回最近项目(按 last_opened 倒序,排除不存在的路径)。"""
        alive: list[ProjectEntry] = []
        for p in self._data.projects:
            if Path(p.path).exists():
                alive.append(p)
        # 重新排序(顺手清理掉不存在的)
        alive.sort(key=lambda e: e.last_opened, reverse=True)
        if len(alive) != len(self._data.projects):
            self._data.projects = alive
            self._save()
        return alive

    def get_last_project(self) -> Optional[ProjectEntry]:
        last = self._data.last_project_path
        if not last:
            return None
        for p in self._data.projects:
            if p.path == last and Path(p.path).exists():
                return p
        return None

    # ---- 注册 / 更新 ----
    def touch(self, project: Project) -> None:
        """把 project 标记为最近打开(并更新统计)。"""
        # 先 remove 旧条目
        self._data.projects = [p for p in self._data.projects if p.path != str(project.root)]

        classes = load_dataset_yaml(project.dataset_yaml)
        images = project.images_dir
        image_count = 0
        if images.exists():
            image_count = sum(1 for _ in images.iterdir() if _.is_file())

        entry = ProjectEntry(
            path=str(project.root),
            name=project.name,
            last_opened=time.time(),
            classes_count=len(classes),
            image_count=image_count,
        )
        self._data.projects.insert(0, entry)
        self._data.last_project_path = str(project.root)
        self._save()

    def forget(self, path: str) -> None:
        """从注册表移除某项目(不删除文件)。"""
        self._data.projects = [p for p in self._data.projects if p.path != path]
        if self._data.last_project_path == path:
            self._data.last_project_path = self._data.projects[0].path if self._data.projects else ""
        self._save()

    # ---- 创建 / 打开 ----
    def open(self, path: Path) -> Project:
        """打开现有项目(必须存在且至少含 data/ 或 configs/dataset.yaml)。"""
        path = path.resolve()
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"项目目录不存在: {path}")

        # 容错:若结构不存在,自动补全
        ensure_layout(path, create_if_missing=True)

        classes = load_dataset_yaml(path / "configs" / "dataset.yaml")
        proj = Project(root=path)
        proj.set_classes(classes)
        self.touch(proj)
        return proj

    def create(self, path: Path, name: str, classes: list[ClassDef]) -> Project:
        """创建新项目(目录必须不存在或为空)。"""
        path = path.resolve()
        if path.exists() and any(path.iterdir()):
            # 允许当已存在 dataset.yaml 时复用,否则报错
            if not (path / "configs" / "dataset.yaml").exists():
                raise FileExistsError(f"目录已存在且非空: {path}")
        path.mkdir(parents=True, exist_ok=True)
        ensure_layout(path, create_if_missing=True)
        save_dataset_yaml(path / "configs" / "dataset.yaml", classes)
        proj = Project(root=path, name=name)
        proj.set_classes(classes)
        self.touch(proj)
        return proj


def ensure_layout(project_root: Path, *, create_if_missing: bool = True) -> None:
    """确保项目目录结构完整。

    若 create_if_missing=False,缺哪个就 FileNotFoundError。
    """
    subdirs = [
        project_root / "data" / "images",
        project_root / "data" / "train" / "images",
        project_root / "data" / "train" / "labels",
        project_root / "data" / "val" / "images",
        project_root / "data" / "val" / "labels",
        project_root / "data" / "test" / "images",
        project_root / "data" / "test" / "labels",
        project_root / "models",
        project_root / "configs",
        project_root / "runs",
        project_root / ".yolo_studio",
    ]
    for d in subdirs:
        if not d.exists():
            if not create_if_missing:
                raise FileNotFoundError(f"项目目录缺少: {d}")
            d.mkdir(parents=True, exist_ok=True)
