from database import get_connection

def seed():
    conn = get_connection()
    cur = conn.cursor()

    # 1. Seed admin_settings
    default_settings = [
        ('maintenance_mode', 'false', 'boolean', 'maintenance', 'Disable user access during system updates', 'system'),
        ('allow_user_registration', 'true', 'boolean', 'feature_flags', 'Allow public self-registration', 'system'),
        ('enable_openrouter_ai', 'true', 'boolean', 'feature_flags', 'Enable AI stock commentary and insights', 'system'),
        ('enable_delivery_momentum', 'true', 'boolean', 'feature_flags', 'Enable NSE Bhavcopy Delivery Momentum Signal', 'system'),
        ('enable_advanced_scanners', 'true', 'boolean', 'feature_flags', 'Enable experimental pattern scanners', 'system'),
        ('global_announcement_enabled', 'false', 'boolean', 'announcements', 'Show announcement banner on all pages', 'system'),
        ('global_announcement_text', 'StockPro is operating at normal capacity with real-time analytics.', 'string', 'announcements', 'Content of the announcement banner', 'system'),
        ('global_announcement_type', 'info', 'string', 'announcements', 'Banner style: info, warning, success, danger', 'system'),
        ('jwt_session_duration_hours', '8', 'integer', 'security', 'Hours before user JWT session expires', 'system'),
        ('max_failed_login_attempts', '5', 'integer', 'security', 'Consecutive failed logins before IP lockout', 'system'),
        ('cache_ttl_seconds', '300', 'integer', 'performance', 'In-memory screen stage cache TTL in seconds', 'system'),
    ]

    for k, val, stype, cat, desc, by in default_settings:
        cur.execute('''
            INSERT INTO admin_settings (setting_key, setting_value, setting_type, category, description, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE description=VALUES(description)
        ''', (k, val, stype, cat, desc, by))

    # 2. Seed scanner_configs
    core_scanners = [
        ('vcp', 'VCP (Volatility Contraction Pattern)', 'Pattern Screens', 'Mark Minervini VCP contraction base detection with contraction count >= 2', 1, 62.4, 14.8, 11.2, -6.8, 2.45, 2.1, 4.8, 9.4, 18.2),
        ('blue_sky', 'Blue Sky Breakout (APB)', 'Pattern Screens', 'All-Time Peak Breakout for stocks within 5% of all-time high with RS >= 70', 1, 68.1, 19.4, 15.6, -7.2, 2.85, 3.4, 7.2, 13.8, 24.1),
        ('multi_year_breakout', 'Multi-Year Breakout', 'Pattern Screens', 'High relative strength stocks breaking out of bases longer than 500 trading days', 1, 65.7, 24.1, 18.3, -8.1, 2.90, 2.8, 6.4, 14.2, 28.6),
        ('ipo_base', 'IPO Base Breakout', 'Pattern Screens', 'Breakout from post-listing consolidation between 3 to 104 weeks', 1, 58.9, 16.2, 12.0, -9.4, 2.10, 1.9, 4.1, 8.7, 16.5),
        ('rsi_divergence', 'RSI Divergence', 'Technical Momentum', 'Bullish double and triple monotonic RSI divergences across 80-bar lookback', 1, 61.2, 12.4, 9.8, -5.5, 2.25, 1.8, 3.9, 7.8, 14.2),
        ('ema_convergence', 'EMA Convergence Ribbon', 'Trend Squeeze', '4, 9, 18, 50, 200 EMA compression squeeze indicating impending expansion', 1, 64.0, 13.7, 10.5, -6.1, 2.38, 2.4, 5.1, 9.8, 17.6),
        ('high_relative_volume', 'High Relative Volume (3x / 5x)', 'Volume Surges', 'Stocks with current session volume exceeding 30-day average volume by 3x or 5x', 1, 59.8, 11.2, 8.4, -6.9, 1.95, 2.0, 4.2, 7.5, 13.1),
        ('high_delivery_volume', 'High Delivery Volume (>80%)', 'Institutional Accumulation', 'Official NSE Bhavcopy delivery quantity surpassing 80% of total traded volume', 1, 67.3, 17.5, 13.9, -5.8, 2.70, 2.9, 6.1, 11.4, 21.0),
    ]

    for sid, name, cat, desc, en, wr, ar, mr, md, pf, r5, r10, r20, r40 in core_scanners:
        cur.execute('''
            INSERT INTO scanner_configs (id, name, category, description, is_enabled, win_rate, avg_return, median_return, max_drawdown, profit_factor, return_5d, return_10d, return_20d, return_40d)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), category=VALUES(category), description=VALUES(description)
        ''', (sid, name, cat, desc, en, wr, ar, mr, md, pf, r5, r10, r20, r40))

    # 3. Seed admin_jobs
    cur.execute('SELECT COUNT(*) FROM admin_jobs')
    if cur.fetchone()[0] == 0:
        jobs = [
            ('ingest.py (OHLCV Fetch)', 'completed', '2026-09-27 18:30:00', '2026-09-27 18:38:22', 502.0, 3162, None, 'scheduler'),
            ('test.py (Signal Engine)', 'completed', '2026-09-27 23:34:37', '2026-09-28 00:27:17', 3160.0, 8106430, None, 'admin:Varshil'),
            ('nse_delivery_fetcher.py', 'completed', '2026-09-27 19:05:00', '2026-09-27 19:08:15', 195.0, 3162, None, 'scheduler'),
        ]
        for name, status, s_at, f_at, dur, recs, err, trig in jobs:
            cur.execute('''
                INSERT INTO admin_jobs (job_name, status, started_at, finished_at, duration_sec, records_processed, error_message, triggered_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (name, status, s_at, f_at, dur, recs, err, trig))

    # 4. Seed initial audit log
    cur.execute('SELECT COUNT(*) FROM admin_audit_logs')
    if cur.fetchone()[0] == 0:
        cur.execute('''
            INSERT INTO admin_audit_logs (admin_id, admin_email, action, target_type, target_id, details, ip_address)
            VALUES (1, 'varshiljain.c@gmail.com', 'SYSTEM_INITIALIZE', 'system', 'admin_portal', '{"note": "StockPro Admin Portal initialized with RBAC and security audit trail"}', '127.0.0.1')
        ''')

    conn.commit()
    conn.close()
    print("Seeding complete!")

if __name__ == '__main__':
    seed()
