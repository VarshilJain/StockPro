from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from datetime import datetime, date

import pandas as pd

from database import get_connection


def _fetch_symbol_df(symbol: str, lookback_days: int = 250) -> pd.DataFrame:
    conn = get_connection()
    cursor = conn.cursor()
    query = (
        "SELECT Timestamp, Open, High, Low, Close, Volume "
        "FROM historical_data WHERE Symbol = %s ORDER BY Timestamp ASC"
    )
    cursor.execute(query, (symbol,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return pd.DataFrame(columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])  # empty

    df = pd.DataFrame(rows, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
    # Ensure Timestamp is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["Timestamp"]):
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    if lookback_days:
        df = df.iloc[-lookback_days:]
    df = df.reset_index(drop=True)
    return df


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    # SMA
    df["SMA20"] = df["Close"].rolling(window=20, min_periods=20).mean()
    df["SMA50"] = df["Close"].rolling(window=50, min_periods=50).mean()

    # RSI(14)
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(window=14, min_periods=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14, min_periods=14).mean()
    rs = gain / loss
    df["RSI14"] = 100 - (100 / (1 + rs))

    # MACD (12,26,9)
    ema12 = df["Close"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    df["MACD"] = macd
    df["MACD_SIGNAL"] = signal

    # Volume spike vs 20-day average
    df["VOL_MA20"] = df["Volume"].rolling(window=20, min_periods=20).mean()
    df["VOL_SPIKE"] = (df["Volume"] > 1.5 * df["VOL_MA20"]).astype(int)

    return df


import yfinance as yf
import time

_BENCHMARK_CACHE = {}

def _get_benchmark_maps(ticker: str = "^CRSLDX") -> dict:
    global _BENCHMARK_CACHE
    now = time.time()
    if ticker in _BENCHMARK_CACHE and (now - _BENCHMARK_CACHE[ticker]["ts"] < 3600):
        return _BENCHMARK_CACHE[ticker]["maps"]

    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT date, close FROM ohlc_data WHERE ticker = %s ORDER BY date ASC",
            (ticker,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return {}

        df = pd.DataFrame(rows)
        df["close"] = df["close"].astype(float)
        df["Timestamp"] = pd.to_datetime(df["date"])
        df = df.sort_values("Timestamp").reset_index(drop=True)

        maps = {}
        n_pct = df["close"].pct_change(periods=30, fill_method=None)
        maps["RCS_30D"] = dict(zip(df["Timestamp"].dt.date, n_pct))

        _BENCHMARK_CACHE[ticker] = {"maps": maps, "ts": now}
        return maps
    except Exception as exc:
        print(f"Error reading EOD benchmark {ticker} from DB: {exc}")
        return {}


def compute_rcs_for_symbol(df: pd.DataFrame, ticker: str = "^CRSLDX") -> pd.DataFrame:
    if df.empty:
        return df
    maps = _get_benchmark_maps(ticker)
    if not maps or "RCS_30D" not in maps:
        df["RCS_30D"] = None
        return df

    df_dates = pd.to_datetime(df["Timestamp"]).dt.date
    stock_pct = df["Close"].pct_change(periods=30, fill_method=None)
    bench_pct = pd.Series(df_dates).map(maps["RCS_30D"]).values
    rcs_vals = (stock_pct.values - bench_pct) * 100.0
    df["RCS_30D"] = [None if pd.isna(v) else round(float(v), 2) for v in rcs_vals]
    return df


def _latest_row(df: pd.DataFrame) -> Optional[pd.Series]:
    if df.empty:
        return None
    return df.iloc[-1]


def compute_signal_confidence(symbol: str) -> Dict[str, object]:
    """Compute a confidence score (0-100) and label for the latest bar of a symbol.

    Rules (each worth +/- 20):
      + SMA20 > SMA50  => bullish
      + MACD > MACD_SIGNAL => bullish
      + 55 <= RSI14 < 70 => bullish (strong momentum but not overbought)
      + VOL_SPIKE (Volume > 1.5x 20d avg) => bullish

      - SMA20 < SMA50 => bearish
      - MACD < MACD_SIGNAL => bearish
      - RSI14 > 70 => bearish (overbought)
      - RSI14 < 45 => bearish (weak momentum)

    Score = clamp(50 + 20*(bullish_count - bearish_count), 0, 100)
    Label: >=70 Strong Buy, 40-69 Neutral, <40 Weak Sell
    """
    df = _fetch_symbol_df(symbol)
    df = _compute_indicators(df)
    row = _latest_row(df)
    if row is None or any(pd.isna(row.get(x)) for x in ["SMA20", "SMA50", "RSI14", "MACD", "MACD_SIGNAL", "VOL_MA20"]):
        return {"symbol": symbol, "confidence_score": 0, "signal": "Insufficient Data"}

    bullish = 0
    bearish = 0

    if row["SMA20"] > row["SMA50"]:
        bullish += 1
    elif row["SMA20"] < row["SMA50"]:
        bearish += 1

    if row["MACD"] > row["MACD_SIGNAL"]:
        bullish += 1
    elif row["MACD"] < row["MACD_SIGNAL"]:
        bearish += 1

    rsi = row["RSI14"]
    if pd.notna(rsi):
        if 55 <= rsi < 70:
            bullish += 1
        elif rsi >= 70:
            bearish += 1
        elif rsi < 45:
            bearish += 1

    if int(row.get("VOL_SPIKE", 0)) == 1:
        bullish += 1

    raw = 50 + 20 * (bullish - bearish)
    score = max(0, min(100, int(round(raw))))

    if score >= 70:
        label = "Strong Buy"
    elif score >= 40:
        label = "Neutral"
    else:
        label = "Weak Sell"

    return {"symbol": symbol, "confidence_score": score, "signal": label}


def generate_signal_explanation(symbol: str) -> str:
    df = _fetch_symbol_df(symbol)
    df = _compute_indicators(df)
    row = _latest_row(df)
    if row is None:
        return "No data available to explain the signal."

    parts: List[str] = []
    if pd.notna(row.get("SMA20")) and pd.notna(row.get("SMA50")):
        if row["SMA20"] > row["SMA50"]:
            parts.append("20-day moving average is above the 50-day, indicating a bullish short-term trend.")
        elif row["SMA20"] < row["SMA50"]:
            parts.append("20-day moving average is below the 50-day, indicating a bearish short-term trend.")

    if pd.notna(row.get("MACD")) and pd.notna(row.get("MACD_SIGNAL")):
        if row["MACD"] > row["MACD_SIGNAL"]:
            parts.append("MACD is above its signal line, showing positive momentum.")
        else:
            parts.append("MACD is below its signal line, showing weakening momentum.")

    rsi = row.get("RSI14")
    if pd.notna(rsi):
        if rsi > 70:
            parts.append("RSI is above 70, suggesting overbought conditions.")
        elif rsi < 30:
            parts.append("RSI is below 30, suggesting oversold conditions.")
        elif rsi >= 50:
            parts.append("RSI is above 50, indicating improving momentum.")
        else:
            parts.append("RSI is below 50, indicating weaker momentum.")

    if int(row.get("VOL_SPIKE", 0)) == 1:
        parts.append("Volume is significantly higher than average, confirming the move.")

    if not parts:
        return "Insufficient indicator data to generate an explanation."
    return " ".join(parts)





def _get_all_symbols() -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT Symbol FROM historical_data")
    symbols = [r[0] for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return symbols


def _volume_spike_for_latest(symbol: str) -> bool:
    df = _compute_indicators(_fetch_symbol_df(symbol))
    row = _latest_row(df)
    return bool(row is not None and int(row.get("VOL_SPIKE", 0)) == 1)










