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
from telegram_notifier import telegram_notifier, make_download_link
import workflow_db_manager

# Базовый URL для API запросов
BASE_URL = os.getenv("SITE_URL", "https://aitube.fly.dev")

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
            print("[TelegramBot] TELEGRAM_BOT_TOKEN не установлен - работаем в режиме заглушки")
            self.is_stub = True
        else:
            self.is_stub = False
    
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
    
    def send_message(self, chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: dict = None) -> bool:
        """Отправляет сообщение с опциональными кнопками"""
        try:
            url = f"{self.api_url}{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode
            }
            
            if reply_markup:
                payload["reply_markup"] = reply_markup
            
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
        
        elif command == "/progress":
            return self.cmd_progress(chat_id, args)
        
        else:
            return "❌ Неизвестная команда. Используйте /help для списка команд."
    
    def cmd_start(self) -> str:
        """Команда /start без токена"""
        return """
🤖 <b>AI Tube Notification Bot</b>

Добро пожаловать! Я помогу вам управлять системой анализа видео.

📋 <b>Основные команды:</b>
/help - Список всех команд
/status - Статус системы
/test_yandex - Тест Yandex API
/system_info - Информация о системе
/logs - Последние логи
/restart - Перезапуск системы

📚 <b>Для пользователей EPUB переводчика:</b>
/start [токен] - Подписаться на уведомления о переводе
/unsubscribe - Отписаться от всех уведомлений

⚽ <b>Для пользователей футбольной аналитики:</b>
/start football - Подписаться на уведомления о футболе
/unsubscribe - Отписаться от всех уведомлений

💡 <b>Примечание:</b>
Команда /unsubscribe отписывает от всех типов уведомлений (переводы и футбол).
        """.strip()
    
    def cmd_start_with_token(self, chat_id: str, token: str) -> str:
        """Команда /start с токеном для подписки на уведомления"""
        print(f"[TelegramBot] cmd_start_with_token вызвана: chat_id={chat_id}, token='{token}'")
        
        if not token:
            return self.cmd_start()
        
        try:
            # Проверяем, это подписка на футбол?
            # Убираем лишние пробелы и приводим к нижнему регистру для надежности
            token_clean = token.strip().lower()
            print(f"[TelegramBot] Очищенный токен: '{token_clean}'")
            
            if token_clean == "football":
                # Простая подписка через команду /start football (без токена)
                # Используем chat_id как user_id напрямую
                try:
                    import football
                    success = football.add_football_subscription(chat_id)
                    
                    if success:
                        message_text = """
✅ <b>Подписка на футбольные уведомления активирована!</b>

⚽ Вы будете получать уведомления о важных матчах.

📱 <b>Команды:</b>
/unsubscribe - Отписаться от уведомлений
                        """.strip()
                        
                        self.send_message(chat_id, message_text)
                        return None  # Возвращаем None, так как сообщение уже отправлено
                    else:
                        return "❌ Ошибка при добавлении подписки. Попробуйте позже."
                        
                except ImportError:
                    return "❌ Модуль футбола не доступен."
                except Exception as e:
                    print(f"[TelegramBot] Ошибка при подписке на футбол пользователя {chat_id}: {e}")
                    return "❌ Ошибка при активации подписки. Попробуйте позже."
            
            elif token_clean == "football_unsub" or token_clean.startswith("football_unsub_"):
                # Deep-link для мгновенной отписки
                try:
                    import football
                    football.remove_football_subscription(chat_id)  # идемпотентно
                    message_text = """
✅ <b>Вы отписались от уведомлений о футболе.</b>

/start football — Подписаться снова
                    """.strip()
                    self.send_message(chat_id, message_text)
                    return None
                except ImportError:
                    return "❌ Модуль футбола не доступен."
                except Exception as e:
                    print(f"[TelegramBot] Ошибка при отписке пользователя {chat_id}: {e}")
                    return "❌ Ошибка при отписке. Попробуйте позже."

            elif token_clean.startswith("football_"):
                # Подписка через ссылку с веб-страницы (с токеном)
                # Извлекаем токен после "football_" (используем оригинальный token для сохранения регистра UUID)
                football_token = token[9:] if len(token) > 9 else ""  # "football_".length = 9
                
                # Импортируем функции футбола
                try:
                    import football
                    # Токен больше не сохраняем — он нужен только для внешней валидации перехода
                    success = football.add_football_subscription(chat_id)
                    # Привязываем токен к user_id в памяти, чтобы UI мог проверить статус
                    if football_token:
                        try:
                            football.bind_token_to_user(football_token, chat_id)
                        except Exception as bind_err:
                            print(f"[TelegramBot] Не удалось привязать токен к пользователю: {bind_err}")
                    # Привязываем токен к user_id в памяти, чтобы UI мог проверить статус
                    if football_token:
                        try:
                            football.bind_token_to_user(football_token, chat_id)
                        except Exception as bind_err:
                            print(f"[TelegramBot] Не удалось привязать токен к пользователю: {bind_err}")
                    
                    if success:
                        message_text = """
✅ <b>Подписка на футбольные уведомления активирована!</b>

⚽ Вы будете получать уведомления о важных матчах.

📱 <b>Команды:</b>
/unsubscribe - Отписаться от уведомлений
                        """.strip()
                        
                        self.send_message(chat_id, message_text)
                        return None  # Возвращаем None, так как сообщение уже отправлено
                    else:
                        return "❌ Ошибка при добавлении подписки. Попробуйте позже."
                        
                except ImportError:
                    return "❌ Модуль футбола не доступен."
                except Exception as e:
                    print(f"[TelegramBot] Ошибка при подписке на футбол пользователя {chat_id}: {e}")
                    return "❌ Ошибка при активации подписки. Попробуйте позже."
            
            # Иначе это подписка на перевод книги
            # Проверяем существование токена
            book_info = workflow_db_manager.get_book_by_access_token(token)
            
            if not book_info:
                return "❌ Токен не найден или недействителен. Проверьте ссылку."
            
            book_id = book_info['book_id']
            filename = book_info['filename']
            target_language = book_info['target_language']
            
            # Добавляем пользователя
            success = workflow_db_manager.add_telegram_user(chat_id, token)
            
            if not success:
                return "❌ Ошибка при добавлении подписки. Попробуйте позже."
            
            # Создаем inline-кнопку для проверки прогресса
            reply_markup = {
                "inline_keyboard": [[
                    {
                        "text": "📊 Проверить прогресс",
                        "callback_data": f"progress_{book_id}"
                    }
                ]]
            }
            
            message_text = f"""
✅ <b>Подписка активирована!</b>

📚 <b>Книга:</b> {filename}
🌍 <b>Язык:</b> {target_language}

🔔 Вы получите уведомление когда перевод будет готов.

📱 <b>Команды:</b>
/unsubscribe - Отписаться от уведомлений
            """.strip()
            
            # Отправляем сообщение с кнопкой
            self.send_message(chat_id, message_text, reply_markup=reply_markup)
            return None  # Возвращаем None, так как сообщение уже отправлено
            
        except Exception as e:
            print(f"[TelegramBot] Ошибка при подписке пользователя {chat_id}: {e}")
            return "❌ Ошибка при активации подписки. Попробуйте позже."
    
    def cmd_unsubscribe(self, chat_id: str) -> str:
        """Команда /unsubscribe для отписки от уведомлений"""
        try:
            # Отписываем от переводов
            books_success = workflow_db_manager.remove_telegram_user(chat_id)
            
            # Отписываем от футбола
            football_success = False
            try:
                import football
                football_success = football.remove_football_subscription(chat_id)
            except ImportError:
                pass
            except Exception as e:
                print(f"[TelegramBot] Ошибка при отписке от футбола пользователя {chat_id}: {e}")
            
            if books_success or football_success:
                messages = []
                if books_success:
                    messages.append("✅ Вы отписались от уведомлений о переводах.")
                if football_success:
                    messages.append("✅ Вы отписались от уведомлений о футболе.")
                return "\n".join(messages)
            else:
                return "ℹ️ Вы не были подписаны на уведомления."
                
        except Exception as e:
            print(f"[TelegramBot] Ошибка при отписке пользователя {chat_id}: {e}")
            return "❌ Ошибка при отписке. Попробуйте позже."
    
    def cmd_progress(self, chat_id: str, book_id: str) -> str:
        """Команда /progress для проверки прогресса перевода"""
        print(f"[TelegramBot] cmd_progress вызвана: chat_id={chat_id}, book_id={book_id}")
        
        if not book_id:
            return "❌ Укажите ID книги. Использование: /progress [book_id]"
        
        try:
            # Проверяем подписку пользователя
            user_subscriptions = workflow_db_manager.get_telegram_user_subscriptions(chat_id)
            
            if not user_subscriptions:
                return "❌ Вы не подписаны на уведомления. Используйте /start [токен] для подписки."
            
            # Проверяем, что пользователь подписан на эту книгу
            book_info = workflow_db_manager.get_book_workflow(book_id)
            if not book_info:
                return "❌ Книга не найдена или у вас нет доступа к ней."
            
            # Проверяем, что пользователь подписан на эту книгу
            user_has_access = False
            for subscription in user_subscriptions:
                if subscription.get('book_id') == book_id:
                    user_has_access = True
                    break
            
            if not user_has_access:
                return "❌ У вас нет доступа к этой книге. Используйте /start [токен] для подписки."
            
            # Получаем статус книги напрямую из БД
            print(f"[TelegramBot] Получаем статус книги {book_id} из БД")
            
            # Получаем данные напрямую из workflow_db_manager
            data = workflow_db_manager.get_workflow_book_status(book_id)
            if not data:
                return "❌ Книга не найдена в базе данных."
            
            # Рассчитываем прогресс по той же формуле, что и в веб-интерфейсе
            stages = data.get('book_stage_statuses', {})
            total_sections = data.get('total_sections_count', 0)
            sections_summary = data.get('sections_status_summary', {})
            
            # Получаем правильный порядок этапов
            stages_ordered = workflow_db_manager.get_all_stages_ordered_workflow()
            
            # Расчет прогресса
            score = 0
            max_score = 5 + 3 + total_sections + 1  # суммаризация + анализ + перевод + epub
            
            # Суммаризация (пропорционально секциям)
            summarized_sections = 0
            if sections_summary.get('summarize'):
                summary = sections_summary['summarize']
                summarized_sections = (summary.get('completed', 0) + 
                                     summary.get('completed_empty', 0) + 
                                     summary.get('skipped', 0))
            if total_sections > 0:
                score += (5 / total_sections) * summarized_sections
            
            # Анализ
            analyze_status = stages.get('analyze', {}).get('status')
            if analyze_status in ["completed", "completed_empty", "skipped"]:
                score += 3
            
            # Перевод
            translated_sections = 0
            if sections_summary.get('translate'):
                summary = sections_summary['translate']
                translated_sections = (summary.get('completed', 0) + 
                                     summary.get('completed_empty', 0) + 
                                     summary.get('skipped', 0))
            score += translated_sections
            
            # EPUB
            epub_status = stages.get('epub_creation', {}).get('status')
            if epub_status in ["completed", "completed_empty", "skipped"]:
                score += 1
            
            progress_percent = (score / max_score * 100) if max_score > 0 else 0
            
            # Формируем ответ
            book_title = data.get('book_title', data.get('filename', 'Неизвестная книга'))
            current_status = data.get('current_workflow_status', 'unknown')
            
            if current_status == 'completed':
                # Получаем access_token для создания ссылки
                access_token = book_info.get('access_token', '')
                download_link = make_download_link(access_token) if access_token else f"/download {book_id}"
                
                result = f"""
📚 <b>{book_title}</b>
✅ <b>Перевод завершен: 100% ({total_sections}/{total_sections} секций)</b>

🔗 {download_link}
                """.strip()
            else:
                # Детали по этапам в правильном порядке
                stage_details = []
                
                # Проходим по этапам в правильном порядке
                for stage in stages_ordered:
                    stage_name = stage['stage_name']
                    stage_data = stages.get(stage_name, {})
                    stage_status = stage_data.get('status', 'pending')
                    is_per_section = stage.get('is_per_section', False)
                    
                    # Определяем иконку и текст для этапа
                    if stage_status in ["completed", "completed_empty", "skipped"]:
                        icon = "✅"
                        if is_per_section and sections_summary.get(stage_name):
                            summary = sections_summary[stage_name]
                            completed = (summary.get('completed', 0) + 
                                       summary.get('completed_empty', 0) + 
                                       summary.get('skipped', 0))
                            stage_details.append(f"{icon} {stage_name.title()}: {completed}/{total_sections}")
                        else:
                            stage_details.append(f"{icon} {stage_name.title()}: завершен")
                    elif stage_status == "processing":
                        icon = "🔄"
                        if is_per_section and sections_summary.get(stage_name):
                            summary = sections_summary[stage_name]
                            completed = (summary.get('completed', 0) + 
                                       summary.get('completed_empty', 0) + 
                                       summary.get('skipped', 0))
                            stage_details.append(f"{icon} {stage_name.title()}: {completed}/{total_sections}")
                        else:
                            stage_details.append(f"{icon} {stage_name.title()}: в процессе")
                    else:
                        icon = "⏳"
                        if is_per_section:
                            stage_details.append(f"{icon} {stage_name.title()}: ожидает")
                        else:
                            stage_details.append(f"{icon} {stage_name.title()}: ожидает")
                
                result = f"""
📚 <b>{book_title}</b>
🔄 <b>Перевод в процессе: {progress_percent:.1f}% ({translated_sections}/{total_sections})</b>

📋 <b>Этапы:</b>
{chr(10).join(stage_details)}
                """.strip()
            
            return result
            
        except Exception as e:
            print(f"[TelegramBot] Ошибка при получении прогресса для книги {book_id}: {e}")
            return "❌ Ошибка при получении статуса перевода. Попробуйте позже."
    
    def cmd_help(self) -> str:
        """Команда /help - список всех команд"""
        return self.cmd_start()  # Используем тот же текст, что и в /start
    
    def cmd_status(self) -> str:
        """Команда /status - показывает статус системы"""
        try:
            from config import MAIN_DB_FILE, VIDEO_DB_FILE, WORKFLOW_DB_FILE, CACHE_DIR, UPLOADS_DIR, FULL_TRANSLATION_DIR
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
                (str(MAIN_DB_FILE), "Основная БД"),
                (str(VIDEO_DB_FILE), "Видео БД"),
                (str(WORKFLOW_DB_FILE), "Workflow БД")
            ]
            
            for db_file, description in db_files:
                if os.path.exists(db_file):
                    size = os.path.getsize(db_file)
                    size_mb = size / (1024 * 1024)
                    status_info.append(f"{description}: ✅ ({size_mb:.1f} MB)")
                else:
                    status_info.append(f"{description}: ❌")
            
            # Проверяем директории
            dirs = [str(CACHE_DIR), str(UPLOADS_DIR), str(FULL_TRANSLATION_DIR)]
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
            # Альтернативная информация без psutil
            try:
                import os
                import platform
                
                # Базовая информация о системе
                system_info = platform.system()
                python_version = platform.python_version()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Попытка получить информацию о памяти через /proc/meminfo (Linux)
                memory_info = "Недоступно"
                try:
                    with open('/proc/meminfo', 'r') as f:
                        mem_lines = f.readlines()
                        total_mem = 0
                        free_mem = 0
                        for line in mem_lines:
                            if line.startswith('MemTotal:'):
                                total_mem = int(line.split()[1]) / 1024  # MB
                            elif line.startswith('MemAvailable:'):
                                free_mem = int(line.split()[1]) / 1024  # MB
                        if total_mem > 0:
                            used_mem = total_mem - free_mem
                            memory_info = f"{used_mem:.0f} MB / {total_mem:.0f} MB"
                except:
                    pass
                
                return f"""
💻 <b>Информация о системе (базовая)</b>

🖥️ <b>Система:</b> {system_info}
🐍 <b>Python:</b> {python_version}
⏰ <b>Время:</b> {current_time}
🧠 <b>Память:</b> {memory_info}

⚠️ <b>psutil не установлен</b>
Для полной информации установите: pip install psutil
                """.strip()
                
            except Exception as e:
                return f"❌ Ошибка получения базовой информации о системе: {e}"
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
            # Обрабатываем сообщения
            message = update.get("message", {})
            if message:
                chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "")
                
                if chat_id and text:
                    # Обрабатываем команды
                    if text.startswith("/"):
                        parts = text.split(" ", 1)
                        command = parts[0]
                        args = parts[1] if len(parts) > 1 else ""
                        
                        # Команды, доступные всем пользователям
                        public_commands = ["/start", "/unsubscribe", "/progress"]
                        
                        # Проверяем доступ только для административных команд
                        if command not in public_commands and str(chat_id) not in self.allowed_chat_ids:
                            self.send_message(chat_id, "❌ Доступ запрещен")
                            continue
                        
                        response = self.handle_command(chat_id, command, args)
                        if response:  # Если response не None
                            self.send_message(chat_id, response)
            
            # Обрабатываем callback-запросы от кнопок
            callback_query = update.get("callback_query", {})
            if callback_query:
                chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
                callback_data = callback_query.get("data", "")
                
                if chat_id and callback_data:
                    if callback_data.startswith("progress_"):
                        book_id = callback_data[9:]  # Убираем "progress_"
                        response = self.cmd_progress(chat_id, book_id)
                        if response:
                            self.send_message(chat_id, response)
    
    def run_polling(self):
        """Запускает polling для получения обновлений"""
        if self.is_stub:
            print("[TelegramBot] Режим заглушки - polling отключен")
            return
            
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