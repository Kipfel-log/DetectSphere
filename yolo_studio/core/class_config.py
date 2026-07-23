"""dataset.yaml 类定义读写。

Ultralytics YOLO 格式:
    path: ../data
    train: train/images
    val: val/images
    nc: 3
    names:
      0: class_a
      1: class_b
      2: class_c
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ClassDef:
    """单个类定义。"""

    class_id: int
    name: str


def load_dataset_yaml(yaml_path: Path) -> list[ClassDef]:
    """读取 dataset.yaml 的类定义。

    文件不存在或格式异常时返回空列表。
    """
    if not yaml_path.exists():
        return []
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []

    names = data.get("names", [])
    classes: list[ClassDef] = []
    if isinstance(names, dict):
        for cid_str, name in names.items():
            try:
                classes.append(ClassDef(class_id=int(cid_str), name=str(name)))
            except (TypeError, ValueError):
                continue
    elif isinstance(names, list):
        for idx, name in enumerate(names):
            classes.append(ClassDef(class_id=idx, name=str(name)))
    classes.sort(key=lambda c: c.class_id)
    return classes


def save_dataset_yaml(
    yaml_path: Path,
    classes: list[ClassDef],
    *,
    data_root_rel: str = "../data",
) -> None:
    """写入 dataset.yaml(包含完整字段)。"""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    names: dict[int, str] = {c.class_id: c.name for c in classes}
    payload = {
        "path": data_root_rel,
        "train": "train/images",
        "val": "val/images",
        "test": "test/images",
        "nc": len(classes),
        "names": names,
    }
    yaml_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def remap_classes_after_edit(
    old_classes: list[ClassDef],
    new_classes: list[ClassDef],
) -> dict[int, int]:
    """类编辑后,计算旧 class_id → 新 class_id 的映射。

    按 name 匹配:name 相同的,新 class_id 是新定义里的 class_id。
    name 被删除/改名的类,不在映射中(调用方需决定如何处理其 .txt 文件)。
    """
    by_name = {c.name: c.class_id for c in new_classes}
    mapping: dict[int, int] = {}
    for old in old_classes:
        if old.name in by_name:
            mapping[old.class_id] = by_name[old.name]
    return mapping
