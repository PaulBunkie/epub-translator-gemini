# --- START OF FILE config.py ---
import os
from pathlib import Path

# Логика определения базовой директории
if os.getenv('FLY_APP_NAME'):
    # На Fly.io используем /data для постоянного хранения
    BASE_DIR = Path("/data")
else:
    # Локально используем текущую директорию
    BASE_DIR = Path(".")

# --- Основные директории ---
CACHE_DIR = BASE_DIR / ".epub_cache"
UPLOADS_DIR = BASE_DIR / "uploads"
FULL_TRANSLATION_DIR = BASE_DIR / ".translated"

# --- Базы данных ---
MAIN_DB_FILE = BASE_DIR / "epub_translator.db"
WORKFLOW_DB_FILE = BASE_DIR / "workflow.db"
VIDEO_DB_FILE = BASE_DIR / "video_analyzer.db"

# --- Создание директорий при импорте ---
def ensure_directories():
    """Создает необходимые директории, если они не существуют."""
    directories = [
        CACHE_DIR,
        UPLOADS_DIR,
        FULL_TRANSLATION_DIR
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"[Config] Директория создана/проверена: {directory}")

# Автоматически создаем директории при импорте
ensure_directories()

# --- END OF FILE config.py --- 