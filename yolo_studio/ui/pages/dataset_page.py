"""DatasetPage — 数据集浏览页。

左侧:图像缩略图网格(ImageGrid)
右侧:项目概览(类列表、统计、打开项目目录按钮)

双击图像:触发 imageActivated → 主窗口(由调用方)切换到 AnnotatePage 并打开该图。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
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
    StrongBodyLabel,
    TitleLabel,
)

from yolo_studio.core.class_config import ClassDef, load_dataset_yaml, save_dataset_yaml
from yolo_studio.core.db import ProjectDB
from yolo_studio.core.dataset import list_all_images_by_split
from yolo_studio.core.io.labels import has_label_file
from yolo_studio.core.io.manifest import IMAGE_EXTS, _is_image
from yolo_studio.core.project import Project
from yolo_studio.ui.widgets.image_grid import ImageGrid


class DatasetPage(QWidget):
    """数据集浏览页。"""

    imageActivated = Signal(str)  # 路径
    imagesChanged = Signal()  # 图像列表变化(导入/删除/划分)→ 通知 AnnotatePage 刷新

    def __init__(self, project: Project, db: ProjectDB) -> None:
        super().__init__()
        self.project = project
        self.db = db

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧:工具栏 + 网格
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self.title_label = TitleLabel("数据集")
        toolbar.addWidget(self.title_label)
        toolbar.addStretch(1)

        self.import_btn = PushButton(FIF.ADD, "导入图像")
        self.import_btn.clicked.connect(self._on_import_images)
        toolbar.addWidget(self.import_btn)

        self.refresh_btn = PushButton(FIF.SYNC, "刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)

        left_layout.addLayout(toolbar)

        self.grid = ImageGrid()
        self.grid.imageActivated.connect(self.imageActivated)
        left_layout.addWidget(self.grid, 1)

        splitter.addWidget(left)

        # 右侧:项目概览
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)

        right_layout.addWidget(StrongBodyLabel("项目概览"))
        right_layout.addSpacing(8)
        self.summary_label = BodyLabel("")
        self.summary_label.setWordWrap(True)
        right_layout.addWidget(self.summary_label)

        right_layout.addSpacing(16)
        right_layout.addWidget(StrongBodyLabel("类别(只读)"))
        self.class_list = QListWidget()
        self.class_list.setMaximumHeight(200)
        right_layout.addWidget(self.class_list)

        right_layout.addSpacing(16)
        open_btn = PushButton(FIF.FOLDER, "打开项目目录")
        open_btn.clicked.connect(self._on_open_project_dir)
        right_layout.addWidget(open_btn)

        right_layout.addStretch(1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        # 初始填充
        self.refresh()

    # ---- 公开 API ----
    def refresh(self, *, full: bool = False) -> None:
        """扫描文件系统,刷新网格和概览。

        full=False(默认):只扫文件 + UI 更新,跳过 annotation 同步(快,~10ms)
        full=True:完整重建(从 .txt 同步标注到 DB,启动时或怀疑 DB 不一致时用)

        点击「刷新」按钮:轻量版 — 用来快速看到新文件/新划分。
        启动时 MainWindow 用完整版。
        """
        import time

        t0 = time.perf_counter()

        # 重新读 dataset.yaml(类可能被改了)
        classes = load_dataset_yaml(self.project.dataset_yaml)
        self.project.set_classes(classes)
        self._refresh_class_list(classes)

        # 收集所有图像(按 split 分组,按 (size,mtime) 指纹去重)
        buckets = list_all_images_by_split(self.project)
        all_images: list[tuple[Path, bool, str]] = []
        for split in ("train", "val", "test", "unassigned"):
            for path, _name, has in buckets.get(split, []):
                all_images.append((path, has, split))

        if full:
            # 完整模式:从 .txt 同步所有标注到 DB(慢,只在启动时调用)
            from yolo_studio.core.io.manifest import rebuild_from_disk
            rebuild_from_disk(self.project, self.db)
        else:
            # 轻量模式:只对 DB 中不存在的图 upsert(算 sha256)
            for path, _has, split in all_images:
                path_str = str(path.resolve())
                row = self.db.get_image_by_path(path_str)
                if row is None:
                    import hashlib

                    sha = hashlib.sha256(path.read_bytes()).hexdigest()
                    img_id = self.db.upsert_image(path_str, sha)
                    if split != "unassigned":
                        self.db.set_split(img_id, split)

        # 更新网格(缩略图是主要耗时点 — 在主线程做小数据集 OK)
        self.grid.set_images([(p, has) for p, has, _ in all_images])
        self._refresh_summary(all_images, classes)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # 收尾提示
        total = len(all_images)
        labeled = sum(1 for _, has, _ in all_images if has)
        InfoBar.success(
            title="刷新完成",
            content=f"{total} 张图({labeled} 已标注) · {elapsed_ms:.0f} ms",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000,
        )

        # 通知其他页面(AnnotatePage)列表变了
        self.imagesChanged.emit()

    def refresh_classes(self, classes: list[ClassDef]) -> None:
        """类被外部修改后调用,刷新右侧类列表 + 概览。"""
        self.project.set_classes(classes)
        self._refresh_class_list(classes)
        # 概览数字不变,无需重扫

    # ---- 内部 ----
    def _refresh_class_list(self, classes: list[ClassDef]) -> None:
        self.class_list.clear()
        for c in classes:
            item = QListWidgetItem(f"{c.class_id}: {c.name}")
            self.class_list.addItem(item)

    def _refresh_summary(self, all_images, classes) -> None:
        by_split: dict[str, int] = {}
        labeled = 0
        for _, has, split in all_images:
            by_split[split] = by_split.get(split, 0) + 1
            if has:
                labeled += 1
        total = sum(by_split.values())

        lines = [
            f"项目路径:{self.project.root}",
            f"图像总数:{total}",
            f"已标注:{labeled}",
            f"未标注:{total - labeled}",
            "",
            "按划分:",
            f"  训练 (train):{by_split.get('train', 0)}",
            f"  验证 (val):{by_split.get('val', 0)}",
            f"  测试 (test):{by_split.get('test', 0)}",
            f"  未划分:{by_split.get('unassigned', 0)}",
            "",
            f"类别数:{len(classes)}",
        ]
        self.summary_label.setText("\n".join(lines))

    # ---- 操作 ----
    def _on_import_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图像文件",
            str(self.project.images_dir),
            "Images (*.jpg *.jpeg *.png *.bmp *.webp)",
        )
        if not files:
            return
        target = self.project.images_dir
        target.mkdir(parents=True, exist_ok=True)
        import shutil
        n = 0
        for f in files:
            src = Path(f)
            dst = target / src.name
            if dst.exists():
                # 避免覆盖 → 加后缀
                i = 1
                while (target / f"{src.stem}_{i}{src.suffix}").exists():
                    i += 1
                dst = target / f"{src.stem}_{i}{src.suffix}"
            shutil.copy2(src, dst)
            n += 1
        InfoBar.success(
            title="导入完成",
            content=f"已导入 {n} 张图像到 data/images/",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self.refresh()

    def _on_open_project_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root)))
