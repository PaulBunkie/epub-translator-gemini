/**
 * EPUB Translator Frontend Logic
 * Handles UI interactions, API calls, and status updates.
 */
document.addEventListener('DOMContentLoaded', () => {
    // --- Получение основных элементов DOM ---
    const tocList = document.getElementById('toc-list');
    const translateAllBtn = document.getElementById('translate-all-btn');
    const downloadFullLink = document.getElementById('download-full-link');
    const downloadFullBtn = document.getElementById('download-full-btn');
    const modelSelect = document.getElementById('model-select');
    const languageSelect = document.getElementById('language-select');

    const translationDisplay = document.getElementById('translation-display');
    const translationSectionIdSpan = document.getElementById('translation-section-id');
    const translationContentDiv = document.getElementById('translation-content');

    // Проверяем наличие book_id (передается из шаблона)
    // Если его нет, скрипт не должен пытаться работать с книгой
    if (typeof currentBookId === 'undefined' || !currentBookId) {
        console.log("Book ID not found. Stopping JS execution for book specific actions.");
        // Можно скрыть/заблокировать элементы управления здесь, если они не должны быть видны без книги
        if(translateAllBtn) translateAllBtn.disabled = true;
        if(downloadFullBtn) downloadFullBtn.disabled = true;
        if(downloadFullLink) downloadFullLink.classList.add('hidden');
        if(modelSelect) modelSelect.disabled = true;
        if(languageSelect) languageSelect.disabled = true;
        return; // Прекращаем выполнение остального скрипта
    }

    // --- Глобальные переменные состояния ---
    let pollInterval;
    let currentPolling = false;
    let activeSectionId = null; // ID секции (файла), отображаемой в данный момент

    // --- Функции обновления UI ---

    /**
     * Обновляет визуальный статус для одного или всех элементов TOC,
     * относящихся к указанному sectionId.
     * @param {string} sectionId - ID секции (файла).
     * @param {string} newStatus - Новый статус.
     * @param {boolean} updateAllMatching - Если true, обновить все элементы с этим sectionId.
     */
    function updateSectionStatusUI(sectionId, newStatus, updateAllMatching = false) {
        if (!tocList) return;
        const sectionItems = updateAllMatching
            ? tocList.querySelectorAll(`.toc-item[data-section-id="${sectionId}"]`)
            : [tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`)];

        sectionItems.forEach(sectionItem => {
            if (!sectionItem) return;

            const previousStatus = sectionItem.dataset.status;
            // Обновляем только если статус реально изменился
            if (previousStatus === newStatus) return;
            sectionItem.dataset.status = newStatus;

            const statusSpan = sectionItem.querySelector('.toc-status');
            const downloadLink = sectionItem.querySelector('.download-section-link');
            const processingIndicator = sectionItem.querySelector('.processing-indicator');
            const updateBtn = sectionItem.querySelector('.update-translation-btn'); // Находим кнопку обновления

            // Обновляем текст и стиль статуса
            if (statusSpan) {
                statusSpan.className = `toc-status status-${newStatus}`;
                let statusText = newStatus.replace(/_/g, ' ').replace(/^error$/, 'Error');
                statusText = statusText.charAt(0).toUpperCase() + statusText.slice(1);
                if (newStatus === 'error_context_limit') statusText = 'Error (Too Large)';
                else if (newStatus === 'error_translation') statusText = 'Error (Translate)';
                else if (newStatus === 'error_caching') statusText = 'Error (Cache)';
                else if (newStatus === 'error_unknown') statusText = 'Error (Unknown)';
                else if (newStatus === 'completed_empty') statusText = 'Empty Section';
                else if (newStatus === 'translated') statusText = 'Translated'; // Явное имя
                 else if (newStatus === 'cached') statusText = 'Translated'; // Заменяем cached на Translated

                statusSpan.textContent = statusText;
            }

            // Обновляем видимость ссылки скачивания и кнопки обновления
            const isReady = ['translated', 'completed_empty'].includes(newStatus);
            const canUpdate = isReady || newStatus.startsWith('error_'); // Обновлять можно готовые или ошибочные

            if (downloadLink) downloadLink.classList.toggle('hidden', !isReady);
            if (updateBtn) updateBtn.classList.toggle('hidden', !canUpdate); // Показываем кнопку Обновить для готовых и ошибочных

            // Обновляем видимость индикатора загрузки
            if (processingIndicator) {
                 processingIndicator.style.display = newStatus === 'processing' ? 'inline' : 'none';
            }

            // Если активная секция завершила обработку (при общем обновлении)
            if (sectionId === activeSectionId && previousStatus === 'processing' && newStatus !== 'processing' && updateAllMatching) {
                console.log(`Polling update finished for active section ${sectionId}, new status: ${newStatus}. Reloading content.`);
                 if (!newStatus.startsWith('error')) {
                     loadAndDisplaySection(sectionId, true); // Обновляем контент
                 } else {
                      displayTranslatedText(`(Ошибка перевода раздела: ${statusSpan ? statusSpan.textContent : newStatus})`);
                 }
            }
        });
    }

    /**
     * Обновляет общий статус книги и UI кнопок.
     * @param {object} bookData - Данные о книге из /book_status.
     */
    function updateOverallBookStatusUI(bookData) {
        if (!bookData) return;

        const isCompleteOrErrors = bookData.status === 'complete' || bookData.status === 'complete_with_errors';
        const anythingTranslated = (bookData.translated_count || 0) > 0 || (bookData.error_count || 0) > 0; // Считаем завершенной, если есть хоть что-то обработанное

        if (downloadFullBtn) downloadFullBtn.disabled = !(isCompleteOrErrors && anythingTranslated);
        if (downloadFullLink) downloadFullLink.classList.toggle('hidden', !(isCompleteOrErrors && anythingTranslated));

        const canTranslateMore = bookData.status !== 'processing' && bookData.status !== 'complete';
        if (translateAllBtn) translateAllBtn.disabled = !canTranslateMore;

        // Обновляем статусы всех секций в TOC
        if (bookData.sections && tocList) {
            for (const [sectionId, status] of Object.entries(bookData.sections)) {
                updateSectionStatusUI(sectionId, status, true);
            }
        } else if (!tocList) {
             console.error("TOC list element not found for status update!");
        }

        // Управляем опросом
        if (isCompleteOrErrors && bookData.status !== 'processing') { // Останавливаем, если все готово И не в процессе
            stopPolling();
        } else if (bookData.status === 'processing' && !currentPolling) {
             startPolling();
        }
    }

    // --- Функции для запросов к API ---

    /**
     * Выполняет fetch с таймаутом.
     */
    async function fetchWithTimeout(resource, options = {}, timeout = 60000) { // Увеличен таймаут
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(resource, { ...options, signal: controller.signal });
            clearTimeout(id);
            return response;
        } catch (error) {
            clearTimeout(id);
            if (error.name === 'AbortError') console.error('Request timed out for:', resource);
            throw error;
        }
    }

    /**
     * Загружает и отображает перевод секции.
     * Если перевода нет, запускает фоновый перевод.
     * @param {string} sectionId - ID секции (файла).
     * @param {boolean} isUpdate - True, если это обновление контента после поллинга.
     */
    async function loadAndDisplaySection(sectionId, isUpdate = false) {
        console.log(`Loading section ${sectionId}. Is update: ${isUpdate}`);
        if (!sectionId || !translationSectionIdSpan || !translationContentDiv || !translationDisplay || !tocList || !languageSelect) {
             console.error("Essential UI element missing for loadAndDisplaySection");
             return;
        }
        activeSectionId = sectionId;
        translationSectionIdSpan.textContent = sectionId;
        translationContentDiv.innerHTML = '<p>Загрузка...</p>';
        translationDisplay.style.display = 'block';

        // Обновляем выделение активного элемента в TOC
        tocList.querySelectorAll('.toc-item').forEach(el => el.dataset.isActive = "false");
        const currentTocItem = tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`);
        if(currentTocItem) currentTocItem.dataset.isActive = "true";
        else console.warn(`TOC item for section ${sectionId} not found.`);

        const selectedLanguage = languageSelect.value; // Берем текущий выбранный язык

        try {
            const response = await fetchWithTimeout(`/get_translation/${currentBookId}/${sectionId}?lang=${selectedLanguage}`);

            if (response.ok) {
                const data = await response.json();
                displayTranslatedText(data.text);
                // Обновляем статус для всех элементов этой секции на 'translated'
                updateSectionStatusUI(sectionId, data.text === "" ? 'completed_empty' : 'translated', true);
            } else if (response.status === 404) {
                const errorData = await response.json().catch(() => ({ error: "Not found" }));
                // Если это не обновление после поллинга, запускаем перевод
                if (!isUpdate) {
                    translationContentDiv.innerHTML = '<p>Перевод не найден. Запускаем перевод...</p>';
                    await startSectionTranslation(sectionId); // Запускаем перевод
                } else {
                     displayTranslatedText(`(Перевод не найден или еще не готов: ${errorData.error || ''})`);
                     updateSectionStatusUI(sectionId, 'not_translated', true);
                }
            } else {
                const errorData = await response.json().catch(() => ({ error: "Unknown error" }));
                console.error(`Error fetching translation for ${sectionId}: ${response.status}`, errorData);
                displayTranslatedText(`Ошибка загрузки перевода (${response.status}): ${errorData.error || ''}`);
                updateSectionStatusUI(sectionId, 'error_unknown', true);
            }
        } catch (error) {
            console.error(`Network error loading section ${sectionId}:`, error);
            displayTranslatedText('Сетевая ошибка при загрузке раздела.');
            updateSectionStatusUI(sectionId, 'error_unknown', true);
        }
    }

     /**
      * Отображает текст в основной области, оборачивая параграфы в <p>.
      * @param {string} text - Текст для отображения (ожидаются параграфы, разделенные \n\n).
      */
     function displayTranslatedText(text) {
         const translationContentDiv = document.getElementById('translation-content');
         if (!translationContentDiv) return;
         translationContentDiv.innerHTML = ''; // Очищаем предыдущий контент

         const paragraphs = (text || "").split('\n\n'); // Разделяем на параграфы

         if (paragraphs.length === 1 && paragraphs[0].trim() === "") {
             // Если текст пустой или состоит только из пробелов
             const pElement = document.createElement('p');
             pElement.textContent = "(Раздел пуст или перевод не содержит текста)";
             pElement.style.fontStyle = 'italic';
             translationContentDiv.appendChild(pElement);
         } else {
             // Итерируем по параграфам и создаем для каждого тег <p>
             paragraphs.forEach(pText => {
                 const trimmedText = pText.trim(); // Убираем пробелы по краям параграфа
                 if (trimmedText) { // Добавляем только не пустые параграфы
                     const pElement = document.createElement('p');
                     // Используем textContent для безопасной вставки ТЕКСТА параграфа
                     pElement.textContent = trimmedText;
                     translationContentDiv.appendChild(pElement);
                 }
                 // Если нужно сохранить пустую строку между параграфами, можно добавить <br> или пустой <p>
                 // else if (pText.length === 0 && paragraphs.length > 1) {
                 //     // Можно добавить <br> или просто пропустить, как сейчас
                 // }
             });
         }
     }

    /**
     * Запускает фоновый перевод ОДНОЙ секции (или обновление).
     * @param {string} sectionId - ID секции (файла).
     */
    async function startSectionTranslation(sectionId) {
        if (!languageSelect || !modelSelect) {
             console.error("Language or Model select not found!");
             return;
        }
        console.log(`Requesting translation for ${sectionId}`);
        // Обновляем только ОДИН элемент на processing
        updateSectionStatusUI(sectionId, 'processing', false);
        startPolling();

        const selectedLanguage = languageSelect.value;
        const selectedModel = modelSelect.value;

        if (!selectedModel) {
            console.error("Модель не выбрана!");
            displayTranslatedText("Ошибка: Модель для перевода не выбрана.");
            updateSectionStatusUI(sectionId, 'error_unknown', true);
            return;
        }

        try {
            const response = await fetchWithTimeout(`/translate_section/${currentBookId}/${sectionId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_language: selectedLanguage,
                    model_name: selectedModel
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                console.error(`Failed to start translation for ${sectionId}: ${response.status}`, errorData);
                if(activeSectionId === sectionId) displayTranslatedText(`Ошибка запуска перевода (${response.status})`);
                updateSectionStatusUI(sectionId, 'error_translation', true); // Обновляем все на ошибку
            } else {
                const data = await response.json();
                console.log(`Translation started for ${sectionId}:`, data);
                if (sectionId === activeSectionId) {
                    displayTranslatedText('Перевод запущен. Ожидайте обновления статуса...');
                }
            }
        } catch (error) {
            console.error(`Network error starting translation for ${sectionId}:`, error);
            updateSectionStatusUI(sectionId, 'error_translation', true);
             if (sectionId === activeSectionId) {
                displayTranslatedText('Сетевая ошибка при запуске перевода.');
            }
        }
    }

    /**
     * Запускает фоновый перевод ВСЕХ непереведенных/ошибочных секций.
     */
    async function startTranslateAll() {
        if (!languageSelect || !modelSelect) {
             console.error("Language or Model select not found!");
             return;
        }
        console.log('Requesting translation for all remaining sections');
        if (translateAllBtn) translateAllBtn.disabled = true;
        startPolling();

        const selectedLanguage = languageSelect.value;
        const selectedModel = modelSelect.value;

        if (!selectedModel) {
            console.error("Модель не выбрана для 'Перевести все'!");
            if (translateAllBtn) translateAllBtn.disabled = false;
            return;
        }

        // Обновляем UI для всех секций, которые будем пытаться перевести
        if (tocList) {
            tocList.querySelectorAll('.toc-item').forEach(item => {
                 const status = item.dataset.status;
                 if (status === 'not_translated' || status.startsWith('error_')) {
                      updateSectionStatusUI(item.dataset.sectionId, 'processing', true);
                 }
            });
        } else { return; }

        try {
            const response = await fetchWithTimeout(`/translate_all/${currentBookId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_language: selectedLanguage,
                    model_name: selectedModel
                })
            });
            if (!response.ok) {
                 const errorData = await response.json().catch(() => ({}));
                console.error(`Failed to start 'translate all': ${response.status}`, errorData);
            } else {
                const data = await response.json();
                console.log(`'Translate all' request sent, ${data.launched_tasks} tasks launched.`);
            }
        } catch (error) {
             console.error(`Network error starting 'translate all':`, error);
        }
    }

    /**
     * Опрашивает статус книги с сервера.
     */
    async function pollBookStatus() {
        if (!currentPolling || typeof currentBookId === 'undefined' || currentBookId === null) {
            stopPolling();
            return;
        }
        console.log("Polling book status...");
        try {
            const timestamp = new Date().getTime();
            const response = await fetchWithTimeout(`/book_status/${currentBookId}?t=${timestamp}`);
            if (!response.ok) {
                console.error(`Error polling status: ${response.status}`);
                // Не останавливаем опрос при временных ошибках
                return;
            }
            const data = await response.json();
            // console.log("Received book status:", data); // Можно раскомментировать для отладки
            updateOverallBookStatusUI(data); // Обновляет все статусы и решает, остановить ли опрос
        } catch (error) {
            console.error('Network error during polling:', error);
            // Не останавливаем опрос при сетевых ошибках
        }
    }

    /**
     * Запускает периодический опрос статуса.
     */
    function startPolling() {
        if (!pollInterval) {
            console.log("Starting status polling...");
            currentPolling = true;
            // Запускаем первый опрос почти сразу
            setTimeout(pollBookStatus, 500);
            pollInterval = setInterval(pollBookStatus, 5000); // Опрос каждые 5 секунд
        }
    }

    /**
     * Останавливает периодический опрос статуса.
     */
    function stopPolling() {
        if (pollInterval) {
            console.log("Stopping status polling.");
            clearInterval(pollInterval);
            pollInterval = null;
            currentPolling = false;
            // Можно сделать финальный запрос статуса для обновления кнопок
            // setTimeout(pollBookStatus, 1000);
        }
    }

    // --- Функция загрузки моделей ---
    async function loadModels() {
        if (!modelSelect) return;
        console.log("Загрузка списка моделей...");
        modelSelect.disabled = true;
        modelSelect.innerHTML = '<option value="">Загрузка...</option>';

        try {
            const response = await fetchWithTimeout('/api/models');
            if (!response.ok) {
                console.error("Не удалось загрузить список моделей:", response.status);
                modelSelect.innerHTML = '<option value="">Ошибка загрузки</option>';
                return;
            }
            const models = await response.json();
            console.log("Доступные модели:", models);
            modelSelect.innerHTML = ''; // Очищаем

            if (!models || models.length === 0) {
                 modelSelect.innerHTML = '<option value="">Модели не найдены</option>';
                 return;
            }

            let defaultModelFound = false;
            const defaultModelValue = "models/gemini-1.5-flash"; // Имя модели по умолчанию

            models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name;
                option.textContent = `${model.display_name} (${model.name.split('/')[1]})`;
                option.title = `In: ${model.input_token_limit || 'N/A'}, Out: ${model.output_token_limit || 'N/A'}`;
                modelSelect.appendChild(option);
                if (model.name === defaultModelValue) {
                     option.selected = true;
                     defaultModelFound = true;
                }
            });
            if (!defaultModelFound && modelSelect.options.length > 0) {
                 modelSelect.options[0].selected = true; // Выбираем первую, если дефолтной нет
            }
             modelSelect.disabled = false;

        } catch (error) {
            console.error("Ошибка при выполнении запроса загрузки моделей:", error);
            modelSelect.innerHTML = '<option value="">Ошибка сети</option>';
        }
    }


    // --- Назначение обработчиков событий ---
    if (tocList) {
        tocList.addEventListener('click', (event) => {
            const link = event.target.closest('.toc-link');
            const updateBtn = event.target.closest('.update-translation-btn');

            if (link) { // Клик по названию главы
                event.preventDefault();
                const sectionItem = link.closest('.toc-item');
                if (!sectionItem) return;
                const sectionId = sectionItem.dataset.sectionId;
                if (sectionItem.dataset.status !== 'processing') {
                     loadAndDisplaySection(sectionId); // Показываем из кэша или запускаем ПЕРВЫЙ перевод
                } else {
                     console.log(`Section ${sectionId} is already processing.`);
                     // Уже обрабатывается, просто показываем сообщение
                     displayTranslatedText('(Раздел уже в процессе перевода...)');
                     translationSectionIdSpan.textContent = sectionId;
                     translationDisplay.style.display = 'block';
                     activeSectionId = sectionId; // Устанавливаем как активную
                     // Выделяем в TOC
                      document.querySelectorAll('.toc-item').forEach(el => el.dataset.isActive = "false");
                      sectionItem.dataset.isActive = "true";
                }
            } else if (updateBtn) { // Клик по кнопке "Обновить"
                const sectionItem = updateBtn.closest('.toc-item');
                 if (!sectionItem) return;
                 const sectionId = sectionItem.dataset.sectionId;
                 console.log(`Update requested for section ${sectionId}`);
                 if (sectionItem.dataset.status !== 'processing') {
                      // Запускаем перевод заново (бэкенд удалит кэш)
                      startSectionTranslation(sectionId);
                      // Показываем индикатор загрузки в основном окне
                      displayTranslatedText('Запускаем обновление перевода...');
                      translationSectionIdSpan.textContent = sectionId;
                      translationDisplay.style.display = 'block';
                      activeSectionId = sectionId;
                      // Выделяем в TOC
                      document.querySelectorAll('.toc-item').forEach(el => el.dataset.isActive = "false");
                      sectionItem.dataset.isActive = "true";
                 } else {
                      console.log(`Section ${sectionId} is already processing.`);
                 }
            }
            // Клик по ссылке скачивания раздела обрабатывается браузером
        });
    } else {
        console.error("TOC list element (#toc-list) not found!");
    }

    if (translateAllBtn) {
        translateAllBtn.addEventListener('click', startTranslateAll);
    } else {
         console.error("Translate All button (#translate-all-btn) not found!");
    }

    // --- Инициализация ---
    if (typeof currentBookId !== 'undefined' && currentBookId) {
         loadModels(); // Загружаем модели
         startPolling(); // Начинаем опрос статуса
    } else {
         console.log("No current book ID found on page load.");
         // Блокируем элементы управления
          if(modelSelect) modelSelect.disabled = true;
          if(languageSelect) languageSelect.disabled = true;
          if(translateAllBtn) translateAllBtn.disabled = true;
          if(downloadFullBtn) downloadFullBtn.disabled = true;
          if(downloadFullLink) downloadFullLink.classList.add('hidden');
    }

}); // Конец DOMContentLoaded