import argparse
import os
import time

# Импортируем функции из других модулей
from translation_module import configure_api, translate_text, CONTEXT_LIMIT_ERROR
from epub_parser import get_epub_structure, extract_section_text
from cache_manager import get_translation_from_cache, save_translation_to_cache, save_translated_chapter # Импортируем функцию сохранения полного файла

# Определим имя папки для полных переводов
from config import FULL_TRANSLATION_DIR

def run_epub_translation_test(epub_filepath, target_language, model_name, save_full_translation=False, max_parts=None):
    """Основная функция для тестирования перевода EPUB."""

    print("-" * 30)
    print(f"Запуск перевода для: {epub_filepath}")
    print(f"Язык перевода: {target_language}") # Ожидается английское название языка
    print(f"Модель: {model_name}")
    if save_full_translation:
        print("Режим: Полный перевод в один файл")
    else:
        print("Режим: Перевод с кэшированием по разделам")
    if max_parts:
        print(f"Ограничение: Максимум {max_parts} разделов")
    print("-" * 30)

    # 1. Получаем структуру EPUB
    section_ids = get_epub_structure(epub_filepath)
    if not section_ids:
        print("Не удалось получить структуру EPUB. Завершение.")
        return

    # 2. Цикл по разделам (имитация чтения)
    total_sections = len(section_ids)
    translated_count = 0 # Счетчик НОВЫХ переводов
    cached_count = 0     # Счетчик взятых из кэша
    error_count = 0
    context_limit_errors = 0 # Счетчик ошибок лимита контекста
    accumulated_translation = [] # Список для накопления перевода в режиме --full

    for i, section_id in enumerate(section_ids):
        # Проверяем ограничение по количеству частей ДО начала обработки
        if max_parts is not None and i >= max_parts:
             print(f"\nДостигнут лимит в {max_parts} разделов. Остановка обработки.")
             break # Выход из цикла for

        print(f"\n--- Обработка раздела {i + 1}/{total_sections if max_parts is None else min(total_sections, max_parts)}: {section_id} ---")

        translated_text = None # Сбрасываем текст для текущего раздела
        was_from_cache = False # Флаг, что взяли из кэша

        # 3. Проверка кэша
        cached_text = get_translation_from_cache(epub_filepath, section_id, target_language)

        if cached_text:
            print("-> Раздел найден в кэше.")
            translated_text = cached_text # Используем текст из кэша
            cached_count += 1
            was_from_cache = True # Устанавливаем флаг
        else:
            # 4. Если нет в кэше - извлекаем текст
            print("-> Перевод не найден в кэше. Извлечение текста...")
            original_text = extract_section_text(epub_filepath, section_id)

            if not original_text:
                print("Извлеченный текст пуст или не удалось извлечь. Пропускаем перевод.")
                continue

            # 5. Переводим извлеченный текст
            print("-> Отправка текста на перевод...")
            # Передаем язык и модель в функцию перевода
            api_result = translate_text(original_text, target_language, model_name)

            # 6. Обработка результата перевода
            if api_result == CONTEXT_LIMIT_ERROR:
                print("ОШИБКА: Текст раздела слишком велик для модели. Перевод прерван для этого раздела.")
                error_count += 1
                context_limit_errors += 1
                # continue # Пропускаем этот раздел
            elif api_result:
                print("-> Перевод успешен.")
                translated_text = api_result # Сохраняем успешный перевод
                translated_count += 1 # Считаем *новый* перевод

                # 7. !!! ВСЕГДА КЭШИРУЕМ УСПЕШНЫЙ НОВЫЙ ПЕРЕВОД !!!
                if not save_translation_to_cache(epub_filepath, section_id, target_language, translated_text):
                     print("Предупреждение: Ошибка сохранения в кэш (но перевод выполнен).")
                     # error_count += 1 # Можно считать ошибкой
            else:
                print("ОШИБКА: Перевод не удался (см. сообщение выше).")
                error_count += 1

        # --- Конец блока Кэш/API/Перевод ---

        # 8. Добавляем в накопленный результат (если режим --full И есть текст для этого раздела)
        if save_full_translation and translated_text:
            accumulated_translation.append(translated_text)

        # Пауза между запросами к API (только если не брали из кэша)
        if not was_from_cache:
            print("Пауза перед следующим разделом...")
            # Можно увеличить паузу при ошибках лимита, чтобы не "долбить" API
            if api_result == CONTEXT_LIMIT_ERROR:
                 time.sleep(5)
            else:
                 time.sleep(2)

    # --- Завершение ---

    # 9. Сохраняем полный перевод (если режим --full)
    if save_full_translation:
        print("\n--- Сохранение полного перевода ---")
        if accumulated_translation:
            final_text = "\n\n".join(accumulated_translation)
            # Создаем папку, если её нет
            os.makedirs(FULL_TRANSLATION_DIR, exist_ok=True)
            # Формируем имя выходного файла
            base_name = os.path.splitext(os.path.basename(epub_filepath))[0]
            output_filename = f"{base_name}_{target_language}_translated.txt" # Добавим язык в имя
            full_output_path = os.path.join(FULL_TRANSLATION_DIR, output_filename)

            if save_translated_chapter(final_text, full_output_path):
                print(f"Полный перевод сохранен в: {full_output_path}")
            else:
                print(f"ОШИБКА: Не удалось сохранить полный перевод в {full_output_path}")
        else:
            print("Нет переведенного текста для сохранения.")


    print("\n--- Итоги ---")
    processed_sections = i + 1 if max_parts is None else min(i + 1, max_parts)
    if max_parts is not None and i >= max_parts : # Если остановились из-за лимита
         processed_sections = max_parts

    print(f"Обработано разделов: {processed_sections} (из {total_sections} всего)")
    if save_full_translation:
         final_saved_count = len(accumulated_translation)
         print(f"Разделов сохранено в итоговый файл: {final_saved_count}")
    else:
         print(f"Успешно переведено и сохранено в кэш: {translated_count}")
         print(f"Загружено из кэша: {cached_count}")
    print(f"Общие ошибки (перевода/сохр.): {error_count}")
    if context_limit_errors > 0:
         print(f"Ошибки превышения лимита токенов: {context_limit_errors}")
    print("-" * 30)


if __name__ == "__main__":
    try:
        configure_api() # Сначала конфигурируем API

        parser = argparse.ArgumentParser(description="Тестовый запуск перевода EPUB файла по разделам.")
        parser.add_argument("epub_filepath", help="Путь к входному файлу EPUB.")
        # Изменяем значение по умолчанию и описание для языка
        parser.add_argument("-l", "--target_language", default="russian", help="Целевой язык перевода (англ. название, по умолчанию: russian).")
        parser.add_argument("-m", "--model_name", default="gemini-1.5-flash", help="Имя используемой модели Gemini (по умолчанию: gemini-1.5-flash).")
        parser.add_argument("--full", action="store_true", help="Сохранить весь перевод в один файл в папку translated.")
        parser.add_argument("--parts", type=int, default=None, help="Остановить перевод после указанного количества разделов.")


        args = parser.parse_args()

        run_epub_translation_test(
            epub_filepath=args.epub_filepath,
            target_language=args.target_language.lower(), # Переводим язык в нижний регистр для надежности сравнения
            model_name=args.model_name,
            save_full_translation=args.full,
            max_parts=args.parts
        )

    except ValueError as e:
        print(f"Ошибка конфигурации: {e}")
    except FileNotFoundError as e:
        print(f"Ошибка: Файл не найден - {e}")
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")