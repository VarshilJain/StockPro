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
)

warnings.filterwarnings("ignore", category=UserWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("test_signals")


def compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


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


def main():
    log.info("Loading OHLCV data from MySQL `ohlc_data` table...")
    conn = get_connection()
    query = """
        SELECT ticker AS Symbol, date AS Timestamp, open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume
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

        group['52w_high'] = group['Close'].rolling(window=TRADING_DAYS_PER_YEAR, min_periods=1).max()
        group['52w_low'] = group['Close'].rolling(window=TRADING_DAYS_PER_YEAR, min_periods=1).min()

        group['new_52w_high'] = np.where(group['Close'] == group['52w_high'], 1, 0)
        group['new_52w_low'] = np.where(group['Close'] == group['52w_low'], 1, 0)

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

        # Convergence signals
        group['convergence_5a'] = ((ema4 > ema9) & (ema9 > ema18) & (ema18 > ema50) & (ema50 > ema200)).astype(int)
        group['convergence_3'] = ((ema4 > ema9) & (ema9 > ema18) & (group['Close'] > sma100) & (group['Close'] > sma150) & (group['Close'] > group['SMA200'])).astype(int)
        group['convergence_4'] = ((ema5 > ema9) & (ema9 > ema21) & (ema21 > ema50)).astype(int)

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

        adx = compute_adx(group, 14)
        group["adx_trigger"] = (adx >= ADX_THRESHOLD).astype(int)

        group = group.drop(['NR5', 'NR6', 'NR7', 'NR8', 'min_5', 'min_6', 'min_7', 'min_8', 'avg_volume_30', 'Range'], axis=1)
        all_results_list.append(group)

    all_results = pd.concat(all_results_list)
    all_results.reset_index(inplace=True)

    sql_columns = [
        "Timestamp", "Open", "High", "Low", "Close", "Volume", "Symbol",
        "Hammer", "Shooting_Star", "Doji", "Engulfing", "Dark_Cloud_Cover",
        "Morning_Star", "Evening_Star", "Piercing_Line",
        "SMA4", "SMA9", "SMA18", "SMA50", "SMA200",
        "signal1", "signal2", "signal3", "signal4", "signal5",
        "PC", "STD", "top_decile",
        "52w_high", "52w_low", "new_52w_high", "new_52w_low",
        "NR", "High_Relative_Volume_30",
        "hit_2y_high_14d", "hit_5y_high_14d", "hit_10y_high_14d",
        "RSI14", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70",
        "adx_trigger", "convergence_5a", "convergence_3", "convergence_4",
        "RCS_30D"
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
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
