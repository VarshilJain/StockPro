"""
routers/scans.py — CRUD endpoints for saved scans + ad-hoc and saved-scan execution.

All endpoints are auth-guarded via Depends(get_current_user).
State-changing endpoints are protected via Depends(verify_csrf).

Route order matters — /run must come before /{id} so FastAPI doesn't try
to cast the literal string "run" as an integer id.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from auth import get_current_user, verify_csrf
from database import get_connection
from scan_engine import run_scan, validate_conditions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scans", tags=["Scans"])


# ──────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────

class ConditionItem(BaseModel):
    field: str
    operator: str
    value: Optional[int | float | str] = None


class ScanBody(BaseModel):
    """Body for ad-hoc /run and for saving a scan."""
    logic: str = "AND"
    conditions: list[ConditionItem] = Field(..., min_length=1, max_length=20)
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("logic")
    @classmethod
    def logic_valid(cls, v: str) -> str:
        if v not in ("AND", "OR"):
            raise ValueError("logic must be 'AND' or 'OR'")
        return v


class SaveScanBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    logic: str = "AND"
    conditions: list[ConditionItem] = Field(..., min_length=1, max_length=20)
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Scan name must not be empty.")
        return v


def _body_to_conditions_dict(body: ScanBody | SaveScanBody) -> dict:
    """Convert a Pydantic body into the raw dict expected by validate_conditions/run_scan."""
    return {
        "logic": body.logic,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "conditions": [
            {k: v for k, v in c.model_dump().items() if v is not None}
            for c in body.conditions
        ],
    }


def _serialize_scan_row(row: dict) -> dict:
    """Ensure datetime fields in a saved_scans row are JSON-serialisable."""
    for key in ("created_at", "updated_at"):
        if key in row and hasattr(row[key], "isoformat"):
            row[key] = row[key].isoformat()
    if "conditions" in row and isinstance(row["conditions"], str):
        row["conditions"] = json.loads(row["conditions"])
    return row


# ──────────────────────────────────────────────────────────────
# Ad-hoc run — POST /api/scans/run
# Must be registered BEFORE /{id} routes.
# ──────────────────────────────────────────────────────────────

@router.post("/run")
def run_scan_adhoc(
    body: ScanBody,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """
    Execute a scan from a conditions JSON body without saving it.
    Useful for "test before save" flows.
    """
    raw = _body_to_conditions_dict(body)
    validate_conditions(raw)

    try:
        rows = run_scan(
            conditions=raw["conditions"],
            logic=raw["logic"],
            start_date=raw.get("start_date"),
            end_date=raw.get("end_date"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("run_scan_adhoc error for user_id=%s", current_user["id"])
        raise HTTPException(status_code=500, detail="Failed to execute scan.")

    return JSONResponse(content={"count": len(rows), "results": rows})


# ──────────────────────────────────────────────────────────────
# CRUD — saved scans
# ──────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
def create_scan(
    body: SaveScanBody,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Save a new scan definition for the current user."""
    raw = _body_to_conditions_dict(body)
    validate_conditions(raw)

    conditions_json = json.dumps(raw)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
            (current_user["id"], body.name.strip(), conditions_json),
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("create_scan DB error for user_id=%s", current_user["id"])
        raise HTTPException(status_code=500, detail="Failed to save scan.")

    return {"id": new_id, "message": "Scan saved successfully."}


@router.get("")
def list_scans(current_user: dict = Depends(get_current_user)):
    """List all saved scans belonging to the current user."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, created_at, updated_at FROM saved_scans "
            "WHERE user_id = %s ORDER BY updated_at DESC",
            (current_user["id"],),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("list_scans DB error for user_id=%s", current_user["id"])
        raise HTTPException(status_code=500, detail="Failed to list scans.")

    return [_serialize_scan_row(r) for r in rows]


@router.get("/{scan_id}")
def get_scan(
    scan_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return the full definition of a single saved scan."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM saved_scans WHERE id = %s AND user_id = %s",
            (scan_id, current_user["id"]),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("get_scan DB error for user_id=%s scan_id=%s", current_user["id"], scan_id)
        raise HTTPException(status_code=500, detail="Failed to fetch scan.")

    if row is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    return _serialize_scan_row(row)


@router.put("/{scan_id}")
def update_scan(
    scan_id: int,
    body: SaveScanBody,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Update a saved scan's name and/or conditions. 403 if not the owner."""
    raw = _body_to_conditions_dict(body)
    validate_conditions(raw)

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        # Ownership check
        cursor.execute("SELECT user_id FROM saved_scans WHERE id = %s", (scan_id,))
        existing = cursor.fetchone()
        if existing is None:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Scan not found.")
        if existing["user_id"] != current_user["id"]:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=403, detail="You do not own this scan.")

        cursor.execute(
            "UPDATE saved_scans SET name = %s, conditions = %s WHERE id = %s",
            (body.name.strip(), json.dumps(raw), scan_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("update_scan DB error for user_id=%s scan_id=%s", current_user["id"], scan_id)
        raise HTTPException(status_code=500, detail="Failed to update scan.")

    return {"message": "Scan updated successfully."}


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scan(
    scan_id: int,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Delete a saved scan. 403 if not the owner."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM saved_scans WHERE id = %s", (scan_id,))
        existing = cursor.fetchone()
        if existing is None:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Scan not found.")
        if existing["user_id"] != current_user["id"]:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=403, detail="You do not own this scan.")

        cursor.execute("DELETE FROM saved_scans WHERE id = %s", (scan_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete_scan DB error for user_id=%s scan_id=%s", current_user["id"], scan_id)
        raise HTTPException(status_code=500, detail="Failed to delete scan.")


# ──────────────────────────────────────────────────────────────
# Run a saved scan — POST /api/scans/{id}/run
# ──────────────────────────────────────────────────────────────

@router.post("/{scan_id}/run")
def run_saved_scan(
    scan_id: int,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Load a saved scan's conditions and execute them."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT conditions FROM saved_scans WHERE id = %s AND user_id = %s",
            (scan_id, current_user["id"]),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.exception("run_saved_scan DB fetch error for user_id=%s scan_id=%s", current_user["id"], scan_id)
        raise HTTPException(status_code=500, detail="Failed to load scan.")

    if row is None:
        raise HTTPException(status_code=404, detail="Scan not found.")

    raw = row["conditions"]
    if isinstance(raw, str):
        raw = json.loads(raw)

    validate_conditions(raw)

    try:
        rows = run_scan(
            conditions=raw["conditions"],
            logic=raw.get("logic", "AND"),
            start_date=raw.get("start_date"),
            end_date=raw.get("end_date"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("run_saved_scan execution error for user_id=%s scan_id=%s", current_user["id"], scan_id)
        raise HTTPException(status_code=500, detail="Failed to execute scan.")

    return JSONResponse(content={"count": len(rows), "results": rows})

