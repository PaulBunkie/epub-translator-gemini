# В файле cache_manager.py

import os
import hashlib
import shutil # <--- Добавляем импорт shutil для удаления папок

CACHE_DIR = ".epub_cache" # Название папки для кэша

def _get_epub_id(epub_filepath):
    """Создает уникальный ID для файла EPUB на основе его пути."""
    return hashlib.md5(os.path.abspath(epub_filepath).encode()).hexdigest()

def _ensure_dir_exists(filepath):
    """Убеждается, что директория для файла существует."""
    directory = os.path.dirname(filepath)
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            print(f"Создана директория: {directory}")
        except OSError as e:
            print(f"ОШИБКА: Не удалось создать директорию {directory}: {e}")
            return False
    return True

def _get_cache_filepath(epub_id, section_id, target_language):
    """Конструирует путь к файлу кэша для конкретного раздела и языка."""
    safe_section_id = "".join(c for c in section_id if c.isalnum() or c in ('_', '-')).rstrip()
    filename = f"{safe_section_id}_{target_language}.txt"
    cache_path = os.path.join(CACHE_DIR, epub_id, filename)
    return cache_path

def get_translation_from_cache(epub_filepath, section_id, target_language):
    """
    Пытается загрузить перевод из кэша.
    Возвращает переведенный текст или None, если его нет в кэше.
    """
    epub_id = _get_epub_id(epub_filepath)
    cache_filepath = _get_cache_filepath(epub_id, section_id, target_language)

    if os.path.exists(cache_filepath):
        # print(f"Найден перевод в кэше: {cache_filepath}") # Можно раскомментировать для отладки
        try:
            with open(cache_filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"ОШИБКА: Не удалось прочитать файл кэша {cache_filepath}: {e}")
            return None
    else:
        return None

def save_translation_to_cache(epub_filepath, section_id, target_language, translated_text):
    """Сохраняет переведенный текст в кэш."""
    # Сохраняем даже пустой текст (результат completed_empty)
    if translated_text is None:
        print("Предупреждение: Попытка сохранить None в кэш.")
        return False

    epub_id = _get_epub_id(epub_filepath)
    cache_filepath = _get_cache_filepath(epub_id, section_id, target_language)
    if not _ensure_dir_exists(cache_filepath):
        return False

    try:
        with open(cache_filepath, "w", encoding="utf-8") as f:
            f.write(translated_text)
        print(f"Перевод сохранен в кэш: {cache_filepath}")
        return True
    except Exception as e:
        print(f"ОШИБКА: Не удалось сохранить перевод в кэш {cache_filepath}: {e}")
        return False

def save_translated_chapter(text, filename):
    """Сохраняет текст (например, полный перевод) в указанный файл."""
    # Сохраняем даже пустой текст
    if text is None:
         print("Предупреждение: Попытка сохранить None в файл.")
         text = "" # Сохраним пустой файл вместо ошибки

    if not _ensure_dir_exists(filename):
        return False

    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as e:
        print(f"ОШИБКА: Не удалось сохранить текст в файл {filename}: {e}")
        return False

# --- НОВАЯ ФУНКЦИЯ УДАЛЕНИЯ КЭША РАЗДЕЛА ---
def delete_section_cache(epub_filepath, section_id, target_language):
    """Удаляет файл кэша для конкретного раздела и языка, если он существует."""
    epub_id = _get_epub_id(epub_filepath)
    cache_filepath = _get_cache_filepath(epub_id, section_id, target_language)

    if os.path.exists(cache_filepath):
        try:
            os.remove(cache_filepath)
            print(f"Удален кэш раздела: {cache_filepath}")
            return True
        except OSError as e:
            print(f"ОШИБКА: Не удалось удалить файл кэша {cache_filepath}: {e}")
            return False
    else:
        # Файла и так нет, считаем операцию успешной (для идемпотентности)
        # print(f"Файл кэша для удаления не найден: {cache_filepath}")
        return True
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---

# --- НОВАЯ ФУНКЦИЯ УДАЛЕНИЯ КЭША КНИГИ (на будущее, если понадобится кнопка) ---
def delete_book_cache(epub_filepath):
    """Удаляет всю папку кэша для указанной книги."""
    epub_id = _get_epub_id(epub_filepath)
    book_cache_dir = os.path.join(CACHE_DIR, epub_id)

    if os.path.exists(book_cache_dir) and os.path.isdir(book_cache_dir):
        try:
            shutil.rmtree(book_cache_dir) # Удаляем папку и все ее содержимое
            print(f"Удалена папка кэша книги: {book_cache_dir}")
            return True
        except OSError as e:
            print(f"ОШИБКА: Не удалось удалить папку кэша книги {book_cache_dir}: {e}")
            return False
    else:
        # Папки нет, считаем успешным
        print(f"Папка кэша книги для удаления не найдена: {book_cache_dir}")
        return True
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---