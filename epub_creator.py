# epub_creator.py (с ebooklib - СТРОГО ПО ДОКУМЕНТАЦИИ)
import ebooklib
from ebooklib import epub
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import io
import traceback
import html
import re
import unicodedata
import xml.etree.ElementTree as ET # Для валидации (опционально)
import tempfile # Нужен для записи в файл

# Регулярные выражения для очистки
REMOVE_TAGS_RE = re.compile(r'<[^>]+>')
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (строго по документации) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    original_filepath = book_info.get("filepath")
    section_ids = book_info.get("section_ids_list", [])
    toc_data = book_info.get("toc", []) # Используем для TOC
    book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]
    epub_id_str = _get_epub_id(original_filepath)
    lang_code = target_language[:2] if target_language else "ru"

    if not original_filepath or not section_ids:
        print("Ошибка: Отсутствует путь к файлу или список секций для создания EPUB.")
        return None

    # --- Создание книги ---
    book = epub.EpubBook()

    # --- Установка метаданных ---
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}")
    book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
    book.set_language(lang_code)
    book.add_author("EPUB Translator Tool")
    # Добавим описание для примера
    book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
    # Добавим кастомный мета-тег (пример)
    # book.add_metadata(None, 'meta', '', {'name': 'translator-engine', 'content': 'Gemini'})

    print("Метаданные установлены.")

    # --- Создание и добавление глав ---
    chapters = [] # Список для хранения объектов глав для TOC/Spine
    failed_cleaning_chapters = []
    failed_xml_validation_chapters = []

    for i, section_id in enumerate(section_ids):
        # print(f"  Подготовка и добавление главы {i+1}/{len(section_ids)}: {section_id}") # Отладка

        translated_text = get_translation_from_cache(original_filepath, section_id, target_language)
        section_status = book_info["sections"].get(section_id, "unknown")

        chapter_final_html_content = "" # Итоговая СТРОКА HTML контента

        # --- Подготовка контента (Агрессивная очистка) ---
        if translated_text is not None:
            stripped_text = translated_text.strip()
            if not stripped_text:
                # Используем простой HTML для пустых глав
                chapter_final_html_content = "<p> </p>"
            else:
                text_no_tags = REMOVE_TAGS_RE.sub('', stripped_text)
                text_normalized = unicodedata.normalize('NFC', text_no_tags)
                # НЕ экранируем html.escape, т.к. set_content, возможно, делает это сам или ожидает чистый текст
                text_final_cleaned = INVALID_XML_CHARS_RE.sub('', text_normalized)

                if not text_final_cleaned.strip():
                    chapter_final_html_content = "<p> </p>"
                    failed_cleaning_chapters.append(section_id)
                else:
                    # Заменяем переносы строк на <br/> и оборачиваем в <p>
                    chapter_final_html_content = "<p>" + text_final_cleaned.replace('\n', '<br/>') + "</p>"
                    # Уберем замену \n\n на </p><p>, т.к. текст уже очищен от тегов

        elif section_status.startswith("error_"):
            chapter_final_html_content = f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: # Missing cache
            chapter_final_html_content = f"<p><i>[Translation data unavailable for section {html.escape(section_id)}]</i></p>"
        # --- Конец подготовки контента ---


        # --- Ищем заголовок для главы и TOC ---
        chapter_title = f"Раздел {i+1}"
        for toc_item in toc_data:
             if toc_item.get('id') == section_id:
                  chapter_title = toc_item.get('translated_title') or toc_item.get('title') or chapter_title
                  break

        # --- Создаем объект EpubHtml ---
        chapter_filename = f'chapter_{i+1}.xhtml'
        epub_chapter = epub.EpubHtml(
            title=chapter_title,       # Title для TOC по умолчанию
            file_name=chapter_filename,
            lang=lang_code,
            # uid не нужен для EpubHtml если мы добавляем объект в TOC/Spine
        )

        # --- !!! УСТАНОВКА КОНТЕНТА ЧЕРЕЗ set_content() !!! ---
        try:
            # Передаем СТРОКУ HTML контента
            epub_chapter.set_content(chapter_final_html_content)
        except Exception as set_content_err:
             print(f"!!! ОШИБКА при вызове epub_chapter.set_content для '{section_id}': {set_content_err}")
             # Устанавливаем контент с ошибкой
             epub_chapter.set_content(f"<p>Error setting content: {html.escape(str(set_content_err))}</p>")

        # --- Добавляем главу в книгу ---
        book.add_item(epub_chapter)
        # Добавляем в наш список для последующего использования в TOC и Spine
        chapters.append(epub_chapter)
    # --> Конец цикла for <--

    if failed_cleaning_chapters:
         print(f"[WARN] Контент {len(failed_cleaning_chapters)} глав стал пустым после очистки.")

    # --- Создание TOC (Table of Contents) ---
    # Создаем TOC на основе объектов глав и данных из toc_data
    book_toc = []
    section_map = {ch.file_name: ch for ch in chapters} # Карта имя_файла -> объект главы
    processed_toc_items = 0
    if toc_data:
        print("Генерация TOC...")
        for item in toc_data:
            item_section_id = item.get('id')
            item_title = item.get('translated_title') or item.get('title')
            item_level = item.get('level', 1) # Уровень пока не используем для плоского TOC

            # Ищем соответствующую главу по section_id через наш список chapters
            target_chapter = None
            for idx, sec_id in enumerate(section_ids):
                 if sec_id == item_section_id and idx < len(chapters):
                      target_chapter = chapters[idx]
                      break

            if target_chapter and item_title:
                 # Добавляем сам объект главы в TOC
                 # Тогда библиотека использует его title и file_name
                 book_toc.append(target_chapter)
                 processed_toc_items += 1
            # elif item_title: # Можно добавить как секцию без ссылки, если нужно
            #      book_toc.append(epub.Section(item_title))
            else:
                 print(f"  Предупреждение TOC: Не удалось найти главу или title для TOC item: {item}")
        print(f"TOC с {processed_toc_items} элементами подготовлен.")
    else:
        # Если нет toc_data, создаем плоский TOC из всех глав
        print("Нет данных TOC, создаем плоский TOC из глав...")
        book_toc = chapters[:] # Копируем список глав

    # Присваиваем TOC книге (как кортеж)
    book.toc = tuple(book_toc)

    # --- Добавляем обязательные NCX и Nav файлы ---
    print("Добавление NCX и Nav...")
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Определение Spine (Порядок чтения) ---
    # Включаем 'nav' и все главы из нашего списка chapters
    book.spine = ['nav'] + chapters
    print(f"Spine установлен: {[ (item if isinstance(item, str) else item.id) for item in book.spine]}")

    # --- ЗАПИСЬ EPUB ВО ВРЕМЕННЫЙ ФАЙЛ (как наиболее надежный способ) ---
    print("Запись EPUB во ВРЕМЕННЫЙ ФАЙЛ...")
    epub_content_bytes = None
    temp_epub_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as temp_f:
            temp_epub_path = temp_f.name
        print(f"  Запись в {temp_epub_path}...")
        # Опции по умолчанию, как в документации
        epub.write_epub(temp_epub_path, book, {}) # <--- Запись в файл
        print(f"  EPUB успешно записан во временный файл.")

        try: # Проверка размера
            file_size = os.path.getsize(temp_epub_path)
            print(f"  Размер временного файла: {file_size} байт")
            if file_size < 500: # EPUB с NCX/NAV должен быть больше
                 print(f"  !!! ПРЕДУПРЕЖДЕНИЕ: Временный файл подозрительно мал!")
        except Exception as size_err:
            print(f"  Не удалось получить размер временного файла: {size_err}")

        print("  Чтение байтов из временного файла...")
        with open(temp_epub_path, 'rb') as f_read:
            epub_content_bytes = f_read.read()
        print(f"  Прочитано байт: {len(epub_content_bytes) if epub_content_bytes else 0}")

        print("EPUB успешно создан (ebooklib, по документации) и прочитан из временного файла.")
        return epub_content_bytes

    except Exception as e:
        print(f"ОШИБКА при записи/чтении временного файла EPUB: {e}")
        print("-" * 20 + " TRACEBACK " + "-" * 20)
        traceback.print_exc()
        print("-" * 50)
        return None
    finally:
        if temp_epub_path and os.path.exists(temp_epub_path):
            try:
                # print(f"  Удаление временного файла: {temp_epub_path}") # Можно убрать лог удаления
                os.remove(temp_epub_path)
            except OSError as os_err:
                print(f"  ОШИБКА при удалении временного файла {temp_epub_path}: {os_err}")
        # elif temp_epub_path:
        #      print(f"  Временный файл {temp_epub_path} не найден для удаления.") # Можно убрать
    # --- КОНЕЦ ЗАПИСИ ВО ВРЕМЕННЫЙ ФАЙЛ ---