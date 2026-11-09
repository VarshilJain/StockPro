"""
tests/test_myb.py — Unit Tests for Multi-Year Breakout (MYB) Detection Logic
"""

import unittest
import pandas as pd
import numpy as np
from myb_state_machine_v2 import detect_myb, MYBConfig


class TestMYBDetection(unittest.TestCase):

    def setUp(self):
        self.n = 300
        self.dates = pd.date_range("2025-01-01", periods=self.n, freq="B")

    def test_case_a_clean_base_forming(self):
        """a) Clean 250+ day base, close below pivot -> Forming with negative pct_from_pivot."""
        # Base ceiling at 100, current price at 90 (-10%)
        closes = [95.0] * 50 + [100.0] + [90.0] * 249
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [85.0] * self.n,
            "SMA200": [80.0] * self.n,
            "rs_rank": [75.0] * self.n
        })
        res = detect_myb(df)
        self.assertEqual(res["stage"], "forming")
        self.assertEqual(res["pivot"], 100.0)
        self.assertEqual(res["pct_from_pivot"], -10.0)
        self.assertLess(res["pct_from_pivot"], 0)

    def test_case_b_fresh_breakout(self):
        """b) Breakout 3 days ago -> Fresh Breakout."""
        # Ceiling at 100, price breaks out to 105 3 days ago
        closes = [90.0] * 289 + [100.0] + [95.0] * 7 + [105.0, 106.0, 107.0]
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [85.0] * self.n,
            "SMA200": [80.0] * self.n,
            "rs_rank": [75.0] * self.n
        })
        res = detect_myb(df)
        self.assertEqual(res["stage"], "fresh_breakout")
        self.assertEqual(res["pivot"], 100.0)
        self.assertGreater(res["pct_from_pivot"], 0)

    def test_case_c_climbing(self):
        """c) Breakout 30 days ago, above SMA50 and SMA200 -> Climbing."""
        # Broke out 30 bars ago, holding above SMA50 (100) and SMA200 (85)
        closes = [90.0] * 260 + [100.0] + [95.0] * 9 + [108.0] * 30
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [100.0] * self.n,
            "SMA200": [85.0] * self.n,
            "rs_rank": [75.0] * self.n
        })
        res = detect_myb(df)
        self.assertEqual(res["stage"], "climbing")
        self.assertGreater(res["pct_from_pivot"], 0)

    def test_case_d_balkrisind_like(self):
        """
        d) BALKRISIND-like case:
        Mid-range level 2169 crossed repeatedly, peak in Feb at ~2707, current price 2410.
        Must NOT be flagged as a breakout, must show pivot near the true ceiling (~2707).
        With strict -10% cutoff, at -10.97% it is excluded ('invalid').
        With forming_min_pct=-15.0%, stage is 'forming' with strictly negative pct_from_pivot.
        """
        # Peak at index 114 (Feb): 2706.88. Chop around 2169, current price 2410
        closes = [2200.0] * 50 + [2706.88] + [2169.0] * 100 + [2300.0] * 50 + [2169.0] * 50 + [2410.0] * 49
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [2300.0] * self.n,
            "SMA200": [2200.0] * self.n,
            "rs_rank": [70.0] * self.n
        })
        # Default config: strict -10.0% cutoff -> 2410 is -10.97%, so fails forming cutoff
        res_default = detect_myb(df)
        self.assertEqual(res_default["stage"], "invalid")
        self.assertEqual(res_default["pivot"], 2706.88)
        self.assertAlmostEqual(res_default["pct_from_pivot"], -10.97, places=2)

        # Config with -15% cutoff -> passes as forming
        res_relaxed = detect_myb(df, config=MYBConfig(forming_min_pct=-15.0))
        self.assertEqual(res_relaxed["stage"], "forming")
        self.assertEqual(res_relaxed["pivot"], 2706.88)
        self.assertLess(res_relaxed["pct_from_pivot"], 0)
        self.assertAlmostEqual(res_relaxed["pct_from_pivot"], -10.97, places=2)

    def test_case_e_no_positive_pct_in_forming(self):
        """e) Acceptance criterion: No stock can show a positive pct_from_pivot while labeled Forming."""
        # Price 105, pivot 100 -> pct_from_pivot = +5.0%. Cannot be forming!
        closes = [90.0] * 290 + [100.0] + [95.0] * 8 + [105.0]
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [85.0] * self.n,
            "SMA200": [80.0] * self.n,
            "rs_rank": [75.0] * self.n
        })
        res = detect_myb(df)
        # 105 crossed 1 bar ago -> fresh_breakout, NOT forming!
        self.assertNotEqual(res["stage"], "forming")

    def test_preconditions_fail(self):
        """Failed pre-conditions (Close < SMA200 or RS < 60) -> invalid."""
        closes = [95.0] * 50 + [100.0] + [90.0] * 249
        df = pd.DataFrame({
            "Timestamp": self.dates,
            "Close": closes,
            "High": closes,
            "Low": closes,
            "SMA50": [85.0] * self.n,
            "SMA200": [95.0] * self.n,  # Close (90) < SMA200 (95)
            "rs_rank": [50.0] * self.n  # RS (50) < 60
        })
        res = detect_myb(df)
        self.assertEqual(res["stage"], "invalid")


if __name__ == "__main__":
    unittest.main()

