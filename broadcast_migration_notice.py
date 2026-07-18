"""
Одноразовый скрипт для рассылки всем подписчикам Telegram уведомления
о смене домена itube.lol -> aitube.fly.dev.

Запуск: python broadcast_migration_notice.py
"""

import os
import sqlite3
import sys
import time
from dotenv import load_dotenv
from config import WORKFLOW_DB_FILE
from telegram_notifier import telegram_notifier

load_dotenv()

DATABASE_FILE = str(WORKFLOW_DB_FILE)

MESSAGE = """\
🔔 <b>Техническое уведомление</b>

Наш сервис переехал на новый адрес. Если при попытке скачать перевод вы видите ошибку — просто замените в адресной строке <code>itube.lol</code> на <code>aitube.fly.dev</code>.

🌐 Новый адрес: https://aitube.fly.dev

Приносим извинения за неудобства!\
"""


def get_all_telegram_users(db_path: str) -> list[str]:
    """Возвращает список уникальных активных user_id из telegram_users."""
    if not os.path.exists(db_path):
        print(f"[Broadcast] БД не найдена: {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("""
            SELECT DISTINCT user_id
            FROM telegram_users
            WHERE is_active = TRUE
        """)
        return [row["user_id"] for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"[Broadcast] Ошибка чтения БД: {e}")
        return []
    finally:
        conn.close()


def main():
    print("[Broadcast] Загрузка списка подписчиков...")
    users = get_all_telegram_users(DATABASE_FILE)

    if not users:
        print("[Broadcast] Нет активных подписчиков. Выход.")
        return

    print(f"[Broadcast] Найдено {len(users)} подписчиков.")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("[Broadcast] TELEGRAM_BOT_TOKEN не установлен. Выход.")
        sys.exit(1)

    print("[Broadcast] Начинаем рассылку...")
    success = 0
    failed = 0

    for i, user_id in enumerate(users, 1):
        print(f"[Broadcast] [{i}/{len(users)}] Отправка пользователю {user_id}...")
        if telegram_notifier.send_message_to_user(user_id, MESSAGE, parse_mode="HTML"):
            success += 1
            print(f"  ✅ Отправлено")
        else:
            failed += 1
            print(f"  ❌ Ошибка отправки")

        # Небольшая пауза чтобы не упереться в лимиты Telegram API (~30 сообщений/сек)
        time.sleep(0.05)

    print(f"\n[Broadcast] Готово! Успешно: {success}, ошибок: {failed}")


if __name__ == "__main__":
    main()