# --- START OF FILE workflow_processor.py ---

import workflow_db_manager
import epub_parser
import workflow_translation_module as translation_module
import os
import traceback
import workflow_cache_manager # TODO: Implement workflow_cache_manager DONE
import time
from flask import current_app
import re

# --- Constants for Workflow Processor ---
MIN_SECTION_LENGTH = 700 # Minimum length of clean text for summarization/analysis

# --- Helper function to clean HTML text ---
def clean_html(html_content):
    """
    Удаляет HTML-теги из строки и возвращает чистый текст.
    """
    if not html_content:
        return ""
    # Удаляем скрипты и стили
    cleaned_text = re.sub(r'<script.*?>.*?<\/script>', '', html_content, flags=re.IGNORECASE|re.DOTALL)
    cleaned_text = re.sub(r'<style.*?>.*?<\/style>', '', cleaned_text, flags=re.IGNORECASE|re.DOTALL)
    # Удаляем остальные HTML-теги
    cleaned_text = re.sub(r'<.*?>', '', cleaned_text)
    # Заменяем сущности HTML на их символьные представления (например, &amp; на &)
    # Простая замена некоторых распространенных
    cleaned_text = cleaned_text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&apos;', "'").replace('&lt;', '<').replace('&gt;', '>')
    # Удаляем лишние пробелы (включая табуляцию и переносы строк)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    return cleaned_text

# Hardcoded model for summarization for now
SUMMARIZATION_MODEL = 'meta-llama/llama-4-maverick:free'
SUMMARIZATION_STAGE_NAME = 'summarize'

# NEW CONSTANT: Instruction for models regarding proper nouns and gender
MODEL_GENDER_INSTRUCTION_PROMPT = "For proper nouns, indicate the presumed gender in parentheses"

# --- Constants for Analysis Stage ---
ANALYSIS_MODEL = 'deepseek/deepseek-chat-v3-0324:free' # Можно использовать ту же модель или другую
ANALYSIS_STAGE_NAME = 'analyze'

# --- Prompt Template for Analysis ---
ANALYSIS_PROMPT_TEMPLATE = "Your response will be used in the translation instruction, structure it so that the neural network that will do the translation understands how to translate individual terms."

# TODO: Определить шаблон промпта для анализа. Он должен использовать результат суммаризации.
# Пример: Проанализируй ключевые сущности (персонажи, места, события) на основе следующего текста.

# --- Workflow Configuration ---
DEBUG_ALLOW_EMPTY = False # Set to True to treat empty model responses (after retries) as completed_empty instead of error
MAX_RETRIES = 2 # Number of additional retries for model calls

def process_section_summarization(book_id: str, section_id: int):
    """
    Процессит суммаризацию одной секции.
    Получает контент секции, вызывает модель суммаризации,
    сохраняет результат и обновляет статус в БД.
    """
    print(f"[WorkflowProcessor] Начат процесс суммаризации для секции {section_id} книги {book_id}")

    try:
        # 1. Получаем информацию о секции и книге из БД
        # TODO: Добавить get_section_by_id_workflow в workflow_db_manager DONE
        with current_app.app_context():
            section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id) 
            if not section_info:
                print(f"[WorkflowProcessor] Ошибка: Секция с ID {section_id} не найдена в БД.")
                # TODO: Возможно, обновить статус книги на ошибку или пропустить секцию?
                return False

            book_id = section_info['book_id']
            book_info = workflow_db_manager.get_book_workflow(book_id)
            if not book_info:
                 print(f"[WorkflowProcessor] Ошибка: Книга с ID {book_id} для секции {section_id} не найдена в БД.")
                 workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, 'error', error_message='Book not found')
                 return False

            # 2. Обновляем статус секции в БД на 'processing'
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, 'processing')

        # 3. Получаем контент секции из EPUB
        epub_filepath = book_info['filepath']
        section_epub_id = section_info['section_epub_id']

        # --- ИЗМЕНЕНИЕ: Используем существующую функцию extract_section_text с section_epub_id ---
        # TODO: Реализовать получение контента секции по epub_filepath и section_epub_id в epub_parser DONE
        # Возможно, потребуется создать новый метод, который открывает EPUB по пути и извлекает контент конкретного файла по его ID DONE
        section_content = epub_parser.extract_section_text(epub_filepath, section_epub_id)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        if not section_content:
            print(f"[WorkflowProcessor] Предупреждение: Контент секции {section_epub_id} (ID: {section_id}) пуст или не может быть извлечен. Помечаем как completed_empty.")
            # Если контент пуст, считаем этап завершенным с пустым результатом
            with current_app.app_context():
                 workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, 'completed_empty', error_message='Empty section content')
            return True # Возвращаем True, так как это не ошибка, а ожидаемое состояние

        # --- НОВОЕ: Проверка длины текста после очистки от HTML ---
        clean_text = clean_html(section_content)
        if len(clean_text) < MIN_SECTION_LENGTH:
            print(f"[WorkflowProcessor] Секция {section_epub_id} (ID: {section_id}) слишком короткая ({len(clean_text)} < {MIN_SECTION_LENGTH} символов). Пропускаем суммаризацию.")
            with current_app.app_context():
                 workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, 'skipped', error_message=f'Section too short ({len(clean_text)} chars)')
                 # Сохраняем пустой результат в кэш для единообразия
                 workflow_cache_manager.save_section_stage_result(book_id, section_id, SUMMARIZATION_STAGE_NAME, "") # Сохраняем пустой результат
            return True # Секция успешно пропущена
        # --- КОНЕЦ НОВОГО БЛОКА ---

        # 4. Вызываем модель суммаризации с ретраями
        # target_language = book_info['target_language'] # Original line
        # Use a hardcoded signal to tell the model to summarize in the original language
        target_language_for_summarization = "ORIGINAL_LANGUAGE"
        operation_type = SUMMARIZATION_STAGE_NAME
        model_name = SUMMARIZATION_MODEL
        # Use the new instruction prompt
        prompt_ext = MODEL_GENDER_INSTRUCTION_PROMPT

        summarized_text = None
        status = 'error' # Default status in case of failure
        error_message = 'Unknown error'

        for attempt in range(MAX_RETRIES + 1):
            print(f"[WorkflowProcessor] Попытка {attempt + 1}/{MAX_RETRIES + 1} вызова модели для секции {section_id} (суммаризация)...")
            try:
                summarized_text = translation_module.translate_text(
                    text_to_translate=section_content,
                    target_language=target_language_for_summarization,
                    model_name=model_name,
                    operation_type=operation_type,
                    prompt_ext=prompt_ext
                )

                if summarized_text is not None and summarized_text.strip() != "":
                    status = 'completed'
                    error_message = None
                    print(f"[WorkflowProcessor] Модель вернула непустой результат на попытке {attempt + 1}.")
                    # Проверка времени после обновления статуса
                    try:
                        with current_app.app_context():
                            updated_section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
                            if updated_section_info and 'stage_statuses' in updated_section_info and SUMMARIZATION_STAGE_NAME in updated_section_info['stage_statuses']:
                                status_info = updated_section_info['stage_statuses'][SUMMARIZATION_STAGE_NAME]
                                print(f"[DEBUG_TIME_CHECK] Секция {section_id}, Этап {SUMMARIZATION_STAGE_NAME}, Статус {status}: start_time={status_info.get('start_time')}, end_time={status_info.get('end_time')}")
                            else:
                                print(f"[DEBUG_TIME_CHECK] Не удалось получить обновленный статус для секции {section_id}.")
                    except Exception as time_check_err:
                        print(f"[DEBUG_TIME_CHECK] Ошибка при проверке времени для секции {section_id}: {time_check_err}")
                    break # Успех, выходим из цикла ретраев
                elif summarized_text == translation_module.EMPTY_RESPONSE_ERROR:
                     error_message = "API вернул EMPTY_RESPONSE_ERROR."
                     print(f"[WorkflowProcessor] Предупреждение: Модель вернула EMPTY_RESPONSE_ERROR на попытке {attempt + 1}.")
                elif summarized_text == translation_module.CONTEXT_LIMIT_ERROR:
                     status = 'error' # Контекстный лимит - не ретраим
                     error_message = "API вернул CONTEXT_LIMIT_ERROR."
                     print(f"[WorkflowProcessor] Ошибка: Модель вернула CONTEXT_LIMIT_ERROR на попытке {attempt + 1}. Не ретраим.")
                     break
                else: # None или пустая строка
                    error_message = "Модель вернула пустой результат (None или пустая строка)."
                    print(f"[WorkflowProcessor] Предупреждение: Модель вернула пустой результат на попытке {attempt + 1}.")

            except Exception as e:
                status = 'error' # Перехватываем другие исключения
                error_message = f"Исключение при вызове модели: {e}"
                print(f"[WorkflowProcessor] ОШИБКА при вызове модели для секции {section_id} на попытке {attempt + 1}: {e}")
                traceback.print_exc()
                # Продолжаем ретраи, если это не Context Limit Error
                if not (isinstance(e, ValueError) and "Context limit" in str(e)): # TODO: Лучше проверять специфические исключения от translation_module
                     time.sleep(1) # Ждем перед следующей попыткой
                     continue
                else:
                     break # Не ретраим при ошибке контекстного лимита

        # 5. Обрабатываем финальный результат после ретраев
        if status == 'completed':
             # Результат получен, сохраняем в кэш
             if workflow_cache_manager.save_section_stage_result(book_id, section_id, SUMMARIZATION_STAGE_NAME, summarized_text):
                 print(f"[WorkflowProcessor] Результат суммаризации для секции {section_id} (Длина: {len(summarized_text)} chars). Сохранен в кэш.")
                 # Статус уже 'completed'
             else:
                 status = 'error_caching'
                 error_message = "Ошибка сохранения в кеш."
                 print(f"[WorkflowProcessor] ОШИБКА сохранения в кеш для {book_id}/{section_id}: {error_message}")
                 workflow_cache_manager.delete_section_stage_cache(book_id, section_id, SUMMARIZATION_STAGE_NAME) # Удаляем потенциально некорректный кеш
                 summarized_text = None # Обнуляем результат, чтобы не использовать некорректный кеш
        elif status == 'error': # Если после ретраев статус остался 'error' (например, CONTEXT_LIMIT_ERROR или финальный пустой результат без DEBUG_ALLOW_EMPTY)
             print(f"[WorkflowProcessor] Финальная ошибка для секции {section_id} (суммаризация): {error_message}")
             # Статус уже 'error', error_message установлен

        elif summarized_text is None or summarized_text.strip() == "": # Финальный пустой результат после всех ретраев
             if DEBUG_ALLOW_EMPTY:
                 status = 'completed_empty'
                 error_message = 'Empty model response (allowed in DEBUG_ALLOW_EMPTY)'
                 print(f"[WorkflowProcessor] Модель вернула пустой результат после ретраев. DEBUG_ALLOW_EMPTY=True. Статус: completed_empty для секции {section_id}.")
                 # Сохраняем пустой файл в кэш, чтобы пометить как завершенную с пустым результатом
                 if workflow_cache_manager.save_section_stage_result(book_id, section_id, SUMMARIZATION_STAGE_NAME, ""):
                      print(f"[WorkflowProcessor] Пустой результат сохранен в кэш для секции {section_id}.")
                 else:
                      status = 'error_caching'
                      error_message = "Ошибка сохранения пустого результата в кеш."
                      print(f"[WorkflowProcessor] ОШИБКА сохранения пустого результата в кеш для {book_id}/{section_id}: {error_message}")
                 # Проверка времени после обновления статуса
                 try:
                     with current_app.app_context():
                         updated_section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
                         if updated_section_info and 'stage_statuses' in updated_section_info and SUMMARIZATION_STAGE_NAME in updated_section_info['stage_statuses']:
                             status_info = updated_section_info['stage_statuses'][SUMMARIZATION_STAGE_NAME]
                             print(f"[DEBUG_TIME_CHECK] Секция {section_id}, Этап {SUMMARIZATION_STAGE_NAME}, Статус {status}: start_time={status_info.get('start_time')}, end_time={status_info.get('end_time')}")
                         else:
                             print(f"[DEBUG_TIME_CHECK] Не удалось получить обновленный статус для секции {section_id}.")
                 except Exception as time_check_err:
                     print(f"[DEBUG_TIME_CHECK] Ошибка при проверке времени для секции {section_id}: {time_check_err}")
             else:
                 status = 'error'
                 error_message = 'Empty model response after retries.'
                 print(f"[WorkflowProcessor] Модель вернула пустой результат после ретраев. DEBUG_ALLOW_EMPTY=False. Статус: error для секции {section_id}.")
                 # Проверка времени после обновления статуса
                 try:
                     with current_app.app_context():
                         updated_section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
                         if updated_section_info and 'stage_statuses' in updated_section_info and SUMMARIZATION_STAGE_NAME in updated_section_info['stage_statuses']:
                             status_info = updated_section_info['stage_statuses'][SUMMARIZATION_STAGE_NAME]
                             print(f"[DEBUG_TIME_CHECK] Секция {section_id}, Этап {SUMMARIZATION_STAGE_NAME}, Статус {status}: start_time={status_info.get('start_time')}, end_time={status_info.get('end_time')}")
                         else:
                             print(f"[DEBUG_TIME_CHECK] Не удалось получить обновленный статус для секции {section_id}.")
                 except Exception as time_check_err:
                     print(f"[DEBUG_TIME_CHECK] Ошибка при проверке времени для секции {section_id}: {time_check_err}")


        # 6. Обновляем статус секции в БД
        with current_app.app_context():
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, status, error_message=error_message)

        print(f"[WorkflowProcessor] Суммаризация для секции ID {section_id} книги {book_id} завершена со статусом: {status}.")

        return status in ['completed', 'completed_empty'] # Возвращаем True, если успешно завершено (включая пустые)

    except Exception as e:
        print(f"[WorkflowProcessor] Неожиданная ОШИБКА при обработке секции {section_id}: {e}")
        traceback.print_exc()
        # Обновляем статус секции на 'error' в случае необработанного исключения
        try:
             with current_app.app_context():
                  workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, SUMMARIZATION_STAGE_NAME, 'error', error_message=f'Unexpected error: {e}')
        except Exception as db_err:
             print(f"[WorkflowProcessor] ОШИБКА при попытке записать статус ошибки для секции {section_id}: {db_err}")
             traceback.print_exc()

        return False

# --- New Helper Function for Stage Transition ---
def transition_section_to_next_stage(book_id: str, section_id: int, current_stage_name: str):
    """
    Проверяет статус секции на текущем этапе и ставит ее в очередь на следующий посекционный этап, если текущий успешно завершен.
    """
    print(f"[WorkflowProcessor] Попытка перехода для секции {section_id} книги {book_id} после этапа '{current_stage_name}'.")

    try:
        with current_app.app_context():
            section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
            if not section_info:
                print(f"[WorkflowProcessor] Ошибка перехода: Секция с ID {section_id} не найдена в БД.")
                return False

            stage_statuses = section_info.get('stage_statuses', {})
            current_stage_status = stage_statuses.get(current_stage_name, {}).get('status')

            # Проверяем, успешно ли завершен текущий этап (включая пропущенные и пустые результаты)
            if current_stage_status in ['completed', 'completed_empty', 'skipped']:
                # Получаем имя следующего посекционного этапа
                next_stage_name = workflow_db_manager.get_next_per_section_stage_name_workflow(current_stage_name)

                if next_stage_name:
                    # Проверяем, что статус секции для следующего этапа еще не final (completed, error, skipped, processing, queued)
                    next_stage_current_status = stage_statuses.get(next_stage_name, {}).get('status', 'pending')
                    if next_stage_current_status not in ['completed', 'cached', 'error', 'skipped', 'processing', 'queued']:
                        print(f"[WorkflowProcessor] Секция {section_id} ({current_stage_name}) успешно завершена. Ставим в очередь на этап '{next_stage_name}'.")
                        # Устанавливаем статус секции для следующего этапа на 'queued'
                        workflow_db_manager.update_section_stage_status_workflow(
                            book_id,
                            section_id,
                            next_stage_name,
                            'queued',
                            error_message=None
                        )
                        return True # Успешно поставлено в очередь
                    else:
                        print(f"[WorkflowProcessor] Секция {section_id} ({current_stage_name}) успешно завершена, но следующий этап '{next_stage_name}' уже в статусе '{next_stage_current_status}'. Переход не требуется.")
                else:
                    print(f"[WorkflowProcessor] Секция {section_id} ({current_stage_name}) успешно завершена. Следующего посекционного этапа нет. Завершение обработки секции.")

            else:
                print(f"[WorkflowProcessor] Секция {section_id} ({current_stage_name}) не в конечном успешном статусе ('{current_stage_status}'). Переход не требуется.")

        return False # Переход не произошел или не требовался

    except Exception as e:
        print(f"[WorkflowProcessor] Необработанная ОШИБКА при переходе секции {section_id} после этапа '{current_stage_name}': {e}")
        traceback.print_exc()
        # В случае ошибки при переходе, можно пометить секцию или книгу как ошибочную
        # Пока просто логируем
        return False

# TODO: Добавить функцию start_book_workflow для запуска процесса для всей книги DONE
def start_book_workflow(book_id: str):
    """
    Запускает многоэтапный рабочий процесс для указанной книги.
    Начинает с этапа суммаризации для всех секций.
    """
    print(f"[WorkflowProcessor] Запуск рабочего процесса для книги ID: {book_id}")

    try:
        # --- ИЗМЕНЕНИЕ: Оборачиваем initial DB calls in app context ---
        with current_app.app_context():
            book_info = workflow_db_manager.get_book_workflow(book_id)
            if not book_info:
                print(f"[WorkflowProcessor] Ошибка запуска: Книга с ID {book_id} не найдена.")
                return False

            current_status = book_info.get('current_workflow_status', 'idle')
            # Не запускаем, если уже в процессе или завершено успешно
            if current_status in ['processing', 'completed']:
                print(f"[WorkflowProcessor] Рабочий процесс для книги ID {book_id} уже в статусе '{current_status}'. Запуск отменен.")
                return False

            # Устанавливаем общий статус книги на 'processing'
            workflow_db_manager.update_book_workflow_status(book_id, 'processing')

            # Получаем все секции для книги
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            if not sections:
                print(f"[WorkflowProcessor] Предупреждение: Для книги ID {book_id} не найдено секций.")
                workflow_db_manager.update_book_workflow_status(book_id, 'completed', error_message='No sections found')
                return True
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # Get all stages in order
        with current_app.app_context():
            stages = workflow_db_manager.get_all_stages_ordered_workflow()
            if not stages:
                 print(f"[WorkflowProcessor] ОШИБКА: Этапы рабочего процесса не определены.")
                 workflow_db_manager.update_book_workflow_status(book_id, 'error', 'Configuration error: Workflow stages not defined.')
                 return False

        print(f"[WorkflowProcessor] Определены этапы рабочего процесса: {[stage['stage_name'] for stage in stages]}")

        # Iterate through stages and process them sequentially
        book_workflow_completed_successfully = True
        book_workflow_error = False
        book_workflow_error_message = None

        for stage in stages:
            stage_name = stage['stage_name']
            is_per_section_stage = stage['is_per_section']
            print(f"[WorkflowProcessor] Обработка этапа '{stage_name}' (per-section: {is_per_section_stage}) для книги ID {book_id}.")

            with current_app.app_context():
                # Get the current status of this stage for the book
                # Using get_book_stage_statuses_workflow and filtering by stage_name
                book_stage_statuses = workflow_db_manager.get_book_stage_statuses_workflow(book_id)
                book_stage_status_info = book_stage_statuses.get(stage_name, None)
                current_stage_status = book_stage_status_info.get('status', 'pending') if book_stage_status_info else 'pending'

            print(f"[WorkflowProcessor] Текущий статус этапа '{stage_name}' для книги {book_id}: '{current_stage_status}'.")

            # If stage is already completed or has errors, move to the next stage
            if current_stage_status in ['completed', 'completed_with_errors', 'error']:
                print(f"[WorkflowProcessor] Этап '{stage_name}' для книги {book_id} уже завершен со статусом '{current_stage_status}'. Пропускаем обработку и переходим к следующему.")
                if current_stage_status in ['completed_with_errors', 'error']:
                     book_workflow_error = True # Mark book workflow as having errors
                     book_workflow_error_message = f"Stage '{stage_name}' completed with errors." # TODO: Aggregate messages
                continue # Move to the next stage in the loop

            # --- НОВОЕ: Устанавливаем статус этапа книги на 'processing' перед началом обработки ---
            # Только если текущий статус не 'processing' (может быть 'queued' или 'pending')
            if current_stage_status not in ['processing']:
                 with current_app.app_context():
                     print(f"[WorkflowProcessor] Установка статуса этапа '{stage_name}' для книги {book_id} на 'processing'.")
                     workflow_db_manager.update_book_stage_status_workflow(
                         book_id,
                         stage_name,
                         'processing',
                         error_message=None # Сбрасываем ошибку при старте
                     )
            # --- КОНЕЦ НОВОГО ---

            # Process the stage if it's pending or processing (or queued for per-section)
            stage_processing_successful = False # Flag to track if processing this stage was successful

            if is_per_section_stage:
                # --- Process Per-Section Stage ---
                print(f"[WorkflowProcessor] Запуск обработки посекционного этапа '{stage_name}' для книги {book_id}.")
                
                # TODO: Implement per-section stage processing logic (identify queued sections, process them in pool)
                # For now, sequential processing of sections in 'pending' status for this stage

                sections_to_process = []
                for section_data in sections:
                     section_id = section_data['section_id']
                     stage_statuses = section_data.get('stage_statuses', {})
                     section_stage_status = stage_statuses.get(stage_name, {}).get('status', 'pending')

                     # If status is pending or queued, add to processing list
                     if section_stage_status in ['pending', 'queued']:
                          sections_to_process.append(section_id)

                # --- LOGGING: Sections to process for this stage ---
                print(f"[WorkflowProcessor] LOG: Этап '{stage_name}', Книга ID {book_id}. Секций для обработки: {len(sections_to_process)}. ID секций: {sections_to_process}")
                # --- END LOGGING ---

                if not sections_to_process:
                    print(f"[WorkflowProcessor] Нет секций в статусе 'pending' или 'queued' для этапа '{stage_name}' книги {book_id}. Пропускаем обработку секций.")
                    # If there are no sections to process, check if all sections are in final states.
                    with current_app.app_context():
                         all_sections = workflow_db_manager.get_sections_for_book_workflow(book_id)

                    all_sections_in_final_state = True
                    for section in all_sections:
                         stage_statuses = section.get('stage_statuses', {})
                         section_stage_status = stage_statuses.get(stage_name, {}).get('status')
                         if section_stage_status not in ['completed', 'cached', 'completed_empty', 'skipped', 'error']:
                              all_sections_in_final_state = False
                              break

                    if all_sections_in_final_state:
                         print(f"[WorkflowProcessor] Все секции для этапа '{stage_name}' книги {book_id} уже в конечном статусе. Считаем этап завершенным.")
                         stage_processing_successful = True # Stage is considered completed if all sections are in final states
                         # TODO: Update book stage status to completed here if all sections are final
                    else:
                         print(f"[WorkflowProcessor] Есть секции, которые еще не в конечном статусе для этапа '{stage_name}' книги {book_id}, но нет секций для обработки. Возможная проблема или ждем другие процессы.")
                         # This might happen if some sections are 'processing' in a real async scenario.
                         # For now, in sequential sync, this means something is wrong.
                         book_workflow_error = True
                         book_workflow_error_message = f"Issue processing sections for stage '{stage_name}'. Some sections not in final state."
                         # Don't break yet, might need to check overall book status at the end

                else:
                    # Process sections sequentially for now
                    for section_id_to_process in sections_to_process:
                         print(f"[WorkflowProcessor] Запуск обработки секции ID {section_id_to_process} для этапа '{stage_name}'...")
                         # Determine which processing function to call based on stage_name
                         if stage_name == SUMMARIZATION_STAGE_NAME:
                              section_success = process_section_summarization(book_id, section_id_to_process)
                         # TODO: Add other per-section stage processing functions here (e.g., Translate)
                         elif stage_name == 'translate':
                             # Call the placeholder function which updates DB status to 'completed'
                             # --- LOGGING: Before calling process_section_translate ---
                             print(f"[WorkflowProcessor] LOG: Вызов process_section_translate для секции {section_id_to_process}, этап '{stage_name}'.")
                             # --- END LOGGING ---
                             section_success = process_section_translate(book_id, section_id_to_process)
                             # --- LOGGING: After calling process_section_translate ---
                             print(f"[WorkflowProcessor] LOG: process_section_translate для секции {section_id_to_process} завершен. Результат success: {section_success}.")
                             # --- END LOGGING ---
                         else:
                               print(f"[WorkflowProcessor] ОШИБКА: Неизвестный посекционный этап: {stage_name}")
                               section_success = False # Treat as failure

                    # After attempting to process all queued/pending sections for this stage,
                    # re-check the overall status of this stage for the book by counting section statuses.
                    with current_app.app_context():
                         sections_after_processing = workflow_db_manager.get_sections_for_book_workflow(book_id)

                    # --- LOGGING: Section statuses after processing this stage ---
                    print(f"[WorkflowProcessor] LOG: Статусы секций для этапа '{stage_name}' после обработки:")
                    for section in sections_after_processing:
                        stage_statuses = section.get('stage_statuses', {})
                        section_stage_status = stage_statuses.get(stage_name, {}).get('status')
                        print(f"  Секция ID {section['section_id']} (EPUB ID: {section['section_epub_id']}): Статус '{stage_name}' = '{section_stage_status}'")
                    # --- END LOGGING ---

                    completed_sections_count = 0
                    error_sections_count = 0
                    all_sections_count = len(sections_after_processing)

                    for section in sections_after_processing:
                         stage_statuses = section.get('stage_statuses', {})
                         section_stage_status = stage_statuses.get(stage_name, {}).get('status') # Get status for the current stage

                         if section_stage_status in ['completed', 'cached', 'completed_empty', 'skipped']:
                             completed_sections_count += 1
                         elif section_stage_status and (section_stage_status == 'error' or section_stage_status.startswith('error_')):
                             error_sections_count += 1

                    # Determine the stage status for the book level
                    stage_status_after_tasks = 'processing' # Default status is still processing tasks
                    stage_error_message = None
                    completion_count = completed_sections_count + error_sections_count

                    # --- LOGGING: Stage status calculation result ---
                    print(f"[WorkflowProcessor] LOG: Результаты подсчета статусов секций для этапа '{stage_name}':")
                    print(f"  completed_sections_count: {completed_sections_count}")
                    print(f"  error_sections_count: {error_sections_count}")
                    print(f"  all_sections_count: {all_sections_count}")
                    # --- END LOGGING ---

                    if all_sections_count > 0 and completion_count == all_sections_count:
                         # Все секции достигли конечного статуса (completed, cached, completed_empty, skipped, error)
                         if error_sections_count == 0:
                             stage_status_after_tasks = 'completed'
                             print(f"[WorkflowProcessor] Все секции для стадии '{stage_name}' для книги ID {book_id} завершены успешно.")
                         else:
                             stage_status_after_tasks = 'completed_with_errors' # Новый статус для стадии завершенной с ошибками секций
                             stage_error_message = f'Stage tasks completed with errors in {error_sections_count}/{all_sections_count} sections.'
                             print(f"[WorkflowProcessor] Все секции для стадии '{stage_name}' для книги ID {book_id} завершены с ошибками.")

                    # --- LOGGING: Final stage status after task processing ---
                    print(f"[WorkflowProcessor] LOG: Определен финальный статус этапа '{stage_name}' после обработки задач: '{stage_status_after_tasks}'.")
                    # --- END LOGGING ---

                    # Update the book stage status in the DB
                    try:
                         with current_app.app_context():
                              workflow_db_manager.update_book_stage_status_workflow(
                                  book_id,
                                  stage_name,
                                  stage_status_after_tasks,
                                  completed_count=completed_sections_count,
                                  total_count=all_sections_count,
                                  error_message=stage_error_message
                              )
                         # --- LOGGING: Success after updating book stage status ---
                         print(f"[WorkflowProcessor] LOG: Успешно обновлен статус этапа книги '{stage_name}' до '{stage_status_after_tasks}'.")
                         # --- END LOGGING ---
                    except Exception as e:
                         # --- LOGGING: Error updating book stage status ---
                         print(f"[WorkflowProcessor] ОШИБКА при обновлении статуса этапа книги '{stage_name}' в БД: {e}")
                         traceback.print_exc()
                         # --- END LOGGING ---
                         # If updating stage status fails, we should mark the overall workflow as error
                         book_workflow_error = True
                         book_workflow_error_message = f"Failed to update status for stage '{stage_name}': {e}"
                         # We still let the loop continue to process other stages if any
                         # The final status will be 'error' due to book_workflow_error = True
                         pass # Let the exception handler in the outer try block handle this potentially

            else: # Stage is book-level (is_per_section_stage is False)
                # --- Process Book-Level Stage ---
                print(f"[WorkflowProcessor] Запуск обработки книжного этапа '{stage_name}' для книги {book_id}.")
                # Check if the stage needs processing (pending or queued)
                if current_stage_status in ['pending', 'queued']:
                    print(f"[WorkflowProcessor] Этап '{stage_name}' книги {book_id} в статусе '{current_stage_status}'. Запускаем обработку.")
                    # Determine which book-level processing function to call based on stage_name
                    
                    stage_status_after_tasks = 'error' # Default for book-level task result
                    stage_error_message = 'Unknown error during book-level task.'

                    try:
                        if stage_name == ANALYSIS_STAGE_NAME:
                            # process_book_analysis теперь возвращает финальный статус этапа
                            stage_status_after_tasks = process_book_analysis(book_id)
                            # process_book_analysis теперь не обновляет статус книги на processing/final
                            
                        # TODO: Add other book-level stage processing functions here (e.g., Epub Creation)
                        elif stage_name == 'epub_creation': 
                            # process_book_epub_creation теперь возвращает финальный статус этапа
                            stage_status_after_tasks = process_book_epub_creation(book_id)
                            # process_book_epub_creation теперь не обновляет статус книги на processing/final

                        else:
                            print(f"[WorkflowProcessor] ОШИБКА: Неизвестный книжный этап: {stage_name}")
                            stage_status_after_tasks = 'error' # Treat as failure
                            stage_error_message = f'Unknown book-level stage: {stage_name}'
                            book_workflow_error = True
                            book_workflow_error_message = stage_error_message

                    except Exception as e:
                        # Catch unexpected exceptions during book-level task execution
                        stage_status_after_tasks = 'error'
                        stage_error_message = f"Exception during book-level stage '{stage_name}' processing: {e}"
                        print(f"[WorkflowProcessor] ОШИБКА при обработке книжного этапа '{stage_name}': {e}")
                        traceback.print_exc()
                        book_workflow_error = True
                        book_workflow_error_message = stage_error_message

                    # After the book-level processing function returns,
                    # we get its determined status (stage_status_after_tasks).
                    # We DON'T immediately update the book stage status to this final status here.
                    # The overall loop logic below handles stage transitions and status updates.

                elif current_stage_status == 'processing':
                     print(f"[WorkflowProcessor] Этап '{stage_name}' книги {book_id} в статусе 'processing'. Пропускаем запуск, ожидаем завершения.")
                     # In a real async scenario, we might wait or check progress here.
                     # In this sync implementation, 'processing' means it was started but didn't finish in a previous run.
                     # We should probably re-run it or mark it as error if it stays in processing for too long.
                     # For now, we'll just skip and rely on the next run to pick it up or error out.
                     # TODO: Implement logic to handle 'processing' status on startup/rerun.
                     stage_status_after_tasks = 'processing' # Keep status as is for now if it was already processing

                else: # Stage was already completed, completed_with_errors, or error (handled at loop start)
                     # Stage status is already final, just ensure book_workflow_error is set if stage had errors
                     if current_stage_status in ['completed_with_errors', 'error']:
                          book_workflow_error = True
                          book_workflow_error_message = f"Stage '{stage_name}' already completed with errors." # TODO: Aggregate messages
                     stage_status_after_tasks = current_stage_status # Keep existing final status

            # --- Проверка, нужно ли прервать рабочий процесс из-за ошибки на этом этапе ---
            # ИСПРАВЛЕНИЕ: НЕ прерываем цикл немедленно при final_stage_status == 'error'
            # Вместо этого, просто отмечаем, что произошла ошибка в workflow
            # Критические ошибки конфигурации уже обрабатываются в начале функции.
            if stage_status_after_tasks == 'error':
                print(f"[WorkflowProcessor] Этап '{stage_name}' завершился с ошибкой. Отмечаем ошибку в workflow.")
                book_workflow_error = True
                # Можно агрегировать сообщения об ошибках, но пока просто отмечаем наличие ошибки.
                # book_workflow_error_message = f\"Error in stage '{stage_name}'.\"
                # TODO: Агрегировать сообщения об ошибках из этапов в final book_workflow_error_message

            # Если этап завершен успешно (или с ошибками секций для посекционного), переходим к следующему
            # Этот переход неявный в данном синхронном цикле, он просто переходит к следующей итерации.
            # Логика прерывания при 'error' выше обеспечивает остановку.

        # --- Конец цикла по этапам ---

        # --- Финальное обновление общего статуса книги ---
        with current_app.app_context():
            # Re-fetch book info to get the latest stage statuses
            latest_book_info = workflow_db_manager.get_book_workflow(book_id)
            if not latest_book_info:
                 print(f"[WorkflowProcessor] Ошибка: Не удалось получить финальную информацию о книге {book_id} для определения статуса.")
                 # Keep the current status (likely error from previous issues) or set to error if somehow not already
                 if not book_workflow_error:
                      workflow_db_manager.update_book_workflow_status(book_id, 'error', error_message='Failed to fetch final book info.')
                 return False # Indicate failure to finalize status

            latest_book_stage_statuses = workflow_db_manager.get_book_stage_statuses_workflow(book_id)
            final_book_status = 'completed' # Assume completed initially
            final_error_message = None
            has_errors = False
            has_completed_with_errors = False
            has_pending_or_processing = False # Should not happen if loop finished correctly in sync mode

            # Iterate through all stages to check their final statuses
            for stage in stages:
                 stage_name = stage['stage_name']
                 stage_status_info = latest_book_stage_statuses.get(stage_name, {})
                 stage_status = stage_status_info.get('status', 'pending')
                 stage_error = stage_status_info.get('error_message')

                 if stage_status == 'error':
                      has_errors = True
                      # Aggregate error messages if needed, for now just mark that an error occurred
                      if final_error_message is None:
                           final_error_message = f"Stage '{stage_name}' failed: {stage_error or 'No specific error provided.'}"
                      else:
                           final_error_message += f"; Stage '{stage_name}' failed: {stage_error or 'No specific error provided.'}"
                 elif stage_status == 'completed_with_errors':
                      has_completed_with_errors = True
                      if final_error_message is None:
                           final_error_message = f"Stage '{stage_name}' completed with errors: {stage_error or 'No specific error provided.'}"
                      else:
                           final_error_message += f"; Stage '{stage_name}' completed with errors: {stage_error or 'No specific error provided.'}"
                 elif stage_status in ['pending', 'queued', 'processing']:
                      # This indicates an issue if the loop finished but a stage is not in a final state
                      has_pending_or_processing = True
                      has_errors = True # Treat as an error state for the workflow overall
                      final_book_status = 'error'
                      final_error_message = f"Workflow ended with stage '{stage_name}' still in status '{stage_status}'."
                      break # No need to check further stages if one is stuck
                 # Note: 'cached', 'completed', 'completed_empty', 'skipped' are considered successful for determining overall status here.

            if has_pending_or_processing:
                 final_book_status = 'error'
            elif has_errors:
                 final_book_status = 'error'
            elif has_completed_with_errors:
                 final_book_status = 'completed_with_errors'
            else:
                 final_book_status = 'completed' # All stages completed successfully (including skipped, empty, cached)

            print(f"[WorkflowProcessor] Финальное определение статуса для книги {book_id}. Has Errors: {has_errors}, Has Completed With Errors: {has_completed_with_errors}, Has Pending/Processing: {has_pending_or_processing}. Финальный статус: {final_book_status}.")

            workflow_db_manager.update_book_workflow_status(book_id, final_book_status, error_message=final_error_message)

        print(f"[WorkflowProcessor] Рабочий процесс start_book_workflow для книги ID: {book_id} завершен (основная функция). Финальный статус книги: {final_book_status}.")

        return True # Or return False depending on final_book_status if needed elsewhere

    except Exception as e:
         print(f"[WorkflowProcessor] Необработанная ОШИБКА в start_book_workflow для книги {book_id}: {e}")
         traceback.print_exc()
         # Update the overall book status to 'error' in case of unhandled exception
         try:
              with current_app.app_context():
                   workflow_db_manager.update_book_workflow_status(book_id, 'error', error_message=f'Unexpected workflow error: {e}')
         except Exception as db_err:
              print(f"[WorkflowProcessor] ОШИБКА при попытке записать статус ошибки для книги {book_id}: {db_err}")
              traceback.print_exc()

         return False

# --- New function to collect summarized text for a book ---
def collect_book_summary_text(book_id: str) -> str:
    """
    Collects the summarized text for all sections of a book from the cache.
    Returns a single string containing all non-empty summaries.
    """
    print(f"[WorkflowProcessor] Собираем текст суммаризаций для книги {book_id}")
    collected_text_parts = []
    try:
        with current_app.app_context():
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)

        if not sections:
            print(f"[WorkflowProcessor] Предупреждение: Нет секций для книги {book_id} при сборе суммаризации.")
            return ""

        for section_data in sections:
            section_id = section_data['section_id']
            # section_epub_id = section_data['section_epub_id'] # Not needed for collection
            # Load summary for this section
            summary_text = workflow_cache_manager.load_section_stage_result(
                book_id,
                section_id,
                SUMMARIZATION_STAGE_NAME
            )

            # Check if the summary is not None and not empty after stripping whitespace
            if summary_text is not None and summary_text.strip():
                # Just append the text. Separation will be added when joining.
                collected_text_parts.append(summary_text)

        # Join the parts with double newline for clear separation
        full_summary_text = "\n\n".join(collected_text_parts)
        print(f"[WorkflowProcessor] Собрано {len(collected_text_parts)} непустых суммаризаций. Общий текст длиной {len(full_summary_text)} символов.")
        return full_summary_text

    except Exception as e:
        print(f"[WorkflowProcessor] ОШИБКА при сборе текста суммаризаций для книги {book_id}: {e}")
        traceback.print_exc()
        return "" # Return empty string on error

# --- New function for Book-level Analysis ---
def process_book_analysis(book_id: str):
    """
    Processes the analysis stage for the entire book.
    Collects summaries, calls the analysis model, saves the result, and updates book stage status.
    """
    print(f"[WorkflowProcessor] Начат процесс анализа для книги {book_id}")
    status = 'error' # Default status
    error_message = 'Unknown error'

    try:
        with current_app.app_context():
            book_info = workflow_db_manager.get_book_workflow(book_id)
            if not book_info:
                print(f"[WorkflowProcessor] Ошибка: Книга с ID {book_id} не найдена в БД для анализа.")
                # Cannot update book status if book is not found, return False
                return False

            # 1. Обновляем статус этапа анализа книги на 'processing'
            workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, 'processing')

        # 2. Собираем текст суммаризаций со всех секций
        collected_summary_text = collect_book_summary_text(book_id)

        if not collected_summary_text.strip():
            print(f"[WorkflowProcessor] Собранный текст суммаризации для книги {book_id} пуст или состоит только из пробелов. Пропускаем анализ.")
            status = 'completed_empty'
            error_message = 'Collected summary text is empty or whitespace only.'
            # TODO: Save an empty result in cache for consistency using save_book_stage_result (needs implementation)
            # workflow_cache_manager.save_book_stage_result(book_id, ANALYSIS_STAGE_NAME, "") # Placeholder call

            with current_app.app_context():
                 workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, status, error_message=error_message)
            return True # Analysis stage completed empty

        # 3. Вызываем модель анализа с ретраями
        target_language = book_info['target_language']
        model_name = ANALYSIS_MODEL
        # ANALYSIS_PROMPT_TEMPLATE already contains the English instruction.

        analysis_result = None
        # status and error_message are already initialized as 'error' and 'Unknown error'

        for attempt in range(MAX_RETRIES + 1):
            print(f"[WorkflowProcessor] Попытка {attempt + 1}/{MAX_RETRIES + 1} вызова модели для анализа книги {book_id}...")
            try:
                 print(f"[WorkflowProcessor] Вызов translate_text для анализа книги {book_id} ({model_name} -> {target_language})")
                 analysis_result = translation_module.translate_text(
                     text_to_translate=collected_summary_text, # Pass the collected summary text
                     target_language=target_language,
                     model_name=model_name,
                     # Combine the existing analysis template with the new gender instruction
                     prompt_ext=f"{ANALYSIS_PROMPT_TEMPLATE} {MODEL_GENDER_INSTRUCTION_PROMPT}", # Pass the combined instruction
                     operation_type=ANALYSIS_STAGE_NAME # Pass the operation type
                 )
                 print(f"[WorkflowProcessor] Результат translate_text: {analysis_result[:100] if analysis_result else 'None'}... (длина {len(analysis_result) if analysis_result is not None else 'None'})")

                 if analysis_result is not None and analysis_result.strip() != "":
                      status = 'completed'
                      error_message = None
                      print(f"[WorkflowProcessor] Модель вернула непустой результат на попытке {attempt + 1}.")
                      break # Success, exit retry loop
                 elif analysis_result == translation_module.EMPTY_RESPONSE_ERROR:
                      error_message = "API returned EMPTY_RESPONSE_ERROR."
                      print(f"[WorkflowProcessor] Warning: Model returned EMPTY_RESPONSE_ERROR on attempt {attempt + 1}.")
                 elif analysis_result == translation_module.CONTEXT_LIMIT_ERROR:
                      status = 'error' # Context limit is not retried
                      error_message = "API returned CONTEXT_LIMIT_ERROR."
                      print(f"[WorkflowProcessor] Error: Model returned CONTEXT_LIMIT_ERROR on attempt {attempt + 1}. No retry.")
                      break
                 else: # None or empty string after stripping whitespace
                     error_message = "Model returned empty result (None or empty string)."
                     print(f"[WorkflowProcessor] Warning: Model returned empty result on attempt {attempt + 1}.")

            except Exception as e:
                 status = 'error' # Catch other exceptions
                 error_message = f"Exception during model call: {e}"
                 print(f"[WorkflowProcessor] ERROR calling model for book analysis {book_id} on attempt {attempt + 1}: {e}")
                 traceback.print_exc()
                 # Continue retrying if not a Context Limit Error
                 # TODO: Better check for specific exceptions from translation_module
                 if not (isinstance(e, ValueError) and "Context limit" in str(e)):
                      time.sleep(1) # Wait before next attempt
                      continue
                 else:
                      break # Do not retry on context limit error

        # 4. Сохраняем результат анализа книги
        if status == 'completed':
             if workflow_cache_manager.save_book_stage_result(book_id, ANALYSIS_STAGE_NAME, analysis_result):
                 print(f"[WorkflowProcessor] Результат анализа для книги {book_id} (Длина: {len(analysis_result)} chars). Сохранен в кэш.")
                 # Status is already 'completed'
             else:
                 status = 'error_caching'
                 error_message = "Ошибка сохранения результата анализа в кеш."
                 print(f"[WorkflowProcessor] ОШИБКА сохранения результата анализа в кеш для {book_id}: {error_message}")
                 # TODO: Implement delete_book_stage_result if needed, or handle potential corrupted cache
                 analysis_result = None # Clear result to avoid using potentially bad cache
        elif status == 'error': # If status remained 'error' after retries (e.g., CONTEXT_LIMIT_ERROR or final empty result without DEBUG_ALLOW_EMPTY)
             print(f"[WorkflowProcessor] Финальная ошибка при анализе книги {book_id}: {error_message}")
             # Status is already 'error', error_message is set

        elif analysis_result is None or analysis_result.strip() == "": # Final empty result after all retries
             # Note: DEBUG_ALLOW_EMPTY check should ideally happen earlier if we want to save completed_empty cache.
             # Currently, it's handled when checking collected_summary_text.
             # If we reach here with an empty result after model call, it's an error unless DEBUG_ALLOW_EMPTY was True AND collected_summary_text was NOT empty.
             # Given the structure, an empty result after a model call when collected_summary_text was NOT empty should be treated as an error.
             status = 'error'
             error_message = 'Empty model response after retries.'
             print(f"[WorkflowProcessor] Модель анализа вернула пустой результат после ретраев для книги {book_id}. Статус: error.")

        # 5. Обновляем финальный статус этапа анализа книги
        with current_app.app_context():
             workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, status, error_message=error_message)
             print(f"[WorkflowProcessor] Этап анализа для книги {book_id} завершен со статусом: {status}.")

        return status in ['completed', 'completed_empty'] # Return True if completed successfully or empty

    except Exception as e:
        status = 'error_unknown'
        error_message = f"Необработанная ошибка при анализе книги {book_id}: {e}"
        print(f"[WorkflowProcessor] {error_message}")
        traceback.print_exc()
        # Update status in DB on error
        try:
             with current_app.app_context():
                  # Ensure book exists before attempting status update on exception
                  if workflow_db_manager.get_book_workflow(book_id):
                       workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, status, error_message=error_message)
        except Exception as db_err:
             print(f"[WorkflowProcessor] ОШИБКА при попытке записать статус ошибки для анализа книги {book_id}: {db_err}")
             traceback.print_exc()
        return False # Return False on error

# --- New function for Section-level Translation ---
def process_section_translate(book_id: str, section_id: int):
    """
    Процессит перевод одной секции.
    (Заглушка: Реальная логика перевода будет добавлена позже.)
    """
    print(f"[WorkflowProcessor] Placeholder: Начат процесс перевода для секции {section_id} книги {book_id}")
    try:
        with current_app.app_context():
            # Имитируем успешное завершение
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'completed', error_message=None)
            # TODO: Реализовать реальную логику перевода секции и сохранение результата

        print(f"[WorkflowProcessor] Placeholder: Перевод для секции ID {section_id} книги {book_id} завершен со статусом: completed.")
        return True # Имитируем успех
    except Exception as e:
        print(f"[WorkflowProcessor] Placeholder ОШИБКА при обработке перевода секции {section_id}: {e}")
        traceback.print_exc()
        try:
             with current_app.app_context():
                  workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'error', error_message=f'Placeholder error: {e}')
        except Exception as db_err:
             print(f"[WorkflowProcessor] Placeholder ОШИБКА при попытке записать статус ошибки для секции {section_id}: {db_err}")
             traceback.print_exc()
        return False

# --- New function for Book-level EPUB Creation ---
def process_book_epub_creation(book_id: str):
    """
    Процессит создание EPUB для всей книги.
    (Заглушка: Реальная логика создания EPUB будет добавлена позже.)
    Обновляет статус этапа 'epub_creation' в БД workflow.
    """
    print(f"[WorkflowProcessor] Placeholder: Начат процесс создания EPUB для книги {book_id}")
    
    status_to_set = 'error' # Статус по умолчанию в случае ошибки
    error_message_to_set = 'Unknown placeholder error'

    try:
        with current_app.app_context():
            # Устанавливаем статус 'processing' при начале этапа
            workflow_db_manager.update_book_stage_status_workflow(
                book_id, 
                'epub_creation', 
                'processing', 
                error_message=None # Сбрасываем ошибку при старте
            )

        # TODO: Реализовать реальную логику создания EPUB и сохранения файла
        # Например:
        # epub_file_path = create_epub_file(book_id, ...) # Реальная функция создания EPUB
        # if not epub_file_path:
        #     raise Exception("Failed to create EPUB file") # Вызываем исключение при ошибке

        # Имитация работы: представим, что здесь происходит создание EPUB и оно успешно.
        print(f"[WorkflowProcessor] Placeholder: Имитация создания EPUB для книги {book_id} прошла успешно.")

        # Если реальная логика создания EPUB завершилась успешно, 
        # устанавливаем статус 'completed'.
        status_to_set = 'completed'
        error_message_to_set = None # Сбрасываем сообщение об ошибке при успехе

    except Exception as e:
        # Если произошла ошибка при реальной логике создания EPUB,
        # перехватываем исключение и устанавливаем статус 'error'.
        status_to_set = 'error'
        error_message_to_set = f"Placeholder error during EPUB creation: {e}"
        print(f"[WorkflowProcessor] Placeholder ОШИБКА при создании EPUB для книги {book_id}: {e}")
        traceback.print_exc()
        # Продолжаем выполнение, чтобы обновить статус этапа в БД ниже.

    finally:
        # Этот блок выполняется всегда, независимо от того, было исключение или нет.
        # Обновляем статус этапа 'epub_creation' в базе данных workflow.
        try:
            with current_app.app_context():
                # Проверяем, существует ли книга, прежде чем пытаться обновить статус
                book_exists_check = workflow_db_manager.get_book_workflow(book_id)
                if book_exists_check:
                     workflow_db_manager.update_book_stage_status_workflow(
                         book_id, 
                         'epub_creation', # Имя этапа
                         status_to_set,
                         error_message=error_message_to_set
                     )
                     print(f"[WorkflowProcessor] Placeholder: Статус этапа 'epub_creation' для книги {book_id} обновлен на '{status_to_set}'.")
                     # Убедитесь, что здесь или внутри update_book_stage_status_workflow происходит коммит!
                else:
                     print(f"[WorkflowProcessor] Placeholder: Книга {book_id} не найдена при попытке обновить статус этапа 'epub_creation'.")

        except Exception as db_err:
            # Если произошла ошибка при попытке ОБНОВИТЬ статус этапа в БД
            print(f"[WorkflowProcessor] Placeholder ОШИБКА при попытке записать статус этапа 'epub_creation' для книги {book_id}: {db_err}")
            traceback.print_exc()
            # В этом случае общий статус книги, вероятно, все равно перейдет в error позже.

    # Возвращаем True, если этап завершился успешно (статус 'completed'), иначе False.
    return status_to_set == 'completed'

# --- END OF FILE workflow_processor.py ---
