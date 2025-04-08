# EPUB Translator 📚 <=> 🌐

Простое веб-приложение для перевода книг в формате EPUB с использованием Google Gemini API. Загрузите свою книгу, выберите язык и модель, переводите по главам или все сразу, а затем скачайте результат в виде текста или нового EPUB файла.

## ✨ Возможности

*   **Загрузка EPUB:** Простой интерфейс для загрузки файлов `.epub`.
*   **Автоматический парсинг:** Извлечение структуры книги и оглавления (TOC).
*   **Перевод оглавления:** Автоматический перевод названий глав в TOC при загрузке книги.
*   **Выбор модели и языка:** Возможность выбрать целевой язык перевода и конкретную модель Google Gemini (список загружается динамически).
*   **Фоновый перевод:** Перевод выполняется в фоновом режиме, не блокируя интерфейс.
    *   Перевод отдельных секций (глав) по клику.
    *   Запуск перевода всех еще не переведенных секций одной кнопкой.
*   **Обновление статуса:** Отображение статуса перевода для каждой секции (не переведено, в процессе, переведено, ошибка) с обновлением в реальном времени (через поллинг).
*   **Просмотр перевода:** Отображение переведенного текста выбранной секции прямо в браузере.
*   **Кэширование:** Переведенные секции кэшируются на диске, чтобы избежать повторных запросов к API и ускорить загрузку.
*   **Скачивание результатов:**
    *   Скачивание переведенного текста отдельной секции в формате `.txt`.
    *   Скачивание всего переведенного текста книги (всех секций) в одном файле `.txt`.
    *   Скачивание переведенной книги в виде нового, упрощенного файла `.epub`.
*   **Управление книгами:** Удаление загруженных книг и их кэша с диска.

## 🚀 Технологический стек

*   **Бэкенд:** Python 3, Flask
*   **API Перевода:** Google Gemini API (`google-generativeai`)
*   **Парсинг и генерация EPUB:** `ebooklib`
*   **Фронтенд:** HTML, CSS, JavaScript (без фреймворков)
*   **Асинхронность:** `concurrent.futures.ThreadPoolExecutor` для фоновых задач.

## 📋 Предварительные требования

*   Python 3 (рекомендуется 3.9+)
*   `pip` (менеджер пакетов Python)
*   **Google API Key:** Активный ключ с доступом к Google Gemini API (Vertex AI или AI Studio). [Как получить ключ](https://aistudio.google.com/app/apikey)

## ⚙️ Установка и настройка

1.  **Клонируйте репозиторий:**
    ```bash
    git clone <URL_вашего_репозитория>
    cd <папка_репозитория>
    ```

2.  **Создайте и активируйте виртуальное окружение** (рекомендуется):
    ```bash
    python -m venv venv
    # Linux/macOS
    source venv/bin/activate
    # Windows
    .\venv\Scripts\activate
    ```

3.  **Установите зависимости:**
    *   **Создайте файл `requirements.txt`** в корне проекта со следующим содержимым:
        ```txt
        Flask>=2.0
        ebooklib>=0.18
        google-generativeai>=0.3
        beautifulsoup4>=4.0
        lxml>=4.0 # Часто является зависимостью bs4/ebooklib, но лучше указать явно
        epubgen>=0.6 # Или актуальная версия
        werkzeug>=2.0 # Часто зависимость Flask
        requests # Может понадобиться для будущих расширений или для Gemini
        ```
    *   **Установите пакеты:**
        ```bash
        pip install -r requirements.txt
        ```

4.  **Настройте API ключ:**
    *   **ВАЖНО:** Никогда не добавляйте ваш API ключ напрямую в код или в систему контроля версий!
    *   **Рекомендуемый способ:** Установите переменную окружения `GOOGLE_API_KEY`.
        *   **Linux/macOS:**
            ```bash
            export GOOGLE_API_KEY='ВАШ_API_КЛЮЧ'
            ```
            (Добавьте эту строку в `~/.bashrc` или `~/.zshrc` для постоянной установки)
        *   **Windows (Command Prompt):**
            ```cmd
            set GOOGLE_API_KEY=ВАШ_API_КЛЮЧ
            ```
        *   **Windows (PowerShell):**
            ```powershell
            $env:GOOGLE_API_KEY="ВАШ_API_КЛЮЧ"
            ```
        *   **Альтернатива:** Использовать файл `.env` и библиотеку `python-dotenv`. Создайте файл `.env` в корне проекта:
            ```env
            GOOGLE_API_KEY=ВАШ_API_КЛЮЧ
            ```
            Затем установите `python-dotenv` (`pip install python-dotenv`) и добавьте в начало `app.py`:
            ```python
            from dotenv import load_dotenv
            load_dotenv()
            ```

## ▶️ Запуск приложения

1.  Убедитесь, что ваше виртуальное окружение активировано и переменная `GOOGLE_API_KEY` установлена.
2.  Запустите Flask приложение:
    ```bash
    python app.py
    ```
3.  Откройте веб-браузер и перейдите по адресу `http://127.0.0.1:5000` (или адресу, указанному в консоли, если используется `host='0.0.0.0'`).

## 📖 Использование

1.  **Загрузка:** На главной странице нажмите "Выберите файл", выберите ваш `.epub` файл, выберите язык перевода по умолчанию и нажмите "Загрузить". Подождите, пока книга обработается (включая перевод оглавления).
2.  **Просмотр/Перевод:** На главной странице нажмите кнопку "Просмотр/Перевод" рядом с нужной книгой.
3.  **Настройка:** На странице книги выберите модель Gemini и целевой язык (если нужно изменить язык по умолчанию).
4.  **Перевод:**
    *   Нажмите на название главы в оглавлении слева. Если перевод уже есть в кэше, он отобразится справа. Если нет, запустится фоновый перевод.
    *   Нажмите кнопку "Перевести все непроведенные", чтобы запустить перевод всех глав без готового перевода или с ошибками.
5.  **Мониторинг:** Следите за статусом перевода секций в оглавлении. Статусы обновляются автоматически.
6.  **Просмотр:** Кликните на переведенную главу, чтобы увидеть ее текст справа.
7.  **Скачивание:**
    *   Нажмите иконку `💾` рядом с переведенной главой, чтобы скачать ее текст.
    *   Когда вся книга переведена (статус "Complete" или "Complete with errors"), станут активны кнопки "Скачать весь текст (.txt)" и "Скачать EPUB".
8.  **Удаление:** На главной странице нажмите кнопку "Удалить" рядом с книгой, чтобы удалить ее файл и весь связанный кэш.

## 📁 Структура проекта
.
├── .epub_cache/ # Директория для кэшированных переводов секций
├── .translated/ # (Опционально) Директория для полных переведенных файлов (если используется)
├── static/ # Статические файлы (CSS, JS)
│ └── js/
│ └── main.js # Логика фронтенда
├── templates/ # HTML шаблоны Flask
│ ├── index.html # Главная страница (загрузка, список книг)
│ └── book_view.html # Страница просмотра/перевода книги
├── uploads/ # Директория для загруженных EPUB файлов
├── app.py # Основной файл приложения Flask (маршруты, логика)
├── cache_manager.py # Функции для работы с кэшем
├── epub_creator.py # Функции для генерации нового EPUB файла (с epubgen)
├── epub_parser.py # Функции для парсинга EPUB (структура, текст, TOC)
├── translation_module.py # Функции для взаимодействия с Gemini API
├── requirements.txt # Список зависимостей Python
└── README.md # Этот файл


## ⚠️ Ограничения и Предупреждения

*   **Состояние в памяти:** Текущая информация о прогрессе перевода (`book_progress`) хранится в памяти Python. Перезапуск сервера Flask приведет к потере этого состояния (хотя кэш на диске останется). Для более надежной работы требуется персистентное хранилище (БД или файлы состояния).
*   **Стоимость API:** Использование Google Gemini API может быть платным в зависимости от объема использования и выбранной модели. Следите за своей квотой и расходами в Google Cloud Console или AI Studio.
*   **Качество перевода:** Качество литературного перевода сильно зависит от выбранной модели Gemini и сложности исходного текста.
*   **Обработка ошибок:** Текущая обработка ошибок покрывает основные сценарии, но может быть улучшена.

## 📄 Лицензия

(Вам нужно выбрать лицензию для вашего проекта, например, MIT, Apache 2.0, и добавить файл `LICENSE` в репозиторий).

---

Этот проект предоставляет удобный способ для перевода EPUB книг. Надеюсь, он будет вам полезен!