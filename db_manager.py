# --- START OF FILE db_manager.py ---
import sqlite3
import json
import time # Оставляем time на случай, если понадобится для отладки или будущих функций

DATABASE_FILE = "epub_translator.db"

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False, timeout=10) 
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    Инициализирует базу данных: создает таблицы books и sections, если их нет,
    и добавляет столбец prompt_ext в таблицу books, если он отсутствует.
    """
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Создание таблицы books ---
        print("[DB] Checking/Creating 'books' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                book_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                original_language TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                toc_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                -- prompt_ext будет добавлен ниже, если его нет
            )
        """)
        conn.commit() # Коммит после CREATE TABLE IF NOT EXISTS

        # --- Проверка и добавление столбца prompt_ext в таблицу books ---
        print("[DB] Checking 'prompt_ext' column in 'books' table...")
        cursor.execute("PRAGMA table_info(books)")
        fetched_rows_prompt = cursor.fetchall()
        columns_prompt = [info[1] for info in fetched_rows_prompt]

        if 'prompt_ext' not in columns_prompt:
            print("[DB] Column 'prompt_ext' not found. Adding column...")
            cursor.execute("ALTER TABLE books ADD COLUMN prompt_ext TEXT NULL DEFAULT ''")
            conn.commit()
            print("[DB] Column 'prompt_ext' added successfully.")
        else:
            print("[DB] Column 'prompt_ext' already exists.")

        # --- Проверка и добавление столбца target_language в таблицу books ---
        print("[DB] Checking 'target_language' column in 'books' table...")
        cursor.execute("PRAGMA table_info(books)")
        fetched_rows_lang = cursor.fetchall()
        columns_lang = [info[1] for info in fetched_rows_lang]

        if 'target_language' not in columns_lang:
            print("[DB] Column 'target_language' not found. Adding column...")
            # Добавляем колонку с DEFAULT значением (можно взять из сессии при создании книги в app.py, но здесь дефолт пустой)
            cursor.execute("ALTER TABLE books ADD COLUMN target_language TEXT NULL DEFAULT ''")
            conn.commit()
            print("[DB] Column 'target_language' added successfully.")
        else:
            print("[DB] Column 'target_language' already exists.")
        # --- Конец добавления столбца ---

        # --- Создание таблицы sections ---
        print("[DB] Checking/Creating 'sections' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sections (
                internal_section_id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                epub_section_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'not_translated',
                error_message TEXT,
                target_language TEXT,
                model_name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                translated_title TEXT,
                UNIQUE (book_id, epub_section_id),
                FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE
            )
        """)
        conn.commit() # Коммит после CREATE TABLE IF NOT EXISTS
        
        # --- НОВОЕ: Создание таблицы location_cache ---
        print("[DB] Checking/Creating 'location_cache' table...") 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS location_cache (
                person_name_key TEXT PRIMARY KEY,
                location_name TEXT,
                latitude REAL,
                longitude REAL,
                error_message TEXT,
                last_updated INTEGER NOT NULL, 
                source_news_summary TEXT 
            )
        ''')
        print("{[DB] Table 'location_cache' checked/created.")
        # --- КОНЕЦ НОВОГО ---        

        print("[DB] Database initialization/update complete.")

    except sqlite3.Error as e:
        print(f"[DB ERROR] Database initialization failed: {e}")
    finally:
        if conn:
            conn.close()

def create_book(book_id, filename, filepath, toc, target_language: str):
    """Создает запись о книге в базе данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        toc_json_str = json.dumps(toc, ensure_ascii=False) if toc else None

        cursor.execute("""
            INSERT INTO books (book_id, filename, filepath, status, toc_json, target_language)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (book_id, filename, filepath, 'idle', toc_json_str, target_language))

        conn.commit()
        print(f"[DB] Book '{filename}' (ID: {book_id}) added.")
        return True

    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to add book '{book_id}': {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_book(book_id):
    """
    Извлекает данные книги по book_id, включая prompt_ext, TOC, ID секций и словарь секций.
    Возвращает словарь или None, если книга не найдена.
    """
    conn = None
    book_info = None # Инициализируем результат
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row # Используем Row factory для удобства
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("SELECT * FROM books WHERE book_id = ?", (book_id,))
        row = cursor.fetchone()

        if row:
            book_info = dict(row) # Преобразуем в словарь

            # Гарантируем наличие ключа prompt_ext (для старых записей или если DEFAULT не сработал)
            if 'prompt_ext' not in book_info or book_info['prompt_ext'] is None: # Добавляем проверку на None
                 book_info['prompt_ext'] = ''

            # --- ДОБАВЛЕНО: Обработка target_language для обратной совместимости ---
            # Если target_language отсутствует или пустое, считаем его 'russian'
            if 'target_language' not in book_info or not book_info['target_language']:
                 print(f"[DB get_book] WARN: target_language отсутствует или пусто для книги {book_id}. Устанавливаем 'russian'.")
                 book_info['target_language'] = 'russian'
            # --- КОНЕЦ ДОБАВЛЕНО ---

            # Получаем секции и добавляем их в результат
            sections_dict = get_sections_for_book(book_id)
            book_info['sections'] = sections_dict

            # Обрабатываем TOC, добавляя переведенные заголовки
            toc_data = []
            if book_info.get('toc_json'):
                try:
                    original_toc = json.loads(book_info['toc_json'])
                    for item in original_toc:
                         section_id = item.get('id')
                         if section_id and section_id in sections_dict:
                              section_data = sections_dict[section_id]
                              translated_title = section_data.get('translated_title')
                              if translated_title:
                                   item['translated_title'] = translated_title
                         toc_data.append(item)
                except json.JSONDecodeError:
                    print(f"[DB WARN] Failed to decode toc_json for book {book_id}")
                    toc_data = []

            book_info['toc'] = toc_data
            book_info['section_ids_list'] = list(sections_dict.keys())

            # Удаляем ненужное поле из результата
            book_info.pop('toc_json', None)

    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get book '{book_id}': {e}")
        book_info = None # Сбрасываем результат в случае ошибки
    finally:
        if conn:
            conn.close()
    return book_info # Возвращаем словарь или None

def get_all_books():
    """Извлекает список всех книг из базы данных (в виде списка словарей)."""
    conn = None
    books = [] # Инициализируем пустой список
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("SELECT * FROM books ORDER BY filename")
        rows = cursor.fetchall()

        for row in rows:
            book_data = dict(row)
            # Гарантируем наличие prompt_ext
            if 'prompt_ext' not in book_data:
                 book_data['prompt_ext'] = ''
            # Обрабатываем TOC
            if book_data.get('toc_json'):
                 try: book_data['toc'] = json.loads(book_data['toc_json'])
                 except json.JSONDecodeError: book_data['toc'] = []
            else: book_data['toc'] = []
            book_data.pop('toc_json', None) # Удаляем исходный JSON
            books.append(book_data)

    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get all books: {e}")
        books = [] # Возвращаем пустой список при ошибке
    finally:
        if conn:
            conn.close()
    return books

def update_book_status(book_id, status):
    """Обновляет статус книги в базе данных."""
    conn = None
    success = False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("UPDATE books SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE book_id = ?", (status, book_id))
        conn.commit()
        if cursor.rowcount > 0: # Проверяем, была ли обновлена хотя бы одна строка
            # print(f"[DB] Book status updated for '{book_id}' to '{status}'.") # Убрал лог
            success = True
        # else: print(f"[DB WARN] No book found with ID '{book_id}' to update status.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to update book status for '{book_id}': {e}")
    finally:
        if conn:
            conn.close()
    return success

def update_book_prompt_ext(book_id, prompt_text):
    """Обновляет поле prompt_ext для указанной книги."""
    conn = None
    success = False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        # Нормализуем None к пустой строке для БД
        prompt_text_to_save = prompt_text if prompt_text is not None else ""
        cursor.execute("UPDATE books SET prompt_ext = ?, updated_at = CURRENT_TIMESTAMP WHERE book_id = ?", (prompt_text_to_save, book_id))
        conn.commit()
        if cursor.rowcount > 0:
            print(f"[DB] prompt_ext updated for book '{book_id}'.")
            success = True
        # else: print(f"[DB WARN] No book found with ID '{book_id}' to update prompt_ext.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to update prompt_ext for book '{book_id}': {e}")
    finally:
        if conn:
            conn.close()
    return success

def delete_book(book_id):
    """Удаляет книгу и связанные секции (через CASCADE) из базы данных."""
    conn = None
    success = False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("DELETE FROM books WHERE book_id = ?", (book_id,))
        conn.commit()
        if cursor.rowcount > 0:
             print(f"[DB] Book '{book_id}' and related data deleted.")
             success = True
        else:
             print(f"[DB WARN] No book found with ID '{book_id}' to delete.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to delete book '{book_id}': {e}")
    finally:
        if conn:
            conn.close()
    return success

def create_section(book_id, epub_section_id, translated_title=None):
    """Создает запись о секции в базе данных."""
    conn = None
    success = False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        # --- ИЗМЕНЕНИЕ: Добавляем status в INSERT ---
        cursor.execute("""
            INSERT INTO sections (book_id, epub_section_id, status, translated_title)
            VALUES (?, ?, ?, ?)
        """, (book_id, epub_section_id, 'not_translated', translated_title)) # <-- Указываем 'not_translated'
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        conn.commit()
        success = True
    except sqlite3.Error as e:
        # Обрабатываем ошибку UNIQUE constraint отдельно, если нужно
        if "UNIQUE constraint failed" in str(e):
             print(f"[DB WARN] Section '{epub_section_id}' for book '{book_id}' already exists.")
             # Можно считать это успехом, если мы хотим идемпотентности
             # success = True
        else:
             print(f"[DB ERROR] Failed to add section '{epub_section_id}' for book '{book_id}': {e}")
    finally:
        if conn:
            conn.close()
    return success

def get_sections_for_book(book_id):
    """Извлекает словарь секций {epub_section_id: section_data} для данной книги."""
    conn = None
    sections_dict = {}
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("SELECT * FROM sections WHERE book_id = ? ORDER BY internal_section_id", (book_id,))
        rows = cursor.fetchall()
        for row in rows:
            section_data = dict(row)
            sections_dict[section_data['epub_section_id']] = section_data
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get sections for book '{book_id}': {e}")
        sections_dict = {} # Возвращаем пустой словарь при ошибке
    finally:
        if conn:
            conn.close()
    return sections_dict

def update_section_status(book_id, epub_section_id, status, model_name=None, target_language=None, error_message=None, operation_type='translate'):
    """Обновляет статус и другие метаданные секции в базе данных."""
    conn = None
    success = False
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Определяем статус для сохранения в БД
        status_to_save = status
        effective_error_message = error_message # Используем переданное сообщение об ошибке по умолчанию
        effective_model_name = model_name # Используем переданное имя модели по умолчанию


        # --- Логика определения итогового статуса для сохранения ---

        if status == 'completed_empty':
            status_to_save = 'completed_empty'
            effective_model_name = None # Для пустых секций модель не использовалась
            effective_error_message = None # Для пустых секций нет ошибки

        elif status.startswith('error_'):
            status_to_save = status # Сохраняем конкретный тип ошибки
            # error_message уже установлен выше из переданного аргумента или остается None
            # effective_model_name остается переданным, если модель пыталась обработать секцию с ошибкой

        elif status in ['translated', 'cached']: # Успешное завершение основной операции
             if operation_type == 'summarize':
                  status_to_save = 'summarized'
             elif operation_type == 'analyze':
                  status_to_save = 'analyzed'
             elif operation_type == 'translate':
                  # Для translate сохраняем 'translated' или 'cached' как пришло
                  status_to_save = status
             # effective_model_name остается переданным, т.к. операция была успешной с этой моделью
             effective_error_message = None # Для успешных операций нет сообщения об ошибке


        # --- Конец логики определения статуса ---


        cursor.execute("""
            UPDATE sections
            SET status = ?, model_name = ?, target_language = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND epub_section_id = ?
        """, (status_to_save, effective_model_name, target_language, effective_error_message, book_id, epub_section_id)) # Используем определенные переменные
        conn.commit()
        if cursor.rowcount > 0:
            success = True
            # --- НОВАЯ ЛОГИКА: Проверка и обновление статуса книги ---
            total_sections = get_section_count_for_book(book_id)
            processed_sections = get_processed_section_count_for_book(book_id)

            if total_sections > 0 and total_sections == processed_sections:
                # Все секции обработаны (не idle, не processing, не not_translated)
                if has_error_sections(book_id):
                    update_book_status(book_id, 'complete_with_errors')
                else:
                    update_book_status(book_id, 'complete')
            # --- КОНЕЦ НОВОЙ ЛОГИКИ ---
        # else: print(f"[DB WARN] Section '{epub_section_id}' for book '{book_id}' not found for status update.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to update section status for '{book_id}/{epub_section_id}': {e}")
    finally:
        if conn:
            conn.close()
    return success

def reset_stuck_processing_sections(active_processing_sections=None):
    """Сбрасывает статусы 'processing' секций (кроме активных) на 'error_unknown' при запуске."""
    conn = None
    updated_count = 0
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        sql_query = "UPDATE sections SET status = 'error_unknown', updated_at = CURRENT_TIMESTAMP WHERE status = 'processing'"
        params = []

        if active_processing_sections:
            active_keys = [f"{book_id}-{section_id}" for book_id, section_id in active_processing_sections]
            if active_keys: # Только если список не пуст
                placeholders_keys = ','.join('?' for _ in active_keys)
                sql_query += f" AND (book_id || '-' || epub_section_id) NOT IN ({placeholders_keys})"
                params.extend(active_keys)

        cursor.execute(sql_query, params)
        updated_count = cursor.rowcount
        conn.commit()

        if updated_count > 0:
            print(f"[DB] Reset {updated_count} stuck 'processing' sections to 'error_unknown'.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to reset stuck processing sections: {e}")
        updated_count = -1 # Возвращаем -1 в случае ошибки
    finally:
        if conn:
            conn.close()
    return updated_count

def get_section_count_for_book(book_id):
    """Возвращает количество секций для данной книги."""
    conn = None
    count = 0
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("SELECT COUNT(*) FROM sections WHERE book_id = ?", (book_id,))
        result = cursor.fetchone()
        if result:
            count = result[0]
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get section count for book '{book_id}': {e}")
        count = 0 # Возвращаем 0 при ошибке
    finally:
        if conn:
            conn.close()
    return count
    
# --- НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С location_cache ---
CACHE_PRINT_PREFIX = "[DB Cache]" # Или используйте ваш DB_PRINT_PREFIX

def get_cached_location(person_name_key: str):
    """Получает кэшированные данные о локации для персоны."""
    print(f"{CACHE_PRINT_PREFIX} Запрос кэша для '{person_name_key}'")
    conn = None # Объявляем conn здесь для использования в finally
    try:
        # Используйте вашу функцию get_db_connection(), если она есть, или создайте соединение
        # Предполагаем, что get_db_connection() существует и настроена с row_factory
        conn = get_db_connection() # Если такой функции нет, то: conn = sqlite3.connect(DATABASE_NAME); conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT location_name, latitude, longitude, error_message, last_updated, source_news_summary
            FROM location_cache
            WHERE person_name_key = ?
        ''', (person_name_key,))
        row = cursor.fetchone()
        
        if row:
            print(f"{CACHE_PRINT_PREFIX} Найден кэш для '{person_name_key}': last_updated={row['last_updated']}")
            return {
                "location_name": row["location_name"],
                "lat": row["latitude"],
                "lon": row["longitude"],
                "error": row["error_message"],
                "last_updated": row["last_updated"],
                "source_news_summary": row["source_news_summary"]
            }
        print(f"{CACHE_PRINT_PREFIX} Кэш для '{person_name_key}' не найден.")
        return None
    except sqlite3.Error as e:
        print(f"{CACHE_PRINT_PREFIX} ОШИБКА SQLite при получении кэша для '{person_name_key}': {e}")
        traceback.print_exc()
        return None
    finally:
        if conn:
            conn.close()

def save_cached_location(person_name_key: str, location_data: dict, source_summary: str = None):
    """Сохраняет или обновляет данные о локации в кэше."""
    print(f"{CACHE_PRINT_PREFIX} Сохранение/обновление кэша для '{person_name_key}'")
    
    loc_name = location_data.get("location_name")
    lat = location_data.get("lat")
    lon = location_data.get("lon")
    error_msg = location_data.get("error") 
    current_timestamp = int(time.time())
    summary_to_save = source_summary if source_summary else location_data.get("source_news_summary")

    conn = None # Объявляем conn здесь
    try:
        conn = get_db_connection() # или conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO location_cache 
            (person_name_key, location_name, latitude, longitude, error_message, last_updated, source_news_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (person_name_key, loc_name, lat, lon, error_msg, current_timestamp, summary_to_save))
        conn.commit()
        print(f"{CACHE_PRINT_PREFIX} Кэш для '{person_name_key}' успешно сохранен/обновлен. Timestamp: {current_timestamp}")
        return True
    except sqlite3.Error as e:
        print(f"{CACHE_PRINT_PREFIX} ОШИБКА SQLite при сохранении кэша для '{person_name_key}': {e}")
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()

def get_processed_section_count_for_book(book_id) -> int:
    """Возвращает количество секций для данной книги, у которых статус НЕ 'not_translated' и НЕ 'processing'."""
    conn = None
    count = 0
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        # Считаем секции, статус которых не 'not_translated' и не 'processing'
        cursor.execute("SELECT COUNT(*) FROM sections WHERE book_id = ? AND status NOT IN (?, ?)", (book_id, 'not_translated', 'processing'))
        result = cursor.fetchone()
        if result:
            count = result[0]
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get processed section count for book '{book_id}': {e}")
        count = 0
    finally:
        if conn:
            conn.close()
    return count

def has_error_sections(book_id) -> bool:
    """Проверяет, есть ли у книги секции со статусом ошибки."""
    conn = None
    has_errors = False
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        # Ищем хотя бы одну секцию, статус которой начинается с 'error_'
        cursor.execute("SELECT 1 FROM sections WHERE book_id = ? AND status LIKE 'error_%' LIMIT 1", (book_id,))
        result = cursor.fetchone()
        if result:
            has_errors = True
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to check for error sections for book '{book_id}': {e}")
        has_errors = False
    finally:
        if conn:
            conn.close()
    return has_errors

# --- Блок для тестирования модуля ---
if __name__ == '__main__':
     print("\n--- Running DB Manager Tests ---")
     # Убедимся, что БД инициализирована для тестов
     init_db()

     test_book_id = "test_db_main_book"
     test_filename = "Test Main DB Book.epub"
     test_filepath = "/fake/path/main_test.epub"
     test_toc = [{'id': 'ch1_main', 'title': 'Chapter 1 Main'}]

     # --- Создание книги ---
     print("\nTesting book creation...")
     if not get_book(test_book_id):
         create_book(test_book_id, test_filename, test_filepath, test_toc, "en")
     else:
          print(f"Book '{test_book_id}' already exists, skipping creation.")

     # --- Получение книги и проверка prompt_ext ---
     print("\nTesting get_book and initial prompt_ext...")
     book = get_book(test_book_id)
     if book:
         print(f"  Book found. Initial prompt_ext: '{book.get('prompt_ext')}' (Type: {type(book.get('prompt_ext'))})")

         # --- Обновление prompt_ext ---
         print("\nTesting update_book_prompt_ext...")
         new_prompt = "Rule 1\nRule 2"
         if update_book_prompt_ext(test_book_id, new_prompt):
             updated_book = get_book(test_book_id)
             if updated_book and updated_book.get('prompt_ext') == new_prompt:
                  print(f"  Update successful. New prompt_ext: '{updated_book.get('prompt_ext')}'")
             else:
                  print("  [FAIL] Book prompt_ext did not update correctly after saving.")
         else:
              print("  [FAIL] update_book_prompt_ext returned False.")

         # --- Очистка prompt_ext ---
         print("\nTesting clearing prompt_ext...")
         if update_book_prompt_ext(test_book_id, ""):
              cleaned_book = get_book(test_book_id)
              if cleaned_book and cleaned_book.get('prompt_ext') == "":
                   print(f"  Clear successful. Cleared prompt_ext: '{cleaned_book.get('prompt_ext')}'")
              else:
                   print("  [FAIL] Book prompt_ext did not clear correctly.")
         else:
              print("  [FAIL] update_book_prompt_ext returned False while clearing.")

     else:
         print(f"  [FAIL] Could not retrieve book '{test_book_id}' for testing.")

     # --- Удаление тестовой книги ---
     print("\nTesting book deletion...")
     if delete_book(test_book_id):
          if not get_book(test_book_id):
              print(f"  Deletion successful. Book '{test_book_id}' not found after delete.")
          else:
              print(f"  [FAIL] Book '{test_book_id}' still exists after delete attempt.")
     else:
         print(f"  [FAIL] delete_book returned False for '{test_book_id}'.")

     print("\n--- DB Manager Tests Complete ---")

# --- END OF FILE db_manager.py ---