# Внутри файла epub_parser.py

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import re

def get_epub_structure(epub_filepath):
    """
    Читает EPUB файл и возвращает упорядоченный список идентификаторов
    текстовых разделов (XHTML/HTML файлов из spine).
    """
    if not os.path.exists(epub_filepath):
        print(f"ОШИБКА: Файл EPUB не найден: {epub_filepath}")
        return None

    try:
        print(f"Чтение структуры EPUB: {epub_filepath}")
        book = epub.read_epub(epub_filepath)
        spine_items = book.spine
        section_ids = []
        # Используем href как более надежный идентификатор, если ID нет
        for item_tuple in spine_items:
             # item_tuple может быть ('id', 'linear') или просто 'id'
             item_id = item_tuple[0] if isinstance(item_tuple, tuple) else item_tuple
             epub_item = book.get_item_with_id(item_id)
             if epub_item:
                 # Убедимся, что это текстовый документ
                 if epub_item.get_type() == ebooklib.ITEM_DOCUMENT:
                      # Добавляем ID, если он есть и уникален, иначе пробуем href
                      item_identifier = epub_item.id if hasattr(epub_item, 'id') and epub_item.id else epub_item.get_name()
                      if item_identifier not in section_ids:
                           section_ids.append(item_identifier)
                      else:
                           # Если ID уже есть, это может быть дубликат или проблема со spine
                           print(f"Предупреждение: Повторный идентификатор '{item_identifier}' в spine.")
                 else:
                     print(f"Предупреждение: Элемент spine '{item_id}' не является документом, пропускаем.")
             else:
                 print(f"Предупреждение: Не найден элемент с ID '{item_id}' в манифесте, указанный в spine.")


        # Альтернативный (менее надежный) способ, если spine пуст или содержит не ID
        if not section_ids:
             print("Предупреждение: Не удалось получить разделы из spine по ID, пытаемся получить все документы...")
             items = book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
              # Используем get_name() как запасной вариант, если ID нет
             section_ids = [item.id if hasattr(item, 'id') and item.id else item.get_name() for item in items]


        print(f"Найдено разделов (секций) в EPUB: {len(section_ids)}")
        return section_ids
    except Exception as e:
        print(f"ОШИБКА: Не удалось прочитать структуру EPUB: {e}")
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