@echo off
cd /d "%~dp0"
title Share Project - Public URL Generator

echo ====================================================================
echo             Smart Thinkers - Project URL Generator
echo ====================================================================
echo.
echo Make sure your app is running (via run.bat) before creating a public URL!
echo.
echo Select an option to generate a URL:
echo.
echo   [1] Instant Public URL via Pinggy (No installation required, uses Windows SSH)
echo   [2] Instant Public URL via LocalTunnel (Node.js npx)
echo   [3] Instant Public URL via Cloudflare Tunnel (if cloudflared is installed)
echo   [4] View Local and Wi-Fi Network URLs (LAN / Mobile on same Wi-Fi)
echo.

set "opt=1"
set /p "opt=Enter your choice [1-4, default: 1]: "

if "%opt%"=="1" goto pinggy
if "%opt%"=="2" goto localtunnel
if "%opt%"=="3" goto cloudflare
if "%opt%"=="4" goto localnetwork
goto pinggy

:pinggy
echo.
echo --------------------------------------------------------------------
echo Connecting to Pinggy tunnel on port 5173...
echo Your public HTTPS URL will appear below:
echo --------------------------------------------------------------------
echo.
ssh -o StrictHostKeyChecking=no -p 443 -R0:localhost:5173 a.pinggy.io
goto end

:localtunnel
echo.
echo --------------------------------------------------------------------
echo Starting LocalTunnel on port 5173...
echo (If prompted to enter password, provide your public IP: check at ipify.org)
echo --------------------------------------------------------------------
echo.
call npx.cmd localtunnel --port 5173
goto end

:cloudflare
echo.
echo --------------------------------------------------------------------
echo Starting Cloudflare Tunnel on port 5173...
echo --------------------------------------------------------------------
echo.
cloudflared tunnel --url http://localhost:5173
if %errorlevel% neq 0 (
    echo.
    echo cloudflared is not installed. You can install it via:
    echo winget install Cloudflare.cloudflared
)
goto end

:localnetwork
echo.
echo --------------------------------------------------------------------
echo Local and Network URLs:
echo --------------------------------------------------------------------
echo.
echo [Local Machine]
echo - Frontend UI:       http://localhost:5173
echo - Backend API:       http://localhost:8000
echo - Swagger Docs:      http://localhost:8000/docs
echo.
echo [Local Wi-Fi Network (For mobile / other PCs on same Wi-Fi)]
for /f "tokens=4" %%a in ('route print ^| findstr 0.0.0.0 ^| findstr /v "0.0.0.0.*0.0.0.0"') do (
    echo - Mobile / LAN URL:  http://%%a:5173
)
echo.
echo Note: If other devices on your Wi-Fi cannot connect, allow port 5173
echo in Windows Defender Firewall.
echo.
goto end

:end
echo.
pause

