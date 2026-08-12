"""
run_delivery_signal.py — Fast Standalone Runner (27 Trading Days Window)
========================================================================
Fetches the last 27 trading days of data per stock (5 recent + 22 baseline),
uses real NSE delivery data, computes the non-overlapping Delivery Momentum
Signal, and updates MySQL historical_data table.
"""

import math
import logging
import pandas as pd
import numpy as np

from database import get_connection
from config import (
    DELIVERY_RECENT_WINDOW,
    DELIVERY_BASELINE_WINDOW,
    DELIVERY_BASELINE_GAP,
    DELIVERY_MIN_BASELINE_DAYS,
    DELIVERY_MIN_RECENT_DAYS,
    DELIVERY_LAG_DAYS,
)
from delivery_signal import compute_delivery_momentum_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("run_delivery_signal")

LOOKBACK_TRADING_DAYS = 27  # 5 recent days + 22 baseline days


def main():
    log.info("Fetching last %d trading days of OHLCV & delivery data from MySQL ohlc_data table...", LOOKBACK_TRADING_DAYS)
    conn = get_connection()
    try:
        full_df = pd.read_sql(
            """
            SELECT
                ticker  AS Symbol,
                date    AS Timestamp,
                open    AS Open,
                high    AS High,
                low     AS Low,
                close   AS Close,
                volume  AS Volume,
                delivery_quantity AS delivery_quantity
            FROM ohlc_data
            ORDER BY ticker, date ASC
            """,
            conn,
            parse_dates=["Timestamp"],
        )
    finally:
        conn.close()

    if full_df.empty:
        log.warning("ohlc_data table is empty!")
        return

    # Fast tail: filter strictly to the last 27 trading days per stock
    df = full_df.groupby("Symbol").tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)

    log.info("Filtered dataset to last %d trading days per stock (%d total rows across %d stocks).",
             LOOKBACK_TRADING_DAYS, len(df), df["Symbol"].nunique())

    # Compute Delivery Momentum Signal (5-day recent vs 22-day baseline)
    sig_df = compute_delivery_momentum_signal(
        df,
        recent_window=DELIVERY_RECENT_WINDOW,
        baseline_window=DELIVERY_BASELINE_WINDOW,
        baseline_gap=DELIVERY_BASELINE_GAP,
        min_baseline_days=DELIVERY_MIN_BASELINE_DAYS,
        min_recent_days=DELIVERY_MIN_RECENT_DAYS,
        delivery_lag=DELIVERY_LAG_DAYS,
        auto_populate_missing=False,  # strictly use real delivery values
    )

    df["Recent_DV"]                = sig_df["Recent_DV"]
    df["Baseline_DV"]              = sig_df["Baseline_DV"]
    df["Recent_Deliv_Pct"]         = sig_df["Recent_Deliv_Pct"]
    df["Baseline_Deliv_Pct"]       = sig_df["Baseline_Deliv_Pct"]
    df["delivery_momentum_signal"] = sig_df["Delivery_Signal"].astype(int)

    # Filter stocks triggering signal on their latest trading day
    latest_rows = df.groupby("Symbol").last().reset_index()
    triggered_latest = latest_rows[latest_rows["delivery_momentum_signal"] == 1]

    log.info("=======================================================================")
    log.info("DELIVERY MOMENTUM SIGNAL RESULTS (Latest Trading Day): %d stocks triggered", len(triggered_latest))
    log.info("=======================================================================")
    if not triggered_latest.empty:
        for r in triggered_latest.itertuples():
            log.info("  [SIGNAL] %-15s | Date: %s | Recent DV: %12.2f | Baseline DV: %12.2f | Recent Deliv%%: %5.2f%% | Baseline Deliv%%: %5.2f%%",
                     r.Symbol, r.Timestamp.strftime("%Y-%m-%d"), r.Recent_DV, r.Baseline_DV,
                     (r.Recent_Deliv_Pct or 0)*100, (r.Baseline_Deliv_Pct or 0)*100)

    # Helper to clean NaNs for MySQL
    def _clean(v):
        if v is None or pd.isna(v):
            return None
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v

    log.info("Updating MySQL historical_data table with 27-day window results...")
    update_conn = get_connection()
    try:
        cursor = update_conn.cursor()

        # Add target columns if not present
        cols = [
            ("delivery_quantity", "BIGINT NULL"),
            ("delivery_momentum_signal", "TINYINT DEFAULT 0"),
            ("Recent_DV", "DOUBLE NULL"),
            ("Baseline_DV", "DOUBLE NULL"),
            ("Recent_Deliv_Pct", "DOUBLE NULL"),
            ("Baseline_Deliv_Pct", "DOUBLE NULL"),
        ]
        for c_name, c_def in cols:
            cursor.execute("SHOW COLUMNS FROM historical_data LIKE %s", (c_name,))
            if not cursor.fetchone():
                cursor.execute(f"ALTER TABLE historical_data ADD COLUMN {c_name} {c_def}")
                update_conn.commit()

        update_stmt = """
        UPDATE historical_data
        SET
            delivery_quantity = %s,
            delivery_momentum_signal = %s,
            Recent_DV = %s,
            Baseline_DV = %s,
            Recent_Deliv_Pct = %s,
            Baseline_Deliv_Pct = %s
        WHERE Symbol = %s AND Timestamp = %s
        """

        df["dt_str"] = df["Timestamp"].dt.strftime("%Y-%m-%d")
        data_tuples = [
            (
                _clean(row.delivery_quantity),
                int(row.delivery_momentum_signal),
                _clean(row.Recent_DV),
                _clean(row.Baseline_DV),
                _clean(row.Recent_Deliv_Pct),
                _clean(row.Baseline_Deliv_Pct),
                row.Symbol,
                row.dt_str,
            )
            for row in df.itertuples()
        ]

        batch_size = 5000
        for i in range(0, len(data_tuples), batch_size):
            batch = data_tuples[i : i + batch_size]
            cursor.executemany(update_stmt, batch)
            update_conn.commit()

        cursor.close()
        log.info("Successfully updated %d records in historical_data table!", len(data_tuples))
    finally:
        update_conn.close()


if __name__ == "__main__":
    main()
