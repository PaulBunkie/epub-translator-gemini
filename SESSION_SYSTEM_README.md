# Система сессий пользователей для EPUB Translator

## Обзор

Система сессий позволяет пользователям закрывать страницу и возвращаться позже, автоматически видя свою книгу без необходимости сохранять ссылку.

## Как это работает

### 1. Создание сессии
- При загрузке EPUB файла автоматически создается сессия пользователя
- Сессия сохраняется в cookie браузера с именем `user_session`
- Время жизни сессии: 24 часа

### 2. Автоматическое перенаправление
- При переходе на `/translate` система проверяет наличие активной сессии
- Если сессия найдена и не истекла, пользователь автоматически перенаправляется на свою книгу
- Если сессии нет, показывается форма загрузки

### 3. Безопасность
- Сессии хранятся в базе данных с привязкой к конкретной книге
- Cookie защищен от XSS атак (`httponly=True`)
- Поддержка CSRF защиты (`samesite='Lax'`)
- Автоматическая очистка истекших сессий каждые 6 часов

## Структура базы данных

### Таблица `user_sessions`
```sql
CREATE TABLE user_sessions (
    session_id TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_accessed DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (access_token) REFERENCES books(access_token) ON DELETE CASCADE
);
```

## API функции

### `create_user_session(access_token, session_duration_hours=24)`
Создает новую сессию для пользователя.

### `get_session_access_token(session_id)`
Получает access_token по session_id, если сессия активна.

### `delete_expired_sessions()`
Удаляет истекшие сессии.

### `delete_user_session(session_id)`
Удаляет конкретную сессию пользователя.

## Пользовательский интерфейс

### Главная страница `/translate`
- Проверяет наличие активной сессии
- Автоматически перенаправляет на книгу пользователя
- Показывает форму загрузки, если сессии нет

### Страница книги `/translate/<access_token>`
- Поддерживает как прямой доступ по токену, так и через сессию
- Автоматически создает сессию при первом посещении
- Показывает информацию о сохраненной сессии

### Информация для пользователя
- Уведомление о том, что сессия сохранена в браузере
- Инструкция о том, как вернуться к книге позже
- Постоянная ссылка как резервный вариант

## Преимущества

1. **Удобство использования**: Пользователи могут закрыть страницу и вернуться позже
2. **Безопасность**: Сессии привязаны к конкретным книгам и имеют ограниченное время жизни
3. **Автоматизация**: Не требует действий от пользователя
4. **Резервный вариант**: Постоянные ссылки остаются доступными

## Технические детали

### Cookie настройки
```python
response.set_cookie(
    'user_session', 
    session_id, 
    max_age=24*60*60,  # 24 часа
    httponly=True,     # Защита от XSS
    secure=False,      # False для HTTP, True для HTTPS
    samesite='Lax'     # Защита от CSRF
)
```

### Автоматическая очистка
Задача в планировщике APScheduler:
```python
scheduler.add_job(
    workflow_db_manager.delete_expired_sessions,
    trigger='interval',
    hours=6,  # Очистка каждые 6 часов
    id='cleanup_expired_sessions_job'
)
```

## Использование

1. Пользователь загружает EPUB файл
2. Система создает сессию и сохраняет её в cookie
3. Пользователь может закрыть страницу
4. При возврате на `/translate` система автоматически показывает его книгу
5. Сессия автоматически истекает через 24 часа
6. Истекшие сессии удаляются каждые 6 часов 