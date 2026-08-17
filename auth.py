"""
auth.py — Password hashing, JWT creation/decoding, user RBAC, and CSRF protection.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
import bcrypt as _bcrypt

from config import ACCESS_TOKEN_EXPIRE_HOURS, JWT_ALGORITHM, JWT_SECRET_KEY
from database import get_connection

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def hash_password(plain: str) -> str:
    """Return bcrypt hash of *plain* password."""
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed* in constant time."""
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def generate_csrf_token() -> str:
    """Generate a cryptographically secure random CSRF token."""
    return secrets.token_urlsafe(32)


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
# FastAPI dependencies
# ──────────────────────────────────────────────
_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(access_token: Optional[str] = Cookie(default=None)) -> dict:
    """
    FastAPI dependency. Reads the httpOnly `access_token` cookie, validates the
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
        # Check columns to support installations before role migration
        try:
            cursor.execute(
                "SELECT id, email, name, role, is_active, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            user = cursor.fetchone()
        except Exception:
            cursor.execute(
                "SELECT id, email, name, is_active, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            user = cursor.fetchone()
            if user:
                user["role"] = "user"
        finally:
            cursor.close()
            conn.close()
    except Exception as exc:
        logger.error("DB error in get_current_user: %s", exc)
        raise _CREDENTIALS_EXCEPTION

    if user is None or not user.get("is_active"):
        raise _CREDENTIALS_EXCEPTION

    if "role" not in user or not user["role"]:
        user["role"] = "user"

    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency that enforces admin privileges.
    Raises 403 Forbidden for non-admin users.
    """
    if current_user.get("role") != "admin":
        logger.warning(
            "SECURITY_EVENT: Unauthorized admin access attempt by user_id=%s, email=%s",
            current_user.get("id"), current_user.get("email")
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required for this action.",
        )
    return current_user


def verify_csrf(
    request: Request,
    csrf_token_cookie: Optional[str] = Cookie(default=None, alias=CSRF_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None, alias="x-csrf-token"),
) -> None:
    """
    Dependency enforcing double-submit CSRF token validation on state-changing requests.
    Exempts safe HTTP methods (GET, HEAD, OPTIONS).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # Check header with fallback case
    header_token = x_csrf_token or request.headers.get("X-CSRF-Token")
    if not csrf_token_cookie or not header_token:
        logger.warning(
            "SECURITY_EVENT: Missing CSRF token on %s %s (cookie_present=%s, header_present=%s)",
            request.method, request.url.path, bool(csrf_token_cookie), bool(header_token)
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid. Please refresh the page.",
        )

    if not hmac.compare_digest(csrf_token_cookie, header_token):
        logger.warning(
            "SECURITY_EVENT: Invalid CSRF token on %s %s from IP %s",
            request.method, request.url.path, request.client.host if request.client else "unknown"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed.",
        )

