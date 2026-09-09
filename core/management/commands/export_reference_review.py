"""Render an enrichment artifact for human review; no database or network writes."""
import csv
import html
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from core.bibliography import safe_url


def text(value):
    return html.escape(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def link(url, label):
    return f'<a href="{text(url)}" target="_blank" rel="noopener noreferrer">{text(label)}</a>' if safe_url(url) else text(label)


def spreadsheet(value):
    value = '' if value is None else str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


class Command(BaseCommand):
    help = 'Create a searchable standalone HTML reference-curation worklist and optional CSV from an enrichment artifact.'

    def add_arguments(self, parser):
        parser.add_argument('--artifact', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--csv-output')
        parser.add_argument('--admin-base-url', default='http://127.0.0.1:8000', help='Site origin for Edit in admin links')

    def handle(self, *args, **options):
        artifact = json.loads(Path(options['artifact']).read_text())
        if artifact.get('schema_version') != 1:
            raise CommandError('Expected an enrich_references schema_version 1 artifact')
        base = options['admin_base_url'].rstrip('/')
        if not safe_url(base):
            raise CommandError('--admin-base-url must be a valid http(s) site URL')
        cards, rows = [], []

        def add(kind, refid, field, original, candidate, reason, evidence, details):
            admin_url = f'{base}/admin/core/reference/{refid}/change/'
            rows.append([kind, refid, field, original, candidate, reason, evidence, admin_url])
            cards.append(f'<article data-kind="{kind}"><h2>{text(kind)} · Reference {refid}</h2>'
                f'<p>{link(admin_url, "Edit reference in Django admin")}</p><p>{text(reason)}</p>'
                f'<dl><dt>Field</dt><dd>{text(field)}</dd><dt>Original / current source</dt><dd>{text(original)}</dd>'
                f'<dt>Candidate (requires review)</dt><dd>{text(candidate)}</dd></dl>{details}</article>')

        for record in artifact['records']:
            if record.get('fields'):
                continue
            candidates = record.get('candidates', [])
            evidence = '\n'.join(a.get('url', '') for a in record.get('attempts', []))
            details = '<details><summary>Attempted providers and candidate evidence</summary>'
            for attempt in record.get('attempts', []):
                details += '<p>' + link(attempt.get('url'), attempt.get('url', 'Provider response')) + '</p><pre>' + text(attempt) + '</pre>'
            for candidate in candidates:
                details += '<pre>' + text(candidate) + '</pre>'
                if candidate.get('doi'):
                    from urllib.parse import quote
                    details += '<p>' + link('https://doi.org/' + quote(candidate['doi'], safe='/'), 'Open candidate DOI') + '</p>'
            details += '</details>'
            add('Unresolved', record['refid'], 'publication identity', record.get('identifier'),
                json.dumps(candidates, ensure_ascii=False) if candidates else 'No confirmed candidate',
                record.get('reason'), evidence, details)
        for entry in artifact['audit']:
            if entry['decision'] == 'conflict':
                add('Conflict', entry['refid'], entry['field'], entry['original_value'], entry['recovered_value'],
                    entry['reason'], entry['evidence_url'], '<p>' + link(entry['evidence_url'], 'Open provider evidence') + '</p>')
        for group in artifact.get('duplicate_dois', []):
            for refid in group['refids']:
                add('Duplicate publication', refid, 'doi', group['doi'], '',
                    'Same normalized DOI occurs at reference IDs ' + ', '.join(map(str, group['refids'])) + '. No records have been merged.', '', '')
        document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPOD reference curation worklist</title><style>
body{max-width:1050px;margin:2rem auto;padding:0 1rem;font:16px/1.5 system-ui;color:#172a35;background:#f3f6f8}article{background:white;border:1px solid #cbd5dc;padding:1rem;margin:1rem 0;border-radius:8px}h2{font-size:1.2rem}dd{margin:0 0 1rem;white-space:pre-wrap;overflow-wrap:anywhere}dt{font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}input,select{font:inherit;padding:.5rem;max-width:95%}a{color:#075b84}summary{cursor:pointer}button{font:inherit}[hidden]{display:none}
</style><h1>Reference curation worklist</h1>
<p>This is a read-only snapshot. Open a reference in Django admin to correct it; this file does not write changes. The links require your site to be running and an authorized curator login.</p>
<ol><li>Review the original citation, candidates and provider evidence.</li><li>In admin, correct DOI/title/date/year only when supported. Preserve date precision: YYYY, YYYY-MM or YYYY-MM-DD. Record supporting evidence in source URL or notes.</li><li>Save in admin. Field changes are audited and protected on subsequent enrichment runs. Adjust the compatible year deliberately if choosing between online and print years.</li><li>Rerun enrichment and regenerate this worklist to get a new snapshot. Historical conflicts may still require interpretation; saving an edit does not alter this file.</li></ol>
<p><label>Find text or reference ID <input id="search" type="search"></label> <label>Issue <select id="kind"><option value="">All</option><option>Unresolved</option><option>Conflict</option><option>Duplicate publication</option></select></label></p><p id="count" role="status"></p>
'''
        document += '<p>Artifact run: ' + text(artifact.get('run_id')) + '. ' + text(artifact.get('summary')) + '</p>' + ''.join(cards)
        document += '''<script>
const articles=Array.from(document.querySelectorAll('article'));
function filter(){const q=document.getElementById('search').value.toLowerCase(),kind=document.getElementById('kind').value;let n=0;for(const card of articles){card.hidden=!(card.textContent.toLowerCase().includes(q)&&(!kind||card.dataset.kind===kind));if(!card.hidden)n++;}document.getElementById('count').textContent=n+' of '+articles.length+' review entries shown';}
document.getElementById('search').addEventListener('input',filter);document.getElementById('kind').addEventListener('change',filter);filter();
</script></html>'''
        Path(options['output']).write_text(document)
        if options['csv_output']:
            with Path(options['csv_output']).open('w', newline='', encoding='utf-8-sig') as file:
                writer = csv.writer(file)
                writer.writerow(['issue', 'refid', 'field', 'original_value', 'candidate_value', 'reason', 'evidence_url', 'admin_url'])
                writer.writerows([[spreadsheet(cell) for cell in row] for row in rows])
        self.stdout.write(f'Wrote {len(rows)} review entries to {options["output"]}; no database changes.')
