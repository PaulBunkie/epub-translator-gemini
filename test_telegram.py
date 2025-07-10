#!/usr/bin/env python3
"""
Тестовый скрипт для проверки Telegram уведомлений
"""

import os
from telegram_notifier import telegram_notifier

def test_telegram_connection():
    """Тестирует подключение к Telegram Bot API"""
    print("🔍 Тестирование подключения к Telegram...")
    
    if telegram_notifier.test_connection():
        print("✅ Подключение к Telegram успешно!")
        return True
    else:
        print("❌ Ошибка подключения к Telegram")
        return False

def test_notifications():
    """Тестирует отправку различных типов уведомлений"""
    print("\n📨 Тестирование уведомлений...")
    
    # Тест 1: Ошибка токена Yandex
    print("\n1️⃣ Тест уведомления об ошибке токена Yandex...")
    success1 = telegram_notifier.notify_yandex_token_error(
        "expired",
        "HTTP 401: Token expired or invalid"
    )
    print(f"Результат: {'✅' if success1 else '❌'}")
    
    # Тест 2: Истечение сессии
    print("\n2️⃣ Тест уведомления об истечении сессии...")
    success2 = telegram_notifier.notify_session_expired("Yandex Session")
    print(f"Результат: {'✅' if success2 else '❌'}")
    
    # Тест 3: Общая ошибка API
    print("\n3️⃣ Тест уведомления об ошибке API...")
    success3 = telegram_notifier.notify_api_error(
        "Yandex API",
        500,
        "Internal server error"
    )
    print(f"Результат: {'✅' if success3 else '❌'}")
    
    return success1 and success2 and success3

def check_environment():
    """Проверяет наличие необходимых переменных окружения"""
    print("🔧 Проверка переменных окружения...")
    
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    print(f"TELEGRAM_BOT_TOKEN: {'✅' if bot_token else '❌'}")
    print(f"TELEGRAM_CHAT_ID: {'✅' if chat_id else '❌'}")
    
    if bot_token and chat_id:
        print("✅ Все необходимые переменные установлены")
        return True
    else:
        print("❌ Отсутствуют необходимые переменные окружения")
        print("\n📝 Для настройки:")
        print("1. Установите TELEGRAM_BOT_TOKEN=ваш_токен_бота")
        print("2. Установите TELEGRAM_CHAT_ID=ваш_chat_id")
        return False

def main():
    """Основная функция тестирования"""
    print("🤖 Тестирование Telegram уведомлений")
    print("=" * 50)
    
    # Проверяем переменные окружения
    if not check_environment():
        return
    
    # Тестируем подключение
    if not test_telegram_connection():
        return
    
    # Тестируем уведомления
    if test_notifications():
        print("\n🎉 Все тесты прошли успешно!")
    else:
        print("\n⚠️ Некоторые тесты не прошли")

if __name__ == "__main__":
    main() 