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


def _ensure_daily_pick_table() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_pick (
            id INT AUTO_INCREMENT PRIMARY KEY,
            pick_date DATE NOT NULL,
            symbol VARCHAR(64) NOT NULL,
            confidence INT NOT NULL,
            signal VARCHAR(32) NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_pick_date (pick_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    conn.commit()
    cursor.close()
    conn.close()


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


def select_and_store_daily_pick(pick_for_date: Optional[date] = None) -> Dict[str, object]:
    _ensure_daily_pick_table()
    if pick_for_date is None:
        pick_for_date = date.today()

    # Try to return existing pick for the date
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM daily_pick WHERE pick_date = %s", (pick_for_date,))
    row = cursor.fetchone()
    if row:
        cursor.close()
        conn.close()
        return {
            "date": pick_for_date.strftime("%Y-%m-%d"),
            "symbol": row["symbol"],
            "confidence": row["confidence"],
            "signal": row["signal"],
            "reason": row.get("reason") or ""
        }

    # Compute over all symbols
    symbols = _get_all_symbols()
    best: Tuple[str, int, str, str] = ("", -1, "", "")
    for sym in symbols:
        conf = compute_signal_confidence(sym)
        score = int(conf.get("confidence_score", 0))
        label = str(conf.get("signal", ""))
        vol_spike = _volume_spike_for_latest(sym)
        reason = generate_signal_explanation(sym)

        # Prefer volume spike ties, then higher score
        current = (sym, score, label, reason)
        if best[1] < 0:
            best = current
        else:
            best_is_spike = _volume_spike_for_latest(best[0])
            if vol_spike and not best_is_spike:
                best = current
            elif vol_spike == best_is_spike and score > best[1]:
                best = current

    if best[1] < 0:
        cursor.close()
        conn.close()
        return {"date": pick_for_date.strftime("%Y-%m-%d"), "symbol": None, "confidence": 0, "signal": "", "reason": "No symbols available"}

    # Store
    cursor.execute(
        "INSERT INTO daily_pick (pick_date, symbol, confidence, signal, reason) VALUES (%s, %s, %s, %s, %s)"
        " ON DUPLICATE KEY UPDATE symbol = VALUES(symbol), confidence = VALUES(confidence), signal = VALUES(signal), reason = VALUES(reason)",
        (pick_for_date, best[0], best[1], best[2], best[3])
    )
    conn.commit()
    cursor.close()
    conn.close()

    return {
        "date": pick_for_date.strftime("%Y-%m-%d"),
        "symbol": best[0],
        "confidence": best[1],
        "signal": best[2],
        "reason": best[3]
    }


def get_daily_pick() -> Dict[str, object]:
    _ensure_daily_pick_table()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM daily_pick ORDER BY pick_date DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return select_and_store_daily_pick()
    return {
        "date": row["pick_date"].strftime("%Y-%m-%d") if isinstance(row["pick_date"], (datetime, date)) else str(row["pick_date"]),
        "symbol": row["symbol"],
        "confidence": row["confidence"],
        "signal": row["signal"],
        "reason": row.get("reason") or ""
    }


# ==================================
# Phase 2: Dashboard Aggregation API
# ==================================

def dashboard_aggregate() -> Dict[str, object]:
    symbols = _get_all_symbols()
    results: List[Tuple[str, int, str]] = []
    for sym in symbols:
        res = compute_signal_confidence(sym)
        results.append((sym, int(res.get("confidence_score", 0)), str(res.get("signal"))))

    top_bullish = sorted(results, key=lambda x: x[1], reverse=True)[:5]
    top_bearish = sorted(results, key=lambda x: x[1])[:5]

    # Placeholder sector heatmap (no sector table available); compute simple shares
    bullish_share = int(round(sum(1 for _, s, _ in results if s >= 60) * 100.0 / max(1, len(results))))
    bearish_share = 100 - bullish_share
    sector_heatmap = [{"sector": "Market", "bullish": bullish_share, "bearish": bearish_share}]

    return {
        "top_bullish": [{"symbol": s, "confidence": c, "signal": lab} for s, c, lab in top_bullish],
        "top_bearish": [{"symbol": s, "confidence": c, "signal": lab} for s, c, lab in top_bearish],
        "sectors": sector_heatmap
    }


