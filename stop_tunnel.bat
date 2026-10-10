@echo off
title StockPro - Stop Tunnel
echo ========================================================
echo  Stopping Cloudflare Tunnel...
echo ========================================================
taskkill /IM cloudflared.exe /F >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Cloudflare tunnel has been stopped successfully.
) else (
    echo [INFO] No running cloudflared process was found.
)
timeout /t 3 >nul
