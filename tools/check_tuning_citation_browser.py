"""Citation-table checks against an isolated localhost server; no database writes."""
import argparse
import copy
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:8775')
parser.add_argument('--output',default='/tmp/vpod-citation-browser')
args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
report={'checks':[],'errors':[]}
warning='Automatically approved from a sequence match. This tuning site still requires manual literature review.'

def check(name,condition):
    assert condition,name
    report['checks'].append(name);print('PASS',name,flush=True)

def open_entry(page,key):
    page.locator('#tmSearch').fill(key)
    study=page.locator(f'input[data-key="{key}"]').first.locator('xpath=ancestor::div[@class="tm-study"]')
    site=study.locator('xpath=ancestor::article').locator('.tm-site-toggle')
    if site.get_attribute('aria-expanded')!='true':site.click()
    group=study.locator('xpath=ancestor::div[@class="tm-mutation-group"]').locator('button').first
    if group.get_attribute('aria-expanded')!='true':group.click()
    details=study.locator('details')
    if details.get_attribute('open') is None:details.locator('summary').click()
    return details

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e:report['errors'].append(str(e)))
    original=page.request.get(args.url+'/api/tuning-sites/').json()
    entry=next(e for e in original['results'] if e['conditions'].get('approval',{}).get('mode')=='AUTO' and len(e['references'])==1 and len(e['assays'])==2)
    page.goto(args.url,wait_until='domcontentloaded');page.locator('#tab-tuning').click();page.wait_for_function('VpodTuning.state.initialized')
    page.locator('#tmCross').check()
    details=open_entry(page,entry['key']);table=details.locator('table')
    expected=['Source species','Phylum','WT λmax (nm)','Mutant λmax (nm)','Published position','Reference position','Expression type','Culture','HetID','DOI']
    check('All requested columns present',table.locator('thead th').all_text_contents()==expected)
    check('One row per citation',table.locator('tbody tr').count()==len(entry['references']))
    check('Automatic warning uses requested wording',details.locator('.tm-warning').inner_text()==warning)
    wt=next(a for a in entry['assays'] if a['role']=='WT');mut=next(a for a in entry['assays'] if a['role']=='Mutant')
    cells=table.locator('tbody tr').first.locator('td').all_text_contents()
    check('Both measurements retain units in headings and source values',float(cells[2])==wt['lambda_max'] and float(cells[3])==mut['lambda_max'])
    check('Experimental record metadata displayed',cells[0]==wt['species'] and cells[1]==wt['phylum'] and cells[6]=='Heterologous' and str(wt['hetid']) in cells[8] and str(mut['hetid']) in cells[8])
    check('Matching diagnostics removed from dropdown',all(s not in details.inner_text() for s in ['Source locator:', 'sequence match basis','supplementary source columns','Measurement-condition equality','numbering scheme','mutation build','review note']))
    page.set_viewport_size({'width':1000,'height':1000})
    region=details.locator('.tm-citation-scroll');region.scroll_into_view_if_needed();region.focus();page.keyboard.press('ArrowRight');page.wait_for_timeout(200)
    check('Wide table supports keyboard scrolling',region.evaluate('(n)=>n.scrollLeft>0'))
    page.set_viewport_size({'width':1440,'height':1000})
    details.screenshot(path=str(out/'citation-desktop.png'))
    page.set_viewport_size({'width':390,'height':844})
    check('Mobile citation rows stack with field labels',table.locator('tbody td').first.evaluate('(n)=>getComputedStyle(n).display')=='grid')
    check('No mobile document overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    details.screenshot(path=str(out/'citation-mobile.png'))
    last=details.locator('tbody td:last-child');last.scroll_into_view_if_needed()
    check('DOI remains reachable within the mobile catalogue',last.evaluate('(n)=>{const a=n.getBoundingClientRect(),b=n.closest(".tm-site-catalogue").getBoundingClientRect();return a.top>=b.top-1 && a.bottom<=b.bottom+1;}'))
    details.screenshot(path=str(out/'citation-mobile-bottom.png'))
    page.set_viewport_size({'width':1440,'height':1000})
    review=open_entry(page,'hagen-lws-mws-180')
    check('Review citations retain separate rows',review.locator('tbody tr').count()==2)
    check('Missing review measurements are not invented',all(t=='Not recorded' for t in review.locator('tbody td:nth-child(3),tbody td:nth-child(4),tbody td:nth-child(7)').all_text_contents()))
    check('Published and selected reference numbering remain distinct','180' in review.locator('tbody td:nth-child(5)').first.inner_text() and review.locator('tbody td:nth-child(6)').first.inner_text().startswith('164 ('))
    check('Manual entries have no automatic-match warning',review.locator('.tm-warning').count()==0)

    # Browser-only fixture: exercise two source publications and hostile source text.
    fixture=copy.deepcopy(original);item=next(e for e in fixture['results'] if e['key']==entry['key'])
    source_a,source_b=copy.deepcopy(item['references'][0]),copy.deepcopy(item['references'][0])
    source_a.update(refid=900001,doi='10.1234/wt',link='https://doi.org/10.1234/wt')
    source_b.update(refid=900002,doi='<img src=x onerror=alert(1)>',link='javascript:alert(1)',title='<script>bad()</script>')
    item['references']=[source_a,source_b]
    for a in item['assays']:
        a['reference_id']=900001 if a['role']=='WT' else 900002
        a['culture']='WT culture' if a['role']=='WT' else '<svg onload=alert(1)>'
    page.route('**/api/tuning-sites/',lambda route:route.fulfill(json=fixture))
    page.reload(wait_until='domcontentloaded');page.locator('#tab-tuning').click();page.wait_for_function('VpodTuning.state.initialized');page.locator('#tmCross').check()
    details=open_entry(page,item['key']);rows=details.locator('tbody tr')
    a,b=[row.locator('td').all_text_contents() for row in rows.all()]
    check('Cross-publication measurements stay in their own rows',float(a[2])==wt['lambda_max'] and a[3]=='Not recorded' and b[2]=='Not recorded' and float(b[3])==mut['lambda_max'])
    check('Culture and HetID stay with their citation',a[7]=='WT culture' and a[8]==f'WT: {wt["hetid"]}' and b[8]==f'Mutant: {mut["hetid"]}')
    check('Untrusted text stays text and unsafe DOI links are disabled',details.locator('svg,img,script,a[href^="javascript:"]').count()==0 and '<svg onload=alert(1)>' in details.inner_text())
    check('No browser exceptions',not report['errors'])
    browser.close()
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Report:',out/'report.json')
