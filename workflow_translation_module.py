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

# User-provided PROMPT_TEMPLATES - EXACTLY AS PROVIDED
PROMPT_TEMPLATES = {
    'translate': f"""You are a professional literary translator working with books. Your goal is to provide a high-quality, natural-sounding translation into {{target_language}}, following these principles:

- Translate with full preservation of the author's tone, rhythm, and stylistic intent.
- Maintain consistency in names, terminology, and gender assignments within the entire response.
- Do not soften explicit, profane, or emotional language unless a cultural or contextual reason requires it.
- Translate standard abbreviations using well-established equivalents in {{target_language}}.
- Leave fictional or uncommon abbreviations (e.g., invented acronyms or alien names) unchanged.
- For neologisms, coined expressions, or wordplay, select a well-adapted and stylistically appropriate equivalent in {{target_language}} and use it consistently.
- Keep all Markdown formatting elements untouched: headings, bold, italic, inline code, bullet points, links, etc.

{{russian_dialogue_rule}}

If clarification is needed for the reader (such as cultural references, wordplay, or untranslatable elements), add a translator's footnote:
- Use superscript numbers (¹,²,³) directly after the relevant word/phrase. If superscript is unavailable, use square brackets ([1], [2], [3]).
- Add a separator (`---`) and a heading (`{{translator_notes_heading}}`) at the end.
- List footnotes in order with clear, brief explanations.

Use footnotes only when truly necessary.

{{prompt_ext_section}}
{{translation_guidelines_section}}
{{previous_context_section}}

Text to Process:
{{text}}

Result:""",

    'summarize': f"""You are a professional summarization engine.

Your goal is to produce a clear and concise summary of the provided text in its original language, capturing the essential points and tone.

Focus especially on:
- Unique names (characters, locations, organizations)
- Important events or turning points
- Recurring terminology, neologisms, or invented concepts
- Thematic or stylistic elements relevant to narrative comprehension

Do not include introduction, meta-comments, or conclusions outside the summary itself.

{{prompt_ext_section}}

Text to Summarize:
{{text}}

Summary:""",

    'analyze': f"""You are a literary analyst and terminology specialist assisting professional translators.

Your task is to analyze the provided text and extract:
- A glossary of key terms with accurate and consistent translations into {{target_language}}
- A short cultural and stylistic adaptation guide

Your response must include **two sections**, marked as follows:

---START_GLOSSARY_TABLE---

**SECTION 1: GLOSSARY TABLE**

Format as a valid Markdown table:

`Term | Type | Gender | Translation | Comment`

Definitions:
- **Term**: As written in the source text.
- **Type**: Define the type of the term (Character, Title, Location, Organization, Abbreviation, Neologism, Cultural Term, Technology, etc.).
- **Gender**:
  For character names in languages with grammatical gender and no natural support for non-binary constructions (e.g., Russian):
  If a character's gender is not explicitly stated, you must assign a binary gender (m or f) for grammatical purposes. Follow these steps:
    First, look for clear contextual indicators: perceived gender of names, titles, pronoun use, relational roles (e.g., "brother," "mother"), or descriptive traits.
    If no reliable indicators exist, default to masculine (m) to preserve grammatical consistency and avoid disrupting the flow of the target language.
  Do not use "—" (unspecified) unless the character is explicitly presented as non-gendered, such as an AI, animal, or abstract entity. This rule applies only to fictional characters or people — not to objects, organizations, or abstract nouns.
- **Translation**: Precise term in {{target_language}}, or `[keep original]` if it should remain unchanged.
- **Comment**: Use only when clarification is needed (e.g. ambiguity, invented term, pun, or stylistic choice).

- **You MUST include all named characters from the text.** Also, include all named locations and organizations. Additionally, include other terms that are contextually important or potentially ambiguous.

Do not include:
- Generic words or everyday vocabulary
- Common place names, unless they are ambiguous or recontextualized
- Anything not present in the source text

---END_GLOSSARY_TABLE---

---START_ADAPTATION_OVERVIEW---

**SECTION 2: ADAPTATION OVERVIEW**

Write a concise guide to help a human translator handle this text accurately and naturally in {{target_language}}.

Include:
- Narrative style and tone
- Use of idioms, slang, dialects, or specialized jargon
- Fictional or cultural references that may require adaptation
- Unusual structures, non-standard grammar, or typographic elements
- Any recurring challenges or stylistic features that must be preserved

Do not suggest specific translations. Focus on highlighting areas that require attention, with reasoning.

---END_ADAPTATION_OVERVIEW---

{{prompt_ext_section}}

Text to Analyze:
{{text}}

Final Answer:
"""
}

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
            return (
                f"You are a professional literary translator. Your task is to translate the given text into {target_language} "
                "with high fidelity, preserving the original tone, voice, and stylistic nuance. "
                "Ensure consistency in the rendering of names, terms, and grammatical gender. "
                "Preserve formatting, punctuation, and structure, including any markup or annotation. "
                "Adapt culturally sensitive references only when required for clarity or natural flow in the target language. "
                "Use provided context or glossary if available; do not invent information."
            )

        elif operation_type == 'summarize':
            return (
                "You are a high-precision summarization engine. Your task is to produce a clear, concise summary "
                "of the given text in its original language. "
                "Focus on preserving essential information, including:\n"
                "- Neologisms and coined terms\n"
                "- Character names, locations, and organizations\n"
                "- Key plot points and thematic elements\n"
                "Avoid interpretation, commentary, or stylistic rewriting."
            )

        elif operation_type == 'analyze':
            return (
                f"You are a literary analyst and terminology specialist. Your task is to analyze the provided text "
                f"and produce two outputs:\n"
                "- A glossary table with key terms and their translations into {target_language}\n"
                "- An overview of stylistic and cultural adaptation considerations for professional translators in {target_language}\n"
                "Maintain precision, avoid speculation, and base all output strictly on the source text.\n\n"
                "Your response must include exactly two sections using the following markers:\n"
                "---START_GLOSSARY_TABLE---\n"
                "---END_GLOSSARY_TABLE---\n\n"
                "---START_ADAPTATION_OVERVIEW---\n"
                "---END_ADAPTATION_OVERVIEW---"
            )

        else:
            return ""

    def _convert_glossary_to_markdown_table(self, glossary_data: List[Dict]) -> str:
        """
        Converts a list of glossary dictionaries into a Markdown table string.
        Assumes each dict has 'Term', 'Type', 'Gender', 'Translation', 'Comment' keys.
        """
        if not glossary_data:
            return "No glossary terms provided."

        headers = ["Term", "Type", "Gender", "Translation", "Comment"]
        table_lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]

        for item in glossary_data:
            row = [
                item.get('Term', ''),
                item.get('Type', ''),
                item.get('Gender', ''),
                item.get('Translation', ''),
                item.get('Comment', '')
            ]
            # Escape pipes within content to avoid breaking markdown table
            row = [cell.replace('|', '\\|') for cell in row]
            table_lines.append(" | ".join(row))
        return "\n".join(table_lines)

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

    def _clean_text_for_api(self, text: str) -> str:
        """
        Очищает текст от проблемных символов и форматирования, которые могут испортить JSON.
        """
        if not text:
            return ""
        
        # Заменяем управляющие символы
        text = text.replace('\x00', '')  # Null byte
        text = text.replace('\x1a', '')  # EOF
        text = text.replace('\x1b', '')  # Escape
        
        # Заменяем проблемные кавычки
        text = text.replace('"', '"').replace('"', '"')  # Умные кавычки на обычные
        text = text.replace(''', "'").replace(''', "'")  # Умные апострофы на обычные
        
        # Заменяем переносы строк на \n
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Удаляем BOM и другие невидимые символы
        text = text.encode('utf-8', 'ignore').decode('utf-8')
        
        return text

    def _build_messages_for_operation(
        self,
        operation_type: str,
        text_to_process: str,
        target_language: str,
        model_name: str | None = None,
        prompt_ext: Optional[str] = None,
        dict_data: dict | None = None,
        previous_context: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        messages = []
        system_instruction = self.get_system_instruction(operation_type, target_language)
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        user_content: str = "" # Initialize user_content to an empty string

        # Очищаем текст перед использованием
        cleaned_text = self._clean_text_for_api(text_to_process)

        # Prepare dynamic parts for the templates based on operation type
        formatted_vars = {
            'target_language': target_language,
            'text': cleaned_text,
            'prompt_ext_section': f"Additional instructions: {prompt_ext}" if prompt_ext else "",
        }

        if operation_type == 'translate':
            formatted_vars['russian_dialogue_rule'] = "For dialogue in Russian, use appropriate punctuation for direct speech (e.g., em dash for conversational breaks)." if target_language.lower() == "russian" else ""
            formatted_vars['translator_notes_heading'] = "Примечания переводчика"

            # Handle translation_guidelines_section for glossary within translate
            translation_guidelines_section = ""
            if dict_data and 'glossary_data' in dict_data and dict_data['glossary_data']:
                glossary_table = self._convert_glossary_to_markdown_table(dict_data['glossary_data'])
                translation_guidelines_section = f"---START_GLOSSARY_FOR_TRANSLATION---\n" \
                                                 f"Use the provided glossary for consistency:\n\n{glossary_table}\n" \
                                                 f"---END_GLOSSARY_FOR_TRANSLATION---"
            formatted_vars['translation_guidelines_section'] = translation_guidelines_section
            
            # Handle previous_context_section for translate
            formatted_vars['previous_context_section'] = f"Previous context (for continuity):\n{previous_context}" if previous_context else ""

            user_content = PROMPT_TEMPLATES['translate'].format(**formatted_vars)
            
        elif operation_type == 'summarize':
            # ИЗМЕНЕНО: Используем общий шаблон PROMPT_TEMPLATES['summarize'] и форматируем его
            user_content = PROMPT_TEMPLATES['summarize'].format(**formatted_vars)
            
        elif operation_type == 'analyze':
            user_content = PROMPT_TEMPLATES['analyze'].format(**formatted_vars)
            
        else:
            raise ValueError(f"Unknown operation type: {operation_type}")

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
                "reasoning": {
                    "exclude": True
                }
                # "max_tokens": 4000, # Определить адекватное значение или убрать
            }

            current_delay = retry_delay_seconds
            for attempt in range(max_retries):
                try:
                    print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries}). URL: {url}")
                    try:
                        json_str = json.dumps(data, ensure_ascii=False)
                        # Проверяем, что JSON валидный
                        json.loads(json_str)
                        response = requests.post(url, headers=headers, data=json_str)
                    except Exception as e:
                        print(f"[OpenRouterTranslator] Ошибка при формировании запроса: {e}")
                        return None
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
