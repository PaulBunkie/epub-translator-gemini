# --- START OF FILE app.py ---

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
    jsonify, send_from_directory, Response, session, g, send_file
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
    configure_api, translate_text, CONTEXT_LIMIT_ERROR, get_models_list
)
from epub_parser import (
    get_epub_structure, extract_section_text, get_epub_toc
)
from cache_manager import (
    get_translation_from_cache, save_translation_to_cache, save_translated_chapter,
    delete_section_cache, delete_book_cache, _get_epub_id
)
import alice_handler

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
              if status in ["translated", "completed_empty", "cached"]: translated_count += 1
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
def run_single_section_translation(task_id, epub_filepath, book_id, section_id, target_language, model_name, prompt_ext):
    """ Выполняется в отдельном потоке для перевода одной секции. """
    print(f"Фоновая задача {task_id}: Старт перевода {section_id} ({book_id}) моделью '{model_name}' на '{target_language}'.")
    print(f"  [BG Task] Используется prompt_ext длиной: {len(prompt_ext) if prompt_ext else 0}")
    current_status = "error_unknown"; error_message = None
    try:
        if task_id in active_tasks: active_tasks[task_id]["status"] = "extracting"
        original_text = extract_section_text(epub_filepath, section_id)
        if not original_text or not original_text.strip():
            print(f"Фоновая задача {task_id}: Текст пуст для {section_id}.")
            current_status = "completed_empty"
            save_translation_to_cache(epub_filepath, section_id, target_language, "")
        else:
            if task_id in active_tasks: active_tasks[task_id]["status"] = "translating"
            api_result = translate_text(original_text, target_language, model_name, prompt_ext=prompt_ext)
            if api_result == CONTEXT_LIMIT_ERROR: current_status = "error_context_limit"; error_message = "Текст раздела слишком велик."
            elif api_result is not None:
                 if task_id in active_tasks: active_tasks[task_id]["status"] = "caching"
                 if save_translation_to_cache(epub_filepath, section_id, target_language, api_result): current_status = "translated"
                 else: current_status = "error_caching"; error_message = "Не удалось сохранить в кэш."
            else: current_status = "error_translation"; error_message = "Ошибка API перевода или фильтр."
        update_section_status(book_id, section_id, current_status, model_name, target_language, error_message)
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА в фоновой задаче {task_id}: {e}"); traceback.print_exc()
        error_message = f"BG Task Error: {e}"; current_status = "error_unknown"
        update_section_status(book_id, section_id, current_status, model_name, target_language, error_message)
    finally:
        if task_id in active_tasks:
             active_tasks[task_id]["status"] = current_status
             if error_message: active_tasks[task_id]["error_message"] = error_message
        print(f"Фоновая задача {task_id}: Завершена ({section_id}) со статусом {current_status}")
        update_overall_book_status(book_id)


# --- Маршруты Flask ---
@app.route('/', methods=['GET'])
def index():
    """ Отображает главную страницу со списком книг. """
    print("Загрузка главной страницы...")
    default_language = session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'gemini-1.5-flash')
    print(f"  Параметры сессии: lang='{default_language}', model='{selected_model}'")
    available_models = get_models_list()
    if not available_models: available_models = list(set([selected_model, 'gemini-1.5-flash'])); print("  WARN: Не удалось получить список моделей от API.")
    active_ids = [(info['book_id'], info['section_id']) for info in active_tasks.values() if info.get('status') in ['queued', 'extracting', 'translating', 'caching']]
    reset_stuck_processing_sections(active_processing_sections=active_ids)
    uploaded_books = []
    try:
        db_books = get_all_books()
        for book_data in db_books: uploaded_books.append({'book_id': book_data['book_id'], 'display_name': book_data['filename'], 'status': book_data['status'], 'total_sections': get_section_count_for_book(book_data['book_id']), 'default_language': default_language})
        uploaded_books.sort(key=lambda x: x['display_name'].lower())
        print(f"  Найдено книг в БД: {len(uploaded_books)}")
    except Exception as e: print(f"ОШИБКА при получении списка книг: {e}"); traceback.print_exc()
    return render_template('index.html', uploaded_books=uploaded_books, default_language=default_language, selected_model=selected_model, available_models=available_models)

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
             toc_model = session.get('model_name', 'gemini-1.5-flash')
             titles_text = "\n|||---\n".join(toc_titles_for_translation)
             translated_titles_text = translate_text(titles_text, target_language, toc_model, prompt_ext=None)
             if translated_titles_text and translated_titles_text != CONTEXT_LIMIT_ERROR:
                  translated_titles = translated_titles_text.split("\n|||---\n")
                  if len(translated_titles) == len(toc_titles_for_translation):
                       for i, item in enumerate(toc):
                            if item.get('title') and item.get('id'): translated_toc_titles[item['id']] = translated_titles[i].strip() if translated_titles[i] else None
                       print("  Оглавление переведено.")
                  else: print(f"  ОШИБКА: Не совпало количество названий TOC.")
             else: print("  ОШИБКА: Не удалось перевести оглавление.")

        if create_book(book_id, original_filename, filepath, toc):
             print(f"  Книга '{book_id}' сохранена в БД.")
             sec_created_count = 0
             if section_ids:
                  for section_id in section_ids:
                       if section_id and create_section(book_id, section_id, translated_title=translated_toc_titles.get(section_id)): sec_created_count += 1
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
    if book_info is None: print(f"  Книга {book_id} не найдена."); return "Книга не найдена.", 404
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'gemini-1.5-flash')
    session['target_language'] = target_language
    print(f"  Параметры для отображения: lang='{target_language}', model='{selected_model}'")
    available_models = get_models_list()
    if not available_models: available_models = list(set([selected_model, 'gemini-1.5-flash'])); print("  WARN: Не удалось получить список моделей.")
    prompt_ext_text = book_info.get('prompt_ext', '')
    return render_template('book_view.html', book_id=book_id, book_info=book_info, target_language=target_language, selected_model=selected_model, available_models=available_models, prompt_ext=prompt_ext_text)

@app.route('/save_prompt_ext/<book_id>', methods=['POST'])
def save_prompt_ext(book_id):
    print(f"Запрос на сохранение prompt_ext для книги: {book_id}")
    if not request.is_json: print("  Ошибка: Запрос не JSON."); return jsonify({"success": False, "error": "Request must be JSON"}), 400
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
        print(f"  [DEBUG] 3.1. Параметры получены: lang={target_language}, model={model_name}")
    except Exception as e:
        print(f"  [DEBUG] 3.2. ОШИБКА получения параметров: {e}")
        return jsonify({"error": f"Invalid JSON payload: {e}"}), 400

    session['target_language'] = target_language; session['model_name'] = model_name

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
    executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name, prompt_ext_text)
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
    except Exception as e: print(f"  Ошибка получения параметров: {e}"); return jsonify({"error": f"Invalid JSON payload: {e}"}), 400
    session['target_language'] = target_language; session['model_name'] = model_name
    sections_list = book_info.get('sections', {})
    if not sections_list: return jsonify({"error": "No sections found"}), 404
    prompt_ext_text = book_info.get('prompt_ext', '')
    print(f"  Параметры: lang='{target_language}', model='{model_name}', prompt_ext len: {len(prompt_ext_text)}")
    launched_tasks = []; something_launched = False
    for section_id, section_data in sections_list.items():
        current_status = section_data['status']
        if current_status not in ['translated', 'completed_empty', 'processing', 'cached'] and not current_status.startswith('error_'):
            if not get_translation_from_cache(filepath, section_id, target_language):
                task_id = str(uuid.uuid4()); active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
                update_section_status(book_id, section_id, "processing")
                executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name, prompt_ext_text)
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
    # --- КОММЕНТИРУЕМ ВЫЗОВ ПЕРЕСЧЕТА СТАТУСА ---
    # update_overall_book_status(book_id) # Обновляем статус перед отдачей
    # ---

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
    filepath = book_info.get("filepath"); target_language = request.args.get('lang') or session.get('target_language', 'russian')
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
    filepath = book_info.get("filepath"); target_language = request.args.get('lang') or session.get('target_language', 'russian')
    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None:
        safe_id = "".join(c for c in section_id if c.isalnum() or c in ('_','-')).rstrip(); filename = f"{safe_id}_{target_language}.txt"
        return Response(translation, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})
    else: return "Translation not found", 404

@app.route('/download_full/<book_id>', methods=['GET'])
def download_full(book_id):
    book_info = get_book(book_id);
    if book_info is None: return "Book not found", 404
    filepath = book_info.get("filepath"); target_language = request.args.get('lang') or session.get('target_language', 'russian')
    section_ids = book_info.get("section_ids_list", [])
    if not section_ids: section_ids = list(book_info.get('sections', {}).keys());
    if not section_ids: return "No sections found", 500
    update_overall_book_status(book_id); book_info = get_book(book_id)
    if book_info.get('status') not in ["complete", "complete_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409
    full_text_parts = []; missing = []; errors = []; sections_status = book_info.get('sections', {})
    for id in section_ids: data = sections_status.get(id, {}); status = data.get('status', '?'); tr = get_translation_from_cache(filepath, id, target_language);
    if tr is not None: full_text_parts.extend([f"\n\n==== {id} ({status}) ====\n\n", tr])
    elif status.startswith("error_"): errors.append(id); full_text_parts.append(f"\n\n==== {id} (ОШИБКА: {data.get('error_message', status)}) ====\n\n")
    else: missing.append(id); full_text_parts.append(f"\n\n==== {id} (ОШИБКА: Нет кэша {target_language}) ====\n\n")
    if not full_text_parts: return f"Нет текста для '{target_language}'.", 404
    if missing: full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Нет кэша {target_language} для: {', '.join(missing)}\n\n")
    if errors: full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Ошибки в секциях: {', '.join(errors)}\n\n")
    full_text = "".join(full_text_parts); base_name = os.path.splitext(book_info['filename'])[0]; out_fn = f"{base_name}_{target_language}_translated.txt"
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
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    update_overall_book_status(book_id); book_info = get_book(book_id)
    if book_info.get('status') not in ["complete", "complete_with_errors"]: return f"Перевод не завершен (Статус: {book_info.get('status')}).", 409
    epub_bytes = create_translated_epub(book_info, target_language) # book_info уже содержит 'sections'
    if epub_bytes is None: return "Server error generating EPUB", 500
    base_name = os.path.splitext(book_info.get('filename', 'tr_book'))[0]; out_fn = f"{base_name}_{target_language}_translated.epub"
    return send_file(io.BytesIO(epub_bytes), mimetype='application/epub+zip', as_attachment=True, download_name=out_fn)

def get_bbc_news():
    """Получает заголовки новостей BBC с NewsAPI."""
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

# --- Запуск приложения ---
if __name__ == '__main__':
    print("Запуск Flask приложения...")
    # use_reloader=False рекомендуется при использовании APScheduler в режиме отладки,
    # чтобы избежать двойного запуска планировщика. Но можно попробовать и без него.
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)

# --- END OF FILE app.py ---