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
    Создает новый EPUB файл в унифицированном формате (Unified Standard Rebuild).
    Обеспечивает максимальную совместимость с FBReader и другими читалками.
    """
    print(f"Запуск создания EPUB (Unified Standard) для: {book_info.get('filename', 'N/A')}")

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

    # --- Создание новой книги ---
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}")
    book.set_title(f"{book_title_orig} ({target_language.capitalize()})")
    book.set_language(lang_code)
    book.add_author("EPUB Translator Tool")

    # --- 1. Попытка перенести обложку из оригинала ---
    if original_filepath:
        try:
            if not os.path.exists(original_filepath):
                from config import UPLOADS_DIR
                original_filepath = os.path.join(UPLOADS_DIR, os.path.basename(original_filepath))
            
            if os.path.exists(original_filepath):
                orig_book = epub.read_epub(original_filepath)
                cover_item = None
                for item_id in ['cover', 'cover-image', 'img-cover']:
                    it = orig_book.get_item_with_id(item_id)
                    if it and it.get_type() == ebooklib.ITEM_IMAGE:
                        cover_item = it
                        break
                if not cover_item:
                    for it in orig_book.get_items_of_type(ebooklib.ITEM_IMAGE):
                        if 'cover' in it.get_name().lower() or 'cover' in it.get_id().lower():
                            cover_item = it
                            break
                if cover_item:
                    ext = os.path.splitext(cover_item.get_name())[1] or '.jpg'
                    cover_name = f"cover{ext}"
                    book.set_cover(cover_name, cover_item.get_content())
                    print(f"  Обложка перенесена: {cover_name}")
                del orig_book
        except Exception as e:
            print(f"  [INFO] Ошибка при попытке копирования обложки: {e}")

    # --- 2. Обработка глав ---
    chapters = []
    default_title_prefix = "Раздел" if lang_code == 'ru' else "Section"
    
    print(f"  Обработка {len(section_ids)} секций...")
    for i, epub_id in enumerate(section_ids):
        chapter_index = i + 1
        
        # Название главы
        chapter_title = None
        for t in toc_data:
            if str(t.get('id')) == str(epub_id):
                chapter_title = t.get('translated_title') or t.get('title')
                break
        if not chapter_title:
            chapter_title = f"{default_title_prefix} {chapter_index}"

        # Служебная ли секция?
        service_titles = ['cover', 'обложка', 'title', 'титульный', 'copyright', 'авторское право', 'contents', 'содержание', 'toc', 'annotation', 'аннотация']
        is_service = any(st in chapter_title.lower() for st in service_titles) or \
                     any(st in str(epub_id).lower() for st in service_titles)

        # Текст
        section_data = sections_data_map.get(epub_id, {})
        translated_text = get_translation_from_cache(original_filepath, epub_id, target_language)
        
        final_html_body = ""
        if not is_service:
            final_html_body += f"<h1>{html.escape(chapter_title)}</h1>\n"

        if translated_text:
            # Чистим AI маркер
            clean_text = re.sub(r'(?:\$\s*){3,}\s*$', '', translated_text).strip()
            # Удаляем дублирующийся заголовок
            clean_text = re.sub(r'^(?:#+\s*|\*\*|)' + re.escape(chapter_title) + r'(?:\*\*|)\s*', '', clean_text, flags=re.IGNORECASE).strip()
            
            original_paragraphs = clean_text.split('\n\n')
            note_definitions = defaultdict(list)
            note_targets_found = set()
            
            # 1. Сбор определений
            for p_raw in original_paragraphs:
                p_strip = p_raw.strip()
                if not p_strip: continue
                if NOTE_LINE_START_RE.match(p_strip):
                    for line in p_strip.split('\n'):
                        m = NOTE_LINE_START_RE.match(line.strip())
                        if m:
                            marker, note_text = m.groups()
                            num = get_int_from_superscript(marker)
                            if num > 0:
                                note_targets_found.add(num)

            # 2. Рендеринг параграфов
            ref_counters = defaultdict(int)
            def_counters = defaultdict(int)
            
            for p_raw in original_paragraphs:
                p_strip = p_raw.strip()
                if not p_strip: continue
                
                if NOTE_LINE_START_RE.match(p_strip):
                    f_lines = []
                    for line in p_strip.split('\n'):
                        line_s = line.strip()
                        if not line_s: continue
                        m = NOTE_LINE_START_RE.match(line_s)
                        if m:
                            marker, note_text = m.groups()
                            num = get_int_from_superscript(marker)
                            if num > 0:
                                def_counters[num] += 1
                                occ = def_counters[num]
                                note_id = f"note_{chapter_index}_{num}_{occ}"
                                ref_id = f"ref_{chapter_index}_{num}_{occ}"
                                
                                n_cleaned = INVALID_XML_CHARS_RE.sub('', note_text)
                                n_html = html.escape(n_cleaned)
                                n_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', n_html)
                                n_html = ITALIC_MD_RE.sub(r'<em>\1</em>', n_html)
                                
                                backlink = f' <a href="#{ref_id}" class="footnote-backlink" title="Back">↩</a>'
                                f_lines.append(f'<p class="footnote-definition" id="{note_id}"><small>{marker}</small> {n_html}{backlink}</p>')
                            else:
                                f_lines.append(f'<p>{html.escape(line_s)}</p>')
                        else:
                            f_lines.append(f'<p>{html.escape(line_s)}</p>')
                    
                    if f_lines:
                        final_html_body += f'<div class="footnote-block" style="font-size: 0.9em; border-top: 1px solid #eee; margin-top: 2em; padding-top: 1em;">\n{"".join(f_lines)}\n</div>'
                else:
                    text_norm = unicodedata.normalize('NFC', p_strip)
                    text_clean = INVALID_XML_CHARS_RE.sub('', text_norm)
                    p_html = html.escape(text_clean).replace('\n', '<br/>')
                    p_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', p_html)
                    p_html = ITALIC_MD_RE.sub(r'<em>\1</em>', p_html)
                    
                    # Замена маркеров на ссылки
                    matches = list(SUPERSCRIPT_MARKER_RE.finditer(p_html))
                    if matches:
                        new_p_html = ""
                        last_idx = 0
                        for m in matches:
                            marker = m.group(1)
                            num = get_int_from_superscript(marker)
                            if num > 0 and num in note_targets_found:
                                ref_counters[num] += 1
                                occ = ref_counters[num]
                                note_id = f"note_{chapter_index}_{num}_{occ}"
                                ref_id = f"ref_{chapter_index}_{num}_{occ}"
                                link = f'<sup class="footnote-ref"><a id="{ref_id}" href="#{note_id}">{marker}</a></sup>'
                                
                                new_p_html += p_html[last_idx:m.start()] + link
                                last_idx = m.end()
                        new_p_html += p_html[last_idx:]
                        p_html = new_p_html
                    
                    final_html_body += f"<p>{p_html}</p>\n"
        
        if not final_html_body:
            final_html_body = "<p> </p>"

        # Создание файла главы
        safe_file_name = f"section_{chapter_index:03d}.xhtml"
        chapter = epub.EpubHtml(
            title=chapter_title,
            file_name=safe_file_name,
            lang=lang_code,
            uid=str(epub_id)
        )
        
        css = "<style>body{font-family: serif; margin: 1em; line-height: 1.5;} h1{text-align: center; border-bottom: 1px dotted #ccc; padding-bottom: 0.5em;} p{margin: 0.5em 0; text-indent: 1.2em;} .footnote{margin-top: 1em; border-top: 1px solid #eee; padding-top: 0.5em;}</style>"
        xhtml_content = f'<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" lang="{lang_code}"><head><title>{html.escape(chapter_title)}</title>{css}</head><body>{final_html_body}</body></html>'
        chapter.content = xhtml_content.encode('utf-8', 'xmlcharrefreplace')
        
        book.add_item(chapter)
        chapters.append(chapter)

    # --- 3. Финализация ---
    book.toc = tuple(chapters)
    book.spine = ['nav'] + chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Запись
    t_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tf:
            t_path = tf.name
        epub.write_epub(t_path, book, {})
        with open(t_path, 'rb') as f:
            data = f.read()
        return data
    except Exception as e:
        print(f"  ОШИБКА записи EPUB: {e}")
        return None
    finally:
        if t_path and os.path.exists(t_path):
            try: os.remove(t_path)
            except: pass
        import gc
        gc.collect()

# --- END OF FILE epub_creator.py ---
