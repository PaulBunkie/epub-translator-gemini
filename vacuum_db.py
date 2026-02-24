import sqlite3
import os
import sys
from pathlib import Path

# Попытка импортировать путь из конфига
try:
    from config import WORKFLOW_DB_FILE
    db_path = str(WORKFLOW_DB_FILE)
except ImportError:
    # Fallback если запуск идет в среде без настроенного pythonpath
    db_path = "/data/workflow.db" if os.path.exists("/data") else "workflow.db"

def run_vacuum():
    if not os.path.exists(db_path):
        print(f"Ошибка: Файл базы данных не найден по пути: {db_path}")
        return

    print(f"Начало процесса VACUUM для {db_path}...")
    print("ВНИМАНИЕ: Это может занять несколько минут и временно заблокировать базу данных.")
    
    try:
        # Устанавливаем большой таймаут, так как операция длительная
        conn = sqlite3.connect(db_path, timeout=300)
        conn.execute("VACUUM")
        conn.close()
        print("✅ VACUUM успешно завершен. База данных оптимизирована и место освобождено.")
    except sqlite3.Error as e:
        print(f"❌ Ошибка SQLite при выполнении VACUUM: {e}")
    except Exception as e:
        print(f"❌ Непредвиденная ошибка: {e}")

if __name__ == "__main__":
    run_vacuum()
