#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тест синхронизации лиг: запрашивает актуальный список из API
и сравнивает с тем, что есть в БД.
"""
import sys
import json
from football import get_manager

def main():
    print("=" * 70)
    print("ТЕСТ СИНХРОНИЗАЦИИ ФУТБОЛЬНЫХ ЛИГ")
    print("=" * 70)
    
    try:
        manager = get_manager()
        print("\n[1/2] Менеджер инициализирован")
        
        print("\n[2/2] Запрашиваем список лиг из API...")
        result = manager.sync_leagues()
        
        print("\n" + "=" * 70)
        print("РЕЗУЛЬТАТЫ СИНХРОНИЗАЦИИ:")
        print(f"  Всего лиг в API:      {result['total']}")
        print(f"  Активных лиг:         {result['active']}")
        print(f"  Обновлено в БД:       {result['updated']}")
        print(f"  Добавлено новых:      {result['new']}")
        
        if result['new_leagues']:
            print(f"\n  ⚠️ НОВЫЕ ЛИГИ (не были в статическом списке):")
            for nl in result['new_leagues']:
                status = "активна" if nl['active'] else "неактивна"
                print(f"    ➡️ {nl['key']} ({nl['title']}) - {status}")
        else:
            print(f"\n  ✅ Новых лиг не обнаружено.")
        
        print("=" * 70)
        print("Готово!")
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())