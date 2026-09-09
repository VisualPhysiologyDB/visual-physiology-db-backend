"""Preview by default; --apply writes only the private review queue and checkpoints."""
import fcntl
import html
import json
import time
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from core.literature_discovery import (read_config, due, initial_checkpoint, search_url, parse_page,
    metadata_for, relevance, inspect_candidate, ingest, identity_values)
from core.metadata_recovery import MetadataClient, digest
from core.models import DiscoveryRun, LiteratureCandidate


class BudgetReached(Exception):
    pass


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
    rows = []
    for row in report['records']:
        if row['outcome'] == 'filtered':
            continue  # Keep the readable review short; every screened row remains in JSON.
        m = row.get('metadata', {})
        link = m.get('source_url', '')
        # Links are constructed by our adapters. Escape everything again at the HTML boundary.
        title = html.escape(m.get('title', '(invalid provider record)'))
        title = f'<a href="{html.escape(link, quote=True)}" rel="noopener noreferrer">{title}</a>' if link else title
        rows.append('<tr>' + ''.join(f'<td>{v}</td>' for v in (html.escape(row['outcome']), title,
            html.escape(str(m.get('year_of_publication') or 'Unknown')), html.escape(row.get('query', '')),
            html.escape(row.get('reason', '')), html.escape(json.dumps(row.get('matches', []))))) + '</tr>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPOD literature preview</title><style>body{font:16px system-ui;margin:2rem;line-height:1.5}table{border-collapse:collapse;width:100%}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}pre{white-space:pre-wrap}.scroll{overflow:auto}</style>
<h1>VPOD literature discovery</h1><p>Suggestions for human review. Nothing in this report is automatically published or a verified measurement. Filtered-out rows are omitted here and retained in the JSON report.</p>'''
    page += '<pre>' + html.escape(json.dumps({k: report[k] for k in ('mode', 'status', 'started_at', 'counts', 'limitations', 'errors')}, indent=2)) + '</pre>'
    page += '<div class="scroll"><table><thead><tr><th>Outcome</th><th>Paper</th><th>Year</th><th>Search</th><th>Reason</th><th>Existing / possible matches</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div></html>'
    path.with_suffix('.html').write_text(page, encoding='utf-8')


class Command(BaseCommand):
    help = 'Discover literature for curator review. Defaults to a preview with no database changes.'

    def add_arguments(self, parser):
        parser.add_argument('--config', default='config/literature.json')
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument('--apply', action='store_true', help='Write private inbox, evidence and checkpoints; never publish.')
        modes.add_argument('--dry-run', action='store_true', help='Explicit preview (also the default).')
        parser.add_argument('--if-due', action='store_true', help='Obey enabled and interval_days; intended for the daily timer.')
        parser.add_argument('--state-dir', default='var/discovery', help='Disposable HTTP cache and reports, not review decisions.')
        parser.add_argument('--output', help='JSON report path; also writes a readable .html report alongside it.')
        parser.add_argument('--offline', action='store_true', help='Use today\'s cached responses only.')

    def handle(self, *args, **options):
        try:
            config = read_config(Path(options['config']))
        except (ValueError, OSError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        if options['if_due'] and not config['enabled']:
            self.stdout.write('Skipped: enabled is false. No searches or database changes.')
            return
        today = timezone.now().date()
        # One lock per database on this host, independent of output/cache options.
        lock_dir = Path(settings.BASE_DIR) / 'var' / 'discovery-locks'
        lock_dir.mkdir(parents=True, exist_ok=True)
        identity = {k: connection.settings_dict.get(k) for k in ('ENGINE', 'NAME', 'HOST', 'PORT')}
        with (lock_dir / (digest(identity) + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError('Another discovery command is running against this database.') from exc
            if options['if_due'] and not due(config, today):
                self.stdout.write('Skipped: next search is not due yet.')
                return
            self.run_search(config, today, options)

    def run_search(self, config, today, options):
        started = time.monotonic()
        stamp = timezone.now()
        run_id = str(uuid.uuid4())
        apply = options['apply']
        if apply:
            # A process holding this database lock cannot coexist with another local runner.
            DiscoveryRun.objects.filter(status='RUNNING').update(status='INTERRUPTED', finished_at=stamp)
        run = DiscoveryRun.objects.create(id=run_id, config_hash=digest(config), configuration=config) if apply else None
        end = today - timedelta(days=1)
        state = Path(options['state_dir'])
        client = MetadataClient(state / 'cache' / today.isoformat(), interval=config['request_interval_seconds'],
                                timeout=20, retries=2, retry_failures=True, offline=options['offline'])
        client.session.headers['User-Agent'] = 'VPOD-literature-discovery/1.0'
        counts = Counter()
        actual_get = client.session.get
        def bounded_get(*args, **kwargs):
            if counts['http_attempts'] >= config['max_requests'] or time.monotonic() - started >= config['max_runtime_seconds']:
                raise BudgetReached('HTTP/time budget reached; unfinished checkpoints retained')
            counts['http_attempts'] += 1
            return actual_get(*args, **kwargs)
        client.session.get = bounded_get
        report = {'run_id': run_id, 'mode': 'apply' if apply else 'preview', 'started_at': stamp.isoformat(),
                  'configuration': config, 'config_hash': digest(config), 'window_end': str(end),
                  'records': [], 'errors': [], 'checkpoints': [], 'limitations': []}
        # Round robin across providers/queries prevents a broad search from consuming every page first.
        streams = [(p, q, initial_checkpoint(p, q, config, end)) for q in config['queries'] for p in config['providers']]
        preview_seen = set()
        awaiting = LiteratureCandidate.objects.filter(decision__in=['NEW', 'DEFERRED']).count()
        stop = False
        try:
            while streams and not stop:
                next_streams = []
                for provider, query, checkpoint in streams:
                    if counts['new'] >= config['max_new_candidates'] or awaiting + counts['new'] >= config['max_unreviewed']:
                        report['limitations'].append('Inbox/new-candidate cap reached. Review the inbox or raise the configured cap; remaining pages will resume.')
                        stop = True
                        break
                    if time.monotonic() - started >= config['max_runtime_seconds'] or counts['pages'] >= config['max_requests']:
                        report['limitations'].append('Page/time budget reached; unfinished checkpoints retained.')
                        stop = True
                        break
                    url = ''
                    page_done = True
                    try:
                        if apply and not checkpoint.pk:
                            # Freeze the initial window even if its first HTTP request fails.
                            checkpoint.save()
                        url, query_text = search_url(provider, query, checkpoint.window_start, checkpoint.window_end, checkpoint.position, config['page_size'])
                        response = client.get(url)
                        counts['pages'] += 1
                        if response.get('error'):
                            raise ValueError(response['error'])
                        items, position = parse_page(provider, response['data'], checkpoint.position, config['page_size'])
                        for item in items:
                            m = {}
                            try:
                                if provider == 'crossref' and item.get('type') in {'grant', 'dataset', 'component', 'peer-review'}:
                                    counts['filtered'] += 1
                                    counts['scanned'] += 1
                                    report['records'].append({'provider': provider, 'query': query['name'], 'outcome': 'filtered',
                                        'reason': 'Outside publication-discovery scope: ' + item['type'], 'evidence_url': url,
                                        'metadata': {'provider_id': item.get('DOI'), 'publication_type': item['type']}})
                                    continue
                                m = metadata_for(provider, item)
                                matched, reason = relevance(m, query)
                                if not matched:
                                    outcome, candidate, matches = 'filtered', None, []
                                else:
                                    outcome, candidate, matches = inspect_candidate(provider, m)
                                    keys = set(identity_values(provider, m))
                                    if not apply and outcome == 'new' and keys & preview_seen:
                                        outcome = 'rediscovered'
                                    if outcome == 'new' and (counts['new'] >= config['max_new_candidates'] or awaiting + counts['new'] >= config['max_unreviewed']):
                                        page_done = False
                                        stop = True
                                        report['limitations'].append('Candidate cap reached mid-page; resume replays this page safely.')
                                        break
                                    if apply:
                                        outcome, candidate, matches = ingest(provider, m, query, url, response['retrieved_at'], reason, run)
                                    else:
                                        preview_seen.update(keys)
                                counts[outcome] += 1
                                counts['scanned'] += 1
                                report['records'].append({'provider': provider, 'query': query['name'], 'search': query_text, 'evidence_url': url,
                                    'retrieved_at': response['retrieved_at'], 'outcome': outcome, 'reason': reason, 'matches': matches,
                                    'candidate_id': candidate.pk if candidate else None,
                                    'metadata': {k: v for k, v in m.items() if k != 'abstract'}})
                            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                                counts['invalid'] += 1
                                counts['scanned'] += 1
                                report['records'].append({'outcome': 'invalid', 'provider': provider, 'query': query['name'],
                                    'reason': str(exc), 'evidence_url': url, 'raw_record': item, 'metadata': m})
                        if page_done:
                            if position is not None:
                                checkpoint.position = position
                                next_streams.append((provider, query, checkpoint))
                            else:
                                checkpoint.completed_through = checkpoint.window_end
                                checkpoint.position = {}
                                if checkpoint.window_end < end:
                                    checkpoint.window_start = checkpoint.window_end + timedelta(days=1)
                                    checkpoint.window_end = min(end, checkpoint.window_start + timedelta(days=config['window_days'] - 1))
                                    next_streams.append((provider, query, checkpoint))
                                else:
                                    checkpoint.window_start = checkpoint.window_end = None
                            if apply:
                                checkpoint.save()
                        elif apply:
                            # Save this page's starting position even on the very first page.
                            checkpoint.save()
                        report['checkpoints'].append({'provider': provider, 'query': query['name'], 'completed_through': str(checkpoint.completed_through or ''),
                            'next_start': str(checkpoint.window_start or ''), 'position': checkpoint.position})
                        self.stdout.write(f'{provider}/{query["name"]}: {len(items)} results; {counts["new"]} new suggestions so far', ending='\n')
                        self.stdout.flush()
                    except BudgetReached as exc:
                        report['limitations'].append(str(exc))
                        stop = True
                        break
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        report['errors'].append({'provider': provider, 'query': query['name'], 'url': url, 'reason': str(exc)})
                        # Other streams continue; no advancement for the failed page.
                streams = next_streams
        except Exception as exc:
            report['errors'].append({'reason': f'Runner stopped: {type(exc).__name__}: {exc}'})
            raise
        finally:
            client.session.close()
            report['finished_at'] = timezone.now().isoformat()
            report['counts'] = dict(counts)
            report['status'] = 'PARTIAL' if report['errors'] or report['limitations'] or counts['invalid'] else 'COMPLETE'
            if apply:
                run.finished_at = timezone.now()
                run.status = report['status']
                run.summary = {k: v for k, v in report.items() if k not in {'records', 'configuration'}}
                run.save()
            output = Path(options['output']) if options['output'] else state / 'reports' / f'{run_id}.json'
            write_report(output, report)
            self.stdout.write(f'{report["status"]}: {json.dumps(dict(counts))}. Report: {output} and {output.with_suffix(".html")}')
        if report['errors'] or counts['invalid']:
            raise CommandError('Some provider requests/records need attention. See the report; other progress was retained.')
