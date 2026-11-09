/**
 * market-breadth.js
 * =============================================================================
 * Market Breadth: % of Stocks Trading Above Their EMA Engine.
 * Supports:
 *   - Universe selection (All NSE stocks, Nifty 500, Nifty 50)
 *   - Lookback duration (6M, 1Y, 2Y)
 *   - Paired charts (10/20, 50/100, 150/200) & Single EMA views (10, 20, 50, 100, 150, 200)
 *   - Exact visual reproduction of benchmark reference:
 *       Overbought (75), Median (50), Oversold (25), Left Y-axis, Dark Theme
 *   - High-DPI Canvas Rendering with interactive hover crosshairs & tooltips
 * =============================================================================
 */

(function (window) {
    'use strict';

    let _breadthData = null;
    let _currentUniverse = 'all';
    let _currentMonths = 12;
    let _currentViewMode = 'paired'; // 'paired' or 'single'
    let _activeSingleEMA = 50; // default single view is 50 EMA (matching screenshot)
    let _activeCharts = {};
    let _isLoading = false;

    // Palette
    const COLORS = {
        ema_10: '#8b5cf6',   // Purple
        ema_20: '#38bdf8',   // Sky blue
        ema_50: '#ec4899',   // Pink / Purple
        ema_100: '#f59e0b',  // Amber
        ema_150: '#06b6d4',  // Cyan
        ema_200: '#3b82f6',  // Blue
        overbought: '#f87171', // Salmon / Red (75)
        median: '#94a3b8',     // Slate Grey (50)
        oversold: '#4ade80',   // Green (25)
        bg: '#0c1220',
        grid: 'rgba(255, 255, 255, 0.05)',
        text: '#8492a6',
        textActive: '#f8fafc'
    };

    /**
     * Entry point called by hash router on #/market-breadth
     */
    async function loadMarketBreadthPage(forceRefresh = false) {
        const container = document.getElementById('marketBreadthSection');
        if (!container) return;

        if (_isLoading) return;
        _isLoading = true;
        updateLoadingState(true);

        try {
            const url = `/api/breadth/ema?universe=${encodeURIComponent(_currentUniverse)}&months=${_currentMonths}&refresh=${forceRefresh ? 'true' : 'false'}`;
            const res = await fetch(url, { credentials: 'same-origin' });
            if (!res.ok) {
                throw new Error(`Failed to load market breadth (${res.status})`);
            }
            _breadthData = await res.json();
            renderMarketBreadthUI(_breadthData);
        } catch (err) {
            console.error('Market breadth error:', err);
            const errBanner = document.getElementById('mbErrorBanner');
            if (errBanner) {
                errBanner.style.display = 'block';
                errBanner.textContent = `Unable to load market breadth data: ${err.message}. Please try again.`;
            }
        } finally {
            _isLoading = false;
            updateLoadingState(false);
        }
    }

    function updateLoadingState(loading) {
        const refreshBtn = document.getElementById('mbRefreshBtn');
        const refreshIcon = document.getElementById('mbRefreshIcon');
        if (refreshBtn) refreshBtn.disabled = loading;
        if (refreshIcon) {
            if (loading) refreshIcon.classList.add('fa-spin');
            else refreshIcon.classList.remove('fa-spin');
        }
    }

    /**
     * Render the full Market Breadth UI
     */
    function renderMarketBreadthUI(data) {
        const errBanner = document.getElementById('mbErrorBanner');
        if (errBanner) errBanner.style.display = 'none';

        // 1. Update As-of timestamps and Universe metadata
        const asOfBadge = document.getElementById('mbAsOfBadge');
        if (asOfBadge && data.metadata) {
            asOfBadge.textContent = `${data.metadata.as_of_time} · ${data.metadata.total_universe_stocks} Active Tickers`;
        }

        // 2. Render Metric KPI Cards
        renderKPICards(data.summary_cards || []);

        // 3. Render Charts based on active view mode
        renderChartsView(data);

        // 4. Render Recent Signals Log
        renderSignalsLog(data.recent_signals || []);
    }

    /**
     * Render KPI Metric Cards for all 6 EMAs
     */
    function renderKPICards(cards) {
        const container = document.getElementById('mbKpiContainer');
        if (!container) return;

        container.innerHTML = cards.map(c => {
            const isOb = c.current_pct >= 75;
            const isOs = c.current_pct <= 25;
            let statusPillClass = 'badge-neutral';
            let statusText = 'Neutral';

            if (isOb) {
                statusPillClass = 'badge-danger';
                statusText = 'Overbought (>75%)';
            } else if (isOs) {
                statusPillClass = 'badge-success';
                statusText = 'Oversold (<25%)';
            } else if (c.current_pct >= 50) {
                statusPillClass = 'badge-info';
                statusText = 'Above Median (>50%)';
            } else {
                statusPillClass = 'badge-warning';
                statusText = 'Below Median (<50%)';
            }

            const deltaColor = c.change_pct >= 0 ? 'var(--accent-success, #22c55e)' : 'var(--accent-danger, #ef4444)';
            const deltaSign = c.change_pct > 0 ? '+' : '';
            const deltaArrow = c.change_pct >= 0 ? '▲' : '▼';

            // Signal trigger badge
            let signalBadge = '';
            if (c.signal_event === 'BULLISH_CROSS_UP') {
                signalBadge = `<div class="mb-signal-pill pulse-green"><i class="fas fa-rocket"></i> Bullish Cross-Up Reversal!</div>`;
            } else if (c.signal_event === 'BEARISH_CROSS_DOWN') {
                signalBadge = `<div class="mb-signal-pill pulse-red"><i class="fas fa-exclamation-triangle"></i> Bearish Cross-Down Top!</div>`;
            }

            return `
                <div class="mb-kpi-card" onclick="selectSingleEMA(${c.period})">
                    <div class="mb-kpi-top">
                        <span class="mb-kpi-period">${c.period_label}</span>
                        <span class="mb-kpi-badge ${statusPillClass}">${statusText}</span>
                    </div>
                    <div class="mb-kpi-val">${c.current_pct.toFixed(2)}<span class="mb-kpi-pct">%</span></div>
                    <div class="mb-kpi-bottom">
                        <span style="color:${deltaColor}; font-weight:600;">
                            ${deltaArrow} ${deltaSign}${c.change_pct.toFixed(2)}%
                        </span>
                        <span class="mb-kpi-sub">vs prev day</span>
                    </div>
                    ${signalBadge}
                </div>
            `;
        }).join('');
    }

    /**
     * Switch view between Paired Charts and Single EMA Charts
     */
    function renderChartsView(data) {
        const pairedWrap = document.getElementById('mbPairedChartsContainer');
        const singleWrap = document.getElementById('mbSingleChartContainer');

        if (_currentViewMode === 'paired') {
            if (pairedWrap) pairedWrap.style.display = 'flex';
            if (singleWrap) singleWrap.style.display = 'none';
            renderPairedCharts(data);
        } else {
            if (pairedWrap) pairedWrap.style.display = 'none';
            if (singleWrap) singleWrap.style.display = 'block';
            renderSingleChart(data, _activeSingleEMA);
        }
    }

    /**
     * Render 3 Paired Charts: (10, 20), (50, 100), (150, 200)
     */
    function renderPairedCharts(data) {
        if (!data.pairs) return;

        data.pairs.forEach(pair => {
            const canvasId = `mbCanvas_${pair.id}`;
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;

            const seriesA = data.series[`ema_${pair.period_a}`] || [];
            const seriesB = data.series[`ema_${pair.period_b}`] || [];

            drawBreadthChart(canvas, {
                title: `${pair.chart_number}. ${pair.title}`,
                subtitle: data.metadata ? data.metadata.as_of_time : '',
                lines: [
                    { name: pair.label_a, color: pair.color_a, data: seriesA },
                    { name: pair.label_b, color: pair.color_b, data: seriesB }
                ],
                benchmarks: data.benchmarks || { overbought: 75, median: 50, oversold: 25 }
            });
        });
    }

    /**
     * Render Single EMA Chart matching screenshot pixel-perfect
     */
    function renderSingleChart(data, period) {
        const canvas = document.getElementById('mbCanvas_single');
        if (!canvas) return;

        const series = data.series[`ema_${period}`] || [];
        const color = COLORS[`ema_${period}`] || '#9d72e6';

        // Compute title matching reference image: "2. % Stocks above 50 EMA"
        const numMap = { 10: '1', 20: '1', 50: '2', 100: '2', 150: '3', 200: '3' };
        const num = numMap[period] || '1';
        const title = `${num}. % Stocks above ${period} EMA`;
        const subtitle = data.metadata ? data.metadata.as_of_time : '';

        drawBreadthChart(canvas, {
            title: title,
            subtitle: subtitle,
            lines: [
                { name: `% Stocks above ${period} EMA`, color: color, data: series }
            ],
            benchmarks: data.benchmarks || { overbought: 75, median: 50, oversold: 25 },
            singleMode: true
        });
    }

    /**
     * High-DPI Responsive Canvas Chart Renderer
     */
    function drawBreadthChart(canvas, options) {
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;

        // Auto resize canvas for high-DPI retina display
        const rect = canvas.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height || 360;

        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        ctx.scale(dpr, dpr);

        const padLeft = 55;
        const padRight = 20;
        const padTop = 30;
        const padBottom = 35;

        const plotWidth = width - padLeft - padRight;
        const plotHeight = height - padTop - padBottom;

        // Clear background
        ctx.fillStyle = COLORS.bg;
        ctx.fillRect(0, 0, width, height);

        // Y-axis range: 0 to 100 (matching 0.00, 10.00 ... 90.00 in reference image)
        const minY = 0.0;
        const maxY = 100.0;

        function getYPos(val) {
            return padTop + plotHeight - ((val - minY) / (maxY - minY)) * plotHeight;
        }

        // Draw horizontal grid lines & Y labels every 10%
        ctx.font = '11px -apple-system, BlinkMacSystemFont, "Inter", sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';

        for (let yVal = 0; yVal <= 90; yVal += 10) {
            const yPos = getYPos(yVal);

            // Grid line
            ctx.beginPath();
            ctx.strokeStyle = COLORS.grid;
            ctx.lineWidth = 1;
            ctx.moveTo(padLeft, yPos);
            ctx.lineTo(width - padRight, yPos);
            ctx.stroke();

            // Label
            ctx.fillStyle = COLORS.text;
            ctx.fillText(yVal.toFixed(2), padLeft - 10, yPos);
        }

        // ── Draw Benchmark Horizontal Lines ──
        const bm = options.benchmarks;

        // 1. Overbought line (75) - Salmon / Coral
        if (bm.overbought != null) {
            const y75 = getYPos(bm.overbought);
            ctx.beginPath();
            ctx.strokeStyle = COLORS.overbought;
            ctx.lineWidth = 1.6;
            ctx.moveTo(padLeft, y75);
            ctx.lineTo(width - padRight, y75);
            ctx.stroke();
        }

        // 2. Median line (50) - Slate / Grey
        if (bm.median != null) {
            const y50 = getYPos(bm.median);
            ctx.beginPath();
            ctx.strokeStyle = COLORS.median;
            ctx.lineWidth = 1.2;
            ctx.moveTo(padLeft, y50);
            ctx.lineTo(width - padRight, y50);
            ctx.stroke();
        }

        // 3. Oversold line (25) - Green
        if (bm.oversold != null) {
            const y25 = getYPos(bm.oversold);
            ctx.beginPath();
            ctx.strokeStyle = COLORS.oversold;
            ctx.lineWidth = 1.6;
            ctx.moveTo(padLeft, y25);
            ctx.lineTo(width - padRight, y25);
            ctx.stroke();
        }

        // Collect dates from the first series
        const firstLine = options.lines[0];
        if (!firstLine || !firstLine.data || firstLine.data.length === 0) {
            ctx.fillStyle = COLORS.text;
            ctx.textAlign = 'center';
            ctx.fillText('No data available for selected period', width / 2, height / 2);
            return;
        }

        const ptsCount = firstLine.data.length;
        function getXPos(idx) {
            if (ptsCount <= 1) return padLeft + plotWidth / 2;
            return padLeft + (idx / (ptsCount - 1)) * plotWidth;
        }

        // ── Draw X-axis Dates (e.g. Apr 2025, Jun 2025, Aug 2025) ──
        ctx.fillStyle = COLORS.text;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';

        // Sample ~6 to 9 labels across the timeline
        const step = Math.max(1, Math.floor(ptsCount / 8));
        for (let i = 0; i < ptsCount; i += step) {
            const pt = firstLine.data[i];
            if (!pt || !pt.time) continue;
            const xPos = getXPos(i);

            // Format YYYY-MM-DD -> "Apr 2025"
            const parts = pt.time.split('-');
            if (parts.length >= 2) {
                const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                const mIdx = parseInt(parts[1], 10) - 1;
                const dateLabel = `${monthNames[mIdx]} ${parts[0]}`;
                ctx.fillText(dateLabel, xPos, padTop + plotHeight + 10);
            }
        }

        // ── Draw Line Series ──
        options.lines.forEach(line => {
            const data = line.data;
            if (!data || data.length === 0) return;

            // Optional subtle gradient fill under curve for single mode
            if (options.singleMode) {
                const grad = ctx.createLinearGradient(0, padTop, 0, padTop + plotHeight);
                grad.addColorStop(0, hexToRgba(line.color, 0.22));
                grad.addColorStop(1, hexToRgba(line.color, 0.01));

                ctx.beginPath();
                let started = false;
                for (let i = 0; i < data.length; i++) {
                    const pt = data[i];
                    if (pt.value == null || isNaN(pt.value)) continue;
                    const x = getXPos(i);
                    const y = getYPos(pt.value);
                    if (!started) {
                        ctx.moveTo(x, padTop + plotHeight);
                        ctx.lineTo(x, y);
                        started = true;
                    } else {
                        ctx.lineTo(x, y);
                    }
                }
                if (started) {
                    ctx.lineTo(getXPos(data.length - 1), padTop + plotHeight);
                    ctx.closePath();
                    ctx.fillStyle = grad;
                    ctx.fill();
                }
            }

            // Draw crisp stroke
            ctx.beginPath();
            ctx.strokeStyle = line.color;
            ctx.lineWidth = 2.2;
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';

            let lineStarted = false;
            for (let i = 0; i < data.length; i++) {
                const pt = data[i];
                if (pt.value == null || isNaN(pt.value)) {
                    lineStarted = false;
                    continue;
                }
                const x = getXPos(i);
                const y = getYPos(pt.value);
                if (!lineStarted) {
                    ctx.moveTo(x, y);
                    lineStarted = true;
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.stroke();
        });

        // Attach Mouse Hover Crosshair & Tooltip
        setupChartInteraction(canvas, options, {
            padLeft, padRight, padTop, padBottom,
            plotWidth, plotHeight, getXPos, getYPos, ptsCount
        });
    }

    /**
     * Interactive crosshair and tooltip on hover
     */
    function setupChartInteraction(canvas, options, metrics) {
        // Remove existing listener if any
        if (canvas._hoverHandler) {
            canvas.removeEventListener('mousemove', canvas._hoverHandler);
            canvas.removeEventListener('mouseleave', canvas._leaveHandler);
        }

        const tooltipId = `${canvas.id}_tooltip`;
        let tooltip = document.getElementById(tooltipId);
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = tooltipId;
            tooltip.className = 'mb-chart-tooltip';
            canvas.parentElement.appendChild(tooltip);
        }

        const handleMouseMove = (e) => {
            const rect = canvas.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;

            if (mouseX < metrics.padLeft || mouseX > canvas.width / (window.devicePixelRatio || 1) - metrics.padRight) {
                tooltip.style.display = 'none';
                return;
            }

            // Find closest index
            const relX = mouseX - metrics.padLeft;
            const frac = Math.max(0, Math.min(1, relX / metrics.plotWidth));
            const idx = Math.round(frac * (metrics.ptsCount - 1));

            const firstLine = options.lines[0];
            if (!firstLine || !firstLine.data[idx]) return;

            const timeStr = firstLine.data[idx].time;

            let html = `<div class="mb-tt-date">${timeStr}</div>`;
            options.lines.forEach(l => {
                const pt = l.data[idx];
                const val = pt && pt.value != null ? pt.value.toFixed(2) + '%' : 'N/A';
                html += `
                    <div class="mb-tt-row">
                        <span class="mb-tt-dot" style="background:${l.color};"></span>
                        <span class="mb-tt-label">${l.name}:</span>
                        <strong class="mb-tt-val">${val}</strong>
                    </div>
                `;
            });

            // Benchmark context
            const valNum = firstLine.data[idx].value;
            if (valNum != null) {
                let regimeTxt = 'Neutral';
                let regimeColor = COLORS.median;
                if (valNum >= 75) {
                    regimeTxt = 'Overbought (>75%)';
                    regimeColor = COLORS.overbought;
                } else if (valNum <= 25) {
                    regimeTxt = 'Oversold (<25%)';
                    regimeColor = COLORS.oversold;
                }
                html += `<div class="mb-tt-regime" style="color:${regimeColor};">${regimeTxt}</div>`;
            }

            tooltip.innerHTML = html;
            tooltip.style.display = 'block';

            // Position tooltip
            const ttX = Math.min(rect.width - 160, Math.max(10, mouseX - 60));
            tooltip.style.left = `${ttX}px`;
            tooltip.style.top = `${Math.max(10, mouseY - 70)}px`;
        };

        const handleMouseLeave = () => {
            tooltip.style.display = 'none';
        };

        canvas.addEventListener('mousemove', handleMouseMove);
        canvas.addEventListener('mouseleave', handleMouseLeave);
        canvas._hoverHandler = handleMouseMove;
        canvas._leaveHandler = handleMouseLeave;
    }

    /**
     * Render Recent Signals Log Table
     */
    function renderSignalsLog(signals) {
        const tbody = document.getElementById('mbSignalsTableBody');
        if (!tbody) return;

        if (!signals || signals.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--text-muted);">No recent 25% cross-up or 75% cross-down threshold events in selected timeframe.</td></tr>`;
            return;
        }

        tbody.innerHTML = signals.map(s => {
            const isBullish = s.type === 'bullish';
            const badgeClass = isBullish ? 'badge-success' : 'badge-danger';
            const icon = isBullish ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down';
            return `
                <tr>
                    <td><strong>${s.date}</strong></td>
                    <td><span class="mb-period-tag">${s.period_label}</span></td>
                    <td><span class="badge ${badgeClass}"><i class="fas ${icon}"></i> ${s.event}</span></td>
                    <td><strong style="color:${isBullish ? 'var(--accent-success)' : 'var(--accent-danger)'}">${s.value.toFixed(2)}%</strong></td>
                    <td style="color:var(--text-muted); font-size:0.85rem;">${s.note}</td>
                </tr>
            `;
        }).join('');
    }

    // ─────────────────────────────────────────────────────────────────────────
    // User Actions & Event Handlers
    // ─────────────────────────────────────────────────────────────────────────

    function setUniverse(univ) {
        if (_currentUniverse === univ) return;
        _currentUniverse = univ;
        document.querySelectorAll('.mb-univ-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.univ === univ);
        });
        loadMarketBreadthPage(false);
    }

    function setLookback(months) {
        if (_currentMonths === months) return;
        _currentMonths = months;
        document.querySelectorAll('.mb-range-btn').forEach(b => {
            b.classList.toggle('active', parseInt(b.dataset.months) === months);
        });
        loadMarketBreadthPage(false);
    }

    function setViewMode(mode) {
        if (_currentViewMode === mode) return;
        _currentViewMode = mode;
        document.querySelectorAll('.mb-view-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.mode === mode);
        });
        if (_breadthData) renderChartsView(_breadthData);
    }

    function selectSingleEMA(period) {
        _activeSingleEMA = period;
        setViewMode('single');
        document.querySelectorAll('.mb-tab-ema').forEach(t => {
            t.classList.toggle('active', parseInt(t.dataset.ema) === period);
        });
        if (_breadthData) renderSingleChart(_breadthData, period);
    }

    function hexToRgba(hex, alpha) {
        const c = hex.replace('#', '');
        const r = parseInt(c.substring(0, 2), 16);
        const g = parseInt(c.substring(2, 4), 16);
        const b = parseInt(c.substring(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    // Window resize handler to redraw charts cleanly
    let _resizeTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => {
            const sec = document.getElementById('marketBreadthSection');
            if (sec && sec.style.display !== 'none' && _breadthData) {
                renderChartsView(_breadthData);
            }
        }, 150);
    });

    // Expose functions globally
    window.loadMarketBreadthPage = loadMarketBreadthPage;
    window.setMarketBreadthUniverse = setUniverse;
    window.setMarketBreadthLookback = setLookback;
    window.setMarketBreadthViewMode = setViewMode;
    window.selectSingleEMA = selectSingleEMA;
    window.refreshMarketBreadth = () => loadMarketBreadthPage(true);

})(window);
