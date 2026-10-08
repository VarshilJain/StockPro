"""
sync_sectors.py — Sync stock_sectors.csv to MySQL stock_metadata and stock_sectors.py
=====================================================================================
Run this whenever you edit or add sectors / industries in `stock_sectors.csv`.
"""

import os
import json
import logging
import pandas as pd
from database import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("sync_sectors")


def sync():
    csv_path = os.path.join(os.path.dirname(__file__), "stock_sectors.csv")
    if not os.path.exists(csv_path):
        log.error("stock_sectors.csv not found at %s", csv_path)
        return

    df = pd.read_csv(csv_path).fillna("")
    log.info("Read %d rows from %s", len(df), csv_path)

    # 1. Update MySQL stock_metadata
    conn = get_connection()
    cur = conn.cursor()

    upsert_sql = """
        INSERT INTO stock_metadata (symbol, company_name, sector, industry)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            company_name = VALUES(company_name),
            sector = VALUES(sector),
            industry = VALUES(industry),
            updated_at = CURRENT_TIMESTAMP
    """

    batch = []
    for _, row in df.iterrows():
        sym = str(row["symbol"]).strip()
        name = str(row["company_name"]).strip() if row["company_name"] else sym
        sec = str(row["sector"]).strip() if row["sector"] else ""
        ind = str(row["industry"]).strip() if row["industry"] else ""
        batch.append((sym, name, sec, ind))

    cur.executemany(upsert_sql, batch)
    conn.commit()
    log.info("Synced %d rows to MySQL `stock_metadata` table.", len(batch))
    cur.close()
    conn.close()

    # 2. Update stock_sectors.py
    py_path = os.path.join(os.path.dirname(__file__), "stock_sectors.py")
    with open(py_path, "w", encoding="utf-8") as f:
        f.write('"""\nstock_sectors.py\n----------------\nMaster sector and industry classification mapping for all NSE stocks.\n"""\n\nSTOCK_SECTORS = {\n')
        for sym, name, sec, ind in batch:
            f.write(f'    "{sym}": {{"company_name": {json.dumps(name)}, "sector": {json.dumps(sec)}, "industry": {json.dumps(ind)}}},\n')
        f.write('}\n\n')
        f.write('''def get_stock_info(symbol: str) -> dict:
    """Return dictionary with company_name, sector, and industry for a symbol."""
    return STOCK_SECTORS.get(symbol.upper(), {"company_name": symbol, "sector": "Other", "industry": "Other"})

def get_sector(symbol: str) -> str:
    """Return sector string for a symbol (or 'Other' if unclassified)."""
    info = STOCK_SECTORS.get(symbol.upper())
    return info["sector"] if info and info["sector"] else "Other"

def get_industry(symbol: str) -> str:
    """Return industry string for a symbol (or 'Other' if unclassified)."""
    info = STOCK_SECTORS.get(symbol.upper())
    return info["industry"] if info and info["industry"] else "Other"

def get_all_sectors() -> list[str]:
    """Return sorted unique list of all known sectors."""
    sectors = set(v["sector"] for v in STOCK_SECTORS.values() if v["sector"])
    return sorted(list(sectors))

def get_all_industries(sector: str = None) -> list[str]:
    """Return sorted unique list of all known industries, optionally filtered by sector."""
    if sector:
        industries = set(v["industry"] for v in STOCK_SECTORS.values() if v["sector"].lower() == sector.lower() and v["industry"])
    else:
        industries = set(v["industry"] for v in STOCK_SECTORS.values() if v["industry"])
    return sorted(list(industries))
''')
    log.info("Updated %s successfully.", py_path)
    print("Done! Sector and industry classifications are fully synced.")


if __name__ == "__main__":
    sync()
