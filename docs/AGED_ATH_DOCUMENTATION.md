# Aged All-Time High (ATH) Screener & Card UI Documentation

> **Status:** Completed  
> **Target Screen:** Pattern Screens > Aged All-Time High (`#/screens/aged-ath`)  
> **Original Backup Location:** `static/index.html.original_backup`  
> **Version:** 2.0 (Full Candlesticks with Multi-Timeframe Daily / Weekly / Monthly Support)

---

## 1. Executive Summary & Purpose

The **Aged All-Time High (ATH)** pattern screen detects institutional-quality setups where a stock established an all-time high more than **2 years ago** and has recently consolidated to within **20%** of that peak or completed an active breakout.

Because these setups span multi-year horizons (2 to 5+ years), standard mini-charts squashed daily candles into dense, wire-like bands. To resolve this, the layout and charting systems were completely overhauled to make the chart the dominant element with single-column cards, full responsive width, autoscale margins, and client-side **Daily (D)**, **Weekly (W)**, and **Monthly (M)** candlestick switching.

---

## 2. Core Screening Engine (`aged_ath_screener.py`)

### 2.1 Qualification Parameters
| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| `MIN_ATH_AGE_YEARS` | `2.0` | Minimum elapsed time since the historical ATH date |
| `MAX_DISTANCE_FROM_ATH_PCT` | `20.0%` | Maximum percentage discount below the ATH (`(ATH - Close) / ATH * 100 <= 20`) |
| `MIN_LISTING_AGE_YEARS` | `3.0` | Minimum trading history since stock listing |
| `PRICE_BASIS` | `"close"` | Option to use closing price or intra-day high (`"high"`) for ATH calculation |

### 2.2 Data Quality & Bad-Tick Filtering
- Single-day anomalies where price spikes > 40% and reverts the following session are flagged and excluded.
- Handled split adjustments, ticker symbol normalization (`.NS` prefix/suffix), and volume validation.

### 2.3 Stage Classification & Precomputed Caching
The screener segments qualifying stocks into two primary actionable stages:
1. **Latest Date (`fresh_breakout`)**: Stocks qualifying or breaking out on the latest trading date (**59 stocks** across the full NSE universe).
2. **Last 30 Days (`climbing`)**: Stocks that established qualification within the last 30 trading sessions (**118 stocks** across the full NSE universe).

Precomputed results are cached in `cache_data/aged_ath/stages_cache.json` for sub-10ms response times.

---

## 3. Backend Architecture & API Routing

### 3.1 Endpoints (`routers/screens.py`)
- `GET /api/screens/aged-ath`: Returns all ranked Aged ATH stocks with full stats (ATH, ATH Date, ATH Age, DMA slopes, 52-week position, 1-year return).
- `GET /api/screens/aged-ath/stages`: Returns structured stage buckets (`fresh_breakout` and `climbing`) with in-memory caching.
- `POST /api/ohlcv/batch` (`routers/stocks.py`): Fetches batch daily historical OHLCV bars (supports up to 1,260 days). Lookback for Aged ATH is configured to **750 trading days** (~3 calendar years).

---

## 4. Layout & Card Design Overhaul (`static/index.html`)

### 4.1 Single-Column Full-Width Grid
- **Scope**: Applied exclusively to `aged-ath`; other pattern screens retain their multi-column auto-fill grid (`repeat(auto-fill, minmax(min(400px, 100%), 1fr))`).
- **Container Style**: `grid-template-columns: 1fr` with a consistent `18px` vertical gap.
- **Card Padding**: Reduced from 24px to `16px` (`12px` on mobile) so the chart extends nearly edge-to-edge.

### 4.2 Card Structure & Dimensions

```
+-----------------------------------------------------------------------------------------+
| [SYMBOL] [Badges] [Heart] [MCap]             [D | W | M]    [₹Price (+%)]  [Open full >] |  <- Header (~34px)
+-----------------------------------------------------------------------------------------+
|                                                                                         |
|                                                                                         |
|                             DOMINANT CANDLESTICK CHART                                  |  <- Chart (380px Desktop
|                     (100% Width, Dotted ATH Line, Scaled Margins)                       |             260px Mobile)
|                                                                                         |
|                                                                                         |
+-----------------------------------------------------------------------------------------+
| ATH: ₹X.XX   | Dist: -X.X%  | Age: X.X yrs | Date: YYYY-MM-DD                           |  <- Compact Stats Grid
| 200-DMA: Up  | Slope: Rise  | 1-Yr: +XX.X% | Tier: Within 5%                            |     (~65px, 4 cols)
+-----------------------------------------------------------------------------------------+
```

| Dimension | Desktop (`> 768px`) | Mobile (`≤ 768px`) |
| :--- | :--- | :--- |
| **Card Width** | `100%` of container (~1,000px – 1,400px) | `100%` of viewport (360px – 420px) |
| **Chart Height** | **380px** (~77% of card height) | **260px** (~60% of card height) |
| **Header Height** | ~34px (Single-row flex) | ~40px (Responsive wrap) |
| **Stats Grid Height** | ~65px (4 columns × 2 rows) | ~110px (2 columns × 4 rows) |
| **Gaps & Margins** | ~10px | ~10px |
| **Total Card Height** | **~490px** | **~445px** |

---

## 5. Chart Engine & Multi-Timeframe Implementation

### 5.1 Full Candlesticks with Wicks
- Configured using TradingView Lightweight Charts `addCandlestickSeries`:
  - Up color: `#10b981` (Green)
  - Down color: `#ef4444` (Red)
  - Wicks enabled: `wickVisible: true`, `wickUpColor: '#10b981'`, `wickDownColor: '#ef4444'`
  - Precision: `priceFormat: { type: 'price', precision: 2, minMove: 0.01 }`

### 5.2 Responsive Autoscale
- Vertical padding set to `scaleMargins: { top: 0.05, bottom: 0.05 }`, reserving 90% of chart height for price bars.
- Connected via `ResizeObserver`: dynamically adjusts chart width on browser resize and triggers `chart.timeScale().fitContent()`.

### 5.3 ATH Reference Line
- Horizontal dashed green reference line created via `series.createPriceLine`:
  - `lineStyle: LightweightCharts.LineStyle.Dashed`
  - `color: '#10b981'`
  - Extends across the entire chart width with an axis price badge (`ATH ₹...`).

### 5.4 Multi-Timeframe Switching: Daily, Weekly, Monthly

Historical data is fetched once (750 daily bars / ~3 years), then aggregated in real-time in client JavaScript (< 5ms switching time, zero server latency):

#### Timeframe Characteristics
1. **Weekly (`W`) — Default**:
   - Aggregates daily bars by calendar week.
   - 750 daily bars -> **~159 weekly candles**.
   - Width: **~7.5 pixels per candle**.
   - **Benefit**: Smooths out intraday noise, cleanly framing the multi-year base and the 2+ year old ATH level.
2. **Daily (`D`)**:
   - Displays the latest **250 trading sessions** (~1 calendar year).
   - Width: **~4.8 pixels per candle**.
   - **Benefit**: Provides granular session-by-session price action near current breakout points.
3. **Monthly (`M`)**:
   - Aggregates daily bars by calendar month.
   - 750 daily bars -> **~37 monthly candles**.
   - Width: **~32 pixels per candle**.
   - **Benefit**: Macro perspective showing multi-year secular structure and multi-year bases.

#### Dual Switcher Controls
- **Screen-Level**: Global toolbar toggle `[ Weekly | Daily | Monthly ]` next to Market Cap filter switches all visible cards simultaneously.
- **Card-Level**: Individual card header pills `[ D | W | M ]` allow switching any stock independently.

---

## 6. Verification & Safety Constraints

1. **Test Verification**:
   - Automated test suite verified `/api/screens/aged-ath/stages` returning 59 Latest Date stocks and 118 Last 30 Days stocks.
   - Batch OHLCV verified fetching 250 bars (1-yr) and 750 bars (3-yr) with sub-30ms performance.
   - DOM assertions passed for `agedAthTimeframeContainer`, `aged-ath-tf-pill-btn`, `aggregateWeeklyCandles`, and `aggregateMonthlyCandles`.
2. **Git Integrity**:
   - Original backup preserved at `static/index.html.original_backup`.
   - All modifications remain strictly in local working files; **zero commits or pushes made to Git/GitHub**.

