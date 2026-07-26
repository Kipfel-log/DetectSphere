# 🎯 DetectSphere

基于 **YOLOv8 + PySide6 + qfluentwidgets** 的通用目标检测桌面工作台。

一站式完成:数据集浏览 / 手动标注 / AI 辅助标注 / 后台训练(实时曲线) / 单图 / 批量 / 摄像头测试 / `.pt` 与 `.onnx` 导入导出 / Label Studio 集成(可选)。

**适合**:目标检测研究者、CV 工程师、需要小规模自定义检测模型(如工业质检、特定物体识别)的开发者。

## ✨ 主要特性

- 🗂️ **多项目架构** — 每个项目独立目录,自带数据/类定义/模型/SQLite,项目间互不干扰
- 🏷️ **类可编辑** — 在 GUI 里增删改类名、调整 ID,自动写回 `dataset.yaml`
- ✏️ **手动标注** — 拖拽画框 / 删除 / 撤销(D 删、Ctrl+S 存)
- 🤖 **AI 辅助标注** — 用已有模型预标注,人工修正(Phase C)
- 🎓 **后台训练** — QThread 跑 Ultralytics,实时 loss / mAP 曲线,UI 不卡顿
- 📷 **测试 / 推理** — 单图 / 批量 / 摄像头三种模式
- 💾 **模型导入导出** — `.pt` 拷贝 + `.onnx` 导出
- 🔄 **Label Studio 集成**(可选,Phase E)
- 🖼️ **EXIF 自动旋转** — 手机竖屏照片不再侧翻;`.txt` 一次性自动迁移
- 📐 **实时系统监控** — CPU / RAM / 每张 GPU(util% + VRAM%)的圆环显示

## 📁 仓库结构

仓库**只包含工具代码,不含任何用户数据**(数据/模型/训练产物都在 `.gitignore` 中):

```
yolo/
├── app.py                          # 入口
├── launch.py                       # 解释器自动发现(替换 .bat 硬编码路径)
├── 启动 DetectSphere.bat            # Windows 一键启动
├── yolo_studio/                    # 应用包
│   ├── core/                       # 纯逻辑(无 Qt 依赖)
│   │   ├── paths.py / project.py / project_manager.py
│   │   ├── class_config.py / dataset.py / db.py
│   │   ├── image_utils.py           # EXIF 旋转 + 框坐标变换
│   │   ├── model_registry.py
│   │   ├── train.py / export.py / metrics.py / inference.py
│   │   └── io/                      # labels / manifest / formats / ls_client
│   ├── workers/                     # QThread 子类
│   └── ui/                          # 启动器 + FluentWindow + 页面 + widgets
├── projects/                        # 用户运行时数据(.gitignore,首次启动 GUI 创建)
├── requirements.txt
└── install.sh
```

## 🚀 快速开始

### 1. 安装依赖

```bash
# 推荐 conda 环境
conda create -n yolo_studio python=3.11 -y
conda activate yolo_studio

# CUDA 版 PyTorch(可选,但强烈推荐用于训练)
# RTX 4060 + CUDA 12.x:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 项目依赖
pip install -r requirements.txt
```

### 2. 启动应用

```bash
# 推荐:用 launch.py 自动发现解释器
pythonw launch.py

# 或直接启动
python app.py

# Windows 一键启动
启动 DetectSphere.bat
```

### 3. 创建第一个项目

启动后会弹出**项目选择器**:

- 列表为空 → 点 **「新建项目」**
- 填项目名(英文/拼音/任意)→ 选存放位置(默认 `projects/<name>/`)→ 填初始类(每行一个) → 创建

进入主窗口后(侧栏 5 个标签):

1. **数据集** — 浏览/导入图像
2. **标注** — 双击图像 → 拖拽画框(自动保存 `.txt`)
3. **训练**(Phase B) — 配超参 → 后台训练 → 实时曲线 → 自动注册 `best.pt`
4. **模型**(Phase B) — 列表 / 设为活动 / 导出 `.onnx` / 导入外部 `.pt`
5. **项目设置** — 类的增删改 / 数据集划分比例

## ⚙️ 系统要求

- Python 3.10+(开发/验证:3.13.9)
- PyTorch(自动随 ultralytics 安装 CPU 版,生产用 CUDA 版)
- Windows / macOS / Linux(qfluentwidgets 已适配)

## 📜 数据格式

### YOLO 标注(`.txt`)

每张图一个 `.txt`,每行一个目标:
```
<class_id> <x_center> <y_center> <width> <height>
```
所有坐标归一化到 [0, 1]。

### 隐式背景约定

**没有 `.txt` 文件 = 没有目标**(YOLO 标准)。删除所有框时 `.txt` 自动删除,**不会**留空文件。

### `dataset.yaml`

```yaml
path: ../data
train: train/images
val: val/images
test: test/images
nc: 3
names:
  0: cap_closed
  1: cap_on_back
  2: no_cap
```

## 🗓️ 阶段状态

- [x] **Phase A** — 骨架 + 启动器 + 数据集浏览 + 手动标注
- [x] **Phase B** — 训练页 + 后台训练 + 模型注册
- [x] **Phase C** — 测试页 (摄像头/单图/批量) + AI 预标注
- [ ] **Phase D** — 导入/导出 + 项目级 ZIP

- [ ] **Phase E** — 设置 + Label Studio 集成 + 打磨

## 📌 注意事项

- 修改类 ID/顺序会破坏已有 `.txt` 中的 `class_id` 对应关系(GUI 会弹警告)。
- 重新划分数据集前 GUI 会弹确认;原图不会被删,只覆盖 `train/val/test/{images,labels}`。
- 多项目数据/模型互不干扰;切换项目 = 关主窗口 + 重选项目。
- 数据集 EXIF Orientation 自动检测 + `.txt` 一次性迁移;只在首次打开图片时执行,之后幂等。