"""DetectSphere 主入口。

启动流程:
  1. 构造 QApplication
  2. while True:
       a. 弹出 LauncherDialog(项目选择/创建)
       b. 用户选定项目 → 瞬间弹出独立无边框 Loading 窗口 (无关闭/最小/最大按钮，左上角标头 DetectSphere)
       c. 后台 ProjectLoaderWorker QThread 异步加载项目与 DB，Spinner 动画保持 60 FPS 绝对流畅
       d. 数据就绪后，直接全屏打开 MainWindow 并销毁 Loading 窗口
       e. 用户关闭 MainWindow:
          - 如果是 X 按钮或退出 → 退出 app
          - 如果是「切换项目」→ 回到启动器
  3. 退出时所有资源清理
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把仓库根加入 sys.path(让 `import yolo_studio` 可用)
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import Theme, setTheme

from yolo_studio import __app_name__, __org_name__, __version__
from yolo_studio.core.paths import user_state_dir
from yolo_studio.ui.launcher_dialog import LauncherDialog
from yolo_studio.ui.widgets.loading_window import LoadingWindow
from yolo_studio.workers.project_loader_worker import LoadedProjectData, ProjectLoaderWorker

# MainWindow will be imported lazily after loading finishes to avoid UI freeze


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__org_name__)

    setTheme(Theme.LIGHT)

    user_state_dir().mkdir(parents=True, exist_ok=True)

    # 启动器 ↔ 主窗口 循环
    while True:
        launcher = LauncherDialog()
        project = launcher.run()  # 阻塞,直到用户选定或取消
        if project is None:
            return 0

        # 1. 弹出独立无边框 Loading 窗口 (无关闭/最小/最大按钮，左上角 DetectSphere)
        loading_win = LoadingWindow(project.name)
        loading_win.show()
        QApplication.processEvents()

        # 2. 启动后台异步线程加载数据，使 Spinner 维持 60 FPS 极度流畅
        loader = ProjectLoaderWorker(project)
        loader.status_changed.connect(loading_win.set_status)

        loop = QEventLoop()
        loaded_data_container: dict[str, LoadedProjectData] = {}

        def _on_finished(data: LoadedProjectData):
            loaded_data_container['data'] = data
            loop.quit()

        def _on_failed(err: str):
            loading_win.set_status(f"加载失败: {err.split('\n', 1)[0]}")
            loop.quit()

        loader.finished_loading.connect(_on_finished)
        loader.failed_loading.connect(_on_failed)
        loader.start()

        loop.exec()

        loaded_data = loaded_data_container.get('data', None)

        # 3. 数据就绪 → 构造 MainWindow (分帧异步，LoadingWindow 继续显示)
        from yolo_studio.ui.main_window import MainWindow
        loading_win.set_status("正在构建主界面...")

        window = MainWindow(project, loaded_data)
        # MainWindow 页面分帧构造期间，把状态更新转发到 LoadingWindow
        window.status_changed.connect(loading_win.set_status)

        # 使用第二个 QEventLoop 等待 MainWindow 所有页面构造完毕
        ready_loop = QEventLoop()

        def _on_ready():
            window.status_changed.disconnect(loading_win.set_status)
            window.showMaximized()
            loading_win.close()
            loading_win.deleteLater()
            ready_loop.quit()

        window.ready.connect(_on_ready)
        ready_loop.exec()

        del launcher

        rc = app.exec()
        switching = bool(getattr(window, "_switching_project", False))
        window.deleteLater()
        if switching:
            # MainWindow 用户点了"切换项目"→ 回到启动器
            continue
        return rc


if __name__ == "__main__":
    sys.exit(main())
