# --- START OF FILE translation_module.py ---

from abc import ABC, abstractmethod
import google.generativeai as genai
import os
import re
from typing import Optional, List, Dict, Any
from openrouter_translation import OpenRouterTranslator

# Константа для обозначения ошибки лимита контекста
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

class BaseTranslator(ABC):
    @abstractmethod
    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None) -> Optional[str]:
        pass

    @abstractmethod
    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                      model_name: str = None, prompt_ext: Optional[str] = None) -> Optional[str]:
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
                        'output_token_limit': model.output_token_limit
                    })
            return models
        except Exception as e:
            print(f"Ошибка при получении списка моделей Google: {e}")
            return []

    def translate_chunk(self, model_name: str, text: str, target_language: str = "russian",
                       previous_context: str = "", prompt_ext: Optional[str] = None) -> Optional[str]:
        """Переводит один кусок текста с использованием предоставленной модели."""
        try:
            model = genai.GenerativeModel(model_name)
        except Exception as e:
            print(f"ОШИБКА: Инициализация модели: {e}")
            return None

        # Формирование базовых инструкций
        prompt_lines = [
            f"You are a professional literary translator translating a book for a {target_language}-speaking audience. Your goal is to provide a high-quality, natural-sounding translation into {target_language}, adhering to the following principles:",
            "- Perform a literary translation, preserving the author's original style, tone, and nuances.",
            "- Maintain consistency in terminology, character names, and gender portrayal *within this entire response*.",
            "- Avoid softening strong language unless culturally necessary.",
            f"- Translate common abbreviations (like 'e.g.', 'i.e.', 'CIA') according to their established {target_language} equivalents.",
            "- Keep uncommon or fictional abbreviations/acronyms (e.g., KPS) in their original form.",
            "- For neologisms or compound words, find accurate and stylistically appropriate {target_language} equivalents and use them consistently *within this response*.",
            f"{' - When formatting dialogue, use the Russian style with em dashes (—), not quotation marks.' if target_language.lower() == 'russian' else ''}",
            f"""- If clarification is needed for a {target_language} reader (cultural notes, untranslatable puns, proper names, etc.), use translator's footnotes.
  - **Format:** Insert a sequential footnote marker directly after the word/phrase.
    - **Preferred format:** Use superscript numbers (like ¹, ², ³).
    - **Alternative format (if superscript is not possible):** Use numbers in square brackets (like [1], [2], [3]).
  - **Content:** At the very end of the translated section, add a separator ('---') and a heading ('{'Примечания переводчика' if target_language.lower() == 'russian' else 'Translator Notes'}'). List all notes sequentially by their marker (e.g., '¹ Explanation.' or '[1] Explanation.').
  - Use footnotes sparingly."""
        ]

        # Добавляем пользовательские инструкции
        if prompt_ext and prompt_ext.strip():
            print(f"  [translate_chunk] Добавляем пользовательские инструкции (prompt_ext) к промпту (длина: {len(prompt_ext)}).")
            prompt_lines.extend([
                "\n---",
                "ADDITIONAL INSTRUCTIONS (Apply if applicable, follow strictly for names and terms defined here):",
                prompt_ext,
                "---"
            ])

        # Добавляем контекст и текст для перевода
        if previous_context:
            prompt_lines.append("\nPrevious Context (use for style and recent terminology reference):\n" + previous_context)
        
        prompt_lines.extend([
            "\nText to Translate:",
            text,
            "\nTranslation:"
        ])
        
        prompt = "\n".join(filter(None, prompt_lines))

        if not text.strip():
            return ""

        try:
            print(f"Отправка запроса к модели {model_name}...")
            response = model.generate_content(prompt)
            raw_text = getattr(response, 'text', None)

            if raw_text is not None:
                return raw_text
            else:
                print("ОШИБКА: Ответ API не содержит текста.")
                return None

        except Exception as e:
            error_text = str(e).lower()
            context_keywords = ["context window", "token limit", "maximum input length",
                              "превышен лимит токенов", "request payload size exceeds the limit",
                              "resource exhausted", "limit exceeded", "400 invalid argument"]
            
            is_likely_context_error = False
            if "400 invalid argument" in error_text and "token" in error_text:
                is_likely_context_error = True
            elif any(keyword in error_text for keyword in context_keywords):
                is_likely_context_error = True

            if is_likely_context_error:
                print(f"ОШИБКА: Лимит контекста? {e}")
                return CONTEXT_LIMIT_ERROR
            else:
                print(f"ОШИБКА: Неизвестная ошибка перевода: {e}")
                import traceback
                traceback.print_exc()
                return None

    def translate_text(self, text_to_translate: str, target_language: str = "russian",
                      model_name: str = "gemini-1.5-flash", prompt_ext: Optional[str] = None) -> Optional[str]:
        """Основная функция для перевода строки текста."""
        CHUNK_SIZE_LIMIT_CHARS = 20000
        text_len = len(text_to_translate)
        print(f"Проверка длины текста: {text_len} симв. Лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} симв.")

        if text_len <= CHUNK_SIZE_LIMIT_CHARS * 1.1:
            print("Пробуем перевод целиком...")
            result = self.translate_chunk(model_name, text_to_translate, target_language, prompt_ext=prompt_ext)
            if result != CONTEXT_LIMIT_ERROR:
                return result
            print("Перевод целиком не удался (лимит контекста), переключаемся на чанки.")

        # Разбиваем на параграфы
        print(f"Текст длинный ({text_len} симв.), разбиваем на чанки...")
        paragraphs = text_to_translate.split('\n\n')
        chunks = []
        current_chunk = []
        current_chunk_len = 0

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            paragraph_len = len(paragraph)
            if paragraph_len > CHUNK_SIZE_LIMIT_CHARS:
                # Если текущий чанк не пустой, сохраняем его
                if current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_chunk_len = 0

                # Разбиваем длинный параграф на предложения
                sentences = paragraph.split('. ')
                temp_chunk = []
                temp_chunk_len = 0

                for sentence in sentences:
                    sentence = sentence.strip()
                    sentence_len = len(sentence)

                    if temp_chunk_len + sentence_len > CHUNK_SIZE_LIMIT_CHARS:
                        if temp_chunk:
                            chunks.append('. '.join(temp_chunk) + '.')
                        temp_chunk = [sentence]
                        temp_chunk_len = sentence_len
                    else:
                        temp_chunk.append(sentence)
                        temp_chunk_len += sentence_len + 2  # +2 для '. '

                if temp_chunk:
                    chunks.append('. '.join(temp_chunk) + '.')
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
            print("Ошибка: Не удалось создать чанки!")
            return None

        # Переводим чанки
        print(f"Текст разбит на {len(chunks)} чанков.")
        translated_chunks = []
        last_successful_translation = ""

        for i, chunk in enumerate(chunks, 1):
            print(f"-- Перевод чанка {i}/{len(chunks)} ({len(chunk)} симв.)...")
            context_fragment = " ".join(last_successful_translation.split()[-100:]) if last_successful_translation else ""
            
            translated_chunk = self.translate_chunk(
                model_name,
                chunk,
                target_language,
                previous_context=context_fragment,
                prompt_ext=prompt_ext
            )

            if translated_chunk == CONTEXT_LIMIT_ERROR:
                print(f"Ошибка лимита контекста на чанке {i}.")
                return CONTEXT_LIMIT_ERROR
            elif translated_chunk is None:
                print(f"Ошибка перевода чанка {i}.")
                return None
            else:
                translated_chunks.append(translated_chunk)
                last_successful_translation = translated_chunk

        print("Сборка переведенных чанков...")
        return "\n\n".join(translated_chunks)

class TranslatorFactory:
    @staticmethod
    def create_translator(model_name: str) -> BaseTranslator:
        """Создает экземпляр переводчика на основе имени модели."""
        if model_name.startswith("gemini-") or model_name.startswith("models/"):
            return GoogleTranslator()
        else:
            return OpenRouterTranslator()

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
                  model_name: str = None, prompt_ext: Optional[str] = None) -> Optional[str]:
    """Переводит текст, используя соответствующий API на основе имени модели."""
    if not model_name:
        model_name = "mistralai/ministral-8b"  # Модель по умолчанию
    
    translator = TranslatorFactory.create_translator(model_name)
    return translator.translate_text(text_to_translate, target_language, model_name, prompt_ext)

def get_models_list() -> List[Dict[str, Any]]:
    """Возвращает отсортированный список моделей: сначала Google, затем бесплатные OpenRouter."""
    google_models = []
    openrouter_free_models = []
    
    # Получаем модели от Google
    try:
        google_translator = GoogleTranslator()
        google_models = google_translator.get_available_models()
        print(f"Получено {len(google_models)} моделей от Google API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей Google: {e}")
    
    # Получаем модели от OpenRouter
    try:
        openrouter_translator = OpenRouterTranslator()
        all_openrouter_models = openrouter_translator.get_available_models()
        
        # Фильтруем только бесплатные модели
        for model in all_openrouter_models:
            name = model.get('name', '').lower()
            display_name = model.get('display_name', '').lower()
            description = model.get('description', '').lower()
            
            # Проверяем наличие слова 'free' в названии или описании
            if 'free' in name or 'free' in display_name or 'free' in description:
                openrouter_free_models.append(model)
        
        print(f"Получено {len(openrouter_free_models)} бесплатных моделей из {len(all_openrouter_models)} от OpenRouter API")
    except Exception as e:
        print(f"Ошибка при получении списка моделей OpenRouter: {e}")
    
    # Объединяем списки: сначала Google, потом бесплатные OpenRouter
    return google_models + sorted(openrouter_free_models, key=lambda x: x.get('display_name', '').lower())

# --- END OF FILE translation_module.py ---