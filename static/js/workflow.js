document.addEventListener('DOMContentLoaded', () => {
    // Читаем admin параметр из URL
    const urlParams = new URLSearchParams(window.location.search);
    const admin = urlParams.get('admin') === 'true' || urlParams.get('user') === 'admin';

    const uploadForm = document.querySelector('.upload-form form');
    const progressOverlay = document.getElementById('progressOverlay');
    const progressText = document.getElementById('progressText');
    const bookList = document.querySelector('.book-list');
    const toggleBookListBtn = document.getElementById('toggleBookListBtn');
    const bookListContainer = document.getElementById('bookListContainer');
    
    const editAnalysisOverlay = document.getElementById('editAnalysisOverlay');
    const analysisTextArea = document.getElementById('analysisTextArea');
    const continueAfterEditBtn = document.getElementById('continueAfterEdit');
    const cancelEditBtn = document.getElementById('cancelEdit');

    const activePollingIntervals = new Map();

    // Инициализация существующих книг (запуск поллинга для тех, что в процессе)
    document.querySelectorAll('.book-item').forEach(item => {
        const bookId = item.getAttribute('data-book-id');
        const status = item.querySelector('.book-overall-status').textContent.trim();
        if (['processing', 'queued'].includes(status)) {
            startPolling(bookId);
        }
    });

    if (toggleBookListBtn && bookListContainer) {
        toggleBookListBtn.addEventListener('click', () => {
            bookListContainer.classList.toggle('hidden-list');
            const isHidden = bookListContainer.classList.contains('hidden-list');
            toggleBookListBtn.textContent = isHidden ? 'Books in Workflow ▼' : 'Books in Workflow ▲';
        });
    }

    function showProgressOverlay(message = 'Starting...') {
        progressText.textContent = message;
        progressOverlay.style.display = 'flex';
    }

    function hideProgressOverlay() {
        progressOverlay.style.display = 'none';
        progressText.textContent = '';
    }

    function updateProgressText(message) {
        progressText.textContent = message;
    }

    function showEditAnalysisOverlay(bookId, analysisText) {
        analysisTextArea.value = analysisText || '';
        editAnalysisOverlay.dataset.bookId = bookId;
        hideProgressOverlay();
        editAnalysisOverlay.style.display = 'flex';
    }

    function hideEditAnalysisOverlay() {
        editAnalysisOverlay.style.display = 'none';
        analysisTextArea.value = '';
        delete editAnalysisOverlay.dataset.bookId;
    }

    async function loadAnalysisForEdit(bookId) {
        try {
            showProgressOverlay('Загружаем результаты анализа...');
            const response = await fetch(`/workflow_download_analysis/${bookId}`);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const analysisText = await response.text();
            showEditAnalysisOverlay(bookId, analysisText);
        } catch (error) {
            console.error('Error loading analysis:', error);
            updateProgressText(`Ошибка загрузки: ${error.message}`);
            setTimeout(hideProgressOverlay, 3000);
        }
    }

    if (continueAfterEditBtn) {
        continueAfterEditBtn.addEventListener('click', async function() {
            const bookId = editAnalysisOverlay.dataset.bookId;
            const editedAnalysis = analysisTextArea.value;
            if (!bookId) return;
            
            try {
                hideEditAnalysisOverlay();
                showProgressOverlay('Сохраняем отредактированный анализ...');
                const response = await fetch(`/workflow_start_existing_book/${bookId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        continue_after_edit: true,
                        edited_analysis: editedAnalysis,
                        admin: admin
                    })
                });
                
                const data = await response.json();
                if (data.status === 'success') {
                    updateProgressText('Анализ сохранен. Продолжаем...');
                    startPolling(bookId);
                } else {
                    alert('Ошибка: ' + data.message);
                    hideProgressOverlay();
                }
            } catch (error) {
                alert('Ошибка сети');
                hideProgressOverlay();
            }
        });
    }
    
    if (cancelEditBtn) {
        cancelEditBtn.addEventListener('click', hideEditAnalysisOverlay);
    }

    if (uploadForm) {
        uploadForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            showProgressOverlay('Uploading file...');
            const formData = new FormData(uploadForm);
            try {
                const response = await fetch(uploadForm.action, { method: 'POST', body: formData });
                const result = await response.json();
                if (response.ok && result.book_id) {
                    updateProgressText('File uploaded. Starting workflow...');
                    setTimeout(() => location.reload(), 1000); 
                } else {
                    alert('Upload failed: ' + (result.error || 'Unknown error'));
                    hideProgressOverlay();
                }
            } catch (error) {
                alert('Upload error: ' + error);
                hideProgressOverlay();
            }
        });
    }

    function startPolling(bookId) {
        if (activePollingIntervals.has(bookId)) return;

        const intervalId = setInterval(async () => {
            try {
                const response = await fetch(`/workflow_book_status/${bookId}`);
                if (response.ok) {
                    const statusData = await response.json();
                    const bookStatus = statusData.current_workflow_status;
                    
                    updateBookListItem(bookId, statusData);

                    // Обновляем список секций, если он открыт
                    const sectionsList = document.getElementById(`sections-${bookId}`);
                    if (sectionsList && sectionsList.style.display !== 'none') {
                        loadBookSections(bookId, sectionsList, false);
                    }

                    const analysisStage = statusData.book_stage_statuses ? statusData.book_stage_statuses.analyze : null;
                    if (admin && analysisStage && analysisStage.status === 'awaiting_edit') {
                        clearInterval(intervalId);
                        activePollingIntervals.delete(bookId);
                        loadAnalysisForEdit(bookId);
                        return;
                    }

                    // Если все процессы (включая комикс) завершены, останавливаем поллинг
                    const isComicBusy = statusData.comic_status === 'processing';
                    if (!['processing', 'queued'].includes(bookStatus) && !isComicBusy) {
                        clearInterval(intervalId);
                        activePollingIntervals.delete(bookId);
                    }
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 3000);
        activePollingIntervals.set(bookId, intervalId);
    }

    function updateBookListItem(bookId, statusData) {
        const bookItem = document.querySelector(`.book-item[data-book-id="${bookId}"]`);
        if (!bookItem) return;

        const overallStatusSpan = bookItem.querySelector('.book-overall-status');
        if (overallStatusSpan) overallStatusSpan.textContent = statusData.current_workflow_status;

        // Обновляем статусы этапов и счетчики
        if (statusData.book_stage_statuses) {
            for (const [stageName, stageInfo] of Object.entries(statusData.book_stage_statuses)) {
                const stageSpan = bookItem.querySelector(`.stage-status[data-stage="${stageName}"]`);
                if (stageSpan) {
                    let statusText = stageInfo.status || 'pending';
                    if (stageInfo.is_per_section) {
                        const processed = statusData['processed_sections_count_' + stageName] || 0;
                        const total = statusData.total_sections_count || 0;
                        statusText += ` (${processed} / ${total} секций)`;
                    }
                    stageSpan.textContent = statusText;
                }

                // Кнопка Сделать комикс / Ссылка на комикс
                if (stageName === 'summarize') {
                    const summarizeDiv = bookItem.querySelector(`.stage-status[data-stage="summarize"]`).parentElement;
                    
                    // Обработка кнопки генерации
                    let comicBtn = summarizeDiv.querySelector('.generate-comic-button');
                    if (admin && ['completed', 'completed_empty'].includes(stageInfo.status)) {
                        if (!summarizeDiv.querySelector('.generate-comic-button') && !summarizeDiv.querySelector('a[href*="comic"]') && statusData.comic_status !== 'processing') {
                            const newBtn = document.createElement('button');
                            newBtn.className = 'generate-comic-button';
                            newBtn.setAttribute('data-book-id', bookId);
                            newBtn.style.cssText = 'margin-left: 10px; background-color: #6c757d; color: white; border: none; padding: 2px 8px; cursor: pointer; font-size: 0.8em; border-radius: 3px;';
                            newBtn.innerText = 'Сделать комикс';
                            summarizeDiv.appendChild(newBtn);
                        }
                    }

                    if (statusData.comic_status === 'processing') {
                        if (comicBtn) {
                            comicBtn.disabled = true;
                            comicBtn.innerText = 'В процессе...';
                        } else if (!summarizeDiv.querySelector('.comic-proc-msg')) {
                            const span = document.createElement('span');
                            span.className = 'comic-proc-msg';
                            span.style.cssText = 'margin-left: 10px; color: orange; font-size: 0.8em;';
                            span.innerText = '(Комикс в процессе...)';
                            summarizeDiv.appendChild(span);
                        }
                    } else if (statusData.comic_status === 'completed') {
                        if (comicBtn) comicBtn.remove();
                        const procMsg = summarizeDiv.querySelector('.comic-proc-msg');
                        if (procMsg) procMsg.remove();
                        
                        if (!summarizeDiv.querySelector('a[href*="comic"]')) {
                            const link = document.createElement('a');
                            link.href = `/workflow/book/${bookId}/comic${admin ? '?admin=true' : ''}`;
                            link.style.cssText = 'margin-left: 10px; color: #6f42c1; font-weight: bold;';
                            link.innerText = 'Смотреть комикс';
                            summarizeDiv.appendChild(link);
                        }
                    }
                }
            }
        }
    }

    document.addEventListener('click', function(e) {
        const toggleLink = e.target.closest('.toggle-sections-link');
        if (toggleLink) {
            e.preventDefault();
            const bookId = toggleLink.getAttribute('data-book-id');
            const sectionsList = document.getElementById(`sections-${bookId}`);
            const icon = toggleLink.querySelector('.toggle-icon');
            
            if (sectionsList.style.display === 'none') {
                sectionsList.style.display = 'block';
                if (icon) icon.textContent = '▼';
                loadBookSections(bookId, sectionsList, true);
            } else {
                sectionsList.style.display = 'none';
                if (icon) icon.textContent = '▶';
            }
            return;
        }

        if (e.target.classList.contains('retranslate-btn')) {
            const bookId = e.target.getAttribute('data-book-id');
            const sectionId = e.target.getAttribute('data-section-id');
            retranslateSection(bookId, sectionId, e.target);
            return;
        }

        const delBtn = e.target.closest('.delete-book-button');
        if (delBtn) {
            const bookId = delBtn.getAttribute('data-book-id');
            if (confirm('Удалить эту книгу?')) {
                fetch(`/workflow_delete_book/${bookId}`, { method: 'POST' })
                .then(r => { if (r.ok) location.reload(); });
            }
            return;
        }

        if (e.target.classList.contains('generate-comic-button')) {
            const bookId = e.target.getAttribute('data-book-id');
            if (confirm('Запустить генерацию комикса?')) {
                const btn = e.target;
                btn.disabled = true;
                btn.innerText = 'Запуск...';
                fetch(`/workflow/api/book/${bookId}/generate_comic`, { method: 'POST' })
                .then(r => r.json())
                .then(d => {
                    if (d.status === 'success') startPolling(bookId);
                    else { alert('Ошибка: ' + d.message); btn.disabled = false; btn.innerText = 'Сделать комикс'; }
                });
            }
            return;
        }

        if (e.target.classList.contains('start-workflow-button')) {
            const bookId = e.target.getAttribute('data-book-id');
            showProgressOverlay('Starting workflow...');
            fetch(`/workflow_start_existing_book/${bookId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ admin: admin })
            })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'success') startPolling(bookId);
                else { alert('Error: ' + d.message); hideProgressOverlay(); }
            });
            return;
        }
    });

    function loadBookSections(bookId, container, showSpinner) {
        if (showSpinner) container.innerHTML = '<div style="padding:10px;text-align:center;"><i class="fas fa-spinner fa-spin"></i> Загрузка...</div>';
        
        fetch(`/workflow/api/book/${bookId}/sections`)
            .then(r => r.json())
            .then(sections => {
                container.innerHTML = '';
                if (!sections.length) { container.innerHTML = 'Секции не найдены'; return; }
                const list = document.createElement('ul');
                list.style.listStyle = 'none'; list.style.padding = '0';
                sections.forEach(s => {
                    const item = document.createElement('li');
                    item.style.cssText = 'display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #eee;padding:5px 0;';
                    
                    const title = document.createElement('span');
                    title.textContent = s.section_title;
                    title.style.cssText = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:250px;';
                    
                    const rightPart = document.createElement('div');
                    const status = document.createElement('span');
                    const sText = typeof s.status === 'object' ? (s.status.status || 'pending') : s.status;
                    status.textContent = `[${sText}]`;
                    status.style.cssText = `font-size:0.8em;font-weight:bold;margin-right:10px;color:${sText==='completed'?'green':(sText==='error'?'red':'orange')}`;
                    rightPart.appendChild(status);

                    if (admin && (sText === 'completed' || sText === 'error' || sText === 'completed_empty')) {
                        const btn = document.createElement('button');
                        btn.textContent = 'Повторить';
                        btn.className = 'retranslate-btn';
                        btn.setAttribute('data-book-id', bookId);
                        btn.setAttribute('data-section-id', s.section_id);
                        btn.style.cssText = 'font-size:0.7em;cursor:pointer;background:#6c757d;color:white;border:none;border-radius:3px;padding:2px 5px;';
                        rightPart.appendChild(btn);
                    }
                    item.appendChild(title); item.appendChild(rightPart);
                    list.appendChild(item);
                });
                container.appendChild(list);
            });
    }

    function retranslateSection(bookId, sectionId, button) {
        if (!confirm('Перевести эту секцию заново?')) return;
        button.disabled = true; button.innerText = '...';
        fetch(`/workflow/api/book/${bookId}/retranslate_section/${sectionId}?admin=true`, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.status === 'success') {
                startPolling(bookId);
            } else {
                alert('Ошибка: ' + d.message);
                button.disabled = false; button.innerText = 'Повторить';
            }
        });
    }
});
