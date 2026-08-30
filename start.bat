@echo off
REM Daily Todo List - launcher (hidden). Does NOT rely on system PATH.
cd /d "%~dp0"

REM ---------- 1. locate pythonw ----------
set "PYW="
REM 1a. PATH (in case it is ever available)
for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYW set "PYW=%%i"
)
REM 1b. WorkBuddy managed Python (version-agnostic glob)
if not defined PYW (
    for /f "delims=" %%i in ('dir /b /s "%USERPROFILE%\.workbuddy\binaries\python\pythonw.exe" 2^>nul') do (
        if not defined PYW set "PYW=%%i"
    )
)
REM 1c. common system Python installs
if not defined PYW (
    for %%d in (
        "C:\Python313\pythonw.exe"
        "C:\Python312\pythonw.exe"
        "C:\Python311\pythonw.exe"
        "%LOCALAPPDATA%\Programs\Python\pythonw.exe"
    ) do (
        if not defined PYW if exist %%d set "PYW=%%d"
    )
)
if not defined PYW (
    echo [ERROR] Cannot find Python (pythonw.exe).
    echo Please install Python, or make sure WorkBuddy's Python is intact.
    pause
    exit /b 1
)

REM ---------- 2. do not start if already running ----------
if exist server.pid (
    set /p PID=<server.pid
    if defined PID (
        echo %PID% | findstr /r "[0-9]" >nul && (
            tasklist /fi "PID eq %PID%" 2>nul | find "%PID%" >nul && (
                echo Service already running (PID %PID%). Opening browser...
                if exist app.url (
                    set /p URL=<app.url
                    if defined URL start "" %URL%
                ) else (
                    start "" http://127.0.0.1:8000/
                )
                pause
                exit /b
            )
        )
    )
    del /f server.pid >nul 2>&1
)

REM ---------- 3. start hidden ----------
echo Starting Daily Todo List...
start "" "%PYW%" app.py

REM ---------- 4. verify it came up ----------
set "UP="
for /l %%n in (1,1,20) do (
    if exist server.pid (
        for /f %%p in (server.pid) do (
            tasklist /fi "PID eq %%p" 2>nul | find "%%p" >nul && set "UP=1"
        )
    )
    if defined UP goto :launched
    ping -n 2 127.0.0.1 >nul
)
:launched
if not defined UP (
    echo [ERROR] Service failed to start. See app.log for details.
    pause
    exit /b 1
)
echo Started. Your browser should open the task list automatically.
ping -n 3 127.0.0.1 >nul
