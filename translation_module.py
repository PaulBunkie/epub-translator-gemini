import google.generativeai as genai
import os
import re 

# Константа для обозначения ошибки лимита контекста
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

def configure_api():
    """Настраивает API ключ из переменной окружения."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Не установлена переменная окружения GOOGLE_API_KEY")
    genai.configure(api_key=api_key)
    print("API ключ успешно сконфигурирован.")

def translate_chunk(model, text, target_language="russian", previous_context=""):
    """
    Переводит один кусок текста с использованием предоставленной модели.
    Возвращает переведенный текст или флаг ошибки контекста.
    Использует английский язык для промпта.
    """
    # Формируем базовый промпт на английском
    prompt_lines = [
        f"You are a professional translator. Translate the following text into {target_language}."
    ]

    # Добавляем инструкцию для диалогов, если целевой язык - русский
    # Сравниваем в нижнем регистре для надежности
    if target_language.lower() == "russian":
        prompt_lines.append("When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.")
        # Можно добавить еще правил, если нужно

    # Добавляем контекст, если он есть
    if previous_context:
        prompt_lines.extend([
            "\nPrevious Context (if available):",
            previous_context
        ])

    # Добавляем основной текст
    prompt_lines.extend([
        "\nText to Translate:",
        text,
        "\nTranslation:"
    ])

    prompt = "\n".join(prompt_lines)

    if not text.strip():
        print("Предупреждение: Попытка перевести пустой текст.")
        return "" # Возвращаем пустую строку, если текст пустой

    try:
        print(f"Отправка запроса к модели {model.model_name} (Целевой язык: {target_language})...")
        # print("--- Промпт ---") # Раскомментировать для отладки
        # print(prompt)          # Раскомментировать для отладки
        # print("--- Конец промпта ---") # Раскомментировать для отладки
        response = model.generate_content(prompt)

        # Проверка, есть ли текст в ответе
        if hasattr(response, 'text') and response.text:
             return response.text
        else:
             # Обработка случая, когда текст не сгенерирован (например, из-за фильтров)
             feedback = getattr(response, 'prompt_feedback', None)
             block_reason = getattr(feedback, 'block_reason', None)
             if block_reason:
                  print(f"ОШИБКА: Генерация заблокирована: {block_reason}")
                  # Можно добавить детализацию safety_ratings, если они есть
                  safety_ratings = getattr(response, 'candidates', [{}])[0].get('safety_ratings', [])
                  if safety_ratings:
                       print(f"  Safety Ratings: {safety_ratings}")
                  return None
             else:
                  # Если причина блокировки не указана, но текста нет
                  print("ОШИБКА: Ответ API не содержит ожидаемого текста и нет явной причины блокировки.")
                  # print(f"Полный ответ: {response}") # Раскомментировать для детальной отладки
                  return None

    except Exception as e:
        error_text = str(e).lower()
        # Ищем ключевые слова, связанные с лимитом токенов
        context_keywords = ["context window", "token limit", "maximum input length",
                            "превышен лимит токенов", "request payload size exceeds the limit",
                            "resource exhausted", "limit exceeded"] # Добавим еще варианты
        if any(keyword in error_text for keyword in context_keywords):
            print(f"ОШИБКА: Обнаружена ошибка, связанная с превышением лимита токенов! {e}")
            return CONTEXT_LIMIT_ERROR # Сообщаем об ошибке контекста
        else:
            print(f"ОШИБКА: Ошибка перевода: {e}")
            return None # Другая ошибка

def translate_text(text_to_translate, target_language="russian", model_name="gemini-1.5-flash"):
    """
    Основная функция для перевода строки текста.
    Автоматически разбивает текст на части, если он слишком длинный.
    """
    print(f"Инициализация модели {model_name} для перевода...")
    try:
        # !!! ИНИЦИАЛИЗИРУЕМ МОДЕЛЬ ЗДЕСЬ !!!
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        print(f"ОШИБКА: Не удалось инициализировать модель: {e}")
        return None

    CHUNK_SIZE_LIMIT_CHARS = 20000 # Используем 20k для теста
    text_len = len(text_to_translate)

    print(f"Проверка длины текста: {text_len} символов. Лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} символов.")

    # Попытка перевести целиком с запасом
    if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1:
         print("Текст короткий или немного больше лимита, пробуем перевод целиком...")
         # !!! ПЕРЕДАЕМ 'model' В translate_chunk !!!
         result = translate_chunk(model, text_to_translate, target_language)
         if result != CONTEXT_LIMIT_ERROR:
              return result
         else:
              print("Перевод целиком не удался (лимит контекста), принудительно переключаемся на чанки.")
              # Продолжаем ниже

    # --- Логика разбиения на чанки (версия 3) ---
    print(f"Текст длинный ({text_len} симв.) или перевод целиком не удался, разбиваем на чанки...")
    paragraphs = text_to_translate.split('\n\n')
    chunks = []
    current_chunk_paragraphs = []
    current_chunk_len = 0
    sentences_buffer = [] # Буфер для предложений из разбитого параграфа

    for i, p in enumerate(paragraphs):
        p_clean = p.strip()
        if not p_clean: continue
        p_len = len(p_clean)
        separator_len = 2 if current_chunk_paragraphs or sentences_buffer else 0

        # Обработка параграфа, который сам по себе слишком длинный
        if p_len > CHUNK_SIZE_LIMIT_CHARS:
            print(f"  Параграф {i+1} слишком длинный ({p_len} симв.), разбиваем его на предложения...")
            if current_chunk_paragraphs or sentences_buffer:
                current_chunk_paragraphs.extend(sentences_buffer)
                chunks.append("\n\n".join(current_chunk_paragraphs))
                print(f"  Создан чанк {len(chunks)} (длина: {current_chunk_len} симв.) перед большим параграфом.")
                current_chunk_paragraphs = []
                current_chunk_len = 0
                sentences_buffer = []

            sentences = re.split(r'(?<=[.?!])\s+', p_clean)
            print(f"    Разбит на ~{len(sentences)} предложений.")

            temp_sentence_chunk = []
            temp_sentence_len = 0
            for s_idx, sentence in enumerate(sentences):
                 s_len = len(sentence)
                 s_separator_len = 1 if temp_sentence_chunk else 0

                 if (temp_sentence_len + s_separator_len + s_len > CHUNK_SIZE_LIMIT_CHARS and temp_sentence_chunk) or s_idx == len(sentences) - 1:
                      if s_idx == len(sentences) - 1:
                          temp_sentence_chunk.append(sentence)
                          temp_sentence_len += s_separator_len + s_len
                      if temp_sentence_chunk:
                           chunks.append(" ".join(temp_sentence_chunk))
                           print(f"  Создан чанк {len(chunks)} (длина: {temp_sentence_len} симв.) из предложений.")
                      if s_idx < len(sentences) - 1:
                           temp_sentence_chunk = [sentence]
                           temp_sentence_len = s_len
                      else:
                           temp_sentence_chunk = []
                           temp_sentence_len = 0
                 else:
                      temp_sentence_chunk.append(sentence)
                      temp_sentence_len += s_separator_len + s_len

            current_chunk_paragraphs = []
            current_chunk_len = 0
            sentences_buffer = []
            continue

        # Обработка обычных параграфов
        if current_chunk_len + separator_len + p_len > CHUNK_SIZE_LIMIT_CHARS and (current_chunk_paragraphs or sentences_buffer):
            current_chunk_paragraphs.extend(sentences_buffer)
            chunks.append("\n\n".join(current_chunk_paragraphs))
            print(f"  Создан чанк {len(chunks)} (длина: {current_chunk_len} симв.)")
            current_chunk_paragraphs = [p_clean]
            current_chunk_len = p_len
            sentences_buffer = []
        else:
            if sentences_buffer:
                 current_chunk_paragraphs.extend(sentences_buffer)
                 sentences_buffer = []
            current_chunk_paragraphs.append(p_clean)
            current_chunk_len += separator_len + p_len

    if current_chunk_paragraphs or sentences_buffer:
        current_chunk_paragraphs.extend(sentences_buffer)
        chunks.append("\n\n".join(current_chunk_paragraphs))
        print(f"  Создан чанк {len(chunks)} (длина: {current_chunk_len} симв.)")

    if not chunks:
         print("ОШИБКА: Не удалось создать чанки из текста!")
         return None

    # --- Цикл перевода чанков ---
    print(f"Текст разбит на {len(chunks)} чанков.")
    full_translated_text = []
    last_successful_translation = ""

    for i, chunk in enumerate(chunks):
        print(f"-- Перевод чанка {i+1}/{len(chunks)} ({len(chunk)} симв.)...")
        context_fragment = " ".join(last_successful_translation.split()[-100:])
        translated_chunk = translate_chunk(model, chunk, target_language, previous_context=context_fragment)

        if translated_chunk == CONTEXT_LIMIT_ERROR:
            print(f"ОШИБКА ЛИМИТА КОНТЕКСТА на чанке {i+1}. Перевод прерван.")
            return CONTEXT_LIMIT_ERROR
        elif translated_chunk is None:
            print(f"ОШИБКА ПЕРЕВОДА чанка {i+1}. Перевод прерван.")
            return None
        else:
            full_translated_text.append(translated_chunk)
            last_successful_translation = translated_chunk
            # !!! СТРОКА С ПАУЗОЙ УДАЛЕНА !!!
            # if i < len(chunks) - 1:
            #      print("Пауза перед следующим чанком...")
            #      time.sleep(2)

    print("Сборка переведенных чанков...")
    return "\n\n".join(full_translated_text)

    print("Сборка переведенных чанков...")
    return "\n\n".join(full_translated_text)

# --- НОВАЯ ФУНКЦИЯ для получения списка моделей ---
def get_models_list():
    """
    Возвращает список доступных моделей, поддерживающих generateContent,
    в формате, удобном для JSON.
    """
    models_data = []
    try:
        print("Запрос списка моделей из API...")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                # Извлекаем только нужные данные
                models_data.append({
                    'name': m.name, # Полное имя (models/...)
                    'display_name': m.display_name,
                    'input_token_limit': getattr(m, 'input_token_limit', 'N/A'),
                    'output_token_limit': getattr(m, 'output_token_limit', 'N/A'),
                    # Можно добавить 'version' если он есть и нужен
                })
        print(f"Найдено {len(models_data)} подходящих моделей.")
        # Сортируем для удобства (например, по display_name)
        models_data.sort(key=lambda x: x['display_name'])
        return models_data
    except Exception as e:
        print(f"ОШИБКА: Не удалось получить список моделей из API: {e}")
        return None # Возвращаем None при ошибке