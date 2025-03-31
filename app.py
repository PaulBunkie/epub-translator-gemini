import os
import uuid
import json
from flask import Flask, request, render_template, redirect, url_for, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor
import time

# Импортируем наши модули
from translation_module import configure_api, translate_text, CONTEXT_LIMIT_ERROR
# Импортируем новые и старые функции парсера
from epub_parser import get_epub_structure, extract_section_text, get_epub_toc
from cache_manager import (
    get_translation_from_cache,
    save_translation_to_cache,
    save_translated_chapter,
    _get_epub_id
)

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
    """ Пересчитывает и обновляет общий статус книги """
    if book_id in book_progress:
        book_data = book_progress[book_id]
        translated_count = 0
        error_count = 0
        processing_count = 0
        # Используем сохраненный список ID секций
        total = len(book_data.get("section_ids_list", []))
        book_data["total_sections"] = total # Обновляем на всякий случай

        for section_id in book_data.get("section_ids_list", []):
             status = book_data["sections"].get(section_id, "not_translated")
             if status in ["translated", "cached", "completed_empty"]: # Считаем переведенные (даже пустые)
                 translated_count += 1
             elif status == "processing":
                  processing_count += 1
             elif status.startswith("error_"):
                  error_count +=1

        book_data["translated_count"] = translated_count
        book_data["error_count"] = error_count

        # Определяем общий статус
        if processing_count > 0:
             book_data["status"] = "processing"
        # Все секции обработаны (есть статус), и нет активных процессов
        elif (translated_count + error_count) == total and total > 0:
             book_data["status"] = "complete" if error_count == 0 else "complete_with_errors"
        elif total == 0:
             book_data["status"] = "error_no_sections" # Если не нашли секций
        else:
             book_data["status"] = "idle" # Есть еще нетронутые

# --- Фоновая задача ---
# Функция run_single_section_translation остается БЕЗ ИЗМЕНЕНИЙ
# (кроме возможного добавления error_message в update_book_section_status)
def run_single_section_translation(task_id, epub_filepath, book_id, section_id, target_language, model_name):
    """ Выполняется в отдельном потоке для перевода одной секции """
    print(f"Фоновая задача {task_id}: Начало перевода {section_id} для {book_id}")
    current_status = "error_unknown"
    error_message = None
    try:
        if task_id in active_tasks: active_tasks[task_id]["status"] = "extracting"
        update_book_section_status(book_id, section_id, "processing")
        original_text = extract_section_text(epub_filepath, section_id)

        if not original_text:
            print(f"Фоновая задача {task_id}: Извлеченный текст пуст для {section_id}.")
            current_status = "completed_empty"
            save_translation_to_cache(epub_filepath, section_id, target_language, "") # Кэшируем пустую строку
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
                     current_status = "translated"
                 else:
                     current_status = "error_caching"
                     error_message = "Не удалось сохранить в кэш."
            else:
                 print(f"Фоновая задача {task_id}: Ошибка перевода для {section_id}.")
                 current_status = "error_translation"
                 error_message = "Ошибка при вызове API перевода."

        update_book_section_status(book_id, section_id, current_status, error_message)

    except Exception as e:
        print(f"Критическая ошибка в фоновой задаче {task_id}: {e}")
        error_message = str(e)
        current_status = "error_unknown"
        # Обновляем статус даже при критической ошибке
        update_book_section_status(book_id, section_id, current_status, error_message)
    finally:
        if task_id in active_tasks:
             active_tasks[task_id]["status"] = current_status # Финальный статус задачи
             if error_message: active_tasks[task_id]["error_message"] = error_message
        print(f"Фоновая задача {task_id}: Завершена со статусом {current_status}")


# --- Маршруты Flask ---

@app.route('/', methods=['GET'])
def index():
    """ Отображает главную страницу с формой загрузки """
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """ Обрабатывает загрузку EPUB файла """
    if 'epub_file' not in request.files:
        return "Файл не найден", 400
    file = request.files['epub_file']
    if file.filename == '':
        return "Файл не выбран", 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        try:
            file.save(filepath)
            print(f"Файл сохранен: {filepath}")

            book_id = get_book_id(filepath)

            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            # Получаем ОБА значения от функции
            result_tuple = get_epub_structure(filepath)
            if result_tuple is None or result_tuple[0] is None: # Проверяем, что результат не None и список ID не None
                 raise ValueError("Не удалось получить структуру EPUB (spine).") # Вызываем ошибку, чтобы попасть в except
            section_ids, id_to_href_map = result_tuple # Распаковываем кортеж
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

            toc = get_epub_toc(filepath, id_to_href_map) if id_to_href_map else [] # Получаем TOC

            if section_ids: # Проверяем, что список section_ids не пустой
                # Инициализируем прогресс
                book_progress[book_id] = {
                    "filename": filename,
                    "filepath": filepath,
                    "total_sections": len(section_ids),
                    "translated_count": 0,
                    "error_count": 0,
                    "status": "idle",
                    "sections": {section_id: "not_translated" for section_id in section_ids},
                    "section_ids_list": section_ids,
                    "toc": toc if toc else []
                }
                initial_check_cache(book_id, "russian") # Проверяем для языка по умолчанию
                return redirect(url_for('view_book', book_id=book_id))
            else:
                # Если section_ids пустой, даже если функция отработала
                os.remove(filepath)
                print("Ошибка: Структура EPUB прочитана, но не найдено секций в spine.")
                return "Ошибка: Не удалось найти читаемые разделы в EPUB файле.", 400
        except Exception as e:
            print(f"Ошибка при сохранении или обработке файла: {e}")
            if os.path.exists(filepath): os.remove(filepath)
            # Можно добавить более специфичную обработку ValueError
            if isinstance(e, ValueError) and "структуру EPUB" in str(e):
                 return "Ошибка: Не удалось прочитать структуру EPUB.", 400
            else:
                 return "Ошибка сервера при обработке файла.", 500
    else:
        return "Ошибка: Недопустимый тип файла.", 400

# Функция для первоначальной проверки кэша
def initial_check_cache(book_id, target_language):
     if book_id in book_progress:
          filepath = book_progress[book_id]['filepath']
          for section_id in book_progress[book_id]['section_ids_list']:
               if get_translation_from_cache(filepath, section_id, target_language):
                    book_progress[book_id]['sections'][section_id] = "cached"
          update_overall_book_status(book_id) # Обновляем статус после проверки кэша


@app.route('/book/<book_id>', methods=['GET'])
def view_book(book_id):
    """ Отображает страницу с оглавлением книги и статусом перевода """
    if book_id not in book_progress:
        return "Ошибка: Книга с таким ID не найдена.", 404

    book_info = book_progress[book_id]
    # Обновляем статусы перед отображением (на случай фоновых изменений)
    initial_check_cache(book_id, request.args.get('lang', 'russian'))

    # Передаем иерархическое оглавление в шаблон
    return render_template('book_view.html', book_id=book_id, book_info=book_info)


# --- Эндпоинты для AJAX ---

@app.route('/translate_section/<book_id>/<section_id>', methods=['POST'])
def translate_section_request(book_id, section_id):
    """ Запускает фоновый перевод для одной секции """
    # ... (Код остается таким же, как в предыдущем ответе) ...
    if book_id not in book_progress:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_progress[book_id].get("filepath")
    if not filepath or not os.path.exists(filepath):
         return jsonify({"error": "EPUB file not found"}), 404
    target_language = request.json.get('target_language', 'russian')
    model_name = request.json.get('model_name', 'gemini-1.5-flash')
    current_status = book_progress[book_id]["sections"].get(section_id)
    if current_status == 'processing':
         return jsonify({"status": "already_processing"}), 409
    if get_translation_from_cache(filepath, section_id, target_language):
        update_book_section_status(book_id, section_id, "cached")
        return jsonify({"status": "already_cached"}), 200
    task_id = str(uuid.uuid4())
    active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
    update_book_section_status(book_id, section_id, "processing")
    executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name)
    print(f"Запущена задача {task_id} для перевода {section_id}")
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
    """ Собирает все переведенные секции и отдает как один файл """
    # ... (Код доработан для итерации по section_ids_list) ...
    if book_id not in book_progress: return "Book not found", 404
    filepath = book_progress[book_id].get("filepath")
    target_language = request.args.get('lang', 'russian')
    book_info = book_progress[book_id]
    update_overall_book_status(book_id)
    # if book_info['status'] not in ["complete", "complete_with_errors"]:
    #     return "Book translation is not complete yet.", 409

    section_ids = book_info.get("section_ids_list", []) # Берем порядок из spine
    if not section_ids: return "No sections found for this book", 500

    full_text_parts = []
    missing_sections = []
    for section_id in section_ids:
        translation = get_translation_from_cache(filepath, section_id, target_language)
        if translation is not None: # Включаем даже пустые переводы
             full_text_parts.append(f"\n\n==== Section: {section_id} ====\n\n")
             full_text_parts.append(translation)
        else:
             # Считаем отсутствующими только те, у которых нет статуса ошибки
             status = book_info["sections"].get(section_id, "not_translated")
             if not status.startswith("error_") and status != 'completed_empty':
                  missing_sections.append(section_id)

    # Разрешаем скачивать, даже если были ошибки, но есть хоть какой-то текст
    if not full_text_parts:
         return "No translated text found to download.", 404

    if missing_sections:
         # Добавляем предупреждение в начало файла
         full_text_parts.insert(0, f"WARNING: Following sections could not be translated: {', '.join(missing_sections)}\n\n")

    full_text = "".join(full_text_parts)
    base_name = os.path.splitext(book_info['filename'])[0]
    output_filename = f"{base_name}_{target_language}_translated.txt"

    return Response(
        full_text,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={output_filename}"}
    )

# --- Запуск приложения ---
if __name__ == '__main__':
    # Указываем host='0.0.0.0', чтобы приложение было доступно извне (если нужно)
    # Для разработки можно оставить 127.0.0.1 или убрать host
    app.run(debug=True, host='0.0.0.0')