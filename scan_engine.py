"""
scan_engine.py — Shared scan execution logic.

This module is the single source of truth for:
  - ALLOWED_FIELDS  : the set of valid scan fields (maps 1-to-1 to DB columns)
  - validate_conditions() : raises HTTPException(400) on bad input
  - run_scan()      : builds one compound SQL query and returns deduplicated rows

Both the existing /api/signal-scanner endpoint AND the new /api/scans/* endpoints
call these helpers, so scanning logic lives in exactly one place.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException, status

from database import get_connection

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Schema: allowed fields + their legal operators
# ──────────────────────────────────────────────────────────────

# Fields that use  WHERE col = 1  (binary/flag columns)
_BINARY_FIELDS: frozenset[str] = frozenset([
    # Candlestick patterns
    "Hammer", "Shooting_Star", "Doji", "Engulfing",
    "Dark_Cloud_Cover", "Morning_Star", "Evening_Star", "Piercing_Line",
    # Custom composite signals
    "signal1", "signal2", "signal3", "signal4", "signal5", "top_decile",
    # Price extremes (2y/5y/10y auto-window handled in run_scan)
    "new_52w_high", "new_52w_low", "near_52w_high",
    "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d",
    # RSI state flags
    "oversold", "overbought", "rsi_lt_30", "rsi_gt_70",
    # Volume
    "High_Relative_Volume_30",
    # ADX
    "adx_trigger",
    # Convergence Scanners
    "convergence_5a", "convergence_3", "convergence_4",
    # Delivery Momentum Signal
    "delivery_momentum_signal",
    # Pattern Screens
    "screen_vcp", "screen_blue_sky", "screen_multi_year_breakout", "screen_ipo_base", "screen_high_relative_volume", "screen_high_delivery_volume",
])

# Fields that use  WHERE col > value
_NR_FIELDS: frozenset[str] = frozenset(["NR"])

# Numeric comparison fields (RCS vs benchmark)
_NUMERIC_FIELDS: frozenset[str] = frozenset([
    "RCS_30D",
])

# Combined set for quick membership checks
ALLOWED_FIELDS: frozenset[str] = _BINARY_FIELDS | _NR_FIELDS | _NUMERIC_FIELDS

# Fields whose scan window is always forced to the latest date window
_AUTO_WINDOW_FIELDS: frozenset[str] = frozenset([
    "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d", "RCS_30D", "delivery_momentum_signal",
    "near_52w_high",
])

# Operator rules per field type
_BINARY_OPS: frozenset[str] = frozenset(["=="])
_NR_OPS: frozenset[str] = frozenset([">"])
_NUMERIC_OPS: frozenset[str] = frozenset([">", "<", ">=", "<=", "=="])

FIELD_META: dict[str, dict] = {
    **{f: {"operators": _BINARY_OPS, "type": "binary"} for f in _BINARY_FIELDS},
    **{f: {"operators": _NR_OPS, "type": "nr"} for f in _NR_FIELDS},
    **{f: {"operators": _NUMERIC_OPS, "type": "numeric"} for f in _NUMERIC_FIELDS},
}

# Human-readable labels for the frontend (exported for reference)
FIELD_LABELS: dict[str, str] = {
    "Hammer": "Hammer (Candlestick)",
    "Shooting_Star": "Shooting Star (Candlestick)",
    "Doji": "Doji (Candlestick)",
    "Engulfing": "Engulfing (Candlestick)",
    "Dark_Cloud_Cover": "Dark Cloud Cover (Candlestick)",
    "Morning_Star": "Morning Star (Candlestick)",
    "Evening_Star": "Evening Star (Candlestick)",
    "Piercing_Line": "Piercing Line (Candlestick)",
    "signal1": "Signal 1 (Composite)",
    "signal2": "Signal 2 (Composite)",
    "signal3": "Signal 3 (Composite)",
    "signal4": "Signal 4 (Composite)",
    "signal5": "Signal 5 (Composite)",
    "top_decile": "Top Decile (Composite)",
    "new_52w_high": "New 52-Week High",
    "new_52w_low": "New 52-Week Low",
    "near_52w_high": "Near 52-Week High (within 3%)",
    "hit_2y_high_14d": "2-Year High (last 14 days)",
    "hit_5y_high_14d": "5-Year High (last 14 days)",
    "hit_10y_high_14d": "10-Year High (last 14 days)",
    "oversold": "Oversold (RSI)",
    "overbought": "Overbought (RSI)",
    "rsi_lt_30": "RSI < 30",
    "rsi_gt_70": "RSI > 70",
    "High_Relative_Volume_30": "High Relative Volume (30d)",
    "adx_trigger": "ADX Trigger",
    "NR": "Narrow Range (NR)",
    "convergence_5a": "Convergence 5A (EMA 4,9,18,50,200)",
    "convergence_3": "Convergence 3 (EMA 4,9,18 + >100,150,200)",
    "convergence_4": "Convergence 4 (EMA 5,9,21,50)",
    "delivery_momentum_signal": "Delivery",
    "RCS_30D": "RCS 30-Day (% vs NIFTY 500)",
    "screen_vcp": "Price Compression Setup (PCS)",
    "screen_blue_sky": "All-Time Peak Breakout (APB)",
    "screen_multi_year_breakout": "Multi-Year Breakout",
    "screen_ipo_base": "IPO Base",
    "screen_high_relative_volume": "High Relative Volume (5x)",
    "screen_high_delivery_volume": "High Delivery Volume (80%+)",
}

# Grouped field list for the frontend dropdown
FIELD_GROUPS: list[dict] = [
    {
        "label": "Relative Strength (vs NIFTY 500)",
        "fields": ["RCS_30D"],
    },
    {
        "label": "Candlestick Patterns",
        "fields": [
            "Hammer", "Shooting_Star", "Doji", "Engulfing",
            "Dark_Cloud_Cover", "Morning_Star", "Evening_Star", "Piercing_Line",
        ],
    },
    {
        "label": "RSI Signals",
        "fields": ["rsi_lt_30", "rsi_gt_70", "oversold", "overbought"],
    },
    {
        "label": "Price Extremes",
        "fields": [
            "new_52w_high", "new_52w_low", "near_52w_high",
            "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d",
        ],
    },
    {
        "label": "Volume & ADX",
        "fields": ["High_Relative_Volume_30", "adx_trigger"],
    },
    {
        "label": "Composite Signals",
        "fields": ["signal1", "signal2", "signal3", "signal4", "signal5", "top_decile",
                   "convergence_5a", "convergence_3", "convergence_4", "delivery_momentum_signal"],
    },
    {
        "label": "Narrow Range",
        "fields": ["NR"],
    },
    {
        "label": "Pattern Screens",
        "fields": ["screen_vcp", "screen_blue_sky", "screen_multi_year_breakout", "screen_ipo_base", "screen_high_relative_volume", "screen_high_delivery_volume"],
    },
]


# ──────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────

def validate_conditions(body: dict) -> None:
    """
    Validate a conditions payload dict.  Raises HTTPException(400) with a
    specific message if anything is wrong.

    Expected shape:
      {
        "logic": "AND" | "OR",
        "conditions": [
          {"field": "<ALLOWED_FIELD>", "operator": "<allowed op>", "value": <opt>},
          ...
        ],
        "start_date": "YYYY-MM-DD",   # optional for non-auto-window fields
        "end_date":   "YYYY-MM-DD",   # optional for non-auto-window fields
      }
    """
    logic = body.get("logic", "AND")
    if logic not in ("AND", "OR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid logic '{logic}'. Must be 'AND' or 'OR'.",
        )

    conditions = body.get("conditions")
    if not conditions or not isinstance(conditions, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'conditions' must be a non-empty list.",
        )

    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Condition at index {i} must be an object.",
            )

        field = cond.get("field")
        if field not in ALLOWED_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown field '{field}' in condition {i}. "
                    f"Allowed fields: {sorted(ALLOWED_FIELDS)}"
                ),
            )

        operator = cond.get("operator")
        allowed_ops = FIELD_META[field]["operators"]
        if operator not in allowed_ops:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Operator '{operator}' is not valid for field '{field}'. "
                    f"Allowed: {sorted(allowed_ops)}"
                ),
            )

        # FIX [LOW-16]: NR can only hold values in {0, 5, 6, 7, 8} because the
        # rolling windows in test.py are 5, 6, 7, 8 days.  Accepting 1-4 was
        # silently misleading (e.g. "NR > 4" was equivalent to "NR > 0").
        if field in _NR_FIELDS:
            value = cond.get("value", 0)
            _valid_nr_values = {0, 5, 6, 7, 8}
            try:
                v = int(value)
                if v not in _valid_nr_values:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"NR 'value' must be one of {sorted(_valid_nr_values)}, "
                        f"got '{value}'. NR windows are 5, 6, 7, and 8 days only."
                    ),
                )


# ──────────────────────────────────────────────────────────────
# SQL clause builder (per condition)
# ──────────────────────────────────────────────────────────────

def _condition_to_clause(cond: dict) -> tuple[str, list]:
    """
    Convert a single validated condition dict to a (sql_fragment, params) pair.
    The sql_fragment contains %s placeholders for safe parameterised queries.
    """
    field = cond["field"]
    operator = cond.get("operator", "==")

    if field in _BINARY_FIELDS:
        # WHERE col = 1
        return (f"`{field}` = %s", [1])
    elif field in _NR_FIELDS:
        # WHERE NR > value
        value = int(cond.get("value", 0))
        return (f"`{field}` > %s", [value])
    elif field in _NUMERIC_FIELDS:
        value = float(cond.get("value", 0))
        op = "=" if operator == "==" else operator
        return (f"`{field}` {op} %s", [value])
    else:
        raise ValueError(f"Unhandled field: {field}")


# ──────────────────────────────────────────────────────────────
# Date window resolution
# ──────────────────────────────────────────────────────────────

def _resolve_date_window(
    conditions: list[dict],
    start_date: Optional[str],
    end_date: Optional[str],
    conn,
) -> tuple[str, str]:
    """
    Determine the effective (start_date, end_date) for the query.

    Rules:
    - If ALL conditions are auto-window fields (2y/5y/10y highs), use last 14 days.
    - If ANY non-auto-window field is present, a date range must be supplied.
    - Mixed scans (some auto-window + some not) use the supplied date range for
      everything; auto-window fields still make sense in that window.
    """
    has_rcs = any(c["field"] in ("RCS_30D", "delivery_momentum_signal", "near_52w_high") for c in conditions)
    all_auto = all(c["field"] in _AUTO_WINDOW_FIELDS for c in conditions)

    if has_rcs or all_auto:
        raw = conn.cursor()
        raw.execute("SELECT DATE(MAX(Timestamp)) FROM historical_data")
        row = raw.fetchone()
        raw.close()
        if not row or not row[0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No data in historical_data table.",
            )
        last_date: date = row[0]
        if has_rcs:
            # RCS, Delivery, and Near 52W High scans lock strictly to the latest trading date available if start/end date not explicitly passed
            if start_date and end_date and not any(c["field"] in ("RCS_30D", "near_52w_high") for c in conditions):
                return (start_date, end_date)
            return (last_date.strftime("%Y-%m-%d"), last_date.strftime("%Y-%m-%d"))
        return (
            (last_date - timedelta(days=14)).strftime("%Y-%m-%d"),
            last_date.strftime("%Y-%m-%d"),
        )

    if not start_date or not end_date:
        raw = conn.cursor()
        raw.execute("SELECT DATE(MAX(Timestamp)) FROM historical_data")
        row = raw.fetchone()
        raw.close()
        if row and row[0]:
            last_date_str = row[0].strftime("%Y-%m-%d")
            return last_date_str, last_date_str
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'start_date' and 'end_date' are required (YYYY-MM-DD) for the selected conditions.",
        )
    return start_date, end_date


# ──────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────

def run_scan(
    conditions: list[dict],
    logic: str = "AND",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """
    Execute a multi-condition scan against historical_data.

    Parameters
    ----------
    conditions : validated list of condition dicts
    logic      : 'AND' | 'OR'
    start_date : 'YYYY-MM-DD'
    end_date   : 'YYYY-MM-DD'

    Returns a list of dicts with Symbol, Timestamp, and all matched
    signal columns, deduplicated by (Symbol, Timestamp).
    """
    if not conditions:
        return []

    conn = get_connection()
    try:
        eff_start, eff_end = _resolve_date_window(conditions, start_date, end_date, conn)

        # Build SELECT columns: Symbol, Timestamp + every field being scanned
        fields_in_query = list({c["field"] for c in conditions})
        if "delivery_momentum_signal" in fields_in_query:
            if "Recent_Deliv_Pct" not in fields_in_query:
                fields_in_query.append("Recent_Deliv_Pct")
            if "Baseline_Deliv_Pct" not in fields_in_query:
                fields_in_query.append("Baseline_Deliv_Pct")
        select_cols = ", ".join(["Symbol", "Timestamp"] + [f"`{f}`" for f in fields_in_query])

        # Build WHERE clauses
        clauses, params = [], []
        for cond in conditions:
            frag, p = _condition_to_clause(cond)
            clauses.append((frag, cond.get("logic", logic)))
            params.extend(p)

        where_conditions = clauses[0][0]
        for frag, cond_logic in clauses[1:]:
            op = "AND" if str(cond_logic).upper() == "AND" else "OR"
            where_conditions = f"({where_conditions} {op} {frag})"

        query = (
            f"SELECT {select_cols} FROM historical_data "
            f"WHERE DATE(Timestamp) BETWEEN %s AND %s "
            f"AND ({where_conditions}) "
            f"ORDER BY Timestamp DESC, Symbol ASC"
        )
        all_params = [eff_start, eff_end] + params

        logger.info("scan_engine SQL: %s | params: %s", query, all_params)

        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, all_params)
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    # If delivery_momentum_signal is present, sort by highest difference (Recent_Deliv_Pct - Baseline_Deliv_Pct) DESC
    if "delivery_momentum_signal" in fields_in_query:
        rows.sort(
            key=lambda r: (
                (float(r["Recent_Deliv_Pct"]) if r.get("Recent_Deliv_Pct") is not None else -999.0) -
                (float(r["Baseline_Deliv_Pct"]) if r.get("Baseline_Deliv_Pct") is not None else 0.0)
            ),
            reverse=True,
        )
    elif "RCS_30D" in fields_in_query:
        rows.sort(
            key=lambda r: (float(r["RCS_30D"]) if r.get("RCS_30D") is not None else -999999.0),
            reverse=True,
        )

    # Deduplicate by (Symbol, Timestamp) and serialise datetime objects
    seen: set = set()
    result: list[dict] = []
    for row in rows:
        key = (row["Symbol"], str(row["Timestamp"]))
        if key in seen:
            continue
        seen.add(key)
        if not isinstance(row["Timestamp"], str):
            row["Timestamp"] = row["Timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        result.append(row)

    return result
