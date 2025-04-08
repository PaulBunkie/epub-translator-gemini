import ebooklib
from ebooklib import epub
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import io
import traceback
import html
import re
import unicodedata
# import xml.etree.ElementTree as ET # Не используется
import tempfile

# Регулярные выражения для очистки
REMOVE_TAGS_RE = re.compile(r'<[^>]+>')
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
# Регулярные выражения для Markdown (должны быть здесь)
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (Markdown->HTML) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    original_filepath = book_info.get("filepath")
    section_ids = book_info.get("section_ids_list", [])
    toc_data = book_info.get("toc", [])
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
    book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
    print("Метаданные установлены.")

    # --- Создание и добавление глав ---
    chapters = []
    failed_cleaning_chapters = []

    for i, section_id in enumerate(section_ids):
        # print(f"  Подготовка и добавление главы {i+1}/{len(section_ids)}: {section_id}")

        translated_text = get_translation_from_cache(original_filepath, section_id, target_language)
        section_status = book_info["sections"].get(section_id, "unknown")

        chapter_final_html_content = "" # Итоговая СТРОКА HTML контента

        # --- Подготовка контента ---
        if translated_text is not None:
            stripped_text = translated_text.strip()
            if not stripped_text:
                chapter_final_html_content = "<p> </p>"
            else:
                # 1. Очистка от HTML тегов и XML символов
                text_no_tags = REMOVE_TAGS_RE.sub('', stripped_text)
                text_normalized = unicodedata.normalize('NFC', text_no_tags)
                text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)

                # ---> 2. ВОЗВРАЩАЕМ КОНВЕРТАЦИЮ Markdown в HTML <---
                text_with_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                text_with_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_html)
                # ---> КОНЕЦ КОНВЕРТАЦИИ <---

                # 3. Формирование абзацев HTML из text_with_html
                if not text_with_html.strip():
                    chapter_final_html_content = "<p> </p>"
                    failed_cleaning_chapters.append(section_id)
                else:
                    paragraphs_html = []
                    # Используем результат конвертации Markdown
                    for para in text_with_html.split('\n\n'):
                         para_strip = para.strip()
                         if para_strip:
                              # Заменяем оставшиеся одиночные \n на <br/>
                              para_with_br = para_strip.replace('\n', '<br/>')
                              paragraphs_html.append(f"<p>{para_with_br}</p>")
                    if paragraphs_html:
                        # Соединяем параграфы для передачи в set_content
                        chapter_final_html_content = "\n".join(paragraphs_html)
                    else:
                         chapter_final_html_content = "<p> </p>"
                         failed_cleaning_chapters.append(section_id)

        elif section_status.startswith("error_"):
            chapter_final_html_content = f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: # Missing cache
            chapter_final_html_content = f"<p><i>[Translation data unavailable for section {html.escape(section_id)}]</i></p>"
        # --- Конец подготовки контента ---

        chapter_title = f"Раздел {i+1}"
        for toc_item in toc_data:
             if toc_item.get('id') == section_id:
                  chapter_title = toc_item.get('translated_title') or toc_item.get('title') or chapter_title
                  break

        chapter_filename = f'chapter_{i+1}.xhtml'
        epub_chapter = epub.EpubHtml(
            title=chapter_title,
            file_name=chapter_filename,
            lang=lang_code,
        )

        # --- УСТАНОВКА КОНТЕНТА ЧЕРЕЗ set_content() ---
        try:
            # Передаем строку, содержащую абзацы <p>...</p>
            epub_chapter.set_content(chapter_final_html_content)
        except Exception as set_content_err:
             print(f"!!! ОШИБКА при вызове epub_chapter.set_content для '{section_id}': {set_content_err}")
             epub_chapter.set_content(f"<p>Error setting content: {html.escape(str(set_content_err))}</p>")

        book.add_item(epub_chapter)
        chapters.append(epub_chapter)
    # --> Конец цикла for <--

    if failed_cleaning_chapters:
         print(f"[WARN] Контент {len(failed_cleaning_chapters)} глав стал пустым после очистки.")

    # --- Создание TOC ---
    book_toc = []
    # ... (код генерации TOC как раньше, используя объекты из chapters) ...
    if toc_data:
         # ...
         processed_toc_items = 0
         for item in toc_data:
              item_section_id = item.get('id')
              item_title = item.get('translated_title') or item.get('title')
              target_chapter = None
              for idx, sec_id in enumerate(section_ids):
                   if sec_id == item_section_id and idx < len(chapters):
                        target_chapter = chapters[idx]; break
              if target_chapter and item_title:
                   book_toc.append(target_chapter); processed_toc_items += 1
         # ...
    else: book_toc = chapters[:]
    book.toc = tuple(book_toc)

    # --- Добавляем NCX и Nav ---
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Определение Spine ---
    book.spine = ['nav'] + chapters
    print(f"Spine установлен: {[ (item if isinstance(item, str) else item.file_name) for item in book.spine]}")

    # --- ЗАПИСЬ EPUB ВО ВРЕМЕННЫЙ ФАЙЛ ---
    print("Запись EPUB (ebooklib, Markdown->HTML) во ВРЕМЕННЫЙ ФАЙЛ...")
    epub_content_bytes = None
    temp_epub_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as temp_f:
            temp_epub_path = temp_f.name
        # print(f"  Запись в {temp_epub_path}...")
        epub.write_epub(temp_epub_path, book, {}) # Запись в файл
        # print(f"  EPUB успешно записан во временный файл.")
        # ... (Проверка размера) ...
        # print("  Чтение байтов из временного файла...")
        with open(temp_epub_path, 'rb') as f_read:
            epub_content_bytes = f_read.read()
        # print(f"  Прочитано байт: {len(epub_content_bytes) if epub_content_bytes else 0}")
        print("EPUB успешно создан (ebooklib, Markdown->HTML) и прочитан из временного файла.")
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
                os.remove(temp_epub_path)
            except OSError as os_err:
                print(f"  ОШИБКА при удалении временного файла {temp_epub_path}: {os_err}")