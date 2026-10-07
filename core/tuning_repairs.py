"""Reviewed duplicate merges and conservative restoration of missing source links."""
import json
from pathlib import Path
from django.db import transaction
from .models import HeterologousData, Opsin, TuningAudit, TuningCandidate
from .bibliography import classify_identifier
from .tuning_extraction import assay_snapshot, read_supplement
from .tuning_mutations import base
from .mutation_accessions import normalized_mutations, split_accession


def source_protein(source):
    """Exact accession/taxon first; a recognized mutant suffix may point to its base WT."""
    accession = (source.get('Accession') or '').strip()
    bare, suffix = split_accession(accession)
    names = [accession]
    if suffix and suffix == normalized_mutations(source.get('Mutations')): names.append(bare)
    candidates = [p for p in Opsin.objects.filter(accession__in=names).order_by('pk')
        if (p.genus or '').strip().casefold() == source.get('Genus', '').strip().casefold()
        and (p.species or '').strip().casefold() == source.get('Species', '').strip().casefold()]
    exact = [p for p in candidates if p.accession == accession]
    pool = exact or candidates
    if not pool or len({(p.protein_sequence or '', p.gene_family or '') for p in pool}) != 1:
        return None, 'No unique accession/taxon/sequence match; identity not guessed.'
    chosen = min(pool, key=lambda p: (str(p.reference_id) != str(source.get('refid')), p.pk))
    return chosen, 'exact_accession_taxon' if exact else 'base_wt_for_mutant_construction'


def merge_duplicates(path, apply=False, actor='repair_mutation_accessions', only_source_ids=None):
    """Explicit same-experiment pairs only; never generic species/wavelength deduplication."""
    outcomes = []
    for item in json.loads(Path(path).read_text()).get('merges', []):
        if only_source_ids is not None and item['source'] not in only_source_ids: continue
        with transaction.atomic():
            records = {a.pk: a for a in HeterologousData.objects.select_for_update().select_related('opsin', 'reference').filter(pk__in=[item['source'], item['target']])}
            source, target = records.get(item['source']), records.get(item['target'])
            result = {'source': item['source'], 'target': item['target']}
            if source is None or target is None:
                outcomes.append({**result, 'outcome': 'absent'}); continue
            if source.duplicate_of_id == target.pk:
                outcomes.append({**result, 'outcome': 'already_merged'}); continue
            def identity(a):
                return {'genus': a.opsin.genus if a.opsin else None, 'species': a.opsin.species if a.opsin else None,
                        'lambda_max': a.lambda_max, 'mutations': a.mutations or '',
                        'doi': classify_identifier(a.reference.doi)['doi'] if a.reference else None,
                        'accession_base': base(a.opsin.accession) if a.opsin else None}
            if source.duplicate_of_id or target.duplicate_of_id or identity(source) != item['expected_source'] or identity(target) != item['expected_target']:
                outcomes.append({**result, 'outcome': 'conflict', 'reason': 'Merge identity guards differ; nothing merged.'}); continue
            if apply:
                before = assay_snapshot(source)
                source.duplicate_of = target; source.status = 'REJECTED'
                source.mutation_build = {**source.mutation_build, 'merge': {'retained_hetid': target.pk,
                    'original_opsin_id': source.opsin_id, 'evidence': item['evidence'], 'reason': item['reason']}}
                source.save(update_fields=['duplicate_of', 'status', 'mutation_build', 'updated_at'])
                candidate = TuningCandidate.objects.filter(assay=source).first()
                if candidate:
                    old = {'decision': candidate.decision, 'outcome': candidate.outcome}
                    candidate.decision = 'REJECTED'; candidate.outcome = 'merged_duplicate'; candidate.stale = True
                    candidate.save(update_fields=['decision', 'outcome', 'stale', 'updated_at'])
                    TuningAudit.objects.create(actor=actor, object_type='TuningCandidate', object_key=str(source.pk), before=old,
                        after={'decision': 'REJECTED', 'duplicate_of': target.pk}, reason=item['reason'])
                TuningAudit.objects.create(actor=actor, object_type='HeterologousData', object_key=str(source.pk),
                    before=before, after=assay_snapshot(source), reason=item['reason'])
            outcomes.append({**result, 'outcome': 'merged' if apply else 'would_merge'})
    return outcomes


@transaction.atomic
def synchronize_merged_accessions(actor='repair_mutation_accessions'):
    """Archived rows link to the retained construct; audits preserve original source identities."""
    for source in HeterologousData.objects.exclude(duplicate_of=None).select_related('duplicate_of__opsin', 'opsin', 'reference'):
        target = source.duplicate_of
        if source.opsin_id == target.opsin_id: continue
        old = source.opsin; before = assay_snapshot(source)
        source.opsin = target.opsin; source.save(update_fields=['opsin', 'updated_at'])
        TuningAudit.objects.create(actor=actor, object_type='HeterologousData', object_key=str(source.pk),
            before=before, after=assay_snapshot(source), reason=f'Archived duplicate links to retained construct from het {target.pk}.')
        if old and old.status == 'APPROVED' and not old.heterologous_records.exists():
            old.status = 'REJECTED'; old.save(update_fields=['status', 'updated_at'])
            TuningAudit.objects.create(actor=actor, object_type='Opsin', object_key=str(old.pk),
                before={'status': 'APPROVED'}, after={'status': 'REJECTED', 'retained_opsin': target.opsin_id},
                reason='Retire unused duplicate protein; original sequence and ID retained.')


def recover_missing_links(path, apply=False, actor='repair_mutation_accessions'):
    """Accession/base WT + normalized taxonomy from an unchanged CSV measurement."""
    raw = read_supplement(path); outcomes = []
    for assay in HeterologousData.objects.filter(opsin=None, is_inferred=False).select_related('reference'):
        if not normalized_mutations(assay.mutations): continue
        source = raw.get(assay.pk, {})
        try:
            matched = int(source['refid']) == assay.reference_id and float(source['LambdaMax']) == assay.lambda_max and source['Mutations'] == assay.mutations
        except (KeyError, ValueError, TypeError): matched = False
        if not matched: continue
        protein, basis = source_protein(source)
        if protein is None:
            outcomes.append({'hetid': assay.pk, 'outcome': 'unresolved', 'reason': basis}); continue
        if apply:
            with transaction.atomic():
                if not HeterologousData.objects.filter(pk=assay.pk, opsin=None).update(opsin=protein): continue
                TuningAudit.objects.create(actor=actor, object_type='HeterologousData', object_key=str(assay.pk), before={'opsin_id': None},
                    after={'opsin_id': protein.pk, 'source': source, 'basis': basis}, reason='Restore unambiguous CSV accession/taxon linkage; base WT is constructed separately as a mutant.')
        outcomes.append({'hetid': assay.pk, 'opsin_id': protein.pk, 'basis': basis, 'outcome': 'linked' if apply else 'would_link'})
    return outcomes
