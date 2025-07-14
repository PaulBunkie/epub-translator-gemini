import os
import re
import traceback
from typing import Optional, List, Dict, Any
import requests # Импорт для выполнения HTTP запросов
import json # Импорт для работы с JSON
import time # Импорт для задержки при ретраях
import google.generativeai as genai # Импорт для Google API

# Константа для обозначения ошибки лимита контекста
# TODO: Возможно, стоит перенести в класс или конфиг
CONTEXT_LIMIT_ERROR = "CONTEXT_LIMIT_ERROR"

# Константа для обозначения ошибки пустого ответа
EMPTY_RESPONSE_ERROR = "__EMPTY_RESPONSE_AFTER_RETRIES__"

# Единый лимит размера чанка для всех операций
CHUNK_SIZE_LIMIT_CHARS = 30000

# Отдельный лимит для анализа - должен быть больше, чтобы вместить всю суммаризацию книги
ANALYSIS_CHUNK_SIZE_LIMIT_CHARS = 100000

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
        'system': f"""You are a professional literary summarizer. Your task is to produce concise and accurate summaries of literary texts while preserving the original language, tone, narrative style, and key ideas. The summary must always be written in the **same language as the input text**.
Your output should:
- Follow the emotional tone and literary style of the original (e.g., poetic, ironic, detached, dramatic).
- Preserve essential plot points, key character actions, and meaningful dialogue or reflections.
- Avoid inserting personal interpretations or modernizing the text unless explicitly asked.
- Omit minor descriptive details or digressions unless they serve a symbolic or structural role.
Use past tense and third person unless otherwise specified.
You may be given excerpts, scenes, chapters, or entire texts. Treat each as self-contained but coherent.

{{prompt_ext_section}}"""
    },
    'reduce': {
        'system': f"""You are a text compressor. Your task is to reduce existing summaries or outlines by approximately 50% (±5%), while retaining all key information, events, characters, organizations, technologies, and terminology. 
This is not a summary of a summary — it is a compression. Do not remove named entities or critical events. Remove only redundancy, verbose phrasing, or low-value connective text. Do not change the order of events.
The result must retain as much information as possible in half the length. Bullet points and headings are allowed only if they were in the original.

{{prompt_ext_section}}"""
    },
    'analyze': {
        'system': f"""You are an expert-level AI system designed for advanced literary and cultural analysis. Your task is to analyze the provided text and produce a comprehensive guide for a professional translator into {{target_language}}.

Your response must consist of TWO SEPARATE MARKDOWN TABLES, one after the other, without any additional commentary.

Overarching Translation & Adaptation Policy
Before performing any tasks, you must adhere to the following global principles:

Principle of Significance: Your primary goal is to identify items that represent a significant translation challenge or are essential for world-building. You must prioritize relevance and impact over exhaustive quantity. Actively filter out and ignore standard, obvious, or trivial terms that do not require a special decision.

Abbreviations and Codes: If an abbreviation has a well-established equivalent in the target language, use that equivalent. If the abbreviation is fictional, universe-specific, author-created, or non-standard, you must preserve the original Latin script without translation. This includes technical or location codes.

Stylistic Neologisms, Invented Terms, and Brand Names: When encountering stylistic neologisms, invented terms, or brand-like constructs, determine the authorial intent. If the term is a real-world brand name or designed to represent a fictitious corporation, software, or stylized object, you must preserve it in the original Latin script to maintain branding effect. However, if the term is a semantic neologism, and especially if it carries metaphorical or satirical meaning, you must translate it into the target language to preserve its literary function and avoid immersion-breaking Latin insertions. Do not retain such terms in Latin if they are meant to be understood rather than branded. Avoid transliteration unless the term has an officially established version in the target language.

TASK 1: Stylistic & Conceptual Glossary
Identify a curated list of the most impactful terms and expressions that represent a significant translation challenge.

Methodology:
Carefully analyze each term to determine the optimal translation strategy, balancing fidelity, tone, and the target language stylistic context. Always follow the overarching policy above.

Filtering Criteria for Inclusion: A term should only be included if it meets at least one of these conditions:

It is a creative neologism or a unique compound word.
Its translation relies on a metaphor or a deeper concept, not just a literal meaning.
It is a piece of in-universe terminology critical to understanding the world (a fictional race, unique technology, specific material, etc.).
It represents a conscious, non-obvious decision, such as choosing a standard word over a literal but awkward equivalent.
Explicit Exclusion Rule: Actively ignore and exclude standard vocabulary, direct literal translations of common concepts, and universally accepted genre terms.

Output Format (Task 1):
A Markdown table with the following three columns:
| Term | Proposed Translation | Rationale |
The Rationale column must explain the reasoning behind your choice, referencing authorial intent, linguistic effect, and cultural positioning when relevant. Your job is to focus on capturing items that require non-obvious decisions and filter out everything else.

TASK 2: Grammatical Roster of Proper Nouns
Identify and list only proper nouns that are recurring, central to the plot, or present a significant translation ambiguity.

Criteria for Including a Proper Noun: A name should only be included if it meets at least one of these conditions:

The character, location, or entity is a main or major recurring element.
The name is ambiguous in its transliteration, allowing for multiple valid options.
The name's gender is not obvious and requires contextual analysis to assign correctly for grammatical purposes.
The name is a complex compound that requires a specific rule for handling its parts.
Explicit Exclusion Rule: Exclude minor, one-off characters or locations unless they present a clear ambiguity.

Rules for Assigning Gender:
For characters (including non-binary ones), follow this strict hierarchy:

Explicit Context: Use direct clues such as pronouns, roles, or relational descriptors.
Name Pattern: Infer gender from typical gender associations with the name.
Default Rule: If gender remains ambiguous, assign masculine gender by default for grammatical clarity. Apply this rule also to characters identified as non-binary, using available cues (e.g., voice, role, associated grammar) to assign either m or f for declension purposes.
Output Format (Task 2):
A Markdown table with the following four columns:
| Name | Recommended Translation | Gender (m/f) | Declension Rule & Notes |
The Gender column must use m for masculine or f for feminine only.
The Declension Rule & Notes column must indicate how the name should decline (if at all) and explain the gender assignment clearly (e.g., derived from context, inferred from usage, defaulted by rule).

Final Instruction:
Now, execute both tasks on the text below. Begin with the table for Task 1.

{{prompt_ext_section}}"""
    },
    'translate_toc': {
        'system': f"""Translate these chapter titles to {{target_language}}. 
Keep the exact same order and format. Use "|||" as separator.
Preserve chapter numbers and punctuation exactly.
Return only the translated titles, separated by "|||". Do not add any explanations, numbering, or additional text."""
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
        'user': f"""Summarize the following literary text in the same language. Preserve the key events, character actions, and overall style. The summary should be brief but accurate, with no interpretations. 
Your response must start directly with the summary.        
Text to Summarize:
{{text}}
"""
    },
    'reduce': {
        'user': f"""Compress the following text to 50% of its original length (±5%). This is not a further abstraction — retain all characters, major events, technologies, and worldbuilding. Do not omit anything essential. Shorten phrasing, remove redundancy, but keep all critical facts.
Your response must start directly with the compressed text.
Text to Reduce:
{{text}}"""
    },
    'analyze': {
        'user': f"""Your response must start directly with the analysis.
Text to Analyze:
{{text}}"""
    },
    'translate_toc': {
        'user': f"""Translate the following chapter titles to {{target_language}}. Use "|||" as separator. Return only the translated titles, separated by "|||". Do not add any explanations, numbering, or additional text. Titles:
{{text}}"""
    }
}

# --- Добавляем отдельные fallback модели для translate_toc ---
FALLBACK_MODELS_TOC = {
    'primary': 'models/gemini-2.5-flash-preview-05-20',
    'fallback': 'google/gemma-3-27b-it:free'
}

# --- НОВЫЙ КЛАСС WorkflowTranslator ---
class WorkflowTranslator:
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
    # TODO: Добавить URL для Google API, если будем его реализовывать здесь же
    
    # Резервные модели импортируются из workflow_processor
    # FALLBACK_MODELS теперь определены в workflow_processor.py

    def __init__(self):
         # Инициализация API ключа OpenRouter
        try:
            self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
            if not self.openrouter_api_key:
                print("Предупреждение: Переменная окружения OPENROUTER_API_KEY не установлена.")
        except Exception as e:
            print(f"[WorkflowTranslator] ОШИБКА при получении OPENROUTER_API_KEY: {e}")
            self.openrouter_api_key = None
        
        # Инициализация Google API ключа
        try:
            self.google_api_key = os.getenv("GOOGLE_API_KEY")
            if self.google_api_key:
                genai.configure(api_key=self.google_api_key)
                print("Google API ключ успешно сконфигурирован для workflow.")
            else:
                print("Предупреждение: Переменная окружения GOOGLE_API_KEY не установлена.")
        except Exception as e:
            print(f"[WorkflowTranslator] ОШИБКА при конфигурации Google API: {e}")
            self.google_api_key = None

    def _get_fallback_model(self, operation_type: str, current_model: str) -> str | None:
        """
        Возвращает резервную модель для указанной операции, если она отличается от текущей.
        Для translate_toc возвращает свою fallback-модель.
        """
        if operation_type == 'translate_toc':
            if current_model == FALLBACK_MODELS_TOC['primary']:
                return FALLBACK_MODELS_TOC['fallback']
            return None
        try:
            import workflow_processor
            fallback_model = workflow_processor.FALLBACK_MODELS.get(operation_type)
            if fallback_model and fallback_model != current_model:
                print(f"[WorkflowTranslator] Переключение на резервную модель для {operation_type}: {current_model} -> {fallback_model}")
                return fallback_model
        except ImportError:
            print(f"[WorkflowTranslator] Предупреждение: Не удалось импортировать FALLBACK_MODELS из workflow_processor")
        return None

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

        elif operation_type == 'translate_toc':
            return SYSTEM_PROMPT_TEMPLATES['translate_toc']['system'].format(target_language=target_language)

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

    def _smart_chunk_text_for_reduction(self, text: str, target_chunk_size: int = ANALYSIS_CHUNK_SIZE_LIMIT_CHARS) -> List[str]:
        """
        Умное разбиение текста на оптимальное количество чанков для рекурсивной суммаризации.
        Цель: минимизировать количество чанков, чтобы каждый был < target_chunk_size.
        """
        if not text.strip():
            return []
        
        text_length = len(text)
        
        # Если текст уже помещается в один чанк
        if text_length <= target_chunk_size:
            return [text]
        
        # Вычисляем оптимальное количество чанков
        if target_chunk_size <= 0:
            print(f"[WorkflowTranslator] Предупреждение: target_chunk_size = {target_chunk_size}, используем дефолтное значение 30000")
            target_chunk_size = 30000
        
        optimal_chunks_count = max(2, (text_length + target_chunk_size - 1) // target_chunk_size)
        print(f"[WorkflowTranslator] Умное разбиение: текст {text_length} символов на {optimal_chunks_count} чанков (цель: <{target_chunk_size} каждый)")
        
        # Разбиваем текст на равные части
        chunk_size = text_length // optimal_chunks_count
        chunks = []
        current_pos = 0
        
        for i in range(optimal_chunks_count):
            if current_pos >= text_length:
                break
                
            # Определяем границы для текущего чанка
            if i == optimal_chunks_count - 1:
                # Последний чанк - берем все оставшееся
                end_pos = text_length
            else:
                end_pos = current_pos + chunk_size
            
            # Ищем ближайший разрыв в пределах ±10% от идеальной позиции
            ideal_pos = (current_pos + end_pos) // 2
            search_start = max(current_pos, ideal_pos - chunk_size // 10)
            search_end = min(end_pos, ideal_pos + chunk_size // 10)
            
            # Приоритет разбиения:
            # 1. Двойной перенос строки (граница абзаца)
            # 2. Одинарный перенос строки
            # 3. Граница предложения (точка, восклицательный знак, вопросительный знак)
            # 4. Идеальная позиция
            
            split_point = -1
            
            # 1. Ищем разрыв абзаца (двойной перенос строки)
            split_point = text.rfind('\n\n', search_start, search_end)
            
            if split_point == -1:
                # 2. Если нет двойного переноса, ищем одинарный
                split_point = text.rfind('\n', search_start, search_end)
            
            if split_point == -1:
                # 3. Если нет переносов строк, ищем границу предложения
                for i in range(search_end, search_start, -1):
                    if text[i-1] in '.!?':
                        # Проверяем, что после знака препинания есть пробел или конец строки
                        if i >= len(text) or text[i] in ' \n':
                            split_point = i
                            break
            
            if split_point == -1:
                # 4. Если ничего не найдено, используем идеальную позицию
                split_point = ideal_pos
            
            # Создаем чанк
            chunk = text[current_pos:split_point].strip()
            if chunk:
                chunks.append(chunk)
            
            # Обновляем позицию для следующего чанка
            current_pos = split_point
        
        print(f"[WorkflowTranslator] Умное разбиение завершено: {len(chunks)} чанков")
        for i, chunk in enumerate(chunks):
            print(f"[WorkflowTranslator] Чанк {i+1}: {len(chunk)} символов")
        
        return chunks

    def _bubble_chunk_text(self, text: str, target_chunk_size: int = CHUNK_SIZE_LIMIT_CHARS) -> List[str]:
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
            
        elif operation_type == 'reduce':
            system_content = SYSTEM_PROMPT_TEMPLATES['reduce']['system'].format(**formatted_vars)
            user_content = USER_PROMPT_TEMPLATES['reduce']['user'].format(**formatted_vars)
            
            messages.append({"role": "system", "content": system_content})
            messages.append({"role": "user", "content": user_content})
            
        elif operation_type == 'translate_toc':
            system_content = SYSTEM_PROMPT_TEMPLATES['translate_toc']['system'].format(**formatted_vars)
            user_content = USER_PROMPT_TEMPLATES['translate_toc']['user'].format(**formatted_vars)
            messages.append({"role": "system", "content": system_content})
            messages.append({"role": "user", "content": user_content})
            
        else:
            raise ValueError(f"Unknown operation type: {operation_type}")

        return messages

    def _determine_api_type(self, model_name: str) -> str:
        """
        Определяет тип API на основе имени модели.
        """
        if model_name and model_name.startswith('models/'):
            return "google"
        else:
            return "openrouter"

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
        Реализует логику вызова OpenRouter API и Google API.

        Args:
            model_name: Имя модели для вызова.
            messages: Список сообщений в формате [{"role": "...", "content": "..."}, ...].
            temperature: Параметр temperature для API вызова.
            max_retries: Максимальное количество попыток вызова при ошибках.
            retry_delay_seconds: Начальная задержка между попытками.

        Returns:
            Текст ответа модели или специальные константы ошибок/None.
        """
        # Определяем тип API на основе имени модели
        api_type = self._determine_api_type(model_name)
        print(f"[WorkflowTranslator] Определен тип API: {api_type} для модели: {model_name}")

        if api_type == "google":
            if not self.google_api_key:
                print("[WorkflowTranslator] ОШИБКА: GOOGLE_API_KEY не установлен.")
                return None

            # Преобразуем messages в формат Google API
            prompt = ""
            for message in messages:
                if message.get('role') == 'user':
                    prompt += message.get('content', '') + "\n"
                elif message.get('role') == 'system':
                    prompt = message.get('content', '') + "\n" + prompt

            try:
                print(f"[WorkflowTranslator] Отправка запроса к Google API (модель: {model_name})...")
                model = genai.GenerativeModel(model_name)
                
                # Настройки безопасности для Google API
                safety_settings = {
                    'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'block_none',
                    'HARM_CATEGORY_HATE_SPEECH': 'block_none',
                    'HARM_CATEGORY_HARASSMENT': 'block_none',
                    'HARM_CATEGORY_DANGEROUS_CONTENT': 'block_none'
                }
                
                response = model.generate_content(prompt, safety_settings=safety_settings)
                
                # Проверка на пустой ответ
                if not response.text:
                    print("[WorkflowTranslator] Google API вернул пустой ответ.")
                    return None
                
                print(f"[WorkflowTranslator] Google API ответ получен успешно.")
                return response.text

            except Exception as e:
                print(f"[WorkflowTranslator] ОШИБКА при вызове Google API: {e}")
                if "context window" in str(e).lower():
                    return CONTEXT_LIMIT_ERROR
                
                # НЕ делаем fallback здесь - это будет сделано на уровне translate_text
                return None

        elif api_type == "openrouter":
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
            input_prompt_tokens = len(prompt_text) // 3
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
                "temperature": 0.5, # УСТАНОВКА ТЕМПЕРАТУРЫ
                "stream": False #, # Убеждаемся, что не ждем стриминг
                #"reasoning": {
                #    "exclude": True
                #},
            }

            # Добавляем max_tokens только для перевода
            if operation_type == 'translate':
                data["max_tokens"] = final_output_token_limit

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
                        response_json = response.json()
                        if 'choices' in response_json and len(response_json['choices']) > 0:
                            choice = response_json['choices'][0]
                            if 'message' in choice and 'content' in choice['message']:
                                output_content = choice['message']['content'].strip()
                                # Проверка на обрезание ответа
                                finish_reason = choice.get('finish_reason')
                                print(f"[WorkflowTranslator] finish_reason: {finish_reason}")
                                
                                # --- ПРОВЕРКА finish_reason на успех ---
                                if finish_reason != 'stop':
                                    print(f"[WorkflowTranslator] ОШИБКА: Модель вернула finish_reason='{finish_reason}' вместо 'stop'. Возвращаем None для ретрая.")
                                    return None
                                # --- КОНЕЦ ПРОВЕРКИ ---
                                
                                # --- ПРОВЕРКА ДЛИНЫ ТЕКСТА ПОСЛЕ УСПЕШНОГО finish_reason ---
                                if not output_content.strip() or len(output_content.strip()) < 10:
                                    print(f"[WorkflowTranslator] ОШИБКА: Модель вернула пустой или слишком короткий текст (длина: {len(output_content.strip())}). Возвращаем None для ретрая.")
                                    return None
                                
                                # --- ЭВРИСТИКА определения потенциально неполного перевода ---
                                if operation_type == 'translate' and chunk_text:
                                    # Честная проверка: сравниваем длину перевода и исходного текста (оба только текст)
                                    input_char_len = len(chunk_text)
                                    output_char_len = len(output_content)
                                    if output_char_len < input_char_len * 0.8:
                                        print(f"[OpenRouterTranslator] Предупреждение: Перевод ({output_char_len} симв.) значительно короче исходного текста ({input_char_len} симв.) (<80%). Возвращаем None для ретрая.")
                                        return None
                                
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
                        time.sleep(current_delay)
                        current_delay *= 2  # Экспоненциальное увеличение задержки
                        continue # Перейти к следующей попытке

                    # --- Обработка других ошибок ---
                    elif response.status_code >= 400:
                        print(f"[OpenRouterTranslator] Ошибка API: Статус {response.status_code}")
                        try:
                            error_details = response.json()
                            print(f"[OpenRouterTranslator] Детали ошибки: {error_details}")
                            
                            # Специальная обработка для 503 "No instances available"
                            if response.status_code == 503:
                                error_message = error_details.get('error', {}).get('message', '')
                                if "No instances available" in error_message:
                                    print("[OpenRouterTranslator] Обнаружена ошибка недоступности модели. Немедленное переключение на резервную.")
                                    fallback_model = self._get_fallback_model(operation_type, model_name)
                                    if fallback_model:
                                        return self._call_model_api(fallback_model, messages, operation_type, chunk_text, 1, 1)
                                    return None
                            
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


                        return None

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
            
            # Попытка переключения на резервную модель
            fallback_model = self._get_fallback_model(operation_type, model_name)
            if fallback_model:
                print(f"[WorkflowTranslator] Попытка переключения на резервную модель: {fallback_model}")
                # Рекурсивный вызов с резервной моделью (только одна попытка)
                return self._call_model_api(fallback_model, messages, operation_type, chunk_text, 1, 1)
            
            return None

        else:
            # Неизвестный тип API
            print(f"[WorkflowTranslator] ОШИБКА: Неизвестный тип API для модели: '{model_name}'.")
            return None # Или специальная ошибка неизвестного API

    def _translate_segment(
        self,
        text_to_process: str,
        target_language: str,
        model_name: str,
        operation_type: str,
        prompt_ext: Optional[str] = None,
        dict_data: dict | None = None
    ) -> str | None:
        """
        Ф1 - Перевод сегмента: определяет лимит для модели, разбивает на чанки, переводит каждый.
        """
        print(f"[WorkflowTranslator] Перевод сегмента с моделью {model_name}")
        
        # Определяем лимит чанка для ЭТОЙ модели
        chunk_limit = self._get_chunk_limit_for_operation(operation_type, model_name)
        
        # Разбиваем текст на чанки по лимиту ЭТОЙ модели
        chunks = self._smart_chunk_text_for_reduction(text_to_process, chunk_limit)
        if not chunks:
            print(f"[WorkflowTranslator] Нет чанков для {operation_type}")
            return None
        
        print(f"[WorkflowTranslator] Текст разбит на {len(chunks)} чанков для {operation_type}")
        
        # Переводим каждый чанк
        results = []
        for i, chunk in enumerate(chunks):
            print(f"[WorkflowTranslator] {operation_type.capitalize()} чанка {i+1}/{len(chunks)} (длина: {len(chunk)} символов)")
            
            result = self._translate_chunk(
                chunk,
                target_language,
                model_name,
                operation_type,
                prompt_ext,
                dict_data
            )
            
            if not result:
                print(f"[WorkflowTranslator] Ошибка перевода чанка {i+1}")
                return None
            
            results.append(result)
        
        # Объединяем результаты
        full_result = "\n\n".join(results)
        print(f"[WorkflowTranslator] {operation_type.capitalize()} завершена. Общая длина: {len(full_result)} символов")
        return full_result

    def _translate_chunk(
        self,
        chunk: str,
        target_language: str,
        model_name: str,
        operation_type: str,
        prompt_ext: Optional[str] = None,
        dict_data: dict | None = None
    ) -> str | None:
        """
        Ф2 - Перевод чанка: просто вызывает API с ретраями.
        """
        messages = self._build_messages_for_operation(
            operation_type,
            chunk,
            target_language,
            model_name=model_name,
            prompt_ext=prompt_ext,
            dict_data=dict_data
        )
        
        # Пытаемся обработать чанк с ретраями
        max_retries = 2
        for attempt in range(max_retries + 1):
            result = self._call_model_api(model_name, messages, operation_type=operation_type, chunk_text=chunk)
            if result is not None and result != CONTEXT_LIMIT_ERROR:
                return result
            
            if attempt < max_retries:
                print(f"[WorkflowTranslator] Ошибка {operation_type} чанка (попытка {attempt+1}/{max_retries+1}), повторяем...")
                time.sleep(2)
            else:
                print(f"[WorkflowTranslator] Чанк не удалось обработать после {max_retries+1} попыток")
                return None
        
        return None

    def _get_chunk_limit_for_operation(self, operation_type: str, model_name: str) -> int:
        """
        Возвращает лимит чанка для данной операции и модели
        """
        from translation_module import get_context_length
        
        context_token_limit = get_context_length(model_name) if model_name else 2048
        context_chars_limit = context_token_limit * 3
        chunk_limit = context_chars_limit - 4000  # Буфер для промпта и ответа
        
        if chunk_limit <= 0:
            chunk_limit = context_chars_limit // 2
        
        if chunk_limit <= 0:
            print(f"[WorkflowTranslator] Предупреждение: context_chars_limit = {context_chars_limit}, используем дефолтное значение")
            chunk_limit = 30000
        
        # Применяем ограничения в зависимости от операции
        if operation_type == 'analyze':
            chunk_limit = min(chunk_limit, ANALYSIS_CHUNK_SIZE_LIMIT_CHARS)
        elif operation_type == 'translate_toc':
            chunk_limit = 1000
        else:
            chunk_limit = min(chunk_limit, CHUNK_SIZE_LIMIT_CHARS)
        
        print(f"[WorkflowTranslator] Лимит чанка для {operation_type}: {chunk_limit} символов (модель: {model_name})")
        return chunk_limit

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
        Ф3 - Fallback контроллер: пытается с основной моделью, затем с резервной.
        """
        print(f"[WorkflowTranslator] Вызов операции '{operation_type}' для текста длиной {len(text_to_translate)} символов")

        # Пытаемся с основной моделью
        if model_name:
            result = self._translate_segment(
                text_to_translate,
                target_language,
                model_name,
                operation_type,
                prompt_ext,
                dict_data
            )
            if result:
                return result
        
        # Если основная модель не сработала или не передана, пробуем резервную
        fallback_model = self._get_fallback_model(operation_type, model_name)
        if fallback_model:
            print(f"[WorkflowTranslator] Переключение на резервную модель: {fallback_model}")
            result = self._translate_segment(
                text_to_translate,
                target_language,
                fallback_model,
                operation_type,
                prompt_ext,
                dict_data
            )
            if result:
                return result
        
        print(f"[WorkflowTranslator] Ошибка: операция '{operation_type}' не удалась ни с основной, ни с резервной моделью")
        return None

    def _summarize_to_fit_chunk(self, text: str, target_language: str, model_name: str, prompt_ext: Optional[str] = None) -> str:
        """
        Рекурсивно суммаризирует текст, пока он не поместится в один чанк.
        Используется для подготовки текста к анализу.
        """
        if not text or len(text) <= ANALYSIS_CHUNK_SIZE_LIMIT_CHARS:
            return text
        
        print(f"[WorkflowTranslator] Текст слишком большой для анализа ({len(text)} символов). Делаем дополнительную суммаризацию.")
        
        # Делаем суммаризацию с использованием translate_text для fallback логики
        summarized_text = self.translate_text(text, target_language, model_name, prompt_ext, 'reduce')
        
        if not summarized_text:
            print(f"[WorkflowTranslator] Ошибка дополнительной суммаризации. Возвращаем исходный текст.")
            return text
        
        # Рекурсивно проверяем размер
        if len(summarized_text) <= ANALYSIS_CHUNK_SIZE_LIMIT_CHARS:
            print(f"[WorkflowTranslator] Дополнительная суммаризация успешна. Размер: {len(summarized_text)} символов.")
            return summarized_text
        else:
            print(f"[WorkflowTranslator] Текст все еще слишком большой ({len(summarized_text)} символов). Повторяем суммаризацию.")
            return self._summarize_to_fit_chunk(summarized_text, target_language, model_name, prompt_ext)

    def _summarize_text_with_limit(self, text: str, target_language: str, model_name: str, prompt_ext: Optional[str] = None, chunk_limit: int = CHUNK_SIZE_LIMIT_CHARS) -> str | None:
        """
        Суммаризирует текст с указанным лимитом чанка.
        """
        print(f"[WorkflowTranslator] Вызов операции 'reduce' для текста длиной {len(text)} символов (лимит чанка: {chunk_limit}).")
        
        # Используем умное разбиение для оптимального количества чанков
        reduced_chunks = []
        
        chunks = self._smart_chunk_text_for_reduction(text, chunk_limit)
        if not chunks:
            print(f"[WorkflowTranslator] Нет чанков для reduce.")
            return None
        
        print(f"[WorkflowTranslator] Текст разбит на {len(chunks)} чанков для reduce (умное разбиение, лимит: {chunk_limit}).")
        
        for i, chunk in enumerate(chunks):
            print(f"[WorkflowTranslator] Reduce чанка {i+1}/{len(chunks)} (длина: {len(chunk)} символов)")
            
            messages = self._build_messages_for_operation(
                'reduce',
                chunk,  # Передаем чанк, а не весь текст
                target_language, 
                model_name=model_name,
                prompt_ext=prompt_ext,
                dict_data=None
            )
            
            # Для reduce не передаем max_tokens, чтобы модель сама определила длину
            max_chunk_retries = 2
            attempt = 0
            result = None
            while attempt <= max_chunk_retries:
                result = self._call_model_api(model_name, messages, operation_type='reduce', chunk_text=chunk)
                if result is None:
                    print(f"[WorkflowTranslator] Ошибка reduce чанка {i+1} (попытка {attempt+1}/{max_chunk_retries+1}). Возможно finish_reason: error.")
                    attempt += 1
                    if attempt > max_chunk_retries:
                        print(f"[WorkflowTranslator] Ошибка: чанк {i+1} не удалось обработать после {max_chunk_retries+1} попыток.")
                        break
                    time.sleep(2)
                    continue
                break
            
            if result and result != CONTEXT_LIMIT_ERROR:
                reduced_chunks.append(result)
            else:
                print(f"[WorkflowTranslator] Ошибка: чанк {i+1} не дал валидного результата.")
                print(f"[WorkflowTranslator] Возвращаем None для ретрая.")
                return None
        
        if reduced_chunks:
            full_result = "\n\n".join(reduced_chunks)
            print(f"[WorkflowTranslator] Reduce завершена. Общая длина: {len(full_result)} символов.")
            return full_result
        else:
            print(f"[WorkflowTranslator] Ошибка: reduce не дала результатов.")
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

# --- ПУБЛИЧНАЯ ФУНКЦИЯ ДЛЯ АНАЛИЗА С АВТОМАТИЧЕСКОЙ СУММАРИЗАЦИЕЙ ---
def analyze_with_summarization(
    text_to_analyze: str,
    target_language: str = "russian",
    model_name: str = None,
    prompt_ext: Optional[str] = None,
    dict_data: dict | None = None,
    summarization_model: str = None,
    book_id: str = None
) -> str | None:
    """
    Анализирует текст с автоматической суммаризацией, если текст слишком большой.
    Гарантирует, что на анализ попадает текст, помещающийся в один чанк.
    """
    print(f"[WorkflowModule] DEBUG: Начало функции analyze_with_summarization")
    print(f"[WorkflowModule] DEBUG: text_to_analyze тип: {type(text_to_analyze)}")
    print(f"[WorkflowModule] DEBUG: text_to_analyze длина: {len(text_to_analyze) if text_to_analyze else 'None'}")
    print(f"[WorkflowModule] Вызов analyze_with_summarization. Размер текста: {len(text_to_analyze)} символов.")
    print(f"[WorkflowModule] DEBUG: Создаём объект WorkflowTranslator...")
    translator = WorkflowTranslator()
    print(f"[WorkflowModule] DEBUG: WorkflowTranslator создан")
    
    # Проверяем размер текста
    if len(text_to_analyze) > ANALYSIS_CHUNK_SIZE_LIMIT_CHARS:
        print(f"[WorkflowModule] Текст слишком большой для анализа. Применяем рекурсивную суммаризацию.")
        print(f"[WorkflowModule] DEBUG: summarization_model = {summarization_model}")
        # Используем модель суммаризации для суммаризации, а не модель анализа
        summarization_model_to_use = summarization_model or 'meta-llama/llama-4-maverick:free'
        print(f"[WorkflowModule] DEBUG: summarization_model_to_use = {summarization_model_to_use}")
        print(f"[WorkflowModule] Используем модель суммаризации: {summarization_model_to_use}")
        
        # Обновляем статус книги на 'processing' для этапа сокращения
        if book_id:
            try:
                print(f"[WorkflowModule] DEBUG: Импортируем workflow_db_manager...")
                import workflow_db_manager
                print(f"[WorkflowModule] DEBUG: workflow_db_manager импортирован успешно")
                workflow_db_manager.update_book_stage_status_workflow(book_id, 'reduce_text', 'processing')
                print(f"[WorkflowModule] Статус книги обновлен на 'processing' для этапа сокращения.")
            except Exception as e:
                print(f"[WorkflowModule] Ошибка при обновлении статуса книги: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"[WorkflowModule] Вызываем _summarize_to_fit_chunk...")
        try:
            summarized_text = translator._summarize_to_fit_chunk(
                text_to_analyze, 
                target_language, 
                summarization_model_to_use,  # Используем модель суммаризации
                prompt_ext
            )
            print(f"[WorkflowModule] _summarize_to_fit_chunk завершена. Результат: {'None' if summarized_text is None else f'{len(summarized_text)} символов'}")
        except Exception as e:
            print(f"[WorkflowModule] ОШИБКА в _summarize_to_fit_chunk: {e}")
            import traceback
            traceback.print_exc()
            summarized_text = None
        
        if not summarized_text:
            print(f"[WorkflowModule] Ошибка суммаризации. Возвращаем None.")
            # Обновляем статус книги на 'error' для этапа сокращения
            if book_id:
                try:
                    import workflow_db_manager
                    workflow_db_manager.update_book_stage_status_workflow(book_id, 'reduce_text', 'error', error_message='Ошибка рекурсивного сокращения текста')
                    print(f"[WorkflowModule] Статус книги обновлен на 'error' для этапа сокращения.")
                except Exception as e:
                    print(f"[WorkflowModule] Ошибка при обновлении статуса книги: {e}")
            return None
        text_to_analyze = summarized_text
        print(f"[WorkflowModule] Текст подготовлен для анализа. Новый размер: {len(text_to_analyze)} символов.")
        
        # Обновляем статус книги на 'completed' для этапа сокращения
        if book_id:
            try:
                import workflow_db_manager
                print(f"[WorkflowModule] Обновляем статус reduce_text на 'completed'...")
                workflow_db_manager.update_book_stage_status_workflow(book_id, 'reduce_text', 'completed')
                print(f"[WorkflowModule] Статус книги обновлен на 'completed' для этапа сокращения.")
            except Exception as e:
                print(f"[WorkflowModule] Ошибка при обновлении статуса книги: {e}")
        
        # Сохраняем суммаризацию в кэш, если передан book_id
        if book_id:
            try:
                import workflow_cache_manager
                if workflow_cache_manager.save_book_stage_result(book_id, 'analysis_summary', summarized_text):
                    print(f"[WorkflowModule] Суммаризация для анализа сохранена в кэш (book_id: {book_id}, размер: {len(summarized_text)} символов)")
                else:
                    print(f"[WorkflowModule] Предупреждение: Не удалось сохранить суммаризацию в кэш (book_id: {book_id})")
            except Exception as e:
                print(f"[WorkflowModule] Ошибка при сохранении суммаризации в кэш: {e}")
    else:
        print(f"[WorkflowModule] Текст достаточно короткий для анализа ({len(text_to_analyze)} символов). Суммаризация не требуется.")
    
    # Теперь анализируем подготовленный текст с моделью анализа
    print(f"[WorkflowModule] Вызываем translate_text для анализа...")
    return translator.translate_text(
        text_to_translate=text_to_analyze,
        target_language=target_language,
        model_name=model_name,  # Используем модель анализа
        prompt_ext=prompt_ext,
        operation_type='analyze',
        dict_data=dict_data
    )

# TODO: Возможно, потребуется реализовать другие функции, аналогичные translation_module,
# например, get_models_list, load_models_on_startup, configure_api,
# если workflow_processor или другие части workflow их используют напрямую.
# На текущий момент, workflow_processor, кажется, вызывает только translate_text.
# Если другие части используют их напрямую, их нужно будет проксировать через этот модуль тоже.

# TODO: get_context_length может понадобиться для логики чанкинга.
# Либо скопировать его сюда, либо вызывать из оригинального translation_module
# (если он публичный или мы его импортировали как translation_module_original)
