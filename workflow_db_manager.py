# --- START OF FILE workflow_db_manager.py ---

import sqlite3
import json
import os
import time
import traceback
from flask import g # Используем Flask's g для управления соединением
from typing import List, Dict, Any
from collections import OrderedDict

# --- Настройки новой базы данных ---
from config import WORKFLOW_DB_FILE

DATABASE_FILE = str(WORKFLOW_DB_FILE)

def get_workflow_db():
    """Универсальный доступ к базе: Flask/g если есть, иначе standalone."""
    try:
        from flask import g
        try:
            db = getattr(g, '_workflow_database', None)
        except Exception:
            db = None
        if db is None:
            db = g._workflow_database = sqlite3.connect(DATABASE_FILE, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON;")
        return db
    except (ImportError, RuntimeError):
        # Не Flask-контекст — просто открываем соединение
        db = sqlite3.connect(DATABASE_FILE, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON;")
        return db

def close_workflow_db(e=None):
    """Закрывает соединение с новой базой данных."""
    db = getattr(g, '_workflow_database', None)
    if db is not None:
        db.close()

# В app.py нужно будет добавить привязку close_workflow_db к teardown_appcontext

def init_workflow_db():
    """Создает таблицы новой базы данных, если они еще не существуют, и заполняет workflow_stages."""
    db = get_workflow_db()
    try:
        with db:
            # Таблица books
            db.execute('''
                CREATE TABLE IF NOT EXISTS books (
                    book_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    toc TEXT,
                    current_workflow_status TEXT NOT NULL DEFAULT 'idle',
                    target_language TEXT,
                    upload_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    workflow_error_message TEXT,
                    generated_prompt_ext TEXT,
                    manual_prompt_ext TEXT,
                    access_token TEXT UNIQUE
                );
            ''')

            # Таблица sections
            db.execute('''
                CREATE TABLE IF NOT EXISTS sections (
                    section_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL, -- Внешний ключ к таблице books
                    section_epub_id TEXT NOT NULL, -- ID секции из EPUB (например, chapter_001.xhtml)
                    section_title TEXT, -- Оригинальное название секции
                    translated_title TEXT, -- Переведенное название секции (из перевода оглавления при загрузке)
                    order_in_book INTEGER, -- Порядок следования секции в книге
                    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE,
                    UNIQUE (book_id, section_epub_id) -- Секция с таким ID уникальна для книги
                );
            ''')

            # Таблица workflow_stages (Справочник этапов рабочего процесса)
            db.execute('''
                CREATE TABLE IF NOT EXISTS workflow_stages (
                    stage_name TEXT PRIMARY KEY, -- 'summarize', 'analyze', 'translate', 'epub_creation'
                    stage_order INTEGER NOT NULL UNIQUE, -- Порядок выполнения
                    display_name TEXT NOT NULL, -- Человекочитаемое имя
                    is_per_section BOOLEAN NOT NULL -- True, если этот этап применяется к каждой секции индивидуально
                );
            ''')

            # Таблица section_stage_statuses (Статусы каждой секции на каждом применимом этапе)
            db.execute('''
                CREATE TABLE IF NOT EXISTS section_stage_statuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_id INTEGER NOT NULL, -- Внешний ключ к sections
                    stage_name TEXT NOT NULL, -- Внешний ключ к workflow_stages (только per-section)
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'queued', 'processing', 'completed', 'error', 'skipped', 'cached', 'completed_empty'
                    model_name TEXT, -- Модель, использованная для этой секции на этом этапе
                    error_message TEXT -- Сообщение об ошибке для этой секции на этом этапе
                    ,
                    start_time DATETIME, -- Время начала обработки этого этапа для этой секции
                    end_time DATETIME -- Время завершения обработки этого этапа для этой секции
                    ,
                    UNIQUE (section_id, stage_name), -- Уникальность статуса этапа для секции
                    FOREIGN KEY (section_id) REFERENCES sections(section_id) ON DELETE CASCADE,
                    FOREIGN KEY (stage_name) REFERENCES workflow_stages(stage_name)
                );
            ''')

            # Таблица book_stage_statuses (Статусы этапов, применимых ко всей книге)
            db.execute('''
                CREATE TABLE IF NOT EXISTS book_stage_statuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL, -- Внешний ключ к books
                    stage_name TEXT NOT NULL, -- Внешний ключ к workflow_stages (только book-level)
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'queued', 'processing', 'completed', 'error', 'skipped'
                    model_name TEXT, -- Модель, использованная для этого этапа на уровне книги
                    error_message TEXT -- Сообщение об ошибке для этого этапа
                    ,
                    start_time DATETIME, -- Время начала обработки этого этапа для книги
                    end_time DATETIME -- Время завершения обработки этого этапа для книги
                    ,
                    UNIQUE (book_id, stage_name), -- Уникальность статуса этапа для книги
                    FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE,
                    FOREIGN KEY (stage_name) REFERENCES workflow_stages(stage_name)
                );
            ''')

            # --- Заполнение таблицы workflow_stages ---
            cursor = db.execute("SELECT COUNT(*) FROM workflow_stages;")
            if cursor.fetchone()[0] == 0:
                print("[WorkflowDB] Заполнение таблицы workflow_stages...")
                stages_data = [
                    ('summarize', 1, 'Суммаризация', True), # is_per_section = True
                    ('analyze', 2, 'Анализ трудностей', False), # is_per_section = False
                    ('translate', 4, 'Перевод', True), # is_per_section = True
                    ('epub_creation', 5, 'Создание EPUB', False), # is_per_section = False
                ]
                db.executemany('''
                    INSERT INTO workflow_stages (stage_name, stage_order, display_name, is_per_section)
                    VALUES (?, ?, ?, ?)
                ''', stages_data)
                print("[WorkflowDB] Таблица workflow_stages заполнена.")
            
            # Мигрируем stage_order к новому формату (если нужно)
            migrate_stage_orders_to_decimals()
            
            # Добавляем этап reduce_text, если его нет
            add_reduce_text_stage()
            
            # Таблица telegram_users для уведомлений пользователей
            db.execute('''
                CREATE TABLE IF NOT EXISTS telegram_users (
                    user_id TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (access_token) REFERENCES books(access_token) ON DELETE CASCADE
                );
            ''')
            
            # Таблица user_sessions для хранения сессий пользователей
            db.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_accessed DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME NOT NULL,
                    FOREIGN KEY (access_token) REFERENCES books(access_token) ON DELETE CASCADE
                );
            ''')
            
            # --- МИГРАЦИЯ: Добавление access_token в таблицу books, если его нет ---
            cursor = db.execute("PRAGMA table_info(books);")
            columns = [row[1] for row in cursor.fetchall()]
            if 'access_token' not in columns:
                try:
                    db.execute("ALTER TABLE books ADD COLUMN access_token TEXT;")
                    print("[WorkflowDB] Колонка access_token добавлена в таблицу books (без UNIQUE)")
                except Exception as e:
                    print(f"[WorkflowDB] ОШИБКА добавления access_token: {e}")
            else:
                print("[WorkflowDB] Колонка access_token уже существует (PRAGMA)")
            # --- Создание уникального индекса для access_token ---
            try:
                db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_books_access_token ON books(access_token);")
                print("[WorkflowDB] Уникальный индекс для access_token создан или уже существует")
            except Exception as e:
                print(f"[WorkflowDB] ОШИБКА создания уникального индекса для access_token: {e}")
            
            # --- КОНЕЦ ИЗМЕНЕНИЯ: Новая структура таблиц ---

        print("[WorkflowDB] База данных инициализирована.")
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА инициализации базы данных: {e}")
        traceback.print_exc()


# --- Функции работы с книгами (общая информация) ---

def create_book_workflow(book_id, filename, filepath, toc_data, target_language, access_token):
    """Создает новую запись о книге в таблице books."""
    print(f"[WorkflowDB] Попытка создания записи для книги ID: {book_id}")
    db = get_workflow_db()
    try:
        # Проверяем, существует ли книга уже
        cursor = db.execute("SELECT 1 FROM books WHERE book_id = ?;", (book_id,))
        if cursor.fetchone():
            print(f"[WorkflowDB] Книга с ID {book_id} уже существует. Отмена создания.")
            return False # Книга уже существует

        db.execute('''
            INSERT INTO books (book_id, filename, filepath, toc, target_language, current_workflow_status, access_token)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (book_id, filename, filepath, json.dumps(toc_data), target_language, 'uploaded', access_token))
        db.commit()  # Явный коммит после создания книги
        print(f"[WorkflowDB] Книга '{book_id}' добавлена в БД со статусом 'uploaded'. (commit)")

        # Удаляем прямой вызов вставки статусов этапов книги здесь.
        # Инициализация будет происходить в отдельной функции, которая будет вызвана после создания книги.
        # book_level_stages_cursor = db.execute("SELECT stage_name FROM workflow_stages WHERE is_per_section = FALSE;")
        # book_stage_statuses_data = [(book_id, stage['stage_name'], 'pending') for stage in book_level_stages_cursor.fetchall()]
        # if book_stage_statuses_data: # Вставляем только если есть book-level этапы
        #      db.executemany('''
        #          INSERT INTO book_stage_statuses (book_id, stage_name, status)
        #          VALUES (?, ?, ?)
        #      ''', book_stage_statuses_data)
        # print(f"[WorkflowDB] Инициализированы статусы этапов книги для '{book_id}'.")

        # db.commit() # Удален явный коммит, так как он уже выполнен выше
        print(f"[WorkflowDB] Запись книги {book_id} успешно создана.")
        return True
    except sqlite3.IntegrityError as e:
        db.rollback()
        print(f"[WorkflowDB] ОШИБКА ЦЕЛОСТНОСТИ при создании книги {book_id}: {e}")
        return False
    except Exception as e:
        db.rollback()
        print(f"[WorkflowDB] Неизвестная ОШИБКА при создании книги {book_id}: {e}")
        traceback.print_exc()
        return False

def get_book_workflow(book_id):
    """Получает информацию о книге из таблицы books по ID, включая статусы этапов."""
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT * FROM books WHERE book_id = ?', (book_id,))
        row = cursor.fetchone()
        if row:
            book_info = dict(row)
            # Преобразуем JSON поле toc обратно в Python объект
            if book_info.get('toc'):
                try: book_info['toc'] = json.loads(book_info['toc'])
                except (json.JSONDecodeError, TypeError) as e: print(f"[WorkflowDB] Ошибка парсинга TOC для {book_id}: {e}"); book_info['toc'] = []
            else: book_info['toc'] = []
            
            # Вместо извлечения из TOC просто используем имя файла
            book_info['book_title'] = book_info['filename']
            # Обеспечиваем наличие target_language
            book_info['target_language'] = book_info.get('target_language') or 'russian' # Default to russian

            # Получаем статусы этапов книги
            book_info['book_stage_statuses'] = get_book_stage_statuses_workflow(book_id)
            # Получаем статусы этапов секций (может быть много, но полезно для общего прогресса)
            # Можно добавить агрегацию или отдельную функцию для сводных статусов секций по этапам
            # Пока просто вернем общее количество секций и количество завершенных на первом per-section этапе
            book_info['total_sections_count'] = get_section_count_for_book_workflow(book_id)

            # Используем новую функцию для получения количества обработанных секций (completed + skipped + empty)
            book_info['processed_sections_count_summarize'] = get_processed_sections_count_for_stage_workflow(book_id, 'summarize')

            # --- НОВОЕ: Добавляем список секций с их статусами ---
            book_info['sections'] = get_sections_for_book_workflow(book_id)
            # --- КОНЕЦ НОВОГО ---

            return book_info
        return None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении книги '{book_id}': {e}")
        traceback.print_exc()
        return None
    finally:
        pass # Ничего не делаем, закрытие через g.teardown

def get_all_books_workflow():
    """Получает информацию обо всех книгах из таблицы books."""
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT * FROM books ORDER BY upload_time DESC')
        rows = cursor.fetchall()
        books_list = []
        # Получаем список всех этапов и определяем, какие из них посекционные
        stages_config = get_all_stages_ordered_workflow()
        per_section_stages = [stage['stage_name'] for stage in stages_config if stage.get('is_per_section')]
        for row in rows:
            book_info = dict(row)
            # Получаем количество секций для отображения прогресса
            book_info['total_sections_count'] = get_section_count_for_book_workflow(book_info['book_id'])
            # Для каждого посекционного этапа добавляем processed_sections_count_<stage_name>
            for stage_name in per_section_stages:
                key =f'processed_sections_count_{stage_name}' 
                book_info[key] = get_processed_sections_count_for_stage_workflow(book_info['book_id'], stage_name)
            # Обеспечиваем наличие target_language
            book_info['target_language'] = book_info.get('target_language') or 'russian' # Default to russian
            books_list.append(book_info)
        return books_list
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении списка книг: {e}")
        traceback.print_exc()
        return []


def update_book_workflow_status(book_id, new_status, error_message=None):
    """Обновляет общий статус рабочего процесса книги."""
    db = get_workflow_db()
    try:
        with db:
            db.execute('''
                UPDATE books
                SET current_workflow_status = ?, workflow_error_message = ?
                WHERE book_id = ?
            ''', (new_status, error_message, book_id))
        print(f"[WorkflowDB] Общий статус книги '{book_id}' обновлен на '{new_status}'.")
        return True
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА обновления статуса книги '{book_id}': {e}")
        traceback.print_exc()
        return False

def update_generated_prompt_ext(book_id, prompt_text):
    """Обновляет сгенерированное дополнение к промпту для книги."""
    db = get_workflow_db()
    try:
        with db:
            db.execute('''
                UPDATE books
                SET generated_prompt_ext = ?
                WHERE book_id = ?
            ''', (prompt_text, book_id))
        print(f"[WorkflowDB] Сгенерированный prompt_ext для книги '{book_id}' обновлен (длина: {len(prompt_text) if prompt_text else 0}).")
        return True
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА обновления generated_prompt_ext для книги '{book_id}': {e}")
        traceback.print_exc()
        return False

def delete_book_workflow(book_id):
    """Удаляет книгу и все связанные записи (секций, статусов этапов)."""
    db = get_workflow_db()
    try:
        with db:
            # ON DELETE CASCADE в FOREIGN KEY позаботится об удалении из sections, section_stage_statuses, book_stage_statuses
            db.execute('DELETE FROM books WHERE book_id = ?', (book_id,))
        print(f"[WorkflowDB] Книга '{book_id}' и связанные записи удалены из БД.")
        return True
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА удаления книги '{book_id}': {e}")
        traceback.print_exc()
        return False

# --- Функции работы с секциями ---

def create_section_workflow(book_id, section_epub_id, section_title, translated_title, order_in_book):
    """Создает запись о секции в таблице sections и начальные статусы для всех per-section этапов."""
    db = get_workflow_db()
    try:
        with db:
            cursor = db.execute('''
                INSERT INTO sections (book_id, section_epub_id, section_title, translated_title, order_in_book)
                VALUES (?, ?, ?, ?, ?)
            ''', (book_id, section_epub_id, section_title, translated_title, order_in_book))
            section_id = cursor.lastrowid # Получаем ID только что вставленной записи

            # Создаем записи статусов для всех per-section этапов для этой секции
            per_section_stages_cursor = db.execute("SELECT stage_name FROM workflow_stages WHERE is_per_section = TRUE;")
            stage_statuses_data = [(section_id, stage['stage_name'], 'pending') for stage in per_section_stages_cursor.fetchall()]
            if stage_statuses_data: # Вставляем только если есть per-section этапы
                 db.executemany('''
                     INSERT INTO section_stage_statuses (section_id, stage_name, status)
                     VALUES (?, ?, ?)
                 ''', stage_statuses_data)

        print(f"[WorkflowDB] Секция '{section_epub_id}' для книги '{book_id}' добавлена в БД с начальными статусами этапов.")
        return True
    except sqlite3.IntegrityError:
        print(f"[WorkflowDB] Секция '{section_epub_id}' для книги '{book_id}' уже существует в БД.")
        return False
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при создании секции '{section_epub_id}' для книги '{book_id}': {e}")
        traceback.print_exc()
        return False

def get_sections_for_book_workflow(book_id):
    """Получает все секции для данной книги из таблицы sections с их статусами по этапам."""
    db = get_workflow_db()
    try:
        # Получаем секции
        sections_cursor = db.execute('''
            SELECT section_id, book_id, section_epub_id, section_title, translated_title, order_in_book
            FROM sections
            WHERE book_id = ?
            ORDER BY order_in_book;
        ''', (book_id,))
        sections_list = [dict(row) for row in sections_cursor.fetchall()]

        # Для каждой секции получаем ее статусы этапов
        for section_info in sections_list:
             section_info['stage_statuses'] = get_section_stage_statuses_workflow(section_info['section_id'])

        return sections_list
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении секций для книги '{book_id}': {e}")
        traceback.print_exc()
        return []

def get_section_by_epub_id_workflow(book_id, section_epub_id):
    """Получает информацию о секции по ID книги и EPUB ID секции."""
    db = get_workflow_db()
    try:
        cursor = db.execute('''
            SELECT * FROM sections
            WHERE book_id = ? AND section_epub_id = ?
        ''', (book_id, section_epub_id))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении секции по EPUB ID '{section_epub_id}' для книги '{book_id}': {e}")
        traceback.print_exc()
        return None

def get_section_by_id_workflow(book_id, section_id):
    """Получает информацию о секции по внутреннему ID секции (PK) и ID книги."""
    db = get_workflow_db()
    try:
        cursor = db.execute('''
            SELECT *
            FROM sections
            WHERE section_id = ? AND book_id = ?
        ''', (section_id, book_id))
        row = cursor.fetchone()
        if row:
            section_info = dict(row)
            # Получаем статусы этапов для этой секции
            section_info['stage_statuses'] = get_section_stage_statuses_workflow(section_id)
            return section_info
        return None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении секции по ID '{section_id}' для книги '{book_id}': {e}")
        traceback.print_exc()
        return None

def get_section_count_for_book_workflow(book_id):
    """Возвращает общее количество секций для книги."""
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT COUNT(*) FROM sections WHERE book_id = ?', (book_id,))
        return cursor.fetchone()[0]
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при подсчете секций для книги '{book_id}': {e}")
        traceback.print_exc()
        return 0

def get_completed_sections_count_for_stage_workflow(book_id, stage_name):
    """Возвращает количество секций книги, завершивших определенный per-section этап ('completed' или 'cached')."""
    db = get_workflow_db()
    try:
        # Debug print: Show the query and parameters
        query = '''
            SELECT COUNT(sss.id)
            FROM section_stage_statuses sss
            JOIN sections s ON sss.section_id = s.section_id
            WHERE s.book_id = ? AND sss.stage_name = ? AND sss.status IN ('completed', 'cached', 'completed_empty')
        '''
        params = (book_id, stage_name)
        
        cursor = db.execute('''
            SELECT COUNT(sss.id)
            FROM section_stage_statuses sss
            JOIN sections s ON sss.section_id = s.section_id
            WHERE s.book_id = ? AND sss.stage_name = ? AND sss.status IN ('completed', 'cached', 'completed_empty')
        ''', (book_id, stage_name))
        count = cursor.fetchone()[0]
        return count
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при подсчете завершенных секций для книги '{book_id}' этапа '{stage_name}': {e}")
        traceback.print_exc()
        return 0

def get_error_sections_count_for_stage_workflow(book_id, stage_name):
    """Возвращает количество секций книги, завершивших определенный per-section этап со статусом ошибки.
    Статусы ошибок начинаются с 'error_'.
    """
    db = get_workflow_db()
    try:
        # Debug print: Show the query and parameters
        query = '''
            SELECT COUNT(sss.id)
            FROM section_stage_statuses sss
            JOIN sections s ON sss.section_id = s.section_id
            WHERE s.book_id = ? AND sss.stage_name = ? AND (sss.status = 'error' OR sss.status LIKE 'error_%')
        '''
        params = (book_id, stage_name)
        print(f"[WorkflowDB] Подсчет ошибочных секций: Запрос: {query.strip()} | Параметры: {params}")

        cursor = db.execute('''
            SELECT COUNT(sss.id)
            FROM section_stage_statuses sss
            JOIN sections s ON sss.section_id = s.section_id
            WHERE s.book_id = ? AND sss.stage_name = ? AND (sss.status = 'error' OR sss.status LIKE 'error_%')
        ''', (book_id, stage_name))
        count = cursor.fetchone()[0]
        print(f"[WorkflowDB] Подсчет ошибочных секций: Результат COUNT: {count}")
        return count
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при подсчете ошибочных секций для книги '{book_id}' этапа '{stage_name}': {e}")
        traceback.print_exc()
        return 0


# --- Функции работы со статусами этапов (для секций и книги) ---

def get_section_stage_statuses_workflow(section_id):
    """Получает статусы всех этапов для конкретной секции из section_stage_statuses."""
    db = get_workflow_db()
    try:
        cursor = db.execute('''
            SELECT stage_name, status, model_name, error_message, start_time, end_time FROM section_stage_statuses
            WHERE section_id = ?
        ''', (section_id,))
        rows = cursor.fetchall()
        statuses = {}
        for row in rows:
            statuses[row['stage_name']] = dict(row)
        return statuses
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении статусов этапов для секции '{section_id}': {e}")
        traceback.print_exc()
        return {}

def update_section_stage_status_workflow(
    book_id, section_id, stage_name, status, model_name=None, error_message=None
):
    """Обновляет статус определенного этапа для конкретной секции в section_stage_statuses."""
    db = get_workflow_db()
    try:
        with db:
            cursor = db.execute('''
                SELECT 1 FROM section_stage_statuses
                WHERE section_id = ? AND stage_name = ?
            ''', (section_id, stage_name))
            exists = cursor.fetchone()

            # Определяем значения для start_time и end_time в зависимости от нового статуса
            current_time = time.time() # Получаем текущее время в виде timestamp
            start_time_val = current_time if status in ('processing', 'queued') else None
            end_time_val = current_time if status in ('completed', 'cached', 'completed_empty', 'error') else None

            if exists:
                # Запись существует, обновляем
                print(f"[WorkflowDB] Обновление статуса для секции {section_id} этапа {stage_name} на '{status}'.")

                # Получаем текущие значения времени, чтобы не перезаписывать их
                cursor = db.execute('''
                    SELECT start_time, end_time FROM section_stage_statuses
                    WHERE section_id = ? AND stage_name = ?
                ''', (section_id, stage_name))
                current_times = cursor.fetchone()
                
                # Сохраняем существующие значения времени, если они есть
                if current_times:
                    existing_start_time = current_times['start_time']
                    existing_end_time = current_times['end_time']
                    
                    # Обновляем start_time только если его нет или если новый статус требует его установки
                    if start_time_val is not None:
                        final_start_time = start_time_val
                    elif existing_start_time is not None:
                        final_start_time = existing_start_time
                    else:
                        final_start_time = None
                    
                    # Обновляем end_time только если новый статус требует его установки
                    if end_time_val is not None:
                        final_end_time = end_time_val
                    elif existing_end_time is not None:
                        final_end_time = existing_end_time
                    else:
                        final_end_time = None
                else:
                    final_start_time = start_time_val
                    final_end_time = end_time_val

                # Строим SQL запрос для обновления с сохранением времени
                db.execute('''
                    UPDATE section_stage_statuses
                    SET status = ?, model_name = ?, error_message = ?, start_time = ?, end_time = ?
                    WHERE section_id = ? AND stage_name = ?
                ''', (status, model_name, error_message, final_start_time, final_end_time,
                      section_id, stage_name))

            else:
                # Записи не существует, вставляем новую
                print(f"[WorkflowDB] Вставка новой записи статуса для секции {section_id} этапа {stage_name} со статусом '{status}'.")
                db.execute('''
                    INSERT INTO section_stage_statuses (section_id, stage_name, status, model_name, error_message, start_time, end_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (section_id, stage_name, status, model_name, error_message,
                     start_time_val, end_time_val))

            # Явный коммит после обновления/вставки статуса секции
            db.commit()
            return True # Возвращаем True только при успешном коммите
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА обновления статуса этапа '{stage_name}' для секции '{section_id}': {e}")
        traceback.print_exc()
        return False


def get_book_stage_statuses_workflow(book_id):
    """Получает статусы всех этапов на уровне книги для данной книги из book_stage_statuses."""
    db = get_workflow_db()
    try:
        cursor = db.execute('''
            SELECT bss.stage_name, bss.status, bss.model_name, bss.error_message, ws.display_name, ws.stage_order FROM book_stage_statuses bss
            JOIN workflow_stages ws ON bss.stage_name = ws.stage_name
            WHERE bss.book_id = ?
            ORDER BY ws.stage_order;
        ''', (book_id,))
        rows = cursor.fetchall()
        # Используем OrderedDict для сохранения порядка этапов
        statuses = OrderedDict()
        for row in rows:
            statuses[row['stage_name']] = dict(row)
        return statuses
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении статусов этапов книги '{book_id}': {e}")
        traceback.print_exc()
        return OrderedDict()


def update_book_stage_status_workflow(book_id, stage_name, status, model_name=None, error_message=None, completed_count=None, total_count=None):
    """Обновляет статус определенного этапа на уровне книги в book_stage_statuses."""
    db = get_workflow_db()
    try:
        with db:
            # Пытаемся обновить существующую запись
            print(f"[WorkflowDB] Обновление статуса этапа '{stage_name}' для книги '{book_id}' на '{status}'.")

            # Определяем значения для start_time и end_time в зависимости от нового статуса
            current_time = time.time() # Получаем текущее время в виде timestamp
            start_time_val = current_time if status in ('processing', 'queued') else None
            end_time_val = current_time if status in ('completed', 'error') else None # Book-level statuses don't have cached/completed_empty

            # Получаем текущие значения времени, чтобы не перезаписывать их
            cursor = db.execute('''
                SELECT start_time, end_time FROM book_stage_statuses
                WHERE book_id = ? AND stage_name = ?
            ''', (book_id, stage_name))
            current_times = cursor.fetchone()
            
            # Сохраняем существующие значения времени, если они есть
            if current_times:
                existing_start_time = current_times['start_time']
                existing_end_time = current_times['end_time']
                
                # Обновляем start_time только если его нет или если новый статус требует его установки
                if start_time_val is not None:
                    final_start_time = start_time_val
                elif existing_start_time is not None:
                    final_start_time = existing_start_time
                else:
                    final_start_time = None
                
                # Обновляем end_time только если новый статус требует его установки
                if end_time_val is not None:
                    final_end_time = end_time_val
                elif existing_end_time is not None:
                    final_end_time = existing_end_time
                else:
                    final_end_time = None
            else:
                final_start_time = start_time_val
                final_end_time = end_time_val

            # Строим SQL запрос для обновления с сохранением времени
            cursor = db.execute('''
                UPDATE book_stage_statuses
                SET status = ?, model_name = ?, error_message = ?, start_time = ?, end_time = ?
                WHERE book_id = ? AND stage_name = ?
            ''', (status, model_name, error_message, final_start_time, final_end_time,
                  book_id, stage_name))

            # Если обновление не затронуло ни одной строки, значит записи не было, вставляем
            if cursor.rowcount == 0:
                print(f"[WorkflowDB] WARNING: Статус для книги {book_id} этапа {stage_name} не найден при попытке UPDATE. Вставляем новую запись.")
                db.execute('''
                    INSERT INTO book_stage_statuses (book_id, stage_name, status, model_name, error_message, start_time, end_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (book_id, stage_name, status, model_name, error_message,
                      start_time_val, end_time_val))
        print(f"[WorkflowDB] Статус этапа '{stage_name}' для книги '{book_id}' обновлен на '{status}'.")
        return True
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА обновления статуса этапа '{stage_name}' для книги '{book_id}': {e}")
        traceback.print_exc()
        return False

# --- Вспомогательные функции для рабочего процесса ---

def get_stage_order_workflow(stage_name):
    """Возвращает порядок выполнения этапа по его имени."""
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT stage_order FROM workflow_stages WHERE stage_name = ?', (stage_name,))
        row = cursor.fetchone()
        return row['stage_order'] if row else None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении порядка этапа '{stage_name}': {e}")
        traceback.print_exc()
        return None

def get_stage_by_order_workflow(stage_order):
    """Возвращает информацию об этапе по его порядку выполнения."""
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT stage_name, display_name, is_per_section FROM workflow_stages WHERE stage_order = ?', (stage_order,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении этапа по порядку '{stage_order}': {e}")
        traceback.print_exc()
        return None

def get_next_stage_name_workflow(current_stage_name):
    """Возвращает имя следующего этапа после текущего."""
    db = get_workflow_db()
    try:
        current_order = get_stage_order_workflow(current_stage_name)
        if current_order is None:
            return None # Текущий этап не найден

        next_order = current_order + 1
        next_stage = get_stage_by_order_workflow(next_order)
        return next_stage['stage_name'] if next_stage else None
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении следующего этапа после '{current_stage_name}': {e}")
        traceback.print_exc()
        return None

def get_per_section_stage_names_workflow():
    """Возвращает список имен этапов, применяемых к каждой секции."""
    db = get_workflow_db()
    try:
        cursor = db.execute("SELECT stage_name FROM workflow_stages WHERE is_per_section = TRUE ORDER BY stage_order;")
        return [row['stage_name'] for row in cursor.fetchall()]
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении списка per-section этапов: {e}")
        traceback.print_exc()
        return []

# --- НОВАЯ ФУНКЦИЯ: Получить имя следующего посекционного этапа ---
def get_next_per_section_stage_name_workflow(current_stage_name: str) -> str | None:
    """
    Возвращает имя следующего посекционного этапа после текущего, основываясь на порядке.
    Возвращает None, если текущий этап последний посекционный или не найден.
    """
    db = get_workflow_db()
    try:
        current_order = get_stage_order_workflow(current_stage_name)
        if current_order is None:
            return None # Текущий этап не найден

        # Ищем следующий этап с большим порядковым номером, который является посекционным
        cursor = db.execute('''
            SELECT stage_name FROM workflow_stages
            WHERE stage_order > ? AND is_per_section = TRUE
            ORDER BY stage_order ASC
            LIMIT 1;
        ''', (current_order,))
        row = cursor.fetchone()

        return row['stage_name'] if row else None

    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении следующего per-section этапа после '{current_stage_name}': {e}")
        traceback.print_exc()
        return None
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---

# TODO: Возможно, добавить функцию для сброса статусов 'processing' при старте приложения


# --- Отдельный файловый кэш для рабочего процесса ---
# Мы можем создать отдельный Cache Manager для workflow, или добавить опцию basePath
# в текущий Cache Manager. Пока оставим текущий и решим потом, как его разделить,
# когда будем сохранять результаты суммаризации и перевода для workflow.
# Для простоты на первом этапе (только суммаризация) можно временно использовать
# существующий cache_manager, но помнить, что кеш суммаризации может конфликтовать
# с кешем перевода старого функционала, если они используют одинаковые имена файлов.
# Лучше сразу предусмотреть отдельную директорию или префикс в именах файлов кеша.
# Пример: .epub_workflow_cache или .epub_cache/workflow/<book_id>/...

# TODO: Модифицировать cache_manager.py или создать workflow_cache_manager.py

# --- Вспомогательная функция для инициализации статусов этапов книги ---
def _initialize_book_stage_statuses(book_id):
    """
    Инициализирует записи статусов для всех определенных этапов рабочего процесса
    для данной книги.
    """
    print(f"[WorkflowDB] Попытка инициализации статусов этапов для книги ID: {book_id}")
    db = get_workflow_db()
    try:
        # Получаем все этапы рабочего процесса
        stages_cursor = db.execute("SELECT stage_name, display_name FROM workflow_stages;")
        all_stages = stages_cursor.fetchall()

        if not all_stages:
            print("[WorkflowDB] WARNING: Нет определенных этапов рабочего процесса в таблице workflow_stages. Инициализация статусов этапов книги пропущена.")
            return

        for stage in all_stages:
            stage_name = stage['stage_name']

            # Проверяем, существует ли уже запись для этой книги и этого этапа
            cursor = db.execute('''
                SELECT 1 FROM book_stage_statuses
                WHERE book_id = ? AND stage_name = ?
            ''', (book_id, stage_name))
            exists = cursor.fetchone()

            if not exists:
                print(f"[WorkflowDB] Инициализация статуса этапа '{stage_name}' для книги '{book_id}'.")
                current_time = time.time() # Получаем текущее время
                # Вставляем новую запись с начальным статусом 'pending' и current_time для start_time
                db.execute('''
                    INSERT INTO book_stage_statuses (book_id, stage_name, status, model_name, error_message, start_time, end_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (book_id, stage_name, 'pending', None, None, current_time, None)) # <-- Используем current_time для start_time
            else:
                 print(f"[WorkflowDB] Статус этапа '{stage_name}' для книги '{book_id}' уже существует. Пропускаем инициализацию.")

        db.commit()
        print(f"[WorkflowDB] Инициализация статусов этапов для книги ID {book_id} завершена.")
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при инициализации статусов этапов книги {book_id}: {e}")
        db.rollback()
        traceback.print_exc()
    finally:
        pass # Ничего не делаем, закрытие через g.teardown
# --- Конец вспомогательной функции ---

# --- НОВАЯ ФУНКЦИЯ: Получить количество обработанных секций для этапа (включая completed, skipped, completed_empty) ---
def get_processed_sections_count_for_stage_workflow(book_id: str, stage_name: str) -> int:
    """Считает количество секций для книги, которые завершили обработку на указанном этапе (статусы completed, skipped, completed_empty)."""
    db = get_workflow_db()
    try:
        # Нам нужно сначала получить внутренние section_id для данной книги
        section_ids_cursor = db.execute('SELECT section_id FROM sections WHERE book_id = ?;', (book_id,))
        section_ids = [row['section_id'] for row in section_ids_cursor.fetchall()]
        
        if not section_ids:
            return 0 # Нет секций для этой книги
        
        # Формируем строку с плейсхолдерами для WHERE IN
        placeholders = ', '.join('?' for _ in section_ids)
        
        cursor = db.execute(f'''
            SELECT COUNT(*)
            FROM section_stage_statuses
            WHERE section_id IN ({placeholders})
            AND stage_name = ?
            AND status IN ('completed', 'skipped', 'completed_empty');
        ''', section_ids + [stage_name]) # Передаем список section_ids и stage_name как параметры
        
        count = cursor.fetchone()[0] # Получаем результат COUNT(*)
        # print(f"[WorkflowDB] Processed sections count for {book_id}/{stage_name}: {count}") # Отладочный вывод
        return count
        
    except Exception as e:
        print(f"[WorkflowDB] ОШИБКА при получении количества processed секций для {book_id}/{stage_name}: {e}")
        traceback.print_exc()
        return 0
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---

# --- Функции работы с этапами рабочего процесса ---

def get_all_stages_ordered_workflow() -> List[Dict[str, Any]]:
    """
    Retrieves all workflow stages from the database, ordered by stage_order.
    """
    db = get_workflow_db()
    try:
        cursor = db.execute('SELECT stage_name, stage_order, display_name, is_per_section FROM workflow_stages ORDER BY stage_order')
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"[WorkflowDB] ERROR retrieving all stages: {e}")
        traceback.print_exc()
        return []

def add_reduce_text_stage():
    """Добавляет этап reduce_text в существующую базу данных."""
    db = get_workflow_db()
    try:
        # Проверяем, существует ли этап
        cursor = db.execute("SELECT 1 FROM workflow_stages WHERE stage_name = 'reduce_text'")
        if cursor.fetchone():
            print("[WorkflowDB] Этап 'reduce_text' уже существует.")
            return True
        
        # Добавляем этап
        db.execute("INSERT INTO workflow_stages (stage_name, stage_order, display_name, is_per_section) VALUES ('reduce_text', 15, 'Сокращение текста', 0)")
        db.commit()
        print("[WorkflowDB] Этап 'reduce_text' добавлен в базу данных.")
        return True
    except Exception as e:
        print(f"[WorkflowDB] Ошибка добавления этапа 'reduce_text': {e}")
        return False

def migrate_stage_orders_to_decimals():
    """Мигрирует stage_order к значениям с шагом 10 (10, 20, 30, 40)."""
    db = get_workflow_db()
    try:
        # Проверяем, нужно ли мигрировать (если есть stage_order < 10, значит миграция нужна)
        cursor = db.execute("SELECT COUNT(*) FROM workflow_stages WHERE stage_order < 10")
        needs_migration = cursor.fetchone()[0] > 0
        
        if not needs_migration:
            print("[WorkflowDB] Миграция stage_order не нужна (уже в новом формате).")
            return True
        
        print("[WorkflowDB] Начинаем миграцию stage_order к новому формату...")
        
        # Обновляем stage_order для каждого этапа
        stage_mapping = {
            'summarize': 10,
            'reduce_text': 15,
            'analyze': 20,
            'translate': 30,
            'epub_creation': 40
        }
        
        for stage_name, new_order in stage_mapping.items():
            db.execute("UPDATE workflow_stages SET stage_order = ? WHERE stage_name = ?", (new_order, stage_name))
            print(f"[WorkflowDB] Обновлен {stage_name}: stage_order = {new_order}")
        
        db.commit()
        print("[WorkflowDB] Миграция stage_order завершена.")
        return True
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка миграции stage_order: {e}")
        db.rollback()
        return False

# --- КОНЕЦ ФУНКЦИЙ МИГРАЦИИ ---

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С ТОКЕНАМИ ДОСТУПА ---

def generate_access_token() -> str:
    """Генерирует уникальный токен доступа"""
    import secrets
    return secrets.token_urlsafe(32)

def set_book_access_token(book_id: str, access_token: str) -> bool:
    """Устанавливает токен доступа для книги"""
    db = get_workflow_db()
    try:
        print(f"[WorkflowDB] Попытка установить токен для книги {book_id}: {access_token}")
        result = db.execute("UPDATE books SET access_token = ? WHERE book_id = ?", (access_token, book_id))
        db.commit()
        if result.rowcount == 0:
            print(f"[WorkflowDB] Не удалось найти книгу для установки токена: {book_id}")
            return False
        print(f"[WorkflowDB] Токен доступа установлен для книги {book_id}: {access_token}")
        return True
    except Exception as e:
        print(f"[WorkflowDB] Ошибка установки токена для книги {book_id}: {e}")
        return False

def get_book_by_access_token(access_token: str):
    print(f'[DEBUG] get_book_by_access_token вызван с access_token={access_token}')
    db = get_workflow_db()
    try:
        cursor = db.execute("SELECT * FROM books WHERE access_token = ?", (access_token,))
        book = cursor.fetchone()
        print(f'[DEBUG] Результат поиска по токену: {book}')
        if book:
            return dict(book)
        return None
    except Exception as e:
        print(f"[WorkflowDB] Ошибка получения книги по токену {access_token}: {e}")
        traceback.print_exc()
        return None

def get_telegram_users_for_book(access_token: str) -> list:
    """Получает список пользователей Telegram, подписанных на уведомления о книге"""
    db = get_workflow_db()
    try:
        cursor = db.execute("""
            SELECT user_id, created_at, is_active 
            FROM telegram_users 
            WHERE access_token = ? AND is_active = TRUE
        """, (access_token,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"[WorkflowDB] Ошибка получения пользователей Telegram для токена {access_token}: {e}")
        return []

def add_telegram_user(user_id: str, access_token: str) -> bool:
    """Добавляет пользователя Telegram для получения уведомлений"""
    db = get_workflow_db()
    try:
        db.execute("""
            INSERT OR REPLACE INTO telegram_users (user_id, access_token, is_active)
            VALUES (?, ?, TRUE)
        """, (user_id, access_token))
        db.commit()
        print(f"[WorkflowDB] Пользователь Telegram {user_id} добавлен для токена {access_token}")
        return True
    except Exception as e:
        print(f"[WorkflowDB] Ошибка добавления пользователя Telegram {user_id}: {e}")
        return False

def remove_telegram_user(user_id: str) -> bool:
    """Удаляет пользователя Telegram из подписок"""
    db = get_workflow_db()
    try:
        db.execute("DELETE FROM telegram_users WHERE user_id = ?", (user_id,))
        db.commit()
        print(f"[WorkflowDB] Пользователь Telegram {user_id} удален из подписок")
        return True
    except Exception as e:
        print(f"[WorkflowDB] Ошибка удаления пользователя Telegram {user_id}: {e}")
        return False

def get_telegram_user_subscriptions(user_id: str) -> list:
    """Получает все подписки пользователя Telegram"""
    db = get_workflow_db()
    try:
        cursor = db.execute("""
            SELECT tu.access_token, tu.created_at, tu.is_active, b.book_id, b.filename, b.target_language
            FROM telegram_users tu
            JOIN books b ON tu.access_token = b.access_token
            WHERE tu.user_id = ? AND tu.is_active = TRUE
        """, (user_id,))
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"[WorkflowDB] Ошибка получения подписок пользователя Telegram {user_id}: {e}")
        return []

def get_workflow_book_status(book_id: str) -> dict:
    """Получает полный статус книги для Telegram бота (аналог API endpoint)"""
    try:
        book_info = get_book_workflow(book_id)
        if not book_info:
            return None
        
        # Получаем конфигурацию этапов
        stages_config = get_all_stages_ordered_workflow()
        stages_config_map = {stage['stage_name']: stage for stage in stages_config}
        
        # Получаем секции
        sections = get_sections_for_book_workflow(book_id)
        total_sections = len(sections)
        
        # Формируем ответ в том же формате, что и API
        response_data = {
            "book_id": book_info.get('book_id'),
            "filename": book_info.get('filename'),
            "book_title": book_info.get('book_title', book_info.get('filename')),
            "target_language": book_info.get('target_language'),
            "current_workflow_status": book_info.get('current_workflow_status'),
            "book_stage_statuses": {},
            "total_sections_count": total_sections,
            "sections_status_summary": {}
        }
        
        # Добавляем статусы этапов
        book_stage_statuses = book_info.get('book_stage_statuses', {})
        for stage_name, stage_data in book_stage_statuses.items():
            response_data['book_stage_statuses'][stage_name] = stage_data
            config = stages_config_map.get(stage_name)
            response_data['book_stage_statuses'][stage_name]['is_per_section'] = config.get('is_per_section', False) if config else False
        
        # Формируем сводку статусов секций для посекционных этапов
        for stage_name, stage_config in stages_config_map.items():
            if stage_config.get('is_per_section'):
                sections_status_summary = {
                    'total': total_sections,
                    'completed': 0,
                    'completed_empty': 0,
                    'processing': 0,
                    'queued': 0,
                    'error': 0,
                    'skipped': 0,
                    'pending': 0,
                    'cached': 0,
                }
                
                for section in sections:
                    section_stage_status = section.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending')
                    if section_stage_status in sections_status_summary:
                        sections_status_summary[section_stage_status] += 1
                
                response_data['sections_status_summary'][stage_name] = sections_status_summary
        
        return response_data
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка получения статуса книги {book_id}: {e}")
        return None

# --- КОНЕЦ ФУНКЦИЙ ДЛЯ РАБОТЫ С ТОКЕНАМИ ДОСТУПА ---

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЬСКИМИ СЕССИЯМИ ---

def create_user_session(access_token: str, session_duration_hours: int = 24) -> str:
    """Создает новую сессию пользователя и возвращает session_id"""
    import secrets
    import datetime
    
    db = get_workflow_db()
    try:
        # Проверяем, что книга с таким токеном существует
        cursor = db.execute("SELECT 1 FROM books WHERE access_token = ?", (access_token,))
        if not cursor.fetchone():
            print(f"[WorkflowDB] Книга с токеном {access_token} не найдена")
            return None
        
        # Генерируем уникальный session_id
        session_id = secrets.token_urlsafe(32)
        
        # Вычисляем время истечения сессии
        expires_at = datetime.datetime.now() + datetime.timedelta(hours=session_duration_hours)
        
        # Создаем сессию
        db.execute("""
            INSERT INTO user_sessions (session_id, access_token, expires_at)
            VALUES (?, ?, ?)
        """, (session_id, access_token, expires_at))
        db.commit()
        
        print(f"[WorkflowDB] Создана сессия {session_id} для токена {access_token}")
        return session_id
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка создания сессии для токена {access_token}: {e}")
        return None

def get_session_access_token(session_id: str) -> str:
    """Получает access_token по session_id, если сессия активна"""
    import datetime
    
    db = get_workflow_db()
    try:
        cursor = db.execute("""
            SELECT access_token 
            FROM user_sessions 
            WHERE session_id = ? AND expires_at > ?
        """, (session_id, datetime.datetime.now()))
        
        row = cursor.fetchone()
        if row:
            # Обновляем время последнего доступа
            db.execute("""
                UPDATE user_sessions 
                SET last_accessed = ? 
                WHERE session_id = ?
            """, (datetime.datetime.now(), session_id))
            db.commit()
            
            return row['access_token']
        return None
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка получения токена для сессии {session_id}: {e}")
        return None

def delete_expired_sessions():
    """Удаляет истекшие сессии"""
    import datetime
    
    db = get_workflow_db()
    try:
        cursor = db.execute("DELETE FROM user_sessions WHERE expires_at <= ?", (datetime.datetime.now(),))
        deleted_count = cursor.rowcount
        db.commit()
        
        if deleted_count > 0:
            print(f"[WorkflowDB] Удалено {deleted_count} истекших сессий")
        
        return deleted_count
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка удаления истекших сессий: {e}")
        return 0

def delete_user_session(session_id: str) -> bool:
    """Удаляет конкретную сессию пользователя"""
    db = get_workflow_db()
    try:
        cursor = db.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))
        deleted = cursor.rowcount > 0
        db.commit()
        
        if deleted:
            print(f"[WorkflowDB] Сессия {session_id} удалена")
        
        return deleted
        
    except Exception as e:
        print(f"[WorkflowDB] Ошибка удаления сессии {session_id}: {e}")
        return False

# --- КОНЕЦ ФУНКЦИЙ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЬСКИМИ СЕССИЯМИ ---