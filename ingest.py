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

import datetime
import logging
import sys
from typing import Optional

import mysql.connector
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
# Nifty 500 universe — same list used by googlefinance.py
# ---------------------------------------------------------------------------
SYMBOLS = [
    "^NSEI", "^CRSLDX", "360ONE.NS", "3MINDIA.NS", "ABB.NS", "ACC.NS", "ACE.NS",
    "AIAENG.NS", "APLAPOLLO.NS", "AUBANK.NS", "AARTIIND.NS", "AAVAS.NS",
    "ABBOTINDIA.NS", "ADANIENSOL.NS", "ADANIENT.NS", "ADANIGREEN.NS", "ADANIPORTS.NS",
    "ADANIPOWER.NS", "ATGL.NS", "AWL.NS", "ABCAPITAL.NS", "ABFRL.NS",
    "AEGISLOG.NS", "AETHER.NS", "AFFLE.NS", "AJANTPHARM.NS", "APLLTD.NS",
    "ALKEM.NS", "ALKYLAMINE.NS", "ALLCARGO.NS", "ALOKINDS.NS", "ARE&M.NS",
    "AMBER.NS", "AMBUJACEM.NS", "ANANDRATHI.NS", "ANGELONE.NS", "ANURAS.NS",
    "APARINDS.NS", "APOLLOHOSP.NS", "APOLLOTYRE.NS", "APTUS.NS", "ACI.NS",
    "ASAHIINDIA.NS", "ASHOKLEY.NS", "ASIANPAINT.NS", "ASTERDM.NS", "ASTRAZEN.NS",
    "ASTRAL.NS", "ATUL.NS", "AUROPHARMA.NS", "AVANTIFEED.NS", "DMART.NS",
    "AXISBANK.NS", "BEML.NS", "BLS.NS", "BSE.NS", "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "BAJAJHLDNG.NS", "BALAMINES.NS", "BALKRISIND.NS",
    "BALRAMCHIN.NS", "BANDHANBNK.NS", "BANKBARODA.NS", "BANKINDIA.NS", "MAHABANK.NS",
    "BATAINDIA.NS", "BAYERCROP.NS", "BERGEPAINT.NS", "BDL.NS", "BEL.NS",
    "BHARATFORG.NS", "BHEL.NS", "BPCL.NS", "BHARTIARTL.NS", "BIKAJI.NS",
    "BIOCON.NS", "BIRLACORPN.NS", "BSOFT.NS", "BLUEDART.NS", "BLUESTARCO.NS",
    "BBTC.NS", "BORORENEW.NS", "BOSCHLTD.NS", "BRIGADE.NS", "BRITANNIA.NS",
    "MAPMYINDIA.NS", "CCL.NS", "CESC.NS", "CGPOWER.NS", "CIEINDIA.NS",
    "CRISIL.NS", "CSBBANK.NS", "CAMPUS.NS", "CANFINHOME.NS", "CANBK.NS",
    "CAPLIPOINT.NS", "CGCL.NS", "CARBORUNIV.NS", "CASTROLIND.NS", "CEATLTD.NS",
    "CELLO.NS", "CENTRALBK.NS", "CDSL.NS", "CENTURYPLY.NS", "ABREL.NS",
    "CERA.NS", "CHALET.NS", "CHAMBLFERT.NS", "CHEMPLASTS.NS", "CHENNPETRO.NS",
    "CHOLAHLDNG.NS", "CHOLAFIN.NS", "CIPLA.NS", "CUB.NS", "CLEAN.NS",
    "COALINDIA.NS", "COCHINSHIP.NS", "COFORGE.NS", "COLPAL.NS", "CAMS.NS",
    "CONCORDBIO.NS", "CONCOR.NS", "COROMANDEL.NS", "CRAFTSMAN.NS", "CREDITACC.NS",
    "CROMPTON.NS", "CUMMINSIND.NS", "CYIENT.NS", "DCMSHRIRAM.NS", "DLF.NS",
    "DOMS.NS", "DABUR.NS", "DALBHARAT.NS", "DATAPATTNS.NS", "DEEPAKFERT.NS",
    "DEEPAKNTR.NS", "DELHIVERY.NS", "DEVYANI.NS", "DIVISLAB.NS", "DIXON.NS",
    "LALPATHLAB.NS", "DRREDDY.NS", "EIDPARRY.NS", "EIHOTEL.NS", "EPL.NS",
    "EASEMYTRIP.NS", "EICHERMOT.NS", "ELECON.NS", "ELGIEQUIP.NS", "EMAMILTD.NS",
    "ENDURANCE.NS", "ENGINERSIN.NS", "EQUITASBNK.NS", "ERIS.NS", "ESCORTS.NS",
    "EXIDEIND.NS", "FDC.NS", "NYKAA.NS", "FEDERALBNK.NS", "FACT.NS",
    "FINEORG.NS", "FINCABLES.NS", "FINPIPE.NS", "FSL.NS", "FIVESTAR.NS",
    "FORTIS.NS", "GAIL.NS", "GMMPFAUDLR.NS", "GMRINFRA.NS", "GRSE.NS",
    "GICRE.NS", "GILLETTE.NS", "GLAND.NS", "GLAXO.NS", "ALIVUS.NS",
    "GLENMARK.NS", "MEDANTA.NS", "GPIL.NS", "GODFRYPHLP.NS", "GODREJCP.NS",
    "GODREJIND.NS", "GODREJPROP.NS", "GRANULES.NS", "GRAPHITE.NS", "GRASIM.NS",
    "GESHIP.NS", "GRINDWELL.NS", "GAEL.NS", "FLUOROCHEM.NS", "GUJGASLTD.NS",
    "GMDCLTD.NS", "GNFC.NS", "GPPL.NS", "GSFC.NS", "GSPL.NS",
    "HEG.NS", "HBLENGINE.NS", "HCLTECH.NS", "HDFCAMC.NS", "HDFCBANK.NS",
    "HDFCLIFE.NS", "HFCL.NS", "HAPPSTMNDS.NS", "HAPPYFORGE.NS", "HAVELLS.NS",
    "HEROMOTOCO.NS", "HSCL.NS", "HINDALCO.NS", "HAL.NS", "HINDCOPPER.NS",
    "HINDPETRO.NS", "HINDUNILVR.NS", "HINDZINC.NS", "POWERINDIA.NS", "HOMEFIRST.NS",
    "HONASA.NS", "HONAUT.NS", "HUDCO.NS", "ICICIBANK.NS", "ICICIGI.NS",
    "ICICIPRULI.NS", "ISEC.NS", "IDBI.NS", "IDFCFIRSTB.NS", "IFCI.NS",
    "IIFL.NS", "IRB.NS", "IRCON.NS", "ITC.NS", "ITI.NS",
    "INDIACEM.NS", "INDIAMART.NS", "INDIANB.NS", "IEX.NS", "INDHOTEL.NS",
    "IOC.NS", "IOB.NS", "IRCTC.NS", "IRFC.NS", "INDIGOPNTS.NS",
    "IGL.NS", "INDUSTOWER.NS", "INDUSINDBK.NS", "NAUKRI.NS", "INFY.NS",
    "INOXWIND.NS", "INTELLECT.NS", "INDIGO.NS", "IPCALAB.NS", "JBCHEPHARM.NS",
    "JKCEMENT.NS", "JBMA.NS", "JKLAKSHMI.NS", "JKPAPER.NS", "JMFINANCIL.NS",
    "JSWENERGY.NS", "JSWINFRA.NS", "JSWSTEEL.NS", "JAIBALAJI.NS", "J&KBANK.NS",
    "JINDALSAW.NS", "JSL.NS", "JINDALSTEL.NS", "JIOFIN.NS", "JUBLFOOD.NS",
    "JUBLINGREA.NS", "JUBLPHARMA.NS", "JWL.NS", "JUSTDIAL.NS", "JYOTHYLAB.NS",
    "KPRMILL.NS", "KEI.NS", "KNRCON.NS", "KPITTECH.NS", "KRBL.NS",
    "KSB.NS", "KAJARIACER.NS", "KPIL.NS", "KALYANKJIL.NS", "KANSAINER.NS",
    "KARURVYSYA.NS", "KAYNES.NS", "KEC.NS", "KFINTECH.NS", "KOTAKBANK.NS",
    "KIMS.NS", "LTF.NS", "LTTS.NS", "LICHSGFIN.NS", "LTIM.NS",
    "LT.NS", "LATENTVIEW.NS", "LAURUSLABS.NS", "LXCHEM.NS", "LEMONTREE.NS",
    "LICI.NS", "LINDEINDIA.NS", "LLOYDSME.NS", "LUPIN.NS", "MMTC.NS",
    "MRF.NS", "MTARTECH.NS", "LODHA.NS", "MGL.NS", "MAHSEAMLES.NS",
    "M&MFIN.NS", "M&M.NS", "MHRIL.NS", "MAHLIFE.NS", "MANAPPURAM.NS",
    "MRPL.NS", "MANKIND.NS", "MARICO.NS", "MARUTI.NS", "MASTEK.NS",
    "MFSL.NS", "MAXHEALTH.NS", "MAZDOCK.NS", "MEDPLUS.NS", "METROBRAND.NS",
    "METROPOLIS.NS", "MINDACORP.NS", "MSUMI.NS", "MOTILALOFS.NS", "MPHASIS.NS",
    "MCX.NS", "MUTHOOTFIN.NS", "NATCOPHARM.NS", "NBCC.NS", "NCC.NS",
    "NHPC.NS", "NLCINDIA.NS", "NMDC.NS", "NSLNISP.NS", "NTPC.NS",
    "NH.NS", "NATIONALUM.NS", "NAVINFLUOR.NS", "NESTLEIND.NS", "NETWORK18.NS",
    "NAM-INDIA.NS", "NUVAMA.NS", "NUVOCO.NS", "OBEROIRLTY.NS", "ONGC.NS",
    "OIL.NS", "OLECTRA.NS", "PAYTM.NS", "OFSS.NS", "POLICYBZR.NS",
    "PCBL.NS", "PIIND.NS", "PNBHOUSING.NS", "PNCINFRA.NS", "PVRINOX.NS",
    "PAGEIND.NS", "PATANJALI.NS", "PERSISTENT.NS", "PETRONET.NS", "PHOENIXLTD.NS",
    "PIDILITIND.NS", "PEL.NS", "PPLPHARMA.NS", "POLYMED.NS", "POLYCAB.NS",
    "POONAWALLA.NS", "PFC.NS", "POWERGRID.NS", "PRAJIND.NS", "PRESTIGE.NS",
    "PRINCEPIPE.NS", "PRSMJOHNSN.NS", "PGHH.NS", "PNB.NS", "QUESS.NS",
    "RRKABEL.NS", "RBLBANK.NS", "RECLTD.NS", "RHIM.NS", "RITES.NS",
    "RADICO.NS", "RVNL.NS", "RAILTEL.NS", "RAINBOW.NS", "RAJESHEXPO.NS",
    "RKFORGE.NS", "RCF.NS", "RATNAMANI.NS", "RTNINDIA.NS", "RAYMOND.NS",
    "REDINGTON.NS", "RELIANCE.NS", "RBA.NS", "ROUTE.NS", "SBFC.NS",
    "SBICARD.NS", "SBILIFE.NS", "SJVN.NS", "SKFINDIA.NS", "SRF.NS",
    "SAFARI.NS", "SAMMAANCAP.NS", "MOTHERSON.NS", "SANOFI.NS", "SAPPHIRE.NS",
    "SAREGAMA.NS", "SCHAEFFLER.NS", "SCHNEIDER.NS", "SHREECEM.NS", "RENUKA.NS",
    "SHRIRAMFIN.NS", "SHYAMMETL.NS", "SIEMENS.NS", "SIGNATURE.NS", "SOBHA.NS",
    "SOLARINDS.NS", "SONACOMS.NS", "SONATSOFTW.NS", "STARHEALTH.NS", "SBIN.NS",
    "SAIL.NS", "SWSOLAR.NS", "STLTECH.NS", "SUMICHEM.NS", "SPARC.NS",
    "SUNPHARMA.NS", "SUNTV.NS", "SUNDARMFIN.NS", "SUNDRMFAST.NS", "SUNTECK.NS",
    "SUPREMEIND.NS", "SUVENPHAR.NS", "SUZLON.NS", "SYNGENE.NS", "TVSMOTOR.NS",
    "TVSSCS.NS", "TANLA.NS", "TATACOMM.NS", "TATACONSUM.NS", "TATAELXSI.NS",
    "TATAMOTORS.NS", "TATAPOWER.NS", "TATASTEEL.NS", "TATATECH.NS", "TCS.NS",
    "TECHM.NS", "TIINDIA.NS", "TIMETECHNO.NS", "TIMKEN.NS", "TITAGARH.NS",
    "TITAN.NS", "TORNTPHARM.NS", "TORNTPOWER.NS", "TRENT.NS", "TRIDENT.NS",
    "TRIVENI.NS", "TRITURBINE.NS", "UNIPARTS.NS", "UTIAMC.NS", "UPL.NS",
    "UCOBANK.NS", "UFLEX.NS", "ULTRACEMCO.NS", "UNIONBANK.NS", "UBL.NS",
    "MCDOWELL-N.NS", "UNOMINDA.NS", "VBL.NS", "VEDL.NS", "VIJAYA.NS",
    "VINATIORGA.NS", "VOLTAS.NS", "VSTIND.NS", "WELCORP.NS", "WELSPUNIND.NS",
    "WESTLIFE.NS", "WHIRLPOOL.NS", "WIPRO.NS", "WOCKPHARMA.NS", "YESBANK.NS",
    "ZEEL.NS", "ZENSARTECH.NS", "ZOMATO.NS", "ZYDUSLIFE.NS",
]


# ---------------------------------------------------------------------------
# DDL — auto-created on first run, idempotent on subsequent runs
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ohlc_data (
    id        INT            NOT NULL AUTO_INCREMENT,
    ticker    VARCHAR(30)    NOT NULL,
    date      DATE           NOT NULL,
    open      DECIMAL(14,4),
    high      DECIMAL(14,4),
    low       DECIMAL(14,4),
    close     DECIMAL(14,4),
    volume    BIGINT,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ticker_date (ticker, date)
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

BATCH_SIZE = 2000  # rows per executemany call


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

def run_ingestion() -> None:
    log.info("=" * 60)
    log.info(
        "Incremental OHLCV Ingestion — %s",
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    log.info("=" * 60)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # Step 1: Ensure ohlc_data table exists (idempotent DDL)
        _ensure_table(cursor)
        conn.commit()
        log.info("Table ohlc_data ready.")

        # Detect MySQL version once for the whole session
        use_legacy = _detect_legacy_mysql(cursor)
        if use_legacy:
            log.info("MySQL < 8.0.19 detected — using VALUES() syntax for upserts.")

        # Step 2: Load per-ticker watermarks (last stored date)
        watermarks = _get_watermarks(cursor)
        log.info(
            "Watermarks loaded: %d tickers already in DB, %d are new this run.",
            len(watermarks),
            len([s for s in SYMBOLS if s not in watermarks]),
        )

        # Steps 3 & 4: Fetch from yfinance and upsert into MySQL
        total_rows_affected = 0
        errors = []

        for ticker in SYMBOLS:
            # None means ticker has never been stored before -> full backfill
            last_date = watermarks.get(ticker)

            try:
                df = _fetch_incremental(ticker, last_date)

                if df.empty:
                    continue  # Up-to-date or no data from yfinance

                rows = list(df.itertuples(index=False, name=None))
                affected = _upsert_batch(cursor, rows, use_legacy)
                conn.commit()

                log.info(
                    "  [OK]   %s — %d rows fetched, %d affected (inserts + updates)",
                    ticker, len(rows), affected,
                )
                total_rows_affected += affected

            except Exception as exc:
                conn.rollback()
                log.error("  [ERR]  %s — %s", ticker, exc)
                errors.append((ticker, str(exc)))

        # Step 5: Summary
        log.info("=" * 60)
        log.info(
            "Done. Total rows affected: %d | Errors: %d",
            total_rows_affected, len(errors),
        )
        if errors:
            log.warning("Failed tickers:")
            for sym, err in errors:
                log.warning("  %s: %s", sym, err)
        log.info("=" * 60)

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    run_ingestion()
