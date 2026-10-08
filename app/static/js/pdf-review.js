/**
 * PDF import: upload reports one at a time, review what was read next to the PDF, correct it,
 * and import. Used by templates/pdf-import.html.
 *
 * Each report gets a review card. Rows are identified by their position in the parsed report
 * (`index`), which is what the server expects in selected_tests and edits.
 */
(function () {
    const OUT = ['high', 'low', 'abnormal'];
    const state = { cards: new Map(), labs: [], labsById: new Map(), providers: [], aiEnabled: false, nextKey: 1 };

    // ---------- helpers ----------
    const $id = id => document.getElementById(id);
    const esc = v => escapeHtml(v === null || v === undefined ? '' : String(v));
    const today = () => {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const displayDate = iso => {
        if (!iso) return '';
        try { return formatDateSync(`${iso.slice(0, 10)}T12:00:00`, window.userDateFormat || 'MM/DD/YYYY'); }
        catch (e) { return iso.slice(0, 10); }
    };

    function statusBadge(status) {
        switch (status) {
            case 'normal': return '<span class="badge badge-status-normal">Normal</span>';
            case 'high': return '<span class="badge badge-status-out"><i class="mdi mdi-arrow-up-bold" aria-hidden="true"></i> High</span>';
            case 'low': return '<span class="badge badge-status-out"><i class="mdi mdi-arrow-down-bold" aria-hidden="true"></i> Low</span>';
            case 'abnormal': return '<span class="badge badge-status-abnormal"><i class="mdi mdi-alert" aria-hidden="true"></i> Abnormal</span>';
            default: return '<span class="badge badge-status-none" title="No reference range to compare against">No range</span>';
        }
    }

    function parseRange(text) {
        const raw = (text || '').trim();
        let m = raw.match(/^(<=?|>=?|≤|≥)\s*(-?\d+(?:\.\d+)?)$/);
        if (m) return /^(>|≥)/.test(m[1]) ? { low: +m[2], high: null } : { low: null, high: +m[2] };
        m = raw.match(/^(-?\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(-?\d+(?:\.\d+)?)$/);
        if (m) return { low: +m[1], high: +m[2] };
        return { low: null, high: null };
    }

    // Same rules as the server: the row's own range, else the chosen saved test's range
    function computeStatus(resultText, rangeText, labId, flag) {
        const value = /^\s*-?\d+(\.\d+)?\s*$/.test(resultText || '') ? parseFloat(resultText) : null;
        if (value !== null) {
            let { low, high } = parseRange(rangeText);
            let inclusive = true;
            if (low === null && high === null && labId && state.labsById.has(labId)) {
                const lab = state.labsById.get(labId);
                if (lab.ref_type === 'greater' && lab.ref_value !== null) { low = lab.ref_value; inclusive = false; }
                else if (lab.ref_type === 'less' && lab.ref_value !== null) { high = lab.ref_value; inclusive = false; }
                else { low = lab.ref_low; high = lab.ref_high; }
            }
            if (low !== null && low !== undefined || high !== null && high !== undefined) {
                if (low !== null && low !== undefined && (value < low || (!inclusive && value === low))) return 'low';
                if (high !== null && high !== undefined && (value > high || (!inclusive && value === high))) return 'high';
                return 'normal';
            }
        }
        const f = (flag || '').trim().toLowerCase();
        if (['h', 'hi', 'high', 'hh'].includes(f)) return 'high';
        if (['l', 'lo', 'low', 'll'].includes(f)) return 'low';
        return f ? 'abnormal' : 'unknown';
    }

    function message(text, type = 'danger', focus = true) {
        const box = $id('reviewMessages');
        box.innerHTML = text ? `<div class="alert alert-${type} mb-3" role="alert" tabindex="-1">${text}</div>` : '';
        if (text && focus) box.firstElementChild.focus();
    }

    // ---------- reference data ----------
    async function loadReferenceData() {
        const [labs, providers, ai] = await Promise.all([
            fetch('/api/labs/?limit=1000').then(r => r.ok ? r.json() : []).catch(() => []),
            fetch('/api/providers/?limit=1000').then(r => r.ok ? r.json() : []).catch(() => []),
            fetch('/api/pdf/ai-status').then(r => r.ok ? r.json() : {}).catch(() => ({})),
        ]);
        state.labs = labs.slice().sort((a, b) => (a.panel_name || '').localeCompare(b.panel_name || '') || a.name.localeCompare(b.name));
        state.labsById = new Map(state.labs.map(l => [l.id, l]));
        state.providers = providers.slice().sort((a, b) => a.name.localeCompare(b.name));
        state.aiEnabled = ai.enabled === true;
        try { window.userDateFormat = await getUserDateFormat(); } catch (e) { /* default format */ }
    }

    // ---------- upload ----------
    function progressRow(file) {
        const key = state.nextKey++;
        const li = document.createElement('li');
        li.className = 'upload-item d-flex flex-wrap align-items-center justify-content-between py-2 border-bottom';
        li.id = `upload-${key}`;
        li.innerHTML = `<span class="mr-2"><i class="mdi mdi-file-pdf-box mr-1" aria-hidden="true"></i>${esc(file.name)}</span>
            <span class="upload-status small" id="upload-status-${key}">Waiting…</span>`;
        $id('uploadList').appendChild(li);
        $id('uploadProgress').hidden = false;
        return key;
    }

    function setProgress(key, html) {
        const el = $id(`upload-status-${key}`);
        if (el) el.innerHTML = html;
    }

    function uploadOne(file, key) {
        return new Promise(resolve => {
            const form = new FormData();
            form.append('file', file);
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/pdf/upload');
            xhr.upload.onprogress = e => {
                if (e.lengthComputable) setProgress(key, `<i class="mdi mdi-upload" aria-hidden="true"></i> Uploading ${Math.round(e.loaded / e.total * 100)}%`);
            };
            xhr.upload.onload = () => setProgress(key, '<i class="mdi mdi-loading mdi-spin" aria-hidden="true"></i> Reading report…');
            xhr.onload = () => {
                let data = {};
                try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
                if (xhr.status >= 200 && xhr.status < 300) return resolve({ ok: true, data });
                resolve({ ok: false, error: typeof data.detail === 'string' ? data.detail : 'This file couldn\'t be read.' });
            };
            xhr.onerror = () => resolve({ ok: false, error: 'Upload failed. Check the connection and try again.' });
            setProgress(key, '<i class="mdi mdi-upload" aria-hidden="true"></i> Uploading…');
            xhr.send(form);
        });
    }

    async function handleFiles(fileList) {
        const files = Array.from(fileList || []);
        const pdfs = files.filter(f => f.type === 'application/pdf' || /\.pdf$/i.test(f.name));
        message(files.length > pdfs.length
            ? `${plural(files.length - pdfs.length, 'file')} skipped because only PDF files can be imported.` : '', 'warning', false);
        if (!pdfs.length) return;
        $id('importSummary').hidden = true;

        const keys = pdfs.map(progressRow);
        // One at a time, so each report's progress is accurate and the server isn't flooded
        for (let i = 0; i < pdfs.length; i++) {
            const key = keys[i];
            const result = await uploadOne(pdfs[i], key);
            if (!result.ok) {
                setProgress(key, `<span class="text-danger"><i class="mdi mdi-alert-circle" aria-hidden="true"></i> ${esc(result.error)}</span>`);
                continue;
            }
            const preview = result.data;
            if (preview.duplicate_warning) {
                const when = displayDate(preview.duplicate_warning.previous_import_date);
                const canReview = preview.import_status === 'pending' ||
                    (preview.duplicate_warning.previous_tests_imported || 0) < (preview.total_tests_found || 0);
                setProgress(key, `<span><i class="mdi mdi-content-duplicate" aria-hidden="true"></i> Already uploaded${when ? ` on ${esc(when)}` : ''}</span>
                    ${canReview ? `<button type="button" class="btn btn-link btn-sm p-0 ml-2" data-review-import="${esc(preview.import_id)}">Review it</button>` : ''}`);
                continue;
            }
            setProgress(key, `<span class="text-success-strong"><i class="mdi mdi-check-circle" aria-hidden="true"></i> ${plural(preview.total_tests_found, 'test')} found</span>`);
            addCard(preview);
        }
        $id('fileInput').value = '';
    }

    // ---------- review cards ----------
    async function openReview(importId) {
        for (const card of state.cards.values()) {
            if (String(card.preview.import_id) === String(importId)) { $id(`card-${card.key}`).scrollIntoView({ block: 'start' }); return; }
        }
        try {
            const response = await fetch(`/api/pdf/review/${encodeURIComponent(importId)}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'This import couldn\'t be opened.');
            $id('importSummary').hidden = true;
            addCard(data);
            $id('reviewSection').scrollIntoView({ block: 'start' });
        } catch (error) {
            message(esc(error.message));
        }
    }

    function addCard(preview, replaceKey, showCompare) {
        const key = replaceKey || state.nextKey++;
        state.cards.set(key, { key, preview, showCompare: !!showCompare });
        const html = renderCard(key, preview);
        const existing = $id(`card-${key}`);
        if (existing) existing.outerHTML = html; else $id('reviewFiles').insertAdjacentHTML('beforeend', html);
        $id('reviewSection').hidden = false;
        updateCounts();
    }

    function labOptions(selected) {
        let html = `<option value="new"${selected === 'new' ? ' selected' : ''}>New test</option>`;
        let panel = null;
        state.labs.forEach(lab => {
            if (lab.panel_name !== panel) {
                if (panel !== null) html += '</optgroup>';
                panel = lab.panel_name;
                html += `<optgroup label="${esc(panel || 'Other')}">`;
            }
            html += `<option value="${lab.id}"${String(selected) === String(lab.id) ? ' selected' : ''}>${esc(lab.name)}${lab.unit_name ? ` (${esc(lab.unit_name)})` : ''}</option>`;
        });
        return html + (panel !== null ? '</optgroup>' : '');
    }

    function providerOptions(selected) {
        return '<option value="">Choose provider…</option>' + state.providers.map(p =>
            `<option value="${p.id}"${String(selected) === String(p.id) ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    }

    function renderRow(key, row, change) {
        const id = `r-${key}-${row.index}`;
        const marker = change === 'different' ? 'Read differently by the other reader'
            : change === 'only_ai' ? 'Only the AI found this' : change === 'only_standard' ? 'Only the built-in reader found this' : '';
        const done = row.already_imported;
        const choice = row.unit_mismatch || !row.matched_lab_id ? 'new' : row.matched_lab_id;
        const checked = row.readable && !done;
        const issues = row.issues.length ? `<div class="row-issue small mt-1" id="${id}-issues"><i class="mdi mdi-alert-outline" aria-hidden="true"></i> ${row.issues.map(esc).join(' ')}${row.unit_mismatch ? ' It will be saved as a new test unless you pick the existing one.' : ''}</div>` : '';
        return `
            <tr data-index="${row.index}" class="${done ? 'row-done' : ''}${row.readable ? '' : ' row-unreadable'}">
                <td class="text-center">
                    <div class="custom-control custom-checkbox">
                        <input type="checkbox" class="custom-control-input row-check" id="${id}-check" ${checked ? 'checked' : ''} ${done ? 'disabled' : ''}
                               aria-label="Import ${esc(row.name || `row ${row.index + 1}`)}">
                        <label class="custom-control-label" for="${id}-check"></label>
                    </div>
                </td>
                <td data-label="Test">
                    <input type="text" class="form-control form-control-sm row-name" id="${id}-name" value="${esc(row.name)}" maxlength="255"
                           aria-label="Test name" ${done ? 'disabled' : ''} ${row.issues.length ? `aria-describedby="${id}-issues"` : ''}>
                    <label class="sr-only" for="${id}-lab">Save as</label>
                    <select class="custom-select custom-select-sm mt-1 row-lab" id="${id}-lab" ${done ? 'disabled' : ''}>${labOptions(choice)}</select>
                    ${marker ? `<span class="badge badge-status-info mt-1"><i class="mdi mdi-compare-horizontal" aria-hidden="true"></i> ${marker}</span>` : ''}
                    ${issues}
                    ${done ? '<div class="small text-muted mt-1">Already imported</div>' : ''}
                </td>
                <td data-label="Result">
                    <input type="text" class="form-control form-control-sm row-result" id="${id}-result" value="${esc(row.result)}" maxlength="255"
                           aria-label="Result" inputmode="decimal" ${done ? 'disabled' : ''}>
                    ${row.flag ? `<span class="badge badge-status-abnormal mt-1" title="Flag printed by the lab">Lab flag: ${esc(row.flag)}</span>` : ''}
                    ${row.lab_comment ? `<div class="small text-muted mt-1"><i class="mdi mdi-comment-text-outline" aria-hidden="true"></i> ${esc(row.lab_comment)}</div>` : ''}
                </td>
                <td data-label="Unit"><input type="text" class="form-control form-control-sm row-unit" id="${id}-unit" value="${esc(row.unit)}" maxlength="50" aria-label="Unit" ${done ? 'disabled' : ''}></td>
                <td data-label="Range"><input type="text" class="form-control form-control-sm row-range" id="${id}-range" value="${esc(row.reference_range.text || '')}" maxlength="100" aria-label="Reference range" placeholder="e.g. 70-99" ${done ? 'disabled' : ''}></td>
                <td data-label="Status" class="row-status" aria-live="polite">${statusBadge(row.status)}</td>
            </tr>`;
    }

    // ---------- built-in reader vs AI ----------
    const READER = { standard: 'Built-in reader', ai: 'AI' };
    const CHANGE_LABEL = { different: 'Different', only_ai: 'Only AI', only_standard: 'Only built-in', same: 'Same' };

    function readingText(reading, differences) {
        if (!reading) return '<span class="text-muted">Not found</span>';
        const part = (field, text) => differences.includes(field) ? `<strong>${text}</strong>` : text;
        let html = part('result', esc(reading.result) || '<span class="text-muted">blank</span>');
        if (reading.unit) html += ' ' + part('unit', esc(reading.unit));
        if (reading.range) html += ` <span class="small">(range ${part('range', esc(reading.range))})</span>`;
        return html;
    }

    function comparisonSummary(c) {
        const s = c.summary;
        const parts = [];
        if (s.only_ai) parts.push(`AI found ${plural(s.only_ai, 'result')} the built-in reader missed`);
        if (s.only_standard) parts.push(`the built-in reader found ${plural(s.only_standard, 'result')} the AI didn't`);
        if (s.different) parts.push(`${plural(s.different, 'result')} read differently`);
        const fields = c.fields.filter(f => !f.same).map(f => f.field.toLowerCase());
        if (fields.length) parts.push(`different ${fields.join(' and ')}`);
        if (!parts.length) return `Both read the same ${plural(s.same, 'result')}.`;
        const text = parts.join(', ');
        return text.charAt(0).toUpperCase() + text.slice(1) + (s.same ? `; ${s.same} the same.` : '.');
    }

    function renderComparison(key, p, open) {
        const c = p.comparison;
        const other = c.active === 'ai' ? 'standard' : 'ai';
        const fieldRows = c.fields.map(f => `
            <tr><th scope="row">${esc(f.field)}</th>
                <td>${f.field === 'Collection date' ? esc(displayDate(f.standard)) : esc(f.standard)}${f.standard ? '' : '<span class="text-muted">Not found</span>'}</td>
                <td>${f.field === 'Collection date' ? esc(displayDate(f.ai)) : esc(f.ai)}${f.ai ? '' : '<span class="text-muted">Not found</span>'}</td>
                <td>${f.same ? 'Same' : '<strong>Different</strong>'}</td></tr>`).join('');
        const rows = c.rows.map(r => `
            <tr class="compare-${r.change}">
                <th scope="row">${esc(r.name)}${r.standard && r.ai && r.standard.name.toLowerCase() !== r.ai.name.toLowerCase()
                    ? `<div class="small text-muted">Built-in: ${esc(r.standard.name)}</div>` : ''}</th>
                <td>${readingText(r.standard, r.differences)}</td>
                <td>${readingText(r.ai, r.differences)}</td>
                <td><span class="badge badge-status-${r.change === 'same' ? 'none' : 'info'}">${CHANGE_LABEL[r.change]}</span>${r.differences.length
                    ? `<div class="small">${r.differences.map(d => d === 'range' ? 'range' : d === 'result' ? 'value' : 'unit').join(', ')}</div>` : ''}</td>
            </tr>`).join('');
        const done = (p.tests || []).some(r => r.already_imported);
        return `
            <details class="compare-readings mb-3" id="compare-${key}" ${open ? 'open' : ''}>
                <summary><i class="mdi mdi-compare-horizontal mr-1" aria-hidden="true"></i><strong>Compare the built-in reader with the AI</strong>
                    <span class="d-block small mt-1">${esc(comparisonSummary(c))} Using: <strong>${READER[c.active]}</strong>.</span></summary>
                <div class="pt-2">
                    ${c.standard_error ? `<p class="small mb-2">The built-in reader couldn't read this report: ${esc(c.standard_error)}</p>` : ''}
                    <div class="table-responsive" tabindex="0" role="region" aria-label="Comparison of the two readings">
                        <table class="table table-sm compare-table mb-2">
                            <caption class="sr-only">What the built-in reader and the AI read from ${esc(p.filename)}. Values that differ are in bold.</caption>
                            <thead><tr><th scope="col">Test</th><th scope="col">Built-in reader (${c.standard_count})</th><th scope="col">AI (${c.ai_count})</th><th scope="col">Difference</th></tr></thead>
                            <tbody>${fieldRows}${rows}</tbody>
                        </table>
                    </div>
                    ${done ? '<p class="small text-muted mb-0">Some results from this report are already imported, so the reading can no longer be switched.</p>'
                        : `<button type="button" class="btn btn-outline-primary btn-sm switch-reading">
                            <i class="mdi mdi-swap-horizontal mr-1" aria-hidden="true"></i>Use the ${READER[other] === 'AI' ? 'AI\'s' : 'built-in reader\'s'} results instead</button>
                           <small class="form-text text-muted">Switching reloads the results below; changes you've made to them are discarded.</small>`}
                </div>
            </details>`;
    }

    function renderCard(key, p) {
        const rows = p.tests || [];
        const readable = rows.filter(r => r.readable && !r.already_imported);
        const unreadable = rows.filter(r => !r.readable && !r.already_imported);
        const done = rows.filter(r => r.already_imported);
        const mismatches = rows.filter(r => r.unit_mismatch && !r.already_imported);
        const out = readable.filter(r => OUT.includes(r.status));
        // Readable rows first, unreadable ones at the bottom, rows already imported last
        const ordered = [...readable, ...unreadable, ...done];
        const physician = p.matched_provider ? '' : (p.physician ? `<small class="form-text text-muted">Report lists <strong>${esc(p.physician)}</strong> · <a href="#" data-new-provider="${key}" data-report-name="1">Add as new provider</a></small>` : '');
        const dateMissing = !p.date_collected;
        const showPdf = window.matchMedia('(min-width: 1200px)').matches;
        const changes = new Map(((p.comparison || {}).rows || []).filter(r => r.active_index !== null).map(r => [r.active_index, r.change]));

        return `
        <section class="card review-card mb-4" id="card-${key}" data-key="${key}" aria-labelledby="card-${key}-title">
            <div class="card-header d-flex flex-wrap align-items-center">
                <h3 class="card-title h6 mb-0 mr-auto" id="card-${key}-title">
                    <i class="mdi mdi-file-pdf-box mr-1" aria-hidden="true"></i>${esc(p.filename)}
                    ${p.parser === 'ai' ? '<span class="badge badge-status-info ml-1" title="Read with AI; check the values against the PDF">AI</span>' : ''}
                </h3>
                <div class="card-tools d-flex flex-wrap">
                    <button type="button" class="btn btn-outline-secondary btn-sm mr-1 mb-1 toggle-pdf" aria-expanded="${showPdf}" aria-controls="card-${key}-pdf">
                        <i class="mdi mdi-file-eye-outline mr-1" aria-hidden="true"></i><span>${showPdf ? 'Hide PDF' : 'Show PDF'}</span></button>
                    ${state.aiEnabled && !done.length ? `<button type="button" class="btn btn-outline-primary btn-sm mr-1 mb-1 rescan-ai"><i class="mdi mdi-robot-outline mr-1" aria-hidden="true"></i>Re-scan with AI</button>` : ''}
                    <button type="button" class="btn btn-outline-secondary btn-sm mb-1 remove-card" aria-label="Remove ${esc(p.filename)} from this review">
                        <i class="mdi mdi-close" aria-hidden="true"></i> Remove</button>
                </div>
            </div>
            <div class="card-body">
                <p class="review-counts mb-3">
                    ${plural(readable.length, 'result')} to import${out.length ? ` · <strong>${out.length} out of range</strong>` : ''}${unreadable.length ? ` · ${unreadable.length} couldn't be read` : ''}${done.length ? ` · ${done.length} already imported` : ''}
                </p>
                ${p.comparison ? renderComparison(key, p, !!(state.cards.get(key) || {}).showCompare) : ''}
                <div class="row">
                    <div class="review-main ${showPdf ? 'col-xl-7' : 'col-12'}">
                        <div class="form-row">
                            <div class="form-group col-sm-6">
                                <label for="date-${key}">Collection date <span class="text-danger" aria-hidden="true">*</span></label>
                                <input type="date" class="form-control${dateMissing ? ' is-invalid' : ''}" id="date-${key}" value="${esc((p.date_collected || '').slice(0, 10))}"
                                       max="${today()}" required aria-describedby="date-${key}-help" ${dateMissing ? 'aria-invalid="true"' : ''}>
                                <small class="${dateMissing ? 'invalid-feedback d-block' : 'form-text text-muted'}" id="date-${key}-help">
                                    ${dateMissing ? 'No collection date was found on this report. Enter it to import.' : 'Read from the report; change it if it\'s wrong.'}</small>
                            </div>
                            <div class="form-group col-sm-6">
                                <label for="provider-${key}">Provider <span class="text-danger" aria-hidden="true">*</span></label>
                                <div class="input-group">
                                    <select class="custom-select" id="provider-${key}" required>${providerOptions(p.matched_provider ? p.matched_provider.id : '')}</select>
                                    <div class="input-group-append">
                                        <button type="button" class="btn btn-outline-secondary" data-new-provider="${key}" aria-label="Add a new provider"><i class="mdi mdi-plus" aria-hidden="true"></i></button>
                                    </div>
                                </div>
                                ${p.matched_provider ? '<small class="form-text text-muted">Selected from the report.</small>' : physician}
                            </div>
                        </div>
                        ${unreadable.length ? `<div class="alert alert-warning py-2"><i class="mdi mdi-alert-outline mr-1" aria-hidden="true"></i>${plural(unreadable.length, 'row')} couldn't be read. ${unreadable.length === 1 ? 'It\'s' : 'They\'re'} at the bottom of the list; fill in the name and result and tick ${unreadable.length === 1 ? 'it' : 'them'} to include ${unreadable.length === 1 ? 'it' : 'them'}.</div>` : ''}
                        ${mismatches.length ? `<div class="alert alert-warning py-2"><i class="mdi mdi-scale-balance mr-1" aria-hidden="true"></i>${plural(mismatches.length, 'result')} use${mismatches.length === 1 ? 's' : ''} a different unit than the saved test. ${mismatches.length === 1 ? 'It\'s' : 'They\'re'} set to be saved as new tests so the charts stay on one scale.</div>` : ''}
                        ${rows.length ? `
                        <div class="table-responsive-md">
                            <table class="table table-sm review-table mb-0">
                                <caption class="sr-only">Results read from ${esc(p.filename)}. Edit any value before importing.</caption>
                                <thead><tr>
                                    <th scope="col" class="text-center" style="width: 2.5rem;">
                                        <div class="custom-control custom-checkbox">
                                            <input type="checkbox" class="custom-control-input check-all" id="check-all-${key}" aria-label="Select all rows">
                                            <label class="custom-control-label" for="check-all-${key}"></label>
                                        </div>
                                    </th>
                                    <th scope="col">Test</th><th scope="col">Result</th><th scope="col">Unit</th><th scope="col">Range</th><th scope="col">Status</th>
                                </tr></thead>
                                <tbody>${ordered.map(r => renderRow(key, r, changes.get(r.index))).join('')}</tbody>
                            </table>
                        </div>` : '<p class="text-muted">No results were found in this report.</p>'}
                    </div>
                    <div class="review-pdf col-xl-5" id="card-${key}-pdf" ${showPdf ? '' : 'hidden'}>
                        <div class="review-pdf-inner">
                            ${showPdf ? `<iframe src="${esc(p.pdf_url)}" title="PDF of ${esc(p.filename)}" loading="lazy"></iframe>` : ''}
                            <a href="${esc(p.pdf_url)}" target="_blank" rel="noopener" class="small d-inline-block mt-1">Open the PDF in a new tab</a>
                        </div>
                    </div>
                </div>
            </div>
        </section>`;
    }

    function cardOf(el) {
        const section = el.closest('.review-card');
        return section ? state.cards.get(parseInt(section.dataset.key, 10)) : null;
    }

    function updateRowStatus(tr) {
        const card = cardOf(tr);
        const row = card.preview.tests.find(r => r.index === parseInt(tr.dataset.index, 10));
        const lab = tr.querySelector('.row-lab').value;
        const status = computeStatus(tr.querySelector('.row-result').value, tr.querySelector('.row-range').value,
            lab === 'new' ? null : parseInt(lab, 10), row.flag);
        tr.querySelector('.row-status').innerHTML = statusBadge(status);
    }

    function updateCounts() {
        let selected = 0;
        document.querySelectorAll('.review-card').forEach(section => {
            const boxes = Array.from(section.querySelectorAll('.row-check:not(:disabled)'));
            const checked = boxes.filter(b => b.checked).length;
            selected += checked;
            const all = section.querySelector('.check-all');
            if (all) {
                all.checked = boxes.length > 0 && checked === boxes.length;
                all.indeterminate = checked > 0 && checked < boxes.length;
                all.disabled = boxes.length === 0;
            }
        });
        const button = $id('importButton');
        button.disabled = selected === 0;
        button.querySelector('span').textContent = selected ? `Import ${plural(selected, 'result')}` : 'Nothing selected';
        if (!state.cards.size) $id('reviewSection').hidden = true;
    }

    function togglePdf(section, show) {
        const card = state.cards.get(parseInt(section.dataset.key, 10));
        const panel = section.querySelector('.review-pdf');
        const main = section.querySelector('.review-main');
        const button = section.querySelector('.toggle-pdf');
        const visible = show === undefined ? panel.hidden : show;
        panel.hidden = !visible;
        main.classList.toggle('col-xl-7', visible);
        main.classList.toggle('col-12', !visible);
        button.setAttribute('aria-expanded', String(visible));
        button.querySelector('span').textContent = visible ? 'Hide PDF' : 'Show PDF';
        if (visible && !panel.querySelector('iframe')) {
            panel.querySelector('.review-pdf-inner').insertAdjacentHTML('afterbegin',
                `<iframe src="${esc(card.preview.pdf_url)}" title="PDF of ${esc(card.preview.filename)}"></iframe>`);
        }
    }

    async function rescan(section) {
        const card = state.cards.get(parseInt(section.dataset.key, 10));
        const button = section.querySelector('.rescan-ai');
        button.disabled = true;
        button.innerHTML = '<i class="mdi mdi-loading mdi-spin mr-1" aria-hidden="true"></i>Reading with AI…';
        try {
            const response = await fetch(`/api/pdf/rescan-ai/${encodeURIComponent(card.preview.import_id)}`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'AI re-scan failed.');
            addCard(data, card.key, true);
            const c = data.comparison;
            message(`AI found ${plural(data.total_tests_found, 'result')} in ${esc(data.filename)}${c ? ` (the built-in reader found ${c.standard_count})` : ''}. `
                + `<a href="#compare-${card.key}">See what changed</a> and check the values against the PDF before importing.`, 'info', false);
        } catch (error) {
            button.disabled = false;
            button.innerHTML = '<i class="mdi mdi-robot-outline mr-1" aria-hidden="true"></i>Re-scan with AI';
            message(`AI re-scan of ${esc(card.preview.filename)} failed: ${esc(error.message)}`);
        }
    }

    async function switchReading(section) {
        const card = state.cards.get(parseInt(section.dataset.key, 10));
        const button = section.querySelector('.switch-reading');
        button.disabled = true;
        try {
            const response = await fetch(`/api/pdf/${encodeURIComponent(card.preview.import_id)}/switch-reading`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Switching failed.');
            addCard(data, card.key, true);
            message(`Now using the ${data.parser === 'ai' ? 'AI\'s' : 'built-in reader\'s'} results for ${esc(data.filename)}.`, 'info', false);
            const summary = document.querySelector(`#compare-${card.key} summary`);
            if (summary) summary.focus();
        } catch (error) {
            button.disabled = false;
            message(`Couldn't switch readings for ${esc(card.preview.filename)}: ${esc(error.message)}`);
        }
    }

    // ---------- validation and import ----------
    function markInvalid(el, text, errors) {
        el.classList.add('is-invalid');
        el.setAttribute('aria-invalid', 'true');
        errors.push(`<li><a href="#${el.id}" class="alert-link">${text}</a></li>`);
    }

    function collect() {
        document.querySelectorAll('.review-card .is-invalid').forEach(el => {
            el.classList.remove('is-invalid'); el.removeAttribute('aria-invalid');
        });
        const errors = [];
        const confirmations = [];
        for (const card of state.cards.values()) {
            const section = $id(`card-${card.key}`);
            const p = card.preview;
            const selected = Array.from(section.querySelectorAll('tbody tr')).filter(tr => tr.querySelector('.row-check:checked:not(:disabled)'));
            if (!selected.length) continue;

            const dateEl = $id(`date-${card.key}`);
            if (!dateEl.value) markInvalid(dateEl, `${esc(p.filename)}: enter the collection date`, errors);
            else if (dateEl.value > today()) markInvalid(dateEl, `${esc(p.filename)}: the collection date is in the future`, errors);
            const providerEl = $id(`provider-${card.key}`);
            if (!providerEl.value) markInvalid(providerEl, `${esc(p.filename)}: choose the provider`, errors);

            const edits = {};
            selected.forEach(tr => {
                const index = parseInt(tr.dataset.index, 10);
                const row = p.tests.find(r => r.index === index);
                const name = tr.querySelector('.row-name');
                const result = tr.querySelector('.row-result');
                const label = esc(name.value.trim() || `row ${index + 1}`);
                if (!name.value.trim()) markInvalid(name, `${esc(p.filename)}, ${label}: enter the test name`, errors);
                if (!result.value.trim()) markInvalid(result, `${esc(p.filename)}, ${label}: enter the result`, errors);

                const edit = {};
                if (name.value.trim() !== row.name) edit.name = name.value.trim();
                if (result.value.trim() !== row.result) edit.result = result.value.trim();
                const unit = tr.querySelector('.row-unit').value.trim();
                if (unit !== row.unit) edit.unit = unit;
                const range = tr.querySelector('.row-range').value.trim();
                if (range !== (row.reference_range.text || '')) edit.reference_range = range;
                const lab = tr.querySelector('.row-lab').value;
                if (lab === 'new') edit.new_lab = true;
                else edit.lab_id = parseInt(lab, 10);
                edits[String(index)] = edit;
            });
            confirmations.push({
                import_id: p.import_id,
                selected_tests: selected.map(tr => parseInt(tr.dataset.index, 10)),
                provider_id: parseInt(providerEl.value, 10) || null,
                patient_id: parseInt(getCookie('selectedPatientId') || '1', 10) || 1,
                manual_date: dateEl.value || null,
                edits,
            });
        }
        return { errors, confirmations };
    }

    async function importSelected() {
        const { errors, confirmations } = collect();
        if (errors.length) {
            message(`<strong>Fix ${plural(errors.length, 'thing')} before importing:</strong><ul class="mb-0 mt-1">${errors.join('')}</ul>`);
            return;
        }
        if (!confirmations.length) return message('Tick at least one result to import.');

        const button = $id('importButton');
        button.disabled = true;
        button.querySelector('span').textContent = 'Importing…';
        try {
            const response = await fetch('/api/pdf/batch-confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ individual_confirmations: confirmations }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Import failed.');
            finish(data.data);
        } catch (error) {
            message(`Import failed: ${esc(error.message)}`);
        } finally {
            updateCounts();
        }
    }

    function finish(result) {
        // Imported files leave the review; failed ones stay so they can be fixed
        result.files.forEach(f => {
            for (const card of state.cards.values()) {
                if (String(card.preview.import_id) === String(f.import_id)) { $id(`card-${card.key}`).remove(); state.cards.delete(card.key); }
            }
        });
        if (result.failed_files.length) {
            message(`<strong>${plural(result.failed_files.length, 'file')} couldn't be imported:</strong><ul class="mb-0 mt-1">${result.failed_files.map(f => `<li>${esc(f.error)}</li>`).join('')}</ul>`);
        } else {
            message('');
        }
        updateCounts();
        if (result.files.length) showSummary(result);
        if (typeof refreshHistory === 'function') refreshHistory();
    }

    function showSummary(result) {
        const out = result.files.reduce((n, f) => n + f.out_of_range.length, 0);
        $id('summaryTitle').textContent = `Imported ${plural(result.total_imported, 'result')}`;
        $id('summaryLead').textContent = out
            ? `${plural(out, 'result')} ${out === 1 ? 'is' : 'are'} outside the reference range.`
            : 'All imported results are within their reference ranges.';
        $id('summaryFiles').innerHTML = result.files.map(f => `
            <li class="list-group-item">
                <div class="d-flex flex-wrap justify-content-between">
                    <strong>${esc(f.filename)}</strong>
                    <span class="text-muted">${plural(f.imported_count, 'result')} · collected ${esc(displayDate(f.date_collected))}</span>
                </div>
                ${f.out_of_range.length ? `<ul class="list-unstyled mb-0 mt-2">${f.out_of_range.map(r => `
                    <li class="mb-1"><a href="/lab/${Number(r.lab_id)}">${esc(r.name)}</a>
                        <span class="result-number ml-1">${esc(r.value)} ${esc(r.unit)}</span> ${statusBadge(r.status)}</li>`).join('')}</ul>` : ''}
            </li>`).join('');
        $id('importSummary').hidden = false;
        $id('summaryTitle').focus();
    }

    // ---------- new provider ----------
    let providerTarget = null;
    function openNewProvider(key, useReportName) {
        providerTarget = key;
        $id('newProviderForm').reset();
        $id('newProviderError').classList.add('d-none');
        const card = state.cards.get(key);
        if (useReportName && card && card.preview.physician) $id('providerName').value = card.preview.physician;
        $('#newProviderModal').modal('show');
    }

    async function createProvider(event) {
        event.preventDefault();
        const name = $id('providerName').value.trim();
        const errorBox = $id('newProviderError');
        if (!name) { errorBox.textContent = 'Enter the provider\'s name.'; errorBox.classList.remove('d-none'); $id('providerName').focus(); return; }
        try {
            const response = await fetch('/api/providers/', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, specialty: $id('providerSpecialty').value.trim() || null }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The provider couldn\'t be saved.');
            const provider = data.data || data;
            state.providers.push(provider);
            state.providers.sort((a, b) => a.name.localeCompare(b.name));
            document.querySelectorAll('.review-card select[id^="provider-"]').forEach(select => {
                const keep = select.value;
                select.innerHTML = providerOptions(keep);
            });
            if (providerTarget !== null && $id(`provider-${providerTarget}`)) $id(`provider-${providerTarget}`).value = provider.id;
            $('#newProviderModal').modal('hide');
        } catch (error) {
            errorBox.textContent = error.message;
            errorBox.classList.remove('d-none');
        }
    }

    function reset() {
        state.cards.clear();
        $id('reviewFiles').innerHTML = '';
        $id('uploadList').innerHTML = '';
        $id('uploadProgress').hidden = true;
        message('');
        updateCounts();
    }

    // ---------- wiring ----------
    async function init() {
        await loadReferenceData();
        const input = $id('fileInput');
        const zone = $id('uploadZone');
        input.addEventListener('change', () => handleFiles(input.files));
        // Clicking anywhere in the drop area opens the file picker; keyboard users use the Choose files button
        zone.addEventListener('click', e => { if (!e.target.closest('label, input')) input.click(); });
        ['dragenter', 'dragover'].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.add('dragover'); }));
        ['dragleave', 'drop'].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.remove('dragover'); }));
        zone.addEventListener('drop', e => handleFiles(e.dataTransfer.files));

        const files = $id('reviewFiles');
        files.addEventListener('change', e => {
            const t = e.target;
            if (t.classList.contains('check-all')) {
                t.closest('.review-card').querySelectorAll('.row-check:not(:disabled)').forEach(b => { b.checked = t.checked; });
            }
            if (t.classList.contains('row-lab')) updateRowStatus(t.closest('tr'));
            if (t.matches('input[type="date"], select[id^="provider-"]') && t.value) {
                t.classList.remove('is-invalid'); t.removeAttribute('aria-invalid');
            }
            updateCounts();
        });
        files.addEventListener('input', e => {
            const tr = e.target.closest('tr[data-index]');
            if (tr && e.target.matches('.row-result, .row-range')) updateRowStatus(tr);
            if (e.target.classList.contains('is-invalid') && e.target.value.trim()) {
                e.target.classList.remove('is-invalid'); e.target.removeAttribute('aria-invalid');
            }
            // Typing into an unreadable row's empty fields includes it once both are filled
            if (tr && tr.classList.contains('row-unreadable')) {
                const box = tr.querySelector('.row-check');
                if (!box.checked && tr.querySelector('.row-name').value.trim() && tr.querySelector('.row-result').value.trim()) {
                    box.checked = true; updateCounts();
                }
            }
        });
        files.addEventListener('click', e => {
            const section = e.target.closest('.review-card');
            if (!section) return;
            if (e.target.closest('.toggle-pdf')) togglePdf(section);
            if (e.target.closest('.rescan-ai')) rescan(section);
            if (e.target.closest('.switch-reading')) switchReading(section);
            if (e.target.closest('.remove-card')) {
                state.cards.delete(parseInt(section.dataset.key, 10));
                section.remove();
                updateCounts();
            }
            const add = e.target.closest('[data-new-provider]');
            if (add) { e.preventDefault(); openNewProvider(parseInt(add.dataset.newProvider, 10), add.dataset.reportName === '1'); }
        });
        document.addEventListener('click', e => {
            const review = e.target.closest('[data-review-import]');
            if (review) { e.preventDefault(); openReview(review.dataset.reviewImport); }
        });
        $id('reviewMessages').addEventListener('click', e => {
            const link = e.target.closest('a[href^="#"]');
            if (!link) return;
            e.preventDefault();
            let target = document.querySelector(link.getAttribute('href'));
            if (target && target.tagName === 'DETAILS') { target.open = true; target = target.querySelector('summary'); }
            if (target) { target.scrollIntoView({ block: 'center' }); target.focus(); }
        });
        $id('importButton').addEventListener('click', importSelected);
        $id('cancelReview').addEventListener('click', reset);
        $id('importMore').addEventListener('click', () => { $id('importSummary').hidden = true; reset(); $id('fileInput').click(); });
        $id('newProviderForm').addEventListener('submit', createProvider);

        // /import?review=<id> reopens an earlier upload (linked from history)
        const reviewId = new URLSearchParams(window.location.search).get('review');
        if (reviewId) openReview(reviewId);
    }

    window.PdfReview = { init, openReview };
})();
