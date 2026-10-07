"""SQLite-only test entry point; never changes deployment settings or its database."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','vpod_backend.settings')
# Explicit isolated settings; Django creates/destroys its own in-memory test DB.
os.environ['VPOD_DATABASE']='sqlite'
os.environ['VPOD_SQLITE_PATH']='/tmp/vpod-tuning-test-base.sqlite3'
os.environ['VPOD_LOG_PATH']='/tmp/vpod-tuning-tests.log'
os.environ['VPOD_TUNING_SITE_INDEX_PATH']='/tmp/vpod-tuning-tests-site-index.json'
import vpod_backend.settings as config
config.DATABASES={'default':config.DATABASES['default']}
import django
django.setup()
from django.core.management import call_command
call_command('test',*(sys.argv[1:] or ['core.test_tuning_sites','core.test_mutation_repair','core.test_tuning_extraction','core.test_tuning','core.test_vpod','core.test_discovery']),verbosity=1)
call_command('makemigrations',check=True,dry_run=True)
