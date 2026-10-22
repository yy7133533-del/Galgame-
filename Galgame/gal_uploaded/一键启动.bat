@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Galgame 一键采集器 - 安装并启动
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请安装 Python 3.10 或更新版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)
echo [1/2] 正在安装所需依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [错误] 依赖安装失败。请检查网络和 Python/pip 安装。
  pause
  exit /b 1
)
echo.
echo [2/2] 正在启动程序...
python galgame_crawler.py
if errorlevel 1 (
  echo.
  echo 程序已退出或启动失败，请查看上方错误信息。
)
pause
