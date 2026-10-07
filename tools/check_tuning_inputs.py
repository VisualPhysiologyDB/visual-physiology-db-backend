"""Focused validation and custom-numbering browser checks; use an isolated server."""
from playwright.sync_api import sync_playwright
import json,argparse
parser=argparse.ArgumentParser();parser.add_argument("--url",default="http://127.0.0.1:8767");parser.add_argument("--output",default="/tmp/vpod-tuning-input-checks.json");args=parser.parse_args()
checks=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox']);page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(args.url,wait_until='domcontentloaded');page.locator('#tab-tuning').click();page.wait_for_function('VpodTuning.state.initialized')
 page.select_option('#tmFamily','R_OPSIN');page.locator('[data-key="spider-e181d"]').check();page.fill('#tmFasta','>DNA\n'+'ATCG'*25);page.locator('#tmMap').click()
 assert 'appears to be DNA' in page.locator('#tmStatus').inner_text();checks.append('Frontend rejects DNA before mapping')
 page.locator('#tmExample').click();page.select_option('#tmReference','bovine');assert page.locator('#tmReferenceWarning').is_visible();checks.append('Cross-family display-reference warning visible')
 seqs=page.evaluate('Object.fromEntries(VpodTuning.state.templates.map(p=>[p.key,p.sequence]))')
 page.select_option('#tmReference','custom');page.fill('#tmCustom','>My numbering\n'+seqs['bovine'])
 page.fill('#tmFasta','>spider\n'+seqs['spider']+'\n>squid\n'+seqs['squid']);page.locator('#tmMap').click();page.wait_for_function('VpodTuning.state.result !== null',timeout=45000)
 rows=page.evaluate('VpodTuning.state.result.rows');assert [r['target_position'] for r in rows]==[194,180],rows
 assert all(r['reference_position']==181 for r in rows);checks.append('Multiple targets map to custom reference numbering')
 page.fill('#tmSearch','absolutely-no-match');assert page.locator('#tmCatalogueEmpty').is_visible();assert page.locator('#tmMap').is_disabled();checks.append('Empty filter state removes hidden selections and disables mapping')
 page.locator('#tab-references').click();page.wait_for_function("document.getElementById('loadingOverlay').classList.contains('hidden')");assert page.locator('#tableBody tr').count()>0
 assert not errors,errors;checks.append('No uncaught errors after final UI changes');b.close()
print(json.dumps(checks));open(args.output,'w').write(json.dumps(checks))
