from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from routers import stocks, fundamentals, auth as auth_router, scans, dashboard as dashboard_router, screens

app = FastAPI()

# ✅ Allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Include routers
app.include_router(auth_router.router)                              # /api/auth/...
app.include_router(stocks.router, prefix="/api", tags=["stocks"])
app.include_router(fundamentals.router, prefix="/api", tags=["Fundamentals"])
app.include_router(scans.router, tags=["Scans"])                    # already has prefix="/api/scans"
app.include_router(screens.router)                                  # /api/screens/..., /api/stage-summary
app.include_router(dashboard_router.router)                         # /api/signals, /api/dashboard/...

# ✅ Clean-URL routes for HTML pages (serve without .html extension)
@app.get("/scan-builder", include_in_schema=False)
def serve_scan_builder():
    return FileResponse("static/scan-builder.html")

@app.get("/login", include_in_schema=False)
def serve_login():
    return FileResponse("static/login.html")

@app.get("/register", include_in_schema=False)
def serve_register():
    return FileResponse("static/register.html")

# ✅ Mount static files (must come LAST, after all API routes)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
