"""qr_utils.py — 二维码生成工具。"""
from __future__ import annotations

import io
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
import qrcode

def generate_qr_pixmap(data: str, size: int | None = None) -> QPixmap:
    """生成标准可扫描的二维码 QPixmap。
    如果提供 `size`，会在保持清晰度的前提下缩放到该尺寸；
    否则返回原始大小。"""
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        qimg = QImage()
        qimg.loadFromData(buf.getvalue())
        pm = QPixmap.fromImage(qimg)
        if size is not None and size != pm.width():
            pm = pm.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pm
    except Exception as e:
        print(f"二维码生成错误: {e}")
        return QPixmap()
