from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from routers import stocks

app = FastAPI()

# API routes
app.include_router(stocks.router, prefix="/api", tags=["Stocks"])

# Serve frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")
