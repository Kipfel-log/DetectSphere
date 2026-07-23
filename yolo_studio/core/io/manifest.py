"""文件系统 ↔ SQLite 双向同步。

标注的真理源是文件系统(.txt 文件),DB 是镜像/索引。
启动时调用 rebuild_from_disk():扫描 data/ 下的 train/val/test,把 .jpg/.jpeg/.png 写回 images 表。

性能:
- 已在 DB 里且路径未变的图,**不重新算 sha256**(节省 90% 时间)
- mtime 变化的图会重新哈希(覆盖旧 sha)
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.io.labels import read_yolo_txt
from yolo_studio.core.project import Project

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS


def _sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def rebuild_from_disk(project: Project, db: ProjectDB) -> None:
    """从文件系统重建 images + annotations 表。

    行为:
    - 扫描 data/train/images, data/val/images, data/test/images 下的所有图像
    - 扫描 data/images(原始素材)作为 unassigned
    - **去重**:data/images/ 里的文件如果与某 split 中某文件 sha256 相同,则不重复索引
    - 对每张图:upsert 到 images 表(根据 split),同步 annotations
    - 不删除 DB 中已存在但磁盘上没文件的行(可能用户用了软链或外置存储)

    **EXIF 一次性 bulk 迁移**(幂等):
    对每个 labels_rotated=False 且 EXIF 旋转 != 0 的图,把 .txt 里的旧框
    换算到旋转后坐标空间,写回 .txt,标记 labels_rotated=1。

    性能:
    - 已索引的图(路径在 DB 中)跳过 sha256 计算,只更新 split
    - 新文件才计算 sha256
    """
    from yolo_studio.core.image_utils import load_rotated, transform_boxes

    buckets: list[tuple[str, Path, Path | None]] = [
        # (split_name, images_dir, labels_dir or None)
        ("train", project.train_images, project.train_labels),
        ("val", project.val_images, project.val_labels),
        ("test", project.test_images, project.test_labels),
    ]

    seen_paths: set[str] = set()
    seen_sha_to_path: dict[str, str] = {}  # sha256 → 第一次见到的路径(优先 split)
    for split, images_dir, labels_dir in buckets:
        if not images_dir.exists():
            continue
        for img in images_dir.iterdir():
            if not _is_image(img):
                continue
            path_str = str(img.resolve())
            seen_paths.add(path_str)

            # 查 DB:已索引且 mtime 未变 → 跳过 sha256
            existing = db.get_image_by_path(path_str)
            try:
                mtime = int(img.stat().st_mtime * 1000)
            except OSError:
                continue

            if existing and existing.sha256:
                sha = existing.sha256  # 复用旧 sha
                img_id = existing.id
            else:
                sha = _sha256(img)
                img_id = db.upsert_image(path_str, sha)

            seen_sha_to_path.setdefault(sha, path_str)
            db.set_split(img_id, split)

            if labels_dir is not None:
                txt_path = labels_dir / (img.stem + ".txt")
                boxes = read_yolo_txt(txt_path)

                # 一次性 bulk EXIF 迁移
                if boxes and not db.get_labels_rotated(img_id):
                    _, exif_rot = load_rotated(img)
                    if exif_rot != 0:
                        # 变换 → 写回 .txt → 标记迁移完成
                        boxes = transform_boxes(boxes, exif_rot)
                        from yolo_studio.core.io.labels import write_yolo_txt
                        write_yolo_txt(txt_path, boxes)
                        db.set_labels_rotated(img_id, True)

                db.replace_annotations(
                    img_id,
                    [(b.class_id, b.xc, b.yc, b.w, b.h) for b in boxes],
                )
                if boxes:
                    db.set_done(img_id, True)

    # 原始素材(unassigned) — 已经被划走的(sha256 相同)就不再列入
    if project.images_dir.exists():
        for img in project.images_dir.iterdir():
            if not _is_image(img):
                continue
            path_str = str(img.resolve())
            if path_str in seen_paths:
                continue

            existing = db.get_image_by_path(path_str)
            if existing and existing.sha256:
                sha = existing.sha256
                if sha in seen_sha_to_path:
                    continue
                # 已索引(unassigned),只是更新路径/split
                continue

            sha = _sha256(img)
            if sha in seen_sha_to_path:
                continue
            db.upsert_image(path_str, sha)
