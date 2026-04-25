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
import datetime
from datetime import timedelta
import logging

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
import video_chat_handler
from workflow_model_config import get_model_for_operation, DEFAULT_MODEL
import toptube10
import video_db
import football
from telegram_notifier import telegram_notifier

# Импорт Telegram бота
try:
    from telegram_bot_handler import TelegramBotHandler
    TELEGRAM_BOT_AVAILABLE = True
    print("[App] Telegram бот доступен")
except ImportError:
    TELEGRAM_BOT_AVAILABLE = False
    print("[App] Telegram бот недоступен (модуль не найден)")

# --- Настройки ---
from config import UPLOADS_DIR, CACHE_DIR, FULL_TRANSLATION_DIR, MEDIA_DIR, MAX_CONTENT_LENGTH

UPLOAD_FOLDER = str(UPLOADS_DIR)
ALLOWED_EXTENSIONS = {'epub'}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
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
     # Инициализируем БД для футбольных матчей
     football.init_football_db()
     # Возобновляем незавершенные воркфлоу
     workflow_processor.resume_all_workflows(app)

# --- Настраиваем API перевода ---
try:
    configure_api()
except ValueError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА НАСТРОЙКИ API: {e}. Перевод не будет работать.")

# --- Управление фоновыми задачами ---
executor = ThreadPoolExecutor(max_workers=int(os.getenv("MAX_TRANSLATION_WORKERS", 3)))
active_tasks = {} # Хранилище статусов активных задач {task_id: {"status": ..., "book_id": ..., "section_id": ...}}
analyzing_risk_fixtures = set()  # Множество fixture_id, для которых идет анализ риска
analyzing_risk_lock = threading.Lock()  # Блокировка для предотвращения race condition
active_parlay_requests = set()  # Блокируем повторные запросы на составление экспресса
active_parlay_lock = threading.Lock()

# --- ИЗМЕНЕНИЕ: Передаем executor в alice_handler ---
alice_handler.initialize_alice_handler(executor)
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# --- Фильтрация шумных логов запросов (например, /api/football/check-subscription) ---
class _SuppressPathFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = str(record.getMessage())
            # Игнорируем частые фоновые пинги подписки
            if "GET /api/football/check-subscription" in msg:
                return False
        except Exception:
            pass
        return True

_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.addFilter(_SuppressPathFilter())

# --- ИЗМЕНЕНИЕ: Настройка и запуск APScheduler ---
scheduler = BackgroundScheduler(daemon=True)

# Проверяем, что мы на fly.io
is_fly_io = os.getenv("FLY_APP_NAME") is not None

# Модель для перевода новостей, настраиваемая через переменные окружения
NEWS_MODEL_NAME = os.getenv("NEWS_TRANSLATION_MODEL", DEFAULT_MODEL)

# --- ФОНОВЫЕ ЗАДАЧИ ТОЛЬКО НА FLY.IO ---
# ВРЕМЕННО ВКЛЮЧЕНО ДЛЯ ЛОКАЛЬНОГО ТЕСТИРОВАНИЯ
if True:  # is_fly_io:
    print("[Scheduler] 🚀 Запуск на fly.io - добавляем фоновые задачи")
    
    # Добавляем задачу обновления кеша новостей, выполняться каждый час
    # scheduler.add_job(
    #     alice_handler.update_translated_news_cache,
    #     'interval',
    #     hours=1,
    #     args=[NEWS_MODEL_NAME],   # Передаем имя модели в задачу
    #     id='bbc_news_updater_job', # Даем ID для управления
    #     replace_existing=True     # Заменять задачу, если она уже есть с таким ID
    # )
    # print("[Scheduler] ✅ Задание 'bbc_news_updater_job' добавлено (обновление новостей каждый час)")

    # --- ЗАДАНИЕ для обновления локаций персон ---
    if hasattr(location_finder, 'update_locations_for_predefined_persons'):
        scheduler.add_job(
            location_finder.update_locations_for_predefined_persons,
            trigger='interval', # Тип триггера - интервал
            hours=1,            # Выполнять каждый час
            id='person_locations_updater_job', # Уникальный ID задания
            replace_existing=True, # Заменять существующее задание с таким ID
            misfire_grace_time=600 # Секунд, на которые может опоздать выполнение (10 минут)
        )
        print("[Scheduler] ✅ Задание 'person_locations_updater_job' добавлено (обновление локаций персон каждый час)")
    else:
        print("[Scheduler] ❌ Функция 'update_locations_for_predefined_persons' не найдена в location_finder")

    # --- ЗАДАНИЯ ДЛЯ TOPTUBE (анализ видео) ---
    scheduler.add_job(
        toptube10.full_workflow_task,
        trigger='interval',
        hours=2,  # Полный рабочий процесс каждые 2 часа
        id='toptube_full_workflow_job',
        replace_existing=True,
        misfire_grace_time=1800  # 30 минут grace time для длительного процесса
    )
    print("[Scheduler] ✅ Задание 'toptube_full_workflow_job' добавлено (анализ видео каждые 2 часа)")

    # --- ЗАДАНИЕ ДЛЯ ОЧИСТКИ ИСТЕКШИХ СЕССИЙ ---
    scheduler.add_job(
        workflow_db_manager.delete_expired_sessions,
        trigger='interval',
        hours=6,  # Очистка каждые 6 часов
        id='cleanup_expired_sessions_job',
        replace_existing=True,
        misfire_grace_time=600  # 10 минут grace time
    )
    print("[Scheduler] ✅ Задание 'cleanup_expired_sessions_job' добавлено (очистка истекших сессий каждые 6 часов)")
    
    # --- ЗАДАНИЯ ДЛЯ ФУТБОЛА ---
    scheduler.add_job(
        football.collect_tomorrow_matches_task,
        trigger='interval',
        days=1,  # Каждый день
        id='collect_football_matches_job',
        replace_existing=True,
        misfire_grace_time=1440  # 1 день grace time (в минутах)
    )
    print("[Scheduler] ✅ Задание 'collect_football_matches_job' добавлено (сбор матчей каждый день)")

    scheduler.add_job(
        football.check_matches_60min_task,
        trigger='interval',
        minutes=3,  # Каждые 3 минуты - статус и 60-я минута
        id='check_football_matches_60min_job',
        replace_existing=True,
        misfire_grace_time=180,  # 3 минуты grace time
        next_run_time=datetime.datetime.now() + timedelta(minutes=3)  # Запуск через 3 минуты после старта
    )
    print("[Scheduler] ✅ Задание 'check_football_matches_60min_job' добавлено (статус/60-я минута каждые 3 минуты)")

    # Отдельная задача для финального счета - каждые 5 минут
    scheduler.add_job(
        football.check_matches_and_collect_task,
        trigger='interval',
        minutes=5,  # Каждые 5 минут - финальный счет и прочее
        id='check_football_matches_final_job',
        replace_existing=True,
        misfire_grace_time=300  # 5 минут grace time
    )
    print("[Scheduler] ✅ Задание 'check_football_matches_final_job' добавлено (финальный счет каждые 5 минут)")

    # Обновление текущих счетов из TheSportsDB каждые 2 минуты (только для in_progress)
    scheduler.add_job(
        football.thesportsdb_update_scores_task,
        trigger='interval',
        minutes=2,
        id='thesportsdb_scores_job',
        replace_existing=True,
        misfire_grace_time=120
    )
    print("[Scheduler] ✅ Задание 'thesportsdb_scores_job' добавлено (обновление счетов из TheSportsDB каждые 2 минуты)")

else:
    print("[Scheduler] 🏠 Локальный запуск - фоновые задачи отключены")
    print("[Scheduler] 📍 Поиск локаций и анализ видео доступны только через API")


try:
    scheduler.start()
    print("Планировщик APScheduler запущен.")
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
@app.route('/translate', methods=['GET'])
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
    selected_model = session.get('model_name', DEFAULT_MODEL)
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    
    # Получаем список моделей, учитывая режим администратора
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models:
        available_models = [
            {
                'name': DEFAULT_MODEL,
                'display_name': DEFAULT_MODEL,
                'description': DEFAULT_MODEL
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
             toc_model = session.get('model_name', DEFAULT_MODEL)
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
            try: os.remove(filepath)
            except OSError as e_del: print(f"  Не удалось удалить файл {filepath}: {e_del}")
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
        return "Ошибка сервера при обработке файла.", 500

@app.route('/book/<book_id>', methods=['GET'])
def view_book(book_id):
    print(f"Запрос страницы книги: {book_id}")
    book_info = get_book(book_id)
    if book_info is None: print(f"  Книга {book_id} не найдена.\n"); return "Книга не найдена.", 404

    book_db_language = book_info.get('target_language')
    target_language = book_db_language or request.args.get('lang') or session.get('target_language', 'russian')

    selected_model = session.get('model_name', 'openrouter/free')

    selected_operation = session.get('operation_type', 'translate')

    # --- Сохраняем определенный язык в сессию для последующих действий ---
    session['target_language'] = target_language
    session['operation_type'] = selected_operation # Save operation type to session
    session['model_name'] = selected_model # Save selected model to session


    print(f"  Параметры для отображения: lang='{target_language}', model='{selected_model}'.\n")
    # Получаем список моделей, учитывая режим администратора
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models: available_models = list(set([selected_model, 'openrouter/free'])); print("  WARN: Не удалось получить список моделей.\n")
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
    print(f"\n[Перевод] >>> Запрос на перевод секции: {book_id}/{section_id}")
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
        model_name = data.get('model_name', session.get('model_name', 'openrouter/free'))
        operation_type = data.get('operation_type', 'translate') # Get operation type from JSON, default to 'translate'
        print(f"  [DEBUG] 3.1. Параметры получены: lang={target_language}, model={model_name}, operation={operation_type}")
    except Exception as e:
        print(f"  [DEBUG] 3.2. ОШИБКА получения параметров: {e}")
        return jsonify({"error": f"Invalid JSON payload: {e}"}), 400

    session['target_language'] = target_language; session['model_name'] = model_name
    session['operation_type'] = operation_type # Save operation type to session
    
    print(f"  [LOG] Используется модель: '{model_name}' (язык: {target_language})")

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
    print(f"\n[Перевод] >>> ЗАПУСК ПЕРЕВОДА ВСЕХ СЕКЦИЙ для книги: {book_id}")
    book_info = get_book(book_id)
    if book_info is None: return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath")
    if not filepath or not os.path.exists(filepath): return jsonify({"error": "EPUB file not found"}), 404
    try:
        data = request.get_json();
        if not data: raise ValueError("Missing JSON")
        target_language = data.get('target_language', session.get('target_language', 'russian'))
        model_name = data.get('model_name', session.get('model_name', 'free'))
        operation_type = data.get('operation_type', 'translate') # Get operation type from JSON, default to 'translate'
    except Exception as e: print(f"  Ошибка получения параметров: {e}"); return jsonify({"error": f"Invalid JSON payload: {e}"}), 400
    session['target_language'] = target_language; session['model_name'] = model_name
    session['operation_type'] = operation_type # Save operation type to session
    
    print(f"  [LOG] Массовый перевод запущен. Модель: '{model_name}', Язык: {target_language}")
    
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

@app.route('/trump', methods=['GET'])
def find_locations_form_page():
    print(f"{APP_PRINT_PREFIX} Запрос страницы /trump (GET)")
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
        print(f"{APP_PRINT_PREFIX}  Вызов location_finder.find_persons_locations_for_user с {valid_names}, test_mode={test_mode_flag}...")
        # Используем функцию для пользовательских запросов (кэш-приоритет)
        locations_map_with_coords = location_finder.find_persons_locations_for_user(valid_names, test_mode=test_mode_flag)

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

@app.route('/api/locations/clear-cache', methods=['POST'])
def api_clear_location_cache():
    """Очищает кэш локаций."""
    print(f"{APP_PRINT_PREFIX} Запрос на очистку кэша локаций")
    try:
        from db_manager import clear_location_cache
        if clear_location_cache():
            return jsonify({"status": "success", "message": "Кэш локаций очищен"}), 200
        else:
            return jsonify({"status": "error", "message": "Ошибка при очистке кэша"}), 500
    except Exception as e:
        print(f"{APP_PRINT_PREFIX} Ошибка при очистке кэша: {e}")
        return jsonify({"status": "error", "message": f"Ошибка сервера: {e}"}), 500

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
    
    # Читаем параметр admin из формы
    admin = request.form.get('admin') == 'true'
    print(f"Admin mode in workflow_upload: {admin}")

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
             print(f"Книга с ID {book_id} уже существует в Workflow DB.")
             # Для /workflow всегда возвращаем JSON, не делаем редирект
             return jsonify({"status": "error", "message": f"Книга с ID {book_id} уже существует."}), 200

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

         # ГЕНЕРИРУЕМ ТОКЕН ДО СОЗДАНИЯ КНИГИ
        access_token = workflow_db_manager.generate_access_token()
        # Создаем запись о книге в новой БД сразу с access_token и admin_mode
        if workflow_db_manager.create_book_workflow(book_id, original_filename, filepath, toc, target_language, access_token, admin_mode=admin):
             print(f"  Книга '{book_id}' сохранена в Workflow DB (admin={admin}).")

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

             # --- Запускаем рабочий процесс для книги через очередь ---
             workflow_processor.workflow_queue_manager.add_book_to_queue(book_id, app, admin=admin)
             print(f"  Книга ID {book_id} поставлена в очередь обработки.")

             # --- СОЗДАЕМ СЕССИЮ ПОЛЬЗОВАТЕЛЯ ---
             session_id = workflow_db_manager.create_user_session(access_token)
             if session_id:
                 print(f"[WorkflowUpload] Создана сессия пользователя: {session_id}")
             else:
                 print(f"[WorkflowUpload] ОШИБКА: Не удалось создать сессию для токена {access_token}")
                 session_id = None

             # --- ВОЗВРАЩАЕМ JSON ДЛЯ WORKFLOW DASHBOARD ---
             # Для /workflow страницы всегда возвращаем JSON, не делаем редирект
             response_data = {
                 "status": "success",
                 "message": "Книга загружена и запущен рабочий процесс.",
                 "book_id": book_id,
                 "filename": original_filename,
                 "total_sections_count": sec_created_count,
                 "access_token": access_token
             }
             if session_id:
                 response_data["session_id"] = session_id
             response = jsonify(response_data)
             if session_id:
                 response.set_cookie(
                     'user_session',
                     session_id,
                     max_age=24*60*60,
                     httponly=True,
                     secure=False,
                     samesite='Lax'
                 )
             return response, 200
        else:
             # Если не удалось создать запись книги в БД, удаляем файл
             print(f"ОШИБКА: Не удалось сохранить книгу '{book_id}' в Workflow DB! Удаляем файл.")
             if filepath and os.path.exists(filepath):
                 try: os.remove(filepath)
                 except OSError as e: print(f"  Не удалось удалить файл {filepath} после ошибки БД: {e}")
             return jsonify({"status": "error", "message": "Ошибка сервера при сохранении информации о книге в Workflow DB."}), 500

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

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ЗАГРУЗКИ С ПОЛЬЗОВАТЕЛЬСКОЙ СТРАНИЦЫ (ВОЗВРАЩАЕТ РЕДИРЕКТ) ---
@app.route('/user_upload', methods=['POST'])
def user_upload_file():
    """ Обрабатывает загрузку EPUB с пользовательской страницы, возвращает редирект. """
    print("Запрос на загрузку файла с пользовательской страницы.")
    if 'epub_file' not in request.files: return "Файл не найден", 400
    file = request.files['epub_file'];
    if file.filename == '': return "Файл не выбран", 400
    if not allowed_file(file.filename): return "Ошибка: Недопустимый тип файла.", 400

    # Целевой язык пока берем из формы или сессии
    form_language = request.form.get('target_language')
    target_language = form_language or session.get('target_language', 'russian')
    
    # Читаем параметр admin из формы
    admin = request.form.get('admin') == 'true'
    print(f"Admin mode in user_upload: {admin}")

    original_filename = secure_filename(file.filename)
    temp_dir = app.config['UPLOAD_FOLDER']
    temp_filepath = None
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
             print(f"Книга с ID {book_id} уже существует в Workflow DB.")
             return redirect(url_for('translate_page', access_token=workflow_db_manager.get_book_workflow(book_id).get('access_token')))

        # Если книга новая, сохраняем файл с уникальным именем
        unique_filename = f"{book_id}.epub"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        if os.path.exists(filepath):
             print(f"Предупреждение: Файл книги {book_id} уже существует при новой загрузке. Удаляем старый.")
             try: os.remove(filepath)
             except OSError as e: print(f"Ошибка при удалении старого файла {filepath}: {e}")

        os.rename(temp_filepath, filepath); print(f"Файл перемещен в хранилище workflow: {filepath}"); temp_filepath = None

        # Парсим структуру EPUB и оглавление
        section_ids, id_to_href_map = epub_parser.get_epub_structure(filepath)
        if section_ids is None: raise ValueError("Не удалось получить структуру EPUB для workflow.")
        toc = epub_parser.get_epub_toc(filepath, id_to_href_map) or []

        # Подготовка данных секций
        sections_data_for_db = []
        order_in_book = 0
        href_to_title_map = {item['href']: item.get('title') for item in toc if item.get('href')}

        for section_id_epub in section_ids:
             section_href = id_to_href_map.get(section_id_epub)
             section_title_original = href_to_title_map.get(section_href) if section_href else None
             if not section_title_original:
                 section_title_original = section_id_epub
                 print(f"Предупреждение: Не найдено название TOC для секции {section_id_epub}. Используем ID.")

             sections_data_for_db.append({
                 'section_epub_id': section_id_epub,
                 'section_title': section_title_original,
                 'translated_title': None,
                 'order_in_book': order_in_book
             })
             order_in_book += 1

        # Генерируем токен и создаем книгу (с сохранением admin_mode)
        access_token = workflow_db_manager.generate_access_token()
        if workflow_db_manager.create_book_workflow(book_id, original_filename, filepath, toc, target_language, access_token, admin_mode=admin):
             print(f"  Книга '{book_id}' сохранена в Workflow DB (admin={admin}).")

             workflow_db_manager._initialize_book_stage_statuses(book_id)

             sec_created_count = 0
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

             # Запускаем рабочий процесс через очередь
             workflow_processor.workflow_queue_manager.add_book_to_queue(book_id, app, admin=admin)
             print(f"  Книга ID {book_id} поставлена в очередь обработки.")

             # Создаем сессию пользователя
             session_id = workflow_db_manager.create_user_session(access_token)
             if session_id:
                 print(f"[UserUpload] Создана сессия пользователя: {session_id}")
             else:
                 print(f"[UserUpload] ОШИБКА: Не удалось создать сессию для токена {access_token}")
                 session_id = None

             # ВОЗВРАЩАЕМ РЕДИРЕКТ ДЛЯ ПОЛЬЗОВАТЕЛЬСКОЙ СТРАНИЦЫ
             redirect_url = url_for('translate_page', access_token=access_token)
             if admin:
                 redirect_url += '?admin=true'
             response = redirect(redirect_url)
             if session_id:
                 response.set_cookie(
                     'user_session',
                     session_id,
                     max_age=24*60*60,
                     httponly=True,
                     secure=False,
                     samesite='Lax'
                 )
             return response
        else:
            # Если не удалось создать запись книги в БД, удаляем файл
            print(f"ОШИБКА: Не удалось сохранить книгу '{book_id}' в Workflow DB! Удаляем файл.")
            if filepath and os.path.exists(filepath):
                try: os.remove(filepath)
                except OSError as e: print(f"  Не удалось удалить файл {filepath} после ошибки БД: {e}")
            return "Ошибка сервера при сохранении информации о книге в Workflow DB.", 500

    except Exception as e:
        print(f"ОШИБКА при обработке загрузки для пользователя: {e}"); traceback.print_exc()
        # Удаляем временный и сохраненный файлы в случае любой ошибки
        if temp_filepath and os.path.exists(temp_filepath):
            try: os.remove(temp_filepath)
            except OSError as e_del: print(f"  Не удалось удалить временный файл {temp_filepath} после ошибки: {e_del}")
        if filepath and os.path.exists(filepath):
            try: os.remove(filepath)
            except OSError as e_del: print(f"  Не удалось удалить файл {filepath} после ошибки: {e_del}")

        return "Ошибка сервера при обработке файла для пользователя.", 500

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ДЛЯ ПОЛЬЗОВАТЕЛЬСКОЙ СТРАНИЦЫ ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ОТОБРАЖЕНИЯ СПИСКА КНИГ В РАБОЧЕМ ПРОЦЕССЕ ---
@app.route('/workflow', methods=['GET'])
def workflow_index():
    """ Отображает страницу со списком книг в новом рабочем процессе. """
    print("Загрузка страницы списка книг рабочего процесса...")
    
    # Проверяем параметр admin (включая сессию)
    admin = request.args.get('admin') == 'true' or request.args.get('user') == 'admin' or session.get('admin_mode') == True

    workflow_books = []
    try:
        # Получаем список книг из новой базы данных
        db_books = workflow_db_manager.get_all_books_workflow()
        stages_config = workflow_db_manager.get_all_stages_ordered_workflow()
        is_per_section_map = {stage['stage_name']: stage.get('is_per_section', False) for stage in stages_config}
        per_section_stages = [stage['stage_name'] for stage in stages_config if stage.get('is_per_section')]
        for book_data in db_books:
             total_sections = workflow_db_manager.get_section_count_for_book_workflow(book_data['book_id'])

             # --- NEW: Get detailed stage statuses for the book ---
             detailed_stage_statuses = workflow_db_manager.get_book_stage_statuses_workflow(book_data['book_id'])
             for stage_name, stage_data in detailed_stage_statuses.items():
                 stage_data['is_per_section'] = is_per_section_map.get(stage_name, False)
             # --- END NEW ---

             # Для каждого посекционного этапа добавляем processed_sections_count_<stage_name>
             for stage_name in per_section_stages:
                 key = f'processed_sections_count_{stage_name}'
                 count = workflow_db_manager.get_processed_sections_count_for_stage_workflow(book_data['book_id'], stage_name)
                 book_data[key] = count

             workflow_books.append({
                 'book_id': book_data['book_id'],
                 'filename': book_data['filename'],
                 'status': book_data.get('current_workflow_status') or 'pending',
                 'target_language': book_data.get('target_language'),
                 'comic_status': book_data.get('comic_status'),
                 'total_sections': total_sections,
                 # Не передаём completed_sections_count и processed_sections_count_summarize!
                 'book_stage_statuses': detailed_stage_statuses,
                 # Передаём все processed_sections_count_<stage_name> из book_data
                 **{k: v for k, v in book_data.items() if k.startswith('processed_sections_count_')}
             })
        workflow_books.sort(key=lambda x: x['filename'].lower()) # Сортируем по имени файла
        print(f"  Найдено книг в Workflow DB: {len(workflow_books)}")
    except Exception as e:
        print(f"ОШИБКА при получении списка книг из Workflow DB: {e}")
        import traceback
        traceback.print_exc() # Логируем полный трейсбэк

    # TODO: Добавить передачу языка и модели по умолчанию, если они нужны на этой странице
    # TODO: Добавить логику получения списка доступных моделей, если форма загрузки будет использовать выбор модели

    # Получаем статус системы для админа
    system_status = None
    if admin:
        try:
            import shutil
            from config import WORKFLOW_DB_FILE
            data_path = "/data" if os.path.exists("/data") else "."
            usage = shutil.disk_usage(data_path)
            
            db_size = 0
            if WORKFLOW_DB_FILE.exists():
                db_size = WORKFLOW_DB_FILE.stat().st_size
                
            # Определяем размер очереди безопасно
            q_size = 0
            active_book_names = []
            try:
                # В Python ThreadPoolExecutor использует _work_queue (Queue)
                if hasattr(workflow_processor.workflow_queue_manager.executor, '_work_queue'):
                    q_size = workflow_processor.workflow_queue_manager.executor._work_queue.qsize()
                
                # Получаем названия книг для активных задач
                for b_id in workflow_processor.workflow_queue_manager.processing_books:
                    b_info = workflow_db_manager.get_book_workflow(b_id, include_sections=False)
                    if b_info:
                        active_book_names.append(b_info.get('filename', b_id))
                    else:
                        active_book_names.append(b_id)
            except:
                pass

            system_status = {
                "free_gb": round(usage.free / (1024**3), 2),
                "used_percent": round((usage.used / usage.total) * 100, 1),
                "db_size_mb": round(db_size / (1024**2), 2),
                "queue_size": q_size,
                "active_books": active_book_names,
                "total_active": len(active_book_names) # processing_books уже включает и очередь, и работу
            }
            print(f"[AdminStatus] Calculated status: {system_status}")
        except Exception as e:
            print(f"[AdminStatus] Error calculating system status: {e}")

    resp = make_response(render_template('workflow_index.html', workflow_books=workflow_books, admin=admin, system_status=system_status))
    # Наследуем CSP политику от основной страницы
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; font-src 'self' https://cdnjs.cloudflare.com; img-src 'self' data: https://unpkg.com;"
    resp.headers['Content-Security-Policy'] = csp_policy

    return resp

@app.route('/workflow/api/book/<book_id>/retranslate_section/<int:section_id>', methods=['POST'])
def workflow_api_retranslate_section(book_id, section_id):
    """
    Эндпойнт для перезапуска перевода конкретной секции.
    """
    if request.args.get('admin') != 'true' and request.args.get('user') != 'admin' and session.get('admin_mode') != True:
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
    import workflow_processor
    success = workflow_processor.retrigger_section_translation(book_id, section_id)
    if success:
        return jsonify({'status': 'success', 'message': 'Section re-translation started'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to start section re-translation'}), 500

@app.route('/workflow/api/book/<book_id>/sections', methods=['GET'])
def workflow_api_get_book_sections(book_id):
    """
    Возвращает список секций книги со статусами перевода.
    """
    import workflow_db_manager
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    
    # Добавляем данные о статусе перевода
    result = []
    for s in sections:
        statuses = workflow_db_manager.get_section_stage_statuses_workflow(s['section_id'])
        translate_stage_data = statuses.get('translate')
        
        # ГАРАНТИРУЕМ, ЧТО СТАТУС - ЭТО СТРОКА (убираем [object Object])
        if isinstance(translate_stage_data, dict):
            translate_status = str(translate_stage_data.get('status', 'pending'))
        else:
            translate_status = str(translate_stage_data or 'pending')
            
        result.append({
            'section_id': s['section_id'],
            'section_title': s['translated_title'] or s['section_title'],
            'status': translate_status
        })
    
    return jsonify(result)

@app.route('/workflow/api/book/<book_id>/generate_comic', methods=['POST'])
def workflow_api_generate_comic(book_id):
    """
    API endpoint to start comic generation for a book.
    """
    import workflow_processor
    # Проверяем, что суммаризация готова
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if not book_info:
        return jsonify({'status': 'error', 'message': 'Book not found'}), 404
        
    book_stage_statuses = book_info.get('book_stage_statuses', {})
    summarize_status = book_stage_statuses.get('summarize', {}).get('status')
    
    if summarize_status not in ['completed', 'completed_with_errors']:
        return jsonify({'status': 'error', 'message': 'Summarization not ready'}), 400

    success = workflow_processor.start_comic_generation_task(book_id, app)
    if success:
        return jsonify({'status': 'success', 'message': 'Comic generation started'})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to start comic generation'}), 500

@app.route('/workflow/book/<book_id>/comic')
def workflow_book_comic_view(book_id):
    """
    Page to view the generated comic for a book.
    """
    import workflow_db_manager
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if not book_info:
        return "Book not found", 404
    
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    # Теперь картинки в БД, поэтому проверяем наличие через БД
    comic_sections = []
    for section in sections:
        # Проверяем наличие изображения в БД БЕЗ загрузки блоба
        if workflow_db_manager.check_comic_image_exists(section['section_id']):
            section['comic_url'] = url_for('workflow_api_comic_image', section_id=section['section_id'])
            # Загружаем суммаризацию для оверлея
            import workflow_cache_manager
            section['summary'] = workflow_cache_manager.load_section_stage_result(book_id, section['section_id'], 'summarize')
            comic_sections.append(section)
            
    # Проверяем админские права для ссылки "Назад"
    admin = request.args.get('admin') == 'true' or request.args.get('user') == 'admin'
    return render_template('comic_view.html', book=book_info, sections=comic_sections, admin=admin)

@app.route('/workflow/api/section/<int:section_id>/regenerate_comic', methods=['POST'])
def workflow_api_regenerate_section_comic(section_id):
    """
    API endpoint to regenerate comic image for a single section.
    """
    admin = request.args.get('admin') == 'true' or request.args.get('user') == 'admin' or session.get('admin_mode') == True
    if not admin:
        # Проверяем наличие валидной сессии, если параметры не переданы
        if not session.get('admin_mode'):
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403

    import workflow_db_manager
    import workflow_cache_manager
    import comic_generator
    import json

    # 1. Получаем инфо о секции
    # Нам нужно найти book_id для этой секции. В workflow_db_manager нет прямого метода get_book_id_by_section_id
    # Но мы можем получить инфо о секции через существующие методы
    # Нам нужно сначала найти book_id. Пройдем через базу.
    db = workflow_db_manager.get_workflow_db()
    row = db.execute("SELECT book_id, section_epub_id FROM sections WHERE section_id = ?", (section_id,)).fetchone()
    if not row:
        return jsonify({'status': 'error', 'message': 'Section not found'}), 404
    
    book_id = row['book_id']
    book_info = workflow_db_manager.get_book_workflow(book_id)
    
    # 2. Получаем суммаризацию
    summary = workflow_cache_manager.load_section_stage_result(book_id, section_id, 'summarize')
    if not summary or len(summary.strip()) < 50:
        return jsonify({'status': 'error', 'message': 'Summary too short or not found'}), 400

    # 3. Подготавливаем Visual Bible
    visual_bible_prompt = ""
    visual_bible_raw = book_info.get('visual_bible')
    if visual_bible_raw:
        try:
            bible_data = json.loads(visual_bible_raw)
            bible_list = [f"- {name}: {desc}" for name, desc in bible_data.items()]
            visual_bible_prompt = "\nREFERENCE FOR CHARACTERS (Follow these descriptions strictly):\n" + "\n".join(bible_list)
        except: pass

    # 4. Формируем промпт (тот самый отлаженный)
    BASE_PROMPT = (
        "Draw a dynamic modern comic adaptation of the text in 6–10 sequential panels. "
        "Short dialogue (1–3 words per bubble) allowed. No captions, no narration, no internal monologue, no long text. "
        "Do not use evenly spaced rectangular panels. Use an asymmetrical, contemporary layout with varied panel sizes, "
        "angled or overlapping frames, and occasional full-bleed panels. "
        "Tell the story through action, movement, body language, lighting, environment, and cinematic camera shifts "
        "(close-ups, wide shots, low angles, Dutch tilt). Each panel must show clear progression and escalating tension. "
        "Style: bold, kinetic, high-end modern graphic novel, Studio Ghibli inspired graphic."
    )
    prompt = f"{BASE_PROMPT}\n\n{visual_bible_prompt}\n\nTEXT TO ADAPT: {summary}"

    # 5. Генерируем
    generator = comic_generator.ComicGenerator()
    image_data, error = generator.generate_image(prompt, book_id, section_id)
    
    if image_data:
        workflow_db_manager.save_comic_image_workflow(book_id, section_id, image_data)
        return jsonify({'status': 'success', 'message': 'Image regenerated'})
    else:
        return jsonify({'status': 'error', 'message': f'Regeneration failed: {error}'}), 500

@app.route('/workflow/api/comic_image/<int:section_id>')
def workflow_api_comic_image(section_id):
    """
    Эндпоинт для получения бинарных данных изображения из БД.
    """
    import workflow_db_manager
    from flask import Response
    image_data = workflow_db_manager.get_comic_image_workflow(section_id)
    if not image_data:
        return "Image not found", 404

    # Определяем тип по сигнатуре (чтобы браузер корректно декодировал)
    mimetype = 'application/octet-stream'
    try:
        if isinstance(image_data, (bytes, bytearray)) and len(image_data) >= 4:
            if image_data[:8] == b"\x89PNG\r\n\x1a\n":
                mimetype = 'image/png'
            elif image_data[:3] == b"\xff\xd8\xff":
                mimetype = 'image/jpeg'
            elif image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
                mimetype = 'image/webp'
    except Exception:
        mimetype = 'application/octet-stream'

    resp = Response(image_data, mimetype=mimetype)
    # Разрешаем браузеру кэшировать и не перетягивать заново при скролле/возврате на страницу
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route('/admin/system_status')
def admin_system_status():
    """
    Эндпоинт для мониторинга свободного места на диске и размера БД (только для админов).
    """
    admin = request.args.get('admin') == 'true' or request.args.get('user') == 'admin'
    if not admin:
        return "Access denied", 403
        
    import shutil
    from pathlib import Path
    
    # Путь к данным на fly.io
    data_path = "/data"
    if not os.path.exists(data_path):
        data_path = "." # Fallback для локальной разработки
        
    usage = shutil.disk_usage(data_path)
    # Имя БД из конфига или дефолт
    db_file = Path(data_path) / "workflow.db"
    db_size = db_file.stat().st_size if db_file.exists() else 0
    
    status = {
        "disk_total_gb": round(usage.total / (1024**3), 2),
        "disk_free_gb": round(usage.free / (1024**3), 2),
        "disk_used_percent": round((usage.used / usage.total) * 100, 1),
        "db_size_mb": round(db_size / (1024**2), 2),
        "location": data_path
    }
    
    return jsonify(status)

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

    if analysis_stage_status not in ['completed', 'completed_with_errors', 'awaiting_edit']:
         print(f"  [DownloadAnalysis] Этап анализа для книги {book_id} не завершен. Статус: {analysis_stage_status}")
         return f"Analysis not complete (Status: {analysis_stage_status}).", 409

    # --- НОВОЕ: Загружаем результат анализа книги целиком ---
    analysis_result = None
    try:
        analysis_result = workflow_cache_manager.load_book_stage_result(book_id, 'analyze')
        
        # Если нет в кэше, проверяем visual_bible в БД (для режима "БЕЗ ПЕРЕВОДА")
        if not analysis_result or not analysis_result.strip():
            if book_info and book_info.get('visual_bible'):
                analysis_result = book_info['visual_bible']

        if not analysis_result or not analysis_result.strip():
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

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ СКАЧИВАНИЯ EPUB WORKFLOW ---
@app.route('/workflow_download_epub/<book_id>', methods=['GET'])
def workflow_download_epub(book_id):
    print(f"Запрос на скачивание EPUB для книги: {book_id}")

    book_info = workflow_db_manager.get_book_workflow(book_id)
    if book_info is None:
        print(f"  [DownloadEPUB] Книга с ID {book_id} не найдена в Workflow DB.")
        return "Book not found", 404

    book_stage_statuses = book_info.get('book_stage_statuses', {})
    epub_stage_status = book_stage_statuses.get('epub_creation', {}).get('status')

    # Если затребована пересборка (force_rebuild=1) или файл отсутствует, запускаем процесс заново
    force_rebuild = request.args.get('force_rebuild') == '1'
    
    # Формируем путь к переведенному EPUB файлу
    base_name = os.path.splitext(book_info.get('filename', 'book'))[0]
    target_language = book_info.get('target_language', 'russian')
    epub_filename = f"{base_name}_{target_language}.epub"
    epub_filepath = UPLOADS_DIR / "translated" / epub_filename

    # Проверяем существование файла и статус
    file_exists = epub_filepath.exists()

    if force_rebuild or not file_exists or epub_stage_status not in ['completed', 'completed_with_errors']:
        print(f"  [DownloadEPUB] Запуск (пере)создания EPUB для книги {book_id}. Force: {force_rebuild}, Exists: {file_exists}, Status: {epub_stage_status}")
        
        # Проверяем, что перевод вообще готов
        translate_stage_status = book_stage_statuses.get('translate', {}).get('status')
        if translate_stage_status not in ['completed', 'completed_with_errors']:
            return f"Cannot create EPUB: translation not ready (Status: {translate_stage_status})", 400

        # Запускаем генерацию EPUB синхронно (для скачивания) или асинхронно?
        # Т.к. это запрос на скачивание, лучше подождать завершения, если файл небольшой,
        # но наш ворклоу обычно асинхронный.
        # Однако для "аккуратного добавления картинок" нам нужно, чтобы при нажатии кнопки скачивания
        # пользователь получил актуальный файл.
        
        import workflow_processor
        from flask import current_app
        # Вызываем функцию создания напрямую
        workflow_processor.process_book_epub_creation(book_id, admin=(request.args.get('admin') == 'true'))
        
        # После завершения проверяем файл еще раз
        if not epub_filepath.exists():
            return "Failed to regenerate EPUB file", 500

    try:
        # Читаем файл и отправляем его
        with open(epub_filepath, 'rb') as f:
            epub_content = f.read()
        download_filename = epub_filename
        return Response(
            epub_content,
            mimetype="application/epub+zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{download_filename}"}
        )
    except Exception as e:
        print(f"  [DownloadEPUB] ОШИБКА при чтении EPUB файла {epub_filepath}: {e}")
        return "Error reading EPUB file", 500

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА СКАЧИВАНИЯ EPUB ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ПОЛЬЗОВАТЕЛЬСКОЙ СТРАНИЦЫ ПЕРЕВОДА ---
def get_news_content():
    """Читает содержимое файла Newsline.json"""
    try:
        news_file = os.path.join(os.path.dirname(__file__), 'Newsline.json')
        if os.path.exists(news_file):
            with open(news_file, 'r', encoding='utf-8') as f:
                news = json.load(f)
                if isinstance(news, list):
                    return news[::-1] # Самые новые новости в начале
                return news
    except Exception as e:
        print(f"[News] Ошибка чтения Newsline.json: {e}")
    return []

@app.route('/translate/<access_token>', methods=['GET'])
def translate_page(access_token):
    """Универсальная страница для пользователей - показывает форму загрузки или прогресс/результат"""
    print(f"Запрос пользовательской страницы для токена: {access_token}")
    
    # Проверяем параметр admin
    admin = request.args.get('admin') == 'true'
    print(f"Admin режим в translate_page: {admin}")
    
    # --- ПРОВЕРЯЕМ СЕССИЮ ПОЛЬЗОВАТЕЛЯ ---
    session_id = request.cookies.get('user_session')
    user_access_token = None
    
    if session_id:
        print(f"Найдена сессия пользователя: {session_id}")
        user_access_token = workflow_db_manager.get_session_access_token(session_id)
        if user_access_token:
            print(f"Сессия активна, токен пользователя: {user_access_token}")
        else:
            print(f"Сессия истекла или недействительна: {session_id}")
    
    # --- ОПРЕДЕЛЯЕМ КАКОЙ ТОКЕН ИСПОЛЬЗОВАТЬ ---
    # Если у пользователя есть активная сессия, используем её токен
    # Иначе используем токен из URL
    effective_token = user_access_token if user_access_token else access_token
    
    # Проверяем есть ли файл с этим токеном
    book_info = workflow_db_manager.get_book_by_access_token(effective_token)
    news_content = get_news_content()
    
    if book_info:
        # Показываем прогресс/результат
        print(f"Найдена книга: {book_info.get('filename')}")
        
        # --- СОЗДАЕМ НОВУЮ СЕССИЮ, ЕСЛИ ЕЁ НЕТ ---
        access_token = None
        if book_info and book_info.get('access_token'):
            access_token = book_info['access_token']
        
        response = render_template('translate_user.html', 
                             access_token=effective_token, 
                             book_info=book_info,
                             admin=admin,
                             news_content=news_content)
        
        if not session_id and effective_token == access_token:
            # Создаем новую сессию для пользователя
            new_session_id = workflow_db_manager.create_user_session(access_token)
            if new_session_id:
                print(f"Создана новая сессия для пользователя: {new_session_id}")
                response_obj = make_response(response)
                response_obj.set_cookie(
                    'user_session', 
                    new_session_id, 
                    max_age=24*60*60,  # 24 часа
                    httponly=True,     # Защита от XSS
                    secure=False,      # False для HTTP, True для HTTPS
                    samesite='Lax'     # Защита от CSRF
                )
                return response_obj
        
        return response
    else:
        # Показываем форму загрузки
        print(f"Книга не найдена для токена {effective_token}, показываем форму загрузки")
        return render_template('translate_user.html', 
                             access_token=effective_token, 
                             book_info=None,
                             admin=admin,
                             news_content=news_content)

@app.route('/user', methods=['GET'])
def user_main_page():
    """Главная страница пользователя - перенаправляет на книгу пользователя или показывает форму загрузки"""
    print("Запрос главной страницы пользователя")
    
    # Проверяем параметр admin
    admin = request.args.get('admin') == 'true'
    print(f"Admin режим: {admin}")
    
    # --- ПРОВЕРЯЕМ СЕССИЮ ПОЛЬЗОВАТЕЛЯ ---
    session_id = request.cookies.get('user_session')
    news_content = get_news_content()
    
    if session_id:
        print(f"Найдена сессия пользователя: {session_id}")
        user_access_token = workflow_db_manager.get_session_access_token(session_id)
        if user_access_token:
            print(f"Сессия активна, перенаправляем на книгу пользователя")
            # Передаем параметр admin в redirect
            redirect_url = url_for('translate_page', access_token=user_access_token)
            if admin:
                redirect_url += '?admin=true'
            return redirect(redirect_url)
        else:
            print(f"Сессия истекла или недействительна: {session_id}")
            # Очищаем недействительную сессию из cookie
            response = make_response(render_template('translate_user.html', access_token=None, book_info=None, admin=admin, news_content=news_content))
            response.delete_cookie('user_session')
            return response
    
    # Если нет активной сессии, показываем форму загрузки
    print("Нет активной сессии, показываем форму загрузки")
    return render_template('translate_user.html', access_token=None, book_info=None, admin=admin, news_content=news_content)

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ДЛЯ ГЛАВНОЙ СТРАНИЦЫ ПОЛЬЗОВАТЕЛЯ ---

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ПОЛУЧЕНИЯ СТАТУСА WORKFLOW КНИГИ ---
@app.route('/workflow_book_status/<book_id>', methods=['GET'])
def get_workflow_book_status(book_id):
    # print(f"Запрос статуса workflow для книги: {book_id}")
    import workflow_db_manager
    response_data = workflow_db_manager.get_workflow_book_status(book_id)
    if response_data is None:
        return jsonify({"error": "Book not found"}), 404
    return jsonify(response_data)


@app.route('/api/user_feedback', methods=['POST'])
def api_user_feedback():
    """
    Принимает сообщение от пользователя со страницы /user и отправляет его разработчику в Telegram.
    """
    try:
        data = request.get_json() or {}
        text = (data.get('message') or '').strip()
        if not text:
            return jsonify({'status': 'error', 'message': 'Пустое сообщение'}), 400

        access_token = data.get('access_token') or None
        book_id = data.get('book_id') or None
        is_admin = bool(data.get('admin'))

        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        tg_message = f"""
📩 <b>Сообщение от пользователя EPUB Translator</b>

⏰ <b>Время (UTC):</b> {ts}
🔑 <b>Access token:</b> {access_token or '—'}
📘 <b>Book ID:</b> {book_id or '—'}
👤 <b>Admin flag:</b> {"true" if is_admin else "false"}
🌐 <b>IP:</b> {ip or '—'}
🖥 <b>User-Agent:</b> {user_agent or '—'}

💬 <b>Сообщение:</b>
{text}
        """.strip()

        ok = telegram_notifier.send_message(tg_message)
        if not ok:
            return jsonify({'status': 'error', 'message': 'Не удалось отправить сообщение в Telegram'}), 500

        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"[UserFeedback] Ошибка обработки сообщения пользователя: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': 'Внутренняя ошибка сервера'}), 500

def get_workflow_book_status_old(book_id):
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
        "book_title": book_info.get('book_title', book_info.get('filename')), # Добавляем название книги
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

    # If still no active stage found (all completed/error), workflow is completed
    # Don't set any stage as active - let the UI handle completed workflow
    if current_active_stage_name is None and stages_ordered:
        # Check if all stages are completed
        all_completed = True
        for stage in stages_ordered:
            stage_name = stage['stage_name']
            status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
            if status not in ['completed', 'completed_empty', 'skipped', 'passed']:
                all_completed = False
                break
        
        if all_completed:
            current_active_stage_name = None  # Workflow is completed
        else:
            # If not all completed but no active stage found, something is wrong
            # Take the first pending stage as fallback
            for stage in stages_ordered:
                stage_name = stage['stage_name']
                status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
                if status == 'pending':
                    current_active_stage_name = stage_name
                    break

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
        # Читаем параметры из JSON body
        request_data = request.get_json() or {}
        admin = request_data.get('admin', False)
        continue_after_edit = request_data.get('continue_after_edit', False)
        edited_analysis = request_data.get('edited_analysis')
        
        # Если это продолжение после редактирования, сохраняем отредактированный анализ
        if continue_after_edit and edited_analysis:
            try:
                import workflow_cache_manager
                import workflow_db_manager
                import json
                
                # 1. Всегда сохраняем в кэш этапа 'analyze' (для глоссария)
                workflow_cache_manager.save_book_stage_result(book_id, 'analyze', edited_analysis)
                
                # 2. Пытаемся сохранить в visual_bible, если это похоже на JSON каст-листа
                if edited_analysis.strip().startswith('{'):
                    try:
                        # Проверяем валидность JSON перед сохранением в спец. колонку
                        json.loads(edited_analysis)
                        workflow_db_manager.update_book_visual_bible_workflow(book_id, edited_analysis)
                        current_app.logger.info(f"Visual Bible обновлен для книги {book_id}")
                    except:
                        pass # Просто текст, не JSON
                
                # 3. Обновляем статус анализа на completed
                workflow_db_manager.update_book_stage_status_workflow(book_id, 'analyze', 'completed')
                # Явно вызываем пересчет общего статуса книги
                workflow_processor.update_overall_workflow_book_status(book_id)
                current_app.logger.info(f"Отредактированный анализ сохранен для книги {book_id}")
            except Exception as e:
                current_app.logger.error(f"Ошибка сохранения отредактированного анализа для книги {book_id}: {e}")
                return jsonify({'status': 'error', 'message': f'Ошибка сохранения анализа: {str(e)}'}), 500
        
        # Запускаем через очередь
        workflow_processor.workflow_queue_manager.add_book_to_queue(book_id, app, admin=admin)
        return jsonify({'status': 'success', 'message': 'Workflow added to queue'}), 200
    except Exception as e:
        current_app.logger.error(f"Ошибка при запуске рабочего процесса для книги {book_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ---

# --- НОВЫЕ МАРШРУТЫ ДЛЯ АНАЛИЗА ВИДЕО ---

@app.route('/video-analysis', methods=['GET'])
def video_analysis_page():
    """Отображает страницу для анализа видео."""
    admin = request.args.get('admin') == 'true'
    resp = make_response(render_template('video_analysis.html', admin=admin))
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; font-src 'self' https://cdnjs.cloudflare.com; img-src 'self' data: https://unpkg.com;"
    resp.headers['Content-Security-Policy'] = csp_policy
    return resp

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

# --- МАРШРУТЫ ДЛЯ ВИДЕО ЧАТА ---

@app.route('/api/videos/<video_id>/chat', methods=['POST'])
def api_video_chat(video_id):
    """API эндпойнт для диалога с ИИ по содержанию видео."""
    try:
        # Получаем данные запроса
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Отсутствует тело запроса'}), 400
        
        user_message = data.get('message', '').strip()
        if not user_message:
            return jsonify({'error': 'Сообщение не может быть пустым'}), 400
        
        history = data.get('history', [])
        if not isinstance(history, list):
            return jsonify({'error': 'История должна быть массивом'}), 400
        
        print(f"[VideoChatAPI] Запрос на диалог для видео {video_id}")
        try:
            print(f"[VideoChatAPI] Сообщение: {user_message}")
        except UnicodeEncodeError:
            print(f"[VideoChatAPI] Сообщение: [содержит специальные символы, длина {len(user_message)}]")
        print(f"[VideoChatAPI] История: {len(history)} сообщений")
        
        # Получаем данные видео из БД по YouTube ID
        video_data = video_db.get_video_by_youtube_id(video_id)
        if not video_data:
            return jsonify({'error': 'Видео не найдено'}), 404
        
        # Получаем данные анализа видео по внутреннему ID
        analysis_data = video_db.get_analysis_by_video_id(video_data['id'])
        if not analysis_data or not (analysis_data.get('extracted_text') or analysis_data.get('analysis_result')):
            return jsonify({'error': 'Анализ видео не найден или не содержит текста для обсуждения'}), 404
        
        # Создаем экземпляр обработчика чата
        try:
            chat_handler = video_chat_handler.VideoChatHandler()
        except ValueError as e:
            return jsonify({'error': f'Ошибка инициализации: {str(e)}'}), 500
        
        # Обрабатываем сообщение
        result = chat_handler.process_chat_message(video_data, analysis_data, user_message, history)
        
        if result['success']:
            return jsonify({
                'success': True,
                'response': result['response'],
                'model_used': result['model_used'],
                'model_level': result['model_level']
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 500
            
    except Exception as e:
        print(f"[VideoChatAPI] Непредвиденная ошибка: {e}")
        import traceback
        print(f"[VideoChatAPI] Traceback:")
        print(traceback.format_exc())
        return jsonify({'error': f'Внутренняя ошибка сервера: {str(e)}'}), 500

@app.route('/api/videos/<video_id>/suggestions', methods=['GET'])
def api_video_suggestions(video_id):
    """
    Генерирует умные подсказки для диалога с видео.
    """
    try:
        print(f"[VideoSuggestionsAPI] Запрос подсказок для видео {video_id}")
        
        # Получаем данные видео из БД по YouTube ID
        video_data = video_db.get_video_by_youtube_id(video_id)
        if not video_data:
            return jsonify({'error': 'Видео не найдено'}), 404
        
        # Получаем анализ видео по внутреннему ID
        analysis_data = video_db.get_analysis_by_video_id(video_data['id'])
        if not analysis_data or not (analysis_data.get('extracted_text') or analysis_data.get('analysis_result')):
            return jsonify({'error': 'Анализ видео не найден или не содержит текста для создания подсказок'}), 404
        
        # Создаем обработчик для генерации подсказок
        chat_handler = video_chat_handler.VideoChatHandler()
        
        # Создаем специальный промпт для генерации подсказок
        title = video_data.get('title', 'Видео')
        extracted_text = analysis_data.get('extracted_text', '')[:5000]  # Ограничиваем для подсказок
        analysis_summary = analysis_data.get('analysis_summary', '')
        
        suggestions_prompt = f"""На основе содержания видео "{title}" предложи ровно 4 интересных вопроса для обсуждения.

КРАТКОЕ СОДЕРЖАНИЕ:
{analysis_summary}

НАЧАЛЬНЫЙ ФРАГМЕНТ:
{extracted_text}

ТРЕБОВАНИЯ:
- Вопросы должны быть конкретными и интересными 
- Каждый вопрос на отдельной строке
- Начинай вопросы с эмодзи: 🤔, 📊, 💡, 🔍
- Формат: "🤔 Ваш вопрос здесь?"
- НЕ используй номера или маркеры
- Вопросы должны касаться ТОЛЬКО содержания данного видео"""
        
        print(f"[VideoSuggestionsAPI] Отправляем запрос к модели для генерации подсказок")
        
        # Подготавливаем сообщения для API
        messages = [
            {"role": "system", "content": "Ты помощник, который создает интересные вопросы для обсуждения видео."},
            {"role": "user", "content": suggestions_prompt}
        ]
        
        # Пробуем модели по очереди (primary -> fallback_level1 -> fallback_level2)
        model_levels = ['primary', 'fallback_level1', 'fallback_level2']
        response = None
        model_used = None
        
        for level in model_levels:
            model_name = get_model_for_operation('video_analyze', level)
            if not model_name:
                continue
                
            print(f"[VideoSuggestionsAPI] Пробуем модель {model_name} (уровень: {level})")
            response = chat_handler.chat_with_model(messages, model_name)
            if response:
                model_used = model_name
                print(f"[VideoSuggestionsAPI] Успешный ответ от модели {model_name}")
                break
            else:
                print(f"[VideoSuggestionsAPI] Модель {model_name} не сработала, пробуем следующую")
        
        if not response:
            # Все модели не сработали - fallback к стандартным подсказкам  
            print(f"[VideoSuggestionsAPI] Все модели недоступны, используем стандартные подсказки")
            suggestions = [
                "🤔 Объясни главную идею простыми словами",
                "📊 Какие факты и данные наиболее важные?",
                "💡 Что было самым неожиданным открытием?",
                "🔍 На какие моменты стоит обратить внимание?"
            ]
        else:
            # Парсим ответ модели
            print(f"[VideoSuggestionsAPI] Получен ответ от модели длиной {len(response)} символов")
            lines = [line.strip() for line in response.strip().split('\n') if line.strip()]
            
            # Фильтруем строки с эмодзи
            suggestions = []
            for line in lines:
                if any(emoji in line for emoji in ['🤔', '📊', '💡', '🔍']) and '?' in line:
                    suggestions.append(line)
            
            # Берем первые 4 или используем fallback
            if len(suggestions) >= 4:
                suggestions = suggestions[:4]
            else:
                print(f"[VideoSuggestionsAPI] Недостаточно подсказок ({len(suggestions)}), используем fallback")
                suggestions = [
                    "🤔 Объясни главную идею простыми словами",
                    "📊 Какие факты и данные наиболее важные?", 
                    "💡 Что было самым неожиданным открытием?",
                    "🔍 На какие моменты стоит обратить внимание?"
                ]
        
        print(f"[VideoSuggestionsAPI] Сгенерировано {len(suggestions)} подсказок")
        
        return jsonify({
            'success': True,
            'suggestions': suggestions,
            'model_used': model_used,
            'generated_by_ai': bool(model_used)
        })
        
    except Exception as e:
        print(f"[VideoSuggestionsAPI] Ошибка: {e}")
        import traceback
        print(f"[VideoSuggestionsAPI] Traceback:")
        print(traceback.format_exc())
        
        # Возвращаем fallback подсказки при ошибке
        return jsonify({
            'success': True,
            'suggestions': [
                "🤔 Объясни главную идею простыми словами",
                "📊 Какие факты и данные наиболее важные?",
                "💡 Что было самым неожиданным открытием?",
                "🔍 На какие моменты стоит обратить внимание?"
            ]
        })

# --- КОНЕЦ МАРШРУТОВ ДЛЯ ВИДЕО ЧАТА ---

# --- НОВЫЕ МАРШРУТЫ ДЛЯ TOPTUBE ---

@app.route('/', methods=['GET'])
def toptube_page():
    """Отображает страницу с проанализированными видео."""
    # Проверяем параметр admin
    admin = request.args.get('admin') == 'true'
    return render_template('toptube.html', admin=admin)

@app.route('/beta', methods=['GET'])
def beta_page():
    """Отображает новую бета-версию страницы с видео."""
    # Проверяем параметр admin
    admin = request.args.get('admin') == 'true'
    return render_template('beta.html', admin=admin)

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

        # Добавляем безопасно: время до следующего запуска планировщика
        time_until_next = None
        toptube_job = scheduler.get_job('toptube_full_workflow_job')
        if toptube_job and toptube_job.next_run_time:
            next_run = toptube_job.next_run_time
            now = datetime.datetime.now(next_run.tzinfo)
            delta = next_run - now
            # Форматируем как HH:MM:SS
            total_seconds = int(delta.total_seconds())
            if total_seconds > 0:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                time_until_next = f"{hours:02}:{minutes:02}:{seconds:02}"
            else:
                time_until_next = "00:00:00"

        return jsonify({
            'success': True,
            'stats': stats,
            'time_until_next': time_until_next
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

@app.route('/api/toptube/videos/<int:video_id>', methods=['DELETE'])
def api_delete_toptube_video(video_id: int):
    """API эндпойнт для удаления одного видео."""
    try:
        import video_db
        
        # Проверяем, существует ли видео
        video = video_db.get_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Видео не найдено'}), 404
        
        # Удаляем видео и добавляем канал в стоп-лист
        success = video_db.delete_video_and_blacklist_channel(video['video_id'])
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Видео "{video["title"]}" успешно удалено'
            }), 200
        else:
            return jsonify({'error': 'Ошибка при удалении видео'}), 500
        
    except Exception as e:
        print(f"[TopTube API] Ошибка удаления видео {video_id}: {e}")
        return jsonify({'error': f'Ошибка удаления видео: {str(e)}'}), 500

# --- КОНЕЦ МАРШРУТОВ ДЛЯ TOPTUBE ---

# --- МАРШРУТЫ ДЛЯ ФУТБОЛА ---

@app.route('/bet', methods=['GET'])
def bet_page():
    """Отображает страницу с матчами и ставками."""
    admin = request.args.get('admin') == 'true'
    return render_template('bet.html', admin=admin)

@app.route('/api/football/matches', methods=['GET'])
def api_get_football_matches():
    """API эндпойнт для получения списка матчей с фаворитом."""
    try:
        matches = football.get_all_matches(filter_fav=True)
        return jsonify({
            'success': True,
            'matches': matches
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка получения матчей: {e}")
        return jsonify({'error': f'Ошибка получения матчей: {str(e)}'}), 500

@app.route('/api/football/matches/all', methods=['GET'])
def api_get_all_football_matches():
    """API эндпойнт для получения всех матчей (включая без фаворита)."""
    try:
        matches = football.get_all_matches(filter_fav=False)
        return jsonify({
            'success': True,
            'matches': matches
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка получения всех матчей: {e}")
        return jsonify({'error': f'Ошибка получения всех матчей: {str(e)}'}), 500

@app.route('/api/football/match-stats', methods=['GET'])
def api_get_match_stats():
    """API эндпойнт для получения stats_60min и счета в перерыве для конкретного матча."""
    try:
        fixture_id = request.args.get('fixture_id')
        if not fixture_id:
            return jsonify({'error': 'Не указан fixture_id'}), 400
        
        from football import get_football_db_connection
        import json
        conn = get_football_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT stats_60min FROM matches WHERE fixture_id = ?", (fixture_id,))
        row = cursor.fetchone()
        conn.close()
        
        stats_60min = None
        halftime_score = None
        
        if row and row['stats_60min']:
            stats_60min = row['stats_60min']
            # Извлекаем счет из статистики
            try:
                stats = json.loads(stats_60min) if isinstance(stats_60min, str) else stats_60min
                if stats and 'score' in stats:
                    halftime_score = {
                        'home': stats['score'].get('home', 0),
                        'away': stats['score'].get('away', 0)
                    }
            except Exception:
                pass
        
        return jsonify({
            'success': True,
            'stats_60min': stats_60min,
            'halftime_score': halftime_score
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка получения статистики матча: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка получения статистики: {str(e)}'}), 500

@app.route('/api/football/limits', methods=['GET'])
def api_get_football_limits():
    """API эндпойнт для получения лимитов API."""
    try:
        limits = football.get_api_limits()
        return jsonify({
            'success': True,
            'limits': limits
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка получения лимитов: {e}")
        return jsonify({'error': f'Ошибка получения лимитов: {str(e)}'}), 500

@app.route('/api/football/sync', methods=['POST'])
def api_sync_football_matches():
    """API эндпойнт для запуска синхронизации матчей с API."""
    try:
        manager = football.get_manager()
        stats = manager.sync_matches()
        return jsonify({
            'success': True,
            'message': 'Синхронизация завершена',
            'stats': stats
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка синхронизации: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка синхронизации: {str(e)}'}), 500

@app.route('/api/football/check', methods=['POST'])
def api_check_football_matches():
    """API эндпойнт для запуска проверки матчей и обновления ставок/статусов."""
    try:
        manager = football.get_manager()
        manager.check_matches_and_collect()
        return jsonify({
            'success': True,
            'message': 'Проверка матчей завершена'
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка проверки матчей: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка проверки матчей: {str(e)}'}), 500

@app.route('/api/football/test-notification', methods=['POST'])
def api_test_football_notification():
    """API эндпойнт для тестирования отправки уведомлений в Telegram."""
    # Проверяем режим администратора
    admin = request.args.get('admin', 'false').lower() == 'true'
    if not admin:
        return jsonify({'error': 'Доступ запрещен. Используйте ?admin=true'}), 403
    
    try:
        import sqlite3
        from football import get_football_db_connection
        
        manager = football.get_manager()
        
        # Создаем тестовые данные
        # Пытаемся взять реальный матч из БД или создаем тестовый объект
        conn = get_football_db_connection()
        cursor = conn.cursor()
        
        # Берем первый матч из БД, если есть
        cursor.execute("SELECT * FROM matches LIMIT 1")
        match_row = cursor.fetchone()
        conn.close()
        
        if match_row:
            # Используем реальный матч
            match = match_row
            print(f"[Football Test] Используем реальный матч: {match['home_team']} vs {match['away_team']}")
        else:
            # Создаем тестовый объект-заглушку
            class TestMatch:
                def __init__(self):
                    self.home_team = "Тестовая команда 1"
                    self.away_team = "Тестовая команда 2"
                    self.fav = "Тестовая команда 1"
                    self.fixture_id = "test_fixture_123"
                
                def __getitem__(self, key):
                    return getattr(self, key)
                
                def keys(self):
                    return ['home_team', 'away_team', 'fav', 'fixture_id']
            
            match = TestMatch()
            print(f"[Football Test] Используем тестовый матч")
        
        # Тестовые данные для статистики
        test_stats = {
            'score': {
                'home': 0,
                'away': 1  # Фаворит проигрывает (для теста)
            },
            'raw_data': {}
        }
        
        # Тестовые параметры для уведомления
        test_live_odds = 2.5
        test_ai_decision = True  # ИИ ответил ДА
        test_ai_reason = "Тестовое обоснование: фаворит контролирует игру, несмотря на проигрыш. Высокая вероятность победы."
        
        # Вызываем функцию отправки уведомления
        result = manager._send_match_notification(
            match=match,
            stats=test_stats,
            live_odds=test_live_odds,
            ai_decision=test_ai_decision,
            ai_reason=test_ai_reason
        )
        
        if result:
            return jsonify({
                'success': True,
                'message': 'Тестовое уведомление отправлено в Telegram'
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': 'Не удалось отправить уведомление (проверьте настройки Telegram или логи)'
            }), 500
            
    except Exception as e:
        print(f"[Football API] Ошибка тестирования уведомления: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка тестирования уведомления: {str(e)}'}), 500

@app.route('/api/football/analyze-risk', methods=['POST'])
def api_analyze_bet_risk():
    """API эндпойнт для анализа риска ставки на основе прогноза ИИ."""
    fixture_id = None
    try:
        data = request.get_json()
        fixture_id = data.get('fixture_id')
        bet_ai = data.get('bet_ai')
        bet_ai_odds = data.get('bet_ai_odds')
        stats_json = data.get('stats_json')
        
        if not fixture_id or not bet_ai or not bet_ai_odds or not stats_json:
            return jsonify({'error': 'Недостаточно данных для анализа'}), 400
        
        # Атомарно проверяем и добавляем fixture_id (с блокировкой)
        with analyzing_risk_lock:
            if fixture_id in analyzing_risk_fixtures:
                return jsonify({
                    'success': False,
                    'error': 'Анализ риска уже выполняется для этого матча'
                }), 409
            
            # Добавляем fixture_id в множество обрабатываемых
            analyzing_risk_fixtures.add(fixture_id)
        
        try:
            manager = football.get_manager()
            analysis = manager.analyze_bet_risk(fixture_id, bet_ai, float(bet_ai_odds), stats_json)
            
            if analysis:
                result = jsonify({
                    'success': True,
                    'analysis': analysis
                }), 200
            else:
                result = jsonify({
                    'success': False,
                    'error': 'Не удалось получить анализ риска'
                }), 500
            
            return result
        except Exception as e:
            # Логируем ошибку, но не прерываем выполнение
            print(f"[Football API] Ошибка анализа риска: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Ошибка анализа риска: {str(e)}'}), 500
        finally:
            # Убираем fixture_id из множества обрабатываемых в любом случае (с блокировкой)
            if fixture_id:
                with analyzing_risk_lock:
                    analyzing_risk_fixtures.discard(fixture_id)
            
    except Exception as e:
        # Убираем fixture_id из множества обрабатываемых в случае ошибки (с блокировкой)
        if fixture_id:
            with analyzing_risk_lock:
                analyzing_risk_fixtures.discard(fixture_id)
        print(f"[Football API] Ошибка анализа риска: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка анализа риска: {str(e)}'}), 500

# --- КОНЕЦ МАРШРУТОВ ДЛЯ ФУТБОЛА ---

@app.route('/api/football/parlay/preview', methods=['POST'])
def api_parlay_preview():
    """
    Составляет экспресс по выбранным матчам.
    Body: { fixture_ids: [str], include_all_if_empty: bool }
    Возвращает: { success: bool, parlay_json: any|null, raw: str|null }
    """
    user_key = request.remote_addr or "anon"
    try:
        data = request.get_json(silent=True) or {}
        fixture_ids = data.get('fixture_ids') or []
        include_all_if_empty = bool(data.get('include_all_if_empty', False))
        try:
            print(f"[Football Parlay API] preview request: fixtures={len(fixture_ids)} include_all={include_all_if_empty} user={user_key}")
        except Exception:
            pass

        # Атомарная блокировка повторных запросов на время выполнения
        with active_parlay_lock:
            if user_key in active_parlay_requests:
                print(f"[Football Parlay API] reject 409 (already running) user={user_key}")
                return jsonify({
                    'success': False,
                    'error': 'Формирование экспресса уже выполняется'
                }), 409
            active_parlay_requests.add(user_key)

        try:
            manager = football.get_manager()
            result = manager.build_parlay_preview(fixture_ids, include_all_if_empty)
            if not result:
                print(f"[Football Parlay API] result=None user={user_key}")
                return jsonify({'success': False, 'error': 'Не удалось составить экспресс'}), 500
            legs_cnt = 0
            try:
                if result.get('parlay_json') and isinstance(result['parlay_json'].get('legs'), list):
                    legs_cnt = len(result['parlay_json']['legs'])
            except Exception:
                legs_cnt = 0
            try:
                pj = result.get('parlay_json') if isinstance(result, dict) else None
                total_odds = pj.get('total_odds') if isinstance(pj, dict) else None
            except Exception:
                total_odds = None
            print(f"[Football Parlay API] success legs={legs_cnt} total_odds={total_odds} user={user_key}")
            return jsonify({'success': True, **result}), 200
        finally:
            with active_parlay_lock:
                active_parlay_requests.discard(user_key)
    except Exception as e:
        with active_parlay_lock:
            active_parlay_requests.discard(user_key)
        print(f"[Football Parlay API] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Ошибка: {str(e)}'}), 500

@app.route('/api/football/match/delete', methods=['POST'])
def api_football_delete_match():
    """
    Удаляет матч из БД по fixture_id. Для админа (простая проверка по флагу).
    Body: { fixture_id: str, admin: bool }
    """
    try:
        data = request.get_json(silent=True) or {}
        fixture_id = data.get('fixture_id')
        is_admin = bool(data.get('admin', False))
        if not is_admin:
            return jsonify({'success': False, 'error': 'Требуются права администратора'}), 403
        if not fixture_id:
            return jsonify({'success': False, 'error': 'fixture_id обязателен'}), 400
        manager = football.get_manager()
        ok = manager._delete_match(str(fixture_id))
        if ok:
            print(f"[Football API] Матч {fixture_id} удален из БД")
            return jsonify({'success': True}), 200
        else:
            return jsonify({'success': False, 'error': 'Не удалось удалить матч'}), 500
    except Exception as e:
        print(f"[Football API] Ошибка удаления матча: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Ошибка: {str(e)}'}), 500

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
    selected_model = session.get('model_name', DEFAULT_MODEL)
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    is_admin_mode = session.get('admin_mode', False)
    available_models = get_models_list(show_all_models=is_admin_mode)
    if not available_models:
        available_models = [
            {
                'name': DEFAULT_MODEL,
                'display_name': DEFAULT_MODEL,
                'description': DEFAULT_MODEL
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
    csp_policy = "default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com; font-src 'self' https://cdnjs.cloudflare.com; img-src 'self' data: https://unpkg.com;"
    resp.headers['Content-Security-Policy'] = csp_policy
    return resp

@app.route('/video/<video_id>')
def video_redirect(video_id):
    # Сохраняем все остальные параметры, если есть
    args = request.args.to_dict()
    args['video'] = video_id
    query = '&'.join(f'{k}={v}' for k, v in args.items())
    return redirect(f'/?{query}')

# --- Инициализация Telegram бота ---
telegram_bot = None
telegram_bot_thread = None

def start_telegram_bot():
    """Запускает Telegram бота в отдельном потоке"""
    global telegram_bot
    # Запускаем бота только на fly.io, не локально
    is_fly_io = os.getenv("FLY_APP_NAME") is not None
    if TELEGRAM_BOT_AVAILABLE and os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") and is_fly_io:
        try:
            telegram_bot = TelegramBotHandler()
            print("[App] 🤖 Telegram бот инициализирован")
            
            # Запускаем бота в отдельном потоке
            def bot_polling():
                try:
                    telegram_bot.run_polling()
                except Exception as e:
                    print(f"[App] ❌ Ошибка в Telegram боте: {e}")
            
            telegram_bot_thread = threading.Thread(target=bot_polling, daemon=True)
            telegram_bot_thread.start()
            print("[App] ✅ Telegram бот запущен в фоновом режиме")
            
        except Exception as e:
            print(f"[App] ❌ Ошибка запуска Telegram бота: {e}")
    else:
        if not is_fly_io:
            print("[App] 🏠 Telegram бот не запущен (локальная среда)")
        else:
            print("[App] ⚠️ Telegram бот не запущен (отсутствуют токен или chat_id)")

# --- НОВЫЙ ЭНДПОЙНТ ДЛЯ ПОИСКА КНИГИ ПО ACCESS_TOKEN ---
@app.route('/workflow_book_by_token/<access_token>', methods=['GET'])
def get_workflow_book_by_token(access_token):
    print(f"Запрос книги по access_token: {access_token}")
    
    if not access_token or access_token == 'None':
        print("  Access token пустой или None")
        return jsonify({"error": "Invalid access token"}), 400
    
    # Ищем книгу по access_token в workflow БД
    book_info = workflow_db_manager.get_book_by_access_token(access_token)
    
    if book_info is None:
        print(f"  Книга с access_token {access_token} не найдена")
        return jsonify({"error": "Book not found"}), 404
    
    print(f"  Найдена книга: {book_info.get('filename', 'Unknown')}")
    
    # Возвращаем основную информацию о книге
    response_data = {
        "book_id": book_info.get('book_id'),
        "filename": book_info.get('filename'),
        "book_title": book_info.get('book_title', book_info.get('filename')), # Добавляем название книги
        "target_language": book_info.get('target_language'),
        "current_workflow_status": book_info.get('current_workflow_status'),
        "access_token": book_info.get('access_token')
    }
    
    return jsonify(response_data), 200

# --- КОНЕЦ НОВОГО ЭНДПОЙНТА ПОИСКА ПО ТОКЕНУ ---

@app.route('/reset_session', methods=['GET'])
def reset_session():
    # Сохраняем admin параметр при сбросе сессии
    admin = request.args.get('admin') == 'true'
    redirect_url = '/user'
    if admin:
        redirect_url += '?admin=true'
    
    response = redirect(redirect_url)
    response.delete_cookie('user_session', path='/')
    return response

@app.route('/check_telegram_subscription')
def check_telegram_subscription():
    access_token = request.args.get('access_token')
    if not access_token:
        return jsonify({'subscribed': False, 'error': 'Нет токена'}), 400
    try:
        import workflow_db_manager
        users = workflow_db_manager.get_telegram_users_for_book(access_token)
        if users:
            return jsonify({'subscribed': True})
        else:
            return jsonify({'subscribed': False})
    except Exception as e:
        return jsonify({'subscribed': False, 'error': str(e)}), 500

@app.route('/api/football/generate-token', methods=['GET'])
def api_generate_football_token():
    """API эндпойнт для генерации временного токена для подписки на футбол."""
    import uuid
    try:
        # Генерируем уникальный токен
        token = str(uuid.uuid4())
        return jsonify({
            'success': True,
            'token': token
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка генерации токена: {e}")
        return jsonify({'error': f'Ошибка генерации токена: {str(e)}'}), 500

@app.route('/api/football/check-subscription', methods=['GET'])
def api_check_football_subscription():
    """API эндпойнт для проверки подписки по токену."""
    token = request.args.get('token')
    if not token:
        return jsonify({'subscribed': False, 'error': 'Нет токена'}), 400
    try:
        subscribed = football.is_football_subscribed_by_token(token)
        return jsonify({
            'subscribed': subscribed
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка проверки подписки: {e}")
        return jsonify({'subscribed': False, 'error': str(e)}), 500

@app.route('/api/football/recalculate-alt-confirm', methods=['GET', 'POST'])
def api_recalculate_alt_confirm():
    """API эндпойнт для пересчета bet_alt_confirm для всех матчей."""
    # Проверяем режим администратора
    admin = request.args.get('admin', 'false').lower() == 'true'
    if not admin:
        return jsonify({'error': 'Доступ запрещен. Используйте ?admin=true'}), 403
    
    try:
        result = football.recalculate_alt_bet_confirm()
        if result and 'error' not in result:
            return jsonify({
                'success': True,
                'message': 'Пересчет bet_alt_confirm завершен',
                'result': result
            }), 200
        else:
            error_msg = result.get('error', 'Неизвестная ошибка') if result else 'Неизвестная ошибка'
            return jsonify({'error': f'Ошибка пересчета bet_alt_confirm: {error_msg}'}), 500
    except Exception as e:
        print(f"[Football API] Ошибка пересчета bet_alt_confirm: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка пересчета bet_alt_confirm: {str(e)}'}), 500

@app.route('/api/football/recalculate-alt-odds', methods=['GET', 'POST'])
def api_recalculate_alt_odds():
    """API эндпойнт для пересчета коэффициентов альтернативных ставок (тоталы) (только для админа)."""
    # Проверяем, что это админ
    admin = request.args.get('admin') == 'true' or session.get('admin', False)
    if not admin:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        result = football.recalculate_alt_bet_odds_for_totals()
        if result is None:
            return jsonify({'error': 'Ошибка пересчета коэффициентов'}), 500
        
        return jsonify({
            'success': True,
            'updated': result['updated'],
            'reset': result['reset'],
            'total_processed': result['total_processed']
        }), 200
    except Exception as e:
        print(f"[Football API] Ошибка пересчета коэффициентов: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': f'Ошибка пересчета: {str(e)}'}), 500

@app.route('/api/football/export-excel', methods=['GET'])
def api_export_football_excel():
    """API эндпойнт для экспорта всех матчей в Excel (только для админа)."""
    # Проверяем, что это админ
    admin = request.args.get('admin') == 'true' or session.get('admin', False)
    if not admin:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    try:
        # Получаем параметры фильтра по дате
        date_filter = request.args.get('date_filter', 'today')  # По умолчанию 'today'
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        match_type = request.args.get('match_type', 'fav')  # 'fav' или 'all'
        
        # Получаем смещение часового пояса пользователя (в минутах)
        timezone_offset_str = request.args.get('timezone_offset')
        timezone_offset = None
        if timezone_offset_str:
            try:
                timezone_offset = int(timezone_offset_str)
            except ValueError:
                pass
        
        excel_file = football.export_matches_to_excel(
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to,
            match_type=match_type,
            timezone_offset=timezone_offset
        )
        if excel_file is None:
            return jsonify({'error': 'Ошибка создания Excel файла'}), 500
        
        # Генерируем имя файла с текущей датой
        from datetime import datetime
        filename = f"football_matches_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"[Football API] Ошибка экспорта в Excel: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': f'Ошибка экспорта: {str(e)}'}), 500

# --- НОВЫЕ МАРШРУТЫ ДЛЯ МЕДИАФАЙЛОВ ---

@app.route('/files', methods=['GET'])
def list_media_files():
    """Отображает страницу со списком загруженных медиафайлов."""
    files = []
    if os.path.exists(MEDIA_DIR):
        files = [f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR, f))]
    
    from urllib.parse import unquote
    def url_decode(s):
        return unquote(s)

    return render_template('files.html', files=files, url_decode=url_decode)

@app.route('/files/upload', methods=['POST'])
def upload_media_file():
    """Обрабатывает загрузку медиафайла."""
    if 'media_file' not in request.files:
        from flask import flash
        flash('Файл не найден', 'danger')
        return redirect(url_for('list_media_files'))
    
    file = request.files['media_file']
    if file.filename == '':
        from flask import flash
        flash('Файл не выбран', 'danger')
        return redirect(url_for('list_media_files'))

    if file:
        from urllib.parse import quote
        filename = quote(file.filename)

        # Проверка расширения
        check_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        media_exts = {'png', 'jpg', 'jpeg', 'gif', 'mp3', 'wav', 'mp4', 'mov', 'avi', 'mkv', 'webp'}
        
        if check_ext not in media_exts:
            from flask import flash
            flash(f'Неподдерживаемый тип файла: {check_ext}', 'danger')
            return redirect(url_for('list_media_files'))

        file_path = os.path.join(MEDIA_DIR, filename)
        file.save(file_path)
        from flask import flash
        flash(f'Файл {filename} успешно загружен', 'success')
        return redirect(url_for('list_media_files'))

@app.route('/media/<path:filename>')
def download_media_file(filename):
    """Эндпойнт для скачивания медиафайлов."""
    from urllib.parse import unquote
    # unquote не нужна для send_from_directory, Flask сам это делает для переменных пути,
    # но используем path: тип для поддержки закодированных слешей и спецсимволов
    return send_from_directory(MEDIA_DIR, filename)

@app.route('/files/delete/<path:filename>', methods=['POST'])
def delete_media_file(filename):
    """Эндпойнт для удаления медиафайла."""
    file_path = os.path.join(MEDIA_DIR, filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            from flask import flash
            flash(f'Файл {filename} успешно удален', 'success')
        except Exception as e:
            from flask import flash
            flash(f'Ошибка при удалении файла: {e}', 'danger')
    else:
        from flask import flash
        flash('Файл не найден', 'warning')
    return redirect(url_for('list_media_files'))

@app.errorhandler(413)
def request_entity_too_large(error):
    """Обработчик ошибки превышения размера файла."""
    from flask import flash
    flash('Ошибка: Файл слишком большой. Максимальный размер — 50 МБ.', 'danger')
    return redirect(url_for('list_media_files'))

# --- КОНЕЦ МАРШРУТОВ ДЛЯ МЕДИАФАЙЛОВ ---

# --- Запуск приложения ---
if __name__ == '__main__':
    # Проверяем среду запуска
    is_fly_io = os.getenv("FLY_APP_NAME") is not None
    environment = "🚀 PRODUCTION (fly.io)" if is_fly_io else "🏠 LOCAL"
    
    print(f"Запуск Flask приложения... {environment}")
    print("=" * 60)
    
    # use_reloader=False рекомендуется при использовании APScheduler в режиме отладки,
    # чтобы избежать двойного запуска планировщика. Но можно попробовать и без него.
    try:
        configure_api() # Проверка ключей API
        load_models_on_startup() # <-- ДОБАВЛЯЕМ ЭТОТ ВЫЗОВ
        
        # Запускаем Telegram бота
        start_telegram_bot()

        print(f"\n🌐 Веб-сервер запускается на http://0.0.0.0:5000")
        if is_fly_io:
            print("📋 Фоновые задачи активны: поиск локаций, анализ видео")
        else:
            print("📋 Фоновые задачи отключены (локальный режим)")
        
        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    except ValueError as e:
        print(f"❌ Ошибка конфигурации API: {e}")
        # Возможно, стоит явно выйти из приложения или как-то иначе сообщить об ошибке
        exit(1)

# --- END OF FILE app.py ---
