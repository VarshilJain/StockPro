# StockPro V2 Codebase Archive

This folder stores the complete, backup source code of the **StockPro V2 Redesign** (TradingView-style financial terminal design).

---

## 📁 Archived Files

1. **`index-v2.html`** (138 KB)
   - Complete unified Single Page Application (SPA) with V2 design style for:
     - Dashboard with live market pulse & custom signal widgets.
     - Pro Stock Chart with interactive LightweightCharts, SVG Speedometer rating gauge, 8-Indicator Live Grid, Performance card, MACD & EMA Trend widgets, Last Close & Volume Spike cards, EMA Overview Table, and Yahoo Finance News.
     - Stock Scanner with multi-parameter filter.
     - Technical Signal Scanner.
     - No-Code Scan Builder.
     - Fundamental Financial Statements.
     - Pattern Screens (PCS, APB, Multi-Year, High Rel Vol, High Deliv Vol, RSI Div, EMA Convergence Ribbon).
     - Watchlist Drawer.
     - Dark / Light Mode theme engine.

2. **`stock-chart-v2.html`** (84 KB)
   - Standalone dedicated Stock Chart V2 page.

---

## 🔁 How to Restore / Run V2 in the Future

If you ever wish to re-enable V2:
1. Move or copy `archive/index-v2.html` to `static/index-v2.html`.
2. In `app.py`, add:
   ```python
   @app.get("/v2", include_in_schema=False)
   def serve_v2_platform():
       return FileResponse("static/index-v2.html")
   ```
3. Visit `http://localhost:8000/v2`.
