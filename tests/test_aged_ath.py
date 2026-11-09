"""
tests/test_aged_ath.py
=============================================================================
Unit tests for Aged All-Time High (ATH) Screener logic & data safeguards.
=============================================================================
"""

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import pytest

from aged_ath_screener import (
    clean_bad_ticks,
    evaluate_aged_ath_stock,
    YFinanceDataProvider,
)
from pattern_screens_logic import compute_screen_aged_ath


def generate_synthetic_stock_series(days: int = 1200, ath_days_ago: int = 800,
                                    current_discount_pct: float = 8.0) -> pd.DataFrame:
    """Helper to generate a controlled synthetic stock price series."""
    dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
    prices = np.linspace(100, 300, days)

    # Inject deliberate ATH at ath_days_ago
    ath_idx = days - ath_days_ago
    ath_price = 500.0
    prices[ath_idx] = ath_price

    # Ensure no prices between ath_idx and end exceed ath_price
    prices[:ath_idx] = np.minimum(prices[:ath_idx], 400.0)
    prices[ath_idx + 1:] = np.minimum(prices[ath_idx + 1:], 450.0)

    # Set current close
    prices[-1] = ath_price * (1.0 - current_discount_pct / 100.0)

    df = pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.01,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": 1000000,
    }, index=dates)

    df["High"].iloc[ath_idx] = ath_price
    df["Close"].iloc[ath_idx] = ath_price
    return df


def test_clean_bad_ticks_spike_reversion():
    """Test that a +50% single-day spike reverting next day is detected and repaired."""
    dates = pd.date_range("2023-01-01", periods=10, freq="B")
    prices = [100.0, 101.0, 102.0, 103.0, 160.0, 104.0, 105.0, 106.0, 107.0, 108.0]

    df = pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
    }, index=dates)

    cleaned = clean_bad_ticks(df, ticker="TEST.NS")
    # Day 4 (160.0) should be interpolated to approx (103 + 104) / 2 = 103.5
    assert cleaned["Close"].iloc[4] < 110.0
    assert cleaned["Close"].iloc[4] == pytest.approx(103.5, abs=1.0)


def test_evaluate_aged_ath_qualifying_stock():
    """Test stock with 2.2 yr old ATH within 8% distance qualifies."""
    # 800 calendar days approx 2.2 years
    df = generate_synthetic_stock_series(days=1200, ath_days_ago=800, current_discount_pct=8.0)

    result, reason = evaluate_aged_ath_stock(
        ticker="TEST.NS",
        df=df,
        min_ath_age_years=2.0,
        max_distance_from_ath_pct=20.0,
        min_listing_age_years=3.0,
        price_basis="close"
    )

    assert result is not None, f"Expected qualification, got skip reason: {reason}"
    assert result["ticker"] == "TEST.NS"
    assert result["ath"] == 500.0
    assert result["distance_pct"] == pytest.approx(8.0, abs=0.5)
    assert result["ath_age_years"] >= 2.0
    assert result["tier"] == "Within 10%"


def test_evaluate_aged_ath_disqualify_recent_ath():
    """Test stock whose ATH was only 6 months ago is disqualified."""
    df = generate_synthetic_stock_series(days=1200, ath_days_ago=120, current_discount_pct=5.0)

    result, reason = evaluate_aged_ath_stock(
        ticker="TEST.NS",
        df=df,
        min_ath_age_years=2.0,
        max_distance_from_ath_pct=20.0,
        min_listing_age_years=3.0,
        price_basis="close"
    )

    assert result is None
    assert "recent" in reason.lower()


def test_evaluate_aged_ath_disqualify_too_far():
    """Test stock whose price is 35% below ATH is disqualified."""
    df = generate_synthetic_stock_series(days=1200, ath_days_ago=800, current_discount_pct=35.0)

    result, reason = evaluate_aged_ath_stock(
        ticker="TEST.NS",
        df=df,
        min_ath_age_years=2.0,
        max_distance_from_ath_pct=20.0,
        min_listing_age_years=3.0,
        price_basis="close"
    )

    assert result is None
    assert "exceeds threshold" in reason.lower()


def test_vectorized_compute_screen_aged_ath():
    """Test the vectorized pattern_screens_logic implementation."""
    df = generate_synthetic_stock_series(days=1200, ath_days_ago=800, current_discount_pct=8.0)
    df["Timestamp"] = df.index

    screen_series = compute_screen_aged_ath(df)
    assert isinstance(screen_series, pd.Series)
    # Final bar should trigger qualification (1)
    assert screen_series.iloc[-1] == 1

