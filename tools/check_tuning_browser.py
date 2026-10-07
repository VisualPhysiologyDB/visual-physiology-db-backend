"""Run with the approved separate Playwright environment and an isolated local server.
Writes only public example screenshots/downloads into --output; never starts a service.
"""
import argparse,json,csv,io
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8767');parser.add_argument('--output',default='/tmp/vpod-tuning-browser-check');args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
root=Path(__file__).resolve().parents[1]
report={'checks':[],'errors':[]}

def check(name,condition):
    if not condition:raise AssertionError(name)
    report['checks'].append(name);print('PASS',name,flush=True)

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:report['errors'].append(str(error)))
    requests=[];page.on('request',lambda request:requests.append({'url':request.url,'method':request.method,'data':request.post_data if request.method=='POST' else None}))
    page.goto(args.url,wait_until='domcontentloaded');page.locator('#tab-tuning').click();page.wait_for_function('window.VpodTuning.state.initialized')
    check('Beta notice visible',page.locator('.tm-banner').is_visible() and 'Beta' in page.locator('.tm-banner').inner_text())
    check('Default family uses bovine numbering',page.locator('#tmReference').input_value()=='bovine')
    check('Cross-family sites hidden by default',page.locator('[data-key="spider-e181d"]').count()==0)
    page.select_option('#tmFamily','R_OPSIN');check('Invertebrate context defaults to squid',page.locator('#tmReference').input_value()=='squid')
    page.locator('#tmExample').click()
    choice=page.locator('[data-key="spider-e181d"]').first
    choice.locator('xpath=ancestor::article').locator('.tm-site-toggle').click()
    choice.locator('xpath=ancestor::div[@class="tm-mutation-group"]').locator('button').click()
    choice.check();page.locator('#tmMap').click();page.wait_for_function('window.VpodTuning.state.result !== null',timeout=45000)
    result=page.evaluate('VpodTuning.state.result');row=result['rows'][0]
    check('Bovine E181 / squid E180 / spider E194', (row['source_position'],row['reference_position'],row['target_position'])==(181,180,194))
    with page.expect_download() as download:page.locator('#tmExport').click()
    download.value.save_as(out/'mapped.csv');export=list(csv.DictReader((out/'mapped.csv').read_text().splitlines()))
    check('Mapped CSV includes evidence and DOI',len(export)==1 and '10.1038/s42003-019-0409-3' in export[0]['source_dois'])
    with page.expect_download() as download:page.locator('#tmEvidenceExport').click()
    download.value.save_as(out/'evidence.csv');check('Evidence CSV includes citation roles','reference_role' in (out/'evidence.csv').read_text())
    with page.expect_download() as download:page.locator('#tmSessionExport').click()
    download.value.save_as(out/'mapping-session.json')
    page.locator('#tmAlignment').evaluate('(n)=>n.parentElement.open=true');page.locator('#tmMappingRows button').first.click()
    check('Selected site highlighted in alignment',page.locator('#tmAlignment .tm-active').count()>=5)
    page.locator('#tmOpenStructures').click()
    for key,expected in [('bovine',181),('squid',180),('spider',194)]:
        page.select_option('#tmStructureReference',key);page.locator('#tmLoadReference').click()
        page.wait_for_function('(key)=>window.VpodStructures.slots.Reference?.file?.name.includes(key)',arg={'bovine':'1U19','squid':'2Z73','spider':'6I9K'}[key],timeout=60000)
        page.locator('#tmReferenceMark').click();page.wait_for_function("VpodStructures.slots.Reference.marks.length>0 || document.getElementById('tmReferenceStructureStatus').classList.contains('tm-error')",timeout=45000)
        check(key+' structure mapped',not page.locator('#tmReferenceStructureStatus').evaluate('(n)=>n.classList.contains("tm-error")'))
        info=page.evaluate("""() => {const slot=VpodStructures.slots.Reference, structure=slot.component.cell.obj.data;const residues=new Set();for(const u of structure.units)for(const a of u.elements)residues.add(u.id+':'+u.residueIndex[a]);return {count:residues.size,rows:slot.mappings,atoms:structure.elementCount};}""")
        check(key+' marks exactly one residue, not the whole chain',info['count']==1 and info['atoms']<30)
        check(key+' author numbering preserved',info['rows'][0]['author_residue']==expected)
    page.locator('#tmMappingRows button').first.click()
    with page.expect_download() as download:page.locator('#tmReferenceImage').click()
    download.value.save_as(out/'spider-sites.png');check('PNG saved',(out/'spider-sites.png').stat().st_size>5000)
    with page.expect_download() as download:page.locator('#tmReferenceViewSave').click()
    download.value.save_as(out/'structure-view.json')
    # Restore whitelisted coordinates/camera, not executable Mol* state or remote URLs.
    page.set_input_files('#tmSessionFile',str(out/'structure-view.json'));page.wait_for_function('VpodStructures.slots.User?.marks.length===1',timeout=60000)
    check('View session restores marked residues locally','Restored 1 saved marks' in page.locator('#tmUserStructureStatus').inner_text())
    # A PDB with intentionally shifted author numbers exercises the chain/sequence distinction.
    pdb=page.evaluate("""() => {const m=VpodStructures.slots.Reference.structure.cell.obj.data.models[0],h=m.atomicHierarchy,c=m.atomicConformation;let lines=[],serial=1;const pad=(v,n)=>String(v).padStart(n);for(let a=h.chainAtomSegments.offsets[0];a<h.chainAtomSegments.offsets[1];a++){const r=h.residueAtomSegments.index[a];const atom=h.atoms.label_atom_id.value(a).padStart(4),res=h.atoms.label_comp_id.value(a);const author=h.residues.auth_seq_id.value(r)+1000;lines.push('ATOM  '+pad(serial++,5)+' '+atom+' '+res+' Z'+pad(author,4)+'    '+pad(c.x[a].toFixed(3),8)+pad(c.y[a].toFixed(3),8)+pad(c.z[a].toFixed(3),8)+'  1.00 20.00          '+pad(h.atoms.type_symbol.value(a),2));}return lines.join('\\n')+'\\nEND\\n';}""")
    fixture=out/'renumbered-spider.pdb';fixture.write_text(pdb)
    page.set_input_files('#tmStructureFile',str(fixture));page.wait_for_function("VpodStructures.slots.User.file.name==='renumbered-spider.pdb'",timeout=60000)
    page.locator('#tmUserMark').click();page.wait_for_function('VpodStructures.slots.User.mappings.length===1',timeout=45000)
    check('Uploaded PDB preserves arbitrary author numbering',page.evaluate('VpodStructures.slots.User.mappings[0].author_residue')==1194)
    page.set_input_files('#tmStructureFile',str(root/'static/core/tuning-structures/6i9k.cif'));page.wait_for_function("VpodStructures.slots.User.file.name==='6i9k.cif'",timeout=60000)
    page.locator('#tmUserMark').click();page.wait_for_function('VpodStructures.slots.User.mappings.length===1',timeout=45000)
    check('Uploaded mmCIF maps correctly',page.evaluate('VpodStructures.slots.User.mappings[0].author_residue')==194)
    check('Only sequence JSON sent by mapper, never coordinates',all(r['url'].endswith('/api/tuning-mappings/') and 'ATOM' not in (r['data'] or '') and '_atom_site' not in (r['data'] or '') for r in requests if r['method']=='POST'))
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'mobile.png'),full_page=True)
    check('Mobile layout has no page-wide overflow',page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'))
    page.set_viewport_size({'width':1440,'height':1000});page.screenshot(path=str(out/'desktop.png'),full_page=True)
    # Inputs invalidate previous exports; malicious FASTA headers remain literal text.
    seq=result['targets'][0]['sequence'];page.fill('#tmFasta','><img src=x onerror=alert(1)>\n'+seq)
    check('Changed inputs hide stale results',page.locator('#tmResults').is_hidden())
    page.locator('#tmMap').click();page.wait_for_function('VpodTuning.state.result !== null',timeout=45000)
    check('Untrusted header rendered as text',page.locator('#tmMappingRows img').count()==0 and '<img' in page.locator('#tmMappingRows').inner_text())
    page.check('#tmCross');check('Cross-family warning visible',page.locator('#tmCrossWarning').is_visible())
    page.set_input_files('#tmSessionFile',str(out/'mapping-session.json'));page.wait_for_function("document.getElementById('tmStatus').textContent.startsWith('Inputs restored')")
    check('Mapping session restores inputs without trusting cached output',page.evaluate('VpodTuning.state.result === null') and page.locator('#tmFasta').input_value().startswith('>Jumping'))
    for tab in ['references','visual-acuity','scp','heterologous','opsins']:
        page.locator('#tab-'+tab).click();page.wait_for_function("document.getElementById('loadingOverlay').classList.contains('hidden')",timeout=45000)
        check(tab+' explorer retained',page.locator('#explorerSearchPanel').is_visible() and page.locator('#tableBody tr').count()>0)
        if tab!='opsins':check(tab+' histogram retained',page.locator('#plotContainer').is_visible())
    check('No uncaught browser errors',not report['errors'])
    report['requests']=[{'url':r['url'],'method':r['method']} for r in requests]
    report['browser']=browser.version;browser.close()
(out/'report.json').write_text(json.dumps(report,indent=2));print('Report:',out/'report.json')
