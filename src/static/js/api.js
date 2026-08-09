// Brother QL Printer App - API Interactions

// Backend requires an explicit confirmation flag when printing this many
// copies or more. The UI mirrors that threshold with a confirm dialog.
const LARGE_BATCH_THRESHOLD = 10;

// The held job currently open in the composer, or null.
//
// Set by openJob and cleared whenever the composer stops representing that job
// -- printing it, saving it, or opening something else. While it is set the
// submit buttons offer "Save changes" alongside "Print", and both carry
// amend_job_id so the held entry is updated in place instead of a near-copy
// being added next to it.
let openedHeldJobId = null;

/**
 * Add amend_job_id to a request payload when a held job is open.
 * @param {object} body   the request body being built
 * @param {boolean} keepHeld  true to stay held (Save), false to print
 */
function withAmend(body, keepHeld) {
    if (openedHeldJobId) {
        body.amend_job_id = openedHeldJobId;
        if (keepHeld) body.hold = true;
    } else if (keepHeld) {
        body.hold = true;
    }
    return body;
}

/**
 * Same, for the multipart endpoints.
 * @param {FormData} form
 * @param {boolean} keepHeld
 */
function appendAmend(form, keepHeld) {
    if (openedHeldJobId) form.append('amend_job_id', openedHeldJobId);
    if (keepHeld) form.append('hold', 'true');
    return form;
}

/**
 * Stop treating the composer as editing a held job, and update the buttons.
 */
function clearOpenedJob() {
    openedHeldJobId = null;
    if (typeof refreshComposerMode === 'function') refreshComposerMode();
}

/**
 * Show or hide the "editing a held label" affordances on every compose form.
 *
 * Injected rather than written into index.html five times: the forms differ
 * only in their fields, and a Save button that must appear beside each of five
 * submit buttons is exactly the sort of thing that rots when a sixth is added.
 *
 * The Save button is type=button and carries data-save-held, so the form's
 * own submit handler (which prints) is not what runs; the click handler in
 * core.js re-submits with hold=true instead.
 */
function refreshComposerMode() {
    const editing = openedHeldJobId != null;

    document.querySelectorAll('form').forEach(form => {
        const submitBtn = form.querySelector('button[type="submit"].btn-print');
        if (!submitBtn) return;

        let saveBtn = form.querySelector('[data-save-held]');
        let banner = form.querySelector('[data-editing-banner]');

        if (!editing) {
            if (saveBtn) saveBtn.remove();
            if (banner) banner.remove();
            if (submitBtn.dataset.printLabel) {
                submitBtn.innerHTML = submitBtn.dataset.printLabel;
                delete submitBtn.dataset.printLabel;
            }
            return;
        }

        if (!banner) {
            banner = document.createElement('div');
            banner.setAttribute('data-editing-banner', '');
            banner.className = 'editing-banner';
            banner.innerHTML =
                '<i class="bi bi-pencil-square"></i> Editing a held label — ' +
                '<button type="button" class="btn-link" data-discard-held>' +
                'stop editing</button>';
            submitBtn.parentNode.insertBefore(banner, submitBtn);
        }

        if (!saveBtn) {
            // Remember the print button's own wording so it can be restored.
            submitBtn.dataset.printLabel = submitBtn.innerHTML;
            submitBtn.innerHTML =
                '<i class="bi bi-printer-fill"></i> Print now';

            saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.setAttribute('data-save-held', '');
            saveBtn.className = 'btn-print btn-secondary';
            saveBtn.innerHTML = '<i class="bi bi-check2"></i> Save changes';
            submitBtn.parentNode.insertBefore(saveBtn, submitBtn);
        }
    });
}

/**
 * Read a panel's copies value (clamped to a sane integer >= 1).
 * @param {string} copiesId - element id of the panel's copies input
 * @returns {number}
 */
function readCopies(copiesId) {
    const el = document.getElementById(copiesId);
    const value = parseInt(el && el.value, 10);
    return Number.isFinite(value) && value >= 1 ? value : 1;
}

/**
 * If the requested copies meet the large-batch threshold, ask the user to
 * confirm. Returns true when it is safe to proceed (either below threshold or
 * the user confirmed), false when the user cancelled.
 * @param {number} copies
 * @returns {Promise<boolean>}
 */
async function confirmLargeBatch(copies) {
    if (copies < LARGE_BATCH_THRESHOLD) return true;
    return confirmDialog(
        `You are about to print ${copies} copies. Print more than 10 copies?`,
        { title: 'Confirm large batch', confirmLabel: 'Print' }
    );
}

/**
 * Parse an error response body and throw an Error carrying its message. Adds a
 * clear message when the backend reports CONFIRMATION_REQUIRED.
 * @param {Response} response
 */
async function throwPrintError(response) {
    let message = `Error: ${response.status}`;
    try {
        const errorData = await response.json();
        if (response.status === 400 && errorData.code === 'CONFIRMATION_REQUIRED') {
            throw new Error(errorData.message || 'Confirmation required for this many copies');
        }
        message = errorData.message || message;
    } catch (e) {
        if (e instanceof Error && e.message && response.status === 400) throw e;
        // Non-JSON body: keep the generic message.
    }
    throw new Error(message);
}

/**
 * Load settings from the API
 */
async function loadSettings() {
    try {
        const response = await fetch('/api/v1/settings');
        if (!response.ok) {
            throw new Error(`Failed to load settings: ${response.status}`);
        }
        
        const settings = await response.json();
        
        // Populate settings form
        document.getElementById('printer-uri').value = settings.printer_uri || '';
        document.getElementById('printer-model').value = settings.printer_model || '';
        // Label Type and Rotation no longer have Settings controls -- they are
        // per-job choices in the preview bar. The saved values are still the
        // defaults the server hands to API callers, so they are kept here for
        // the accessors to fall back on rather than written to a dead field.
        savedLabelSize = settings.label_size || '62';
        document.getElementById('text-font-size').value = settings.font_size || '50';
        document.getElementById('text-alignment').value = settings.alignment || 'left';
        const valignEl = document.getElementById('text-vertical-alignment');
        if (valignEl) valignEl.value = settings.vertical_alignment || 'top';
        const brokerUrlEl = document.getElementById('canva-broker-url');
        if (brokerUrlEl) brokerUrlEl.value = settings.canva_broker_url || '';
        const brokerTokenEl = document.getElementById('canva-broker-token');
        if (brokerTokenEl) brokerTokenEl.value = settings.canva_broker_token || '';
        savedRotate = settings.rotate != null ? String(settings.rotate) : '0';
        document.getElementById('threshold').value = settings.threshold || '70';
        document.getElementById('dither').value = settings.dither ? 'true' : 'false';
        document.getElementById('red').value = settings.red ? 'true' : 'false';
        // Apply the saved copies/cut defaults to every compose panel.
        const defaultCopies = settings.copies || 1;
        const defaultCutMode = settings.cut_mode || 'each';
        ['copies', 'copies-image', 'copies-qrcode', 'copies-label', 'copies-pdf', 'copies-textimage'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = defaultCopies;
        });
        ['cut-mode', 'cut-mode-image', 'cut-mode-qrcode', 'cut-mode-label', 'cut-mode-pdf', 'cut-mode-textimage'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = defaultCutMode;
        });
        document.getElementById('dpi-600').value = settings.dpi_600 ? 'true' : 'false';
        document.getElementById('keep-alive-enabled').value = settings.keep_alive_enabled ? 'true' : 'false';
        document.getElementById('keep-alive-interval').value = settings.keep_alive_interval || '60';

        // Keep alive mode + duration (derive a sensible value+unit for display)
        document.getElementById('keep-alive-mode').value = settings.keep_alive_mode || 'forever';
        applyKeepAliveDuration(settings.keep_alive_duration_seconds);
        // Reflect the current mode in the duration controls' visibility/state.
        updateKeepAliveModeUI();

        // Also check the current keep alive status
        loadKeepAliveStatus();
        
        console.log('Settings loaded successfully');
    } catch (error) {
        console.error('Error loading settings:', error);
        showNotification('Error loading settings', 'error');
    }
}

/**
 * Derive a sensible value + unit from a keep_alive_duration_seconds value and
 * populate the duration input/unit fields. Prefers hours when the value divides
 * evenly by 3600, otherwise falls back to minutes.
 * @param {number} seconds
 */
function applyKeepAliveDuration(seconds) {
    const valueEl = document.getElementById('keep-alive-duration-value');
    const unitEl = document.getElementById('keep-alive-duration-unit');
    if (!valueEl || !unitEl) return;

    const total = (typeof seconds === 'number' && seconds >= 0) ? seconds : 7200;

    if (total > 0 && total % 3600 === 0) {
        valueEl.value = String(total / 3600);
        unitEl.value = 'hours';
    } else {
        valueEl.value = String(Math.round(total / 60));
        unitEl.value = 'minutes';
    }
}

/**
 * Toggle the visibility / disabled state of the keep-alive duration controls
 * based on the selected keep-alive mode. The duration only applies in "timed"
 * mode, so it is hidden + disabled in "forever" mode.
 */
function updateKeepAliveModeUI() {
    const modeEl = document.getElementById('keep-alive-mode');
    const durationField = document.getElementById('keep-alive-duration-field');
    const valueEl = document.getElementById('keep-alive-duration-value');
    const unitEl = document.getElementById('keep-alive-duration-unit');
    if (!modeEl || !durationField) return;

    const timed = modeEl.value === 'timed';
    durationField.style.display = timed ? '' : 'none';
    if (valueEl) valueEl.disabled = !timed;
    if (unitEl) unitEl.disabled = !timed;
}

/**
 * Load keep alive status from the API
 */
async function loadKeepAliveStatus() {
    try {
        const response = await fetch('/api/v1/printers/keep-alive');
        if (!response.ok) {
            throw new Error(`Failed to load keep alive status: ${response.status}`);
        }
        
        const status = await response.json();

        // Reflect state in the always-visible navbar pill
        updateKeepAlivePill(status);

        // Update status indicator
        const keepAliveEnabled = document.getElementById('keep-alive-enabled');
        const statusText = status.running ?
            'Keep alive is active and running' :
            'Keep alive is not running';
        
        // Add a status indicator below the keep alive controls
        const statusIndicator = document.createElement('div');
        statusIndicator.id = 'keep-alive-status';
        statusIndicator.className = status.running ? 'text-success mt-2' : 'text-muted mt-2';
        statusIndicator.innerHTML = `<i class="bi ${status.running ? 'bi-check-circle-fill' : 'bi-x-circle-fill'} me-1"></i> ${statusText}`;
        
        // Replace existing status indicator if it exists
        const existingStatus = document.getElementById('keep-alive-status');
        if (existingStatus) {
            existingStatus.replaceWith(statusIndicator);
        } else {
            // Find the parent element to append the status indicator
            const keepAliveParent = keepAliveEnabled.closest('.col-md-6');
            keepAliveParent.appendChild(statusIndicator);
        }
        
        console.log('Keep alive status loaded successfully', status);
    } catch (error) {
        console.error('Error loading keep alive status:', error);
    }
}

/**
 * Update the always-visible navbar keep-alive pill to mirror the current state.
 * @param {{enabled?: boolean, running?: boolean}} status
 */
function updateKeepAlivePill(status) {
    const pill = document.getElementById('navbar-keepalive');
    const label = document.getElementById('keepalive-indicator');
    if (!pill || !label) return;
    const running = !!(status && status.running);
    pill.classList.toggle('ka-active', running);
    pill.setAttribute('aria-pressed', running ? 'true' : 'false');
    pill.title = running ? 'Keep alive is running — click to turn off' : 'Keep alive is off — click to turn on';
    label.textContent = running ? 'Keep Alive: On' : 'Keep Alive: Off';
}

/**
 * Toggle keep alive on/off from the navbar pill. Reuses the keep-alive interval
 * configured in Settings (falling back to 60s) and refreshes the pill afterwards.
 */
async function toggleKeepAliveFromNavbar() {
    const pill = document.getElementById('navbar-keepalive');
    if (!pill) return;
    const turnOn = !pill.classList.contains('ka-active');
    const intervalEl = document.getElementById('keep-alive-interval');
    let interval = parseInt(intervalEl && intervalEl.value, 10);
    if (!Number.isFinite(interval) || interval < 10) interval = 60;

    pill.classList.add('busy');
    try {
        await updateKeepAlive(turnOn, interval);
        // Keep the Settings dropdown in sync if it is present in the DOM
        const enabledSel = document.getElementById('keep-alive-enabled');
        if (enabledSel) enabledSel.value = turnOn ? 'true' : 'false';
    } finally {
        pill.classList.remove('busy');
        // Re-sync the pill with the real server state (covers failed toggles too)
        loadKeepAliveStatus();
    }
}

/**
 * Check printer status
 */
async function checkPrinterStatus() {
    const statusResult = document.getElementById('status-result');
    const statusIndicator = document.getElementById('status-indicator');
    const navbarStatusBtn = document.getElementById('navbar-check-status');
    
    // Show loading state
    if (statusResult) {
        statusResult.innerHTML = '<div class="d-flex justify-content-center"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div></div>';
    }
    if (statusIndicator) {
        statusIndicator.textContent = 'Checking...';
    }
    
    try {
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        
        if (!printerUri || !printerModel) {
            throw new Error('Printer URI and model are required');
        }
        
        const response = await fetch('/api/v1/printers/status', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                printer_uri: printerUri,
                printer_model: printerModel
            })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || `Error: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.available) {
            // Update status result in modal
            if (statusResult) {
                statusResult.innerHTML = `
                    <div class="alert alert-success">
                        <div class="d-flex align-items-center">
                            <i class="bi bi-check-circle-fill me-2 fs-4"></i>
                            <div>
                                <strong>Printer is available</strong><br>
                                ${data.status}
                            </div>
                        </div>
                    </div>
                `;
            }
            
            // Update navbar status indicator
            if (statusIndicator) {
                statusIndicator.textContent = 'Online';
            }
            if (navbarStatusBtn) {
                navbarStatusBtn.classList.remove('offline');
                navbarStatusBtn.classList.add('online');
            }
        } else {
            // Update status result in modal
            if (statusResult) {
                statusResult.innerHTML = `
                    <div class="alert alert-warning">
                        <div class="d-flex align-items-center">
                            <i class="bi bi-exclamation-triangle-fill me-2 fs-4"></i>
                            <div>
                                <strong>Printer is not available</strong><br>
                                ${data.status}
                            </div>
                        </div>
                    </div>
                `;
            }
            
            // Update navbar status indicator
            if (statusIndicator) {
                statusIndicator.textContent = 'Offline';
            }
            if (navbarStatusBtn) {
                navbarStatusBtn.classList.remove('online');
                navbarStatusBtn.classList.add('offline');
            }
        }
    } catch (error) {
        console.error('Error checking printer status:', error);
        
        // Update status result in modal
        if (statusResult) {
            // Check if it's a connection error
            const isConnectionError = error.message.includes('Connection refused');
            
            statusResult.innerHTML = `
                <div class="alert alert-danger">
                    <div class="d-flex align-items-center">
                        <i class="bi bi-x-circle-fill me-2 fs-4"></i>
                        <div>
                            <strong>${isConnectionError ? 'Connection Error' : 'Error'}</strong><br>
                            ${error.message}
                            ${isConnectionError ? '<br><br>Please check that:<ul class="mb-0 ps-3"><li>The printer is turned on</li><li>The printer is connected to the network</li><li>The IP address is correct</li></ul>' : ''}
                        </div>
                    </div>
                </div>
            `;
        }
        
        // Update navbar status indicator
        if (statusIndicator) {
            statusIndicator.textContent = 'Error';
        }
        if (navbarStatusBtn) {
            navbarStatusBtn.classList.remove('online');
            navbarStatusBtn.classList.add('offline');
        }
    }
}

/**
 * Handle text print form submission
 * @param {Event} event - Form submit event
 */
async function handleTextPrint(event) {
    event.preventDefault();
    
    try {
        const text = document.getElementById('text-input').value;
        const fontSize = document.getElementById('text-font-size').value;
        const alignment = document.getElementById('text-alignment').value;
        const verticalAlignment = readVerticalAlignment();
        
        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';
        
        if (!text) {
            throw new Error('Text is required');
        }

        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const copies = readCopies('copies');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        const requestBody = {
            text: text,
            settings: {
                printer_uri: printerUri,
                printer_model: printerModel,
                label_size: labelSize,
                font_size: parseInt(fontSize),
                alignment: alignment,
                vertical_alignment: verticalAlignment,
                rotate: parseInt(rotate),
                rotate_mode: activeRotateMode(),
                threshold: parseFloat(threshold),
                dither: dither,
                red: red,
                copies: copies,
                cut_mode: document.getElementById('cut-mode').value,
                dpi_600: document.getElementById('dpi-600').value === 'true'
            }
        };
        if (copies >= LARGE_BATCH_THRESHOLD) {
            requestBody.confirm_large_batch = true;
        }

        // Save-vs-print: withAmend adds amend_job_id when a held job is open,
        // so either path updates that job rather than adding another.
        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        withAmend(requestBody, keepHeld);

        const response = await fetch('/api/v1/text/print', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }

        const data = await response.json();

        showNotification(data.held ? 'Held label updated' : 'Added to print queue',
                         'success');
        console.log('Print result:', data);
        // Printing ends the edit; saving keeps it open for another pass.
        if (!keepHeld) clearOpenedJob();
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing text:', error);
        showNotification(`Error printing text: ${error.message}`, 'error');
    }
}

/**
 * Handle image print form submission
 * @param {Event} event - Form submit event
 */
async function handleImagePrint(event) {
    event.preventDefault();
    
    try {
        const imageInput = document.getElementById('image-input');
        const imageMode = document.getElementById('image-mode');
        
        if (!imageInput.files || imageInput.files.length === 0) {
            throw new Error('No image selected');
        }
        
        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        
        // Determine dithering based on image mode
        let dither = document.getElementById('dither').value === 'true';
        if (imageMode.value === 'bw-dither') {
            dither = true;
        } else if (imageMode.value === 'bw') {
            dither = false;
        }
        
        const red = document.getElementById('red').value === 'true';
        
        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const copies = readCopies('copies-image');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        const formData = new FormData();
        formData.append('image', imageInput.files[0]);
        formData.append('settings', JSON.stringify({
            printer_uri: printerUri,
            printer_model: printerModel,
            label_size: labelSize,
            rotate: parseInt(rotate),
            rotate_mode: activeRotateMode(),
            threshold: parseFloat(threshold),
            dither: dither,
            red: red,
            copies: copies,
            cut_mode: document.getElementById('cut-mode-image').value,
            dpi_600: document.getElementById('dpi-600').value === 'true',
            // Same control the preview reads, so the two cannot disagree.
            scale_mode: (document.getElementById('image-scale-mode') || {}).value || 'actual',
            image_mode: imageMode.value
        }));
        if (copies >= LARGE_BATCH_THRESHOLD) {
            formData.append('confirm_large_batch', 'true');
        }

        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        appendAmend(formData, keepHeld);
        const response = await fetch('/api/v1/image/print', {
            method: 'POST',
            body: formData
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }

        const data = await response.json();

        showNotification(data.held ? 'Held label updated' : 'Added to print queue', 'success');
        if (!keepHeld) clearOpenedJob();
        console.log('Print result:', data);
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing image:', error);
        showNotification(`Error printing image: ${error.message}`, 'error');
    }
}

/**
 * Handle PDF print form submission
 * @param {Event} event - Form submit event
 */
async function handlePdfPrint(event) {
    event.preventDefault();

    try {
        const pdfInput = document.getElementById('pdf-input');

        if (!pdfInput.files || pdfInput.files.length === 0) {
            throw new Error('No PDF selected');
        }

        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';

        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const pages = document.getElementById('pdf-pages').value;
        const scaleMode = document.getElementById('pdf-scale-mode').value;

        const copies = readCopies('copies-pdf');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        const formData = new FormData();
        formData.append('file', pdfInput.files[0]);
        formData.append('settings', JSON.stringify({
            printer_uri: printerUri,
            printer_model: printerModel,
            label_size: labelSize,
            rotate: parseInt(rotate),
            rotate_mode: activeRotateMode(),
            threshold: parseFloat(threshold),
            dither: dither,
            red: red,
            copies: copies,
            cut_mode: document.getElementById('cut-mode-pdf').value,
            dpi_600: document.getElementById('dpi-600').value === 'true'
        }));
        formData.append('pages', pages);
        formData.append('scale_mode', scaleMode);
        if (copies >= LARGE_BATCH_THRESHOLD) {
            formData.append('confirm_large_batch', 'true');
        }

        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        appendAmend(formData, keepHeld);
        const response = await fetch('/api/v1/pdf/print', {
            method: 'POST',
            body: formData
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }

        const data = await response.json();

        showNotification(data.held ? 'Held label updated' : 'Added to print queue', 'success');
        if (!keepHeld) clearOpenedJob();
        console.log('Print result:', data);
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing PDF:', error);
        showNotification(`Error printing PDF: ${error.message}`, 'error');
    }
}

// Holds the AbortController of the in-flight PDF preview request so that
// rapidly firing triggers (file change + debounced page input) cannot leave
// stale results on screen.
let pdfPreviewController = null;

/**
 * Hide the PDF preview container and clear its contents. Optionally restore the
 * placeholder if no other preview is currently visible.
 */
function clearPdfPreview() {
    const pdfPreview = document.getElementById('pdf-preview');
    const pdfPreviewPages = document.getElementById('pdf-preview-pages');
    const pdfPreviewNotice = document.getElementById('pdf-preview-notice');
    const previewPlaceholder = document.getElementById('preview-placeholder');

    if (pdfPreviewPages) pdfPreviewPages.innerHTML = '';
    if (pdfPreviewNotice) {
        pdfPreviewNotice.textContent = '';
        pdfPreviewNotice.classList.add('d-none');
    }
    if (pdfPreview) pdfPreview.classList.add('d-none');

    // Restore the placeholder if nothing else is shown.
    if (previewPlaceholder &&
        typeof areAllPreviewsEmpty === 'function' &&
        areAllPreviewsEmpty()) {
        previewPlaceholder.classList.remove('d-none');
    }
}

/**
 * Render a server-side PDF preview for the currently selected file.
 * Reads the file from #pdf-input and the page selection from #pdf-pages,
 * POSTs them to /api/v1/pdf/preview and renders the returned thumbnails.
 */
async function previewPdf() {
    const pdfInput = document.getElementById('pdf-input');
    const pdfPreview = document.getElementById('pdf-preview');
    const pdfPreviewPages = document.getElementById('pdf-preview-pages');
    const pdfPreviewNotice = document.getElementById('pdf-preview-notice');
    const previewPlaceholder = document.getElementById('preview-placeholder');

    if (!pdfInput || !pdfPreview || !pdfPreviewPages) return;

    // No file -> clear and hide the preview.
    if (!pdfInput.files || pdfInput.files.length === 0) {
        clearPdfPreview();
        return;
    }

    const pages = document.getElementById('pdf-pages')
        ? document.getElementById('pdf-pages').value
        : '';

    // Abort any preview request still in flight so its (older) response can be
    // ignored and never overwrites a newer one.
    if (pdfPreviewController) {
        pdfPreviewController.abort();
    }
    const controller = new AbortController();
    pdfPreviewController = controller;

    // Loading state.
    pdfPreviewPages.innerHTML =
        '<div class="d-flex justify-content-center py-4">' +
        '<div class="spinner-border text-primary" role="status">' +
        '<span class="visually-hidden">Loading...</span></div></div>';
    if (pdfPreviewNotice) {
        pdfPreviewNotice.textContent = '';
        pdfPreviewNotice.classList.add('d-none');
    }
    pdfPreview.classList.remove('d-none');
    if (previewPlaceholder) previewPlaceholder.classList.add('d-none');
    // Hide the other previews while showing the PDF preview.
    if (typeof hideOtherPreviews === 'function') {
        hideOtherPreviews('pdf-preview');
    }

    try {
        const formData = new FormData();
        formData.append('file', pdfInput.files[0]);
        formData.append('pages', pages || '');

        const response = await fetch('/api/v1/pdf/preview', {
            method: 'POST',
            body: formData,
            signal: controller.signal
        });

        // A newer request started while this one was running: ignore this result.
        if (pdfPreviewController !== controller) return;

        if (!response.ok) {
            let message = `Error: ${response.status}`;
            try {
                const errorData = await response.json();
                message = errorData.message || message;
            } catch (e) {
                // Response body was not JSON; keep the generic message.
            }
            clearPdfPreview();
            showNotification(`PDF preview error: ${message}`, 'error');
            return;
        }

        const data = await response.json();

        // Render thumbnails.
        pdfPreviewPages.innerHTML = '';

        const previews = Array.isArray(data.previews) ? data.previews : [];
        previews.forEach(preview => {
            const wrapper = document.createElement('div');
            wrapper.className = 'pdf-preview-page text-center';

            const label = document.createElement('div');
            label.className = 'pdf-preview-page-label text-muted small mb-1';
            label.textContent = `Page ${preview.page}`;

            const img = document.createElement('img');
            img.className = 'pdf-preview-thumb img-fluid border rounded';
            img.alt = `Page ${preview.page}`;
            img.src = preview.image;
            img.style.maxWidth = '100%';

            wrapper.appendChild(label);
            wrapper.appendChild(img);
            pdfPreviewPages.appendChild(wrapper);
        });

        // Truncation notice.
        if (pdfPreviewNotice) {
            if (data.truncated) {
                const shown = previews.length;
                const total = data.total_pages != null ? data.total_pages : shown;
                pdfPreviewNotice.textContent =
                    `Showing first ${shown} of ${total} pages`;
                pdfPreviewNotice.classList.remove('d-none');
            } else {
                pdfPreviewNotice.textContent = '';
                pdfPreviewNotice.classList.add('d-none');
            }
        }

        if (previews.length === 0) {
            // Nothing to show -> fall back to a clean/hidden state.
            clearPdfPreview();
            return;
        }

        pdfPreview.classList.remove('d-none');
        if (previewPlaceholder) previewPlaceholder.classList.add('d-none');
    } catch (error) {
        // Ignore aborts triggered by a newer request.
        if (error && error.name === 'AbortError') return;
        console.error('Error generating PDF preview:', error);
        clearPdfPreview();
        showNotification(`PDF preview error: ${error.message}`, 'error');
    } finally {
        if (pdfPreviewController === controller) {
            pdfPreviewController = null;
        }
    }
}

/**
 * Handle QR code print form submission
 * @param {Event} event - Form submit event
 */
async function handleQRCodePrint(event) {
    event.preventDefault();
    
    try {
        const qrData = document.getElementById('qr-data').value;
        const qrSize = document.getElementById('qr-size').value;
        const qrErrorCorrection = document.getElementById('qr-error-correction').value;
        const qrShowText = document.getElementById('qr-show-text').checked;
        const qrTextContent = document.getElementById('qr-text-content').value;
        const qrTextPosition = document.getElementById('qr-text-position').value;
        const qrTextFontSize = document.getElementById('qr-text-font-size').value;
        const qrTextAlignment = document.getElementById('qr-text-alignment').value;
        
        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';
        
        if (!qrData) {
            throw new Error('QR code data is required');
        }
        
        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const copies = readCopies('copies-qrcode');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        // Prepare request body with new API structure
        const requestBody = {
            qr: {
                data: qrData,
                size: parseInt(qrSize),
                error_correction: qrErrorCorrection,
                version: 1,
                box_size: 10,
                border: 4
            },
            settings: {
                printer_uri: printerUri,
                printer_model: printerModel,
                label_size: labelSize,
                rotate: parseInt(rotate),
                rotate_mode: activeRotateMode(),
                threshold: parseFloat(threshold),
                dither: dither,
                red: red,
                copies: copies,
                cut_mode: document.getElementById('cut-mode-qrcode').value,
                dpi_600: document.getElementById('dpi-600').value === 'true'
            }
        };

        // Add text settings if needed
        if (qrShowText && qrTextContent) {
            requestBody.text = {
                content: qrTextContent,
                position: qrTextPosition,
                font_size: parseInt(qrTextFontSize),
                alignment: qrTextAlignment
            };
        }
        if (copies >= LARGE_BATCH_THRESHOLD) {
            requestBody.confirm_large_batch = true;
        }

        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        withAmend(requestBody, keepHeld);
        const response = await fetch('/api/v1/qrcode/print', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }

        const data = await response.json();

        showNotification(data.held ? 'Held label updated' : 'Added to print queue', 'success');
        if (!keepHeld) clearOpenedJob();
        console.log('Print result:', data);
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing QR code:', error);
        showNotification(`Error printing QR code: ${error.message}`, 'error');
    }
}

/**
 * Handle label print form submission
 * @param {Event} event - Form submit event
 */
async function handleLabelPrint(event) {
    event.preventDefault();
    
    try {
        const labelQrData = document.getElementById('label-qr-data').value;
        const labelQrPosition = document.getElementById('label-qr-position').value;
        const labelQrErrorCorrection = document.getElementById('label-qr-error-correction').value;
        const labelTextContent = document.getElementById('label-text-content').value;
        const labelTextFontSize = document.getElementById('label-text-font-size').value;
        const labelTextAlignment = document.getElementById('label-text-alignment').value;
        
        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';
        
        if (!labelQrData) {
            throw new Error('QR code data is required');
        }
        
        if (!labelTextContent) {
            throw new Error('Text content is required');
        }
        
        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const copies = readCopies('copies-label');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        // Prepare request body with new API structure
        const requestBody = {
            qr: {
                data: labelQrData,
                position: labelQrPosition,
                size: 400,
                error_correction: labelQrErrorCorrection || 'M',
                version: 1,
                box_size: 10,
                border: 4
            },
            text: {
                content: labelTextContent,
                font_size: parseInt(labelTextFontSize),
                alignment: labelTextAlignment
            },
            settings: {
                printer_uri: printerUri,
                printer_model: printerModel,
                label_size: labelSize,
                rotate: parseInt(rotate),
                rotate_mode: activeRotateMode(),
                threshold: parseFloat(threshold),
                dither: dither,
                red: red,
                copies: copies,
                cut_mode: document.getElementById('cut-mode-label').value,
                dpi_600: document.getElementById('dpi-600').value === 'true'
            }
        };
        if (copies >= LARGE_BATCH_THRESHOLD) {
            requestBody.confirm_large_batch = true;
        }

        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        withAmend(requestBody, keepHeld);
        const response = await fetch('/api/v1/label/text-qrcode', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }
        
        const data = await response.json();
        
        showNotification(data.held ? 'Held label updated' : 'Added to print queue', 'success');
        if (!keepHeld) clearOpenedJob();
        console.log('Print result:', data);
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing label:', error);
        showNotification(`Error printing label: ${error.message}`, 'error');
    }
}

/**
 * Handle text + image print form submission
 * @param {Event} event - Form submit event
 */
async function handleTextImagePrint(event) {
    event.preventDefault();

    try {
        const imageInput = document.getElementById('textimage-input');
        const text = document.getElementById('textimage-text').value;
        const fontSize = document.getElementById('textimage-font-size').value;
        const alignment = document.getElementById('textimage-alignment').value;
        const position = document.getElementById('textimage-position').value;

        if (!imageInput.files || imageInput.files.length === 0) {
            throw new Error('No image selected');
        }

        if (!text) {
            throw new Error('Text is required');
        }

        // Get printer settings
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';

        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer settings are incomplete');
        }

        const copies = readCopies('copies-textimage');
        if (!await confirmLargeBatch(copies)) return;

        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Printing...';

        const formData = new FormData();
        formData.append('image', imageInput.files[0]);
        formData.append('text', text);
        formData.append('font_size', fontSize);
        formData.append('alignment', alignment);
        formData.append('position', position);
        formData.append('settings', JSON.stringify({
            printer_uri: printerUri,
            printer_model: printerModel,
            label_size: labelSize,
            rotate: parseInt(rotate),
            rotate_mode: activeRotateMode(),
            threshold: parseFloat(threshold),
            dither: dither,
            red: red,
            copies: copies,
            cut_mode: document.getElementById('cut-mode-textimage').value,
            dpi_600: document.getElementById('dpi-600').value === 'true'
        }));
        if (copies >= LARGE_BATCH_THRESHOLD) {
            formData.append('confirm_large_batch', 'true');
        }

        const keepHeld = window.saveHeldOnly === true;
        window.saveHeldOnly = false;
        appendAmend(formData, keepHeld);
        const response = await fetch('/api/v1/label/text-image', {
            method: 'POST',
            body: formData
        });

        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;

        if (!response.ok) {
            await throwPrintError(response);
        }

        const data = await response.json();

        showNotification(data.held ? 'Held label updated' : 'Added to print queue', 'success');
        if (!keepHeld) clearOpenedJob();
        console.log('Print result:', data);
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        console.error('Error printing text + image:', error);
        showNotification(`Error printing text + image: ${error.message}`, 'error');
    }
}

/**
 * Handle save settings form submission
 * @param {Event} event - Form submit event
 */
async function handleSaveSettings(event) {
    event.preventDefault();
    
    try {
        const printerUri = document.getElementById('printer-uri').value;
        const printerModel = document.getElementById('printer-model').value;
        // activeLabelSize() for the same reason as activeRotate() above.
        const labelSize = activeLabelSize();
        const fontSize = document.getElementById('text-font-size').value;
        const alignment = document.getElementById('text-alignment').value;
        const verticalAlignment = readVerticalAlignment();
        // activeRotate(), NOT #rotate: the ROTATION control under the preview is
        // an override, and reading the Settings-page field directly meant the
        // preview honoured the change and the print ignored it.
        const rotate = activeRotate();
        const threshold = document.getElementById('threshold').value;
        const dither = document.getElementById('dither').value === 'true';
        const red = document.getElementById('red').value === 'true';
        const copies = parseInt(document.getElementById('copies').value) || 1;
        const cutMode = document.getElementById('cut-mode').value;
        const dpi600 = document.getElementById('dpi-600').value === 'true';
        const keepAliveEnabled = document.getElementById('keep-alive-enabled').value === 'true';
        const keepAliveInterval = parseInt(document.getElementById('keep-alive-interval').value);
        const keepAliveMode = document.getElementById('keep-alive-mode').value;
        const keepAliveDurationVal = parseInt(document.getElementById('keep-alive-duration-value').value) || 0;
        const keepAliveDurationUnit = document.getElementById('keep-alive-duration-unit').value;
        const keepAliveDurationSeconds = keepAliveDurationUnit === 'hours'
            ? keepAliveDurationVal * 3600
            : keepAliveDurationVal * 60;

        if (!printerUri || !printerModel || !labelSize) {
            throw new Error('Printer URI, model, and label size are required');
        }
        
        if (keepAliveInterval < 10) {
            throw new Error('Keep alive interval must be at least 10 seconds');
        }
        
        // Show loading state
        const submitBtn = event.target.querySelector('button[type="submit"]');
        const originalBtnText = submitBtn.innerHTML;
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Saving...';
        
        const response = await fetch('/api/v1/settings', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                printer_uri: printerUri,
                printer_model: printerModel,
                label_size: labelSize,
                font_size: parseInt(fontSize),
                alignment: alignment,
                vertical_alignment: verticalAlignment,
                rotate: parseInt(rotate),
                canva_broker_url: (document.getElementById('canva-broker-url') || {}).value || '',
                canva_broker_token: (document.getElementById('canva-broker-token') || {}).value || '',
                threshold: parseFloat(threshold),
                dither: dither,
                red: red,
                copies: copies,
                cut_mode: cutMode,
                dpi_600: dpi600,
                keep_alive_enabled: keepAliveEnabled,
                keep_alive_interval: keepAliveInterval,
                keep_alive_mode: keepAliveMode,
                keep_alive_duration_seconds: keepAliveDurationSeconds
            })
        });
        
        // Reset button state
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalBtnText;
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || `Error: ${response.status}`);
        }
        
        const data = await response.json();
        
        showNotification('Settings saved successfully', 'success');
        console.log('Settings saved:', data);
        
        // Update keep alive status based on new settings
        await updateKeepAlive(keepAliveEnabled, keepAliveInterval);
    } catch (error) {
        console.error('Error saving settings:', error);
        showNotification(`Error saving settings: ${error.message}`, 'error');
    }
}

/**
 * Read the text tab's vertical alignment, falling back to "top" when the
 * control is absent (older cached index.html) or holds an unexpected value.
 *
 * Only affects die-cut labels: continuous tape is cut to the height the text
 * needs, so there is no spare room to align within and the backend ignores it.
 * @returns {string} "top" | "middle" | "bottom"
 */
function readVerticalAlignment() {
    const el = document.getElementById('text-vertical-alignment');
    const value = el ? el.value : 'top';
    return ['top', 'middle', 'bottom'].includes(value) ? value : 'top';
}

// ===================== Canva browser ========================================
//
// Lists designs from a Canva folder and prints one. There is no local copy, no
// sync and no import step: a design is exported only when it is printed, and the
// resulting job behaves exactly like an uploaded image (reprint, open in the
// composer) because that is literally what the backend queues.
//
// Browsing costs no export quota -- Canva returns a thumbnail URL per item.

// Designs currently listed, by id, so an action can find its design without
// re-reading the DOM.
let canvaDesigns = {};

/**
 * Show or hide the Canva nav item based on whether the feature is usable, and
 * surface why when it is not.
 *
 * Called on load. A tab that is present but always errors is worse than no tab,
 * so it stays hidden until the backend says Canva is at least configured.
 */
async function initCanvaTab() {
    const navItem = document.getElementById('canva-tab');
    if (!navItem) return;

    let status;
    try {
        const response = await fetch('/api/v1/canva/status');
        status = response.ok ? await response.json() : null;
    } catch (error) {
        status = null;
    }

    if (!status || !status.configured) {
        navItem.classList.add('d-none');
        return;
    }

    navItem.classList.remove('d-none');

    // Configured but not authorized is worth showing: the fix is a one-time
    // visit to the broker's /auth, and hiding the tab would give no clue.
    if (!status.connected) {
        showCanvaNotice(status.message
            || 'Canva is configured but not authorized yet.');
    }
}

/**
 * Display an inline notice in the Canva panel (configuration/auth problems).
 * @param {string} message
 */
function showCanvaNotice(message) {
    const notice = document.getElementById('canva-notice');
    if (!notice) return;
    notice.textContent = message;
    notice.classList.remove('d-none');
}

function clearCanvaNotice() {
    const notice = document.getElementById('canva-notice');
    if (notice) notice.classList.add('d-none');
}

/**
 * Populate the folder picker. Canva has no "list all designs" endpoint, so a
 * folder has to be chosen before anything can be listed.
 */
async function loadCanvaFolders() {
    const select = document.getElementById('canva-folder');
    if (!select) return;

    try {
        const response = await fetch('/api/v1/canva/folders');
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            showCanvaNotice(body.message || 'Could not list Canva folders.');
            return;
        }
        const body = await response.json();
        const folders = body.folders || [];

        // Indent by nesting depth so one flat dropdown conveys the tree.
        // Figure spaces (U+2007) rather than &nbsp; or padding: option elements
        // ignore CSS padding in most browsers, and entities inside <option> are
        // unreliable, but a real space character always renders.
        select.innerHTML = '<option value="">Select a folder…</option>' +
            folders.map(f => {
                const indent = '\u2007\u2007'.repeat(Math.max(0, f.depth || 0));
                return `<option value="${escapeHtml(f.id)}">` +
                       `${indent}${escapeHtml(f.name)}</option>`;
            }).join('');

        if (!folders.length) {
            showCanvaNotice('No folders found in your Canva account.');
        } else {
            clearCanvaNotice();
        }
    } catch (error) {
        showCanvaNotice(`Could not list Canva folders: ${error.message}`);
    }
}

/**
 * List the designs in the selected folder and render the grid.
 */
async function loadCanvaDesigns() {
    const select = document.getElementById('canva-folder');
    const grid = document.getElementById('canva-grid');
    if (!select || !grid) return;

    const folderId = select.value;
    if (!folderId) {
        canvaDesigns = {};
        grid.innerHTML = '<div class="queue-empty"><i class="bi bi-palette"></i>' +
            '<p>Pick a folder to see its designs</p></div>';
        return;
    }

    grid.innerHTML = '<div class="d-flex justify-content-center py-4">' +
        '<div class="spinner-border text-primary" role="status">' +
        '<span class="visually-hidden">Loading…</span></div></div>';

    try {
        const response = await fetch(
            `/api/v1/canva/designs?folder_id=${encodeURIComponent(folderId)}`);
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            grid.innerHTML = '';
            showCanvaNotice(body.message || 'Could not list designs.');
            return;
        }
        const body = await response.json();
        renderCanvaDesigns(body.designs || []);
        clearCanvaNotice();
    } catch (error) {
        grid.innerHTML = '';
        showCanvaNotice(`Could not list designs: ${error.message}`);
    }
}

/**
 * Render the design grid.
 * @param {Array} designs
 */
function renderCanvaDesigns(designs) {
    const grid = document.getElementById('canva-grid');
    if (!grid) return;

    canvaDesigns = {};
    designs.forEach(d => { canvaDesigns[d.id] = d; });

    if (!designs.length) {
        grid.innerHTML = '<div class="queue-empty"><i class="bi bi-palette"></i>' +
            '<p>No designs in this folder</p></div>';
        return;
    }

    grid.innerHTML = designs.map(d => {
        const id = escapeHtml(d.id);
        const title = escapeHtml(d.title || 'Untitled');
        // Thumbnail URLs are short-lived, so they are used as-is and never
        // cached. A missing one is normal, not an error.
        const thumb = d.thumbnail
            ? `<img class="canva-card-thumb" src="${escapeHtml(d.thumbnail)}" alt="" loading="lazy">`
            : '<div class="canva-card-thumb is-missing"><i class="bi bi-image"></i></div>';
        const edit = d.edit_url
            ? `<a class="btn-ghost btn-sm" href="${escapeHtml(d.edit_url)}" target="_blank" rel="noopener noreferrer" title="Edit in Canva"><i class="bi bi-box-arrow-up-right"></i></a>`
            : '';
        return `
            <div class="canva-card" data-design-id="${id}">
              ${thumb}
              <div class="canva-card-body">
                <div class="canva-card-title">${title}</div>
                <div class="canva-card-actions">
                  <button type="button" class="btn-ghost btn-sm" data-canva-action="print" data-design-id="${id}">
                    <i class="bi bi-printer"></i> Print
                  </button>
                  <button type="button" class="btn-ghost btn-sm" data-canva-action="open" data-design-id="${id}">
                    <i class="bi bi-pencil-square"></i> Open
                  </button>
                  ${edit}
                </div>
              </div>
            </div>`;
    }).join('');
}

/**
 * Export a design and queue it for printing, using the current output settings.
 * @param {string} designId
 */
async function printCanvaDesign(designId) {
    const design = canvaDesigns[designId];
    const card = document.querySelector(`.canva-card[data-design-id="${designId}"]`);
    if (card) card.classList.add('is-busy');

    try {
        const response = await fetch('/api/v1/canva/print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                design_id: designId,
                title: design ? design.title : undefined,
                settings: collectPreviewSettings(),
            }),
        });
        const body = await response.json().catch(() => ({}));

        if (!response.ok) {
            // The media guard lives here too: a mismatched roll is rejected
            // before the export is spent, and its message names the fix.
            showNotification(body.message || 'Could not print design', 'error');
            return;
        }
        showNotification(
            `Queued "${design ? design.title : 'design'}" for printing`, 'success');
        if (typeof refreshJobs === 'function') refreshJobs();
    } catch (error) {
        showNotification(`Could not print design: ${error.message}`, 'error');
    } finally {
        if (card) card.classList.remove('is-busy');
    }
}

/**
 * Export a design and load it into the image composer WITHOUT printing, so it
 * can be adjusted first.
 *
 * Uses /canva/export rather than /canva/print: the queue has only a global
 * pause, so there is no way to enqueue a job that will not eventually run.
 * Opening must never print.
 * @param {string} designId
 */
async function openCanvaDesign(designId) {
    const design = canvaDesigns[designId];
    const card = document.querySelector(`.canva-card[data-design-id="${designId}"]`);
    if (card) card.classList.add('is-busy');

    try {
        const response = await fetch('/api/v1/canva/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                design_id: designId,
                title: design ? design.title : undefined,
            }),
        });
        const body = await response.json().catch(() => ({}));

        if (!response.ok || !body.image) {
            showNotification(body.message || 'Could not export design', 'error');
            return;
        }

        // Hand the PNG to the image tab as if it had been picked from disk, so
        // the existing preview/print path takes over unchanged.
        const loaded = await loadDataUrlIntoInput(
            body.image, 'image-input', body.filename || 'canva-design.png');
        if (!loaded) {
            showNotification('Could not load the design into the composer', 'error');
            return;
        }
        activateComposeTab('image-tab');
        dispatchOn('image-input', 'change');
        showNotification(
            `Opened "${design ? design.title : 'design'}" in the composer`, 'success');
    } catch (error) {
        showNotification(`Could not open design: ${error.message}`, 'error');
    } finally {
        if (card) card.classList.remove('is-busy');
    }
}

/**
 * Put a data URL into a file input as if the user had chosen it.
 * @param {string} dataUrl
 * @param {string} inputId
 * @param {string} filename
 * @returns {Promise<boolean>} whether the input now holds the file
 */
async function loadDataUrlIntoInput(dataUrl, inputId, filename) {
    const input = document.getElementById(inputId);
    if (!input) return false;
    try {
        const blob = await (await fetch(dataUrl)).blob();
        const file = new File([blob], filename, { type: blob.type || 'image/png' });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        return true;
    } catch (error) {
        console.error('Error loading data URL into input:', error);
        return false;
    }
}

// ===================== Output overrides (label size / rotation) ==============
//
// Label size and rotation live in Settings as the saved defaults, and are also
// exposed under the preview so they can be changed while composing -- the
// common case being "show me this on the roll I am about to load".
//
// These are OVERRIDES, not a second copy of the setting: they feed previews and
// prints issued from this screen, and are never written back to the saved
// configuration. Reading them through these two helpers keeps that rule in one
// place; call sites must not read #label-size / #rotate directly.

/**
 * The rotate mode for text on continuous tape.
 *   "image"  -- rotate the finished render (keeps text size)
 *   "layout" -- lay out lengthwise and scale up to fill the tape width
 * @returns {string}
 */
function activeRotateMode() {
    const el = document.getElementById('preview-rotate-mode');
    const value = el ? el.value : 'image';
    return ['image', 'layout'].includes(value) ? value : 'image';
}

/**
 * The label size the preview and any print from this screen should use.
 * Falls back to the saved setting when the override is absent (older cached
 * index.html) or empty.
 * @returns {string}
 */
function activeLabelSize() {
    const override = document.getElementById('preview-label-size');
    if (override && override.value) return override.value;
    return savedLabelSize;
}

/**
 * The rotation the preview and any print from this screen should use.
 * @returns {number} 0 | 90 | 180 | 270
 */
function activeRotate() {
    const override = document.getElementById('preview-rotate');
    const raw = (override && override.value !== '') ? override.value : savedRotate;
    const n = parseInt(raw, 10);
    return [0, 90, 180, 270].includes(n) ? n : 0;
}


// ---- Loaded media + print gating ------------------------------------------
//
// The app already asks the printer which roll it holds; before this that answer
// only ever appeared inside the Status modal. Showing it under the preview means
// the working screen finally states what is actually in the printer.
//
// The override exists so a label can be composed for a roll that is not loaded
// yet, so a mismatch is a normal intermediate state -- previews must keep
// rendering. Printing, however, cannot succeed: the server rejects a mismatched
// job with HTTP 400 (and would otherwise be silently discarded by the printer),
// so the print buttons are disabled with the reason spelled out, rather than
// letting the click fail.

// Most recent successful media reading: {width, length, sizes[]} or null when
// the printer could not be asked (asleep, unplugged, network printer).
let loadedMedia = null;

// Saved defaults for values that no longer have a Settings control. They still
// exist server-side -- API callers inherit them -- so the UI keeps them to fall
// back on when no per-job override is set.
let savedLabelSize = '62';
let savedRotate = '0';

/**
 * Ask the printer what roll it holds and update the readout + print gating.
 * Failure is not an error state here -- an unreadable printer just means the
 * UI cannot say, and must not block printing on a guess.
 */
async function refreshLoadedMedia() {
    const printerUri = document.getElementById('printer-uri');
    const printerModel = document.getElementById('printer-model');
    if (!printerUri || !printerUri.value) return;

    try {
        const response = await fetch('/api/v1/printers/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                printer_uri: printerUri.value,
                printer_model: printerModel ? printerModel.value : 'QL-800'
            })
        });
        const data = response.ok ? await response.json() : null;
        const d = data && data.details;
        // media_width_mm is only present for USB printers that answered.
        if (d && d.media_width_mm) {
            loadedMedia = {
                width: d.media_width_mm,
                length: d.media_length_mm || 0,
                sizes: d.loadable_label_sizes || [],
                errors: d.errors || []
            };
        } else {
            loadedMedia = null;
        }
    } catch (error) {
        loadedMedia = null;
    }
    // Seed the label selector from the roll actually loaded, ONCE, on the first
    // successful read -- the printer knowing what is in it is better than a
    // markup default, and it saves reaching for "Match loaded" every time.
    //
    // Only while untouched: once a roll has been chosen deliberately, a later
    // poll must not move it. Composing for a roll you are about to load is the
    // whole point of the override, and the media guard is what stops a mismatch
    // reaching the printer.
    const sel = document.getElementById('preview-label-size');
    if (sel && !sel.dataset.touched && loadedMedia
            && loadedMedia.sizes && loadedMedia.sizes.length) {
        const want = String(loadedMedia.sizes[0]);
        if (sel.value !== want) {
            sel.value = want;
            // The change listener sets dataset.touched, which is what stops a
            // later poll moving the selector again. Relying on that side effect
            // would be fragile, so mark it here too: seeding happens once, and
            // whatever is selected afterwards is the user's.
            sel.dataset.touched = '1';
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    updateOutputBar();
}

/**
 * Describe the loaded roll, e.g. "50mm continuous" or "62x29mm die-cut".
 */
function describeLoadedMedia(media) {
    if (!media) return null;
    return media.length
        ? `${media.width}x${media.length}mm die-cut`
        : `${media.width}mm continuous`;
}

/**
 * Whether the chosen label size can print on the loaded roll.
 *
 * Uses loadable_label_sizes, which the server derives from the printer's own
 * reported media -- the same source the server-side guard uses, so the button
 * state and the guard cannot disagree. Returns true when unknown: never block
 * on a guess.
 */
function labelSizeFitsLoaded(labelSize) {
    if (!loadedMedia || !loadedMedia.sizes || !loadedMedia.sizes.length) return true;
    return loadedMedia.sizes.includes(String(labelSize));
}

/**
 * Refresh the loaded-media readout, the "Match loaded" affordance, and whether
 * printing is allowed.
 */
function updateOutputBar() {
    const dot = document.getElementById('preview-loaded-dot');
    const text = document.getElementById('preview-loaded-text');
    const wrap = document.getElementById('preview-loaded-media');
    const matchBtn = document.getElementById('preview-match-loaded');
    if (!wrap || !text) return;

    const chosen = activeLabelSize();
    const description = describeLoadedMedia(loadedMedia);
    const fits = labelSizeFitsLoaded(chosen);

    wrap.classList.remove('is-match', 'is-mismatch');

    if (!description) {
        // Could not ask the printer. Say so plainly and leave printing enabled:
        // the server guard also lets an unreadable printer through, and the most
        // common cause is simply that the QL has gone to sleep.
        text.textContent = 'Printer not reporting media';
        if (matchBtn) matchBtn.classList.add('d-none');
        setPrintingBlocked(false);
        return;
    }

    if (fits) {
        wrap.classList.add('is-match');
        text.textContent = `Loaded: ${description}`;
        if (matchBtn) matchBtn.classList.add('d-none');
        setPrintingBlocked(false);
    } else {
        wrap.classList.add('is-mismatch');
        text.textContent = `Loaded: ${description} — ${chosen} will not print`;
        if (matchBtn) matchBtn.classList.remove('d-none');
        setPrintingBlocked(true, chosen, description);
    }
}

/**
 * Enable or disable every print button, explaining why when disabled.
 *
 * Previews are untouched: composing for a roll you have not loaded is the point
 * of the override. Only the irreversible action is gated.
 */
function setPrintingBlocked(blocked, chosen, description) {
    document.querySelectorAll('.btn-print').forEach(btn => {
        btn.disabled = !!blocked;
        if (blocked) {
            btn.title = `Load ${chosen} to print this, or choose a size that ` +
                        `fits the ${description} currently in the printer`;
            btn.classList.add('is-blocked');
        } else {
            btn.removeAttribute('title');
            btn.classList.remove('is-blocked');
        }
    });
}

/**
 * Set the label override to the roll actually loaded.
 */
function matchLoadedMedia() {
    if (!loadedMedia || !loadedMedia.sizes || !loadedMedia.sizes.length) return;
    const select = document.getElementById('preview-label-size');
    if (!select) return;
    select.value = loadedMedia.sizes[0];
    select.dispatchEvent(new Event('change', { bubbles: true }));
}

// ===================== Hybrid live server preview =====================
//
// The client-side preview (preview.js) updates instantly while typing. In
// addition, we debounce a request to the server which renders the EXACT print
// label as a PNG and overlays it on top of the instant client preview.
//
// A single shared debounce timer + AbortController ensure only the latest
// request "wins": older in-flight requests are aborted and stale responses are
// ignored.

let serverPreviewTimer = null;
let serverPreviewController = null;

/**
 * Collect the shared printer/render settings exactly like the print handlers
 * do, so the server preview matches the real print output.
 */
function collectPreviewSettings() {
    return {
        printer_uri: document.getElementById('printer-uri').value,
        printer_model: document.getElementById('printer-model').value,
        label_size: activeLabelSize(),
        rotate: activeRotate(),
        rotate_mode: activeRotateMode(),
        threshold: parseFloat(document.getElementById('threshold').value),
        dither: document.getElementById('dither').value === 'true',
        red: document.getElementById('red').value === 'true',
        copies: parseInt(document.getElementById('copies').value) || 1,
        cut_mode: document.getElementById('cut-mode').value,
        dpi_600: document.getElementById('dpi-600').value === 'true'
    };
}

/**
 * Physical size of a rendered label, from the PNG's own pixel dimensions.
 *
 * Derived from the image rather than looked up from the label id, so it stays
 * honest for every mode -- continuous tape whose length follows the text, a
 * die-cut roll's fixed size, or a lengthwise layout. The printer rasterises at
 * 300dpi, so 1mm is 300/25.4 px.
 */
const PX_PER_MM = 300 / 25.4;

/**
 * Show the rendered label's real dimensions, and which way it feeds.
 *
 * On screen a 29mm and a 62mm label look identical, so the caption is the only
 * thing that says how big the thing actually is.
 * @param {HTMLImageElement} img - the loaded server-preview image
 */
function updatePreviewDims(img) {
    const out = document.getElementById('preview-dims');
    if (!out) return;
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (!w || !h) {
        out.classList.add('d-none');
        return;
    }
    const wmm = Math.round(w / PX_PER_MM);
    const hmm = Math.round(h / PX_PER_MM);

    // Dimension lines, not a sentence. Which number is the tape and which is
    // the feed depends on rotation, so an unlabelled "2 x 47 mm" pair is
    // ambiguous -- it was read as a 47mm-long label when 47 was the tape width.
    // Ticks alongside each axis say what is being measured without prose.
    const chosenCap = labelSizeToMm(
        typeof activeLabelSize === 'function' ? activeLabelSize() : null);
    const tapeMm = (chosenCap && chosenCap.width)
        || (loadedMedia && loadedMedia.width);
    let dims;
    if (tapeMm) {
        const widthIsTape = Math.abs(wmm - tapeMm) < Math.abs(hmm - tapeMm);
        const acrossMm = widthIsTape ? wmm : hmm;
        const feedMm = widthIsTape ? hmm : wmm;
        // Across-tape stays in the caption; the feed measurement moves next to
        // the feed arrow, where it labels the axis it describes instead of
        // sitting in a pair the reader has to disambiguate.
        dims =
            `<span class="dim-axis"><span class="dim-tick">\u2194</span>` +
            `${acrossMm} mm<span class="dim-axis-name">across tape</span></span>`;
        const feedOut = document.getElementById('preview-feed-mm');
        if (feedOut) feedOut.textContent = `${feedMm} mm`;
    } else {
        dims = `${wmm} \u00d7 ${hmm} mm`;
    }

    out.innerHTML =
        dims +
        `<span class="dim-sep">|</span>` +
        `<span class="dim-px">${w} \u00d7 ${h} px @ 300dpi</span>`;
    out.classList.remove('d-none');
    updateFeedIndicator(img);
    warnIfOverflowsDieCut(wmm, hmm);
}

/**
 * Warn when a render will not fit the chosen die-cut label.
 *
 * Die-cut stock is a fixed size in BOTH axes. The image path scales artwork to
 * the label WIDTH and lets height follow the aspect ratio, which is right for
 * continuous tape and wrong here: a rotated or tall image produces a canvas
 * longer than the label, spanning several stickers. convert() then rejects it
 * with "Bad image dimensions", so nothing misprints -- but the preview drew the
 * oversized label as though it were real, which is the part worth fixing.
 *
 * Deliberately only a warning. Silently resizing to fit would be inventing a
 * layout the user did not ask for, and the print already fails safely.
 *
 * @param {number} wmm - rendered width in mm
 * @param {number} hmm - rendered height in mm
 */
function warnIfOverflowsDieCut(wmm, hmm) {
    const el = document.getElementById('preview-overflow-warning');
    if (!el) return;

    const chosen = labelSizeToMm(
        typeof activeLabelSize === 'function' ? activeLabelSize() : null);
    if (!chosen || !chosen.length) {
        el.classList.add('d-none');
        return;
    }

    // 1mm of slack for rounding; the render is reported in whole mm.
    const overflows = (wmm > chosen.width + 1) || (hmm > chosen.length + 1);
    el.textContent = overflows
        ? `This render is ${wmm}\u00d7${hmm} mm but the label is ` +
          `${chosen.width}\u00d7${chosen.length} mm \u2014 it spans more than one ` +
          `label and the printer will reject it.`
        : '';
    el.classList.toggle('d-none', !overflows);
}

/**
 * Point the feed arrow along the direction the label leaves the printer.
 *
 * This is ALWAYS the image's Y axis, not "whichever side is longer". The QL
 * rasterises one line at a time across the tape width, and the tape advances
 * per line -- so X is the tape width and Y is the feed direction, for every
 * label type. brother_ql's own geometry agrees: 62x29 is tape_size (62, 29) and
 * dots_printable (696, 271), i.e. 62mm across X and the 29mm length down Y.
 *
 * The arrow is therefore always vertical; only the "how long is that" reading
 * changes. Kept as a function so the class is applied in one place.
 */
function updateFeedIndicator(img) {
    const feed = document.getElementById('preview-feed');
    if (!feed) return;
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    if (!w || !h) {
        feed.classList.add('d-none');
        return;
    }

    feed.classList.add('is-vertical');
    feed.classList.remove('is-horizontal');

    // Sit just outside the label on the right, vertically centred, so it never
    // overlaps the render it annotates.
    feed.style.right = '14px';
    feed.style.top = '50%';
    feed.style.transform = 'translateY(-50%)';
    feed.classList.remove('d-none');
}

/**
 * Hide the dimensions caption and feed arrow (no server render on screen).
 */
function clearPreviewDims() {
    const out = document.getElementById('preview-dims');
    const feed = document.getElementById('preview-feed');
    if (out) out.classList.add('d-none');
    if (feed) feed.classList.add('d-none');
}


/**
 * Width and length in mm of a brother_ql label identifier.
 *
 * Identifiers are millimetre sizes: "50" and "62" are continuous (no length),
 * "62x29" is die-cut width x length. Returns null for anything unrecognised so
 * callers fall back to the loaded roll rather than drawing a wrong one.
 *
 * @param {string} id - e.g. "50", "62x29"
 * @returns {{width: number, length: number}|null}
 */
function labelSizeToMm(id) {
    if (!id) return null;
    const m = String(id).trim().match(/^(\d+)(?:x(\d+))?$/i);
    if (!m) return null;
    return { width: Number(m[1]), length: m[2] ? Number(m[2]) : 0 };
}

/**
 * Size the tape backdrop to the roll physically loaded, so the raster is shown
 * at true proportion against it.
 *
 * The point is that the printed area and the TAPE are different things. A
 * narrow raster on wide tape is a small mark with blank tape either side, not a
 * narrow label -- and the old preview, which styled the raster itself as the
 * label, made those two look identical. That is the single most confusing thing
 * about rotation: turning text 90 degrees produces a genuinely narrow raster,
 * and it used to look like the label had become a sliver.
 *
 * Falls back to sizing from the image (the previous behaviour) when the roll is
 * unknown -- an unreadable printer must not blank the preview.
 *
 * @param {HTMLImageElement} img - the loaded server-preview image
 */
function sizeTapeToMedia(img) {
    const tape = document.getElementById('preview-backing');
    const sticker = document.getElementById('preview-sticker');
    if (!tape || !img || !img.naturalWidth) return;

    // The roll the user is COMPOSING FOR, not the one in the machine. If the
    // preview silently redrew itself at the loaded width, choosing 62mm while
    // 50mm is loaded would show a 50mm label and contradict the selection. The
    // media guard already reports that mismatch in words and blocks the print;
    // the preview should not restate it by drawing the wrong label.
    const chosen = labelSizeToMm(
        typeof activeLabelSize === 'function' ? activeLabelSize() : null);
    const tapeMm = (chosen && chosen.width) || (loadedMedia && loadedMedia.width);
    if (!tapeMm) {
        if (sticker) sticker.style.removeProperty('--tape-aspect');
        return;
    }

    // The raster's own long axis, in mm, is how far it feeds down the roll.
    const rasterWmm = img.naturalWidth / PX_PER_MM;
    const rasterHmm = img.naturalHeight / PX_PER_MM;

    // Tape width is fixed; the feed direction is whichever axis is not the tape
    // width. Compare both against the known roll rather than assuming the
    // raster's wider side is the tape -- rotation makes that assumption wrong.
    const widthIsTape = Math.abs(rasterWmm - tapeMm) < Math.abs(rasterHmm - tapeMm);
    const feedMm = widthIsTape ? rasterHmm : rasterWmm;

    // Never collapse the box: a very short label still needs a visible strip of
    // tape, and aspect-ratio of 50/1 is unreadable.
    const shownFeedMm = Math.max(feedMm, tapeMm * 0.25);
    if (sticker) {
        sticker.style.setProperty('--tape-aspect',
            widthIsTape ? `${tapeMm} / ${shownFeedMm}` : `${shownFeedMm} / ${tapeMm}`);
    }

    // A raster that spans the tape needs no outline: there is no blank tape to
    // distinguish it from, and the tape's own edge already draws the boundary.
    // Most labels are full-width, so this keeps the common case clean and marks
    // only the case that was actually confusing -- a narrow mark on wide tape.
    const acrossMm = widthIsTape ? rasterWmm : rasterHmm;
    tape.classList.toggle('is-full-width', Math.abs(acrossMm - tapeMm) < 1);

    // Continuous ("endless") tape has no length -- the roll simply keeps going,
    // and the printer cuts wherever the label ends. Drawing a closed rectangle
    // implies a fixed sheet, so fade the feed edge to show the tape continues.
    // Die-cut rolls DO have a real edge there, so they keep the hard boundary.
    // Feed is ALWAYS down the screen, never derived from the raster's shape.
    //
    // Tape leaves the printer one way regardless of how the artwork is rotated,
    // and updateFeedIndicator already pins its arrow vertically for exactly
    // that reason. Deriving the edge from the raster (as this first did) made
    // the continuation flip sides when the label was rotated -- the tape
    // appearing to change direction because the picture on it turned.
    // Die-cut versus continuous follows the choice too, for the same reason.
    const dieCutLength = chosen ? chosen.length
        : (loadedMedia ? loadedMedia.length : 0);
    tape.classList.toggle('is-endless', !dieCutLength);
    tape.classList.toggle('is-diecut', !!dieCutLength);
}

/**
 * Hide the server preview image and clear its source. The client preview /
 * placeholder underneath then becomes visible again.
 */
function clearServerPreview() {
    const serverImg = document.getElementById('preview-server');
    const legend = document.getElementById('preview-legend');
    if (legend) legend.classList.add('d-none');
    const tape = document.getElementById('preview-backing');
    if (tape) tape.classList.add('d-none');
    // Clear the sticker's aspect so a stale roll shape is not reused when the
    // next render arrives before sizeTapeToMedia has run.
    const stickerEl = document.getElementById('preview-sticker');
    if (stickerEl) stickerEl.style.removeProperty('--tape-aspect');
    if (serverImg) {
        serverImg.classList.add('d-none');
        // Use removeAttribute rather than src='' — an empty src makes the
        // browser try to load the page URL and logs a spurious ERR_INVALID_URL.
        serverImg.removeAttribute('src');
    }
    clearPreviewDims();

    // No authoritative render on screen. If a client preview is still showing
    // (server render errored, or was never requested) it is provisional and
    // must stay marked as such -- this is precisely when the user most needs to
    // know the preview is an approximation. With nothing showing at all, the
    // placeholder is next and the badge would be noise.
    if (typeof setPreviewDraft === 'function' &&
        typeof areAllPreviewsEmpty === 'function') {
        setPreviewDraft(!areAllPreviewsEmpty(), false);
    }
}

/**
 * Show the server preview image and hide the client previews + placeholder so
 * the server-rendered label "wins".
 * @param {string} dataUrl - data:image/png;base64,... returned by the API
 */
function showServerPreview(dataUrl) {
    const serverImg = document.getElementById('preview-server');
    if (!serverImg) return;
    serverImg.src = dataUrl;
    serverImg.classList.remove('d-none');
    const tape = document.getElementById('preview-backing');
    if (tape) tape.classList.remove('d-none');
    const legend = document.getElementById('preview-legend');
    if (legend) legend.classList.remove('d-none');
    if (serverImg.complete) {
        sizeTapeToMedia(serverImg);
    } else {
        serverImg.addEventListener('load', () => sizeTapeToMedia(serverImg), {once: true});
    }

    // naturalWidth/Height are only meaningful once decoded.
    if (serverImg.complete) {
        updatePreviewDims(serverImg);
    } else {
        serverImg.onload = () => updatePreviewDims(serverImg);
    }

    // Hide the instant client previews + placeholder; the server image wins.
    ['preview-text', 'preview-image', 'preview-qrcode', 'preview-label',
     'pdf-preview', 'preview-placeholder'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('d-none');
    });

    // What is on screen is now exactly what will print: leave the draft state
    // so the preview is shown at full strength and the badge disappears.
    if (typeof setPreviewDraft === 'function') setPreviewDraft(false);
}

/**
 * Build the request descriptor (endpoint + body) for the given compose mode,
 * or return null when there is nothing to render (empty input / no file).
 * @param {string} mode - 'text' | 'qrcode' | 'label' | 'image'
 */
function buildPreviewRequest(mode) {
    const settings = collectPreviewSettings();

    if (mode === 'text') {
        const text = document.getElementById('text-input').value;
        if (!text.trim()) return null;
        return {
            url: '/api/v1/text/preview',
            json: {
                text: text,
                settings: Object.assign({}, settings, {
                    font_size: parseInt(document.getElementById('text-font-size').value),
                    alignment: document.getElementById('text-alignment').value,
                    vertical_alignment: readVerticalAlignment()
                })
            }
        };
    }

    if (mode === 'qrcode') {
        const qrData = document.getElementById('qr-data').value;
        if (!qrData.trim()) return null;
        const body = {
            qr: {
                data: qrData,
                size: parseInt(document.getElementById('qr-size').value),
                error_correction: document.getElementById('qr-error-correction').value,
                version: 1,
                box_size: 10,
                border: 4
            },
            settings: settings
        };
        const showText = document.getElementById('qr-show-text').checked;
        const textContent = document.getElementById('qr-text-content').value;
        if (showText && textContent) {
            body.text = {
                content: textContent,
                position: document.getElementById('qr-text-position').value,
                font_size: parseInt(document.getElementById('qr-text-font-size').value),
                alignment: document.getElementById('qr-text-alignment').value
            };
        }
        return { url: '/api/v1/qrcode/preview', json: body };
    }

    if (mode === 'label') {
        const qrData = document.getElementById('label-qr-data').value;
        const textContent = document.getElementById('label-text-content').value;
        if (!qrData.trim() || !textContent.trim()) return null;
        return {
            url: '/api/v1/label/preview',
            json: {
                qr: {
                    data: qrData,
                    position: document.getElementById('label-qr-position').value,
                    size: 400,
                    error_correction: document.getElementById('label-qr-error-correction').value || 'M',
                    version: 1,
                    box_size: 10,
                    border: 4
                },
                text: {
                    content: textContent,
                    font_size: parseInt(document.getElementById('label-text-font-size').value),
                    alignment: document.getElementById('label-text-alignment').value
                },
                settings: settings
            }
        };
    }

    if (mode === 'image') {
        const imageInput = document.getElementById('image-input');
        if (!imageInput || !imageInput.files || imageInput.files.length === 0) {
            return null;
        }
        const imageMode = document.getElementById('image-mode');
        let dither = settings.dither;
        if (imageMode.value === 'bw-dither') {
            dither = true;
        } else if (imageMode.value === 'bw') {
            dither = false;
        }
        const formData = new FormData();
        formData.append('image', imageInput.files[0]);
        const scaleEl = document.getElementById('image-scale-mode');
        formData.append('settings', JSON.stringify(Object.assign({}, settings, {
            dither: dither,
            // Preview and print must agree, so both read the same control. An
            // absent element falls back to `actual`, which is the default.
            scale_mode: scaleEl ? scaleEl.value : 'actual',
            image_mode: imageMode.value
        })));
        return { url: '/api/v1/image/preview', form: formData };
    }

    return null;
}

/**
 * Request a server-rendered, true-to-print preview for the given compose mode.
 * Debounced (~250ms) and abortable: the newest request always wins. On success
 * the returned PNG overlays the instant client preview; on any error / empty
 * input the server image is hidden so the client preview stays visible.
 * @param {string} mode - 'text' | 'qrcode' | 'label' | 'image'
 */
function requestServerPreview(mode) {
    clearTimeout(serverPreviewTimer);
    serverPreviewTimer = setTimeout(() => {
        const request = buildPreviewRequest(mode);

        // Nothing to render -> drop any server image, let the client preview show.
        if (!request) {
            clearServerPreview();
            return;
        }

        // Abort any in-flight request so its (older) response is ignored.
        if (serverPreviewController) {
            serverPreviewController.abort();
        }
        const controller = new AbortController();
        serverPreviewController = controller;

        const options = { method: 'POST', signal: controller.signal };
        if (request.form) {
            options.body = request.form;
        } else {
            options.headers = { 'Content-Type': 'application/json' };
            options.body = JSON.stringify(request.json);
        }

        fetch(request.url, options)
            .then(response => {
                // A newer request started meanwhile: ignore this result.
                if (serverPreviewController !== controller) return null;
                if (!response.ok) {
                    // 400 / invalid input -> keep the client preview, no server image.
                    clearServerPreview();
                    return null;
                }
                return response.json();
            })
            .then(data => {
                if (!data) return;
                if (serverPreviewController !== controller) return;
                if (data.image) {
                    showServerPreview(data.image);
                } else {
                    clearServerPreview();
                }
            })
            .catch(error => {
                // Swallow aborts from superseding requests.
                if (error && error.name === 'AbortError') return;
                console.error('Error generating server preview:', error);
                clearServerPreview();
            })
            .finally(() => {
                if (serverPreviewController === controller) {
                    serverPreviewController = null;
                }
            });
    }, 250);
}

/**
 * Update keep alive settings
 * @param {boolean} enabled - Whether keep alive should be enabled
 * @param {number} interval - Interval between pings in seconds
 */
async function updateKeepAlive(enabled, interval) {
    try {
        const response = await fetch('/api/v1/printers/keep-alive', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                enabled: enabled,
                interval: interval
            })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || `Error: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Update the status indicator
        loadKeepAliveStatus();
        
        console.log('Keep alive updated:', data);
    } catch (error) {
        console.error('Error updating keep alive:', error);
        showNotification(`Error updating keep alive: ${error.message}`, 'error');
    }
}

// ===================== Print queue =====================
//
// The print endpoints queue jobs that are processed asynchronously. The Queue
// panel lists those jobs, polled while it is the active tab. refreshJobs() does
// a single GET + render; startJobsPolling()/stopJobsPolling() (in core.js)
// control the interval.

const JOB_STATUS_META = {
    queued:    { label: 'Queued',    cls: 'queued' },
    printing:  { label: 'Printing',  cls: 'printing' },
    done:      { label: 'Done',      cls: 'done' },
    failed:    { label: 'Failed',    cls: 'failed' },
    cancelled: { label: 'Cancelled', cls: 'cancelled' },
    // Held jobs are waiting for a human, not for the worker. Without an entry
    // here the badge fell back to the raw status string styled as "queued",
    // which read as though it were about to print by itself.
    held:      { label: 'Held',      cls: 'held' }
};

/**
 * Escape a string for safe insertion into innerHTML.
 */
function escapeHtml(value) {
    if (value == null) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Format a timestamp into a compact relative ("12s ago") string with the
 * absolute time as a title. Accepts ISO strings or epoch seconds/ms.
 */
function formatJobTime(value) {
    if (!value) return { text: '', title: '' };
    let date;
    if (typeof value === 'number') {
        date = new Date(value < 1e12 ? value * 1000 : value);
    } else {
        date = new Date(value);
    }
    if (isNaN(date.getTime())) {
        return { text: String(value), title: String(value) };
    }
    const diffMs = Date.now() - date.getTime();
    const sec = Math.round(diffMs / 1000);
    let text;
    if (sec < 5) {
        text = 'just now';
    } else if (sec < 60) {
        text = `${sec}s ago`;
    } else if (sec < 3600) {
        text = `${Math.floor(sec / 60)}m ago`;
    } else if (sec < 86400) {
        text = `${Math.floor(sec / 3600)}h ago`;
    } else {
        text = `${Math.floor(sec / 86400)}d ago`;
    }
    return { text, title: date.toLocaleString() };
}

/**
 * Update the sidebar badge with the number of active (queued + printing) jobs.
 */
function updateQueueBadge(jobs) {
    const badge = document.getElementById('queue-badge');
    if (!badge) return;
    const active = jobs.filter(j => j.status === 'queued' || j.status === 'printing').length;
    if (active > 0) {
        badge.textContent = String(active);
        badge.hidden = false;
    } else {
        badge.hidden = true;
    }
}

// Cache of the most recently rendered jobs, keyed by id, so per-row actions
// (e.g. Open) can read the job's `params` without an extra round-trip.
const jobsById = {};

/**
 * Render the list of jobs into the Queue panel.
 */
function renderJobs(jobs) {
    const list = document.getElementById('queue-list');
    if (!list) return;

    // Refresh the id -> job cache for action handlers.
    for (const key in jobsById) delete jobsById[key];
    if (Array.isArray(jobs)) {
        jobs.forEach(job => { if (job && job.id != null) jobsById[job.id] = job; });
    }

    if (!Array.isArray(jobs) || jobs.length === 0) {
        list.innerHTML =
            '<div class="queue-empty">' +
            '<i class="bi bi-inbox"></i>' +
            '<p>No print jobs yet</p>' +
            '</div>';
        return;
    }

    const rows = jobs.map(job => {
        const meta = JOB_STATUS_META[job.status] || { label: job.status || 'unknown', cls: 'queued' };
        const time = formatJobTime(job.finished_at || job.started_at || job.created_at);
        const spinner = job.status === 'printing'
            ? '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> '
            : '';
        const cancelBtn = job.status === 'queued'
            ? `<button type="button" class="btn-ghost btn-sm queue-cancel" data-action="cancel" data-job-id="${escapeHtml(job.id)}"><i class="bi bi-x-lg"></i> Cancel</button>`
            : '';
        // Reprint re-runs a job's stored executor, so it needs one.
        const reprintBtn = job.can_reprint === true
            ? `<button type="button" class="btn-ghost btn-sm queue-reprint" data-action="reprint" data-job-id="${escapeHtml(job.id)}"><i class="bi bi-arrow-clockwise"></i> Reprint</button>`
            : '';
        // Print is the whole point of holding: it releases the job through the
        // normal print path, where the media guard runs against whatever roll
        // is loaded NOW rather than at hold time.
        const releaseBtn = job.can_release === true
            ? `<button type="button" class="btn-ghost btn-sm queue-release" data-action="release" data-job-id="${escapeHtml(job.id)}"><i class="bi bi-printer"></i> Print</button>`
            : '';
        // Open restores a job into the composer. Text, qrcode and label jobs
        // rebuild from params alone; image and pdf additionally need their
        // stored file, so they need one to exist.
        //
        // Reviewing before printing is the entire point of holding a job, so
        // this must stay available on held jobs -- gating it on having a file
        // took the button away from exactly the labels most worth checking.
        // A held image job (from /image/compose) has can_reprint false but does
        // have its file, so held must count as "the file is there" too.
        const needsFile = job.type === 'image' || job.type === 'pdf';
        const hasFile = job.can_reprint === true || job.status === 'held';
        const canRestore = job.params != null && (!needsFile || hasFile);
        const openBtn = (canRestore && (job.can_reprint === true || job.status === 'held'))
            ? `<button type="button" class="btn-ghost btn-sm queue-open" data-action="open" data-job-id="${escapeHtml(job.id)}"><i class="bi bi-box-arrow-up-right"></i> ${job.status === 'held' ? 'Review' : 'Open'}</button>`
            : '';
        const reprintBtns = reprintBtn + releaseBtn + openBtn;
        // Delete is available for any job that is not currently printing.
        const deleteBtn = job.status !== 'printing'
            ? `<button type="button" class="btn-ghost btn-sm queue-delete" data-action="delete" data-job-id="${escapeHtml(job.id)}" data-job-status="${escapeHtml(job.status || '')}" title="Delete job"><i class="bi bi-trash3"></i></button>`
            : '';
        const errorRow = (job.status === 'failed' && job.error)
            ? `<div class="queue-error">${escapeHtml(job.error)}</div>`
            : '';

        return (
            `<div class="queue-item">` +
                `<div class="queue-item-main">` +
                    `<span class="queue-status ${meta.cls}">${spinner}${escapeHtml(meta.label)}</span>` +
                    `<span class="queue-type">${escapeHtml(job.type || '')}</span>` +
                    `<span class="queue-label" title="${escapeHtml(job.label || '')}">${escapeHtml(job.label || '—')}</span>` +
                    `<span class="queue-time" title="${escapeHtml(time.title)}">${escapeHtml(time.text)}</span>` +
                    `<span class="queue-actions">${reprintBtns}${cancelBtn}${deleteBtn}</span>` +
                `</div>` +
                errorRow +
            `</div>`
        );
    });

    list.innerHTML = rows.join('');
}

/**
 * Fetch the current jobs and render them. Also refreshes the sidebar badge.
 */
async function refreshJobs() {
    try {
        const response = await fetch('/api/v1/jobs');
        if (!response.ok) {
            throw new Error(`Failed to load jobs: ${response.status}`);
        }
        const data = await response.json();
        const jobs = Array.isArray(data.jobs) ? data.jobs : [];
        renderJobs(jobs);
        updateQueueBadge(jobs);
    } catch (error) {
        console.error('Error loading jobs:', error);
    }
    // Keep the queue control state (pause/resume + paused badge) in sync,
    // folded into the same poll so we don't add a second fast interval.
    refreshQueueState();
}

/**
 * Reflect the queue control state (paused/running) in the header controls: the
 * Pause/Resume toggle's label + icon and the "Queue paused" badge.
 * @param {{paused?: boolean}} state
 */
function applyQueueState(state) {
    const paused = !!(state && state.paused);

    const toggle = document.getElementById('queue-pause-toggle');
    if (toggle) {
        toggle.innerHTML = paused
            ? '<i class="bi bi-play-fill"></i> Resume'
            : '<i class="bi bi-pause-fill"></i> Pause';
        toggle.setAttribute('aria-pressed', paused ? 'true' : 'false');
        toggle.title = paused ? 'Resume the queue' : 'Pause the queue';
    }

    const badge = document.getElementById('queue-paused-badge');
    if (badge) badge.hidden = !paused;
}

/**
 * Fetch the current queue control state (paused/queued/printing counts) and
 * reflect it in the header controls. Folded into the polling loop alongside
 * refreshJobs so it stays in sync without a second fast interval.
 */
async function refreshQueueState() {
    try {
        const response = await fetch('/api/v1/jobs/queue');
        if (!response.ok) {
            throw new Error(`Failed to load queue state: ${response.status}`);
        }
        const state = await response.json();
        applyQueueState(state);
    } catch (error) {
        console.error('Error loading queue state:', error);
    }
}

/**
 * Toggle the queue between paused and running, then refresh state + list.
 * Reads the current state from the toggle button's aria-pressed flag.
 */
async function toggleQueuePause() {
    const toggle = document.getElementById('queue-pause-toggle');
    const paused = toggle && toggle.getAttribute('aria-pressed') === 'true';
    const endpoint = paused ? 'resume' : 'pause';
    try {
        const response = await fetch(`/api/v1/jobs/${endpoint}`, { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const state = await response.json();
        applyQueueState(state);
        showNotification(paused ? 'Queue resumed' : 'Queue paused', 'success');
    } catch (error) {
        console.error('Error toggling queue:', error);
        showNotification(`Error toggling queue: ${error.message}`, 'error');
        refreshQueueState();
    } finally {
        refreshJobs();
    }
}

/**
 * Stop the queue: pause it and cancel all waiting jobs. Confirms first, then
 * surfaces how many jobs were cancelled.
 */
async function stopQueue() {
    const confirmed = await confirmDialog(
        'Stop the queue and cancel all waiting jobs?',
        { title: 'Stop queue', confirmLabel: 'Stop' }
    );
    if (!confirmed) return;

    try {
        const response = await fetch('/api/v1/jobs/stop', { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const data = await response.json();
        applyQueueState(data);
        const n = data.cancelled || 0;
        showNotification(`Stopped — ${n} job${n === 1 ? '' : 's'} cancelled`, 'success');
    } catch (error) {
        console.error('Error stopping queue:', error);
        showNotification(`Error stopping queue: ${error.message}`, 'error');
        refreshQueueState();
    } finally {
        refreshJobs();
    }
}

/**
 * Clear ALL jobs (including waiting ones): cancels every queued job and removes
 * all jobs except one that may currently be printing. Confirms first.
 */
async function clearAllJobs() {
    const confirmed = await confirmDialog(
        'Delete ALL jobs, including waiting ones?',
        { title: 'Clear all', confirmLabel: 'Delete all' }
    );
    if (!confirmed) return;

    try {
        const response = await fetch('/api/v1/jobs/clear-all', { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const data = await response.json();
        const n = data.cleared || 0;
        showNotification(`Cleared ${n} job${n === 1 ? '' : 's'}`, 'success');
    } catch (error) {
        console.error('Error clearing all jobs:', error);
        showNotification(`Error clearing all jobs: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}

/**
 * Delete a single job (queued OR finished). A queued job is confirmed first; an
 * already-finished job is deleted without a prompt to keep it quick. A printing
 * job cannot be deleted (the server returns removed:false).
 * @param {string} jobId
 * @param {string} status - the job's current status (for the confirm decision)
 */
async function deleteJob(jobId, status) {
    if (status === 'queued') {
        const confirmed = await confirmDialog('Delete this waiting job?', {
            title: 'Delete job',
            confirmLabel: 'Delete'
        });
        if (!confirmed) return;
    }

    try {
        const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/delete`, {
            method: 'POST'
        });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const data = await response.json();
        if (!data.removed) {
            showNotification('Job could not be deleted', 'warning');
        }
    } catch (error) {
        console.error('Error deleting job:', error);
        showNotification(`Error deleting job: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}

/**
 * Cancel a queued job, then refresh the list.
 * @param {string} jobId
 */
async function cancelJob(jobId) {
    try {
        const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
            method: 'POST'
        });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const data = await response.json();
        if (data.cancelled) {
            showNotification('Job cancelled', 'success');
        } else {
            showNotification('Job could not be cancelled', 'warning');
        }
    } catch (error) {
        console.error('Error cancelling job:', error);
        showNotification(`Error cancelling job: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}

/**
 * Show a Bootstrap confirmation dialog and resolve to true/false based on the
 * user's choice. Falls back to a native confirm() if Bootstrap or the modal
 * markup is unavailable.
 * @param {string} message - The question shown to the user.
 * @param {Object} [options]
 * @param {string} [options.title] - Modal title.
 * @param {string} [options.confirmLabel] - Confirm button label.
 * @returns {Promise<boolean>}
 */
function confirmDialog(message, options = {}) {
    const modalEl = document.getElementById('confirmModal');
    if (!modalEl || !(window.bootstrap && bootstrap.Modal)) {
        return Promise.resolve(window.confirm(message));
    }

    return new Promise(resolve => {
        const messageEl = document.getElementById('confirm-message');
        const okBtn = document.getElementById('confirm-ok');
        const titleEl = document.getElementById('confirmModalLabel');

        if (messageEl) messageEl.textContent = message;
        if (titleEl) titleEl.textContent = options.title || 'Confirm';
        if (okBtn) okBtn.textContent = options.confirmLabel || 'Confirm';

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        let confirmed = false;

        const onOk = () => {
            confirmed = true;
            modal.hide();
        };
        const onHidden = () => {
            if (okBtn) okBtn.removeEventListener('click', onOk);
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
            resolve(confirmed);
        };

        if (okBtn) okBtn.addEventListener('click', onOk);
        modalEl.addEventListener('hidden.bs.modal', onHidden);
        modal.show();
    });
}

/**
 * Re-queue a previous job for printing via its persisted params, then refresh.
 * Asks the user to confirm before re-queuing.
 * @param {string} jobId
 */
async function releaseJob(jobId) {
    const confirmed = await confirmDialog(
        'Print this held label now?',
        { title: 'Print Held Label', confirmLabel: 'Print' });
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/release`, {
            method: 'POST'
        });
        if (!response.ok) {
            let message = `Error: ${response.status}`;
            try {
                const errorData = await response.json();
                // The media guard runs at release, so a roll mismatch surfaces
                // here rather than at submit. Its message names the loaded roll,
                // which is the useful part -- do not flatten it to a status code.
                message = errorData.message || errorData.error || message;
            } catch (e) { /* non-JSON body */ }
            throw new Error(message);
        }
        showNotification('Queued for printing', 'success');
    } catch (error) {
        console.error('Error releasing job:', error);
        showNotification(`Could not print: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}

async function reprintJob(jobId) {
    const confirmed = await confirmDialog('Really reprint this job?', {
        title: 'Reprint Job',
        confirmLabel: 'Reprint'
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/reprint`, {
            method: 'POST'
        });
        if (!response.ok) {
            let message = `Error: ${response.status}`;
            try {
                const errorData = await response.json();
                message = errorData.message || message;
            } catch (e) { /* non-JSON body */ }
            throw new Error(message);
        }
        showNotification('Re-queued for printing', 'success');
    } catch (error) {
        console.error('Error re-printing job:', error);
        showNotification(`Error re-printing job: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}

/**
 * Helper: set a form field's value if the element exists.
 */
function setFieldValue(id, value) {
    const el = document.getElementById(id);
    if (el && value != null) el.value = value;
}

/**
 * Helper: set a select rendered as a boolean ('true'/'false') if the element
 * exists and the value is a boolean.
 */
function setBoolField(id, value) {
    if (typeof value !== 'boolean') return;
    const el = document.getElementById(id);
    if (el) el.value = value ? 'true' : 'false';
}

/**
 * Populate the shared printer/render settings fields from a settings object.
 * Robust against missing fields and missing settings.
 */
function applySettingsToForm(settings) {
    if (!settings || typeof settings !== 'object') return;
    setFieldValue('printer-uri', settings.printer_uri);
    setFieldValue('printer-model', settings.printer_model);
    setFieldValue('label-size', settings.label_size);
    setFieldValue('rotate', settings.rotate != null ? String(settings.rotate) : null);
    setFieldValue('threshold', settings.threshold != null ? String(settings.threshold) : null);
    setBoolField('dither', settings.dither);
    setBoolField('red', settings.red);
    setFieldValue('copies', settings.copies != null ? String(settings.copies) : null);
    setFieldValue('cut-mode', settings.cut_mode);
    setBoolField('dpi-600', settings.dpi_600);

    // Restore the OVERRIDES as well, not just the saved-defaults fields above.
    //
    // #label-size and #rotate are the saved configuration; every preview and
    // every print issued from the compose screen reads #preview-label-size and
    // #preview-rotate instead (see activeLabelSize/activeRotate). Setting only
    // the former meant reopening a job restored its size into a field nothing
    // reads: a label held for 50mm continuous came back showing 12mm endless,
    // and printing it would have used 12mm.
    //
    // That matters most for exactly the jobs worth reopening -- a held label is
    // reviewed precisely to check the roll, rotation and scale.
    // Only these two: rotate_mode, scale_mode and scale_percent are API-only by
    // design (see "Not building the advanced scaling UI" in the fork's README),
    // so there is no control to restore them into. They survive on the job and
    // are re-sent unchanged when it is released; reopening a job in the
    // composer and printing from there drops them to the form's defaults.
    setFieldValue('preview-label-size', settings.label_size);
    setFieldValue('preview-rotate', settings.rotate != null ? String(settings.rotate) : null);
    // The preview re-renders on change, so nudge it once both are set.
    dispatchOn('preview-label-size', 'change');
}

/**
 * Activate a compose tab by its trigger button id (Bootstrap Tab + click
 * fallback) and close the mobile drawer if present.
 */
function activateComposeTab(tabId) {
    const tabBtn = document.getElementById(tabId);
    if (tabBtn) {
        if (window.bootstrap && bootstrap.Tab) {
            new bootstrap.Tab(tabBtn).show();
        } else {
            tabBtn.click();
        }
    }
    // Close the off-canvas drawer on mobile.
    const rail = document.getElementById('rail');
    if (rail && rail.classList.contains('open') &&
        window.matchMedia('(max-width: 768px)').matches) {
        rail.classList.remove('open');
        const railScrim = document.getElementById('rail-scrim');
        if (railScrim) railScrim.hidden = true;
        const railToggle = document.getElementById('rail-toggle');
        if (railToggle) railToggle.setAttribute('aria-expanded', 'false');
    }
}

/**
 * Helper: dispatch an event on a field if it exists.
 */
function dispatchOn(id, eventName) {
    const el = document.getElementById(id);
    if (el) el.dispatchEvent(new Event(eventName, { bubbles: true }));
}

/**
 * Load a persisted job's params back into the matching compose form and switch
 * to its tab so the user only needs to press "Print". For image/pdf jobs the
 * persisted file is fetched from the server.
 * @param {string} jobId
 */
async function openJob(jobId) {
    let job = jobsById[jobId];

    // Fall back to a fresh fetch if the job (or its params) is not cached.
    if (!job || !job.params) {
        try {
            const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
            if (response.ok) {
                job = await response.json();
            }
        } catch (e) {
            console.error('Error loading job:', e);
        }
    }

    if (!job || !job.params) {
        showNotification('Job details are no longer available', 'error');
        return;
    }

    const params = job.params;
    const type = params.type;
    const settings = params.settings || {};

    // Only a HELD job can be edited in place. Reopening a finished one is a
    // "start from this" convenience, and amending it would rewrite the record
    // of something that already printed.
    openedHeldJobId = job.status === 'held' ? job.id : null;
    if (typeof refreshComposerMode === 'function') refreshComposerMode();

    try {
        if (type === 'text') {
            applySettingsToForm(settings);
            setFieldValue('text-input', params.text);
            setFieldValue('text-font-size', settings.font_size != null ? String(settings.font_size) : null);
            setFieldValue('text-alignment', settings.alignment);
            setFieldValue('text-vertical-alignment', settings.vertical_alignment);
            activateComposeTab('text-tab');
            dispatchOn('text-input', 'input');
        } else if (type === 'qrcode') {
            applySettingsToForm(settings);
            setFieldValue('qr-data', params.data);
            setFieldValue('qr-size', settings.size != null ? String(settings.size) : null);
            setFieldValue('qr-error-correction', settings.error_correction);
            activateComposeTab('qrcode-tab');
            dispatchOn('qr-data', 'input');
        } else if (type === 'label') {
            applySettingsToForm(settings);
            setFieldValue('label-text-content', params.text);
            setFieldValue('label-qr-data', params.data);
            setFieldValue('label-text-font-size', settings.font_size != null ? String(settings.font_size) : null);
            setFieldValue('label-text-alignment', settings.alignment);
            setFieldValue('label-qr-position', settings.qr_position);
            setFieldValue('label-qr-error-correction', settings.error_correction);
            activateComposeTab('label-tab');
            dispatchOn('label-text-content', 'input');
        } else if (type === 'image') {
            applySettingsToForm(settings);
            setFieldValue('image-mode', settings.image_mode);
            const loaded = await loadJobFileIntoInput(jobId, 'image-input', params.filename || 'reprint-image');
            if (!loaded) return;
            activateComposeTab('image-tab');
            dispatchOn('image-input', 'change');
        } else if (type === 'pdf') {
            applySettingsToForm(settings);
            setFieldValue('pdf-pages', params.pages);
            setFieldValue('pdf-scale-mode', params.scale_mode);
            const loaded = await loadJobFileIntoInput(jobId, 'pdf-input', params.filename || 'reprint.pdf');
            if (!loaded) return;
            activateComposeTab('pdf-tab');
            dispatchOn('pdf-input', 'change');
        } else {
            showNotification('Unsupported job type', 'error');
            return;
        }
    } catch (error) {
        console.error('Error opening job:', error);
        showNotification(`Error opening job: ${error.message}`, 'error');
    }
}

/**
 * Fetch a job's persisted file and place it into the given file input via a
 * DataTransfer. Returns true on success, false on failure (e.g. expired file).
 * @param {string} jobId
 * @param {string} inputId
 * @param {string} fileName
 */
async function loadJobFileIntoInput(jobId, inputId, fileName) {
    const input = document.getElementById(inputId);
    if (!input) return false;

    let response;
    try {
        response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/file`);
    } catch (e) {
        showNotification('File no longer available (expired)', 'error');
        return false;
    }

    if (response.status === 404) {
        showNotification('File no longer available (expired)', 'error');
        return false;
    }
    if (!response.ok) {
        showNotification('File no longer available (expired)', 'error');
        return false;
    }

    const blob = await response.blob();
    const file = new File([blob], fileName, { type: blob.type });
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    return true;
}

/**
 * Clear all finished jobs (done/failed/cancelled) from the queue.
 */
async function clearFinishedJobs() {
    try {
        const response = await fetch('/api/v1/jobs/clear', { method: 'POST' });
        if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
        }
        const data = await response.json();
        const n = data.cleared || 0;
        showNotification(`Cleared ${n} finished job${n === 1 ? '' : 's'}`, 'success');
    } catch (error) {
        console.error('Error clearing jobs:', error);
        showNotification(`Error clearing jobs: ${error.message}`, 'error');
    } finally {
        refreshJobs();
    }
}
