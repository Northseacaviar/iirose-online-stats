@echo off
rem ==================================================
rem  iirose 在线人数监测 — 开关
rem  双击一次:未运行则启动;已在运行则停止
rem  (判断依据:本程序专用的 127.0.0.1:8080 端口)
rem ==================================================
title iirose 监测开关

netstat -ano | findstr ":8080" | findstr "LISTENING" >nul
if errorlevel 1 goto START

echo 监测正在运行,正在停止...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8080" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1
echo 已停止。
ping -n 2 127.0.0.1 >nul
exit /b

:START
echo 正在启动监测...
start "" "D:\IIROSEolinestats\.venv\Scripts\pythonw.exe" "D:\IIROSEolinestats\app\run.py"
echo 已启动(后台运行,日志在 app\logs\collector.log)。
ping -n 2 127.0.0.1 >nul
exit /b
