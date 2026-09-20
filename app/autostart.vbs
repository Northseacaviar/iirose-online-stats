' iirose 在线人数监测 —— 开机自启(无窗口,静默运行)
' 原理:本文件位于用户 Startup 启动文件夹,登录时由系统执行。
' 取消自启:删除启动文件夹里的副本(Startup\iirose-online-stats.vbs)即可。
CreateObject("Wscript.Shell").Run """D:\IIROSEolinestats\.venv\Scripts\pythonw.exe"" ""D:\IIROSEolinestats\app\run.py""", 0, False
