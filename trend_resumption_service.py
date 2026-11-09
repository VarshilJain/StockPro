import logging
from typing import Dict, List, Optional
import time
import pandas as pd
import numpy as np
from database import get_connection

logger = logging.getLogger(__name__)


def ensure_trend_resumption_table():
    """Ensure the trend_resumption_results table exists in the database."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trend_resumption_results (
            date DATE NOT NULL,
            symbol VARCHAR(50) NOT NULL,
            criteria VARCHAR(30) NOT NULL,
            close DOUBLE,
            open DOUBLE,
            rs_rank DOUBLE,
            ema5 DOUBLE,
            ema9 DOUBLE,
            ema10 DOUBLE,
            ema13 DOUBLE,
            ema21 DOUBLE,
            ema50 DOUBLE,
            ema100 DOUBLE,
            ema150 DOUBLE,
            ema200 DOUBLE,
            pct_above_ema50 DOUBLE,
            pct_above_ema200 DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (date, symbol),
            KEY idx_date (date),
            KEY idx_symbol (symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    conn.commit()
    cur.close()
    conn.close()


def compute_and_store_trend_resumption(days_lookback: int = 420) -> int:
    """
    Computes Trend Resumption across all active stocks in historical_data
    and persists qualifying rows for the most recent trading dates into trend_resumption_results.
    Returns the number of rows inserted/updated.
    """
    ensure_trend_resumption_table()
    t0 = time.time()
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # 1. Fetch recent price history
    cur.execute("""
        SELECT Symbol, Timestamp, Close, Open, IFNULL(rs_rank, 0) as rs_rank
        FROM historical_data
        WHERE Timestamp >= DATE_SUB((SELECT MAX(Timestamp) FROM historical_data), INTERVAL %s DAY)
        ORDER BY Symbol, Timestamp ASC
    """, (days_lookback,))
    rows = cur.fetchall()
    if not rows:
        cur.close()
        conn.close()
        return 0

    df = pd.DataFrame(rows)
    df['Close'] = df['Close'].astype(float)
    df['Open'] = df['Open'].astype(float)
    df['rs_rank'] = df['rs_rank'].astype(float)

    # 2. Vectorized EMA calculation per symbol
    # EMAs required by Criteria 1 & 2: 5, 9, 10, 13, 21, 50, 100, 150, 200
    spans = [5, 9, 10, 13, 21, 50, 100, 150, 200]
    for span in spans:
        df[f'ema{span}'] = df.groupby('Symbol')['Close'].transform(lambda s: s.ewm(span=span, adjust=False).mean())

    # 3. Criteria 1 Evaluation
    # rule a: Close > EMA200, EMA150, EMA100, EMA50
    c1_a = (df['Close'] > df['ema200']) & (df['Close'] > df['ema150']) & (df['Close'] > df['ema100']) & (df['Close'] > df['ema50'])
    # rule b: EMA50 > EMA200, EMA150, EMA100
    c1_b = (df['ema50'] > df['ema200']) & (df['ema50'] > df['ema150']) & (df['ema50'] > df['ema100'])
    # rule c: EMA21 > EMA200, EMA150, EMA100
    c1_c = (df['ema21'] > df['ema200']) & (df['ema21'] > df['ema150']) & (df['ema21'] > df['ema100'])
    # rule d: EMA21 < EMA50 and EMA13 > EMA50 and EMA9 > EMA50 and EMA5 > EMA50
    c1_d = (df['ema21'] < df['ema50']) & (df['ema13'] > df['ema50']) & (df['ema9'] > df['ema50']) & (df['ema5'] > df['ema50'])
    crit1 = c1_a & c1_b & c1_c & c1_d

    # 4. Criteria 2 Evaluation
    # rule a: Close > EMA200, EMA150, EMA100, EMA50
    c2_a = c1_a
    # rule b: EMA50 > EMA200, EMA150, EMA100
    c2_b = c1_b
    # rule c: EMA21 > EMA200, EMA150, EMA50
    c2_c = (df['ema21'] > df['ema200']) & (df['ema21'] > df['ema150']) & (df['ema21'] > df['ema50'])
    # rule d: EMA10 > EMA50 and EMA10 < EMA21 and EMA5 > EMA21
    c2_d = (df['ema10'] > df['ema50']) & (df['ema10'] < df['ema21']) & (df['ema5'] > df['ema21'])
    crit2 = c2_a & c2_b & c2_c & c2_d

    df['crit1'] = crit1
    df['crit2'] = crit2
    qualifies = crit1 | crit2

    # Filter to only qualifying rows
    qual_df = df[qualifies].copy()
    if qual_df.empty:
        cur.close()
        conn.close()
        return 0

    # Retain the last 30 trading dates of qualifications
    trading_dates = sorted(df['Timestamp'].unique(), reverse=True)
    recent_dates = set(trading_dates[:30])
    qual_df = qual_df[qual_df['Timestamp'].isin(recent_dates)]

    # Assign criteria text label
    def get_crit_label(row):
        if row['crit1'] and row['crit2']:
            return 'Criteria 1 & 2'
        elif row['crit1']:
            return 'Criteria 1'
        else:
            return 'Criteria 2'

    qual_df['criteria'] = qual_df.apply(get_crit_label, axis=1)
    qual_df['pct_above_ema50'] = ((qual_df['Close'] - qual_df['ema50']) / qual_df['ema50']) * 100.0
    qual_df['pct_above_ema200'] = ((qual_df['Close'] - qual_df['ema200']) / qual_df['ema200']) * 100.0

    insert_sql = """
        INSERT INTO trend_resumption_results (
            date, symbol, criteria, close, open, rs_rank,
            ema5, ema9, ema10, ema13, ema21, ema50, ema100, ema150, ema200,
            pct_above_ema50, pct_above_ema200
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s
        ) ON DUPLICATE KEY UPDATE
            criteria = VALUES(criteria),
            close = VALUES(close),
            open = VALUES(open),
            rs_rank = VALUES(rs_rank),
            ema5 = VALUES(ema5),
            ema9 = VALUES(ema9),
            ema10 = VALUES(ema10),
            ema13 = VALUES(ema13),
            ema21 = VALUES(ema21),
            ema50 = VALUES(ema50),
            ema100 = VALUES(ema100),
            ema150 = VALUES(ema150),
            ema200 = VALUES(ema200),
            pct_above_ema50 = VALUES(pct_above_ema50),
            pct_above_ema200 = VALUES(pct_above_ema200)
    """

    insert_batch = []
    for _, r in qual_df.iterrows():
        d_val = r['Timestamp'].date() if hasattr(r['Timestamp'], 'date') else str(r['Timestamp']).split()[0]
        insert_batch.append((
            d_val,
            r['Symbol'],
            r['criteria'],
            round(float(r['Close']), 2),
            round(float(r['Open']), 2) if pd.notnull(r['Open']) else None,
            round(float(r['rs_rank']), 2) if pd.notnull(r['rs_rank']) else 0.0,
            round(float(r['ema5']), 2),
            round(float(r['ema9']), 2),
            round(float(r['ema10']), 2),
            round(float(r['ema13']), 2),
            round(float(r['ema21']), 2),
            round(float(r['ema50']), 2),
            round(float(r['ema100']), 2),
            round(float(r['ema150']), 2),
            round(float(r['ema200']), 2),
            round(float(r['pct_above_ema50']), 2),
            round(float(r['pct_above_ema200']), 2),
        ))

    if insert_batch:
        cur.executemany(insert_sql, insert_batch)
        conn.commit()

    cur.close()
    conn.close()
    logger.info(f"Trend Resumption computed and saved {len(insert_batch)} rows in {time.time() - t0:.2f}s")
    return len(insert_batch)


def get_trend_resumption_stages(
    criteria_filter: Optional[str] = None,
    min_market_cap: Optional[float] = None
) -> Dict:
    """
    Fetches the Trend Resumption screen stages:
    - 'fresh_breakout': Triggered on latest trading date
    - 'climbing': Triggered within last 5 trading days
    """
    ensure_trend_resumption_table()
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # Check latest trading date in historical_data
    cur.execute("SELECT MAX(Timestamp) as max_date FROM historical_data")
    row = cur.fetchone()
    if not row or not row["max_date"]:
        cur.close()
        conn.close()
        return {"screen": "trend-resumption", "as_of_date": None, "stages": {}}

    max_hist_date = row["max_date"].date() if hasattr(row["max_date"], "date") else str(row["max_date"]).split()[0]

    # Check if we already have records up to max_hist_date in trend_resumption_results
    cur.execute("SELECT MAX(date) as max_res_date FROM trend_resumption_results")
    res_row = cur.fetchone()
    max_res_date = res_row["max_res_date"] if res_row and res_row["max_res_date"] else None

    # If results table is empty or behind latest date, compute it
    if max_res_date is None or max_res_date < max_hist_date:
        cur.close()
        conn.close()
        compute_and_store_trend_resumption()
        conn = get_connection()
        cur = conn.cursor(dictionary=True)

    # Get the latest 5 trading dates from historical_data (using index on Timestamp)
    cur.execute(
        "SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT 5"
    )
    raw_dates = [r["Timestamp"] for r in cur.fetchall()]
    trading_dates = [
        d.date() if hasattr(d, "date") else d
        for d in raw_dates
    ]
    if not trading_dates:
        cur.close()
        conn.close()
        return {"screen": "trend-resumption", "as_of_date": str(max_hist_date), "stages": {}}

    max_date = trading_dates[0]
    five_days_start = trading_dates[-1]

    # Query trend_resumption_results for last 5 trading days
    where_clauses = ["date >= %s", "date <= %s"]
    params = [five_days_start, max_date]

    if criteria_filter and criteria_filter != "all":
        if criteria_filter in ("criteria1", "Criteria 1"):
            where_clauses.append("(criteria = 'Criteria 1' OR criteria = 'Criteria 1 & 2')")
        elif criteria_filter in ("criteria2", "Criteria 2"):
            where_clauses.append("(criteria = 'Criteria 2' OR criteria = 'Criteria 1 & 2')")

    sql = f"""
        SELECT * FROM trend_resumption_results
        WHERE {" AND ".join(where_clauses)}
        ORDER BY date DESC, rs_rank DESC
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from routers.screens import get_symbol_market_cap

    date_to_days_ago = {
        (d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)): idx
        for idx, d in enumerate(trading_dates)
    }
    max_date_str = max_date.strftime("%Y-%m-%d") if hasattr(max_date, "strftime") else str(max_date)

    stages = {
        "fresh_breakout": {"count": 0, "symbols": []},  # Latest Date
        "climbing": {"count": 0, "symbols": []}          # Last 5 Days
    }

    seen_5day_symbols = set()
    climbing_all_symbols = []

    for r in rows:
        sym = r["symbol"]
        mcap = get_symbol_market_cap(sym)
        if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):
            continue

        d_val = r["date"]
        d_str = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
        days_since = date_to_days_ago.get(d_str, 0)

        item = {
            "symbol": sym,
            "market_cap_cr": mcap,
            "rs_rank": float(r["rs_rank"]) if r.get("rs_rank") is not None else 0.0,
            "close": float(r["close"]) if r.get("close") is not None else None,
            "open": float(r["open"]) if r.get("open") is not None else None,
            "criteria": r["criteria"],
            "ema5": float(r["ema5"]) if r.get("ema5") is not None else None,
            "ema9": float(r["ema9"]) if r.get("ema9") is not None else None,
            "ema10": float(r["ema10"]) if r.get("ema10") is not None else None,
            "ema13": float(r["ema13"]) if r.get("ema13") is not None else None,
            "ema21": float(r["ema21"]) if r.get("ema21") is not None else None,
            "ema50": float(r["ema50"]) if r.get("ema50") is not None else None,
            "ema100": float(r["ema100"]) if r.get("ema100") is not None else None,
            "ema150": float(r["ema150"]) if r.get("ema150") is not None else None,
            "ema200": float(r["ema200"]) if r.get("ema200") is not None else None,
            "pct_above_ema50": float(r["pct_above_ema50"]) if r.get("pct_above_ema50") is not None else None,
            "pct_above_ema200": float(r["pct_above_ema200"]) if r.get("pct_above_ema200") is not None else None,
            "base_start_date": d_str,
            "days_since": days_since,
            "breakout_today": 1 if days_since == 0 else 0,
            "breakout_date": d_str,
            "last_breakout_level": None,
        }

        # If on latest trading day, add to fresh_breakout
        if d_str == max_date_str:
            stages["fresh_breakout"]["symbols"].append(item)

        # Add to climbing (Last 5 Days), deduplicated by symbol
        if sym not in seen_5day_symbols:
            seen_5day_symbols.add(sym)
            climbing_all_symbols.append(item)

    climbing_all_symbols.sort(key=lambda x: x["rs_rank"], reverse=True)
    stages["climbing"]["symbols"] = climbing_all_symbols

    stages["fresh_breakout"]["count"] = len(stages["fresh_breakout"]["symbols"])
    stages["climbing"]["count"] = len(stages["climbing"]["symbols"])

    return {
        "screen": "trend-resumption",
        "as_of_date": max_date_str,
        "stages": stages
    }
