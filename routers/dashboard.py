"""
routers/dashboard.py — Signal widget registry and user dashboard widget CRUD.

Endpoints
---------
GET  /api/signals                       — public signal registry
GET  /api/dashboard/widgets             — user's widgets (auth required)
POST /api/dashboard/widgets             — add widget (auth required)
DELETE /api/dashboard/widgets/{id}      — remove widget (auth required)
GET  /api/dashboard/widgets/{id}/summary — run existing scanner for today (auth required)

Architecture note
-----------------
The summary endpoint calls scan_engine.run_scan() directly — the same function
used by /api/signal-scanner.  No signal-detection logic is duplicated here.

Caching strategy
----------------
Widget summaries are cached in-memory, keyed by (scanner_signal, scan_date).
The cache is automatically invalidated whenever test.py runs its
  DELETE FROM historical_data + batch INSERT
because that changes MAX(Timestamp) in historical_data, which we use as a
"data version" fingerprint.  No hooks into test.py or ingest.py are needed.

Cache lifecycle:
  1. First request for signal X on date D  → run_scan() → store result.
  2. Subsequent requests                   → return cached result instantly.
  3. test.py runs                          → MAX(Timestamp) changes
                                           → _get_data_version() returns new value
                                           → cache miss → run_scan() again.
  4. After test.py finishes               → new results cached, cycle repeats.
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth import get_current_user, verify_csrf
from database import get_connection
from scan_engine import run_scan
import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Dashboard Widgets"])

MAX_WIDGETS = 8


# ──────────────────────────────────────────────────────────────
# Widget Summary Cache
# ──────────────────────────────────────────────────────────────
# Keyed by (scanner_signal, scan_date_str).
# Each entry stores:
#   {
#     "data_version": "2026-08-10T18:23:00"  ← MAX(Timestamp) when cached
#     "result": { count, data_available, last_updated, ... }
#   }
# The cache is invalidated automatically: if the current data_version
# (MAX Timestamp in historical_data) differs from the stored one, the
# cached entry is discarded and run_scan() is called again.
# This means the cache resets exactly when test.py finishes writing
# new data — no explicit hooks into test.py or ingest.py required.

_summary_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# Cached data_version string so we only query MAX(Timestamp) once per request
# batch rather than once per widget.
_data_version_cache: Dict[str, Any] = {"version": None, "fetched_at": None}
_version_ttl_seconds = 30  # re-check DB version at most every 30 s


class AddWidgetBody(BaseModel):
    signal_id: int


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _get_latest_data_date(conn) -> Optional[date]:
    """Return the latest DATE in historical_data, or None if table is empty."""
    cur = conn.cursor()
    cur.execute("SELECT DATE(MAX(Timestamp)) FROM historical_data")
    row = cur.fetchone()
    cur.close()
    if row and row[0]:
        return row[0]
    return None


def _get_data_version(conn) -> Optional[str]:
    """
    Return a string fingerprint of the current data state:
        str(MAX(Timestamp))  from historical_data

    This value changes every time test.py runs its DELETE + batch INSERT,
    making it a perfect cache-invalidation key.
    Returns None when the table is empty.
    """
    import time
    now = time.monotonic()
    with _cache_lock:
        fetched_at = _data_version_cache["fetched_at"]
        if fetched_at is not None and (now - fetched_at) < _version_ttl_seconds:
            return _data_version_cache["version"]

    # Re-query the DB
    cur = conn.cursor()
    cur.execute("SELECT MAX(Timestamp) FROM historical_data")
    row = cur.fetchone()
    cur.close()
    version = str(row[0]) if (row and row[0]) else None

    with _cache_lock:
        _data_version_cache["version"]    = version
        _data_version_cache["fetched_at"] = time.monotonic()

    return version


def _serialize_widget(row: dict) -> dict:
    """Make widget row JSON-serialisable."""
    if "created_at" in row and hasattr(row["created_at"], "isoformat"):
        row["created_at"] = row["created_at"].isoformat()
    return row


# ──────────────────────────────────────────────────────────────
# GET /api/signals — public signal registry
# ──────────────────────────────────────────────────────────────

@router.get("/signals")
def list_signals():
    """Return all active signals from signal_registry, ordered by category then display_name."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT signal_id, display_name, description, category, scanner_signal "
            "FROM signal_registry WHERE is_active = 1 "
            "ORDER BY category, display_name"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("list_signals DB error")
        raise HTTPException(status_code=500, detail="Failed to load signals.")

    return rows


# ──────────────────────────────────────────────────────────────
# GET /api/dashboard/widgets
# ──────────────────────────────────────────────────────────────

@router.get("/dashboard/widgets")
def get_user_widgets(current_user: dict = Depends(get_current_user)):
    """Return the current user's dashboard widgets, joined with signal_registry."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                w.id,
                w.signal_id,
                w.position,
                w.created_at,
                sr.display_name,
                sr.description,
                sr.category,
                sr.scanner_signal
            FROM user_dashboard_widgets w
            JOIN signal_registry sr ON sr.signal_id = w.signal_id
            WHERE w.user_id = %s
            ORDER BY w.position ASC
            """,
            (current_user["id"],),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("get_user_widgets DB error for user_id=%s", current_user["id"])
        raise HTTPException(status_code=500, detail="Failed to load widgets.")

    return [_serialize_widget(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# POST /api/dashboard/widgets
# ──────────────────────────────────────────────────────────────

@router.post("/dashboard/widgets", status_code=status.HTTP_201_CREATED)
def add_widget(
    body: AddWidgetBody,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """
    Add a signal widget to the current user's dashboard.
    - Verifies the signal exists and is active.
    - Rejects if user already has that signal (409).
    - Rejects if user already has 8 widgets (400).
    """
    user_id = current_user["id"]

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # 1. Verify signal exists and is active
        cursor.execute(
            "SELECT signal_id, display_name FROM signal_registry "
            "WHERE signal_id = %s AND is_active = 1",
            (body.signal_id,),
        )
        signal_row = cursor.fetchone()
        if not signal_row:
            cursor.close(); conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Signal {body.signal_id} not found or inactive.",
            )

        # 2. Count current widgets
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM user_dashboard_widgets WHERE user_id = %s",
            (user_id,),
        )
        cnt = cursor.fetchone()["cnt"]
        if cnt >= MAX_WIDGETS:
            cursor.close(); conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum of {MAX_WIDGETS} widgets reached. Remove one before adding another.",
            )

        # 3. Check for duplicate
        cursor.execute(
            "SELECT id FROM user_dashboard_widgets WHERE user_id = %s AND signal_id = %s",
            (user_id, body.signal_id),
        )
        if cursor.fetchone():
            cursor.close(); conn.close()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Widget for '{signal_row['display_name']}' already exists on your dashboard.",
            )

        # 4. Determine next position
        cursor.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos "
            "FROM user_dashboard_widgets WHERE user_id = %s",
            (user_id,),
        )
        next_pos = cursor.fetchone()["next_pos"]

        # 5. Insert
        cursor.execute(
            "INSERT INTO user_dashboard_widgets (user_id, signal_id, position) VALUES (%s, %s, %s)",
            (user_id, body.signal_id, next_pos),
        )
        conn.commit()

        # 6. Fetch the created row with signal info
        cursor.execute(
            """
            SELECT w.id, w.signal_id, w.position, w.created_at,
                   sr.display_name, sr.description, sr.category, sr.scanner_signal
            FROM user_dashboard_widgets w
            JOIN signal_registry sr ON sr.signal_id = w.signal_id
            WHERE w.user_id = %s AND w.signal_id = %s
            """,
            (user_id, body.signal_id),
        )
        created = cursor.fetchone()
        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("add_widget DB error for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Failed to add widget.")

    return _serialize_widget(created)



# ──────────────────────────────────────────────────────────────
# DELETE /api/dashboard/widgets/{id}
# ──────────────────────────────────────────────────────────────

@router.delete("/dashboard/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_widget(
    widget_id: int,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """
    Remove a widget. Only the owning user may delete.
    After deletion, compacts positions so there are no gaps.
    """
    user_id = current_user["id"]
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Ownership check
        cursor.execute(
            "SELECT id, user_id FROM user_dashboard_widgets WHERE id = %s",
            (widget_id,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.close(); conn.close()
            raise HTTPException(status_code=404, detail="Widget not found.")
        if row["user_id"] != user_id:
            cursor.close(); conn.close()
            raise HTTPException(status_code=403, detail="You do not own this widget.")

        # Delete
        cursor.execute("DELETE FROM user_dashboard_widgets WHERE id = %s", (widget_id,))

        # Compact positions — renumber remaining widgets 0, 1, 2, ...
        cursor.execute(
            "SELECT id FROM user_dashboard_widgets WHERE user_id = %s ORDER BY position ASC",
            (user_id,),
        )
        remaining = cursor.fetchall()
        for idx, r in enumerate(remaining):
            cursor.execute(
                "UPDATE user_dashboard_widgets SET position = %s WHERE id = %s",
                (idx, r["id"]),
            )

        conn.commit()
        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete_widget DB error for user_id=%s widget_id=%s", user_id, widget_id)
        raise HTTPException(status_code=500, detail="Failed to delete widget.")



# ──────────────────────────────────────────────────────────────
# GET /api/dashboard/widgets/{id}/summary
# ──────────────────────────────────────────────────────────────

@router.get("/dashboard/widgets/{widget_id}/summary")
def widget_summary(
    widget_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Run the existing Technical Signal Scanner for today and return a count summary.

    Results are cached in-memory, keyed by (scanner_signal, scan_date).
    The cache is invalidated automatically when test.py writes new data:
      test.py does DELETE FROM historical_data + INSERT, which changes
      MAX(Timestamp) — we use that as our data-version fingerprint.

    Reuses scan_engine.run_scan() — zero new detection logic is added here.

    Returns:
        {
            "signal_id": 1,
            "signal_name": "Hammer",
            "scanner_signal": "Hammer",
            "date": "2026-08-10",
            "count": 12,
            "data_available": true,
            "last_updated": "2026-08-10",
            "cached": true
        }
    """
    user_id = current_user["id"]
    today_str = date.today().isoformat()

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Verify widget belongs to user
        cursor.execute(
            """
            SELECT w.id, w.signal_id,
                   sr.display_name, sr.scanner_signal
            FROM user_dashboard_widgets w
            JOIN signal_registry sr ON sr.signal_id = w.signal_id
            WHERE w.id = %s AND w.user_id = %s
            """,
            (widget_id, user_id),
        )
        widget = cursor.fetchone()
        if widget is None:
            cursor.close(); conn.close()
            raise HTTPException(status_code=404, detail="Widget not found.")

        # Get data version fingerprint (MAX Timestamp) — cheap, TTL-cached 30s
        data_version = _get_data_version(conn)

        # Get latest data date for data_available flag
        latest_date = _get_latest_data_date(conn)
        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("widget_summary DB error")
        raise HTTPException(status_code=500, detail="Failed to fetch widget summary.")

    if latest_date is None:
        return {
            "signal_id":      widget["signal_id"],
            "signal_name":    widget["display_name"],
            "scanner_signal": widget["scanner_signal"],
            "date":           today_str,
            "count":          0,
            "data_available": False,
            "last_updated":   None,
            "cached":         False,
        }

    last_updated_str = latest_date.isoformat()
    data_available   = (latest_date is not None)
    scan_date        = last_updated_str          # always scan the latest available date
    scanner_signal   = widget["scanner_signal"]

    # ── Cache lookup ─────────────────────────────────────────
    cache_key = (scanner_signal, scan_date)
    with _cache_lock:
        cached_entry = _summary_cache.get(cache_key)
        if cached_entry is not None and cached_entry["data_version"] == data_version:
            logger.info("cache HIT  signal=%s date=%s", scanner_signal, scan_date)
            result = cached_entry["result"].copy()
            result["cached"] = True
            return result

    # ── Cache miss: run the actual scanner ──────────────────
    logger.info("cache MISS signal=%s date=%s version=%s", scanner_signal, scan_date, data_version)

    # Build condition exactly as /api/signal-scanner does
    if scanner_signal == "NR":
        condition = {"field": "NR", "operator": ">", "value": 0}
    else:
        condition = {"field": scanner_signal, "operator": "=="}

    try:
        rows  = run_scan(
            conditions=[condition],
            logic="AND",
            start_date=scan_date,
            end_date=scan_date,
        )
        count = len(rows)
    except Exception as exc:
        logger.warning("widget_summary scan error for signal=%s: %s", scanner_signal, exc)
        count = 0

    result = {
        "signal_id":      widget["signal_id"],
        "signal_name":    widget["display_name"],
        "scanner_signal": scanner_signal,
        "date":           scan_date,
        "count":          count,
        "data_available": data_available,
        "last_updated":   last_updated_str,
    }

    # ── Store in cache ───────────────────────────────────────
    with _cache_lock:
        _summary_cache[cache_key] = {
            "data_version": data_version,
            "result":       result,
        }
        # Evict stale entries for the same signal on older dates
        stale = [k for k in _summary_cache if k[0] == scanner_signal and k[1] != scan_date]
        for k in stale:
            del _summary_cache[k]

    return {**result, "cached": False}


# ──────────────────────────────────────────────────────────────
# GET /api/market-strip
# ──────────────────────────────────────────────────────────────

@router.get("/market-strip", tags=["Dashboard"])
def get_market_strip():
    """
    Returns full-width market strip data:
    Nifty 50, Nifty 500 close & % change, breadth (advancing, declining, unchanged),
    market turnover, and latest close date.
    """
    conn = get_connection()
    try:
        data_version = _get_data_version(conn)
    finally:
        conn.close()

    cache_key = "market_strip_v1"
    cached = cache.get(cache_key, data_version) if "cache" in globals() else None
    if cached is not None:
        return JSONResponse(content=cached)

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT 2")
        date_rows = cur.fetchall()
        if not date_rows:
            return JSONResponse(content={"error": "No data available"})

        latest_ts = date_rows[0]["Timestamp"]
        prev_ts = date_rows[1]["Timestamp"] if len(date_rows) > 1 else None

        # Fetch index values for latest and previous date
        cur.execute(
            """
            SELECT Symbol, Timestamp, Close, Open
            FROM historical_data
            WHERE Symbol IN ('^NSEI', '^CRSLDX')
              AND Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
            """,
            (latest_ts, latest_ts)
        )
        latest_indices = {r["Symbol"]: r for r in cur.fetchall()}

        prev_indices = {}
        if prev_ts:
            cur.execute(
                """
                SELECT Symbol, Timestamp, Close
                FROM historical_data
                WHERE Symbol IN ('^NSEI', '^CRSLDX')
                  AND Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                """,
                (prev_ts, prev_ts)
            )
            prev_indices = {r["Symbol"]: r for r in cur.fetchall()}

        def _calc_change(sym):
            curr = latest_indices.get(sym)
            if not curr or curr["Close"] is None:
                return {"close": None, "change_pct": 0.0}
            c_val = float(curr["Close"])
            prev = prev_indices.get(sym)
            if prev and prev["Close"]:
                p_val = float(prev["Close"])
                pct = round(((c_val - p_val) / p_val) * 100, 2)
            elif curr.get("Open"):
                o_val = float(curr["Open"])
                pct = round(((c_val - o_val) / o_val) * 100, 2)
            else:
                pct = 0.0
            return {"close": c_val, "change_pct": pct}

        nifty50_data = _calc_change("^NSEI")
        nifty500_data = _calc_change("^CRSLDX")

        # Market Breadth and Turnover on latest_ts
        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN Close > Open THEN 1 ELSE 0 END) as advances,
                SUM(CASE WHEN Close < Open THEN 1 ELSE 0 END) as declines,
                SUM(CASE WHEN Close = Open THEN 1 ELSE 0 END) as unchanged,
                SUM(Volume * Close) as turnover_val,
                SUM(Volume) as total_volume
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
              AND Symbol NOT LIKE '%%^%%'
            """,
            (latest_ts, latest_ts)
        )
        breadth_row = cur.fetchone() or {}

        tot = int(breadth_row.get("total") or 0)
        adv = int(breadth_row.get("advances") or 0)
        dec = int(breadth_row.get("declines") or 0)
        unc = int(breadth_row.get("unchanged") or 0)
        turnover_val = float(breadth_row.get("turnover_val") or 0)

        # Formatted turnover
        turnover_cr = round(turnover_val / 1e7, 2)
        turnover_str = f"₹{turnover_cr:,.0f} Cr" if turnover_cr >= 1 else f"₹{turnover_val:,.0f}"

        as_of_str = latest_ts.strftime("%Y-%m-%d") if hasattr(latest_ts, "strftime") else str(latest_ts)[:10]

        result = {
            "as_of_date": as_of_str,
            "nifty_50": nifty50_data,
            "nifty_500": nifty500_data,
            "breadth": {
                "total": tot,
                "advancing": adv,
                "declining": dec,
                "unchanged": unc,
                "advancing_pct": round((adv / max(tot, 1)) * 100, 1),
                "declining_pct": round((dec / max(tot, 1)) * 100, 1),
                "unchanged_pct": round((unc / max(tot, 1)) * 100, 1),
            },
            "turnover": turnover_val,
            "turnover_cr": turnover_cr,
            "turnover_formatted": turnover_str,
        }

        try:
            cache.set(cache_key, result, data_version, ttl=120)
        except Exception:
            pass

        return JSONResponse(content=result)
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# GET /api/dashboard/today
# ──────────────────────────────────────────────────────────────

@router.get("/dashboard/today", tags=["Dashboard"])
def get_dashboard_today(current_user: dict = Depends(get_current_user)):
    """
    Returns today's pattern screens snapshot with:
    - Counts and deltas vs previous trading session
    - Top 3 tickers by RS rating for each screen
    """
    conn = get_connection()
    try:
        data_version = _get_data_version(conn)
    finally:
        conn.close()

    cache_key = "dashboard_today_v1"
    try:
        cached = cache.get(cache_key, data_version)
        if cached is not None:
            return JSONResponse(content=cached)
    except Exception:
        pass

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT DISTINCT Timestamp FROM historical_data ORDER BY Timestamp DESC LIMIT 2")
        date_rows = cur.fetchall()
        if not date_rows:
            return JSONResponse(content={"as_of_date": None, "screens": []})

        latest_ts = date_rows[0]["Timestamp"]
        prev_ts = date_rows[1]["Timestamp"] if len(date_rows) > 1 else None

        # 1. Screen counts today
        cur.execute(
            """
            SELECT 
                SUM(screen_vcp) as vcp_cnt,
                SUM(screen_blue_sky) as bluesky_cnt,
                SUM(screen_multi_year_breakout) as myb_cnt,
                SUM(High_Relative_Volume_30) as hrv_cnt,
                SUM(screen_high_delivery_volume) as hdv_cnt,
                SUM(CASE WHEN rsi_divergence_type IS NOT NULL THEN 1 ELSE 0 END) as rsi_cnt,
                SUM(convergence_3) as conv_cnt,
                SUM(signal5) as gc_cnt,
                SUM(CASE WHEN stage=2 AND stage_bucket='fresh_breakout' THEN 1 ELSE 0 END) as s2_cnt
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
            """,
            (latest_ts, latest_ts)
        )
        today_counts = cur.fetchone() or {}

        # 2. Screen counts yesterday for deltas
        prev_counts = {}
        if prev_ts:
            cur.execute(
                """
                SELECT 
                    SUM(screen_vcp) as vcp_cnt,
                    SUM(screen_blue_sky) as bluesky_cnt,
                    SUM(screen_multi_year_breakout) as myb_cnt,
                    SUM(High_Relative_Volume_30) as hrv_cnt,
                    SUM(screen_high_delivery_volume) as hdv_cnt,
                    SUM(CASE WHEN rsi_divergence_type IS NOT NULL THEN 1 ELSE 0 END) as rsi_cnt,
                    SUM(convergence_3) as conv_cnt,
                    SUM(signal5) as gc_cnt,
                    SUM(CASE WHEN stage=2 AND stage_bucket='fresh_breakout' THEN 1 ELSE 0 END) as s2_cnt
                FROM historical_data
                WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
                """,
                (prev_ts, prev_ts)
            )
            prev_counts = cur.fetchone() or {}

        # 3. Top tickers by RS rank today
        cur.execute(
            """
            SELECT 
                Symbol, IFNULL(rs_rank, 0) as rs_rank, Close, Open,
                screen_vcp, screen_blue_sky, screen_multi_year_breakout,
                High_Relative_Volume_30, screen_high_delivery_volume,
                rsi_divergence_type, convergence_3, signal5,
                (stage=2 AND stage_bucket='fresh_breakout') as s2
            FROM historical_data
            WHERE Timestamp >= %s AND Timestamp < %s + INTERVAL 1 DAY
              AND Symbol NOT LIKE '%%^%%'
            ORDER BY IFNULL(rs_rank, 0) DESC
            """,
            (latest_ts, latest_ts)
        )
        ranked_rows = cur.fetchall()

        screen_configs = [
            {"id": "vcp", "name": "Price Compression Setup", "acronym": "PCS", "key": "vcp_cnt", "flag": "screen_vcp", "icon": "fa-compress-alt"},
            {"id": "blue-sky", "name": "All-Time Peak Breakout", "acronym": "APB", "key": "bluesky_cnt", "flag": "screen_blue_sky", "icon": "fa-cloud"},
            {"id": "multi-year-breakout", "name": "Multi-Year Breakout", "acronym": "MYB", "key": "myb_cnt", "flag": "screen_multi_year_breakout", "icon": "fa-calendar-alt"},
            {"id": "high-relative-volume", "name": "High Relative Volume", "acronym": "5x", "key": "hrv_cnt", "flag": "High_Relative_Volume_30", "icon": "fa-volume-up"},
            {"id": "high-delivery-volume", "name": "High Delivery Volume", "acronym": "75%-99%", "key": "hdv_cnt", "flag": "screen_high_delivery_volume", "icon": "fa-truck-loading"},
            {"id": "rsi-divergence", "name": "RSI Divergence", "acronym": "RSI", "key": "rsi_cnt", "flag": "rsi_divergence_type", "icon": "fa-arrows-alt-h"},
            {"id": "ema-convergence", "name": "EMA Convergence", "acronym": "C3", "key": "conv_cnt", "flag": "convergence_3", "icon": "fa-compress-arrows-alt"},
            {"id": "golden-crossover", "name": "Golden Crossover", "acronym": "50/200", "key": "gc_cnt", "flag": "signal5", "icon": "fa-crosshairs"},
            {"id": "stage-2", "name": "Stage 2 Stocks", "acronym": "Stage 2", "key": "s2_cnt", "flag": "s2", "icon": "fa-arrow-trend-up"}
        ]

        def _clean_sym(s):
            return s[:-3] if s.endswith(".NS") else s

        screen_results = []
        for sc in screen_configs:
            cnt = int(today_counts.get(sc["key"]) or 0)
            p_cnt = int(prev_counts.get(sc["key"]) or 0)
            delta = cnt - p_cnt

            flag_name = sc["flag"]
            top_stocks = []
            for r in ranked_rows:
                val = r.get(flag_name)
                if val and val != 0 and val != "0":
                    c_val = float(r["Close"]) if r.get("Close") else 0
                    o_val = float(r["Open"]) if r.get("Open") else c_val
                    chg = round(((c_val - o_val) / o_val) * 100, 2) if o_val else 0
                    top_stocks.append({
                        "symbol": _clean_sym(r["Symbol"]),
                        "full_symbol": r["Symbol"],
                        "rs_rank": float(r["rs_rank"] or 0),
                        "close": c_val,
                        "change_pct": chg
                    })
                    if len(top_stocks) >= 3:
                        break

            # Special case for RSI divergence if count on single day is 0: check recent divergence window
            if sc["id"] == "rsi-divergence" and cnt == 0:
                try:
                    cur.execute(
                        """
                        SELECT Symbol, IFNULL(rs_rank, 0) as rs_rank, Close, Open
                        FROM historical_data
                        WHERE rsi_divergence_type IS NOT NULL
                          AND Timestamp >= DATE_SUB(%s, INTERVAL 15 DAY)
                          AND Symbol NOT LIKE '%%^%%'
                        ORDER BY rsi_divergence_score DESC, IFNULL(rs_rank, 0) DESC
                        LIMIT 3
                        """,
                        (latest_ts,)
                    )
                    rsi_rows = cur.fetchall()
                    if rsi_rows:
                        cnt = len(rsi_rows)
                        for rr in rsi_rows:
                            rc = float(rr["Close"]) if rr.get("Close") else 0
                            ro = float(rr["Open"]) if rr.get("Open") else rc
                            top_stocks.append({
                                "symbol": _clean_sym(rr["Symbol"]),
                                "full_symbol": rr["Symbol"],
                                "rs_rank": float(rr["rs_rank"] or 0),
                                "close": rc,
                                "change_pct": round(((rc - ro) / ro) * 100, 2) if ro else 0
                            })
                except Exception:
                    pass

            screen_results.append({
                "id": sc["id"],
                "name": sc["name"],
                "acronym": sc["acronym"],
                "icon": sc["icon"],
                "count": cnt,
                "delta": delta,
                "top_tickers": top_stocks
            })

        as_of_str = latest_ts.strftime("%Y-%m-%d") if hasattr(latest_ts, "strftime") else str(latest_ts)[:10]

        res = {
            "as_of_date": as_of_str,
            "screens": screen_results
        }

        try:
            cache.set(cache_key, res, data_version, ttl=180)
        except Exception:
            pass

        return JSONResponse(content=res)
    finally:
        cur.close()
        conn.close()
