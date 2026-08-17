import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import get_current_user, require_admin, verify_csrf
from database import get_connection
from services import (
    compute_signal_confidence,
    generate_signal_explanation,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def convert_date_format(date_str: str) -> str:
    """Convert DD-MM-YYYY to YYYY-MM-DD format with validation."""
    try:
        if not date_str:
            return date_str
        parts = date_str.split('-')
        if len(parts) == 3 and len(parts[0]) <= 2:
            day, month, year = parts
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return date_str
    except Exception:
        return date_str


# 📌 Schema for posting stock data
class StockData(BaseModel):
    Symbol: str = Field(..., max_length=32)
    Timestamp: str = Field(..., max_length=32)  # Format: 'YYYY-MM-DD HH:MM:SS'
    Open: float
    High: float
    Low: float
    Close: float
    Volume: int = Field(..., ge=0)


@router.get("/symbols")
def get_symbols(_: dict = Depends(get_current_user)):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT Symbol FROM historical_data ORDER BY Symbol ASC")
        symbols = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return {"symbols": symbols}
    except Exception as exc:
        logger.exception("Error fetching symbols")
        raise HTTPException(status_code=500, detail="Failed to fetch symbols.")


@router.get("/data")
def get_stock_data(
    symbol: Optional[str] = Query(None, max_length=32),
    start_date: str = Query(...),
    end_date: str = Query(...),
    signal: Optional[str] = Query(None, description="Signal column to filter by"),
    _: dict = Depends(get_current_user),
):
    # Convert date format from DD-MM-YYYY to YYYY-MM-DD
    start_date = convert_date_format(start_date)
    end_date = convert_date_format(end_date)
    
    # Validate date range
    try:
        d_start = datetime.strptime(start_date, "%Y-%m-%d")
        d_end = datetime.strptime(end_date, "%Y-%m-%d")
        if d_start > d_end:
            raise HTTPException(status_code=400, detail="start_date must not be after end_date.")
        if (d_end - d_start).days > 1826:  # Max ~5 years
            raise HTTPException(status_code=400, detail="Requested date range exceeds maximum allowed window of 5 years.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD or DD-MM-YYYY.")

    allowed_signals = [
        "Hammer", "Shooting_Star", "Doji", "Engulfing", "Dark_Cloud_Cover", "Morning_Star", "Evening_Star", "Piercing_Line",
        "signal1", "signal2", "signal3", "signal4", "signal5", "top_decile", "new_52w_high", "new_52w_low", "near_52w_high", "NR", "High_Relative_Volume_30",
        "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70", "adx_trigger",
        "rsi_divergence_type", "rsi_divergence_direction", "rsi_divergence_score", "rsi_divergence",
        "convergence_3", "convergence_4", "convergence_5", "delivery_momentum_signal", "RCS_30D"
    ]
    if signal and signal not in allowed_signals:
        return JSONResponse(content=[])

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        if symbol and signal:
            if signal == "NR":
                query = f"SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND {signal} > 0 ORDER BY Timestamp ASC"
            elif signal in ("rsi_divergence_type", "rsi_divergence_direction", "rsi_divergence_score", "rsi_divergence"):
                query = "SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND `rsi_divergence_type` IS NOT NULL ORDER BY Timestamp ASC"
            elif signal == "RCS_30D":
                query = "SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND `RCS_30D` > 0 ORDER BY Timestamp ASC"
            else:
                query = f"SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND {signal} = 1 ORDER BY Timestamp ASC"
            params = [symbol, start_date, end_date]
        elif symbol:
            query = "SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s ORDER BY Timestamp ASC"
            params = [symbol, start_date, end_date]
        elif signal:
            if signal == "NR":
                query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} > 0 ORDER BY Timestamp ASC"
            elif signal in ("rsi_divergence_type", "rsi_divergence_direction", "rsi_divergence_score", "rsi_divergence"):
                query = "SELECT Symbol, Timestamp, `rsi_divergence_type`, `rsi_divergence_direction`, `rsi_divergence_score` FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND `rsi_divergence_type` IS NOT NULL ORDER BY Timestamp ASC"
            elif signal == "RCS_30D":
                query = "SELECT Symbol, Timestamp, `RCS_30D` FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND `RCS_30D` > 0 ORDER BY Timestamp ASC"
            elif signal == "delivery_momentum_signal":
                query = "SELECT Symbol, Timestamp, `Recent_Deliv_Pct`, `Baseline_Deliv_Pct`, `delivery_momentum_signal` FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND `delivery_momentum_signal` = 1 ORDER BY Timestamp ASC"
            else:
                query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} = 1 ORDER BY Timestamp ASC"
            params = [start_date, end_date]
        else:
            query = "SELECT * FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s ORDER BY Timestamp ASC, Symbol ASC"
            params = [start_date, end_date]

        logger.debug("Executing stock data query for symbol=%s, signal=%s", symbol, signal)
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("Error executing get_stock_data query")
        raise HTTPException(status_code=500, detail="Failed to retrieve stock data.")

    # Remove duplicates based on (Timestamp, Symbol)
    unique_rows = []
    seen = set()
    for row in rows:
        key = (row["Timestamp"], row["Symbol"])
        if key not in seen:
            seen.add(key)
            if not isinstance(row["Timestamp"], str):
                row["Timestamp"] = row["Timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            for k, v in row.items():
                if isinstance(v, Decimal):
                    row[k] = float(v)
                elif isinstance(v, (date, datetime)) and k != "Timestamp":
                    row[k] = str(v)
            unique_rows.append(row)


    # Failsafe: If both symbol and signal, filter out rows where signal doesn't match criteria
    if symbol and signal:
        if signal == "NR":
            unique_rows = [row for row in unique_rows if int(row.get(signal, 0)) > 0]
        elif signal in ("rsi_divergence_type", "rsi_divergence_direction", "rsi_divergence_score", "rsi_divergence"):
            unique_rows = [row for row in unique_rows if row.get("rsi_divergence_type") is not None]
        elif signal == "RCS_30D":
            unique_rows = [row for row in unique_rows if row.get("RCS_30D") is not None and float(row.get("RCS_30D", 0)) > 0]
        else:
            unique_rows = [row for row in unique_rows if str(row.get(signal, 0)) == '1']

    return JSONResponse(content=unique_rows)


# Privileged: POST endpoint to insert canonical stock data (Admin only + CSRF protected)
@router.post("/data")
def add_stock_data(
    data: List[StockData],
    admin_user: dict = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """
    Insert or update historical stock data.
    Restricted strictly to administrators.
    """
    if len(data) > 5000:
        raise HTTPException(
            status_code=400,
            detail="Batch payload exceeds maximum allowed limit of 5000 items per request.",
        )

    conn = get_connection()
    cursor = conn.cursor()

    insert_query = """
        INSERT INTO historical_data (Symbol, Timestamp, Open, High, Low, Close, Volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Open = VALUES(Open),
            High = VALUES(High),
            Low = VALUES(Low),
            Close = VALUES(Close),
            Volume = VALUES(Volume)
    """

    try:
        for entry in data:
            cursor.execute(insert_query, (
                entry.Symbol,
                entry.Timestamp,
                entry.Open,
                entry.High,
                entry.Low,
                entry.Close,
                entry.Volume
            ))
        conn.commit()
        logger.info(
            "SECURITY_EVENT: ADMIN_DATA_WRITE admin_id=%s count=%d",
            admin_user["id"], len(data)
        )
    except Exception as e:
        conn.rollback()
        logger.exception("Error inserting canonical stock data")
        raise HTTPException(status_code=500, detail="Failed to insert/update stock data.")
    finally:
        cursor.close()
        conn.close()

    return {"message": "Stock data inserted/updated successfully"}


@router.get("/signal-scanner")
def get_signal_scanner_data(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    signal: str = Query(...),
    limit: int = Query(1000, ge=1, le=5000, description="Maximum number of rows to return (max 5000)"),
    _: dict = Depends(get_current_user),
):
    """
    Technical Signal Scanner endpoint.
    Returns stocks that have the specified signal = 1 within the date range.
    Delegates to scan_engine.run_scan() so scanning logic lives in one place.
    """
    from scan_engine import ALLOWED_FIELDS, run_scan

    if signal not in ALLOWED_FIELDS:
        return JSONResponse(content=[], status_code=400)

    # Convert DD-MM-YYYY to YYYY-MM-DD if needed
    if start_date:
        start_date = convert_date_format(start_date)
    if end_date:
        end_date = convert_date_format(end_date)

    # NR default: match any NR day (> 0); RCS_30D match all valid rows
    condition = {"field": signal, "operator": "=="}
    if signal == "NR":
        condition = {"field": "NR", "operator": ">", "value": 0}
    elif signal == "RCS_30D":
        condition = {"field": "RCS_30D", "operator": ">", "value": -999.0}
    elif signal == "rsi_divergence_score":
        condition = {"field": "rsi_divergence_score", "operator": ">", "value": 0.0}
    elif signal in ("rsi_divergence_type", "rsi_divergence_direction"):
        condition = {"field": signal, "operator": "IS NOT NULL"}
    elif signal == "rsi_divergence":
        condition = {"field": "rsi_divergence", "operator": "=="}

    try:
        rows = run_scan(
            conditions=[condition],
            logic="AND",
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        logger.exception("Error executing signal scanner for signal=%s", signal)
        return JSONResponse(content=[], status_code=400)

    return JSONResponse(content=rows[:limit])


# Phase 1: Signal Confidence
@router.get("/signal-confidence/{symbol}")
def signal_confidence(symbol: str, _: dict = Depends(get_current_user)):
    try:
        result = compute_signal_confidence(symbol)
        return JSONResponse(content=result)
    except Exception as exc:
        logger.exception("Error computing signal confidence for %s", symbol)
        raise HTTPException(status_code=500, detail="Failed to compute signal confidence.")


# Phase 1: Explain My Signal
@router.get("/explain-signal/{symbol}")
def explain_signal(symbol: str, _: dict = Depends(get_current_user)):
    try:
        text = generate_signal_explanation(symbol)
        return {"symbol": symbol, "explanation": text}
    except Exception as exc:
        logger.exception("Error generating signal explanation for %s", symbol)
        raise HTTPException(status_code=500, detail="Failed to generate signal explanation.")


# =====================
# Feature: Indicator Panel + Candlestick Chart Data
# =====================

@router.get("/indicators/{symbol}")
def get_indicators(symbol: str, _: dict = Depends(get_current_user)):
    """Return latest RSI14, MACD, MACD_SIGNAL, SMA20, SMA50, Close, and RCS 30D (vs NIFTY 500) for the indicator panel."""
    try:
        from services import _fetch_symbol_df, _compute_indicators, compute_rcs_for_symbol
        import pandas as pd
        df = _fetch_symbol_df(symbol, lookback_days=250)
        df = _compute_indicators(df)
        df = compute_rcs_for_symbol(df, ticker="^CRSLDX")
        if df.empty:
            raise HTTPException(status_code=404, detail="No data found for symbol.")
        row = df.iloc[-1]
        def safe(v):
            return None if (v is None or (isinstance(v, float) and pd.isna(v))) else round(float(v), 2)
        return {
            "symbol": symbol,
            "benchmark": "NIFTY 500",
            "close": safe(row.get("Close")),
            "rsi14": safe(row.get("RSI14")),
            "macd": safe(row.get("MACD")),
            "macd_signal": safe(row.get("MACD_SIGNAL")),
            "sma20": safe(row.get("SMA20")),
            "sma50": safe(row.get("SMA50")),
            "vol_spike": bool(int(row.get("VOL_SPIKE", 0)) == 1),
            "rcs_30d": safe(row.get("RCS_30D")),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error fetching indicators for symbol: %s", symbol)
        raise HTTPException(status_code=500, detail="Failed to fetch indicators.")


@router.get("/ohlcv/{symbol}")
def get_ohlcv(
    symbol: str,
    days: int = Query(0, ge=0, le=2520, description="Number of recent days (max 2520 / 10 years), 0 for all"),
    _: dict = Depends(get_current_user)
):
    """Return OHLCV data for candlestick chart rendering."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        fields = (
            "DATE(Timestamp) as date, Open, High, Low, Close, Volume, "
            "RSI14, "
            "Hammer, Shooting_Star, Doji, Engulfing, Dark_Cloud_Cover, Morning_Star, Evening_Star, Piercing_Line, "
            "signal1, signal2, signal3, signal4, signal5, top_decile, new_52w_high, new_52w_low, near_52w_high, NR, High_Relative_Volume_30, "
            "hit_2y_high_14d, hit_5y_high_14d, hit_10y_high_14d, oversold, overbought, rsi_lt_30, rsi_gt_70, adx_trigger, "
            "convergence_3, convergence_4, convergence_5"
        )
        if days > 0:
            cursor.execute(
                f"SELECT {fields} FROM historical_data WHERE Symbol = %s ORDER BY Timestamp DESC LIMIT %s",
                (symbol, days)
            )
        else:
            cursor.execute(
                f"SELECT {fields} FROM historical_data WHERE Symbol = %s ORDER BY Timestamp DESC",
                (symbol,)
            )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        rows = list(reversed(rows))
        result = []
        for r in rows:
            d = r["date"]
            result.append({
                "time": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "open": float(r["Open"]),
                "high": float(r["High"]),
                "low": float(r["Low"]),
                "close": float(r["Close"]),
                "volume": int(r["Volume"]),
                "rsi": float(r["RSI14"]) if r.get("RSI14") is not None else None,
                "signals": {
                    "Hammer": bool(r.get("Hammer") == 1),
                    "Shooting_Star": bool(r.get("Shooting_Star") == 1),
                    "Doji": bool(r.get("Doji") == 1),
                    "Engulfing": bool(r.get("Engulfing") == 1),
                    "Dark_Cloud_Cover": bool(r.get("Dark_Cloud_Cover") == 1),
                    "Morning_Star": bool(r.get("Morning_Star") == 1),
                    "Evening_Star": bool(r.get("Evening_Star") == 1),
                    "Piercing_Line": bool(r.get("Piercing_Line") == 1),
                    "signal1": bool(r.get("signal1") == 1),
                    "signal2": bool(r.get("signal2") == 1),
                    "signal3": bool(r.get("signal3") == 1),
                    "signal4": bool(r.get("signal4") == 1),
                    "signal5": bool(r.get("signal5") == 1),
                    "top_decile": bool(r.get("top_decile") == 1),
                    "new_52w_high": bool(r.get("new_52w_high") == 1),
                    "new_52w_low": bool(r.get("new_52w_low") == 1),
                    "near_52w_high": bool(r.get("near_52w_high") == 1),
                    "NR": bool(r.get("NR", 0) and r.get("NR") > 0),
                    "High_Relative_Volume_30": bool(r.get("High_Relative_Volume_30") == 1),
                    "hit_2y_high_14d": bool(r.get("hit_2y_high_14d") == 1),
                    "hit_5y_high_14d": bool(r.get("hit_5y_high_14d") == 1),
                    "hit_10y_high_14d": bool(r.get("hit_10y_high_14d") == 1),
                    "oversold": bool(r.get("oversold") == 1),
                    "overbought": bool(r.get("overbought") == 1),
                    "rsi_lt_30": bool(r.get("rsi_lt_30") == 1),
                    "rsi_gt_70": bool(r.get("rsi_gt_70") == 1),
                    "adx_trigger": bool(r.get("adx_trigger") == 1),
                    "convergence_3": bool(r.get("convergence_3") == 1),
                    "convergence_4": bool(r.get("convergence_4") == 1),
                    "convergence_5": bool(r.get("convergence_5") == 1),
                }
            })
        return result
    except Exception as exc:
        logger.exception("Error fetching OHLCV for %s", symbol)
        raise HTTPException(status_code=500, detail="Failed to fetch OHLCV data.")

