#!/usr/bin/env python3
"""
Тестовый скрипт для проверки генерации краткой версии анализа
"""

import os
import sys
from video_analyzer import VideoAnalyzer
from video_db import init_video_db, save_analysis

def test_analysis_summary():
    """Тестирует генерацию краткой версии анализа"""
    
    print("=== Тест генерации краткой версии анализа ===")
    
    # Инициализируем БД
    print("1. Инициализация БД...")
    init_video_db()
    
    # Создаем анализатор
    print("2. Создание анализатора...")
    try:
        analyzer = VideoAnalyzer()
        print("✅ Анализатор создан успешно")
    except Exception as e:
        print(f"❌ Ошибка создания анализатора: {e}")
        return False
    
    # Тестовый анализ (используем короткое видео)
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Roll для теста
    
    print(f"3. Тестирование анализа видео: {test_url}")
    
    try:
        result = analyzer.analyze_video(test_url)
        
        if result.get('error'):
            print(f"❌ Ошибка анализа: {result['error']}")
            return False
        
        if result.get('analysis'):
            print("✅ Полный анализ получен")
            print(f"Длина анализа: {len(result['analysis'])} символов")
            
            if result.get('analysis_summary'):
                print("✅ Краткая версия сгенерирована")
                print(f"Краткая версия: {result['analysis_summary']}")
                
                # Сохраняем в БД для проверки
                print("4. Сохранение в БД...")
                video_data = {
                    'video_id': 'test_video_123',
                    'title': 'Тестовое видео',
                    'channel_title': 'Тестовый канал',
                    'duration': 180,
                    'views': 1000,
                    'published_at': '2024-01-01',
                    'subscribers': 5000,
                    'url': test_url,
                    'status': 'new'
                }
                
                from video_db import add_video
                video_id = add_video(video_data)
                
                if video_id:
                    analysis_data = {
                        'sharing_url': result.get('sharing_url'),
                        'extracted_text': result.get('extracted_text'),
                        'analysis_result': result.get('analysis'),
                        'analysis_summary': result.get('analysis_summary'),
                        'error_message': result.get('error')
                    }
                    
                    if save_analysis(video_id, analysis_data):
                        print("✅ Анализ сохранен в БД")
                        return True
                    else:
                        print("❌ Ошибка сохранения в БД")
                        return False
                else:
                    print("❌ Ошибка добавления видео в БД")
                    return False
            else:
                print("❌ Краткая версия не сгенерирована")
                return False
        else:
            print("❌ Анализ не получен")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        return False

if __name__ == "__main__":
    success = test_analysis_summary()
    if success:
        print("\n🎉 Тест прошел успешно!")
    else:
        print("\n💥 Тест не прошел!")
        sys.exit(1) 