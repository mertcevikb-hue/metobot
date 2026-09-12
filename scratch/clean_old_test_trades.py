"""
Clean up stale test trade records from trade_logs in database/metobot.db.
Preserves watchlist_symbols, model_weights, attribution_logs, and user data.
"""
import sqlite3
import os

db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "database", "metobot.db"))
print(f"Connecting to {db_path}...")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Check before
cur.execute("SELECT count(*), sum(case when status='OPEN' then 1 else 0 end) FROM trade_logs")
total, open_cnt = cur.fetchone()
print(f"Before cleanup: {total} total trade logs, {open_cnt} open positions.")

# Clear trade_logs
cur.execute("DELETE FROM trade_logs")
conn.commit()

# Check after
cur.execute("SELECT count(*), sum(case when status='OPEN' then 1 else 0 end) FROM trade_logs")
total_after, open_cnt_after = cur.fetchone()
print(f"After cleanup: {total_after} total trade logs, {open_cnt_after or 0} open positions.")

conn.close()
print("Clean start complete: OPEN POSITIONS = 0")
