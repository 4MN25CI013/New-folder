@echo off
cd /d "%~dp0"
title Mobile Phone Storage Monitor

echo ===============================================================
echo   Mobile Phone Storage Monitor (On-Screen Notification Only)
echo   ZERO APPS REQUIRED ON PHONE - Silent On-Screen Alerts
echo ===============================================================
echo.
echo Choose an option:
echo   [1] Start continuous monitoring
echo   [2] Run a single check now
echo   [3] Send test alert to phone screen
echo.

set "choice=1"
set /p "choice=Enter choice [1, 2, or 3, default: 1]: "

if "%choice%"=="1" goto opt1
if "%choice%"=="2" goto opt2
if "%choice%"=="3" goto opt3
goto opt1

:opt1
echo.
echo Starting real-time monitor... (Checks every 10 seconds)
echo Press Ctrl+C in this window anytime to stop.
echo.
".venv\Scripts\python.exe" -m backend.mobile --watch
goto end

:opt2
echo.
echo Checking phone storage once...
echo.
".venv\Scripts\python.exe" -m backend.mobile
goto end

:opt3
echo.
echo Sending test alert to your phone screen...
echo.
".venv\Scripts\python.exe" -m backend.mobile --test
goto end

:end
echo.
echo Execution finished.
pause
