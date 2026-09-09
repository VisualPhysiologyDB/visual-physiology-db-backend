"""Read-only follow-up checks against the isolated review database on localhost."""
import csv
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto((Path('reports/reference-review.html').resolve()).as_uri())
    assert page.locator('article').count() == 145
    page.locator('#kind').select_option('Unresolved')
    assert page.locator('article:visible').count() == 59
    page.locator('#kind').select_option('Conflict')
    assert page.locator('article:visible').count() == 4
    page.locator('#search').fill('599')
    assert page.locator('article:visible').count() == 1
    assert page.locator('article:visible a').first.get_attribute('href') == 'http://127.0.0.1:8000/admin/core/reference/599/change/'
    page.locator('#search').fill(''); page.locator('#kind').select_option('Unresolved')
    page.locator('article:visible summary').first.focus()
    page.keyboard.press('Enter')
    assert page.locator('details[open]').count() == 1
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    results.append('Review snapshot: issue/text filters, all counts, admin links, keyboard evidence access, mobile width')
    page.goto('http://127.0.0.1:8765/')
    page.locator('#tab-scp').click()
    page.wait_for_function("document.getElementById('recordCount').textContent.includes('Records Found')")
    page.wait_for_function('filteredData.length === 3796')
    assert page.locator('#tableBody tr').count() == 3796
    with page.expect_download() as download:
        page.locator('#exportDataButton').click()
    rows = list(csv.DictReader(open(download.value.path())))
    assert len(rows) == 3796
    assert page.evaluate('chart.data.datasets[0].data.reduce((a,b)=>a+b,0)') == int(page.locator('#plotCount').inner_text().split()[0])
    response = page.request.get('http://127.0.0.1:8765/api/scp/')
    assert len(response.json()) == 3796
    results.append('MSP: 3796 public table/API/export records and matching chart counts')
    assert not errors, errors
    browser.close()
Path('reports/followup-browser-verification.json').write_text(json.dumps({'checks': results, 'javascript_errors': errors}, indent=2))
print(json.dumps(results))
