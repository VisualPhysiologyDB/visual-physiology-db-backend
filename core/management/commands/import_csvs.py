"""Conservative curated legacy imports: create missing rows, preserve existing edits."""
import csv
import json
from pathlib import Path
from collections import Counter
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection
from django.conf import settings
from core.models import Reference, Opsin, HeterologousData, CuratedSCP
from core.bibliography import classify_identifier, normalize_methods
from core.source_references import CURATED_SCP_SOURCE_DATASET, ensure_source_publication_references


def number(value):
    return float(value) if str(value or '').strip() else None


def integer(value):
    val = number(value)
    if val is not None and not val.is_integer():
        raise ValueError('Nonintegral identifier/year')
    return int(val) if val is not None else None


class Command(BaseCommand):
    help = 'Import curated legacy CSVs without overwriting existing records or moderation decisions.'

    def add_arguments(self, parser):
        parser.add_argument('csv_dir')
        parser.add_argument('--references-only', action='store_true')
        parser.add_argument('--report', default='import-report.json')

    def handle(self, *args, **options):
        root = Path(options['csv_dir'])
        report = {'policy': 'create-only; references fill missing source provenance once; preserve normalized metadata and status', 'files': {}}
        names = ['references.csv'] if options['references_only'] else ['references.csv', 'opsins.csv', 'heterologous.csv', 'curated_scp.csv']
        for name in names:
            counts, outcomes = Counter(), []
            path = root / name
            if not path.exists():
                report['files'][name] = {'error': 'file not found'}
                continue
            with path.open(encoding='utf-8-sig', newline='') as handle:
                for line, row in enumerate(csv.DictReader(handle), 2):
                    try:
                        outcome, detail = self.import_row(name, row)
                    except (ValueError, TypeError, KeyError) as exc:
                        outcome, detail = 'unresolved', str(exc)
                    counts[outcome] += 1
                    outcomes.append({'line': line, 'outcome': outcome, 'detail': detail})
            report['files'][name] = {'counts': dict(counts), 'rows': outcomes}
        # Explicit primary keys must also advance PostgreSQL sequences.
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [Reference, Opsin, HeterologousData, CuratedSCP]):
                cursor.execute(sql)
        # Imported mutant rows may carry the WT's base accession. Construct their
        # own protein after all WT rows exist; never leave a mutant linked to WT.
        new_mutants = [r['detail']['id'] for r in report['files'].get('heterologous.csv', {}).get('rows', [])
                       if r['outcome'] == 'imported' and isinstance(r['detail'], dict)]
        if new_mutants:
            from core.tuning_mutations import repair_inventory, apply_source_corrections
            from core.tuning_repairs import merge_duplicates, synchronize_merged_accessions
            # Apply the shipped, fingerprint-guarded legacy repairs to NEW rows
            # before a bare accession is split from WT. Reimports leave old rows alone.
            patch = Path(settings.BASE_DIR) / 'data/tuning/source-corrections.json'
            if patch.exists():
                new_opsins = [r['detail']['id'] for r in report['files'].get('opsins.csv', {}).get('rows', [])
                              if r['outcome'] == 'imported' and isinstance(r['detail'], dict)]
                allowed = {('HeterologousData', pk) for pk in new_mutants} | {('Opsin', pk) for pk in new_opsins}
                report['reviewed_source_corrections'] = apply_source_corrections(patch, apply=True, actor='import_csvs', only_ids=allowed)
                report['reviewed_duplicate_merges'] = merge_duplicates(patch, apply=True, actor='import_csvs', only_source_ids=new_mutants)
            built = repair_inventory(apply=True, assay_ids=new_mutants, actor='import_csvs')
            synchronize_merged_accessions(actor='import_csvs')
            report['mutation_construction'] = {'counts': built['counts'], 'rows': [
                {k: p.get(k) for k in ('hetid', 'outcome', 'action', 'reason', 'numbering')} for p in built['rows']]}
        sources = ensure_source_publication_references()
        report['source_publications'] = {k: r.pk for k, r in sources.items()}
        Path(options['report']).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        self.stdout.write(json.dumps({k: v.get('counts', v) for k, v in report['files'].items()}))

    def import_row(self, name, row):
        if name == 'references.csv':
            refid = integer(row['refid'])
            if not refid:
                raise ValueError('Missing reference ID')
            invalid_year = None
            try:
                year = integer(row.get('YOP'))
            except ValueError:
                year = None
                invalid_year = row.get('YOP')
            obj, created = Reference.objects.get_or_create(refid=refid, defaults={
                'doi': row.get('DOI') or None, 'year_of_publication': year,
                'notes': row.get('notes') or None, 'status': 'APPROVED',
                'mom_raw': row.get('MOM') or None, 'measurement_methods': normalize_methods(row.get('MOM')),
                'source_data': row,
            })
            if created:
                return 'imported', {'refid': refid, 'invalid_year_raw': invalid_year}
            # Populate source provenance only when the ID also agrees with the identifier.
            supplied, existing = classify_identifier(row.get('DOI')), classify_identifier(obj.doi)
            same = (obj.doi or '').strip() == (row.get('DOI') or '').strip() or bool(supplied['doi'] and supplied['doi'] == existing['doi'])
            if not obj.source_data and same:
                obj.source_data = row
                if not obj.mom_raw:
                    obj.mom_raw = row.get('MOM') or None
                if not obj.measurement_methods:
                    obj.measurement_methods = normalize_methods(obj.mom_raw)
                obj.save()
                return 'updated', {'refid': refid, 'invalid_year_raw': invalid_year}
            if not obj.source_data and not same:
                return 'unresolved', {'refid': refid, 'reason': 'Existing identifier differs; preserved database record', 'csv_identifier': row.get('DOI'), 'database_identifier': obj.doi}
            return 'unchanged', {'refid': refid, 'invalid_year_raw': invalid_year}
        refid = integer(row.get('refid'))
        ref = Reference.objects.filter(pk=refid).first() if refid else None
        details = {'reference_id': refid, 'reference_unresolved': bool(refid and not ref)}
        if name == 'opsins.csv':
            model, key = Opsin, {'opsinid': integer(row['opsinid'])}
            values = {k: row.get(v) or None for k, v in {'gene_family': 'GeneFamily', 'phylum': 'Phylum', 'genus': 'Genus', 'species': 'Species', 'accession': 'Accession', 'dna_sequence': 'DNA', 'protein_sequence': 'Protein'}.items()}
        elif name == 'heterologous.csv':
            model, key = HeterologousData, {'hetid': integer(row['hetid'])}
            from core.tuning_repairs import source_protein
            opsin, linkage = source_protein(row)
            details['opsin_unresolved'] = opsin is None
            details['opsin_linkage'] = linkage
            values = {'opsin': opsin, 'mutations': row.get('Mutations') or None, 'lambda_max': number(row.get('LambdaMax')), 'error': number(row.get('error')), 'cell_culture': row.get('CellCulture') or None}
            if values['lambda_max'] is None:
                raise ValueError('Missing lambda_max; no zero sentinel invented')
        else:
            record_id = str(integer(row.get('scpid') or row.get('maxid')))
            model, key = CuratedSCP, {'scpid': int(record_id)}
            values = {k: row.get(v) or None for k, v in {'genus': 'Genus', 'species': 'Species', 'phylum': 'Phylum', 'photoreceptor_type': 'CellType', 'cell_subtype': 'CellSubType', 'chromophore': 'Chromophore', 'notes': 'Notes'}.items()}
            values.update(lambda_max=number(row.get('LambdaMax')), error=number(row.get('error')), source_dataset=CURATED_SCP_SOURCE_DATASET, source_record_id=record_id)
        existing = model.objects.filter(**key).first()
        if existing:
            details['id'] = existing.pk
            return 'unchanged', details
        values.update(reference=ref, status='APPROVED')
        obj = model(**key, **values)
        # Report invalid rows instead of aborting the rest of an import.
        from django.core.exceptions import ValidationError
        try:
            obj.full_clean()
        except ValidationError as exc:
            return 'unresolved', {'id': key, 'errors': exc.message_dict}
        obj.save()
        details['id'] = obj.pk
        return 'imported', details
