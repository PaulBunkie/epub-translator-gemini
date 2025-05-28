# --- START OF FILE translation_module.py ---

from abc import ABC, abstractmethod
import google.generativeai as genai
import os
import re
from typing import Optional, List, Dict, Any
import requests
import json
import time

# Константа для обозначения ошибки лимита контекста
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

# --- Шаблоны промптов для различных операций ---
PROMPT_TEMPLATES = {
    'translate': """You are a professional literary translator translating a book for a {target_language}-speaking audience. Your goal is to provide a high-quality, natural-sounding translation into {target_language}, adhering to the following principles:
- Perform a literary translation, preserving the author's original style, tone, and nuances.
- Maintain consistency in terminology, character names, and gender portrayal *within this entire response*.
- Avoid softening strong language unless culturally necessary.
- Translate common abbreviations (like 'e.g.', 'i.e.', 'CIA') according to their established {target_language} equivalents.
- Keep uncommon or fictional abbreviations/acronyms (e.g., KPS) in their original form.
- For neologisms or compound words, find accurate and stylistically appropriate {target_language} equivalents and use them consistently *within this response*.
- Keep all Markdown elements like headings (#, ##), lists (-, *), bold (**), italic (*), code (`), and links ([text](url)) unchanged.
{russian_dialogue_rule}
- If clarification is needed for a {target_language} reader (cultural notes, untranslatable puns, proper names, etc.), use translator's footnotes.
  - **Format:** Insert a sequential footnote marker directly after the word/phrase.
    - **Preferred format:** Use superscript numbers (like ¹,²,³).
    - **Alternative format (if superscript is not possible):** Use numbers in square brackets (like [1], [2], [3]).
  - **Content:** At the very end of the translated section, add a separator ('---') and a heading('{translator_notes_heading}'). List all notes sequentially by their marker (e.g., '¹ Explanation.' or '[1] Explanation.').
  - Use footnotes sparingly.
{prompt_ext_section}
{previous_context_section}
Text to Process:
{text}

Result:""",

    # --- Шаблон для суммаризации (пересказа) ---
    'summarize': """Summarize the following text into {target_language} and follow user instruction {prompt_ext_section}:

{text}

Summary:""",

    # --- Шаблон для анализа трудностей перевода ---
    'analyze': """You are a literary analyst assisting a translator. Your task is to read the following text and identify potential translation difficulties for a target audience unfamiliar with the source material. Focus on finding and listing:
    - Proper nouns (names of people, places, organizations, etc.)
    - Neologisms, invented words, or unusual compound words.
    - Unfamiliar or fictional abbreviations and acronyms.
    - Any other elements that might be challenging to translate or understand without context (e.g., specific slang, cultural references, wordplay, archaic terms).

    Provide your analysis and lists strictly in {target_language}.
    List only items that are likely to be unusual, unfamiliar, or potentially difficult for an *educated* general reader of {target_language}. Exclude common names, well-known places (like countries or major cities), and widely recognized organizations unless their usage in the text is unusual or requires specific context.
    For each listed item:
    - Briefly explain *why* it might be a difficulty (e.g., "выдуманное имя", "потенциальный неологизм", "малоизвестная аббревиатура" in {target_language}).
    - **MUST** provide at least one suggested translation option into {target_language}.
    - If the difficulty is complex, provide multiple suggested translation options.

    Explicitly exclude common idioms, standard phrases, and straightforward descriptive constructions that are easily translatable or understandable by an educated reader.

    {prompt_ext_section}

    Text to Analyze:
    {text}

Analysis:"""
}

# --- Форматирование дополнительных секций промпта ---
def _format_prompt_section(title: str, content: Optional[str]) -> str:
    """Форматирует дополнительную секцию промпта, если контент существует."""
    if content and content.strip():
        return f"\\n---\\n{title}:\\n{content}\\n---"
    return ""

class BaseTranslator(ABC):
    @staticmethod
    def _build_prompt(
        operation_type: str,
        target_language: str,
        text: str,
        previous_context: str = "",
        prompt_ext: Optional[str] = None
    ) -> str:
        """Формирует строку промпта для модели на основе типа операции и входных данных."""
        template = PROMPT_TEMPLATES.get(operation_type)
        if not template:
            raise ValueError(f"Неизвестный тип операции: {operation_type}")

        # Форматируем дополнительные секции отдельно, чтобы не добавлять их, если они пустые
        prompt_ext_section = _format_prompt_section("ADDITIONAL INSTRUCTIONS (Apply if applicable, follow strictly for names and terms defined here)", prompt_ext)
        previous_context_section = _format_prompt_section("Previous Context (use for style and recent terminology reference)", previous_context)

        # --- Рассчитываем значение для правила русского диалога ---
        russian_dialogue_rule = ' - When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.' if target_language.lower() == 'russian' else ''

        # --- Рассчитываем значение для заголовка примечаний переводчика ---
        translator_notes_heading = 'Примечания переводчика' if target_language.lower() == 'russian' else 'Translator Notes'

        # Используем f-строку для форматирования шаблона
        prompt = template.format(
            target_language=target_language,
            text=text,
            prompt_ext_section=prompt_ext_section,
            previous_context_section=previous_context_section,
            russian_dialogue_rule=russian_dialogue_rule,
            translator_notes_heading=translator_notes_heading, # Передаем рассчитанное значение
            # Добавляем сюда другие переменные, если они понадобятся для других шаблонов
            # например, summary_length=summary_length для суммаризации
        )

        # Удаляем возможные двойные пустые строки, если секции были пустыми
        # return "\n".join(line for line in prompt.split('\n') if line.strip() or line == '') # Более строгий вариант
        return prompt.replace("\n\n\n", "\n\n").strip() # Простой вариант очистки пустых строк

    @abstractmethod
    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None,
                       operation_type: str = 'translate') -> Optional[str]:
        pass

    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                      model_name: str = None, prompt_ext: Optional[str] = None,
                      operation_type: str = 'translate') -> Optional[str]:
        """Основная функция для перевода строки текста (реализация в базовом классе)."""
        # Эта константа может быть специфична для модели/API, но пока оставим ее здесь.
        # В идеале, CHUNK_SIZE_LIMIT_CHARS должен быть определяем в дочерних классах
        # или передаваться как аргумент.
        CHUNK_SIZE_LIMIT_CHARS = 20000
        text_len = len(text_to_translate)
        print(f"[BaseTranslator] Проверка длины текста: {text_len} симв. Лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} симв.")

        if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1:
            print("[BaseTranslator] Пробуем перевод целиком...")
            # Вызываем абстрактный метод translate_chunk, реализованный в дочернем классе
            result = self.translate_chunk(model_name, text_to_translate, target_language, prompt_ext=prompt_ext, operation_type=operation_type)
            if result != CONTEXT_LIMIT_ERROR:
                return result
            print("[BaseTranslator] Перевод целиком не удался (лимит контекста), переключаемся на чанки.")

        # Разбиваем на параграфы
        print(f"[BaseTranslator] Текст длинный ({text_len} симв.), разбиваем на чанки...")
        paragraphs = text_to_translate.split('\n\n')
        chunks = []
        current_chunk = []
        current_chunk_len = 0

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            paragraph_len = len(paragraph)
            if paragraph_len > CHUNK_SIZE_LIMIT_CHARS:
                if current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_chunk_len = 0

                sentences = paragraph.split('. ')
                temp_chunk = []
                temp_chunk_len = 0

                for sentence in sentences:
                    sentence = sentence.strip()
                    sentence_len = len(sentence)

                    if temp_chunk_len + sentence_len > CHUNK_SIZE_LIMIT_CHARS:
                        if temp_chunk:
                            chunks.append('. '.join(temp_chunk) + '.')
                        temp_chunk = [sentence]
                        temp_chunk_len = sentence_len
                    else:
                        temp_chunk.append(sentence)
                        temp_chunk_len += sentence_len + 2  # +2 для '. '

                if temp_chunk:
                    chunks.append('. '.join(temp_chunk) + '.')
            else:
                if current_chunk_len + paragraph_len > CHUNK_SIZE_LIMIT_CHARS:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = [paragraph]
                    current_chunk_len = paragraph_len
                else:
                    current_chunk.append(paragraph)
                    current_chunk_len += paragraph_len + 4  # +4 для '\n\n'

        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))

        if not chunks:
            print("[BaseTranslator] Ошибка: Не удалось создать чанки!")
            return None

        print(f"[BaseTranslator] Текст разбит на {len(chunks)} чанков.")
        translated_chunks = []
        last_successful_translation = ""

        for i, chunk in enumerate(chunks, 1):
            print(f"[BaseTranslator] -- Перевод чанка {i}/{len(chunks)} ({len(chunk)} симв.)...")
            context_fragment = " ".join(last_successful_translation.split()[-100:]) if last_successful_translation else ""
            
            # Вызываем абстрактный метод translate_chunk
            translated_chunk = self.translate_chunk(
                model_name,
                chunk,
                target_language,
                previous_context=context_fragment,
                prompt_ext=prompt_ext,
                operation_type=operation_type
            )

            if translated_chunk == CONTEXT_LIMIT_ERROR:
                print(f"[BaseTranslator] Ошибка лимита контекста на чанке {i}.")
                return CONTEXT_LIMIT_ERROR
            elif translated_chunk is None:
                print(f"[BaseTranslator] Ошибка перевода чанка {i}.")
                return None
            else:
                translated_chunks.append(translated_chunk)
                last_successful_translation = translated_chunk

            # --- НОВАЯ ЛОГИКА: Короткая задержка после обработки каждого чанка ---
            # Добавляем небольшую задержку, чтобы снизить частоту запросов для больших секций
            # Этот sleep выполняется в фоновом потоке, не блокирует UI
            print(f"[BaseTranslator] Задержка {1} сек после чанка {i}/{len(chunks)}.")
            time.sleep(1) # Длительность задержки в секундах (можно настроить)
            # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        print("[BaseTranslator] Сборка переведенных чанков...")
        return "\n\n".join(translated_chunks)

    @abstractmethod
    def get_available_models(self) -> List[Dict[str, Any]]:
        pass

class GoogleTranslator(BaseTranslator):
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Не установлена переменная окружения GOOGLE_API_KEY")
        genai.configure(api_key=self.api_key)
        print("Google API ключ успешно сконфигурирован.")

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Возвращает список доступных моделей от Google."""
        try:
            models = []
            for model in genai.list_models():
                if "generateContent" in model.supported_generation_methods:
                    models.append({
                        'name': model.name,
                        'display_name': f"Google {model.name}",
                        'input_token_limit': model.input_token_limit,
                        'output_token_limit': model.output_token_limit,
                        'source': 'google'
                    })
            return models
        except Exception as e:
            print(f"Ошибка при получении списка моделей Google: {e}")
            return []

    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None,
                       operation_type: str = 'translate') -> Optional[str]:
        """Переводит чанк текста с использованием Google API с обработкой ошибок и лимитов."""
        prompt = self._build_prompt(
            operation_type=operation_type,
            target_language=target_language,
            text=text,
            previous_context=previous_context,
            prompt_ext=prompt_ext
        )

        if not text.strip():
            return ""

        # --- Retry logic for API errors ---
        max_retries = 5
        retry_delay_seconds = 5 # Initial delay

        for attempt in range(max_retries):
            try:
                print(f"[GoogleTranslator] Отправка запроса на Google API (попытка {attempt + 1}/{max_retries})...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                print(f"[GoogleTranslator] Получен ответ.")

                # Check for finish reason indicating potential issues
                if (hasattr(response, 'candidates') and response.candidates
                    and hasattr(response.candidates[0], 'finish_reason')): # Check if finish_reason exists
                     finish_reason = response.candidates[0].finish_reason
                     print(f"[GoogleTranslator] Finish reason: {finish_reason}")

                     if finish_reason in ['MAX_TOKENS', 'STOP', 'SAFETY', 'RECITATION', 'OTHER']:
                         # These might indicate issues like context limit or safety filters
                         if finish_reason == 'MAX_TOKENS':
                             print("[GoogleTranslator] Вероятно, превышен лимит токенов (MAX_TOKENS).")
                             return CONTEXT_LIMIT_ERROR # Or handle specifically if needed
                         elif finish_reason == 'SAFETY':
                             print("[GoogleTranslator] Ответ заблокирован фильтрами безопасности (SAFETY).")
                             # Decide how to handle safety issues - maybe return a specific error or None
                             return None # For now, treat as a failure
                         elif finish_reason == 'STOP':
                             print("[GoogleTranslator] Модель остановилась до завершения (STOP).")
                             # This might be normal, but could indicate an issue depending on prompt
                             pass # Continue processing the response
                         else:
                             # Handle other less common finish reasons if necessary
                             print(f"[GoogleTranslator] Неизвестная причина завершения: {finish_reason}")
                             pass # Continue processing the response



                # Check for valid response content
                if hasattr(response, 'text'):
                    return response.text.strip()
                else:
                    print("[GoogleTranslator] Ошибка: Ответ от Google API не содержит текста.")
                    # Print details if available for debugging
                    if hasattr(response, 'prompt_feedback'):
                         print(f"[GoogleTranslator] Prompt feedback: {response.prompt_feedback}")
                    if hasattr(response, 'candidates'):
                        print(f"[GoogleTranslator] Candidates: {response.candidates}")

                    # Check if it's a context limit issue indicated by response structure/feedback
                    if (hasattr(response, 'prompt_feedback') and response.prompt_feedback
                        and hasattr(response.prompt_feedback, 'block_reason')
                        and response.prompt_feedback.block_reason == 'RESOURCE_EXHAUSTED'):
                         print("[GoogleTranslator] Ошибка: Превышен лимит контекста (RESOURCE_EXHAUSTED).")
                         return CONTEXT_LIMIT_ERROR

                    return None # Return None if no text and not a known context error


            except Exception as e:
                error_text = str(e).lower()
                print(f"[GoogleTranslator] Ошибка Google API: {e}")

                # Check if the error is likely a context limit issue
                context_keywords = [
                    "context window", "token limit", "maximum input length",
                    "resource exhausted", "limit exceeded", "400 invalid argument"
                ]
                is_likely_context_error = any(keyword in error_text for keyword in context_keywords)

                if is_likely_context_error:
                    print("[GoogleTranslator] Ошибка: Вероятно, превышен лимит контекста.")
                    return CONTEXT_LIMIT_ERROR

                # Simple retry for other general exceptions
                if attempt < max_retries - 1:
                     print(f"[GoogleTranslator] Повторная попытка через {retry_delay_seconds} сек...")
                     time.sleep(retry_delay_seconds)
                     retry_delay_seconds *= 2 # Exponential backoff
                     continue
                else:
                    print("[GoogleTranslator] Максимальное количество попыток исчерпано.")
                    return None # Return None if all retries fail

        print("[GoogleTranslator] Не удалось получить успешный ответ после всех попыток.")
        return None # Return None if all retries fail

class OpenRouterTranslator(BaseTranslator):
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1"

    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("Не установлена переменная окружения OPENROUTER_API_KEY")
        
        # Базовые заголовки для всех запросов
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000"),  # Требуется openrouter.ai
            "Content-Type": "application/json",
        }

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Получает список доступных моделей от openrouter.ai."""
        try:
            response = requests.get(
                f"{self.OPENROUTER_API_URL}/models",
                headers=self.headers
            )
            response.raise_for_status()
            models = response.json().get("data", [])
            
            # Форматируем данные в тот же формат, что и для Google API
            formatted_models = []
            for model in models:
                formatted_models.append({
                    'name': model['id'],
                    'display_name': model.get('name', model['id']),
                    'input_token_limit': model.get('context_length', 'N/A'),
                    'output_token_limit': model.get('context_length', 'N/A'),
                    'source': 'openrouter',
                    'pricing': model.get('pricing') # Добавляем информацию о стоимости
                })
            
            return sorted(formatted_models, key=lambda x: x['display_name'])
        except Exception as e:
            print(f"Ошибка при получении списка моделей: {e}")
            return []

    def translate_chunk(
        self,
        model_name: str,
        text: str,
        target_language: str = "russian",
        previous_context: str = "",
        prompt_ext: Optional[str] = None,
        operation_type: str = 'translate'
    ) -> Optional[str]:
        """Переводит чанк текста с использованием OpenRouter API с обработкой ошибок и лимитов."""
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            # "HTTP-Referer": os.getenv("YOUR_SITE_URL"), # Optional: Replace with your website URL
            # "X-Title": os.getenv("YOUR_APP_NAME"), # Optional: Replace with your app name
        }

        prompt = self._build_prompt(
            operation_type=operation_type,
            target_language=target_language,
            text=text,
            previous_context=previous_context,
            prompt_ext=prompt_ext
        )

        data = {
            "model": model_name,
            "prompt": prompt,
            "max_tokens": 4000, # Adjust based on model and task
        }

        # --- Retry logic for 429 errors ---
        max_retries = 5
        retry_delay_seconds = 5 # Initial delay

        for attempt in range(max_retries):
            try:
                print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries})...")
                response = requests.post(f"{self.OPENROUTER_API_URL}/chat/completions", headers=headers, data=json.dumps(data))
                print(f"[OpenRouterTranslator] Получен ответ: Статус {response.status_code}")

                # --- Проверка заголовков лимитов (опционально) ---
                if 'X-Ratelimit-Remaining' in response.headers:
                    print(f"[OpenRouterTranslator] X-Ratelimit-Remaining: {response.headers['X-Ratelimit-Remaining']}")
                if 'X-Ratelimit-Limit' in response.headers:
                     print(f"[OpenRouterTranslator] X-Ratelimit-Limit: {response.headers['X-Ratelimit-Limit']}")
                if 'X-Ratelimit-Reset' in response.headers:
                    print(f"[OpenRouterTranslator] X-Ratelimit-Reset: {response.headers['X-Ratelimit-Reset']}")


                if response.status_code == 200:
                    response_json = response.json()
                    # Проверка наличия 'choices' и 'message'
                    if response_json and 'choices' in response_json and response_json['choices']:
                         # --- ИЗМЕНЕНИЕ: Проверяем наличие 'message' и 'content' ИЛИ только 'text' ---
                         if 'message' in response_json['choices'][0] and 'content' in response_json['choices'][0]['message']:
                             print("[OpenRouterTranslator] Ответ получен в формате message.content")
                             return response_json['choices'][0]['message']['content'].strip()
                         elif 'text' in response_json['choices'][0]:
                              print("[OpenRouterTranslator] Ответ получен в формате text")
                              return response_json['choices'][0]['text'].strip()
                         # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                         else:
                             print("[OpenRouterTranslator] Ошибка: Неверный формат ответа от API (отсутствует content или text).")
                             print(f"Ответ API: {response_json}")
                             return None
                    else:
                        print("[OpenRouterTranslator] Ошибка: Неверный формат ответа от API (отсутствуют choices).")
                        print(f"Ответ API: {response_json}")
                        return None

                elif response.status_code == 429:
                    print(f"[OpenRouterTranslator] Ошибка 429 (Too Many Requests). Повторная попытка через {retry_delay_seconds} сек...")
                    # Check for Retry-After header
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            retry_delay_seconds = int(retry_after) # Use value from header if available
                        except ValueError:
                            pass # Stick with default if header is not an integer
                    time.sleep(retry_delay_seconds)
                    retry_delay_seconds *= 2 # Exponential backoff
                    continue # Try again

                elif response.status_code >= 400:
                    print(f"[OpenRouterTranslator] Ошибка API: Статус {response.status_code}")
                    try:
                        error_details = response.json()
                        print(f"[OpenRouterTranslator] Детали ошибки: {error_details}")
                    except json.JSONDecodeError:
                        print("[OpenRouterTranslator] Ошибка API: Не удалось декодировать JSON ответа.")

                    # Check for context limit error indicator
                    if response.text and "context window" in response.text.lower():
                        return CONTEXT_LIMIT_ERROR

                    return None # Return None for other client/server errors

            except requests.exceptions.RequestException as e:
                print(f"[OpenRouterTranslator] Ошибка запроса к API: {e}")
                if attempt < max_retries - 1:
                     print(f"[OpenRouterTranslator] Повторная попытка через {retry_delay_seconds} сек...")
                     time.sleep(retry_delay_seconds)
                     retry_delay_seconds *= 2  # Exponential backoff
                     continue
                else:
                    print("[OpenRouterTranslator] Максимальное количество попыток исчерпано.")
                    return None

            except Exception as e:
                 print(f"[OpenRouterTranslator] Непредвиденная ошибка: {e}")
                 return None

        print("[OpenRouterTranslator] Не удалось получить успешный ответ после всех попыток.")
        return None # Return None if all retries fail

def configure_api() -> None:
    """Проверяет наличие необходимых ключей API."""
    errors = []
    
    # Проверяем Google API
    if not os.getenv("GOOGLE_API_KEY"):
        errors.append("Не установлена переменная окружения GOOGLE_API_KEY")
    
    # Проверяем OpenRouter API
    if not os.getenv("OPENROUTER_API_KEY"):
        errors.append("Не установлена переменная окружения OPENROUTER_API_KEY")
    
    if errors:
        raise ValueError("\n".join(errors))

def translate_text(text_to_translate: str, target_language: str = "russian",
                  model_name: str = None, prompt_ext: Optional[str] = None,
                  operation_type: str = 'translate') -> Optional[str]:
    """Переводит текст, используя соответствующий API на основе имени модели и ее источника."""

    if not model_name:
        # Если модель не указана, используем дефолтную и определяем ее источник
        model_name = "gemini-1.5-flash"

    # Получаем полный список моделей, чтобы найти источник по имени
    # ВНИМАНИЕ: Этот вызов может быть медленным, если API отвечают долго.
    # В более сложном приложении, список моделей лучше кэшировать.
    available_models = get_models_list()
    selected_model_info = None
    for model_info in available_models:
        # Проверяем, что это словарь и что у него есть ключ 'name' и 'source'
        if isinstance(model_info, dict) and model_info.get('name') == model_name and 'source' in model_info:
            selected_model_info = model_info
            break

    if not selected_model_info:
        print(f"ОШИБКА: Не найдена информация об источнике для модели '{model_name}'. Невозможно перевести.")
        return None # Или выбросить ошибку, или использовать дефолтный переводчик

    source = selected_model_info['source']
    # Теперь у нас есть источник, создаем нужный переводчик
    if source == "google":
        translator = GoogleTranslator()
    elif source == "openrouter":
        translator = OpenRouterTranslator()
    else:
        # Этого не должно произойти, если get_models_list корректно добавляет source
        print(f"ОШИБКА: Неизвестный источник '{source}' для модели '{model_name}'.")
        return None

    # Вызываем метод translate_text у выбранного экземпляра переводчика
    # Передаем model_name, так как это часто нужно самому API
    # Передаем target_language и prompt_ext как обычно
    return translator.translate_text(text_to_translate, target_language, model_name, prompt_ext, operation_type)

def get_models_list() -> List[Dict[str, Any]]:
    """Возвращает отсортированный список моделей: сначала Google, затем бесплатные OpenRouter."""
    google_models = []
    openrouter_zero_cost_models = [] # Меняем название переменной для ясности
    
    # Получаем модели от Google
    try:
        google_translator = GoogleTranslator()
        google_models = google_translator.get_available_models()
        print(f"Получено {len(google_models)} моделей от Google API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей Google: {e}")
    
    # Получаем модели от OpenRouter
    try:
        openrouter_translator = OpenRouterTranslator()
        all_openrouter_models = openrouter_translator.get_available_models()
        
        # Фильтруем только модели с нулевой стоимостью
        for model in all_openrouter_models:
            pricing = model.get('pricing')
            # Проверяем, что есть информация о стоимости, есть поля prompt и completion,
            # и их числовые значения равны 0.0
            if pricing and 'prompt' in pricing and 'completion' in pricing:
                try:
                    prompt_cost = float(pricing['prompt'])
                    completion_cost = float(pricing['completion'])
                    if prompt_cost == 0.0 and completion_cost == 0.0:
                        openrouter_zero_cost_models.append(model)
                except (ValueError, TypeError) as e:
                    # Ловим ошибки преобразования, если стоимость не число
                    print(f"Ошибка парсинга стоимости для модели {model.get('name', model['id'])}: {e}")
                    continue # Пропускаем эту модель, если не удалось распарсить стоимость

        print(f"Получено {len(openrouter_zero_cost_models)} моделей с нулевой стоимостью из {len(all_openrouter_models)} от OpenRouter API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей OpenRouter: {e}")
    
    # Объединяем списки: сначала Google, потом модели OpenRouter с нулевой стоимостью
    return google_models + sorted(openrouter_zero_cost_models, key=lambda x: x.get('display_name', '').lower())

# --- END OF FILE translation_module.py ---