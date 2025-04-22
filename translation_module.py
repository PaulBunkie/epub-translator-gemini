# --- START OF FILE translation_module.py ---

import google.generativeai as genai
import os
import re
# Убрали импорт g

# Константа для обозначения ошибки лимита контекста
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

def configure_api():
    """Настраивает API ключ из переменной окружения."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Не установлена переменная окружения GOOGLE_API_KEY")
    genai.configure(api_key=api_key)
    print("API ключ успешно сконфигурирован.")

# --- translate_chunk() с полными инструкциями и аргументом prompt_ext ---
def translate_chunk(model, text, target_language="russian", previous_context="", prompt_ext=None):
    """
    Переводит один кусок текста с использованием предоставленной модели.
    Возвращает переведенный текст или флаг ошибки контекста.
    Использует английский язык для промпта с улучшенными инструкциями.
    Использует переданный prompt_ext для дополнительных инструкций.
    """
    # --- Формирование базовых инструкций ---
    prompt_lines = [
        # --- НАЧАЛО ПОЛНЫХ ИНСТРУКЦИЙ ---
        f"You are a professional literary translator translating a book for a {target_language}-speaking audience. Your goal is to provide a high-quality, natural-sounding translation into {target_language}, adhering to the following principles:",
        "- Perform a literary translation, preserving the author's original style, tone, and nuances.",
        "- Maintain consistency in terminology, character names, and gender portrayal *within this entire response*.",
        "- Avoid softening strong language unless culturally necessary.",
        f"- Translate common abbreviations (like 'e.g.', 'i.e.', 'CIA') according to their established {target_language} equivalents.",
        "- Keep uncommon or fictional abbreviations/acronyms (e.g., KPS) in their original form.",
        "- For neologisms or compound words, find accurate and stylistically appropriate {target_language} equivalents and use them consistently *within this response*.",
        f"{' - When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.' if target_language.lower() == 'russian' else ''}",
        # Инструкция про сноски
        f"""- If clarification is needed for a {target_language} reader (cultural notes, untranslatable puns, proper names, etc.), use translator's footnotes.
  - **Format:** Insert a sequential footnote marker directly after the word/phrase.
    - **Preferred format:** Use superscript numbers (like ¹, ², ³).
    - **Alternative format (if superscript is not possible):** Use numbers in square brackets (like [1], [2], [3]).
  - **Content:** At the very end of the translated section, add a separator ('---') and a heading ('{'Примечания переводчика' if target_language.lower() == 'russian' else 'Translator Notes'}'). List all notes sequentially by their marker (e.g., '¹ Explanation.' or '[1] Explanation.').
  - Use footnotes sparingly."""
        # --- КОНЕЦ ПОЛНЫХ ИНСТРУКЦИЙ ---
    ]

    # --- Добавление пользовательских инструкций из аргумента prompt_ext ---
    if prompt_ext and prompt_ext.strip():
        print(f"  [translate_chunk] Добавляем пользовательские инструкции (prompt_ext) к промпту (длина: {len(prompt_ext)}).")
        prompt_lines.extend([
            "\n---",
            "ADDITIONAL INSTRUCTIONS (Apply if applicable, follow strictly for names and terms defined here):",
            prompt_ext, # Используем аргумент функции
            "---"
        ])
    # --- КОНЕЦ Добавления ---

    # Добавляем контекст и текст для перевода
    prompt_lines.extend([
        ("\nPrevious Context (use for style and recent terminology reference):\n" + previous_context) if previous_context else "",
        "\nText to Translate:",
        text,
        "\nTranslation:"
    ])
    prompt = "\n".join(filter(None, prompt_lines))

    if not text.strip(): return ""
    try:
        print(f"Отправка запроса к модели {model.model_name}...")
        # Отладка промпта (можно раскомментировать при необходимости)
        # print("\n" + "="*20 + " FINAL PROMPT SENT TO MODEL " + "="*20)
        # print(prompt)
        # print("="*20 + " END FINAL PROMPT " + "="*20 + "\n")

        response = model.generate_content(prompt)
        raw_text = getattr(response, 'text', None)

        # Обработка ответа и ошибок (краткая версия для примера)
        if raw_text is not None:
             # Проверить finish_reason если нужно (MAX_TOKENS, SAFETY)
             # finish_reason = getattr(response.candidates[0], 'finish_reason', None) if response.candidates else None
             # if finish_reason == 'MAX_TOKENS': print("WARN: MAX_TOKENS")
             # elif finish_reason == 'SAFETY': print("ERROR: SAFETY"); return None
             return raw_text
        else:
             # Проверить prompt_feedback на блокировку
             # block_reason = getattr(response.prompt_feedback, 'block_reason', None) if hasattr(response, 'prompt_feedback') else None
             # if block_reason: print(f"ERROR: Prompt Blocked - {block_reason}"); return None
             print("ОШИБКА: Ответ API не содержит текста.")
             return None
    except Exception as e:
        error_text = str(e).lower()
        context_keywords = ["context window", "token limit", "maximum input length", "превышен лимит токенов", "request payload size exceeds the limit", "resource exhausted", "limit exceeded", "400 invalid argument"]
        is_likely_context_error = False
        if "400 invalid argument" in error_text and "token" in error_text: is_likely_context_error = True
        elif any(keyword in error_text for keyword in context_keywords): is_likely_context_error = True
        if is_likely_context_error: print(f"ОШИБКА: Лимит контекста? {e}"); return CONTEXT_LIMIT_ERROR
        else: print(f"ОШИБКА: Неизвестная ошибка перевода: {e}"); import traceback; traceback.print_exc(); return None


# --- translate_text() с передачей prompt_ext ---
def translate_text(text_to_translate, target_language="russian", model_name="gemini-1.5-flash", prompt_ext=None):
    """
    Основная функция для перевода строки текста.
    Передает prompt_ext в функцию перевода чанков.
    """
    print(f"Инициализация модели {model_name} для перевода...")
    try: model = genai.GenerativeModel(model_name)
    except Exception as e: print(f"ОШИБКА: Инициализация модели: {e}"); return None

    CHUNK_SIZE_LIMIT_CHARS = 20000
    text_len = len(text_to_translate)
    print(f"Проверка длины текста: {text_len} симв. Лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} симв.")

    if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1:
         print("Пробуем перевод целиком...")
         result = translate_chunk(model, text_to_translate, target_language, prompt_ext=prompt_ext) # Передаем prompt_ext
         if result != CONTEXT_LIMIT_ERROR: return result
         else: print("Перевод целиком не удался (лимит контекста), переключаемся на чанки.")

    # --- Логика разбиения на чанки (без изменений) ---
    print(f"Текст длинный ({text_len} симв.), разбиваем на чанки...")
    paragraphs = text_to_translate.split('\n\n'); chunks = []; current_chunk_paragraphs = []; current_chunk_len = 0
    for i, p in enumerate(paragraphs):
        p_clean = p.strip(); p_len = len(p_clean)
        if not p_clean: continue
        separator_len = 2 if current_chunk_paragraphs else 0
        if p_len > CHUNK_SIZE_LIMIT_CHARS: # Параграф слишком длинный
            if current_chunk_paragraphs: chunks.append("\n\n".join(current_chunk_paragraphs)); print(f"  Создан чанк {len(chunks)}...");
            current_chunk_paragraphs = []; current_chunk_len = 0
            sentences = re.split(r'(?<=[.?!])\s+', p_clean); temp_sentence_chunk = []; temp_sentence_len = 0
            for s_idx, sentence in enumerate(sentences): # Разбиваем на предложения
                 s_len = len(sentence); s_separator_len = 1 if temp_sentence_chunk else 0
                 if (temp_sentence_chunk and temp_sentence_len + s_separator_len + s_len > CHUNK_SIZE_LIMIT_CHARS) or s_idx == len(sentences) - 1:
                      if s_idx == len(sentences) - 1 and temp_sentence_len + s_separator_len + s_len <= CHUNK_SIZE_LIMIT_CHARS : temp_sentence_chunk.append(sentence); temp_sentence_len += s_separator_len + s_len
                      if temp_sentence_chunk: chunks.append(" ".join(temp_sentence_chunk)); print(f"  Создан чанк {len(chunks)} из предложений...");
                      if s_idx < len(sentences) - 1 and not (s_idx == len(sentences) - 1 and temp_sentence_len + s_separator_len + s_len <= CHUNK_SIZE_LIMIT_CHARS) : temp_sentence_chunk = [sentence]; temp_sentence_len = s_len
                      elif s_idx == len(sentences) - 1 and temp_sentence_len + s_separator_len + s_len > CHUNK_SIZE_LIMIT_CHARS : chunks.append(sentence); print(f"  Создан чанк {len(chunks)} для посл. предл..."); temp_sentence_chunk = []; temp_sentence_len = 0
                      else: temp_sentence_chunk = []; temp_sentence_len = 0
                 else: temp_sentence_chunk.append(sentence); temp_sentence_len += s_separator_len + s_len
            continue
        if current_chunk_paragraphs and current_chunk_len + separator_len + p_len > CHUNK_SIZE_LIMIT_CHARS: # Завершаем чанк
            chunks.append("\n\n".join(current_chunk_paragraphs)); print(f"  Создан чанк {len(chunks)} (длина: {current_chunk_len} симв.)")
            current_chunk_paragraphs = [p_clean]; current_chunk_len = p_len
        else: current_chunk_paragraphs.append(p_clean); current_chunk_len += separator_len + p_len # Добавляем в чанк
    if current_chunk_paragraphs: chunks.append("\n\n".join(current_chunk_paragraphs)); print(f"  Создан чанк {len(chunks)} (длина: {current_chunk_len} симв.)")
    if not chunks: print("ОШИБКА: Не удалось создать чанки!"); return None

    # --- Цикл перевода чанков ---
    print(f"Текст разбит на {len(chunks)} чанков.")
    full_translated_text = []; last_successful_translation = ""
    for i, chunk in enumerate(chunks):
        print(f"-- Перевод чанка {i+1}/{len(chunks)} ({len(chunk)} симв.)...")
        context_fragment = " ".join(last_successful_translation.split()[-100:]) if last_successful_translation else ""
        translated_chunk = translate_chunk(model, chunk, target_language, previous_context=context_fragment, prompt_ext=prompt_ext) # Передаем prompt_ext
        if translated_chunk == CONTEXT_LIMIT_ERROR: print(f"ОШИБКА ЛИМИТА КОНТЕКСТА на чанке {i+1}."); return CONTEXT_LIMIT_ERROR
        elif translated_chunk is None: print(f"ОШИБКА ПЕРЕВОДА чанка {i+1}."); return None
        else: full_translated_text.append(translated_chunk); last_successful_translation = translated_chunk

    print("Сборка переведенных чанков...")
    return "\n\n".join(full_translated_text)

# --- get_models_list() без изменений ---
def get_models_list():
    """Возвращает список доступных моделей (объектов/словарей)."""
    models_data = []
    try:
        print("Запрос списка моделей из API...")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods: models_data.append({'name': m.name, 'display_name': m.display_name, 'input_token_limit': getattr(m, 'input_token_limit', 'N/A'), 'output_token_limit': getattr(m, 'output_token_limit', 'N/A')})
        print(f"Найдено {len(models_data)} подходящих моделей."); models_data.sort(key=lambda x: x['display_name'])
        return models_data
    except Exception as e: print(f"ОШИБКА: Не удалось получить список моделей: {e}"); return None

# --- END OF FILE translation_module.py ---