"""Browser-level fixture containment. MOCK only; not Oracle or service isolation."""
from playwright.sync_api import sync_playwright
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
BASE=Path(__file__).resolve().parents[2]/'hq'
html=(BASE/'index.html').read_text().replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/hq.js" defer></script>','')
css=(BASE/'assets/hq.css').read_text();js=(BASE/'assets/hq.js').read_text();geo=json.loads((BASE/'data/uk-geography.json').read_text())
now=datetime.now(timezone.utc)
timefmt=lambda d:d.isoformat().replace('+00:00','Z')
start=timefmt(now-timedelta(seconds=10));end=timefmt(now+timedelta(minutes=5))
manifest={'hq':'fixture','contractVersion':'1.0','components':[{'id':id,'status':'ready' if id in ('bus','rail') else 'not_installed'} for id in ('air','sea','rail','bus','road')]}
text='<img src=x onerror="window.injection=true">'
bus={'component':'bus','contractVersion':'1.0','health':'ready','receivedAt':start,'expiresAt':end,'attribution':'MOCK fixture', 'entities':[{'id':'B-001','label':text,'observedAt':start,'quality':'observed','position':{'lat':53.745,'lon':-0.334}}]}
rail={'component':'rail','contractVersion':'1.0','health':'ready','receivedAt':start,'expiresAt':end,'attribution':'MOCK fixture', 'entities':[{'id':'R-001','label':'invalid-location','observedAt':start,'quality':'observed','position':{'lat':999,'lon':-0.334}}]}
responses={'/api/v4/manifest':manifest,'/data/uk-geography.json':geo,'/api/v4/components/bus/snapshot':bus,'/api/v4/components/rail/snapshot':rail}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(html);page.add_style_tag(content=css)
 page.evaluate('''responses=>{window.fetch=(url)=>{let key=String(url).split('?')[0];const obj=responses[key];return Promise.resolve(new Response(JSON.stringify(obj||{error:'missing fixture'}),{status:obj?200:503,headers:{'content-type':'application/json'}}));};}''',responses)
 page.add_script_tag(content=js)
 page.wait_for_function('window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().components.bus==="ready" && window.TPA_HQ_READONLY_STATE().components.rail==="unavailable"',timeout=10000)
 page.locator('#search').fill('hull');page.locator('.search-result').first.click();page.wait_for_timeout(300)
 assert page.locator('.vehicle-pin.bus').count()==1, 'Bus fixture did not render'
 assert page.locator('.vehicle-pin.rail').count()==0, 'Invalid rail fixture rendered'
 assert page.locator('img[src="x"]').count()==0, 'Untrusted label injected HTML'
 assert not page.evaluate('window.injection===true'), 'Untrusted label executed JS'
 assert page.locator('#rail-chip strong').inner_text()=='UNAVAILABLE'
 assert page.locator('#bus-chip strong').inner_text()=='MOCK'
 assert page.locator('#air-chip strong').inner_text()=='EMPTY'
 assert not errors,errors
 print('FIXTURE ONLY: valid bus displays; invalid rail contained; others remain empty; malicious label did not execute; no browser JS errors')
 browser.close()
