import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

data = pd.read_csv(r"D:\HistoricData\practice.csv")
data['Timestamp'] = pd.to_datetime(data['Timestamp'])
data.set_index('Timestamp', inplace=True)

def compute_rsi(close_series: pd.Series, period: int = 14) -> pd.Series:
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def hammer(df):
    return (df['Close'] < df['Open']) & \
           ((df['Open'] - df['Low']) > 2 * (df['Close'] - df['Open']))

def shooting_star(df):
    return (df['Close'] > df['Open']) & \
           ((df['High'] - df['Close']) > 2 * (df['Close'] - df['Open']))

def doji(df):
    return abs(df['Close'] - df['Open']) <= (df['High'] - df['Low']) * 0.1

def engulfing(df):
    bullish = (df['Open'].shift(1) < df['Close'].shift(1)) & \
              (df['Open'] < df['Close']) & \
              (df['Open'] < df['Close'].shift(1)) & \
              (df['Close'] > df['Open'].shift(1))
    bearish = (df['Open'].shift(1) > df['Close'].shift(1)) & \
              (df['Open'] > df['Close']) & \
              (df['Open'] > df['Close'].shift(1)) & \
              (df['Close'] < df['Open'].shift(1))
    return bullish | bearish


def dark_cloud_cover(df):
    return (df['Close'].shift(1) > df['Open'].shift(1)) & \
           (df['Open'] > df['Close']) & \
           (df['Close'] < df['Open'].shift(1) - (df['Close'].shift(1) - df['Open'].shift(1)) * 0.5)

def morning_star(df):
    return (df['Close'].shift(2) < df['Open'].shift(2)) & \
           (df['Close'].shift(1) < df['Open'].shift(1)) & \
           (df['Open'] < df['Close']) & \
           (df['Close'] > df['Open'].shift(2))

def evening_star(df):
    return (df['Close'].shift(2) > df['Open'].shift(2)) & \
           (df['Close'].shift(1) > df['Open'].shift(1)) & \
           (df['Open'] > df['Close']) & \
           (df['Close'] < df['Open'].shift(2))

def piercing_line(df):
    return (df['Close'].shift(1) < df['Open'].shift(1)) & \
           (df['Close'] > df['Open']) & \
           (df['Open'] < df['Close'].shift(1)) & \
           (df['Close'] > df['Open'].shift(1) - (df['Close'].shift(1) - df['Open'].shift(1)) * 0.5)

# ✅ Narrow Range function
def get_nr(row):
    if row["NR8"] == 1: return 8
    elif row["NR7"] == 1: return 7
    elif row["NR6"] == 1: return 6
    elif row["NR5"] == 1: return 5
    else: return 0

all_results = pd.DataFrame()

for symbol, group in data.groupby('Symbol'):
    group['Hammer'] = hammer(group)
    group['Shooting Star'] = shooting_star(group)
    group['Doji'] = doji(group)
    group['Engulfing'] = engulfing(group)
    group['Dark Cloud Cover'] = dark_cloud_cover(group)
    group['Morning Star'] = morning_star(group)
    group['Evening Star'] = evening_star(group)
    group['Piercing Line'] = piercing_line(group)

    group['52w_high'] = group['Close'].rolling(window=252, min_periods=1).max()
    group['52w_low'] = group['Close'].rolling(window=252, min_periods=1).min()

    group['new_52w_high'] = np.where(group['Close'] == group['52w_high'], 1, 0)
    group['new_52w_low'] = np.where(group['Close'] == group['52w_low'], 1, 0)

    # === 2y / 5y / 10y breakout signals based on Close price ===
    # Flag ONLY the actual breakout day: Close crosses from <= prior N-year max Close to > prior N-year max Close
    trading_days_per_year = 252
    two_y_window = trading_days_per_year * 2
    five_y_window = trading_days_per_year * 5
    ten_y_window = trading_days_per_year * 10

    prior_2y_max_close = group['Close'].rolling(window=two_y_window, min_periods=two_y_window).max().shift(1)
    prior_5y_max_close = group['Close'].rolling(window=five_y_window, min_periods=five_y_window).max().shift(1)
    prior_10y_max_close = group['Close'].rolling(window=ten_y_window, min_periods=ten_y_window).max().shift(1)

    cross_2y = (group['Close'] > prior_2y_max_close) & (group['Close'].shift(1) <= prior_2y_max_close.shift(1))
    cross_5y = (group['Close'] > prior_5y_max_close) & (group['Close'].shift(1) <= prior_5y_max_close.shift(1))
    cross_10y = (group['Close'] > prior_10y_max_close) & (group['Close'].shift(1) <= prior_10y_max_close.shift(1))

    group['hit_2y_high_14d'] = cross_2y.fillna(False).astype(int)
    group['hit_5y_high_14d'] = cross_5y.fillna(False).astype(int)
    group['hit_10y_high_14d'] = cross_10y.fillna(False).astype(int)

    # store prior max close values for potential reporting (not exported here)
    group['prior_2y_max_close'] = prior_2y_max_close
    group['prior_5y_max_close'] = prior_5y_max_close
    group['prior_10y_max_close'] = prior_10y_max_close

    group['SMA4'] = group['Close'].rolling(window=4).mean()
    group['SMA9'] = group['Close'].rolling(window=9).mean()
    group['SMA18'] = group['Close'].rolling(window=18).mean()
    group['SMA50'] = group['Close'].rolling(window=50).mean()
    group['SMA200'] = group['Close'].rolling(window=200).mean()

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

    doji_condition = (group["Open"] / group["Close"] >= 0.995) & (group["Open"] / group["Close"] <= 1.005)
    group["Doji"] = np.where(doji_condition, 1, 0)

    group["PC"] = group["SMA50"].pct_change(fill_method=None)
    group["STD"] = group["PC"].rolling(window=252, min_periods=1).std() * 100

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



    all_time_high_volume = group["Volume"].max()



    group["RSI14"] = compute_rsi(group["Close"], 14)
    group["oversold"] = (group["RSI14"] < 25).astype(int)
    group["overbought"] = (group["RSI14"] > 80).astype(int)
    group["rsi_lt_30"] = (group["RSI14"] < 30).astype(int)
    group["rsi_gt_70"] = (group["RSI14"] > 70).astype(int)

    group = group.drop(['NR5', 'NR6', 'NR7', 'NR8', 'min_5', 'min_6', 'min_7', 'min_8', 'avg_volume_30','Range'], axis=1)

    all_results = pd.concat([all_results, group])

bool_columns = ['Hammer', 'Shooting Star', 'Doji', 'Engulfing', 
                'Dark Cloud Cover', 'Morning Star', 'Evening Star', 'Piercing Line']
all_results[bool_columns] = all_results[bool_columns].astype(int)

all_results.reset_index(inplace=True)



all_results.to_csv(r"D:\HistoricData\candlestick_patterns.csv", index=False)
print("Printed ✅")
