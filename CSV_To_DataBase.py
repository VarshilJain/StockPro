import pandas as pd
import mysql.connector
import numpy as np

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',
    'database': 'stock_data'
}

csv_file = r"D:\HistoricData\candlestick_patterns.csv"
df = pd.read_csv(csv_file)

df = df.replace([np.nan, 'nan', 'NaN', 'NaT'], None)

df.rename(columns={
    "Shooting Star": "Shooting_Star",
    "Dark Cloud Cover": "Dark_Cloud_Cover",
    "Morning Star": "Morning_Star",
    "Evening Star": "Evening_Star",
    "Piercing Line": "Piercing_Line"
}, inplace=True)

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
    "RSI14", "oversold", "overbought", "rsi_lt_30", "rsi_gt_70","adx_trigger",
    "convergence_5a", "convergence_3", "convergence_4"
]
df = df[sql_columns]


placeholders = ",".join(["%s"] * len(sql_columns))
insert_query = f"INSERT INTO historical_data ({','.join(sql_columns)}) VALUES ({placeholders})"


conn = mysql.connector.connect(**db_config)
cursor = conn.cursor()


cursor.execute("DELETE FROM historical_data;")
conn.commit()
print(" Previous data deleted from historical_data")

batch_size = 5000
data = df.values.tolist()

for i in range(0, len(data), batch_size):
    batch = data[i:i+batch_size]
    cursor.executemany(insert_query, batch)
    conn.commit()
    print(f" Inserted {i+len(batch)} rows...")

cursor.close()
conn.close()
print(" Data inserted successfully into MySQL!")