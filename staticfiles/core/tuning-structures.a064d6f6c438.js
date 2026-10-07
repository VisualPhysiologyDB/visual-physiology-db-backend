/* Pinned Mol* 5.13.0 adapter. Coordinate files and view sessions stay in the browser. */
(() => {
    'use strict';
    const T = window.VpodTuning, $ = id => document.getElementById(id);
    const slots = {}, queues = {}, MAX_BYTES = 10 * 1024 * 1024;
    let loading;
    async function library() {
        if (window.molstar) return;
        if (!loading) loading = new Promise((resolve, reject) => {
            const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = $('tuningPanel').dataset.molstarCss; document.head.append(css);
            const script = document.createElement('script'); script.src = $('tuningPanel').dataset.molstarJs; script.onload = resolve; script.onerror = () => {loading = null; reject(new Error('Structure viewer could not load. Tables and exports are still available.'));}; document.head.append(script);
        });
        await loading;
    }
    function message(key, text, error = false) {const n = $(`tm${key}StructureStatus`); n.textContent = text; n.classList.toggle('tm-error', error);}
    async function viewer(key) {
        await library();
        if (!slots[key]) {
            const v = await molstar.Viewer.create($(`tm${key}Viewer`), {layoutIsExpanded: false, layoutShowControls: false, layoutShowLeftPanel: false, layoutShowLog: false, layoutShowSequence: true, viewportShowExpand: false, viewportShowSelectionMode: false, pdbProvider: 'rcsb'});
            slots[key] = {viewer: v, chains: [], marks: [], mappings: [], generation: 0};
        }
        return slots[key];
    }
    function chainsIn(model) {
        const h = model.atomicHierarchy, result = [];
        for (let c = 0; c < h.chains._rowCount; c++) {
            const entity = h.index.getEntityFromChain(c), sequence = model.sequence.byEntityKey[entity]?.sequence;
            if (!sequence || sequence.kind !== 'protein' || sequence.length < 30) continue;
            const label = h.chains.label_asym_id.value(c), author = h.chains.auth_asym_id.value(c), observed = new Map();
            const start = h.chainAtomSegments.offsets[c], end = h.chainAtomSegments.offsets[c + 1];
            for (let atom = start; atom < end; atom++) {
                const r = h.residueAtomSegments.index[atom], seqid = h.residues.label_seq_id.value(r);
                observed.set(seqid, {author: h.residues.auth_seq_id.value(r), insertion: h.residues.pdbx_PDB_ins_code.value(r)});
            }
            const residues = [], code = [];
            for (let i = 0; i < sequence.length; i++) {const seqid = sequence.seqId.value(i), aa = sequence.code.value(i); residues.push({seqid, observed: observed.get(seqid)}); code.push(/^[ACDEFGHIKLMNPQRSTVWYBXZJUO]$/.test(aa) ? aa : 'X');}
            result.push({label, author, sequence: code.join(''), residues});
        }
        return result;
    }
    async function removeMarks(slot) {
        slot.generation++;
        const v = slot.viewer; v.structureInteractivity({action: ['select', 'highlight']});
        if (slot.component) {await v.plugin.state.data.build().delete(slot.component.ref).commit(); slot.component = null;}
        slot.marks = []; slot.mappings = [];
    }
    async function load(key, text, format, name, template = null) {
        if (typeof text !== 'string' || text.length > MAX_BYTES || !['pdb', 'mmcif'].includes(format)) throw new Error('Use a PDB/mmCIF file up to 10 MB.');
        message(key, 'Loading local coordinates…'); const slot = await viewer(key); await removeMarks(slot); await slot.viewer.plugin.clear();
        await slot.viewer.loadStructureFromData(text, format, {dataLabel: name.slice(0, 120)});
        const structure = slot.viewer.plugin.managers.structure.hierarchy.current.structures[0];
        if (!structure?.cell.obj?.data.models[0]) throw new Error('No atomic model found.');
        slot.structure = structure; slot.file = {text, format, name: name.slice(0, 120)}; slot.chains = chainsIn(structure.cell.obj.data.models[0]);
        const select = $(`tm${key}Chain`); select.replaceChildren();
        for (const c of slot.chains) select.add(new Option(`Chain ${c.label} (author ${c.author}), ${c.sequence.length} residues`, c.label));
        if (!slot.chains.length) throw new Error('No protein chain of at least 30 residues found.');
        message(key, 'First model loaded. Choose a chain, then mark sites. Sequence is taken from the file; missing sequence records can limit mapping.');
        if (template) {slot.family = template.family; slot.referenceKey = template.key; message(key, `First model · ${template.structure.pdb_id}. ${template.structure.ligand_note}. Choose a chain, then mark sites.`);}
        return slot;
    }
    async function paint(slot, elements) {
        if (!elements.length) return;
        const structure = slot.structure.cell.obj.data, E = molstar.lib.structure.StructureElement;
        const loci = E.Loci.fromSchema(structure, {items: elements});
        if (E.Loci.isEmpty(loci)) throw new Error('No coordinates matched these chain/residue identifiers.');
        const bundle = E.Bundle.fromLoci(loci);
        const component = await slot.viewer.plugin.builders.structure.tryCreateComponent(slot.structure.cell.transform.ref, {type: {name: 'bundle', params: bundle}, nullIfEmpty: true, label: 'VPOD tuning sites'}, 'vpod-tuning-sites');
        if (!component) throw new Error('Selected residues have no viewable atoms.');
        slot.component = component;
        await slot.viewer.plugin.builders.structure.representation.addRepresentation(component, {type: 'ball-and-stick', color: 'uniform', colorParams: {value: 0xf97316}, typeParams: {sizeFactor: 0.35}});
        await slot.viewer.plugin.builders.structure.representation.addRepresentation(component, {type: 'label', color: 'uniform', colorParams: {value: 0x9a3412}, typeParams: {level: 'residue', background: true, backgroundColor: 0xffffff, backgroundOpacity: 0.7}});
        slot.marks = elements;
    }
    async function mark(key) {
        const slot = slots[key]; if (!slot?.file) throw new Error('Load a structure first.');
        if (key === 'Reference' && slot.referenceKey !== $('tmStructureReference').value) throw new Error('Load the selected reference structure before marking sites.');
        if (!T.state.selected.size) throw new Error('Select catalogue evidence first.');
        await removeMarks(slot); const generation = slot.generation, revision = T.state.revision;
        const chain = slot.chains.find(c => c.label === $(`tm${key}Chain`).value); if (!chain) throw new Error('Choose a protein chain.');
        message(key, 'Mapping selected evidence to the chain sequence…');
        const request = T.payload(`>Structure chain ${chain.label}\n${chain.sequence}`);
        if (key === 'Reference' && slot.family) request.target_family = slot.family;
        const result = await T.api('/api/tuning-mappings/', request);
        if (generation !== slot.generation || revision !== T.state.revision) {message(key, 'Selection changed. Mark sites again.'); return;}
        const elements = [], rows = []; let missing = 0, unresolved = 0, review = 0;
        for (const r of result.rows) {
            const residue = r.target_position === null ? null : chain.residues[r.target_position - 1];
            if (!residue) {unresolved++; continue;} if (!residue.observed) {missing++; continue;}
            if (r.status !== 'MAPPED') review++;
            const schema = {label_asym_id: chain.label, label_seq_id: residue.seqid}; elements.push(schema); rows.push({...r, schema, chain_label: chain.label, chain_author: chain.author, label_seq_id: residue.seqid, author_residue: residue.observed.author, insertion_code: residue.observed.insertion});
        }
        const unique = [...new Map(elements.map(e => [e.label_seq_id, e])).values()]; await paint(slot, unique);
        if (generation !== slot.generation || revision !== T.state.revision) {await removeMarks(slot); message(key, 'Selection changed. Mark sites again.'); return;}
        slot.mappings = rows; slot.profile = result.profile_sha256; slot.evidenceKeys = result.selected_keys;
        message(key, `${slot.file.name}: ${unique.length} residues marked in orange (${review} mapped evidence row(s) require review); ${missing} site row(s) lack coordinates; ${unresolved} unresolved. Labels use the structure’s author numbering. Drag to rotate; wheel to zoom.`);
        const details = T.node('details'); details.append(T.node('summary', 'Residue numbering and mapping warnings'));
        for (const r of rows) details.append(T.node('p', `${r.source_protein} ${r.source_position} → chain ${r.chain_author}, ${r.author_residue}${r.insertion_code || ''} (label_seq_id ${r.label_seq_id}; chain sequence ${r.target_position}). ${r.warnings.join(' ')}`, 'tm-hint'));
        $(`tm${key}StructureStatus`).append(details);
    }
    async function saveImage(key) {
        const slot = slots[key]; if (!slot?.file) throw new Error('Load a structure first.');
        const uri = await slot.viewer.plugin.helpers.viewportScreenshot.getImageDataUri(); const a = T.node('a'); a.href = uri; a.download = `VPOD_${key.toLowerCase()}_tuning_sites.png`; a.click();
    }
    function saveView(key) {
        const slot = slots[key]; if (!slot?.file) throw new Error('Load a structure first.');
        const camera = slot.viewer.plugin.canvas3d.camera.getSnapshot();
        T.download(`VPOD_${key.toLowerCase()}_structure_view.json`, JSON.stringify({kind: 'vpod-structure-view', version: 1, molstar: '5.13.0', saved_at: new Date().toISOString(), structure: slot.file, chain: $(`tm${key}Chain`).value, marked: slot.marks, mappings: slot.mappings, evidence_keys: slot.evidenceKeys, profile_sha256: slot.profile, camera, caveat: 'Saved view only; coordinate mapping is not a predicted spectral effect.'}), 'application/json');
    }
    async function restore(data) {
        if (data.version !== 1 || !data.structure || typeof data.structure.name !== 'string' || !Array.isArray(data.marked) || data.marked.length > 1000) throw new Error('Invalid structure view session.');
        await open(); const slot = await load('User', data.structure.text, data.structure.format, data.structure.name);
        const valid = data.marked.filter(e => typeof e.label_asym_id === 'string' && Number.isInteger(e.label_seq_id) && slot.chains.some(c => c.label === e.label_asym_id && c.residues.some(r => r.seqid === e.label_seq_id && r.observed))).map(e => ({label_asym_id: e.label_asym_id, label_seq_id: e.label_seq_id}));
        await paint(slot, valid); if (slot.chains.some(c => c.label === data.chain)) $('tmUserChain').value = data.chain;
        const camera = {};
        for (const key of ['position', 'target', 'up']) if (Array.isArray(data.camera?.[key]) && data.camera[key].length === 3 && data.camera[key].every(x => typeof x === 'number' && Number.isFinite(x) && Math.abs(x) < 1e9)) camera[key] = data.camera[key];
        if (Object.keys(camera).length === 3) slot.viewer.plugin.canvas3d.requestCameraReset({snapshot: camera, durationMs: 0});
        message('User', `Restored ${valid.length} saved marks and camera locally. Saved marks have not been reverified against today’s catalogue; use “Mark selected sites” to verify.`);
    }
    async function open() {$('tmStructures').hidden = false; await library();}
    function guarded(key, fn) {
        const task = (queues[key] || Promise.resolve()).then(fn);
        queues[key] = task.catch(error => message(key, error.message || 'Structure viewer could not complete this operation.', true));
        return queues[key];
    }
    $('tmOpenStructures').onclick = () => guarded('Reference', open);
    $('tmLoadReference').onclick = () => guarded('Reference', async () => {const p = T.state.templates.find(p => p.key === $('tmStructureReference').value); if (!p?.structure.url) throw new Error('Reference coordinates are unavailable.'); const response = await fetch(p.structure.url); if (!response.ok) throw new Error('Reference coordinate download failed.'); await load('Reference', await response.text(), 'mmcif', `${p.name} · ${p.structure.pdb_id}`, p);});
    $('tmStructureFile').onchange = event => guarded('User', async () => {const file = event.target.files[0]; if (!file) return; if (file.size > MAX_BYTES) throw new Error('Structure file exceeds 10 MB.'); const format = /\.(cif|mmcif)$/i.test(file.name) ? 'mmcif' : /\.(pdb|ent)$/i.test(file.name) ? 'pdb' : null; if (!format) throw new Error('Choose a .pdb, .ent, .cif or .mmcif file.'); await load('User', await file.text(), format, file.name);});
    for (const key of ['Reference', 'User']) {
        $(`tm${key}Mark`).onclick = () => guarded(key, () => mark(key)); $(`tm${key}Image`).onclick = () => guarded(key, () => saveImage(key)); $(`tm${key}ViewSave`).onclick = () => guarded(key, () => saveView(key));
        $(`tm${key}Chain`).onchange = () => guarded(key, async () => {if (slots[key]) await removeMarks(slots[key]); message(key, 'Chain changed. Mark sites again.');});
    }
    window.VpodStructures = {restore, slots,
        clearMarks: () => {for (const [key, slot] of Object.entries(slots)) {slot.generation++; guarded(key, async () => {await removeMarks(slot); message(key, 'Inputs changed. Mark selected sites again.');});}},
        focus: (sourceProtein, sourcePosition) => {for (const slot of Object.values(slots)) {const elements = slot.mappings.filter(r => r.source_protein === sourceProtein && r.source_position === sourcePosition).map(r => r.schema); if (elements.length) slot.viewer.structureInteractivity({elements: {items: elements}, action: ['highlight', 'focus']});}}
    };
})();
