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
# --- Новая константа для обозначения обрезанного ответа ---
TRUNCATED_RESPONSE_ERROR = "__TRUNCATED_RESPONSE_DETECTED__"
# --- Конец новой константы ---

# Переменная для кэширования списка моделей. Теперь это словарь.
_cached_models: Dict[str, Optional[List[Dict[str, Any]]]] = {"free": None, "all": None}
_model_list_last_update: Optional[float] = None # Опционально: для будущего кэширования по времени
_MODEL_LIST_CACHE_TTL = 3600 # Время жизни кэша в секундах (1 час) - можно настроить

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ЭВРИСТИКИ (ПЕРЕМЕЩЕНО СЮДА) ---
def _ends_with_complete_sentence(text: str) -> bool:
    """
    Проверяет, заканчивается ли строка полным предложением (т.е. на знак препинания).
    """
    if not text:
        return False
    # Удаляем любые пробелы и небуквенные символы в конце, кроме знаков препинания,
    # чтобы корректно обработать текст, заканчивающийся на ".", "!", "?"
    cleaned_text = text.strip()
    # Проверяем, заканчивается ли текст на '.', '!', '?' (возможно, с кавычками/скобками после)
    return bool(re.search(r'[.!?]["\'\])}]*$', cleaned_text))
# --- КОНЕЦ ПЕРЕМЕЩЕННОЙ ВСПОМОГАТЕЛЬНОЙ ФУНКЦИИ ---

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
        return f"\n---\n{title}:\n{content}\n---"
    return ""

class BaseTranslator(ABC):
    @staticmethod
    def _build_prompt(
        operation_type: str,
        target_language: str,
        text: str,
        previous_context: str = "",
        prompt_ext: Optional[str] = None
    ) -> tuple[str, int]:
        """Формирует строку промпта для модели на основе типа операции и входных данных.
        Возвращает кортеж: (сформированный промпт, оценочная длина символов промпта без основного текста).
        """
        template = PROMPT_TEMPLATES.get(operation_type)
        if not template:
            raise ValueError(f"Неизвестный тип операции: {operation_type}")

        # Форматируем дополнительные секции отдельно, чтобы не добавлять их, если они пустые
        # И сразу получаем их длину для подсчета нетекстовой части
        prompt_ext_section = _format_prompt_section("ADDITIONAL INSTRUCTIONS (Apply if applicable, follow strictly for names and terms defined here)", prompt_ext)
        previous_context_section = _format_prompt_section("Previous Context (use for style and recent terminology reference)", previous_context)

        # --- Рассчитываем значение для правила русского диалога ---
        russian_dialogue_rule = ' - When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.' if target_language.lower() == 'russian' else ''

        # --- Рассчитываем значение для заголовка примечаний переводчика ---
        translator_notes_heading = 'Примечания переводчика' if target_language.lower() == 'russian' else 'Translator Notes'

        # Используем f-строку для форматирования шаблона
        # Временные плейсхолдеры, чтобы потом вычислить длину "не-текстовой" части
        temp_prompt_for_overhead_calc = template.format(
            target_language=target_language,
            text="", # Замещаем текст пустым, чтобы получить длину остального промпта
            prompt_ext_section=prompt_ext_section,
            previous_context_section=previous_context_section,
            russian_dialogue_rule=russian_dialogue_rule,
            translator_notes_heading=translator_notes_heading,
        )
        
        # Вычисляем общую длину символов не-текстовой части промпта.
        # Учитываем, что .format() вставляет текст и его плейсхолдеры.
        # Просто вычитаем длину placeholder "{text}" и добавляем длину реального текста.
        # Более надежный способ: сформировать промпт с пустой строкой вместо {text}
        
        estimated_non_text_char_length = len(temp_prompt_for_overhead_calc.replace("{text}", "").strip()) # Удаляем {text} и лишние пробелы

        # Формируем окончательный промпт с реальным текстом
        final_prompt = template.format(
            target_language=target_language,
            text=text,
            prompt_ext_section=prompt_ext_section,
            previous_context_section=previous_context_section,
            russian_dialogue_rule=russian_dialogue_rule,
            translator_notes_heading=translator_notes_heading,
        )

        # Удаляем возможные двойные пустые строки, если секции были пустыми
        final_prompt = final_prompt.replace("\n\n\n", "\n\n").strip()

        # Возвращаем сам промпт и оценочную длину нетекстовой части
        return final_prompt, estimated_non_text_char_length

    @abstractmethod
    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None,
                       operation_type: str = 'translate') -> Optional[str]:
        pass

    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                      model_name: str = None, prompt_ext: Optional[str] = None,
                      operation_type: str = 'translate') -> Optional[str]:
        """Основная функция для перевода строки текста (реализация в базовом классе)."""
        text_len = len(text_to_translate)
        CHUNK_SIZE_LIMIT_CHARS = 0 # Инициализируем переменную
        limit_source_desc = ""

        # --- ИСПРАВЛЕНИЕ: Определяем MIN_CHUNK_SIZE в начале функции ---
        MIN_CHUNK_SIZE = 1000 # Минимум 1000 символов, можно настроить
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        actual_model_name = model_name if model_name else "meta-llama/llama-4-maverick:free"
        
        # Получаем полный контекстный лимит модели (input + output)
        model_total_context_limit = get_context_length(actual_model_name)
        
        # Получаем максимально заявленный лимит вывода
        model_declared_output_limit = get_model_output_token_limit(actual_model_name)

        # Для translate, мы не хотим запрашивать слишком много вывода в одном чанке,
        # чтобы оставить место для ВХОДА. Устанавливаем разумный хардкап для выходных токенов.
        # Этот хардкап должен быть меньше или равен заявленному output_token_limit модели.
        HARD_CAP_OUTPUT_TOKENS_TRANSLATE = 128000 # Макс. 128K токенов для одного перевода чанка

        # Оценим размер промпта в токенах, который не является непосредственно текстом для перевода.
        # Включает системный промпт, previous_context и prompt_ext.
        # Это "накладные расходы" промпта, которые занимают место во входном контексте.
        
        # --- ИСПРАВЛЕНИЕ: Более точная оценка накладных расходов промпта ---
        # Оценим базовый промпт без текста и previous_context/prompt_ext
        # Пример: System prompt tokens (из шаблона) + instructions
        # ОЧЕНЬ грубая оценка для *структуры* промпта (без content): ~500 токенов
        base_prompt_structure_tokens = 500
        if target_language.lower() == 'russian':
            base_prompt_structure_tokens += 10 # За правило русского диалога
        
        # Оценка prompt_ext токенов
        prompt_ext_tokens = int(len(prompt_ext) / 3) if prompt_ext else 0
        
        # Оценка previous_context токенов (для целей расчета CHUNK_SIZE_LIMIT_CHARS, берем пессимистичный случай)
        # Если actual_requested_output_tokens берется из общего лимита, то previous_context должен быть максимально учтен
        estimated_previous_context_tokens = 500 # Оценка: ~100 слов * 5 символов/слово = 500 символов, /3 ~ 166 токенов, округлим до 500 для запаса
        # Максимально возможные токены, которые могут быть заняты фиксированными частями промпта + максимальным previous_context
        # Эта оценка нужна для расчета CHUNK_SIZE_LIMIT_CHARS, чтобы гарантировать, что *даже самый маленький чанк*
        # (включая свой previous_context и prompt_ext) оставит место для вывода.
        estimated_max_input_overhead_for_chunk = base_prompt_structure_tokens + \
                                                 prompt_ext_tokens + \
                                                 estimated_previous_context_tokens
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        # Рассчитываем, сколько токенов осталось для ВХОДНОГО ТЕКСТА чанка.
        # Это то количество токенов, которое сам 'text' может занимать, чтобы
        # (text_tokens + estimated_max_input_overhead_for_chunk + SOME_OUTPUT_TOKENS) <= model_total_context_limit
        # Вместо того чтобы вычитать actual_requested_output_tokens (который теперь рассчитывается в translate_chunk),
        # мы вычтем "резерв" для вывода, чтобы получить лимит на входной текст.
        # Для CHUNK_SIZE_LIMIT_CHARS, мы хотим, чтобы чанк был достаточно мал, чтобы оставить место
        # для output_tokens. Зарезервируем минимально необходимый output_token_limit.
        MIN_RESERVE_OUTPUT_TOKENS = 4096 # Минимальный резерв для вывода, если модель может дать больше
        
        # Рассчитываем, сколько токенов осталось для ВХОДНОГО ТЕКСТА чанка,
        # чтобы влезли: (текст чанка + overhead_prompt + МИНИМАЛЬНЫЙ резерв для вывода) <= total_context
        remaining_tokens_for_input_text_chunk = model_total_context_limit - estimated_max_input_overhead_for_chunk - MIN_RESERVE_OUTPUT_TOKENS

        # Конвертируем токены в символы, используя приблизительный коэффициент 3 символа/токен
        # Убедимся, что лимит не отрицательный и не слишком маленький
        CHUNK_SIZE_LIMIT_CHARS = max(MIN_CHUNK_SIZE, int(remaining_tokens_for_input_text_chunk * 3))
        
        limit_source_desc = (f"Лимит по токенам модели (Общий: {model_total_context_limit}, "
                             f"Резерв вывода: {MIN_RESERVE_OUTPUT_TOKENS}, Промпт: ~{estimated_max_input_overhead_for_chunk})")

        # Для summarize и analyze, мы можем использовать более высокий output_token_limit
        # так как их выход обычно короче, и мы можем отдать под него больше от общего контекста.
        # Текущая логика для них остается использовать input_token_limit * 3.
        if operation_type in ['summarize', 'analyze']:
            # Для этих операций, CHUNK_SIZE_LIMIT_CHARS может быть просто модель_total_context_limit - overhead
            # поскольку мы ожидаем более короткий вывод. Но пока оставим как есть.
            CHUNK_SIZE_LIMIT_CHARS = int(model_total_context_limit * 3) # Для sum/analyze берем просто общий лимит контекста
            limit_source_desc = f"Лимит по токенам модели (Общий: {model_total_context_limit} * 3)"
            # ИЛИ если мы хотим более точно:
            # calculated_output_limit_for_sum_analyze = model_total_context_limit - (base_prompt_structure_tokens + prompt_ext_tokens)
            # CHUNK_SIZE_LIMIT_CHARS = max(MIN_CHUNK_SIZE, int((model_total_context_limit - (base_prompt_structure_tokens + prompt_ext_tokens + some_small_output_reserve)) * 3))

        # Добавляем минимальное ограничение, чтобы избежать деления на ноль или слишком маленьких чанков
        # MIN_CHUNK_SIZE уже определено в начале функции
        if CHUNK_SIZE_LIMIT_CHARS < MIN_CHUNK_SIZE:
             CHUNK_SIZE_LIMIT_CHARS = MIN_CHUNK_SIZE
             limit_source_desc += " (увеличен до минимума)"
             print(f"[BaseTranslator] Лимит чанка ({operation_type}) был меньше минимального. Установлен: {CHUNK_SIZE_LIMIT_CHARS}.") # ИСПРАВЛЕНА ОПЕЧАТКА
        
        # Защита от отрицательного или слишком маленького лимита чанка (может произойти, если estimated_max_input_overhead_for_chunk слишком велик)
        if CHUNK_SIZE_LIMIT_CHARS <= 0:
            print(f"[BaseTranslator] Предупреждение: Рассчитанный CHUNK_SIZE_LIMIT_CHARS <= 0. Устанавливается дефолтный: {MIN_CHUNK_SIZE}.")
            CHUNK_SIZE_LIMIT_CHARS = MIN_CHUNK_SIZE
            limit_source_desc += " (установлен дефолтный из-за отрицательного расчета)"


        # Обновляем сообщение в логе, чтобы отразить, как был установлен лимит
        print(f"[BaseTranslator] Проверка длины текста: {text_len} симв. Лимит чанка ({limit_source_desc}): {CHUNK_SIZE_LIMIT_CHARS} симв.")
        print(f"[BaseTranslator] Ожидаемый макс. токены ввода для текста: {remaining_tokens_for_input_text_chunk} (для перевода)")

        # Теперь используем CHUNK_SIZE_LIMIT_CHARS в оставшейся логике функции
        # Учитываем, что CHUNK_SIZE_LIMIT_CHARS может быть очень большим, если оставшихся токенов много
        # Мы все равно хотим разбить на чанки, если полный текст очень большой.
        # Используем CHUNK_SIZE_LIMIT_CHARS для определения, нужен ли вообще чанкинг
        # Если text_len все еще слишком велик для рассчитанного CHUNK_SIZE_LIMIT_CHARS,
        # то логика чанкинга ниже сработает.
        
        # Если operation_type - translate, используем рассчитанный actual_requested_output_tokens
        # для переопределения лимита вывода в translate_chunk.
        # Условие text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1 определяет, пробуем ли мы переводить целиком.
        if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1: 
            if operation_type == 'translate':
                print("[BaseTranslator] Пробуем перевод целиком (operation_type=translate)...")
                # ИСПРАВЛЕНИЕ: previous_context для первого вызова должен быть пустой строкой
                result = self.translate_chunk(model_name, text_to_translate, target_language, 
                                              previous_context="", prompt_ext=prompt_ext, operation_type=operation_type) 
                
                # --- ИСПРАВЛЕНИЕ: Корректная обработка TRUNCATED_RESPONSE_ERROR и других ошибок ---
                if result == TRUNCATED_RESPONSE_ERROR:
                    print("[BaseTranslator] Перевод целиком вернул TRUNCATED_RESPONSE_ERROR. Переключаемся на чанки.")
                    # Продолжаем выполнение функции, чтобы перейти к логике чанкинга
                elif result == CONTEXT_LIMIT_ERROR:
                    print("[BaseTranslator] Перевод целиком не удался (лимит контекста), переключаемся на чанки.")
                    # Продолжаем выполнение функции, чтобы перейти к логике чанкинга
                elif result is None:
                    print("[BaseTranslator] Перевод целиком вернул None (ошибка API). Прерываем перевод.")
                    return None # Окончательная ошибка, завершаем
                else: # Если result - это успешно полученный текст (не ошибка), возвращаем его
                    return result
                # --- КОНЕЦ ИСПРАВЛЕНИЯ ---
            else: # Для summarize/analyze и других операций
                print(f"[BaseTranslator] Пробуем перевод целиком (operation_type={operation_type})...")
                result = self.translate_chunk(model_name, text_to_translate, target_language, 
                                              previous_context="", prompt_ext=prompt_ext, operation_type=operation_type)
                if result != CONTEXT_LIMIT_ERROR:
                    return result
                print(f"[BaseTranslator] Операция целиком не удалась (лимит контекста) для {operation_type}, переключаемся на чанки.")
        else: # Если текст слишком длинный для рассчитанного CHUNK_SIZE_LIMIT_CHARS, сразу переходим к чанкингу
            print(f"[BaseTranslator] Текст слишком длинный ({text_len} симв.) для перевода целиком (лимит: {CHUNK_SIZE_LIMIT_CHARS}), сразу переходим к чанкингу.")

        # Разбиваем на параграфы
        print(f"[BaseTranslator] Текст длинный ({text_len} симв.), разбиваем на чанки. Лимит чанка по символам: {CHUNK_SIZE_LIMIT_CHARS}...")
        paragraphs = text_to_translate.split('\n\n')
        chunks = []
        current_chunk = []
        current_chunk_len = 0

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            paragraph_len = len(paragraph)
            # Внимание: здесь мы используем CHUNK_SIZE_LIMIT_CHARS для разделения исходного текста.
            # Важно, чтобы этот лимит оставлял место для промпта + output.
            # Если большой параграф все равно не помещается, то модель будет возвращать ошибку контекста.
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
                    # Добавим точку, если ее нет, для более корректного split/join
                    if sentence and not (sentence.endswith('.') or sentence.endswith('?') or sentence.endswith('!')):
                        sentence += '.'
                    
                    sentence_len = len(sentence)

                    if temp_chunk_len + sentence_len > CHUNK_SIZE_LIMIT_CHARS:
                        if temp_chunk:
                            chunks.append(' '.join(temp_chunk).strip()) # Удаляем лишнюю точку, она уже в sentence, strip() уберет лишние пробелы
                        temp_chunk = [sentence]
                        temp_chunk_len = sentence_len
                    else:
                        temp_chunk.append(sentence)
                        temp_chunk_len += sentence_len + (2 if sentence else 0)  # +2 для '. ' между предложениями, если есть предложение

                if temp_chunk:
                    chunks.append(' '.join(temp_chunk).strip()) # Удаляем лишнюю точку, она уже в sentence
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

        # --- НОВАЯ ЛОГИКА: Ретраи на уровне чанков ---
        max_chunk_retries = 2 # Максимальное количество повторных попыток на чанк (всего 1 + 2 ретрая = 3 попытки)
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        for i, chunk in enumerate(chunks, 1):
            chunk_translated_successfully = False
            chunk_retry_attempt = 0
            chunk_result = None

            while not chunk_translated_successfully and chunk_retry_attempt <= max_chunk_retries:
                print(f"[BaseTranslator] -- Перевод чанка {i}/{len(chunks)} ({len(chunk)} симв.). Попытка {chunk_retry_attempt + 1}/{max_chunk_retries + 1}...")
                context_fragment = " ".join(last_successful_translation.split()[-100:]) if last_successful_translation else ""
                
                chunk_result = self.translate_chunk(model_name, chunk, target_language, 
                                                    previous_context=context_fragment, prompt_ext=prompt_ext, 
                                                    operation_type=operation_type)

                if chunk_result == TRUNCATED_RESPONSE_ERROR:
                    print(f"[BaseTranslator] -- Чанк {i} вернул TRUNCATED_RESPONSE_ERROR. Повторная попытка...")
                    chunk_retry_attempt += 1
                    time.sleep(2) # Небольшая задержка перед повторной попыткой
                    continue # Повторяем внутренний цикл
                elif chunk_result == EMPTY_RESPONSE_ERROR:
                    print(f"[BaseTranslator] -- Чанк {i} вернул EMPTY_RESPONSE_ERROR после ретраев. Прерываем перевод.\n")
                    return EMPTY_RESPONSE_ERROR
                elif chunk_result is None:
                    print(f"[BaseTranslator] -- Чанк {i} вернул None. Прерываем перевод.\n")
                    return None
                elif chunk_result == CONTEXT_LIMIT_ERROR:
                    print(f"[BaseTranslator] -- Чанк {i} превысил лимит контекста. Возвращаем ошибку.")
                    return CONTEXT_LIMIT_ERROR
                else:
                    chunk_translated_successfully = True # Чанк успешно переведен

            if not chunk_translated_successfully:
                print(f"[BaseTranslator] -- Чанк {i} не удалось перевести после {max_chunk_retries + 1} попыток. Прерываем перевод.\n")
                return None # Или можно вернуть TRUNCATED_RESPONSE_ERROR, если хотим явно указать на причину

            # Если чанк успешно переведен, добавляем его к общему результату
            translated_chunks.append(chunk_result)
            last_successful_translation = chunk_result

            print(f"[BaseTranslator] Задержка {1} сек после чанка {i}/{len(chunks)}.")
            time.sleep(1) # Длительность задержки в секундах (можно настроить)
        # --- КОНЕЦ НОВОЙ ЛОГИКИ: Ретраи на уровне чанков ---

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
        prompt, _ = self._build_prompt(operation_type, target_language, text, previous_context, prompt_ext)
        max_retries = 3 # Всего 3 попытки (1 начальная + 2 ретрая) для пустых ответов и ошибок API
        retry_delay_seconds = 5 # Задержка между попытками

        # --- НОВОЕ ИЗМЕНЕНИЕ: Динамический расчет max_output_tokens ---
        model_total_context_limit = get_context_length(model_name)
        model_declared_output_limit = get_model_output_token_limit(model_name)
        
        # Оценим длину промпта в токенах (грубо 3 символа на токен)
        input_prompt_tokens = len(prompt) // 3
        
        # Максимально допустимое количество токенов для вывода
        # = Общий лимит контекста - токены входного промпта - небольшой буфер
        calculated_max_output_tokens = model_total_context_limit - input_prompt_tokens - 100 # Буфер 100 токенов
        
        # Окончательный лимит вывода: минимум из заявленного моделью, рассчитанного и нашего хардкапа
        # Для Google API, обычно, model_declared_output_limit уже учтен.
        # Также не запрашиваем слишком мало.
        final_output_token_limit = max(
            256, # Минимальный лимит, чтобы не запрашивать слишком мало
            min(model_declared_output_limit, calculated_max_output_tokens)
        )
        print(f"[GoogleTranslator] Рассчитанный output_token_limit для API: {final_output_token_limit} (Общий контекст: {model_total_context_limit}, Входной промпт: ~{input_prompt_tokens} токенов)")
        # --- КОНЕЦ НОВОГО ИЗМЕНЕНИЯ ---

        for attempt in range(max_retries):
            try:
                print(f"[GoogleTranslator] Отправка запроса на Google API (попытка {attempt + 1}/{max_retries})...")
                model = genai.GenerativeModel(model_name)
                # Передаем лимит выходных токенов и температуру в generation_config
                response = model.generate_content(
                    prompt, 
                    generation_config={"max_output_tokens": final_output_token_limit, "temperature": 0.5}
                )
                print(f"[GoogleTranslator] Получен ответ.")

                # Проверяем наличие текста в ответе. Если есть, возвращаем его сразу.
                if response.text and response.text.strip():
                    print(f"[GoogleTranslator] Успешно получен текст ответа на попытке {attempt + 1}.")
                    
                    # --- НОВАЯ ЭВРИСТИКА: Проверка на потенциальное обрезание ---
                    # Google API не всегда предоставляет finish_reason напрямую в response.text
                    # Это эвристика будет более общей, если нет явного finish_reason.
                    if operation_type == 'translate':
                        input_char_len = len(text) # Длина оригинального текста чанка
                        output_char_len = len(response.text.strip())
                        
                        # Если вывод значительно короче входа, это может быть обрезание.
                        # Коэффициент, например, 0.90: если вывод меньше 90% от входа.
                        if output_char_len < input_char_len * 0.90: 
                            print(f"[GoogleTranslator] Предупреждение: Потенциальное обрезание обнаружено. "
                                  f"Длина входа: {input_char_len} симв., длина вывода: {output_char_len} симв. "
                                  f"(Меньше 90% от оригинала). Возвращаем TRUNCATED_RESPONSE_ERROR.")
                            return TRUNCATED_RESPONSE_ERROR # Возвращаем новую ошибку
                    # --- КОНЕЦ НОВОЙ ЭВРИСТИКИ ---
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
                # Determine output_token_limit:
                # Prefer max_completion_tokens if available
                # If not, use a conservative default like 128000, NOT context_length
                output_limit = model.get('max_completion_tokens')
                if output_limit is None or output_limit == 'N/A':
                    output_limit = 128000 # Conservative default if specific completion limit is not given
                
                formatted_models.append({
                    'name': model['id'],
                    'display_name': model.get('name', model['id']),
                    'input_token_limit': model.get('context_length', 'N/A'), # This is the total context
                    'output_token_limit': output_limit, # Use the determined output limit
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
            # "HTTP-Referer": os.getenv("YOUR_SITE_URL", "http://localhost:5000"), # Optional: Replace with your website URL
            # "X-Title": os.getenv("YOUR_APP_NAME", "EPUB Translator"), # Optional: Replace with your app name
        }

        # ИЗМЕНЕНИЕ: Теперь _build_prompt возвращает prompt и non_text_char_length
        prompt, estimated_non_text_char_length = self._build_prompt(
            operation_type=operation_type,
            target_language=target_language,
            text=text,
            previous_context=previous_context,
            prompt_ext=prompt_ext
        )
        total_prompt_char_length = len(prompt) # Общая длина сформированного промпта в символах

        # --- НОВОЕ ИЗМЕНЕНИЕ: Динамический расчет max_output_tokens ---
        model_total_context_limit = get_context_length(model_name)
        model_declared_output_limit = get_model_output_token_limit(model_name)
        
        # Оценим длину промпта в токенах (грубо 3 символа на токен).
        # Это входные токены, которые OpenRouter называет "text input" в своей ошибке.
        input_prompt_tokens = len(prompt) // 3 
        
        # Максимально допустимое количество токенов для вывода
        # = Общий лимит контекста - токены входного промпта - небольшой буфер
        calculated_max_output_tokens = model_total_context_limit - input_prompt_tokens - 100 # Буфер 100 токенов
        
        # Окончательный лимит вывода: минимум из заявленного моделью, рассчитанного и нашего хардкапа.
        # OpenRouter часто явно указывает max_completion_tokens, но важно не превысить общий контекст.
        final_output_token_limit = max(
            256, # Минимальный лимит, чтобы не запрашивать слишком мало
            min(model_declared_output_limit, calculated_max_output_tokens)
        )
        print(f"[OpenRouterTranslator] Рассчитанный output_token_limit для API: {final_output_token_limit} (Общий контекст: {model_total_context_limit}, Входной промпт: ~{input_prompt_tokens} токенов)")
        # --- КОНЕЦ НОВОГО ИЗМЕНЕНИЯ ---

        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}], # Используем формат messages
            "max_tokens": final_output_token_limit, # ИЗМЕНЕНИЕ: Используем динамический лимит
            "temperature": 0.5, # УСТАНОВКА ТЕМПЕРАТУРЫ
            "stream": False # Убеждаемся, что не ждем стриминг
        }

        # --- Retry logic for 429 errors ---
        max_retries = 5
        retry_delay_seconds = 5 # Initial delay

        for attempt in range(max_retries):
            try:
                print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries})...")
                # ИЗМЕНЕНИЕ: Отправляем запрос на /chat/completions с форматом messages
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
                         choice = response_json['choices'][0]
                         finish_reason = choice.get('finish_reason')
                         print(f"[OpenRouterTranslator] finish_reason: {finish_reason}") # ДОБАВЛЕНО ЛОГИРОВАНИЕ finish_reason

                         # --- Добавляем логирование информации об использовании токенов ---
                         if 'usage' in response_json:
                             usage = response_json['usage']
                             prompt_t = usage.get('prompt_tokens', 0) # Теперь это точное количество токенов, которые мы отправили
                             completion_t = usage.get('completion_tokens', 0)
                             total_t = usage.get('total_tokens', 'N/A')
                             print(f"[OpenRouterTranslator] Использование токенов: Вход={prompt_t}, Выход={completion_t}, Всего={total_t}")
                         # --- Конец логирования токенов ---

                         # --- НОВАЯ ЭВРИСТИКА: Проверка на потенциальное обрезание ---
                         if operation_type == 'translate' and finish_reason == 'stop':
                             input_char_len = len(text) # Длина оригинального текста чанка (для логов и оценки)
                             output_content = ""
                             if 'message' in choice and 'content' in choice['message']:
                                 output_content = choice['message']['content'].strip()
                             elif 'text' in choice:
                                 output_content = choice['text'].strip()
                             
                             is_truncated = False
                             if not _ends_with_complete_sentence(output_content):
                                 is_truncated = True
                                 print(f"[OpenRouterTranslator] Предупреждение: Потенциальное обрезание обнаружено. "
                                       f"finish_reason: '{finish_reason}', вывод заканчивается неполным предложением. "
                                       f"Длина входа (симв): {input_char_len}, длина вывода (симв): {len(output_content)}. " # Логируем для информации
                                       f"Возвращаем TRUNCATED_RESPONSE_ERROR.")
                             # Добавляем новую токен-ориентированную проверку длины
                             elif 'usage' in response_json and prompt_t > 0: # Убеждаемся, что prompt_t есть и не 0
                                 # Оцениваем количество токенов исходного текста на основе токенов всего промпта и соотношения символов
                                 estimated_source_text_tokens = 0
                                 if total_prompt_char_length > 0:
                                     # ИСПРАВЛЕНИЕ: Вычисляем оценочную длину символов только для текста в промпте
                                     # Если estimated_non_text_char_length больше total_prompt_char_length, то текст вообще не влез.
                                     # Или если total_prompt_char_length == estimated_non_text_char_length, то текст пустой.
                                     # Если total_prompt_char_length - estimated_non_text_char_length <= 0, то текст либо отсутствует, либо
                                     # его длина настолько мала, что не является значимой частью промпта.
                                     
                                     # Более надежная оценка: доля символов текста в общем промпте
                                     char_ratio_of_text_in_prompt = input_char_len / total_prompt_char_length
                                     estimated_source_text_tokens = int(prompt_t * char_ratio_of_text_in_prompt)
                                 else: # Если общий промпт пуст (крайне маловероятно), то текст = prompt_t
                                     estimated_source_text_tokens = prompt_t

                                 # Если количество выходных токенов значительно меньше оцененных токенов исходного текста
                                 if completion_t < estimated_source_text_tokens * 0.80: # ИЗМЕНЕНИЕ: Сравниваем с estimated_source_text_tokens
                                     is_truncated = True
                                     print(f"[OpenRouterTranslator] Предупреждение: Потенциальное обрезание обнаружено. "
                                           f"finish_reason: '{finish_reason}', выходные токены ({completion_t}) значительно меньше оцененных токенов исходного текста ({estimated_source_text_tokens}) (<80%). "
                                           f"Возвращаем TRUNCATED_RESPONSE_ERROR.")
                             
                             if is_truncated:
                                 return TRUNCATED_RESPONSE_ERROR # Возвращаем новую ошибку
                         # --- КОНЕЦ НОВОЙ ЭВРИСТИКИ ---

                         if 'message' in choice and 'content' in choice['message']:
                             print("[OpenRouterTranslator] Ответ получен в формате message.content")
                             return choice['message']['content'].strip()
                         elif 'text' in choice:
                              print("[OpenRouterTranslator] Ответ получен в формате text")
                              return choice['text'].strip()
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
        model_name = "meta-llama/llama-4-maverick:free"

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

def get_models_list(show_all_models: bool = False) -> List[Dict[str, Any]]:
    """
    Возвращает отсортированный список моделей с кэшированием.
    Принимает флаг `show_all_models` для отключения фильтрации бесплатных моделей.
    Кэш обновляется при первом вызове или по истечении TTL.
    """
    global _cached_models, _model_list_last_update

    current_time = time.time()
    cache_key = "all" if show_all_models else "free"

    # Проверяем, есть ли кэш и не истек ли его срок
    if _cached_models.get(cache_key) is not None and _model_list_last_update is not None and (current_time - _model_list_last_update) < _MODEL_LIST_CACHE_TTL:
        print(f"[get_models_list] Возвращаем кэшированный список моделей (режим: {cache_key}).")
        return _cached_models[cache_key]

    # Если один кэш есть, а другой нет, не делаем полный запрос заново, если TTL не истек
    # Вместо этого, если возможно, сгенерируем один список из другого
    if _model_list_last_update is not None and (current_time - _model_list_last_update) < _MODEL_LIST_CACHE_TTL:
        # Если нужен список бесплатных, а есть полный - фильтруем из полного
        if cache_key == "free" and _cached_models.get("all") is not None:
            print("[get_models_list] Генерируем 'free' список из кэшированного 'all'.")
            all_models = _cached_models["all"]
            google_models = [m for m in all_models if m.get('source') == 'google']
            
            openrouter_free_models = []
            for model in all_models:
                if model.get('source') == 'openrouter':
                    pricing = model.get('pricing', {})
                    prompt_cost = float(pricing.get('prompt', 1))
                    completion_cost = float(pricing.get('completion', 1))
                    if prompt_cost == 0.0 and completion_cost == 0.0:
                        openrouter_free_models.append(model)

            filtered_list = google_models + sorted(openrouter_free_models, key=lambda x: x.get('display_name', '').lower())
            _cached_models['free'] = filtered_list
            return filtered_list
            
        # Если нужен полный, а есть только бесплатный, то придется делать запрос заново.
        # Эта ветка покрывается общей логикой ниже.

    print(f"[get_models_list] Кэш списка моделей (режим: {cache_key}) отсутствует или устарел. Получаем с API...")
    google_models = []
    openrouter_all_models_from_api = []
    
    # Получаем модели от Google (они всегда одинаковы)
    try:
        google_translator = GoogleTranslator()
        google_models = google_translator.get_available_models()
        print(f"Получено {len(google_models)} моделей от Google API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей Google: {e}")

    # Получаем ВСЕ модели от OpenRouter
    try:
        openrouter_translator = OpenRouterTranslator()
        openrouter_all_models_from_api = openrouter_translator.get_available_models()
        print(f"Получено {len(openrouter_all_models_from_api)} всего моделей от OpenRouter API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей OpenRouter: {e}")

    # --- Создаем полный список ("all") ---
    # Создаем set с "базовыми" именами моделей от Google для быстрой проверки.
    google_base_model_names = {model['name'].split('/')[-1] for model in google_models if isinstance(model, dict) and 'name' in model}
    
    unique_openrouter_models = []
    for model in openrouter_all_models_from_api:
        model_name = model.get('name', '')
        base_name = model_name.split('/')[-1]
        if base_name not in google_base_model_names:
            unique_openrouter_models.append(model)
            
    # Собираем и кэшируем полный список
    combined_list_all = google_models + sorted(unique_openrouter_models, key=lambda x: x.get('display_name', '').lower())
    _cached_models['all'] = combined_list_all
    
    # --- Теперь создаем отфильтрованный список ("free") из полного ---
    openrouter_free_models = []
    for model in unique_openrouter_models: # Используем уже отфильтрованный от дублей Google список
        pricing = model.get('pricing', {})
        try:
            prompt_cost = float(pricing.get('prompt', 1))
            completion_cost = float(pricing.get('completion', 1))
            if prompt_cost == 0.0 and completion_cost == 0.0:
                openrouter_free_models.append(model)
        except (ValueError, TypeError):
             continue # Игнорируем модели с некорректным прайсингом
             
    combined_list_free = google_models + sorted(openrouter_free_models, key=lambda x: x.get('display_name', '').lower())
    _cached_models['free'] = combined_list_free
    
    # Обновляем время кэширования
    _model_list_last_update = current_time
    print("[get_models_list] Списки моделей (all и free) успешно получены и закэшированы.")
    
    # Возвращаем запрошенный список
    return _cached_models[cache_key]

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
    # Сначала проверяем самый полный кэш ("all"), затем "free".
    models = _cached_models.get("all")
    if models is None:
        models = _cached_models.get("free")

    # Если кэш еще не загружен или пуст, принудительно загружаем
    if models is None or not models:
         # print("[get_context_length] Предупреждение: Кэш моделей не загружен или пуст. Принудительно загружаем.") # Убираем print
         models = get_models_list() # Вызовет с show_all_models=False по умолчанию

         if not models:
             print(f"[get_context_length] Ошибка: Не удалось загрузить список моделей для '{model_name}'. Используем дефолт токенов.")
             return 8000 # Дефолтное значение в токенах

    for model in models:
        # Для OpenRouter, input_token_limit - это context_length (общий лимит контекста)
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
             return 8000 # Дефолтное значение в токенов

    print(f"[get_context_length] Предупреждение: Модель '{model_name}' не найдена в закэшированном списке. Используем дефолт токенов.")
    return 8000 # Дефолтное значение в токенах

def get_model_output_token_limit(model_name: str) -> int:
    """
    Возвращает лимит выходных токенов для данной модели.
    Использует закэшированный список моделей.
    Возвращает дефолтное значение (например, 2048 токенов), если модель не найдена
    или лимит недоступен.
    """
    # Сначала проверяем самый полный кэш ("all"), затем "free".
    models = _cached_models.get("all")
    if models is None:
        models = _cached_models.get("free")

    if models is None or not models:
        print(f"[get_model_output_token_limit] Предупреждение: Кэш моделей не загружен или пуст. Принудительно загружаем.")
        models = get_models_list()
        if not models:
            print(f"[get_model_output_token_limit] Ошибка: Не удалось загрузить список моделей для '{model_name}'. Используем дефолт вых. токенов.")
            return 2048 # Дефолтное значение в токенах

    for model in models:
        if isinstance(model, dict) and model.get('name') == model_name and 'output_token_limit' in model:
            token_limit = model['output_token_limit']
            if isinstance(token_limit, (int, float)) and token_limit != 'N/A':
                return int(token_limit)
            else:
                print(f"[get_model_output_token_limit] Предупреждение: Нечисловой лимит вых. токенов '{token_limit}' для модели '{model_name}'. Используем дефолт вых. токенов.")
                return 2048 # Дефолтное значение в токенах
        elif isinstance(model, dict) and model.get('name') == model_name and 'output_token_limit' not in model:
            print(f"[get_model_output_token_limit] Предупреждение: Для модели '{model_name}' отсутствует 'output_token_limit'. Используем дефолт вых. токенов.")
            return 2048 # Дефолтное значение в токенов

    print(f"[get_model_output_token_limit] Предупреждение: Модель '{model_name}' не найдена в закэшированном списке. Используем дефолт вых. токенов.")
    return 2048 # Дефолтное значение в токенах

# --- END OF FILE translation_module.py ---