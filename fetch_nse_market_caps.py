#!/usr/bin/env python3
"""
Populate nse_stock_market_caps.csv from NSE's daily PR market-cap report.

Requirements:
    pip install requests

Usage (Windows / VS Code):
    python fetch_nse_market_caps.py

Optional:
    python fetch_nse_market_caps.py --date 2026-10-01
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install with: pip install requests")
    raise SystemExit(1)

DEFAULT_INPUTS = [
    Path("Pasted markdown(20261002-082640).md"),
    Path("symbols_list.py"),
]
OUTPUT = Path("nse_stock_market_caps.csv")

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/154.0 Safari/537.36",
    "Accept": "application/zip,application/octet-stream,*/*",
    "Referer": "https://www.nseindia.com/",
}


def find_input_file() -> Path:
    for p in DEFAULT_INPUTS:
        if p.exists():
            return p
    print("Could not find the symbol-list file in the current directory.")
    raise SystemExit(2)


def extract_symbols(path: Path) -> list[str]:
    txt = path.read_text(encoding="utf-8")
    # Works with the pasted markdown format and normal Python lists.
    syms = re.findall(r'"([^\"]+\\?\.NS)"', txt)
    syms = [s.replace("\\.", ".") for s in syms]
    out, seen = [], set()
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def candidate_dates(start: date, days: int = 20):
    for i in range(days + 1):
        yield start - timedelta(days=i)


def archive_urls(d: date):
    ddmmyy = d.strftime("%d%m%y")
    # Current nse-data project documents this archive route; retain the
    # older historical route as a fallback for dates archived there.
    urls = [
        f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{ddmmyy}.zip",
        f"https://archives.nseindia.com/archives/equities/bhavcopy/pr/PR{ddmmyy}.zip",
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{d.year}/{d.strftime('%b').upper()}/PR{ddmmyy}.zip",
        f"https://archives.nseindia.com/content/historical/EQUITIES/{d.year}/{d.strftime('%b').upper()}/PR{ddmmyy}.zip",
    ]
    return urls


def download_pr_zip(session: requests.Session, d: date):
    for url in archive_urls(d):
        try:
            r = session.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and r.content[:2] == b"PK":
                return r.content, url
        except requests.RequestException:
            continue
    return None, None


def extract_mcap_rows(zip_bytes: bytes) -> tuple[str, list[dict[str, str]]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        candidates = [n for n in names if re.fullmatch(r"(?i)mcap\d{6,8}\.csv", Path(n).name)]
        if not candidates:
            raise RuntimeError(f"PR ZIP did not contain an MCAP CSV. Files: {names[:20]}")
        name = candidates[0]
        raw = z.read(name)

    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = None
    rows = []
    for r in reader:
        if not r or not any(x.strip() for x in r):
            continue
        cleaned = [c.strip() for c in r]
        if header is None:
            if any("SYMBOL" in c.upper() for c in cleaned):
                header = cleaned
            continue
        row_dict = {}
        for idx, col in enumerate(header):
            if idx < len(cleaned):
                row_dict[col] = cleaned[idx]
        rows.append(row_dict)
    return name, rows


def parse_decimal(val: str | None) -> Decimal | None:
    if not val:
        return None
    val_clean = re.sub(r"[^\d.-]", "", val.strip())
    if not val_clean:
        return None
    try:
        return Decimal(val_clean)
    except InvalidOperation:
        return None


def format_trade_date(val: str | None) -> str:
    if not val:
        return ""
    val = val.strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return val


def main():
    parser = argparse.ArgumentParser(
        description="Populate nse_stock_market_caps.csv from NSE's daily PR market-cap report."
    )
    parser.add_argument(
        "--date",
        help="Target date in YYYY-MM-DD format (default: latest available trading date).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Input symbol file path (defaults to symbols_list.py).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
        help=f"Output CSV path (default: {OUTPUT}).",
    )
    args = parser.parse_args()

    input_file = args.input or find_input_file()
    print(f"Reading target symbols from: {input_file}")
    symbols = extract_symbols(input_file)
    print(f"Extracted {len(symbols)} symbols from input file.")

    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except Exception as exc:
        print(f"Note: NSE homepage handshake returned: {exc}")

    zip_bytes = None
    found_url = None
    target_d = None

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date format: {args.date}. Expected YYYY-MM-DD.")
            raise SystemExit(1)
        print(f"Fetching PR ZIP for requested date: {d} ...")
        zip_bytes, found_url = download_pr_zip(session, d)
        if not zip_bytes:
            print(f"Could not download PR ZIP for requested date {d}.")
            raise SystemExit(1)
        target_d = d
    else:
        print("Searching for latest available NSE PR report across recent trading days...")
        today = date.today()
        for d in candidate_dates(today, days=25):
            print(f"  Checking {d} ...", end="\r", flush=True)
            content, url = download_pr_zip(session, d)
            if content:
                zip_bytes = content
                found_url = url
                target_d = d
                print(f"Found PR report for date: {d} at {url} (size: {len(content):,} bytes)")
                break

    if not zip_bytes:
        print("\nCould not find or download any recent PR report from NSE archives.")
        raise SystemExit(1)

    csv_name, raw_rows = extract_mcap_rows(zip_bytes)
    print(f"Extracted '{csv_name}' containing {len(raw_rows)} records from PR ZIP.")

    # Find the right column keys
    sample = raw_rows[0] if raw_rows else {}
    col_sym = next((k for k in sample if "SYMBOL" in k.upper()), "Symbol")
    col_name = next((k for k in sample if "SECURITY" in k.upper() or "NAME" in k.upper()), "Security Name")
    col_series = next((k for k in sample if "SERIES" in k.upper()), "Series")
    col_date = next((k for k in sample if "TRADE DATE" in k.upper()), "Trade Date")
    col_close = next((k for k in sample if "CLOSE" in k.upper()), "Close Price/Paid up value(Rs.)")
    col_mcap = next((k for k in sample if "MARKET CAP" in k.upper()), "Market Cap(Rs.)")
    col_fv = next((k for k in sample if "FACE" in k.upper()), "Face Value(Rs.)")
    col_issue = next((k for k in sample if "ISSUE" in k.upper()), "Issue Size")
    col_cat = next((k for k in sample if "CATEGORY" in k.upper()), "Category")

    # Index by symbol, prioritizing EQ series over others
    mcap_map: dict[str, dict[str, str]] = {}
    mcap_prio: dict[str, int] = {}

    for r in raw_rows:
        sym_raw = r.get(col_sym, "").strip()
        if not sym_raw:
            continue
        series = r.get(col_series, "").strip().upper()
        # Priority: EQ=1, BE=2, SM=3, other=4
        prio = 1 if series == "EQ" else (2 if series == "BE" else (3 if series == "SM" else 4))
        if sym_raw not in mcap_map or prio < mcap_prio.get(sym_raw, 99):
            mcap_map[sym_raw] = r
            mcap_prio[sym_raw] = prio

    fieldnames = [
        "symbol",
        "company_name",
        "series",
        "trade_date",
        "close_price",
        "market_cap_rs",
        "market_cap_cr",
        "face_value",
        "issue_size",
        "category",
    ]

    matched_count = 0
    out_rows = []

    for full_sym in symbols:
        base_sym = full_sym.replace(".NS", "").strip()
        mcap_row = mcap_map.get(base_sym)

        if mcap_row:
            matched_count += 1
            comp_name = mcap_row.get(col_name, "").strip()
            series = mcap_row.get(col_series, "").strip()
            trade_d = format_trade_date(mcap_row.get(col_date, ""))
            category = mcap_row.get(col_cat, "").strip()

            close_dec = parse_decimal(mcap_row.get(col_close))
            fv_dec = parse_decimal(mcap_row.get(col_fv))
            issue_dec = parse_decimal(mcap_row.get(col_issue))
            mcap_dec = parse_decimal(mcap_row.get(col_mcap))

            mcap_cr_str = ""
            if mcap_dec is not None:
                cr_val = (mcap_dec / Decimal(10_000_000)).quantize(Decimal("0.01"))
                mcap_cr_str = str(cr_val)

            out_rows.append({
                "symbol": full_sym,
                "company_name": comp_name,
                "series": series,
                "trade_date": trade_d or (target_d.isoformat() if target_d else ""),
                "close_price": str(close_dec) if close_dec is not None else "",
                "market_cap_rs": str(mcap_dec) if mcap_dec is not None else "",
                "market_cap_cr": mcap_cr_str,
                "face_value": str(fv_dec) if fv_dec is not None else "",
                "issue_size": str(int(issue_dec)) if issue_dec is not None else "",
                "category": category,
            })
        else:
            out_rows.append({
                "symbol": full_sym,
                "company_name": "",
                "series": "",
                "trade_date": target_d.isoformat() if target_d else "",
                "close_price": "",
                "market_cap_rs": "",
                "market_cap_cr": "",
                "face_value": "",
                "issue_size": "",
                "category": "",
            })

    output_path = args.output
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print("\n" + "=" * 60)
    print(f"SUCCESS: Market cap data saved to: {output_path.resolve()}")
    print(f"  Report Date:     {target_d}")
    print(f"  Source URL:      {found_url}")
    print(f"  Total Symbols:   {len(symbols)}")
    print(f"  Matched Symbols: {matched_count} ({matched_count / len(symbols) * 100:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    main()
