@echo off
REM 每日任务清单 · 启动脚本（隐藏运行）
cd /d "%~dp0"

REM 已在运行则不再启动
if exist server.pid (
    for /f %%p in (server.pid) do (
        tasklist /fi "PID eq %%p" 2>nul | find "%%p" >nul && (
            echo 服务已在运行（PID %%p）。如需重启，请先运行 stop.bat。
            pause
            exit /b
        )
    )
    del /f server.pid >nul 2>&1
)

REM pythonw 启动：无控制台窗口，后台隐藏运行，并自动打开浏览器
start "" pythonw app.py
echo 正在启动，浏览器将自动打开……
