"""Legacy command name retained; duplicates are now linked, never deleted."""
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import CuratedSCP
from core.scp_duplicates import duplicate_groups


class Command(BaseCommand):
    help = 'Report same-publication MSP/SCP repeats. --commit retains and hides duplicates; never deletes records.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Link retained duplicates to the primary record; no deletion')
        parser.add_argument('--report', required=True, help='JSON review report')

    @transaction.atomic
    def handle(self, *args, **options):
        report = {'applied': options['commit'], 'groups': [], 'deleted': 0, 'newly_linked': 0}
        for rows in duplicate_groups(CuratedSCP.objects.all()):
            keeper = rows[0]
            detail = {'primary_id': keeper.pk, 'genus': keeper.genus, 'species': keeper.species,
                      'lambda_max': keeper.lambda_max, 'records': []}
            for row in rows:
                detail['records'].append({'scpid': row.pk, 'reference_id': row.reference_id,
                    'doi': row.reference.doi if row.reference else None, 'status': row.status,
                    'source_dataset': row.source_dataset, 'source_record_id': row.source_record_id,
                    'duplicate_of_before': row.duplicate_of_id})
            changes = [r.pk for r in rows[1:] if r.duplicate_of_id != keeper.pk]
            report['newly_linked'] += len(changes)
            if options['commit']:
                CuratedSCP.objects.filter(pk__in=changes).update(duplicate_of=keeper)
                CuratedSCP.objects.filter(pk=keeper.pk).update(duplicate_of=None)
            report['groups'].append(detail)
        report['group_count'] = len(report['groups'])
        report['retained_duplicate_rows'] = sum(len(g['records']) - 1 for g in report['groups'])
        Path(options['report']).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k != 'groups'}))
