document.addEventListener('DOMContentLoaded', () => {
    console.log('workflow.js loaded.');

    const uploadForm = document.querySelector('.upload-form form');
    const progressOverlay = document.getElementById('progressOverlay');
    const progressText = document.getElementById('progressText');
    const bookList = document.querySelector('.book-list');

    let pollingInterval = null; // To store the interval timer
    let currentBookId = null; // To store the book_id being processed

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
                    currentBookId = result.book_id;
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
        if (pollingInterval) clearInterval(pollingInterval); // Clear previous interval if any

        // --- MODIFICATION: Use the new workflow status API endpoint ---
        const statusApiUrl = `/workflow_book_status/${bookId}`;

        pollingInterval = setInterval(async () => {
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
                    const completedSummary = summaryStageData ? (summaryStageData.completed_count || 0) : 0;

                    // Determine overall progress display (focusing on summarize for now)
                    let progressMessage = `Book Status: ${bookStatus}`;

                    if (summaryStageData) {
                         progressMessage = `Summarization: ${completedSummary}/${totalSections} sections (${summaryStageData.status})`;
                    } else {
                         progressMessage = `Book Status: ${bookStatus} (Summarization stage info not available)`;
                    }

                    updateProgressText(progressMessage);

                    // --- MODIFICATION: Find the book item in the list and update its display ---
                    const bookItem = bookList.querySelector(`[data-book-id="${bookId}"]`);
                    if (bookItem) {
                        // Update overall status text (optional, focus on stage status)
                        const overallStatusSpan = bookItem.querySelector('.book-overall-status');
                         if (overallStatusSpan) {
                            overallStatusSpan.textContent = summaryStageData ? summaryStageData.status : (bookStatus || 'unknown');
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

                    }
                    // --- End of MODIFICATION for book item update ---


                    // --- MODIFICATION: Check if the summarization workflow stage is complete ---
                    // Stop polling and initiate download only when the 'summarize' stage is complete.
                    if (summaryStageData && summaryStageData.status === 'completed') {
                        console.log(`Summarization stage complete for book ${bookId}. Stopping polling.`);
                        clearInterval(pollingInterval); // Stop polling
                        pollingInterval = null; // Clear the variable
                        hideProgressOverlay(); // Hide progress overlay

                         // Automatically trigger download (optional, might annoy users)
                         // console.log("Initiating download...");
                         // window.location.href = `/workflow_download_summary/${bookId}`;

                         // Instead of auto-download, ensure the download link is visible (handled above)
                         console.log("Download link for summarization should now be visible.");
                    }

                } else {
                    // Handle non-OK HTTP responses during polling (e.g., 404 if book deleted)
                    console.error(`Error polling status for book ${bookId}: ${response.status} ${response.statusText}`);
                    updateProgressText(`Error polling status: ${response.status}`);
                    // Optionally stop polling on certain errors like 404
                     if (response.status === 404) {
                         clearInterval(pollingInterval);
                         pollingInterval = null;
                         updateProgressText(`Book ${bookId} not found. Polling stopped.`);
                          hideProgressOverlay(); // Hide overlay on 404
                     }
                }
            } catch (error) {
                console.error(`An error occurred during polling for book ${bookId}:`, error);
                updateProgressText(`Polling error: ${error}`);
                 // Stop polling on network errors
                 clearInterval(pollingInterval);
                 pollingInterval = null;
                 hideProgressOverlay();
            }
        }, 3000); // Poll every 3 seconds
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

             const totalSections = statusData.total_sections_count || 0;
             const completedSummary = summaryStageData ? (summaryStageData.completed_count || 0) : 0;

             // Update overall status text
             const overallStatusSpan = bookItem.querySelector('.book-overall-status');
             if (overallStatusSpan) {
                 overallStatusSpan.textContent = summaryStageData ? summaryStageData.status : (bookStatus || 'unknown');
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

             // Show download link or placeholder based on summarize stage status
             const downloadLink = bookItem.querySelector('.download-summary-link');
             const downloadPlaceholder = bookItem.querySelector('.download-summary-placeholder');

             if (summaryStageData && summaryStageData.status === 'completed') {
                 if (downloadLink) downloadLink.style.display = ''; // Show the link
                 if (downloadPlaceholder) downloadPlaceholder.style.display = 'none'; // Hide placeholder
                  if (downloadLink) downloadLink.href = `/workflow_download_summary/${bookId}`;
             } else if (summaryStageData && (summaryStageData.status === 'error' || summaryStageData.status.startsWith('error_'))) {
                 if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                 if (downloadPlaceholder) {
                     downloadPlaceholder.style.display = '';
                     downloadPlaceholder.textContent = `Ошибка суммаризации: ${summaryStageData.status}`; // Show error
                 }
             } else if (bookStatus && bookStatus.startsWith('error_')) {
                 if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                 if (downloadPlaceholder) {
                     downloadPlaceholder.style.display = '';
                     downloadPlaceholder.textContent = `Ошибка книги: ${bookStatus}`; // Show error
                 }
             } else {
                 if (downloadLink) downloadLink.style.display = 'none'; // Hide link
                 if (downloadPlaceholder) {
                     downloadPlaceholder.style.display = '';
                     downloadPlaceholder.textContent = 'Суммаризация не готова'; // Placeholder
                 }
             }
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

    // --- Function to delete a book workflow ---
    async function deleteBook(bookId) {
        console.log(`Attempting to delete book workflow with ID: ${bookId}`);
        if (!confirm('Вы уверены, что хотите удалить этот рабочий процесс книги?')) {
            return;
        }

        try {
            const response = await fetch(`/delete_book/${bookId}`, {
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
                // Optional: Show a success message to the user
                alert('Рабочий процесс книги удален.');
            } else {
                const errorText = await response.text();
                console.error(`Failed to delete book workflow ${bookId}: ${response.status} ${response.statusText}. Response: ${errorText}`);
                alert(`Не удалось удалить рабочий процесс книги: ${errorText}`);
            }
        } catch (error) {
            console.error(`Error deleting book workflow ${bookId}:`, error);
            alert(`Произошла ошибка при удалении рабочего процесса книги: ${error}`);
        }
    }

}); 