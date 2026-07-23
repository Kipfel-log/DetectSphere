"""ProjectSettingsPage — 项目级设置。

包含:
  - 项目元数据(只读:name、root)
  - 类编辑(ClassEditor)→ 应用后写回 dataset.yaml
  - 数据集划分(70/20/10,可调比例)
  - 危险操作:重新划分、清空标注(占位)
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
)

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml, save_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.dataset import split_dataset
from yolo_studio.core.io.manifest import rebuild_from_disk
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.class_editor import ClassEditor


class ProjectSettingsPage(QWidget):
    """项目设置页。"""

    classesChanged = Signal(list)  # 新类列表

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db
        self._suppress_signal = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        layout.addWidget(TitleLabel("项目设置"))

        # ---- 元信息 ----
        meta_box = QFormLayout()
        meta_box.addRow("项目名:", BodyLabel(project.name))
        meta_box.addRow("路径:", BodyLabel(str(project.root)))
        meta_box.addRow("类别数:", BodyLabel(str(project.num_classes())))
        layout.addLayout(meta_box)

        # ---- 类编辑 ----
        layout.addSpacing(16)
        layout.addWidget(StrongBodyLabel("类别"))
        self.class_editor = ClassEditor(project.classes)
        self.class_editor.classesChanged.connect(self._on_classes_applied)
        layout.addWidget(self.class_editor, 2)

        # ---- 数据集划分 ----
        layout.addSpacing(16)
        layout.addWidget(StrongBodyLabel("数据集划分"))
        split_box = QFormLayout()

        self.train_spin = QSpinBox()
        self.train_spin.setRange(0, 100)
        self.train_spin.setValue(70)
        self.train_spin.setSuffix(" %")
        split_box.addRow("训练集比例:", self.train_spin)

        self.val_spin = QSpinBox()
        self.val_spin.setRange(0, 100)
        self.val_spin.setValue(20)
        self.val_spin.setSuffix(" %")
        split_box.addRow("验证集比例:", self.val_spin)

        self.test_spin = QSpinBox()
        self.test_spin.setRange(0, 100)
        self.test_spin.setValue(10)
        self.test_spin.setSuffix(" %")
        split_box.addRow("测试集比例:", self.test_spin)

        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999999)
        self.seed_spin.setValue(42)
        split_box.addRow("随机种子:", self.seed_spin)

        layout.addLayout(split_box)

        # 划分按钮
        split_row = QHBoxLayout()
        self.split_btn = PrimaryPushButton(FIF.SEND, "重新划分数据集")
        self.split_btn.clicked.connect(self._on_split)
        split_row.addWidget(self.split_btn)
        layout.addLayout(split_row)

        # 自动调整 test 比例(让三者之和 = 100)
        def _sync_test():
            if self._suppress_signal:
                return
            self._suppress_signal = True
            total = self.train_spin.value() + self.val_spin.value()
            self.test_spin.setValue(max(0, 100 - total))
            self._suppress_signal = False

        self.train_spin.valueChanged.connect(lambda _: _sync_test())
        self.val_spin.valueChanged.connect(lambda _: _sync_test())

        layout.addStretch(1)

        # ---- 提示 ----
        layout.addWidget(
            CaptionLabel(
                "• 修改类后点 '应用' 写回 dataset.yaml\n"
                "• 重新划分会覆盖现有 train/val/test 目录(原图不会被删)"
            )
        )

    # ---- 事件 ----
    def _on_classes_applied(self, classes: list[ClassDef]) -> None:
        """用户点 ClassEditor 的'应用'按钮。"""
        # 写盘
        try:
            save_dataset_yaml(self.project.dataset_yaml, classes)
        except Exception as e:
            QMessageBox.critical(self, "写入失败", str(e))
            return
        self.project.set_classes(classes)
        InfoBar.success(
            title="已应用",
            content=f"已写入 {self.project.dataset_yaml}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        # 广播
        self.classesChanged.emit(classes)

    def _on_split(self) -> None:
        train = self.train_spin.value() / 100
        val = self.val_spin.value() / 100
        test = self.test_spin.value() / 100
        if abs(train + val + test - 1.0) > 1e-6:
            QMessageBox.warning(self, "比例错误", "训练/验证/测试比例之和必须等于 100%。")
            return
        if QMessageBox.question(
            self,
            "重新划分",
            f"将覆盖 train/val/test 目录。\n比例:{int(train*100)}% / {int(val*100)}% / {int(test*100)}%\n继续?",
        ) != QMessageBox.StandardButton.Yes:
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
            QMessageBox.critical(self, "划分失败", str(e))
            return

        # 重新建 manifest
        rebuild_from_disk(self.project, self.db)

        InfoBar.success(
            title="划分完成",
            content=(
                f"训练 {stats.train} · 验证 {stats.val} · 测试 {stats.test} · "
                f"未划分 {stats.unlabeled} (共 {stats.total})"
            ),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )
