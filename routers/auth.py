"""
routers/auth.py — Registration, login, logout, and current-user endpoints.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator

from auth import (
    CSRF_COOKIE_NAME,
    create_access_token,
    generate_csrf_token,
    get_current_user,
    hash_password,
    verify_password,
)
from config import ACCESS_TOKEN_EXPIRE_HOURS, COOKIE_SECURE
from database import get_connection
from rate_limiter import get_client_ip, login_rate_limiter

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
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long.")
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
        if len(v) > 128:
            raise ValueError("Name must be 128 characters or fewer.")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str


class ResetPasswordDirectRequest(BaseModel):
    email: EmailStr
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("New password must be at least 12 characters long.")
        if not re.search(r"[A-Za-z]", v):
            raise ValueError("New password must contain at least one letter.")
        if not re.search(r"\d", v):
            raise ValueError("New password must contain at least one digit.")
        return v



# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request):
    """Create a new user account."""
    ip = get_client_ip(request)
    hashed = hash_password(body.password)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (email, hashed_password, name, role, auth_provider) VALUES (%s, %s, %s, %s, %s)",
            (body.email.lower(), hashed, body.name.strip(), "user", "local"),
        )
        new_user_id = cursor.lastrowid

        # Seed the 5 default strategies for the new user
        default_strategies = [
            ("1. Institutional Delivery & Smart-Money Accumulation", json.dumps({"logic": "AND", "conditions": [{"field": "delivery_momentum_signal", "operator": "=="}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("2. Momentum & Multi-Year Breakout", json.dumps({"logic": "AND", "conditions": [{"field": "new_52w_high", "operator": "=="}, {"field": "RCS_30D", "operator": ">", "value": 0}, {"field": "adx_trigger", "operator": "=="}]})),
            ("3. Volatility Contraction (VCP) & Narrow Range", json.dumps({"logic": "AND", "conditions": [{"field": "NR", "operator": ">", "value": 6}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("4. High-Probability Reversal & Dip Buying", json.dumps({"logic": "AND", "conditions": [{"field": "oversold", "operator": "=="}, {"field": "Hammer", "operator": "=="}]})),
            ("5. EMA Ribbon Convergence & Golden Cross", json.dumps({"logic": "AND", "conditions": [{"field": "convergence_5", "operator": "=="}, {"field": "adx_trigger", "operator": "=="}]})),
        ]
        for name, conds in default_strategies:
            cursor.execute(
                "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
                (new_user_id, name, conds)
            )

        conn.commit()
        cursor.close()
        conn.close()
        logger.info("SECURITY_EVENT: AUTH_REGISTER_SUCCESS user_id=%s, email=%s, ip=%s", new_user_id, body.email.lower(), ip)
    except Exception as exc:
        err = str(exc)
        if "Duplicate entry" in err or "1062" in err:
            logger.warning("SECURITY_EVENT: AUTH_REGISTER_FAILED email=%s already exists, ip=%s", body.email.lower(), ip)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )
        logger.exception("Error during registration")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"message": "Account created successfully. Please log in."}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate, record rate limits, and set httpOnly auth and CSRF cookies."""
    ip = get_client_ip(request)
    login_rate_limiter.check_and_record(request, email=body.email)

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
        logger.exception("DB error during login for email: %s", body.email.lower())
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

    if user is None or not user.get("hashed_password") or not verify_password(body.password, user["hashed_password"]):
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_FAILED email=%s, ip=%s, reason=invalid_credentials", body.email.lower(), ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if not user.get("is_active"):
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_FAILED email=%s, ip=%s, reason=account_deactivated", body.email.lower(), ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    # Reset account failure attempts
    login_rate_limiter.reset_account(body.email)

    token = create_access_token(
        {"sub": str(user["id"])},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )
    csrf_token = generate_csrf_token()

    # Set httpOnly JWT session cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    # Set frontend-accessible CSRF cookie
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    logger.info("SECURITY_EVENT: AUTH_LOGIN_SUCCESS user_id=%s, email=%s, ip=%s", user["id"], body.email.lower(), ip)
    return {"message": "Login successful.", "csrf_token": csrf_token}


@router.post("/google")
def google_login(body: GoogleAuthRequest, request: Request, response: Response):
    """
    Authenticate or auto-provision a user using Google Identity Services ID Token.
    """
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    from config import GOOGLE_CLIENT_ID

    ip = get_client_ip(request)
    
    # 1. Verify Google ID token
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv(override=True)
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()

        req = google_requests.Request()
        audience = client_id if client_id else None
        id_info = id_token.verify_oauth2_token(
            body.credential,
            req,
            audience=audience,
            clock_skew_in_seconds=60
        )
        
        email = id_info.get("email", "").strip().lower()
        name = id_info.get("name", "").strip() or email.split("@")[0]
        google_id = id_info.get("sub")
        email_verified = id_info.get("email_verified", False)

        if not email or not email_verified:
            logger.warning("SECURITY_EVENT: GOOGLE_AUTH_REJECTED email=%s, verified=%s, ip=%s", email, email_verified, ip)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google email is unverified or missing.")

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("SECURITY_EVENT: GOOGLE_TOKEN_INVALID exc=%s, ip=%s, audience=%s", exc, ip, audience)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Google authentication token: {exc}")

    # 2. Lookup or Provision User in Database
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, email, name, role, is_active FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user is None:
            # Auto-register new Google user
            cursor.execute(
                "INSERT INTO users (email, name, role, google_id, auth_provider, is_active) VALUES (%s, %s, 'user', %s, 'google', 1)",
                (email, name, google_id),
            )
            user_id = cursor.lastrowid

            # Seed default strategies
            default_strategies = [
                ("1. Institutional Delivery & Smart-Money Accumulation", json.dumps({"logic": "AND", "conditions": [{"field": "delivery_momentum_signal", "operator": "=="}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
                ("2. Momentum & Multi-Year Breakout", json.dumps({"logic": "AND", "conditions": [{"field": "new_52w_high", "operator": "=="}, {"field": "RCS_30D", "operator": ">", "value": 0}, {"field": "adx_trigger", "operator": "=="}]})),
                ("3. Volatility Contraction (VCP) & Narrow Range", json.dumps({"logic": "AND", "conditions": [{"field": "NR", "operator": ">", "value": 6}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
                ("4. High-Probability Reversal & Dip Buying", json.dumps({"logic": "AND", "conditions": [{"field": "oversold", "operator": "=="}, {"field": "Hammer", "operator": "=="}]})),
                ("5. EMA Ribbon Convergence & Golden Cross", json.dumps({"logic": "AND", "conditions": [{"field": "convergence_5", "operator": "=="}, {"field": "adx_trigger", "operator": "=="}]})),
            ]
            for s_name, conds in default_strategies:
                cursor.execute(
                    "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
                    (user_id, s_name, conds)
                )
            conn.commit()
            role = "user"
            logger.info("SECURITY_EVENT: GOOGLE_AUTH_PROVISION_SUCCESS user_id=%s, email=%s, ip=%s", user_id, email, ip)
        else:
            user_id = user["id"]
            role = user.get("role", "user")
            if not user.get("is_active"):
                cursor.close(); conn.close()
                logger.warning("SECURITY_EVENT: GOOGLE_AUTH_FAILED user_id=%s, email=%s, reason=deactivated, ip=%s", user_id, email, ip)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")
            
            # Update google_id if missing
            cursor.execute("UPDATE users SET google_id = %s WHERE id = %s AND (google_id IS NULL OR google_id = '')", (google_id, user_id))
            conn.commit()
            logger.info("SECURITY_EVENT: GOOGLE_AUTH_LOGIN_SUCCESS user_id=%s, email=%s, ip=%s", user_id, email, ip)

        cursor.close()
        conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("DB error during Google authentication for email=%s", email)
        raise HTTPException(status_code=500, detail="Authentication failed. Please try again.")

    # 3. Create Session Token & CSRF Token
    token = create_access_token(
        {"sub": str(user_id)},
        expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )
    csrf_token = generate_csrf_token()

    # Set httpOnly JWT session cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    # Set frontend-accessible CSRF cookie
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=COOKIE_MAX_AGE,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    return {
        "message": "Google authentication successful.",
        "csrf_token": csrf_token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "role": role,
        },
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    """Clear both auth and CSRF cookies."""
    ip = get_client_ip(request)
    response.delete_cookie(key=COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="lax")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="lax")
    logger.info("SECURITY_EVENT: AUTH_LOGOUT ip=%s", ip)
    return {"message": "Logged out."}


@router.get("/config")
def auth_config():
    """Return public auth config like Google Client ID for GIS frontend initialization."""
    import os
    from dotenv import load_dotenv
    load_dotenv(override=True)
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    return {
        "google_client_id": client_id,
    }


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
        "role":       user.get("role", "user"),
        "created_at": user.get("created_at"),
    }


@router.post("/reset-password")
def reset_password_direct(body: ResetPasswordDirectRequest, request: Request):
    """
    Direct password reset requiring email, current password, and new password.
    """
    ip = get_client_ip(request)
    login_rate_limiter.check_and_record(request, email=body.email)

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, email, hashed_password, is_active FROM users WHERE email = %s",
            (body.email.lower(),),
        )
        user = cursor.fetchone()

        if user is None or not user.get("hashed_password") or not verify_password(body.current_password, user["hashed_password"]):
            cursor.close()
            conn.close()
            logger.warning("SECURITY_EVENT: PASSWORD_RESET_FAILED email=%s, ip=%s, reason=invalid_credentials", body.email.lower(), ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password or email is incorrect.",
            )

        if not user.get("is_active"):
            cursor.close()
            conn.close()
            logger.warning("SECURITY_EVENT: PASSWORD_RESET_FAILED email=%s, ip=%s, reason=deactivated", body.email.lower(), ip)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has been deactivated.",
            )

        # Update password hash
        new_hash = hash_password(body.new_password)
        cursor.execute("UPDATE users SET hashed_password = %s WHERE id = %s", (new_hash, user["id"]))
        conn.commit()
        cursor.close()
        conn.close()

        login_rate_limiter.reset_account(body.email)
        logger.info("SECURITY_EVENT: PASSWORD_RESET_SUCCESS user_id=%s, email=%s, ip=%s", user["id"], body.email.lower(), ip)
        return {"message": "Password updated successfully. You can now sign in with your new password."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("DB error during direct password reset for email: %s", body.email.lower())
        raise HTTPException(status_code=500, detail="Password update failed. Please try again.")



