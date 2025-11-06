import sqlite3
import requests
import json
import time
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
import os
import re
from dotenv import load_dotenv



from config import FOOTBALL_DB_FILE
from workflow_model_config import get_model_for_operation

# Попытка импортировать telegram_notifier (может отсутствовать)
try:
    from telegram_notifier import telegram_notifier
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("[Football] Telegram notifier не доступен")

load_dotenv()

FOOTBALL_DATABASE_FILE = str(FOOTBALL_DB_FILE)
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
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
    #"soccer_epl",                    # Английская Премьер-лига
    #"soccer_uefa_champs_league",     # Лига Чемпионов
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
        
                # --- Проверка и добавление поля last_odds ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'last_odds' not in columns:
            print("[FootballDB] Adding 'last_odds' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN last_odds REAL")
            conn.commit()
            print("[FootballDB] Column 'last_odds' added successfully.")
        else:
            print("[FootballDB] Column 'last_odds' already exists.")
        
        # --- Проверка и добавление поля bet_ai ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'bet_ai' not in columns:
            print("[FootballDB] Adding 'bet_ai' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN bet_ai TEXT")
            conn.commit()
            print("[FootballDB] Column 'bet_ai' added successfully.")
        else:
            print("[FootballDB] Column 'bet_ai' already exists.")
        
        # --- Проверка и добавление поля bet_ai_reason ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'bet_ai_reason' not in columns:
            print("[FootballDB] Adding 'bet_ai_reason' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN bet_ai_reason TEXT")
            conn.commit()
            print("[FootballDB] Column 'bet_ai_reason' added successfully.")
        else:
            print("[FootballDB] Column 'bet_ai_reason' already exists.")
        
        # --- Проверка и добавление поля bet_ai_odds ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'bet_ai_odds' not in columns:
            print("[FootballDB] Adding 'bet_ai_odds' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN bet_ai_odds REAL")
            conn.commit()
            print("[FootballDB] Column 'bet_ai_odds' added successfully.")
        else:
            print("[FootballDB] Column 'bet_ai_odds' already exists.")
        
        # --- Проверка и добавление поля live_odds ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'live_odds' not in columns:
            print("[FootballDB] Adding 'live_odds' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN live_odds REAL")
            conn.commit()
            print("[FootballDB] Column 'live_odds' added successfully.")
        else:
            print("[FootballDB] Column 'live_odds' already exists.")
        
        # --- Проверка и добавление полей для коэффициентов исходов (для расчета bet_ai_odds) ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        for odds_field in ['live_odds_1', 'live_odds_x', 'live_odds_2']:
            if odds_field not in columns:
                print(f"[FootballDB] Adding '{odds_field}' column to 'matches' table...")
                cursor.execute(f"ALTER TABLE matches ADD COLUMN {odds_field} REAL")
                conn.commit()
                print(f"[FootballDB] Column '{odds_field}' added successfully.")
            else:
                print(f"[FootballDB] Column '{odds_field}' already exists.")
        
        # --- Проверка и добавление поля sport_key ---
        cursor.execute("PRAGMA table_info(matches)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'sport_key' not in columns:
            print("[FootballDB] Adding 'sport_key' column to 'matches' table...")
            cursor.execute("ALTER TABLE matches ADD COLUMN sport_key TEXT")
            conn.commit()
            print("[FootballDB] Column 'sport_key' added successfully.")
        else:
            print("[FootballDB] Column 'sport_key' already exists.")
        
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
        
        # OpenRouter API для ИИ-прогнозов
        self.openrouter_api_key = OPENROUTER_API_KEY
        self.openrouter_api_url = "https://openrouter.ai/api/v1"
        
        # Модели для футбольных прогнозов из конфигурации
        self.ai_primary_model = get_model_for_operation('football_predict', 'primary')
        self.ai_fallback_model1 = get_model_for_operation('football_predict', 'fallback_level1')
        self.ai_fallback_model2 = get_model_for_operation('football_predict', 'fallback_level2')
        
        # Модели для анализа риска ставки
        self.risk_analysis_primary = get_model_for_operation('bet_risk_analysis', 'primary')
        self.risk_analysis_fallback1 = get_model_for_operation('bet_risk_analysis', 'fallback_level1')
        self.risk_analysis_fallback2 = get_model_for_operation('bet_risk_analysis', 'fallback_level2')

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
            
            # Парсим время матча из БД (в UTC, но без tzinfo)
            try:
                match_datetime_naive = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M")
                # Добавляем UTC часовой пояс, так как время в БД сохранено в UTC
                match_datetime = match_datetime_naive.replace(tzinfo=timezone.utc)
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
                        # startTimestamp от SofaScore - это Unix timestamp в UTC
                        event_datetime = datetime.fromtimestamp(start_timestamp, tz=timezone.utc)
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
                        # startTimestamp от SofaScore - это Unix timestamp в UTC
                        event_dt = datetime.fromtimestamp(event_time, tz=timezone.utc)
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
                            # startTimestamp от SofaScore - это Unix timestamp в UTC
                            event_dt = datetime.fromtimestamp(event_time, tz=timezone.utc)
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

                    # Извлекаем коэффициенты 1, X, 2 для всех матчей
                    odds_1_x_2 = self._extract_odds_1_x_2(match_data)
                    
                    # Определяем фаворита
                    fav_info = self._determine_favorite(match_data)
                    
                    # Проверяем, существует ли матч в БД
                    match_exists = self._match_exists(fixture_id)
                    
                    # Определяем, есть ли фаворит с кэфом <= 1.30
                    has_favorite = fav_info is not None and fav_info['odds'] <= 1.30
                    
                    if has_favorite:
                        # Матч с фаворитом - заполняем все поля
                        if match_exists:
                            # Проверяем статус перед обновлением - не обновляем завершенные матчи
                            match_status = self._get_match_status(fixture_id)
                            if match_status == 'finished':
                                print(f"[Football] Пропущен матч с фаворитом {match_data.get('home_team')} vs {match_data.get('away_team')} - матч завершен")
                                continue
                            # Обновляем существующий матч
                            success = self._update_match(fixture_id, fav_info, match_data, odds_1_x_2)
                            if success:
                                stats['updated'] += 1
                                print(f"[Football] Обновлен матч с фаворитом {match_data.get('home_team')} vs {match_data.get('away_team')}, кэф: {fav_info['odds']}")
                        else:
                            # Добавляем новый матч
                            success = self._save_match(match_data, fav_info, odds_1_x_2)
                            if success:
                                stats['added'] += 1
                                print(f"[Football] Добавлен матч с фаворитом {match_data.get('home_team')} vs {match_data.get('away_team')}, кэф: {fav_info['odds']}")
                    else:
                        # Матч без фаворита или с кэфом > 1.30 - заполняем только базовые поля
                        if match_exists:
                            # Проверяем статус перед обновлением - не обновляем завершенные матчи
                            match_status = self._get_match_status(fixture_id)
                            if match_status == 'finished':
                                print(f"[Football] Пропущен матч без фаворита {match_data.get('home_team')} vs {match_data.get('away_team')} - матч завершен")
                                continue
                            # Обновляем существующий матч (без fav)
                            success = self._update_match_without_fav(fixture_id, match_data, odds_1_x_2)
                            if success:
                                stats['updated'] += 1
                                print(f"[Football] Обновлен матч без фаворита {match_data.get('home_team')} vs {match_data.get('away_team')}")
                        else:
                            # Добавляем новый матч (без fav)
                            success = self._save_match_without_fav(match_data, odds_1_x_2)
                            if success:
                                stats['added'] += 1
                                print(f"[Football] Добавлен матч без фаворита {match_data.get('home_team')} vs {match_data.get('away_team')}")
                
            except Exception as e:
                print(f"[Football ERROR] Ошибка при обработке лиги {league_key}: {e}")
                stats['leagues_failed'] += 1
                continue

        # Удаляем матчи из БД, которых больше нет в API (опционально, если нужно)
        # Пока не реализовано, так как API может не возвращать все матчи

        print(f"[Football] Синхронизация завершена: лиг обработано={stats['leagues_processed']}, лиг с ошибками={stats['leagues_failed']}, добавлено={stats['added']}, обновлено={stats['updated']}, удалено={stats['deleted']}, пропущено (прошлое)={stats['skipped_past']}")
        
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

    def _extract_odds_1_x_2(self, match_data: Dict) -> Optional[Dict[str, float]]:
        """
        Извлекает медианные коэффициенты для исходов 1, X, 2 из данных матча.
        
        Args:
            match_data: Данные матча от API (уже содержат bookmakers)
            
        Returns:
            Словарь с коэффициентами: {'odds_1': float, 'odds_x': float, 'odds_2': float} или None
        """
        try:
            home_team = match_data.get('home_team')
            away_team = match_data.get('away_team')
            bookmakers = match_data.get('bookmakers', [])
            
            if not home_team or not away_team or not bookmakers:
                return None
            
            # Собираем коэффициенты для каждой команды и ничьей
            home_odds = []
            away_odds = []
            draw_odds = []
            
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
                        
                        if name == home_team:
                            home_odds.append(float(price))
                        elif name == away_team:
                            away_odds.append(float(price))
                        elif name.lower() == 'draw':
                            draw_odds.append(float(price))
            
            if not home_odds or not away_odds or not draw_odds:
                return None
            
            # Вычисляем медианные коэффициенты
            def get_median(odds_list):
                n = len(odds_list)
                if n == 0:
                    return None
                sorted_odds = sorted(odds_list)
                if n % 2 == 0:
                    return (sorted_odds[n//2 - 1] + sorted_odds[n//2]) / 2.0
                else:
                    return sorted_odds[n//2]
            
            odds_1 = get_median(home_odds)
            odds_x = get_median(draw_odds)
            odds_2 = get_median(away_odds)
            
            if odds_1 is None or odds_x is None or odds_2 is None:
                return None
            
            return {
                'odds_1': odds_1,
                'odds_x': odds_x,
                'odds_2': odds_2
            }
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка извлечения коэффициентов 1, X, 2: {e}")
            return None

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
            
            # Сортируем коэффициенты для расчета медианы
            home_odds_sorted = sorted(home_odds)
            away_odds_sorted = sorted(away_odds)
            
            # Берем медианный коэффициент для каждой команды (устойчив к выбросам)
            def get_median(odds_list):
                n = len(odds_list)
                if n == 0:
                    return None
                if n % 2 == 0:
                    return (odds_list[n//2 - 1] + odds_list[n//2]) / 2.0
                else:
                    return odds_list[n//2]
            
            median_home_odd = get_median(home_odds_sorted)
            median_away_odd = get_median(away_odds_sorted)
            
            if median_home_odd is None or median_away_odd is None:
                print(f"[Football] Не удалось рассчитать медианные коэффициенты")
                return None
            
            # Определяем фаворита (меньший медианный коэффициент)
            if median_home_odd <= median_away_odd:
                fav_team = home_team
                fav_is_home = True
                fav_odd = median_home_odd
            else:
                fav_team = away_team
                fav_is_home = False
                fav_odd = median_away_odd
            
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

    def _save_match(self, match_data: Dict, fav_info: Dict, odds_1_x_2: Optional[Dict[str, float]] = None) -> bool:
        """
        Сохраняет матч в БД с фаворитом.
        
        Args:
            match_data: Данные матча от The Odds API
            fav_info: Информация о фаворите
            odds_1_x_2: Словарь с коэффициентами {'odds_1': float, 'odds_x': float, 'odds_2': float}
            
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
            sport_key = match_data.get('sport_key')

            if not home_team or not away_team:
                print(f"[Football] Нет названий команд для матча {event_id}")
                return False

            # Дата и время матча (в UTC от The Odds API)
            commence_time = match_data.get('commence_time')
            if commence_time:
                # Парсим UTC время от The Odds API
                dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                # Сохраняем в UTC (без tzinfo для совместимости с текстовыми полями БД)
                # ВАЖНО: время в БД хранится в UTC, при чтении нужно добавлять timezone.utc
                dt = dt.replace(tzinfo=None)
                match_date = dt.strftime('%Y-%m-%d')
                match_time = dt.strftime('%H:%M')
            else:
                print(f"[Football] Нет даты для матча {event_id}")        
                return False

            # Сохраняем
            # При первом сохранении initial_odds и last_odds одинаковые   
            fav_odds = fav_info['odds']
            
            # Извлекаем коэффициенты 1, X, 2
            live_odds_1 = odds_1_x_2.get('odds_1') if odds_1_x_2 else None
            live_odds_x = odds_1_x_2.get('odds_x') if odds_1_x_2 else None
            live_odds_2 = odds_1_x_2.get('odds_2') if odds_1_x_2 else None
            
            cursor.execute("""
                INSERT INTO matches
                (fixture_id, home_team, away_team, fav, fav_team_id,      
                 match_date, match_time, initial_odds, last_odds, status, sport_key,
                 live_odds_1, live_odds_x, live_odds_2) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,  # fixture_id = event_id из The Odds API        
                home_team,
                away_team,
                fav_info['team'],
                1 if fav_info['is_home'] else 0,  # fav_team_id: 1=home, 0=away
                match_date,
                match_time,
                fav_odds,  # initial_odds - первая котировка
                fav_odds,  # last_odds - при первом сохранении такая же   
                'scheduled',
                sport_key,  # sport_key для использования в запросах live odds
                live_odds_1,
                live_odds_x,
                live_odds_2
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

    def _get_match_status(self, fixture_id: str) -> Optional[str]:
        """
        Получает статус матча из БД.

        Args:
            fixture_id: ID матча из API

        Returns:
            Статус матча ('scheduled', 'in_progress', 'finished') или None если матч не найден
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM matches WHERE fixture_id = ?", (fixture_id,))
            result = cursor.fetchone()
            return result[0] if result else None
        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка получения статуса матча: {e}")
            return None
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

    def _update_match(self, fixture_id: str, fav_info: Dict, match_data: Dict, odds_1_x_2: Optional[Dict[str, float]] = None) -> bool:
        """
        Обновляет коэффициент существующего матча с фаворитом.

        Args:
            fixture_id: ID матча из API
            fav_info: Информация о фаворите
            match_data: Данные матча от API
            odds_1_x_2: Словарь с коэффициентами {'odds_1': float, 'odds_x': float, 'odds_2': float}

        Returns:
            True если успешно, False если ошибка
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()

            # Обновляем только коэффициент (last_odds), фаворита, sport_key, коэффициенты 1/X/2 и время обновления
            # initial_odds не трогаем - там хранится первая котировка
            sport_key = match_data.get('sport_key')
            
            # Извлекаем коэффициенты 1, X, 2
            live_odds_1 = odds_1_x_2.get('odds_1') if odds_1_x_2 else None
            live_odds_x = odds_1_x_2.get('odds_x') if odds_1_x_2 else None
            live_odds_2 = odds_1_x_2.get('odds_2') if odds_1_x_2 else None
            
            cursor.execute("""
                UPDATE matches
                SET fav = ?, fav_team_id = ?, last_odds = ?, sport_key = ?,
                    live_odds_1 = ?, live_odds_x = ?, live_odds_2 = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE fixture_id = ?
            """, (
                fav_info['team'],
                1 if fav_info['is_home'] else 0,
                fav_info['odds'],
                sport_key,
                live_odds_1,
                live_odds_x,
                live_odds_2,
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

    def _save_match_without_fav(self, match_data: Dict, odds_1_x_2: Optional[Dict[str, float]] = None) -> bool:
        """
        Сохраняет матч в БД без фаворита (только базовые поля и коэффициенты 1, X, 2).
        
        Args:
            match_data: Данные матча от The Odds API
            odds_1_x_2: Словарь с коэффициентами {'odds_1': float, 'odds_x': float, 'odds_2': float}
            
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
            sport_key = match_data.get('sport_key')

            if not home_team or not away_team:
                print(f"[Football] Нет названий команд для матча {event_id}")
                return False

            # Дата и время матча (в UTC от The Odds API)
            commence_time = match_data.get('commence_time')
            if commence_time:
                # Парсим UTC время от The Odds API
                dt = datetime.fromisoformat(commence_time.replace('Z', '+00:00'))
                dt = dt.replace(tzinfo=None)
                match_date = dt.strftime('%Y-%m-%d')
                match_time = dt.strftime('%H:%M')
            else:
                print(f"[Football] Нет даты для матча {event_id}")        
                return False

            # Извлекаем коэффициенты 1, X, 2
            live_odds_1 = odds_1_x_2.get('odds_1') if odds_1_x_2 else None
            live_odds_x = odds_1_x_2.get('odds_x') if odds_1_x_2 else None
            live_odds_2 = odds_1_x_2.get('odds_2') if odds_1_x_2 else None
            
            # Сохраняем только базовые поля (fav = 'NONE', fav_team_id = -1, initial_odds, last_odds остаются NULL)
            cursor.execute("""
                INSERT INTO matches
                (fixture_id, home_team, away_team, fav, fav_team_id, match_date, match_time, status, sport_key,
                 live_odds_1, live_odds_x, live_odds_2) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                home_team,
                away_team,
                'NONE',  # Специальное значение вместо NULL
                -1,  # Специальное значение вместо NULL
                match_date,
                match_time,
                'scheduled',
                sport_key,
                live_odds_1,
                live_odds_x,
                live_odds_2
            ))

            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка сохранения матча без фаворита: {e}")
            return False
        except Exception as e:
            print(f"[Football ERROR] Неожиданная ошибка: {e}")
            import traceback
            print(traceback.format_exc())
            return False
        finally:
            if conn:
                conn.close()

    def _update_match_without_fav(self, fixture_id: str, match_data: Dict, odds_1_x_2: Optional[Dict[str, float]] = None) -> bool:
        """
        Обновляет матч без фаворита (только базовые поля и коэффициенты 1, X, 2).

        Args:
            fixture_id: ID матча из API
            match_data: Данные матча от API
            odds_1_x_2: Словарь с коэффициентами {'odds_1': float, 'odds_x': float, 'odds_2': float}

        Returns:
            True если успешно, False если ошибка
        """
        conn = None
        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()

            sport_key = match_data.get('sport_key')
            
            # Извлекаем коэффициенты 1, X, 2
            live_odds_1 = odds_1_x_2.get('odds_1') if odds_1_x_2 else None
            live_odds_x = odds_1_x_2.get('odds_x') if odds_1_x_2 else None
            live_odds_2 = odds_1_x_2.get('odds_2') if odds_1_x_2 else None
            
            # Обновляем только базовые поля и коэффициенты (fav, initial_odds, last_odds не трогаем)
            cursor.execute("""
                UPDATE matches
                SET sport_key = ?, live_odds_1 = ?, live_odds_x = ?, live_odds_2 = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE fixture_id = ?
            """, (
                sport_key,
                live_odds_1,
                live_odds_x,
                live_odds_2,
                fixture_id
            ))

            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[Football ERROR] Ошибка обновления матча без фаворита: {e}")
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
        Проверяет активные матчи и собирает статистику на 60-й минуте и финальный результат.
        Вызывается каждые 5 минут.
        """
        print("[Football] Проверка матчей и сбор статистики")

        try:
            conn = get_football_db_connection()
            cursor = conn.cursor()

            # ===== ЧАСТЬ 1: Обработка матчей на 60-й минуте (только bet IS NULL) =====
            # Разделяем матчи с фаворитом и без
            cursor.execute("""
                SELECT * FROM matches
                WHERE status IN ('scheduled', 'in_progress')
                AND bet IS NULL
                AND fav != 'NONE'
                ORDER BY match_date, match_time
            """)

            matches_with_fav = cursor.fetchall()
            print(f"[Football] Найдено {len(matches_with_fav)} необработанных матчей с фаворитом (bet IS NULL, fav != 'NONE') для проверки на 60-й минуте")

            cursor.execute("""
                SELECT * FROM matches
                WHERE status IN ('scheduled', 'in_progress')
                AND bet IS NULL
                AND fav = 'NONE'
                ORDER BY match_date, match_time
            """)

            matches_without_fav = cursor.fetchall()
            print(f"[Football] Найдено {len(matches_without_fav)} необработанных матчей без фаворита (bet IS NULL, fav = 'NONE') для проверки на 60-й минуте")

            # Обрабатываем матчи с фаворитом (как раньше - с live_odds)
            for match in matches_with_fav:
                match_id = match['id']
                fixture_id = match['fixture_id']
                match_datetime_str = f"{match['match_date']} {match['match_time']}"
                
                try:
                    # Парсим дату и время из БД (они в UTC, но без tzinfo)
                    match_datetime_naive = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
                    # Добавляем UTC часовой пояс, так как время в БД сохранено в UTC
                    match_datetime = match_datetime_naive.replace(tzinfo=timezone.utc)
                    
                    # Используем UTC время для сравнения (независимо от часового пояса сервера)
                    now = datetime.now(timezone.utc)
                    
                    # Вычисляем разницу во времени
                    time_diff = now - match_datetime
                    minutes_diff = time_diff.total_seconds() / 60

                    print(f"[Football] Матч {fixture_id}: прошло {minutes_diff:.1f} минут, статус: {match['status']}")

                    # Проверяем что матч уже начался
                    if minutes_diff < 0:
                        print(f"[Football] Матч {fixture_id} еще не начался (прошло {minutes_diff:.1f} минут). Пропускаем.")
                        continue  # Матч еще не начался

                    # Обновляем статус на in_progress если нужно
                    if match['status'] == 'scheduled':
                        print(f"[Football] Обновляем статус матча {fixture_id} на 'in_progress'")
                        cursor.execute(
                            "UPDATE matches SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (match_id,)
                        )
                        conn.commit()

                    # Проверяем 60-я минута (минимум 55 минут, верхнее ограничение убрано для тестирования)
                    # Обрабатываем только необработанные матчи (bet IS NULL)
                    if minutes_diff >= 55:
                        print(f"[Football] Матч {fixture_id} прошло {minutes_diff:.1f} минут (>= 55). Собираем статистику и обрабатываем...")
                        try:
                            self._collect_60min_stats(match)
                        except Exception as e:
                            print(f"[Football ERROR] Ошибка сбора статистики 60min для {fixture_id}: {e}")
                            import traceback
                            print(traceback.format_exc())
                            # В случае ошибки тоже помечаем как обработанный, чтобы не повторять
                            cursor.execute(
                                "UPDATE matches SET bet = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (match_id,)
                            )
                            conn.commit()
                    else:
                        # Матч еще не достиг 55 минут или еще не начался - оставляем bet = NULL для следующей проверки
                        if minutes_diff < 0:
                            print(f"[Football] Матч {fixture_id} еще не начался - прошло {minutes_diff:.1f} минут. Оставляем для следующей проверки.")
                        else:
                            print(f"[Football] Матч {fixture_id} еще не достиг 55 минут - прошло {minutes_diff:.1f} минут. Оставляем для следующей проверки.")
                        # Не трогаем bet - оставляем NULL

                except Exception as e:
                    print(f"[Football ERROR] Ошибка проверки матча на 60-ю минуту {fixture_id}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
                
                # Задержка между матчами (4-8 секунд) для избежания бана в SofaScore
                delay_between_matches = random.uniform(4.0, 8.0)
                print(f"[Football] Задержка {delay_between_matches:.1f} сек перед следующим матчем с фаворитом")
                time.sleep(delay_between_matches)

            # Обрабатываем матчи без фаворита (без live_odds, только прогноз ИИ)
            for match in matches_without_fav:
                match_id = match['id']
                fixture_id = match['fixture_id']
                match_datetime_str = f"{match['match_date']} {match['match_time']}"
                
                try:
                    # Парсим дату и время из БД (они в UTC, но без tzinfo)
                    match_datetime_naive = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
                    # Добавляем UTC часовой пояс, так как время в БД сохранено в UTC
                    match_datetime = match_datetime_naive.replace(tzinfo=timezone.utc)
                    
                    # Используем UTC время для сравнения (независимо от часового пояса сервера)
                    now = datetime.now(timezone.utc)
                    
                    # Вычисляем разницу во времени
                    time_diff = now - match_datetime
                    minutes_diff = time_diff.total_seconds() / 60

                    print(f"[Football] Матч без фаворита {fixture_id}: прошло {minutes_diff:.1f} минут, статус: {match['status']}")

                    # Проверяем что матч уже начался
                    if minutes_diff < 0:
                        print(f"[Football] Матч {fixture_id} еще не начался (прошло {minutes_diff:.1f} минут). Пропускаем.")
                        continue  # Матч еще не начался

                    # Обновляем статус на in_progress если нужно
                    if match['status'] == 'scheduled':
                        print(f"[Football] Обновляем статус матча {fixture_id} на 'in_progress'")
                        cursor.execute(
                            "UPDATE matches SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (match_id,)
                        )
                        conn.commit()

                    # Проверяем 60-я минута (минимум 55 минут)
                    if minutes_diff >= 55:
                        print(f"[Football] Матч без фаворита {fixture_id} прошло {minutes_diff:.1f} минут (>= 55). Собираем статистику и обрабатываем...")
                        try:
                            self._collect_60min_stats_without_fav(match)
                        except Exception as e:
                            print(f"[Football ERROR] Ошибка сбора статистики 60min для матча без фаворита {fixture_id}: {e}")
                            import traceback
                            print(traceback.format_exc())
                            # В случае ошибки тоже помечаем как обработанный, чтобы не повторять
                            cursor.execute(
                                "UPDATE matches SET bet = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (match_id,)
                            )
                            conn.commit()
                    else:
                        # Матч еще не достиг 55 минут - оставляем bet = NULL для следующей проверки
                        print(f"[Football] Матч без фаворита {fixture_id} еще не достиг 55 минут - прошло {minutes_diff:.1f} минут. Оставляем для следующей проверки.")
                        # Не трогаем bet - оставляем NULL

                except Exception as e:
                    print(f"[Football ERROR] Ошибка проверки матча без фаворита на 60-ю минуту {fixture_id}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
                
                # Задержка между матчами (4-8 секунд) для избежания бана в SofaScore
                delay_between_matches = random.uniform(4.0, 8.0)
                print(f"[Football] Задержка {delay_between_matches:.1f} сек перед следующим матчем без фаворита")
                time.sleep(delay_between_matches)

            # ===== ЧАСТЬ 1.5: Обновление live_odds для уже обработанных матчей без live_odds =====
            cursor.execute("""
                SELECT * FROM matches
                WHERE status = 'in_progress'
                AND bet IS NOT NULL
                AND live_odds IS NULL
                ORDER BY match_date, match_time
            """)
            
            matches_for_live_odds = cursor.fetchall()
            print(f"[Football] Найдено {len(matches_for_live_odds)} матчей с bet, но без live_odds для обновления")
            
            for match in matches_for_live_odds:
                try:
                    fixture_id = match['fixture_id']
                    match_datetime_str = f"{match['match_date']} {match['match_time']}"
                    match_datetime_naive = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
                    match_datetime = match_datetime_naive.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    minutes_diff = (now - match_datetime).total_seconds() / 60.0
                    
                    # Обновляем live_odds только если прошло >= 55 минут
                    if minutes_diff >= 55:
                        print(f"[Football] Обновляем live_odds для матча {fixture_id} (прошло {minutes_diff:.1f} минут)...")
                        sport_key = match['sport_key'] if 'sport_key' in match.keys() else None
                        live_odds_value = self._get_live_odds(fixture_id, sport_key)
                        if live_odds_value:
                            print(f"[Football] Получены live odds для {fixture_id}: {live_odds_value}")
                            cursor.execute("""
                                UPDATE matches
                                SET live_odds = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (live_odds_value, match['id']))
                            conn.commit()
                        else:
                            print(f"[Football] Не удалось получить live odds для {fixture_id}")
                except Exception as e:
                    print(f"[Football ERROR] Ошибка обновления live_odds для {match['fixture_id']}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue

            # ===== ЧАСТЬ 2: Сбор финального результата (для всех матчей in_progress, независимо от bet) =====
            cursor.execute("""
                SELECT * FROM matches
                WHERE status = 'in_progress'
                ORDER BY match_date, match_time
            """)

            matches_for_final = cursor.fetchall()
            print(f"[Football] Найдено {len(matches_for_final)} матчей in_progress для проверки финального результата")

            # Проверяем каждый матч на завершение
            for match in matches_for_final:
                match_id = match['id']
                fixture_id = match['fixture_id']
                sofascore_event_id = match['sofascore_event_id'] if 'sofascore_event_id' in match.keys() and match['sofascore_event_id'] else None
                match_datetime_str = f"{match['match_date']} {match['match_time']}"

                try:
                    # Парсим дату и время из БД (они в UTC, но без tzinfo)
                    match_datetime_naive = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
                    # Добавляем UTC часовой пояс, так как время в БД сохранено в UTC
                    match_datetime = match_datetime_naive.replace(tzinfo=timezone.utc)

                    # Используем UTC время для сравнения (независимо от часового пояса сервера)
                    now = datetime.now(timezone.utc)

                    # Вычисляем разницу во времени
                    time_diff = now - match_datetime
                    minutes_diff = time_diff.total_seconds() / 60

                    # Проверяем статус матча из SofaScore API (предпочтительный способ)
                    # Вызываем только если прошло минимум 85 минут (матч может быть близок к завершению)
                    should_check_final = False
                    
                    if sofascore_event_id and minutes_diff >= 85:
                        print(f"[Football] Проверяем статус матча {fixture_id} из SofaScore API (event_id={sofascore_event_id}, прошло {minutes_diff:.1f} минут)...")
                        event_status = self._fetch_sofascore_event_status(sofascore_event_id)
                        
                        if event_status == 'finished':
                            print(f"[Football] Матч {fixture_id} завершен по статусу из SofaScore API. Собираем финальный результат...")
                            should_check_final = True
                        elif event_status:
                            print(f"[Football] Статус матча {fixture_id} из SofaScore: {event_status}")
                            # Если матч не завершен по статусу, но прошло много времени - используем запасной вариант
                            if minutes_diff >= 200:
                                print(f"[Football] Матч {fixture_id} еще не завершен по статусу, но прошло {minutes_diff:.1f} минут. Используем запасной вариант.")
                                should_check_final = True
                        else:
                            # Если не удалось получить статус из API, используем проверку по времени
                            print(f"[Football] Не удалось получить статус из SofaScore API для {fixture_id}. Используем проверку по времени.")
                            if minutes_diff >= 200:
                                print(f"[Football] Матч {fixture_id} прошло {minutes_diff:.1f} минут. Проверяем финальный результат...")
                                should_check_final = True
                    elif not sofascore_event_id:
                        # Если нет sofascore_event_id, используем только проверку по времени
                        print(f"[Football] У матча {fixture_id} нет sofascore_event_id. Используем проверку по времени.")
                        if minutes_diff >= 200:
                            print(f"[Football] Матч {fixture_id} прошло {minutes_diff:.1f} минут. Проверяем финальный результат...")
                            should_check_final = True
                    elif minutes_diff < 85:
                        # Матч еще слишком рано (меньше 85 минут) - не проверяем статус из API
                        print(f"[Football] Матч {fixture_id} прошло только {minutes_diff:.1f} минут (меньше 85). Пропускаем проверку статуса.")

                    if should_check_final:
                        try:
                            self._collect_final_result(match)
                        except Exception as e:
                            print(f"[Football ERROR] Ошибка сбора финального результата для {fixture_id}: {e}")
                            import traceback
                            print(traceback.format_exc())

                except Exception as e:
                    print(f"[Football ERROR] Ошибка проверки финального результата для {fixture_id}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue

            conn.close()
            print(f"[Football] Обработка матчей завершена. Проверено с фаворитом: {len(matches_with_fav)}, без фаворита: {len(matches_without_fav)}, на финальный результат: {len(matches_for_final)}")
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка проверки матчей: {e}")
            import traceback
            print(traceback.format_exc())
        finally:
            if conn:
                conn.close()

    def _fetch_sofascore_statistics(self, sofascore_event_id: int) -> Optional[Dict]:
        """
        Получает статистику матча с SofaScore API.

        Args:
            sofascore_event_id: ID события в SofaScore

        Returns:
            Словарь со статистикой или None в случае ошибки
        """
        import random
        
        url = f"{SOFASCORE_API_URL}/event/{sofascore_event_id}/statistics"
        max_retries = 5
        attempt = 0
        
        while attempt < max_retries:
            try:
                # Выбираем случайный User-Agent
                headers = SOFASCORE_DEFAULT_HEADERS.copy()
                headers['User-Agent'] = random.choice(SOFASCORE_USER_AGENTS)
                
                # Случайная задержка перед запросом (1-3 секунды)
                if attempt > 0:
                    delay = random.uniform(2.0, 4.0) * (2 ** attempt)  # Экспоненциальный backoff
                    time.sleep(delay)
                
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    return data
                elif response.status_code == 403:
                    print(f"[Football SofaScore] 403 Forbidden при запросе статистики для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    if attempt < max_retries:
                        time.sleep(random.uniform(5.0, 10.0))
                    continue
                elif response.status_code >= 500:
                    print(f"[Football SofaScore] Ошибка сервера {response.status_code} при запросе статистики для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    continue
                else:
                    print(f"[Football SofaScore] Ошибка {response.status_code} при запросе статистики для event_id={sofascore_event_id}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                print(f"[Football SofaScore] Сетевая ошибка при запросе статистики для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}: {e}")
                attempt += 1
                if attempt >= max_retries:
                    return None
                time.sleep(random.uniform(2.0, 4.0) * (2 ** attempt))
        
        print(f"[Football SofaScore] Не удалось получить статистику для event_id={sofascore_event_id} после {max_retries} попыток")
        return None

    def _fetch_sofascore_event_status(self, sofascore_event_id: int) -> Optional[str]:
        """
        Получает статус матча из SofaScore API.

        Args:
            sofascore_event_id: ID события в SofaScore

        Returns:
            Статус матча ('finished', 'live', 'notstarted', 'postponed' и т.д.) или None в случае ошибки
        """
        import random

        url = f"{SOFASCORE_API_URL}/event/{sofascore_event_id}"
        max_retries = 3
        attempt = 0

        while attempt < max_retries:
            try:
                # Выбираем случайный User-Agent
                headers = SOFASCORE_DEFAULT_HEADERS.copy()
                headers['User-Agent'] = random.choice(SOFASCORE_USER_AGENTS)

                # Случайная задержка перед запросом
                if attempt > 0:
                    delay = random.uniform(2.0, 4.0) * (2 ** attempt)
                    time.sleep(delay)

                response = requests.get(url, headers=headers, timeout=30)

                if response.status_code == 200:
                    data = response.json()
                    
                    # Извлекаем статус из различных возможных полей
                    # Обычно статус находится в event.status или event.statusText
                    event = data.get('event', {})
                    
                    # Варианты полей со статусом
                    status = event.get('status') or event.get('statusText') or event.get('statusDescription')
                    
                    if status:
                        # Нормализуем статус
                        status_lower = str(status).lower()
                        if 'finished' in status_lower or 'ft' in status_lower:
                            return 'finished'
                        elif 'live' in status_lower or 'inprogress' in status_lower:
                            return 'live'
                        elif 'notstarted' in status_lower or 'not started' in status_lower:
                            return 'notstarted'
                        elif 'postponed' in status_lower or 'cancelled' in status_lower:
                            return 'postponed'
                        else:
                            return status_lower
                    
                    # Если статус не найден, проверяем другие поля
                    # Иногда статус может быть в корне объекта
                    status = data.get('status') or data.get('statusText')
                    if status:
                        return str(status).lower()
                    
                    return None
                    
                elif response.status_code == 403:
                    print(f"[Football SofaScore] 403 Forbidden при запросе статуса для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    if attempt < max_retries:
                        time.sleep(random.uniform(5.0, 10.0))
                    continue
                elif response.status_code >= 500:
                    print(f"[Football SofaScore] Ошибка сервера {response.status_code} при запросе статуса для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    continue
                else:
                    print(f"[Football SofaScore] Ошибка {response.status_code} при запросе статуса для event_id={sofascore_event_id}")
                    return None

            except requests.exceptions.RequestException as e:
                print(f"[Football SofaScore] Сетевая ошибка при запросе статуса для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}: {e}")
                attempt += 1
                if attempt >= max_retries:
                    return None
                time.sleep(random.uniform(2.0, 4.0) * (2 ** attempt))

        print(f"[Football SofaScore] Не удалось получить статус для event_id={sofascore_event_id} после {max_retries} попыток")
        return None

    def _fetch_sofascore_event(self, sofascore_event_id: int) -> Optional[Dict]:
        """
        Получает полные данные о событии из SofaScore API.

        Args:
            sofascore_event_id: ID события в SofaScore

        Returns:
            Словарь с данными события или None в случае ошибки
        """
        import random

        url = f"{SOFASCORE_API_URL}/event/{sofascore_event_id}"
        max_retries = 3
        attempt = 0

        while attempt < max_retries:
            try:
                # Выбираем случайный User-Agent
                headers = SOFASCORE_DEFAULT_HEADERS.copy()
                headers['User-Agent'] = random.choice(SOFASCORE_USER_AGENTS)

                # Случайная задержка перед запросом
                if attempt > 0:
                    delay = random.uniform(2.0, 4.0) * (2 ** attempt)
                    time.sleep(delay)

                response = requests.get(url, headers=headers, timeout=30)

                if response.status_code == 200:
                    data = response.json()
                    return data

                elif response.status_code == 403:
                    print(f"[Football SofaScore] 403 Forbidden при запросе события для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    if attempt < max_retries:
                        time.sleep(random.uniform(5.0, 10.0))
                    continue
                elif response.status_code >= 500:
                    print(f"[Football SofaScore] Ошибка сервера {response.status_code} при запросе события для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}")
                    attempt += 1
                    continue
                else:
                    print(f"[Football SofaScore] Ошибка {response.status_code} при запросе события для event_id={sofascore_event_id}")
                    return None

            except requests.exceptions.RequestException as e:
                print(f"[Football SofaScore] Сетевая ошибка при запросе события для event_id={sofascore_event_id}, попытка {attempt + 1}/{max_retries}: {e}")
                attempt += 1
                if attempt >= max_retries:
                    return None
                time.sleep(random.uniform(2.0, 4.0) * (2 ** attempt))

        print(f"[Football SofaScore] Не удалось получить данные события для event_id={sofascore_event_id} после {max_retries} попыток")
        return None

    def _get_live_odds(self, fixture_id: str, sport_key: Optional[str] = None) -> Optional[float]:
        """
        Получает актуальные live коэффициенты фаворита на победу с The Odds API.
        
        Использует эндпойнт /v4/sports/{sport}/events/{eventId}/odds для получения коэффициентов конкретного события.
        Требует конкретный sport_key (например, 'soccer_uefa_champs_league'), который должен быть сохранен в БД.

        Args:
            fixture_id: ID матча в The Odds API (eventId)
            sport_key: Ключ вида спорта (например, 'soccer_epl'). Если не указан, будет получен из БД.

        Returns:
            Коэффициент фаворита на победу или None в случае ошибки/отсутствия live odds
        """
        try:
            # Если sport_key не передан, пытаемся получить из БД
            if not sport_key:
                conn = get_football_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT sport_key FROM matches WHERE fixture_id = ?", (fixture_id,))
                row = cursor.fetchone()
                conn.close()
                
                if row and row['sport_key']:
                    sport_key = row['sport_key']
                else:
                    # Если sport_key не найден в БД - это ошибка, не делаем запрос
                    print(f"[Football ERROR] sport_key не найден в БД для fixture {fixture_id}, пропускаем запрос live odds")
                    print(f"[Football] Запустите синхронизацию матчей (/api/football/sync) для обновления sport_key")
                    return None
            
            # Параметры запроса
            params = {
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            
            # Используем эндпойнт /sports/{sport}/events/{eventId}/odds
            # Требуется конкретный sport_key (например, 'soccer_uefa_champs_league')
            endpoint = f"/sports/{sport_key}/events/{fixture_id}/odds"
            data = self._make_api_request(endpoint, params)
            
            if not data or not isinstance(data, dict):
                print(f"[Football] Не удалось получить live odds для fixture {fixture_id} (ответ не является объектом)")
                return None
            
            # Проверяем, что ID матча совпадает
            if data.get('id') != fixture_id:
                print(f"[Football] Несоответствие ID: запрошен {fixture_id}, получен {data.get('id')}")
                return None
            
            # Извлекаем медианные коэффициенты для 1, X, 2 для сохранения в БД
            home_team = data.get('home_team')
            away_team = data.get('away_team')
            bookmakers = data.get('bookmakers', [])
            
            live_odds_1 = None
            live_odds_x = None
            live_odds_2 = None
            
            if home_team and away_team and bookmakers:
                # Собираем коэффициенты для каждой команды и ничьей
                home_odds = []
                away_odds = []
                draw_odds = []
                
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
                            
                            if name == home_team:
                                home_odds.append(float(price))
                            elif name == away_team:
                                away_odds.append(float(price))
                            elif name.lower() == 'draw':
                                draw_odds.append(float(price))
                
                # Вычисляем медианные коэффициенты
                def get_median(odds_list):
                    n = len(odds_list)
                    if n == 0:
                        return None
                    sorted_odds = sorted(odds_list)
                    if n % 2 == 0:
                        return (sorted_odds[n//2 - 1] + sorted_odds[n//2]) / 2.0
                    else:
                        return sorted_odds[n//2]
                
                live_odds_1 = get_median(home_odds)
                live_odds_x = get_median(draw_odds)
                live_odds_2 = get_median(away_odds)
            
            # Сохраняем коэффициенты в БД
            if live_odds_1 is not None or live_odds_x is not None or live_odds_2 is not None:
                conn = get_football_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE matches
                    SET live_odds_1 = ?, live_odds_x = ?, live_odds_2 = ?
                    WHERE fixture_id = ?
                """, (live_odds_1, live_odds_x, live_odds_2, fixture_id))
                conn.commit()
                conn.close()
            
            # Находим фаворита по медианному коэффициенту
            fav_info = self._determine_favorite(data)
            if fav_info:
                return fav_info['odds']
            
            print(f"[Football] Не удалось определить фаворита для fixture {fixture_id}")
            return None
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка получения live odds для fixture {fixture_id}: {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def _get_ai_prediction_odds(self, fixture_id: str, bet_ai: str) -> Optional[float]:
        """
        Получает коэффициент на прогнозированный исход ИИ (1, 1X, X, X2, 2) из БД.
        
        Для одиночных исходов (1, X, 2) берет коэффициент из БД (live_odds_1, live_odds_x, live_odds_2).
        Для двойных шансов (1X, X2) вычисляет по формуле: 1 / (1/odd1 + 1/oddX)
        
        Коэффициенты должны быть сохранены в БД при запросе live_odds (_get_live_odds).
        
        Args:
            fixture_id: ID матча в The Odds API
            bet_ai: Прогноз ИИ ('1', '1X', 'X', 'X2', '2')

        Returns:
            Коэффициент на прогнозированный исход или None в случае ошибки
        """
        try:
            if not bet_ai:
                return None
            
            # Получаем коэффициенты из БД
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT live_odds_1, live_odds_x, live_odds_2
                FROM matches
                WHERE fixture_id = ?
            """, (fixture_id,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                print(f"[Football ERROR] Матч не найден в БД для fixture {fixture_id}")
                return None
            
            odd1 = row['live_odds_1']
            oddX = row['live_odds_x']
            odd2 = row['live_odds_2']
            
            if odd1 is None or oddX is None or odd2 is None:
                print(f"[Football] Коэффициенты для расчета bet_ai_odds не найдены в БД для fixture {fixture_id}")
                print(f"[Football] Возможно, live_odds еще не были запрошены. Коэффициенты: 1={odd1}, X={oddX}, 2={odd2}")
                return None
            
            # Возвращаем коэффициент в зависимости от прогноза ИИ
            bet_ai_upper = bet_ai.upper()
            
            if bet_ai_upper == '1':
                return float(odd1)
            elif bet_ai_upper == 'X':
                return float(oddX)
            elif bet_ai_upper == '2':
                return float(odd2)
            elif bet_ai_upper == '1X':
                # Двойной шанс: победа хозяев или ничья
                return 1.0 / (1.0/float(odd1) + 1.0/float(oddX))
            elif bet_ai_upper == 'X2':
                # Двойной шанс: ничья или победа гостей
                return 1.0 / (1.0/float(oddX) + 1.0/float(odd2))
            else:
                print(f"[Football] Неизвестный прогноз ИИ: {bet_ai}")
                return None
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка получения коэффициента для прогноза ИИ (bet_ai={bet_ai}): {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def _collect_60min_stats(self, match: sqlite3.Row):
        """
        Собирает статистику на 60-й минуте с SofaScore.

        Args:
            match: Запись матча из БД
        """
        try:
            fixture_id = match['fixture_id']
            sofascore_event_id = match['sofascore_event_id'] if 'sofascore_event_id' in match.keys() else None

            if not sofascore_event_id:
                print(f"[Football] Нет sofascore_event_id для матча {fixture_id}, пропускаем")
                return

            # Сначала получаем основное событие для актуального счета
            event_data = self._fetch_sofascore_event(sofascore_event_id)
            actual_score = None
            if event_data and 'event' in event_data:
                event = event_data['event']
                home_score_obj = event.get('homeScore', {})
                away_score_obj = event.get('awayScore', {})
                
                if isinstance(home_score_obj, dict) and isinstance(away_score_obj, dict):
                    # Приоритет: current (текущий счет) > normaltime > display
                    score_home = home_score_obj.get('current') or home_score_obj.get('normaltime') or home_score_obj.get('display')
                    score_away = away_score_obj.get('current') or away_score_obj.get('normaltime') or away_score_obj.get('display')
                    
                    if score_home is not None and score_away is not None:
                        try:
                            actual_score = {
                                'home': int(score_home),
                                'away': int(score_away)
                            }
                            print(f"[Football] Актуальный счет для fixture {fixture_id}: {actual_score['home']}-{actual_score['away']}")
                        except (ValueError, TypeError):
                            print(f"[Football] Ошибка преобразования счета в числа: home={score_home}, away={score_away}")

            # Задержка между запросами к SofaScore (2-5 секунд) для избежания бана
            delay_between_requests = random.uniform(2.0, 5.0)
            print(f"[Football] Задержка {delay_between_requests:.1f} сек перед запросом статистики для матча {fixture_id}")
            time.sleep(delay_between_requests)

            # Получаем статистику с SofaScore
            stats_data = self._fetch_sofascore_statistics(sofascore_event_id)

            if not stats_data:
                print(f"[Football] Не удалось получить статистику с SofaScore для event_id={sofascore_event_id}")
                return

            # Парсим статистику из SofaScore
            stats = self._parse_sofascore_statistics(stats_data, match)
            
            # Перезаписываем счет актуальным из основного события, если он был получен
            if actual_score:
                stats['score'] = actual_score
                print(f"[Football] Счет заменен на актуальный из основного события: {actual_score}")

            # ВСЕГДА запрашиваем live_odds, независимо от условий
            print(f"[Football] Запрашиваем live odds для матча {fixture_id}...")
            sport_key = match['sport_key'] if 'sport_key' in match.keys() else None
            live_odds_value = self._get_live_odds(fixture_id, sport_key)
            if live_odds_value:
                print(f"[Football] Получены live odds для {fixture_id}: {live_odds_value}")
            else:
                print(f"[Football] Не удалось получить live odds для {fixture_id}")

                        # Проверяем условия и записываем bet
            bet_value, _, ai_decision, ai_reason = self._calculate_bet(match, stats, fixture_id)

            # Отправляем уведомление админу (если фаворит не выигрывает)
            self._send_match_notification(match, stats, live_odds_value, ai_decision, ai_reason)

            # Сохраняем в БД (всегда сохраняем live_odds, даже если условия не выполнены)
            conn = get_football_db_connection()
            cursor = conn.cursor()

            stats_json = json.dumps(stats)
            cursor.execute("""
                UPDATE matches
                SET stats_60min = ?, bet = ?, live_odds = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (stats_json, bet_value, live_odds_value, match['id']))

            conn.commit()
            conn.close()

            print(f"[Football] Статистика на 60-й минуте сохранена для fixture {fixture_id}, bet: {bet_value}")
            
            # Получаем прогноз от ИИ
            print(f"[Football] Запрашиваем ИИ-прогноз для fixture {fixture_id}...")
            bet_ai, bet_ai_reason = self._get_ai_prediction(match, stats) 

            if bet_ai or bet_ai_reason:
                # Получаем коэффициент на прогнозированный исход из БД
                bet_ai_odds = None
                if bet_ai:
                    print(f"[Football] Получаем коэффициент для прогноза ИИ '{bet_ai}' для fixture {fixture_id}...")
                    bet_ai_odds = self._get_ai_prediction_odds(fixture_id, bet_ai)
                    if bet_ai_odds:
                        print(f"[Football] Получен коэффициент {bet_ai_odds} для прогноза ИИ '{bet_ai}'")
                    else:
                        print(f"[Football] Не удалось получить коэффициент для прогноза ИИ '{bet_ai}' (возможно, live_odds еще не были запрошены)")
                
                # Сохраняем результат ИИ в БД
                conn = get_football_db_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE matches
                    SET bet_ai = ?, bet_ai_reason = ?, bet_ai_odds = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (bet_ai, bet_ai_reason, bet_ai_odds, match['id']))

                conn.commit()
                conn.close()

                if bet_ai:
                    print(f"[Football] ИИ-прогноз сохранен для fixture {fixture_id}: {bet_ai}, коэффициент: {bet_ai_odds}")
                else:
                    print(f"[Football] ИИ-прогноз не распознан, но ответ сохранен для fixture {fixture_id}")

        except Exception as e:
            print(f"[Football ERROR] Ошибка сбора статистики 60min: {e}")
            import traceback
            print(traceback.format_exc())

    def _collect_60min_stats_without_fav(self, match: sqlite3.Row):
        """
        Собирает статистику на 60-й минуте для матчей без фаворита.
        Не запрашивает live_odds, только статистику и прогноз ИИ.
        
        Args:
            match: Запись матча из БД
        """
        try:
            fixture_id = match['fixture_id']
            sofascore_event_id = match['sofascore_event_id'] if 'sofascore_event_id' in match.keys() else None

            if not sofascore_event_id:
                print(f"[Football] Нет sofascore_event_id для матча {fixture_id}, пропускаем")
                return

            # Сначала получаем основное событие для актуального счета
            event_data = self._fetch_sofascore_event(sofascore_event_id)
            actual_score = None
            if event_data and 'event' in event_data:
                event = event_data['event']
                home_score_obj = event.get('homeScore', {})
                away_score_obj = event.get('awayScore', {})
                
                if isinstance(home_score_obj, dict) and isinstance(away_score_obj, dict):
                    # Приоритет: current (текущий счет) > normaltime > display
                    score_home = home_score_obj.get('current') or home_score_obj.get('normaltime') or home_score_obj.get('display')
                    score_away = away_score_obj.get('current') or away_score_obj.get('normaltime') or away_score_obj.get('display')
                    
                    if score_home is not None and score_away is not None:
                        try:
                            actual_score = {
                                'home': int(score_home),
                                'away': int(score_away)
                            }
                            print(f"[Football] Актуальный счет для fixture {fixture_id}: {actual_score['home']}-{actual_score['away']}")
                        except (ValueError, TypeError):
                            print(f"[Football] Ошибка преобразования счета в числа: home={score_home}, away={score_away}")

            # Задержка между запросами к SofaScore (2-5 секунд) для избежания бана
            delay_between_requests = random.uniform(2.0, 5.0)
            print(f"[Football] Задержка {delay_between_requests:.1f} сек перед запросом статистики для матча без фаворита {fixture_id}")
            time.sleep(delay_between_requests)

            # Получаем статистику с SofaScore
            stats_data = self._fetch_sofascore_statistics(sofascore_event_id)

            if not stats_data:
                print(f"[Football] Не удалось получить статистику с SofaScore для event_id={sofascore_event_id}")
                return

            # Парсим статистику из SofaScore
            stats = self._parse_sofascore_statistics(stats_data, match)
            
            # Перезаписываем счет актуальным из основного события, если он был получен
            if actual_score:
                stats['score'] = actual_score
                print(f"[Football] Счет заменен на актуальный из основного события: {actual_score}")

            # ===== ОТЛАДКА: Запрашиваем live odds для матчей без фаворита =====
            # TODO: Убрать этот блок после отладки или при достижении лимитов API
            # Цель: обновить live_odds_1, live_odds_x, live_odds_2 в таблице для отображения
            # ВАЖНО: Это расходует запросы к The Odds API. При достижении лимитов - закомментировать
            live_odds_value = None
            try:
                print(f"[Football DEBUG] Запрашиваем live odds для матча без фаворита {fixture_id}...")
                sport_key = match['sport_key'] if 'sport_key' in match.keys() else None
                live_odds_value = self._get_live_odds(fixture_id, sport_key)
                if live_odds_value:
                    print(f"[Football DEBUG] Получены live odds для матча без фаворита {fixture_id}: {live_odds_value}")
                else:
                    print(f"[Football DEBUG] Не удалось получить live odds для матча без фаворита {fixture_id}")
            except Exception as e:
                print(f"[Football DEBUG ERROR] Ошибка получения live odds для матча без фаворита {fixture_id}: {e}")
                # Не прерываем выполнение, продолжаем без live odds
            # ===== КОНЕЦ ОТЛАДКИ =====

            # Сохраняем статистику в БД (bet пока не устанавливаем, он будет установлен после получения рекомендации ИИ)
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

            print(f"[Football] Статистика на 60-й минуте сохранена для матча без фаворита {fixture_id}")
            
            # Получаем прогноз от ИИ (без упоминания фаворита)
            print(f"[Football] Запрашиваем ИИ-прогноз для матча без фаворита {fixture_id}...")
            bet_ai, bet_ai_reason, bet_recommendation = self._get_ai_prediction_without_fav(match, stats) 

            # Устанавливаем bet на основе рекомендации
            bet_value = 1 if bet_recommendation else 0

            if bet_ai or bet_ai_reason:
                # Получаем коэффициент на прогнозированный исход из БД
                bet_ai_odds = None
                if bet_ai:
                    print(f"[Football] Получаем коэффициент для прогноза ИИ '{bet_ai}' для fixture {fixture_id}...")
                    bet_ai_odds = self._get_ai_prediction_odds(fixture_id, bet_ai)
                    if bet_ai_odds:
                        print(f"[Football] Получен коэффициент {bet_ai_odds} для прогноза ИИ '{bet_ai}'")
                    else:
                        print(f"[Football] Не удалось получить коэффициент для прогноза ИИ '{bet_ai}'")
                
                # Сохраняем результат ИИ в БД
                conn = get_football_db_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE matches
                    SET bet_ai = ?, bet_ai_reason = ?, bet_ai_odds = ?, bet = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (bet_ai, bet_ai_reason, bet_ai_odds, bet_value, match['id']))

                conn.commit()
                conn.close()

                recommendation_text = "СТАВИМ" if bet_recommendation else "ИГНОРИРУЕМ"
                if bet_ai:
                    print(f"[Football] ИИ-прогноз сохранен для матча без фаворита {fixture_id}: {bet_ai}, коэффициент: {bet_ai_odds}, рекомендация: {recommendation_text}, bet: {bet_value}")
                else:
                    print(f"[Football] ИИ-прогноз не распознан, но ответ сохранен для матча без фаворита {fixture_id}, bet: {bet_value}")
            else:
                # Если прогноз не получен, все равно обновляем bet = 0
                conn = get_football_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE matches
                    SET bet = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (bet_value, match['id']))
                conn.commit()
                conn.close()
                print(f"[Football] ИИ-прогноз не получен для матча без фаворита {fixture_id}, установлен bet: {bet_value}")

        except Exception as e:
            print(f"[Football ERROR] Ошибка сбора статистики 60min для матча без фаворита: {e}")
            import traceback
            print(traceback.format_exc())

    def _get_ai_prediction_without_fav(self, match: sqlite3.Row, stats: Dict) -> Tuple[Optional[str], Optional[str], Optional[bool]]:
        """
        Получает прогноз от ИИ для матчей без фаворита (без упоминания фаворита в промпте).
        
        Args:
            match: Запись матча из БД
            stats: Статистика на 60-й минуте (из stats_60min)
        
        Returns:
            Кортеж (bet_ai, bet_ai_reason):
            - bet_ai: Прогноз ('1', '1X', 'X', 'X2', '2') или None
            - bet_ai_reason: Полный ответ от ИИ или None
        """
        if not self.openrouter_api_key:
            print("[Football] OpenRouter API ключ не установлен, пропускаем ИИ-прогноз")
            return None, None
        
        try:
            # Формируем промпт без упоминания фаворита
            home_team = match['home_team']
            away_team = match['away_team']
            
            score = stats.get('score', {})
            home_score = score.get('home', 0)
            away_score = score.get('away', 0)
            
            # Получаем коэффициенты из БД (они должны быть уже сохранены при запросе live_odds)
            fixture_id = match['fixture_id']
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT live_odds_1, live_odds_x, live_odds_2
                FROM matches
                WHERE fixture_id = ?
            """, (fixture_id,))
            row = cursor.fetchone()
            conn.close()
            
            live_odds_1 = row['live_odds_1'] if row else None
            live_odds_x = row['live_odds_x'] if row else None
            live_odds_2 = row['live_odds_2'] if row else None
            
            # Форматируем статистику как JSON для передачи ИИ
            stats_json = json.dumps(stats, ensure_ascii=False, indent=2)
            
            # Формируем строку с коэффициентами
            odds_info = ""
            if live_odds_1 is not None or live_odds_x is not None or live_odds_2 is not None:
                odds_info = f"""
- Текущие коэффициенты на исходы:
  * Победа {home_team}: {live_odds_1 if live_odds_1 is not None else 'N/A'}
  * Ничья: {live_odds_x if live_odds_x is not None else 'N/A'}
  * Победа {away_team}: {live_odds_2 if live_odds_2 is not None else 'N/A'}
"""
            
            prompt = f"""Ты - футбольный аналитик. Сейчас перерыв после первого тайма. Изучи предоставленную статистику матча после первого тайма, хорошо подумай и сделай прогноз на итоговый результат матча в основное время.

Информация о матче:
- Команды: {home_team} vs {away_team}
- Текущий счет после первого тайма: {home_score} - {away_score}
Детальная статистика первого тайма:
{stats_json}

Твой ответ должен состоять в виде строки в формате: "Результат (1, 1X, X, X2, 2) Рекомендация (ИГНОРИРУЕМ или СТАВИМ)".

1. Результат СТРОГО в виде одного из вариантов: 1 или 1X или X или X2 или 2
Где:
- 1 = победа домашней команды ({home_team})
- 1X = ничья или победа домашней команды ({home_team})
- X = ничья
- X2 = ничья или победа гостевой команды ({away_team})
- 2 = победа гостевой команды ({away_team})

2. Рекомендация, стоит ли ставить на этот исход (СТАВИМ или ИГНОРИРУЕМ) при текущих коэффициентах букмекеров.
{odds_info}
Отвчечай СТАВИМ только если прогноз имеет хорошее соотношение цены и вероятности на основе коэффициентов и статистики.

Примеры ответа:
1X СТАВИМ
1 СТАВИМ
X ИГНОРИРУЕМ
X2 СТАВИМ
2 ИГНОРИРУЕМ
1X ИГНОРИРУЕМ
1 ИГНОРИРУЕМ
X СТАВИМ
X2 ИГНОРИРУЕМ
2 СТАВИМ
"""
            
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + два fallback)
            models_to_try = [self.ai_primary_model, self.ai_fallback_model1, self.ai_fallback_model2]
            
            for model_idx, model in enumerate(models_to_try):
                if not model:
                    continue
                    
                print(f"[Football AI] Пробуем модель {model_idx + 1}/{len(models_to_try)}: {model}")
                
                try:
                    payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_tokens": 2000,
                        "temperature": 0.3  # Низкая температура для более детерминированного ответа
                    }
                    
                    print(f"[Football AI] Отправка запроса к OpenRouter API (модель: {model})")
                    
                    response = requests.post(
                        f"{self.openrouter_api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                ai_response = data['choices'][0]['message']['content']
                                print(f"[Football AI] Получен ответ длиной {len(ai_response)} символов от модели {model}")
                                
                                # Парсим ответ - ищем один из вариантов: 1, 1X, X, X2, 2
                                bet_ai = self._parse_ai_prediction(ai_response)
                                
                                if bet_ai:
                                    print(f"[Football AI] Успешно распознан прогноз: {bet_ai}")
                                    return bet_ai, ai_response
                                else:
                                    print(f"[Football AI] Не удалось распознать прогноз в ответе, пробуем следующую модель")
                                    if model_idx < len(models_to_try) - 1:
                                        continue
                                    else:
                                        # Последняя модель - возвращаем ответ даже если не распознан
                                        print(f"[Football AI] Все модели испробованы, возвращаем ответ без распознанного прогноза")
                                        return None, ai_response
                        except json.JSONDecodeError as e:
                            print(f"[Football AI ERROR] Ошибка парсинга JSON ответа: {e}")
                            continue
                    else:
                        print(f"[Football AI ERROR] Ошибка API: {response.status_code} - {response.text}")
                        continue
                        
                except requests.exceptions.RequestException as e:
                    print(f"[Football AI ERROR] Ошибка запроса к OpenRouter: {e}")
                    continue
            
            print(f"[Football AI] Не удалось получить прогноз от всех моделей")
            return None, None, None
            
        except Exception as e:
            print(f"[Football AI ERROR] Ошибка получения ИИ-прогноза: {e}")
            import traceback
            print(traceback.format_exc())
            return None, None, None

    def analyze_bet_risk(self, fixture_id: str, bet_ai: str, bet_ai_odds: float, stats_json: str) -> Optional[str]:
        """
        Анализирует риск ставки на основе прогноза ИИ, коэффициента и статистики.
        
        Args:
            fixture_id: ID матча
            bet_ai: Прогноз ИИ ('1', '1X', 'X', 'X2', '2')
            bet_ai_odds: Коэффициент на прогнозированный исход
            stats_json: JSON строка со статистикой матча (stats_60min)
        
        Returns:
            Ответ от ИИ с анализом риска или None в случае ошибки
        """
        if not self.openrouter_api_key:
            print("[Football] OpenRouter API ключ не установлен, пропускаем анализ риска")
            return None
        
        try:
            # Парсим статистику
            stats = json.loads(stats_json) if isinstance(stats_json, str) else stats_json
            
            # Получаем информацию о матче из БД
            conn = get_football_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM matches WHERE fixture_id = ?", (fixture_id,))
            match_row = cursor.fetchone()
            conn.close()
            
            if not match_row:
                print(f"[Football] Матч {fixture_id} не найден в БД")
                return None
            
            match = dict(match_row)
            home_team = match.get('home_team', '')
            away_team = match.get('away_team', '')
            
            score = stats.get('score', {})
            home_score = score.get('home', 0)
            away_score = score.get('away', 0)
            
            # Форматируем статистику для промпта
            stats_formatted = json.dumps(stats, ensure_ascii=False, indent=2)
            
            # Определяем название исхода
            outcome_names = {
                '1': f'победа домашней команды ({home_team})',
                '1X': f'ничья или победа домашней команды ({home_team})',
                'X': 'ничья',
                'X2': f'ничья или победа гостевой команды ({away_team})',
                '2': f'победа гостевой команды ({away_team})'
            }
            outcome_name = outcome_names.get(bet_ai.upper(), bet_ai)
            
            prompt = f"""Ты - эксперт по анализу рисков ставок на футбол. Твоя задача - проанализировать предложенную ставку и дать рекомендацию: стоит ли рисковать или нет.

Информация о матче:
- Команды: {home_team} vs {away_team}
- Текущий счет после первого тайма: {home_score} - {away_score}

Прогноз ИИ:
- Исход: {outcome_name} ({bet_ai})
- Коэффициент: {bet_ai_odds}

Детальная статистика первого тайма:
{stats_formatted}

Проанализируй статистику, текущий счет, прогноз ИИ и коэффициент. Дай обоснованную рекомендацию: СТОИТ ЛИ РИСКНУТЬ или НЕ СТОИТ РИСКОВАТЬ, и подробно объясни свое решение."""
            
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + два fallback)
            models_to_try = [self.risk_analysis_primary, self.risk_analysis_fallback1, self.risk_analysis_fallback2]
            
            for model_idx, model in enumerate(models_to_try):
                if not model:
                    continue
                    
                print(f"[Football Risk Analysis] Пробуем модель {model_idx + 1}/{len(models_to_try)}: {model}")
                
                try:
                    payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_tokens": 2000,
                        "temperature": 0.7  # Средняя температура для более развернутого ответа
                    }
                    
                    print(f"[Football Risk Analysis] Отправка запроса к OpenRouter API (модель: {model})")
                    
                    response = requests.post(
                        f"{self.openrouter_api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                ai_response = data['choices'][0]['message']['content']
                                print(f"[Football Risk Analysis] Получен ответ длиной {len(ai_response)} символов от модели {model}")
                                return ai_response
                            else:
                                print(f"[Football Risk Analysis] Неожиданный формат ответа от модели {model}")
                        except Exception as e:
                            print(f"[Football Risk Analysis] Ошибка парсинга ответа от модели {model}: {e}")
                    else:
                        print(f"[Football Risk Analysis] Ошибка API для модели {model}: статус {response.status_code}")
                        if response.status_code == 429:
                            print(f"[Football Risk Analysis] Превышен лимит запросов для модели {model}, пробуем следующую")
                            continue
                        elif response.status_code == 401:
                            print(f"[Football Risk Analysis] Ошибка авторизации для модели {model}")
                            break
                
                except requests.exceptions.Timeout:
                    print(f"[Football Risk Analysis] Таймаут при запросе к модели {model}")
                    continue
                except Exception as e:
                    print(f"[Football Risk Analysis] Ошибка при запросе к модели {model}: {e}")
                    continue
            
            print(f"[Football Risk Analysis] Не удалось получить ответ ни от одной модели")
            return None
            
        except Exception as e:
            print(f"[Football Risk Analysis ERROR] Ошибка анализа риска: {e}")
            import traceback
            print(traceback.format_exc())
            return None

    def _collect_final_result(self, match: sqlite3.Row):
        """
        Собирает финальный результат матча из SofaScore API.

        Args:
            match: Запись матча из БД
        """
        try:
            fixture_id = match['fixture_id']
            sofascore_event_id = match['sofascore_event_id'] if 'sofascore_event_id' in match.keys() and match['sofascore_event_id'] else None

            if not sofascore_event_id:
                print(f"[Football] У матча {fixture_id} нет sofascore_event_id, пропускаем сбор финального результата")
                return

            print(f"[Football] Получаем финальный результат из SofaScore для event_id {sofascore_event_id}")

            # Получаем полные данные о событии
            event_data = self._fetch_sofascore_event(sofascore_event_id)

            if not event_data:
                print(f"[Football] Не удалось получить данные из SofaScore для event_id {sofascore_event_id}")
                return

            # Извлекаем счет из данных SofaScore
            # Структура данных из /api/v1/event/{event_id}:
            # event.homeScore.current - счет домашней команды
            # event.awayScore.current - счет гостевой команды
            # Также доступны: display, normaltime, period1, period2
            score_home = None
            score_away = None

            event = event_data.get('event', {})
            
            # Основной способ: event.homeScore.current и event.awayScore.current
            home_score_obj = event.get('homeScore', {})
            away_score_obj = event.get('awayScore', {})
            
            if isinstance(home_score_obj, dict):
                # Приоритет: normaltime (обычное время) > current > display
                score_home = home_score_obj.get('normaltime') or home_score_obj.get('current') or home_score_obj.get('display')
            
            if isinstance(away_score_obj, dict):
                # Приоритет: normaltime (обычное время) > current > display
                score_away = away_score_obj.get('normaltime') or away_score_obj.get('current') or away_score_obj.get('display')

            if score_home is None or score_away is None:
                print(f"[Football] Не удалось извлечь счет из данных SofaScore для event_id {sofascore_event_id}")
                print(f"[Football] Доступные поля в event: {list(event.keys()) if event else 'N/A'}")
                print(f"[Football] Доступные поля в корне: {list(event_data.keys())}")
                # Попробуем вывести всю структуру для отладки
                import json
                print(f"[Football] Полная структура данных (первые 2000 символов): {json.dumps(event_data, indent=2, ensure_ascii=False)[:2000]}")
                return

            # Преобразуем счет в целые числа
            try:
                score_home = int(score_home) if score_home is not None else None
                score_away = int(score_away) if score_away is not None else None
            except (ValueError, TypeError):
                print(f"[Football] Ошибка преобразования счета в числа: home={score_home}, away={score_away}")
                return

            # Определяем, выиграл ли фаворит
            # fav_team_id: 1 = home, 0 = away
            fav_team_id = match['fav_team_id']
            fav_won = None

            if score_home > score_away:
                # Домашняя команда выиграла
                fav_won = 1 if fav_team_id == 1 else 0
            elif score_away > score_home:
                # Гостевая команда выиграла
                fav_won = 1 if fav_team_id == 0 else 0
            else:
                # Ничья
                fav_won = 0

            # Сохраняем
            conn = get_football_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE matches
                SET final_score_home = ?, final_score_away = ?,
                    fav_won = ?, status = 'finished', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (score_home, score_away, fav_won, match['id']))

            conn.commit()
            conn.close()

            print(f"[Football] Финальный результат сохранен для fixture {fixture_id}: {score_home}-{score_away}, фаворит выиграл: {fav_won == 1}")

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

    def _parse_sofascore_statistics(self, stats_data: Dict, match: sqlite3.Row) -> Dict:
        """
        Парсит статистику из SofaScore API.

        Args:
            stats_data: Сырые данные статистики от SofaScore
            match: Запись матча из БД

        Returns:
            Словарь с отпарсенной статистикой: {'score': {...}, 'possession': {...}, 'shots_on_target': {...}, 'xG': {...}}
        """
        stats = {}
        
        try:
            # Сохраняем весь ответ API в raw_data для полноты информации
            stats['raw_data'] = stats_data

            # Получаем текущий счет (для удобства выносим отдельно)
            home_score = stats_data.get('homeScore', {}).get('current', 0)
            away_score = stats_data.get('awayScore', {}).get('current', 0)
            stats['score'] = {
                'home': home_score,
                'away': away_score
            }

            # Сохраняем все остальные поля из API
            # Сохраняем периоды со всей статистикой
            if 'periods' in stats_data:
                stats['periods'] = stats_data['periods']
            
            # Сохраняем статистику напрямую (если есть)
            if 'statistics' in stats_data:
                stats['statistics'] = stats_data['statistics']
            
            # Сохраняем все остальные поля из API
            for key in stats_data:
                if key not in ['homeScore', 'awayScore', 'periods', 'statistics']:
                    stats[key] = stats_data[key]

            # Получаем статистику по группам (periods или statistics) для парсинга
            periods = stats_data.get('periods', [])
            statistics = stats_data.get('statistics', [])
            
            # Парсим часто используемые поля для удобства доступа
            # Извлекаем часто используемые поля из periods
            if periods:
                for period in periods:
                    if period.get('period') == 'all' or period.get('period') == 'REGULAR':
                        groups = period.get('groups', [])
                        parsed_stats = {}
                        for group in groups:
                            group_name = group.get('groupName', '')
                            stat_items = group.get('statisticsItems', [])
                            for item in stat_items:
                                item_name = item.get('name', '')
                                # Сохраняем все статистики из группы
                                if group_name not in parsed_stats:
                                    parsed_stats[group_name] = []
                                parsed_stats[group_name].append({
                                    'name': item_name,
                                    'home': item.get('home'),
                                    'away': item.get('away'),
                                    'total': item.get('total')
                                })
                        if parsed_stats:
                            stats['parsed_period_all'] = parsed_stats

            # Извлекаем часто используемые поля из statistics
            if statistics:
                parsed_stats = {}
                for stat_group in statistics:
                    if isinstance(stat_group, dict):
                        group_name = stat_group.get('groupName', '')
                        stat_items = stat_group.get('statisticsItems', [])
                        parsed_items = []
                        for item in stat_items:
                            parsed_items.append({
                                'name': item.get('name', ''),
                                'home': item.get('home'),
                                'away': item.get('away'),
                                'total': item.get('total')
                            })
                        if parsed_items:
                            parsed_stats[group_name] = parsed_items
                if parsed_stats:
                    stats['parsed_statistics'] = parsed_stats

            print(f"[Football] Распарсена полная статистика SofaScore: score={stats.get('score')}, сохранено {len(stats)} полей")
            
        except Exception as e:
            print(f"[Football ERROR] Ошибка парсинга статистики SofaScore: {e}")
            import traceback
            print(traceback.format_exc())
            # В случае ошибки всё равно сохраняем сырые данные
            stats = {'raw_data': stats_data}
            if 'homeScore' in stats_data and 'awayScore' in stats_data:
                stats['score'] = {
                    'home': stats_data.get('homeScore', {}).get('current', 0),
                    'away': stats_data.get('awayScore', {}).get('current', 0)
                }
        
        return stats

    def _extract_stat_value(self, stats: Dict, stat_group_name: str, stat_item_name: str) -> Dict[str, float]:
        """
        Извлекает значение статистики из новой структуры данных.
        
        Args:
            stats: Словарь со статистикой
            stat_group_name: Название группы статистики (например, 'Ball possession', 'Shots on target')
            stat_item_name: Название конкретной статистики (например, 'Ball possession', 'Shots on target')
        
        Returns:
            Словарь {'home': value, 'away': value} или пустой словарь если не найдено
        """
        result = {'home': 0, 'away': 0}
        
        # Пытаемся найти в parsed_period_all
        if 'parsed_period_all' in stats:
            parsed = stats['parsed_period_all']
            if stat_group_name in parsed:
                for item in parsed[stat_group_name]:
                    if item.get('name') == stat_item_name:
                        result['home'] = item.get('home', 0) or 0
                        result['away'] = item.get('away', 0) or 0
                        return result
        
        # Пытаемся найти в parsed_statistics
        if 'parsed_statistics' in stats:
            parsed = stats['parsed_statistics']
            if stat_group_name in parsed:
                for item in parsed[stat_group_name]:
                    if item.get('name') == stat_item_name:
                        result['home'] = item.get('home', 0) or 0
                        result['away'] = item.get('away', 0) or 0
                        return result
        
        # Пытаемся найти в raw_data через periods
        if 'raw_data' in stats:
            raw_data = stats['raw_data']
            periods = raw_data.get('periods', [])
            for period in periods:
                if period.get('period') == 'all' or period.get('period') == 'REGULAR':
                    groups = period.get('groups', [])
                    for group in groups:
                        if group.get('groupName') == stat_group_name:
                            stat_items = group.get('statisticsItems', [])
                            for item in stat_items:
                                if item.get('name') == stat_item_name:
                                    result['home'] = item.get('home', 0) or 0
                                    result['away'] = item.get('away', 0) or 0
                                    return result
        
        # Пытаемся найти в raw_data через statistics
        if 'raw_data' in stats:
            raw_data = stats['raw_data']
            statistics = raw_data.get('statistics', [])
            for stat_group in statistics:
                if isinstance(stat_group, dict) and stat_group.get('groupName') == stat_group_name:
                    stat_items = stat_group.get('statisticsItems', [])
                    for item in stat_items:
                        if item.get('name') == stat_item_name:
                            result['home'] = item.get('home', 0) or 0
                            result['away'] = item.get('away', 0) or 0
                            return result
        
        return result

    def _calculate_bet(self, match: sqlite3.Row, stats: Dict, fixture_id: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Рассчитывает значение bet на основе решения ИИ.

        Вместо эвристик (владение, xG и т.д.) используется ИИ, который анализирует
        всю доступную статистику и решает ДА/НЕТ.

        Args:
            match: Запись матча
            stats: Статистика на 60-й минуте (от SofaScore, с raw_data)
            fixture_id: ID матча в The Odds API

                Returns:
            Кортеж (bet_value, live_odds, ai_decision, ai_reason):
            - bet_value: Коэффициент live odds если ИИ ответил ДА, 0 если НЕТ, 1 если лимит API исчерпан
            - live_odds: Реальное значение live odds из API (может быть None если не удалось получить)
            - ai_decision: Решение ИИ (True = ДА, False = НЕТ, None = ошибка)
            - ai_reason: Полный ответ от ИИ или None
        """
        try:
            fav_team = match['fav']

            # Получаем решение от ИИ
            print(f"[Football] Запрашиваем решение ИИ для матча {fixture_id}...")
            is_yes, ai_reason = self._get_bet_ai_decision(match, stats)

            if is_yes is None:
                # Не удалось получить ответ от ИИ - не делаем ставку
                print(f"[Football] Не удалось получить решение ИИ для матча {fixture_id}, устанавливаем bet=0")
                return (0, None, None, ai_reason)

            if not is_yes:
                # ИИ ответил НЕТ - не делаем ставку
                print(f"[Football] ИИ ответил НЕТ для матча {fixture_id}: {ai_reason[:200] if ai_reason else 'N/A'}...")
                return (0, None, False, ai_reason)

            # ИИ ответил ДА - запрашиваем live odds
            print(f"[Football] ИИ ответил ДА для матча {fixture_id}. Запрашиваем live odds...")
            sport_key = match['sport_key'] if 'sport_key' in match.keys() else None
            live_odds = self._get_live_odds(fixture_id, sport_key)

            if live_odds is None:
                # Если не удалось получить live odds (лимит исчерпан или матч не найден), сохраняем 1 в bet
                print(f"[Football] Не удалось получить live odds для {fixture_id}, сохраняем bet=1, live_odds=NULL")
                return (1, None, True, ai_reason)

            print(f"[Football] Получены live odds для фаворита {fav_team}: {live_odds}")
            return (live_odds, live_odds, True, ai_reason)

        except Exception as e:
            print(f"[Football ERROR] Ошибка расчета bet: {e}")
            import traceback
            print(traceback.format_exc())
            return (0, None, None, None)

    def _send_match_notification(self, match: sqlite3.Row, stats: Dict, live_odds: Optional[float], ai_decision: Optional[bool], ai_reason: Optional[str]) -> bool:
        """
        Отправляет уведомление в Telegram админу о матче, если фаворит не выигрывает.

        Args:
            match: Запись матча из БД
            stats: Статистика на 60-й минуте
            live_odds: Коэффициент live odds (K60)
            ai_decision: Решение ИИ (True = ДА, False = НЕТ, None = ошибка)
            ai_reason: Полный ответ от ИИ

        Returns:
            bool: True если уведомление отправлено успешно
        """
        if not TELEGRAM_AVAILABLE:
            return False

        try:
            # Проверяем условие: фаворит не выигрывает
            score = stats.get('score', {})
            home_score = score.get('home', 0)
            away_score = score.get('away', 0)

            home_team = match['home_team']
            away_team = match['away_team']
            fav_team = match['fav']
            fav_is_home = (fav_team == home_team)

            # Вычисляем разницу в счете с точки зрения фаворита
            if fav_is_home:
                fav_score = home_score
                opp_score = away_score
            else:
                fav_score = away_score
                opp_score = home_score

            score_diff = opp_score - fav_score  # Положительное значение = фаворит проигрывает

            # Отправляем уведомление только если фаворит не выигрывает (score_diff >= 0)
            if score_diff < 0:
                print(f"[Football] Фаворит {fav_team} выигрывает ({fav_score}-{opp_score}), пропускаем уведомление")
                return False

            # Формируем решение ИИ для сообщения
            ai_decision_text = "ДА" if ai_decision is True else ("НЕТ" if ai_decision is False else "ОШИБКА")
            ai_reason_short = (ai_reason[:200] + "...") if ai_reason and len(ai_reason) > 200 else (ai_reason or "Нет данных")

            # Формируем сообщение
            message = f"""
⚽ <b>Футбольная аналитика - уведомление</b>

🏟️ <b>Матч:</b> {home_team} vs {away_team}
📊 <b>Счет:</b> {home_score} - {away_score}
⭐ <b>Фаворит:</b> {fav_team}
💰 <b>K60:</b> {live_odds if live_odds else 'N/A'}

🤖 <b>Решение ИИ:</b> {ai_decision_text}
📝 <b>Обоснование:</b> {ai_reason_short}
            """.strip()

            # Отправляем уведомление админу
            if telegram_notifier.send_message(message):
                print(f"[Football] Уведомление отправлено админу для матча {match['fixture_id']}")
                return True
            else:
                print(f"[Football] Ошибка отправки уведомления для матча {match['fixture_id']}")
                return False

        except Exception as e:
            print(f"[Football ERROR] Ошибка отправки уведомления: {e}")
            import traceback
            print(traceback.format_exc())
            return False

    def _get_ai_prediction(self, match: sqlite3.Row, stats: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Получает прогноз от ИИ на основе статистики матча.
        
        Args:
            match: Запись матча из БД
            stats: Статистика на 60-й минуте (из stats_60min)
        
        Returns:
            Кортеж (bet_ai, bet_ai_reason):
            - bet_ai: Прогноз ('1', '1X', 'X', 'X2', '2') или None
            - bet_ai_reason: Полный ответ от ИИ или None
        """
        if not self.openrouter_api_key:
            print("[Football] OpenRouter API ключ не установлен, пропускаем ИИ-прогноз")
            return None, None
        
        try:
            # Формируем промпт
            home_team = match['home_team']
            away_team = match['away_team']
            fav = match['fav']
            initial_odds = match['initial_odds'] if 'initial_odds' in match.keys() and match['initial_odds'] is not None else '-'
            last_odds = match['last_odds'] if 'last_odds' in match.keys() and match['last_odds'] is not None else '-'
            
            score = stats.get('score', {})
            home_score = score.get('home', 0)
            away_score = score.get('away', 0)
            
            # Форматируем статистику как JSON для передачи ИИ
            stats_json = json.dumps(stats, ensure_ascii=False, indent=2)
            
            prompt = f"""Ты - спортивный аналитик. Изучи предоставленную статистику матча после первого тайма, хорошо подумай и сделай прогноз на итоговый результат матча в основное время.

Информация о матче:
- Команды: {home_team} vs {away_team}
- Фаворит: {fav} (текущий коэффициент ставки на победу фаворита {last_odds})
- Текущий счет после первого тайма: {home_score} - {away_score}

Детальная статистика первого тайма:
{stats_json}

Ответ верни ТОЛЬКО в виде одного из вариантов: 1 или 1X или X или X2 или 2
Где:
- 1 = победа домашней команды ({home_team})
- 1X = ничья или победа домашней команды ({home_team})
- X = ничья
- X2 = ничья или победа гостевой команды ({away_team})
- 2 = победа гостевой команды ({away_team})"""
            
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + два fallback)
            models_to_try = [self.ai_primary_model, self.ai_fallback_model1, self.ai_fallback_model2]
            
            for model_idx, model in enumerate(models_to_try):
                if not model:
                    continue
                    
                print(f"[Football AI] Пробуем модель {model_idx + 1}/{len(models_to_try)}: {model}")
                
                try:
                    payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_tokens": 2000,
                        "temperature": 0.3  # Низкая температура для более детерминированного ответа
                    }
                    
                    print(f"[Football AI] Отправка запроса к OpenRouter API (модель: {model})")
                    
                    response = requests.post(
                        f"{self.openrouter_api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                ai_response = data['choices'][0]['message']['content']
                                print(f"[Football AI] Получен ответ длиной {len(ai_response)} символов от модели {model}")
                                
                                # Парсим ответ - ищем один из вариантов: 1, 1X, X, X2, 2
                                bet_ai = self._parse_ai_prediction(ai_response)
                                
                                if bet_ai:
                                    print(f"[Football AI] Успешно распознан прогноз: {bet_ai}")
                                    return bet_ai, ai_response
                                else:
                                    print(f"[Football AI] Не удалось распознать валидный прогноз в ответе: {ai_response[:200]}...")
                                    # Продолжаем с следующей моделью
                                    continue
                            else:
                                print(f"[Football AI] Неверный формат ответа от OpenRouter API для модели {model}")
                                continue
                        except json.JSONDecodeError as e:
                            print(f"[Football AI] Ошибка парсинга JSON для модели {model}: {e}")
                            continue
                    else:
                        print(f"[Football AI] HTTP ошибка OpenRouter API для модели {model}: {response.status_code}")
                        try:
                            error_details = response.json()
                            print(f"[Football AI] Детали ошибки: {error_details}")
                            
                            # Если это ошибка 503 "No instances available", переходим к следующей модели
                            if response.status_code == 503 and "No instances available" in str(error_details):
                                print(f"[Football AI] Модель {model} недоступна (503), переходим к следующей")
                                continue
                        except:
                            print(f"[Football AI] Текст ошибки: {response.text[:500]}...")
                        continue
                        
                except requests.exceptions.Timeout:
                    print(f"[Football AI] Таймаут запроса к модели {model}")
                    continue
                except requests.exceptions.RequestException as e:
                    print(f"[Football AI] Ошибка запроса к модели {model}: {e}")
                    continue
                except Exception as e:
                    print(f"[Football AI] Неожиданная ошибка при запросе к модели {model}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
            
            # Если все модели не дали валидного ответа
            print("[Football AI] Все модели не дали валидного прогноза")
            return None, None
            
        except Exception as e:
            print(f"[Football AI ERROR] Ошибка получения ИИ-прогноза: {e}")
            import traceback
            print(traceback.format_exc())
            return None, None
    
    def _parse_ai_prediction(self, ai_response: str) -> Optional[str]:
        """
        Парсит ответ ИИ и извлекает прогноз (1, 1X, X, X2, 2).
        
        Args:
            ai_response: Полный ответ от ИИ
        
        Returns:
            Прогноз ('1', '1X', 'X', 'X2', '2') или None если не найден
        """
        # Ищем один из вариантов в ответе (регистронезависимо)
        # Используем word boundary чтобы не захватывать часть других слов
        valid_predictions = ['1X', 'X2', '1', 'X', '2']
        
        # Сначала ищем двухсимвольные варианты (1X, X2), потом односимвольные
        for pred in valid_predictions:
            # Используем регулярное выражение для поиска точного совпадения
            pattern = r'\b' + re.escape(pred) + r'\b'
            if re.search(pattern, ai_response, re.IGNORECASE):
                return pred.upper()
        
        return None

    def _parse_ai_recommendation(self, ai_response: str) -> bool:
        """
        Парсит ответ ИИ и извлекает рекомендацию (СТАВИМ/ИГНОРИРУЕМ).
        
        Args:
            ai_response: Полный ответ от ИИ
        
        Returns:
            True если найдено "СТАВИМ", False если "ИГНОРИРУЕМ" или не найдено
        """
        # Ищем слово "СТАВИМ" (регистронезависимо)
        if re.search(r'\bСТАВИМ\b', ai_response, re.IGNORECASE):
            return True
        return False

    def _get_bet_ai_decision(self, match: sqlite3.Row, stats: Dict) -> Tuple[Optional[bool], Optional[str]]:
        """
        Получает решение ИИ о том, стоит ли делать ставку (ДА/НЕТ) на основе статистики матча.

        Args:
            match: Запись матча из БД
            stats: Статистика на 60-й минуте (из stats_60min, с raw_data)

        Returns:
            Кортеж (is_yes, ai_reason):
            - is_yes: True если ИИ ответил ДА, False если НЕТ, None если ошибка
            - ai_reason: Полный ответ от ИИ или None
        """
        if not self.openrouter_api_key:
            print("[Football] OpenRouter API ключ не установлен, пропускаем ИИ-решение для bet")
            return None, None

        try:
            # Формируем промпт
            home_team = match['home_team']
            away_team = match['away_team']
            fav = match['fav']
            initial_odds = match['initial_odds'] if 'initial_odds' in match.keys() and match['initial_odds'] is not None else '-'
            last_odds = match['last_odds'] if 'last_odds' in match.keys() and match['last_odds'] is not None else '-'
            
            # Получаем текущий счет
            score = stats.get('score', {})
            home_score = score.get('home', 0)
            away_score = score.get('away', 0)
            
            # Берем сырую статистику из raw_data
            raw_stats = stats.get('raw_data', {})
            
            # Сериализуем статистику в JSON для промпта
            import json
            stats_json = json.dumps(raw_stats, ensure_ascii=False, indent=2)
            
            prompt = f"""Ты - футбольный аналитик. Сейчас начинается второй тайм матча фаворита с аутсайдером. Твоя задача - тщательно изучить подробную статистику первого тайма и дать прогноз на исход. Если по твоему мнению аутсайдер играет на пределах своих возможностей и везения, а фаворит полностью контролирует игру и способен выиграть этот матч при текущем счете - ответь ДА. Если шансы на победу фаворита сомнительны из-за удивительно слаженных действия аутсайдера и растерянности фаворита - ответь НЕТ. Ответ должен состоять только из ДА или НЕТ.

Информация о матче:
- Команды: {home_team} vs {away_team}
- Фаворит: {fav} (текущий коэффициент ставки на победу фаворита {last_odds})
- Текущий счет после первого тайма: {home_score} - {away_score}

Статистика первого тайма:
{stats_json}"""

            # Заголовки для OpenRouter API
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "HTTP-Referer": "https://github.com",
                "X-Title": "Football Bet Analysis"
            }

            # Пробуем все доступные модели
            models_to_try = [self.ai_primary_model, self.ai_fallback_model1, self.ai_fallback_model2]

            for model_idx, model in enumerate(models_to_try):
                if not model:
                    continue

                print(f"[Football Bet AI] Пробуем модель {model_idx + 1}/{len(models_to_try)}: {model}")

                try:
                    payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_tokens": 500,
                        "temperature": 0.3  # Низкая температура для более детерминированного ответа
                    }

                    print(f"[Football Bet AI] Отправка запроса к OpenRouter API (модель: {model})")

                    response = requests.post(
                        f"{self.openrouter_api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=60
                    )

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                ai_response = data['choices'][0]['message']['content']
                                print(f"[Football Bet AI] Получен ответ длиной {len(ai_response)} символов от модели {model}")

                                # Парсим ответ - ищем ДА или НЕТ
                                is_yes = self._parse_bet_ai_response(ai_response)

                                if is_yes is not None:
                                    print(f"[Football Bet AI] Успешно распознан ответ: {'ДА' if is_yes else 'НЕТ'}")
                                    return is_yes, ai_response
                                else:
                                    print(f"[Football Bet AI] Не удалось распознать ДА/НЕТ в ответе: {ai_response[:200]}...")
                                    # Продолжаем с следующей моделью
                                    continue
                            else:
                                print(f"[Football Bet AI] Неверный формат ответа от OpenRouter API для модели {model}")
                                continue
                        except json.JSONDecodeError as e:
                            print(f"[Football Bet AI] Ошибка парсинга JSON для модели {model}: {e}")
                            continue
                    else:
                        print(f"[Football Bet AI] HTTP ошибка OpenRouter API для модели {model}: {response.status_code}")
                        try:
                            error_details = response.json()
                            print(f"[Football Bet AI] Детали ошибки: {error_details}")

                            # Если это ошибка 503 "No instances available", переходим к следующей модели
                            if response.status_code == 503 and "No instances available" in str(error_details):
                                print(f"[Football Bet AI] Модель {model} недоступна (503), переходим к следующей")
                                continue
                        except:
                            print(f"[Football Bet AI] Текст ошибки: {response.text[:500]}...")
                        continue

                except requests.exceptions.Timeout:
                    print(f"[Football Bet AI] Таймаут запроса к модели {model}")
                    continue
                except requests.exceptions.RequestException as e:
                    print(f"[Football Bet AI] Ошибка запроса к модели {model}: {e}")
                    continue
                except Exception as e:
                    print(f"[Football Bet AI] Неожиданная ошибка при запросе к модели {model}: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue

            # Если все модели не дали валидного ответа
            print("[Football Bet AI] Все модели не дали валидного ответа")
            return None, None

        except Exception as e:
            print(f"[Football Bet AI ERROR] Ошибка получения ИИ-решения: {e}")
            import traceback
            print(traceback.format_exc())
            return None, None

    def _parse_bet_ai_response(self, ai_response: str) -> Optional[bool]:
        """
        Парсит ответ ИИ и извлекает ДА/НЕТ.

        Args:
            ai_response: Полный ответ от ИИ

        Returns:
            True если ДА, False если НЕТ, None если не найден
        """
        # Ищем ДА или НЕТ в ответе (регистронезависимо)
        # Используем word boundary чтобы не захватывать часть других слов
        response_upper = ai_response.upper().strip()
        
        # Ищем ДА (может быть написано как "ДА", "ДА.", "ДА!", "ДА," и т.д.)
        if re.search(r'\bДА\b', response_upper):
            return True
        
        # Ищем НЕТ (может быть написано как "НЕТ", "НЕТ.", "НЕТ!", "НЕТ," и т.д.)
        if re.search(r'\bНЕТ\b', response_upper):
            return False
        
        return None

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


def get_all_matches(filter_fav: bool = True) -> List[Dict[str, Any]]:
    """
    Получает матчи для UI.
    
    Args:
        filter_fav: Если True, возвращает только матчи с фаворитом (fav != 'NONE').
                    Если False, возвращает все матчи.

    Returns:
        Список матчей
    """
    conn = None
    try:
        conn = get_football_db_connection()
        cursor = conn.cursor()

        if filter_fav:
            cursor.execute("""
                SELECT * FROM matches
                WHERE fav != 'NONE'
                ORDER BY match_date DESC, match_time DESC
            """)
        else:
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

