"""
seed_strategies.py — Populates all 5 institutional & technical strategies into saved_scans table.
"""
import json
from database import get_connection

STRATEGIES = [
    {
        "name": "1. Institutional Delivery & Smart-Money Accumulation",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"field": "delivery_momentum_signal", "operator": "=="},
                {"field": "High_Relative_Volume_30", "operator": "=="}
            ]
        }
    },
    {
        "name": "2. Momentum & Multi-Year Breakout",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"field": "new_52w_high", "operator": "=="},
                {"field": "RCS_30D", "operator": ">", "value": 0},
                {"field": "adx_trigger", "operator": "=="}
            ]
        }
    },
    {
        "name": "3. Volatility Contraction (VCP) & Narrow Range",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"field": "NR", "operator": ">", "value": 6},
                {"field": "High_Relative_Volume_30", "operator": "=="}
            ]
        }
    },
    {
        "name": "4. High-Probability Reversal & Dip Buying",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"field": "oversold", "operator": "=="},
                {"field": "Hammer", "operator": "=="}
            ]
        }
    },
    {
        "name": "5. EMA Ribbon Convergence & Golden Cross",
        "conditions": {
            "logic": "AND",
            "conditions": [
                {"field": "convergence_5a", "operator": "=="},
                {"field": "adx_trigger", "operator": "=="}
            ]
        }
    }
]

def seed():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Get all users
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    
    total_added = 0
    for u in users:
        uid = u['id']
        for strat in STRATEGIES:
            name = strat['name']
            conds_json = json.dumps(strat['conditions'])
            
            # Check if scan already exists for this user
            cursor.execute("SELECT id FROM saved_scans WHERE user_id = %s AND name = %s", (uid, name))
            existing = cursor.fetchone()
            if not existing:
                cursor.execute(
                    "INSERT INTO saved_scans (user_id, name, conditions) VALUES (%s, %s, %s)",
                    (uid, name, conds_json)
                )
                total_added += 1
            else:
                cursor.execute(
                    "UPDATE saved_scans SET conditions = %s WHERE id = %s",
                    (conds_json, existing['id'])
                )
    
    conn.commit()
    cursor.close()
    conn.close()
    print(f"[OK] Successfully seeded/updated {total_added} strategy scans across {len(users)} user(s).")

if __name__ == "__main__":
    seed()
