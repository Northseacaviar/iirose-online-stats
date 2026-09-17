@echo off
rem iirose 在线状态监测器 —— 本地端启动脚本(双击即可运行)
chcp 65001 >nul
cd /d "%~dp0"
if exist "..\.venv\Scripts\python.exe" (
    ..\.venv\Scripts\python.exe run.py
) else (
    echo 未找到虚拟环境 ..\.venv,请先按 README 安装依赖
)
pause
