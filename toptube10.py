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
Q_TEMPLATE = "interview|интервью|беседа|обзор|разговор|репортаж"
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
        for region in regions:
            page_token = None
            print(f"[TopTube] Сбор mostPopular для региона {region}")
            
            for page_num in range(1, pages_to_fetch + 1):
                params = {
                    "part": "snippet,contentDetails,statistics",
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
        
        try:
            search_params = {
                "part": "snippet",
                "type": "video",
                "publishedAfter": published_after,
                "q": Q_TEMPLATE,
                "order": "viewCount",
                "videoDuration": "long",
                "maxResults": 50,
                "key": self.api_key
            }
            
            search_resp = requests.get(search_url, params=search_params, timeout=30)
            search_resp.raise_for_status()
            search_data = search_resp.json()
            search_items = search_data.get("items", [])
            
            print(f"[TopTube] search.list: получено {len(search_items)} видео")
            
            # Для search.list нужно получить детали видео
            search_video_ids = [item["id"]["videoId"] for item in search_items if "videoId" in item["id"]]
            if search_video_ids:
                details_params = {
                    "part": "snippet,contentDetails,statistics",
                    "id": ",".join(search_video_ids),
                    "key": self.api_key
                }
                details_resp = requests.get(videos_url, params=details_params, timeout=30)
                details_resp.raise_for_status()
                details_data = details_resp.json()
                details_items = details_data.get("items", [])
                all_videos.extend(details_items)
                
        except Exception as e:
            print(f"[TopTube] Ошибка при поиске по ключевым словам: {e}")
        
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
            
            # Анализируем видео
            analyzer = video_analyzer.VideoAnalyzer()
            result = analyzer.analyze_video(video_data['url'])
            
            # Сохраняем результат
            success = video_db.save_analysis(video_data['id'], result)
            
            if result.get('error'):
                print(f"[TopTube] Ошибка анализа видео {video_data['title']}: {result['error']}")
                return False
            else:
                print(f"[TopTube] Видео {video_data['title']} успешно проанализировано")
                return True
                
        except Exception as e:
            print(f"[TopTube] Ошибка при анализе видео {video_data.get('title', 'unknown')}: {e}")
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
    
    def _should_save_video(self, video: Dict[str, Any], channels_dict: Dict[str, Any]) -> bool:
        """Проверяет, нужно ли сохранять видео."""
        try:
            # Проверяем длительность (минимум 1 час)
            duration_str = video["contentDetails"]["duration"]
            duration = isodate.parse_duration(duration_str)
            if duration.total_seconds() < 3600:
                return False
            
            # Проверяем дату публикации (не старше 3 дней)
            published = video["snippet"]["publishedAt"]
            published_dt = datetime.fromisoformat(published.replace('Z', '+00:00'))
            if published_dt <= datetime.now().astimezone() - timedelta(days=DAYS):
                return False
            
            # Проверяем количество подписчиков (минимум 1 миллион)
            channel_id = video["snippet"]["channelId"]
            channel_info = channels_dict.get(channel_id)
            if not channel_info:
                return False
            
            subs = int(channel_info["statistics"].get("subscriberCount", 0))
            if subs < 1_000_000:
                return False
            
            # Проверяем количество просмотров (минимум 100 тысяч)
            views = int(video["statistics"].get("viewCount", 0))
            if views < 100_000:
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
            'url': f"https://www.youtube.com/watch?v={video['id']}",
            'status': 'new'
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
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче сбора: {e}")

def analyze_next_video_task():
    """Задача для планировщика - анализ следующего видео."""
    try:
        # Получаем следующее необработанное видео
        video = video_db.get_next_unprocessed_video()
        if not video:
            print("[TopTube] Нет необработанных видео для анализа")
            return
        
        # Анализируем видео
        manager = get_manager()
        success = manager.analyze_single_video(video)
        
        if success:
            print(f"[TopTube] Видео '{video['title']}' успешно проанализировано")
        else:
            print(f"[TopTube] Ошибка анализа видео '{video['title']}'")
            
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче анализа: {e}")

def cleanup_videos_task():
    """Задача для планировщика - очистка старых данных."""
    try:
        manager = get_manager()
        deleted_count = manager.cleanup_old_data(days=30)
        print(f"[TopTube] Очистка завершена: удалено {deleted_count} старых записей")
    except Exception as e:
        print(f"[TopTube] Ошибка в задаче очистки: {e}")
