"""Site grouping, position-only selection and target-residue compatibility."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIClient
from .models import TuningProtein, TuningEvidence, TuningCitation
from .tuning_mapping import mutation_assessment, read_alignment, coordinate_maps
from .tuning_sites import site_groups, write_index, sequence_hash, profile_data
from .tuning_views import public_evidence
from .test_tuning_extraction import FakeCache, BOVINE


class ResidueCompatibilityTests(SimpleTestCase):
    def test_starting_residue_matches_without_predicting_effect(self):
        kind,note,target=mutation_assessment({'from':'D','to':'N'},(91,'D'))
        self.assertEqual((kind,target),('START_MATCHES','D91N'))
        self.assertIn('not a prediction',note)

    def test_mismatch_already_present_uncertain_gap_and_position_only(self):
        change={'from':'D','to':'N'}
        for residue,expected in [('A','RESIDUE_MISMATCH'),('N','ALREADY_PRESENT'),('X','UNCERTAIN')]:
            kind,note,notation=mutation_assessment(change,(91,residue))
            self.assertEqual(kind,expected);self.assertIsNone(notation)
        self.assertEqual(mutation_assessment(change,None)[0],'UNRESOLVED')
        self.assertEqual(mutation_assessment({'from':None,'to':None},(91,'A'))[0],'SITE_ONLY')


class GroupedSitesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with tempfile.TemporaryDirectory() as tmp:
            call_command('import_tuning_catalogue',apply=True,output=tmp+'/r.json',stdout=io.StringIO())

    def setUp(self):
        cache.clear(); self.client=APIClient()
        self.proteins=list(TuningProtein.objects.all())
        self.bovine=TuningProtein.objects.get(key='bovine');self.spider=TuningProtein.objects.get(key='spider')
        self.entry=TuningEvidence.objects.get(key='spider-e181d')
        self.site='bovine|R_OPSIN|bovine|181'
        self.payload={'fasta':'>spider\n'+self.spider.sequence,'site_keys':[self.site],'reference':'bovine','target_family':'R_OPSIN'}

    def add(self,key,protein,position,family='C_OPSIN'):
        e=TuningEvidence.objects.create(key=key,protein=protein,title='Synthetic grouping fixture',family=family,subtype='test',
            organism='Test species',category='MEASURED',changes=[{'position':position,'from':protein.sequence[position-1],'to':'A'}],
            baseline_label='Fixture WT',baseline_nm=500,mutant_nm=503,source_locator='Fixture only',release='test',status='APPROVED')
        TuningCitation.objects.create(evidence=e,reference=self.entry.citations.first().reference,role='PRIMARY')
        return e

    def test_different_numbering_joins_only_at_mapped_site(self):
        one=self.add('test-bovine-164',self.bovine,164)
        human=TuningProtein.objects.get(key='human-lws');two=self.add('test-human-180',human,180)
        same_number=self.add('test-human-164',human,164)
        groups=site_groups(public_evidence(),self.proteins)['bovine']
        grouped=next(g for g in groups if g['key']=='bovine|C_OPSIN|bovine|164')
        keys={m['evidence_key'] for m in grouped['members']}
        self.assertTrue({one.key,two.key}<=keys);self.assertNotIn(same_number.key,keys)
        self.assertTrue(grouped['reference_mapped'])

    def test_opsin_families_never_collapsed_into_one_group(self):
        e=self.add('test-ciliary-181',self.bovine,181)
        groups=site_groups(public_evidence(),self.proteins)['bovine']
        hits=[g for g in groups if g['position']==181 and g['protein_key']=='bovine']
        self.assertEqual({g['family'] for g in hits},{'C_OPSIN','R_OPSIN'})

    def test_missing_or_stale_index_uses_named_source_numbering(self):
        sequence='A'+BOVINE[1:]
        p=TuningProtein.objects.create(key='source-test',name='Unindexed source',family='C_OPSIN',sequence=sequence,
            numbering_note='Exact source',provenance={'kind':'extracted_source','sequence_sha256':sequence_hash(sequence)},status='APPROVED')
        e=self.add('test-unindexed',p,83)
        with patch('core.tuning_sites.read_index',return_value={'profile_sha256':'stale','sequences':{sequence_hash(sequence):{'bovine':[1]*len(sequence)}}}):
            group=next(g for g in site_groups([e],[*self.proteins,p])['bovine'] if g['members'])
        self.assertFalse(group['reference_mapped']);self.assertEqual(group['protein_key'],p.key)

    def test_index_checks_profile_and_sequence_and_does_not_store_targets(self):
        p=TuningProtein.objects.create(key='source-test',name='Indexed source',family='C_OPSIN',sequence='A'+BOVINE[1:],
            numbering_note='Exact source',provenance={'kind':'extracted_source'},status='APPROVED')
        e=self.add('test-indexed',p,83)
        with tempfile.TemporaryDirectory() as tmp,override_settings(VPOD_TUNING_SITE_INDEX_PATH=tmp+'/index.json'):
            report=write_index([p],FakeCache());self.assertEqual(report['indexed_sequences'],1)
            group=site_groups([e],[*self.proteins,p])['bovine'][0]
            self.assertTrue(group['reference_mapped']);self.assertEqual(group['position'],83)
            p.sequence='G'+p.sequence[1:]
            self.assertFalse(site_groups([e],[*self.proteins,p])['bovine'][0]['reference_mapped'])

    def test_site_only_maps_position_once_without_prescribing_substitution(self):
        response=self.client.post('/api/tuning-mappings/',self.payload,format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(len(response.data['rows']),1)
        row=response.data['rows'][0]
        self.assertEqual((row['target_position'],row['target_residue']),(194,'E'))
        self.assertEqual(row['selection_kind'],'SITE');self.assertEqual(row['mutation_status'],'SITE_ONLY')
        for name in ('source_from','source_to','target_mutation','shift_nm_in_source'):self.assertIsNone(row[name])
        self.assertTrue(response.data['evidence']);self.assertEqual(response.data['selected_site_keys'],[self.site])
        self.assertEqual(response.data['selected_keys'],[])

    def test_site_and_mutation_can_be_selected_together(self):
        r=self.client.post('/api/tuning-mappings/',{**self.payload,'evidence_keys':[self.entry.key]},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual({row['selection_kind'] for row in r.data['rows']},{'SITE','MUTATION'})
        mutation=next(row for row in r.data['rows'] if row['selection_kind']=='MUTATION')
        self.assertEqual(mutation['mutation_status'],'START_MATCHES')
        self.assertEqual(mutation['target_mutation'],'E194D')

    def test_each_target_checks_its_own_residue_and_never_silently_substitutes(self):
        sequences='\n'.join('>'+name+'\n'+self.spider.sequence[:193]+aa+self.spider.sequence[194:] for name,aa in [('matching','E'),('different','A'),('already','D'),('unknown','X')])
        r=self.client.post('/api/tuning-mappings/',{**self.payload,'fasta':sequences,'site_keys':[],'evidence_keys':[self.entry.key]},format='json')
        self.assertEqual(r.status_code,200,r.data)
        rows={row['target_name']:row for row in r.data['rows']}
        self.assertEqual({k:v['mutation_status'] for k,v in rows.items()},{'matching':'START_MATCHES','different':'RESIDUE_MISMATCH','already':'ALREADY_PRESENT','unknown':'UNCERTAIN'})
        self.assertIsNone(rows['different']['target_mutation']);self.assertIsNone(rows['already']['target_mutation'])
        self.assertEqual(rows['different']['status'],'REVIEW')
        self.assertEqual(r.data['targets'][1]['sequence'][193],'A')

    def test_sites_do_not_bypass_approval_or_cutoff(self):
        TuningEvidence.objects.exclude(pk=self.entry.pk).update(status='REJECTED')
        self.entry.reported_shift_nm=.5;self.entry.save()
        r=self.client.post('/api/tuning-mappings/',self.payload,format='json');self.assertEqual(r.status_code,400)
        self.entry.status='PENDING';self.entry.save()
        self.assertEqual(self.client.get('/api/tuning-sites/').data['site_groups']['bovine'],[])
        self.assertEqual(self.client.post('/api/tuning-mappings/',self.payload,format='json').status_code,400)

    def test_unknown_duplicate_and_overlarge_site_requests_rejected(self):
        for sites in [['untrusted'],[self.site,self.site],[self.site]*101,'not-a-list',[{}]]:
            r=self.client.post('/api/tuning-mappings/',{**self.payload,'site_keys':sites},format='json')
            self.assertEqual(r.status_code,400,r.data)

    def test_combination_stays_whole_but_site_only_has_no_combined_shift(self):
        self.entry.changes=[{'position':181,'from':'E','to':'D'},{'position':186,'from':'S','to':'A'}];self.entry.save()
        r=self.client.post('/api/tuning-mappings/',{**self.payload,'evidence_keys':[self.entry.key]},format='json')
        self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(len(r.data['rows']),3)
        self.assertEqual([row['combination_size'] for row in r.data['rows'] if row['selection_kind']=='MUTATION'],[2,2])
        self.assertIsNone(next(row['shift_nm_in_source'] for row in r.data['rows'] if row['selection_kind']=='SITE'))

    def test_site_index_command_is_read_only_for_database(self):
        before=TuningProtein.objects.count(),TuningEvidence.objects.count()
        with tempfile.TemporaryDirectory() as tmp:
            call_command('index_tuning_sites',output=tmp+'/index.json',stdout=io.StringIO())
            self.assertTrue(Path(tmp+'/index.json').exists())
        self.assertEqual(before,(TuningProtein.objects.count(),TuningEvidence.objects.count()))
