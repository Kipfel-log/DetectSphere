"""MobileCapturePanel — 手机无线采集控制子面板 (Windows 11 Fluent 设计风格)。

功能:
- 支持 IP 下拉切换 (支持多网卡/Wi-Fi/热点切换)
- 结合 CardWidget 完美契合 QFluentWidgets 原生 Windows 11 视觉规范
- 5 分钟未连接自动刷新 6 位 PIN 验证码 (仅在 Tab 激活时倒计时)
- 展示手机连接状态 (等待连接 / 已连接)
- 中央使用 AspectVideoWidget 大图实时展示最新从手机接收到的照片
- 收到照片实时推送 photoReceived(QImage, timestamp_str) 信号入库
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, Slot, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QSpacerItem,
    QSizePolicy,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TransparentToolButton,
)

from yolo_studio.core.mobile_server import MobileServerManager, get_all_lan_ips
from yolo_studio.core.qr_utils import generate_qr_pixmap
from yolo_studio.ui.widgets.aspect_video_widget import AspectVideoWidget


class PinDisplayWidget(QWidget):
    """Windows 11 Fluent 风格大字 6 位验证码控件。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._digits = "------"

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 4, 0, 4)
        self.layout.setSpacing(8)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._labels: list[QLabel] = []
        for _ in range(6):
            lbl = QLabel("-")
            lbl.setFixedSize(42, 50)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "QLabel { background-color: rgba(128, 128, 128, 0.08); border: 1px solid rgba(128, 128, 128, 0.2); border-bottom: 2px solid #005fb8; border-radius: 6px; font-size: 24px; font-weight: 700; color: #005fb8; }"
            )
            self.layout.addWidget(lbl)
            self._labels.append(lbl)

    def set_pin(self, pin: str) -> None:
        pin = str(pin).zfill(6)[:6]
        self._digits = pin
        for idx, char in enumerate(pin):
            if idx < len(self._labels):
                self._labels[idx].setText(char)


class MobileCapturePanel(QWidget):
    """手机无线采集子面板 (Windows 11 Fluent 风格)。"""

    photoReceived = Signal(QImage, str)
    stateChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.server_mgr = MobileServerManager(self)
        self.server_mgr.photo_received.connect(self._on_photo_received)
        self.server_mgr.client_connected.connect(self._on_client_connected)
        self.server_mgr.client_disconnected.connect(self._on_client_disconnected)

        self._pin_remaining_sec = 300
        self._pin_timer = QTimer(self)
        self._pin_timer.setInterval(1000)
        self._pin_timer.timeout.connect(self._on_pin_timer_tick)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)

        # 添加左侧弹性空间（用于未配对时居中）
        self.left_spacer = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.main_layout.addSpacerItem(self.left_spacer)

        # ── 左侧: 局域网信息与验证码配对 CardWidget ──
        self.left_widget = QWidget(self)
        self.left_widget.setMaximumWidth(320)
        left_box = QVBoxLayout(self.left_widget)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(12)

        left_box.addWidget(StrongBodyLabel("手机局域网无线采集"))

        # 网卡 IP 选择与 URL 展示 CardWidget
        info_card = CardWidget(self)
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(8)

        card_layout.addWidget(CaptionLabel("1. 选择网卡与访问链接:"))

        self.ip_combo = ComboBox(self)
        all_ips = get_all_lan_ips()
        lan_ips = [ip for ip in all_ips if not ip.startswith("127.")]
        if not lan_ips:
            lan_ips = all_ips
        for ip in lan_ips:
            self.ip_combo.addItem(f"局域网 IP: {ip}", userData=ip)
        self.ip_combo.currentIndexChanged.connect(self._on_ip_changed)
        card_layout.addWidget(self.ip_combo)

        self.url_label = BodyLabel("http://127.0.0.1:8989")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setStyleSheet("color: #005fb8; font-weight: bold; font-size: 15px;")
        card_layout.addWidget(self.url_label)

        # 二维码 QLabel
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(170, 170)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet("background-color: #FFFFFF; border-radius: 8px; padding: 4px; border: 1px solid rgba(0,0,0,0.1);")
        card_layout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignCenter)

        left_box.addWidget(info_card)

        # 验证码 CardWidget
        pin_card = CardWidget(self)
        pin_layout = QVBoxLayout(pin_card)
        pin_layout.setContentsMargins(14, 14, 14, 14)
        pin_layout.setSpacing(8)

        pin_layout.addWidget(CaptionLabel("2. 手机网页输入配对验证码:"))
        self.pin_widget = PinDisplayWidget(self)
        pin_layout.addWidget(self.pin_widget)

        refresh_row = QHBoxLayout()
        self.timer_label = CaptionLabel("未连接 (倒计时: 05:00)")
        self.timer_label.setStyleSheet("color: #888888;")
        refresh_row.addWidget(self.timer_label)

        self.refresh_pin_btn = TransparentToolButton(FIF.SYNC, self)
        self.refresh_pin_btn.setToolTip("手动刷新验证码")
        self.refresh_pin_btn.clicked.connect(self.refresh_pin)
        refresh_row.addWidget(self.refresh_pin_btn)
        pin_layout.addLayout(refresh_row)

        left_box.addWidget(pin_card)

        # 状态指示
        self.status_label = StrongBodyLabel("等待手机连接...")
        left_box.addWidget(self.status_label)

        # 已连接设备列表
        self.device_list = QListWidget(self)
        self.device_list.setMaximumHeight(120)
        self.device_list.setStyleSheet(
            "QListWidget { background-color: transparent; border: none; outline: none; }"
            "QListWidget::item { padding: 4px; border-bottom: 1px solid rgba(128, 128, 128, 0.15); }"
        )
        left_box.addWidget(self.device_list)

        left_box.addStretch(1)
        self.main_layout.addWidget(self.left_widget)

        # 添加右侧弹性空间（用于未配对时居中）
        self.right_spacer = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.main_layout.addSpacerItem(self.right_spacer)

        # ── 中央: 放大展示最新收到照片 ──
        self.center_widget = QWidget(self)
        self.center_widget.setMaximumWidth(750)
        center_box = QVBoxLayout(self.center_widget)
        center_box.setContentsMargins(0, 0, 0, 0)
        center_box.setSpacing(6)

        center_header = QHBoxLayout()
        center_header.addWidget(StrongBodyLabel("手机最新照片实时预览"))
        center_header.addStretch(1)
        self.received_time_label = CaptionLabel("尚未收到照片")
        self.received_time_label.setStyleSheet("color: #888888;")
        center_header.addWidget(self.received_time_label)

        center_box.addLayout(center_header)

        self.photo_widget = AspectVideoWidget(self)
        self.photo_widget.setText("手机配对连接后点击「拍摄并上传」\n最新收到的照片大图将实时在此展示")
        center_box.addWidget(self.photo_widget, 1)

        self.main_layout.addWidget(self.center_widget, 1)

        # 初始刷新布局状态 (隐藏中央区域，居中左侧)
        self._update_layout_state()

        # 初始化服务器
        self._init_server()

    def _init_server(self) -> None:
        ok, base_url = self.server_mgr.start_server()
        if ok:
            selected_ip = str(self.ip_combo.currentData() or self.server_mgr.current_ip)
            full_url = f"http://{selected_ip}:{self.server_mgr.port}"
            self.url_label.setText(full_url)
            self._update_qr_code(full_url)
            self.pin_widget.set_pin(self.server_mgr.pin_code)
        else:
            self.url_label.setText("服务器启动失败")

    def _on_ip_changed(self, idx: int) -> None:
        ip = str(self.ip_combo.currentData() or "127.0.0.1")
        full_url = f"http://{ip}:{self.server_mgr.port}"
        self.url_label.setText(full_url)
        self._update_qr_code(full_url)

    def _update_qr_code(self, url: str) -> None:
        pm = generate_qr_pixmap(url, size=170)
        self.qr_label.setPixmap(pm)

    def refresh_pin(self) -> None:
        new_pin = self.server_mgr.generate_pin()
        self.pin_widget.set_pin(new_pin)
        self._pin_remaining_sec = 300
        self._update_timer_label()
        InfoBar.info(
            title="验证码已刷新",
            content=f"新验证码: {new_pin}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )

    def activate(self) -> None:
        """当选项卡切换至手机模式时激活。"""
        if len(self.server_mgr.active_devices) == 0:
            self._pin_timer.start()
        self._update_layout_state()

    def deactivate(self) -> None:
        """当离开手机模式选项卡时暂停倒计时。"""
        self._pin_timer.stop()

    def _on_pin_timer_tick(self) -> None:
        if len(self.server_mgr.active_devices) > 0:
            return

        self._pin_remaining_sec -= 1
        self._update_timer_label()

        if self._pin_remaining_sec <= 0:
            self.refresh_pin()

    def _update_timer_label(self) -> None:
        mm = self._pin_remaining_sec // 60
        ss = self._pin_remaining_sec % 60
        self.timer_label.setText(f"未连接 (倒计时: {mm:02d}:{ss:02d})")

    def _update_layout_state(self) -> None:
        """根据连接设备数量动态切换布局（无设备时居中，有设备时展开）。"""
        has_devices = len(self.server_mgr.active_devices) > 0
        has_staged = getattr(self, "has_staged_photos", False)
        should_expand = has_devices or has_staged
        
        if should_expand:
            self.center_widget.show()
            self.left_spacer.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            self.right_spacer.changeSize(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
            if has_devices:
                self.status_label.setText("手机端已连接，可继续接入更多设备")
                self.timer_label.setText("设备在线中")
                self._pin_timer.stop()
            else:
                self.status_label.setText("所有设备已断连，等待手机重新连接...")
                self.timer_label.setText("未连接 (倒计时: 05:00)")
                self._pin_timer.start()
        else:
            self.center_widget.hide()
            self.left_spacer.changeSize(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
            self.right_spacer.changeSize(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
            self.status_label.setText("等待手机连接...")
            self.timer_label.setText("未连接 (倒计时: 05:00)")
            self._pin_timer.start()
            
        self.main_layout.invalidate()
        self.stateChanged.emit(should_expand)

    @Slot(str, str)
    def _on_client_connected(self, token: str, ua: str) -> None:
        self.device_list.addItem(f"{ua} ({token[-4:]})")
        self._update_layout_state()
        self.refresh_pin()
        
        InfoBar.success(
            title="新设备已接入",
            content=f"{ua} 已成功连接",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    @Slot(str, str)
    def _on_client_disconnected(self, token: str, ua: str) -> None:
        # 从列表中移除
        target = f"{ua} ({token[-4:]})"
        for i in range(self.device_list.count()):
            if self.device_list.item(i).text() == target:
                self.device_list.takeItem(i)
                break
                
        self._update_layout_state()
        
        InfoBar.warning(
            title="设备已断开",
            content=f"{ua} 心跳超时已断开连接",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    @Slot(QImage, str)
    def _on_photo_received(self, qimg: QImage, ts_str: str) -> None:
        self.photo_widget.setPixmap(QPixmap.fromImage(qimg))
        self.received_time_label.setText(f"接收时间: {ts_str}")
        self.photoReceived.emit(qimg, ts_str)
