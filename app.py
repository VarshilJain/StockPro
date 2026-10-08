import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import (
    IS_PRODUCTION,
    STOCKPRO_ENV,
    STOCKPRO_FRONTEND_ORIGINS,
)
from routers import stocks, fundamentals, auth as auth_router, scans, dashboard as dashboard_router, screens, admin as admin_router, watchlist as watchlist_router
from routers import export as export_router, alerts as alerts_router, confluence as confluence_router

logger = logging.getLogger("stockpro")

app = FastAPI(
    title="StockPro Analytics API",
    docs_url="/docs" if not IS_PRODUCTION else None,
    redoc_url="/redoc" if not IS_PRODUCTION else None,
)


# ──────────────────────────────────────────────────────────────
# Security Headers Middleware
# ──────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Core Security Headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        
        if IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            
        # Content Security Policy (Compatible with CDN scripts, Google Identity Services, Google Fonts, and FontAwesome)
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://accounts.google.com/gsi/client; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://accounts.google.com/gsi/style; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
            "img-src 'self' data: https: https://*.googleusercontent.com; "
            "frame-src 'self' https://accounts.google.com/gsi/; "
            "connect-src 'self' https://accounts.google.com/gsi/; "
            "frame-ancestors 'self';"
        )
        response.headers["Content-Security-Policy"] = csp
        return response



app.add_middleware(SecurityHeadersMiddleware)


# ──────────────────────────────────────────────────────────────
# Global Exception Handler (No raw internal error leakage)
# ──────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # If it is already an HTTPException, FastAPI handles it with exc.status_code and exc.detail
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )
    logger.exception("UNHANDLED_EXCEPTION on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again later."},
    )


# ──────────────────────────────────────────────────────────────
# Strict CORS Middleware
# ──────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=STOCKPRO_FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ──────────────────────────────────────────────────────────────
# Include Routers
# ──────────────────────────────────────────────────────────────
app.include_router(auth_router.router)                              # /api/auth/...
app.include_router(stocks.router, prefix="/api", tags=["stocks"])
app.include_router(fundamentals.router, prefix="/api", tags=["Fundamentals"])
app.include_router(scans.router, tags=["Scans"])                    # already has prefix="/api/scans"
app.include_router(screens.router)                                  # /api/screens/..., /api/stage-summary
app.include_router(dashboard_router.router)                         # /api/signals, /api/dashboard/...
app.include_router(admin_router.router)                             # /admin and /api/admin/...
app.include_router(watchlist_router.router)                         # /api/watchlist/...
app.include_router(export_router.router)                            # /api/export/...
app.include_router(alerts_router.router)                            # /api/alerts/...
app.include_router(confluence_router.router)                        # /api/confluence/..., /api/market-breadth


# ──────────────────────────────────────────────────────────────
# Health Check Endpoint
# ──────────────────────────────────────────────────────────────
@app.get("/api/health", tags=["System"])
def health_check():
    """System health check — verifies DB connectivity and reports latest data date."""
    import time
    start = time.monotonic()
    status_info = {"status": "ok", "db": "unknown", "latest_data": None, "stock_count": 0}
    try:
        from database import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DATE(MAX(Timestamp)) as d, COUNT(DISTINCT Symbol) as c FROM historical_data")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        status_info["db"] = "connected"
        if row and row[0]:
            status_info["latest_data"] = row[0].isoformat()
            status_info["stock_count"] = int(row[1])
    except Exception as exc:
        status_info["db"] = "error"
        status_info["status"] = "degraded"
        logger.warning("Health check DB error: %s", exc)
    status_info["response_time_ms"] = round((time.monotonic() - start) * 1000, 1)
    return status_info


# ──────────────────────────────────────────────────────────────
# Clean-URL routes for HTML pages (serve without .html extension)
# ──────────────────────────────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/", include_in_schema=False)
def serve_index(request: Request):
    """
    Serve main application page.
    Admins are strictly routed to the StockPro Admin Console (/admin).
    """
    token = request.cookies.get("access_token")
    if token:
        try:
            from auth import get_current_user
            user = get_current_user(token)
            if user and user.get("role") == "admin":
                return RedirectResponse(url="/admin", status_code=302)
        except Exception:
            pass
    return FileResponse("static/index.html")


@app.get("/scan-builder", include_in_schema=False)
def serve_scan_builder(request: Request):
    """
    Serve scan builder page.
    Admins are strictly routed to the StockPro Admin Console (/admin).
    """
    token = request.cookies.get("access_token")
    if token:
        try:
            from auth import get_current_user
            user = get_current_user(token)
            if user and user.get("role") == "admin":
                return RedirectResponse(url="/admin", status_code=302)
        except Exception:
            pass
    return FileResponse("static/scan-builder.html")


@app.get("/login", include_in_schema=False)
def serve_login():
    return FileResponse("static/login.html")


@app.get("/register", include_in_schema=False)
def serve_register():
    return FileResponse("static/register.html")


# ──────────────────────────────────────────────────────────────
# Mount static files (must come LAST, after all API routes)
# ──────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
