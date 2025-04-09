# epub_creator.py (v_final_final_checked)
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

# --- Регулярные выражения ---
# Удаляем HTML теги (если вдруг попали в кэш и не нужны)
REMOVE_TAGS_RE = re.compile(r'<[^>]+>')
# Удаляем недопустимые символы XML 1.0
INVALID_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')
# Конвертируем Markdown **bold** и *italic*
BOLD_MD_RE = re.compile(r'\*\*(.*?)\*\*')
ITALIC_MD_RE = re.compile(r'\*(.*?)\*')
# Ищем ТОЛЬКО маркеры сносок в виде цифр в верхнем регистре (¹²)
# Захватываем весь маркер (может быть несколько цифр подряд, напр. ¹⁰)
POSSIBLE_NOTE_MARKER_RE = re.compile(r'(\¹|\²|\³|[\u2074-\u2079]+)') # Группа 1 - сам маркер
# Словарь для конвертации суперскрипта в обычные цифры
SUPERSCRIPT_DIGITS_MAP = {'¹': 1, '²': 2, '³': 3, '⁴': 4, '⁵': 5, '⁶': 6, '⁷': 7, '⁸': 8, '⁹': 9, '⁰': 0}
# Ищем строку, начинающуюся с маркера-суперскрипта и пробела
NOTE_TEXT_START_RE = re.compile(r"^\s*(\¹|\²|\³|[\u2074-\u2079]+)\s+", re.MULTILINE) # Группа 1 - маркер

# --- Основная функция ---
def create_translated_epub(book_info, target_language):
    print(f"Запуск создания EPUB с ebooklib (Ссылки сносок, Заголовки v_final_checked) для книги: {book_info.get('filename', 'N/A')}, язык: {target_language}")

    # --- Получение исходных данных ---
    original_filepath = book_info.get("filepath")
    section_ids = book_info.get("section_ids_list", [])
    toc_data = book_info.get("toc", [])
    book_title_orig = os.path.splitext(book_info.get('filename', 'Untitled'))[0]
    epub_id_str = _get_epub_id(original_filepath)
    lang_code = target_language[:2] if target_language else "ru"

    if not original_filepath or not section_ids:
        print("Ошибка: Отсутствует путь к файлу или список секций для создания EPUB.")
        return None

    # --- Создание объекта книги EPUB ---
    book = epub.EpubBook()

    # --- Установка Метаданных ---
    book.set_identifier(f"urn:uuid:{epub_id_str}-{target_language}")
    book.set_title(f"{book_title_orig} ({target_language.capitalize()} Translation)")
    book.set_language(lang_code)
    book.add_author("EPUB Translator Tool")
    book.add_metadata('DC', 'description', 'Translated using EPUB Translator Tool')
    print("Метаданные установлены.")

    # --- Подготовка карты заголовков глав из TOC ---
    chapters = [] # Список для хранения созданных объектов EpubHtml
    chapter_titles_map = {} # Словарь section_id -> title
    default_title_prefix = "Раздел" # Префикс для глав без названия в TOC
    if toc_data:
        for item in toc_data:
            sec_id = item.get('id')
            title = item.get('translated_title') or item.get('title')
            if sec_id and title:
                 chapter_titles_map[sec_id] = title
                 # Проверяем язык заголовка, чтобы выбрать правильный префикс по умолчанию
                 if default_title_prefix == "Раздел" and any('a' <= c <= 'z' for c in title.lower()):
                      default_title_prefix = "Section"
    else:
        print("[WARN] Нет данных TOC, будут использованы заголовки по умолчанию.")

    # --- Основной цикл по секциям для создания глав ---
    for i, section_id in enumerate(section_ids):
        chapter_index = i + 1 # Номер главы для ID сносок
        # Получаем переведенный текст из кэша
        translated_text = get_translation_from_cache(original_filepath, section_id, target_language)
        # Получаем статус (для обработки ошибок)
        section_status = book_info["sections"].get(section_id, "unknown")

        # Переменная для итогового HTML контента тега <body>
        final_html_body_content = ""

        # Получаем заголовок главы из карты или генерируем по умолчанию
        chapter_title = chapter_titles_map.get(section_id, f"{default_title_prefix} {chapter_index}")
        chapter_title_escaped = html.escape(chapter_title)
        # Формируем HTML для заголовка H1
        header_html = f"<h1>{chapter_title_escaped}</h1>\n"

        # Обрабатываем только если есть переведенный текст
        if translated_text is not None:
            stripped_text = translated_text.strip()
            # Если текст пустой или только пробелы, ставим заглушку
            if not stripped_text:
                final_html_body_content = header_html + "<p> </p>"
            else:
                # --- Начало обработки текста главы ---
                note_targets_found = set() # Множество номеров сносок, для которых найден текст
                note_start_lines = {} # Словарь {note_num: line_index} для добавления якоря

                # 1. Найти строки, начинающиеся с маркеров сносок (¹²³)
                # print(f"    Поиск начал строк сносок в гл.{chapter_index}...")
                lines_original = stripped_text.splitlines()
                for line_idx, line in enumerate(lines_original):
                    match_start = NOTE_TEXT_START_RE.match(line)
                    if match_start:
                        current_note_num = -1
                        marker_super = match_start.group(1)
                        if marker_super:
                            try:
                                # Конвертируем маркер (возможно, из нескольких цифр) в число
                                num_str = "".join([str(SUPERSCRIPT_DIGITS_MAP.get(c, '')) for c in marker_super])
                                current_note_num = int(num_str) if num_str else -1
                            except (ValueError, KeyError):
                                pass # Ошибка конвертации, игнорируем
                        # Если номер успешно определен
                        if current_note_num > 0:
                            note_targets_found.add(current_note_num)
                            # Запоминаем индекс первой строки для этого номера
                            if current_note_num not in note_start_lines:
                                note_start_lines[current_note_num] = line_idx
                # if note_targets_found: print(f"      Найдены начала строк для сносок: {sorted(list(note_targets_found))}")

                # 2. Очистка ВСЕГО текста и конвертация Markdown -> HTML
                # Удаляем потенциально оставшиеся HTML теги (если были в кэше)
                text_no_tags = REMOVE_TAGS_RE.sub('', stripped_text)
                # Нормализуем Unicode
                text_normalized = unicodedata.normalize('NFC', text_no_tags)
                 # Удаляем недопустимые XML символы
                text_cleaned_xml = INVALID_XML_CHARS_RE.sub('', text_normalized)
                # Конвертируем Markdown
                text_with_md_html = BOLD_MD_RE.sub(r'<strong>\1</strong>', text_cleaned_xml)
                text_with_md_html = ITALIC_MD_RE.sub(r'<em>\1</em>', text_with_md_html)

                # 3. Замена маркеров сносок (только ¹²³) на HTML ссылки <a>
                final_text_with_links = text_with_md_html
                offset = 0 # Смещение индексов из-за замен
                replaced_count = 0
                # Ищем ТОЛЬКО маркеры-суперскрипты
                markers_found = list(POSSIBLE_NOTE_MARKER_RE.finditer(text_with_md_html))
                markers_found.sort(key=lambda m: m.start()) # Сортируем по позиции

                for match in markers_found:
                    current_note_num = -1
                    marker_super = match.group(1) # Группа для суперскрипта
                    # marker_digit_sq = match.group(2) # Группа для [1] - пока не используем
                    # marker_digit_curly = match.group(3) # Группа для {1} - пока не используем

                    # Определяем номер ТОЛЬКО из суперскрипта
                    if marker_super:
                        try:
                             num_str="".join([str(SUPERSCRIPT_DIGITS_MAP.get(c,'')) for c in marker_super])
                             current_note_num=int(num_str) if num_str else -1
                        except (ValueError, KeyError):
                             pass

                    # Заменяем маркер на ссылку ТОЛЬКО ЕСЛИ для этого номера была найдена строка-сноска
                    if current_note_num > 0 and current_note_num in note_targets_found:
                        start, end = match.start() + offset, match.end() + offset
                        note_anchor_id = f"note_{chapter_index}_{current_note_num}" # ID якоря, куда ведет ссылка
                        ref_id = f"ref_{chapter_index}_{current_note_num}"   # ID самой ссылки (для обратной навигации)
                        # Простая HTML ссылка, БЕЗ epub:type
                        replacement = f'<a id="{ref_id}" href="#{note_anchor_id}">{match.group(0)}</a>' # Используем group(0) для вставки оригинального маркера
                        final_text_with_links = final_text_with_links[:start] + replacement + final_text_with_links[end:]
                        offset += len(replacement) - (end - start) # Корректируем смещение
                        replaced_count += 1
                # if replaced_count > 0: print(f"      Заменено маркеров ссылками: {replaced_count}")

                # 4. Генерация финального HTML: разбивка на параграфы и добавление якорей к строкам сносок
                final_paragraphs_html = []
                processed_text_lines = final_text_with_links.splitlines() # Текст с <strong>, <em>, <a>
                current_paragraph_lines = [] # Буфер для строк текущего параграфа

                for line_idx, line in enumerate(processed_text_lines):
                    line_strip = line.strip()
                    anchor_html = "" # Якорь будет добавлен перед строкой

                    # Проверяем, является ли эта строка началом текста сноски
                    for note_num, start_line_idx in note_start_lines.items():
                         if line_idx == start_line_idx:
                              # Если да, создаем якорь <span id="...">
                              anchor_id = f"note_{chapter_index}_{note_num}"
                              anchor_html = f'<span id="{anchor_id}"></span>'
                              # print(f"        Добавлен якорь {anchor_id} к строке {line_idx}")
                              break # Якорь добавлен, переходим к следующей строке

                    # Добавляем якорь (если он есть) и саму строку в буфер параграфа
                    current_paragraph_lines.append(anchor_html + line)

                    # Проверяем, нужно ли завершить текущий параграф
                    is_last_line = (line_idx == len(processed_text_lines) - 1)
                    # Завершаем параграф, если строка пустая (разрыв абзаца) или это последняя строка файла
                    if not line_strip or is_last_line:
                         if current_paragraph_lines:
                              # Собираем строки абзаца, заменяя внутренние переносы на <br/>
                              # Фильтруем пустые строки перед join
                              valid_lines_in_para = [l for l in current_paragraph_lines if l.strip()]
                              if valid_lines_in_para:
                                   # НЕ ИСПОЛЬЗУЕМ html.escape() здесь, чтобы сохранить <a>, <span>, <strong>, <em>
                                   para_content = "<br/>".join(valid_lines_in_para)
                                   final_paragraphs_html.append(f"<p>{para_content}</p>")
                              # Очищаем буфер для следующего абзаца
                              current_paragraph_lines = []

                # Если после цикла не осталось абзацев (например, текст был только из пробелов)
                if not final_paragraphs_html:
                    final_paragraphs_html.append("<p> </p>")

                # Собираем итоговый HTML для <body>: Заголовок + Абзацы
                final_html_body_content = header_html + "\n".join(final_paragraphs_html)
                # ОТДЕЛЬНЫЙ БЛОК СНОСОК НЕ СОЗДАЕТСЯ

        # Обработка случая ошибки перевода или отсутствия кэша
        elif section_status.startswith("error_"):
            final_html_body_content = header_html + f"<p><i>[Translation Error: {html.escape(section_status)}]</i></p>"
        else: # Missing cache
            final_html_body_content = header_html + f"<p><i>[Translation data unavailable for section {section_id}]</i></p>"
        # --- Конец подготовки контента ---


        # --- Создание объекта главы EpubHtml ---
        chapter_filename = f'chapter_{chapter_index}.xhtml'
        epub_chapter = epub.EpubHtml(
            title=chapter_title, # Используется для TOC/NCX
            file_name=chapter_filename,
            lang=lang_code,
        )
        # Установка содержимого через set_content()
        try:
            epub_chapter.set_content(final_html_body_content)
        except Exception as set_content_err:
             print(f"!!! SET_CONTENT ERROR '{section_id}': {set_content_err}")
             # Запасной контент при ошибке
             epub_chapter.set_content(f"<h1>{chapter_title_escaped}</h1><p>Error setting content: {html.escape(str(set_content_err))}</p>")

        # Добавление главы в книгу и наш список
        book.add_item(epub_chapter)
        chapters.append(epub_chapter)
    # --- КОНЕЦ ЦИКЛА FOR ---


    # --- Создание Table of Contents (TOC) ---
    book_toc = []
    processed_toc_items = 0
    if toc_data:
        print("Генерация TOC из toc_data...")
        for item in toc_data:
             target_chapter=None; item_section_id=item.get('id'); item_title=item.get('translated_title') or item.get('title')
             # Ищем соответствующий объект главы в нашем списке chapters
             for idx, sec_id in enumerate(section_ids):
                 if sec_id == item_section_id and idx < len(chapters):
                     target_chapter = chapters[idx]
                     break
             # Добавляем объект главы в TOC, если нашли
             if target_chapter and item_title:
                 book_toc.append(target_chapter)
                 processed_toc_items += 1
        if processed_toc_items > 0:
             print(f"TOC с {processed_toc_items} элементами подготовлен.")
        else:
             print("[WARN] Не удалось сопоставить элементы TOC... Создаем плоский TOC.")
             book_toc = chapters[:] # Используем все созданные главы
    else:
        print("Нет данных TOC, создаем плоский TOC из глав...")
        book_toc = chapters[:] # Используем все созданные главы
    # Устанавливаем итоговый TOC (должен быть кортеж)
    book.toc = tuple(book_toc)


    # --- Добавляем обязательные NCX и Nav ---
    print("Добавление NCX и Nav...")
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())


    # --- Определение Spine (порядок чтения) ---
    # Должен включать 'nav' и все главы (объекты EpubHtml)
    book.spine = ['nav'] + chapters
    print(f"Spine установлен: {[ (item if isinstance(item, str) else item.file_name) for item in book.spine]}")


    # --- Запись EPUB во временный файл ---
    print(f"Запись EPUB (ebooklib, {target_language}) во ВРЕМЕННЫЙ ФАЙЛ...")
    epub_content_bytes = None
    temp_epub_path = None
    try:
         # Создаем временный файл
         with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as temp_f:
             temp_epub_path = temp_f.name
         # Записываем книгу в файл
         epub.write_epub(temp_epub_path, book, {})
         # Читаем байты из созданного файла
         with open(temp_epub_path, 'rb') as f_read:
             epub_content_bytes = f_read.read()
         print(f"EPUB успешно создан и прочитан. Размер: {len(epub_content_bytes)} байт.")
         return epub_content_bytes
    except Exception as e:
        print(f"ОШИБКА записи EPUB во временный файл: {e}")
        traceback.print_exc()
        return None
    finally:
        # Гарантированно удаляем временный файл
        if temp_epub_path and os.path.exists(temp_epub_path):
              try:
                  os.remove(temp_epub_path)
              except OSError as os_err:
                  print(f"ОШИБКА удаления временного файла {temp_epub_path}: {os_err}")
# --- КОНЕЦ ФУНКЦИИ ---