import os
import requests
import json
import re
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any
import time

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
                except:
                    print(f"[VideoAnalyzer] Текст ошибки: {response.text[:500]}...")
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
    
    def get_sharing_url_session(self, video_url: str) -> Optional[str]:
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
                except:
                    print(f"[VideoAnalyzer] Fallback текст ошибки: {response.text[:500]}...")
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
            
            while attempt < max_attempts:
                attempt += 1
                
                if attempt > 1:
                    time.sleep(poll_interval_ms / 1000)
                
                second_response = requests.post(
                    api_url,
                    json=second_payload,
                    headers=headers,
                    timeout=30
                )
                
                if second_response.status_code != 200:
                    return None
                
                second_data = second_response.json()
                error_code = second_data.get('error_code')
                status_code = second_data.get('status_code')
                
                if error_code or status_code > 1:
                    return None
                
                sharing_url = second_data.get('sharing_url', '')
                if sharing_url:
                    print(f"[VideoAnalyzer] Fallback дал sharing URL: {sharing_url}")
                    return sharing_url
                
                poll_interval_ms = second_data.get('poll_interval_ms', poll_interval_ms)
            
            return None
            
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка fallback: {e}")
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
4. Практические выводы или рекомендации

Итог дай в виде связного обзора с примерно 7-10 пунктами, раскрывающих основные уникальные моменты материала."""

            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000")
            }
            
            # Используем модель по умолчанию
            model = "microsoft/mai-ds-r1:free"
            
            # Retry логика с уменьшением max_tokens
            max_tokens_options = [64000, 32000, 16000, 8000, 4000, 2000]
            
            for max_tokens in max_tokens_options:
                try:
                    payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": f"{prompt}\n\nТекст для анализа:\n\n{text}"
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
                                print(f"[VideoAnalyzer] Получен анализ длиной {len(content)} символов")
                                return content
                            else:
                                print("[VideoAnalyzer] Неверный формат ответа от OpenRouter API")
                                print(f"[VideoAnalyzer] Структура ответа: {data}")
                                continue  # Пробуем с меньшим max_tokens
                        except json.JSONDecodeError as e:
                            print(f"[VideoAnalyzer] Ошибка парсинга JSON: {e}")
                            print(f"[VideoAnalyzer] Текст ответа: {response.text[:500]}...")
                            continue  # Пробуем с меньшим max_tokens
                    else:
                        print(f"[VideoAnalyzer] HTTP ошибка OpenRouter API: {response.status_code}")
                        try:
                            error_details = response.json()
                            print(f"[VideoAnalyzer] Детали ошибки: {error_details}")
                        except:
                            print(f"[VideoAnalyzer] Текст ошибки: {response.text[:500]}...")
                        continue  # Пробуем с меньшим max_tokens
                        
                except requests.exceptions.Timeout:
                    print(f"[VideoAnalyzer] Таймаут при max_tokens={max_tokens}, пробуем меньше")
                    continue
                except Exception as e:
                    print(f"[VideoAnalyzer] Ошибка при max_tokens={max_tokens}: {e}")
                    continue
            
            print("[VideoAnalyzer] Все попытки с разными max_tokens не удались")
            return None
                
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при анализе текста: {e}")
            return None
    
    def analyze_video(self, video_url: str) -> dict:
        """
        Анализирует видео по URL, получая sharing URL от Yandex API,
        извлекая текст из страницы и анализируя его через OpenRouter.
        
        Args:
            video_url: URL видео для анализа
            
        Returns:
            Словарь с результатами анализа
        """
        result = {
            'video_url': video_url,
            'sharing_url': None,
            'extracted_text': None,
            'analysis': None,
            'error': None
        }
        
        try:
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
            if not result['sharing_url']:
                if self.session_id:
                    print("[VideoAnalyzer] Попытка через fallback (сессия)...")
                    sharing_url = self.get_sharing_url_session(video_url)
                    if sharing_url:
                        result['sharing_url'] = sharing_url
                        print(f"[VideoAnalyzer] Успешно получен sharing URL через fallback: {sharing_url}")
                    else:
                        print("[VideoAnalyzer] Fallback тоже не сработал")
                else:
                    print("[VideoAnalyzer] Fallback недоступен (не установлен YANDEX_SESSION_ID)")
            
            # Если не удалось получить sharing URL
            if not result['sharing_url']:
                error_msg = "Не удалось получить sharing URL"
                if not self.yandex_token and not self.session_id:
                    error_msg += " (не установлены ни YANDEX_API_TOKEN, ни YANDEX_SESSION_ID)"
                elif not self.yandex_token:
                    error_msg += " (не установлен YANDEX_API_TOKEN)"
                elif not self.session_id:
                    error_msg += " (не установлен YANDEX_SESSION_ID)"
                result['error'] = error_msg
                return result
            
            # Извлекаем текст из sharing URL
            print(f"[VideoAnalyzer] Извлекаем текст из: {result['sharing_url']}")
            extracted_text = self.extract_text_from_sharing_url(result['sharing_url'])
            
            if not extracted_text:
                result['error'] = 'Не удалось извлечь текст из sharing URL'
                return result
            
            result['extracted_text'] = extracted_text
            print(f"[VideoAnalyzer] Извлечено {len(extracted_text)} символов текста")
            
            # Анализируем текст через OpenRouter
            print("[VideoAnalyzer] Отправляем текст на анализ в OpenRouter...")
            analysis = self.analyze_text_with_openrouter(extracted_text)
            
            if not analysis:
                result['error'] = 'Не удалось проанализировать текст через OpenRouter'
                return result
            
            result['analysis'] = analysis
            print("[VideoAnalyzer] Анализ завершен успешно")
            
        except Exception as e:
            print(f"[VideoAnalyzer] Ошибка при анализе видео: {e}")
            result['error'] = f'Непредвиденная ошибка: {str(e)}'
        
        return result 