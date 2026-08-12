"""
auth.py — Password hashing, JWT creation/decoding, and the get_current_user dependency.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt
import bcrypt as _bcrypt

from config import ACCESS_TOKEN_EXPIRE_HOURS, JWT_ALGORITHM, JWT_SECRET_KEY
from database import get_connection

logger = logging.getLogger(__name__)


def hash_password(plain: str) -> str:
    """Return bcrypt hash of *plain* password."""
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False



# ──────────────────────────────────────────────
# JWT helpers
# ──────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    payload.update({"exp": expire})
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns payload dict or None on failure."""
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        logger.debug("JWT decode failed: %s", exc)
        return None


# ──────────────────────────────────────────────
# FastAPI dependency
# ──────────────────────────────────────────────
_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(access_token: Optional[str] = Cookie(default=None)) -> dict:
    """
    FastAPI dependency.  Reads the httpOnly `access_token` cookie, validates the
    JWT, then fetches and returns the matching active user row from the DB.
    Raises 401 if the token is missing, invalid, expired, or the user is inactive.
    """
    if not access_token:
        raise _CREDENTIALS_EXCEPTION

    payload = decode_token(access_token)
    if payload is None:
        raise _CREDENTIALS_EXCEPTION

    user_id_str: Optional[str] = payload.get("sub")
    if user_id_str is None:
        raise _CREDENTIALS_EXCEPTION
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise _CREDENTIALS_EXCEPTION

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, email, name, is_active, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()
    except Exception as exc:
        logger.error("DB error in get_current_user: %s", exc)
        raise _CREDENTIALS_EXCEPTION

    if user is None or not user.get("is_active"):
        raise _CREDENTIALS_EXCEPTION

    return user
