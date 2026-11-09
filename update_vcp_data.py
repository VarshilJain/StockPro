"""
update_vcp_data.py — Recalculate and Backfill VCP Engine v2.0 Data
==================================================================
Runs base detection and VCP v2.0 scoring across symbols in `historical_data`
and updates the database with the new fields:
  - screen_vcp
  - vcp_score
  - vcp_grade
  - breakout_flag
  - base_width_weeks
  - pivot_price
  - contraction_depths
  - 11 score_* subcomponents
  - updated base metrics (base_active, base_high, base_low, etc.)

Usage:
  python update_vcp_data.py               # Updates last 180 days across all active symbols (default)
  python update_vcp_data.py --days 365    # Updates last 365 days
  python update_vcp_data.py --all         # Updates full history
  python update_vcp_data.py --symbol TRENT # Updates a single symbol
"""

import argparse
import concurrent.futures
import json
import logging
import math
import sys
import time
from typing import Optional

import numpy as np
import pandas as pd

from database import get_connection
from pattern_screens_logic import (
    detect_base,
    compute_screen_pcs,
    compute_screen_atb,
    compute_screen_multi_year_breakout,
    compute_screen_ipo_base,
    classify_stages,
    DEFAULT_CONFIG,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("update_vcp")


def sanitize_val(val):
    """Convert numpy types, NaN, and Inf to Python native or None for MySQL."""
    if val is None:
        return None
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, (np.bool_, bool)):
        return int(val)
    if pd.isna(val):
        return None
    return val


UPDATE_SQL = """
    UPDATE historical_data SET
        screen_vcp = %s,
        vcp_score = %s,
        vcp_grade = %s,
        breakout_flag = %s,
        base_width_weeks = %s,
        pivot_price = %s,
        contraction_depths = %s,
        score_progression = %s,
        score_final_depth = %s,
        score_contraction_count = %s,
        score_vol_final = %s,
        score_quietest_day = %s,
        score_up_down_vol = %s,
        score_rs_rank = %s,
        score_off_52wh = %s,
        score_base_count = %s,
        score_atr_compression = %s,
        score_pivot_proximity = %s,
        base_active = %s,
        base_start_date = %s,
        base_length_days = %s,
        base_high = %s,
        base_low = %s,
        base_depth_pct = %s,
        pct_from_pivot = %s,
        contraction_count = %s,
        breakout_today = %s,
        last_breakout_level = %s,
        base_count = %s,
        pct_off_52w_high = %s,
        pct_above_52w_low = %s,
        vdu_flag = %s,
        atr_compression_ratio = %s,
        last_contraction_depth_pct = %s,
        contractions_shrinking = %s,
        coil = %s,
        dry = %s,
        stage_bucket = %s,
        stage = %s,
        screen_blue_sky = %s,
        screen_multi_year_breakout = %s,
        screen_ipo_base = %s
    WHERE Symbol = %s AND Timestamp = %s
"""


def process_single_symbol(sym: str, cutoff_date: Optional[str]) -> int:
    """Process a single symbol and update MySQL. Returns number of updated rows."""
    conn = get_connection()
    read_cur = conn.cursor(dictionary=True)
    write_cur = conn.cursor()
    try:
        min_load_bars = 800

        if cutoff_date is not None:
            read_cur.execute(f"""
                SELECT Symbol, Timestamp, Open, High, Low, Close, Volume,
                       SMA50, SMA200, rs_rank, weeks_since_listing
                FROM (
                    SELECT Symbol, Timestamp, Open, High, Low, Close, Volume,
                           SMA50, SMA200, rs_rank, weeks_since_listing
                    FROM historical_data
                    WHERE Symbol = %s
                    ORDER BY Timestamp DESC
                    LIMIT {min_load_bars}
                ) t
                ORDER BY Timestamp ASC
            """, (sym,))
        else:
            read_cur.execute("""
                SELECT Symbol, Timestamp, Open, High, Low, Close, Volume,
                       SMA50, SMA200, rs_rank, weeks_since_listing
                FROM historical_data
                WHERE Symbol = %s
                ORDER BY Timestamp ASC
            """, (sym,))
        rows = read_cur.fetchall()
        if not rows or len(rows) < 15:
            return 0


        df = pd.DataFrame(rows)
        for num_col in ['Open', 'High', 'Low', 'Close', 'Volume', 'SMA50', 'SMA200', 'rs_rank', 'weeks_since_listing']:
            if num_col in df.columns:
                df[num_col] = pd.to_numeric(df[num_col], errors='coerce')

        if 'weeks_since_listing' not in df.columns or df['weeks_since_listing'].isna().all():
            first_ts = pd.to_datetime(df['Timestamp'].iloc[0])
            df['weeks_since_listing'] = (pd.to_datetime(df['Timestamp']) - first_ts).dt.days // 7

        # Run base detection & pattern screening
        df = detect_base(df, DEFAULT_CONFIG)
        df['screen_vcp'] = compute_screen_pcs(df, DEFAULT_CONFIG)
        df['screen_blue_sky'] = compute_screen_atb(df, DEFAULT_CONFIG)
        df['screen_ipo_base'] = compute_screen_ipo_base(df, DEFAULT_CONFIG)
        df['screen_multi_year_breakout'] = compute_screen_multi_year_breakout(df, DEFAULT_CONFIG)
        df = classify_stages(df, DEFAULT_CONFIG)

        # 1. Ensure screen_vcp = 1 for all valid VCP setups across the 3 stages:
        # Forming: base_active, base_depth <= 35%, length >= 15, coil <= 0.90, dry <= 0.90, rs >= 70
        is_vcp_forming = (
            (df['stage_bucket'] == 'forming') &
            (df['base_depth_pct'] <= 35.0) &
            (df['base_length_days'] >= 15) &
            (df['coil'] <= 0.90) &
            (df['dry'] <= 0.90) &
            (df['rs_rank'].fillna(70) >= 70)
        )
        # Fresh breakout: breakout past pivot within last 5 trading days & rs >= 70
        is_vcp_fresh_bo = (df['stage_bucket'] == 'fresh_breakout') & (df['rs_rank'].fillna(70) >= 70)
        # Climbing: Stage 2 advancing above 50 SMA & rs >= 70
        is_vcp_climbing = (df['stage_bucket'] == 'climbing') & (df['rs_rank'].fillna(70) >= 70)

        df['screen_vcp'] = np.where(
            (df['screen_vcp'] == 1) | is_vcp_forming | is_vcp_fresh_bo | is_vcp_climbing,
            1, 0
        )

        # 2. Ensure screen_blue_sky = 1 across stages aligned with BananaPatterns preset:
        # ["Blue sky", { lvl: [1, 1], rs: [70, 99], ext: [-20, 0] }]
        is_bs_forming = (df['screen_blue_sky'] == 1)
        is_bs_fresh = (df['stage_bucket'] == 'fresh_breakout') & (df['pct_off_52w_high'].fillna(30) <= 15.0) & (df['rs_rank'].fillna(70) >= 70)
        is_bs_climbing = (df['stage_bucket'] == 'climbing') & (df['pct_off_52w_high'].fillna(30) <= 15.0) & (df['rs_rank'].fillna(70) >= 70)

        df['screen_blue_sky'] = np.where(
            is_bs_forming | is_bs_fresh | is_bs_climbing,
            1, 0
        )

        # 3. Multi-Year Breakout (MYB):
        from myb_state_machine_v2 import compute_myb_stage_v2
        myb_df = compute_myb_stage_v2(df)
        is_myb_fresh = myb_df['myb_stage'] == 'fresh_breakout'
        is_myb_climbing = myb_df['myb_stage'] == 'climbing'
        is_myb_forming = myb_df['myb_stage'] == 'forming'
        is_myb = myb_df['screen_multi_year_breakout'] == 1

        df['screen_multi_year_breakout'] = np.where(is_myb, 1, 0)

        # For MYB setups, set pivot_price and pct_from_pivot to the true multi-year ceiling
        df['pivot_price'] = np.where(is_myb, myb_df['myb_pivot'], df['pivot_price'])
        df['pct_from_pivot'] = np.where(is_myb, myb_df['myb_pct_from_pivot'], df['pct_from_pivot'])

        # Update stage_bucket for MYB setups
        df['stage_bucket'] = np.where(is_myb, myb_df['myb_stage'], df['stage_bucket'])

        # 4. Ensure screen_ipo_base = 1 across stages:
        is_ipo_forming = (df['screen_ipo_base'] == 1)
        is_ipo_fresh = (df['stage_bucket'] == 'fresh_breakout') & (df['weeks_since_listing'].fillna(100) <= 52) & (df['Close'] > df['SMA50'])
        is_ipo_climbing = (df['stage_bucket'] == 'climbing') & (df['weeks_since_listing'].fillna(100) <= 52) & (df['Close'] > df['SMA50'])

        df['screen_ipo_base'] = np.where(
            is_ipo_forming | is_ipo_fresh | is_ipo_climbing,
            1, 0
        )

        # Filter rows to write (only recent rows if lookback specified)
        if cutoff_date is not None:
            mask = pd.to_datetime(df['Timestamp']) >= pd.to_datetime(cutoff_date)
            write_df = df[mask]
        else:
            write_df = df

        if write_df.empty:
            return 0

        batch = []
        for _, r in write_df.iterrows():
            ts = r['Timestamp']
            c_depths = r.get('contraction_depths')
            if isinstance(c_depths, list):
                c_depths = json.dumps(c_depths)
            elif pd.isna(c_depths) or c_depths is None:
                c_depths = "[]"

            b_start = r.get('base_start_date')
            if pd.isna(b_start) or str(b_start) == 'NaT':
                b_start = None
            elif hasattr(b_start, 'date'):
                b_start = b_start.date()


            vals = (
                int(r.get('screen_vcp', 0)),
                sanitize_val(r.get('vcp_score')),
                sanitize_val(r.get('vcp_grade')),
                int(r.get('breakout_flag', 0)),
                sanitize_val(r.get('base_width_weeks')),
                sanitize_val(r.get('pivot_price')),
                c_depths,
                sanitize_val(r.get('score_progression')),
                sanitize_val(r.get('score_final_depth')),
                sanitize_val(r.get('score_contraction_count')),
                sanitize_val(r.get('score_vol_final')),
                sanitize_val(r.get('score_quietest_day')),
                sanitize_val(r.get('score_up_down_vol')),
                sanitize_val(r.get('score_rs_rank')),
                sanitize_val(r.get('score_off_52wh')),
                sanitize_val(r.get('score_base_count')),
                sanitize_val(r.get('score_atr_compression')),
                sanitize_val(r.get('score_pivot_proximity')),
                int(r.get('base_active', 0)),
                b_start,
                int(r.get('base_length_days', 0)),
                sanitize_val(r.get('base_high')),
                sanitize_val(r.get('base_low')),
                sanitize_val(r.get('base_depth_pct')),
                sanitize_val(r.get('pct_from_pivot')),
                int(r.get('contraction_count', 0)),
                int(r.get('breakout_today', 0)),
                sanitize_val(r.get('last_breakout_level')),
                int(r.get('base_count', 0)),
                sanitize_val(r.get('pct_off_52w_high')),
                sanitize_val(r.get('pct_above_52w_low')),
                int(r.get('vdu_flag', 0)),
                sanitize_val(r.get('atr_compression_ratio')),
                sanitize_val(r.get('last_contraction_depth_pct')),
                int(r.get('contractions_shrinking', 0)),
                sanitize_val(r.get('coil')),
                sanitize_val(r.get('dry')),
                sanitize_val(r.get('stage_bucket', 'unknown')),
                int(r.get('stage', 1)),
                int(r.get('screen_blue_sky', 0)),
                int(r.get('screen_multi_year_breakout', 0)),
                int(r.get('screen_ipo_base', 0)),
                sym,
                ts
            )
            batch.append(vals)

        if batch:
            write_cur.executemany(UPDATE_SQL, batch)
            conn.commit()
            return len(batch)
        return 0
    except Exception as exc:
        log.error("Error updating %s: %s", sym, exc)
        return 0
    finally:
        read_cur.close()
        write_cur.close()
        conn.close()


def update_vcp_for_symbols(symbols: list[str], days_lookback: Optional[int] = 180, max_workers: int = 8):
    conn = get_connection()
    read_cur = conn.cursor(dictionary=True)
    cutoff_date = None
    try:
        if days_lookback:
            read_cur.execute("SELECT DATE_SUB(MAX(Timestamp), INTERVAL %s DAY) as cutoff FROM historical_data", (days_lookback,))
            row = read_cur.fetchone()
            cutoff_date = row["cutoff"] if row else None
            log.info("Updating rows on or after cutoff date: %s", cutoff_date)
    finally:
        read_cur.close()
        conn.close()

    total_symbols = len(symbols)
    log.info("Starting concurrent VCP v2.0 update for %d symbols with %d workers...", total_symbols, max_workers)

    start_time = time.time()
    total_updated_rows = 0
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sym = {executor.submit(process_single_symbol, sym, cutoff_date): sym for sym in symbols}
        for future in concurrent.futures.as_completed(future_to_sym):
            completed += 1
            rows_updated = future.result()
            total_updated_rows += rows_updated
            if completed % 100 == 0 or completed == total_symbols:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                log.info("Progress: %d / %d symbols (%.1f%%) | Rows updated: %d | Rate: %.1f sym/s | Time: %.1fs",
                         completed, total_symbols, (completed / total_symbols) * 100, total_updated_rows, rate, elapsed)

    total_elapsed = time.time() - start_time
    log.info("VCP v2.0 update completed successfully! Total rows updated: %d in %.1fs", total_updated_rows, total_elapsed)
    try:
        from trend_resumption_service import compute_and_store_trend_resumption
        tr_rows = compute_and_store_trend_resumption()
        log.info("Trend Resumption screen updated successfully! Rows stored: %d", tr_rows)
    except Exception as tr_exc:
        log.warning("Could not update Trend Resumption: %s", tr_exc)

    try:
        import cache
        cache.invalidate_all()
        log.info("Cleared in-memory cache successfully.")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Update VCP v2.0 data in historical_data")
    parser.add_argument("--days", type=int, default=30, help="Number of recent days to update (default: 30)")
    parser.add_argument("--all", action="store_true", help="Update entire history for all symbols")
    parser.add_argument("--symbol", type=str, default=None, help="Update a specific symbol only")
    parser.add_argument("--workers", type=int, default=10, help="Number of worker threads (default: 10)")
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    try:
        if args.symbol:
            sym_input = args.symbol.strip().upper()
            if not sym_input.endswith(".NS") and not sym_input.startswith("^"):
                symbols = [f"{sym_input}.NS"]
            else:
                symbols = [sym_input]
        else:
            cur.execute("""
                SELECT Symbol
                FROM (
                    SELECT Symbol, MAX(Timestamp) as max_ts
                    FROM historical_data
                    WHERE Timestamp >= DATE_SUB((SELECT MAX(Timestamp) FROM historical_data), INTERVAL 60 DAY)
                    GROUP BY Symbol
                ) t
                ORDER BY max_ts DESC, Symbol ASC
            """)
            symbols = [r[0] for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    lookback = None if args.all else args.days
    update_vcp_for_symbols(symbols, days_lookback=lookback, max_workers=args.workers)


if __name__ == "__main__":
    main()
