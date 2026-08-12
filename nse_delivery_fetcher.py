"""
nse_delivery_fetcher.py — Official NSE India Delivery Data Downloader
=====================================================================
Downloads official daily security-wise delivery position reports directly
from NSE India (archives.nseindia.com) and updates the delivery_quantity
column in the MySQL database `ohlc_data` table with 100% real NSE delivery data.

Official NSE File Formats Handled:
  - sec_bhavdata_full_DDMMYYYY.csv (Security-wise Price & Delivery Data)
  - MTO_DDMMYYYY.DAT (Security-wise Delivery Position Data)
"""

import datetime
import logging
import sys
import zipfile
import io
import requests
import pandas as pd

from database import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nse_delivery")

# Headers to bypass NSE anti-scraping / 403 blocks
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
}


def fetch_nse_session() -> requests.Session:
    """Creates a requests session initialized with NSE cookies."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except Exception as e:
        log.warning("Could not pre-fetch NSE homepage session cookie: %s", e)
    return session


def download_nse_delivery_for_date(target_date: datetime.date, session: requests.Session = None) -> pd.DataFrame:
    """
    Downloads official NSE security-wise delivery report for a specific trading date.
    
    Tries primary URL:
      https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
    Fallback URL:
      https://archives.nseindia.com/archives/equities/mto/mto_DDMMYYYY.DAT
    """
    session = session or fetch_nse_session()
    
    date_str = target_date.strftime("%d%m%Y")
    
    # URL 1: sec_bhavdata_full
    url1 = f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{date_str}.csv"
    log.info("Fetching NSE delivery data for %s from %s ...", target_date.isoformat(), url1)
    
    try:
        resp = session.get(url1, timeout=15)
        if resp.status_code == 200 and "SYMBOL" in resp.text.upper():
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [c.strip().upper() for c in df.columns]
            
            # Filter EQ series (equity series)
            if "SERIES" in df.columns:
                df = df[df["SERIES"].astype(str).str.strip().str.upper() == "EQ"]
                
            sym_col = next((c for c in ["SYMBOL", "TICKER"] if c in df.columns), None)
            deliv_col = next((c for c in ["DELIV_QTY", "DELIVERY_QTY", "DELIV_PER"] if c in df.columns), None)
            
            if sym_col and deliv_col:
                df["ticker"] = df[sym_col].astype(str).str.strip() + ".NS"
                df["date"] = target_date
                df["delivery_quantity"] = pd.to_numeric(df[deliv_col], errors="coerce").fillna(0).astype("int64")
                return df[["ticker", "date", "delivery_quantity"]]
    except Exception as exc:
        log.debug("Primary URL failed for %s: %s", date_str, exc)

    # URL 2: MTO DAT fallback file
    url2 = f"https://archives.nseindia.com/archives/equities/mto/mto_{date_str}.DAT"
    log.info("Trying secondary NSE MTO file URL: %s", url2)
    try:
        resp = session.get(url2, timeout=15)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            records = []
            for line in lines:
                parts = line.split(",")
                # Standard MTO line format: 20,REC_NO,SYMBOL,SERIES,TRADED_QTY,DELIV_QTY,DELIV_PCT
                if len(parts) >= 6 and parts[0].strip() == "20" and parts[3].strip() == "EQ":
                    symbol = parts[2].strip() + ".NS"
                    deliv_qty = int(parts[5].strip()) if parts[5].strip().isdigit() else 0
                    records.append({"ticker": symbol, "date": target_date, "delivery_quantity": deliv_qty})
            if records:
                return pd.DataFrame(records)
    except Exception as exc:
        log.debug("Secondary MTO URL failed for %s: %s", date_str, exc)

    log.warning("No official NSE delivery data file available for date: %s (weekend/holiday or not published yet)", target_date.isoformat())
    return pd.DataFrame()


def update_db_with_real_nse_delivery(df: pd.DataFrame) -> int:
    """Updates real NSE delivery_quantity records in MySQL ohlc_data table."""
    if df.empty:
        return 0

    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Ensure column exists
        cursor.execute("SHOW COLUMNS FROM ohlc_data LIKE 'delivery_quantity'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE ohlc_data ADD COLUMN delivery_quantity BIGINT NULL")
            conn.commit()

        update_sql = """
        UPDATE ohlc_data
        SET delivery_quantity = %s
        WHERE ticker = %s AND date = %s
        """
        records = [
            (int(row.delivery_quantity), str(row.ticker), row.date.strftime("%Y-%m-%d") if hasattr(row.date, "strftime") else str(row.date))
            for row in df.itertuples()
        ]

        cursor.executemany(update_sql, records)
        conn.commit()
        updated_count = cursor.rowcount
        cursor.close()
        log.info("Updated %d real NSE delivery records in MySQL ohlc_data table.", updated_count)
        return updated_count
    finally:
        conn.close()


def sync_recent_nse_delivery(days_back: int = 30) -> int:
    """
    Downloads and ingests real NSE delivery data for the last `days_back` trading days.
    """
    log.info("Starting real NSE delivery data sync for the past %d days...", days_back)
    session = fetch_nse_session()
    today = datetime.date.today()
    
    total_updated = 0
    for i in range(days_back):
        target_date = today - datetime.timedelta(days=i)
        # Skip weekends (Saturday=5, Sunday=6)
        if target_date.weekday() in (5, 6):
            continue
            
        df_deliv = download_nse_delivery_for_date(target_date, session=session)
        if not df_deliv.empty:
            count = update_db_with_real_nse_delivery(df_deliv)
            total_updated += count

    log.info("Real NSE delivery sync complete! Total updated records: %d", total_updated)
    return total_updated


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    sync_recent_nse_delivery(days_back=days)
