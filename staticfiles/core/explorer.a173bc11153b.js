/* Active VPOD explorer: plain DOM rendering, shared filters, full-population exports. */
'use strict';
const $ = id => document.getElementById(id);
let currentTab = 'opsins';
let allData = [];
let filteredData = [];
let chart = null;
let activeFetch = null;
let requestNumber = 0;
const tabs = ['opsins', 'heterologous', 'scp', 'visual-acuity', 'references'];
const acuityInputs = {cpd: 'subCpd', eye_type: 'subEyeType', body_length_cm: 'subBodyLength', interommatidial_angle_deg: 'subInterommatidial', acceptance_angle_deg: 'subAcceptance', lens_diameter_mm: 'subLensDiameter'};
const measurementLabels = {body_length_cm: 'BL: body length (cm)', interommatidial_angle_deg: 'Δϕ: interommatidial angle (degrees)', acceptance_angle_deg: 'Δρ: acceptance angle (degrees)', lens_diameter_mm: 'Lens diameter (mm)', feller_ref_id: 'FellerRefID', source_dataset: 'Source dataset', source_record_id: 'Source record ID', notes: 'Notes', quality_flags: 'Quality flags', source_data: 'Original source columns'};

function element(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text === null || text === '' ? 'Unknown' : String(text);
    if (cls) el.className = cls;
    return el;
}
function longValue(value, unknown = 'Unknown') {
    if (!value) return element('span', unknown, 'text-slate-500');
    const details = element('details');
    const summary = element('summary', value, 'ellipsis');
    summary.title = value;
    summary.setAttribute('aria-label', `${value}. Activate to show full text.`);
    details.append(summary, element('p', value, 'full-value'));
    return details;
}
function sourceLink(reference) {
    if (!reference) return element('span', 'Reference unavailable', 'text-slate-500');
    const wrap = element('div');
    const doi = reference.doi;
    const candidate = reference.link || reference.source_url || (doi && /^10\.\d{4,9}\/\S+$/i.test(doi) ? `https://doi.org/${encodeURI(doi)}` : doi);
    const url = VpodData.safeLink(candidate);
    const label = doi || reference.source_url || reference.raw_citation || 'Source unknown';
    wrap.append(element('div', `Ref #${reference.refid}`, 'text-xs text-slate-500'));
    if (url) {
        const a = element('a', undefined, 'source-link text-sky-600 hover:text-sky-800 hover:underline inline-flex items-center gap-1');
        a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.title = label;
        a.append(element('span', label, 'ellipsis'));
        const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        for (const [name, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'h-3 w-3 shrink-0'})) icon.setAttribute(name, value);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'M15 3h6v6 M10 14 21 3 M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4');
        icon.append(path); a.append(icon);
        wrap.append(a);
    } else wrap.append(longValue(label));
    return wrap;
}
function detailList(row, labels) {
    const details = element('details'); details.append(element('summary', 'Details', 'text-sky-700'));
    const dl = element('dl', undefined, 'full-value text-sm');
    Object.entries(labels).forEach(([key, label]) => {
        const value = row[key];
        dl.append(element('dt', label, 'font-semibold mt-2'));
        dl.append(element('dd', value && typeof value === 'object' ? JSON.stringify(value, null, 2) : value ?? 'Unknown'));
    });
    details.append(dl); return details;
}
function methodText(methods) {
    if (!methods?.length) return 'Unknown';
    return methods.map(m => `${m.name}${m.uncertain ? ' (uncertain)' : ''}${m.kind !== 'experimental' ? ` [${m.kind.replaceAll('_', ' ')}]` : ''}`).join('; ');
}
function organism(row, subtitle = row.phylum) {
    const name = [row.genus, row.species].filter(Boolean).join(' ');
    const wrap = element('div', undefined, 'organism-cell');
    wrap.append(element('div', name || row.source_data?.Species || 'Unknown taxonomy', 'organism-name text-sm font-medium text-slate-900 italic'));
    if (subtitle) wrap.append(element('div', subtitle, 'text-xs text-slate-500 mt-0.5'));
    return wrap;
}
function recordId(id) {return element('span', `#${id}`, 'record-id text-slate-500 whitespace-nowrap');}
function accession(value) {
    const detail = longValue(value);
    detail.classList.add('accession-value', 'font-mono', 'text-slate-600');
    return detail;
}
function geneFamily(value) {return element('span', value || 'Unknown', 'gene-family-badge px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-slate-100 text-slate-700');}
function evidenceId(row) {
    const wrap = element('div', undefined, 'space-y-1');
    wrap.append(element('div', `#${row.hetid}`, 'text-slate-500'));
    const badge = element('span', row.is_inferred ? 'Inferred' : 'Experimental', 'evidence-badge px-2 py-1 text-[10px] uppercase tracking-wide font-bold rounded inline-flex');
    badge.classList.add(...(row.is_inferred ? ['bg-purple-100', 'text-purple-700'] : ['bg-sky-100', 'text-sky-700']));
    badge.title = row.is_inferred ? `Computationally inferred${row.inference_source ? `: ${row.inference_source}` : ''}` : 'Experimental measurement';
    wrap.append(badge);
    return wrap;
}
function spectralValue(row) {
    if (!Number.isFinite(row.lambda_max) || row.lambda_max <= 0) return element('span', 'Unknown', 'text-slate-400');
    const value = row.lambda_max;
    const color = value < 400 ? 'text-purple-600' : value < 490 ? 'text-blue-600' : value < 550 ? 'text-green-600' : value < 590 ? 'text-yellow-600' : 'text-red-600';
    const wrap = element('span', undefined, `spectral-value text-sm font-bold whitespace-nowrap ${color}`);
    wrap.append(document.createTextNode(String(value) + ' '), element('span', 'nm', 'text-slate-400 font-normal text-xs'));
    if (Number.isFinite(row.error)) wrap.append(element('span', ` ±${row.error}`, 'text-slate-400 font-normal text-xs ml-1'));
    return wrap;
}
// Original explorer's illustrative spectral palette, restricted to wavelength plots.
function wavelengthToColor(w) {
    let r, g, b, factor;
    if (w >= 380 && w < 440) {r = -(w - 440) / 60; g = 0; b = 1;}
    else if (w >= 440 && w < 490) {r = 0; g = (w - 440) / 50; b = 1;}
    else if (w >= 490 && w < 510) {r = 0; g = 1; b = -(w - 510) / 20;}
    else if (w >= 510 && w < 580) {r = (w - 510) / 70; g = 1; b = 0;}
    else if (w >= 580 && w < 645) {r = 1; g = -(w - 645) / 65; b = 0;}
    else if (w >= 645 && w <= 780) {r = 1; g = 0; b = 0;}
    else return w <= 380 ? 'rgba(128,0,128,0.8)' : 'rgba(100,0,0,0.8)';
    if (w >= 380 && w < 420) factor = 0.3 + 0.7 * (w - 380) / 40;
    else if (w >= 420 && w <= 700) factor = 1;
    else if (w > 700 && w <= 780) factor = 0.3 + 0.7 * (780 - w) / 80;
    else factor = 0.3;
    return `rgba(${Math.round(r * factor * 255)}, ${Math.round(g * factor * 255)}, ${Math.round(b * factor * 255)}, 0.85)`;
}
function renderTable() {
    $('tableHeader').replaceChildren(); $('tableBody').replaceChildren();
    $('emptyState').classList.toggle('hidden', filteredData.length !== 0);
    document.querySelector('#emptyState h3').textContent = 'No records found';
    let headers;
    if (currentTab === 'references') headers = ['Reference / source', 'Publication title', 'Publication date', 'Measurement method(s)', 'Details'];
    else if (currentTab === 'visual-acuity') headers = ['Acuity ID', 'Organism', 'Eye type', 'Acuity (cycles/degree)', 'Reference', 'Measurements / provenance'];
    else if (currentTab === 'opsins') headers = ['Opsin ID', 'Organism', 'Gene family', 'Accession', 'Reference'];
    else if (currentTab === 'heterologous') headers = ['Het ID / evidence', 'Organism', 'Accession', 'λmax (nm)', 'Mutations', 'Reference'];
    else headers = ['SCP ID', 'Organism', 'Receptor type', 'Chromophore', 'λmax (nm)', 'Reference'];
    const tr = element('tr');
    headers.forEach(h => {const th = element('th', h, 'px-4 sm:px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider'); th.scope = 'col'; tr.append(th);});
    $('tableHeader').append(tr);
    const fragment = document.createDocumentFragment();
    for (const row of filteredData) {
        let cells;
        if (currentTab === 'references') {
            cells = [sourceLink(row), longValue(row.title, 'Unknown title'), row.publication_date || row.year_of_publication || 'Unknown date', longValue(methodText(row.measurement_methods)), detailList(row, {online_date: 'Online date', print_date: 'Print date', year_of_publication: 'Histogram year (legacy year retained)', mom_raw: 'Original MOM', raw_citation: 'Original citation / identifier', notes: 'Notes'})];
            if (row.status === 'APPROVED') cells[0].append(element('span', 'Approved', 'approval-badge mt-1 px-2 py-1 text-[10px] uppercase font-bold rounded bg-emerald-100 text-emerald-700 inline-flex'));
        } else if (currentTab === 'visual-acuity') {
            cells = [recordId(row.acuid), organism(row), row.eye_type || 'Unknown', element('span', row.cpd ?? 'Unknown', 'font-semibold text-slate-900'), sourceLink(row.reference), detailList(row, measurementLabels)];
        } else if (currentTab === 'opsins') {
            cells = [recordId(row.opsinid), organism(row), geneFamily(row.gene_family), accession(row.accession), sourceLink(row.reference)];
        } else if (currentTab === 'heterologous') {
            cells = [evidenceId(row), organism(row.opsin || {}, row.opsin?.gene_family), accession(row.opsin?.accession), spectralValue(row), longValue(row.mutations || 'None'), sourceLink(row.reference)];
        } else {
            cells = [recordId(row.scpid), organism(row), element('span', [row.photoreceptor_type, row.cell_subtype].filter(Boolean).join(' · ') || 'Unknown', 'font-semibold'), row.chromophore || 'Unknown', spectralValue(row), sourceLink(row.reference)];
        }
        const tr = element('tr', undefined, 'hover:bg-sky-50 transition-colors');
        cells.forEach(value => {const td = element('td', undefined, 'px-4 sm:px-6 py-4 text-sm text-slate-600'); td.append(value instanceof Node ? value : document.createTextNode(String(value))); tr.append(td);});
        fragment.append(tr);
    }
    $('tableBody').append(fragment);
}
function inputValue(id) {return $(id).value.trim();}
function numericValue(id) {const value = inputValue(id); return value === '' ? null : Number(value);}
function getProp(item, key) {return item[key] ?? item.opsin?.[key] ?? '';}
function applyFilters() {
    const query = inputValue('searchInput').toLowerCase();
    const textFilters = {filterPhylum: 'phylum', filterFamily: 'gene_family', filterGenus: 'genus', filterSpecies: 'species', filterAccession: 'accession'};
    filteredData = allData.filter(row => {
        if (query && !JSON.stringify(row).toLowerCase().includes(query)) return false;
        if (currentTab !== 'references') for (const [id, field] of Object.entries(textFilters)) {
            if ($(id).disabled) continue;
            const term = inputValue(id).toLowerCase();
            const value = String(getProp(row, field) || (field === 'species' ? row.source_data?.Species || '' : '')).toLowerCase();
            if (term && !value.includes(term)) return false;
        }
        const doi = row.reference?.doi || row.doi || row.source_url || '';
        if (inputValue('filterDoi') && !doi.toLowerCase().includes(inputValue('filterDoi').toLowerCase())) return false;
        if (currentTab === 'visual-acuity') {
            if (inputValue('filterEyeType') && !(row.eye_type || '').toLowerCase().includes(inputValue('filterEyeType').toLowerCase())) return false;
            const min = numericValue('filterCpdMin'), max = numericValue('filterCpdMax');
            if ((min !== null || max !== null) && !Number.isFinite(row.cpd)) return false;
            if (min !== null && row.cpd < min || max !== null && row.cpd > max) return false;
            if ($('filterCpdMissing').value === 'known' && row.cpd === null || $('filterCpdMissing').value === 'missing' && row.cpd !== null) return false;
        }
        if (['heterologous', 'scp'].includes(currentTab)) {
            const min = numericValue('filterLmaxMin'), max = numericValue('filterLmaxMax');
            if ((min !== null || max !== null) && !Number.isFinite(row.lambda_max)) return false;
            if (min !== null && row.lambda_max < min || max !== null && row.lambda_max > max) return false;
            if ($('filterWildtype').checked && currentTab === 'heterologous' && !['', 'none', 'wt', 'wildtype'].includes((row.mutations || '').toLowerCase().trim())) return false;
        }
        return true;
    });
    $('recordCount').textContent = `${filteredData.length} Records Found`;
    renderTable(); updateChart();
}
function updateChart() {
    if (chart) {chart.destroy(); chart = null;}
    let histogram, axis, title, explanation;
    if (currentTab === 'references') {
        histogram = VpodData.referenceHistogram(filteredData); axis = 'Publication year'; title = 'Publication years';
        explanation = 'Counts reference records in this filtered table, including duplicate publications; each record counts once. Existing curated years take precedence; otherwise the selected publication date supplies the year. Unknown years are excluded.';
    } else if (currentTab === 'visual-acuity') {
        histogram = VpodData.acuityHistogram(filteredData); axis = 'Acuity (cycles per degree; variable-width intervals)'; title = 'Visual acuity distribution';
        explanation = 'Counts observations, not species. Variable-width categorical bins show counts, not density: lower boundary included, upper excluded; zero excluded. Last bin is ≥200 CPD. Empty bins are retained. CPD is supplied data; methods and conditions may differ.';
    } else if (['scp', 'heterologous'].includes(currentTab)) {
        const valid = filteredData.filter(r => Number.isFinite(r.lambda_max) && r.lambda_max > 0);
        const labels = Array.from({length: 51}, (_, i) => i * 10 + 300);
        histogram = {labels, counts: labels.map(v => valid.filter(r => Math.round(r.lambda_max / 10) * 10 === v).length), plotted: valid.length, excluded: filteredData.length - valid.length};
        axis = 'Wavelength (nm; rounded to nearest 10 nm)'; title = 'Spectral sensitivity distribution'; explanation = 'Counts filtered assay records; missing values and legacy zero sentinels are excluded.';
    }
    const show = Boolean(histogram);
    $('plotContainer').classList.toggle('hidden', !show); $('plotValues').classList.toggle('hidden', !show);
    $('plotExplanation').textContent = explanation || '';
    document.querySelector('.spectrum-bg').classList.toggle('hidden', ['references', 'visual-acuity'].includes(currentTab));
    if (!show) return;
    $('plotTitle').textContent = title;
    $('plotCount').textContent = `${histogram.plotted} plotted · ${histogram.excluded} excluded`;
    $('plotValuesText').textContent = histogram.labels.map((v, i) => `${v}: ${histogram.counts[i]}`).join('; ') || 'No known values';
    $('lmaxChart').setAttribute('aria-label', `${title}. ${$('plotCount').textContent}. Counts available below.`);
    if (typeof Chart === 'undefined') { $('plotExplanation').textContent += ' Chart library unavailable; use the counts below.'; return; }
    const colors = ['scp', 'heterologous'].includes(currentTab) ? histogram.labels.map(wavelengthToColor) : '#0284c7';
    chart = new Chart($('lmaxChart'), {type: 'bar', data: {labels: histogram.labels, datasets: [{label: 'Record count', data: histogram.counts, backgroundColor: colors, borderRadius: 4}]}, options: {animation: false, responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {title: {display: true, text: axis}}, y: {beginAtZero: true, ticks: {precision: 0}, title: {display: true, text: 'Record count'}}}}});
}
async function fetchData(endpoint) {
    const serial = ++requestNumber;
    if (activeFetch) activeFetch.abort();
    activeFetch = new AbortController();
    allData = []; filteredData = []; renderTable(); updateChart();
    showLoading(true); $('recordCount').textContent = 'Loading…';
    try {
        const rows = []; let next = `/api/${endpoint}/`; const seen = new Set();
        while (next) {
            const url = new URL(next, location.origin);
            if (url.origin !== location.origin || !url.pathname.startsWith('/api/') || seen.has(url.href)) throw new Error('Invalid pagination link');
            seen.add(url.href);
            const response = await fetch(url.href, {signal: activeFetch.signal});
            if (!response.ok) throw new Error(`API returned ${response.status}`);
            const body = await response.json();
            if (Array.isArray(body)) {rows.push(...body); next = null;}
            else if (Array.isArray(body.results)) {rows.push(...body.results); next = body.next;}
            else throw new Error('Unexpected API response');
        }
        if (serial !== requestNumber) return;
        allData = rows; applyFilters();
    } catch (error) {
        if (serial !== requestNumber || error.name === 'AbortError') return;
        allData = []; filteredData = []; renderTable(); updateChart();
        $('recordCount').textContent = 'Unable to load records';
        document.querySelector('#emptyState h3').textContent = 'Connection error. Select the tab again to retry.';
    } finally {if (serial === requestNumber) showLoading(false);}
}
function showLoading(show) { $('loadingOverlay').classList.toggle('hidden', !show); $('exportDataButton').disabled = show; $('copyDataButton').disabled = show; }
function switchTab(tab) {
    currentTab = tab;
    for (const t of tabs) { $('tab-' + t).classList.toggle('tab-active', t === tab); $('tab-' + t).classList.toggle('tab-inactive', t !== tab); }
    $('acuityFilters').classList.toggle('hidden', tab !== 'visual-acuity');
    $('lmaxFilters').classList.toggle('hidden', !['heterologous', 'scp'].includes(tab));
    for (const id of ['filterPhylum', 'filterFamily', 'filterGenus', 'filterSpecies', 'filterAccession']) {
        $(id).disabled = tab === 'references' || tab === 'visual-acuity' && ['filterPhylum', 'filterFamily', 'filterAccession'].includes(id);
        $(id).parentElement.classList.toggle('hidden', $(id).disabled);
    }
    fetchData(tab);
}
function toggleFilters() {
    $('advancedFilters').classList.toggle('hidden');
    $('filterToggleText').textContent = $('advancedFilters').classList.contains('hidden') ? 'Show Advanced Filters' : 'Hide Advanced Filters';
}
function formatDataForExport() {return VpodData.exportCSV(filteredData);}
function downloadCSV() {
    const data = formatDataForExport(); if (!data) return alert('No data to export.');
    const url = URL.createObjectURL(new Blob([data], {type: 'text/csv;charset=utf-8'}));
    const link = element('a'); link.href = url; link.download = `VPOD_${currentTab}_export.csv`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    const input = element('textarea'); input.value = text; document.body.append(input); input.select();
    const copied = document.execCommand('copy'); input.remove(); if (!copied) throw new Error('Clipboard unavailable');
}
async function copyTableData() {try {if (!filteredData.length) return alert('No data to copy.'); await copyText(formatDataForExport()); alert('CSV data copied.');} catch (_) {alert('Clipboard unavailable; use Export CSV.');}}
function toggleCitationModal() {$('citationModal').classList.toggle('hidden');}
async function copyCitation() {
    try {
        await copyText($('vpodCitationText').textContent.trim());
        const button = $('copyCitationButton');
        const original = [...button.childNodes].map(node => node.cloneNode(true));
        button.replaceChildren(element('span', '✓', 'h-4 w-4'), document.createTextNode('Copied'));
        setTimeout(() => button.replaceChildren(...original), 1800);
    } catch (_) {alert('Unable to copy citation.');}
}
function toggleContributeModal() {
    $('contributeModal').classList.toggle('hidden');
    if (!$('contributeModal').classList.contains('hidden')) { $('submissionForm').reset(); $('doiWarning').classList.add('hidden'); setSubTier('PUBLICATION'); $('subDoi').focus(); }
}
function setSubTier(tier) {
    $('subTier').value = tier; const detailed = tier === 'DATA';
    $('publicationFields').classList.toggle('hidden', detailed); $('dataEntryFields').classList.toggle('hidden', !detailed); $('btnSubmitAnother').classList.toggle('hidden', !detailed);
    const selected = 'w-1/2 py-2 text-sm font-semibold border-b-2 border-sky-500 text-sky-600';
    const inactive = 'w-1/2 py-2 text-sm font-medium text-slate-500 hover:text-slate-700 transition-colors';
    $('tier-pub').className = detailed ? inactive : selected;
    $('tier-data').className = detailed ? selected : inactive;
    $('subGenus').required = detailed; $('subSpecies').required = detailed;
    setDetailedDataType();
}
function setDetailedDataType() {
    const type = $('subType').value, detailed = $('subTier').value === 'DATA';
    $('heterologousFields').classList.toggle('hidden', type !== 'Heterologous'); $('scpFields').classList.toggle('hidden', type !== 'SCP');
    $('acuityFields').classList.toggle('hidden', type !== 'Visual Acuity'); $('spectralFields').classList.toggle('hidden', type === 'Visual Acuity');
    $('subLmax').required = detailed && type !== 'Visual Acuity'; $('subCpd').required = detailed && type === 'Visual Acuity';
    for (const selector of ['#dataEntryFields input', '#dataEntryFields textarea']) document.querySelectorAll(selector).forEach(input => { input.disabled = !detailed || Boolean(input.closest('#heterologousFields.hidden, #scpFields.hidden, #acuityFields.hidden, #spectralFields.hidden')); input.setCustomValidity(''); });
}
function validIdentifier(value) {return /^10\.\d{4,9}\/[^\s"<>]+$/i.test(value.replace(/^doi:\s*/i, '')) || Boolean(VpodData.safeLink(value));}
async function submitData(event, addAnother = false) {
    event.preventDefault();
    if (!$('submissionForm').reportValidity()) return;
    const payload = {submission_type: $('subTier').value, doi: inputValue('subDoi'), year_of_publication: numericValue('subYear'), notes: inputValue('subNotes'), submitter_email: inputValue('subEmail')};
    if (!validIdentifier(payload.doi)) return alert('Supply a DOI or an http(s) source URL.');
    if (payload.submission_type === 'PUBLICATION') payload.relevance = $('subRelevance').value;
    else {
        payload.data_type = $('subType').value;
        for (const [field, id] of Object.entries({genus: 'subGenus', species: 'subSpecies', phylum: 'subPhylum'})) payload[field] = inputValue(id);
        if (payload.data_type === 'Visual Acuity') {
            for (const [field, id] of Object.entries(acuityInputs)) {
                const value = field === 'eye_type' ? inputValue(id) : numericValue(id);
                if (field !== 'eye_type' && value !== null && (!Number.isFinite(value) || value <= 0)) return alert('Acuity measurements must be finite and greater than zero.');
                payload[field] = value;
            }
        } else {
            payload.lambda_max = numericValue('subLmax'); payload.error = numericValue('subError');
            if (!Number.isFinite(payload.lambda_max) || payload.lambda_max !== 0 && (payload.lambda_max < 300 || payload.lambda_max > 800)) return alert('Use 300–800 nm, or the legacy 0 sentinel.');
            if (payload.error !== null && (!Number.isFinite(payload.error) || payload.error < 0)) return alert('Error must be finite and nonnegative.');
            const fields = payload.data_type === 'Heterologous' ? {gene_family: 'subGeneFamily', accession: 'subAccession', mutations: 'subMutations', cell_culture: 'subCellCulture', dna_sequence: 'subDna', protein_sequence: 'subProtein'} : {photoreceptor_type: 'subPhotoreceptorType', cell_subtype: 'subCellSubtype', chromophore: 'subChromophore'};
            for (const [key, id] of Object.entries(fields)) payload[key] = inputValue(id);
        }
    }
    const buttons = [...$('submissionForm').querySelectorAll('button')]; buttons.forEach(b => b.disabled = true);
    try {
        const csrf = document.cookie.split('; ').find(v => v.startsWith('csrftoken='))?.split('=').slice(1).join('=');
        const headers = {'Content-Type': 'application/json'}; if (csrf) headers['X-CSRFToken'] = decodeURIComponent(csrf);
        const response = await fetch('/api/submissions/', {method: 'POST', headers, body: JSON.stringify(payload)});
        if (!response.ok) {alert(`Submission failed: ${await response.text()}`); return;}
        alert('Submitted to VPOD curators for review.');
        if (addAnother) {
            document.querySelectorAll('#dataEntryFields input, #dataEntryFields textarea').forEach(input => {input.value = '';});
        } else toggleContributeModal();
    } catch (_) {alert('Connection error submitting data.');}
    finally {buttons.forEach(b => b.disabled = false);}
}
$('submissionForm').addEventListener('submit', submitData);
$('subDoi').addEventListener('blur', async () => {
    const value = inputValue('subDoi').replace(/^https?:\/\/(dx\.)?doi.org\//i, '').replace(/^doi:\s*/i, '').toLowerCase();
    try { const response = await fetch(`/api/references/?doi=${encodeURIComponent(value)}`); if (!response.ok) return; const body = await response.json(); $('doiWarning').classList.toggle('hidden', !(body.results || body).length); } catch (_) { /* Optional hint only. */ }
});
document.querySelectorAll('#advancedFilters input, #advancedFilters select, #searchInput').forEach(input => input.addEventListener('input', applyFilters));
document.addEventListener('keydown', e => {if (e.key === 'Escape') for (const id of ['contributeModal', 'citationModal']) $(id).classList.add('hidden');});
if (typeof lucide !== 'undefined') lucide.createIcons();
setSubTier('PUBLICATION'); switchTab('opsins');
