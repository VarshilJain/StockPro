"""
myb_state_machine_v2.py
=============================================================================
Multi-Year Breakout (MYB) Detection Engine & State Logic.

Architecture:
1. Computes P[t] = max(close[t-W : t]) excluding bar t, and base_age[t] = t - (last index where close == P[t]).
2. Identifies qualified breakout events in the last MAX_AGE bars (close[t] > P[t] and close[t-1] <= P[t]).
3. Freezes the pivot at the breakout bar: frozen_pivot = P[event_bar].
4. For all subsequent bars, computes metrics strictly from the frozen pivot (no pivot drift).
5. Classifies stages: Fresh Breakout, Climbing, Failed, Extended, Forming.
6. Trend filter: Close > SMA200 and SMA200 >= SMA200[t-20] * 0.995 both on event bar and today.
=============================================================================
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


@dataclass
class MYBConfig:
    W: int = 1008                         # Lookback window (default 1008 bars ≈ 4 calendar years)
    MIN_BASE_DAYS: int = 250              # Minimum base age (default 250 trading days ≈ 1 calendar year)
    MAX_AGE: int = 25                     # Search last MAX_AGE bars for events (default 25 bars)
    FRESH_MAX_DAYS: int = 5               # Fresh breakout window: days 1 to 5
    FAIL_PCT: float = -3.0                # Pullback below -3.0% from frozen pivot marks Failed
    FRESH_MAX_PCT: float = 10.0           # Maximum gain for Fresh Breakout (above is Extended)
    CLIMB_MAX_PCT: float = 20.0           # Maximum gain for Climbing (above is Extended)
    FORMING_MIN_PCT: float = -10.0        # Forming range: -10.0% to 0.0%
    REQUIRE_TREND_FILTER: bool = True     # Close > SMA200 and SMA200 >= SMA200[t-20] * 0.995

    @property
    def min_base_days(self) -> int:
        return self.MIN_BASE_DAYS

    @property
    def climbing_days(self) -> int:
        return self.MAX_AGE

    @property
    def fresh_breakout_days(self) -> int:
        return self.FRESH_MAX_DAYS


MYB_CONFIG_DEFAULT = MYBConfig()


def find_event(
    close: pd.Series,
    sma200: Optional[pd.Series] = None,
    config: Optional[MYBConfig] = None
) -> Optional[Dict[str, Any]]:
    """
    Core MYB Detection function.
    Finds and classifies multi-year breakout events with a frozen pivot.
    
    Returns dict with keys:
      - event_bar: index/timestamp of breakout bar (or None if forming)
      - frozen_pivot: price ceiling established at breakout (or live ceiling if forming)
      - base_age_at_event: trading days the stock spent below ceiling prior to breakout
      - days_since: bars elapsed since event bar (where event bar is day 1)
      - pct_from_pivot: percentage distance from frozen pivot
      - stage: 'Fresh Breakout', 'Climbing', 'Failed', 'Extended', 'Forming', or None
      - failed_date: date when price breached FAIL_PCT (if stage == 'Failed')
      - reattempt: True if an earlier breakout occurred in the last MAX_AGE window
    """
    cfg = config or MYB_CONFIG_DEFAULT
    n = len(close)
    min_required_bars = cfg.MIN_BASE_DAYS + 200

    if n < min_required_bars or close.empty:
        return None

    # Handle Series with NaNs or clean values
    vals = close.to_numpy(dtype=float, copy=False)

    if sma200 is not None:
        sma = sma200.to_numpy(dtype=float, copy=False)
    else:
        # Compute 200 SMA directly from close
        sma = close.rolling(200, min_periods=200).mean().to_numpy(dtype=float, copy=False)

    # Pre-calculate rolling ceiling P[t] = max(close[t-W : t]) excluding bar t
    P_series = close.shift(1).rolling(window=cfg.W, min_periods=cfg.MIN_BASE_DAYS).max().to_numpy(dtype=float, copy=False)

    def get_base_age(t: int, p_val: float) -> int:
        """Finds base age: t - (last index where close == p_val) in [max(0, t-W) : t]."""
        w_start = max(0, t - cfg.W)
        w = vals[w_start:t]
        for offset in range(len(w) - 1, -1, -1):
            if np.isclose(w[offset], p_val, atol=1e-5):
                return len(w) - offset
        return 0

    def check_trend(t: int) -> bool:
        """Rule 7: Close > SMA200 and SMA200 >= SMA200[t-20] * 0.995."""
        if not cfg.REQUIRE_TREND_FILTER:
            return True
        if t < 20 or np.isnan(sma[t]) or np.isnan(sma[t - 20]) or np.isnan(vals[t]):
            # If not enough SMA bars at this index, fall back to valid
            return True
        c_ok = vals[t] > sma[t]
        slope_ok = sma[t] >= (sma[t - 20] * 0.995)
        return bool(c_ok and slope_ok)

    # Search the last MAX_AGE bars for breakout events
    start_search = max(min_required_bars, n - cfg.MAX_AGE)
    event_bars = []
    active_pivot = None
    active_b_age = 0

    for t in range(start_search, n):
        p_t = P_series[t]
        if np.isnan(p_t):
            continue

        is_direct_bo = False
        # Breakout event 1: Direct cross of multi-year base ceiling
        if vals[t] > p_t and vals[t - 1] <= p_t:
            b_age = get_base_age(t, p_t)
            if b_age >= cfg.MIN_BASE_DAYS and check_trend(t):
                event_bars.append((t, p_t, b_age))
                active_pivot = p_t
                active_b_age = b_age
                is_direct_bo = True

        # Breakout event 2: Reattempt cross above active frozen pivot after a pullback
        if not is_direct_bo and active_pivot is not None:
            if vals[t] > active_pivot and vals[t - 1] <= active_pivot and check_trend(t):
                event_bars.append((t, active_pivot, active_b_age))

    today_idx = n - 1
    today_trend_ok = check_trend(today_idx)

    # If breakout event(s) found in the last MAX_AGE bars
    if event_bars:
        # Step 3: Use the MOST RECENT event
        reattempt = len(event_bars) > 1
        ev_t, frozen_pivot, b_age = event_bars[-1]

        # Step 4: Days since event (event bar is day 1)
        days_since = (today_idx - ev_t) + 1
        c_today = vals[today_idx]
        pct_from_pivot = round((c_today - frozen_pivot) / frozen_pivot * 100.0, 2)

        # Step 5: Check if pct < FAIL_PCT (-3%) at ANY point after the event
        post_closes = vals[ev_t : today_idx + 1]
        post_pcts = (post_closes - frozen_pivot) / frozen_pivot * 100.0
        failed_mask = post_pcts < cfg.FAIL_PCT

        if np.any(failed_mask):
            failed_offset = int(np.where(failed_mask)[0][0])
            fail_idx = ev_t + failed_offset
            fail_date = str(close.index[fail_idx]) if hasattr(close, "index") else str(fail_idx)
            return {
                "event_bar": close.index[ev_t] if hasattr(close, "index") else ev_t,
                "frozen_pivot": round(float(frozen_pivot), 2),
                "base_age_at_event": int(b_age),
                "days_since": int(days_since),
                "pct_from_pivot": float(pct_from_pivot),
                "stage": "Failed",
                "failed_date": fail_date,
                "reattempt": reattempt,
            }

        # If trend filter fails today, the setup is invalid
        if not today_trend_ok:
            return None

        # Stage classification from frozen pivot
        if 1 <= days_since <= cfg.FRESH_MAX_DAYS:
            if 0.0 <= pct_from_pivot <= cfg.FRESH_MAX_PCT:
                stage = "Fresh Breakout"
            elif pct_from_pivot > cfg.FRESH_MAX_PCT:
                stage = "Extended"
            else:
                stage = "Fresh Breakout"  # Holding shallow retest >= -3% on days 1-5
        elif cfg.FRESH_MAX_DAYS < days_since <= cfg.MAX_AGE:
            if cfg.FAIL_PCT <= pct_from_pivot <= cfg.CLIMB_MAX_PCT:
                stage = "Climbing"
            elif pct_from_pivot > cfg.CLIMB_MAX_PCT:
                stage = "Extended"
            else:
                stage = "Failed"
        else:
            stage = None

        return {
            "event_bar": close.index[ev_t] if hasattr(close, "index") else ev_t,
            "frozen_pivot": round(float(frozen_pivot), 2),
            "base_age_at_event": int(b_age),
            "days_since": int(days_since),
            "pct_from_pivot": float(pct_from_pivot),
            "stage": stage,
            "failed_date": None,
            "reattempt": reattempt,
        }

    # Step 6: Forming check (no event in the last MAX_AGE bars)
    p_today = P_series[today_idx]
    if np.isnan(p_today):
        return None

    b_age_today = get_base_age(today_idx, p_today)
    c_today = vals[today_idx]
    pct_today = round((c_today - p_today) / p_today * 100.0, 2)

    if b_age_today >= cfg.MIN_BASE_DAYS and (cfg.FORMING_MIN_PCT <= pct_today <= 0.0) and today_trend_ok:
        return {
            "event_bar": None,
            "frozen_pivot": round(float(p_today), 2),
            "base_age_at_event": int(b_age_today),
            "days_since": None,
            "pct_from_pivot": float(pct_today),
            "stage": "Forming",
            "failed_date": None,
            "reattempt": False,
        }

    return None


def detect_myb(
    df: pd.DataFrame,
    rs_rank: Optional[float] = None,
    config: Optional[MYBConfig | Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Wrapper around find_event for DataFrame input with backward compatibility.
    """
    if config is None:
        cfg = MYB_CONFIG_DEFAULT
    elif isinstance(config, dict):
        cfg = MYBConfig(
            W=config.get("W", 1008),
            MIN_BASE_DAYS=config.get("MIN_BASE_DAYS", config.get("min_base_days", 250)),
            MAX_AGE=config.get("MAX_AGE", config.get("climbing_days", 25)),
            FRESH_MAX_DAYS=config.get("FRESH_MAX_DAYS", config.get("fresh_breakout_days", 5)),
            FAIL_PCT=config.get("FAIL_PCT", -3.0),
            FRESH_MAX_PCT=config.get("FRESH_MAX_PCT", 10.0),
            CLIMB_MAX_PCT=config.get("CLIMB_MAX_PCT", 20.0),
            FORMING_MIN_PCT=config.get("FORMING_MIN_PCT", config.get("forming_min_pct", -10.0)),
            REQUIRE_TREND_FILTER=config.get("REQUIRE_TREND_FILTER", True),
        )
    else:
        cfg = config

    if df is None or len(df) < cfg.MIN_BASE_DAYS + 200:
        return {
            "pivot": None,
            "base_start": None,
            "base_length_days": 0,
            "base_depth": None,
            "pct_from_pivot": None,
            "stage": "invalid",
            "breakout_date": None,
            "failed_date": None,
            "reattempt": False,
        }

    close_series = df["Close"]
    sma200_series = df["SMA200"] if "SMA200" in df.columns else None

    # Evaluate using find_event
    res = find_event(close_series, sma200=sma200_series, config=cfg)

    if res is None or res.get("stage") is None:
        return {
            "pivot": None,
            "base_start": None,
            "base_length_days": 0,
            "base_depth": None,
            "pct_from_pivot": None,
            "stage": "invalid",
            "breakout_date": None,
            "failed_date": None,
            "reattempt": False,
        }

    raw_stage = res["stage"]
    stage_slug = raw_stage.lower().replace(" ", "_")

    return {
        "pivot": res["frozen_pivot"],
        "base_start": str(res["event_bar"]) if res["event_bar"] else None,
        "base_length_days": res["base_age_at_event"],
        "base_depth": None,
        "pct_from_pivot": res["pct_from_pivot"],
        "stage": stage_slug,
        "breakout_date": str(res["event_bar"]) if res["event_bar"] else None,
        "failed_date": res.get("failed_date"),
        "reattempt": res.get("reattempt", False),
        "days_since": res.get("days_since"),
    }


def compute_myb_stage_v2(
    df: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    eval_bars: int = 1
) -> pd.DataFrame:
    """
    Vectorized & symbol-level evaluation of Multi-Year Breakout across bars.
    Adds/updates:
      - myb_pivot
      - myb_pct_from_pivot
      - myb_stage
      - screen_multi_year_breakout
      - myb_reattempt
      - myb_failed_date
      - myb_base_length_days
      - myb_base_width_weeks
      - myb_bo_date
      - myb_age_bars
    """
    res_df = df.copy()
    n = len(df)
    cfg_obj = MYB_CONFIG_DEFAULT
    if config:
        cfg_obj = MYBConfig(
            W=config.get("W", 1008),
            MIN_BASE_DAYS=config.get("MIN_BASE_DAYS", config.get("min_base_days", 250)),
            MAX_AGE=config.get("MAX_AGE", config.get("climbing_days", 25)),
            FRESH_MAX_DAYS=config.get("FRESH_MAX_DAYS", config.get("fresh_breakout_days", 5)),
            FAIL_PCT=config.get("FAIL_PCT", -3.0),
            FRESH_MAX_PCT=config.get("FRESH_MAX_PCT", 10.0),
            CLIMB_MAX_PCT=config.get("CLIMB_MAX_PCT", 20.0),
            FORMING_MIN_PCT=config.get("FORMING_MIN_PCT", config.get("forming_min_pct", -10.0)),
            REQUIRE_TREND_FILTER=config.get("REQUIRE_TREND_FILTER", True),
        )

    res_df["myb_stage"] = "invalid"
    res_df["screen_multi_year_breakout"] = 0
    res_df["myb_reattempt"] = 0
    res_df["myb_failed_date"] = None
    res_df["myb_base_length_days"] = np.nan
    res_df["myb_base_width_weeks"] = np.nan
    res_df["myb_bo_date"] = None
    res_df["myb_age_bars"] = None

    min_required = cfg_obj.MIN_BASE_DAYS + 200
    if n < min_required:
        res_df["myb_pivot"] = np.nan
        res_df["myb_pct_from_pivot"] = np.nan
        return res_df

    # Fast rolling ceiling across all bars
    res_df["myb_pivot"] = res_df["Close"].shift(1).rolling(window=cfg_obj.W, min_periods=cfg_obj.MIN_BASE_DAYS).max()
    res_df["myb_pct_from_pivot"] = (res_df["Close"] - res_df["myb_pivot"]) / res_df["myb_pivot"] * 100.0

    sma200_s = res_df["SMA200"] if "SMA200" in res_df.columns else None

    # Evaluate find_event for the requested tail bars
    num_eval = max(1, min(eval_bars, n - min_required + 1))
    start_bar = n - num_eval

    for i in range(start_bar, n):
        idx_label = res_df.index[i]
        sub_close = res_df["Close"].iloc[: i + 1]
        sub_sma = sma200_s.iloc[: i + 1] if sma200_s is not None else None
        ev = find_event(sub_close, sma200=sub_sma, config=cfg_obj)

        if ev and ev.get("stage"):
            stage_slug = ev["stage"].lower().replace(" ", "_")
            res_df.loc[idx_label, "myb_pivot"] = ev["frozen_pivot"]
            res_df.loc[idx_label, "myb_pct_from_pivot"] = ev["pct_from_pivot"]
            res_df.loc[idx_label, "myb_stage"] = stage_slug
            is_active = 1 if stage_slug in ("fresh_breakout", "climbing", "forming", "extended") else 0
            res_df.loc[idx_label, "screen_multi_year_breakout"] = is_active
            res_df.loc[idx_label, "myb_reattempt"] = 1 if ev.get("reattempt") else 0
            res_df.loc[idx_label, "myb_failed_date"] = ev.get("failed_date")
            b_age = ev.get("base_age_at_event")
            res_df.loc[idx_label, "myb_base_length_days"] = b_age
            res_df.loc[idx_label, "myb_base_width_weeks"] = round(b_age / 5.0, 1) if b_age else np.nan
            res_df.loc[idx_label, "myb_bo_date"] = str(ev.get("event_bar")) if ev.get("event_bar") else None
            res_df.loc[idx_label, "myb_age_bars"] = ev.get("days_since")
        else:
            res_df.loc[idx_label, "myb_stage"] = "invalid"
            res_df.loc[idx_label, "screen_multi_year_breakout"] = 0
            res_df.loc[idx_label, "myb_reattempt"] = 0
            res_df.loc[idx_label, "myb_failed_date"] = None

    return res_df
