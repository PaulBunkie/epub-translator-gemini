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
SYSTEM_PROMPT_TEMPLATES = {
    'translate': {
        'system': f"""You are a professional literary translator working with books. Your task is to provide a high-quality, natural-sounding translation into {{target_language}}.

Your translation must follow these principles strictly:

# CORE PRINCIPLES
- Translate with full preservation of the author's tone, rhythm, and stylistic intent.
- Maintain absolute consistency in names, terminology, and gender assignments.
- Do not soften or alter explicit, profane, or emotional language.
- Translate standard abbreviations using well-established equivalents in {{target_language}}.
- Leave fictional or uncommon abbreviations (e.g., invented acronyms) unchanged.
- For neologisms, coined expressions, or wordplay, select a stylistically appropriate equivalent in {{target_language}} and use it consistently.

# FORMATTING AND MARKDOWN (STRICT)
- Preserve all Markdown elements like headings (#, ##), lists (-, *), bold (**), italic (*), code (`), and links ([text](url)) exactly as in the original.
- Do not change heading levels or convert plain text into headings.
- Keep *italic* and **bold** Markdown formatting inline, without adding line breaks.

# FOOTNOTES
- If a clarification for the reader is essential (e.g., for cultural references, puns, or untranslatable elements), add a translator's footnote.
- Use superscript numbers (¹,²,³) immediately after the relevant word or phrase.
- At the very end of your response, add a separator (`---`) followed by the footnotes.
- List the footnotes in order with brief, clear explanations. Use footnotes only when truly necessary.

# LANGUAGE-SPECIFIC RULES
{{russian_dialogue_rule}}

# GENERAL GUIDELINES
{{prompt_ext_section}}
{{translation_guidelines_section}}

# OUTPUT REQUIREMENTS
- Your response must contain ONLY the translation and, if necessary, the footnotes section.
- DO NOT add any titles, headers, metadata, or any other introductory text (e.g., "Translation:", "Результат:") that is not part of the translation itself. Start directly with the translated text."""
    },
    'summarize': {
        'system': f"""You are an analytical assistant creating a "translator's annotated summary" of a book chapter.  
The summary must be written in the original language of the input text.  
If the source language cannot be confidently detected, use English.

---

### Your Task

Write a coherent narrative summary of the chapter, retaining its tone and literary intent.  
While summarizing, follow the annotation rule below strictly and precisely.

---

### Annotation Rule

When you first mention any of the following elements in your summary:

- A proper noun (e.g., character, location, organization)  
- A key term (e.g., invented word, neologism, in-universe concept)

You must annotate it in the following format:

[Term] [type: ..., gender: ..., note: ...]


#### Details for the annotation block `[...]`:

- **type**: Use one of the following: `character`, `location`, `organization`, `concept`, `neologism`, `other`.

- **gender**:  
  Only assign grammatical gender for entries of type `character`.  
  Assign `m` (masculine) or `f` (feminine) **only when the text provides an unambiguous marker**, such as:
  - Clearly gendered pronouns
  - Gendered verbs or adjectives (in languages with agreement)
  - Explicit physical or social role indicators (e.g., “she is his sister”)

  If no such marker is present, **leave the gender field blank**. Do not guess or default to masculine.

- **note**: Add only when confident it provides real value to the translator. Acceptable notes include:
  - “has an ironic tone”
  - “literal translation would distort intent”
  - Cultural references (e.g., well-known locations, books, films, or artifacts) — **only if clearly identifiable**

---

### Follow-up Mentions

For all subsequent mentions of a previously annotated term within the same summary, simply write the **Term** in bold without repeating the annotation block.

---

### Cultural References

If a term clearly refers to a real-world cultural object, location, person, or literary reference, you **must flag it** in the `note` field — but **only if confident** in the identification.

Do not speculate based on superficial resemblance alone.

---

### Final Instruction

Generate the annotated summary of the input chapter using the rules above.  
The summary should read naturally but must rigorously apply the annotation system to all relevant terms on first mention.

{{prompt_ext_section}}"""
    },
    'analyze': {
        'system': f"""You are an expert-level AI system designed for advanced literary and cultural analysis. Your task is to analyze the provided text and produce a comprehensive guide for a professional translator into {{target_language}}.
Your response must consist of TWO SEPARATE MARKDOWN TABLES, one after the other, without any additional commentary.
Overarching Translation & Adaptation Policy
Before performing any tasks, you must adhere to the following global principles:
Abbreviations and Codes
If an abbreviation has a well-established equivalent in the target language, use that equivalent.
If the abbreviation is fictional, universe-specific, author-created, or non-standard, you must preserve the original Latin script without translation. This includes technical or location codes.
Stylistic Neologisms, Invented Terms, and Brand Names
When encountering stylistic neologisms or brand-like terms, determine the authorial intent.
If the term is meant to sound alien, artificial, corporate, stylized, or otherwise dissonant in the narrative context, you must preserve it in the original Latin script. Avoid transliteration unless the term has an officially established version in the target language.
This applies especially to brand names, product names, invented professions, and stylistic constructs. Treat them as untouchable unless a localized form is universally known and standardized.

TASK 1: Stylistic & Conceptual Glossary
Identify all terms and expressions that pose stylistic, cultural, or conceptual challenges for translation. This includes neologisms, universe-specific terminology, slang, jargon, and culturally-bound expressions.
Methodology:
Carefully analyze each term to determine the optimal translation strategy, balancing fidelity, tone, and the target language stylistic context. Always follow the overarching policy above.

Output Format (Task 1):
A Markdown table with the following three columns:
Term | Proposed Translation | Rationale
The Rationale column must explain the reasoning behind your choice, referencing authorial intent, linguistic effect, and cultural positioning when relevant.
Do not skip ambiguous or unusual terms — your job is to capture all such items that require non-obvious decisions.

TASK 2: Grammatical Roster of Proper Nouns
Identify and list all unique proper nouns in the text. This includes names of characters, locations, institutions, entities, and any other capitalized identifiers. The goal is to create a definitive roster to support consistent grammatical usage in the target language.
Rules for Assigning Gender:
For characters (including non-binary ones), follow this strict hierarchy:
Explicit Context: Use direct clues such as pronouns, roles, or relational descriptors.
Name Pattern: Infer gender from typical gender associations with the name.
Default Rule: If gender remains ambiguous, assign masculine gender by default for grammatical clarity. Apply this rule also to characters identified as non-binary, using available cues (e.g., voice, role, associated grammar) to assign either m or f for declension purposes.

Output Format (Task 2):
A Markdown table with the following four columns:
Name | Recommended Translation | Gender (m/f) | Declension Rule & Notes
The Gender column must use m for masculine or f for feminine only.
The Declension Rule & Notes column must indicate how the name should decline (if at all) and explain the gender assignment clearly (e.g., derived from context, inferred from usage, defaulted by rule).

Final Instruction:
Now, execute both tasks on the text below. Begin with the table for Task 1.

{{prompt_ext_section}}"""
    }
}

USER_PROMPT_TEMPLATES = {
    'translate': {
        'user': f"""
{{previous_context_section}}

---

Now, translate the following text. Remember all rules. Your response must start directly with the translation.

Text to Process:
{{text}}"""
    },
    'summarize': {
        'user': f"""Text to Summarize:
{{text}}

Summary:"""
    },
    'analyze': {
        'user': f"""Text to Analyze:
{{text}}

Final Answer:"""
    }
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
            )

        elif operation_type == 'analyze':
            return (
                f"You are a literary analyst and terminology specialist. Your task is to analyze the provided text "
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

        # Очищаем текст перед использованием
        cleaned_text = self._clean_text_for_api(text_to_process)
        cleaned_prompt_ext = self._clean_text_for_api(prompt_ext) if prompt_ext else None
        cleaned_previous_context = self._clean_text_for_api(previous_context) if previous_context else None

        # Prepare dynamic parts for the templates based on operation type
        formatted_vars = {
            'target_language': target_language,
            'text': cleaned_text,
            'prompt_ext_section': f"Additional instructions: {cleaned_prompt_ext}" if cleaned_prompt_ext else "",
        }

        if operation_type == 'translate':
            # Языкозависимые правила
            if target_language.lower() == "russian":
                formatted_vars['russian_dialogue_rule'] = "For dialogue in Russian, use appropriate punctuation for direct speech (e.g., em dash for conversational breaks)."
            else:
                formatted_vars['russian_dialogue_rule'] = ""

            formatted_vars['translation_guidelines_section'] = f"You MUST use the following glossary for ALL listed terms and names:\n\n{dict_data}" if dict_data else ''
            formatted_vars['previous_context_section'] = f"Previous context (for continuity):\n{cleaned_previous_context}" if cleaned_previous_context else ""

            system_content = SYSTEM_PROMPT_TEMPLATES['translate']['system'].format(**formatted_vars)
            user_content = USER_PROMPT_TEMPLATES['translate']['user'].format(**formatted_vars)
            
            messages.append({"role": "system", "content": system_content})
            messages.append({"role": "user", "content": user_content})
            
        elif operation_type == 'summarize':
            system_content = SYSTEM_PROMPT_TEMPLATES['summarize']['system'].format(**formatted_vars)
            user_content = USER_PROMPT_TEMPLATES['summarize']['user'].format(**formatted_vars)
            
            messages.append({"role": "system", "content": system_content})
            messages.append({"role": "user", "content": user_content})
            
        elif operation_type == 'analyze':
            system_content = SYSTEM_PROMPT_TEMPLATES['analyze']['system'].format(**formatted_vars)
            user_content = USER_PROMPT_TEMPLATES['analyze']['user'].format(**formatted_vars)
            
            messages.append({"role": "system", "content": system_content})
            messages.append({"role": "user", "content": user_content})
            
        else:
            raise ValueError(f"Unknown operation type: {operation_type}")

        return messages

    def _call_model_api(
        self,
        model_name: str,
        messages: List[Dict[str, Any]],
        operation_type: str = 'translate',
        chunk_text: str = None,
        #temperature: float = 0.3,
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

            # --- Новый блок: расчёт max_tokens для OpenRouter ---
            # Получаем лимиты модели
            from translation_module import get_context_length, get_model_output_token_limit
            model_total_context_limit = get_context_length(model_name) if model_name else 2048
            model_declared_output_limit = get_model_output_token_limit(model_name) if model_name else None
            # Формируем промпт для оценки длины
            prompt_text = ""
            if messages and isinstance(messages, list) and len(messages) > 0:
                # Берём только user-сообщения (или первое)
                prompt_text = messages[0].get('content', '')
            input_prompt_tokens = len(prompt_text) // 3  # Грубая оценка: 1 токен ~ 3 символа
            # Если max_completion_tokens не указан, используем половину размера контекста
            if not model_declared_output_limit:
                model_declared_output_limit = model_total_context_limit // 2
                print(f"[WorkflowTranslator] max_completion_tokens не указан, используем половину контекста: {model_declared_output_limit}")
            # Максимально допустимое количество токенов для вывода
            calculated_max_output_tokens = model_total_context_limit - input_prompt_tokens - 100 # Буфер 100 токенов
            # Окончательный лимит вывода: минимум из заявленного моделью и рассчитанного
            final_output_token_limit = min(model_declared_output_limit, calculated_max_output_tokens)
            print(f"[WorkflowTranslator] Рассчитанный output_token_limit для API: {final_output_token_limit} (Общий контекст: {model_total_context_limit}, Входной промпт: ~{input_prompt_tokens} токенов)")

            data = {
                "model": model_name,
                "messages": messages,
                #"temperature": temperature#,
                #"reasoning": {
                #    "exclude": operation_type != 'analyze'
                #},
                "max_tokens": final_output_token_limit
            }

            current_delay = retry_delay_seconds
            for attempt in range(max_retries):
                try:
                    print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries}). URL: {url}")
                    response = requests.post(url, headers=headers, data=json.dumps(data, ensure_ascii=False))

                    # --- Проверка заголовков лимитов (опционально) ---
                    if 'X-Ratelimit-Remaining' in response.headers:
                        print(f"[OpenRouterTranslator] X-Ratelimit-Remaining: {response.headers['X-Ratelimit-Remaining']}")
                    # ... другие заголовки лимитов ...

                    # --- Обработка успешного ответа ---
                    if response.status_code == 200:
                        try:
                            response_json = response.json()
                        except Exception as e_json:
                            print(f"[OpenRouterTranslator] ОШИБКА: Не удалось распарсить JSON-ответ от OpenRouter: {e_json}")
                            # Логируем часть тела ответа для диагностики
                            resp_text = response.text if hasattr(response, 'text') else str(response.content)
                            print(f"[OpenRouterTranslator] Тело ответа (первые 500 символов): {resp_text[:500]}")
                            return None
                        # Проверка наличия 'choices' и 'message'
                        if response_json and 'choices' in response_json and response_json['choices']:
                             choice = response_json['choices'][0]
                             finish_reason = choice.get('finish_reason')
                             print(f"[OpenRouterTranslator] finish_reason: {finish_reason}")
                             # --- Получаем текст ответа ---
                             output_content = ""
                             if 'message' in choice and 'content' in choice['message']:
                                 output_content = choice['message']['content'].strip()
                             elif 'text' in choice:
                                 output_content = choice['text'].strip()
                             print(f"[OpenRouterTranslator] ПЕРВЫЕ 100 СИМВОЛОВ ОТВЕТА: '{output_content[:100]}'")
                             if chunk_text is not None:
                                 print(f"[OpenRouterTranslator] Длина исходного текста секции: {len(chunk_text)} символов. Длина перевода: {len(output_content)} символов.")
                             # --- Проверка на обрезание ---
                             is_truncated = False
                             # Применяем честную эвристику только для перевода: сравниваем длину перевода и исходного текста в символах
                             if operation_type == 'translate' and finish_reason == 'stop' and chunk_text:
                                 input_char_len = len(chunk_text.strip())
                                 output_char_len = len(output_content.strip())
                                 if output_char_len < input_char_len * 0.8:
                                     is_truncated = True
                                     print(f"[OpenRouterTranslator] Предупреждение: Перевод ({output_char_len} символов) значительно короче исходного текста ({input_char_len} символов) (<80%). Возвращаем TRUNCATED_RESPONSE_ERROR.")
                             if is_truncated:
                                 return 'TRUNCATED_RESPONSE_ERROR'
                             print("[OpenRouterTranslator] Ответ получен в формате message.content. Успех.")
                             return output_content
                        else:
                            print("[OpenRouterTranslator] Ошибка: Неверный формат ответа от API (отсутствуют choices).")
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
            # Новый динамический расчет лимита чанка для перевода
            from translation_module import get_context_length
            context_token_limit = get_context_length(model_name) if model_name else 2048
            context_chars_limit = context_token_limit * 3
            # Оставляем буфер для промпта и ответа (4000 символов)
            CHUNK_SIZE_LIMIT_CHARS = context_chars_limit - 4000
            if CHUNK_SIZE_LIMIT_CHARS <= 0:
                CHUNK_SIZE_LIMIT_CHARS = context_chars_limit // 2
            print(f"[WorkflowTranslator] Динамический лимит чанка для перевода: {CHUNK_SIZE_LIMIT_CHARS} символов (модель: {model_name})")
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
            return self._call_model_api(model_name, messages, operation_type=operation_type, chunk_text=text_to_translate)

        elif operation_type == 'translate':
            print(f"[WorkflowTranslator] Вызов операции 'translate' для текста длиной {len(text_to_translate)} символов.")

            translated_chunks = []
            chunks = self._chunk_text(text_to_translate, CHUNK_SIZE_LIMIT_CHARS)
            if not chunks:
                print("[WorkflowTranslator] Нет чанков для перевода.")
                return None

            print(f"[WorkflowTranslator] Текст разбит на {len(chunks)} чанков с лимитом {CHUNK_SIZE_LIMIT_CHARS} символов.")

            for i, chunk in enumerate(chunks):
                chunk_stripped = chunk.strip()
                chunk_length = len(chunk_stripped)  # Длина исходного текста секции/чанка
                print(f"[WorkflowTranslator] Перевод чанка {i+1}/{len(chunks)} (длина исходного текста: {chunk_length} симв).")
                messages = self._build_messages_for_operation(
                    operation_type,
                    chunk, # Передаем сам чанк
                    target_language,
                    model_name=model_name,
                    prompt_ext=prompt_ext, 
                    dict_data=dict_data 
                )

                max_chunk_retries = 2
                attempt = 0
                translated_chunk = None
                while attempt <= max_chunk_retries:
                    translated_chunk = self._call_model_api(model_name, messages, operation_type=operation_type, chunk_text=chunk)
                    if translated_chunk is None:
                        print(f"[WorkflowTranslator] Ошибка перевода чанка {i+1} (попытка {attempt+1}). Прерывание.")
                        return None
                    # --- Эвристика: если чанк короткий (<100 символов), принимаем любой непустой перевод ---
                    if chunk_length < 100:
                        if translated_chunk.strip():
                            break
                        else:
                            print(f"[WorkflowTranslator] Короткий чанк (<100 симв). Перевод пустой. Попытка {attempt+1}/{max_chunk_retries+1}.")
                            attempt += 1
                            if attempt > max_chunk_retries:
                                print(f"[WorkflowTranslator] Ошибка: короткий чанк остался пустым после {max_chunk_retries+1} попыток. Возвращаем EMPTY_RESPONSE_ERROR.")
                                return EMPTY_RESPONSE_ERROR
                            time.sleep(2)
                            continue
                    # --- Для длинных чанков прежняя эвристика, но сравниваем только с длиной исходного текста ---
                    elif not translated_chunk.strip() or len(translated_chunk.strip()) < max(10, chunk_length * 0.8):
                        print(f"[WorkflowTranslator] Предупреждение: перевод чанка {i+1} подозрительно короткий или пустой (длина: {len(translated_chunk.strip())}, исходный: {chunk_length}). Попытка {attempt+1}/{max_chunk_retries+1}.")
                        attempt += 1
                        if attempt > max_chunk_retries:
                            print(f"[WorkflowTranslator] Ошибка: перевод чанка {i+1} остался пустым после {max_chunk_retries+1} попыток. Возвращаем EMPTY_RESPONSE_ERROR.")
                            return EMPTY_RESPONSE_ERROR
                        time.sleep(2) # небольшая задержка между попытками
                        continue
                    break
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
