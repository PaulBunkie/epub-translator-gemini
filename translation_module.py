import google.generativeai as genai
import os

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
    Создает модель и вызывает translate_chunk.
    """
    print(f"Инициализация модели {model_name} для перевода...")
    try:
        model = genai.GenerativeModel(model_name)
        # Передаем язык и модель в translate_chunk
        translated_text = translate_chunk(model, text_to_translate, target_language)
        return translated_text # Возвращаем результат (текст, None или CONTEXT_LIMIT_ERROR)
    except Exception as e:
        print(f"ОШИБКА: Не удалось инициализировать модель или выполнить перевод: {e}")
        return None