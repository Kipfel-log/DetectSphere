"""LoadingWindow — Unity 风格沉浸式无边框启动加载窗口。

设计规范 (参考 Unity 启动 Splash 界面):
- 顶部 (70% 高度): 沉浸式海报 banner 图 (yolo_studio/assets/splash.png)。
- 分隔线: 底部绿/青色 Accent 进度条线。
- 底部 (30% 高度): 深色底色 (#080808)。
  - 左侧: 品牌 Logo 组合 ("DetectSphere®") + 底部版权宣告 ("© 2026 DetectSphere Studio")。
  - 右侧: 顶部小字状态 ("Open Project: 初始化数据库...") + 大字项目名 ("TEST1")，底部大字版本号 ("2026.1")。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from yolo_studio import __version__
from yolo_studio.core.paths import REPO_ROOT


def _get_splash_path() -> Path:
    """返回 Splash 图片资产路径 (yolo_studio/assets/splash.png)。"""
    return REPO_ROOT / "yolo_studio" / "assets" / "splash.png"


class SplashBannerWidget(QWidget):
    """顶部 Banner 控件 — 保持像素长宽比 (KeepAspectRatioByExpanding 居中裁切)，绝不拉伸变形。"""

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self.setFixedHeight(270)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._pixmap.isNull():
            painter.fillRect(self.rect(), QColor("#111116"))
            return

        target_rect = QRectF(self.rect())
        pw, ph = self._pixmap.width(), self._pixmap.height()
        tw, th = target_rect.width(), target_rect.height()

        # 计算 KeepAspectRatioByExpanding (Cover 效果)
        scale = max(tw / pw, th / ph)
        sw = tw / scale
        sh = th / scale
        sx = (pw - sw) / 2
        sy = (ph - sh) / 2

        src_rect = QRectF(sx, sy, sw, sh)
        painter.drawPixmap(target_rect, self._pixmap, src_rect)


class LoadingWindow(QWidget):
    """Unity 风格沉浸式无边框启动窗口。"""

    def __init__(self, project_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_name = project_name

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(600, 420)

        # 屏幕居中
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

        # 加载标准资产目录中的 Splash 图片
        splash_path = _get_splash_path()
        self._pixmap = QPixmap(str(splash_path))
        if self._pixmap.isNull() and splash_path.exists():
            try:
                from PIL import Image
                import numpy as np

                pil_img = Image.open(str(splash_path))
                if pil_img.mode not in ("RGB", "RGBA"):
                    pil_img = pil_img.convert("RGB")
                arr = np.array(pil_img)
                h, w, ch = arr.shape
                qfmt = QImage.Format.Format_RGB888 if ch == 3 else QImage.Format.Format_RGBA8888
                qimg = QImage(arr.data, w, h, ch * w, qfmt)
                self._pixmap = QPixmap.fromImage(qimg.copy())
            except Exception:
                qimg = QImage(str(splash_path))
                if not qimg.isNull():
                    self._pixmap = QPixmap.fromImage(qimg)

        # 主布局
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. 顶部图片 Banner (KeepAspectRatio 居中裁切, 高 270px)
        self.banner_widget = SplashBannerWidget(self._pixmap, self)
        root.addWidget(self.banner_widget)

        # 2. 绿/青 Accent 装饰分割线 (高 3px)
        self.divider = QWidget(self)
        self.divider.setFixedHeight(3)
        self.divider.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22c55e, stop:0.4 #10b981, stop:1 #06b6d4);"
        )
        root.addWidget(self.divider)

        # 3. 底部黑色控制栏
        bottom = QWidget(self)
        bottom.setStyleSheet("background-color: #080808;")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(28, 18, 28, 18)

        # ── 左侧: Logo + 版权
        left_box = QVBoxLayout()
        left_box.setSpacing(4)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)
        logo_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        logo_icon = QLabel("◆", bottom)
        logo_icon.setStyleSheet("font-size: 24px; color: #FFFFFF; font-weight: bold; background: transparent;")
        logo_row.addWidget(logo_icon)

        brand_title = QLabel("DetectSphere®", bottom)
        brand_title.setStyleSheet(
            "font-size: 24px; font-weight: 800; color: #FFFFFF; font-family: 'Segoe UI', 'Inter', sans-serif; background: transparent;"
        )
        logo_row.addWidget(brand_title)

        left_box.addLayout(logo_row)
        left_box.addStretch(1)

        copyright_lbl = QLabel("© 2026 DetectSphere Technologies", bottom)
        copyright_lbl.setStyleSheet("font-size: 11px; color: #666666; background: transparent;")
        left_box.addWidget(copyright_lbl)

        bottom_layout.addLayout(left_box, 1)

        # ── 右侧: 状态 + 项目名 + 版本号
        right_box = QVBoxLayout()
        right_box.setSpacing(4)
        right_box.setAlignment(Qt.AlignmentFlag.AlignRight)

        self.status_label = QLabel("Open Project: Initializing core components...", bottom)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.status_label.setStyleSheet("font-size: 12px; color: #888888; background: transparent;")
        right_box.addWidget(self.status_label)

        self.project_label = QLabel(project_name, bottom)
        self.project_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.project_label.setStyleSheet(
            "font-size: 20px; font-weight: 800; color: #FFFFFF; letter-spacing: 1px; background: transparent;"
        )
        right_box.addWidget(self.project_label)

        right_box.addStretch(1)

        ver_text = f"2026.1 ({__version__})" if not __version__.startswith("202") else __version__
        self.version_label = QLabel(ver_text, bottom)
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.version_label.setStyleSheet("font-size: 18px; font-weight: 800; color: #FFFFFF; background: transparent;")
        right_box.addWidget(self.version_label)

        bottom_layout.addLayout(right_box, 1)

        root.addWidget(bottom, 1)

    def set_status(self, text: str) -> None:
        """更新状态文字并刷新。"""
        self.status_label.setText(f"Open Project: {text}")
        QApplication.processEvents()

    def paintEvent(self, event) -> None:
        """绘制整体圆角裁切和框线。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 14, 14)
        painter.setClipPath(path)
        painter.fillPath(path, QColor("#080808"))

        painter.setPen(QColor("#262626"))
        painter.drawPath(path)

        super().paintEvent(event)

