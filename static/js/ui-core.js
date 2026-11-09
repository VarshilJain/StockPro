/**
 * StockPro UI Core & Shared Foundations (Stage P1)
 * Includes:
 *  - fmt: Standardized formatting for dates, prices, percentages, quantities, large INR, and tickers
 *  - SymbolCombobox: Universal accessible symbol picker component
 *  - TableHelper: Table density, boolean dots, empty states, and capped result footers
 *  - Toast: Undo-capable toast notification manager
 */

(function (window) {
    'use strict';

    /* ═══════════════════════════════════════════════════════════════
       1. FORMATTING UTILITIES (fmt) [G8, G9, G10]
       ═══════════════════════════════════════════════════════════════ */
    const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const _monthsFull = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

    const _indexNames = {
        '^NSEI': 'Nifty 50',
        '^CRSLDX': 'Nifty 500',
        '^NSEBANK': 'Nifty Bank',
        '^CNXIT': 'Nifty IT',
        '^BSESN': 'Sensex',
        'NIFTY 50': 'Nifty 50',
        'NIFTY 500': 'Nifty 500',
        'NIFTY BANK': 'Nifty Bank',
        'NIFTY IT': 'Nifty IT',
        'NIFTY AUTO': 'Nifty Auto',
        'NIFTY PHARMA': 'Nifty Pharma',
        'NIFTY FMCG': 'Nifty FMCG',
        'NIFTY METAL': 'Nifty Metal',
        'NIFTY REALTY': 'Nifty Realty',
        'NIFTY ENERGY': 'Nifty Energy',
        'NIFTY INFRA': 'Nifty Infra'
    };

    const _symbolNameCache = {};

    const fmt = {
        parseDate(d) {
            if (!d) return null;
            if (d instanceof Date) return isNaN(d.getTime()) ? null : d;
            if (typeof d === 'number') {
                const date = new Date(d < 1e11 ? d * 1000 : d);
                return isNaN(date.getTime()) ? null : date;
            }
            if (typeof d === 'string') {
                const s = d.trim();
                // Match YYYY-MM-DD or YYYY-MM-DDTHH:mm:ss
                const parts = s.split(/[-T :]/);
                if (parts.length >= 3 && parts[0].length === 4) {
                    const y = parseInt(parts[0], 10);
                    const m = parseInt(parts[1], 10) - 1;
                    const day = parseInt(parts[2], 10);
                    if (!isNaN(y) && !isNaN(m) && !isNaN(day)) {
                        return new Date(y, m, day);
                    }
                }
                const parsed = new Date(s);
                return isNaN(parsed.getTime()) ? null : parsed;
            }
            return null;
        },

        /**
         * fmt.date(d) -> "08 Oct 2026"
         * Never M/D/Y, never ISO in visible UI.
         */
        date(d) {
            const dt = this.parseDate(d);
            if (!dt) return typeof d === 'string' && d.length > 0 ? d : '—';
            const day = String(dt.getDate()).padStart(2, '0');
            const mon = _months[dt.getMonth()];
            const yr = dt.getFullYear();
            return `${day} ${mon} ${yr}`;
        },

        /**
         * fmt.dateShort(d) -> "08 Oct"
         */
        dateShort(d) {
            const dt = this.parseDate(d);
            if (!dt) return typeof d === 'string' && d.length > 0 ? d : '—';
            const day = String(dt.getDate()).padStart(2, '0');
            const mon = _months[dt.getMonth()];
            return `${day} ${mon}`;
        },

        /**
         * fmt.price(n) -> "₹1,178.00"
         * Indian rupee formatting with 2 decimals.
         */
        price(n) {
            if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
            const num = Number(n);
            return '₹' + num.toLocaleString('en-IN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        },

        /**
         * fmt.pct(n) -> "+2.34%" / "−1.49%"
         * Uses true minus sign U+2212, always signed.
         */
        pct(n) {
            if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
            const num = Number(n);
            const sign = num > 0 ? '+' : (num < 0 ? '\u2212' : '');
            const abs = Math.abs(num).toFixed(2);
            return `${sign}${abs}%`;
        },

        /**
         * fmt.qty(n) -> Indian short scale
         * 12,152,819 -> "1.22 Cr", 845,000 -> "8.45 L", under 1 lakh -> "84,500"
         */
        qty(n) {
            if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
            const num = Number(n);
            const abs = Math.abs(num);
            const sign = num < 0 ? '\u2212' : '';
            if (abs >= 10000000) {
                return `${sign}${(abs / 10000000).toFixed(2)} Cr`;
            }
            if (abs >= 100000) {
                return `${sign}${(abs / 100000).toFixed(2)} L`;
            }
            return `${sign}${Math.round(abs).toLocaleString('en-IN')}`;
        },

        /**
         * fmt.inrLarge(n) for fundamentals
         * Convention: L Cr (Lakh Crore) / Cr (Crore)
         * e.g. ₹2.37 L Cr, ₹529.95 Cr
         */
        inrLarge(n) {
            if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
            const num = Number(n);
            const abs = Math.abs(num);
            const sign = num < 0 ? '\u2212' : '';
            // If raw INR number (typical for market cap, revenue)
            if (abs >= 1e12) {
                return `${sign}₹${(abs / 1e12).toFixed(2)} L Cr`;
            }
            if (abs >= 1e7) {
                return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`;
            }
            if (abs >= 1e5) {
                return `${sign}₹${(abs / 1e5).toFixed(2)} L`;
            }
            return `${sign}₹${num.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
        },

        /**
         * fmt.ticker(sym) -> strips .NS / .BO for display
         */
        ticker(sym) {
            if (!sym) return '';
            return String(sym).replace(/\.(NS|BO)$/i, '').trim();
        },

        /**
         * Symbol to Name Map registration & lookup
         */
        registerSymbolNames(obj) {
            if (obj && typeof obj === 'object') {
                for (const [k, v] of Object.entries(obj)) {
                    if (v) {
                        const cleanK = this.ticker(k);
                        const cleanV = typeof v === 'string' ? v : (v.company_name || v.name || '');
                        if (cleanV) {
                            _symbolNameCache[k] = cleanV;
                            _symbolNameCache[cleanK] = cleanV;
                        }
                    }
                }
            }
        },

        getName(sym) {
            if (!sym) return '';
            const raw = String(sym).trim();
            const clean = this.ticker(raw);
            if (_indexNames[raw]) return _indexNames[raw];
            if (_indexNames[clean]) return _indexNames[clean];
            if (_symbolNameCache[raw]) return _symbolNameCache[raw];
            if (_symbolNameCache[clean]) return _symbolNameCache[clean];
            // If unknown, return empty string (symbol itself is displayed)
            return '';
        },

        /**
         * fmt.tickerLabel(sym, name) -> renders RELIANCE with company name secondary
         */
        tickerLabel(sym, name) {
            const t = this.ticker(sym);
            const n = name || this.getName(sym);
            if (n && n.toLowerCase() !== t.toLowerCase()) {
                return `<span class="ticker-badge">${t}</span><span class="ticker-company-name">${n}</span>`;
            }
            return `<span class="ticker-badge">${t}</span>`;
        }
    };

    /* ═══════════════════════════════════════════════════════════════
       2. SYMBOL COMBOBOX COMPONENT (createSymbolCombobox) [G11, C1, AL2, F1]
       ═══════════════════════════════════════════════════════════════ */
    const POPULAR_INDICES = [
        { symbol: '^NSEI', name: 'Nifty 50', type: 'INDEX' },
        { symbol: '^CRSLDX', name: 'Nifty 500', type: 'INDEX' },
        { symbol: '^NSEBANK', name: 'Nifty Bank', type: 'INDEX' },
        { symbol: '^CNXIT', name: 'Nifty IT', type: 'INDEX' },
        { symbol: 'NIFTY AUTO', name: 'Nifty Auto', type: 'INDEX' },
        { symbol: 'NIFTY PHARMA', name: 'Nifty Pharma', type: 'INDEX' },
        { symbol: 'NIFTY METAL', name: 'Nifty Metal', type: 'INDEX' }
    ];

    function getRecentSymbols() {
        try {
            const raw = localStorage.getItem('stockpro_recent_symbols');
            return raw ? JSON.parse(raw) : ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN'];
        } catch (e) {
            return ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY'];
        }
    }

    function saveRecentSymbol(sym) {
        if (!sym) return;
        const clean = fmt.ticker(sym);
        try {
            let recents = getRecentSymbols();
            recents = recents.filter(s => fmt.ticker(s) !== clean);
            recents.unshift(clean);
            if (recents.length > 6) recents = recents.slice(0, 6);
            localStorage.setItem('stockpro_recent_symbols', JSON.stringify(recents));
        } catch (e) {}
    }

    let _comboboxInstances = [];

    function createSymbolCombobox(targetEl, options = {}) {
        if (!targetEl) return null;
        const opts = Object.assign({
            placeholder: 'Search stock or company…',
            initialValue: '',
            onSelect: null,
            allowCustom: true,
            id: targetEl.id || ('combobox_' + Math.random().toString(36).substring(2, 9))
        }, options);

        // If targetEl is a SELECT or INPUT, convert/wrap it
        let wrapper = targetEl.closest('.symbol-combobox-wrapper');
        let inputEl;

        if (!wrapper) {
            wrapper = document.createElement('div');
            wrapper.className = 'symbol-combobox-wrapper';
            targetEl.parentNode.insertBefore(wrapper, targetEl);

            if (targetEl.tagName.toLowerCase() === 'select') {
                inputEl = document.createElement('input');
                inputEl.type = 'text';
                inputEl.id = targetEl.id;
                inputEl.name = targetEl.name || targetEl.id;
                inputEl.className = (targetEl.className || 'form-input') + ' symbol-combobox-input';
                targetEl.style.display = 'none';
                wrapper.appendChild(inputEl);
            } else {
                inputEl = targetEl;
                inputEl.classList.add('symbol-combobox-input');
                wrapper.appendChild(inputEl);
            }
        } else {
            inputEl = wrapper.querySelector('input') || targetEl;
        }

        inputEl.placeholder = opts.placeholder;
        inputEl.setAttribute('role', 'combobox');
        inputEl.setAttribute('aria-autocomplete', 'list');
        inputEl.setAttribute('aria-expanded', 'false');
        inputEl.setAttribute('autocomplete', 'off');
        inputEl.setAttribute('spellcheck', 'false');

        // Search icon prefix if not present
        if (!wrapper.querySelector('.symbol-combobox-icon')) {
            const icon = document.createElement('i');
            icon.className = 'fas fa-search symbol-combobox-icon';
            wrapper.appendChild(icon);
        }

        // Dropdown menu
        let dropdown = wrapper.querySelector('.symbol-combobox-dropdown');
        if (!dropdown) {
            dropdown = document.createElement('div');
            dropdown.className = 'symbol-combobox-dropdown';
            dropdown.setAttribute('role', 'listbox');
            dropdown.id = opts.id + '_listbox';
            inputEl.setAttribute('aria-controls', dropdown.id);
            wrapper.appendChild(dropdown);
        }

        let highlightedIndex = -1;
        let currentOptions = [];
        let debounceTimer = null;

        function renderOptions(items, isDefaultList = false) {
            currentOptions = items.slice(0, 50);
            highlightedIndex = -1;
            dropdown.innerHTML = '';

            if (!currentOptions.length) {
                dropdown.innerHTML = '<div class="symbol-combobox-empty">No matching stocks found</div>';
                dropdown.classList.add('open');
                inputEl.setAttribute('aria-expanded', 'true');
                return;
            }

            if (isDefaultList) {
                const recentList = getRecentSymbols();
                if (recentList.length) {
                    const header = document.createElement('div');
                    header.className = 'symbol-combobox-group-header';
                    header.innerHTML = '<i class="fas fa-history"></i> Recent Searches';
                    dropdown.appendChild(header);

                    recentList.forEach((sym, idx) => {
                        const opt = createOptionEl(sym, fmt.getName(sym), 'NSE', idx, 'recent');
                        dropdown.appendChild(opt);
                    });
                }

                const indicesHeader = document.createElement('div');
                indicesHeader.className = 'symbol-combobox-group-header';
                indicesHeader.innerHTML = '<i class="fas fa-chart-line"></i> Popular Indices';
                dropdown.appendChild(indicesHeader);

                POPULAR_INDICES.forEach((ind, idx) => {
                    const opt = createOptionEl(ind.symbol, ind.name, ind.type, idx + 100, 'index');
                    dropdown.appendChild(opt);
                });

                dropdown.classList.add('open');
                inputEl.setAttribute('aria-expanded', 'true');
                return;
            }

            // Normal search results (Top 8 shown prominently)
            currentOptions.slice(0, 8).forEach((item, idx) => {
                const sym = typeof item === 'string' ? item : item.symbol;
                const name = typeof item === 'string' ? fmt.getName(sym) : (item.name || fmt.getName(sym));
                const exchange = (typeof item === 'object' && item.exchange) ? item.exchange : 'NSE';
                const opt = createOptionEl(sym, name, exchange, idx, 'result');
                dropdown.appendChild(opt);
            });

            dropdown.classList.add('open');
            inputEl.setAttribute('aria-expanded', 'true');
        }

        function createOptionEl(symbol, name, exchange, idx, kind) {
            const opt = document.createElement('div');
            opt.className = 'symbol-combobox-option';
            opt.setAttribute('role', 'option');
            opt.setAttribute('id', `${opts.id}_opt_${idx}`);
            opt.dataset.symbol = symbol;
            opt.dataset.index = idx;

            const cleanSym = fmt.ticker(symbol);
            const company = name || cleanSym;

            opt.innerHTML = `
                <div class="combobox-opt-left">
                    <span class="combobox-opt-sym">${cleanSym}</span>
                    <span class="combobox-opt-name">${company !== cleanSym ? company : ''}</span>
                </div>
                <div class="combobox-opt-right">
                    <span class="combobox-badge">${exchange || 'NSE'}</span>
                </div>
            `;

            opt.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectItem(symbol);
            });

            return opt;
        }

        function filterSymbols(query) {
            const q = query.trim().toUpperCase();
            if (!q) {
                renderOptions([], true);
                return;
            }

            const cleanQ = fmt.ticker(q);
            const masterList = window.allSymbols || [];
            const prefixMatches = [];
            const substringMatches = [];

            for (let i = 0; i < masterList.length; i++) {
                const s = masterList[i];
                const cleanS = fmt.ticker(s).toUpperCase();
                const name = (fmt.getName(s) || '').toUpperCase();

                if (cleanS.startsWith(cleanQ)) {
                    prefixMatches.push({ symbol: s, name: fmt.getName(s), exchange: 'NSE' });
                } else if (cleanS.includes(cleanQ) || (name && name.includes(cleanQ))) {
                    substringMatches.push({ symbol: s, name: fmt.getName(s), exchange: 'NSE' });
                }

                if (prefixMatches.length + substringMatches.length >= 50) break;
            }

            const combined = prefixMatches.concat(substringMatches);
            renderOptions(combined, false);
        }

        function selectItem(symbol) {
            const clean = fmt.ticker(symbol);
            inputEl.value = clean;
            saveRecentSymbol(clean);
            closeDropdown();

            // Sync hidden select if present
            if (targetEl.tagName.toLowerCase() === 'select') {
                targetEl.value = symbol;
                // If not found by full name, try matching clean
                if (!targetEl.value) {
                    for (let i = 0; i < targetEl.options.length; i++) {
                        if (fmt.ticker(targetEl.options[i].value) === clean) {
                            targetEl.selectedIndex = i;
                            break;
                        }
                    }
                }
                const evt = new Event('change', { bubbles: true });
                targetEl.dispatchEvent(evt);
            }

            const changeEvt = new Event('change', { bubbles: true });
            inputEl.dispatchEvent(changeEvt);

            if (typeof opts.onSelect === 'function') {
                opts.onSelect(symbol, clean);
            }
        }

        function highlightOption(idx) {
            const options = dropdown.querySelectorAll('.symbol-combobox-option');
            options.forEach(o => o.classList.remove('highlighted'));
            if (idx >= 0 && idx < options.length) {
                const current = options[idx];
                current.classList.add('highlighted');
                current.scrollIntoView({ block: 'nearest' });
                inputEl.setAttribute('aria-activedescendant', current.id);
                highlightedIndex = idx;
            } else {
                inputEl.removeAttribute('aria-activedescendant');
                highlightedIndex = -1;
            }
        }

        function closeDropdown() {
            dropdown.classList.remove('open');
            inputEl.setAttribute('aria-expanded', 'false');
            inputEl.removeAttribute('aria-activedescendant');
            highlightedIndex = -1;
        }

        // Event listeners
        inputEl.addEventListener('focus', () => {
            if (!inputEl.value.trim()) {
                renderOptions([], true);
            } else {
                filterSymbols(inputEl.value);
            }
        });

        inputEl.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                filterSymbols(inputEl.value);
            }, 120);
        });

        inputEl.addEventListener('keydown', (e) => {
            const options = dropdown.querySelectorAll('.symbol-combobox-option');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (!dropdown.classList.contains('open')) {
                    filterSymbols(inputEl.value);
                } else {
                    highlightOption(Math.min(highlightedIndex + 1, options.length - 1));
                }
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (dropdown.classList.contains('open')) {
                    highlightOption(Math.max(highlightedIndex - 1, 0));
                }
            } else if (e.key === 'Enter') {
                if (dropdown.classList.contains('open')) {
                    e.preventDefault();
                    if (highlightedIndex >= 0 && options[highlightedIndex]) {
                        const sym = options[highlightedIndex].dataset.symbol;
                        selectItem(sym);
                    } else if (options.length > 0) {
                        selectItem(options[0].dataset.symbol);
                    } else if (opts.allowCustom && inputEl.value.trim()) {
                        selectItem(inputEl.value.trim());
                    }
                }
            } else if (e.key === 'Escape') {
                if (dropdown.classList.contains('open')) {
                    e.preventDefault();
                    closeDropdown();
                }
            }
        });

        document.addEventListener('click', (e) => {
            if (!wrapper.contains(e.target)) {
                closeDropdown();
            }
        });

        const instance = {
            wrapper,
            inputEl,
            setValue: (val) => {
                const clean = fmt.ticker(val);
                inputEl.value = clean;
                if (targetEl.tagName.toLowerCase() === 'select') {
                    targetEl.value = val;
                }
            },
            getValue: () => inputEl.value.trim(),
            focus: () => {
                inputEl.focus();
                inputEl.select();
            }
        };

        _comboboxInstances.push(instance);
        return instance;
    }


    /* ═══════════════════════════════════════════════════════════════
       3. TABLE STANDARD & UTILITIES [G19, S2, T3, AL4, F2]
       ═══════════════════════════════════════════════════════════════ */
    const TableHelper = {
        getDensity() {
            return localStorage.getItem('stockpro_table_density') || 'compact';
        },

        setDensity(density) {
            const valid = density === 'comfortable' ? 'comfortable' : 'compact';
            localStorage.setItem('stockpro_table_density', valid);
            document.documentElement.setAttribute('data-density', valid);
            document.querySelectorAll('.data-table, table.table').forEach(tbl => {
                tbl.classList.toggle('density-comfortable', valid === 'comfortable');
                tbl.classList.toggle('density-compact', valid === 'compact');
            });
            const toggleBtns = document.querySelectorAll('#tableDensityToggle, .density-toggle-btn');
            toggleBtns.forEach(toggleBtn => {
                toggleBtn.setAttribute('title', `Density: ${valid} (click to toggle)`);
                toggleBtn.innerText = valid === 'comfortable' ? 'Comfortable' : 'Compact';
            });
            const densityLabel = document.getElementById('avatarDensityLabel');
            if (densityLabel) {
                densityLabel.innerText = valid === 'comfortable' ? 'Comfortable' : 'Compact';
            }
            window.dispatchEvent(new CustomEvent('densitychange', { detail: { density: valid } }));
        },

        toggleDensity() {
            const current = this.getDensity();
            this.setDensity(current === 'compact' ? 'comfortable' : 'compact');
        },

        applyDensityToAll() {
            this.setDensity(this.getDensity());
        },

        /**
         * Render boolean dot (filled green dot if true, empty muted circle if false)
         */
        signalDot(isTrue, label = '') {
            const boolVal = Boolean(isTrue && isTrue !== '0' && isTrue !== 0 && isTrue !== 'false');
            if (boolVal) {
                return `<span class="signal-dot signal-dot-true" title="${label || 'True'}"><span class="sr-only">True</span></span>`;
            }
            return `<span class="signal-dot signal-dot-false" title="${label || 'False'}"><span class="sr-only">False</span></span>`;
        },

        /**
         * Render capped results footer
         */
        renderFooter(displayed, total, onExportAll) {
            if (!total || total <= displayed) {
                return `<div class="data-table-footer"><span>Showing all ${displayed} results</span></div>`;
            }
            return `
                <div class="data-table-footer">
                    <span>Showing ${displayed} of ${total} results</span>
                    <button type="button" class="btn-link btn-export-all" onclick="${onExportAll || 'exportTableData()'}">
                        <i class="fas fa-file-export"></i> Export all ${total}
                    </button>
                </div>
            `;
        },

        /**
         * Standard Empty State HTML
         */
        emptyState(colSpan, message = 'No data available', actionHtml = '') {
            return `
                <tr>
                    <td colspan="${colSpan}" class="table-empty-td">
                        <div class="table-empty-box">
                            <i class="fas fa-folder-open table-empty-icon"></i>
                            <div class="table-empty-msg">${message}</div>
                            ${actionHtml ? `<div class="table-empty-action">${actionHtml}</div>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }
    };

    /* ═══════════════════════════════════════════════════════════════
       4. TOAST NOTIFICATION SYSTEM (with Undo support) [AL4, SB2]
       ═══════════════════════════════════════════════════════════════ */
    let _toastContainer = null;

    function getToastContainer() {
        if (!_toastContainer || !document.body.contains(_toastContainer)) {
            _toastContainer = document.getElementById('appToastContainer');
            if (!_toastContainer) {
                _toastContainer = document.createElement('div');
                _toastContainer.id = 'appToastContainer';
                _toastContainer.className = 'toast-container';
                _toastContainer.setAttribute('aria-live', 'polite');
                document.body.appendChild(_toastContainer);
            }
        }
        return _toastContainer;
    }

    const Toast = {
        show(message, type = 'info', durationMs = 4000) {
            const container = getToastContainer();
            const toast = document.createElement('div');
            toast.className = `app-toast toast-${type}`;
            const iconMap = {
                success: 'fa-check-circle',
                danger: 'fa-exclamation-circle',
                warning: 'fa-exclamation-triangle',
                info: 'fa-info-circle'
            };
            const icon = iconMap[type] || 'fa-info-circle';

            toast.innerHTML = `
                <div class="toast-content">
                    <i class="fas ${icon} toast-icon"></i>
                    <span class="toast-text">${message}</span>
                </div>
                <button type="button" class="btn-reset toast-close" aria-label="Dismiss">&times;</button>
            `;

            toast.querySelector('.toast-close').onclick = () => removeToast(toast);
            container.appendChild(toast);

            setTimeout(() => toast.classList.add('visible'), 10);
            const timer = setTimeout(() => removeToast(toast), durationMs);

            function removeToast(el) {
                clearTimeout(timer);
                el.classList.remove('visible');
                setTimeout(() => el.remove(), 250);
            }
            return toast;
        },

        success(msg, ms) { return this.show(msg, 'success', ms); },
        danger(msg, ms) { return this.show(msg, 'danger', ms); },
        info(msg, ms) { return this.show(msg, 'info', ms); },

        /**
         * Toast with Undo action (used for Alert deletion, Saved Scan deletion)
         */
        withUndo(message, onUndo, onCommit, timeoutMs = 5000) {
            const container = getToastContainer();
            const toast = document.createElement('div');
            toast.className = 'app-toast toast-undo';

            toast.innerHTML = `
                <div class="toast-content">
                    <i class="fas fa-trash-alt toast-icon"></i>
                    <span class="toast-text">${message}</span>
                </div>
                <div class="toast-actions">
                    <button type="button" class="btn-toast-undo">Undo</button>
                    <button type="button" class="btn-reset toast-close" aria-label="Dismiss">&times;</button>
                </div>
            `;

            let executed = false;
            let timer = null;

            function finalize() {
                if (executed) return;
                executed = true;
                toast.classList.remove('visible');
                setTimeout(() => toast.remove(), 250);
                if (typeof onCommit === 'function') onCommit();
            }

            function handleUndo() {
                if (executed) return;
                executed = true;
                clearTimeout(timer);
                toast.classList.remove('visible');
                setTimeout(() => toast.remove(), 250);
                if (typeof onUndo === 'function') onUndo();
            }

            toast.querySelector('.btn-toast-undo').onclick = handleUndo;
            toast.querySelector('.toast-close').onclick = finalize;

            container.appendChild(toast);
            setTimeout(() => toast.classList.add('visible'), 10);
            timer = setTimeout(finalize, timeoutMs);
        }
    };

    // Export to global window
    window.fmt = fmt;
    window.createSymbolCombobox = createSymbolCombobox;
    window.TableHelper = TableHelper;
    window.Toast = Toast;

    try {
        TableHelper.applyDensityToAll();
    } catch (e) {}

})(window);
