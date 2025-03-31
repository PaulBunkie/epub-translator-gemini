# Внутри файла epub_parser.py

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import re

def get_epub_structure(epub_filepath):
    """
    Читает EPUB файл и возвращает упорядоченный список идентификаторов
    текстовых разделов (XHTML/HTML файлов из spine) и карту ID->Имя файла.
    """
    if not os.path.exists(epub_filepath):
        print(f"ОШИБКА: Файл EPUB не найден: {epub_filepath}")
        return None, None # Возвращаем два None

    try:
        print(f"Чтение структуры EPUB (spine): {epub_filepath}")
        book = epub.read_epub(epub_filepath)
        spine_items_raw = book.spine # Получаем spine как есть
        section_ids = []
        id_to_href_map = {item.id: item.file_name for item in book.get_items() if hasattr(item, 'id') and hasattr(item, 'file_name')} # Карта ID -> Имя файла

        if not spine_items_raw:
             print("Предупреждение: Элемент <spine> пуст или не найден.")
             # Попробуем альтернативный метод, если spine пуст
             items = book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
             section_ids = [item.id for item in items if hasattr(item, 'id') and item.id]
             print(f"Найдено {len(section_ids)} документов в манифесте.")
             if not section_ids:
                  print("ОШИБКА: Не найдено ни spine, ни документов в манифесте.")
                  return None, None # Возвращаем два None
             else:
                  print("Предупреждение: Используется порядок документов из манифеста, он может отличаться от порядка чтения.")
                  return section_ids, id_to_href_map

        # --- ИСПРАВЛЕННАЯ ЛОГИКА ОБРАБОТКИ SPINE ---
        print(f"Обработка {len(spine_items_raw)} элементов spine...")
        for spine_entry in spine_items_raw:
            item_id = None
            # Пытаемся извлечь idref, независимо от того, кортеж это или строка
            if isinstance(spine_entry, tuple) and len(spine_entry) > 0:
                item_id = spine_entry[0]
            elif isinstance(spine_entry, str):
                item_id = spine_entry

            if not item_id:
                 print(f"Предупреждение: Не удалось извлечь ID из элемента spine: {spine_entry}")
                 continue # Пропускаем этот элемент spine

            # Ищем элемент в манифесте по этому ID
            epub_item = book.get_item_with_id(item_id)
            if epub_item:
                if epub_item.get_type() == ebooklib.ITEM_DOCUMENT:
                    # Используем ID элемента как идентификатор секции
                    if item_id not in section_ids:
                        section_ids.append(item_id)
                    # else: # Повторный ID в spine - это нормально, не будем выводить предупреждение
                    #     print(f"Предупреждение: Повторный ID '{item_id}' в spine.")
                # else: # Игнорируем не-документы в spine молча
                #     pass
            else:
                print(f"Предупреждение: Не найден элемент с ID '{item_id}' в манифесте, указанный в spine.")
        # --- КОНЕЦ ИСПРАВЛЕННОЙ ЛОГИКИ ---

        # Если после обработки spine список пуст (очень странно, но возможно)
        if not section_ids:
             print("ОШИБКА: Не удалось извлечь ID документов из spine.")
             # Попробуем еще раз альтернативный метод
             items = book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
             section_ids = [item.id for item in items if hasattr(item, 'id') and item.id]
             if not section_ids:
                   return None, None
             else:
                   print("Предупреждение: Используется порядок документов из манифеста.")


        print(f"Найдено разделов (секций) в EPUB: {len(section_ids)}")
        return section_ids, id_to_href_map # Возвращаем список ID и карту
    except Exception as e:
        print(f"ОШИБКА: Не удалось прочитать структуру EPUB: {e}")
        return None, None # Возвращаем два None в случае любой ошибки

def get_epub_toc(epub_filepath, id_to_href_map):
    """
    Извлекает иерархическое оглавление (TOC) из EPUB.
    Возвращает список словарей [{'level': int, 'title': str, 'href': str, 'id': str}]
    или пустой список, или None в случае ошибки.
    """
    if not os.path.exists(epub_filepath):
        print(f"ОШИБКА: Файл EPUB не найден: {epub_filepath}")
        return None
    try:
        print(f"Чтение TOC EPUB: {epub_filepath}")
        book = epub.read_epub(epub_filepath)
        toc_list = []
        processed_hrefs = set()

        # --- ИЗМЕНЕННАЯ РЕКУРСИВНАЯ ФУНКЦИЯ ---
        def process_toc_item(item, level):
            # Проверяем тип элемента
            if isinstance(item, ebooklib.epub.Link):
                # Это ссылка - обрабатываем как раньше
                if not hasattr(item, 'title') or not hasattr(item, 'href'):
                    print(f"Предупреждение: Пропуск элемента Link из-за отсутствия атрибутов title/href: {item}")
                    return

                title = item.title
                href_full = item.href

                if href_full in processed_hrefs:
                    return # Пропускаем дубликаты href
                processed_hrefs.add(href_full)

                href_parts = href_full.split('#')
                file_href = href_parts[0]
                anchor = href_parts[1] if len(href_parts) > 1 else None

                section_id = None
                for item_id, item_href in id_to_href_map.items():
                    if os.path.basename(item_href) == os.path.basename(file_href):
                        section_id = item_id
                        break

                if section_id:
                    toc_list.append({
                        'level': level,
                        'title': title,
                        'href': href_full,
                        'id': section_id,
                        'anchor': anchor
                    })
                else:
                    print(f"Предупреждение: Не найден ID для элемента TOC href '{file_href}'. Ссылка TOC '{title}' может не работать.")

                # У Link тоже могут быть дети (вложенные ссылки в NCX)
                if hasattr(item, 'children') and item.children:
                     for child in item.children:
                         process_toc_item(child, level + 1)

            elif isinstance(item, ebooklib.epub.Section):
                 # Это секция - обрабатываем ее детей, саму секцию не добавляем
                 # print(f"Обработка секции TOC: {getattr(item, 'title', 'Без названия')}") # Для отладки
                 if hasattr(item, 'children') and item.children:
                      for child in item.children:
                          # Увеличиваем уровень для детей секции
                          process_toc_item(child, level + 1)

            elif isinstance(item, tuple):
                 # Иногда элементы book.toc могут быть кортежами (Section, Children)
                 # Пытаемся обработать и секцию, и детей
                 print(f"Предупреждение: Обнаружен кортеж в TOC: {item}")
                 if len(item) > 0:
                      process_toc_item(item[0], level) # Обрабатываем первый элемент кортежа (возможно, Section)
                 if len(item) > 1 and isinstance(item[1], list):
                      for child in item[1]: # Обрабатываем второй элемент (список детей)
                          process_toc_item(child, level + 1) # Увеличиваем уровень для детей

            else:
                # Пропускаем другие типы или если нет атрибутов
                print(f"Предупреждение: Пропуск неизвестного типа элемента TOC: {type(item)} - {item}")


        # --- КОНЕЦ РЕКУРСИВНОЙ ФУНКЦИИ ---

        if book.toc:
             for item in book.toc:
                 process_toc_item(item, 1) # Начинаем с уровня 1
        else:
             print("Предупреждение: TOC (book.toc) не найден в EPUB.")


        print(f"Извлечено {len(toc_list)} элементов TOC.")
        return toc_list if toc_list else [] # Возвращаем пустой список, если ничего не извлекли

    except Exception as e:
        print(f"ОШИБКА: Не удалось прочитать TOC EPUB: {e}")
        import traceback # Для детальной ошибки
        traceback.print_exc() # Печатаем traceback
        return None

def extract_section_text(epub_filepath, section_id):
    """
    Извлекает "чистый" текст из указанного раздела (по ID) EPUB файла.
    """
    if not os.path.exists(epub_filepath):
        print(f"ОШИБКА: Файл EPUB не найден: {epub_filepath}")
        return None

    try:
        book = epub.read_epub(epub_filepath)
        item = book.get_item_with_id(section_id)

        if item is None:
            print(f"ОШИБКА: Раздел с ID '{section_id}' не найден в EPUB.")
            return None

        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            print(f"ОШИБКА: Элемент с ID '{section_id}' не является текстовым документом.")
            return None

        print(f"Извлечение текста из раздела: {section_id} ({item.get_name()})")
        html_content = item.get_content()
        # Используем XML парсер для XHTML
        soup = BeautifulSoup(html_content, 'xml')

        # --- Улучшенное извлечение текста ---
        text_parts = []
        # Ищем тег body
        body = soup.find('body')
        if body:
            # Итерируемся по всем прямым потомкам body
            for element in body.find_all(recursive=False):
                 # Получаем текст из элемента, разделяя строки (важно для сохранения абзацев)
                 # separator='\n' может помочь сохранить переносы внутри тегов
                 text = element.get_text(separator='\n', strip=True)
                 if text:
                     text_parts.append(text)
            # Если внутри body не нашлось прямых потомков с текстом,
            # но сам body не пустой, попробуем взять весь текст из body
            if not text_parts and body.get_text(strip=True):
                 print("Предупреждение: Не найдены прямые потомки с текстом в body, извлекаем весь текст.")
                 body_text = body.get_text(separator='\n\n', strip=True)
                 if body_text:
                      text_parts.append(body_text)
        else:
            print("Предупреждение: Тег body не найден, пытаемся извлечь текст из всего документа.")
            # Если нет body, пытаемся взять текст из всего документа
            full_text = soup.get_text(separator='\n\n', strip=True)
            if full_text:
                text_parts.append(full_text)


        extracted_text = "\n\n".join(text_parts)

        # Дополнительная очистка от лишних пробелов и пустых строк
        extracted_text = re.sub(r'\n{3,}', '\n\n', extracted_text).strip()

        print(f"Извлечено ~{len(extracted_text)} символов текста.")
        # Возвращаем пустую строку, если ничего не извлекли, но не None
        return extracted_text if extracted_text else ""

    except Exception as e:
        print(f"ОШИБКА: Не удалось извлечь текст из раздела '{section_id}': {e}")
        # Возвращаем пустую строку в случае ошибки парсинга
        return ""