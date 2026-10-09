/**
 * Shared lab result trend chart (Chart.js).
 *
 * - Points sit on a real time axis, so uneven gaps between draws look uneven.
 * - Straight lines between results; values between draws are not interpolated smoothly.
 * - Out-of-range points use a shape and an H/L label as well as color.
 * - The reference range is drawn per point (each result's own range, else the test's).
 * - The canvas gets a text summary for screen readers.
 * - Colors follow the theme, and drawn charts are recolored when body.dark-mode changes.
 *
 * LabChart.create(canvas, {
 *     name, unit,
 *     points: [{date, value, low, high, status, flag, fasting}]
 * }, {compact: false, title: true})
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

    // Sets every theme-dependent color; works on a chart config before creation and on a live chart
    function applyColors(chart, points) {
        const c = colors();
        const isOut = p => OUT.includes(p.status);
        chart.data.datasets.forEach((ds, i) => {
            if (i === 0) {
                ds.borderColor = ds.backgroundColor = c.line;
                ds.pointBackgroundColor = ds.pointBorderColor = points.map(p => isOut(p) ? c.out : c.line);
            } else {
                ds.borderColor = c.range;
                if (ds.fill) ds.backgroundColor = c.band;
            }
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

    // Draws "H" / "L" / "!" next to out-of-range points so status isn't conveyed by color alone
    const flagLabels = {
        id: 'labFlagLabels',
        afterDatasetsDraw(chart) {
            const meta = chart.getDatasetMeta(0);
            const points = chart.$labPoints || [];
            const { ctx } = chart;
            ctx.save();
            ctx.font = '600 11px "IBM Plex Sans", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = colors().out;
            meta.data.forEach((el, i) => {
                const p = points[i];
                if (!p || !OUT.includes(p.status)) return;
                const label = p.status === 'high' ? 'H' : p.status === 'low' ? 'L' : '!';
                const below = p.status === 'low';
                ctx.textBaseline = below ? 'top' : 'bottom';
                ctx.fillText(label, el.x, el.y + (below ? 9 : -9));
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

        const lows = points.map(p => ({ x: p.x, y: p.low }));
        const highs = points.map(p => ({ x: p.x, y: p.high }));
        const hasLow = lows.some(p => p.y !== null);
        const hasHigh = highs.some(p => p.y !== null);
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

        const rangeLine = (label, values) => ({
            label, data: values, borderWidth: opts.compact ? 1 : 1.5,
            borderDash: [5, 4], stepped: 'middle', spanGaps: true, fill: false, pointRadius: 0, pointHoverRadius: 0,
        });
        if (hasHigh) datasets.push(rangeLine('Upper reference', highs));
        if (hasLow) {
            const lower = rangeLine('Lower reference', lows);
            // Shade the normal range between the two lines when both are known
            if (hasHigh) lower.fill = '-1';
            datasets.push(lower);
        }

        const span = points[points.length - 1].x - points[0].x;
        const pad = span ? span * 0.04 : 15 * 86400000;
        const font = size => ({ family: 'IBM Plex Sans', size });

        const config = {
            type: 'line',
            data: { datasets },
            plugins: [flagLabels],
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
                                    lines.push(`Range: ${p.low !== null && p.high !== null ? `${p.low} - ${p.high}` : p.low !== null ? `≥ ${p.low}` : `≤ ${p.high}`}`);
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
                        min: points[0].x - pad,
                        max: points[points.length - 1].x + pad,
                        grid: { display: !opts.compact },
                        ticks: { font: font(opts.compact ? 10 : 12), maxTicksLimit: opts.compact ? 4 : 7, maxRotation: 0, callback: v => shortDate(v) },
                    },
                    y: {
                        grace: '8%',
                        grid: {},
                        title: { display: !opts.compact && !!data.unit, text: data.unit || '', font: font(12) },
                        ticks: { font: font(opts.compact ? 10 : 12), maxTicksLimit: opts.compact ? 4 : 8 },
                    }
                }
            }
        };
        applyColors(config, points);
        const chart = new Chart(canvas.getContext('2d'), config);
        chart.$labPoints = points;
        return chart;
    }

    if (document.body) watchTheme();
    else document.addEventListener('DOMContentLoaded', watchTheme);

    window.LabChart = { create, summarize };
})();
