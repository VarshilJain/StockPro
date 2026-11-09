"""
current_project_vcp_logic.py — Preserved Logic of Original StockPro VCP Engine
================================================================================
This file preserves the exact VCP (Volatility Contraction Pattern / PCS) engine
logic as originally implemented in StockPro's `update_vcp_data.py` and
`pattern_screens_logic.py`.

Original Logic Summary:
1. Base Detection Engine (detect_base):
   - State machine tracking base building from OHLCV data.
   - High peak detected, drop >= 5% initiates base.
   - Tracks swing highs and lows, contractions (T1, T2, T3...), base depth, length.
   - Breakout confirmed when Close > base_high.

2. PCS Quantitative Rules (compute_screen_pcs):
   - Trend template: Close > SMA50 and SMA50 > SMA200.
   - Active consolidation: base_active == 1.
   - Contraction count >= 2.
   - Pivot proximity: pct_from_pivot <= 8.0%.
   - Volume dry-up (VDU): recent volume < 0.70 * SMA(Volume, 20).

3. VCP v2.0 Two-Tier 100-Point Scoring System:
   - Evaluates 11 sub-components:
     * Progression (15 pts): each contraction shallower than previous
     * Final Depth (15 pts): tightest contraction <= 3.0% (optimal)
     * Contraction Count (10 pts): 2 to 4 contractions
     * Volume on Final Contraction (10 pts): volume reduction during coil
     * Quietest Volume Day (10 pts): extreme low volume day in final contraction
     * Up/Down Volume Ratio (10 pts): accumulation vs distribution in base
     * RS Rank (10 pts): RS Rank >= 85+
     * Off 52-Week High (5 pts): within 15% of 52-week high
     * Base Count (5 pts): Base #1 or #2 preferred
     * ATR Compression (5 pts): ATR20 contraction
     * Proximity to Pivot (5 pts): within 3-5% of pivot

4. Multi-Stage Classification (from update_vcp_data.py):
   - Forming: base_active, base_depth <= 35%, length >= 15, coil <= 0.90, dry <= 0.90, rs >= 70
   - Fresh Breakout: breakout past pivot within last 5 trading days & rs >= 70
   - Climbing: Stage 2 advancing above 50 SMA & rs >= 70
   - Consolidated into screen_vcp = 1.
"""

import math
import json
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional

# Default configuration from pattern_screens_logic.py
DEFAULT_VCP_CONFIG = {
    "min_base_length": 15,
    "max_base_length": 300,
    "min_base_depth_pct": 5.0,
    "max_base_depth_pct": 35.0,
    "max_pct_from_pivot": 8.0,
    "min_contractions": 2,
    "min_rs_rank": 70,
    "vdu_volume_factor": 0.70,
    "volume_ma_period": 20,
    "atr_compression_threshold": 0.85,
    "coil_threshold": 0.90,
    "dry_threshold": 0.90,
}


def sanitize_val(val):
    """Convert numpy types, NaN, and Inf to Python native or None for MySQL."""
    if val is None:
        return None
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, (np.bool_, bool)):
        return int(val)
    if pd.isna(val):
        return None
    return val


def compute_vcp_logic_on_dataframe(df: pd.DataFrame, config: Dict[str, Any] = DEFAULT_VCP_CONFIG) -> pd.DataFrame:
    """
    Applies the full original StockPro VCP processing pipeline on a symbol's DataFrame.
    Imports from pattern_screens_logic.py.
    """
    from pattern_screens_logic import (
        detect_base,
        compute_screen_pcs,
        compute_screen_atb,
        compute_screen_multi_year_breakout,
        compute_screen_ipo_base,
        classify_stages,
    )

    df = df.copy()
    for num_col in ['Open', 'High', 'Low', 'Close', 'Volume', 'SMA50', 'SMA200', 'rs_rank', 'weeks_since_listing']:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors='coerce')

    if 'weeks_since_listing' not in df.columns or df['weeks_since_listing'].isna().all():
        first_ts = pd.to_datetime(df['Timestamp'].iloc[0])
        df['weeks_since_listing'] = (pd.to_datetime(df['Timestamp']) - first_ts).dt.days // 7

    # Run base detection & pattern screening
    df = detect_base(df, config)
    df['screen_vcp'] = compute_screen_pcs(df, config)
    df['screen_blue_sky'] = compute_screen_atb(df, config)
    df['screen_ipo_base'] = compute_screen_ipo_base(df, config)
    df['screen_multi_year_breakout'] = compute_screen_multi_year_breakout(df, config)
    df = classify_stages(df, config)

    # 1. Ensure screen_vcp = 1 for all valid VCP setups across the 3 stages:
    # Forming: base_active, base_depth <= 35%, length >= 15, coil <= 0.90, dry <= 0.90, rs >= 70
    is_vcp_forming = (
        (df['stage_bucket'] == 'forming') &
        (df['base_depth_pct'] <= 35.0) &
        (df['base_length_days'] >= 15) &
        (df['coil'] <= 0.90) &
        (df['dry'] <= 0.90) &
        (df['rs_rank'].fillna(70) >= 70)
    )
    # Fresh breakout: breakout past pivot within last 5 trading days & rs >= 70
    is_vcp_fresh_bo = (df['stage_bucket'] == 'fresh_breakout') & (df['rs_rank'].fillna(70) >= 70)
    # Climbing: Stage 2 advancing above 50 SMA & rs >= 70
    is_vcp_climbing = (df['stage_bucket'] == 'climbing') & (df['rs_rank'].fillna(70) >= 70)

    df['screen_vcp'] = np.where(
        (df['screen_vcp'] == 1) | is_vcp_forming | is_vcp_fresh_bo | is_vcp_climbing,
        1, 0
    )

    return df

