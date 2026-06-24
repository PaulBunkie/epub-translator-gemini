import sqlite3

for db in ['football.db', 'football_matches.db']:
    try:
        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"{db} tables: {tables}")
        conn.close()
    except Exception as e:
        print(f"{db} error: {e}")