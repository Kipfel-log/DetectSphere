"""全局/项目路径解析。

不依赖 Qt。所有路径都用 pathlib.Path,跨平台。
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

# ---------- 仓库根(应用安装位置) ----------
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


# ---------- 用户级全局状态目录 ----------
def user_state_dir() -> Path:
    """返回 ~/.yolo_studio/(或平台对应位置),用于 projects.json、QSettings、缓存。"""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "yolo_studio"


def user_projects_json() -> Path:
    return user_state_dir() / "projects.json"


def user_settings_ini() -> Path:
    return user_state_dir() / "settings.ini"


def user_cache_dir() -> Path:
    return user_state_dir() / "cache"


# ---------- 项目级路径 ----------
def project_data_dir(project_root: Path) -> Path:
    """项目的数据目录(包含 images、train/val/test)。"""
    return project_root / "data"


def project_images_dir(project_root: Path) -> Path:
    return project_data_dir(project_root) / "images"


def project_train_images(project_root: Path) -> Path:
    return project_data_dir(project_root) / "train" / "images"


def project_train_labels(project_root: Path) -> Path:
    return project_data_dir(project_root) / "train" / "labels"


def project_val_images(project_root: Path) -> Path:
    return project_data_dir(project_root) / "val" / "images"


def project_val_labels(project_root: Path) -> Path:
    return project_data_dir(project_root) / "val" / "labels"


def project_test_images(project_root: Path) -> Path:
    return project_data_dir(project_root) / "test" / "images"


def project_test_labels(project_root: Path) -> Path:
    return project_data_dir(project_root) / "test" / "labels"


def project_models_dir(project_root: Path) -> Path:
    return project_root / "models"


def project_registry_json(project_root: Path) -> Path:
    return project_models_dir(project_root) / "registry.json"


def project_configs_dir(project_root: Path) -> Path:
    return project_root / "configs"


def project_dataset_yaml(project_root: Path) -> Path:
    return project_configs_dir(project_root) / "dataset.yaml"


def project_runs_dir(project_root: Path) -> Path:
    return project_root / "runs"


def project_internal_dir(project_root: Path) -> Path:
    """项目内部的 .yolo_studio 目录(SQLite 等)。"""
    return project_root / ".yolo_studio"


def project_db(project_root: Path) -> Path:
    return project_internal_dir(project_root) / "project.db"


def project_snapshots_dir(project_root: Path) -> Path:
    return project_root / "snapshots"


# ---------- 校验 ----------
def is_project_root(path: Path) -> bool:
    """粗略判断 path 是否为合法项目根(至少要能作为项目使用)。"""
    if not path or not path.exists() or not path.is_dir():
        return False
    # 至少包含 data/ 目录,或者我们能让它创建出来
    return True  # 宽松校验;具体校验在 Project 中


# ---------- 训练输出解析(原 visualize_results.py 的逻辑) ----------
def find_latest_run(project_root: Path, sub: str = "train") -> Path | None:
    """递归搜索 runs/ 下最近修改的子目录(用于解析训练产物)。"""
    runs = project_runs_dir(project_root)
    if not runs.exists():
        return None

    candidates: list[tuple[float, Path]] = []
    # Ultralytics 偶尔会把 runs/ 嵌套一次(在 runs/detect/runs/...),所以递归一层
    for p in runs.rglob(""):
        if not p.is_dir():
            continue
        if p.parent.name == sub or any(part == sub for part in p.parts):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, p))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_latest_weights(project_root: Path) -> Path | None:
    """返回项目下最近训练出的 best.pt(若存在)。"""
    runs = project_runs_dir(project_root)
    if not runs.exists():
        return None
    bests = sorted(runs.rglob("best.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return bests[0] if bests else None
