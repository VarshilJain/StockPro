from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from database import get_connection
from datetime import datetime

router = APIRouter()


def convert_date_format(date_str):
    """Convert DD-MM-YYYY to YYYY-MM-DD format"""
    try:
        # Try DD-MM-YYYY format first
        if len(date_str.split('-')) == 3 and len(date_str.split('-')[0]) <= 2:
            day, month, year = date_str.split('-')
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        # If already in YYYY-MM-DD format, return as is
        return date_str
    except:
        return date_str


# 📌 Schema for posting stock data
class StockData(BaseModel):
    Symbol: str
    Timestamp: str  # Format: 'YYYY-MM-DD HH:MM:SS'
    Open: float
    High: float
    Low: float
    Close: float
    Volume: int


@router.get("/symbols")
def get_symbols():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT Symbol FROM historical_data")
    symbols = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return {"symbols": symbols}


@router.get("/data")
def get_stock_data(symbol: Optional[str] = None, start_date: str = Query(...), end_date: str = Query(...), signal: str = Query(None, description="Signal column to filter by")):
    # Convert date format from DD-MM-YYYY to YYYY-MM-DD
    start_date = convert_date_format(start_date)
    end_date = convert_date_format(end_date)
    
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    allowed_signals = [
        "Hammer", "Shooting_Star", "Doji", "Engulfing", "Dark_Cloud_Cover", "Morning_Star", "Evening_Star", "Piercing_Line",
        "signal1", "signal2", "signal3", "signal4", "signal5", "top_decile", "new_52w_high", "new_52w_low", "NR", "High_Relative_Volume_30",
        "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70"
    ]
    if signal and signal not in allowed_signals:
        return JSONResponse(content=[])

    if symbol and signal:
        # Both stock and scanner selected: all columns, filter rows where scanner matches criteria
        if signal == "NR":
            # For NR, we want values > 0 (5, 6, 7, or 8)
            query = f"SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND {signal} > 0 ORDER BY Timestamp ASC"
        else:
            # For other signals, we want value = 1
            query = f"SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s AND {signal} = 1 ORDER BY Timestamp ASC"
        params = [symbol, start_date, end_date]
    elif symbol:
        # Only stock selected: all columns
        query = "SELECT * FROM historical_data WHERE Symbol = %s AND DATE(Timestamp) BETWEEN %s AND %s ORDER BY Timestamp ASC"
        params = [symbol, start_date, end_date]
    elif signal:
        # Only scanner selected: Symbol, Timestamp, scanner column, filter rows where scanner matches criteria
        if signal == "NR":
            # For NR, we want values > 0 (5, 6, 7, or 8)
            query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} > 0 ORDER BY Timestamp ASC"
        else:
            # For other signals, we want value = 1
            query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} = 1 ORDER BY Timestamp ASC"
        params = [start_date, end_date]
    else:
        # Neither selected: return all data for the date range
        query = "SELECT * FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s ORDER BY Timestamp ASC, Symbol ASC"
        params = [start_date, end_date]

    print('Executing SQL:', query)
    print('With params:', params)
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # ✅ Remove duplicates based on (Timestamp, Symbol)
    unique_rows = []
    seen = set()
    for row in rows:
        key = (row["Timestamp"], row["Symbol"])
        if key not in seen:
            seen.add(key)
            if not isinstance(row["Timestamp"], str):
                row["Timestamp"] = row["Timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            unique_rows.append(row)

    # Failsafe: If both symbol and signal, filter out rows where signal doesn't match criteria
    if symbol and signal:
        if signal == "NR":
            # For NR, we want values > 0 (5, 6, 7, or 8)
            unique_rows = [row for row in unique_rows if int(row.get(signal, 0)) > 0]
        else:
            # For other signals, we want value = 1
            unique_rows = [row for row in unique_rows if str(row.get(signal, 0)) == '1']

    return JSONResponse(content=unique_rows)


# ✅ NEW: POST endpoint to insert stock data
@router.post("/data")
def add_stock_data(data: List[StockData]):
    conn = get_connection()
    cursor = conn.cursor()

    insert_query = """
        INSERT INTO historical_data (Symbol, Timestamp, Open, High, Low, Close, Volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Open = VALUES(Open),
            High = VALUES(High),
            Low = VALUES(Low),
            Close = VALUES(Close),
            Volume = VALUES(Volume)
    """

    try:
        for entry in data:
            cursor.execute(insert_query, (
                entry.Symbol,
                entry.Timestamp,
                entry.Open,
                entry.High,
                entry.Low,
                entry.Close,
                entry.Volume
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

    return {"message": "Stock data inserted/updated successfully"}


@router.get("/signal-scanner")
def get_signal_scanner_data(start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None), signal: str = Query(...), limit: int = Query(1000, description="Maximum number of rows to return")):
    """
    Technical Signal Scanner endpoint
    Returns stocks that have the specified signal = 1 within the date range
    """
    # Convert date format from DD-MM-YYYY to YYYY-MM-DD
    if start_date:
        start_date = convert_date_format(start_date)
    if end_date:
        end_date = convert_date_format(end_date)
    
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    allowed_signals = [
        "Hammer", "Shooting_Star", "Doji", "Engulfing", "Dark_Cloud_Cover", "Morning_Star", "Evening_Star", "Piercing_Line",
        "signal1", "signal2", "signal3", "signal4", "signal5", "top_decile", "new_52w_high", "new_52w_low", "NR", "High_Relative_Volume_30",
        "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70"
    ]
    if signal not in allowed_signals:
        return JSONResponse(content=[], status_code=400)

    # For 2y/5y/10y signals, ignore provided dates and auto-use last 14 days
    auto_window_signals = {"hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d"}
    if signal in auto_window_signals:
        raw_cursor = conn.cursor()
        raw_cursor.execute("SELECT DATE(MAX(Timestamp)) FROM historical_data")
        last_date_row = raw_cursor.fetchone()
        raw_cursor.close()
        if not last_date_row or not last_date_row[0]:
            cursor.close()
            conn.close()
            return JSONResponse(content=[])
        last_date = last_date_row[0]
        from datetime import timedelta
        start_date = (last_date - timedelta(days=14)).strftime("%Y-%m-%d")
        end_date = last_date.strftime("%Y-%m-%d")
    else:
        if not start_date or not end_date:
            cursor.close()
            conn.close()
            return JSONResponse(content=[], status_code=400)

    # Query to get stocks with the specified signal in the date range
    if signal == "NR":
        # For NR, we want values > 0 (5, 6, 7, or 8)
        query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} > 0 ORDER BY Timestamp ASC, Symbol ASC"
    else:
        # For other signals, we want value = 1
        query = f"SELECT Symbol, Timestamp, {signal} FROM historical_data WHERE DATE(Timestamp) BETWEEN %s AND %s AND {signal} = 1 ORDER BY Timestamp ASC, Symbol ASC"
    
    params = [start_date, end_date]

    print('Executing Signal Scanner SQL:', query)
    print('With params:', params)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # ✅ Remove duplicates based on (Timestamp, Symbol)
    unique_rows = []
    seen = set()
    for row in rows:
        key = (row["Timestamp"], row["Symbol"])
        if key not in seen:
            seen.add(key)
            if not isinstance(row["Timestamp"], str):
                row["Timestamp"] = row["Timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            unique_rows.append(row)

    return JSONResponse(content=unique_rows)
    