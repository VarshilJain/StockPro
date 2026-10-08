@echo off
echo Starting StockPro Web Server...
python -m uvicorn app:app --reload --port 8000
pause
