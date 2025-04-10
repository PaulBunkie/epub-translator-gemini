# epub_creator.py (ИСПРАВЛЕННЫЙ ФАЙЛ - v25 - обработка ДО HTML, с <br/> в сносках)
from ebooklib import epub
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import traceback
import html
import re
import unicodedata
import tempfile

# Регулярные выражения (как в v22 - должны быть верны)
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
SUPERSCRIPT_MARKER_RE = re.compile(r"([\¹\²\³\u2070\u2074-\u2079]+)")
# NOTE_TEXT_START_RE используется для поиска начала строк ВНУТРИ блока сносок
NOTE_LINE_START_RE = re.compile(r"^\s*([\¹\²\³\u2070\u2074-\u2079]+)\s*(.*)", re.UNICODE)

# --- Карта для преобразования надстрочных цифр в обычные ---
SUPERSCRIPT_INT_MAP = {'¹': 1, '²': 2, '³': 3, '⁰': 0, '⁴': 4, '⁵': 5, '⁶': 6, '⁷': 7, '⁸': 8, '⁹': 9}

def get_int_from_superscript(marker_str):
    """Преобразует строку надстрочных цифр в int."""
    num_str = "".join(str(SUPERSCRIPT_INT_MAP.get(c, '')) for c in marker_str)
    try: return int(num_str) if num_str else -1
    except ValueError: return -1

# --- Основная функция (изменения для пост-процессинга) ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (Обработка ДО HTML v25 - с <br/> в сносках) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    original_filepath = book_info.get("filepath"); section_ids = book_info.get("section_ids_list", []); toc_data = book_info.get("toc", [])
    book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]; epub_id_str = _get_epub_id(original_filepath)
    lang_code = target_language[:2] if target_language else "ru"
    if not original_filepath or not section_ids: return None

    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}"); book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
    book.set_language(lang_code); book.add_author("EPUB Translator Tool"); book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
    print("Метаданные установлены.")

    chapters = []; chapter_titles_map = {}; default_title_prefix = "Раздел"
    if toc_data: # Определение chapter_titles_map
        for item in toc_data:
            sec_id = item.get('id'); title = item.get('translated_title') or item.get('title')
            if sec_id and title:
                 chapter_titles_map[sec_id] = title
                 if default_title_prefix == "Раздел" and any('a' <= c <= 'z' for c in title.lower()): default_title_prefix = "Section"
        if not chapter_titles_map: print("[WARN] Не удалось извлечь заголовки из toc_data.")
    else: print("[WARN] Нет данных TOC, будут использованы заголовки по умолчанию.")

    for i, section_id in enumerate(section_ids): # Основной цикл по главам
        chapter_index = i + 1; translated_text = get_translation_from_cache(original_filepath, section_id, target_language)
        section_status = book_info["sections"].get(section_id, "unknown"); final_html_body_content = ""
        chapter_title = chapter_titles_map.get(section_id, f"{default_title_prefix} {chapter_index}"); chapter_title_escaped = html.escape(chapter_title)
        header_html = f"<h1>{chapter_title_escaped}</h1>\n"

        if translated_text is not None:
            stripped_text = translated_text.strip()
            if not stripped_text: final_html_body_content = header_html + "<p> </p>"
            else:
                # --- Логика Двусторонних Ссылок ---
                note_targets_found = set(); note_paragraph_indices = {}
                original_paragraphs = stripped_text.split('\n\n')
                # 1. Найти индексы параграфов, содержащих НАЧАЛО блока сносок
                for para_idx, para_text_raw in enumerate(original_paragraphs):
                    # Ищем самое первое совпадение в начале параграфа
                    first_line = para_text_raw.strip().split('\n')[0]
                    match_start = NOTE_LINE_START_RE.match(first_line)
                    if match_start:
                        marker = match_start.group(1)
                        note_num = get_int_from_superscript(marker)
                        if note_num > 0:
                            # Запоминаем индекс параграфа для КАЖДОГО номера сноски в нем
                            note_paragraph_indices[note_num] = para_idx
                            # Добавляем все номера сносок из этого параграфа в targets
                            for line in para_text_raw.strip().split('\n'):
                                line_match = NOTE_LINE_START_RE.match(line.strip())
                                if line_match:
                                    line_marker = line_match.group(1)
                                    line_note_num = get_int_from_superscript(line_marker)
                                    if line_note_num > 0:
                                        note_targets_found.add(line_note_num)

                print(f"note_targets_found: {note_targets_found}") # Проверяем найденные номера

                # 2. Генерация HTML с обработкой сносок ДО HTML
                final_content_blocks = []
                processed_markers_count = 0
                for para_idx, para_original_raw in enumerate(original_paragraphs):
                    para_strip = para_original_raw.strip()
                    if not para_strip:
                        final_content_blocks.append("<p> </p>")
                        continue

                    is_footnote_para = para_idx in note_paragraph_indices.values()

                    if is_footnote_para:
                        # --- Обработка параграфа-сноски (возможно, многострочного) ---
                        footnote_lines_html = []
                        lines = para_strip.split('\n')
                        for line in lines:
                            line_strip = line.strip()
                            if not line_strip: continue

                            match_line = NOTE_LINE_START_RE.match(line_strip)
                            if match_line:
                                marker = match_line.group(1)
                                note_text = match_line.group(2).strip()
                                note_num = get_int_from_superscript(marker)

                                if note_num > 0:
                                    note_anchor_id = f"note_para_{chapter_index}_{note_num}"
                                    ref_id = f"ref_{chapter_index}_{note_num}"
                                    backlink_html = f' <a href="#{ref_id}">↩</a>'
                                    # Очищаем текст сноски от XML и Markdown (на всякий случай)
                                    note_text_cleaned = INVALID_XML_CHARS_RE.sub('', note_text)
                                    note_text_md = BOLD_MD_RE.sub(r'<strong>\1</strong>', note_text_cleaned)
                                    note_text_md = ITALIC_MD_RE.sub(r'<em>\1</em>', note_text_md)

                                    footnote_lines_html.append(f'<p id="{note_anchor_id}">{marker} {note_text_md}{backlink_html}</p>')
                                else: # Строка не начинается с маркера сноски, но внутри блока
                                     footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>') # Просто экранируем
                            else: # Строка не начинается с маркера сноски
                                footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>') # Просто экранируем

                        if footnote_lines_html:
                            final_content_blocks.append(f'<div class="footnote-block">\n{"\n".join(footnote_lines_html)}\n</div>')

                    else:
                        # --- Обработка обычного параграфа ---
                        # Очистка XML
                        text_normalized = unicodedata.normalize('NFC', para_strip)
                        text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                        # Markdown
                        text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                        text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)

                        # Замена маркеров сносок на ссылки <a>
                        current_para_html = text_with_md_html
                        offset = 0
                        markers_in_para = list(SUPERSCRIPT_MARKER_RE.finditer(text_with_md_html))
                        markers_in_para.sort(key=lambda m: m.start()) # Сортируем по позиции

                        for match in markers_in_para:
                            marker = match.group(1)
                            note_num = get_int_from_superscript(marker)

                            if note_num > 0 and note_num in note_targets_found:
                                start, end = match.start() + offset, match.end() + offset
                                note_anchor_id = f"note_para_{chapter_index}_{note_num}"
                                ref_id = f"ref_{chapter_index}_{note_num}"
                                replacement = f'<a id="{ref_id}" href="#{note_anchor_id}">{marker}</a>'
                                current_para_html = current_para_html[:start] + replacement + current_para_html[end:]
                                offset += len(replacement) - (end - start)
                                processed_markers_count += 1

                        # Замена \n на <br/> и обертка в <p>
                        final_para_html = f"<p>{current_para_html.replace('\n', '<br/>')}</p>"
                        final_content_blocks.append(final_para_html)

                if processed_markers_count > 0:
                    print(f"      Заменено маркеров ссылками: {processed_markers_count}")

                # Собираем финальный HTML
                final_html_body_content = header_html + "\n".join(final_content_blocks)


        elif section_status.startswith("error_"): final_html_body_content = header_html + f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: final_html_body_content = header_html + f"<p><i>[Translation data unavailable for section {section_id}]</i></p>"

        chapter_filename = f'chapter_{chapter_index}.xhtml'
        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_filename, lang=lang_code)
        try: epub_chapter.set_content(final_html_body_content)
        except Exception as set_content_err: print(f"!!! SET_CONTENT ERROR '{section_id}': {set_content_err}"); #... fallback ...
        book.add_item(epub_chapter); chapters.append(epub_chapter)
    # --- КОНЕЦ ЦИКЛА FOR ---

    # --- Создание TOC (без изменений) ---
    book_toc = []
    processed_toc_items = 0
    if toc_data:
        print("Генерация TOC из toc_data...")
        for item in toc_data:
             target_chapter=None
             item_section_id=item.get('id')
             item_title=item.get('translated_title') or item.get('title')
             for idx, sec_id in enumerate(section_ids):
                 if sec_id == item_section_id and idx < len(chapters):
                     target_chapter = chapters[idx]
                     break
             if target_chapter and item_title:
                 book_toc.append(target_chapter)
                 processed_toc_items += 1
        if processed_toc_items > 0:
             print(f"TOC с {processed_toc_items} элементами подготовлен.")
        else:
             print("[WARN] Не удалось сопоставить элементы TOC из toc_data. Создаем плоский TOC.")
             book_toc = chapters[:]
    else:
        print("Нет данных TOC, создаем плоский TOC из глав...")
        book_toc = chapters[:]
    book.toc = tuple(book_toc)
    # --- КОНЕЦ СОЗДАНИЯ TOC ---

    # --- Добавляем NCX и Nav (без изменений) ---
    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    # --- Определение Spine (без изменений) ---
    book.spine = ['nav'] + chapters; print(f"Spine установлен: {[ (item if isinstance(item, str) else item.file_name) for item in book.spine]}")

    # --- ЗАПИСЬ EPUB ВО ВРЕМЕННЫЙ ФАЙЛ (без изменений) ---
    print(f"Запись EPUB (ebooklib, {target_language}) во ВРЕМЕННЫЙ ФАЙЛ...")
    epub_content_bytes = None; temp_epub_path = None
    try:
         with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as temp_f: temp_epub_path = temp_f.name
         epub.write_epub(temp_epub_path, book, {})
         with open(temp_epub_path, 'rb') as f_read: epub_content_bytes = f_read.read()
         print("EPUB успешно создан и прочитан.")
         return epub_content_bytes
    except Exception as e: print(f"ОШИБКА записи: {e}"); traceback.print_exc(); return None
    finally:
         if temp_epub_path and os.path.exists(temp_epub_path):
              try: os.remove(temp_epub_path)
              except OSError as os_err: print(f"ОШИБКА удаления temp file: {os_err}")
# --- КОНЕЦ ФУНКЦИИ ---
