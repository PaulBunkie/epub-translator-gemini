import translation_module
import os
import re
import traceback
from typing import Optional, List, Dict, Any
import requests # Импорт для выполнения HTTP запросов
import json # Импорт для работы с JSON
import time # Импорт для задержки при ретраях

# Константа для обозначения ошибки лимита контекста
# TODO: Возможно, стоит перенести в класс или конфиг
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

# Константа для обозначения ошибки пустого ответа
EMPTY_RESPONSE_ERROR = "__EMPTY_RESPONSE_AFTER_RETRIES__"

# TODO: Реализовать логику перевода с глоссарием здесь
# Эта функция-заглушка больше не будет использоваться напрямую в translate_text после реализации API вызова
def translate_section_with_glossary_logic(
    section_text: str,
    target_language: str,
    model_name: str,
    glossary: dict | None = None,
    system_instruction: str | None = None,
    user_instruction: str | None = None
) -> str | None:
    """
    Заглушка или базовая реализация логики перевода с глоссарием.
    Пока просто возвращает заглушку или None.
    """
    print(f"[WorkflowTranslateLogic] Вызов логики перевода с глоссарием (заглушка).")
    # return f"[TRANSLATED PLACEHOLDER] {section_text}"
    # Вернем None, чтобы имитировать, что пока нет результата перевода или произошла ошибка
    return None

# --- НОВЫЙ КЛАСС WorkflowTranslator ---
class WorkflowTranslator:
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
    # TODO: Добавить URL для Google API, если будем его реализовывать здесь же

    def __init__(self):
         # Инициализация API ключа OpenRouter
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.openrouter_api_key:
            print("Предупреждение: Переменная окружения OPENROUTER_API_KEY не установлена.")
        # TODO: Добавить инициализацию Google API ключа

    def get_system_instruction(self, operation_type: str, target_language: str) -> str:
        """
        Provides system-level instructions for the model based on the operation type.
        """
        if operation_type == 'translate':
            # Instruct the model to act as a professional translator.
            return (f"You are a professional book translator. Translate the given text into {target_language}. "
                    "Maintain the original tone, style, and formatting. "
                    "Pay close attention to proper nouns, names, and technical terms, and ensure their consistent translation based on any provided glossary or context. "
                    "When adapting content, ensure the translation resonates culturally with the target audience without losing the original meaning or intent.")
        elif operation_type == 'summarize':
            # Instruct the model to act as a summarization engine in the original language.
            return ("You are a summarization engine. Summarize the following text concisely and clearly in the original language. "
                    "Focus especially on:\n"
                    "- Neologisms (newly coined words or expressions),\n"
                    "- Words used in unusual or specific contexts within the book,\n"
                    "- Names of characters, locations, organizations, and unique objects, indicating presumed gender in parentheses for proper nouns.\n"
                    "- Key plot points, themes, and significant events that are crucial for understanding the narrative's flow and context.")
        elif operation_type == 'analyze':
            # Instruct the model to act as a literary analyst for glossary and adaptation guidelines.
            return (f"You are a literary analyst. Your task is to analyze the provided text (which is a summary of a book) "
                    f"and extract a comprehensive glossary of key terms, proper nouns, and their precise {target_language} translations. "
                    "Additionally, you must provide clear, concise adaptation guidelines for a human translator. "
                    "Ensure consistency and accuracy in both outputs. Indicate the presumed gender in parentheses for all proper nouns."
                    "\n\nFormat your response STRICTLY as follows, using the exact markers:\n\n"
                    "---START_GLOSSARY_TABLE---\n"
                    "| Original Term | Translation/Explanation |\n"
                    "|---|---|\n"
                    "| Example Name (gender) | Example Translation/Explanation |\n"
                    "---END_GLOSSARY_TABLE---\n\n"
                    "---START_ADAPTATION_GUIDELINES---\n"
                    "1. Guideline One: ...\n"
                    "2. Guideline Two: ...\n"
                    "---END_ADAPTATION_GUIDELINES---")
        else:
            return "" # Default empty system instruction for unknown operation types

    def _chunk_text(self, text: str, chunk_size_limit_chars: int) -> List[str]:
        """
        Разбивает текст на чанки по приблизительному количеству символов,
        используя переданный chunk_size_limit_chars.
        Этот метод является базовой реализацией, стараясь не разрывать слова.
        """
        if not text:
            return []

        # Используем переданный лимит символов напрямую
        max_chunk_chars = chunk_size_limit_chars

        chunks = []
        current_pos = 0
        text_len = len(text)

        while current_pos < text_len:
            end_pos = min(current_pos + max_chunk_chars, text_len)
            chunk = text[current_pos:end_pos]

            if end_pos < text_len and not text[end_pos].isspace() and text[end_pos-1].isalpha():
                last_space = chunk.rfind(' ')
                if last_space > 0 and (len(chunk) - last_space) < 50:
                    chunk = chunk[:last_space]
                    end_pos = current_pos + len(chunk)

            chunks.append(chunk.strip())
            current_pos = end_pos

        return [c for c in chunks if c]

    def _parse_markdown_table_to_glossary_dict(self, markdown_table_string: str) -> Dict[str, str]:
        """
        Парсит Markdown таблицу в словарь глоссария.
        """
        # Используем регулярное выражение для поиска строк таблицы
        table_rows = re.findall(r'\|(.*?)\|', markdown_table_string)

        # Инициализируем пустой словарь для глоссария
        glossary_dict = {}

        # Проходим по строкам таблицы, начиная со второй (первая - заголовки)
        for row in table_rows[1:]:
            # Разбиваем строку на ячейки
            cells = [cell.strip() for cell in row.split('|')]

            # Проверяем, что у нас есть как минимум две ячейки (термин и перевод)
            if len(cells) >= 2:
                term = cells[0]
                translation = cells[1]

                # Добавляем термин и перевод в словарь
                glossary_dict[term] = translation

        return glossary_dict

    def _build_messages_for_operation(
        self,
        operation_type: str,
        text_to_process: str,
        target_language: str,
        model_name: str | None = None,
        prompt_ext: Optional[str] = None,
        dict_data: dict | None = None
    ) -> List[Dict[str, Any]]:
        messages = []
        system_instruction = self.get_system_instruction(operation_type, target_language)
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        user_content: str = "" # Initialize user_content to an empty string

        if operation_type == 'translate':
            if dict_data and 'glossary_data' in dict_data and dict_data['glossary_data']:
                glossary_table = self._convert_glossary_to_markdown_table(dict_data['glossary_data'])
                user_content = f"Translate the following text into {target_language}. Use the provided glossary for consistency:\n\n{glossary_table}\n\nText to translate:\n{text_to_process}"
            else:
                user_content = f"Translate the following text into {target_language}:\n{text_to_process}"
        elif operation_type == 'summarize':
            user_content = f"Summarize the following text in the original language:\n\n{text_to_process}"
        elif operation_type == 'analyze':
            # For 'analyze' operation, the text_to_process is the collected summary
            # and prompt_ext is used as the primary instruction.
            # user_content should combine prompt_ext with the collected summary.
            user_content = f"{prompt_ext}\n\nAnalyze the following text:\n{text_to_process}"
            # Ensure the structure of the output is strictly followed, as previously discussed.
            user_content += f"\n\n---START_GLOSSARY_TABLE---\n(Your markdown glossary table here)\n---END_GLOSSARY_TABLE---"
            user_content += f"\n\n---START_ADAPTATION_GUIDELINES---\n(Your adaptation guidelines here)\n---END_ADAPTATION_GUIDELINES---"
        else:
            raise ValueError(f"Unknown operation type: {operation_type}")

        if prompt_ext and operation_type != 'analyze': # prompt_ext is integrated differently for 'analyze'
            user_content = f"{prompt_ext}\n\n{user_content}"

        messages.append({"role": "user", "content": user_content})
        return messages

    def _call_model_api(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_retries: int = 3, # Количество попыток
        retry_delay_seconds: int = 5 # Начальная задержка
    ) -> str | None:
        """
        Вызывает API модели (Google или OpenRouter) с заданным списком сообщений.
        Реализует логику вызова OpenRouter API.

        Args:
            model_name: Имя модели для вызова.
            messages: Список сообщений в формате [{"role": "...", "content": "..."}, ...].
            temperature: Параметр temperature для API вызова.
            max_retries: Максимальное количество попыток вызова при ошибках.
            retry_delay_seconds: Начальная задержка между попытками.

        Returns:
            Текст ответа модели или специальные константы ошибок/None.
        """
        print(f"[WorkflowTranslator] Вызов API для модели: '{model_name}'.")

        # TODO: Добавить логику выбора API (Google/OpenRouter) на основе model_name
        # Для начала реализуем только OpenRouter
        api_type = "openrouter" # Пока только OpenRouter
        # TODO: Определить api_type на основе model_name или другого признака

        if api_type == "openrouter":
            if not self.openrouter_api_key:
                 print("[WorkflowTranslator] ОШИБКА: OPENROUTER_API_KEY не установлен.")
                 return None # Или специальная ошибка конфигурации

            url = f"{self.OPENROUTER_API_URL}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:5000"), # Требуется openrouter.ai
                # "X-Title": os.getenv("YOUR_APP_NAME", "EPUB Translator"), # Optional: Replace with your app name
            }

            # TODO: Добавить обработку max_tokens, streaming и других параметров API
            data = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                # "max_tokens": 4000, # Определить адекватное значение или убрать
            }

            current_delay = retry_delay_seconds
            for attempt in range(max_retries):
                try:
                    print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries}). URL: {url}")
                    response = requests.post(url, headers=headers, data=json.dumps(data))
                    print(f"[OpenRouterTranslator] Получен ответ от OpenRouter: Статус {response.status_code}")

                    # --- Проверка заголовков лимитов (опционально) ---
                    if 'X-Ratelimit-Remaining' in response.headers:
                        print(f"[OpenRouterTranslator] X-Ratelimit-Remaining: {response.headers['X-Ratelimit-Remaining']}")
                    # ... другие заголовки лимитов ...

                    # --- Обработка успешного ответа ---
                    if response.status_code == 200:
                        response_json = response.json()
                        # Проверка наличия 'choices' и 'message'
                        if response_json and 'choices' in response_json and response_json['choices']:
                             # Извлекаем контент из первого сообщения
                             if 'message' in response_json['choices'][0] and 'content' in response_json['choices'][0]['message']:
                                 print("[OpenRouterTranslator] Ответ получен в формате message.content. Успех.")
                                 return response_json['choices'][0]['message']['content'].strip()
                             elif 'text' in response_json['choices'][0]: # Для старых моделей или других форматов
                                  print("[OpenRouterTranslator] Ответ получен в формате text. Успех.")
                                  return response_json['choices'][0]['text'].strip()
                             else:
                                 print("[OpenRouterTranslator] Ошибка: Неверный формат ответа от API (отсутствует content или text).")
                                 print(f"Ответ API: {response_json}")
                                 return None # Или специальная ошибка формата ответа
                        else:
                            print("[OpenRouterTranslator] Ошибка: Неверный формат ответа от API (отсутствуют choices).")
                            print(f"Ответ API: {response_json}")
                            return None # Или специальная ошибка формата ответа

                    # --- Обработка ошибок, требующих ретрая ---
                    elif response.status_code == 429: # Too Many Requests
                        print(f"[OpenRouterTranslator] Ошибка 429 (Too Many Requests). Повторная попытка через {current_delay} сек...")
                        # Check for Retry-After header
                        retry_after = response.headers.get('Retry-After')
                        if retry_after:
                            try:
                                # Используем значение из заголовка, если оно есть и корректно
                                current_delay = int(retry_after)
                                print(f"[OpenRouterTranslator] Используется задержка из Retry-After: {current_delay} сек.")
                            except ValueError:
                                pass # Оставляем текущую задержку, если заголовок некорректен

                        if attempt < max_retries - 1:
                            time.sleep(current_delay)
                            current_delay *= 2 # Экспоненциальное увеличение задержки
                            continue # Перейти к следующей попытке
                        else:
                            print("[OpenRouterTranslator] Максимальное количество попыток 429 исчерпано.")
                            return None # Или специальная ошибка лимита запросов

                    # --- Обработка других ошибок API (не ретраим по умолчанию) ---
                    elif response.status_code >= 400:
                        print(f"[OpenRouterTranslator] Ошибка API: Статус {response.status_code}")
                        try:
                            error_details = response.json()
                            print(f"[OpenRouterTranslator] Детали ошибки: {error_details}")
                            # Проверка на ошибку контекстного лимита по содержимому ответа
                            if isinstance(error_details, dict) and 'error' in error_details and 'message' in error_details['error']:
                                if "context window" in error_details['error']['message'].lower():
                                    print("[OpenRouterTranslator] Обнаружена ошибка контекстного лимита.")
                                    return CONTEXT_LIMIT_ERROR
                            elif isinstance(response.text, str) and "context window" in response.text.lower():
                                 print("[OpenRouterTranslator] Обнаружена ошибка контекстного лимита в тексте ответа.")
                                 return CONTEXT_LIMIT_ERROR


                        except json.JSONDecodeError:
                            print("[OpenRouterTranslator] Ошибка API: Не удалось декодировать JSON ответа с ошибкой.")
                            print(f"Текст ответа: {response.text}")


                        return None # Возвращаем None для других ошибок API

                except requests.exceptions.RequestException as e:
                    # Обработка ошибок сети, таймаутов и т.п.
                    print(f"[OpenRouterTranslator] Ошибка запроса к API на попытке {attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                         print(f"[OpenRouterTranslator] Повторная попытка через {current_delay} сек...")
                         time.sleep(current_delay)
                         current_delay *= 2  # Экспоненциальное увеличение задержки
                         continue # Перейти к следующей попытке
                    else:
                        print("[OpenRouterTranslator] Максимальное количество попыток запроса исчерпано.")
                        return None # Возвращаем None при неустранимой ошибке запроса

                except Exception as e:
                     # Обработка любых других непредвиденных ошибок
                     print(f"[OpenRouterTranslator] Непредвиденная ошибка при вызове API: {e}")
                     traceback.print_exc()
                     return None # Возвращаем None при непредвиденной ошибке

            print("[OpenRouterTranslator] Не удалось получить успешный ответ от OpenRouter API после всех попыток.")
            return None # Возвращаем None, если все попытки исчерпаны

        # TODO: Добавить логику для Google API вызова здесь

        else:
            # Неизвестный тип API
            print(f"[WorkflowTranslator] ОШИБКА: Неизвестный тип API для модели: '{model_name}'.")
            return None # Или специальная ошибка неизвестного API

    def translate_text(
        self,
        text_to_translate: str,
        target_language: str = "russian",
        model_name: str = None,
        prompt_ext: Optional[str] = None,
        operation_type: str = 'translate',
        dict_data: dict | None = None
    ) -> str | None:
        """
        Основной метод для обработки текста в зависимости от operation_type.
        Использует _build_messages_for_operation для создания промпта
        и _call_model_api для взаимодействия с моделью.
        """
        CHUNK_SIZE_LIMIT_CHARS = 0 # Инициализируем переменную для определения лимита чанка

        # Определение лимита чанка в зависимости от типа операции.
        # Поскольку _get_context_length УДАЛЕНА, для summarize/analyze используем фиксированный большой лимит.
        if operation_type == 'translate':
            CHUNK_SIZE_LIMIT_CHARS = 20000
            print("[WorkflowTranslator] Использован фиксированный лимит для перевода.")
        elif operation_type in ['summarize', 'analyze']:
            # Внимание: здесь мы больше НЕ используем _get_context_length.
            # Если для summarize/analyze требуется динамическое определение лимита из API,
            # нужно будет перенести сюда весь блок get_context_length из translation_module.py
            # вместе со вспомогательными функциями (_cached_models_list, get_models_list).
            # Пока используем большой фиксированный лимит, чтобы избежать поломки.
            CHUNK_SIZE_LIMIT_CHARS = 60000 # Достаточно большой лимит для большинства summarize/analyze запросов
            print(f"[WorkflowTranslator] Для '{operation_type}' использован большой фиксированный лимит ({CHUNK_SIZE_LIMIT_CHARS} символов) вместо динамического.")
        else:
            CHUNK_SIZE_LIMIT_CHARS = 20000 # Дефолтное значение для неизвестных операций
            print(f"[WorkflowTranslator] Предупреждение: Неизвестный тип операции '{operation_type}'. Используется дефолтный лимит чанка.")

        # Далее логика для summarize/analyze/translate.
        # Для summarize и analyze мы сейчас не чанкуем текст здесь, а передаем его целиком в _build_messages_for_operation.
        # CHUNK_SIZE_LIMIT_CHARS, определенный выше, будет использоваться только для "translate".

        if operation_type == 'summarize' or operation_type == 'analyze':
            messages = self._build_messages_for_operation(
                operation_type,
                text_to_translate, # Передаем полный текст
                target_language, 
                model_name=model_name,
                prompt_ext=prompt_ext,
                dict_data=dict_data
            )
            return self._call_model_api(model_name, messages)

        elif operation_type == 'translate':
            print(f"[WorkflowTranslator] Вызов операции 'translate' для текста длиной {len(text_to_translate)} символов.")

            translated_chunks = []
            
            # Чанкирование текста
            # Передаем CHUNK_SIZE_LIMIT_CHARS, который уже был определен выше
            chunks = self._chunk_text(text_to_translate, CHUNK_SIZE_LIMIT_CHARS)
            
            if not chunks:
                print("[WorkflowTranslator] Нет чанков для перевода.")
                return None

            print(f"[WorkflowTranslator] Текст разбит на {len(chunks)} чанков с лимитом {CHUNK_SIZE_LIMIT_CHARS} символов.")

            # Обработка каждого чанка
            for i, chunk in enumerate(chunks):
                print(f"[WorkflowTranslator] Перевод чанка {i+1}/{len(chunks)} (длина: {len(chunk)} симв).")
                messages = self._build_messages_for_operation(
                    operation_type,
                    chunk, # Передаем сам чанк
                    target_language,
                    model_name=model_name,
                    prompt_ext=prompt_ext, 
                    dict_data=dict_data 
                )
                
                translated_chunk = self._call_model_api(model_name, messages)
                
                if translated_chunk is None:
                    print(f"[WorkflowTranslator] Ошибка перевода чанка {i+1}. Прерывание.")
                    return None 
                
                translated_chunks.append(translated_chunk)
            
            full_translated_text = "".join(translated_chunks)
            print(f"[WorkflowTranslator] Перевод завершен. Общая длина: {len(full_translated_text)} симв.")
            return full_translated_text
        else:
            print(f"[WorkflowTranslator] Неизвестный тип операции: {operation_type}")
            return None 

# --- ПУБЛИЧНАЯ ФУНКЦИЯ, КОТОРАЯ ВЫЗЫВАЕТ МЕТОД КЛАССА ---
def translate_text(
    text_to_translate: str,
    target_language: str = "russian",
    model_name: str = None,
    prompt_ext: Optional[str] = None,
    operation_type: str = 'translate',
    dict_data: dict | None = None # !!! ИЗМЕНЕНО: workflow_data -> dict_data !!!
) -> str | None:
    """
    Публичная точка входа для перевода/обработки в workflow.
    Создает экземпляр WorkflowTranslator и вызывает его метод translate_text.
    """
    print(f"[WorkflowModule] Вызов публичной translate_text. Операция: '{operation_type}'")
    translator = WorkflowTranslator()
    # Передаем новый необязательный параметр dict_data в метод класса
    return translator.translate_text(
        text_to_translate=text_to_translate,
        target_language=target_language,
        model_name=model_name,
        prompt_ext=prompt_ext,
        operation_type=operation_type,
        dict_data=dict_data # !!! Передаем dict_data дальше !!!
    )

# TODO: Возможно, потребуется реализовать другие функции, аналогичные translation_module,
# например, get_models_list, load_models_on_startup, configure_api,
# если workflow_processor или другие части workflow их используют напрямую.
# На текущий момент, workflow_processor, кажется, вызывает только translate_text.
# Если другие части используют их напрямую, их нужно будет проксировать через этот модуль тоже.

# TODO: get_context_length может понадобиться для логики чанкинга.
# Либо скопировать его сюда, либо вызывать из оригинального translation_module
# (если он публичный или мы его импортировали как translation_module_original)

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
{translation_guidelines_section}
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
    'analyze': """You are an expert literary terminology analyst and cross-cultural adaptation specialist working with professional translators.
Your task is to thoroughly analyze the provided text, identify its cultural context, key stylistic features, and crucial terms.

Your output MUST be structured into exactly two main sections, separated by a unique marker.

---START_GLOSSARY_TABLE---

**SECTION 1: GLOSSARY TABLE**
This section must contain a clean, valid Markdown table with specific terms and their translations.
- Only extract terms that are clearly and unambiguously mentioned in the text and require consistent translation.
- Never assume, invent or guess meanings, genders, or translations.
- Prioritize clarity, grammatical precision, and usefulness for automated and human translation.

Your table MUST use the following columns: `Term | Type | Gender | Translation | Comment`
- **Term**: The original form exactly as it appears in the text.
- **Type**: One of: `Character`, `Organization`, `Abbreviation`, `Job Title`, `Technology`, `Neologism`, `Cultural Term`, `Other`.
- **Gender**: For characters: `m`, `f`, or `—` if not clear. For nouns: grammatical gender in target language (e.g., `м`, `ж`, `ср` for Russian), or `—` if not applicable.
- **Translation**: A single suggested translation into {target_language}.
- **Comment**: A short explanation for context or difficulty. If the term's gender is known, mention why (e.g., "Referred to as 'he'"). If it's an abbreviation, note whether it's explained or needs expansion. If it's ambiguous or culturally loaded, explain that.

Do not include common words or phrases that don't require glossary treatment.
Avoid adding common country or city names unless there is a translation ambiguity or special context.
Do not guess meanings or add speculative terms. Use only direct evidence from the text.
Prefer fewer high-quality entries over too many weak or generic ones.

---END_GLOSSARY_TABLE---
---START_ADAPTATION_GUIDELINES---

**SECTION 2: ADAPTATION GUIDELINES**
This section must contain practical recommendations for a professional translator adapting this book for a {target_language}-speaking audience.
- Highlight aspects requiring special attention and explain *why* they are important.
- Focus on analysis and recommendations, NOT on translating or summarizing the text itself.
- Be concrete and actionable.

Structure your recommendations using the following sub-headings, with bullet points or short paragraphs under each:

### Style and Presentation
- Tone (e.g., ironic, dramatic, scientific).
- Rhythm and pace (e.g., fast, measured, disjointed).
- Overall narrative style and author's voice.
- Formatting peculiarities if stylistically relevant.

### Lexicon and Linguistic Peculiarities
- Specific vocabulary, dialectisms, slang, jargon (e.g., military, scientific, subcultural).
- Neologisms, invented words, unique compound words (if not already in glossary).
- Archaic or outdated vocabulary.
- Word usage peculiarities, idioms, figures of speech difficult for direct translation.

### Cultural Context and Allusions
- Historical, cultural, mythological, literary, or biblical allusions and parallels.
- Unique traditions, customs, social norms unfamiliar to the target audience.
- Geographical or social realities requiring explanation or adaptation.

### Technologies and Concepts
- Descriptions of specific or fictional technologies, devices.
- Unique philosophical, scientific, or speculative concepts.

---END_ADAPTATION_GUIDELINES---

{prompt_ext_section}

Text to Analyze:
{text}

Final Answer:
"""
}