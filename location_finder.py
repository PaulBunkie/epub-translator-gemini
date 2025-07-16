# --- START OF FILE location_finder.py ---
import requests
import google.generativeai as genai
import os
import traceback
import time
import json
import datetime
from typing import Optional

from db_manager import get_cached_location, save_cached_location

NEWS_API_KEY = "2126e6e18adb478fb9ade262cb1102af"
NEWS_API_URL = 'https://newsapi.org/v2/everything'
REQUEST_TIMEOUT_SECONDS = 20
NEWS_FETCH_DAYS_AGO = 3
LOCATION_CACHE_TTL_SECONDS = 4000
USER_REQUEST_CACHE_TTL_SECONDS = 86400  # 24 часа для пользовательских запросов

# Модели для анализа локаций (как в video_analyzer.py)
PRIMARY_MODEL = os.getenv("LOCATION_FINDER_PRIMARY_MODEL", "gemini-2.5-flash-preview-05-20")
FALLBACK_MODEL = os.getenv("LOCATION_FINDER_FALLBACK_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

_gemini_model_instance = None
_is_gemini_api_configured = False
LF_PRINT_PREFIX = "[LF]"

class LocationFinderAI:
    """
    Класс для анализа локаций с fallback на разные AI модели.
    Поддерживает Google Gemini и OpenRouter API.
    """
    
    def __init__(self):
        # Google API
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        
        # OpenRouter API
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.openrouter_api_url = "https://openrouter.ai/api/v1"
        
        # Модели для анализа
        self.primary_model = PRIMARY_MODEL
        self.fallback_model = FALLBACK_MODEL
        
        if not self.google_api_key and not self.openrouter_api_key:
            raise ValueError("Необходимо установить GOOGLE_API_KEY или OPENROUTER_API_KEY")
    
    def _get_api_source(self, model_name: str) -> str:
        """
        Определяет источник API по имени модели.
        Аналогично translation_module.py
        """
        if model_name.startswith("gemini-"):
            return "google"
        elif "/" in model_name:  # OpenRouter модели содержат "/"
            return "openrouter"
        else:
            return "google"  # По умолчанию считаем Google
    
    def _analyze_with_google(self, person_name: str, news_summaries_text: str, model_name: str) -> Optional[dict]:
        """
        Анализирует локацию с помощью Google Gemini API.
        """
        try:
            if not self.google_api_key:
                print(f"{LF_PRINT_PREFIX} Google API ключ не установлен")
                return None
            
            # Инициализация Gemini
            genai.configure(api_key=self.google_api_key)
            model = genai.GenerativeModel(model_name)
            
            # Промпт
            prompt = self._get_location_prompt_template(person_name, news_summaries_text)
            print(f"{LF_PRINT_PREFIX} [Google] Отправка запроса к {model_name} для '{person_name}'...")
            
            response = model.generate_content(prompt)
            
            if response.text:
                location_text_raw = response.text.strip()
                print(f"{LF_PRINT_PREFIX} [Google] Ответ от {model_name}: '{location_text_raw}'")
                return self._parse_location_response(location_text_raw, person_name)
            else:
                print(f"{LF_PRINT_PREFIX} [Google] Пустой ответ от {model_name}")
                return None
                
        except Exception as e:
            print(f"{LF_PRINT_PREFIX} [Google] Ошибка при анализе с {model_name}: {e}")
            return None
    
    def _analyze_with_openrouter(self, person_name: str, news_summaries_text: str, model_name: str) -> Optional[dict]:
        """
        Анализирует локацию с помощью OpenRouter API.
        """
        try:
            if not self.openrouter_api_key:
                print(f"{LF_PRINT_PREFIX} OpenRouter API ключ не установлен")
                return None
            
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
            
            prompt = self._get_location_prompt_template(person_name, news_summaries_text)
            
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 200,
                "temperature": 0.1
            }
            
            print(f"{LF_PRINT_PREFIX} [OpenRouter] Отправка запроса к {model_name} для '{person_name}'...")
            
            response = requests.post(
                f"{self.openrouter_api_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            
            print(f"{LF_PRINT_PREFIX} [OpenRouter] Получен ответ: Статус {response.status_code}")
            
            # Проверка заголовков лимитов
            if 'X-Ratelimit-Remaining' in response.headers:
                print(f"{LF_PRINT_PREFIX} [OpenRouter] X-Ratelimit-Remaining: {response.headers['X-Ratelimit-Remaining']}")
            if 'X-Ratelimit-Limit' in response.headers:
                print(f"{LF_PRINT_PREFIX} [OpenRouter] X-Ratelimit-Limit: {response.headers['X-Ratelimit-Limit']}")
            if 'X-Ratelimit-Reset' in response.headers:
                print(f"{LF_PRINT_PREFIX} [OpenRouter] X-Ratelimit-Reset: {response.headers['X-Ratelimit-Reset']}")
            
            if response.status_code == 200:
                data = response.json()
                if 'choices' in data and len(data['choices']) > 0:
                    content = data['choices'][0]['message']['content'].strip()
                    print(f"{LF_PRINT_PREFIX} [OpenRouter] Ответ от {model_name}: '{content}'")
                    return self._parse_location_response(content, person_name)
                else:
                    print(f"{LF_PRINT_PREFIX} [OpenRouter] Неверный формат ответа от {model_name}")
                    return None
            else:
                print(f"{LF_PRINT_PREFIX} [OpenRouter] HTTP ошибка {response.status_code} от {model_name}")
                try:
                    error_details = response.json()
                    print(f"{LF_PRINT_PREFIX} [OpenRouter] Детали ошибки: {error_details}")
                except:
                    print(f"{LF_PRINT_PREFIX} [OpenRouter] Текст ошибки: {response.text[:500]}...")
                return None
                
        except Exception as e:
            print(f"{LF_PRINT_PREFIX} [OpenRouter] Ошибка при анализе с {model_name}: {e}")
            return None
    
    def _get_location_prompt_template(self, person_name: str, news_summaries_text: str) -> str:
        """
        Формирует промпт для анализа локации.
        """
        return f"""Analyze the following news summaries about {person_name}.
Your primary goal is to identify a specific geographic location (city and country) where {person_name} has demonstrably been physically present or performed a significant action very recently (e.g., within the last 1-2 days).
Consider actions like: - Explicitly stated arrivals, visits, or current presence. - Making official statements or appearances from a specified location. - Reports of them being seen or engaging in activities at a particular place.
De-prioritize or ignore: - Planned future visits, upcoming meetings, or speculative travel. - General discussions about locations without confirmed recent presence. - Locations mentioned only in the context of other people interacting with {person_name} unless {person_name}'s presence there is also confirmed. - Vague locations like "on a plane" unless a specific destination of arrival is mentioned in conjunction.
If a credible recent physical location (city and country) can be determined, provide it in the format: "Country, City".
Examples: "Russia, Moscow", "USA, Washington D.C.", "Qatar, Doha".
If multiple recent locations are mentioned, try to determine the most current one.
If {person_name} is reported to be in their primary country of operation (e.g., their capital city) making official statements or performing duties, and no more recent international travel is clearly confirmed, this can be considered.
If, after careful analysis, no such specific, recent, and confirmed physical location can be reasonably determined from the provided texts, respond with "Unknown".
Your entire response MUST BE ONLY "Country, City" OR "Unknown". Do not add any explanations, apologies, or other text.
News summaries for {person_name}:
{news_summaries_text}
Location:"""
    
    def _parse_location_response(self, location_text_raw: str, person_name: str) -> dict:
        """
        Парсит ответ AI и возвращает структурированные данные.
        """
        location_name_for_geocoding = "Unknown"
        geocoding_error_message = None
        
        if not location_text_raw:
            print(f"{LF_PRINT_PREFIX} Пустой ответ -> 'Unknown'.")
        elif location_text_raw == "Unknown":
            print(f"{LF_PRINT_PREFIX} -> 'Unknown'.")
        else:
            parts = location_text_raw.split(',')
            if len(parts) >= 2:
                country = parts[0].strip()
                city = ",".join(parts[1:]).strip()
                if country and city and len(location_text_raw) < 150:
                    location_name_for_geocoding = f"{country}, {city}"
                else:
                    geocoding_error_message = f"Format error: {location_text_raw}"
                    location_name_for_geocoding = "Unknown"
            else:
                geocoding_error_message = f"Unexpected format: {location_text_raw}"
                location_name_for_geocoding = "Unknown"

        lat, lon = None, None
        final_name_to_return = location_name_for_geocoding if location_name_for_geocoding != "Unknown" else location_text_raw
        
        if location_name_for_geocoding != "Unknown":
            lat, lon = _geocode_location(location_name_for_geocoding)
            if lat is None or lon is None:
                geocoding_error_message = (geocoding_error_message + "; " if geocoding_error_message else "") + f"Geocoding failed for '{location_name_for_geocoding}'"
        elif geocoding_error_message is None and location_text_raw == "Unknown":
            geocoding_error_message = "Location is Unknown"

        return {
            "location_name": final_name_to_return,
            "lat": lat,
            "lon": lon,
            "error": geocoding_error_message
        }
    
    def analyze_location(self, person_name: str, news_summaries_text: str) -> Optional[dict]:
        """
        Анализирует локацию с fallback логикой.
        Сначала пробует основную модель, затем резервные.
        """
        # Список моделей для попыток (основная + резервная)
        models_to_try = [self.primary_model, self.fallback_model]
        
        for model in models_to_try:
            print(f"{LF_PRINT_PREFIX} Пробуем модель: {model}")
            
            # Определяем источник API
            api_source = self._get_api_source(model)
            
            if api_source == "google":
                result = self._analyze_with_google(person_name, news_summaries_text, model)
            elif api_source == "openrouter":
                result = self._analyze_with_openrouter(person_name, news_summaries_text, model)
            else:
                print(f"{LF_PRINT_PREFIX} Неизвестный источник API для модели {model}")
                continue
            
            if result:
                print(f"{LF_PRINT_PREFIX} Успешный анализ с моделью {model}")
                return result
            else:
                print(f"{LF_PRINT_PREFIX} Модель {model} не дала результата, пробуем следующую")
        
        print(f"{LF_PRINT_PREFIX} Все модели не сработали")
        return None

# Глобальный экземпляр LocationFinderAI
_location_finder_ai = None

def _get_location_finder_ai() -> LocationFinderAI:
    """
    Возвращает глобальный экземпляр LocationFinderAI.
    """
    global _location_finder_ai
    if _location_finder_ai is None:
        _location_finder_ai = LocationFinderAI()
    return _location_finder_ai

def _initialize_gemini():
    global _is_gemini_api_configured, _gemini_model_instance
    if _gemini_model_instance:
        print(f"{LF_PRINT_PREFIX} Gemini модель '{GEMINI_MODEL_NAME}' уже загружена.")
        return True
    print(f"{LF_PRINT_PREFIX} Начало инициализации Gemini...")
    if not _is_gemini_api_configured:
        if not (hasattr(genai, 'API_KEY') and genai.API_KEY):
            try:
                from translation_module import configure_api as configure_main_gemini_api
                print(f"{LF_PRINT_PREFIX} Попытка конфигурации API через translation_module...")
                configure_main_gemini_api()
                print(f"{LF_PRINT_PREFIX} Gemini API успешно сконфигурирован через translation_module.")
                _is_gemini_api_configured = True
            except ImportError:
                print(f"{LF_PRINT_PREFIX} translation_module не найден. Попытка конфигурации через env GOOGLE_API_KEY.")
                api_key_env = os.getenv("GOOGLE_API_KEY")
                if api_key_env:
                    try:
                        genai.configure(api_key=api_key_env)
                        print(f"{LF_PRINT_PREFIX} Gemini API успешно сконфигурирован через GOOGLE_API_KEY.")
                        _is_gemini_api_configured = True
                    except Exception as e_cfg:
                        print(f"{LF_PRINT_PREFIX} ОШИБКА конфигурации Gemini API через GOOGLE_API_KEY: {e_cfg}")
                        return False
                else:
                    print(f"{LF_PRINT_PREFIX} КРИТИКА: Ключ GOOGLE_API_KEY не найден и translation_module недоступен.")
                    return False
            except ValueError as ve:
                 print(f"{LF_PRINT_PREFIX} ОШИБКА при вызове translation_module.configure_api: {ve}")
                 return False
            except Exception as e_main_cfg:
                 print(f"{LF_PRINT_PREFIX} Неожиданная ОШИБКА при вызове translation_module.configure_api: {e_main_cfg}")
                 traceback.print_exc()
                 return False
        else:
            print(f"{LF_PRINT_PREFIX} Gemini API, похоже, уже сконфигурирован (genai.API_KEY установлен).")
            _is_gemini_api_configured = True
    if not _is_gemini_api_configured and not (hasattr(genai, 'API_KEY') and genai.API_KEY):
        print(f"{LF_PRINT_PREFIX} Не удалось сконфигурировать Gemini API.")
        return False
    try:
        print(f"{LF_PRINT_PREFIX} Загрузка модели Gemini: {GEMINI_MODEL_NAME}...")
        safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
        generation_config = genai.types.GenerationConfig(temperature=0.1)
        _gemini_model_instance = genai.GenerativeModel(GEMINI_MODEL_NAME, safety_settings=safety_settings, generation_config=generation_config)
        print(f"{LF_PRINT_PREFIX} Модель Gemini '{GEMINI_MODEL_NAME}' успешно загружена.")
        return True
    except Exception as e_model:
        print(f"{LF_PRINT_PREFIX} КРИТИЧЕСКАЯ ОШИБКА загрузки модели ({GEMINI_MODEL_NAME}): {e_model}")
        traceback.print_exc()
        _gemini_model_instance = None
        return False

def _fetch_news(person_name: str, num_articles: int = 100, days_ago: int = NEWS_FETCH_DAYS_AGO):
    if not NEWS_API_KEY:
        print(f"{LF_PRINT_PREFIX} ОШИБКА: NEWS_API_KEY не установлен для '{person_name}'.")
        return []
    actual_page_size = min(num_articles, 100)
    from_date_str = (datetime.date.today() - datetime.timedelta(days=days_ago)).strftime('%Y-%m-%d')
    params = {
        'qInTitle': person_name, 'language': 'en', 'sortBy': 'publishedAt',
        'pageSize': actual_page_size, 'apiKey': NEWS_API_KEY, 'from': from_date_str
    }
    headers = {'User-Agent': 'LocationFinderApp/1.0 (epub_translator project; paulbunkie@gmail.com)'}
    print(f"{LF_PRINT_PREFIX} Запрос новостей для '{person_name}' с NewsAPI (с {from_date_str}). Params: qInTitle={params.get('qInTitle')}, pageSize={params.get('pageSize')}")
    try:
        response = requests.get(NEWS_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        print(f"{LF_PRINT_PREFIX} NewsAPI для '{person_name}' ответил статусом: {response.status_code}")
        response.raise_for_status()
        data = response.json()
        articles_data = data.get('articles', [])
        print(f"{LF_PRINT_PREFIX} Получено {len(articles_data)} статей для '{person_name}' (запрошено {actual_page_size} за посл. {days_ago} дня).")
        if articles_data: print(f"{LF_PRINT_PREFIX} Пример заголовка: {articles_data[0].get('title')}")
        filtered_articles = []
        for art in articles_data:
            title = art.get("title")
            if title:
                description = art.get("description")
                filtered_articles.append({"title": title, "description": description})
        print(f"{LF_PRINT_PREFIX} Отфильтровано (по наличию title) {len(filtered_articles)} статей для '{person_name}'.")
        return filtered_articles
    except requests.exceptions.Timeout: print(f"{LF_PRINT_PREFIX} ОШИБКА: Таймаут NewsAPI для '{person_name}'.")
    except requests.exceptions.HTTPError as e: print(f"{LF_PRINT_PREFIX} ОШИБКА HTTP NewsAPI для '{person_name}': {e.response.status_code} {e.response.text[:100]}")
    except Exception as e: print(f"{LF_PRINT_PREFIX} ОШИБКА в _fetch_news для '{person_name}': {e}"); traceback.print_exc()
    return []

def _geocode_location(location_name: str):
    if not location_name or location_name == "Unknown": return None, None
    headers = {'User-Agent': 'LocationFinderApp/1.0 (epub_translator project; paulbunkie@gmail.com)'}
    params = {'q': location_name, 'format': 'json', 'limit': 1}
    nominatim_url = "https://nominatim.openstreetmap.org/search"
    print(f"{LF_PRINT_PREFIX} Геокодинг для '{location_name}'...")
    try:
        time.sleep(1.1)
        response = requests.get(nominatim_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and isinstance(data, list) and data[0]:
            place = data[0]
            lat, lon = float(place.get('lat')), float(place.get('lon'))
            print(f"{LF_PRINT_PREFIX} Геокодинг '{location_name}': lat={lat}, lon={lon}")
            return lat, lon
    except Exception as e: print(f"{LF_PRINT_PREFIX} ОШИБКА геокодинга '{location_name}': {e}")
    return None, None



def _get_location_from_ai(person_name: str, news_summaries_text: str):
    """
    Получает локацию с помощью AI (с fallback логикой).
    """
    try:
        location_finder_ai = _get_location_finder_ai()
        result = location_finder_ai.analyze_location(person_name, news_summaries_text)
        
        if result:
            return result
        else:
            return {"location_name": "Error", "lat": None, "lon": None, "error": "AI analysis failed"}
            
    except Exception as e:
        print(f"{LF_PRINT_PREFIX} Ошибка при анализе локации: {e}")
        return {"location_name": "Error", "lat": None, "lon": None, "error": f"AI analysis error: {e}"}


def find_persons_locations(person_names: list, test_mode: bool = False, force_fresh: bool = False):
    results = {}
    print(f"\n{LF_PRINT_PREFIX} Запуск find_persons_locations для: {person_names}. Тестовый режим: {test_mode}, Принудительное обновление: {force_fresh}")

    if not person_names:
        print(f"{LF_PRINT_PREFIX} Список person_names пуст.")
        return {"error": "No person names provided"}

    if not test_mode:
        # Проверяем доступность AI через LocationFinderAI
        try:
            _get_location_finder_ai()
        except Exception as e:
            print(f"{LF_PRINT_PREFIX} ОШИБКА: Не удалось инициализировать AI. Прерывание: {e}")
            for name_original in person_names:
                 results[name_original if isinstance(name_original, str) else str(name_original)] = {
                     "location_name": "Error", "lat": None, "lon": None,
                     "error": f"AI initialization failed: {e}"
                 }
            return results

    current_time_unix = time.time()

    for original_person_name in person_names:
        person_name_cleaned = ""
        person_name_key = ""

        if not isinstance(original_person_name, str) or not original_person_name.strip():
            error_key_name = original_person_name if isinstance(original_person_name, str) else f"invalid_entry_{type(original_person_name).__name__}"
            results[error_key_name] = {"location_name": "Invalid Name", "lat": None, "lon": None, "error": "Invalid person name provided"}
            continue

        person_name_cleaned = original_person_name.strip()
        person_name_key = person_name_cleaned.lower()
        print(f"\n{LF_PRINT_PREFIX} Обработка: '{person_name_cleaned}' (ключ: '{person_name_key}')")

        if test_mode:
            print(f"{LF_PRINT_PREFIX} Тестовый режим для '{person_name_cleaned}'. Заглушка: Стамбул.")
            results[person_name_cleaned] = {
                "location_name": "Turkey, Istanbul (Test)", "lat": 41.0082, "lon": 28.9784, "error": None,
                "last_updated": int(current_time_unix)
            }
            time.sleep(0.1); continue

        # Определяем TTL в зависимости от режима
        cache_ttl = USER_REQUEST_CACHE_TTL_SECONDS if not force_fresh else LOCATION_CACHE_TTL_SECONDS
        
        use_fresh_data = force_fresh  # Если force_fresh=True, всегда запрашиваем свежие данные
        cached_entry = get_cached_location(person_name_key)
        had_good_stale_cache = False

        if cached_entry and not force_fresh:
            cache_age = current_time_unix - cached_entry["last_updated"]
            
            # Добавляем отладочную информацию
            print(f"{LF_PRINT_PREFIX} Детали кэша для '{person_name_key}':")
            print(f"{LF_PRINT_PREFIX}   - location_name: '{cached_entry.get('location_name')}'")
            print(f"{LF_PRINT_PREFIX}   - lat: {cached_entry.get('lat')}")
            print(f"{LF_PRINT_PREFIX}   - lon: {cached_entry.get('lon')}")
            print(f"{LF_PRINT_PREFIX}   - error: '{cached_entry.get('error')}'")
            
            is_good_cache_entry = (
                cached_entry.get("lat") is not None and
                cached_entry.get("lon") is not None and
                not cached_entry.get("error") and
                cached_entry.get("location_name") != "Unknown" and
                not (cached_entry.get("location_name") or "").lower().startswith("error")
            )

            if is_good_cache_entry:
                if cache_age < cache_ttl:
                    print(f"{LF_PRINT_PREFIX} 'Хороший' кэш для '{person_name_key}' актуален (возраст: {int(cache_age)} сек, TTL: {cache_ttl} сек). Используем его.")
                    results[person_name_cleaned] = {
                        "location_name": cached_entry["location_name"], "lat": cached_entry["lat"],
                        "lon": cached_entry["lon"], "error": cached_entry["error"],
                        "last_updated": cached_entry["last_updated"]
                    }
                    use_fresh_data = False
                else:
                    print(f"{LF_PRINT_PREFIX} 'Хороший' кэш для '{person_name_key}' устарел (возраст: {int(cache_age)} сек, TTL: {cache_ttl} сек). Попытаемся обновить.")
                    had_good_stale_cache = True
                    use_fresh_data = True  # Запрашиваем свежие данные для устаревшего кэша
            else:
                print(f"{LF_PRINT_PREFIX} Кэш для '{person_name_key}' 'плохой' (Unknown/ошибка/нет координат). Запрашиваем свежие данные.")
                use_fresh_data = True  # Запрашиваем свежие данные для плохого кэша
        elif force_fresh:
            print(f"{LF_PRINT_PREFIX} Принудительное обновление для '{person_name_key}'. Игнорируем кэш.")
            use_fresh_data = True  # Принудительное обновление
        else:
            print(f"{LF_PRINT_PREFIX} Кэш для '{person_name_key}' не найден в БД. Запрашиваем свежие данные.")
            use_fresh_data = True  # Запрашиваем свежие данные если кэша нет

        if not use_fresh_data:
            print(f"{LF_PRINT_PREFIX} Пропускаем получение свежих данных для '{person_name_cleaned}' (use_fresh_data=False)")
            continue

        print(f"{LF_PRINT_PREFIX} Получение свежих данных для '{person_name_cleaned}' (use_fresh_data=True)...")
        articles = _fetch_news(person_name_cleaned, num_articles=100, days_ago=NEWS_FETCH_DAYS_AGO)

        person_api_data_fresh = {}
        news_summaries_text_for_cache = "N/A"

        if not articles:
            person_api_data_fresh = {"location_name": "Error", "lat": None, "lon": None, "error": "Could not fetch news"}
        else:
            news_summaries = []
            for art_idx, article_item in enumerate(articles):
                title = article_item.get("title","").strip(); description = article_item.get("description","") # Не strip() здесь, чтобы сохранить None
                if title: # Берем если есть title
                     news_summaries.append(f"Article {art_idx+1}:{chr(10)}Title: {title}{chr(10)}Description: {description if description else ''}{chr(10)}---")

            if not news_summaries:
                person_api_data_fresh = {"location_name": "Error", "lat": None, "lon": None, "error": "No suitable news summaries found"}
            else:
                news_text = "\n\n".join(news_summaries)
                news_summaries_text_for_cache = news_text[:500] + ("..." if len(news_text)>500 else "")
                summary_preview_len = 1000
                text_preview = news_text[:summary_preview_len]
                remaining_chars = len(news_text) - summary_preview_len if len(news_text) > summary_preview_len else 0
                print(f"{chr(10)}{LF_PRINT_PREFIX} ---- ТЕКСТ ДЛЯ GEMINI ({person_name_cleaned}) (из {len(news_summaries)} статей, превью) ----{chr(10)}{text_preview}...{chr(10)}(Далее еще {remaining_chars} симв.){chr(10)}---- КОНЕЦ ТЕКСТА ----{chr(10)}")
                print(f"{LF_PRINT_PREFIX} Сформировано саммари ({len(news_summaries)} статей). Длина: {len(news_text)}.")
                MAX_CHARS_FOR_GEMINI = 750000
                if len(news_text) > MAX_CHARS_FOR_GEMINI:
                    news_text = news_text[:MAX_CHARS_FOR_GEMINI] + "\n...(truncated)"
                person_api_data_fresh = _get_location_from_ai(person_name_cleaned, news_text)

        person_api_data_fresh["last_updated"] = int(current_time_unix)

        is_fresh_data_good = (
            person_api_data_fresh.get("lat") is not None and
            person_api_data_fresh.get("lon") is not None and
            not person_api_data_fresh.get("error") and
            person_api_data_fresh.get("location_name") != "Unknown" and
            not (person_api_data_fresh.get("location_name") or "").lower().startswith("error")
        )

        final_data_for_person = {}
        if is_fresh_data_good:
            print(f"{LF_PRINT_PREFIX} Получены 'хорошие' свежие данные для '{person_name_cleaned}'.")
            final_data_for_person = person_api_data_fresh
            save_cached_location(person_name_key, final_data_for_person, source_summary=news_summaries_text_for_cache)
        elif had_good_stale_cache and cached_entry and not force_fresh:
            # Используем старый кэш только если НЕ принудительное обновление
            print(f"{LF_PRINT_PREFIX} Свежие данные 'плохие'. Используем старый 'хороший' кэш для '{person_name_cleaned}'.")
            final_data_for_person = {
                "location_name": cached_entry["location_name"],
                "lat": cached_entry["lat"], "lon": cached_entry["lon"],
                "error": cached_entry["error"],
                "last_updated": cached_entry["last_updated"]
            }
        else:
            print(f"{LF_PRINT_PREFIX} Свежие данные 'плохие', старого хорошего кэша нет или принудительное обновление. Используем/сохраняем 'плохие' свежие для '{person_name_cleaned}'.")
            final_data_for_person = person_api_data_fresh
            save_cached_location(person_name_key, final_data_for_person, source_summary=news_summaries_text_for_cache)

        results[person_name_cleaned] = final_data_for_person

        if len(person_names) > 1 and original_person_name != person_names[-1]: time.sleep(1)

    print(f"{LF_PRINT_PREFIX} Завершение. Результаты: {json.dumps(results, ensure_ascii=False, indent=2)}")
    return results

PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE = [
    "Putin", "Trump", "Zelensky"
    #, "Macron", "Merz", "Starmer", "Xi Jinping", "Kim Jong Un"
]

def update_locations_for_predefined_persons():
    print(f"\n{LF_PRINT_PREFIX} === ЗАПУСК ФОНОВОГО ОБНОВЛЕНИЯ ЛОКАЦИЙ ===")
    print(f"{LF_PRINT_PREFIX} Персоны: {PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE}")
    try:
        # Фоновое обновление всегда принудительное
        find_persons_locations(PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE, test_mode=False, force_fresh=True)
        print(f"{LF_PRINT_PREFIX} === ФОНОВОЕ ОБНОВЛЕНИЕ ЛОКАЦИЙ УСПЕШНО ЗАВЕРШЕНО ===")
    except Exception as e:
        print(f"{LF_PRINT_PREFIX} === ФОНОВОЕ ОБНОВЛЕНИЕ ЛОКАЦИЙ ОШИБКА: {e} ===")
        traceback.print_exc()

def find_persons_locations_for_user(person_names: list, test_mode: bool = False):
    """
    Функция для пользовательских запросов - в основном использует кэш.
    Запрашивает свежие данные только если в БД нет данных за последние сутки.
    """
    print(f"{LF_PRINT_PREFIX} Пользовательский запрос для: {person_names}")
    return find_persons_locations(person_names, test_mode=test_mode, force_fresh=False)

if __name__ == '__main__':
    print(f"{LF_PRINT_PREFIX} --- Тестирование location_finder.py (локальный запуск) ---")
    try:
        import db_manager
        print(f"{LF_PRINT_PREFIX} Вызов db_manager.init_db() для теста...")
        db_manager.init_db()
    except ImportError:
        print(f"{LF_PRINT_PREFIX} Не удалось импортировать db_manager для инициализации БД в тесте.")

    print(f"{LF_PRINT_PREFIX} --- Тест пользовательского запроса (кэш-приоритет) ---")
    user_test_persons = ["Trump", "Biden"]
    user_locations_data = find_persons_locations_for_user(user_test_persons, test_mode=False)
    print(f"{LF_PRINT_PREFIX}\n--- Результаты пользовательского запроса ---")
    for person, data in user_locations_data.items():
        print(f"{LF_PRINT_PREFIX} {person}: Name='{data.get('location_name')}', Lat={data.get('lat')}, Lon={data.get('lon')}, Error='{data.get('error')}', UpdatedTS={data.get('last_updated')}")
    
    print(f"{LF_PRINT_PREFIX} --- Тест фонового обновления (принудительное) ---")
    background_locations_data = find_persons_locations(user_test_persons, test_mode=False, force_fresh=True)
    print(f"{LF_PRINT_PREFIX}\n--- Результаты фонового обновления ---")
    for person, data in background_locations_data.items():
        print(f"{LF_PRINT_PREFIX} {person}: Name='{data.get('location_name')}', Lat={data.get('lat')}, Lon={data.get('lon')}, Error='{data.get('error')}', UpdatedTS={data.get('last_updated')}")
# --- END OF location_finder.py ---