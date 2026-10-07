from django.core.management.base import BaseCommand
from core.tuning_extraction import AlignmentCache
from core.tuning_sites import write_index
from core.tuning_views import public_evidence
import json


class Command(BaseCommand):
    help = 'Build the reusable public-protein site index offline. Writes a derived JSON file; no database edits.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Default: VPOD_TUNING_SITE_INDEX_PATH.')
        parser.add_argument('--cache-dir', default='var/tuning-extraction-cache')

    def handle(self, *args, **options):
        proteins = {e.protein_id: e.protein for e in public_evidence()}
        result = write_index(proteins.values(), AlignmentCache(options['cache_dir']), options['output'])
        self.stdout.write(json.dumps(result))
