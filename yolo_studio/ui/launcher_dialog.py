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
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
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
        self.classes_edit = LineEdit()
        self.classes_edit.setPlaceholderText("例:car\ntruck\npedestrian")
        self.classes_edit.setMinimumHeight(120)
        layout.addWidget(self.classes_edit, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = PrimaryPushButton("创建")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择项目根目录", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def _on_ok(self) -> None:
        name = self.name_edit.text().strip()
        parent_dir = Path(self.path_edit.text().strip())
        if not name:
            QMessageBox.warning(self, "新建项目", "请输入项目名称。")
            return
        if not parent_dir.exists():
            QMessageBox.warning(self, "新建项目", f"目录不存在:\n{parent_dir}")
            return
        classes_raw = self.classes_edit.toPlainText().strip()
        if not classes_raw:
            QMessageBox.warning(self, "新建项目", "请至少定义一个类。")
            return
        class_names = [line.strip() for line in classes_raw.splitlines() if line.strip()]
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
        self.setWindowTitle("YOLO Studio — 选择项目")
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
        right.addWidget(TitleLabel("YOLO Studio"))
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
        text = f"{entry.name}\n  {entry.path}\n  {entry.classes_count} 类 · {entry.image_count} 图 · 上次打开 {last}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, entry.path)
        self.list_widget.addItem(item)

    def _on_selection_changed(self) -> None:
        self.open_btn.setEnabled(self.list_widget.currentItem() is not None)

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
                QMessageBox.critical(self, "新建项目失败", str(e))
                return
            self._selected = proj
            self.accept()

    def _open_path(self, path: Path) -> None:
        try:
            proj = self._manager.open(path)
        except Exception as e:
            QMessageBox.critical(self, "打开项目失败", str(e))
            return
        self._selected = proj
        self.accept()

    # ---- 公开 API ----
    def run(self) -> Optional[Project]:
        """阻塞运行对话框,返回选中的 Project 或 None(用户取消)。"""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self._selected
        return None
