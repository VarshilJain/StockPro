"""
create_widget_tables.py — One-time migration: creates signal_registry and
user_dashboard_widgets tables, then seeds all signals from scan_engine.

Run with:
    python create_widget_tables.py
"""
import sys
from database import get_connection

# ── DDL ──────────────────────────────────────────────────────────────────────

CREATE_SIGNAL_REGISTRY = """
CREATE TABLE IF NOT EXISTS signal_registry (
    signal_id      INT          AUTO_INCREMENT PRIMARY KEY,
    display_name   VARCHAR(100) NOT NULL,
    description    TEXT,
    category       VARCHAR(50),
    scanner_signal VARCHAR(100) NOT NULL,
    is_active      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sr_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

CREATE_USER_DASHBOARD_WIDGETS = """
CREATE TABLE IF NOT EXISTS user_dashboard_widgets (
    id         INT  AUTO_INCREMENT PRIMARY KEY,
    user_id    INT  NOT NULL,
    signal_id  INT  NOT NULL,
    position   INT  NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)   REFERENCES users(id)              ON DELETE CASCADE,
    FOREIGN KEY (signal_id) REFERENCES signal_registry(signal_id) ON DELETE CASCADE,
    UNIQUE KEY  uq_user_signal    (user_id, signal_id),
    INDEX       idx_user_position (user_id, position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# ── Seed data: all signals that exist in scan_engine.ALLOWED_FIELDS ───────────
# Tuple: (display_name, description, category, scanner_signal)
SEED_SIGNALS = [
    # Candlestick Patterns
    ("Hammer",          "Bullish hammer candlestick pattern",                "Candlestick Patterns",  "Hammer"),
    ("Shooting Star",   "Bearish shooting star candlestick pattern",         "Candlestick Patterns",  "Shooting_Star"),
    ("Doji",            "Doji candlestick — indecision pattern",             "Candlestick Patterns",  "Doji"),
    ("Engulfing",       "Bullish or bearish engulfing candlestick pattern",  "Candlestick Patterns",  "Engulfing"),
    ("Dark Cloud Cover","Bearish dark cloud cover candlestick pattern",      "Candlestick Patterns",  "Dark_Cloud_Cover"),
    ("Morning Star",    "Bullish morning star three-candle pattern",         "Candlestick Patterns",  "Morning_Star"),
    ("Evening Star",    "Bearish evening star three-candle pattern",         "Candlestick Patterns",  "Evening_Star"),
    ("Piercing Line",   "Bullish piercing line candlestick pattern",         "Candlestick Patterns",  "Piercing_Line"),
    # RSI Signals
    ("RSI < 30",        "RSI falls below 30 - standard oversold zone",      "RSI Signals",           "rsi_lt_30"),
    ("RSI > 70",        "RSI rises above 70 - standard overbought zone",    "RSI Signals",           "rsi_gt_70"),
    ("Oversold",        "RSI below 25 - extreme oversold condition",        "RSI Signals",           "oversold"),
    ("Overbought",      "RSI above 80 - extreme overbought condition",      "RSI Signals",           "overbought"),
    # Price Extremes
    ("New 52-Week High","Stock hits a new 52-week high",                    "Price Extremes",        "new_52w_high"),
    ("New 52-Week Low", "Stock hits a new 52-week low",                     "Price Extremes",        "new_52w_low"),
    ("2-Year High",     "Stock hit a 2-year high in the last 14 days",      "Price Extremes",        "hit_2y_high_14d"),
    ("5-Year High",     "Stock hit a 5-year high in the last 14 days",      "Price Extremes",        "hit_5y_high_14d"),
    ("10-Year High",    "Stock hit a 10-year high in the last 14 days",     "Price Extremes",        "hit_10y_high_14d"),
    # Volume & ADX
    ("High Relative Volume", "Volume > 3x 30-day average (volume spike)",  "Volume & ADX",          "High_Relative_Volume_30"),
    ("ADX Trigger",     "ADX crosses above 25 - strong trend confirmed",    "Volume & ADX",          "adx_trigger"),
    # Composite Signals
    ("Signal 1",        "Close crosses above SMA9 - bullish crossover",     "Composite Signals",     "signal1"),
    ("Signal 2",        "Close crosses below SMA9 - bearish crossover",     "Composite Signals",     "signal2"),
    ("Signal 3",        "SMA9 crosses above SMA18 - bullish momentum",      "Composite Signals",     "signal3"),
    ("Signal 4",        "Close between SMA200 and SMA50 - transition zone", "Composite Signals",     "signal4"),
    ("Golden Cross",    "SMA50 crosses above SMA200 - long-term bullish",   "Composite Signals",     "signal5"),
    ("Top Decile",      "Stock ranks in top decile by composite score",     "Composite Signals",     "top_decile"),
    ("Convergence 5A",  "EMA 4/9/18/50/200 convergence - trend alignment",  "Composite Signals",     "convergence_5a"),
    ("Convergence 3",   "EMA 4/9/18 convergence + price above 100/150/200", "Composite Signals",    "convergence_3"),
    ("Convergence 4",   "EMA 5/9/21/50 convergence - medium-term trend",    "Composite Signals",     "convergence_4"),
    # Delivery Signals
    ("Delivery",         "5-day recent delivery volume & value exceeds 22-day baseline", "Delivery Signals", "delivery_momentum_signal"),
    # Narrow Range
    ("Narrow Range",    "NR5-NR8 narrow range day - volatility contraction","Narrow Range",          "NR"),
]

INSERT_SIGNAL = """
INSERT IGNORE INTO signal_registry
    (display_name, description, category, scanner_signal)
VALUES (%s, %s, %s, %s)
"""


def main():
    print("Connecting to database...")
    try:
        conn = get_connection()
    except Exception as exc:
        print(f"ERROR: Could not connect to database: {exc}")
        sys.exit(1)

    cursor = conn.cursor()
    try:
        print("Creating `signal_registry` table (IF NOT EXISTS)...")
        cursor.execute(CREATE_SIGNAL_REGISTRY)

        print("Creating `user_dashboard_widgets` table (IF NOT EXISTS)...")
        cursor.execute(CREATE_USER_DASHBOARD_WIDGETS)

        print("Seeding signal_registry...")
        inserted = 0
        for row in SEED_SIGNALS:
            cursor.execute(INSERT_SIGNAL, row)
            if cursor.rowcount:
                inserted += 1

        conn.commit()
        print(f"[OK] Tables ready. {inserted} new signal(s) seeded.")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
