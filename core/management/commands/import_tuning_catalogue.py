"""Apply a reviewed catalogue artifact; existing curator values are never overwritten."""
import csv,json,re,hashlib
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from core.models import Reference,TuningProtein,TuningEvidence,TuningCitation,TuningAudit,HeterologousData
from core.bibliography import classify_identifier
from core.tuning_admin import snapshot


class Command(BaseCommand):
    help='Review/import the beta tuning catalogue. Default is a dry run. Chimeras are excluded.'
    def add_arguments(self,parser):
        parser.add_argument('--catalogue',default='data/tuning/catalogue.json')
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--output',default='reports/tuning-import.json')

    @transaction.atomic
    def handle(self,*args,**options):
        data=json.loads(Path(options['catalogue']).read_text());apply=options['apply']
        report={'mode':'apply' if apply else 'dry-run','release':data['release'],'created':[], 'existing_preserved':[], 'conflicts':[], 'unavailable_assay_links':[], 'inventory':[]}
        references={};proteins={}
        def save_new(obj):
            obj.full_clean(exclude=[] if apply else ['protein','wild_type_assay','mutant_assay'])
            report['created'].append({'type':type(obj).__name__,'key':getattr(obj,'key',getattr(obj,'doi',None))})
            if apply:
                obj.save()
                TuningAudit.objects.create(actor='import_tuning_catalogue',object_type=type(obj).__name__,object_key=getattr(obj,'key',obj.pk),before=None,after=snapshot(obj),reason='Reviewed artifact '+data['release'])
        for values in data['references']:
            doi=values['doi'];matches=[r for r in Reference.objects.order_by('refid') if classify_identifier(r.doi)['doi']==doi]
            if matches:
                ref=min(matches,key=lambda r:({'APPROVED':0,'PENDING':1,'REJECTED':2}[r.status],r.pk))
                report['existing_preserved'].append({'type':'Reference','refid':ref.pk,'doi':doi,'status':ref.status})
            else:
                ref=Reference(**values,status='APPROVED',identifier_kind='doi',source_data={'tuning_catalogue_release':data['release']})
                save_new(ref)
            references[doi]=ref
        for values in data['proteins']:
            existing=TuningProtein.objects.filter(key=values['key']).first()
            if existing:
                differences=[k for k,v in values.items() if getattr(existing,k)!=v]
                report['conflicts' if differences else 'existing_preserved'].append({'type':'TuningProtein','key':existing.key,'fields':differences})
                proteins[existing.key]=existing
            else:
                obj=TuningProtein(**values,status='APPROVED');save_new(obj);proteins[obj.key]=obj
        curated_assays=set()
        for item in data['evidence']:
            values=dict(item);key=values['key'];citations=values.pop('references');protein=proteins[values.pop('protein')];assays=values.pop('assay_ids',{});identities=values.pop('assay_identity',{})
            for field,pk in assays.items():
                assay=HeterologousData.objects.select_related('reference','opsin').filter(pk=pk,is_inferred=False).first()
                expected={c['doi'] for c in citations}
                actual={'mutations':assay.mutations or '', 'lambda_max':assay.lambda_max,
                    'sequence_sha256':hashlib.sha256((assay.opsin.protein_sequence or '').encode()).hexdigest() if assay.opsin else None} if assay else None
                if assay and assay.reference and classify_identifier(assay.reference.doi)['doi'] in expected and identities.get(field)==actual:
                    if re.search(r'chimer(?:a|ic)',assay.mutations or '',re.I):raise CommandError(f'{key}: source assay is chimeric')
                    values[field]=assay
                    curated_assays.add(assay.pk)
                else:report['unavailable_assay_links'].append({'key':key,'field':field,'expected_hetid':pk,'reason':'Missing source row or DOI/mutation/wavelength/sequence fingerprint differs'})
            existing=TuningEvidence.objects.filter(key=key).first()
            if existing:
                fields=[k for k,v in values.items() if getattr(existing,k)!=v]
                if existing.protein_id!=protein.pk:fields.append('protein')
                expected_citations={(references[c['doi']].pk,c['role'],c['locator']) for c in citations}
                actual=set(existing.citations.values_list('reference_id','role','locator'))
                if actual!=expected_citations:fields.append('citations')
                report['conflicts' if fields else 'existing_preserved'].append({'type':'TuningEvidence','key':key,'fields':fields})
                continue
            obj=TuningEvidence(**values,protein=protein,status='APPROVED');save_new(obj)
            if apply:
                for c in citations:TuningCitation.objects.create(evidence=obj,reference=references[c['doi']],role=c['role'],locator=c['locator'])
        for assay in HeterologousData.objects.filter(is_inferred=False).order_by('pk'):
            raw=(assay.mutations or '').strip()
            outcome='not_public' if assay.status!='APPROVED' else 'included_or_comparator' if assay.pk in curated_assays else 'wild_type_record' if not raw else 'out_of_scope_chimera' if re.search(r'chimer(?:a|ic)',raw,re.I) else 'needs_primary_review'
            report['inventory'].append({'hetid':assay.pk,'reference_id':assay.reference_id,'mutations':raw,'status':assay.status,'outcome':outcome,
                'reason':'No spectral shift inferred without a verified comparator, source numbering and compatible conditions.' if outcome=='needs_primary_review' else ''})
        from collections import Counter
        report['inventory_counts']=dict(Counter(r['outcome'] for r in report['inventory']))
        output=Path(options['output']);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        with output.with_suffix('.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['hetid','reference_id','mutations','status','outcome','reason']);writer.writeheader();writer.writerows(report['inventory'])
        self.stdout.write(json.dumps({k:v for k,v in report.items() if k in ['mode','release','inventory_counts']}))
        self.stdout.write(f'{len(report["created"])} creations; {len(report["conflicts"])} preserved conflicts. Report: {output}')
