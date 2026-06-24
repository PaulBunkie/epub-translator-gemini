#!/usr/bin/env python3
"""
Smart sync: update home_team_sofascore_id / away_team_sofascore_id
in football_matches.db using team_registry.db.

Algorithm:
1. Load registry: name → sofascore_team_id (with accent normalization)
2. For each match with missing IDs:
   a. Exact match (normalized)
   b. Partial match (one contains the other)
   c. Fallback: query SofaScore API via event_id (rare, only if event_id exists)
"""
import sqlite3
import unicodedata
import time
import json
import urllib.request

MATCHES_DB = 'football_matches.db'
REGISTRY_DB = 'team_registry.db'
API_BASE = 'https://api.sofascore1.com/api/v1'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Referer': 'https://www.sofascore.com/',
    'Origin': 'https://www.sofascore.com',
}


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def normalize(s):
    return strip_accents(s.lower().strip())


def api_get(path):
    url = f'{API_BASE}{path}'
    req = urllib.request.Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=15)
            return json.loads(r.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
    return None


def load_registry():
    """Load registry: {normalized_name: (team_id, original_name)}"""
    reg = sqlite3.connect(REGISTRY_DB)
    reg.row_factory = sqlite3.Row
    registry = {}
    for row in reg.execute('SELECT sofascore_team_id, name FROM teams'):
        name = row['name']
        team_id = row['sofascore_team_id']
        registry[normalize(name)] = (team_id, name)
    reg.close()
    return registry


def find_team_id(team_name, registry):
    """Find team ID in registry using accent-normalized matching."""
    name_norm = normalize(team_name)

    # 1. Exact match
    if name_norm in registry:
        return registry[name_norm][0]

    # 2. Partial match: registry name is part of team_name or vice versa
    best_match = None
    best_len = 0
    for reg_norm, (team_id, reg_name) in registry.items():
        if len(reg_norm) < 3 or len(name_norm) < 3:
            continue
        if reg_norm in name_norm or name_norm in reg_norm:
            # Prefer longer match
            match_len = min(len(reg_norm), len(name_norm))
            if match_len > best_len:
                best_len = match_len
                best_match = team_id
    if best_match:
        return best_match

    return None


def fetch_team_ids_from_event(event_id):
    """Fallback: get team IDs from SofaScore event data."""
    data = api_get(f'/event/{event_id}')
    if not data or 'event' not in data:
        return None, None
    event = data['event']
    home_id = event.get('homeTeam', {}).get('id')
    away_id = event.get('awayTeam', {}).get('id')
    return home_id, away_id


def main():
    reg = sqlite3.connect(REGISTRY_DB)
    reg.row_factory = sqlite3.Row
    registry = {}
    for row in reg.execute('SELECT sofascore_team_id, name FROM teams'):
        registry[normalize(row['name'])] = (row['sofascore_team_id'], row['name'])
    reg.close()
    print(f"Loaded {len(registry)} teams from registry")

    conn = sqlite3.connect(MATCHES_DB)
    conn.row_factory = sqlite3.Row

    # Find matches needing IDs
    rows = conn.execute("""
        SELECT id, home_team, away_team, sofascore_event_id,
               home_team_sofascore_id, away_team_sofascore_id
        FROM matches
        WHERE home_team_sofascore_id IS NULL OR away_team_sofascore_id IS NULL
    """).fetchall()
    print(f"Matches needing IDs: {len(rows)}")

    updated = 0
    api_calls = 0

    for row in rows:
        match_id = row['id']
        home = row['home_team']
        away = row['away_team']
        event_id = row['sofascore_event_id']

        home_id = find_team_id(home, registry)
        away_id = find_team_id(away, registry)

        # Fallback to API if event_id exists and we're missing a team ID
        if event_id and (not home_id or not away_id):
            time.sleep(0.3)
            api_home, api_away = fetch_team_ids_from_event(event_id)
            api_calls += 1
            if api_home and not home_id:
                home_id = api_home
            if api_away and not away_id:
                away_id = api_away

        if home_id or away_id:
            conn.execute("""
                UPDATE matches
                SET home_team_sofascore_id = COALESCE(?, home_team_sofascore_id),
                    away_team_sofascore_id = COALESCE(?, away_team_sofascore_id)
                WHERE id = ?
            """, (home_id, away_id, match_id))
            updated += 1

        if updated % 500 == 0 and updated > 0:
            conn.commit()
            print(f"  Progress: {updated} updated...")

    conn.commit()
    conn.close()

    print(f"\n=== DONE ===")
    print(f"Updated: {updated}")
    print(f"API fallback calls: {api_calls}")


if __name__ == '__main__':
    main()