@echo off
echo ====================================================================
echo  Starting Capacity Intelligence (Backend + Frontend + Mobile Monitor)
echo ====================================================================

REM Free port 8000 if an old zombie process was left running
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)

echo [1/3] Starting FastAPI Backend on http://localhost:8000 ...
start "Capacity Intelligence - Backend" cmd /k ""%~dp0.venv\Scripts\python.exe" -m uvicorn backend.app:app --host 0.0.0.0 --port 8000"

echo [2/3] Starting Vite Frontend on http://localhost:5173 ...
start "Capacity Intelligence - Frontend" cmd /k "cd /d "%~dp0frontend" && npm.cmd run dev"

echo [3/3] Starting Mobile Phone Storage Monitor ...
start "Capacity Intelligence - Mobile Monitor" cmd /k ""%~dp0.venv\Scripts\python.exe" -m backend.mobile --watch"

echo.
echo All services are launching in separate CMD windows!
echo - Local UI:       http://localhost:5173
echo - Network UI:     http://10.10.106.107:5173 (for mobile/devices on same Wi-Fi)
echo - Backend API:    http://localhost:8000  (Docs: http://localhost:8000/docs)
echo - Public URL:     Run "share_url.bat" to generate a public HTTPS link
echo - Mobile Monitor: Active in CMD (Checks phone storage every 10s)
echo.

timeout /t 3 >nul
start http://localhost:5173
