"""
main.py — entry point for `python main.py` (runs uvicorn directly).
Equivalent to app.py so both launch methods work identically.
"""
import uvicorn
from app import app  # re-use the fully configured FastAPI app from app.py

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
