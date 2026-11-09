"""
create_watchlist_table.py — Create user_watchlist table for storing user saved stocks.
"""
from database import get_connection

CREATE_USER_WATCHLIST = """
CREATE TABLE IF NOT EXISTS user_watchlist (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    symbol     VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY uq_user_watchlist_symbol (user_id, symbol),
    INDEX idx_user_watchlist (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

def main():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(CREATE_USER_WATCHLIST)
        conn.commit()
        print("Successfully created user_watchlist table!")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
