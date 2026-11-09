@echo off
cd /d "%~dp0"
echo ===================================================
echo Starting StockPro Web Application
echo URL: http://localhost:8000
echo ===================================================
python -m uvicorn app:app --reload --port 8000
pause

