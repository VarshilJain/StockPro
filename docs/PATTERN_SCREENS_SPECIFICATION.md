# StockPro Pattern Screens: Quantitative Specification & Mathematical Logic

This document specifies the exact logic, quantitative rules, state machine, and parameters for **PCS**, **ATB**, **Multi-Year Breakout (MYB)**, **Base Detection Engine**, **IPO Base**, and **Stan Weinstein Stage Classification**.

---

## 1. Base Detection Engine (`detect_base`)

A state-machine algorithm that tracks base building directly from daily OHLCV candlestick data.

### State Machine Lifecycle

```
[ New Peak / Rally ]
        │
        ▼ (Drop >= 5% from peak)
[ Base Initiated ] ──► Base High (Pivot) = Peak Price
        │          ──► Base Low = Absolute lowest low in window
        │          ──► Days in Base = count(trading days)
        │
   [ ZigZag Tracking ]
   • Contraction Count: counts wave contractions (T1, T2, T3)
   • % from Pivot: (Base High - Close) / Base High * 100
        │
        ▼ (Close > Base High)
[ Breakout Confirmed ] ──► Base terminates, breakout_today = 1, state resets
```

### Core Metrics & Formulas

1. **Base Initiation Trigger:**
   $$\text{Recent High} = \max_{t-21 \le i \le t}(\text{High}_i)$$
   $$\text{Drop \%} = \frac{\text{Recent High} - \text{Close}_t}{\text{Recent High}} \times 100 \ge 5.0\%$$

2. **Base Depth %:**
   $$\text{Base Depth \%} = \frac{\text{Base High} - \text{Base Low}}{\text{Base High}} \times 100$$

3. **Pivot Proximity (% from Pivot):**
   $$\text{Distance from Pivot \%} = \frac{\text{Base High} - \text{Close}_t}{\text{Base High}} \times 100$$

4. **Contraction Count (VCP ZigZag Logic):**
   Tracks alternating swing highs and swing lows:
   $$\text{Contraction Verified if: } \text{Swing High}_k < \text{Swing High}_{k-1} \quad \text{AND} \quad \text{Swing Low}_k > \text{Swing Low}_{k-1}$$

---

## 2. PCS (Price Compression Setup / VCP)

Identifies stocks in established uptrends where price volatility has compressed into tight coil near the pivot.

### Quantitative Rules:
1. **Trend Template:**
   $$\text{Close} > \text{SMA}_{50} \quad \text{AND} \quad \text{SMA}_{50} > \text{SMA}_{200}$$
2. **Active Consolidation:**
   $$\text{base\_active} = 1$$
3. **Volatility Contraction Count:**
   $$\text{contraction\_count} \ge 2 \quad (\text{typically 2 to 4 contractions: } T_1 \to T_2 \to T_3)$$
4. **Pivot Proximity:**
   $$\text{pct\_from\_pivot} \le 8.0\% \quad (\text{tight to the breakout pivot})$$
5. **Volume Signature (Dry-up):**
   $$\text{Volume}_{\text{recent}} < 0.70 \times \text{SMA}(\text{Volume}, 20)$$

---

## 3. ATB (All-Time Breakout / Blue Sky Setup / APB)

Screens for market leaders emerging out of consolidations directly at or adjacent to their All-Time Highs (Zero Overhead Supply).

### Quantitative Rules:
1. **Active Base:**
   $$\text{base\_active} = 1$$
2. **Base High Proximity to ATH:**
   $$\text{Base High} \ge \text{All Time High} \times (1 - 0.03) \quad (\text{within 3\% of ATH})$$
3. **Current Close Proximity to ATH:**
   $$\text{Close} \ge \text{All Time High} \times (1 - 0.05) \quad (\text{within 5\% of ATH})$$
4. **Pivot Proximity:**
   $$\text{pct\_from\_pivot} \le 5.0\%$$
5. **Market Leadership (Relative Strength Rank):**
   $$\text{RS Rank} \in [70, 99] \quad (\text{Top 30\% market performance})$$

---

## 4. Multi-Year Breakout (MYB)

Screens for structural, multi-year consolidations (often 1.5 to 3+ years) resolving to the upside.

### Quantitative Rules:
1. **Base Length Duration:**
   $$\text{base\_length\_days} \ge 365 \text{ days} \quad (\text{typically } 365 \text{ to } 500+ \text{ trading days})$$
2. **Macro Trend Alignment:**
   $$\text{Close} > \text{SMA}_{200}$$
3. **Relative Strength:**
   $$\text{RS Rank} \in [60, 99]$$
4. **Pivot Proximity:**
   $$\text{pct\_from\_pivot} \le 20.0\%$$

---

## 5. IPO Base Screen

Screens for newly listed companies constructing their very first constructive base before Stage 2 launch.

### Quantitative Rules:
1. **Age Post-Listing:**
   $$2 \le \text{weeks\_since\_listing} \le 50 \quad (\text{under 1 year of public trading})$$
2. **Minimum Base Duration:**
   $$\text{base\_length\_days} \ge 21 \text{ trading days} \quad (\ge 1 \text{ month})$$
3. **Constructive Base Depth:**
   $$2.0\% \le \text{base\_depth\_pct} \le 35.0\% \quad (\text{avoids wild, speculative swings})$$
4. **Intermediate Support:**
   $$\text{Close} > \text{SMA}_{50}$$
5. **Pivot Proximity:**
   $$\text{pct\_from\_pivot} \le 8.0\%$$

---

## 6. Stan Weinstein Stage Classification (Stages 1 – 4)

Divides price action into the four foundational market stages based on the 200-day Simple Moving Average (SMA200) trend and price momentum.

### Classification Rules:

| Stage | Name | Quantitative Rule |
| :--- | :--- | :--- |
| **Stage 1** | Base / Accumulation | Price oscillating around flat $\text{SMA}_{200}$ ($\Delta \text{SMA}_{200} \approx 0$) |
| **Stage 2** | Advancing / Uptrend | $\text{Close} > \text{SMA}_{200}$ **AND** $\text{SMA}_{200}(t) > \text{SMA}_{200}(t-20)$ (rising slope) |
| **Stage 3** | Topping / Distribution | High volatility, net price progress $\le 5\%$ over 30 days, losing $\text{SMA}_{200}$ |
| **Stage 4** | Declining / Downtrend | $\text{Close} < \text{SMA}_{200}$ **AND** $\text{SMA}_{200}(t) < \text{SMA}_{200}(t-20)$ (falling slope) |

### Actionable Sub-Buckets:
- `forming`: Stage 1 + $\text{base\_active} = 1$
- `breakout`: Stage 2 + $\text{breakout\_today} = 1$
- `extended`: Stage 2 + $\text{Close} > 1.20 \times \text{SMA}_{50}$ (overextended from moving average)
- `pullback`: Stage 2 + $\text{Close} \le 1.03 \times \text{SMA}_{50}$ (orderly pullback to support)
- `distribution`: Stage 3
- `declining`: Stage 4

---

## 7. Relative Strength Ranking (RS Blend & RS Rank)

Calculates the William O'Neil / IBD style blended performance percentile against the broad market:

$$\text{RS Blend} = 0.40 \cdot \text{Perf}_{63\text{d}} + 0.30 \cdot \text{Perf}_{126\text{d}} + 0.30 \cdot \text{Perf}_{252\text{d}}$$

Where:
- $\text{Perf}_{63\text{d}}$: 3-month price return
- $\text{Perf}_{126\text{d}}$: 6-month price return
- $\text{Perf}_{252\text{d}}$: 12-month price return

$$\text{RS Rank} = \text{PercentileRank}(\text{RS Blend}) \times 98 + 1 \quad \in [1, 99]$$
