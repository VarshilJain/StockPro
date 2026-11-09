"""
create_price_alerts_table.py — Creates the user_price_alerts table.

Run once: python create_price_alerts_table.py
Idempotent: uses CREATE TABLE IF NOT EXISTS.
"""
import logging
from database import get_connection

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def create_table():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_price_alerts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(50) NOT NULL,
                `condition` ENUM('above', 'below', 'crosses_above', 'crosses_below') NOT NULL,
                target_price DECIMAL(12,2) NOT NULL,
                note VARCHAR(200) DEFAULT NULL,
                is_triggered BOOLEAN DEFAULT FALSE,
                triggered_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_user_active (user_id, is_triggered),
                INDEX idx_symbol (symbol),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        log.info("✅ Table `user_price_alerts` created (or already exists).")
    except Exception as exc:
        log.error("Failed to create table: %s", exc)
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    create_table()
