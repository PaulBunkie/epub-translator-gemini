import os
import requests
import json
from typing import Optional
from datetime import datetime

BASE_URL = "https://aitube.fly.dev"

def make_download_link(token: str) -> str:
    url = f"{BASE_URL}/translate/{token}"
    return f'<a href="{url}">Скачать перевод</a>'

class TelegramNotifier:
    """
    Модуль для отправки уведомлений в Telegram о критических ошибках системы
    """
    
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.api_url = "https://api.telegram.org/bot"
        
        if not self.bot_token:
            print("[TelegramNotifier] ВНИМАНИЕ: TELEGRAM_BOT_TOKEN не установлен")
        if not self.chat_id:
            print("[TelegramNotifier] ВНИМАНИЕ: TELEGRAM_CHAT_ID не установлен")
    
    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Отправляет сообщение в Telegram
        
        Args:
            message: Текст сообщения
            parse_mode: Режим парсинга (HTML, Markdown)
            
        Returns:
            bool: True если сообщение отправлено успешно
        """
        if not self.bot_token or not self.chat_id:
            print("[TelegramNotifier] Не удалось отправить сообщение: отсутствуют токен или chat_id")
            return False
        
        try:
            url = f"{self.api_url}{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print(f"[TelegramNotifier] Сообщение отправлено успешно")
                    return True
                else:
                    print(f"[TelegramNotifier] Ошибка API Telegram: {result.get('description', 'Unknown error')}")
                    return False
            else:
                print(f"[TelegramNotifier] HTTP ошибка {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            print(f"[TelegramNotifier] Ошибка отправки сообщения: {e}")
            return False
    
    def notify_yandex_token_error(self, error_type: str, details: str = "") -> bool:
        """
        Отправляет уведомление об ошибке токена Yandex API
        
        Args:
            error_type: Тип ошибки (expired, invalid, etc.)
            details: Дополнительные детали ошибки
            
        Returns:
            bool: True если уведомление отправлено успешно
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🚨 <b>Ошибка Yandex API Token</b>

⏰ <b>Время:</b> {timestamp}
❌ <b>Тип ошибки:</b> {error_type}

{details if details else "Токен требует обновления"}

🔧 <b>Действие:</b> Обновите токен в переменных окружения
        """.strip()
        
        return self.send_message(message)
    
    def notify_session_expired(self, session_type: str = "Yandex Session") -> bool:
        """
        Отправляет уведомление об истечении сессии
        
        Args:
            session_type: Тип сессии
            
        Returns:
            bool: True если уведомление отправлено успешно
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
⚠️ <b>Сессия истекла</b>

⏰ <b>Время:</b> {timestamp}
🔑 <b>Тип:</b> {session_type}

🔄 <b>Действие:</b> Обновите Session_id в переменных окружения
        """.strip()
        
        return self.send_message(message)
    
    def notify_api_error(self, api_name: str, status_code: int, error_message: str) -> bool:
        """
        Отправляет уведомление об ошибке API
        
        Args:
            api_name: Название API
            status_code: HTTP статус код
            error_message: Сообщение об ошибке
            
        Returns:
            bool: True если уведомление отправлено успешно
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔴 <b>Ошибка API</b>

⏰ <b>Время:</b> {timestamp}
🌐 <b>API:</b> {api_name}
📊 <b>Статус:</b> {status_code}

💬 <b>Ошибка:</b> {error_message}
        """.strip()
        
        return self.send_message(message)
    
    def test_connection(self) -> bool:
        """
        Тестирует подключение к Telegram Bot API
        
        Returns:
            bool: True если подключение работает
        """
        if not self.bot_token:
            print("[TelegramNotifier] Не удалось протестировать: отсутствует TELEGRAM_BOT_TOKEN")
            return False
        
        try:
            url = f"{self.api_url}{self.bot_token}/getMe"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    bot_info = result.get("result", {})
                    print(f"[TelegramNotifier] ✅ Подключение успешно. Бот: @{bot_info.get('username', 'Unknown')}")
                    return True
                else:
                    print(f"[TelegramNotifier] ❌ Ошибка API: {result.get('description', 'Unknown error')}")
                    return False
            else:
                print(f"[TelegramNotifier] ❌ HTTP ошибка {response.status_code}")
                return False
                
        except Exception as e:
            print(f"[TelegramNotifier] ❌ Ошибка подключения: {e}")
            return False
    
    def send_message_to_user(self, user_id: str, message: str, parse_mode: str = "HTML") -> bool:
        """
        Отправляет сообщение конкретному пользователю
        
        Args:
            user_id: ID пользователя в Telegram
            message: Текст сообщения
            parse_mode: Режим парсинга (HTML, Markdown)
            
        Returns:
            bool: True если сообщение отправлено успешно
        """
        if not self.bot_token:
            print("[TelegramNotifier] Не удалось отправить сообщение: отсутствует TELEGRAM_BOT_TOKEN")
            return False
        
        try:
            url = f"{self.api_url}{self.bot_token}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print(f"[TelegramNotifier] Сообщение отправлено пользователю {user_id}")
                    return True
                else:
                    print(f"[TelegramNotifier] Ошибка API Telegram для пользователя {user_id}: {result.get('description', 'Unknown error')}")
                    return False
            else:
                print(f"[TelegramNotifier] HTTP ошибка {response.status_code} для пользователя {user_id}: {response.text}")
                return False
                
        except Exception as e:
            print(f"[TelegramNotifier] Ошибка отправки сообщения пользователю {user_id}: {e}")
            return False

# Глобальный экземпляр для использования в других модулях
telegram_notifier = TelegramNotifier() 