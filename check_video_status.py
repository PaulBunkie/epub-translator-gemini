#!/usr/bin/env python3
"""
Скрипт для проверки статусов видео в БД
"""

import video_db

def check_video_statuses():
    """Проверяет статусы видео и их анализы"""
    
    print("=== Проверка статусов видео ===")
    
    # Инициализируем БД
    video_db.init_video_db()
    
    # Получаем статистику
    stats = video_db.get_video_stats()
    print(f"Статистика: {stats}")
    
    # Проверяем все видео
    all_videos = video_db.get_all_videos(limit=100)
    print(f"\nВсего видео: {len(all_videos)}")
    
    # Группируем по статусам
    status_groups = {}
    for video in all_videos:
        status = video.get('status', 'unknown')
        if status not in status_groups:
            status_groups[status] = []
        status_groups[status].append(video)
    
    # Анализируем каждую группу
    for status, videos in status_groups.items():
        print(f"\n--- Статус: {status} ({len(videos)} видео) ---")
        
        for video in videos:
            video_id = video.get('id')
            title = video.get('title', 'Без названия')[:50]
            analysis_result = video.get('analysis_result')
            analysis_summary = video.get('analysis_summary')
            
            print(f"ID: {video_id}, Название: {title}")
            print(f"  Полный анализ: {'Есть' if analysis_result else 'Нет'}")
            print(f"  Краткая версия: {'Есть' if analysis_summary else 'Нет'}")
            
            if analysis_result and not analysis_summary:
                print(f"  ⚠️  ПРОБЛЕМА: Есть анализ, но нет краткой версии!")
            
            if not analysis_result and status == 'analyzed':
                print(f"  ⚠️  ПРОБЛЕМА: Статус 'analyzed', но нет анализа!")

if __name__ == "__main__":
    check_video_statuses() 