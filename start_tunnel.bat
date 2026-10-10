@echo off
title StockPro - Cloudflare Tunnel
echo ========================================================
echo  Starting Cloudflare Tunnel for StockPro (port 8000)...
echo  Public URL will appear below in a few seconds.
echo  To STOP the tunnel: Press Ctrl+C or run stop_tunnel.bat
echo ========================================================
echo.
.\cloudflared.exe tunnel --url http://localhost:8000
pause
