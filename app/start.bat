@echo off
rem iirose 在线状态监测器 —— 手动启动脚本(双击静默启动,无窗口)
rem 服务启动后常驻;停止服务:任务管理器结束 pythonw.exe,或让我帮你停
chcp 65001 >nul
cd /d "%~dp0"
if exist "..\.venv\Scripts\pythonw.exe" (
    start "" "..\.venv\Scripts\pythonw.exe" run.py
    echo 已启动(后台静默运行),日志见 logs\collector.log
) else (
    echo 未找到虚拟环境 ..\.venv,请先按 README 安装依赖
)
timeout /t 3 >nul
