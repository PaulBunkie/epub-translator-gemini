import os
import requests
import json
from typing import Optional, List, Dict, Any

# Константы
OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

class OpenRouterTranslator:
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
                f"{OPENROUTER_API_URL}/models",
                headers=self.headers
            )
            response.raise_for_status()
            models = response.json().get("data", [])
            
            # Форматируем данные в тот же формат, что и для Google API
            formatted_models = []
            for model in models:
                formatted_models.append({
                    'name': model['id'],
                    'display_name': model.get('name', model['id']),
                    'input_token_limit': model.get('context_length', 'N/A'),
                    'output_token_limit': model.get('context_length', 'N/A')
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
        prompt_ext: Optional[str] = None
    ) -> Optional[str]:
        """Переводит один фрагмент текста используя указанную модель."""
        
        # Формируем базовые инструкции
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
            response = requests.post(
                f"{OPENROUTER_API_URL}/chat/completions",
                headers=self.headers,
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": "You are a professional literary translator."},
                        {"role": "user", "content": prompt}
                    ]
                }
            )
            response.raise_for_status()
            
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                translated_text = result['choices'][0]['message']['content']
                return translated_text
            else:
                print("Ошибка: Неожиданный формат ответа от API")
                return None
                
        except requests.exceptions.RequestException as e:
            error_text = str(e).lower()
            # Проверяем различные варианты ошибок, связанных с превышением контекста
            context_keywords = [
                "context window", "token limit", "maximum input length",
                "превышен лимит токенов", "request payload size exceeds the limit",
                "resource exhausted", "limit exceeded", "400 invalid argument"
            ]
            
            is_likely_context_error = any(keyword in error_text for keyword in context_keywords)
            if is_likely_context_error:
                print(f"Ошибка: Превышен лимит контекста: {e}")
                return CONTEXT_LIMIT_ERROR
            else:
                print(f"Ошибка при выполнении запроса: {e}")
                return None

    def translate_text(
        self,
        text_to_translate: str,
        target_language: str = "russian",
        model_name: str = "anthropic/claude-3-opus",
        prompt_ext: Optional[str] = None
    ) -> Optional[str]:
        """
        Основная функция для перевода текста.
        Разбивает длинный текст на части при необходимости.
        """
        CHUNK_SIZE_LIMIT_CHARS = 20000  # Можно настроить в зависимости от модели
        text_len = len(text_to_translate)
        print(f"Проверка длины текста: {text_len} симв. Лимит чанка: {CHUNK_SIZE_LIMIT_CHARS} симв.")

        # Пробуем перевести весь текст целиком, если он не слишком длинный
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