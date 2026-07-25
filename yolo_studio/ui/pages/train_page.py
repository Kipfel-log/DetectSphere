"""TrainPage — 训练控制台(重布局版本)。

布局:
  左(固定宽 ~400)        │ 右(主区域)
  ────────────          │ ────────────
  表单                    │ 训练进度(进度条 + best mAP ring)
   基础模型               │
   运行名称               │ matplotlib 2×2 子图(15×11 英寸,大)
   Epochs / Batch / imgsz│
   设备 / Patience       │ 每轮指标表格
   增强 ☑                │
  [开始] [停止]           │ ── 日志区 ──
  状态                    │ [verbosity 下拉] [折叠] [清空]
                         │ ┌─────────────────────────┐
  ── 系统监控 ──         │ │  日志(QPlainTextEdit)   │
  ⭕ CPU 5%              │ │  (可折叠)               │
  ⭕ RAM 59%             │ └─────────────────────────┘
  ⭕ GPU0 70%            │
  ⭕ GPU1 30%            │
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TitleLabel,
)

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.model_registry import ModelEntry, add_entry
from yolo_studio.core.project import Project
from yolo_studio.core.train import TrainConfig, parse_final_metrics_from_csv
from yolo_studio.ui.widgets.log_pane import LogPane
from yolo_studio.ui.widgets.system_monitor import SystemMonitor
from yolo_studio.ui.widgets.training_progress import TrainingProgressWidget
from yolo_studio.workers.training_worker import TrainingWorker

import torch


def _multi_gpu_subsets(n: int, max_size: int = 4) -> list[list[int]]:
    """所有 size 2..max_size 的子集。"""
    from itertools import combinations

    out: list[list[int]] = []
    for size in range(2, min(n, max_size) + 1):
        for combo in combinations(range(n), size):
            out.append(list(combo))
    return out


class _DeviceCombo(ComboBox):
    """device 下拉 — 自动 / CPU / 单卡 / 多卡。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.refresh_devices()

    def refresh_devices(self) -> None:
        self.clear()
        self.addItem("自动(优先 GPU)", userData="auto")
        self.addItem("CPU", userData="cpu")
        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            for i in range(n):
                name = torch.cuda.get_device_name(i)
                self.addItem(f"CUDA:{i} ({name})", userData=str(i))
            for subset in _multi_gpu_subsets(n):
                label = "CUDA:" + ",".join(str(i) for i in subset)
                self.addItem(f"{label} (多卡)", userData=label)

    def current_device(self) -> str:
        return self.currentData() or "auto"


class _VerbosityCombo(ComboBox):
    """日志详细级别下拉:精简 / 标准 / 详细。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.addItem("精简", userData="minimal")
        self.addItem("标准", userData="standard")
        self.addItem("详细(全部 INFO)", userData="verbose")
        self.setCurrentIndex(1)


class TrainPage(QWidget):
    """训练页。"""

    modelRegistered = Signal(str)

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db
        self._worker: Optional[TrainingWorker] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        # ===== 左栏:表单 + 系统监控 =====
        left = QWidget()
        left.setMinimumWidth(340)
        left.setMaximumWidth(500)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # 表单
        form_box = QFormLayout()

        # 基础模型
        model_row = QHBoxLayout()
        self.model_combo = ComboBox()
        self._refresh_base_models()
        self.model_combo.addItem("浏览其它 .pt …")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo, 1)
        form_box.addRow("基础模型:", model_row)

        self.run_name_edit = QLineEdit()
        self.run_name_edit.setPlaceholderText("留空自动 pen_<timestamp>")
        form_box.addRow("运行名称:", self.run_name_edit)

        self.epochs_spin = SpinBox()
        self.epochs_spin.setRange(1, 10000)
        self.epochs_spin.setValue(10)
        form_box.addRow("训练轮数:", self.epochs_spin)

        self.batch_spin = SpinBox()
        self.batch_spin.setRange(1, 256)
        self.batch_spin.setValue(4)
        form_box.addRow("批大小:", self.batch_spin)

        self.imgsz_spin = SpinBox()
        self.imgsz_spin.setRange(32, 2048)
        self.imgsz_spin.setSingleStep(32)
        self.imgsz_spin.setValue(640)
        form_box.addRow("图像尺寸:", self.imgsz_spin)

        self.device_combo = _DeviceCombo()
        form_box.addRow("设备:", self.device_combo)

        self.patience_spin = SpinBox()
        self.patience_spin.setRange(0, 1000)
        self.patience_spin.setValue(20)
        form_box.addRow("早停耐心值:", self.patience_spin)

        self.save_period_spin = SpinBox()
        self.save_period_spin.setRange(1, 100)
        self.save_period_spin.setValue(10)
        form_box.addRow("保存间隔(轮):", self.save_period_spin)

        self.augment_check = QCheckBox("启用数据增强")
        self.augment_check.setChecked(True)
        form_box.addRow("", self.augment_check)

        left_layout.addLayout(form_box)

        # 控制按钮
        ctrl_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.PLAY, "开始训练")
        self.start_btn.clicked.connect(self._on_start)
        ctrl_row.addWidget(self.start_btn)

        self.stop_btn = PushButton(FIF.CLOSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        ctrl_row.addWidget(self.stop_btn)

        left_layout.addLayout(ctrl_row)
        self.status_label = BodyLabel("就绪")
        left_layout.addWidget(self.status_label)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        left_layout.addWidget(sep)

        # 系统监控(用户要求在此处)
        self.system_monitor = SystemMonitor(refresh_ms=2000)
        # 给 system_monitor 一个最大高度,避免它把表单挤没了
        self.system_monitor.setMaximumHeight(280)
        left_layout.addWidget(self.system_monitor)
        left_layout.addStretch(1)

        splitter.addWidget(left)

        # ===== 右栏:进度 + 表格 + 日志 =====
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        self.progress = TrainingProgressWidget()
        right_layout.addWidget(self.progress, 4)

        # 日志工具栏:verbosity 下拉 + 折叠 + 清空
        log_toolbar = QHBoxLayout()
        log_toolbar.setSpacing(6)

        log_toolbar.addWidget(StrongBodyLabel("日志"))
        log_toolbar.addStretch(1)

        log_toolbar.addWidget(CaptionLabel("详细级别:"))
        self.verbosity = _VerbosityCombo()
        self.verbosity.currentIndexChanged.connect(self._on_verbosity_changed)
        log_toolbar.addWidget(self.verbosity)

        self.collapse_btn = PushButton("折叠")
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.toggled.connect(self._on_collapse_toggled)
        log_toolbar.addWidget(self.collapse_btn)

        self.clear_log_btn = PushButton("清空")
        self.clear_log_btn.clicked.connect(lambda: self.log_pane.clear())
        log_toolbar.addWidget(self.clear_log_btn)

        right_layout.addLayout(log_toolbar)

        self.log_pane = LogPane()
        self.log_pane.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        right_layout.addWidget(self.log_pane, 1)

        splitter.addWidget(right)

        # 比例 — 左列窄,右列主体
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 880])

        # 默认折叠日志区
        self._collapsed = False
        self._on_collapse_toggled(False)

    # ---- 基础模型下拉 ----
    def _refresh_base_models(self) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for size in ("yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt"):
            self.model_combo.addItem(f"预训练 {size}", userData=size)
        if self.project.models_dir.exists():
            for pt in sorted(self.project.models_dir.glob("*.pt")):
                self.model_combo.addItem(f"项目模型 {pt.name}", userData=str(pt))
        idx = self.model_combo.findData("yolov8n.pt")
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)

    def _on_model_changed(self, idx: int) -> None:
        text = self.model_combo.itemText(idx)
        if "浏览" in text:
            f, _ = QFileDialog.getOpenFileName(
                self,
                "选择基础 .pt 模型",
                str(self.project.models_dir),
                "PyTorch 模型 (*.pt)",
            )
            if f:
                self.model_combo.addItem(Path(f).name, userData=f)
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
            else:
                self._refresh_base_models()

    # ---- 日志详细级别 + 折叠 ----
    def _on_verbosity_changed(self, idx: int) -> None:
        level = self.verbosity.currentData() or "standard"
        self.log_pane.set_level(level)

    def _on_collapse_toggled(self, checked: bool) -> None:
        self._collapsed = checked
        self.log_pane.setVisible(not checked)
        self.collapse_btn.setText("展开" if checked else "折叠")

    # ---- 启动 / 停止 ----
    def _on_start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        base_model = self.model_combo.currentData() or "yolov8n.pt"
        if not Path(base_model).exists() and not base_model.startswith("yolov8"):
            self.status_label.setText("未选择有效的模型文件")
            return

        cfg = TrainConfig(
            project=self.project,
            base_model=base_model,
            epochs=self.epochs_spin.value(),
            batch=self.batch_spin.value(),
            imgsz=self.imgsz_spin.value(),
            device=self.device_combo.current_device(),
            patience=self.patience_spin.value(),
            save_period=self.save_period_spin.value(),
            run_name=self.run_name_edit.text().strip(),
            augment=self.augment_check.isChecked(),
        )

        self.progress.reset()
        self.progress.set_total_epochs(cfg.epochs)
        self.log_pane.clear()
        self.log_pane.append(f"[启动] device={cfg.effective_device()} run={cfg.effective_run_name()}")
        self.log_pane.append(f"[启动] data={cfg.effective_data_yaml()}")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("训练中…")

        self._worker = TrainingWorker(cfg)
        self._worker.metrics.connect(self.progress.update_metrics)
        self._worker.log.connect(self.log_pane.append)
        self._worker.finished_train.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._reset_buttons)
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker is None:
            return
        self._worker.request_stop()
        self.status_label.setText("正在停止…")
        self.stop_btn.setEnabled(False)

    @Slot()
    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    @Slot(dict)
    def _on_finished(self, result: dict) -> None:
        self.status_label.setText("训练完成 ✓")
        best = result.get("best_path")
        if best:
            self.log_pane.append(f"[完成] best.pt → {best}")
        registered = result.get("registered_path")
        if registered:
            self.log_pane.append(f"[完成] 已注册到: {registered}")
            csv = result.get("results_csv")
            metrics = parse_final_metrics_from_csv(csv) if csv else {}
            entry = ModelEntry(
                name=registered.name,
                path=str(registered),
                classes=[c.name for c in self.project.classes],
                metrics={
                    "mAP50": metrics.get("metrics/mAP50(B)", 0.0),
                    "mAP50-95": metrics.get("metrics/mAP50-95(B)", 0.0),
                    "precision": metrics.get("metrics/precision(B)", 0.0),
                    "recall": metrics.get("metrics/recall(B)", 0.0),
                },
                parent_run=str(result.get("save_dir", "")),
                source="training",
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            add_entry(self.project, entry)
            self.modelRegistered.emit(str(registered))
        elif best:
            self.log_pane.append("[完成] 未自动注册(请到模型注册页手动添加)")

        InfoBar.success(
            title="训练完成",
            content=f"best.pt: {best}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    @Slot(str)
    def _on_failed(self, err: str) -> None:
        self.status_label.setText(f"失败:{err}")
        self.log_pane.append(f"[ERROR] {err}")

        InfoBar.error(
            title="训练失败",
            content=str(err)[:200],
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def refresh_models(self) -> None:
        cur_data = self.model_combo.currentData()
        self._refresh_base_models()
        if cur_data:
            idx = self.model_combo.findData(cur_data)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
