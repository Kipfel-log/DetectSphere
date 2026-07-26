"""AnnotatePage — 标注页。

左侧:图像列表(分 split 标签)
中间:AnnotationCanvas
右侧:当前类(RadioButton 互斥组) + 工具按钮 + 当前 boxes 列表

行为:
  - 打开图像 → 加载已有 boxes(从 .txt 或 DB)
  - 用户编辑 boxes → 自动保存(写 .txt + DB)
  - 双击左侧图像 → 切换当前图像
  - 切换 split 标签 → 加载该 split 的图像列表
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushButton,
    RadioButton,
    StrongBodyLabel,
    TitleLabel,
    TreeView,
)

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.dataset import (
    get_split_for_image,
    list_all_images_by_split,
    save_boxes_for_image,
)
from yolo_studio.core.io.labels import Box, read_yolo_txt
from yolo_studio.core.io.manifest import _is_image
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.annotation_canvas import AnnotationCanvas, Mode


class AnnotatePage(QWidget):
    """标注页。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db
        self._current_image: Optional[Path] = None
        self._suppress_save = False
        self._is_modified = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ---- 左:图像列表(单一 TreeView,按 split 分组) ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(StrongBodyLabel("图像"))

        # TreeView 模型(顶层节点 = split,子节点 = 图像文件名)
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["图像"])
        self.tree = TreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        # 单击即打开(clicked 信号 = QAbstractItemView 标准)
        self.tree.clicked.connect(self._on_image_clicked)
        left_layout.addWidget(self.tree, 1)

        # split 元数据(标签 / 图标 / tooltip)
        self._splits_order = ["train", "val", "test", "unassigned"]
        self._split_meta = {
            "train": ("训练 train", FIF.EDIT, "训练集 — 用来训练模型的图像"),
            "val": ("验证 val", FIF.SEARCH, "验证集 — 训练中用来评估模型的图像"),
            "test": ("测试 test", FIF.SEND, "测试集 — 训练完后评估泛化能力的图像"),
            "unassigned": ("未划分", FIF.FOLDER, "未划分 — 还没划进任何 split 的新图像(标注后用「项目设置 → 重新划分」分到 train/val/test)"),
        }
        self._top_items: dict[str, QStandardItem] = {}  # split → top-level item
        self._build_tree_top_level()
        self._populate_image_lists()

        splitter.addWidget(left)

        # ---- 中:画布 + 工具条(用 Fluent CommandBar) ----
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 0, 8, 0)

        from PySide6.QtGui import QAction
        from qfluentwidgets import CommandBar

        self.nav_label = BodyLabel("未选中图像")
        self.draw_action = QAction(FIF.ADD.icon(), "新建框 (N)")
        self.draw_action.setCheckable(True)
        self.draw_action.setToolTip("新建框模式 (快捷键 N)")
        self.draw_action.triggered.connect(self._on_toggle_draw)
        self.delete_action = QAction(FIF.DELETE.icon(), "删除 (Del)")
        self.delete_action.setToolTip("删除选中框 (快捷键 Del)")
        self.delete_action.triggered.connect(self._on_delete_selected)
        self.fit_action = QAction(FIF.FIT_PAGE.icon(), "适应窗口")
        self.fit_action.triggered.connect(self._on_fit)
        self.save_action = QAction(FIF.SAVE.icon(), "保存 (Ctrl+S)")
        self.save_action.setToolTip("保存 (Ctrl+S)")
        self.save_action.triggered.connect(self._save_current)
        self.save_next_action = QAction(FIF.RIGHT_ARROW.icon(), "保存并下一张 (Ctrl+Enter)")
        self.save_next_action.setToolTip("保存当前并跳到下一张 (Ctrl+Enter)")
        self.save_next_action.triggered.connect(self._on_save_next)

        self.ai_annotate_action = QAction(FIF.ROBOT.icon(), "AI 预标注")
        self.ai_annotate_action.setToolTip("使用当前模型预标注此图")
        self.ai_annotate_action.triggered.connect(self._on_ai_annotate_current)

        self.ai_batch_action = QAction(FIF.ALBUM.icon(), "批量 AI 预标注")
        self.ai_batch_action.setToolTip("对未标注图片批量自动预标注")
        self.ai_batch_action.triggered.connect(self._on_ai_annotate_batch)

        self.tips_action = QAction(FIF.QUESTION.icon(), "快捷键")
        self.tips_action.setToolTip("查看快捷键")
        self.tips_action.triggered.connect(self._show_tips)

        self.command_bar = CommandBar()
        self.command_bar.addAction(self.draw_action)
        self.command_bar.addSeparator()
        self.command_bar.addAction(self.delete_action)
        self.command_bar.addAction(self.fit_action)
        self.command_bar.addAction(self.save_action)
        self.command_bar.addAction(self.save_next_action)
        self.command_bar.addSeparator()
        self.command_bar.addAction(self.ai_annotate_action)
        self.command_bar.addAction(self.ai_batch_action)
        self.command_bar.addSeparator()
        self.command_bar.addAction(self.tips_action)
        # 兼容旧代码里对 self.draw_btn 的引用
        self.draw_btn = self.draw_action


        # nav_label 放成独立行 — CommandBar.addWidget 在某些样式下 widget 会被挤压/隐藏
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self.nav_label)
        nav_row.addStretch(1)

        center_layout.addLayout(nav_row)
        center_layout.addWidget(self.command_bar)

        # 画布
        self.canvas = AnnotationCanvas()
        self.canvas.boxesChanged.connect(self._on_boxes_changed)
        self.canvas.modeChanged.connect(self._on_canvas_mode_changed)
        center_layout.addWidget(self.canvas, 1)

        splitter.addWidget(center)

        # ---- 右:类 + boxes 列表 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        # 当前类:互斥 RadioButton 组(取代原来的 ComboBox)
        right_layout.addWidget(StrongBodyLabel("当前类(新建框用)"))
        self._class_button_group = QButtonGroup(self)
        self._class_button_group.setExclusive(True)
        self._class_radios: list[RadioButton] = []  # 与 class_id 同步索引
        self._class_ids: list[int] = []  # _class_radios[i] 对应的 class_id
        self._class_radio_layout = QVBoxLayout()
        right_layout.addLayout(self._class_radio_layout)

        right_layout.addSpacing(8)
        right_layout.addWidget(StrongBodyLabel("提示"))
        hint = CaptionLabel(
            "• 鼠标滚轮:缩放\n"
            "• 中键拖动 / Space+拖动:平移\n"
            "• 点 Draw 按钮进入绘制模式,拖拽出框\n"
            "• Del:删除选中框\n"
            "• 自动保存(改 boxes 即写盘)"
        )
        hint.setWordWrap(True)
        right_layout.addWidget(hint)

        right_layout.addSpacing(16)
        right_layout.addWidget(StrongBodyLabel("标注列表"))
        self.box_list = QListWidget()
        right_layout.addWidget(self.box_list, 1)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)

        # 快捷键
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_current)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_save_next)  # 保存并下一张
        QShortcut(QKeySequence("N"), self, activated=self._on_toggle_draw)  # 切换 Draw 模式
        QShortcut(QKeySequence("["), self, activated=self._goto_prev)
        QShortcut(QKeySequence("]"), self, activated=self._goto_next)
        QShortcut(QKeySequence("D"), self, activated=self._on_delete_selected)
        QShortcut(QKeySequence("C"), self, activated=self._cycle_class)

    # ---- 公开 API ----
    def refresh(self) -> None:
        """重新扫描图像列表(数据集变更后调用)。"""
        self._populate_image_lists()

    def refresh_classes(self, classes: list[ClassDef]) -> None:
        self.project.set_classes(classes)
        self._refresh_class_radios(classes)
        # 把每个类的持久化颜色推给画布(空颜色让画布走默认调色板)
        self.canvas.set_class_color_map(
            {c.class_id: c.color for c in classes if c.color}
        )

    def _refresh_class_radios(self, classes: list[ClassDef]) -> None:
        """重建 RadioButton 列表(类被外部修改时调用)。"""
        # 删旧 radio
        for rb in self._class_radios:
            self._class_button_group.removeButton(rb)
            rb.setParent(None)
            rb.deleteLater()
        self._class_radios.clear()
        self._class_ids.clear()
        # 清空 layout 里所有旧 widget
        while self._class_radio_layout.count():
            item = self._class_radio_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        # 加新的
        from yolo_studio.core.class_config import DEFAULT_CLASS_PALETTE
        for c in classes:
            rb = RadioButton(f"{c.class_id}: {c.name}")
            if c.color:
                rb.setStyleSheet(
                    f"color: {c.color}; font-weight: 600;"
                )
            self._class_button_group.addButton(rb, c.class_id)
            rb.toggled.connect(self._on_class_radio_toggled)
            self._class_radios.append(rb)
            self._class_ids.append(c.class_id)
            self._class_radio_layout.addWidget(rb)
        # 默认选第一个
        if classes:
            self.set_current_class_id(classes[0].class_id)

    def _on_class_radio_toggled(self, checked: bool) -> None:
        if not checked:
            return
        cid = self._class_button_group.checkedId()
        if cid < 0:
            return
        self.canvas.set_current_class_id(cid)
        # 切换类:同步更新所有选中框的类
        self.canvas.cycle_selected_class()

    def set_current_class_id(self, class_id: int) -> None:
        """程序化选中某个 radio(打开图像时把第一个 box 的类同步过去)。"""
        for rb in self._class_radios:
            if self._class_button_group.id(rb) == class_id:
                rb.setChecked(True)
                return

    def open_image(self, path: str) -> None:
        """从外部(DatasetPage 双击)打开一张图。"""
        p = Path(path)
        if not p.exists():
            return
        # 在 TreeView 中定位并选中(展开父节点 + 滚动到该子项)
        self._select_in_tree(p)
        self._open_image(p)

    # ---- 内部 ----
    def _build_tree_top_level(self) -> None:
        """创建 4 个 split 顶层节点(图标 + tooltip + 不可编辑)。"""
        for split in self._splits_order:
            label, icon, tooltip = self._split_meta[split]
            top = QStandardItem(label)
            top.setIcon(icon.icon())
            top.setToolTip(tooltip)
            top.setData(split, Qt.ItemDataRole.UserRole)
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tree_model.appendRow(top)
            self._top_items[split] = top

    def _populate_image_lists(self) -> None:
        """扫描所有 split(按 sha256 去重),填充各 split 的子节点。"""
        # 记录当前选中的图像路径
        selected_path = None
        cur = self.tree.currentIndex()
        if cur.isValid():
            item = self.tree_model.itemFromIndex(cur)
            if item and item.parent():
                selected_path = item.data(Qt.ItemDataRole.UserRole)

        buckets = list_all_images_by_split(self.project)
        try:
            ai_paths = self.db.get_ai_image_paths()
        except Exception:
            ai_paths = set()

        for split, top in self._top_items.items():
            top.removeRows(0, top.rowCount())
            for path, name, has_boxes in buckets.get(split, []):
                p_str = str(path.resolve())
                if has_boxes:
                    if p_str in ai_paths:
                        label = f"- [AI] {name}"
                        tt = f" AI 自动预标注文件: {p_str}"
                    else:
                        label = f"● {name}"
                        tt = f"● 人工标注文件: {p_str}"
                else:
                    label = f"○ {name}"
                    tt = f"○ 未标注文件: {p_str}"

                child = QStandardItem(label)
                child.setData(p_str, Qt.ItemDataRole.UserRole)
                child.setToolTip(tt)
                child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsEditable)
                top.appendRow(child)
        self.tree.expandAll()

        # 恢复选中状态
        if selected_path:
            self._select_in_tree(Path(selected_path))

    def _select_in_tree(self, path: Path) -> None:
        """在 TreeView 中定位 path 对应的子项,展开父节点 + 高亮。"""
        target = str(path.resolve())
        for split, top in self._top_items.items():
            for r in range(top.rowCount()):
                child = top.child(r)
                if child.data(Qt.ItemDataRole.UserRole) == target:
                    idx = self.tree_model.indexFromItem(child)
                    self.tree.scrollTo(idx)
                    self.tree.setCurrentIndex(idx)
                    return

    def _update_tree_item_status(
        self, path: Path, has_boxes: bool, is_ai: bool = False
    ) -> None:
        """更新 TreeView 中指定图像节点的名称和提示，无需重构整个树。"""
        p_str = str(path.resolve())
        name = path.name
        if has_boxes:
            if is_ai:
                label = f"- [AI] {name}"
                tt = f" AI 自动预标注文件: {p_str}"
            else:
                label = f"● {name}"
                tt = f"● 人工标注文件: {p_str}"
        else:
            label = f"○ {name}"
            tt = f"○ 未标注文件: {p_str}"

        for split, top in self._top_items.items():
            for r in range(top.rowCount()):
                child = top.child(r)
                if child and child.data(Qt.ItemDataRole.UserRole) == p_str:
                    if child.text() != label:
                        child.setText(label)
                        child.setToolTip(tt)
                    return

    def _on_image_clicked(self, index) -> None:
        """TreeView 单击回调(只处理子节点;顶层节点不打开图)。"""
        if not index.isValid():
            return
        item = self.tree_model.itemFromIndex(index)
        if item is None or item.parent() is None:
            return  # 顶层 split 节点
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_image(Path(path))

    def _on_image_chosen(self, item) -> None:
        """兼容旧调用(传 item 进来)。"""
        if isinstance(item, QStandardItem):
            if item.parent() is None:
                return
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self._open_image(Path(path))

    def _open_image(self, path: Path) -> None:
        if self._current_image is not None and self._current_image != path:
            # 切换之前，如果有改动才提示已保存
            if self._is_modified:
                boxes = self.canvas.get_boxes()
                split = get_split_for_image(self.project, self._current_image)
                img_id = self.db.upsert_image(str(self._current_image.resolve()))
                if split != "unassigned":
                    self.db.set_split(img_id, split)
                self.db.replace_annotations(
                    img_id,
                    [(b.class_id, b.xc, b.yc, b.w, b.h) for b in boxes],
                )
                if boxes:
                    self.db.set_done(img_id, True)
                else:
                    self.db.set_done(img_id, False)
                self.db.set_is_ai(img_id, False)
                save_boxes_for_image(self.project, split, self._current_image.name, boxes)
                self.db.set_labels_rotated(img_id, True)
                self._update_tree_item_status(self._current_image, has_boxes=bool(boxes), is_ai=False)
                InfoBar.success(
                    title="已自动保存",
                    content=f"已保存 {self._current_image.name} 的标注",
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=1200,
                )

        self._current_image = path
        self._is_modified = False

        # 1. 读取 .txt(.txt 可能在原始(未旋转)坐标空间)
        boxes = read_yolo_txt(self._labels_path_for(path))

        # 2. 检测 EXIF 旋转:如果 DB 里 labels_rotated=0 且 EXIF 要旋转,
        #    变换旧框到新画布空间并标记为"已迁移"。
        from yolo_studio.core.image_utils import load_rotated, transform_boxes

        _, exif_rot = load_rotated(path)
        if exif_rot != 0:
            img_row = self.db.get_image_by_path(str(path.resolve()))
            if img_row and not self.db.get_labels_rotated(img_row.id):
                # 一键迁移 — 旧 .txt 的框换算到旋转后的画布空间
                boxes = transform_boxes(boxes, exif_rot)
                self.db.set_labels_rotated(img_row.id, True)

        # 3. 交给 canvas(boxes 已经在旋转后坐标空间)
        self.canvas.set_image(path, boxes)
        # 高亮 box list
        self._refresh_box_list(boxes)
        # 同步类选择到第一个 box 的类(若有)
        if boxes:
            self.set_current_class_id(boxes[0].class_id)
            self.canvas.set_current_class_id(boxes[0].class_id)
        # 顶部状态
        self.nav_label.setText(f"当前:{path.name}  ({len(boxes)} 框)")

    def _labels_path_for(self, image_path: Path) -> Path:
        split = get_split_for_image(self.project, image_path)
        lbl_dir = {
            "train": self.project.train_labels,
            "val": self.project.val_labels,
            "test": self.project.test_labels,
            "unassigned": self.project.images_dir.parent / "labels",
        }[split]
        return lbl_dir / (image_path.stem + ".txt")

    # ---- 事件 ----
    def _on_boxes_changed(self, boxes: list[Box]) -> None:
        if self._suppress_save or self._current_image is None:
            return
        
        # 标记已修改
        self._is_modified = True
        
        # 自动保存(用户真实修改触发的)
        split = get_split_for_image(self.project, self._current_image)
        # 标 ID upsert + 写 .txt
        img_id = self.db.upsert_image(str(self._current_image.resolve()))
        if split != "unassigned":
            self.db.set_split(img_id, split)
        self.db.replace_annotations(
            img_id,
            [(b.class_id, b.xc, b.yc, b.w, b.h) for b in boxes],
        )
        if boxes:
            self.db.set_done(img_id, True)
        else:
            self.db.set_done(img_id, False)
        # 用户手动修改/确认后，转为人工标注 (is_ai = 0)
        self.db.set_is_ai(img_id, False)
        # 写 .txt
        save_boxes_for_image(self.project, split, self._current_image.name, boxes)
        # 此时 .txt 已在画布坐标系(即 EXIF 旋转后的空间) → 标记 labels_rotated=1
        self.db.set_labels_rotated(img_id, True)
        # 刷新 box list + nav label
        self._refresh_box_list(boxes)
        self.nav_label.setText(f"当前:{self._current_image.name}  ({len(boxes)} 框)")
        # 刷新左侧树节点图标/文字
        self._update_tree_item_status(self._current_image, has_boxes=bool(boxes), is_ai=False)

    def _on_canvas_mode_changed(self, mode: str) -> None:
        if mode == Mode.DRAW.value:
            self.draw_btn.setChecked(True)
        else:
            self.draw_btn.setChecked(False)

    def _on_toggle_draw(self) -> None:
        if self.draw_btn.isChecked():
            self.canvas.set_mode(Mode.DRAW)
        else:
            self.canvas.set_mode(Mode.SELECT)

    def _on_save_next(self) -> None:
        """Ctrl+Enter:保存当前 + 跳到下一张。"""
        self._save_current()
        self._goto_next()

    def _show_tips(self) -> None:
        """CommandBar 上的「快捷键」按钮 → 弹 TeachingTip。"""
        from qfluentwidgets import TeachingTip, TeachingTipView

        view = TeachingTipView(
            title="快捷键提示",
            content=(
                "• 单击左侧图像:打开标注\n"
                "• [/]:上一/下一张\n"
                "• N:切换新建框模式\n"
                "• Del / Backspace:删除选中框\n"
                "• C:把选中框的类改成当前类\n"
                "• 鼠标滚轮:缩放\n"
                "• 中键拖动 / Space+拖动:平移\n"
                "• Ctrl+S:保存\n"
                "• Ctrl+Enter:保存并下一张"
            ),
            icon=FIF.QUESTION,
        )
        # 锚到触发它的 button 控件
        target = getattr(self.command_bar, "actionButton", lambda a: None)(self.tips_action) or self.command_bar
        TeachingTip.make(
            target=target,
            view=view,
            duration=4000,
            parent=self,
        )


    def _on_delete_selected(self) -> None:
        # 通过模拟 Del 键(AnnotationCanvas 监听 Delete/Backspace)
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        self.canvas.keyPressEvent(ev)

    def _on_fit(self) -> None:
        self.canvas.fit_to_view()

    def _cycle_class(self) -> None:
        # 切换类 = 把当前 class_id 应用到所有选中框
        self.canvas.cycle_selected_class()

    def _save_current(self) -> None:
        """手动保存(Ctrl+S)。"""
        if self._current_image:
            boxes = self.canvas.get_boxes()
            split = get_split_for_image(self.project, self._current_image)
            img_id = self.db.upsert_image(str(self._current_image.resolve()))
            if split != "unassigned":
                self.db.set_split(img_id, split)
            self.db.replace_annotations(
                img_id,
                [(b.class_id, b.xc, b.yc, b.w, b.h) for b in boxes],
            )
            if boxes:
                self.db.set_done(img_id, True)
            else:
                self.db.set_done(img_id, False)
            # 用户点击保存，标记为人工标注
            self.db.set_is_ai(img_id, False)
            save_boxes_for_image(self.project, split, self._current_image.name, boxes)
            self.db.set_labels_rotated(img_id, True)
            self._refresh_box_list(boxes)
            self.nav_label.setText(f"当前:{self._current_image.name}  ({len(boxes)} 框)")
            
            # 刷新节点状态
            self._update_tree_item_status(self._current_image, has_boxes=bool(boxes), is_ai=False)

        self._is_modified = False  # 重置修改状态，避免切换时重复弹提示
        InfoBar.success(
            title="已保存",
            content="标注已保存",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=1500,
        )

    def _refresh_box_list(self, boxes: list[Box]) -> None:
        self.box_list.clear()
        names = {c.class_id: c.name for c in self.project.classes}
        for i, b in enumerate(boxes):
            cls_name = names.get(b.class_id, f"id={b.class_id}")
            self.box_list.addItem(
                f"{i+1}. [{cls_name}]  xc={b.xc:.3f} yc={b.yc:.3f}  w={b.w:.3f} h={b.h:.3f}"
            )

    # ---- 导航 ----
    def _goto_prev(self) -> None:
        self._navigate(-1)

    def _goto_next(self) -> None:
        self._navigate(1)

    def _navigate(self, delta: int) -> None:
        """在当前选中的 split 组内上下移动,跨 split 时不切换。"""
        cur = self.tree.currentIndex()
        if not cur.isValid():
            # 没选中 → 选第一个 split 的第一项
            first = self._top_items.get(self._splits_order[0])
            if first and first.rowCount() > 0:
                child_idx = first.child(0).index()
                self.tree.setCurrentIndex(child_idx)
                self._on_image_chosen(first.child(0))
            return
        item = self.tree_model.itemFromIndex(cur)
        if item is None or item.parent() is None:
            return  # 顶层 split 节点
        parent = item.parent()
        row = item.row()
        new_row = max(0, min(parent.rowCount() - 1, row + delta))
        if new_row == row:
            return
        child_idx = parent.child(new_row).index()
        self.tree.setCurrentIndex(child_idx)
        self._on_image_chosen(parent.child(new_row))

    # ---- AI 预标注 ----
    def _on_ai_annotate_current(self) -> None:
        if self._current_image is None or not self._current_image.exists():
            InfoBar.warning(
                title="无法执行预标注",
                content="请先选择一张待标注图像",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        from yolo_studio.ui.widgets.auto_label_dialog import AutoLabelDialog

        dialog = AutoLabelDialog(
            project=self.project,
            title="AI 辅助预标注 (当前图像)",
            is_batch=False,
            parent=self,
        )
        if not dialog.exec():
            return

        model_path = dialog.get_selected_model_path()
        conf = dialog.get_conf()

        if model_path is None or not model_path.exists():
            InfoBar.error(
                title="无可用的模型",
                content="未找到所选的模型文件，请先在「模型」页训练或导入模型",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        try:
            from yolo_studio.core.inference import Predictor, results_to_boxes

            class_name_mapping = {c.name: c.class_id for c in self.project.classes} if self.project.classes else None
            predictor = Predictor(model_path, conf=conf, iou=0.7)
            results = predictor.predict_image(self._current_image)
            boxes = results_to_boxes(results, class_name_mapping=class_name_mapping)

            if not boxes:
                InfoBar.info(
                    title="预标注结果",
                    content="模型未在该图中检测到任何符合阈值的目标",
                    parent=self,
                    position=InfoBarPosition.TOP,
                )
                return

            self._suppress_save = True
            try:
                self.canvas.set_boxes(boxes)
            finally:
                self._suppress_save = False

            split = get_split_for_image(self.project, self._current_image)
            save_boxes_for_image(self.project, split, self._current_image.name, boxes)

            # 标记为 AI 预标注并刷新列表状态
            img_id = self.db.upsert_image(str(self._current_image.resolve()))
            if split != "unassigned":
                self.db.set_split(img_id, split)
            self.db.replace_annotations(
                img_id,
                [(b.class_id, b.xc, b.yc, b.w, b.h) for b in boxes],
            )
            self.db.set_done(img_id, True)
            self.db.set_is_ai(img_id, True)
            self.db.set_labels_rotated(img_id, True)
            self._is_modified = False

            self._refresh_box_list(boxes)
            self._update_tree_item_status(self._current_image, has_boxes=True, is_ai=True)

            InfoBar.success(
                title="AI 预标注完成",
                content=f"使用模型 [{model_path.name}] (conf={conf:.2f}) 成功生成 {len(boxes)} 个目标框",
                parent=self,
                position=InfoBarPosition.TOP,
            )
        except Exception as e:
            InfoBar.error(
                title="预标注失败",
                content=f"{e}",
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _on_ai_annotate_batch(self) -> None:
        from yolo_studio.ui.widgets.auto_label_dialog import AutoLabelDialog

        dialog = AutoLabelDialog(
            project=self.project,
            title="批量 AI 辅助预标注",
            is_batch=True,
            parent=self,
        )
        if not dialog.exec():
            return

        model_path = dialog.get_selected_model_path()
        conf = dialog.get_conf()
        only_unlabeled = dialog.is_only_unlabeled()

        if model_path is None or not model_path.exists():
            InfoBar.error(
                title="无可用的模型",
                content="未找到所选的模型文件，请先在「模型」页训练或导入模型",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        images = list_all_images_by_split(self.project)
        all_image_paths = []
        for s, path_list in images.items():
            for p, name, has_boxes in path_list:
                all_image_paths.append(p)

        if not all_image_paths:
            InfoBar.warning(
                title="无数据",
                content="当前项目的数据集中没有图像文件",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        from qfluentwidgets import StateToolTip
        from yolo_studio.workers.predict_worker import AutoLabelWorker

        self.state_tooltip = StateToolTip("批量 AI 预标注中", "准备启动后台处理线程...", self)
        self.state_tooltip.show()

        self._auto_worker = AutoLabelWorker(
            project=self.project,
            model_path=model_path,
            image_paths=all_image_paths,
            conf=conf,
            only_unlabeled=only_unlabeled,
            parent=self,
        )

        def _on_progress(done, total):
            if hasattr(self, "state_tooltip") and self.state_tooltip:
                self.state_tooltip.setContent(f"正在进行 AI 预标注: {done}/{total}")

        def _on_finished(count):
            if hasattr(self, "state_tooltip") and self.state_tooltip:
                self.state_tooltip.setState(True)
                self.state_tooltip = None
            InfoBar.success(
                title="批量预标注完成",
                content=f"已成功完成批量预标注，新增 {count} 张图的标注文件",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=4000,
            )
            self._populate_image_lists()

        def _on_failed(err):
            if hasattr(self, "state_tooltip") and self.state_tooltip:
                self.state_tooltip.setState(True)
                self.state_tooltip = None
            InfoBar.error(
                title="批量预标注出错",
                content=err.split("\n", 1)[0],
                parent=self,
                position=InfoBarPosition.TOP,
            )

        self._auto_worker.progress.connect(_on_progress)
        self._auto_worker.finished_autolabel.connect(_on_finished)
        self._auto_worker.failed.connect(_on_failed)
        self._auto_worker.start()


