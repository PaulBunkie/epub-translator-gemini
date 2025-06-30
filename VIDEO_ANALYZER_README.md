# Video Analyzer

Система для анализа видео через Yandex 300.ya.ru API и OpenRouter AI.

## Возможности

- Анализ видео с YouTube, VK и других платформ
- Получение краткого содержания через Yandex 300.ya.ru
- Детальный AI-анализ через OpenRouter
- Гибридный подход к авторизации: сначала официальный API, потом fallback

## Установка

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
cd epub-translator-gemini
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Настройте переменные окружения в файле `.env`:

```env
# Обязательно: OpenRouter API ключ
OPENROUTER_API_KEY=your_openrouter_api_key

# Один из двух способов авторизации в Yandex:
# Способ 1: Официальный OAuth токен (приоритет)
YANDEX_API_TOKEN=your_yandex_oauth_token

# Способ 2: Session_id из cookies (fallback)
YANDEX_SESSION_ID=your_session_id_cookie
```

## Гибридная авторизация

Система использует двухуровневый подход к авторизации в Yandex API:

1. **Официальный API** (приоритет): Использует OAuth токен `YANDEX_API_TOKEN`
2. **Fallback через сессию**: Если официальный API недоступен, использует `YANDEX_SESSION_ID`

### Получение YANDEX_API_TOKEN

1. Зарегистрируйте приложение в [Yandex OAuth](https://oauth.yandex.ru/)
2. Получите OAuth токен для приложения
3. Установите переменную `YANDEX_API_TOKEN`

### Получение YANDEX_SESSION_ID (fallback)

1. Войдите в аккаунт на [300.ya.ru](https://300.ya.ru/)
2. Откройте DevTools (F12) → Application/Storage → Cookies
3. Найдите cookie `Session_id` и скопируйте его значение
4. Установите переменную `YANDEX_SESSION_ID`

## Использование

### API Endpoint

```
POST /analyze-video
Content-Type: application/json

{
    "video_url": "https://www.youtube.com/watch?v=example"
}
```

### Пример ответа

```json
{
    "video_url": "https://www.youtube.com/watch?v=example",
    "sharing_url": "https://300.ya.ru/v_abc123",
    "extracted_text": "Краткое содержание видео...",
    "analysis": "Детальный AI-анализ содержания...",
    "error": null
}
```

### Запуск сервера

```bash
python app.py
```

Сервер будет доступен по адресу `http://localhost:5000`

## Поддерживаемые платформы

- YouTube
- VK
- Другие платформы, поддерживаемые 300.ya.ru

## Логирование

Система ведет подробные логи процесса анализа:
- Попытки авторизации через разные методы
- Статусы API запросов
- Результаты извлечения текста
- Ошибки и их причины

## Устранение неполадок

### Ошибки авторизации

- **403 Forbidden**: Проверьте правильность токена/сессии
- **401 Unauthorized**: Токен или сессия истекли
- **404 Not Found**: URL не поддерживается API

### Обновление сессии

Session_id имеет ограниченный срок действия. При ошибках авторизации:
1. Повторно войдите в аккаунт на 300.ya.ru
2. Обновите значение `YANDEX_SESSION_ID`

### Автоматическое обновление сессии

Для автоматического обновления сессии можно использовать:
- Selenium WebDriver для автоматического входа
- OAuth flow для получения новых токенов
- Периодическое обновление через cron/планировщик 