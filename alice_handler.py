# --- START OF FILE alice_handler.py ---

import requests
import time
import threading
import os
import re
import traceback
import json

# Импорты Gemini API и нужных частей
import google.generativeai as genai

# Импортируем функцию перевода и константу из нашего основного модуля
try:
    from translation_module import translate_text, CONTEXT_LIMIT_ERROR, configure_api
except ImportError:
    print("ALICE_HANDLER_ERROR: Не удалось импортировать translation_module.")
    def translate_text(text, *args, **kwargs): print("WARN: translate_text is a stub!"); return None
    CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR_STUB"
    def configure_api(): print("WARN: configure_api is a stub!"); pass

# --- Конфигурация ---
NEWS_CACHE_TTL_SECONDS = 600 # 10 минут

# --- Кеш для Новостей (/alice) ---
translated_news_cache = {"translated_titles": [], "last_updated": 0}
news_cache_lock = threading.Lock()

# --- Глобальные переменные для Умной Алисы (/alice/smart) ---
pending_gemini_results = {}
results_lock = threading.Lock()
background_executor = None

def initialize_alice_handler(executor_instance):
    global background_executor
    if executor_instance: background_executor = executor_instance; print("[Alice Handler] Инициализирован с executor.")
    else: print("[Alice Handler ERROR] Executor instance is None!")

# --- Вспомогательная функция для получения новостей BBC ---
def _get_bbc_news_from_api():
    api_key = '2126e6e18adb478fb9ade262cb1102af';
    url = 'https://newsapi.org/v2/top-headlines?sources=bbc-news';
    headers = {'x-api-key': api_key};
    titles = []
    try:
        print("[Alice Handler/News API] Запрос к NewsAPI..."); response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
        titles = [a["title"] for a in response.json().get("articles", []) if a.get("title")]
        print(f"[Alice Handler/News API] Получено {len(titles)} заголовков.")
    except requests.exceptions.Timeout: print("[Alice Handler/News API ERROR] Таймаут.")
    except requests.exceptions.RequestException as e: print(f"[Alice Handler/News API ERROR] Ошибка сети/API: {e}")
    except Exception as e: print(f"[Alice Handler/News API ERROR] Неожиданная ошибка: {e}")
    return titles

# --- Функция для ОБНОВЛЕНИЯ кеша новостей (вызывается лениво из /alice) ---
def update_translated_news_cache(model_for_news: str = "free"):
    global translated_news_cache, news_cache_lock; print(f"[Alice Handler/News Update] Запуск обновления новостей с моделью '{model_for_news}'..."); english_titles = _get_bbc_news_from_api()
    if not english_titles: print("[Alice Handler/News Update] Не удалось получить заголовки."); return False
    new_translated_titles = []; target_language = "russian"; alice_prompt_ext = "Do not use footnotes..."
    try:
        if translate_text is None: raise ImportError("translate_text missing")
        print(f"[Alice Handler/News Update] Переводим до {min(len(english_titles), 7)} заголовков...")
        titles_processed = 0
        for i, title in enumerate(english_titles[:7]):
            translated_title_raw = translate_text(title, target_language, model_for_news, prompt_ext=alice_prompt_ext); titles_processed += 1
            if translated_title_raw and translated_title_raw != CONTEXT_LIMIT_ERROR:
                cleaned_title = re.sub(r'\[\d+\]|¹|²|³|⁴|⁵|⁶|⁷|⁸|⁹', '', translated_title_raw); cleaned_title = re.sub(r'\n+---\n.*', '', cleaned_title, flags=re.DOTALL).strip()
                if cleaned_title: new_translated_titles.append(cleaned_title)
    except Exception as e_translate: print(f"[Alice Handler/News Update ERROR] Ошибка перевода: {e_translate}"); traceback.print_exc(); return False
    if new_translated_titles:
        print(f"[Alice Handler/News Update] Переведено {len(new_translated_titles)}/{titles_processed}. Обновляем кеш...");
        with news_cache_lock: translated_news_cache["translated_titles"] = new_translated_titles; translated_news_cache["last_updated"] = time.time()
        print("[Alice Handler/News Update] Кеш новостей обновлен."); return True
    else: print("[Alice Handler/News Update] Не удалось перевести заголовки."); return False

# --- Фоновая функция для /alice/smart ---
def run_gemini_query_background(session_id, user_query):
    global pending_gemini_results, results_lock
    log_prefix = f"[Alice Handler/BG Gemini {session_id}]"; print(f"{log_prefix} Запуск: '{user_query[:50]}...'")
    result_payload = {"status": "error", "error": "BG error"}; start_time = time.time()
    try:
        model_name = "free"; print(f"{log_prefix} Иниц. {model_name}...")
        try: model = genai.GenerativeModel(model_name); print(f"{log_prefix} Модель готова.")
        except Exception as model_e: print(f"{log_prefix} ОШИБКА иниц. модели: {model_e}"); raise model_e
        prompt = f"""Ты — ассистент Алиса. Ответь кратко. Макс 950 символов. Без Markdown.

Вопрос: {user_query}

Ответ:"""
        print(f"{log_prefix} Запрос к Gemini..."); response = model.generate_content(prompt); response_time = time.time(); print(f"{log_prefix} Ответ получен за {response_time - start_time:.2f} сек.")
        gemini_text = None
        try: gemini_text = response.text
        except Exception as e_resp: print(f"{log_prefix} Ошибка ответа: {e_resp}"); block_reason = getattr(response.prompt_feedback, 'block_reason', None) if hasattr(response, 'prompt_feedback') else f"RespError: {e_resp}"; result_payload = {"status": "error", "error": f"Ответ некорректен ({block_reason})."}
        if gemini_text:
            print(f"{log_prefix} Текст len: {len(gemini_text)}. Обрезка..."); MAX_LEN = 1000
            if len(gemini_text) > MAX_LEN:
                last_space = gemini_text.rfind(' ', 0, MAX_LEN);
                # --- ИСПРАВЛЕНИЕ: разделяем присваивания ---
                if last_space != -1:
                    final_text = gemini_text[:last_space].rstrip('.,!?;: ') + "..."
                else:
                    final_text = gemini_text[:MAX_LEN] + "..."
                print(f"...обрезан до {len(final_text)}")
            else: final_text = gemini_text.strip()
            result_payload = {"status": "done", "result": final_text}
        elif result_payload.get('status') != "error": result_payload = {"status": "error", "error": "Пустой ответ."}
    except Exception as e: print(f"{log_prefix} КРИТ. ОШИБКА BG: {e}"); traceback.print_exc(); result_payload = {"status": "error", "error": f"Внутр. ошибка ({type(e).__name__})."}
    finally:
        print(f"{log_prefix} Finally. Сохранение результата...")
        with results_lock:
            pending_gemini_results[session_id] = result_payload
        saved_status = result_payload.get('status', 'unknown')
        # --- ИСПРАВЛЕНИЕ: разделяем print ---
        print(f"{log_prefix} Результат сохранен, статус: {saved_status}.")
        print(f"{log_prefix} Задача завершена за {time.time() - start_time:.2f} сек.")

# --- ИЗМЕНЕНИЕ: Обработчик /alice/smart с КОРОТКИМ ОЖИДАНИЕМ ---
def handle_smart_alice_request(request_data):
    global pending_gemini_results, results_lock, background_executor
    log_prefix = "[Alice Handler/Smart Poll]"; session_id = "unknown_session" # Обновил префикс
    print(f"{log_prefix} Обработка входящего запроса...")
    if not request_data: print(f"{log_prefix} ОШИБКА: request_data пустой!"); return {"response": {"text": "Ошибка: Пустой запрос.", "end_session": True}, "version": "1.0"}
    # print(f"{log_prefix} Полный request_data: {json.dumps(request_data, indent=2, ensure_ascii=False)}")
    session_data = request_data.get('session', {});
    if isinstance(session_data, dict): session_id = session_data.get('session_id', session_id)
    log_prefix += f" {session_id}"
    state_data = request_data.get('state', {}); session_state = {}
    if isinstance(state_data, dict): session_state = state_data.get('session', {});
    if not isinstance(session_state, dict): session_state = {}
    print(f"{log_prefix} Прочитанный session_state: {session_state}")
    current_status = session_state.get('status')
    print(f"{log_prefix} Текущий статус из session_state: {current_status}")

    response_payload = {}; final_text = "Ошибка."; end_session = True; session_state_update = {}
    quick_poll_timeout = 3.9 # Макс. время ожидания в секундах
    poll_interval = 0.1     # Интервал проверки результата в секундах

    if current_status == "waiting_gemini":
        # --- Логика обработки ВТОРОГО запроса (остается без изменений) ---
        print(f"{log_prefix} Статус 'waiting_gemini'. Проверяем результат...");
        with results_lock: pending_result = pending_gemini_results.get(session_id)
        print(f"{log_prefix} Результат из pending: {pending_result}")
        if pending_result:
            result_status = pending_result.get("status")
            if result_status == "done":
                print(f"{log_prefix} Результат ГОТОВ."); final_text = pending_result.get("result", "Ошибка извлечения."); end_session = True
                with results_lock: pending_gemini_results.pop(session_id, None); print(f"{log_prefix} Запись удалена.")
            elif result_status == "error":
                print(f"{log_prefix} Результат: ОШИБКА."); error_msg = pending_result.get("error", "Неизвестно."); final_text = f"Извините, ошибка: {error_msg}"; end_session = True
                with results_lock: pending_gemini_results.pop(session_id, None); print(f"{log_prefix} Запись удалена.")
            else: # Pending
                print(f"{log_prefix} Результат '{result_status or 'pending'}'. Отвечаем 'еще думаю'."); final_text = "Я все еще думаю... Пожалуйста, подождите еще немного..."; end_session = False; session_state_update = {"status": "waiting_gemini"}
        else: print(f"{log_prefix} Результат НЕ НАЙДЕН."); final_text = "Ой, повторите вопрос?"; end_session = True
        # --- Конец логики второго запроса ---

    else: # ПЕРВЫЙ ЗАПРОС - запускаем задачу и активно ждем немного
        print(f"{log_prefix} Новый запрос (статус: {current_status})."); request_field = request_data.get('request', {}); user_input = ""
        if isinstance(request_field, dict): user_input = request_field.get('command', request_field.get('original_utterance', '')).strip()
        print(f"{log_prefix} Ввод: '{user_input}'")

        # --- ПРОВЕРКА НА ПОМОЩЬ ---
        help_triggers = ["помощь", "что ты умеешь", "справка", "хелп", "help"]
        if user_input.lower() in help_triggers:
            print(f"{log_prefix} Запрос помощи.")
            help_text = "Я могу ответить на любой вопрос кратко и изобретательно, не хуже настоящего Знатока. Просто задайте мне вопрос."
            return {
                "response": {"text": help_text, "tts": help_text, "end_session": False}, # Не завершаем, чтобы пользователь мог спросить
                "version": "1.0"
            }
        # --- КОНЕЦ ПРОВЕРКИ НА ПОМОЩЬ ---

        if not user_input: print(f"{log_prefix} Пустой ввод."); final_text = "Слушаю."; end_session = False
        elif background_executor is None: print(f"{log_prefix} ОШИБКА: Executor не готов!"); final_text = "Обработчик не готов."; end_session = True
        else:
            print(f"{log_prefix} Запуск BG задачи и активное ожидание до {quick_poll_timeout} сек...")
            with results_lock: pending_gemini_results[session_id] = {"status": "pending"} # Ставим pending
            try:
                 future = background_executor.submit(run_gemini_query_background, session_id, user_input)
                 print(f"{log_prefix} Задача отправлена. Начинаем опрос результата...")

                 start_poll_time = time.time()
                 got_result = False
                 while time.time() - start_poll_time < quick_poll_timeout:
                     with results_lock: pending_result = pending_gemini_results.get(session_id)
                     # print(f"{log_prefix} Poll check: {pending_result}") # DEBUG - можно раскомментировать

                     if pending_result and pending_result.get("status") != "pending":
                         # Результат готов (done или error)!
                         print(f"{log_prefix} Результат получен во время опроса! Статус: {pending_result.get('status')}")
                         result_status = pending_result.get("status")
                         if result_status == "done":
                             final_text = pending_result.get("result", "Ошибка извлечения.")
                             end_session = True
                         elif result_status == "error":
                             error_msg = pending_result.get("error", "Неизвестно.")
                             final_text = f"Извините, ошибка: {error_msg}"
                             end_session = True
                         # Удаляем результат из словаря
                         with results_lock: pending_gemini_results.pop(session_id, None); print(f"{log_prefix} Запись удалена из pending.")
                         got_result = True
                         break # Выходим из цикла while
                     # Если статус pending или результата еще нет, ждем
                     time.sleep(poll_interval)

                 # Если вышли из цикла и результат НЕ был получен
                 if not got_result:
                     print(f"{log_prefix} Результат НЕ получен за {quick_poll_timeout} сек. Отвечаем 'сейчас подумаю'.")
                     final_text = "Хороший вопрос! Дайте мне пару секунд, чтобы подумать..."
                     end_session = False
                     session_state_update = {"status": "waiting_gemini"} # Устанавливаем статус ожидания

            except Exception as e: # Ошибка при submit или в логике ожидания/опроса
                 print(f"{log_prefix} ОШИБКА во время запуска/опроса: {e}"); traceback.print_exc()
                 with results_lock: pending_gemini_results.pop(session_id, None) # Убираем pending
                 final_text = "Ошибка запуска обработчика."; end_session = True

    # Формируем финальный payload
    response_payload = {"response": {"text": final_text, "tts": final_text, "end_session": end_session}, "version": "1.0"}
    if session_state_update: response_payload["session_state"] = session_state_update
    print(f"{log_prefix} Отправка ответа (end_session={end_session}).")
    return response_payload


# --- Обработчик для новостей (/alice) ---
def handle_alice_request(request_data):
    """ Обрабатывает запрос новостей, читая из кеша или обновляя его при необходимости. """
    global translated_news_cache, news_cache_lock; print("[Alice Handler/News] Обработка запроса..."); current_time = time.time(); needs_update = False; cached_titles = []; cache_time = 0; ttl = NEWS_CACHE_TTL_SECONDS
    # --- ИСПРАВЛЕНИЕ: Проверка кеша и копирование ПОД локом ---
    with news_cache_lock:
        cache_age = current_time - translated_news_cache["last_updated"]
        is_cache_valid = translated_news_cache["translated_titles"] and cache_age < ttl
        if is_cache_valid:
            print(f"[Alice Handler/News] Используем кеш (возраст: {int(cache_age)} сек).")
            cached_titles = list(translated_news_cache["translated_titles"])
            needs_update = False
        else:
            print("[Alice Handler/News] Кеш пуст или устарел. Требуется обновление.")
            needs_update = True
            # Оставляем cached_titles пустым
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    if needs_update:
        print("[Alice Handler/News] Запуск синхронного обновления..."); update_successful = update_translated_news_cache();
        if update_successful:
             with news_cache_lock: cached_titles = list(translated_news_cache["translated_titles"]); print(f"[Alice Handler/News] Кеш прочитан после обновления...")
        else:
             print("[Alice Handler/News] Обновление не удалось.")
             # Пытаемся вернуть старые данные, если обновление не удалось
             with news_cache_lock:
                 if translated_news_cache["translated_titles"]: cached_titles = list(translated_news_cache["translated_titles"]); print("[Alice Handler/News] Возвращаем старые...")

    # Формируем ответ
    response_text = ""; MAX_RESPONSE_LENGTH = 1000; headlines_added = 0
    if cached_titles:
        intro_phrase = "Вот заголовки с БиБиСи Ньюс:\n"; current_text = intro_phrase
        # --- ИСПРАВЛЕНИЕ: разделяем действия ---
        for i, title in enumerate(cached_titles):
            next_headline_part = f"{i+1}. {title}\n"
            if len(current_text) + len(next_headline_part) <= MAX_RESPONSE_LENGTH:
                current_text += next_headline_part
                headlines_added += 1
            else:
                break
        if headlines_added > 0:
            response_text = current_text.strip()
            print(f"[Alice Handler/News] Ответ с {headlines_added} заголовками.")
        else:
             response_text = "Извините, новости не помещаются в ответ."
             print("[Alice Handler/News] Ошибка: новости не влезли в лимит.")
    else: print("[Alice Handler/News] Нет данных."); response_text = "Извините, не удалось получить новости."
    if not response_text: response_text = "Извините, ошибка."

    return {"response": {"text": response_text, "tts": response_text, "end_session": True}, "version": "1.0"}

# --- END OF FILE alice_handler.py ---