"""启动器对话框 — 启动应用时显示。

功能:
- 列出最近项目
- 打开现有项目
- 创建新项目(目录 + 名称 + 初始类)
- 浏览其他位置
- 取消退出

返回:Project 实例(用户在对话框内点击"打开"),或 None(用户取消)。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    LineEdit,
    MessageDialog,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
    TransparentToolButton,
)

from yolo_studio.core.class_config import ClassDef
from yolo_studio.core.paths import REPO_ROOT
from yolo_studio.core.project import Project
from yolo_studio.core.project_manager import ProjectEntry, ProjectManager


class NewProjectDialog(QDialog):
    """新建项目对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.resize(480, 360)
        self._result: Optional[tuple[Path, str, list[ClassDef]]] = None

        layout = QVBoxLayout(self)

        layout.addWidget(StrongBodyLabel("项目名称:"))
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("例如:car_detection")
        layout.addWidget(self.name_edit)

        layout.addWidget(StrongBodyLabel("项目位置:"))
        path_row = QHBoxLayout()
        self.path_edit = LineEdit()
        self.path_edit.setText(str(REPO_ROOT / "projects"))
        self.path_edit.setPlaceholderText("选择项目根目录(各项目作为子目录)")
        path_row.addWidget(self.path_edit, 1)
        browse_btn = PushButton("浏览…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        layout.addWidget(StrongBodyLabel("初始类(每行一个,从 0 开始):"))

        # 用 TableWidget(单列 "类名")取代原来的 LineEdit(toPlainText 错位)
        self.classes_edit = TableWidget()
        self.classes_edit.setColumnCount(1)
        self.classes_edit.setHorizontalHeaderLabels(["类名"])
        self.classes_edit.setRowCount(1)
        self.classes_edit.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.classes_edit.setAlternatingRowColors(True)
        # 默认放一个空行(用户直接在第一个格子里输入第一个类名)
        self.classes_edit.setItem(0, 0, QTableWidgetItem(""))
        layout.addWidget(self.classes_edit, 1)

        # "+" 加一行;行内直接编辑即可
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        add_btn = PushButton(FIF.ADD, "添加类")
        add_btn.clicked.connect(self._add_class_row)
        add_row.addWidget(add_btn)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = PrimaryPushButton("创建")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _add_class_row(self) -> None:
        """新增一行(空),光标定位到该行的类名单元格。"""
        r = self.classes_edit.rowCount()
        self.classes_edit.insertRow(r)
        item = QTableWidgetItem("")
        self.classes_edit.setItem(r, 0, item)
        self.classes_edit.setCurrentCell(r, 0)
        self.classes_edit.editItem(item)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择项目根目录", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def _on_ok(self) -> None:
        name = self.name_edit.text().strip()
        parent_dir = Path(self.path_edit.text().strip())
        if not name:
            MessageDialog("新建项目", "请输入项目名称。", self).exec()
            return
        if not parent_dir.exists():
            MessageDialog("新建项目", f"目录不存在:\n{parent_dir}", self).exec()
            return
        # 从 TableWidget 收集类名(每行 1 个)
        class_names: list[str] = []
        for r in range(self.classes_edit.rowCount()):
            item = self.classes_edit.item(r, 0)
            if item is None:
                continue
            text = item.text().strip()
            if text:
                class_names.append(text)
        if not class_names:
            MessageDialog("新建项目", "请至少定义一个类。", self).exec()
            return
        classes = [ClassDef(class_id=i, name=n) for i, n in enumerate(class_names)]
        target = parent_dir / name
        self._result = (target, name, classes)
        self.accept()

    def result_project(self) -> Optional[tuple[Path, str, list[ClassDef]]]:
        return self._result


class LauncherDialog(QDialog):
    """启动器主对话框。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("DetectSphere — 选择项目")
        self.resize(720, 480)
        self._manager = ProjectManager()
        self._selected: Optional[Project] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # 左侧:项目列表
        left = QVBoxLayout()
        left.addWidget(TitleLabel("最近项目"))
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        left.addWidget(self.list_widget, 1)
        self._refresh_list()

        # 右侧:操作
        right = QVBoxLayout()
        right.addWidget(TitleLabel("DetectSphere"))
        right.addWidget(BodyLabel("通用 YOLO 桌面工作台\n数据集 · 标注 · 训练 · 测试 · 导出"))
        right.addSpacing(16)
        right.addWidget(CaptionLabel(f"项目根:{REPO_ROOT}"))

        right.addSpacing(16)
        self.open_btn = PrimaryPushButton("打开选中项目")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._on_open_clicked)
        right.addWidget(self.open_btn)

        self.new_btn = PushButton("新建项目")
        self.new_btn.clicked.connect(self._on_new_clicked)
        right.addWidget(self.new_btn)

        self.browse_btn = PushButton("浏览其他位置…")
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        right.addWidget(self.browse_btn)

        right.addStretch(1)

        self.exit_btn = PushButton("退出")
        self.exit_btn.clicked.connect(self.reject)
        right.addWidget(self.exit_btn)

        container_left = QWidget()
        container_left.setLayout(left)
        container_right = QWidget()
        container_right.setLayout(right)

        layout.addWidget(container_left, 2)
        layout.addWidget(container_right, 1)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for entry in self._manager.list_recent():
            self._add_entry_item(entry)
        # 把已存在但未在列表里的项目也加入(发现式)
        known = {e.path for e in self._manager.list_recent()}
        projects_root = REPO_ROOT / "projects"
        if projects_root.exists():
            for child in sorted(projects_root.iterdir()):
                if not child.is_dir():
                    continue
                if str(child.resolve()) in known:
                    continue
                # 只显示含 dataset.yaml 的
                if (child / "configs" / "dataset.yaml").exists():
                    entry = ProjectEntry(
                        path=str(child.resolve()),
                        name=child.name,
                        last_opened=child.stat().st_mtime,
                        classes_count=0,
                        image_count=0,
                    )
                    self._add_entry_item(entry)

    def _add_entry_item(self, entry: ProjectEntry) -> None:
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.last_opened))
        # 用 setItemWidget 装复合 widget:左 label + 右删除按钮
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, entry.path)

        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)
        label = BodyLabel(
            f"{entry.name}\n  {entry.path}\n  {entry.classes_count} 类 · {entry.image_count} 图 · 上次打开 {last}"
        )
        label.setWordWrap(True)
        h.addWidget(label, 1)
        del_btn = TransparentToolButton(FIF.DELETE)
        del_btn.setToolTip("从启动器列表移除(项目文件保留)")
        del_btn.clicked.connect(lambda _checked=False, p=entry.path: self._on_delete_entry(p))
        h.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)
        w.setLayout(h)
        # 设 item 高度给 widget 留空间
        item.setSizeHint(w.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, w)

    def _on_selection_changed(self) -> None:
        self.open_btn.setEnabled(self.list_widget.currentItem() is not None)

    def _on_delete_entry(self, path: str) -> None:
        """从启动器列表移除某项目(项目文件保留在磁盘)。"""
        name = Path(path).name
        if not MessageDialog(
            "删除项目",
            f"从启动器列表移除:\n\n{name}\n{path}\n\n(项目文件仍保留在磁盘,可稍后重新添加)",
            self,
        ).exec():
            return
        self._manager.forget(path)
        # 移除对应 QListWidgetItem
        for i in range(self.list_widget.count() - 1, -1, -1):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == path:
                self.list_widget.takeItem(i)
                break

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self._open_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _on_open_clicked(self) -> None:
        item = self.list_widget.currentItem()
        if item:
            self._open_path(Path(item.data(Qt.ItemDataRole.UserRole)))

    def _on_browse_clicked(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择项目根目录")
        if d:
            self._open_path(Path(d))

    def _on_new_clicked(self) -> None:
        dlg = NewProjectDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            res = dlg.result_project()
            if res is None:
                return
            target, name, classes = res
            try:
                proj = self._manager.create(target, name=name, classes=classes)
            except Exception as e:
                MessageDialog("新建项目失败", str(e), self).exec()
                return
            self._selected = proj
            self.accept()

    def _open_path(self, path: Path) -> None:
        try:
            proj = self._manager.open(path)
        except Exception as e:
            MessageDialog("打开项目失败", str(e), self).exec()
            return
        self._selected = proj
        self.accept()

    # ---- 公开 API ----
    def run(self) -> Optional[Project]:
        """阻塞运行对话框,返回选中的 Project 或 None(用户取消)。"""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self._selected
        return None
