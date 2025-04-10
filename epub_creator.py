# epub_creator.py (ПОЛНЫЙ ФАЙЛ С ИСПРАВЛЕННЫМИ ОТСТУПАМИ В TOC v14 - УНИВЕРСАЛЬНЫЕ СНОСКИ)
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
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
# УНИВЕРСАЛЬНАЯ РЕГУЛЯРКА: Ищем ЛЮБЫЕ символы в верхнем индексе
# УПРОЩЕННАЯ РЕГУЛЯРКА: Основные символы и диапазоны верхних индексов
SUPERSCRIPT_MARKER_RE = re.compile(r'([\u2070-\u2079\u00B9-\u00B3]+)', re.UNICODE)
NOTE_TEXT_START_RE = re.compile(r"^\s*([\u2070-\u2079\u00B9-\u00B3]+)\s+", re.UNICODE)
# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (Двусторонние ссылки v14 - Универс. Сноски) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

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
                # --- Логика УНИВЕРСАЛЬНЫХ Двусторонних Ссылок ---
                note_markers_found = {}; note_paragraph_indices = {} # Используем маркер как ключ
                original_paragraphs = stripped_text.split('\n\n')

                # 1. Найти маркеры параграфов-сносок и их первое появление
                for para_idx, para_text_raw in enumerate(original_paragraphs):
                    match_start = NOTE_TEXT_START_RE.match(para_text_raw.strip())
                    if match_start:
                        marker = match_start.group(1)
                        if marker not in note_markers_found: # Первое упоминание маркера - это "reference"
                            note_markers_found[marker] = {'ref_para_index': para_idx, 'text_para_indices': []}
                        note_paragraph_indices[para_idx] = marker # Сохраняем индекс параграфа и маркер

                # 2. Очистка и Markdown -> HTML
                text_normalized = unicodedata.normalize('NFC', stripped_text); text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml); text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)

                # 3. Замена маркеров на ссылки в ОСНОВНОМ ТЕКСТЕ
                final_text_with_links = text_with_md_html; offset = 0; replaced_count = 0
                markers_found_in_text = list(SUPERSCRIPT_MARKER_RE.finditer(text_with_md_html)); markers_found_in_text.sort(key=lambda m: m.start())
                for match in markers_found_in_text:
                    marker = match.group(1)
                    for note_marker, note_data in note_markers_found.items(): # Ищем маркер в списке найденных сносок
                        if marker == note_marker:
                            start, end = match.start() + offset, match.end() + offset
                            ref_para_index = note_data['ref_para_index']
                            note_anchor_id = f"note_para_{chapter_index}_{marker}_{ref_para_index}"; ref_id = f"ref_{chapter_index}_{marker}_{ref_para_index}" # ID теперь включает маркер
                            replacement = f'<a id="{ref_id}" href="#{note_anchor_id}">{marker}</a>'; final_text_with_links = final_text_with_links[:start] + replacement + final_text_with_links[end:]; offset += len(replacement)-(end-start); replaced_count += 1
                            break # Нашли соответствие, выходим из внутреннего цикла
                if replaced_count > 0: print(f"      Заменено маркеров ссылками в гл.{chapter_index}: {replaced_count}")

                # 4. Генерация финального HTML по параграфам (ИСПРАВЛЕНА ЛОГИКА)
                final_paragraphs_html = []
                for para_idx, para_original_raw in enumerate(original_paragraphs):
                    para_strip = para_original_raw.strip()
                    if not para_strip: continue # Пропускаем пустые

                    p_id_attribute = ""; backlink_html = ""
                    is_note_paragraph = False
                    marker_for_this_para = note_paragraph_indices.get(para_idx) # Получаем маркер для этого параграфа, если есть

                    if marker_for_this_para:
                        is_note_paragraph = True

                    if is_note_paragraph:
                        # --- ЭТО АБЗАЦ-СНОСКА ---
                        ref_para_index = note_markers_found[marker_for_this_para]['ref_para_index']
                        note_anchor_id = f"note_para_{chapter_index}_{marker_for_this_para}_{ref_para_index}" # ID для параграфа
                        p_id_attribute = f' id="{note_anchor_id}"' # ID для параграфа
                        ref_id = f"ref_{chapter_index}_{marker_for_this_para}_{ref_para_index}"
                        backlink_html = f' <a href="#{ref_id}">↩</a>' # Обратная ссылка

                        # Берем ОРИГИНАЛЬНЫЙ текст параграфа (он уже содержит маркер в начале)
                        # Применяем к нему ТОЛЬКО очистку XML и нормализацию Unicode
                        # НЕ применяем Markdown и НЕ заменяем маркер на ссылку
                        text_normalized = unicodedata.normalize('NFC', para_strip)
                        text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                        # Заменяем переносы строк на <br/>
                        final_para_content_html = text_cleaned_xml.replace('\n', '<br/>')
                        # Добавляем обратную ссылку в конец
                        final_para_content_html += backlink_html
                        print(f"        Обработан параграф сноски с маркером '{marker_for_this_para}', ID '{note_anchor_id}'")

                    else:
                        # --- ЭТО ОБЫЧНЫЙ АБЗАЦ ---
                        # Применяем очистку XML, Markdown -> HTML и ЗАМЕНУ МАРКЕРОВ НА ССЫЛКИ
                        text_normalized = unicodedata.normalize('NFC', para_strip)
                        text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                        text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                        text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)

                        # Вставляем уже обработанный текст с ссылками из final_text_with_links
                        # Находим соответствующий параграф в original_paragraphs и берем его часть из final_text_with_links
                        start_index = 0
                        for i in range(para_idx): # Находим начало параграфа в final_text_with_links
                            start_index += len(original_paragraphs[i]) + 2 # +2 для разделителя '\n\n'
                        end_index = start_index + len(para_strip)
                        para_content_with_links = final_text_with_links[start_index:end_index]

                        # Заменяем переносы строк на <br/> в тексте с ссылками
                        final_para_content_html = para_content_with_links.replace('\n', '<br/>')


                    # Добавляем готовый параграф
                    final_paragraphs_html.append(f"<p{p_id_attribute}>{final_para_content_html}</p>")

                if not final_paragraphs_html: final_paragraphs_html.append("<p> </p>")
                # Итоговый HTML для body
                final_html_body_content = header_html + "\n".join(final_paragraphs_html)

        elif section_status.startswith("error_"): final_html_body_content = header_html + f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: final_html_body_content = header_html + f"<p><i>[Translation data unavailable for section {section_id}]</i></p>"

        chapter_filename = f'chapter_{chapter_index}.xhtml'
        epub_chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_filename, lang=lang_code)
        try: epub_chapter.set_content(final_html_body_content)
        except Exception as set_content_err: print(f"!!! SET_CONTENT ERROR '{section_id}': {set_content_err}"); #... fallback ...
        book.add_item(epub_chapter); chapters.append(epub_chapter)
    # --- КОНЕЦ ЦИКЛА FOR ---

    # --- Создание TOC (ПОЛНЫЙ БЛОК С ПРАВИЛЬНЫМИ ОТСТУПАМИ) ---
    book_toc = []
    processed_toc_items = 0
    if toc_data:
        print("Генерация TOC из toc_data...")
        for item in toc_data:
             target_chapter=None
             item_section_id=item.get('id')
             item_title=item.get('translated_title') or item.get('title')
             for idx, sec_id in enumerate(section_ids):
                 # Находим главу в нашем списке chapters по section_id
                 if sec_id == item_section_id and idx < len(chapters):
                     target_chapter = chapters[idx]
                     break
             # Добавляем объект главы в оглавление, если нашли
             if target_chapter and item_title:
                 book_toc.append(target_chapter)
                 processed_toc_items += 1
        # Проверяем, удалось ли что-то добавить из toc_data
        if processed_toc_items > 0:
             print(f"TOC с {processed_toc_items} элементами подготовлен.")
        # Если не удалось добавить (например, не нашли совпадений ID)
        else:
             print("[WARN] Не удалось сопоставить элементы TOC из toc_data. Создаем плоский TOC.")
             book_toc = chapters[:] # Используем все созданные главы
    # Если toc_data вообще не было
    else:
        print("Нет данных TOC, создаем плоский TOC из глав...")
        book_toc = chapters[:] # Используем все созданные главы
    # Устанавливаем итоговый TOC (должен быть кортеж)
    book.toc = tuple(book_toc)
    # --- КОНЕЦ СОЗДАНИЯ TOC ---

    # --- Добавляем NCX и Nav ---
    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    # --- Определение Spine ---
    book.spine = ['nav'] + chapters; print(f"Spine установлен: {[ (item if isinstance(item, str) else item.file_name) for item in book.spine]}")

    # --- ЗАПИСЬ EPUB ВО ВРЕМЕННЫЙ ФАЙЛ ---
    print(f"Запись EPUB (ebooklib, {target_language}) во ВРЕМЕННЫЙ ФАЙЛ...")
    # ... (Код записи во временный файл и возврата байтов) ...
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
