"""YOLO txt 格式读写。

YOLO 格式:
    <class_id> <x_center> <y_center> <width> <height>
所有坐标归一化到 [0, 1]。

按 YOLO 隐式背景约定:**不写空 .txt 文件**。
  - 无文件 = 未标注 / 无目标
  - 有文件(即使只有一个框) = 已标注
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Box:
    """一个标注框(class_id + 归一化坐标)。"""

    class_id: int
    xc: float
    yc: float
    w: float
    h: float

    def to_yolo_line(self) -> str:
        return f"{self.class_id} {self.xc:.6f} {self.yc:.6f} {self.w:.6f} {self.h:.6f}"

    def to_xyxy_norm(self) -> tuple[float, float, float, float]:
        """返回 (x1, y1, x2, y2),归一化坐标。"""
        x1 = self.xc - self.w / 2
        y1 = self.yc - self.h / 2
        x2 = self.xc + self.w / 2
        y2 = self.yc + self.h / 2
        return x1, y1, x2, y2

    @classmethod
    def from_xyxy_norm(cls, class_id: int, x1: float, y1: float, x2: float, y2: float) -> "Box":
        xc = (x1 + x2) / 2
        yc = (y1 + y2) / 2
        w = x2 - x1
        h = y2 - y1
        return cls(class_id=class_id, xc=xc, yc=yc, w=w, h=h)

    @classmethod
    def from_yolo_line(cls, line: str) -> "Box":
        parts = line.strip().split()
        if len(parts) != 5:
            raise ValueError(f"非法 YOLO 行: {line!r}")
        cls_id, xc, yc, w, h = parts
        return cls(
            class_id=int(cls_id),
            xc=float(xc),
            yc=float(yc),
            w=float(w),
            h=float(h),
        )


def read_yolo_txt(path: Path) -> list[Box]:
    """读取 .txt 标注文件;不存在则返回空列表(隐式背景)。"""
    if not path.exists():
        return []
    boxes: list[Box] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            boxes.append(Box.from_yolo_line(line))
        except ValueError:
            continue
    return boxes


def write_yolo_txt(path: Path, boxes: list[Box]) -> None:
    """写入 .txt 标注文件。

    按 YOLO 隐式背景约定:boxes 为空时**删除**该文件(而不是写空文件)。
    """
    if not boxes:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(b.to_yolo_line() for b in boxes) + "\n"
    path.write_text(content, encoding="utf-8")


def has_label_file(image_path: Path, labels_dir: Path) -> bool:
    """判断 image_path 在 labels_dir 中是否有同名 .txt 标注文件。"""
    return (labels_dir / (image_path.stem + ".txt")).exists()
