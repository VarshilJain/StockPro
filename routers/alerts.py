"""
routers/alerts.py — Price Alert System

Users can set price alerts on stocks. Alerts are checked during the nightly pipeline
and surfaced on the dashboard via the notifications system.

Endpoints:
  GET    /api/alerts          — list user's active alerts
  POST   /api/alerts          — create a new price alert
  DELETE /api/alerts/{id}     — delete an alert
  GET    /api/alerts/triggered — list recently triggered alerts
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import get_current_user, verify_csrf
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["Price Alerts"])

MAX_ALERTS_PER_USER = 25


class CreateAlertBody(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=50)
    condition: str = Field(..., description="One of: above, below, crosses_above, crosses_below")
    target_price: float = Field(..., gt=0, description="Target price in INR")
    note: Optional[str] = Field(None, max_length=200)


def _normalize_symbol(sym: str) -> str:
    return sym.strip().upper()


def _serialize_alert(row: dict) -> dict:
    for key in ("created_at", "triggered_at"):
        if key in row and hasattr(row[key], "isoformat"):
            row[key] = row[key].isoformat()
    if "target_price" in row:
        row["target_price"] = float(row["target_price"])
    return row


@router.get("")
def list_alerts(
    triggered: Optional[bool] = Query(None, description="Filter by triggered status"),
    current_user: dict = Depends(get_current_user),
):
    """List all price alerts for the current user."""
    user_id = current_user["id"]
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        query = "SELECT * FROM user_price_alerts WHERE user_id = %s"
        params = [user_id]
        if triggered is True:
            query += " AND is_triggered = TRUE"
        elif triggered is False:
            query += " AND is_triggered = FALSE"
        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Enrich with latest price if available
        symbols = list({r["symbol"] for r in rows})
        price_map = {}
        if symbols:
            try:
                placeholders = ",".join(["%s"] * len(symbols))
                cursor.execute(
                    f"""
                    SELECT h.Symbol, h.Close
                    FROM historical_data h
                    INNER JOIN (
                        SELECT Symbol, MAX(Timestamp) as max_ts
                        FROM historical_data
                        WHERE Symbol IN ({placeholders})
                        GROUP BY Symbol
                    ) m ON h.Symbol = m.Symbol AND h.Timestamp = m.max_ts
                    """,
                    symbols,
                )
                for pr in cursor.fetchall():
                    price_map[pr["Symbol"]] = float(pr["Close"])
            except Exception as e:
                logger.warning("Could not fetch current prices for alerts: %s", e)

        newly_triggered_ids = []
        serialized = []
        for r in rows:
            item = _serialize_alert(r)
            curr = price_map.get(item["symbol"])
            item["current_price"] = curr
            target = item.get("target_price")
            if curr and target:
                item["diff_pct"] = round(((target - curr) / curr) * 100, 2)
                # Auto-evaluate trigger condition for active alerts
                if not item.get("is_triggered"):
                    cond = item.get("condition")
                    is_trig = False
                    if cond in ("above", "crosses_above") and curr >= target:
                        is_trig = True
                    elif cond in ("below", "crosses_below") and curr <= target:
                        is_trig = True

                    if is_trig:
                        item["is_triggered"] = True
                        item["triggered_at"] = datetime.now().isoformat()
                        newly_triggered_ids.append(item["id"])
            else:
                item["diff_pct"] = None
            serialized.append(item)

        if newly_triggered_ids:
            try:
                id_placeholders = ",".join(["%s"] * len(newly_triggered_ids))
                cursor.execute(
                    f"UPDATE user_price_alerts SET is_triggered = TRUE, triggered_at = NOW() WHERE id IN ({id_placeholders})",
                    newly_triggered_ids,
                )
                conn.commit()
            except Exception as e:
                logger.warning("Failed to persist triggered alerts: %s", e)
    finally:
        cursor.close()
        conn.close()

    return JSONResponse(content=serialized)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_alert(
    body: CreateAlertBody,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Create a new price alert."""
    valid_conditions = ("above", "below", "crosses_above", "crosses_below")
    if body.condition not in valid_conditions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid condition '{body.condition}'. Must be one of: {valid_conditions}",
        )

    user_id = current_user["id"]
    symbol = _normalize_symbol(body.symbol)

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Check alert count
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM user_price_alerts WHERE user_id = %s AND is_triggered = FALSE",
            (user_id,),
        )
        cnt = cursor.fetchone()["cnt"]
        if cnt >= MAX_ALERTS_PER_USER:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum of {MAX_ALERTS_PER_USER} active alerts. Remove some before adding new ones.",
            )

        # Check for duplicate
        cursor.execute(
            """
            SELECT id FROM user_price_alerts
            WHERE user_id = %s AND symbol = %s AND `condition` = %s
              AND target_price = %s AND is_triggered = FALSE
            """,
            (user_id, symbol, body.condition, body.target_price),
        )
        if cursor.fetchone():
            raise HTTPException(
                status_code=409,
                detail="An identical active alert already exists.",
            )

        cursor.execute(
            """
            INSERT INTO user_price_alerts (user_id, symbol, `condition`, target_price, note)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, symbol, body.condition, body.target_price, body.note),
        )
        conn.commit()
        new_id = cursor.lastrowid

        # Fetch and return the created alert
        cursor.execute("SELECT * FROM user_price_alerts WHERE id = %s", (new_id,))
        created = cursor.fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error creating alert for user %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to create alert.")
    finally:
        cursor.close()
        conn.close()

    return JSONResponse(
        status_code=201,
        content={"message": "Alert created", "alert": _serialize_alert(created)},
    )


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Delete a price alert. Only the owning user may delete."""
    user_id = current_user["id"]
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, user_id FROM user_price_alerts WHERE id = %s", (alert_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found.")
        if row["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You do not own this alert.")
        cursor.execute("DELETE FROM user_price_alerts WHERE id = %s", (alert_id,))
        conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error deleting alert %s", alert_id)
        raise HTTPException(status_code=500, detail="Failed to delete alert.")
    finally:
        cursor.close()
        conn.close()


@router.get("/triggered")
def get_triggered_alerts(current_user: dict = Depends(get_current_user)):
    """Get recently triggered alerts (last 30 days)."""
    user_id = current_user["id"]
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT * FROM user_price_alerts
            WHERE user_id = %s AND is_triggered = TRUE
              AND triggered_at >= NOW() - INTERVAL 30 DAY
            ORDER BY triggered_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    return JSONResponse(content=[_serialize_alert(r) for r in rows])
