#!/usr/bin/env python3
"""
Скрипт для запуска интерактивного Telegram бота
"""

import os
import sys
from telegram_bot_handler import TelegramBotHandler

def main():
    """Основная функция запуска бота"""
    print("🤖 Запуск Telegram бота...")
    print("=" * 50)
    
    # Проверяем переменные окружения
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не установлен")
        print("Установите переменную: TELEGRAM_BOT_TOKEN=ваш_токен")
        return
    
    if not chat_id:
        print("❌ TELEGRAM_CHAT_ID не установлен")
        print("Установите переменную: TELEGRAM_CHAT_ID=ваш_chat_id")
        print("Или запустите: python get_chat_id.py")
        return
    
    print("✅ Переменные окружения настроены")
    print(f"🤖 Бот: @aitube_notification_bot")
    print(f"👤 Chat ID: {chat_id}")
    
    try:
        # Создаем и запускаем бота
        bot = TelegramBotHandler()
        
        print("\n🚀 Бот запущен!")
        print("📱 Отправьте /start в Telegram для начала работы")
        print("📋 Доступные команды:")
        print("  /help - Список команд")
        print("  /status - Статус системы")
        print("  /cache_info - Информация о кэше")
        print("  /test_yandex - Тест Yandex API")
        print("  /system_info - Информация о системе")
        print("\n⏹️ Для остановки нажмите Ctrl+C")
        
        # Запускаем polling
        bot.run_polling()
        
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")

if __name__ == "__main__":
    main() 