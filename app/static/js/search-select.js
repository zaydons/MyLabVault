/* Search dropdown: turns a <select> into a text box that filters its options as you type.
 *
 *     <select id="labSelect" data-search-select data-search-placeholder="Search tests…">
 *       <optgroup label="Chemistry"><option value="3" data-detail="12 results">Glucose</option></optgroup>
 *     </select>
 *     SearchSelect.enhance(document.getElementById('labSelect'));
 *
 * The <select> stays in the page (hidden) and holds the value, so code that reads it, sets it with
 * $(select).val(x).trigger('change'), or listens for 'change' works as before. Every typed word must
 * appear in the option's name, its detail or its group, in any order. Arrow keys move, Enter picks,
 * Escape closes; the ARIA combobox pattern lets screen readers follow along.
 */
(function () {
    let nextId = 0;

    const words = text => text.toLowerCase().split(/\s+/).filter(Boolean);

    function enhance(select) {
        if (!select || select.dataset.searchSelectReady) return;
        select.dataset.searchSelectReady = '1';
        const id = select.id || `search-select-${++nextId}`;
        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);

        const box = document.createElement('div');
        box.className = 'search-select';
        const icon = document.createElement('i');
        icon.className = 'mdi mdi-magnify search-select-icon';
        icon.setAttribute('aria-hidden', 'true');
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-control';
        input.id = `${id}-search`;
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.placeholder = select.dataset.searchPlaceholder || 'Type to search…';
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', `${id}-list`);
        const caret = document.createElement('i');
        caret.className = 'mdi mdi-chevron-down search-select-caret';
        caret.setAttribute('aria-hidden', 'true');
        const list = document.createElement('div');
        list.className = 'search-select-list';
        list.id = `${id}-list`;
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        const status = document.createElement('div');
        status.className = 'sr-only';
        status.setAttribute('role', 'status');

        // The visible label now names the text box, and the hidden select is left out of the page's tab order
        if (label) {
            label.htmlFor = input.id;
            label.id = label.id || `${id}-label`;
            list.setAttribute('aria-labelledby', label.id);
        }
        select.hidden = true;
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');
        box.append(icon, input, caret, list, status);
        select.after(box);

        let shown = [];     // option elements currently listed, in order
        let active = -1;    // index into shown

        const current = () => select.options[select.selectedIndex];
        const showCurrent = () => {
            const opt = current();
            input.value = opt && opt.value ? opt.text : '';
        };

        function render(query) {
            const terms = words(query);
            list.replaceChildren();
            shown = [];
            const groups = new Map();
            for (const opt of select.options) {
                if (!opt.value || opt.disabled) continue;
                const group = opt.parentElement.tagName === 'OPTGROUP' ? opt.parentElement.label : '';
                const haystack = `${opt.text} ${opt.dataset.detail || ''} ${group}`.toLowerCase();
                if (!terms.every(t => haystack.includes(t))) continue;
                if (!groups.has(group)) groups.set(group, []);
                groups.get(group).push(opt);
            }
            for (const [group, opts] of groups) {
                let parent = list;
                if (group) {
                    parent = document.createElement('div');
                    parent.setAttribute('role', 'group');
                    const heading = document.createElement('div');
                    heading.className = 'search-select-group';
                    heading.id = `${list.id}-g${list.children.length}`;
                    heading.setAttribute('role', 'presentation');
                    heading.textContent = group;
                    parent.setAttribute('aria-labelledby', heading.id);
                    parent.append(heading);
                    list.append(parent);
                }
                for (const opt of opts) {
                    const li = document.createElement('div');
                    li.id = `${list.id}-o${shown.length}`;
                    li.setAttribute('role', 'option');
                    li.setAttribute('aria-selected', 'false');
                    li.dataset.index = shown.length;
                    const isCurrent = opt === current();
                    if (isCurrent) li.classList.add('current');
                    const check = document.createElement('i');
                    check.className = `mdi ${isCurrent ? 'mdi-check' : 'mdi-blank'} search-select-check`;
                    check.setAttribute('aria-hidden', 'true');
                    const name = document.createElement('span');
                    name.className = 'name';
                    name.textContent = opt.text;
                    li.append(check, name);
                    if (opt.dataset.detail) {
                        const detail = document.createElement('span');
                        detail.className = 'detail';
                        detail.textContent = opt.dataset.detail;
                        li.append(detail);
                    }
                    if (isCurrent) {
                        const sr = document.createElement('span');
                        sr.className = 'sr-only';
                        sr.textContent = ' (current)';
                        li.append(sr);
                    }
                    parent.append(li);
                    shown.push(opt);
                }
            }
            if (!shown.length) {
                const li = document.createElement('div');
                li.className = 'empty';
                li.setAttribute('role', 'presentation');
                li.textContent = select.dataset.searchEmpty || 'No matches';
                list.append(li);
            }
            status.textContent = shown.length === 1 ? '1 match' : `${shown.length} matches`;
            const at = shown.indexOf(current());
            setActive(terms.length ? 0 : at);
        }

        function setActive(index) {
            const previous = list.querySelector('[aria-selected="true"]');
            if (previous) previous.setAttribute('aria-selected', 'false');
            active = shown.length ? Math.max(-1, Math.min(index, shown.length - 1)) : -1;
            const li = active >= 0 ? document.getElementById(`${list.id}-o${active}`) : null;
            if (li) {
                li.setAttribute('aria-selected', 'true');
                input.setAttribute('aria-activedescendant', li.id);
                li.scrollIntoView({ block: 'nearest' });
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function open(query = '') {
            render(query);
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            place();
        }

        // Open upward when the box is near the bottom of the screen, and never run off it
        function place() {
            list.classList.remove('above');
            list.style.maxHeight = '';
            const rect = input.getBoundingClientRect();
            const below = window.innerHeight - rect.bottom - 12;
            const above = rect.top - 12;
            const wanted = Math.min(list.scrollHeight, parseFloat(getComputedStyle(list).maxHeight) || Infinity);
            const up = wanted > below && above > below;
            list.classList.toggle('above', up);
            list.style.maxHeight = `${Math.max(120, Math.min(wanted, up ? above : below))}px`;
        }

        function close(restore = true) {
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
            status.textContent = '';
            if (restore) showCurrent();
        }

        function choose(opt) {
            close(false);
            if (opt && opt !== current()) {
                select.value = opt.value;
                select.dispatchEvent(new Event('change', { bubbles: true }));  // jQuery handlers get this too
            }
            showCurrent();
        }

        input.addEventListener('focus', () => { input.select(); open(); });
        input.addEventListener('click', () => { if (list.hidden) open(); });
        input.addEventListener('input', () => open(input.value));
        input.addEventListener('keydown', e => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (list.hidden) return open();
                setActive(active + (e.key === 'ArrowDown' ? 1 : -1));
            } else if (e.key === 'Home' && !list.hidden && e.ctrlKey) {
                e.preventDefault(); setActive(0);
            } else if (e.key === 'End' && !list.hidden && e.ctrlKey) {
                e.preventDefault(); setActive(shown.length - 1);
            } else if (e.key === 'Enter') {
                if (!list.hidden) {
                    e.preventDefault();
                    if (active >= 0) choose(shown[active]);
                }
            } else if (e.key === 'Escape') {
                if (!list.hidden) { e.preventDefault(); close(); input.select(); }
            } else if (e.key === 'Tab') {
                close();
            }
        });
        // mousedown, not click, so the pick happens before the text box loses focus
        list.addEventListener('mousedown', e => {
            e.preventDefault();
            const li = e.target.closest('[role="option"]');
            if (li) choose(shown[+li.dataset.index]);
        });
        caret.addEventListener('mousedown', e => { e.preventDefault(); list.hidden ? input.focus() : close(); });
        input.addEventListener('blur', () => close());

        // Keep the text in step when other code changes the select, e.g. $(select).val(x).trigger('change')
        select.addEventListener('change', showCurrent);
        if (window.jQuery) jQuery(select).on('change', showCurrent);
        showCurrent();
        return input;
    }

    window.SearchSelect = { enhance };
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('select[data-search-select]').forEach(enhance);
    });
})();
