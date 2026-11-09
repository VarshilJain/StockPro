"""
create_saved_scans_table.py — One-time migration to create the saved_scans table.
Run once: python create_saved_scans_table.py
"""
import sys
print("Connecting to database...")
from database import get_connection

CREATE_SAVED_SCANS_SQL = """
CREATE TABLE IF NOT EXISTS saved_scans (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    name        VARCHAR(128) NOT NULL,
    conditions  JSON NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_saved_scans_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

try:
    conn = get_connection()
    cursor = conn.cursor()
    print("Creating `saved_scans` table (IF NOT EXISTS)...")
    cursor.execute(CREATE_SAVED_SCANS_SQL)
    conn.commit()
    cursor.close()
    conn.close()
    print("[OK] `saved_scans` table is ready.")
except Exception as exc:
    print(f"ERROR: {exc}")
    sys.exit(1)
