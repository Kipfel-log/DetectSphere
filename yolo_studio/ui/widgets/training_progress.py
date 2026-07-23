"""TrainingProgressWidget — 实时 loss/mAP 曲线 + 指标表。

4 个 subplot (2x2):
  左上:train/val box_loss
  右上:mAP50 + mAP50-95
  左下:precision + recall
  右下:train/val cls_loss

下方:QTableWidget 显示每 epoch 的关键指标。

matplotlib Figure 与 FigureCanvasQTAgg 必须在 QApplication 构造之后才能
实例化(否则 'figure with no Axes' 警告)。我们在 __init__ 里建,QApplication
早已存在(由 MainWindow 创建过程触发)。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ProgressBar,
    ProgressRing,
    StrongBodyLabel,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure


# 全局 matplotlib 字体样式(模块级 — 首次导入时设定一次)
import matplotlib as mpl

mpl.rcParams["font.size"] = 10
mpl.rcParams["axes.titlesize"] = 12
mpl.rcParams["axes.labelsize"] = 10
mpl.rcParams["legend.fontsize"] = 9
mpl.rcParams["xtick.labelsize"] = 9
mpl.rcParams["ytick.labelsize"] = 9


METRIC_COLS = [
    "epoch",
    "train/box_loss",
    "val/box_loss",
    "train/cls_loss",
    "val/cls_loss",
    "metrics/precision(B)",
    "metrics/recall(B)",
    "metrics/mAP50(B)",
    "metrics/mAP50-95(B)",
]


class TrainingProgressWidget(QWidget):
    """训练进度显示组件。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # 训练指标状态
        self._total_epochs = 1
        self._best_mAP50 = 0.0
        self._best_mAP5095 = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 顶部标题
        layout.addWidget(StrongBodyLabel("训练进度"))

        # 顶部状态条:进度条 + best mAP ring(用户不要 epoch ring)
        status_row = QHBoxLayout()
        status_row.setSpacing(16)

        # 进度条
        prog_block = QVBoxLayout()
        prog_block.setSpacing(2)
        prog_block.addWidget(CaptionLabel("Epoch 进度"))
        self.epoch_progress = ProgressBar()
        self.epoch_progress.setMinimum(0)
        self.epoch_progress.setMaximum(100)
        self.epoch_progress.setValue(0)
        prog_block.addWidget(self.epoch_progress)
        status_row.addLayout(prog_block, 2)

        # best mAP50 ring
        m50_block = QVBoxLayout()
        m50_block.setSpacing(2)
        self.m50_ring = ProgressRing()
        self.m50_ring.setFixedSize(72, 72)
        m50_block.addWidget(self.m50_ring, alignment=Qt.AlignmentFlag.AlignCenter)
        self.m50_label = CaptionLabel("Best mAP50: —")
        self.m50_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m50_block.addWidget(self.m50_label)
        status_row.addLayout(m50_block)

        layout.addLayout(status_row)

        # matplotlib Figure + Canvas — 加大尺寸便于查看
        self.fig = Figure(figsize=(15, 11), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)

        # 4 个子图
        self.ax_box = self.fig.add_subplot(2, 2, 1)
        self.ax_map = self.fig.add_subplot(2, 2, 2)
        self.ax_pr = self.fig.add_subplot(2, 2, 3)
        self.ax_cls = self.fig.add_subplot(2, 2, 4)

        for ax, title in [
            (self.ax_box, "Box Loss"),
            (self.ax_map, "mAP"),
            (self.ax_pr, "Precision / Recall"),
            (self.ax_cls, "Cls Loss"),
        ]:
            ax.set_title(title)
            ax.grid(True, alpha=0.3)

        self._init_lines()

        layout.addWidget(self.canvas, 1)

        # 指标表
        layout.addWidget(StrongBodyLabel("每轮指标"))
        self.table = QTableWidget(0, len(METRIC_COLS))
        self.table.setHorizontalHeaderLabels(METRIC_COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)

        # 状态行
        self.status_label = BodyLabel("尚未开始")
        layout.addWidget(self.status_label)

    # ---- 公开 API ----
    def set_total_epochs(self, n: int) -> None:
        """TrainPage 在启动训练前设置,用于进度计算。"""
        self._total_epochs = max(1, int(n))
        self.epoch_progress.setMaximum(self._total_epochs)

    def reset(self) -> None:
        """新一轮训练开始前清空曲线和表。"""
        self._init_lines()
        self.table.setRowCount(0)
        self.status_label.setText("训练中…")
        self.epoch_progress.setValue(0)
        self._best_mAP50 = 0.0
        self._best_mAP5095 = 0.0
        self.m50_ring.setValue(0)
        self.m50_label.setText("Best mAP50: —")

    @Slot(dict)
    def update_metrics(self, payload: dict) -> None:
        """接收 {'epoch': int, 'metrics': {key: value, ...}}。"""
        epoch = payload.get("epoch", -1)
        m = payload.get("metrics", {})

        # 提取(若 key 不存在则 None)
        train_box = m.get("train/box_loss")
        val_box = m.get("val/box_loss")
        train_cls = m.get("train/cls_loss")
        val_cls = m.get("val/cls_loss")
        prec = m.get("metrics/precision(B)")
        rec = m.get("metrics/recall(B)")
        m50 = m.get("metrics/mAP50(B)")
        m5095 = m.get("metrics/mAP50-95(B)")

        # 进度条(Ultralytics 的 epoch 从 0 计,所以 +1 给用户看)
        if epoch >= 0:
            display_epoch = epoch + 1
            self.epoch_progress.setValue(min(display_epoch, self._total_epochs))

        # Best mAP 跟踪
        if m50 is not None and m50 > self._best_mAP50:
            self._best_mAP50 = m50
            self.m50_ring.setValue(int(m50 * 100))
            self.m50_label.setText(f"Best mAP50: {m50:.3f}")
        if m5095 is not None and m5095 > self._best_mAP5095:
            self._best_mAP5095 = m5095

        # 追加数据 + 重绘
        if train_box is not None:
            self.train_box_x.append(epoch)
            self.train_box_y.append(train_box)
            self._line_train_box.set_data(self.train_box_x, self.train_box_y)
        if val_box is not None:
            self.val_box_x.append(epoch)
            self.val_box_y.append(val_box)
            self._line_val_box.set_data(self.val_box_x, self.val_box_y)
        if train_cls is not None:
            self.train_cls_x.append(epoch)
            self.train_cls_y.append(train_cls)
            self._line_train_cls.set_data(self.train_cls_x, self.train_cls_y)
        if val_cls is not None:
            self.val_cls_x.append(epoch)
            self.val_cls_y.append(val_cls)
            self._line_val_cls.set_data(self.val_cls_x, self.val_cls_y)
        if prec is not None:
            self.prec_x.append(epoch)
            self.prec_y.append(prec)
            self._line_prec.set_data(self.prec_x, self.prec_y)
        if rec is not None:
            self.rec_x.append(epoch)
            self.rec_y.append(rec)
            self._line_rec.set_data(self.rec_x, self.rec_y)
        if m50 is not None:
            self.m50_x.append(epoch)
            self.m50_y.append(m50)
            self._line_m50.set_data(self.m50_x, self.m50_y)
        if m5095 is not None:
            self.m5095_x.append(epoch)
            self.m5095_y.append(m5095)
            self._line_m5095.set_data(self.m5095_x, self.m5095_y)

        # 重设坐标轴范围(简单做法:每次重算)
        for ax in (self.ax_box, self.ax_cls, self.ax_map, self.ax_pr):
            ax.relim()
            ax.autoscale_view()

        self.canvas.draw_idle()

        # 追加表格行
        row = [
            str(epoch),
            _fmt(train_box),
            _fmt(val_box),
            _fmt(train_cls),
            _fmt(val_cls),
            _fmt(prec),
            _fmt(rec),
            _fmt(m50),
            _fmt(m5095),
        ]
        self.table.insertRow(self.table.rowCount())
        for col, val in enumerate(row):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(self.table.rowCount() - 1, col, item)

        # 滚动到底
        if self.table.rowCount() > 0:
            self.table.scrollToBottom()

        # 状态
        if m50 is not None or m5095 is not None:
            self.status_label.setText(
                f"Epoch {epoch} · mAP50 {_fmt(m50)} · mAP50-95 {_fmt(m5095)}"
            )

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ---- 内部 ----
    def _init_lines(self) -> None:
        """重置所有数据 + 创建线对象(供 update_metrics 增量追加)。"""
        for ax in (self.ax_box, self.ax_map, self.ax_pr, self.ax_cls):
            ax.clear()
            ax.grid(True, alpha=0.3)

        self.ax_box.set_title("Box Loss (train / val)")
        self.ax_box.set_xlabel("epoch")
        self.train_box_x: list = []
        self.train_box_y: list = []
        self.val_box_x: list = []
        self.val_box_y: list = []
        (self._line_train_box,) = self.ax_box.plot([], [], "b-", label="train", linewidth=1.5)
        (self._line_val_box,) = self.ax_box.plot([], [], "r-", label="val", linewidth=1.5)
        self.ax_box.legend(loc="upper right", fontsize=8)

        self.ax_cls.set_title("Cls Loss (train / val)")
        self.ax_cls.set_xlabel("epoch")
        self.train_cls_x = []
        self.train_cls_y = []
        self.val_cls_x = []
        self.val_cls_y = []
        (self._line_train_cls,) = self.ax_cls.plot([], [], "b-", label="train", linewidth=1.5)
        (self._line_val_cls,) = self.ax_cls.plot([], [], "r-", label="val", linewidth=1.5)
        self.ax_cls.legend(loc="upper right", fontsize=8)

        self.ax_map.set_title("mAP")
        self.ax_map.set_xlabel("epoch")
        self.ax_map.set_ylim(0, 1)
        self.m50_x = []
        self.m50_y = []
        self.m5095_x = []
        self.m5095_y = []
        (self._line_m50,) = self.ax_map.plot([], [], "g-", label="mAP50", linewidth=1.5)
        (self._line_m5095,) = self.ax_map.plot([], [], "m-", label="mAP50-95", linewidth=1.5)
        self.ax_map.legend(loc="lower right", fontsize=8)

        self.ax_pr.set_title("Precision / Recall")
        self.ax_pr.set_xlabel("epoch")
        self.ax_pr.set_ylim(0, 1)
        self.prec_x = []
        self.prec_y = []
        self.rec_x = []
        self.rec_y = []
        (self._line_prec,) = self.ax_pr.plot([], [], "b-", label="precision", linewidth=1.5)
        (self._line_rec,) = self.ax_pr.plot([], [], "r-", label="recall", linewidth=1.5)
        self.ax_pr.legend(loc="lower right", fontsize=8)

        self.canvas.draw_idle()


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if f >= 0.01 or f == 0:
            return f"{f:.3f}"
        return f"{f:.4g}"
    except (TypeError, ValueError):
        return str(v)
