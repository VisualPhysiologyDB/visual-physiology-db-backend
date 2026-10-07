"""Temporary, bounded MAFFT calculations. No network requests or user-sequence persistence."""
import hashlib
from functools import lru_cache
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from django.conf import settings

AMINO = set('ACDEFGHIKLMNPQRSTVWYBXZJUO')


class MappingError(ValueError):
    pass


def fasta(text, max_count=20):
    if not isinstance(text,str) or len(text)>60000:
        raise MappingError('Provide at most 20 protein sequences (60 KB total).')
    entries=[];name=None;parts=[]
    for line in text.strip().splitlines():
        line=line.strip()
        if not line:continue
        if line.startswith('>'):
            if name is not None:entries.append((name,''.join(parts)))
            name=line[1:].strip();parts=[]
            if not name or len(name)>150:raise MappingError('Each FASTA name must contain 1–150 characters.')
        else:
            if name is None:name='Sequence 1'
            parts.append(re.sub(r'\s+','',line).upper())
    if name is not None:entries.append((name,''.join(parts)))
    if not entries or len(entries)>max_count:raise MappingError(f'Provide 1–{max_count} protein sequences.')
    seen=set();result=[]
    for name,seq in entries:
        if name in seen:raise MappingError('FASTA names must be unique.')
        seen.add(name)
        if not 30<=len(seq)<=2000:raise MappingError(f'{name}: use 30–2000 amino acids; fragments are allowed.')
        if set(seq)-AMINO:raise MappingError(f'{name}: invalid protein characters. Supply ungapped amino acids, without stop symbols.')
        if set(seq)<=set('ACGTN'):raise MappingError(f'{name}: this appears to be DNA; provide a translated protein.')
        result.append({'name':name,'sequence':seq,'sha256':hashlib.sha256(seq.encode()).hexdigest()})
    return result


def read_alignment(text):
    result={};key=None
    for line in text.splitlines():
        if line.startswith('>'):key=line[1:].strip();result[key]=''
        elif key is not None:result[key]+=line.strip().upper()
    if not result or len({len(s) for s in result.values()})!=1:raise MappingError('Alignment service returned invalid output.')
    return result


def run_mafft(arguments, timeout):
    executable=getattr(settings,'VPOD_MAFFT_PATH','/usr/local/bin/mafft')
    try:
        process=subprocess.Popen([executable,'--quiet','--thread','1',*arguments],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,start_new_session=True,env={**os.environ,'OMP_NUM_THREADS':'1'})
    except OSError as exc:raise MappingError('Alignment service is unavailable. The maintainer must configure MAFFT.') from exc
    try:
        out,_=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid,signal.SIGKILL);process.communicate()
        raise MappingError('Alignment exceeded the time limit. Try fewer or shorter sequences.') from exc
    if process.returncode:raise MappingError('Alignment could not be completed; check the protein sequences.')
    return read_alignment(out)


@lru_cache(maxsize=4)
def aligner_version(executable):
    try:
        result=subprocess.run([executable,'--version'],capture_output=True,text=True,timeout=2)
        found=re.search(r'v?\d+\.\d+(?:\.\d+)?',result.stdout+' '+result.stderr)
        return found.group(0) if found else 'unknown'
    except (OSError,subprocess.TimeoutExpired):return 'unavailable'


def coordinate_maps(alignment):
    maps={}
    for key,seq in alignment.items():
        positions={};columns={};n=0
        for col,aa in enumerate(seq):
            if aa!='-':n+=1;positions[n]=col;columns[col]=(n,aa)
        maps[key]=(positions,columns)
    return maps


def mutation_assessment(change, hit):
    start, end = change.get('from'), change.get('to')
    if not start or not end:
        return 'SITE_ONLY', 'Position only; no amino-acid substitution is prescribed.', None
    if not hit:
        return 'UNRESOLVED', 'Cannot assess the substitution without a reliable target coordinate.', None
    pos, residue = hit
    if residue in 'XBZJUO':
        return 'UNCERTAIN', f'Target residue {residue}{pos} is ambiguous; the starting amino acid cannot be checked.', None
    if residue == end:
        return 'ALREADY_PRESENT', f'Target already has {end}{pos}, the reported replacement residue. This would not change its amino acid.', None
    if residue != start:
        return 'RESIDUE_MISMATCH', f'Tested substitution starts from {start}; target has {residue}{pos}. {residue}{pos}{end} would be a different, unvalidated substitution.', None
    return 'START_MATCHES', f'Target has the required starting residue {start}{pos}. The source spectral effect is not a prediction for this protein.', f'{start}{pos}{end}'


def map_sites(proteins,evidence,targets,display_key,custom=None,target_family='OTHER'):
    """Freeze the reference alignment, add targets twice under distinct gap penalties.

    Agreement is a diagnostic, not a calibrated probability of homology. Cross-family
    mappings remain review-required even when the two alignments agree.
    """
    started=time.monotonic()
    template=Path(settings.BASE_DIR)/'data/tuning/reference-profile.fasta'
    profile=read_alignment(template.read_text())
    source_keys={p.key:p for p in proteins}
    dynamic={k:p for k,p in source_keys.items() if p.provenance.get('kind')=='extracted_source'}
    for p in dynamic.values():
        if hashlib.sha256(p.sequence.encode()).hexdigest()!=p.provenance.get('sequence_sha256'):
            raise MappingError('A source sequence snapshot changed. Ask a curator to review the evidence.')
    for key,p in source_keys.items():
        if key not in dynamic and (key not in profile or profile[key].replace('-','')!=p.sequence):
            raise MappingError('Reference sequence differs from the released alignment. Ask a curator to rebuild the catalogue release.')
    profile={k:v for k,v in profile.items() if k in source_keys}
    additions={f't{i}':t['sequence'] for i,t in enumerate(targets)}
    if len(dynamic)>20:raise MappingError('Select evidence from at most 20 source proteins per mapping.')
    additions.update({k:p.sequence for k,p in dynamic.items()})
    if custom:additions['custom']=custom['sequence'];display_key='custom'
    elif display_key not in source_keys:raise MappingError('Choose an available numbering reference.')
    with tempfile.TemporaryDirectory(prefix='vpod-map-') as folder:
        folder=Path(folder);pf=folder/'profile.fasta';nf=folder/'targets.fasta'
        pf.write_text(''.join(f'>{k}\n{s}\n' for k,s in profile.items()))
        nf.write_text(''.join(f'>{k}\n{s}\n' for k,s in additions.items()))
        alignments=[]
        for gap in ('1.53','3.0'):
            remaining=20-(time.monotonic()-started)
            if remaining<=0:raise MappingError('Mapping exceeded its total time limit.')
            alignment=run_mafft(['--inputorder','--op',gap,'--addfragments',str(nf),str(pf)],remaining)
            if set(alignment)!=set(profile)|set(additions):raise MappingError('Alignment identifiers failed validation.')
            if any(alignment[k].replace('-','')!=s for k,s in {**{k:v.replace('-','') for k,v in profile.items()},**additions}.items()):
                raise MappingError('Alignment changed an input sequence; no mappings returned.')
            alignments.append(alignment)
    first,second=alignments;maps,other=map(coordinate_maps,alignments)
    rows=[];summaries=[]
    for i,target in enumerate(targets):
        key=f't{i}'
        similarities=[]
        for pkey,p in source_keys.items():
            pairs=[(a,b) for a,b in zip(first[pkey],first[key]) if a!='-' and b!='-' and a not in 'XBZJUO' and b not in 'XBZJUO']
            identity=sum(a==b for a,b in pairs)/len(pairs) if pairs else 0
            similarities.append((identity,pkey,len(pairs)))
        identity,closest,overlap=max(similarities)
        coverage=overlap/len(target['sequence'])
        summaries.append({'name':target['name'],'closest_template':closest,'identity':round(identity,3),'aligned_target_fraction':round(coverage,3),
                          'note':'Nearest available template, not a phylogenetic classification.'})
        for e in evidence:
            for change in e.changes:
                source=e.protein.key;position=change['position'];col=maps[source][0][position];col2=other[source][0][position]
                hit=maps[key][1].get(col);hit2=other[key][1].get(col2)
                display=maps[display_key][1].get(col);display2=other[display_key][1].get(col2)
                warnings=[];state='MAPPED'
                if target_family!='OTHER' and e.family!=target_family:warnings.append('Evidence is from another opsin family; its spectral effect cannot be transferred.');state='REVIEW'
                if target_family=='OTHER':warnings.append('Target opsin family was not specified.');state='REVIEW'
                if not hit:state='ABSENT';warnings.append('Site is absent or outside this sequence fragment.')
                elif identity<0.25 or coverage<0.4:
                    state='UNRESOLVED';hit=None;warnings.append('Sequence support is insufficient for a defensible coordinate.')
                elif hit!=hit2:state='AMBIGUOUS';hit=None;warnings.append('Position changes under alternative alignment settings; coordinate withheld.')
                elif hit[1] in 'XBZJUO':state='UNCERTAIN';warnings.append('Ambiguous target amino acid.')
                else:
                    lo=max(0,col-5);hi=min(len(first[source]),col+6)
                    near=list(zip(first[source][lo:hi],first[key][lo:hi]))
                    exact=sum(a==b and a!='-' for a,b in near)/len(near)
                    if exact<0.35 or any(a=='-' or b=='-' for a,b in near):
                        if state=='MAPPED':state='REVIEW'
                        warnings.append('Local sequence is divergent or adjacent to an insertion/deletion.')
                mutation_status, mutation_note, target_mutation = mutation_assessment(change, hit)
                if mutation_status in {'RESIDUE_MISMATCH', 'ALREADY_PRESENT'}:
                    warnings.append(mutation_note)
                    if state == 'MAPPED': state = 'REVIEW'
                if display!=display2:display=None;warnings.append('Display-reference numbering is ambiguous; coordinate withheld.')
                if not display:warnings.append('No reliable coordinate in the selected numbering reference.')
                rows.append({'evidence_key':e.key,'target_name':target['name'],'source_protein':source,'source_position':position,
                    'selection_kind':getattr(e, 'selection_kind', 'MUTATION'), 'site_key':getattr(e, 'site_key', None),
                    'mutation_status':mutation_status, 'mutation_note':mutation_note, 'target_mutation':target_mutation,
                    'source_from':change.get('from'),'source_to':change.get('to'),'reference_name':custom['name'] if custom else source_keys[display_key].name,
                    'reference_position':display[0] if display else None,'reference_residue':display[1] if display else None,
                    'target_position':hit[0] if hit else None,'target_residue':hit[1] if hit else None,'alignment_column':col+1,
                    'status':state,'warnings':warnings,'shift_nm_in_source':e.shift_nm,'category':e.category,'subtype':e.subtype,
                    'combination_size':len(e.changes),'baseline_label':e.baseline_label})
    return {'beta':True,'algorithm':'MAFFT --addfragments; gap penalties 1.53 and 3.0; agreement is not a probability',
        'mafft_version':aligner_version(getattr(settings,'VPOD_MAFFT_PATH','/usr/local/bin/mafft')),
        'profile_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),'rows':rows,'targets':targets,'summaries':summaries,
        'alignment':{'sequences':[{'name':source_keys[k].name if k in source_keys else (custom['name'] if k=='custom' else targets[int(k[1:])]['name']),
                       'key':k,'aligned':v} for k,v in first.items()]},'elapsed_seconds':round(time.monotonic()-started,3)}
