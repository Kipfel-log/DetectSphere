# 🎯 YOLO Studio 项目进度

仓库**只包含工具代码**,不含任何用户数据(`projects/` 已在 `.gitignore`)。用户首次启动 GUI 时点"新建项目"创建自己的工作目录。

## ✅ Phase A 完成(2026-07)

### 仓库结构
```
yolo/
├── app.py                          # 入口
├── launch.py                       # 解释器自动发现
├── 启动 YOLO Studio.bat            # Windows 一键启动
├── yolo_studio/                    # 应用包(PySide6 + qfluentwidgets)
│   ├── core/                       # 纯逻辑(无 Qt 依赖)
│   │   ├── paths.py / project.py / project_manager.py
│   │   ├── class_config.py / dataset.py / db.py
│   │   ├── image_utils.py           # EXIF 旋转 + 框坐标变换
│   │   ├── model_registry.py
│   │   ├── train.py / inference.py / metrics.py / export.py
│   │   └── io/{labels,manifest,formats,ls_client}.py
│   ├── workers/                    # QThread 子类
│   │   ├── training_worker.py       # 后台训练(Phase B)
│   │   └── (待补: predict/import/export/ls_sync)
│   └── ui/                         # launcher / main_window / pages / widgets
│       ├── launcher_dialog.py / main_window.py
│       ├── pages/{dataset,annotate,train,model_registry,project_settings,settings}
│       └── widgets/{annotation_canvas,image_grid,training_progress,
│                    log_pane,system_monitor,class_picker,class_editor}
├── projects/                        # 用户运行时数据(.gitignore)
├── requirements.txt
└── install.sh
```

### 环境
- Python 3.13.9(`yolo_test` conda env)
- PyTorch 2.13.0(CPU 版,用户可换 CUDA 版)
- Ultralytics YOLOv8 8.4.102
- **PySide6 6.11.1**(替代原 PyQt6)
- **qfluentwidgets**(PySide6-Fluent-Widgets,Fluent 风格)
- OpenCV 4.13.0
- matplotlib 3.11.1(训练曲线)
- keyring 25.7.0(LS token 加密)
- psutil 7.2.2、nvidia-ml-py3 7.352.0(系统监控)
- Pillow 10+(EXIF 旋转)

### Phase A 功能(已完成)
- ✅ **多项目架构** — `~/.yolo_studio/projects.json` 注册;每个项目独立目录
- ✅ **启动器** — 最近项目 + 新建/打开/浏览
- ✅ **数据集浏览** — 缩略图网格(按 sha256 去重)、按 split 统计
- ✅ **手动标注** — QGraphicsView 画布、拖拽画框、Del 删、Ctrl+S 存
- ✅ **隐式背景** — 无 `.txt` = 无目标,自动删除空 `.txt`
- ✅ **EXIF 自动旋转** — Pillow `ImageOps.exif_transpose`;`labels_rotated` DB 标志幂等迁移
- ✅ **类编辑器** — QTableView 增删改/上下移,应用后写 `dataset.yaml`
- ✅ **数据集划分** — 比例 + 种子可设
- ✅ **SQLite 镜像** — 每项目独立 `project.db`,启动 `rebuild_from_disk` 重建

### Phase B 功能(已完成)
- ✅ **后台训练** — `TrainingWorker(QThread)` + Ultralytics `on_train_epoch_end` 回调
- ✅ **实时曲线** — matplotlib `FigureCanvasQTAgg`,Figure (15, 11) 英寸,4 子图(box_loss / mAP / P-R / cls_loss)
- ✅ **训练指标控件** — ProgressBar(Epoch 进度)+ ProgressRing(Best mAP50)
- ✅ **系统监控** — ProgressRing × (CPU + RAM + 每张 GPU util + 每张 GPU VRAM)
- ✅ **训练日志** — `LogPane` 颜色编码(ERROR 红 / WARN 橙 / INFO 灰);verbosity 下拉(精简/标准/详细)
- ✅ **日志折叠** — 一键隐藏/展开
- ✅ **自动注册** — 训练结束自动 `shutil.copy` `best.pt` → `models/`,写入 `registry.json`
- ✅ **模型注册表页** — 列表 / 设为活动 / 导出 ONNX / 导入外部 / 删除
- ✅ **多 GPU 支持** — 自动枚举 CUDA 设备,生成 2-4 卡组合选项

### Phase B 性能优化
- 标注页图像延迟 fit(viewport 未就绪时挂起,resizeEvent 触发)— 修了首次打开图不显示的 bug
- 缩略图 QThreadPool 异步 + 内存缓存 — 21 张图刷新 < 100ms
- `rebuild_from_disk` 跳过已索引图 sha256(增量启动 < 50ms)

### 修复记录
- `QPixmap: Must construct a QGuiApplication before a QPixmap` — 占位图懒加载
- 标注页图像不显示 — fit_to_view 时机修复(viewport 0×0 时退化变换)
- 训练页 epoch_ring 删除(用户偏好);系统监控左下 + 全 ring;日志折叠 + 详细级别
- 图被压扁 — Figure (14,7) → (15,11) + matplotlib 字号微调
- 双重 EXIF 变换 — DB 加 `labels_rotated` 标志,只在第一次写盘时设置

### 类名(规范)
- `0: cap_closed`(笔盖盖上)
- `1: cap_on_back`(笔盖在笔末)
- `2: no_cap`(没有笔盖)

## ⏳ Phase C 计划

- 测试页:Image / Folder / Camera 三子页
- AI 预标注流程(用现有模型预测,导入标注画布供人工修正)
- 摄像头推理后台线程化 + 实时 FPS

## ⏳ Phase D 计划

- 导入:YOLO/LS-JSON/COCO
- 导出:数据集 zip、ONNX
- 项目级 ZIP 导入导出(跨机器迁移)

## ⏳ Phase E 计划

- 全局设置页(LS URL/token、解释器路径)
- Label Studio 集成(可选,keyring 加密 token)
- README 截图、Polish

---

**当前状态**:Phase A + B 完成。GUI 启动 → 选/建项目 → 5 个页面(数据集/标注/训练/模型/项目设置)全部可用。训练在后线程,UI 不卡顿。