"""
admin_service.py — Data access layer and business logic for StockPro Admin Console.
Includes RBAC validation, audit logging, system health telemetry, and safeguards.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from database import get_connection
from auth import hash_password
import cache

logger = logging.getLogger("stockpro.admin")


def log_audit(
    admin_user: dict,
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Record an immutable admin action into admin_audit_logs."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        details_json = json.dumps(details or {})
        cur.execute(
            """
            INSERT INTO admin_audit_logs (admin_id, admin_email, action, target_type, target_id, details, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                admin_user.get("id", 0),
                admin_user.get("email", "unknown"),
                action,
                target_type,
                str(target_id) if target_id is not None else None,
                details_json,
                ip_address,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("Failed to write admin audit log: %s", exc)


def log_security_event(
    event_type: str,
    severity: str = "info",
    user_id: Optional[int] = None,
    email: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Record a security event into admin_security_events."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        details_json = json.dumps(details or {})
        cur.execute(
            """
            INSERT INTO admin_security_events (event_type, severity, user_id, email, ip_address, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (event_type, severity, user_id, email, ip_address, details_json),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("Failed to write security event log: %s", exc)


def get_overview_stats() -> dict:
    """Return high-level KPIs for admin dashboard overview."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # Users counts
        cur.execute("""
            SELECT 
                COUNT(*) as total, 
                SUM(CASE WHEN is_active=1 AND (approval_status='approved' OR approval_status IS NULL) THEN 1 ELSE 0 END) as active, 
                SUM(CASE WHEN role='admin' THEN 1 ELSE 0 END) as admins,
                SUM(CASE WHEN approval_status='pending' THEN 1 ELSE 0 END) as pending_approvals,
                SUM(CASE WHEN (approval_status='approved' OR approval_status IS NULL) AND expires_at < NOW() THEN 1 ELSE 0 END) as expired_users
            FROM users
        """)
        u_stats = cur.fetchone() or {"total": 0, "active": 0, "admins": 0, "pending_approvals": 0, "expired_users": 0}

        # Market data stats
        cur.execute("SELECT COUNT(DISTINCT ticker) as total_symbols, MAX(date) as max_date, COUNT(*) as ohlc_rows FROM ohlc_data")
        ohlc_stats = cur.fetchone() or {"total_symbols": 0, "max_date": None, "ohlc_rows": 0}

        cur.execute("SELECT COUNT(*) as hist_rows, MAX(Timestamp) as max_hist FROM historical_data")
        hist_stats = cur.fetchone() or {"hist_rows": 0, "max_hist": None}

        # Pattern events
        cur.execute("SELECT COUNT(*) as total_events FROM pattern_events")
        pe_stats = cur.fetchone() or {"total_events": 0}

        # Saved scans & Signals
        cur.execute("SELECT COUNT(*) as total_scans FROM saved_scans")
        scan_stats = cur.fetchone() or {"total_scans": 0}

        cur.execute("SELECT COUNT(*) as total_signals, SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) as active_signals FROM signal_registry")
        sig_stats = cur.fetchone() or {"total_signals": 0, "active_signals": 0}

        # Recent 8 audit logs
        cur.execute("SELECT * FROM admin_audit_logs ORDER BY created_at DESC LIMIT 8")
        recent_logs = cur.fetchall()

        # Maintenance mode check
        cur.execute("SELECT setting_value FROM admin_settings WHERE setting_key = 'maintenance_mode'")
        m_row = cur.fetchone()
        maintenance_active = (m_row and m_row["setting_value"].lower() == "true") if m_row else False

        # System health probe
        t0 = time.time()
        cur.execute("SELECT 1")
        cur.fetchone()
        db_ping_ms = round((time.time() - t0) * 1000, 2)

        return {
            "users": {
                "total": u_stats["total"] or 0,
                "active": u_stats["active"] or 0,
                "admins": u_stats["admins"] or 0,
                "pending_approvals": u_stats["pending_approvals"] or 0,
                "expired_users": u_stats["expired_users"] or 0,
            },
            "market_data": {
                "total_symbols": ohlc_stats["total_symbols"] or 0,
                "latest_date": str(ohlc_stats["max_date"]) if ohlc_stats["max_date"] else "N/A",
                "ohlc_rows": ohlc_stats["ohlc_rows"] or 0,
                "historical_rows": hist_stats["hist_rows"] or 0,
            },
            "signals_and_patterns": {
                "active_signals": sig_stats["active_signals"] or 0,
                "total_signals": sig_stats["total_signals"] or 0,
                "pattern_events": pe_stats["total_events"] or 0,
                "saved_scans": scan_stats["total_scans"] or 0,
            },
            "system_health": {
                "db_status": "Healthy",
                "db_ping_ms": db_ping_ms,
                "maintenance_mode": maintenance_active,
            },
            "recent_activity": [
                {
                    "id": log["id"],
                    "admin_email": log["admin_email"],
                    "action": log["action"],
                    "target_type": log["target_type"],
                    "target_id": log["target_id"],
                    "created_at": str(log["created_at"]),
                    "details": json.loads(log["details"]) if isinstance(log.get("details"), str) else (log.get("details") or {}),
                    "ip_address": log["ip_address"],
                }
                for log in recent_logs
            ],
        }
    finally:
        cur.close()
        conn.close()


def get_users_list(
    search: Optional[str] = None,
    role: Optional[str] = None,
    status_filter: Optional[str] = None,
    approval_filter: Optional[str] = None,
    page: int = 1,
    limit: int = 15,
) -> dict:
    """Return paginated list of users with search, role, status, approval filters and 365-day countdown."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        where_clauses = ["1=1"]
        params: List[Any] = []

        if search:
            where_clauses.append("(email LIKE %s OR name LIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if role in ("admin", "user"):
            where_clauses.append("role = %s")
            params.append(role)

        if approval_filter in ("pending", "approved", "rejected"):
            where_clauses.append("approval_status = %s")
            params.append(approval_filter)

        if status_filter == "active":
            where_clauses.append("is_active = 1")
        elif status_filter in ("inactive", "blocked"):
            where_clauses.append("is_active = 0")
        elif status_filter == "expired":
            where_clauses.append("expires_at < NOW()")

        where_sql = " AND ".join(where_clauses)

        # Count total matching
        cur.execute(f"SELECT COUNT(*) as total FROM users WHERE {where_sql}", tuple(params))
        total_count = cur.fetchone()["total"]

        # Count pending approvals in system
        cur.execute("SELECT COUNT(*) as pending_count FROM users WHERE approval_status = 'pending'")
        pending_count = cur.fetchone()["pending_count"]

        # Fetch page with 365-day calculation
        offset = max(0, (page - 1) * limit)
        cur.execute(
            f"""
            SELECT id, email, name, role, is_active, auth_provider, created_at, token_version, 
                   last_login_at, last_login_ip, approval_status, expires_at,
                   DATEDIFF(expires_at, NOW()) as days_left
            FROM users
            WHERE {where_sql}
            ORDER BY 
                CASE WHEN approval_status = 'pending' THEN 0 ELSE 1 END,
                id DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )
        users = cur.fetchall()

        formatted_users = []
        for u in users:
            days_left = u.get("days_left")
            days_remaining = max(0, int(days_left)) if days_left is not None else None
            is_expired = bool(u.get("expires_at") and days_left is not None and days_left <= 0)

            formatted_users.append({
                "id": u["id"],
                "email": u["email"],
                "name": u["name"],
                "role": u["role"],
                "is_active": bool(u["is_active"]),
                "auth_provider": u["auth_provider"],
                "approval_status": u.get("approval_status") or "approved",
                "expires_at": str(u["expires_at"]) if u.get("expires_at") else None,
                "days_remaining": days_remaining,
                "is_expired": is_expired,
                "created_at": str(u["created_at"]) if u["created_at"] else None,
                "token_version": u.get("token_version", 1),
                "last_login_at": str(u["last_login_at"]) if u.get("last_login_at") else None,
                "last_login_ip": u.get("last_login_ip"),
            })

        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

        return {
            "users": formatted_users,
            "total_count": total_count,
            "pending_count": pending_count,
            "page": page,
            "total_pages": total_pages,
            "limit": limit,
        }
    finally:
        cur.close()
        conn.close()


def update_user_role(admin_user: dict, target_user_id: int, new_role: str, ip_address: Optional[str] = None) -> dict:
    """
    Safely update user role.
    SAFEGUARD: Prevents demoting the last remaining active admin account.
    """
    if new_role not in ("admin", "user"):
        raise ValueError("Invalid role specified. Must be 'admin' or 'user'.")

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, role, is_active FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        old_role = target["role"]
        if old_role == new_role:
            return {"message": f"User is already assigned the '{new_role}' role.", "role": new_role}

        # SAFEGUARD: Last admin protection
        if old_role == "admin" and new_role != "admin":
            cur.execute("SELECT COUNT(*) as admin_count FROM users WHERE role = 'admin' AND is_active = 1")
            admin_count = cur.fetchone()["admin_count"]
            if admin_count <= 1:
                raise PermissionError("Cannot demote the last remaining active administrator account.")

        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, target_user_id))
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_ROLE_CHANGE",
            target_type="user",
            target_id=str(target_user_id),
            details={"target_email": target["email"], "old_role": old_role, "new_role": new_role},
            ip_address=ip_address,
        )

        return {"message": f"Successfully updated user role to {new_role}.", "role": new_role}
    finally:
        cur.close()
        conn.close()


def update_user_status(admin_user: dict, target_user_id: int, is_active: bool, ip_address: Optional[str] = None) -> dict:
    """
    Activate or deactivate a user account.
    SAFEGUARD: Prevents deactivating the last remaining active admin account.
    """
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, role, is_active FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        # SAFEGUARD: Last admin protection
        if not is_active and target["role"] == "admin":
            cur.execute("SELECT COUNT(*) as admin_count FROM users WHERE role = 'admin' AND is_active = 1")
            admin_count = cur.fetchone()["admin_count"]
            if admin_count <= 1:
                raise PermissionError("Cannot deactivate the last remaining active administrator account.")

        cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (1 if is_active else 0, target_user_id))
        
        # If deactivating, also invalidate their current session
        if not is_active:
            cur.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (target_user_id,))

        conn.commit()

        action = "USER_ACTIVATE" if is_active else "USER_DEACTIVATE"
        log_audit(
            admin_user=admin_user,
            action=action,
            target_type="user",
            target_id=str(target_user_id),
            details={"target_email": target["email"], "is_active": is_active},
            ip_address=ip_address,
        )

        return {"message": f"User account successfully {'activated' if is_active else 'deactivated'}.", "is_active": is_active}
    finally:
        cur.close()
        conn.close()


def revoke_user_sessions(admin_user: dict, target_user_id: int, ip_address: Optional[str] = None) -> dict:
    """
    Increment token_version to immediately invalidate all active JWT sessions for target user.
    """
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, token_version FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        new_version = target.get("token_version", 1) + 1
        cur.execute("UPDATE users SET token_version = %s WHERE id = %s", (new_version, target_user_id))
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="SESSIONS_REVOKE",
            target_type="user",
            target_id=str(target_user_id),
            details={"target_email": target["email"], "new_token_version": new_version},
            ip_address=ip_address,
        )

        return {"message": f"All active sessions for {target['email']} have been revoked.", "token_version": new_version}
    finally:
        cur.close()
        conn.close()


def create_user_admin(
    admin_user: dict,
    email: str,
    name: str,
    password: str,
    role: str = "user",
    validity_days: int = 365,
    ip_address: Optional[str] = None
) -> dict:
    """Create a new user or administrator directly from the admin console with 365-day validity."""
    if role not in ("admin", "user"):
        raise ValueError("Invalid role specified. Must be 'admin' or 'user'.")
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one number.")
    if not email or "@" not in email:
        raise ValueError("Invalid email address provided.")

    email_clean = email.strip().lower()
    name_clean = name.strip() or email_clean.split("@")[0]

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (email_clean,))
        if cur.fetchone():
            raise ValueError(f"User with email '{email_clean}' already exists.")

        hashed = hash_password(password)
        cur.execute(
            """
            INSERT INTO users (email, hashed_password, name, role, auth_provider, is_active, approval_status, expires_at)
            VALUES (%s, %s, %s, %s, 'local', 1, 'approved', DATE_ADD(NOW(), INTERVAL %s DAY))
            """,
            (email_clean, hashed, name_clean, role, validity_days),
        )
        new_id = cur.lastrowid

        # Seed default strategies
        default_strategies = [
            ("1. Institutional Delivery & Smart-Money Accumulation", json.dumps({"logic": "AND", "conditions": [{"field": "delivery_momentum_signal", "operator": "=="}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("2. Momentum & Multi-Year Breakout", json.dumps({"logic": "AND", "conditions": [{"field": "new_52w_high", "operator": "=="}, {"field": "RCS_30D", "operator": ">", "value": 0}, {"field": "adx_trigger", "operator": "=="}]})),
            ("3. Volatility Contraction (VCP) & Narrow Range", json.dumps({"logic": "AND", "conditions": [{"field": "NR", "operator": ">", "value": 6}, {"field": "High_Relative_Volume_30", "operator": "=="}]})),
            ("4. High-Probability Reversal & Dip Buying", json.dumps({"logic": "AND", "conditions": [{"field": "oversold", "operator": "=="}, {"field": "Hammer", "operator": "=="}]})),
            ("5. EMA Ribbon Convergence & Golden Cross", json.dumps({"logic": "AND", "conditions": [{"field": "convergence_5", "operator": "=="}, {"field": "adx_trigger", "operator": "=="}]})),
        ]
        for s_name, conds in default_strategies:
            cur.execute(
                "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
                (new_id, s_name, conds)
            )

        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_CREATE_BY_ADMIN",
            target_type="user",
            target_id=str(new_id),
            details={"email": email_clean, "role": role, "validity_days": validity_days},
            ip_address=ip_address,
        )

        return {
            "message": f"Successfully created new {role} '{email_clean}' with {validity_days} days validity.",
            "user_id": new_id,
            "role": role,
            "email": email_clean,
            "validity_days": validity_days
        }
    finally:
        cur.close()
        conn.close()


def approve_user(admin_user: dict, target_user_id: int, validity_days: int = 365, ip_address: Optional[str] = None) -> dict:
    """Approve a pending user registration and grant 365-day access."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, name, role, approval_status FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        cur.execute(
            """
            UPDATE users 
            SET approval_status = 'approved',
                is_active = 1,
                expires_at = DATE_ADD(NOW(), INTERVAL %s DAY)
            WHERE id = %s
            """,
            (validity_days, target_user_id),
        )
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_APPROVE",
            target_type="user",
            target_id=str(target_user_id),
            details={"email": target["email"], "validity_days": validity_days},
            ip_address=ip_address,
        )

        return {"message": f"User {target['email']} approved successfully with {validity_days} days access.", "approval_status": "approved"}
    finally:
        cur.close()
        conn.close()


def reject_user(admin_user: dict, target_user_id: int, ip_address: Optional[str] = None) -> dict:
    """Reject a pending registration request."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, role, approval_status FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        if target.get("role") == "admin":
            raise PermissionError("Cannot reject an administrator account.")

        cur.execute("UPDATE users SET approval_status = 'rejected', is_active = 0 WHERE id = %s", (target_user_id,))
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_REJECT",
            target_type="user",
            target_id=str(target_user_id),
            details={"email": target["email"]},
            ip_address=ip_address,
        )

        return {"message": f"User registration for {target['email']} has been rejected.", "approval_status": "rejected"}
    finally:
        cur.close()
        conn.close()


def delete_user(admin_user: dict, target_user_id: int, ip_address: Optional[str] = None) -> dict:
    """
    Permanently delete a user account and remove their access completely.
    SAFEGUARDS:
      - Cannot delete the last remaining active admin.
      - Admin cannot delete their own active account.
    """
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, role, is_active FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        if target_user_id == admin_user.get("id"):
            raise PermissionError("You cannot remove your own active administrator account.")

        if target.get("role") == "admin":
            cur.execute("SELECT COUNT(*) as admin_count FROM users WHERE role = 'admin' AND is_active = 1")
            admin_count = cur.fetchone()["admin_count"]
            if admin_count <= 1:
                raise PermissionError("Cannot remove the last remaining active administrator account.")

        # Clean up dependent user saved scans
        cur.execute("DELETE FROM saved_scans WHERE user_id = %s", (target_user_id,))
        # Remove user row
        cur.execute("DELETE FROM users WHERE id = %s", (target_user_id,))
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_DELETE",
            target_type="user",
            target_id=str(target_user_id),
            details={"deleted_email": target["email"], "role": target["role"]},
            ip_address=ip_address,
        )

        return {"message": f"User '{target['email']}' and all associated access have been permanently removed."}
    finally:
        cur.close()
        conn.close()


def renew_user_validity(admin_user: dict, target_user_id: int, days: int = 365, ip_address: Optional[str] = None) -> dict:
    """Extend / renew user access period by specified days (default 365)."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, email, expires_at FROM users WHERE id = %s", (target_user_id,))
        target = cur.fetchone()
        if not target:
            raise KeyError("User not found.")

        cur.execute(
            """
            UPDATE users 
            SET expires_at = DATE_ADD(GREATEST(NOW(), COALESCE(expires_at, NOW())), INTERVAL %s DAY),
                is_active = 1
            WHERE id = %s
            """,
            (days, target_user_id),
        )
        conn.commit()

        log_audit(
            admin_user=admin_user,
            action="USER_RENEW_VALIDITY",
            target_type="user",
            target_id=str(target_user_id),
            details={"email": target["email"], "extended_by_days": days},
            ip_address=ip_address,
        )

        return {"message": f"Successfully extended validity for {target['email']} by {days} days."}
    finally:
        cur.close()
        conn.close()


def get_market_data_telemetry() -> dict:
    """Return comprehensive data sync health, universe details, and delivery coverage."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # Date boundaries and counts
        cur.execute("""
            SELECT 
                COUNT(DISTINCT ticker) as total_symbols,
                MIN(date) as first_date,
                MAX(date) as latest_date,
                COUNT(*) as total_rows,
                SUM(CASE WHEN delivery_quantity IS NOT NULL THEN 1 ELSE 0 END) as delivery_rows
            FROM ohlc_data
        """)
        ohlc = cur.fetchone()

        cur.execute("""
            SELECT 
                COUNT(DISTINCT Symbol) as total_symbols,
                MIN(Timestamp) as first_date,
                MAX(Timestamp) as latest_date,
                COUNT(*) as total_rows
            FROM historical_data
        """)
        hist = cur.fetchone()

        # Check latest trading dates
        cur.execute("SELECT DISTINCT date FROM ohlc_data ORDER BY date DESC LIMIT 5")
        recent_dates = [str(r["date"]) for r in cur.fetchall()]

        # Find stale / zero recent volume tickers
        cur.execute("""
            SELECT ticker, MAX(date) as last_seen, COUNT(*) as rows_count
            FROM ohlc_data
            GROUP BY ticker
            HAVING last_seen < %s
            LIMIT 20
        """, (recent_dates[0] if recent_dates else "2026-09-25",))
        stale_symbols = cur.fetchall()

        delivery_coverage_pct = round((ohlc["delivery_rows"] / ohlc["total_rows"] * 100), 2) if ohlc["total_rows"] else 0.0

        return {
            "ohlc_data": {
                "symbols": ohlc["total_symbols"] or 0,
                "first_date": str(ohlc["first_date"]) if ohlc["first_date"] else None,
                "latest_date": str(ohlc["latest_date"]) if ohlc["latest_date"] else None,
                "total_rows": ohlc["total_rows"] or 0,
                "delivery_rows": ohlc["delivery_rows"] or 0,
                "delivery_coverage_pct": delivery_coverage_pct,
            },
            "historical_signals": {
                "symbols": hist["total_symbols"] or 0,
                "first_date": str(hist["first_date"]) if hist["first_date"] else None,
                "latest_date": str(hist["latest_date"]) if hist["latest_date"] else None,
                "total_rows": hist["total_rows"] or 0,
            },
            "recent_trading_dates": recent_dates,
            "stale_symbols_count": len(stale_symbols),
            "stale_samples": [
                {"ticker": r["ticker"], "last_seen": str(r["last_seen"]), "rows": r["rows_count"]}
                for r in stale_symbols
            ],
            "sync_status": "UP_TO_DATE" if ohlc["latest_date"] else "NEEDS_SYNC",
        }
    finally:
        cur.close()
        conn.close()


def get_market_symbols_table(search: Optional[str] = None, page: int = 1, limit: int = 20) -> dict:
    """Paginated list of universe tickers with data statistics."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        where = "1=1"
        params: List[Any] = []
        if search:
            where = "ticker LIKE %s"
            params.append(f"%{search}%")

        cur.execute(f"SELECT COUNT(DISTINCT ticker) as total FROM ohlc_data WHERE {where}", tuple(params))
        total = cur.fetchone()["total"]

        offset = max(0, (page - 1) * limit)
        cur.execute(
            f"""
            SELECT ticker, MIN(date) as first_date, MAX(date) as last_date, COUNT(*) as rows_count,
                   SUM(CASE WHEN delivery_quantity IS NOT NULL THEN 1 ELSE 0 END) as delivery_rows
            FROM ohlc_data
            WHERE {where}
            GROUP BY ticker
            ORDER BY ticker ASC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )
        symbols = cur.fetchall()

        formatted = []
        for s in symbols:
            formatted.append({
                "ticker": s["ticker"],
                "first_date": str(s["first_date"]),
                "last_date": str(s["last_date"]),
                "rows_count": s["rows_count"],
                "has_delivery": s["delivery_rows"] > 0,
                "delivery_rows": s["delivery_rows"],
            })

        return {
            "symbols": formatted,
            "total_count": total,
            "page": page,
            "total_pages": (total + limit - 1) // limit if total > 0 else 1,
            "limit": limit,
        }
    finally:
        cur.close()
        conn.close()


def get_scanners_and_strategies() -> List[dict]:
    """Return all configured scanners with their win rate and multi-period performance."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM scanner_configs ORDER BY category, name ASC")
        rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "description": r["description"],
                "is_enabled": bool(r["is_enabled"]),
                "win_rate": float(r["win_rate"] or 0.0),
                "avg_return": float(r["avg_return"] or 0.0),
                "median_return": float(r["median_return"] or 0.0),
                "max_drawdown": float(r["max_drawdown"] or 0.0),
                "profit_factor": float(r["profit_factor"] or 0.0),
                "return_5d": float(r["return_5d"] or 0.0),
                "return_10d": float(r["return_10d"] or 0.0),
                "return_20d": float(r["return_20d"] or 0.0),
                "return_40d": float(r["return_40d"] or 0.0),
                "updated_at": str(r["updated_at"]) if r.get("updated_at") else None,
            }
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


def toggle_scanner_status(admin_user: dict, scanner_id: str, is_enabled: bool, ip_address: Optional[str] = None) -> dict:
    """Enable or disable a specific pattern scanner."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, name, is_enabled FROM scanner_configs WHERE id = %s", (scanner_id,))
        sc = cur.fetchone()
        if not sc:
            raise KeyError(f"Scanner with id '{scanner_id}' not found.")

        cur.execute("UPDATE scanner_configs SET is_enabled = %s WHERE id = %s", (1 if is_enabled else 0, scanner_id))
        conn.commit()

        # Invalidate cache so users immediately get updated scanner availability
        cache.invalidate_all()

        log_audit(
            admin_user=admin_user,
            action="SCANNER_TOGGLE",
            target_type="scanner",
            target_id=scanner_id,
            details={"scanner_name": sc["name"], "is_enabled": is_enabled},
            ip_address=ip_address,
        )

        return {"message": f"Scanner '{sc['name']}' {'enabled' if is_enabled else 'disabled'}.", "is_enabled": is_enabled}
    finally:
        cur.close()
        conn.close()


def get_system_monitoring() -> dict:
    """Return live system telemetry, DB latency, memory, uptime, and background jobs."""
    import sys

    table_sizes = []
    jobs = []
    sec_events = []

    # 1. Fetch Database metrics safely
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT 
                    table_name,
                    ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
                    table_rows
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                ORDER BY (data_length + index_length) DESC
                LIMIT 10
            """)
            raw_tables = cur.fetchall()
            for t in raw_tables:
                name = t.get("table_name") or t.get("TABLE_NAME") or "unknown"
                size = t.get("size_mb") or t.get("SIZE_MB") or 0.0
                rows = t.get("table_rows") or t.get("TABLE_ROWS") or 0
                table_sizes.append({"name": str(name), "size_mb": float(size), "rows": int(rows)})

            cur.execute("SELECT * FROM admin_jobs ORDER BY started_at DESC LIMIT 10")
            jobs = cur.fetchall()

            cur.execute("SELECT * FROM admin_security_events ORDER BY created_at DESC LIMIT 10")
            sec_events = cur.fetchall()
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.error("Failed to query DB telemetry in get_system_monitoring: %s", exc)

    # 2. Fetch System & Process Memory safely
    process_ram_mb = 0.0
    total_ram_gb = 0.0
    ram_percent = 0.0
    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        process_ram_mb = round(mem_info.rss / 1024 / 1024, 2)
        total_ram_gb = round(psutil.virtual_memory().total / 1024 / 1024 / 1024, 2)
        ram_percent = round(float(psutil.virtual_memory().percent), 1)
    except Exception as exc:
        logger.warning("psutil telemetry not available: %s", exc)
        try:
            # Fallback for Windows host memory
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_ram_gb = round(stat.ullTotalPhys / 1024 / 1024 / 1024, 2)
            ram_percent = float(stat.dwMemoryLoad)
            process_ram_mb = 64.0
        except Exception:
            pass

    return {
        "server": {
            "python_version": sys.version.split(" ")[0],
            "os": os.name,
            "process_ram_mb": process_ram_mb,
            "system_ram_gb": total_ram_gb,
            "system_ram_percent": ram_percent,
        },
        "database": {
            "tables": table_sizes,
        },
        "background_jobs": [
            {
                "id": j["id"],
                "name": j["job_name"],
                "status": j["status"],
                "started_at": str(j["started_at"]) if j["started_at"] else None,
                "finished_at": str(j["finished_at"]) if j["finished_at"] else None,
                "duration_sec": j["duration_sec"],
                "records_processed": j["records_processed"],
                "triggered_by": j["triggered_by"],
                "error_message": j["error_message"],
            }
            for j in jobs
        ],
        "recent_security_events": [
            {
                "id": se["id"],
                "event_type": se["event_type"],
                "severity": se["severity"],
                "email": se["email"],
                "ip_address": se["ip_address"],
                "created_at": str(se["created_at"]),
                "details": json.loads(se["details"]) if isinstance(se.get("details"), str) else (se.get("details") or {}),
            }
            for se in sec_events
        ],
    }


def get_audit_trail(action: Optional[str] = None, page: int = 1, limit: int = 25) -> dict:
    """Return paginated audit log history."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        where = "1=1"
        params: List[Any] = []
        if action:
            where = "action = %s"
            params.append(action)

        cur.execute(f"SELECT COUNT(*) as total FROM admin_audit_logs WHERE {where}", tuple(params))
        total = cur.fetchone()["total"]

        offset = max(0, (page - 1) * limit)
        cur.execute(
            f"""
            SELECT id, admin_id, admin_email, action, target_type, target_id, details, ip_address, created_at
            FROM admin_audit_logs
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params + [limit, offset]),
        )
        logs = cur.fetchall()

        formatted = []
        for l in logs:
            formatted.append({
                "id": l["id"],
                "admin_id": l["admin_id"],
                "admin_email": l["admin_email"],
                "action": l["action"],
                "target_type": l["target_type"],
                "target_id": l["target_id"],
                "ip_address": l["ip_address"],
                "created_at": str(l["created_at"]),
                "details": json.loads(l["details"]) if isinstance(l.get("details"), str) else (l.get("details") or {}),
            })

        return {
            "logs": formatted,
            "total_count": total,
            "page": page,
            "total_pages": (total + limit - 1) // limit if total > 0 else 1,
            "limit": limit,
        }
    finally:
        cur.close()
        conn.close()


def get_all_settings() -> dict:
    """Return all runtime settings grouped by category."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM admin_settings ORDER BY category, setting_key ASC")
        rows = cur.fetchall()

        grouped: Dict[str, List[dict]] = {}
        for r in rows:
            cat = r["category"] or "general"
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append({
                "key": r["setting_key"],
                "value": r["setting_value"],
                "type": r["setting_type"],
                "category": r["category"],
                "description": r["description"],
                "updated_by": r["updated_by"],
                "updated_at": str(r["updated_at"]) if r.get("updated_at") else None,
            })
        return grouped
    finally:
        cur.close()
        conn.close()


def update_setting_value(admin_user: dict, setting_key: str, new_value: str, ip_address: Optional[str] = None) -> dict:
    """Update a specific runtime configuration setting and log audit."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT setting_key, setting_value, setting_type FROM admin_settings WHERE setting_key = %s", (setting_key,))
        setting = cur.fetchone()
        if not setting:
            raise KeyError(f"Setting '{setting_key}' not found.")

        old_val = setting["setting_value"]
        cur.execute(
            """
            UPDATE admin_settings 
            SET setting_value = %s, updated_by = %s, updated_at = CURRENT_TIMESTAMP
            WHERE setting_key = %s
            """,
            (new_value, admin_user.get("email", "admin"), setting_key),
        )
        conn.commit()

        # If cache TTL or maintenance mode was changed, flush cache
        if setting_key in ("cache_ttl_seconds", "maintenance_mode"):
            cache.invalidate_all()
            _maintenance_cache["fetched_at"] = 0.0

        log_audit(
            admin_user=admin_user,
            action="SETTING_UPDATE",
            target_type="setting",
            target_id=setting_key,
            details={"key": setting_key, "old_value": old_val, "new_value": new_value},
            ip_address=ip_address,
        )

        return {"message": f"Setting '{setting_key}' successfully updated.", "key": setting_key, "value": new_value}
    finally:
        cur.close()
        conn.close()


_maintenance_cache = {"value": False, "fetched_at": 0.0}

def is_maintenance_mode_active() -> bool:
    """Quick check if maintenance mode is enabled with 5s local cache."""
    now = time.monotonic()
    if now - _maintenance_cache["fetched_at"] < 5.0:
        return _maintenance_cache["value"]

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT setting_value FROM admin_settings WHERE setting_key = 'maintenance_mode'")
        row = cur.fetchone()
        val = bool(row and row["setting_value"].strip().lower() == "true")
        _maintenance_cache["value"] = val
        _maintenance_cache["fetched_at"] = now
        return val
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()
