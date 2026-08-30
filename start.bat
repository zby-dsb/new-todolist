@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG=start.log"
echo [%DATE% %TIME%] start.bat launched > "%LOG%"

REM ---- locate pythonw, do not rely on system PATH ----
set "PYW="
for %%i in (pythonw.exe) do set "PYW=%%~$PATH:i"
echo [%DATE% %TIME%] PATH pythonw: %PYW% >> "%LOG%"

if not defined PYW (
  if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
)
if not defined PYW (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
)
if not defined PYW (
  if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
)
if not defined PYW (
  for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PYW if exist "%%d\pythonw.exe" set "PYW=%%d\pythonw.exe"
  )
)
if not defined PYW (
  if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" set "PYW=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
)
if not defined PYW (
  if exist "C:\Python313\pythonw.exe" set "PYW=C:\Python313\pythonw.exe"
)
echo [%DATE% %TIME%] resolved pythonw: %PYW% >> "%LOG%"

if not defined PYW (
  call :fatal "Python not found. Install Python 3.x (pythonw.exe) or keep WorkBuddy Python."
  exit /b 1
)

REM ---- already running? ----
if exist server.pid (
  set /p PID=<server.pid
  echo [%DATE% %TIME%] found server.pid PID=%PID% >> "%LOG%"
  tasklist /fi "PID eq %PID%" 2>nul | find "%PID%" >nul
  if not errorlevel 1 (
    echo [%DATE% %TIME%] already running, reopening browser >> "%LOG%"
    call :open_url
    exit /b 0
  )
  del /f server.pid >nul 2>&1
)

REM ---- start hidden ----
echo [%DATE% %TIME%] starting %PYW% app.py >> "%LOG%"
start "" "%PYW%" app.py

REM ---- wait for app.url (max ~30s) ----
set "URL="
for /l %%n in (1,1,30) do (
  if exist app.url (
    set /p URL=<app.url
    echo [%DATE% %TIME%] got app.url: %URL% >> "%LOG%"
    goto :open
  )
  ping -n 2 127.0.0.1 >nul
)

if not defined URL (
  call :fatal "Server did not start. See app.log for details."
  exit /b 1
)

:open
call :open_url
ping -n 3 127.0.0.1 >nul
exit /b 0

:open_url
set "U=http://127.0.0.1:8000/"
if defined URL set "U=%URL%"
echo [%DATE% %TIME%] opening %U% >> "%LOG%"
start "" "%U%"
exit /b 0

:fatal
echo.
echo ERROR: %~1
echo ERROR: %~1 >> "%LOG%"
echo %~1 > start_error.txt
echo MsgBox "%~1", 48, "Daily Todo List" > "%TEMP%\todomsg.vbs"
cscript //nologo "%TEMP%\todomsg.vbs" >nul 2>&1
pause
exit /b 0
