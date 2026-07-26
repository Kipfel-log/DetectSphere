"""ProjectSettingsPage — 项目级设置页（全面使用 PyQt-Fluent-Widgets 重写）。

包含:
  - 项目元数据 (CardWidget)
  - 类编辑 (ClassEditor)
  - 数据集划分 (SpinBox + 可视化进度条)
  - 危险操作区
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    MessageDialog,
    PrimaryPushButton,
    PushButton,
    SmoothScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml, save_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.dataset import list_images, split_dataset
from yolo_studio.core.io.manifest import rebuild_from_disk
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.class_editor import ClassEditor


# ── 可视化比例条 ─────────────────────────────────────────────────────────────
class _SplitBar(QWidget):
    """显示 train/val/test 三段比例的彩色可视化条。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(20)
        self._train = 70
        self._val = 20
        self._test = 10

    def set_values(self, train: int, val: int, test: int) -> None:
        self._train = train
        self._val = val
        self._test = test
        self.update()

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter, QPainterPath
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = max(self._train + self._val + self._test, 1)
        w = self.width()
        h = self.height()
        r = 6  # corner radius

        colors = [QColor("#4CAF50"), QColor("#2196F3"), QColor("#FF9800")]
        ratios = [self._train / total, self._val / total, self._test / total]

        x = 0
        for i, (ratio, color) in enumerate(zip(ratios, colors)):
            seg_w = int(w * ratio)
            if i == len(ratios) - 1:  # 最后一段填满剩余宽度
                seg_w = w - x
            path = QPainterPath()
            left_r = r if i == 0 else 0
            right_r = r if i == len(ratios) - 1 else 0
            path.moveTo(x + left_r, 0)
            path.lineTo(x + seg_w - right_r, 0)
            path.arcTo(x + seg_w - right_r * 2, 0, right_r * 2, right_r * 2, 90, -90)
            path.lineTo(x + seg_w, h - right_r)
            path.arcTo(x + seg_w - right_r * 2, h - right_r * 2, right_r * 2, right_r * 2, 0, -90)
            path.lineTo(x + left_r, h)
            path.arcTo(x, h - right_r * 2, right_r * 2, right_r * 2, 270, -90)
            path.lineTo(x, right_r)
            path.arcTo(x, 0, right_r * 2, right_r * 2, 180, -90)
            path.closeSubpath()
            painter.fillPath(path, color)
            x += seg_w
        painter.end()


# ── 设置卡片基础组件 ─────────────────────────────────────────────────────────
class _SectionCard(CardWidget):
    """带标题的内容卡片。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(20, 16, 20, 16)
        self._root.setSpacing(12)

        hdr = SubtitleLabel(title)
        hdr.setStyleSheet("font-weight: 600;")
        self._root.addWidget(hdr)

    def body_layout(self) -> QVBoxLayout:
        return self._root


class _MetaRow(QHBoxLayout):
    """元数据行：左标签 + 右值。"""

    def __init__(self, label: str, value: str) -> None:
        super().__init__()
        lbl = StrongBodyLabel(label)
        lbl.setFixedWidth(80)
        val = BodyLabel(value)
        val.setWordWrap(True)
        self.addWidget(lbl)
        self.addWidget(val, 1)


# ── 主页面 ───────────────────────────────────────────────────────────────────
class ProjectSettingsPage(QWidget):
    """项目设置页（PyQt-Fluent-Widgets 全面重写版）。"""

    classesChanged = Signal(list)  # 新类列表
    datasetChanged = Signal()       # 数据集划分/结构变更

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db
        self._suppress_signal = False

        # ── 外层 SmoothScrollArea ──────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                background: transparent; width: 6px;
                margin: 2px 2px 2px 0; border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0,0,0,0.2); border-radius: 3px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(0,0,0,0.35); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        # 使用普通 QVBoxLayout，而非 ExpandLayout（后者仅支持 GroupHeaderCardWidget）
        page_layout = QVBoxLayout(container)
        page_layout.setContentsMargins(24, 16, 24, 24)
        page_layout.setSpacing(12)

        scroll.setWidget(container)
        outer.addWidget(scroll)

        # ── 页面标题 ─────────────────────────────────────────────────────────
        title_lbl = TitleLabel("项目设置")
        page_layout.addWidget(title_lbl)

        # ── 卡片 1：项目元数据 ───────────────────────────────────────────────
        meta_card = _SectionCard("项目信息")
        meta_card.body_layout().addLayout(_MetaRow("项目名", project.name))
        meta_card.body_layout().addLayout(_MetaRow("根目录", str(project.root)))
        meta_card.body_layout().addLayout(_MetaRow("类别数", str(project.num_classes())))
        page_layout.addWidget(meta_card)

        # ── 卡片 2：类别编辑 ─────────────────────────────────────────────────
        cls_card = _SectionCard("类别管理")
        self.class_editor = ClassEditor(project.classes)
        self.class_editor.classesChanged.connect(self._on_classes_applied)
        cls_card.body_layout().addWidget(self.class_editor)
        page_layout.addWidget(cls_card)

        # ── 卡片 3：数据集划分 ───────────────────────────────────────────────
        split_card = _SectionCard("数据集划分")
        split_body = split_card.body_layout()

        # 可视化比例条
        self._split_bar = _SplitBar()
        split_body.addWidget(self._split_bar)

        # 图例
        legend = QHBoxLayout()
        for color, text in [("#4CAF50", "训练集"), ("#2196F3", "验证集"), ("#FF9800", "测试集")]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 16px;")
            lbl = CaptionLabel(text)
            legend.addWidget(dot)
            legend.addWidget(lbl)
            legend.addSpacing(12)
        legend.addStretch(1)
        split_body.addLayout(legend)

        # 比例输入行
        def _make_spin_row(label: str, value: int, color: str) -> SpinBox:
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 16px;")
            row.addWidget(dot)
            row.addWidget(StrongBodyLabel(label))
            spin = SpinBox()
            spin.setRange(0, 100)
            spin.setValue(value)
            spin.setSuffix(" %")
            spin.setFixedWidth(120)
            row.addStretch(1)
            row.addWidget(spin)
            split_body.addLayout(row)
            return spin

        self.train_spin = _make_spin_row("训练集比例", 70, "#4CAF50")
        self.val_spin   = _make_spin_row("验证集比例", 20, "#2196F3")
        self.test_spin  = _make_spin_row("测试集比例", 10, "#FF9800")

        # 随机种子
        seed_row = QHBoxLayout()
        seed_row.addWidget(StrongBodyLabel("随机种子"))
        self.seed_spin = SpinBox()
        self.seed_spin.setRange(0, 999999)
        self.seed_spin.setValue(42)
        self.seed_spin.setFixedWidth(120)
        seed_row.addStretch(1)
        seed_row.addWidget(self.seed_spin)
        split_body.addLayout(seed_row)

        # 自动同步 test = 100 - train - val
        def _sync_test():
            if self._suppress_signal:
                return
            self._suppress_signal = True
            total = self.train_spin.value() + self.val_spin.value()
            self.test_spin.setValue(max(0, 100 - total))
            self._suppress_signal = False
            self._update_split_bar()

        self.train_spin.valueChanged.connect(lambda _: _sync_test())
        self.val_spin.valueChanged.connect(lambda _: _sync_test())
        self.test_spin.valueChanged.connect(lambda _: self._update_split_bar())

        # 划分按钮
        self.split_btn = PrimaryPushButton(FIF.SEND, "重新划分数据集")
        self.split_btn.clicked.connect(self._on_split)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.split_btn)
        split_body.addLayout(btn_row)

        page_layout.addWidget(split_card)

        # ── 提示 ──────────────────────────────────────────────────────────────
        hint_card = CardWidget()
        hint_layout = QVBoxLayout(hint_card)
        hint_layout.setContentsMargins(20, 12, 20, 12)
        hint_layout.addWidget(CaptionLabel(
            "• 修改类后点\"应用\"写回 dataset.yaml\n"
            "• 重新划分会覆盖现有 train/val/test 目录（原图不会被删除）"
        ))
        page_layout.addWidget(hint_card)
        page_layout.addStretch(1)

        # 初始更新比例条
        self._update_split_bar()


    # ── 内部方法 ─────────────────────────────────────────────────────────────

    def _update_split_bar(self) -> None:
        self._split_bar.set_values(
            self.train_spin.value(),
            self.val_spin.value(),
            self.test_spin.value(),
        )

    def _on_classes_applied(self, classes: list[ClassDef]) -> None:
        try:
            save_dataset_yaml(self.project.dataset_yaml, classes)
        except Exception as e:
            MessageDialog("写入失败", str(e), self).exec()
            return
        self.project.set_classes(classes)
        InfoBar.success(
            title="已应用",
            content=f"已写入 {self.project.dataset_yaml}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        self.classesChanged.emit(classes)

    def _on_split(self) -> None:
        train = self.train_spin.value() / 100
        val   = self.val_spin.value()   / 100
        test  = self.test_spin.value()  / 100
        if abs(train + val + test - 1.0) > 1e-6:
            MessageDialog("比例错误", "训练/验证/测试比例之和必须等于 100%。", self).exec()
            return

        has_new = bool(list_images(self.project.images_dir))
        has_existing = any(
            list_images(getattr(self.project, f"{s}_images"))
            for s in ("train", "val", "test")
        )
        if has_new and has_existing:
            confirm_msg = (
                f"检测到 data/images 中有新图，且 train/val/test 已有数据。\n"
                f"将只对新图按 {int(train*100)}% / {int(val*100)}% / {int(test*100)}% "
                f"划分并追加，已有数据不会被覆盖或重新打乱。\n继续?"
            )
        else:
            confirm_msg = (
                f"将覆盖 train/val/test 目录。\n"
                f"比例：{int(train*100)}% / {int(val*100)}% / {int(test*100)}%\n继续?"
            )
        if not MessageDialog("重新划分", confirm_msg, self).exec():
            return

        try:
            stats = split_dataset(
                self.project,
                train_ratio=train,
                val_ratio=val,
                test_ratio=test,
                seed=self.seed_spin.value(),
            )
        except Exception as e:
            MessageDialog("划分失败", str(e), self).exec()
            return

        rebuild_from_disk(self.project, self.db)

        if stats.mode == "incremental":
            content = (
                f"检测到已有 train/val/test，仅对 data/images 中的新图做了增量划分：\n"
                f"新增训练 {stats.train} · 验证 {stats.val} · 测试 {stats.test}"
                f"（共 {stats.total} 张，{stats.skipped} 张因同名冲突被跳过）"
            )
        else:
            content = (
                f"训练 {stats.train} · 验证 {stats.val} · 测试 {stats.test} · "
                f"未划分 {stats.unlabeled}（共 {stats.total}）"
            )
        InfoBar.success(
            title="划分完成",
            content=content,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
        self.datasetChanged.emit()
