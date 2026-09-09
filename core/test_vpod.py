import csv
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, SimpleTestCase
from rest_framework.test import APIClient
from core.bibliography import classify_identifier, safe_url, normalize_methods, date_parts, citation_identity, crossref_metadata
from core.metadata_recovery import MetadataClient, recover
from core.models import Reference, VisualAcuity, CuratedSCP, Opsin, HeterologousData, ReferenceMetadataAudit, SubmissionReceipt
from core.management.commands.import_visual_acuity import REQUIRED_COLUMNS, SOURCE, parse_taxonomy
from core.admin import approve_records

MESSAGE = {'DOI': '10.1234/example', 'title': ['Resolving visual acuity in animal eyes'], 'author': [{'family': 'Smith'}], 'published-print': {'date-parts': [[2020]]}, 'published-online': {'date-parts': [[2019, 12, 9]]}, 'container-title': ['Journal of Vision'], 'volume': '7', 'page': '22-30'}
CITATION = 'Smith A. (2020). Resolving visual acuity in animal eyes. Journal of Vision 7, 22-30.'


class BibliographyTests(SimpleTestCase):
    def test_identifier_classification_and_safe_links(self):
        for raw in ['10.1234/example', ' DOI: 10.1234/EXAMPLE ', 'https://dx.doi.org/10.1234/example']:
            self.assertEqual(classify_identifier(raw)['doi'], '10.1234/example')
        self.assertEqual(classify_identifier(CITATION)['kind'], 'citation')
        self.assertEqual(classify_identifier('https://example.org/thesis')['kind'], 'url')
        self.assertIsNone(classify_identifier('missing')['doi'])
        self.assertEqual(classify_identifier('GenBank Search')['kind'], 'nonpublication')
        self.assertEqual(classify_identifier('http10.1234/example')['candidate_doi'], '10.1234/example')
        self.assertEqual(classify_identifier('http://dx.10.1016/j.visres.2016.06.013')['kind'], 'malformed_doi')
        for raw in ['javascript:alert(1)', 'data:text/html,x', '//evil.test', 'https://user:pass@example.org/', 'https://good.org/\nfoo', 'http://dx.10.1016/j.visres.2016.06.013', 'https://doi-org/10.1021/jp208107r']:
            self.assertIsNone(safe_url(raw))

    def test_partial_dates_and_precedence(self):
        self.assertEqual(date_parts({'date-parts': [[2020]]}), '2020')
        self.assertEqual(date_parts({'date-parts': [[2020, 2]]}), '2020-02')
        self.assertIsNone(date_parts({'date-parts': [[2020, 2, 30]]}))
        self.assertIsNone(date_parts({}))
        self.assertEqual(crossref_metadata(MESSAGE)['publication_date'], '2020')
        self.assertEqual(crossref_metadata(MESSAGE)['online_date'], '2019-12-09')
        self.assertIsNone(crossref_metadata({'created': {'date-parts': [[2026]]}})['publication_date'])

    def test_multiple_methods_uncertainty_and_other_labels(self):
        methods = normalize_methods('Hetereologous , MSP?, evolution, OPTICS, CSP // ERG')
        self.assertEqual(methods[0]['name'], 'Heterologous expression')
        self.assertTrue(methods[1]['uncertain'])
        self.assertEqual(methods[2]['kind'], 'unclassified')
        self.assertEqual(methods[3]['kind'], 'computational')
        self.assertEqual(methods[4]['name'], 'CSP')
        self.assertIsNone(normalize_methods('N/A'))

    def test_identity_requires_independent_evidence(self):
        self.assertTrue(citation_identity(CITATION, MESSAGE)[0])
        self.assertFalse(citation_identity(MESSAGE['title'][0], MESSAGE)[0])
        self.assertFalse(citation_identity(CITATION.replace('Smith', 'Jones'), MESSAGE)[0])
        self.assertFalse(citation_identity(CITATION.replace('2020', '2018'), MESSAGE)[0])

    def test_ambiguous_search_does_not_accept_first_result(self):
        client = Mock()
        response = {'url': 'https://api.crossref.org/works?query.bibliographic=x', 'retrieved_at': '2026-09-09T12:00:00+00:00', 'data': {'message': {'items': [MESSAGE, dict(MESSAGE, DOI='10.1234/second')]}}}
        client.search.return_value = response
        ref = Reference(refid=1, doi=CITATION)
        result = recover(ref, client)
        self.assertEqual(result['fields'], {})
        self.assertEqual(len(result['candidates']), 2)
        client.crossref.assert_not_called()

    def test_http_failure_cached_and_retry_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            client = MetadataClient(folder, retries=1)
            response = Mock(status_code=503, headers={})
            with patch.object(client.session, 'get', return_value=response) as get, patch('core.metadata_recovery.time.sleep'):
                first = client.get('https://api.crossref.org/works/10.1234/example')
                second = client.get('https://api.crossref.org/works/10.1234/example')
            self.assertEqual(get.call_count, 2)
            self.assertEqual(first, second)
            self.assertEqual(first['status'], 503)


class AcuityImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref = Reference.objects.create(refid=30, doi='10.1234/example', status='APPROVED')

    def write_acuity(self, rows):
        file = self.root / 'acuity.csv'
        with file.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(REQUIRED_COLUMNS))
            writer.writeheader()
            for r in rows: writer.writerow(dict({k: '' for k in REQUIRED_COLUMNS}, **r))
        return file

    def run_import(self, rows):
        path = self.write_acuity(rows)
        call_command('import_visual_acuity', str(path), report=str(self.root / 'report.json'), stdout=io.StringIO())
        return json.loads((self.root / 'report.json').read_text())

    def test_all_rows_nulls_raw_taxonomy_and_links(self):
        report = self.run_import([{'acuID': '1', 'Species': 'Apis mellifera', 'refid': '30', 'CPD': '.02', 'FellerRefID': '17'}, {'acuID': '2', 'Species': 'Trisopsis or Lestodiplosis sp.', 'refid': '999'}, {'acuID': '3', 'Species': 'Apis mellifera', 'refid': '30', 'CPD': '.02'}])
        self.assertEqual(report['counts'], {'imported': 3})
        first = VisualAcuity.objects.get(source_record_id='1')
        self.assertEqual((first.genus, first.species), ('Apis', 'mellifera'))
        self.assertEqual(first.reference, self.ref)
        self.assertEqual(first.feller_ref_id, '17')
        unknown = VisualAcuity.objects.get(source_record_id='2')
        self.assertIsNone(unknown.cpd); self.assertIsNone(unknown.genus); self.assertIsNone(unknown.reference)
        self.assertEqual(unknown.source_data['Species'], 'Trisopsis or Lestodiplosis sp.')
        self.assertEqual(VisualAcuity.objects.count(), 3)

    def test_repeat_import_protects_curator_values_and_status(self):
        row = {'acuID': '1', 'Species': 'Apis mellifera', 'refid': '30', 'CPD': '1'}
        self.run_import([row]); obj = VisualAcuity.objects.get()
        obj.cpd = 7; obj.status = 'REJECTED'; obj.save()
        report = self.run_import([dict(row, CPD='2', **{'BL (cm)': '2'})])
        obj.refresh_from_db()
        self.assertEqual((obj.cpd, obj.body_length_cm, obj.status), (7, 2, 'REJECTED'))
        self.assertEqual(len(report['rows'][0]['conflicts']), 1)
        self.run_import([dict(row, CPD='2', **{'BL (cm)': '2'})])
        self.assertEqual(VisualAcuity.objects.count(), 1)

    def test_invalid_numbers_are_reported_raw_and_retained_as_null(self):
        report = self.run_import([{'acuID': '1', 'CPD': 'NaN', 'BL (cm)': '-1', 'Δϕ (deg)': 'inf', 'Δρ (deg)': 'oops'}])
        obj = VisualAcuity.objects.get()
        self.assertIsNone(obj.cpd)
        self.assertEqual(obj.source_data['CPD'], 'NaN')
        self.assertEqual(sum('invalid_numeric' in f for f in report['rows'][0]['flags']), 4)

    def test_duplicate_ids_reported_and_dry_run_does_not_write(self):
        report = self.run_import([{'acuID': '1'}, {'acuID': '1'}])
        self.assertEqual(report['counts'], {'unresolved': 2})
        file = self.write_acuity([{'acuID': '2'}])
        call_command('import_visual_acuity', str(file), dry_run=True, report=str(self.root / 'dry.json'), stdout=io.StringIO())
        self.assertFalse(VisualAcuity.objects.exists())

    def test_numeric_constraints_and_public_requirement(self):
        for value in [-1, 0, float('inf'), float('nan')]:
            with self.assertRaises(ValidationError): VisualAcuity.objects.create(cpd=value)
        with self.assertRaises(ValidationError): VisualAcuity.objects.create(cpd=None)
        obj = VisualAcuity.objects.create(cpd=1)
        for value in [-1, 0, float('inf')]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                VisualAcuity.objects.filter(pk=obj.pk).update(cpd=value)


class EnrichmentTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)

    def test_import_enrich_reimport_and_approval_preservation(self):
        path = self.root / 'references.csv'
        path.write_text('refid,DOI,MOM,YOP,notes\n1,10.1234/example,"Heterolgous,MSP?",,\n')
        with patch('core.management.commands.import_csvs.ensure_source_publication_references', return_value={}):
            call_command('import_csvs', str(self.root), references_only=True, report=str(self.root/'import.json'), stdout=io.StringIO())
            ref = Reference.objects.get(pk=1); ref.status = 'REJECTED'; ref.save()
            response = {'url': 'https://api.crossref.org/works/10.1234/example', 'retrieved_at': '2026-09-09T12:00:00+00:00', 'data': {'message': MESSAGE}}
            with patch.object(MetadataClient, 'crossref', return_value=response):
                call_command('enrich_references', apply=True, output=str(self.root/'enrich.json'), cache=str(self.root/'cache'), stdout=io.StringIO())
            call_command('import_csvs', str(self.root), references_only=True, report=str(self.root/'again.json'), stdout=io.StringIO())
        ref.refresh_from_db()
        self.assertEqual(ref.title, MESSAGE['title'][0]); self.assertEqual(ref.year_of_publication, 2020)
        self.assertEqual(ref.publication_date, '2020'); self.assertEqual(ref.status, 'REJECTED')
        self.assertTrue(ref.measurement_methods[1]['uncertain'])
        self.assertTrue(ReferenceMetadataAudit.objects.filter(field='title', applied=True).exists())

    def test_curated_values_conflict_duplicates_and_database_only_inventory(self):
        Reference.objects.create(refid=900, doi='10.1234/example', title='Curator title', year_of_publication=2018)
        Reference.objects.create(refid=901, doi='10.1234/example')
        response = {'url': 'https://api.crossref.org/works/10.1234/example', 'retrieved_at': '2026-09-09T12:00:00+00:00', 'data': {'message': MESSAGE}}
        with patch.object(MetadataClient, 'crossref', return_value=response):
            call_command('enrich_references', output=str(self.root/'dry.json'), cache=str(self.root/'cache'), stdout=io.StringIO())
        dry = json.loads((self.root/'dry.json').read_text())
        self.assertEqual(dry['summary']['processed'], 2)
        self.assertEqual(dry['summary']['duplicate_groups'], 1)
        self.assertEqual(dry['summary']['conflict_fields'], 2)
        self.assertIsNone(Reference.objects.get(pk=901).title)
        self.assertFalse(ReferenceMetadataAudit.objects.exists())
        call_command('enrich_references', from_artifact=str(self.root/'dry.json'), apply=True, output=str(self.root/'applied.json'), stdout=io.StringIO())
        self.assertEqual(Reference.objects.get(pk=900).title, 'Curator title')
        self.assertEqual(Reference.objects.get(pk=901).title, MESSAGE['title'][0])

    def test_citation_doi_recovery_preserves_original_and_replays(self):
        Reference.objects.create(refid=1, doi=CITATION)
        response = {'url': 'https://api.crossref.org/works/10.1234/example', 'retrieved_at': '2026-09-09T12:00:00+00:00', 'data': {'message': MESSAGE}}
        search = dict(response, data={'message': {'items': [MESSAGE]}})
        with patch.object(MetadataClient, 'crossref', return_value=response), patch.object(MetadataClient, 'search', return_value=search):
            call_command('enrich_references', output=str(self.root/'dry.json'), cache=str(self.root/'cache'), stdout=io.StringIO())
        call_command('enrich_references', from_artifact=str(self.root/'dry.json'), apply=True, output=str(self.root/'applied.json'), stdout=io.StringIO())
        ref = Reference.objects.get(pk=1)
        self.assertEqual(ref.doi, '10.1234/example'); self.assertEqual(ref.raw_citation, CITATION)
        report = json.loads((self.root/'applied.json').read_text()); self.assertEqual(report['replay_conflicts'], [])


class SubmissionPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.ref = Reference.objects.create(doi='10.1234/example', title='Published', status='APPROVED')
        self.payload = {'submission_type': 'DATA', 'data_type': 'Visual Acuity', 'genus': 'Apis', 'species': 'mellifera', 'doi': 'https://doi.org/10.1234/EXAMPLE', 'cpd': .2, 'submitter_email': 'private@example.org'}

    def test_pending_acuity_reuses_reference_without_editing_it(self):
        response = self.client.post('/api/submissions/', dict(self.payload, notes='New unreviewed note'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        obj = VisualAcuity.objects.get(); self.assertEqual(obj.status, 'PENDING'); self.assertEqual(obj.reference, self.ref)
        self.assertEqual(self.client.get('/api/visual-acuity/').data, [])
        self.ref.refresh_from_db(); self.assertIsNone(self.ref.notes)
        self.assertEqual(SubmissionReceipt.objects.get().submitter_email, 'private@example.org')
        staff = User.objects.create_user(username='curator', is_staff=True)
        approve_records(None, Mock(user=staff), VisualAcuity.objects.filter(pk=obj.pk))
        data = self.client.get('/api/visual-acuity/').data
        self.assertEqual(len(data), 1); self.assertNotIn('private@example.org', json.dumps(data))
        self.assertEqual(self.client.get('/api/visual-acuity/?cpd_min=1').data, [])
        self.assertEqual(len(self.client.get('/api/visual-acuity/?search=Apis').data), 1)

    def test_new_reference_pending_and_rejected_reference_not_recreated(self):
        self.payload['doi'] = 'https://example.org/paper'
        response = self.client.post('/api/submissions/', self.payload, format='json')
        self.assertEqual(response.status_code, 201)
        ref = Reference.objects.get(source_url='https://example.org/paper'); self.assertEqual(ref.status, 'PENDING')
        ref.status = 'REJECTED'; ref.save()
        response = self.client.post('/api/submissions/', {'submission_type': 'PUBLICATION', 'doi': self.payload['doi'], 'relevance': 'Visual Acuity'}, format='json')
        self.assertEqual(response.data['reference_id'], ref.pk)
        ref.refresh_from_db(); self.assertEqual(ref.status, 'REJECTED')

    def test_rejects_status_and_invalid_measurements(self):
        for update in [{'status': 'APPROVED'}, {'cpd': None}, {'cpd': 0}, {'cpd': -1}, {'cpd': 'NaN'}, {'cpd': 'Infinity'}, {'body_length_cm': -2}, {'source_dataset': 'legacy'}, {'doi': 'javascript:alert(1)'}]:
            response = self.client.post('/api/submissions/', dict(self.payload, **update), format='json')
            self.assertEqual(response.status_code, 400, update)
        self.assertFalse(VisualAcuity.objects.exists())

    def test_no_public_or_ordinary_user_model_writes(self):
        opsin = Opsin.objects.create(status='APPROVED')
        het = HeterologousData.objects.create(opsin=opsin, lambda_max=500, status='APPROVED')
        scp = CuratedSCP.objects.create(lambda_max=500, status='APPROVED')
        acuity = VisualAcuity.objects.create(cpd=1, status='APPROVED')
        user = User.objects.create_user(username='submitter')
        for auth in [None, user]:
            self.client.force_authenticate(auth)
            for route, obj in [('references', self.ref), ('opsins', opsin), ('heterologous', het), ('scp', scp), ('visual-acuity', acuity)]:
                for method in ['patch', 'put', 'delete']:
                    response = getattr(self.client, method)(f'/api/{route}/{obj.pk}/', {'status': 'APPROVED'}, format='json')
                    self.assertIn(response.status_code, [403, 405], (route, method))
                self.assertIn(self.client.post(f'/api/{route}/', {}, format='json').status_code, [403, 405])
            self.assertIn(self.client.get('/api/submissions/').status_code, [401, 403])

    def test_public_populations_and_nested_relations(self):
        for state in ['APPROVED', 'PENDING', 'REJECTED']:
            ref = Reference.objects.create(title=state, status=state)
            VisualAcuity.objects.create(cpd=1, status=state, reference=ref)
        data = self.client.get('/api/visual-acuity/').data
        self.assertEqual(len(data), 1)
        pending = Reference.objects.get(title='PENDING')
        approved = VisualAcuity.objects.get(status='APPROVED'); approved.reference = pending; approved.save()
        self.assertIsNone(self.client.get('/api/visual-acuity/').data[0]['reference'])
        self.assertEqual(len(self.client.get('/api/references/?search=PENDING').data), 0)

    def test_existing_submission_types_preserved(self):
        for data_type in ['Heterologous', 'SCP']:
            response = self.client.post('/api/submissions/', dict(self.payload, data_type=data_type, lambda_max=500), format='json')
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(HeterologousData.objects.get().status, 'PENDING'); self.assertEqual(CuratedSCP.objects.get().status, 'PENDING')
        response = self.client.post('/api/submissions/', {'submission_type': 'PUBLICATION', 'doi': self.ref.doi, 'relevance': 'ERG'}, format='json')
        self.assertEqual(response.status_code, 201); self.assertEqual(response.data['reference_id'], self.ref.pk)

class AdditionalRecoveryTests(TestCase):
    def test_datacite_fallback_and_missing_date(self):
        client = Mock()
        client.crossref.return_value = {'status': 404, 'url': 'https://api.crossref.org/works/10.1234/data', 'retrieved_at': '2026-09-09T00:00:00+00:00'}
        client.datacite.return_value = {'url': 'https://api.datacite.org/dois/10.1234/data', 'retrieved_at': '2026-09-09T00:00:00+00:00', 'data': {'data': {'attributes': {'doi': '10.1234/data', 'titles': [{'title': 'A deposited dataset'}], 'publicationYear': 2020}}}}
        result = recover(Reference(refid=1, doi='10.1234/data'), client)
        self.assertEqual(result['provider'], 'DataCite'); self.assertEqual(result['fields']['publication_date'], '2020')
        client.datacite.return_value['data']['data']['attributes'].pop('publicationYear')
        result = recover(Reference(refid=1, doi='10.1234/data'), client)
        self.assertIsNone(result['fields']['publication_date'])

    def test_disputed_source_blocks_registry_acceptance(self):
        client = Mock()
        ref = Reference(refid=1, doi='10.1234/example', source_data={'YOP': 'DOI WRONG'})
        result = recover(ref, client)
        self.assertEqual(result['classification'], 'disputed'); self.assertEqual(result['fields'], {})
        client.crossref.assert_not_called()

    def test_source_helper_preserves_rejection(self):
        from core.source_references import ensure_source_publication_reference
        ref = Reference.objects.create(doi='10.1234/example', status='REJECTED')
        result = ensure_source_publication_reference('example', {'doi': ref.doi, 'label': 'Example', 'citation': CITATION, 'year_of_publication': 2020})
        self.assertEqual(result.pk, ref.pk); self.assertEqual(result.status, 'REJECTED')

    def test_encoded_doi_and_scp_distinct_publications(self):
        self.assertEqual(classify_identifier('10.1234%2Fexample')['doi'], '10.1234/example')
        for doi in ['10.1234/first', '10.1234/second']:
            ref = Reference.objects.create(doi=doi)
            CuratedSCP.objects.create(genus='Apis', species='mellifera', lambda_max=500, reference=ref)
        self.assertEqual(CuratedSCP.objects.count(), 2)

    def test_replay_dry_run_has_sequential_identity_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Reference.objects.create(refid=1, doi=CITATION)
            response = {'url': 'https://api.crossref.org/works/10.1234/example', 'retrieved_at': '2026-09-09T12:00:00+00:00', 'data': {'message': MESSAGE}}
            with patch.object(MetadataClient, 'crossref', return_value=response), patch.object(MetadataClient, 'search', return_value=dict(response, data={'message': {'items': [MESSAGE]}})):
                call_command('enrich_references', output=str(root/'dry.json'), cache=str(root/'cache'), stdout=io.StringIO())
            call_command('enrich_references', from_artifact=str(root/'dry.json'), output=str(root/'replay.json'), stdout=io.StringIO())
            self.assertEqual(json.loads((root/'replay.json').read_text())['replay_conflicts'], [])
            self.assertEqual(Reference.objects.get().doi, CITATION)
            self.assertFalse(ReferenceMetadataAudit.objects.exists())


class SCPDuplicateTests(TestCase):
    def setUp(self):
        self.ref = Reference.objects.create(doi='10.1234/paper', status='APPROVED')
        self.other = Reference.objects.create(doi='10.1234/different', status='APPROVED')
        self.values = dict(genus='Apis', species='mellifera', lambda_max=500, reference=self.ref, status='APPROVED')
        self.first = CuratedSCP.objects.create(**self.values)

    def test_same_publication_blocked_different_publication_allowed(self):
        with self.assertRaises(ValidationError):
            CuratedSCP.objects.create(**self.values)
        CuratedSCP.objects.create(**dict(self.values, reference=self.other))
        self.assertEqual(len(APIClient().get('/api/scp/').data), 2)

    def test_whitespace_case_and_duplicate_doi_reference_ids_blocked(self):
        alias = Reference.objects.create(doi='https://doi.org/10.1234/PAPER')
        with self.assertRaises(ValidationError):
            CuratedSCP.objects.create(**dict(self.values, genus=' apis ', species='MELLIFERA ', reference=alias))
        with self.assertRaises(IntegrityError), transaction.atomic():
            CuratedSCP.objects.bulk_create([CuratedSCP(**dict(self.values, genus='APIS ', species=' mellifera'))])

    def test_no_reference_is_not_an_exception_and_unknown_wavelength_not_deduplicated(self):
        CuratedSCP.objects.create(**dict(self.values, reference=None))
        with self.assertRaises(ValidationError):
            CuratedSCP.objects.create(**dict(self.values, reference=None))
        for wavelength in [None, 0, None, 0]:
            CuratedSCP.objects.create(**dict(self.values, lambda_max=wavelength))

    def test_retained_duplicates_hidden_without_deletion_or_status_change(self):
        duplicate = CuratedSCP.objects.create(**dict(self.values, duplicate_of=self.first))
        self.assertEqual(CuratedSCP.objects.count(), 2)
        self.assertEqual(duplicate.status, 'APPROVED')
        client = APIClient()
        self.assertEqual([r['scpid'] for r in client.get('/api/scp/').data], [self.first.pk])
        self.assertEqual(client.get(f'/api/scp/{duplicate.pk}/').status_code, 404)
        with self.assertRaises(ValidationError):
            CuratedSCP.objects.create(**dict(self.values, reference=self.other, duplicate_of=self.first))

    def test_public_duplicate_submission_returns_validation_error(self):
        payload = dict(submission_type='DATA', data_type='SCP', genus='Apis', species='mellifera', lambda_max=500, doi=self.ref.doi)
        client = APIClient()
        response = client.post('/api/submissions/', payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('lambda_max', response.data)
        self.assertFalse(SubmissionReceipt.objects.exists())
        response = client.post('/api/submissions/', dict(payload, doi=self.other.doi), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(CuratedSCP.objects.get(pk=response.data['record_id']).status, 'PENDING')
        response = client.post('/api/submissions/', dict(payload, duplicate_of=self.first.pk), format='json')
        self.assertEqual(response.status_code, 400)

    def test_duplicate_reconciliation_dry_run_and_apply_preserve_different_publications(self):
        alias = Reference.objects.create(doi=self.ref.doi, status='APPROVED')
        # Simulate pre-existing aliases or direct SQL; normal model validation rejects this.
        CuratedSCP.objects.bulk_create([CuratedSCP(**dict(self.values, reference=alias))])
        other = CuratedSCP.objects.create(**dict(self.values, reference=self.other))
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'duplicates.json')
            call_command('clear_scp_duplicates', report=path, stdout=io.StringIO())
            self.assertEqual(CuratedSCP.objects.filter(duplicate_of__isnull=True).count(), 3)
            call_command('clear_scp_duplicates', report=path, commit=True, stdout=io.StringIO())
            report = json.loads(Path(path).read_text())
            self.assertEqual(report['retained_duplicate_rows'], 1)
            self.assertEqual(report['deleted'], 0)
            self.assertEqual(CuratedSCP.objects.count(), 3)
            self.assertEqual(set(r['scpid'] for r in APIClient().get('/api/scp/').data), {self.first.pk, other.pk})
            call_command('clear_scp_duplicates', report=path, commit=True, stdout=io.StringIO())
            self.assertEqual(json.loads(Path(path).read_text())['newly_linked'], 0)


class ReferenceReviewExportTests(SimpleTestCase):
    def test_standalone_report_escapes_source_and_links_to_admin(self):
        from django.core.management.base import CommandError
        artifact = {'schema_version': 1, 'run_id': 'test', 'summary': {}, 'audit': [], 'duplicate_dois': [],
            'records': [{'refid': 17, 'fields': {}, 'identifier': '<script>alert(1)</script>', 'reason': '=unverified', 'attempts': [{'url': 'javascript:alert(1)'}], 'candidates': []}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'artifact.json').write_text(json.dumps(artifact))
            call_command('export_reference_review', artifact=str(root/'artifact.json'), output=str(root/'review.html'), csv_output=str(root/'review.csv'), stdout=io.StringIO())
            result = (root/'review.html').read_text()
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', result)
            self.assertNotIn('href="javascript:', result)
            self.assertIn('http://127.0.0.1:8000/admin/core/reference/17/change/', result)
            self.assertIn("'=unverified", (root/'review.csv').read_text(encoding='utf-8-sig'))
            with self.assertRaises(CommandError):
                call_command('export_reference_review', artifact=str(root/'artifact.json'), output=str(root/'review.html'), admin_base_url='javascript:alert(1)')


class HostConfigurationTests(SimpleTestCase):
    def configured_hosts(self, value=None):
        import os
        import runpy
        with patch.dict(os.environ), patch('dotenv.load_dotenv'):
            os.environ.pop('ALLOWED_HOSTS', None)
            if value is not None:
                os.environ['ALLOWED_HOSTS'] = value
            return runpy.run_path(str(Path(__file__).resolve().parents[1] / 'vpod_backend/settings.py'))['ALLOWED_HOSTS']

    def test_existing_production_host_survives_missing_or_empty_override(self):
        for value in [None, '', ' ,  ']:
            self.assertEqual(self.configured_hosts(value), ['visphys.eemb.ucsb.edu', 'localhost', '127.0.0.1'])

    def test_explicit_host_override_is_trimmed_and_respected(self):
        self.assertEqual(self.configured_hosts(' staging.example.org, localhost, '), ['staging.example.org', 'localhost'])

    def test_landing_accepts_production_host_and_rejects_unrelated_host_with_debug_off(self):
        from django.test import override_settings
        with override_settings(ALLOWED_HOSTS=self.configured_hosts(), DEBUG=False,
                STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                          'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
            self.assertEqual(self.client.get('/', HTTP_HOST='visphys.eemb.ucsb.edu').status_code, 200)
            self.assertEqual(self.client.get('/', HTTP_HOST='unrelated.invalid').status_code, 400)
