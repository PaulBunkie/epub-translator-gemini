import requests
from datetime import timedelta, datetime
import isodate
import os
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# Импортируем наши модули
import video_db
import video_analyzer

# Константы
DAYS = 3
Q_TEMPLATE = "interview|интервью|беседа|обзор|разговор|репортаж|дудь|вдудь|варламов|собчак|лебедев|rogan|tucker|Ferriss|Musk|редакция|TheDiaryOfACEO|investigation|расследование"
load_dotenv()
API_KEY = os.getenv('YOUTUBE_API_KEY')

# URLs для YouTube API
videos_url = "https://www.googleapis.com/youtube/v3/videos"
search_url = "https://www.googleapis.com/youtube/v3/search"
regions = ["RU", "US", "GB"]

class TopTubeManager:
    """
    Менеджер для сбора и анализа популярных YouTube видео.
    """
    
    def __init__(self):
        self.api_key = API_KEY
        if not self.api_key:
            raise ValueError("Не установлена переменная окружения YOUTUBE_API_KEY")
        
        # Инициализируем БД
        video_db.init_video_db()
        
        print("[TopTube] Менеджер инициализирован")
    
    def collect_videos(self, pages_to_fetch: int = 20) -> int:
        """
        Собирает популярные видео с YouTube и сохраняет в БД.
        
        Args:
            pages_to_fetch: Количество страниц для сбора
            
        Returns:
            Количество собранных видео
        """
        print(f"[TopTube] Начинаем сбор видео (страниц: {pages_to_fetch})")
        
        all_videos = []
        published_after = (datetime.now().astimezone() - timedelta(days=DAYS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # Сбор mostPopular по регионам
        # ПРИМЕЧАНИЕ: параметр eventType не поддерживается для mostPopular, 
        # поэтому фильтрацию стримов делаем в _should_save_video()
        for region in regions:
            page_token = None
            print(f"[TopTube] Сбор mostPopular для региона {region}")
            
            for page_num in range(1, pages_to_fetch + 1):
                params = {
                    "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                    "chart": "mostPopular",
                    "regionCode": region,
                    "maxResults": 50,
                    "key": self.api_key
                }
                if page_token:
                    params["pageToken"] = page_token
                
                try:
                    resp = requests.get(videos_url, params=params, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get("items", [])
                    
                    print(f"[TopTube] {region} — страница {page_num}: получено {len(items)} видео")
                    all_videos.extend(items)
                    
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        print(f"[TopTube] {region}: достигнут конец выдачи")
                        break
                        
                except Exception as e:
                    print(f"[TopTube] Ошибка при сборе {region} страница {page_num}: {e}")
                    break
        
        # Сбор через search.list по ключевым словам
        print(f"[TopTube] Сбор через search.list с q='{Q_TEMPLATE}'")
        
        search_page_token = None
        search_page_num = 1
        max_search_pages = 5  # Максимум 2 страницы поиска (было 5)
        
        while search_page_num <= max_search_pages:
            try:
                search_params = {
                    "part": "snippet",
                    "type": "video",
                    "publishedAfter": published_after,
                    "q": Q_TEMPLATE,
                    "order": "viewCount",
                    "videoDuration": "long",
                    "eventType": "completed",  # Исключает активные и предстоящие стримы
                    "maxResults": 100,
                    "key": self.api_key
                }
                
                # Исключаем игровые видео - категория Gaming имеет ID 20
                # Но параметр videoCategoryId работает только как включающий фильтр,
                # поэтому фильтрацию игр делаем в _should_save_video()
                
                if search_page_token:
                    search_params["pageToken"] = search_page_token
                
                search_resp = requests.get(search_url, params=search_params, timeout=30)
                search_resp.raise_for_status()
                search_data = search_resp.json()
                search_items = search_data.get("items", [])
                
                print(f"[TopTube] search.list страница {search_page_num}: получено {len(search_items)} видео")
                
                # Для search.list нужно получить детали видео
                search_video_ids = [item["id"]["videoId"] for item in search_items if "videoId" in item["id"]]
                if search_video_ids:
                    details_params = {
                        "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                        "id": ",".join(search_video_ids),
                        "key": self.api_key
                    }
                    details_resp = requests.get(videos_url, params=details_params, timeout=30)
                    details_resp.raise_for_status()
                    details_data = details_resp.json()
                    details_items = details_data.get("items", [])
                    all_videos.extend(details_items)
                
                # Проверяем, есть ли следующая страница
                search_page_token = search_data.get("nextPageToken")
                if not search_page_token:
                    print(f"[TopTube] search.list: достигнут конец выдачи")
                    break
                
                search_page_num += 1
                
            except Exception as e:
                print(f"[TopTube] Ошибка при поиске по ключевым словам страница {search_page_num}: {e}")
                break
        
        print(f"[TopTube] Всего получено видео: {len(all_videos)}")
        
        # Получаем статистику каналов
        channel_ids = list({v["snippet"]["channelId"] for v in all_videos})
        channels_dict = self._get_channels_info(channel_ids)
        
        # Фильтруем и сохраняем видео
        saved_count = 0
        for video in all_videos:
            try:
                if self._should_save_video(video, channels_dict):
                    video_data = self._prepare_video_data(video, channels_dict)
                    if video_db.add_video(video_data):
                        saved_count += 1
            except Exception as e:
                print(f"[TopTube] Ошибка при обработке видео {video.get('id', 'unknown')}: {e}")
        
        print(f"[TopTube] Сохранено в БД: {saved_count} видео")
        
        # Сохраняем информацию о сборе
        video_db.save_collection_info(saved_count, 'manual')
        
        return saved_count
    
    def analyze_single_video(self, video_data: Dict[str, Any]) -> bool:
        """
        Анализирует одно видео.
        
        Args:
            video_data: Данные видео из БД
            
        Returns:
            True в случае успеха, False в случае ошибки
        """
        try:
            print(f"[TopTube] Анализируем видео: {video_data['title']}")
            
            # Обновляем статус на "processing"
            video_db.update_video_status(video_data['id'], 'processing')
            
            # Получаем существующие данные анализа из базы
            existing_analysis = video_db.get_analysis_by_video_id(video_data['id'])
            
            # Анализируем видео
            analyzer = video_analyzer.VideoAnalyzer()
            result = analyzer.analyze_video(video_data['url'], existing_analysis)
            
            # Сохраняем результат
            success = video_db.save_analysis(video_data['id'], result)
            
            if result.get('error'):
                print(f"[TopTube] Ошибка анализа видео {video_data['title']}: {result['error']}")
                # Переводим статус в 'error', чтобы не брать видео снова
                video_db.update_video_status(video_data['id'], 'error')
                return False
            else:
                print(f"[TopTube] Видео {video_data['title']} успешно проанализировано")
                return True
                
        except Exception as e:
            import traceback
            print(f"[TopTube] Ошибка при анализе видео {video_data.get('title', 'unknown')}: {e}")
            print(f"[TopTube] Полный traceback:")
            print(traceback.format_exc())
            # Переводим статус в 'error', чтобы не брать видео снова
            video_db.update_video_status(video_data['id'], 'error')
            return False
    
    def _get_channels_info(self, channel_ids: List[str]) -> Dict[str, Any]:
        """Получает информацию о каналах."""
        channels_dict = {}
        channels_url = "https://www.googleapis.com/youtube/v3/channels"
        
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i+50]
            try:
                channels_params = {
                    "part": "statistics,snippet",
                    "id": ",".join(batch),
                    "key": self.api_key
                }
                channels_response = requests.get(channels_url, params=channels_params, timeout=30)
                channels_response.raise_for_status()
                channels_data = channels_response.json()
                
                for c in channels_data.get("items", []):
                    channels_dict[c["id"]] = c
                    
            except Exception as e:
                print(f"[TopTube] Ошибка при получении информации о каналах: {e}")
        
        return channels_dict
    
    def _is_stream_video(self, title: str) -> bool:
        """Проверяет, является ли видео стримом по названию."""
        title_lower = title.lower()
        
        # Сильные индикаторы стрима (если есть - точно стрим)
        strong_stream_indicators = [
            'прямой эфир', 'в прямом эфире', 'прямая трансляция', 
            'онлайн трансляция', 'live stream', 'livestream',
            'going live', 'streaming now', 'стримим'
        ]
        
        for indicator in strong_stream_indicators:
            if indicator in title_lower:
                return True
        
        # Проверяем эмодзи live (красный круг)
        if '🔴' in title:
            return True
        
        # Контекстные проверки для "стрим" и "stream"
        import re
        
        # Паттерны для стримов
        stream_patterns = [
            r'\bстрим\b.*\b(запись|завершен|окончен|закончен)\b',  # "стрим запись", "завершенный стрим"
            r'\b(завершен|окончен|закончен).*\bстрим\b',            # "завершенный стрим"
            r'\bстрим\b.*\b(прямо|сейчас|онлайн)\b',                # "стрим прямо сейчас"
            r'\b(полный|весь)\s+стрим\b',                           # "полный стрим"
            r'\bстрим\s+(по|игр|ночи|до)\b',                       # "стрим по игре", "стрим всю ночь"
            r'\bstream\b.*\b(record|vod|full)\b',                   # "stream record"
            r'\b(full|complete).*\bstream\b',                       # "full stream"
            r'\bstream\b.*\b(now|live|today)\b',                    # "stream now"
        ]
        
        for pattern in stream_patterns:
            if re.search(pattern, title_lower):
                return True
        
        # Исключения - когда "стрим" используется в обычном контексте
        normal_usage_patterns = [
            r'\bстрим\b.*\b(был|вчера|позавчера|недавно|хорош|класс|понравил)\b',  # "стрим был классный"
            r'\b(смотрел|видел|помню)\b.*\bстрим\b',                                # "смотрел стрим"
            r'\bстрим\b.*\b(канал|автор)\b',                                        # "стримера канал"
        ]
        
        for pattern in normal_usage_patterns:
            if re.search(pattern, title_lower):
                return False  # НЕ стрим
        
        # Простая проверка отдельных слов (но не в исключениях выше)
        simple_keywords = ['live', 'эфир', 'трансляция']
        for keyword in simple_keywords:
            if keyword in title_lower:
                return True
                
        return False

    def _is_gaming_video(self, video: Dict[str, Any], channel_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли видео игровым контентом."""
        
        # 1. Проверяем категорию видео (categoryId = 20 = Gaming)
        category_id = video["snippet"].get("categoryId", "")
        if category_id == "20":
            return True
        
        # 2. Анализ названия видео
        title = video["snippet"]["title"].lower()
        
        # Сначала проверяем исключения - когда слова используются в НЕ игровом контексте
        non_gaming_patterns = [
            r'\bигра\s+(света|теней|цвета|слов|престолов)',  # "игра света", "игра престолов"
            r'\b(политическая|экономическая|финансовая)\s+игра\b',  # политические/экономические игры
            r'\bигра\s+(актер|актрис|исполнени)',  # актерская игра
            r'\bgame\s+of\s+thrones\b',  # Game of Thrones
            r'\bvideo\s+game\s+(music|soundtrack|composer)\b',  # музыка из игр (не сами игры)
            r'\b(олимпийские|спортивные)\s+игры\b',  # спортивные игры
        ]
        
        import re
        for pattern in non_gaming_patterns:
            if re.search(pattern, title):
                return False  # НЕ игровой контент
        
        # Игровые ключевые слова
        gaming_keywords = [
            # Популярные игры
            'minecraft', 'fortnite', 'roblox', 'gta', 'fifa', 'pubg', 'valorant', 
            'league of legends', 'dota', 'cs:go', 'counter-strike', 'overwatch',
            'world of warcraft', 'wow', 'call of duty', 'cod', 'apex legends',
            'among us', 'fall guys', 'rocket league', 'cyberpunk', 'witcher',
            'resident evil', 'silent hill', 'final fantasy', 'zelda', 'mario',
            'pokemon', 'skyrim', 'fallout', 'assassin\'s creed', 'battlefield',
            'destiny', 'elden ring', 'dark souls', 'sekiro', 'bloodborne',
            
            # Игровые термины на русском (исключили общие слова)
            'играю', 'геймплей', 'летсплей', 'прохождение',
            'обзор игры', 'игровой', 'геймер', 'пвп', 'рейд', 'данж', 'квест',
            'майнкрафт', 'роблокс', 'фортнайт', 'танки', 'варфейс', 'дота',
            'челлендж', 'марафон', 'турнир',
            
            # Игровые термины на английском
            'gameplay', 'gaming', 'playthrough', 'walkthrough', 'lets play',
            'game review', 'gaming setup', 'speedrun', 'boss fight', 'pvp',
            'mmo', 'rpg', 'fps', 'moba', 'battle royale', 'esports',
            'challenge', 'marathon', 'tournament', 'ranked', 'grinding',
            
            # Игровые платформы
            'steam', 'epic games', 'battle.net', 'playstation', 'xbox', 'nintendo switch',
            'mobile gaming', 'android games', 'ios games',
            
            # Игровое оборудование
            'rtx', 'gaming pc', 'gaming laptop', 'mouse', 'keyboard gaming',
            'headset', 'monitor gaming', 'игровая мышь', 'игровая клавиатура'
        ]
        
        # Проверяем наличие игровых слов в названии
        for keyword in gaming_keywords:
            if keyword in title:
                return True
        
        # 3. Анализ названия канала
        channel_title = video["snippet"]["channelTitle"].lower()
        
        # Известные игровые стримеры/каналы
        known_gaming_channels = [
            'jynxzi', 'penguinz0', 'moistcr1tikal', 'xqc', 'shroud', 'ninja',
            'pokimane', 'tfue', 'summit1g', 'lirik', 'sodapoppin', 'asmongold',
            'игромания', 'stopgame', 'gameland', 'caramba tv', 'treshbox'
        ]
        
        # Проверяем известных геймеров
        for known_channel in known_gaming_channels:
            if known_channel in channel_title:
                return True
        
        gaming_channel_indicators = [
            'gaming', 'games', 'gamer', 'play', 'stream', 'esports',
            'игры', 'геймер', 'игровой', 'плей', 'стрим'
        ]
        
        for indicator in gaming_channel_indicators:
            if indicator in channel_title:
                return True
        
        # 4. Паттерны в названиях, характерные для игрового контента
        
        gaming_patterns = [
            r'\b(играю|играем)\s+в\s+\w+',          # "играю в Minecraft"
            r'\b\w+\s+(gameplay|летсплей)\b',        # "Cyberpunk gameplay"
            r'\b(обзор|review)\s+игры\b',            # "обзор игры"
            r'\b(прохождение|walkthrough)\s+\w+',    # "прохождение Skyrim"
            r'\b\w+\s+(стрим|stream)\b',             # "Dota стрим"
            r'\b(новая|new)\s+(игра|game)\b',        # "новая игра"
            r'\b(лучшие|best)\s+(игры|games)\b',     # "лучшие игры"
            r'\bтоп\s+\d+\s+игр\b',                  # "топ 10 игр"
            r'\b\w+\s+vs\s+\w+.*challenge\b',       # "X vs Y challenge"
            r'\b\w+\s+marathon\b',                   # "game marathon"
            r'\b(99-0|100-0|no death)\s+challenge\b', # "99-0 challenge"
            r'\b\w+\s+(турнир|tournament)\b',        # игровые турниры
        ]
        
        for pattern in gaming_patterns:
            if re.search(pattern, title):
                return True
        
        return False

    def _should_save_video(self, video: Dict[str, Any], channels_dict: Dict[str, Any]) -> bool:
        """Проверяет, нужно ли сохранять видео."""
        try:
            # Проверяем liveBroadcastContent - исключаем стримы на уровне API
            live_broadcast_content = video["snippet"].get("liveBroadcastContent", "none")
            if live_broadcast_content in ["live", "upcoming"]:
                print(f"[TopTube] Видео является стримом (API): {video['snippet']['title'][:50]}... — пропускаем")
#                return False
            
            # Проверяем liveStreamingDetails - исключаем ЗАВЕРШЕННЫЕ стримы
            live_streaming_details = video.get("liveStreamingDetails", {})
            if live_streaming_details:
                # Если есть actualEndTime - это завершенный стрим
                actual_end_time = live_streaming_details.get("actualEndTime")
                if actual_end_time:
                    print(f"[TopTube] Видео является завершенным стримом (liveStreamingDetails): {video['snippet']['title'][:50]}... — пропускаем")
#                    return False
                
                # Если есть actualStartTime но нет actualEndTime - активный стрим
                actual_start_time = live_streaming_details.get("actualStartTime")
                if actual_start_time and not actual_end_time:
                    print(f"[TopTube] Видео является активным стримом (liveStreamingDetails): {video['snippet']['title'][:50]}... — пропускаем")
 #                   return False
            
            # Дополнительная проверка по названию (запасной вариант)
            title = video["snippet"]["title"]
            if self._is_stream_video(title):
                print(f"[TopTube] Видео является стримом (название): {title[:50]}... — пропускаем")
 #               return False
            
            # Проверяем, не является ли это игровым контентом
            channel_id = video["snippet"]["channelId"]
            channel_info = channels_dict.get(channel_id, {})
            if self._is_gaming_video(video, channel_info):
                print(f"[TopTube] Видео является игровым контентом: {title[:50]}... — пропускаем")
                return False
            
            # Проверяем длительность (минимум 90 минут, максимум 5 часов)
            duration_str = video["contentDetails"]["duration"]
            duration = isodate.parse_duration(duration_str)
            duration_seconds = duration.total_seconds()
            
            # Дополнительная проверка на подозрительно длинные видео (возможные стримы)
            if duration_seconds > 14400:  # 4 часа
                # Дополнительные проверки для длинных видео
                suspicious_long_patterns = [
                    r'\bvs\b', r'\bchallenge\b', r'\bmarathon\b', r'\btournament\b',
                    r'\branked\b', r'\bgrinding\b', r'\b\d+-\d+\b',  # счёт типа "99-0"
                ]
                import re
                for pattern in suspicious_long_patterns:
                    if re.search(pattern, title.lower()):
                        print(f"[TopTube] Подозрительно длинное видео со стрим-паттерном: {title[:50]}... — пропускаем")
#                        return False
            
            if duration_seconds < 5400:  # 1.5 часа = 5400 секунд (90 минут)
                print(f"[TopTube] Видео слишком короткое: {duration_seconds//60} мин — пропускаем")
                return False
            
            if duration_seconds > 18000:  # 5 часов = 18000 секунд
                print(f"[TopTube] Видео слишком длинное: {duration_seconds//60} мин — пропускаем (Яндекс не справляется)")
                return False
            
            # Проверяем дату публикации (не старше 30 дней)
            published = video["snippet"]["publishedAt"]
            published_dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
            if published_dt <= datetime.now().astimezone() - timedelta(days=DAYS):
                return False
            
            # Проверяем количество подписчиков (минимум 3 миллиона)
            channel_id = video["snippet"]["channelId"]
            channel_info = channels_dict.get(channel_id)
            if not channel_info:
                return False
            
            subs = int(channel_info["statistics"].get("subscriberCount", 0))
            if subs < 3_000_000:
                return False
            
            # Проверяем количество просмотров (минимум 200 тысяч)
            views = int(video["statistics"].get("viewCount", 0))
            if views < 200_000:
                return False
            
            return True
            
        except Exception as e:
            print(f"[TopTube] Ошибка при проверке видео: {e}")
            return False
    
    def _prepare_video_data(self, video: Dict[str, Any], channels_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Подготавливает данные видео для сохранения в БД."""
        duration_str = video["contentDetails"]["duration"]
        duration = isodate.parse_duration(duration_str)
        duration_seconds = int(duration.total_seconds())
        
        channel_id = video["snippet"]["channelId"]
        channel_info = channels_dict.get(channel_id, {})
        subs = int(channel_info.get("statistics", {}).get("subscriberCount", 0))
        views = int(video["statistics"].get("viewCount", 0))
        
        return {
            'video_id': video["id"],
            'title': video["snippet"]["title"],
            'channel_title': video["snippet"]["channelTitle"],
            'duration': duration_seconds,
            'views': views,
            'published_at': video["snippet"]["publishedAt"],
            'subscribers': subs,
            'url': f"https://www.youtube.com/watch?v={video['id']}"
            # НЕ устанавливаем статус здесь - он будет сохранен из БД если видео уже существует
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Получает статистику по видео."""
        return video_db.get_video_stats()
    
    def cleanup_old_data(self, days: int = 30) -> int:
        """Очищает старые данные."""
        return video_db.cleanup_old_videos(days)

# Глобальный экземпляр менеджера
_manager = None

def get_manager() -> TopTubeManager:
    """Получает глобальный экземпляр менеджера."""
    global _manager
    if _manager is None:
        _manager = TopTubeManager()
    return _manager

# --- Функции для APScheduler ---

def collect_videos_task():
    """Задача для планировщика - сбор видео."""
    try:
        manager = get_manager()
        count = manager.collect_videos()
        print(f"[TopTube] Задача сбора завершена: {count} видео")
        return count
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче сбора: {e}")
        return 0

def analyze_next_video_task():
    """Задача для планировщика - анализ всех необработанных видео."""
    try:
        manager = get_manager()
        processed_count = 0
        
        # Сначала сбрасываем все зависшие видео со статусом "processing" обратно в "new"
        stuck_count = video_db.reset_stuck_videos()
        if stuck_count > 0:
            print(f"[TopTube] Сброшено {stuck_count} зависших видео перед началом анализа")
        
        # Также сбрасываем видео со статусом "error" обратно в "new" для повторной попытки
        error_count = video_db.reset_error_videos()
        if error_count > 0:
            print(f"[TopTube] Сброшено {error_count} видео с ошибками для повторного анализа")
        
        while True:
            # Получаем следующее необработанное видео
            video = video_db.get_next_unprocessed_video()
            if not video:
                if processed_count == 0:
                    print("[TopTube] Нет необработанных видео для анализа")
                else:
                    print(f"[TopTube] Обработано {processed_count} видео, больше необработанных нет")
                break
            
            # Анализируем видео
            success = manager.analyze_single_video(video)
            processed_count += 1
            
            if success:
                print(f"[TopTube] Видео '{video['title']}' успешно проанализировано (всего: {processed_count})")
            else:
                print(f"[TopTube] Ошибка анализа видео '{video['title']}' (всего: {processed_count})")
            
            # Небольшая пауза между видео, чтобы не перегружать API
            import time
            time.sleep(2)
        
        return processed_count
            
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче анализа: {e}")
        return 0

def cleanup_videos_task():
    """Задача для планировщика - очистка старых данных."""
    try:
        manager = get_manager()
        deleted_count = manager.cleanup_old_data(days=30)
        print(f"[TopTube] Очистка завершена: удалено {deleted_count} старых записей")
        return deleted_count
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче очистки: {e}")
        return 0

def full_workflow_task():
    """Полный рабочий процесс: сбор → анализ → очистка."""
    try:
        print("[TopTube] 🚀 Начинаем полный рабочий процесс...")
        
        # 1. Сбор видео
        print("[TopTube] 📥 Этап 1: Сбор видео")
        collected_count = collect_videos_task()
        print(f"[TopTube] ✅ Сбор завершен: {collected_count} видео")
        
        # 2. Анализ всех необработанных видео
        print("[TopTube] 🔍 Этап 2: Анализ видео")
        analyzed_count = analyze_next_video_task()
        print(f"[TopTube] ✅ Анализ завершен: {analyzed_count} видео")
        
        # 3. Очистка старых данных
        print("[TopTube] 🧹 Этап 3: Очистка старых данных")
        cleaned_count = cleanup_videos_task()
        print(f"[TopTube] ✅ Очистка завершена: {cleaned_count} записей удалено")
        
        print(f"[TopTube] 🎉 Полный рабочий процесс завершен!")
        print(f"[TopTube] 📊 Итоги: собрано {collected_count}, проанализировано {analyzed_count}, очищено {cleaned_count}")
        
    except Exception as e:
        print(f"[TopTube] ❌ Ошибка в полном рабочем процессе: {e}")
