import os
import uuid
import json
from flask import Flask, request, render_template, redirect, url_for, jsonify, send_from_directory, Response, session
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor
import time
from flask import send_file
import io

# Импортируем наши модули
from epub_creator import create_translated_epub
from db_manager import (
    get_all_books,
    get_book, # Возможно, понадобится позже, импортируем сейчас
    create_book, # Возможно, понадобится позже, импортируем сейчас
    update_book_status, # Возможно, понадобится позже, импортируем сейчас
    delete_book, # Возможно, понадобится позже, импортируем сейчас
    create_section,
    get_sections_for_book,
    update_section_status,
    reset_stuck_processing_sections,
    get_section_count_for_book  
)
from translation_module import configure_api, translate_text, CONTEXT_LIMIT_ERROR, get_models_list
from epub_parser import get_epub_structure, extract_section_text, get_epub_toc
from cache_manager import (
    get_translation_from_cache,
    save_translation_to_cache,
    save_translated_chapter, # Используем для сохранения полного файла
    delete_section_cache,    
    delete_book_cache,     
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

# book_progress = {}

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
    """ Пересчитывает и обновляет общий статус книги, учитывая только секции из TOC """ # <--- ОБНОВЛЕНО ОПИСАНИЕ
    # --- Получаем book_info (включая toc) из БД через get_book ---
    book_data = get_book(book_id) # Используем get_book из db_manager
    if book_data is None:
         print(f"[WARN] Книга с ID '{book_id}' не найдена в БД при попытке обновить общий статус.")
         return False

    # --- Получаем sections_list (словарь ВСЕХ секций) из БД через get_sections_for_book ---
    all_sections_dict = get_sections_for_book(book_id) # Используем get_sections_for_book

    # --- ИЗМЕНЕНИЕ: Определяем список ID "нужных" секций (из TOC) ---
    needed_section_ids = set() # Используем set для уникальности и быстрого поиска
    if book_data.get('toc'):
         for item in book_data['toc']:
              section_id = item.get('id')
              if section_id:
                   needed_section_ids.add(section_id)
    print(f"[DEBUG] update_overall_book_status: Найдено {len(needed_section_ids)} 'нужных' секций в TOC для книги '{book_id}'.")

    translated_count = 0
    error_count = 0
    processing_count = 0
    total_needed = len(needed_section_ids) # <--- ИЗМЕНЕНИЕ: Используем количество "нужных" секций

    # --- ИЗМЕНЕНИЕ: Итерируем только по "нужным" секциям ---
    for section_id in needed_section_ids:
         section_data = all_sections_dict.get(section_id) # Получаем данные нужной секции
         if section_data: # Если секция найдена в БД
              status = section_data['status']
              if status in ["translated", "completed_empty"]:
                  translated_count += 1
              elif status == "processing":
                  processing_count += 1
              elif status.startswith("error_"):
                  error_count +=1
         else: # Если нужная секция из TOC не найдена в БД (странно, но обрабатываем)
              print(f"[WARN] Секция '{section_id}' из TOC не найдена в БД для книги '{book_id}'!")
              # Как считать такую секцию? Можно считать как ошибку, или игнорировать. Пока игнорируем.
              pass

    overall_status = "idle" # Статус по умолчанию
    # --- ИЗМЕНЕНИЕ: Используем total_needed в условии определения "готовности" ---
    if processing_count > 0:
         overall_status = "processing"
    elif (translated_count + error_count) == total_needed and total_needed > 0 and processing_count == 0: # <--- ИСПОЛЬЗУЕМ total_needed
         overall_status = "complete" if error_count == 0 else "complete_with_errors"
    elif total_needed == 0 and len(all_sections_dict) > 0: # <--- ИЗМЕНЕНИЕ: Если нет "нужных" секций, но есть другие - возможно, ошибка структуры?
         overall_status = "error_no_toc_sections" # <--- НОВЫЙ СТАТУС? Или оставить "idle"?
    elif len(all_sections_dict) == 0: # Если вообще нет секций (включая служебные)
         overall_status = "error_no_sections"
    else:
         overall_status = "idle"

    # --- Обновляем общий статус книги в БД через update_book_status ---
    if update_book_status(book_id, overall_status):
         print(f"Общий статус книги '{book_id}' обновлен в БД на '{overall_status}'.")
         return True
    else:
         print(f"ОШИБКА: Не удалось обновить общий статус книги '{book_id}' в БД!")
         return False

# --- Фоновая задача ---
def run_single_section_translation(task_id, epub_filepath, book_id, section_id, target_language, model_name):
    """ Выполняется в отдельном потоке для перевода одной секции (данные из БД) """
    # Добавляем model_name в лог для ясности
    print(f"Фоновая задача {task_id}: Начало перевода {section_id} для {book_id} моделью '{model_name}' на язык '{target_language}'")
    current_status = "error_unknown"
    error_message = None # Инициализируем сообщение об ошибке

    try:
        if task_id in active_tasks: active_tasks[task_id]["status"] = "extracting"

        original_text = extract_section_text(epub_filepath, section_id)

        if not original_text:
            print(f"Фоновая задача {task_id}: Извлеченный текст пуст для {section_id}.")
            current_status = "completed_empty"
            save_translation_to_cache(epub_filepath, section_id, target_language, "")
            # error_message остается None
        else:
            if task_id in active_tasks: active_tasks[task_id]["status"] = "translating"
            # Передаем model_name в функцию перевода
            api_result = translate_text(original_text, target_language, model_name)

            if api_result == CONTEXT_LIMIT_ERROR:
                print(f"Фоновая задача {task_id}: Ошибка лимита контекста для {section_id}.")
                current_status = "error_context_limit"
                error_message = "Текст раздела слишком велик для модели."
            elif api_result is not None:
                 if task_id in active_tasks: active_tasks[task_id]["status"] = "caching"
                 if save_translation_to_cache(epub_filepath, section_id, target_language, api_result):
                     current_status = "translated"
                     # error_message остается None
                 else:
                     current_status = "error_caching"
                     error_message = "Не удалось сохранить в кэш."
            else:
                 print(f"Фоновая задача {task_id}: Ошибка перевода для {section_id}.")
                 current_status = "error_translation"
                 error_message = "Ошибка при вызове API перевода."

        # --- ИЗМЕНЕНИЕ: Обновляем статус, МОДЕЛЬ, ЯЗЫК и ОШИБКУ секции в БД ---
        # Передаем model_name, target_language и error_message в обновленную функцию
        update_section_status(book_id, section_id, current_status, model_name, target_language, error_message)

    except Exception as e:
        print(f"Критическая ошибка в фоновой задаче {task_id}: {e}")
        error_message = str(e) # Сохраняем текст исключения
        current_status = "error_unknown"
        # --- ИЗМЕНЕНИЕ: Обновляем статус, МОДЕЛЬ, ЯЗЫК и ОШИБКУ секции в БД и при ошибке ---
        # Передаем model_name, target_language и error_message и в случае общей ошибки
        update_section_status(book_id, section_id, current_status, model_name, target_language, error_message)
    finally:
        # Обновляем финальный статус задачи в active_tasks (без изменений)
        if task_id in active_tasks:
             active_tasks[task_id]["status"] = current_status
             if error_message: active_tasks[task_id]["error_message"] = error_message
        print(f"Фоновая задача {task_id}: Завершена со статусом {current_status}")

# --- Маршруты Flask ---

@app.route('/', methods=['GET'])
def index():
    """ Отображает главную страницу со списком загруженных книг из БД. """
    uploaded_books = []
    print(f"Загрузка списка книг из БД...")

    # --- ИЗМЕНЕНИЕ: Получаем язык И МОДЕЛЬ из сессии ---
    default_language = session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'gemini-1.5-flash') # Получаем текущую модель
    print(f"Index page load: lang='{default_language}', model='{selected_model}' (from session)")

    # --- ИЗМЕНЕНИЕ: Получаем список доступных моделей ---
    available_models = get_models_list()
    if not available_models:
        print("WARN (index): Не удалось получить список моделей от API, используем дефолтную.")
        # Показываем хотя бы текущую выбранную + дефолтную, если они разные
        available_models = list(set([selected_model, 'gemini-1.5-flash']))


    # --- Код reset_stuck_processing_sections (без изменений) ---
    active_processing_sections_list = []
    for task_id, task_info in active_tasks.items():
         if task_info.get('status') in ['queued', 'extracting', 'translating', 'caching']:
              active_processing_sections_list.append( (task_info['book_id'], task_info['section_id']) )
    print(f"  Найдено активных задач перевода: {len(active_processing_sections_list)}")
    reset_count = reset_stuck_processing_sections(active_processing_sections=active_processing_sections_list)
    print(f"  Функция reset_stuck_processing_sections() сбросила {reset_count} статусов.")
    # --- Конец кода reset_stuck_processing_sections ---

    try:
        db_books = get_all_books()
        print(f"  Получено из БД: {len(db_books)} книг.")
        for book_data in db_books:
             book_id = book_data['book_id']
             total_sections = get_section_count_for_book(book_id)

             uploaded_books.append({
                 'book_id': book_id,
                 'display_name': book_data['filename'],
                 'status': book_data['status'],
                 'total_sections': total_sections,
                 'default_language': default_language # Язык оставляем, хотя и убрали из ссылки
             })
        uploaded_books.sort(key=lambda x: x['display_name'].lower())
        print(f"Сформирован список uploaded_books: {len(uploaded_books)} книг")
    except Exception as e:
        print(f"Ошибка при формировании списка книг из БД: {e}")
        import traceback
        traceback.print_exc()
    print(f"Передача в шаблон index.html: {len(uploaded_books)} книг, lang={default_language}, model={selected_model}")

    # --- ИЗМЕНЕНИЕ: Передаем язык, МОДЕЛЬ и СПИСОК МОДЕЛЕЙ в шаблон ---
    return render_template(
        'index.html',
        uploaded_books=uploaded_books,
        default_language=default_language, # Передаем язык из сессии
        selected_model=selected_model,     # Передаем модель из сессии
        available_models=available_models  # Передаем список моделей
    )

@app.route('/delete_book/<book_id>', methods=['POST'])
def delete_book_request(book_id):
    """ Удаляет книгу из БД, ее файл и кэш """ # <--- ОБНОВЛЕНО ОПИСАНИЕ
    # --- ИЗМЕНЕНИЕ: Получаем book_info из БД через get_book ---
    book_info = get_book(book_id) # Используем get_book из db_manager

    if book_info: # Если книга найдена в БД
        filepath = book_info.get("filepath") # Берем filepath из book_info (из БД)
        original_filename = book_info.get("filename", book_id) # Берем filename для сообщения

        print(f"Удаление книги ID: {book_id}, файл: {filepath}")

        # --- 1. Удаляем запись о книге из БД (с каскадным удалением секций) ---
        if delete_book(book_id): # Вызываем delete_book из db_manager
             print(f"Запись о книге '{original_filename}' (ID: {book_id}) успешно удалена из БД.")
        else:
             print(f"ОШИБКА: Не удалось удалить запись о книге '{original_filename}' (ID: {book_id}) из БД!")
             # !!! ВАЖНО: В случае ошибки удаления из БД, возможно, не стоит удалять файл и кэш?
             # Или, наоборот, нужно попытаться удалить файл и кэш, чтобы не оставлять "висячие" файлы?
             # Пока оставим как есть - продолжаем удаление файла и кэша, даже если была ошибка в БД.
             pass # Просто для наглядности, что продолжаем

        # --- 2. Удаляем файл из uploads (логика без изменений) ---
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"Удален файл: {filepath}")
            except OSError as e:
                print(f"Ошибка удаления файла {filepath}: {e}")
                # Продолжаем удаление остального

        # --- 3. Удаляем кэш (логика без изменений) ---
        if filepath: # Нужен путь для генерации ID в delete_book_cache
             delete_book_cache(filepath) # Функция из cache_manager

        # Можно добавить flash сообщение об успехе
        # flash(f"Книга '{original_filename}' успешно удалена.")

    else: # Если книга не найдена в БД
        print(f"Попытка удаления несуществующей книги ID: {book_id} (не найдена в БД).")
        # Можно добавить flash сообщение об ошибке
        # flash(f"Книга с ID {book_id} не найдена.", "error")

    return redirect(url_for('index')) # Возвращаемся на главную

@app.route('/upload', methods=['POST'])
def upload_file():
    """ Обрабатывает загрузку EPUB файла, сохраняет инфо в БД и переводит оглавление """
    if 'epub_file' not in request.files: return "Файл не найден", 400
    file = request.files['epub_file']
    if file.filename == '': return "Файл не выбран", 400

    # --- ИЗМЕНЕНИЕ: Получаем язык из формы или сессии ---
    # Приоритет: форма -> сессия -> дефолт ('russian')
    form_language = request.form.get('target_language')
    target_language = form_language or session.get('target_language', 'russian')
    # --- ИЗМЕНЕНИЕ: Сохраняем выбранный (или дефолтный) язык в сессию ---
    session['target_language'] = target_language
    print(f"Язык для перевода TOC (upload): {target_language} (из формы: {form_language}, сохранен в сессию)")

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        # --- Сохраняем файл ВО ВРЕМЕННОЕ МЕСТО, чтобы получить путь для хэша ---
        temp_dir = app.config['UPLOAD_FOLDER']
        temp_filepath = os.path.join(temp_dir, f"temp_{uuid.uuid4().hex}.epub")
        filepath = None # Инициализируем filepath

        try:
             file.save(temp_filepath) # Сохраняем во временный файл
             print(f"Файл временно сохранен: {temp_filepath}")

             # --- ИЗМЕНЕНИЕ: Вычисляем book_id как хэш ПУТИ (_get_epub_id) ---
             book_id = _get_epub_id(temp_filepath) # <-- Вычисляем ID как хэш ПУТИ !!!
             print(f"Вычислен book_id (хэш пути): {book_id}")

             # --- Переименовываем временный файл в постоянный (с book_id) ---
             unique_filename = f"{book_id}.epub"
             filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

             if os.path.exists(filepath):
                  print(f"Файл с ID (хэшем пути) {book_id} уже существует: {filepath}")
                  # Удаляем временный файл, т.к. постоянный уже есть
                  try: os.remove(temp_filepath)
                  except OSError as e: print(f"  Ошибка удаления временного файла {temp_filepath}: {e}")
             else:
                  try:
                       os.rename(temp_filepath, filepath) # Переименовываем временный файл
                       print(f"Файл переименован в: {filepath}")
                  except OSError as e:
                       print(f"ОШИБКА: Не удалось переименовать временный файл в {filepath}: {e}")
                       # Пытаемся удалить временный файл
                       try: os.remove(temp_filepath)
                       except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")
                       return "Ошибка сервера при сохранении файла.", 500

        except Exception as e_save:
             print(f"Ошибка при временном сохранении файла: {e_save}")
             # Пытаемся удалить временный файл, если он был создан
             if os.path.exists(temp_filepath) and filepath is None: # Удаляем только если не переименовали
                  try: os.remove(temp_filepath)
                  except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")
             return "Ошибка сервера при сохранении файла.", 500

        # --- Проверяем наличие книги в БД (логика без изменений) ---
        if get_book(book_id):
             print(f"Книга с ID {book_id} уже есть в БД.")
             # Если временный файл все еще существует (например, если книга уже была в БД)
             if os.path.exists(temp_filepath) and filepath != temp_filepath:
                  try: os.remove(temp_filepath)
                  except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")
             return redirect(url_for('view_book', book_id=book_id))
        else:
             try: # <--- Добавляем try-except блок для обработки ошибок парсинга и перевода
                  print(f"Чтение структуры для новой книги ID: {book_id}")
                  section_ids, id_to_href_map = get_epub_structure(filepath)
                  if section_ids is None: raise ValueError("Не удалось получить структуру EPUB (spine).")

                  print("Вызов get_epub_toc...")
                  toc = get_epub_toc(filepath, id_to_href_map) or []

                  # --- ПЕРЕВОД ОГЛАВЛЕНИЯ (ВОЗВРАЩЕН НА МЕСТО!) ---
                  print(f"Перевод оглавления на язык: {target_language}...")
                  toc_titles_for_translation = [item['title'] for item in toc if item.get('title')]
                  translated_toc_titles = {} # <--- НОВЫЙ СЛОВАРЬ для хранения переведенных названий {section_id: translated_title}

                  if toc_titles_for_translation:
                       # --- ИЗМЕНЕНИЕ: Получаем модель из сессии или используем дефолтную ---
                       # (Хотя для TOC, возможно, всегда лучше использовать быструю модель?)
                       toc_model = session.get('model_name', 'gemini-1.5-flash') # Используем модель из сессии или flash
                       print(f"  Используем модель для TOC: {toc_model}")
                       titles_text = "\n|||---\n".join(toc_titles_for_translation)
                       translated_titles_text = translate_text(titles_text, target_language, toc_model) # Передаем модель

                       if translated_titles_text:
                            translated_titles = translated_titles_text.split("\n|||---\n")
                            if len(translated_titles) == len(toc_titles_for_translation):
                                 # --- Сохраняем переведенные названия в словарь translated_toc_titles ---
                                 for i, item in enumerate(toc):
                                      if item.get('title') and item.get('id'): # Проверяем наличие title и id
                                           translated_title = translated_titles[i] if translated_titles[i] else None # или ""?
                                           translated_toc_titles[item['id']] = translated_title # Сохраняем в словарь по section_id
                                 print("Оглавление переведено, переводы сохранены в translated_toc_titles.")
                            else:
                                 print(f"ОШИБКА: Количество переведенных названий TOC ({len(translated_titles)}) не совпадает с оригинальным ({len(toc_titles_for_translation)}). Используем оригинальные названия.")
                       else:
                            print("ОШИБКА: Не удалось перевести оглавление. Используем оригинальные названия.")
                  else:
                       print("Оглавление не содержит названий для перевода.")
                  # --- КОНЕЦ ПЕРЕВОДА ОГЛАВЛЕНИЯ ---

                  print(f"Сохранение информации о книге в БД. ID: {book_id}, filename: {original_filename}")
                  # --- Сохраняем информацию о книге в БД через create_book ---
                  if create_book(book_id, original_filename, filepath, toc):
                       print(f"Информация о книге '{original_filename}' (ID: {book_id}) успешно сохранена в БД.")

                       # --- Создаем записи о секциях в таблице sections ---
                       print(f"Создание записей о секциях в таблице sections. Книга ID: {book_id}, секций: {len(section_ids)}")
                       if section_ids:
                            for section_id in section_ids:
                                 if section_id: # Проверка на None или пустой ID (на всякий случай)
                                      # --- Получаем translated_title из словаря, если есть ---
                                      translated_title_for_section = translated_toc_titles.get(section_id)
                                      if not create_section(book_id, section_id, translated_title=translated_title_for_section): # <--- Передаем translated_title в create_section
                                           print(f"  ОШИБКА: Не удалось создать запись о секции '{section_id}' для книги '{book_id}' в БД!")
                                 else:
                                      print(f"  ПРЕДУПРЕЖДЕНИЕ: Пропущен section_id как None или пустой.")
                            print(f"Записи о {len(section_ids)} секциях для книги '{book_id}' успешно созданы в БД.")
                       else:
                            print(f"ПРЕДУПРЕЖДЕНИЕ: section_ids_list пуст для книги '{book_id}'. Секции не созданы.")
                       # --- КОНЕЦ СОЗДАНИЯ СЕКЦИЙ ---

                       # --- Удаляем временный файл, если он все еще существует (на всякий случай) ---
                       if os.path.exists(temp_filepath) and filepath != temp_filepath:
                            try: os.remove(temp_filepath)
                            except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")

                       return redirect(url_for('view_book', book_id=book_id))

                  else: # Ошибка create_book
                       print(f"ОШИБКА: Не удалось сохранить информацию о книге '{original_filename}' (ID: {book_id}) в БД!")
                       # !!! ВАЖНО: В случае ошибки сохранения в БД, нужно удалить загруженный файл!
                       if os.path.exists(filepath):
                            try: os.remove(filepath)
                            except OSError as e: print(f"  Ошибка удаления файла {filepath}: {e}")
                       # Пытаемся удалить временный файл, если он все еще существует
                       if os.path.exists(temp_filepath) and filepath != temp_filepath:
                            try: os.remove(temp_filepath)
                            except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")

                       return "Ошибка сервера при сохранении информации о книге.", 500

             except Exception as e_parse_translate: # Ловим ошибки парсинга и перевода
                  print(f"Ошибка при обработке EPUB или переводе оглавления: {e_parse_translate}")
                  # Удаляем загруженный файл (если он есть) и временный файл (если он есть)
                  if os.path.exists(filepath):
                       try: os.remove(filepath)
                       except OSError as e_del: print(f"  Ошибка удаления файла {filepath}: {e_del}")
                  if os.path.exists(temp_filepath) and filepath != temp_filepath:
                       try: os.remove(temp_filepath)
                       except OSError as e_del: print(f"  Ошибка удаления временного файла {temp_filepath}: {e_del}")

                  if isinstance(e_parse_translate, ValueError) and "структуру EPUB" in str(e_parse_translate):
                       return "Ошибка: Не удалось прочитать структуру EPUB.", 400
                  else:
                       import traceback
                       traceback.print_exc()
                       return "Ошибка сервера при обработке файла.", 500

    else: # if not allowed_file(file.filename):
        return "Ошибка: Недопустимый тип файла.", 400

def initial_check_cache(book_id, target_language):
    """ Проверяет кэш для секций книги и обновляет их статусы в БД """ # <--- ОБНОВЛЕНО ОПИСАНИЕ
    # --- ИЗМЕНЕНИЕ: Получаем book_info из БД через get_book ---
    book_info = get_book(book_id) # Используем get_book из db_manager
    if book_info is None:
        print(f"[WARN] Книга с ID '{book_id}' не найдена в БД при проверке кэша.")
        return # Выходим, если книги нет

    filepath = book_info.get('filepath') # Берем filepath из book_info (из БД)
    if not filepath:
        print(f"[WARN] Отсутствует filepath для книги '{book_id}' при проверке кэша.")
        return # Выходим, если нет filepath

    # --- ИЗМЕНЕНИЕ: Получаем sections_list из БД через get_sections_for_book ---
    sections_list = get_sections_for_book(book_id) # Используем get_sections_for_book для получения статусов

    if not sections_list:
        print(f"[INFO] Нет секций для книги '{book_id}' при проверке кэша.")
        update_overall_book_status(book_id) # Обновляем общий статус (может быть 'error_no_sections')
        return # Выходим, если нет секций

    print(f"Проверка кэша для книги '{book_id}' ({len(sections_list)} секций)...")
    sections_updated_count = 0

    # --- ИЗМЕНЕНИЕ: Итерируем по sections_list (словарю секций из БД) ---
    for section_id, section_data in sections_list.items(): # Итерируем по словарю {epub_section_id: section_data}
        current_status = section_data['status'] # Получаем статус из section_data (из БД)

        # --- Проверяем только те, что еще не переведены ---
        if current_status in ['not_translated', 'idle', 'error_unknown']: # <--- ИЗМЕНЕННОЕ УСЛОВИЕ ПРОВЕРКИ СТАТУСА
            if get_translation_from_cache(filepath, section_id, target_language) is not None:
                 # --- Обновляем статус секции в БД на 'translated' ---
                 if update_section_status(book_id, section_id, "translated"):
                      sections_updated_count += 1
                      print(f"  Секция '{section_id}' найдена в кэше, статус обновлен на 'translated'.")
                 else:
                      print(f"  ОШИБКА: Не удалось обновить статус секции '{section_id}' на 'translated' в БД.")

    # --- Обновляем общий статус книги ПОСЛЕ проверки кэша ---
    if sections_updated_count > 0:
        print(f"Проверка кэша завершена. Обновлено {sections_updated_count} статусов секций. Обновляем общий статус книги...")
        update_overall_book_status(book_id) # Обновляем общий статус, только если были изменения
    else:
        print("Проверка кэша завершена. Статусы секций не изменились.")
        # Можно не вызывать update_overall_book_status, если ничего не изменилось,
        # но на всякий случай, можно вызвать, чтобы убедиться, что общий статус актуален.
        update_overall_book_status(book_id)

@app.route('/book/<book_id>', methods=['GET'])
def view_book(book_id):
    """ Отображает страницу с оглавлением книги и статусом перевода (данные из БД) """
    # --- Получаем book_info из БД через get_book ---
    book_info = get_book(book_id) # Используем get_book из db_manager
    if book_info is None: # Книга не найдена в БД
        return "Ошибка: Книга с таким ID не найдена.", 404

    sections_list = get_sections_for_book(book_id)
    book_info['sections'] = sections_list

    # --- ИЗМЕНЕНИЕ: Получаем язык и модель из сессии или request.args (для совместимости) или дефолтов ---
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    selected_model = session.get('model_name', 'gemini-1.5-flash') # Модель берем только из сессии или дефолт

    # --- ИЗМЕНЕНИЕ: Сохраняем актуальные значения в сессию (если пришли из args, например) ---
    session['target_language'] = target_language
    # session['model_name'] = selected_model # Модель обновляется только при запуске перевода

    print(f"View book {book_id}: lang='{target_language}', model='{selected_model}' (актуальные из сессии/args)")

    # initial_check_cache(book_id, target_language) # Пока оставляем

    # --- ИЗМЕНЕНИЕ: Получаем список моделей для dropdown ---
    available_models = get_models_list()
    if not available_models: # Если API не вернуло список, используем дефолтную
        print("WARN: Не удалось получить список моделей от API, используем дефолтную.")
        available_models = [selected_model] # Показываем хотя бы текущую выбранную

    # --- ИЗМЕНЕНИЕ: Передаем язык, модель и список моделей в шаблон ---
    return render_template(
        'book_view.html',
        book_id=book_id,
        book_info=book_info,
        target_language=target_language, # Передаем текущий язык
        selected_model=selected_model,   # Передаем текущую модель
        available_models=available_models # Передаем список моделей
    )

@app.route('/translate_section/<book_id>/<section_id>', methods=['POST'])
def translate_section_request(book_id, section_id):
    """ Запускает фоновый перевод для одной секции, ПРЕДВАРИТЕЛЬНО УДАЛЯЯ КЭШ (данные из БД) """
    # --- ИЗМЕНЕНИЕ: Получаем book_info из БД через get_book ---
    book_info = get_book(book_id) # Используем get_book из db_manager
    if book_info is None:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath") # Берем filepath из book_info (из БД)
    if not filepath or not os.path.exists(filepath):
         return jsonify({"error": "EPUB file not found"}), 404

    # --- Получаем язык и модель из запроса ---
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing JSON payload"}), 400
        # --- ИЗМЕНЕНИЕ: Используем session.get как fallback ---
        target_language = data.get('target_language', session.get('target_language', 'russian'))
        model_name = data.get('model_name', session.get('model_name', 'gemini-1.5-flash'))
    except Exception as e:
        return jsonify({"error": f"Invalid JSON payload: {e}"}), 400


    # --- ИЗМЕНЕНИЕ: Сохраняем выбранные язык и модель в сессию ---
    session['target_language'] = target_language
    session['model_name'] = model_name
    print(f"Translate section {section_id}: lang='{target_language}', model='{model_name}' (сохранено в сессию)")


    sections_list = get_sections_for_book(book_id)
    section_info = sections_list.get(section_id) # Получаем данные секции по ID

    if section_info:
         current_status = section_info['status']
         if current_status == 'processing':
              return jsonify({"status": "already_processing", "message": "Раздел уже в процессе перевода."}), 409
    else:
         return jsonify({"error": "Section data not found in database"}), 404

    print(f"Попытка удалить кэш для {section_id} ({target_language}) перед переводом...")
    delete_section_cache(filepath, section_id, target_language)

    task_id = str(uuid.uuid4())
    active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
    update_section_status(book_id, section_id, "processing")
    executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name)
    print(f"Запущена задача {task_id} для перевода (обновления) {section_id}")

    return jsonify({"status": "processing", "task_id": task_id}), 202

@app.route('/translate_all/<book_id>', methods=['POST'])
def translate_all_request(book_id):
    """ Запускает фоновый перевод для ВСЕХ непереведенных секций (данные из БД) """
    book_info = get_book(book_id)
    if book_info is None:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath")
    if not filepath or not os.path.exists(filepath):
         return jsonify({"error": "EPUB file not found"}), 404

    # --- Получаем язык и модель из запроса ---
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing JSON payload"}), 400
        # --- ИЗМЕНЕНИЕ: Используем session.get как fallback ---
        target_language = data.get('target_language', session.get('target_language', 'russian'))
        model_name = data.get('model_name', session.get('model_name', 'gemini-1.5-flash'))
    except Exception as e:
        return jsonify({"error": f"Invalid JSON payload: {e}"}), 400

    # --- ИЗМЕНЕНИЕ: Сохраняем выбранные язык и модель в сессию ---
    session['target_language'] = target_language
    session['model_name'] = model_name
    print(f"Translate all for {book_id}: lang='{target_language}', model='{model_name}' (сохранено в сессию)")

    sections_list = get_sections_for_book(book_id)
    if not sections_list:
         return jsonify({"error": "No sections found for this book"}), 500

    launched_tasks = []
    for section_id, section_data in sections_list.items():
        current_status = section_data['status']
        # --- ИЗМЕНЕНИЕ: Проверяем кэш с правильным языком ---
        # Используем filepath вместо book_id в get_translation_from_cache
        if current_status not in ['translated', 'completed_empty', 'processing'] and not current_status.startswith('error_'):
            if not get_translation_from_cache(filepath, section_id, target_language): # <--- ИСПРАВЛЕНО: передаем filepath
                task_id = str(uuid.uuid4())
                active_tasks[task_id] = {"status": "queued", "book_id": book_id, "section_id": section_id}
                update_section_status(book_id, section_id, "processing")
                executor.submit(run_single_section_translation, task_id, filepath, book_id, section_id, target_language, model_name)
                launched_tasks.append(task_id)
            else: # Если перевод уже есть в кэше (но статус в БД был не 'translated')
                 # Обновляем статус секции в БД на 'translated', передавая язык и модель
                 # (Модель здесь может быть не та, что в кэше, но запишем текущую выбранную)
                 update_section_status(book_id, section_id, "translated", model_name, target_language)

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
    """ Возвращает общий статус книги и ПОЛНЫЕ ДАННЫЕ секций (включая модель) """ # <--- Обновлен Docstring
    book_info = get_book(book_id) # Получаем книгу (уже включает TOC с переведенными названиями)
    if book_info is None:
         return jsonify({"error": "Book not found"}), 404

    # --- Обновляем общий статус книги ПЕРЕД возвратом ---
    update_overall_book_status(book_id)
    # --- Получаем ОБНОВЛЕННЫЙ book_info после update_overall_book_status ---
    # (get_book внутри себя вызывает get_sections_for_book)
    updated_book_info = get_book(book_id) # Получаем самый свежий статус книги и данные секций

    # --- ИЗМЕНЕНИЕ: Получаем полный словарь секций из БД ---
    sections_dict = get_sections_for_book(book_id) # {epub_section_id: {данные секции}}

    # --- Агрегируем статусы (для общего счетчика, если нужно) ---
    translated_count = 0
    error_count = 0
    total_sections = len(sections_dict)

    for section_data in sections_dict.values():
         status = section_data.get('status')
         if status in ["translated", "completed_empty", "cached"]: # 'cached' тоже считаем переведенным
              translated_count += 1
         elif status and status.startswith("error_"):
              error_count +=1

    # --- ГОТОВИМ ДАННЫЕ СЕКЦИЙ ДЛЯ JSON ---
    # Отправляем только нужные поля для каждой секции, чтобы не перегружать ответ
    sections_for_json = {}
    for epub_id, sec_data in sections_dict.items():
        sections_for_json[epub_id] = {
            'status': sec_data.get('status', 'unknown'),
            'model_name': sec_data.get('model_name'), # <--- ДОБАВЛЕНО ИМЯ МОДЕЛИ
            'error_message': sec_data.get('error_message') # <--- Добавлено сообщение об ошибке
        }

    # --- Возвращаем JSON с обновленными данными ---
    return jsonify({
         "filename": updated_book_info.get('filename', 'N/A'),
         "total_sections": total_sections, # Берем актуальное из sections_dict
         "translated_count": translated_count,
         "error_count": error_count,
         "status": updated_book_info.get('status', 'unknown'), # Берем актуальный статус книги
         "sections": sections_for_json, # <--- Отправляем словарь с моделью и ошибкой
         "toc": updated_book_info.get('toc', []) # Отправляем TOC с переведенными названиями
    })

@app.route('/get_translation/<book_id>/<section_id>', methods=['GET'])
def get_section_translation_text(book_id, section_id):
    """ Возвращает переведенный текст секции из кэша (для отображения), данные из БД """
    book_info = get_book(book_id)
    if book_info is None:
        return jsonify({"error": "Book not found"}), 404
    filepath = book_info.get("filepath")
    # --- ИЗМЕНЕНИЕ: Получаем язык из args или сессии ---
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    print(f"Get translation text for {section_id}: lang='{target_language}' (from args/session)")

    translation = get_translation_from_cache(filepath, section_id, target_language)

    if translation is not None:
        print(f"Перевод раздела '{section_id}' (книга ID: {book_id}) загружен из кэша для отображения.")
        return jsonify({"text": translation})
    else:
        # ... (обработка случая, когда перевода нет в кэше - без изменений) ...
        sections_list = get_sections_for_book(book_id)
        section_info = sections_list.get(section_id)
        if section_info:
             section_status = section_info['status']
             if section_status.startswith("error_"):
                  error_msg = section_info.get('error_message', section_status)
                  return jsonify({"error": f"Translation failed: {error_msg}"}), 404
             else:
                  return jsonify({"error": "Translation not found or not ready"}), 404
        else:
             return jsonify({"error": "Section data not found in database"}), 404

# --- Эндпоинты для скачивания ---

@app.route('/download_section/<book_id>/<section_id>', methods=['GET'])
def download_section(book_id, section_id):
    """ Отдает переведенный текст секции как файл (данные из БД и кэша) """
    book_info = get_book(book_id)
    if book_info is None: return "Book not found", 404
    filepath = book_info.get("filepath")
    # --- ИЗМЕНЕНИЕ: Получаем язык из args или сессии ---
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    print(f"Download section {section_id}: lang='{target_language}' (from args/session)")

    translation = get_translation_from_cache(filepath, section_id, target_language)
    if translation is not None:
        safe_section_id = "".join(c for c in section_id if c.isalnum() or c in ('_', '-')).rstrip()
        filename = f"{safe_section_id}_{target_language}.txt"
        return Response(translation, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})
    else: return "Translation not found", 404

@app.route('/download_full/<book_id>', methods=['GET'])
def download_full(book_id):
    """ Отдает полный переведенный текст книги как файл (данные из БД и кэша) """
    book_info = get_book(book_id)
    if book_info is None: return "Book not found", 404
    filepath = book_info.get("filepath")
    # --- ИЗМЕНЕНИЕ: Получаем язык из args или сессии ---
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    print(f"Download full text {book_id}: lang='{target_language}' (from args/session)")

    # --- ИЗМЕНЕНИЕ: Получаем section_ids_list из book_info (из БД) ---
    # Теперь get_book возвращает section_ids_list
    section_ids = book_info.get("section_ids_list", [])
    if not section_ids:
        # Пытаемся получить ID из ключей словаря sections, если list пуст
        sections_dict_fallback = get_sections_for_book(book_id)
        section_ids = list(sections_dict_fallback.keys())
        if not section_ids:
             return "No sections found for this book", 500
        else:
             print("WARN: section_ids_list не найден в book_info, использованы ключи из get_sections_for_book.")


    sections_list_for_status = get_sections_for_book(book_id)
    update_overall_book_status(book_id)
    # Получаем обновленный book_info после update_overall_book_status
    book_info = get_book(book_id)
    print(f"Статус книги перед скачиванием полного файла: {book_info.get('status', 'unknown')}")

    if book_info.get('status') not in ["complete", "complete_with_errors"]:
         print("Отказ в скачивании: перевод книги еще не завершен.")
         # ... (код для сообщения об ошибке без изменений) ...
         translated_count = 0; error_count = 0; processing_count = 0; total_needed = 0
         needed_section_ids = set()
         if book_info.get('toc'):
              for item in book_info['toc']: needed_section_ids.add(item.get('id'))
         total_needed = len(needed_section_ids)

         if sections_list_for_status:
              for section_id in needed_section_ids: # Считаем только нужные секции
                   sec_data = sections_list_for_status.get(section_id)
                   if sec_data:
                       status = sec_data['status']
                       if status in ["translated", "completed_empty"]: translated_count += 1
                       elif status.startswith("error_"): error_count += 1
                       elif status == 'processing': processing_count += 1

         missing_sections_count = total_needed - (translated_count + error_count + processing_count) # Более точный подсчет
         message = f"Перевод книги не завершен. Статус: {book_info.get('status', 'unknown')}. Не хватает: {missing_sections_count}. В процессе: {processing_count}. С ошибками: {error_count}."
         return message, 409

    full_text_parts = []
    missing_for_download = []
    error_sections_included = []

    print("Сборка полного текста из кэша...")
    for section_id in section_ids: # Итерируем по порядку из section_ids_list
        section_data = sections_list_for_status.get(section_id, {})
        section_status = section_data.get('status', 'not_translated')
        # --- ИЗМЕНЕНИЕ: Получаем перевод для правильного языка ---
        translation = get_translation_from_cache(filepath, section_id, target_language)

        if translation is not None:
             full_text_parts.append(f"\n\n==== Section: {section_id} ({section_status}) ====\n\n")
             full_text_parts.append(translation)
        elif section_status.startswith("error_"):
             error_msg = section_data.get('error_message', section_status)
             full_text_parts.append(f"\n\n==== Section: {section_id} (ОШИБКА: {error_msg}) ====\n\n")
             error_sections_included.append(section_id)
        else:
             print(f"Предупреждение: Статус книги '{book_info.get('status')}', но кэш для секции '{section_id}' (статус: {section_status}, язык: {target_language}) не найден!")
             missing_for_download.append(section_id)
             full_text_parts.append(f"\n\n==== Section: {section_id} (ОШИБКА: Перевод отсутствует в кэше для языка {target_language}) ====\n\n")

    if not full_text_parts:
         return f"Не найдено переведенного текста для языка '{target_language}'.", 404

    if missing_for_download:
         full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Не найден кэш для языка '{target_language}' для секций: {', '.join(missing_for_download)}\n\n")
    if error_sections_included:
          full_text_parts.insert(0, f"ПРЕДУПРЕЖДЕНИЕ: Следующие секции не были переведены из-за ошибок: {', '.join(error_sections_included)}\n\n")

    full_text = "".join(full_text_parts)
    base_name = os.path.splitext(book_info['filename'])[0]
    output_filename = f"{base_name}_{target_language}_translated.txt"

    print(f"Отправка полного файла: {output_filename}")
    return Response(
        full_text,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{output_filename}"}
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

@app.route('/download_epub/<book_id>', methods=['GET'])
def download_epub(book_id):
    """ Генерирует и отдает переведенную книгу в формате EPUB (данные из БД) """
    book_info = get_book(book_id)
    if book_info is None:
        return "Book not found", 404

    # --- ИЗМЕНЕНИЕ: Получаем язык из args или сессии ---
    target_language = request.args.get('lang') or session.get('target_language', 'russian')
    print(f"Download EPUB {book_id}: lang='{target_language}' (from args/session)")

    update_overall_book_status(book_id)
    book_info = get_book(book_id)

    print(f"Запрос на скачивание EPUB. Статус книги: {book_info.get('status', 'unknown')}")

    if book_info.get('status') not in ["complete", "complete_with_errors"]:
        print("Отказ в скачивании EPUB: перевод книги еще не завершен.")
        message = f"Перевод книги еще не завершен. Статус: {book_info.get('status', 'unknown')}."
        return message, 409

    sections_dict = get_sections_for_book(book_id)
    book_info['sections'] = sections_dict

    # --- Передаем book_info и target_language в create_translated_epub ---
    epub_content_bytes = create_translated_epub(book_info, target_language)

    if epub_content_bytes is None:
        print("Ошибка: Не удалось сгенерировать EPUB файл.")
        return "Server error generating EPUB file.", 500

    base_name = os.path.splitext(book_info.get('filename', 'translated_book'))[0]
    output_filename = f"{base_name}_{target_language}_translated.epub"

    print(f"Отправка сгенерированного EPUB файла: {output_filename}")
    return send_file(
        io.BytesIO(epub_content_bytes),
        mimetype='application/epub+zip',
        as_attachment=True,
        download_name=output_filename
    )


# --- Запуск приложения ---
if __name__ == '__main__':
    # Указываем host='0.0.0.0', чтобы приложение было доступно извне (если нужно)
    # Для разработки можно оставить 127.0.0.1 или убрать host
    app.run(debug=True, host='0.0.0.0')