"""ModelRegistryPage — 项目模型管理。

列出 models/*.pt,操作:
- 「设为活动」 — 影响 TrainPage "Resume from" 和 TestPage 默认模型
- 「导出 ONNX」 — Ultralytics 的 model.export(format='onnx')
- 「删除」 — 从 models/ 删 + registry 移除
- 「导入外部模型」 — 文件选择 → 复制到 models/

布局:左侧列表,右侧详情 + 操作。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    MessageDialog,
    PushButton,
    PrimaryPushButton,
    StrongBodyLabel,
    TitleLabel,
)

from yolo_studio.core.db import ProjectDB
from yolo_studio.core.model_registry import (
    ModelEntry,
    add_entry,
    get_active_entry,
    import_model_from,
    load_registry,
    remove_entry,
    save_registry,
    scan_models,
    set_active,
)
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.log_pane import LogPane


class ModelRegistryPage(QWidget):
    """模型注册表页。"""

    modelsChanged = Signal()  # 模型列表变更(导入/删除/设为活动)

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ---- 左:列表 ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(TitleLabel("模型注册"))
        left_layout.addSpacing(4)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["活动", "名称", "mAP50", "mAP50-95", "来源", "创建时间"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.table, 1)

        # 工具栏
        toolbar = QHBoxLayout()
        self.refresh_btn = PushButton(FIF.SYNC, "刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)

        self.set_active_btn = PushButton(FIF.ACCEPT, "设为活动")
        self.set_active_btn.clicked.connect(self._on_set_active)
        self.set_active_btn.setEnabled(False)
        toolbar.addWidget(self.set_active_btn)

        self.export_btn = PushButton(FIF.SHARE, "导出 ONNX")
        self.export_btn.clicked.connect(self._on_export_onnx)
        self.export_btn.setEnabled(False)
        toolbar.addWidget(self.export_btn)

        self.import_btn = PushButton(FIF.ADD, "导入外部…")
        self.import_btn.clicked.connect(self._on_import_external)
        toolbar.addWidget(self.import_btn)

        self.delete_btn = PushButton(FIF.DELETE, "删除")
        self.delete_btn.clicked.connect(self._on_delete)
        self.delete_btn.setEnabled(False)
        toolbar.addWidget(self.delete_btn)

        toolbar.addStretch(1)
        self.open_dir_btn = PushButton("打开目录")
        self.open_dir_btn.clicked.connect(self._on_open_dir)
        toolbar.addWidget(self.open_dir_btn)

        left_layout.addLayout(toolbar)
        splitter.addWidget(left)

        # ---- 右:详情 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        right_layout.addWidget(StrongBodyLabel("模型详情"))
        self.detail_label = BodyLabel("选中一行查看详情")
        self.detail_label.setWordWrap(True)
        right_layout.addWidget(self.detail_label)

        right_layout.addSpacing(16)
        right_layout.addWidget(StrongBodyLabel("操作日志"))
        self.log_pane = LogPane()
        right_layout.addWidget(self.log_pane, 1)

        right_layout.addStretch(1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.refresh()

    # ---- 公开 API ----
    def refresh(self) -> None:
        """扫描 + 重新填充表格。"""
        entries = scan_models(self.project)
        reg = load_registry(self.project)
        active_name = reg.active_model

        self.table.setRowCount(0)
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)

            mark = "★" if entry.name == active_name else ""
            self.table.setItem(row, 0, _center_item(mark))
            self.table.setItem(row, 1, _item(entry.name))
            m = entry.metrics or {}
            self.table.setItem(row, 2, _center_item(f"{m.get('mAP50', 0):.3f}" if m else "—"))
            self.table.setItem(row, 3, _center_item(f"{m.get('mAP50-95', 0):.3f}" if m else "—"))
            self.table.setItem(row, 4, _item(entry.source))
            self.table.setItem(row, 5, _item(entry.created_at))

            # 把 name 存在 Qt.UserRole,供 _on_set_active 用
            self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, entry.name)

        # 自动选中第一行(若有)
        if self.table.rowCount() > 0:
            self.table.selectRow(0)

    # ---- 事件 ----
    @Slot()
    def _on_selection_changed(self) -> None:
        sel = self.table.selectionModel().selectedRows()
        has = bool(sel)
        self.set_active_btn.setEnabled(has)
        self.export_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        if has:
            row = sel[0].row()
            name_item = self.table.item(row, 1)
            if name_item:
                self._show_detail(name_item.text(), name_item.data(Qt.ItemDataRole.UserRole))

    def _show_detail(self, display: str, name: str) -> None:
        reg = load_registry(self.project)
        entry = next((m for m in reg.models if m.name == name), None)
        if entry is None:
            self.detail_label.setText(f"未找到 {name}")
            return
        lines = [
            f"<b>{entry.name}</b>",
            f"路径:{entry.path}",
            f"来源:{entry.source}",
            f"父运行:{entry.parent_run or '—'}",
            f"创建时间:{entry.created_at or '—'}",
        ]
        if entry.classes:
            lines.append(f"类:{', '.join(entry.classes)}")
        if entry.metrics:
            metrics = entry.metrics
            lines.append("指标:")
            for k, v in metrics.items():
                try:
                    lines.append(f"  {k}: {float(v):.4f}")
                except (TypeError, ValueError):
                    lines.append(f"  {k}: {v}")
        self.detail_label.setText("<br>".join(lines))

    def _selected_name(self) -> Optional[str]:
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        row = sel[0].row()
        item = self.table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_set_active(self) -> None:
        name = self._selected_name()
        if not name:
            return
        set_active(self.project, name)
        InfoBar.success(
            title="已设为活动模型",
            content=name,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        self.log_pane.append(f"[{time.strftime('%H:%M:%S')}] SetActive: {name}")
        self.refresh()
        self.modelsChanged.emit()

    def _on_export_onnx(self) -> None:
        name = self._selected_name()
        if not name:
            return
        reg = load_registry(self.project)
        entry = next((m for m in reg.models if m.name == name), None)
        if entry is None:
            return

        out_path = self.project.models_dir / (Path(name).stem + ".onnx")
        self.log_pane.append(f"[{time.strftime('%H:%M:%S')}] Exporting ONNX → {out_path}")
        try:
            from ultralytics import YOLO

            YOLO(str(entry.path)).export(format="onnx", imgsz=640)
            # Ultralytics 默认输出到同目录同名 .onnx
            if out_path.exists():
                self.log_pane.append(f"[{time.strftime('%H:%M:%S')}] Done → {out_path}")
                InfoBar.success(
                    title="ONNX 导出完成",
                    content=str(out_path),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                )
            else:
                self.log_pane.append(f"[warn] .onnx 预期路径不存在:{out_path}")
                InfoBar.warning(
                    title="导出异常",
                    content="请查看日志",
                    parent=self,
                    position=InfoBarPosition.TOP,
                )
        except Exception as e:
            self.log_pane.append(f"[ERROR] {e}")
            InfoBar.error(
                title="ONNX 导出失败",
                content=str(e)[:200],
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )

    def _on_import_external(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self,
            "选择外部 .pt 模型",
            "",
            "PyTorch 模型 (*.pt)",
        )
        if not f:
            return
        src = Path(f)
        try:
            entry = import_model_from(self.project, src)
            self.log_pane.append(f"[{time.strftime('%H:%M:%S')}] Imported: {entry.name}")
            InfoBar.success(
                title="导入完成",
                content=entry.name,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000,
            )
            self.refresh()
            self.modelsChanged.emit()
        except Exception as e:
            self.log_pane.append(f"[ERROR] import failed: {e}")
            InfoBar.error(
                title="导入失败",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _on_delete(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if not MessageDialog(
            "删除模型",
            f"确认从 models/ 删除 {name}?",
            self,
        ).exec():
            return
        reg = load_registry(self.project)
        entry = next((m for m in reg.models if m.name == name), None)
        if entry is None:
            return
        # 删文件
        path = Path(entry.path)
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                InfoBar.error(
                    title="删除失败",
                    content=str(e),
                    parent=self,
                    position=InfoBarPosition.TOP,
                )
                return
        # 删 registry
        remove_entry(self.project, name)
        self.log_pane.append(f"[{time.strftime('%H:%M:%S')}] Deleted: {name}")
        InfoBar.success(
            title="已删除",
            content=name,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )
        self.refresh()
        self.modelsChanged.emit()

    def _on_open_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.models_dir)))


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    return it


def _center_item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return it
