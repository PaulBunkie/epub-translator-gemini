"""Test: fetch SofaScore /event/{id} for real matches — verify status, minute, score"""
import sqlite3, requests, json, time, random
from datetime import datetime, timezone

SOFASCORE_API_URL = "https://api.sofascore1.com/api/v1"
HEADERS = {
    "User-Agent": random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]),
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

conn = sqlite3.connect("football_matches.db")
conn.row_factory = sqlite3.Row

# Все статусы: scheduled + in_progress + finished — по 3 матча каждого
for target_status in ['scheduled', 'in_progress', 'finished']:
    rows = conn.execute("""
        SELECT sofascore_event_id, home_team, away_team, status, match_date, match_time
        FROM matches
        WHERE sofascore_event_id IS NOT NULL AND status = ?
        ORDER BY match_date DESC
        LIMIT 3
    """, (target_status,)).fetchall()

    print(f"\n{'#'*80}")
    print(f"# STATUS: {target_status} — found {len(rows)} match(es)")
    print(f"{'#'*80}")

    for row in rows:
        eid = row['sofascore_event_id']
        url = f"{SOFASCORE_API_URL}/event/{eid}"
        print(f"\n{'='*80}")
        print(f"Match: {row['home_team']} vs {row['away_team']}")
        print(f"DB status: {row['status']}, date: {row['match_date']} {row['match_time']}")
        print(f"SofaScore event_id: {eid}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            print(f"HTTP {resp.status_code}")
            if resp.status_code != 200:
                print(f"Body: {resp.text[:300]}")
                continue

            data = resp.json()
            event = data.get('event', {})

            # STATUS
            st = event.get('status', {})
            if isinstance(st, dict):
                print(f"STATUS: type={st.get('type')}, code={st.get('code')}, desc={st.get('description')}")
            else:
                print(f"STATUS (raw): {st}")

            # SCORE
            hs = event.get('homeScore', {})
            aw = event.get('awayScore', {})
            if isinstance(hs, dict) and isinstance(aw, dict):
                print(f"SCORE: home={hs.get('current')}/{hs.get('normaltime')} away={aw.get('current')}/{aw.get('normaltime')}")
            else:
                print(f"SCORE (raw): home={hs} away={aw}")

            # TIME / current minute
            t = event.get('time', {})
            print(f"TIME: {json.dumps(t, ensure_ascii=False) if t else 'empty'}")

            # startTimestamp
            sts = event.get('startTimestamp')
            if sts:
                print(f"startTimestamp: {sts} ({datetime.fromtimestamp(sts, tz=timezone.utc)})")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(1.5)

conn.close()
print("\nDone.")