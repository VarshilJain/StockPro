"""
delivery_signal.py — Delivery Momentum Signal Implementation
============================================================

Computes a composite stock signal called "Delivery Momentum Signal"
using non-overlapping comparison windows.

Formula:
  Step 1 — Delivery Value:
           DeliveryValue(t) = Delivery Quantity(t) × EOD Close Price(t)

  Step 2 — Delivery Percentage:
           Deliv%(t) = Delivery Quantity(t) / Traded Quantity(t)

  Step 3 — Non-Overlapping Windows (as of trading day t):
           Recent window   = last 5 trading days (t-4 to t)
           Baseline window = 22 trading days immediately BEFORE recent window (t-26 to t-5)
                             (strictly non-overlapping: baseline ends at t-5, recent starts at t-4)

  Step 4 — Rolling Averages (mean, excluding NaNs):
           Recent_DV       = mean(DeliveryValue, Recent window)
           Baseline_DV     = mean(DeliveryValue, Baseline window)
           Recent_Deliv%   = mean(Deliv%, Recent window)
           Baseline_Deliv% = mean(Deliv%, Baseline window)

  Step 5 — Composite Signal:
           Signal = True if (Recent_DV > Baseline_DV) and (Recent_Deliv% > Baseline_Deliv%)

Notes & Protection Rules:
  - Missing / NaN delivery data is excluded from window averages (skipna=True).
  - Minimum number of valid trading days in Baseline window is required (default: 15 of 22).
  - Delivery data for day T can be shifted by `delivery_lag` days (default: 0, or 1 for T+1 publication lag)
    to prevent look-ahead bias.
  - Zero/NaN traded quantity is handled safely without division-by-zero errors.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import Optional, Union, Tuple, Dict, Any

logger = logging.getLogger(__name__)


def populate_missing_delivery_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Populates missing/NaN delivery data in a DataFrame based on stock volume,
    candle body size, and price movement dynamics.

    Returns the DataFrame with a populated 'Delivery_Quantity' column.
    """
    df = df.copy()

    # Find volume and close columns
    vol_col = None
    for c in ["Volume", "volume", "Traded_Quantity", "traded_quantity"]:
        if c in df.columns:
            vol_col = c
            break

    close_col = None
    for c in ["Close", "close", "eod_close"]:
        if c in df.columns:
            close_col = c
            break

    open_col = None
    for c in ["Open", "open"]:
        if c in df.columns:
            open_col = c
            break

    high_col = None
    for c in ["High", "high"]:
        if c in df.columns:
            high_col = c
            break

    low_col = None
    for c in ["Low", "low"]:
        if c in df.columns:
            low_col = c
            break

    deliv_col = None
    for c in ["Delivery_Quantity", "delivery_quantity", "DeliveryQuantity"]:
        if c in df.columns:
            deliv_col = c
            break

    if not deliv_col:
        df["Delivery_Quantity"] = np.nan
        deliv_col = "Delivery_Quantity"

    if vol_col and vol_col in df.columns:
        vol = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)
        
        # Calculate base delivery ratio (default ~40%)
        deliv_ratio = pd.Series(0.40, index=df.index)

        # Boost delivery ratio on strong green bullish expansion candles
        if close_col and open_col and high_col and low_col:
            close = pd.to_numeric(df[close_col], errors="coerce")
            open_ = pd.to_numeric(df[open_col], errors="coerce")
            high = pd.to_numeric(df[high_col], errors="coerce")
            low = pd.to_numeric(df[low_col], errors="coerce")

            rng = (high - low).replace(0, np.nan)
            body = (close - open_).abs()
            body_pct = (body / rng).fillna(0)

            # Bullish close near high -> higher institutional delivery ratio (55% - 75%)
            bullish_mask = (close > open_) & (body_pct > 0.4)
            deliv_ratio = np.where(bullish_mask, 0.40 + 0.35 * body_pct, deliv_ratio)

            # High relative volume expansion -> additional institutional delivery bump
            vol_ma = vol.rolling(20, min_periods=5).mean()
            vol_spike = (vol > 1.3 * vol_ma)
            deliv_ratio = np.where(bullish_mask & vol_spike, deliv_ratio + 0.10, deliv_ratio)

        # Cap ratio between 15% and 85%
        deliv_ratio = np.clip(deliv_ratio, 0.15, 0.85)

        # Fill NaNs in Delivery_Quantity
        estimated_deliv = (vol * deliv_ratio).round().astype("float64")
        df[deliv_col] = df[deliv_col].fillna(estimated_deliv)

    return df


def compute_delivery_momentum_signal(
    df: pd.DataFrame,
    delivery_col: str = "Delivery_Quantity",
    traded_col: str = "Traded_Quantity",
    close_col: str = "Close",
    symbol_col: Optional[str] = "Symbol",
    date_col: Optional[str] = "Timestamp",
    recent_window: int = 5,
    baseline_window: int = 22,
    baseline_gap: int = 5,
    min_baseline_days: int = 15,
    min_recent_days: int = 3,
    delivery_lag: int = 0,
    auto_populate_missing: bool = False,
) -> pd.DataFrame:
    """
    Computes the Delivery Momentum Signal for a given DataFrame of stock data.

    Parameters:
    -----------
    df : pd.DataFrame
        DataFrame containing price, traded quantity, and delivery quantity columns.
    delivery_col : str
        Column name for Delivery Quantity (shares settled).
    traded_col : str
        Column name for Traded Quantity (total volume).
    close_col : str
        Column name for EOD Close Price.
    symbol_col : str, optional
        Column name for Ticker / Stock symbol (for grouping). If None, treats df as single stock.
    date_col : str, optional
        Column name for Date/Timestamp. If present, df is sorted by date ascending.
    recent_window : int, default 5
        Size of recent window in trading days (t-4 to t).
    baseline_window : int, default 22
        Size of baseline window in trading days (t-26 to t-5).
    baseline_gap : int, default 5
        Offset gap separating baseline window end from current day t (baseline ends at t - baseline_gap).
        For recent_window=5, baseline_gap=5 ensures strictly non-overlapping windows (ends at t-5).
    min_baseline_days : int, default 15
        Minimum valid non-NaN days required in baseline window.
    min_recent_days : int, default 3
        Minimum valid non-NaN days required in recent window.
    delivery_lag : int, default 0
        Number of trading days to shift delivery data to account for publication lag (e.g. 1 for T+1).
    auto_populate_missing : bool, default True
        If True, estimates delivery quantity when missing/all-NaN from volume dynamics.

    Returns:
    --------
    pd.DataFrame
        DataFrame with original index preserved containing calculated columns:
        ['Recent_DV', 'Baseline_DV', 'Recent_Deliv_Pct', 'Baseline_Deliv_Pct', 'Delivery_Signal', 'Signal']
    """
    if df.empty:
        empty_df = pd.DataFrame(
            index=df.index,
            columns=[
                "Recent_DV", "Baseline_DV",
                "Recent_Deliv_Pct", "Baseline_Deliv_Pct",
                "Recent_Deliv%", "Baseline_Deliv%",
                "Signal", "Delivery_Signal", "delivery_quantity",
            ],
        )
        return empty_df

    # Auto-populate missing delivery data if requested
    if auto_populate_missing:
        df = populate_missing_delivery_data(df)

    # Auto-detect column aliases if defaults are not present
    input_cols = {col.lower(): col for col in df.columns}
    
    deliv_c = _resolve_col(delivery_col, input_cols, ["delivery_quantity", "delivery_qty", "delivery_vol", "deliv_qty", "deliveryquantity"])
    traded_c = _resolve_col(traded_col, input_cols, ["traded_quantity", "traded_qty", "volume", "tradedquantity", "vol"])
    close_c = _resolve_col(close_col, input_cols, ["close", "eod_close", "close_price"])
    
    if not deliv_c or not traded_c or not close_c:
        missing = []
        if not deliv_c: missing.append(f"Delivery Quantity ('{delivery_col}')")
        if not traded_c: missing.append(f"Traded Quantity ('{traded_col}')")
        if not close_c: missing.append(f"Close Price ('{close_col}')")
        raise KeyError(f"Missing required input column(s): {', '.join(missing)}")

    sym_c = symbol_col if (symbol_col and symbol_col in df.columns) else None
    if not sym_c and "ticker" in input_cols:
        sym_c = input_cols["ticker"]
    elif not sym_c and "symbol" in input_cols:
        sym_c = input_cols["symbol"]

    dt_c = date_col if (date_col and date_col in df.columns) else None
    if not dt_c and "timestamp" in input_cols:
        dt_c = input_cols["timestamp"]
    elif not dt_c and "date" in input_cols:
        dt_c = input_cols["date"]

    # Work on a copy with index preserved
    work_df = df.copy()
    work_df["_orig_idx"] = work_df.index

    def _process_group(group: pd.DataFrame) -> pd.DataFrame:
        if dt_c:
            group = group.sort_values(by=dt_c, ascending=True)

        # Extract numeric series
        deliv_q = pd.to_numeric(group[deliv_c], errors="coerce")
        traded_q = pd.to_numeric(group[traded_c], errors="coerce")
        close_p = pd.to_numeric(group[close_c], errors="coerce")

        # Handle T+1 delivery publication lag if specified
        if delivery_lag > 0:
            deliv_q = deliv_q.shift(delivery_lag)

        # Step 1: Delivery Value
        delivery_val = deliv_q * close_p

        # Step 2: Delivery Percentage (safely handle division by zero / negative / invalid)
        deliv_pct = np.where(
            (traded_q > 0) & (deliv_q >= 0),
            deliv_q / traded_q,
            np.nan,
        )
        deliv_pct = pd.Series(deliv_pct, index=group.index)

        # Step 3 & 4: Non-overlapping rolling window means
        # Recent window: last 5 days (t-4 to t)
        recent_dv = delivery_val.rolling(window=recent_window, min_periods=min_recent_days).mean()
        recent_deliv_pct = deliv_pct.rolling(window=recent_window, min_periods=min_recent_days).mean()

        # Baseline window: 22 days immediately BEFORE recent window (t-26 to t-5)
        # baseline_gap = 5 shifts the 22-day rolling window by 5 days
        baseline_dv = delivery_val.rolling(window=baseline_window, min_periods=min_baseline_days).mean().shift(baseline_gap)
        baseline_deliv_pct = deliv_pct.rolling(window=baseline_window, min_periods=min_baseline_days).mean().shift(baseline_gap)

        # Step 5: Composite Signal
        # Signal = True if ALL hold: Recent_DV > Baseline_DV AND Recent_Deliv% > Baseline_Deliv%
        sig_cond = (
            recent_dv.notna() & baseline_dv.notna() &
            recent_deliv_pct.notna() & baseline_deliv_pct.notna() &
            (recent_dv > baseline_dv) &
            (recent_deliv_pct > baseline_deliv_pct)
        )

        res = pd.DataFrame({
            "_orig_idx": group["_orig_idx"],
            "delivery_quantity": deliv_q,
            "Recent_DV": recent_dv,
            "Baseline_DV": baseline_dv,
            "Recent_Deliv_Pct": recent_deliv_pct,
            "Baseline_Deliv_Pct": baseline_deliv_pct,
            "Recent_Deliv%": recent_deliv_pct,
            "Baseline_Deliv%": baseline_deliv_pct,
            "Signal": sig_cond,
            "Delivery_Signal": sig_cond.astype(bool),
        })

        return res

    if sym_c:
        try:
            results = work_df.groupby(sym_c, group_keys=False).apply(_process_group, include_groups=False)
        except TypeError:  # pandas < 2.2 fallback
            results = work_df.groupby(sym_c, group_keys=False).apply(_process_group)
    else:
        results = _process_group(work_df)

    # Restore original DataFrame index ordering
    results = results.set_index("_orig_idx").reindex(df.index)
    return results


def _resolve_col(given_name: str, input_cols_map: dict[str, str], candidates: list[str]) -> Optional[str]:
    """Helper to find column name by exact match or candidate alias."""
    if given_name in input_cols_map.values():
        return given_name
    given_lower = given_name.lower()
    if given_lower in input_cols_map:
        return input_cols_map[given_lower]
    for c in candidates:
        if c in input_cols_map:
            return input_cols_map[c]
    return None


def import_nse_delivery_csv(csv_path: str, db_connection=None) -> int:
    """
    Parses an NSE delivery report CSV file (e.g. sec_bhavdata_full.csv or MTO file)
    and updates the delivery_quantity column in the ohlc_data database table.

    Expected CSV columns (NSE standard):
    - SYMBOL / Ticker
    - DATE / DATE1 / CHNG_DATE
    - DELIV_QTY / DELIV_PER / Delivery_Quantity

    Returns total number of rows updated in MySQL.
    """
    import pandas as pd
    from database import get_connection

    df = pd.read_csv(csv_path)
    df.columns = [c.strip().upper() for c in df.columns]

    sym_col = next((c for c in ["SYMBOL", "TICKER"] if c in df.columns), None)
    date_col = next((c for c in ["DATE", "DATE1", "TIMESTAMP", "TRADING_DATE"] if c in df.columns), None)
    deliv_col = next((c for c in ["DELIV_QTY", "DELIVERY_QTY", "DELIVERY_QUANTITY"] if c in df.columns), None)

    if not sym_col or not date_col or not deliv_col:
        raise ValueError(f"CSV missing required NSE columns. Found: {list(df.columns)}")

    df["TICKER_NS"] = df[sym_col].astype(str).str.strip() + ".NS"
    df["DATE_PARSED"] = pd.to_datetime(df[date_col]).dt.date
    df["DELIV_QTY_NUM"] = pd.to_numeric(df[deliv_col], errors="coerce").fillna(0).astype("int64")

    records = list(df[["TICKER_NS", "DATE_PARSED", "DELIV_QTY_NUM"]].itertuples(index=False, name=None))

    conn = db_connection or get_connection()
    close_conn = db_connection is None
    cursor = conn.cursor()

    update_query = """
    UPDATE ohlc_data
    SET delivery_quantity = %s
    WHERE ticker = %s AND date = %s
    """
    batch_records = [(r[2], r[0], r[1]) for r in records]
    
    total_updated = 0
    batch_size = 5000
    for i in range(0, len(batch_records), batch_size):
        batch = batch_records[i : i + batch_size]
        cursor.executemany(update_query, batch)
        total_updated += cursor.rowcount

    conn.commit()
    cursor.close()
    if close_conn:
        conn.close()

    logger.info("Successfully updated %d delivery records in ohlc_data from %s", total_updated, csv_path)
    return total_updated
