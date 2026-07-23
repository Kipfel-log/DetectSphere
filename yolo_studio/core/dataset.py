"""数据集索引与划分(取代原 scripts/prepare_dataset.py)。

旧 prepare_dataset.py 的问题:
- 比例硬编码(70/20/10)
- 不能处理空标签(隐式背景)
- 文件名 hash 前缀解析脆弱(`'-'.join(...split('-')[1:])`)
- 重复运行静默覆盖

本模块:
- 比例可配置(默认 70/20/10)
- 隐式背景:无 .txt = 无目标(不写空 .txt)
- 按 stem 匹配,不依赖 Label Studio hash 前缀
- 重划分前确认(由调用方负责)
"""
from __future__ import annotations

import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from yolo_studio.core.io.labels import read_yolo_txt, write_yolo_txt
from yolo_studio.core.io.manifest import IMAGE_EXTS, _is_image
from yolo_studio.core.project import Project


@dataclass
class SplitStats:
    """划分结果统计。"""

    train: int = 0
    val: int = 0
    test: int = 0
    unlabeled: int = 0
    total: int = 0


def list_images(images_dir: Path) -> list[Path]:
    if not images_dir.exists():
        return []
    return sorted(p for p in images_dir.iterdir() if _is_image(p))


def _quick_sha(path: Path, *, chunk: int = 1 << 20) -> str:
    """快速计算 sha256(与 manifest._sha256 重复,这里本地实现避免 import)。"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _quick_fingerprint(path: Path) -> str:
    """快速指纹:`size:mtime`。只对未改动过的文件返回稳定 hash,内容变化会自动改 mtime。

    用途:在不计算 sha256 的前提下做"同一文件识别",够用于 Phase A 的去重/未改动判断。
    真正的 sha256 仅在 manifest.rebuild_from_disk 为新增图建立 DB 记录时计算一次。
    """
    try:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime * 1000)}"
    except OSError:
        return ""


def list_all_images_by_split(project: Project) -> dict[str, list[tuple[Path, str, bool]]]:
    """扫描项目所有图像来源,按 split 分组,按 (size+mtime) 指纹去重。

    优先级:train > val > test > unassigned。同一指纹在更高优先级 split 已出现,
    就从低优先级中排除(避免 UI 重复显示同一文件)。

    返回: { split_name: [(image_path, image_name, has_boxes), ...], ... }
    """
    sources: list[tuple[str, Path, Path | None]] = [
        ("train", project.train_images, project.train_labels),
        ("val", project.val_images, project.val_labels),
        ("test", project.test_images, project.test_labels),
        ("unassigned", project.images_dir, None),
    ]

    buckets: dict[str, list[tuple[Path, str, bool]]] = {s: [] for s, _, _ in sources}
    seen_fp: set[str] = set()

    for split, img_dir, lbl_dir in sources:
        if not img_dir.exists():
            continue
        for p in sorted(img_dir.iterdir()):
            if not _is_image(p):
                continue
            fp = _quick_fingerprint(p)
            if not fp:
                continue
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            has_boxes = False
            if lbl_dir is not None:
                has_boxes = (lbl_dir / (p.stem + ".txt")).exists()
            buckets[split].append((p, p.name, has_boxes))

    return buckets


def split_dataset(
    project: Project,
    *,
    train_ratio: float = 0.7,
    val_ratio: float = 0.2,
    test_ratio: float = 0.1,
    seed: int = 42,
    source: str = "images",
) -> SplitStats:
    """从 data/{source}/ 划分到 data/{train,val,test}/。

    标签同步复制(若存在)。空标签(无 .txt)按 YOLO 隐式背景约定**不**生成空 .txt。

    返回划分统计。
    """
    # 比例容错
    total_ratio = train_ratio + val_ratio + test_ratio
    if abs(total_ratio - 1.0) > 1e-6:
        raise ValueError(f"比例之和应为 1.0,实际 {total_ratio}")

    src_images = project.root / "data" / source / "images" if source != "images" else project.images_dir
    src_labels = project.root / "data" / source / "labels" if source != "images" else (project.root / "data" / source / "labels")

    if not src_images.exists():
        raise FileNotFoundError(f"图像源目录不存在: {src_images}")

    images = list_images(src_images)
    if not images:
        return SplitStats()

    # 收集有标注的图像(必须同时存在 .txt)与无标注的图像
    annotated: list[Path] = []
    unlabeled: list[Path] = []
    for img in images:
        if (src_labels / (img.stem + ".txt")).exists():
            annotated.append(img)
        else:
            unlabeled.append(img)

    # 只对已标注的做随机划分(未标注的留给用户后续标注)
    rng = random.Random(seed)
    rng.shuffle(annotated)
    n = len(annotated)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_set = annotated[:n_train]
    val_set = annotated[n_train : n_train + n_val]
    test_set = annotated[n_train + n_val :]

    # 清空旧的 train/val/test
    for split, dst_img_dir, dst_lbl_dir in [
        ("train", project.train_images, project.train_labels),
        ("val", project.val_images, project.val_labels),
        ("test", project.test_images, project.test_labels),
    ]:
        if dst_img_dir.exists():
            shutil.rmtree(dst_img_dir)
        if dst_lbl_dir.exists():
            shutil.rmtree(dst_lbl_dir)
        dst_img_dir.mkdir(parents=True, exist_ok=True)
        dst_lbl_dir.mkdir(parents=True, exist_ok=True)

    # 复制文件
    for split_set, dst_img_dir, dst_lbl_dir in [
        (train_set, project.train_images, project.train_labels),
        (val_set, project.val_images, project.val_labels),
        (test_set, project.test_images, project.test_labels),
    ]:
        for img in split_set:
            shutil.copy2(img, dst_img_dir / img.name)
            src_lbl = src_labels / (img.stem + ".txt")
            if src_lbl.exists():
                shutil.copy2(src_lbl, dst_lbl_dir / (img.stem + ".txt"))

    return SplitStats(
        train=len(train_set),
        val=len(val_set),
        test=len(test_set),
        unlabeled=len(unlabeled),
        total=len(images),
    )


def save_boxes_for_image(
    project: Project,
    split: str,
    image_name: str,
    boxes,
) -> None:
    """把 boxes 写到对应 split 的 labels 目录(.txt)。

    boxes 来自 yolo_studio.core.io.labels.Box 的列表。
    按隐式背景约定:boxes 为空时**删除**已有 .txt(若存在)。
    """
    if split == "unassigned":
        # 未划分:写回 data/images/ 同名 .txt
        lbl_path = project.images_dir.parent / "labels" / (Path(image_name).stem + ".txt")
    else:
        lbl_dir = {
            "train": project.train_labels,
            "val": project.val_labels,
            "test": project.test_labels,
        }[split]
        lbl_path = lbl_dir / (Path(image_name).stem + ".txt")
    write_yolo_txt(lbl_path, boxes)


def get_split_for_image(project: Project, image_path: Path) -> str:
    """根据 image_path 所在目录判定 split。"""
    p = image_path.resolve()
    for split, d in [
        ("train", project.train_images),
        ("val", project.val_images),
        ("test", project.test_images),
    ]:
        try:
            p.relative_to(d.resolve())
            return split
        except ValueError:
            continue
    return "unassigned"
