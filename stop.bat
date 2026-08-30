@echo off
cd /d "%~dp0"

if not exist server.pid (
    echo No server.pid found. The service may not be running.
    pause
    exit /b
)

for /f %%p in (server.pid) do (
    taskkill /PID %%p /F >nul 2>&1
)
del /f server.pid >nul 2>&1
echo Service stopped.
pause
