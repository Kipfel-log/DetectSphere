"""AnnotatePage — 标注页。

左侧:图像列表(分 split 标签)
中间:AnnotationCanvas
右侧:当前类(ClassPicker) + 工具按钮 + 当前 boxes 列表

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
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
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
    StrongBodyLabel,
    TitleLabel,
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
from yolo_studio.ui.widgets.class_picker import ClassPicker


class AnnotatePage(QWidget):
    """标注页。"""

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db
        self._current_image: Optional[Path] = None
        self._suppress_save = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ---- 左:图像列表 ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(StrongBodyLabel("图像"))

        self.split_tabs = QTabWidget()
        self.split_tabs.currentChanged.connect(self._on_split_changed)
        left_layout.addWidget(self.split_tabs, 1)

        self._image_lists: dict[str, QListWidget] = {}
        # 分裂点标签 — 顺序、内容、tooltip、图标
        self._splits_order = ["train", "val", "test", "unassigned"]
        split_meta = {
            "train": ("训练 train", FIF.EDIT, "训练集 — 用来训练模型的图像"),
            "val": ("验证 val", FIF.SEARCH, "验证集 — 训练中用来评估模型的图像"),
            "test": ("测试 test", FIF.SEND, "测试集 — 训练完后评估泛化能力的图像"),
            "unassigned": ("未划分", FIF.FOLDER, "未划分 — 还没划进任何 split 的新图像(标注后用「项目设置 → 重新划分」分到 train/val/test)"),
        }
        for split in self._splits_order:
            list_widget = QListWidget()
            list_widget.itemDoubleClicked.connect(self._on_image_chosen)
            self._image_lists[split] = list_widget
            label, icon, tooltip = split_meta[split]
            idx = self.split_tabs.addTab(list_widget, label)
            self.split_tabs.setTabIcon(idx, icon.icon())
            self.split_tabs.setTabToolTip(idx, tooltip)
        self._populate_image_lists()

        splitter.addWidget(left)

        # ---- 中:画布 + 工具条 ----
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(8, 0, 8, 0)

        # 工具条
        toolbar = QHBoxLayout()
        self.nav_label = BodyLabel("未选中图像")
        toolbar.addWidget(self.nav_label)
        toolbar.addStretch(1)

        self.draw_btn = PushButton(FIF.ADD, "新建框(Draw)")
        self.draw_btn.setCheckable(True)
        self.draw_btn.clicked.connect(self._on_toggle_draw)
        toolbar.addWidget(self.draw_btn)

        self.delete_btn = PushButton(FIF.DELETE, "删除选中(Del)")
        self.delete_btn.clicked.connect(self._on_delete_selected)
        toolbar.addWidget(self.delete_btn)

        self.fit_btn = PushButton(FIF.FIT_PAGE, "适应窗口")
        self.fit_btn.clicked.connect(self._on_fit)
        toolbar.addWidget(self.fit_btn)

        self.save_btn = PushButton(FIF.SAVE, "保存(Ctrl+S)")
        self.save_btn.clicked.connect(self._save_current)
        toolbar.addWidget(self.save_btn)

        center_layout.addLayout(toolbar)

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

        right_layout.addWidget(StrongBodyLabel("当前类(新建框用)"))
        self.class_picker = ClassPicker()
        self.class_picker.set_classes(project.classes)
        self.class_picker.classChanged.connect(self._on_class_picker_changed)
        right_layout.addWidget(self.class_picker)

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
        self.class_picker.set_classes(classes)

    def open_image(self, path: str) -> None:
        """从外部(DatasetPage 双击)打开一张图。"""
        p = Path(path)
        if not p.exists():
            return
        # 同步 split tab
        split = get_split_for_image(self.project, p)
        idx = list(self._image_lists.keys()).index(split) if split in self._image_lists else 0
        self.split_tabs.setCurrentIndex(idx)
        # 选中该图
        list_widget = self._image_lists[split]
        for i in range(list_widget.count()):
            if Path(list_widget.item(i).data(Qt.ItemDataRole.UserRole)) == p:
                list_widget.setCurrentRow(i)
                break
        self._open_image(p)

    # ---- 内部 ----
    def _populate_image_lists(self) -> None:
        """扫描所有 split(按 sha256 去重),填充各 split 的列表。"""
        buckets = list_all_images_by_split(self.project)
        for split, list_widget in self._image_lists.items():
            list_widget.clear()
            for path, name, has_boxes in buckets.get(split, []):
                mark = "●" if has_boxes else "○"
                item = QListWidgetItem(f"{mark} {name}")
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                list_widget.addItem(item)

    def _on_split_changed(self, idx: int) -> None:
        # 不自动打开,只更新列表
        pass

    def _on_image_chosen(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        self._open_image(Path(path))

    def _open_image(self, path: Path) -> None:
        # 若有未保存的 boxes 已经在 on_boxes_changed 自动处理
        self._current_image = path

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
            self.class_picker.set_current_class_id(boxes[0].class_id)
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
        # 写 .txt
        save_boxes_for_image(self.project, split, self._current_image.name, boxes)
        # 此时 .txt 已在画布坐标系(即 EXIF 旋转后的空间) → 标记 labels_rotated=1
        self.db.set_labels_rotated(img_id, True)
        # 刷新 box list + nav label
        self._refresh_box_list(boxes)
        self.nav_label.setText(f"当前:{self._current_image.name}  ({len(boxes)} 框)")

    def _on_class_picker_changed(self, class_id: int) -> None:
        self.canvas.set_current_class_id(class_id)
        # 切换类:同步更新所有选中框的类
        self.canvas.cycle_selected_class()

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
        """手动保存(其实已经自动保存了,这里给个提示)。"""
        InfoBar.success(
            title="已保存",
            content="标注已实时保存到 .txt 文件",
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
        idx = self.split_tabs.currentIndex()
        if idx < 0 or idx >= len(self._splits_order):
            return
        split = self._splits_order[idx]
        list_widget = self._image_lists[split]
        row = list_widget.currentRow()
        if row < 0:
            row = 0
        else:
            row = max(0, min(list_widget.count() - 1, row + delta))
        list_widget.setCurrentRow(row)
        item = list_widget.currentItem()
        if item:
            self._open_image(Path(item.data(Qt.ItemDataRole.UserRole)))
