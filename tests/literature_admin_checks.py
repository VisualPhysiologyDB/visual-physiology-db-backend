"""Browser checks against an explicitly isolated database/server. Never run against deployment."""
import json
import os
import uuid
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vpod_backend.settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from core.models import LiteratureCandidate, Reference, DiscoveryReviewEvent
from playwright.sync_api import sync_playwright

if not str(settings.DATABASES['default']['NAME']).startswith('/tmp/vpod-discovery-browser'):
    raise SystemExit('Use a /tmp/vpod-discovery-browser* database and its matching local test server')
base = 'http://127.0.0.1:8766'
suffix = uuid.uuid4().hex[:10]
user = User.objects.create_superuser('discovery_browser_' + suffix, password=None)
client = Client()
client.force_login(user)
candidate = LiteratureCandidate.objects.create(title='Browser fixture: <script>window.untrusted=true</script> fish acuity',
    doi='10.1234/browser-discovery-' + suffix, source_url='javascript:alert(1)',
    metadata={'publication_date': '2026', 'year_of_publication': 2026, 'abstract': 'Fixture source text; not a paper.'}, categories=['Visual acuity'])
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1280, 'height': 900})
    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': base}])
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base + '/admin/core/literaturecandidate/')
    page.locator(f'a[href="/admin/core/literaturecandidate/{candidate.pk}/change/"]').click()
    page.wait_for_url(base + f'/admin/core/literaturecandidate/{candidate.pk}/change/')
    assert page.evaluate('window.untrusted') is None
    page.get_by_text('No safe source link', exact=True).wait_for()
    checks.append('Inbox displays source text safely and refuses unsafe links')
    def save():
        with page.expect_navigation(wait_until='domcontentloaded'):
            page.locator('input[name="_continue"]').click()
    page.locator('#id_decision').select_option('REJECTED')
    save()
    assert page.get_by_text('Write a reason', exact=False).is_visible()
    page.locator('#id_review_note').fill('Browser fixture rejection reason')
    save()
    candidate.refresh_from_db()
    assert candidate.decision == 'REJECTED' and candidate.reference_id is None
    checks.append('Reject requires a reason and creates a review-history event')
    page.locator('#id_decision').select_option('ACCEPTED')
    page.locator('#id_review_note').fill('Reconsidered fixture for browser workflow verification')
    save()
    candidate.refresh_from_db()
    assert candidate.decision == 'ACCEPTED' and candidate.reference.status == 'PENDING'
    assert candidate.reference.publication_date == '2026'
    assert DiscoveryReviewEvent.objects.filter(candidate=candidate).count() == 2
    checks.append('Explicit reconsideration creates only a pending Reference at supported date precision')
    page.locator('#id_data_status').select_option('COMPLETE')
    save()
    candidate.refresh_from_db()
    assert candidate.data_status == 'COMPLETE' and candidate.reference.status == 'PENDING'
    checks.append('Data-entry tracking does not approve publication')
    assert not page.locator('a.deletelink').count()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('#id_decision').is_visible()
    page.screenshot(path='/tmp/vpod-literature-inbox-mobile.png', full_page=True)
    checks.append('Mobile curator form remains usable; no delete action is offered')
    public = browser.new_context()
    response = public.request.get(base + '/api/references/')
    assert response.status == 200 and '10.1234/browser-discovery' not in response.text()
    assert public.request.get(base + '/').status == 200
    checks.append('Pending reference stays private and existing landing page returns 200')
    assert not errors, errors
    browser.close()
output = {'checks': checks, 'passed': len(checks), 'browser': 'Playwright Chromium', 'database': settings.DATABASES['default']['NAME']}
Path('reports/literature-browser-checks.json').write_text(json.dumps(output, indent=2))
print(json.dumps(output, indent=2))
