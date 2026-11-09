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

REM Step 3: Compute daily indicators & signals incrementally,
REM         writing only today's enriched rows directly into historical_data table.
python incremental_signals.py
if errorlevel 1 (
    echo [ERROR] incremental_signals.py failed. Aborting pipeline.
    exit /b 1
)

REM Step 4: Compute Delivery Momentum Signal
python run_delivery_signal.py
if errorlevel 1 (
    echo [ERROR] Delivery Momentum Signal calculation failed.
    exit /b 1
)

REM Step 5: Compute Pattern Screens & VCP v2.0 Scoring (PCS, APB, MYB, IPO, Trend Resumption)
python update_vcp_data.py --days 14 --workers 10
if errorlevel 1 (
    echo [ERROR] VCP / Pattern screens calculation failed.
    exit /b 1
)

REM Step 6: Compute Market Breadth (% Stocks Above EMAs: 10, 20, 50, 100, 150, 200)
python market_breadth_service.py
if errorlevel 1 (
    echo [ERROR] Market Breadth calculation failed.
    exit /b 1
)

echo.
REM Step 7: Compute Market Strength Pattern Screens (VCP & Cup with Handle Stages Cache)
python market_strength_screens_service.py
if errorlevel 1 (
    echo [ERROR] Market Strength Screens cache generation failed.
    exit /b 1
)

echo Pipeline complete.
