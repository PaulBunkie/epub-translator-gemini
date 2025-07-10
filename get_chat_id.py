#!/usr/bin/env python3
"""
Скрипт для получения chat_id пользователя Telegram
"""

import os
import requests
import json
from datetime import datetime

def get_updates(bot_token):
    """Получает последние обновления от бота"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                return data.get("result", [])
            else:
                print(f"❌ Ошибка API: {data.get('description', 'Unknown error')}")
                return []
        else:
            print(f"❌ HTTP ошибка {response.status_code}")
            return []
            
    except Exception as e:
        print(f"❌ Ошибка получения обновлений: {e}")
        return []

def find_chat_ids(updates):
    """Находит все уникальные chat_id в обновлениях"""
    chat_ids = set()
    
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        
        if chat_id:
            chat_type = chat.get("type", "unknown")
            first_name = chat.get("first_name", "")
            last_name = chat.get("last_name", "")
            username = chat.get("username", "")
            
            chat_info = {
                "chat_id": chat_id,
                "type": chat_type,
                "name": f"{first_name} {last_name}".strip(),
                "username": username
            }
            chat_ids.add(json.dumps(chat_info, sort_keys=True))
    
    return [json.loads(chat_info) for chat_info in chat_ids]

def main():
    """Основная функция"""
    print("🔍 Получение chat_id для Telegram бота")
    print("=" * 50)
    
    # Проверяем токен бота
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не установлен")
        print("\n📝 Для настройки:")
        print("1. Создайте бота через @BotFather")
        print("2. Установите TELEGRAM_BOT_TOKEN=ваш_токен")
        return
    
    print(f"✅ Токен бота найден")
    
    # Получаем обновления
    print("\n📨 Получение обновлений от бота...")
    updates = get_updates(bot_token)
    
    if not updates:
        print("❌ Обновлений не найдено")
        print("\n💡 Для получения chat_id:")
        print("1. Найдите вашего бота в Telegram")
        print("2. Отправьте ему любое сообщение (например, /start)")
        print("3. Запустите этот скрипт снова")
        return
    
    print(f"✅ Найдено {len(updates)} обновлений")
    
    # Находим chat_id
    chat_ids = find_chat_ids(updates)
    
    if not chat_ids:
        print("❌ Chat ID не найден в обновлениях")
        return
    
    print(f"\n📋 Найденные чаты:")
    print("-" * 50)
    
    for i, chat_info in enumerate(chat_ids, 1):
        print(f"{i}. Chat ID: {chat_info['chat_id']}")
        print(f"   Тип: {chat_info['type']}")
        if chat_info['name']:
            print(f"   Имя: {chat_info['name']}")
        if chat_info['username']:
            print(f"   Username: @{chat_info['username']}")
        print()
    
    # Рекомендации
    print("💡 Рекомендации:")
    print("- Для личных сообщений используйте chat_id пользователя")
    print("- Для групповых чатов используйте chat_id группы")
    print("- Установите переменную: TELEGRAM_CHAT_ID=выбранный_chat_id")
    
    # Пример настройки
    if chat_ids:
        first_chat = chat_ids[0]
        print(f"\n📝 Пример настройки:")
        print(f"export TELEGRAM_CHAT_ID={first_chat['chat_id']}")

if __name__ == "__main__":
    main() 