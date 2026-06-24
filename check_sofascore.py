import sqlite3
import json

conn = sqlite3.connect('football_matches.db')
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM matches WHERE sofascore_join IS NOT NULL AND sofascore_join != ''")
print('Matches with sofascore_join:', cursor.fetchone()[0])

cursor.execute("SELECT fixture_id, home_team, away_team, sofascore_join FROM matches WHERE sofascore_join IS NOT NULL AND sofascore_join != '' LIMIT 5")
for r in cursor.fetchall():
    print(r)

conn.close()