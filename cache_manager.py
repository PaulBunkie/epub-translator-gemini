import os
import hashlib

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
            # Если директорию создать не удалось, возвращаем False
            return False
    return True # Директория существует или успешно создана


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
        print(f"Найден перевод в кэше: {cache_filepath}")
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
    if not translated_text:
        print("Предупреждение: Попытка сохранить пустой перевод в кэш.")
        return False

    epub_id = _get_epub_id(epub_filepath)
    cache_filepath = _get_cache_filepath(epub_id, section_id, target_language)
    if not _ensure_dir_exists(cache_filepath): # Убедимся, что папка существует
        return False # Не удалось создать директорию

    try:
        with open(cache_filepath, "w", encoding="utf-8") as f:
            f.write(translated_text)
        print(f"Перевод сохранен в кэш: {cache_filepath}")
        return True
    except Exception as e:
        print(f"ОШИБКА: Не удалось сохранить перевод в кэш {cache_filepath}: {e}")
        return False

# --- НОВАЯ ФУНКЦИЯ ---
def save_translated_chapter(text, filename):
    """Сохраняет текст (например, полный перевод) в указанный файл."""
    if not text:
        print("Предупреждение: Попытка сохранить пустой текст в файл.")
        return False

    if not _ensure_dir_exists(filename): # Убедимся, что папка существует
        return False # Не удалось создать директорию

    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        # Не будем выводить сообщение здесь, оно будет в main_tester
        return True
    except Exception as e:
        print(f"ОШИБКА: Не удалось сохранить текст в файл {filename}: {e}")
        return False
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---