"""DetectSphere 主入口。

启动流程:
  1. 构造 QApplication
  2. while True:
       a. 弹出 LauncherDialog(项目选择/创建)
       b. 用户选定项目 → 打开 MainWindow
       c. 用户关闭 MainWindow:
          - 如果是 X 按钮或退出 → 退出 app
          - 如果是「切换项目」→ 回到启动器
  3. 退出时所有资源清理

推荐运行方式:
  pythonw.exe launch.py         # 跨环境启动器
  python app.py                 # 直接启动
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把仓库根加入 sys.path(让 `import yolo_studio` 可用)
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import setTheme, Theme

from yolo_studio import __app_name__, __org_name__, __version__
from yolo_studio.core.paths import user_state_dir
from yolo_studio.ui.launcher_dialog import LauncherDialog
from yolo_studio.ui.main_window import MainWindow


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

        window = MainWindow(project)
        window.show()
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
