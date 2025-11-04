import sqlite3
import requests
import json
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import os
from dotenv import load_dotenv



from config import FOOTBALL_DB_FILE

load_dotenv()

FOOTBALL_DATABASE_FILE = str(FOOTBALL_DB_FILE)
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_API_URL = "https://api.the-odds-api.com/v4"
SOFASCORE_API_URL = "https://api.sofascore1.com/api/v1"

# Список User-Agent'ов для SofaScore (случайный выбор, чтобы уменьшить шанс бана)
SOFASCORE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1"
]

SOFASCORE_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}

# Полный список всех доступных футбольных лиг из The Odds API
# Источник: https://api.the-odds-api.com/v4/sports/
ALL_AVAILABLE_FOOTBALL_LEAGUES = [
    # --- Европейские топ-лиги ---
    "soccer_epl",                    # Английская Премьер-лига (EPL)
    "soccer_spain_la_liga",          # Ла Лига (Испания)
    "soccer_italy_serie_a",          # Серия A (Италия)
    "soccer_germany_bundesliga",     # Бундеслига (Германия)
    "soccer_france_ligue_one",       # Лига 1 (Франция)
    "soccer_netherlands_eredivisie", # Эредивизи (Нидерланды)
    "soccer_portugal_primeira_liga", # Примейра Лига (Португалия)
    "soccer_spl",                    # Премьершип (Шотландия)
    
    # --- Европейские вторые лиги ---
    "soccer_efl_champ",              # Чемпионшип (Англия)
    "soccer_spain_segunda_division", # Ла Лига 2 (Испания)
    "soccer_italy_serie_b",          # Серия B (Италия)
    "soccer_germany_bundesliga2",    # Бундеслига 2 (Германия)
    "soccer_germany_liga3",          # 3. Лига (Германия)
    "soccer_france_ligue_two",       # Лига 2 (Франция)
    "soccer_england_league1",        # Лига 1 (Англия)
    "soccer_england_league2",        # Лига 2 (Англия)
    "soccer_sweden_superettan",      # Суперэттан (Швеция)
    
    # --- Другие европейские лиги ---
    "soccer_belgium_first_div",      # Первый дивизион (Бельгия)
    "soccer_austria_bundesliga",     # Бундеслига (Австрия)
    "soccer_switzerland_superleague", # Суперлига (Швейцария)
    "soccer_greece_super_league",    # Суперлига (Греция)
    "soccer_turkey_super_league",    # Суперлига (Турция)
    "soccer_poland_ekstraklasa",     # Экстракласса (Польша)
    "soccer_denmark_superliga",      # Суперлига (Дания)
    "soccer_norway_eliteserien",     # Элитсериен (Норвегия)
    "soccer_sweden_allsvenskan",     # Алльсвенскан (Швеция)
    "soccer_finland_veikkausliiga",  # Вейккауслига (Финляндия)
    "soccer_germany_liga3",          # 3. Лига (Германия) - дубликат?
    
    # --- Европейские клубные турниры ---
    "soccer_uefa_champs_league",     # Лига Чемпионов
    "soccer_uefa_europa_league",     # Лига Европы
    "soccer_uefa_europa_conference_league", # Лига Конференций
    "soccer_fifa_world_cup_qualifiers_europe", # Отборочные ЧМ (Европа)
    
    # --- Южноамериканские лиги ---
    "soccer_argentina_primera_division", # Примера Дивизион (Аргентина)
    "soccer_brazil_campeonato",      # Серия A (Бразилия)
    "soccer_brazil_serie_b",         # Серия B (Бразилия)
    "soccer_chile_campeonato",       # Примера Дивизион (Чили)
    "soccer_conmebol_copa_libertadores", # Копа Либертадорес
    "soccer_conmebol_copa_sudamericana", # Копа Судамерикана
    
    # --- Североамериканские лиги ---
    "soccer_usa_mls",                # MLS (США/Канада)
    "soccer_mexico_ligamx",          # Лига MX (Мексика)
    
    # --- Азиатские лиги ---
    "soccer_japan_j_league",         # J League (Япония)
    "soccer_korea_kleague1",         # K League 1 (Корея)
    "soccer_china_superleague",      # Суперлига (Китай)
    
    # --- Океания ---
    "soccer_australia_aleague",      # A-League (Австралия)
]

# Список лиг для сбора матчей (можно переопределить через FOOTBALL_LEAGUES в .env)
# Формат: "soccer_epl,soccer_spain_la_liga,soccer_germany_bundesliga" и т.д.
# Если не указано, используется список по умолчанию ниже
# 
# ВАЖНО: Для отладки используем только 3 лиги, чтобы не выйти за лимит запросов API
# Чтобы включить все лиги, раскомментируйте нужные строки ниже
DEFAULT_FOOTBALL_LEAGUES = [
    "soccer_epl",                    # Английская Премьер-лига
    "soccer_uefa_champs_league",     # Лига Чемпионов
    "soccer_uefa_europa_league",     # Лига Европы
    # --- Раскомментируйте для включения остальных лиг ---
    # "soccer_spain_la_liga",          # Ла Лига (Испания)
    # "soccer_italy_serie_a",          # Серия A (Италия)
    # "soccer_germany_bundesliga",     # Бундеслига (Германия)
    # "soccer_france_ligue_one",       # Лига 1 (Франция)
    # "soccer_netherlands_eredivisie", # Эредивизи (Нидерланды)
    # "soccer_portugal_primeira_liga", # Примейра Лига (Португалия)
    # "soccer_spl",                    # Премьершип (Шотландия)
    # "soccer_efl_champ",              # Чемпионшип (Англия)
    # "soccer_spain_segunda_division", # Ла Лига 2 (Испания)
    # "soccer_italy_serie_b",          # Серия B (Италия)
    # "soccer_germany_bundesliga2",    # Бундеслига 2 (Германия)
    # "soccer_germany_liga3",          # 3. Лига (Германия)
    # "soccer_france_ligue_two",       # Лига 2 (Франция)
    # "soccer_england_league1",        # Лига 1 (Англия)
    # "soccer_england_league2",        # Лига 2 (Англия)
    # "soccer_belgium_first_div",      # Первый дивизион (Бельгия)
    # "soccer_austria_bundesliga",     # Бундеслига (Австрия)
    # "soccer_switzerland_superleague", # Суперлига (Швейцария)
    # "soccer_greece_super_league",    # Суперлига (Греция)
    # "soccer_turkey_super_league",    # Суперлига (Турция)
    # "soccer_poland_ekstraklasa",     # Экстракласса (Польша)
    # "soccer_denmark_superliga",      # Суперлига (Дания)
    # "soccer_norway_eliteserien",     # Элитсериен (Норвегия)
    # "soccer_sweden_allsvenskan",     # Алльсвенскан (Швеция)
    # "soccer_sweden_superettan",      # Суперэттан (Швеция)
    # "soccer_finland_veikkausliiga",  # Вейккауслига (Финляндия)
     "soccer_uefa_europa_conference_league", # Лига Конференций
    # "soccer_fifa_world_cup_qualifiers_europe", # Отборочные ЧМ (Европа)
    # "soccer_argentina_primera_division", # Примера Дивизион (Аргентина)
    # "soccer_brazil_campeonato",      # Серия A (Бразилия)
    # "soccer_brazil_serie_b",         # Серия B (Бразилия)
    # "soccer_chile_campeonato",       # Примера Дивизион (Чили)
    # "soccer_conmebol_copa_libertadores", # Копа Либертадорес
    # "soccer_conmebol_copa_sudamericana", # Копа Судамерикана
    # "soccer_usa_mls",                # MLS (США/Канада)
    # "soccer_mexico_ligamx",          # Лига MX (Мексика)
    # "soccer_japan_j_league",         # J League (Япония)
    # "soccer_korea_kleague1",         # K League 1 (Корея)
    # "soccer_china_superleague",      # Суперлига (Китай)
    # "soccer_australia_aleague",      # A-League (Австралия)
]

# Глобальный экземпляр менеджера
_manager = None


def get_football_db_connection():
    """Создает соединение с БД футбольных матчей."""
    conn = sqlite3.connect(FOOTBALL_DATABASE_FILE, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_football_db():
    """
    Инициализирует базу данных футбольных матчей.
    Создает таблицу matches если её нет.
    """
    conn = None
    try:
        conn = get_football_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Создание таблицы matches ---
        print("[FootballDB] Checking/Creating 'matches' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id TEXT UNIQUE NOT NULL,
                sofascore_event_id INTEGER,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                fav TEXT NOT NULL,
                fav_team_id INTEGER NOT NULL,  -- 1=home, 0=away
                match_date TEXT NOT NULL,
                match_time TEXT NOT NULL,
                initial_odds REAL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                stats_60min TEXT,  -- JSON с статистикой на 60-й минуте
                bet INTEGER,  -- Результат проверки условий на 60-й минуте
                final_score_home INTEGER,
                final_score_away INTEGER,
                fav_won INTEGER,  -- 1 если фаворит выиграл, 0 если нет
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        # --- Проверка и добавление поля bet ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'bet' not in columns:
            print("[FootballDB] Adding 'bet' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN bet INTEGER")
            conn.commit()
        
        # --- Проверка и добавление поля sofascore_join ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'sofascore_join' not in columns:
            print("[FootballDB] Adding 'sofascore_join' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN sofascore_join TEXT")
            conn.commit()
            print("[FootballDB] Column 'sofascore_join' added successfully.")
        else:
            print("[FootballDB] Column 'sofascore_join' already exists.")

        # --- Создание индексов ---
        print("[FootballDB] Creating indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_fixture_id ON matches(fixture_id)")
        conn.commit()

        print("[FootballDB] Database initialization complete.")

    except sqlite3.Error as e:
        print(f"[FootballDB ERROR] Database initialization failed: {e}")
        raise
    finally:
        if conn:
            conn.close()


def get_manager():
    """Получает глобальный экземпляр менеджера."""
    global _manager
    if _manager is None:
        _manager = FootballManager()
    return _manager


class FootballManager:
    """
    Менеджер для работы с футбольными матчами и коэффициентами.
    """

    def __init__(self):
        if not ODDS_API_KEY:
            raise ValueError("Не установлена переменная окружения ODDS_API_KEY")
        
        self.api_key = ODDS_API_KEY
        
        # Переменные для отслеживания лимитов API (в памяти)
        self.requests_remaining = None
        self.requests_used = None
        self.requests_last_cost = None
        
        # Получаем список лиг для сбора (из переменной окружения или по умолчанию)
        leagues_env = os.getenv("FOOTBALL_LEAGUES")
        if leagues_env:
            # Парсим список лиг из переменной окружения (через запятую)
            self.leagues = [league.strip() for league in leagues_env.split(",") if league.strip()]
            print(f"[Football] Используются лиги из FOOTBALL_LEAGUES: {len(self.leagues)} лиг")
        else:
            self.leagues = DEFAULT_FOOTBALL_LEAGUES
            print(f"[Football] Используются лиги по умолчанию: {len(self.leagues)} лиг")
        
        # Инициализируем БД
        init_football_db()
        
        # Получаем начальные значения лимитов через запрос к /sports
        self._initialize_api_limits()
        
        print("[Football] Менеджер инициализирован")

    def _extract_api_limits_from_headers(self, response: requests.Response):
        """
        Извлекает лимиты API из заголовков ответа и обновляет переменные класса.
        
        Args:
            response: Объект ответа от requests
        """
        try:
            # Извлекаем заголовки (API использует lowercase заголовки)
            remaining = response.headers.get('x-requests-remaining')
            used = response.headers.get('x-requests-used')
            last_cost = response.headers.get('x-requests-last')
            
            if remaining is not None:
                try:
                    self.requests_remaining = int(remaining)
                except (ValueError, TypeError):
                    pass
            
            if used is not None:
                try:
                    self.requests_used = int(used)
                except (ValueError, TypeError):
                    pass
            
            if last_cost is not None:
                try:
                    self.requests_last_cost = int(last_cost)
                except (ValueError, TypeError):
                    pass
            
            # Логируем текущие значения лимитов
            if self.requests_remaining is not None:
                print(f"[Football API Limits] Осталось запросов: {self.requests_remaining}, Использовано: {self.requests_used}, Стоимость последнего: {self.requests_last_cost}")
                
                # Предупреждение при низком лимите
                if self.requests_remaining < 50:
                    print(f"[Football WARNING] Критически низкий лимит запросов: {self.requests_remaining}")
                elif self.requests_remaining < 100:
                    print(f"[Football WARNING] Низкий лимит запросов: {self.requests_remaining}")
                    
        except Exception as e:
            print(f"[Football ERROR] Ошибка извлечения лимитов из заголовков: {e}")

    def _initialize_api_limits(self):
        """
        Инициализирует начальные значения лимитов API через запрос к /sports.
        Вызывается при старте приложения.
        """
        try:
            print("[Football] Получение начальных значений лимитов API через запрос к /sports...")
            params = {'apiKey': self.api_key}
            
            url = f"{ODDS_API_URL}/sports"
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            # Извлекаем лимиты из заголовков
            self._extract_api_limits_from_headers(response)
            
            print(f"[Football] Начальные лимиты API установлены: осталось={self.requests_remaining}, использовано={self.requests_used}")
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка инициализации лимитов API: {e}")
            # Не падаем, если не удалось получить лимиты - продолжим без них

    def _make_api_request(self, endpoint: str, params: dict) -> Optional[list]:
        """
        Выполняет запрос к The Odds API.
        
        Args:
            endpoint: Путь эндпоинта (например, "/sports/soccer_epl/odds")
            params: Параметры запроса
            
        Returns:
            Список данных или None в случае ошибки
        """
        try:
            url = f"{ODDS_API_URL}{endpoint}"
            print(f"[Football] Запрос к API: {endpoint}, params: {params}")
            
            # Добавляем API ключ в параметры
            params['apiKey'] = self.api_key
            
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            # Извлекаем и обновляем лимиты из заголовков ответа
            self._extract_api_limits_from_headers(response)
            
            data = response.json()
            
            print(f"[Football] Успешный ответ от API, получено {len(data) if isinstance(data, list) else 1} записей")
            return data
            
        except requests.exceptions.RequestException as e:
            print(f"[Football ERROR] Ошибка запроса к API: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"[Football ERROR] Ошибка парсинга JSON: {e}")
            return None

    def get_available_soccer_leagues(self) -> List[Dict[str, Any]]:
        """
        Получает список доступных футбольных лиг из API.
        
        Returns:
            Список словарей с информацией о лигах: [{'key': 'soccer_epl', 'title': 'EPL', ...}, ...]
        """
        try:
            data = self._make_api_request("/sports", {})
            
            if not data:
                print("[Football] Не удалось получить список лиг")
                return []
            
            # Фильтруем только футбольные лиги (без outrights)
            soccer_leagues = [
                league for league in data
                if league.get('group') == 'Soccer' and not league.get('has_outrights', False)
            ]
            
            print(f"[Football] Найдено {len(soccer_leagues)} доступных футбольных лиг")
            return soccer_leagues
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка получения списка лиг: {e}")
            import traceback
            print(traceback.format_exc())
            return []

    def _normalize_team_name(self, name: str) -> str:
        """
        Нормализует название команды для сравнения.
        Убирает пробелы, приводит к нижнему регистру, убирает специальные символы и префиксы.
        Нормализует специальные символы (датские, норвежские, немецкие буквы и т.д.).
        
        Args:
            name: Исходное название команды
            
        Returns:
            Нормализованное название
        """
        if not name:
            return ""
        
        # Список префиксов для удаления (распространенные префиксы футбольных клубов)
        prefixes = [
            'sk ', 'fc ', 'sc ', 'cf ', 'ac ', 'as ', 'rc ', 'fk ', 'if ', 'bk ',
            '1. ', '1 ', '2. ', '3. ', 'cd ', 'ud ', 'cf ', 'sd ', 'fc. ', 'sc. ',
            'royale ', 'royal ', 'r. ', 'r ', 'h. ', 'h ', 'v. ', 'v ', 'vs ', 'vs. ',
            'the ', 'of ', 'de ', 'la ', 'le ', 'los ', 'las ', 'el ', 'der ', 'die ', 'das ',
            'afc ', 'cfc ', 'dfc ', 'sfc ', 'pfc ', 'kfc ', 'bfc ', 'vfc ', 'tsv ', 'fsv ',
            'vv ', 'vv. ', 'vvv ', 'vvv-', 'vvv. ', 'vvv ', 'vvv-', 'vvv. '
        ]
        
        normalized = name.lower().strip()
        
        # Удаляем префиксы
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
        
        # Нормализуем специальные символы (датские, норвежские, немецкие, испанские и т.д.)
        # Это поможет сопоставить "Copenhagen" с "København", "München" с "Munich", и т.д.
        char_replacements = {
            'ø': 'o', 'Ø': 'o',  # Датская/норвежская буква
            'æ': 'ae', 'Æ': 'ae',  # Датская/норвежская буква
            'å': 'aa', 'Å': 'aa',  # Датская/норвежская буква
            'ö': 'o', 'Ö': 'o',  # Немецкая/шведская буква
            'ü': 'u', 'Ü': 'u',  # Немецкая буква
            'ä': 'a', 'Ä': 'a',  # Немецкая/шведская буква
            'ß': 'ss',  # Немецкая буква
            'ñ': 'n', 'Ñ': 'n',  # Испанская буква
            'ç': 'c', 'Ç': 'c',  # Французская/португальская буква
            'é': 'e', 'É': 'e',  # Французская буква
            'è': 'e', 'È': 'e',  # Французская буква
            'ê': 'e', 'Ê': 'e',  # Французская буква
            'ë': 'e', 'Ë': 'e',  # Французская буква
            'à': 'a', 'À': 'a',  # Французская буква
            'á': 'a', 'Á': 'a',  # Испанская буква
            'â': 'a', 'Â': 'a',  # Французская буква
            'ã': 'a', 'Ã': 'a',  # Португальская буква
            'í': 'i', 'Í': 'i',  # Испанская буква
            'î': 'i', 'Î': 'i',  # Французская буква
            'ï': 'i', 'Ï': 'i',  # Французская буква
            'ó': 'o', 'Ó': 'o',  # Испанская буква
            'ô': 'o', 'Ô': 'o',  # Французская буква
            'õ': 'o', 'Õ': 'o',  # Португальская буква
            'ú': 'u', 'Ú': 'u',  # Испанская буква
            'û': 'u', 'Û': 'u',  # Французская буква
            'ý': 'y', 'Ý': 'y',  # Чешская буква
        }
        for old_char, new_char in char_replacements.items():
            normalized = normalized.replace(old_char, new_char)
        
        # Убираем пробелы, приводим к нижнему регистру, убираем дефисы и подчеркивания
        normalized = normalized.replace(" ", "").replace("-", "").replace("_", "")
        
        # Убираем другие специальные символы (точки, запятые и т.д.)
        normalized = ''.join(c for c in normalized if c.isalnum())
        
        return normalized

    def _fetch_sofascore_events(self, date: str, max_retries: int = 5) -> Optional[List[Dict]]:
        """
        Получает список запланированных событий из SofaScore для указанной даты.
        Использует простой requests с ретраями и экспоненциальным бэкоффом.
        
        Args:
            date: Дата в формате YYYY-MM-DD
            max_retries: Максимальное количество попыток
            
        Returns:
            Список событий или None в случае ошибки
        """
        import random
        
        url = f"{SOFASCORE_API_URL}/sport/football/scheduled-events/{date}"
        attempt = 0
        
        while attempt < max_retries:
            attempt += 1
            
            # Формируем заголовки со случайным User-Agent
            headers = SOFASCORE_DEFAULT_HEADERS.copy()
            headers["User-Agent"] = random.choice(SOFASCORE_USER_AGENTS)
            
            try:
                print(f"[Football SofaScore] Запрос событий на дату {date} (попытка {attempt}/{max_retries})")
                response = requests.get(url, headers=headers, timeout=15.0)
                code = response.status_code
                
                if code == 200:
                    # Успешный ответ
                    try:
                        data = response.json()
                        events = data.get('events', [])
                        print(f"[Football SofaScore] Получено {len(events)} событий на дату {date}")
                        return events
                    except json.JSONDecodeError as e:
                        print(f"[Football SofaScore ERROR] Ошибка парсинга JSON: {e}")
                        return None
                
                elif code == 403:
                    # 403 Forbidden - пробуем с задержкой
                    retry_after = response.headers.get("Retry-After")
                    wait = 5 + random.uniform(0.5, 3.0)
                    print(f"[Football SofaScore] 403 Forbidden для даты {date}. Retry-After: {retry_after}. Ждём {wait:.1f}s и пробуем снова...")
                    if attempt < max_retries:
                        time.sleep(wait)
                        continue
                    else:
                        print(f"[Football SofaScore ERROR] Не удалось получить данные за {max_retries} попыток (403 Forbidden)")
                        return None
                
                elif 500 <= code < 600:
                    # Серверная ошибка - экспоненциальный бэкофф
                    wait = min(2 ** attempt + random.random(), 60)
                    print(f"[Football SofaScore] Серверная ошибка {code} для даты {date}. Ждём {wait:.1f}s...")
                    if attempt < max_retries:
                        time.sleep(wait)
                        continue
                    else:
                        print(f"[Football SofaScore ERROR] Серверная ошибка {code} после {max_retries} попыток")
                        return None
                
                else:
                    # Другие коды - выводим и прекращаем
                    print(f"[Football SofaScore ERROR] HTTP {code} для даты {date}. Response: {response.text[:200]}")
                    return None
                    
            except requests.RequestException as e:
                # Ошибка сети - экспоненциальный бэкофф
                wait = min(2 ** attempt + random.random(), 30)
                print(f"[Football SofaScore] Ошибка сети для даты {date}: {e}. Ждём {wait:.1f}s и повторяем...")
                if attempt < max_retries:
                    time.sleep(wait)
                    continue
                else:
                    print(f"[Football SofaScore ERROR] Не удалось получить данные из-за сетевой ошибки после {max_retries} попыток: {e}")
                    return None
            except Exception as e:
                print(f"[Football SofaScore ERROR] Неожиданная ошибка для даты {date}: {e}")
                import traceback
                traceback.print_exc()
                return None
        
        print(f"[Football SofaScore ERROR] Не удалось получить данные за {max_retries} попыток")
        return None

    def _match_sofascore_event(self, match: Dict, sofascore_events: List[Dict]) -> Optional[int]:
        """
        Сопоставляет матч из The Odds API с событием из SofaScore по названиям команд.
        Время не проверяется, так как данные фильтруются по дате, и две команды не могут играть два матча в один день.
        
        Args:
            match: Словарь с данными матча из БД (или The Odds API)
                  Должен содержать: home_team, away_team
            sofascore_events: Список событий из SofaScore (уже отфильтрованные по дате)
            
        Returns:
            sofascore_event_id если найден, иначе None
        """
        try:
            home_team_odds = match.get('home_team', '')
            away_team_odds = match.get('away_team', '')
            
            if not all([home_team_odds, away_team_odds]):
                return None
            
            # Нормализуем названия команд из матча
            home_normalized = self._normalize_team_name(home_team_odds)
            away_normalized = self._normalize_team_name(away_team_odds)
            
            # Ищем совпадение в событиях SofaScore
            for event in sofascore_events:
                try:
                    event_id = event.get('id')
                    if not event_id:
                        continue
                    
                    home_team_obj = event.get('homeTeam', {})
                    away_team_obj = event.get('awayTeam', {})
                    
                    if not home_team_obj or not away_team_obj:
                        continue
                    
                    # Получаем все возможные варианты названий команд из SofaScore
                    home_team_variants = []
                    away_team_variants = []
                    
                    # Основное название
                    if home_team_obj.get('name'):
                        home_team_variants.append(home_team_obj['name'])
                    if away_team_obj.get('name'):
                        away_team_variants.append(away_team_obj['name'])
                    
                    # Короткое название
                    if home_team_obj.get('shortName'):
                        home_team_variants.append(home_team_obj['shortName'])
                    if away_team_obj.get('shortName'):
                        away_team_variants.append(away_team_obj['shortName'])
                    
                    # Переводы (русский, английский и другие)
                    home_translations = home_team_obj.get('fieldTranslations', {}).get('nameTranslation', {})
                    if home_translations:
                        for lang, translation in home_translations.items():
                            if translation:
                                home_team_variants.append(translation)
                    
                    away_translations = away_team_obj.get('fieldTranslations', {}).get('nameTranslation', {})
                    if away_translations:
                        for lang, translation in away_translations.items():
                            if translation:
                                away_team_variants.append(translation)
                    
                    # Нормализуем все варианты
                    home_sf_normalized_set = {self._normalize_team_name(v) for v in home_team_variants if v}
                    away_sf_normalized_set = {self._normalize_team_name(v) for v in away_team_variants if v}
                    
                    # Проверяем совпадение названий команд (оба варианта: прямой и обратный)
                    # Проверяем точное совпадение И частичное (если одно название содержит другое)
                    teams_match = False
                    for home_sf_norm in home_sf_normalized_set:
                        for away_sf_norm in away_sf_normalized_set:
                            # Точное совпадение (прямое или обратное)
                            exact_match = (
                                (home_normalized == home_sf_norm and away_normalized == away_sf_norm) or
                                (home_normalized == away_sf_norm and away_normalized == home_sf_norm)
                            )
                            
                            # Частичное совпадение: одно название является частью другого
                            # Используем минимальную длину 3 символа, чтобы избежать случайных совпадений
                            home_partial_match = (
                                (len(home_normalized) >= 3 and len(home_sf_norm) >= 3) and
                                (home_normalized in home_sf_norm or home_sf_norm in home_normalized)
                            )
                            away_partial_match = (
                                (len(away_normalized) >= 3 and len(away_sf_norm) >= 3) and
                                (away_normalized in away_sf_norm or away_sf_norm in away_normalized)
                            )
                            
                            # Обратное частичное совпадение
                            home_away_partial_match = (
                                (len(home_normalized) >= 3 and len(away_sf_norm) >= 3) and
                                (home_normalized in away_sf_norm or away_sf_norm in home_normalized)
                            )
                            away_home_partial_match = (
                                (len(away_normalized) >= 3 and len(home_sf_norm) >= 3) and
                                (away_normalized in home_sf_norm or home_sf_norm in away_normalized)
                            )
                            
                            # Совпадение, если обе команды совпадают (точно или частично) в одном порядке
                            if exact_match or (home_partial_match and away_partial_match):
                                teams_match = True
                                break
                            
                            # Или обратный порядок
                            if (home_normalized == away_sf_norm and away_normalized == home_sf_norm) or \
                               (home_away_partial_match and away_home_partial_match):
                                teams_match = True
                                break
                            
                        if teams_match:
                            break
                    
                    if not teams_match:
                        continue

                                        # Найдено совпадение по названиям команд (время не проверяем, так как данные уже отфильтрованы по дате)
                    print(f"[Football SofaScore] Найдено совпадение: {home_team_odds} vs {away_team_odds} -> event_id={event_id}")
                    return event_id
                    
                except Exception as e:
                    print(f"[Football SofaScore] Ошибка при обработке события SofaScore: {e}")
                    continue
            
            # Если не нашли совпадение, выводим детальную информацию для отладки
            if home_team_odds and away_team_odds:
                print(f"[Football SofaScore DEBUG] Не найдено совпадение для {home_team_odds} vs {away_team_odds}")
                print(f"[Football SofaScore DEBUG] Нормализованные: {home_normalized} vs {away_normalized}")
                print(f"[Football SofaScore DEBUG] Проверено событий SofaScore: {len(sofascore_events)}")
                # Выводим первые 3 события для примера
                for idx, event in enumerate(sofascore_events[:3]):
                    event_home = event.get('homeTeam', {}).get('name', 'N/A')
                    event_away = event.get('awayTeam', {}).get('name', 'N/A')
                    print(f"[Football SofaScore DEBUG]   Событие {idx+1}: {event_home} vs {event_away}")

            return None
            
        except Exception as e:
            print(f"[Football SofaScore ERROR] Ошибка сопоставления матча: {e}")
            return None

    def _match_sofascore_event_by_team_and_time(self, match: Dict, sofascore_events: List[Dict], time_tolerance_minutes: int = 5) -> Optional[Dict]:
        """
        Сопоставляет матч по одной команде (home или away) + времени.
        Используется как второй проход для матчей, которые не были найдены по двум командам.
        
        Args:
            match: Словарь с данными матча из БД (должен содержать: home_team, away_team, match_date, match_time)
            sofascore_events: Список событий из SofaScore
            time_tolerance_minutes: Допуск по времени в минутах (по умолчанию 5, плюс автоматическая поправка на часовой пояс)
            
        Returns:
            Словарь с ключами 'event_id', 'slug', 'startTimestamp' если найден, иначе None
        """
        try:
            home_team_odds = match.get('home_team', '')
            away_team_odds = match.get('away_team', '')
            match_date = match.get('match_date', '')
            match_time = match.get('match_time', '')
            
            if not all([home_team_odds, away_team_odds, match_date, match_time]):
                return None
            
            # Нормализуем названия команд
            home_normalized = self._normalize_team_name(home_team_odds)
            away_normalized = self._normalize_team_name(away_team_odds)
            
            # Парсим время матча из БД
            try:
                match_datetime = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M")
            except Exception as e:
                print(f"[Football SofaScore] Ошибка парсинга времени матча: {e}")
                return None
            
            # Ищем совпадение в событиях SofaScore
            for event in sofascore_events:
                try:
                    event_id = event.get('id')
                    if not event_id:
                        continue
                    
                    home_team_obj = event.get('homeTeam', {})
                    away_team_obj = event.get('awayTeam', {})
                    
                    if not home_team_obj or not away_team_obj:
                        continue
                    
                    # Проверяем время начала матча
                    start_timestamp = event.get('startTimestamp')
                    if not start_timestamp:
                        continue
                    
                    try:
                        event_datetime = datetime.fromtimestamp(start_timestamp)
                        time_diff_seconds = abs((match_datetime - event_datetime).total_seconds())
                        time_diff_minutes = time_diff_seconds / 60
                        
                        # Проверяем, что время совпадает в пределах ±5 минут + поправка на часовой пояс
                        # Если разница близка к целому количеству часов (в пределах ±5 минут),
                        # то это разница в часовых поясах, и мы ее учитываем
                        hours_diff = round(time_diff_minutes / 60)
                        minutes_remainder = abs(time_diff_minutes - hours_diff * 60)
                        
                        # Если остаток меньше 5 минут, значит это разница в часовых поясах
                        if minutes_remainder <= 5:
                            # Время совпадает с учетом часового пояса
                            pass  # Продолжаем проверку команд
                        elif time_diff_minutes <= 5:
                            # Время совпадает без учета часового пояса (тот же часовой пояс)
                            pass  # Продолжаем проверку команд
                        else:
                            # Время не совпадает
                            continue
                    except Exception as e:
                        continue
                    
                    # Для отладки: проверяем, есть ли хотя бы частичное совпадение по командам
                    # Это поможет понять, почему не находится совпадение
                    
                    # Получаем все возможные варианты названий команд из SofaScore
                    home_team_variants = []
                    away_team_variants = []
                    
                    if home_team_obj.get('name'):
                        home_team_variants.append(home_team_obj['name'])
                    if home_team_obj.get('shortName'):
                        home_team_variants.append(home_team_obj['shortName'])
                    home_translations = home_team_obj.get('fieldTranslations', {}).get('nameTranslation', {})
                    if home_translations:
                        for lang, translation in home_translations.items():
                            if translation:
                                home_team_variants.append(translation)
                    
                    if away_team_obj.get('name'):
                        away_team_variants.append(away_team_obj['name'])
                    if away_team_obj.get('shortName'):
                        away_team_variants.append(away_team_obj['shortName'])
                    away_translations = away_team_obj.get('fieldTranslations', {}).get('nameTranslation', {})
                    if away_translations:
                        for lang, translation in away_translations.items():
                            if translation:
                                away_team_variants.append(translation)
                    
                    # Нормализуем все варианты
                    home_sf_normalized_set = {self._normalize_team_name(v) for v in home_team_variants if v}
                    away_sf_normalized_set = {self._normalize_team_name(v) for v in away_team_variants if v}
                    
                    # Проверяем совпадение хотя бы одной команды (home или away) с учетом частичного совпадения
                    home_match = False
                    away_match = False
                    
                    # Проверяем home команду
                    for home_sf_norm in home_sf_normalized_set:
                        if (home_normalized == home_sf_norm or 
                            (len(home_normalized) >= 3 and len(home_sf_norm) >= 3 and 
                             (home_normalized in home_sf_norm or home_sf_norm in home_normalized))):
                            home_match = True
                            break
                    
                    # Проверяем away команду
                    for away_sf_norm in away_sf_normalized_set:
                        if (away_normalized == away_sf_norm or 
                            (len(away_normalized) >= 3 and len(away_sf_norm) >= 3 and 
                             (away_normalized in away_sf_norm or away_sf_norm in away_normalized))):
                            away_match = True
                            break
                    
                    # Проверяем обратный порядок (home vs away или away vs home)
                    if not home_match:
                        for away_sf_norm in away_sf_normalized_set:
                            if (home_normalized == away_sf_norm or 
                                (len(home_normalized) >= 3 and len(away_sf_norm) >= 3 and 
                                 (home_normalized in away_sf_norm or away_sf_norm in home_normalized))):
                                home_match = True
                                break
                    
                    if not away_match:
                        for home_sf_norm in home_sf_normalized_set:
                            if (away_normalized == home_sf_norm or 
                                (len(away_normalized) >= 3 and len(home_sf_norm) >= 3 and 
                                 (away_normalized in home_sf_norm or home_sf_norm in away_normalized))):
                                away_match = True
                                break
                    
                    # Если хотя бы одна команда совпадает и время совпадает, считаем это совпадением
                    if home_match or away_match:
                        print(f"[Football SofaScore] Найдено совпадение по команде+времени: {home_team_odds} vs {away_team_odds} -> event_id={event_id} (home_match={home_match}, away_match={away_match})")
                        # Возвращаем словарь с event_id и данными для сохранения
                        return {
                            'event_id': event_id,
                            'slug': event.get('slug', ''),
                            'startTimestamp': event.get('startTimestamp')
                        }
                    
                except Exception as e:
                    continue
            
            # Если не нашли совпадение, выводим детальную информацию для отладки
            if home_team_odds and away_team_odds:
                print(f"[Football SofaScore DEBUG 2nd pass] Не найдено совпадение по команде+времени для {home_team_odds} vs {away_team_odds}")
                print(f"[Football SofaScore DEBUG 2nd pass] Нормализованные: {home_normalized} vs {away_normalized}")
                print(f"[Football SofaScore DEBUG 2nd pass] Время матча из БД: {match_datetime}")
                print(f"[Football SofaScore DEBUG 2nd pass] Проверено событий SofaScore: {len(sofascore_events)}")
                
                # Ищем события с похожим временем (в пределах 60 минут)
                similar_time_events = []
                for event in sofascore_events:
                    event_time = event.get('startTimestamp')
                    if event_time:
                        event_dt = datetime.fromtimestamp(event_time)
                        time_diff = abs((match_datetime - event_dt).total_seconds()) / 60
                        if time_diff <= 60:  # В пределах часа
                            event_home = event.get('homeTeam', {}).get('name', 'N/A')
                            event_away = event.get('awayTeam', {}).get('name', 'N/A')
                            similar_time_events.append((event_home, event_away, event_dt, time_diff))
                
                if similar_time_events:
                    print(f"[Football SofaScore DEBUG 2nd pass] Найдено {len(similar_time_events)} событий с похожим временем (в пределах 60 мин):")
                    for idx, (eh, ea, edt, tdiff) in enumerate(similar_time_events[:5]):  # Показываем первые 5
                        print(f"[Football SofaScore DEBUG 2nd pass]   {idx+1}. {eh} vs {ea}, время: {edt}, разница: {tdiff:.1f} мин")
                else:
                    print(f"[Football SofaScore DEBUG 2nd pass] Нет событий с похожим временем (в пределах 60 мин)")
                    # Выводим первые 3 события для примера
                    for idx, event in enumerate(sofascore_events[:3]):
                        event_home = event.get('homeTeam', {}).get('name', 'N/A')
                        event_away = event.get('awayTeam', {}).get('name', 'N/A')
                        event_time = event.get('startTimestamp')
                        if event_time:
                            event_dt = datetime.fromtimestamp(event_time)
                            time_diff = abs((match_datetime - event_dt).total_seconds()) / 60
                            print(f"[Football SofaScore DEBUG 2nd pass]   Событие {idx+1}: {event_home} vs {event_away}, время: {event_dt}, разница: {time_diff:.1f} мин")
                        else:
                            print(f"[Football SofaScore DEBUG 2nd pass]   Событие {idx+1}: {event_home} vs {event_away}, время: N/A")
            
            return None
            
        except Exception as e:
            print(f"[Football SofaScore ERROR] Ошибка сопоставления по команде+времени: {e}")
            return None

    def update_sofascore_ids(self) -> Dict[str, int]:
        """
        Обновляет sofascore_event_id для матчей, у которых он отсутствует.
        Запрашивает события из SofaScore для дат матчей без sofascore_event_id.
        
        Returns:
            Словарь со статистикой: {'updated': int, 'failed': int}
        """
        stats = {
            'updated': 0,
            'failed': 0,
            'dates_processed': 0
        }
        
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            
            # Получаем все матчи без sofascore_event_id, сгруппированные по дате
            cursor.execute("""
                SELECT DISTINCT match_date 
                FROM matches 
                WHERE sofascore_event_id IS NULL 
                AND status IN ('scheduled', 'in_progress')
                ORDER BY match_date
            """)
            
            dates_to_process = [row[0] for row in cursor.fetchall()]
            
            if not dates_to_process:
                print("[Football SofaScore] Нет матчей без sofascore_event_id для обновления")
                conn.close()
                return stats
            
            print(f"[Football SofaScore] Найдено {len(dates_to_process)} дат для обработки")
            
            # Обрабатываем каждую дату
            for date_str in dates_to_process:
                try:
                    # Получаем события из SofaScore для этой даты
                    events = self._fetch_sofascore_events(date_str)
                    if not events:
                        stats['failed'] += 1
                        continue
                    
                    stats['dates_processed'] += 1
                    
                    # Получаем все матчи на эту дату без sofascore_event_id
                    cursor.execute("""
                        SELECT * FROM matches 
                        WHERE match_date = ? 
                        AND sofascore_event_id IS NULL
                        AND status IN ('scheduled', 'in_progress')
                                        """, (date_str,))

                    matches = cursor.fetchall()
                    print(f"[Football SofaScore] Обрабатываем {len(matches)} матчей на дату {date_str}")

                    # Первый проход: сопоставляем по двум командам
                    unmatched_matches = []
                    for match_row in matches:
                        match_dict = dict(match_row)
                        event_id = self._match_sofascore_event(match_dict, events)

                        if event_id:
                            # Обновляем sofascore_event_id в БД
                            cursor.execute("""
                                UPDATE matches
                                SET sofascore_event_id = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (event_id, match_dict['id']))

                            stats['updated'] += 1
                            print(f"[Football SofaScore] Обновлен sofascore_event_id={event_id} для матча {match_dict['home_team']} vs {match_dict['away_team']}")
                        else:
                            # Сохраняем для второго прохода
                            unmatched_matches.append(match_dict)

                    conn.commit()

                    # Второй проход: сопоставляем по одной команде + времени
                    if unmatched_matches:
                        print(f"[Football SofaScore] Второй проход: ищем {len(unmatched_matches)} ненайденных матчей по команде+времени")
                        for match_dict in unmatched_matches:
                            event_data = self._match_sofascore_event_by_team_and_time(match_dict, events)

                            if event_data:
                                event_id = event_data['event_id']
                                # Формируем JSON для сохранения в sofascore_join
                                sofascore_join_data = {
                                    'slug': event_data.get('slug', ''),
                                    'startTimestamp': event_data.get('startTimestamp')
                                }
                                sofascore_join_json = json.dumps(sofascore_join_data, ensure_ascii=False)
                                
                                # Обновляем sofascore_event_id и sofascore_join в БД
                                cursor.execute("""
                                    UPDATE matches
                                    SET sofascore_event_id = ?, sofascore_join = ?, updated_at = CURRENT_TIMESTAMP
                                    WHERE id = ?
                                """, (event_id, sofascore_join_json, match_dict['id']))

                                stats['updated'] += 1
                                stats['failed'] -= 1  # Уменьшаем счетчик failed, так как теперь нашли
                                print(f"[Football SofaScore] Обновлен sofascore_event_id={event_id} для матча {match_dict['home_team']} vs {match_dict['away_team']} (второй проход)")
                            else:
                                stats['failed'] += 1
                                print(f"[Football SofaScore] Не найдено совпадение для {match_dict['home_team']} vs {match_dict['away_team']} ({match_dict['match_date']} {match_dict['match_time']})")

                    conn.commit()
                    
                    # Задержка между запросами к SofaScore (минимум 2-3 секунды для избежания блокировки)
                    # SofaScore может заблокировать IP при >5 запросов/сек или при слишком частых запросах
                    # Используем 2.5 секунды для безопасности
                    time.sleep(2.5)
                    
                except Exception as e:
                    print(f"[Football SofaScore ERROR] Ошибка обработки даты {date_str}: {e}")
                    stats['failed'] += 1
                    continue
            
            conn.close()
            print(f"[Football SofaScore] Обновление завершено: обновлено={stats['updated']}, не найдено={stats['failed']}, дат обработано={stats['dates_processed']}")
            
        except Exception as e:
            print(f"[Football SofaScore ERROR] Критическая ошибка при обновлении sofascore_event_id: {e}")
            import traceback
            traceback.print_exc()
        
        return stats

    def sync_matches(self, leagues: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Синхронизирует матчи из API с БД.
        Собирает ВСЕ матчи из указанных лиг, независимо от даты.
        Обновляет существующие матчи, удаляет матчи с коэффициентами > 1.30.
        
        Args:
            leagues: Список ключей лиг для сбора (например, ['soccer_epl', 'soccer_spain_la_liga']).
                     Если None, используется список из self.leagues.
        
        Returns:
            Словарь со статистикой: {'added': int, 'updated': int, 'deleted': int, ...}
        """
        # Используем переданный список лиг или список по умолчанию
        leagues_to_process = leagues if leagues is not None else self.leagues
        
        print(f"[Football] Начинаем синхронизацию матчей из {len(leagues_to_process)} лиг")
        
        stats = {
            'added': 0,
            'updated': 0,
            'deleted': 0,
            'skipped_no_fav': 0,
            'skipped_past': 0,
            'leagues_processed': 0,
            'leagues_failed': 0
        }
        
        now = datetime.now()
        fixture_ids_from_api = set()  # Для отслеживания матчей из API

        # Обрабатываем каждую лигу
        for league_key in leagues_to_process:
            try:
                print(f"[Football] Обрабатываем лигу: {league_key}")
                
                params = {
                    "regions": "eu",
                    "markets": "h2h",
                    "oddsFormat": "decimal"
                }
                
                                # Запрашиваем матчи для конкретной лиги
                data = self._make_api_request(f"/sports/{league_key}/odds", params)

                if not data:
                    print(f"[Football] Нет матчей для лиги {league_key} или ошибка запроса")
                    stats['leagues_failed'] += 1
                    continue
                
                print(f"[Football] Получено {len(data)} матчей из лиги {league_key}")
                stats['leagues_processed'] += 1
                
                for match_data in data:
                    fixture_id = match_data.get('id')
                    if not fixture_id:
                        continue
                    
                    fixture_ids_from_api.add(fixture_id)
                    
                    # Проверяем дату матча (пропускаем только матчи в прошлом)      
                    commence_time = match_data.get('commence_time')
                    if not commence_time:
                        continue

                    # Парсим время начала матча
                    try:
                        match_dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                        match_dt = match_dt.replace(tzinfo=None)
                    except Exception as e:
                        print(f"[Football] Ошибка парсинга времени матча: {e}")     
                        continue

                    # Пропускаем матчи в прошлом
                    if match_dt < now:
                        stats['skipped_past'] += 1
                        continue

                    # Определяем фаворита
                    fav_info = self._determine_favorite(match_data)
                    if not fav_info:
                        stats['skipped_no_fav'] += 1
                        print(f"[Football] Не удалось определить фаворита для матча {fixture_id} ({match_data.get('home_team')} vs {match_data.get('away_team')})")
                        continue

                    # Проверяем, существует ли матч в БД
                    match_exists = self._match_exists(fixture_id)

                    # Если коэффициент <= 1.30 - добавляем/обновляем
                    if fav_info['odds'] <= 1.30:
                        if match_exists:
                            # Обновляем существующий матч
                            success = self._update_match(fixture_id, fav_info, match_data)
                            if success:
                                stats['updated'] += 1
                                print(f"[Football] Обновлен матч {match_data.get('home_team')} vs {match_data.get('away_team')}, новый кэф: {fav_info['odds']}")
                        else:
                            # Добавляем новый матч
                            success = self._save_match(match_data, fav_info)
                            if success:
                                stats['added'] += 1
                                print(f"[Football] Добавлен матч {match_data.get('home_team')} vs {match_data.get('away_team')}, кэф: {fav_info['odds']}")
                    else:
                        # Коэффициент > 1.30 - удаляем из БД, если существует
                        # НО: не удаляем, если bet уже установлен (ставка сделана)
                        if match_exists:
                            # Проверяем, есть ли у матча значение bet
                            bet_value = self._get_match_bet_value(fixture_id)
                            if bet_value is not None:
                                # У матча уже есть ставка (bet не null), не удаляем
                                print(f"[Football] Матч {match_data.get('home_team')} vs {match_data.get('away_team')} имеет bet={bet_value}, не удаляем даже при кэф {fav_info['odds']} > 1.30")
                                continue
                            
                            # bet не установлен, можно удалить
                            success = self._delete_match(fixture_id)
                            if success:
                                stats['deleted'] += 1
                                print(f"[Football] Удален матч {match_data.get('home_team')} vs {match_data.get('away_team')}, кэф {fav_info['odds']} > 1.30")
                
            except Exception as e:
                print(f"[Football ERROR] Ошибка при обработке лиги {league_key}: {e}")
                stats['leagues_failed'] += 1
                continue

        # Удаляем матчи из БД, которых больше нет в API (опционально, если нужно)
        # Пока не реализовано, так как API может не возвращать все матчи

        print(f"[Football] Синхронизация завершена: лиг обработано={stats['leagues_processed']}, лиг с ошибками={stats['leagues_failed']}, добавлено={stats['added']}, обновлено={stats['updated']}, удалено={stats['deleted']}, пропущено (нет фаворита)={stats['skipped_no_fav']}, пропущено (прошлое)={stats['skipped_past']}")
        
        # Обновляем sofascore_event_id для матчей без него
        print("[Football] Начинаем обновление sofascore_event_id...")
        sofascore_stats = self.update_sofascore_ids()
        stats['sofascore_updated'] = sofascore_stats['updated']
        stats['sofascore_failed'] = sofascore_stats['failed']
        stats['sofascore_dates_processed'] = sofascore_stats['dates_processed']
        
        return stats

    def collect_tomorrow_matches(self) -> int:
        """
        Алиас для sync_matches для обратной совместимости.
        Теперь использует sync_matches.
        
        Returns:
            Количество добавленных матчей
        """
        stats = self.sync_matches()
        return stats['added']

    def _determine_favorite(self, match_data: Dict) -> Optional[Dict]:
        """
        Определяет фаворита по коэффициентам из The Odds API.
        
        Args:
            match_data: Данные матча от API (уже содержат bookmakers)
            
        Returns:
            Словарь с информацией о фаворите: {'team', 'is_home', 'odds'} или None
        """
        try:
            home_team = match_data.get('home_team')
            away_team = match_data.get('away_team')
            
            if not home_team or not away_team:
                print("[Football] Нет названий команд в данных матча")
                return None
            
            # Получаем коэффициенты из bookmakers
            bookmakers = match_data.get('bookmakers', [])
            if not bookmakers:
                print("[Football] Нет букмекеров в данных")
                return None
            
            # Собираем все коэффициенты для каждой команды по всем букмекерам
            home_odds = []
            away_odds = []
            
            for bookmaker in bookmakers:
                markets = bookmaker.get('markets', [])
                for market in markets:
                    if market.get('key') != 'h2h':
                        continue
                    
                    outcomes = market.get('outcomes', [])
                    for outcome in outcomes:
                        name = outcome.get('name')
                        price = outcome.get('price')
                        
                        if not price or not name:
                            continue
                        
                        # Пропускаем Draw
                        if name.lower() == 'draw':
                            continue
                        
                        # Определяем команду по имени
                        if name == home_team:
                            home_odds.append(float(price))
                        elif name == away_team:
                            away_odds.append(float(price))
            
            if not home_odds or not away_odds:
                print(f"[Football] Не удалось получить коэффициенты для команд")
                return None
            
            # Берем минимальный коэффициент для каждой команды
            min_home_odd = min(home_odds)
            min_away_odd = min(away_odds)
            
            # Определяем фаворита (меньший коэффициент)
            if min_home_odd <= min_away_odd:
                fav_team = home_team
                fav_is_home = True
                fav_odd = min_home_odd
            else:
                fav_team = away_team
                fav_is_home = False
                fav_odd = min_away_odd
            
            print(f"[Football] Фаворит: {fav_team} (кэф: {fav_odd})")
            
            return {
                'team': fav_team,
                'is_home': fav_is_home,
                'odds': fav_odd
            }
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка определения фаворита: {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def _save_match(self, match_data: Dict, fav_info: Dict) -> bool:
        """
        Сохраняет матч в БД.
        
        Args:
            match_data: Данные матча от The Odds API
            fav_info: Информация о фаворите
            
        Returns:
            True если успешно, False если ошибка
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            
            # The Odds API использует "id" вместо "fixture_id"
            event_id = match_data.get('id')
            
            # Проверяем, не существует ли уже матч
            cursor.execute("SELECT id FROM matches WHERE fixture_id = ?", (event_id,))
            if cursor.fetchone():
                print(f"[Football] Матч {event_id} уже существует, пропускаем")
                return False
            
            # Извлекаем данные
            home_team = match_data.get('home_team')
            away_team = match_data.get('away_team')
            
            if not home_team or not away_team:
                print(f"[Football] Нет названий команд для матча {event_id}")
                return False
            
            # Дата и время матча
            commence_time = match_data.get('commence_time')
            if commence_time:
                dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                # Приводим к локальному времени
                dt = dt.replace(tzinfo=None)
                match_date = dt.strftime('%Y-%m-%d')
                match_time = dt.strftime('%H:%M')
            else:
                print(f"[Football] Нет даты для матча {event_id}")
                return False
            
            # Сохраняем
            cursor.execute("""
                INSERT INTO matches 
                (fixture_id, home_team, away_team, fav, fav_team_id, 
                 match_date, match_time, initial_odds, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,  # fixture_id = event_id из The Odds API
                home_team,
                away_team,
                fav_info['team'],
                1 if fav_info['is_home'] else 0,  # fav_team_id: 1=home, 0=away
                match_date,
                match_time,
                                fav_info['odds'],
                'scheduled'
            ))

            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка сохранения матча: {e}")
            return False
        except Exception as e:
            print(f"[Football ERROR] Неожиданная ошибка: {e}")
            import traceback
            print(traceback.format_exc())
            return False
        finally:
            if conn:
                conn.close()

    def _match_exists(self, fixture_id: str) -> bool:
        """
        Проверяет, существует ли матч в БД.

        Args:
            fixture_id: ID матча из API

        Returns:
            True если матч существует, False если нет
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM matches WHERE fixture_id = ?", (fixture_id,))
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка проверки существования матча: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _get_match_bet_value(self, fixture_id: str) -> Optional[int]:
        """
        Получает значение bet для матча.

        Args:
            fixture_id: ID матча из API

        Returns:
            Значение bet или None, если матч не найден или bet не установлен
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT bet FROM matches WHERE fixture_id = ?", (fixture_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                return row[0]
            return None
        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка получения bet для матча: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _update_match(self, fixture_id: str, fav_info: Dict, match_data: Dict) -> bool:
        """
        Обновляет коэффициент существующего матча.

        Args:
            fixture_id: ID матча из API
            fav_info: Информация о фаворите
            match_data: Данные матча от API

        Returns:
            True если успешно, False если ошибка
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()

            # Обновляем только коэффициент, фаворита и время обновления
            cursor.execute("""
                UPDATE matches 
                SET fav = ?, fav_team_id = ?, initial_odds = ?, updated_at = CURRENT_TIMESTAMP
                WHERE fixture_id = ?
            """, (
                fav_info['team'],
                1 if fav_info['is_home'] else 0,
                fav_info['odds'],
                fixture_id
            ))

            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка обновления матча: {e}")
            return False
        except Exception as e:
            print(f"[Football ERROR] Неожиданная ошибка при обновлении: {e}")
            import traceback
            print(traceback.format_exc())
            return False
        finally:
            if conn:
                conn.close()

    def _delete_match(self, fixture_id: str) -> bool:
        """
        Удаляет матч из БД.

        Args:
            fixture_id: ID матча из API

        Returns:
            True если успешно, False если ошибка
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()

            cursor.execute("DELETE FROM matches WHERE fixture_id = ?", (fixture_id,))
            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка удаления матча: {e}")
            return False
        except Exception as e:
            print(f"[Football ERROR] Неожиданная ошибка при удалении: {e}")
            import traceback
            print(traceback.format_exc())
            return False
        finally:
            if conn:
                conn.close()

    def check_matches_and_collect(self):
        """
        Проверяет активные матчи и собирает статистику на 60-й и 300-й минутах.
        Вызывается каждые 5 минут.
        """
        print("[Football] Проверка матчей и сбор статистики")
        
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            
            # Получаем матчи в статусе scheduled или in_progress
            cursor.execute("""
                SELECT * FROM matches 
                WHERE status IN ('scheduled', 'in_progress')
                ORDER BY match_date, match_time
            """)
            
            matches = cursor.fetchall()
            print(f"[Football] Найдено {len(matches)} активных матчей")
            
            if not matches:
                print("[Football] Нет активных матчей для проверки")
                conn.close()
                return
            
            # Проверяем каждый матч
            for match in matches:
                match_id = match['id']
                fixture_id = match['fixture_id']
                match_datetime_str = f"{match['match_date']} {match['match_time']}"
                
                try:
                    # Парсим дату и время
                    match_datetime = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
                    now = datetime.now()
                    
                    # Вычисляем разницу во времени
                    time_diff = now - match_datetime
                    minutes_diff = time_diff.total_seconds() / 60
                    
                    # Проверяем что матч уже начался
                    if minutes_diff < 0:
                        continue  # Матч еще не начался
                    
                    print(f"[Football] Матч {fixture_id}: прошло {minutes_diff:.1f} минут")
                    
                    # Обновляем статус на in_progress если нужно
                    if match['status'] == 'scheduled':
                        cursor.execute(
                            "UPDATE matches SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (match_id,)
                        )
                        conn.commit()
                    
                    # Проверяем 60-я минута (с допуском ±5 минут)
                    if 55 <= minutes_diff <= 65 and match['stats_60min'] is None:
                        print(f"[Football] Собираем статистику на 60-й минуте для fixture {fixture_id}")
                        self._collect_60min_stats(match)
                    
                    # Проверяем 300-я минута (окончание матча + допуск ±10 минут)
                    if 290 <= minutes_diff <= 310:
                        print(f"[Football] Собираем финальный результат для fixture {fixture_id}")
                        self._collect_final_result(match)
                    
                except Exception as e:
                    print(f"[Football ERROR] Ошибка проверки матча {fixture_id}: {e}")
                    continue
            
            conn.close()
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка проверки матчей: {e}")
            import traceback
            print(traceback.format_exc())
        finally:
            if conn:
                conn.close()

    def _collect_60min_stats(self, match: sqlite3.Row):
        """
        Собирает статистику на 60-й минуте.
        
        Args:
            match: Запись матча из БД
        """
        try:
            fixture_id = match['fixture_id']
            
            # Получаем статистику от API-Football
            params = {"fixture": str(fixture_id)}
            data = self._make_api_request("/fixtures/statistics", params)
            
            if not data or not data.get('response'):
                print(f"[Football] Не удалось получить статистику для fixture {fixture_id}")
                return
            
            stats_data = data['response'][0] if data['response'] else None
            if not stats_data:
                print(f"[Football] Пустой ответ статистики для fixture {fixture_id}")
                return
            
            # Парсим статистику
            stats = self._parse_statistics(stats_data)
            
            # Получаем счёт на данный момент
            fixture_data = self._make_api_request("/fixtures", {"id": str(fixture_id)})
            if fixture_data and fixture_data.get('response'):
                fixture = fixture_data['response'][0].get('fixture', {}).get('status', {})
                goals_home = fixture.get('halftime', {}).get('home') or fixture.get('fulltime', {}).get('home') or 0
                goals_away = fixture.get('halftime', {}).get('away') or fixture.get('fulltime', {}).get('away') or 0
                
                stats['score'] = {'home': goals_home, 'away': goals_away}
            
            # Проверяем условия и записываем bet
            bet_value = self._calculate_bet(match, stats)
            
            # Сохраняем в БД
            conn = get_football_db_connection()
            cursor = conn.cursor()
            
            stats_json = json.dumps(stats)
            cursor.execute("""
                UPDATE matches 
                SET stats_60min = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (stats_json, match['id']))
            
            conn.commit()
            conn.close()
            
            print(f"[Football] Статистика на 60-й минуте сохранена для fixture {fixture_id}, bet: {bet_value}")
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка сбора статистики 60min: {e}")
            import traceback
            print(traceback.format_exc())

    def _collect_final_result(self, match: sqlite3.Row):
        """
        Собирает финальный результат матча.
        
        Args:
            match: Запись матча из БД
        """
        try:
            fixture_id = match['fixture_id']
            
            # Получаем финальный счёт
            params = {"id": str(fixture_id)}
            data = self._make_api_request("/fixtures", params)
            
            if not data or not data.get('response'):
                print(f"[Football] Не удалось получить результат для fixture {fixture_id}")
                return
            
            fixture = data['response'][0].get('fixture', {})
            score = fixture.get('score', {}).get('fulltime', {})
            
            goals_home = score.get('home', 0)
            goals_away = score.get('away', 0)
            
            # Определяем кто выиграл
            # fav_team_id - это ID фаворита (home_team_id или away_team_id)
            fav_team_id = match['fav_team_id']
            home_team_id = match['id']  # Это неправильно, нужно получить из fixture
            
            # TODO: Правильно определить кто выиграл сравнив ID
            # Пока просто проверяем счёт
            if goals_home > goals_away:
                winner_is_home = True
            elif goals_away > goals_home:
                winner_is_home = False
            else:
                winner_is_home = None  # Ничья
            
            # Сохраняем
            conn = get_football_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE matches 
                SET final_score_home = ?, final_score_away = ?, 
                    status = 'finished', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (goals_home, goals_away, match['id']))
            
            conn.commit()
            conn.close()
            
            print(f"[Football] Финальный результат сохранен для fixture {fixture_id}: {goals_home}-{goals_away}")
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка сбора финального результата: {e}")
            import traceback
            print(traceback.format_exc())

    def _parse_statistics(self, stats_data: Dict) -> Dict:
        """
        Парсит статистику из API-Football.
        
        Args:
            stats_data: Сырые данные статистики
            
        Returns:
            Словарь с отпарсенной статистикой
        """
        stats = {}
        
        try:
            # API-Football возвращает статистику для каждой команды
            for team_stats in stats_data.get('statistics', []):
                team = team_stats.get('team', {}).get('name', '')
                
                # Парсим метрики
                for stat in team_stats.get('statistics', []):
                    stat_type = stat.get('type', '')
                    stat_value = stat.get('value')
                    
                    if stat_type == 'Ball Possession':
                        stats[team.lower()] = {'possession': stat_value}
                    elif stat_type == 'Shots on Goal':
                        if team.lower() not in stats:
                            stats[team.lower()] = {}
                        stats[team.lower()]['shots_on_target'] = stat_value
                    elif stat_type == 'expected_goals':
                        if team.lower() not in stats:
                            stats[team.lower()] = {}
                        stats[team.lower()]['xG'] = stat_value
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка парсинга статистики: {e}")
        
        return stats

    def _calculate_bet(self, match: sqlite3.Row, stats: Dict) -> int:
        """
        Рассчитывает значение bet на основе условий.
        
        Args:
            match: Запись матча
            stats: Статистика на 60-й минуте
            
        Returns:
            1 если условия выполнены, 0 если нет (или 1 если лимит исчерпан)
        """
        try:
            # TODO: Реализовать полную логику проверки условий
            
            return 0
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка расчета bet: {e}")
            return 0


# === Функции для APScheduler ===

def collect_tomorrow_matches_task():
    """Задача для планировщика - сбор матчей на завтра."""
    try:
        manager = get_manager()
        count = manager.collect_tomorrow_matches()
        print(f"[Football] Задача сбора завершена: {count} матчей")
        return count
    except Exception as e:
        print(f"[Football] Ошибка в задаче сбора: {e}")
        import traceback
        print(traceback.format_exc())
        return 0


def check_matches_and_collect_task():
    """Задача для планировщика - проверка матчей и сбор статистики."""
    try:
        manager = get_manager()
        manager.check_matches_and_collect()
    except Exception as e:
        print(f"[Football] Ошибка в задаче проверки: {e}")
        import traceback
        print(traceback.format_exc())


def get_all_matches() -> List[Dict[str, Any]]:
    """
    Получает все матчи для UI.

    Returns:
        Список матчей
    """
    conn = None
    try:
        conn = get_football_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM matches
            ORDER BY match_date DESC, match_time DESC
        """)

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    except sqlite3.Error as e:
        print(f"[Football ERROR] Ошибка получения матчей: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_api_limits() -> Dict[str, Any]:
    """
    Получает текущие лимиты API для UI.

    Returns:
        Словарь с информацией о лимитах API
    """
    try:
        manager = get_manager()
        return {
            'requests_remaining': manager.requests_remaining,
            'requests_used': manager.requests_used,
            'requests_last_cost': manager.requests_last_cost
        }
    except Exception as e:
        print(f"[Football ERROR] Ошибка получения лимитов API: {e}")
        return {
            'requests_remaining': None,
            'requests_used': None,
            'requests_last_cost': None
        }

