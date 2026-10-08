"""
routers/confluence.py — Signal Confluence Score & Market Breadth

Provides:
  GET /api/confluence/{symbol}  — aggregated signal confluence for a stock
  GET /api/market-breadth       — market-wide breadth indicators
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from auth import get_current_user
from database import get_connection
from routers.dashboard import _get_data_version
import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Analytics"])


# ──────────────────────────────────────────────────────────────
# Signal Confluence Score
# ──────────────────────────────────────────────────────────────

# Each signal group has a label, the columns to check, and whether
# the signal is bullish (+1) or bearish (-1).
_BULLISH_SIGNALS = [
    ("EMA Convergence (4>9>18>50>200)", "convergence_5", 1),
    ("EMA Convergence (4>9>18 + SMA)", "convergence_3", 1),
    ("EMA Convergence (5>9>21>50)", "convergence_4", 1),
    ("Above SMA200", None, 0),  # Custom check
    ("Golden Cross (EMA50>EMA200)", "signal5", 1),
    ("Price above SMA50", None, 0),  # Custom check
    ("RSI in 50-70 zone", None, 0),  # Custom check
    ("High Relative Volume", "High_Relative_Volume_30", 1),
    ("ADX Trend Confirmed", "adx_trigger", 1),
    ("Near 52-Week High", "near_52w_high", 1),
    ("New 52-Week High", "new_52w_high", 1),
    ("VCP Pattern Active", "screen_vcp", 1),
    ("Blue Sky Breakout", "screen_blue_sky", 1),
    ("Breakout Today", "breakout_today", 1),
    ("Stage 2 (Uptrend)", None, 0),  # Custom check
    ("RS Rank > 80", None, 0),  # Custom check
]

_BEARISH_SIGNALS = [
    ("Overbought (RSI>80)", "overbought", -1),
    ("RSI < 30", "rsi_lt_30", -1),
    ("Stage 4 (Downtrend)", None, 0),  # Custom check
    ("Below SMA200", None, 0),  # Custom check
]


@router.get("/confluence/{symbol}")
def get_signal_confluence(
    symbol: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Return a detailed signal confluence analysis for a stock.

    The confluence score (0-100) shows how many bullish vs bearish signals
    are simultaneously active. Higher = stronger bullish setup.

    Returns:
        {
            "symbol": "RELIANCE.NS",
            "score": 72,
            "label": "Strong Bullish Confluence",
            "active_bullish": [...],
            "active_bearish": [...],
            "summary": "8 of 16 bullish signals active, 0 bearish warnings."
        }
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Get the latest row for this symbol
        cursor.execute(
            """
            SELECT *
            FROM historical_data
            WHERE Symbol = %s
            ORDER BY Timestamp DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"No data found for symbol '{symbol}'.")

    # Serialize types
    for k, v in row.items():
        if isinstance(v, Decimal):
            row[k] = float(v)
        elif isinstance(v, (datetime, date)):
            row[k] = str(v)

    active_bullish = []
    active_bearish = []

    close = float(row.get("Close") or 0)
    sma50 = float(row.get("SMA50") or 0) if row.get("SMA50") else None
    sma200 = float(row.get("SMA200") or 0) if row.get("SMA200") else None
    rsi = float(row.get("RSI14") or 0) if row.get("RSI14") else None
    rs_rank = float(row.get("rs_rank") or 0) if row.get("rs_rank") else None
    stage = int(row.get("stage") or 0) if row.get("stage") else None

    # Check bullish signals
    for label, col, _ in _BULLISH_SIGNALS:
        if col and row.get(col) == 1:
            active_bullish.append(label)
        elif label == "Above SMA200" and sma200 and close > sma200:
            active_bullish.append(label)
        elif label == "Price above SMA50" and sma50 and close > sma50:
            active_bullish.append(label)
        elif label == "RSI in 50-70 zone" and rsi and 50 <= rsi <= 70:
            active_bullish.append(label)
        elif label == "Stage 2 (Uptrend)" and stage == 2:
            active_bullish.append(label)
        elif label == "RS Rank > 80" and rs_rank and rs_rank > 80:
            active_bullish.append(label)

    # Check bearish signals
    for label, col, _ in _BEARISH_SIGNALS:
        if col and row.get(col) == 1:
            active_bearish.append(label)
        elif label == "Stage 4 (Downtrend)" and stage == 4:
            active_bearish.append(label)
        elif label == "Below SMA200" and sma200 and close < sma200:
            active_bearish.append(label)

    total_bullish = len(_BULLISH_SIGNALS)
    bull_count = len(active_bullish)
    bear_count = len(active_bearish)

    # Score: base 50, +3 per bullish, -8 per bearish, clamped to 0-100
    raw_score = 50 + (bull_count * 3) - (bear_count * 8)
    score = max(0, min(100, raw_score))

    # Label
    if score >= 80:
        label = "Very Strong Bullish Confluence"
    elif score >= 65:
        label = "Strong Bullish Confluence"
    elif score >= 50:
        label = "Moderate Bullish Confluence"
    elif score >= 35:
        label = "Weak / Neutral"
    else:
        label = "Bearish Caution"

    summary = f"{bull_count} of {total_bullish} bullish signals active, {bear_count} bearish warning{'s' if bear_count != 1 else ''}."

    return JSONResponse(content={
        "symbol": symbol,
        "score": score,
        "label": label,
        "bullish_count": bull_count,
        "bearish_count": bear_count,
        "total_possible_bullish": total_bullish,
        "active_bullish": active_bullish,
        "active_bearish": active_bearish,
        "summary": summary,
        "as_of": str(row.get("Timestamp", "")),
        "key_metrics": {
            "close": close,
            "rsi14": rsi,
            "rs_rank": rs_rank,
            "stage": stage,
            "sma50": sma50,
            "sma200": sma200,
            "stage_bucket": row.get("stage_bucket"),
        },
    })


# ──────────────────────────────────────────────────────────────
# Market Breadth Dashboard
# ──────────────────────────────────────────────────────────────

@router.get("/market-breadth")
def get_market_breadth(current_user: dict = Depends(get_current_user)):
    """
    Returns market-wide breadth indicators:
    - % above SMA50 / SMA200
    - Advance-Decline counts
    - New 52W High vs Low
    - Stage distribution (1/2/3/4)
    - Stage bucket distribution
    - Breadth history for the last 30 trading days (for charting)
    """
    conn = get_connection()
    try:
        data_version = _get_data_version(conn)
    finally:
        conn.close()

    cache_key = "market_breadth_v2"
    cached = cache.get(cache_key, data_version)
    if cached is not None:
        return JSONResponse(content=cached)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Get last 30 trading dates
        cursor.execute(
            "SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT 30"
        )
        trading_dates = [r["Timestamp"] for r in cursor.fetchall()]
        if not trading_dates:
            return JSONResponse(content={"error": "No data available"})

        latest_date = trading_dates[0]
        earliest_date = trading_dates[-1]

        # Latest day snapshot
        cursor.execute(
            """
            SELECT
                COUNT(*) as total_stocks,
                SUM(CASE WHEN Close > SMA50 THEN 1 ELSE 0 END) as above_sma50,
                SUM(CASE WHEN Close > SMA200 THEN 1 ELSE 0 END) as above_sma200,
                SUM(CASE WHEN Close > SMA50 AND SMA50 > SMA200 THEN 1 ELSE 0 END) as strong_uptrend,
                SUM(new_52w_high) as new_highs,
                SUM(new_52w_low) as new_lows,
                SUM(CASE WHEN stage = 1 THEN 1 ELSE 0 END) as stage_1,
                SUM(CASE WHEN stage = 2 THEN 1 ELSE 0 END) as stage_2,
                SUM(CASE WHEN stage = 3 THEN 1 ELSE 0 END) as stage_3,
                SUM(CASE WHEN stage = 4 THEN 1 ELSE 0 END) as stage_4,
                SUM(CASE WHEN stage_bucket = 'forming' THEN 1 ELSE 0 END) as bucket_forming,
                SUM(CASE WHEN stage_bucket = 'fresh_breakout' THEN 1 ELSE 0 END) as bucket_fresh_breakout,
                SUM(CASE WHEN stage_bucket = 'climbing' THEN 1 ELSE 0 END) as bucket_climbing,
                SUM(CASE WHEN stage_bucket = 'played_out' THEN 1 ELSE 0 END) as bucket_played_out,
                SUM(CASE WHEN RSI14 < 30 THEN 1 ELSE 0 END) as rsi_below_30,
                SUM(CASE WHEN RSI14 > 70 THEN 1 ELSE 0 END) as rsi_above_70,
                SUM(breakout_today) as breakouts_today,
                AVG(NULLIF(rs_rank, 0)) as avg_rs_rank
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
              AND Symbol NOT LIKE '%%^%%'
            """,
            (latest_date, latest_date),
        )
        snapshot = cursor.fetchone()

        total = snapshot["total_stocks"] or 1

        # Breadth history for charting (last 30 days)
        cursor.execute(
            """
            SELECT
                DATE(Timestamp) as trade_date,
                COUNT(*) as total,
                SUM(CASE WHEN Close > SMA50 THEN 1 ELSE 0 END) as above_sma50,
                SUM(CASE WHEN Close > SMA200 THEN 1 ELSE 0 END) as above_sma200,
                SUM(new_52w_high) as new_highs,
                SUM(new_52w_low) as new_lows,
                SUM(CASE WHEN stage = 2 THEN 1 ELSE 0 END) as stage_2_count
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp <= %s + INTERVAL 1 DAY
              AND Symbol NOT LIKE '%%^%%'
            GROUP BY DATE(Timestamp)
            ORDER BY trade_date ASC
            """,
            (earliest_date, latest_date),
        )
        history_rows = cursor.fetchall()

    finally:
        cursor.close()
        conn.close()

    # Build history arrays
    history = []
    for hr in history_rows:
        ht = hr["total"] or 1
        history.append({
            "date": hr["trade_date"].isoformat() if hasattr(hr["trade_date"], "isoformat") else str(hr["trade_date"]),
            "pct_above_sma50": round(int(hr["above_sma50"] or 0) / ht * 100, 1),
            "pct_above_sma200": round(int(hr["above_sma200"] or 0) / ht * 100, 1),
            "new_highs": int(hr["new_highs"] or 0),
            "new_lows": int(hr["new_lows"] or 0),
            "pct_stage_2": round(int(hr["stage_2_count"] or 0) / ht * 100, 1),
            "high_low_diff": int(hr["new_highs"] or 0) - int(hr["new_lows"] or 0),
        })

    result = {
        "as_of_date": latest_date.strftime("%Y-%m-%d") if hasattr(latest_date, "strftime") else str(latest_date),
        "total_stocks": int(total),

        "snapshot": {
            "pct_above_sma50": round(int(snapshot["above_sma50"] or 0) / total * 100, 1),
            "pct_above_sma200": round(int(snapshot["above_sma200"] or 0) / total * 100, 1),
            "pct_strong_uptrend": round(int(snapshot["strong_uptrend"] or 0) / total * 100, 1),
            "new_52w_highs": int(snapshot["new_highs"] or 0),
            "new_52w_lows": int(snapshot["new_lows"] or 0),
            "high_low_ratio": round(
                int(snapshot["new_highs"] or 0) / max(int(snapshot["new_lows"] or 0), 1), 2
            ),
            "breakouts_today": int(snapshot["breakouts_today"] or 0),
            "rsi_below_30": int(snapshot["rsi_below_30"] or 0),
            "rsi_above_70": int(snapshot["rsi_above_70"] or 0),
            "avg_rs_rank": round(float(snapshot["avg_rs_rank"] or 0), 1),
        },

        "stage_distribution": {
            "stage_1_accumulation": int(snapshot["stage_1"] or 0),
            "stage_2_uptrend": int(snapshot["stage_2"] or 0),
            "stage_3_distribution": int(snapshot["stage_3"] or 0),
            "stage_4_decline": int(snapshot["stage_4"] or 0),
        },

        "bucket_distribution": {
            "forming": int(snapshot["bucket_forming"] or 0),
            "fresh_breakout": int(snapshot["bucket_fresh_breakout"] or 0),
            "climbing": int(snapshot["bucket_climbing"] or 0),
            "played_out": int(snapshot["bucket_played_out"] or 0),
        },

        "market_regime": _classify_regime(snapshot, total),

        "history": history,
    }

    cache.set(cache_key, result, data_version, ttl=300)
    return JSONResponse(content=result)


def _classify_regime(snapshot: dict, total: int) -> dict:
    """Classify the current market regime based on breadth data."""
    pct_above_200 = int(snapshot["above_sma200"] or 0) / total * 100
    pct_above_50 = int(snapshot["above_sma50"] or 0) / total * 100
    high_low = int(snapshot["new_highs"] or 0) - int(snapshot["new_lows"] or 0)
    stage_2_pct = int(snapshot["stage_2"] or 0) / total * 100

    if pct_above_200 > 70 and pct_above_50 > 60 and stage_2_pct > 40:
        regime = "Strong Bull Market"
        color = "#22c55e"
        advice = "Broad participation. Focus on breakouts and trend-following setups."
    elif pct_above_200 > 50 and pct_above_50 > 40:
        regime = "Moderate Bull Market"
        color = "#84cc16"
        advice = "Selective opportunities. Focus on high RS-rank stocks in Stage 2."
    elif pct_above_200 > 35:
        regime = "Neutral / Transitional"
        color = "#f59e0b"
        advice = "Mixed signals. Reduce position sizing and wait for clearer direction."
    elif pct_above_200 > 20:
        regime = "Weak / Bear Market"
        color = "#ef4444"
        advice = "Most stocks in downtrends. Defensive positioning recommended."
    else:
        regime = "Severe Bear Market"
        color = "#991b1b"
        advice = "Extreme weakness. Cash is king. Watch for capitulation breadth spikes."

    return {"regime": regime, "color": color, "advice": advice}
