"""
routers/fundamentals.py
-----------------------
FastAPI router that fetches fundamental financial statements for a stock
using yfinance. Supports annual and quarterly balance sheets, income
statements, cash-flow statements, and the full stock.info dict.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from symbols_list import SYMBOLS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Fundamentals"])

# Maps the frontend `statement` param to the yfinance Ticker attribute name.
# "stock_info" is special-cased: it calls stock.info (returns a dict).
STATEMENT_MAP: dict[str, str] = {
    "balance_sheet":           "balance_sheet",
    "income_statement":        "financials",
    "cash_flow":               "cashflow",
    "quarterly_balance_sheet": "quarterly_balance_sheet",
    "quarterly_income":        "quarterly_financials",
    "quarterly_cash_flow":     "quarterly_cashflow",
    "stock_info":              "info",   # dict, not DataFrame
    "news":                    "news",   # list of news articles
}


def _sanitize(value: Any) -> Any:
    """Convert pandas/numpy types to JSON-safe Python scalars."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    # numpy numeric types
    try:
        import numpy as np
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            v = float(value)
            return None if (math.isnan(v) or math.isinf(v)) else v
    except ImportError:
        pass
    return value


def _df_to_payload(df: pd.DataFrame) -> dict:
    """
    Convert a yfinance DataFrame to a JSON-safe dict with:
      - columns: list of column header strings (report dates)
      - rows:    list of {label, values} objects
    """
    if df is None or df.empty:
        return {}

    # Column headers: Timestamps → "YYYY-MM-DD" strings
    cols: list[str] = []
    for col in df.columns:
        if hasattr(col, "strftime"):
            cols.append(col.strftime("%Y-%m-%d"))
        else:
            cols.append(str(col))

    rows: list[dict] = []
    for idx, row in df.iterrows():
        label = str(idx)
        values = [_sanitize(v) for v in row]
        rows.append({"label": label, "values": values})

    return {"columns": cols, "rows": rows}


def _info_to_payload(info: dict) -> dict:
    """
    Convert stock.info (a plain dict) into the same {columns, rows} shape
    used by _df_to_payload so the frontend table renders it identically.

    Layout:  columns=["Field", "Value"],  rows=[{label, values:[field, val]}, ...]
    """
    rows: list[dict] = []
    for key, raw_val in info.items():
        # Sanitize numeric values; keep strings as-is
        if isinstance(raw_val, (int, float)):
            val = _sanitize(raw_val)
        elif raw_val is None:
            val = None
        else:
            val = str(raw_val)
        rows.append({"label": key, "values": [val]})

    return {"columns": ["Value"], "rows": rows}


def _news_to_payload(stock_news: list) -> dict:
    """
    Convert yfinance news list to a JSON-safe dict with a structured list
    of news articles. Each article has: title, summary, provider, link.
    """
    articles: list[dict] = []
    for item in stock_news:
        content = item.get("content", item)  # handles both old & new yfinance formats
        title    = content.get("title", "No title")
        summary  = content.get("summary", "") or content.get("description", "") or ""
        provider_raw = content.get("provider", {})
        provider = (
            provider_raw.get("displayName", "")
            if isinstance(provider_raw, dict)
            else str(provider_raw)
        )
        canonical = content.get("canonicalUrl", {})
        link = (
            canonical.get("url", "")
            if isinstance(canonical, dict)
            else item.get("link", "")
        )
        articles.append({
            "title":    title,
            "summary":  summary,
            "provider": provider,
            "link":     link,
        })
    return {"columns": ["Title", "Summary", "Source", "Link"], "articles": articles}


@router.get("/fundamentals")
def fetch_fundamentals(
    ticker: str = Query(..., description="Stock ticker, e.g. TATAPOWER.NS, AAPL"),
    statement: str = Query(
        ...,
        description=(
            "One of: balance_sheet, income_statement, cash_flow, "
            "quarterly_balance_sheet, quarterly_income, quarterly_cash_flow, "
            "stock_info, news"
        ),
    ),
) -> JSONResponse:
    """
    Fetch a fundamental financial statement for the given ticker.

    Returns a JSON object:
    {
      "ticker":    "TATAPOWER.NS",
      "statement": "balance_sheet",
      "columns":   ["2024-03-31", "2023-03-31", ...],
      "rows":      [{"label": "Total Assets", "values": [...]}, ...]
    }

    For statement="news", returns:
    {
      "ticker":    "TATAPOWER.NS",
      "statement": "news",
      "columns":   ["Title", "Summary", "Source", "Link"],
      "articles":  [{"title": ..., "summary": ..., "provider": ..., "link": ...}, ...]
    }

    Errors:
      404 – no data found for that ticker/statement combo
      400 – unknown statement type
      502 – yfinance raised an exception
    """
    statement = statement.strip().lower()
    if statement not in STATEMENT_MAP:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown statement '{statement}'. "
                f"Valid values: {list(STATEMENT_MAP.keys())}"
            ),
        )

    attr = STATEMENT_MAP[statement]
    is_info = (statement == "stock_info")
    is_news = (statement == "news")

    try:
        stock = yf.Ticker(ticker)
        raw = getattr(stock, attr)
    except Exception as exc:
        logger.error("yfinance error for ticker=%s attr=%s: %s", ticker, attr, exc)
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch data from financial data provider. Please try again later.",
        )


    # ── news returns a list of articles ──
    if is_news:
        if not raw:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No news found for ticker '{ticker}'. "
                    "Check that the ticker is valid."
                ),
            )
        payload = _news_to_payload(raw)

    # ── stock.info returns a dict ──
    elif is_info:
        if not raw:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No info returned for ticker '{ticker}'. "
                    "Check that the ticker is valid."
                ),
            )
        payload = _info_to_payload(raw)

    # ── everything else is a DataFrame ──
    else:
        df: pd.DataFrame = raw
        note = None
        is_fallback = False

        if df is None or df.empty:
            # Fallback for quarterly cash flow when yfinance has no 3-month data
            if statement == "quarterly_cash_flow":
                try:
                    df = stock.cashflow
                    if df is not None and not df.empty:
                        is_fallback = True
                        note = (
                            "Quarterly cash flow is not published on Yahoo Finance for this ticker "
                            "(SEBI LODR rules mandate half-yearly or annual cash flow disclosures for non-IT companies). "
                            "Displaying Annual Cash Flow as a fallback."
                        )
                except Exception:
                    pass

            if df is None or df.empty:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No data returned for ticker '{ticker}' and statement '{statement}'. "
                        "Quarterly cash flow is not published for most non-IT Indian listed companies on Yahoo Finance. "
                        "Try selecting Annual Cash Flow instead."
                    ),
                )
        else:
            # Check date intervals for quarterly balance sheet
            if statement == "quarterly_balance_sheet":
                cols = list(df.columns)
                if len(cols) >= 2:
                    try:
                        d1 = pd.to_datetime(cols[0])
                        d2 = pd.to_datetime(cols[1])
                        days_diff = abs((d1 - d2).days)
                        if days_diff > 100:
                            note = (
                                "Notice: Under SEBI LODR regulations, Indian companies publish Balance Sheets "
                                "half-yearly (6-month) or annually (12-month) rather than 3-month quarterly intervals."
                            )
                    except Exception:
                        pass

        payload = _df_to_payload(df)
        if note:
            payload["note"] = note
        payload["is_fallback"] = is_fallback

    payload["ticker"] = ticker.upper()
    payload["statement"] = statement

    return JSONResponse(content=payload)


@router.get("/usd-rate")
def get_usd_rate() -> JSONResponse:
    """
    Fetch the live INR → USD exchange rate using yfinance (USDINR=X ticker).
    Returns:
      { "usd_per_inr": 0.01201, "inr_per_usd": 83.25, "updated_at": "2024-..." }
    """
    try:
        ticker = yf.Ticker("USDINR=X")
        info = ticker.fast_info
        # fast_info.last_price gives the latest USDINR rate (e.g. 83.25)
        inr_per_usd = float(info.last_price)
        usd_per_inr = round(1.0 / inr_per_usd, 8)

        import datetime
        return JSONResponse(content={
            "usd_per_inr": usd_per_inr,
            "inr_per_usd": round(inr_per_usd, 4),
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        })
    except Exception as exc:
        logger.error("Failed to fetch USD/INR rate: %s", exc)
        # Fallback: return a sensible default so the frontend doesn't break
        return JSONResponse(
            status_code=200,
            content={
                "usd_per_inr": 0.012,
                "inr_per_usd": 83.5,
                "updated_at": None,
                "fallback": True,
            },
        )


@router.get("/all-symbols")
def get_all_symbols():
    """Return the full static list of NSE stock symbols for frontend autocomplete."""
    return {"symbols": sorted(SYMBOLS)}
