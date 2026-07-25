"""dataset.yaml 类定义读写 + 类颜色 sidecar。

Ultralytics YOLO 格式:
    path: <abs path>
    train: data/train/images
    val: data/val/images
    test: data/test/images
    nc: 3
    names:
      0: class_a
      1: class_b
      2: class_c

类颜色单独存到 sidecar `<project>/configs/class_colors.json`:
    {"0": "#FF6B6B", "1": "#4ECDC4", ...}
不进入 dataset.yaml(保持 YOLO 标准兼容)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ClassDef:
    """单个类定义。"""

    class_id: int
    name: str
    color: str = ""  # 十六进制 "#RRGGBB";空表示用默认调色板


def load_dataset_yaml(yaml_path: Path) -> list[ClassDef]:
    """读取 dataset.yaml 的类定义(不含颜色,颜色在 sidecar)。

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

    # 合并颜色 sidecar(若存在)
    colors = load_class_colors(yaml_path)
    for c in classes:
        c.color = colors.get(c.class_id, "")
    return classes


def save_dataset_yaml(
    yaml_path: Path,
    classes: list[ClassDef],
    *,
    data_root: Path | str | None = None,
) -> None:
    """写入 dataset.yaml(包含完整字段)。颜色存到 sidecar,**不进 yaml**。

    注意:Ultralytics 8.x 把 `path:` 当作相对 CWD 解析(不是相对 yaml 目录)。
    因此我们用**绝对路径**作为 `path:`,避免歧义。

    data_root:数据根目录绝对路径。None 时默认 yaml_path.parent.parent(项目根)。
    """
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    if data_root is None:
        data_root = yaml_path.parent.parent.resolve()
    else:
        data_root = Path(data_root).resolve()

    names: dict[int, str] = {c.class_id: c.name for c in classes}
    payload = {
        "path": str(data_root),
        "train": "data/train/images",
        "val": "data/val/images",
        "test": "data/test/images",
        "nc": len(classes),
        "names": names,
    }
    yaml_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # 颜色写 sidecar
    color_map = {
        c.class_id: c.color
        for c in classes
        if c.color  # 只保存非空
    }
    save_class_colors(yaml_path, color_map)


# ---------- 类颜色 sidecar ----------
def _colors_path(yaml_path: Path) -> Path:
    return yaml_path.parent / "class_colors.json"


def load_class_colors(yaml_path: Path) -> dict[int, str]:
    """读取 `<project>/configs/class_colors.json` → {class_id: '#RRGGBB'}。

    文件不存在 → {}。失败容错(返回空 dict)。
    """
    p = _colors_path(yaml_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}
    out: dict[int, str] = {}
    for k, v in data.items():
        try:
            cid = int(k)
            if isinstance(v, str) and v:
                out[cid] = v
        except (TypeError, ValueError):
            continue
    return out


def save_class_colors(yaml_path: Path, color_map: dict[int, str]) -> None:
    """写入 `<project>/configs/class_colors.json` 。空 map 也写空 dict(可选)。"""
    p = _colors_path(yaml_path)
    payload = {str(k): v for k, v in color_map.items()}
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- 调色板 fallback ----------
# 类颜色默认调色板(7 色循环),被 AnnotationCanvas 等使用
DEFAULT_CLASS_PALETTE: list[str] = [
    "#FF6B6B",  # 红
    "#4ECDC4",  # 青
    "#FFD93D",  # 黄
    "#6BCB77",  # 绿
    "#4D96FF",  # 蓝
    "#9D6B9D",  # 紫
    "#FF9F45",  # 橙
]


def default_color_for(class_id: int, used: set[str] | None = None) -> str:
    """给 class_id 分配一个默认颜色:优先从调色板选未用的。"""
    if used is None:
        return DEFAULT_CLASS_PALETTE[class_id % len(DEFAULT_CLASS_PALETTE)]
    for c in DEFAULT_CLASS_PALETTE:
        if c not in used:
            used.add(c)
            return c
    return DEFAULT_CLASS_PALETTE[class_id % len(DEFAULT_CLASS_PALETTE)]


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
