from database import get_connection

def migrate():
    print("Connecting to DB...")
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SHOW COLUMNS FROM users")
    existing_cols = [row[0].lower() for row in cursor.fetchall()]
    print("Existing columns:", existing_cols)

    if 'role' not in existing_cols:
        print("Adding role column...")
        cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'user'")
        conn.commit()

    if 'google_id' not in existing_cols:
        print("Adding google_id column...")
        cursor.execute("ALTER TABLE users ADD COLUMN google_id VARCHAR(255) NULL")
        conn.commit()

    if 'auth_provider' not in existing_cols:
        print("Adding auth_provider column...")
        cursor.execute("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(32) NOT NULL DEFAULT 'local'")
        conn.commit()

    print("Modifying hashed_password to allow NULL...")
    cursor.execute("ALTER TABLE users MODIFY hashed_password VARCHAR(255) NULL")
    conn.commit()

    cursor.execute("DESCRIBE users")
    for row in cursor.fetchall():
        print("  ", row)

    cursor.close()
    conn.close()
    print("MIGRATION COMPLETED SUCCESSFULLY.")

if __name__ == "__main__":
    migrate()
