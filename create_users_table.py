"""
One-time migration script: creates the `users` table in the configured MySQL database.
Run with: python create_users_table.py
"""
import sys
from database import get_connection


CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id               INT          AUTO_INCREMENT PRIMARY KEY,
    email            VARCHAR(255) NOT NULL UNIQUE,
    hashed_password  VARCHAR(255) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    is_active        TINYINT(1)   NOT NULL DEFAULT 1,
    INDEX idx_users_email (email)
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
        print("Creating `users` table (IF NOT EXISTS)...")
        cursor.execute(CREATE_USERS_SQL)
        conn.commit()
        print("[OK] `users` table is ready.")
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
