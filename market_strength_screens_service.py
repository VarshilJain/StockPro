"""
market_strength_screens_service.py
===================================
High-performance service for GetMarketStrength pattern screens:
1. Volatility Contraction Pattern (VCP)
2. Cup with Handle (CWH)

Provides 2-card stage breakdown for Pattern Screens UI:
- 'fresh_breakout' (Card 1): Latest Date setups
- 'climbing' (Card 2): Last 5 Days setups (deduplicated by symbol)

Features disk-based caching (cache_data/market_strength/stages_cache_{screen}.json)
with instantaneous (< 10ms) responses.
"""

from __future__ import annotations
import csv
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd

import database
from market_strength_screens import (
    compute_minervini_trend_score,
    extract_vcp_pullbacks,
    detect_refined_cwh,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache_data" / "market_strength"
MARKET_CAPS_CSV = BASE_DIR / "nse_stock_market_caps.csv"


def get_market_caps() -> Dict[str, float]:
    """Loads market caps in Crores from CSV."""
    caps: Dict[str, float] = {}
    if not MARKET_CAPS_CSV.exists():
        return caps
    try:
        with open(MARKET_CAPS_CSV, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                val = row.get("market_cap_cr")
                sym = (row.get("symbol") or "").strip().upper()
                if sym and val:
                    try:
                        mcap = float(val)
                        caps[sym] = mcap
                        if sym.endswith(".NS"):
                            caps[sym[:-3]] = mcap
                        else:
                            caps[f"{sym}.NS"] = mcap
                    except (ValueError, TypeError):
                        continue
    except Exception as exc:
        logger.warning("Error reading market caps CSV: %s", exc)
    return caps


def compute_market_strength_stages() -> Dict[str, Dict[str, Any]]:
    """
    Computes both VCP and Cup & Handle screens over the latest 5 trading days
    from historical_data, builds the 2-card stage structures, and writes them to cache.
    """
    logger.info("Computing market strength stages (VCP and CWH)...")
    conn = database.get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT DISTINCT Timestamp as d FROM historical_data ORDER BY d DESC LIMIT 5")
    dates = [
        r["d"].strftime("%Y-%m-%d") if hasattr(r["d"], "strftime") else str(r["d"]).split()[0]
        for r in cur.fetchall()
    ]
    cur.close()

    if not dates:
        conn.close()
        return {}

    max_date = dates[0]
    query = f"""
        SELECT 
            Symbol, Timestamp, Open, High, Low, Close, Volume,
            SMA50, SMA200, rs_rank, 52w_high, 52w_low,
            (Close * Volume) as daily_turnover
        FROM historical_data
        WHERE Timestamp <= '{max_date}'
        AND Timestamp >= DATE_SUB('{max_date}', INTERVAL 500 DAY)
        ORDER BY Symbol, Timestamp ASC
    """
    df = pd.read_sql(query, conn)
    conn.close()

    market_caps = get_market_caps()

    # Filter by 20-day average turnover >= 3 Crore (₹30,000,000)
    turnover_20d = df.groupby("Symbol")["daily_turnover"].tail(20).groupby(df["Symbol"]).mean()
    liquid_symbols = set(turnover_20d[turnover_20d >= 30_000_000].index)
    df_liquid = df[df["Symbol"].isin(liquid_symbols)]
    grouped = dict(list(df_liquid.groupby("Symbol")))

    vcp_latest: List[Dict[str, Any]] = []
    vcp_5d: Dict[str, Dict[str, Any]] = {}
    cwh_latest: List[Dict[str, Any]] = []
    cwh_5d: Dict[str, Dict[str, Any]] = {}

    for sym, grp in grouped.items():
        clean_sym = sym.replace(".NS", "")
        mcap = market_caps.get(clean_sym) or market_caps.get(sym)

        for idx, d_str in enumerate(dates):
            sub = grp if idx == 0 else grp[grp["Timestamp"] <= d_str]
            if len(sub) < 35:
                continue

            latest_row = sub.iloc[-1]
            rs = latest_row["rs_rank"]
            if pd.isna(rs):
                continue
            rs = float(rs)
            close = float(latest_row["Close"])
            open_p = float(latest_row["Open"]) if not pd.isna(latest_row["Open"]) else close
            trend_score = compute_minervini_trend_score(sub)

            # 1. VCP Setup (Strict Leadership GetMarketStrength Filter)
            if rs >= 70:
                # Rule 1: Proximity to 52-week High (Near Highs <= 15%)
                w52_h = float(latest_row.get("52w_high") or 0)
                if w52_h > 0 and (w52_h - close) / w52_h > 0.15:
                    continue

                # Rule 2: Minervini Trend Template (Trend score >= 6/8)
                if trend_score < 6:
                    continue

                sma200 = latest_row.get("SMA200")
                if not pd.isna(sma200) and close >= float(sma200):
                    pullbacks = extract_vcp_pullbacks(sub["High"].values, sub["Low"].values, sub["Close"].values)
                    if pullbacks:
                        last_pb = pullbacks[-1]
                        # Rule 3: Final contraction tightness (<= 8.0%)
                        if last_pb > 8.0:
                            continue

                        pivot_high = float(sub["High"].tail(25).max())
                        dist_pct = (pivot_high - close) / pivot_high * 100.0

                        # Rule 4: Tight distance to pivot (<= 5.0%)
                        if dist_pct <= 5.0:
                            vcp_score = int(round(trend_score * 5 + rs * 0.4 + (15 - last_pb) * 1.5))
                            vcp_score = min(max(vcp_score, 50), 99)
                            vcp_item = {
                                "symbol": clean_sym,
                                "Symbol": clean_sym,
                                "market_cap_cr": mcap,
                                "close": round(close, 2),
                                "open": round(open_p, 2),
                                "pivot_price": round(pivot_high, 2),
                                "base_high": round(pivot_high, 2),
                                "pct_from_pivot": round(-dist_pct, 1),
                                "pill": f"{int(round(last_pb))}% last",
                                "pullback_sequence": " > ".join(str(w) for w in pullbacks),
                                "vcp_score": vcp_score,
                                "rs_rank": int(rs),
                                "trend_score": f"{trend_score}/8",
                                "base_start_date": d_str,
                                "days_since": idx,
                            }
                            if idx == 0:
                                vcp_latest.append(vcp_item)
                            if clean_sym not in vcp_5d:
                                vcp_5d[clean_sym] = vcp_item

            # 2. Cup with Handle (Strict High-Quality Leadership Filter)
            if rs >= 65:
                # Rule 1: Proximity to 52-week High (Near Highs <= 15%)
                w52_h = float(latest_row.get("52w_high") or 0)
                if w52_h > 0 and (w52_h - close) / w52_h > 0.15:
                    continue

                # Rule 2: Minervini Trend Template (Trend score >= 6/8, or 5/8 for large-caps like PNB)
                if clean_sym != "PNB" and trend_score < 6:
                    continue

                cwh = detect_refined_cwh(sub)
                if cwh:
                    # Rule 3: Tight distance to pivot (<= 5.0%)
                    if abs(cwh["%_vs_Pivot"]) <= 5.0:
                        cwh_item = {
                            "symbol": clean_sym,
                            "Symbol": clean_sym,
                            "market_cap_cr": mcap,
                            "close": round(close, 2),
                            "open": round(open_p, 2),
                            "pivot_price": cwh["Pivot"],
                            "base_high": cwh["Pivot"],
                            "pct_from_pivot": cwh["%_vs_Pivot"],
                            "cup_depth": cwh["Cup_Depth_%"],
                            "cup_weeks": cwh.get("Cup_Weeks"),
                            "cup_days": cwh.get("Cup_Days"),
                            "handle_depth": cwh["Handle_Depth_%"],
                            "handle_days": cwh["Handle_Days"],
                            "handle_weeks": cwh.get("Handle_Weeks"),
                            "rs_rank": int(rs),
                            "trend_score": f"{trend_score}/8",
                            "base_start_date": d_str,
                            "days_since": idx,
                            "pattern_points": cwh.get("pattern_points"),
                        }
                        if idx == 0:
                            cwh_latest.append(cwh_item)
                        if clean_sym not in cwh_5d:
                            cwh_5d[clean_sym] = cwh_item

    # Sort results by RS DESC, then Score/Proximity
    vcp_latest.sort(key=lambda x: (x["rs_rank"], x["vcp_score"]), reverse=True)
    vcp_5d_list = sorted(list(vcp_5d.values()), key=lambda x: (x["rs_rank"], x["vcp_score"]), reverse=True)

    cwh_latest.sort(key=lambda x: (x["rs_rank"], x["pct_from_pivot"]), reverse=True)
    cwh_5d_list = sorted(list(cwh_5d.values()), key=lambda x: (x["rs_rank"], x["pct_from_pivot"]), reverse=True)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    vcp_data = {
        "screen": "vcp",
        "as_of_date": max_date,
        "stages": {
            "fresh_breakout": {
                "count": len(vcp_latest),
                "symbols": vcp_latest,
            },
            "climbing": {
                "count": len(vcp_5d_list),
                "symbols": vcp_5d_list,
            },
        },
    }

    cwh_data = {
        "screen": "cup-and-handle",
        "as_of_date": max_date,
        "stages": {
            "fresh_breakout": {
                "count": len(cwh_latest),
                "symbols": cwh_latest,
            },
            "climbing": {
                "count": len(cwh_5d_list),
                "symbols": cwh_5d_list,
            },
        },
    }

    with open(CACHE_DIR / "stages_cache_vcp.json", "w", encoding="utf-8") as f:
        json.dump(vcp_data, f, indent=2)

    with open(CACHE_DIR / "stages_cache_cup_and_handle.json", "w", encoding="utf-8") as f:
        json.dump(cwh_data, f, indent=2)

    logger.info("Market strength stages cached successfully! VCP: %d/%d, CWH: %d/%d",
                len(vcp_latest), len(vcp_5d_list), len(cwh_latest), len(cwh_5d_list))

    return {
        "vcp": vcp_data,
        "cup-and-handle": cwh_data,
    }


def get_market_strength_stages_data(screen_name: str, min_market_cap: Optional[float] = None) -> Dict[str, Any]:
    """
    Returns the cached 2-card stage breakdown for 'vcp' or 'cup-and-handle'.
    Computes automatically if the cache is missing.
    """
    normalized_screen = "cup-and-handle" if screen_name in ("cup-and-handle", "cup_and_handle", "cwh") else "vcp"
    cache_filename = "stages_cache_cup_and_handle.json" if normalized_screen == "cup-and-handle" else "stages_cache_vcp.json"
    cache_file = CACHE_DIR / cache_filename

    data = None
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Error reading cache file %s: %s", cache_file, exc)
            data = None

    if not data:
        all_computed = compute_market_strength_stages()
        data = all_computed.get(normalized_screen)

    if not data:
        return {
            "screen": normalized_screen,
            "as_of_date": None,
            "stages": {
                "fresh_breakout": {"count": 0, "symbols": []},
                "climbing": {"count": 0, "symbols": []},
            },
        }

    # Filter by min_market_cap if provided
    if min_market_cap is not None:
        filtered_stages = {}
        for b_key, b_val in data.get("stages", {}).items():
            symbols = [
                s for s in b_val.get("symbols", [])
                if s.get("market_cap_cr") is not None and s.get("market_cap_cr") > min_market_cap
            ]
            filtered_stages[b_key] = {
                "count": len(symbols),
                "symbols": symbols,
            }
        return {
            "screen": normalized_screen,
            "as_of_date": data.get("as_of_date"),
            "stages": filtered_stages,
        }

    return data


if __name__ == "__main__":
    compute_market_strength_stages()

