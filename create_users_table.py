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
    hashed_password  VARCHAR(255) NULL,
    name             VARCHAR(128) NOT NULL,
    role             VARCHAR(32)  NOT NULL DEFAULT 'user',
    google_id        VARCHAR(255) NULL,
    auth_provider    VARCHAR(32)  NOT NULL DEFAULT 'local',
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    is_active        TINYINT(1)   NOT NULL DEFAULT 1,
    INDEX idx_users_email (email),
    INDEX idx_users_role (role),
    INDEX idx_users_google_id (google_id)
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
        
        # Check if role column exists (for existing tables)
        cursor.execute("SHOW COLUMNS FROM users LIKE 'role'")
        if not cursor.fetchone():
            print("Adding `role` column to existing `users` table...")
            cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'user' AFTER name")
            cursor.execute("ALTER TABLE users ADD INDEX idx_users_role (role)")
            
        # Check if google_id column exists
        cursor.execute("SHOW COLUMNS FROM users LIKE 'google_id'")
        if not cursor.fetchone():
            print("Adding `google_id` column to existing `users` table...")
            cursor.execute("ALTER TABLE users ADD COLUMN google_id VARCHAR(255) NULL AFTER role")
            cursor.execute("ALTER TABLE users ADD INDEX idx_users_google_id (google_id)")

        # Check if auth_provider column exists
        cursor.execute("SHOW COLUMNS FROM users LIKE 'auth_provider'")
        if not cursor.fetchone():
            print("Adding `auth_provider` column to existing `users` table...")
            cursor.execute("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(32) NOT NULL DEFAULT 'local' AFTER google_id")

        # Ensure hashed_password allows NULL for OAuth users
        cursor.execute("ALTER TABLE users MODIFY hashed_password VARCHAR(255) NULL")

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
