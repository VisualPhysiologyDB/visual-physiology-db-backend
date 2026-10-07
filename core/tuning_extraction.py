"""Offline, conservative single-site extraction. Sequence checks are not paper verification.

VPOD's published mutant sequences were reconstructed from mutation descriptions.
Automatic approval records sequence consistency, never a fictitious human paper review.
"""
import csv
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import HeterologousData, TuningCandidate, TuningEvidence, TuningProtein, TuningCitation, TuningAudit
from .tuning_mapping import read_alignment, coordinate_maps, run_mafft, aligner_version, MappingError
from .bibliography import classify_identifier

VERSION = 'single-sites-2'
AA = 'ACDEFGHIKLMNPQRSTVWY'
PRIORITY = ['bovine', 'human', 'squid', 'self', 'spider']
def strict_conditions(candidate=None):
    return bool(getattr(settings, 'VPOD_TUNING_STRICT_CONDITIONS', False) or
                (candidate and candidate.require_condition_match))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def mutation(raw):
    raw = (raw or '').strip()
    if raw.casefold() in {'', 'wt', 'wild type', 'wild-type', 'wildtype', 'no mutations'}:
        return {'kind': 'wild_type'}
    match = re.fullmatch(f'([{AA}])([1-9][0-9]*)([{AA}])', raw)
    if match:
        return {'kind': 'substitution' if match[1] != match[3] else 'no_change',
                'from': match[1], 'position': int(match[2]), 'to': match[3]}
    match = re.fullmatch(f'([{AA}])([1-9][0-9]*)del', raw)
    if match:
        return {'kind': 'deletion', 'from': match[1], 'position': int(match[2]), 'to': None}
    match = re.fullmatch(f'ins([1-9][0-9]*)([{AA}])', raw)
    if match:
        return {'kind': 'insertion', 'from': None, 'position': int(match[1]), 'to': match[2]}
    return {'kind': 'wild_type' if not raw else 'chimera' if re.search(r'chimer(?:a|ic)', raw, re.I)
            else 'multiple' if ',' in raw or ';' in raw else 'unparsed'}


def assay_snapshot(a):
    o = a.opsin
    raw_sequence = (o.protein_sequence or '') if o else ''
    return {'hetid': a.pk, 'mutations': a.mutations or '', 'lambda_max': a.lambda_max,
            'mutation_numbering': a.mutation_numbering, 'mutation_build': a.mutation_build, 'duplicate_of_id': a.duplicate_of_id,
            'error': a.error, 'cell_culture': a.cell_culture or '', 'status': a.status,
            'is_inferred': a.is_inferred, 'reference_id': a.reference_id,
            'doi': a.reference.doi if a.reference else None,
            'reference_status': a.reference.status if a.reference else None,
            'opsin_id': a.opsin_id, 'opsin_status': o.status if o else None,
            'protein_sequence_raw': raw_sequence,
            'protein_sequence': re.sub(r'\s+', '', raw_sequence).upper(),
            **{k: (getattr(o, k) or '') if o else '' for k in
               ('genus', 'species', 'gene_family', 'phylum', 'accession')}}


def inventory():
    return [assay_snapshot(a) for a in HeterologousData.objects.select_related('opsin', 'reference').order_by('pk')]


def valid_sequence(s):
    return isinstance(s, str) and 30 <= len(s) <= 2000 and re.fullmatch(f'[{AA}X]+', s) is not None


def family_hint(row):
    gene = row['gene_family'].lower().replace('-', '')
    if row['phylum'] == 'Chordata' and gene in {'rh1', 'rh2', 'sws1', 'sws2', 'uvs', 'sws', 'lws', 'mws', 'opn1sw'}:
        return 'C_OPSIN'
    if row['phylum'] in {'Arthropoda', 'Mollusca'} and gene in {
        'ivrh', 'ivrh1', 'ivrh6', 'rhmws', 'ivuvs', 'ivlws',
        'invrh1', 'brh1', 'prb', 'prv', 'rtc'}:
        return 'R_OPSIN'
    return 'OTHER'


def same_publication(one, two):
    if one['reference_id'] is not None and one['reference_id'] == two['reference_id']:
        return True
    doi = classify_identifier(one['doi'])['doi']
    return bool(doi and doi == classify_identifier(two['doi'])['doi'])


def base_accession(row):
    # Preserve accession namespace; only strip a recognizable substitution suffix/version.
    value = row['accession'].strip()
    value = re.sub(r'_[ACDEFGHIKLMNPQRSTVWY][1-9][0-9]*[ACDEFGHIKLMNPQRSTVWY](?:[,;_ ]+[ACDEFGHIKLMNPQRSTVWY][1-9][0-9]*[ACDEFGHIKLMNPQRSTVWY])*$', '', value)
    return re.sub(r'\.[0-9]+$', '', value)


def single_difference(wt, mutant):
    if not valid_sequence(wt) or not valid_sequence(mutant) or len(wt) != len(mutant):
        return None
    differences = [i + 1 for i, (a, b) in enumerate(zip(wt, mutant)) if a != b]
    return differences[0] if len(differences) == 1 else None


def comparator_pool(row, rows):
    # User-requested search across ALL publications: same species, no mutations,
    # accession first, then full sequences differing at exactly one amino acid.
    # A sequence match alone does not establish comparable measurement conditions.
    if not row['genus'] or not row['species']:
        return []
    taxon = (row['genus'].casefold().strip(), row['species'].casefold().strip())
    pool = []
    for w in rows:
        if w.get('duplicate_of_id') or w['is_inferred'] or mutation(w['mutations'])['kind'] != 'wild_type' or taxon != (w['genus'].casefold().strip(), w['species'].casefold().strip()):
            continue
        accession_match = bool(base_accession(row) and base_accession(row) == base_accession(w))
        if accession_match or single_difference(w['protein_sequence'], row['protein_sequence']) is not None:
            pool.append(w)
    return sorted(pool, key=lambda w: (base_accession(row) != base_accession(w),
                                      not same_publication(row, w), w['hetid']))


def read_supplement(path):
    if path is None:
        return {}
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        result = {}
        for row in csv.DictReader(handle):
            if str(row.get('hetid', '')).isdigit():
                key = int(row['hetid'])
                if key in result:
                    raise ValueError(f'Duplicate hetid {key} in supplement; resolve before extraction.')
                result[key] = {k.strip(): v for k, v in row.items() if k and k.strip() in
                               {'hetid', 'refid', 'Mutations', 'LambdaMax', 'Genus', 'Species',
                                'Accession', 'CellCulture', 'Purification', 'Spectrum', 'SourceType', 'Notes'}}
        return result


def supplementary(row, raw):
    data = raw.get(row['hetid'])
    if not data:
        return {'available': False}
    try:
        matches = (data['Mutations'] or '') == row['mutations'] and int(data['refid']) == row['reference_id'] \
            and float(data['LambdaMax']) == row['lambda_max'] \
            and (data['Genus'], data['Species'], data['Accession'], data['CellCulture']) == \
            (row['genus'], row['species'], row['accession'], row['cell_culture'])
    except (KeyError, ValueError, TypeError):
        matches = False
    return {'available': True, 'identity_matches': bool(matches), 'raw': data}


class AlignmentCache:
    """One target per alignment: results do not change with batch order/size."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.profile_path = Path(settings.BASE_DIR) / 'data/tuning/reference-profile.fasta'
        self.profile = read_alignment(self.profile_path.read_text())
        self.version = aligner_version(getattr(settings, 'VPOD_MAFFT_PATH', '/usr/local/bin/mafft'))
        self.identity = {'version': VERSION, 'profile_sha256': hashlib.sha256(self.profile_path.read_bytes()).hexdigest(),
                         'mafft_version': self.version, 'gap_penalties': ['1.53', '3.0']}

    def align(self, sequence):
        # Alignment arguments did not change in extraction v2; reuse validated v1 caches.
        key = digest([{**self.identity, 'version': 'single-sites-1'}, sequence])
        path = self.directory / (key + '.json')
        if path.exists():
            result = json.loads(path.read_text())
            if result.get('key') == key:
                self.validate(result['alignments'], sequence)
                return result['alignments']
        with tempfile.TemporaryDirectory(prefix='vpod-extract-') as folder:
            target = Path(folder) / 'target.fasta'
            target.write_text('>query\n' + sequence + '\n')
            alignments = [run_mafft(['--auto', '--inputorder', '--op', gap, '--addfragments', str(target),
                                     str(self.profile_path)], 20) for gap in ('1.53', '3.0')]
        self.validate(alignments, sequence)
        payload = {'key': key, 'alignments': alignments}
        with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, suffix='.tmp', delete=False) as handle:
            json.dump(payload, handle); temporary = Path(handle.name)
        temporary.replace(path)
        return alignments

    def validate(self, alignments, sequence):
        if len(alignments) != 2:
            raise MappingError('Invalid cached alignment count.')
        for alignment in alignments:
            if set(alignment) != set(self.profile) | {'query'} or len({len(s) for s in alignment.values()}) != 1:
                raise MappingError('Invalid cached alignment identifiers/lengths.')
            for k, seq in {**{k: s.replace('-', '') for k, s in self.profile.items()}, 'query': sequence}.items():
                if alignment[k].replace('-', '') != seq:
                    raise MappingError('Cached alignment changed an input sequence.')


def hypotheses(row, parsed, alignments):
    maps = [coordinate_maps(a) for a in alignments]
    result = []
    for scheme in PRIORITY:
        if scheme == 'human' and (row['genus'].lower(), row['species'].lower()) != ('homo', 'sapiens'):
            continue
        # Human means this human opsin's own sequence, not automatically human LWS.
        key = 'query' if scheme in {'human', 'self'} else scheme
        hits = []
        for mapping in maps:
            col = mapping[key][0].get(parsed['position'])
            hits.append(mapping['query'][1].get(col))
        stable = hits[0] is not None and hits[0] == hits[1]
        hit = hits[0] if stable else None
        matches = bool(hit and hit[1] in {parsed['from'], parsed['to']})
        result.append({'scheme': scheme, 'source_position': hit[0] if hit else None,
                       'residue': hit[1] if hit else None, 'stable': stable, 'residue_compatible': matches,
                       'meaning': 'This human opsin sequence (subtype-specific)' if scheme == 'human' else scheme,
                       'assessment': 'sequence-consistent hypothesis, not publication verification' if matches
                       else 'alignment disagreement/gap' if not stable else 'residue mismatch'})
    return result


def live_inputs(row, rows, raw):
    pool = comparator_pool(row, rows)
    return {'assay': row, 'comparators': pool,
            'supplement': {str(r['hetid']): supplementary(r, raw) for r in [row, *pool]}}


def analyze(row, rows, raw, cache, existing):
    inputs = live_inputs(row, rows, raw)
    parsed = mutation(row['mutations'])
    result = {'hetid': row['hetid'], 'notation': row['mutations'], 'kind': parsed['kind'],
              'organism': ' '.join(filter(None, [row['genus'], row['species']])), 'phylum': row['phylum'],
              'family_hint': family_hint(row), 'subtype': row['gene_family'], 'reference_id': row['reference_id'],
              'doi': row['doi'], 'inputs': inputs, 'algorithm': cache.identity,
              'fingerprint': digest([inputs, cache.identity]), 'hypotheses': [], 'options': [], 'sequence_diagnostics': [],
              'existing_evidence': existing.get(row['hetid'], []),
              'warnings': ['Legacy mutant sequences were reconstructed; residue agreement is not independent evidence of numbering.',
                           'Phylum/gene-family guidance is not a phylogenetic determination.']}
    if result['existing_evidence']:
        result['warnings'].append('Reviewed catalogue entries already exist; extraction will not replace or duplicate them.')
    if row.get('duplicate_of_id'):
        result['outcome'] = 'merged_duplicate'
        result['warnings'].append(f'Archived duplicate. Use retained heterologous record {row["duplicate_of_id"]}.')
        return result
    if row.get('mutation_build', {}).get('needs_sequence_review'):
        result['outcome'] = 'accession_repaired_sequence_needs_review'
        result['warnings'].append(row['mutation_build'].get('reason', 'Check the source sequence and mutation notation.'))
        return result
    if row['protein_sequence'] != row['protein_sequence_raw']:
        result['warnings'].append('Sequence whitespace/case normalized for comparison; the original text is retained in inputs.')
    if parsed['kind'] != 'substitution':
        result['outcome'] = 'indel_needs_review'
        result['warnings'].append('Insertion/deletion coordinates and comparison require primary-source review; no substitution is invented.')
        return result
    if not math.isfinite(row['lambda_max']) or row['lambda_max'] <= 0:
        result['outcome'] = 'missing_measured_wavelength'
        result['warnings'].append('A zero sentinel or nonfinite wavelength cannot establish a measured tuning shift.')
        return result
    if not valid_sequence(row['protein_sequence']):
        result['outcome'] = 'missing_or_invalid_sequence'; return result
    try:
        result['hypotheses'] = hypotheses(row, parsed, cache.align(row['protein_sequence']))
    except (MappingError, OSError, ValueError) as exc:
        result['outcome'] = 'alignment_failed'; result['warnings'].append(str(exc)); return result
    for wt in inputs['comparators']:
        a, b = wt['protein_sequence'], row['protein_sequence']
        differences = [{'position': i+1, 'wt': left, 'mutant': right} for i, (left, right) in enumerate(zip(a, b)) if left != right]
        reasons = []
        if a == b:
            reasons.append('WT and mutant sequences are identical; there is no stored single-residue change.')
        if wt['opsin_id'] == row['opsin_id']:
            reasons.append(f'Both assays link to Opsin {row["opsin_id"]}. Do not edit that shared sequence to create a mutant; rebuild this mutant from its WT.')
        if len(a) != len(b): reasons.append(f'Sequence lengths differ: WT {len(a)}, mutant {len(b)}.')
        if len(differences) > 1: reasons.append(f'{len(differences)} residue differences; a single substitution is required.')
        result['sequence_diagnostics'].append({'comparator_id': wt['hetid'], 'wt_length': len(a), 'mutant_length': len(b),
            'difference_count': len(differences), 'differences': differences[:20], 'reasons': reasons})
    for h in result['hypotheses']:
        if not h['residue_compatible']:
            continue
        position = h['source_position']
        for wt in inputs['comparators']:
            if not math.isfinite(wt['lambda_max']) or wt['lambda_max'] <= 0:
                continue
            a, b = wt['protein_sequence'], row['protein_sequence']
            if len(a) != len(b) or not valid_sequence(a):
                continue
            if single_difference(a, b) != position or a[position-1] != parsed['from'] or b[position-1] != parsed['to']:
                continue
            conflicts = []; condition_warnings = []
            if not wt['cell_culture'] or not row['cell_culture']:
                condition_warnings.append('Cell culture is missing.')
            elif wt['cell_culture'] != row['cell_culture']:
                condition_warnings.append('Cell cultures differ.')
            for field in ('Purification', 'Spectrum'):
                one, two = [inputs['supplement'][str(r['hetid'])] for r in (row, wt)]
                if all(s.get('identity_matches') for s in (one, two)):
                    v, w = one['raw'].get(field, ''), two['raw'].get(field, '')
                    if v and w and v != w:
                        condition_warnings.append(f'{field} differs: {v} / {w}.')
                    elif not v or not w:
                        condition_warnings.append(f'{field} is missing for one or both measurements.')
                else:
                    condition_warnings.append(f'{field} lacks a matching source snapshot.')
            if any(r['status'] != 'APPROVED' or r['reference_status'] != 'APPROVED' or r['opsin_status'] != 'APPROVED' for r in (wt, row)):
                conflicts.append('A source assay, opsin or reference is not approved.')
            if any(s.get('available') and not s.get('identity_matches') for s in
                   (inputs['supplement'][str(row['hetid'])], inputs['supplement'][str(wt['hetid'])])):
                condition_warnings.append('Legacy supplement identity differs from database; its condition labels were not used.')
            shift = round(row['lambda_max'] - wt['lambda_max'], 8)
            result['options'].append({'key': f'{h["scheme"]}:{wt["hetid"]}:{position}', 'scheme': h['scheme'],
                'comparator_id': wt['hetid'], 'source_position': position, 'baseline_nm': wt['lambda_max'],
                'mutant_nm': row['lambda_max'], 'candidate_shift_nm': shift,
                'comparator_reference_id': wt['reference_id'], 'cross_publication': not same_publication(wt, row),
                'match_basis': 'base_accession_and_one_residue' if base_accession(row) and base_accession(row) == base_accession(wt) else 'one_residue_same_species',
                'meets_cutoff': math.isfinite(shift) and abs(shift) >= 1, 'conflicts': conflicts,
                'condition_warnings': condition_warnings})
    # Prefer accession-supported sequence matches; within that tier prefer the
    # experiment's own publication. Keep every alternative visible for review.
    rank = lambda o: (o['match_basis'] != 'base_accession_and_one_residue', o['cross_publication'])
    preferred_options = [o for o in result['options'] if rank(o) == min(map(rank, result['options']))] if result['options'] else []
    wt_by_id = {w['hetid']: w for w in inputs['comparators']}
    pairs = {(wt_by_id[o['comparator_id']]['protein_sequence'], o['baseline_nm'], o['source_position']) for o in preferred_options}
    if len(pairs) == 1:
        preferred = preferred_options[0]
        result['suggested_selection'] = preferred['key']
        result['equivalent_comparator_ids'] = sorted({o['comparator_id'] for o in preferred_options})
        result['outcome'] = 'conditions_need_review' if preferred['conflicts'] else 'pair_ready_for_review' if preferred['meets_cutoff'] else 'below_cutoff'
    elif pairs:
        result['outcome'] = 'ambiguous_comparator_or_numbering'
    elif not inputs['comparators']:
        result['outcome'] = 'no_same_species_wt_match'
    else:
        result['outcome'] = 'sequence_or_numbering_disagreement'
    return result


def current_input_check(candidate):
    inputs = candidate.analysis['inputs']
    ids = [inputs['assay']['hetid'], *[r['hetid'] for r in inputs['comparators']]]
    actual = {a.pk: assay_snapshot(a) for a in HeterologousData.objects.filter(pk__in=ids).select_related('opsin', 'reference')}
    return all(actual.get(r['hetid']) == r for r in [inputs['assay'], *inputs['comparators']])


def validate_review(candidate):
    data = candidate.analysis
    options = [o for o in data.get('options', []) if o['key'] == candidate.selection]
    if data.get('existing_evidence') and not candidate.evidence_id:
        raise ValidationError('This assay already has reviewed evidence. Edit that evidence instead of duplicating it.')
    if candidate.stale or not current_input_check(candidate):
        raise ValidationError('Source records changed. Rerun build_tuning_candidates and review the new analysis.')
    if len(options) != 1:
        raise ValidationError('No valid WT–mutant comparison is selected. See Sequence diagnostics. Correct the source assay through its edit link, then run Repair selected mutant accessions on the candidate list. A numbering hypothesis alone is not a comparison.')
    option = options[0]
    if option['conflicts']:
        raise ValidationError('Resolve the listed source-data conflicts and re-extract before approval. Do not override them here.')
    if strict_conditions(candidate) and option.get('condition_warnings'):
        raise ValidationError('Strict condition matching is enabled: ' + ' '.join(option['condition_warnings']))
    if not option['meets_cutoff']:
        raise ValidationError('This comparison is below the 1 nm cutoff. Keep it as context; a literature-supported site can be curated separately.')
    if any(not math.isfinite(option[k]) or option[k] <= 0 for k in ('baseline_nm', 'mutant_nm')):
        raise ValidationError('Both measured wavelengths must be finite and positive; zero is a missing-value sentinel.')
    inputs = data['inputs']
    row = inputs['assay']
    wt = next((w for w in inputs['comparators'] if w['hetid'] == option['comparator_id']), None)
    parsed = mutation(row['mutations'])
    pos = option['source_position']
    if wt is None or parsed['kind'] != 'substitution' or single_difference(wt['protein_sequence'], row['protein_sequence']) != pos \
            or wt['protein_sequence'][pos-1] != parsed['from'] or row['protein_sequence'][pos-1] != parsed['to'] \
            or option['baseline_nm'] != wt['lambda_max'] or option['mutant_nm'] != row['lambda_max'] \
            or option['candidate_shift_nm'] != round(row['lambda_max']-wt['lambda_max'], 8) \
            or option['cross_publication'] != (not same_publication(wt, row)):
        raise ValidationError('Extracted comparison differs from its source snapshot. Re-extract.')
    if any(r['status'] != 'APPROVED' or r['reference_status'] != 'APPROVED' or r['opsin_status'] != 'APPROVED'
           or r['is_inferred'] or r.get('duplicate_of_id') or r.get('mutation_build', {}).get('needs_sequence_review') for r in (wt, row)) or mutation(wt['mutations'])['kind'] != 'wild_type':
        raise ValidationError('Only approved, non-inferred sources and an unmutated comparator can be published.')
    if candidate.evidence_id:
        e = candidate.evidence
        expected_changes = [{'position': pos, 'from': parsed['from'], 'to': parsed['to']}]
        expected_refs = {row['reference_id'], wt['reference_id']}
        if e.protein.sequence != wt['protein_sequence'] or e.changes != expected_changes \
                or (e.wild_type_assay_id, e.mutant_assay_id, e.baseline_nm, e.mutant_nm) != \
                (wt['hetid'], row['hetid'], wt['lambda_max'], row['lambda_max']) \
                or not expected_refs <= set(e.citations.values_list('reference_id', flat=True)):
            raise ValidationError('Linked Tuning evidence differs from this comparison. Reconcile it explicitly before re-approval; extraction will not overwrite curator values.')
    else:
        if TuningEvidence.objects.filter(key=f'heterologous-single-{row["hetid"]}').exists():
            raise ValidationError('A catalogue entry already uses this source key. Resolve the duplicate before approval.')
        protein = TuningProtein.objects.filter(key='source-'+hashlib.sha256(wt['protein_sequence'].encode()).hexdigest()[:32]).first()
        if protein and (protein.sequence != wt['protein_sequence'] or protein.status != 'APPROVED'):
            raise ValidationError('The existing source snapshot differs or is not approved. Its curator decision is preserved.')
    return option


@transaction.atomic
def publish_candidate(candidate, user=None, automatic=False):
    """Permission-protected curator action or explicitly applied maintainer extraction."""
    option = validate_review(candidate)
    policy = {'mode': 'AUTO' if automatic else 'MANUAL', 'strict_conditions': strict_conditions(candidate),
              'condition_warnings': option.get('condition_warnings', []), 'algorithm': candidate.analysis['algorithm'],
              'paper_review_claimed': bool(not automatic and candidate.numbering_confirmed and candidate.conditions_confirmed)}
    candidate.approval_mode = policy['mode']; candidate.approval_policy = policy
    if candidate.evidence_id:
        candidate.reviewed_fingerprint = candidate.fingerprint
        evidence = candidate.evidence
        evidence.conditions = {**evidence.conditions, 'approval': policy}
        evidence.approved_by = user
        evidence.save(update_fields=['conditions', 'approved_by', 'updated_at'])
        return
    data = candidate.analysis; inputs = data['inputs']; row = inputs['assay']
    wt = next(w for w in inputs['comparators'] if w['hetid'] == option['comparator_id'])
    sequence = wt['protein_sequence']; key = 'source-' + hashlib.sha256(sequence.encode()).hexdigest()[:32]
    protein, created = TuningProtein.objects.get_or_create(key=key, defaults={
        'name': f'{data["organism"]} {data["subtype"]} source snapshot', 'family': data['family_hint'],
        'subtype': data['subtype'] or 'Unclassified', 'sequence': sequence, 'accession': wt['accession'],
        'numbering_note': '1-based position in this exact comparator sequence. Published numbering is retained separately.',
        'provenance': {'kind': 'extracted_source', 'sequence_sha256': hashlib.sha256(sequence.encode()).hexdigest()},
        'status': 'APPROVED', 'approved_by': user})
    if protein.sequence != sequence or protein.status != 'APPROVED':
        raise ValidationError('Existing source snapshot differs or is not approved; curator decision preserved.')
    parsed = mutation(row['mutations'])
    locator = candidate.review_note or f'VPOD heterologous records {wt["hetid"]} (WT) and {row["hetid"]} (mutant); {VERSION} sequence comparison.'
    evidence = TuningEvidence(key=f'heterologous-single-{row["hetid"]}', protein=protein,
        title=f'{data["organism"]} {row["mutations"]} (heterologous {row["hetid"]})',
        family=data['family_hint'], subtype=data['subtype'] or 'Unclassified', organism=data['organism'], category='MEASURED',
        changes=[{'position': option['source_position'], 'from': parsed['from'], 'to': parsed['to']}],
        original_notation=row['mutations'], baseline_label=f'VPOD heterologous {wt["hetid"]}: {wt["accession"]}',
        wild_type_assay_id=wt['hetid'], mutant_assay_id=row['hetid'], baseline_nm=wt['lambda_max'], mutant_nm=row['lambda_max'],
        conditions={'numbering_scheme': option['scheme'], 'published_position': parsed['position'],
                    'source_position': option['source_position'], 'phylum': data['phylum'],
                    'cross_publication_comparison': option['cross_publication'], 'sequence_match_basis': option['match_basis'],
                    'supplementary_source_columns': {str(pk): data['inputs']['supplement'][str(pk)] for pk in (row['hetid'], wt['hetid'])},
                    'review_note': candidate.review_note, 'approval': policy,
                    'mutation_build': row.get('mutation_build', {}),
                    'original_assay_notation': data['inputs']['assay']['mutations']},
        notes='Database-derived comparator difference. ' + ('Automatically approved from an unambiguous sequence match. ' if automatic else 'Approved by a curator. ')
              + ('Measurement-condition equality was not required. ' if not strict_conditions(candidate) else 'Recorded condition labels match. ')
              + 'This is not a predicted effect in another protein.',
        source_locator=locator, release=VERSION, status='APPROVED', approved_by=user)
    evidence.full_clean(); evidence.save()
    TuningCitation.objects.create(evidence=evidence, reference_id=row['reference_id'], role='PRIMARY', locator=locator)
    if wt['reference_id'] != row['reference_id']:
        TuningCitation.objects.create(evidence=evidence, reference_id=wt['reference_id'], role='PRIMARY',
                                     locator='Comparator source. ' + locator)
    candidate.evidence = evidence
    candidate.reviewed_fingerprint = candidate.fingerprint
    candidate.approval_mode = policy['mode']; candidate.approval_policy = policy
    TuningAudit.objects.create(actor=f'admin:{user.pk}' if user else 'build_tuning_candidates:auto', object_type='TuningEvidence', object_key=evidence.key,
        before=None, after={'candidate': candidate.pk, 'fingerprint': candidate.fingerprint, 'option': option,
                           'protein_key': protein.key, 'reference_id': row['reference_id'], 'policy': policy}, reason=locator)


def automatic_eligibility(candidate):
    if candidate.decision != 'PENDING' or candidate.evidence_id:
        return 'existing_decision_preserved'
    if candidate.stale or candidate.review_note or candidate.reviewed_by_id:
        return 'curator_review_preserved'
    selection = candidate.analysis.get('suggested_selection')
    if not selection:
        return 'no_unambiguous_comparison'
    if candidate.selection and candidate.selection != selection:
        return 'curator_selection_preserved'
    candidate.selection = selection
    try:
        validate_review(candidate)
    except ValidationError as exc:
        return '; '.join(exc.messages)
    return 'eligible'


@transaction.atomic
def auto_approve(candidate):
    reason = automatic_eligibility(candidate)
    if reason != 'eligible':
        return reason
    try:
        publish_candidate(candidate, automatic=True)
    except ValidationError as exc:
        return 'validation_requires_review: ' + '; '.join(exc.messages)
    candidate.decision = 'APPROVED'; candidate.save()
    TuningAudit.objects.create(actor='build_tuning_candidates:auto', object_type='TuningCandidate', object_key=str(candidate.assay_id),
        before={'decision': 'PENDING'}, after={'decision': 'APPROVED', 'fingerprint': candidate.fingerprint,
            'selection': candidate.selection, 'policy': candidate.approval_policy}, reason='Automatic approval of an unambiguous eligible sequence comparison.')
    return 'approved'


@transaction.atomic
def store_analysis(data):
    before = None
    candidate, created = TuningCandidate.objects.get_or_create(assay_id=data['hetid'], defaults={
        'fingerprint': data['fingerprint'], 'analysis': data, 'outcome': data['outcome'],
        'selection': data.get('suggested_selection', '')})
    if created:
        action = 'created'
    elif candidate.fingerprint == data['fingerprint'] and candidate.analysis == data:
        return 'unchanged'
    else:
        action = 'updated_preserving_review'
        before = {'fingerprint': candidate.fingerprint, 'outcome': candidate.outcome}
        candidate.stale = candidate.decision != 'PENDING'
        candidate.numbering_confirmed = False; candidate.conditions_confirmed = False; candidate.cross_publication_confirmed = False
        candidate.fingerprint = data['fingerprint']; candidate.analysis = data; candidate.outcome = data['outcome']
        # Never silently select a different interpretation for the curator.
        candidate.save()
    TuningAudit.objects.create(actor='build_tuning_candidates', object_type='TuningCandidate', object_key=str(data['hetid']),
        before=before, after={'fingerprint': data['fingerprint'], 'outcome': data['outcome'], 'action': action},
        reason='Offline extraction; approval, when eligible and enabled, is audited separately.')
    return action
