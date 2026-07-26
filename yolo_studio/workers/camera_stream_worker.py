"""CameraStreamWorker(QThread) — 高帧率实时摄像头采集线程 (支持设置分辨率)。

设计:
- 在后台线程中使用 cv2.VideoCapture 拉帧
- 支持配置分辨率 (如 1920x1080, 1280x720, 640x480)
- BGR -> RGB -> QImage 转换
- 发出 frame_ready(QImage) 与 fps_updated(float)
- 主线程 stop() 时优雅退出
"""
from __future__ import annotations

import time
from typing import Optional
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class CameraStreamWorker(QThread):
    """纯摄像头视频流拉取线程。"""

    frame_ready = Signal(QImage)
    fps_updated = Signal(float)
    failed = Signal(str)

    def __init__(
        self,
        camera_id: int = 0,
        width: int = 1280,
        height: int = 720,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._camera_id = int(camera_id)
        self._width = int(width)
        self._height = int(height)
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        import cv2

        cap: Optional[cv2.VideoCapture] = None
        try:
            cap = cv2.VideoCapture(self._camera_id)
            if not cap.isOpened():
                self.failed.emit(f"无法打开摄像头设备 {self._camera_id}")
                return

            if self._width > 0 and self._height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)

            self._running = True
            last_fps_t = time.perf_counter()
            frames = 0

            while self._running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.03)
                    continue

                # BGR → RGB → QImage
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                qimg = qimg.copy()  # 脱离 numpy buffer

                self.frame_ready.emit(qimg)

                frames += 1
                now = time.perf_counter()
                if now - last_fps_t >= 1.0:
                    fps = frames / (now - last_fps_t)
                    self.fps_updated.emit(fps)
                    last_fps_t = now
                    frames = 0

                time.sleep(0.01)  # 平滑控速，避免 CPU 100% 飙高
        except Exception as e:
            import traceback

            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        finally:
            if cap is not None:
                cap.release()
