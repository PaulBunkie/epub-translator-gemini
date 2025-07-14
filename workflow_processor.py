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
from config import UPLOADS_DIR
import sys
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import threading

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

# --- Резервные модели для автоматического переключения при ошибках ---
# УДАЛЕНО: Теперь используется workflow_model_config.py

def clean_toc_title(title):
    """
    Очищает заголовок от HTML и Markdown разметки.
    Сохраняет номера глав и пунктуацию.
    """
    if not title:
        return title
    
    # Убираем HTML теги
    title = re.sub(r'<[^>]+>', '', title)
    
    # Убираем Markdown разметку
    title = re.sub(r'\*\*(.*?)\*\*', r'\1', title)  # **bold**
    title = re.sub(r'\*(.*?)\*', r'\1', title)      # *italic*
    title = re.sub(r'^#+\s*', '', title)            # # heading
    title = re.sub(r'`([^`]+)`', r'\1', title)      # `code`
    
    # Убираем лишние пробелы в начале и конце
    title = title.strip()
    
    # Убираем множественные пробелы внутри
    title = re.sub(r'\s+', ' ', title)
    
    return title

def translate_toc_titles_workflow(titles: List[str], target_language: str) -> List[str]:
    """
    Переводит заголовки оглавления с retry логикой для workflow.
    Использует отдельный operation_type 'translate_toc' с собственными моделями и промптами.
    """
    if not titles:
        return []
    
    # Очищаем заголовки
    cleaned_titles = [clean_toc_title(title) for title in titles]
    
    # Объединяем в одну строку с разделителем
    titles_text = "|||".join(cleaned_titles)
    
    print(f"[WorkflowProcessor] Перевод TOC: используем operation_type 'translate_toc'")
    
    try:
        # Вызываем перевод с operation_type='translate_toc' (модель будет взята из конфига)
        result = workflow_translation_module.translate_text(
            text_to_translate=titles_text,
            target_language=target_language,
            model_name=None,  # Будет взята из конфига
            prompt_ext=None,
            operation_type='translate_toc',
            book_id=None,  # TOC не привязан к конкретной секции
            section_id=None
        )
        
        if result and result != 'CONTEXT_LIMIT_ERROR':
            # Парсим результат
            translated_titles = result.split("|||")
            
            # Проверяем количество строк
            if len(translated_titles) == len(titles):
                print(f"[WorkflowProcessor] TOC переведен успешно: {len(translated_titles)} заголовков")
                return translated_titles
            else:
                print(f"[WorkflowProcessor] ОШИБКА TOC: ожидалось {len(titles)}, получено {len(translated_titles)} строк")
        else:
            print(f"[WorkflowProcessor] ОШИБКА TOC: пустой ответ или CONTEXT_LIMIT_ERROR")
            
    except Exception as e:
        print(f"[WorkflowProcessor] ОШИБКА TOC: {e}")
    
    print("[WorkflowProcessor] Не удалось перевести TOC")
    return []

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
                    prompt_ext=prompt_ext,
                    book_id=book_id,
                    section_id=section_id
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
def start_book_workflow(book_id: str, app_instance: Flask):
    """
    Запускает полный рабочий процесс для книги, начиная с указанного этапа (или с самого начала).
    После завершения каждого этапа автоматически переходит к следующему.
    """
    print(f"[WorkflowProcessor] Запуск рабочего процесса для книги ID: {book_id}")

    # --- УМНАЯ ЛОГИКА: Определяем первый незавершенный этап и сбрасываем его и все последующие ---
    stages = workflow_db_manager.get_all_stages_ordered_workflow()
    print(f"[WorkflowProcessor] Определены этапы рабочего процесса: {[stage['stage_name'] for stage in stages]}")
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if not book_info:
        print(f"[WorkflowProcessor] Книга {book_id} не найдена в Workflow DB. Прерывание.")
        return False
    
    # Определяем первый незавершенный этап КНИГИ
    first_incomplete_stage = None
    stages_to_reset = []
    
    for stage in stages:
        stage_name = stage['stage_name']
        
        # Проверяем статус этапа на уровне КНИГИ
        book_stage_statuses = book_info.get('book_stage_statuses', {})
        current_stage_status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
        
        print(f"[WorkflowProcessor] Проверяем этап {stage_name}: статус книги = {current_stage_status}")
        
        if current_stage_status not in ['completed', 'completed_empty', 'skipped']:
            first_incomplete_stage = stage_name
            print(f"[WorkflowProcessor] Найден первый незавершенный этап книги: {stage_name}")
            break
    
    # Определяем первый незавершенный этап (с которого нужно начать перезапуск)
    if first_incomplete_stage:
        print(f"[WorkflowProcessor] Workflow упал на этапе: {first_incomplete_stage}. Начинаем перезапуск с этого этапа.")
    else:
        print(f"[WorkflowProcessor] Все этапы завершены. Workflow завершен успешно.")
        return True
    
    # Определяем этапы для сброса (первый незавершенный и все последующие)
    if first_incomplete_stage:
        reset_started = False
        for stage in stages:
            stage_name = stage['stage_name']
            if stage_name == first_incomplete_stage:
                reset_started = True
            
            if reset_started:
                # Проверяем, нужно ли сбрасывать этот этап
                is_per_section_stage = stage.get('is_per_section', False)
                
                if is_per_section_stage:
                    # Для per-section этапов проверяем все секции
                    sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
                    all_sections_completed = True
                    for section in sections:
                        section_id = section['section_id']
                        section_stage_status = section.get('stage_statuses', {}).get(stage_name, {}).get('status', 'pending')
                        if section_stage_status not in ['completed', 'completed_empty']:
                            all_sections_completed = False
                            break
                    
                    if not all_sections_completed:
                        stages_to_reset.append(stage_name)
                        print(f"[WorkflowProcessor] Этап {stage_name} будет сброшен (не все секции завершены)")
                    else:
                        print(f"[WorkflowProcessor] Этап {stage_name} пропущен (все секции завершены)")
                else:
                    # Для book-level этапов проверяем статус книги
                    book_stage_statuses = book_info.get('book_stage_statuses', {})
                    current_stage_status = book_stage_statuses.get(stage_name, {}).get('status', 'pending')
                    
                    if current_stage_status not in ['completed', 'completed_empty', 'skipped']:
                        stages_to_reset.append(stage_name)
                        print(f"[WorkflowProcessor] Этап {stage_name} будет сброшен (статус: {current_stage_status})")
                    else:
                        print(f"[WorkflowProcessor] Этап {stage_name} пропущен (статус: {current_stage_status})")
        
        print(f"[WorkflowProcessor] Сбрасываем этапы: {stages_to_reset}")
        
        # Сбрасываем определенные этапы
        for stage_name in stages_to_reset:
            is_per_section_stage = next((s.get('is_per_section', False) for s in stages if s['stage_name'] == stage_name), False)
            
            if is_per_section_stage:
                # Сброс per-section этапа
                sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
                for section in sections:
                    section_id = section['section_id']
                    workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, stage_name, 'pending', model_name=None, error_message=None)
                    import workflow_cache_manager
                    workflow_cache_manager.delete_section_stage_result(book_id, section_id, stage_name)
                # Пересчитываем статус этапа на уровне книги после сброса секций
                recalculate_book_stage_status(book_id, stage_name)
                print(f"[WorkflowProcessor] Сброшен per-section этап: {stage_name}")
            else:
                # Сброс book-level этапа
                workflow_db_manager.update_book_stage_status_workflow(book_id, stage_name, 'pending', model_name=None, error_message=None)
                import workflow_cache_manager
                workflow_cache_manager.delete_book_stage_result(book_id, stage_name)
                print(f"[WorkflowProcessor] Сброшен book-level этап: {stage_name}")
        
        # Обновляем book_info после сброса
        book_info = workflow_db_manager.get_book_workflow(book_id)
    
    # Определяем с какого этапа начинать
    start_index = 0
    if first_incomplete_stage:
        for i, stage in enumerate(stages):
            if stage['stage_name'] == first_incomplete_stage:
                start_index = i
                break
    
    # --- ВАЖНО: Обновляем book_info еще раз перед циклом выполнения ---
    # Это нужно, чтобы получить актуальные статусы после сброса
    book_info = workflow_db_manager.get_book_workflow(book_id)
    
    # Последовательно обрабатываем этапы
    for stage in stages[start_index:]:
        stage_name = stage['stage_name']
        is_per_section_stage = stage.get('is_per_section', False)
        print(f"[WorkflowProcessor] Обработка этапа '{stage_name}' (per-section: {is_per_section_stage}) для книги ID {book_id}.")
        # Проверяем статус этапа (обновляем book_info для получения актуальных данных)
        book_info = workflow_db_manager.get_book_workflow(book_id)
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
                # Этап reduce_text больше не нужен - логика сокращения встроена в analyze_with_summarization
                workflow_db_manager.update_book_stage_status_workflow(book_id, 'reduce_text', 'passed', error_message='Этап упразднен - сокращение встроено в анализ')
                print(f"[WorkflowProcessor] Этап 'reduce_text' помечен как 'passed' - логика сокращения встроена в analyze_with_summarization")
            # Можно добавить другие этапы по аналогии
            
            # Проверяем результат книжного этапа
            if result is False:
                print(f"[WorkflowProcessor] Критическая ошибка на книжном этапе '{stage_name}'. Останавливаем workflow.")
                return False
        # После завершения этапа обновляем book_info для получения актуальных статусов
        book_info = workflow_db_manager.get_book_workflow(book_id)
        # --- ВЫЗЫВАЕМ обновление статуса книги ---
        update_overall_workflow_book_status(book_id)
    
    # --- ФИНАЛЬНОЕ ОБНОВЛЕНИЕ СТАТУСА КНИГИ ---
    # После завершения всех этапов проверяем, что все этапы завершены успешно
    book_info = workflow_db_manager.get_book_workflow(book_id)
    if book_info:
        book_stage_statuses = book_info.get('book_stage_statuses', {})
        all_completed = True
        for stage_name, stage_data in book_stage_statuses.items():
            status = stage_data.get('status', 'pending')
            if status not in ['completed', 'completed_empty', 'skipped']:
                all_completed = False
                break
        
        if all_completed:
            workflow_db_manager.update_book_workflow_status(book_id, 'completed')
            print(f"[WorkflowProcessor] Все этапы завершены успешно. Статус книги {book_id} обновлен на 'completed'.")
            
            # Отправляем уведомление в Telegram
            send_telegram_notification(book_id, 'completed')
        else:
            print(f"[WorkflowProcessor] Не все этапы завершены. Статус книги {book_id} остается текущим.")
    
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
        print(f"[WorkflowProcessor] Вызов translate_text для секции {section_id} ({model_name} -> {target_language})")
        translated_text = workflow_translation_module.translate_text(
            text_to_translate=section_text,
            target_language=target_language,
            model_name=model_name,
            prompt_ext=TRANSLATION_PROMPT_EXT,
            operation_type='translate',
            dict_data=dict_data,
            book_id=book_id,
            section_id=section_id
        )
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
    Собирает переведенные секции из кэша workflow и создает новый EPUB файл.
    """
    print(f"[WorkflowProcessor] Начат процесс создания EPUB для книги {book_id}")
    
    status_to_set = 'error'
    error_message_to_set = 'Unknown error'

    try:
        # Устанавливаем статус 'processing' при начале этапа
        workflow_db_manager.update_book_stage_status_workflow(
            book_id,
            'epub_creation',
            'processing',
            error_message=None
        )

        # 1. Получаем информацию о книге
        book_info = workflow_db_manager.get_book_workflow(book_id)
        if not book_info:
            raise Exception(f"Book {book_id} not found in workflow DB")

        # 2. Получаем все секции книги
        sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
        if not sections:
            raise Exception(f"No sections found for book {book_id}")

        # 3. Получаем переведенные секции и TOC
        target_language = book_info.get('target_language', 'russian')
        translated_sections = []
        translated_toc_titles = {}
        
        # Получаем переведенные заголовки TOC
        toc_data = book_info.get('toc', [])
        if toc_data:
            toc_titles_for_translation = [item['title'] for item in toc_data if item.get('title')]
            if toc_titles_for_translation:
                print(f"[WorkflowProcessor] Перевод {len(toc_titles_for_translation)} заголовков TOC...")
                
                # Используем новую функцию с правильными моделями и retry логикой
                translated_titles = translate_toc_titles_workflow(toc_titles_for_translation, target_language)
                
                if translated_titles and len(translated_titles) == len(toc_titles_for_translation):
                    for i, item in enumerate(toc_data):
                        if item.get('title') and item.get('id'):
                            translated_toc_titles[item['id']] = translated_titles[i].strip() if translated_titles[i] else None
                    print(f"[WorkflowProcessor] TOC переведен: {len(translated_toc_titles)} заголовков")
                else:
                    print(f"[WorkflowProcessor] ОШИБКА: Не удалось перевести оглавление или не совпало количество заголовков")
        
        # Получаем переведенные секции
        for section in sections:
            section_id = section['section_id']
            translated_text = workflow_cache_manager.load_section_stage_result(
                book_id, section_id, 'translate'
            )
            
            if translated_text is None or not translated_text.strip():
                print(f"[WorkflowProcessor] Предупреждение: Секция {section_id} не переведена или пуста")
                continue
                
            translated_sections.append({
                'section_id': section_id,
                'section_epub_id': section['section_epub_id'],
                'translated_text': translated_text
            })

        if not translated_sections:
            raise Exception(f"No translated sections found for book {book_id}")

        # 4. Создаем EPUB файл используя модифицированную функцию для workflow
        from epub_creator import create_translated_epub
        
        # Подготавливаем book_info в формате, ожидаемом create_translated_epub
        # create_translated_epub ожидает section_ids_list и sections как словарь
        section_ids_list = [section['section_epub_id'] for section in translated_sections]
        sections_dict = {}
        
        for section in translated_sections:
            section_id = section['section_epub_id']
            sections_dict[section_id] = {
                'status': 'translated',
                'translated_text': section['translated_text']
            }
        
        epub_book_info = {
            'filepath': book_info.get('filepath'),  # create_translated_epub ожидает 'filepath'
            'filename': book_info.get('filename'),
            'book_id': book_info.get('book_id'),
            'target_language': target_language,
            'section_ids_list': section_ids_list,  # Список ID секций в порядке spine
            'sections': sections_dict,  # Словарь с данными секций
            'toc': []
        }
        
        # Отладочная информация
        print(f"[WorkflowProcessor] DEBUG: epub_book_info keys: {list(epub_book_info.keys())}")
        print(f"[WorkflowProcessor] DEBUG: filepath = {epub_book_info['filepath']}")
        print(f"[WorkflowProcessor] DEBUG: filename = {epub_book_info['filename']}")
        print(f"[WorkflowProcessor] DEBUG: section_ids_list count = {len(epub_book_info['section_ids_list'])}")
        print(f"[WorkflowProcessor] DEBUG: sections dict count = {len(epub_book_info['sections'])}")
        print(f"[WorkflowProcessor] DEBUG: original book_info keys: {list(book_info.keys())}")
        print(f"[WorkflowProcessor] DEBUG: book_info filepath = {book_info.get('filepath')}")
        
        # Добавляем переведённые заголовки TOC
        for item in toc_data:
            toc_item = item.copy()
            if item.get('id') in translated_toc_titles:
                toc_item['translated_title'] = translated_toc_titles[item['id']]
            epub_book_info['toc'].append(toc_item)
        
        # Создаем модифицированную функцию для workflow
        def create_workflow_epub(book_info, target_language):
            """Модифицированная версия create_translated_epub для workflow"""
            from ebooklib import epub
            import ebooklib
            import os
            import traceback
            import html
            import re
            import unicodedata
            import tempfile
            from collections import defaultdict
            
            # Регулярные выражения (копируем из epub_creator.py)
            INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
            BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
            ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
            SUPERSCRIPT_MARKER_RE = re.compile(r"([\¹\²\³\⁰\⁴\⁵\⁶\⁷\⁸\⁹]+)")
            NOTE_LINE_START_RE = re.compile(r"^\s*([\¹\²\³\⁰\⁴\⁵\⁶\⁷\⁸\⁹]+)\s*(.*)", re.UNICODE)
            
            def get_int_from_superscript(marker_str):
                """Преобразует строку надстрочных цифр в целое число."""
                SUPerscript_INT_MAP = {'¹': '1', '²': '2', '³': '3', '⁰': '0', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
                if not marker_str: return -1
                num_str = "".join(SUPerscript_INT_MAP.get(c, '') for c in marker_str)
                try: return int(num_str) if num_str else -1
                except ValueError: return -1
            
            print(f"Запуск создания EPUB для: {book_info.get('filename', 'N/A')}, язык: {target_language}")
            
            original_filepath = book_info.get("filepath")
            section_ids = book_info.get("section_ids_list", [])
            toc_data = book_info.get("toc", [])
            sections_data_map = book_info.get("sections", {})
            
            book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]
            epub_id_str = book_info.get('book_id', 'unknown-book-id')
            lang_code = target_language[:2] if target_language else "ru"
            
            if not original_filepath or not os.path.exists(original_filepath) or not section_ids:
                print("[ERROR epub_creator] Отсутствует путь к файлу, файл не найден или нет ID секций.")
                return None
            
            # Чтение оригинала для копирования ресурсов
            try:
                original_book = epub.read_epub(original_filepath)
                print(f"  Оригинальная книга прочитана: {original_filepath}")
            except Exception as e:
                print(f"  ОШИБКА чтения оригинальной книги: {e}")
                traceback.print_exc()
                return None
            
            # Создание новой книги
            book = epub.EpubBook()
            book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}")
            book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
            book.set_language(lang_code)
            book.add_author("EPUB Translator Tool")
            book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
            
            # Копирование ресурсов
            copied_items_ids = set()
            items_to_copy = []
            print("  Копирование ресурсов...")
            for item in original_book.get_items():
                is_cover = item.get_id() == 'cover' or 'cover' in item.get_name().lower()
                if item.get_type() != ebooklib.ITEM_DOCUMENT or is_cover:
                    item_id = item.get_id()
                    if item_id not in copied_items_ids:
                        items_to_copy.append(item)
                        copied_items_ids.add(item_id)
            
            for item in items_to_copy:
                book.add_item(item)
            print(f"  Скопировано {len(items_to_copy)} ресурсов/служебных файлов.")

            # --- ЯВНО УСТАНАВЛИВАЕМ ОБЛОЖКУ ---
            cover_item = None
            for item in original_book.get_items_of_type(ebooklib.ITEM_IMAGE):
                if item.get_id() == 'cover' or 'cover' in item.get_name().lower():
                    cover_item = item
                    break
            if cover_item:
                book.set_cover(cover_item.file_name, cover_item.get_content())
                print(f'  Обложка установлена: {cover_item.file_name}')
            else:
                print('  [WARN] Обложка не найдена в оригинале, не будет установлена явно.')

            # Обработка и добавление переведенных глав
            chapters = []
            chapter_titles_map = {}
            default_title_prefix = "Section"
            if toc_data:
                for item in toc_data:
                    sec_id = item.get('id')
                    title = item.get('translated_title') or item.get('title')
                    if sec_id and title:
                        chapter_titles_map[sec_id] = title
                        if default_title_prefix == "Section" and any('а' <= c <= 'я' for c in title.lower()):
                            default_title_prefix = "Раздел"
                if not chapter_titles_map:
                    print("  [WARN] Не удалось извлечь заголовки из TOC.")
            else:
                print("  [WARN] Нет данных TOC.")
            
            print(f"  Обработка {len(section_ids)} секций книги...")
            for i, section_id in enumerate(section_ids):
                chapter_index = i + 1
                
                # Ищем название главы в TOC по section_id
                chapter_title = None
                if toc_data:
                    for item in toc_data:
                        if item.get('id') == section_id:
                            chapter_title = item.get('translated_title') or item.get('title')
                            break
                
                # Если не нашли в TOC, используем fallback
                if not chapter_title:
                    chapter_title = f"{default_title_prefix} {chapter_index}"
                
                chapter_title_escaped = html.escape(chapter_title)
                # Убираем заголовок из содержимого - он будет только в метаданных главы
                final_html_body_content = ""
                
                section_data = sections_data_map.get(section_id)
                section_status = section_data.get("status", "unknown") if section_data else "unknown"
                error_message = section_data.get("error_message") if section_data else None
                
                # Получаем перевод из sections_data_map вместо кэша
                translated_text = section_data.get("translated_text") if section_data else None
                
                if translated_text is not None:
                    # Обработка параграфов и сносок (упрощенная версия)
                    note_definitions = defaultdict(list)
                    note_targets_found = set()
                    note_paragraph_indices = set()
                    reference_markers_data = []
                    original_paragraphs = translated_text.split('\n\n')
                    
                    # Этап 1: Сбор информации о сносках
                    for para_idx, para_text_raw in enumerate(original_paragraphs):
                        para_strip_orig = para_text_raw.strip()
                        if not para_strip_orig:
                            continue
                        is_definition_para = False
                        lines = para_strip_orig.split('\n')
                        for line in lines:
                            match_line = NOTE_LINE_START_RE.match(line.strip())
                            if match_line:
                                is_definition_para = True
                                marker = match_line.group(1)
                                note_text = match_line.group(2).strip()
                                note_num = get_int_from_superscript(marker)
                                if note_num > 0:
                                    note_definitions[note_num].append(note_text)
                                    note_targets_found.add(note_num)
                        if is_definition_para:
                            note_paragraph_indices.add(para_idx)
                        # Ищем ссылки-маркеры
                        for match in SUPERSCRIPT_MARKER_RE.finditer(para_strip_orig):
                            marker = match.group(1)
                            note_num = get_int_from_superscript(marker)
                            if note_num > 0:
                                reference_markers_data.append((para_idx, match, note_num))
                    
                    # Этап 2: Генерация HTML
                    final_content_blocks = []
                    processed_markers_count = 0
                    reference_occurrence_counters = defaultdict(int)
                    definition_occurrence_counters = defaultdict(int)
                    
                    for para_idx, para_original_raw in enumerate(original_paragraphs):
                        para_strip = para_original_raw.strip()
                        if not para_strip:
                            if para_original_raw:
                                final_content_blocks.append("<p> </p>")
                            continue
                        
                        # Проверяем, содержит ли параграф определения сносок
                        is_footnote_para = False
                        lines_for_check = para_strip.split('\n')
                        for line_check in lines_for_check:
                            if NOTE_LINE_START_RE.match(line_check.strip()):
                                is_footnote_para = True
                                break
                        
                        if is_footnote_para:
                            # Обработка параграфа-сноски
                            footnote_lines_html = []
                            lines = para_strip.split('\n')
                            for line in lines:
                                line_strip = line.strip()
                                if not line_strip:
                                    continue
                                match_line = NOTE_LINE_START_RE.match(line_strip)
                                if match_line:
                                    marker = match_line.group(1)
                                    note_text = match_line.group(2).strip()
                                    note_num = get_int_from_superscript(marker)
                                    if note_num > 0:
                                        definition_occurrence_counters[note_num] += 1
                                        occ = definition_occurrence_counters[note_num]
                                        note_anchor_id = f"note_{section_id}_{note_num}_{occ}"
                                        ref_id = f"ref_{section_id}_{note_num}_{occ}"
                                        backlink_html = f' <a class="footnote-backlink" href="#{ref_id}" title="Вернуться к тексту">↩</a>'
                                        note_text_cleaned = INVALID_XML_CHARS_RE.sub('', note_text)
                                        note_text_md = BOLD_MD_RE.sub(r'<strong>\1</strong>', note_text_cleaned)
                                        note_text_md = ITALIC_MD_RE.sub(r'<em>\1</em>', note_text_md)
                                        footnote_lines_html.append(f'<p class="footnote-definition" id="{note_anchor_id}">{marker} {note_text_md}{backlink_html}</p>')
                                    else:
                                        footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')
                                else:
                                    footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')
                            if footnote_lines_html:
                                final_content_blocks.append(f'<div class="footnote-block">\n{chr(10).join(footnote_lines_html)}\n</div>')
                        else:
                            # Обработка обычного параграфа
                            text_normalized = unicodedata.normalize('NFC', para_strip)
                            text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                            text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                            text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)
                            
                            current_para_html = text_with_md_html
                            offset = 0
                            markers_in_para = sorted(list(SUPERSCRIPT_MARKER_RE.finditer(text_with_md_html)), key=lambda m: m.start())
                            
                            for match in markers_in_para:
                                marker = match.group(1)
                                note_num = get_int_from_superscript(marker)
                                if note_num > 0 and note_num in note_targets_found:
                                    reference_occurrence_counters[note_num] += 1
                                    occ = reference_occurrence_counters[note_num]
                                    start, end = match.start() + offset, match.end() + offset
                                    note_anchor_id = f"note_{section_id}_{note_num}_{occ}"
                                    ref_id = f"ref_{section_id}_{note_num}_{occ}"
                                    replacement = f'<sup class="footnote-ref"><a id="{ref_id}" href="#{note_anchor_id}" title="См. примечание {note_num}">{marker}</a></sup>'
                                    current_para_html = current_para_html[:start] + replacement + current_para_html[end:]
                                    offset += len(replacement) - (end - start)
                                    processed_markers_count += 1
                            processed_html = current_para_html.replace('\n', '<br/>')
                            final_para_html = f"<p>{processed_html}</p>"
                            final_content_blocks.append(final_para_html)
                    
                    # После всех циклов для секции
                    if processed_markers_count > 0:
                        print(f"      Заменено маркеров ссылками: {processed_markers_count} для {section_id}")
                    # Добавляем собранные блоки
                    final_html_body_content += "\n".join(final_content_blocks)
                    # Если после всего контент (кроме заголовка) остался пустым, добавим пустой параграф
                    if not final_content_blocks and translated_text.strip() == "":
                        final_html_body_content += "<p> </p>"
                
                elif section_status.startswith("error_"):
                    error_display_text = error_message if error_message else section_status
                    final_html_body_content += f"\n<p><i>[Ошибка перевода: {html.escape(error_display_text)}]</i></p>"
                else:
                    final_html_body_content += f"\n<p><i>[Перевод недоступен (статус: {html.escape(section_status)})]</i></p>"
                
                # Определяем имя файла главы
                original_item = original_book.get_item_with_id(section_id)
                if original_item and original_item.get_type() == ebooklib.ITEM_DOCUMENT:
                    chapter_filename_to_use = original_item.file_name
                else:
                    fallback_fname = f"chapter_{chapter_index}.xhtml"
                    print(f"  [WARN] Не найден оригинальный документ для section_id '{section_id}'. Используем fallback: {fallback_fname}")
                    chapter_filename_to_use = fallback_fname
                
                # Создаем и добавляем главу
                epub_chapter = epub.EpubHtml(
                    title=chapter_title,
                    file_name=chapter_filename_to_use,
                    lang=lang_code,
                    uid=section_id
                )
                try:
                    basic_css = "<style>body{line-height:1.5; margin: 1em;} h1{margin-top:0; border-bottom: 1px solid #eee; padding-bottom: 0.2em; margin-bottom: 1em;} p{margin: 0.5em 0; text-indent: 0;} .footnote-block{font-size:0.9em; margin-top: 2em; border-top: 1px solid #eee; padding-top: 0.5em;} .footnote-definition{margin: 0.2em 0;} .footnote-ref a {text-decoration: none; vertical-align: super; font-size: 0.8em;}</style>"
                    full_content = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang_code}" xml:lang="{lang_code}"><head><meta charset="utf-8"/><title>{chapter_title_escaped}</title>{basic_css}</head><body>{final_html_body_content}</body></html>'
                    epub_chapter.content = full_content.encode('utf-8', 'xmlcharrefreplace')
                except Exception as set_content_err:
                    print(f"  !!! ОШИБКА set_content для '{section_id}': {set_content_err}")
                    epub_chapter.content = f"<html><body><h1>Error</h1><p>Failed to set content for section {html.escape(section_id)}.</p></body></html>".encode('utf-8')
                
                book.add_item(epub_chapter)
                chapters.append(epub_chapter)
            
            print("  Генерация TOC и Spine...")
            # Создание TOC и Spine
            book_toc = []
            processed_toc_items = 0
            if toc_data:
                href_to_chapter_map = {ch.file_name: ch for ch in chapters}
                for item in toc_data:
                    item_href = item.get('href')
                    item_title = item.get('translated_title') or item.get('title')
                    if item_href and item_title:
                        clean_href = item_href.split('#')[0]
                        target_chapter = href_to_chapter_map.get(clean_href)
                        if target_chapter:
                            link_target = target_chapter.file_name + (f"#{item_href.split('#')[1]}" if '#' in item_href else '')
                            toc_entry = epub.Link(link_target, item_title, uid=item.get('id', clean_href))
                            book_toc.append(toc_entry)
                            processed_toc_items += 1
            if processed_toc_items > 0:
                print(f"  TOC с {processed_toc_items} элементами подготовлен.")
                book.toc = tuple(book_toc)
            else:
                print("  [WARN] Не удалось создать TOC из данных, используем плоский список.")
                book.toc = tuple(chapters[:])
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())
            book.spine = ['nav'] + chapters
            print(f"  Spine установлен: {len(book.spine)} элементов.")
            
            # Запись файла во временный файл
            print(f"  Запись EPUB во временный файл...")
            epub_content_bytes = None
            temp_epub_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".epub", mode='wb') as temp_f:
                    temp_epub_path = temp_f.name
                epub.write_epub(temp_epub_path, book, {})
                print(f"    EPUB записан в {temp_epub_path}")
                with open(temp_epub_path, 'rb') as f_read:
                    epub_content_bytes = f_read.read()
                print(f"    EPUB прочитан ({len(epub_content_bytes)} байт).")
                return epub_content_bytes
            except Exception as e:
                print(f"  ОШИБКА записи/чтения EPUB: {e}")
                traceback.print_exc()
                return None
            finally:
                if temp_epub_path and os.path.exists(temp_epub_path):
                    try:
                        os.remove(temp_epub_path)
                    except OSError as os_err:
                        print(f"  ОШИБКА удаления temp file {temp_epub_path}: {os_err}")
        
        epub_bytes = create_workflow_epub(epub_book_info, target_language)
        
        if not epub_bytes:
            raise Exception("Failed to create EPUB file")

        # Сохраняем EPUB файл
        output_dir = UPLOADS_DIR / "translated"
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(book_info.get('filename', 'translated_book'))[0]
        output_filename = f"{base_name}_{target_language}.epub"
        epub_file_path = output_dir / output_filename
        with open(epub_file_path, 'wb') as f:
            f.write(epub_bytes)
        if not epub_file_path:
            raise Exception("Failed to create EPUB file")
        print(f"[WorkflowProcessor] EPUB успешно создан: {epub_file_path}")
        status_to_set = 'completed'
        error_message_to_set = None

    except Exception as e:
        status_to_set = 'error'
        error_message_to_set = f"Error during EPUB creation: {e}"
        print(f"[WorkflowProcessor] ОШИБКА при создании EPUB для книги {book_id}: {e}")
        traceback.print_exc()

    finally:
        # Обновляем статус этапа в БД
        try:
            book_exists_check = workflow_db_manager.get_book_workflow(book_id)
            if book_exists_check:
                workflow_db_manager.update_book_stage_status_workflow(
                    book_id,
                    'epub_creation',
                    status_to_set,
                    error_message=error_message_to_set
                )
                print(f"[WorkflowProcessor] Статус этапа 'epub_creation' для книги {book_id} обновлен на '{status_to_set}'.")
            else:
                print(f"[WorkflowProcessor] Книга {book_id} не найдена при попытке обновить статус этапа 'epub_creation'.")

        except Exception as db_err:
            print(f"[WorkflowProcessor] ОШИБКА при попытке записать статус этапа 'epub_creation' для книги {book_id}: {db_err}")
            traceback.print_exc()

    return status_to_set == 'completed'

# --- Function to create EPUB from workflow translated sections ---
# Удалена - теперь используется стандартная create_translated_epub из epub_creator.py

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

# --- КОНЕЦ ФУНКЦИЙ ДЛЯ РАБОТЫ С ТОКЕНАМИ ДОСТУПА ---

def send_telegram_notification(book_id: str, status: str = 'completed'):
    """Отправляет уведомление в Telegram когда перевод готов"""
    try:
        import workflow_db_manager
        from telegram_notifier import telegram_notifier
        
        # Получаем информацию о книге
        book_info = workflow_db_manager.get_book_workflow(book_id)
        if not book_info:
            print(f"[WorkflowProcessor] Не удалось получить информацию о книге {book_id} для уведомления")
            return False
        
        access_token = book_info.get('access_token')
        if not access_token:
            print(f"[WorkflowProcessor] У книги {book_id} нет токена доступа для уведомлений")
            return False
        
        # Получаем список пользователей Telegram
        telegram_users = workflow_db_manager.get_telegram_users_for_book(access_token)
        if not telegram_users:
            print(f"[WorkflowProcessor] Нет пользователей Telegram для уведомления о книге {book_id}")
            return False
        
        # Формируем сообщение
        filename = book_info.get('filename', 'Unknown')
        target_language = book_info.get('target_language', 'Unknown')
        download_url = f"http://localhost:5000/translate/{access_token}"
        
        message = f"""
✅ <b>Перевод готов!</b>

📚 <b>Книга:</b> {filename}
🌍 <b>Язык:</b> {target_language}

📥 <b>Скачать:</b> {download_url}

🔗 <b>Ваша ссылка:</b> {download_url}
        """.strip()
        
        # Отправляем уведомления всем подписчикам
        success_count = 0
        for user in telegram_users:
            user_id = user['user_id']
            if telegram_notifier.send_message_to_user(user_id, message):
                success_count += 1
                print(f"[WorkflowProcessor] Уведомление отправлено пользователю {user_id}")
            else:
                print(f"[WorkflowProcessor] Ошибка отправки уведомления пользователю {user_id}")
        
        print(f"[WorkflowProcessor] Уведомления отправлены {success_count}/{len(telegram_users)} пользователям")
        return success_count > 0
        
    except Exception as e:
        print(f"[WorkflowProcessor] Ошибка отправки Telegram уведомлений для книги {book_id}: {e}")
        return False

# --- END OF FILE workflow_processor.py ---
