# epub_creator.py (с ebooklib - ИСПРАВЛЕНЫ ВСЕ ОШИБКИ СИНТАКСИСА v4 - ФИНАЛ)
import ebooklib
from ebooklib import epub
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import io
import traceback
import html
import re
import unicodedata
import tempfile

# Регулярные выражения
REMOVE_TAGS_RE = re.compile(r'<[^>]+>')
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
POSSIBLE_NOTE_MARKER_RE = re.compile(r'(\¹|\²|\³|[\u2074-\u2079])|\[(\d+)\]|\{(\d+)\}')
SUPERSCRIPT_DIGITS_MAP = {'¹': 1, '²': 2, '³': 3, '⁴': 4, '⁵': 5, '⁶': 6, '⁷': 7, '⁸': 8, '⁹': 9, '⁰': 0}
NOTE_TEXT_START_RE = re.compile(r"^\s*(?:(\¹|\²|\³|[\u2074-\u2079]+)|(?:\[?(\d+)\]?\.?\)?))\s+(.*)", re.MULTILINE)

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (Корректные сноски, испр. синтаксис v4) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    original_filepath = book_info.get("filepath"); section_ids = book_info.get("section_ids_list", []); toc_data = book_info.get("toc", [])
    book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]; epub_id_str = _get_epub_id(original_filepath)
    lang_code = target_language[:2] if target_language else "ru"
    if not original_filepath or not section_ids: return None

    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}"); book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
    book.set_language(lang_code); book.add_author("EPUB Translator Tool"); book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
    print("Метаданные установлены.")

    chapters = []; chapter_titles_map = {}; default_title_prefix = "Раздел"
    if toc_data:
        for item in toc_data:
            sec_id = item.get('id'); title = item.get('translated_title') or item.get('title')
            if sec_id and title:
                 chapter_titles_map[sec_id] = title
                 if default_title_prefix == "Раздел" and any('a' <= c <= 'z' for c in title.lower()): default_title_prefix = "Section"
    else: print("[WARN] Нет данных TOC...")

    for i, section_id in enumerate(section_ids):
        chapter_index = i + 1; translated_text = get_translation_from_cache(original_filepath, section_id, target_language)
        section_status = book_info["sections"].get(section_id, "unknown"); final_html_body_content = ""
        chapter_title = chapter_titles_map.get(section_id, f"{default_title_prefix} {chapter_index}"); chapter_title_escaped = html.escape(chapter_title)
        header_html = f"<h1>{chapter_title_escaped}</h1>\n"

        if translated_text is not None:
            stripped_text = translated_text.strip()
            if not stripped_text: final_html_body_content = header_html + "<p> </p>"
            else:
                extracted_notes = {}; note_paragraphs_map = {}
                all_paragraphs_raw = stripped_text.split('\n\n')
                for para_idx, para_text_raw in enumerate(all_paragraphs_raw):
                    match = NOTE_TEXT_START_RE.match(para_text_raw.strip())
                    if match:
                        note_text = match.group(3).strip()
                        current_note_num = -1; marker_super = match.group(1); marker_digit = match.group(2)
                        # --- ИСПРАВЛЕННЫЙ БЛОК 1 (РАЗБИТ НА СТРОКИ) ---
                        if marker_super:
                            try:
                                num_str = "".join([str(SUPERSCRIPT_DIGITS_MAP.get(c, '')) for c in marker_super])
                                current_note_num = int(num_str) if num_str else -1
                            except:
                                pass
                        elif marker_digit:
                            try:
                                current_note_num = int(marker_digit)
                            except:
                                pass
                        # --- КОНЕЦ ИСПРАВЛЕНИЯ 1 ---
                        if current_note_num > 0 and note_text:
                             original_note_content = NOTE_TEXT_START_RE.sub(r'\3', para_text_raw.strip()).strip()
                             extracted_notes[current_note_num] = original_note_content
                             note_paragraphs_map[current_note_num] = para_idx

                text_no_tags = REMOVE_TAGS_RE.sub('', stripped_text)
                text_normalized = unicodedata.normalize('NFC', text_no_tags)
                text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)

                final_text_with_links = text_with_md_html; offset = 0; replaced_count = 0
                markers_found = list(POSSIBLE_NOTE_MARKER_RE.finditer(text_with_md_html)); markers_found.sort(key=lambda m: m.start())
                for match in markers_found:
                    current_note_num = -1; marker_super = match.group(1); marker_digit_sq = match.group(2); marker_digit_curly = match.group(3)
                    # --- ИСПРАВЛЕННЫЙ БЛОК 2 (РАЗБИТ НА СТРОКИ) ---
                    if marker_super:
                        try:
                             num_str="".join([str(SUPERSCRIPT_DIGITS_MAP.get(c,'')) for c in marker_super])
                             current_note_num=int(num_str) if num_str else -1
                        except:
                             pass
                    elif marker_digit_sq:
                         try:
                              current_note_num = int(marker_digit_sq)
                         except:
                              pass
                    elif marker_digit_curly:
                         try:
                              current_note_num = int(marker_digit_curly)
                         except:
                              pass
                    # --- КОНЕЦ ИСПРАВЛЕНИЯ 2 ---
                    if current_note_num > 0 and current_note_num in extracted_notes:
                        start, end = match.start() + offset, match.end() + offset; note_anchor_id = f"note_para_{chapter_index}_{current_note_num}"
                        replacement = f'<a epub:type="noteref" href="#{note_anchor_id}">{match.group(0)}</a>'
                        final_text_with_links = final_text_with_links[:start] + replacement + final_text_with_links[end:]; offset += len(replacement)-(end-start); replaced_count += 1
                # if replaced_count > 0: print(...)

                final_paragraphs_html = []
                if final_text_with_links.strip():
                    paragraphs_processed_with_links = final_text_with_links.split('\n\n')
                    for para_idx, para_processed in enumerate(paragraphs_processed_with_links):
                        para_strip = para_processed.strip(); p_id_attribute = ""; para_content_final = ""
                        if not para_strip: continue
                        note_num_for_this_para = None
                        for num, idx in note_paragraphs_map.items():
                             if idx == para_idx: note_num_for_this_para = num; break
                        if note_num_for_this_para is not None:
                            note_anchor_id = f"note_para_{chapter_index}_{note_num_for_this_para}"; p_id_attribute = f' id="{note_anchor_id}"'
                            original_note_content = extracted_notes.get(note_num_for_this_para, "")
                            note_text_cleaned = INVALID_XML_CHARS_RE.sub('', unicodedata.normalize('NFC', REMOVE_TAGS_RE.sub('', original_note_content)))
                            para_content_final = f"{note_num_for_this_para}. {html.escape(note_text_cleaned)}"
                            # print(...)
                        else:
                            para_content_final = para_strip.replace('\n', '<br/>')
                        final_paragraphs_html.append(f"<p{p_id_attribute}>{para_content_final}</p>")
                if not final_paragraphs_html: final_paragraphs_html.append("<p> </p>")
                final_html_body_content = header_html + "\n".join(final_paragraphs_html)

        elif section_status.startswith("error_"): final_html_body_content = header_html + f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: final_html_body_content = header_html + f"<p><i>[Translation data unavailable for section {html.escape(section_id)}]</i></p>"

        chapter_filename = f'chapter_{chapter_index}.xhtml'
        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_filename, lang=lang_code)
        try: epub_chapter.set_content(final_html_body_content)
        except Exception as set_content_err: print(f"!!! SET_CONTENT ERROR '{section_id}': {set_content_err}"); epub_chapter.set_content(f"<h1>{chapter_title_escaped}</h1><p>Error setting content: {html.escape(str(set_content_err))}</p>")
        book.add_item(epub_chapter); chapters.append(epub_chapter)
    # --- КОНЕЦ ЦИКЛА FOR ---

    book_toc = []; processed_toc_items = 0
    if toc_data:
        print("Генерация TOC из toc_data...")
        for item in toc_data:
             target_chapter=None; item_section_id=item.get('id'); item_title=item.get('translated_title') or item.get('title')
             for idx, sec_id in enumerate(section_ids):
                 if sec_id == item_section_id and idx < len(chapters): target_chapter = chapters[idx]; break
             if target_chapter and item_title: book_toc.append(target_chapter); processed_toc_items += 1
        if processed_toc_items > 0: print(f"TOC с {processed_toc_items} элементами подготовлен.")
        else: print("[WARN] Не удалось сопоставить элементы TOC... Создаем плоский TOC."); book_toc = chapters[:]
    else: print("Нет данных TOC, создаем плоский TOC из глав..."); book_toc = chapters[:]
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