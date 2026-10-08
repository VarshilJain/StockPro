"""
ingest.py — Incremental OHLCV Ingestion Job
============================================
Run daily (cron / Windows Task Scheduler / run_nightly.bat).

Flow per run:
  1. Auto-create `ohlc_data` table if it doesn't exist (idempotent).
  2. Query MAX(date) per ticker already stored in MySQL.
  3. For each ticker in SYMBOLS:
       - NEW ticker   -> yfinance download(period="max")  [one-time full backfill]
       - KNOWN ticker -> yfinance download(start=last_date+1d, end=today)
  4. Upsert into `ohlc_data` via INSERT ... ON DUPLICATE KEY UPDATE.
       - The UNIQUE KEY on (ticker, date) prevents duplicates.
       - ON DUPLICATE KEY UPDATE handles split/dividend retroactive adjustments.
  5. Print a summary of rows inserted / updated / skipped.

Signal computation (test.py) runs AFTER this script and reads from MySQL.
The `historical_data` table (signal-enriched) is NOT touched here.
"""

import concurrent.futures
import datetime
import logging
import os
import random
import sys
import time
from typing import Optional

import mysql.connector
from mysql.connector import pooling
import pandas as pd
import yfinance as yf

from config import DB_CONFIG

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ingest")

# ---------------------------------------------------------------------------
# Missing / Delisted symbols filtering & persistence
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_MISSING_SYMBOLS_FILE = os.path.join(_BASE_DIR, "missing_symbols.txt")

def _load_missing_symbols() -> set:
    if os.path.exists(_MISSING_SYMBOLS_FILE):
        try:
            with open(_MISSING_SYMBOLS_FILE, "r", encoding="utf-8") as f:
                return {line.strip() for line in f if line.strip() and not line.startswith("#")}
        except Exception as exc:
            log.warning("Could not read missing_symbols.txt: %s", exc)
    return set()

def _mark_symbol_missing(ticker: str) -> None:
    try:
        with open(_MISSING_SYMBOLS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ticker}\n")
    except Exception as exc:
        log.warning("Could not append %s to missing_symbols.txt: %s", ticker, exc)

_MISSING_SET = _load_missing_symbols()

# ---------------------------------------------------------------------------
# Universe of symbols (Canonical list loaded from symbols_list.py)
# ---------------------------------------------------------------------------
from symbols_list import SYMBOLS as _STOCK_SYMBOLS

# Benchmark indices needed for relative strength and market baseline
INDEX_SYMBOLS = ["^NSEI", "^CRSLDX"]

# Full list of symbols to ingest: benchmarks first, followed by all stocks excluding known missing
SYMBOLS = INDEX_SYMBOLS + [s for s in _STOCK_SYMBOLS if s not in INDEX_SYMBOLS and s not in _MISSING_SET]



# ---------------------------------------------------------------------------
# DDL — auto-created on first run, idempotent on subsequent runs
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ohlc_data (
    ticker    VARCHAR(30)    NOT NULL,
    date      DATE           NOT NULL,
    open      DECIMAL(14,4),
    high      DECIMAL(14,4),
    low       DECIMAL(14,4),
    close     DECIMAL(14,4),
    volume    BIGINT,
    PRIMARY KEY (ticker, date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# MySQL 8.0.19+ alias syntax: cleaner and avoids VALUES() deprecation warning
_UPSERT_SQL = """
INSERT INTO ohlc_data (ticker, date, open, high, low, close, volume)
VALUES (%s, %s, %s, %s, %s, %s, %s)
AS new_row
ON DUPLICATE KEY UPDATE
    open   = new_row.open,
    high   = new_row.high,
    low    = new_row.low,
    close  = new_row.close,
    volume = new_row.volume
"""

# Fallback for MySQL < 8.0.19 which does not support the AS alias syntax
_UPSERT_SQL_LEGACY = """
INSERT INTO ohlc_data (ticker, date, open, high, low, close, volume)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    open   = VALUES(open),
    high   = VALUES(high),
    low    = VALUES(low),
    close  = VALUES(close),
    volume = VALUES(volume)
"""

BATCH_SIZE = 5000  # rows per executemany call

# ---------------------------------------------------------------------------
# Fetch reliability & concurrency constants
# ---------------------------------------------------------------------------
FETCH_RETRIES   = 2     # max attempts per ticker on network error
JITTER_MIN      = 0.05  # seconds — safe min sleep between yfinance calls
JITTER_MAX      = 0.15  # seconds — safe max sleep between yfinance calls
BACKOFF_BASE    = 2     # exponential backoff multiplier on retry
NUM_WORKERS     = 6     # concurrent worker threads for non-blocking HTTP fetch


# ---------------------------------------------------------------------------
# Connection pool — reuses MySQL connections across calls
# ---------------------------------------------------------------------------
_DB_POOL = pooling.MySQLConnectionPool(
    pool_name="stockpro_ingest",
    pool_size=5,           # 5 is sufficient for single-threaded ingest
    pool_reset_session=True,
    **DB_CONFIG
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_table(cursor) -> None:
    """Create ohlc_data if it does not exist. Safe to call every run."""
    cursor.execute(_CREATE_TABLE_SQL)


def _get_watermarks(cursor) -> dict:
    """
    Return {ticker: last_stored_date} for every ticker already in ohlc_data.
    Tickers with no rows at all are simply absent from the returned dict.
    """
    cursor.execute("SELECT ticker, MAX(date) FROM ohlc_data GROUP BY ticker")
    return {row[0]: row[1] for row in cursor.fetchall()}


def _fetch_incremental(ticker: str, last_date) -> pd.DataFrame:
    """
    Download OHLCV data from yfinance.

    - last_date is None  -> first-ever fetch for this ticker; download full max history.
    - last_date is set   -> incremental fetch from (last_date + 1 day) to today.

    Returns a cleaned DataFrame with lowercase columns:
        [ticker, date, open, high, low, close, volume]
    or an empty DataFrame if nothing new is available.
    """
    today = datetime.date.today()
    now = datetime.datetime.now()
    market_close_time = datetime.time(15, 30)

    if now.time() >= market_close_time:
        max_allowed_date = today
    else:
        max_allowed_date = today - datetime.timedelta(days=1)

    if last_date is None:
        # Full historical backfill (one-time per ticker)
        log.info("  [NEW]  %s — full backfill (period=max)", ticker)
        df = yf.download(
            ticker,
            period="max",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    else:
        # Incremental fetch: only missing days up to max_allowed_date
        fetch_start = last_date + datetime.timedelta(days=1)

        if fetch_start > max_allowed_date:
            # Already up-to-date; nothing to do
            log.info("  [SKIP] %s — already current (last=%s)", ticker, last_date)
            return pd.DataFrame()

        end_fetch_date = max_allowed_date + datetime.timedelta(days=1)
        log.info(
            "  [INC]  %s — fetching %s to %s",
            ticker, fetch_start.isoformat(), max_allowed_date.isoformat(),
        )
        df = yf.download(
            ticker,
            start=fetch_start.isoformat(),
            end=end_fetch_date.isoformat(),  # end is exclusive in yfinance
            interval="1d",
            progress=False,
            auto_adjust=True,
        )

    if df is None or df.empty:
        return pd.DataFrame()

    # Normalise columns: yfinance sometimes returns a MultiIndex for a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Validate required columns exist
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        log.warning("  [WARN] %s — missing columns %s; skipping", ticker, missing)
        return pd.DataFrame()

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "date"
    df.reset_index(inplace=True)

    # Ensure date column is a plain Python date (not tz-aware datetime)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # Enforce strict cutoff: filter out any partial intraday bars for today if market is not closed yet
    df = df[df["date"] <= max_allowed_date]

    # Filter out dummy holiday non-trading bars (where volume is 0 and prices are completely flat)
    dummy_mask = (df["Volume"] == 0) & (df["Open"] == df["Close"]) & (df["High"] == df["Low"])
    df = df[~dummy_mask]

    if df.empty:
        return pd.DataFrame()

    # Add ticker identifier
    df.insert(0, "ticker", ticker)

    # Drop rows with NaN close
    df = df.dropna(subset=["close"] if "close" in df.columns else ["Close"])

    # Convert volume to int (yfinance can return float)
    df["Volume"] = df["Volume"].fillna(0).astype("int64")

    # Reorder and rename to lowercase to match the INSERT column list
    df = df[["ticker", "date", "Open", "High", "Low", "Close", "Volume"]]
    df.columns = ["ticker", "date", "open", "high", "low", "close", "volume"]

    return df


def _fetch_with_retry(ticker: str, last_date) -> pd.DataFrame:
    """
    Wraps _fetch_incremental with network retries and per-call jitter.
    If a new ticker returns 0 rows (delisted/no-data), immediately records it in
    missing_symbols.txt and returns empty without wasting time on retries.
    """
    for attempt in range(FETCH_RETRIES):
        try:
            df = _fetch_incremental(ticker, last_date)
            if df is None or df.empty:
                if last_date is None:
                    _mark_symbol_missing(ticker)
                    log.info("  [SKIP-NEW] %s — no data found; added to missing_symbols.txt", ticker)
                return pd.DataFrame()

            time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            return df
        except Exception as exc:
            if attempt < FETCH_RETRIES - 1:
                wait = (BACKOFF_BASE ** (attempt + 1)) + random.uniform(0.5, 1.0)
                log.warning(
                    "  [RETRY-ERR]   %s — attempt %d/%d failed (%s) — backing off %.1fs",
                    ticker, attempt + 1, FETCH_RETRIES, exc, wait,
                )
                time.sleep(wait)
            else:
                log.error("  [GIVE UP]     %s — all %d attempts failed: %s", ticker, FETCH_RETRIES, exc)
    return pd.DataFrame()


def _fetch_worker(ticker: str, last_date) -> tuple:
    t0 = time.perf_counter()
    is_new = last_date is None
    try:
        df = _fetch_with_retry(ticker, last_date)
    except Exception as exc:
        log.error("Worker error fetching %s: %s", ticker, exc)
        df = pd.DataFrame()
    duration = time.perf_counter() - t0
    return ticker, is_new, df, duration


def _upsert_batch(cursor, rows: list, use_legacy: bool) -> int:
    """
    Batch-upsert a list of row tuples into ohlc_data.

    MySQL rowcount semantics per row:
        1  -> newly inserted
        2  -> updated (values changed)
        0  -> duplicate, same values, no-op
    Returns total affected rows across all batches.
    """
    sql = _UPSERT_SQL_LEGACY if use_legacy else _UPSERT_SQL
    total_affected = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i: i + BATCH_SIZE]
        cursor.executemany(sql, chunk)
        total_affected += cursor.rowcount
    return total_affected


def _detect_legacy_mysql(cursor) -> bool:
    """Return True if MySQL version is older than 8.0.19 (no AS alias in UPSERT)."""
    cursor.execute("SELECT VERSION()")
    version_str = cursor.fetchone()[0]  # e.g. "8.0.33" or "5.7.44"
    try:
        parts = [int(x) for x in version_str.split("-")[0].split(".")]
        major = parts[0]
        minor = parts[1] if len(parts) > 1 else 0
        patch = parts[2] if len(parts) > 2 else 0
        return (major, minor, patch) < (8, 0, 19)
    except Exception:
        # Cannot parse version string -> use safe legacy syntax
        return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _format_time(seconds: float) -> str:
    """Format duration in seconds into human-readable HH:MM:SS or MM:SS."""
    if seconds < 0:
        seconds = 0
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


# ---------------------------------------------------------------------------
# Main entry point with live performance monitoring
# ---------------------------------------------------------------------------

def run_ingestion(limit: Optional[int] = None, checkpoint_interval: int = 25) -> None:
    start_time_overall = time.perf_counter()

    log.info("=" * 80)
    log.info("StockPro — Incremental OHLCV Ingestion & Performance Monitor")
    log.info("Timestamp : %s", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 80)

    conn = _DB_POOL.get_connection()
    cursor = conn.cursor()

    try:
        # Step 1: Ensure ohlc_data table exists (idempotent DDL)
        t0_setup = time.perf_counter()
        _ensure_table(cursor)
        conn.commit()
        setup_time = time.perf_counter() - t0_setup
        log.info("Table ohlc_data verified in %.3fs.", setup_time)

        # Detect MySQL version once for the whole session
        use_legacy = _detect_legacy_mysql(cursor)
        if use_legacy:
            log.info("MySQL < 8.0.19 detected — using VALUES() syntax for upserts.")

        # Step 2: Load per-ticker watermarks (last stored date)
        t0_wm = time.perf_counter()
        watermarks = _get_watermarks(cursor)
        wm_time = time.perf_counter() - t0_wm

        target_symbols = SYMBOLS[:limit] if limit else SYMBOLS
        total_targets = len(target_symbols)

        new_count = len([s for s in target_symbols if s not in watermarks])
        known_count = total_targets - new_count

        log.info(
            "Watermarks loaded in %.3fs: %d cached in DB | Target run: %d tickers (%d new, %d existing)",
            wm_time, len(watermarks), total_targets, new_count, known_count,
        )
        if limit:
            log.info(">>> TEST MODE ACTIVE: limited to first %d tickers <<<", limit)
        log.info("-" * 80)

        # Performance accumulation counters
        total_rows_affected = 0
        total_rows_fetched = 0
        total_fetch_time = 0.0
        total_db_time = 0.0
        success_count = 0
        skip_count = 0
        errors = []

        ingest_start = time.perf_counter()

        with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            future_to_ticker = {
                executor.submit(_fetch_worker, ticker, watermarks.get(ticker)): ticker
                for ticker in target_symbols
            }

            for idx, future in enumerate(concurrent.futures.as_completed(future_to_ticker), start=1):
                ticker, is_new, df, fetch_duration = future.result()
                total_fetch_time += fetch_duration

                elapsed = time.perf_counter() - ingest_start
                rate_per_min = (idx / elapsed * 60) if elapsed > 0 else 0.0
                remaining = total_targets - idx
                eta_sec = (remaining / (idx / elapsed)) if elapsed > 0 and idx > 0 else 0.0
                progress_pct = (idx / total_targets) * 100.0

                if df is None or df.empty:
                    skip_count += 1
                    status_reason = "up-to-date" if not is_new else "no data from source"
                    log.info(
                        "[%d/%d] (%5.1f%%) [SKIP] %-14s (%s) | Fetch: %5.2fs | Elapsed: %s | ETA: %s",
                        idx, total_targets, progress_pct, ticker, status_reason,
                        fetch_duration, _format_time(elapsed), _format_time(eta_sec),
                    )
                    continue

                rows = list(df.itertuples(index=False, name=None))
                num_rows = len(rows)
                total_rows_fetched += num_rows

                # Step 4: Batch write to MySQL (single-threaded on main connection)
                t0_db = time.perf_counter()
                try:
                    affected = _upsert_batch(cursor, rows, use_legacy)
                    conn.commit()
                    db_duration = time.perf_counter() - t0_db
                    total_db_time += db_duration
                    total_rows_affected += affected
                    success_count += 1

                    write_rate = (num_rows / db_duration) if db_duration > 0 else 0.0
                    total_stk_time = fetch_duration + db_duration
                    mode_tag = "NEW" if is_new else "INC"

                    log.info(
                        "[%d/%d] (%5.1f%%) [OK-%s] %-14s | Rows: %6d | Fetch: %5.2fs | DB: %5.2fs (%6.0f r/s) | Tot: %5.2fs | Rate: %4.1f stk/m | Elapsed: %s | ETA: %s",
                        idx, total_targets, progress_pct, mode_tag, ticker, num_rows,
                        fetch_duration, db_duration, write_rate, total_stk_time,
                        rate_per_min, _format_time(elapsed), _format_time(eta_sec),
                    )
                except Exception as exc:
                    conn.rollback()
                    errors.append((ticker, str(exc)))
                    log.error(
                        "[%d/%d] (%5.1f%%) [DB-ERR] %-14s — %s (Elapsed: %s)",
                        idx, total_targets, progress_pct, ticker, exc, _format_time(elapsed),
                    )

                # Checkpoint banner every N tickers
                if idx % checkpoint_interval == 0 and idx < total_targets:
                    elapsed = time.perf_counter() - ingest_start
                    avg_fetch = total_fetch_time / idx if idx > 0 else 0.0
                    avg_db = total_db_time / max(success_count, 1)
                    stks_per_min = (idx / elapsed * 60) if elapsed > 0 else 0.0
                    agg_write_rate = (total_rows_affected / total_db_time) if total_db_time > 0 else 0.0
                    remaining = total_targets - idx
                    eta_sec = (remaining / (idx / elapsed)) if elapsed > 0 else 0.0

                    log.info("-" * 80)
                    log.info(
                        "[CHECKPOINT] [%d/%d] (%.1f%%) | Elapsed: %s | ETA: %s | Speed: %.1f stks/min",
                        idx, total_targets, progress_pct, _format_time(elapsed), _format_time(eta_sec), stks_per_min,
                    )
                    log.info(
                        "   Avg Fetch: %.3fs/stk | Avg DB Write: %.3fs/stk | DB Write Speed: %.0f rows/sec",
                        avg_fetch, avg_db, agg_write_rate,
                    )
                    log.info(
                        "   Rows written: %s | Success: %d | Skipped: %d | Errors: %d",
                        f"{total_rows_affected:,}", success_count, skip_count, len(errors),
                    )
                    log.info("-" * 80)

        # Step 5: Full Performance Summary Report
        total_wall_clock = time.perf_counter() - start_time_overall
        total_ingest_time = time.perf_counter() - ingest_start
        overall_stks_per_min = (total_targets / total_ingest_time * 60) if total_ingest_time > 0 else 0.0
        overall_rows_per_sec = (total_rows_affected / total_ingest_time) if total_ingest_time > 0 else 0.0
        overall_db_rate = (total_rows_affected / total_db_time) if total_db_time > 0 else 0.0

        pct_fetch = (total_fetch_time / total_ingest_time * 100) if total_ingest_time > 0 else 0.0
        pct_db = (total_db_time / total_ingest_time * 100) if total_ingest_time > 0 else 0.0

        log.info("=" * 80)
        log.info("                    INGESTION PERFORMANCE SUMMARY REPORT")
        log.info("=" * 80)
        log.info("Tickers Evaluated       : %d", total_targets)
        log.info("  - Successful Writes   : %d", success_count)
        log.info("  - Skipped (Current)   : %d", skip_count)
        log.info("  - Errors Encountered  : %d", len(errors))
        log.info("Total Rows Affected     : %s rows", f"{total_rows_affected:,}")
        log.info("Total Wall-Clock Time   : %s (%.1f seconds)", _format_time(total_wall_clock), total_wall_clock)
        log.info("Overall Throughput      : %.1f stocks/min | %.1f rows/sec", overall_stks_per_min, overall_rows_per_sec)
        log.info("-" * 80)
        log.info("Time Breakdown:")
        log.info(
            "  - Network/API Fetch   : %s (%4.1f%%) | Avg: %.3fs/ticker",
            _format_time(total_fetch_time), pct_fetch,
            (total_fetch_time / total_targets) if total_targets > 0 else 0.0,
        )
        log.info(
            "  - MySQL DB Upsert     : %s (%4.1f%%) | Avg: %.3fs/ticker | Write Rate: %.0f rows/s",
            _format_time(total_db_time), pct_db,
            (total_db_time / max(success_count, 1)), overall_db_rate,
        )
        log.info("=" * 80)

        if errors:
            log.warning("Failed tickers list (%d):", len(errors))
            for sym, err in errors:
                log.warning("  %-15s : %s", sym, err)
            log.info("=" * 80)

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="StockPro Incremental OHLCV Ingestion with Live Telemetry")
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Process only first N symbols (ideal for performance benchmarking before full run)",
    )
    parser.add_argument(
        "--checkpoint", "-c",
        type=int,
        default=25,
        help="Display performance checkpoint banner every N tickers (default: 25)",
    )

    args = parser.parse_args()
    run_ingestion(limit=args.limit, checkpoint_interval=args.checkpoint)

