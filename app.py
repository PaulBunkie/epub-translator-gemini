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

# Flask и связанные утилиты
from flask import (
    Flask, request, render_template, redirect, url_for,
    jsonify, send_from_directory, Response, session, g, send_file, make_response, current_app
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
import html
import video_analyzer
import toptube10
import video_db

# --- Настройки ---
from config import UPLOADS_DIR, CACHE_DIR, FULL_TRANSLATION_DIR

UPLOAD_FOLDER = str(UPLOADS_DIR)
ALLOWED_EXTENSIONS = {'epub'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.urandom(24) # Для сессий и flash-сообщений

# --- Регистрация закрытия соединения с workflow БД ---
app.teardown_appcontext(workflow_db_manager.close_workflow_db)

# --- Инициализируем БД при старте приложения ---
with app.app_context():
     init_db()
     # Инициализируем новую БД для рабочего процесса
     workflow_db_manager.init_workflow_db()
     # Инициализируем БД для видео
     video_db.init_video_db()

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

# Модель для перевода новостей, настраиваемая через переменные окружения
NEWS_MODEL_NAME = os.getenv("NEWS_TRANSLATION_MODEL", "meta-llama/llama-4-maverick:free")

# Добавляем задачу обновления кеша новостей, выполняться каждый час
scheduler.add_job(
    alice_handler.update_translated_news_cache,
    'interval',
    hours=1,
    args=[NEWS_MODEL_NAME],   # Передаем имя модели в задачу
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

# --- ЗАДАНИЯ ДЛЯ TOPTUBE ---
scheduler.add_job(
    toptube10.full_workflow_task,
    trigger='interval',
    hours=4,  # Полный рабочий процесс каждые 4 часа
    id='toptube_full_workflow_job',
    replace_existing=True,
    misfire_grace_time=1800  # 30 минут grace time для длительного процесса
)
print("[Scheduler] Задание 'toptube_full_workflow_job' добавлено (полный рабочий процесс каждые 4 часа).")
# --- КОНЕЦ ЗАДАНИЙ ДЛЯ TOPTUBE ---

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
         overall_status = "complete" if error_count == 0 else "complete_with_errors"

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
    # --- НОВОЕ: Проверка и установка режима администратора ---
    admin_param = request.args.get('admin')
    if admin_param == 'true':
        session['admin_mode'] = True
        print("Включен режим администратора (показ всех моделей).")
    elif admin_param == 'false':
        session.pop('admin_mode', None)
        print("Выключен режим администратора.")
    # --- КОНЕЦ НОВОГО ---

    print("Загрузка главной страницы...")
    default_language = session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'meta-llama/llama-4-maverick:free')
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    
    # Получаем список моделей, учитывая режим администратора
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models:
        available_models = [
            {
                'name': 'meta-llama/llama-4-maverick:free',
                'display_name': 'Meta Llama 4 Maverick (Free)',
                'description': 'Default Meta Llama model'
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

    resp = make_response(render_template('index.html', uploaded_books=uploaded_books, default_language=default_language, selected_model=selected_model, available_models=available_models, is_admin_mode=is_admin_mode))
    # --- ИЗМЕНЕНИЕ: Добавляем 'unsafe-inline' в script-src ---
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    resp.headers['Content-Security-Policy'] = csp_policy
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    return resp

@app.route('/delete_book/<book_id>', methods=['POST'])
def delete_book_request(book_id):
    """ Удаляет книгу, ее файл и кэш. """
    print(f"Запрос на удаление книги: {book_id}")
    book_info = get_book(book_id)
    if book_info:
        filepath = book_info.get("filepath"); original_filename = book_info.get("filename", book_id)
        if delete_book(book_id): print(f"  Запись '{original_filename}' удалена из БД.")
        else: print(f"  ОШИБКА удаления записи из БД!")
        if filepath and os.path.exists(filepath):
            try: os.remove(filepath); print(f"  Файл {filepath} удален.")
            except OSError as e: print(f"  Ошибка удаления файла {filepath}: {e}")
        if filepath: delete_book_cache(filepath)
    else: print(f"  Книга {book_id} не найдена в БД.")
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
             toc_model = session.get('model_name', 'meta-llama/llama-4-maverick:free')
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

    # --- ИЗМЕНЕНИЕ: Меняем модель по умолчанию на 'meta-llama/llama-4-maverick:free' ---
    selected_model = session.get('model_name', 'meta-llama/llama-4-maverick:free')
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    selected_operation = session.get('operation_type', 'translate')

    # --- Сохраняем определенный язык в сессию для последующих действий ---
    session['target_language'] = target_language
    session['operation_type'] = selected_operation # Save operation type to session
    session['model_name'] = selected_model # Save selected model to session


    print(f"  Параметры для отображения: lang='{target_language}', model='{selected_model}'.\n")
    # Получаем список моделей, учитывая режим администратора
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models: available_models = list(set([selected_model, 'meta-llama/llama-4-maverick:free'])); print("  WARN: Не удалось получить список моделей.\n")
    prompt_ext_text = book_info.get('prompt_ext', '')

    resp = make_response(render_template('book_view.html', book_id=book_id, book_info=book_info, target_language=target_language, selected_model=selected_model, available_models=available_models, prompt_ext=prompt_ext_text, isinstance=isinstance, selected_operation=selected_operation, is_admin_mode=is_admin_mode))
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
        model_name = data.get('model_name', session.get('model_name', 'meta-llama/llama-4-maverick:free'))
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
        model_name = data.get('model_name', session.get('model_name', 'meta-llama/llama-4-maverick:free'))
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

    if book_info.get('status') not in ["complete", "complete_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409

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
        warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: Нет кэша {target_language} (или ошибка чтения кэша) для секций: {', '.join(missing_cache)}\n")
    if errors:
        warnings.append(f"ПРЕДУПРЕЖДЕНИЕ: Ошибки обработки для секций: {', '.join(errors)}\n")

    full_text = "".join(warnings) + "".join(full_text_parts) # Добавляем предупреждения в начало

    base_name = os.path.splitext(book_info['filename'])[0]; out_fn = f"{base_name}_{target_language}_translated.txt"
    return Response(full_text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{out_fn}"})

@app.route('/api/models', methods=['GET'])
def api_get_models():
    # --- НОВОЕ: API тоже должно поддерживать режим администратора ---
    is_admin = request.args.get('all', 'false').lower() == 'true'
    models = get_models_list(show_all_models=is_admin)
    # --- КОНЕЦ НОВОГО ---
    if models is not None: return jsonify(models)
    else: return jsonify({"error": "Could not retrieve models"}), 500

@app.route('/download_epub/<book_id>', methods=['GET'])
def download_epub(book_id):
    book_info = get_book(book_id);
    if book_info is None: return "Book not found", 404
    target_language = book_info.get('target_language', session.get('target_language', 'russian'))
    update_overall_book_status(book_id); book_info = get_book(book_id)
    if book_info.get('status') not in ["complete", "complete_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409
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
@app.route('/alice', methods=['GET', 'POST'])
@app.route('/alice/', methods=['GET', 'POST'])  # Добавляем вариант со слешем
def alice_webhook():
    """ Обрабатывает запросы от Яндекс.Алисы, вызывая alice_handler. """
    if request.method == 'GET':
        return jsonify({
            "status": "ok",
            "service": "alice-webhook",
            "version": "1.0",
            "endpoints": {
                "/alice": "POST - основной вебхук для Алисы",
                "/alice/smart": "POST - умный вебхук с Gemini"
            }
        })
    
    request_data = request.json
    response_payload = alice_handler.handle_alice_request(request_data)
    return jsonify(response_payload)
# --- КОНЕЦ Маршрута для Алисы ---

# --- НОВЫЙ МАРШРУТ для "умной" Алисы ---
@app.route('/alice/smart', methods=['POST'])
@app.route('/alice/smart/', methods=['POST'])  # Добавляем вариант со слешем
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
             def run_workflow_in_context(book_id):
                 with app.app_context(): # 'app' is the global Flask app instance
                     current_app.logger.info(f"Запущен рабочий процесс для книги ID {book_id} в отдельном потоке.")
                     # Теперь start_book_workflow принимает app_instance и start_from_stage.
                     # Для новой загрузки start_from_stage всегда None.
                     workflow_processor.start_book_workflow(book_id, current_app._get_current_object(), None)
             threading.Thread(target=run_workflow_in_context, args=(book_id,)).start()
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

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ---

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
             # Исправлено: используем get_processed_sections_count_for_stage_workflow
             processed_sections_count_summarize = workflow_db_manager.get_processed_sections_count_for_stage_workflow(book_data['book_id'], 'summarize')

             # --- NEW: Get detailed stage statuses for the book ---
             detailed_stage_statuses = workflow_db_manager.get_book_stage_statuses_workflow(book_data['book_id'])
             # --- END NEW ---

             workflow_books.append({
                 'book_id': book_data['book_id'],
                 'filename': book_data['filename'], # Используем 'filename' для отображения
                 'status': book_data['current_workflow_status'],
                 'target_language': book_data.get('target_language'),
                 'total_sections': total_sections,
                 # Исправлено: передаем количество обработанных секций для суммаризации
                 'completed_sections_count': processed_sections_count_summarize,
                 # --- NEW: Pass detailed stage statuses to the template ---
                 'book_stage_statuses': detailed_stage_statuses
                 # --- END NEW ---
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

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ СКАЧИВАНИЯ СУММАРИЗАЦИИ WORKFLOW ---
@app.route('/workflow_download_summary/<book_id>', methods=['GET'])
def workflow_download_summary(book_id):
    print(f"Запрос на скачивание суммаризации для книги: {book_id}")
    # Убедимся, что вызываем функции работы с workflow DB
    # import workflow_db_manager # Убедимся, что импорт на верхнем уровне
    # import workflow_cache_manager # Убедимся, что импорт на верхнем уровне
    # import os # Убедимся, что импорт на верхнем уровне
    # import html # Убедимся, что импорт на верхнем уровне
    # from flask import Response # Убедимся, что импорт на верхнем уровне

    # 1. Получаем информацию о книге из Workflow DB
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if book_info is None:
        print(f"  [DownloadSummary] Книга с ID {book_id} не найдена в Workflow DB.")
        return "Book not found", 404

    # Проверяем статус этапа суммаризации книги.
    # Получаем статусы этапов книги
    book_stage_statuses = book_info.get('book_stage_statuses', {})
    summarize_stage_status = book_stage_statuses.get('summarize', {}).get('status')

    # Требуем статус 'completed' или 'completed_with_errors' для скачивания
    if summarize_stage_status not in ['completed', 'completed_with_errors']:
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

    # 3. Итерируемся по секциям и загружаем суммаризацию из workflow кеша
    for section_data in sections:
        section_id = section_data['section_id'] # Внутренний ID секции из БД
        section_epub_id = section_data['section_epub_id'] # Оригинальный EPUB ID секции
        # Используем заголовок из БД, если есть, иначе заглушку с порядковым номером
        section_title = section_data.get('section_title') or f'Section {section_data["order_in_book"] + 1}'
        # Используем переведенный заголовок, если есть, для отображения в файле
        display_title = section_data.get('translated_title') or section_title

        # Получаем статус этой секции для этапа суммаризации из данных секции
        section_stage_statuses = section_data.get('stage_statuses', {})
        summarize_section_status = section_stage_statuses.get('summarize', {}).get('status')
        section_error_message = section_stage_statuses.get('summarize', {}).get('error_message')

        summary_text = None
        # --- MODIFICATION: Only try to load cache if status is completed or completed_empty ---
        # Загружаем кэш только для успешно завершенных или пустых секций
        if summarize_section_status in ['completed', 'completed_empty']:
            try:
                 # workflow_cache_manager.load_section_stage_result вернет "" для completed_empty
                 summary_text = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
            except Exception as e:
                 print(f"  [DownloadSummary] ОШИБКА при загрузке суммаризации из кеша для секции {section_id} (EPUB ID: {section_epub_id}): {e}")
                 # В случае ошибки загрузки кэша для completed/completed_empty секции, помечаем как ошибку для вывода
                 summarize_section_status = 'error_cache_load'
                 section_error_message = f'Failed to load cache: {e}'


        # --- MODIFICATION: Include completed, completed_empty, and error sections ---
        # Включаем в файл только те секции, для которых есть результат (completed/completed_empty)
        # ИЛИ те, которые завершились с ошибкой.
        if summarize_section_status in ['completed', 'completed_empty'] and summary_text is not None:
             # Успешно загрузили результат (даже пустой) для завершенной/пустой секции
             escaped_title = html.escape(display_title)
             # Добавляем заголовок и содержимое
             # Если контент пустой и статус completed_empty, добавляем пометку "Раздел пуст" в заголовок
             header = f"\n\n==== {section_epub_id} - {escaped_title} (Статус: {summarize_section_status}) ====\n\n"
             if summarize_section_status == 'completed_empty' and (summary_text is None or summary_text.strip() == ""):
                  header = f"\n\n==== {section_epub_id} - {escaped_title} (Статус: {summarize_section_status} - Раздел пуст) ====\n\n"

             full_summary_parts.append(header + (summary_text if summary_text is not None else "")) # Добавляем содержимое (даже пустое "")

        elif summarize_section_status and summarize_section_status.startswith('error_'):
             # Секция завершилась с ошибкой на этапе суммаризации
             escaped_title = html.escape(display_title)
             header = f"\n\n==== {section_epub_id} - {escaped_title} (Статус: {summarize_section_status}) ====\n\n"
             error_content = f"ОШИБКА: {section_error_message or 'Неизвестная ошибка'}"
             full_summary_parts.append(header + error_content)

        # Секции со статусами pending, queued, processing, skipped НЕ включаются в файл.


    if not full_summary_parts:
         # Это может произойти, если ни для одной секции не было статусов completed, completed_empty или error_
         print(f"  [DownloadSummary] Не удалось собрать текст суммаризации для книги {book_id} из кеша workflow. Нет готовых или ошибочных секций.")
         # Если статус книги complete, но нет данных, возможно, проблема в кеше или логике.
         # Возвращаем ошибку, так как книга помечена как завершенная, но файл пустой.
         if summarize_stage_status in ['completed', 'completed_with_errors']:
              return "Could not retrieve any completed summary text from workflow cache.", 500
         else:
              # Книга не завершена, поэтому отсутствие готовых секций ожидаемо.
              return f"Summarization not complete (Status: {summarize_stage_status}). No completed sections to download.", 409


    # --- MODIFICATION: Combine parts directly without warnings ---
    full_summary_text = "".join(full_summary_parts) # Combine parts directly

    # 4. Формируем и отдаем файл
    # Имя файла для скачивания: [имя_оригинала_без_расширения]_summarized.txt
    # Берем оригинальное имя файла из book_info
    base_name = os.path.splitext(book_info.get('filename', 'summary_book'))[0]
    out_fn = f"{base_name}_summarized.txt"

    # Возвращаем текст как файл
    return Response(full_summary_text, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{out_fn}"})

# --- КОНЕЦ НОВОГО ЭНДPOЙНТА СКАЧИВАНИЯ ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ СКАЧИВАНИЯ АНАЛИЗА WORKFLOW ---
@app.route('/workflow_download_analysis/<book_id>', methods=['GET'])
def workflow_download_analysis(book_id):
    print(f"Запрос на скачивание анализа для книги: {book_id}")

    book_info = workflow_db_manager.get_book_workflow(book_id)
    if book_info is None:
        print(f"  [DownloadAnalysis] Книга с ID {book_id} не найдена в Workflow DB.")
        return "Book not found", 404

    book_stage_statuses = book_info.get('book_stage_statuses', {})
    analysis_stage_status = book_stage_statuses.get('analyze', {}).get('status')

    if analysis_stage_status not in ['completed', 'completed_with_errors']:
         print(f"  [DownloadAnalysis] Этап анализа для книги {book_id} не завершен. Статус: {analysis_stage_status}")
         return f"Analysis not complete (Status: {analysis_stage_status}).", 409

    # --- НОВОЕ: Загружаем результат анализа книги целиком ---
    analysis_result = None
    try:
        analysis_result = workflow_cache_manager.load_book_stage_result(book_id, 'analyze')
        if analysis_result is None or not analysis_result.strip():
            # Если результат пустой или только пробелы, возможно, анализ завершился как completed_empty
            # Или файл не найден / пустой, но статус в БД completed/completed_with_errors
            print(f"  [DownloadAnalysis] Результат анализа для книги {book_id} пуст или не найден в кеше.")
            if analysis_stage_status == 'completed_empty':
                 # Это ожидаемое состояние для completed_empty
                 analysis_result = "Анализ не проводился, т.к. собранный текст суммаризации пуст." # Или другое сообщение
            else:
                 # Неожиданно пустой результат для completed/completed_with_errors
                 print(f"  [DownloadAnalysis] ОШИБКА: Этап анализа книги {book_id} завершен со статусом {analysis_stage_status}, но результат в кеше пуст или отсутствует.")
                 return "Analysis result is empty or missing in cache.", 500

    except Exception as e:
        print(f"  [DownloadAnalysis] ОШИБКА при загрузке результата анализа книги {book_id} из кеша: {e}")
        traceback.print_exc()
        return "Error loading analysis result from cache.", 500

    # --- КОНЕЦ НОВОГО БЛОКА ---

    # Имя файла для скачивания: [имя_оригинала_без_расширения]_analyzed.txt
    base_name = os.path.splitext(book_info.get('filename', 'analysis_book'))[0]
    out_fn = f"{base_name}_analyzed.txt"

    # Возвращаем текст как файл
    return Response(analysis_result, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{out_fn}"})
# --- КОНЕЦ НОВОГО ЭНДПОЙНТА СКАЧИВАНИЯ АНАЛИЗА ---

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

    # --- НОВОЕ: Получаем конфигурацию этапов для добавления is_per_section в ответ API ---
    stages_config = workflow_db_manager.get_all_stages_ordered_workflow()
    stages_config_map = {stage['stage_name']: stage for stage in stages_config}
    # --- КОНЕЦ НОВОГО ---

    # --- ИСПРАВЛЕНИЕ: Рассчитываем общее количество секций, запрашивая из БД ---
    total_sections = workflow_db_manager.get_section_count_for_book_workflow(book_id)
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    # Формируем ответ
    response_data = {
        "book_id": book_info.get('book_id'),
        "filename": book_info.get('filename'),
        "target_language": book_info.get('target_language'), # Add target_language
        "current_workflow_status": book_info.get('current_workflow_status'),
        # --- ИСПОЛЬЗУЕМ НОВЫЕ ДАННЫЕ С КОНФИГУРАЦИЕЙ НИЖЕ ---
        "book_stage_statuses": {}, # Инициализируем пустым, заполним с is_per_section
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        "total_sections_count": total_sections, # <-- Убедитесь, что здесь используется total_sections
        "sections_status_summary": {} # Сводка статусов секций по этапам
    }

    # --- НОВОЕ: Добавляем данные этапов книги и is_per_section в ответ ---
    for stage_name, stage_data in book_stage_statuses.items():
         response_data['book_stage_statuses'][stage_name] = stage_data # Копируем существующие данные
         # Добавляем is_per_section из конфигурации, если найдено
         config = stages_config_map.get(stage_name)
         if config:
              response_data['book_stage_statuses'][stage_name]['is_per_section'] = config['is_per_section']
         else:
              # Если этап найден в book_stage_statuses, но нет в stages_config
              # Помечаем как False, чтобы не пытаться посчитать секции для неизвестных этапов.
              response_data['book_stage_statuses'][stage_name]['is_per_section'] = False
    # --- КОНЕЦ НОВОГО ---

    # Подсчитаем количество секций для каждого статуса на каждом этапе
    # Берем все этапы из конфигурации + любые другие этапы, найденные в секциях.
    # --- ИЗМЕНЕНИЕ: Формируем sections_status_summary только для ПОСЕКЦИОННЫХ этапов ---
    sections_status_summary = {} # Инициализируем или очищаем этот словарь

    # Получаем конфигурацию этапов (уже должно быть получено выше)
    # stages_config = workflow_db_manager.get_all_stages_ordered_workflow()
    # stages_config_map = {stage['stage_name']: stage for stage in stages_config} # уже получено выше

    # Итерируемся по сконфигурированным этапам и добавляем сводку только для посекционных
    for stage_name, stage_config in stages_config_map.items():
        if stage_config.get('is_per_section'):
            # Инициализируем сводку для этого посекционного этапа
            sections_status_summary[stage_name] = {
                'total': len(sections), # Общее количество секций книги (корректно для посекционного этапа)
                'completed': 0,
                'completed_empty': 0,
                'processing': 0,
                'queued': 0,
                'error': 0,
                'skipped': 0,
                'pending': 0,
                'cached': 0, # Для этапа перевода
                # TODO: Учесть custom error statuses, если есть
            }
            # Теперь проходим по всем секциям книги и считаем статусы для ТЕКУЩЕГО посекционного этапа
            for section in sections:
                section_stage_status = section.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending')
                if section_stage_status in sections_status_summary[stage_name]:
                    sections_status_summary[stage_name][section_stage_status] += 1
                else:
                    # Если встречен неизвестный статус у секции для этого этапа
                    print(f"  [WorkflowStatusAPI] Предупреждение: Неизвестный статус секции '{section.get('section_epub_id')}' для посекционного этапа '{stage_name}': '{section_stage_status}'")
                    # Пока игнорируем или можно добавить в 'error' или отдельную категорию


    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- ИСПРАВЛЕНИЕ: Присваиваем локальную переменную sections_status_summary в response_data ---
    response_data['sections_status_summary'] = sections_status_summary
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    # TODO: Включить в ответ детальные статусы каждой секции, если нужно для фронтенда
    # response_data['sections_details'] = sections # Осторожно: может быть большой объем данных!

    # --- NEW: Determine the current active stage and add to book_info ---
    current_active_stage_name = None
    # Get all stages in order
    stages_ordered = workflow_db_manager.get_all_stages_ordered_workflow()

    book_stage_statuses = book_info.get('book_stage_statuses', {})

    # Find the first stage with 'processing' or 'queued' status
    for stage in stages_ordered:
        stage_name = stage['stage_name']
        status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
        if status in ['processing', 'queued']:
            current_active_stage_name = stage_name
            break # Found processing or queued, stop search

    # If no processing/queued stage found, find the first 'pending' stage
    if current_active_stage_name is None:
        for stage in stages_ordered:
            stage_name = stage['stage_name']
            status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
            if status == 'pending':
                current_active_stage_name = stage_name
                break # Found pending, stop search

    # If still no active stage found (all completed/error), take the last stage
    if current_active_stage_name is None and stages_ordered:
        current_active_stage_name = stages_ordered[-1]['stage_name'] # Take the name of the last stage

    # Add the determined active stage name to the book_info dictionary
    # ИСПРАВЛЕНИЕ: Добавляем поле в response_data, а не в book_info
    response_data['current_stage_name'] = current_active_stage_name
    # --- END NEW ---

    # Возвращаем статус книги как JSON
    return jsonify(response_data), 200 # <-- Эта строка

# --- КОНЕЦ НОВОГО ЭНДПОЙНТ СТАТУСА ---

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

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА УДАЛЕНИЯ ---

@app.route('/workflow_start_existing_book/<book_id>', methods=['POST'])
def workflow_start_existing_book(book_id):
    current_app.logger.info(f"Запрос на запуск рабочего процесса для существующей книги: {book_id}")
    try:
        start_from_stage = request.json.get('start_from_stage')
        current_app.logger.info(f"Получен start_from_stage: {start_from_stage} для книги {book_id}")

        # --- Гарантируем app context через глобальный app ---
        from app import app as global_app
        def run_workflow_in_context(book_id, start_from_stage):
            with global_app.app_context():
                workflow_processor.start_book_workflow(book_id, global_app, start_from_stage)

        executor.submit(run_workflow_in_context, book_id, start_from_stage)
        
        return jsonify({'status': 'success', 'message': 'Workflow started in background'}), 200
    except Exception as e:
        current_app.logger.error(f"Ошибка при запуске рабочего процесса для книги {book_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ---

# --- НОВЫЕ МАРШРУТЫ ДЛЯ АНАЛИЗА ВИДЕО ---

@app.route('/video-analysis', methods=['GET'])
def video_analysis_page():
    """Отображает страницу для анализа видео."""
    return render_template('video_analysis.html')

@app.route('/api/analyze-video', methods=['POST'])
def api_analyze_video():
    """API эндпойнт для анализа видео."""
    try:
        data = request.get_json()
        if not data or 'video_url' not in data:
            return jsonify({'error': 'Не указан URL видео'}), 400
        
        video_url = data['video_url'].strip()
        if not video_url:
            return jsonify({'error': 'URL видео не может быть пустым'}), 400
        
        print(f"[VideoAnalysis] Запрос на анализ видео: {video_url}")
        
        # Создаем экземпляр анализатора
        try:
            analyzer = video_analyzer.VideoAnalyzer()
        except ValueError as e:
            return jsonify({'error': f'Ошибка инициализации: {str(e)}'}), 500
        
        # Выполняем анализ
        result = analyzer.analyze_video(video_url)
        
        if result.get('error'):
            return jsonify({
                'success': False,
                'error': result['error']
            }), 500
        else:
            return jsonify({
                'success': True,
                'sharing_url': result['sharing_url'],
                'extracted_text_length': len(result['extracted_text']) if result['extracted_text'] else 0,
                'analysis': result['analysis']
            }), 200
            
    except Exception as e:
        print(f"[VideoAnalysis] Непредвиденная ошибка: {e}")
        return jsonify({'error': f'Непредвиденная ошибка: {str(e)}'}), 500

# --- КОНЕЦ МАРШРУТОВ ДЛЯ АНАЛИЗА ВИДЕО ---

# --- НОВЫЕ МАРШРУТЫ ДЛЯ TOPTUBE ---

@app.route('/toptube', methods=['GET'])
def toptube_page():
    """Отображает страницу с проанализированными видео."""
    return render_template('toptube.html')

@app.route('/api/toptube/videos', methods=['GET'])
def api_get_toptube_videos():
    """API эндпойнт для получения списка видео."""
    try:
        import video_db
        
        # Получаем параметры
        status = request.args.get('status', 'analyzed')  # По умолчанию показываем проанализированные
        limit = int(request.args.get('limit', 50))
        
        if status == 'analyzed':
            videos = video_db.get_analyzed_videos(limit=limit)
        elif status == 'all':
            # Для 'all' получаем все видео без фильтра по статусу
            videos = video_db.get_all_videos(limit=limit)
        else:
            videos = video_db.get_videos_by_status(status, limit=limit)
        
        print(f"[TopTube API] Запрошено видео со статусом '{status}', получено {len(videos)} видео")
        
        return jsonify({
            'success': True,
            'videos': videos,
            'count': len(videos)
        }), 200
        
    except Exception as e:
        print(f"[TopTube API] Ошибка получения видео: {e}")
        return jsonify({'error': f'Ошибка получения видео: {str(e)}'}), 500

@app.route('/api/toptube/stats', methods=['GET'])
def api_get_toptube_stats():
    """API эндпойнт для получения статистики."""
    try:
        import toptube10
        
        manager = toptube10.get_manager()
        stats = manager.get_stats()
        
        return jsonify({
            'success': True,
            'stats': stats
        }), 200
        
    except Exception as e:
        print(f"[TopTube API] Ошибка получения статистики: {e}")
        return jsonify({'error': f'Ошибка получения статистики: {str(e)}'}), 500

@app.route('/api/toptube/collect', methods=['POST'])
def api_collect_videos():
    """API эндпойнт для запуска сбора видео."""
    try:
        import toptube10
        
        # Запускаем сбор в фоне
        executor.submit(toptube10.collect_videos_task)
        
        return jsonify({
            'success': True,
            'message': 'Сбор видео запущен в фоне'
        }), 202
        
    except Exception as e:
        print(f"[TopTube API] Ошибка запуска сбора: {e}")
        return jsonify({'error': f'Ошибка запуска сбора: {str(e)}'}), 500

@app.route('/api/toptube/analyze', methods=['POST'])
def api_analyze_next_video():
    """API эндпойнт для анализа всех необработанных видео."""
    try:
        import toptube10
        
        # Запускаем анализ в фоне
        executor.submit(toptube10.analyze_next_video_task)
        
        return jsonify({
            'success': True,
            'message': 'Анализ всех необработанных видео запущен в фоне'
        }), 202
        
    except Exception as e:
        print(f"[TopTube API] Ошибка запуска анализа: {e}")
        return jsonify({'error': f'Ошибка запуска анализа: {str(e)}'}), 500

@app.route('/api/toptube/full-workflow', methods=['POST'])
def api_full_workflow():
    """API эндпойнт для запуска полного рабочего процесса."""
    try:
        import toptube10
        
        # Запускаем полный рабочий процесс в фоне
        executor.submit(toptube10.full_workflow_task)
        
        return jsonify({
            'success': True,
            'message': 'Полный рабочий процесс запущен в фоне (сбор → анализ → очистка)'
        }), 202
        
    except Exception as e:
        print(f"[TopTube API] Ошибка запуска полного рабочего процесса: {e}")
        return jsonify({'error': f'Ошибка запуска полного рабочего процесса: {str(e)}'}), 500

@app.route('/api/toptube/reset-stuck', methods=['POST'])
def api_reset_stuck_videos():
    """API эндпойнт для сброса зависших видео."""
    try:
        import video_db
        
        # Сбрасываем зависшие видео
        reset_count = video_db.reset_stuck_videos(minutes_threshold=30)
        
        return jsonify({
            'success': True,
            'message': f'Сброшено {reset_count} зависших видео',
            'reset_count': reset_count
        }), 200
        
    except Exception as e:
        print(f"[TopTube API] Ошибка сброса зависших видео: {e}")
        return jsonify({'error': f'Ошибка сброса зависших видео: {str(e)}'}), 500

@app.route('/api/toptube/reset-errors', methods=['POST'])
def api_reset_error_videos():
    """API эндпойнт для сброса видео с ошибками."""
    try:
        import video_db
        
        # Сбрасываем видео с ошибками
        reset_count = video_db.reset_error_videos()
        
        return jsonify({
            'success': True,
            'message': f'Сброшено {reset_count} видео с ошибками для повторного анализа',
            'reset_count': reset_count
        }), 200
        
    except Exception as e:
        print(f"[TopTube API] Ошибка сброса видео с ошибками: {e}")
        return jsonify({'error': f'Ошибка сброса видео с ошибками: {str(e)}'}), 500

@app.route('/api/toptube/delete-non-analyzed', methods=['POST'])
def api_delete_non_analyzed_videos():
    """API эндпойнт для удаления всех видео со статусом, отличным от analyzed."""
    try:
        import video_db
        
        # Удаляем видео со статусом, отличным от analyzed
        deleted_count = video_db.delete_videos_by_status_not_analyzed()
        
        return jsonify({
            'success': True,
            'message': f'Удалено {deleted_count} неуспешных видео',
            'deleted_count': deleted_count
        }), 200
        
    except Exception as e:
        print(f"[TopTube API] Ошибка удаления неуспешных видео: {e}")
        return jsonify({'error': f'Ошибка удаления неуспешных видео: {str(e)}'}), 500

# --- КОНЕЦ МАРШРУТОВ ДЛЯ TOPTUBE ---

@app.route('/books', methods=['GET'])
def books():
    admin_param = request.args.get('admin')
    if admin_param == 'true':
        session['admin_mode'] = True
        print("Включен режим администратора (показ всех моделей).")
    elif admin_param == 'false':
        session.pop('admin_mode', None)
        print("Выключен режим администратора.")

    print("Загрузка страницы /books...")
    default_language = session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'meta-llama/llama-4-maverick:free')
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models:
        available_models = [
            {
                'name': 'meta-llama/llama-4-maverick:free',
                'display_name': 'Meta Llama 4 Maverick (Free)',
                'description': 'Default Meta Llama model'
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

    resp = make_response(render_template('book_list.html', uploaded_books=uploaded_books, default_language=default_language, selected_model=selected_model, available_models=available_models, is_admin_mode=is_admin_mode))
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    resp.headers['Content-Security-Policy'] = csp_policy
    return resp

# --- Запуск приложения ---
if __name__ == '__main__':
    print("Запуск Flask приложения...")
    # use_reloader=False рекомендуется при использовании APScheduler в режиме отладки,
    # чтобы избежать двойного запуска планировщика. Но можно попробовать и без него.
    try:
        configure_api() # Проверка ключей API
        load_models_on_startup() # <-- ДОБАВЛЯЕМ ЭТОТ ВЫЗОВ

        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except ValueError as e:
        print(f"Ошибка конфигурации API: {e}")
        # Возможно, стоит явно выйти из приложения или как-то иначе сообщить об ошибке
        exit(1)

# --- END OF FILE app.py ---