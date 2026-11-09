"""
Migration: Add active_session_id column to users table for single active session tracking.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_connection

def migrate():
    conn = get_connection()
    c = conn.cursor(dictionary=True)
    try:
        c.execute("SHOW COLUMNS FROM users LIKE 'active_session_id'")
        if not c.fetchone():
            print("Adding `active_session_id` column to `users` table...")
            c.execute("ALTER TABLE users ADD COLUMN active_session_id VARCHAR(64) NULL DEFAULT NULL AFTER token_version")
            c.execute("ALTER TABLE users ADD INDEX idx_users_active_session (active_session_id)")
            conn.commit()
            print("Column `active_session_id` added successfully.")
        else:
            print("Column `active_session_id` already exists.")
    except Exception as exc:
        print(f"Migration error: {exc}")
        sys.exit(1)
    finally:
        c.close()
        conn.close()

if __name__ == "__main__":
    migrate()
