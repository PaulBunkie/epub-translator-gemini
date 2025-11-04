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

# Список лиг для сбора матчей (можно переопределить через FOOTBALL_LEAGUES в .env)
# Формат: "soccer_epl,soccer_spain_la_liga,soccer_germany_bundesliga" и т.д.
# Если не указано, используется список по умолчанию (топ-лиги)
DEFAULT_FOOTBALL_LEAGUES = [
    "soccer_epl",                    # Английская Премьер-лига
    "soccer_spain_la_liga",          # Ла Лига
    "soccer_italy_serie_a",          # Серия A
    "soccer_germany_bundesliga",     # Бундеслига
    "soccer_france_ligue_one",       # Лига 1
    "soccer_netherlands_eredivisie", # Эредивизи
    "soccer_portugal_primeira_liga", # Примейра Лига
    "soccer_uefa_champs_league",     # Лига Чемпионов
    "soccer_uefa_europa_league",     # Лига Европы
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
        
        print("[Football] Менеджер инициализирован")

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
                        if match_exists:
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

