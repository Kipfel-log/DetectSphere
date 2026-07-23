"""图像工具:EXIF 自动旋转 + 框坐标变换。

QPixmap 默认不应用 EXIF orientation —— 所有手机竖屏照片会显示成侧翻。
本模块提供统一入口:
  - load_rotated(path) → (QImage, exif_rotation_applied)
  - transform_box(box, exif_rotation) → Box(已变换到新画布)
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

from PIL import Image, ImageOps
from PySide6.QtGui import QImage

from yolo_studio.core.io.labels import Box


# 旋转常量(0/1/2/3) — 与 EXIF orientation 对应,但语义是"应用到画布的旋转方向"
#   0 = 无需变换
#   1 = 90° CW   (orientation 转 8 — 拍摄存储时已转 90° CW for display)
#   2 = 180°
#   3 = 90° CCW  (orientation 转 6)


def _detect_exif_rotation(pil_img: Image.Image) -> int:
    """读 PIL Image 的 EXIF orientation,返回 0/1/2/3。

    0 表示 orientation==1(无需变换)。
    """
    try:
        exif = pil_img._getexif() or {}
        raw = exif.get(274) or exif.get("Orientation")
        if raw is None:
            return 0
        # EXIF 值 → 我们的 internal code
        if raw == 1:
            return 0
        if raw == 2:
            return 2  # 镜像翻转 + 上下翻转? 简化为 180
        if raw == 3:
            return 2
        if raw == 4:
            return 2
        if raw == 5:
            return 1
        if raw == 6:
            return 3  # 90° CCW to display
        if raw == 7:
            return 1
        if raw == 8:
            return 1  # 90° CW to display
        return 0
    except Exception:
        return 0


def load_rotated(path: Path) -> Tuple[QImage, int]:
    """读图 + 应用 EXIF 旋转,返回(QImage, exif_rotation)。

    调用方负责把 QImage 喂给 QPixmap.fromImage。
    exif_rotation 取值见模块顶部 0/1/2/3。
    """
    path = Path(path)
    try:
        pil = Image.open(str(path))
    except Exception:
        # 读失败 → 返回空 QImage,旋转 0
        return QImage(), 0

    exif_rot = _detect_exif_rotation(pil)
    if exif_rot != 0:
        # ImageOps.exif_transpose 综合处理镜像/旋转,等价于按 EXIF 转正
        pil = ImageOps.exif_transpose(pil)
        if pil is None:
            pil = Image.open(str(path))

    # 转换模式:CMYK/P 之类 → RGB(避免 Qt 解码失败)
    if pil.mode not in ("RGB", "RGBA"):
        pil = pil.convert("RGB")

    # PIL → QImage(bytes 法):把图像编为 PNG/BMP bytes 再 QImage.fromData
    # 更快做法:把 PIL 的 bytes 直接交给 Qt — 用 image.bits().tobytes() 需要 stride。
    # 简化版:用 BMP 编码(无依赖、原生支持 24/32bit)
    import io

    buf = io.BytesIO()
    pil.save(buf, format="BMP")
    qimg = QImage.fromData(buf.getvalue(), "BMP")
    return qimg, exif_rot


def transform_box(box: Box, exif_rotation: int) -> Box:
    """将框坐标从"原始(未旋转)"坐标空间变换到"已应用 EXIF 旋转"坐标空间。

    全部归一化坐标 [0, 1]。
    返回新 Box(class_id 不变)。
    """
    if exif_rotation == 0:
        return box
    xc, yc, w, h = box.xc, box.yc, box.w, box.h
    if exif_rotation == 1:  # 90° CW
        # (x, y) in original → (1 - y, x) in rotated
        return Box(box.class_id, xc=1 - yc, yc=xc, w=h, h=w)
    if exif_rotation == 2:  # 180°
        return Box(box.class_id, xc=1 - xc, yc=1 - yc, w=w, h=h)
    if exif_rotation == 3:  # 90° CCW
        return Box(box.class_id, xc=yc, yc=1 - xc, w=h, h=w)
    return box


def transform_boxes(boxes: list[Box], exif_rotation: int) -> list[Box]:
    """批量变换。"""
    if exif_rotation == 0:
        return boxes
    return [transform_box(b, exif_rotation) for b in boxes]
