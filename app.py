import os
import uuid
import json
from flask import Flask, request, render_template, redirect, url_for, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor
import time
import hashlib 
from cache_manager import delete_book_cache # Импортируем удаление кэша книги

from flask import send_file
import io

from epub_creator import create_translated_epub
# from epub_creator_test import create_translated_epub # <-- Используем тестовую версию

# Импортируем наши модули
from translation_module import configure_api, translate_text, CONTEXT_LIMIT_ERROR, get_models_list
# Импортируем новые и старые функции парсера
from epub_parser import get_epub_structure, extract_section_text, get_epub_toc
from cache_manager import (
    get_translation_from_cache,
    save_translation_to_cache,
    save_translated_chapter, # Используем для сохранения полного файла
    delete_section_cache,    # <--- ДОБАВИТЬ ЭТУ СТРОКУ
    # delete_book_cache,     # Пока не используем
    _get_epub_id
)
import hashlib # Добавим hashlib для хэша

# --- Настройки ---
UPLOAD_FOLDER = 'uploads'
CACHE_DIR = ".epub_cache"
FULL_TRANSLATION_DIR = ".translated"
ALLOWED_EXTENSIONS = {'epub'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.urandom(24)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(FULL_TRANSLATION_DIR, exist_ok=True)

try:
    configure_api()
except ValueError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: {e}. Приложение не сможет выполнять перевод.")

# --- Управление фоновыми задачами и прогрессом ---
executor = ThreadPoolExecutor(max_workers=3)
active_tasks = {} # {task_id: {"status": ..., "book_id": ..., "section_id": ...}}
# Теперь book_progress будет хранить больше информации
# {book_id: {"filename": ..., "filepath": ..., "total_sections": N, "translated_count": N, "error_count": N, "status": ...,
#             "sections": {section_id: "status"}, "section_ids_list": [...], "toc": [...]}}
book_progress = {}

# --- Вспомогательные функции ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_book_id(epub_filepath):
    return _get_epub_id(epub_filepath)

def update_book_section_status(book_id, section_id, status, error_msg=None):
    """ Обновляет статус конкретной секции и общий статус книги """
    if book_id in book_progress:
         if section_id in book_progress[book_id]["sections"]:
              book_progress[book_id]["sections"][section_id] = status
              if error_msg:
                   # Можно добавить поле для ошибки секции, если нужно
                   book_progress[book_id]["sections"][f"{section_id}_error"] = error_msg
              update_overall_book_status(book_id) # Обновляем общий статус
         else:
              print(f"Предупреждение: Попытка обновить статус для неизвестной секции {section_id} книги {book_id}")

def update_overall_book_status(book_id):
    """ Пересчитывает и обновляет общий статус книги, используя только статус 'translated' """
    if book_id in book_progress:
        book_data = book_progress[book_id]
        translated_count = 0
        error_count = 0
        processing_count = 0
        total = len(book_data.get("section_ids_list", []))
        book_data["total_sections"] = total

        for section_id in book_data.get("section_ids_list", []):
             status = book_data["sections"].get(section_id, "not_translated")
             # --- ИСПОЛЬЗУЕМ ТОЛЬКО 'translated' ---
             if status in ["translated", "completed_empty"]: # Считаем переведенные (включая пустые)
                 translated_count += 1
             elif status == "processing":
                  processing_count += 1
             elif status.startswith("error_"):
                  error_count +=1
             # Статус 'cached' больше не используется для подсчета отдельно

        book_data["translated_count"] = translated_count # Теперь это общее число готовых
        book_data["error_count"] = error_count

        # Определяем общий статус
        if processing_count > 0:
             book_data["status"] = "processing"
        elif (translated_count + error_count) == total and total > 0 and processing_count == 0:
             book_data["status"] = "complete" if error_count == 0 else "complete_with_errors"
        elif total == 0:
             book_data["status"] = "error_no_sections"
        else:
             book_data["status"] = "idle"

# --- Фоновая задача ---
def run_single_section_translation(task_id, epub_filepath, book_id, section_id, target_language, model_name):
    """ Выполняется в отдельном потоке для перевода одной секции """
    print(f"Фоновая задача {task_id}: Начало перевода {section_id} для {book_id}")
    current_status = "error_unknown"
    error_message = None
    try:
        if task_id in active_tasks: active_tasks[task_id]["status"] = "extracting"
        # Статус раздела уже должен быть "processing" из вызывающей функции
        # update_book_section_status(book_id, section_id, "processing") # Можно убрать?
        original_text = extract_section_text(epub_filepath, section_id)

        if not original_text:
            print(f"Фоновая задача {task_id}: Извлеченный текст пуст для {section_id}.")
            current_status = "completed_empty"
            save_translation_to_cache(epub_filepath, section_id, target_language, "")
        else:
            if task_id in active_tasks: active_tasks[task_id]["status"] = "translating"
            api_result = translate_text(original_text, target_language, model_name)

            if api_result == CONTEXT_LIMIT_ERROR:
                print(f"Фоновая задача {task_id}: Ошибка лимита контекста для {section_id}.")
                current_status = "error_context_limit"
                error_message = "Текст раздела слишком велик для модели."
            elif api_result is not None:
                 if task_id in active_tasks: active_tasks[task_id]["status"] = "caching"
                 if save_translation_to_cache(epub_filepath, section_id, target_language, api_result):
                     # --- ИСПОЛЬЗУЕМ СТАТУС 'translated' ---
                     current_status = "translated"
                 else:
                     current_status = "error_caching"
                     error_message = "Не удалось сохранить в кэш."
            else:
                 print(f"Фоновая задача {task_id}: Ошибка перевода для {section_id}.")
                 current_status = "error_translation"
                 error_message = "Ошибка при вызове API перевода."

        # Обновляем статус секции в book_progress
        update_book_section_status(book_id, section_id, current_status, error_message)

    except Exception as e:
        print(f"Критическая ошибка в фоновой задаче {task_id}: {e}")
        error_message = str(e)
        current_status = "error_unknown"
        update_book_section_status(book_id, section_id, current_status, error_message)
    finally:
        # Обновляем финальный статус задачи в active_tasks
        if task_id in active_tasks:
             active_tasks[task_id]["status"] = current_status
             if error_message: active_tasks[task_id]["error_message"] = error_message
        print(f"Фоновая задача {task_id}: Завершена со статусом {current_status}")


# --- Маршруты Flask ---

@app.route('/', methods=['GET'])
def index():
    """ Отображает главную страницу с формой загрузки и списком загруженных файлов ИЗ ПАМЯТИ. """
    uploaded_books = [] # Список для передачи в шаблон
    # --- ИЗМЕНЕНИЕ: Вместо сканирования папки, берем из словаря book_progress ---
    print(f"Загрузка списка книг из book_progress (в памяти). Всего ключей: {len(book_progress)}") # Отладка
    try:
        # Итерируем по словарю в памяти
        for book_id, data in book_progress.items():
             print(f"  Найден book_id в памяти: {book_id}") # Отладка
             uploaded_books.append({
                 'book_id': book_id,
                 'display_name': data.get('filename', book_id + ".epub"), # Используем сохраненное имя файла
                 'status': data.get('status', 'N/A'), # Берем статус из памяти
                 'total_sections': data.get('total_sections', 0),
                 'default_language': data.get('default_language', 'russian')
             })
        # Сортируем по имени файла
        uploaded_books.sort(key=lambda x: x['display_name'].lower())
        print(f"Сформирован список uploaded_books: {len(uploaded_books)} книг") # Отладка

    except Exception as e:
        print(f"Ошибка при формировании списка книг из book_progress: {e}")
        import traceback
        traceback.print_exc() # Печатаем traceback ошибки

    print(f"Передача в шаблон index.html: {uploaded_books}") # Отладка
    # Передаем ИМЕННО uploaded_books
    return render_template('index.html', uploaded_books=uploaded_books)

@app.route('/delete_book/<book_id>', methods=['POST'])
def delete_book_request(book_id):
    """ Удаляет книгу, ее файл и кэш """
    if book_id in book_progress:
        book_data = book_progress[book_id]
        filepath = book_data.get("filepath")

        print(f"Удаление книги ID: {book_id}, файл: {filepath}")

        # 1. Удаляем файл из uploads
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"Удален файл: {filepath}")
            except OSError as e:
                print(f"Ошибка удаления файла {filepath}: {e}")
                # Продолжаем удаление остального

        # 2. Удаляем кэш
        if filepath: # Нужен путь для генерации ID в delete_book_cache
             delete_book_cache(filepath) # Функция из cache_manager

        # 3. Удаляем запись из памяти
        del book_progress[book_id]
        print(f"Удалена запись о книге {book_id} из памяти.")

        # Можно добавить flash сообщение об успехе
        # flash(f"Книга '{book_data.get('filename', book_id)}' успешно удалена.")

    else:
        print(f"Попытка удаления несуществующей книги ID: {book_id}")
        # Можно добавить flash сообщение об ошибке
        # flash(f"Книга с ID {book_id} не найдена.", "error")

    return redirect(url_for('index')) # Возвращаемся на главную

@app.route('/upload', methods=['POST'])
def upload_file():
    """ Обрабатывает загрузку EPUB файла и переводит оглавление """
    if 'epub_file' not in request.files: return "Файл не найден", 400
    file = request.files['epub_file']
    if file.filename == '': return "Файл не выбран", 400

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        file.seek(0)
        file_content = file.read()
        file.seek(0)
        hasher = hashlib.md5()
        hasher.update(file_content)
        book_id = hasher.hexdigest()
        unique_filename = f"{book_id}.epub"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

        try:
            if not os.path.exists(filepath):
                 with open(filepath, 'wb') as f: f.write(file_content)
                 print(f"Файл сохранен: {filepath}")
            else: print(f"Файл с ID (хэшем) {book_id} уже существует: {filepath}")

            if book_id in book_progress:
                 print(f"Найден существующий прогресс для книги ID: {book_id}")
                 return redirect(url_for('view_book', book_id=book_id))
            else:
                 print(f"Чтение структуры для новой книги ID: {book_id}")
                 section_ids, id_to_href_map = get_epub_structure(filepath)
                 if section_ids is None: raise ValueError("Не удалось получить структуру EPUB (spine).")

                 print("Вызов get_epub_toc...")
                 toc = get_epub_toc(filepath, id_to_href_map) or []

                 # --- НОВЫЙ БЛОК: ПЕРЕВОД ОГЛАВЛЕНИЯ ---
                 target_language = request.form.get('target_language', 'russian') # Или 'russian'
                 print(f"Перевод оглавления на язык: {target_language}...")
                 toc_titles_for_translation = [item['title'] for item in toc if item.get('title')] # Извлекаем только названия
                 if toc_titles_for_translation:
                      # Соединяем названия в одну строку с разделителем
                      titles_text = "\n|||---\n".join(toc_titles_for_translation) # Уникальный разделитель
                      translated_titles_text = translate_text(titles_text, target_language, "gemini-1.5-flash") # Или model из формы?

                      if translated_titles_text:
                           translated_titles = translated_titles_text.split("\n|||---\n") # Разделяем обратно в список
                           if len(translated_titles) == len(toc_titles_for_translation):
                                # Обновляем TOC, добавляя translated_title
                                for i, item in enumerate(toc):
                                     if item.get('title'): # Проверяем на None на всякий случай
                                          item['translated_title'] = translated_titles[i] # Добавляем переведенное название
                                print("Оглавление переведено и добавлено в структуру.")
                           else:
                                print(f"ОШИБКА: Количество переведенных названий TOC ({len(translated_titles)}) не совпадает с оригинальным ({len(toc_titles_for_translation)}). Используем оригинальные названия.")
                      else:
                           print("ОШИБКА: Не удалось перевести оглавление. Используем оригинальные названия.")
                 else:
                      print("Оглавление не содержит названий для перевода.")
                 # --- КОНЕЦ БЛОКА ПЕРЕВОДА ОГЛАВЛЕНИЯ ---


                 print(f"Инициализация прогресса для книги ID: {book_id}")
                 book_progress[book_id] = {
                     "filename": original_filename,
                     "filepath": filepath,
                     "total_sections": len(section_ids) if section_ids else 0,
                     "translated_count": 0,
                     "error_count": 0,
                     "status": "idle",
                     "sections": {section_id: "not_translated" for section_id in section_ids} if section_ids else {},
                     "section_ids_list": section_ids if section_ids else [],
                     "toc": toc # Сохраняем обновленный TOC (с translated_title)
                 }
                 initial_check_cache(book_id, target_language)
                 return redirect(url_for('view_book', book_id=book_id, lang=target_language)) # Передаем язык в URL


        except Exception as e:
            print(f"Ошибка при сохранении или обработке файла: {e}")
            if os.path.exists(filepath): os.remove(filepath)
            if isinstance(e, ValueError) and "структуру EPUB" in str(e):
                 return "Ошибка: Не удалось прочитать структуру EPUB.", 400
            else:
                 import traceback
                 traceback.print_exc()
                 return "Ошибка сервера при обработке файла.", 500
    else:
        return "Ошибка: Недопустимый тип файла.", 400

def initial_check_cache(book_id, target_language):
     if book_id in book_progress:
          filepath = book_progress[book_id].get('filepath')
          if not filepath: return # Если нет пути к файлу
          # Итерируем по копии ключей, чтобы избежать ошибок при изменении словаря
          section_ids_to_check = list(book_progress[book_id].get('sections', {}).keys())
          for section_id in section_ids_to_check:
               # Проверяем только те, что еще не переведены или без статуса
               current_status = book_progress[book_id]['sections'].get(section_id)
               if current_status == 'not_translated' or current_status is None:
                    if get_translation_from_cache(filepath, section_id, target_language) is not None:
                         # --- ИСПОЛЬЗУЕМ СТАТУС 'translated' ---
                         update_book_section_status(book_id, section_id, "translated")
          update_overall_book_status(book_id) # Обновляем общий статус


@app.route('/book/<book_id>', methods=['GET'])
def view_book(book_id):
    """ Отображает страницу с оглавлением книги и статусом перевода """
    if book_id not in book_progress:
        # Попытка загрузить из uploads, если в памяти нет (на случай перезапуска)
        # TODO: Реализовать более надежное восстановление состояния из БД/файлов
        return "Ошибка: Книга с таким ID не найдена в текущей сессии.", 404

    book_info = book_progress[book_id]
    target_language = request.args.get('lang', 'russian') # Получаем язык для проверки кэша
    initial_check_cache(book_id, target_language) # Обновляем статусы из кэша перед отображением

    return render_template('book_view.html', book_id=book_id, book_info=book_info, target_language=target_language)


# --- Эндпоинты для AJAX ---

@app.route('/translate_section/<book_id>/<section_id>', methods=['POST'])
def translate_section_request(book_id, section_id):
    """ Запускает фоновый перевод для одной секции, ПРЕДВАРИТЕЛЬНО УДАЛЯЯ КЭШ """
    if book_id not in book_progress:
        return jsonify({"error": "Book not found"}), 404

    filepath = book_progress[book_id].get("filepath")
    if not filepath or not os.path.exists(filepath):
         return jsonify({"error": "EPUB file not found"}), 404

    target_language = request.json.get('target_language', 'russian')
    model_name = request.json.get('model_name', 'gemini-1.5-flash')

    current_status = book_progress[book_id]["sections"].get(section_id)
    if current_status == 'processing':
         return jsonify({"status": "already_processing", "message": "Раздел уже в процессе перевода."}), 409

    # !!! 1. УДАЛЯЕМ СТАРЫЙ КЭШ !!!
    print(f"Попытка удалить кэш для {section_id} ({target_language}) перед переводом...")
    # Теперь функция должна быть доступна
    delete_section_cache(filepath, section_id, target_language)
    # Мы не проверяем результат delete_section_cache, т.к. она возвращает True, даже если файла не было

    # !!! 2. ЗАПУСКАЕМ ФОНОВУЮ ЗАДАЧУ !!!
    task_id = str(uuid.uuid4())
    active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
    update_book_section_status(book_id, section_id, "processing")
    executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name)
    print(f"Запущена задача {task_id} для перевода (обновления) {section_id}")

    return jsonify({"status": "processing", "task_id": task_id}), 202


@app.route('/translate_all/<book_id>', methods=['POST'])
def translate_all_request(book_id):
    """ Запускает фоновый перевод для ВСЕХ непереведенных секций """
    # ... (Код остается таким же, как в предыдущем ответе, но использует section_ids_list) ...
    if book_id not in book_progress:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_progress[book_id].get("filepath")
    if not filepath or not os.path.exists(filepath):
         return jsonify({"error": "EPUB file not found"}), 404
    target_language = request.json.get('target_language', 'russian')
    model_name = request.json.get('model_name', 'gemini-1.5-flash')
    section_ids = book_progress[book_id].get("section_ids_list", []) # Берем порядок из spine
    if not section_ids:
         return jsonify({"error": "No sections found for this book"}), 500
    launched_tasks = []
    for section_id in section_ids:
        current_status = book_progress[book_id]["sections"].get(section_id, "not_translated")
        if current_status not in ['cached', 'translated', 'processing', 'completed_empty'] and not current_status.startswith('error_'):
            if not get_translation_from_cache(filepath, section_id, target_language):
                task_id = str(uuid.uuid4())
                active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
                update_book_section_status(book_id, section_id, "processing")
                executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name)
                launched_tasks.append(task_id)
            else:
                 update_book_section_status(book_id, section_id, "cached")
    print(f"Запущено {len(launched_tasks)} задач для 'Перевести все' для книги {book_id}")
    return jsonify({"status": "processing_all", "launched_tasks": len(launched_tasks)}), 202


@app.route('/task_status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """ Возвращает статус конкретной фоновой задачи """
    # ... (Код остается без изменений) ...
    task_info = active_tasks.get(task_id)
    if task_info:
        return jsonify(task_info)
    else:
        return jsonify({"status": "not_found_or_completed"}), 404


@app.route('/book_status/<book_id>', methods=['GET'])
def get_book_status(book_id):
    """ Возвращает общий статус книги и статусы секций """
    # ... (Код остается таким же, как в предыдущем ответе, но использует обновленный book_progress) ...
    if book_id in book_progress:
         update_overall_book_status(book_id) # Пересчитываем на всякий случай
         # Возвращаем только нужную информацию для фронтенда
         book_data = book_progress[book_id]
         return jsonify({
              "filename": book_data.get("filename"),
              "total_sections": book_data.get("total_sections"),
              "translated_count": book_data.get("translated_count"),
              "error_count": book_data.get("error_count"),
              "status": book_data.get("status"),
              "sections": book_data.get("sections", {}), # Статусы всех секций
              "toc": book_data.get("toc", []) # Добавляем TOC для возможного обновления
         })
    else:
         return jsonify({"error": "Book not found"}), 404


@app.route('/get_translation/<book_id>/<section_id>', methods=['GET'])
def get_section_translation_text(book_id, section_id):
    """ Возвращает переведенный текст секции из кэша (для отображения) """
    # ... (Код остается без изменений) ...
    if book_id not in book_progress:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_progress[book_id].get("filepath")
    target_language = request.args.get('lang', 'russian')
    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None:
        return jsonify({"text": translation})
    else:
        # Если нет в кэше, проверяем статус - может, ошибка?
        section_status = book_progress[book_id]["sections"].get(section_id, "not_translated")
        if section_status.startswith("error_"):
             return jsonify({"error": f"Translation failed: {section_status}"}), 404 # Можно вернуть код ошибки
        else:
             return jsonify({"error": "Translation not found or not ready"}), 404


# --- Эндпоинты для скачивания ---

@app.route('/download_section/<book_id>/<section_id>', methods=['GET'])
def download_section(book_id, section_id):
    """ Отдает переведенный текст секции как файл """
    # ... (Код остается без изменений) ...
    if book_id not in book_progress: return "Book not found", 404
    filepath = book_progress[book_id].get("filepath")
    target_language = request.args.get('lang', 'russian')
    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None:
        safe_section_id = "".join(c for c in section_id if c.isalnum() or c in ('_', '-')).rstrip()
        filename = f"{safe_section_id}_{target_language}.txt"
        return Response(translation, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})
    else: return "Translation not found", 404


@app.route('/download_full/<book_id>', methods=['GET'])
def download_full(book_id):
    if book_id not in book_progress: return "Book not found", 404
    filepath = book_progress[book_id].get("filepath")
    target_language = request.args.get('lang', 'russian')
    book_info = book_progress[book_id]

    # Пересчитываем статус ПЕРЕД проверкой
    update_overall_book_status(book_id)
    print(f"Статус книги перед скачиванием полного файла: {book_info['status']}") # Отладка

    # Разрешаем скачивать, если статус 'complete' или 'complete_with_errors'
    if book_info['status'] not in ["complete", "complete_with_errors"]:
         print("Отказ в скачивании: перевод книги еще не завершен.")
         # Возвращаем более понятную ошибку для пользователя
         missing_sections_count = book_info['total_sections'] - (book_info.get('translated_count', 0) + book_info.get('error_count', 0))
         processing_count = 0 # Посчитать заново, если нужно точнее
         for status in book_info['sections'].values():
              if status == 'processing': processing_count+=1
         message = f"Перевод книги не завершен. Осталось: {missing_sections_count}. В процессе: {processing_count}."
         return message, 409 # 409 Conflict - состояние не позволяет выполнить

    section_ids = book_info.get("section_ids_list", [])
    if not section_ids: return "No sections found for this book", 500

    full_text_parts = []
    missing_for_download = [] # Секции, которые должны быть, но нет в кэше (странно)
    error_sections_included = [] # Секции с ошибками, текст которых не будет включен

    print("Сборка полного текста из кэша...")
    for section_id in section_ids:
        section_status = book_info["sections"].get(section_id, "not_translated")
        translation = get_translation_from_cache(filepath, section_id, target_language)

        if translation is not None: # Есть в кэше (даже пустой)
             full_text_parts.append(f"\n\n==== Section: {section_id} ({section_status}) ====\n\n")
             full_text_parts.append(translation)
        elif section_status.startswith("error_"):
             # Если была ошибка, добавляем сообщение об этом
             full_text_parts.append(f"\n\n==== Section: {section_id} (ОШИБКА: {section_status}) ====\n\n")
             error_sections_included.append(section_id)
        else:
             # Странно: статус complete, а кэша нет
             print(f"Предупреждение: Статус книги '{book_info['status']}', но кэш для секции '{section_id}' (статус: {section_status}) не найден!")
             missing_for_download.append(section_id)
             full_text_parts.append(f"\n\n==== Section: {section_id} (ОШИБКА: Перевод отсутствует в кэше) ====\n\n")


    if not full_text_parts:
         return "Не найдено переведенного текста для скачивания.", 404

    # Добавляем предупреждения в начало, если были проблемы
    if missing_for_download:
         full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Не найден кэш для секций: {', '.join(missing_for_download)}\n\n")
    if error_sections_included:
          full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Следующие секции не были переведены из-за ошибок: {', '.join(error_sections_included)}\n\n")


    full_text = "".join(full_text_parts)
    base_name = os.path.splitext(book_info['filename'])[0]
    output_filename = f"{base_name}_{target_language}_translated.txt"

    print(f"Отправка полного файла: {output_filename}")
    return Response(
        full_text,
        mimetype="text/plain; charset=utf-8", # Добавляем charset
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"} # Кодируем имя файла
    )

# --- НОВЫЙ МАРШРУТ для API моделей ---
@app.route('/api/models', methods=['GET'])
def api_get_models():
    """ Возвращает список доступных моделей Gemini в формате JSON. """
    models = get_models_list()
    if models is not None:
        return jsonify(models)
    else:
        # Возвращаем пустой список или ошибку сервера
        return jsonify({"error": "Could not retrieve models from API"}), 500

# --- НОВЫЙ МАРШРУТ для скачивания EPUB ---
@app.route('/download_epub/<book_id>', methods=['GET'])
def download_epub(book_id):
    """ Генерирует и отдает переведенную книгу в формате EPUB """
    if book_id not in book_progress:
        return "Book not found", 404

    book_info = book_progress[book_id]
    target_language = request.args.get('lang', 'russian') # Получаем язык из запроса

    # Пересчитываем статус ПЕРЕД проверкой
    update_overall_book_status(book_id)
    print(f"Запрос на скачивание EPUB. Статус книги: {book_info['status']}") # Отладка

    # Разрешаем скачивать, если статус 'complete' или 'complete_with_errors'
    if book_info['status'] not in ["complete", "complete_with_errors"]:
        print("Отказ в скачивании EPUB: перевод книги еще не завершен.")
        message = f"Перевод книги еще не завершен. Статус: {book_info['status']}."
        return message, 409 # 409 Conflict - состояние не позволяет выполнить

    # Вызов функции создания EPUB
    epub_content_bytes = create_translated_epub(book_info, target_language)

    if epub_content_bytes is None:
        print("Ошибка: Не удалось сгенерировать EPUB файл.")
        return "Server error generating EPUB file.", 500

    # Подготовка имени файла для скачивания
    base_name = os.path.splitext(book_info.get('filename', 'translated_book'))[0]
    output_filename = f"{base_name}_{target_language}_translated.epub"

    print(f"Отправка сгенерированного EPUB файла: {output_filename}")
    # Отправка файла из памяти
    return send_file(
        io.BytesIO(epub_content_bytes), # Создаем BytesIO из полученных байтов
        mimetype='application/epub+zip',
        as_attachment=True,
        download_name=output_filename # Используем безопасное имя файла
    )

# --- Запуск приложения ---
if __name__ == '__main__':
    # Указываем host='0.0.0.0', чтобы приложение было доступно извне (если нужно)
    # Для разработки можно оставить 127.0.0.1 или убрать host
    app.run(debug=True, host='0.0.0.0')