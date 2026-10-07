"""Repair mutant accession identities without rebuilding already tagged proteins.

A bare accession linked to WT gets a separate construct. Unsupported reconstruction
keeps the measured assay but leaves its mutant sequence unknown and reports why.
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import HeterologousData, Opsin, TuningAudit, TuningCandidate
from .tuning_extraction import (AlignmentCache, PRIORITY, assay_snapshot, inventory,
    mutation, valid_sequence, same_publication, read_supplement)
from .mutation_accessions import normalized_mutations, split_accession, tagged_accession
from .tuning_mapping import coordinate_maps, MappingError

BUILD_VERSION = 'accession-repair-1'


def sha(sequence):
    return hashlib.sha256(sequence.encode()).hexdigest()


def base(value):
    return re.sub(r'\.[0-9]+$', '', split_accession(value)[0])


def substitutions(value):
    label = normalized_mutations(value)
    parts = [mutation(p) for p in label.split(',')] if label else []
    return parts if parts and all(p['kind'] == 'substitution' for p in parts) else []


def plan_mutant(row, rows, cache, retry_unresolved=False):
    plan = {'hetid': row['hetid'], 'notation': row['mutations'], 'original_opsin_id': row['opsin_id'],
            'outcome': 'unresolved', 'reason': '', 'input': row}
    if row.get('duplicate_of_id'):
        plan.update(outcome='merged_duplicate', reason=f'Retained assay {row["duplicate_of_id"]}.'); return plan
    label = normalized_mutations(row['mutations'])
    if not label:
        plan.update(outcome='excluded_annotation', reason='Not an explicit mutation label; chimeras are outside scope.'); return plan
    if not row['opsin_id'] or not row['accession']:
        plan['reason'] = 'No linked sequence/accession. Correct the source identity in admin; no taxonomy guessed.'; return plan
    accession = tagged_accession(row['accession'], row['mutations'])
    flagged = row.get('mutation_build', {}).get('needs_sequence_review')
    if accession == row['accession'].strip() and not (retry_unresolved and flagged):
        plan.update(outcome='already_tagged_needs_review' if flagged else 'already_tagged',
                    needs_sequence_review=bool(flagged), reason=row['mutation_build'].get('reason', '') if flagged else 'Existing mutant sequence and accession preserved.')
        return plan
    plan.update(accession=accession, outcome='tag', sequence=row['protein_sequence_raw'] or None,
                needs_sequence_review=True)
    _, previous_label = split_accession(row['accession'])
    source_corrected = bool(row.get('mutation_build', {}).get('source_corrections'))
    if previous_label and previous_label != label and not source_corrected:
        plan['reason'] = 'Accession suffix disagreed with the assay mutation. Sequence retained; verify the notation against the paper before tuning approval.'
        return plan
    # A shared WT must never become a mutant merely by renaming its accession.
    shared_wt = any(w['opsin_id'] == row['opsin_id'] and mutation(w['mutations'])['kind'] == 'wild_type'
                    for w in rows)
    if shared_wt: plan['sequence'] = None
    parts = substitutions(row['mutations'])
    if not parts:
        plan['reason'] = 'Indel sequence requires source curation; automatic construction handles substitutions only.'; return plan
    taxon = (row['genus'].strip().casefold(), row['species'].strip().casefold())
    matches = [w for w in rows if not w.get('duplicate_of_id') and mutation(w['mutations'])['kind'] == 'wild_type'
        and not w['is_inferred'] and base(w['accession']) == base(row['accession']) and valid_sequence(w['protein_sequence'])
        and (w['genus'].strip().casefold(), w['species'].strip().casefold()) == taxon]
    if not all(taxon) or not matches:
        plan['reason'] = 'No same-species WT measurement with the base accession. Accession tagged; sequence not reconstructed.'; return plan
    exact = [w for w in matches if w['accession'].strip() == split_accession(row['accession'])[0]]
    pool = exact or matches
    if len({w['protein_sequence'] for w in pool}) != 1:
        plan['reason'] = 'Conflicting WT sequences for this accession; curator must select the source.'; return plan
    wt = min(pool, key=lambda w: (not same_publication(w, row), w['hetid']))
    sequence = wt['protein_sequence']
    # Even without a WT assay sharing this Opsin ID, an identical base sequence is not a mutant.
    if row['protein_sequence'] == sequence: plan['sequence'] = None
    try:
        maps = [coordinate_maps(a) for a in cache.align(sequence)]
    except (MappingError, OSError, ValueError) as exc:
        plan['reason'] = str(exc); return plan
    choices = []
    schemes = [row['mutation_numbering']] if row.get('mutation_numbering') else PRIORITY
    for scheme in schemes:
        if scheme == 'human' and taxon != ('homo', 'sapiens'): continue
        key = 'query' if scheme in {'self', 'human'} else scheme
        changes = []
        for part in parts:
            hits = [m['query'][1].get(m[key][0].get(part['position'])) for m in maps]
            if not hits[0] or hits[0] != hits[1] or hits[0][1] != part['from']: break
            changes.append({'position': hits[0][0], 'from': part['from'], 'to': part['to'], 'published_position': part['position']})
        if len(changes) == len(parts) and len({c['position'] for c in changes}) == len(changes):
            choices.append({'scheme': scheme, 'changes': changes})
    if not choices:
        plan['reason'] = 'No stable numbering matches all WT starting residues. Check source notation; mutations are never reversed automatically.'; return plan
    chosen = choices[0]; result = list(sequence)
    for change in chosen['changes']: result[change['position']-1] = change['to']
    result = ''.join(result)
    if row['protein_sequence'] not in ('', sequence, result):
        plan['reason'] = 'Stored sequence differs from both the WT and expected mutant. Preserved for primary-source review.'; return plan
    plan.update(sequence=result, needs_sequence_review=False, wt=wt, numbering=chosen['scheme'],
        changes=chosen['changes'], alternatives=choices,
        reason='Tagged construct checked against the base WT and forward substitutions; existing correctly tagged proteins are unchanged.')
    return plan


@transaction.atomic
def apply_plan(plan, actor):
    if plan['outcome'] != 'tag': return plan['outcome']
    assay = HeterologousData.objects.select_for_update().select_related('opsin', 'reference').get(pk=plan['hetid'])
    if assay_snapshot(assay) != plan['input']:
        raise ValidationError('Source changed after planning; rerun the repair.')
    if plan.get('wt'):
        wt = HeterologousData.objects.select_related('opsin', 'reference').get(pk=plan['wt']['hetid'])
        if assay_snapshot(wt) != plan['wt']: raise ValidationError('WT changed after planning; rerun.')
    original = assay.opsin; old_id = original.pk
    previous = dict(assay.mutation_build)
    provenance = {**previous, 'version': BUILD_VERSION, 'original_opsin_id': previous.get('original_opsin_id', old_id),
        'original_accession': previous.get('original_accession', original.accession),
        'original_sequence_sha256': previous.get('original_sequence_sha256', sha(plan['input']['protein_sequence'])),
        'notation': plan['notation'], 'reason': plan['reason'], 'needs_sequence_review': plan['needs_sequence_review'],
        'mutant_sha256': sha(plan['sequence']) if plan['sequence'] else None}
    if plan.get('wt'):
        provenance.update(wt_assay_id=plan['wt']['hetid'], wt_opsin_id=plan['wt']['opsin_id'],
            wt_sha256=sha(plan['wt']['protein_sequence']), numbering=plan['numbering'], changes=plan['changes'],
            numbering_alternatives=plan['alternatives'],
            sequence_provenance='Constructed/checked from WT and recorded substitutions, not independent mutant sequencing.')
    # Rename an exclusively linked, unchanged mutant in place. Shared proteins are copied.
    before = {'opsin_id': old_id, 'accession': original.accession, 'mutation_build': previous}
    exclusive = not original.heterologous_records.exclude(pk=assay.pk).exists()
    if exclusive and (original.protein_sequence or None) == plan['sequence']:
        original.accession = plan['accession']; original.full_clean(); original.save(update_fields=['accession', 'updated_at'])
        protein = original
    else:
        protein = Opsin.objects.filter(genus=original.genus, species=original.species, accession=plan['accession'],
            protein_sequence=plan['sequence'], status=assay.status, reference=assay.reference).order_by('pk').first()
        if protein is None:
            protein = Opsin(genus=original.genus, species=original.species, gene_family=original.gene_family,
                phylum=original.phylum, accession=plan['accession'], protein_sequence=plan['sequence'],
                dna_sequence=original.dna_sequence if plan['sequence'] == original.protein_sequence else None,
                reference=assay.reference, status=assay.status)
            protein.full_clean(); protein.save()
    assay.opsin = protein; assay.mutation_build = provenance
    assay.save(update_fields=['opsin', 'mutation_build', 'updated_at'])
    TuningAudit.objects.create(actor=actor, object_type='HeterologousData', object_key=str(assay.pk), before=before,
        after={'opsin_id': protein.pk, 'accession': protein.accession, 'mutation_build': provenance},
        reason='Repair accession identity; shared WT proteins and source measurements retained.')
    return 'tagged_needs_review' if plan['needs_sequence_review'] else 'tagged_verified_construct'


def apply_source_corrections(path, apply=False, actor='repair_mutation_accessions', only_ids=None):
    """Small explicit, reviewed corrections; conditional updates, never broad reversal."""
    from .bibliography import classify_identifier
    data = json.loads(Path(path).read_text()); outcomes = []
    for item in data['corrections']:
        if only_ids is not None and (item['model'], item['id']) not in only_ids: continue
        model = Opsin if item['model'] == 'Opsin' else HeterologousData if item['model'] == 'HeterologousData' else None
        if model is None: raise ValueError('Unsupported source-correction model.')
        with transaction.atomic():
            obj = model.objects.select_for_update().select_related('reference').filter(pk=item['id']).first()
            if obj is None:
                outcomes.append({'id': item['id'], 'outcome': 'absent'}); continue
            updates = item['set']
            if set(updates) - ({'protein_sequence'} if model is Opsin else {'mutations', 'mutation_numbering'}):
                raise ValueError('Source corrections can only change sequence/notation/numbering.')
            current = {k: getattr(obj, k) for k in updates}
            if current == updates:
                outcomes.append({'id': obj.pk, 'outcome': 'unchanged'}); continue
            expected = item['expected']
            checks = {k: (sha(re.sub(r'\s+', '', obj.protein_sequence or '').upper()) if k == 'sequence_sha256'
                       else classify_identifier(obj.reference.doi)['doi'] if k == 'reference_doi' and obj.reference
                       else getattr(obj, k, None)) for k in expected}
            if checks != expected:
                outcomes.append({'id': obj.pk, 'outcome': 'conflict', 'reason': 'Original-value guards differ; curator values preserved.'}); continue
            if apply:
                for key, value in updates.items(): setattr(obj, key, value)
                if model is HeterologousData:
                    obj.mutation_build = {**obj.mutation_build, 'source_corrections': {'original': current, 'evidence': item['evidence'], 'reason': item['reason']}}
                obj.full_clean(); obj.save()
                TuningAudit.objects.create(actor=actor, object_type=model.__name__, object_key=str(obj.pk), before=current,
                    after={'values': updates, 'evidence': item['evidence']}, reason=item['reason'])
            outcomes.append({'id': obj.pk, 'outcome': 'corrected' if apply else 'would_correct'})
    return outcomes



def repair_inventory(apply=False, assay_ids=None, actor='repair_mutation_accessions', cache=None, retry_unresolved=False):
    class LazyCache:
        inner = None
        def align(self, sequence):
            if self.inner is None: self.inner = AlignmentCache(Path(settings.BASE_DIR) / 'var/tuning-extraction-cache')
            return self.inner.align(sequence)
    rows = inventory(); cache = cache or LazyCache()
    plans = []
    for row in rows:
        if row['is_inferred'] or mutation(row['mutations'])['kind'] == 'wild_type' or (assay_ids is not None and row['hetid'] not in assay_ids): continue
        plan = plan_mutant(row, rows, cache, retry_unresolved=retry_unresolved)
        if apply:
            try: plan['action'] = apply_plan(plan, actor)
            except ValidationError as exc: plan['action'] = 'conflict'; plan['reason'] = '; '.join(exc.messages)
        plans.append(plan)
    return {'version': BUILD_VERSION, 'mode': 'apply' if apply else 'dry-run', 'rows': plans,
            'counts': dict(Counter(p.get('action', p['outcome']) for p in plans))}
