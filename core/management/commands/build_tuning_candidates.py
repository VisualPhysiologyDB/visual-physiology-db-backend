"""Reconcile every assay and populate an idempotent, private review queue."""
import csv
import html
import json
import copy
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings
from django.test.utils import override_settings
from core.models import TuningEvidence, TuningCandidate
from core.tuning_extraction import (VERSION, PRIORITY, AlignmentCache, inventory, mutation,
    read_supplement, analyze, store_analysis, digest, live_inputs, auto_approve, automatic_eligibility)


class Command(BaseCommand):
    help = 'Extract single-site candidates. Dry run by default; --apply automatically approves eligible unambiguous matches unless disabled.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--supplement', default='heterologous.csv', help='Legacy extra measurement columns, guarded against row identity mismatches.')
        parser.add_argument('--no-supplement', action='store_true')
        parser.add_argument('--cache-dir', default='var/tuning-extraction-cache')
        parser.add_argument('--output', default='reports/tuning-single-sites.json')
        parser.add_argument('--from-report', help='Replay a reviewed extraction artifact without MAFFT; fails on changed inputs/version/profile.')
        approval = parser.add_mutually_exclusive_group()
        approval.add_argument('--auto-approve', dest='auto_approve', action='store_true', default=None)
        approval.add_argument('--no-auto-approve', dest='auto_approve', action='store_false')
        conditions = parser.add_mutually_exclusive_group()
        conditions.add_argument('--strict-conditions', dest='strict_conditions', action='store_true', default=None)
        conditions.add_argument('--relaxed-conditions', dest='strict_conditions', action='store_false')

    def handle(self, *args, **options):
        strict = settings.VPOD_TUNING_STRICT_CONDITIONS if options['strict_conditions'] is None else options['strict_conditions']
        with override_settings(VPOD_TUNING_STRICT_CONDITIONS=strict):
            return self.run(*args, **options)

    def run(self, *args, **options):
        approve = settings.VPOD_TUNING_AUTO_APPROVE if options['auto_approve'] is None else options['auto_approve']
        raw = read_supplement(None if options['no_supplement'] else options['supplement'])
        rows = inventory(); cache = AlignmentCache(options['cache_dir'])
        existing = {}
        for e in TuningEvidence.objects.exclude(mutant_assay=None).order_by('key'):
            existing.setdefault(e.mutant_assay_id, []).append(e.key)
        old = {c.assay_id: c for c in TuningCandidate.objects.all()}
        extracted_keys = {c.evidence.key for c in old.values() if c.evidence_id}
        existing = {k: [v for v in values if v not in extracted_keys] for k, values in existing.items()}
        report = {'version': VERSION, 'created_at': timezone.now().isoformat(),
                  'mode': 'apply' if options['apply'] else 'dry-run', 'priority': PRIORITY,
                  'algorithm': cache.identity, 'inventory': [], 'candidates': [], 'actions': {},
                  'policy': {'auto_approve': approve, 'strict_conditions': settings.VPOD_TUNING_STRICT_CONDITIONS,
                             'unit': 'Sequence-consistent database comparison; automatic approval does not claim human paper review.'}}
        replay = None
        if options['from_report']:
            previous = json.loads(Path(options['from_report']).read_text())
            if previous.get('version') != VERSION or previous.get('algorithm') != cache.identity:
                raise CommandError('Artifact version, MAFFT version or frozen reference profile changed; re-extract.')
            replay = {r['hetid']: r for r in previous['candidates']}
        actions = Counter()
        for row in rows:
            kind = mutation(row['mutations'])['kind']
            eligible = not row['is_inferred'] and kind in {'substitution', 'deletion', 'insertion'}
            item = {'hetid': row['hetid'], 'reference_id': row['reference_id'], 'notation': row['mutations'],
                    'outcome': 'inferred_excluded' if row['is_inferred'] else kind}
            if eligible:
                if replay is not None:
                    data = replay.get(row['hetid'])
                    inputs = live_inputs(row, rows, raw)
                    if not data or data.get('inputs') != inputs or data.get('fingerprint') != digest([inputs, cache.identity]) \
                            or data.get('existing_evidence') != existing.get(row['hetid'], []):
                        raise CommandError(f'Het {row["hetid"]}: artifact/source conflict. Re-extract; no forced overwrite.')
                    data = {k: v for k, v in data.items() if k not in {'review', 'auto_approval'}}
                else:
                    data = analyze(row, rows, raw, cache, existing)
                item['outcome'] = data['outcome']
                report['candidates'].append(data)
            report['inventory'].append(item)
            if len(report['inventory']) % 100 == 0:
                self.stdout.write(f'Inspected {len(report["inventory"])}/{len(rows)} assays; {len(report["candidates"])} single-site candidates.', ending='\n')
        if replay is not None and set(replay) != {d['hetid'] for d in report['candidates']}:
            raise CommandError('Artifact inventory differs from current source. Re-extract.')
        # Validate the whole replay before any queue mutation. Each normal analysis is independently resumable.
        for data in report['candidates']:
            actions[store_analysis(data) if options['apply'] else 'preview'] += 1
            if options['apply']:
                c = TuningCandidate.objects.get(assay_id=data['hetid'])
                data['auto_approval'] = auto_approve(c) if approve else 'disabled'
            else:
                c = copy.copy(old.get(data['hetid'])) if data['hetid'] in old else TuningCandidate(assay_id=data['hetid'])
                c.analysis = data; c.fingerprint = data['fingerprint']; c.outcome = data['outcome']
                data['auto_approval'] = automatic_eligibility(c) if approve else 'disabled'
            data['review'] = {k: getattr(c, k) for k in ('decision', 'selection', 'numbering_confirmed',
                'conditions_confirmed', 'cross_publication_confirmed', 'review_note', 'reviewed_fingerprint', 'stale', 'approval_mode')}
        report['counts'] = dict(Counter(r['outcome'] for r in report['inventory']))
        report['single_site_counts'] = dict(Counter(r['kind'] for r in report['candidates']))
        report['actions'] = dict(actions)
        report['approval_counts'] = dict(Counter(d['auto_approval'] for d in report['candidates']))
        if options['apply']:
            from core.tuning_sites import write_index
            from core.tuning_views import public_evidence
            proteins = {e.protein_id: e.protein for e in public_evidence()}
            try:
                report['site_index'] = write_index(proteins.values(), cache)
            except (OSError, ValueError) as exc:
                report['site_index'] = {'error': str(exc), 'action': 'Run index_tuning_sites after correcting the file/cache issue; source numbering remains available.'}
        output = Path(options['output']); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        columns = ['hetid', 'notation', 'organism', 'phylum', 'subtype', 'reference_id', 'doi', 'outcome',
                   'suggested_selection', 'existing_evidence', 'hypotheses', 'options', 'warnings', 'auto_approval']
        def cell(v):
            text = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v or '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
        with output.with_suffix('.csv').open('w', newline='') as handle:
            writer = csv.writer(handle); writer.writerow(columns)
            writer.writerows([[cell(d.get(k)) for k in columns] for d in report['candidates']])
        compact = ['hetid', 'notation', 'organism', 'phylum', 'reference_id', 'outcome', 'auto_approval', 'suggested_selection', 'options', 'warnings']
        body = ''.join('<tr>' + ''.join('<td>' + html.escape(cell(d.get(k))) + '</td>' for k in compact) + '</tr>' for d in report['candidates'])
        output.with_suffix('.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>VPOD single-site review</title><style>body{font:16px system-ui;margin:2em;color:#172d39}input{padding:.7em;width:80%}table{border-collapse:collapse;font-size:14px}td,th{padding:.7em;border:1px solid #ccd;vertical-align:top;overflow-wrap:anywhere}td{min-width:100px;max-width:420px}thead{background:#edf5f8}tr[hidden]{display:none}</style>
<h1>Single-site catalogue review</h1><p>Applied runs can automatically approve eligible unambiguous matches. See each row’s approval outcome and the JSON policy. Numbering priority: bovine → human (human opsins only) → squid → self → spider. Sequence consistency does not establish a transferable spectral effect.</p>
<p>Use Django admin → Tuning candidates to review/edit decisions. This file is a searchable snapshot; editing it does not change the database. JSON stores complete source snapshots; CSV opens in a spreadsheet.</p>
<label>Filter rows <input id="filter" type="search" placeholder="Species, mutation, reference ID, outcome…"></label><p id="count"></p><div style="overflow:auto"><table><thead><tr>'''
            + ''.join('<th>' + html.escape(k) + '</th>' for k in compact) + '</tr></thead><tbody>' + body + '''</tbody></table></div>
<script>const input=document.getElementById('filter'),rows=[...document.querySelectorAll('tbody tr')];function filter(){let n=0;for(const row of rows){row.hidden=!row.textContent.toLowerCase().includes(input.value.toLowerCase());if(!row.hidden)n++;}document.getElementById('count').textContent=n+' / '+rows.length+' candidates';}input.addEventListener('input',filter);filter();</script></html>''')
        self.stdout.write(json.dumps({k: report[k] for k in ('counts', 'single_site_counts', 'actions', 'approval_counts')}))
        self.stdout.write(f'Reports: {output}, {output.with_suffix(".csv")}, {output.with_suffix(".html")}')
