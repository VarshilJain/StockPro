"""
routers/screens.py - Preset pattern screen endpoints and stage summary
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from auth import get_current_user
from database import get_connection
from scan_engine import run_scan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Screens"])

def _run_single_flag_scan(flag: str) -> list[dict]:
    try:
        rows = run_scan(
            conditions=[{"field": flag, "operator": "=="}],
            logic="AND"
        )
        return rows
    except Exception as exc:
        logger.exception(f"Error running scan for {flag}")
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/screens/vcp")
def get_vcp_screen(current_user: dict = Depends(get_current_user)):
    rows = _run_single_flag_scan("screen_vcp")
    return JSONResponse(content={"count": len(rows), "results": rows})

@router.get("/screens/blue-sky")
def get_blue_sky_screen(current_user: dict = Depends(get_current_user)):
    rows = _run_single_flag_scan("screen_blue_sky")
    return JSONResponse(content={"count": len(rows), "results": rows})

@router.get("/screens/multi-year-breakout")
def get_multi_year_screen(current_user: dict = Depends(get_current_user)):
    rows = _run_single_flag_scan("screen_multi_year_breakout")
    return JSONResponse(content={"count": len(rows), "results": rows})

@router.get("/screens/ipo-base")
def get_ipo_base_screen(current_user: dict = Depends(get_current_user)):
    rows = _run_single_flag_scan("screen_ipo_base")
    return JSONResponse(content={"count": len(rows), "results": rows})

@router.get("/stage-summary")
def get_stage_summary(current_user: dict = Depends(get_current_user)):
    """
    Returns counts + a few sample rows per bucket:
      { "forming": {"count": N, "samples": [...]},
        "fresh_breakout": {...}, "climbing": {...}, "played_out": {...} }
    """
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get the latest date
        cursor.execute("SELECT DATE(MAX(Timestamp)) as max_date FROM historical_data")
        row = cursor.fetchone()
        if not row or not row["max_date"]:
            return JSONResponse(content={})
            
        max_date = row["max_date"]
        
        # Fetch rows for the latest date where stage_bucket != 'unknown'
        cursor.execute(
            """
            SELECT Symbol, Timestamp, stage_bucket, Close, base_active, stage, breakout_today
            FROM historical_data
            WHERE DATE(Timestamp) = %s AND stage_bucket != 'unknown'
            ORDER BY Symbol ASC
            """,
            (max_date,)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        summary = {
            "forming": {"count": 0, "samples": []},
            "fresh_breakout": {"count": 0, "samples": []},
            "climbing": {"count": 0, "samples": []},
            "played_out": {"count": 0, "samples": []}
        }
        
        for r in rows:
            bucket = r["stage_bucket"]
            if bucket in summary:
                summary[bucket]["count"] += 1
                if len(summary[bucket]["samples"]) < 5:
                    if not isinstance(r["Timestamp"], str):
                        r["Timestamp"] = r["Timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                    summary[bucket]["samples"].append(r)
                    
        return JSONResponse(content=summary)
    except Exception as exc:
        logger.exception("Error in stage-summary")
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/screens/{screen_name}/stages")
def get_screen_stages(screen_name: str, current_user: dict = Depends(get_current_user)):
    """
    Returns the stage breakdown specifically for one screen, including
    historical failed breakouts from pattern_events for 'played_out'.
    """
    # Map API param to DB column name
    screen_map = {
        "vcp": "screen_vcp",
        "blue-sky": "screen_blue_sky",
        "multi-year-breakout": "screen_multi_year_breakout",
        "ipo-base": "screen_ipo_base"
    }
    
    db_col = screen_map.get(screen_name)
    if not db_col:
        raise HTTPException(status_code=400, detail="Invalid screen name")
        
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get the latest date
        cursor.execute("SELECT DATE(MAX(Timestamp)) as max_date FROM historical_data")
        row = cursor.fetchone()
        if not row or not row["max_date"]:
            return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})
            
        max_date = row["max_date"]
        
        # 1. Fetch active buckets (forming, fresh_breakout, climbing) from historical_data
        # We order by rs_rank DESC natively in SQL so we can just grab the top 20 later.
        # But wait, rs_rank might be null, so we use IFNULL(rs_rank, 0)
        query_active = f"""
            SELECT Symbol, stage_bucket, IFNULL(rs_rank, 0) as sort_rank,
                   base_length_days, contraction_count, pct_from_pivot, base_high, Close, Open,
                   base_start_date, breakout_today, last_breakout_level
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
              AND {db_col} = 1
              AND stage_bucket IN ('forming', 'fresh_breakout', 'climbing', 'played_out')
            ORDER BY IFNULL(rs_rank, 0) DESC
        """
        cursor.execute(query_active, (max_date, max_date))
        active_rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Format the response
        stages = {
            "forming": {"count": 0, "symbols": []},
            "fresh_breakout": {"count": 0, "symbols": []},
            "climbing": {"count": 0, "symbols": []},
            "played_out": {"count": 0, "symbols": []}
        }
        
        # Populate active
        for r in active_rows:
            bucket = r["stage_bucket"]
            sym = r["Symbol"]
            stages[bucket]["count"] += 1
            if len(stages[bucket]["symbols"]) < 20:
                stages[bucket]["symbols"].append({
                    "symbol": sym,
                    "rs_rank": r["sort_rank"],
                    "base_length_days": r.get("base_length_days"),
                    "contraction_count": r.get("contraction_count"),
                    "pct_from_pivot": r.get("pct_from_pivot"),
                    "base_high": float(r["base_high"]) if r.get("base_high") else None,
                    "close": float(r["Close"]) if r.get("Close") else None,
                    "open": float(r["Open"]) if r.get("Open") else None,
                    "base_start_date": str(r["base_start_date"]) if r.get("base_start_date") else None,
                    "breakout_today": r.get("breakout_today"),
                    "last_breakout_level": float(r["last_breakout_level"]) if r.get("last_breakout_level") else None
                })
                

        
        return JSONResponse(content={
            "screen": screen_name,
            "as_of_date": str(max_date),
            "stages": stages
        })
        
    except Exception as exc:
        logger.exception(f"Error fetching stages for {screen_name}")
        raise HTTPException(status_code=500, detail=str(exc))

