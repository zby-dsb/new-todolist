@echo off
REM 每日任务清单 · 停止脚本
cd /d "%~dp0"

if not exist server.pid (
    echo 未找到 server.pid，服务可能未运行。
    pause
    exit /b
)

for /f %%p in (server.pid) do (
    taskkill /PID %%p /F >nul 2>&1
)
del /f server.pid >nul 2>&1
echo 已停止服务。
pause
