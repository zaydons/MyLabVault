/**
 * Shared lab result trend chart (Chart.js).
 *
 * - Points sit on a real time axis, so uneven gaps between draws look uneven.
 * - Straight lines between results; values between draws are not interpolated smoothly.
 * - Out-of-range points are triangles (up for high, down for low) as well as a color; the newest
 *   one also gets an H/L label. Every point's status is in its tooltip.
 * - The current reference range (the newest result's, else the test's) is one shaded band. A point
 *   whose own range differed shows that range in its tooltip.
 * - The y-axis has four or five round steps from the data and the current range, and never goes
 *   below 0 for values that can't be negative.
 * - The canvas gets a text summary for screen readers.
 * - Colors follow the theme, and drawn charts are recolored when body.dark-mode changes.
 *
 * LabChart.create(canvas, {
 *     name, unit,
 *     points: [{date, value, low, high, status, flag, fasting}]
 * }, {compact: false, title: true, xMin, xMax})    // xMin/xMax (ms): a shared date span for charts side by side
 */
(function () {
    const OUT = ['high', 'low', 'abnormal'];
    const toNum = v => (v === null || v === undefined || v === '' || isNaN(parseFloat(v))) ? null : parseFloat(v);
    const isDark = () => document.body.classList.contains('dark-mode');
    const shortDate = ms => new Date(ms).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
    const longDate = ms => new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    // Date-only strings are read as local noon so they don't shift a day in negative UTC offsets
    const parseDate = d => new Date(/^\d{4}-\d{2}-\d{2}$/.test(d) ? `${d}T12:00:00` : d).getTime();
    const statusWord = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : 'No range';

    function colors() {
        return isDark()
            ? { line: '#8ec5ff', out: '#ff8a95', range: '#7fd18f', band: 'rgba(127, 209, 143, 0.12)', text: '#dee2e6', grid: 'rgba(255, 255, 255, 0.18)' }
            : { line: '#0062cc', out: '#c82333', range: '#1e7e34', band: 'rgba(30, 126, 52, 0.08)', text: '#495057', grid: 'rgba(0, 0, 0, 0.08)' };
    }

    // Round axis bounds: about four steps of 1, 2, 2.5 or 5 x a power of ten, covering the values with a
    // little room, and never below 0 when none of the values is negative
    function yBounds(values) {
        const nums = values.filter(v => v !== null && v !== undefined && !isNaN(v));
        if (!nums.length) return null;
        let lo = Math.min(...nums), hi = Math.max(...nums);
        const span = hi - lo || Math.abs(hi) || 1;
        lo -= span * 0.1;
        hi += span * 0.1;
        if (Math.min(...nums) >= 0) lo = Math.max(0, lo);
        const raw = (hi - lo) / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(st => st >= raw);
        const min = Math.floor(lo / step) * step;
        return { min: Math.min(...nums) >= 0 ? Math.max(0, min) : min, max: Math.ceil(hi / step) * step, step };
    }

    // The range that applies now: the newest result's own range, else the test's
    function currentRange(points) {
        for (let i = points.length - 1; i >= 0; i--) {
            if (points[i].low !== null || points[i].high !== null) return { low: points[i].low, high: points[i].high };
        }
        return { low: null, high: null };
    }

    const rangeText = (low, high) => low !== null && high !== null ? `${low} - ${high}` : low !== null ? `≥ ${low}` : `≤ ${high}`;

    // Sets every theme-dependent color; works on a chart config before creation and on a live chart
    function applyColors(chart, points) {
        const c = colors();
        const isOut = p => OUT.includes(p.status);
        chart.data.datasets.forEach(ds => {
            ds.borderColor = ds.backgroundColor = c.line;
            ds.pointBackgroundColor = ds.pointBorderColor = points.map(p => isOut(p) ? c.out : c.line);
        });
        const { plugins, scales } = chart.options;
        plugins.title.color = c.text;
        scales.y.title.color = c.text;
        ['x', 'y'].forEach(axis => {
            scales[axis].ticks.color = c.text;
            scales[axis].grid.color = c.grid;
        });
    }

    // Recolor charts already on the page when dark mode is switched without a reload
    function watchTheme() {
        let dark = isDark();
        new MutationObserver(() => {
            if (isDark() === dark) return;
            dark = isDark();
            Object.values(Chart.instances || {}).forEach(chart => {
                if (!chart.$labPoints) return;
                applyColors(chart, chart.$labPoints);
                chart.update('none');
            });
        }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    }

    // Draws "H" / "L" / "!" next to the newest point when it's out of range; earlier points carry their
    // status in their shape and tooltip, so the chart isn't crowded with labels
    const flagLabels = {
        id: 'labFlagLabels',
        afterDatasetsDraw(chart) {
            const meta = chart.getDatasetMeta(0);
            const points = chart.$labPoints || [];
            const i = points.length - 1, p = points[i], el = meta.data[i];
            if (!p || !el || !OUT.includes(p.status)) return;
            const { ctx } = chart;
            ctx.save();
            ctx.font = '700 12px "IBM Plex Sans", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = colors().out;
            const below = p.status === 'low';
            ctx.textBaseline = below ? 'top' : 'bottom';
            ctx.fillText(p.status === 'high' ? 'H' : p.status === 'low' ? 'L' : '!', el.x, el.y + (below ? 10 : -10));
            ctx.restore();
        }
    };

    // The current reference range as one shaded band with dashed edges, behind the line
    const rangeBand = {
        id: 'labRangeBand',
        beforeDatasetsDraw(chart) {
            const range = chart.$labRange;
            if (!range || (range.low === null && range.high === null)) return;
            const { ctx, chartArea: area, scales: { y } } = chart;
            const top = range.high !== null ? Math.max(area.top, y.getPixelForValue(range.high)) : area.top;
            const bottom = range.low !== null ? Math.min(area.bottom, y.getPixelForValue(range.low)) : area.bottom;
            const c = colors();
            ctx.save();
            ctx.fillStyle = c.band;
            if (bottom > top) ctx.fillRect(area.left, top, area.right - area.left, bottom - top);
            ctx.strokeStyle = c.range;
            ctx.lineWidth = 1.5;
            ctx.setLineDash([5, 4]);
            [range.high, range.low].forEach(v => {
                if (v === null || v <= y.min || v >= y.max) return;
                const py = Math.round(y.getPixelForValue(v)) + 0.5;
                ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
            });
            ctx.restore();
        }
    };

    function summarize(name, unit, points) {
        if (!points.length) return `${name}: no numeric results to chart.`;
        const last = points[points.length - 1];
        const out = points.filter(p => OUT.includes(p.status)).length;
        return `${name} trend: ${points.length} result${points.length === 1 ? '' : 's'} from ` +
            `${longDate(points[0].x)} to ${longDate(last.x)}. Latest ${last.y}${unit ? ' ' + unit : ''} ` +
            `on ${longDate(last.x)}, ${statusWord(last.status).toLowerCase()}. ` +
            `${out} of ${points.length} outside the reference range.`;
    }

    function create(canvas, data, options) {
        if (!canvas || !window.Chart) return null;
        const opts = Object.assign({ compact: false, title: true }, options || {});
        const points = (data.points || [])
            .map(p => ({ ...p, x: parseDate(p.date), y: toNum(p.value), low: toNum(p.low), high: toNum(p.high) }))
            .filter(p => p.y !== null && !isNaN(p.x))
            .sort((a, b) => a.x - b.x);

        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', summarize(data.name, data.unit, points));
        if (!points.length) return null;

        const range = currentRange(points);
        const bounds = yBounds([...points.map(p => p.y), range.low, range.high]);
        const pointStyle = p => p.status === 'high' || p.status === 'low' ? 'triangle' : p.status === 'abnormal' ? 'rectRot' : 'circle';
        const radius = opts.compact ? 3 : 5;

        const datasets = [{
            label: data.name + (data.unit ? ` (${data.unit})` : ''),
            data: points.map(p => ({ x: p.x, y: p.y })),
            borderWidth: 2,
            tension: 0,
            fill: false,
            pointStyle: points.map(pointStyle),
            rotation: points.map(p => p.status === 'low' ? 180 : 0),
            pointRadius: points.map(p => OUT.includes(p.status) ? radius + 2 : radius),
            pointHoverRadius: radius + 3,
        }];

        const xMin = opts.xMin ?? points[0].x, xMax = opts.xMax ?? points[points.length - 1].x;
        const span = xMax - xMin;
        const pad = span ? span * 0.04 : 15 * 86400000;
        const font = size => ({ family: 'IBM Plex Sans', size });

        const config = {
            type: 'line',
            data: { datasets },
            plugins: [rangeBand, flagLabels],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 300 },
                layout: { padding: { top: 14, bottom: 4 } },
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    title: { display: opts.title && !opts.compact, text: `${data.name} over time`, font: { ...font(15), weight: '600' } },
                    legend: { display: false },
                    tooltip: {
                        filter: item => item.datasetIndex === 0,
                        titleFont: font(12), bodyFont: font(12),
                        callbacks: {
                            title: items => longDate(items[0].parsed.x),
                            label: item => `${item.parsed.y}${data.unit ? ' ' + data.unit : ''}`,
                            afterLabel: item => {
                                const p = points[item.dataIndex];
                                const lines = [`Status: ${statusWord(p.status)}`];
                                if (p.low !== null || p.high !== null) {
                                    const differs = p.low !== range.low || p.high !== range.high;
                                    lines.push(`${differs ? 'Range then' : 'Range'}: ${rangeText(p.low, p.high)}`);
                                }
                                if (p.flag) lines.push(`Lab flag: ${p.flag}`);
                                if (p.fasting === true) lines.push('Fasting');
                                return lines;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'linear',
                        min: xMin - pad,
                        max: xMax + pad,
                        grid: { display: !opts.compact },
                        ticks: { font: font(opts.compact ? 10 : 12), maxTicksLimit: opts.compact ? 4 : 7, maxRotation: 0, callback: v => shortDate(v) },
                    },
                    y: {
                        min: bounds.min,
                        max: bounds.max,
                        grid: {},
                        title: { display: !opts.compact && !!data.unit, text: data.unit || '', font: font(12) },
                        ticks: { font: font(opts.compact ? 10 : 12), stepSize: bounds.step },
                    }
                }
            }
        };
        applyColors(config, points);
        const chart = new Chart(canvas.getContext('2d'), config);
        chart.$labPoints = points;
        chart.$labRange = range;
        chart.update('none');  // draw the band now that the chart knows its range
        return chart;
    }

    if (document.body) watchTheme();
    else document.addEventListener('DOMContentLoaded', watchTheme);

    window.LabChart = { create, summarize, yBounds, currentRange, parseDate };
})();
