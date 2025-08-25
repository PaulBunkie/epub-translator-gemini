import os
import requests
import json
import re
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any
import time
from workflow_model_config import get_model_for_operation
from telegram_notifier import telegram_notifier


class VideoAnalyzer:
    """
    Модуль для анализа видео через Yandex API и OpenRouter.
    Извлекает текст из видео и анализирует его с помощью AI.
    """
    
    def __init__(self):
        self.yandex_token = os.getenv("YANDEX_API_TOKEN")  # Официальный OAuth токен
        self.session_id = os.getenv("YANDEX_SESSION_ID")   # Session_id для fallback
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_api_url = "https://openrouter.ai/api/v1"
        
        # Модели для анализа из конфигурации
        self.primary_model = get_model_for_operation('video_analyze', 'primary')
        self.fallback_model = get_model_for_operation('video_analyze', 'fallback_level1')
        
        # Модели для перевода заголовков
        self.title_translate_primary = get_model_for_operation('title_translate', 'primary')
        self.title_translate_fallback = get_model_for_operation('title_translate', 'fallback_level1')
        
        if not self.yandex_token and not self.session_id:
            raise ValueError("Необходимо установить YANDEX_API_TOKEN или YANDEX_SESSION_ID")
        if not self.openrouter_api_key:
            raise ValueError("Не установлена переменная окружения OPENROUTER_API_KEY")
    
    def extract_video_id(self, text: str) -> str | None:
        """Извлекает YouTube video ID из URL"""
        youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=|shorts/)?([^&=%\?]{11})'
        import re
        urls = re.findall(re.compile(youtube_regex), text)
        return [url[-1] for url in urls][0] if len(urls) > 1 else (urls[0][-1] if urls else None)
    
    def ya_headers(self) -> dict[str, str]:
        """
        Формирует заголовки для Yandex API с Session_id из cookies
        """
        import re
        from datetime import datetime
        
        # Извлекаем yandex_csyr из session_id
        match = re.search(r':(\d+)\.', self.session_id)
        yandex_csyr = match.group(1) if match else int(datetime.now().timestamp())
        
        return {
            'accept': '*/*',
            'content-type': 'application/json',
            'cookie': f'yandex_csyr={yandex_csyr}; Session_id={self.session_id}',
        }
    
    def get_sharing_url_official(self, video_url: str) -> Optional[str]:
        """
        Пытается получить sharing URL через официальный API с OAuth токеном
        """
        try:
            api_url = 'https://300.ya.ru/api/sharing-url'
            
            headers = {
                'Authorization': f'OAuth {self.yandex_token}',
                'Content-Type': 'application/json'
            }
            
            # Для всех URL используем article_url
            payload = {
                'article_url': video_url
            }
            
            print(f"[VideoAnalyzer] Попытка официального API для URL: {video_url}")
            
            # Прямой запрос к API
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            print(f"[VideoAnalyzer] Официальный API ответ: статус {response.status_code}")
            
            if response.status_code != 200:
                print(f"[VideoAnalyzer] Официальный API ошибка: {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"[VideoAnalyzer] Детали ошибки API: {error_data}")
                    
                    # Проверяем на ошибки токена и отправляем уведомления
                    if response.status_code in [401, 403]:
                        error_message = error_data.get('message', '') if isinstance(error_data, dict) else str(error_data)
                        if 'token' in error_message.lower() or 'expired' in error_message.lower():
                            telegram_notifier.notify_yandex_token_error(
                                "expired" if response.status_code == 401 else "invalid",
                                f"HTTP {response.status_code}: {error_message}"
                            )
                        else:
                            telegram_notifier.notify_api_error(
                                "Yandex Official API",
                                response.status_code,
                                error_message
                            )
                except:
                    error_text = response.text[:500]
                    print(f"[VideoAnalyzer] Текст ошибки: {error_text}...")
                    
                    # Отправляем уведомление даже если не удалось распарсить JSON
                    if response.status_code in [401, 403]:
                        telegram_notifier.notify_yandex_token_error(
                            "expired" if response.status_code == 401 else "invalid",
                            f"HTTP {response.status_code}: {error_text}"
                        )
                return None
            
            # Парсим ответ
            data = response.json()
            print(f"[VideoAnalyzer] Официальный API ответ: {data}")
            
            if data.get('status') == 'success':
                sharing_url = data.get('sharing_url', '')
                if sharing_url:
                    print(f"[VideoAnalyzer] Официальный API дал sharing URL: {sharing_url}")
                    return sharing_url
            
            print("[VideoAnalyzer] Официальный API не вернул sharing_url")
            return None
            
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка официального API: {e}")
            return None
    
    def get_sharing_url_session(self, video_url: str) -> Optional[str] | tuple[str, dict]:
        """
        Получает sharing URL через сессию (fallback метод)
        """
        if not self.session_id:
            print("[VideoAnalyzer] Session_id не установлен, fallback недоступен")
            return None
            
        try:
            api_url = 'https://300.ya.ru/api/generation'
            headers = self.ya_headers()
            
            # Определяем тип контента как в оригинальном коде
            yt_id = self.extract_video_id(video_url)
            if yt_id:
                payload = {'video_url': f'https://www.youtube.com/watch?v={yt_id}', 'type': 'video'}
            else:
                payload = {'article_url': video_url, 'type': 'article'}
            
            print(f"[VideoAnalyzer] Fallback через сессию для URL: {video_url}")
            print(f"[VideoAnalyzer] Fallback заголовки: {headers}")
            print(f"[VideoAnalyzer] Fallback payload: {payload}")
            
            # Первый запрос - инициализация
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            print(f"[VideoAnalyzer] Fallback ответ: статус {response.status_code}")
            
            if response.status_code != 200:
                print(f"[VideoAnalyzer] Fallback ошибка: {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"[VideoAnalyzer] Fallback детали ошибки: {error_data}")
                    
                    # Проверяем на ошибки сессии и отправляем уведомления
                    if response.status_code in [401, 403]:
                        error_message = error_data.get('message', '') if isinstance(error_data, dict) else str(error_data)
                        if 'session' in error_message.lower() or 'expired' in error_message.lower():
                            telegram_notifier.notify_session_expired("Yandex Session")
                        else:
                            telegram_notifier.notify_api_error(
                                "Yandex Session API",
                                response.status_code,
                                error_message
                            )
                except:
                    error_text = response.text[:500]
                    print(f"[VideoAnalyzer] Fallback текст ошибки: {error_text}...")
                    
                    # Отправляем уведомление даже если не удалось распарсить JSON
                    if response.status_code in [401, 403]:
                        telegram_notifier.notify_session_expired("Yandex Session")
                return None
            
            # Остальная логика polling'а...
            first_data = response.json()
            print(f"[VideoAnalyzer] Fallback ответ: {first_data}")
            
            session_id = first_data.get('session_id')
            poll_interval_ms = first_data.get('poll_interval_ms', 1000)
            status_code = first_data.get('status_code')
            
            if not session_id or status_code > 1:
                print(f"[VideoAnalyzer] Fallback не дал session_id или ошибка")
                return None
            
            # Второй запрос - получение результата
            is_video = self.extract_video_id(video_url)
            second_payload = {'session_id': session_id, 'type': 'video' if is_video else 'article'}
            
            # Polling до получения sharing_url
            max_attempts = 30
            attempt = 0
            
            print(f"[VideoAnalyzer] Начинаем поллинг с интервалом {poll_interval_ms}ms, максимум {max_attempts} попыток")
            
            while attempt < max_attempts:
                attempt += 1
                
                if attempt > 1:
                    print(f"[VideoAnalyzer] Поллинг попытка {attempt}/{max_attempts}, пауза {poll_interval_ms}ms")
                    time.sleep(poll_interval_ms / 1000)
                else:
                    print(f"[VideoAnalyzer] Поллинг попытка {attempt}/{max_attempts} (без паузы)")
                
                second_response = requests.post(
                    api_url,
                    json=second_payload,
                    headers=headers,
                    timeout=30
                )
                
                print(f"[VideoAnalyzer] Поллинг ответ {attempt}: статус HTTP {second_response.status_code}")
                
                if second_response.status_code != 200:
                    print(f"[VideoAnalyzer] Поллинг неуспешен на попытке {attempt}: HTTP {second_response.status_code}")
                    print(f"[VideoAnalyzer] Поллинг текст ошибки: {second_response.text[:200]}...")
                    return None
                
                second_data = second_response.json()
                print(f"[VideoAnalyzer] Поллинг данные {attempt}: {second_data}")
                
                error_code = second_data.get('error_code')
                status_code = second_data.get('status_code')
                
                print(f"[VideoAnalyzer] Поллинг статусы {attempt}: error_code={error_code}, status_code={status_code}")
                
                if error_code:
                    print(f"[VideoAnalyzer] Поллинг завершен с ошибкой на попытке {attempt}: error_code={error_code}")
                    return None
                
                if status_code > 1:
                    print(f"[VideoAnalyzer] Поллинг завершен с ошибкой на попытке {attempt}: status_code={status_code}")
                    return None
                
                sharing_url = second_data.get('sharing_url', '')
                keypoints = second_data.get('keypoints', [])
                
                # Новая логика: проверяем keypoints вместо sharing_url
                if sharing_url:
                    print(f"[VideoAnalyzer] Поллинг успешен на попытке {attempt}! sharing URL: {sharing_url}")
                    return sharing_url
                elif keypoints and len(keypoints) > 0:
                    # Проверяем что keypoints содержат достаточно данных
                    total_theses = sum(len(kp.get('theses', [])) for kp in keypoints)
                    if total_theses >= 3:  # Минимум 3 тезиса для завершения
                        print(f"[VideoAnalyzer] Поллинг успешен на попытке {attempt}! Получены keypoints: {len(keypoints)} разделов, {total_theses} тезисов")
                        # Возвращаем tuple с маркером и данными keypoints
                        return ("USE_KEYPOINTS", second_data)
                
                # Обновляем интервал поллинга если сервер его изменил
                new_poll_interval = second_data.get('poll_interval_ms', poll_interval_ms)
                if new_poll_interval != poll_interval_ms:
                    print(f"[VideoAnalyzer] Поллинг интервал изменен с {poll_interval_ms}ms на {new_poll_interval}ms")
                    poll_interval_ms = new_poll_interval
                
                print(f"[VideoAnalyzer] Поллинг {attempt}: sharing_url пока пуст, продолжаем (status_code={status_code})")
            
            print(f"[VideoAnalyzer] Поллинг исчерпан после {max_attempts} попыток, sharing_url так и не получен")
            
            return None
            
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка fallback: {e}")
            return None
    
    def extract_text_from_keypoints(self, keypoints_data: dict) -> Optional[str]:
        """
        Извлекает текст из keypoints данных полученных от Yandex API.
        
        Args:
            keypoints_data: Данные с keypoints из последнего ответа поллинга
        
        Returns:
            Извлеченный и форматированный текст или None в случае ошибки
        """
        try:
            keypoints = keypoints_data.get('keypoints', [])
            title = keypoints_data.get('title', '')
            
            if not keypoints:
                print("[VideoAnalyzer] keypoints пусты")
                return None
            
            text_blocks = []
            
            # Добавляем заголовок
            if title:
                text_blocks.append(f"# {title}\n")
            
            # Обрабатываем каждый keypoint
            for kp in keypoints:
                section_title = kp.get('content', '')
                theses = kp.get('theses', [])
                start_time = kp.get('start_time', 0)
                
                if section_title:
                    # Форматируем время
                    minutes = start_time // 60
                    seconds = start_time % 60
                    time_str = f"{minutes:02d}:{seconds:02d}"
                    
                    text_blocks.append(f"\n## {section_title} ({time_str})\n")
                
                # Добавляем тезисы
                for thesis in theses:
                    thesis_content = thesis.get('content', '').strip()
                    if thesis_content:
                        text_blocks.append(f"• {thesis_content}")
                
                if theses:  # Добавляем пустую строку после каждой секции
                    text_blocks.append("")
            
            result = '\n'.join(text_blocks).strip()
            
            if result:
                print(f"[VideoAnalyzer] Извлечен текст из keypoints: {len(result)} символов, {len(keypoints)} разделов")
                return result
            else:
                print("[VideoAnalyzer] keypoints не содержат текста")
                return None
                
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка извлечения текста из keypoints: {e}")
            return None

    def extract_text_from_sharing_url(self, sharing_url: str) -> Optional[str]:
        """
        Извлекает текст из HTML страницы sharing URL (300.ya.ru/v_xxx).
        
        Args:
            sharing_url: URL полученный от Yandex API
        
        Returns:
            Извлеченный текст или None в случае ошибки
        """
        try:
            print(f"[VideoAnalyzer] Загрузка HTML с URL: {sharing_url}")
            response = requests.get(sharing_url, timeout=30)
            if response.status_code != 200:
                print(f"[VideoAnalyzer] HTTP ошибка при загрузке HTML: {response.status_code}")
                return None
            soup = BeautifulSoup(response.content, 'html.parser')
            text_blocks = []

            # 1. Основной пересказ (summary-scroll-inner, summary-scroll)
            for selector in [
                'div.summary-scroll-inner',
                'div.summary-scroll',
            ]:
                for el in soup.select(selector):
                    text = el.get_text(separator='\n', strip=True)
                    if text and len(text) > 50:
                        text_blocks.append(text)

            # 2. Краткие тезисы (p.thesis-text, span.text-wrapper)
            for selector in [
                'p.thesis-text',
                'span.text-wrapper',
            ]:
                for el in soup.select(selector):
                    text = el.get_text(separator=' ', strip=True)
                    if text and len(text) > 30:
                        text_blocks.append(text)

            # 3. Мета-теги description/og:description
            meta_names = ['description', 'og:description']
            for meta_name in meta_names:
                meta = soup.find('meta', attrs={'name': meta_name})
                if not meta:
                    meta = soup.find('meta', attrs={'property': meta_name})
                if meta:
                    text = meta.get('content', '').strip()
                    if text and len(text) > 30:
                        text_blocks.append(text)

            # Удаляем дубли и пустые строки
            unique_blocks = []
            seen = set()
            for block in text_blocks:
                norm = block.strip()
                if norm and norm not in seen:
                    unique_blocks.append(norm)
                    seen.add(norm)

            if unique_blocks:
                full_text = '\n\n'.join(unique_blocks)
                print(f"[VideoAnalyzer] Извлечено {len(full_text)} символов текста из sharing URL")
                return full_text
            else:
                print("[VideoAnalyzer] Не удалось найти текст в sharing URL")
                return None
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при извлечении текста: {e}")
            return None
    
    def analyze_text_with_openrouter(self, text: str) -> Optional[str]:
        """
        Анализирует текст с помощью OpenRouter API.
        Использует основную модель, а при ошибках - резервную.
        
        Args:
            text: Текст для анализа
            
        Returns:
            Результат анализа или None в случае ошибки
        """
        try:
            prompt = """Проанализируй данный текст/транскрипт/видео и выдели самые важные и необычные аспекты, которые раскрываются в материале.

Выдели нестандартные и интересные инсайты, которые обычно упускают в простых резюме.

Сфокусируйся на психологических, технических, культурных и этических нюансах.

Не делай простого пересказа по хронологии, а структурируй обзор по ключевым темам и интересным фактам.

Кратко объясни суть каждого пункта, избегай общих фраз и «воды».

При возможности выдели необычные наблюдения или примеры, которые иллюстрируют раскрываемые идеи.

Структурируй ответ следующим образом:
1. Основная тема и контекст (2-3 предложения)
2. Ключевые инсайты (7-10 пунктов с подробным объяснением)
3. Необычные аспекты и наблюдения

Итог дай в виде связного обзора с примерно 7-10 пунктами, раскрывающих основные уникальные моменты материала."""

            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + резервная)
            models_to_try = [self.primary_model, self.fallback_model]
            
            for model in models_to_try:
                print(f"[VideoAnalyzer] Пробуем модель: {model}")
                
                # Retry логика с уменьшением max_tokens для каждой модели
                max_tokens_options = [64000, 32000, 16000, 8000, 4000, 2000]
                
                for max_tokens in max_tokens_options:
                    try:
                        payload = {
                            "model": model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"{prompt}{chr(10)}{chr(10)}Текст для анализа:{chr(10)}{chr(10)}{text}"
                                }
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.7
                        }
                        
                        print(f"[VideoAnalyzer] Отправка запроса к OpenRouter API (модель: {model}, max_tokens: {max_tokens})")
                        response = requests.post(
                            f"{self.openrouter_api_url}/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=120  # Увеличиваем таймаут
                        )
                        
                        if response.status_code == 200:
                            try:
                                data = response.json()
                                if 'choices' in data and len(data['choices']) > 0:
                                    content = data['choices'][0]['message']['content']
                                    print(f"[VideoAnalyzer] Получен анализ длиной {len(content)} символов от модели {model}")
                                    return content
                                else:
                                    print(f"[VideoAnalyzer] Неверный формат ответа от OpenRouter API для модели {model}")
                                    print(f"[VideoAnalyzer] Структура ответа: {data}")
                                    continue  # Пробуем с меньшим max_tokens
                            except json.JSONDecodeError as e:
                                print(f"[VideoAnalyzer] Ошибка парсинга JSON для модели {model}: {e}")
                                print(f"[VideoAnalyzer] Текст ответа: {response.text[:500]}...")
                                continue  # Пробуем с меньшим max_tokens
                        else:
                            print(f"[VideoAnalyzer] HTTP ошибка OpenRouter API для модели {model}: {response.status_code}")
                            try:
                                error_details = response.json()
                                print(f"[VideoAnalyzer] Детали ошибки: {error_details}")
                                
                                # Если это ошибка 503 "No instances available", сразу переходим к следующей модели
                                if response.status_code == 503 and "No instances available" in str(error_details):
                                    print(f"[VideoAnalyzer] Модель {model} недоступна (503), переходим к следующей")
                                    break  # Выходим из цикла max_tokens и переходим к следующей модели
                                    
                            except:
                                print(f"[VideoAnalyzer] Текст ошибки: {response.text[:500]}...")
                            continue  # Пробуем с меньшим max_tokens
                            
                    except requests.exceptions.Timeout:
                        print(f"[VideoAnalyzer] Таймаут при max_tokens={max_tokens} для модели {model}, пробуем меньше")
                        continue
                    except Exception as e:
                        print(f"[VideoAnalyzer] Ошибка при max_tokens={max_tokens} для модели {model}: {e}")
                        continue
                
                print(f"[VideoAnalyzer] Все попытки с моделью {model} не удались")
            
            print("[VideoAnalyzer] Все модели и все попытки с разными max_tokens не удались")
            return None
                
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при анализе текста: {e}")
            return None
    
    def translate_video_title(self, title: str) -> str:
        """
        Переводит заголовок видео на русский язык, если он не на русском.
        Если заголовок уже на русском, возвращает оригинал.
        
        Args:
            title: Заголовок видео для перевода
            
        Returns:
            Переведенный заголовок или оригинал, если перевод не удался
        """
        try:
            prompt = """Переведи заголовок на русский язык. Если заголовок уже на русском - верни его без изменений. 

ВАЖНО: В ответе должен быть ТОЛЬКО заголовок, без кавычек, без объяснений, без дополнительного текста.

Заголовок: {title}

Перевод:"""

            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + резервная)
            models_to_try = [self.title_translate_primary, self.title_translate_fallback]
            
            for model in models_to_try:
                if not model:
                    continue
                    
                print(f"[VideoAnalyzer] Пробуем модель для перевода заголовка: {model}")
            
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt.format(title=title)
                        }
                    ],
                    "max_tokens": 100,  # Минимальное количество токенов для заголовка
                    "temperature": 0.3   # Низкая температура для точности перевода
                }
                
                print(f"[VideoAnalyzer] Переводим заголовок: '{title[:50]}...' с моделью {model}")
                
                try:
                    response = requests.post(
                        f"{self.openrouter_api_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=30
                    )
                    
                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                translated_title = data['choices'][0]['message']['content'].strip()
                                
                                if translated_title and translated_title != title:
                                    # Очищаем ответ от лишних символов и форматирования
                                    cleaned_title = translated_title.strip()
                                    # Убираем кавычки в начале и конце
                                    if cleaned_title.startswith('"') and cleaned_title.endswith('"'):
                                        cleaned_title = cleaned_title[1:-1].strip()
                                    if cleaned_title.startswith("'") and cleaned_title.endswith("'"):
                                        cleaned_title = cleaned_title[1:-1].strip()
                                    # Убираем возможные префиксы типа "Перевод:", "Результат:" и т.д.
                                    for prefix in ["Перевод:", "Результат:", "Ответ:", "Title:", "Translation:"]:
                                        if cleaned_title.startswith(prefix):
                                            cleaned_title = cleaned_title[len(prefix):].strip()
                                    
                                    if cleaned_title and cleaned_title != title:
                                        print(f"[VideoAnalyzer] Заголовок переведен: '{title}' -> '{cleaned_title}'")
                                        return cleaned_title
                                    else:
                                        print(f"[VideoAnalyzer] После очистки перевод не изменился: '{title}'")
                                        return title
                                else:
                                    print(f"[VideoAnalyzer] Заголовок уже на русском или перевод не изменился: '{title}'")
                                    return title
                            else:
                                print(f"[VideoAnalyzer] Неверный формат ответа от OpenRouter API для перевода заголовка")
                                continue  # Пробуем следующую модель
                        except json.JSONDecodeError as e:
                            print(f"[VideoAnalyzer] Ошибка парсинга JSON для перевода заголовка: {e}")
                            continue  # Пробуем следующую модель
                    else:
                        print(f"[VideoAnalyzer] HTTP ошибка OpenRouter API для перевода заголовка: {response.status_code}")
                        continue  # Пробуем следующую модель
                        
                except Exception as e:
                    print(f"[VideoAnalyzer] Ошибка при запросе к OpenRouter для перевода заголовка: {e}")
                    continue  # Пробуем следующую модель
            
            print("[VideoAnalyzer] Все модели для перевода заголовка не сработали")
            return title
                
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при переводе заголовка: {e}")
            return title

    def generate_analysis_summary(self, analysis_text: str) -> Optional[str]:
        """
        Генерирует краткую версию анализа.
        Использует основную модель, а при ошибках - резервную.
        
        Args:
            analysis_text: Полный текст анализа
            
        Returns:
            Краткая версия анализа или None в случае ошибки
        """
        try:
            prompt = "Выдели одну максимально интересную или неожиданную деталь и изложи её в одной-двух метких фразах. ОТВЕТ ДОЛЖЕН СОСТОЯТЬ ТОЛЬКО ИЗ ЭТИХ ФРАЗ И БЫТЬ ДИНАМИЧНЫМ. Ответ не должен содержать никакого форматирования или тегов, только текст."

            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Список моделей для попыток (основная + резервная)
            models_to_try = [self.primary_model, self.fallback_model]
            
            for model in models_to_try:
                print(f"[VideoAnalyzer] Генерируем краткую версию с моделью: {model}")
                
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": f"{prompt}{chr(10)}{chr(10)}{analysis_text}"
                        }
                    ],
#                    "max_tokens": 200,  # Небольшое количество токенов для краткого ответа
                    "temperature": 0.8  # Немного выше для креативности
                }
                
                # Простой запрос без retry логики
                print(f"[VideoAnalyzer] Отправляем запрос к OpenRouter для краткой версии (модель: {model})...")
                response = requests.post(
                    f"{self.openrouter_api_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30
                )
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        print(f"[VideoAnalyzer] Ответ API для краткой версии (модель {model}): {data}")
                        if 'choices' in data and len(data['choices']) > 0:
                            content = data['choices'][0]['message']['content'].strip()
                            print(f"[VideoAnalyzer] Сырой content от модели {model}: '{content}'")
                            
                            if content:  # Проверяем, что контент не пустой
                                print(f"[VideoAnalyzer] Сгенерирована краткая версия от модели {model}: {content}")
                                return content
                            else:
                                print(f"[VideoAnalyzer] API вернул пустой ответ для краткой версии от модели {model}")
                                continue  # Пробуем следующую модель
                        else:
                            print(f"[VideoAnalyzer] Неверный формат ответа для краткой версии от модели {model}")
                            continue  # Пробуем следующую модель
                    except json.JSONDecodeError as e:
                        print(f"[VideoAnalyzer] Ошибка парсинга JSON для краткой версии от модели {model}: {e}")
                        print(f"[VideoAnalyzer] Текст ответа: {response.text[:500]}...")
                        continue  # Пробуем следующую модель
                else:
                    print(f"[VideoAnalyzer] HTTP ошибка при генерации краткой версии от модели {model}: {response.status_code}")
                    try:
                        error_details = response.json()
                        print(f"[VideoAnalyzer] Детали ошибки: {error_details}")
                        
                        # Если это ошибка 503 "No instances available", сразу переходим к следующей модели
                        if response.status_code == 503 and "No instances available" in str(error_details):
                            print(f"[VideoAnalyzer] Модель {model} недоступна (503) для краткой версии, переходим к следующей")
                            continue  # Переходим к следующей модели
                            
                    except:
                        print(f"[VideoAnalyzer] Текст ошибки: {response.text[:500]}...")
                    continue  # Пробуем следующую модель
            
            print("[VideoAnalyzer] Все модели для краткой версии не сработали")
            return None
                
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при генерации краткой версии: {e}")
            return None
    
    def analyze_video(self, video_url: str, existing_data: dict = None, title: str = None) -> dict:
        """
        Анализирует видео по URL, получая sharing URL от Yandex API,
        извлекая текст из страницы и анализируя его через OpenRouter.
        
        Args:
            video_url: URL видео для анализа
            existing_data: Существующие данные анализа (если есть)
            title: Заголовок видео для перевода (опционально)
            
        Returns:
            Словарь с результатами анализа
        """
        print(f"[VideoAnalyzer] === НАЧАЛО АНАЛИЗА ВИДЕО ===")
        print(f"[VideoAnalyzer] URL: {video_url}")
        print(f"[VideoAnalyzer] Yandex Token: {'УСТАНОВЛЕН' if self.yandex_token else 'НЕ УСТАНОВЛЕН'}")
        print(f"[VideoAnalyzer] Session ID: {'УСТАНОВЛЕН' if self.session_id else 'НЕ УСТАНОВЛЕН'}")
        print(f"[VideoAnalyzer] OpenRouter Key: {'УСТАНОВЛЕН' if self.openrouter_api_key else 'НЕ УСТАНОВЛЕН'}")
        
        result = {
            'video_url': video_url,
            'sharing_url': None,
            'extracted_text': None,
            'analysis': None,
            'error': None
        }
        
        # Добавляем заголовок, если он передан
        if title:
            result['title'] = title
        
        try:
            # Проверяем, есть ли уже извлеченный текст
            if existing_data and existing_data.get('extracted_text'):
                print(f"[VideoAnalyzer] Используем уже извлеченный текст ({len(existing_data['extracted_text'])} символов)")
                result['extracted_text'] = existing_data['extracted_text']
                result['sharing_url'] = existing_data.get('sharing_url')
                # Добавляем заголовок из existing_data, если он там есть
                if existing_data.get('title') and 'title' not in result:
                    result['title'] = existing_data['title']
            else:
                # Гибридный подход: сначала официальный API, потом fallback
                print(f"[VideoAnalyzer] Начинаем анализ видео: {video_url}")
                
                # Попытка 1: Официальный API через OAuth токен
                if self.yandex_token:
                    print("[VideoAnalyzer] Попытка через официальный API...")
                    sharing_url = self.get_sharing_url_official(video_url)
                    if sharing_url:
                        result['sharing_url'] = sharing_url
                        print(f"[VideoAnalyzer] Успешно получен sharing URL через официальный API: {sharing_url}")
                    else:
                        print("[VideoAnalyzer] Официальный API не сработал, пробуем fallback...")
                else:
                    print("[VideoAnalyzer] Официальный API недоступен (не установлен YANDEX_API_TOKEN)")
                
                # Попытка 2: Fallback через сессию
                keypoints_data = None
                if not result['sharing_url']:
                    if self.session_id:
                        print("[VideoAnalyzer] Попытка через fallback (сессия)...")
                        fallback_result = self.get_sharing_url_session(video_url)
                        
                        if fallback_result:
                            if isinstance(fallback_result, tuple) and fallback_result[0] == "USE_KEYPOINTS":
                                # Получили keypoints вместо sharing_url
                                keypoints_data = fallback_result[1]
                                print(f"[VideoAnalyzer] Успешно получены keypoints через fallback")
                            elif isinstance(fallback_result, str):
                                # Получили обычный sharing_url
                                result['sharing_url'] = fallback_result
                                print(f"[VideoAnalyzer] Успешно получен sharing URL через fallback: {fallback_result}")
                        else:
                            print("[VideoAnalyzer] Fallback тоже не сработал")
                    else:
                        print("[VideoAnalyzer] Fallback недоступен (не установлен YANDEX_SESSION_ID)")
                
                # Если не удалось получить ни sharing URL, ни keypoints
                if not result['sharing_url'] and not keypoints_data:
                    error_msg = "Не удалось получить данные для анализа"
                    if not self.yandex_token and not self.session_id:
                        error_msg += " (не установлены ни YANDEX_API_TOKEN, ни YANDEX_SESSION_ID)"
                    elif not self.yandex_token:
                        error_msg += " (не установлен YANDEX_API_TOKEN)"
                    elif not self.session_id:
                        error_msg += " (не установлен YANDEX_SESSION_ID)"
                    result['error'] = error_msg
                    return result
                
                # Извлекаем текст - либо из sharing URL, либо из keypoints
                if keypoints_data:
                    print("[VideoAnalyzer] Извлекаем текст из keypoints...")
                    extracted_text = self.extract_text_from_keypoints(keypoints_data)
                    result['sharing_url'] = "keypoints_data"  # Устанавливаем маркер что использовались keypoints
                else:
                    print(f"[VideoAnalyzer] Извлекаем текст из sharing URL: {result['sharing_url']}")
                    extracted_text = self.extract_text_from_sharing_url(result['sharing_url'])
                
                if not extracted_text:
                    result['error'] = 'Не удалось извлечь текст из полученных данных'
                    return result
                
                result['extracted_text'] = extracted_text
                print(f"[VideoAnalyzer] Извлечено {len(extracted_text)} символов текста")
            
            # Анализируем текст через OpenRouter
            print("[VideoAnalyzer] Отправляем текст на анализ в OpenRouter...")
            analysis = self.analyze_text_with_openrouter(result['extracted_text'])
            
            if not analysis:
                result['error'] = 'Не удалось проанализировать текст через OpenRouter'
                return result
            
            result['analysis'] = analysis
            print("[VideoAnalyzer] Анализ завершен успешно")
            
            # Пауза перед генерацией краткой версии для избежания rate limit
            print("[VideoAnalyzer] Пауза 3 секунды перед генерацией краткой версии...")
            time.sleep(3)
            
            # Генерируем краткую версию анализа
            analysis_summary = self.generate_analysis_summary(analysis)
            
            if analysis_summary:
                result['analysis_summary'] = analysis_summary
                print("[VideoAnalyzer] Краткая версия сгенерирована успешно")
            else:
                print("[VideoAnalyzer] Краткая версия не сгенерирована, но анализ сохранен")
                # Не падаем в ошибку, просто не добавляем краткую версию
            
            # Переводим заголовок видео на русский, если он не на русском
            if 'title' in result:
                title = result['title']
                print(f"[VideoAnalyzer] Переводим заголовок: '{title[:50]}...'")
                translated_title = self.translate_video_title(title)
                if translated_title != title:
                    result['translated_title'] = translated_title
                    print(f"[VideoAnalyzer] Заголовок переведен: '{title}' -> '{translated_title}'")
                else:
                    result['translated_title'] = title
                    print(f"[VideoAnalyzer] Заголовок уже на русском: '{title}'")
            
        except Exception as e:
            import traceback
            print(f"[VideoAnalyzer] Ошибка при анализе видео: {e}")
            print(f"[VideoAnalyzer] Полный traceback:")
            print(traceback.format_exc())
            result['error'] = f'Непредвиденная ошибка: {str(e)}'
        
        print(f"[VideoAnalyzer] === КОНЕЦ АНАЛИЗА ВИДЕО ===")
        print(f"[VideoAnalyzer] Результат: {'УСПЕХ' if not result.get('error') else 'ОШИБКА: ' + result['error']}")
        return result 