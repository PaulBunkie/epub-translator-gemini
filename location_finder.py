# --- START OF FILE location_finder.py ---
import requests
import google.generativeai as genai
import os
import traceback 
import time 
import json 
import datetime

# --- Configuration ---
NEWS_API_KEY = "2126e6e18adb478fb9ade262cb1102af" 
NEWS_API_URL = 'https://newsapi.org/v2/everything'
GEMINI_MODEL_NAME = os.getenv("LOCATION_FINDER_MODEL_NAME", "gemini-1.5-flash") 
REQUEST_TIMEOUT_SECONDS = 20 

_gemini_model_instance = None
_is_gemini_api_configured = False

LF_PRINT_PREFIX = "[LF]"

# НЕ ДОЛЖНО БЫТЬ @app.route ЗДЕСЬ
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

# НЕ ДОЛЖНО БЫТЬ @app.route ЗДЕСЬ
def _fetch_news(person_name: str, num_articles: int = 100):
    if not NEWS_API_KEY:
        print(f"{LF_PRINT_PREFIX} ОШИБКА: NEWS_API_KEY не установлен для '{person_name}'.")
        return []

    # Вычисляем дату "3 дня назад"
    date_three_days_ago = datetime.date.today() - datetime.timedelta(days=3)
    from_date_iso = date_three_days_ago.strftime('%Y-%m-%d') # Формат ISO 8601 (только дата)

    actual_page_size = min(num_articles, 100)
    params = {
        'qInTitle': person_name,
        'language': 'en',
        'sortBy': 'publishedAt',
        'pageSize': actual_page_size,
        'apiKey': NEWS_API_KEY,
        'from': from_date_iso  # <--- Добавляем параметр 'from'
    }
    # ЗАМЕНИТЕ EMAIL на ваш или вашего приложения
    headers = {'User-Agent': 'LocationFinderApp/1.0 (Test Project; your_email@example.com)'}
    # Обновляем лог, чтобы включить новый параметр
    print(f"{LF_PRINT_PREFIX} Запрос новостей для '{person_name}' с NewsAPI. URL: {NEWS_API_URL}, Params: qInTitle={params.get('qInTitle')}, pageSize={params.get('pageSize')}, from={params.get('from')}")
    try:
        response = requests.get(NEWS_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        print(f"{LF_PRINT_PREFIX} NewsAPI для '{person_name}' ответил статусом: {response.status_code}")
        response.raise_for_status()
        data = response.json()
        articles_data = data.get('articles', [])
        # Обновляем лог
        print(f"{LF_PRINT_PREFIX} Получено {len(articles_data)} статей для '{person_name}' из NewsAPI (запрошено {actual_page_size} за последние 3 дня).")
        if articles_data and len(articles_data) > 0:
            print(f"{LF_PRINT_PREFIX} Пример первого заголовка для '{person_name}': {articles_data[0].get('title')}")
            # Для отладки можно посмотреть дату публикации первой статьи:
            # print(f"{LF_PRINT_PREFIX} Дата публикации первой статьи: {articles_data[0].get('publishedAt')}")
        filtered_articles = [{"title": art["title"], "description": art["description"]} for art in articles_data if art.get("title") and art.get("description")]
        print(f"{LF_PRINT_PREFIX} Отфильтровано {len(filtered_articles)} статей (с title и description) для '{person_name}'.")
        return filtered_articles
    except requests.exceptions.Timeout:
        print(f"{LF_PRINT_PREFIX} ОШИБКА: Таймаут ({REQUEST_TIMEOUT_SECONDS}s) при запросе к NewsAPI для '{person_name}'.")
    except requests.exceptions.HTTPError as http_err:
        print(f"{LF_PRINT_PREFIX} ОШИБКА HTTP {http_err.response.status_code} при запросе к NewsAPI для '{person_name}'. Ответ: {http_err.response.text[:200]}...")
    except requests.exceptions.RequestException as req_err:
        print(f"{LF_PRINT_PREFIX} ОШИБКА сети при запросе к NewsAPI для '{person_name}': {req_err}")
    except Exception as e:
        print(f"{LF_PRINT_PREFIX} Неожиданная ОШИБКА при получении новостей для '{person_name}': {e}")
        traceback.print_exc()
    return []

# НЕ ДОЛЖНО БЫТЬ @app.route ЗДЕСЬ
def _geocode_location(location_name: str):
    if not location_name or location_name == "Unknown":
        return None, None
    headers = {'User-Agent': 'LocationFinderApp/1.0 (Test Project; your_actual_email@example.com)'} # ЗАМЕНИТЕ EMAIL
    params = {'q': location_name, 'format': 'json', 'limit': 1}
    nominatim_url = "https://nominatim.openstreetmap.org/search"
    print(f"{LF_PRINT_PREFIX} Геокодинг для '{location_name}' через Nominatim...")
    try:
        time.sleep(1.1) 
        response = requests.get(nominatim_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and isinstance(data, list) and len(data) > 0:
            place = data[0]
            lat = float(place.get('lat'))
            lon = float(place.get('lon'))
            print(f"{LF_PRINT_PREFIX} Геокодинг успешен для '{location_name}': lat={lat}, lon={lon}")
            return lat, lon
        else:
            print(f"{LF_PRINT_PREFIX} Геокодинг не дал результатов для '{location_name}'. Ответ: {data}")
            return None, None
    except requests.exceptions.Timeout:
        print(f"{LF_PRINT_PREFIX} ОШИБКА: Таймаут при запросе к Nominatim для '{location_name}'.")
    except requests.exceptions.HTTPError as http_err:
        print(f"{LF_PRINT_PREFIX} ОШИБКА HTTP {http_err.response.status_code} при запросе к Nominatim для '{location_name}'. Ответ: {http_err.response.text[:200]}...")
    except (ValueError, TypeError, KeyError) as json_err:
        resp_text = response.text[:200] if 'response' in locals() and hasattr(response, 'text') else 'N/A'
        print(f"{LF_PRINT_PREFIX} ОШИБКА обработки ответа Nominatim для '{location_name}': {json_err}. Ответ: {resp_text}")
    except Exception as e:
        print(f"{LF_PRINT_PREFIX} Неожиданная ОШИБКА при геокодинге '{location_name}': {e}")
        traceback.print_exc()
    return None, None

# НЕ ДОЛЖНО БЫТЬ @app.route ЗДЕСЬ
def _get_gemini_prompt_template(person_name: str):
    return f"""Analyze the following news summaries about {person_name}.
Your primary goal is to identify a specific geographic location (city and country) where {person_name} has demonstrably been physically present or performed a significant action very recently (e.g., within the last 1-2 days).
Consider actions like:
- Explicitly stated arrivals, visits, or current presence.
- Making official statements or appearances from a specified location.
- Reports of them being seen or engaging in activities at a particular place.
De-prioritize or ignore:
- Planned future visits, upcoming meetings, or speculative travel.
- General discussions about locations without confirmed recent presence.
- Locations mentioned only in the context of other people interacting with {person_name} unless {person_name}'s presence there is also confirmed.
- Vague locations like "on a plane" unless a specific destination of arrival is mentioned in conjunction.
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
        print(f"{LF_PRINT_PREFIX} Модель Gemini не была инициализирована перед вызовом _get_location_from_gemini для '{person_name}'. Попытка инициализации...")
        if not _initialize_gemini():
             print(f"{LF_PRINT_PREFIX} ОШИБКА: Не удалось инициализировать модель Gemini для '{person_name}'.")
             return {"location_name": "Error", "lat": None, "lon": None, "error": "Gemini model not available (initialization failed)"}
    
    model_to_use = _gemini_model_instance
    prompt_template = _get_gemini_prompt_template(person_name)
    full_prompt = prompt_template.format(news_summaries_text=news_summaries_text)
    
    print(f"{LF_PRINT_PREFIX} Подготовлен промпт для Gemini для '{person_name}'. Длина промпта: {len(full_prompt)} символов.")

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
                
                location_name_for_geocoding = "Unknown"
                geocoding_error_message = None

                if not location_text_raw:
                    print(f"{LF_PRINT_PREFIX} Gemini вернул пустой ответ для '{person_name}'. Считаем 'Unknown'.")
                elif location_text_raw == "Unknown":
                    print(f"{LF_PRINT_PREFIX} Gemini определил локацию как 'Unknown' для '{person_name}'.")
                else:
                    parts = location_text_raw.split(',')
                    if len(parts) >= 2:
                        country = parts[0].strip()
                        city = ",".join(parts[1:]).strip() 
                        if country and city and len(location_text_raw) < 150:
                            location_name_for_geocoding = f"{country}, {city}"
                            print(f"{LF_PRINT_PREFIX} Gemini результат для геокодинга '{person_name}': {location_name_for_geocoding}")
                        else:
                            print(f"{LF_PRINT_PREFIX} Gemini вернул что-то похожее на локацию, но части пусты или слишком длинно для '{person_name}': '{location_text_raw}'. Считаем 'Unknown'.")
                            geocoding_error_message = f"Gemini format error: {location_text_raw}"
                            location_name_for_geocoding = "Unknown"
                    else:
                         print(f"{LF_PRINT_PREFIX} Gemini вернул НЕОЖИДАННЫЙ ФОРМАТ для '{person_name}': '{location_text_raw}'. Считаем 'Unknown'.")
                         geocoding_error_message = f"Gemini unexpected format: {location_text_raw}"
                         location_name_for_geocoding = "Unknown"
                
                lat, lon = None, None
                final_location_name_to_return = location_name_for_geocoding if location_name_for_geocoding != "Unknown" else location_text_raw

                if location_name_for_geocoding != "Unknown":
                    lat, lon = _geocode_location(location_name_for_geocoding)
                    if lat is None or lon is None:
                        geocoding_error_message = (geocoding_error_message + "; " if geocoding_error_message else "") + f"Geocoding failed for '{location_name_for_geocoding}'"
                elif geocoding_error_message is None and location_text_raw == "Unknown": 
                    geocoding_error_message = "Location is Unknown (from Gemini)"
                
                return {
                    "location_name": final_location_name_to_return,
                    "lat": lat,
                    "lon": lon,
                    "error": geocoding_error_message
                }

            elif response.prompt_feedback and response.prompt_feedback.block_reason:
                block_reason_val = response.prompt_feedback.block_reason
                block_message = response.prompt_feedback.block_reason_message if hasattr(response.prompt_feedback, 'block_reason_message') else "No message"
                print(f"{LF_PRINT_PREFIX} Запрос к Gemini для '{person_name}' ЗАБЛОКИРОВАН: {block_reason_val} ({block_message})")
                return {"location_name": "Blocked by Gemini", "lat": None, "lon": None, "error": f"Gemini request blocked ({block_reason_val})"}
            else:
                candidates_info = "N/A"
                if hasattr(response, 'candidates') and response.candidates:
                    try:
                        candidate_details = response.candidates[0]
                        candidates_info = f"Finish Reason: {candidate_details.finish_reason.name if candidate_details.finish_reason else 'N/A'}. Safety Ratings: {[(sr.category.name, sr.probability.name) for sr in candidate_details.safety_ratings] if candidate_details.safety_ratings else 'N/A'}"
                    except Exception:
                        candidates_info = str(response.candidates[0])
                print(f"{LF_PRINT_PREFIX} НЕОЖИДАННЫЙ ОТВЕТ (нет parts, не заблокирован) от Gemini для '{person_name}'. Кандидаты: {candidates_info}")
                return {"location_name": "Error", "lat": None, "lon": None, "error": "Unexpected Gemini response structure"}

        except genai.types.generation_types.BlockedPromptException as bpe:
            print(f"{LF_PRINT_PREFIX} КРИТИКА: Промпт для '{person_name}' был заблокирован Gemini (BlockedPromptException): {bpe}")
            return {"location_name": "Error", "lat": None, "lon": None, "error": "Gemini prompt blocked"}
        except Exception as e:
            print(f"{LF_PRINT_PREFIX} ОШИБКА при запросе к Gemini для '{person_name}' (попытка {attempt + 1}): {e}")
            traceback.print_exc()
            if attempt == max_retries - 1:
                return {"location_name": "Error", "lat": None, "lon": None, "error": f"Gemini API failure after {max_retries} retries"}
            print(f"{LF_PRINT_PREFIX} Пауза перед повторной попыткой для '{person_name}'...")
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
            print(f"{LF_PRINT_PREFIX} ОШИБКА: Не удалось инициализировать Gemini перед циклом. Прерывание.")
            for name in person_names:
                results[name] = {"location_name": "Error", "lat": None, "lon": None, "error": "Gemini initialization failed"}
            return results

    for person_name_original in person_names: 
        current_person_result = {"location_name": "Processing...", "lat": None, "lon": None, "error": None}
        person_name = person_name_original 

        if not isinstance(person_name_original, str) or not person_name_original.strip():
            error_key = person_name_original if isinstance(person_name_original, str) else f"invalid_entry_{type(person_name_original).__name__}"
            print(f"{LF_PRINT_PREFIX} Невалидное имя персоны: '{person_name_original}'. Пропуск.")
            current_person_result = {"location_name": "Invalid Name", "lat": None, "lon": None, "error": "Invalid person name provided"}
            results[error_key] = current_person_result
            continue
        
        person_name = person_name_original.strip()
        print(f"\n{LF_PRINT_PREFIX} Обработка персоны: '{person_name}'")

        if test_mode:
            print(f"{LF_PRINT_PREFIX} Тестовый режим для '{person_name}'. Используем заглушку 'Turkey, Istanbul'.")
            test_lat, test_lon = 41.0082, 28.9784 
            results[person_name] = {
                "location_name": "Turkey, Istanbul (Test)",
                "lat": test_lat,
                "lon": test_lon,
                "error": None
            }
            time.sleep(0.1) 
            continue 

        articles = _fetch_news(person_name, num_articles=100)
        
        if not articles:
            print(f"{LF_PRINT_PREFIX} Новости для '{person_name}' не найдены или произошла ошибка получения.")
            current_person_result["location_name"] = "Error"
            current_person_result["error"] = "Could not fetch news"
            results[person_name] = current_person_result
            continue

        news_summaries = []
        for i, article in enumerate(articles):
            title = article.get("title", "").strip()
            description = article.get("description", "").strip()
            if title and description:
                 news_summaries.append(f"Article {i+1}:\nTitle: {title}\nDescription: {description}\n---")
            elif title:
                 news_summaries.append(f"Article {i+1}:\nTitle: {title}\n(No description provided)\n---")
        
        if not news_summaries:
            print(f"{LF_PRINT_PREFIX} Нет подходящих новостных саммари для '{person_name}' после фильтрации.")
            current_person_result["location_name"] = "Error"
            current_person_result["error"] = "No suitable news summaries found"
            results[person_name] = current_person_result
            continue
            
        news_summaries_text = "\n\n".join(news_summaries)
        
        summary_preview_len = 1000 
        text_preview = news_summaries_text[:summary_preview_len]
        remaining_chars = len(news_summaries_text) - summary_preview_len if len(news_summaries_text) > summary_preview_len else 0
        
        print(f"\n{LF_PRINT_PREFIX} ---- ТЕКСТ ДЛЯ GEMINI ({person_name}) (из {len(news_summaries)} статей, превью) ----\n{text_preview}...\n(Далее еще {remaining_chars} симв.)\n---- КОНЕЦ ТЕКСТА ДЛЯ GEMINI ({person_name}) ----\n")
        print(f"{LF_PRINT_PREFIX} Сформировано саммари ({len(news_summaries)} статей) для '{person_name}'. Длина текста: {len(news_summaries_text)}.")
        
        MAX_CHARS_FOR_GEMINI = 750000 
        if len(news_summaries_text) > MAX_CHARS_FOR_GEMINI:
            print(f"{LF_PRINT_PREFIX} ВНИМАНИЕ: Текст для Gemini для '{person_name}' слишком длинный ({len(news_summaries_text)}). Обрезаем до {MAX_CHARS_FOR_GEMINI}.")
            news_summaries_text = news_summaries_text[:MAX_CHARS_FOR_GEMINI] 
            news_summaries_text += "\n\n... (text automatically truncated due to length limit)"

        location_data = _get_location_from_gemini(person_name, news_summaries_text)
        
        print(f"{LF_PRINT_PREFIX} Результат (с геоданными) для '{person_name}': {location_data}")
        results[person_name] = location_data
        
        if len(person_names) > 1 and person_name != person_names[-1]:
            print(f"{LF_PRINT_PREFIX} Пауза 0.5 сек перед следующей персоной...")
            time.sleep(0.5) 
    
    print(f"{LF_PRINT_PREFIX} Завершение find_persons_locations. Результаты: {json.dumps(results, ensure_ascii=False, indent=2)}")
    return results

if __name__ == '__main__':
    print(f"{LF_PRINT_PREFIX} --- Тестирование location_finder.py (локальный запуск) ---")
    
    print(f"{LF_PRINT_PREFIX} --- Тест в РЕАЛЬНОМ режиме ---")
    real_test_persons = ["Trump"] 
    real_locations_data = find_persons_locations(real_test_persons, test_mode=False)
    print(f"{LF_PRINT_PREFIX}\n--- Результаты РЕАЛЬНОГО поиска локаций (локальный тест) ---")
    for person, data in real_locations_data.items():
        print(f"{LF_PRINT_PREFIX} {person}: Name='{data.get('location_name')}', Lat={data.get('lat')}, Lon={data.get('lon')}, Error='{data.get('error')}'")

    print(f"\n{LF_PRINT_PREFIX} --- Тест в ТЕСТОВОМ режиме (заглушка Стамбул) ---")
    stub_test_persons = ["Person1", "Person2"]
    stub_locations_data = find_persons_locations(stub_test_persons, test_mode=True)
    print(f"{LF_PRINT_PREFIX}\n--- Результаты ТЕСТОВОГО поиска локаций (заглушка) ---")
    for person, data in stub_locations_data.items():
        print(f"{LF_PRINT_PREFIX} {person}: Name='{data.get('location_name')}', Lat={data.get('lat')}, Lon={data.get('lon')}, Error='{data.get('error')}'")

    print(f"\n{LF_PRINT_PREFIX}--- Прямой тест Геокодинга ---")
    lat_paris, lon_paris = _geocode_location("France, Paris")
    print(f"{LF_PRINT_PREFIX} Геокодинг для 'France, Paris': Lat={lat_paris}, Lon={lon_paris}")
# --- END OF FILE location_finder.py ---