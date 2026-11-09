import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

from pattern_screens_logic import compute_myb_stage, DEFAULT_CONFIG
from database import get_connection

TEST_CONFIG = {
    **DEFAULT_CONFIG,
    "MYB_CEILING_BARS": 10,
    "MYB_VOL_AVG_BARS": 10,
    "MYB_FRESH_BARS": 3,
    "MYB_CLIMB_BARS": 8,
    "MYB_COOLDOWN_BARS": 12,
    "MYB_MIN_TRADED_VALUE_INR": 0,
}

def build_base_frame(n_bars: int) -> pd.DataFrame:
    base_date = datetime(2025, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(n_bars)]
    
    close = np.full(n_bars, 99.0)
    if n_bars > 29:
        close[29] = 100.0
        
    sma50 = np.array([90.0 + 0.1 * i for i in range(n_bars)])
    sma200 = np.array([80.0 + 0.1 * i for i in range(n_bars)])
    
    df = pd.DataFrame({
        "Timestamp": dates,
        "Close": close,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Volume": np.full(n_bars, 1000.0),
        "rs_rank": np.full(n_bars, 80.0),
        "SMA50": sma50,
        "SMA200": sma200,
    })
    return df

def test_scenario_a():
    df = build_base_frame(51)
    
    overrides = {
        30: {"Close": 96.0, "High": 97.0, "Low": 95.0, "Volume": 1000.0},
        31: {"Close": 90.0, "High": 91.0, "Low": 89.0, "Volume": 1000.0},
        32: {"Close": 84.0, "High": 85.0, "Low": 83.0, "Volume": 1000.0},
        33: {"Close": 97.0, "High": 98.0, "Low": 96.0, "Volume": 1000.0},
        34: {"Close": 99.0, "High": 100.0, "Low": 98.0, "Volume": 1000.0},
        35: {"Close": 99.0, "High": 108.0, "Low": 98.0, "Volume": 3000.0},
        36: {"Close": 101.0, "High": 102.0, "Low": 99.5, "Volume": 1000.0},
        37: {"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0},
        38: {"Close": 108.0, "High": 109.0, "Low": 105.0, "Volume": 3000.0},
        39: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        40: {"Close": 110.0, "High": 111.0, "Low": 106.0, "Volume": 5000.0},
        41: {"Close": 111.0, "High": 112.0, "Low": 110.0, "Volume": 1000.0},
        42: {"Close": 112.0, "High": 113.0, "Low": 111.0, "Volume": 1000.0},
        43: {"Close": 110.0, "High": 111.0, "Low": 109.0, "Volume": 1000.0},
        44: {"Close": 109.0, "High": 110.0, "Low": 108.0, "Volume": 1000.0},
        45: {"Close": 108.0, "High": 109.0, "Low": 107.0, "Volume": 1000.0},
        46: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        47: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        48: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        49: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        50: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
    }
    for idx, vals in overrides.items():
        for k, v in vals.items():
            df.at[idx, k] = v
            
    res = compute_myb_stage(df, TEST_CONFIG)
    
    expected = {
        30: ("forming", 100.0, 4.000),
        31: ("watch", 100.0, 10.000),
        32: ("", 100.0, 16.000),
        33: ("forming", 100.0, 3.000),
        34: ("forming", 100.0, 1.000),
        35: ("forming", 100.0, 1.000),
        36: ("", 100.0, -1.000),
        37: ("fresh", 101.0, -4.950),
        38: ("fresh", 101.0, -6.931),
        39: ("fresh", 101.0, -5.941),
        40: ("fresh", 101.0, -8.911),
        41: ("climbing", 101.0, -9.901),
        42: ("climbing", 101.0, -10.891),
        43: ("climbing", 101.0, -8.911),
        44: ("climbing", 101.0, -7.921),
        45: ("climbing", 101.0, -6.931),
        46: ("", 112.0, 4.464),
        47: ("", 112.0, 4.464),
        48: ("", 112.0, 4.464),
        49: ("", 112.0, 4.464),
        50: ("forming", 112.0, 4.464),
    }
    
    for idx, (exp_stage, exp_lvl, exp_dist) in expected.items():
        act_stage = res.at[idx, "myb_stage"]
        act_lvl = res.at[idx, "myb_level"]
        act_dist = res.at[idx, "myb_dist_pct"]
        assert act_stage == exp_stage, f"Row {idx} stage mismatch: expected {exp_stage}, got {act_stage}"
        assert abs(act_lvl - exp_lvl) < 1e-3, f"Row {idx} level mismatch: expected {exp_lvl}, got {act_lvl}"
        assert abs(act_dist - exp_dist) < 1e-3, f"Row {idx} dist mismatch: expected {exp_dist}, got {act_dist}"
        
    for idx in range(37, 46):
        assert res.at[idx, "myb_bo_date"] == df.at[37, "Timestamp"], f"Row {idx} bo_date mismatch"
        assert res.at[idx, "myb_age_bars"] == idx - 37, f"Row {idx} age_bars mismatch"

def test_scenario_b():
    df = build_base_frame(44)
    overrides_a = {
        30: {"Close": 96.0, "High": 97.0, "Low": 95.0, "Volume": 1000.0},
        31: {"Close": 90.0, "High": 91.0, "Low": 89.0, "Volume": 1000.0},
        32: {"Close": 84.0, "High": 85.0, "Low": 83.0, "Volume": 1000.0},
        33: {"Close": 97.0, "High": 98.0, "Low": 96.0, "Volume": 1000.0},
        34: {"Close": 99.0, "High": 100.0, "Low": 98.0, "Volume": 1000.0},
        35: {"Close": 99.0, "High": 108.0, "Low": 98.0, "Volume": 3000.0},
        36: {"Close": 101.0, "High": 102.0, "Low": 99.5, "Volume": 1000.0},
        37: {"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0},
        38: {"Close": 100.0, "High": 101.0, "Low": 99.0, "Volume": 1000.0},
        39: {"Close": 97.0, "High": 98.0, "Low": 96.0, "Volume": 1000.0},
        40: {"Close": 102.0, "High": 103.0, "Low": 101.0, "Volume": 1000.0},
        41: {"Close": 103.0, "High": 104.0, "Low": 102.0, "Volume": 1000.0},
        42: {"Close": 108.0, "High": 109.0, "Low": 105.0, "Volume": 6000.0},
        43: {"Close": 109.0, "High": 110.0, "Low": 108.0, "Volume": 1000.0},
    }
    for idx, vals in overrides_a.items():
        for k, v in vals.items():
            df.at[idx, k] = v
            
    res = compute_myb_stage(df, TEST_CONFIG)
    
    expected = {
        38: ("fresh", 101.0, 0.990),
        39: ("failed", 101.0, 3.960),
        40: ("failed", 101.0, -0.990),
        41: ("failed", 101.0, -1.980),
        42: ("fresh", 106.0, -1.887),
        43: ("fresh", 106.0, -2.830),
    }
    for idx, (exp_stage, exp_lvl, exp_dist) in expected.items():
        act_stage = res.at[idx, "myb_stage"]
        act_lvl = res.at[idx, "myb_level"]
        act_dist = res.at[idx, "myb_dist_pct"]
        assert act_stage == exp_stage, f"Row {idx} stage mismatch: expected {exp_stage}, got {act_stage}"
        assert abs(act_lvl - exp_lvl) < 1e-3, f"Row {idx} level mismatch: expected {exp_lvl}, got {act_lvl}"
        assert abs(act_dist - exp_dist) < 1e-3, f"Row {idx} dist mismatch: expected {exp_dist}, got {act_dist}"

    stages = res["myb_stage"].tolist()
    for i in range(1, len(stages)):
        assert not (stages[i-1] == "failed" and stages[i] == "climbing"), f"Invalid transition failed -> climbing at {i}"

def test_scenario_c():
    base_overrides = {
        30: {"Close": 96.0, "High": 97.0, "Low": 95.0, "Volume": 1000.0},
        31: {"Close": 90.0, "High": 91.0, "Low": 89.0, "Volume": 1000.0},
        32: {"Close": 84.0, "High": 85.0, "Low": 83.0, "Volume": 1000.0},
        33: {"Close": 97.0, "High": 98.0, "Low": 96.0, "Volume": 1000.0},
        34: {"Close": 99.0, "High": 100.0, "Low": 98.0, "Volume": 1000.0},
        35: {"Close": 99.0, "High": 108.0, "Low": 98.0, "Volume": 3000.0},
        36: {"Close": 101.0, "High": 102.0, "Low": 99.5, "Volume": 1000.0},
    }
    
    variants = {
        "C1": ({"Close": 106.0, "High": 110.0, "Low": 102.0, "Volume": 4000.0, "rs_rank": 80.0}, "", None),
        "C2": ({"Close": 106.0, "High": 106.0, "Low": 106.0, "Volume": 4000.0, "rs_rank": 80.0}, "", None),
        "C3": ({"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0, "rs_rank": 69.0}, "", None),
        "C4": ({"Close": 101.4, "High": 102.4, "Low": 100.4, "Volume": 4000.0, "rs_rank": 80.0}, "", None),
        "C5": ({"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 1799.0, "rs_rank": 80.0}, "", None),
        "C6": ({"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0, "rs_rank": 70.0}, "fresh", 101.0),
        "C7": ({"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0, "rs_rank": 100.0}, "fresh", 101.0),
        "C8": ({"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 1800.0, "rs_rank": 80.0}, "fresh", 101.0),
    }
    
    for name, (var_37, exp_stage, exp_lvl) in variants.items():
        df = build_base_frame(38)
        for idx, vals in base_overrides.items():
            for k, v in vals.items():
                df.at[idx, k] = v
        for k, v in var_37.items():
            df.at[37, k] = v
            
        res = compute_myb_stage(df, TEST_CONFIG)
        act_stage = res.at[37, "myb_stage"]
        assert act_stage == exp_stage, f"{name} stage mismatch: expected {exp_stage}, got {act_stage}"
        if exp_lvl is not None:
            act_lvl = res.at[37, "myb_level"]
            assert abs(act_lvl - exp_lvl) < 1e-3, f"{name} level mismatch: expected {exp_lvl}, got {act_lvl}"

def test_scenario_d():
    df = build_base_frame(51)
    overrides_a = {
        30: {"Close": 96.0, "High": 97.0, "Low": 95.0, "Volume": 1000.0},
        31: {"Close": 90.0, "High": 91.0, "Low": 89.0, "Volume": 1000.0},
        32: {"Close": 84.0, "High": 85.0, "Low": 83.0, "Volume": 1000.0},
        33: {"Close": 97.0, "High": 98.0, "Low": 96.0, "Volume": 1000.0},
        34: {"Close": 99.0, "High": 100.0, "Low": 98.0, "Volume": 1000.0},
        35: {"Close": 99.0, "High": 108.0, "Low": 98.0, "Volume": 3000.0},
        36: {"Close": 101.0, "High": 102.0, "Low": 99.5, "Volume": 1000.0},
        37: {"Close": 106.0, "High": 107.0, "Low": 102.0, "Volume": 4000.0},
        38: {"Close": 108.0, "High": 109.0, "Low": 105.0, "Volume": 3000.0},
        39: {"Close": 107.0, "High": 108.0, "Low": 106.0, "Volume": 1000.0},
        40: {"Close": 110.0, "High": 111.0, "Low": 106.0, "Volume": 5000.0},
        41: {"Close": 111.0, "High": 112.0, "Low": 110.0, "Volume": 1000.0},
        42: {"Close": 112.0, "High": 113.0, "Low": 111.0, "Volume": 1000.0, "rs_rank": 65.0},
        43: {"Close": 110.0, "High": 111.0, "Low": 109.0, "Volume": 1000.0, "rs_rank": 55.0},
        44: {"Close": 109.0, "High": 110.0, "Low": 108.0, "Volume": 1000.0, "rs_rank": 80.0},
        45: {"Close": 108.0, "High": 109.0, "Low": 107.0, "Volume": 1000.0},
    }
    for idx, vals in overrides_a.items():
        for k, v in vals.items():
            df.at[idx, k] = v
            
    res = compute_myb_stage(df, TEST_CONFIG)
    
    assert res.at[41, "myb_stage"] == "climbing"
    assert res.at[42, "myb_stage"] == "climbing"
    assert res.at[43, "myb_stage"] == ""
    assert abs(res.at[43, "myb_level"] - 101.0) < 1e-3
    assert abs(res.at[43, "myb_dist_pct"] - (-8.911)) < 1e-3
    assert res.at[44, "myb_stage"] == "climbing"
    assert abs(res.at[44, "myb_level"] - 101.0) < 1e-3
    assert res.at[44, "myb_bo_date"] == df.at[37, "Timestamp"]

def test_scenario_e():
    df = build_base_frame(10)
    res = compute_myb_stage(df, TEST_CONFIG)
    for i in range(len(res)):
        assert res.at[i, "myb_stage"] == ""
        assert pd.isna(res.at[i, "myb_level"])
        assert pd.isna(res.at[i, "myb_dist_pct"])

def reference_myb_stage(df_in: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rows = df_in.to_dict("records")
    N = len(rows)
    
    c_bars = cfg.get("MYB_CEILING_BARS", 365)
    v_bars = cfg.get("MYB_VOL_AVG_BARS", 50)
    bo_buf = cfg.get("MYB_BREAKOUT_BUFFER_PCT", 0.5)
    fail_buf = cfg.get("MYB_FAIL_BUFFER_PCT", 3.0)
    w_dist = cfg.get("MYB_WATCH_DIST_PCT", 15.0)
    f_dist = cfg.get("MYB_FORMING_DIST_PCT", 8.0)
    fresh_bars = cfg.get("MYB_FRESH_BARS", 5)
    climb_bars = cfg.get("MYB_CLIMB_BARS", 60)
    cd_bars = cfg.get("MYB_COOLDOWN_BARS", 120)
    max_ext = cfg.get("MYB_MAX_EXTENSION_PCT", 15.0)
    ent_rs = cfg.get("MYB_ENTRY_MIN_RS", 70.0)
    hold_rs = cfg.get("MYB_HOLD_MIN_RS", 60.0)
    max_depth = cfg.get("MYB_MAX_RANGE_DEPTH_PCT", 40.0)
    vol_mult = cfg.get("MYB_BREAKOUT_VOL_MULT", 1.5)
    min_cloc = cfg.get("MYB_MIN_CLOSE_LOC", 0.60)
    min_tv = cfg.get("MYB_MIN_TRADED_VALUE_INR", 0.0)
    
    out_stage = []
    out_level = []
    out_dist = []
    out_bo_date = []
    out_age = []
    out_flag = []
    
    state = "NONE"
    bo_idx = -1
    bo_level = None
    fail_idx = -1
    last_event_idx = -1
    
    for i in range(N):
        r = rows[i]
        c = float(r["Close"]) if r.get("Close") is not None and not pd.isna(r["Close"]) else None
        h = float(r["High"]) if r.get("High") is not None and not pd.isna(r["High"]) else None
        l = float(r["Low"]) if r.get("Low") is not None and not pd.isna(r["Low"]) else None
        v = float(r["Volume"]) if r.get("Volume") is not None and not pd.isna(r["Volume"]) else None
        rs = float(r["rs_rank"]) if r.get("rs_rank") is not None and not pd.isna(r["rs_rank"]) else None
        sma50 = float(r["SMA50"]) if r.get("SMA50") is not None and not pd.isna(r["SMA50"]) else None
        sma200 = float(r["SMA200"]) if r.get("SMA200") is not None and not pd.isna(r["SMA200"]) else None
        
        if i >= 20:
            s200_prev = float(rows[i-20]["SMA200"]) if rows[i-20].get("SMA200") is not None and not pd.isna(rows[i-20]["SMA200"]) else None
        else:
            s200_prev = None
            
        if i >= c_bars:
            prior_c = [rows[k]["Close"] for k in range(i - c_bars, i)]
            prior_h = [rows[k]["High"] for k in range(i - c_bars, i)]
            prior_l = [rows[k]["Low"] for k in range(i - c_bars, i)]
            ceiling = max(prior_c)
            max_h = max(prior_h)
            min_l = min(prior_l)
            range_depth = (max_h - min_l) / max_h * 100.0 if max_h > 0 else None
        else:
            ceiling = None
            range_depth = None
            
        if i >= v_bars:
            prior_v = [rows[k]["Volume"] for k in range(i - v_bars, i)]
            vol_avg = sum(prior_v) / v_bars
        else:
            vol_avg = None
            
        if h is not None and l is not None and c is not None and (h - l) > 0:
            cloc = (c - l) / (h - l)
        else:
            cloc = None
            
        if i >= 19:
            tv_sum = sum(rows[k]["Close"] * rows[k]["Volume"] for k in range(i - 19, i + 1))
            tv20 = tv_sum / 20.0
        else:
            tv20 = None
            
        entry_ok = (
            c is not None and sma200 is not None and c > sma200 and
            sma50 is not None and sma50 > sma200 and
            s200_prev is not None and sma200 > s200_prev and
            rs is not None and rs >= ent_rs and
            tv20 is not None and tv20 >= min_tv and
            range_depth is not None and range_depth <= max_depth
        )
        
        hold_ok = (
            c is not None and sma200 is not None and c > sma200 and
            rs is not None and rs >= hold_rs and
            tv20 is not None and tv20 >= min_tv
        )
        
        breakout_bar = (
            entry_ok and
            ceiling is not None and c > ceiling * (1.0 + bo_buf / 100.0) and
            vol_avg is not None and v is not None and v >= vol_mult * vol_avg and
            cloc is not None and cloc >= min_cloc
        )
        
        if state == "ACTIVE":
            if i - bo_idx > climb_bars:
                state = "NONE"
                last_event_idx = bo_idx
            elif c is not None and c < bo_level * (1.0 - fail_buf / 100.0):
                state = "FAILED"
                fail_idx = i
                last_event_idx = fail_idx
                
        if state == "FAILED":
            if i - fail_idx > cd_bars:
                state = "NONE"
            elif breakout_bar:
                state = "ACTIVE"
                bo_level = ceiling
                bo_idx = i
                
        if state == "NONE":
            if breakout_bar:
                state = "ACTIVE"
                bo_level = ceiling
                bo_idx = i
                
        st_out = ""
        lvl_out = None
        dist_out = None
        date_out = None
        age_out = None
        
        if state == "ACTIVE":
            age = i - bo_idx
            ext = (c - bo_level) / bo_level * 100.0 if (c is not None and bo_level > 0) else 0.0
            if age <= fresh_bars and ext <= max_ext and hold_ok:
                st_out = "fresh"
            elif age <= fresh_bars and ext > max_ext:
                st_out = "extended"
            elif fresh_bars < age <= climb_bars and c is not None and sma50 is not None and c > sma50 and hold_ok:
                st_out = "climbing"
            else:
                st_out = ""
            lvl_out = round(float(bo_level), 4)
            dist_out = (lvl_out - c) / lvl_out * 100.0 if (c is not None and lvl_out > 0) else None
            date_out = rows[bo_idx]["Timestamp"]
            age_out = age
        elif state == "FAILED":
            st_out = "failed"
            lvl_out = round(float(bo_level), 4)
            dist_out = (lvl_out - c) / lvl_out * 100.0 if (c is not None and lvl_out > 0) else None
            date_out = rows[bo_idx]["Timestamp"]
            age_out = i - bo_idx
        elif state == "NONE":
            if ceiling is not None:
                lvl_out = round(float(ceiling), 4)
                dist_out = (lvl_out - c) / lvl_out * 100.0 if (c is not None and lvl_out > 0) else None
                cd_ok = (last_event_idx == -1 or (i - last_event_idx > cd_bars))
                if entry_ok and cd_ok and dist_out is not None:
                    if 0.0 <= dist_out <= f_dist:
                        st_out = "forming"
                    elif f_dist < dist_out <= w_dist:
                        st_out = "watch"
                    else:
                        st_out = ""
                else:
                    st_out = ""
            else:
                lvl_out = None
                dist_out = None
                st_out = ""
            date_out = None
            age_out = None
            
        flg = 1 if st_out in ("fresh", "climbing", "forming", "watch") else 0
        out_stage.append(st_out)
        out_level.append(lvl_out)
        out_dist.append(dist_out)
        out_bo_date.append(date_out)
        out_age.append(age_out)
        out_flag.append(flg)
        
    res = df_in.copy()
    res["myb_stage"] = out_stage
    res["myb_level"] = out_level
    res["myb_dist_pct"] = out_dist
    res["myb_bo_date"] = out_bo_date
    res["myb_age_bars"] = out_age
    res["screen_multi_year_breakout"] = out_flag
    return res

def test_scenario_f():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT Symbol FROM historical_data")
    syms = [r["Symbol"] for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    
    rng = random.Random(42)
    selected_syms = rng.sample(syms, 50)
    
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    
    for sym in selected_syms:
        cursor.execute("""
            SELECT Timestamp, Close, High, Low, Volume, rs_rank, SMA50, SMA200
            FROM historical_data
            WHERE Symbol = %s
            ORDER BY Timestamp ASC
        """, (sym,))
        rows = cursor.fetchall()
        if not rows:
            continue
        df = pd.DataFrame(rows)
        for col in ["Close", "High", "Low", "Volume", "rs_rank", "SMA50", "SMA200"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        
        prod_res = compute_myb_stage(df, DEFAULT_CONFIG)
        ref_res = reference_myb_stage(df, DEFAULT_CONFIG)
        
        for i in range(len(df)):
            assert prod_res.at[i, "myb_stage"] == ref_res.at[i, "myb_stage"], f"Symbol {sym} row {i} stage mismatch: prod {prod_res.at[i, 'myb_stage']} vs ref {ref_res.at[i, 'myb_stage']}"
            p_lvl = prod_res.at[i, "myb_level"]
            r_lvl = ref_res.at[i, "myb_level"]
            if pd.isna(p_lvl) or pd.isna(r_lvl):
                assert pd.isna(p_lvl) and pd.isna(r_lvl), f"Symbol {sym} row {i} level NaN mismatch"
            else:
                assert abs(p_lvl - r_lvl) < 1e-3, f"Symbol {sym} row {i} level mismatch: prod {p_lvl} vs ref {r_lvl}"
                
            p_dist = prod_res.at[i, "myb_dist_pct"]
            r_dist = ref_res.at[i, "myb_dist_pct"]
            if pd.isna(p_dist) or pd.isna(r_dist):
                assert pd.isna(p_dist) and pd.isna(r_dist), f"Symbol {sym} row {i} dist NaN mismatch"
            else:
                assert abs(p_dist - r_dist) < 1e-3, f"Symbol {sym} row {i} dist mismatch: prod {p_dist} vs ref {r_dist}"
                
            p_bo = prod_res.at[i, "myb_bo_date"]
            r_bo = ref_res.at[i, "myb_bo_date"]
            if pd.isna(p_bo) or pd.isna(r_bo) or p_bo is None or r_bo is None:
                assert (pd.isna(p_bo) or p_bo is None) and (pd.isna(r_bo) or r_bo is None), f"Symbol {sym} row {i} bo_date None mismatch"
            else:
                assert str(p_bo).split()[0] == str(r_bo).split()[0], f"Symbol {sym} row {i} bo_date mismatch: prod {p_bo} vs ref {r_bo}"

    cursor.close()
    conn.close()

def test_idempotence():
    df = build_base_frame(51)
    res1 = compute_myb_stage(df, TEST_CONFIG)
    res2 = compute_myb_stage(res1, TEST_CONFIG)
    pd.testing.assert_frame_equal(res1, res2)

def test_determinism():
    df = build_base_frame(51)
    shuffled = df.sample(frac=1.0, random_state=42)
    with pytest.raises(AssertionError):
        compute_myb_stage(shuffled, TEST_CONFIG)
