# --- START OF FILE workflow_processor.py ---

import workflow_db_manager
import epub_parser
import translation_module
import os
import traceback
import workflow_cache_manager # TODO: Implement workflow_cache_manager DONE
import time
from flask import current_app

# Hardcoded model for summarization for now
SUMMARIZATION_MODEL = 'meta-llama/llama-4-scout:free'
SUMMARIZATION_STAGE_NAME = 'summarize'

# TODO: Определить шаблон промпта для суммаризации
# Пока используем простой шаблон
SUMMARIZATION_PROMPT_TEMPLATE = "Summarize the following text in Russian:\n\n{text}"

# --- Constants for Analysis Stage ---
ANALYSIS_MODEL = 'meta-llama/llama-4-scout:free' # Можно использовать ту же модель или другую
ANALYSIS_STAGE_NAME = 'analyze'

# TODO: Определить шаблон промпта для анализа. Он должен использовать результат суммаризации.
# Пример: Проанализируй ключевые сущности (персонажи, места, события) на основе следующего текста.
ANALYSIS_PROMPT_TEMPLATE = "Based on the following summarized text, identify and list the key entities (characters, places, events) mentioned. Provide brief descriptions for each. \n\nSummarized Text:\n{summarized_text}"

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

        # 4. Вызываем модель суммаризации с ретраями
        target_language = book_info['target_language']
        operation_type = SUMMARIZATION_STAGE_NAME
        model_name = SUMMARIZATION_MODEL
        prompt_ext = SUMMARIZATION_PROMPT_TEMPLATE.format(text=section_content)

        summarized_text = None
        status = 'error' # Default status in case of failure
        error_message = 'Unknown error'

        for attempt in range(MAX_RETRIES + 1):
            print(f"[WorkflowProcessor] Попытка {attempt + 1}/{MAX_RETRIES + 1} вызова модели для секции {section_id} (суммаризация)...")
            try:
                summarized_text = translation_module.translate_text(
                    text_to_translate=section_content,
                    target_language=target_language,
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

# --- New function for Analysis Stage ---
def process_section_analysis(book_id: str, section_id: int):
    """ Обрабатывает одну секцию на стадии анализа. """
    print(f"[WorkflowProcessor] Начат процесс анализа для секции {section_id} книги {book_id}")

    try:
        # 1. Получаем информацию о секции и книге из БД
        section_info = workflow_db_manager.get_section_by_id_workflow(book_id, section_id)
        if not section_info:
            print(f"[WorkflowProcessor] Ошибка: Секция с ID {section_id} не найдена в БД.")
            # TODO: Возможно, обновить статус книги на ошибку или пропустить секцию?
            return False

        book_info = workflow_db_manager.get_book_workflow(book_id)
        if not book_info:
             print(f"[WorkflowProcessor] Ошибка: Книга с ID {book_id} для секции {section_id} не найдена в БД.")
             workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, ANALYSIS_STAGE_NAME, 'error', error_message='Book not found')
             return False

        # 2. Обновляем статус секции в БД на 'processing' для стадии 'analyze'
        workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, ANALYSIS_STAGE_NAME, 'processing')

        # 3. Получаем результат предыдущей стадии (суммаризацию) из кэша
        summarized_text = workflow_cache_manager.load_section_stage_result(book_id, section_id, SUMMARIZATION_STAGE_NAME)

        if summarized_text is None or not summarized_text.strip():
             print(f"[WorkflowProcessor] Результат суммаризации для секции {section_id} книги {book_id} пуст или не найден. Пропускаем анализ и помечаем как completed_empty.")
             # Если нет результата суммаризации, стадия анализа не может быть выполнена
             with current_app.app_context():
                 workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, ANALYSIS_STAGE_NAME, 'completed_empty', error_message='Summarization result missing or empty')
             # Сохраняем пустой результат анализа в кэш
             if workflow_cache_manager.save_section_stage_result(book_id, section_id, ANALYSIS_STAGE_NAME, ""):
                 print(f"[WorkflowProcessor] Пустой результат анализа сохранен в кэш для секции {section_id}.")
             return True # Возвращаем True, так как это не ошибка, а ожидаемое состояние

        print(f"[WorkflowProcessor] Получен результат суммаризации для секции {section_id} длиной {len(summarized_text)} символов.")

        # 4. Подготавливаем промпт для анализа
        prompt_text = ANALYSIS_PROMPT_TEMPLATE.format(summarized_text=summarized_text)

        # 5. Вызываем модель для анализа с ретраями
        target_language = book_info.get('target_language', 'russian') # Анализ может быть на целевом языке или английском
        # TODO: Возможно, добавить опцию для выбора языка анализа

        analysis_result = None
        status = 'error' # Default status in case of failure
        error_message = 'Unknown error'

        for attempt in range(MAX_RETRIES + 1):
            print(f"[WorkflowProcessor] Попытка {attempt + 1}/{MAX_RETRIES + 1} вызова модели для секции {section_id} (анализ)...")
            try:
                 print(f"[WorkflowProcessor] Вызов translate_text для анализа {book_id}/{section_id} ({ANALYSIS_MODEL} -> {target_language})")
                 analysis_result = translation_module.translate_text(
                     text_to_translate=summarized_text, # Передаем суммаризированный текст
                     target_language=target_language,
                     model_name=ANALYSIS_MODEL,
                     prompt_ext=prompt_text, # Передаем наш промпт для анализа
                     operation_type=ANALYSIS_STAGE_NAME # Указываем тип операции
                 )
                 print(f"[WorkflowProcessor] Результат translate_text: {analysis_result[:100] if analysis_result else 'None'}... (длина {len(analysis_result) if analysis_result is not None else 'None'})")

                 if analysis_result is not None and analysis_result.strip() != "":
                      status = 'completed'
                      error_message = None
                      print(f"[WorkflowProcessor] Модель вернула непустой результат на попытке {attempt + 1}.")
                      break # Успех, выходим из цикла ретраев
                 elif analysis_result == translation_module.EMPTY_RESPONSE_ERROR:
                      error_message = "API вернул EMPTY_RESPONSE_ERROR."
                      print(f"[WorkflowProcessor] Предупреждение: Модель вернула EMPTY_RESPONSE_ERROR на попытке {attempt + 1}.")
                 elif analysis_result == translation_module.CONTEXT_LIMIT_ERROR:
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


        # 6. Обрабатываем финальный результат после ретраев
        if status == 'completed':
             # Результат получен, сохраняем в кэш
             if workflow_cache_manager.save_section_stage_result(book_id, section_id, ANALYSIS_STAGE_NAME, analysis_result):
                 print(f"[WorkflowProcessor] Результат анализа для {book_id}/{section_id} сохранен в кеш.")
                 # Статус уже 'completed'
             else:
                 status = 'error_caching'
                 error_message = "Ошибка сохранения в кеш."
                 print(f"[WorkflowProcessor] ОШИБКА сохранения в кеш для {book_id}/{section_id}: {error_message}")
                 workflow_cache_manager.delete_section_stage_cache(book_id, section_id, ANALYSIS_STAGE_NAME) # Удаляем потенциально некорректный кеш
                 analysis_result = None # Обнуляем результат
        elif status == 'error': # Если после ретраев статус остался 'error'
             print(f"[WorkflowProcessor] Финальная ошибка для секции {section_id} (анализ): {error_message}")
             # Статус уже 'error', error_message установлен

        elif analysis_result is None or analysis_result.strip() == "": # Финальный пустой результат после всех ретраев
             if DEBUG_ALLOW_EMPTY:
                 status = 'completed_empty'
                 error_message = 'Empty model response (allowed in DEBUG_ALLOW_EMPTY)'
                 print(f"[WorkflowProcessor] Модель вернула пустой результат после ретраев. DEBUG_ALLOW_EMPTY=True. Статус: completed_empty для секции {section_id}.")
                 # Сохраняем пустой файл в кэш
                 if workflow_cache_manager.save_section_stage_result(book_id, section_id, ANALYSIS_STAGE_NAME, ""):
                     print(f"[WorkflowProcessor] Пустой результат анализа сохранен в кэш для секции {section_id}.")
                 else:
                      status = 'error_caching'
                      error_message = "Ошибка сохранения пустого результата в кеш."
                      print(f"[WorkflowProcessor] ОШИБКА сохранения пустого результата в кеш для {book_id}/{section_id}: {error_message}")
             else:
                 status = 'error'
                 error_message = 'Empty model response after retries.'
                 print(f"[WorkflowProcessor] Модель вернула пустой результат после ретраев. DEBUG_ALLOW_EMPTY=False. Статус: error для секции {section_id}.")


    except Exception as e:
        status = 'error_unknown'
        error_message = f"Необработанная ошибка: {e}"
        print(f"[WorkflowProcessor] Необработанная ошибка при анализе {book_id}/{section_id}: {e}")
        traceback.print_exc() # Логируем полный трейсбэк
        workflow_cache_manager.delete_section_stage_cache(book_id, section_id, ANALYSIS_STAGE_NAME)
    finally:
        # 7. Обновляем статус секции в БД (конечный статус)
        with current_app.app_context():
            workflow_db_manager.update_section_stage_status_workflow(book_id, section_id, ANALYSIS_STAGE_NAME, status, error_message=error_message)
        print(f"[WorkflowProcessor] Анализ для секции {section_id} книги {book_id} завершен со статусом: {status}")
        # TODO: Здесь нужно проверить, завершены ли все секции для этой стадии и запустить следующую стадию (перевод) или обновить статус книги
        return status in ['completed', 'completed_empty'] # Возвращаем True, если успешно завершено (включая пустые)


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
            if current_status in ['processing', 'complete']:
                print(f"[WorkflowProcessor] Рабочий процесс для книги ID {book_id} уже в статусе '{current_status}'. Запуск отменен.")
                return False

            # Устанавливаем общий статус книги на 'processing'
            workflow_db_manager.update_book_workflow_status(book_id, 'processing')

            # Получаем все секции для книги
            sections = workflow_db_manager.get_sections_for_book_workflow(book_id)
            if not sections:
                print(f"[WorkflowProcessor] Предупреждение: Для книги ID {book_id} не найдено секций.")
                workflow_db_manager.update_book_workflow_status(book_id, 'complete', error_message='No sections found')
                return True
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # Идем по всем секциям и ставим в очередь (если нужно) для первого per-section этапа (summarize)
        first_per_section_stage = workflow_db_manager.get_stage_by_order_workflow(1) # Предполагаем, что 1 - это summarize и он per-section
        if not first_per_section_stage or not first_per_section_stage['is_per_section']:
             print(f"[WorkflowProcessor] ОШИБКА: Первый этап рабочего процесса не является per-section или не найден.")
             workflow_db_manager.update_book_workflow_status(book_id, 'error', 'Configuration error: First stage not per-section.')
             return False

        first_stage_name = first_per_section_stage['stage_name']

        sections_to_process = []
        for section_data in sections:
             section_id = section_data['section_id']
             # Получаем статус этой секции для текущего этапа
             stage_statuses = section_data.get('stage_statuses', {})
             current_stage_status = stage_statuses.get(first_stage_name, {}).get('status', 'pending')

             # Если статус не указывает на завершение или ошибку, ставим в очередь
             # Добавляем 'queued' в список статусов, которые не должны быть переставлены в очередь
             if current_stage_status not in ['complete', 'cached', 'error', 'skipped', 'processing', 'queued']:
                  print(f"[WorkflowProcessor] Ставим в очередь секцию ID {section_id} ({section_data.get('section_epub_id')}) для этапа '{first_stage_name}'.")
                  # Устанавливаем статус секции на 'queued'
                  workflow_db_manager.update_section_stage_status_workflow(
                       book_id,
                       section_id,
                       first_stage_name,
                       'queued',
                       error_message=None
                   )
                  sections_to_process.append(section_id)

        print(f"[WorkflowProcessor] Поставлено в очередь {len(sections_to_process)} секций для этапа '{first_stage_name}'.")

        # --- Запускаем обработку queued секций в пуле потоков --- (TODO: Implement real thread pool)
        # For now, still processing sequentially for debugging
        for section_id_to_process in sections_to_process:
             print(f"[WorkflowProcessor] Запуск обработки секции ID {section_id_to_process} для этапа '{first_stage_name}'...")
             # TODO: Replace with actual thread pool submission
             # Ensure section processing happens within the app context
             with current_app.app_context():
                 success = process_section_summarization(book_id, section_id_to_process) # Вызываем функцию обработки
                 if not success:
                     print(f"[WorkflowProcessor] Обработка секции {section_id_to_process} этапа '{first_stage_name}' завершилась неудачно.")
                     # If a section fails, we might want to update the book status earlier
                     # For now, we just log and continue

        # --- Проверка завершения стадии после обработки всех секций ---
        # This part should run after ALL sections are processed and their statuses are committed
        with current_app.app_context():
            # Re-fetch sections with their potentially updated statuses
            sections_after_processing = workflow_db_manager.get_sections_for_book_workflow(book_id)

            # Recalculate counts based on the latest statuses
            completed_sections_count = 0
            error_sections_count = 0
            all_sections_count = len(sections_after_processing)

            for section in sections_after_processing:
                # print(f"[WorkflowProcessor] Проверка статусов для секции ID {section.get('section_id')}: {section.get('stage_statuses')}") # Удален отладочный принт
                stage_statuses = section.get('stage_statuses', {})
                current_stage_status = stage_statuses.get(first_stage_name, {}).get('status') # Get status for the first stage

                # Считаем секции с финишным статусом для стадии
                # Исправлено: изменено 'complete' на 'completed'
                if current_stage_status in ['completed', 'cached', 'completed_empty']:
                    completed_sections_count += 1
                elif current_stage_status and (current_stage_status == 'error' or current_stage_status.startswith('error_')):
                    error_sections_count += 1

            # print(f"[WorkflowProcessor] Пересчитано статусов после обработки: {completed_sections_count} завершено, {error_sections_count} с ошибками из {all_sections_count} секций.") # Удален отладочный принт

            # Determine the stage status for the book level
            stage_status = 'processing' # Default status
            completion_count = completed_sections_count + error_sections_count

            if all_sections_count > 0 and completion_count == all_sections_count:
                # All sections have a final status (complete, cached, or error)
                if error_sections_count == 0:
                    stage_status = 'complete'
                    print(f"[WorkflowProcessor] Стадия '{first_stage_name}' для книги ID {book_id} завершена успешно (все секции завершены без ошибок).")
                else:
                    stage_status = 'error'
                    print(f"[WorkflowProcessor] Стадия '{first_stage_name}' для книги ID {book_id} завершена с ошибками в {error_sections_count} из {all_sections_count} секций.")

            # TODO: Implement actual stage transition logic here
            next_stage_name = workflow_db_manager.get_next_stage_name_workflow(first_stage_name)

            # Determine the final book workflow status
            final_book_workflow_status = 'processing' # Default remains processing if there's a next stage
            final_error_message = None

            if stage_status == 'complete':
                if next_stage_name:
                    # Stage completed successfully, there is a next stage - overall status remains processing
                     print(f"[WorkflowProcessor] Стадия '{first_stage_name}' завершена успешно. Есть следующий этап '{next_stage_name}'. Общий статус книги остается 'processing'.")
                else:
                     # Last stage completed successfully
                     final_book_workflow_status = 'complete'
                     final_error_message = 'Workflow completed successfully.'
                     print(f"[WorkflowProcessor] Последняя стадия '{first_stage_name}' завершена успешно. Общий статус книги: '{final_book_workflow_status}'.")
            elif stage_status == 'error':
                 # Stage completed with errors - overall book status becomes error
                 final_book_workflow_status = 'error'
                 final_error_message = f'Stage \'{first_stage_name}\' completed with errors.'
                 print(f"[WorkflowProcessor] Стадия '{first_stage_name}' завершена с ошибками. Общий статус книги: '{final_book_workflow_status}'.")
            else:
                 # Unexpected stage status - overall book status becomes error
                 final_book_workflow_status = 'error'
                 final_error_message = f'Stage \'{first_stage_name}\' completed with unexpected status: {stage_status}'
                 print(f"[WorkflowProcessor] Стадия '{first_stage_name}' завершена с неожиданным статусом '{stage_status}'. Общий статус книги: '{final_book_workflow_status}'.")

            # Update the book stage status in the DB
            workflow_db_manager.update_book_stage_status_workflow(book_id, first_stage_name, stage_status)
            print(f"[WorkflowProcessor] Стадия '{first_stage_name}' для книги ID {book_id} завершена со статусом: {stage_status}")

            # Update the overall book status if it's not 'processing' anymore
            if final_book_workflow_status != 'processing':
                 workflow_db_manager.update_book_workflow_status(book_id, final_book_workflow_status, error_message=final_error_message)
                 print(f"[WorkflowProcessor] Общий статус книги ID {book_id} обновлен на '{final_book_workflow_status}'.")
            else:
                 print(f"[WorkflowProcessor] Общий статус книги ID {book_id} остается 'processing'.")

            # TODO: Implement actual stage transition logic here
            if stage_status == 'complete' and next_stage_name:
                 # Move to the next stage (e.g., analysis) - For now, workflow stops here
                 print(f"[WorkflowProcessor] Переход к следующему этапу '{next_stage_name}' для книги ID {book_id} (пока не реализовано)...")

        print(f"[WorkflowProcessor] Рабочий процесс start_book_workflow для книги ID: {book_id} завершен (основная функция).")
        return True  # Process was initiated

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

# --- END OF FILE workflow_processor.py ---
