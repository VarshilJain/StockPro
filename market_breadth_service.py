"""
market_breadth_service.py
=============================================================================
Market Breadth: Percentage of Stocks Trading Above Their EMA Engine.

Mathematical Specification & Algorithm:
- INPUTS:
    * Configurable list of stock tickers (universe: 'all', 'nifty500', 'nifty50', or custom list)
    * Daily close prices for each stock with >= 400 trading days of history
    * EMA periods: 10, 20, 50, 100, 150, 200
    * Overbought level = 75, Oversold level = 25, Median level = 50

- STEP 1: EMA PER STOCK
    EMA_N = close.ewm(span=N, adjust=False).mean()
    Mask EMA_N as NaN for the first N-1 rows (stock counted once it has >= N days history).

- STEP 2: ABOVE/BELOW FLAG
    above_N = 1 if close > EMA_N, else 0.
    If EMA_N is NaN or close is missing, treat as NaN, not 0.

- STEP 3: BREADTH % PER DATE
    pct_above_N = (sum of above_N across stocks) / (count of valid stocks on date) * 100.0

- STEP 4: OUTPUT SERIES
    Columns: pct_above_10, pct_above_20, pct_above_50, pct_above_100, pct_above_150, pct_above_200.
    Trim to requested display window (e.g. last 12 months) after calculating on full history.

- STEP 5: CHART PAIRING
    Chart 1: pct_above_10 & pct_above_20
    Chart 2: pct_above_50 & pct_above_100
    Chart 3: pct_above_150 & pct_above_200
    Benchmarks: 75 (Overbought), 50 (Median), 25 (Oversold).

- STEP 6: SIGNAL LOGIC
    * overbought: pct > 75
    * oversold: pct < 25
    * cross-up: pct crosses above 25 from below (potential reversal / bullish thrust)
    * cross-down: pct crosses below 75 from above (potential top / exhaustion)
=============================================================================
"""

import logging
import math
import os
import sys
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple

import numpy as np
import pandas as pd

from database import get_connection
import cache

logger = logging.getLogger("market_breadth")

EMA_PERIODS = [10, 20, 50, 100, 150, 200]
OVERBOUGHT_LEVEL = 75.0
OVERSOLD_LEVEL = 25.0
MEDIAN_LEVEL = 50.0

TABLE_NAME = "market_breadth_ema_history"

# ─────────────────────────────────────────────────────────────────────────────
# Universe Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_nifty500_symbols() -> List[str]:
    """Retrieve top 500 market cap symbols from nse_stock_market_caps.csv if available."""
    csv_path = os.path.join(os.path.dirname(__file__), "nse_stock_market_caps.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            sym_col = None
            mcap_col = None
            for col in df.columns:
                cl = col.lower().strip()
                if "symbol" in cl or "ticker" in cl:
                    sym_col = col
                elif "cap" in cl or "mcap" in cl:
                    mcap_col = col
            if sym_col and mcap_col:
                df[mcap_col] = pd.to_numeric(df[mcap_col], errors="coerce")
                df = df.dropna(subset=[sym_col, mcap_col]).sort_values(by=mcap_col, ascending=False)
                syms = df[sym_col].astype(str).tolist()[:500]
                formatted = [s.strip().upper() if s.endswith(".NS") else f"{s.strip().upper()}.NS" for s in syms]
                return formatted
        except Exception as exc:
            logger.warning("Error reading nse_stock_market_caps.csv for Nifty 500: %s", exc)
    return []


def get_universe_symbols(universe: str = "all", custom_symbols: Optional[List[str]] = None) -> List[str]:
    """Return list of symbols for the given universe specification."""
    if custom_symbols and len(custom_symbols) > 0:
        return [s.strip().upper() if s.endswith(".NS") or s.startswith("^") else f"{s.strip().upper()}.NS" for s in custom_symbols]

    universe = universe.lower().strip()
    n500 = get_nifty500_symbols()

    if universe == "nifty500" and n500:
        return n500
    if universe == "nifty50" and n500:
        return n500[:50]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT Symbol
            FROM historical_data
            WHERE Timestamp >= DATE_SUB((SELECT MAX(Timestamp) FROM historical_data), INTERVAL 60 DAY)
              AND Symbol NOT LIKE '%%^%%'
            ORDER BY Symbol ASC
        """)
        all_syms = [r[0] for r in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()

    if universe == "nifty50":
        return all_syms[:50]
    elif universe == "nifty500":
        return all_syms[:500]

    return all_syms


# ─────────────────────────────────────────────────────────────────────────────
# Database Persistence for Ultra-Fast Loading (<5ms)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_breadth_table():
    """Ensure the table market_breadth_ema_history exists."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                universe VARCHAR(20) NOT NULL,
                trade_date DATE NOT NULL,
                pct_above_10 DOUBLE,
                pct_above_20 DOUBLE,
                pct_above_50 DOUBLE,
                pct_above_100 DOUBLE,
                pct_above_150 DOUBLE,
                pct_above_200 DOUBLE,
                valid_count_10 INT,
                valid_count_20 INT,
                valid_count_50 INT,
                valid_count_100 INT,
                valid_count_150 INT,
                valid_count_200 INT,
                PRIMARY KEY (universe, trade_date),
                INDEX idx_breadth_univ_date (universe, trade_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def save_breadth_df_to_db(universe: str, df: pd.DataFrame):
    """Upsert calculated market breadth history dataframe into MySQL."""
    ensure_breadth_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        insert_sql = f"""
            INSERT INTO {TABLE_NAME} (
                universe, trade_date,
                pct_above_10, pct_above_20, pct_above_50,
                pct_above_100, pct_above_150, pct_above_200,
                valid_count_10, valid_count_20, valid_count_50,
                valid_count_100, valid_count_150, valid_count_200
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
                pct_above_10 = VALUES(pct_above_10),
                pct_above_20 = VALUES(pct_above_20),
                pct_above_50 = VALUES(pct_above_50),
                pct_above_100 = VALUES(pct_above_100),
                pct_above_150 = VALUES(pct_above_150),
                pct_above_200 = VALUES(pct_above_200),
                valid_count_10 = VALUES(valid_count_10),
                valid_count_20 = VALUES(valid_count_20),
                valid_count_50 = VALUES(valid_count_50),
                valid_count_100 = VALUES(valid_count_100),
                valid_count_150 = VALUES(valid_count_150),
                valid_count_200 = VALUES(valid_count_200)
        """
        rows = []
        for ts, r in df.iterrows():
            t_date = ts.date() if hasattr(ts, "date") else ts
            rows.append((
                universe,
                t_date,
                float(r["pct_above_10"]) if not pd.isna(r["pct_above_10"]) else None,
                float(r["pct_above_20"]) if not pd.isna(r["pct_above_20"]) else None,
                float(r["pct_above_50"]) if not pd.isna(r["pct_above_50"]) else None,
                float(r["pct_above_100"]) if not pd.isna(r["pct_above_100"]) else None,
                float(r["pct_above_150"]) if not pd.isna(r["pct_above_150"]) else None,
                float(r["pct_above_200"]) if not pd.isna(r["pct_above_200"]) else None,
                int(r["valid_count_10"]) if not pd.isna(r.get("valid_count_10")) else 0,
                int(r["valid_count_20"]) if not pd.isna(r.get("valid_count_20")) else 0,
                int(r["valid_count_50"]) if not pd.isna(r.get("valid_count_50")) else 0,
                int(r["valid_count_100"]) if not pd.isna(r.get("valid_count_100")) else 0,
                int(r["valid_count_150"]) if not pd.isna(r.get("valid_count_150")) else 0,
                int(r["valid_count_200"]) if not pd.isna(r.get("valid_count_200")) else 0,
            ))
        cur.executemany(insert_sql, rows)
        conn.commit()
        logger.info("Persisted %d rows to %s for universe %s", len(rows), TABLE_NAME, universe)
    finally:
        cur.close()
        conn.close()


def load_breadth_df_from_db(universe: str, display_months: int = 12) -> Optional[Tuple[pd.DataFrame, Dict[str, Any]]]:
    """
    Attempt to load cached history from database table.
    Returns (df, metadata) if valid and fresh, otherwise None.
    """
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # Check max date in historical_data
        cur.execute("SELECT DATE(MAX(Timestamp)) as max_db_date FROM historical_data")
        db_row = cur.fetchone()
        if not db_row or not db_row["max_db_date"]:
            return None
        max_db_date = db_row["max_db_date"]

        # Check max date in table
        cur.execute(f"SELECT MAX(trade_date) as max_br_date, COUNT(*) as cnt FROM {TABLE_NAME} WHERE universe = %s", (universe,))
        br_row = cur.fetchone()
        if not br_row or not br_row["max_br_date"] or br_row["cnt"] < 50:
            return None

        max_br_date = br_row["max_br_date"]
        # If breadth history is behind latest trading day, invalidate
        if max_br_date < max_db_date:
            return None

        # Fetch records
        cutoff_date = max_br_date - timedelta(days=int(display_months * 30.5))
        cur.execute(f"""
            SELECT trade_date,
                   pct_above_10, pct_above_20, pct_above_50,
                   pct_above_100, pct_above_150, pct_above_200,
                   valid_count_10, valid_count_20, valid_count_50,
                   valid_count_100, valid_count_150, valid_count_200
            FROM {TABLE_NAME}
            WHERE universe = %s AND trade_date >= %s
            ORDER BY trade_date ASC
        """, (universe, cutoff_date))
        rows = cur.fetchall()
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df.set_index("trade_date", inplace=True)

        # Apply Step 6 signal logic on loaded dataframe
        for N in EMA_PERIODS:
            col = f"pct_above_{N}"
            series = df[col]
            prev_series = series.shift(1)
            df[f"ob_{N}"] = (series > OVERBOUGHT_LEVEL).astype(int)
            df[f"os_{N}"] = (series < OVERSOLD_LEVEL).astype(int)
            df[f"cross_up_{N}"] = ((prev_series <= OVERSOLD_LEVEL) & (series > OVERSOLD_LEVEL)).astype(int)
            df[f"cross_down_{N}"] = ((prev_series >= OVERBOUGHT_LEVEL) & (series < OVERBOUGHT_LEVEL)).astype(int)

        latest_dt = df.index[-1]
        metadata = {
            "as_of_date": latest_dt.strftime("%Y-%m-%d"),
            "as_of_time": latest_dt.strftime("%d %b %Y, 3:30 PM IST"),
            "universe": universe,
            "total_universe_stocks": int(df.iloc[-1].get("valid_count_50", 0)),
            "total_dates_computed": int(br_row["cnt"]),
            "display_dates_count": len(df),
            "calculation_time_sec": 0.01,
            "source": "database_table_cache"
        }
        return df, metadata
    except Exception as exc:
        logger.warning("Failed to load breadth from DB: %s", exc)
        return None
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Core Market Breadth Calculation Algorithm
# ─────────────────────────────────────────────────────────────────────────────

def calculate_market_breadth_df(
    symbols: Optional[List[str]] = None,
    universe: str = "all",
    min_history_bars: int = 400,
    display_months: int = 12,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Computes market breadth dataframe according to STEPS 1 - 6.
    
    Returns:
        (df_display, metadata)
    """
    t_start = time.time()
    sym_list = get_universe_symbols(universe, custom_symbols=symbols)
    if not sym_list:
        raise ValueError("No symbols found for universe")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Step 0: Get the last (min_history_bars + 100) trading dates
        query_dates_limit = max(min_history_bars + 100, 500)
        cursor.execute(
            f"SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT {query_dates_limit}"
        )
        trading_dates = [r[0] for r in cursor.fetchall()]
        if not trading_dates:
            raise ValueError("No historical data available in database")

        trading_dates.sort()
        earliest_ts = trading_dates[0]
        latest_ts = trading_dates[-1]

        # Fetch Symbol, Timestamp, Close for requested symbols
        if len(sym_list) < 600:
            format_strings = ','.join(['%s'] * len(sym_list))
            query = f"""
                SELECT Symbol, Timestamp, Close
                FROM historical_data
                WHERE Timestamp >= %s
                  AND Symbol IN ({format_strings})
                ORDER BY Symbol, Timestamp ASC
            """
            cursor.execute(query, [earliest_ts] + sym_list)
        else:
            query = """
                SELECT Symbol, Timestamp, Close
                FROM historical_data
                WHERE Timestamp >= %s
                  AND Symbol NOT LIKE '%%^%%'
                ORDER BY Symbol, Timestamp ASC
            """
            cursor.execute(query, (earliest_ts,))

        raw_rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    if not raw_rows:
        raise ValueError("No price records found for calculation")

    t_fetch = time.time()
    logger.info("Fetched %d rows for breadth calculation in %.2fs", len(raw_rows), t_fetch - t_start)

    # Build DataFrame and pivot
    raw_df = pd.DataFrame(raw_rows, columns=["Symbol", "Timestamp", "Close"])
    raw_df["Close"] = pd.to_numeric(raw_df["Close"], errors="coerce")
    
    # Filter to requested universe symbols if queried all
    if symbols or universe in ("nifty500", "nifty50"):
        raw_df = raw_df[raw_df["Symbol"].isin(set(sym_list))]

    # Pivot: index = Timestamp, columns = Symbol, values = Close
    pivoted_close = raw_df.pivot(index="Timestamp", columns="Symbol", values="Close")
    pivoted_close.sort_index(inplace=True)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: EMA PER STOCK
    # For each stock and each period N in [10, 20, 50, 100, 150, 200]:
    #   EMA_N = close.ewm(span=N, adjust=False).mean()
    #   Mask EMA_N as NaN for the first N-1 rows per stock
    #
    # STEP 2: ABOVE/BELOW FLAG
    #   above_N = 1 if close > EMA_N, else 0
    #   If EMA_N is NaN or close is missing, treat as NaN, not 0
    #
    # STEP 3: BREADTH % PER DATE
    #   pct_above_N = sum(above_N) / count(valid non-NaN above_N) * 100
    # ─────────────────────────────────────────────────────────────────────────

    breadth_series: Dict[str, pd.Series] = {}
    valid_counts_series: Dict[str, pd.Series] = {}

    for N in EMA_PERIODS:
        # Vectorized EMA calculation across all columns
        ema_matrix = pivoted_close.ewm(span=N, adjust=False).mean()

        # Mask EMA_N as NaN for the first N-1 rows per stock:
        valid_cum_count = pivoted_close.notna().cumsum(axis=0)
        insufficient_history = valid_cum_count < N
        ema_matrix = ema_matrix.mask(insufficient_history, np.nan)

        # STEP 2: Above/below flag
        is_above = (pivoted_close > ema_matrix).astype(float)
        is_above = is_above.mask(ema_matrix.isna() | pivoted_close.isna(), np.nan)

        # STEP 3: Breadth % per date
        valid_stocks_count = is_above.notna().sum(axis=1)
        stocks_above_count = is_above.sum(axis=1)

        pct_above = np.where(valid_stocks_count > 0, (stocks_above_count / valid_stocks_count) * 100.0, np.nan)
        breadth_series[f"pct_above_{N}"] = pd.Series(pct_above, index=pivoted_close.index)
        valid_counts_series[f"valid_count_{N}"] = valid_stocks_count

    # Combine into full breadth DataFrame
    full_df = pd.DataFrame(breadth_series, index=pivoted_close.index)
    for k, v in valid_counts_series.items():
        full_df[k] = v

    # Save full historical calculation to DB table for future instant lookup
    try:
        save_breadth_df_to_db(universe, full_df)
    except Exception as exc:
        logger.warning("Could not persist breadth df to DB: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: SIGNAL LOGIC & REVERSAL DETECTION
    # ─────────────────────────────────────────────────────────────────────────
    for N in EMA_PERIODS:
        col = f"pct_above_{N}"
        series = full_df[col]
        prev_series = series.shift(1)

        full_df[f"ob_{N}"] = (series > OVERBOUGHT_LEVEL).astype(int)
        full_df[f"os_{N}"] = (series < OVERSOLD_LEVEL).astype(int)

        # Cross-up: previous was <= 25, current is > 25
        full_df[f"cross_up_{N}"] = ((prev_series <= OVERSOLD_LEVEL) & (series > OVERSOLD_LEVEL)).astype(int)

        # Cross-down: previous was >= 75, current is < 75
        full_df[f"cross_down_{N}"] = ((prev_series >= OVERBOUGHT_LEVEL) & (series < OVERBOUGHT_LEVEL)).astype(int)

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: OUTPUT SERIES
    # Trim to last display_months (default ~12 months / ~252 trading days)
    # ─────────────────────────────────────────────────────────────────────────
    cutoff_dt = latest_ts - timedelta(days=int(display_months * 30.5))
    display_df = full_df[full_df.index >= cutoff_dt].copy()
    if len(display_df) < 50 and len(full_df) >= 50:
        display_df = full_df.iloc[-252:].copy()

    t_calc = time.time()
    logger.info("Market breadth calculated in %.2fs (total: %.2fs)", t_calc - t_fetch, t_calc - t_start)

    metadata = {
        "as_of_date": latest_ts.strftime("%Y-%m-%d"),
        "as_of_time": latest_ts.strftime("%d %b %Y, 3:30 PM IST") if hasattr(latest_ts, "strftime") else str(latest_ts),
        "universe": universe,
        "total_universe_stocks": len(pivoted_close.columns),
        "total_dates_computed": len(full_df),
        "display_dates_count": len(display_df),
        "calculation_time_sec": round(t_calc - t_start, 2),
    }

    return display_df, metadata


# ─────────────────────────────────────────────────────────────────────────────
# JSON Serialization & API Payload Builder
# ─────────────────────────────────────────────────────────────────────────────

def get_market_breadth_payload(universe: str = "all", display_months: int = 12, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Computes or retrieves cached market breadth payload with chart series and signals.
    """
    cache_key = f"market_breadth_ema_v2_{universe}_{display_months}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Try fast DB load first if not force_refresh
    df = None
    meta = None
    if not force_refresh:
        db_res = load_breadth_df_from_db(universe=universe, display_months=display_months)
        if db_res is not None:
            df, meta = db_res

    # If DB load didn't hit, run full calculation
    if df is None:
        df, meta = calculate_market_breadth_df(universe=universe, display_months=display_months)

    # Build dates list
    dates_str = [d.strftime("%Y-%m-%d") for d in df.index]

    # Build individual series
    series_data: Dict[str, List[Dict[str, Any]]] = {}
    for N in EMA_PERIODS:
        col = f"pct_above_{N}"
        pts = []
        for dt_str, val in zip(dates_str, df[col]):
            pts.append({
                "time": dt_str,
                "value": round(float(val), 2) if not pd.isna(val) else None
            })
        series_data[f"ema_{N}"] = pts

    # Latest values and signals summary
    latest_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else latest_row

    summary_cards = []
    for N in EMA_PERIODS:
        val = float(latest_row[f"pct_above_{N}"]) if not pd.isna(latest_row[f"pct_above_{N}"]) else 0.0
        prev_val = float(prev_row[f"pct_above_{N}"]) if not pd.isna(prev_row[f"pct_above_{N}"]) else val
        change = round(val - prev_val, 2)

        # State classification
        if val >= OVERBOUGHT_LEVEL:
            state = "Overbought"
            badge_color = "danger"
            state_desc = f"Extreme strength / Extended (>{int(OVERBOUGHT_LEVEL)}%)"
        elif val <= OVERSOLD_LEVEL:
            state = "Oversold"
            badge_color = "success"
            state_desc = f"Extreme weakness / Reversal Watch (<{int(OVERSOLD_LEVEL)}%)"
        elif val >= MEDIAN_LEVEL:
            state = "Positive"
            badge_color = "info"
            state_desc = f"Above median (>{int(MEDIAN_LEVEL)}%)"
        else:
            state = "Negative"
            badge_color = "warning"
            state_desc = f"Below median (<{int(MEDIAN_LEVEL)}%)"

        # Signal event
        signal_event = None
        if bool(latest_row.get(f"cross_up_{N}")):
            signal_event = "BULLISH_CROSS_UP"
        elif bool(latest_row.get(f"cross_down_{N}")):
            signal_event = "BEARISH_CROSS_DOWN"

        valid_cnt = int(latest_row.get(f"valid_count_{N}", 0))

        summary_cards.append({
            "period": N,
            "period_label": f"{N} EMA",
            "current_pct": round(val, 2),
            "previous_pct": round(prev_val, 2),
            "change_pct": change,
            "state": state,
            "state_desc": state_desc,
            "badge_color": badge_color,
            "signal_event": signal_event,
            "valid_stocks": valid_cnt,
        })

    # Recent Signal Events across the display timeline (last 90 days)
    signal_log = []
    recent_tail = df.tail(90)
    for ts, row in recent_tail.iterrows():
        dt_str = ts.strftime("%Y-%m-%d")
        for N in EMA_PERIODS:
            if bool(row.get(f"cross_up_{N}")):
                signal_log.append({
                    "date": dt_str,
                    "period": N,
                    "period_label": f"{N} EMA",
                    "event": "Cross-Up (Reversal Thrust)",
                    "type": "bullish",
                    "value": round(float(row[f"pct_above_{N}"]), 2),
                    "note": f"% stocks crossed above {OVERSOLD_LEVEL}% oversold threshold"
                })
            elif bool(row.get(f"cross_down_{N}")):
                signal_log.append({
                    "date": dt_str,
                    "period": N,
                    "period_label": f"{N} EMA",
                    "event": "Cross-Down (Exhaustion / Top)",
                    "type": "bearish",
                    "value": round(float(row[f"pct_above_{N}"]), 2),
                    "note": f"% stocks dropped below {OVERBOUGHT_LEVEL}% overbought threshold"
                })

    signal_log.sort(key=lambda x: (x["date"], x["period"]), reverse=True)

    payload = {
        "metadata": meta,
        "benchmarks": {
            "overbought": OVERBOUGHT_LEVEL,
            "median": MEDIAN_LEVEL,
            "oversold": OVERSOLD_LEVEL
        },
        "pairs": [
            {
                "id": "pair_10_20",
                "chart_number": "1",
                "title": "% Stocks Above 10 & 20 EMA",
                "subtitle": "Short-Term Momentum & Tactical Breadth",
                "period_a": 10,
                "period_b": 20,
                "label_a": "10 EMA",
                "label_b": "20 EMA",
                "color_a": "#8b5cf6", # Purple
                "color_b": "#38bdf8", # Sky blue
            },
            {
                "id": "pair_50_100",
                "chart_number": "2",
                "title": "% Stocks Above 50 & 100 EMA",
                "subtitle": "Intermediate-Term Trend Participation",
                "period_a": 50,
                "period_b": 100,
                "label_a": "50 EMA",
                "label_b": "100 EMA",
                "color_a": "#ec4899", # Pink
                "color_b": "#f59e0b", # Amber
            },
            {
                "id": "pair_150_200",
                "chart_number": "3",
                "title": "% Stocks Above 150 & 200 EMA",
                "subtitle": "Long-Term Structural / Macro Breadth",
                "period_a": 150,
                "period_b": 200,
                "label_a": "150 EMA",
                "label_b": "200 EMA",
                "color_a": "#06b6d4", # Cyan
                "color_b": "#3b82f6", # Blue
            }
        ],
        "summary_cards": summary_cards,
        "series": series_data,
        "recent_signals": signal_log[:25],
    }

    # Cache for 15 minutes
    cache.set(cache_key, payload, ttl=900)
    return payload


def backfill_all_universes():
    """CLI / Nightly hook to compute and persist breadth for all standard universes."""
    print("=" * 70)
    print("BACKFILLING MARKET BREADTH EMA ENGINE FOR ALL UNIVERSES")
    print("=" * 70)
    universes = ["nifty50", "nifty500", "all"]
    for u in universes:
        t0 = time.time()
        print(f"--> Computing universe: {u}...")
        df, meta = calculate_market_breadth_df(universe=u, display_months=18)
        print(f"    Done in {time.time() - t0:.2f}s! Stored {len(df)} dates. As-of: {meta['as_of_date']}")
    print("[SUCCESS] All universes backfilled into database.")


if __name__ == "__main__":
    backfill_all_universes()
