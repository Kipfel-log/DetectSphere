"""系统监控 widget — CPU / RAM / 每张 GPU,全部用 ProgressRing。

QPixmap 必须在 QApplication 构造后才能实例化,所以图例图标也用懒加载。

每张 GPU 显示一行:[Ring(util%)] + [Ring(VRAM%)] + 文本(GPU 名/数字)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ProgressRing,
    StrongBodyLabel,
)

import psutil

try:
    import pynvml  # type: ignore

    _HAVE_PYNVML = True
except Exception:
    pynvml = None  # type: ignore
    _HAVE_PYNVML = False


def _read_gpu_via_nvidia_smi() -> list[dict]:
    """nvidia-smi 兜底(pynvml 在 Windows 上常因找不到 nvml.dll 失败)。

    返回 [{index, name, util(%), mem_used(MiB), mem_total(MiB)}]
    """
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return []
        out: list[dict] = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                out.append(
                    {
                        "index": int(parts[0]),
                        "name": parts[1],
                        "util": int(parts[2]),
                        "mem_used": int(parts[3]),
                        "mem_total": int(parts[4]),
                    }
                )
            except ValueError:
                continue
        return out
    except Exception:
        return []


# 一些平台常量(Python 没直接接口给 CPU 频率)
def _cpu_static_info() -> tuple[int, float]:
    try:
        cores = psutil.cpu_count(logical=False) or 1
    except Exception:
        cores = 1
    try:
        freq = psutil.cpu_freq().current
    except Exception:
        freq = 0.0
    return cores, freq


class _MetricRow(QWidget):
    """单指标行:[Ring] + 数值文本 + 标题。"""

    def __init__(
        self,
        title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        # 标题(左对齐,固定宽度)
        self.title_label = StrongBodyLabel(title)
        self.title_label.setMinimumWidth(72)
        layout.addWidget(self.title_label)

        # Ring(48x48 比 64x64 更紧凑,适合堆叠)
        self.ring = ProgressRing()
        self.ring.setFixedSize(56, 56)
        layout.addWidget(self.ring)

        # 数值文本(下方)
        self.value_label = CaptionLabel("—")
        layout.addWidget(self.value_label, 1)

    def update(self, percent: Optional[int], text: str = "—") -> None:
        if percent is None:
            self.ring.setValue(0)
            self.value_label.setText(f"<span style='color:#999'>{text}</span>")
            return
        self.ring.setValue(int(percent))
        self.value_label.setText(text)


class _GpuRow(QWidget):
    """一张 GPU 一行:[util Ring] + [VRAM Ring] + 名称 + 详细数字。"""

    def __init__(self, name: str = "GPU", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name = name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        # 名称
        self.name_label = StrongBodyLabel(name)
        self.name_label.setMinimumWidth(80)
        layout.addWidget(self.name_label)

        # util ring
        util_block = QVBoxLayout()
        util_block.setSpacing(0)
        self.util_ring = ProgressRing()
        self.util_ring.setFixedSize(56, 56)
        util_block.addWidget(self.util_ring, alignment=Qt.AlignmentFlag.AlignCenter)
        self.util_label = CaptionLabel("util —%")
        self.util_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        util_block.addWidget(self.util_label)
        layout.addLayout(util_block)

        # VRAM ring
        vram_block = QVBoxLayout()
        vram_block.setSpacing(0)
        self.vram_ring = ProgressRing()
        self.vram_ring.setFixedSize(56, 56)
        vram_block.addWidget(self.vram_ring, alignment=Qt.AlignmentFlag.AlignCenter)
        self.vram_label = CaptionLabel("VRAM —%")
        self.vram_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vram_block.addWidget(self.vram_label)
        layout.addLayout(vram_block)

        layout.addStretch(1)

    def update(
        self,
        name: str,
        util_pct: Optional[int],
        vram_used_mb: Optional[int],
        vram_total_mb: Optional[int],
    ) -> None:
        self.name_label.setText(name)

        if util_pct is None:
            self.util_ring.setValue(0)
            self.util_label.setText("util N/A")
        else:
            self.util_ring.setValue(int(util_pct))
            self.util_label.setText(f"util {util_pct}%")

        if vram_used_mb is None or vram_total_mb is None or vram_total_mb == 0:
            self.vram_ring.setValue(0)
            self.vram_label.setText("VRAM N/A")
        else:
            pct = max(0, min(100, int(vram_used_mb * 100 / vram_total_mb)))
            self.vram_ring.setValue(pct)
            self.vram_label.setText(f"VRAM {pct}%")


class SystemMonitor(QWidget):
    """系统监控(CPU + RAM + N 块 GPU,全部用 ProgressRing)。"""

    def __init__(self, refresh_ms: int = 2000, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_pynvml()

        # 一次性记住 CPU 核数 / 频率
        self._cpu_cores, self._cpu_freq = _cpu_static_info()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(StrongBodyLabel("系统监控"))

        # CPU 行
        self.cpu_row = _MetricRow("CPU")
        layout.addWidget(self.cpu_row)

        # RAM 行
        self.ram_row = _MetricRow("RAM")
        layout.addWidget(self.ram_row)

        # GPU 行容器(动态展开多张卡)
        self._gpu_container = QVBoxLayout()
        self._gpu_container.setSpacing(2)
        self._gpu_rows: list[_GpuRow] = []
        layout.addLayout(self._gpu_container)

        layout.addStretch(1)

        # 定时器
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_ms)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.refresh()

    # ---- pynvml ----
    def _init_pynvml(self) -> None:
        self._nvml_ok = False
        if not _HAVE_PYNVML:
            return
        try:
            pynvml.nvmlInit()
            self._nvml_ok = True
        except Exception:
            self._nvml_ok = False

    @property
    def gpu_count(self) -> int:
        if self._nvml_ok:
            try:
                return int(pynvml.nvmlDeviceGetCount())
            except Exception:
                pass
        # 兜底:nvidia-smi
        return len(_read_gpu_via_nvidia_smi())

    # ---- 公开 API ----
    def set_running(self, running: bool) -> None:
        if running:
            self._timer.start()
        else:
            self._timer.stop()

    def refresh(self) -> None:
        # CPU
        try:
            cpu_pct = int(psutil.cpu_percent(interval=None))
        except Exception:
            cpu_pct = None
        cpu_text = (
            f"{cpu_pct}% · {self._cpu_cores} cores · {self._cpu_freq:.0f} MHz"
            if cpu_pct is not None
            else "N/A"
        )
        self.cpu_row.update(cpu_pct, cpu_text)

        # RAM
        try:
            vm = psutil.virtual_memory()
            ram_pct = int(vm.percent)
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            ram_text = f"{used_gb:.1f} / {total_gb:.1f} GB ({ram_pct}%)"
            self.ram_row.update(ram_pct, ram_text)
        except Exception:
            self.ram_row.update(None, "N/A")

        # GPU
        self._refresh_gpus()

    def _refresh_gpus(self) -> None:
        # 数据源:优先 pynvml,失败则 nvidia-smi
        gpu_data: list[dict] = []
        if self._nvml_ok:
            try:
                for i in range(self.gpu_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name_raw = pynvml.nvmlDeviceGetName(handle)
                    name = (
                        name_raw.decode("utf-8", errors="ignore")
                        if isinstance(name_raw, bytes)
                        else str(name_raw)
                    )
                    util = int(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    gpu_data.append(
                        {
                            "index": i,
                            "name": name,
                            "util": util,
                            "mem_used": int(mem.used / (1024 * 1024)),
                            "mem_total": int(mem.total / (1024 * 1024)),
                        }
                    )
            except Exception:
                gpu_data = []
        if not gpu_data:
            # 兜底
            gpu_data = _read_gpu_via_nvidia_smi()

        n = len(gpu_data)
        if n != len(self._gpu_rows):
            while self._gpu_container.count():
                item = self._gpu_container.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            self._gpu_rows = []
            for i in range(n):
                row = _GpuRow(f"GPU {i}")
                self._gpu_container.addWidget(row)
                self._gpu_rows.append(row)
            if n == 0:
                lbl = CaptionLabel("(未检测到 GPU,或 nvidia-smi 也不在 PATH)")
                self._gpu_container.addWidget(lbl)
                self._gpu_rows.append(lbl)  # type: ignore[arg-type]

        if n == 0:
            return

        for entry, row in zip(gpu_data, self._gpu_rows):
            if not isinstance(row, _GpuRow):
                continue
            row.update(
                entry.get("name", "GPU"),
                entry.get("util"),
                entry.get("mem_used"),
                entry.get("mem_total"),
            )

    def closeEvent(self, event) -> None:
        try:
            if self._nvml_ok:
                pynvml.nvmlShutdown()
        except Exception:
            pass
        super().closeEvent(event)
