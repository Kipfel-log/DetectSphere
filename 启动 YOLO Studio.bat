@echo off
REM YOLO Studio 一键启动
REM launch.py 会自动发现带 ultralytics 的 Python 解释器

echo ========================================
echo   YOLO Studio
echo ========================================
echo.

REM 优先用 launch.py(自动发现 Python 解释器)
pythonw launch.py
if errorlevel 1 (
  echo.
  echo [fallback] launch.py 失败,尝试直接启动 app.py
  pythonw app.py
)

pause
