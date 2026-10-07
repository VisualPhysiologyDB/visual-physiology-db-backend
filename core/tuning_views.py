import fcntl
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.templatetags.static import static
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from .models import TuningProtein, TuningEvidence, Opsin
from .serializers import ReferenceSerializer
from .tuning_mapping import fasta, map_sites, MappingError
from .tuning_sites import site_groups


def public_evidence():
    queryset=TuningEvidence.objects.filter(status='APPROVED',protein__status='APPROVED').select_related('protein','wild_type_assay__opsin','wild_type_assay__reference','mutant_assay__opsin','mutant_assay__reference','extracted_candidate').prefetch_related('citations__reference').order_by('key')
    return [e for e in queryset if e.citations.all() and all(c.reference.status=='APPROVED' for c in e.citations.all())
            and all(a is None or (a.status=='APPROVED' and not a.is_inferred and not a.duplicate_of_id) for a in (e.wild_type_assay,e.mutant_assay))
            and extracted_current(e)]


def extracted_current(e):
    from .tuning_extraction import assay_snapshot
    candidate=getattr(e,'extracted_candidate',None)
    if candidate is None:return True
    if candidate.decision!='APPROVED' or candidate.stale or candidate.fingerprint!=candidate.reviewed_fingerprint:return False
    inputs=candidate.analysis['inputs']
    snapshots={r['hetid']:r for r in [inputs['assay'],*inputs['comparators']]}
    return all(a is not None and assay_snapshot(a)==snapshots.get(a.pk) for a in (e.wild_type_assay,e.mutant_assay))


def evidence_json(e):
    return {'key':e.key,'title':e.title,'protein_key':e.protein.key,'numbering_note':e.protein.numbering_note,
        'family':e.family,'subtype':e.subtype,'organism':e.organism,'category':e.category,'changes':e.changes,
        'original_notation':e.original_notation,'baseline_label':e.baseline_label,'baseline_nm':e.baseline_nm,'mutant_nm':e.mutant_nm,
        'shift_nm':e.shift_nm,'conditions':e.conditions,'notes':e.notes,'source_locator':e.source_locator,'release':e.release,
        'wild_type_assay_id':e.wild_type_assay_id,'mutant_assay_id':e.mutant_assay_id,
        'qualifies':e.category!='MEASURED' or e.shift_nm is not None and abs(e.shift_nm)>=1,
        'references':[{'role':c.role,'locator':c.locator,**ReferenceSerializer(c.reference).data} for c in e.citations.all()]}


class PublicTuningView(APIView):
    authentication_classes=[]
    permission_classes=[]


class TuningCatalogue(PublicTuningView):
    def get(self,request):
        entries=public_evidence()
        return Response({'beta':True,'scope':'Curated literature and automatically approved unambiguous sequence comparisons. Approval provenance accompanies each entry. Chimeras excluded. Mapping does not predict target spectral shifts.',
            'results':[evidence_json(e) for e in entries],'count':len(entries),
            'site_groups':site_groups(entries, TuningProtein.objects.filter(status='APPROVED'))})


class TuningTemplates(PublicTuningView):
    def get(self,request):
        values=[]
        for p in TuningProtein.objects.filter(status='APPROVED').order_by('key'):
            if p.provenance.get('kind')=='extracted_source':continue
            structure=dict(p.structure)
            if structure.get('file'):
                # Only curator-shipped local structure files can be requested by this endpoint.
                allowed={'1u19.cif','2z73.cif','6i9k.cif'}
                structure['url']=static('core/tuning-structures/'+structure['file']) if structure['file'] in allowed else None
            values.append({'key':p.key,'name':p.name,'family':p.family,'subtype':p.subtype,'accession':p.accession,
                'sequence':p.sequence,'numbering_note':p.numbering_note,'provenance':p.provenance,'structure':structure})
        return Response({'results':values,'count':len(values),'beta':True})


class MappingThrottle(AnonRateThrottle):
    rate='12/min'
    scope='tuning_mapping'


@contextmanager
def computation_slot():
    directory=Path(settings.BASE_DIR)/'var/tuning-locks';directory.mkdir(parents=True,exist_ok=True)
    handle=None
    for n in range(2):
        candidate=(directory/f'{n}.lock').open('a')
        try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB);handle=candidate;break
        except BlockingIOError:candidate.close()
    if handle is None:raise MappingError('Both alignment slots are busy. Please retry shortly.')
    try:yield
    finally:handle.close()


class TuningMapping(PublicTuningView):
    throttle_classes=[MappingThrottle]
    def post(self,request):
        if int(request.META.get('CONTENT_LENGTH') or 0)>120000:return Response({'error':'Request is too large.'},status=413)
        try:
            data=request.data
            if not isinstance(data,dict) or set(data)-{'fasta','opsin_ids','evidence_keys','site_keys','reference','custom_reference','target_family'}:
                raise MappingError('Unsupported request fields. Mapping does not accept approval or catalogue edits.')
            targets=[]
            if data.get('fasta'):targets=fasta(data['fasta'])
            ids=data.get('opsin_ids',[])
            if not isinstance(ids,list) or len(ids)>20 or any(type(i) is not int for i in ids):raise MappingError('Use up to 20 VPOD protein IDs.')
            for pk in ids:
                protein=Opsin.objects.filter(pk=pk,status='APPROVED').first()
                if not protein or not protein.protein_sequence:raise MappingError('A selected VPOD protein is unavailable or lacks a sequence.')
                targets.extend(fasta(f'>VPOD {pk}: {protein.genus} {protein.species}\n{protein.protein_sequence}'))
            if not targets or len(targets)>20 or len({t['name'] for t in targets})!=len(targets):raise MappingError('Choose 1–20 targets with distinct names.')
            keys=data.get('evidence_keys',[]); selected_sites=data.get('site_keys',[])
            if any(not isinstance(v,list) or any(not isinstance(k,str) for k in v) for v in (keys, selected_sites)) \
                    or not 1<=len(keys)+len(selected_sites)<=100:
                raise MappingError('Select 1–100 mutations/evidence entries or sites in total.')
            if len(set(keys)) != len(keys) or len(set(selected_sites)) != len(selected_sites):
                raise MappingError('Each mutation or site must be selected once.')
            entries=public_evidence();chosen=[e for e in entries if e.key in keys]
            if {e.key for e in chosen}!=set(keys):raise MappingError('A selected evidence entry is unavailable. Reload the catalogue.')
            if any(e.category=='MEASURED' and (e.shift_nm is None or abs(e.shift_nm)<1) for e in chosen):
                raise MappingError('Measured entries below 1 nm are contextual evidence, not selectable tuning sites.')
            available = list(TuningProtein.objects.filter(status='APPROVED').order_by('key'))
            groups = {g['key']:g for values in site_groups(entries, available).values() for g in values} if selected_sites else {}
            selected_groups = []
            by_key = {e.key:e for e in entries}; by_protein = {p.key:p for p in available}
            for key in selected_sites:
                group = groups.get(key)
                if not group or not any(evidence_json(by_key[m['evidence_key']])['qualifies'] for m in group['members']):
                    raise MappingError('A selected site is unavailable or has only below-cutoff evidence. Reload the catalogue.')
                selected_groups.append(group)
            site_assertions = [SimpleNamespace(key='site:'+g['key'], protein=by_protein[g['protein_key']],
                changes=[{'position':g['position'], 'from':None, 'to':None}], family=g['family'], shift_nm=None,
                category='SITE', subtype='', baseline_label='', selection_kind='SITE', site_key=g['key']) for g in selected_groups]
            family=data.get('target_family','OTHER')
            if not isinstance(family,str) or family not in {'C_OPSIN','R_OPSIN','OTHER'}:raise MappingError('Choose a supported target family or Other / unsure.')
            if not isinstance(data.get('reference','bovine'),str):raise MappingError('Reference must be a catalogue key.')
            custom=fasta(data['custom_reference'],1)[0] if data.get('custom_reference') else None
            source_ids={e.protein_id for e in chosen} | {e.protein.pk for e in site_assertions}
            proteins=[p for p in available if p.pk in source_ids or p.provenance.get('kind')!='extracted_source']
            with computation_slot():
                result=map_sites(proteins,[*site_assertions,*chosen],targets,data.get('reference','bovine'),custom,family)
            # Include all curated observations at selected coordinates, including subthreshold/no-effect results.
            sites={(e.protein_id,c['position']) for e in chosen for c in e.changes}
            site_evidence = {m['evidence_key'] for g in selected_groups for m in g['members']}
            related=[e for e in entries if e.key in site_evidence or any((e.protein_id,c['position']) in sites for c in e.changes)]
            result['evidence']=[evidence_json(e) for e in related]
            result['selected_keys']=[e.key for e in chosen]
            result['selected_site_keys']=selected_sites
            result['mapped_sites']=selected_groups
            response=Response(result);response['Cache-Control']='no-store';return response
        except (MappingError,FileNotFoundError) as exc:
            response=Response({'error':str(exc) if isinstance(exc,MappingError) else 'Reference alignment is not installed.'},status=400)
            response['Cache-Control']='no-store';return response
