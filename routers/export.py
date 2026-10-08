"""
routers/export.py — CSV Export endpoints for scan results, screens, and watchlist.

Provides download-ready CSV streams for:
  - Ad-hoc scan results
  - Preset screen stages (VCP, Blue Sky, etc.)
  - User watchlist
  - Market breadth snapshot

All endpoints are auth-guarded. CSV is streamed via StreamingResponse
so memory stays flat even for large result sets.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from auth import get_current_user
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["Export"])


def _make_csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    """
    Convert a list of dicts into a CSV StreamingResponse.
    Handles Decimal, datetime, date types gracefully.
    """
    if not rows:
        # Return an empty CSV with just a header note
        output = io.StringIO()
        output.write("No data available for export.\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Collect all unique keys across all rows (preserving insertion order)
    all_keys = list(dict.fromkeys(k for row in rows for k in row.keys()))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()

    for row in rows:
        clean_row = {}
        for k, v in row.items():
            if isinstance(v, Decimal):
                clean_row[k] = float(v)
            elif isinstance(v, (datetime, date)):
                clean_row[k] = v.isoformat()
            elif v is None:
                clean_row[k] = ""
            else:
                clean_row[k] = v
        writer.writerow(clean_row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/scan-results")
def export_scan_results(
    signal: str = Query(..., description="Signal field to scan"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=10000),
    current_user: dict = Depends(get_current_user),
):
    """Export scan results for a given signal as CSV download."""
    from scan_engine import ALLOWED_FIELDS, run_scan

    if signal not in ALLOWED_FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown signal: {signal}")

    # Build condition matching signal-scanner logic
    if signal == "NR":
        condition = {"field": "NR", "operator": ">", "value": 0}
    elif signal == "RCS_30D":
        condition = {"field": "RCS_30D", "operator": ">", "value": -999.0}
    elif signal == "rsi_divergence_score":
        condition = {"field": "rsi_divergence_score", "operator": ">", "value": 0.0}
    elif signal in ("rsi_divergence_type", "rsi_divergence_direction"):
        condition = {"field": signal, "operator": "IS NOT NULL"}
    elif signal == "rsi_divergence":
        condition = {"field": "rsi_divergence", "operator": "=="}
    else:
        condition = {"field": signal, "operator": "=="}

    try:
        rows = run_scan(
            conditions=[condition],
            logic="AND",
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Export scan error for signal=%s", signal)
        raise HTTPException(status_code=500, detail="Failed to export scan results.")

    today_str = date.today().isoformat()
    filename = f"stockpro_{signal}_{today_str}.csv"
    return _make_csv_response(rows, filename)


@router.get("/screen/{screen_name}")
def export_screen_results(
    screen_name: str,
    min_market_cap: Optional[float] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Export all stocks from a preset screen as CSV."""
    from routers.screens import get_symbol_market_cap

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT DATE(MAX(Timestamp)) as max_date FROM historical_data")
        row = cursor.fetchone()
        if not row or not row["max_date"]:
            raise HTTPException(status_code=404, detail="No data available.")
        max_date = row["max_date"]

        db_rows = []
        if screen_name in ("stage-2", "stage-2-stocks"):
            cursor.execute("""
                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank, stage, stage_bucket
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND stage = 2
                  AND stage_bucket = 'fresh_breakout'
                ORDER BY IFNULL(rs_rank, 0) DESC
            """, (max_date, max_date))
            db_rows = cursor.fetchall()

        elif screen_name in ("vcp", "blue-sky", "multi-year-breakout", "ipo-base"):
            screen_col_map = {
                "vcp": "screen_vcp",
                "blue-sky": "screen_blue_sky",
                "multi-year-breakout": "screen_multi_year_breakout",
                "ipo-base": "screen_ipo_base",
            }
            col = screen_col_map[screen_name]
            cursor.execute(f"""
                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank,
                       stage_bucket, base_high, pct_from_pivot, base_length_days, contraction_count
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND {col} = 1
                ORDER BY IFNULL(rs_rank, 0) DESC
            """, (max_date, max_date))
            db_rows = cursor.fetchall()

        elif screen_name == "golden-crossover":
            cursor.execute("""
                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank
                FROM historical_data
                WHERE Timestamp >= %s - INTERVAL 7 DAY AND Timestamp < %s + INTERVAL 1 DAY
                  AND signal5 = 1
                ORDER BY Timestamp DESC, IFNULL(rs_rank, 0) DESC
            """, (max_date, max_date))
            db_rows = cursor.fetchall()

        elif screen_name == "high-relative-volume":
            cursor.execute("""
                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND (screen_high_relative_volume = 1 OR High_Relative_Volume_30 = 1)
                ORDER BY IFNULL(rs_rank, 0) DESC
            """, (max_date, max_date))
            db_rows = cursor.fetchall()

        elif screen_name == "high-delivery-volume":
            cursor.execute("""
                SELECT ticker as Symbol, date as Timestamp, close as Close, open as Open, volume as Volume, delivery_quantity,
                       ROUND((delivery_quantity / volume) * 100, 2) as delivery_pct
                FROM ohlc_data
                WHERE date = (SELECT MAX(date) FROM ohlc_data)
                  AND delivery_quantity IS NOT NULL AND volume > 0 AND (delivery_quantity / volume) >= 0.8
                ORDER BY delivery_pct DESC
            """)
            db_rows = cursor.fetchall()

        elif screen_name == "rsi-divergence":
            cursor.execute("""
                SELECT Symbol, Timestamp, Close, Open, IFNULL(rs_rank, 0) as rs_rank,
                       rsi_divergence_type, rsi_divergence_direction, rsi_divergence_score
                FROM historical_data
                WHERE Timestamp >= %s - INTERVAL 20 DAY
                  AND rsi_divergence_type IS NOT NULL
                ORDER BY rsi_divergence_score DESC
            """, (max_date,))
            db_rows = cursor.fetchall()

        elif screen_name == "ema-convergence":
            cursor.execute("""
                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank,
                       convergence_3, convergence_4, convergence_5
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND (convergence_3 = 1 OR convergence_4 = 1 OR convergence_5 = 1)
                ORDER BY IFNULL(rs_rank, 0) DESC
            """, (max_date, max_date))
            db_rows = cursor.fetchall()

        else:
            raise HTTPException(status_code=400, detail=f"Unknown screen: {screen_name}")

        clean_rows = []
        for r in db_rows:
            sym = r["Symbol"]
            mcap = get_symbol_market_cap(sym)
            if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):
                continue
            r["market_cap_cr"] = mcap
            clean = {}
            for k, v in r.items():
                if isinstance(v, Decimal):
                    clean[k] = float(v)
                elif isinstance(v, (datetime, date)):
                    clean[k] = str(v)
                else:
                    clean[k] = v
            clean_rows.append(clean)

        today_str = date.today().isoformat()
        return _make_csv_response(clean_rows, f"stockpro_{screen_name}_{today_str}.csv")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Export screen error for %s", screen_name)
        raise HTTPException(status_code=500, detail="Failed to export screen results.")
    finally:
        cursor.close()
        conn.close()


@router.get("/watchlist")
def export_watchlist(current_user: dict = Depends(get_current_user)):
    """Export the current user's watchlist as CSV."""
    from routers.watchlist import _get_user_id, _get_symbol_details

    user_id = _get_user_id(current_user)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT symbol, created_at FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        db_rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    rows = []
    for r in db_rows:
        details = _get_symbol_details(r["symbol"])
        details["added_at"] = (
            r["created_at"].strftime("%Y-%m-%d %H:%M")
            if hasattr(r["created_at"], "strftime")
            else str(r["created_at"])
        )
        rows.append(details)

    today_str = date.today().isoformat()
    return _make_csv_response(rows, f"stockpro_watchlist_{today_str}.csv")


@router.get("/market-breadth")
def export_market_breadth(current_user: dict = Depends(get_current_user)):
    """Export the latest market breadth snapshot as CSV."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT DATE(MAX(Timestamp)) as max_date FROM historical_data")
        row = cursor.fetchone()
        if not row or not row["max_date"]:
            raise HTTPException(status_code=404, detail="No data available.")
        max_date = row["max_date"]

        cursor.execute(
            """
            SELECT Symbol, Close, SMA50, SMA200, RSI14,
                   IFNULL(rs_rank, 0) as rs_rank,
                   stage, stage_bucket,
                   new_52w_high, new_52w_low,
                   base_active, breakout_today
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
            ORDER BY IFNULL(rs_rank, 0) DESC
            """,
            (max_date, max_date),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    # Serialize Decimal fields
    clean_rows = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if isinstance(v, Decimal):
                clean[k] = float(v)
            elif isinstance(v, (datetime, date)):
                clean[k] = str(v)
            else:
                clean[k] = v
        clean_rows.append(clean)

    return _make_csv_response(clean_rows, f"stockpro_breadth_{max_date}.csv")
