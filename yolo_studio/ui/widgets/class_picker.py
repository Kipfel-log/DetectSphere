"""ClassPicker — 标注时的类下拉选择。

封装 Fluent ComboBox,数据源是 ClassDef 列表。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from qfluentwidgets import ComboBox

from yolo_studio.core.class_config import ClassDef


class ClassPicker(ComboBox):
    """标注页用的类下拉。"""

    classChanged = Signal(int)  # 选中的 class_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._classes: list[ClassDef] = []
        self.currentIndexChanged.connect(self._on_index_changed)

    def set_classes(self, classes: list[ClassDef]) -> None:
        """重新填充类列表(尽量保留当前选择)。"""
        prev_id = self.current_class_id()
        self.blockSignals(True)
        self.clear()
        self._classes = list(classes)
        for c in classes:
            self.addItem(f"{c.class_id}: {c.name}", userData=c.class_id)
        # 还原选择
        for i, c in enumerate(classes):
            if c.class_id == prev_id:
                self.setCurrentIndex(i)
                break
        self.blockSignals(False)
        self._on_index_changed(self.currentIndex())

    def current_class_id(self) -> int:
        idx = self.currentIndex()
        if 0 <= idx < len(self._classes):
            return self._classes[idx].class_id
        return -1

    def set_current_class_id(self, class_id: int) -> None:
        for i, c in enumerate(self._classes):
            if c.class_id == class_id:
                self.setCurrentIndex(i)
                return

    def _on_index_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._classes):
            self.classChanged.emit(self._classes[idx].class_id)
