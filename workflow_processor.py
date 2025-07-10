# --- START OF FILE workflow_processor.py ---

import workflow_db_manager
import epub_parser
import workflow_translation_module
import os
import traceback
import workflow_cache_manager # TODO: Implement workflow_cache_manager DONE
import time
from flask import current_app, Flask
import re
from typing import Optional

# --- Constants for Workflow Processor ---
MIN_SECTION_LENGTH = 3000 # Minimum length of clean text for summarization/analysis

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
SUMMARIZATION_MODEL = 'meta-llama/llama-4-maverick:free' #'models/gemini-2.5-flash-preview-04-17' #'qwen/qwen3-32b:free' #'google/gemini-2.0-flash-exp:free' #'meta-llama/llama-4-maverick:free' 
SUMMARIZATION_STAGE_NAME = 'summarize'

# --- Constants for Analysis Stage ---
ANALYSIS_MODEL = 'models/gemini-2.5-flash-preview-05-20' # Можно использовать ту же модель или другую
ANALYSIS_STAGE_NAME = 'analyze'

# --- Constants for Recursive Reduction Stage ---
REDUCTION_STAGE_NAME = 'reduce_text'

# --- Workflow Configuration ---
DEBUG_ALLOW_EMPTY = False # Set to True to treat empty model responses (after retries) as completed_empty instead of error
MAX_RETRIES = 2 # Number of additional retries for model calls

# --- Хардкодим модель для перевода, как для summarize/analyze ---
TRANSLATION_MODEL = 'deepseek/deepseek-chat-v3-0324:free'

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
        # Use a hardcoded signal to tell the model to summarize in the original language
        target_language_for_summarization = "ORIGINAL_LANGUAGE"
        operation_type = SUMMARIZATION_STAGE_NAME
        model_name = SUMMARIZATION_MODEL
        prompt_ext = None # ИЗМЕНЕНО: MODEL_GENDER_INSTRUCTION_PROMPT больше не используется здесь.

        summarized_text = None
        status = 'error' # Default status in case of failure
        error_message = 'Unknown error'

        for attempt in range(MAX_RETRIES + 1):
            print(f"[WorkflowProcessor] Попытка {attempt + 1}/{MAX_RETRIES + 1} вызова модели для секции {section_id} (суммаризация)...")
            try:
                summarized_text = workflow_translation_module.translate_text(
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
                elif summarized_text == workflow_translation_module.EMPTY_RESPONSE_ERROR:
                     error_message = "API вернул EMPTY_RESPONSE_ERROR."
                     print(f"[WorkflowProcessor] Предупреждение: Модель вернула EMPTY_RESPONSE_ERROR на попытке {attempt + 1}.")
                elif summarized_text == workflow_translation_module.CONTEXT_LIMIT_ERROR:
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

        # --- ВМЕСТО копирования статуса из одной секции ---
        recalculate_book_stage_status(book_id, 'summarize')
        update_overall_workflow_book_status(book_id)
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---

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

# --- Новая функция для пересчета статуса книги в workflow ---
def update_overall_workflow_book_status(book_id):
    """
    Пересчитывает и обновляет общий статус книги в workflow на основе статусов этапов и секций.
    """
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if not book_info:
        return False
    book_stage_statuses = book_info.get('book_stage_statuses', {})
    stages_ordered = workflow_db_manager.get_all_stages_ordered_workflow()
    has_processing = False
    has_error = False
    has_completed_with_errors = False
    all_completed = True
    all_pending = True
    for stage in stages_ordered:
        stage_name = stage['stage_name']
        status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
        if status in ['processing', 'queued']:
            has_processing = True
        if status not in ['pending']:
            all_pending = False
        if status in ['error'] or (isinstance(status, str) and status.startswith('error')):
            has_error = True
        if status == 'completed_with_errors':
            has_completed_with_errors = True
        if status not in ['completed', 'completed_empty', 'skipped']:
            all_completed = False
    if has_error:
        final_status = 'error'
    elif has_processing:
        final_status = 'processing'
    elif all_pending:
        final_status = 'uploaded'
    elif has_completed_with_errors:
        final_status = 'completed_with_errors'
    elif all_completed:
        final_status = 'completed'
    else:
        final_status = 'processing'
    workflow_db_manager.update_book_workflow_status(book_id, final_status)
    print(f"[WorkflowProcessor] update_overall_workflow_book_status: book_id={book_id}, current_workflow_status={final_status}")
    return True

# TODO: Добавить функцию start_book_workflow для запуска процесса для всей книги DONE
def start_book_workflow(book_id: str, app_instance: Flask, start_from_stage: Optional[str] = None):
    """
    Запускает полный рабочий процесс для книги, начиная с указанного этапа (или с самого начала).
    После завершения каждого этапа автоматически переходит к следующему.
    """
    print(f"[WorkflowProcessor] Запуск рабочего процесса для книги ID: {book_id}, старт с этапа: {start_from_stage}")

    # --- ДОБАВЛЕНО: Сброс статусов и кэша для этапов, кроме summarize ---
    if start_from_stage == 'analyze':
        print(f"[WorkflowProcessor] Сброс этапов analyze, translate, epub_creation для книги {book_id} перед повторным запуском workflow.")
        # Сброс book-level этапов
        for stage in ['analyze', 'epub_creation']:
            workflow_db_manager.update_book_stage_status_workflow(book_id, stage, 'pending', model_name=None, error_message=None)
            import workflow_cache_manager
            workflow_cache_manager.delete_book_stage_result(book_id, stage)
            # Явная проверка статуса после сброса
            status = workflow_db_manager.get_book_workflow(book_id)['book_stage_statuses'][stage]['status']
            print(f"[DEBUG] Статус этапа {stage} после сброса: {status}")
        # Сброс per-section этапа translate
        sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
        for section in sections:
            section_id = section['section_id']
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'pending', model_name=None, error_message=None)
            workflow_cache_manager.delete_section_stage_result(book_id, section_id, 'translate')
        # --- ОБНОВЛЯЮ book_info после сброса ---
        book_info = workflow_db_manager.get_book_workflow(book_id)

    stages = workflow_db_manager.get_all_stages_ordered_workflow()
    print(f"[WorkflowProcessor] Определены этапы рабочего процесса: {[stage['stage_name'] for stage in stages]}")
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if not book_info:
        print(f"[WorkflowProcessor] Книга {book_id} не найдена в Workflow DB. Прерывание.")
        return False
    # Определяем с какого этапа начинать
    start_index = 0
    if start_from_stage:
        for i, stage in enumerate(stages):
            if stage['stage_name'] == start_from_stage:
                start_index = i
                break
    # Последовательно обрабатываем этапы
    for stage in stages[start_index:]:
        stage_name = stage['stage_name']
        is_per_section_stage = stage.get('is_per_section', False)
        print(f"[WorkflowProcessor] Обработка этапа '{stage_name}' (per-section: {is_per_section_stage}) для книги ID {book_id}.")
        # Проверяем статус этапа
        book_stage_statuses = book_info.get('book_stage_statuses', {})
        current_stage_status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
        print(f"[WorkflowProcessor] Текущий статус этапа '{stage_name}' для книги {book_id}: '{current_stage_status}'.")
        if current_stage_status == 'completed':
            print(f"[WorkflowProcessor] Этап '{stage_name}' для книги {book_id} уже завершен со статусом 'completed'. Пропускаем обработку и переходим к следующему.")
            continue
        # Запускаем обработку этапа
        if is_per_section_stage:
            # Перед началом обработки секций явно выставляем статус этапа книги в 'processing', если есть незавершённые секции
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            statuses = [s.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending') for s in sections]
            if any(s in ['pending', 'queued', 'processing'] for s in statuses):
                workflow_db_manager.update_book_stage_status_workflow(book_id, stage_name, 'processing')
                # --- ДОБАВЛЯЮ: Немедленно обновить статус книги ---
                update_overall_workflow_book_status(book_id)
            # Обработка всех секций для этапа
            for section in sections:
                section_id = section['section_id']
                section_stage_status = section.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending')
                if section_stage_status in ['completed', 'completed_empty']:
                    continue
                if stage_name == 'summarize':
                    result = process_section_summarization(book_id, section_id)
                elif stage_name == 'translate':
                    result = process_section_translate(book_id, section_id)
                else:
                    result = True  # Для других этапов по умолчанию не останавливаем
                # --- ОСТАНОВКА ПРИ КРИТИЧЕСКОЙ ОШИБКЕ ---
                if result is False:
                    print(f"[WorkflowProcessor] Критическая ошибка при обработке секции {section_id} на этапе '{stage_name}'. Останавливаем этап и выставляем статус ошибки.")
                    # Выставляем статус этапа книги в 'error'
                    workflow_db_manager.update_book_stage_status_workflow(book_id, stage_name, 'error', error_message=f"Critical error in section {section_id} at stage '{stage_name}'")
                    # --- ДОБАВЛЯЮ: Явно пересчитываем статус этапа и книги ---
                    recalculate_book_stage_status(book_id, stage_name)
                    update_overall_workflow_book_status(book_id)
                    return False  # Останавливаем весь workflow (можно break, если хотим только этап)
                time.sleep(0.1)  # Чтобы не перегружать API
            # --- ДОБАВЛЯЮ: Пересчитываем статус этапа после завершения всех секций ---
            recalculate_book_stage_status(book_id, stage_name)
        else:
            # Книжный этап (анализ, создание epub, сокращение и т.д.)
            result = True  # По умолчанию успех
            if stage_name == 'analyze':
                result = process_book_analysis(book_id)
            elif stage_name == 'epub_creation':
                result = process_book_epub_creation(book_id)
            elif stage_name == 'reduce_text':
                # Проверяем, нужен ли этап сокращения (текст слишком большой?)
                book_info = workflow_db_manager.get_book_workflow(book_id)
                collected_summary_text = collect_book_summary_text(book_id)
                if len(collected_summary_text) <= 12000:  # или ANALYSIS_CHUNK_SIZE_LIMIT_CHARS
                    # Если текст уже короткий, этап сокращения не нужен
                    workflow_db_manager.update_book_stage_status_workflow(book_id, 'reduce_text', 'skipped', error_message='Сокращение не требуется')
                    print(f"[WorkflowProcessor] Этап 'reduce_text' пропущен: текст уже короткий.")
                else:
                    # Вызовем анализ с автоматической суммаризацией (он сам обновит статус reduce_text)
                    workflow_translation_module.analyze_with_summarization(
                        text_to_analyze=collected_summary_text,
                        target_language=book_info['target_language'],
                        model_name=None,
                        prompt_ext=None,
                        dict_data=None,
                        summarization_model=None,
                        book_id=book_id
                    )
            # Можно добавить другие этапы по аналогии
            
            # Проверяем результат книжного этапа
            if result is False:
                print(f"[WorkflowProcessor] Критическая ошибка на книжном этапе '{stage_name}'. Останавливаем workflow.")
                return False
        # После завершения этапа обновляем book_info для получения актуальных статусов
        book_info = workflow_db_manager.get_book_workflow(book_id)
        # --- ВЫЗЫВАЕМ обновление статуса книги ---
        update_overall_workflow_book_status(book_id)
    print(f"[WorkflowProcessor] Рабочий процесс start_book_workflow для книги ID: {book_id} завершен (основная функция). Финальный статус книги: {book_info.get('current_workflow_status') if book_info else 'unknown'}.")
    return True

# --- New function to collect summarized text for a book ---
def collect_book_summary_text(book_id: str) -> str:
    """
    Collects the summarized text for all sections of a book from the cache.
    Returns a single string containing all non-empty summaries.
    """
    print(f"[WorkflowProcessor] Собираем текст суммаризаций для книги {book_id}")
    collected_text_parts = []
    try:
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
                 print(f"[WorkflowProcessor] Вызов analyze_with_summarization для анализа книги {book_id} ({model_name} -> {target_language})")
                 analysis_result = workflow_translation_module.analyze_with_summarization(
                     text_to_analyze=collected_summary_text, # Pass the collected summary text
                     target_language=target_language,
                     model_name=model_name,
                     prompt_ext="", # prompt_ext всегда пустой для анализа
                     dict_data=None, # dict_data не нужен для анализа
                     summarization_model=ANALYSIS_MODEL, # Используем модель анализа для рекурсивной суммаризации
                     book_id=book_id # Передаем book_id для сохранения суммаризации в кэш
                 )
                 print(f"[WorkflowProcessor] Результат analyze_with_summarization: {analysis_result[:100] if analysis_result else 'None'}... (длина {len(analysis_result) if analysis_result is not None else 'None'})")

                 if analysis_result is not None and analysis_result.strip() != "":
                      status = 'completed'
                      error_message = None
                      print(f"[WorkflowProcessor] Модель вернула непустой результат на попытке {attempt + 1}.")
                      break # Success, exit retry loop
                 elif analysis_result == workflow_translation_module.EMPTY_RESPONSE_ERROR:
                      error_message = "API returned EMPTY_RESPONSE_ERROR."
                      print(f"[WorkflowProcessor] Warning: Model returned EMPTY_RESPONSE_ERROR on attempt {attempt + 1}.")
                 elif analysis_result == workflow_translation_module.CONTEXT_LIMIT_ERROR:
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
             # If we reach here with an empty result after a model call, it's an error unless DEBUG_ALLOW_EMPTY was True AND collected_summary_text was NOT empty.
             # Given the structure, an empty result after a model call when collected_summary_text was NOT empty should be treated as an error.
             status = 'error'
             error_message = 'Empty model response after retries.'
             print(f"[WorkflowProcessor] Модель анализа вернула пустой результат после ретраев для книги {book_id}. Статус: error.")

        # 5. Обновляем финальный статус этапа анализа книги
        workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, status, error_message=error_message)
        print(f"[WorkflowProcessor] Этап анализа для книги {book_id} завершён со статусом: {status}.")

        # --- ВМЕСТО копирования статуса из одной секции ---
        recalculate_book_stage_status(book_id, 'analyze')
        update_overall_workflow_book_status(book_id)
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---

        return status in ['completed', 'completed_empty'] # Return True if completed successfully or empty

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[WorkflowProcessor] Необработанная ошибка при анализе книги {book_id}: {e}{chr(10)}{tb}")
        workflow_db_manager.update_book_stage_status_workflow(book_id, ANALYSIS_STAGE_NAME, f"error_unknown", error_message=f"Необработанная ошибка: {e}{chr(10)}{tb}")
        update_overall_workflow_book_status(book_id)
        return False

# --- New function for Section-level Translation ---
def process_section_translate(book_id: str, section_id: int):
    """
    Процессит перевод одной секции.
    """
    from epub_parser import extract_section_text
    import workflow_cache_manager
    import workflow_db_manager
    import workflow_translation_module

    TRANSLATION_PROMPT_EXT = ""  # Константа, можно будет подтянуть из конфига

    print(f"[WorkflowProcessor] Начат процесс перевода для секции {section_id} книги {book_id}")
    status = 'error'
    error_message = 'Unknown error'
    translated_text = None
    try:
        # 1. Обновляем статус секции на 'processing'
        workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'processing', error_message=None)

        # 2. Извлекаем текст секции
        section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
        if not section_info:
            error_message = f"Section {section_id} not found in workflow DB."
            print(f"[WorkflowProcessor] {error_message}")
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'error', error_message=error_message)
            return False
        section_epub_id = section_info['section_epub_id']
        book_info = workflow_db_manager.get_book_workflow(book_id)
        if not book_info:
            error_message = f"Book {book_id} not found in workflow DB."
            print(f"[WorkflowProcessor] {error_message}")
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'error', error_message=error_message)
            return False
        epub_path = book_info['filepath']
        section_text = extract_section_text(epub_path, section_epub_id)
        if not section_text or not section_text.strip():
            error_message = "Section text is empty."
            print(f"[WorkflowProcessor] {error_message}")
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'completed_empty', error_message=error_message)
            workflow_cache_manager.save_section_stage_result(book_id, section_id, 'translate', "")
            return True

        # 3. Загружаем глоссарий и рекомендации из анализа
        # (должны быть сохранены после этапа анализа)
        analysis_data = workflow_cache_manager.load_book_stage_result(book_id, 'analyze')
        dict_data = {'glossary_data': analysis_data} if analysis_data else None
        # 4. Получаем язык и модель
        target_language = book_info.get('target_language', 'russian')
        model_name = TRANSLATION_MODEL  # <-- теперь всегда используем хардкод

        # 5. Вызываем перевод
        print(f"[WorkflowProcessor] ВРЕМЕННО ЗАКОММЕНТИРОВАНО: Вызов translate_text для секции {section_id} ({model_name} -> {target_language})")
        # translated_text = workflow_translation_module.translate_text(
        #     text_to_translate=section_text,
        #     target_language=target_language,
        #     model_name=model_name,
        #     prompt_ext=TRANSLATION_PROMPT_EXT,
        #     operation_type='translate',
        #     dict_data=dict_data
        # )
        # ВРЕМЕННАЯ ЗАГЛУШКА для отладки анализа
        translated_text = f"[ЗАГЛУШКА ПЕРЕВОДА] Секция {section_id}: {section_text[:100]}..."
        print(f"[WorkflowProcessor] Результат translate_text: {translated_text[:100] if translated_text else 'None'}... (длина {len(translated_text) if translated_text is not None else 'None'})")

        # 6. Сохраняем результат и обновляем статус
        section_text_length = len(section_text.strip()) if section_text else 0
        if section_text_length < 100:
            # Для очень коротких секций: если перевод не пустой — completed, если пустой — completed_empty
            if translated_text is not None and translated_text.strip() != "":
                if workflow_cache_manager.save_section_stage_result(book_id, section_id, 'translate', translated_text):
                    status = 'completed'
                    error_message = None
                    print(f"[WorkflowProcessor] Короткая секция (<100 симв). Перевод принят как валидный. Статус: completed.")
                else:
                    status = 'error_caching'
                    error_message = "Ошибка сохранения результата перевода в кеш."
                    print(f"[WorkflowProcessor] ОШИБКА сохранения результата перевода в кеш для {book_id}/{section_id}: {error_message}")
            else:
                status = 'completed_empty'
                error_message = "Короткая секция (<100 симв). Перевод пустой."
                workflow_cache_manager.save_section_stage_result(book_id, section_id, 'translate', "")
                print(f"[WorkflowProcessor] Короткая секция (<100 симв). Перевод пустой. Статус: completed_empty.")
        else:
            if translated_text is not None and translated_text.strip() != "" and translated_text not in [workflow_translation_module.EMPTY_RESPONSE_ERROR, workflow_translation_module.CONTEXT_LIMIT_ERROR, 'TRUNCATED_RESPONSE_ERROR']:
                if workflow_cache_manager.save_section_stage_result(book_id, section_id, 'translate', translated_text):
                    status = 'completed'
                    error_message = None
                    print(f"[WorkflowProcessor] Перевод для секции ID {section_id} книги {book_id} завершён со статусом: {status}.")
                else:
                    status = 'error_caching'
                    error_message = "Ошибка сохранения результата перевода в кеш."
                    print(f"[WorkflowProcessor] ОШИБКА сохранения результата перевода в кеш для {book_id}/{section_id}: {error_message}")
            else:
                status = 'error'
                if translated_text == workflow_translation_module.EMPTY_RESPONSE_ERROR:
                    error_message = "API вернул EMPTY_RESPONSE_ERROR."
                    print(f"[WorkflowProcessor] Warning: Model returned EMPTY_RESPONSE_ERROR.")
                elif translated_text == workflow_translation_module.CONTEXT_LIMIT_ERROR:
                    error_message = "API вернул CONTEXT_LIMIT_ERROR."
                    print(f"[WorkflowProcessor] Error: Model returned CONTEXT_LIMIT_ERROR.")
                elif translated_text == 'TRUNCATED_RESPONSE_ERROR':
                    error_message = "API вернул TRUNCATED_RESPONSE_ERROR (перевод обрезан)."
                    print(f"[WorkflowProcessor] Error: Model returned TRUNCATED_RESPONSE_ERROR.")
                else:
                    error_message = "Model returned empty result (None, empty string или неизвестная ошибка)."
                    print(f"[WorkflowProcessor] Warning: Model returned empty result или неизвестную ошибку.")
                workflow_cache_manager.save_section_stage_result(book_id, section_id, 'translate', "")

        workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', status, error_message=error_message)

        # --- ВМЕСТО копирования статуса из одной секции ---
        recalculate_book_stage_status(book_id, 'translate')
        update_overall_workflow_book_status(book_id)
        # --- КОНЕЦ ДОБАВЛЕНИЯ ---

        return status in ['completed', 'completed_empty', 'cached']
    except Exception as e:
        error_message = f"Exception during translation: {e}"
        print(f"[WorkflowProcessor] ОШИБКА при обработке перевода секции {section_id}: {e}")
        traceback.print_exc()
        try:
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, 'translate', 'error', error_message=error_message)
        except Exception as db_err:
            print(f"[WorkflowProcessor] ОШИБКА при попытке записать статус ошибки для секции {section_id}: {db_err}")
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

# --- New function to recalculate book stage status ---
def recalculate_book_stage_status(book_id, stage_name):
    """
    Пересчитывает статус этапа книги по всем секциям на этом этапе.
    Если этап не per-section (например, 'analyze', 'epub_creation'), статус не пересчитывается.
    """
    # --- Проверка: если этап не per-section, не трогаем статус ---
    per_section_stages = ['summarize', 'translate']
    if stage_name not in per_section_stages:
        print(f"[WorkflowProcessor] recalculate_book_stage_status: этап '{stage_name}' не per-section, статус не пересчитывается.")
        return
    # --- Дальше обычная логика для per-section этапов ---
    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
    statuses = [s.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending') for s in sections]
    if not statuses:
        status = 'pending'
    elif all(s == 'pending' for s in statuses):
        status = 'pending'
    elif any(s in ['processing', 'queued'] for s in statuses):
        status = 'processing'
    elif any(isinstance(s, str) and s.startswith('error') for s in statuses):
        status = 'completed_with_errors'
    elif all(s in ['completed', 'completed_empty', 'skipped'] for s in statuses):
        status = 'completed'
    else:
        status = 'processing'
    workflow_db_manager.update_book_stage_status_workflow(book_id, stage_name, status)
    print(f"[WorkflowProcessor] recalculate_book_stage_status: book_id={book_id}, stage={stage_name}, status={status}")

# --- END OF FILE workflow_processor.py ---
