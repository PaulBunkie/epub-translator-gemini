import os
import requests
import json
from typing import List, Dict, Any, Optional
from workflow_model_config import get_model_for_operation


class VideoChatHandler:
    """
    Класс для обработки диалогов с ИИ по содержанию видео.
    Использует модели из workflow_model_config и контекст видео из БД.
    """
    
    def __init__(self):
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.openrouter_api_key:
            raise ValueError("Не установлена переменная окружения OPENROUTER_API_KEY")
        
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self.max_history_messages = 10  # Максимум сообщений в истории
    
    def get_video_context_prompt(self, video_data: Dict[str, Any], analysis_data: Dict[str, Any]) -> str:
        """
        Создает системный промпт с контекстом видео.
        
        Args:
            video_data: Данные видео из БД
            analysis_data: Данные анализа из БД
            
        Returns:
            Системный промпт
        """
        title = video_data.get('title', 'Видео')
        channel = video_data.get('channel_title', 'Неизвестный канал')
        url = video_data.get('url', '')
        
        # Извлекаем тексты для контекста
        extracted_text = analysis_data.get('extracted_text', '')
        analysis_result = analysis_data.get('analysis_result', '')
        analysis_summary = analysis_data.get('analysis_summary', '')
        
        # Ограничиваем размер контекста и очищаем от проблемных символов
        max_text_length = 8000  # ~8k символов для контекста видео
        if len(extracted_text) > max_text_length:
            extracted_text = extracted_text[:max_text_length] + "\n\n[Текст обрезан для оптимизации...]"
        
        # Очищаем от суррогатных пар Unicode (проблема Windows)
        try:
            extracted_text = extracted_text.encode('utf-8', errors='ignore').decode('utf-8')
            analysis_result = analysis_result.encode('utf-8', errors='ignore').decode('utf-8') if analysis_result else ''
            analysis_summary = analysis_summary.encode('utf-8', errors='ignore').decode('utf-8') if analysis_summary else ''
        except:
            pass  # Если есть проблемы с кодировкой, просто игнорируем
        
        prompt = f"""Ты — эксперт-аналитик, который помогает пользователям разбираться в содержании видео.

=== КОНТЕКСТ ВИДЕО ===
Название: {title}
Канал: {channel}
URL: {url}

ИЗВЛЕЧЕННЫЙ ТЕКСТ С ВРЕМЕННЫМИ МЕТКАМИ:
{extracted_text}

{f'''КРАТКИЙ АНАЛИЗ:
{analysis_summary}''' if analysis_summary else ''}

{f'''ПОДРОБНЫЙ АНАЛИЗ:
{analysis_result[:2000] + '...' if len(analysis_result) > 2000 else analysis_result}''' if analysis_result else ''}

=== ИНСТРУКЦИИ ===
1. Отвечай ТОЛЬКО на основе предоставленного контекста видео
2. При обсуждении конкретных моментов указывай временные метки из извлеченного текста в формате (MM:SS) или (HH:MM:SS)
3. **ФОРМАТИРОВАНИЕ**: Используй Markdown - **жирный текст**, *курсив*, ## заголовки, * списки для структуры
4. Если пользователь спрашивает что-то НЕ связанное с содержанием видео - вежливо напомни, что ты можешь обсуждать только это видео
5. Будь дружелюбным и объясняй сложные концепции простым языком
6. Используй структуру временных меток для навигации по темам видео
7. Если в тексте есть конкретные данные, цифры, имена - ссылайся на них точно

Готов обсуждать содержание этого видео!"""

        return prompt
    
    def prepare_messages(self, system_prompt: str, user_message: str, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Подготавливает массив сообщений для API запроса.
        
        Args:
            system_prompt: Системный промпт с контекстом
            user_message: Новое сообщение пользователя  
            history: История диалога (максимум 10 сообщений)
            
        Returns:
            Массив сообщений для OpenRouter API
        """
        # Очищаем сообщение пользователя от проблемных символов
        try:
            user_message = user_message.encode('utf-8', errors='ignore').decode('utf-8')
        except:
            pass
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Обрезаем историю до максимального размера
        if len(history) > self.max_history_messages:
            history = history[-self.max_history_messages:]
        
        # Добавляем историю диалога (с очисткой)
        for msg in history:
            if msg.get('role') in ['user', 'assistant'] and msg.get('content'):
                content = msg['content']
                try:
                    content = content.encode('utf-8', errors='ignore').decode('utf-8')
                except:
                    pass
                messages.append({
                    "role": msg['role'],
                    "content": content
                })
        
        # Добавляем новое сообщение пользователя
        messages.append({"role": "user", "content": user_message})
        
        return messages
    
    def chat_with_model(self, messages: List[Dict[str, str]], model_name: str) -> Optional[str]:
        """
        Отправляет запрос к модели через OpenRouter API.
        
        Args:
            messages: Массив сообщений
            model_name: Имя модели для использования
            
        Returns:
            Ответ модели или None в случае ошибки
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": 0.3,  # Более консистентные ответы для диалогов
                "max_tokens": 2000,  # Ограничиваем длину ответа
                "stream": False
            }
            
            print(f"[VideoChatHandler] Отправка запроса к модели: {model_name}")
            print(f"[VideoChatHandler] Количество сообщений: {len(messages)}")
            
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=60
            )
            
            print(f"[VideoChatHandler] Ответ API: статус {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if 'choices' in data and len(data['choices']) > 0:
                    choice = data['choices'][0]
                    if 'message' in choice and 'content' in choice['message']:
                        content = choice['message']['content'].strip()
                        try:
                            print(f"[VideoChatHandler] Получен ответ длиной {len(content)} символов")
                        except UnicodeEncodeError:
                            print(f"[VideoChatHandler] Получен ответ длиной {len(content)} символов [содержит специальные символы]")
                        return content
                    else:
                        print("[VideoChatHandler] Неверная структура ответа")
                else:
                    print("[VideoChatHandler] Пустой ответ от модели")
            else:
                try:
                    error_data = response.json()
                    print(f"[VideoChatHandler] Ошибка API: {error_data}")
                except:
                    print(f"[VideoChatHandler] HTTP ошибка: {response.status_code}, текст: {response.text[:500]}")
                
            return None
            
        except Exception as e:
            print(f"[VideoChatHandler] Исключение при запросе к модели {model_name}: {e}")
            return None
    
    def process_chat_message(self, video_data: Dict[str, Any], analysis_data: Dict[str, Any], 
                           user_message: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Обрабатывает сообщение пользователя и возвращает ответ ИИ.
        
        Args:
            video_data: Данные видео из БД
            analysis_data: Данные анализа из БД  
            user_message: Сообщение пользователя
            history: История диалога
            
        Returns:
            Результат обработки с ответом или ошибкой
        """
        try:
            # Создаем системный промпт с контекстом видео
            system_prompt = self.get_video_context_prompt(video_data, analysis_data)
            
            # Подготавливаем сообщения для API
            messages = self.prepare_messages(system_prompt, user_message, history)
            
            # Пробуем модели по очереди (primary -> fallback_level1 -> fallback_level2)
            model_levels = ['primary', 'fallback_level1', 'fallback_level2']
            
            for level in model_levels:
                model_name = get_model_for_operation('video_chat', level)
                if not model_name:
                    continue
                    
                response = self.chat_with_model(messages, model_name)
                if response:
                    return {
                        'success': True,
                        'response': response,
                        'model_used': model_name,
                        'model_level': level
                    }
                else:
                    print(f"[VideoChatHandler] Модель {model_name} не сработала, пробуем следующую")
            
            # Все модели не сработали
            return {
                'success': False,
                'error': 'Все модели недоступны. Попробуйте позже.',
                'model_used': None
            }
            
        except Exception as e:
            print(f"[VideoChatHandler] Ошибка обработки сообщения: {e}")
            return {
                'success': False,
                'error': f'Внутренняя ошибка: {str(e)}',
                'model_used': None
            }