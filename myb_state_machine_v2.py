"""
myb_state_machine_v2.py
=============================================================================
Multi-Year Breakout (MYB) Detection Engine & State Logic.

Rules:
1. Pivot = highest close in the base window (last 250+ trading days, excluding current bar).
   No close inside the base may be above the pivot (pivot is ceiling by definition).
   When a breakout has occurred, the pivot is the ceiling that was crossed.
2. pct_from_pivot = (Close - pivot) / pivot * 100, strictly signed.
3. Stages (mutually exclusive):
   - Forming: close <= pivot (-20% <= pct_from_pivot <= 0%) with no active breakout.
   - Fresh Breakout: crossed above pivot within the last 1-5 days.
   - Climbing: crossed above pivot within last 60 days, close > SMA50 and close > SMA200.
   - Invalid / None: anything else (not shown in MYB list).
4. Pre-conditions: base >= 250 days, close > SMA200, rs_rank >= 60.
=============================================================================
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


@dataclass
class MYBConfig:
    min_base_days: int = 250
    forming_min_pct: float = -10.0          # Strict Option 2: Strictly >= -10.0% below ceiling pivot
    fresh_breakout_days: int = 5
    climbing_days: int = 60
    min_rs_rank: float = 60.0
    require_stage2: bool = True            # Strict Option 2: Close > SMA50 and SMA50 > SMA200


MYB_CONFIG_DEFAULT = MYBConfig()


def detect_myb(
    df: pd.DataFrame,
    rs_rank: Optional[float] = None,
    config: Optional[MYBConfig | Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Detects Multi-Year Breakout (MYB) stage and metrics for a given symbol DataFrame.
    Evaluates up to the latest bar with zero lookahead bias.
    """
    if config is None:
        cfg = MYB_CONFIG_DEFAULT
    elif isinstance(config, dict):
        cfg = MYBConfig(
            min_base_days=config.get("min_base_days", 250),
            forming_min_pct=config.get("forming_min_pct", -10.0),
            fresh_breakout_days=config.get("fresh_breakout_days", 5),
            climbing_days=config.get("climbing_days", 60),
            min_rs_rank=config.get("min_rs_rank", 60.0),
            require_stage2=config.get("require_stage2", True),
        )
    else:
        cfg = config

    min_base_days = cfg.min_base_days
    forming_min_pct = cfg.forming_min_pct
    fresh_days = cfg.fresh_breakout_days
    climb_days = cfg.climbing_days
    min_rs = cfg.min_rs_rank
    req_stage2 = cfg.require_stage2

    if df is None or len(df) < min_base_days + 1:
        return {
            "pivot": None,
            "base_start": None,
            "base_length_days": 0,
            "base_depth": None,
            "pct_from_pivot": None,
            "stage": "invalid",
            "breakout_date": None,
        }

    # Rolling ceiling: max close in preceding min_base_days excluding that bar
    rolling_ceil = df["Close"].shift(1).rolling(window=min_base_days, min_periods=min_base_days).max()

    # Breakout cross: close crossed strictly above rolling_ceiling
    crosses = (df["Close"] > rolling_ceil) & (df["Close"].shift(1) <= rolling_ceil.shift(1))

    curr = df.iloc[-1]
    c_curr = float(curr["Close"])
    s200 = float(curr["SMA200"]) if "SMA200" in curr and pd.notna(curr["SMA200"]) else None
    s50 = float(curr["SMA50"]) if "SMA50" in curr and pd.notna(curr["SMA50"]) else None

    if rs_rank is None:
        rs_rank = float(curr["rs_rank"]) if "rs_rank" in curr and pd.notna(curr["rs_rank"]) else 70.0

    precond_ok = (s200 is not None and c_curr > s200) and (rs_rank >= min_rs)

    # Option 2 Strict: Close > SMA50 and SMA50 > SMA200
    stage2_ok = True
    if req_stage2:
        stage2_ok = (s50 is not None and c_curr > s50) and (s200 is not None and s50 > s200)

    # Check for breakouts in the last climb_days (up to 60 bars)
    recent_bo_series = crosses.iloc[-climb_days:]
    has_recent_bo = bool(recent_bo_series.any())

    if has_recent_bo:
        # Most recent breakout
        bo_indices = recent_bo_series[recent_bo_series].index
        bo_idx = bo_indices[-1]
        bo_bar = df.loc[bo_idx]
        breakout_date = str(bo_bar.get("Timestamp", bo_bar.name)).split()[0]
        # The pivot is the ceiling that was crossed at the breakout bar
        pivot = float(rolling_ceil.loc[bo_idx])
        days_since_bo = len(df) - 1 - df.index.get_loc(bo_idx)

        pct_from_pivot = round((c_curr - pivot) / pivot * 100.0, 2)

        if precond_ok and days_since_bo < fresh_days:
            stage = "fresh_breakout"
        elif precond_ok and days_since_bo < climb_days and (s50 is not None and c_curr > s50) and (s200 is not None and c_curr > s200):
            stage = "climbing"
        else:
            stage = "invalid"
    else:
        # Forming state (no breakout in last climb_days)
        pivot = float(rolling_ceil.iloc[-1])
        pct_from_pivot = round((c_curr - pivot) / pivot * 100.0, 2)
        breakout_date = None

        if precond_ok and (forming_min_pct <= pct_from_pivot <= 0.0) and stage2_ok:
            stage = "forming"
        else:
            stage = "invalid"

    window = df.iloc[-(min_base_days + 1):-1]
    w_high = float(window["High"].max()) if "High" in window else pivot
    w_low = float(window["Low"].min()) if "Low" in window else float(window["Close"].min())
    base_depth = (w_high - w_low) / w_high * 100.0 if w_high > 0 else 0.0
    base_start = str(window.iloc[0].get("Timestamp", window.index[0]))

    return {
        "pivot": round(pivot, 2),
        "base_start": base_start,
        "base_length_days": min_base_days,
        "base_depth": round(base_depth, 2),
        "pct_from_pivot": pct_from_pivot,
        "stage": stage,
        "breakout_date": breakout_date,
    }


def compute_myb_stage_v2(df: pd.DataFrame, config: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """
    Vectorized computation of Multi-Year Breakout (MYB) across all bars of a symbol DataFrame.
    Adds/updates columns:
      - myb_pivot
      - myb_pct_from_pivot
      - myb_stage
      - screen_multi_year_breakout
    """
    cfg = config or {}
    min_base_days = cfg.get("min_base_days", 250)
    forming_min_pct = cfg.get("forming_min_pct", -10.0)
    fresh_days = cfg.get("fresh_breakout_days", 5)
    climb_days = cfg.get("climbing_days", 60)
    min_rs = cfg.get("min_rs_rank", 60.0)
    req_stage2 = cfg.get("require_stage2", True)

    n = len(df)
    res_df = df.copy()

    if n < min_base_days + 1:
        res_df["myb_pivot"] = np.nan
        res_df["myb_pct_from_pivot"] = np.nan
        res_df["myb_stage"] = "invalid"
        res_df["screen_multi_year_breakout"] = 0
        return res_df

    # Rolling ceiling: max close in preceding min_base_days excluding that bar
    res_df["myb_pivot"] = res_df["Close"].shift(1).rolling(window=min_base_days, min_periods=min_base_days).max()
    res_df["myb_pct_from_pivot"] = (res_df["Close"] - res_df["myb_pivot"]) / res_df["myb_pivot"] * 100.0

    # Cross occurs when Close crosses strictly above myb_pivot
    cross = (res_df["Close"] > res_df["myb_pivot"]) & (res_df["Close"].shift(1) <= res_df["myb_pivot"].shift(1))
    cross_recent_5 = cross.rolling(window=fresh_days, min_periods=1).max() == 1
    cross_recent_60 = cross.rolling(window=climb_days, min_periods=1).max() == 1

    precond_ok = (res_df["Close"] > res_df["SMA200"]) & (res_df["rs_rank"].fillna(70) >= min_rs)

    # Option 2 Strict: Close > SMA50 and SMA50 > SMA200
    if req_stage2:
        stage2_ok = (res_df["Close"] > res_df["SMA50"]) & (res_df["SMA50"] > res_df["SMA200"])
    else:
        stage2_ok = True

    is_fresh = precond_ok & cross_recent_5
    is_climbing = precond_ok & cross_recent_60 & (~is_fresh) & (res_df["Close"] > res_df["SMA50"]) & (res_df["Close"] > res_df["SMA200"])
    is_forming = precond_ok & (res_df["myb_pct_from_pivot"] >= forming_min_pct) & (res_df["myb_pct_from_pivot"] <= 0.0) & (~cross_recent_60) & stage2_ok

    cond_list = [is_fresh, is_climbing, is_forming]
    choice_list = ["fresh_breakout", "climbing", "forming"]
    res_df["myb_stage"] = np.select(cond_list, choice_list, default="invalid")

    is_active_myb = is_fresh | is_climbing | is_forming
    res_df["screen_multi_year_breakout"] = np.where(is_active_myb, 1, 0)

    return res_df
