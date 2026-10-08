"""
migrate_users_schema.py — Add approval_status and expires_at to users table.
"""
from database import get_connection

def migrate():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("DESCRIBE users")
        cols = [c["Field"] for c in cur.fetchall()]
        
        if "approval_status" not in cols:
            print("Adding approval_status column...")
            cur.execute("ALTER TABLE users ADD COLUMN approval_status VARCHAR(32) NOT NULL DEFAULT 'approved'")
            cur.execute("ALTER TABLE users ADD INDEX idx_users_approval (approval_status)")
            
        if "expires_at" not in cols:
            print("Adding expires_at column...")
            cur.execute("ALTER TABLE users ADD COLUMN expires_at TIMESTAMP NULL DEFAULT NULL")
            cur.execute("ALTER TABLE users ADD INDEX idx_users_expires (expires_at)")
            
        # Update existing records with default 365-day validity from created_at
        cur.execute("""
            UPDATE users 
            SET approval_status = 'approved',
                expires_at = DATE_ADD(COALESCE(created_at, NOW()), INTERVAL 365 DAY)
            WHERE expires_at IS NULL
        """)
        conn.commit()
        print("Migration completed successfully.")
    except Exception as e:
        conn.rollback()
        print("Migration error:", e)
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    migrate()
