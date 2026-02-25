# --- START OF FILE epub_creator.py ---

from ebooklib import epub
import ebooklib # Для доступа к ITEM_DOCUMENT
from cache_manager import get_translation_from_cache, _get_epub_id
import os
import traceback
import html
import re
import unicodedata
import tempfile
from collections import defaultdict

# Регулярные выражения
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
SUPERSCRIPT_MARKER_RE = re.compile(r"([\¹\²\³\⁰\⁴\⁵\⁶\⁷\⁸\⁹]+)")
NOTE_LINE_START_RE = re.compile(r"^\s*([\¹\²\³\⁰\⁴\⁵\⁶\⁷\⁸\⁹]+)\s*(.*)", re.UNICODE)

# Карта для преобразования надстрочных цифр в обычные
SUPERSCRIPT_INT_MAP = {'¹': '1', '²': '2', '³': '3', '⁰': '0', '⁴': '4', '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}

def get_int_from_superscript(marker_str):
    """Преобразует строку надстрочных цифр в целое число."""
    if not marker_str: return -1
    num_str = "".join(SUPERSCRIPT_INT_MAP.get(c, '') for c in marker_str)
    try: return int(num_str) if num_str else -1
    except ValueError: return -1

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    """
    Создает новый EPUB файл с переведенным текстом. 
    Собирается из текста в БД/кэше. Оригинал используется только для ресурсов, если доступен.
    """
    print(f"Запуск создания EPUB для: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    original_filepath = book_info.get("filepath")
    section_ids = book_info.get("section_ids_list", [])
    if not section_ids and 'sections' in book_info:
        section_ids = list(book_info['sections'].keys())

    if not section_ids:
        print("[ERROR epub_creator] Нет ID секций для сборки.")
        return None

    toc_data = book_info.get("toc", [])
    sections_data_map = book_info.get("sections", {})
    book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]
    epub_id_str = book_info.get('book_id', 'unknown-book-id')
    lang_code = target_language[:2] if target_language else "ru"

    # --- Попытка чтения оригинала (необязательно) ---
    original_book = None
    if original_filepath:
        try:
            if os.path.exists(original_filepath):
                original_book = epub.read_epub(original_filepath)
            else:
                # Попытка найти в uploads для совместимости
                from config import UPLOADS_DIR
                alt_p = os.path.join(UPLOADS_DIR, os.path.basename(original_filepath))
                if os.path.exists(alt_p):
                    original_book = epub.read_epub(alt_p)
        except:
            pass

    if original_book:
        print(f"  Оригинальная книга прочитана для копирования ресурсов.")
    else:
        print(f"  [INFO] Сборка EPUB 'на лету' без оригинальных ресурсов.")

    # --- Создание новой книги ---
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}")
    book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
    book.set_language(lang_code)
    book.add_author("EPUB Translator Tool")
    book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')

    # --- Копирование ресурсов ---
    if original_book:
        copied_items_ids = set()
        items_to_copy = []
        for item in original_book.get_items():
            is_cover = item.get_id() == 'cover' or 'cover' in item.get_name().lower()
            if item.get_type() != ebooklib.ITEM_DOCUMENT or is_cover:
                item_id = item.get_id()
                if item_id not in copied_items_ids:
                    items_to_copy.append(item)
                    copied_items_ids.add(item_id)

        for item in items_to_copy:
            book.add_item(item)
        print(f"  Скопировано {len(items_to_copy)} ресурсов.")

        # Установка обложки
        cover_item = None
        for item in original_book.get_items_of_type(ebooklib.ITEM_IMAGE):
            if item.get_id() == 'cover' or 'cover' in item.get_name().lower():
                cover_item = item
                break
        if cover_item:
            book.set_cover(cover_item.file_name, cover_item.get_content())

    # --- Обработка и добавление глав ---
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

    print(f"  Обработка {len(section_ids)} секций...")
    for i, section_id in enumerate(section_ids):
        chapter_index = i + 1
        chapter_title = chapter_titles_map.get(section_id, f"{default_title_prefix} {chapter_index}")
        chapter_title_escaped = html.escape(chapter_title)
        header_html = f"<h1>{chapter_title_escaped}</h1>\n"
        final_html_body_content = header_html

        section_data = sections_data_map.get(section_id)
        section_status = section_data.get("status", "unknown") if section_data else "unknown"
        error_message = section_data.get("error_message") if section_data else None

        translated_text = get_translation_from_cache(original_filepath, section_id, target_language)

        if translated_text is not None:
            note_definitions = defaultdict(list)
            note_targets_found = set()
            note_paragraph_indices = set()
            reference_markers_data = []
            original_paragraphs = translated_text.split('\n\n')

            for para_idx, para_text_raw in enumerate(original_paragraphs):
                para_strip_orig = para_text_raw.strip()
                if not para_strip_orig: continue
                is_definition_para = False
                lines = para_strip_orig.split('\n')
                for line in lines:
                    match_line = NOTE_LINE_START_RE.match(line.strip())
                    if match_line:
                        is_definition_para = True
                        marker = match_line.group(1); note_text = match_line.group(2).strip()
                        note_num = get_int_from_superscript(marker)
                        if note_num > 0: note_definitions[note_num].append(note_text); note_targets_found.add(note_num)
                if is_definition_para: note_paragraph_indices.add(para_idx)
                for match in SUPERSCRIPT_MARKER_RE.finditer(para_strip_orig):
                     marker = match.group(1); note_num = get_int_from_superscript(marker)
                     if note_num > 0: reference_markers_data.append((para_idx, match, note_num))

            final_content_blocks = []
            definition_occurrence_counters = defaultdict(int)
            reference_occurrence_counters = defaultdict(int)

            for para_idx, para_original_raw in enumerate(original_paragraphs):
                para_strip = para_original_raw.strip()
                if not para_strip:
                    if para_original_raw: final_content_blocks.append("<p> </p>")
                    continue

                is_footnote_para = False
                for line_check in para_strip.split('\n'):
                    if NOTE_LINE_START_RE.match(line_check.strip()): is_footnote_para = True; break

                if is_footnote_para:
                    footnote_lines_html = []
                    for line in para_strip.split('\n'):
                        line_strip = line.strip()
                        if not line_strip: continue
                        match_line = NOTE_LINE_START_RE.match(line_strip)
                        if match_line:
                            marker = match_line.group(1); note_text = match_line.group(2).strip()
                            note_num = get_int_from_superscript(marker)
                            if note_num > 0:
                                definition_occurrence_counters[note_num] += 1; occ = definition_occurrence_counters[note_num]
                                note_anchor_id = f"note_{section_id}_{note_num}_{occ}"; ref_id = f"ref_{section_id}_{note_num}_{occ}"
                                backlink_html = f' <a class="footnote-backlink" href="#{ref_id}" title="Вернуться к тексту">↩</a>'
                                note_text_cleaned = INVALID_XML_CHARS_RE.sub('', note_text)
                                note_text_md = BOLD_MD_RE.sub(r'<strong>\1</strong>', note_text_cleaned)
                                note_text_md = ITALIC_MD_RE.sub(r'<em>\1</em>', note_text_md)
                                footnote_lines_html.append(f'<p class="footnote-definition" id="{note_anchor_id}">{marker} {note_text_md}{backlink_html}</p>')
                            else: footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')
                        else: footnote_lines_html.append(f'<p>{html.escape(line_strip)}</p>')
                    if footnote_lines_html: final_content_blocks.append(f'<div class="footnote-block">\n{chr(10).join(footnote_lines_html)}\n</div>')
                else:
                    text_normalized = unicodedata.normalize('NFC', para_strip)
                    text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                    text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                    text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)
                    
                    current_para_html = text_with_md_html; offset = 0
                    markers_in_para = sorted(list(SUPERSCRIPT_MARKER_RE.finditer(text_with_md_html)), key=lambda m: m.start())
                    for match in markers_in_para:
                        marker = match.group(1); note_num = get_int_from_superscript(marker)
                        if note_num > 0 and note_num in note_targets_found:
                            reference_occurrence_counters[note_num] += 1; occ = reference_occurrence_counters[note_num]
                            start, end = match.start() + offset, match.end() + offset
                            note_anchor_id = f"note_{section_id}_{note_num}_{occ}"; ref_id = f"ref_{section_id}_{note_num}_{occ}"
                            replacement = f'<sup class="footnote-ref"><a id="{ref_id}" href="#{note_anchor_id}" title="См. примечание {note_num}">{marker}</a></sup>'
                            current_para_html = current_para_html[:start] + replacement + current_para_html[end:]
                            offset += len(replacement) - (end - start)
                    
                    processed_html = current_para_html.replace('\n', '<br/>')
                    final_para_html = f"<p>{processed_html}</p>"
                    final_content_blocks.append(final_para_html)

            final_html_body_content += "\n".join(final_content_blocks)
            if not final_content_blocks and translated_text.strip() == "":
                 final_html_body_content += "<p> </p>"
        elif section_status.startswith("error_"):
            final_html_body_content += f"\n<p><i>[Ошибка перевода: {html.escape(error_message or section_status)}]</i></p>"
        else:
            final_html_body_content += f"\n<p><i>[Перевод недоступен (статус: {html.escape(section_status)})]</i></p>"

        # Определяем имя файла
        chapter_filename_to_use = f"chapter_{chapter_index}.xhtml"
        if original_book:
            try:
                original_item = original_book.get_item_with_id(section_id)
                if original_item and original_item.get_type() == ebooklib.ITEM_DOCUMENT:
                    chapter_filename_to_use = original_item.file_name
            except: pass

        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_filename_to_use, lang=lang_code, uid=section_id)
        basic_css = "<style>body{line-height:1.5; margin: 1em;} h1{margin-top:0; border-bottom: 1px solid #eee; padding-bottom: 0.2em; margin-bottom: 1em;} p{margin: 0.5em 0; text-indent: 0;} .footnote-block{font-size:0.9em; margin-top: 2em; border-top: 1px solid #eee; padding-top: 0.5em;} .footnote-definition{margin: 0.2em 0;} .footnote-ref a {text-decoration: none; vertical-align: super; font-size: 0.8em;}</style>"
        full_content = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang_code}" xml:lang="{lang_code}"><head><meta charset="utf-8"/><title>{chapter_title_escaped}</title>{basic_css}</head><body>{final_html_body_content}</body></html>'
        epub_chapter.content = full_content.encode('utf-8', 'xmlcharrefreplace')
        book.add_item(epub_chapter)
        chapters.append(epub_chapter)

    # TOC/Spine
    book_toc = []
    if toc_data:
        href_to_chapter_map = {ch.file_name: ch for ch in chapters}
        for item in toc_data:
            item_href = item.get('href'); item_title = item.get('translated_title') or item.get('title')
            if item_href and item_title:
                clean_href = item_href.split('#')[0]; target_chapter = href_to_chapter_map.get(clean_href)
                if target_chapter:
                    link_target = target_chapter.file_name + (f"#{item_href.split('#')[1]}" if '#' in item_href else '')
                    book_toc.append(epub.Link(link_target, item_title, uid=item.get('id', clean_href)))
    
    book.toc = tuple(book_toc) if book_toc else tuple(chapters)
    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    
    # Запись
    epub_content_bytes = None; temp_epub_path = None
    try:
         with tempfile.NamedTemporaryFile(delete=False, suffix=".epub", mode='wb') as temp_f: temp_epub_path = temp_f.name
         epub.write_epub(temp_epub_path, book, {})
         with open(temp_epub_path, 'rb') as f_read: epub_content_bytes = f_read.read()
         return epub_content_bytes
    except Exception as e: print(f"  ОШИБКА записи EPUB: {e}"); traceback.print_exc(); return None
    finally:
         if temp_epub_path and os.path.exists(temp_epub_path):
              try: os.remove(temp_epub_path)
              except: pass

# --- END OF FILE epub_creator.py ---
