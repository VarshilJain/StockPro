from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from datetime import datetime, date

import pandas as pd
import numpy as np

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
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
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
    df["RSI14"] = compute_rsi(df["Close"], 14)

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


def compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
    close_series = pd.Series(close_series).astype(float)
    if len(close_series) <= period:
        return pd.Series(np.nan, index=close_series.index)
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    seed_gain = gain.rolling(window=period, min_periods=period).mean()
    seed_loss = loss.rolling(window=period, min_periods=period).mean()
    w_gain = pd.Series(np.nan, index=close_series.index, dtype=float)
    w_loss = pd.Series(np.nan, index=close_series.index, dtype=float)
    w_gain.iloc[period] = seed_gain.iloc[period]
    w_loss.iloc[period] = seed_loss.iloc[period]
    w_gain.iloc[period+1:] = gain.iloc[period+1:]
    w_loss.iloc[period+1:] = loss.iloc[period+1:]
    avg_gain = w_gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = w_loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def detect_swing_points(df: pd.DataFrame, left: int = 4, right: int = 4, min_spacing: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    lows = []
    highs = []
    
    n = len(df)
    if n <= left + right:
        return [], []
        
    low_vals = df["Low"].values
    high_vals = df["High"].values
    
    for i in range(left, n - right):
        val_low = low_vals[i]
        if val_low == np.min(low_vals[i-left : i+right+1]):
            lows.append((i, val_low))
            
        val_high = high_vals[i]
        if val_high == np.max(high_vals[i-left : i+right+1]):
            highs.append((i, val_high))
            
    def dedup(pivots, is_low):
        if not pivots:
            return []
        accepted = [pivots[0]]
        for cand in pivots[1:]:
            last = accepted[-1]
            if cand[0] - last[0] <= min_spacing:
                if is_low:
                    if cand[1] < last[1]:
                        accepted[-1] = cand
                else:
                    if cand[1] > last[1]:
                        accepted[-1] = cand
            else:
                accepted.append(cand)
        return accepted

    return dedup(lows, is_low=True), dedup(highs, is_low=False)


def detect_rsi_divergence(
    df: pd.DataFrame,
    rsi_col: str = "RSI14",
    lookback: int = 80,
    left: int = 4,
    right: int = 4,
    min_spacing: int = 5,
    min_price_change_pct: float = 1.5,
    min_rsi_change: float = 3.0,
    bearish_rsi_floor: float = 60.0,
    bullish_rsi_ceil: float = 40.0,
    max_score: float = 10.0,
    use_zone_filters: bool = True
) -> List[Dict]:
    if len(df) < lookback or rsi_col not in df.columns:
        return []

    # Ensure price and indicator columns are floats to avoid TypeError with Decimal objects
    df = df.copy()
    for col in ["Open", "High", "Low", "Close", "Volume", rsi_col]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    lows, highs = detect_swing_points(df, left=left, right=right, min_spacing=min_spacing)
    
    n = len(df)
    lookback_start = n - lookback
    lows = [p for p in lows if p[0] >= lookback_start]
    highs = [p for p in highs if p[0] >= lookback_start]
    
    rsi_vals = df[rsi_col].values
    
    def get_price_change_pct(pA, pB):
        return abs(pB[1] - pA[1]) / pA[1] * 100
        
    def get_rsi_change(idxA, idxB):
        return abs(rsi_vals[idxB] - rsi_vals[idxA])
        
    def calculate_score(p_first, p_last, chain_weight):
        price_delta_pct = abs(p_last[1] - p_first[1]) / p_first[1] * 100
        rsi_delta = abs(rsi_vals[p_last[0]] - rsi_vals[p_first[0]])
        safe_price_delta = max(price_delta_pct, min_price_change_pct)
        raw_score = chain_weight * (rsi_delta / safe_price_delta)
        return min(raw_score, max_score)

    events = []
    covered_pairs = set()

    # --- A. BULLISH DIVERGENCE (using lows) ---
    # 1. Check triples
    for i in range(len(lows) - 2):
        p1, p2, p3 = lows[i], lows[i+1], lows[i+2]
        r1, r2, r3 = rsi_vals[p1[0]], rsi_vals[p2[0]], rsi_vals[p3[0]]
        
        if (p3[1] < p2[1] < p1[1]) and (r3 > r2 > r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_price_change_pct(p2, p3) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change and
                get_rsi_change(p2[0], p3[0]) >= min_rsi_change):
                
                if not use_zone_filters or r3 < bullish_rsi_ceil:
                    score = calculate_score(p1, p3, chain_weight=3)
                    events.append({
                        "divergence_type": "triple",
                        "divergence_direction": "bullish",
                        "pivot_indices": [p1[0], p2[0], p3[0]],
                        "pivot_prices": [p1[1], p2[1], p3[1]],
                        "pivot_rsi": [r1, r2, r3],
                        "score": score,
                        "last_idx": p3[0]
                    })
                    covered_pairs.add((p1[0], p2[0]))
                    covered_pairs.add((p2[0], p3[0]))

    # 2. Check doubles
    for i in range(len(lows) - 1):
        p1, p2 = lows[i], lows[i+1]
        if (p1[0], p2[0]) in covered_pairs:
            continue
            
        r1, r2 = rsi_vals[p1[0]], rsi_vals[p2[0]]
        
        if (p2[1] < p1[1]) and (r2 > r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change):
                
                if not use_zone_filters or r2 < bullish_rsi_ceil:
                    score = calculate_score(p1, p2, chain_weight=2)
                    events.append({
                        "divergence_type": "double",
                        "divergence_direction": "bullish",
                        "pivot_indices": [p1[0], p2[0]],
                        "pivot_prices": [p1[1], p2[1]],
                        "pivot_rsi": [r1, r2],
                        "score": score,
                        "last_idx": p2[0]
                    })

    # --- B. BEARISH DIVERGENCE (using highs) ---
    # 1. Check triples
    for i in range(len(highs) - 2):
        p1, p2, p3 = highs[i], highs[i+1], highs[i+2]
        r1, r2, r3 = rsi_vals[p1[0]], rsi_vals[p2[0]], rsi_vals[p3[0]]
        
        if (p3[1] > p2[1] > p1[1]) and (r3 < r2 < r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_price_change_pct(p2, p3) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change and
                get_rsi_change(p2[0], p3[0]) >= min_rsi_change):
                
                if not use_zone_filters or r3 > bearish_rsi_floor:
                    score = calculate_score(p1, p3, chain_weight=3)
                    events.append({
                        "divergence_type": "triple",
                        "divergence_direction": "bearish",
                        "pivot_indices": [p1[0], p2[0], p3[0]],
                        "pivot_prices": [p1[1], p2[1], p3[1]],
                        "pivot_rsi": [r1, r2, r3],
                        "score": score,
                        "last_idx": p3[0]
                    })
                    covered_pairs.add((p1[0], p2[0]))
                    covered_pairs.add((p2[0], p3[0]))

    # 2. Check doubles
    for i in range(len(highs) - 1):
        p1, p2 = highs[i], highs[i+1]
        if (p1[0], p2[0]) in covered_pairs:
            continue
            
        r1, r2 = rsi_vals[p1[0]], rsi_vals[p2[0]]
        
        if (p2[1] > p1[1]) and (r2 < r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change):
                
                if not use_zone_filters or r2 > bearish_rsi_floor:
                    score = calculate_score(p1, p2, chain_weight=2)
                    events.append({
                        "divergence_type": "double",
                        "divergence_direction": "bearish",
                        "pivot_indices": [p1[0], p2[0]],
                        "pivot_prices": [p1[1], p2[1]],
                        "pivot_rsi": [r1, r2],
                        "score": score,
                        "last_idx": p2[0]
                    })

    # --- C. HIDDEN DIVERGENCE (evaluated independently) ---
    # 1. Hidden Bullish (using lows)
    for i in range(len(lows) - 1):
        p1, p2 = lows[i], lows[i+1]
        r1, r2 = rsi_vals[p1[0]], rsi_vals[p2[0]]
        
        if (p2[1] > p1[1]) and (r2 < r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change):
                
                if not use_zone_filters or r2 < bullish_rsi_ceil:
                    score = calculate_score(p1, p2, chain_weight=2)
                    events.append({
                        "divergence_type": "double",
                        "divergence_direction": "hidden_bullish",
                        "pivot_indices": [p1[0], p2[0]],
                        "pivot_prices": [p1[1], p2[1]],
                        "pivot_rsi": [r1, r2],
                        "score": score,
                        "last_idx": p2[0]
                    })

    # 2. Hidden Bearish (using highs)
    for i in range(len(highs) - 1):
        p1, p2 = highs[i], highs[i+1]
        r1, r2 = rsi_vals[p1[0]], rsi_vals[p2[0]]
        
        if (p2[1] < p1[1]) and (r2 > r1):
            if (get_price_change_pct(p1, p2) >= min_price_change_pct and
                get_rsi_change(p1[0], p2[0]) >= min_rsi_change):
                
                if not use_zone_filters or r2 > bearish_rsi_floor:
                    score = calculate_score(p1, p2, chain_weight=2)
                    events.append({
                        "divergence_type": "double",
                        "divergence_direction": "hidden_bearish",
                        "pivot_indices": [p1[0], p2[0]],
                        "pivot_prices": [p1[1], p2[1]],
                        "pivot_rsi": [r1, r2],
                        "score": score,
                        "last_idx": p2[0]
                    })

    return events










