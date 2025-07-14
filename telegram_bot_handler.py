#!/usr/bin/env python3
"""
Расширенный обработчик Telegram бота с дополнительными командами
"""

import os
import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from telegram_notifier import telegram_notifier

class TelegramBotHandler:
    """
    Расширенный обработчик Telegram бота с командами управления системой
    """
    
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.api_url = "https://api.telegram.org/bot"
        self.last_update_id = 0
        
        # Поддержка множественных пользователей
        self.allowed_chat_ids = set()
        if self.chat_id:
            self.allowed_chat_ids.add(str(self.chat_id))
        
        # Дополнительные пользователи через переменную окружения
        additional_users = os.getenv("TELEGRAM_ADDITIONAL_USERS", "")
        if additional_users:
            for user_id in additional_users.split(","):
                user_id = user_id.strip()
                if user_id:
                    self.allowed_chat_ids.add(user_id)
        
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")
    
    def get_updates(self, timeout: int = 30) -> list:
        """Получает обновления от Telegram API"""
        try:
            url = f"{self.api_url}{self.bot_token}/getUpdates"
            params = {
                "offset": self.last_update_id + 1,
                "timeout": timeout
            }
            
            response = requests.get(url, params=params, timeout=timeout + 5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    if updates:
                        self.last_update_id = updates[-1]["update_id"]
                    return updates
            return []
            
        except Exception as e:
            print(f"[TelegramBot] Ошибка получения обновлений: {e}")
            return []
    
    def send_message(self, chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
        """Отправляет сообщение"""
        try:
            url = f"{self.api_url}{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"[TelegramBot] Ошибка отправки сообщения: {e}")
            return False
    
    def handle_command(self, chat_id: str, command: str, args: str = "") -> str:
        """Обрабатывает команды бота"""
        
        if command == "/start":
            return self.cmd_start_with_token(chat_id, args)
        
        elif command == "/help":
            return self.cmd_help()
        
        elif command == "/status":
            return self.cmd_status()
        
        elif command == "/test_yandex":
            return self.cmd_test_yandex()
        
        elif command == "/system_info":
            return self.cmd_system_info()
        
        elif command == "/logs":
            return self.cmd_logs(args)
        
        elif command == "/restart":
            return self.cmd_restart()
        
        elif command == "/unsubscribe":
            return self.cmd_unsubscribe(chat_id)
        
        else:
            return "❌ Неизвестная команда. Используйте /help для списка команд."
    
    def cmd_start(self) -> str:
        """Команда /start без токена"""
        return """
🤖 <b>AI Tube Notification Bot</b>

Добро пожаловать! Я помогу вам управлять системой анализа видео.

📋 <b>Доступные команды:</b>
/help - Список всех команд
/status - Статус системы
/test_yandex - Тест Yandex API
/system_info - Информация о системе
/logs - Последние логи
/restart - Перезапуск системы

📚 <b>Для пользователей EPUB переводчика:</b>
/start [токен] - Подписаться на уведомления о переводе
/unsubscribe - Отписаться от уведомлений

💡 Используйте /help для подробной информации о командах.
        """.strip()
    
    def cmd_start_with_token(self, chat_id: str, token: str) -> str:
        """Команда /start с токеном для подписки на уведомления"""
        if not token:
            return self.cmd_start()
        
        try:
            # Импортируем здесь, чтобы избежать циклических импортов
            import sqlite3
            from datetime import datetime
            
            # Подключаемся к БД
            conn = sqlite3.connect('workflow.db')
            cursor = conn.cursor()
            
            # Проверяем существование токена в workflow
            cursor.execute("""
                SELECT book_id, filename, target_language 
                FROM books 
                WHERE access_token = ?
            """, (token,))
            
            book_info = cursor.fetchone()
            
            if not book_info:
                return "❌ Токен не найден или недействителен. Проверьте ссылку."
            
            book_id, filename, target_language = book_info
            
            # Добавляем или обновляем пользователя
            cursor.execute("""
                INSERT OR REPLACE INTO telegram_users (user_id, access_token, created_at, is_active)
                VALUES (?, ?, ?, ?)
            """, (chat_id, token, datetime.now(), True))
            
            conn.commit()
            conn.close()
            
            return f"""
✅ <b>Подписка активирована!</b>

📚 <b>Книга:</b> {filename}
🌍 <b>Язык:</b> {target_language}

🔔 Вы получите уведомление когда перевод будет готов.

📱 <b>Команды:</b>
/unsubscribe - Отписаться от уведомлений
            """.strip()
            
        except Exception as e:
            print(f"[TelegramBot] Ошибка при подписке пользователя {chat_id}: {e}")
            return "❌ Ошибка при активации подписки. Попробуйте позже."
    
    def cmd_unsubscribe(self, chat_id: str) -> str:
        """Команда /unsubscribe для отписки от уведомлений"""
        try:
            import sqlite3
            
            conn = sqlite3.connect('workflow.db')
            cursor = conn.cursor()
            
            # Удаляем пользователя
            cursor.execute("DELETE FROM telegram_users WHERE user_id = ?", (chat_id,))
            
            if cursor.rowcount > 0:
                conn.commit()
                conn.close()
                return "✅ Вы отписались от уведомлений о переводах."
            else:
                conn.close()
                return "ℹ️ Вы не были подписаны на уведомления."
                
        except Exception as e:
            print(f"[TelegramBot] Ошибка при отписке пользователя {chat_id}: {e}")
            return "❌ Ошибка при отписке. Попробуйте позже."
    
    def cmd_help(self) -> str:
        """Команда /help"""
        return """
📚 <b>Справка по командам</b>

🔍 <b>Мониторинг:</b>
/status - Показывает статус всех компонентов системы
/system_info - Информация о сервере, памяти, диске

🔧 <b>Тестирование:</b>
/test_yandex - Проверяет работоспособность Yandex API
/logs [количество] - Показывает последние логи (по умолчанию 10)

⚡ <b>Управление:</b>
/restart - Перезапускает систему (требует подтверждения)

📊 <b>Автоматические уведомления:</b>
• Ошибки токенов Yandex API
• Истечение сессий
• Критические ошибки системы
        """.strip()
    
    def cmd_status(self) -> str:
        """Команда /status - показывает статус системы"""
        try:
            # Проверяем основные компоненты
            status_info = []
            
            # Проверяем переменные окружения
            yandex_token = "✅" if os.getenv("YANDEX_API_TOKEN") else "❌"
            yandex_session = "✅" if os.getenv("YANDEX_SESSION_ID") else "❌"
            openrouter_key = "✅" if os.getenv("OPENROUTER_API_KEY") else "❌"
            
            status_info.append(f"Yandex API Token: {yandex_token}")
            status_info.append(f"Yandex Session ID: {yandex_session}")
            status_info.append(f"OpenRouter API Key: {openrouter_key}")
            
            # Проверяем базы данных
            db_files = [
                ("epub_translator.db", "Основная БД"),
                ("video_analyzer.db", "Видео БД"),
                ("workflow.db", "Workflow БД")
            ]
            
            for db_file, description in db_files:
                if os.path.exists(db_file):
                    size = os.path.getsize(db_file)
                    size_mb = size / (1024 * 1024)
                    status_info.append(f"{description}: ✅ ({size_mb:.1f} MB)")
                else:
                    status_info.append(f"{description}: ❌")
            
            # Проверяем директории
            dirs = [".epub_cache", "uploads", ".translated"]
            for dir_name in dirs:
                if os.path.exists(dir_name):
                    status_info.append(f"Директория {dir_name}: ✅")
                else:
                    status_info.append(f"Директория {dir_name}: ❌")
            
            return f"""
📊 <b>Статус системы</b>

⏰ <b>Время:</b> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

🔑 <b>Переменные окружения:</b>
{chr(10).join(status_info[:3])}

🗄️ <b>Базы данных:</b>
{chr(10).join(status_info[3:6])}

📁 <b>Директории:</b>
{chr(10).join(status_info[6:])}
            """.strip()
            
        except Exception as e:
            return f"❌ Ошибка получения статуса: {e}"
    
    def cmd_test_yandex(self) -> str:
        """Команда /test_yandex - тестирует Yandex API"""
        try:
            from video_analyzer import VideoAnalyzer
            
            # Создаем анализатор
            analyzer = VideoAnalyzer()
            
            # Тестируем с простым URL
            test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            
            result = analyzer.analyze_video(test_url)
            
            if result.get('error'):
                return f"""
🔴 <b>Тест Yandex API - ОШИБКА</b>

❌ Ошибка: {result['error']}

🔧 Проверьте настройки токенов
                """.strip()
            else:
                return f"""
✅ <b>Тест Yandex API - УСПЕХ</b>

📹 URL: {test_url}
🔗 Sharing URL: {result.get('sharing_url', 'N/A')}
📝 Текст: {len(result.get('extracted_text', ''))} символов
🤖 Анализ: {len(result.get('analysis', ''))} символов

🎉 API работает корректно
                """.strip()
                
        except Exception as e:
            return f"❌ Ошибка тестирования Yandex API: {e}"
    
    def cmd_system_info(self) -> str:
        """Команда /system_info - информация о системе"""
        try:
            import psutil
            
            # Информация о CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            
            # Информация о памяти
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used = memory.used / (1024**3)  # GB
            memory_total = memory.total / (1024**3)  # GB
            
            # Информация о диске
            disk = psutil.disk_usage('.')
            disk_percent = disk.percent
            disk_used = disk.used / (1024**3)  # GB
            disk_total = disk.total / (1024**3)  # GB
            
            # Время работы системы
            uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
            uptime_str = str(uptime).split('.')[0]  # Убираем микросекунды
            
            return f"""
💻 <b>Информация о системе</b>

🖥️ <b>CPU:</b> {cpu_percent}% ({cpu_count} ядер)
🧠 <b>Память:</b> {memory_percent}% ({memory_used:.1f} GB / {memory_total:.1f} GB)
💾 <b>Диск:</b> {disk_percent}% ({disk_used:.1f} GB / {disk_total:.1f} GB)
⏰ <b>Время работы:</b> {uptime_str}

📊 <b>Статус:</b> {'🟢 Нормальный' if cpu_percent < 80 and memory_percent < 80 else '🟡 Нагрузка' if cpu_percent < 95 and memory_percent < 95 else '🔴 Высокая нагрузка'}
            """.strip()
            
        except ImportError:
            return "❌ Модуль psutil не установлен. Установите: pip install psutil"
        except Exception as e:
            return f"❌ Ошибка получения информации о системе: {e}"
    
    def cmd_logs(self, args: str = "") -> str:
        """Команда /logs - показывает последние логи"""
        try:
            # Определяем количество строк
            try:
                lines = int(args) if args else 10
                lines = min(lines, 50)  # Максимум 50 строк
            except ValueError:
                lines = 10
            
            # Пытаемся найти логи
            log_files = [
                "app.log",
                "video_analyzer.log",
                "location_finder.log"
            ]
            
            log_content = []
            for log_file in log_files:
                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        file_lines = f.readlines()
                        if file_lines:
                            recent_lines = file_lines[-lines:]
                            log_content.append(f"📄 {log_file}:")
                            log_content.extend(recent_lines)
                            log_content.append("")
            
            if log_content:
                # Объединяем логи и ограничиваем длину
                full_log = "".join(log_content)
                if len(full_log) > 4000:
                    full_log = full_log[:4000] + "\n... (обрезано)"
                
                return f"""
📋 <b>Последние логи ({lines} строк)</b>

{full_log}
                """.strip()
            else:
                return "ℹ️ Логи не найдены"
                
        except Exception as e:
            return f"❌ Ошибка чтения логов: {e}"
    
    def cmd_restart(self) -> str:
        """Команда /restart - перезапуск системы"""
        return """
⚠️ <b>Перезапуск системы</b>

🔄 Для перезапуска системы выполните:
1. Остановите текущий процесс (Ctrl+C)
2. Запустите заново: python app.py

💡 Или используйте systemd/PM2 для автоматического перезапуска
        """.strip()
    
    def process_updates(self):
        """Обрабатывает входящие обновления"""
        updates = self.get_updates()
        
        for update in updates:
            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "")
            
            if not chat_id or not text:
                continue
            
            # Обрабатываем команды
            if text.startswith("/"):
                parts = text.split(" ", 1)
                command = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                
                # Команды, доступные всем пользователям
                public_commands = ["/start", "/unsubscribe"]
                
                # Проверяем доступ только для административных команд
                if command not in public_commands and str(chat_id) not in self.allowed_chat_ids:
                    self.send_message(chat_id, "❌ Доступ запрещен")
                    continue
                
                response = self.handle_command(chat_id, command, args)
                self.send_message(chat_id, response)
    
    def run_polling(self):
        """Запускает polling для получения обновлений"""
        print("[TelegramBot] Запуск polling...")
        
        while True:
            try:
                self.process_updates()
                time.sleep(1)  # Пауза между запросами
            except KeyboardInterrupt:
                print("[TelegramBot] Остановка polling...")
                break
            except Exception as e:
                print(f"[TelegramBot] Ошибка в polling: {e}")
                time.sleep(5)  # Пауза при ошибке

# Глобальный экземпляр
telegram_bot = TelegramBotHandler() 