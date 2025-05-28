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
    const downloadEpubLink = document.getElementById('download-epub-link');
    const downloadEpubBtn = document.getElementById('download-epub-btn'); 
    const modelSelect = document.getElementById('model-select');
    const languageSelect = document.getElementById('language-select');
    const operationSelect = document.getElementById('operation-select');

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
        if(downloadEpubBtn) downloadEpubBtn.disabled = true; 
        if(downloadEpubLink) downloadEpubLink.classList.add('hidden');
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
            const isReady = ['translated', 'completed_empty', 'cached', 'summarized', 'analyzed'].includes(newStatus);
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
        console.log("[DEBUG-UI] updateOverallBookStatusUI received bookData:", bookData);

        const isCompleteOrErrors = bookData.status === 'complete' || bookData.status === 'complete_with_errors';
        
        // --- ИЗМЕНЕНИЕ: Активируем кнопки скачивания, только если общий статус книги complete или complete_with_errors ---
        const showDownloadButtons = isCompleteOrErrors; // Возвращаем к исходной логике
        // --- КОНЕЦ ИЗМЕНЕНИЯ ---

        if (downloadFullBtn) downloadFullBtn.disabled = !showDownloadButtons;
        if (downloadFullLink) downloadFullLink.classList.toggle('hidden', !showDownloadButtons);

        if (downloadEpubBtn) downloadEpubBtn.disabled = !showDownloadButtons;
        if (downloadEpubLink) downloadEpubLink.classList.toggle('hidden', !showDownloadButtons);

        // --- ИЗМЕНЕНИЕ: Управляем активностью кнопки 'Перевести все' ---
        // Кнопка активна, если общий статус книги НЕ 'complete' и НЕ 'complete_with_errors'
        const enableTranslateAll = bookData.status !== 'complete' && bookData.status !== 'complete_with_errors';
        if (translateAllBtn) translateAllBtn.disabled = !enableTranslateAll;
        // --- КОНЕЦ ИЗМЕНЕНИЯ ---

        // Обновляем статусы всех секций в TOC
        if (bookData.sections && tocList) {
            // --- НОВЫЙ КОД (ЗАМЕНИТЬ СТАРЫЙ ЦИКЛ): ---
            for (const [sectionId, sectionInfo] of Object.entries(bookData.sections)) { // Получаем sectionInfo (объект), а не просто status
                 if (!sectionInfo) continue; // Пропускаем, если данных нет

                 const status = sectionInfo.status || 'not_translated';
                 console.log(`[DEBUG-UI] Processing section ${sectionId}, status: '${status}', type: ${typeof status}`);
                 const modelName = sectionInfo.model_name; // <--- Получаем имя модели
                 const errorMessage = sectionInfo.error_message; // <--- Получаем сообщение об ошибке

                 // Находим соответствующий элемент в DOM
                 const sectionItem = tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`);
                 if (!sectionItem) continue; // Пропускаем, если элемент не найден

                 const statusSpan = sectionItem.querySelector('.toc-status');
                 const downloadLink = sectionItem.querySelector('.download-section-link');
                 const processingIndicator = sectionItem.querySelector('.processing-indicator');
                 const updateBtn = sectionItem.querySelector('.update-translation-btn');

                 // Проверяем наличие элементов перед обновлением
                 if (!statusSpan || !downloadLink || !processingIndicator || !updateBtn) {
                     console.warn(`Missing UI elements within TOC item for section ${sectionId}`);
                     continue;
                 }

                 // --- Определяем статус, текст, класс и тултип --- 
                 let statusText = ''; // Объявляем здесь
                 let statusClass = ''; // Объявляем здесь
                 let tooltip = '';    // Объявляем здесь

                 switch (status) {
                     case 'translated':
                         statusText = 'Translated';
                         statusClass = 'status-translated';
                         if (modelName) { // Если есть модель, показываем ее имя и особый класс
                              statusText = modelName.includes('/') ? modelName.substring(modelName.lastIndexOf('/') + 1) : modelName;
                              statusClass = 'status-translated-model';
                              tooltip = `Translated by: ${modelName}`; 
                         }
                         break;
                     case 'cached': // Кэшированный перевод также отображается как Translated
                         statusText = 'Translated'; // Или 'Cached', если хотим отличать кэш без модели
                         statusClass = 'status-translated'; // Или 'status-cached' если хотим отдельный стиль
                         if (modelName) { // Если есть модель в кэше (редко), показываем ее
                              statusText = modelName.includes('/') ? modelName.substring(modelName.lastIndexOf('/') + 1) : modelName;
                              statusClass = 'status-translated-model';
                              tooltip = `Cached translation by: ${modelName}`; 
                         } else { // Кэш без модели, просто 'Cached' или 'Translated' (как сейчас) 
                              tooltip = 'From cache';
                         }
                         break;
                     case 'completed_empty':
                         statusText = 'Empty Section';
                         statusClass = 'status-completed-empty';
                         tooltip = 'Section was empty or contained no translatable text.';
                         break;
                     case 'summarized':
                         statusText = 'Summarized';
                         statusClass = 'status-summarized';
                          if (modelName) { // Если есть модель
                              statusText = modelName.includes('/') ? modelName.substring(modelName.lastIndexOf('/') + 1) : modelName;
                              tooltip = `Summarized by: ${modelName}`; 
                         }
                         break;
                     case 'analyzed':
                         statusText = 'Analyzed';
                         statusClass = 'status-analyzed';
                          if (modelName) { // Если есть модель
                              statusText = modelName.includes('/') ? modelName.substring(modelName.lastIndexOf('/') + 1) : modelName;
                              tooltip = `Analyzed by: ${modelName}`; 
                         }
                         break;
                     case 'processing':
                         statusText = 'Processing';
                         statusClass = 'status-processing';
                         break;
                     case 'not_translated':
                     case 'idle':
                         statusText = 'Not Translated';
                         statusClass = 'status-not-translated';
                         break;
                     case 'error_context_limit':
                         statusText = 'Error (Too Large)';
                         statusClass = 'status-error';
                         tooltip = errorMessage || status;
                         break;
                     case 'error_translation':
                         statusText = 'Error (Translate)';
                         statusClass = 'status-error';
                         tooltip = errorMessage || status;
                         break;
                     case 'error_caching':
                          statusText = 'Error (Cache)';
                          statusClass = 'status-error';
                          tooltip = errorMessage || status;
                          break;
                     case 'error_extraction':
                          statusText = 'Error (Extract)';
                          statusClass = 'status-error';
                          tooltip = errorMessage || status;
                          break;
                     case 'error_unknown':
                          statusText = 'Error (Unknown)';
                          statusClass = 'status-error';
                          tooltip = errorMessage || status;
                          break;
                     default: // Совсем неизвестный статус
                         statusText = status;
                         statusClass = 'status-unknown';
                         tooltip = errorMessage || status;
                         break;
                 }

                 // Применяем класс и текст
                 statusSpan.className = `toc-status ${statusClass}`; 
                 statusSpan.textContent = statusText;

                 // Применяем тултип
                 if (tooltip) {
                     statusSpan.title = tooltip; 
                 } else {
                     statusSpan.removeAttribute('title'); 
                 }

                 // --- Обновляем видимость кнопок и индикатора (как в твоем старом коде updateSectionStatusUI) ---
                 const isReady = ['translated', 'completed_empty', 'cached', 'summarized', 'analyzed'].includes(status);
                 const canUpdate = isReady || status.startsWith('error_');

                 downloadLink.classList.toggle('hidden', !isReady);
                 updateBtn.classList.toggle('hidden', !canUpdate);
                 processingIndicator.style.display = status === 'processing' ? 'inline' : 'none';

                 // --- Обновление контента активной секции (можно оставить как было) ---
                 const previousStatus = sectionItem.dataset.status; // Используем data-атрибут для хранения предыдущего статуса
                 if (sectionId === activeSectionId && previousStatus === 'processing' && status !== 'processing') {
                    console.log(`Polling update finished for active section ${sectionId}, new status: ${status}. Reloading content.`);
                     if (!status.startsWith('error_')) {
                         loadAndDisplaySection(sectionId, true); // Обновляем контент, если не ошибка
                     } else {
                          displayTranslatedText(`(Ошибка перевода раздела: ${errorMessage || status})`);
                     }
                 }
                 sectionItem.dataset.status = status; // Сохраняем текущий статус в data-атрибут

            }
            // --- КОНЕЦ НОВОГО КОДА ---
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
      * Отображает текст в основной области, оборачивая параграфы в <p>,
      * заменяя одинарные переносы на <br> и обрабатывая базовый Markdown (*, **).
      * @param {string} text - Текст для отображения.
      */
     function displayTranslatedText(text) {
         const translationContentDiv = document.getElementById('translation-content');
         if (!translationContentDiv) return;
         translationContentDiv.innerHTML = '';

         let processedText = (text || "");

         // --- Обработка Markdown ---
         // Сначала обрабатываем двойные звездочки (полужирный)
         processedText = processedText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
         // Затем одинарные звездочки (курсив) - важно делать после двойных!
         processedText = processedText.replace(/\*(.*?)\*/g, '<em>$1</em>');
         // --- Конец обработки Markdown ---


         // --- Обработка переносов строк ---
         const paragraphMarker = "%%%PARAGRAPH_BREAK%%%";
         processedText = processedText
                               .replace(/\n\n/g, paragraphMarker) // Заменяем двойные переносы
                               .replace(/\n/g, '<br>'); // Заменяем одинарные на <br>

         const paragraphsHtml = processedText.split(paragraphMarker); // Разделяем на параграфы

         if (paragraphsHtml.length === 1 && paragraphsHtml[0].trim() === "") {
              // ... (код для пустого текста) ...
             const pElement = document.createElement('p');
             pElement.textContent = "(Раздел пуст или перевод не содержит текста)";
             pElement.style.fontStyle = 'italic';
             translationContentDiv.appendChild(pElement);
         } else {
             paragraphsHtml.forEach(pHtml => {
                 const trimmedHtml = pHtml.trim();
                  // Проверяем, что строка не пустая и не состоит только из <br> (после trim)
                 if (trimmedHtml && trimmedHtml !== '<br>') {
                     const pElement = document.createElement('p');
                     // Используем innerHTML для вставки HTML с <br>, <em>, <strong>
                     pElement.innerHTML = trimmedHtml;
                     translationContentDiv.appendChild(pElement);
                 }
             });
         }
     }

    /**
     * Запускает процесс перевода одной секции на бэкенде.
     * @param {string} sectionId - ID секции для перевода.
     */
    async function startSectionTranslation(sectionId) {
        console.log(`Запуск перевода секции ${sectionId}...`);
        const targetLanguage = languageSelect ? languageSelect.value : initialTargetLanguage;
        const modelName = modelSelect ? modelSelect.value : initialSelectedModel; // Use initialSelectedModel as fallback
        const operationType = operationSelect ? operationSelect.value : 'translate'; // Get selected operation type

        // Обновляем UI секции на processing
        updateSectionStatusUI(sectionId, 'processing', true);

        try {
            const response = await fetchWithTimeout(`/translate_section/${currentBookId}/${sectionId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    target_language: targetLanguage,
                    model_name: modelName,
                    operation_type: operationType // Include operation type in the request body
                })
            });

            console.log(`[startSectionTranslation] Response status for ${sectionId}: ${response.status}`);
            if (!response.ok) {
                // ... (обработка ошибок ответа сервера как раньше) ...
                const errorData = await response.json().catch(() => ({}));
                console.error(`[startSectionTranslation] Failed to start translation for ${sectionId}: ${response.status}`, errorData);
                if(activeSectionId === sectionId) displayTranslatedText(`Ошибка запуска перевода (${response.status}): ${errorData.error || ''}`);
                updateSectionStatusUI(sectionId, `error_start_${response.status}`, true);
            } else {
                const data = await response.json();
                console.log(`[startSectionTranslation] Translation started successfully for ${sectionId}:`, data);
                // Сообщение об ожидании уже выведено выше
            }
        } catch (error) { // Ловим именно сетевую ошибку fetch
            console.error(`[startSectionTranslation] FETCH FAILED for ${sectionId}:`, error);
            updateSectionStatusUI(sectionId, 'error_network', true);
             if (sectionId === activeSectionId) {
                displayTranslatedText(`Сетевая ошибка при запуске перевода: ${error.message}`);
            }
        }
    }

    /**
     * Запускает процесс перевода всех непереведенных секций на бэкенде.
     */
    async function startTranslateAll() {
        console.log('Запуск перевода всех непереведенных секций...');
        const targetLanguage = languageSelect ? languageSelect.value : initialTargetLanguage;
        const modelName = modelSelect ? modelSelect.value : initialSelectedModel; // Use initialSelectedModel as fallback
        const operationType = operationSelect ? operationSelect.value : 'translate'; // Get selected operation type

        // Обновляем UI для всех непереведенных секций на processing
        if (tocList) {
            tocList.querySelectorAll('.toc-item[data-status="not_translated"]').forEach(item => {
                 const sectionId = item.dataset.sectionId;
                 if (sectionId) {
                      updateSectionStatusUI(sectionId, 'processing', true);
                 }
            });
        }

        try {
            const response = await fetchWithTimeout(`/translate_all/${currentBookId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    target_language: targetLanguage,
                    model_name: modelName,
                    operation_type: operationType // Include operation type in the request body
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

    async function loadModels() {
        if (!modelSelect) return;
        console.log("Загрузка списка моделей...");
        modelSelect.disabled = true;
        modelSelect.innerHTML = '<option value="">Загрузка...</option>';

        // --- ИЗМЕНЕНИЕ: Используем initialSelectedModel, переданный из шаблона ---
        // Убедимся, что initialSelectedModel доступна в этой области видимости
        // Если она объявлена глобально в HTML, то будет доступна.
        // const selectedModelFromServer = initialSelectedModel; // Можно присвоить локальной переменной для ясности
        console.log("Модель, которая должна быть выбрана (из сессии):", initialSelectedModel);

        try {
            const response = await fetchWithTimeout('/api/models');
            if (!response.ok) {
                // ... обработка ошибки ...
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

            let modelToSelectFound = false; // Переименовали для ясности

            models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.name; // Предполагаем, что API возвращает объекты с полем name
                 // Используем display_name, если есть, иначе name
                 option.textContent = model.display_name ? `${model.display_name} (${model.name.split('/')[1]})` : model.name;
                 // Добавляем title с лимитами токенов, если есть
                option.title = `In: ${model.input_token_limit || 'N/A'}, Out: ${model.output_token_limit || 'N/A'}`;

                modelSelect.appendChild(option);

                // --- ИЗМЕНЕНИЕ: Сравниваем с initialSelectedModel, а не с defaultModelValue ---
                if (model.name === initialSelectedModel) {
                     option.selected = true;
                     modelToSelectFound = true; // Отмечаем, что нашли нужную модель
                     console.log(`Модель ${initialSelectedModel} найдена и выбрана.`);
                }
            });

            // --- ИЗМЕНЕНИЕ: Если модель из сессии не найдена, выбираем первую ---
            // (Или можно выбрать 'gemini-1.5-flash', если он есть, как fallback)
            if (!modelToSelectFound && modelSelect.options.length > 0) {
                 // Попробуем найти 'gemini-1.5-flash' как запасной вариант
                 let fallbackDefaultFound = false;
                 for(let i=0; i < modelSelect.options.length; i++){
                     if(modelSelect.options[i].value === "models/gemini-1.5-flash"){
                         modelSelect.options[i].selected = true;
                         fallbackDefaultFound = true;
                         console.warn(`Модель из сессии (${initialSelectedModel}) не найдена в списке. Выбрана дефолтная 'gemini-1.5-flash'.`);
                         break;
                     }
                 }
                 // Если и дефолтной нет, выбираем первую
                 if(!fallbackDefaultFound){
                    modelSelect.options[0].selected = true;
                    console.warn(`Модель из сессии (${initialSelectedModel}) и дефолтная 'gemini-1.5-flash' не найдены. Выбрана первая модель: ${modelSelect.options[0].value}`);
                 }
            }
             modelSelect.disabled = false;

        } catch (error) {
            // ... обработка ошибки ...
             console.error("Ошибка при выполнении запроса загрузки моделей:", error);
             modelSelect.innerHTML = '<option value="">Ошибка сети</option>';
        }
    }

    /**
     * Обновляет текст кнопок 'Перевести все' и '🔄' в зависимости от выбранной операции.
     */
    function updateButtonTexts() {
         const selectedOperation = operationSelect ? operationSelect.value : 'translate';
         let translateAllText = 'Перевести все непереведенные';
         let updateButtonTitle = 'Перевести заново';

         switch (selectedOperation) {
              case 'summarize':
                   translateAllText = 'Пересказать все необработанные';
                   updateButtonTitle = 'Пересказать заново';
                   break;
              case 'analyze':
                   translateAllText = 'Проанализировать все необработанные';
                   updateButtonTitle = 'Проанализировать заново';
                   break;
              case 'translate':
              default:
                   // Текст по умолчанию уже установлен
                   break;
         }

         // Обновляем текст кнопки 'Перевести все'
         if (translateAllBtn) {
              translateAllBtn.textContent = translateAllText;
         }

         // Обновляем заголовок (title) кнопок '🔄' в оглавлении
         if (tocList) {
              tocList.querySelectorAll('.update-translation-btn').forEach(btn => {
                   btn.title = updateButtonTitle;
              });
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
                      displayTranslatedText(
                           'Запускаем обновление перевода...\n\n' +
                           '(Перевод может занимать до нескольких минут в зависимости от выбранной модели и размера раздела. Пожалуйста, дождитесь завершения процесса.)'
                      );
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

    // --- Обработчик для смены операции --- (Добавляем после объявления operationSelect)
    if (operationSelect) {
         operationSelect.addEventListener('change', updateButtonTexts);
         // Вызываем при загрузке, чтобы установить начальный текст
         updateButtonTexts(); 
    } else {
         console.error("Operation select element (#operation-select) not found!");
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
          if(downloadEpubBtn) downloadEpubBtn.disabled = true; 
          if(downloadEpubLink) downloadEpubLink.classList.add('hidden'); 
    }

}); // Конец DOMContentLoaded