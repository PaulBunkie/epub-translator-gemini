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
        # TODO: Добавить инициализацию Google API ключа

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
        """
        Формирует список сообщений для API на основе типа операции и входных данных.
        Использует dict_data для получения глоссария и инструкций для translate.
        """
        messages = []

        # Извлекаем данные из dict_data для операции translate, если они есть
        glossary = dict_data.get('glossary') if dict_data else None
        system_instruction = dict_data.get('system_instruction') if dict_data else None
        user_instruction = dict_data.get('user_instruction') if dict_data else None

        # Базовые системные инструкции
        base_system_instruction = system_instruction or "You are a helpful assistant."
        system_content = base_system_instruction

        # TODO: Уточнить системные и пользовательские инструкции на основе operation_type и potential prompt_ext
        # Сейчас делаем простые инструкции для тестирования
        if operation_type == 'summarize':
            system_content = system_instruction or "You are a summarization engine. Provide a concise summary."
            user_content = f"Summarize the following text in the original language:\n\n{text_to_process}"
            # Примечание: Суммаризация в оригинальном языке согласно workflow_processor.

        elif operation_type == 'analyze':
            # Системный промпт для анализа
            system_content = """You are an expert literary terminology analyst working with professional translators. Your job is to extract all key terms from fiction or narrative non-fiction that require consistent translation across chapters.

Be strict and conservative:
- Only extract terms that are clearly and unambiguously mentioned in the text.
- Never assume, invent or guess meanings, genders, or translations.
- Prioritize clarity, grammatical precision, and usefulness for automated and human translation.

Your output must always be a clean, valid Markdown table using the following columns:

Term | Type | Gender | Translation | Comment

Never output explanations or anything outside the table."""
            # Пользовательский промпт для анализа, с подстановкой target_language и text_to_process
            user_content = f"""Analyze the following text excerpt to extract terms that require consistent translation into {target_language}. These include, but are not limited to:

- Personal names (first names, surnames, nicknames)
- Abbreviations or acronyms
- Institutions, organizations, companies, groups
- Technologies, procedures, job titles
- Neologisms and invented terms
- Culturally specific words or expressions that may not translate directly

For each term found, add a row to the Markdown table with the following columns:

**Term** — The original form exactly as it appears in the text.
**Type** — One of: Character, Organization, Abbreviation, Job Title, Technology, Neologism, Cultural Term, Other.
**Gender** — For characters: 'm', 'f', or '—' if not clear. For nouns: grammatical gender in target language, or '—' if not applicable.
**Translation** — A single suggested translation into {target_language}.
**Comment** — A short explanation for context or difficulty. If the term's gender is known, mention why (e.g., "Referred to as 'he'"). If it's an abbreviation, note whether it's explained or needs expansion. If it's ambiguous or culturally loaded, explain that.

Do not include common words or phrases that don't require glossary treatment.
Avoid adding common country or city names unless there is a translation ambiguity or special context.
Do not guess meanings or add speculative terms. Use only direct evidence from the text.
Prefer fewer high-quality entries over too many weak or generic ones.

Here is the text to analyze:

{text_to_process}"""

        elif operation_type == 'translate':
             print("[WorkflowTranslator] Формирование сообщений для translate.")
             # TODO: Реализовать логику формирования структурированного промпта с глоссарием и инструкциями.
             # Это будет сделано позже.
             # Для translate, messages будет в формате, нужном для конкретного API (OpenRouter/Gemini)
             # Пример для OpenRouter/OpenAI Chat Completions API:

             system_content = dict_data.get('system_instruction') if dict_data else None
             user_instruction = dict_data.get('user_instruction') if dict_data else None
             glossary = dict_data.get('glossary') if dict_data else None

             # Base system instruction - can be overridden by dict_data
             base_system_instruction = system_content or "You are a professional literary translator."

             messages.append({"role": "system", "content": base_system_instruction})

             user_content_parts = []
             if user_instruction: user_content_parts.append(user_instruction)

             # Добавляем глоссарий, если он предоставлен в dict_data
             if glossary:
                 user_content_parts.append("# GLOSSARY (must be followed exactly):")
                 # TODO: Форматировать глоссарий более строго? JSON? Пока простой текст.
                 # Пример форматирования глоссария как в примере OpenRouter:
                 # {"role": "user", "content": "Translate the following text. Use this glossary: [term: translation]"}
                 # Пока оставим простой текстовый формат
                 glossary_text = "\n".join([f"{k} → {v}" for k, v in glossary.items()])
                 user_content_parts.append(glossary_text)

             # TODO: Учесть prompt_ext для translate. Возможно, добавить его в user_content_parts.
             # if prompt_ext: user_content_parts.append(f"ADDITIONAL INSTRUCTIONS:\n{prompt_ext}")
             # или добавить его в системные инструкции? Решить как лучше интегрировать.

             user_content_parts.append("# TEXT TO TRANSLATE:")
             user_content_parts.append(text_to_process) # text_to_process здесь - это чанк

             user_content = "\n\n".join(user_content_parts)
             messages.append({"role": "user", "content": user_content})

             # TODO: Для некоторых моделей может потребоваться добавить роль assistant с примером ответа?

        else:
            # Дефолтный случай или неизвестная операция
            user_content = text_to_process # Просто отправляем исходный текст как user content

        messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": user_content})

        # TODO: Учесть prompt_ext - куда его добавить? В старом модуле prompt_ext добавлялся как отдельная секция в _build_prompt.
        # Для summarize/analyze мы можем добавить его в user_content.
        # Для translate prompt_ext может быть обработан внутри логики формирования translate-промпта,
        # возможно, объединив его с user_instruction из dict_data или добавив как отдельную секцию.
        # TODO: Определить, как использовать prompt_ext для операции translate.


        print(f"[WorkflowTranslator] Сформирован промпт для '{operation_type}' (первые 200 симв):\n---\nSystem: {messages[0]['content'][:200]}...\nUser: {messages[-1]['content'][:200]}...\n---")

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