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


def _distribute_items(
    items: list[Path],
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    initial_counts: tuple[int, int, int] = (0, 0, 0),
) -> tuple[list[Path], list[Path], list[Path]]:
    """将 items 按全局最优比例分配到 train/val/test。"""
    train_set, val_set, test_set = [], [], []
    curr_train, curr_val, curr_test = initial_counts
    
    for item in items:
        next_total = curr_train + curr_val + curr_test + 1
        deficits = [
            (next_total * train_ratio - curr_train, "train"),
            (next_total * val_ratio - curr_val, "val"),
            (next_total * test_ratio - curr_test, "test"),
        ]
        deficits.sort(reverse=True, key=lambda x: x[0])
        best_split = deficits[0][1]
        
        if best_split == "train":
            train_set.append(item)
            curr_train += 1
        elif best_split == "val":
            val_set.append(item)
            curr_val += 1
        else:
            test_set.append(item)
            curr_test += 1
            
    return train_set, val_set, test_set


@dataclass
class SplitStats:
    """划分结果统计。"""

    train: int = 0
    val: int = 0
    test: int = 0
    unlabeled: int = 0
    total: int = 0
    mode: str = ""  # 实际使用的 source: images / splits / incremental
    skipped: int = 0  # 因同名冲突被跳过的新图数量(仅 incremental 会用到)


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


def gather_labeled_images(
    project: Project,
    *,
    include_unassigned: bool = True,
) -> list[tuple[Path, str | None]]:
    """从已有 split 目录(或 unassigned)收集**有标注**的图像。

    返回:list of (image_path, source_split_or_None)。
    - 扫 project.{train,val,test}_images,只取同 split 下同名 .txt 存在的图。
    - 若 include_unassigned,再扫 project.images_dir + data/labels/<stem>.txt(若有)。

    用途:`split_dataset(source="splits")` 用这个 pool 来重新划分;项目设置
    的"重新划分"按钮就是这种用法。
    """
    out: list[tuple[Path, str | None]] = []
    seen: set[Path] = set()
    for split in ("train", "val", "test"):
        img_dir = getattr(project, f"{split}_images")
        lbl_dir = getattr(project, f"{split}_labels")
        if not img_dir.exists():
            continue
        for img in sorted(p for p in img_dir.iterdir() if _is_image(p)):
            if img in seen:
                continue
            if (lbl_dir / (img.stem + ".txt")).exists():
                out.append((img, split))
                seen.add(img)
    if include_unassigned:
        img_dir = project.images_dir
        lbl_dir = img_dir.parent / "labels"
        if img_dir.exists():
            for img in sorted(p for p in img_dir.iterdir() if _is_image(p)):
                if img in seen:
                    continue
                if (lbl_dir / (img.stem + ".txt")).exists():
                    out.append((img, None))
                    seen.add(img)
    return out


def split_dataset(
    project: Project,
    *,
    train_ratio: float = 0.7,
    val_ratio: float = 0.2,
    test_ratio: float = 0.1,
    seed: int = 42,
    source: str = "auto",
) -> SplitStats:
    """从源池(有标注的图)划分到 data/{train,val,test}/。

    标签同步复制(若存在)。空标签(无 .txt)按 YOLO 隐式背景约定**不**生成空 .txt。

    source 取值:
      "auto"        — data/images 非空且 train/val/test 也非空时用 "incremental";
                      只有 data/images 非空时用 "images";否则用 "splits"
      "images"      — 仅用 data/images(会清空并重建 train/val/test)
      "splits"      — 仅用已有 train/val/test 的池,整体重新打乱划分(常用于"重新划分")
      "incremental" — 只把 data/images 里的新图划分并追加到 train/val/test,
                      已有数据不动、不重新打乱、不清空

    返回划分统计。
    """
    # 比例容错
    total_ratio = train_ratio + val_ratio + test_ratio
    if abs(total_ratio - 1.0) > 1e-6:
        raise ValueError(f"比例之和应为 1.0,实际 {total_ratio}")

    # 决定源池
    if source == "auto":
        has_new = bool(list_images(project.images_dir))
        has_existing = any(
            list_images(getattr(project, f"{s}_images"))
            for s in ("train", "val", "test")
        )
        if has_new and has_existing:
            source = "incremental"
        else:
            source = "images" if has_new else "splits"

    if source == "incremental":
        return _split_incremental(project, train_ratio, val_ratio, test_ratio, seed)

    annotated: list[Path] = []
    unlabeled: list[Path] = []
    # 记录每张标注图来自哪个 split(用于 source=="splits" 时找回原 .txt)
    orig_split_per_path: dict[Path, str] = {}
    src_labels_per_split = {
        "train": project.train_labels,
        "val": project.val_labels,
        "test": project.test_labels,
    }
    src_labels: Path = project.images_dir.parent / "labels"  # 仅 source=="images" 时使用

    if source == "images":
        src_images = project.images_dir
        src_labels = src_images.parent / "labels"
        if not src_images.exists():
            raise FileNotFoundError(f"图像源目录不存在: {src_images}")
        for img in list_images(src_images):
            if (src_labels / (img.stem + ".txt")).exists():
                annotated.append(img)
            else:
                unlabeled.append(img)
    elif source == "splits":
        # 从已有 train/val/test 收集**所有图**(包括背景图/无标注图),不能只用 gather_labeled_images
        # 否则背景图会在重新划分时被永久丢失(Bug 2)
        pool: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for split in ("train", "val", "test"):
            img_dir = getattr(project, f"{split}_images")
            lbl_dir = getattr(project, f"{split}_labels")
            if not img_dir.exists():
                continue
            for img in sorted(p for p in img_dir.iterdir() if _is_image(p)):
                if img in seen:
                    continue
                seen.add(img)
                pool.append((img, split))
                # 检查有无标注
                if (lbl_dir / (img.stem + ".txt")).exists():
                    annotated.append(img)
                else:
                    unlabeled.append(img)
                orig_split_per_path[img] = split
        if not pool:
            return SplitStats(mode="splits")
    else:
        raise ValueError(f"未知 source: {source!r},应是 images / splits / auto")

    # 把已标注 + 未标注一起打进 pool — YOLO 训练允许无标签(背景图)作为负样本。
    # 否则 data/images/ 里未标注的图永远划不进 train/val/test,统计里永远 15 未划分。
    pool_all = annotated + unlabeled

    rng = random.Random(seed)
    rng.shuffle(pool_all)

    train_set, val_set, test_set = _distribute_items(
        pool_all, train_ratio, val_ratio, test_ratio, initial_counts=(0, 0, 0)
    )

    # 用临时目录暂存,避免 source=splits 时 rmtree 把自己 source 删了再 copy 失败
    import tempfile
    staging = Path(tempfile.mkdtemp(prefix="ds_split_", dir=str(project.root)))
    try:
        st_train_img = staging / "train" / "images"; st_train_img.mkdir(parents=True)
        st_train_lbl = staging / "train" / "labels"; st_train_lbl.mkdir(parents=True)
        st_val_img = staging / "val" / "images"; st_val_img.mkdir(parents=True)
        st_val_lbl = staging / "val" / "labels"; st_val_lbl.mkdir(parents=True)
        st_test_img = staging / "test" / "images"; st_test_img.mkdir(parents=True)
        st_test_lbl = staging / "test" / "labels"; st_test_lbl.mkdir(parents=True)

        for split_set, st_img_dir, st_lbl_dir in [
            (train_set, st_train_img, st_train_lbl),
            (val_set, st_val_img, st_val_lbl),
            (test_set, st_test_img, st_test_lbl),
        ]:
            for img in split_set:
                shutil.copy2(img, st_img_dir / img.name)
                if source == "images":
                    src_lbl = src_labels / (img.stem + ".txt")
                else:  # splits
                    orig_split = orig_split_per_path.get(img)
                    if orig_split is None:
                        continue  # 理论上不会发生(pool_all 里每个 img 都在字典里)
                    src_lbl = src_labels_per_split[orig_split] / (img.stem + ".txt")
                if src_lbl.exists():
                    shutil.copy2(src_lbl, st_lbl_dir / (img.stem + ".txt"))

        # 清空旧的 train/val/test
        for dst_img_dir, dst_lbl_dir in [
            (project.train_images, project.train_labels),
            (project.val_images, project.val_labels),
            (project.test_images, project.test_labels),
        ]:
            if dst_img_dir.exists():
                shutil.rmtree(dst_img_dir)
            if dst_lbl_dir.exists():
                shutil.rmtree(dst_lbl_dir)

        # 把临时目录的最终结果搬回去
        for st_img_dir, st_lbl_dir, dst_img_dir, dst_lbl_dir in [
            (st_train_img, st_train_lbl, project.train_images, project.train_labels),
            (st_val_img, st_val_lbl, project.val_images, project.val_labels),
            (st_test_img, st_test_lbl, project.test_images, project.test_labels),
        ]:
            shutil.move(str(st_img_dir), str(dst_img_dir))
            shutil.move(str(st_lbl_dir), str(dst_lbl_dir))

        # 清空 source 池(避免 data/images/ / data/labels/ 残留旧文件)
        # source=="images" 时清的就是数据源;source=="splits" 时 train/val/test
        # 已经在前面 rmtree 了,但若 pool 来自 unassigned 仍要清。
        if source == "images":
            if project.images_dir.exists():
                shutil.rmtree(project.images_dir)
                project.images_dir.mkdir(parents=True, exist_ok=True)
            if (project.images_dir.parent / "labels").exists():
                shutil.rmtree(project.images_dir.parent / "labels")
    finally:
        # 清理临时目录(若 move 成功,staging 已空;失败也清掉)
        shutil.rmtree(staging, ignore_errors=True)

    return SplitStats(
        train=len(train_set),
        val=len(val_set),
        test=len(test_set),
        unlabeled=0,  # 现在所有图都被分到 train/val/test,不再有遗留
        total=n,
        mode=source,
    )


def _split_incremental(
    project: Project,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
) -> SplitStats:
    """只把 data/images 里的新图划分并追加到已有 train/val/test,不动、不打乱旧数据。

    用于"已经有 train/val/test,又导入了一批新图"的场景 — 旧数据不重新打乱、
    不清空,只对新图做一次性划分并追加进去。若新图与某个 split 里已有同名文件
    冲突,跳过该图(计入 SplitStats.skipped),不覆盖已有数据。
    """
    src_images = project.images_dir
    src_labels = src_images.parent / "labels"

    new_images = list_images(src_images)
    if not new_images:
        return SplitStats(mode="incremental")

    annotated: list[Path] = []
    unlabeled: list[Path] = []
    for img in new_images:
        if (src_labels / (img.stem + ".txt")).exists():
            annotated.append(img)
        else:
            unlabeled.append(img)

    pool_all = annotated + unlabeled
    rng = random.Random(seed)
    rng.shuffle(pool_all)

    curr_train = len(list_images(project.train_images))
    curr_val = len(list_images(project.val_images))
    curr_test = len(list_images(project.test_images))

    train_set, val_set, test_set = _distribute_items(
        pool_all,
        train_ratio,
        val_ratio,
        test_ratio,
        initial_counts=(curr_train, curr_val, curr_test),
    )

    counts = {"train": 0, "val": 0, "test": 0}
    skipped = 0
    copied: list[Path] = []
    for split_name, split_set in [("train", train_set), ("val", val_set), ("test", test_set)]:
        dst_img_dir = getattr(project, f"{split_name}_images")
        dst_lbl_dir = getattr(project, f"{split_name}_labels")
        dst_img_dir.mkdir(parents=True, exist_ok=True)
        dst_lbl_dir.mkdir(parents=True, exist_ok=True)
        for img in split_set:
            dst_img = dst_img_dir / img.name
            if dst_img.exists():
                skipped += 1
                continue
            shutil.copy2(img, dst_img)
            src_lbl = src_labels / (img.stem + ".txt")
            if src_lbl.exists():
                shutil.copy2(src_lbl, dst_lbl_dir / (img.stem + ".txt"))
            counts[split_name] += 1
            copied.append(img)

    # 只清掉已成功搬入 split 的新图(跳过的保留在 data/images,避免丢失)
    for img in copied:
        img.unlink(missing_ok=True)
        lbl = src_labels / (img.stem + ".txt")
        lbl.unlink(missing_ok=True)

    return SplitStats(
        train=counts["train"],
        val=counts["val"],
        test=counts["test"],
        unlabeled=0,
        total=len(copied),
        mode="incremental",
        skipped=skipped,
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
