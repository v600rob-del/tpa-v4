from playwright.sync_api import sync_playwright
from pathlib import Path
import json
pbase=Path(__file__).resolve().parents[2]/'hq'
html=(pbase/'index.html').read_text().replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/hq.js" defer></script>','')
css=(pbase/'assets/hq.css').read_text();js=(pbase/'assets/hq.js').read_text();geo=json.loads((pbase/'data/uk-geography.json').read_text())
out=Path(__file__).resolve().parents[2]/'docs/hq'
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for label,width,height in [('desktop',1440,900),('tablet',820,1180),('mobile',390,844)]:
  page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content(html,wait_until='domcontentloaded')
  page.add_style_tag(content=css)
  page.evaluate('''data=>{window.fetch=(uri)=>Promise.resolve(new Response(JSON.stringify(uri==='/data/uk-geography.json'?data:{hq:'test',contractVersion:'1.0',components:['air','sea','rail','bus','road'].map(id=>({id,status:'not_installed'}))}),{status:200,headers:{'content-type':'application/json'}}));}''',geo)
  page.add_script_tag(content=js)
  page.wait_for_function('window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().geographyLoaded', timeout=10000)
  init=page.evaluate('window.TPA_HQ_READONLY_STATE()')
  page.screenshot(path=str(out/f'HQ-INITIAL-{label.upper()}.png'),full_page=True)
  page.locator('#zoom-in').click();z=page.evaluate('window.TPA_HQ_READONLY_STATE().zoom');assert z==13,(label,z)
  page.locator('#home-button').click();home=page.evaluate('window.TPA_HQ_READONLY_STATE()');assert abs(home['lon']+0.423)<.001 and home['zoom']==12
  if label=='mobile':page.locator('#menu-button').click();page.wait_for_timeout(350)
  page.locator('#search').fill('hull');page.locator('.search-result').first.click()
  hull=page.evaluate('window.TPA_HQ_READONLY_STATE()');assert abs(hull['lat']-53.745)<.001,(label,hull)
  page.wait_for_timeout(380) # mobile sidebar exit transition must finish before map drag
  # Actual pointer pan (touch-style coordinates are also supported by pointer events).
  before=page.evaluate('window.TPA_HQ_READONLY_STATE().lon')
  mapbox=page.locator('#map').bounding_box()
  cx=mapbox['x']+mapbox['width']*.45;cy=mapbox['y']+mapbox['height']*.65
  page.mouse.move(cx,cy);page.mouse.down();page.mouse.move(cx+95,cy+30,steps=5);page.mouse.up()
  after=page.evaluate('window.TPA_HQ_READONLY_STATE().lon');assert abs(after-before)>0.0001,(label,before,after)
  page.wait_for_timeout(350)
  page.screenshot(path=str(out/f'HQ-MAP-{label.upper()}.png'),full_page=True)
  if label=='desktop':
   page.locator('#search').fill('United Kingdom')
   page.locator('.search-result').first.click()
   page.wait_for_timeout(250)
   overview=page.evaluate('window.TPA_HQ_READONLY_STATE()')
   assert overview['zoom']==5.5 and abs(overview['lon']+3)<.001
   page.screenshot(path=str(out/'HQ-UK-OVERVIEW.png'),full_page=True)
  assert not errors,(label,errors)
  assert page.locator('script[src*="admin"]').count()==0
  assert page.locator('[data-component]').count()==5
  assert page.locator('.vehicle-pin').count()==0
  print(label,{'geography':init['geographyLoaded'],'zoomIn':z,'home':(home['lat'],home['lon']),'Hull':(hull['lat'],hull['lon']),'panMoved':abs(after-before)>0.0001,'jsErrors':errors})
  page.close()
 browser.close()
