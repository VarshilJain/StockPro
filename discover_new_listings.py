"""
discover_new_listings.py — Discover & Sync Newly Listed Stocks
=============================================================
Fetches the official NSE equity master list (EQUITY_L.csv) directly from NSE India archives,
compares against StockPro's symbols_list.py, and identifies newly listed stocks / IPOs.

Usage:
  python discover_new_listings.py              # List newly listed stocks not yet in StockPro
  python discover_new_listings.py --add        # Append newly listed symbols to symbols_list.py
  python discover_new_listings.py --add --sync # Add to symbols_list.py and backfill data immediately
"""

import argparse
import io
import logging
import os
import subprocess
import sys
import urllib.request
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("new_listings")

NSE_EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SYMBOLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symbols_list.py")


def fetch_nse_master() -> pd.DataFrame:
    """Download official NSE Mainboard Equity list."""
    log.info("Fetching official equity master from NSE India (%s)...", NSE_EQUITY_URL)
    req = urllib.request.Request(
        NSE_EQUITY_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        df = pd.read_csv(io.BytesIO(resp.read()))

    # Clean columns & strings
    df.columns = [c.strip() for c in df.columns]
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()

    date_col = next((c for c in df.columns if "DATE" in c), "DATE OF LISTING")
    df["listing_date"] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
    df["ticker"] = df["SYMBOL"] + ".NS"
    return df


def load_current_symbols() -> list[str]:
    """Read existing symbols from symbols_list.py."""
    import symbols_list
    return list(symbols_list.SYMBOLS)


def filter_valid_equities(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter out temporary Rights Entitlements (-RE), non-equity series, or odd instruments.
    Series EQ = Normal Rolling Settlement Equity.
    Series BE = Trade-for-Trade Equity.
    Series SM = SME Emerge.
    """
    # Exclude Rights Entitlements (e.g. -RE, -RE1)
    mask = ~df["SYMBOL"].str.contains("-RE", na=False, regex=True)
    # Require standard series if present
    if "SERIES" in df.columns:
        mask &= df["SERIES"].isin(["EQ", "BE", "SM", "BZ"])
    return df[mask]


def add_symbols_to_file(new_tickers: list[str]) -> int:
    """Safely append new tickers into symbols_list.py maintaining formatting."""
    if not os.path.exists(SYMBOLS_FILE):
        log.error("symbols_list.py not found at %s", SYMBOLS_FILE)
        return 0

    with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the closing bracket of SYMBOLS = [...]
    last_bracket_idx = content.rfind("]")
    if last_bracket_idx == -1:
        log.error("Could not locate closing bracket in symbols_list.py")
        return 0

    # Format new tickers with 4-space indentation
    formatted_new = "\n".join([f'    "{t}",' for t in new_tickers])
    new_content = content[:last_bracket_idx].rstrip() + "\n" + formatted_new + "\n" + content[last_bracket_idx:]

    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    log.info("Successfully appended %d new symbols to symbols_list.py", len(new_tickers))
    return len(new_tickers)


def main():
    parser = argparse.ArgumentParser(description="Find and add newly listed NSE stocks to StockPro.")
    parser.add_argument("--add", action="store_true", help="Append newly discovered symbols to symbols_list.py")
    parser.add_argument("--sync", action="store_true", help="Backfill OHLCV data immediately using ingest.py")
    parser.add_argument("--days", type=int, default=None, help="Filter to stocks listed within the last N days")
    args = parser.parse_args()

    nse_df = fetch_nse_master()
    valid_df = filter_valid_equities(nse_df)
    current_symbols = set(load_current_symbols())

    # Find newly listed stocks
    missing_df = valid_df[~valid_df["ticker"].isin(current_symbols)].copy()
    if args.days and not missing_df.empty:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=args.days)
        missing_df = missing_df[missing_df["listing_date"] >= cutoff]

    missing_df = missing_df.sort_values(by="listing_date", ascending=False)

    print("=" * 80)
    print(f"STOCKPRO NEW LISTINGS AUDIT")
    print(f"NSE Mainboard Listings Active: {len(valid_df)}")
    print(f"StockPro Current Universe:     {len(current_symbols)}")
    print(f"Discovered New Listings:       {len(missing_df)}")
    print("=" * 80)

    if missing_df.empty:
        print("StockPro universe is fully up-to-date with NSE. No new listings found.")
        return

    print("\nNewly Listed Stocks (Sorted by Listing Date):")
    print(f"{'Ticker':<18} | {'Listing Date':<12} | {'Series':<6} | {'Company Name'}")
    print("-" * 80)
    for _, r in missing_df.iterrows():
        dt_str = r["listing_date"].strftime("%Y-%m-%d") if pd.notna(r["listing_date"]) else "N/A"
        print(f"{r['ticker']:<18} | {dt_str:<12} | {r.get('SERIES', 'EQ'):<6} | {r.get('NAME OF COMPANY', '')}")

    new_tickers = missing_df["ticker"].tolist()

    if args.add:
        add_symbols_to_file(new_tickers)
        if args.sync:
            log.info("Running incremental ingest to backfill history for new tickers...")
            subprocess.run([sys.executable, "ingest.py"], check=True)
            log.info("Running incremental signals calculation...")
            subprocess.run([sys.executable, "incremental_signals.py"], check=True)
            log.info("All new listings are fully ingested, enriched with signals, and available in StockPro!")
    else:
        print("\nTip: Run with --add to automatically append these symbols to symbols_list.py:")
        print("  python discover_new_listings.py --add")
        print("To also fetch historical data immediately:")
        print("  python discover_new_listings.py --add --sync")


if __name__ == "__main__":
    main()

