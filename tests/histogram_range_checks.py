"""Read-only browser checks: all API calls and staged JS are intercepted locally."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

fixtures={
    'opsins':[],
    'heterologous':[dict(hetid=1,lambda_max=502),dict(hetid=2,lambda_max=617),dict(hetid=3,lambda_max=None,mutations='unmeasured')],
    'scp':[dict(scpid=1,lambda_max=359),dict(scpid=2,lambda_max=501),dict(scpid=3,lambda_max=None)],
    'visual-acuity':[dict(acuid=1,cpd=1.5),dict(acuid=2,cpd=6),dict(acuid=3,cpd=None,notes='unmeasured')],
    'references':[dict(refid=1,year_of_publication=2020),dict(refid=2,year_of_publication=2022),dict(refid=3,year_of_publication=None)],
}
results=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('**/api/**',lambda route:route.fulfill(json=fixtures[urlsplit(route.request.url).path.strip('/').split('/')[1]]))
    page.route('**/static/core/explorer*.js',lambda route:route.fulfill(content_type='text/javascript',body=Path('static/core/explorer.js').read_text()))
    page.route('**/static/core/vpod-data*.js',lambda route:route.fulfill(content_type='text/javascript',body=Path('static/core/vpod-data.js').read_text()))
    page.goto('http://127.0.0.1:8000/')
    for tab,low,high in [('heterologous',500,620),('scp',360,500)]:
        page.locator('#tab-'+tab).click()
        page.wait_for_function('filteredData.length === 3')
        labels=page.evaluate('chart.data.labels')
        assert labels==list(range(low,high+1,10)),labels
        counts=page.evaluate('chart.data.datasets[0].data')
        assert counts[0]==counts[-1]==1 and 0 in counts and sum(counts)==2
        assert page.locator('#plotCount').inner_text()=='2 plotted · 1 excluded'
        if tab=='heterologous':
            page.screenshot(path='/tmp/vpod-histogram-fitted-range.png',full_page=True)
            page.locator('button',has_text='Show Advanced Filters').click()
            page.locator('#filterLmaxMin').fill('550')
            assert page.evaluate('chart.data.labels')==[620]
            page.locator('#filterLmaxMin').fill('')
        results.append(tab+': observed extent, internal gaps, counts and exclusions preserved')
    page.locator('#tab-visual-acuity').click();page.wait_for_function('filteredData.length === 3')
    assert page.evaluate('chart.data.labels')==['1–<2','2–<5','5–<10']
    assert page.evaluate('chart.data.datasets[0].data')==[1,0,1]
    page.locator('#filterCpdMin').fill('5')
    assert page.evaluate('chart.data.labels')==['5–<10']
    page.locator('#filterCpdMin').fill('')
    page.locator('#searchInput').fill('unmeasured')
    assert page.evaluate('chart.data.labels')==[]
    assert page.locator('#plotCount').inner_text()=='0 plotted · 1 excluded'
    page.locator('#searchInput').fill('')
    results.append('Acuity: outer empty bins omitted; bin boundaries, internal gaps, filtered and missing-only states preserved')
    page.locator('#tab-references').click();page.wait_for_function('filteredData.length === 3')
    assert page.evaluate('chart.data.labels')==[2020,2021,2022]
    assert page.evaluate('chart.data.datasets[0].data')==[1,0,1]
    results.append('Reference years retain their observed extent and intervening zero years')
    assert not errors,errors
    browser.close()
Path('reports/histogram-range-verification.json').write_text(json.dumps({'checks':results,'javascript_errors':errors,'data':'Browser fixtures only; no database writes'},indent=2))
print(json.dumps(results))
