#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки сбора футбольных матчей.

TODO: УДАЛИТЬ ПОСЛЕ ОТЛАДКИ - временный файл для тестирования
"""
import sys
from football import get_manager

def test_collect_matches():
    """Тестирует сбор матчей на завтра."""
    try:
        print("=" * 60)
        print("Тест сбора футбольных матчей")
        print("=" * 60)
        
        # Получаем менеджер
        manager = get_manager()
        print("\n[TEST] Менеджер инициализирован успешно")
        
        # Собираем матчи
        print("\n[TEST] Начинаем сбор матчей на завтра...")
        count = manager.collect_tomorrow_matches()
        
        print("\n" + "=" * 60)
        print(f"[TEST] Результат: собрано {count} матчей")
        print("=" * 60)
        
        return count
        
    except Exception as e:
        print(f"\n[TEST ERROR] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 0

if __name__ == "__main__":
    count = test_collect_matches()
    sys.exit(0 if count >= 0 else 1)
