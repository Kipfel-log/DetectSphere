"""mobile_server.py — 嵌入式手机无线采集 HTTP 服务器。

提供局域网网页服务，允许手机在同局域网内扫码/访问网页，输入 6 位验证码后调用手机摄像头拍照并上传到电脑端。
绑定 0.0.0.0 全网卡监听，确保 100% 局域网连通性。
"""
from __future__ import annotations

import base64
import io
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface


def get_all_lan_ips() -> list[str]:
    """返回本机所有真实局域网 IPv4 地址 (排除虚拟网卡与回环网卡)。"""
    ips: list[str] = []

    # 1. 使用 UDP 外连分析系统主路由物理网卡 IP (最准确)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("114.114.114.114", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            ips.append(primary_ip)
    except Exception:
        pass

    # 2. 使用 QNetworkInterface 遍历更多真实网卡 (如 Wi-Fi / 热点)
    try:
        for interface in QNetworkInterface.allInterfaces():
            flags = interface.flags()
            if (flags & QNetworkInterface.InterfaceFlag.IsUp) and not (flags & QNetworkInterface.InterfaceFlag.IsLoopBack):
                name = interface.humanReadableName().lower()
                # 过滤常见虚拟网卡
                if any(v in name for v in ["vmware", "virtualbox", "vbox", "hyper-v", "wsl", "vethernet"]):
                    continue
                for entry in interface.addressEntries():
                    ip = entry.ip()
                    if ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                        ip_str = ip.toString()
                        if not ip_str.startswith("127.") and not ip_str.startswith("169.254."):
                            if ip_str not in ips:
                                ips.append(ip_str)
    except Exception:
        pass

    if not ips:
        ips.append("127.0.0.1")
    return ips


MOBILE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>DetectSphere 移动端拍摄</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif; }
        body { background: #f3f4f6; color: #1f2937; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; }
        @media (prefers-color-scheme: dark) {
            body { background: #202020; color: #ffffff; }
            .card { background: #2d2d2d !important; border-color: rgba(255, 255, 255, 0.1) !important; }
            .pin-inputs input { background: #383838 !important; border-color: rgba(255, 255, 255, 0.2) !important; color: #60cdff !important; }
            .pin-inputs input:focus { border-color: #60cdff !important; }
            .camera-card { background: rgba(255, 255, 255, 0.05) !important; border-color: rgba(255, 255, 255, 0.15) !important; }
            .camera-card-title { color: #ffffff !important; }
            .camera-card-sub { color: #9ca3af !important; }
        }
        .card { background: #ffffff; border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 12px; padding: 28px 24px; width: 100%; max-width: 400px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1); text-align: center; }
        h1 { font-size: 20px; font-weight: 600; margin-bottom: 6px; color: #005fb8; }
        p { font-size: 13px; color: #6b7280; margin-bottom: 24px; }
        .pin-inputs { display: flex; gap: 8px; justify-content: center; margin-bottom: 20px; }
        .pin-inputs input { width: 44px; height: 56px; font-size: 24px; font-weight: 700; text-align: center; background: #f9fafb; border: 1px solid #d1d5db; border-bottom: 2px solid #005fb8; border-radius: 6px; color: #005fb8; outline: none; transition: all 0.15s ease; }
        .pin-inputs input:focus { border-color: #005fb8; box-shadow: 0 0 0 2px rgba(0, 95, 184, 0.2); }
        .hidden { display: none !important; }
        #status-msg { margin-top: 14px; font-size: 13px; color: #d97706; min-height: 20px; font-weight: 600; }
        
        /* 大按钮拍照卡片 */
        .camera-card { background: #f8fafc; border: 2px dashed #005fb8; border-radius: 14px; padding: 36px 16px; margin-top: 10px; cursor: pointer; transition: all 0.2s ease; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; }
        .camera-card:active { transform: scale(0.97); background: #e0f2fe; }
        .camera-icon-bg { width: 72px; height: 72px; background: rgba(0, 95, 184, 0.1); border-radius: 50%; display: flex; align-items: center; justify-content: center; }
        .camera-card-title { font-size: 17px; font-weight: 700; color: #005fb8; }
        .camera-card-sub { font-size: 12px; color: #64748b; }
    </style>
</head>
<body>
    <!-- 步骤 1: 验证码输入屏 (第6位自动提交通过) -->
    <div id="step-pin" class="card">
        <h1>DetectSphere 移动端配对</h1>
        <p>请输入电脑屏幕上显示的 6 位数验证码</p>
        <div class="pin-inputs" id="pin-container">
            <input type="tel" maxlength="1" autofocus />
            <input type="tel" maxlength="1" />
            <input type="tel" maxlength="1" />
            <input type="tel" maxlength="1" />
            <input type="tel" maxlength="1" />
            <input type="tel" maxlength="1" />
        </div>
        <div id="status-msg"></div>
    </div>

    <!-- 步骤 2: 手机拍照屏 (无黑框，点击大卡片即刻调取原生相机) -->
    <div id="step-camera" class="card hidden">
        <h1>移动端无线拍摄</h1>
        <p>点击下方卡片直接拍照并实时上传</p>

        <div class="camera-card" onclick="triggerCamera()">
            <div class="camera-icon-bg">
                <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#005fb8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
                    <circle cx="12" cy="13" r="3"/>
                </svg>
            </div>
            <div class="camera-card-title">点击拍摄 / 选取照片</div>
            <div class="camera-card-sub">自动调取系统相机与相册</div>
        </div>

        <input type="file" id="native-camera-input" accept="image/*" capture="environment" style="display:none;" onchange="handleFile(this)" />

        <div id="cam-status" style="margin-top:16px; font-size:14px; color:#10b981; font-weight:600;"></div>
    </div>

    <script>
        let sessionToken = '';
        const inputs = document.querySelectorAll('#pin-container input');

        inputs.forEach((inp, idx) => {
            inp.addEventListener('input', (e) => {
                if (e.target.value) {
                    if (idx < inputs.length - 1) {
                        inputs[idx + 1].focus();
                    }
                    // 自动判断：第 6 位输入完毕后无需手动点击，立即自动提交
                    let pin = Array.from(inputs).map(i => i.value).join('');
                    if (pin.length === 6) {
                        submitPin();
                    }
                }
            });
            inp.addEventListener('keydown', (e) => {
                if (e.key === 'Backspace' && !e.target.value && idx > 0) {
                    inputs[idx - 1].focus();
                }
            });
        });

        async function submitPin() {
            let pin = Array.from(inputs).map(i => i.value).join('');
            if (pin.length !== 6) return;

            showMsg('正在验证连接...');
            try {
                let res = await fetch('/api/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pin: pin, ua: navigator.userAgent })
                });
                let data = await res.json();
                if (data.ok) {
                    sessionToken = data.token;
                    document.getElementById('step-pin').classList.add('hidden');
                    document.getElementById('step-camera').classList.remove('hidden');
                    startHeartbeat();
                } else {
                    showMsg(data.error || '验证码错误，请核对电脑屏幕上的 6 位数字');
                    inputs[inputs.length - 1].focus();
                }
            } catch (err) {
                showMsg('连接服务器失败，请确认手机与电脑在同一 Wi-Fi 下');
            }
        }

        function startHeartbeat() {
            setInterval(() => {
                if (!sessionToken) return;
                fetch('/api/heartbeat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: sessionToken })
                }).catch(() => {});
            }, 5000);
        }

        function showMsg(msg) {
            document.getElementById('status-msg').innerText = msg;
        }

        function triggerCamera() {
            document.getElementById('native-camera-input').click();
        }

        function handleFile(input) {
            if (!input.files || !input.files[0]) return;
            let file = input.files[0];
            let reader = new FileReader();
            reader.onload = function(e) {
                uploadDataUrl(e.target.result);
            };
            reader.readAsDataURL(file);
        }

        async function uploadDataUrl(dataUrl) {
            let status = document.getElementById('cam-status');
            status.innerText = '正在上传到电脑...';
            status.style.color = '#d97706';
            try {
                let res = await fetch('/api/upload', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: sessionToken, image: dataUrl })
                });
                let data = await res.json();
                if (data.ok) {
                    status.innerText = '✓ 上传成功！电脑端已接收';
                    status.style.color = '#10b981';
                    setTimeout(() => { status.innerText = ''; }, 3000);
                } else {
                    status.innerText = '上传失败: ' + data.error;
                    status.style.color = '#ef4444';
                }
            } catch (e) {
                status.innerText = '上传出错，请重试';
                status.style.color = '#ef4444';
            }
        }
    </script>
</body>
</html>
"""


class MobileHTTPHandler(BaseHTTPRequestHandler):
    """处理移动端 HTTP 请求。"""

    server: "ThreadedMobileHTTPServer"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(MOBILE_HTML.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(length)

        if self.path == "/api/verify":
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                pin = str(data.get("pin", "")).strip()
                if pin == self.server.manager.pin_code:
                    token = f"tok_{random.randint(100000, 999999)}"
                    ua = str(data.get("ua", "Mobile Device"))
                    
                    self.server.manager.add_device(token, ua)
                    self._send_json({"ok": True, "token": token})
                else:
                    self._send_json({"ok": False, "error": "验证码错误，请核对电脑屏幕上的 6 位数字"})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        elif self.path == "/api/heartbeat":
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                token = data.get("token", "")
                if token in self.server.manager.active_devices:
                    self.server.manager.update_heartbeat(token)
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "error": "无效的会话"})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})

        elif self.path == "/api/upload":
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                token = data.get("token", "")
                data_url = data.get("image", "")

                if token not in self.server.manager.active_devices:
                    self._send_json({"ok": False, "error": "无效的或已超时的会话，请重新连接"})
                    return
                
                self.server.manager.update_heartbeat(token)

                if not data_url or "," not in data_url:
                    self._send_json({"ok": False, "error": "无效的图片数据"})
                    return

                header, b64data = data_url.split(",", 1)
                img_bytes = base64.b64decode(b64data)

                # 使用 PIL 与 ImageOps.exif_transpose 自动校正手机竖屏/横屏 EXIF 旋转
                try:
                    from PIL import Image, ImageOps
                    import numpy as np

                    pil_img = Image.open(io.BytesIO(img_bytes))
                    pil_img = ImageOps.exif_transpose(pil_img)
                    if pil_img.mode not in ("RGB", "RGBA"):
                        pil_img = pil_img.convert("RGB")
                    arr = np.array(pil_img)
                    h, w, ch = arr.shape
                    qfmt = QImage.Format.Format_RGB888 if ch == 3 else QImage.Format.Format_RGBA8888
                    qimg = QImage(arr.data, w, h, ch * w, qfmt).copy()
                except Exception:
                    qimg = QImage()
                    qimg.loadFromData(img_bytes)

                if qimg.isNull():
                    self._send_json({"ok": False, "error": "解析图片失败"})
                    return

                ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
                device_name = self.server.manager.active_devices[token]["ua"]
                self.server.manager.photo_received.emit(qimg, f"{ts_str} ({device_name})")
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
        else:
            self.send_error(404)

    def _send_json(self, obj: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))


class ThreadedMobileHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, manager: MobileServerManager):
        super().__init__(server_address, RequestHandlerClass)
        self.manager = manager


class MobileServerManager(QObject):
    """MobileServerManager — 管理后台 HTTP 服务器与多设备状态。"""

    photo_received = Signal(QImage, str)
    client_connected = Signal(str, str)     # token, ua
    client_disconnected = Signal(str, str)  # token, ua

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pin_code = self.generate_pin()
        
        # active_devices: dict[token, dict["ua": str, "last_heartbeat": float]]
        self.active_devices: dict[str, dict] = {}
        self._lock = threading.Lock()

        self.port = 8989
        self.current_ip = "127.0.0.1"
        self._server: Optional[ThreadedMobileHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        
        self.is_running = False
        self._cleanup_thread: Optional[threading.Thread] = None
        
    def add_device(self, token: str, ua: str) -> None:
        with self._lock:
            self.active_devices[token] = {"ua": ua, "last_heartbeat": time.time()}
        # 抛出连接信号，带上简短设备名
        short_name = "iOS" if "iPhone" in ua or "iPad" in ua else ("Android" if "Android" in ua else "Mobile")
        self.client_connected.emit(token, f"{short_name} 客户端")

    def update_heartbeat(self, token: str) -> None:
        with self._lock:
            if token in self.active_devices:
                self.active_devices[token]["last_heartbeat"] = time.time()
                
    def _timeout_checker(self) -> None:
        while self.is_running:
            time.sleep(3)
            now = time.time()
            disconnected_tokens = []
            with self._lock:
                for token, info in list(self.active_devices.items()):
                    if now - info["last_heartbeat"] > 30:  # 30秒无心跳即掉线
                        disconnected_tokens.append((token, info["ua"]))
                        del self.active_devices[token]
            
            for token, ua in disconnected_tokens:
                short_name = "iOS" if "iPhone" in ua or "iPad" in ua else ("Android" if "Android" in ua else "Mobile")
                self.client_disconnected.emit(token, f"{short_name} 客户端")

    def generate_pin(self) -> str:
        """随机生成 6 位数字验证码。"""
        self.pin_code = f"{random.randint(100000, 999999)}"
        return self.pin_code

    def start_server(self) -> tuple[bool, str]:
        """启动后台 HTTP 服务。"""
        lan_ips = get_all_lan_ips()
        self.current_ip = lan_ips[0]

        if self.is_running:
            return True, f"http://{self.current_ip}:{self.port}"

        for attempt in range(5):
            try:
                self._server = ThreadedMobileHTTPServer(("0.0.0.0", self.port), MobileHTTPHandler, self)
                self.is_running = True
                
                self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._server_thread.start()
                
                self._cleanup_thread = threading.Thread(target=self._timeout_checker, daemon=True)
                self._cleanup_thread.start()
                
                return True, f"http://{self.current_ip}:{self.port}"
            except Exception:
                self.port += 1

        return False, "无法绑定局域网端口"

    def stop_server(self) -> None:
        """停止后台 HTTP 服务。"""
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self.is_running = False
