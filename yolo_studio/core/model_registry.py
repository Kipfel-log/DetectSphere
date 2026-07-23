"""per-project 模型注册表。

registry.json 结构:
{
  "active_model": "best.pt",
  "models": [
    {
      "name": "best.pt",
      "path": "models/best.pt",
      "classes": ["cap_closed", "cap_on_back", "no_cap"],
      "metrics": {"mAP50": 0.995, "mAP50-95": 0.788},
      "parent_run": "runs/train/pen_detection",
      "source": "training",
      "created_at": "2026-07-21T10:25:00",
      "sha256": "..."
    }
  ]
}
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from yolo_studio.core.project import Project


@dataclass
class ModelEntry:
    name: str
    path: str
    classes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    parent_run: str = ""
    source: str = "imported"  # 'training' | 'imported' | 'external'
    created_at: str = ""
    sha256: str = ""


@dataclass
class Registry:
    active_model: str = ""
    models: list[ModelEntry] = field(default_factory=list)


def _sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _read_yaml_classes(pt_path: Path) -> list[str]:
    """从 .pt 文件提取 Ultralytics 嵌入的类名。

    YOLO 模型训练时会保存 names 字典到 ckpt,加载时 model.names 暴露。
    """
    try:
        from ultralytics import YOLO

        model = YOLO(str(pt_path))
        names = model.names
        if isinstance(names, dict):
            return [names[k] for k in sorted(names.keys())]
        return list(names)
    except Exception:
        return []


def load_registry(project: Project) -> Registry:
    p = project.registry_json
    if not p.exists():
        return Registry()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Registry()
    return Registry(
        active_model=data.get("active_model", ""),
        models=[ModelEntry(**m) for m in data.get("models", [])],
    )


def save_registry(project: Project, reg: Registry) -> None:
    project.registry_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_model": reg.active_model,
        "models": [asdict(m) for m in reg.models],
    }
    project.registry_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def scan_models(project: Project) -> list[ModelEntry]:
    """扫描 models/*.pt,与 registry 合并,返回 (发现的所有 .pt 列表)。

    - 已在 registry 里的 → 用 registry 的元数据
    - 不在 registry 里的 → 创建一个默认条目(只读出来源待注册)
    """
    reg = load_registry(project)
    by_name: dict[str, ModelEntry] = {m.name: m for m in reg.models}

    found: list[ModelEntry] = []
    if project.models_dir.exists():
        for pt in sorted(project.models_dir.glob("*.pt")):
            name = pt.name
            if name in by_name:
                entry = by_name[name]
                # 路径漂移则更新
                if entry.path != str(pt):
                    entry.path = str(pt)
                found.append(entry)
            else:
                # 自动注册(默认从导入源,需要扫描 + 读 .pt 的类名)
                classes = _read_yaml_classes(pt)
                found.append(
                    ModelEntry(
                        name=name,
                        path=str(pt),
                        classes=classes,
                        source="imported",
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        sha256=_sha256(pt),
                    )
                )

    # 同步到 registry
    reg.models = found
    if not reg.active_model and found:
        reg.active_model = found[0].name
    save_registry(project, reg)
    return found


def set_active(project: Project, name: str) -> None:
    reg = load_registry(project)
    reg.active_model = name
    save_registry(project, reg)


def get_active_entry(project: Project) -> Optional[ModelEntry]:
    reg = load_registry(project)
    if not reg.active_model:
        return None
    for m in reg.models:
        if m.name == reg.active_model:
            return m
    return None


def add_entry(project: Project, entry: ModelEntry) -> None:
    reg = load_registry(project)
    # 去重 by name
    reg.models = [m for m in reg.models if m.name != entry.name]
    reg.models.append(entry)
    if not reg.active_model:
        reg.active_model = entry.name
    save_registry(project, reg)


def remove_entry(project: Project, name: str) -> None:
    reg = load_registry(project)
    reg.models = [m for m in reg.models if m.name != name]
    if reg.active_model == name:
        reg.active_model = reg.models[0].name if reg.models else ""
    save_registry(project, reg)


def import_model_from(
    project: Project,
    src_pt: Path,
    *,
    dest_name: str | None = None,
) -> ModelEntry:
    """从外部路径导入 .pt 到项目 models/。

    返回新创建的 ModelEntry(已写入 registry)。
    """
    if not src_pt.exists() or src_pt.suffix.lower() != ".pt":
        raise FileNotFoundError(f"无效模型文件: {src_pt}")
    project.models_dir.mkdir(parents=True, exist_ok=True)

    name = dest_name or src_pt.name
    dest = project.models_dir / name
    if dest.resolve() != src_pt.resolve():
        import shutil

        shutil.copy2(src_pt, dest)

    classes = _read_yaml_classes(dest)
    entry = ModelEntry(
        name=name,
        path=str(dest),
        classes=classes,
        source="imported",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        sha256=_sha256(dest),
    )
    add_entry(project, entry)
    return entry
