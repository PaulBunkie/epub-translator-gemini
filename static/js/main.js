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
     * @param {object} sectionInfo - Объект с данными секции (status, model_name, error_message, etc.).
     * @param {boolean} updateAllMatching - Если true, обновить все элементы с этим sectionId.
     */
    function updateSectionStatusUI(sectionId, sectionInfo, updateAllMatching = false) {
        // Извлекаем статус и имя модели из sectionInfo
        const newStatus = sectionInfo.status || 'not_translated';
        const modelName = sectionInfo.model_name;
        const errorMessage = sectionInfo.error_message;

        console.log(`[DEBUG-UI-Status] updateSectionStatusUI вызван для sectionId: ${sectionId}, newStatus: ${newStatus}, modelName: ${modelName}, updateAllMatching: ${updateAllMatching}`);
        if (!tocList) {
             console.log(`[DEBUG-UI-Status] tocList не найден.`);
             return;
        }
        const sectionItems = updateAllMatching
            ? tocList.querySelectorAll(`.toc-item[data-section-id="${sectionId}"]`)
            : [tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`)];

        sectionItems.forEach(sectionItem => {
            if (!sectionItem) {
                 console.log(`[DEBUG-UI-Status] sectionItem для ${sectionId} не найден.`);
                 return;
            }

            const previousStatus = sectionItem.dataset.status;
            // --- ДОБАВЛЕН ЛОГ: Текущий статус из DOM ---
            console.log(`[DEBUG-UI-Status] DOM Status для ${sectionId} перед обновлением: ${previousStatus}`);
            // --- КОНЕЦ ДОБАВЛЕННОГО ЛОГА ---

            console.log(`[DEBUG-UI-Status] Секция ${sectionId} (DOM): Предыдущий статус: ${previousStatus}, Новый статус: ${newStatus}`);

            // Обновляем только если статус реально изменился
            if (previousStatus === newStatus) {
                 console.log(`[DEBUG-UI-Status] Статус для ${sectionId} не изменился (${newStatus}), пропускаем обновление UI.`);
                 return;
            }
            sectionItem.dataset.status = newStatus;
            console.log(`[DEBUG-UI-Status] Статус в dataset для ${sectionId} обновлен на: ${sectionItem.dataset.status}`);


            const statusSpan = sectionItem.querySelector('.toc-status');
            const downloadLink = sectionItem.querySelector('.download-section-link');
            const processingIndicator = sectionItem.querySelector('.processing-indicator');
            const updateBtn = sectionItem.querySelector('.update-translation-btn');

            // Обновляем текст и стиль статуса
            if (statusSpan) {
                statusSpan.className = `toc-status status-${newStatus}`;
                let statusText = ''; // Переопределяем для нового подхода
                let tooltip = ''; // Переопределяем для нового подхода

                // --- Определяем statusText (приоритет у имени модели) ---
                // Если статус "успешный" и есть имя модели - показываем имя модели
                if (modelName && ['translated', 'cached', 'summarized', 'analyzed'].includes(newStatus)) {
                    // Показываем только часть имени после последнего слэша для краткости
                    statusText = modelName.includes('/') ? modelName.substring(modelName.lastIndexOf('/') + 1) : modelName;
                } else {
                    // Иначе, показываем стандартный текст статуса
                    switch (newStatus) {
                        case 'completed_empty': statusText = 'Empty Section'; break;
                        case 'processing': statusText = 'Processing'; break;
                        case 'not_translated':
                        case 'idle': statusText = 'Not Translated'; break;
                        case 'error_context_limit': statusText = 'Error (Too Large)'; break;
                        case 'error_translation': statusText = 'Error (Translate)'; break;
                        case 'error_caching': statusText = 'Error (Cache)'; break;
                        case 'error_extraction': statusText = 'Error (Extract)'; break;
                        case 'error_unknown': statusText = 'Error (Unknown)'; break;
                        default: // Для любых других неожиданных статусов
                            statusText = newStatus.replace(/_/g, ' ').replace(/^error$/, 'Error');
                            statusText = statusText.charAt(0).toUpperCase() + statusText.slice(1);
                            break;
                    }
                }

                statusSpan.textContent = statusText;
                console.log(`[DEBUG-UI-Status] statusSpan текст для ${sectionId} установлен: ${statusText}`);


                // --- Формируем тултип (приоритет у ошибки) ---
                if (errorMessage) {
                    tooltip = errorMessage; // Если есть ошибка, тултип - сообщение об ошибке
                } else if (['translated', 'cached', 'summarized', 'analyzed'].includes(newStatus)) {
                    // Если нет ошибки, но статус "успешный", тултип - тип операции и лимиты
                    let operationTypeForTooltip = '';
                    switch (newStatus) {
                         case 'translated':
                         case 'cached': operationTypeForTooltip = 'Translated'; break;
                         case 'summarized': operationTypeForTooltip = 'Summarized'; break;
                         case 'analyzed': operationTypeForTooltip = 'Analyzed'; break;
                    }
                    tooltip = operationTypeForTooltip;

                    // Добавляем лимиты токенов, если они есть в sectionInfo
                    if (sectionInfo.input_token_limit || sectionInfo.output_token_limit) {
                         tooltip += ` (In: ${sectionInfo.input_token_limit || 'N/A'}, Out: ${sectionInfo.output_token_limit || 'N/A'})`;
                    }
                }
                // Если нет ни ошибки, ни "успешного" статуса для тултипа, тултип остается пустым

                if (tooltip) {
                    statusSpan.title = tooltip;
                } else {
                    statusSpan.removeAttribute('title');
                }

            } else { console.log(`[DEBUG-UI-Status] statusSpan для ${sectionId} не найден.`); }

            // Обновляем видимость ссылки скачивания и кнопки обновления
            const isReady = ['translated', 'completed_empty', 'cached', 'summarized', 'analyzed'].includes(newStatus);
            const canUpdate = isReady || newStatus.startsWith('error_');

            if (downloadLink) { downloadLink.classList.toggle('hidden', !isReady); console.log(`[DEBUG-UI-Status] downloadLink hidden для ${sectionId}: ${!isReady}`); } else { console.log(`[DEBUG-UI-Status] downloadLink для ${sectionId} не найден.`); }

            if (updateBtn) {
                updateBtn.classList.toggle('hidden', !canUpdate);
                updateBtn.disabled = newStatus === 'processing';
                console.log(`[DEBUG-UI-Status] updateBtn hidden для ${sectionId}: ${!canUpdate}, disabled: ${updateBtn.disabled}`);
            } else { console.log(`[DEBUG-UI-Status] updateBtn для ${sectionId} не найден.`); }

            if (processingIndicator) {
                 processingIndicator.style.display = newStatus === 'processing' ? 'inline' : 'none';
                 console.log(`[DEBUG-UI-Status] processingIndicator display для ${sectionId} установлен: ${processingIndicator.style.display}`);
            } else { console.log(`[DEBUG-UI-Status] processingIndicator для ${sectionId} не найден.`); }

            if (sectionId === activeSectionId && previousStatus === 'processing' && newStatus !== 'processing' && updateAllMatching) {
                console.log(`Polling update finished for active section ${sectionId}, new status: ${newStatus}. Reloading content.`);
                 if (!newStatus.startsWith('error')) {
                     loadAndDisplaySection(sectionId, true);
                 } else {
                      displayTranslatedText(`(Ошибка обработки раздела: ${errorMessage || newStatus})`);
                 }
            }
        }); // Конец forEach
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
            for (const [sectionId, sectionInfo] of Object.entries(bookData.sections)) { // sectionInfo содержит status, model_name и т.д.
                 if (!sectionInfo) continue; // Пропускаем, если данных нет

                 // --- ИЗМЕНЕНИЕ: Передаем ВЕСЬ объект sectionInfo ---
                 // updateAllMatching = true, потому что это общее обновление от поллинга
                 updateSectionStatusUI(sectionId, sectionInfo, true);
                 // --- КОНЕЦ ИЗМЕНЕНИЯ ---
            }
        } else if (!tocList) {
             console.error("TOC list element not found for status update!");
        }

        // Управляем опросом
        const isBookProcessing = bookData.status === 'processing'; // Проверяем статус книги
        if (isCompleteOrErrors && !isBookProcessing) { // Останавливаем, если все готово И книга не в процессе
             stopPolling();
         } // Нет else if для startPolling, т.к. поллинг запускается при старте задач
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
     * Если перевода нет, показывает индикатор и ждет поллинга (при первом клике),
     * или показывает "не найден" (при обновлении от поллинга).
     * @param {string} sectionId - ID секции (файла).
     * @param {boolean} isUpdate - True, если это обновление контента после поллинга.
     */
    async function loadAndDisplaySection(sectionId, isUpdate = false) {
        console.log(`Loading section ${sectionId}. Is update: ${isUpdate}`);
        if (!sectionId || !translationSectionIdSpan || !translationContentDiv || !translationDisplay || !tocList) {
             console.error("Essential UI element missing for loadAndDisplaySection");
             return;
        }
        activeSectionId = sectionId;
        translationSectionIdSpan.textContent = sectionId;
        translationDisplay.style.display = 'block';

        // Проверяем текущий статус секции в UI (если элемент существует)
        const currentTocItem = tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`);
        const currentStatus = currentTocItem ? currentTocItem.dataset.status : 'unknown';

        // Обновляем выделение активного элемента в TOC
        tocList.querySelectorAll('.toc-item').forEach(el => el.dataset.isActive = "false");
        if(currentTocItem) currentTocItem.dataset.isActive = "true";
        else console.warn(`TOC item for section ${sectionId} not found.`);

        // --- ИЗМЕНЕНИЕ: Если секция в процессе (по UI), показываем сообщение и выходим ---
        if (currentStatus === 'processing' && !isUpdate) { // Только если это не обновление от поллинга
             displayTranslatedText('(Раздел уже в процессе перевода...)');
             translationContentDiv.innerHTML = '<p>Раздел уже в процессе обработки. Ожидайте завершения.</p>'; // Более явное сообщение
             console.log(`Section ${sectionId} is processing (UI status). Waiting for polling update.`);
             return; // Не пытаемся загрузить данные сейчас, ждем поллинга
         }
        // --- КОНЕЦ ИЗМЕНЕНИЯ ---

        translationContentDiv.innerHTML = '<p>Загрузка...</p>'; // Сброс текста загрузки

        const selectedLanguage = initialTargetLanguage; // Берем язык из переменной, переданной из бэкенда

        try {
            const response = await fetchWithTimeout(`/get_translation/${currentBookId}/${sectionId}?lang=${selectedLanguage}`);

            if (response.ok) {
                const data = await response.json();
                displayTranslatedText(data.text);
                // Статус обновится поллингом
            } else if (response.status === 404) {
                const errorData = await response.json().catch(() => ({ error: "Not found" }));
                console.warn(`Translation not found for ${sectionId}: ${errorData.error || ''}. isUpdate: ${isUpdate}`);

                // Если это первый клик (не обновление от поллинга) И статус не processing
                if (!isUpdate && currentStatus !== 'processing') {
                     console.log(`[loadAndDisplaySection] 404 при первом клике на не обработанную секцию. Запускаем обработку для ${sectionId}.`);
                     // --- ИЗМЕНЕНИЕ: Запускаем обработку, т.к. результата нет ---
                     startSectionTranslation(sectionId);
                     // startSectionTranslation сама обновит статус на processing
                     // и покажет сообщение об ожидании
                     // displayTranslatedText уже не нужно вызывать здесь, startSectionTranslation сделает это
                     // Вместо этого можно показать сообщение "Запускаем обработку..."
                      displayTranslatedText(
                           'Запускаем обработку раздела...\n\n' +
                           '(Обработка может занимать до нескольких минут в зависимости от выбранной модели и размера раздела. Пожалуйста, дождитесь завершения.)'
                      );
                     // --- КОНЕЦ ИЗМЕНЕНИЯ ---

                 } else {
                     // Это либо обновление от поллинга (isUpdate=true), либо секция уже processing.
                     // Показываем сообщение об отсутствии результата или ошибке.
                      displayTranslatedText(`(Результат обработки раздела не найден или ошибка: ${errorData.error || 'Неизвестная ошибка'})`);
                      // UI статус должен обновиться поллингом, если это не processing
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
        startPolling(); // Ensure polling is running
        console.log(`Запуск перевода секции ${sectionId}...`);
        const targetLanguage = languageSelect ? languageSelect.value : initialTargetLanguage;
        const modelName = modelSelect ? modelSelect.value : initialSelectedModel; // Use initialSelectedModel as fallback
        const operationType = operationSelect ? operationSelect.value : 'translate'; // Get selected operation type

        // --- ДОБАВЛЕН ЛОГ: Перед вызовом updateSectionStatusUI на processing ---
        console.log(`[DEBUG-Start] Вызов updateSectionStatusUI для ${sectionId} со статусом 'processing' перед запросом.`);
        // --- КОНЕЦ ДОБАВЛЕННОГО ЛОГА ---
        // Обновляем UI секции на processing
        // Передаем минимальный sectionInfo объект с только статусом
        updateSectionStatusUI(sectionId, { status: 'processing' }, true);

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

            // --- ДОБАВЛЕНО: Запускаем поллинг после отправки запроса ---
            startPolling();
            // --- КОНЕЦ ДОБАВЛЕНО ---

            console.log(`[startSectionTranslation] Response status for ${sectionId}: ${response.status}`);
            if (!response.ok) {
                // ... обработка ошибок ...
                 const errorData = await response.json().catch(() => ({}));
                 const errorStatus = `error_start_${response.status}`;
                 console.error(`[startSectionTranslation] Failed to start translation for ${sectionId}: ${response.status}`, errorData);
                 if(activeSectionId === sectionId) displayTranslatedText(`Ошибка запуска перевода (${response.status}): ${errorData.error || ''}`);
                 // --- ДОБАВЛЕН ЛОГ: Перед вызовом updateSectionStatusUI на ошибку ---
                 console.log(`[DEBUG-Start] Вызов updateSectionStatusUI для ${sectionId} со статусом ошибки '${errorStatus}'.`);
                 // --- КОНЕЦ ДОБАВЛЕННОГО ЛОГА ---
                 updateSectionStatusUI(sectionId, { status: errorStatus, error_message: errorData.error }, true); // Обновляем статус на ошибку
            } else {
                const data = await response.json();
                console.log(`[startSectionTranslation] Translation started successfully for ${sectionId}:`, data);
                // Сообщение об ожидании уже выведено выше, UI статус обновлен на processing
            }
        } catch (error) { // Ловим именно сетевую ошибку fetch
            console.error(`[startSectionTranslation] FETCH FAILED for ${sectionId}:`, error);
             // --- ДОБАВЛЕН ЛОГ: Перед вызовом updateSectionStatusUI на сетевую ошибку ---
             console.log(`[DEBUG-Start] Вызов updateSectionStatusUI для ${sectionId} со статусом 'error_network'.`);
             // --- КОНЕЦ ДОБАВЛЕННОГО ЛОГА ---
            updateSectionStatusUI(sectionId, { status: 'error_network', error_message: error.message }, true); // Обновляем статус на сетевую ошибку
             if (sectionId === activeSectionId) {
                displayTranslatedText(`Сетевая ошибка при запуске перевода: ${error.message}`);
            }
        }
    }

    /**
     * Запускает процесс перевода всех непереведенных секций на бэкенде.
     */
    async function startTranslateAll() {
        startPolling(); // Ensure polling is running
        console.log('Запуск перевода всех непереведенных секций...');
        const targetLanguage = languageSelect ? languageSelect.value : initialTargetLanguage;
        const modelName = modelSelect ? modelSelect.value : initialSelectedModel; // Use initialSelectedModel as fallback
        const operationType = operationSelect ? operationSelect.value : 'translate'; // Get selected operation type

        // Обновляем UI для всех непереведенных секций на processing
        if (tocList) {
            tocList.querySelectorAll('.toc-item[data-status="not_translated"]').forEach(item => {
                 const sectionId = item.dataset.sectionId;
                 if (sectionId) {
                      // --- ДОБАВЛЕН ЛОГ: Перед вызовом updateSectionStatusUI на processing ---
                      console.log(`[DEBUG-StartAll] Вызов updateSectionStatusUI для ${sectionId} со статусом 'processing' перед запросом.`);
                      // --- КОНЕЦ ДОБАВЛЕННОГО ЛОГА ---
                      // Передаем минимальный sectionInfo объект с только статусом
                      updateSectionStatusUI(sectionId, { status: 'processing' }, true);
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

            // --- ДОБАВЛЕНО: Запускаем поллинг после отправки запроса ---
            startPolling();
            // --- КОНЕЦ ДОБАВЛЕНО ---

            if (!response.ok) {
                 const errorData = await response.json().catch(() => ({}));
                console.error(`Failed to start 'translate all': ${response.status}`, errorData);
                // Не обновляем статус секций здесь, т.к. они уже были установлены в processing.
                // Ошибки на уровне отдельных секций будут приходить через поллинг.
            } else {
                const data = await response.json();
                console.log(`'Translate all' request sent, ${data.launched_tasks} tasks launched.`);
            }
        } catch (error) {
             console.error(`Network error starting 'translate all':`, error);
             // Аналогично, ошибки на уровне секций придут через поллинг.
        }
    }

    /**
     * Опрашивает статус книги с сервера.
     */
    async function pollBookStatus() {
        console.log("[DEBUG-Polling] pollBookStatus вызван. currentPolling:", currentPolling);
        if (currentPolling) {
             console.log("[DEBUG-Polling] pollBookStatus уже выполняется, пропускаем.");
             return; // Избегаем дублирования запросов
        }

        currentPolling = true;
        console.log("[DEBUG-Polling] Устанавливаем currentPolling = true");

        try {
            const fetchUrl = `/book_status/${currentBookId}?t=${Date.now()}`;
            console.log(`[DEBUG-Polling] Отправка запроса на ${fetchUrl}`);
            const response = await fetchWithTimeout(fetchUrl);
            console.log(`[DEBUG-Polling] Получен ответ от ${fetchUrl}. Status: ${response.status}`);

            if (!response.ok) {
                console.error(`[DEBUG-Polling] Ошибка HTTP при получении статуса книги: ${response.status}`);
                // Не выходим сразу, чтобы finally выполнился
            } else {
                const bookData = await response.json();
                console.log("[DEBUG-Polling] Получены данные статуса книги:", bookData);

                updateOverallBookStatusUI(bookData); // Обновляем UI на основе полученных данных
            }

        } catch (error) {
            console.error("[DEBUG-Polling] Ошибка при опросе статуса книги (catch):", error);
            // В случае ошибки запроса опрос продолжается.
        } finally {
            currentPolling = false; // Сброс флага в конце
            console.log("[DEBUG-Polling] Сброс currentPolling = false в finally");
        }
    }

    /**
     * Запускает периодический опрос статуса.
     */
    function startPolling() {
        if (!pollInterval) {
            console.log("Starting status polling...");
            // currentPolling = true; // Эту строку удаляем отсюда
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