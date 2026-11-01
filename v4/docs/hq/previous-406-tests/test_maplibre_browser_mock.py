"""Browser test of *integration with a MapLibre API MOCK* (not genuine GL rendering)."""
from playwright.sync_api import sync_playwright
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[2]
HQ=ROOT/'hq'
html=(HQ/'index.html').read_text()
# Static head is provided by server in production; in this isolated browser test,
# scripts/styles are intentionally injected after a mock renderer is supplied.
html=html.replace('<link rel="stylesheet" href="/assets/vendor/maplibre-gl.css"/>','').replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/vendor/maplibre-gl.js" defer></script>','').replace('<script src="/assets/hq.js" defer></script>','')
geo=json.loads((HQ/'data/uk-geography.json').read_text())
MAPLIBRE_MOCK='''(()=>{class TestMap{constructor(opts){window.__testMapOpts=opts;window.__testMap=this;this.lon=opts.center[0];this.lat=opts.center[1];this.zoom=opts.zoom;this.fn={};setTimeout(()=>this.emit('load'),70);}on(e,f){(this.fn[e]??=[]).push(f);return this;}emit(e){(this.fn[e]||[]).forEach(f=>f());}getCenter(){return {lng:this.lon,lat:this.lat};}getZoom(){return this.zoom;}jumpTo(o){this.lon=o.center[0];this.lat=o.center[1];this.zoom=o.zoom;this.emit('move');}zoomTo(z){this.zoom=z;this.emit('move');}project(p){let map=document.getElementById('map');return {x:map.clientWidth/2+(p[0]-this.lon)*180,y:map.clientHeight/2-(p[1]-this.lat)*180};}resize(){this.emit('move');}}window.maplibregl={Map:TestMap};})();'''
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for label,w,h in [('desktop',1440,900),('tablet',820,1180),('mobile',390,844)]:
  page=browser.new_page(viewport={'width':w,'height':h});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content(html);page.add_style_tag(content=(HQ/'assets/hq.css').read_text())
  page.evaluate('''geo=>{window.fetch=(url)=>Promise.resolve(new Response(JSON.stringify(String(url).startsWith('/data/')?geo:{contractVersion:'1.0',components:['air','sea','rail','bus','road'].map(id=>({id,status:'not_installed'}))}),{status:200,headers:{'content-type':'application/json'}}));}''',geo)
  page.add_script_tag(content=MAPLIBRE_MOCK);page.add_script_tag(content=(HQ/'assets/hq.js').read_text())
  page.wait_for_function("window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().renderer==='maplibre'")
  style=page.evaluate('window.__testMapOpts.style')
  assert style=='/basemap/openfreemap/styles/liberty',style
  assert page.evaluate('window.__testMapOpts.transformRequest("https://tiles.openfreemap.org/planet/1/1/1.pbf").url')=='/basemap/openfreemap/planet/1/1/1.pbf'
  page.locator('#zoom-in').click()
  assert page.evaluate('window.TPA_HQ_READONLY_STATE().zoom')==13
  page.locator('#home-button').click()
  state=page.evaluate('window.TPA_HQ_READONLY_STATE()')
  assert state['zoom']==12 and abs(state['lon']+.423)<.001,state
  if label=='mobile':page.locator('#menu-button').click();page.wait_for_timeout(300)
  page.locator('#search').fill('hull');page.locator('.search-result').first.click()
  state=page.evaluate('window.TPA_HQ_READONLY_STATE()')
  assert abs(state['lat']-53.745)<.001,state
  assert not errors,errors
  assert page.locator('script[src*="admin"]').count()==0
  assert page.locator('[data-component]').count()==5
  assert page.locator('.vehicle-pin').count()==0
  if label=='desktop':
   page.evaluate("for(let i=0;i<8;i++)window.__testMap.emit('error')")
   page.wait_for_function("window.TPA_HQ_READONLY_STATE().renderer==='degraded'")
   assert page.evaluate("getComputedStyle(document.querySelector('#geo-canvas')).opacity")=='1'
   assert page.evaluate("getComputedStyle(document.querySelector('#vector-map')).opacity")=='0'
  print('MOCK MAPLIBRE',label,'PASS: style Liberty; same-origin URL; zoom; Beverley Home; Hull search; five sockets; no JS errors')
  page.close()
 browser.close()
