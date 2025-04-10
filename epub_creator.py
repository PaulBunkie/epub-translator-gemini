# epub_creator.py (v28 - Поддержка непоследовательной нумерации сносок)

from ebooklib import epub
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import traceback
import html
import re
import unicodedata
import tempfile
from collections import defaultdict # Импортируем defaultdict

# Регулярные выражения (как в v25)
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
SUPERSCRIPT_MARKER_RE = re.compile(r"([\¹\²\³\u2070\u2074-\u2079]+)")
NOTE_LINE_START_RE = re.compile(r"^\s*([\¹\²\³\u2070\u2074-\u2079]+)\s*(.*)", re.UNICODE)

# --- Карта для преобразования ---
SUPERSCRIPT_INT_MAP = {'¹': 1, '²': 2, '³': 3, '⁰': 0, '⁴': 4, '⁵': 5, '⁶': 6, '⁷': 7, '⁸': 8, '⁹': 9}

def get_int_from_superscript(marker_str):
    if not marker_str: return -1
    num_str = "".join(str(SUPERSCRIPT_INT_MAP.get(c, '')) for c in marker_str)
    try: return int(num_str) if num_str else -1
    except ValueError: return -1

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (v28 - непослед. нумерация) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

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
            if not stripped_text: final_html_body_content = header_html + "<p> </p>"
            else:
                # --- Этап 1: Сбор информации о сносках ---
                note_definitions = defaultdict(list) # {note_num: [text1, text2,...]}
                note_targets_found = set()           # {1, 2, 3, ...}
                note_paragraph_indices = set()       # {index1, index2, ...} - Индексы параграфов с определениями
                reference_markers_data = []          # [(para_idx, match_obj, note_num), ...] - Ссылки в тексте

                original_paragraphs = stripped_text.split('\n\n')
                for para_idx, para_text_raw in enumerate(original_paragraphs):
                    para_strip_orig = para_text_raw.strip()
                    if not para_strip_orig: continue

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

                    # Ищем ссылки во всех параграфах (даже в блоках сносок, если вдруг)
                    # Важно: finditer, а не search/match
                    for match in SUPERSCRIPT_MARKER_RE.finditer(para_strip_orig):
                         marker = match.group(1)
                         note_num = get_int_from_superscript(marker)
                         if note_num > 0:
                             reference_markers_data.append((para_idx, match, note_num))

                print(f"note_targets_found: {note_targets_found}")

                # --- Этап 2: Генерация HTML ---
                final_content_blocks = []
                processed_markers_count = 0
                reference_occurrence_counters = defaultdict(int) # Счетчик для ссылок {note_num: count}
                definition_occurrence_counters = defaultdict(int)# Счетчик для определений {note_num: count}

                for para_idx, para_original_raw in enumerate(original_paragraphs):
                    para_strip = para_original_raw.strip()
                    if not para_strip:
                        final_content_blocks.append("<p> </p>")
                        continue

                    # is_footnote_para = para_idx in note_paragraph_indices
                    # Проверяем немного иначе: содержит ли *этот* параграф определения?
                    is_footnote_para = False
                    lines_for_check = para_strip.split('\n')
                    for line_check in lines_for_check:
                        if NOTE_LINE_START_RE.match(line_check.strip()):
                            is_footnote_para = True
                            break # Достаточно одного совпадения

                    if is_footnote_para:
                        # --- Обработка параграфа-сноски ---
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
                                    # Увеличиваем счетчик определений для этого номера
                                    definition_occurrence_counters[note_num] += 1
                                    occ = definition_occurrence_counters[note_num]

                                    note_anchor_id = f"note_para_{chapter_index}_{note_num}_{occ}"
                                    ref_id = f"ref_{chapter_index}_{note_num}_{occ}" # ID для обратной ссылки
                                    backlink_html = f' <a href="#{ref_id}">↩</a>' # Ссылка указывает на ID ссылки в тексте

                                    note_text_cleaned = INVALID_XML_CHARS_RE.sub('', note_text)
                                    note_text_md = BOLD_MD_RE.sub(r'<strong>\1</strong>', note_text_cleaned)
                                    note_text_md = ITALIC_MD_RE.sub(r'<em>\1</em>', note_text_md)
                                    # Формируем параграф определения с уникальным ID
                                    footnote_lines_html.append(f'<p id="{note_anchor_id}">{marker} {note_text_md}{backlink_html}</p>')
                                else:
                                     footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')
                            else:
                                footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')

                        if footnote_lines_html:
                            final_content_blocks.append(f'<div class="footnote-block">\n{"\n".join(footnote_lines_html)}\n</div>')

                    else:
                        # --- Обработка обычного параграфа ---
                        text_normalized = unicodedata.normalize('NFC', para_strip)
                        text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                        text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                        text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_cleaned_xml)

                        # Замена маркеров сносок на ссылки <a>
                        current_para_html = text_with_md_html
                        offset = 0
                        # Используем finditer для поиска всех маркеров
                        markers_in_para = list(SUPERSCRIPT_MARKER_RE.finditer(text_with_md_html))
                        markers_in_para.sort(key=lambda m: m.start())

                        for match in markers_in_para:
                            marker = match.group(1)
                            note_num = get_int_from_superscript(marker)

                            # Проверяем, есть ли вообще такое определение
                            if note_num > 0 and note_num in note_targets_found:
                                # Увеличиваем счетчик ССЫЛОК для этого номера
                                reference_occurrence_counters[note_num] += 1
                                occ = reference_occurrence_counters[note_num]

                                start, end = match.start() + offset, match.end() + offset
                                # Генерируем ID на основе номера и ПОРЯДКОВОГО НОМЕРА ССЫЛКИ
                                note_anchor_id = f"note_para_{chapter_index}_{note_num}_{occ}" # Куда ссылаемся
                                ref_id = f"ref_{chapter_index}_{note_num}_{occ}" # ID самой ссылки
                                replacement = f'<a id="{ref_id}" href="#{note_anchor_id}">{marker}</a>'

                                current_para_html = current_para_html[:start] + replacement + current_para_html[end:]
                                original_length = match.end() - match.start()
                                offset += len(replacement) - original_length
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

    # ... (Создание TOC, NCX/Nav, Spine, Запись файла - без изменений) ...
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

    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters; print(f"Spine установлен: {[ (item if isinstance(item, str) else item.file_name) for item in book.spine]}")

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
