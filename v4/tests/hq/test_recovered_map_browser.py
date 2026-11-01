"""Real Chromium, real HQ JS and historical adapter/loader, with stubbed vendor APIs.
Map output is MOCK; no live OpenFreeMap tiles and no proof of production rendering.
"""
import json,re,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
HQ=Path(__file__).resolve().parents[2]/'hq'
LEAFLET="""(()=>{function lmap(id,opts){const m={_center:{lat:53.842,lng:-.423},_zoom:11,_fn:{},getCenter(){return {...this._center}},getZoom(){return this._zoom},latLngToContainerPoint(p){let e=document.getElementById('map');return{x:e.clientWidth/2+(p[1]-this._center.lng)*180,y:e.clientHeight/2-(p[0]-this._center.lat)*180}},setView(p,z){this._center={lat:p[0],lng:p[1]};this._zoom=z;this.fire('move');this.fire('zoomend');return this},setZoom(z){this._zoom=z;this.fire('zoomend');return this},on(n,f){n.split(' ').forEach(e=>(this._fn[e]??=[]).push(f));return this},once(n,f){return this.on(n,f)},fire(n){(this._fn[n]||[]).forEach(f=>f({type:n}));return this},whenReady(f){f();return this},invalidateSize(){this.fire('move');return this},remove(){}};window.__leafmap=m;return m;}window.L={map:lmap,control:{attribution(){return{addTo(){}}}}};})();"""
MAPLIBRE="window.maplibregl={Map:function(){}};"
BRIDGE="""(()=>{const gl={loaded(){return true},getCenter(){return {lat:53.842,lng:-.423}},getZoom(){return 10},once(n,f){if(n==='load')f()},on(n,f){this['_'+n]=f}};class Base{constructor(opts){this.options={padding:.3,...opts}}addTo(map){this._map=map;this._glMap=gl;window.__style=this.options.style;return this}getMaplibreMap(){return gl}}Base.extend=function(obj){class Extension extends Base{}Object.assign(Extension.prototype,obj);return Extension};L.MaplibreGL=Base;})();"""
class RecoveryBrowser(unittest.TestCase):
 def test_three_viewports(self):
  html=(HQ/'index.html').read_text()
  html=re.sub(r'<script[^>]*\bsrc="[^"]+"[^>]*></script>','',html)
  html=re.sub(r'<link[^>]*\bhref="/assets/vendor/[^\"]+"[^>]*>','',html)
  html=html.replace('<link rel="stylesheet" href="/assets/hq.css"/>','')
  geo=json.loads((HQ/'data/uk-geography.json').read_text())
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
   try:
    for label,w,h in [('desktop',1440,900),('tablet',820,1180),('mobile',390,844)]:
     with self.subTest(label=label):
      page=browser.new_page(viewport={'width':w,'height':h});errors=[]
      page.on('pageerror',lambda e:errors.append(str(e)))
      page.set_content(html)
      page.add_style_tag(content=(HQ/'assets/hq.css').read_text())
      page.evaluate("""geo=>{window.fetch=uri=>Promise.resolve(new Response(JSON.stringify(String(uri).startsWith('/data/')?geo:String(uri).startsWith('/hq-map-status')?{upstreamErrors:{},browser:{lastCode:'none'}}:{contractVersion:'1.0',components:['air','sea','rail','bus','road'].map(id=>({id,status:'not_installed'}))}),{status:200,headers:{'content-type':'application/json'}}));}""",geo)
      page.add_script_tag(content=LEAFLET)
      page.evaluate("""()=>{let style=document.createElement('style');style.id='tpa-maplibre-css';document.head.append(style);let c=document.createElement('script');c.id='tpa-basemap-loader';c.dataset.maplibre='/assets/vendor/maplibre-gl.js';c.dataset.bridge='/assets/vendor/leaflet-maplibre-gl.js';c.dataset.vector='/assets/transport-vector-layer.js';document.head.append(c);let l=document.createElement('script');l.id='tpa-leaflet-js';document.head.append(l);}""")
      page.evaluate("""({maplibre,bridge,adapter})=>{const original=document.head.append.bind(document.head);document.head.append=function(el){if(el.tagName==='SCRIPT'&&el.src){let code=el.src.includes('leaflet-maplibre-gl')?bridge:el.src.includes('transport-vector-layer')?adapter:maplibre;(0,eval)(code);Promise.resolve().then(()=>el.dispatchEvent(new Event('load')));return;}return original(el);};}""",{'maplibre':MAPLIBRE,'bridge':BRIDGE,'adapter':(HQ/'assets/transport-vector-layer.js').read_text()})
      page.add_script_tag(content=(HQ/'assets/tpa-basemap-loader.js').read_text())
      page.add_script_tag(content=(HQ/'assets/hq.js').read_text())
      page.wait_for_function("window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().renderer==='leaflet-maplibre'",timeout=8000)
      self.assertEqual(page.evaluate('window.__style'),'/basemap/openfreemap/styles/liberty')
      page.locator('#zoom-in').click();self.assertEqual(page.evaluate('window.TPA_HQ_READONLY_STATE().zoom'),12)
      page.locator('#home-button').click();self.assertEqual(page.evaluate('window.TPA_HQ_READONLY_STATE().zoom'),11)
      if label=='mobile':page.locator('#menu-button').click()
      page.locator('#search').fill('Hull');page.locator('.search-result').first.click()
      self.assertAlmostEqual(page.evaluate('window.TPA_HQ_READONLY_STATE().lat'),53.745,places=3)
      self.assertEqual(page.locator('[data-component]').count(),5)
      self.assertEqual(page.locator('script[src*="admin"]').count(),0)
      self.assertFalse(errors,errors)
      if label=='desktop':
       page.add_style_tag(content="body::after{content:'MOCK MAPPING LIBRARIES — NOT LIVE STREET MAP';position:fixed;z-index:9999;right:12px;top:10px;background:#fae9a1;color:#331;font:700 12px system-ui;padding:8px;border-radius:7px;pointer-events:none}")
       out=HQ.parent/'docs/hq/HQ-4.0.7-BRIDGE-MOCK-DESKTOP.png';page.screenshot(path=str(out));print('MOCK screenshot:',out)
      print('Chromium bridge MOCK:',label,'PASS')
      page.close()
   finally:browser.close()
if __name__=='__main__':unittest.main()
