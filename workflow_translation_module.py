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
        # Для translate prompt_ext будет частью structured user_content (как в старом _build_prompt).
        # Нам нужно решить, как его использовать в новом структурированном промпте для translate.
        if prompt_ext and operation_type in ['summarize', 'analyze']:
             # Добавляем prompt_ext как дополнительную инструкцию в user_content для sum/analyze
             messages[-1]['content'] += f"\n\nADDITIONAL INSTRUCTIONS:\n{prompt_ext}"
        # Для translate, prompt_ext может быть обработан внутри логики формирования translate-промпта,
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

    # TODO: Реализовать метод для определения длины контекста модели, возможно, используя translation_module.get_context_length
    def _get_context_length(self, model_name: str) -> int:
        """Получает лимит токенов для модели."""
        try:
            # Попробуем использовать оригинальную функцию, если импортирован translation_module
            limit = translation_module.get_context_length(model_name)
            # print(f"[WorkflowTranslator] Используется реальный лимит контекста из translation_module: {limit} для модели {model_name}")
            return limit
        except (AttributeError, NameError):
             # Если не удалось или модуль не импортирован/функция отсутствует, используем дефолтное значение
             print("[WorkflowTranslator] WARNING: Не удалось вызвать translation_module.get_context_length. Использован дефолтный лимит.")
             return 30000 # Дефолтное большое значение на всякий случай

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
        Обрабатывает различные операции рабочего процесса (sum, analyze, translate)
        для данного класса.
        Проксирует запросы sum/analyze к старому модулю (пока нет, используем заглушку).
        Для операции 'translate' реализует новую логику (чанкинг, JSON промпт, вызов API).

        Args:
            text_to_translate: Текст для обработки (секция).
            target_language: Целевой язык.
            model_name: Имя модели.
            prompt_ext: Дополнительные инструкции для модели (используются старым модулем).
            operation_type: Тип операции ('summarize', 'analyze', 'translate').
            dict_data: Дополнительные данные для workflow (например, глоссарий), необязательный.

        Returns:
            Результат операции (текст) или None в случае ошибки.
        """
        print(f"[WorkflowTranslator] Вызов метода translate_text. Операция: '{operation_type}'")

        # dict_data теперь доступен здесь и может быть передан дальше
        # Обработка summarize и analyze (сейчас используют заглушку _call_model_api)
        if operation_type in ['summarize', 'analyze']:
            print(f"[WorkflowTranslator] Обработка операции '{operation_type}'.")
            # Шаг 1: Сформировать сообщения для API
            messages = self._build_messages_for_operation(
                 operation_type=operation_type,
                 text_to_process=text_to_translate,
                 target_language=target_language,
                 model_name=model_name, # Передаем model_name
                 prompt_ext=prompt_ext,
                 dict_data=dict_data # Передаем dict_data
            )

            # Шаг 2: Вызвать API с сформированными сообщениями (используя заглушку _call_model_api)
            api_result = self._call_model_api(
                model_name=model_name,
                messages=messages,
                # Дополнительные параметры API, если нужны (temperature и т.д.)
            )

            # Шаг 3: Обработать результат API вызова
            # TODO: Добавить реальную обработку ошибок API (None, EMPTY_RESPONSE_ERROR, CONTEXT_LIMIT_ERROR)
            if api_result is not None and api_result != translation_module.EMPTY_RESPONSE_ERROR:
                 print(f"[WorkflowTranslator] API вызов для '{operation_type}' успешен (заглушка).")
                 return api_result
            else:
                 print(f"[WorkflowTranslator] API вызов для '{operation_type}' вернул ошибку или пустой результат (заглушка).")
                 # В реальной реализации здесь будет логика ретраев и определения статуса ошибки
                 return None # Или индикатор ошибки

        elif operation_type == 'translate':
            print("[WorkflowTranslator] Вызов новой логики перевода с глоссарием/чанками.")
            # TODO: Реализовать здесь логику чанкинга, формирования JSON промпта и вызова API.

            text_len = len(text_to_translate)
            # Получаем лимит контекста для модели
            # Используем заглушку _get_context_length пока не реализуем правильно
            token_limit = self._get_context_length(model_name or "default-model") # Используем дефолт если model_name нет
            # Оцениваем лимит символов, умножая лимит токенов на ~3-4 с запасом
            # Это грубая оценка, идеальна токенизация. Но для начала сойдет.
            CHUNK_SIZE_LIMIT_CHARS = int(token_limit * 3.5) # Умножаем на 3.5 для запаса

            # Добавляем минимальное ограничение
            MIN_CHUNK_SIZE = 1000
            if CHUNK_SIZE_LIMIT_CHARS < MIN_CHUNK_SIZE:
                 CHUNK_SIZE_LIMIT_CHARS = MIN_CHUNK_SIZE

            print(f"[WorkflowTranslator] Проверка длины текста: {text_len} симв. Оценочный лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} симв.")

            # Пробуем перевести целиком, если текст достаточно короткий
            # Добавляем небольшой буфер (10%), чтобы не разбивать слишком близко к лимиту
            if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1:
                print("[WorkflowTranslator] Пробуем перевод целиком...")
                try:
                     # Формируем сообщения для всего текста как одного чанка
                     messages = self._build_messages_for_operation(
                          operation_type=operation_type,
                          text_to_process=text_to_translate,
                          target_language=target_language,
                          model_name=model_name,
                          prompt_ext=prompt_ext,
                          dict_data=dict_data # Передаем dict_data
                     )
                     # Вызываем API для всего текста
                     translated_text = self._call_model_api(
                         model_name=model_name,
                         messages=messages
                     )
                     # TODO: Обработка CONTEXT_LIMIT_ERROR от API вызова
                     if translated_text != translation_module.CONTEXT_LIMIT_ERROR:
                         print("[WorkflowTranslator] Перевод целиком успешен.")
                         return translated_text
                     else:
                         print("[WorkflowTranslator] Перевод целиком не удался (лимит контекста), переключаемся на чанки.")
                except Exception as e:
                     print(f"[WorkflowTranslator] ОШИБКА при переводе целиком: {e}")
                     traceback.print_exc()
                     print("[WorkflowTranslator] Переключаемся на чанки после ошибки.")

            # Разбиваем на чанки по параграфам/предложениям (логика как в старом модуле)
            print(f"[WorkflowTranslator] Текст длинный ({text_len} симв.), разбиваем на чанки...")
            paragraphs = text_to_translate.split('\n\n')
            chunks = []
            current_chunk = []
            current_chunk_len = 0

            for paragraph in paragraphs:
                paragraph = paragraph.strip()
                if not paragraph:
                    continue

                paragraph_len = len(paragraph)
                # Если параграф сам по себе больше лимита, пытаемся разбить его на предложения
                if paragraph_len > CHUNK_SIZE_LIMIT_CHARS:
                    if current_chunk: # Добавляем текущий накопленный чанк перед обработкой длинного параграфа
                        chunks.append('\n\n'.join(current_chunk).strip())
                        current_chunk = []
                        current_chunk_len = 0

                    # Разбиваем длинный параграф на предложения
                    sentences = re.split(r'(?<=[.!?])\s+', paragraph) # Разбиваем по концу предложения с пробелом
                    temp_chunk = []
                    temp_chunk_len = 0

                    for sentence in sentences:
                        sentence = sentence.strip()
                        if not sentence: continue

                        sentence_len = len(sentence)
                        # Если добавление предложения превысит лимит
                        if temp_chunk_len + sentence_len + (2 if temp_chunk else 0) > CHUNK_SIZE_LIMIT_CHARS:
                            if temp_chunk:
                                chunks.append('. '.join(temp_chunk).strip()) # Добавляем накопленный чанк предложений
                            temp_chunk = [sentence] # Начинаем новый чанк с текущего предложения
                            temp_chunk_len = sentence_len
                        else:
                            temp_chunk.append(sentence) # Добавляем предложение к текущему чанку
                            temp_chunk_len += sentence_len + (2 if temp_chunk_len > 0 else 0) # Учитываем ". " между предложениями

                    if temp_chunk: # Добавляем последний чанк предложений
                        chunks.append('. '.join(temp_chunk).strip())

                else: # Параграф меньше или равен лимиту чанка
                    # Если добавление параграфа превысит лимит текущего чанка
                    if current_chunk_len + paragraph_len + (4 if current_chunk else 0) > CHUNK_SIZE_LIMIT_CHARS:
                        chunks.append('\n\n'.join(current_chunk).strip()) # Добавляем накопленный чанк параграфов
                        current_chunk = [paragraph] # Начинаем новый чанк с текущего параграфа
                        current_chunk_len = paragraph_len
                    else:
                        current_chunk.append(paragraph) # Добавляем параграф к текущему чанку
                        current_chunk_len += paragraph_len + (4 if current_chunk_len > 0 else 0) # Учитываем "\n\n" между параграфами

            if current_chunk: # Добавляем последний чанк параграфов
                chunks.append('\n\n'.join(current_chunk).strip())

            # Удаляем пустые чанки, которые могли появиться из-за strip() или пустых строк
            chunks = [chunk for chunk in chunks if chunk]

            if not chunks:
                print("[WorkflowTranslator] Ошибка: Не удалось создать чанки!")
                return None

            print(f"[WorkflowTranslator] Текст разбит на {len(chunks)} чанков.")
            translated_chunks = []
            last_successful_translation = "" # TODO: Использовать для previous_context если нужно

            for i, chunk in enumerate(chunks, 1):
                print(f"[WorkflowTranslator] -- Обработка чанка {i}/{len(chunks)} ({len(chunk)} симв.)...")
                # TODO: Реализовать логику ретраев для каждого чанка здесь

                # Шаг 1: Сформировать сообщения для чанка
                # Здесь dict_data будет передано, если оно было передано в translate_text
                messages = self._build_messages_for_operation(
                     operation_type=operation_type,
                     text_to_process=chunk, # Обрабатываем чанк
                     target_language=target_language,
                     model_name=model_name,
                     prompt_ext=prompt_ext, # Пока прокидываем, но нужно решить как использовать в translate
                     dict_data=dict_data # Передаем dict_data с глоссарием/инструкциями
                )

                # Шаг 2: Вызвать API для чанка
                chunk_result = self._call_model_api(
                    model_name=model_name,
                    messages=messages
                )

                # Шаг 3: Обработать результат чанка
                # TODO: Обработка ошибок: CONTEXT_LIMIT_ERROR, EMPTY_RESPONSE_ERROR, другие ошибки API
                if chunk_result is not None and chunk_result != translation_module.CONTEXT_LIMIT_ERROR and chunk_result != translation_module.EMPTY_RESPONSE_ERROR:
                    translated_chunks.append(chunk_result)
                    # TODO: Обновить last_successful_translation для контекста следующего чанка (если нужно)
                    # last_successful_translation = chunk_result
                else:
                    print(f"[WorkflowTranslator] Ошибка обработки чанка {i}: {chunk_result}. Пропускаем чанк или обрабатываем ошибку.")
                    # TODO: Реализовать логику обработки ошибок чанка: пропустить, пометить как ошибку, прекратить процесс?
                    # Пока просто пропускаем чанк, если API вернуло ошибку или None
                    translated_chunks.append(f"[ERROR_PROCESSING_CHUNK_{i}]") # Помечаем ошибку

            # Шаг 4: Собрать переведенные чанки обратно
            if translated_chunks:
                 # TODO: Определить, как лучше объединять чанки. Простое объединение '\n\n' может быть неидеальным.
                 # Возможно, нужно сохранять структуру параграфов/предложений.
                 final_translated_text = '\n\n'.join(translated_chunks)
                 print("[WorkflowTranslator] Все чанки обработаны.")
                 return final_translated_text
            else:
                 print("[WorkflowTranslator] ОШИБКА: Ни один чанк не был успешно обработан.")
                 return None # Возвращаем None, если все чанки дали ошибку

        else:
            print(f"[WorkflowTranslator] Предупреждение: Неизвестный тип операции рабочего процесса: {operation_type}")
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