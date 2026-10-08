/**
 * Settings → Merge duplicates: find lab tests, panels, units and providers saved more than once,
 * let the user review each suggestion (what to keep, its name), and merge only what they confirm.
 */
(function () {
    const KIND_TITLE = { labs: 'Lab tests', panels: 'Panels', units: 'Units', providers: 'Providers' };
    const COUNT_WORD = { labs: 'result', panels: 'test', units: 'test', providers: 'result' };
    const KIND_NOUN = { labs: 'tests', panels: 'panels', units: 'units', providers: 'providers' };
    const SOURCE = { rule: 'Found by name', ai: 'Suggested by AI', both: 'Found by name and AI' };
    const state = { groups: [] };
    const $id = id => document.getElementById(id);
    const esc = v => escapeHtml(v === null || v === undefined ? '' : String(v));
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

    function status(html, type) {
        $id('duplicatesStatus').innerHTML = html ? `<div class="alert alert-${type || 'info'} mb-0">${html}</div>` : '';
    }

    function itemDetail(kind, item) {
        const parts = [];
        if (kind === 'labs') {
            if (item.unit) parts.push(esc(item.unit));
            if (item.panel) parts.push(esc(item.panel));
        }
        parts.push(plural(item.count, COUNT_WORD[kind]));
        return parts.join(' · ');
    }

    function renderGroup(group, i) {
        const id = `dup-${i}`;
        return `
            <fieldset class="dup-group border rounded p-3 mb-3" data-index="${i}">
                <legend class="sr-only">${esc(group.items.map(it => it.name).join(', '))}</legend>
                <div class="custom-control custom-checkbox mb-2">
                    <input type="checkbox" class="custom-control-input dup-check" id="${id}-check">
                    <label class="custom-control-label font-weight-bold" for="${id}-check">${group.items.length === 2
                        ? `Merge ${esc(group.items[0].name)} and ${esc(group.items[1].name)}`
                        : `Merge ${group.items.length} ${KIND_NOUN[group.kind]} into one: ${esc(group.name)}`}</label>
                </div>
                <p class="small mb-2"><span class="badge badge-status-info mr-1">${SOURCE[group.source] || ''}</span>${esc(group.reason)}${group.ai_reason ? ` · AI: ${esc(group.ai_reason)}` : ''}</p>
                <div class="form-group mb-2" role="radiogroup" aria-labelledby="${id}-keep-label">
                    <div class="small font-weight-bold mb-1" id="${id}-keep-label">Keep</div>
                    ${group.items.map(item => `
                        <div class="custom-control custom-radio">
                            <input type="radio" class="custom-control-input dup-keep" name="${id}-keep" id="${id}-keep-${item.id}" value="${item.id}" ${item.id === group.keep_id ? 'checked' : ''}>
                            <label class="custom-control-label" for="${id}-keep-${item.id}">${esc(item.name)} <span class="text-muted small">${itemDetail(group.kind, item)}</span></label>
                        </div>`).join('')}
                </div>
                <div class="form-group mb-0">
                    <label class="small font-weight-bold" for="${id}-name">Name after merging</label>
                    <input type="text" class="form-control form-control-sm dup-name" id="${id}-name" value="${esc(group.name)}" maxlength="${group.kind === 'units' ? 50 : 255}" style="max-width: 24rem;">
                </div>
            </fieldset>`;
    }

    function render(data) {
        state.groups = data.groups;
        const list = $id('duplicatesList');
        if (!data.groups.length) {
            list.innerHTML = '';
            $id('duplicatesActions').hidden = true;
            status(`<i class="mdi mdi-check-circle mr-1" aria-hidden="true"></i>No likely duplicates found among ${plural(data.totals.labs, 'test')}, ${plural(data.totals.panels, 'panel')}, ${plural(data.totals.units, 'unit')} and ${plural(data.totals.providers, 'provider')}.`, 'success');
            return;
        }
        status(`${plural(data.groups.length, 'possible duplicate')} found. Tick the ones to merge and choose what to keep.`);
        let html = '';
        Object.keys(KIND_TITLE).forEach(kind => {
            const groups = data.groups.map((g, i) => [g, i]).filter(([g]) => g.kind === kind);
            if (!groups.length) return;
            html += `<h3 class="h6 mt-3">${KIND_TITLE[kind]} (${groups.length})</h3>` + groups.map(([g, i]) => renderGroup(g, i)).join('');
        });
        list.innerHTML = html;
        $id('duplicatesActions').hidden = false;
        updateButton();
    }

    function selected() {
        return Array.from(document.querySelectorAll('.dup-group')).filter(f => f.querySelector('.dup-check').checked).map(f => {
            const group = state.groups[parseInt(f.dataset.index, 10)];
            const keepId = parseInt(f.querySelector('.dup-keep:checked').value, 10);
            return { group, fieldset: f, keepId, name: f.querySelector('.dup-name').value.trim() };
        });
    }

    function updateButton() {
        const n = selected().length;
        const button = $id('mergeSelected');
        button.disabled = n === 0;
        button.querySelector('span').textContent = n ? `Merge ${plural(n, 'selected group')}` : 'Merge selected';
    }

    async function find(useAi) {
        const button = useAi ? $id('aiDuplicates') : $id('findDuplicates');
        const label = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<i class="mdi mdi-loading mdi-spin mr-1" aria-hidden="true"></i>${useAi ? 'AI is reviewing…' : 'Looking…'}`;
        try {
            const response = await fetch(useAi ? '/api/cleanup/suggestions/ai' : '/api/cleanup/suggestions', { method: useAi ? 'POST' : 'GET' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Search failed');
            render(data);
        } catch (error) {
            status(esc(error.message), 'danger');
        } finally {
            button.disabled = false;
            button.innerHTML = label;
        }
    }

    function confirmMerges() {
        const picks = selected();
        // An item can only be merged once; overlapping groups have to be merged one at a time
        const seen = new Map();
        for (const p of picks) {
            for (const item of p.group.items) {
                const key = `${p.group.kind}:${item.id}`;
                if (seen.has(key)) {
                    status(`${esc(item.name)} is in two ticked groups. Untick one, merge, then search again.`, 'danger');
                    return;
                }
                seen.set(key, true);
            }
            if (!p.name) {
                status('Enter a name for each merged item.', 'danger');
                p.fieldset.querySelector('.dup-name').focus();
                return;
            }
        }
        $id('mergeConfirmList').innerHTML = picks.map(p => {
            const keep = p.group.items.find(i => i.id === p.keepId);
            const others = p.group.items.filter(i => i.id !== p.keepId);
            const moved = others.reduce((n, i) => n + i.count, 0);
            return `<li class="list-group-item">
                <strong>${esc(p.name)}</strong>${p.name !== keep.name ? ` <span class="text-muted">(renamed from ${esc(keep.name)})</span>` : ''}
                <div class="small">Merges in: ${others.map(i => esc(i.name)).join(', ')} · ${plural(moved, COUNT_WORD[p.group.kind])} moved</div>
            </li>`;
        }).join('');
        $('#mergeConfirmModal').modal('show');
    }

    async function merge() {
        const picks = selected();
        const button = $id('mergeConfirmButton');
        button.disabled = true;
        try {
            const response = await fetch('/api/cleanup/merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    merges: picks.map(p => ({
                        kind: p.group.kind,
                        keep_id: p.keepId,
                        merge_ids: p.group.items.map(i => i.id).filter(id => id !== p.keepId),
                        name: p.name,
                    })),
                }),
            });
            const data = await response.json();
            $('#mergeConfirmModal').modal('hide');
            if (!response.ok) throw new Error(data.detail || 'Merging failed');
            const done = data.merged.map(m => `<li>${esc(m.kept)} ← ${m.merged.map(esc).join(', ')}</li>`).join('');
            await find(false);
            status(`<i class="mdi mdi-check-circle mr-1" aria-hidden="true"></i><strong>Merged ${plural(data.merged.length, 'group')}:</strong><ul class="mb-0 mt-1">${done}</ul>`
                + ($id('duplicatesList').children.length ? '<div class="mt-1">More possible duplicates are listed below.</div>' : ''), 'success');
        } catch (error) {
            status(`Nothing was merged: ${esc(error.message)}`, 'danger');
        } finally {
            button.disabled = false;
        }
    }

    async function init() {
        if (!$id('mergeDuplicates')) return;
        $id('findDuplicates').addEventListener('click', () => find(false));
        $id('aiDuplicates').addEventListener('click', () => find(true));
        $id('mergeSelected').addEventListener('click', confirmMerges);
        $id('mergeConfirmButton').addEventListener('click', merge);
        const list = $id('duplicatesList');
        list.addEventListener('change', e => {
            const fieldset = e.target.closest('.dup-group');
            if (e.target.classList.contains('dup-keep')) {
                // Follow the chosen item's name unless the user has typed their own
                const group = state.groups[parseInt(fieldset.dataset.index, 10)];
                const nameEl = fieldset.querySelector('.dup-name');
                if (group.items.some(i => i.name === nameEl.value) || nameEl.value === group.name) {
                    nameEl.value = group.items.find(i => String(i.id) === e.target.value).name;
                }
                fieldset.querySelector('.dup-check').checked = true;
            }
            updateButton();
        });
        try {
            const ai = await fetch('/api/pdf/ai-status').then(r => r.ok ? r.json() : {});
            $id('aiDuplicates').hidden = $id('aiDuplicatesHelp').hidden = !ai.enabled;
        } catch (e) { /* AI button stays hidden */ }
    }

    window.MergeDuplicates = { init };
})();
