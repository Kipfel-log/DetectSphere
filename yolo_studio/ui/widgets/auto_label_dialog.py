"""AutoLabelDialog — AI 预标注模型选择与参数设置对话框。

功能:
- 下拉框选择项目模型 (默认选中活动模型)
- 滑块调节置信度 (conf 0.05 ~ 0.95)
- (可选) 勾选“只针对未标注图像生效”
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    MessageBoxBase,
    Slider,
    SubtitleLabel,
)

from yolo_studio.core.model_registry import load_registry, scan_models
from yolo_studio.core.project import Project


class AutoLabelDialog(MessageBoxBase):
    """AI 预标注对话框。"""

    def __init__(
        self,
        project: Project,
        title: str = "AI 辅助预标注设置",
        is_batch: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.is_batch = is_batch

        self.title_label = SubtitleLabel(title, self)
        self.viewLayout.addWidget(self.title_label)

        # 表单布局
        form = QFormLayout()
        form.setSpacing(12)

        # 1. 模型选择
        self.model_combo = ComboBox(self)
        self._populate_models()
        form.addRow("选择模型:", self.model_combo)

        # 2. conf 滑块
        conf_box = QVBoxLayout()
        conf_box.setSpacing(2)
        conf_row = QHBoxLayout()
        self.conf_slider = Slider(Qt.Orientation.Horizontal, self)
        self.conf_slider.setRange(5, 95)
        self.conf_slider.setValue(35)
        conf_row.addWidget(self.conf_slider, 1)

        self.conf_val_label = BodyLabel("0.35", self)
        self.conf_val_label.setMinimumWidth(40)
        conf_row.addWidget(self.conf_val_label)
        conf_box.addLayout(conf_row)

        self.conf_slider.valueChanged.connect(
            lambda v: self.conf_val_label.setText(f"{v / 100:.2f}")
        )
        form.addRow("置信度 (conf):", conf_box)

        # 3. 批量特有: 仅未标注
        if self.is_batch:
            self.only_unlabeled_check = CheckBox("仅对未标注的图像生效", self)
            self.only_unlabeled_check.setChecked(True)
            form.addRow("范围约束:", self.only_unlabeled_check)
        else:
            self.only_unlabeled_check = None

        self.viewLayout.addLayout(form)

        # 设置按钮文字
        self.yesButton.setText("开始预标注")
        self.cancelButton.setText("取消")

        self.widget.setMinimumWidth(420)

    def _populate_models(self) -> None:
        """填充模型下拉。"""
        self.model_combo.clear()
        try:
            scan_models(self.project)
            reg = load_registry(self.project)
            active = reg.active_model
            active_idx = 0

            for i, entry in enumerate(reg.models):
                label = entry.name + (" ★(当前活动)" if entry.name == active else "")
                path_str = str(self.project.models_dir / entry.name)
                self.model_combo.addItem(label, userData=path_str)
                if entry.name == active:
                    active_idx = i

            if reg.models:
                self.model_combo.setCurrentIndex(active_idx)
        except Exception:
            pass

    def get_selected_model_path(self) -> Optional[Path]:
        path_str = self.model_combo.currentData()
        return Path(path_str) if path_str else None

    def get_conf(self) -> float:
        return self.conf_slider.value() / 100.0

    def is_only_unlabeled(self) -> bool:
        if self.only_unlabeled_check:
            return self.only_unlabeled_check.isChecked()
        return False
