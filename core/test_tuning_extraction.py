"""Single-site extraction, species/WT matching, review and publication boundaries."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient
from core.models import Reference, Opsin, HeterologousData, TuningCandidate, TuningEvidence
from core.tuning_extraction import (mutation, base_accession, comparator_pool, family_hint, hypotheses,
    analyze, inventory, store_analysis, validate_review, publish_candidate, AlignmentCache)
from core.tuning_mapping import read_alignment, coordinate_maps, map_sites, fasta
from core.tuning_views import public_evidence
from core.tuning_admin import CandidateForm

RELEASE = json.loads((Path(settings.BASE_DIR) / 'data/tuning/catalogue.json').read_text())
BOVINE = next(p['sequence'] for p in RELEASE['proteins'] if p['key'] == 'bovine')


class FakeCache:
    """Stable known profile for deterministic unit tests; real MAFFT tested separately."""
    identity = {'test_version': 1}
    def align(self, sequence):
        profile = read_alignment((Path(settings.BASE_DIR) / 'data/tuning/reference-profile.fasta').read_text())
        chars = iter(sequence)
        profile['query'] = ''.join('-' if a == '-' else next(chars) for a in profile['bovine'])
        return [profile, dict(profile)]


class ParserTests(SimpleTestCase):
    def test_strict_single_site_not_multi_chimera_or_label(self):
        expected = {'D83N': 'substitution', 'D83D': 'no_change', 'F86del': 'deletion', 'ins86F': 'insertion',
                    'D83N,A292S': 'multiple', 'Chimeric construct': 'chimera', 'SWS2': 'unparsed', '': 'wild_type', 'WT': 'wild_type', 'no mutations': 'wild_type'}
        self.assertEqual({s: mutation(s)['kind'] for s in expected}, expected)
        self.assertEqual(base_accession({'accession': 'NM_001014890.2_D83N'}), 'NM_001014890')
        self.assertEqual(base_accession({'accession': 'NM_001014890_D83N'}), 'NM_001014890')

    def test_phylum_does_not_make_all_invertebrates_rhabdomeric(self):
        self.assertEqual(family_hint({'phylum': 'Annelida', 'gene_family': 'IV-Opn3'}), 'OTHER')
        self.assertEqual(family_hint({'phylum': 'Mollusca', 'gene_family': 'unclassified'}), 'OTHER')


class ExtractionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with tempfile.TemporaryDirectory() as tmp:
            call_command('import_tuning_catalogue', apply=True, output=tmp+'/r.json', stdout=io.StringIO())
        cls.user = User.objects.create_superuser('curator', 'curator@example.invalid', 'test-only')

    def setUp(self):
        self.ref = Reference.objects.create(doi='10.1234/test', status='APPROVED')
        self.other_ref = Reference.objects.create(doi='10.1234/other', status='APPROVED')
        self.wt = self.assay(BOVINE, '', 500, 'NM_001014890.2')
        self.mut = self.assay(BOVINE[:82]+'N'+BOVINE[83:], 'D83N', 502, 'NM_001014890_D83N')

    def assay(self, sequence, notation, nm, accession, **overrides):
        protein = Opsin.objects.create(protein_sequence=sequence, genus=overrides.pop('genus', 'Bos'),
            species=overrides.pop('species', 'taurus'), gene_family='RH1', phylum='Chordata',
            accession=accession, status='APPROVED')
        return HeterologousData.objects.create(opsin=protein, mutations=notation, lambda_max=nm,
            reference=overrides.pop('reference', self.ref), status='APPROVED', cell_culture='HEK293S', **overrides)

    def analysis(self):
        rows = inventory(); row = next(r for r in rows if r['hetid'] == self.mut.pk)
        return analyze(row, rows, {}, FakeCache(), {})

    def reviewed_candidate(self):
        d = self.analysis(); store_analysis(d); c = TuningCandidate.objects.get(assay=self.mut)
        c.selection = d['options'][0]['key']; c.decision = 'APPROVED'
        c.numbering_confirmed = True; c.conditions_confirmed = True
        c.review_note = 'Synthetic test fixture, Table 1: numbering and matched conditions checked.'
        return c

    def test_numbering_priority_bovine_first_equivalent_self_retained(self):
        d = self.analysis()
        self.assertEqual([h['scheme'] for h in d['hypotheses']], ['bovine', 'squid', 'self', 'spider'])
        self.assertEqual(d['suggested_selection'], f'bovine:{self.wt.pk}:83')
        self.assertTrue(any(o['scheme'] == 'self' for o in d['options']))

    def test_human_is_this_opsin_not_automatically_lws(self):
        self.mut.opsin.genus = 'Homo'; self.mut.opsin.species = 'sapiens'; self.mut.opsin.gene_family = 'SWS1'; self.mut.opsin.save()
        d = self.analysis()
        self.assertEqual([h['scheme'] for h in d['hypotheses']], ['bovine', 'human', 'squid', 'self', 'spider'])
        human = next(h for h in d['hypotheses'] if h['scheme'] == 'human')
        self.assertEqual(human['source_position'], 83)

    def test_sequence_fallback_same_species_no_mutations_required(self):
        self.wt.opsin.accession = 'UNRELATED_ID'; self.wt.opsin.gene_family = 'unknown'; self.wt.opsin.save()
        other_species = self.assay(BOVINE, '', 500, 'X', species='other')
        not_wt = self.assay(BOVINE, 'A292S', 500, 'Y')
        inferred = self.assay(BOVINE, '', 500, 'Z', is_inferred=True)
        d = self.analysis()
        self.assertEqual({o['comparator_id'] for o in d['options']}, {self.wt.pk})
        self.assertEqual(d['options'][0]['match_basis'], 'one_residue_same_species')

    def test_accession_match_is_not_enough_when_sequence_differs_twice(self):
        self.wt.opsin.protein_sequence = 'A'+BOVINE[1:]; self.wt.opsin.save()
        self.assertEqual(self.analysis()['options'], [])

    def test_explicit_wild_type_label_is_eligible(self):
        self.wt.mutations='WT'; self.wt.save()
        self.assertTrue(self.analysis()['options'])

    def test_duplicate_wt_observations_are_ambiguous_not_arbitrarily_chosen(self):
        self.assay(BOVINE, '', 499, 'NM_001014890.2')
        d = self.analysis()
        self.assertEqual(d['outcome'], 'ambiguous_comparator_or_numbering')
        self.assertNotIn('suggested_selection', d)

    def test_accession_then_same_publication_preference_keeps_alternatives(self):
        self.assay(BOVINE, '', 499, 'different-accession')
        self.assay(BOVINE, '', 498, 'NM_001014890.2', reference=self.other_ref)
        d = self.analysis()
        self.assertEqual(d['suggested_selection'], f'bovine:{self.wt.pk}:83')
        self.assertEqual(len({o['comparator_id'] for o in d['options']}), 3)

    def test_whitespace_normalized_but_raw_sequence_retained(self):
        raw = self.mut.opsin.protein_sequence+'\n'
        self.mut.opsin.protein_sequence = raw; self.mut.opsin.save()
        d = self.analysis()
        self.assertTrue(d['options'])
        self.assertEqual(d['inputs']['assay']['protein_sequence_raw'], raw)
        self.assertNotIn('\n', d['inputs']['assay']['protein_sequence'])

    def test_rejected_stale_candidate_can_be_explicitly_reconsidered(self):
        c = self.reviewed_candidate(); c.decision='REJECTED'; c.save()
        self.mut.lambda_max=503; self.mut.save(); store_analysis(self.analysis()); c.refresh_from_db()
        form=CandidateForm(instance=c,data={'selection':c.selection,'numbering_confirmed':True,'conditions_confirmed':True,
            'acknowledge_changes':True,'review_note':'Table 1: corrected source and numbering checked.','decision':'APPROVED'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertFalse(form.instance.stale)

    def test_cross_publication_keeps_warning_and_both_citations(self):
        self.wt.reference = self.other_ref; self.wt.save()
        c = self.reviewed_candidate()
        self.assertTrue(validate_review(c)['cross_publication'])
        publish_candidate(c, self.user); c.save()
        self.assertEqual(set(c.evidence.citations.values_list('reference_id', flat=True)), {self.ref.pk, self.other_ref.pk})

    def test_duplicate_doi_reference_ids_are_same_publication_without_merging(self):
        self.other_ref.doi = 'https://doi.org/10.1234/test'; self.other_ref.save()
        self.wt.reference = self.other_ref; self.wt.save()
        d = self.analysis()
        self.assertFalse(d['options'][0]['cross_publication'])
        self.assertEqual(Reference.objects.filter(pk__in=[self.ref.pk,self.other_ref.pk]).count(),2)

    def test_unprivileged_staff_cannot_read_or_approve_candidate(self):
        d = self.analysis(); store_analysis(d); c = TuningCandidate.objects.get(assay=self.mut)
        staff = User.objects.create_user('ordinary_staff',is_staff=True)
        self.client.force_login(staff)
        url=f'/admin/core/tuningcandidate/{c.pk}/change/'
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,{'decision':'APPROVED'}).status_code,403)

    def test_optional_conditions_but_below_cutoff_still_blocks_approval(self):
        c = self.reviewed_candidate(); c.conditions_confirmed = False
        self.assertTrue(validate_review(c)['condition_warnings'])
        c.require_condition_match = True
        with self.assertRaisesRegex(ValidationError, 'Strict'): validate_review(c)
        self.mut.lambda_max = 500.5; self.mut.save(); c = self.reviewed_candidate()
        with self.assertRaisesRegex(ValidationError, 'below'): validate_review(c)

    def test_conflicting_cell_culture_blocks_only_when_strict(self):
        self.wt.cell_culture = 'other'; self.wt.save(); c = self.reviewed_candidate()
        self.assertIn('Cell cultures differ.', validate_review(c)['condition_warnings'])
        c.require_condition_match = True
        with self.assertRaisesRegex(ValidationError, 'Strict'): validate_review(c)

    def test_reimport_preserves_rejection_and_new_source_marks_stale(self):
        d = self.analysis(); self.assertEqual(store_analysis(d), 'created')
        c = TuningCandidate.objects.get(assay=self.mut); c.decision = 'REJECTED'; c.review_note = 'Bad original assignment'; c.save()
        self.assertEqual(store_analysis(d), 'unchanged')
        self.mut.lambda_max = 503; self.mut.save(); store_analysis(self.analysis())
        c.refresh_from_db(); self.assertEqual(c.decision, 'REJECTED'); self.assertEqual(c.review_note, 'Bad original assignment'); self.assertTrue(c.stale)

    def test_pending_private_approved_visible_changed_source_hidden(self):
        c = self.reviewed_candidate()
        self.assertFalse(any(e.mutant_assay_id == self.mut.pk for e in public_evidence()))
        publish_candidate(c, self.user); c.save()
        self.assertTrue(any(e.pk == c.evidence_id for e in public_evidence()))
        self.wt.lambda_max = 501; self.wt.save()
        self.assertFalse(any(e.pk == c.evidence_id for e in public_evidence()))
        with self.assertRaisesRegex(ValidationError, 'changed'): validate_review(c)

    def test_admin_review_creates_one_assertion_and_audit_no_public_write_access(self):
        d = self.analysis(); store_analysis(d); c = TuningCandidate.objects.get(assay=self.mut)
        url = f'/admin/core/tuningcandidate/{c.pk}/change/'
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        payload = {'selection': d['options'][0]['key'], 'numbering_confirmed': 'on', 'conditions_confirmed': 'on',
                   'review_note': 'Table 1: synthetic fixture conditions and numbering checked.', 'decision': 'APPROVED', '_save': 'Save'}
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        c.refresh_from_db(); self.assertIsNotNone(c.evidence_id)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(TuningEvidence.objects.filter(mutant_assay=self.mut).count(), 1)
        api = APIClient()
        self.assertEqual(api.post('/api/tuning-sites/', {'decision':'APPROVED'}, format='json').status_code, 405)
        self.assertEqual(api.delete('/api/tuning-sites/').status_code, 405)
        c.decision = 'REJECTED'; c.save()
        self.assertFalse(any(e.pk == c.evidence_id for e in public_evidence()))

    def test_new_self_anchor_maps_without_changing_frozen_profile(self):
        c = self.reviewed_candidate(); publish_candidate(c, self.user); c.save()
        from core.models import TuningProtein
        proteins = list(TuningProtein.objects.all())
        result = map_sites(proteins, [c.evidence], fasta(BOVINE), 'bovine', target_family='C_OPSIN')
        self.assertEqual(result['rows'][0]['target_position'], 83)
        self.assertEqual(result['rows'][0]['reference_position'], 83)
        templates = APIClient().get('/api/tuning-templates/').json()
        self.assertNotIn(c.evidence.protein.key, {p['key'] for p in templates['results']})

    def test_command_reconciliation_dry_apply_rerun_and_replay_conflict(self):
        self.assay(BOVINE, 'F86del', 490, 'indel')
        self.assay(BOVINE, 'ins86F', 510, 'insertion')
        self.assay(BOVINE, 'D83N,A292S', 510, 'multiple')
        self.assay(BOVINE, 'Chimeric', 510, 'chimera')
        with tempfile.TemporaryDirectory() as tmp, patch('core.management.commands.build_tuning_candidates.AlignmentCache', return_value=FakeCache()):
            path = tmp+'/out.json'
            args = {'no_supplement': True, 'cache_dir':tmp, 'output':path, 'stdout':io.StringIO()}
            call_command('build_tuning_candidates', **args)
            self.assertEqual(TuningCandidate.objects.count(), 0)
            report = json.loads(Path(path).read_text()); self.assertEqual(len(report['inventory']), 6)
            self.assertEqual(report['single_site_counts'], {'substitution':1,'deletion':1,'insertion':1})
            call_command('build_tuning_candidates', apply=True, **args)
            call_command('build_tuning_candidates', apply=True, from_report=path, **args)
            self.assertEqual(TuningCandidate.objects.count(), 3)
            self.assertEqual(set(TuningCandidate.objects.values_list('decision',flat=True)), {'PENDING', 'APPROVED'})
            self.assertEqual(TuningCandidate.objects.get(assay=self.mut).approval_mode, 'AUTO')
            self.wt.lambda_max = 499; self.wt.save()
            with self.assertRaises(CommandError): call_command('build_tuning_candidates', apply=True, from_report=path, **args)

    def test_existing_reviewed_entry_not_duplicated(self):
        d = self.analysis(); d['existing_evidence'] = ['already-reviewed']; store_analysis(d)
        c = TuningCandidate.objects.get(assay=self.mut); c.decision='APPROVED'; c.selection=d['options'][0]['key']
        c.numbering_confirmed=True; c.conditions_confirmed=True; c.review_note='Table 1 reviewed against original source.'
        with self.assertRaisesRegex(ValidationError, 'already'): validate_review(c)

    def test_alignment_disagreement_withholds_coordinate(self):
        rows=inventory(); row=next(r for r in rows if r['hetid']==self.mut.pk)
        alignments=FakeCache().align(row['protein_sequence'])
        chars=list(alignments[1]['query']); index=coordinate_maps(alignments[1])['query'][0][83]
        chars[index]='-'; chars[index-1]='N'; alignments[1]['query']=''.join(chars)
        values=hypotheses(row,mutation('D83N'),alignments)
        self.assertIsNone(values[0]['source_position'])

    def test_real_alignment_cache_is_reusable_and_does_not_drop_insertions(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=AlignmentCache(tmp); first=cache.align(BOVINE)
            with patch('core.tuning_extraction.run_mafft',side_effect=AssertionError('Should use cache')):
                self.assertEqual(cache.align(BOVINE), first)
            self.assertEqual(first[0]['query'].replace('-',''),BOVINE)
