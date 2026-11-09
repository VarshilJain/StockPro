"""
aged_ath_screener.py
=============================================================================
StockPro Pattern Screens: Aged All-Time High (ATH) Screener
=============================================================================

GOAL:
  Flag stocks whose All-Time High (ATH) is more than 2 years old and whose
  current price is within 20% below that ATH. The ATH is computed over the
  stock's entire listed history.

DATA-QUALITY SAFEGUARDS:
  - Detects and repairs/ignores single-day bad ticks (>40% spike that reverts next day)
  - Rate limiting handling with exponential backoff & jitter
  - Local caching (pickle format) to ensure high-speed reruns
  - Full validation of missing data, listing age, and price anomalies

EXTRA QUALITY & RANKING METRICS:
  - Distance tiers (Within 5%, Within 10%, Within 20%, or Breakout)
  - Price above 200-DMA (True / False)
  - 50-DMA slope (Rising / Falling based on 20-day trend)
  - Breakout flag (Current close >= Old ATH)
  - 52-week range position (%)
  - 1-Year trailing return (%)
  - Exact ATH age and Listing age in years

INCLUDES:
  - Ranked output table sorted by distance_pct ascending (top 25 displayed)
  - CSV output export
  - High-res visualization function (horizontal ATH line, shaded band, DMAs)
  - Point-in-time forward return Backtest Engine vs. Nifty 500 benchmark

REQUIREMENTS:
  pip install yfinance pandas numpy matplotlib
=============================================================================
"""

from __future__ import annotations

import abc
import argparse
from datetime import datetime, date, timedelta
import logging
import os
from pathlib import Path
import random
import sys
import time
from typing import Dict, Any, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
# Use non-interactive backend when running headless or saving
matplotlib.use("Agg" if not os.environ.get("DISPLAY") and sys.platform != "win32" else "TkAgg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# =============================================================================
# INPUTS / CONFIGURATION (Editable Parameters)
# =============================================================================
MIN_ATH_AGE_YEARS: float = 2.0           # ATH must be older than 2.0 years
MAX_DISTANCE_FROM_ATH_PCT: float = 20.0  # Current price within 20% of ATH
MIN_LISTING_AGE_YEARS: float = 3.0      # Minimum stock listed history (years)
PRICE_BASIS: str = "close"              # Options: "close" or "high"

DEFAULT_UNIVERSE: str = "all"           # Options: "all", "nifty500", "csv"
DEFAULT_CSV_PATH: str = "nse_stock_market_caps.csv"
BENCHMARK_TICKER: str = "^CRSLDX"       # Nifty 500 Index (^NSEI as fallback)

CACHE_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_data", "aged_ath")
CACHE_EXPIRY_HOURS: float = 24.0        # Cache validity window in hours
OUTPUT_CSV_FILE: str = "aged_ath_screen_results.csv"

# Moving average parameters
DMA_50_PERIOD: int = 50
DMA_200_PERIOD: int = 200
DMA_50_SLOPE_LOOKBACK: int = 20         # Bars to measure 50-DMA trajectory

# Data quality thresholds
BAD_TICK_SPIKE_PCT: float = 0.40        # >40% 1-day move
BAD_TICK_REVERSION_PCT: float = 0.25    # >25% reversal next day
BAD_TICK_NET_TOLERANCE_PCT: float = 0.15 # Net move between day t-1 and t+1 < 15%

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AgedATH")


# =============================================================================
# DATA QUALITY SAFEGUARDS
# =============================================================================

def clean_bad_ticks(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """
    Detect and correct obvious bad ticks (e.g. single-day moves >40% that revert next day).
    Also filters non-positive prices and corrupted OHLC bars.
    """
    if df is None or len(df) < 5:
        return df

    df = df.copy()

    # Ensure required columns exist
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            return df
        # Coerce numeric
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Remove non-positive or NaN prices
    valid_mask = (df["Close"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0)
    df = df[valid_mask]
    if len(df) < 5:
        return df

    # Enforce High >= Low integrity
    df["High"] = np.maximum(df["High"], df[["Open", "Close"]].max(axis=1))
    df["Low"] = np.minimum(df["Low"], df[["Open", "Close"]].min(axis=1))

    # Vectorized / rolling check for 1-day transitory spikes / flash crashes
    closes = df["Close"].values
    dates = df.index
    cleaned_count = 0

    # Iterate through internal bars to inspect spike-reversion
    for i in range(1, len(closes) - 1):
        prev_c = closes[i - 1]
        curr_c = closes[i]
        next_c = closes[i + 1]

        if prev_c <= 0 or curr_c <= 0 or next_c <= 0:
            continue

        r1 = (curr_c - prev_c) / prev_c
        r2 = (next_c - curr_c) / curr_c
        r_net = abs((next_c - prev_c) / prev_c)

        # Condition: Single-day spike/drop > 40%, next day reverts by > 25% opposite sign,
        # and net move between t-1 and t+1 is small (< 15%)
        is_bad_spike = (r1 > BAD_TICK_SPIKE_PCT and r2 < -BAD_TICK_REVERSION_PCT and r_net < BAD_TICK_NET_TOLERANCE_PCT)
        is_bad_crash = (r1 < -BAD_TICK_SPIKE_PCT and r2 > BAD_TICK_REVERSION_PCT and r_net < BAD_TICK_NET_TOLERANCE_PCT)

        if is_bad_spike or is_bad_crash:
            fixed_val = (prev_c + next_c) / 2.0
            closes[i] = fixed_val
            df.iat[i, df.columns.get_loc("Close")] = fixed_val
            df.iat[i, df.columns.get_loc("Open")] = fixed_val
            df.iat[i, df.columns.get_loc("High")] = max(prev_c, next_c)
            df.iat[i, df.columns.get_loc("Low")] = min(prev_c, next_c)
            cleaned_count += 1
            logger.debug("Repaired bad tick for %s on %s (spike=%.1f%%, rev=%.1f%%)",
                         ticker, dates[i], r1 * 100, r2 * 100)

    if cleaned_count > 0:
        logger.info("Cleaned %d anomalous bad tick(s) for %s", cleaned_count, ticker)

    return df


# =============================================================================
# DATA PROVIDER INTERFACE & CACHING LAYER
# =============================================================================

class BaseDataProvider(abc.ABC):
    """Abstract data provider interface allowing pluggable market data sources."""

    @abc.abstractmethod
    def fetch_history(self, ticker: str) -> Optional[pd.DataFrame]:
        """Fetch full historical daily OHLCV dataframe since listing."""
        pass


class YFinanceDataProvider(BaseDataProvider):
    """
    yfinance data provider with disk caching, rate limiting backoff,
    and automated bad tick cleaning.
    """

    def __init__(self, cache_dir: str = CACHE_DIR, cache_expiry_hours: float = CACHE_EXPIRY_HOURS):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_expiry_hours = cache_expiry_hours

    def _get_cache_path(self, ticker: str) -> Path:
        clean_name = ticker.replace("^", "IDX_").replace(".NS", "").replace("/", "_")
        return self.cache_dir / f"{clean_name}.pkl"

    def fetch_history(self, ticker: str, force_refresh: bool = False) -> Optional[pd.DataFrame]:
        cache_path = self._get_cache_path(ticker)

        # Check local cache validity
        if not force_refresh and cache_path.exists():
            try:
                mtime = cache_path.stat().st_mtime
                age_hours = (time.time() - mtime) / 3600.0
                if age_hours <= self.cache_expiry_hours:
                    df = pd.read_pickle(cache_path)
                    if isinstance(df, pd.DataFrame) and len(df) > 0:
                        return df
            except Exception as e:
                logger.debug("Failed reading cache for %s: %s", ticker, e)

        # Download from yfinance with retry and exponential backoff
        max_retries = 4
        backoff = 1.5

        for attempt in range(1, max_retries + 1):
            try:
                # Add .NS suffix if missing and not an index
                yf_symbol = ticker
                if not yf_symbol.startswith("^") and not yf_symbol.endswith(".NS"):
                    yf_symbol = f"{yf_symbol}.NS"

                t = yf.Ticker(yf_symbol)
                df = t.history(period="max", auto_adjust=True)

                if df is None or len(df) == 0:
                    # Retry once without suffix or fallback
                    if yf_symbol.endswith(".NS"):
                        alt_sym = yf_symbol[:-3]
                        df = yf.Ticker(alt_sym).history(period="max", auto_adjust=True)

                if df is not None and len(df) > 0:
                    # Format index
                    if isinstance(df.index, pd.DatetimeIndex):
                        if df.index.tz is not None:
                            df.index = df.index.tz_localize(None)
                    df.sort_index(inplace=True)

                    # Clean bad data & ticks
                    df = clean_bad_ticks(df, ticker=ticker)

                    # Save to local cache
                    try:
                        df.to_pickle(cache_path)
                    except Exception as ce:
                        logger.debug("Cache write failed for %s: %s", ticker, ce)

                    return df

                # If empty result, wait and retry briefly
                time.sleep(backoff)
            except Exception as exc:
                err_str = str(exc).lower()
                is_rate_limited = "rate" in err_str or "too many requests" in err_str or "429" in err_str
                if attempt == max_retries:
                    logger.warning("Failed to download %s after %d retries: %s", ticker, max_retries, exc)
                    return None
                wait_time = (backoff ** attempt) + random.uniform(0.5, 1.5)
                if is_rate_limited:
                    wait_time += 3.0
                    logger.warning("Rate limit encountered for %s. Backing off for %.1fs...", ticker, wait_time)
                time.sleep(wait_time)

        return None


class LocalDBDataProvider(BaseDataProvider):
    """
    Optional swappable data provider pulling directly from StockPro's MySQL database.
    """

    def __init__(self, fallback_provider: Optional[BaseDataProvider] = None):
        self.fallback_provider = fallback_provider or YFinanceDataProvider()

    def fetch_history(self, ticker: str) -> Optional[pd.DataFrame]:
        try:
            from database import get_connection
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)
            # Query full daily history from ohlc_data or historical_data
            clean_sym = ticker.replace(".NS", "")
            cursor.execute("""
                SELECT Timestamp as Date, Open, High, Low, Close, Volume
                FROM ohlc_data
                WHERE Symbol = %s OR Symbol = %s
                ORDER BY Timestamp ASC
            """, (clean_sym, f"{clean_sym}.NS"))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if rows and len(rows) > 100:
                df = pd.DataFrame(rows)
                df["Date"] = pd.to_datetime(df["Date"])
                df.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                return clean_bad_ticks(df, ticker=ticker)
        except Exception as e:
            logger.debug("LocalDBDataProvider query failed for %s: %s; falling back", ticker, e)

        return self.fallback_provider.fetch_history(ticker)


# =============================================================================
# UNIVERSE RESOLVER
# =============================================================================

def get_universe(universe_type: str = DEFAULT_UNIVERSE,
                 csv_path: Optional[str] = None,
                 limit: Optional[int] = None) -> List[Tuple[str, str]]:
    """
    Resolve stock universe into list of (symbol, company_name) tuples.
    Supports Nifty 500, custom CSV, and all listed NSE stocks.
    """
    results: List[Tuple[str, str]] = []

    # Priority 1: User-specified custom CSV
    candidate_csv = csv_path or DEFAULT_CSV_PATH
    if os.path.exists(candidate_csv):
        try:
            df = pd.read_csv(candidate_csv)
            # Find symbol column
            sym_col = next((c for c in df.columns if c.lower() in ["symbol", "ticker", "stock"]), None)
            name_col = next((c for c in df.columns if c.lower() in ["company_name", "company", "name"]), None)

            if sym_col:
                # If Nifty 500 requested and market_cap column is present, sort top 500
                if universe_type.lower() == "nifty500" and "market_cap_cr" in df.columns:
                    df["market_cap_cr"] = pd.to_numeric(df["market_cap_cr"], errors="coerce")
                    df = df.sort_values(by="market_cap_cr", ascending=False)
                    if limit is None:
                        limit = 500

                if limit:
                    df = df.head(limit)

                for _, row in df.iterrows():
                    sym = str(row[sym_col]).strip()
                    if not sym or sym.lower() == "nan":
                        continue
                    if not sym.endswith(".NS") and not sym.startswith("^"):
                        sym = f"{sym}.NS"
                    comp_name = str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) else sym
                    results.append((sym, comp_name))

                logger.info("Loaded %d stocks from %s (universe=%s)", len(results), candidate_csv, universe_type)
                return results
        except Exception as e:
            logger.warning("Could not parse CSV %s: %s", candidate_csv, e)

    # Priority 2: Fallback to symbols_list.py
    try:
        from symbols_list import SYMBOLS as RAW_SYMBOLS
        syms = RAW_SYMBOLS[:limit] if limit else RAW_SYMBOLS
        logger.info("Falling back to symbols_list.py with %d symbols", len(syms))
        return [(s, s.replace(".NS", "")) for s in syms]
    except Exception as exc:
        logger.error("Unable to load universe: %s", exc)

    # Minimal fallback default top Indian bluechips
    fallback = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "BHARTIARTL.NS", "ICICIBANK.NS",
                "SBIN.NS", "INFY.NS", "LICI.NS", "ITC.NS", "LT.NS"]
    return [(s, s.replace(".NS", "")) for s in fallback]


# =============================================================================
# CORE AGED ATH SCREENING LOGIC
# =============================================================================

def evaluate_aged_ath_stock(ticker: str,
                            df: pd.DataFrame,
                            company_name: str = "",
                            min_ath_age_years: float = MIN_ATH_AGE_YEARS,
                            max_distance_from_ath_pct: float = MAX_DISTANCE_FROM_ATH_PCT,
                            min_listing_age_years: float = MIN_LISTING_AGE_YEARS,
                            price_basis: str = PRICE_BASIS) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Evaluate a single stock's full historical series against Aged ATH criteria.

    Returns:
      (result_dict, None) if qualified
      (None, skip_reason) if disqualified or insufficient data
    """
    if df is None or len(df) < 252:
        return None, f"Insufficient price history (total bars: {len(df) if df is not None else 0} < 252)"

    basis_col = "Close" if price_basis.lower() == "close" else "High"
    if basis_col not in df.columns or "Close" not in df.columns:
        return None, f"Missing required price column '{basis_col}'"

    # 1. Listing Age validation
    first_date = df.index[0]
    last_date = df.index[-1]
    listing_age_years = (last_date - first_date).days / 365.25

    if listing_age_years < min_listing_age_years:
        return None, f"Listing age {listing_age_years:.2f} yrs < {min_listing_age_years:.1f} yrs minimum"

    # 2. ATH & Date Computation
    # Check if a multi-year aged ATH exists.
    # Note: If stock recently made a fresh breakout (e.g. within last 30 trading days)
    # exceeding a 2+ year old peak, we detect the aged benchmark ATH to preserve breakout_flag.
    today_dt = last_date
    current_close = float(df["Close"].iloc[-1])

    series_basis = df[basis_col]
    overall_ath = float(series_basis.max())
    overall_ath_date = series_basis.idxmax()
    overall_ath_age_years = (today_dt - overall_ath_date).days / 365.25

    # Case A: Pure Aged ATH (all-time peak occurred > min_ath_age_years ago)
    if overall_ath_age_years >= min_ath_age_years:
        ath = overall_ath
        ath_date = overall_ath_date
        ath_age_years = overall_ath_age_years
    else:
        # Case B: Recent Breakout check.
        # Check if the stock was below an aged peak up until the last 30 trading days.
        recent_window_days = 30
        prior_slice = series_basis.iloc[:-recent_window_days] if len(series_basis) > recent_window_days else None

        if prior_slice is not None and len(prior_slice) > 0:
            prior_ath = float(prior_slice.max())
            prior_ath_date = prior_slice.idxmax()
            prior_ath_age_years = (today_dt - prior_ath_date).days / 365.25

            # If the prior peak was >= min_ath_age_years and current close has broken above it
            if prior_ath_age_years >= min_ath_age_years and current_close >= prior_ath:
                ath = prior_ath
                ath_date = prior_ath_date
                ath_age_years = prior_ath_age_years
            else:
                return None, f"ATH is recent ({overall_ath_age_years:.2f} yrs old on {overall_ath_date.strftime('%Y-%m-%d')})"
        else:
            return None, f"ATH is recent ({overall_ath_age_years:.2f} yrs old on {overall_ath_date.strftime('%Y-%m-%d')})"

    # 3. Distance from ATH (%)
    # distance_pct = (ATH - current_close) / ATH * 100
    if ath <= 0:
        return None, "Invalid ATH value <= 0"

    distance_pct = ((ath - current_close) / ath) * 100.0

    # 4. Qualification Filter
    # Distance must be <= max_distance_from_ath_pct (e.g. within 20% below ATH)
    # and not an extreme unadjusted split anomaly (distance_pct >= -25%)
    if distance_pct > max_distance_from_ath_pct or distance_pct < -25.0:
        return None, f"Distance from ATH {distance_pct:.2f}% out of valid range [-25.0%, {max_distance_from_ath_pct:.1f}%]"

    # 5. Extra Technical Fields & Indicators
    # Moving Averages
    close_series = df["Close"]
    sma_200 = close_series.rolling(window=DMA_200_PERIOD).mean()
    sma_50 = close_series.rolling(window=DMA_50_PERIOD).mean()

    curr_sma_200 = float(sma_200.iloc[-1]) if pd.notna(sma_200.iloc[-1]) else np.nan
    curr_sma_50 = float(sma_50.iloc[-1]) if pd.notna(sma_50.iloc[-1]) else np.nan

    # Trend Flags
    price_above_200_dma = bool(current_close > curr_sma_200) if pd.notna(curr_sma_200) else False

    # 50-DMA slope over DMA_50_SLOPE_LOOKBACK bars
    if len(sma_50) > DMA_50_SLOPE_LOOKBACK and pd.notna(sma_50.iloc[-DMA_50_SLOPE_LOOKBACK]):
        dma_50_past = float(sma_50.iloc[-DMA_50_SLOPE_LOOKBACK])
        dma_50_slope = "rising" if curr_sma_50 > dma_50_past else "falling"
    else:
        dma_50_slope = "neutral"

    # Breakout Flag: Current close >= Old ATH
    breakout_flag = bool(current_close >= ath)

    # Distance Tier classification
    if distance_pct < 0.0:
        tier = "Breakout (<0%)"
    elif distance_pct <= 5.0:
        tier = "Within 5%"
    elif distance_pct <= 10.0:
        tier = "Within 10%"
    else:
        tier = "Within 20%"

    # 52-Week Range Position (%)
    past_52w = df.iloc[-252:] if len(df) >= 252 else df
    low_52w = float(past_52w["Low"].min())
    high_52w = float(past_52w["High"].max())

    if high_52w > low_52w:
        range_52w_position_pct = ((current_close - low_52w) / (high_52w - low_52w)) * 100.0
    else:
        range_52w_position_pct = 50.0

    # 1-Year Return (%)
    if len(df) >= 252:
        close_1y_ago = float(df["Close"].iloc[-252])
        one_year_return_pct = ((current_close - close_1y_ago) / close_1y_ago) * 100.0
    else:
        one_year_return_pct = 0.0

    result = {
        "ticker": ticker,
        "company_name": company_name or ticker.replace(".NS", ""),
        "current_price": round(current_close, 2),
        "ath": round(ath, 2),
        "ath_date": ath_date.strftime("%Y-%m-%d"),
        "ath_age_years": round(ath_age_years, 2),
        "distance_pct": round(distance_pct, 2),
        "tier": tier,
        "price_above_200_dma": price_above_200_dma,
        "dma_50_slope": dma_50_slope,
        "breakout_flag": breakout_flag,
        "range_52w_pct": round(range_52w_position_pct, 1),
        "one_year_return_pct": round(one_year_return_pct, 1),
        "listing_age_years": round(listing_age_years, 2),
    }

    return result, None


# =============================================================================
# FULL SCREEN EXECUTION ENGINE
# =============================================================================

def run_aged_ath_screener(universe_type: str = DEFAULT_UNIVERSE,
                          csv_path: Optional[str] = None,
                          custom_symbols: Optional[List[str]] = None,
                          min_ath_age_years: float = MIN_ATH_AGE_YEARS,
                          max_distance_from_ath_pct: float = MAX_DISTANCE_FROM_ATH_PCT,
                          min_listing_age_years: float = MIN_LISTING_AGE_YEARS,
                          price_basis: str = PRICE_BASIS,
                          data_provider: Optional[BaseDataProvider] = None,
                          save_csv_path: Optional[str] = OUTPUT_CSV_FILE,
                          limit_universe: Optional[int] = None,
                          workers: int = 8,
                          quiet_logging: bool = False) -> pd.DataFrame:
    """
    Execute the Aged All-Time High screener across the selected stock universe.
    Ranks qualifying setups by distance_pct ascending, prints top 25, and saves CSV.
    """
    import concurrent.futures

    if data_provider is None:
        data_provider = YFinanceDataProvider()

    if custom_symbols:
        universe = [(s, s.replace(".NS", "")) for s in custom_symbols]
    else:
        universe = get_universe(universe_type=universe_type, csv_path=csv_path, limit=limit_universe)

    total_stocks = len(universe)
    logger.info("Starting Aged ATH Screener across %d stocks (workers=%d)...", total_stocks, workers)
    logger.info("Criteria: ATH Age >= %.1f yrs | Distance <= %.1f%% | Listing Age >= %.1f yrs | Basis: %s",
                min_ath_age_years, max_distance_from_ath_pct, min_listing_age_years, price_basis)

    qualified_records: List[Dict[str, Any]] = []
    skipped_summary: Dict[str, int] = {}

    start_time = time.time()
    completed_count = 0

    def process_symbol(item: Tuple[str, str]):
        sym, name = item
        try:
            df = data_provider.fetch_history(sym)
            if df is None:
                return None, "Download / Data fetch failed"
            return evaluate_aged_ath_stock(
                ticker=sym,
                df=df,
                company_name=name,
                min_ath_age_years=min_ath_age_years,
                max_distance_from_ath_pct=max_distance_from_ath_pct,
                min_listing_age_years=min_listing_age_years,
                price_basis=price_basis,
            )
        except Exception as e:
            return None, f"Execution error: {type(e).__name__}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(process_symbol, item): item for item in universe}
        for future in concurrent.futures.as_completed(future_map):
            completed_count += 1
            if not quiet_logging and (completed_count % 50 == 0 or completed_count == total_stocks):
                logger.info("Progress: %d/%d (%.1f%%) | Qualified so far: %d",
                            completed_count, total_stocks, (completed_count / total_stocks) * 100, len(qualified_records))
            res, skip_reason = future.result()
            if res is not None:
                qualified_records.append(res)
            elif skip_reason:
                cat = skip_reason.split("(")[0].strip()
                skipped_summary[cat] = skipped_summary.get(cat, 0) + 1

    elapsed = time.time() - start_time
    logger.info("Screen complete in %.1f seconds. Found %d qualifying stocks.", elapsed, len(qualified_records))

    # Log skipped reason breakdown
    logger.info("Disqualification Breakdown:")
    for reason, count in sorted(skipped_summary.items(), key=lambda x: x[1], reverse=True)[:6]:
        logger.info("  - %-40s: %d", reason, count)

    if not qualified_records:
        logger.warning("No stocks matched the screening criteria.")
        return pd.DataFrame()

    # Create ranked DataFrame
    results_df = pd.DataFrame(qualified_records)
    # Primary sort: distance_pct ascending (closest to ATH or broken out first)
    results_df.sort_values(by=["distance_pct", "ath_age_years"], ascending=[True, False], inplace=True)
    results_df.reset_index(drop=True, inplace=True)

    # Save to CSV
    if save_csv_path:
        results_df.to_csv(save_csv_path, index=False)
        logger.info("Saved complete results (%d rows) to %s", len(results_df), save_csv_path)

    # Print Top 25
    print_top_results(results_df, top_n=25)

    return results_df


def print_top_results(df: pd.DataFrame, top_n: int = 25) -> None:
    """Pretty-print the top N screening results in a clean formatted ASCII table."""
    if df is None or len(df) == 0:
        print("\nNo qualifying Aged ATH setups to display.\n")
        return

    top = df.head(top_n)

    header = (
        f"\n{'='*125}\n"
        f"  AGED ALL-TIME HIGH (ATH) SCREENER — TOP {len(top)} QUALIFYING STOCKS\n"
        f"{'='*125}\n"
        f"{'Rank':<5} {'Ticker':<14} {'CMP (Rs)':>10} {'ATH (Rs)':>10} {'ATH Date':<11} "
        f"{'Age(Yr)':>8} {'Dist %':>8} {'Tier':<14} {'200-DMA':<8} {'50-Slope':<9} {'Breakout':<9} {'1Y-Ret%':>8}\n"
        f"{'-'*125}"
    )
    print(header)

    for i, row in top.iterrows():
        dma200_str = "Above" if row["price_above_200_dma"] else "Below"
        bo_str = "YES" if row["breakout_flag"] else "No"
        print(
            f"{i+1:<5} {row['ticker']:<14} {row['current_price']:>10.2f} {row['ath']:>10.2f} "
            f"{row['ath_date']:<11} {row['ath_age_years']:>8.1f} {row['distance_pct']:>8.1f}% "
            f"{row['tier']:<14} {dma200_str:<8} {row['dma_50_slope']:<9} {bo_str:<9} {row['one_year_return_pct']:>7.1f}%"
        )

    print(f"{'='*125}\n")


# =============================================================================
# CHARTING & VISUALIZATION FUNCTION
# =============================================================================

def plot_aged_ath(ticker: str,
                  df: Optional[pd.DataFrame] = None,
                  data_provider: Optional[BaseDataProvider] = None,
                  save_path: Optional[str] = None,
                  show: bool = False,
                  price_basis: str = PRICE_BASIS) -> Optional[str]:
    """
    Generate a publication-grade chart showing:
      - Stock price over history
      - ATH horizontal reference line with price label
      - Marked ATH date with vertical reference line
      - Shaded consolidation band from 80% of ATH to ATH (or user MAX_DISTANCE_FROM_ATH_PCT)
      - 50-DMA and 200-DMA indicators
      - Current price and distance callout
    """
    if df is None:
        provider = data_provider or YFinanceDataProvider()
        df = provider.fetch_history(ticker)

    if df is None or len(df) < 50:
        logger.error("Cannot plot %s: insufficient data", ticker)
        return None

    basis_col = "Close" if price_basis.lower() == "close" else "High"
    series = df[basis_col]

    ath = float(series.max())
    ath_date = series.idxmax()
    ath_age_years = (df.index[-1] - ath_date).days / 365.25
    curr_close = float(df["Close"].iloc[-1])
    dist_pct = ((ath - curr_close) / ath) * 100.0

    # Moving averages
    sma50 = df["Close"].rolling(50).mean()
    sma200 = df["Close"].rolling(200).mean()

    # Band levels
    band_lower = ath * (1.0 - MAX_DISTANCE_FROM_ATH_PCT / 100.0)
    band_upper = ath

    # Chart setup (Dark themed, crisp)
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(14, 7), dpi=150)

    # Plot Price & DMAs
    ax.plot(df.index, df["Close"], label=f"{ticker} Close", color="#38bdf8", linewidth=1.5, zorder=3)
    ax.plot(df.index, sma50, label="50-DMA", color="#f59e0b", linestyle="--", linewidth=1.1, alpha=0.85)
    ax.plot(df.index, sma200, label="200-DMA", color="#ef4444", linestyle="-.", linewidth=1.1, alpha=0.85)

    # ATH Horizontal Line
    ax.axhline(y=ath, color="#10b981", linestyle="--", linewidth=1.8, label=f"ATH: Rs {ath:.2f}")

    # Shaded ATH Consolidation Band
    ax.axhspan(band_lower, band_upper, color="#10b981", alpha=0.15,
               label=f"Aged ATH Band ({100-MAX_DISTANCE_FROM_ATH_PCT:.0f}% - 100% of ATH)")

    # Mark ATH Date
    ax.axvline(x=ath_date, color="#a78bfa", linestyle=":", linewidth=1.4)
    ax.scatter([ath_date], [ath], color="#10b981", s=90, edgecolors="white", linewidth=1.5, zorder=5)
    ax.annotate(f"ATH Peak: Rs {ath:.2f}\n{ath_date.strftime('%d-%b-%Y')}\n({ath_age_years:.1f} yrs ago)",
                xy=(ath_date, ath),
                xytext=(ath_date + pd.Timedelta(days=60), ath * 0.96),
                arrowprops=dict(facecolor="#10b981", shrink=0.08, width=1.5, headwidth=6),
                bbox=dict(boxstyle="round,pad=0.4", fc="#064e3b", ec="#10b981", alpha=0.9),
                fontsize=9, color="#ffffff")

    # Current Price Callout
    curr_date = df.index[-1]
    ax.scatter([curr_date], [curr_close], color="#38bdf8", s=80, edgecolors="white", zorder=5)
    ax.annotate(f"CMP: Rs {curr_close:.2f}\nDist: {dist_pct:.1f}%",
                xy=(curr_date, curr_close),
                xytext=(curr_date - pd.Timedelta(days=180), curr_close * 1.06),
                arrowprops=dict(facecolor="#38bdf8", shrink=0.08, width=1.2, headwidth=5),
                bbox=dict(boxstyle="round,pad=0.3", fc="#0f172a", ec="#38bdf8", alpha=0.85),
                fontsize=9, color="#ffffff")

    # Titles & Labels
    ax.set_title(f"Aged All-Time High Setup — {ticker}  |  ATH Age: {ath_age_years:.1f} Years  |  Distance: {dist_pct:.1f}%",
                 fontsize=13, fontweight="bold", pad=15, color="#f8fafc")
    ax.set_xlabel("Date", fontsize=10, labelpad=8, color="#94a3b8")
    ax.set_ylabel("Price (INR)", fontsize=10, labelpad=8, color="#94a3b8")

    # Formatting axes
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x:,.0f}"))
    ax.grid(True, linestyle=":", alpha=0.25, color="#64748b")
    ax.legend(loc="upper left", frameon=True, facecolor="#0f172a", edgecolor="#334155", fontsize=9)

    plt.tight_layout()

    out_file = save_path or f"aged_ath_{ticker.replace('.NS', '')}.png"
    plt.savefig(out_file, dpi=150)
    logger.info("Saved Aged ATH chart for %s to %s", ticker, out_file)

    if show:
        plt.show()

    plt.close()
    return out_file


# =============================================================================
# BACKTEST ENGINE (Point-In-Time Simulation vs Nifty 500)
# =============================================================================

def run_aged_ath_backtest(tickers: Optional[List[str]] = None,
                          benchmark_ticker: str = BENCHMARK_TICKER,
                          start_date: str = "2019-01-01",
                          end_date: Optional[str] = None,
                          sample_step_bars: int = 21,
                          holding_periods: Tuple[int, int, int] = (63, 126, 252),
                          data_provider: Optional[BaseDataProvider] = None) -> pd.DataFrame:
    """
    Point-in-Time Backtest of the Aged ATH setup:
      - Simulates screening signal strictly using historical data available up to date T (zero lookahead).
      - Tracks forward 3-month (63 bars), 6-month (126 bars), and 12-month (252 bars) returns.
      - Measures excess return (Alpha) over Nifty 500 index over exact identical windows.

    SURVIVORSHIP BIAS NOTICE:
      Note: Delisted or merged companies may be excluded from the current universe,
      which creates survivorship bias in historical performance figures.
    """
    provider = data_provider or YFinanceDataProvider()

    print("\n" + "="*90)
    print("  AGED ATH POINT-IN-TIME BACKTEST ENGINE")
    print("="*90)
    print(f"Holding Periods: 3M ({holding_periods[0]}d), 6M ({holding_periods[1]}d), 12M ({holding_periods[2]}d)")
    print(f"Benchmark: {benchmark_ticker} | Simulation Start Date: {start_date}")
    print("="*90 + "\n")

    # Fetch benchmark history
    logger.info("Fetching benchmark data for %s...", benchmark_ticker)
    bench_df = provider.fetch_history(benchmark_ticker)
    if bench_df is None or len(bench_df) < 500:
        logger.warning("Could not fetch %s, falling back to ^NSEI", benchmark_ticker)
        bench_df = provider.fetch_history("^NSEI")

    if bench_df is None:
        raise RuntimeError("Benchmark data unavailable for backtest.")

    bench_close = bench_df["Close"]

    # Select tickers for backtest
    if not tickers:
        universe = get_universe(universe_type="nifty500", limit=100)
        tickers = [s for s, _ in universe]

    results: List[Dict[str, Any]] = []
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date) if end_date else datetime.now() - pd.Timedelta(days=365)

    max_hold_bars = max(holding_periods)

    logger.info("Running point-in-time simulation across %d tickers...", len(tickers))

    for s_idx, sym in enumerate(tickers, start=1):
        df = provider.fetch_history(sym)
        if df is None or len(df) < 500:
            continue

        # Find starting bar index matching start_dt
        valid_indices = [i for i in range(len(df)) if df.index[i] >= start_dt and df.index[i] <= end_dt]
        if not valid_indices:
            continue

        # Step through evaluation dates (e.g. monthly sampling to avoid overlapping clustering)
        for bar_idx in range(valid_indices[0], valid_indices[-1], sample_step_bars):
            if bar_idx + max_hold_bars >= len(df):
                break

            # Strictly historical point-in-time slice up to bar_idx (No lookahead!)
            df_pit = df.iloc[: bar_idx + 1]
            eval_date = df_pit.index[-1]

            res, _ = evaluate_aged_ath_stock(
                ticker=sym,
                df=df_pit,
                min_ath_age_years=MIN_ATH_AGE_YEARS,
                max_distance_from_ath_pct=MAX_DISTANCE_FROM_ATH_PCT,
                min_listing_age_years=MIN_LISTING_AGE_YEARS,
                price_basis=PRICE_BASIS
            )

            if res is not None:
                entry_price = float(df["Close"].iloc[bar_idx])

                # Stock forward returns
                fwd_ret: Dict[str, float] = {}
                bench_ret: Dict[str, float] = {}

                for hold_bars in holding_periods:
                    label = f"{hold_bars}d"
                    fwd_price = float(df["Close"].iloc[bar_idx + hold_bars])
                    fwd_date = df.index[bar_idx + hold_bars]
                    stk_r = ((fwd_price - entry_price) / entry_price) * 100.0
                    fwd_ret[label] = stk_r

                    # Benchmark return over same date range
                    bench_at_entry = bench_close.asof(eval_date)
                    bench_at_exit = bench_close.asof(fwd_date)
                    if pd.notna(bench_at_entry) and pd.notna(bench_at_exit) and bench_at_entry > 0:
                        b_r = ((bench_at_exit - bench_at_entry) / bench_at_entry) * 100.0
                    else:
                        b_r = 0.0
                    bench_ret[label] = b_r

                results.append({
                    "ticker": sym,
                    "signal_date": eval_date.strftime("%Y-%m-%d"),
                    "entry_price": entry_price,
                    "ath": res["ath"],
                    "ath_age_years": res["ath_age_years"],
                    "distance_pct": res["distance_pct"],
                    "above_200_dma": res["price_above_200_dma"],
                    "ret_3m": fwd_ret.get(f"{holding_periods[0]}d", 0.0),
                    "bench_3m": bench_ret.get(f"{holding_periods[0]}d", 0.0),
                    "ret_6m": fwd_ret.get(f"{holding_periods[1]}d", 0.0),
                    "bench_6m": bench_ret.get(f"{holding_periods[1]}d", 0.0),
                    "ret_12m": fwd_ret.get(f"{holding_periods[2]}d", 0.0),
                    "bench_12m": bench_ret.get(f"{holding_periods[2]}d", 0.0),
                })

    if not results:
        logger.warning("No historical signal occurrences found in backtest.")
        return pd.DataFrame()

    bt_df = pd.DataFrame(results)

    # Compute Excess Returns
    bt_df["alpha_3m"] = bt_df["ret_3m"] - bt_df["bench_3m"]
    bt_df["alpha_6m"] = bt_df["ret_6m"] - bt_df["bench_6m"]
    bt_df["alpha_12m"] = bt_df["ret_12m"] - bt_df["bench_12m"]

    # Display Backtest Metrics
    print("\n" + "="*80)
    print("  AGED ATH BACKTEST PERFORMANCE SUMMARY")
    print("="*80)
    print(f"Total Qualified Signals Evaluated: {len(bt_df)}")
    print(f"{'-'*80}")
    print(f"{'Metric':<25} {'3-Month (63d)':>16} {'6-Month (126d)':>16} {'12-Month (252d)':>16}")
    print(f"{'-'*80}")

    for metric_name, cols in [
        ("Avg Stock Return", ("ret_3m", "ret_6m", "ret_12m")),
        ("Median Stock Return", ("ret_3m", "ret_6m", "ret_12m")),
        ("Avg Benchmark Return", ("bench_3m", "bench_6m", "bench_12m")),
        ("Alpha (Excess Return)", ("alpha_3m", "alpha_6m", "alpha_12m")),
        ("Win Rate (% Positive)", ("ret_3m", "ret_6m", "ret_12m")),
        ("Beat Benchmark Rate", ("alpha_3m", "alpha_6m", "alpha_12m")),
    ]:
        if "Median" in metric_name:
            v3 = bt_df[cols[0]].median()
            v6 = bt_df[cols[1]].median()
            v12 = bt_df[cols[2]].median()
            print(f"{metric_name:<25} {v3:>15.2f}% {v6:>15.2f}% {v12:>15.2f}%")
        elif "Win Rate" in metric_name:
            v3 = (bt_df[cols[0]] > 0).mean() * 100.0
            v6 = (bt_df[cols[1]] > 0).mean() * 100.0
            v12 = (bt_df[cols[2]] > 0).mean() * 100.0
            print(f"{metric_name:<25} {v3:>15.1f}% {v6:>15.1f}% {v12:>15.1f}%")
        elif "Beat Benchmark" in metric_name:
            v3 = (bt_df[cols[0]] > 0).mean() * 100.0
            v6 = (bt_df[cols[1]] > 0).mean() * 100.0
            v12 = (bt_df[cols[2]] > 0).mean() * 100.0
            print(f"{metric_name:<25} {v3:>15.1f}% {v6:>15.1f}% {v12:>15.1f}%")
        else:
            v3 = bt_df[cols[0]].mean()
            v6 = bt_df[cols[1]].mean()
            v12 = bt_df[cols[2]].mean()
            print(f"{metric_name:<25} {v3:>15.2f}% {v6:>15.2f}% {v12:>15.2f}%")

    print(f"{'='*80}")
    print("\n[!] SURVIVORSHIP BIAS DISCLAIMER:")
    print("    This backtest evaluates stocks present in the specified universe.")
    print("    Historical survivorship bias exists because companies that were subsequently")
    print("    delisted, liquidated, or restructured are not included in current lists.\n")

    return bt_df


# =============================================================================
# TWO-CARD STAGE BREAKDOWN: LATEST DATE & LAST 30 DAYS
# =============================================================================

def get_aged_ath_stages_data(min_market_cap: Optional[float] = None,
                             universe_type: str = "all",
                             limit_universe: Optional[int] = None,
                             workers: int = 16) -> Dict[str, Any]:
    """
    Returns structured two-card stage breakdown for Pattern Screens UI across ALL stocks:
      - 'fresh_breakout' (Card 1): Latest Date qualifying stocks.
      - 'climbing' (Card 2): Stocks qualifying within the Last 30 Days.
    """
    import concurrent.futures

    provider = YFinanceDataProvider()
    universe = get_universe(universe_type=universe_type, limit=limit_universe)

    # Load market cap dictionary for quick lookup
    market_caps: Dict[str, float] = {}
    if os.path.exists(DEFAULT_CSV_PATH):
        try:
            mdf = pd.read_csv(DEFAULT_CSV_PATH)
            for _, r in mdf.iterrows():
                s = str(r.get("symbol", "")).strip().upper()
                mc = r.get("market_cap_cr")
                if s and pd.notna(mc):
                    val = float(mc)
                    market_caps[s] = val
                    if s.endswith(".NS"):
                        market_caps[s[:-3]] = val
                    else:
                        market_caps[f"{s}.NS"] = val
        except Exception as e:
            logger.debug("Failed loading market caps: %s", e)

    latest_list: List[Dict[str, Any]] = []
    last_30d_list: List[Dict[str, Any]] = []
    as_of_date = str(date.today())

    def process_item(item: Tuple[str, str]):
        sym, name = item
        clean_s = sym.replace(".NS", "")
        mcap = market_caps.get(sym) or market_caps.get(clean_s)
        if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):
            return None, None

        cache_path = provider._get_cache_path(sym)
        df = None
        if cache_path.exists():
            try:
                df = pd.read_pickle(cache_path)
            except Exception:
                df = None
        elif len(universe) <= 100:
            df = provider.fetch_history(sym)

        if df is None or len(df) < 252:
            return None, None

        as_of = df.index[-1].strftime("%Y-%m-%d")

        # 1. Evaluate at latest date
        res_latest, _ = evaluate_aged_ath_stock(ticker=sym, df=df, company_name=name)
        if res_latest is not None:
            it = dict(res_latest)
            it["Symbol"] = sym
            it["symbol"] = sym
            it["Close"] = it["current_price"]
            it["close"] = it["current_price"]
            it["market_cap_cr"] = mcap
            it["rs_rank"] = 75.0
            it["Timestamp"] = as_of
            it["breakout_date"] = it["ath_date"]
            return it, it

        # 2. Check last 30 days window
        df_30d = df[df.index >= (df.index[-1] - pd.Timedelta(days=30))]
        if len(df_30d) >= 2:
            for bar_idx in range(len(df_30d) - 1):
                sub_df = df.loc[:df_30d.index[bar_idx]]
                res_sub, _ = evaluate_aged_ath_stock(ticker=sym, df=sub_df, company_name=name)
                if res_sub is not None:
                    it_30d = dict(res_sub)
                    it_30d["Symbol"] = sym
                    it_30d["symbol"] = sym
                    it_30d["Close"] = float(df["Close"].iloc[-1])
                    it_30d["close"] = float(df["Close"].iloc[-1])
                    it_30d["market_cap_cr"] = mcap
                    it_30d["rs_rank"] = 70.0
                    it_30d["Timestamp"] = df_30d.index[bar_idx].strftime("%Y-%m-%d")
                    it_30d["breakout_date"] = it_30d["ath_date"]
                    return None, it_30d

        return None, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_item, item) for item in universe]
        for f in concurrent.futures.as_completed(futures):
            try:
                l_item, c_item = f.result()
                if l_item:
                    latest_list.append(l_item)
                if c_item:
                    last_30d_list.append(c_item)
            except Exception as e:
                logger.debug("Stage item processing error: %s", e)

    latest_list.sort(key=lambda x: x.get("distance_pct", 999))
    last_30d_list.sort(key=lambda x: x.get("distance_pct", 999))

    return {
        "screen": "aged-ath",
        "as_of_date": as_of_date,
        "stages": {
            "fresh_breakout": {
                "count": len(latest_list),
                "symbols": latest_list
            },
            "climbing": {
                "count": len(last_30d_list),
                "symbols": last_30d_list
            }
        }
    }


# =============================================================================
# CLI & SHORT USAGE INTERFACE
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Aged All-Time High (ATH) Stock Screener & Pattern Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 1. Run live screener on top 500 NSE stocks:
  python aged_ath_screener.py --run

  # 2. Run screener with custom thresholds on a custom CSV of tickers:
  python aged_ath_screener.py --run --csv my_tickers.csv --ath-age 2.5 --dist 15.0

  # 3. Plot Aged ATH chart for a specific symbol:
  python aged_ath_screener.py --plot RELIANCE.NS

  # 4. Run historical Point-in-Time backtest vs Nifty 500:
  python aged_ath_screener.py --backtest --start 2020-01-01
        """
    )
    parser.add_argument("--run", action="store_true", help="Run the live screener on universe")
    parser.add_argument("--universe", type=str, default=DEFAULT_UNIVERSE, choices=["nifty500", "csv", "all"],
                        help="Stock universe source (default: nifty500)")
    parser.add_argument("--csv", type=str, default=None, help="Path to custom CSV file containing tickers")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated list of symbols (e.g. RELIANCE,TCS)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of stocks to scan (e.g. 50)")

    parser.add_argument("--ath-age", type=float, default=MIN_ATH_AGE_YEARS, help="Minimum ATH age in years (default: 2.0)")
    parser.add_argument("--dist", type=float, default=MAX_DISTANCE_FROM_ATH_PCT, help="Max distance from ATH %% (default: 20.0)")
    parser.add_argument("--listing-age", type=float, default=MIN_LISTING_AGE_YEARS, help="Min listing age in years (default: 3.0)")
    parser.add_argument("--basis", type=str, default=PRICE_BASIS, choices=["close", "high"], help="Price basis for ATH")

    parser.add_argument("--plot", type=str, default=None, help="Plot Aged ATH chart for given ticker (e.g. TRENT.NS)")
    parser.add_argument("--show", action="store_true", help="Display plot interactively in GUI window")
    parser.add_argument("--backtest", action="store_true", help="Run historical Point-in-Time backtest")
    parser.add_argument("--start", type=str, default="2020-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--workers", type=int, default=8, help="Number of concurrent workers (default: 8)")
    parser.add_argument("--output", type=str, default=OUTPUT_CSV_FILE, help="Output CSV path for screen results")

    args = parser.parse_args()

    # Mode 1: Plotting
    if args.plot:
        sym = args.plot.strip()
        if not sym.endswith(".NS") and not sym.startswith("^"):
            sym = f"{sym}.NS"
        out_path = plot_aged_ath(sym, price_basis=args.basis, show=args.show)
        if out_path:
            print(f"Chart generated and saved to: {out_path}")
        return

    # Mode 2: Backtest
    if args.backtest:
        syms = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
        run_aged_ath_backtest(tickers=syms, start_date=args.start)
        return

    # Mode 3: Live Screener (Default if --run or no flag provided)
    custom_syms = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    run_aged_ath_screener(
        universe_type=args.universe,
        csv_path=args.csv,
        custom_symbols=custom_syms,
        min_ath_age_years=args.ath_age,
        max_distance_from_ath_pct=args.dist,
        min_listing_age_years=args.listing_age,
        price_basis=args.basis,
        save_csv_path=args.output,
        limit_universe=args.limit,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
