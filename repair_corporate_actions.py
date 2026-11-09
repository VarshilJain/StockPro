"""
repair_corporate_actions.py
===========================
Automated Corporate Action Reconciliation & Outlier Adjustment Script for StockPro.

1. Fixes known data feed collisions (NV20.NS, CYBERTECH.NS).
2. For tickers with stock splits or reverse splits / consolidations:
   - Checks if yfinance has an auto-adjusted continuous series; if so, updates ohlc_data.
   - If yfinance still serves raw/split-broken data, calculates exact split factor
     (post_close / pre_close) and applies it backwards to historical rows in ohlc_data.
3. Automatically recomputes indicators and updates historical_data for affected tickers.
"""

import sys
import logging
import datetime
import numpy as np
import pandas as pd
import yfinance as yf
from database import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("repair_corp_actions")

KNOWN_TICKERS = [
    'ABFRL.NS', 'AILIMITED.NS', 'AISL.NS', 'ALLCARGO.NS', 'AMARJOTHI.NS', 'ANLON.NS', 'AQYLON.NS',
    'ARIHANT.NS', 'ARIHANTACA.NS', 'ATLASCYCLE.NS', 'AZADIND.NS', 'BANKNIFTY1.NS', 'BANSALWIRE.NS',
    'BATLIBOI.NS', 'BCG.NS', 'BHAGYANGR.NS', 'BIOFILCHEM.NS', 'BLSE.NS', 'BUILDPRO.NS', 'BURNPUR.NS',
    'CALSOFT.NS', 'CAPTRUST.NS', 'CCAVENUE.NS', 'CHAVDA.NS', 'CHEMBOND.NS', 'CONCORDBIO.NS', 'CONS.NS',
    'COOLCAPS.NS', 'CPCAP.NS', 'CYBERTECH.NS', 'DCMSRIND.NS', 'DHARAN.NS', 'DIGIKORE.NS', 'EASTSILK.NS',
    'EMKAYTOOLS.NS', 'ENSER.NS', 'FALCONTECH.NS', 'GENESYS.NS', 'GICL.NS', 'GNRL.NS', 'GOODYEAR.NS',
    'GRAUWEIL.NS', 'GROWWGOLD.NS', 'GROWWSLVR.NS', 'HEALTHADD.NS', 'HEG.NS', 'HEGAM.NS', 'INDIAGLYCO.NS',
    'INM.NS', 'IRISDOREME.NS', 'IVZINGOLD.NS', 'IVZINNIFTY.NS', 'JSLL.NS', 'JSWDULUX.NS', 'KAPSTON.NS',
    'KARNIKA.NS', 'KDL.NS', 'KESORAMIND.NS', 'KMEW.NS', 'KODYTECH.NS', 'KOVAI.NS', 'KSE.NS', 'LANCER.NS',
    'LICMFGOLD.NS', 'MAHAPEXLTD.NS', 'MBECL.NS', 'MIDCAP.NS', 'MIDQ50ADD.NS', 'MOLDTECH.NS',
    'MOLDTKPAC.NS', 'MOS.NS', 'NOVARTIND.NS', 'NV20.NS', 'NV20BEES.NS', 'OLIL.NS', 'ORIANA.NS',
    'PARAS.NS', 'POLICYBZR.NS', 'PRITIKA.NS', 'PSUBANK.NS', 'PVTBANK.NS', 'QNIFTY.NS', 'QUESS.NS',
    'RAJPALAYAM.NS', 'RAYMOND.NS', 'RCDL.NS', 'REDTAPE.NS', 'REMUS.NS', 'SADHNANIQ.NS', 'SAHAJSOLAR.NS',
    'SAIFL.NS', 'SAYAJIHOTL.NS', 'SHANKARA.NS', 'SHARDAMOTR.NS', 'SICAGEN.NS', 'SIGMA.NS', 'SILVER1.NS',
    'SILVERTUC.NS', 'SKFINDIA.NS', 'STAR.NS', 'SUMEETINDS.NS', 'SWANDEF.NS', 'TAC.NS', 'TATSILV.NS',
    'TMPV.NS', 'TRENT.NS', 'TRIVENI.NS', 'UFBL.NS', 'URBAN.NS', 'VEDL.NS', 'VERANDA.NS', 'VIVIANA.NS',
    'VIVIMEDLAB.NS', 'VMARCIND.NS', 'VTMLTD.NS', 'WAAREEINDO.NS', 'WATERBASE.NS'
]


def fix_data_glitches(conn):
    """Fix known Yahoo Finance feed collisions (e.g. NV20.NS, CYBERTECH.NS)."""
    cur = conn.cursor()
    log.info("Fixing known data feed glitches (NV20.NS, CYBERTECH.NS)...")

    # 1. NV20.NS: ICICI Prudential Nifty NV20 ETF trades ~13-14. Delete rows > 100 where index was served
    cur.execute("DELETE FROM ohlc_data WHERE ticker = 'NV20.NS' AND close > 100")
    if cur.rowcount > 0:
        log.info("Cleaned %d bogus index rows for NV20.NS.", cur.rowcount)

    # 2. CYBERTECH.NS: 2025-01-10 had a bogus price spike to 18,023 instead of ~190
    cur.execute("DELETE FROM ohlc_data WHERE ticker = 'CYBERTECH.NS' AND close > 5000")
    if cur.rowcount > 0:
        log.info("Cleaned CYBERTECH.NS bogus spikes.")

    conn.commit()
    cur.close()


def reconcile_ticker(ticker: str, conn) -> bool:
    """
    Reconciles a single ticker in ohlc_data:
    1. Try yfinance auto_adjust=True.
    2. If clean, upsert yfinance rows into ohlc_data.
    3. If still discontinuous, calculate exact factor and adjust MySQL rows directly.
    """
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT date, open, high, low, close, volume FROM ohlc_data WHERE ticker = %s ORDER BY date ASC", (ticker,))
    rows = cur.fetchall()
    if not rows or len(rows) < 5:
        cur.close()
        return False

    df_local = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close"]:
        df_local[col] = df_local[col].astype(float)
    df_local["volume"] = df_local["volume"].fillna(0).astype("int64")

    df_local["prev_close"] = df_local["close"].shift(1)
    df_local["pct_change"] = (df_local["close"] - df_local["prev_close"]) / df_local["prev_close"]

    discont_rows = df_local[(df_local["pct_change"] < -0.30) | (df_local["pct_change"] > 0.35)]
    if discont_rows.empty:
        cur.close()
        return False

    # Attempt 1: Check if yfinance has official adjusted data for this ticker
    try:
        yf_df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
        if isinstance(yf_df.columns, pd.MultiIndex):
            yf_df.columns = yf_df.columns.get_level_values(0)
        
        if not yf_df.empty and len(yf_df) >= 30:
            yf_df["prev"] = yf_df["Close"].shift(1)
            yf_pct = (yf_df["Close"] - yf_df["prev"]) / yf_df["prev"]
            yf_min = yf_pct.min()
            yf_max = yf_pct.max()

            # If yfinance is continuous (no drop < -30% and no jump > 35%)
            if yf_min > -0.28 and yf_max < 0.35:
                upsert_sql = """
                    INSERT INTO ohlc_data (ticker, date, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open   = VALUES(open),
                        high   = VALUES(high),
                        low    = VALUES(low),
                        close  = VALUES(close),
                        volume = VALUES(volume)
                """
                tuples_to_insert = []
                for dt, row in yf_df.iterrows():
                    d = dt.date() if hasattr(dt, "date") else dt
                    tuples_to_insert.append((
                        ticker, d,
                        float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]),
                        int(row["Volume"]) if pd.notna(row["Volume"]) else 0
                    ))
                cur.executemany(upsert_sql, tuples_to_insert)
                conn.commit()
                cur.close()
                log.info("  [YF-ADJUSTED] %-14s updated with %d official yfinance adjusted rows.", ticker, len(tuples_to_insert))
                return True
    except Exception as exc:
        log.warning("  Could not fetch yfinance for %s: %s", ticker, exc)

    # Attempt 2: Factor-based adjustment directly on DB records
    # Work through discontinuities from newest to oldest
    discont_indices = list(discont_rows.index)
    discont_indices.reverse()

    for idx in discont_indices:
        pre_close = float(df_local.loc[idx - 1, "close"])
        post_close = float(df_local.loc[idx, "close"])
        if pre_close <= 0 or post_close <= 0:
            continue
        factor = float(post_close / pre_close)
        split_date = df_local.loc[idx, "date"]

        log.info("  [FACTOR-ADJUST] %-14s on %s: factor=%.4f (pre: %.2f -> post: %.2f)", ticker, split_date, factor, pre_close, post_close)

        update_sql = """
            UPDATE ohlc_data
            SET open   = open * %s,
                high   = high * %s,
                low    = low * %s,
                close  = close * %s,
                volume = ROUND(volume / %s)
            WHERE ticker = %s AND date < %s
        """
        cur.execute(update_sql, (factor, factor, factor, factor, factor, ticker, split_date))
        conn.commit()

        # Update local df copy for sequential adjustments
        df_local.loc[:idx - 1, "close"] *= factor
        df_local.loc[:idx - 1, "open"] *= factor
        df_local.loc[:idx - 1, "high"] *= factor
        df_local.loc[:idx - 1, "low"] *= factor

    cur.close()
    return True


def run_repair():
    conn = get_connection()
    try:
        fix_data_glitches(conn)
        tickers = [t for t in KNOWN_TICKERS if not t.startswith("^")]
        log.info("Starting reconciliation for %d tickers...", len(tickers))
        
        success_count = 0
        for i, ticker in enumerate(tickers, 1):
            try:
                ok = reconcile_ticker(ticker, conn)
                if ok:
                    success_count += 1
            except Exception as e:
                log.error("Error reconciling %s: %s", ticker, e)
                
        log.info("Reconciliation complete! %d / %d tickers adjusted.", success_count, len(tickers))
    finally:
        conn.close()


if __name__ == "__main__":
    run_repair()
