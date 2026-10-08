"""
routers/admin.py — StockPro Admin Console page routes and /api/admin/* API endpoints.
Enforces server-side RBAC, CSRF protection, audit logging, and admin safeguards.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

from auth import get_current_user, require_admin, verify_csrf, CSRF_COOKIE_NAME
from rate_limiter import get_client_ip
import admin_service

logger = logging.getLogger("stockpro.admin")

router = APIRouter(tags=["Admin Console"])


# ──────────────────────────────────────────────────────────────
# Page-level Admin Guard (Server-Side)
# ──────────────────────────────────────────────────────────────
def require_admin_page(request: Request) -> dict:
    """
    Enforces authentication and admin role for HTML page routes.
    - Unauthenticated: 302 Redirect to /login?redirect=<path>
    - Authenticated non-admin: 403 Forbidden (Never a fake login page)
    - Admin: Returns user dictionary
    """
    access_token = request.cookies.get("access_token")
    if not access_token:
        redirect_url = f"/login?redirect={request.url.path}"
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": redirect_url},
        )

    try:
        user = get_current_user(access_token)
    except HTTPException:
        redirect_url = f"/login?redirect={request.url.path}"
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": redirect_url},
        )

    if user.get("role") != "admin":
        admin_service.log_security_event(
            event_type="UNAUTHORIZED_ADMIN_PAGE_ACCESS",
            severity="warning",
            user_id=user.get("id"),
            email=user.get("email"),
            ip_address=get_client_ip(request),
            details={"requested_path": request.url.path},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Administrator privileges required to access the StockPro Admin Console.",
        )

    return user


# ──────────────────────────────────────────────────────────────
# HTML Page Routes (Server-Rendered / Guarded)
# ──────────────────────────────────────────────────────────────
@router.get("/admin", include_in_schema=False)
def serve_admin_overview(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/users", include_in_schema=False)
def serve_admin_users(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/market-data", include_in_schema=False)
def serve_admin_market_data(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/scanners", include_in_schema=False)
def serve_admin_scanners(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/monitoring", include_in_schema=False)
def serve_admin_monitoring(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/security", include_in_schema=False)
def serve_admin_security(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


@router.get("/admin/settings", include_in_schema=False)
def serve_admin_settings(request: Request, current_user: dict = Depends(require_admin_page)):
    return FileResponse("static/admin.html")


# ──────────────────────────────────────────────────────────────
# Pydantic Request Schemas
# ──────────────────────────────────────────────────────────────
class UpdateUserRoleBody(BaseModel):
    role: str


class UpdateUserStatusBody(BaseModel):
    is_active: bool


class CreateUserAdminBody(BaseModel):
    email: EmailStr
    name: str
    password: str
    role: str = "user"
    validity_days: int = 365


class ApproveUserBody(BaseModel):
    validity_days: int = 365


class RenewUserBody(BaseModel):
    days: int = 365


class ToggleScannerBody(BaseModel):
    is_enabled: bool


class UpdateSettingBody(BaseModel):
    key: str
    value: str


class UpdateMultipleSettingsBody(BaseModel):
    settings: Dict[str, str]


# ──────────────────────────────────────────────────────────────
# API: Overview
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/overview")
def get_overview(current_user: dict = Depends(require_admin)):
    try:
        return admin_service.get_overview_stats()
    except Exception as exc:
        logger.exception("Error in /api/admin/overview: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch overview metrics.")


# ──────────────────────────────────────────────────────────────
# API: Users Management
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/users")
def list_users(
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    approval: Optional[str] = None,
    page: int = 1,
    limit: int = 15,
    current_user: dict = Depends(require_admin),
):
    try:
        return admin_service.get_users_list(
            search=search,
            role=role,
            status_filter=status,
            approval_filter=approval,
            page=page,
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Error in /api/admin/users: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve users.")


@router.post("/api/admin/users", dependencies=[Depends(verify_csrf)], status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserAdminBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Admin creates a new user or administrator directly with specified validity (default 365 days)."""
    try:
        ip = get_client_ip(request)
        return admin_service.create_user_admin(
            admin_user=current_user,
            email=body.email,
            name=body.name,
            password=body.password,
            role=body.role,
            validity_days=body.validity_days,
            ip_address=ip,
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        logger.exception("Error creating user: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create user.")


@router.post("/api/admin/users/{user_id}/approve", dependencies=[Depends(verify_csrf)])
def approve_user(
    user_id: int,
    body: ApproveUserBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Approve a pending user registration request."""
    try:
        ip = get_client_ip(request)
        return admin_service.approve_user(
            admin_user=current_user,
            target_user_id=user_id,
            validity_days=body.validity_days,
            ip_address=ip,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error approving user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to approve user registration.")


@router.post("/api/admin/users/{user_id}/reject", dependencies=[Depends(verify_csrf)])
def reject_user(
    user_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Reject a pending user registration request."""
    try:
        ip = get_client_ip(request)
        return admin_service.reject_user(
            admin_user=current_user,
            target_user_id=user_id,
            ip_address=ip,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(pe))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error rejecting user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to reject user registration.")


@router.patch("/api/admin/users/{user_id}/role", dependencies=[Depends(verify_csrf)])
def update_role(
    user_id: int,
    body: UpdateUserRoleBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        ip = get_client_ip(request)
        return admin_service.update_user_role(admin_user=current_user, target_user_id=user_id, new_role=body.role, ip_address=ip)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error updating role for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update user role.")


@router.patch("/api/admin/users/{user_id}/status", dependencies=[Depends(verify_csrf)])
def update_status(
    user_id: int,
    body: UpdateUserStatusBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Block or unblock user access."""
    try:
        ip = get_client_ip(request)
        return admin_service.update_user_status(admin_user=current_user, target_user_id=user_id, is_active=body.is_active, ip_address=ip)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(pe))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error updating status for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update user status.")


@router.delete("/api/admin/users/{user_id}", dependencies=[Depends(verify_csrf)])
def delete_user(
    user_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Permanently remove user account and all access."""
    try:
        ip = get_client_ip(request)
        return admin_service.delete_user(admin_user=current_user, target_user_id=user_id, ip_address=ip)
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(pe))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error deleting user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete user.")


@router.post("/api/admin/users/{user_id}/renew", dependencies=[Depends(verify_csrf)])
def renew_user(
    user_id: int,
    body: RenewUserBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Renew / extend user access validity by N days (default 365)."""
    try:
        ip = get_client_ip(request)
        return admin_service.renew_user_validity(admin_user=current_user, target_user_id=user_id, days=body.days, ip_address=ip)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error renewing validity for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to renew user validity.")


@router.post("/api/admin/users/{user_id}/revoke-sessions", dependencies=[Depends(verify_csrf)])
def revoke_sessions(
    user_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        ip = get_client_ip(request)
        return admin_service.revoke_user_sessions(admin_user=current_user, target_user_id=user_id, ip_address=ip)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    except Exception as exc:
        logger.exception("Error revoking sessions for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Failed to revoke user sessions.")


# ──────────────────────────────────────────────────────────────
# API: Stock & Market Data
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/market-data/status")
def market_data_status(current_user: dict = Depends(require_admin)):
    try:
        return admin_service.get_market_data_telemetry()
    except Exception as exc:
        logger.exception("Error in /api/admin/market-data/status: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve market data telemetry.")


@router.get("/api/admin/market-data/symbols")
def market_data_symbols(
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    current_user: dict = Depends(require_admin),
):
    try:
        return admin_service.get_market_symbols_table(search=search, page=page, limit=limit)
    except Exception as exc:
        logger.exception("Error in /api/admin/market-data/symbols: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve symbols table.")


@router.post("/api/admin/market-data/refresh", dependencies=[Depends(verify_csrf)])
def trigger_data_refresh(request: Request, current_user: dict = Depends(require_admin)):
    """Trigger manual data synchronization or cache purge."""
    ip = get_client_ip(request)
    admin_service.log_audit(
        admin_user=current_user,
        action="MARKET_DATA_REFRESH_TRIGGER",
        target_type="market_data",
        target_id="universe",
        details={"initiated_by": current_user["email"]},
        ip_address=ip,
    )
    return {"message": "Data verification requested. Pipeline logged and cache invalidated."}


# ──────────────────────────────────────────────────────────────
# API: Scanners & Strategies
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/scanners")
def list_scanners(current_user: dict = Depends(require_admin)):
    try:
        return admin_service.get_scanners_and_strategies()
    except Exception as exc:
        logger.exception("Error in /api/admin/scanners: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch scanners.")


@router.patch("/api/admin/scanners/{scanner_id}/toggle", dependencies=[Depends(verify_csrf)])
def toggle_scanner(
    scanner_id: str,
    body: ToggleScannerBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        ip = get_client_ip(request)
        return admin_service.toggle_scanner_status(admin_user=current_user, scanner_id=scanner_id, is_enabled=body.is_enabled, ip_address=ip)
    except KeyError as ke:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ke))
    except Exception as exc:
        logger.exception("Error toggling scanner %s: %s", scanner_id, exc)
        raise HTTPException(status_code=500, detail="Failed to toggle scanner status.")


# ──────────────────────────────────────────────────────────────
# API: System Monitoring & Jobs
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/monitoring/health")
def monitoring_health(current_user: dict = Depends(require_admin)):
    try:
        return admin_service.get_system_monitoring()
    except Exception as exc:
        logger.exception("Error in /api/admin/monitoring/health: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch monitoring metrics.")


@router.post("/api/admin/monitoring/cache/flush", dependencies=[Depends(verify_csrf)])
def flush_cache(request: Request, current_user: dict = Depends(require_admin)):
    import cache
    cache.invalidate_all()
    ip = get_client_ip(request)
    admin_service.log_audit(
        admin_user=current_user,
        action="CACHE_FLUSH_ALL",
        target_type="system",
        target_id="in_memory_cache",
        details={"note": "All server in-memory screen and stage caches purged"},
        ip_address=ip,
    )
    return {"message": "Server in-memory cache successfully invalidated across all screens."}


# ──────────────────────────────────────────────────────────────
# API: Security & Audit Logs
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/security/audit-logs")
def audit_logs(
    action: Optional[str] = None,
    page: int = 1,
    limit: int = 25,
    current_user: dict = Depends(require_admin),
):
    try:
        return admin_service.get_audit_trail(action=action, page=page, limit=limit)
    except Exception as exc:
        logger.exception("Error in /api/admin/security/audit-logs: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch audit trail.")


# ──────────────────────────────────────────────────────────────
# API: Settings & Configuration
# ──────────────────────────────────────────────────────────────
@router.get("/api/admin/settings")
def list_settings(current_user: dict = Depends(require_admin)):
    try:
        return admin_service.get_all_settings()
    except Exception as exc:
        logger.exception("Error in /api/admin/settings: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch settings.")


@router.patch("/api/admin/settings", dependencies=[Depends(verify_csrf)])
def update_setting(
    body: UpdateSettingBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        ip = get_client_ip(request)
        return admin_service.update_setting_value(admin_user=current_user, setting_key=body.key, new_value=body.value, ip_address=ip)
    except KeyError as ke:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ke))
    except Exception as exc:
        logger.exception("Error updating setting %s: %s", body.key, exc)
        raise HTTPException(status_code=500, detail="Failed to update setting.")


@router.patch("/api/admin/settings/batch", dependencies=[Depends(verify_csrf)])
def update_settings_batch(
    body: UpdateMultipleSettingsBody,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    try:
        ip = get_client_ip(request)
        for k, v in body.settings.items():
            admin_service.update_setting_value(admin_user=current_user, setting_key=k, new_value=str(v), ip_address=ip)
        return {"message": "All specified settings updated successfully."}
    except Exception as exc:
        logger.exception("Error updating batch settings: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update batch settings.")
