"""Run against an explicitly isolated localhost test instance. Never target production."""
import csv
import io
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get('VPOD_BROWSER_URL', 'http://127.0.0.1:8765')
if not BASE.startswith(('http://127.0.0.1:', 'http://localhost:')):
    raise SystemExit('Browser verification must target an isolated localhost server')
results = []

def record(name):
    results.append({'check': name, 'result': 'passed'})

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(has_touch=True, viewport={'width': 1280, 'height': 900}, permissions=['clipboard-read', 'clipboard-write'])
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('dialog', lambda d: d.accept())
    page.goto(BASE)
    page.wait_for_function("document.getElementById('recordCount').textContent.includes('Records Found')")
    for tab in ['opsins', 'heterologous', 'scp', 'references', 'visual-acuity']:
        page.locator('#tab-' + tab).click()
        page.wait_for_function("document.getElementById('recordCount').textContent.includes('Records Found')")
        assert page.locator('#tableBody tr').count() == page.evaluate('filteredData.length') > 0
        if tab != 'opsins':
            assert page.evaluate('chart !== null')
            assert page.evaluate('chart.data.datasets[0].data.reduce((a,b)=>a+b,0)') == page.evaluate("Number(document.getElementById('plotCount').textContent.split(' ')[0])")
        record('real API table and chart: ' + tab)
    assert page.evaluate('filteredData.length') == 549
    assert page.locator('#plotCount').inner_text() == '546 plotted · 3 excluded'
    page.locator('button', has_text='Show Advanced Filters').click()
    page.locator('#filterEyeType').fill('apposition')
    assert page.evaluate('filteredData.every(r => r.eye_type === "apposition")')
    page.locator('#filterCpdMin').fill('1')
    assert page.evaluate('filteredData.every(r => r.cpd >= 1)')
    assert page.evaluate('chart.data.datasets[0].data.reduce((a,b)=>a+b,0) === filteredData.length')
    page.locator('#filterEyeType').fill(''); page.locator('#filterCpdMin').fill('')
    page.locator('#filterCpdMissing').select_option('missing')
    assert page.locator('#tableBody tr').count() == 3
    assert page.locator('#plotCount').inner_text() == '0 plotted · 3 excluded'
    page.locator('#filterCpdMissing').select_option('')
    page.locator('#filterGenus').fill('Apis')
    assert page.evaluate('filteredData.length > 0 && filteredData.every(r => (r.genus || "").includes("Apis"))')
    page.locator('#filterGenus').fill('')
    page.locator('#searchInput').fill('no-species-with-this-name')
    assert page.locator('#emptyState').is_visible()
    page.locator('#searchInput').fill('')
    record('acuity eye type, species, range, missing, search and empty states')
    with page.expect_download() as download:
        page.locator('#exportDataButton').click()
    path = download.value.path()
    exported = list(csv.DictReader(open(path)))
    assert len(exported) == 549 and all(k in exported[0] for k in ['cpd', 'body_length_cm', 'interommatidial_angle_deg', 'acceptance_angle_deg', 'lens_diameter_mm', 'source_data'])
    page.locator('#copyDataButton').click()
    clipboard = page.evaluate('navigator.clipboard.readText()')
    assert len(list(csv.DictReader(io.StringIO(clipboard)))) == 549
    page.locator('#tableBody tr').first.locator('td').nth(5).locator('summary').click()
    assert page.locator('#tableBody details[open] dd').count() > 4
    record('all 549 acuity records export/copy and full source details')
    page.locator('button', has_text='Contribute Data').click()
    page.locator('#tier-data').click()
    page.locator('#subType').select_option('Visual Acuity')
    assert page.locator('#subLmax').is_disabled()
    assert page.locator('#subCpd').get_attribute('required') is not None
    page.locator('#subDoi').fill('10.1234/vpod-browser-test')
    page.locator('#subGenus').fill('Apis'); page.locator('#subSpecies').fill('mellifera'); page.locator('#subCpd').fill('0')
    before = page.request.get(BASE + '/api/visual-acuity/').json()
    page.locator('button[type=submit]').click()
    assert page.locator('#contributeModal').is_visible()
    page.locator('#subCpd').fill('0.25')
    with page.expect_response(lambda r: r.url.endswith('/api/submissions/') and r.request.method == 'POST') as response:
        page.locator('button[type=submit]').click()
    assert response.value.status == 201
    assert response.value.json()['status'] == 'PENDING'
    assert page.request.get(BASE + '/api/visual-acuity/').json() == before
    record('acuity frontend required/positive validation and real pending API submission')
    # Paginated and malicious external data fixture, deliberately kept out of the database.
    title = 'A very long publication title ' * 12 + '<img src=x onerror="window.injected=true">'
    records = [dict(refid=7001, doi='javascript:alert(1)', title=title, year_of_publication=2020, publication_date='2020', measurement_methods=[{'name': 'MSP', 'kind': 'experimental', 'uncertain': True}], status='APPROVED'), dict(refid=7002, title='Second paper', doi='10.1234/second', year_of_publication=2022, status='APPROVED'), dict(refid=7003, title='Unknown year', doi=None, year_of_publication=None, status='APPROVED')]
    def fixture(route):
        if 'page=2' in route.request.url:
            route.fulfill(json={'count': 3, 'next': None, 'results': records[1:]})
        else:
            route.fulfill(json={'count': 3, 'next': BASE + '/api/references/?page=2', 'results': records[:1]})
    page.route('**/api/references/**', fixture)
    page.locator('#tab-references').click()
    page.wait_for_function('filteredData.length === 3')
    assert page.evaluate('chart.data.labels') == [2020, 2021, 2022]
    assert page.evaluate('chart.data.datasets[0].data') == [1, 0, 1]
    assert page.locator('#plotCount').inner_text() == '2 plotted · 1 excluded'
    assert page.locator('#tableBody img').count() == 0
    assert page.locator('#tableBody a[href^="javascript:"]').count() == 0
    assert not page.evaluate('Boolean(window.injected)')
    summary = page.locator('#tableBody tr').first.locator('td').nth(1).locator('summary')
    assert summary.get_attribute('title') == title
    assert summary.evaluate('(el)=>el.scrollWidth > el.clientWidth')
    summary.focus(); page.keyboard.press('Enter')
    assert page.locator('#tableBody tr').first.locator('td').nth(1).locator('details').get_attribute('open') is not None
    assert list(csv.DictReader(io.StringIO(page.evaluate('formatDataForExport()'))))[0]['title'] == title
    page.locator('#searchInput').fill('Second paper')
    assert page.evaluate('filteredData.length') == 1 and page.evaluate('chart.data.datasets[0].data') == [1]
    page.locator('#searchInput').fill('')
    record('paginated references, zero years, unknowns, safe rendering, full-text keyboard/hover and metadata search/export')
    page.screenshot(path='/tmp/vpod-references-desktop.png')
    page.set_viewport_size({'width': 390, 'height': 844})
    summary = page.locator('#tableBody tr').first.locator('td').nth(1).locator('summary')
    summary.scroll_into_view_if_needed(); summary.tap()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    page.screenshot(path='/tmp/vpod-references-mobile.png')
    record('390px viewport contained layout and emulated touch full-text access')
    page.unroute('**/api/references/**', fixture)
    page.route('**/api/references/**', lambda r: r.fulfill(status=503, body='Unavailable'))
    page.locator('#tab-references').click()
    page.wait_for_function("document.getElementById('recordCount').textContent === 'Unable to load records'")
    assert page.evaluate('filteredData.length') == 0
    assert 'Connection error' in page.locator('#emptyState h3').inner_text()
    record('API failure clears stale table/chart/export population')
    assert errors == [], errors
    browser.close()
Path(os.environ.get('VPOD_BROWSER_REPORT', 'reports/browser-verification.json')).write_text(json.dumps({'target': 'isolated localhost SQLite copy', 'browser': 'Playwright Chromium', 'checks': results, 'javascript_errors': errors}, indent=2))
print(json.dumps(results, indent=2))
