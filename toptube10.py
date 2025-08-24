import requests
from datetime import timedelta, datetime
import isodate
import os
import json
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# Импортируем наши модули
import video_db
import video_analyzer

# Константы
DAYS = 3
Q_TEMPLATE = "interview|интервью|беседа|обзор|разговор|репортаж|дудь|вдудь|варламов|собчак|лебедев|rogan|tucker|Ferriss|Musk|редакция|TheDiaryOfACEO|investigation|расследование"

# Словарь игровых ключевых слов для исключения
# Добавляйте сюда новые игровые ключевые слова по мере необходимости
# Система будет автоматически исключать видео, содержащие эти слова в заголовке
GAMING_KEYWORDS = [
    "JYNXZI",  # Игровой стример
    # Примеры для добавления:
    # "FORTNITE", "MINECRAFT", "GTA", "CS2", "VALORANT",
    # "STREAMER", "GAMEPLAY", "WALKTHROUGH", "SPEEDRUN"
]
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
        
        # OpenRouter API для LLM-фильтрации
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_api_url = "https://openrouter.ai/api/v1"
        
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
                    "maxResults": 100,
                    "key": self.api_key
                }
                
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
                        "part": "snippet,contentDetails,statistics",
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
        
        # Фильтруем видео (базовые критерии)
        filtered_videos = []
        for video in all_videos:
            try:
                if self._should_save_video(video, channels_dict):
                    filtered_videos.append(video)
            except Exception as e:
                print(f"[TopTube] Ошибка при фильтрации видео {video.get('id', 'unknown')}: {e}")
        
        print(f"[TopTube] После базовой фильтрации: {len(filtered_videos)} видео")
        
        # LLM-фильтрация нецелевого контента (игры + неподходящие языки)
        final_videos = self._filter_non_target_content_with_llm(filtered_videos)
        
        # Сохраняем финальные видео
        saved_count = 0
        for video in final_videos:
            try:
                video_data = self._prepare_video_data(video, channels_dict)
                if video_db.add_video(video_data):
                    saved_count += 1
            except Exception as e:
                print(f"[TopTube] Ошибка при сохранении видео {video.get('id', 'unknown')}: {e}")
        
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
    
    def _should_save_video(self, video: Dict[str, Any], channels_dict: Dict[str, Any]) -> bool:
        """Проверяет, нужно ли сохранять видео."""
        try:
            # Проверяем длительность (минимум 50 минут, максимум 4 часа)
            duration_str = video["contentDetails"]["duration"]
            duration = isodate.parse_duration(duration_str)
            duration_seconds = duration.total_seconds()
            
            if duration_seconds < 3600:  # 1 час = 3600 секунд (60 минут)
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
            
            # Фильтруем игровой контент по категории YouTube
            category_id = video["snippet"].get("categoryId", "")
            if category_id == "20":  # Gaming категория
                print(f"[TopTube] Видео является игровым контентом (категория): {video['snippet']['title'][:50]}... — пропускаем")
                return False
            
            # Фильтруем игровой контент по ключевым словам
            title = video["snippet"]["title"].upper()
            for keyword in GAMING_KEYWORDS:
                if keyword.upper() in title:
                    print(f"[TopTube] Видео содержит игровое ключевое слово '{keyword}': {video['snippet']['title'][:50]}... — пропускаем")
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
            'url': f"https://www.youtube.com/watch?v={video['id']}"
            # НЕ устанавливаем статус здесь - он будет сохранен из БД если видео уже существует
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Получает статистику по видео."""
        return video_db.get_video_stats()
    
    def cleanup_old_data(self, days: int = 30) -> int:
        """Очищает старые данные."""
        return video_db.cleanup_old_videos(days)
    
    def _filter_non_target_content_with_llm(self, videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Фильтрует нецелевой контент с помощью LLM (игровой контент + неподходящие языки).
        
        Args:
            videos: Список видео для проверки
            
        Returns:
            Список видео без нецелевого контента
        """
        if not self.openrouter_api_key:
            print("[TopTube] OpenRouter API ключ не установлен, пропускаем LLM-фильтрацию")
            return videos
            
        if len(videos) == 0:
            return videos
            
        # Формируем пронумерованный список заголовков
        titles_list = []
        for i, video in enumerate(videos, 1):
            title = video["snippet"]["title"]
            titles_list.append(f"{i}. {title}")
        
        titles_text = "\n".join(titles_list)
        
        prompt = f"""Ниже пронумерованный список заголовков YouTube видео. Определи какие из них НЕ подходят для русскоязычной аудитории по следующим критериям:

1) ИГРОВОЙ КОНТЕНТ:
- Компьютерные игры, мобильные игры
- Игровые механики, прохождения игр, летсплеи
- Игровые стримы, киберспорт
- Обзоры игр, игровое оборудование

2) НЕПОДХОДЯЩИЙ ЯЗЫК:
- Видео на вьетнамском, корейском языках
- Видео на языках народов Индии, Пакистана, Бангладеш

НЕ исключай:
- Интервью, новости, обзоры на русском/английском
- Спортивные события и турниры
- Бизнес, жизненные челленджи
- Музыкальные битвы, рэп-баттлы
- Документальные фильмы

В ответе укажи ТОЛЬКО номера видео для исключения через запятую (например: 1, 3, 7). Если исключать нечего, ответь "нет".

Список видео:
{titles_text}"""

        try:
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "google/gemma-3-27b-it:free",  # Используем бесплатную модель
                "messages": [
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                "max_tokens": 100,
                "temperature": 0.1
            }
            
            print(f"[TopTube] Отправка {len(videos)} заголовков для LLM-фильтрации (игры + языки)...")
            
            response = requests.post(
                f"{self.openrouter_api_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            
            if response.status_code != 200:
                print(f"[TopTube] Ошибка LLM API: {response.status_code} - {response.text}")
                return videos  # Возвращаем все видео если API недоступно
                
            result = response.json()
            llm_response = result["choices"][0]["message"]["content"].strip().lower()
            
            print(f"[TopTube] LLM ответ: '{llm_response}'")
            
            # Парсим ответ LLM
            if llm_response == "нет" or "нет" in llm_response:
                print("[TopTube] LLM не нашел контента для исключения")
                return videos
                
            # Извлекаем номера видео для исключения
            exclude_indices = []
            for part in llm_response.replace(" ", "").split(","):
                try:
                    if part.isdigit():
                        exclude_indices.append(int(part) - 1)  # Переводим в 0-based индексы
                except ValueError:
                    continue
            
            # Фильтруем видео, исключая нецелевые
            filtered_videos = []
            for i, video in enumerate(videos):
                if i not in exclude_indices:
                    filtered_videos.append(video)
                else:
                    print(f"[TopTube] LLM исключил: {video['snippet']['title'][:70]}...")
            
            print(f"[TopTube] LLM-фильтрация: было {len(videos)}, стало {len(filtered_videos)} видео")
            return filtered_videos
            
        except Exception as e:
            print(f"[TopTube] Ошибка в LLM-фильтрации: {e}")
            return videos  # Возвращаем все видео если что-то пошло не так

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
