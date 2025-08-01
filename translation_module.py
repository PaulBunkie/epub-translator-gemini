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
- Preserve exact Markdown heading levels. Do not change #/##/### levels or convert plain text into headings.
- Keep *italic* and **bold** Markdown formatting inline, exactly as in the original. Do not introduce line breaks instead of or around italicized text.
- Do not add any titles, headers, or metadata (e.g., "### Literary translation", "Translation:", etc.) that are not present in the source text. Start directly with the translation.
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
        return f"{chr(10)}---{chr(10)}{title}:{chr(10)}{content}{chr(10)}---"
    return ""

class BaseTranslator(ABC):
    """
    Базовый абстрактный класс для всех трансляторов.
    
    TODO: Перенести логику разбивки на чанки в базовый класс:
    1. Сейчас разбивка реализована только в OpenRouterTranslator._get_next_chunk
    2. Но даже там не используется в translate_chunk
    3. В GoogleTranslator вообще нет разбивки
    4. Нужно:
       - Перенести _get_next_chunk в базовый класс
       - Сделать его общей логикой для всех провайдеров
       - Учитывать особенности каждого провайдера (разные лимиты, форматы)
       - Использовать в translate_chunk для всех провайдеров
    """
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
                        'source': 'google',
                        'is_free': True  # Все модели Google бесплатные
                    })
            return models
        except Exception as e:
            print(f"Ошибка при получении списка моделей Google: {e}")
            return []

    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                          model_name: str = None, prompt_ext: Optional[str] = None,
                          operation_type: str = 'translate') -> Optional[str]:
        """
        Основная функция для перевода текста с использованием Google.
        Google API обрабатывает большие тексты самостоятельно, поэтому логика чанкинга здесь не нужна.
        """
        return self.translate_chunk(
            model_name=model_name,
            text=text_to_translate,
            target_language=target_language,
            prompt_ext=prompt_ext,
            operation_type=operation_type
        )

    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None,
                       operation_type: str = 'translate') -> Optional[str]:
        """
        Переводит один чанк текста с использованием Google Gemini.
        Обрабатывает ошибки API, включая лимит контекста.
        """
        # --- ИЗМЕНЕНИЕ: Логика вынесена из `translate_text` в `translate_chunk` ---
        # 1. Формируем промпт
        final_prompt, non_text_len = self._build_prompt(
            operation_type, target_language, text, previous_context, prompt_ext
        )
        # 2. Проверяем лимит контекста (примерный)
        model_context_limit = get_context_length(model_name)
        # Для Gemini считаем в токенах, но для простоты используем символы с коэффициентом
        # ~4 символа на токен
        estimated_tokens = len(final_prompt) / 3.5
        if estimated_tokens > model_context_limit:
             print(f"  [Google] ОШИБКА: Расчетные токены ({int(estimated_tokens)}) > лимит модели ({model_context_limit}) для '{model_name}'.")
             return CONTEXT_LIMIT_ERROR

        # 3. Выполняем запрос к API
        try:
            print(f"  [Google] Отправка запроса к '{model_name}' (длина: {len(text)} симв.)...")
            model = genai.GenerativeModel(model_name)
            # --- ИЗМЕНЕНИЕ: Убираем safety_settings, если они вызывают проблемы ---
            # Убраны 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_HARASSMENT'
            safety_settings = {
                 'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'block_none',
                 'HARM_CATEGORY_DANGEROUS_CONTENT': 'block_none'
            }
            response = model.generate_content(final_prompt, safety_settings=safety_settings)
            
            # --- ПРОВЕРКА НА ПУСТОЙ ОТВЕТ ---
            # response.text может быть пустой строкой, если модель ничего не вернула.
            # response.prompt_feedback.block_reason может указать причину.
            if not response.text and response.prompt_feedback.block_reason:
                block_reason = response.prompt_feedback.block_reason
                print(f"  [Google] ОШИБКА: Ответ заблокирован. Причина: '{block_reason}'.")
                # Можно вернуть специальную ошибку или None
                return None
            
            print(f"  [Google] Ответ получен от '{model_name}'.")
            return response.text

        except Exception as e:
            print(f"  [Google] КРИТИЧЕСКАЯ ОШИБКА при вызове API Gemini: {e}")
            # Здесь можно добавить логику повторных попыток для сетевых ошибок
            return None
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

class OpenRouterTranslator(BaseTranslator):
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
    # Таймаут для запросов к API OpenRouter в секундах
    API_TIMEOUT = 180

    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("Не установлена переменная окружения OPENROUTER_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def get_available_models(self) -> List[Dict[str, Any]]:
        """
        Возвращает список доступных моделей от OpenRouter.
        Теперь также добавляет флаг is_free.
        """
        try:
            response = requests.get(f"{self.OPENROUTER_API_URL}/models", timeout=self.API_TIMEOUT)
            response.raise_for_status()
            models_data = response.json().get('data', [])
            
            processed_models = []
            for model in models_data:
                pricing = model.get('pricing', {})
                is_free = float(pricing.get('prompt', 1)) == 0.0 and float(pricing.get('completion', 1)) == 0.0
                
                processed_models.append({
                    'name': model['id'],
                    'display_name': model.get('name', model['id']),
                    'input_token_limit': model.get('context_length'),  # Переименовываем context_length в input_token_limit
                    'output_token_limit': model.get('top_provider', {}).get('max_completion_tokens'),  # Переименовываем max_completion_tokens в output_token_limit
                    'pricing': pricing,
                    'is_free': is_free,
                    'source': 'openrouter'
                })
            return processed_models
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении списка моделей OpenRouter: {e}")
            return []

    def _bubble_chunk_text(self, text: str, target_chunk_size: int = 30000) -> List[str]:
        """
        Метод пузырька: делим текст пополам по границам предложений и абзацев, пока чанки не станут < target_chunk_size
        """
        if not text.strip():
            return []
        
        chunks = [text]
        
        while True:
            new_chunks = []
            need_splitting = False
            
            for chunk in chunks:
                if len(chunk) > target_chunk_size:
                    # Ищем середину текста
                    mid = len(chunk) // 2
                    
                    # Приоритет разбиения:
                    # 1. Двойной перенос строки (граница абзаца)
                    # 2. Одинарный перенос строки
                    # 3. Граница предложения (точка, восклицательный знак, вопросительный знак)
                    # 4. Середина текста
                    
                    split_point = -1
                    
                    # 1. Ищем ближайший разрыв абзаца (двойной перенос строки) до середины
                    split_point = chunk.rfind('\n\n', 0, mid)
                    
                    if split_point == -1:
                        # 2. Если нет двойного переноса, ищем одинарный
                        split_point = chunk.rfind('\n', 0, mid)
                    
                    if split_point == -1:
                        # 3. Если нет переносов строк, ищем границу предложения
                        # Ищем последнюю точку, восклицательный или вопросительный знак до середины
                        for i in range(mid, 0, -1):
                            if chunk[i-1] in '.!?':
                                # Проверяем, что после знака препинания есть пробел или конец строки
                                if i >= len(chunk) or chunk[i] in ' \n':
                                    split_point = i
                                    break
                    
                    if split_point == -1:
                        # 4. Если ничего не найдено, используем середину
                        split_point = mid
                    
                    # Убираем лишние пробелы в начале второго чанка
                    first_chunk = chunk[:split_point].rstrip()
                    second_chunk = chunk[split_point:].lstrip()
                    
                    if first_chunk:
                        new_chunks.append(first_chunk)
                    if second_chunk:
                        new_chunks.append(second_chunk)
                    
                    need_splitting = True
                else:
                    new_chunks.append(chunk)
            
            chunks = new_chunks
            if not need_splitting:
                break
        
        return chunks

    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                       model_name: str = None, prompt_ext: Optional[str] = None,
                       operation_type: str = 'translate') -> Optional[str]:
        """
        Основная функция для перевода текста с использованием OpenRouter.
        Обрабатывает разделение на чанки методом пузырька, повторные попытки и ошибки API.
        """
        # Получаем лимит контекста для модели в токенах
        context_token_limit = get_context_length(model_name)
        
        # Грубая оценка: 1 токен ~ 3.5-4 символа. Возьмем 3 для запаса.
        context_chars_limit = context_token_limit * 3
        
        # Устанавливаем лимит для чанка, оставляя ~4000 символов для промпта и ответа.
        CHUNK_SIZE_LIMIT_CHARS = context_chars_limit - 4000
        if CHUNK_SIZE_LIMIT_CHARS <= 0:
            CHUNK_SIZE_LIMIT_CHARS = context_chars_limit // 2
        
        # --- ПРИНУДИТЕЛЬНОЕ ОГРАНИЧЕНИЕ: максимум 30000 символов ТОЛЬКО ДЛЯ ПЕРЕВОДА ---
        if operation_type == 'translate':
            CHUNK_SIZE_LIMIT_CHARS = min(CHUNK_SIZE_LIMIT_CHARS, 30000)
        # --- КОНЕЦ ПРИНУДИТЕЛЬНОГО ОГРАНИЧЕНИЯ ---
        
        # Разбиваем текст на чанки методом пузырька
        chunks = self._bubble_chunk_text(text_to_translate, CHUNK_SIZE_LIMIT_CHARS)
        if not chunks:
            print("[OpenRouter] Нет чанков для перевода.")
            return None
        
        print(f"[OpenRouter] Текст разбит на {len(chunks)} чанков методом пузырька (target_size: {CHUNK_SIZE_LIMIT_CHARS} символов).")
        
        final_translation = []
        previous_chunk_context = ""
        MAX_CHUNK_RETRIES = 2
        
        for i, chunk in enumerate(chunks):
            chunk_stripped = chunk.strip()
            chunk_length = len(chunk_stripped)
            print(f"[OpenRouter] Перевод чанка {i+1}/{len(chunks)} (длина: {chunk_length} символов).")
            
            max_chunk_retries = MAX_CHUNK_RETRIES
            attempt = 0
            translated_chunk = None
            
            while attempt <= max_chunk_retries:
                translated_chunk = self.translate_chunk(
                    model_name=model_name,
                    text=chunk,
                    target_language=target_language,
                    previous_context=previous_chunk_context,
                    prompt_ext=prompt_ext,
                    operation_type=operation_type
                )
                
                if translated_chunk is None:
                    print(f"[OpenRouter] Ошибка перевода чанка {i+1} (попытка {attempt+1}/{max_chunk_retries+1}). Возможно finish_reason: error.")
                    attempt += 1
                    if attempt > max_chunk_retries:
                        print(f"[OpenRouter] Ошибка: чанк {i+1} не удалось перевести после {max_chunk_retries+1} попыток.")
                        break
                    time.sleep(2)
                    continue
                
                # Проверка на специальные ошибки
                if translated_chunk == TRUNCATED_RESPONSE_ERROR:
                    print(f"[OpenRouter] Обнаружена ошибка TRUNCATED_RESPONSE_ERROR для чанка {i+1}. Попытка {attempt+1}/{max_chunk_retries+1}.")
                    attempt += 1
                    if attempt > max_chunk_retries:
                        print(f"[OpenRouter] Ошибка: чанк {i+1} остался обрезанным после {max_chunk_retries+1} попыток.")
                        break
                    time.sleep(2)
                    continue
                
                # Проверка на другие ошибки
                if translated_chunk in [CONTEXT_LIMIT_ERROR, EMPTY_RESPONSE_ERROR]:
                    print(f"[OpenRouter] Ошибка обработки чанка {i+1}: {translated_chunk}")
                    break
                
                # Успешный перевод
                break
            
            # Обработка результата
            if translated_chunk and translated_chunk not in [CONTEXT_LIMIT_ERROR, EMPTY_RESPONSE_ERROR, TRUNCATED_RESPONSE_ERROR]:
                final_translation.append(translated_chunk)
                # Обновляем контекст для следующего чанка
                previous_chunk_context = translated_chunk[-500:]  # Последние 500 символов
            else:
                # Добавляем оригинальный текст с пометкой об ошибке
                error_msg = translated_chunk if translated_chunk else "None"
                final_translation.append(f"{chr(10)}{chr(10)}[ОШИБКА ПЕРЕВОДА: {error_msg}]{chr(10)}{chunk}{chr(10)}[КОНЕЦ ОШИБКИ]{chr(10)}{chr(10)}")
        
        if not final_translation:
            return None
        
        # Объединяем чанки с двойным переносом строки для сохранения структуры параграфов
        return "\n\n".join(final_translation)

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
        
        # Если max_completion_tokens не указан, используем половину размера контекста
        if not model_declared_output_limit:
            model_declared_output_limit = model_total_context_limit // 2
            print(f"[OpenRouterTranslator] max_completion_tokens не указан, используем половину контекста: {model_declared_output_limit}")
        
        # Оценим длину промпта в токенах (грубо 3 символа на токен)
        input_prompt_tokens = len(prompt) // 3
        
        # Максимально допустимое количество токенов для вывода
        # = Общий лимит контекста - токены входного промпта - небольшой буфер
        calculated_max_output_tokens = model_total_context_limit - input_prompt_tokens - 100 # Буфер 100 токенов
        
        # Окончательный лимит вывода: минимум из заявленного моделью и рассчитанного
        final_output_token_limit = min(model_declared_output_limit, calculated_max_output_tokens)
        
        print(f"[OpenRouterTranslator] Рассчитанный output_token_limit для API: {final_output_token_limit} (Общий контекст: {model_total_context_limit}, Входной промпт: ~{input_prompt_tokens} токенов)")
        # --- КОНЕЦ НОВОГО ИЗМЕНЕНИЯ ---

        data = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}], # Используем формат messages
            "max_tokens": final_output_token_limit, # ИЗМЕНЕНИЕ: Используем динамический лимит
            "temperature": 0.5, # УСТАНОВКА ТЕМПЕРАТУРЫ
            "stream": False #, # Убеждаемся, что не ждем стриминг
            #"reasoning": {
            #    "exclude": True
            #},
        }

        # --- Retry logic for 429 errors ---
        max_retries = 5
        retry_delay_seconds = 5 # Initial delay

        for attempt in range(max_retries):
            try:
                print(f"[OpenRouterTranslator] Отправка запроса на OpenRouter API (попытка {attempt + 1}/{max_retries})...")
                json_str = json.dumps(data, ensure_ascii=False)
                # Проверяем, что JSON валидный
                json.loads(json_str)
                response = requests.post(f"{self.OPENROUTER_API_URL}/chat/completions", headers=headers, data=json_str)
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

                         # --- ПРОВЕРКА finish_reason на ошибку ---
                         if finish_reason == 'error':
                             print(f"[OpenRouterTranslator] ОШИБКА: Модель вернула finish_reason='error'. Возвращаем None.")
                             return None
                         # --- КОНЕЦ ПРОВЕРКИ ---

                         # --- Добавляем логирование информации об использовании токенов ---
                         if 'usage' in response_json:
                             usage = response_json['usage']
                             prompt_t = usage.get('prompt_tokens', 0) # Теперь это точное количество токенов, которые мы отправили
                             completion_t = usage.get('completion_tokens', 0)
                             total_t = usage.get('total_tokens', 'N/A')
                             print(f"[OpenRouterTranslator] Использование токенов: Вход={prompt_t}, Выход={completion_t}, Всего={total_t}")
                         # --- Конец логирования токенов ---

                         # --- УБРАНА ЭВРИСТИКА сравнения доли чанка во всём промпте. Оставляем только прямое сравнение длины исходного текста и перевода, если нужно.
                         is_truncated = False
                         if 'usage' in response_json and prompt_t > 0 and operation_type == 'translate':
                             # Честная проверка: сравниваем длину перевода и исходного текста (оба только текст)
                             input_char_len = len(text)
                             output_content = ""
                             if 'message' in choice and 'content' in choice['message']:
                                 output_content = choice['message']['content'].strip()
                             elif 'text' in choice:
                                 output_content = choice['text'].strip()
                             output_char_len = len(output_content)
                             if output_char_len < input_char_len * 0.8:
                                 is_truncated = True
                                 print(f"[OpenRouterTranslator] Предупреждение: Перевод ({output_char_len} симв.) значительно короче исходного текста ({input_char_len} симв.) (<80%). Возвращаем TRUNCATED_RESPONSE_ERROR.")
                         if is_truncated:
                             return TRUNCATED_RESPONSE_ERROR

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

    def _build_prompt(
        self,
        operation_type: str,
        target_language: str,
        text: str,
        previous_context: str = "",
        prompt_ext: Optional[str] = None
    ) -> tuple[str, int]:
        template = PROMPT_TEMPLATES.get(operation_type)
        if not template:
            raise ValueError(f"Неизвестный тип операции: {operation_type}")

        prompt_ext_section = _format_prompt_section("ADDITIONAL INSTRUCTIONS (Apply if applicable, follow strictly for names and terms defined here)", prompt_ext)
        previous_context_section = _format_prompt_section("Previous Context (use for style and recent terminology reference)", previous_context)

        russian_dialogue_rule = ' - When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.' if target_language.lower() == 'russian' else ''
        translator_notes_heading = 'Примечания переводчика' if target_language.lower() == 'russian' else 'Translator Notes'

        cleaned_text = self._clean_text_for_api(text)

        temp_prompt_for_overhead_calc = template.format(
            target_language=target_language,
            text="", # Замещаем текст пустым, чтобы получить длину остального промпта
            prompt_ext_section=prompt_ext_section,
            previous_context_section=previous_context_section,
            russian_dialogue_rule=russian_dialogue_rule,
            translator_notes_heading=translator_notes_heading,
        )
        estimated_non_text_char_length = len(temp_prompt_for_overhead_calc.replace("{text}", "").strip())

        final_prompt = template.format(
            target_language=target_language,
            text=cleaned_text,
            prompt_ext_section=prompt_ext_section,
            previous_context_section=previous_context_section,
            russian_dialogue_rule=russian_dialogue_rule,
            translator_notes_heading=translator_notes_heading,
        )
        final_prompt = final_prompt.replace("\n\n\n", "\n\n").strip()
        return final_prompt, estimated_non_text_char_length

def configure_api() -> None:
    """
    Проверяет наличие необходимых ключей API в переменных окружения.
    Выбрасывает ValueError, если какой-либо из требуемых ключей отсутствует.
    """
    google_key = os.getenv("GOOGLE_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if not google_key and not openrouter_key:
        raise ValueError("Необходимо установить хотя бы одну переменную окружения: GOOGLE_API_KEY или OPENROUTER_API_KEY")
    if not openrouter_key:
        print("ПРЕДУПРЕЖДЕНИЕ: OPENROUTER_API_KEY не установлен. Модели OpenRouter не будут доступны.")
    if not google_key:
        print("ПРЕДУПРЕЖДЕНИЕ: GOOGLE_API_KEY не установлен. Модели Google не будут доступны.")


def translate_text(text_to_translate: str, target_language: str = "russian",
                  model_name: str = None, prompt_ext: Optional[str] = None,
                  operation_type: str = 'translate') -> Optional[str]:
    """
    Основная функция-диспетчер для перевода текста.
    Определяет источник модели и вызывает соответствующий класс-транслятор.
    """
    if not model_name:
        model_name = "meta-llama/llama-4-maverick:free"

    # Шаг 1: Получить ПОЛНЫЙ список моделей, чтобы найти источник по имени.
    # Бэкенд должен уметь работать с любой валидной моделью, независимо от режима UI.
    print("[translate_text] Получение полного списка моделей для определения источника...")
    available_models = get_models_list(show_all_models=True)

    if not available_models:
        print(f"ОШИБКА: Не удалось получить список моделей. Невозможно перевести.")
        return None

    # Шаг 2: Найти информацию о выбранной модели
    selected_model_info = next((m for m in available_models if isinstance(m, dict) and m.get('name') == model_name), None)

    if not selected_model_info:
        print(f"ОШИБКА: Не найдена информация о модели '{model_name}' в полном списке моделей. Невозможно перевести.")
        return None

    # Шаг 3: Определить источник и создать соответствующий экземпляр транслятора
    source = selected_model_info.get('source')
    if not source:
        print(f"ОШИБКА: В информации о модели '{model_name}' отсутствует поле 'source'.")
        return None

    try:
        if source == "google":
            translator = GoogleTranslator()
        elif source == "openrouter":
            translator = OpenRouterTranslator()
        else:
            print(f"ОШИБКА: Неизвестный источник '{source}' для модели '{model_name}'.")
            return None
    except ValueError as e:
        print(f"ОШИБКА при инициализации транслятора для источника '{source}': {e}")
        return None

    # Шаг 4: Вызвать метод translate_text у созданного экземпляра
    return translator.translate_text(
        text_to_translate,
        target_language,
        model_name,
        prompt_ext,
        operation_type
    )


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
            
            # Фильтруем бесплатные модели из полного списка
            free_models_list = [
                m for m in all_models 
                if m.get('is_free')
            ]
            
            _cached_models['free'] = free_models_list
            return free_models_list
            
        # Если нужен полный, а есть только бесплатный, то придется делать запрос заново.
        # Эта ветка покрывается общей логикой ниже.

    print(f"[get_models_list] Кэш списка моделей (режим: {cache_key}) отсутствует или устарел. Получаем с API...")
    
    # --- Получаем модели от всех провайдеров ---
    all_provider_models = []
    
    if os.getenv("GOOGLE_API_KEY"):
        try:
            google_translator = GoogleTranslator()
            google_models = google_translator.get_available_models()
            all_provider_models.extend(google_models)
            print(f"Получено {len(google_models)} моделей от Google API")
        except Exception as e:
            print(f"Ошибка при получении списка моделей Google: {e}")

    if os.getenv("OPENROUTER_API_KEY"):
        try:
            openrouter_translator = OpenRouterTranslator()
            openrouter_models = openrouter_translator.get_available_models()
            all_provider_models.extend(openrouter_models)
            print(f"Получено {len(openrouter_models)} всего моделей от OpenRouter API")
        except Exception as e:
            print(f"Ошибка при получении списка моделей OpenRouter: {e}")

    # --- Создаем и кэшируем оба списка: "all" и "free" ---
    # Кэш "all" - это все, что мы получили, отсортированное
    _cached_models['all'] = sorted(all_provider_models, key=lambda x: x.get('display_name', x.get('name', '')).lower())
    
    # Кэш "free" - это отфильтрованный список
    free_models = [
        model for model in all_provider_models
        if model.get('is_free')
    ]
    _cached_models['free'] = sorted(free_models, key=lambda x: x.get('display_name', x.get('name', '')).lower())
    
    # Обновляем время кэширования
    _model_list_last_update = current_time
    print("[get_models_list] Списки моделей (all и free) успешно получены и закэшированы.")

    # Возвращаем запрошенный список
    return _cached_models[cache_key]


def load_models_on_startup():
    """Принудительно загружает список моделей при старте приложения."""
    print("[startup] Загрузка списка моделей...")
    try:
        get_models_list(show_all_models=False)
    except Exception as e:
        print(f"[startup] Ошибка при загрузке списка моделей: {e}")


def get_context_length(model_name: str) -> int:
    """Получает максимальный размер контекста для модели."""
    all_models = get_models_list(show_all_models=True)
    model_info = next((m for m in all_models if m.get('name') == model_name), None)
    if model_info:
        # Теперь используем только input_token_limit
        if 'input_token_limit' in model_info:
            return model_info['input_token_limit']
        print(f"Предупреждение: Для модели '{model_name}' не найден ключ 'input_token_limit'. Возвращено 0.")
    return 0

def get_model_output_token_limit(model_name: str) -> int:
    """Получает максимальный размер ответа для модели."""
    all_models = get_models_list(show_all_models=True)
    model_info = next((m for m in all_models if m.get('name') == model_name), None)
    if model_info:
        # Теперь используем только output_token_limit
        output_limit = model_info.get('output_token_limit')
        if output_limit:
            return output_limit
        print(f"Предупреждение: Для модели '{model_name}' не найден ключ 'output_token_limit'. Возвращено 0.")
    return 0

# --- END OF FILE translation_module.py ---