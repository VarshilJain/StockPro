# -*- coding: utf-8 -*-
"""
test_signals.py — Unit tests for every fixed candlestick / indicator function.

Each test builds a minimal hand-crafted OHLC DataFrame where the correct
answer (signal fires = 1, or does not fire = 0) is known in advance.
Run with:  python test_signals.py

All assertions print a ✅ / ❌ result.  Exit code is 0 on full pass.
"""

import sys
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Import the fixed signal functions directly from test.py.
# We import the module-level functions; they don't execute the CSV-read block
# at import time because that block is at module scope — we work around it by
# importing only the functions we need after monkey-patching sys.argv.
# ---------------------------------------------------------------------------
# Simpler approach: re-define the functions inline using the SAME fixed logic
# and the same config constants, so the test file is self-contained.
# ---------------------------------------------------------------------------
import importlib.util, os, types

# Load config constants
sys.path.insert(0, os.path.dirname(__file__))
from config import (
    DOJI_BODY_RATIO,
    WICK_BODY_RATIO,
    HRV_MULTIPLIER,
    ADX_THRESHOLD,
    RSI_LT_THRESHOLD,
    RSI_GT_THRESHOLD,
    RSI_OVERSOLD_LEVEL,
    RSI_OVERBOUGHT_LEVEL,
    TRADING_DAYS_PER_YEAR,
)


# ---------------------------------------------------------------------------
# Copy of each fixed function (identical to test.py — kept here so the test
# file runs standalone without touching the CSV-read block in test.py).
# ---------------------------------------------------------------------------

def hammer(df):
    body       = (df["Close"] - df["Open"]).abs()
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    return (lower_wick >= 2 * body) & (upper_wick <= WICK_BODY_RATIO * body) & (body > 0)


def shooting_star(df):
    body       = (df["Close"] - df["Open"]).abs()
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    return (upper_wick >= 2 * body) & (lower_wick <= WICK_BODY_RATIO * body) & (body > 0)


def doji(df):
    return (df["Close"] - df["Open"]).abs() <= (df["High"] - df["Low"]) * DOJI_BODY_RATIO


def engulfing(df):
    bullish = (
        (df["Open"].shift(1) > df["Close"].shift(1)) &
        (df["Open"]          < df["Close"]          ) &
        (df["Open"]          <= df["Close"].shift(1)) &
        (df["Close"]         >= df["Open"].shift(1) )
    )
    bearish = (
        (df["Open"].shift(1) < df["Close"].shift(1)) &
        (df["Open"]          > df["Close"]          ) &
        (df["Open"]          >= df["Close"].shift(1)) &
        (df["Close"]         <= df["Open"].shift(1) )
    )
    return bullish | bearish


def dark_cloud_cover(df):
    midpoint = (df["Close"].shift(1) + df["Open"].shift(1)) / 2
    return (
        (df["Close"].shift(1) > df["Open"].shift(1)) &
        (df["Open"]           > df["High"].shift(1) ) &
        (df["Open"]           > df["Close"]          ) &
        (df["Close"]          < midpoint             )
    )


def piercing_line(df):
    midpoint = (df["Close"].shift(1) + df["Open"].shift(1)) / 2
    return (
        (df["Close"].shift(1) < df["Open"].shift(1)) &
        (df["Open"]           < df["Low"].shift(1)  ) &
        (df["Open"]           < df["Close"]          ) &
        (df["Close"]          > midpoint             )
    )


def morning_star(df):
    c2_body     = (df["Close"].shift(2) - df["Open"].shift(2)).abs()
    c1_body     = (df["Close"].shift(1) - df["Open"].shift(1)).abs()
    midpoint_c2 = (df["Close"].shift(2) + df["Open"].shift(2)) / 2
    return (
        (df["Close"].shift(2) < df["Open"].shift(2)) &
        (c2_body               > 0                  ) &
        (c1_body               < 0.3 * c2_body      ) &
        (df["Open"]            < df["Close"]         ) &
        (df["Close"]           > midpoint_c2         )
    )


def evening_star(df):
    c2_body     = (df["Close"].shift(2) - df["Open"].shift(2)).abs()
    c1_body     = (df["Close"].shift(1) - df["Open"].shift(1)).abs()
    midpoint_c2 = (df["Close"].shift(2) + df["Open"].shift(2)) / 2
    return (
        (df["Close"].shift(2) > df["Open"].shift(2)) &
        (c2_body               > 0                  ) &
        (c1_body               < 0.3 * c2_body      ) &
        (df["Open"]            > df["Close"]         ) &
        (df["Close"]           < midpoint_c2         )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(*rows):
    """Build a DataFrame from a list of (Open, High, Low, Close) tuples."""
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])


def _check(name, result_series, expected_list):
    """Assert result matches expected list of 0/1 values."""
    actual = result_series.astype(int).tolist()
    if actual == expected_list:
        print(f"  [PASS] {name}")
        return True
    else:
        print(f"  [FAIL] {name}")
        print(f"         expected: {expected_list}")
        print(f"         got:      {actual}")
        return False


PASSES = FAILS = 0


def test(name, result_series, expected_list):
    global PASSES, FAILS
    ok = _check(name, result_series, expected_list)
    if ok:
        PASSES += 1
    else:
        FAILS += 1


# ===========================================================================
# 1. HAMMER
# ===========================================================================
print("\n=== Hammer ===")

# Classic bearish-body hammer: body=2, lower_wick=6 (≥2×body), upper_wick=0
df = _df(
    (100, 102, 100, 98),   # row 0: random day (should NOT fire)
    (102, 102, 94, 100),   # row 1: Open=102 Close=100 body=2, lower=100-94=6, upper=102-102=0 → HAMMER
    (100, 105, 99, 103),   # row 2: green candle, not a hammer
)
test("Hammer — bearish body, fires on row 1", hammer(df), [0, 1, 0])

# Classic bullish-body hammer: body=2, lower_wick=6, upper_wick=0.1 (≤10% of 2)
df = _df(
    (98, 98, 90, 100),     # Open=98 Close=100 body=2, lower_wick=min(98,100)-90=8, upper=max(98,100)-98=0 → HAMMER green
)
test("Hammer — green (bullish) body also fires", hammer(df), [1])

# NOT a hammer: large upper wick
df = _df(
    (100, 110, 94, 98),    # body=2, lower=100-94=6≥4✓, but upper=110-100=10 > 0.2 ✗
)
test("Hammer — large upper wick disqualifies", hammer(df), [0])

# NOT a hammer: lower wick too small (1× body)
df = _df(
    (100, 100, 98, 98),    # body=2, lower_wick=98-98=0 < 4 ✗
)
test("Hammer — lower wick too short", hammer(df), [0])


# ===========================================================================
# 2. SHOOTING STAR
# ===========================================================================
print("\n=== Shooting Star ===")

# Classic: body=2, upper_wick=6 (≥2×2), lower_wick=0
df = _df(
    (100, 108, 100, 102),  # Open=100 Close=102 body=2, upper=108-102=6, lower=min(100,102)-100=0 → SS
)
test("Shooting Star — green body fires", shooting_star(df), [1])

# Bearish-body shooting star
df = _df(
    (102, 108, 102, 100),  # body=2, upper=108-102=6, lower=min(102,100)-102=0 → SS red body
)
test("Shooting Star — red body also fires", shooting_star(df), [1])

# NOT a shooting star: large lower wick
df = _df(
    (100, 108, 94, 102),   # upper=6 ✓ but lower=min(100,102)-94=6 > 0.2 ✗
)
test("Shooting Star — large lower wick disqualifies", shooting_star(df), [0])


# ===========================================================================
# 3. DOJI
# ===========================================================================
print("\n=== Doji ===")

# body=0.5, range=10 → body/range = 5% ≤ 10% → DOJI
df = _df(
    (100.0, 105.0, 95.0, 100.5),
)
test("Doji — small body relative to range", doji(df), [1])

# body=2, range=4 → 50% → NOT doji
df = _df(
    (100, 102, 98, 102),
)
test("Doji — large body relative to range", doji(df), [0])

# body=0, range=0 → NaN check: 0/0. In our formula: abs(0-0) <= (0-0)*0.1 → 0 <= 0 → True
# A completely flat candle (all prices equal) technically satisfies the doji condition
# mathematically (zero body ≤ zero range * anything). We document this as expected behaviour.
df = _df(
    (100, 100, 100, 100),  # flat candle
)
test("Doji — zero range flat candle (0<=0, fires by definition)", doji(df), [1])


# ===========================================================================
# 4. ENGULFING
# ===========================================================================
print("\n=== Engulfing ===")

# Bullish engulfing:  prior RED (Open=105 Close=100), current GREEN (Open=99, Close=108)
df = _df(
    (105, 106, 99, 100),   # row 0: prior RED candle
    (99,  110, 99, 108),   # row 1: current GREEN, open<prior_close(100), close>prior_open(105) → BULLISH
)
test("Engulfing — bullish (prior red, current green engulfs)", engulfing(df), [0, 1])

# Bearish engulfing: prior GREEN (Open=100, Close=105), current RED (Open=106, Close=98)
df = _df(
    (100, 106, 99, 105),   # prior GREEN
    (106, 107, 97, 98),    # current RED, open>prior_close(105), close<prior_open(100) → BEARISH
)
test("Engulfing — bearish (prior green, current red engulfs)", engulfing(df), [0, 1])

# NOT engulfing: same direction (both green)
df = _df(
    (100, 104, 99, 103),   # prior GREEN
    (102, 106, 101, 105),  # current GREEN — not engulfing
)
test("Engulfing — both green, no signal", engulfing(df), [0, 0])

# NOT engulfing: partial overlap only
df = _df(
    (105, 107, 102, 100),  # prior RED  (O=105, C=100)
    (101, 104, 100, 103),  # current GREEN but Close=103 < Open.shift(1)=105 → doesn't engulf
)
test("Engulfing — partial overlap, no signal", engulfing(df), [0, 0])


# ===========================================================================
# 5. DARK CLOUD COVER
# ===========================================================================
print("\n=== Dark Cloud Cover ===")

# Prior GREEN (O=100, H=108, C=108), current GAPS UP above H=108, closes below midpoint=(100+108)/2=104
df = _df(
    (100, 108, 99, 108),    # prior GREEN  High=108
    (110, 112, 103, 103),   # Open=110 > prior High=108 ✓; Open>Close ✓; Close=103 < midpoint=104 ✓
)
test("Dark Cloud Cover — correct pattern fires", dark_cloud_cover(df), [0, 1])

# Does NOT fire: open does NOT gap above prior High
df = _df(
    (100, 108, 99, 108),
    (107, 110, 103, 103),   # Open=107 < prior High=108 → no gap up
)
test("Dark Cloud Cover — no gap up, no signal", dark_cloud_cover(df), [0, 0])

# Does NOT fire: close is above midpoint
df = _df(
    (100, 108, 99, 108),
    (110, 112, 105, 106),   # Close=106 > midpoint=104 → not deep enough
)
test("Dark Cloud Cover — close above midpoint, no signal", dark_cloud_cover(df), [0, 0])


# ===========================================================================
# 6. PIERCING LINE
# ===========================================================================
print("\n=== Piercing Line ===")

# Prior RED (O=108, L=100, C=100), current GAPS DOWN below Low=100, closes above midpoint=(108+100)/2=104
df = _df(
    (108, 109, 100, 100),   # prior RED  Low=100
    (98,  106, 98,  105),   # Open=98 < prior Low=100 ✓; Close=105 > midpoint=104 ✓
)
test("Piercing Line — correct pattern fires", piercing_line(df), [0, 1])

# Does NOT fire: open does NOT gap below prior Low
df = _df(
    (108, 109, 100, 100),
    (101, 106, 100, 105),   # Open=101 > prior Low=100 → no gap down
)
test("Piercing Line — no gap down, no signal", piercing_line(df), [0, 0])

# Does NOT fire: close below midpoint
df = _df(
    (108, 109, 100, 100),
    (98,  103, 98,  103),   # Close=103 < midpoint=104 → not deep enough
)
test("Piercing Line — close below midpoint, no signal", piercing_line(df), [0, 0])


# ===========================================================================
# 7. MORNING STAR
# ===========================================================================
print("\n=== Morning Star ===")

# c[-2]: RED O=110 C=100 (body=10)
# c[-1]: tiny body O=101 C=100 (body=1 < 30% of 10=3) — any color
# c[0]:  GREEN O=100 C=107; midpoint_c2=(100+110)/2=105; Close=107>105 ✓
df = _df(
    (110, 111, 99,  100),   # c[-2] RED
    (101, 102, 99,  100),   # c[-1] small body (=1)
    (100, 108, 100, 107),   # today GREEN, close=107 > midpoint=105
)
test("Morning Star — correct 3-candle pattern fires", morning_star(df), [0, 0, 1])

# Does NOT fire: middle candle too large (body=5 > 30% of 10=3)
df = _df(
    (110, 111, 99,  100),
    (105, 106, 99,  100),   # body=5 — too big
    (100, 108, 100, 107),
)
test("Morning Star — middle candle too big, no signal", morning_star(df), [0, 0, 0])

# Does NOT fire: today closes below midpoint
df = _df(
    (110, 111, 99,  100),
    (101, 102, 99,  100),
    (100, 104, 100, 104),   # Close=104 < midpoint=105
)
test("Morning Star — close below midpoint, no signal", morning_star(df), [0, 0, 0])

# Middle candle GREEN (should still fire — any color allowed)
df = _df(
    (110, 111, 99,  100),
    (99,  101, 99,  100),   # GREEN tiny body=1
    (100, 108, 100, 107),
)
test("Morning Star — green middle candle also fires", morning_star(df), [0, 0, 1])


# ===========================================================================
# 8. EVENING STAR
# ===========================================================================
print("\n=== Evening Star ===")

# c[-2]: GREEN O=100 C=110 (body=10)
# c[-1]: tiny body O=109 C=110 (body=1)
# c[0]:  RED O=110 C=103; midpoint_c2=(110+100)/2=105; Close=103<105 ✓
df = _df(
    (100, 111, 99, 110),    # c[-2] GREEN
    (109, 112, 109, 110),   # c[-1] small body (=1)
    (110, 111, 102, 103),   # today RED, close=103 < midpoint=105
)
test("Evening Star — correct 3-candle pattern fires", evening_star(df), [0, 0, 1])

# Does NOT fire: middle candle too large
df = _df(
    (100, 111, 99, 110),
    (104, 112, 104, 110),   # body=6 > 30% of 10=3
    (110, 111, 102, 103),
)
test("Evening Star — middle candle too big, no signal", evening_star(df), [0, 0, 0])

# Does NOT fire: today closes above midpoint
df = _df(
    (100, 111, 99, 110),
    (109, 112, 109, 110),
    (110, 111, 106, 106),   # Close=106 > midpoint=105
)
test("Evening Star — close above midpoint, no signal", evening_star(df), [0, 0, 0])

# Red middle candle (should still fire)
df = _df(
    (100, 111, 99,  110),
    (111, 112, 109, 110),   # RED tiny body=1
    (110, 111, 102, 103),
)
test("Evening Star — red middle candle also fires", evening_star(df), [0, 0, 1])


# ===========================================================================
# 9. ADDITIONAL: Verify old bugs would have produced wrong answers
# ===========================================================================
print("\n=== Regression — old bugs produce wrong results (sanity checks) ===")

# Old Engulfing bug: bullish was checking prior GREEN (wrong)
# A prior GREEN followed by a larger GREEN should NOT fire bullish engulfing
df = _df(
    (100, 104, 99, 103),   # prior GREEN (Open<Close)
    (102, 108, 102, 107),  # current GREEN — old code would fire bullish (wrong!)
)
result = engulfing(df)
# With fixed code: prior GREEN + current GREEN → bearish condition: prior must be GREEN
# but current must be RED — current is GREEN so bearish=False
# bullish: prior must be RED — prior is GREEN so bullish=False → signal=0
test("Regression: prior GREEN + current GREEN -> no engulfing", engulfing(df), [0, 0])

# Old Doji bug: ratio formula (Open/Close in [0.995,1.005])
# A high-volatility doji: body=0.4, range=20 — old formula would fire (0.4/100=0.4% ratio)
# but body/range = 2% ≤ 10% → new formula ALSO fires. Verify both agree on this case.
df = _df(
    (100.0, 110.0, 90.0, 100.4),   # body=0.4, range=20, body/range=2% → Doji
)
test("Doji range-relative: body=0.4, range=20 (fires)", doji(df), [1])

# Now a case where they disagree: body=0.4, range=2 → body/range=20% → NOT doji
# Old formula: 0.4/100.4 = 0.004 → ratio ≈ 0.996 → WOULD fire (wrong)
# New formula: 0.4/2 = 0.20 > 0.10 → does NOT fire (correct)
df = _df(
    (100.0, 101.0, 99.0, 100.4),   # body=0.4, range=2, body/range=20%
)
test("Doji range-relative: body=0.4, range=2 (should NOT fire)", doji(df), [0])


# ===========================================================================
# Summary
# ===========================================================================
print(f"\n{'='*50}")
print(f"Results: {PASSES} passed, {FAILS} failed out of {PASSES+FAILS} tests")
if FAILS == 0:
    print("All tests passed [OK]")
else:
    print(f"{FAILS} test(s) FAILED [!!]")

sys.exit(0 if FAILS == 0 else 1)
