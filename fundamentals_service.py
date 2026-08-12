"""
fundamentals_service.py
-----------------------
Stub implementation for fundamental analysis data.
Currently returns placeholder data. Can be extended to call
Gemini API or any financial data provider.
"""

from __future__ import annotations
from typing import Dict


def get_fundamentals(ticker: str) -> Dict:
    """
    Return cached or freshly fetched fundamental data for a ticker.
    Stub: returns placeholder data until a real data source is wired in.
    """
    return {
        "ticker": ticker,
        "pe_ratio": None,
        "eps": None,
        "market_cap": None,
        "book_value": None,
        "dividend_yield": None,
        "52_week_high": None,
        "52_week_low": None,
        "note": "Fundamental data not yet available. Connect a data provider to populate this.",
    }


def refresh_fundamentals(ticker: str) -> Dict:
    """
    Force-refresh fundamental data for a ticker, bypassing any cache.
    Stub: same as get_fundamentals until a real provider is wired in.
    """
    return get_fundamentals(ticker)
