@echo off
cd /d "%~dp0"

REM Step 1: Incremental OHLCV fetch from yfinance into MySQL ohlc_data table.
REM         Replaces googlefinance.py — no CSV written, no data deleted.
python ingest.py
if errorlevel 1 (
    echo [ERROR] ingest.py failed. Aborting pipeline.
    exit /b 1
)

REM Step 2: Fetch official real NSE India delivery reports into MySQL ohlc_data table
python nse_delivery_fetcher.py 40

REM Step 3: Compute signals on the full dataset (reads from MySQL ohlc_data),
REM         then writes the enriched rows directly into historical_data table.
python test.py
if errorlevel 1 (
    echo [ERROR] test.py failed. Aborting pipeline.
    exit /b 1
)

REM Step 4: Compute Delivery Momentum Signal
python run_delivery_signal.py
if errorlevel 1 (
    echo [ERROR] Delivery Momentum Signal calculation failed.
    exit /b 1
)

echo.
echo Pipeline complete.


