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
    if screen_name in ("high-relative-volume", "high-delivery-volume"):
        try:
            import pandas as pd
            import numpy as np
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)
            
            # Get the latest date
            cursor.execute("SELECT DATE(MAX(date)) as max_date FROM ohlc_data")
            row = cursor.fetchone()
            if not row or not row["max_date"]:
                return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})
            max_date = row["max_date"]
            
            # Fetch last 40 trading days of OHLCV & delivery data per symbol from ohlc_data
            query = """
                SELECT ticker as Symbol, date as Timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume, delivery_quantity as delivery_quantity
                FROM (
                    SELECT ticker, date, open, high, low, close, volume, delivery_quantity,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) as rn
                    FROM ohlc_data
                ) t
                WHERE rn <= 40
                ORDER BY ticker, date ASC
            """
            df = pd.read_sql(query, conn, parse_dates=["Timestamp"])
            
            # Fetch latest RS ranks
            cursor.execute("""
                SELECT Symbol, rs_rank 
                FROM historical_data 
                WHERE Timestamp = (SELECT MAX(Timestamp) FROM historical_data)
            """)
            rs_ranks = {r['Symbol']: r['rs_rank'] for r in cursor.fetchall()}
            
            cursor.close()
            conn.close()
            
            if df.empty:
                return JSONResponse(content={"screen": screen_name, "as_of_date": str(max_date), "stages": {}})
            
            # Calculations based on screen type
            if screen_name == "high-relative-volume":
                df['avg_volume_30'] = df.groupby('Symbol')['Volume'].transform(lambda x: x.rolling(window=30, min_periods=1).mean())
                df['metric_value'] = df['Volume'] / df['avg_volume_30'].replace(0, np.nan)
                threshold = 5.0
            else: # high-delivery-volume
                df['metric_value'] = (df['delivery_quantity'] / df['Volume'].replace(0, np.nan)).clip(upper=1.0)
                threshold = 0.8
                
            # Find latest row per symbol
            latest_rows = df.groupby('Symbol').last().reset_index()
            
            # Find which symbols triggered in their last 5 days
            last_5_df = df.groupby('Symbol').tail(5)
            triggered = last_5_df[last_5_df['metric_value'] >= threshold]
            triggered_grouped = triggered.sort_values('metric_value').groupby('Symbol').last().reset_index()
            triggered_dict = {r.Symbol: r for r in triggered_grouped.itertuples()}
            
            stages = {
                "forming": {"count": 0, "symbols": []},       # Empty
                "fresh_breakout": {"count": 0, "symbols": []}, # Latest Day Spike
                "climbing": {"count": 0, "symbols": []},       # Last 5 Days Spike
                "played_out": {"count": 0, "symbols": []}      # Empty
            }
            
            for r in latest_rows.itertuples():
                sym = r.Symbol
                rs_rank = rs_ranks.get(sym, None)
                cur_val = r.metric_value
                close_p = r.Close
                open_p = r.Open
                
                # Check latest date condition
                is_latest_day = (r.Timestamp.date() == max_date) if hasattr(max_date, 'date') else (r.Timestamp == max_date)
                
                t_date_str = r.Timestamp.strftime("%Y-%m-%d") if hasattr(r.Timestamp, 'strftime') else str(r.Timestamp)
                t_date_str = t_date_str.split(' ')[0].split('T')[0]
                
                item = {
                    "symbol": sym,
                    "rs_rank": rs_rank if rs_rank is not None and not pd.isna(rs_rank) else None,
                    "volume_multiple": float(cur_val) if not pd.isna(cur_val) else None,
                    "volume": int(r.Volume) if not pd.isna(r.Volume) else None,
                    "avg_volume": None,
                    "close": float(close_p) if not pd.isna(close_p) else None,
                    "open": float(open_p) if not pd.isna(open_p) else None,
                    "base_high": None,
                    "base_start_date": t_date_str,
                    "pct_from_pivot": None,
                    "base_length_days": None,
                    "contraction_count": None,
                    "breakout_today": 0,
                    "last_breakout_level": None
                }
                
                if screen_name == "high-delivery-volume":
                    deliv_qty = getattr(r, 'delivery_quantity', None)
                    item["delivery_qty"] = int(deliv_qty) if deliv_qty is not None and not pd.isna(deliv_qty) else None
                    item["delivery_pct"] = float(cur_val) if not pd.isna(cur_val) else None
                else:
                    avg_vol = getattr(r, 'avg_volume_30', None)
                    item["avg_volume"] = float(avg_vol) if avg_vol is not None and not pd.isna(avg_vol) else None
                
                if is_latest_day and cur_val >= threshold:
                    stages["fresh_breakout"]["symbols"].append(item)
                
                if sym in triggered_dict:
                    trig_row = triggered_dict[sym]
                    item_recent = item.copy()
                    item_recent["volume_multiple"] = float(trig_row.metric_value)
                    item_recent["volume"] = int(trig_row.Volume)
                    item_recent["close"] = float(trig_row.Close)
                    item_recent["open"] = float(trig_row.Open)
                    
                    if screen_name == "high-delivery-volume":
                        deliv_qty_trig = getattr(trig_row, 'delivery_quantity', None)
                        item_recent["delivery_qty"] = int(deliv_qty_trig) if deliv_qty_trig is not None and not pd.isna(deliv_qty_trig) else None
                        item_recent["delivery_pct"] = float(trig_row.metric_value)
                    else:
                        avg_vol_trig = getattr(trig_row, 'avg_volume_30', None)
                        item_recent["avg_volume"] = float(avg_vol_trig) if avg_vol_trig is not None and not pd.isna(avg_vol_trig) else None
                    
                    t_str = trig_row.Timestamp.strftime("%Y-%m-%d") if hasattr(trig_row.Timestamp, 'strftime') else str(trig_row.Timestamp)
                    item_recent["base_start_date"] = t_str.split(' ')[0].split('T')[0]
                    stages["climbing"]["symbols"].append(item_recent)
            
            # Sort and count
            for b in stages:
                stages[b]["symbols"] = sorted(stages[b]["symbols"], key=lambda x: x["volume_multiple"] or 0, reverse=True)
                stages[b]["count"] = len(stages[b]["symbols"])
                
            return JSONResponse(content={
                "screen": screen_name,
                "as_of_date": str(max_date),
                "stages": stages
            })
        except Exception as exc:
            logger.exception(f"Error dynamically computing {screen_name} screen stages")
            raise HTTPException(status_code=500, detail=str(exc))

    # Map API param to DB column name
    screen_map = {
        "vcp": "screen_vcp",
        "blue-sky": "screen_blue_sky",
        "multi-year-breakout": "screen_multi_year_breakout",
        "ipo-base": "screen_ipo_base",
        "high-relative-volume": "screen_high_relative_volume",
        "high-delivery-volume": "screen_high_delivery_volume"
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
              AND stage_bucket IN ('forming', 'fresh_breakout', 'climbing')
            ORDER BY IFNULL(rs_rank, 0) DESC
        """
        cursor.execute(query_active, (max_date, max_date))
        active_rows = cursor.fetchall()
        
        # 2. Fetch failed breakouts (played_out) from pattern_events
        current_year = max_date.year if hasattr(max_date, 'year') else int(str(max_date)[:4])
        query_failed_events = """
            SELECT symbol, pivot_price, triggered_date
            FROM pattern_events
            WHERE screen_name = %s
              AND outcome = 'failed'
              AND YEAR(outcome_date) = %s
            ORDER BY outcome_date DESC
        """
        cursor.execute(query_failed_events, (db_col, current_year))
        failed_events = cursor.fetchall()
        
        failed_rows = []
        if failed_events:
            symbols = list(set([r['symbol'] for r in failed_events]))
            format_strings = ','.join(['%s'] * len(symbols))
            query_hd = f"""
                SELECT Symbol, rs_rank, base_length_days, contraction_count, pct_from_pivot, Close, Open, breakout_today
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND Symbol IN ({format_strings})
            """
            cursor.execute(query_hd, [max_date, max_date] + symbols)
            hd_rows = {r['Symbol']: r for r in cursor.fetchall()}
            
            for pe in failed_events:
                sym = pe['symbol']
                hd = hd_rows.get(sym, {})
                failed_rows.append({
                    "Symbol": sym,
                    "sort_rank": hd.get("rs_rank", 0),
                    "base_length_days": hd.get("base_length_days"),
                    "contraction_count": hd.get("contraction_count"),
                    "pct_from_pivot": hd.get("pct_from_pivot"),
                    "base_high": pe["pivot_price"],
                    "Close": hd.get("Close"),
                    "Open": hd.get("Open"),
                    "base_start_date": pe["triggered_date"],
                    "breakout_today": hd.get("breakout_today", 0),
                    "last_breakout_level": pe["pivot_price"]
                })
        
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
                
        # Populate failed
        for r in failed_rows:
            sym = r["Symbol"]
            # Unique symbols in list
            if not any(x["symbol"] == sym for x in stages["played_out"]["symbols"]):
                stages["played_out"]["count"] += 1
                if len(stages["played_out"]["symbols"]) < 20:
                    stages["played_out"]["symbols"].append({
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

