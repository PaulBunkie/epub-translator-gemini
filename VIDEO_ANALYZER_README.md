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

# Опционально: Telegram уведомления об ошибках
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
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

## Telegram уведомления

Система поддерживает автоматические уведомления в Telegram о критических ошибках:

### Настройка Telegram бота

1. Создайте бота через [@BotFather](https://t.me/botfather) в Telegram
2. Получите токен бота и установите переменную `TELEGRAM_BOT_TOKEN`
3. Найдите ваш `chat_id`:
   - Автоматически: `python get_chat_id.py` (отправьте боту сообщение сначала)
   - Вручную: используйте [@userinfobot](https://t.me/userinfobot)
4. Установите переменную `TELEGRAM_CHAT_ID`

### Типы уведомлений

- **Ошибки токенов Yandex API**: Уведомления об истечении или недействительности токенов
- **Ошибки сессий**: Уведомления об истечении Session_id
- **Общие ошибки API**: Уведомления о других критических ошибках

### Тестирование уведомлений

```bash
python test_telegram.py
```

Этот скрипт проверит подключение к Telegram и отправит тестовые уведомления.

### Интерактивный бот

Запустите интерактивный бот для управления системой:

```bash
python run_telegram_bot.py
```

#### Доступные команды:

**🔍 Мониторинг:**
- `/status` - Статус всех компонентов системы
- `/system_info` - Информация о сервере, памяти, диске

**🗄️ Кэш:**
- `/cache_info` - Статистика кэша
- `/clear_cache` - Очистка всех кэшей

**🔧 Тестирование:**
- `/test_yandex` - Проверка Yandex API
- `/logs [количество]` - Последние логи

**⚡ Управление:**
- `/restart` - Инструкции по перезапуску 