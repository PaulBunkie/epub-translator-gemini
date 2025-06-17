# --- START OF FILE location_finder.py ---
import requests
import google.generativeai as genai
import os
import traceback
import time
import json
import datetime

from db_manager import get_cached_location, save_cached_location

NEWS_API_KEY = "2126e6e18adb478fb9ade262cb1102af"
NEWS_API_URL = 'https://newsapi.org/v2/everything'
GEMINI_MODEL_NAME = os.getenv("LOCATION_FINDER_MODEL_NAME", "gemini-2.5-flash-preview-05-20")
REQUEST_TIMEOUT_SECONDS = 20
NEWS_FETCH_DAYS_AGO = 3
LOCATION_CACHE_TTL_SECONDS = 4000

_gemini_model_instance = None
_is_gemini_api_configured = False
LF_PRINT_PREFIX = "[LF]"

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

def _get_gemini_prompt_template(person_name: str):
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
{{news_summaries_text}}
Location:"""

def _get_location_from_gemini(person_name: str, news_summaries_text: str):
    global _gemini_model_instance
    if not _gemini_model_instance:
        if not _initialize_gemini():
             return {"location_name": "Error", "lat": None, "lon": None, "error": "Gemini model not available (initialization failed)"}

    model_to_use = _gemini_model_instance
    prompt_template = _get_gemini_prompt_template(person_name)
    full_prompt = prompt_template.format(news_summaries_text=news_summaries_text)
    print(f"{LF_PRINT_PREFIX} Подготовлен промпт для Gemini для '{person_name}'. Длина: {len(full_prompt)}.")

    max_retries = 2
    for attempt in range(max_retries):
        try:
            print(f"{LF_PRINT_PREFIX} Запрос к Gemini ({model_to_use.model_name}) для '{person_name}' (попытка {attempt + 1}/{max_retries})...")
            start_time = time.time()
            response = model_to_use.generate_content(full_prompt)
            end_time = time.time()
            print(f"{LF_PRINT_PREFIX} Gemini ответил для '{person_name}' за {end_time - start_time:.2f} сек.")

            if response.parts:
                location_text_raw = response.text.strip()
                print(f"{LF_PRINT_PREFIX} Сырой ответ Gemini для '{person_name}': '{location_text_raw}'")
                location_name_for_geocoding = "Unknown"; geocoding_error_message = None
                if not location_text_raw: print(f"{LF_PRINT_PREFIX} Gemini пустой ответ -> 'Unknown'.")
                elif location_text_raw == "Unknown": print(f"{LF_PRINT_PREFIX} Gemini -> 'Unknown'.")
                else:
                    parts = location_text_raw.split(',');
                    if len(parts) >= 2:
                        country = parts[0].strip(); city = ",".join(parts[1:]).strip()
                        if country and city and len(location_text_raw) < 150:
                            location_name_for_geocoding = f"{country}, {city}"
                        else:
                            geocoding_error_message = f"Gemini format error: {location_text_raw}"
                            location_name_for_geocoding = "Unknown"
                    else:
                        geocoding_error_message = f"Gemini unexpected format: {location_text_raw}"
                        location_name_for_geocoding = "Unknown"

                lat, lon = None, None
                final_name_to_return = location_name_for_geocoding if location_name_for_geocoding != "Unknown" else location_text_raw
                if location_name_for_geocoding != "Unknown":
                    lat, lon = _geocode_location(location_name_for_geocoding)
                    if lat is None or lon is None:
                        geocoding_error_message = (geocoding_error_message + "; " if geocoding_error_message else "") + f"Geocoding failed for '{location_name_for_geocoding}'"
                elif geocoding_error_message is None and location_text_raw == "Unknown":
                    geocoding_error_message = "Location is Unknown (from Gemini)"

                # last_updated будет добавлен в find_persons_locations
                return {"location_name": final_name_to_return, "lat": lat, "lon": lon, "error": geocoding_error_message}

            elif response.prompt_feedback and response.prompt_feedback.block_reason:
                return {"location_name": "Blocked by Gemini", "lat": None, "lon": None, "error": f"Gemini request blocked ({response.prompt_feedback.block_reason})"}
            else:
                return {"location_name": "Error", "lat": None, "lon": None, "error": "Unexpected Gemini response structure"}
        except genai.types.generation_types.BlockedPromptException as bpe:
            print(f"{LF_PRINT_PREFIX} КРИТИКА: Промпт для '{person_name}' заблокирован Gemini: {bpe}")
            return {"location_name": "Error", "lat": None, "lon": None, "error": "Gemini prompt blocked"}
        except Exception as e:
            print(f"{LF_PRINT_PREFIX} ОШИБКА при запросе к Gemini для '{person_name}' (попытка {attempt + 1}): {e}")
            traceback.print_exc()
            if attempt == max_retries - 1:
                return {"location_name": "Error", "lat": None, "lon": None, "error": f"Gemini API failure after {max_retries} retries"}
            time.sleep(1.5)
    return {"location_name": "Error", "lat": None, "lon": None, "error": "Max retries exceeded for Gemini"}


def find_persons_locations(person_names: list, test_mode: bool = False):
    results = {}
    print(f"\n{LF_PRINT_PREFIX} Запуск find_persons_locations для: {person_names}. Тестовый режим: {test_mode}")

    if not person_names:
        print(f"{LF_PRINT_PREFIX} Список person_names пуст.")
        return {"error": "No person names provided"}

    if not test_mode:
        if not _gemini_model_instance and not _initialize_gemini():
            print(f"{LF_PRINT_PREFIX} ОШИБКА: Не удалось инициализировать Gemini. Прерывание.")
            for name_original in person_names:
                 results[name_original if isinstance(name_original, str) else str(name_original)] = {
                     "location_name": "Error", "lat": None, "lon": None,
                     "error": "Gemini initialization failed"
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

        use_fresh_data = True
        cached_entry = get_cached_location(person_name_key)
        had_good_stale_cache = False

        if cached_entry:
            cache_age = current_time_unix - cached_entry["last_updated"]
            is_good_cache_entry = (
                cached_entry.get("lat") is not None and
                cached_entry.get("lon") is not None and
                not cached_entry.get("error") and
                cached_entry.get("location_name") != "Unknown" and
                not (cached_entry.get("location_name") or "").lower().startswith("error")
            )

            if is_good_cache_entry:
                if cache_age < LOCATION_CACHE_TTL_SECONDS:
                    print(f"{LF_PRINT_PREFIX} 'Хороший' кэш для '{person_name_key}' актуален (возраст: {int(cache_age)} сек). Используем его.")
                    results[person_name_cleaned] = {
                        "location_name": cached_entry["location_name"], "lat": cached_entry["lat"],
                        "lon": cached_entry["lon"], "error": cached_entry["error"],
                        "last_updated": cached_entry["last_updated"]
                    }
                    use_fresh_data = False
                else:
                    print(f"{LF_PRINT_PREFIX} 'Хороший' кэш для '{person_name_key}' устарел (возраст: {int(cache_age)} сек). Попытаемся обновить.")
                    had_good_stale_cache = True
            else:
                print(f"{LF_PRINT_PREFIX} Кэш для '{person_name_key}' 'плохой' (Unknown/ошибка/нет координат). Запрашиваем свежие данные.")
        else:
            print(f"{LF_PRINT_PREFIX} Кэш для '{person_name_key}' не найден в БД. Запрашиваем свежие данные.")

        if not use_fresh_data:
            continue

        print(f"{LF_PRINT_PREFIX} Получение свежих данных для '{person_name_cleaned}'...")
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
                     news_summaries.append(f"Article {art_idx+1}:\nTitle: {title}\nDescription: {description if description else ''}\n---")

            if not news_summaries:
                person_api_data_fresh = {"location_name": "Error", "lat": None, "lon": None, "error": "No suitable news summaries found"}
            else:
                news_text = "\n\n".join(news_summaries)
                news_summaries_text_for_cache = news_text[:500] + ("..." if len(news_text)>500 else "")
                summary_preview_len = 1000
                text_preview = news_text[:summary_preview_len]
                remaining_chars = len(news_text) - summary_preview_len if len(news_text) > summary_preview_len else 0
                print(f"\n{LF_PRINT_PREFIX} ---- ТЕКСТ ДЛЯ GEMINI ({person_name_cleaned}) (из {len(news_summaries)} статей, превью) ----\n{text_preview}...\n(Далее еще {remaining_chars} симв.)\n---- КОНЕЦ ТЕКСТА ----\n")
                print(f"{LF_PRINT_PREFIX} Сформировано саммари ({len(news_summaries)} статей). Длина: {len(news_text)}.")
                MAX_CHARS_FOR_GEMINI = 750000
                if len(news_text) > MAX_CHARS_FOR_GEMINI:
                    news_text = news_text[:MAX_CHARS_FOR_GEMINI] + "\n...(truncated)"
                person_api_data_fresh = _get_location_from_gemini(person_name_cleaned, news_text)

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
        elif had_good_stale_cache and cached_entry:
            print(f"{LF_PRINT_PREFIX} Свежие данные 'плохие'. Используем старый 'хороший' кэш для '{person_name_cleaned}'.")
            final_data_for_person = {
                "location_name": cached_entry["location_name"],
                "lat": cached_entry["lat"], "lon": cached_entry["lon"],
                "error": cached_entry["error"],
                "last_updated": cached_entry["last_updated"]
            }
        else:
            print(f"{LF_PRINT_PREFIX} Свежие данные 'плохие', старого хорошего кэша нет. Используем/сохраняем 'плохие' свежие для '{person_name_cleaned}'.")
            final_data_for_person = person_api_data_fresh
            save_cached_location(person_name_key, final_data_for_person, source_summary=news_summaries_text_for_cache)

        results[person_name_cleaned] = final_data_for_person

        if len(person_names) > 1 and original_person_name != person_names[-1]: time.sleep(1)

    print(f"{LF_PRINT_PREFIX} Завершение. Результаты: {json.dumps(results, ensure_ascii=False, indent=2)}")
    return results

PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE = [
    "Putin", "Trump", "Zelensky" #, "Xi Jinping",
    #"Kim Jong Un", "Macron", "Merz", "Starmer"
]

def update_locations_for_predefined_persons():
    print(f"\n{LF_PRINT_PREFIX} === ЗАПУСК ФОНОВОГО ОБНОВЛЕНИЯ ЛОКАЦИЙ ===")
    print(f"{LF_PRINT_PREFIX} Персоны: {PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE}")
    if not _gemini_model_instance and not _initialize_gemini():
        print(f"{LF_PRINT_PREFIX} ФОН: ОШИБКА Gemini init. Обновление отменено.")
        return
    try:
        find_persons_locations(PREDEFINED_PERSONS_FOR_BACKGROUND_UPDATE, test_mode=False)
        print(f"{LF_PRINT_PREFIX} === ФОНОВОЕ ОБНОВЛЕНИЕ ЛОКАЦИЙ УСПЕШНО ЗАВЕРШЕНО ===")
    except Exception as e:
        print(f"{LF_PRINT_PREFIX} === ФОНОВОЕ ОБНОВЛЕНИЕ ЛОКАЦИЙ ОШИБКА: {e} ===")
        traceback.print_exc()

if __name__ == '__main__':
    print(f"{LF_PRINT_PREFIX} --- Тестирование location_finder.py (локальный запуск) ---")
    try:
        import db_manager
        print(f"{LF_PRINT_PREFIX} Вызов db_manager.init_db() для теста...")
        db_manager.init_db()
    except ImportError:
        print(f"{LF_PRINT_PREFIX} Не удалось импортировать db_manager для инициализации БД в тесте.")

    print(f"{LF_PRINT_PREFIX} --- Тест в РЕАЛЬНОМ режиме ---")
    real_test_persons = ["Trump", "Biden"]
    real_locations_data = find_persons_locations(real_test_persons, test_mode=False)
    print(f"{LF_PRINT_PREFIX}\n--- Результаты РЕАЛЬНОГО поиска локаций ---")
    for person, data in real_locations_data.items():
        print(f"{LF_PRINT_PREFIX} {person}: Name='{data.get('location_name')}', Lat={data.get('lat')}, Lon={data.get('lon')}, Error='{data.get('error')}', UpdatedTS={data.get('last_updated')}")
# --- END OF location_finder.py ---