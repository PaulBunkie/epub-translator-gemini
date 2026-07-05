# SofaScore Match Details - API URLs and Data Flow

## Overview

Match details are fetched from the SofaScore API (`https://api.sofascore1.com/api/v1`) to provide:
- Match events (scheduled, live, finished)
- Live statistics at 60th minute
- Final scores
- Team information

## SofaScore API Base URL

```
https://api.sofascore1.com/api/v1
```

## API Endpoints Used

### 1. Scheduled Events (Match List by Date)

**URL**: `/sport/football/scheduled-events/{date}`

**Purpose**: Fetches all scheduled football events for a specific date

**Used in**: `football.py::_fetch_sofascore_events()`

**Parameters**:
- `date` (YYYY-MM-DD format)

**Example**:
```python
url = f"{SOFASCORE_API_URL}/sport/football/scheduled-events/{date}"
response = requests.get(url, headers=headers, timeout=15.0)
```

**Response**: List of events with:
- `id` - event ID
- `homeTeam` - home team object
- `awayTeam` - away team object
- `startTimestamp` - match start time (Unix timestamp)
- `status` - match status

---

### 2. Search Events (Find Match by Team Name)

**URL**: `/search/events?q={query}&page=0`

**Purpose**: Searches for events by team name when scheduled-events fails

**Used in**: `football.py::_search_sofascore_event_by_team()`

**Parameters**:
- `q` - URL-encoded team name
- `page` - page number (0-based)

**Example**:
```python
url = f"{SOFASCORE_API_URL}/search/events?q={requests.utils.quote(query_team)}&page=0"
response = requests.get(url, headers=headers, timeout=15.0)
```

**Response**: Search results with:
- `results` - array of search results
- `entity` - event object inside each result
- `homeTeam`, `awayTeam` - team objects
- `id` - event ID
- `slug` - event slug
- `startTimestamp` - start time

---

### 3. Event Statistics (60th Minute Stats)

**URL**: `/event/{event_id}/statistics`

**Purpose**: Gets detailed match statistics (possession, shots, xG, etc.)

**Used in**: `football.py::_fetch_sofascore_statistics()`

**Parameters**:
- `event_id` (int) - SofaScore event ID

**Example**:
```python
url = f"{SOFASCORE_API_URL}/event/{sofascore_event_id}/statistics"
response = requests.get(url, headers=headers, timeout=30.0)
```

**Response**: Statistics object with:
- `homeScore`, `awayScore` - current scores
- `statistics` - array of stat groups
- `periods` - statistics by period (first half, second half, all)
- Each stat group contains:
  - `groupName` - category name
  - `statisticsItems` - array of stats
    - `name` - stat name
    - `home` - home team value
    - `away` - away team value
    - `total` - total value

**Stored in**: `matches.stats_60min` column (JSON)

---

### 4. Event Details (Current Score & Status)

**URL**: `/event/{event_id}`

**Purpose**: Gets current match status and score

**Used in**: 
- `football.py::_fetch_sofascore_event()` - for live scores
- `football.py::_fetch_sofascore_event_status()` - for status only

**Parameters**:
- `event_id` (int) - SofaScore event ID

**Example**:
```python
url = f"{SOFASCORE_API_URL}/event/{sofascore_event_id}"
response = requests.get(url, headers=headers, timeout=30.0)
```

**Response**: Event object with:
- `event` - main event object
- `event.homeScore` - home score object
  - `current` - current score
  - `normaltime` - regular time score
  - `display` - display score
- `event.status` - match status
- `event.homeTeam` - home team object
- `event.awayTeam` - away team object

**Status values**: `finished`, `live`, `notstarted`, `postponed`

---

## Data Flow to Frontend

### 1. Data Collection Flow

```
┌──────────────────────────────────────────────────────────────────┐
│ STEP 1: Scheduled Events Collection                            │
│                                                                  │
│  football.py::update_sofascore_ids()                            │
│  ├── Calls _fetch_sofascore_events(date)                        │
│  │   └── GET /sport/football/scheduled-events/{date}            │
│  │       Returns: list of events with team IDs                 │
│  │                                                               │
│  ├── Matches events with DB matches by team name                │
│  │   └── _match_sofascore_event()                               │
│  │                                                               │
│  └── Saves sofascore_event_id to matches table                  │
│                                                                  │
│  Fallback: If scheduled-events fails:                           │
│  └── _search_sofascore_event_by_team()                          │
│      └── GET /search/events?q={team_name}                       │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ STEP 2: 60th Minute Statistics Collection (every 3 min)        │
│                                                                  │
│  football.py::check_matches_60min_and_status()                  │
│  ├── Queries matches where bet IS NULL                          │
│  │                                                               │
│  ├── _collect_60min_stats(match)                                │
│  │   ├── GET /event/{event_id}                                  │
│  │   │   Returns: current score (homeScore.current)             │
│  │   │                                                           │
│  │   ├── GET /event/{event_id}/statistics                       │
│  │   │   Returns: full stats (possession, shots, xG, etc.)      │
│  │   │                                                           │
│  │   └─ Saves to DB:                                            │
│  │       - stats_60min (JSON)                                   │
│  │       - live_odds (from The Odds API)                        │
│  │       - bet (AI decision: live odds or 0)                    │
│  │       - bet_ai (AI prediction: 1, X, 2, etc.)               │
│  │       - bet_ai_odds                                         │
│  │       - bet_alt_code, bet_alt_odds, bet_alt_confirm         │
│  │                                                               │
│  └── Triggers Telegram notification if conditions met          │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ STEP 3: Final Score Collection (every 5 min)                    │
│                                                                  │
│  football.py::check_matches_and_collect()                       │
│  ├── Queries matches with status = 'in_progress'                │
│  │                                                               │
│  ├── For matches past 100 minutes:                              │
│  │   └── _fetch_sofascore_event_status(event_id)                │
│  │       └── GET /event/{event_id}                              │
│  │           Returns: status ('finished' or other)              │
│  │                                                               │
│  └── If status == 'finished':                                   │
│      └── _collect_final_result(match)                           │
│          └── GET /event/{event_id}                              │
│              Returns: final score (homeScore.normaltime)        │
│              Saves to DB:                                       │
│              - final_score_home                                 │
│              - final_score_away                                 │
│              - fav_won (1/0)                                   │
│              - status = 'finished'                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Endpoint Details

### Request Headers (Required for SofaScore)

```python
SOFASCORE_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}

# Random User-Agent rotation (to avoid bans)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15...",
    # ... more agents
]
```

### Retry Logic

All SofaScore API calls implement:
- **Max retries**: 3-5 attempts
- **Backoff**: Exponential backoff (2^attempt seconds)
- **Random delay**: 2-5 seconds between requests
- **Rate limit handling**: 5-10 second delays on 403 Forbidden

---

## Database Schema (Matches Table)

### Key Columns for Match Details

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    fixture_id TEXT,              -- The Odds API event ID
    sofascore_event_id INTEGER,   -- SofaScore event ID
    home_team TEXT,
    away_team TEXT,
    home_team_sofascore_id INTEGER,  -- SofaScore team ID
    away_team_sofascore_id INTEGER,
    match_date TEXT,
    match_time TEXT,
    status TEXT,                  -- scheduled, in_progress, finished
    stats_60min TEXT,             -- JSON statistics from /statistics
    final_score_home INTEGER,     -- From /event/{id}
    final_score_away INTEGER,
    live_odds_1 REAL,             -- Current odds (1/X/2)
    live_odds_x REAL,
    live_odds_2 REAL,
    bet INTEGER,                  -- AI decision (live odds or 0)
    bet_ai TEXT,                  -- AI prediction (1, X, 2, 1X, X2)
    bet_ai_odds REAL,
    bet_alt_code TEXT,            -- Alternative bet code
    bet_alt_odds REAL,
    bet_alt_confirm INTEGER
);
```

---

## Match Details Storage Flow

### 1. Initial Match Addition (sync_matches)
**Source**: The Odds API (`/sports/{league}/odds`)
**Saves**:
- `fixture_id` (from Odds API)
- `home_team`, `away_team`
- `initial_odds`, `last_odds`
- `status = 'scheduled'`

### 2. SofaScore ID Update (update_sofascore_ids)
**Source**: SofaScore scheduled-events API
**Saves**:
- `sofascore_event_id`
- `home_team_sofascore_id`
- `away_team_sofascore_id`
- `sofascore_join` (JSON with slug, startTimestamp)

### 3. 60th Minute Processing (check_matches_60min)
**Sources**: 
- SofaScore `/event/{id}` - current score
- SofaScore `/event/{id}/statistics` - stats
- The Odds API `/sports/{sport}/events/{id}/odds` - live odds

**Saves**:
- `stats_60min` (JSON with full statistics)
- `final_score_home` (current score)
- `live_odds` (favorite odds)
- `live_odds_1`, `live_odds_x`, `live_odds_2`
- `bet` (live odds if AI says YES, else 0)
- `bet_ai` (AI prediction)
- `bet_ai_odds`
- `bet_alt_code`, `bet_alt_odds`, `bet_alt_confirm`

### 4. Final Result (check_matches_and_collect)
**Source**: SofaScore `/event/{id}`
**Saves**:
- `final_score_home` (final score)
- `final_score_away`
- `fav_won` (1 if favorite won, 0 otherwise)
- `status = 'finished'`

---

## Example: Full Match Lifecycle

```python
# 1. Initial sync (The Odds API)
match = {
    'fixture_id': 'abc123',
    'home_team': 'Real Madrid',
    'away_team': 'Barcelona',
    'initial_odds': 1.85,
    'last_odds': 1.90,
    'status': 'scheduled'
}

# 2. Update SofaScore IDs
match['sofascore_event_id'] = 123456
match['home_team_sofascore_id'] = 789
match['away_team_sofascore_id'] = 101112

# 3. At 60th minute (SofaScore API)
match['stats_60min'] = {
    "score": {"home": 1, "away": 1},
    "raw_data": { /* full SofaScore response */ },
    "parsed_period_all": { /* parsed stats */ }
}
match['live_odds'] = 2.10
match['bet'] = 2.10  # AI said YES
match['bet_ai'] = 'X2'
match['bet_ai_odds'] = 1.95

# 4. Final result (SofaScore API)
match['final_score_home'] = 2
match['final_score_away'] = 1
match['fav_won'] = 1  # Favorite won
match['status'] = 'finished'
```

---

## Frontend Display

### Match Card in bet.html

```html
<!-- Team logos from /api/team-logo/{sofascore_team_id} -->
<img src="/api/team-logo/${match.home_team_sofascore_id}" class="team-logo">
${match.home_team} vs ${match.away_team}
<img src="/api/team-logo/${match.away_team_sofascore_id}" class="team-logo">

<!-- Match details from DB -->
<div>Date: ${match.match_date} ${match.match_time}</div>
<div>Favorite: ${match.fav}</div>
<div>Odds: ${match.live_odds}</div>
<div>AI Prediction: ${match.bet_ai} @ ${match.bet_ai_odds}</div>
<div>Score: ${match.final_score_home}-${match.final_score_away}</div>
```

---

## Summary: URLs We "Pull" from SofaScore

1. **`/sport/football/scheduled-events/{date}`** - Get all matches for a date
2. **`/search/events?q={team_name}`** - Fallback search by team name
3. **`/event/{event_id}`** - Get event details, score, and status (used 2x: at 60 min and at finish)
4. **`/event/{event_id}/statistics`** - Get detailed statistics at 60th minute

All these URLs are called from `football.py` methods, stored in SQLite, and displayed via Flask templates.