"""
pattern_screens_logic.py
=============================================================================
Complete Mathematical & Algorithmic Logic for StockPro Pattern Screens:
  1. Base Detection Engine (State-Machine Base Tracker)
  2. PCS (Price Compression Setup / VCP)
  3. ATB (All-Time Breakout / Blue Sky Setup / APB)
  4. Multi-Year Breakout (MYB)
  5. IPO Base Screen
  6. Weinstein Stage Classification (Stages 1 to 4)
  7. Relative Strength Ranking (RS Blend & RS Rank)

Author: StockPro Quant Engine
=============================================================================
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


# =============================================================================
# DEFAULT THRESHOLD CONSTANTS
# =============================================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    # Base Detection Defaults
    "BASE_MIN_DROP_PCT": 5.0,            # Min % decline from running peak to trigger base initiation
    "BASE_SWING_LOOKBACK_DAYS": 21,      # Rolling window for swing high detection (approx 1 trading month)

    # PCS (Price Compression Setup / VCP) Defaults
    "PCS_MIN_CONTRACTIONS": 2,           # Min number of tightening waves (e.g. T1, T2, T3)
    "PCS_MAX_PIVOT_DIST_PCT": 6.0,       # Current close must be within 6% of the pivot level (tightened)
    "PCS_MIN_BASE_DAYS": 15,             # Minimum base width: >= 3 trading weeks
    "PCS_MAX_BASE_COUNT": 3,             # Only Base 1-3 are prime setups; 4+ are late-stage

    # VCP 52-Week High Proximity (BananaPatterns alignment: nearHigh <= 30%, aboveLow >= 15%)
    "PCS_MAX_OFF_52W_HIGH_PCT": 30.0,    # Close must be within 30% of 52-week high
    "PCS_MIN_ABOVE_52W_LOW_PCT": 15.0,   # Close must be >= 15% above 52-week low (anti-laggard)

    # VCP Volume Dry-Up (VDU) Thresholds
    "PCS_VDU_BAR_RATIO": 0.60,           # Single bar: Volume < 60% of 20-day avg volume
    "PCS_VDU_CLUSTER_RATIO": 0.75,       # 5-day vol SMA <= 75% of 50-day vol SMA
    "PCS_VDU_ASYMMETRY_RATIO": 0.80,     # Down-day avg vol < 80% of up-day avg vol

    # VCP Volatility: ATR% Compression & BananaPatterns Dials
    "PCS_ATR_COMPRESSION_RATIO": 0.65,   # Current ATR% / ATR%_at_base_start <= 0.65
    "PCS_ATR_PERIOD": 14,                # ATR period in days
    "VCP_COIL_MAX": 0.90,                # ATR tightening ratio: 2nd half ATR% / 1st half ATR% <= 0.90
    "VCP_DRY_MAX": 0.90,                 # Volume dry-up ratio: 2nd half Vol / 1st half Vol <= 0.90
    "VCP_BASE_DEPTH_MAX_PCT": 35.0,      # Maximum base depth <= 35% (BananaPatterns baseDepth <= 35%)

    # VCP v2.0 Two-Tier Hard Gates & Scoring Parameters (BananaPatterns Aligned)
    "VCP_MIN_HISTORY_BARS": 252,          # Minimum required daily bars to evaluate VCP
    "VCP_ZIGZAG_MIN_REVERSAL_PCT": 2.0,   # Floor minimum reversal % for adaptive ZigZag swings
    "VCP_ZIGZAG_ATR_MULT": 1.5,           # ATR%14 multiplier for adaptive ZigZag swings
    "VCP_BASE_TRIGGER_DROP_PCT": 8.0,     # Drop from recent peak required to trigger Base Start
    "VCP_GATE_MIN_WIDTH_DAYS": 15,        # Minimum consolidation trading days (3 trading weeks / baseWk >= 3)
    "VCP_GATE_MAX_WIDTH_DAYS": 1500,      # Extended to 300 weeks (multi-week to multi-year, baseWk <= 300)
    "VCP_GATE_MAX_CONTRACTION_RATIO": 0.75, # Hard limit: each D_k must be <= 0.75 * D_{k-1}
    "VCP_GATE_MAX_PIVOT_DIST_PCT": 8.0,   # Maximum distance below pivot allowed (0 to 8%)
    "VCP_GATE_MIN_52W_LOW_PCT": 15.0,     # Minimum % above 52-week low (BananaPatterns aboveLow >= 15%)
    "VCP_GATE_MAX_52W_HIGH_PCT": 30.0,    # Maximum % below 52-week high (BananaPatterns nearHigh <= 30%)
    "VCP_GATE_MIN_RS_RANK": 70,           # Minimum 12-month Relative Strength percentile (rs >= 70)

    # VCP Scoring Weights (100 Points Total)
    "VCP_WEIGHT_PROGRESSION": 15.0,       # Progression mean ratio D_k / D_{k-1}
    "VCP_WEIGHT_FINAL_DEPTH": 10.0,       # Tightness of final contraction wave
    "VCP_WEIGHT_WAVE_COUNT": 5.0,         # Number of contraction waves
    "VCP_WEIGHT_VOL_COMPRESSION": 15.0,   # Final contraction volume vs 50-day SMA
    "VCP_WEIGHT_QUIETEST_DAY": 5.0,       # 10-bar absolute base volume dry-up
    "VCP_WEIGHT_UP_DOWN_VOL": 5.0,        # Up/Down volume asymmetry across base
    "VCP_WEIGHT_RS_RANK": 10.0,           # Relative strength leadership
    "VCP_WEIGHT_52W_HIGH": 10.0,          # Proximity to 52-week high
    "VCP_WEIGHT_BASE_COUNT": 10.0,        # Early Stage 2 base maturity
    "VCP_WEIGHT_ATR_COMPRESSION": 10.0,   # Normalized 14-day ATR% squeeze
    "VCP_WEIGHT_PIVOT_PROXIMITY": 5.0,    # Tight positioning under pivot

    # VCP Grade Cutoffs
    "VCP_GRADE_A_CUTOFF": 80.0,           # Grade A: Elite Setup (Score >= 80)
    "VCP_GRADE_B_CUTOFF": 65.0,           # Grade B: High Quality (Score 65 - 79)
    "VCP_GRADE_C_CUTOFF": 50.0,           # Grade C: Developing / Speculative (Score 50 - 64)

    # Base Count Increment Thresholds
    "BASE_COUNT_MIN_ADVANCE_PCT": 20.0,  # Stock must rally >=20% after breakout to count a new base

    # ATB (All-Time Breakout / Blue Sky) Defaults (BananaPatterns: lvl=1, rs >= 70, ext in [-20, 0])
    "ATB_BASE_ATH_PROXIMITY_PCT": 5.0,   # Base high must be within 5% of All-Time High
    "ATB_CLOSE_ATH_PROXIMITY_PCT": 20.0, # Current close within 20% of All-Time High (ext: [-20, 0])
    "ATB_MAX_PIVOT_DIST_PCT": 20.0,      # Proximity to pivot: ext in [-20, 0] (up to 20% below pivot)
    "ATB_MIN_RS_RANK": 70,               # Leader benchmark: RS Rank >= 70

    # Multi-Year Breakout (MYB) Defaults (Original Logic)
    "MULTI_YEAR_BASE_MIN_DAYS": 250,     # Minimum length (trading days, ~1 calendar year) for a multi-year base
    "VCP_MAX_PIVOT_DIST_PCT": 20.0,      # Max percent distance from pivot

    # Aged All-Time High (Aged ATH) Defaults
    "AGED_ATH_MIN_AGE_YEARS": 2.0,       # ATH must be older than 2 years
    "AGED_ATH_MAX_DIST_PCT": 20.0,       # Current close within 20% below ATH
    "AGED_ATH_MIN_LISTING_YEARS": 3.0,   # Minimum listing age in years
    "AGED_ATH_PRICE_BASIS": "close",     # "close" or "high"


    # IPO Base Defaults
    "IPO_MIN_WEEKS": 2,                  # Minimum age post-listing
    "IPO_MAX_WEEKS": 50,                 # Maximum age post-listing (< 1 year)
    "IPO_MIN_BASE_DAYS": 21,             # Base duration >= 1 month
    "IPO_MIN_DEPTH_PCT": 2.0,            # Minimum base depth %
    "IPO_MAX_DEPTH_PCT": 35.0,           # Maximum base depth % (cannot be broken/deep)

    # Stage Classification Defaults
    "STAGE3_LOOKBACK_WINDOW": 30,        # Rolling window to evaluate price plateauing
    "STAGE3_MAX_PROGRESS_PCT": 5.0,      # Max absolute % price gain/loss over window to qualify as topping
}


# =============================================================================
# 1. BASE DETECTION ENGINE (State-Machine Tracker)
# =============================================================================

def _compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Compute the Average True Range (ATR) for each bar.
    True Range = max(High - Low, |High - Close_prev|, |Low - Close_prev|)
    ATR = Simple moving average of TR over `period` bars.
    """
    n = len(highs)
    tr = np.zeros(n, dtype=float)
    for i in range(n):
        hl = highs[i] - lows[i]
        if i == 0:
            tr[i] = hl
        else:
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr[i] = max(hl, hc, lc)
    atr = np.full(n, np.nan, dtype=float)
    for i in range(n):
        start = max(0, i - period + 1)
        atr[i] = tr[start : i + 1].mean()
    return atr


def detect_base(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.DataFrame:
    """
    Executes a state-machine scan over historical daily OHLCV bars to detect,
    measure, and track base consolidations.

    Enhanced with full 5-criterion VCP metrics (Minervini / O'Neil):
      1. Wave-by-wave contraction depth tracking & sequential shrinkage validation.
      2. Base count (Stage 2 base numbering) with increment & reset rules.
      3. % off 52-week high & % above 52-week low.
      4. Volume dry-up flag (bar-level, cluster, and asymmetric accumulation).
      5. ATR% compression ratio (volatility reduction since base start).

    Original mechanics preserved:
      - Running Peak / Base Initiation / Base Depth % / Contraction Count / Breakout logic.
    """
    n = len(df)
    closes = df['Close'].values.astype(float)
    highs = df['High'].values.astype(float)
    lows = df['Low'].values.astype(float)
    volumes = df['Volume'].values.astype(float) if 'Volume' in df.columns else np.zeros(n, dtype=float)
    dates = df.index.values

    # ── ATR pre-computation ──────────────────────────────────────────────────
    atr_period = config.get("PCS_ATR_PERIOD", 14)
    atr_values = _compute_atr(highs, lows, closes, period=atr_period)
    # ATR% = ATR / Close * 100
    atr_pct = np.where(closes > 0, atr_values / closes * 100.0, 0.0)

    # ── 52-week rolling high/low (252 trading days) ──────────────────────────
    w52 = 252
    high_52w = np.zeros(n, dtype=float)
    low_52w = np.zeros(n, dtype=float)
    for i in range(n):
        start = max(0, i - w52 + 1)
        high_52w[i] = highs[start : i + 1].max()
        low_52w[i] = lows[start : i + 1].min()

    # ── Volume SMAs (pre-computed for VDU) ──────────────────────────────────
    vol_sma20 = np.zeros(n, dtype=float)
    vol_sma50 = np.zeros(n, dtype=float)
    for i in range(n):
        s20 = max(0, i - 19)
        s50 = max(0, i - 49)
        vol_sma20[i] = volumes[s20 : i + 1].mean()
        vol_sma50[i] = volumes[s50 : i + 1].mean()

    # ── Output arrays ───────────────────────────────────────────────────────
    base_active         = np.zeros(n, dtype=int)
    base_start_date     = np.empty(n, dtype=object)
    base_length_days    = np.zeros(n, dtype=int)
    base_high_arr       = np.zeros(n, dtype=float)
    base_low_arr        = np.zeros(n, dtype=float)
    base_depth_pct      = np.zeros(n, dtype=float)
    pct_from_pivot      = np.zeros(n, dtype=float)
    contraction_count   = np.zeros(n, dtype=int)
    breakout_today      = np.zeros(n, dtype=int)
    last_breakout_level = np.full(n, np.nan, dtype=float)

    # ── VCP v2.0 metric arrays ──────────────────────────────────────────────
    base_count_arr              = np.zeros(n, dtype=int)   # Stage 2 base number
    pct_off_52w_high_arr        = np.zeros(n, dtype=float) # % below 52w high
    pct_above_52w_low_arr       = np.zeros(n, dtype=float) # % above 52w low
    vdu_flag_arr                = np.zeros(n, dtype=int)   # Volume dry-up flag
    atr_compression_ratio_arr   = np.full(n, np.nan, dtype=float)  # ATR% ratio
    last_contraction_depth_arr  = np.zeros(n, dtype=float) # Last swing depth %
    contractions_shrinking_arr  = np.zeros(n, dtype=int)   # 1 if waves sequentially shrink
    base_width_weeks_arr        = np.zeros(n, dtype=float) # trading_days / 5.0
    pivot_price_arr             = np.zeros(n, dtype=float) # resistance high of final wave
    final_contraction_low_arr   = np.zeros(n, dtype=float) # trough low of final wave
    higher_lows_arr             = np.zeros(n, dtype=int)   # 1 if each trough is higher than prior
    contraction_depths_arr      = np.empty(n, dtype=object)# JSON serialized array of depths
    atr_max_base_arr            = np.zeros(n, dtype=float) # max ATR% inside base
    final_wave_vol_ratio_arr    = np.zeros(n, dtype=float) # Mean(Vol_final) / VolSMA50
    up_dn_vol_ratio_arr         = np.zeros(n, dtype=float) # Up/Down volume ratio in base
    prog_mean_ratio_arr         = np.zeros(n, dtype=float) # Mean consecutive depth ratio
    coil_arr                    = np.ones(n, dtype=float)  # BananaPatterns coil (2nd half ATR% / 1st half ATR%)
    dry_arr                     = np.ones(n, dtype=float)  # BananaPatterns dry (2nd half Vol / 1st half Vol)

    # ── State variables ─────────────────────────────────────────────────────
    in_base = False
    cur_base_high = 0.0
    cur_base_start_date = None
    cur_base_low = float('inf')
    cur_contractions = 0
    cur_days_in_base = 0
    cur_last_breakout_level = np.nan

    running_peak = 0.0
    running_peak_idx = 0

    # Base count state
    cur_base_count = 0             # Current Stage 2 base number
    last_breakout_price = np.nan   # Price at which last breakout occurred
    peak_since_breakout = np.nan   # Highest close reached after last breakout
    base_count_pending = False     # Waiting for +20% advance before incrementing

    # Stage 2 tracking for base count resets
    has_sma200 = 'SMA200' in df.columns
    sma200_vals = df['SMA200'].values if has_sma200 else np.zeros(n, dtype=float)

    # Swing tracker for contraction measurement
    leg_high = 0.0
    leg_low = float('inf')
    last_swing_low = float('inf')
    prev_swing_low = float('inf')
    last_swing_high = float('inf')
    leg_high_idx = 0
    trend = 0  # 1 = up-leg, -1 = down-leg

    # Wave depth tracking for sequential shrinkage and higher lows
    swing_depths: list[float] = []
    swing_lows: list[float] = []
    waves_info: list[dict] = []
    cur_swing_leg_high = 0.0
    cur_swing_leg_low = float('inf')

    # ATR at base start
    atr_pct_at_base_start = np.nan

    min_drop_pct = config.get("VCP_BASE_TRIGGER_DROP_PCT", config.get("BASE_MIN_DROP_PCT", 8.0))
    lookback = config.get("BASE_SWING_LOOKBACK_DAYS", 21)
    advance_pct_thresh = config.get("BASE_COUNT_MIN_ADVANCE_PCT", 20.0)
    reversal_min = config.get("VCP_ZIGZAG_MIN_REVERSAL_PCT", 2.0)
    atr_mult = config.get("VCP_ZIGZAG_ATR_MULT", 1.5)
    max_base_days = config.get("VCP_GATE_MAX_WIDTH_DAYS", 65)

    for i in range(n):
        c = closes[i]
        h = highs[i]
        l = lows[i]
        v = volumes[i]
        d = dates[i]

        if i == 0:
            running_peak = h
            running_peak_idx = 0

        # ── 52-week proximity (computed every bar regardless of base state) ──
        h52 = high_52w[i]
        l52 = low_52w[i]
        pct_off_h52 = ((h52 - c) / h52 * 100.0) if h52 > 0 else 0.0
        pct_above_l52 = ((c - l52) / l52 * 100.0) if l52 > 0 else 0.0
        pct_off_52w_high_arr[i] = pct_off_h52
        pct_above_52w_low_arr[i] = pct_above_l52

        # ── Base count: advance tracking ────────────────────────────────────
        if base_count_pending and not np.isnan(last_breakout_price) and last_breakout_price > 0:
            if np.isnan(peak_since_breakout):
                peak_since_breakout = c
            else:
                peak_since_breakout = max(peak_since_breakout, c)
            advance = (peak_since_breakout - last_breakout_price) / last_breakout_price * 100.0
            if advance >= advance_pct_thresh:
                cur_base_count += 1
                base_count_pending = False

        # ── Base count: Stage 4 reset ────────────────────────────────────────
        if has_sma200 and sma200_vals[i] > 0:
            sma200_prev = sma200_vals[max(0, i - 20)]
            sma200_falling = sma200_vals[i] < sma200_prev
            if c < sma200_vals[i] and sma200_falling:
                cur_base_count = 0
                base_count_pending = False
                last_breakout_price = np.nan
                peak_since_breakout = np.nan

        # ─────────────────────────────────────────────────────────────────────
        # Step A: Check Base End (Breakout OR Failure / Expiration)
        # ─────────────────────────────────────────────────────────────────────
        is_breakout = False
        is_failure = False

        if in_base:
            cur_days_in_base = i - running_peak_idx
            
            # Breakout: close clears resistance ceiling / base high
            if c > cur_base_high:
                is_breakout = True
                breakout_today[i] = 1
                cur_last_breakout_level = cur_base_high
                last_breakout_price = cur_base_high
                peak_since_breakout = c
                base_count_pending = True
            elif (cur_days_in_base > max_base_days) or (c < cur_base_low * 0.90) or (((cur_base_high - c) / cur_base_high * 100.0) > 45.0):
                is_failure = True

        # ─────────────────────────────────────────────────────────────────────
        # Step B: Base Initiation Check (if currently un-based)
        # ─────────────────────────────────────────────────────────────────────
        if not in_base or is_failure:
            if is_failure:
                in_base = False
                running_peak = h
                running_peak_idx = i

            if not in_base:
                if h > running_peak:
                    running_peak = h
                    running_peak_idx = i

                start_idx = max(0, i - lookback)
                window_highs = highs[start_idx : i + 1]
                recent_high = window_highs.max()

                # Has price retreated enough from recent high (>= 8.0%)?
                if recent_high > 0 and ((recent_high - c) / recent_high * 100) >= min_drop_pct:
                    in_base = True
                    cur_base_high = running_peak
                    cur_base_start_date = dates[running_peak_idx]
                    cur_days_in_base = i - running_peak_idx
                    cur_base_low = l
                    cur_contractions = 0
                    if cur_base_count == 0:
                        cur_base_count = 1
                    swing_depths = []
                    swing_lows = []
                    waves_info = []
                    cur_swing_leg_high = h
                    cur_swing_leg_low = l

                    # Capture ATR% at base initiation
                    atr_pct_at_base_start = atr_pct[running_peak_idx] if running_peak_idx < n else atr_pct[i]

                    leg_high = cur_base_high
                    leg_low = l
                    leg_high_idx = running_peak_idx
                    last_swing_low = float('inf')
                    prev_swing_low = 0.0
                    last_swing_high = cur_base_high
                    trend = -1

        # ─────────────────────────────────────────────────────────────────────
        # Step C: Base Progression & Contraction Tracking (Adaptive ZigZag)
        # ─────────────────────────────────────────────────────────────────────
        if in_base:
            cur_days_in_base = i - running_peak_idx
            if l < cur_base_low:
                cur_base_low = l

            # Adaptive reversal threshold: max(2.0%, 1.5 * ATR%14)
            rev_pct = max(reversal_min, atr_mult * atr_pct[i])
            reversal = rev_pct / 100.0

            if trend == -1:  # Downward leg
                if l < leg_low:
                    leg_low = l
                # Reversal upwards: close advances by reversal from leg_low
                reversed_up = (leg_low > 0 and (c - leg_low) / leg_low >= reversal)
                if reversed_up:
                    trend = 1
                    # A down-leg contraction has completed at leg_low
                    if not waves_info:
                        # Wave 1: initial base drop from cur_base_high to leg_low
                        wave1_depth = ((cur_base_high - leg_low) / cur_base_high * 100.0) if cur_base_high > 0 else 0.0
                        swing_depths.append(wave1_depth)
                        swing_lows.append(leg_low)
                        waves_info.append({
                            'high': cur_base_high,
                            'low': leg_low,
                            'depth': wave1_depth,
                            'length': max(1, i - running_peak_idx),
                            'start_idx': running_peak_idx
                        })
                        cur_contractions = 1
                    else:
                        # Wave 2, 3, etc.: subsequent pullback from leg_high to leg_low
                        wave_depth = ((leg_high - leg_low) / leg_high * 100.0) if leg_high > 0 else 0.0
                        cur_contractions += 1
                        swing_depths.append(wave_depth)
                        swing_lows.append(leg_low)
                        waves_info.append({
                            'high': leg_high,
                            'low': leg_low,
                            'depth': wave_depth,
                            'length': max(1, i - leg_high_idx),
                            'start_idx': leg_high_idx
                        })
                    last_swing_high = leg_high
                    last_swing_low = leg_low
                    leg_high = h
                    leg_high_idx = i
            elif trend == 1:  # Upward leg
                if h > leg_high:
                    leg_high = h
                    leg_high_idx = i
                # Reversal downwards: close drops by reversal from leg_high
                reversed_down = (leg_high > 0 and (leg_high - c) / leg_high >= reversal)
                if reversed_down:
                    trend = -1
                    leg_low = l

            # ── Contraction Dimensions & Sequential Shrinkage ────────────────
            if waves_info:
                cur_pivot = waves_info[-1]['high']
                cur_final_low = waves_info[-1]['low']
                cur_final_depth = waves_info[-1]['depth']
            else:
                cur_pivot = cur_base_high
                cur_final_low = cur_base_low
                cur_final_depth = ((cur_base_high - cur_base_low) / cur_base_high * 100.0) if cur_base_high > 0 else 0.0

            if len(swing_depths) >= 2:
                # Sequential amplitude contraction: D_k <= 0.75 * D_{k-1}
                shrinking = all(swing_depths[k] <= 0.75 * swing_depths[k - 1] for k in range(1, len(swing_depths)))
                contractions_shrinking = 1 if shrinking else 0
                depth_ratios = [swing_depths[k] / swing_depths[k - 1] for k in range(1, len(swing_depths)) if swing_depths[k - 1] > 0]
                prog_mean_ratio = float(np.mean(depth_ratios)) if depth_ratios else 0.60
            elif len(swing_depths) == 1:
                contractions_shrinking = 1
                prog_mean_ratio = 0.60
            else:
                contractions_shrinking = 0
                prog_mean_ratio = 0.60

            # Higher lows requirement
            if len(swing_lows) >= 2:
                higher_lows = 1 if all(swing_lows[k] > swing_lows[k - 1] for k in range(1, len(swing_lows))) else 0
            else:
                higher_lows = 1

            # ── Volume Dry-Up (VDU) Flag (Blueprint Section 3.2.2) ───────────
            # Quietest day in last 10 bars == absolute lowest of the entire base
            quietest_10 = volumes[max(0, i - 9) : i + 1].min()
            base_min_vol = volumes[running_peak_idx : i + 1].min()
            vdu_flag = 1 if (quietest_10 <= base_min_vol and base_min_vol > 0) else 0

            # ── Final Wave Volume Compression ────────────────────────────────
            if waves_info and 'start_idx' in waves_info[-1]:
                f_start = max(running_peak_idx, waves_info[-1]['start_idx'])
                f_vol = volumes[f_start : i + 1].mean()
            else:
                s_final = max(running_peak_idx, i - 4)
                f_vol = volumes[s_final : i + 1].mean()
            final_vol_ratio = (f_vol / vol_sma50[i]) if (vol_sma50[i] > 0) else 1.0

            # ── Base Volume Asymmetry (Blueprint Section 3.2.3) ──────────────
            base_start_i = running_peak_idx
            base_indices = np.arange(base_start_i, i + 1)
            valid_indices = base_indices[base_indices > 0]
            if len(valid_indices) > 0:
                prev_c = closes[valid_indices - 1]
                cur_c_seg = closes[valid_indices]
                vols_seg = volumes[valid_indices]
                up_vols = vols_seg[cur_c_seg > prev_c]
                dn_vols = vols_seg[cur_c_seg < prev_c]
                sum_up = float(up_vols.sum())
                sum_dn = float(dn_vols.sum())
                if sum_dn == 0:
                    up_dn_ratio = 1.25  # Zero down-close days: pure accumulation awards full 5 pts
                else:
                    up_dn_ratio = sum_up / sum_dn
            else:
                up_dn_ratio = 1.25

            # ── ATR Compression Ratio & ATR Max inside base ──────────────────
            atr_max_base = atr_pct[running_peak_idx : i + 1].max()
            if atr_max_base > 0:
                atr_ratio = atr_pct[i] / atr_max_base
            elif atr_pct_at_base_start > 0 and not np.isnan(atr_pct_at_base_start):
                atr_ratio = atr_pct[i] / atr_pct_at_base_start
            else:
                atr_ratio = np.nan

            # ── Write outputs ────────────────────────────────────────────────
            base_active[i] = 1
            base_start_date[i] = cur_base_start_date
            base_length_days[i] = cur_days_in_base
            base_width_weeks_arr[i] = round(cur_days_in_base / 5.0, 2)
            base_high_arr[i] = cur_base_high
            base_low_arr[i] = cur_base_low
            base_depth_pct[i] = ((cur_base_high - cur_base_low) / cur_base_high) * 100.0 if cur_base_high > 0 else 0.0
            pct_from_pivot[i] = ((cur_pivot - c) / cur_pivot) * 100.0 if cur_pivot > 0 else 0.0
            pivot_price_arr[i] = cur_pivot
            final_contraction_low_arr[i] = cur_final_low
            higher_lows_arr[i] = higher_lows
            contraction_depths_arr[i] = json.dumps([round(float(d), 2) for d in swing_depths])
            contraction_count[i] = cur_contractions
            base_count_arr[i] = cur_base_count
            contractions_shrinking_arr[i] = contractions_shrinking
            last_contraction_depth_arr[i] = cur_final_depth
            vdu_flag_arr[i] = vdu_flag
            atr_compression_ratio_arr[i] = atr_ratio
            atr_max_base_arr[i] = atr_max_base
            final_wave_vol_ratio_arr[i] = final_vol_ratio
            up_dn_vol_ratio_arr[i] = up_dn_ratio
            prog_mean_ratio_arr[i] = prog_mean_ratio

            # ── BananaPatterns Coil (ATR Tightening) & Volume Dry-Up (dry) ───
            if cur_days_in_base >= 6:
                mid_idx = running_peak_idx + cur_days_in_base // 2
                h1_atr = float(np.nanmean(atr_pct[running_peak_idx:mid_idx]))
                h2_atr = float(np.nanmean(atr_pct[mid_idx:i+1]))
                bp_coil = (h2_atr / h1_atr) if h1_atr > 0 else 1.0

                h1_vol = float(np.nansum(volumes[running_peak_idx:mid_idx]))
                h2_vol = float(np.nansum(volumes[mid_idx:i+1]))
                bp_dry = (h2_vol / h1_vol) if h1_vol > 0 else 1.0
            else:
                bp_coil = 1.0
                bp_dry = 1.0
            coil_arr[i] = round(bp_coil, 4)
            dry_arr[i] = round(bp_dry, 4)

            if is_breakout:
                # Reset base machine on successful breakout
                in_base = False
                running_peak = h
                running_peak_idx = i
                cur_base_high = 0.0
                cur_base_low = float('inf')
                cur_contractions = 0
                cur_days_in_base = 0
                swing_depths = []
                swing_lows = []
                waves_info = []
                atr_pct_at_base_start = np.nan
        else:
            base_active[i] = 0
            base_start_date[i] = d
            base_length_days[i] = 0
            base_width_weeks_arr[i] = 0.0
            base_high_arr[i] = 0.0
            base_low_arr[i] = 0.0
            base_depth_pct[i] = 0.0
            pct_from_pivot[i] = 0.0
            pivot_price_arr[i] = 0.0
            final_contraction_low_arr[i] = 0.0
            higher_lows_arr[i] = 0
            contraction_depths_arr[i] = "[]"
            contraction_count[i] = 0
            base_count_arr[i] = cur_base_count
            contractions_shrinking_arr[i] = 0
            last_contraction_depth_arr[i] = 0.0
            vdu_flag_arr[i] = 0
            atr_compression_ratio_arr[i] = np.nan
            atr_max_base_arr[i] = 0.0
            final_wave_vol_ratio_arr[i] = 0.0
            up_dn_vol_ratio_arr[i] = 0.0
            prog_mean_ratio_arr[i] = 0.0
            coil_arr[i] = 1.0
            dry_arr[i] = 1.0

        last_breakout_level[i] = cur_last_breakout_level

    out = df.copy()
    out['base_active']              = base_active
    out['base_start_date']          = pd.to_datetime(base_start_date)
    out['base_length_days']         = base_length_days
    out['base_width_weeks']         = base_width_weeks_arr
    out['base_high']                = base_high_arr
    out['base_low']                 = base_low_arr
    out['base_depth_pct']           = base_depth_pct
    out['pct_from_pivot']           = pct_from_pivot
    out['pivot_price']              = pivot_price_arr
    out['final_contraction_low']    = final_contraction_low_arr
    out['higher_lows']              = higher_lows_arr
    out['contraction_depths']       = contraction_depths_arr
    out['contraction_count']        = contraction_count
    out['breakout_today']           = breakout_today
    out['last_breakout_level']      = last_breakout_level
    out['base_count']               = base_count_arr
    out['pct_off_52w_high']         = pct_off_52w_high_arr
    out['pct_above_52w_low']        = pct_above_52w_low_arr
    out['vdu_flag']                 = vdu_flag_arr
    out['atr_compression_ratio']    = atr_compression_ratio_arr
    out['atr_max_base']             = atr_max_base_arr
    out['final_wave_vol_ratio']     = final_wave_vol_ratio_arr
    out['up_dn_vol_ratio']          = up_dn_vol_ratio_arr
    out['prog_mean_ratio']          = prog_mean_ratio_arr
    out['last_contraction_depth_pct'] = last_contraction_depth_arr
    out['contractions_shrinking']   = contractions_shrinking_arr
    out['coil']                     = coil_arr
    out['dry']                      = dry_arr
    return out


# =============================================================================
# 2. RELATIVE STRENGTH (RS BLEND & RS RANK 1-99)
# =============================================================================

def compute_rs_blend_and_rank(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes William O'Neil / Investor's Business Daily style Relative Strength:
      - RS Blend = 0.40 * 3M_Return + 0.30 * 6M_Return + 0.30 * 12M_Return
        Where:
          3M  = 63 trading days
          6M  = 126 trading days
          12M = 252 trading days
      - RS Rank: Cross-sectional percentile rank across the entire market universe [1 to 99].
    """
    out = df.copy()
    pct_63 = out.groupby('Symbol')['Close'].pct_change(periods=63, fill_method=None)
    pct_126 = out.groupby('Symbol')['Close'].pct_change(periods=126, fill_method=None)
    pct_252 = out.groupby('Symbol')['Close'].pct_change(periods=252, fill_method=None)

    out['rs_blend'] = 0.4 * pct_63 + 0.3 * pct_126 + 0.3 * pct_252

    # Cross-sectional percentile ranking per Timestamp
    rs_rank_calc = (out.groupby('Timestamp')['rs_blend'].rank(pct=True) * 98 + 1).round(0)
    out['rs_rank'] = np.where(out['rs_blend'].isna(), np.nan, rs_rank_calc)
    return out


# =============================================================================
# 3. PCS (PRICE COMPRESSION SETUP / VCP v2.0 TWO-TIER ENGINE)
# =============================================================================

def compute_screen_pcs(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.Series:
    """
    Enhanced VCP Engine (v2.0) — Mark Minervini Volatility Contraction Pattern
    & William O'Neil Base Stage Rules.
    
    Two-Tier Architecture:
      Tier 1: Hard Binary Gates (Eligibility Filter)
        - Gate 1: Trend Template & RS: Close > SMA50 > SMA150 > SMA200, SMA200 rising,
                  pct_above_52w_low >= 30%, pct_off_52w_high <= 25%, rs_rank >= 70.
        - Gate 2: Base Width: 15 <= base_length_days <= 65 (3 to 13 trading weeks).
        - Gate 3: Contraction Progression & Higher Lows: contraction_count >= 2,
                  each D_k <= 0.75 * D_{k-1}, higher_lows == 1.
        - Gate 4: Final Contraction Integrity: Close >= final_contraction_low.
        - Gate 5: Pivot Proximity: 0.0 <= pct_from_pivot <= 6.0%.
      
      Tier 2: 100-Point Quantitative Score
        - 1. Contraction Characteristics (30 pts: progression 15, final depth 10, count 5)
        - 2. Volume Signatures (25 pts: final vol ratio 15, dry-up 5, up/down asymmetry 5)
        - 3. Trend & Momentum Quality (20 pts: RS rank 10, 52w high proximity 10)
        - 4. Base Maturity (10 pts: Stage 2 base number)
        - 5. Volatility Compression (10 pts: ATR% squeeze ratio)
        - 6. Pivot Proximity (5 pts: tightness under pivot)

      Classification & Screening:
        - Grade A: Score >= 80
        - Grade B: Score 65 - 79
        - Grade C: Score 50 - 64
        - screen_vcp = 1 if all gates pass AND Score >= 50.0

      Breakout Flag:
        - breakout_flag = 1 if Close > pivot_prev AND Volume >= 1.5 * VolSMA50 AND passed_gates_prev.
    """
    n = len(df)
    if n == 0:
        return pd.Series([], dtype=int)

    # Process by symbol if 'Symbol' column exists, otherwise as single entity
    has_symbols = 'Symbol' in df.columns
    groups = df.groupby('Symbol', group_keys=False) if has_symbols else [(None, df)]

    screen_vcp_arr = np.zeros(n, dtype=int)
    vcp_score_arr = np.full(n, np.nan, dtype=float)
    vcp_grade_arr = np.empty(n, dtype=object)
    vcp_grade_arr[:] = None
    breakout_flag_arr = np.zeros(n, dtype=int)

    # 11 subcomponent score arrays
    score_progression_arr = np.full(n, np.nan, dtype=float)
    score_final_depth_arr = np.full(n, np.nan, dtype=float)
    score_count_arr = np.full(n, np.nan, dtype=float)
    score_vol_final_arr = np.full(n, np.nan, dtype=float)
    score_quietest_day_arr = np.full(n, np.nan, dtype=float)
    score_up_down_vol_arr = np.full(n, np.nan, dtype=float)
    score_rs_rank_arr = np.full(n, np.nan, dtype=float)
    score_off_52wh_arr = np.full(n, np.nan, dtype=float)
    score_base_count_arr = np.full(n, np.nan, dtype=float)
    score_atr_comp_arr = np.full(n, np.nan, dtype=float)
    score_pivot_prox_arr = np.full(n, np.nan, dtype=float)

    min_history = config.get("VCP_MIN_HISTORY_BARS", 252)

    for sym, group in (groups if has_symbols else [('__single__', df)]):
        idx_locs = df.index.get_indexer(group.index) if has_symbols else np.arange(n)
        gn = len(group)
        if gn == 0:
            continue

        # Data sufficiency check (if history < 252 bars, skip evaluation unless min_history is 0)
        if min_history > 0 and gn < min_history:
            continue

        closes = group['Close'].values.astype(float)
        volumes = group['Volume'].values.astype(float) if 'Volume' in group.columns else np.zeros(gn, dtype=float)
        
        # SMAs
        sma50 = group['SMA50'].values.astype(float) if 'SMA50' in group.columns else pd.Series(closes).rolling(50, min_periods=1).mean().values
        sma200 = group['SMA200'].values.astype(float) if 'SMA200' in group.columns else pd.Series(closes).rolling(200, min_periods=1).mean().values
        sma150 = group['SMA150'].values.astype(float) if 'SMA150' in group.columns else pd.Series(closes).rolling(150, min_periods=1).mean().values
        vol_sma50 = group['VolSMA50'].values.astype(float) if 'VolSMA50' in group.columns else pd.Series(volumes).rolling(50, min_periods=1).mean().values

        # Base & contraction fields
        base_active = group['base_active'].values.astype(int) if 'base_active' in group.columns else np.zeros(gn, dtype=int)
        base_length_days = group['base_length_days'].values.astype(int) if 'base_length_days' in group.columns else np.zeros(gn, dtype=int)
        contraction_count = group['contraction_count'].values.astype(int) if 'contraction_count' in group.columns else np.zeros(gn, dtype=int)
        contractions_shrinking = group['contractions_shrinking'].values.astype(int) if 'contractions_shrinking' in group.columns else np.zeros(gn, dtype=int)
        higher_lows = group['higher_lows'].values.astype(int) if 'higher_lows' in group.columns else np.ones(gn, dtype=int)
        pivot_price = group['pivot_price'].values.astype(float) if 'pivot_price' in group.columns else (group['base_high'].values.astype(float) if 'base_high' in group.columns else closes)
        final_contraction_low = group['final_contraction_low'].values.astype(float) if 'final_contraction_low' in group.columns else (group['base_low'].values.astype(float) if 'base_low' in group.columns else closes)
        pct_from_pivot = group['pct_from_pivot'].values.astype(float) if 'pct_from_pivot' in group.columns else np.zeros(gn, dtype=float)
        last_contraction_depth = group['last_contraction_depth_pct'].values.astype(float) if 'last_contraction_depth_pct' in group.columns else np.zeros(gn, dtype=float)
        
        pct_off_52w_high = group['pct_off_52w_high'].values.astype(float) if 'pct_off_52w_high' in group.columns else np.zeros(gn, dtype=float)
        pct_above_52w_low = group['pct_above_52w_low'].values.astype(float) if 'pct_above_52w_low' in group.columns else np.full(gn, 50.0, dtype=float)
        rs_rank = group['rs_rank'].values.astype(float) if 'rs_rank' in group.columns else np.full(gn, 75.0, dtype=float)
        base_count = group['base_count'].values.astype(int) if 'base_count' in group.columns else np.ones(gn, dtype=int)
        vdu_flag = group['vdu_flag'].values.astype(int) if 'vdu_flag' in group.columns else np.zeros(gn, dtype=int)
        atr_ratio = group['atr_compression_ratio'].values.astype(float) if 'atr_compression_ratio' in group.columns else np.ones(gn, dtype=float)
        final_wave_vol_ratio = group['final_wave_vol_ratio'].values.astype(float) if 'final_wave_vol_ratio' in group.columns else np.ones(gn, dtype=float)
        up_dn_vol_ratio = group['up_dn_vol_ratio'].values.astype(float) if 'up_dn_vol_ratio' in group.columns else np.full(gn, 1.25, dtype=float)
        prog_mean_ratio = group['prog_mean_ratio'].values.astype(float) if 'prog_mean_ratio' in group.columns else np.full(gn, 0.60, dtype=float)

        # Gate threshold constants
        min_width = config.get("VCP_GATE_MIN_WIDTH_DAYS", 15)
        max_width = config.get("VCP_GATE_MAX_WIDTH_DAYS", 65)
        max_pivot_dist = config.get("VCP_GATE_MAX_PIVOT_DIST_PCT", 6.0)
        min_52w_low = config.get("VCP_GATE_MIN_52W_LOW_PCT", 30.0)
        max_52w_high = config.get("VCP_GATE_MAX_52W_HIGH_PCT", 25.0)
        min_rs_rank = config.get("VCP_GATE_MIN_RS_RANK", 70)

        # Track previous bar gate state and pivot
        passed_gates_prev = False
        pivot_prev = np.nan

        for i in range(gn):
            c = closes[i]
            v = volumes[i]
            vsma = vol_sma50[i]

            # ── 1. Breakout Flag (evaluated before gate early returns) ────────
            is_breakout = (
                not np.isnan(pivot_prev) and
                pivot_prev > 0 and
                c > pivot_prev and
                vsma > 0 and
                v >= 1.50 * vsma and
                passed_gates_prev
            )
            g_idx = idx_locs[i]
            breakout_flag_arr[g_idx] = 1 if is_breakout else 0

            # ── 2. Tier 1: Hard Binary Gates (All 5 must pass) ───────────────
            # Gate 1: Trend Template & RS Rank (BananaPatterns: vs50 >= 0, vs200 >= 0, nearHigh <= 30, aboveLow >= 15, rs >= 70)
            gate_1_trend = (
                (c >= sma50[i]) and (c >= sma200[i]) and
                (pct_above_52w_low[i] >= min_52w_low) and
                (pct_off_52w_high[i] <= max_52w_high) and
                (np.isnan(rs_rank[i]) or rs_rank[i] >= min_rs_rank)
            )

            # Gate 2: Base Width & Base Depth (BananaPatterns: baseWk >= 3 weeks, baseDepth <= 35%)
            b_depth = group['base_depth_pct'].values[i] if 'base_depth_pct' in group.columns else 0.0
            gate_2_width = (
                (base_active[i] == 1) and
                (min_width <= base_length_days[i] <= max_width) and
                (b_depth <= config.get("VCP_BASE_DEPTH_MAX_PCT", 35.0))
            )

            # Gate 3: Contraction Progression & Higher Lows (Minervini waves OR BananaPatterns coil & dry)
            b_coil = group['coil'].values[i] if 'coil' in group.columns else 1.0
            b_dry = group['dry'].values[i] if 'dry' in group.columns else 1.0
            gate_3_contractions = (
                (
                    (contraction_count[i] >= config.get("PCS_MIN_CONTRACTIONS", 2)) and
                    (contractions_shrinking[i] == 1) and
                    (higher_lows[i] == 1)
                ) or (
                    b_coil <= config.get("VCP_COIL_MAX", 0.90) and
                    b_dry <= config.get("VCP_DRY_MAX", 0.90)
                )
            )

            # Gate 4: Final Contraction Integrity
            gate_4_low_integrity = (c >= final_contraction_low[i])

            # Gate 5: Pivot Proximity
            gate_5_pivot = (0.0 <= pct_from_pivot[i] <= max_pivot_dist)

            passed_all_gates = (
                gate_1_trend and
                gate_2_width and
                gate_3_contractions and
                gate_4_low_integrity and
                gate_5_pivot
            )

            # Update prev bar trackers for next iteration
            passed_gates_prev = bool(passed_all_gates)
            pivot_prev = pivot_price[i] if pivot_price[i] > 0 else np.nan

            if not passed_all_gates:
                continue

            # ── 3. Tier 2: 100-Point Scoring Model ────────────────────────────
            # 3.1 Contractions (30 pts)
            pmr = prog_mean_ratio[i] if not np.isnan(prog_mean_ratio[i]) else 0.60
            s_progression = 15.0 * float(np.clip((0.75 - pmr) / (0.75 - 0.50), 0.0, 1.0))
            
            f_depth = last_contraction_depth[i] if not np.isnan(last_contraction_depth[i]) else 5.0
            s_final_depth = 10.0 * float(np.clip((10.0 - f_depth) / (10.0 - 4.0), 0.0, 1.0))

            cnt = contraction_count[i]
            s_count = 5.0 if cnt in (3, 4) else (3.0 if cnt == 2 else (2.0 if cnt in (5, 6) else 0.0))

            # 3.2 Volume Signatures (25 pts)
            v_ratio = final_wave_vol_ratio[i] if not np.isnan(final_wave_vol_ratio[i]) else 1.0
            s_vol_final = 15.0 * float(np.clip((1.00 - v_ratio) / (1.00 - 0.60), 0.0, 1.0))

            s_quietest_day = 5.0 if vdu_flag[i] == 1 else 0.0

            ud_ratio = up_dn_vol_ratio[i] if not np.isnan(up_dn_vol_ratio[i]) else 1.0
            s_up_down_vol = 5.0 * float(np.clip((ud_ratio - 1.00) / (1.25 - 1.00), 0.0, 1.0))

            # 3.3 Trend Quality (20 pts)
            r_rank = rs_rank[i] if not np.isnan(rs_rank[i]) else 70.0
            s_rs_rank = 10.0 * float(np.clip((r_rank - 70.0) / (95.0 - 70.0), 0.0, 1.0))

            off_52w = pct_off_52w_high[i] if not np.isnan(pct_off_52w_high[i]) else 15.0
            s_off_52wh = 10.0 * float(np.clip((25.0 - off_52w) / (25.0 - 5.0), 0.0, 1.0))

            # 3.4 Base Maturity (10 pts)
            b_map = {1: 10.0, 2: 8.0, 3: 5.0, 4: 2.0}
            s_base_count = b_map.get(base_count[i], 0.0)

            # 3.5 Volatility Compression (10 pts)
            atrr = atr_ratio[i] if not np.isnan(atr_ratio[i]) else 1.0
            s_atr_comp = 10.0 * float(np.clip((0.90 - atrr) / (0.90 - 0.50), 0.0, 1.0))

            # 3.6 Pivot Proximity (5 pts)
            p_dist = pct_from_pivot[i] if not np.isnan(pct_from_pivot[i]) else 3.0
            s_pivot_prox = 5.0 * float(np.clip((6.0 - p_dist) / (6.0 - 2.0), 0.0, 1.0))

            # Total Score & Grade
            total_score = round(
                s_progression + s_final_depth + s_count + s_vol_final +
                s_quietest_day + s_up_down_vol + s_rs_rank + s_off_52wh +
                s_base_count + s_atr_comp + s_pivot_prox, 1
            )

            grade = "A" if total_score >= config.get("VCP_GRADE_A_CUTOFF", 80.0) else (
                "B" if total_score >= config.get("VCP_GRADE_B_CUTOFF", 65.0) else (
                    "C" if total_score >= config.get("VCP_GRADE_C_CUTOFF", 50.0) else "Fail"
                )
            )

            screen_vcp_arr[g_idx] = 1 if total_score >= config.get("VCP_GRADE_C_CUTOFF", 50.0) else 0
            vcp_score_arr[g_idx] = total_score
            vcp_grade_arr[g_idx] = grade if total_score >= 50.0 else None

            # Subcomponent scores
            score_progression_arr[g_idx] = round(s_progression, 1)
            score_final_depth_arr[g_idx] = round(s_final_depth, 1)
            score_count_arr[g_idx] = round(s_count, 1)
            score_vol_final_arr[g_idx] = round(s_vol_final, 1)
            score_quietest_day_arr[g_idx] = round(s_quietest_day, 1)
            score_up_down_vol_arr[g_idx] = round(s_up_down_vol, 1)
            score_rs_rank_arr[g_idx] = round(s_rs_rank, 1)
            score_off_52wh_arr[g_idx] = round(s_off_52wh, 1)
            score_base_count_arr[g_idx] = round(s_base_count, 1)
            score_atr_compression_arr = score_atr_comp_arr
            score_atr_comp_arr[g_idx] = round(s_atr_comp, 1)
            score_pivot_prox_arr[g_idx] = round(s_pivot_prox, 1)

    # Attach all computed features and scores to the input DataFrame
    df['vcp_score'] = vcp_score_arr
    df['vcp_grade'] = vcp_grade_arr
    df['breakout_flag'] = breakout_flag_arr
    df['score_progression'] = score_progression_arr
    df['score_final_depth'] = score_final_depth_arr
    df['score_contraction_count'] = score_count_arr
    df['score_vol_final'] = score_vol_final_arr
    df['score_quietest_day'] = score_quietest_day_arr
    df['score_up_down_vol'] = score_up_down_vol_arr
    df['score_rs_rank'] = score_rs_rank_arr
    df['score_off_52wh'] = score_off_52wh_arr
    df['score_base_count'] = score_base_count_arr
    df['score_atr_compression'] = score_atr_comp_arr
    df['score_pivot_proximity'] = score_pivot_prox_arr

    return pd.Series(screen_vcp_arr, index=df.index, dtype=int)


# =============================================================================
# 4. ATB (ALL-TIME BREAKOUT / BLUE SKY SETUP / APB)
# =============================================================================

def compute_screen_atb(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.Series:
    """
    ATB (All-Time Breakout / Blue Sky Setup):
    Aligned with BananaPatterns preset: ["Blue sky", { lvl: [1, 1], rs: [70, 99], ext: [-20, 0] }]
      1. Base Consolidation active: base_active == 1
      2. Base High at All-Time High level: base_high >= ATH * (1.0 - ATB_BASE_ATH_PROXIMITY_PCT / 100)
      3. Close within 20% of ATH: Close >= ATH * (1.0 - ATB_CLOSE_ATH_PROXIMITY_PCT / 100)
      4. Pivot Proximity: pct_from_pivot <= 20.0% (ext in [-20, 0])
      5. Market Leadership Quality: RS Rank between 70 and 99
    """
    ath = df['Close'].cummax() if 'Close' in df.columns else df['High'].cummax()
    max_piv = config.get("ATB_MAX_PIVOT_DIST_PCT", 20.0)
    min_rs = config.get("ATB_MIN_RS_RANK", 70)
    cond = (
        (df['base_active'] == 1) &
        (df['base_high'] >= ath * (1.0 - config["ATB_BASE_ATH_PROXIMITY_PCT"] / 100.0)) &
        (df['Close'] >= ath * (1.0 - config["ATB_CLOSE_ATH_PROXIMITY_PCT"] / 100.0)) &
        (df['pct_from_pivot'] <= max_piv) &
        (df['rs_rank'].fillna(min_rs).between(min_rs, 99))
    )
    return cond.astype(int)


# =============================================================================
# 5. MULTI-YEAR BREAKOUT (MYB)
# =============================================================================

def compute_screen_multi_year_breakout(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.Series:
    """
    Multi-Year Breakout (MYB):
    1. Pivot = highest close in preceding 250+ trading days excluding current bar.
    2. Forming: close <= pivot and within 20% below it (-20% <= pct_from_pivot <= 0%).
    3. Fresh Breakout: close crossed above pivot in last 1-5 days.
    4. Climbing: crossed above pivot in last 60 days, close > SMA50 and close > SMA200.
    5. Pre-conditions: close > SMA200, rs_rank >= 60.
    """
    from myb_state_machine_v2 import compute_myb_stage_v2
    res = compute_myb_stage_v2(df, config)
    return res["screen_multi_year_breakout"]


# Multi-Year Breakout State Machine logic has been moved to myb_state_machine_v2.py
from myb_state_machine_v2 import compute_myb_stage_v2 as compute_myb_stage



# =============================================================================
# 6. IPO BASE SCREEN
# =============================================================================

def compute_screen_ipo_base(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.Series:
    """
    IPO Base Screen:
    
    Logic & Conditions:
      1. Listing Horizon:
         - weeks_since_listing between 2 and 50 weeks (young, newly listed stock)
      2. Base Duration:
         - base_length_days >= 21 days (consolidating for at least 1 month)
      3. Constructive Base Depth:
         - base_depth_pct between 2.0% and 35.0% (orderly, institutional accumulation)
      4. Intermediate Moving Average:
         - Close > 50-day SMA
      5. Pivot Proximity:
         - pct_from_pivot <= 8.0%
    """
    cond = (
        (df['weeks_since_listing'].between(config["IPO_MIN_WEEKS"], config["IPO_MAX_WEEKS"])) &
        (df['base_length_days'] >= config["IPO_MIN_BASE_DAYS"]) &
        (df['base_depth_pct'].between(config["IPO_MIN_DEPTH_PCT"], config["IPO_MAX_DEPTH_PCT"])) &
        (df['Close'] > df['SMA50']) &
        (df['pct_from_pivot'] <= 8.0)
    )
    return cond.astype(int)


# =============================================================================
# 6B. AGED ALL-TIME HIGH (AGED ATH)
# =============================================================================

def compute_screen_aged_ath(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.Series:
    """
    Aged All-Time High Screen:
      1. All-time high was established > AGED_ATH_MIN_AGE_YEARS (default 2 years) ago.
      2. Current price is within AGED_ATH_MAX_DIST_PCT (default 20%) below that ATH.
      3. Stock listing age >= AGED_ATH_MIN_LISTING_YEARS (default 3 years).
    """
    min_ath_age_yr = float(config.get("AGED_ATH_MIN_AGE_YEARS", 2.0))
    max_dist_pct = float(config.get("AGED_ATH_MAX_DIST_PCT", 20.0))
    min_listing_yr = float(config.get("AGED_ATH_MIN_LISTING_YEARS", 3.0))
    basis_choice = str(config.get("AGED_ATH_PRICE_BASIS", "close")).lower()
    basis_col = "High" if basis_choice == "high" and "High" in df.columns else "Close"

    if basis_col not in df.columns or "Close" not in df.columns or len(df) == 0:
        return pd.Series(0, index=df.index, dtype=int)

    # Resolve date series
    if "Timestamp" in df.columns:
        dates = pd.to_datetime(df["Timestamp"])
    elif isinstance(df.index, pd.DatetimeIndex):
        dates = pd.Series(df.index, index=df.index)
    else:
        return pd.Series(0, index=df.index, dtype=int)

    # 1. Listing age (years)
    listing_age_years = (dates - dates.iloc[0]).dt.total_seconds() / (365.25 * 86400)

    # 2. Cumulative All-Time High & Date of ATH
    cum_ath = df[basis_col].cummax()
    ath_date = dates.where(df[basis_col] == cum_ath).ffill()
    ath_age_years = (dates - ath_date).dt.total_seconds() / (365.25 * 86400)

    # 3. Distance % below ATH
    distance_pct = ((cum_ath - df["Close"]) / cum_ath) * 100.0

    # 4. Qualification condition
    cond = (
        (listing_age_years >= min_listing_yr) &
        (ath_age_years >= min_ath_age_yr) &
        (distance_pct <= max_dist_pct) &
        (distance_pct >= -10.0)
    )
    return cond.astype(int)


# =============================================================================
# 7. WEINSTEIN STAGE CLASSIFICATION (STAGES 1 - 4)
# =============================================================================

def classify_stages(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.DataFrame:
    """
    Stan Weinstein Stage Analysis:
      - Stage 1: Base / Accumulation (flat 200 SMA, oscillating price)
      - Stage 2: Advancing / Uptrend (Close > 200 SMA, 200 SMA sloping upwards)
      - Stage 3: Topping / Distribution (volatility spike, flattened progress, losing 200 SMA)
      - Stage 4: Declining / Downtrend (Close < 200 SMA, 200 SMA sloping downwards)
      
    Actionable Buckets:
      - 'forming': Stage 1 + active base
      - 'breakout': Stage 2 + fresh base breakout bar
      - 'extended': Stage 2 + Close > 20% above 50 SMA
      - 'pullback': Stage 2 + Close testing 10/20 EMA or 50 SMA
      - 'distribution': Stage 3
      - 'declining': Stage 4
    """
    out = df.copy()

    # Slope of 200 SMA over 20 sessions (approx 1 trading month)
    sma200_shift20 = out.groupby('Symbol')['SMA200'].shift(20)
    sma200_rising = out['SMA200'] > sma200_shift20
    sma200_falling = out['SMA200'] < sma200_shift20

    # Stage 2: Advancing Uptrend
    stage_2_cond = (out['Close'] > out['SMA200']) & sma200_rising

    # Stage 4: Declining Downtrend
    stage_4_cond = (out['Close'] < out['SMA200']) & sma200_falling

    # Stage 3: Topping / Distribution
    window = config["STAGE3_LOOKBACK_WINDOW"]
    past_close = out.groupby('Symbol')['Close'].shift(window)
    net_progress = ((out['Close'] - past_close) / past_close).abs() * 100.0

    stage_3_cond = (
        (out['Close'] >= out['SMA200'] * 0.90) &
        (~sma200_rising) & (~sma200_falling) &
        (net_progress <= config["STAGE3_MAX_PROGRESS_PCT"])
    )

    # Mutually exclusive priority assignment
    cond_list = [stage_2_cond, stage_3_cond, stage_4_cond]
    choice_list = [2, 3, 4]
    out['stage'] = np.select(cond_list, choice_list, default=1)

    # Stage status buckets aligned with BananaPatterns (forming, fresh_breakout, climbing)
    stage_bucket = pd.Series("unknown", index=out.index)

    # Recent breakout check (within last 5 sessions)
    if 'Symbol' in out.columns and len(out.groupby('Symbol')) > 1:
        recent_breakout = out.groupby('Symbol')['breakout_today'].rolling(5, min_periods=1).max().reset_index(level=0, drop=True) == 1
    else:
        recent_breakout = out['breakout_today'].rolling(5, min_periods=1).max() == 1

    # 1. forming -> Base active and price coiling/contracting under pivot ceiling
    stage_bucket.loc[(out['base_active'] == 1) & (out['pct_from_pivot'] >= -2.0) & (~recent_breakout)] = "forming"

    # 2. fresh_breakout -> Broke out past pivot within the last 5 trading days (independent of base_active)
    stage_bucket.loc[recent_breakout] = "fresh_breakout"

    # 3. climbing -> Advancing in Stage 2 (or broke out past pivot and holding above SMA50 and SMA200)
    is_climbing = (
        (~recent_breakout) &
        (out['Close'] >= out['SMA50']) &
        (
            (out['stage'] == 2) |
            ((out['Close'] >= out['SMA200']) & (out['pct_from_pivot'] <= 0.0))
        )
    )
    stage_bucket.loc[is_climbing] = "climbing"

    out['stage_bucket'] = stage_bucket
    return out


# =============================================================================
# 8. MASTER PIPELINE RUNNER
# =============================================================================

def run_all_pattern_screens(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_CONFIG) -> pd.DataFrame:
    """
    Runs the complete sequence of indicator calculations, base tracking,
    relative strength ranking, pattern screens, and stage classification.
    """
    # 1. Base Detection
    processed_symbols = []
    for symbol, group in df.groupby('Symbol'):
        # Moving Averages
        group = group.copy()
        group['SMA50'] = group['Close'].rolling(window=50, min_periods=1).mean()
        group['SMA150'] = group['Close'].rolling(window=150, min_periods=1).mean()
        group['SMA200'] = group['Close'].rolling(window=200, min_periods=1).mean()
        if 'Volume' in group.columns:
            group['VolSMA50'] = group['Volume'].rolling(window=50, min_periods=1).mean()
        group['all_time_high'] = group['Close'].cummax()

        # Listing Age
        first_date = group.index[0]
        group['weeks_since_listing'] = (group.index - first_date).days // 7

        # Base Detection State Machine
        group = detect_base(group, config)
        processed_symbols.append(group)

    res = pd.concat(processed_symbols)

    # 2. RS Rank calculation
    res = compute_rs_blend_and_rank(res)

    # 3. Pattern Screens
    res['screen_vcp'] = compute_screen_pcs(res, config)                  # PCS (Price Compression Setup)
    res['screen_blue_sky'] = compute_screen_atb(res, config)             # ATB (All-Time Breakout)
    res['screen_multi_year_breakout'] = compute_screen_multi_year_breakout(res, config) # MYB
    res['screen_ipo_base'] = compute_screen_ipo_base(res, config)        # IPO Base

    # 4. Stage Classification
    res = classify_stages(res, config)

    return res
