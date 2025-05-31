# --- START OF FILE test_workflow_db_manager.py ---

import pytest
import sqlite3
import os
import json
import time
from flask import Flask, g

# Импортируем наш новый модуль
import workflow_db_manager

# --- Фикстуры для тестов ---

# Фикстура для временной базы данных в памяти
@pytest.fixture
def temp_workflow_db():
    """Создает временную базу данных в памяти для каждого теста."""
    # Указываем ин-мемори БД ':memory:'
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    # Используем g Flask для эмуляции контекста приложения
    app = Flask(__name__)
    with app.app_context():
        g._workflow_database = db
        workflow_db_manager.init_workflow_db() # Инициализируем схему в памяти
        yield db # Передаем соединение тесту
        db.close() # Закрываем соединение после теста

# --- Тесты ---

def test_init_workflow_db(temp_workflow_db):
    """Проверяем, что таблицы созданы и workflow_stages заполнены."""
    db = temp_workflow_db
    cursor = db.cursor()

    # Проверяем существование таблиц
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in cursor.fetchall()]
    assert 'books' in tables
    assert 'sections' in tables
    assert 'workflow_stages' in tables
    assert 'section_stage_statuses' in tables
    assert 'book_stage_statuses' in tables

    # Проверяем заполнение workflow_stages
    cursor.execute("SELECT COUNT(*) FROM workflow_stages;")
    count = cursor.fetchone()[0]
    assert count == 4 # Проверяем, что 4 этапа были добавлены

    cursor.execute("SELECT stage_name, stage_order, display_name, is_per_section FROM workflow_stages ORDER BY stage_order;")
    stages = [dict(row) for row in cursor.fetchall()]
    assert stages[0] == {'stage_name': 'summarize', 'stage_order': 1, 'display_name': 'Суммаризация', 'is_per_section': 1}
    assert stages[1] == {'stage_name': 'analyze', 'stage_order': 2, 'display_name': 'Анализ трудностей', 'is_per_section': 0}
    assert stages[2] == {'stage_name': 'translate', 'stage_order': 3, 'display_name': 'Перевод', 'is_per_section': 1}
    assert stages[3] == {'stage_name': 'epub_creation', 'stage_order': 4, 'display_name': 'Создание EPUB', 'is_per_section': 0}


def test_create_and_get_book_workflow(temp_workflow_db):
    """Тестируем создание и получение книги."""
    book_id = 'test-book-123'
    filename = 'test_book.epub'
    filepath = '/fake/path/test_book.epub'
    toc = [{'id': 'chap1', 'title': 'Chapter 1'}]
    target_language = 'english'

    success = workflow_db_manager.create_book_workflow(book_id, filename, filepath, toc, target_language)
    assert success is True

    book_info = workflow_db_manager.get_book_workflow(book_id)
    assert book_info is not None
    assert book_info['book_id'] == book_id
    assert book_info['filename'] == filename
    assert book_info['filepath'] == filepath
    assert book_info['toc'] == toc
    assert book_info['current_workflow_status'] == 'uploaded'
    assert book_info['target_language'] == target_language
    assert 'upload_time' in book_info # Проверяем наличие поля
    assert book_info['workflow_error_message'] is None
    assert book_info['generated_prompt_ext'] is None
    assert book_info['manual_prompt_ext'] is None

    # Проверяем наличие начальных статусов для book-level этапов
    book_stage_statuses = book_info['book_stage_statuses']
    assert 'analyze' in book_stage_statuses
    assert book_stage_statuses['analyze']['status'] == 'pending'
    assert 'epub_creation' in book_stage_statuses
    assert book_stage_statuses['epub_creation']['status'] == 'pending'
    assert len(book_stage_statuses) == 2 # Убедимся, что нет других статусов
    assert book_info['total_sections_count'] == 0
    assert book_info['completed_sections_count_summarize'] == 0


def test_create_and_get_section_workflow(temp_workflow_db):
    """Тестируем создание и получение секции, включая начальные статусы этапов."""
    book_id = 'test-book-456'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path/book.epub', [], 'russian') # Создаем книгу-родителя

    section_epub_id = 'chap1.xhtml'
    section_title = 'Chapter One'
    translated_title = 'Глава Один'
    order_in_book = 1

    success = workflow_db_manager.create_section_workflow(book_id, section_epub_id, section_title, translated_title, order_in_book)
    assert success is True

    section_info = workflow_db_manager.get_section_by_epub_id_workflow(book_id, section_epub_id)
    assert section_info is not None
    assert section_info['book_id'] == book_id
    assert section_info['section_epub_id'] == section_epub_id
    assert section_info['section_title'] == section_title
    assert section_info['translated_title'] == translated_title
    assert section_info['order_in_book'] == order_in_book
    assert 'section_id' in section_info # Проверяем, что ID присвоен

    # Проверяем создание начальных статусов для per-section этапов ('summarize', 'translate')
    statuses = workflow_db_manager.get_section_stage_statuses_workflow(section_info['section_id'])
    assert 'summarize' in statuses
    assert statuses['summarize']['status'] == 'pending'
    assert 'translate' in statuses
    assert statuses['translate']['status'] == 'pending'
    assert 'analyze' not in statuses # analyze не per-section этап
    assert 'epub_creation' not in statuses # epub_creation не per-section этап


def test_update_section_stage_status_workflow(temp_workflow_db):
    """Тестируем обновление статуса этапа секции."""
    book_id = 'test-book-789'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path/book.epub', [], 'russian')
    workflow_db_manager.create_section_workflow(book_id, 'sec1.xhtml', 'Sec One', 'Секция Один', 1)

    section_info = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec1.xhtml')
    section_id = section_info['section_id']

    # Обновляем статус суммаризации
    print(f"DEBUG: Updating status for section_id={section_id}, stage_name='summarize'")
    workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'summarize', 'processing', model_name='test-model')
    db = workflow_db_manager.get_workflow_db()
    cursor = db.execute('SELECT start_time FROM section_stage_statuses WHERE section_id = ? AND stage_name = ?', (section_id, 'summarize'))
    db_start_time = cursor.fetchone()[0]
    print(f"DEBUG: start_time from direct DB query after update: {db_start_time}")
    print(f"DEBUG: Getting updated statuses for section_id={section_id}")
    updated_statuses = workflow_db_manager.get_section_stage_statuses_workflow(section_id)
    assert updated_statuses['summarize']['status'] == 'processing'
    assert updated_statuses['summarize']['model_name'] == 'test-model'
    assert updated_statuses['summarize']['error_message'] is None
    time.sleep(0.1)
    assert updated_statuses['summarize']['start_time'] is not None
    assert updated_statuses['summarize']['end_time'] is None

    # Обновляем статус на complete
    time.sleep(0.1) # Ждем немного, чтобы end_time отличался
    workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'summarize', 'complete', model_name='test-model-final', error_message='No error')
    updated_statuses_complete = workflow_db_manager.get_section_stage_statuses_workflow(section_id)
    assert updated_statuses_complete['summarize']['status'] == 'complete'
    assert updated_statuses_complete['summarize']['model_name'] == 'test-model-final' # Модель может обновиться
    assert updated_statuses_complete['summarize']['error_message'] == 'No error' # Сообщение об ошибке может быть установлено/сброшено
    assert updated_statuses_complete['summarize']['start_time'] is not None # start_time не должен меняться при завершении
    assert updated_statuses_complete['summarize']['end_time'] is not None # end_time должен быть установлен

    # Проверяем, что start_time < end_time
    assert updated_statuses_complete['summarize']['start_time'] < updated_statuses_complete['summarize']['end_time']


def test_update_book_stage_status_workflow(temp_workflow_db):
    """Тестируем обновление статуса этапа книги."""
    book_id = 'test-book-abc'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path/book.epub', [], 'russian')

    # Обновляем статус этапа analyze (который на уровне книги)
    workflow_db_manager.update_book_stage_status_workflow(book_id, 'analyze', 'processing', model_name='analyze-model')
    book_info = workflow_db_manager.get_book_workflow(book_id)
    statuses = book_info['book_stage_statuses']
    assert 'analyze' in statuses
    assert statuses['analyze']['status'] == 'processing'
    assert statuses['analyze']['model_name'] == 'analyze-model'
    assert statuses['analyze']['error_message'] is None


def test_get_all_books_workflow(temp_workflow_db):
    """Тестируем получение списка всех книг."""
    # Создаем несколько книг
    workflow_db_manager.create_book_workflow('book1', 'file1.epub', '/path1', [], 'en')
    time.sleep(1.0)
    workflow_db_manager.create_book_workflow('book2', 'file2.epub', '/path2', [], 'ru')

    # Создаем секции для одной из книг и обновляем статус суммаризации
    workflow_db_manager.create_section_workflow('book1', 'sec1a', 'Sec 1A', '', 1)
    sec1a_info = workflow_db_manager.get_section_by_epub_id_workflow('book1', 'sec1a')
    workflow_db_manager.update_section_stage_status_workflow('book1', sec1a_info['section_id'], 'summarize', 'complete')

    books_list = workflow_db_manager.get_all_books_workflow()

    assert len(books_list) == 2
    # Проверяем, что книги отсортированы по времени загрузки (последняя созданная - первая в списке)
    assert books_list[0]['book_id'] == 'book2'
    assert books_list[1]['book_id'] == 'book1'

    # Проверяем информацию о книгах
    book1_info = next(item for item in books_list if item['book_id'] == 'book1')
    assert book1_info['filename'] == 'file1.epub'
    assert book1_info['target_language'] == 'en'
    assert book1_info['total_sections_count'] == 1 # Добавлена 1 секция в тесте
    assert book1_info['completed_sections_count_summarize'] == 1 # Одна секция суммаризирована

    book2_info = next(item for item in books_list if item['book_id'] == 'book2')
    assert book2_info['filename'] == 'file2.epub'
    assert book2_info['target_language'] == 'ru'
    assert book2_info['total_sections_count'] == 0 # Нет секций
    assert book2_info['completed_sections_count_summarize'] == 0 # Нет завершенных секций


def test_delete_book_workflow(temp_workflow_db):
    """Тестируем удаление книги и каскадное удаление связанных записей."""
    book_id = 'book-to-delete-123'
    section_epub_id = 'section-to-delete'

    db = temp_workflow_db # Получаем соединение из фикстуры
    db.execute("PRAGMA foreign_keys = ON;") # Явно включаем внешние ключи в тестовом соединении

    # Используем прямое соединение для создания записей
    with db:
        # Создаем книгу
        db.execute('''
            INSERT INTO books (book_id, filename, filepath, target_language, current_workflow_status)
            VALUES (?, ?, ?, ?, ?)
        ''', (book_id, 'delete_me.epub', '/path/delete_me', 'es', 'uploaded'))

        # Создаем секцию для этой книги
        cursor = db.execute('''
            INSERT INTO sections (book_id, section_epub_id, section_title, order_in_book)
            VALUES (?, ?, ?, ?)
        ''', (book_id, section_epub_id, 'Delete Section', 1))
        section_id = cursor.lastrowid

        # Создаем статус для этой секции (минимально необходимое для теста)
        db.execute('''
            INSERT INTO section_stage_statuses (section_id, stage_name, status)
            VALUES (?, ?, ?)
        ''', (section_id, 'summarize', 'processing'))

    # Проверяем, что записи существуют перед удалением (используем функции модуля, они должны работать с тем же соединением через g)
    assert workflow_db_manager.get_book_workflow(book_id) is not None
    cursor = db.execute('SELECT COUNT(*) FROM section_stage_statuses WHERE section_id = ?', (section_id,))
    assert cursor.fetchone()[0] > 0

    # Удаляем книгу
    success = workflow_db_manager.delete_book_workflow(book_id)
    assert success is True

    # Проверяем, что книга удалена
    assert workflow_db_manager.get_book_workflow(book_id) is None

    # Проверяем, что секции и статусы секций тоже удалены (каскадное удаление)
    # Проверяем, что секция удалена прямым запросом к БД
    cursor = db.execute('SELECT COUNT(*) FROM sections WHERE section_id = ?', (section_id,))
    assert cursor.fetchone()[0] == 0
    cursor = db.execute('SELECT COUNT(*) FROM section_stage_statuses WHERE section_id = ?', (section_id,))
    assert cursor.fetchone()[0] == 0

    # Проверяем, что book-level статусы тоже удалены
    cursor = db.execute('SELECT COUNT(*) FROM book_stage_statuses WHERE book_id = ?', (book_id,))
    assert cursor.fetchone()[0] == 0


def test_create_book_workflow_unique_constraint(temp_workflow_db):
    """Тестируем ограничение уникальности для book_id при создании книги."""
    book_id = 'unique-book-test'
    workflow_db_manager.create_book_workflow(book_id, 'file1.epub', '/path1', [], 'en')

    # Попытка создать книгу с тем же book_id должна вернуть False
    success = workflow_db_manager.create_book_workflow(book_id, 'file2.epub', '/path2', [], 'ru')
    assert success is False

    # Проверяем, что в базе осталась только одна книга с этим ID
    books = workflow_db_manager.get_all_books_workflow()
    filtered_books = [b for b in books if b['book_id'] == book_id]
    assert len(filtered_books) == 1
    assert filtered_books[0]['filename'] == 'file1.epub' # Убеждаемся, что осталась первая книга


def test_create_section_workflow_unique_constraint(temp_workflow_db):
    """Тестируем ограничение уникальности для (book_id, section_epub_id) при создании секции."""
    book_id = 'unique-section-test-book'
    section_epub_id = 'unique-section-id'

    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path', [], 'en') # Создаем родительскую книгу

    # Создаем первую секцию
    success1 = workflow_db_manager.create_section_workflow(book_id, section_epub_id, 'Section One', '', 1)
    assert success1 is True

    # Попытка создать секцию с тем же book_id и section_epub_id должна вернуть False
    success2 = workflow_db_manager.create_section_workflow(book_id, section_epub_id, 'Section Duplicate', '', 2)
    assert success2 is False

    # Проверяем, что в базе осталась только одна секция с этой парой (book_id, section_epub_id)
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    filtered_sections = [s for s in sections if s['section_epub_id'] == section_epub_id]
    assert len(filtered_sections) == 1
    assert filtered_sections[0]['section_title'] == 'Section One' # Убеждаемся, что осталась первая секция


def test_stage_helper_functions(temp_workflow_db):
    """Тестируем вспомогательные функции для работы с этапами."""

    # get_stage_order_workflow
    assert workflow_db_manager.get_stage_order_workflow('summarize') == 1
    assert workflow_db_manager.get_stage_order_workflow('analyze') == 2
    assert workflow_db_manager.get_stage_order_workflow('translate') == 3
    assert workflow_db_manager.get_stage_order_workflow('epub_creation') == 4
    assert workflow_db_manager.get_stage_order_workflow('non_existent_stage') is None

    # get_stage_by_order_workflow
    assert workflow_db_manager.get_stage_by_order_workflow(1)['stage_name'] == 'summarize'
    assert workflow_db_manager.get_stage_by_order_workflow(4)['stage_name'] == 'epub_creation'
    assert workflow_db_manager.get_stage_by_order_workflow(99) is None

    # get_next_stage_name_workflow
    assert workflow_db_manager.get_next_stage_name_workflow('summarize') == 'analyze'
    assert workflow_db_manager.get_next_stage_name_workflow('analyze') == 'translate'
    assert workflow_db_manager.get_next_stage_name_workflow('translate') == 'epub_creation'
    assert workflow_db_manager.get_next_stage_name_workflow('epub_creation') is None # Последний этап
    assert workflow_db_manager.get_next_stage_name_workflow('non_existent_stage') is None

    # get_per_section_stage_names_workflow
    per_section_stages = workflow_db_manager.get_per_section_stage_names_workflow()
    assert 'summarize' in per_section_stages
    assert 'translate' in per_section_stages
    assert 'analyze' not in per_section_stages
    assert 'epub_creation' not in per_section_stages
    assert len(per_section_stages) == 2

def test_section_counting_functions(temp_workflow_db):
    """Тестируем функции подсчета секций по статусам и этапам."""
    book_id = 'counting-test-book'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path', [], 'en')

    # Создаем несколько секций
    workflow_db_manager.create_section_workflow(book_id, 'sec-a', 'A', '', 1)
    workflow_db_manager.create_section_workflow(book_id, 'sec-b', 'B', '', 2)
    workflow_db_manager.create_section_workflow(book_id, 'sec-c', 'C', '', 3)
    workflow_db_manager.create_section_workflow(book_id, 'sec-d', 'D', '', 4)

    # Проверяем общее количество секций
    assert workflow_db_manager.get_section_count_for_book_workflow(book_id) == 4
    assert workflow_db_manager.get_section_count_for_book_workflow('non-existent-book') == 0

    # Обновляем статусы для разных секций и этапов
    sec_a_id = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec-a')['section_id']
    sec_b_id = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec-b')['section_id']
    sec_c_id = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec-c')['section_id']
    sec_d_id = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec-d')['section_id']

    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_a_id, 'summarize', 'complete')
    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_b_id, 'summarize', 'completed_empty')
    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_c_id, 'summarize', 'error')
    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_d_id, 'summarize', 'processing')

    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_a_id, 'translate', 'complete')
    workflow_db_manager.update_section_stage_status_workflow(book_id, sec_c_id, 'translate', 'error')

    # Проверяем подсчет завершенных секций (complete или completed_empty)
    assert workflow_db_manager.get_completed_sections_count_for_stage_workflow(book_id, 'summarize') == 2
    assert workflow_db_manager.get_completed_sections_count_for_stage_workflow(book_id, 'translate') == 1 # Только sec-a complete
    assert workflow_db_manager.get_completed_sections_count_for_stage_workflow(book_id, 'analyze') == 0 # Не per-section этап
    assert workflow_db_manager.get_completed_sections_count_for_stage_workflow('non-existent-book', 'summarize') == 0

    # Проверяем подсчет секций с ошибками
    assert workflow_db_manager.get_error_sections_count_for_stage_workflow(book_id, 'summarize') == 1
    assert workflow_db_manager.get_error_sections_count_for_stage_workflow(book_id, 'translate') == 1
    assert workflow_db_manager.get_error_sections_count_for_stage_workflow(book_id, 'analyze') == 0 # Не per-section этап
    assert workflow_db_manager.get_error_sections_count_for_stage_workflow('non-existent-book', 'summarize') == 0

def test_get_sections(temp_workflow_db):
    """Тестируем получение секций для книги и по ID."""
    book_id = 'get-sections-test-book'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path', [], 'en')

    # Создаем несколько секций
    workflow_db_manager.create_section_workflow(book_id, 'sec1', 'Section 1', 'Перевод 1', 1)
    workflow_db_manager.create_section_workflow(book_id, 'sec2', 'Section 2', 'Перевод 2', 2)
    workflow_db_manager.create_section_workflow(book_id, 'sec3', 'Section 3', 'Перевод 3', 3)

    # Тестируем get_sections_for_book_workflow
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    assert len(sections) == 3
    assert sections[0]['section_epub_id'] == 'sec1'
    assert sections[1]['section_epub_id'] == 'sec2'
    assert sections[2]['section_epub_id'] == 'sec3'
    # Проверяем, что статусы этапов для per-section этапов включены
    assert 'stage_statuses' in sections[0]
    assert 'summarize' in sections[0]['stage_statuses']
    assert sections[0]['stage_statuses']['summarize']['status'] == 'pending'

    sections_nonexistent = workflow_db_manager.get_sections_for_book_workflow('non-existent-book')
    assert len(sections_nonexistent) == 0

    # Тестируем get_section_by_id_workflow
    sec1_info_by_epub_id = workflow_db_manager.get_section_by_epub_id_workflow(book_id, 'sec1')
    sec1_id = sec1_info_by_epub_id['section_id']
    sec1_info_by_id = workflow_db_manager.get_section_by_id_workflow(book_id, sec1_id)
    assert sec1_info_by_id is not None
    assert sec1_info_by_id['section_epub_id'] == 'sec1'
    assert sec1_info_by_id['section_title'] == 'Section 1'
    assert sec1_info_by_id['translated_title'] == 'Перевод 1'
    assert sec1_info_by_id['order_in_book'] == 1
    # Проверяем, что статусы этапов включены
    assert 'stage_statuses' in sec1_info_by_id
    assert 'summarize' in sec1_info_by_id['stage_statuses']

    # Тестируем получение несуществующей секции
    assert workflow_db_manager.get_section_by_id_workflow(book_id, 999) is None
    assert workflow_db_manager.get_section_by_id_workflow('non-existent-book', sec1_id) is None

def test_update_generated_prompt_ext(temp_workflow_db):
    """Тестируем обновление сгенерированного дополнения к промпту."""
    book_id = 'prompt-ext-test-book'
    workflow_db_manager.create_book_workflow(book_id, 'book.epub', '/path', [], 'en')

    # Проверяем начальное состояние
    book_info_initial = workflow_db_manager.get_book_workflow(book_id)
    assert book_info_initial['generated_prompt_ext'] is None
    assert book_info_initial['manual_prompt_ext'] is None

    # Обновляем сгенерированное дополнение
    new_prompt_text = 'This is the generated prompt extension.'
    success = workflow_db_manager.update_generated_prompt_ext(book_id, new_prompt_text)
    assert success is True

    # Проверяем, что поле обновилось
    book_info_updated = workflow_db_manager.get_book_workflow(book_id)
    assert book_info_updated['generated_prompt_ext'] == new_prompt_text
    assert book_info_updated['manual_prompt_ext'] is None # Manual prompt should remain None

    # Обновляем еще раз
    newer_prompt_text = 'This is the newer generated prompt extension.'
    success_again = workflow_db_manager.update_generated_prompt_ext(book_id, newer_prompt_text)
    assert success_again is True

    # Проверяем, что поле снова обновилось
    book_info_newer = workflow_db_manager.get_book_workflow(book_id)
    assert book_info_newer['generated_prompt_ext'] == newer_prompt_text

# --- Фикстура для Flask приложения (нужна для контекста g) ---
@pytest.fixture(scope='session')
def app():
    """Создает тестовое Flask приложение для контекста."""
    app = Flask(__name__)
    # Здесь можно добавить минимальную конфигурацию, если нужно
    # app.config['SECRET_KEY'] = 'testing'
    return app

# --- Hook для инициализации БД в контексте Flask ---
# Этот хук убеждается, что get_workflow_db работает в контексте Flask
@pytest.fixture(autouse=True)
def setup_app_context(app):
    with app.app_context():
        yield

# --- END OF FILE test_workflow_db_manager.py ---