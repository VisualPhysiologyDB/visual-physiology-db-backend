import csv
import html
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from core.tuning_mutations import repair_inventory, apply_source_corrections
from core.tuning_repairs import merge_duplicates, synchronize_merged_accessions, recover_missing_links


class Command(BaseCommand):
    help = 'Tag mutation accessions and apply explicitly reviewed duplicate/source repairs. Default: read-only preview.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--assay-ids', nargs='+', type=int)
        parser.add_argument('--retry-unresolved', action='store_true', help='Recheck flagged constructs after correcting their source sequence/notation/numbering.')
        parser.add_argument('--source-corrections', help='Reviewed original-value-guarded correction/merge JSON.')
        parser.add_argument('--supplement', default='heterologous.csv')
        parser.add_argument('--output', default='reports/mutation-accessions.json')

    def handle(self, *args, **options):
        apply = options['apply']; path = options['source_corrections']
        corrections = apply_source_corrections(path, apply=apply, actor='repair_mutation_accessions') if path else []
        merges = merge_duplicates(path, apply=apply) if path else []
        links = recover_missing_links(options['supplement'], apply=apply) if Path(options['supplement']).exists() else []
        report = repair_inventory(apply=apply, assay_ids=options.get('assay_ids'), retry_unresolved=options['retry_unresolved'])
        if apply: synchronize_merged_accessions()
        report.update(source_corrections=corrections, merges=merges, restored_links=links)
        if path and not apply:
            report['preview_note'] = 'Read-only preview uses current source values. Merge/tag outcomes can change after the listed source corrections; use an isolated copy for a complete applied rehearsal.'
        output = Path(options['output']); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
        fields = ('hetid', 'notation', 'original_accession', 'accession', 'outcome', 'action', 'needs_sequence_review', 'reason')
        rows = [{**p, 'original_accession': p['input']['accession']} for p in report['rows']]
        def safe(value):
            value = str(value if value is not None else '')
            return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        with output.with_suffix('.csv').open('w', newline='') as handle:
            writer = csv.writer(handle); writer.writerow(fields)
            writer.writerows([[safe(row.get(k)) for k in fields] for row in rows])
        output.with_suffix('.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Mutation accession repair</title><style>body{font:16px system-ui;margin:2rem}td,th{padding:.5rem;border:1px solid #bbb;text-align:left}table{border-collapse:collapse}input{padding:.5rem}</style><h1>Mutation accession repair</h1><p>Search by assay ID, accession or outcome. Corrections are made in Django admin, not in this report.</p><label>Filter <input id="filter" type="search"></label><p id="count"></p><table><thead><tr>' + ''.join('<th>'+html.escape(k)+'</th>' for k in fields) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(safe(row.get(k)))+'</td>' for k in fields)+'</tr>' for row in rows) + '</tbody></table><script>const q=document.querySelector("input"),rows=[...document.querySelectorAll("tbody tr")];function update(){let n=0;for(const r of rows){r.hidden=!r.textContent.toLowerCase().includes(q.value.toLowerCase());if(!r.hidden)n++}document.querySelector("#count").textContent=n+" / "+rows.length;}q.oninput=update;update();</script></html>')
        self.stdout.write(json.dumps({k: report[k] for k in ('counts', 'source_corrections', 'merges', 'restored_links')}))
        self.stdout.write(f'Reports: {output}, {output.with_suffix(".csv")}, {output.with_suffix(".html")}')
