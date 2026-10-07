"""Scientific coordinates, import preservation and public boundaries for the beta mapper."""
import io
import json
import math
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient
from core.models import Reference, Opsin, HeterologousData, TuningProtein, TuningEvidence, TuningCitation, TuningAudit
from core.tuning_mapping import fasta, read_alignment, coordinate_maps, map_sites, MappingError, run_mafft
from core.tuning_views import evidence_json, computation_slot

CATALOGUE = Path(settings.BASE_DIR) / 'data/tuning/catalogue.json'
RELEASE = json.loads(CATALOGUE.read_text())


class ProteinInputTests(SimpleTestCase):
    def test_plain_multifasta_and_private_names(self):
        seq=RELEASE['proteins'][0]['sequence']
        self.assertEqual(fasta(seq)[0]['name'],'Sequence 1')
        parsed=fasta(f'>first\n{seq.lower()}\n><img onerror=alert(1)>\n{seq}')
        self.assertEqual(len(parsed),2)
        self.assertEqual(parsed[0]['sequence'],seq)
        self.assertEqual(len(parsed[0]['sha256']),64)

    def test_dna_gaps_stops_empty_duplicate_and_limits(self):
        seq=RELEASE['proteins'][0]['sequence']
        for raw in ['', 'ATGC'*30,seq+'*',seq+'-', '>same\n'+seq+'\n>same\n'+seq, 'M'*29, 'M'*2001, '>x\n'+'M'*60001]:
            with self.subTest(raw=raw[:15]),self.assertRaises(MappingError):fasta(raw)
        with self.assertRaises(MappingError):fasta('\n'.join(f'>s{i}\n{seq}' for i in range(21)))

    def test_ambiguous_amino_acids_retained(self):
        self.assertIn('X',fasta('M'*30+'XBZJUO')[0]['sequence'])

    def test_reference_profile_known_numbering(self):
        profile=read_alignment((CATALOGUE.parent/'reference-profile.fasta').read_text());maps=coordinate_maps(profile)
        for source,target in [(113,126),(181,194),(186,199),(296,321)]:
            self.assertEqual(maps['spider'][1][maps['bovine'][0][source]][0],target)
        for human,bovine in [(180,164),(197,181),(277,261),(285,269),(308,292)]:
            self.assertEqual(maps['bovine'][1][maps['human-lws'][0][human]][0],bovine)
        for p in RELEASE['proteins']:self.assertEqual(profile[p['key']].replace('-',''),p['sequence'])

    def test_timeout_kills_process_group(self):
        process=Mock(pid=123,returncode=-9);process.communicate.side_effect=[subprocess.TimeoutExpired('mafft',1),('','')]
        with patch('core.tuning_mapping.subprocess.Popen',return_value=process),patch('core.tuning_mapping.os.killpg') as kill:
            with self.assertRaisesRegex(MappingError,'time limit'):run_mafft([],1)
            kill.assert_called_once()

    @override_settings(VPOD_MAFFT_PATH='/nonexistent/vpod-mafft')
    def test_missing_aligner_friendly_error(self):
        with self.assertRaisesRegex(MappingError,'unavailable'):run_mafft([],1)

    def test_concurrency_bound(self):
        with computation_slot(), computation_slot():
            with self.assertRaisesRegex(MappingError,'busy'):
                with computation_slot():pass


class TuningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with tempfile.TemporaryDirectory() as tmp:
            call_command('import_tuning_catalogue',apply=True,output=tmp+'/import.json',stdout=io.StringIO())

    def setUp(self):
        cache.clear();self.client=APIClient()
        self.proteins=list(TuningProtein.objects.order_by('key'))
        self.bovine=TuningProtein.objects.get(key='bovine');self.spider=TuningProtein.objects.get(key='spider')
        self.entry=TuningEvidence.objects.get(key='spider-e181d')

    def payload(self,**changes):
        value={'fasta':'>spider\n'+self.spider.sequence,'evidence_keys':[self.entry.key],'reference':'bovine','target_family':'R_OPSIN'};value.update(changes);return value

    def test_review_references_present_and_linked(self):
        self.assertTrue(Reference.objects.filter(doi='10.1016/j.ydbio.2022.10.014').exists())
        self.assertTrue(Reference.objects.filter(doi='10.1098/rstb.2021.0279').exists())
        lws=TuningEvidence.objects.get(key='hagen-lws-mws-180')
        self.assertEqual(lws.protein.key,'human-lws');self.assertEqual(lws.citations.count(),2)

    def test_citation_assay_details_preserve_publication_and_measurement_identity(self):
        wt_ref=Reference.objects.create(doi='10.1234/wt',status='APPROVED')
        mutant_ref=Reference.objects.create(doi='10.1234/mutant',status='APPROVED')
        opsin=Opsin.objects.create(genus='Test',species='species',phylum='Chordata',status='APPROVED')
        wt=HeterologousData.objects.create(opsin=opsin,reference=wt_ref,lambda_max=500,cell_culture='WT culture',status='APPROVED')
        mutant=HeterologousData.objects.create(opsin=opsin,reference=mutant_ref,lambda_max=503,cell_culture='Mutant culture',status='APPROVED')
        self.entry.wild_type_assay=wt;self.entry.mutant_assay=mutant;self.entry.save()
        data=evidence_json(self.entry)
        self.assertEqual(data['assays'],[
            {'role':'WT','hetid':wt.pk,'reference_id':wt_ref.pk,'species':'Test species','phylum':'Chordata','lambda_max':500,'culture':'WT culture','expression_type':'Heterologous'},
            {'role':'Mutant','hetid':mutant.pk,'reference_id':mutant_ref.pk,'species':'Test species','phylum':'Chordata','lambda_max':503,'culture':'Mutant culture','expression_type':'Heterologous'}])
        self.assertEqual(evidence_json(TuningEvidence.objects.get(key='hagen-rh1-83'))['assays'],[])
        wt.status='PENDING';wt.save()
        self.assertNotIn(self.entry.key,[e['key'] for e in self.client.get('/api/tuning-sites/').data['results']])

    def test_citation_assay_details_keep_missing_taxonomy_and_culture_unknown(self):
        assay=HeterologousData.objects.create(lambda_max=500,status='APPROVED')
        self.entry.wild_type_assay=assay;self.entry.save()
        row=evidence_json(self.entry)['assays'][0]
        for field in ['species','phylum','culture','reference_id']:
            self.assertIsNone(row[field])

    def test_dry_run_and_reimport_preserve_curator_changes_and_decisions(self):
        self.entry.status='PENDING';self.entry.notes='Curator checked this';self.entry.save()
        ref=self.entry.citations.first().reference;ref.title='Curated title';ref.status='REJECTED';ref.save()
        before=(TuningEvidence.objects.count(),Reference.objects.count(),TuningAudit.objects.count())
        with tempfile.TemporaryDirectory() as tmp:
            for apply in [False,True]:
                call_command('import_tuning_catalogue',apply=apply,output=tmp+'/report.json',stdout=io.StringIO())
                report=json.loads(Path(tmp+'/report.json').read_text());self.assertEqual(report['created'],[])
                self.assertTrue(any(c['key']==self.entry.key and 'notes' in c['fields'] for c in report['conflicts']))
        self.entry.refresh_from_db();ref.refresh_from_db()
        self.assertEqual(self.entry.status,'PENDING');self.assertEqual(self.entry.notes,'Curator checked this');self.assertEqual(ref.title,'Curated title');self.assertEqual(ref.status,'REJECTED')
        self.assertEqual(before,(TuningEvidence.objects.count(),Reference.objects.count(),TuningAudit.objects.count()))

    def test_dry_run_creates_no_new_records(self):
        data=dict(RELEASE);data['evidence']=[{**RELEASE['evidence'][0],'key':'dry-only'}]
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'seed.json';artifact.write_text(json.dumps(data));call_command('import_tuning_catalogue',catalogue=str(artifact),output=tmp+'/r.json',stdout=io.StringIO())
        self.assertFalse(TuningEvidence.objects.filter(key='dry-only').exists())

    def test_import_reports_all_assays_including_chimeras_and_rejected(self):
        for mutations,status,inferred in [('A83D','APPROVED',False),('Chimeric construct','APPROVED',False),('', 'REJECTED',False),('', 'APPROVED',True)]:
            HeterologousData.objects.create(mutations=mutations,status=status,is_inferred=inferred,lambda_max=500)
        with tempfile.TemporaryDirectory() as tmp:
            call_command('import_tuning_catalogue',output=tmp+'/r.json',stdout=io.StringIO());r=json.loads(Path(tmp+'/r.json').read_text())
        self.assertEqual(len(r['inventory']),3);self.assertEqual(r['inventory_counts'],{'needs_primary_review':1,'out_of_scope_chimera':1,'not_public':1})

    def test_finite_measurements_and_explicit_baselines(self):
        for attr,value in [('reported_shift_nm',math.inf),('baseline_nm',-1),('changes',[{'position':9999}]),('baseline_label','Chimeric baseline'),('original_notation','Chimera X')]:
            e=TuningEvidence.objects.get(pk=self.entry.pk);setattr(e,attr,value)
            with self.subTest(attr=attr),self.assertRaises(ValidationError):e.full_clean()
        self.entry.reported_shift_nm=None
        with self.assertRaises(ValidationError):self.entry.full_clean()

    def test_inferred_or_chimeric_assay_cannot_be_evidence(self):
        for values in [dict(is_inferred=True),dict(mutations='chimeric protein')]:
            assay=HeterologousData.objects.create(lambda_max=500,**values);self.entry.mutant_assay=assay
            with self.assertRaises(ValidationError):self.entry.full_clean()

    def test_multi_site_effect_is_single_construct(self):
        self.entry.changes=[{'position':181,'from':'E','to':'D'},{'position':186,'from':'S','to':'A'}];self.entry.full_clean()
        result=map_sites(self.proteins,[self.entry],fasta(self.payload()['fasta']),'bovine',target_family='R_OPSIN')
        self.assertEqual(len(result['rows']),2)
        self.assertEqual({r['combination_size'] for r in result['rows']},{2})
        self.assertEqual({r['shift_nm_in_source'] for r in result['rows']},{-8})

    def test_cutoff_at_one_nm_and_no_effect_context(self):
        self.entry.reported_shift_nm=1;self.assertTrue(evidence_json(self.entry)['qualifies'])
        self.entry.reported_shift_nm=-1;self.assertTrue(evidence_json(self.entry)['qualifies'])
        self.entry.reported_shift_nm=.99;self.assertFalse(evidence_json(self.entry)['qualifies'])
        result=self.client.post('/api/tuning-mappings/',self.payload(evidence_keys=['echidna-t158a']),format='json')
        self.assertEqual(result.status_code,400)
        self.assertIn('contextual',result.data['error'])

    def test_actual_spider_mapping_uses_bovine_numbering(self):
        result=self.client.post('/api/tuning-mappings/',self.payload(),format='json')
        self.assertEqual(result.status_code,200,result.data)
        row=result.data['rows'][0];self.assertEqual((row['source_position'],row['reference_position'],row['target_position'],row['target_residue']),(181,181,194,'E'))
        self.assertEqual(row['shift_nm_in_source'],-8);self.assertEqual(result['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/api/tuning-mappings/').status_code,405)

    def test_custom_numbering_and_cross_family_flag(self):
        result=map_sites(self.proteins,[self.entry],fasta('>bovine\n'+self.bovine.sequence),'bovine',custom=fasta('>My reference\n'+self.spider.sequence)[0],target_family='C_OPSIN')
        row=result['rows'][0];self.assertEqual(row['reference_position'],194);self.assertEqual(row['target_position'],181)
        self.assertIn('another opsin family',' '.join(row['warnings']))

    def test_fragment_missing_site_and_input_preserved(self):
        fragment=self.spider.sequence[:100];result=map_sites(self.proteins,[self.entry],fasta('>fragment\n'+fragment),'bovine',target_family='R_OPSIN')
        self.assertIsNone(result['rows'][0]['target_position']);self.assertEqual(result['rows'][0]['status'],'ABSENT')
        target=next(s for s in result['alignment']['sequences'] if s['key']=='t0');self.assertEqual(target['aligned'].replace('-',''),fragment)

    def test_ambiguous_residue_flag(self):
        seq=self.spider.sequence[:193]+'X'+self.spider.sequence[194:]
        result=map_sites(self.proteins,[self.entry],fasta('>uncertain\n'+seq),'bovine',target_family='R_OPSIN')
        self.assertEqual(result['rows'][0]['status'],'UNCERTAIN')

    def test_alignment_disagreement_withholds_coordinate(self):
        seq=self.bovine.sequence
        profile=read_alignment((CATALOGUE.parent/'reference-profile.fasta').read_text())
        first={**profile,'t0':profile['bovine']}
        second=dict(first);position=coordinate_maps(first)['bovine'][0][181]
        # Swap a residue and adjacent gap without changing ungapped input.
        second={key:value[:position]+'-'+value[position:] for key,value in first.items()}
        second['t0']=first['t0'][:position+1]+'-'+first['t0'][position+1:]
        with patch('core.tuning_mapping.run_mafft',side_effect=[first,second]):
            result=map_sites(self.proteins,[self.entry],fasta(seq),'bovine',target_family='R_OPSIN')
        self.assertEqual(result['rows'][0]['status'],'AMBIGUOUS');self.assertIsNone(result['rows'][0]['target_position'])

    def test_approval_dependencies_and_public_export_population(self):
        def public():return {v['key'] for v in self.client.get('/api/tuning-sites/').data['results']}
        self.assertIn(self.entry.key,public())
        for state in ['PENDING','REJECTED']:
            self.entry.status=state;self.entry.save();self.assertNotIn(self.entry.key,public())
        self.entry.status='APPROVED';self.entry.save();self.assertIn(self.entry.key,public())
        ref=self.entry.citations.first().reference;ref.status='PENDING';ref.save();self.assertNotIn(self.entry.key,public())
        denied=self.client.post('/api/tuning-mappings/',self.payload(),format='json');self.assertEqual(denied.status_code,400)

    def test_unapproved_protein_or_assay_hides_evidence(self):
        self.bovine.status='PENDING';self.bovine.save();self.assertEqual(self.client.get('/api/tuning-sites/').data['count'],5)
        self.bovine.status='APPROVED';self.bovine.save()
        assay=HeterologousData.objects.create(lambda_max=527,status='PENDING');self.entry.mutant_assay=assay;self.entry.save()
        self.assertNotIn(self.entry.key,[e['key'] for e in self.client.get('/api/tuning-sites/').data['results']])

    def test_catalogue_and_audit_are_not_publicly_writable(self):
        for path in ['/api/tuning-sites/','/api/tuning-templates/']:
            for verb in ['post','put','patch','delete']:
                self.assertEqual(getattr(self.client,verb)(path,{'status':'APPROVED'},format='json').status_code,405)
        self.assertEqual(self.client.post('/api/tuning-mappings/',self.payload(status='APPROVED'),format='json').status_code,400)
        self.assertEqual(self.client.get('/api/tuning-audits/').status_code,404)

    def test_pending_vpod_sequence_not_retrievable_via_mapping(self):
        opsin=Opsin.objects.create(protein_sequence=self.spider.sequence,status='PENDING')
        payload=self.payload(fasta='',opsin_ids=[opsin.pk]);self.assertEqual(self.client.post('/api/tuning-mappings/',payload,format='json').status_code,400)
        opsin.status='APPROVED';opsin.save();response=self.client.post('/api/tuning-mappings/',payload,format='json');self.assertEqual(response.status_code,200)

    def test_template_sequence_drift_fails_closed(self):
        self.bovine.sequence+='A'
        proteins=[self.bovine if p.key=='bovine' else p for p in self.proteins]
        with self.assertRaisesRegex(MappingError,'differs'):map_sites(proteins,[self.entry],fasta(self.spider.sequence),'bovine')

    def test_no_sequence_persistence(self):
        counts=(Opsin.objects.count(),TuningEvidence.objects.count(),TuningAudit.objects.count())
        original=tempfile.TemporaryDirectory;created=[]
        def tracked(*args,**kwargs):
            folder=original(*args,**kwargs);created.append(Path(folder.name));return folder
        with patch('core.tuning_mapping.tempfile.TemporaryDirectory',side_effect=tracked):
            response=self.client.post('/api/tuning-mappings/',self.payload(),format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(counts,(Opsin.objects.count(),TuningEvidence.objects.count(),TuningAudit.objects.count()))
        self.assertTrue(created);self.assertFalse(any(p.exists() for p in created))

    def test_admin_approval_is_audited_and_private(self):
        from django.contrib.auth.models import User
        from django.test import RequestFactory
        from django.contrib import admin
        from core.tuning_admin import EvidenceAdmin
        curator=User.objects.create_superuser('tuning-curator','curator@example.test','test-password')
        self.entry.status='PENDING';self.entry.save()
        request=RequestFactory().post('/admin/');request.user=curator
        self.entry.status='APPROVED'
        EvidenceAdmin(TuningEvidence,admin.site).save_model(request,self.entry,Mock(),change=True)
        audit=TuningAudit.objects.filter(object_key=self.entry.key,actor=f'admin:{curator.pk}').latest('pk')
        self.assertEqual(audit.before['status'],'PENDING');self.assertEqual(audit.after['status'],'APPROVED')
        public=self.client.get('/api/tuning-sites/').data
        self.assertIn(self.entry.key,[e['key'] for e in public['results']])
        self.assertNotIn('curator@example.test',json.dumps(public))
        ordinary=User.objects.create_user('ordinary',password='test-password',is_staff=True)
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(f'/admin/core/tuningevidence/{self.entry.pk}/change/').status_code,403)

    def test_import_does_not_link_a_reused_id_to_a_changed_assay(self):
        import copy
        data=copy.deepcopy(RELEASE)
        entry=next(e for e in data['evidence'] if e['key']=='echidna-n83d')
        entry['key']='different-source-fingerprint';data['evidence']=[entry]
        ref=Reference.objects.get(doi='10.1017/s0952523812000223')
        HeterologousData.objects.create(pk=1,lambda_max=497.9,mutations='D83N',reference=ref,status='APPROVED')
        with tempfile.TemporaryDirectory() as tmp:
            artifact=Path(tmp)/'seed.json';artifact.write_text(json.dumps(data))
            call_command('import_tuning_catalogue',catalogue=str(artifact),apply=True,output=tmp+'/r.json',stdout=io.StringIO())
            report=json.loads(Path(tmp+'/r.json').read_text())
        imported=TuningEvidence.objects.get(key=entry['key'])
        self.assertIsNone(imported.wild_type_assay_id)
        self.assertTrue(any(v['expected_hetid']==1 and 'fingerprint' in v['reason'] for v in report['unavailable_assay_links']))
