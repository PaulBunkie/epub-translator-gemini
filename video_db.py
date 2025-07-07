# --- START OF FILE video_db.py ---
import sqlite3
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, List

from config import VIDEO_DB_FILE

VIDEO_DATABASE_FILE = str(VIDEO_DB_FILE)

def get_video_db_connection():
    """Создает соединение с БД видео."""
    conn = sqlite3.connect(VIDEO_DATABASE_FILE, check_same_thread=False, timeout=10) 
    conn.row_factory = sqlite3.Row
    return conn

def init_video_db():
    """
    Инициализирует базу данных видео: создает таблицы videos и analyses.
    Поддерживает расширение структуры в будущем.
    """
    conn = None
    try:
        conn = sqlite3.connect(VIDEO_DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # --- Создание таблицы videos ---
        print("[VideoDB] Checking/Creating 'videos' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                channel_title TEXT NOT NULL,
                duration INTEGER NOT NULL,
                views INTEGER NOT NULL,
                published_at TEXT NOT NULL,
                subscribers INTEGER NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # --- Создание таблицы analyses ---
        print("[VideoDB] Checking/Creating 'analyses' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                sharing_url TEXT,
                extracted_text TEXT,
                analysis_result TEXT,
                analysis_summary TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

        # --- Создание индексов для производительности ---
        print("[VideoDB] Creating indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_published_at ON videos(published_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_views ON videos(views)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_analyses_video_id ON analyses(video_id)")
        conn.commit()

        print("[VideoDB] Database initialization complete.")

    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Database initialization failed: {e}")
        raise
    finally:
        if conn:
            conn.close()

def add_video(video_data: Dict[str, Any]) -> Optional[int]:
    """
    Добавляет видео в БД.
    
    Args:
        video_data: Словарь с данными видео
        
    Returns:
        ID добавленного видео или None в случае ошибки
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO videos 
            (video_id, title, channel_title, duration, views, published_at, subscribers, url, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            video_data['video_id'],
            video_data['title'],
            video_data['channel_title'],
            video_data['duration'],
            video_data['views'],
            video_data['published_at'],
            video_data['subscribers'],
            video_data['url'],
            video_data.get('status', 'new')
        ))
        
        video_db_id = cursor.lastrowid
        conn.commit()
        print(f"[VideoDB] Video '{video_data['title']}' added/updated (DB ID: {video_db_id})")
        return video_db_id
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to add video: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_video_by_youtube_id(youtube_id: str) -> Optional[Dict[str, Any]]:
    """
    Получает видео по YouTube ID.
    
    Args:
        youtube_id: YouTube ID видео
        
    Returns:
        Словарь с данными видео или None
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, a.sharing_url, a.extracted_text, a.analysis_result, a.analysis_summary, a.error_message
            FROM videos v
            LEFT JOIN analyses a ON v.id = a.video_id
            WHERE v.video_id = ?
        """, (youtube_id,))
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get video: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_videos_by_status(status: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Получает видео по статусу.
    
    Args:
        status: Статус видео
        limit: Максимальное количество записей
        
    Returns:
        Список видео
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, a.sharing_url, a.extracted_text, a.analysis_result, a.analysis_summary, a.error_message
            FROM videos v
            LEFT JOIN analyses a ON v.id = a.video_id
            WHERE v.status = ?
            ORDER BY v.created_at DESC
            LIMIT ?
        """, (status, limit))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get videos by status: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_analyzed_videos(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Получает видео с готовым анализом.
    
    Args:
        limit: Максимальное количество записей
        
    Returns:
        Список проанализированных видео
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, a.sharing_url, a.extracted_text, a.analysis_result, a.analysis_summary, a.error_message
            FROM videos v
            INNER JOIN analyses a ON v.id = a.video_id
            WHERE v.status = 'analyzed' AND a.analysis_result IS NOT NULL
            ORDER BY a.created_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get analyzed videos: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_all_videos(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Получает все видео без фильтра по статусу.
    
    Args:
        limit: Максимальное количество записей
        
    Returns:
        Список всех видео
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT v.*, a.sharing_url, a.extracted_text, a.analysis_result, a.analysis_summary, a.error_message
            FROM videos v
            LEFT JOIN analyses a ON v.id = a.video_id
            ORDER BY v.created_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get all videos: {e}")
        return []
    finally:
        if conn:
            conn.close()

def update_video_status(video_id: int, status: str) -> bool:
    """
    Обновляет статус видео.
    
    Args:
        video_id: ID видео в БД
        status: Новый статус
        
    Returns:
        True в случае успеха, False в случае ошибки
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE videos 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, video_id))
        
        conn.commit()
        print(f"[VideoDB] Video {video_id} status updated to '{status}'")
        return True
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to update video status: {e}")
        return False
    finally:
        if conn:
            conn.close()

def save_analysis(video_id: int, analysis_data: Dict[str, Any]) -> bool:
    """
    Сохраняет результат анализа видео.
    
    Args:
        video_id: ID видео в БД
        analysis_data: Данные анализа
        
    Returns:
        True в случае успеха, False в случае ошибки
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        # Удаляем существующий анализ, если есть
        cursor.execute("DELETE FROM analyses WHERE video_id = ?", (video_id,))
        
        # Добавляем новый анализ
        cursor.execute("""
            INSERT INTO analyses 
            (video_id, sharing_url, extracted_text, analysis_result, analysis_summary, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            video_id,
            analysis_data.get('sharing_url'),
            analysis_data.get('extracted_text'),
            analysis_data.get('analysis_result') or analysis_data.get('analysis'),
            analysis_data.get('analysis_summary'),
            analysis_data.get('error_message')
        ))
        
        # Обновляем статус видео
        status = 'analyzed' if analysis_data.get('analysis_result') or analysis_data.get('analysis') else 'error'
        cursor.execute("""
            UPDATE videos 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status, video_id))
        
        conn.commit()
        print(f"[VideoDB] Analysis saved for video {video_id}")
        return True
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to save analysis: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_next_unprocessed_video() -> Optional[Dict[str, Any]]:
    """
    Получает следующее необработанное видео.
    
    Returns:
        Данные видео или None, если нет необработанных
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM videos 
            WHERE status = 'new'
            ORDER BY created_at ASC
            LIMIT 1
        """)
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get next unprocessed video: {e}")
        return None
    finally:
        if conn:
            conn.close()

def reset_stuck_videos(minutes_threshold: int = 30) -> int:
    """
    Сбрасывает статус видео со статусом 'processing', которые зависли более указанного времени.
    Это нужно для обработки случаев, когда сервер перезапустился во время анализа.
    
    Args:
        minutes_threshold: Минимальное время в минутах, после которого видео считается зависшим
        
    Returns:
        Количество сброшенных видео
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        # Находим видео со статусом 'processing', которые обновлялись более указанного времени назад
        cursor.execute("""
            SELECT COUNT(*) FROM videos 
            WHERE status = 'processing' 
            AND updated_at < datetime('now', '-{} minutes')
        """.format(minutes_threshold))
        
        stuck_count = cursor.fetchone()[0]
        
        if stuck_count > 0:
            # Сбрасываем статус на 'new'
            cursor.execute("""
                UPDATE videos 
                SET status = 'new', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing' 
                AND updated_at < datetime('now', '-{} minutes')
            """.format(minutes_threshold))
            
            conn.commit()
            print(f"[VideoDB] Сброшено {stuck_count} зависших видео (старше {minutes_threshold} мин) со статуса 'processing' в 'new'")
            return stuck_count
        else:
                    print(f"[VideoDB] Зависших видео (старше {minutes_threshold} мин) не найдено")
        return 0
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to reset stuck videos: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def reset_error_videos() -> int:
    """
    Сбрасывает статус видео со статусом 'error' обратно в 'new' для повторного анализа.
    
    Returns:
        Количество сброшенных видео
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        # Находим количество видео со статусом 'error'
        cursor.execute("SELECT COUNT(*) FROM videos WHERE status = 'error'")
        error_count = cursor.fetchone()[0]
        
        if error_count > 0:
            # Сбрасываем статус на 'new' и удаляем старые анализы
            cursor.execute("""
                UPDATE videos 
                SET status = 'new', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'error'
            """)
            
            # Удаляем связанные анализы для видео со статусом 'error'
            cursor.execute("""
                DELETE FROM analyses 
                WHERE video_id IN (
                    SELECT id FROM videos WHERE status = 'new'
                )
            """)
            
            conn.commit()
            print(f"[VideoDB] Сброшено {error_count} видео со статуса 'error' в 'new' для повторного анализа")
            return error_count
        else:
            print("[VideoDB] Видео со статусом 'error' не найдено")
            return 0
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to reset error videos: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def cleanup_old_videos(days: int = 30) -> int:
    """
    Удаляет старые видео и их анализы.
    
    Args:
        days: Количество дней для хранения
        
    Returns:
        Количество удаленных записей
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        # Удаляем видео старше указанного количества дней
        cursor.execute("""
            DELETE FROM videos 
            WHERE created_at < datetime('now', '-{} days')
        """.format(days))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        print(f"[VideoDB] Cleaned up {deleted_count} old videos")
        return deleted_count
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to cleanup old videos: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def get_video_stats() -> Dict[str, Any]:
    """
    Получает статистику по видео.
    
    Returns:
        Словарь со статистикой
    """
    conn = None
    try:
        conn = get_video_db_connection()
        cursor = conn.cursor()
        
        # Общее количество видео
        cursor.execute("SELECT COUNT(*) FROM videos")
        total_videos = cursor.fetchone()[0]
        
        # Количество по статусам
        cursor.execute("""
            SELECT status, COUNT(*) as count 
            FROM videos 
            GROUP BY status
        """)
        status_counts = dict(cursor.fetchall())
        
        # Количество проанализированных видео
        cursor.execute("""
            SELECT COUNT(*) FROM videos v
            INNER JOIN analyses a ON v.id = a.video_id
            WHERE a.analysis_result IS NOT NULL
        """)
        analyzed_count = cursor.fetchone()[0]
        
        # Последнее обновление
        cursor.execute("SELECT MAX(updated_at) FROM videos")
        last_update = cursor.fetchone()[0]
        
        return {
            'total': total_videos,
            'analyzed': analyzed_count,
            'processing': status_counts.get('processing', 0),
            'error': status_counts.get('error', 0),
            'new': status_counts.get('new', 0),
            'status_counts': status_counts,
            'last_update': last_update
        }
        
    except sqlite3.Error as e:
        print(f"[VideoDB ERROR] Failed to get video stats: {e}")
        return {
            'total': 0,
            'analyzed': 0,
            'processing': 0,
            'error': 0,
            'new': 0,
            'status_counts': {},
            'last_update': None
        }
    finally:
        if conn:
            conn.close()

# --- END OF FILE video_db.py --- 