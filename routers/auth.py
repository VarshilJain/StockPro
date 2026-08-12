"""
routers/auth.py — Registration, login, logout, and current-user endpoints.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from config import ACCESS_TOKEN_EXPIRE_HOURS
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# ──────────────────────────────────────────────
# Cookie config
# ──────────────────────────────────────────────
COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = ACCESS_TOKEN_EXPIRE_HOURS * 3600  # seconds


# ──────────────────────────────────────────────
# Request / Response schemas
# ──────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Password must contain at least one letter.")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit.")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty.")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ──────────────────────────────────────────────
# Simple in-memory rate limiter for login
# (5 attempts / 60 s per IP)
# ──────────────────────────────────────────────
import time
from collections import defaultdict

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 5        # max attempts
_RATE_WINDOW = 60.0    # seconds


def _check_login_rate_limit(ip: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts
    if len(attempts) > _RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait a minute and try again.",
        )


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    """Create a new user account."""
    hashed = hash_password(body.password)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (email, hashed_password, name) VALUES (%s, %s, %s)",
            (body.email.lower(), hashed, body.name.strip()),
        )
        new_user_id = cursor.lastrowid

        # Seed the 5 default strategies for the new user
        default_strategies = [
            ("1. Institutional Delivery & Smart-Money Accumulation", json.dumps({"logic": "AND", "conditions": [{"field": "delivery_momentum_signal", "operator": "=="}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("2. Momentum & Multi-Year Breakout", json.dumps({"logic": "AND", "conditions": [{"field": "new_52w_high", "operator": "=="}, {"field": "RCS_30D", "operator": ">", "value": 0}, {"field": "adx_trigger", "operator": "=="}]})),
            ("3. Volatility Contraction (VCP) & Narrow Range", json.dumps({"logic": "AND", "conditions": [{"field": "NR", "operator": ">", "value": 6}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("4. High-Probability Reversal & Dip Buying", json.dumps({"logic": "AND", "conditions": [{"field": "oversold", "operator": "=="}, {"field": "Hammer", "operator": "=="}]})),
            ("5. EMA Ribbon Convergence & Golden Cross", json.dumps({"logic": "AND", "conditions": [{"field": "convergence_5a", "operator": "=="}, {"field": "adx_trigger", "operator": "=="}]})),
        ]
        for name, conds in default_strategies:
            cursor.execute(
                "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
                (new_user_id, name, conds)
            )

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as exc:
        err = str(exc)
        if "Duplicate entry" in err or "1062" in err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )
        logger.exception("Error during registration")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"message": "Account created successfully. Please log in."}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate and set an httpOnly JWT cookie."""
    ip = request.client.host if request.client else "unknown"
    _check_login_rate_limit(ip)

    # Look up user
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, hashed_password, is_active FROM users WHERE email = %s",
            (body.email.lower(),),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception:
        logger.exception("DB error during login")
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

    if user is None or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    token = create_access_token(
        {"sub": str(user["id"])},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=False,   # Set to True in production behind HTTPS
        path="/",
    )
    return {"message": "Login successful."}


@router.post("/logout")
def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"message": "Logged out."}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """Return the currently authenticated user's public info."""
    user = dict(current_user)
    # Serialize datetime fields
    if "created_at" in user and hasattr(user["created_at"], "isoformat"):
        user["created_at"] = user["created_at"].isoformat()
    return {
        "id":         user["id"],
        "email":      user["email"],
        "name":       user["name"],
        "created_at": user.get("created_at"),
    }
