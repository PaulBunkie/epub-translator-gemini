# Внутри файла epub_parser.py

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import re
import uuid

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
    Извлекает иерархическое оглавление (TOC) из EPUB,
    пытаясь прочитать NCX или Навигационный документ XHTML.
    Возвращает список словарей или None.
    """
    if not os.path.exists(epub_filepath):
        print(f"ОШИБКА: Файл EPUB не найден: {epub_filepath}")
        return None
    try:
        print(f"Чтение TOC EPUB (NCX/Nav): {epub_filepath}")
        book = epub.read_epub(epub_filepath)
        toc_list = []
        processed_hrefs = set()

        # --- Попытка 1: Парсинг Навигационного документа (EPUB 3) ---
        nav_doc = None
        # Преобразуем генератор в список, чтобы проверить его длину и взять элемент
        nav_items_list = list(book.get_items_of_type(ebooklib.ITEM_NAVIGATION)) # <--- ИЗМЕНЕНИЕ
        if nav_items_list: # Проверяем, что список не пустой
            nav_doc = nav_items_list[0] # <--- Теперь берем из списка
            print(f"Найден Навигационный документ: {nav_doc.get_name()}")
            nav_content = nav_doc.get_content()
            nav_soup = BeautifulSoup(nav_content, 'xml')
            toc_nav = nav_soup.find('nav', attrs={'epub:type': 'toc'})
            if toc_nav:
                root_ol = toc_nav.find('ol', recursive=False)
                if root_ol:
                    print("Парсинг TOC из Nav Document (XHTML)...")
                    _parse_nav_ol(root_ol, 1, toc_list, processed_hrefs, id_to_href_map)
            nav_content = nav_doc.get_content()
            nav_soup = BeautifulSoup(nav_content, 'xml')
            # Ищем <nav epub:type="toc">, внутри него <ol>
            toc_nav = nav_soup.find('nav', attrs={'epub:type': 'toc'})
            if toc_nav:
                root_ol = toc_nav.find('ol', recursive=False)
                if root_ol:
                    print("Парсинг TOC из Nav Document (XHTML)...")
                    _parse_nav_ol(root_ol, 1, toc_list, processed_hrefs, id_to_href_map)

        # --- Попытка 2: Парсинг NCX (EPUB 2), если Nav не дал результатов ---
        if not toc_list: # Если после парсинга Nav список пуст
            ncx_item = book.get_item_with_id('ncx') # Ищем стандартный ID 'ncx'
            # Иногда ID может быть 'ncxtoc' или другим, ищем по имени файла
            if not ncx_item:
                for item in book.get_items():
                     if item.get_name().lower().endswith('.ncx'):
                          ncx_item = item
                          break
            if ncx_item:
                 print(f"Найден NCX файл: {ncx_item.get_name()}")
                 ncx_content = ncx_item.get_content()
                 ncx_soup = BeautifulSoup(ncx_content, 'xml')
                 # Ищем корневые navPoint внутри navMap
                 nav_map = ncx_soup.find('navMap')
                 if nav_map:
                      print("Парсинг TOC из NCX...")
                      for nav_point in nav_map.find_all('navPoint', recursive=False):
                           _parse_ncx_navpoint(nav_point, 1, toc_list, processed_hrefs, id_to_href_map)

        # --- Если ничего не найдено ---
        if not toc_list:
             print("Предупреждение: Не удалось извлечь TOC ни из Nav документа, ни из NCX.")
             # В крайнем случае, можно вернуть плоский список из spine, но без названий
             # return [{'level': 1, 'title': section_id, 'href': id_to_href_map.get(section_id,''), 'id': section_id, 'anchor': None} for section_id in get_epub_structure(epub_filepath)[0]]
             return [] # Возвращаем пустой список

        print(f"Извлечено {len(toc_list)} элементов TOC.")
        return toc_list

    except Exception as e:
        print(f"ОШИБКА: Не удалось прочитать TOC EPUB: {e}")
        import traceback
        traceback.print_exc()
        return None

# Вспомогательная функция для парсинга <ol> из Nav Document
def _parse_nav_ol(ol_element, level, toc_list, processed_hrefs, id_to_href_map):
    if not ol_element or not hasattr(ol_element, 'find_all'):
        return
    for li in ol_element.find_all('li', recursive=False):
        link = li.find('a', recursive=False)
        if link and link.get('href'):
            title = link.get_text(strip=True)
            href_full = link['href']

            # Пропускаем дубликаты href
            if href_full in processed_hrefs:
                # Но проверяем на вложенность
                nested_ol = li.find('ol', recursive=False)
                if nested_ol:
                    _parse_nav_ol(nested_ol, level + 1, toc_list, processed_hrefs, id_to_href_map)
                continue
            processed_hrefs.add(href_full)

            href_parts = href_full.split('#')
            file_href = href_parts[0]
            anchor = href_parts[1] if len(href_parts) > 1 else None

            section_id = None
            for item_id_map, item_href_map in id_to_href_map.items():
                if os.path.basename(item_href_map) == os.path.basename(file_href):
                    section_id = item_id_map
                    break

            if section_id:
                toc_list.append({
                    'level': level, 'title': title, 'href': href_full,
                    'id': section_id, 'anchor': anchor
                })
            else:
                print(f"Предупреждение (Nav): Не найден ID для href '{file_href}'. Ссылка '{title}' может не работать.")

            # Ищем вложенный <ol> и рекурсивно вызываем
            nested_ol = li.find('ol', recursive=False)
            if nested_ol:
                _parse_nav_ol(nested_ol, level + 1, toc_list, processed_hrefs, id_to_href_map)
        else:
            # Иногда текст может быть просто в li без ссылки, или это заголовок группы
            # Можно попробовать взять текст из li, но без href он бесполезен для навигации
            list_text = li.get_text(strip=True)
            if list_text:
                print(f"Предупреждение (Nav): Найден элемент li без ссылки: {list_text}")
            # Все равно проверяем на вложенность
            nested_ol = li.find('ol', recursive=False)
            if nested_ol:
                _parse_nav_ol(nested_ol, level + 1, toc_list, processed_hrefs, id_to_href_map)


# Вспомогательная функция для парсинга <navPoint> из NCX
def _parse_ncx_navpoint(nav_point, level, toc_list, processed_hrefs, id_to_href_map):
    if not nav_point or not hasattr(nav_point, 'find'):
        return

    nav_label = nav_point.find('navLabel')
    content = nav_point.find('content')

    if nav_label and content and content.get('src'):
        title = nav_label.find('text')
        title_text = title.get_text(strip=True) if title else "Без названия"
        href_full = content['src']

        # Пропускаем дубликаты href
        if href_full in processed_hrefs:
            # Но проверяем на вложенность
            for child_nav_point in nav_point.find_all('navPoint', recursive=False):
                _parse_ncx_navpoint(child_nav_point, level + 1, toc_list, processed_hrefs, id_to_href_map)
            return
        processed_hrefs.add(href_full)

        href_parts = href_full.split('#')
        file_href = href_parts[0]
        anchor = href_parts[1] if len(href_parts) > 1 else None

        section_id = None
        for item_id_map, item_href_map in id_to_href_map.items():
            if os.path.basename(item_href_map) == os.path.basename(file_href):
                section_id = item_id_map
                break

        if section_id:
            toc_list.append({
                'level': level, 'title': title_text, 'href': href_full,
                'id': section_id, 'anchor': anchor
            })
        else:
            print(f"Предупреждение (NCX): Не найден ID для src '{file_href}'. Ссылка '{title_text}' может не работать.")

        # Рекурсивно обрабатываем вложенные navPoint
        for child_nav_point in nav_point.find_all('navPoint', recursive=False):
            _parse_ncx_navpoint(child_nav_point, level + 1, toc_list, processed_hrefs, id_to_href_map)
    else:
        print(f"Предупреждение (NCX): Пропуск navPoint без navLabel/content@src: {nav_point.get('id', '')}")
        # Все равно обрабатываем детей
        for child_nav_point in nav_point.find_all('navPoint', recursive=False):
            _parse_ncx_navpoint(child_nav_point, level + 1, toc_list, processed_hrefs, id_to_href_map)


def extract_section_text(epub_filepath, section_id, toc_data=None):
    """
    Извлекает "чистый" текст из указанного раздела (по ID) EPUB файла.
    Если передан toc_data, ищет заголовки-ссылки в начале секции.
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

        # --- Извлечение текста с обработкой заголовков ---
        text_parts = []
        # Ищем тег body
        body = soup.find('body')
        if body:
            # Ищем заголовок-ссылку в начале секции, если передан TOC
            section_title_from_toc = None
            if toc_data:
                for toc_item in toc_data:
                    if toc_item.get('id') == section_id:
                        section_title_from_toc = toc_item.get('title')
                        break
            
            # Рекурсивно извлекаем текст с сохранением структуры
            def extract_text_recursive(element, level=0):
                """Рекурсивно извлекает текст с правильным форматированием"""
                from bs4 import NavigableString, Tag
                
                # Если это текстовый узел (NavigableString), просто возвращаем его текст
                if isinstance(element, NavigableString):
                    text = str(element).strip()
                    return [text] if text else []
                
                # Если это не Tag, пропускаем
                if not isinstance(element, Tag):
                    return []
                
                parts = []
                
                # Блочные элементы, которые должны создавать переносы строк
                block_elements = ['p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
                                'li', 'blockquote', 'pre', 'table', 'tr', 'td', 'th']
                
                # Элементы, которые должны создавать отступы (списки)
                list_elements = ['ul', 'ol', 'dl']
                
                # Элементы, которые мы игнорируем (но обрабатываем их содержимое)
                ignore_elements = ['span', 'em', 'strong', 'i', 'b', 'small', 'sub', 'sup']
                
                # Обрабатываем заголовки
                if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                    text = element.get_text(strip=True)
                    if text:
                        parts.append(f"**{text}**")
                    return parts
                
                # Обрабатываем списки
                elif element.name in ['li']:
                    text = element.get_text(' ', strip=True)
                    if text:
                        indent = "  " * level  # Отступ для вложенности
                        parts.append(f"{indent}• {text}")
                    return parts
                
                # Элементы переноса строки
                elif element.name in ['br']:
                    parts.append("")
                    return parts
                
                # Блочные элементы
                elif element.name in block_elements:
                    # Проверяем есть ли inline теги форматирования
                    has_inline_formatting = any(isinstance(child, Tag) and child.name in ['em', 'i', 'strong', 'b'] 
                                              for child in element.children)
                    
                    if has_inline_formatting:
                        # Обрабатываем как единый текст с сохранением inline форматирования
                        text_parts = []
                        for child in element.children:
                            if isinstance(child, NavigableString):
                                text_parts.append(str(child))
                            elif isinstance(child, Tag):
                                if child.name in ['em', 'i']:
                                    text_parts.append(f"*{child.get_text()}*")
                                elif child.name in ['strong', 'b']:
                                    text_parts.append(f"**{child.get_text()}**")
                                else:
                                    text_parts.append(child.get_text())
                        
                        combined_text = ''.join(text_parts).strip()
                        if combined_text:
                            parts.append(combined_text)
                    elif not any(isinstance(child, Tag) for child in element.children):
                        # Только текст без дочерних тегов
                        text = element.get_text(' ', strip=True)
                        if text:
                            parts.append(text)
                    else:
                        # Рекурсивно обрабатываем дочерние элементы (нет inline форматирования)
                        for child in element.children:
                            child_parts = extract_text_recursive(child, level + 1)
                            parts.extend(child_parts)
                    return parts
                
                # Списочные контейнеры
                elif element.name in list_elements:
                    for child in element.children:
                        if isinstance(child, Tag):
                            child_parts = extract_text_recursive(child, level)
                            parts.extend(child_parts)
                    return parts
                
                # Игнорируемые элементы - просто извлекаем текст
                elif element.name in ignore_elements:
                    text = element.get_text(' ', strip=True)
                    if text:
                        parts.append(text)
                    return parts
                
                # Для всех остальных элементов - рекурсивно обрабатываем содержимое
                else:
                    for child in element.children:
                        child_parts = extract_text_recursive(child, level)
                        parts.extend(child_parts)
                    return parts
            
            # Обрабатываем специальные заголовки-ссылки из TOC
            processed_first_link = False
            for element in body.find_all(recursive=False):
                # Проверяем первую ссылку на соответствие TOC
                if not processed_first_link and section_title_from_toc and element.find('a'):
                    first_link = element.find('a')
                    if first_link:
                        link_text = first_link.get_text(strip=True)
                        if link_text and (link_text == section_title_from_toc or 
                                        link_text.replace(' ', '') == section_title_from_toc.replace(' ', '') or
                                        section_title_from_toc in link_text or link_text in section_title_from_toc):
                            text_parts.append(f"**{link_text}**")
                            first_link.extract()  # Удаляем ссылку
                            processed_first_link = True
                
                # Извлекаем текст из элемента
                element_parts = extract_text_recursive(element)
                text_parts.extend(element_parts)
            
            # Если не удалось извлечь текст обычным способом
            if not text_parts and body.get_text(strip=True):
                print("Предупреждение: Не найдены элементы с текстом в body, извлекаем весь текст.")
                # Используем рекурсивную функцию для всего body
                text_parts = extract_text_recursive(body)
        else:
            print("Предупреждение: Тег body не найден, пытаемся извлечь текст из всего документа.")
            # Если нет body, извлекаем из всего документа
            text_parts = extract_text_recursive(soup) if soup else []

        # Объединяем части с правильными переносами строк
        extracted_text = "\n\n".join(part for part in text_parts if part.strip())

        # Очистка от лишних пробелов и пустых строк
        extracted_text = re.sub(r'\n{4,}', '\n\n\n', extracted_text)
        extracted_text = re.sub(r'[ \t]+', ' ', extracted_text)  # Убираем лишние пробелы
        extracted_text = extracted_text.strip()

        print(f"Извлечено ~{len(extracted_text)} символов текста.")
        return extracted_text if extracted_text else ""

    except Exception as e:
        print(f"ОШИБКА: Не удалось извлечь текст из раздела '{section_id}': {e}")
        import traceback
        traceback.print_exc()
        return ""


