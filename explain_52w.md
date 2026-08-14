# 52-Week High & Low Calculation Logic

This document explains the mathematical and programmatic implementation of the **52-Week High** and **52-Week Low** signals within the StockPro Analytics platform.

---

## 1. Parameters & Configuration
The calculation window is defined in [`config.py`](file:///c:/Users/Varshil/Google%20Drive/It-Vedant/Stock_Market_Project/config.py#L30):
```python
TRADING_DAYS_PER_YEAR = 252  # Number of trading sessions in a standard calendar year
```
* **Why 252?** Although a year has 365 calendar days, there are approximately 252 active trading sessions on the stock exchange (NSE/BSE) after subtracting weekends and official market holidays.

---

## 2. Programmatic Calculation (Python)
The signals are computed on a per-symbol basis within the data pipeline inside [`test.py`](file:///c:/Users/Varshil/Google%20Drive/It-Vedant/Stock_Market_Project/test.py#L393-L397):

```python
# 1. Calculate the rolling 52-week high excluding the most recent 20 sessions (ref window 232, shifted by 20)
ref_window = TRADING_DAYS_PER_YEAR - 20
group['52w_high'] = group['Close'].rolling(window=ref_window, min_periods=1).max().shift(20)

# 2. Calculate the rolling 52-week low excluding the most recent 20 sessions (ref window 232, shifted by 20)
group['52w_low'] = group['Close'].rolling(window=ref_window, min_periods=1).min().shift(20)

# 3. Trigger binary flag (1 or 0) if current close is equal to the age-constrained 52-week high
group['new_52w_high'] = np.where(group['Close'] == group['52w_high'], 1, 0)

# 4. Trigger binary flag (1 or 0) if current close is equal to the age-constrained 52-week low
group['new_52w_low'] = np.where(group['Close'] == group['52w_low'], 1, 0)
```

### Detailed Execution Mechanics:
1. **`rolling(window=232, min_periods=1).max().shift(20)`**:
   * Creates a sliding window containing 232 sessions and shifts it forward by 20 sessions.
   * This effectively excludes the most recent 20 trading sessions $[t-19, t]$ from the 252-day window, measuring the maximum price established in the range $[t-251, t-20]$.
   * `min_periods=1` ensures that even if a stock has less than 252 days of historical data (e.g. a newly listed IPO stock), the system will still calculate the high/low based on whatever history is available instead of returning `NaN`.
2. **`.max()` & `.min()`**:
   * Extracts the maximum and minimum closing price values in that 252-day window.
3. **`np.where(...)`**:
   * Evaluates if the current close price matches the calculated rolling high or low.
   * If `True`, sets the flag to `1` (triggered).
   * If `False`, sets the flag to `0`.

---

## 3. Database Schema Mapping
The resulting values are stored as daily records in the `historical_data` table in MySQL:
* **`52w_high`**: `DECIMAL(10, 2)` (The actual high price value).
* **`52w_low`**: `DECIMAL(10, 2)` (The actual low price value).
* **`new_52w_high`**: `TINYINT` (`1` if triggered today, `0` otherwise).
* **`new_52w_low`**: `TINYINT` (`1` if triggered today, `0` otherwise).

---

## 4. Frontend Integration
These columns are exposed as searchable scan criteria in the **Stock Scanner** and **Technical Signal Scanner** dropdown lists:
* Selecting **"New 52-Week High"** or **"New 52-Week Low"** queries the database for rows where `new_52w_high = 1` or `new_52w_low = 1` on the targeted dates.

---

## 5. Near 52-Week High Expansion (Within 3%)
A special condition is added to identify stocks that are trading very close to their 52-week peak, indicating high consolidation/potential breakout strength.

### Code Logic:
```python
# Trigger binary flag (1 or 0) if current close is within 3% of the 52-week high,
# excluding the exact 52-week high breakout itself.
group['near_52w_high'] = np.where((group['Close'] >= group['52w_high'] * 0.97) & (group['Close'] < group['52w_high']), 1, 0)
```
* **Trigger Window:** The signal triggers `1` if:
  $$\text{52w\_high} \times 0.97 \le \text{Close} < \text{52w\_high}$$
* **Usage:** Consumed by the scan engine and scanners under the label **"Near 52-Week High (within 3%)"**.

