"""
routers/watchlist.py — Endpoints for user stock watchlist
"""
from __future__ import annotations
import logging
from typing import Optional
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from auth import get_current_user
from database import get_connection
from routers.screens import get_symbol_market_cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["Watchlist"])


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=50)


def _normalize_symbol(sym: str) -> str:
    return sym.strip().upper()


def _get_symbol_details(symbol: str) -> dict:
    norm_sym = _normalize_symbol(symbol)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        alt_sym = norm_sym[:-3] if norm_sym.endswith(".NS") else f"{norm_sym}.NS"
        # Try historical_data first for full stats including rs_rank
        cursor.execute(
            """
            SELECT Symbol, Close, Open, High, Low, Volume, IFNULL(rs_rank, 0) as rs_rank, Timestamp
            FROM historical_data
            WHERE Symbol = %s OR Symbol = %s
            ORDER BY Timestamp DESC
            LIMIT 1
            """,
            (norm_sym, alt_sym)
        )
        row = cursor.fetchone()

        # Fallback to ohlc_data if not found
        if not row:
            cursor.execute(
                """
                SELECT ticker as Symbol, close as Close, open as Open, high as High, low as Low, volume as Volume, NULL as rs_rank, date as Timestamp
                FROM ohlc_data
                WHERE ticker = %s OR ticker = %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (norm_sym, alt_sym)
            )
            row = cursor.fetchone()

        real_sym = row["Symbol"] if row else norm_sym
        mcap = get_symbol_market_cap(real_sym)

        return {
            "symbol": real_sym,
            "close": float(row["Close"]) if row and row.get("Close") is not None else None,
            "open": float(row["Open"]) if row and row.get("Open") is not None else None,
            "high": float(row["High"]) if row and row.get("High") is not None else None,
            "low": float(row["Low"]) if row and row.get("Low") is not None else None,
            "volume": int(row["Volume"]) if row and row.get("Volume") is not None else None,
            "rs_rank": float(row["rs_rank"]) if row and row.get("rs_rank") is not None else None,
            "market_cap_cr": mcap,
            "timestamp": row["Timestamp"].strftime("%Y-%m-%d") if row and hasattr(row["Timestamp"], "strftime") else str(row["Timestamp"]) if row else None
        }
    finally:
        cursor.close()
        conn.close()


def _get_user_id(user) -> int:
    if isinstance(user, dict):
        return int(user["id"])
    return int(getattr(user, "id"))


@router.get("")
def get_watchlist(current_user: dict = Depends(get_current_user)):
    user_id = _get_user_id(current_user)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT symbol, created_at
            FROM user_watchlist
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    results = []
    for r in rows:
        sym = r["symbol"]
        details = _get_symbol_details(sym)
        created_str = r["created_at"].strftime("%Y-%m-%d %H:%M") if hasattr(r["created_at"], "strftime") else str(r["created_at"])
        details["added_at"] = created_str
        results.append(details)

    return JSONResponse(content=results)


@router.get("/symbols")
def get_watchlist_symbols(current_user: dict = Depends(get_current_user)):
    user_id = _get_user_id(current_user)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT symbol FROM user_watchlist WHERE user_id = %s",
            (user_id,)
        )
        rows = cursor.fetchall()
        symbols = [r["symbol"] for r in rows]
        return JSONResponse(content={"symbols": symbols})
    finally:
        cursor.close()
        conn.close()


@router.post("")
def add_to_watchlist(body: WatchlistAddRequest, current_user: dict = Depends(get_current_user)):
    user_id = _get_user_id(current_user)
    sym = _normalize_symbol(body.symbol)

    details = _get_symbol_details(sym)
    canonical_symbol = details["symbol"]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO user_watchlist (user_id, symbol)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE symbol = symbol
            """,
            (user_id, canonical_symbol)
        )
        conn.commit()
    except Exception as exc:
        logger.exception("Error adding to watchlist")
        raise HTTPException(status_code=500, detail="Failed to add to watchlist.")
    finally:
        cursor.close()
        conn.close()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"message": "Added to watchlist", "item": details, "symbol": canonical_symbol}
    )


@router.delete("/{symbol}")
def remove_from_watchlist(symbol: str, current_user: dict = Depends(get_current_user)):
    user_id = _get_user_id(current_user)
    norm_sym = _normalize_symbol(symbol)
    alt_sym = norm_sym[:-3] if norm_sym.endswith(".NS") else f"{norm_sym}.NS"

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            DELETE FROM user_watchlist
            WHERE user_id = %s AND (symbol = %s OR symbol = %s)
            """,
            (user_id, norm_sym, alt_sym)
        )
        conn.commit()
        deleted_count = cursor.rowcount
    except Exception as exc:
        logger.exception("Error removing from watchlist")
        raise HTTPException(status_code=500, detail="Failed to remove from watchlist.")
    finally:
        cursor.close()
        conn.close()

    return JSONResponse(content={"message": "Removed from watchlist", "symbol": norm_sym, "deleted": deleted_count > 0})
