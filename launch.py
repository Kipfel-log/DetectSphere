"""跨环境启动器。

解决 `启动摄像头检测.bat` 里 conda 路径硬编码的问题。
按以下顺序查找 Python 解释器:
  1. 环境变量 YOLO_STUDIO_PYTHON
  2. 同目录 runtime.json 记录的 python.exe
  3. conda envs 下含 ultralytics 的任意 python.exe
  4. PATH 上的 pythonw.exe / python.exe

找到后把 stdin/stdout/stderr 透传给该解释器执行 app.py。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
RUNTIME_JSON = REPO_ROOT / "runtime.json"


def _conda_envs_root() -> Path | None:
    """返回 conda envs 目录(若存在)。"""
    candidates = [
        Path.home() / ".conda" / "envs",
        Path.home() / "Anaconda3" / "envs",
        Path.home() / "miniconda3" / "envs",
        Path("C:/ProgramData/Anaconda3/envs").resolve(),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _python_in_env_has_ultralytics(env_root: Path) -> bool:
    """探测 env 里的 python.exe 是否装有 ultralytics(不实际 import,只 import 一次确认)。"""
    py = env_root / "python.exe"
    if not py.exists():
        return False
    try:
        # 极快的探测:用 -c 跑 import
        r = subprocess.run(
            [str(py), "-c", "import ultralytics"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def discover_python() -> Path | None:
    """按优先级解析 Python 解释器。"""
    # 1. 环境变量
    env_py = os.environ.get("YOLO_STUDIO_PYTHON")
    if env_py and Path(env_py).exists():
        return Path(env_py)

    # 2. runtime.json
    if RUNTIME_JSON.exists():
        try:
            data = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
            p = Path(data.get("python", ""))
            if p.exists():
                return p
        except Exception:
            pass

    # 3. conda envs 中含 ultralytics 的解释器
    envs = _conda_envs_root()
    if envs:
        # 优先名为 yolo_/yolo 的环境
        for child in sorted(envs.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            if not (child.name.startswith("yolo") or "yolo" in child.name.lower()):
                continue
            if _python_in_env_has_ultralytics(child):
                return child / "python.exe"

        # 其次任何有 ultralytics 的 env
        for child in sorted(envs.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            if _python_in_env_has_ultralytics(child):
                return child / "python.exe"

    # 4. PATH 上的 pythonw.exe / python.exe
    for name in ("pythonw.exe", "python.exe", "python3.exe"):
        p = shutil.which(name)
        if p:
            return Path(p)

    return None


def main() -> int:
    python = discover_python()
    if not python:
        print("[launch.py] ERROR: 没有找到可用的 Python 解释器。", file=sys.stderr)
        print("请设置环境变量 YOLO_STUDIO_PYTHON,或安装带 ultralytics 的 conda 环境。", file=sys.stderr)
        return 1

    # 写回 runtime.json 备用
    try:
        RUNTIME_JSON.write_text(
            json.dumps({"python": str(python)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # 透传参数
    args = [str(python), str(REPO_ROOT / "app.py"), *sys.argv[1:]]
    print(f"[launch.py] using: {python}")
    rc = subprocess.call(args, cwd=str(REPO_ROOT))
    return rc


if __name__ == "__main__":
    sys.exit(main())
