"""Mutation identity, preserved WT proteins, source merges and automatic moderation."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.management import call_command
from rest_framework.test import APIClient
from .models import Reference, Opsin, HeterologousData, TuningCandidate, TuningAudit, TuningEvidence
from .test_tuning_extraction import BOVINE, FakeCache
from .mutation_accessions import split_accession, tagged_accession
from .tuning_mutations import repair_inventory, apply_source_corrections
from .tuning_repairs import merge_duplicates, synchronize_merged_accessions, source_protein
from .tuning_extraction import inventory, analyze, store_analysis, auto_approve
from .tuning_views import public_evidence


class MutationRepairTests(TestCase):
    def setUp(self):
        self.ref = Reference.objects.create(doi='10.1234/example', status='APPROVED')
        self.protein = Opsin.objects.create(genus='Bos', species='taurus', accession='NM_001014890.2',
            gene_family='RH1', phylum='Chordata', protein_sequence=BOVINE, status='APPROVED', reference=self.ref)
        self.wt = HeterologousData.objects.create(opsin=self.protein, reference=self.ref, mutations='', lambda_max=500, status='APPROVED')
        self.mut = HeterologousData.objects.create(opsin=self.protein, reference=self.ref, mutations='D83N', lambda_max=502, status='APPROVED')

    def repair(self, **options):
        return repair_inventory(cache=FakeCache(), **options)

    def analyze(self):
        rows = inventory(); row = next(r for r in rows if r['hetid'] == self.mut.pk)
        d = analyze(row, rows, {}, FakeCache(), {})
        store_analysis(d); return TuningCandidate.objects.get(assay=self.mut)

    def test_bare_accession_is_split_from_wt_and_never_reverses_mutation(self):
        report = self.repair(apply=True)
        self.mut.refresh_from_db(); self.protein.refresh_from_db()
        self.assertEqual(self.protein.protein_sequence, BOVINE)
        self.assertEqual(self.protein.accession, 'NM_001014890.2')
        self.assertNotEqual(self.mut.opsin_id, self.wt.opsin_id)
        self.assertEqual(self.mut.opsin.accession, 'NM_001014890.2_D83N')
        self.assertEqual(self.mut.opsin.protein_sequence, BOVINE[:82]+'N'+BOVINE[83:])
        self.assertEqual(report['counts'], {'tagged_verified_construct': 1})
        n = Opsin.objects.count(); audits = TuningAudit.objects.count()
        self.repair(apply=True)
        self.assertEqual(Opsin.objects.count(), n); self.assertEqual(TuningAudit.objects.count(), audits)

    def test_dry_run_does_not_change_records(self):
        self.repair(); self.mut.refresh_from_db()
        self.assertEqual(self.mut.opsin_id, self.wt.opsin_id)
        self.assertEqual(TuningAudit.objects.count(), 0)

    def test_correctly_tagged_sequence_is_not_rebuilt_even_if_inconsistent(self):
        self.mut.opsin = Opsin.objects.create(accession='NM_001014890.2_D83N', protein_sequence=BOVINE, status='APPROVED')
        self.mut.save()
        with patch.object(FakeCache, 'align', side_effect=AssertionError('Do not rebuild existing tagged sequences')):
            self.assertEqual(self.repair(apply=True)['counts'], {'already_tagged': 1})
        self.assertEqual(self.analyze().analysis['options'], [])

    def test_unknown_direction_gets_tag_but_not_fake_wt_as_mutant_sequence(self):
        self.mut.mutations='N83D'; self.mut.mutation_numbering='self'; self.mut.save()
        self.repair(apply=True); self.mut.refresh_from_db()
        self.assertEqual(self.mut.opsin.accession, 'NM_001014890.2_N83D')
        self.assertIsNone(self.mut.opsin.protein_sequence)
        self.assertTrue(self.mut.mutation_build['needs_sequence_review'])
        self.assertEqual(auto_approve(self.analyze()), 'no_unambiguous_comparison')
        self.assertEqual(self.protein.protein_sequence, BOVINE)

    def test_multiple_substitutions_have_one_canonical_suffix(self):
        self.mut.mutations='D83N, A292S'; self.mut.save(); self.repair(apply=True); self.mut.refresh_from_db()
        self.assertEqual(self.mut.opsin.accession, 'NM_001014890.2_D83N,A292S')
        self.assertEqual(self.mut.opsin.protein_sequence[82], 'N')
        self.assertEqual(self.mut.opsin.protein_sequence[291], 'S')
        self.assertEqual(tagged_accession('NM_1_D83N_A292S', 'D83N,A292S'), 'NM_1_D83N_A292S')
        self.assertEqual(split_accession('NM_001014890.2'), ('NM_001014890.2', None))

    def test_import_links_suffixed_csv_to_base_wt_then_builds_separate_mutant(self):
        from .management.commands.import_csvs import Command
        source={'Genus':' Bos ', 'Species':'taurus ', 'Accession':'NM_001014890.2_D83N',
                'Mutations':'D83N', 'LambdaMax':'502', 'refid':str(self.ref.pk), 'hetid':'9000'}
        protein,basis=source_protein(source)
        self.assertEqual(protein.pk,self.protein.pk); self.assertEqual(basis,'base_wt_for_mutant_construction')
        outcome,detail=Command().import_row('heterologous.csv',source)
        self.assertEqual(outcome,'imported')
        self.repair(apply=True,assay_ids=[9000])
        mutant=HeterologousData.objects.get(pk=9000)
        self.assertEqual(mutant.opsin.accession,'NM_001014890.2_D83N')
        self.assertNotEqual(mutant.opsin_id,self.protein.pk)
        self.assertEqual(mutant.opsin.protein_sequence[82],'N')

    def test_import_will_not_guess_different_taxonomy_or_conflicting_wt(self):
        source={'Genus':'Bos','Species':'other','Accession':'NM_001014890.2_D83N','Mutations':'D83N'}
        self.assertIsNone(source_protein(source)[0])
        source['Species']='taurus'
        Opsin.objects.create(genus='Bos',species='taurus',accession=self.protein.accession,protein_sequence='A'+BOVINE[1:],gene_family='RH1')
        self.assertIsNone(source_protein(source)[0])

    def test_long_construct_accession_is_preserved(self):
        label='D83N,A292S,'*12+'F261Y'
        protein=Opsin(accession='NM_001014890.2_'+label,protein_sequence=BOVINE)
        protein.full_clean();protein.save()
        self.assertGreater(len(protein.accession),100)
        self.assertEqual(Opsin.objects.get(pk=protein.pk).accession,'NM_001014890.2_'+label)

    def test_wrong_existing_suffix_is_retained_in_audit_and_withheld(self):
        mutant = Opsin.objects.create(accession='NM_001014890.2_D84N', protein_sequence=BOVINE[:82]+'N'+BOVINE[83:], genus='Bos', species='taurus', status='APPROVED')
        self.mut.opsin=mutant; self.mut.save(); self.repair(apply=True); self.mut.refresh_from_db()
        self.assertEqual(self.mut.opsin_id, mutant.pk)
        self.assertEqual(self.mut.opsin.accession, 'NM_001014890.2_D83N')
        self.assertTrue(self.mut.mutation_build['needs_sequence_review'])
        self.assertEqual(auto_approve(self.analyze()), 'no_unambiguous_comparison')
        # Curator explicitly retries after checking/correcting source values.
        self.repair(apply=True, retry_unresolved=True); self.mut.refresh_from_db()
        self.assertFalse(self.mut.mutation_build['needs_sequence_review'])
        self.assertEqual(auto_approve(self.analyze()), 'approved')

    def test_chimeras_are_not_changed(self):
        self.mut.mutations='chimera,D83N'; self.mut.save()
        self.assertEqual(self.repair(apply=True)['counts'], {'excluded_annotation': 1})
        self.mut.refresh_from_db(); self.assertEqual(self.mut.opsin_id, self.protein.pk)

    def test_zero_wavelength_never_becomes_large_tuning_shift(self):
        self.repair(apply=True); self.mut.refresh_from_db(); self.mut.lambda_max=0; self.mut.save()
        candidate=self.analyze()
        self.assertEqual(candidate.outcome,'missing_measured_wavelength')
        self.assertNotEqual(auto_approve(candidate),'approved')
        self.wt.lambda_max=0; self.wt.save(); self.mut.lambda_max=502; self.mut.save()
        self.assertEqual(self.analyze().analysis['options'], [])

    def test_automatic_approval_is_idempotent_and_does_not_claim_manual_review(self):
        self.repair(apply=True); c=self.analyze()
        self.assertEqual(auto_approve(c),'approved'); c.refresh_from_db()
        self.assertEqual(c.approval_mode,'AUTO'); self.assertIsNone(c.reviewed_by)
        self.assertFalse(c.numbering_confirmed); self.assertFalse(c.approval_policy['paper_review_claimed'])
        self.assertFalse(c.approval_policy['strict_conditions'])
        self.assertEqual(auto_approve(c),'existing_decision_preserved')
        self.assertEqual(TuningEvidence.objects.count(),1)
        self.assertTrue(public_evidence())

    @override_settings(VPOD_TUNING_STRICT_CONDITIONS=True)
    def test_optional_global_strict_conditions_withhold_missing_labels(self):
        self.repair(apply=True); c=self.analyze()
        self.assertIn('Strict',auto_approve(c)); self.assertEqual(TuningEvidence.objects.count(),0)

    def test_rejected_candidate_and_curator_choice_are_preserved(self):
        self.repair(apply=True); c=self.analyze(); c.decision='REJECTED'; c.save()
        self.assertEqual(auto_approve(c),'existing_decision_preserved')
        c.decision='PENDING'; c.review_note='Needs paper review'; c.save()
        self.assertEqual(auto_approve(c),'curator_review_preserved')

    def test_pending_source_prevents_automatic_approval(self):
        self.repair(apply=True); self.mut.refresh_from_db(); self.mut.status='PENDING'; self.mut.save()
        self.assertIn('source-data conflicts',auto_approve(self.analyze()))

    def test_public_submission_adds_suffix_and_stays_pending_without_reusing_wt(self):
        api=APIClient(); response=api.post('/api/submissions/', {'submission_type':'DATA','data_type':'Heterologous',
            'genus':'Bos','species':'taurus','doi':self.ref.doi,'lambda_max':502,'mutations':'D83N','accession':self.protein.accession},format='json')
        self.assertEqual(response.status_code,201,response.data)
        record=HeterologousData.objects.get(pk=response.data['record_id'])
        self.assertEqual(record.status,'PENDING'); self.assertEqual(record.opsin.accession,'NM_001014890.2_D83N')
        self.assertNotEqual(record.opsin_id,self.wt.opsin_id)
        for method in ('patch','put','delete'):
            response=getattr(api,method)(f'/api/heterologous/{self.mut.pk}/',{'status':'APPROVED'},format='json')
            self.assertEqual(response.status_code,405)

    def test_duplicate_merge_is_guarded_idempotent_and_not_public(self):
        self.repair(apply=True)
        copy=HeterologousData.objects.create(opsin=self.mut.opsin, reference=self.ref, mutations='D83N', lambda_max=502, status='APPROVED')
        def identity(a):
            return dict(genus=a.opsin.genus,species=a.opsin.species,lambda_max=a.lambda_max,mutations=a.mutations,doi=self.ref.doi,accession_base='NM_001014890')
        self.mut.refresh_from_db()
        entry={'source':copy.pk,'target':self.mut.pk,'expected_source':identity(copy),'expected_target':identity(self.mut),'evidence':['https://example.invalid/paper'],'reason':'Fixture: same experiment.'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'merges.json'; path.write_text(json.dumps({'merges':[entry]}))
            copy.lambda_max=503; copy.save()
            self.assertEqual(merge_duplicates(path,True)[0]['outcome'],'conflict')
            copy.lambda_max=502; copy.save()
            self.assertEqual(merge_duplicates(path,True)[0]['outcome'],'merged')
            self.assertEqual(merge_duplicates(path,True)[0]['outcome'],'already_merged')
        copy.refresh_from_db(); self.assertEqual(copy.duplicate_of_id,self.mut.pk)
        # Even a mistaken bulk approval cannot expose an archived duplicate.
        copy.status='APPROVED'; copy.save()
        self.assertEqual(APIClient().get(f'/api/heterologous/{copy.pk}/').status_code,404)
        self.assertEqual(HeterologousData.objects.count(),3)

    def test_source_correction_guard_preserves_curator_edits(self):
        item={'model':'HeterologousData','id':self.mut.pk,'expected':{'mutations':'wrong'},'set':{'mutations':'D83N'},'evidence':'https://example.invalid','reason':'fixture'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'patch.json';path.write_text(json.dumps({'corrections':[item]}))
            self.mut.mutations='D83E';self.mut.save()
            self.assertEqual(apply_source_corrections(path,True)[0]['outcome'],'conflict')
        self.mut.refresh_from_db();self.assertEqual(self.mut.mutations,'D83E')
