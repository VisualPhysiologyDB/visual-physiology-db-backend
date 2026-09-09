import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import VisualAcuity, Reference
from core.validators import positive_finite
from django.core.exceptions import ValidationError

SOURCE = 'visual_acuity_legacy'
COLUMNS = {'BL (cm)': 'body_length_cm', 'Δϕ (deg)': 'interommatidial_angle_deg', 'Δρ (deg)': 'acceptance_angle_deg', 'CPD': 'cpd', 'Lens Diameter (mm)': 'lens_diameter_mm'}
REQUIRED_COLUMNS = {'acuID', 'Genus', 'Species', 'Eye Type', 'FellerRefID', 'refid', 'Notes', *COLUMNS}


def parse_taxonomy(row):
    genus, species = row['Genus'].strip(), row['Species'].strip()
    flags = []
    if not genus:
        match = re.fullmatch(r'([A-Z][a-z]+) ([a-z][a-z-]+)', species)
        if match:
            genus, species = match.groups()
        else:
            genus, species = None, None
            flags.append('taxonomy_unresolved: inspect raw Genus/Species')
    elif not re.fullmatch(r'[A-Z][a-z]+', genus) or not re.fullmatch(r'[a-z][a-z-]+', species):
        flags.append('taxonomy_unverified: supplied spelling retained')
    return genus or None, species or None, flags


class Command(BaseCommand):
    help = 'Import all acuity rows with stable source IDs and three-way protection of curator edits.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--report', default='acuity-import-report.json')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        path = Path(options['csv_path'])
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
                raise CommandError('Missing required source headers')
            rows = list(reader)
        ids = Counter(row['acuID'].strip() for row in rows)
        report = {'source_dataset': SOURCE, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'total': len(rows), 'dry_run': options['dry_run'], 'rows': []}
        counts = Counter()
        with transaction.atomic():
            for line, row in enumerate(rows, 2):
                source_id = row['acuID'].strip()
                if not source_id or ids[source_id] != 1:
                    result = {'line': line, 'source_record_id': source_id, 'outcome': 'unresolved', 'flags': ['Missing or duplicate acuID'], 'raw': row}
                else:
                    result = self.import_row(row, source_id)
                    result['line'] = line
                counts[result['outcome']] += 1
                report['rows'].append(result)
            if options['dry_run']:
                transaction.set_rollback(True)
        report['counts'] = dict(counts)
        Path(options['report']).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        self.stdout.write(json.dumps({'total': len(rows), 'counts': counts}))

    def import_row(self, row, source_id):
        genus, species, flags = parse_taxonomy(row)
        values = {'genus': genus, 'species': species, 'eye_type': row['Eye Type'].strip() or None, 'feller_ref_id': row['FellerRefID'].strip() or None, 'notes': row['Notes'] or None}
        for column, field in COLUMNS.items():
            raw = row[column].strip()
            value = None
            if raw:
                try:
                    value = float(raw)
                    positive_finite(value)
                except (ValueError, ValidationError):
                    flags.append(f'invalid_numeric: {column}={raw!r}; raw retained')
                    value = None
            values[field] = value
        if values['cpd'] is None:
            flags.append('cpd_missing: excluded from CPD histogram')
        ref = Reference.objects.filter(pk=int(row['refid'])).first() if row['refid'].strip().isdigit() else None
        values['reference_id'] = ref.pk if ref else None
        if not ref:
            flags.append(f'unresolved_reference: {row["refid"]}')
        flags.append('measurement_protocol_unverified: source CPD retained; no angular conversion')
        obj = VisualAcuity.objects.filter(source_dataset=SOURCE, source_record_id=source_id).first()
        conflicts = []
        if obj is None:
            obj = VisualAcuity(source_dataset=SOURCE, source_record_id=source_id, status='APPROVED', **values)
            outcome = 'imported'
        else:
            outcome = 'unchanged'
            for field, proposed in values.items():
                current = getattr(obj, field)
                previous = obj.import_baseline.get(field)
                if current != proposed:
                    if field in obj.import_baseline and current == previous:
                        setattr(obj, field, proposed)
                        outcome = 'updated'
                    else:
                        conflicts.append({'field': field, 'current': current, 'incoming': proposed, 'reason': 'curator edit or no baseline; preserved'})
            # Moderation status and approvals are never imported on existing rows.
        if obj.source_data != row or obj.quality_flags != flags:
            if outcome == 'unchanged':
                outcome = 'updated'
        obj.source_data, obj.import_baseline, obj.quality_flags = row, values, flags
        if outcome != 'unchanged':
            obj.save()
        return {'source_record_id': source_id, 'outcome': outcome, 'reference_id': obj.reference_id, 'flags': flags, 'conflicts': conflicts}
