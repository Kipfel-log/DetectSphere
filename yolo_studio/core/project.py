"""Project 数据类 — 表示一个 YOLO Studio 项目。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yolo_studio.core.class_config import ClassDef


@dataclass
class Project:
    """单个项目的内存表示。

    不持有任何 IO 资源(数据库、模型),这些通过外部 service 注入。
    仅作为路径 + 元数据容器。
    """

    root: Path
    name: str = ""
    description: str = ""
    _classes: list["ClassDef"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.root.name

    # ---- 类定义代理 ----
    @property
    def classes(self) -> list["ClassDef"]:
        return self._classes

    def set_classes(self, classes: list["ClassDef"]) -> None:
        self._classes = list(classes)

    def num_classes(self) -> int:
        return len(self._classes)

    # ---- 路径便捷访问 ----
    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def images_dir(self) -> Path:
        return self.root / "data" / "images"

    @property
    def train_images(self) -> Path:
        return self.root / "data" / "train" / "images"

    @property
    def train_labels(self) -> Path:
        return self.root / "data" / "train" / "labels"

    @property
    def val_images(self) -> Path:
        return self.root / "data" / "val" / "images"

    @property
    def val_labels(self) -> Path:
        return self.root / "data" / "val" / "labels"

    @property
    def test_images(self) -> Path:
        return self.root / "data" / "test" / "images"

    @property
    def test_labels(self) -> Path:
        return self.root / "data" / "test" / "labels"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def configs_dir(self) -> Path:
        return self.root / "configs"

    @property
    def dataset_yaml(self) -> Path:
        return self.root / "configs" / "dataset.yaml"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def internal_dir(self) -> Path:
        return self.root / ".yolo_studio"

    @property
    def db_path(self) -> Path:
        return self.root / ".yolo_studio" / "project.db"

    @property
    def registry_json(self) -> Path:
        return self.root / "models" / "registry.json"
