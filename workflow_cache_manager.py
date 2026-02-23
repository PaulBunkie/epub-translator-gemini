import os
import json
import traceback
import shutil
from pathlib import Path

from config import CACHE_DIR

# Базовая директория для кэша рабочего процесса
WORKFLOW_CACHE_BASE_DIR = str(CACHE_DIR / "workflow")

# Убедимся, что базовая директория кэша существует при импорте модуля
os.makedirs(WORKFLOW_CACHE_BASE_DIR, exist_ok=True)

def _get_cache_dir_for_stage(book_id, stage_name):
    """Возвращает путь к директории для кэша определенного этапа для данной книги."""
    # Например: .epub_cache/workflow/book-id-123/summaries/
    return str(Path(WORKFLOW_CACHE_BASE_DIR) / book_id / stage_name)

def _get_cache_file_path(book_id, section_id, stage_name, file_extension='.txt'):
    """Возвращает полный путь к файлу кэша для секции на определенном этапе."""
    stage_dir = _get_cache_dir_for_stage(book_id, stage_name)
    # Имя файла будет просто ID секции с расширением
    filename = f'{section_id}{file_extension}'
    return os.path.join(stage_dir, filename)

def save_section_stage_result(book_id, section_id, stage_name, content):
    """Сохраняет результат обработки секции на определенном этапе в файловый кэш."""
    stage_dir = _get_cache_dir_for_stage(book_id, stage_name)
    file_path = _get_cache_file_path(book_id, section_id, stage_name)

    try:
        # Создаем директории, если их нет
        os.makedirs(stage_dir, exist_ok=True)

        # Сохраняем контент в файл
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"[WorkflowCache] ОШИБКА при сохранении кэша для {file_path}: {e}")
        traceback.print_exc()
        return False

def load_section_stage_result(book_id, section_id, stage_name):
    """Загружает результат обработки секции на определенном этапе из файлового кэша."""
    file_path = _get_cache_file_path(book_id, section_id, stage_name)

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return content
    except Exception as e:
        print(f"[WorkflowCache] ОШИБКА при загрузке кэша из {file_path}: {e}")
        traceback.print_exc()
        return None

def delete_section_stage_result(book_id, section_id, stage_name):
    """Удаляет файл кэша для секции на определенном этапе."""
    file_path = _get_cache_file_path(book_id, section_id, stage_name)

    if not os.path.exists(file_path):
        return False # Файл не найден, считаем успешным удалением (в смысле, его нет)

    try:
        os.remove(file_path)
        return True
    except Exception as e:
        print(f"[WorkflowCache] ОШИБКА при удалении кэша {file_path}: {e}")
        traceback.print_exc()
        return False

def delete_book_workflow_cache(book_id):
    """Удаляет всю директорию кэша для данной книги."""
    book_cache_dir = os.path.join(WORKFLOW_CACHE_BASE_DIR, book_id)

    if not os.path.exists(book_cache_dir):
        return False # Директория не найденa, считаем успешным удалением

    try:
        shutil.rmtree(book_cache_dir)
        return True
    except Exception as e:
        print(f"[WorkflowCache] ОШИБКА при удалении директории кэша книги {book_cache_dir}: {e}")
        traceback.print_exc()
        return False

# --- New function to save book-level stage result ---
def save_book_stage_result(book_id, stage_name, content, file_extension='.txt'):
    """
    Saves the result of a book-level stage to a file cache.
    """
    stage_dir = _get_cache_dir_for_stage(book_id, stage_name)
    # For book-level stages, the filename can be a fixed name, e.g., 'result.txt'
    filename = f'result{file_extension}'
    file_path = os.path.join(stage_dir, filename)

    try:
        # Create directories if they don't exist
        os.makedirs(stage_dir, exist_ok=True)

        # Save content to file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"[WorkflowCache] ERROR saving book-level cache for {file_path}: {e}")
        traceback.print_exc()
        return False

# --- New function to load book-level stage result ---
def load_book_stage_result(book_id, stage_name, file_extension='.txt'):
    """
    Loads the result of a book-level stage from a file cache.
    """
    stage_dir = _get_cache_dir_for_stage(book_id, stage_name)
    filename = f'result{file_extension}'
    file_path = os.path.join(stage_dir, filename)

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return content
    except Exception as e:
        print(f"[WorkflowCache] ERROR loading book-level cache from {file_path}: {e}")
        traceback.print_exc()
        return None

# TODO: Implement load_book_stage_result

# TODO: Возможно, добавить функцию для удаления кэша всей книги

def delete_book_stage_result(book_id, stage_name):
    """
    Удаляет кэш только для одного этапа книги (например, analyze, translate, epub_creation).
    """
    stage_dir = _get_cache_dir_for_stage(book_id, stage_name)

    if not os.path.exists(stage_dir):
        return False # Директория не найдена, считаем успешным удалением

    try:
        shutil.rmtree(stage_dir)
        return True
    except Exception as e:
        print(f"[WorkflowCache] ОШИБКА при удалении директории кэша этапа {stage_dir}: {e}")
        traceback.print_exc()
        return False
