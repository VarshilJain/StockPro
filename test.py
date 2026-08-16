import logging
import pandas as pd
import numpy as np
import warnings
from database import get_connection
from config import (
    DOJI_BODY_RATIO,
    WICK_BODY_RATIO,
    ADX_THRESHOLD,
    RSI_OVERSOLD_LEVEL,
    RSI_OVERBOUGHT_LEVEL,
    TRADING_DAYS_PER_YEAR,
    BASE_MIN_DROP_PCT,
    VCP_MAX_PIVOT_DIST_PCT,
    VCP_MIN_CONTRACTIONS,
    BLUE_SKY_ATH_PROXIMITY_PCT,
    BLUE_SKY_CLOSE_ATH_PROXIMITY_PCT,
    BLUE_SKY_PIVOT_ATH_PROXIMITY,
    MULTI_YEAR_BASE_MIN_DAYS,
    IPO_BASE_MIN_WEEKS,
    IPO_BASE_MAX_WEEKS,
    IPO_BASE_MIN_DAYS,
    IPO_BASE_MIN_DEPTH_PCT,
    IPO_BASE_MAX_DEPTH_PCT,
    BASE_SWING_LOOKBACK_DAYS,
    STAGE3_NET_PROGRESS_WINDOW,
    STAGE3_MAX_PROGRESS_PCT,
)

warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("test_signals")


from services import detect_rsi_divergence

def compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
    if len(close_series) <= period:
        return pd.Series(np.nan, index=close_series.index)
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    seed_gain = gain.rolling(window=period, min_periods=period).mean()
    seed_loss = loss.rolling(window=period, min_periods=period).mean()
    w_gain = pd.Series(np.nan, index=close_series.index)
    w_loss = pd.Series(np.nan, index=close_series.index)
    w_gain.iloc[period] = seed_gain.iloc[period]
    w_loss.iloc[period] = seed_loss.iloc[period]
    w_gain.iloc[period+1:] = gain.iloc[period+1:]
    w_loss.iloc[period+1:] = loss.iloc[period+1:]
    avg_gain = w_gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = w_loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['High']
    low = df['Low']
    close = df['Close']

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = pd.Series(tr).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    plus_di = 100 * (plus_dm_smooth / tr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm_smooth / tr_smooth.replace(0, np.nan))

    di_diff = (plus_di - minus_di).abs()
    di_sum = plus_di + minus_di
    dx = 100 * (di_diff / di_sum.replace(0, np.nan))

    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx.fillna(0)


def hammer(df):
    body = (df["Close"] - df["Open"]).abs()
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    return (lower_wick >= 2 * body) & (upper_wick <= WICK_BODY_RATIO * body) & (body > 0)


def shooting_star(df):
    body = (df["Close"] - df["Open"]).abs()
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]
    return (upper_wick >= 2 * body) & (lower_wick <= WICK_BODY_RATIO * body) & (body > 0)


def doji(df):
    return (df["Close"] - df["Open"]).abs() <= (df["High"] - df["Low"]) * DOJI_BODY_RATIO


def engulfing(df):
    bullish = (
        (df["Open"].shift(1) > df["Close"].shift(1)) &
        (df["Open"] < df["Close"]) &
        (df["Open"] <= df["Close"].shift(1)) &
        (df["Close"] >= df["Open"].shift(1))
    )
    bearish = (
        (df["Open"].shift(1) < df["Close"].shift(1)) &
        (df["Open"] > df["Close"]) &
        (df["Open"] >= df["Close"].shift(1)) &
        (df["Close"] <= df["Open"].shift(1))
    )
    return bullish | bearish


def dark_cloud_cover(df):
    midpoint = (df["Close"].shift(1) + df["Open"].shift(1)) / 2
    return (
        (df["Close"].shift(1) > df["Open"].shift(1)) &
        (df["Open"] > df["High"].shift(1)) &
        (df["Open"] > df["Close"]) &
        (df["Close"] < midpoint)
    )


def morning_star(df):
    c2_body = (df["Close"].shift(2) - df["Open"].shift(2)).abs()
    c1_body = (df["Close"].shift(1) - df["Open"].shift(1)).abs()
    midpoint_c2 = (df["Close"].shift(2) + df["Open"].shift(2)) / 2
    return (
        (df["Close"].shift(2) < df["Open"].shift(2)) &
        (c2_body > 0) &
        (c1_body < 0.3 * c2_body) &
        (df["Open"] < df["Close"]) &
        (df["Close"] > midpoint_c2)
    )


def evening_star(df):
    c2_body = (df["Close"].shift(2) - df["Open"].shift(2)).abs()
    c1_body = (df["Close"].shift(1) - df["Open"].shift(1)).abs()
    midpoint_c2 = (df["Close"].shift(2) + df["Open"].shift(2)) / 2
    return (
        (df["Close"].shift(2) > df["Open"].shift(2)) &
        (c2_body > 0) &
        (c1_body < 0.3 * c2_body) &
        (df["Open"] > df["Close"]) &
        (df["Close"] < midpoint_c2)
    )


def piercing_line(df):
    midpoint = (df["Close"].shift(1) + df["Open"].shift(1)) / 2
    return (
        (df["Close"].shift(1) < df["Open"].shift(1)) &
        (df["Open"] < df["Low"].shift(1)) &
        (df["Open"] < df["Close"]) &
        (df["Close"] > midpoint)
    )


def get_nr(row):
    if row["NR8"] == 1: return 8
    elif row["NR7"] == 1: return 7
    elif row["NR6"] == 1: return 6
    elif row["NR5"] == 1: return 5
    else: return 0


def detect_base(group: pd.DataFrame) -> pd.DataFrame:
    """
    detect_base: V1 Base Detection Algorithm
    
    Known Limitations:
    1. Only walks forward from simple N-day trailing highs, which can miss complex double-bottom structures.
    2. Deeply broken bases might artificially reset and create multiple 'micro bases' instead of one large base.
    3. Contraction logic uses a simplified zigzag detector that may overcount minor noise.
    4. Base lows are strict absolute minimums, ignoring momentary false breakdowns (shakeouts).
    """
    closes = group['Close'].values
    highs = group['High'].values
    lows = group['Low'].values
    dates = group.index.values
    
    n = len(group)
    base_active = np.zeros(n, dtype=int)
    base_start_date = np.empty(n, dtype=object)
    base_length_days = np.zeros(n, dtype=int)
    base_high_arr = np.zeros(n, dtype=float)
    base_low_arr = np.zeros(n, dtype=float)
    base_depth_pct = np.zeros(n, dtype=float)
    pct_from_pivot = np.zeros(n, dtype=float)
    contraction_count = np.zeros(n, dtype=int)
    breakout_today = np.zeros(n, dtype=int)
    last_breakout_level = np.full(n, np.nan, dtype=float)
    
    in_base = False
    cur_base_high = 0.0
    cur_base_start_date = None
    cur_base_low = float('inf')
    cur_contractions = 0
    cur_days_in_base = 0
    cur_last_breakout_level = np.nan
    
    # State machine running peak parameters
    running_peak = 0.0
    running_peak_idx = 0
    
    leg_high = 0.0
    leg_low = float('inf')
    last_swing_low = float('inf')
    prev_swing_low = float('inf')
    last_swing_high = float('inf')
    trend = 0
    
    for i in range(n):
        c = closes[i]
        h = highs[i]
        l = lows[i]
        d = dates[i]
        
        if i == 0:
            running_peak = h
            running_peak_idx = 0
            
        # 1. Breakout Check
        is_breakout = False
        if in_base and c > cur_base_high:
            is_breakout = True
            breakout_today[i] = 1
            cur_last_breakout_level = cur_base_high
            
        # 2. Base Initiation / Pullback Trigger Check
        if not in_base:
            # Update running peak
            if h > running_peak:
                running_peak = h
                running_peak_idx = i
                
            start_idx = max(0, i - BASE_SWING_LOOKBACK_DAYS)
            window_highs = highs[start_idx:i+1]
            recent_high = window_highs.max()
            
            if recent_high > 0 and ((recent_high - c) / recent_high * 100) >= BASE_MIN_DROP_PCT:
                in_base = True
                cur_base_high = running_peak
                cur_base_start_date = dates[running_peak_idx]
                cur_days_in_base = i - running_peak_idx
                cur_base_low = np.min(lows[running_peak_idx:i+1])
                cur_contractions = 0
                
                leg_high = h
                leg_low = l
                last_swing_low = float('inf')
                prev_swing_low = float('inf')
                last_swing_high = cur_base_high
                trend = -1

        # 3. Base Tracking Logic
        if in_base:
            cur_days_in_base = i - running_peak_idx
            if l < cur_base_low:
                cur_base_low = l
                
            if trend == -1:
                if l < leg_low:
                    leg_low = l
                if i > 0 and c > highs[i-1]:
                    trend = 1
                    last_swing_low = leg_low
                    leg_high = h
            elif trend == 1:
                if h > leg_high:
                    leg_high = h
                if i > 0 and c < lows[i-1]:
                    trend = -1
                    if leg_high < last_swing_high and last_swing_low > prev_swing_low:
                        cur_contractions += 1
                    last_swing_high = leg_high
                    prev_swing_low = last_swing_low
                    leg_low = l
            
            base_active[i] = 1
            base_start_date[i] = cur_base_start_date
            base_length_days[i] = cur_days_in_base
            base_high_arr[i] = cur_base_high
            base_low_arr[i] = cur_base_low
            base_depth_pct[i] = (cur_base_high - cur_base_low) / cur_base_high * 100
            pct_from_pivot[i] = (cur_base_high - c) / cur_base_high * 100
            contraction_count[i] = cur_contractions
            
            if is_breakout:
                in_base = False
                running_peak = h
                running_peak_idx = i
                cur_base_high = 0.0
                cur_base_low = float('inf')
                cur_contractions = 0
                cur_days_in_base = 0
        else:
            base_active[i] = 0
            base_start_date[i] = d
            base_length_days[i] = 0
            base_high_arr[i] = 0.0
            base_low_arr[i] = 0.0
            base_depth_pct[i] = 0.0
            pct_from_pivot[i] = 0.0
            contraction_count[i] = 0
            
        last_breakout_level[i] = cur_last_breakout_level
        
    group = group.copy()
    group['base_active'] = base_active
    group['base_start_date'] = pd.to_datetime(base_start_date)
    group['base_length_days'] = base_length_days
    group['base_high'] = base_high_arr
    group['base_low'] = base_low_arr
    group['base_depth_pct'] = base_depth_pct
    group['pct_from_pivot'] = pct_from_pivot
    group['contraction_count'] = contraction_count
    group['breakout_today'] = breakout_today
    group['last_breakout_level'] = last_breakout_level
    return group


def classify_stages(all_results: pd.DataFrame) -> pd.DataFrame:
    """
    Computes Stage 1-4 classification and buckets.
    Operates on the entire concatenated dataframe (all symbols).
    """
    sma200_rising = (all_results['SMA200'] > all_results.groupby('Symbol')['SMA200'].shift(20))
    sma200_falling = (all_results['SMA200'] < all_results.groupby('Symbol')['SMA200'].shift(20))
    
    stage_2_cond = (all_results['Close'] > all_results['SMA200']) & sma200_rising
    stage_4_cond = (all_results['Close'] < all_results['SMA200']) & sma200_falling
    
    # Stage 3: Topping (net price progress < STAGE3_MAX_PROGRESS_PCT over STAGE3_NET_PROGRESS_WINDOW days)
    past_close = all_results.groupby('Symbol')['Close'].shift(STAGE3_NET_PROGRESS_WINDOW)
    net_progress = ((all_results['Close'] - past_close) / past_close).abs() * 100
    stage_3_cond = (
        (all_results['Close'] >= all_results['SMA200'] * 0.9) & 
        (~sma200_rising) & (~sma200_falling) & 
        (all_results['top_decile'] == 1) &
        (net_progress <= STAGE3_MAX_PROGRESS_PCT)
    )
    
    # Mutually exclusive assignment
    cond_list = [stage_2_cond, stage_3_cond, stage_4_cond]
    choice_list = [2, 3, 4]
    all_results['stage'] = np.select(cond_list, choice_list, default=1)
    
    # Bucket mapping
    stage_bucket = pd.Series("unknown", index=all_results.index)
    
    # forming -> stage == 1 AND base_active == 1
    stage_bucket.loc[(all_results['stage'] == 1) & (all_results['base_active'] == 1)] = "forming"
    
    # fresh_breakout -> breakout_today == 1 in the last 10 trading sessions
    recent_breakout = all_results.groupby('Symbol')['breakout_today'].rolling(10, min_periods=1).max().reset_index(level=0, drop=True) == 1
    stage_bucket.loc[recent_breakout] = "fresh_breakout"
    
    # climbing -> stage == 2 AND breakout happened earlier
    stage_bucket.loc[(all_results['stage'] == 2) & (~recent_breakout)] = "climbing"
    
    # played_out -> stage in (3, 4) AND had a breakout this calendar year that has since failed
    current_year = all_results['Timestamp'].dt.year
    breakout_year = pd.Series(np.where(all_results['breakout_today'] == 1, current_year, np.nan), index=all_results.index)
    # Forward fill the breakout year per symbol to check if the last breakout was this year
    last_breakout_year = breakout_year.groupby(all_results['Symbol']).ffill()
    failed_breakout = (last_breakout_year == current_year) & (all_results['Close'] < all_results['last_breakout_level'])
    
    stage_bucket.loc[all_results['stage'].isin([3, 4]) & failed_breakout] = "played_out"
    
    all_results['stage_bucket'] = stage_bucket
    return all_results


def main():
    log.info("Loading OHLCV data from MySQL `ohlc_data` table...")
    conn = get_connection()
    query = """
        SELECT ticker AS Symbol, date AS Timestamp, open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume, delivery_quantity AS delivery_quantity
        FROM ohlc_data
        ORDER BY ticker, date
    """
    data = pd.read_sql(query, conn)
    conn.close()
    log.info("Loaded %d rows for %d symbols.", len(data), data['Symbol'].nunique())

    data['Timestamp'] = pd.to_datetime(data['Timestamp'])
    data.set_index('Timestamp', inplace=True)

    all_results_list = []

    # Pre-compute benchmark 30-day percentage change for ^CRSLDX or ^NSEI
    bench_group = data[data['Symbol'] == '^CRSLDX']
    if bench_group.empty:
        bench_group = data[data['Symbol'] == '^NSEI']

    if not bench_group.empty:
        bench_pct_30 = bench_group['Close'].pct_change(periods=30, fill_method=None)
        bench_map = dict(zip(bench_group.index.date, bench_pct_30))
    else:
        bench_map = {}

    log.info("Computing signals and technical indicators...")
    for symbol, group in data.groupby('Symbol'):
        group['Hammer'] = hammer(group).astype(int)
        group['Shooting_Star'] = shooting_star(group).astype(int)
        group['Doji'] = doji(group).astype(int)
        group['Engulfing'] = engulfing(group).astype(int)
        group['Dark_Cloud_Cover'] = dark_cloud_cover(group).astype(int)
        group['Morning_Star'] = morning_star(group).astype(int)
        group['Evening_Star'] = evening_star(group).astype(int)
        group['Piercing_Line'] = piercing_line(group).astype(int)

        # RCS 30D
        stock_pct_30 = group['Close'].pct_change(periods=30, fill_method=None)
        bench_pct_30 = pd.Series(group.index.date).map(bench_map).values
        rcs_vals = (stock_pct_30.values - bench_pct_30) * 100.0
        group['RCS_30D'] = [None if pd.isna(v) else round(float(v), 2) for v in rcs_vals]

        ref_window = TRADING_DAYS_PER_YEAR - 20
        group['52w_high'] = group['Close'].rolling(window=ref_window, min_periods=1).max().shift(20)
        group['52w_low'] = group['Close'].rolling(window=ref_window, min_periods=1).min().shift(20)

        group['new_52w_high'] = np.where(group['Close'] == group['52w_high'], 1, 0)
        group['new_52w_low'] = np.where(group['Close'] == group['52w_low'], 1, 0)
        group['near_52w_high'] = np.where((group['Close'] >= group['52w_high'] * 0.97) & (group['Close'] < group['52w_high']), 1, 0)

        two_y_window = TRADING_DAYS_PER_YEAR * 2
        five_y_window = TRADING_DAYS_PER_YEAR * 5
        ten_y_window = TRADING_DAYS_PER_YEAR * 10

        prior_2y_max_close = group['Close'].rolling(window=two_y_window, min_periods=two_y_window).max().shift(1)
        prior_5y_max_close = group['Close'].rolling(window=five_y_window, min_periods=five_y_window).max().shift(1)
        prior_10y_max_close = group['Close'].rolling(window=ten_y_window, min_periods=ten_y_window).max().shift(1)

        cross_2y = (group['Close'] > prior_2y_max_close) & (group['Close'].shift(1) <= prior_2y_max_close.shift(1))
        cross_5y = (group['Close'] > prior_5y_max_close) & (group['Close'].shift(1) <= prior_5y_max_close.shift(1))
        cross_10y = (group['Close'] > prior_10y_max_close) & (group['Close'].shift(1) <= prior_10y_max_close.shift(1))

        group['hit_2y_high_14d'] = cross_2y.fillna(False).astype(int)
        group['hit_5y_high_14d'] = cross_5y.fillna(False).astype(int)
        group['hit_10y_high_14d'] = cross_10y.fillna(False).astype(int)

        # SMAs
        group['SMA4'] = group['Close'].rolling(window=4).mean()
        group['SMA9'] = group['Close'].rolling(window=9).mean()
        group['SMA18'] = group['Close'].rolling(window=18).mean()
        group['SMA50'] = group['Close'].rolling(window=50).mean()
        group['SMA200'] = group['Close'].rolling(window=200).mean()

        sma100 = group['Close'].rolling(window=100, min_periods=1).mean()
        sma150 = group['Close'].rolling(window=150, min_periods=1).mean()

        # EMAs
        ema4 = group['Close'].ewm(span=4, adjust=False).mean()
        ema5 = group['Close'].ewm(span=5, adjust=False).mean()
        ema9 = group['Close'].ewm(span=9, adjust=False).mean()
        ema18 = group['Close'].ewm(span=18, adjust=False).mean()
        ema21 = group['Close'].ewm(span=21, adjust=False).mean()
        ema50 = group['Close'].ewm(span=50, adjust=False).mean()
        ema200 = group['Close'].ewm(span=200, adjust=False).mean()

        # Convergence signals with tightness (squeeze) constraints
        spread_3 = (ema4 - ema18) / ema18 * 100
        group['convergence_3'] = ((ema4 > ema9) & (ema9 > ema18) & (spread_3 <= 1.5) & (group['Close'] > sma100) & (group['Close'] > sma150) & (group['Close'] > group['SMA200'])).astype(int)
        
        spread_4 = (ema5 - ema50) / ema50 * 100
        group['convergence_4'] = ((ema5 > ema9) & (ema9 > ema21) & (ema21 > ema50) & (spread_4 <= 2.5)).astype(int)

        spread_5 = (ema4 - ema200) / ema200 * 100
        group['convergence_5'] = ((ema4 > ema9) & (ema9 > ema18) & (ema18 > ema50) & (ema50 > ema200) & (spread_5 <= 5.0)).astype(int)

        cross_sma_below = (group['Close'] > group["SMA9"]) & (group['Close'].shift(1) < group["SMA9"].shift(1))
        group["signal1"] = np.where(cross_sma_below, 1, 0)

        cross_sma_above = (group['Close'] < group["SMA9"]) & (group['Close'].shift(1) > group["SMA9"].shift(1))
        group["signal2"] = np.where(cross_sma_above, 1, 0)

        cross_sma9_sma18_below = (group['SMA9'] > group["SMA18"]) & (group['SMA9'].shift(1) < group["SMA18"].shift(1))
        group["signal3"] = np.where(cross_sma9_sma18_below, 1, 0)

        between = (group["Close"] > group["SMA200"]) & (group["Close"] < group["SMA50"])
        group["signal4"] = np.where(between, 1, 0)

        golden_cross_over = (group["SMA50"] > group["SMA200"]) & (group["SMA50"].shift(1) < group["SMA200"].shift(1))
        group["signal5"] = np.where(golden_cross_over, 1, 0)

        group["PC"] = group["SMA50"].pct_change(fill_method=None)
        group["STD"] = group["PC"].rolling(window=TRADING_DAYS_PER_YEAR, min_periods=1).std() * 100

        threshold = group["STD"].quantile(0.9)
        group["top_decile"] = np.where(group["STD"] >= threshold, 1, 0)

        group["Range"] = group["High"] - group["Low"]
        group["min_5"] = group["Range"].rolling(5).min()
        group["min_6"] = group["Range"].rolling(6).min()
        group["min_7"] = group["Range"].rolling(7).min()
        group["min_8"] = group["Range"].rolling(8).min()
        group["avg_volume_30"] = group["Volume"].rolling(window=30, min_periods=1).mean()
        group["High_Relative_Volume_30"] = np.where(group["Volume"] > (group["avg_volume_30"] * 3), 1, 0)
        group["screen_high_relative_volume"] = np.where(group["Volume"] > (group["avg_volume_30"] * 5), 1, 0)
        group["delivery_pct"] = (group["delivery_quantity"] / group["Volume"].replace(0, np.nan)).clip(upper=1.0)
        group["screen_high_delivery_volume"] = np.where(group["delivery_pct"] > 0.8, 1, 0)
        group["NR5"] = (group["Range"] == group["min_5"]).astype(int)
        group["NR6"] = (group["Range"] == group["min_6"]).astype(int)
        group["NR7"] = (group["Range"] == group["min_7"]).astype(int)
        group["NR8"] = (group["Range"] == group["min_8"]).astype(int)

        group["NR"] = group.apply(get_nr, axis=1)

        group["RSI14"] = compute_rsi(group["Close"], 14)
        group["oversold"] = (group["RSI14"] < RSI_OVERSOLD_LEVEL).astype(int)
        group["overbought"] = (group["RSI14"] > RSI_OVERBOUGHT_LEVEL).astype(int)
        group["rsi_lt_30"] = (group["RSI14"] < 30).astype(int)
        group["rsi_gt_70"] = (group["RSI14"] > 70).astype(int)

        # RSI Divergence detection
        group["rsi_divergence_type"] = None
        group["rsi_divergence_direction"] = None
        group["rsi_divergence_score"] = None
        
        divergences = detect_rsi_divergence(group, rsi_col="RSI14", lookback=80)
        for div in divergences:
            last_idx = div["pivot_indices"][-1]
            label = group.index[last_idx]
            existing_score = group.at[label, "rsi_divergence_score"]
            if pd.isna(existing_score) or existing_score is None or div["score"] > existing_score:
                group.at[label, "rsi_divergence_type"] = div["divergence_type"]
                group.at[label, "rsi_divergence_direction"] = div["divergence_direction"]
                group.at[label, "rsi_divergence_score"] = div["score"]

        adx = compute_adx(group, 14)
        group["adx_trigger"] = (adx >= ADX_THRESHOLD).astype(int)

        # ---------------------------------------------------------
        # NEW COLUMNS: ATH, Listing, Base Detection
        # ---------------------------------------------------------
        group['all_time_high'] = group['Close'].cummax()
        group['is_at_ath'] = np.isclose(group['Close'], group['all_time_high']).astype(int)
        
        ath_changes = group['all_time_high'] != group['all_time_high'].shift(1)
        group['days_since_ath'] = group.groupby(ath_changes.cumsum()).cumcount()

        first_date = group.index[0]
        # Flagging as first date in our DB, not verified IPO date.
        group['first_listed_date'] = first_date
        group['weeks_since_listing'] = (group.index - first_date).days // 7
        
        # RS Blend for relative strength ranking later
        group['pct_63'] = group['Close'].pct_change(periods=63, fill_method=None)
        group['pct_126'] = group['Close'].pct_change(periods=126, fill_method=None)
        group['pct_252'] = group['Close'].pct_change(periods=252, fill_method=None)
        group['rs_blend'] = 0.4 * group['pct_63'] + 0.3 * group['pct_126'] + 0.3 * group['pct_252']
        
        group = detect_base(group)

        group = group.drop(['NR5', 'NR6', 'NR7', 'NR8', 'min_5', 'min_6', 'min_7', 'min_8', 'avg_volume_30', 'Range', 'pct_63', 'pct_126', 'pct_252', 'delivery_pct'], axis=1)
        all_results_list.append(group)

    all_results = pd.concat(all_results_list)
    all_results.reset_index(inplace=True)

    log.info("Computing relative strength ranks and pattern screens...")
    # RS Rank (1-99), preserving NaNs for <12 months history
    rs_rank_calc = (all_results.groupby('Timestamp')['rs_blend'].rank(pct=True) * 98 + 1).round(0)
    all_results['rs_rank'] = np.where(all_results['rs_blend'].isna(), np.nan, rs_rank_calc)
    
    # ---------------------------------------------------------
    # PATTERN SCREENS
    # ---------------------------------------------------------
    
    # screen_vcp
    all_results['screen_vcp'] = (
        (all_results['Close'] > all_results['SMA50']) &
        (all_results['SMA50'] > all_results['SMA200']) &
        (all_results['base_active'] == 1) &
        (all_results['contraction_count'] >= VCP_MIN_CONTRACTIONS) &
        (all_results['pct_from_pivot'] <= VCP_MAX_PIVOT_DIST_PCT)
    ).astype(int)
    
    # screen_blue_sky (All-Time Peak Breakout - APB)
    all_results['screen_blue_sky'] = (
        (all_results['base_active'] == 1) &
        (all_results['base_high'] >= all_results['all_time_high'] * (1 - BLUE_SKY_ATH_PROXIMITY_PCT / 100.0)) &
        (all_results['pct_from_pivot'] <= 5.0) &
        (all_results['Close'] >= all_results['all_time_high'] * (1 - BLUE_SKY_CLOSE_ATH_PROXIMITY_PCT / 100.0)) &
        (all_results['rs_rank'].between(70, 99))
    ).astype(int)
    
    # screen_multi_year_breakout
    all_results['screen_multi_year_breakout'] = (
        (all_results['base_length_days'] >= MULTI_YEAR_BASE_MIN_DAYS) &
        (all_results['Close'] > all_results['SMA200']) &
        (all_results['rs_rank'].between(60, 99)) &
        (all_results['pct_from_pivot'] <= VCP_MAX_PIVOT_DIST_PCT)
    ).astype(int)
    
    # screen_ipo_base (RS Rank naturally low for IPOs, so we exclude it as requested)
    all_results['screen_ipo_base'] = (
        (all_results['weeks_since_listing'].between(IPO_BASE_MIN_WEEKS, IPO_BASE_MAX_WEEKS)) &
        (all_results['base_length_days'] >= IPO_BASE_MIN_DAYS) &
        (all_results['base_depth_pct'].between(IPO_BASE_MIN_DEPTH_PCT, IPO_BASE_MAX_DEPTH_PCT)) &
        (all_results['Close'] > all_results['SMA50']) &
        (all_results['pct_from_pivot'] <= VCP_MAX_PIVOT_DIST_PCT)
    ).astype(int)
    
    # ---------------------------------------------------------
    # STAGE CLASSIFICATION
    # ---------------------------------------------------------
    all_results = classify_stages(all_results)

    sql_columns = [
        "Timestamp", "Open", "High", "Low", "Close", "Volume", "Symbol",
        "Hammer", "Shooting_Star", "Doji", "Engulfing", "Dark_Cloud_Cover",
        "Morning_Star", "Evening_Star", "Piercing_Line",
        "SMA4", "SMA9", "SMA18", "SMA50", "SMA200",
        "signal1", "signal2", "signal3", "signal4", "signal5",
        "PC", "STD", "top_decile",
        "52w_high", "52w_low", "new_52w_high", "new_52w_low", "near_52w_high",
        "NR", "High_Relative_Volume_30",
        "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d",
        "RSI14", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70",
        "rsi_divergence_type", "rsi_divergence_direction", "rsi_divergence_score",
        "adx_trigger", "convergence_5", "convergence_3", "convergence_4",
        "RCS_30D",
        "all_time_high", "is_at_ath", "days_since_ath", "first_listed_date", "weeks_since_listing",
        "rs_rank", "base_active", "base_start_date", "base_length_days", "base_high", "base_low", 
        "base_depth_pct", "pct_from_pivot", "contraction_count", "breakout_today", "last_breakout_level",
        "screen_vcp", "screen_blue_sky", "screen_multi_year_breakout", "screen_ipo_base", "screen_high_relative_volume", "screen_high_delivery_volume",
        "stage", "stage_bucket"
    ]

    log.info("Saving results directly into MySQL `historical_data` table...")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("TRUNCATE TABLE historical_data")
        conn.commit()
        log.info("Cleared existing rows from `historical_data`.")

        df_clean = all_results[sql_columns].replace({np.nan: None, np.inf: None, -np.inf: None})

        placeholders = ",".join(["%s"] * len(sql_columns))
        insert_query = f"INSERT INTO historical_data ({','.join(sql_columns)}) VALUES ({placeholders})"

        batch_size = 10000
        rows = df_clean.values.tolist()
        total_rows = len(rows)

        for i in range(0, total_rows, batch_size):
            batch = rows[i:i + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()
            log.info("Inserted %d / %d rows into `historical_data`...", min(i + batch_size, total_rows), total_rows)

        log.info("Successfully updated `historical_data` with %d rows! ✅", total_rows)

        # Create pattern_events table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pattern_events (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                screen_name VARCHAR(50) NOT NULL,
                triggered_date DATE NOT NULL,
                pivot_price DECIMAL(10,2) NOT NULL,
                outcome VARCHAR(20) DEFAULT 'active',
                outcome_date DATE,
                UNIQUE KEY (symbol, screen_name, triggered_date)
            )
        """)
        conn.commit()

        # Rebuild pattern_events from full history
        log.info("Rebuilding pattern_events from full history...")
        cursor.execute("TRUNCATE TABLE pattern_events")
        
        screen_names = ['screen_vcp', 'screen_blue_sky', 'screen_multi_year_breakout', 'screen_ipo_base']
        events_to_insert = []
        
        # We need a quick way to find future failed dates for each symbol
        grouped = all_results.groupby('Symbol')
        
        # Identify all historical breakouts
        breakout_mask = (all_results['breakout_today'] == 1) & (all_results[screen_names].sum(axis=1) > 0)
        breakout_rows = all_results[breakout_mask]
        
        for _, row in breakout_rows.iterrows():
            sym = row['Symbol']
            trigger_date = row['Timestamp']
            pivot = row['base_high']
            
            triggered_screens = [s for s in screen_names if row[s] == 1]
            if not triggered_screens:
                continue
                
            sym_df = grouped.get_group(sym)
            # Find future rows where it drops below pivot AND stage is 3 or 4
            future = sym_df[sym_df['Timestamp'] > trigger_date]
            failed = future[(future['Close'] < pivot) & (future['stage'].isin([3, 4]))]
            
            outcome = 'active'
            outcome_date = None
            if not failed.empty:
                outcome = 'failed'
                first_failed = failed.iloc[0]['Timestamp']
                outcome_date = first_failed.date() if hasattr(first_failed, 'date') else first_failed
                
            # Date for trigger
            t_date = trigger_date.date() if hasattr(trigger_date, 'date') else trigger_date
            
            for screen in triggered_screens:
                events_to_insert.append((sym, screen, t_date, pivot, outcome, outcome_date))
                
        if events_to_insert:
            insert_event_query = """
                INSERT INTO pattern_events (symbol, screen_name, triggered_date, pivot_price, outcome, outcome_date)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            batch_size = 10000
            for i in range(0, len(events_to_insert), batch_size):
                cursor.executemany(insert_event_query, events_to_insert[i:i + batch_size])
            conn.commit()
            log.info("Logged %d historical pattern events.", len(events_to_insert))
            
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
