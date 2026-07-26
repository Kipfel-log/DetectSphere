"""ClassEditor — 类的增删改 UI。

QTableView + 工具栏:
  - + 添加类
  - - 删除选中
  - ↑ 上移(交换 ID)
  - ↓ 下移
  - 应用(写回 dataset.yaml)

注意:ID 改变会导致已有 .txt 文件中的 class_id 错位;在应用前弹确认。
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import ColorDialog, FluentIcon as FIF, MessageDialog, PushButton

from yolo_studio.core.class_config import ClassDef, DEFAULT_CLASS_PALETTE, default_color_for


class _ClassModel(QAbstractTableModel):
    """类列表的表格模型。

    列:ID | Name | Color
    - Color 列用 DecorationRole 画色块;DisplayRole 显示 hex 文本
    - Color 列不直接编辑,双击单元格由 ClassEditor 弹 ColorDialog 选色
    """

    HEADERS = ["ID", "Name", "Color"]

    def __init__(self, classes: list[ClassDef], parent=None) -> None:
        super().__init__(parent)
        # 复制一份，同时为没有颜色的类自动分配默认调色板颜色
        used_colors: set[str] = {c.color for c in classes if c.color}
        result: list[ClassDef] = []
        for c in classes:
            color = c.color
            if not color:
                color = default_color_for(c.class_id, used_colors)
                used_colors.add(color)
            result.append(ClassDef(class_id=c.class_id, name=c.name, color=color))
        self._classes = result

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._classes)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._classes):
            return None
        c = self._classes[index.row()]
        if index.column() == 0:
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                return c.class_id
        elif index.column() == 1:
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                return c.name
        elif index.column() == 2:
            # 颜色列:DecorationRole → QColor 自动画色块;DisplayRole → hex 文本
            if role == Qt.ItemDataRole.DecorationRole and c.color:
                return QColor(c.color)
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return c.color if c.color else "(未设)"
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or index.row() >= len(self._classes):
            return False
        if role != Qt.ItemDataRole.EditRole:
            return False
        if index.column() == 1:
            new_name = str(value).strip()
            if not new_name:
                return False
            for i, c in enumerate(self._classes):
                if i != index.row() and c.name == new_name:
                    return False
            old = self._classes[index.row()]
            self._classes[index.row()] = ClassDef(class_id=old.class_id, name=new_name, color=old.color)
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def set_color(self, row: int, hex_color: str) -> bool:
        """由 ColorDialog 触发 — 直接改 color 字段并 emit。"""
        if not (0 <= row < len(self._classes)):
            return False
        old = self._classes[row]
        if old.color == hex_color:
            return False
        self._classes[row] = ClassDef(class_id=old.class_id, name=old.name, color=hex_color)
        idx_left = self.index(row, 0)
        idx_right = self.index(row, 2)
        self.dataChanged.emit(idx_left, idx_right, [])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 1:
            base |= Qt.ItemFlag.ItemIsEditable
        # 颜色列不直接编辑 — 双击走 ColorDialog
        return base

    # ---- 列表操作 ----
    def add_class(self) -> int:
        new_id = max((c.class_id for c in self._classes), default=-1) + 1
        # 找一个唯一默认名
        base = "new_class"
        name = base
        suffix = 1
        existing = {c.name for c in self._classes}
        while name in existing:
            suffix += 1
            name = f"{base}_{suffix}"
        # 自动从调色板分一个未用的颜色
        used_colors = {c.color for c in self._classes if c.color}
        new_color = default_color_for(new_id, used_colors)
        self._classes.append(ClassDef(class_id=new_id, name=name, color=new_color))
        row = len(self._classes) - 1
        self.beginInsertRows(QModelIndex(), row, row)
        self.endInsertRows()
        return row

    def remove_class(self, row: int) -> bool:
        if not (0 <= row < len(self._classes)):
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        self._classes.pop(row)
        self.endRemoveRows()
        return True

    def move(self, row: int, delta: int) -> bool:
        new_row = row + delta
        if not (0 <= row < len(self._classes)) or not (0 <= new_row < len(self._classes)):
            return False
        # ID 跟着交换(颜色跟随原 class 走,不互换)
        a = self._classes[row]
        b = self._classes[new_row]
        a_id, b_id = a.class_id, b.class_id
        a2 = ClassDef(class_id=b_id, name=a.name, color=a.color)
        b2 = ClassDef(class_id=a_id, name=b.name, color=b.color)
        self._classes[row] = a2
        self._classes[new_row] = b2
        top = self.index(min(row, new_row), 0)
        bot = self.index(max(row, new_row), 2)
        self.dataChanged.emit(top, bot, [])
        return True

    def classes(self) -> list[ClassDef]:
        return list(self._classes)


class ClassEditor(QWidget):
    """类编辑器 widget。"""

    classesChanged = Signal(list)  # 编辑后,emit 当前类列表(尚未写盘)

    def __init__(self, classes: list[ClassDef], parent=None) -> None:
        super().__init__(parent)
        self._model = _ClassModel(classes)
        self._has_changes = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        toolbar = QHBoxLayout()
        self.add_btn = PushButton(FIF.ADD, "添加类")
        self.add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(self.add_btn)

        self.del_btn = PushButton(FIF.DELETE, "删除")
        self.del_btn.clicked.connect(self._on_delete)
        toolbar.addWidget(self.del_btn)

        self.up_btn = PushButton(FIF.UP, "上移")
        self.up_btn.clicked.connect(lambda: self._on_move(-1))
        toolbar.addWidget(self.up_btn)

        self.down_btn = PushButton(FIF.DOWN, "下移")
        self.down_btn.clicked.connect(lambda: self._on_move(1))
        toolbar.addWidget(self.down_btn)

        toolbar.addStretch(1)

        self.reset_btn = PushButton("重置")
        self.reset_btn.clicked.connect(self._on_reset)
        toolbar.addWidget(self.reset_btn)

        self.apply_btn = PushButton("应用")
        self.apply_btn.clicked.connect(self._on_apply)
        toolbar.addWidget(self.apply_btn)

        layout.addLayout(toolbar)

        # 表格
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(False)
        # ID 列(Name 拉伸)
        self.table.setColumnWidth(0, 50)
        # 颜色列固定宽度,让 Name 列拉伸
        self.table.setColumnWidth(2, 120)
        # Name 列拉伸
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        # 双击颜色列 → 打开 ColorDialog
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table, 1)

    # ---- 公开 API ----
    def classes(self) -> list[ClassDef]:
        return self._model.classes()

    def has_changes(self) -> bool:
        return self._has_changes

    def set_classes(self, classes: list[ClassDef]) -> None:
        self._model = _ClassModel(classes)
        self.table.setModel(self._model)
        self._has_changes = False

    # ---- handlers ----
    def _on_add(self) -> None:
        row = self._model.add_class()
        idx = self._model.index(row, 1)
        self.table.setCurrentIndex(idx)
        self.table.edit(idx)
        self._mark_changed()

    def _on_delete(self) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        row = idx.row()
        if not MessageDialog(
            "删除类",
            f"确认删除类 '{self._model.classes()[row].name}'?\n"
            f"(这不会自动迁移已有 .txt 文件中的 class_id)",
            self,
        ).exec():
            return
        self._model.remove_class(row)
        self._mark_changed()

    def _on_move(self, delta: int) -> None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        if self._model.move(idx.row(), delta):
            self.table.selectRow(idx.row() + delta)
            self._mark_changed()

    def _on_reset(self) -> None:
        if not MessageDialog("重置", "放弃所有未应用的修改?", self).exec():
            return
        # 由调用方通过 set_classes 重新载入
        self.classesChanged.emit(self._model.classes())
        self._has_changes = False

    def _on_apply(self) -> None:
        classes = self._model.classes()
        if not classes:
            MessageDialog("应用", "类列表不能为空。", self).exec()
            return
        if not MessageDialog(
            "应用类修改",
            "将以下类定义写入 dataset.yaml?\n\n"
            + "\n".join(f"  {c.class_id}: {c.name}" for c in classes)
            + "\n\n注意:类 ID/顺序的改变会导致已有 .txt 文件中的 class_id 错位。",
            self,
        ).exec():
            return
        self.classesChanged.emit(classes)
        self._has_changes = False

    def _mark_changed(self) -> None:
        self._has_changes = True

    def _on_table_double_clicked(self, index: QModelIndex) -> None:
        """双击颜色列 → 打开 ColorDialog 选色（以顶级主窗口为父级，确保全屏弹出）。"""
        if not index.isValid() or index.column() != 2:
            return
        row = index.row()
        cur_color = self._model._classes[row].color or "#888888"
        # 使用顶级窗口作为父级，避免 ColorDialog 被嵌入小区域内
        top_window = self.window()
        dlg = ColorDialog(QColor(cur_color), f"选择类 {row} 的颜色", top_window)
        if dlg.exec():
            new_hex = dlg.color.name().lower()  # "#rrggbb"
            self._model.set_color(row, new_hex)
            self._mark_changed()
