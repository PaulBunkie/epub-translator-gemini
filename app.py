# --- START OF FILE app.py ---

from dotenv import load_dotenv
load_dotenv()  # Загружаем переменные окружения из .env файла

import os
import uuid
import json
import io
import time
import traceback # Для вывода ошибок
import atexit
import threading
import html

# Flask и связанные утилиты
from flask import (
    Flask, request, render_template, redirect, url_for,
    jsonify, send_from_directory, Response, session, g, send_file, make_response
)
from werkzeug.utils import secure_filename

# Асинхронность
from concurrent.futures import ThreadPoolExecutor

# --- ДОБАВЛЯЕМ импорт APScheduler ---
from apscheduler.schedulers.background import BackgroundScheduler

# Наши модули
from epub_creator import create_translated_epub
from db_manager import (
    init_db, get_all_books, get_book, create_book, update_book_status,
    update_book_prompt_ext, delete_book, create_section, get_sections_for_book,
    update_section_status, reset_stuck_processing_sections, get_section_count_for_book
)
from translation_module import (
    configure_api, translate_text, CONTEXT_LIMIT_ERROR, EMPTY_RESPONSE_ERROR, get_models_list, load_models_on_startup
)
from epub_parser import (
    get_epub_structure, extract_section_text, get_epub_toc
)
from cache_manager import (
    get_translation_from_cache, save_translation_to_cache, save_translated_chapter,
    delete_section_cache, delete_book_cache, _get_epub_id
)
import alice_handler
import location_finder
import workflow_db_manager
import epub_parser
import workflow_processor
import workflow_cache_manager

# --- Настройки ---
UPLOAD_FOLDER = 'uploads'
CACHE_DIR = ".epub_cache"
FULL_TRANSLATION_DIR = ".translated"
ALLOWED_EXTENSIONS = {'epub'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.urandom(24) # Для сессий и flash-сообщений

# --- Инициализируем БД при старте приложения ---
with app.app_context():
     init_db()

# --- Создаем необходимые директории ---
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(FULL_TRANSLATION_DIR, exist_ok=True)

# --- Настраиваем API перевода ---
try:
    configure_api()
except ValueError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА НАСТРОЙКИ API: {e}. Перевод не будет работать.")

# --- Управление фоновыми задачами ---
executor = ThreadPoolExecutor(max_workers=int(os.getenv("MAX_TRANSLATION_WORKERS", 3)))
active_tasks = {} # Хранилище статусов активных задач {task_id: {"status": ..., "book_id": ..., "section_id": ...}}

# --- ИЗМЕНЕНИЕ: Передаем executor в alice_handler ---
alice_handler.initialize_alice_handler(executor)
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# --- ИЗМЕНЕНИЕ: Настройка и запуск APScheduler ---
scheduler = BackgroundScheduler(daemon=True)
# Добавляем задачу обновления кеша новостей, выполняться каждый час
scheduler.add_job(
    alice_handler.update_translated_news_cache,
    'interval',
    hours=1,
    id='bbc_news_updater_job', # Даем ID для управления
    replace_existing=True     # Заменять задачу, если она уже есть с таким ID
)
# --- ИЗМЕНЕНИЕ: НЕ ЗАПУСКАЕМ задачу немедленно при старте ---
# Убираем блок с initial_update_thread.start()

# --- НОВОЕ ЗАДАНИЕ для обновления локаций персон ---
# Убедимся, что location_finder импортирован
if hasattr(location_finder, 'update_locations_for_predefined_persons'):
    scheduler.add_job(
        location_finder.update_locations_for_predefined_persons,
        trigger='interval', # Тип триггера - интервал
        hours=1,            # Выполнять каждый час
        id='person_locations_updater_job', # Уникальный ID задания
        replace_existing=True, # Заменять существующее задание с таким ID
        misfire_grace_time=600 # Секунд, на которые может опоздать выполнение (10 минут)
    )
    print("[Scheduler] Задание 'person_locations_updater_job' добавлено (обновление локаций персон каждый час).")
else:
    print("[Scheduler] ОШИБКА: Функция 'update_locations_for_predefined_persons' не найдена в location_finder. Задание не добавлено.")
# --- КОНЕЦ НОВОГО ЗАДАНИЯ ---

try:
    scheduler.start()
    print("Планировщик APScheduler запущен (задача запустится через час или по расписанию).")
except Exception as e:
     print(f"ОШИБКА запуска APScheduler: {e}")
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# Регистрируем функцию для остановки планировщика при выходе
atexit.register(lambda: scheduler.shutdown())
print("Зарегистрирована остановка планировщика при выходе.")
# --- КОНЕЦ ИЗМЕНЕНИЯ APScheduler ---

# --- Вспомогательные функции ---
def allowed_file(filename):
    """Проверяет, имеет ли файл разрешенное расширение."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def update_overall_book_status(book_id):
    """
    Пересчитывает и обновляет общий статус книги в БД на основе статусов
    секций, перечисленных в оглавлении (TOC).
    """
    book_data = get_book(book_id)
    if book_data is None: return False
    all_sections_dict = book_data.get('sections', {})
    needed_section_ids = set(item.get('id') for item in book_data.get('toc', []) if item.get('id'))

    if not needed_section_ids:
        current_status = book_data.get('status')
        new_status = "error_no_toc_sections" if all_sections_dict else "error_no_sections"
        if current_status != new_status: update_book_status(book_id, new_status)
        return True

    translated_count = 0; error_count = 0; processing_count = 0
    total_needed = len(needed_section_ids)
    for section_id in needed_section_ids:
         section_data = all_sections_dict.get(section_id)
         if section_data:
              status = section_data['status']
              if status in ["translated", "completed_empty", "cached", "summarized", "analyzed"]:
                   translated_count += 1
              elif status == "processing": processing_count += 1
              elif status.startswith("error_"): error_count +=1

    overall_status = "idle"
    if processing_count > 0: overall_status = "processing"
    elif (translated_count + error_count) == total_needed and processing_count == 0:
         overall_status = "completed" if error_count == 0 else "completed_with_errors"

    if book_data.get('status') != overall_status:
        if update_book_status(book_id, overall_status): print(f"Общий статус книги '{book_id}' -> '{overall_status}'.")
        else: print(f"ОШИБКА обновления статуса книги '{book_id}'!"); return False
    return True

# --- Фоновая задача ---
def run_single_section_translation(task_id, epub_filepath, book_id, section_id, target_language, model_name, prompt_ext, operation_type: str = 'translate'):
    """ Выполняется в отдельном потоке для перевода одной секции. """
    print(f"Фоновая задача {task_id}: Старт перевода {section_id} ({book_id}) моделью '{model_name}' на '{target_language}'. Операция: '{operation_type}'.")
    print(f"  [BG Task] Используется prompt_ext длиной: {len(prompt_ext) if prompt_ext else 0}")
    current_status = "error_unknown"; error_message = None
    try:
        if task_id in active_tasks: active_tasks[task_id]["status"] = "extracting"
        original_text = extract_section_text(epub_filepath, section_id)
        if not original_text or not original_text.strip():
            print(f"Фоновая задача {task_id}: Текст пуст для {section_id}.")
            current_status = "completed_empty"
            save_translation_to_cache(epub_filepath, section_id, target_language, "")
            # Важно: сохранить статус completed_empty в БД сразу же
            update_section_status(book_id, section_id, current_status, model_name=None, target_language=target_language, error_message=None, operation_type=operation_type)
        else:
            if task_id in active_tasks: active_tasks[task_id]["status"] = "translating"
            api_result = translate_text(original_text, target_language, model_name, prompt_ext=prompt_ext, operation_type=operation_type)

            # --- ДОБАВЛЕНА ЛОГИКА ОБРАБОТКИ EMPTY_RESPONSE_ERROR ---
            if api_result == EMPTY_RESPONSE_ERROR:
                current_status = "error_empty_response_retries"
                error_message = "Модель вернула пустой результат после всех попыток."
                print(f"Фоновая задача {task_id}: {error_message} для {section_id}.")
            # --- КОНЕЦ ДОБАВЛЕННОЙ ЛОГИКИ ---
            elif api_result == CONTEXT_LIMIT_ERROR:
                current_status = "error_context_limit"
                error_message = "Текст раздела слишком велик."
                print(f"Фоновая задача {task_id}: {error_message} для {section_id}.")
            elif api_result is not None:
                 if task_id in active_tasks: active_tasks[task_id]["status"] = "caching"
                 if save_translation_to_cache(epub_filepath, section_id, target_language, api_result): current_status = "translated"
                 else: current_status = "error_caching"; error_message = "Не удалось сохранить в кэш."
                 print(f"Фоновая задача {task_id}: Успешно сохранено в кэш для {section_id}.")
            else: # Это случай, когда translate_text вернул None после ошибок API
                current_status = "error_translation"
                error_message = "Ошибка API перевода или фильтр."
                print(f"Фоновая задача {task_id}: {error_message} для {section_id}.")

            update_section_status(book_id, section_id, current_status, model_name, target_language, error_message, operation_type=operation_type)
        update_overall_book_status(book_id)
    except Exception as e:
        print(f"Фоновая задача {task_id}: Необработанная ошибка при обработке секции {section_id}: {e}")
        import traceback
        traceback.print_exc() # Логируем полный трейсбэк
        current_status = "error_unknown"
        error_message = f"Необработанная ошибка: {e}"
        update_section_status(book_id, section_id, current_status, model_name, target_language, error_message, operation_type=operation_type)
        update_overall_book_status(book_id)
    finally:
        if task_id in active_tasks:
             active_tasks[task_id]["status"] = current_status
             if error_message: active_tasks[task_id]["error_message"] = error_message
        print(f"Фоновая задача {task_id} завершена.")
        update_overall_book_status(book_id)


# --- Маршруты Flask ---
@app.route('/', methods=['GET'])
def index():
    """ Отображает главную страницу со списком книг. """
    print("Загрузка главной страницы...")
    default_language = session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'meta-llama/llama-4-scout:free')
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    
    # Получаем список моделей
    available_models = get_models_list()
    if not available_models:
        available_models = [
            {
                'name': 'gemini-1.5-flash',
                'display_name': 'Google Gemini 1.5 Flash',
                'description': 'Default Google Gemini model'
            }
        ]
        print("  WARN: Не удалось получить список моделей от API.")
    
    active_ids = [(info['book_id'], info['section_id']) for info in active_tasks.values() if info.get('status') in ['queued', 'extracting', 'translating', 'caching']]
    reset_stuck_processing_sections(active_processing_sections=active_ids)
    uploaded_books = []
    try:
        db_books = get_all_books()
        for book_data in db_books:
            uploaded_books.append({
                'book_id': book_data['book_id'],
                'display_name': book_data['filename'],
                'status': book_data['status'],
                'total_sections': get_section_count_for_book(book_data['book_id']),
                'target_language': book_data.get('target_language')
            })
        uploaded_books.sort(key=lambda x: x['display_name'].lower())
        print(f"  Найдено книг в БД: {len(uploaded_books)}")
    except Exception as e: print(f"ОШИБКА при получении списка книг: {e}"); traceback.print_exc()

    resp = make_response(render_template('index.html', uploaded_books=uploaded_books, default_language=default_language, selected_model=selected_model, available_models=available_models))
    # --- ИЗМЕНЕНИЕ: Добавляем 'unsafe-inline' в script-src ---
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    resp.headers['Content-Security-Policy'] = csp_policy
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    return resp

@app.route('/delete_book/<book_id>', methods=['POST'])
def delete_book_request(book_id):
    """ Удаляет книгу, ее файл и кэш. """
    print(f"Запрос на удаление книги: {book_id}")
    book_info = get_book(book_id) # Получаем информацию из старой БД

    if book_info is None:
        print(f"  Книга {book_id} не найдена в старой БД.")
        return "Book not found in old database.", 404

    # Удаление из старой БД
    if delete_book(book_id): # Используем старую функцию удаления
        print(f"  Запись книги '{book_id}' удалена из старой БД.")
    else:
        print(f"  ОШИБКА удаления записи книги '{book_id}' из старой БД!")

    # Удаление файла книги
    filepath = book_info.get("filepath") # Путь к файлу берем из старой БД
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            print(f"  Файл книги {filepath} удален.")
        except OSError as e:
            print(f"  Ошибка удаления файла книги {filepath}: {e}")

    # Удаление кэша старой системы перевода
    delete_book_cache(book_id) # Используем старую функцию удаления кэша
    print(f"  Старый кэш перевода для книги {book_id} удален.")

    # Удаление книги из Workflow (если она там есть)
    # Это отдельная операция, которая не должна блокировать удаление из старой системы
    # TODO: Возможно, добавить здесь асинхронный вызов новой функции удаления из Workflow
    # Пока просто логгируем, что ее нужно удалить и из Workflow вручную или через отдельную кнопку
    print(f"  [INFO] Книга '{book_id}' также может существовать в системе Workflow. Пожалуйста, удалите ее оттуда отдельно, если требуется.")

    # Перенаправляем пользователя на главную страницу старой системы
    # (предполагается, что этот маршрут вызывается со старой главной страницы)
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_file():
    """ Обрабатывает загрузку EPUB, парсит, переводит TOC, сохраняет в БД. """
    if 'epub_file' not in request.files: return "Файл не найден", 400
    file = request.files['epub_file'];
    if file.filename == '': return "Файл не выбран", 400
    if not allowed_file(file.filename): return "Ошибка: Недопустимый тип файла.", 400

    form_language = request.form.get('target_language'); target_language = form_language or session.get('target_language', 'russian'); session['target_language'] = target_language
    original_filename = secure_filename(file.filename)
    temp_dir = app.config['UPLOAD_FOLDER']; temp_filename = f"temp_{uuid.uuid4().hex}.epub"; temp_filepath = os.path.join(temp_dir, temp_filename)
    filepath = None; book_id = None

    try: # Сохранение и переименование
        file.save(temp_filepath); print(f"Файл временно сохранен: {temp_filepath}")
        book_id = _get_epub_id(temp_filepath); print(f"Вычислен Book ID: {book_id}")
        unique_filename = f"{book_id}.epub"; filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        if os.path.exists(filepath):
             print(f"Файл книги {book_id} уже существует: {filepath}"); os.remove(temp_filepath); temp_filepath = None
        else: os.rename(temp_filepath, filepath); print(f"Файл перемещен в: {filepath}"); temp_filepath = None

        if get_book(book_id): return redirect(url_for('view_book', book_id=book_id))

        print(f"Обработка новой книги: {book_id}")
        section_ids, id_to_href_map = get_epub_structure(filepath)
        if section_ids is None: raise ValueError("Не удалось получить структуру EPUB.")
        toc = get_epub_toc(filepath, id_to_href_map) or []

        toc_titles_for_translation = [item['title'] for item in toc if item.get('title')]
        translated_toc_titles = {}
        if toc_titles_for_translation:
             print(f"Перевод {len(toc_titles_for_translation)} заголовков TOC...")
             toc_model = session.get('model_name', 'gemini-1.5-flash')
             titles_text = "\n|||---\n".join(toc_titles_for_translation)
             # Здесь мы не передаем operation_type, потому что это всегда просто перевод названий TOC
             translated_titles_text = translate_text(titles_text, target_language, toc_model, prompt_ext=None)
             if translated_titles_text and translated_titles_text != CONTEXT_LIMIT_ERROR:
                  translated_titles = translated_titles_text.split("\n|||---\n")
                  if len(translated_titles) == len(toc_titles_for_translation):
                       for i, item in enumerate(toc):
                            if item.get('title') and item.get('id'): translated_toc_titles[item['id']] = translated_titles[i].strip() if translated_titles[i] else None
                       print("  Оглавление переведено.")
                  else: print(f"  ОШИБКА: Не совпало количество названий TOC.")
             else: print("  ОШИБКА: Не удалось перевести оглавление.")

        if create_book(book_id, original_filename, filepath, toc, target_language):
             print(f"  Книга '{book_id}' сохранена в БД.")
             sec_created_count = 0
             if section_ids:
                  for section_id in section_ids:
                       selected_operation = session.get('operation_type', 'translate') # Get the current operation from session
                       if section_id and create_section(book_id, section_id, translated_toc_titles.get(section_id)): sec_created_count += 1 # Pass operation_type
                  print(f"  Создано {sec_created_count} записей о секциях.")
             return redirect(url_for('view_book', book_id=book_id))
        else:
             # --- ИСПРАВЛЕНИЕ: Удаляем файл, если не удалось сохранить в БД ---
             print(f"ОШИБКА: Не удалось сохранить книгу '{book_id}' в БД!")
             if filepath and os.path.exists(filepath):
                 try:
                     os.remove(filepath)
                     print(f"  Удален файл {filepath} после ошибки сохранения в БД.")
                 except OSError as e_del:
                     print(f"  Не удалось удалить файл {filepath}: {e_del}")
             # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
             return "Ошибка сервера при сохранении информации о книге.", 500

    except Exception as e:
        print(f"ОШИБКА при обработке загрузки: {e}"); traceback.print_exc()
        # --- ИСПРАВЛЕНИЕ: Правильное удаление файлов ---
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"  Удален временный файл {temp_filepath} после ошибки.")
            except OSError as e_del:
                print(f"  Не удалось удалить временный файл {temp_filepath}: {e_del}")
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"  Удален файл {filepath} после ошибки.")
            except OSError as e_del:
                print(f"  Не удалось удалить файл {filepath}: {e_del}")
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        return "Ошибка сервера при обработке файла.", 500

@app.route('/book/<book_id>', methods=['GET'])
def view_book(book_id):
    print(f"Запрос страницы книги: {book_id}")
    book_info = get_book(book_id)
    if book_info is None: print(f"  Книга {book_id} не найдена.\n"); return "Книга не найдена.", 404

    book_db_language = book_info.get('target_language')
    target_language = book_db_language or request.args.get('lang') or session.get('target_language', 'russian')

    # --- ИЗМЕНЕНИЕ: Меняем модель по умолчанию на 'meta-llama/llama-4-scout:free' ---
    selected_model = session.get('model_name', 'meta-llama/llama-4-scout:free')
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    selected_operation = session.get('operation_type', 'translate')

    # --- Сохраняем определенный язык в сессию для последующих действий ---
    session['target_language'] = target_language
    session['operation_type'] = selected_operation # Save operation type to session
    session['model_name'] = selected_model # Save selected model to session


    print(f"  Параметры для отображения: lang='{target_language}', model='{selected_model}'.\n")
    available_models = get_models_list()
    if not available_models: available_models = list(set([selected_model, 'gemini-1.5-flash'])); print("  WARN: Не удалось получить список моделей.\n")
    prompt_ext_text = book_info.get('prompt_ext', '')

    resp = make_response(render_template('book_view.html', book_id=book_id, book_info=book_info, target_language=target_language, selected_model=selected_model, available_models=available_models, prompt_ext=prompt_ext_text, isinstance=isinstance, selected_operation=selected_operation))
    # --- ИЗМЕНЕНИЕ: Добавляем 'unsafe-inline' в script-src ---
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    resp.headers['Content-Security-Policy'] = csp_policy
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    return resp

@app.route('/save_prompt_ext/<book_id>', methods=['POST'])
def save_prompt_ext(book_id):
    print(f"Запрос на сохранение prompt_ext для книги: {book_id}\n")
    if not request.is_json: print("  Ошибка: Запрос не JSON.\n"); return jsonify({"success": False, "error": "Request must be JSON"}), 400
    data = request.get_json(); prompt_text = data.get('prompt_text')
    if prompt_text is None: print("  Ошибка: Отсутствует поле 'prompt_text'."); return jsonify({"success": False, "error": "Missing 'prompt_text'"}), 400
    if not get_book(book_id): print(f"  Ошибка: Книга {book_id} не найдена."); return jsonify({"success": False, "error": "Book not found"}), 404
    if update_book_prompt_ext(book_id, prompt_text): print(f"  prompt_ext для книги {book_id} успешно сохранен (длина: {len(prompt_text)})."); return jsonify({"success": True})
    else: print(f"  ОШИБКА: Не удалось обновить prompt_ext в БД для книги {book_id}."); return jsonify({"success": False, "error": "DB update failed"}), 500

@app.route('/translate_section/<book_id>/<section_id>', methods=['POST'])
def translate_section_request(book_id, section_id):
    print(f"Запрос на перевод секции: {book_id}/{section_id}")
    print("  [DEBUG] 1. Вызов get_book...")
    book_info = get_book(book_id)
    if book_info is None:
        print("  [DEBUG] 1.1. ОШИБКА: get_book вернул None!")
        return jsonify({"error": "Book not found"}), 404

    print(f"  [DEBUG] 2. book_info получен. Проверка filepath: {book_info.get('filepath')}")
    filepath = book_info.get("filepath")
    if not filepath or not os.path.exists(filepath):
        print(f"  [DEBUG] 2.1. ОШИБКА: Файл не найден по пути '{filepath}'!")
        return jsonify({"error": "EPUB file not found"}), 404

    print("  [DEBUG] 3. Файл найден. Получение JSON параметров...")
    try:
        data = request.get_json();
        if not data: raise ValueError("Missing JSON")
        target_language = data.get('target_language', session.get('target_language', 'russian'))
        model_name = data.get('model_name', session.get('model_name', 'gemini-1.5-flash'))
        operation_type = data.get('operation_type', 'translate') # Get operation type from JSON, default to 'translate'
        print(f"  [DEBUG] 3.1. Параметры получены: lang={target_language}, model={model_name}, operation={operation_type}")
    except Exception as e:
        print(f"  [DEBUG] 3.2. ОШИБКА получения параметров: {e}")
        return jsonify({"error": f"Invalid JSON payload: {e}"}), 400

    session['target_language'] = target_language; session['model_name'] = model_name
    session['operation_type'] = operation_type # Save operation type to session

    print(f"  [DEBUG] 4. Проверка section_info для ID: {section_id}")
    sections = book_info.get('sections', {})
    section_info = sections.get(section_id)
    if not section_info:
        print(f"  [DEBUG] 4.1. ОШИБКА: Данные для секции '{section_id}' не найдены!")
        # Выведем ключи, которые есть, для сравнения
        print(f"      Доступные ID секций: {list(sections.keys())}")
        return jsonify({"error": "Section data not found"}), 404

    print(f"  [DEBUG] 5. Данные секции найдены. Статус: {section_info.get('status')}")
    if section_info['status'] == 'processing':
        print("  [DEBUG] 5.1. Секция уже в обработке.")
        return jsonify({"status": "already_processing"}), 409

    print("  [DEBUG] 6. Получение prompt_ext...")
    prompt_ext_text = book_info.get('prompt_ext', '')
    print(f"     prompt_ext len: {len(prompt_ext_text)}")

    print("  [DEBUG] 7. Удаление кэша...")
    delete_section_cache(filepath, section_id, target_language)

    print("  [DEBUG] 8. Запуск задачи в executor...")
    task_id = str(uuid.uuid4())
    active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
    update_section_status(book_id, section_id, "processing")
    executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name, prompt_ext_text, operation_type)
    print(f"  [DEBUG] 9. Задача {task_id} запущена.")

    return jsonify({"status": "processing", "task_id": task_id}), 202

@app.route('/translate_all/<book_id>', methods=['POST'])
def translate_all_request(book_id):
    print(f"Запрос на перевод всех секций: {book_id}")
    book_info = get_book(book_id)
    if book_info is None: return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath")
    if not filepath or not os.path.exists(filepath): return jsonify({"error": "EPUB file not found"}), 404
    try:
        data = request.get_json();
        if not data: raise ValueError("Missing JSON")
        target_language = data.get('target_language', session.get('target_language', 'russian'))
        model_name = data.get('model_name', session.get('model_name', 'gemini-1.5-flash'))
        operation_type = data.get('operation_type', 'translate') # Get operation type from JSON, default to 'translate'
    except Exception as e: print(f"  Ошибка получения параметров: {e}"); return jsonify({"error": f"Invalid JSON payload: {e}"}), 400
    session['target_language'] = target_language; session['model_name'] = model_name
    session['operation_type'] = operation_type # Save operation type to session
    sections_list = book_info.get('sections', {})
    if not sections_list: return jsonify({"error": "No sections found"}), 404
    prompt_ext_text = book_info.get('prompt_ext', '')
    print(f"  Параметры: lang='{target_language}', model='{model_name}', prompt_ext len: {len(prompt_ext_text)}")
    launched_tasks = []; something_launched = False
    for section_id, section_data in sections_list.items():
        current_status = section_data['status']
        # Обрабатываем секцию, если ее статус НЕ является успешно завершенным или в процессе.
        # Список успешно завершенных статусов включает: translated, completed_empty, cached, summarized, analyzed.
        if current_status not in ['translated', 'completed_empty', 'processing', 'cached', 'summarized', 'analyzed']:
            if not get_translation_from_cache(filepath, section_id, target_language):
                task_id = str(uuid.uuid4()); active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
                update_section_status(book_id, section_id, "processing")
                executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name, prompt_ext_text, operation_type)
                launched_tasks.append(task_id); something_launched = True
            else: update_section_status(book_id, section_id, "cached", model_name, target_language)
    print(f"  Запущено {len(launched_tasks)} задач для 'Перевести все'.")
    if something_launched: update_overall_book_status(book_id)
    return jsonify({"status": "processing_all", "launched_tasks": len(launched_tasks)}), 202

# --- Остальные маршруты ---
@app.route('/task_status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    task_info = active_tasks.get(task_id)
    if task_info: return jsonify(task_info)
    else: return jsonify({"status": "not_found_or_completed"}), 404

@app.route('/book_status/<book_id>', methods=['GET'])
def get_book_status(book_id):
    """
    Возвращает JSON с текущим статусом книги и секций из БД.
    Статус книги НЕ пересчитывается при каждом запросе, а берется как есть.
    """
    update_overall_book_status(book_id) # Обновляем статус перед отдачей

    # Просто получаем текущее состояние книги из БД
    # get_book уже включает в себя получение секций ('sections') и их обработку для TOC
    book_info = get_book(book_id)
    if book_info is None:
         return jsonify({"error": "Book not found"}), 404

    # Получаем словарь секций из данных книги
    sections_dict = book_info.get('sections', {})
    total_sections = len(sections_dict) # Общее количество секций в БД для этой книги

    # --- Подсчет переведенных и ошибочных секций (для информации) ---
    # Этот подсчет не влияет на возвращаемый статус книги, он только для отображения прогресса
    translated_count = 0
    error_count = 0
    for section_data in sections_dict.values():
         status = section_data.get('status')
         # Считаем переведенными также кэшированные и пустые
         if status in ["translated", "completed_empty", "cached"]:
              translated_count += 1
         elif status and status.startswith("error_"):
              error_count +=1
    # --- Конец подсчета ---

    # --- Формируем данные о секциях для JSON ответа ---
    sections_for_json = {}
    for epub_id, sec_data in sections_dict.items():
        sections_for_json[epub_id] = {
            'status': sec_data.get('status', 'unknown'),
            'model_name': sec_data.get('model_name'), # Передаем имя модели
            'error_message': sec_data.get('error_message') # Передаем сообщение об ошибке
        }
    # --- Конец формирования данных о секциях ---

    # --- Возвращаем JSON ответ ---
    return jsonify({
         "filename": book_info.get('filename', 'N/A'),
         "total_sections": total_sections, # Общее число секций в БД
         "translated_count": translated_count, # Посчитано выше
         "error_count": error_count,         # Посчитано выше
         "status": book_info.get('status', 'unknown'), # <-- Берем статус книги КАК ОН ЕСТЬ в БД
         "sections": sections_for_json, # Словарь статусов секций
         "toc": book_info.get('toc', []) # Оглавление (уже с переведенными названиями из get_book)
    })

@app.route('/get_translation/<book_id>/<section_id>', methods=['GET'])
def get_section_translation_text(book_id, section_id):
    book_info = get_book(book_id);
    if book_info is None: return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath");
    target_language = book_info.get('target_language', session.get('target_language', 'russian'))

    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None: return jsonify({"text": translation})
    else:
        section_info = book_info.get('sections', {}).get(section_id)
        if section_info: status = section_info['status']; return jsonify({"error": f"Перевод не удался: {section_info.get('error_message', status)}"}) if status.startswith("error_") else jsonify({"error": "Перевод не найден или не готов"}), 404
        else: return jsonify({"error": "Данные раздела не найдены"}), 404

@app.route('/download_section/<book_id>/<section_id>', methods=['GET'])
def download_section(book_id, section_id):
    book_info = get_book(book_id);
    if book_info is None: return "Book not found", 404
    filepath = book_info.get("filepath");
    target_language = book_info.get('target_language', session.get('target_language', 'russian'))

    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None:
        safe_id = "".join(c for c in section_id if c.isalnum() or c in ('_','-')).rstrip(); filename = f"{safe_id}_{target_language}.txt"
        return Response(translation, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})
    else: return "Translation not found", 404

@app.route('/download_full/<book_id>', methods=['GET'])
def download_full(book_id):
    book_info = get_book(book_id);
    if book_info is None: return "Book not found", 404
    filepath = book_info.get("filepath");
    target_language = book_info.get('target_language', session.get('target_language', 'russian'))

    if book_info.get('status') not in ["complete", "completed_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409

    # Вместо получения section_ids из toc или sections.keys(),
    # всегда берем все ключи из словаря sections, т.к. это полный список обработанных секций
    sections_status = book_info.get('sections', {})
    if not sections_status:
        return "No sections found in book data", 500 # Добавил более явное сообщение об ошибке

    section_ids_to_process = sections_status.keys() # Берем ВСЕ ID секций из БД

    full_text_parts = []; missing_cache = []; errors = [];
    for id in section_ids_to_process:
        data = sections_status.get(id, {})
        status = data.get('status', '?')
        error_message = data.get('error_message')

        # Всегда пытаемся получить кэш для каждой секции, независимо от статуса в БД
        tr = get_translation_from_cache(filepath, id, target_language)

        # Условие для добавления в итоговый текст:
        # 1. Кэш найден (даже если пустой, т.к. completed_empty секции должны быть включены)
        # ИЛИ 2. Статус секции указывает на ошибку (чтобы включить информацию об ошибке в файл)
        if tr is not None: # get_translation_from_cache возвращает None только при ошибке чтения или отсутствии файла
             # Кэш успешно прочитан (даже если файл был пустой для completed_empty)
             full_text_parts.extend([f"\n\n==== {id} ({status}) ====\n\n", tr])
        elif status.startswith("error_"):
             # Кэша нет, но статус - ошибка. Добавляем заголовок с ошибкой.
             errors.append(id)
             full_text_parts.append(f"\n\n==== {id} (ОШИБКА: {error_message or status}) ====\n\n")
        else:
             # Кэша нет, и статус не ошибка (например, 'not_translated'). Отмечаем как пропущенное.
             missing_cache.append(id)
             full_text_parts.append(f"\n\n==== {id} (ПРЕДУПРЕЖДЕНИЕ: Нет кэша {target_language}, статус: {status}) ====\n\n")


    if not full_text_parts:
         # Это должно произойти только если sections_status не пуст, но для всех секций
         # get_translation_from_cache вернул None и статус не error_
         return f"Не удалось получить текст или информацию об ошибке для '{target_language}'.", 404

    # Добавляем предупреждения в начало, если есть пропущенные или ошибочные секции
    warnings = []
    if missing_cache:
        warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: Нет кэша {target_language} (или ошибка чтения кэша) для секций: {", ".join(missing_cache)}\n")
    if errors:
        warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: Ошибки обработки для секций: {", ".join(errors)}\n")

    full_text = "".join(warnings) + "".join(full_text_parts) # Добавляем предупреждения в начало

    base_name = os.path.splitext(book_info['filename'])[0]; out_fn = f"{base_name}_{target_language}_translated.txt"
    return Response(full_text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{out_fn}"})

@app.route('/api/models', methods=['GET'])
def api_get_models():
    models = get_models_list()
    if models is not None: return jsonify(models)
    else: return jsonify({"error": "Could not retrieve models"}), 500

@app.route('/download_epub/<book_id>', methods=['GET'])
def download_epub(book_id):
    book_info = get_book(book_id);
    if book_info is None: return "Book not found", 404
    target_language = book_info.get('target_language', session.get('target_language', 'russian'))
    update_overall_book_status(book_id); book_info = get_book(book_id)
    if book_info.get('status') not in ["completed", "completed_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409
    epub_bytes = create_translated_epub(book_info, target_language) # book_info уже содержит 'sections'
    if epub_bytes is None: return "Server error generating EPUB", 500
    base_name = os.path.splitext(book_info.get('filename', 'tr_book'))[0]; out_fn = f"{base_name}_{target_language}_translated.epub"
    return send_file(io.BytesIO(epub_bytes), mimetype='application/epub+zip', as_attachment=True, download_name=out_fn)

def get_bbc_news():
    """Получает заголовки новостей BBC с NewsAPI."""
    import requests # Added import inside function
    url = 'https://newsapi.org/v2/top-headlines?sources=bbc-news'
    headers = {'x-api-key': '2126e6e18adb478fb9ade262cb1102af'}
    news_titles = []
    try:
        response = requests.get(url, headers=headers, timeout=10) # Добавляем таймаут
        response.raise_for_status() # Проверяем на HTTP ошибки (4xx, 5xx)

        data = response.json()
        articles = data.get("articles", [])
        # --- Извлекаем ЗАГОЛОВКИ (title) ---
        news_titles = [article["title"] for article in articles if "title" in article and article["title"]]
        print(f"[BBC News] Получено {len(news_titles)} заголовков.")

    except requests.exceptions.RequestException as e:
        print(f"[BBC News] Ошибка сети или API при получении новостей: {e}")
    except Exception as e:
        print(f"[BBC News] Неожиданная ошибка при обработке новостей: {e}")

    return news_titles

# --- Маршрут для Алисы (упрощенный) ---
@app.route('/alice', methods=['POST'])
def alice_webhook():
    """ Обрабатывает запросы от Яндекс.Алисы, вызывая alice_handler. """
    request_data = request.json
    response_payload = alice_handler.handle_alice_request(request_data)
    return jsonify(response_payload)
# --- КОНЕЦ Маршрута для Алисы ---

# --- НОВЫЙ МАРШРУТ для "умной" Алисы ---
@app.route('/alice/smart', methods=['POST'])
def alice_smart_webhook():
    """ Обрабатывает запросы к Gemini через Алису. """
    request_data = request.json
    # Вызываем новую логику из alice_handler
    response_payload = alice_handler.handle_smart_alice_request(request_data)
    return jsonify(response_payload)
# --- КОНЕЦ НОВОГО МАРШРУТА ---

# --- НОВЫЕ МАРШРУТЫ ДЛЯ ПОИСКА ЛОКАЦИЙ (вставляются в конец секции маршрутов) ---
APP_PRINT_PREFIX = "[AppLF]"

@app.route('/find-locations-form', methods=['GET'])
def find_locations_form_page():
    print(f"{APP_PRINT_PREFIX} Запрос страницы /find-locations-form (GET)")
    return render_template('find_locations_form.html')

@app.route('/api/locations', methods=['POST'])
def api_find_persons_locations():
    print(f"\n{APP_PRINT_PREFIX} Поступил запрос на /api/locations (POST)")

    if not request.is_json:
        print(f"{APP_PRINT_PREFIX}  Ошибка: Запрос не является JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    try:
        data = request.get_json()
        print(f"{APP_PRINT_PREFIX}  Получено JSON тело: {json.dumps(data, ensure_ascii=False)}") # Можно и вывести тело для отладки
    except Exception as e_json:
        print(f"{APP_PRINT_PREFIX}  Ошибка парсинга JSON: {e_json}")
        if 'traceback' in globals() or 'traceback' in locals(): traceback.print_exc()
        return jsonify({"error": f"Invalid JSON payload: {e_json}"}), 400

    person_names_raw = data.get('persons')
    test_mode_flag = data.get('test_mode', False) # Получаем флаг тестового режима

    print(f"{APP_PRINT_PREFIX}  Получен флаг test_mode: {test_mode_flag}")

    if not person_names_raw or not isinstance(person_names_raw, list):
        # ... (обработка ошибки списка person_names)
        print(f"{APP_PRINT_PREFIX}  Ошибка: Отсутствует или неверный список 'persons' в JSON. Получено: {person_names_raw}")
        return jsonify({"error": "Missing or invalid 'persons' list in JSON body"}), 400

    valid_names = []
    # ... (валидация имен) ...
    for i, name_raw in enumerate(person_names_raw):
        if not isinstance(name_raw, str) or not name_raw.strip():
            print(f"{APP_PRINT_PREFIX}  Ошибка: Обнаружено невалидное имя '{name_raw}' на позиции {i}.")
            return jsonify({"error": f"Invalid name found in 'persons' list: '{name_raw}'. All names must be non-empty strings."}), 400
        valid_names.append(name_raw.strip())
    if not valid_names:
         print(f"{APP_PRINT_PREFIX}  Ошибка: Список 'persons' не содержит валидных имен после очистки.")
         return jsonify({"error": "The 'persons' list contains no valid (non-empty, non-whitespace) names."}),400


    print(f"{APP_PRINT_PREFIX}  Валидные имена для поиска: {valid_names}")

    try:
        print(f"{APP_PRINT_PREFIX}  Вызов location_finder.find_persons_locations с {valid_names}, test_mode={test_mode_flag}...")
        # Передаем флаг test_mode
        locations_map_with_coords = location_finder.find_persons_locations(valid_names, test_mode=test_mode_flag)

        print(f"{APP_PRINT_PREFIX}  Результат от location_finder: {json.dumps(locations_map_with_coords, ensure_ascii=False, indent=2)}")
        print(f"{APP_PRINT_PREFIX}  Отправка JSON ответа клиенту.")
        return jsonify(locations_map_with_coords)

    except Exception as e:
        # ... (обработка общей ошибки) ...
        print(f"{APP_PRINT_PREFIX}  КРИТИЧЕСКАЯ ОШИБКА в /api/locations: {e}")
        if 'traceback' in globals() or 'traceback' in locals(): traceback.print_exc()
        error_response = {name: f"Server error processing request for this person ({type(e).__name__})" for name in valid_names}
        print(f"{APP_PRINT_PREFIX}  Отправка JSON с общей ошибкой сервера: {json.dumps(error_response, ensure_ascii=False)}")
        return jsonify(error_response), 500
# --- КОНЕЦ НОВЫХ МАРШРУТОВ ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ЗАГРУЗКИ И ЗАПУСКА РАБОЧЕГО ПРОЦЕССА ---
@app.route('/workflow_upload', methods=['POST'])
def workflow_upload_file():
    """ Обрабатывает загрузку EPUB для нового рабочего процесса, создает запись в новой БД и запускает процесс. """
    print("Запрос на загрузку файла для рабочего процесса.")
    if 'epub_file' not in request.files: return "Файл не найден", 400
    file = request.files['epub_file'];
    if file.filename == '': return "Файл не выбран", 400
    if not allowed_file(file.filename): return "Ошибка: Недопустимый тип файла.", 400

    # Целевой язык пока берем из формы или сессии (по аналогии со старым)
    form_language = request.form.get('target_language') # TODO: Убедиться, что форма на новой главной странице передает язык
    target_language = form_language or session.get('target_language', 'russian')

    original_filename = secure_filename(file.filename)
    temp_dir = app.config['UPLOAD_FOLDER']
    temp_filepath = None # Инициализируем None
    filepath = None
    book_id = None

    try:
        # Сохранение и определение Book ID
        temp_filename = f"temp_{uuid.uuid4().hex}.epub"
        temp_filepath = os.path.join(temp_dir, temp_filename)
        file.save(temp_filepath); print(f"Файл временно сохранен: {temp_filepath}")

        book_id = _get_epub_id(temp_filepath); print(f"Вычислен Book ID: {book_id}")

        # Проверяем, существует ли книга уже в новой БД
        if workflow_db_manager.get_book_workflow(book_id):
             print(f"Книга с ID {book_id} уже существует в Workflow DB. Перенаправление...")
             # TODO: Перенаправить на страницу новой книги
             return f"Книга с ID {book_id} уже существует.", 200 # Временно возвращаем сообщение

        # Если книга новая, сохраняем файл с уникальным именем
        unique_filename = f"{book_id}.epub"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        # Убедимся, что файл с таким именем не существует (на всякий случай, хотя Book ID должен быть уникальным)
        if os.path.exists(filepath):
             print(f"Предупреждение: Файл книги {book_id} уже существует при новой загрузке. Удаляем старый.")
             try: os.remove(filepath) # Удаляем старый файл, если он почему-то остался
             except OSError as e: print(f"Ошибка при удалении старого файла {filepath}: {e}")

        os.rename(temp_filepath, filepath); print(f"Файл перемещен в хранилище workflow: {filepath}"); temp_filepath = None # Файл успешно перемещен, обнуляем temp_filepath

        # Парсим структуру EPUB и оглавление
        section_ids, id_to_href_map = epub_parser.get_epub_structure(filepath)
        if section_ids is None: raise ValueError("Не удалось получить структуру EPUB для workflow.")
        toc = epub_parser.get_epub_toc(filepath, id_to_href_map) or []

        # Перевод оглавления для новой БД не требуется на этом этапе, т.к. мы не используем его для отображения секций как раньше.
        # Названия секций в БД нужны только для информации. Возьмем оригинальные или заглушки.
        sections_data_for_db = []
        order_in_book = 0
        # Создаем мапу href -> toc_title для быстрого поиска названия по href
        href_to_title_map = {item['href']: item.get('title') for item in toc if item.get('href')}

        for section_id_epub in section_ids:
             # Ищем соответствующий элемент в TOC по EPUB ID (немного костыльно, но пока так)
             # Лучше было бы, если get_epub_structure возвращал бы не только ID, но и href/title
             # Найдем href для этого section_id_epub
             section_href = id_to_href_map.get(section_id_epub)
             section_title_original = href_to_title_map.get(section_href) if section_href else None # Берем название из TOC по href
             # Если нет названия из TOC, попробуем использовать section_id_epub как название или заглушку
             if not section_title_original:
                 section_title_original = section_id_epub # Используем EPUB ID как название по умолчанию
                 print(f"Предупреждение: Не найдено название TOC для секции {section_id_epub}. Используем ID.")

             sections_data_for_db.append({
                 'section_epub_id': section_id_epub,
                 'section_title': section_title_original,
                 'translated_title': None, # Переведенное название пока не нужно
                 'order_in_book': order_in_book
             })
             order_in_book += 1

        # Создаем запись о книге в новой БД
        if workflow_db_manager.create_book_workflow(book_id, original_filename, filepath, toc, target_language):
             print(f"  Книга '{book_id}' сохранена в Workflow DB.")

             # --- ДОБАВЛЯЕМ ИНИЦИАЛИЗАЦИЮ СТАТУСОВ ЭТАПОВ КНИГИ ---
             workflow_db_manager._initialize_book_stage_statuses(book_id)
             # --- КОНЕЦ ДОБАВЛЕНИЯ ---

             sec_created_count = 0
             # Создаем записи о секциях в новой БД
             for section_data in sections_data_for_db:
                  if workflow_db_manager.create_section_workflow(
                      book_id,
                      section_data['section_epub_id'],
                      section_data['section_title'],
                      section_data['translated_title'],
                      section_data['order_in_book']
                  ):
                       sec_created_count += 1
             print(f"  Создано {sec_created_count} записей о секциях в Workflow DB.")

             # --- Запускаем рабочий процесс для книги ---
             # Запускаем в отдельном потоке, чтобы запрос не висел
             # TODO: Использовать более надежный способ запуска фоновых задач, например, очередь задач (Celery, Redis Queue)
             # Пока используем простой поток для демонстрации
             import threading
             # --- ИЗМЕНЕНИЕ: Запускаем поток с контекстом приложения ---
             def run_workflow_in_context(app, book_id):
                 with app.app_context():
                     workflow_processor.start_book_workflow(book_id)
             threading.Thread(target=run_workflow_in_context, args=(app, book_id,)).start()
             # --- КОНЕЦ ИЗМЕНЕНИЯ ---
             print(f"  Запущен рабочий процесс для книги ID {book_id} в отдельном потоке.")

             # TODO: Перенаправить на страницу новой главной страницы workflow
             # Пока просто возвращаем успешный ответ
             # return f"Книга {original_filename} загружена и запущен рабочий процесс с ID: {book_id}", 200
             
             # --- ИЗМЕНЕНИЕ: Возвращаем JSON с book_id ---
             return jsonify({"status": "success", "message": "Книга загружена и запущен рабочий процесс.", "book_id": book_id, "filename": original_filename, "total_sections_count": sec_created_count}), 200
             # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        else:
             # Если не удалось создать запись книги в БД, удаляем файл
             print(f"ОШИБКА: Не удалось сохранить книгу '{book_id}' в Workflow DB! Удаляем файл.")
             if filepath and os.path.exists(filepath):
                 try: os.remove(filepath)
                 except OSError as e: print(f"  Не удалось удалить файл {filepath} после ошибки БД: {e}")
             return "Ошибка сервера при сохранении информации о книге в Workflow DB.", 500

    except Exception as e:
        print(f"ОШИБКА при обработке загрузки для workflow: {e}"); traceback.print_exc()
        # Удаляем временный и сохраненный файлы в случае любой ошибки
        if temp_filepath and os.path.exists(temp_filepath):
            try: os.remove(temp_filepath)
            except OSError as e_del: print(f"  Не удалось удалить временный файл {temp_filepath} после ошибки: {e_del}")
        if filepath and os.path.exists(filepath):
            try: os.remove(filepath)
            except OSError as e_del: print(f"  Не удалось удалить файл {filepath} после ошибки: {e_del}")

        return "Ошибка сервера при обработке файла для рабочего процесса.", 500

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ОТОБРАЖЕНИЯ СПИСКА КНИГ В РАБОЧЕМ ПРОЦЕССЕ ---
@app.route('/workflow', methods=['GET'])
def workflow_index():
    """ Отображает страницу со списком книг в новом рабочем процессе. """
    print("Загрузка страницы списка книг рабочего процесса...")

    workflow_books = []
    try:
        # Получаем список книг из новой базы данных
        db_books = workflow_db_manager.get_all_books_workflow()
        for book_data in db_books:
             # Получаем количество секций для отображения прогресса
             total_sections = workflow_db_manager.get_section_count_for_book_workflow(book_data['book_id'])
             # Получаем количество секций, завершенных на этапе суммаризации (для отображения прогресса на главном экране)
             completed_sections_count = workflow_db_manager.get_completed_sections_count_for_stage_workflow(book_data['book_id'], 'summarize')

             workflow_books.append({
                 'book_id': book_data['book_id'],
                 'filename': book_data['filename'], # Используем 'filename' для отображения
                 'status': book_data['current_workflow_status'],
                 'target_language': book_data.get('target_language'),
                 'total_sections': total_sections,
                 'completed_sections_count': completed_sections_count # Передаем количество завершенных секций
             })
        workflow_books.sort(key=lambda x: x['filename'].lower()) # Сортируем по имени файла
        print(f"  Найдено книг в Workflow DB: {len(workflow_books)}")
    except Exception as e:
        print(f"ОШИБКА при получении списка книг из Workflow DB: {e}")
        import traceback
        traceback.print_exc() # Логируем полный трейсбэк

    # TODO: Добавить передачу языка и модели по умолчанию, если они нужны на этой странице
    # TODO: Добавить логику получения списка доступных моделей, если форма загрузки будет использовать выбор модели

    resp = make_response(render_template('workflow_index.html', workflow_books=workflow_books))
    # Наследуем CSP политику от основной страницы
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    resp.headers['Content-Security-Policy'] = csp_policy

    return resp

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ СКАЧИВАНИЯ СУММАРИЗАЦИИ WORKFLOW ---
@app.route('/workflow_download_summary/<book_id>', methods=['GET'])
def workflow_download_summary(book_id):
    print(f"Запрос на скачивание суммаризации для книги: {book_id}")
    # Убедимся, что вызываем функции работы с workflow DB/кешем
    # from . import workflow_db_manager, workflow_cache_manager # Remove commented import
    # import os # Remove commented import
    # import html # Remove commented import
    # from flask import Response # Remove commented import

    # 1. Получаем информацию о книге из Workflow DB
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if book_info is None:
        print(f"  [DownloadSummary] Книга с ID {book_id} не найдена в Workflow DB.")
        return "Book not found", 404

    # Проверяем статус этапа суммаризации книги.
    # Получаем статусы этапов книги
    book_stage_statuses = book_info.get('book_stage_statuses', {})
    summarize_stage_status = book_stage_statuses.get('summarize', {}).get('status')

    # TODO: Решить, нужно ли требовать статус complete или разрешать скачивание partial results
    # Пока требуем complete
    # На фронтенде ссылка должна появляться только при complete статусе.
    if summarize_stage_status != 'completed':
         print(f"  [DownloadSummary] Этап суммаризации для книги {book_id} не завершен. Статус: {summarize_stage_status}")
         # Возвращаем 409 Conflict, чтобы показать, что ресурс не готов
         return f"Summarization not complete (Status: {summarize_stage_status}).", 409

    # 2. Получаем список секций для книги
    # Берем секции из Workflow DB, они упорядочены по order_in_book
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    if not sections:
        print(f"  [DownloadSummary] Не найдено секций для книги {book_id} в Workflow DB.")
        # Это unexpected, т.к. книга есть, а секций нет. Возможно, ошибка парсинга при загрузке.
        return "No sections found for this book", 500

    full_summary_parts = []
    missing_summaries = [] # Секции, для которых не удалось получить результат
    error_sections = [] # Секции, где этап суммаризации завершился с ошибкой

    # 3. Итерируемся по секциям и загружаем суммаризацию из workflow кеша
    for section_data in sections:
        section_id = section_data['section_id'] # Внутренний ID секции из БД
        section_epub_id = section_data['section_epub_id'] # Оригинальный EPUB ID секции
        # Используем заголовок из БД, если есть, иначе заглушку с порядковым номером
        section_title = section_data.get('section_title') or f'Section {section_data["order_in_book"] + 1}'
        # Используем переведенный заголовок, если есть, для отображения в файле
        display_title = section_data.get('translated_title') or section_title

        # Получаем статус этой секции для этапа суммаризации из данных секции
        # (Эти статусы уже загружены workflow_db_manager.get_sections_for_book_workflow)
        section_stage_statuses = section_data.get('stage_statuses', {})
        summarize_section_status = section_stage_statuses.get('summarize', {}).get('status')
        section_error_message = section_stage_statuses.get('summarize', {}).get('error_message')


        summary_text = None
        try:
             summary_text = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
        except Exception as e:
             print(f"  [DownloadSummary] ОШИБКА при загрузке суммаризации из кеша для секции {section_id} (EPUB ID: {section_epub_id}): {e}")
             continue # Пропускаем секцию при ошибке чтения кеша

        # Обработка результата, полученного из кеша, и статуса секции из БД
        if summary_text is not None and summary_text.strip() != "":
            # Успешно загружена непустая суммаризация ИЛИ загружен пустой файл для completed_empty
            # (т.к. load_section_stage_result для completed_empty вернет пустую строку, а не None)
            # Проверяем статус секции, чтобы понять причину пустого текста, если он пустой
            if summary_text.strip() == "" and summarize_section_status != 'completed_empty':
                 # Текст пустой, но статус не completed_empty - это unexpected
                 missing_summaries.append(section_epub_id)
                 escaped_title = html.escape(display_title)
                 full_summary_parts.append(f"\n\n==== {section_epub_id} - {escaped_title} (ПРЕДУПРЕЖДЕНИЕ: Суммаризация пуста, статус {summarize_section_status or 'unknown'}) ====\n\n")
            else:
                 # Успешно загружена непустая суммаризация ИЛИ пустая для completed_empty
                 escaped_title = html.escape(display_title)
                 # Добавляем заголовок секции и текст суммаризации
                 full_summary_parts.append(f"\n\n==== {section_epub_id} - {escaped_title} ====\n\n{summary_text}")

        elif summarize_section_status and (summarize_section_status == 'error' or summarize_section_status.startswith('error_')):
             # В кеше нет текста (или ошибка загрузки), а статус секции в БД - ошибка.
             error_sections.append(section_epub_id)
             escaped_title = html.escape(display_title)
             full_summary_parts.append(f"\n\n==== {section_epub_id} - {escaped_title} (ОШИБКА: {section_error_message or summarize_section_status}) ====\n\n")
        else:
            # Если статус не error_ и не completed_empty, и текст из кеша None/пустой (и не completed_empty).
            # Это означает, что для этой секции нет готового результата суммаризации в кеше,
            # хотя общий статус книги 'complete'. Такого быть не должно при корректном workflow.
            # Обработаем на всякий случай.
             missing_summaries.append(section_epub_id)
             escaped_title = html.escape(display_title)
             full_summary_parts.append(f"\n\n==== {section_epub_id} - {escaped_title} (ПРЕДУПРЕЖДЕНИЕ: Суммаризация недоступна. Статус секции: {summarize_section_status or 'unknown'}) ====\n\n")


    # Проверяем, удалось ли получить хоть какой-то контент (даже ошибки/предупреждения)
    if not full_summary_parts:
         # Это может произойти, если не удалось получить ни одного результата или ошибки для всех секций
         print(f"  [DownloadSummary] Не удалось собрать текст суммаризации для книги {book_id} из кеша workflow.")
         # Если статус книги complete, но нет данных, возможно, проблема в кеше.
         return "Could not retrieve any summary text from workflow cache.", 500


    # Добавляем предупреждения в начало, если есть пропущенные или ошибочные секции
    # Если есть missing_summaries или error_sections, добавляем общий заголовок предупреждений
    all_warnings = []
    if missing_summaries or error_sections:
         all_warnings.append("==== ПРЕДУПРЕЖДЕНИЯ / ERRORS ====\n\n")
    if missing_summaries:
        # Corrected f-string syntax
        all_warnings.append(f"Не удалось загрузить суммаризацию из кеша workflow (или результат был пуст при неожиданном статусе) для секций (EPUB ID): {', '.join(missing_summaries)}\n\n")
    if error_sections:
        # Corrected f-string syntax
        all_warnings.append(f"Суммаризация завершилась с ошибками на уровне секций для (EPUB ID): {', '.join(error_sections)}\n\n")
    if all_warnings:
         all_warnings.append("==== КОНЕЦ ПРЕДУПРЕЖДЕНИЙ / END OF ERRORS ====\n\n")

    full_summary_text = "".join(all_warnings) + "\n".join(full_summary_parts) # Добавляем предупреждения в начало

    # 4. Формируем и отдаем файл
    # Имя файла для скачивания: [имя_оригинала_без_расширения]_summarized.txt
    # Берем оригинальное имя файла из book_info
    base_name = os.path.splitext(book_info.get('filename', 'summary_book'))[0]
    out_fn = f"{base_name}_summarized.txt"

    # Возвращаем текст как файл
    return Response(full_summary_text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{out_fn}"})

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ПОЛУЧЕНИЯ СТАТУСА WORKFLOW КНИГИ ---
@app.route('/workflow_book_status/<book_id>', methods=['GET'])
def get_workflow_book_status(book_id):
    print(f"Запрос статуса workflow для книги: {book_id}")

    # Убедимся, что вызываем функции работы с workflow DB
    # Импорты workflow_db_manager, json, Response, jsonify должны быть на верхнем уровне

    book_info = workflow_db_manager.get_book_workflow(book_id)

    if book_info is None:
        print(f"  Книга с ID {book_id} не найдена в Workflow DB.")
        return jsonify({"error": "Book not found in workflow database"}), 404

    # Получаем детальные статусы этапов и секций из book_info
    # get_book_workflow уже должен загружать 'book_stage_statuses' и 'sections'
    book_stage_statuses = book_info.get('book_stage_statuses', {})
    sections = book_info.get('sections', []) # Список секций с их stage_statuses

    # Формируем ответ
    response_data = {
        "book_id": book_info.get('book_id'),
        "filename": book_info.get('filename'),
        "current_workflow_status": book_info.get('current_workflow_status'),
        "book_stage_statuses": book_stage_statuses,
        "total_sections_count": book_info.get('total_sections_count', len(sections)), # Берем из book_info или считаем
        "sections_status_summary": {} # Сводка статусов секций по этапам
    }

    # --- Добавляем completed_count для этапа summarize в book_stage_statuses ---
    if 'summarize' in response_data['book_stage_statuses']:
        # Берем общее количество обработанных секций (completed + skipped + empty)
        processed_sum_count = book_info.get('processed_sections_count_summarize', 0)
        response_data['book_stage_statuses']['summarize']['completed_count'] = processed_sum_count
    # --- Конец добавления completed_count ---

    # Подсчитаем количество секций для каждого статуса на каждом этапе
    stage_names = list(book_stage_statuses.keys()) # Получаем список этапов книги
    # Также добавим этапы, которые есть у секций, но могут отсутствовать на уровне книги пока
    for section in sections:
         for stage_name in section.get('stage_statuses', {}).keys():
              if stage_name not in stage_names: stage_names.append(stage_name)

    for stage_name in stage_names:
         response_data['sections_status_summary'][stage_name] = {
             'total': len(sections),
             'completed': 0,
             'completed_empty': 0,
             'processing': 0,
             'queued': 0,
             'error': 0,
             'skipped': 0,
             'pending': 0,
             'cached': 0 # Для перевода
         }
         for section in sections:
              section_stage_status = section.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending')
              if section_stage_status in response_data['sections_status_summary'][stage_name]:
                   response_data['sections_status_summary'][stage_name][section_stage_status] += 1
              else:
                   # Если встречен неизвестный статус
                   print(f"  [WorkflowStatusAPI] Предупреждение: Неизвестный статус секции '{section.get('section_epub_id')}' для этапа '{stage_name}': '{section_stage_status}'")
                   # Можно добавить в отдельную категорию или игнорировать

    # TODO: Включить в ответ детальные статусы каждой секции, если нужно для фронтенда
    # response_data['sections_details'] = sections # Осторожно: может быть большой объем данных!

    return jsonify(response_data), 200

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА СТАТУСА ---

@app.route('/workflow_delete_book/<book_id>', methods=['POST'])
def workflow_delete_book_request(book_id):
    """ Удаляет книгу рабочего процесса, ее файл и кэш. """
    print(f"Запрос на удаление книги рабочего процесса: {book_id}")

    book_info = workflow_db_manager.get_book_workflow(book_id)

    if book_info:
        filepath = book_info.get("filepath"); original_filename = book_info.get("filename", book_id)
        if workflow_db_manager.delete_book_workflow(book_id): print(f"  Запись '{original_filename}' удалена из Workflow БД.")
        else: print(f"  ОШИБКА удаления записи из Workflow БД!")

        if filepath and os.path.exists(filepath):
            try: os.remove(filepath); print(f"  Файл {filepath} удален.")
            except OSError as e: print(f"  Ошибка удаления файла {filepath}: {e}")
        # Удаление кэша книги Workflow
        workflow_cache_manager.delete_book_workflow_cache(book_id)
        print(f"  Кеш workflow для книги {book_id} удален.")
    else: print(f"  Книга рабочего процесса {book_id} не найдена в БД.")

    # Возвращаем JSON ответ, так как удаление происходит через AJAX
    return jsonify({'success': True, 'book_id': book_id}), 200

# --- Запуск приложения ---
if __name__ == '__main__':
    print("Запуск Flask приложения...")
    # use_reloader=False рекомендуется при использовании APScheduler в режиме отладки,
    # чтобы избежать двойного запуска планировщика. Но можно попробовать и без него.
    try:
        configure_api() # Проверка ключей API
        load_models_on_startup() # <-- ДОБАВЛЯЕМ ЭТОТ ВЫЗОВ
        # --- ИЗМЕНЕНИЕ: Добавляем инициализацию workflow DB ---
        with app.app_context():
            workflow_db_manager.init_workflow_db()
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except ValueError as e:
        print(f"Ошибка конфигурации API: {e}")
        # Возможно, стоит явно выйти из приложения или как-то иначе сообщить об ошибке
        exit(1)
# --- END OF FILE app.py ---