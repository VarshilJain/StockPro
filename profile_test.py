"""
profile_test.py — Measures exact time of each section in test.py.
Run: python profile_test.py
"""
import gc
import time
import logging
import pandas as pd
import numpy as np
import warnings
from database import get_connection
from config import (
    DOJI_BODY_RATIO, WICK_BODY_RATIO, ADX_THRESHOLD,
    RSI_OVERSOLD_LEVEL, RSI_OVERBOUGHT_LEVEL, TRADING_DAYS_PER_YEAR,
    BASE_MIN_DROP_PCT, BASE_SWING_LOOKBACK_DAYS,
)
from services import detect_rsi_divergence
from test import (
    compute_rsi, compute_adx, hammer, shooting_star, doji, engulfing,
    dark_cloud_cover, morning_star, evening_star, piercing_line,
    get_nr, detect_base,
)

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("profiler")

# Timing helpers
timings = {}

class Timer:
    def __init__(self, label):
        self.label = label
    def __enter__(self):
        self._start = time.perf_counter()
        return self
    def __exit__(self, *_):
        elapsed = time.perf_counter() - self._start
        timings[self.label] = timings.get(self.label, 0) + elapsed


# Config: how many tickers to profile
SAMPLE_SIZE = 20   # change to more for better accuracy

def main():
    log.info("=== StockPro test.py Profiler ===")
    log.info("Sampling %d tickers for timing analysis.", SAMPLE_SIZE)

    # Step 1: Fetch ticker list
    with Timer("1. fetch_ticker_list"):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM ohlc_data ORDER BY ticker LIMIT %s", (SAMPLE_SIZE,))
        tickers = [r[0] for r in cur.fetchall()]

        bench_map = {}
        for bench in ('^CRSLDX', '^NSEI'):
            cur.execute("SELECT date, close FROM ohlc_data WHERE ticker=%s ORDER BY date", (bench,))
            rows = cur.fetchall()
            if rows:
                bench_close = pd.Series([float(r[1]) for r in rows], index=[r[0] for r in rows])
                bench_pct_30 = bench_close.pct_change(periods=30, fill_method=None)
                bench_map = dict(zip(bench_close.index, bench_pct_30))
                break
        cur.close()
        conn.close()

    log.info("Tickers to process: %s", tickers)

    # Step 2: DB reads (per ticker)
    read_conn = get_connection()
    all_groups = {}

    for symbol in tickers:
        with Timer("2. db_read_per_ticker"):
            df = pd.read_sql(
                "SELECT ticker AS Symbol, date AS Timestamp, open AS Open, high AS High, "
                "low AS Low, close AS Close, volume AS Volume, delivery_quantity AS delivery_quantity "
                "FROM ohlc_data WHERE ticker=%s ORDER BY date",
                read_conn, params=(symbol,)
            )
            if df.empty:
                continue
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            df.set_index('Timestamp', inplace=True)
            all_groups[symbol] = df

    read_conn.close()

    # Step 3: Per-ticker signal computation
    for symbol, group in all_groups.items():

        with Timer("3a. candlestick_patterns"):
            group['Hammer']           = hammer(group).astype(int)
            group['Shooting_Star']    = shooting_star(group).astype(int)
            group['Doji']             = doji(group).astype(int)
            group['Engulfing']        = engulfing(group).astype(int)
            group['Dark_Cloud_Cover'] = dark_cloud_cover(group).astype(int)
            group['Morning_Star']     = morning_star(group).astype(int)
            group['Evening_Star']     = evening_star(group).astype(int)
            group['Piercing_Line']    = piercing_line(group).astype(int)

        with Timer("3b. rcs_30d"):
            stock_pct_30 = group['Close'].pct_change(periods=30, fill_method=None)
            bench_pct_30 = pd.Series(group.index.date).map(bench_map).values
            rcs_vals = (stock_pct_30.values - bench_pct_30) * 100.0
            group['RCS_30D'] = [None if pd.isna(v) else round(float(v), 2) for v in rcs_vals]

        with Timer("3c. rolling_52w_multiyr"):
            ref_window = TRADING_DAYS_PER_YEAR - 20
            group['52w_high'] = group['Close'].rolling(window=ref_window, min_periods=1).max().shift(20)
            group['52w_low']  = group['Close'].rolling(window=ref_window, min_periods=1).min().shift(20)
            group['new_52w_high']  = np.where(group['Close'] == group['52w_high'], 1, 0)
            group['new_52w_low']   = np.where(group['Close'] == group['52w_low'],  1, 0)
            group['near_52w_high'] = np.where(
                (group['Close'] >= group['52w_high'] * 0.97) & (group['Close'] < group['52w_high']), 1, 0)
            two_y  = TRADING_DAYS_PER_YEAR * 2
            five_y = TRADING_DAYS_PER_YEAR * 5
            ten_y  = TRADING_DAYS_PER_YEAR * 10
            p2  = group['Close'].rolling(window=two_y,  min_periods=two_y).max().shift(1)
            p5  = group['Close'].rolling(window=five_y, min_periods=five_y).max().shift(1)
            p10 = group['Close'].rolling(window=ten_y,  min_periods=ten_y).max().shift(1)
            group['hit_2y_high_14d']  = ((group['Close'] > p2)  & (group['Close'].shift(1) <= p2.shift(1))).fillna(False).astype(int)
            group['hit_5y_high_14d']  = ((group['Close'] > p5)  & (group['Close'].shift(1) <= p5.shift(1))).fillna(False).astype(int)
            group['hit_10y_high_14d'] = ((group['Close'] > p10) & (group['Close'].shift(1) <= p10.shift(1))).fillna(False).astype(int)

        with Timer("3d. sma_ema_signals"):
            group['SMA4']   = group['Close'].rolling(4).mean()
            group['SMA9']   = group['Close'].rolling(9).mean()
            group['SMA18']  = group['Close'].rolling(18).mean()
            group['SMA50']  = group['Close'].rolling(50).mean()
            group['SMA200'] = group['Close'].rolling(200).mean()
            sma100 = group['Close'].rolling(100, min_periods=1).mean()
            sma150 = group['Close'].rolling(150, min_periods=1).mean()
            ema4   = group['Close'].ewm(span=4,   adjust=False).mean()
            ema5   = group['Close'].ewm(span=5,   adjust=False).mean()
            ema9   = group['Close'].ewm(span=9,   adjust=False).mean()
            ema18  = group['Close'].ewm(span=18,  adjust=False).mean()
            ema21  = group['Close'].ewm(span=21,  adjust=False).mean()
            ema50  = group['Close'].ewm(span=50,  adjust=False).mean()
            ema200 = group['Close'].ewm(span=200, adjust=False).mean()
            group['convergence_3'] = (
                (ema4 > ema9) & (ema9 > ema18) & ((ema4-ema18)/ema18*100 <= 1.5)
                & (group['Close'] > sma100) & (group['Close'] > sma150)
                & (group['Close'] > group['SMA200'])
            ).astype(int)
            group['convergence_4'] = (
                (ema5 > ema9) & (ema9 > ema21) & (ema21 > ema50)
                & ((ema5-ema50)/ema50*100 <= 2.5)
            ).astype(int)
            group['convergence_5'] = (
                (ema4 > ema9) & (ema9 > ema18) & (ema18 > ema50) & (ema50 > ema200)
                & ((ema4-ema200)/ema200*100 <= 5.0)
            ).astype(int)
            group["signal1"] = np.where((group['Close'] > group["SMA9"])  & (group['Close'].shift(1) < group["SMA9"].shift(1)), 1, 0)
            group["signal2"] = np.where((group['Close'] < group["SMA9"])  & (group['Close'].shift(1) > group["SMA9"].shift(1)), 1, 0)
            group["signal3"] = np.where((group['SMA9']  > group["SMA18"]) & (group['SMA9'].shift(1)  < group["SMA18"].shift(1)), 1, 0)
            group["signal4"] = np.where((group["Close"] > group["SMA200"]) & (group["Close"] < group["SMA50"]), 1, 0)
            group["signal5"] = np.where((ema50 > ema200) & (ema50.shift(1) < ema200.shift(1)), 1, 0)
            group["PC"]  = group["SMA50"].pct_change(fill_method=None)
            group["STD"] = group["PC"].rolling(window=TRADING_DAYS_PER_YEAR, min_periods=1).std() * 100
            group["top_decile"] = np.where(group["STD"] >= group["STD"].quantile(0.9), 1, 0)

        with Timer("3e. volume_nr_range"):
            group["Range"]    = group["High"] - group["Low"]
            group["min_5"]    = group["Range"].rolling(5).min()
            group["min_6"]    = group["Range"].rolling(6).min()
            group["min_7"]    = group["Range"].rolling(7).min()
            group["min_8"]    = group["Range"].rolling(8).min()
            group["avg_volume_30"]          = group["Volume"].rolling(30, min_periods=1).mean()
            group["High_Relative_Volume_30"]     = np.where(group["Volume"] > group["avg_volume_30"] * 3, 1, 0)
            group["screen_high_relative_volume"] = np.where(group["Volume"] > group["avg_volume_30"] * 5, 1, 0)
            group["delivery_pct"]            = (group["delivery_quantity"] / group["Volume"].replace(0, np.nan)).clip(upper=1.0)
            group["screen_high_delivery_volume"] = np.where(group["delivery_pct"] > 0.8, 1, 0)
            group["NR5"] = (group["Range"] == group["min_5"]).astype(int)
            group["NR6"] = (group["Range"] == group["min_6"]).astype(int)
            group["NR7"] = (group["Range"] == group["min_7"]).astype(int)
            group["NR8"] = (group["Range"] == group["min_8"]).astype(int)

        with Timer("3f. get_nr_apply -- SUSPECT SLOW"):
            group["NR"] = group.apply(get_nr, axis=1)

        with Timer("3g. rsi_compute"):
            group["RSI14"]     = compute_rsi(group["Close"], 14)
            group["oversold"]  = (group["RSI14"] < RSI_OVERSOLD_LEVEL).astype(int)
            group["overbought"]= (group["RSI14"] > RSI_OVERBOUGHT_LEVEL).astype(int)
            group["rsi_lt_30"] = (group["RSI14"] < 30).astype(int)
            group["rsi_gt_70"] = (group["RSI14"] > 70).astype(int)

        with Timer("3h. rsi_divergence -- SUSPECT SLOW"):
            group["rsi_divergence_type"]      = None
            group["rsi_divergence_direction"] = None
            group["rsi_divergence_score"]     = None
            divergences = detect_rsi_divergence(group, rsi_col="RSI14", lookback=80)
            for div in divergences:
                last_idx = div["pivot_indices"][-1]
                label    = group.index[last_idx]
                existing = group.at[label, "rsi_divergence_score"]
                if pd.isna(existing) or existing is None or div["score"] > existing:
                    group.at[label, "rsi_divergence_type"]     = div["divergence_type"]
                    group.at[label, "rsi_divergence_direction"] = div["divergence_direction"]
                    group.at[label, "rsi_divergence_score"]    = div["score"]

        with Timer("3i. adx_compute"):
            group["adx_trigger"] = (compute_adx(group, 14) >= ADX_THRESHOLD).astype(int)

        with Timer("3j. ath_listing_pct"):
            group['all_time_high']       = group['Close'].cummax()
            group['is_at_ath']           = np.isclose(group['Close'], group['all_time_high']).astype(int)
            ath_changes = group['all_time_high'] != group['all_time_high'].shift(1)
            group['days_since_ath']      = group.groupby(ath_changes.cumsum()).cumcount()
            first_date = group.index[0]
            group['first_listed_date']   = first_date
            group['weeks_since_listing'] = (group.index - first_date).days // 7
            group['pct_63']  = group['Close'].pct_change(periods=63,  fill_method=None)
            group['pct_126'] = group['Close'].pct_change(periods=126, fill_method=None)
            group['pct_252'] = group['Close'].pct_change(periods=252, fill_method=None)
            group['rs_blend']= 0.4 * group['pct_63'] + 0.3 * group['pct_126'] + 0.3 * group['pct_252']

        with Timer("3k. detect_base -- SUSPECT SLOW"):
            group = detect_base(group)

        all_groups[symbol] = group

    # Step 4: Concat
    with Timer("4. concat_all_results"):
        all_results = pd.concat(list(all_groups.values()))
        all_results.reset_index(inplace=True)

    # Step 5: rs_rank cross-sectional
    with Timer("5. rs_rank_crosssectional"):
        rs_rank_calc = (
            all_results.groupby('Timestamp')['rs_blend'].rank(pct=True) * 98 + 1
        ).round(0)
        all_results['rs_rank'] = np.where(all_results['rs_blend'].isna(), np.nan, rs_rank_calc)

    # Print Results
    n = len(tickers)
    total = sum(timings.values())

    print("\n" + "=" * 68)
    print(f"  PROFILING RESULTS  ({n} tickers sampled)")
    print("=" * 68)
    print(f"  {'Section':<43} {'Total':>7}  {'Per ticker':>10}  {'%':>5}")
    print("-" * 68)

    for label, secs in sorted(timings.items(), key=lambda x: -x[1]):
        per_ticker_ms = (secs / n) * 1000
        pct = secs / total * 100
        flag = "  <<< FIX THIS" if pct > 20 else ("  << WATCH" if pct > 10 else "")
        print(f"  {label:<43} {secs:>6.2f}s  {per_ticker_ms:>8.1f}ms  {pct:>4.1f}%{flag}")

    print("-" * 68)
    print(f"  {'TOTAL (sample)':<43} {total:>6.2f}s")
    extrapolated = (total / n) * 2000
    print(f"  {'Estimated for 2000 tickers':<43} {extrapolated:>6.0f}s  ({extrapolated/60:.1f} min)")
    print("=" * 68)
    print()


if __name__ == "__main__":
    main()
