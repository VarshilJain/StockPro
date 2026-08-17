import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import (
    IS_PRODUCTION,
    STOCKPRO_ENV,
    STOCKPRO_FRONTEND_ORIGINS,
)
from routers import stocks, fundamentals, auth as auth_router, scans, dashboard as dashboard_router, screens

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


# ──────────────────────────────────────────────────────────────
# Include Routers
# ──────────────────────────────────────────────────────────────
app.include_router(auth_router.router)                              # /api/auth/...
app.include_router(stocks.router, prefix="/api", tags=["stocks"])
app.include_router(fundamentals.router, prefix="/api", tags=["Fundamentals"])
app.include_router(scans.router, tags=["Scans"])                    # already has prefix="/api/scans"
app.include_router(screens.router)                                  # /api/screens/..., /api/stage-summary
app.include_router(dashboard_router.router)                         # /api/signals, /api/dashboard/...


# ──────────────────────────────────────────────────────────────
# Clean-URL routes for HTML pages (serve without .html extension)
# ──────────────────────────────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")

@app.get("/scan-builder", include_in_schema=False)
def serve_scan_builder():
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
