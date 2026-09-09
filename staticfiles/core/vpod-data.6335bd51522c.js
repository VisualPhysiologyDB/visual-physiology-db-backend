/* Shared pure data functions, usable in browser and Node tests. */
(function(root) {
    const ACUITY_BOUNDS = [0, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, Infinity];
    function referenceHistogram(rows) {
        const years = rows.map(r => r.year_of_publication ?? (r.publication_date ? Number(r.publication_date.slice(0, 4)) : null))
            .filter(y => Number.isInteger(y) && y > 0 && y <= 9999);
        const labels = years.length ? Array.from({length: Math.max(...years) - Math.min(...years) + 1}, (_, i) => Math.min(...years) + i) : [];
        const counts = new Map(); years.forEach(y => counts.set(y, (counts.get(y) || 0) + 1));
        return {labels, counts: labels.map(y => counts.get(y) || 0), plotted: years.length, excluded: rows.length - years.length};
    }
    function acuityHistogram(rows) {
        const labels = ACUITY_BOUNDS.slice(0, -1).map((v, i) => i === ACUITY_BOUNDS.length - 2 ? `≥${v}` : `${v}–<${ACUITY_BOUNDS[i + 1]}`);
        const counts = labels.map(() => 0);
        let plotted = 0;
        for (const row of rows) {
            if (!Number.isFinite(row.cpd) || row.cpd <= 0) continue;
            const index = ACUITY_BOUNDS.findIndex((v, i) => i < counts.length && row.cpd >= v && row.cpd < ACUITY_BOUNDS[i + 1]);
            if (index >= 0) {counts[index]++; plotted++;}
        }
        return {labels, counts, plotted, excluded: rows.length - plotted};
    }
    function trimEmptyBins(histogram) {
        const first = histogram.counts.findIndex(count => count > 0);
        if (first < 0) return {...histogram, labels: [], counts: []};
        let last = histogram.counts.length - 1;
        while (histogram.counts[last] === 0) last--;
        return {...histogram, labels: histogram.labels.slice(first, last + 1), counts: histogram.counts.slice(first, last + 1)};
    }
    function safeLink(value) {
        if (!value || /[\s\u0000-\u001f]/.test(value)) return null;
        try {
            const u = new URL(value);
            return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null;
        } catch (_) {return null;}
    }
    function flatten(value, prefix = '', result = {}) {
        for (const [key, item] of Object.entries(value)) {
            const name = prefix ? `${prefix}.${key}` : key;
            if (item && typeof item === 'object' && !Array.isArray(item) && key !== 'source_data') flatten(item, name, result);
            else result[name] = item && typeof item === 'object' ? JSON.stringify(item) : item;
        }
        return result;
    }
    function csvCell(value) {
        let text = value === null || value === undefined ? '' : String(value);
        // Spreadsheet safety; full original strings remain available through JSON API.
        if (typeof value === 'string' && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
        return '"' + text.replace(/"/g, '""') + '"';
    }
    function exportCSV(rows) {
        if (!rows.length) return '';
        const flat = rows.map(r => flatten(r));
        const headers = [...new Set(flat.flatMap(r => Object.keys(r)))];
        return [headers.map(csvCell).join(','), ...flat.map(r => headers.map(h => csvCell(r[h])).join(','))].join('\r\n');
    }
    const api = {ACUITY_BOUNDS, referenceHistogram, acuityHistogram, trimEmptyBins, safeLink, exportCSV};
    if (typeof module !== 'undefined') module.exports = api;
    root.VpodData = api;
})(typeof window !== 'undefined' ? window : globalThis);
