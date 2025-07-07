#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы видео-модулей
"""

import os
import sys
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

def test_imports():
    """Тестирует импорт всех модулей"""
    print("🔍 Тестирование импортов...")
    
    try:
        import video_db
        print("✅ video_db импортируется")
    except Exception as e:
        print(f"❌ Ошибка импорта video_db: {e}")
        return False
    
    try:
        import video_analyzer
        print("✅ video_analyzer импортируется")
    except Exception as e:
        print(f"❌ Ошибка импорта video_analyzer: {e}")
        return False
    
    try:
        import toptube10
        print("✅ toptube10 импортируется")
    except Exception as e:
        print(f"❌ Ошибка импорта toptube10: {e}")
        return False
    
    return True

def test_database():
    """Тестирует инициализацию базы данных"""
    print("\n🗄️ Тестирование базы данных...")
    
    try:
        import video_db
        video_db.init_video_db()
        print("✅ База данных инициализирована")
        return True
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        return False

def test_analyzer():
    """Тестирует инициализацию анализатора"""
    print("\n🤖 Тестирование анализатора...")
    
    try:
        from video_analyzer import VideoAnalyzer
        
        # Проверяем наличие необходимых переменных окружения
        yandex_token = os.getenv("YANDEX_API_TOKEN")
        yandex_session = os.getenv("YANDEX_SESSION_ID")
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        
        print(f"Yandex API Token: {'✅' if yandex_token else '❌'}")
        print(f"Yandex Session ID: {'✅' if yandex_session else '❌'}")
        print(f"OpenRouter API Key: {'✅' if openrouter_key else '❌'}")
        
        if not yandex_token and not yandex_session:
            print("⚠️ Предупреждение: Не установлены Yandex API переменные")
        
        if not openrouter_key:
            print("⚠️ Предупреждение: Не установлен OpenRouter API ключ")
        
        # Пытаемся создать анализатор
        analyzer = VideoAnalyzer()
        print("✅ VideoAnalyzer создан успешно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка создания анализатора: {e}")
        return False

def test_toptube_manager():
    """Тестирует создание менеджера TopTube"""
    print("\n📺 Тестирование TopTube менеджера...")
    
    try:
        youtube_api_key = os.getenv("YOUTUBE_API_KEY")
        print(f"YouTube API Key: {'✅' if youtube_api_key else '❌'}")
        
        if not youtube_api_key:
            print("⚠️ Предупреждение: Не установлен YouTube API ключ")
            return False
        
        import toptube10
        manager = toptube10.get_manager()
        print("✅ TopTube менеджер создан успешно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка создания TopTube менеджера: {e}")
        return False

def test_scheduler_tasks():
    """Тестирует функции планировщика"""
    print("\n⏰ Тестирование задач планировщика...")
    
    try:
        import toptube10
        
        # Проверяем, что функции существуют
        assert hasattr(toptube10, 'collect_videos_task')
        assert hasattr(toptube10, 'analyze_next_video_task')
        assert hasattr(toptube10, 'cleanup_videos_task')
        
        print("✅ Все задачи планировщика найдены")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка проверки задач планировщика: {e}")
        return False

def main():
    """Основная функция тестирования"""
    print("🚀 Запуск тестов видео-модулей\n")
    
    tests = [
        test_imports,
        test_database,
        test_analyzer,
        test_toptube_manager,
        test_scheduler_tasks
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print(f"📊 Результаты: {passed}/{total} тестов прошли успешно")
    
    if passed == total:
        print("🎉 Все тесты прошли! Модули готовы к работе.")
        return True
    else:
        print("⚠️ Некоторые тесты не прошли. Проверьте настройки.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 