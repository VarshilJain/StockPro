"""
create_historical_data_table.py — One-time migration to create the historical_data table.
Run once: python create_historical_data_table.py
"""
import sys
from database import get_connection

CREATE_HISTORICAL_DATA_SQL = """
CREATE TABLE IF NOT EXISTS historical_data (
    Timestamp DATETIME NOT NULL,
    Symbol VARCHAR(30) NOT NULL,
    Open DECIMAL(14,4),
    High DECIMAL(14,4),
    Low DECIMAL(14,4),
    Close DECIMAL(14,4),
    Volume BIGINT,
    Hammer TINYINT DEFAULT 0,
    Shooting_Star TINYINT DEFAULT 0,
    Doji TINYINT DEFAULT 0,
    Engulfing TINYINT DEFAULT 0,
    Dark_Cloud_Cover TINYINT DEFAULT 0,
    Morning_Star TINYINT DEFAULT 0,
    Evening_Star TINYINT DEFAULT 0,
    Piercing_Line TINYINT DEFAULT 0,
    SMA4 DECIMAL(14,4),
    SMA9 DECIMAL(14,4),
    SMA18 DECIMAL(14,4),
    SMA50 DECIMAL(14,4),
    SMA200 DECIMAL(14,4),
    signal1 TINYINT DEFAULT 0,
    signal2 TINYINT DEFAULT 0,
    signal3 TINYINT DEFAULT 0,
    signal4 TINYINT DEFAULT 0,
    signal5 TINYINT DEFAULT 0,
    PC DOUBLE,
    STD DOUBLE,
    top_decile TINYINT DEFAULT 0,
    52w_high DECIMAL(14,4),
    52w_low DECIMAL(14,4),
    new_52w_high TINYINT DEFAULT 0,
    new_52w_low TINYINT DEFAULT 0,
    near_52w_high TINYINT DEFAULT 0,
    NR TINYINT DEFAULT 0,
    High_Relative_Volume_30 TINYINT DEFAULT 0,
    hit_2y_high_14d TINYINT DEFAULT 0,
    hit_5y_high_14d TINYINT DEFAULT 0,
    hit_10y_high_14d TINYINT DEFAULT 0,
    RSI14 DOUBLE,
    oversold TINYINT DEFAULT 0,
    overbought TINYINT DEFAULT 0,
    rsi_lt_30 TINYINT DEFAULT 0,
    rsi_gt_70 TINYINT DEFAULT 0,
    adx_trigger TINYINT DEFAULT 0,
    convergence_5 TINYINT DEFAULT 0,
    convergence_3 TINYINT DEFAULT 0,
    convergence_4 TINYINT DEFAULT 0,
    RCS_30D DOUBLE,
    all_time_high DECIMAL(14,4),
    is_at_ath TINYINT DEFAULT 0,
    days_since_ath INT,
    first_listed_date DATE,
    weeks_since_listing DOUBLE,
    rs_rank INT,
    base_active TINYINT DEFAULT 0,
    base_start_date DATE,
    base_length_days INT,
    base_high DECIMAL(14,4),
    base_low DECIMAL(14,4),
    base_depth_pct DOUBLE,
    pct_from_pivot DOUBLE,
    contraction_count INT,
    breakout_today TINYINT DEFAULT 0,
    last_breakout_level DECIMAL(14,4),
    screen_vcp TINYINT DEFAULT 0,
    screen_blue_sky TINYINT DEFAULT 0,
    screen_multi_year_breakout TINYINT DEFAULT 0,
    screen_ipo_base TINYINT DEFAULT 0,
    screen_high_relative_volume TINYINT DEFAULT 0,
    screen_high_delivery_volume TINYINT DEFAULT 0,
    stage DOUBLE,
    stage_bucket VARCHAR(50),
    delivery_quantity BIGINT DEFAULT NULL,
    delivery_momentum_signal TINYINT DEFAULT 0,
    Recent_DV DOUBLE DEFAULT NULL,
    Baseline_DV DOUBLE DEFAULT NULL,
    Recent_Deliv_Pct DOUBLE DEFAULT NULL,
    Baseline_Deliv_Pct DOUBLE DEFAULT NULL,
    rsi_divergence_type VARCHAR(10) DEFAULT NULL,
    rsi_divergence_direction VARCHAR(20) DEFAULT NULL,
    rsi_divergence_score DOUBLE DEFAULT NULL,
    PRIMARY KEY (Symbol, Timestamp),
    INDEX idx_hist_sym_time (Symbol, Timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
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
        print("Creating `historical_data` table (IF NOT EXISTS)...")
        cursor.execute(CREATE_HISTORICAL_DATA_SQL)
        conn.commit()
        print("[OK] `historical_data` table is ready.")
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
