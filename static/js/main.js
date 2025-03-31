document.addEventListener('DOMContentLoaded', () => {
    // Получаем элементы один раз
    const tocList = document.getElementById('toc-list');
    const translateAllBtn = document.getElementById('translate-all-btn');
    const downloadFullLink = document.getElementById('download-full-link');
    const downloadFullBtn = document.getElementById('download-full-btn');
    // const bookStatusSpan = document.getElementById('book-status'); // Больше не используется напрямую
    // const translatedCountSpan = document.getElementById('translated-count');
    // const totalCountSpan = document.getElementById('total-count');

    const translationDisplay = document.getElementById('translation-display');
    const translationSectionIdSpan = document.getElementById('translation-section-id');
    const translationContentPre = document.getElementById('translation-content');
    // const closeTranslationBtn = document.getElementById('close-translation-btn'); // Убрали кнопку закрытия

    let pollInterval;
    let currentPolling = false; // Флаг, что опрос активен

    // --- Функции обновления UI ---

    function updateSectionStatusUI(sectionId, status) {
        const sectionItem = tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`);
        if (!sectionItem) return;

        sectionItem.dataset.status = status; // Обновляем статус в data-атрибуте
        const statusSpan = sectionItem.querySelector('.toc-status');
        const downloadLink = sectionItem.querySelector('.download-section-link');
        const processingIndicator = sectionItem.querySelector('.processing-indicator');

        if (statusSpan) {
            statusSpan.className = `toc-status status-${status}`;
            // Формируем текст статуса
            let statusText = status.replace(/_/g, ' ').replace(/^error$/, 'Error'); // Общая ошибка
            statusText = statusText.charAt(0).toUpperCase() + statusText.slice(1);
            if (status === 'error_context_limit') statusText = 'Error (Too Large)';
            else if (status === 'error_translation') statusText = 'Error (Translate)';
            else if (status === 'error_caching') statusText = 'Error (Cache)';
            else if (status === 'error_unknown') statusText = 'Error (Unknown)';
            else if (status === 'completed_empty') statusText = 'Empty Section';

            statusSpan.textContent = statusText;
        }

        if (downloadLink) {
            // Показываем иконку скачивания для переведенных или кэшированных
            downloadLink.classList.toggle('hidden', !['cached', 'translated', 'completed_empty'].includes(status));
        }
        if (processingIndicator) {
             processingIndicator.style.display = status === 'processing' ? 'inline' : 'none';
        }
    }

    function updateOverallBookStatusUI(bookData) {
        if (!bookData) return;
        // Обновляем счетчики (если они есть в шаблоне)
        // translatedCountSpan.textContent = bookData.translated_count !== undefined ? bookData.translated_count : '?';
        // totalCountSpan.textContent = bookData.total_sections !== undefined ? bookData.total_sections : '?';

        // Активируем кнопку скачивания всей книги
        const isComplete = bookData.status === 'complete' || (bookData.status === 'complete_with_errors' && (bookData.translated_count || 0) > 0);
        downloadFullBtn.disabled = !isComplete;
        downloadFullLink.classList.toggle('hidden', !isComplete);

        // Блокируем кнопку "Перевести все", если идет обработка
        translateAllBtn.disabled = bookData.status === 'processing';

        // Обновляем статусы всех секций в TOC
        if (bookData.sections) {
            for (const [sectionId, status] of Object.entries(bookData.sections)) {
                updateSectionStatusUI(sectionId, status);
            }
        }

        // Остановить опрос, если книга обработана
        if (bookData.status === 'complete' || bookData.status === 'complete_with_errors') {
            stopPolling();
        } else if (bookData.status === 'processing' && !currentPolling) {
             // Если статус processing, а опрос не идет - запускаем
             startPolling();
        }
    }

    // --- Функции для запросов к API ---

    async function fetchWithTimeout(resource, options = {}, timeout = 60000) { // Увеличим таймаут
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(resource, { ...options, signal: controller.signal });
            clearTimeout(id);
            return response;
        } catch (error) {
            clearTimeout(id);
            if (error.name === 'AbortError') console.error('Request timed out');
            throw error;
        }
    }

    // Запрашиваем перевод или получаем текст из кэша для отображения
    async function loadAndDisplaySection(sectionId) {
        console.log(`Loading section ${sectionId}`);
        translationSectionIdSpan.textContent = sectionId;
        translationContentPre.textContent = 'Загрузка перевода...';
        translationDisplay.classList.remove('hidden');
        document.querySelectorAll('.toc-item').forEach(el => el.style.fontWeight = 'normal'); // Сброс выделения
        const currentTocItem = tocList.querySelector(`.toc-item[data-section-id="${sectionId}"]`);
        if(currentTocItem) currentTocItem.style.fontWeight = 'bold'; // Выделяем текущий

        try {
            // Пытаемся получить готовый перевод
            const response = await fetchWithTimeout(`/get_translation/${currentBookId}/${sectionId}?lang=${defaultTargetLanguage}`);

            if (response.ok) {
                const data = await response.json();
                displayTranslatedText(data.text);
                updateSectionStatusUI(sectionId, 'cached'); // Обновляем статус на случай если он был другим
            } else if (response.status === 404) {
                // Перевода нет, запускаем процесс
                translationContentPre.textContent = 'Перевод не найден. Запускаем перевод...';
                await startSectionTranslation(sectionId);
            } else {
                // Другая ошибка при получении перевода
                const errorData = await response.json().catch(() => ({}));
                console.error(`Error fetching translation for ${sectionId}: ${response.status}`, errorData);
                translationContentPre.textContent = `Ошибка загрузки перевода (${response.status}): ${errorData.error || ''}`;
                updateSectionStatusUI(sectionId, 'error_unknown'); // Примерный статус ошибки
            }
        } catch (error) {
            console.error(`Network error loading section ${sectionId}:`, error);
            translationContentPre.textContent = 'Сетевая ошибка при загрузке раздела.';
            updateSectionStatusUI(sectionId, 'error_unknown');
        }
    }

     // Отображает текст в основной области
    function displayTranslatedText(text) {
         translationContentPre.innerHTML = ''; // Очищаем
         const paragraphs = (text || "").split('\n\n');
         if (paragraphs.length === 1 && paragraphs[0] === "") {
             translationContentPre.textContent = "(Раздел пуст или перевод пустой)";
         } else {
             paragraphs.forEach(pText => {
                 if (pText.trim()) {
                     const pElement = document.createElement('p');
                     pElement.textContent = pText;
                     translationContentPre.appendChild(pElement);
                 }
             });
         }
     }

    // Запускает фоновый перевод ОДНОЙ секции
    async function startSectionTranslation(sectionId) {
        console.log(`Requesting translation for ${sectionId}`);
        updateSectionStatusUI(sectionId, 'processing');
        startPolling(); // Начинаем опрос

        try {
            const response = await fetchWithTimeout(`/translate_section/${currentBookId}/${sectionId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_language: defaultTargetLanguage,
                    model_name: defaultModelName
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                console.error(`Failed to start translation for ${sectionId}: ${response.status}`, errorData);
                // Статус ошибки установится при следующем опросе book_status
                translationContentPre.textContent = `Ошибка запуска перевода (${response.status})`;
            } else {
                const data = await response.json();
                console.log(`Translation started for ${sectionId}:`, data);
                translationContentPre.textContent = 'Перевод запущен. Ожидайте обновления статуса...';
                // Статус и результат обновятся при опросе book_status
            }
        } catch (error) {
            console.error(`Network error starting translation for ${sectionId}:`, error);
            updateSectionStatusUI(sectionId, 'error_translation', 'Сетевая ошибка');
            translationContentPre.textContent = 'Сетевая ошибка при запуске перевода.';
        }
    }

    // Запускает фоновый перевод ВСЕХ непереведенных
    async function startTranslateAll() {
        console.log('Requesting translation for all untranslated sections');
        translateAllBtn.disabled = true;
        startPolling();

        // Обновляем UI для всех "not_translated" или "error_*" секций
        tocList.querySelectorAll('.toc-item').forEach(item => {
             const status = item.dataset.status;
             if (status === 'not_translated' || status.startsWith('error_')) {
                  updateSectionStatusUI(item.dataset.sectionId, 'processing');
             }
        });

        try {
            const response = await fetchWithTimeout(`/translate_all/${currentBookId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_language: defaultTargetLanguage,
                    model_name: defaultModelName
                })
            });
            if (!response.ok) {
                 const errorData = await response.json().catch(() => ({}));
                console.error(`Failed to start 'translate all': ${response.status}`, errorData);
                 // Можно показать общее сообщение об ошибке
            } else {
                const data = await response.json();
                console.log(`'Translate all' request sent, ${data.launched_tasks} tasks launched.`);
            }
        } catch (error) {
             console.error(`Network error starting 'translate all':`, error);
        } finally {
            // Кнопка разблокируется при следующем обновлении статуса книги, если нужно
            // translateAllBtn.disabled = false;
        }
    }

    // Опрос статуса книги
    async function pollBookStatus() {
        if (!currentPolling) return; // Не опрашивать, если остановлено
        console.log("Polling book status...");
        try {
            const response = await fetchWithTimeout(`/book_status/${currentBookId}`);
            if (!response.ok) {
                console.error(`Error polling status: ${response.status}`);
                // Возможно, не стоит останавливать опрос из-за временной ошибки
                // stopPolling();
                return;
            }
            const data = await response.json();
            console.log("Received book status:", data);
            updateOverallBookStatusUI(data); // Эта функция решает, остановить ли опрос

            // Если текущая отображаемая секция завершила обработку, обновим ее текст
            const displayedSectionId = translationSectionIdSpan.textContent;
            const displayedSectionItem = tocList.querySelector(`.toc-item[data-section-id="${displayedSectionId}"]`);
            if (displayedSectionItem && displayedSectionItem.dataset.status !== 'processing') {
                 const newStatus = data.sections[displayedSectionId];
                 // Если статус изменился с processing на что-то другое
                 if (newStatus && newStatus !== 'processing' ) {
                      // Запросим текст заново, чтобы отобразить результат или ошибку
                      const transResp = await fetchWithTimeout(`/get_translation/${currentBookId}/${displayedSectionId}?lang=${defaultTargetLanguage}`);
                      if(transResp.ok){
                           const transData = await transResp.json();
                           displayTranslatedText(transData.text);
                      } else {
                           displayTranslatedText(`(Ошибка загрузки перевода: ${transResp.status})`);
                      }
                 }
            }

        } catch (error) {
            console.error('Network error during polling:', error);
            // stopPolling(); // Возможно, не стоит останавливать из-за временной ошибки
        }
    }

    function startPolling() {
        if (!pollInterval) {
            console.log("Starting status polling...");
            currentPolling = true;
            pollBookStatus(); // Запросить статус немедленно
            pollInterval = setInterval(pollBookStatus, 5000); // Опрос каждые 5 секунд
        }
    }

    function stopPolling() {
        if (pollInterval) {
            console.log("Stopping status polling.");
            clearInterval(pollInterval);
            pollInterval = null;
            currentPolling = false;
        }
    }

    // --- Назначение обработчиков событий ---

    if (tocList) {
        tocList.addEventListener('click', (event) => {
            const link = event.target.closest('.toc-link');
            if (link) {
                event.preventDefault(); // Предотвращаем переход по #
                const sectionItem = link.closest('.toc-item');
                const sectionId = sectionItem.dataset.sectionId;
                loadAndDisplaySection(sectionId); // Загружаем и показываем (или запускаем перевод)
            }
            // Обработчики для кнопок скачивания не нужны, т.к. это прямые ссылки <a>
        });
    }


    if (translateAllBtn) {
        translateAllBtn.addEventListener('click', startTranslateAll);
    }

    // --- Инициализация ---
    // Запускаем опрос статуса сразу при загрузке страницы
    startPolling();

});