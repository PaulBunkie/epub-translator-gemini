document.addEventListener('DOMContentLoaded', () => {
    console.log('workflow.js loaded.');

    const uploadForm = document.querySelector('.upload-form form');
    const progressOverlay = document.getElementById('progressOverlay');
    const progressText = document.getElementById('progressText');
    const bookList = document.querySelector('.book-list');
    const toggleBookListBtn = document.getElementById('toggleBookListBtn');
    const bookListContainer = document.getElementById('bookListContainer');

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
                console.log('Upload successful:', result);

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
                                <span class="language">(${result.target_language || 'russian'})</span>
                                <div class="book-status book-status-processing">
                                    Status: <span class="book-overall-status">processing</span>
                                    <span class="summarize-progress">
                                        Summarization: <span class="completed-count">0</span> / <span class="total-count">${result.total_sections_count || '?'}</span> sections
                                    </span>
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
                    console.error('Book ID not found in upload response:', result);
                    setTimeout(hideProgressOverlay, 3000); // Hide after 3 seconds
                }

            } else {
                // Handle HTTP errors (response not ok)
                const errorText = result.error || `HTTP error! Status: ${response.status}`;
                updateProgressText(`Upload failed: ${errorText}`);
                console.error('Upload failed:', response.status, response.statusText, result);
                 // Keep overlay open with error message
            }
        } catch (error) {
            updateProgressText(`An error occurred during upload: ${error}`);
            console.error('Upload error:', error);
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
                    console.log('DEBUG UI Update:', {
                        statusData: statusData,
                        summaryStageData: statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null,
                        completedSummary: statusData.book_stage_statuses && statusData.book_stage_statuses.summarize ? (statusData.book_stage_statuses.summarize.completed_count || 0) : 0,
                        totalSections: statusData.total_sections_count || 0
                    });

                    // --- MODIFICATION: Update progress based on workflow status data ---
                    const bookStatus = statusData.current_workflow_status;
                    // Correctly access the summarization stage data
                    const summaryStageData = statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null;

                    const totalSections = statusData.total_sections_count || 0;
                    const completedSummary = summaryStageData ? 
                                             ((summaryStageData.completed_count || 0) + 
                                              (summaryStageData.skipped_count || 0) + 
                                              (summaryStageData.completed_empty_count || 0)) : 0;

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

                    } else {
                        console.log(`Book ${bookId} workflow status is '${bookStatus}'. Polling continues.`);
                        // Убедитесь, что оверлей показывается, если статус processing или queued, и скрыт иначе
                        if (bookStatus === 'processing' || bookStatus === 'queued') {
                             // Оверлей уже должен быть показан при старте или загрузке.
                             // updateProgressText вызывается выше и обновляет текст.
                             
                             // --- NEW: Update overlay text with progress ---                             
                             const activeStageName = statusData.current_stage_name || 'Processing'; // Get active stage name if available, else use 'Processing'
                             updateProgressText(`${activeStageName}: ${completedSummary} / ${totalSections} sections`);
                             // --- END NEW ---                             

                        } else {
                             // Если статус не processing/queued, но и не финальный (что странно),
                             // возможно, стоит скрыть оверлей или показать статус книги без деталей этапа.
                             // В нормальном workflow эта ветка не должна достигаться до завершения.
                             // Если все этапы прошли, а статус не финальный, возможно, проблема в бэкенде.
                             hideProgressOverlay(); // На всякий случай скрываем, если статус неактивный и нефинальный
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
                            if (summaryStageData) {
                                const summarizeStageStatus = summaryStageData.status || 'unknown';
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
                            const statusClass = (summaryStageData ? summaryStageData.status : bookStatus || '').toLowerCase().replace(/_/g, '-');
                             if (statusClass) {
                                 overallStatusSpan.parentElement.classList.add(`book-status-${statusClass}`);
                             }
                         }

                        // Update summarization progress counts
                        const summarizeProgressSpan = bookItem.querySelector('.summarize-progress');
                        if (summarizeProgressSpan && totalSections > 0) {
                            const completedCountSpan = summarizeProgressSpan.querySelector('.completed-count');
                            const totalCountSpan = summarizeProgressSpan.querySelector('.total-count');
                            if (completedCountSpan) completedCountSpan.textContent = completedSummary;
                            if (totalCountSpan) totalCountSpan.textContent = totalSections;
                            // Update summarization stage status text if needed (currently not displayed separately in HTML)
                            // If you add a span for summarize status, update it here.
                        } else if (summarizeProgressSpan) {
                            // If totalSections is 0 or stage data missing, hide or update the progress display
                            summarizeProgressSpan.textContent = ''; // Or 'No sections'
                        }

                        // --- MODIFICATION: Show download link or placeholder based on summarize stage status ---
                        const downloadLink = bookItem.querySelector('.download-summary-link');
                        const downloadPlaceholder = bookItem.querySelector('.download-summary-placeholder');

                        if (summaryStageData && summaryStageData.status === 'completed') {
                            // Summarization is complete
                            if (downloadLink) downloadLink.style.display = ''; // Show the link
                            if (downloadPlaceholder) downloadPlaceholder.style.display = 'none'; // Hide placeholder
                             if (downloadLink) downloadLink.href = `/workflow_download_summary/${bookId}`;

                        } else if (summaryStageData && (summaryStageData.status === 'error' || summaryStageData.status.startsWith('error_'))) {
                            // Summarization stage level error
                             if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                            if (downloadPlaceholder) {
                                downloadPlaceholder.style.display = '';
                                downloadPlaceholder.textContent = `Ошибка суммаризации: ${summaryStageData.status}`; // Show error in placeholder
                            }
                        } else if (bookStatus && bookStatus.startsWith('error_')) {
                             // Book level error (fallback if stage data missing)
                             if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                            if (downloadPlaceholder) {
                                downloadPlaceholder.style.display = '';
                                downloadPlaceholder.textContent = `Ошибка книги: ${bookStatus}`; // Show error in placeholder
                            }
                        } else {
                            // Processing, queued, pending, etc. - hide link, show placeholder
                            if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                            if (downloadPlaceholder) {
                                downloadPlaceholder.style.display = ''; // Show placeholder
                                 downloadPlaceholder.textContent = 'Суммаризация не готова'; // Reset placeholder text
                            }
                        }

                        // --- НОВАЯ ЛОГИКА: Показ ссылки на скачивание АНАЛИЗА ---
                        const downloadAnalysisLink = bookItem.querySelector('.download-analysis-link');
                        const downloadAnalysisPlaceholder = bookItem.querySelector('.download-analysis-placeholder');
                        const analysisStageData = statusData.book_stage_statuses ? statusData.book_stage_statuses.analyze : null; // Получаем данные этапа анализа

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

    // --- Initial check and polling start for existing books on page load ---
    // This is important if the user refreshes the page while a workflow is running
    bookList.querySelectorAll('.book-item').forEach(item => {
        const bookId = item.getAttribute('data-book-id');
        const overallStatusSpan = item.querySelector('.book-overall-status');
        // Only start polling for books that are currently in a 'processing' state
        if (bookId && overallStatusSpan && overallStatusSpan.textContent.includes('processing')) {
            console.log(`Found processing book ${bookId} on page load. Starting polling.`);
            startPolling(bookId);
        }
    });

     // --- Initial call to fetch status for *all* books on page load ---
     // This ensures that the status display is up-to-date when the page loads,
     // even for books that might have completed or errored out while the page was closed.
     // This is a better approach than just polling 'processing' books.
     console.log('Fetching initial status for all books on page load...');
     bookList.querySelectorAll('.book-item').forEach(item => {
        const bookId = item.getAttribute('data-book-id');
        if (bookId) {
            // Fetch status once for each book on load
            fetch(`/workflow_book_status/${bookId}`)
                .then(response => response.json())
                .then(statusData => {
                    console.log(`Initial status for book ${bookId}:`, statusData);
                    // Manually update the list item display based on the initial status
                    updateBookListItem(bookId, statusData); // Call a helper function to update the item
                    // If the book is still processing after initial fetch, start polling
                    if (statusData.current_workflow_status === 'processing') {
                        startPolling(bookId); // Start polling if still processing
                    }
                })
                .catch(error => console.error(`Error fetching initial status for book ${bookId}:`, error));
        }
     });

     // --- Helper function to update a single book list item based on status data ---
     function updateBookListItem(bookId, statusData) {
         // Debugging: Log status data and derived variables in helper function
         console.log('DEBUG updateBookListItem:', {
             bookId: bookId,
             statusData: statusData,
             summaryStageData: statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null,
             completedSummary: statusData.book_stage_statuses && statusData.book_stage_statuses.summarize ? (statusData.book_stage_statuses.summarize.completed_count || 0) : 0,
             totalSections: statusData.total_sections_count || 0
         });

         const bookItem = bookList.querySelector(`[data-book-id="${bookId}"]`);
         if (bookItem) {
             const bookStatus = statusData.current_workflow_status;
             const summaryStageData = statusData.book_stage_statuses ? statusData.book_stage_statuses.summarize : null;

             // Update displayed language
             const languageSpan = bookItem.querySelector('.book-info .language');
             if (languageSpan && statusData.target_language) {
                 languageSpan.textContent = `(${statusData.target_language})`;
             }

             const totalSections = statusData.total_sections_count || 0;
             const completedSummary = summaryStageData ? 
                                              ((summaryStageData.completed_count || 0) + 
                                               (summaryStageData.skipped_count || 0) + 
                                               (summaryStageData.completed_empty_count || 0)) : 0;

             // Update overall status text
             const overallStatusSpan = bookItem.querySelector('.book-overall-status');
             if (overallStatusSpan) {
                 // Prioritize summarization stage status if available and finished
                 let statusToDisplay = bookStatus || 'unknown';
                 if (summaryStageData) {
                     const summarizeStageStatus = summaryStageData.status || 'unknown';
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
                const statusClass = (summaryStageData ? summaryStageData.status : bookStatus || '').toLowerCase().replace(/_/g, '-');
                if (statusClass) {
                    overallStatusSpan.parentElement.classList.add(`book-status-${statusClass}`);
                }
             }

             // Update summarization progress counts
             const summarizeProgressSpan = bookItem.querySelector('.summarize-progress');
             if (summarizeProgressSpan && totalSections > 0) {
                 const completedCountSpan = summarizeProgressSpan.querySelector('.completed-count');
                 const totalCountSpan = summarizeProgressSpan.querySelector('.total-count');
                 if (completedCountSpan) completedCountSpan.textContent = completedSummary;
                 if (totalCountSpan) totalCountSpan.textContent = totalSections;
             } else if (summarizeProgressSpan) {
                 summarizeProgressSpan.textContent = ''; // Or 'No sections'
             }

             // --- НОВАЯ ЛОГИКА: Показ ссылки на скачивание СУММАРИЗАЦИИ ---
             const downloadSummaryLink = bookItem.querySelector('.download-summary-link');
             const downloadSummaryPlaceholder = bookItem.querySelector('.download-summary-placeholder');

             if (summaryStageData && (summaryStageData.status === 'completed' || summaryStageData.status === 'completed_empty')) {
                 if (downloadSummaryLink) {
                     downloadSummaryLink.style.display = '';
                     downloadSummaryLink.href = `/workflow_download_summary/${bookId}`;
                 }
                 if (downloadSummaryPlaceholder) downloadSummaryPlaceholder.style.display = 'none';
             } else if (summaryStageData && (summaryStageData.status === 'error' || summaryStageData.status.startsWith('error_') || summaryStageData.status === 'completed_with_errors')) {
                  if (downloadSummaryLink) downloadSummaryLink.style.display = 'none';
                  if (downloadSummaryPlaceholder) {
                      downloadSummaryPlaceholder.style.display = '';
                      downloadSummaryPlaceholder.textContent = `Ошибка суммаризации: ${summaryStageData.status}`;
                  }
             } else {
                  if (downloadSummaryLink) downloadSummaryLink.style.display = 'none';
                  if (downloadSummaryPlaceholder) {
                      downloadSummaryPlaceholder.style.display = '';
                      downloadSummaryPlaceholder.textContent = 'Суммаризация не готова';
                  }
             }
             // --- КОНЕЦ НОВОЙ ЛОГИКИ для ссылки суммаризации ---

             // --- НОВАЯ ЛОГИКА: Показ ссылки на скачивание АНАЛИЗА ---
             const downloadAnalysisLink = bookItem.querySelector('.download-analysis-link');
             const downloadAnalysisPlaceholder = bookItem.querySelector('.download-analysis-placeholder');
             const analysisStageData = statusData.book_stage_statuses ? statusData.book_stage_statuses.analyze : null; // Получаем данные этапа анализа

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

}); 