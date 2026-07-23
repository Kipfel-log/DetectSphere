"""LogPane — 只追加日志的 QPlainTextEdit(带 INFO/WARN/ERROR 颜色)。

自动滚动到底部;可清空;行数上限(防内存膨胀)。
"""
from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PushButton


_MAX_LINES = 5000


# Verbosity 等级:
#   verbose   — 不过滤,显示所有 INFO
#   standard  — 过滤掉特别啰嗦的 Ultralytics 内部行(`[34m[<module>:` 这种带 ANSI 转义的)
#   minimal   — 只显示含 epoch / loss / mAP / 错误 / 完成 / 启动 等关键词
_KEEP_KEYWORDS = (
    "epoch",
    "loss",
    "map",
    "mAP",
    "started",
    "starting",
    "完成",
    "fail",
    "error",
    "warning",
    "[!]",
    "stop",
    "best",
    "all",
    "validated",
)


def _should_show(line: str, level: str) -> bool:
    if level == "verbose":
        return True
    lower = line.lower()
    if level == "minimal":
        return any(kw in lower for kw in _KEEP_KEYWORDS)
    # standard:过滤带复杂 ANSI 转义 / dataloader worker 详情等
    if "[\x1b[" in line:
        # 带 ANSI 颜色码的 Ultralytics 进度条行
        return any(kw in lower for kw in _KEEP_KEYWORDS)
    return True


class LogPane(QWidget):
    """只追加日志窗格。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = "standard"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        self.clear_btn = PushButton("清空")
        self.clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self.clear_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # 文本框
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(_MAX_LINES)
        font = QFont("Consolas" if _is_windows() else "Menlo", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text.setFont(font)
        layout.addWidget(self.text, 1)

    @Slot(str)
    def append(self, line: str) -> None:
        """追加一条日志,根据内容上色(简单启发式)+ verbosity 过滤。"""
        if not _should_show(line, self._level):
            return

        if not line.endswith("\n"):
            line_full = line + "\n"
        else:
            line_full = line

        # 颜色
        lower = line.lower()
        if "[error]" in lower or "traceback" in lower or "exception" in lower:
            color = QColor("#ff6b6b")
        elif "[warn" in lower or "warning" in lower:
            color = QColor("#ffa500")
        else:
            color = QColor("#dddddd")

        self.text.setCurrentCharFormat(_colored_format(color))
        self.text.insertPlainText(line_full)
        # 自动滚动到底
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    @Slot()
    def clear(self) -> None:
        self.text.clear()

    def set_level(self, level: str) -> None:
        """设置详细级别:minimal / standard / verbose。

        注:只影响后续 append 的过滤,不清空已显示的内容。
        """
        if level not in ("minimal", "standard", "verbose"):
            return
        self._level = level


def _colored_format(color: QColor):
    from PySide6.QtGui import QTextCharFormat

    fmt = QTextCharFormat()
    fmt.setForeground(color)
    return fmt


def _is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")
