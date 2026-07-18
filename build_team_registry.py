#!/usr/bin/env python3
"""
Build / update team_registry.db from:
1. static/team_logos_archive/ (PNG logos + slugs + league folders)
2. SofaScore search API (real team IDs by name)

NEW BEHAVIOUR:
- INSERT OR REPLACE (never deletes existing teams)
- Improved name matching (fuzzy, accent-stripped)
- Outputs two lists: logos that failed to match, and teams in matches DB
  that still have no logo entry after the update.
"""
import sqlite3
import os
import json
import re
import time
import urllib.request
import urllib.parse
import unicodedata

from config import TEAM_REGISTRY_DB_FILE

ARCHIVE_DIR = 'static/team_logos_archive'

REGISTRY_DB = str(TEAM_REGISTRY_DB_FILE)
MATCHES_DB = 'football_matches.db'
API_BASE = 'https://api.sofascore1.com/api/v1'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://www.sofascore.com/',
    'Origin': 'https://www.sofascore.com',
}

# ---------------------------------------------------------------
# Manual slug → probable SofaScore team name mapping
# ---------------------------------------------------------------
SLUG_TO_NAME = {
    'bayern-muenchen': 'Bayern Munich',
    'bvb': 'Borussia Dortmund',
    'koeln': '1. FC Köln',
    'mainz-05': '1. FSV Mainz 05',
    'st-pauli': 'FC St. Pauli',
    'rb-leipzig': 'RB Leipzig',
    'werder-bremen': 'SV Werder Bremen',
    'inter-miami': 'Inter Miami',
    'inter': 'Inter',
    'milan': 'AC Milan',
    'bod-glimt': 'FK Bodø/Glimt',
    'lillestr-m': 'Lillestrøm',
    'kfum-oslo': 'KFUM Oslo',
    'sarpsborg-08': 'Sarpsborg 08',
    'viking-stavanger': 'Viking FK',
    'valerenga': 'Vålerenga',
    'hamarkameratene': 'HamKam',
    'troms': 'Tromsø',
    'aalesund': 'Aalesund',
    'fredrikstad': 'Fredrikstad FK',
    'start': 'IK Start',
    'blau-wei-linz': 'FC Blau-Weiß Linz',
    'tirol': 'WSG Tirol',
    'red-bull-salzburg': 'Red Bull Salzburg',
    'rapid-wien': 'SK Rapid Wien',
    'ried': 'SV Ried',
    'rheindorf-altach': 'SV Ried',
    'lask': 'LASK',
    'hartberg': 'TSV Hartberg',
    'grazer-ak': 'Grazer AK',
    'wolfsberger': 'Wolfsberger AC',
    'austria-wien': 'FK Austria Wien',
    'st-gallen': 'FC St. Gallen',
    'thun': 'FC Thun',
    'winterthur': 'FC Winterthur',
    'young-boys': 'BSC Young Boys',
    'zurich': 'FC Zürich',
    'lugano': 'FC Lugano',
    'luzern': 'FC Luzern',
    'sion': 'FC Sion',
    'grasshopper': 'Grasshopper Zürich',
    'lausanne-sport': 'FC Lausanne-Sport',
    'basel': 'FC Basel',
    'servette': 'Servette FC',
    'athletico-paranaense': 'Athletico Paranaense',
    'atletico-mineiro': 'Atlético Mineiro',
    'corinthians': 'Corinthians',
    'coritiba': 'Coritiba',
    'flamengo': 'Flamengo',
    'fluminense': 'Fluminense',
    'gremio': 'Grêmio',
    'inter-de-porto-alegre': 'Internacional',
    'palmeiras': 'Palmeiras',
    'santos': 'Santos',
    'sao-paulo': 'São Paulo',
    'vitoria': 'Vitória',
    'botafogo': 'Botafogo',
    'bahia': 'Bahia',
    'cruzeiro': 'Cruzeiro',
    'chapecoense': 'Chapecoense',
    'mirassol': 'Mirassol',
    'red-bull-bragantino': 'Red Bull Bragantino',
    'remo': 'Remo',
    'vasco-da-gama': 'Vasco da Gama',
    'bayer-leverkusen': 'Bayer 04 Leverkusen',
    'borussia-dortmund': 'Borussia Dortmund',
    'borussia-moenchengladbach': 'Borussia Mönchengladbach',
    'eintracht-frankfurt': 'Eintracht Frankfurt',
    'freiburg': 'SC Freiburg',
    'heidenheim': '1. FC Heidenheim',
    'hoffenheim': 'TSG Hoffenheim',
    'stuttgart': 'VfB Stuttgart',
    'union-berlin': '1. FC Union Berlin',
    'wolfsburg': 'VfL Wolfsburg',
    'augsburg': 'FC Augsburg',
    'hamburger-sv': 'Hamburger SV',
    'ajax': 'Ajax',
    'az-alkmaar': 'AZ Alkmaar',
    'feyenoord-rotterdam': 'Feyenoord',
    'psv-eindhoven': 'PSV Eindhoven',
    'twente': 'FC Twente',
    'utrecht': 'FC Utrecht',
    'anderlecht': 'RSC Anderlecht',
    'cercle-brugge': 'Cercle Brugge',
    'charleroi': 'Charleroi',
    'club-brugge': 'Club Brugge',
    'dender': 'Dender',
    'genk': 'KRC Genk',
    'gent': 'KAA Gent',
    'la-louviere': 'La Louvière',
    'mechelen': 'KV Mechelen',
    'oh-leuven': 'OH Leuven',
    'royal-antwerp': 'Royal Antwerp',
    'sint-truiden': 'Sint-Truiden',
    'standard-de-liege': 'Standard Liège',
    'union-saint-gilloise': 'Union Saint-Gilloise',
    'westerlo': 'Westerlo',
    'zulte-waregem': 'Zulte-Waregem',
    'athletic-club': 'Athletic Club',
    'atletico-de-madrid': 'Atlético Madrid',
    'barcelona': 'FC Barcelona',
    'celta-de-vigo': 'Celta Vigo',
    'deportivo-alaves': 'Deportivo Alavés',
    'deportivo-de-la-coruna': 'Deportivo de La Coruña',
    'elche': 'Elche',
    'espanyol': 'Espanyol',
    'getafe': 'Getafe',
    'levante': 'Levante',
    'malaga': 'Málaga',
    'osasuna': 'Osasuna',
    'racing-de-santander': 'Racing de Santander',
    'rayo-vallecano': 'Rayo Vallecano',
    'real-betis': 'Real Betis',
    'real-madrid': 'Real Madrid',
    'real-sociedad': 'Real Sociedad',
    'sevilla': 'Sevilla',
    'valencia': 'Valencia',
    'villarreal': 'Villarreal',
    'benfica': 'SL Benfica',
    'braga': 'SC Braga',
    'casa-pia': 'Casa Pia',
    'estoril-praia': 'Estoril Praia',
    'estrela-da-amadora': 'Estrela Amadora',
    'famalicao': 'Famalicão',
    'gil-vicente': 'Gil Vicente',
    'moreirense': 'Moreirense',
    'porto': 'FC Porto',
    'rio-ave': 'Rio Ave',
    'santa-clara': 'Santa Clara',
    'sporting-de-lisboa': 'Sporting CP',
    'tondela': 'Tondela',
    'vitoria-de-guimaraes': 'Vitória de Guimarães',
    'angers': 'Angers',
    'auxerre': 'Auxerre',
    'brest': 'Brest',
    'le-havre': 'Le Havre',
    'lens': 'Lens',
    'lille': 'Lille',
    'lorient': 'Lorient',
    'metz': 'Metz',
    'monaco': 'AS Monaco',
    'nantes': 'Nantes',
    'nice': 'Nice',
    'olympique-de-marseille': 'Olympique de Marseille',
    'olympique-lyonnais': 'Olympique Lyonnais',
    'paris-fc': 'Paris FC',
    'paris-saint-germain': 'Paris Saint-Germain',
    'stade-rennais': 'Stade Rennais',
    'strasbourg': 'Strasbourg',
    'toulouse': 'Toulouse',
    'amiens': 'Amiens',
    'annecy': 'Annecy',
    'bastia': 'Bastia',
    'boulogne': 'Boulogne',
    'clermont-foot': 'Clermont Foot',
    'dunkerque': 'Dunkerque',
    'grenoble': 'Grenoble',
    'guingamp': 'Guingamp',
    'le-mans': 'Le Mans',
    'montpellier': 'Montpellier',
    'nancy': 'Nancy',
    'pau': 'Pau',
    'red-star': 'Red Star',
    'rodez-aveyron': 'Rodez Aveyron',
    'saint-etienne': 'Saint-Étienne',
    'stade-de-reims': 'Stade de Reims',
    'stade-lavallois': 'Stade Lavallois',
    'troyes': 'Troyes',
    'atlanta-united': 'Atlanta United',
    'austin': 'Austin FC',
    'charlotte': 'Charlotte FC',
    'chicago-fire': 'Chicago Fire',
    'cincinnati': 'FC Cincinnati',
    'colorado-rapids': 'Colorado Rapids',
    'columbus-crew': 'Columbus Crew',
    'd-c-united': 'D.C. United',
    'dallas-fc': 'FC Dallas',
    'houston-dynamo': 'Houston Dynamo',
    'los-angeles-fc': 'Los Angeles FC',
    'los-angeles-galaxy': 'LA Galaxy',
    'minnesota-united': 'Minnesota United',
    'montreal': 'CF Montréal',
    'nashville': 'Nashville SC',
    'new-england-revolution': 'New England Revolution',
    'new-york-city': 'New York City FC',
    'new-york-red-bulls': 'New York Red Bulls',
    'orlando-city': 'Orlando City',
    'philadelphia-union': 'Philadelphia Union',
    'portland-timbers': 'Portland Timbers',
    'real-salt-lake': 'Real Salt Lake',
    'san-diego-fc': 'San Diego FC',
    'san-jose-earthquakes': 'San Jose Earthquakes',
    'seattle-sounders': 'Seattle Sounders',
    'sporting-kansas-city': 'Sporting Kansas City',
    'st-louis-city': 'St. Louis City',
    'toronto-fc': 'Toronto FC',
    'vancouver-whitecaps': 'Vancouver Whitecaps',
    'arsenal': 'Arsenal',
    'aston-villa': 'Aston Villa',
    'bournemouth': 'AFC Bournemouth',
    'brentford': 'Brentford',
    'brighton-and-hove-albion': 'Brighton',
    'chelsea': 'Chelsea',
    'coventry-city': 'Coventry City',
    'crystal-palace': 'Crystal Palace',
    'everton': 'Everton',
    'fulham': 'Fulham',
    'hull-city': 'Hull City',
    'ipswich-town': 'Ipswich Town',
    'leeds-united': 'Leeds United',
    'liverpool': 'Liverpool',
    'manchester-city': 'Manchester City',
    'manchester-united': 'Manchester United',
    'newcastle-united': 'Newcastle United',
    'nottingham-forest': 'Nottingham Forest',
    'sunderland': 'Sunderland',
    'tottenham-hotspur': 'Tottenham Hotspur',
    'atalanta': 'Atalanta',
    'bologna': 'Bologna',
    'cagliari': 'Cagliari',
    'como': 'Como',
    'fiorentina': 'Fiorentina',
    'frosinone': 'Frosinone',
    'genoa': 'Genoa',
    'juventus': 'Juventus',
    'lazio': 'Lazio',
    'lecce': 'Lecce',
    'monza': 'AC Monza',
    'napoli': 'Napoli',
    'parma': 'Parma',
    'roma': 'AS Roma',
    'sassuolo': 'Sassuolo',
    'torino': 'Torino',
    'udinese': 'Udinese',
    'venezia': 'Venezia',
    'besiktas': 'Beşiktaş',
    'fenerbahce': 'Fenerbahçe',
    'galatasaray': 'Galatasaray',
    'trabzonspor': 'Trabzonspor',
    'alanyaspor': 'Alanyaspor',
    'amedspor': 'Amedspor',
    'caykur-rizespor': 'Çaykur Rizespor',
    'corum': 'Çorum FK',
    'erzurumspor': 'BB Erzurumspor',
    'eyupspor': 'Eyüpspor',
    'gaziantep': 'Gaziantep FK',
    'genclerbirligi': 'Gençlerbirliği',
    'goztepe': 'Göztepe',
    'istanbul-basaksehir': 'İstanbul Başakşehir',
    'kas-mpasa': 'Kasımpaşa',
    'kocaelispor': 'Kocaelispor',
    'konyaspor': 'Konyaspor',
    'samsunspor': 'Samsunspor',
    'atlas-guadalajara': 'Atlas',
    'pumas-de-la-unam': 'Pumas UNAM',
    'tigres-de-la-uanl': 'Tigres UANL',
    'central-cordoba-santiago-del-estero': 'Central Cordoba SDE',
    'sarmiento-de-junin': 'Sarmiento de Junin',
    # ── new failed logos ──
    'br-ndby': 'Brondby IF',
    'k-benhavn': 'FC Copenhagen',
    'nordsj-lland': 'FC Nordsjaelland',
    's-nderjyske': 'Sonderjyske',
    'brei-ablik': 'Breidablik',
    'or-akureyri': 'Thor Akureyri',
    'racing-union-letzebuerg': 'Racing Union Luxembourg',
    'botafogo-de-ribeirao-preto': 'Botafogo SP',
    'fortaleza-esporte-clube': 'Fortaleza',
    'pac-omonia-29m': 'Omonia 29th May',
    'ertis-pavlodar': 'Irtysh Pavlodar',
    'politehnica-utm': 'Politehnica UTM',
    'kf-bashkimi-1947': 'KF Bashkimi 1947',
    'havnar-boltfelag': 'HB Torshavn',
    'vikingur-g-ta': 'Vikingur Gota',
    'mladost-donja-gorica': 'Mladost DG',
    'araz-naxc-van': 'Araz Naxcivan',
    'imisli-pfk': 'Imisli',
    'jagiellonia-bia-ystok': 'Jagiellonia Bialystok',
    'epitsentr-kamianets-podilskyi': 'Epitsentr Kamianets-Podilskyi',
    'tps-jalkapallo': 'TPS',
    'amrun-spartans': 'Hamrun Spartans',
    # ── more slug fixes ──
    'laci': 'KF Laci',
    'noah': 'FC Noah',
    'van': 'FC Van',
    'kapaz': 'Kapaz PFK',
    'qabala': 'Qabala FK',
    's-fa': 'Sabail FK',
    'imt': 'IMT Novi Beograd',
    'wis-a-krakow': 'Wisla Krakow',
    'wis-a-p-ock': 'Wisla Plock',
    'oulu': 'AC Oulu',
}

FIFA_SLUG_TO_NAME = {
    'algeria': 'Algeria', 'argentina': 'Argentina', 'australia': 'Australia',
    'austria': 'Austria', 'belgium': 'Belgium', 'brazil': 'Brazil',
    'bosnia-and-herzegovina': 'Bosnia & Herzegovina', 'canada': 'Canada',
    'cape-verde': 'Cabo Verde', 'colombia': 'Colombia', 'croatia': 'Croatia',
    'curacao': 'Curaçao', 'czechia': 'Czech Republic',
    'democratic-republic-of-the-congo': 'DR Congo',
    'ecuador': 'Ecuador', 'egypt': 'Egypt', 'england': 'England',
    'france': 'France', 'germany': 'Germany', 'ghana': 'Ghana',
    'haiti': 'Haiti', 'iran': 'Iran', 'iraq': 'Iraq',
    'ivory-coast': 'Ivory Coast', 'japan': 'Japan', 'jordan': 'Jordan',
    'mexico': 'Mexico', 'morocco': 'Morocco', 'netherlands': 'Netherlands',
    'new-zealand': 'New Zealand', 'norway': 'Norway', 'panama': 'Panama',
    'paraguay': 'Paraguay', 'portugal': 'Portugal', 'qatar': 'Qatar',
    'saudi-arabia': 'Saudi Arabia', 'scotland': 'Scotland', 'senegal': 'Senegal',
    'south-africa': 'South Africa', 'south-korea': 'South Korea', 'spain': 'Spain',
    'sweden': 'Sweden', 'switzerland': 'Switzerland', 'tunisia': 'Tunisia',
    'turkey': 'Turkey', 'united-states': 'USA', 'uruguay': 'Uruguay',
    'uzbekistan': 'Uzbekistan',
}


# =============================================================================
#  Name normalisation helpers  (same logic as api_sync_team_ids in app.py)
# =============================================================================
def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def _norm(s):
    return _strip_accents(s.lower().strip())


def slug_to_name(slug, league):
    if 'fifa-world-cup' in league:
        return FIFA_SLUG_TO_NAME.get(slug, slug.replace('-', ' ').title())
    return SLUG_TO_NAME.get(slug, slug.replace('-', ' ').title())


# =============================================================================
#  SofaScore API helpers
# =============================================================================
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
            print(f'  API attempt {attempt+1} failed: {e}')
            if attempt < 2:
                time.sleep(2)
    return None


def _duckduckgo_search_team(name, league=None):
    """Fallback: search DuckDuckGo HTML for SofaScore team ID.
    Returns (team_id, display_name) or (None, None)."""
    # Build search query
    query_parts = ['football', name]
    if league:
        # Extract meaningful league name from folder name
        league_clean = league.replace('-logos', '').replace('-', ' ')
        query_parts.append(league_clean)
    query_parts.append('sofascore')
    query_str = ' '.join(query_parts)
    # DuckDuckGo HTML requires POST
    data = urllib.parse.urlencode({'q': query_str}).encode()
    url = 'https://html.duckduckgo.com/html/'
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})

    try:
        r = urllib.request.urlopen(req, timeout=10)
        html_text = r.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f'  DuckDuckGo request failed: {e}')
        return None, None

    # Find first REAL result (inside class="result__url"), not random JS/related links
    for m in re.finditer(r'class="result__url"[^>]*>.*?sofascore\.com/football/team/[^/]+/(\d+)', html_text, re.DOTALL):
        team_id = int(m.group(1))
        return team_id, name
    return None, None


def search_team_id(name, league=None):
    """Search SofaScore for team by name.
    Returns (team_id, api_name) or (None, None).
    Uses accent-stripped / case-insensitive fuzzy matching on SofaScore API,
    then falls back to DuckDuckGo search for SofaScore team page.
    """
    search_name = urllib.request.quote(name)
    data = api_get(f'/search/teams/{search_name}')
    if data:
        teams = data if isinstance(data, list) else data.get('teams', data.get('results', []))
        if isinstance(teams, dict):
            teams = teams.get('results', [])

        if teams:
            # Build list of (entity, norm_name) — filter out youth/women/beach
            import re as _re
            _SKIP_RE = _re.compile(
                r'\b(U\d{1,2}|Women|Reserve|II+|Academy|Beach|Youth|U?19|U?20|U?21|U?22|U?23)\b',
                _re.IGNORECASE,
            )
            candidates = []
            for t in teams:
                entity = t.get('entity', t)
                t_name = entity.get('name', '')
                if not t_name:
                    continue
                if _SKIP_RE.search(t_name):
                    continue
                candidates.append((entity, _norm(t_name)))

            target_norm = _norm(name)

            # 1st pass: exact accent-stripped match
            for entity, cn in candidates:
                if cn == target_norm:
                    return entity.get('id'), entity.get('name')

            # 2nd pass: fuzzy best match (longest common substring)
            best_score = 0
            best_entity = None
            for entity, cn in candidates:
                score = _lcs_len(target_norm, cn)
                if len(cn) < 3 or len(target_norm) < 3:
                    continue
                if score > best_score:
                    best_score = score
                    best_entity = entity

            if best_entity and best_score >= 5:
                # Only accept fuzzy match if LCS score is high enough
                return best_entity.get('id'), best_entity.get('name')

            # No good match — fall through to DuckDuckGo

    # SofaScore API failed or returned empty — try DuckDuckGo
    print('  S→DDG', end=' ')
    ddg_id, ddg_name = _duckduckgo_search_team(name, league)
    if ddg_id:
        return ddg_id, ddg_name
    return None, None


def _lcs_len(a, b):
    """Longest common substring length (simple DP)."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(m):
        for j in range(n):
            if a[i] == b[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
                if dp[i + 1][j + 1] > best:
                    best = dp[i + 1][j + 1]
    return best


# =============================================================================
#  Main
# =============================================================================
def main():
    print("=== Scanning archive ===")
    teams_to_process = []

    for league_dir in sorted(os.listdir(ARCHIVE_DIR)):
        league_path = os.path.join(ARCHIVE_DIR, league_dir)
        if not os.path.isdir(league_path):
            continue
        for fname in sorted(os.listdir(league_path)):
            if not fname.endswith('.png'):
                continue
            slug = fname.replace('.png', '')
            with open(os.path.join(league_path, fname), 'rb') as f:
                logo_data = f.read()
            name = slug_to_name(slug, league_dir)
            teams_to_process.append({
                'slug': slug, 'name': name, 'league': league_dir, 'logo_data': logo_data,
            })

    print(f"Found {len(teams_to_process)} teams in archive")

    # Open / create registry — wipe and rebuild from scratch
    reg_conn = sqlite3.connect(REGISTRY_DB)
    reg_conn.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            sofascore_team_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT,
            league TEXT,
            logo_data BLOB
        )
    """)
    reg_conn.execute("DELETE FROM teams")
    reg_conn.commit()

    success = 0
    failed = 0
    failed_teams = []

    for i, team in enumerate(teams_to_process):
        print(f"[{i+1}/{len(teams_to_process)}] {team['name']} ({team['slug']})", end=' ')

        time.sleep(0.3)
        team_id, api_name = search_team_id(team['name'], team['league'])

        if team_id:
            reg_conn.execute(
                "INSERT OR REPLACE INTO teams (sofascore_team_id, name, slug, league, logo_data) VALUES (?, ?, ?, ?, ?)",
                (team_id, api_name, team['slug'], team['league'], team['logo_data'])
            )
            reg_conn.commit()
            success += 1
            print(f"OK → {api_name} (id={team_id})")
        else:
            failed += 1
            failed_teams.append(team)
            print("FAILED")

    # =========================================================================
    #  Report 1: Logos that could not be matched to a SofaScore team ID
    # =========================================================================
    print(f"\n{'='*60}")
    print(f"LOGOS WITHOUT SOFASCORE ID ({len(failed_teams)}):")
    if failed_teams:
        for t in failed_teams:
            print(f"  - {t['name']}  ({t['slug']}, {t['league']})")
    else:
        print("  (none)")

    # =========================================================================
    #  Report 2: Teams in football_matches.db that have no logo in registry
    # =========================================================================
    print(f"\n{'='*60}")
    print(f"TEAMS IN MATCHES DB WITHOUT LOGO ENTRY:")
    missing_matches = []
    if os.path.isfile(MATCHES_DB):
        reg_ids = set(
            r[0] for r in reg_conn.execute("SELECT sofascore_team_id FROM teams")
        )
        m_conn = sqlite3.connect(MATCHES_DB)
        m_conn.row_factory = sqlite3.Row
        rows = m_conn.execute("""
            SELECT home_team_sofascore_id AS sid, home_team AS tname FROM matches
            WHERE home_team_sofascore_id IS NOT NULL
            UNION
            SELECT away_team_sofascore_id AS sid, away_team AS tname FROM matches
            WHERE away_team_sofascore_id IS NOT NULL
        """)
        seen = set()
        for r in rows:
            sid, tname = r['sid'], r['tname']
            if sid not in reg_ids and sid not in seen:
                seen.add(sid)
                missing_matches.append((sid, tname))
        m_conn.close()
    else:
        print(f"  WARNING: {MATCHES_DB} not found, skipping this check.")

    if missing_matches:
        print(f"  Count: {len(missing_matches)}")
        for sid, tname in sorted(missing_matches, key=lambda x: x[1].lower()):
            print(f"  - id={sid}  {tname}")
    else:
        print("  (none)")

    reg_conn.close()

    print(f"\n=== DONE ===")
    print(f"Success (matched & saved): {success}")
    print(f"Failed logos: {failed}")
    print(f"Teams in matches without logo: {len(missing_matches)}")


if __name__ == '__main__':
    main()