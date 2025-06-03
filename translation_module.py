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

# --- Новая константа для обозначения ошибки пустого ответа после ретраев ---
EMPTY_RESPONSE_ERROR = "__EMPTY_RESPONSE_AFTER_RETRIES__"
# --- Конец новой константы ---

# Переменная для кэширования списка моделей
_cached_models_list: Optional[List[Dict[str, Any]]] = None
_model_list_last_update: Optional[float] = None # Опционально: для будущего кэширования по времени
_MODEL_LIST_CACHE_TTL = 3600 # Время жизни кэша в секундах (1 час) - можно настроить

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
    'summarize': """Your task is to act as a highly effective summarization engine.
You will be given a text and a target language.
Your GOAL is to provide a concise and accurate summary of the provided text in the specified target language.
Your output MUST be ONLY the summary. Do not include any introductory or concluding remarks outside the summary itself.
{prompt_ext_section}

Target Language: {target_language}

Text to Summarize:
{text}

Summary in {target_language}:""",

    # --- Шаблон для анализа трудностей перевода ---
    'analyze': """You are a literary analyst assisting a translator. Your task is to read the following text and identify potential translation difficulties for a target audience unfamiliar with the source material. Focus on finding and listing:
    - Proper nouns (names of people, places, organizations, etc.)
    - Neologisms, invented words, or unusual compound words.
    - Unfamiliar or fictional abbreviations and acronyms.
    - Any other elements that might be challenging to translate or understand without context (e.g., specific slang, cultural references, wordplay, archaic terms).

    Provide your analysis and lists strictly in {target_language}.
    List only items that are likely to be unusual, unfamiliar, or potentially difficult for an *educated* general reader of {target_language}. Exclude common names, well-known places (like countries or major cities), and widely recognized organizations unless their usage in the text is unusual or requires specific context.
    For each listed item:
    - Briefly explain *why* it might be a difficulty (e.g., fictional name, potential neologism, obscure abbreviation in {target_language}).
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

        text_len = len(text_to_translate)
        CHUNK_SIZE_LIMIT_CHARS = 0 # Инициализируем переменную
        limit_source_desc = ""

        # ИЗМЕНЕНИЕ: Определяем лимит чанка в зависимости от типа операции
        if operation_type == 'translate':
            CHUNK_SIZE_LIMIT_CHARS = 20000
            limit_source_desc = "Фиксированный лимит для перевода"
        elif operation_type in ['summarize', 'analyze']:
            # Получаем лимит токенов для операций summarize/analyze
            actual_model_name = model_name if model_name else "gemini-1.5-flash"
            token_limit = get_context_length(actual_model_name) # Получаем лимит токенов

            # Оцениваем лимит символов, умножая лимит токенов на 3 (с запасом)
            CHUNK_SIZE_LIMIT_CHARS = int(token_limit * 3)
            limit_source_desc = f"Лимит по токенам модели ({token_limit} * 3)"
        else:
            # Дефолтное значение для неизвестных операций
            CHUNK_SIZE_LIMIT_CHARS = 20000
            limit_source_desc = "Дефолтный лимит для неизвестной операции"
            print(f"[BaseTranslator] Предупреждение: Неизвестный тип операции '{operation_type}'. Используется дефолтный лимит чанка.")


        # Добавляем минимальное ограничение, чтобы избежать деления на ноль или слишком маленьких чанков
        MIN_CHUNK_SIZE = 1000 # Минимум 1000 символов, можно настроить
        if CHUNK_SIZE_LIMIT_CHARS < MIN_CHUNK_SIZE:
             CHUNK_SIZE_LIMIT_CHARS = MIN_CHUNK_SIZE
             limit_source_desc += " (увеличен до минимума)"
             print(f"[BaseTranslator] Лимит чанка ({operation_type}) был меньше минимального. Установлен: {CHUNK_SIZE_LIMIT_CHARS}.")


        # Обновляем сообщение в логе, чтобы отразить, как был установлен лимит
        print(f"[BaseTranslator] Проверка длины текста: {text_len} симв. Лимит чанка ({limit_source_desc}): {CHUNK_SIZE_LIMIT_CHARS} симв.")

        # Теперь используем CHUNK_SIZE_LIMIT_CHARS в оставшейся логике функции
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
            
            # Вызываем абстрактный метод translate_chunk для перевода текущего чанка
            # Передаем контекст предыдущего успешного перевода и prompt_ext
            chunk_result = self.translate_chunk(model_name, chunk, target_language, previous_context=context_fragment, prompt_ext=prompt_ext, operation_type=operation_type)

            # --- ДОБАВЛЕНО: Обработка EMPTY_RESPONSE_ERROR из translate_chunk ---
            if chunk_result == EMPTY_RESPONSE_ERROR:
                print(f"[BaseTranslator] -- Чанк {i} вернул EMPTY_RESPONSE_ERROR после ретраев. Прерываем перевод.\n")
                return EMPTY_RESPONSE_ERROR # Пропагандируем ошибку пустого ответа после ретраев
            # --- КОНЕЦ ДОБАВЛЕНО ---

            if chunk_result is None:
                print(f"[BaseTranslator] -- Чанк {i} вернул None. Прерываем перевод.\n")
                return None # Ошибка в чанке, останавливаем весь перевод

            if chunk_result == CONTEXT_LIMIT_ERROR:
                print(f"[BaseTranslator] -- Чанк {i} превысил лимит контекста. Прерываем перевод.\n")
                return CONTEXT_LIMIT_ERROR # Пропагандируем ошибку лимита контекста

            # Если чанк успешно переведен, добавляем его к общему результату
            translated_chunks.append(chunk_result)
            last_successful_translation = chunk_result

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
        """Переводит чанк текста с использованием Google API с обработкой ошибок, лимитов и ретраями для пустых ответов."""
        prompt = self._build_prompt(operation_type, target_language, text, previous_context, prompt_ext)
        max_retries = 3 # Всего 3 попытки (1 начальная + 2 ретрая) для пустых ответов и ошибок API
        retry_delay_seconds = 5 # Задержка между попытками

        for attempt in range(max_retries):
            try:
                print(f"[GoogleTranslator] Отправка запроса на Google API (попытка {attempt + 1}/{max_retries})...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                print(f"[GoogleTranslator] Получен ответ.")

                # Проверяем наличие текста в ответе. Если есть, возвращаем его сразу.
                if response.text and response.text.strip():
                    print(f"[GoogleTranslator] Успешно получен текст ответа на попытке {attempt + 1}.")
                    return response.text.strip() # Возвращаем очищенный текст
                else:
                    # Ответ пустой или содержит только пробелы
                    print(f"[GoogleTranslator] Получен пустой ответ от модели на попытке {attempt + 1}.")
                    if attempt < max_retries - 1:
                        print(f"[GoogleTranslator] Ожидание {retry_delay_seconds} секунд перед следующей попыткой...")
                        time.sleep(retry_delay_seconds) # Ждем перед следующей попыткой
                    else:
                        print("[GoogleTranslator] Максимальное количество попыток достигнуто с пустым ответом.")
                        # После последней попытки возвращаем специальный индикатор ошибки
                        return EMPTY_RESPONSE_ERROR

            except Exception as e:
                # Логика обработки ошибок API, как было раньше
                print(f"[GoogleTranslator] Ошибка Google API на попытке {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    print(f"[GoogleTranslator] Ожидание {retry_delay_seconds} секунд перед следующей попыткой после ошибки...")
                    time.sleep(retry_delay_seconds)
                else:
                    print("[GoogleTranslator] Максимальное количество попыток достигнуто с ошибкой API.")
                    # В случае ошибки API после всех попыток, возвращаем None, как раньше
                    return None # Возвращаем None в случае окончательной ошибки API

        # Эта часть кода не должна быть достигнута при правильной работе цикла
        return None # Fallback, на всякий случай

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
    """
    Возвращает отсортированный список моделей с кэшированием.
    Кэш обновляется при первом вызове или по истечении TTL.
    """
    global _cached_models_list, _model_list_last_update

    current_time = time.time()

    # Проверяем, есть ли кэш и не истек ли его срок
    if _cached_models_list is not None and _model_list_last_update is not None and (current_time - _model_list_last_update) < _MODEL_LIST_CACHE_TTL:
        print("[get_models_list] Возвращаем кэшированный список моделей.")
        return _cached_models_list

    print("[get_models_list] Кэш списка моделей отсутствует или устарел. Получаем с API...")
    google_models = []
    openrouter_zero_cost_models = []

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
            if pricing and 'prompt' in pricing and 'completion' in pricing:
                try:
                    prompt_cost = float(pricing['prompt'])
                    completion_cost = float(pricing['completion'])
                    if prompt_cost == 0.0 and completion_cost == 0.0:
                        openrouter_zero_cost_models.append(model)
                except (ValueError, TypeError) as e:
                    print(f"Ошибка парсинга стоимости для модели {model.get('name', model['id'])}: {e}")
                    continue

        print(f"Получено {len(openrouter_zero_cost_models)} моделей с нулевой стоимостью из {len(all_openrouter_models)} от OpenRouter API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей OpenRouter: {e}")

    # Объединяем списки: сначала Google, потом модели OpenRouter с нулевой стоимостью
    # Добавим проверку на случай, если оба списка пусты
    combined_list = google_models + sorted(openrouter_zero_cost_models, key=lambda x: x.get('display_name', '').lower())

    if not combined_list:
        print("[get_models_list] Предупреждение: Не удалось получить список моделей от API.")
        # Можно вернуть пустой список или поднять исключение, в зависимости от желаемого поведения
        # Для начала вернем пустой список, чтобы приложение не падало при старте, если API недоступны
        _cached_models_list = []
        _model_list_last_update = current_time # Кэшируем пустой список, чтобы не спамить API
        return []


    # Кэшируем полученный список
    _cached_models_list = combined_list
    _model_list_last_update = current_time

    print("[get_models_list] Список моделей успешно получен и закэширован.")
    return _cached_models_list

# Функция для принудительной загрузки списка моделей при старте (опционально)
def load_models_on_startup():
    """Принудительно загружает список моделей при старте приложения."""
    print("[startup] Загрузка списка моделей...")
    try:
        get_models_list()
        print("[startup] Список моделей загружен.")
    except Exception as e:
        print(f"[startup] Ошибка при загрузке списка моделей: {e}")


# Модифицируем get_context_length, чтобы использовать кэш и возвращать чистый лимит токенов
def get_context_length(model_name: str) -> int:
    """
    Возвращает лимит входных токенов для данной модели.
    Использует закэшированный список моделей.
    Возвращает дефолтное значение (например, 8000 токенов), если модель не найдена
    или лимит недоступен.
    """
    # Используем закэшированный список напрямую
    models = _cached_models_list

    # Если кэш еще не загружен или пуст, принудительно загружаем
    if models is None or not models:
         # print("[get_context_length] Предупреждение: Кэш моделей не загружен или пуст. Принудительно загружаем.") # Убираем print
         models = get_models_list()

         if not models:
             print(f"[get_context_length] Ошибка: Не удалось загрузить список моделей для '{model_name}'. Используем дефолт токенов.")
             return 8000 # Дефолтное значение в токенах

    for model in models:
        if isinstance(model, dict) and model.get('name') == model_name and 'input_token_limit' in model:
            token_limit = model['input_token_limit']
            if isinstance(token_limit, (int, float)) and token_limit != 'N/A':
                # Теперь возвращаем сам лимит токенов
                return int(token_limit)
            else:
                 print(f"[get_context_length] Предупреждение: Нечисловой лимит токенов '{token_limit}' для модели '{model_name}'. Используем дефолт токенов.")
                 return 8000 # Дефолтное значение в токенах
        elif isinstance(model, dict) and model.get('name') == model_name and 'input_token_limit' not in model:
             print(f"[get_context_length] Предупреждение: Для модели '{model_name}' отсутствует 'input_token_limit'. Используем дефолт токенов.")
             return 8000 # Дефолтное значение в токенах

    print(f"[get_context_length] Предупреждение: Модель '{model_name}' не найдена в закэшированном списке. Используем дефолт токенов.")
    return 8000 # Дефолтное значение в токенах

# --- END OF FILE translation_module.py ---