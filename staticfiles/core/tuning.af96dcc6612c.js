/* VPOD beta mapper. Source text is rendered through textContent, never HTML. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const node = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
    const state = {templates: [], catalogue: [], selected: new Set(), result: null, initialized: false, busy: false, revision: 0};
    function status(message, error = false) { $('tmStatus').textContent = message; $('tmStatus').classList.toggle('tm-error', error); }
    async function api(url, payload) {
        const response = await fetch(url, payload === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        let data; try {data = await response.json();} catch (_) {throw new Error('The service did not return data. Please try again or contact a maintainer.');}
        if (!response.ok) throw new Error(data.error || data.detail || `Request failed (${response.status}).`);
        return data;
    }
    function safeLink(text, url) {
        try {const parsed = new URL(url, location.origin); if (!['https:', 'http:'].includes(parsed.protocol)) throw Error();
            const a = node('a', text); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
        } catch (_) {return node('span', text);}
    }
    function refs(e) {
        const wrap = node('div'); const seen = new Set();
        for (const r of e.references) {
            const key = r.doi || String(r.refid); if (seen.has(key)) continue; seen.add(key);
            const p = node('p'); p.append(safeLink(`Ref ${r.refid}: ${r.title || r.doi || 'Source'} (${r.role})`, r.link || r.source_url || (r.doi ? `https://doi.org/${r.doi}` : `/api/references/${r.refid}/`)));
            if (r.locator) p.append(node('span', ` — ${r.locator}`)); wrap.append(p);
        }
        return wrap;
    }
    function effect(e) {return e.shift_nm === null ? 'Not assigned' : `${e.shift_nm > 0 ? '+' : ''}${e.shift_nm} nm${e.changes.length > 1 ? ' (combined)' : ''}`;}
    function visible() {
        const term = $('tmSearch').value.toLowerCase().trim(), family = $('tmFamily').value;
        return state.catalogue.filter(e => e.qualifies && (e.category !== 'PROPOSED' || $('tmProposed').checked)
            && ($('tmCross').checked || family === 'OTHER' || family === e.family)
            && (!$('tmSubtype').value || e.subtype === $('tmSubtype').value)
            && (!$('tmCategory').value || e.category === $('tmCategory').value)
            && (!term || JSON.stringify(e).toLowerCase().includes(term)));
    }
    function validateProteinInput(text, maximum = 20) {
        if (typeof text !== 'string' || text.length > 60000) throw new Error('FASTA limit is 60 KB.');
        const entries = []; let current = null;
        for (const raw of text.trim().split(/\r?\n/)) {
            const line = raw.trim(); if (!line) continue;
            if (line.startsWith('>')) {current = {name: line.slice(1).trim(), sequence: ''}; entries.push(current);}
            else {if (!current) {current = {name: 'Sequence 1', sequence: ''}; entries.push(current);} current.sequence += line.replace(/\s/g, '').toUpperCase();}
        }
        if (!entries.length || entries.length > maximum) throw new Error(`Provide 1–${maximum} proteins.`);
        const names = new Set();
        for (const e of entries) {
            if (!e.name || e.name.length > 150 || names.has(e.name)) throw new Error('Use distinct FASTA names of 1–150 characters.'); names.add(e.name);
            if (e.sequence.length < 30 || e.sequence.length > 2000 || !/^[ACDEFGHIKLMNPQRSTVWYBXZJUO]+$/.test(e.sequence)) throw new Error(`${e.name}: use 30–2,000 ungapped amino acids, without stop symbols.`);
            if (/^[ACGTN]+$/.test(e.sequence)) throw new Error(`${e.name}: this appears to be DNA; provide a translated protein.`);
        }
    }
    function referenceWarning() {
        const reference = state.templates.find(p => p.key === $('tmReference').value), family = $('tmFamily').value;
        $('tmReferenceWarning').hidden = !reference || family === 'OTHER' || reference.family === family;
    }
    function invalidate() {
        referenceWarning(); state.revision++; state.result = null; $('tmResults').hidden = true;
        if (window.VpodStructures) window.VpodStructures.clearMarks();
    }
    function updateCount() {$('tmSelectionCount').textContent = `${state.selected.size} evidence entries selected`; $('tmMap').disabled = state.busy || !state.selected.size;}
    function renderCatalogue() {
        const entries = visible(), allowed = new Set(entries.map(e => e.key));
        for (const key of state.selected) if (!allowed.has(key)) state.selected.delete(key);
        const body = $('tmCatalogue'); body.replaceChildren();
        for (const e of entries) {
            const tr = node('tr'), select = node('input'); select.type = 'checkbox'; select.checked = state.selected.has(e.key); select.setAttribute('aria-label', `Select ${e.title}`); select.dataset.key = e.key;
            select.addEventListener('change', () => {select.checked ? state.selected.add(e.key) : state.selected.delete(e.key); invalidate(); updateCount();});
            const first = node('td'); first.append(select); tr.append(first);
            const site = node('td'); site.append(node('strong', e.original_notation || e.changes.map(c => c.position).join(' + ')), node('p', `${e.protein_key} numbering`, 'tm-hint'));
            tr.append(site, node('td', `${e.organism} · ${e.subtype}`), node('td', `${e.category === 'LITERATURE' ? 'Literature supported' : e.category === 'PROPOSED' ? 'Proposed candidate' : 'Measured'} · ${effect(e)}`));
            const details = node('details'), content = node('div', undefined, 'tm-detail'); details.append(node('summary', `${e.references.length} citation(s) · details`));
            content.append(node('p', e.title), node('p', e.notes), node('p', e.numbering_note), node('p', `Source locator: ${e.source_locator}`));
            if (e.baseline_label) content.append(node('p', `Comparator: ${e.baseline_label}`));
            if (e.baseline_nm !== null) content.append(node('p', `Comparator ${e.baseline_nm} nm → mutant ${e.mutant_nm} nm`));
            for (const [key, value] of Object.entries(e.conditions)) content.append(node('p', `${key.replaceAll('_', ' ')}: ${typeof value === 'object' ? JSON.stringify(value) : value}`));
            content.append(refs(e)); details.append(content); const td = node('td'); td.append(details); tr.append(td); body.append(tr);
        }
        $('tmCatalogueEmpty').hidden = !!entries.length; updateCount();
    }
    function refreshSubtypes() {
        const sel = $('tmSubtype'), old = sel.value; sel.replaceChildren(new Option('All subtypes', ''));
        const family = $('tmFamily').value;
        for (const value of [...new Set(state.catalogue.filter(e => $('tmCross').checked || family === 'OTHER' || e.family === family).map(e => e.subtype))].sort()) sel.add(new Option(value, value));
        if ([...sel.options].some(o => o.value === old)) sel.value = old;
    }
    function payload(fastaOverride) {
        const value = {fasta: fastaOverride === undefined ? $('tmFasta').value : fastaOverride, evidence_keys: [...state.selected], reference: $('tmReference').value, target_family: $('tmFamily').value};
        if (value.reference === 'custom') value.custom_reference = $('tmCustom').value;
        return value;
    }
    async function map() {
        if (state.busy) return;
        if (!$('tmFasta').value.trim()) {status('Add a target amino-acid sequence first.', true); $('tmFasta').focus(); return;}
        if ($('tmReference').value === 'custom' && !$('tmCustom').value.trim()) {status('Add your custom reference protein.', true); return;}
        try {validateProteinInput($('tmFasta').value); if ($('tmReference').value === 'custom') validateProteinInput($('tmCustom').value, 1);} catch (error) {status(error.message, true); return;}
        state.busy = true; updateCount(); status('Aligning proteins and checking coordinate stability…');
        const revision = state.revision, input = payload();
        try {
            const result = await api('/api/tuning-mappings/', input);
            if (revision !== state.revision) {status('Inputs changed while mapping. Map again with the new selection.'); return;}
            state.result = {...result, input}; renderResults(); status(`Mapping complete in ${result.elapsed_seconds} seconds. Review flags before interpreting a site.`);
        } catch (error) {status(error.message, true);} finally {state.busy = false; updateCount();}
    }
    function focusColumn(column, row, sourceProtein, sourcePosition) {
        $('tmAlignment').querySelectorAll('.tm-active').forEach(n => n.classList.remove('tm-active'));
        $('tmAlignment').querySelectorAll(`[data-column="${column}"]`).forEach(n => n.classList.add('tm-active'));
        const span = $('tmAlignment').querySelector(`[data-column="${column}"]`); if (span) $('tmAlignment').scrollLeft = Math.max(0, span.offsetLeft - $('tmAlignment').offsetLeft - 260);
        $('tmMappingRows').querySelectorAll('.tm-row-active').forEach(n => n.classList.remove('tm-row-active')); if (row) row.classList.add('tm-row-active');
        if (window.VpodStructures) window.VpodStructures.focus(sourceProtein, sourcePosition);
    }
    function renderResults() {
        const result = state.result, body = $('tmMappingRows'); body.replaceChildren(); $('tmResults').hidden = false;
        const counts = {}; for (const row of result.rows) counts[row.status] = (counts[row.status] || 0) + 1;
        $('tmResultSummary').textContent = `${result.targets.length} target(s); ${result.selected_keys.length} evidence entries; ${result.rows.length} site rows. ${Object.entries(counts).map(([key, count]) => `${count} ${key.toLowerCase()}`).join(' · ')}. Evidence quality and mapping assessment are separate.`;
        $('tmSimilarity').replaceChildren(...result.summaries.map(s => node('p', `${s.name}: nearest available template ${s.closest_template}, ${(100 * s.identity).toFixed(1)}% identity across aligned residues; ${(100 * s.aligned_target_fraction).toFixed(1)}% target overlap. ${s.note}`, 'tm-hint')));
        for (const r of result.rows) {
            const e = result.evidence.find(e => e.key === r.evidence_key), tr = node('tr'), target = node('td'), button = node('button', r.target_name); button.type = 'button'; button.addEventListener('click', () => focusColumn(r.alignment_column, tr, r.source_protein, r.source_position)); target.append(button);
            tr.append(target, node('td', `${e.original_notation || r.source_position} · ${r.source_protein} ${r.source_position}`), node('td', `${r.reference_name}: ${r.reference_position === null ? 'Unresolved' : r.reference_residue + r.reference_position}`), node('td', r.target_position === null ? 'Unresolved / absent' : r.target_residue + r.target_position));
            const assessment = node('td'); assessment.append(node('strong', r.status)); for (const warning of r.warnings) assessment.append(node('p', warning, 'tm-hint')); tr.append(assessment, node('td', effect(e))); body.append(tr);
        }
        const cols = new Set(result.rows.map(r => r.alignment_column)); $('tmAlignment').replaceChildren();
        for (const seq of result.alignment.sequences) {
            const line = node('div', undefined, 'tm-alignment-line'); const label = node('span', seq.name, 'tm-seqname'); label.title = seq.name; line.append(label); let position = 0;
            for (let i = 0; i < seq.aligned.length; i++) {const aa = seq.aligned[i]; if (aa !== '-') position++; const cell = node('span', aa, 'tm-residue' + (cols.has(i + 1) ? ' tm-site' : '')); cell.dataset.column = i + 1; cell.title = `${seq.name}: ${aa === '-' ? 'gap' : aa + position}, alignment column ${i + 1}`; line.append(cell);}
            $('tmAlignment').append(line);
        }
    }
    function download(name, data, type = 'text/plain') {const url = URL.createObjectURL(new Blob([data], {type})); const a = node('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1500);}
    function csv(rows) {if (!rows.length) return ''; const keys = [...new Set(rows.flatMap(r => Object.keys(r)))]; const cell = value => {let text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value); if (/^[\s]*[=+@-]/.test(text) && typeof value !== 'number') text = "'" + text; return '"' + text.replaceAll('"', '""') + '"';}; return [keys, ...rows.map(r => keys.map(k => r[k]))].map(r => r.map(cell).join(',')).join('\r\n');}
    function mappingCSV() {
        const result = state.result;
        return csv(result.rows.map(r => {
            const e = result.evidence.find(e => e.key === r.evidence_key);
            const supporting = result.evidence.filter(other => other.protein_key === r.source_protein && other.changes.some(c => c.position === r.source_position));
            const citations = [...new Map(supporting.flatMap(e => e.references).map(ref => [ref.doi || ref.refid, ref])).values()];
            return {...r, published_notation: e.original_notation, source_organism: e.organism, source_conditions: e.conditions, evidence_notes: e.notes,
                source_locator: e.source_locator, source_reference_ids: citations.map(ref => ref.refid), source_dois: citations.map(ref => ref.doi), source_titles: citations.map(ref => ref.title),
                catalogue_release: e.release, alignment_profile_sha256: result.profile_sha256, algorithm: result.algorithm, mafft_version: result.mafft_version, warning_effect_is_not_a_prediction: true};
        }));
    }
    async function proteinSearch() {
        const term = $('tmProteinSearch').value.trim(); if (!term) return;
        $('tmProteinResults').textContent = 'Searching…';
        try {const data = await api('/api/opsins/?search=' + encodeURIComponent(term)); const rows = (Array.isArray(data) ? data : data.results || []).filter(r => r.protein_sequence).slice(0, 12); $('tmProteinResults').replaceChildren();
            for (const p of rows) {const button = node('button', `Add ${p.genus || ''} ${p.species || ''} · VPOD ${p.opsinid}`); button.type = 'button'; button.addEventListener('click', () => {const id = p.opsinid; $('tmFasta').value += `${$('tmFasta').value.trim() ? '\n' : ''}>VPOD ${id}: ${p.genus || ''} ${p.species || ''}\n${p.protein_sequence}\n`; invalidate(); status('Protein added. Review the target names before mapping.');}); $('tmProteinResults').append(button);}
            $('tmProteinResults').append(node('p', rows.length ? 'Showing up to 12 matches with protein sequences; narrow the search if needed.' : 'No matching public protein sequences.', 'tm-hint'));
        } catch (error) {$('tmProteinResults').textContent = error.message;}
    }
    async function restore(file) {
        if (!file || file.size > 25e6) throw new Error('Choose a VPOD JSON session up to 25 MB.');
        const data = JSON.parse(await file.text());
        if (data.kind === 'vpod-structure-view') {await window.VpodStructures.restore(data); return;}
        if (data.kind !== 'vpod-tuning-mapping' || data.version !== 1 || !data.input || typeof data.input.fasta !== 'string' || data.input.fasta.length > 60000) throw new Error('Not a supported VPOD session.');
        const input = data.input; $('tmFasta').value = input.fasta; $('tmCustom').value = typeof input.custom_reference === 'string' ? input.custom_reference.slice(0, 60000) : '';
        $('tmFamily').value = ['C_OPSIN', 'R_OPSIN', 'OTHER'].includes(input.target_family) ? input.target_family : 'OTHER';
        $('tmReference').value = [...$('tmReference').options].some(o => o.value === input.reference) ? input.reference : 'bovine'; $('tmCustomArea').hidden = $('tmReference').value !== 'custom';
        $('tmSearch').value = ''; $('tmSubtype').value = ''; $('tmCategory').value = ''; $('tmCross').checked = true; $('tmCrossWarning').hidden = false;
        const keys = Array.isArray(input.evidence_keys) ? input.evidence_keys : []; $('tmProposed').checked = state.catalogue.some(e => keys.includes(e.key) && e.category === 'PROPOSED');
        state.selected = new Set(keys.filter(k => state.catalogue.some(e => e.key === k))); invalidate(); refreshSubtypes(); renderCatalogue(); status('Inputs restored. Map again to verify against the current catalogue; saved results were not trusted.');
    }
    async function open() {
        if (state.initialized) return;
        status('Loading the curated tuning catalogue…');
        try {const [catalogue, templates] = await Promise.all([api('/api/tuning-sites/'), api('/api/tuning-templates/')]); state.catalogue = catalogue.results; state.templates = templates.results; refreshSubtypes(); renderCatalogue(); state.initialized = true; status(`${catalogue.count} curated evidence entries loaded. Select the sites relevant to your protein.`);}
        catch (error) {status(`${error.message} The beta catalogue may need its migration and import; other explorer tabs remain available.`, true);}
    }
    for (const id of ['tmFasta', 'tmCustom']) $(id).addEventListener('input', invalidate);
    $('tmReference').addEventListener('change', () => {$('tmCustomArea').hidden = $('tmReference').value !== 'custom'; invalidate();});
    $('tmFamily').addEventListener('change', () => {const key = $('tmFamily').value === 'R_OPSIN' ? 'squid' : 'bovine'; $('tmReference').value = key; $('tmStructureReference').value = key; $('tmCustomArea').hidden = true; invalidate(); refreshSubtypes(); renderCatalogue();});
    for (const id of ['tmSearch', 'tmSubtype', 'tmCategory', 'tmProposed', 'tmCross']) $(id).addEventListener(id === 'tmSearch' ? 'input' : 'change', () => {invalidate(); $('tmCrossWarning').hidden = !$('tmCross').checked; if (id === 'tmCross') refreshSubtypes(); renderCatalogue();});
    $('tmSelectAll').onclick = () => {for (const e of visible()) state.selected.add(e.key); invalidate(); renderCatalogue();}; $('tmClear').onclick = () => {state.selected.clear(); invalidate(); renderCatalogue();}; $('tmMap').onclick = map;
    $('tmExample').onclick = () => {const p = state.templates.find(p => p.key === ($('tmFamily').value === 'R_OPSIN' ? 'spider' : 'bovine')); if (p) {$('tmFasta').value = `>${p.name}\n${p.sequence}`; invalidate();}};
    $('tmFastaFile').onchange = async event => {try {const f = event.target.files[0]; if (!f) return; if (f.size > 60000) throw new Error('FASTA limit is 60 KB.'); $('tmFasta').value = await f.text(); invalidate();} catch (error) {status(error.message, true);}};
    $('tmProteinFind').onclick = proteinSearch;
    $('tmExport').onclick = () => {if (state.result) download('VPOD_mapped_sites.csv', mappingCSV(), 'text/csv;charset=utf-8');};
    $('tmEvidenceExport').onclick = () => {if (state.result) download('VPOD_tuning_evidence.csv', csv(state.result.evidence.flatMap(e => e.references.map(r => ({evidence_key: e.key, title: e.title, category: e.category, organism: e.organism, subtype: e.subtype, numbering_protein: e.protein_key, changes: e.changes, original_notation: e.original_notation, baseline_label: e.baseline_label, baseline_nm: e.baseline_nm, mutant_nm: e.mutant_nm, source_shift_nm: e.shift_nm, conditions: e.conditions, notes: e.notes, source_locator: e.source_locator, release: e.release, ...Object.fromEntries(Object.entries(r).map(([k,v]) => ['reference_' + k, v]))})))), 'text/csv;charset=utf-8');};
    $('tmAlignmentExport').onclick = () => {if (state.result) download('VPOD_alignment.fasta', state.result.alignment.sequences.map(s => `>${s.name}\n${s.aligned}`).join('\n'));};
    $('tmSessionExport').onclick = () => {if (state.result) download('VPOD_mapping_session.json', JSON.stringify({kind: 'vpod-tuning-mapping', version: 1, saved_at: new Date().toISOString(), input: state.result.input, result: state.result}, null, 2), 'application/json');};
    $('tmSessionFile').onchange = async e => {try {await restore(e.target.files[0]);} catch (error) {status(error.message, true);} e.target.value = '';};
    window.VpodTuning = {open, state, status, api, node, download, payload, csv, focusColumn};
})();
