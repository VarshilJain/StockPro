"""

routers/screens.py - Preset pattern screen endpoints and stage summary

"""

from __future__ import annotations

import csv

from datetime import datetime, date

from decimal import Decimal

import logging

from pathlib import Path

from typing import Optional



from fastapi import APIRouter, Depends, HTTPException, status

from fastapi.responses import JSONResponse



from auth import get_current_user

from database import get_connection

from scan_engine import run_scan

from routers.dashboard import _get_data_version

import cache



logger = logging.getLogger(__name__)



router = APIRouter(prefix="/api", tags=["Screens"])



_market_caps_cache: dict[str, float] | None = None

_market_caps_mtime: float | None = None





def get_market_caps() -> dict[str, float]:

    """

    Load market caps (in Crores) from nse_stock_market_caps.csv.

    Caches in memory and invalidates automatically if the file is updated.

    """

    global _market_caps_cache, _market_caps_mtime

    candidates = [

        Path("nse_stock_market_caps.csv"),

        Path(__file__).resolve().parent.parent / "nse_stock_market_caps.csv",

    ]

    csv_path = None

    for p in candidates:

        if p.exists():

            csv_path = p

            break



    if not csv_path:

        return {}



    try:

        current_mtime = csv_path.stat().st_mtime

        if _market_caps_cache is not None and _market_caps_mtime == current_mtime:

            return _market_caps_cache



        caps: dict[str, float] = {}

        with open(csv_path, mode="r", encoding="utf-8") as f:

            reader = csv.DictReader(f)

            for row in reader:

                val = row.get("market_cap_cr")

                if val:

                    try:

                        mcap_val = float(val)

                        sym = (row.get("symbol") or "").strip().upper()

                        if sym:

                            caps[sym] = mcap_val

                            if sym.endswith(".NS"):

                                caps[sym[:-3]] = mcap_val

                            else:

                                caps[f"{sym}.NS"] = mcap_val

                    except (ValueError, TypeError):

                        continue



        _market_caps_cache = caps

        _market_caps_mtime = current_mtime

        return caps

    except Exception as exc:

        logger.warning("Error reading market caps CSV: %s", exc)

        return _market_caps_cache or {}







_aged_ath_memory_cache: dict | None = None

_aged_ath_cache_mtime: float | None = None



def _get_aged_ath_stages_cached() -> dict:

    global _aged_ath_memory_cache, _aged_ath_cache_mtime

    import json

    cache_file = Path("cache_data/aged_ath/stages_cache.json")

    if not cache_file.exists():

        try:

            from aged_ath_screener import get_aged_ath_stages_data

            data = get_aged_ath_stages_data()

            cache_file.parent.mkdir(parents=True, exist_ok=True)

            with open(cache_file, "w", encoding="utf-8") as f:

                json.dump(data, f)

            _aged_ath_memory_cache = data

            _aged_ath_cache_mtime = cache_file.stat().st_mtime

            return data

        except Exception as exc:

            logger.warning("Error generating stages_cache.json: %s", exc)

            return {"screen": "aged-ath", "as_of_date": str(date.today()), "stages": {"fresh_breakout": {"count": 0, "symbols": []}, "climbing": {"count": 0, "symbols": []}}}



    try:

        cur_mtime = cache_file.stat().st_mtime

        if _aged_ath_memory_cache is not None and _aged_ath_cache_mtime == cur_mtime:

            return _aged_ath_memory_cache



        with open(cache_file, "r", encoding="utf-8") as f:

            data = json.load(f)

        _aged_ath_memory_cache = data

        _aged_ath_cache_mtime = cur_mtime

        return data

    except Exception as exc:

        logger.warning("Error reading stages_cache.json: %s", exc)

        return _aged_ath_memory_cache or {"screen": "aged-ath", "as_of_date": str(date.today()), "stages": {"fresh_breakout": {"count": 0, "symbols": []}, "climbing": {"count": 0, "symbols": []}}}



def get_symbol_market_cap(symbol: str) -> Optional[float]:

    """Get the market cap in Crores for a symbol, or None if unknown."""

    if not symbol:

        return None

    caps = get_market_caps()

    s = symbol.strip().upper()

    if s in caps:

        return caps[s]

    if s.endswith(".NS"):

        return caps.get(s[:-3])

    return caps.get(f"{s}.NS")





_latest_market_date_cache = {"date": None, "fetched_at": 0.0}

def _get_latest_market_date(cursor) -> Optional[date]:
    """
    Returns the latest legitimate market trading date where the broad market traded
    (minimum 500 stocks with positive volume). Falls back to DATE(MAX(Timestamp)).
    Cached in-memory for 60 seconds for fast repeated screen evaluation.
    """
    import time
    now = time.monotonic()
    if _latest_market_date_cache["date"] is not None and (now - _latest_market_date_cache["fetched_at"]) < 60.0:
        return _latest_market_date_cache["date"]

    cursor.execute("""
        SELECT Timestamp
        FROM historical_data
        WHERE Timestamp >= DATE_SUB((SELECT MAX(Timestamp) FROM historical_data), INTERVAL 30 DAY)
        GROUP BY Timestamp
        HAVING COUNT(*) >= 500 AND SUM(Volume) > 0 AND SUM(CASE WHEN stage_bucket != 'unknown' THEN 1 ELSE 0 END) >= 500
        ORDER BY Timestamp DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    res_date = None
    if row:
        val = row["Timestamp"] if isinstance(row, dict) else row[0]
        res_date = val.date() if hasattr(val, "date") else val
    else:
        cursor.execute("SELECT DATE(MAX(Timestamp)) as max_date FROM historical_data")
        row = cursor.fetchone()
        if row:
            val = row["max_date"] if isinstance(row, dict) else row[0]
            res_date = val

    if res_date is not None:
        _latest_market_date_cache["date"] = res_date
        _latest_market_date_cache["fetched_at"] = now
    return res_date





def _run_single_flag_scan(flag: str, min_market_cap: Optional[float] = None) -> list[dict]:

    try:
        data_version = _get_data_version()
        cache_key = f"single_flag:{flag}:{min_market_cap}"

        cached = cache.get(cache_key, data_version)

        if cached is not None:

            return cached



        raw_rows = run_scan(

            conditions=[{"field": flag, "operator": "=="}],

            logic="AND"

        )

        rows = []

        for r in raw_rows:

            sym = r.get("Symbol") or r.get("symbol")

            mcap = get_symbol_market_cap(sym) if sym else None

            r["market_cap_cr"] = mcap

            if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                continue

            rows.append(r)



        cache.set(cache_key, rows, data_version, ttl=300)

        return rows

    except Exception as exc:

        logger.exception("Error running scan for %s", flag)

        raise HTTPException(status_code=500, detail="Failed to execute pattern screen.")





@router.get("/screens/vcp")

def get_vcp_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    from market_strength_screens_service import get_market_strength_stages_data

    cached_data = get_market_strength_stages_data("vcp", min_market_cap=min_market_cap)

    symbols = cached_data.get("stages", {}).get("fresh_breakout", {}).get("symbols", [])

    return JSONResponse(content={"count": len(symbols), "results": symbols})





@router.get("/screens/cup-and-handle")

def get_cup_and_handle_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    from market_strength_screens_service import get_market_strength_stages_data

    cached_data = get_market_strength_stages_data("cup-and-handle", min_market_cap=min_market_cap)

    symbols = cached_data.get("stages", {}).get("fresh_breakout", {}).get("symbols", [])

    return JSONResponse(content={"count": len(symbols), "results": symbols})



@router.get("/screens/blue-sky")

def get_blue_sky_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    rows = _run_single_flag_scan("screen_blue_sky", min_market_cap=min_market_cap)

    return JSONResponse(content={"count": len(rows), "results": rows})





@router.get("/screens/aged-ath")

def get_aged_ath_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    """

    Aged All-Time High (ATH) Screener endpoint.

    Serves instant in-memory cached results.

    """

    cached_data = _get_aged_ath_stages_cached()

    symbols = cached_data.get("stages", {}).get("fresh_breakout", {}).get("symbols", [])

    if min_market_cap is not None:

        symbols = [s for s in symbols if s.get("market_cap_cr") is not None and s.get("market_cap_cr") > min_market_cap]

    return JSONResponse(content={"count": len(symbols), "results": symbols})



@router.get("/screens/multi-year-breakout")

def get_multi_year_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    rows = _run_single_flag_scan("screen_multi_year_breakout", min_market_cap=min_market_cap)

    return JSONResponse(content={"count": len(rows), "results": rows})



@router.get("/screens/ipo-base")

def get_ipo_base_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    rows = _run_single_flag_scan("screen_ipo_base", min_market_cap=min_market_cap)

    return JSONResponse(content={"count": len(rows), "results": rows})



@router.get("/screens/golden-crossover")

def get_golden_crossover_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    rows = _run_single_flag_scan("signal5", min_market_cap=min_market_cap)

    return JSONResponse(content={"count": len(rows), "results": rows})



@router.get("/screens/stage-2")

@router.get("/screens/stage-2-stocks")

def get_stage_2_screen(min_market_cap: Optional[float] = None, current_user: dict = Depends(get_current_user)):

    conn = get_connection()

    cursor = conn.cursor(dictionary=True)

    try:

        max_date = _get_latest_market_date(cursor)

        if not max_date:

            return JSONResponse(content={"count": 0, "results": []})

        cursor.execute("""

            SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank, stage

            FROM historical_data

            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY

              AND stage = 2

              AND stage_bucket = 'fresh_breakout'

            ORDER BY IFNULL(rs_rank, 0) DESC

        """, (max_date, max_date))

        rows = cursor.fetchall()

        results = []

        for r in rows:

            sym = r["Symbol"]

            mcap = get_symbol_market_cap(sym)

            if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                continue

            r["market_cap_cr"] = mcap

            for k, v in r.items():

                if isinstance(v, Decimal):

                    r[k] = float(v)

                elif isinstance(v, (datetime, date)):

                    r[k] = str(v)

            results.append(r)

        return JSONResponse(content={"count": len(results), "results": results})

    finally:

        cursor.close()

        conn.close()



@router.get("/stage-summary")

def get_stage_summary(current_user: dict = Depends(get_current_user)):

    """

    Returns counts + a few sample rows per bucket:

      { "forming": {"count": N, "samples": [...]},

        "fresh_breakout": {...}, "climbing": {...}, "played_out": {...} }

    """

    try:
        data_version = _get_data_version()
        cache_key = "stage_summary"

        cached = cache.get(cache_key, data_version)

        if cached is not None:

            return JSONResponse(content=cached)



        conn = get_connection()

        cursor = conn.cursor(dictionary=True)

        

        # Get the latest active market date

        max_date = _get_latest_market_date(cursor)

        if not max_date:

            cursor.close()

            conn.close()

            return JSONResponse(content={})

        

        # Fetch rows for the latest date using index range (Timestamp >= max_date AND Timestamp < max_date + 1 DAY)

        cursor.execute(

            """

            SELECT Symbol, Timestamp, stage_bucket, Close, base_active, stage, breakout_today

            FROM historical_data

            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY AND stage_bucket != 'unknown'

            ORDER BY Symbol ASC

            """,

            (max_date, max_date)

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

                    if r.get("Close") is not None:

                        r["Close"] = float(r["Close"])

                    if r.get("stage") is not None:

                        r["stage"] = float(r["stage"])

                    summary[bucket]["samples"].append(r)

                    

        cache.set(cache_key, summary, data_version, ttl=300)

        return JSONResponse(content=summary)

    except Exception as exc:

        logger.exception("Error in stage-summary")

        raise HTTPException(status_code=500, detail="Failed to retrieve stage summary.")





def _filter_stage_result_by_mcap(data: dict, min_market_cap: Optional[float]) -> dict:
    if min_market_cap is None:
        return data
    filtered_stages = {}
    for b_key, b_val in data.get("stages", {}).items():
        if isinstance(b_val, dict) and "symbols" in b_val:
            filtered_syms = [
                s for s in b_val.get("symbols", [])
                if s.get("market_cap_cr") is not None and s.get("market_cap_cr") > min_market_cap
            ]
            res_dict = {
                "count": len(filtered_syms),
                "symbols": filtered_syms
            }
            if "label" in b_val:
                res_dict["label"] = b_val["label"]
            if "description" in b_val:
                res_dict["description"] = b_val["description"]
            filtered_stages[b_key] = res_dict
        else:
            filtered_stages[b_key] = b_val
    return {
        "screen": data.get("screen"),
        "as_of_date": data.get("as_of_date"),
        "stages": filtered_stages
    }


@router.get("/screens/{screen_name}/stages")
def get_screen_stages(
    screen_name: str,
    type: str = "convergence_3",
    stage: Optional[str] = None,
    min_market_cap: Optional[float] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Returns the stage breakdown specifically for one screen, including
    historical failed breakouts from pattern_events for 'played_out'.
    Optionally filters by min_market_cap (in Crores).
    """
    data_version = _get_data_version()

    cache_key = f"screen_stages:{screen_name}:{type}:{stage}:{min_market_cap}"
    cached = cache.get(cache_key, data_version)
    if cached is not None:
        return JSONResponse(content=cached)

    base_cache_key = f"screen_stages:{screen_name}:{type}:{stage}:None"
    base_cached = cache.get(base_cache_key, data_version)
    if base_cached is not None and min_market_cap is not None:
        filtered = _filter_stage_result_by_mcap(base_cached, min_market_cap)
        cache.set(cache_key, filtered, data_version, ttl=300)
        return JSONResponse(content=filtered)



    if screen_name == "rsi-divergence":

        try:

            conn = get_connection()

            cursor = conn.cursor(dictionary=True)

            

            # Get latest 20 trading dates

            cursor.execute("SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT 20")

            dates = [r["Timestamp"] for r in cursor.fetchall()]

            if not dates:

                return JSONResponse(content={"screen": "rsi-divergence", "as_of_date": None, "stages": {}})

            min_date = min(dates)

            max_date = max(dates)

            

            # Fetch divergence rows in this window

            # Order by score DESC so strongest signals are shown first

            query = """

                SELECT Symbol, Timestamp, rsi_divergence_type, rsi_divergence_direction, rsi_divergence_score, Close, Open, rs_rank

                FROM historical_data

                WHERE Timestamp >= %s

                  AND rsi_divergence_type IS NOT NULL

                ORDER BY rsi_divergence_score DESC

            """

            cursor.execute(query, (min_date,))

            rows = cursor.fetchall()

            cursor.close()

            conn.close()

            

            stages = {

                "fresh_breakout": {"count": 0, "symbols": []},  # Double divergence

                "climbing": {"count": 0, "symbols": []}          # Triple divergence

            }

            

            # Format and distribute rows

            for r in rows:

                sym = r["Symbol"]

                mcap = get_symbol_market_cap(sym)

                if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                    continue



                t_type = r["rsi_divergence_type"]

                t_dir = r["rsi_divergence_direction"]

                score = r["rsi_divergence_score"]

                

                t_date_str = r["Timestamp"].strftime("%Y-%m-%d") if hasattr(r["Timestamp"], 'strftime') else str(r["Timestamp"])

                t_date_str = t_date_str.split(' ')[0].split('T')[0]

                

                item = {

                    "symbol": sym,

                    "market_cap_cr": mcap,

                    "rs_rank": r["rs_rank"] if r["rs_rank"] is not None else None,

                    "close": float(r["Close"]) if r["Close"] is not None else None,

                    "open": float(r["Open"]) if r["Open"] is not None else None,

                    "base_start_date": t_date_str, # trigger date

                    "rsi_divergence_type": t_type,

                    "rsi_divergence_direction": t_dir,

                    "rsi_divergence_score": float(score) if score is not None else None,

                }

                

                if t_type == "double":

                    stages["fresh_breakout"]["symbols"].append(item)

                elif t_type == "triple":

                    stages["climbing"]["symbols"].append(item)

                    

            # Deduplicate by Symbol (keeping the latest trigger or highest score)

            for key in stages:

                seen_syms = set()

                deduped = []

                for item in stages[key]["symbols"]:

                    if item["symbol"] not in seen_syms:

                        seen_syms.add(item["symbol"])

                        deduped.append(item)

                stages[key]["symbols"] = deduped

                stages[key]["count"] = len(deduped)

                

            result = {

                "screen": "rsi-divergence",

                "as_of_date": max_date.strftime("%Y-%m-%d") if hasattr(max_date, 'strftime') else str(max_date),

                "stages": stages

            }

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error dynamically computing rsi-divergence screen stages")

            raise HTTPException(status_code=500, detail="Failed to compute RSI divergence screen stages.")



    elif screen_name in ("high-relative-volume", "high-delivery-volume"):

        try:

            import datetime

            import pandas as pd

            import numpy as np

            conn = get_connection()

            cursor = conn.cursor(dictionary=True)

            

            # Get the latest date

            cursor.execute("SELECT DATE(MAX(date)) as max_date FROM ohlc_data")

            row = cursor.fetchone()

            if not row or not row["max_date"]:

                cursor.close()

                conn.close()

                return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})

            max_date = row["max_date"]

            target_date_obj = max_date if isinstance(max_date, datetime.date) else pd.to_datetime(max_date).date()

            

            # Optimized targeted data fetching:

            if screen_name == "high-delivery-volume":

                # High Delivery only requires the last 14 calendar days where delivery data exists

                query = """

                    SELECT ticker as Symbol, date as Timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume, delivery_quantity

                    FROM ohlc_data

                    WHERE date >= %s - INTERVAL 14 DAY AND delivery_quantity IS NOT NULL AND volume > 0

                    ORDER BY ticker, date ASC

                """

                cursor.execute(query, (max_date,))

                rows = cursor.fetchall()

                df = pd.DataFrame(rows)

                if not df.empty and "Timestamp" in df.columns:

                    df["Timestamp"] = pd.to_datetime(df["Timestamp"])

                df['metric_value'] = (df['delivery_quantity'] / df['Volume'].replace(0, np.nan)).clip(upper=1.0)

                threshold = 0.8

            else: # high-relative-volume

                # Only fetch rolling history for candidate symbols that triggered high volume in recent trading days

                cursor.execute("""

                    SELECT DISTINCT Symbol 

                    FROM historical_data 

                    WHERE Timestamp >= %s - INTERVAL 10 DAY 

                      AND (screen_high_relative_volume = 1 OR High_Relative_Volume_30 = 1)

                """, (max_date,))

                candidate_symbols = [r["Symbol"] for r in cursor.fetchall()]

                

                if candidate_symbols:

                    format_strings = ','.join(['%s'] * len(candidate_symbols))

                    query = f"""

                        SELECT ticker as Symbol, date as Timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume

                        FROM ohlc_data

                        WHERE date >= %s - INTERVAL 50 DAY AND ticker IN ({format_strings})

                        ORDER BY ticker, date ASC

                    """

                    cursor.execute(query, [max_date] + candidate_symbols)

                else:

                    query = """

                        SELECT ticker as Symbol, date as Timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume

                        FROM ohlc_data

                        WHERE date >= %s - INTERVAL 50 DAY

                        ORDER BY ticker, date ASC

                    """

                    cursor.execute(query, (max_date,))

                rows = cursor.fetchall()

                df = pd.DataFrame(rows)

                if not df.empty and "Timestamp" in df.columns:

                    df["Timestamp"] = pd.to_datetime(df["Timestamp"])

                df['avg_volume_30'] = df.groupby('Symbol')['Volume'].transform(lambda x: x.rolling(window=30, min_periods=1).mean())

                df['metric_value'] = df['Volume'] / df['avg_volume_30'].replace(0, np.nan)

                threshold = 5.0

            

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

            

            # Find latest row per symbol

            latest_rows = df.groupby('Symbol').last().reset_index()

            

            # Find which symbols triggered in their last 5 days

            last_5_df = df.groupby('Symbol').tail(5)

            if screen_name == "high-relative-volume":

                triggered = last_5_df[(last_5_df['metric_value'] >= threshold) & (last_5_df['avg_volume_30'] > 50000)]

            elif screen_name == "high-delivery-volume":

                triggered = last_5_df[

                    (last_5_df['metric_value'] > 0.75) &

                    (last_5_df['metric_value'] < 0.99) &

                    (last_5_df['delivery_quantity'] > 100000)

                ]

            else:

                triggered = last_5_df[last_5_df['metric_value'] >= threshold]

            triggered_grouped = triggered.sort_values('metric_value').groupby('Symbol').last().reset_index()

            triggered_dict = {r.Symbol: r for r in triggered_grouped.itertuples()}

            

            stages = {
                "fresh_breakout": {"count": 0, "symbols": []}, # Latest Day Spike
                "climbing": {"count": 0, "symbols": []}        # Last 5 Days Spike
            }

            for r in latest_rows.itertuples():
                sym = r.Symbol
                in_triggered = sym in triggered_dict

                # Check latest date condition
                r_date = r.Timestamp.date() if hasattr(r.Timestamp, 'date') else pd.to_datetime(r.Timestamp).date()
                is_latest_day = (r_date == target_date_obj)
                cur_val = r.metric_value

                if pd.isna(cur_val):
                    qualifies_latest = False
                elif screen_name == "high-delivery-volume":
                    deliv_qty = getattr(r, 'delivery_quantity', None)
                    qualifies_latest = is_latest_day and (0.75 < cur_val < 0.99) and (deliv_qty is not None and not pd.isna(deliv_qty) and deliv_qty > 100000)
                elif screen_name == "high-relative-volume":
                    avg_vol = getattr(r, 'avg_volume_30', None)
                    qualifies_latest = is_latest_day and (cur_val >= threshold) and (avg_vol is not None and not pd.isna(avg_vol) and avg_vol > 50000)
                else:
                    qualifies_latest = is_latest_day and (cur_val >= threshold)

                if not qualifies_latest and not in_triggered:
                    continue

                mcap = get_symbol_market_cap(sym)
                if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):
                    continue

                rs_rank = rs_ranks.get(sym, None)
                close_p = r.Close
                open_p = r.Open

                t_date_str = r.Timestamp.strftime("%Y-%m-%d") if hasattr(r.Timestamp, 'strftime') else str(r.Timestamp)
                t_date_str = t_date_str.split(' ')[0].split('T')[0]

                item = {
                    "symbol": sym,
                    "market_cap_cr": mcap,
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

                if qualifies_latest:
                    stages["fresh_breakout"]["symbols"].append(item)

                if in_triggered:
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
                        t_str = trig_row.Timestamp.strftime("%Y-%m-%d") if hasattr(trig_row.Timestamp, 'strftime') else str(trig_row.Timestamp)
                        item_recent["base_start_date"] = t_str.split(' ')[0].split('T')[0]
                        if (
                            item_recent["delivery_pct"] is not None and 0.75 < item_recent["delivery_pct"] < 0.99
                            and item_recent["delivery_qty"] is not None and item_recent["delivery_qty"] > 100000
                        ):
                            stages["climbing"]["symbols"].append(item_recent)
                    else:
                        avg_vol_trig = getattr(trig_row, 'avg_volume_30', None)
                        item_recent["avg_volume"] = float(avg_vol_trig) if avg_vol_trig is not None and not pd.isna(avg_vol_trig) else None
                        t_str = trig_row.Timestamp.strftime("%Y-%m-%d") if hasattr(trig_row.Timestamp, 'strftime') else str(trig_row.Timestamp)
                        item_recent["base_start_date"] = t_str.split(' ')[0].split('T')[0]
                        if screen_name != "high-relative-volume" or (item_recent["avg_volume"] is not None and item_recent["avg_volume"] > 50000):
                            stages["climbing"]["symbols"].append(item_recent)

            

            # Sort and count

            for b in stages:

                stages[b]["symbols"] = sorted(stages[b]["symbols"], key=lambda x: x["volume_multiple"] or 0, reverse=True)

                stages[b]["count"] = len(stages[b]["symbols"])

                

            result = {

                "screen": screen_name,

                "as_of_date": str(max_date),

                "stages": stages

            }

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error dynamically computing %s screen stages", screen_name)

            raise HTTPException(status_code=500, detail="Failed to compute volume screen stages.")



    # ── EMA CONVERGENCE SCREEN (days-based buckets, no stage dependency) ──────

    if screen_name == "ema-convergence":

        if type not in ("convergence_3", "convergence_4", "convergence_5"):

            raise HTTPException(status_code=400, detail="Invalid convergence type")

        db_col = type

        try:

            conn = get_connection()

            cursor = conn.cursor(dictionary=True)



            # Get the latest 20 trading dates to define our lookback window

            cursor.execute(

                "SELECT DISTINCT Timestamp as d FROM historical_data ORDER BY d DESC LIMIT 20"

            )

            trading_dates = [r["d"] for r in cursor.fetchall()]

            if not trading_dates:

                return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})



            max_date = trading_dates[0]  # most recent trading day

            # 1. Fetch only candidate symbols where the signal is ON on the latest date (uses idx_ema_conv index)
            cursor.execute(f"""
                SELECT Symbol
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND {db_col} = 1
            """, (max_date, max_date))
            active_syms = [r["Symbol"] for r in cursor.fetchall()]

            if not active_syms:
                cursor.close()
                conn.close()
                max_d_str = max_date.strftime("%Y-%m-%d") if hasattr(max_date, "strftime") else str(max_date).split()[0]
                result = {
                    "screen": screen_name,
                    "as_of_date": max_d_str,
                    "stages": {
                        "fresh": {"count": 0, "symbols": []},
                        "recent": {"count": 0, "symbols": []},
                        "sustained": {"count": 0, "symbols": []}
                    }
                }
                cache.set(cache_key, result, data_version, ttl=300)
                return JSONResponse(content=result)

            # 2. Fetch recent lookback state only for candidate active symbols
            placeholders = ",".join(["%s"] * len(active_syms))
            query = f"""
                SELECT Symbol, Timestamp as d, {db_col} as signal_on,
                       IFNULL(rs_rank, 0) as rs_rank, Close, Open
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                  AND Symbol IN ({placeholders})
                ORDER BY Symbol, Timestamp DESC
            """
            lookback_start = trading_dates[-1] if len(trading_dates) >= 20 else trading_dates[-1]
            cursor.execute(query, [lookback_start, max_date] + active_syms)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()



            # Build a dict: symbol -> sorted list of (date, signal_on, rs_rank, close, open)

            from collections import defaultdict

            sym_rows = defaultdict(list)

            for r in rows:

                sym_rows[r["Symbol"]].append(r)



            stages = {

                "fresh": {"count": 0, "symbols": []},      # signal turned ON today (new entry)

                "recent": {"count": 0, "symbols": []},     # signal ON for 1–4 days

                "sustained": {"count": 0, "symbols": []}   # signal ON for 5+ days

            }



            for sym, sym_data in sym_rows.items():

                mcap = get_symbol_market_cap(sym)

                if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                    continue



                # sym_data is already sorted desc by date (most recent first)

                # Only process symbols where signal is ON on the latest date

                latest = sym_data[0]

                if str(latest["d"]) != str(max_date):

                    continue  # no data for latest date

                if not latest["signal_on"]:

                    continue  # signal is off today



                # Count consecutive days the signal has been ON (starting from today)

                consecutive_days = 0

                for row in sym_data:

                    if row["signal_on"]:

                        consecutive_days += 1

                    else:

                        break  # streak broken



                entry = {

                    "symbol": sym,

                    "market_cap_cr": mcap,

                    "rs_rank": float(latest["rs_rank"]) if latest.get("rs_rank") is not None else 0,

                    "close": float(latest["Close"]) if latest.get("Close") else None,

                    "open": float(latest["Open"]) if latest.get("Open") else None,

                    "days_active": consecutive_days,

                    "base_high": None,

                    "pct_from_pivot": None,

                    "base_length_days": None,

                    "contraction_count": None,

                    "base_start_date": None,

                    "breakout_today": None,

                    "breakout_date": None,

                    "last_breakout_level": None

                }



                if consecutive_days == 1:

                    bucket = "fresh"

                elif consecutive_days <= 4:

                    bucket = "recent"

                else:

                    bucket = "sustained"



                stages[bucket]["count"] += 1

                stages[bucket]["symbols"].append(entry)



            # Sort each bucket by rs_rank DESC

            for b in stages:

                stages[b]["symbols"].sort(key=lambda x: x["rs_rank"], reverse=True)



            result = {

                "screen": screen_name,

                "as_of_date": str(max_date),

                "stages": stages

            }

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error fetching ema-convergence stages")

            raise HTTPException(status_code=500, detail="Failed to compute EMA convergence stages.")



    # ── GOLDEN CROSSOVER SCREEN (Latest Date & Last 5 Days) ───────────────────

    if screen_name == "golden-crossover":

        try:

            conn = get_connection()

            cursor = conn.cursor(dictionary=True)



            # Get the latest 5 trading dates
            cursor.execute(
                "SELECT DISTINCT Timestamp as d FROM historical_data ORDER BY Timestamp DESC LIMIT 5"
            )

            trading_dates = [r["d"] for r in cursor.fetchall()]

            if not trading_dates:

                cursor.close()

                conn.close()

                return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})



            max_date = trading_dates[0]

            five_days_start = trading_dates[-1]



            # Fetch rows where signal5 = 1 within the last 5 trading days

            # Note: signal5 = 1 represents 50 SMA / EMA crossing above 200 SMA / EMA

            query = """

                SELECT Symbol, Timestamp, Close, Open, IFNULL(rs_rank, 0) as rs_rank

                FROM historical_data

                WHERE Timestamp >= %s AND Timestamp <= %s + INTERVAL 1 DAY

                  AND signal5 = 1

                ORDER BY Timestamp DESC, IFNULL(rs_rank, 0) DESC

            """

            cursor.execute(query, (five_days_start, max_date))

            rows = cursor.fetchall()

            cursor.close()

            conn.close()



            # Mapping trading dates to days ago (0 = today, 1 = 1 trading day ago, etc.)

            date_to_days_ago = {

                (d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d).split()[0]): idx

                for idx, d in enumerate(trading_dates)

            }

            max_date_str = max_date.strftime("%Y-%m-%d") if hasattr(max_date, "strftime") else str(max_date).split()[0]



            stages = {

                "fresh_breakout": {"count": 0, "symbols": []},  # Latest Date

                "climbing": {"count": 0, "symbols": []}          # Last 5 Days

            }



            seen_5day_symbols = set()

            for r in rows:

                sym = r["Symbol"]

                mcap = get_symbol_market_cap(sym)

                if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                    continue



                t = r["Timestamp"]

                t_str = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t).split()[0]

                days_since = date_to_days_ago.get(t_str, 0)



                item = {

                    "symbol": sym,

                    "market_cap_cr": mcap,

                    "rs_rank": float(r["rs_rank"]) if r.get("rs_rank") is not None else 0,

                    "close": float(r["Close"]) if r.get("Close") else None,

                    "open": float(r["Open"]) if r.get("Open") else None,

                    "base_start_date": t_str,

                    "days_since_cross": days_since,

                    "crossover_type": "Golden Cross (50 > 200)",

                    "base_high": None,

                    "pct_from_pivot": None,

                    "base_length_days": None,

                    "contraction_count": None,

                    "breakout_today": 1 if days_since == 0 else 0,

                    "breakout_date": t_str,

                    "last_breakout_level": None,

                }



                # If on latest trading day, add to fresh_breakout (Latest Date)

                if t_str == max_date_str:

                    stages["fresh_breakout"]["symbols"].append(item)



                # Add to climbing (Last 5 Days), deduplicated by symbol

                if sym not in seen_5day_symbols:

                    seen_5day_symbols.add(sym)

                    stages["climbing"]["symbols"].append(item)



            stages["fresh_breakout"]["count"] = len(stages["fresh_breakout"]["symbols"])

            stages["climbing"]["count"] = len(stages["climbing"]["symbols"])



            result = {

                "screen": screen_name,

                "as_of_date": max_date_str,

                "stages": stages

            }

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error dynamically computing golden-crossover screen stages")

            raise HTTPException(status_code=500, detail="Failed to compute Golden Crossover screen stages.")





    # ── AGED ALL-TIME HIGH (ATH) SCREEN (Latest Date & Last 30 Days) ───────────

    if screen_name in ("aged-ath", "aged_ath"):

        try:

            cached_data = _get_aged_ath_stages_cached()

            if min_market_cap is not None:

                filtered_stages = {}

                for b_key, b_val in cached_data.get("stages", {}).items():

                    filtered_syms = [

                        s for s in b_val.get("symbols", [])

                        if s.get("market_cap_cr") is not None and s.get("market_cap_cr") > min_market_cap

                    ]

                    filtered_stages[b_key] = {

                        "count": len(filtered_syms),

                        "symbols": filtered_syms

                    }

                stages_result = {

                    "screen": "aged-ath",

                    "as_of_date": cached_data.get("as_of_date"),

                    "stages": filtered_stages

                }

            else:

                stages_result = cached_data



            cache.set(cache_key, stages_result, data_version, ttl=300)

            return JSONResponse(content=stages_result)

        except Exception as exc:

            logger.exception("Error serving aged-ath screen stages")

            raise HTTPException(status_code=500, detail="Failed to retrieve Aged ATH screen stages.")



    # ── STAGE 2 STOCKS SCREEN (Latest Date & Last 5 Days) ─────────────────────

    if screen_name in ("stage-2", "stage-2-stocks"):

        try:

            conn = get_connection()

            cursor = conn.cursor(dictionary=True)



            # Get the latest 5 trading dates
            cursor.execute(
                "SELECT DISTINCT Timestamp as d FROM historical_data ORDER BY Timestamp DESC LIMIT 5"
            )

            trading_dates = [r["d"] for r in cursor.fetchall()]

            if not trading_dates:

                cursor.close()

                conn.close()

                return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})



            max_date = trading_dates[0]

            five_days_start = trading_dates[-1]



            # Fetch rows where stage = 2 and stage_bucket = 'fresh_breakout' within the last 5 trading days

            query = """

                SELECT Symbol, Timestamp, Close, Open, SMA50, SMA200, IFNULL(rs_rank, 0) as rs_rank,

                       stage_bucket, breakout_today

                FROM historical_data

                WHERE Timestamp >= %s AND Timestamp <= %s + INTERVAL 1 DAY

                  AND stage = 2

                  AND stage_bucket = 'fresh_breakout'

                ORDER BY Timestamp DESC, IFNULL(rs_rank, 0) DESC

            """

            cursor.execute(query, (five_days_start, max_date))

            rows = cursor.fetchall()



            breakout_dates = {}

            if rows:

                syms = list({r["Symbol"] for r in rows})

                placeholders = ",".join(["%s"] * len(syms))

                cursor.execute(f"""

                    SELECT Symbol, DATE(MAX(Timestamp)) as latest_breakout_date

                    FROM historical_data

                    WHERE Symbol IN ({placeholders}) AND breakout_today = 1 AND Timestamp <= %s

                    GROUP BY Symbol

                """, tuple(syms) + (max_date,))

                for br in cursor.fetchall():

                    d_val = br["latest_breakout_date"]

                    breakout_dates[br["Symbol"]] = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)



            cursor.close()

            conn.close()



            # Mapping trading dates to days ago (0 = today, 1 = 1 trading day ago, etc.)

            date_to_days_ago = {

                (d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d).split()[0]): idx

                for idx, d in enumerate(trading_dates)

            }

            max_date_str = max_date.strftime("%Y-%m-%d") if hasattr(max_date, "strftime") else str(max_date).split()[0]



            stages = {

                "fresh_breakout": {"count": 0, "symbols": []},  # Latest Date

                "climbing": {"count": 0, "symbols": []}          # Last 5 Days

            }



            seen_5day_symbols = set()

            climbing_all_symbols = []

            for r in rows:

                sym = r["Symbol"]

                mcap = get_symbol_market_cap(sym)

                if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):

                    continue



                t = r["Timestamp"]

                t_str = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t).split()[0]

                days_since = date_to_days_ago.get(t_str, 0)



                close = float(r["Close"]) if r.get("Close") else None

                open_p = float(r["Open"]) if r.get("Open") else None

                sma50 = float(r["SMA50"]) if r.get("SMA50") else None

                sma200 = float(r["SMA200"]) if r.get("SMA200") else None



                pct_above_200 = round(((close - sma200) / sma200 * 100), 2) if (close and sma200 and sma200 > 0) else None

                pct_above_50 = round(((close - sma50) / sma50 * 100), 2) if (close and sma50 and sma50 > 0) else None



                item = {

                    "symbol": sym,

                    "market_cap_cr": mcap,

                    "rs_rank": float(r["rs_rank"]) if r.get("rs_rank") is not None else 0,

                    "close": close,

                    "open": open_p,

                    "sma50": sma50,

                    "sma200": sma200,

                    "pct_above_sma200": pct_above_200,

                    "pct_above_sma50": pct_above_50,

                    "base_start_date": t_str,

                    "days_since": days_since,

                    "stage": 2,

                    "stage_status": "Stage 2 (Fresh Breakout)",

                    "base_high": None,

                    "pct_from_pivot": None,

                    "base_length_days": None,

                    "contraction_count": None,

                    "breakout_today": r.get("breakout_today", 0),

                    "breakout_date": breakout_dates.get(sym, t_str),

                    "last_breakout_level": None,

                }



                # If on latest trading day, count and collect symbols

                if t_str == max_date_str:

                    stages["fresh_breakout"]["count"] += 1

                    stages["fresh_breakout"]["symbols"].append(item)



                # Add to climbing (Last 5 Days), deduplicated by symbol

                if sym not in seen_5day_symbols:

                    seen_5day_symbols.add(sym)

                    stages["climbing"]["count"] += 1

                    climbing_all_symbols.append(item)



            # Sort climbing symbols by rs_rank DESC

            climbing_all_symbols.sort(key=lambda x: x["rs_rank"], reverse=True)

            stages["climbing"]["symbols"] = climbing_all_symbols



            result = {

                "screen": screen_name,

                "as_of_date": max_date_str,

                "stages": stages

            }

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error dynamically computing stage-2 screen stages")

            raise HTTPException(status_code=500, detail="Failed to compute Stage 2 screen stages.")



    # ── TREND RESUMPTION SCREEN (Criteria 1 & 2) ─────────────────────────────

    if screen_name in ("trend-resumption", "trend_resumption"):

        try:

            from trend_resumption_service import get_trend_resumption_stages

            crit_filter = type if type in ("criteria1", "criteria2") else "all"

            result = get_trend_resumption_stages(

                criteria_filter=crit_filter,

                min_market_cap=min_market_cap

            )

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error dynamically computing trend-resumption screen stages")

            raise HTTPException(status_code=500, detail="Failed to compute Trend Resumption screen stages.")



    # ── MARKET STRENGTH SCREENS: VCP & CUP AND HANDLE (Latest Date & Last 5 Days) ──

    if screen_name in ("vcp", "cup-and-handle", "cup_and_handle", "cwh"):

        try:

            from market_strength_screens_service import get_market_strength_stages_data

            result = get_market_strength_stages_data(screen_name, min_market_cap=min_market_cap)

            cache.set(cache_key, result, data_version, ttl=300)

            return JSONResponse(content=result)

        except Exception as exc:

            logger.exception("Error serving %s screen stages", screen_name)

            raise HTTPException(status_code=500, detail=f"Failed to retrieve {screen_name} screen stages.")



    # ── GENERIC STAGE-BUCKET SCREENS ─────────────────────────────────────────

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



        # Get the latest active market date

        max_date = _get_latest_market_date(cursor)

        if not max_date:

            return JSONResponse(content={"screen": screen_name, "as_of_date": None, "stages": {}})



        # 1. Fetch active buckets (forming, fresh_breakout, climbing) from historical_data

        # We order by rs_rank DESC natively in SQL so we can just grab the top 20 later.

        # But wait, rs_rank might be null, so we use IFNULL(rs_rank, 0)

        query_active = f"""
            SELECT Symbol, stage_bucket, IFNULL(rs_rank, 0) as sort_rank,
                   base_length_days, contraction_count, pct_from_pivot, base_high, base_low, Close, Open,
                   base_start_date, breakout_today, last_breakout_level,
                   base_count, pct_off_52w_high, pct_above_52w_low,
                   vdu_flag, atr_compression_ratio, last_contraction_depth_pct, contractions_shrinking,
                   base_width_weeks, pivot_price, vcp_score, vcp_grade, breakout_flag,
                   score_progression, score_final_depth, score_contraction_count, score_vol_final,
                   score_quietest_day, score_up_down_vol, score_rs_rank, score_off_52wh,
                   score_base_count, score_atr_compression, score_pivot_proximity,
                   coil, dry, SMA50, SMA200
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
              AND {db_col} = 1
              AND stage_bucket IN ('forming', 'fresh_breakout', 'climbing')
            ORDER BY IFNULL(rs_rank, 0) DESC
        """
        cursor.execute(query_active, (max_date, max_date))
        active_rows = cursor.fetchall()

        # 2. Get latest breakout dates and base lengths for active symbols to show when the breakout occurred
        breakout_dates = {}
        breakout_base_lens = {}
        breakout_base_wks = {}
        if active_rows:
            symbols_list = [r["Symbol"] for r in active_rows]
            placeholders = ",".join(["%s"] * len(symbols_list))
            query_breakouts = f"""
                SELECT h.Symbol, DATE(h.Timestamp) as latest_breakout_date, h.base_length_days, h.base_width_weeks
                FROM historical_data h
                INNER JOIN (
                    SELECT Symbol, MAX(Timestamp) as max_ts
                    FROM historical_data
                    WHERE Symbol IN ({placeholders}) AND breakout_today = 1 AND Timestamp <= %s
                    GROUP BY Symbol
                ) m ON h.Symbol = m.Symbol AND h.Timestamp = m.max_ts
            """
            cursor.execute(query_breakouts, tuple(symbols_list) + (max_date,))
            for br in cursor.fetchall():
                sym = br["Symbol"]
                d_val = br["latest_breakout_date"]
                breakout_dates[sym] = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
                breakout_base_lens[sym] = br.get("base_length_days")
                breakout_base_wks[sym] = float(br["base_width_weeks"]) if br.get("base_width_weeks") is not None else None

        cursor.close()
        conn.close()

        # Format the response
        stages = {
            "forming": {"count": 0, "symbols": []},
            "fresh_breakout": {"count": 0, "symbols": []},
            "climbing": {"count": 0, "symbols": []}
        }

        # Populate active
        for r in active_rows:
            bucket = r["stage_bucket"]
            sym = r["Symbol"]
            mcap = get_symbol_market_cap(sym)
            if min_market_cap is not None and (mcap is None or mcap <= min_market_cap):
                continue

            base_len = r.get("base_length_days")
            base_wks = float(r["base_width_weeks"]) if r.get("base_width_weeks") is not None else None
            bo_date_str = breakout_dates.get(sym)

            if screen_name == "multi-year-breakout":
                bucket = r.get("stage_bucket") or "forming"
                bo_len = breakout_base_lens.get(sym)
                bo_w = breakout_base_wks.get(sym)
                if bo_len and bo_len >= 250:
                    base_len = bo_len
                    base_wks = bo_w
                elif base_len is None or base_len < 250:
                    base_len = 250
                    base_wks = 50.0
            else:
                if bucket in ("fresh_breakout", "climbing") and (base_len in (None, 0)):
                    if sym in breakout_base_lens and breakout_base_lens[sym]:
                        base_len = breakout_base_lens[sym]
                        base_wks = breakout_base_wks.get(sym) or (round(base_len / 5.0, 1) if base_len else None)

            if bucket in stages:
                stages[bucket]["count"] += 1
                base_h = float(r["base_high"]) if r.get("base_high") else None
                last_b = float(r["last_breakout_level"]) if r.get("last_breakout_level") else None
                pivot = float(r["pivot_price"]) if (r.get("pivot_price") and float(r["pivot_price"]) > 0) else (base_h if (base_h and base_h > 0) else last_b)
                stages[bucket]["symbols"].append({
                    "symbol": sym,
                    "Symbol": sym,
                    "market_cap_cr": mcap,
                    "rs_rank": r["sort_rank"],
                    "base_length_days": base_len,
                    "base_width_weeks": base_wks,
                    "stage_bucket": bucket,
                    "myb_stage": "fresh" if bucket == "fresh_breakout" else bucket,
                    "contraction_count": r.get("contraction_count"),
                    "pct_from_pivot": r.get("pct_from_pivot"),
                    "myb_dist_pct": r.get("pct_from_pivot"),
                    "pivot_price": pivot,
                    "base_high": pivot,
                    "base_low": float(r["base_low"]) if r.get("base_low") else None,
                    "close": float(r["Close"]) if r.get("Close") else None,
                    "open": float(r["Open"]) if r.get("Open") else None,
                    "base_start_date": str(r["base_start_date"]) if r.get("base_start_date") else None,
                    "breakout_today": r.get("breakout_today"),
                    "last_breakout_level": last_b,
                    "breakout_date": breakout_dates.get(sym),
                    # Enhanced VCP fields
                    "base_count": r.get("base_count"),
                    "pct_off_52w_high": float(r["pct_off_52w_high"]) if r.get("pct_off_52w_high") is not None else None,
                    "pct_above_52w_low": float(r["pct_above_52w_low"]) if r.get("pct_above_52w_low") is not None else None,
                    "vdu_flag": r.get("vdu_flag"),
                    "atr_compression_ratio": float(r["atr_compression_ratio"]) if r.get("atr_compression_ratio") is not None else None,
                    "last_contraction_depth_pct": float(r["last_contraction_depth_pct"]) if r.get("last_contraction_depth_pct") is not None else None,
                    "contractions_shrinking": r.get("contractions_shrinking"),
                    "coil": float(r["coil"]) if r.get("coil") is not None else None,
                    "dry": float(r["dry"]) if r.get("dry") is not None else None,
                    # VCP v2.0 Two-Tier additions
                    "vcp_score": float(r["vcp_score"]) if r.get("vcp_score") is not None else None,
                    "vcp_grade": r.get("vcp_grade"),
                    "breakout_flag": r.get("breakout_flag", 0),
                    "scores": {
                        "progression": float(r["score_progression"]) if r.get("score_progression") is not None else None,
                        "final_depth": float(r["score_final_depth"]) if r.get("score_final_depth") is not None else None,
                        "contraction_count": float(r["score_contraction_count"]) if r.get("score_contraction_count") is not None else None,
                        "vol_final": float(r["score_vol_final"]) if r.get("score_vol_final") is not None else None,
                        "quietest_day": float(r["score_quietest_day"]) if r.get("score_quietest_day") is not None else None,
                        "up_down_vol": float(r["score_up_down_vol"]) if r.get("score_up_down_vol") is not None else None,
                        "rs_rank": float(r["score_rs_rank"]) if r.get("score_rs_rank") is not None else None,
                        "off_52wh": float(r["score_off_52wh"]) if r.get("score_off_52wh") is not None else None,
                        "base_count": float(r["score_base_count"]) if r.get("score_base_count") is not None else None,
                        "atr_compression": float(r["score_atr_compression"]) if r.get("score_atr_compression") is not None else None,
                        "pivot_proximity": float(r["score_pivot_proximity"]) if r.get("score_pivot_proximity") is not None else None,
                    }
                })

        # For multi-year-breakout, sort all 3 cards in descending order of breakout age / base length
        if screen_name == "multi-year-breakout":
            for b in stages:
                stages[b]["symbols"].sort(
                    key=lambda x: (x.get("base_length_days") or 0),
                    reverse=True
                )
                stages[b]["count"] = len(stages[b]["symbols"])



        result = {

            "screen": screen_name,

            "as_of_date": str(max_date),

            "stages": stages

        }

        cache.set(cache_key, result, data_version, ttl=300)

        return JSONResponse(content=result)



    except Exception as exc:

        logger.exception("Error fetching stages for %s", screen_name)

        raise HTTPException(status_code=500, detail="Failed to fetch pattern screen stages.")







@router.get("/screens/rsi-divergence-points/{symbol}")
def get_rsi_divergence_points(symbol: str, current_user: dict = Depends(get_current_user)):
    data_version = _get_data_version()
    cache_key = f"rsi_points:{symbol}"
    cached = cache.get(cache_key, data_version)
    if cached is not None:
        return JSONResponse(content=cached)



    conn = get_connection()

    cursor = conn.cursor(dictionary=True)

    alt_symbol = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol[:-3]

    cursor.execute("""
        SELECT date as Timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume
        FROM ohlc_data
        WHERE ticker = %s OR ticker = %s
        ORDER BY date DESC
        LIMIT 500
    """, (symbol, alt_symbol))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="Symbol not found")

    rows.reverse()

    import pandas as pd
    df = pd.DataFrame(rows)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    from services import compute_rsi, detect_rsi_divergence
    df["RSI14"] = compute_rsi(df["Close"])

    # Detect divergences over recent history
    divs = detect_rsi_divergence(df, rsi_col="RSI14", lookback=len(df), left=4, right=4, min_spacing=4)

    # Fast list comprehension instead of slow iterrows()
    rsi_series = df["RSI14"].tolist()
    df_list = []
    for r, rsi_val in zip(rows, rsi_series):
        t = r["Timestamp"]
        t_str = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t).split()[0]
        df_list.append({
            "time": t_str,
            "open": float(r["Open"]) if r["Open"] is not None else 0.0,
            "high": float(r["High"]) if r["High"] is not None else 0.0,
            "low": float(r["Low"]) if r["Low"] is not None else 0.0,
            "close": float(r["Close"]) if r["Close"] is not None else 0.0,
            "rsi": float(rsi_val) if (rsi_val is not None and not pd.isna(rsi_val)) else None
        })

    formatted_divs = []
    for d in divs:
        pivot_dates = []
        for idx in d["pivot_indices"]:
            p_time = df.loc[idx, "Timestamp"]
            p_time_str = p_time.strftime("%Y-%m-%d") if hasattr(p_time, "strftime") else str(p_time).split()[0]
            pivot_dates.append(p_time_str)

        trigger_time = df.loc[d["last_idx"], "Timestamp"]
        trigger_time_str = trigger_time.strftime("%Y-%m-%d") if hasattr(trigger_time, "strftime") else str(trigger_time).split()[0]

        formatted_divs.append({
            "divergence_type": d["divergence_type"],
            "divergence_direction": d["divergence_direction"],
            "score": d["score"],
            "pivot_dates": pivot_dates,
            "trigger_date": trigger_time_str
        })

        

    result = {

        "symbol": symbol,

        "ohlcv": df_list,

        "divergences": formatted_divs

    }

    cache.set(cache_key, result, data_version, ttl=600)

    return JSONResponse(content=result)



