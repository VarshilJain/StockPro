"""
routers/auth.py — Registration, login, logout, and current-user endpoints.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator

from auth import (
    CSRF_COOKIE_NAME,
    create_access_token,
    decode_token,
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
REMEMBER_ME_DAYS = 30
REMEMBER_ME_COOKIE_MAX_AGE = REMEMBER_ME_DAYS * 86400  # seconds (30 days)


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
    remember_me: bool = False


class GoogleAuthRequest(BaseModel):
    credential: str
    remember_me: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: Optional[str] = None
    email: Optional[EmailStr] = None
    current_password: Optional[str] = None
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


class ResetPasswordDirectRequest(ResetPasswordRequest):
    pass


ResetPasswordRequest.model_rebuild()
ResetPasswordDirectRequest.model_rebuild()


class ChangePasswordRequest(BaseModel):
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
    """Create a new user account with pending admin approval."""
    ip = get_client_ip(request)
    hashed = hash_password(body.password)
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (email, hashed_password, name, role, auth_provider, is_active, approval_status, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            """,
            (body.email.lower(), hashed, body.name.strip(), "user", "local", 0, "pending"),
        )
        new_user_id = cursor.lastrowid

        # Seed default strategies for the new user
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
        logger.info("SECURITY_EVENT: AUTH_REGISTER_PENDING_APPROVAL user_id=%s, email=%s, ip=%s", new_user_id, body.email.lower(), ip)
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

    return {
        "message": "Registration submitted successfully! Your account request is pending admin approval. You will receive access once approved.",
        "approval_status": "pending",
        "user_id": new_user_id,
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    """Authenticate, verify approval and 365-day validity, and set cookies."""
    ip = get_client_ip(request)
    login_rate_limiter.check_and_record(request, email=body.email)

    # Look up user
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, hashed_password, is_active, approval_status, expires_at, token_version, role FROM users WHERE email = %s",
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

    # 1. Admin Approval check
    if user.get("approval_status") == "pending":
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_BLOCKED email=%s, ip=%s, reason=pending_approval", body.email.lower(), ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account registration is pending admin approval. You will receive access once approved by an administrator.",
        )
    if user.get("approval_status") == "rejected":
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_BLOCKED email=%s, ip=%s, reason=rejected_registration", body.email.lower(), ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account registration request has been rejected by an administrator.",
        )

    # 2. Block / Deactivate check
    if not user.get("is_active"):
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_FAILED email=%s, ip=%s, reason=account_blocked", body.email.lower(), ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been blocked or deactivated. Please contact an administrator.",
        )

    # 3. 365-Day Validity Expiration check
    if user.get("expires_at") and user["expires_at"] < datetime.now():
        logger.warning("SECURITY_EVENT: AUTH_LOGIN_FAILED email=%s, ip=%s, reason=account_expired, expired_at=%s", body.email.lower(), ip, user["expires_at"])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your 365-day access validity has expired. Please contact an administrator to renew access.",
        )

    # Reset account failure attempts
    login_rate_limiter.reset_account(body.email)

    # Atomic session update with exclusive row lock to serialize concurrent logins safely
    new_session_id = secrets.token_hex(16)
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        conn.start_transaction()
        cursor.execute("SELECT id, token_version FROM users WHERE id = %s FOR UPDATE", (user["id"],))
        lock_row = cursor.fetchone()
        new_token_version = ((lock_row.get("token_version") if lock_row else user.get("token_version")) or 0) + 1
        cursor.execute(
            """
            UPDATE users
            SET active_session_id = %s, token_version = %s, last_login_at = NOW(), last_login_ip = %s
            WHERE id = %s
            """,
            (new_session_id, new_token_version, ip, user["id"]),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        logger.exception("DB error updating active_session_id during login for user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="Login failed. Please try again.")

    if body.remember_me:
        token_expires = timedelta(days=REMEMBER_ME_DAYS)
        cookie_max_age = REMEMBER_ME_COOKIE_MAX_AGE
    else:
        token_expires = timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        cookie_max_age = COOKIE_MAX_AGE

    token = create_access_token(
        {"sub": str(user["id"]), "sid": new_session_id, "ver": new_token_version},
        expires_delta=token_expires,
    )
    csrf_token = generate_csrf_token()

    # Set httpOnly JWT session cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=cookie_max_age,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    # Set frontend-accessible CSRF cookie
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=cookie_max_age,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    user_role = user.get("role", "user")
    logger.info("SECURITY_EVENT: AUTH_LOGIN_SUCCESS user_id=%s, email=%s, role=%s, ip=%s", user["id"], body.email.lower(), user_role, ip)
    return {"message": "Login successful.", "csrf_token": csrf_token, "role": user_role}


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
        cursor.execute("SELECT id, email, name, role, is_active, approval_status, expires_at FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user is None:
            # Auto-register new Google verified user with 365-day validity
            cursor.execute(
                """
                INSERT INTO users (email, name, role, google_id, auth_provider, is_active, approval_status, expires_at)
                VALUES (%s, %s, 'user', %s, 'google', 1, 'approved', DATE_ADD(NOW(), INTERVAL 365 DAY))
                """,
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

            if user.get("approval_status") == "pending":
                cursor.close(); conn.close()
                logger.warning("SECURITY_EVENT: GOOGLE_AUTH_PENDING user_id=%s, email=%s, ip=%s", user_id, email, ip)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is pending admin approval.")

            if user.get("approval_status") == "rejected":
                cursor.close(); conn.close()
                logger.warning("SECURITY_EVENT: GOOGLE_AUTH_REJECTED user_id=%s, email=%s, ip=%s", user_id, email, ip)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account registration request has been rejected by an administrator.")

            if not user.get("is_active"):
                cursor.close(); conn.close()
                logger.warning("SECURITY_EVENT: GOOGLE_AUTH_FAILED user_id=%s, email=%s, reason=deactivated, ip=%s", user_id, email, ip)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been blocked or deactivated.")

            if user.get("expires_at") and user["expires_at"] < datetime.now():
                cursor.close(); conn.close()
                logger.warning("SECURITY_EVENT: GOOGLE_AUTH_EXPIRED user_id=%s, email=%s, reason=expired, ip=%s", user_id, email, ip)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your 365-day access validity has expired.")
            
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

    # 3. Create Session Token & CSRF Token with atomic session update
    new_session_id = secrets.token_hex(16)
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        conn.start_transaction()
        cursor.execute("SELECT id, token_version FROM users WHERE id = %s FOR UPDATE", (user_id,))
        lock_row = cursor.fetchone()
        new_token_version = ((lock_row.get("token_version") if lock_row else 0) or 0) + 1
        cursor.execute(
            """
            UPDATE users
            SET active_session_id = %s, token_version = %s, last_login_at = NOW(), last_login_ip = %s
            WHERE id = %s
            """,
            (new_session_id, new_token_version, ip, user_id),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        logger.exception("DB error updating active_session_id during Google login for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Authentication failed. Please try again.")

    if body.remember_me:
        token_expires = timedelta(days=REMEMBER_ME_DAYS)
        cookie_max_age = REMEMBER_ME_COOKIE_MAX_AGE
    else:
        token_expires = timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        cookie_max_age = COOKIE_MAX_AGE

    token = create_access_token(
        {"sub": str(user_id), "sid": new_session_id, "ver": new_token_version},
        expires_delta=token_expires,
    )
    csrf_token = generate_csrf_token()

    # Set httpOnly JWT session cookie
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=cookie_max_age,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )

    # Set frontend-accessible CSRF cookie
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=cookie_max_age,
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
def logout(request: Request, response: Response, access_token: Optional[str] = Cookie(default=None)):
    """Clear both auth and CSRF cookies, and immediately invalidate the active session in DB."""
    ip = get_client_ip(request)
    if access_token:
        try:
            payload = decode_token(access_token)
            if payload and payload.get("sub"):
                user_id = int(payload["sub"])
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET active_session_id = NULL, token_version = token_version + 1 WHERE id = %s",
                    (user_id,),
                )
                conn.commit()
                cursor.close()
                conn.close()
                logger.info("SECURITY_EVENT: AUTH_LOGOUT user_id=%s, ip=%s", user_id, ip)
        except Exception:
            logger.exception("Error clearing active_session_id on logout")

    response.delete_cookie(key=COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="lax")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", secure=COOKIE_SECURE, samesite="lax")
    return {"message": "Logged out."}


@router.post("/refresh")
def refresh_token(request: Request, response: Response, current_user: dict = Depends(get_current_user)):
    """
    Refresh access token for the currently active session.
    Rejects requests from terminated/superseded sessions with HTTP 401 Unauthorized.
    """
    ip = get_client_ip(request)
    user_id = current_user["id"]
    active_sid = current_user.get("active_session_id")
    token_ver = current_user.get("token_version", 1)

    token_expires = timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    cookie_max_age = COOKIE_MAX_AGE
    new_token = create_access_token(
        {"sub": str(user_id), "sid": active_sid, "ver": token_ver},
        expires_delta=token_expires,
    )
    csrf_token = generate_csrf_token()

    response.set_cookie(
        key=COOKIE_NAME,
        value=new_token,
        httponly=True,
        max_age=cookie_max_age,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=cookie_max_age,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    logger.info("SECURITY_EVENT: AUTH_TOKEN_REFRESH user_id=%s, ip=%s", user_id, ip)
    return {"message": "Token refreshed successfully.", "csrf_token": csrf_token}


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
    """Return the currently authenticated user's public info including 365-day validity countdown."""
    user = dict(current_user)
    # Serialize datetime fields
    if "created_at" in user and hasattr(user["created_at"], "isoformat"):
        user["created_at"] = user["created_at"].isoformat()

    expires_at = user.get("expires_at")
    days_remaining = None
    if expires_at:
        now = datetime.now()
        diff = (expires_at - now).total_seconds() / 86400.0
        days_remaining = max(0, int(diff + 0.999))
        if hasattr(expires_at, "isoformat"):
            expires_at = expires_at.isoformat()
        else:
            expires_at = str(expires_at)
    elif user.get("role") == "admin":
        days_remaining = 365

    return {
        "id":             user["id"],
        "email":          user["email"],
        "name":           user["name"],
        "role":           user.get("role", "user"),
        "created_at":     user.get("created_at"),
        "expires_at":     expires_at,
        "days_remaining": days_remaining,
        "total_days":     365,
    }


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, request: Request):
    """
    Generate password reset link. Returns neutral response regardless of whether email exists.
    Link valid for 30 minutes. Single-use via token_version.
    """
    ip = get_client_ip(request)
    login_rate_limiter.check_and_record(request, email=body.email)

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, email, token_version, is_active FROM users WHERE email = %s", (body.email.lower(),))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and user.get("is_active"):
            reset_token = create_access_token(
                {
                    "sub": str(user["id"]),
                    "purpose": "password_reset",
                    "ver": user.get("token_version", 1),
                },
                expires_delta=timedelta(minutes=30),
            )
            logger.info("SECURITY_EVENT: PASSWORD_RESET_REQUESTED email=%s, ip=%s", body.email.lower(), ip)
            logger.info("PASSWORD_RESET_LINK: /reset-password.html?token=%s (for email=%s)", reset_token, body.email.lower())
    except Exception:
        logger.exception("Error processing forgot-password for email: %s", body.email.lower())

    return {
        "message": "If an account exists for that email, we've sent a reset link. It expires in 30 minutes."
    }


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, request: Request):
    """
    Password reset supporting either a single-use 30-min token (from email link)
    or direct reset with email + current_password (backward compatibility).
    """
    ip = get_client_ip(request)

    if body.token:
        # 1. Token-based reset from reset-password.html
        payload = decode_token(body.token)
        if not payload or payload.get("purpose") != "password_reset":
            logger.warning("SECURITY_EVENT: PASSWORD_RESET_TOKEN_INVALID ip=%s", ip)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset link. Please request a new one.",
            )
        user_id = payload.get("sub")
        token_ver = payload.get("ver", 1)

        try:
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, email, is_active, token_version FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()

            if not user or not user.get("is_active"):
                cursor.close()
                conn.close()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account not found or deactivated.")

            if user.get("token_version", 1) != token_ver:
                cursor.close()
                conn.close()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset link has already been used.")

            new_hash = hash_password(body.new_password)
            new_version = user.get("token_version", 1) + 1
            cursor.execute(
                "UPDATE users SET hashed_password = %s, token_version = %s, active_session_id = NULL WHERE id = %s",
                (new_hash, new_version, user["id"]),
            )
            conn.commit()
            cursor.close()
            conn.close()

            logger.info("SECURITY_EVENT: PASSWORD_RESET_TOKEN_SUCCESS user_id=%s, ip=%s", user["id"], ip)
            return {"message": "Password updated successfully. You can now sign in with your new password."}
        except HTTPException:
            raise
        except Exception:
            logger.exception("DB error during token password reset")
            raise HTTPException(status_code=500, detail="Password update failed. Please try again.")

    elif body.email and body.current_password:
        # 2. Direct reset with current password
        login_rate_limiter.check_and_record(request, email=body.email)
        try:
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, email, hashed_password, is_active, token_version FROM users WHERE email = %s",
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
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")

            new_hash = hash_password(body.new_password)
            new_ver = user.get("token_version", 1) + 1
            cursor.execute("UPDATE users SET hashed_password = %s, token_version = %s, active_session_id = NULL WHERE id = %s", (new_hash, new_ver, user["id"]))
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
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either a reset token or current password and email are required.",
        )


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, request: Request, current_user: dict = Depends(get_current_user)):
    """
    Authenticated change password endpoint from avatar menu.
    """
    user_id = current_user["id"]
    ip = get_client_ip(request)

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, hashed_password, token_version FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()

        if not user or not user.get("hashed_password") or not verify_password(body.current_password, user["hashed_password"]):
            cursor.close()
            conn.close()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect.")

        new_hash = hash_password(body.new_password)
        new_ver = user.get("token_version", 1) + 1
        cursor.execute("UPDATE users SET hashed_password = %s, token_version = %s, active_session_id = NULL WHERE id = %s", (new_hash, new_ver, user_id))
        conn.commit()
        cursor.close()
        conn.close()

        logger.info("SECURITY_EVENT: PASSWORD_CHANGED_SUCCESS user_id=%s, ip=%s", user_id, ip)
        return {"message": "Password changed successfully."}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error changing password for user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Failed to change password. Please try again.")




