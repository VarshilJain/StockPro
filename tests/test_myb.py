"""
tests/test_myb.py — Unit Tests for Multi-Year Breakout (MYB) Frozen Pivot Architecture

Tests covering:
A. Stock below 100 for 2 years, closes 103, 108, 112, ..., 115 on day 10.
   Expect frozen_pivot = 100 and pct = +15%, stage Climbing.
B. Same stock, broke out 8 days ago, then closes 4% below 100.
   Expect stage Failed.
C. Peak set only 60 days ago, then a cross. Expect no event (base too young).
D. Stock broke out 30 days ago. Expect no active stage.
E. Stock with a +14% gap on day 2. Expect "Extended", not unclassified.
F. Edge case: Ties for max close (verify last occurrence is used for base age).
G. Edge case: Reattempt flag when multiple crosses occur in the 25-day window.
H. Edge case: Trend filter (Close > SMA200 and SMA200 slope >= 0.995).
"""

import unittest
import pandas as pd
import numpy as np
from myb_state_machine_v2 import find_event, detect_myb, MYBConfig


class TestMYBFrozenPivot(unittest.TestCase):

    def setUp(self):
        # Default config: MIN_BASE_DAYS = 250, W = 1008
        self.cfg = MYBConfig(REQUIRE_TREND_FILTER=False)  # Isolate price logic for core synthetic tests

    def test_case_a_climbing_frozen_pivot(self):
        """
        A. Stock below 100 for 2 years (500 bars), closes 103, 108, 112, ..., 115 on day 10.
        Expect frozen_pivot = 100 and pct = +15%, stage Climbing.
        """
        # Peak 100 at index 10, 489 bars at 90, breakout at bar 500 (103), then climbs to 115 on day 10
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [103.0, 104.0, 106.0, 108.0, 110.0, 111.0, 112.0, 113.0, 114.0, 115.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertIsNotNone(res)
        self.assertEqual(res["frozen_pivot"], 100.0)
        self.assertEqual(res["pct_from_pivot"], 15.0)
        self.assertEqual(res["stage"], "Climbing")
        self.assertEqual(res["days_since"], 10)
        self.assertIsNone(res["failed_date"])

    def test_case_b_failed_stage(self):
        """
        B. Same stock, broke out 8 days ago, then closes 4% below 100.
        Expect stage Failed.
        """
        # Broke out 8 days ago (103 at bar 500), then drops to 96 (-4% below 100) on day 8
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [103.0, 104.0, 102.0, 100.0, 99.0, 98.0, 97.0, 96.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertIsNotNone(res)
        self.assertEqual(res["frozen_pivot"], 100.0)
        self.assertEqual(res["pct_from_pivot"], -4.0)
        self.assertEqual(res["stage"], "Failed")
        self.assertIsNotNone(res["failed_date"])

    def test_case_c_base_too_young(self):
        """
        C. Peak set only 60 days ago, then a cross.
        Expect no event (base too young).
        """
        # 440 bars at 80, peak 100 at bar 440, then only 59 bars at 90 (< 250 days), then 103
        closes = [80.0] * 440 + [100.0] + [90.0] * 59 + [103.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        # Base age is 60 bars < 250 MIN_BASE_DAYS -> No event
        self.assertTrue(res is None or res.get("stage") is None)

    def test_case_d_breakout_30_days_ago(self):
        """
        D. Stock broke out 30 days ago.
        Expect no active stage (exceeds MAX_AGE 25).
        """
        # Peak 100, broke out at bar 500 (103), then 29 more bars (total 30 days since event)
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [103.0] + [105.0] * 29
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertTrue(res is None or res.get("stage") is None)

    def test_case_e_extended_stage(self):
        """
        E. Stock with a +14% gap on day 2.
        Expect Extended, not unclassified.
        """
        # Broke out on bar 500 (103), day 2 closes at 114 (+14% above 100)
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [103.0, 114.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertIsNotNone(res)
        self.assertEqual(res["frozen_pivot"], 100.0)
        self.assertEqual(res["pct_from_pivot"], 14.0)
        self.assertEqual(res["stage"], "Extended")
        self.assertEqual(res["days_since"], 2)

    def test_case_f_ties_use_last_occurrence(self):
        """
        F. Edge Case: Ties for max close in lookback window.
        Must use the LAST occurrence to calculate base_age.
        """
        # Peak 100 at bar 10, and peak 100 again at bar 200. Breakout at bar 500.
        # Base age must be 500 - 200 = 300 (from last occurrence), NOT 500 - 10 = 490.
        closes = [80.0] * 10 + [100.0] + [90.0] * 189 + [100.0] + [90.0] * 299 + [105.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertIsNotNone(res)
        self.assertEqual(res["base_age_at_event"], 300)

    def test_case_g_reattempt_flag(self):
        """
        G. Edge Case: Multiple crosses in 25 days -> flag reattempt = True.
        """
        # Crosses at day -15 (102.0), dips to 99, crosses again at day -2 (103.0), closes at 104.0 today
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [102.0] + [99.0] * 12 + [103.0, 104.0]
        s = pd.Series(closes)
        res = find_event(s, config=self.cfg)

        self.assertIsNotNone(res)
        self.assertTrue(res["reattempt"])
        self.assertEqual(res["days_since"], 2)
        self.assertEqual(res["frozen_pivot"], 100.0)

    def test_case_h_trend_filter(self):
        """
        H. Trend Filter: Close > SMA200 and SMA200 slope >= 0.995.
        Stock with falling SMA200 must NOT pass.
        """
        # Declining price series where SMA200 is plunging downwards
        declining_sma = pd.Series(np.linspace(150, 100, 520))
        closes = [80.0] * 10 + [100.0] + [90.0] * 489 + [103.0, 104.0]
        s = pd.Series(closes)

        # With trend filter enabled, if SMA200 slope falls > 0.5%, it must be rejected
        cfg_trend = MYBConfig(REQUIRE_TREND_FILTER=True)
        res = find_event(s, sma200=declining_sma, config=cfg_trend)
        self.assertTrue(res is None or res.get("stage") is None)


if __name__ == "__main__":
    unittest.main()
