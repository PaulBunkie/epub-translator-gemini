#!/usr/bin/env python3
"""
Скрипт сборки team_registry.db.

Читает football_matches.db (все уникальные команды + sofascore_team_id).
Сканирует static/team_logos_archive/**/*.png.
Строит маппинг: сначала по sofascore_team_id, fallback по normalized_name.
Собирает team_registry.db с BLOB логотипов.

Использование:
    python build_team_registry.py

Результат:
    - team_registry.db  (SQLite с таблицами teams + team_aliases)
    - Вывод MISSING списка команд без лого
"""

import sqlite3
import os
import re
import glob
from typing import Dict, Optional, List, Tuple

# Путь к основной БД матчей
FOOTBALL_DB = os.path.join(os.path.dirname(__file__), 'football_matches.db')
# Путь к выходной БД реестра
REGISTRY_DB = os.path.join(os.path.dirname(__file__), 'team_registry.db')
# Путь к папке с логотипами
LOGOS_ARCHIVE = os.path.join(os.path.dirname(__file__), 'static', 'team_logos_archive')


# Утилита: приводим display_name к normalised_name
def slugify(name: str) -> str:
    """
    Приводит название команды к стандартному slug.
    "Inter Milan" -> "inter-milan"
    "FC Barcelona" -> "barcelona"
    """
    if not name:
        return ""
    s = name.lower().strip()
    
    # Сначала проверяем точные известные маппинги (источники: файлы в archive)
    known_mappings = {
        'bayern munich': 'bayern-muenchen',
        'borussia mönchengladbach': 'borussia-moenchengladbach',
        'borussia monchengladbach': 'borussia-moenchengladbach',
        '1. fc köln': 'koeln',
        '1. fc heidenheim': 'heidenheim',
        'tsg hoffenheim': 'hoffenheim',
        'vfb stuttgart': 'stuttgart',
        'vfl wolfsburg': 'wolfsburg',
        'bodø/glimt': 'bod-glimt',
        'mjøndalen': 'n',
    }
    if s in known_mappings:
        return known_mappings[s]
    
    # Убираем распространённые префиксы
    prefixes = [
        'sk ', 'fc ', 'sc ', 'cf ', 'ac ', 'as ', 'rc ', 'fk ', 'if ', 'bk ',
        '1. ', '1 ', '2. ', '3. ', 'cd ', 'ud ', 'cf ', 'sd ',
        'royale ', 'royal ', 'r. ', 'r ', 'h. ', 'h ',
        'the ', 'de ', 'la ', 'le ', 'los ', 'las ', 'el ',
        'afc ', 'cfc ', 'dfc ', 'sfc ', 'pfc ', 'kfc ', 'bfc ',
        'tsv ', 'fsv ',
    ]
    for prefix in prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    
    # Замена умлаутов и спецсимволов
    replacements = {
        'ø': 'o', 'æ': 'ae', 'å': 'aa',
        'ö': 'o', 'ü': 'u', 'ä': 'a', 'ß': 'ss',
        'ñ': 'n', 'ç': 'c',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'à': 'a', 'á': 'a', 'â': 'a', 'ã': 'a',
        'í': 'i', 'î': 'i', 'ï': 'i',
        'ó': 'o', 'ô': 'o', 'õ': 'o',
        'ú': 'u', 'û': 'u',
        'ý': 'y',
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    
    # Заменяем пробелы, дефисы на дефисы, убираем лишнее
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s


def scan_logo_files(archive_path: str) -> Dict[str, str]:
    """
    Сканирует папку с логотипами и возвращает словарь:
    {slug: full_path_to_png}
    """
    logos = {}
    pattern = os.path.join(archive_path, '**', '*.png')
    for filepath in glob.glob(pattern, recursive=True):
        basename = os.path.splitext(os.path.basename(filepath))[0]
        slug = basename.lower().strip()
        if slug:
            logos[slug] = filepath
    return logos


def build_registry():
    print("=" * 60)
    print("Build Team Registry")
    print("=" * 60)
    
    # 0. Авто-миграция: добавляем колонки в football_matches.db если их нет
    print(f"\n[0] Checking football_matches.db schema...")
    if not os.path.isfile(FOOTBALL_DB):
        print(f"    ERROR: File not found: {FOOTBALL_DB}")
        return
    
    conn_fb = sqlite3.connect(FOOTBALL_DB)
    cursor_fb = conn_fb.cursor()
    cursor_fb.execute("PRAGMA table_info(matches)")
    columns = [row[1] for row in cursor_fb.fetchall()]
    for col_name in ['home_team_sofascore_id', 'away_team_sofascore_id']:
        if col_name not in columns:
            print(f"    Adding column '{col_name}'...")
            cursor_fb.execute(f"ALTER TABLE matches ADD COLUMN {col_name} INTEGER")
            conn_fb.commit()
        else:
            print(f"    Column '{col_name}' already exists.")
    conn_fb.close()
    
    # 1. Сканируем PNG
    print(f"\n[1] Scanning logos in: {LOGOS_ARCHIVE}")
    if not os.path.isdir(LOGOS_ARCHIVE):
        print(f"    WARNING: Directory not found: {LOGOS_ARCHIVE}")
        logo_map = {}
    else:
        logo_map = scan_logo_files(LOGOS_ARCHIVE)
        print(f"    Found {len(logo_map)} logo files")
    
    # 2. Читаем football_matches.db
    print(f"\n[2] Reading football matches from: {FOOTBALL_DB}")
    if not os.path.isfile(FOOTBALL_DB):
        print(f"    ERROR: File not found: {FOOTBALL_DB}")
        return
    
    conn_fb = sqlite3.connect(FOOTBALL_DB)
    conn_fb.row_factory = sqlite3.Row
    cursor = conn_fb.cursor()
    
    # Собираем уникальные команды
    cursor.execute("""
        SELECT DISTINCT home_team AS team_name, home_team_sofascore_id AS sofascore_id
        FROM matches
        WHERE home_team IS NOT NULL AND home_team != ''
        UNION
        SELECT DISTINCT away_team AS team_name, away_team_sofascore_id AS sofascore_id
        FROM matches
        WHERE away_team IS NOT NULL AND away_team != ''
        ORDER BY team_name
    """)
    rows = cursor.fetchall()
    print(f"    Found {len(rows)} unique team names")
    
    # 3. Строим маппинг команд
    print(f"\n[3] Building team mappings...")
    
    # {sofascore_id: {'display_name': ..., 'normalized': ...}}
    teams_by_id: Dict[int, dict] = {}
    # {normalized: {'display_name': ..., 'sofascore_id': ...}}
    teams_by_slug: Dict[str, dict] = {}
    # Все названия для aliases
    aliases: List[Tuple[Optional[int], str]] = []  # (sofascore_id, alias_name)
    
    for row in rows:
        name = row['team_name'].strip()
        sid = row['sofascore_id']
        slug = slugify(name)
        aliases.append((sid, name))
        
        if sid is not None:
            if sid not in teams_by_id:
                teams_by_id[sid] = {'display_name': name, 'normalized': slug}
            # Если уже есть, но display_name короче/лучше — обновим
            elif len(name) < len(teams_by_id[sid]['display_name']):
                teams_by_id[sid] = {'display_name': name, 'normalized': slug}
        
        if slug:
            if slug not in teams_by_slug:
                teams_by_slug[slug] = {'display_name': name, 'sofascore_id': sid}
            elif teams_by_slug[slug]['sofascore_id'] is None and sid is not None:
                teams_by_slug[slug] = {'display_name': name, 'sofascore_id': sid}
    
    conn_fb.close()
    
    print(f"    Teams by sofascore_id: {len(teams_by_id)}")
    print(f"    Teams by slug: {len(teams_by_slug)}")
    
    # 4. Собираем team_registry.db
    print(f"\n[4] Building {REGISTRY_DB}...")
    conn_reg = sqlite3.connect(REGISTRY_DB)
    cursor_reg = conn_reg.cursor()
    # Очищаем существующие таблицы (не удаляем файл, чтобы не было блокировки)
    cursor_reg.execute("DROP TABLE IF EXISTS team_aliases")
    cursor_reg.execute("DROP TABLE IF EXISTS teams")
    
    cursor_reg.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            sofascore_team_id INTEGER PRIMARY KEY,
            normalized_name   TEXT NOT NULL,
            display_name      TEXT NOT NULL,
            logo_data         BLOB,
            logo_format       TEXT DEFAULT 'png',
            has_logo          INTEGER DEFAULT 0,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor_reg.execute("""
        CREATE TABLE IF NOT EXISTS team_aliases (
            alias_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sofascore_team_id INTEGER REFERENCES teams(sofascore_team_id),
            alias_name        TEXT NOT NULL,
            source_type       TEXT NOT NULL DEFAULT 'odds_api',
            UNIQUE(sofascore_team_id, alias_name, source_type)
        )
    """)
    cursor_reg.execute("CREATE INDEX IF NOT EXISTS idx_aliases_name ON team_aliases(alias_name)")
    cursor_reg.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_norm ON teams(normalized_name)")
    
    # Множество для отслеживания обработанных normalized_name
    processed_slugs = set()
    missing_list = []  # (display_name, reason)
    inserted_count = 0
    
    # Сначала проход по командам с sofascore_id
    for sid, info in teams_by_id.items():
        slug = info['normalized']
        if not slug:
            continue
        if slug in processed_slugs:
            continue
        processed_slugs.add(slug)
        
        # Ищем лого
        logo_data = None
        has_logo = 0
        
        # Ищем лого через find_logo (несколько вариантов slug)
        filepath, logo_data = find_logo(slug, logo_map)
        if logo_data:
            has_logo = 1
        
        cursor_reg.execute("""
            INSERT OR REPLACE INTO teams
                (sofascore_team_id, normalized_name, display_name, logo_data, logo_format, has_logo)
            VALUES (?, ?, ?, ?, 'png', ?)
        """, (sid, slug, info['display_name'], logo_data, has_logo))
        inserted_count += 1
        
        if not has_logo:
            missing_list.append((info['display_name'], "нет PNG, есть sofascore_id"))
    
    # Потом проход по командам без sofascore_id
    for slug, info in teams_by_slug.items():
        if slug in processed_slugs:
            continue
        # Пропускаем если есть те же данные через ID
        # (уже обработаны)
        processed_slugs.add(slug)
        
        sid = info['sofascore_id']
        
        # Ищем лого через find_logo (несколько вариантов slug)
        filepath, logo_data = find_logo(slug, logo_map)
        if logo_data:
            has_logo = 1
        
        # При sofascore_id=NULL используем autoincrement? Нет, сохраняем NULL
        cursor_reg.execute("""
            INSERT OR IGNORE INTO teams
                (sofascore_team_id, normalized_name, display_name, logo_data, logo_format, has_logo)
            VALUES (?, ?, ?, ?, 'png', ?)
        """, (sid, slug, info['display_name'], logo_data, has_logo))
        inserted_count += 1
        
        if not has_logo:
            if sid:
                missing_list.append((info['display_name'], "нет PNG, есть sofascore_id"))
            else:
                missing_list.append((info['display_name'], "нет ни PNG, ни sofascore_id"))
    
    # 5. Пишем aliases
    alias_count = 0
    for sid, alias_name in aliases:
        # Определяем source_type
        source_type = 'odds_api'
        # Пытаемся найти sid в teams_by_id
        if sid is None:
            # Ищем через slug
            slug = slugify(alias_name)
            # Находим sid по slug
            found_sid = None
            for t_sid, t_info in teams_by_id.items():
                if t_info['normalized'] == slug:
                    found_sid = t_sid
                    break
            if found_sid is None:
                # Ищем в teams_by_slug
                if slug in teams_by_slug and teams_by_slug[slug]['sofascore_id']:
                    found_sid = teams_by_slug[slug]['sofascore_id']
            sid = found_sid
        
        try:
            cursor_reg.execute("""
                INSERT OR IGNORE INTO team_aliases (sofascore_team_id, alias_name, source_type)
                VALUES (?, ?, ?)
            """, (sid, alias_name, source_type))
            alias_count += 1
        except sqlite3.IntegrityError:
            pass
    
    conn_reg.commit()
    
    # 6. Статистика
    cursor_reg.execute("SELECT COUNT(*) FROM teams")
    total_teams = cursor_reg.fetchone()[0]
    cursor_reg.execute("SELECT COUNT(*) FROM teams WHERE has_logo = 1")
    teams_with_logo = cursor_reg.fetchone()[0]
    cursor_reg.execute("SELECT COUNT(*) FROM teams WHERE has_logo = 0")
    teams_without_logo = cursor_reg.fetchone()[0]
    
    conn_reg.close()
    
    print(f"\n[5] Registry summary:")
    print(f"    Total teams: {total_teams}")
    print(f"    With logo:   {teams_with_logo}")
    print(f"    Without logo: {teams_without_logo}")
    print(f"    Aliases:     {alias_count}")
    print(f"    DB size:     {os.path.getsize(REGISTRY_DB) / 1024:.1f} KB")
    
    # MISSING список
    if missing_list:
        print(f"\n[!] MISSING logos ({len(missing_list)}):")
        for name, reason in sorted(missing_list, key=lambda x: x[0]):
            print(f"    - {name} ({reason})")
    else:
        print(f"\n[✓] All teams have logos!")
    
    print(f"\nDone. Registry saved to: {REGISTRY_DB}")


def find_logo(slug: str, logo_map: Dict[str, str]) -> Tuple[Optional[str], Optional[bytes]]:
    """
    Ищет лого по slug и альтернативным вариантам.
    Возвращает (filepath, logo_data) или (None, None).
    """
    if not slug:
        return None, None
    
    # Варианты slug для проверки
    candidates = []
    
    # Основной slug
    candidates.append(slug)
    # Без дефисов
    candidates.append(slug.replace('-', ''))
    
    # Для немецких команд: пробуем немецкую транслитерацию
    german_variants = {
        'muenchen': 'muenchen',
        'muenchener': 'muenchen',
        'munchen': 'muenchen',
        'moenchengladbach': 'moenchengladbach',
        'monchengladbach': 'moenchengladbach',
        'nuernberg': 'nuremberg',
        'nurnberg': 'nuremberg',
    }
    for part in slug.split('-'):
        if part in german_variants:
            candidates.append(slug.replace(part, german_variants[part]))
    
    # Для скандинавских: пробуем без специальных букв через replacements
    # (уже обработано в slugify, но на всякий случай)
    # Для испанских: пробуем безtilde
    # Для французских: без umlaut
    
    # Пробуем с префиксом "fc-"
    if not slug.startswith('fc-'):
        candidates.append('fc-' + slug)
        candidates.append('fc-' + slug.replace('-', ''))
    
    # Проверяем каждый кандидат
    for candidate in candidates:
        if candidate in logo_map:
            filepath = logo_map[candidate]
            data = read_png(filepath)
            if data:
                return filepath, data
    
    return None, None


def read_png(filepath: str) -> Optional[bytes]:
    """Читает PNG файл и возвращает bytes или None."""
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        return data
    except Exception as e:
        print(f"    ERROR reading {filepath}: {e}")
        return None


if __name__ == '__main__':
    build_registry()