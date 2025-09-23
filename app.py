from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routers import stocks
import mysql.connector

app = FastAPI()

# ✅ Allow frontend (React, Angular, etc.) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ MySQL Connection
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="stock_data"
    )

# ✅ Include routers
app.include_router(stocks.router, prefix="/api", tags=["stocks"])

# ✅ Mount static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")

# ✅ Fetch available stock symbols
@app.get("/symbols")
def get_symbols():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT Symbol FROM historical_data")
    symbols = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return {"symbols": symbols}
