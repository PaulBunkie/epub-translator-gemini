# --- START OF FILE db_manager.py ---
import sqlite3
import json
import time

DATABASE_FILE = "epub_translator.db"

def init_db():
    """Инициализирует базу данных, создает таблицы, если их нет."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Создание таблицы books ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                book_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                original_language TEXT,
                status TEXT NOT NULL,
                toc_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # --- Создание таблицы sections ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sections (
                internal_section_id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                epub_section_id TEXT NOT NULL,
                status TEXT NOT NULL,
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
        # ON DELETE CASCADE - важно для удаления связанных секций при удалении книги

        conn.commit()
        print("База данных инициализирована, таблицы созданы (если их не было).")

    except sqlite3.Error as e:
        print(f"Ошибка инициализации базы данных: {e}")
    finally:
        if conn:
            conn.close()

def create_book(book_id, filename, filepath, toc):
    """Создает запись о книге в базе данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        toc_json_str = json.dumps(toc, ensure_ascii=False)

        cursor.execute("""
            INSERT INTO books (book_id, filename, filepath, status, toc_json)
            VALUES (?, ?, ?, ?, ?)
        """, (book_id, filename, filepath, 'idle', toc_json_str))

        conn.commit()
        print(f"Книга '{filename}' (ID: {book_id}) добавлена в базу данных.")
        return True

    except sqlite3.Error as e:
        print(f"Ошибка при добавлении книги в базу данных: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_book(book_id):
    """Извлекает данные книги по book_id из базы данных, включая переведенный TOC и список ID секций.""" # <--- ОБНОВЛЕНО ОПИСАНИЕ
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.row_factory = sqlite3.Row

        cursor.execute("SELECT * FROM books WHERE book_id = ?", (book_id,))
        row = cursor.fetchone()

        if row:
            book_info = {
                'book_id': row['book_id'],
                'filename': row['filename'],
                'filepath': row['filepath'],
                'original_language': row['original_language'],
                'status': row['status'],
                'toc_json': row['toc_json'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            }

            sections_list = get_sections_for_book(book_id) # Получаем словарь секций

            toc_data = []
            if book_info.get('toc_json'):
                try:
                    toc_data = json.loads(book_info['toc_json'])
                    for item in toc_data:
                         section_id = item.get('id')
                         if section_id and section_id in sections_list:
                              section_data = sections_list[section_id]
                              translated_title = section_data.get('translated_title')
                              if translated_title:
                                   item['translated_title'] = translated_title
                except json.JSONDecodeError:
                    print(f"Ошибка декодирования toc_json для книги {book_id}")
                    toc_data = []

            book_info['toc'] = toc_data
            # --- ДОБАВЛЕНИЕ: Возвращаем section_ids_list из ключей словаря секций ---
            book_info['section_ids_list'] = list(sections_list.keys()) # <--- Добавляем список ID секций

            # book_info.pop('toc_json', None) # Можно удалить

            return book_info
        else:
            return None
    except sqlite3.Error as e:
        print(f"Ошибка при получении книги из базы данных: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_all_books():
    """Извлекает список всех книг из базы данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        conn.row_factory = sqlite3.Row # Для доступа к столбцам по имени
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM books ORDER BY filename
        """)
        rows = cursor.fetchall()
        books = []
        for row in rows:
            book_data = dict(row)
            if book_data.get('toc_json'):
                book_data['toc'] = json.loads(book_data['toc_json']) # Десериализация TOC
            else:
                book_data['toc'] = [] # или None, если toc_json пустой/null в БД
            books.append(book_data)
        return books

    except sqlite3.Error as e:
        print(f"Ошибка при получении списка книг из базы данных: {e}")
        return [] # Возвращаем пустой список в случае ошибки
    finally:
        if conn:
            conn.close()

def update_book_status(book_id, status):
    """Обновляет статус книги в базе данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("""
            UPDATE books SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE book_id = ?
        """, (status, book_id))

        conn.commit()
        print(f"Статус книги '{book_id}' обновлен на '{status}'.")
        return True

    except sqlite3.Error as e:
        print(f"Ошибка при обновлении статуса книги в базе данных: {e}")
        return False
    finally:
        if conn:
            conn.close()

def delete_book(book_id):
    """Удаляет книгу и связанные секции из базы данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Сначала удаляем секции, связанные с книгой (благодаря ON DELETE CASCADE, это не обязательно)
        # cursor.execute("""
        #     DELETE FROM sections WHERE book_id = ?
        # """, (book_id,))

        # Затем удаляем книгу
        cursor.execute("""
            DELETE FROM books WHERE book_id = ?
        """, (book_id,))

        conn.commit()
        print(f"Книга '{book_id}' и связанные данные удалены из базы данных.")
        return True

    except sqlite3.Error as e:
        print(f"Ошибка при удалении книги из базы данных: {e}")
        return False
    finally:
        if conn:
            conn.close()

def create_section(book_id, epub_section_id, translated_title=None): # <--- ПАРАМЕТР translated_title
    """Создает запись о секции в базе данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- ИЗМЕНЕНИЕ: Добавляем translated_title в INSERT запрос ---
        cursor.execute("""
            INSERT INTO sections (book_id, epub_section_id, status, translated_title) 
            VALUES (?, ?, ?, ?)
        """, (book_id, epub_section_id, 'not_translated', translated_title)) # <--- Передаем translated_title

        conn.commit()
        # print(f"Секция '{epub_section_id}' (книга ID: {book_id}) добавлена в базу данных.") # Too verbose
        return True

    except sqlite3.Error as e:
        print(f"Ошибка при добавлении секции в базу данных: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_sections_for_book(book_id):
    """Извлекает словарь секций для данной книги из базы данных.""" # <--- ИЗМЕНЕН ЗАГОЛОВОК
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей для этого соединения ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        conn.row_factory = sqlite3.Row # Для доступа к столбцам по имени
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM sections WHERE book_id = ? ORDER BY internal_section_id
        """, (book_id,)) # ordering by internal_section_id for consistent order
        rows = cursor.fetchall()
        sections_dict = {} # <--- ИЗМЕНЕНИЕ: Создаем словарь, а не список
        for row in rows:
            section_data = dict(row) # Преобразуем sqlite3.Row в словарь
            sections_dict[section_data['epub_section_id']] = section_data # <--- Ключ - epub_section_id
        return sections_dict # <--- ВОЗВРАЩАЕМ СЛОВАРЬ

    except sqlite3.Error as e:
        print(f"Ошибка при получении списка секций из базы данных: {e}")
        return {} # Return empty dict in case of error # <--- Возвращаем пустой словарь
    finally:
        if conn:
            conn.close()

def update_section_status(book_id, epub_section_id, status, model_name=None, target_language=None, error_message=None):
    """Обновляет статус, модель, язык и сообщение об ошибке секции в базе данных.""" # <--- Обновлен Docstring
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Обновляем все релевантные поля ---
        cursor.execute("""
            UPDATE sections
            SET status = ?,
                model_name = ?,
                target_language = ?,
                error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND epub_section_id = ?
        """, (status, model_name, target_language, error_message, book_id, epub_section_id)) # <--- Передаем все параметры

        conn.commit()
        # Можно сделать лог чуть подробнее, если нужно
        # print(f"Статус секции '{epub_section_id}' (книга ID: {book_id}) обновлен на '{status}' (Модель: {model_name}, Язык: {target_language}).")
        return True

    except sqlite3.Error as e:
        print(f"Ошибка при обновлении статуса/данных секции '{epub_section_id}' (книга ID: {book_id}): {e}") # <--- Уточнено сообщение об ошибке
        return False
    finally:
        if conn:
            conn.close()

def reset_stuck_processing_sections(active_processing_sections=None): # <--- ДОБАВЛЯЕМ ПАРАМЕТР active_processing_sections
    """Сбрасывает статусы 'processing' секций (КРОМЕ АКТИВНЫХ) на 'error_unknown' при запуске приложения.""" # <--- ОБНОВЛЕНО ОПИСАНИЕ
    conn = None
    updated_count = 0 # Инициализируем
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Формируем SQL-запрос для сброса статусов, ИСКЛЮЧАЯ АКТИВНЫЕ ---
        sql_query = """
            UPDATE sections SET status = 'error_unknown', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
        """
        params = []

        # --- ИЗМЕНЕНИЕ: Добавляем условие WHERE для исключения активных секций ---
        if active_processing_sections: # Если есть список активных секций
            # Формируем строку плейсхолдеров (?,?,?) для IN (...)
            placeholders = ','.join('?' for _ in active_processing_sections)
            # Формируем условие NOT IN ((book_id, epub_section_id), ...)
            # Важно: SQLite не поддерживает кортежи в IN напрямую, используем book_id || '-' || epub_section_id
            active_keys = [f"{book_id}-{section_id}" for book_id, section_id in active_processing_sections]
            placeholders_keys = ','.join('?' for _ in active_keys)
            sql_query += f" AND (book_id || '-' || epub_section_id) NOT IN ({placeholders_keys})"
            params.extend(active_keys)
            print(f"[DEBUG] Исключаем активные секции: {active_keys}")

        cursor.execute(sql_query, params) # <--- Выполняем запрос с параметрами

        updated_count = conn.total_changes # Получаем количество обновленных строк
        conn.commit()

        print(f"При запуске приложения сброшено {updated_count} зависших статусов секций 'processing' (исключая активные) на 'error_unknown'.")
        return updated_count

    except sqlite3.Error as e:
        print(f"Ошибка при сбросе зависших статусов секций: {e}")
        return -1 # Возвращаем -1 в случае ошибки
    finally:
        if conn:
            conn.close()


# Инициализация БД при импорте модуля
init_db()

if __name__ == '__main__':
    # --- Пример использования (для тестирования) ---
    test_book_id = "test_book_123" # Используем тот же ID, что и раньше
    test_filename = "Test Book Title.epub"
    test_filepath = "/path/to/test/book.epub"
    test_toc = [
        {'level': 1, 'title': 'Глава 1', 'href': 'chapter1.xhtml', 'id': 'chapter1', 'anchor': None},
        {'level': 2, 'title': 'Раздел 1.1', 'href': 'chapter1.xhtml#sec1', 'id': 'sec1', 'anchor': 'sec1'}
    ]

    # --- Создание книги (если еще не создана) ---
    if not get_book(test_book_id): # Проверяем, существует ли книга
        if create_book(test_book_id, test_filename, test_filepath, test_toc):
            print("Тестовая книга успешно добавлена.")
        else:
            print("Не удалось добавить тестовую книгу.")
    else:
        print(f"Книга '{test_book_id}' уже существует, пропуск создания.")

    # --- Создание секций для книги ---
    test_section_ids = ["section_1", "section_2", "section_3"]
    for sec_id in test_section_ids:
        create_section(test_book_id, sec_id)
    print(f"Создано {len(test_section_ids)} секций для книги '{test_book_id}'.")

    # --- Получение списка секций для книги ---
    book_sections = get_sections_for_book(test_book_id)
    if book_sections:
        print(f"\n--- Секции для книги '{test_book_id}' (get_sections_for_book) ---")
        for section in book_sections:
            print(f"  - Section ID: {section['epub_section_id']}, Status: {section['status']}")
    else:
        print(f"Нет секций для книги '{test_book_id}' (get_sections_for_book).")

    # --- Удаление книги (и связанных секций) ---
    if delete_book(test_book_id): # <--- УБЕДИТЕСЬ, ЧТО ЗДЕСЬ ИМЕННО delete_book(test_book_id) !!!
        print(f"\nКнига '{test_book_id}' и связанные данные удалены из базы данных.")
        deleted_book_data = get_book(test_book_id)
        if not deleted_book_data:
            print(f"Подтверждение удаления: книга '{test_book_id}' не найдена после удаления (get_book).")
        # --- Проверка, что секции тоже удалены ---
        time.sleep(0.1) # <--- Паузу пока оставляем
        deleted_sections = get_sections_for_book(test_book_id)
        if not deleted_sections:
            print(f"Подтверждение удаления: секции книги '{test_book_id}' также удалены (get_sections_for_book).")
        else:
            print(f"ОШИБКА: Секции книги '{test_book_id}' все еще существуют после удаления книги!")
    else:
        print(f"Не удалось удалить книгу '{test_book_id}'.")



def get_section_count_for_book(book_id):
    """Возвращает количество секций для данной книги из базы данных."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()

        # --- Включаем поддержку внешних ключей (на всякий случай, хотя здесь не критично) ---
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("""
            SELECT COUNT(*) FROM sections WHERE book_id = ?
        """, (book_id,))
        count = cursor.fetchone()[0] # Получаем результат COUNT(*)
        return count

    except sqlite3.Error as e:
        print(f"Ошибка при получении количества секций из базы данных: {e}")
        return 0 # Возвращаем 0 в случае ошибки
    finally:
        if conn:
            conn.close()


# --- END OF FILE db_manager.py ---