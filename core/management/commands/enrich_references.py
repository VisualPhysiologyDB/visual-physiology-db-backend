"""Complete-inventory enrichment, auditable field updates, and offline artifact replay."""
import json
import os
from collections import defaultdict
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils.dateparse import parse_datetime
from core.models import Reference, ReferenceMetadataAudit
from core.bibliography import classify_identifier, normalize_methods
from core.metadata_recovery import MetadataClient, recover, digest, now

FIELDS = ('doi', 'title', 'publication_date', 'online_date', 'print_date', 'year_of_publication', 'source_url', 'raw_citation', 'identifier_kind', 'mom_raw', 'measurement_methods')
INVENTORY_FIELDS = ('refid', *FIELDS, 'notes', 'status', 'source_data')


def completeness():
    rows = list(Reference.objects.values(*FIELDS))
    return {'records': len(rows), **{field: sum(r[field] not in (None, '', [], {}) for r in rows) for field in FIELDS}, 'syntactic_dois': sum(bool(classify_identifier(r['doi'])['doi']) for r in rows)}


class Command(BaseCommand):
    help = 'Recover all reference metadata. Default writes a review artifact only; --apply writes audited corrections.'

    def add_arguments(self, parser):
        parser.add_argument('--output', required=True)
        parser.add_argument('--cache', default='.cache/vpod-metadata')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--from-artifact')
        parser.add_argument('--seed-inventory', action='store_true', help='With --from-artifact: create missing legacy inventory IDs; refuses occupied ID mismatches')
        parser.add_argument('--retry-failures', action='store_true')
        parser.add_argument('--offline', action='store_true')
        parser.add_argument('--timeout', type=float, default=20)
        parser.add_argument('--retries', type=int, default=2)
        parser.add_argument('--interval', type=float, default=1.05)
        parser.add_argument('--mailto', default=os.environ.get('METADATA_MAILTO'))

    def handle(self, *args, **opt):
        if opt['apply'] and opt['dry_run']:
            raise CommandError('Choose --apply or --dry-run, not both')
        if not 0 < opt['timeout'] <= 60 or not 0 <= opt['retries'] <= 5:
            raise CommandError('Timeout must be 0..60 seconds and retries 0..5')
        if opt['seed_inventory'] and not opt['from_artifact']:
            raise CommandError('--seed-inventory requires --from-artifact')
        source = json.loads(Path(opt['from_artifact']).read_text()) if opt['from_artifact'] else None
        if source and source.get('schema_version') != 1:
            raise CommandError('Unsupported artifact schema')
        run_id = now()
        report = {'schema_version': 1, 'run_id': run_id, 'applied': opt['apply'], 'database_engine': connection.vendor, 'before': completeness(), 'records': [], 'audit': [], 'replay_conflicts': []}
        if source and opt['seed_inventory']:
            with transaction.atomic():
                for row in source['inventory']:
                    values = {k: v for k, v in row.items() if k in INVENTORY_FIELDS}
                    ref = Reference.objects.filter(pk=values['refid']).first()
                    if ref is not None and ref.doi != values['doi'] and ref.raw_citation != values['doi']:
                        raise CommandError(f'Inventory ID {ref.pk} already occupied by different identifier; no records seeded')
                    if ref is None and opt['apply']:
                        Reference.objects.create(**values)
                if opt['apply']:
                    with connection.cursor() as cursor:
                        for sql in connection.ops.sequence_reset_sql(no_style(), [Reference]):
                            cursor.execute(sql)
        report['inventory'] = list(Reference.objects.order_by('pk').values(*INVENTORY_FIELDS))
        if source:
            self.replay_references = {}
            # Offline replay can commit as a single unit; avoid thousands of
            # SQLite fsyncs and leave no half-applied artifact on validation errors.
            with transaction.atomic():
                for entry in source['audit']:
                    self.replay(entry, run_id, report, opt['apply'])
            report['records'] = source['records']
        else:
            client = MetadataClient(opt['cache'], interval=opt['interval'], timeout=opt['timeout'], retries=opt['retries'], retry_failures=opt['retry_failures'], mailto=opt['mailto'], offline=opt['offline'])
            for ref in Reference.objects.order_by('pk').iterator():
                result = recover(ref, client)
                report['records'].append(result)
                # Network has completed; commit each reference and its audits together.
                with transaction.atomic():
                    identity = classify_identifier(ref.doi)
                    original_identifier = ref.doi
                    changes = {}
                    if original_identifier and identity['kind'] != 'doi':
                        changes.update(raw_citation=ref.raw_citation or original_identifier, doi=None)
                    elif original_identifier and original_identifier != identity['doi']:
                        changes.update(raw_citation=ref.raw_citation or original_identifier, doi=identity['doi'])
                    if identity['source_url']:
                        changes['source_url'] = identity['source_url']
                    changes['identifier_kind'] = identity['kind']
                    if result['classification'] == 'disputed':
                        changes.update(raw_citation=ref.raw_citation or original_identifier, doi=None, identifier_kind='disputed')
                    if ref.mom_raw:
                        changes['measurement_methods'] = normalize_methods(ref.mom_raw)
                    for field, value in changes.items():
                        self.propose(ref, field, value, 'VPOD classifier', 'references.csv' if ref.source_data else f'vpod:reference/{ref.pk}', run_id, 'classified', 'Conservative source classification; raw identifier preserved', run_id, report, opt['apply'], repair=field in {'doi', 'identifier_kind'})
                    # Apply verified DOI last, without losing the original expected value on a dry run.
                    for field, value in result['fields'].items():
                        if value is not None:
                            self.propose(ref, field, value, result['provider'], result['evidence_url'], result['retrieved_at'], 'verified', result['reason'], run_id, report, opt['apply'], repair=field == 'doi' and identity['kind'] != 'doi')
                    if result['candidates'] and not result['fields']:
                        self.propose(ref, 'doi', result['candidates'], 'Crossref', (result['attempts'][-1]['url'] if result['attempts'] else ''), (result['attempts'][-1]['retrieved_at'] if result['attempts'] else run_id), 'candidate', result['reason'], run_id, report, opt['apply'])
                # Each row is durable in the audit table; bound report I/O while
                # the HTTP cache keeps an interrupted run resumable.
                if len(report['records']) % 25 == 0:
                    self.write_report(opt['output'], report)
                if len(report['records']) % 50 == 0:
                    self.stdout.write(f'Processed {len(report["records"])} reference records', ending='\n')
        groups = defaultdict(list)
        for ref in Reference.objects.order_by('pk'):
            doi = classify_identifier(ref.doi)['doi']
            if doi:
                groups[doi].append(ref.pk)
        report['duplicate_dois'] = [{'doi': d, 'refids': ids} for d, ids in groups.items() if len(ids) > 1]
        report['after'] = completeness()
        report['summary'] = {'processed': len(report['records']), 'verified_records': sum(bool(r['fields']) for r in report['records']), 'unresolved_records': sum(not r['fields'] for r in report['records']), 'conflict_fields': sum(a['decision'] == 'conflict' for a in report['audit']), 'duplicate_groups': len(report['duplicate_dois']), 'verified_doi_recoveries': sum(r['classification'] != 'doi' and bool(r['fields'].get('doi')) for r in report['records'])}
        self.write_report(opt['output'], report)
        self.stdout.write(json.dumps(report['summary']))

    def write_report(self, path, report):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        temporary.replace(target)

    def propose(self, ref, field, value, provider, url, retrieved_at, decision, reason, run_id, report, apply, repair=False):
        current = getattr(ref, field)
        if current == value:
            return
        curated = ref.metadata_audits.filter(field=field, decision='curator').exists()
        if decision != 'candidate' and (curated or (current not in (None, '', [], {}) and not repair)):
            decision, reason = 'conflict', reason + '; existing value protected'
        entry = {'refid': ref.pk, 'field': field, 'original_value': current, 'recovered_value': value,
                 'provider': provider, 'evidence_url': url, 'retrieved_at': retrieved_at, 'decision': decision, 'reason': reason,
                 'identity_guard': {'doi': ref.doi, 'raw_citation': ref.raw_citation}}
        entry['fingerprint'] = digest(entry)
        can_apply = decision in {'verified', 'classified'}
        entry['accepted'] = can_apply
        report['audit'].append(entry)
        # Advance the in-memory reference even on dry runs so replay has a sequential, exact old-value chain.
        if can_apply:
            setattr(ref, field, value)
        if apply:
            with transaction.atomic():
                if can_apply:
                    ref.save(update_fields=[field, 'updated_at'])
                self.persist(entry, run_id, can_apply)

    def persist(self, entry, run_id, applied):
        ReferenceMetadataAudit.objects.get_or_create(fingerprint=entry['fingerprint'], defaults={
            'reference_id': entry['refid'], **{k: entry[k] for k in ('field', 'original_value', 'recovered_value', 'provider', 'evidence_url', 'decision', 'reason')},
            'retrieved_at': parse_datetime(entry['retrieved_at']), 'run_id': run_id, 'applied': applied,
        })

    def replay(self, entry, run_id, report, apply):
        ref = self.replay_references.get(entry['refid']) or Reference.objects.filter(pk=entry['refid']).first()
        if ref:
            self.replay_references[ref.pk] = ref
        if not ref:
            report['replay_conflicts'].append({'refid': entry['refid'], 'reason': 'ID absent; seed inventory on an empty fresh database first'})
            return
        if entry['field'] not in FIELDS:
            raise CommandError('Artifact contains unsupported field')
        if not entry['accepted']:
            report['audit'].append(entry)
            if apply:
                self.persist(entry, run_id, False)
            return
        current = getattr(ref, entry['field'])
        if current == entry['recovered_value']:
            return
        # Field + identity guards prevent applying corrections to an unrelated or edited reference.
        guard = entry['identity_guard']
        if current != entry['original_value'] or (ref.doi != guard['doi'] or ref.raw_citation != guard['raw_citation']) or ref.metadata_audits.filter(field=entry['field'], decision='curator').exists():
            report['replay_conflicts'].append({'refid': ref.pk, 'field': entry['field'], 'current': current, 'reason': 'Old value, identity or curator guard failed'})
            return
        try:
            Reference._meta.get_field(entry['field']).clean(entry['recovered_value'], ref)
        except ValidationError as exc:
            report['replay_conflicts'].append({'refid': ref.pk, 'field': entry['field'], 'reason': 'Invalid artifact field value: ' + str(exc)})
            return
        report['audit'].append(entry)
        setattr(ref, entry['field'], entry['recovered_value'])
        if apply:
            with transaction.atomic():
                ref.save(update_fields=[entry['field'], 'updated_at'])
                self.persist(entry, run_id, True)
