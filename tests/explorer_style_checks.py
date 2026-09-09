"""Compare the original explorer's visual vocabulary with the restored renderer."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8765'
# Fixed pre-implementation revision, so later commits do not move the baseline.
BASELINE = '374306a0df1ba556277a2a6c36f68ee62261a4aa:templates/index.html'
ref = dict(refid=701, doi='10.1234/style-fixture', title='Reference title used only for interface verification', publication_date='2020', year_of_publication=2020, status='APPROVED', measurement_methods=[dict(name='Microspectrophotometry (MSP)', kind='experimental', uncertain=False)])
opsin = dict(opsinid=801, genus='Danio', species='rerio', phylum='Chordata', gene_family='RH1', accession='STYLE_TEST_ACCESSION_123456789', reference=ref, status='APPROVED')
fixtures = {
    'opsins': [opsin],
    'heterologous': [dict(hetid=901, opsin=opsin, lambda_max=500, error=2, mutations='None', reference=ref, is_inferred=False), dict(hetid=902, opsin=opsin, lambda_max=620, error=0, mutations='A292S', reference=ref, is_inferred=True, inference_source='Style fixture')],
    'scp': [dict(scpid=903, genus='Danio', species='rerio', phylum='Chordata', lambda_max=360, error=1, photoreceptor_type='Cone', cell_subtype='single', chromophore='A1', reference=ref)],
    'references': [ref],
    'visual-acuity': [dict(acuid=904, genus='Danio', species='rerio', eye_type='Camera', cpd=1.5, reference=ref)],
}

def api(route):
    tab = urlsplit(route.request.url).path.strip('/').split('/')[1]
    route.fulfill(json=fixtures[tab])

results=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    original=browser.new_page(viewport={'width':1440,'height':1000})
    original.route('**/api/**',api)
    original.route(BASE+'/',lambda route: route.fulfill(content_type='text/html',body=subprocess.check_output(['git','show',BASELINE],text=True)))
    original.goto(BASE)
    original.wait_for_function('filteredData.length === 1')
    original.screenshot(path='/tmp/vpod-style-original-sequences.png',full_page=True)
    baseline=original.locator('#tableBody .italic').first.evaluate('(e)=>getComputedStyle(e).fontStyle')
    original.locator('#tab-heterologous').click()
    original.wait_for_function('filteredData.length === 2')
    original.screenshot(path='/tmp/vpod-style-original-spectral.png',full_page=True)
    original.close()

    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/api/**',api)
    page.goto(BASE)
    page.wait_for_function('filteredData.length === 1')
    assert page.locator('.organism-name').first.evaluate('(e)=>getComputedStyle(e).fontStyle') == baseline == 'italic'
    assert page.locator('.gene-family-badge').inner_text() == 'RH1'
    assert 'mono' in page.locator('.accession-value').evaluate('(e)=>getComputedStyle(e).fontFamily')
    assert 'Chordata' in page.locator('.organism-cell').inner_text()
    assert page.locator('.source-link svg').count() == 1
    assert page.locator('#tableHeader th').first.evaluate('(e)=>getComputedStyle(e).textTransform') == 'uppercase'
    page.screenshot(path='/tmp/vpod-style-restored-sequences.png',full_page=True)
    results.append('Original typography, phylum subtitle, gene-family badge, monospace accession, DOI icon and table headings restored')
    page.locator('#tab-heterologous').click(); page.wait_for_function('filteredData.length === 2')
    assert page.locator('.evidence-badge').all_text_contents() == ['Experimental','Inferred']
    colors=page.evaluate('chart.data.datasets[0].backgroundColor')
    assert len(set(colors)) > 5 and page.evaluate('chart.data.datasets[0].borderRadius') == 4
    assert '±2' in page.locator('.spectral-value').first.inner_text()
    assert '±0' in page.locator('.spectral-value').nth(1).inner_text()
    assert page.locator('.spectral-value').first.evaluate('(e)=>getComputedStyle(e).color') != page.locator('.spectral-value').nth(1).evaluate('(e)=>getComputedStyle(e).color')
    assert page.locator('.spectrum-bg').is_visible()
    page.screenshot(path='/tmp/vpod-style-restored-spectral.png',full_page=True)
    results.append('Spectral colors, rounded histogram bars, uncertainty values and experimental/inferred badges restored')
    for tab in ['references','visual-acuity']:
        page.locator('#tab-'+tab).click(); page.wait_for_function('currentTab === "'+tab+'" && filteredData.length === 1')
        assert page.evaluate('chart.data.datasets[0].backgroundColor') == '#0284c7'
        assert page.locator('.spectral-value').count() == 0
        assert page.locator('.spectrum-bg').is_hidden()
    page.locator('button',has_text='Contribute Data').click()
    page.locator('#tier-data').click()
    assert 'border-sky-500' in page.locator('#tier-data').get_attribute('class')
    assert 'border-sky-500' not in page.locator('#tier-pub').get_attribute('class')
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('#siteActions').evaluate('(e)=>e.getBoundingClientRect().right <= innerWidth')
    page.screenshot(path='/tmp/vpod-style-restored-mobile.png',full_page=True)
    results.append('Acuity and reference charts remain nonspectral; contribution selection and mobile header verified')
    assert not errors,errors
    browser.close()
Path('reports/landing-style-verification.json').write_text(json.dumps({'checks':results,'javascript_errors':errors,'baseline':BASELINE,'screenshots':'/tmp/vpod-style-*.png'},indent=2))
print(json.dumps(results))
