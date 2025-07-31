document.addEventListener('DOMContentLoaded', () => {
    // console.log('workflow.js loaded.'); // Убрана отладочная строка

    const uploadForm = document.querySelector('.upload-form form');
    const progressOverlay = document.getElementById('progressOverlay');
    const progressText = document.getElementById('progressText');
    const bookList = document.querySelector('.book-list');
    const toggleBookListBtn = document.getElementById('toggleBookListBtn');
    const bookListContainer = document.getElementById('bookListContainer');
    
    // Edit analysis overlay elements
    const editAnalysisOverlay = document.getElementById('editAnalysisOverlay');
    const analysisTextArea = document.getElementById('analysisTextArea');
    const continueAfterEditBtn = document.getElementById('continueAfterEdit');
    const cancelEditBtn = document.getElementById('cancelEdit');

    // Use a Map to store polling intervals for each book
    const activePollingIntervals = new Map(); // Map<book_id, interval_id>

    if (toggleBookListBtn && bookListContainer) {
        toggleBookListBtn.addEventListener('click', () => {
            bookListContainer.classList.toggle('hidden-list');
            const isHidden = bookListContainer.classList.contains('hidden-list');
            // Update text for link to keep 'Books in Workflow' and toggle arrow
            toggleBookListBtn.textContent = isHidden ? 'Books in Workflow ▼' : 'Books in Workflow ▲';
        });
        // Optional: Hide by default and show button text as "Show"
        bookListContainer.classList.add('hidden-list');
        // Update initial text for link to 'Books in Workflow ▼'
        toggleBookListBtn.textContent = 'Books in Workflow ▼';
    }

    // --- Function to show progress overlay ---
    function showProgressOverlay(message = 'Starting...') {
        progressText.textContent = message;
        progressOverlay.style.display = 'flex'; // Use flex to center content
    }

    // --- Function to hide progress overlay ---
    function hideProgressOverlay() {
        progressOverlay.style.display = 'none';
        progressText.textContent = ''; // Clear message
    }

    // --- Function to update progress text ---
    function updateProgressText(message) {
        progressText.textContent = message;
    }

    // --- Functions for edit analysis overlay ---
    function showEditAnalysisOverlay(bookId, analysisText) {
        console.log(`Showing edit analysis overlay for book ${bookId}`);
        analysisTextArea.value = analysisText || '';
        
        // Store bookId for later use
        editAnalysisOverlay.dataset.bookId = bookId;
        
        // Hide progress overlay and show edit overlay
        hideProgressOverlay();
        editAnalysisOverlay.style.display = 'flex';
    }

    function hideEditAnalysisOverlay() {
        editAnalysisOverlay.style.display = 'none';
        analysisTextArea.value = '';
        delete editAnalysisOverlay.dataset.bookId;
    }

    // --- Function to load analysis for editing ---
    async function loadAnalysisForEdit(bookId) {
        try {
            console.log(`Loading analysis for edit: book ${bookId}`);
            showProgressOverlay('Загружаем результаты анализа...');
            
            const response = await fetch(`/workflow_download_analysis/${bookId}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const analysisText = await response.text();
            console.log(`Analysis loaded for book ${bookId}, length: ${analysisText.length}`);
            
            showEditAnalysisOverlay(bookId, analysisText);
            
        } catch (error) {
            console.error('Error loading analysis for edit:', error);
            updateProgressText(`Ошибка загрузки анализа: ${error.message}`);
            setTimeout(hideProgressOverlay, 3000);
        }
    }

    // --- Event listeners for edit analysis buttons ---
    if (continueAfterEditBtn) {
        continueAfterEditBtn.addEventListener('click', async function() {
            const bookId = editAnalysisOverlay.dataset.bookId;
            const editedAnalysis = analysisTextArea.value;
            
            if (!bookId) {
                alert('Ошибка: не найден ID книги');
                return;
            }
            
            try {
                console.log(`Continuing workflow with edited analysis for book ${bookId}`);
                hideEditAnalysisOverlay();
                showProgressOverlay('Сохраняем отредактированный анализ...');
                
                const response = await fetch(`/workflow_start_existing_book/${bookId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ 
                        continue_after_edit: true,
                        edited_analysis: editedAnalysis 
                    })
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.message || 'Network response was not ok.');
                }
                
                const data = await response.json();
                if (data.status === 'success') {
                    updateProgressText('Анализ сохранен. Продолжаем workflow...');
                    startPolling(bookId);
                } else {
                    updateProgressText(`Ошибка: ${data.message || 'Unknown error'}`);
                    setTimeout(hideProgressOverlay, 3000);
                }
                
            } catch (error) {
                console.error('Error continuing after edit:', error);
                updateProgressText(`Ошибка: ${error.message}`);
                setTimeout(hideProgressOverlay, 3000);
            }
        });
    }
    
    if (cancelEditBtn) {
        cancelEditBtn.addEventListener('click', function() {
            console.log('Edit analysis cancelled');
            hideEditAnalysisOverlay();
        });
    }

    // --- Function to handle form submission ---
    uploadForm.addEventListener('submit', async (event) => {
        event.preventDefault(); // Prevent default form submission

        showProgressOverlay('Uploading file...');

        const formData = new FormData(uploadForm);

        try {
            const response = await fetch(uploadForm.action, {
                method: 'POST',
                body: formData
            });

            // --- MODIFICATION: Expecting JSON response from /workflow_upload ---
            const result = await response.json();

            if (response.ok) {
                // console.log('Upload successful:', result); // Убрана отладочная строка

                // Check if book_id is in the JSON response
                if (result && result.book_id) {
                    const currentBookId = result.book_id; // Use const, no need for global currentBookId
                    console.log('Processing started for Book ID:', currentBookId);
                    updateProgressText('File uploaded. Starting workflow...');

                    // --- NEW LOGIC: Dynamically add the new book to the list if it's not already there ---
                    let bookItem = bookList.querySelector(`[data-book-id="${currentBookId}"]`);
                    if (!bookItem) {
                        console.log('Adding new book item to list dynamically:', currentBookId);
                        bookItem = document.createElement('li');
                        bookItem.classList.add('book-item');
                        bookItem.setAttribute('data-book-id', currentBookId);

                        // Basic structure (should match HTML template structure as much as possible)
                        bookItem.innerHTML = `
                            <div class="book-info">
                                <strong>${result.filename || 'Unknown Filename'}</strong>
                                <span class="language">(${document.getElementById('target_language').value || 'unknown'})</span>
                                <div class="book-status book-status-processing">
                                    Status: <span class="book-overall-status">processing</span>
                                    <span class="summarize-progress">
                                        Summarization: <span class="completed-count">0</span> / <span class="total-count">${result.total_sections_count || '?'}</span> sections
                                    </span>
                                    <!-- Placeholder for Analysis status - will be updated by JS -->
                                    <span class="analysis-progress">Analysis: pending</span>
                                </div>
                            </div>
                            <div class="book-actions">
                                 <span class="download-summary-placeholder">Суммаризация не готова</span>
                                <a href="#" class="download-summary-link" style="display: none;">Скачать суммаризацию</a>
                                <span class="download-analysis-placeholder" style="display: none;">Анализ не готов</span>
                                <a href="#" class="download-analysis-link" style="display: none;">Скачать анализ</a>
                                <button class="delete-book-button" data-book-id="${result.book_id}">Удалить</button>
                            </div>
                        `;
                        // Append the new item to the list
                        bookList.appendChild(bookItem);
                        console.log('New book item added to DOM.');
                    }
                    // --- End NEW LOGIC ---

                    // Start polling for status updates
                    startPolling(currentBookId);
                } else {
                    // Handle case where JSON is ok but book_id is missing
                    updateProgressText('Upload successful, but Book ID missing in response.');
                    // console.error('Book ID not found in upload response:', result); // Убрана отладочная строка
                    setTimeout(hideProgressOverlay, 3000); // Hide after 3 seconds
                }

            } else {
                // Handle HTTP errors (response not ok)
                const errorText = result.error || `HTTP error! Status: ${response.status}`;
                updateProgressText(`Upload failed: ${errorText}`);
                // console.error('Upload failed:', response.status, response.statusText, result); // Убрана отладочная строка
                 // Keep overlay open with error message
            }
        } catch (error) {
            updateProgressText(`An error occurred during upload: ${error}`);
            // console.error('Upload error:', error); // Убрана отладочная строка
             // Keep overlay open with error message
        }
    });

    // --- Function to start polling for workflow status ---
    function startPolling(bookId) {
        // Stop existing polling for this book if it's already active
        if (activePollingIntervals.has(bookId)) {
            console.log(`Stopping existing polling for book ${bookId}`);
            clearInterval(activePollingIntervals.get(bookId));
        }

        // --- MODIFICATION: Use the new workflow status API endpoint ---
        const statusApiUrl = `/workflow_book_status/${bookId}`;

        const intervalId = setInterval(async () => {
            console.log(`Polling status for book ${bookId} at ${statusApiUrl}...`);

            try {
                const response = await fetch(statusApiUrl);
                if (response.ok) {
                    const statusData = await response.json();
                    console.log('Status update received:', statusData);

                    // Debugging: Log status data and derived variables
                    // console.log('DEBUG UI Update:', { // Убрана отладочная строка
                    //     statusData: statusData,
                    //     summaryStageData: statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null,
                    //     completedSummary: statusData.book_stage_statuses && statusData.book_stage_statuses.summarize ? (statusData.book_stage_statuses.summarize.completed_count || 0) : 0,
                    //     totalSections: statusData.total_sections_count || 0
                    // });

                    // --- MODIFIED: Update progress based on workflow status data ---
                    const bookStatus = statusData.current_workflow_status;
                    // CORRECTED: Get summarization stage data for status, but section counts from sections_status_summary
                    const summaryStageStatusData = statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null; // Summarization stage data for status/display_name
                    const summarySectionCountsData = statusData.sections_status_summary ? statusData.sections_status_summary.summarize : null; // Summarization section counts

                    const analysisStageData = statusData.book_stage_statuses ? statusData.book_stage_statuses.analyze : null; // Analysis stage data

                    console.log(`[updateBookListItem] Book ${bookId} - summaryStageStatusData:`, summaryStageStatusData); // NEW LOG: Check summary stage status data
                    console.log(`[updateBookListItem] Book ${bookId} - summarySectionCountsData:`, summarySectionCountsData); // NEW LOG: Check summary section counts data

                    console.log(`[updateBookListItem] Analysis Stage Data for ${bookId}:`, analysisStageData); // Keep existing log
                    
                    // --- ПРОВЕРКА НА СТАТУС AWAITING_EDIT ---
                    if (analysisStageData && analysisStageData.status === 'awaiting_edit') {
                        console.log(`Analysis is awaiting edit for book ${bookId}. Showing edit form.`);
                        clearInterval(intervalId);
                        activePollingIntervals.delete(bookId);
                        
                        // Загружаем результаты анализа и показываем форму редактирования
                        loadAnalysisForEdit(bookId);
                        return; // Останавливаем дальнейшую обработку
                    }

                    const totalSections = statusData.total_sections_count || 0;
                    const completedSummary = summaryStageStatusData ? 
                                             ((summaryStageStatusData.completed_count || 0) + 
                                              (summaryStageStatusData.skipped_count || 0) + 
                                              (summaryStageStatusData.completed_empty_count || 0)) : 0;

                    // --- НОВАЯ ЛОГИКА ОСТАНОВКИ ПОЛЛИНГА: Проверяем только общий статус книги ---
                    const isBookWorkflowFinished = bookStatus &&
                                                  (bookStatus === 'completed' ||
                                                   bookStatus === 'completed_with_errors' ||
                                                   bookStatus === 'error' ||
                                                   bookStatus.startsWith('error_'));

                    if (isBookWorkflowFinished) {
                        console.log(`Book workflow completed for book ${bookId}. Final status: ${bookStatus}. Stopping polling.`);
                        clearInterval(intervalId);
                        activePollingIntervals.delete(bookId);
                        hideProgressOverlay();
                        console.log(`Polling stopped for book ${bookId}. Overall status: ${bookStatus}.`);
                        
                        // --- НОВОЕ: Убеждаемся, что элемент списка книги обновлен финальными данными ---
                        updateBookListItem(bookId, statusData);
                        // --- КОНЕЦ НОВОГО ---

                    } else {
                        console.log(`Book ${bookId} workflow status is '${bookStatus}'. Polling continues.`);
                        
                        // Проверяем, есть ли активный этап
                        const currentStageName = statusData.current_stage_name;
                        
                        if (currentStageName && (bookStatus === 'processing' || bookStatus === 'queued')) {
                             // Есть активный этап и workflow в процессе - показываем оверлей
                             
                             // Get stage details from book_stage_statuses to check if it's per-section and get its status
                             const currentStageDetails = statusData.book_stage_statuses ? statusData.book_stage_statuses[currentStageName] : null;
                             const stageStatus = currentStageDetails ? currentStageDetails.status : 'unknown'; // Status of the current stage
                             const stageDisplayName = currentStageDetails ? currentStageDetails.display_name : currentStageName; // Display name or fallback

                             let progressTextContent = `${stageDisplayName}: ${stageStatus}`; // Start with display name and status

                             // --- ИЗМЕНЕНИЕ: Проверяем, есть ли сводка по секциям для текущего этапа ---
                             // Если есть сводка и общее количество секций > 0, считаем этап посекционным для целей отображения.
                             const stageSummary = statusData.sections_status_summary ? statusData.sections_status_summary[currentStageName] : null;

                             // Проверяем, что это потенциально посекционный этап (есть сводка) и что он активен (processing/queued)
                             if (stageSummary && stageSummary.total !== undefined && (stageStatus === 'processing' || stageStatus === 'queued')) {
                                   // Sum up completed, skipped, and empty sections from the section summary
                                   const completed = (stageSummary.completed || 0) + (stageSummary.skipped || 0) + (stageSummary.completed_empty || 0) + (stageSummary.processing || 0);
                                   const total = stageSummary.total || 0; // Use total from summary

                                   if (total > 0) {
                                        // Display progress as completed / total for per-section stages with actual progress
                                       progressTextContent += `: ${completed} / ${total} sections`; // Add counts
                                   } else { // Per-section active stage, data available but total is 0
                                        progressTextContent += `: 0 / ${total} sections`; // Explicitly show 0/0 or 0/something if total is 0
                                   }
                             }
                             // Если нет сводка по секциям или этап не активен, текст остается только статус.
                             
                             updateProgressText(progressTextContent);
                             
                         } else {
                              // Нет активного этапа или workflow завершен - скрываем оверлей
                              hideProgressOverlay();
                         }
                    }
                    // --- КОНЕЦ НОВОЙ ЛОГИКИ остановки поллинга и скрытия оверлея ---

                    // --- MODIFICATION: Find the book item in the list and update its display ---
                    const bookItem = bookList.querySelector(`[data-book-id="${bookId}"]`);
                    if (bookItem) {
                        // Update overall status text (optional, focus on stage status)
                        const overallStatusSpan = bookItem.querySelector('.book-overall-status');
                         if (overallStatusSpan) {
                            // Prioritize summarization stage status if available and finished
                            let statusToDisplay = bookStatus || 'unknown';
                            if (summaryStageStatusData) {
                                const summarizeStageStatus = summaryStageStatusData.status || 'unknown';
                                if (['completed', 'completed_empty', 'completed_with_errors', 'error'].includes(summarizeStageStatus) || summarizeStageStatus.startsWith('error_')) {
                                    statusToDisplay = summarizeStageStatus;
                                }
                                 // Always show summarize stage status if available, even if not finished, unless book status is a final error
                                 else if (bookStatus && (bookStatus === 'error' || bookStatus.startsWith('error_'))) {
                                     statusToDisplay = bookStatus;
                                 } else {
                                      statusToDisplay = summarizeStageStatus;
                                 }
                            }

                            overallStatusSpan.textContent = statusToDisplay;

                             // Update status class for styling
                            overallStatusSpan.parentElement.className = 'book-status'; // Reset to base class
                            const statusClass = (summaryStageStatusData ? summaryStageStatusData.status : bookStatus || '').toLowerCase().replace(/_/g, '-');
                             if (statusClass) {
                                 overallStatusSpan.parentElement.classList.add(`book-status-${statusClass}`);
                             }
                         }

                        // --- MODIFIED: Logic for Summarization Progress Display ---
                        const summarizeProgressSpan = bookItem.querySelector('.summarize-progress');
                        // Calculate completed sections count (sum of completed, skipped, empty)
                        // Use summarySectionCountsData for the counts
                        const completedSections = summarySectionCountsData ? (summarySectionCountsData.completed || 0) + (summarySectionCountsData.skipped || 0) + (summarySectionCountsData.completed_empty || 0) : 0;

                        // Use total from summarySectionCountsData if available, otherwise use the overall totalSections
                        const totalSectionsForStage = summarySectionCountsData ? summarySectionCountsData.total || totalSections : totalSections;

                        console.log(`[updateBookListItem] Book ${bookId} - Calculated Progress: completedSections=${completedSections}, totalSectionsForStage=${totalSectionsForStage}`); // Keep existing log
                        console.log(`[updateBookListItem] Book ${bookId} - summarizeProgressSpan found: ${!!summarizeProgressSpan}`); // Keep existing log

                        // Update summarization progress counts if the span exists and total sections > 0
                        if (summarizeProgressSpan && totalSectionsForStage > 0) {
                            summarizeProgressSpan.innerHTML = `Summarization: <span class="completed-count">${completedSections}</span> / <span class="total-count">${totalSectionsForStage}</span> sections`;
                            summarizeProgressSpan.style.display = ''; // Make sure it's visible

                            // NEW LOGS: Verify the span content after update
                            const completedSpan = summarizeProgressSpan.querySelector('.completed-count');
                            const totalSpan = summarizeProgressSpan.querySelector('.total-count');
                            console.log(`[updateBookListItem] Book ${bookId} - Updated Span Content: completed=${completedSpan ? completedSpan.textContent : 'N/A'}, total=${totalSpan ? totalSpan.textContent : 'N/A'}`);

                        } else if (summarizeProgressSpan) {
                            // If totalSections is 0 or stage data missing, hide or update the progress display
                            summarizeProgressSpan.textContent = ''; // Or 'No sections'
                            summarizeProgressSpan.style.display = 'none'; // Hide the element
                        }

                        // --- NEW: Logic for Analysis Status Display ---
                        const analysisProgressSpan = bookItem.querySelector('.analysis-progress');
                        if (analysisProgressSpan && analysisStageData) {
                            analysisProgressSpan.textContent = `Analysis: ${analysisStageData.status || 'unknown'}`;
                            // You might also want to add a class for styling based on analysis status
                             analysisProgressSpan.className = 'analysis-progress'; // Reset class
                             const analysisStatusClass = (analysisStageData.status || '').toLowerCase().replace(/_/g, '-');
                             if (analysisStatusClass) {
                                  analysisProgressSpan.classList.add(`analysis-progress-${analysisStatusClass}`);
                             }
                             analysisProgressSpan.style.display = ''; // Ensure it's visible
                        } else if (analysisProgressSpan) {
                            // If analysis data is missing, hide the span
                             analysisProgressSpan.style.display = 'none';
                        }

                        // --- НОВАЯ ЛОГИКА: Показ ссылки на скачивание АНАЛИЗА ---
                        const downloadAnalysisLink = bookItem.querySelector('.download-analysis-link');
                        const downloadAnalysisPlaceholder = bookItem.querySelector('.download-analysis-placeholder');

                        if (analysisStageData && (analysisStageData.status === 'completed' || analysisStageData.status === 'completed_empty')) {
                            // Анализ успешно завершен (включая пустые)
                            if (downloadAnalysisLink) {
                                downloadAnalysisLink.style.display = ''; // Показываем ссылку
                                downloadAnalysisLink.href = `/workflow_download_analysis/${bookId}`; // Устанавливаем href
                            }
                            if (downloadAnalysisPlaceholder) downloadAnalysisPlaceholder.style.display = 'none'; // Скрываем плейсхолдер

                        } else if (analysisStageData && (analysisStageData.status === 'error' || analysisStageData.status.startsWith('error_') || analysisStageData.status === 'completed_with_errors')) {
                            // Анализ завершен с ошибкой или этап завершился с ошибками
                             if (downloadAnalysisLink) downloadAnalysisLink.style.display = 'none'; // Скрываем ссылку
                             if (downloadAnalysisPlaceholder) {
                                 downloadAnalysisPlaceholder.style.display = '';
                                 // Отображаем статус ошибки этапа
                                 downloadAnalysisPlaceholder.textContent = `Ошибка анализа: ${analysisStageData.status}`;
                             }
                        } else {
                            // Этап анализа еще не завершен (processing, queued, pending)
                            if (downloadAnalysisLink) downloadAnalysisLink.style.display = 'none'; // Скрываем ссылку
                             if (downloadAnalysisPlaceholder) {
                                 downloadAnalysisPlaceholder.style.display = '';
                                 downloadAnalysisPlaceholder.textContent = 'Анализ не готов'; // Показываем плейсхолдер "не готов"
                             }
                        }
                        // --- КОНЕЦ НОВОЙ ЛОГИКИ для ссылки анализа ---

                    }
                    // --- End of MODIFICATION for book item update ---

                } else {
                    // Handle non-OK HTTP responses during polling (e.g., 404 if book deleted)
                    console.error(`Error polling status for book ${bookId}: ${response.status} ${response.statusText}`);
                    updateProgressText(`Error polling status: ${response.status}`);
                    // Optionally stop polling on certain errors like 404
                     if (response.status === 404) {
                         clearInterval(intervalId);
                         activePollingIntervals.delete(bookId);
                          updateProgressText(`Book ${bookId} not found. Polling stopped.`);
                           hideProgressOverlay(); // Hide overlay on 404
                     }
                }
            } catch (error) {
                console.error(`An error occurred during polling for book ${bookId}:`, error);
                updateProgressText(`Polling error: ${error}`);
                 // Stop polling on network errors
                 clearInterval(intervalId);
                 activePollingIntervals.delete(bookId);
                 hideProgressOverlay();
            }
        }, 3000); // Poll every 3 seconds
        // Store the interval ID in the map
        activePollingIntervals.set(bookId, intervalId);
    }

    // Helper function updateBookListItem now expects detailed statusData directly
    function updateBookListItem(bookId, statusData) {
        const bookItem = bookList.querySelector(`[data-book-id="${bookId}"]`);
        if (!bookItem) return;

        // Обновление языка и статуса (оставляем как есть)
        const languageSpan = bookItem.querySelector('.book-info .language');
        if (languageSpan && statusData.target_language) {
            languageSpan.textContent = `(${statusData.target_language})`;
        }
        const overallStatusSpan = bookItem.querySelector('.book-overall-status');
        if (overallStatusSpan) {
            overallStatusSpan.textContent = statusData.current_workflow_status || 'unknown';
        }

        // --- ПЕРЕРИСОВКА СПИСКА ЭТАПОВ ---
        const bookStatusBlock = bookItem.querySelector('.book-status');
        if (bookStatusBlock && statusData.book_stage_statuses) {
            let stagesHtml = '';
            // --- ДОБАВЛЯЕМ ОБЩИЙ СТАТУС КНИГИ ---
            stagesHtml += `Status: <span class="book-overall-status">${statusData.current_workflow_status || 'unknown'}</span>`;
            // Сортируем этапы по stage_order
            const stages = Object.entries(statusData.book_stage_statuses);
            stages.sort((a, b) => (a[1].stage_order || 0) - (b[1].stage_order || 0));
            for (const [stageName, stageData] of stages) {
                stagesHtml += `<div style="margin-bottom:2px;">
                    <strong>${stageData.display_name || stageName}:</strong>
                    <span class="stage-status" data-stage="${stageName}">
                        ${stageData.status || 'pending'}
                        ${stageData.is_per_section ? ` (${statusData['processed_sections_count_' + stageName] || 0} / ${statusData.total_sections_count || 0} секций)` : ''}
                    </span>
                    ${stageName === 'summarize' && ['completed', 'completed_empty'].includes(stageData.status) ? `<a href="/workflow_download_summary/${statusData.book_id}" style="margin-left:10px;">Скачать суммаризацию</a>` : ''}
                    ${stageName === 'analyze' && ['completed', 'completed_empty'].includes(stageData.status) ? `<a href="/workflow_download_analysis/${statusData.book_id}" style="margin-left:10px;">Скачать анализ</a>` : ''}
                    ${stageName === 'epub_creation' && ['completed', 'completed_with_errors'].includes(stageData.status) ? `<a href="/workflow_download_epub/${statusData.book_id}" style="margin-left:10px;">Скачать EPUB</a>` : ''}
                </div>`;
            }
            bookStatusBlock.innerHTML = stagesHtml;
        }
    }

    // --- Add event listeners for delete buttons ---
    // Use event delegation on the book list to handle clicks on delete buttons
    bookList.addEventListener('click', function(event) {
        const deleteButton = event.target.closest('.delete-book-button');
        if (deleteButton) {
            const bookId = deleteButton.getAttribute('data-book-id');
            if (bookId) {
                deleteBook(bookId);
            }
        }
    });

    // --- Function to handle book deletion ---
    async function deleteBook(bookId) {
        // --- MODIFICATION: Remove confirmation and always attempt deletion ---
        try {
            console.log(`Attempting to delete book workflow with ID: ${bookId}`);
            const response = await fetch(`/workflow_delete_book/${bookId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
            });

            if (response.ok) {
                console.log(`Book workflow ${bookId} deleted successfully.`);
                // Remove the book item from the DOM
                const bookItem = document.querySelector(`[data-book-id="${bookId}"]`);
                if (bookItem) {
                    bookItem.remove();
                     console.log(`Book item ${bookId} removed from DOM.`);
                }
                // Optional: Show a success message to the user (can be removed if no visual feedback is desired)
                // alert('Рабочий процесс книги удален.');

                 // Also stop polling for the deleted book if it was active
                 if (activePollingIntervals.has(bookId)) {
                     console.log(`Stopping polling for deleted book ${bookId}`);
                     clearInterval(activePollingIntervals.get(bookId));
                     activePollingIntervals.delete(bookId);
                 }

            } else {
                const errorText = await response.text();
                console.error(`Failed to delete book workflow ${bookId}: ${response.status} ${response.statusText}. Response: ${errorText}`);
                // Still show alert on failure
                alert(`Не удалось удалить рабочий процесс книги: ${errorText}`);
            }
        } catch (error) {
            console.error(`Error deleting book workflow ${bookId}:`, error);
            // Still show alert on error
            alert(`Произошла ошибка при удалении рабочего процесса книги: ${error}`);
        }
    }

    // Event listener for Start Workflow buttons
    document.querySelectorAll('.start-workflow-button').forEach(button => {
        button.addEventListener('click', function() {
            const bookId = this.dataset.bookId;
            
            // Проверяем URL параметр admin
            const urlParams = new URLSearchParams(window.location.search);
            const admin = urlParams.get('admin') === 'true';
            
            console.log(`Starting workflow for book ${bookId}, admin: ${admin}`);
            startWorkflowForExistingBook(bookId, admin);
        });
    });

    // Function to start workflow for an existing book
    function startWorkflowForExistingBook(bookId, admin = false) {
        const overlay = document.getElementById('progressOverlay');
        const progressText = document.getElementById('progressText');

        overlay.style.display = 'flex';
        progressText.textContent = `Starting workflow for book ${bookId}...`;

        console.log(`Starting workflow for book ID: ${bookId}, admin: ${admin}`);

        fetch(`/workflow_start_existing_book/${bookId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ admin: admin })
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(errorData => {
                    throw new Error(errorData.message || 'Network response was not ok.');
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.status === 'success') {
                updateProgressText('Workflow started. Please wait for updates...');
                // Start polling for status updates
                startPolling(bookId);
            } else {
                updateProgressText(`Error starting workflow: ${data.message || 'Unknown error'}`);
                setTimeout(hideProgressOverlay, 3000);
            }
        })
        .catch(error => {
            console.error('Error starting workflow:', error);
            updateProgressText(`Network error starting workflow: ${error}`);
            setTimeout(hideProgressOverlay, 3000);
        });
    }

}); 