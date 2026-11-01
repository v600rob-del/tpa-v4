"""Live Chromium JS/DOM exercising mocked data-only gateway state changes.
NOT a real transport feed, real gateway authentication or runtime process-isolation proof.
"""
import copy
import json
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

DIR=Path(__file__).resolve().parents[3]
BASE=DIR/'v4/hq'
FIX=DIR/'v4/tests/hq/fixtures/icon_review'
IDS=('air','sea','rail','bus','road')
HTML=(BASE/'index.html').read_text(encoding='utf8').replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/hq.js" defer></script>','')
CSS=(BASE/'assets/hq.css').read_text(encoding='utf8')
JS=(BASE/'assets/hq.js').read_text(encoding='utf8')
GEO=json.loads((BASE/'data/uk-geography.json').read_text(encoding='utf8'))
FRAMES={id:json.loads((FIX/f'{id}.json').read_text(encoding='utf8')) for id in IDS}

class SocketIsolation(unittest.TestCase):
    def test_5_sockets_attach_detach_invalid_recover_and_stale(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':900})
            errors=[]
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.set_content(HTML)
            page.add_style_tag(content=CSS)
            page.evaluate('''({geo,frames})=>{
              window.__geo=geo;
              window.__frames=frames;
              window.__registry=Object.fromEntries(['air','sea','rail','bus','road'].map(id=>[id,'not_installed']));
              window.__errors={};
              window.__calls=Object.fromEntries(['air','sea','rail','bus','road'].map(id=>[id,0]));
              const oldInterval=window.setInterval.bind(window),oldTimeout=window.setTimeout.bind(window);
              window.setInterval=(cb,period)=>period===30000?(window.__tick=cb,101):oldInterval(cb,period);
              window.setTimeout=(cb,ms,...args)=>oldTimeout(cb,ms===5000?180:ms,...args);
              window.fetch=(url,opt={})=>{
                let key=String(url).split('?')[0];
                let data;
                if(key==='/data/uk-geography.json')data=window.__geo;
                else if(key==='/api/v4/manifest')data={contractVersion:'1.0',components:Object.entries(window.__registry).map(([id,status])=>({id,status}))};
                else if(key.includes('/snapshot')){
                  let id=key.split('/')[4];window.__calls[id]++;
                  if(window.__errors[id]==='hang')return new Promise((resolve,reject)=>{
                    opt.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
                  });
                  if(window.__errors[id]==='bad')data={...window.__frames[id],entities:[{id:'wrong',label:'wrong',quality:'observed',observedAt:'2026-10-29T21:00:00Z',position:{lon:-0.4,lat:'bad'}}]};
                  else if(window.__errors[id]==='http')return Promise.resolve(new Response('down',{status:503}));
                  else data=window.__frames[id];
                } else return Promise.resolve(new Response('{}',{status:200}));
                return Promise.resolve(new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}}));
              };
            }''',{'geo':GEO,'frames':FRAMES})
            page.add_script_tag(content=JS)
            page.wait_for_function('window.__tick && window.TPA_HQ_READONLY_STATE')
            # All sockets empty at startup; a new attachment requires only manifest + JSON.
            page.wait_for_function("Object.values(window.TPA_HQ_READONLY_STATE().components).every(v=>v==='not_installed')")
            self.assertEqual(page.locator('.review-pin').count(),0)
            page.evaluate("Object.keys(window.__registry).forEach(k=>window.__registry[k]='ready')")
            page.evaluate('window.__tick()')
            page.wait_for_function("Object.values(window.TPA_HQ_READONLY_STATE().components).every(v=>v==='ready')")
            page.wait_for_function("document.querySelectorAll('.review-pin').length===9")
            self.assertEqual(page.locator('.review-pin.rail').count(),2)
            self.assertEqual(page.locator('.review-pin.bus').count(),2)
            # Remove rail: do NOT disturb bus and other four layer snapshots.
            page.locator('.review-pin.rail').first.click(force=True)
            self.assertIn('MOCK Train',page.locator('#map-selection').inner_text())
            page.evaluate("window.__registry.rail='not_installed'")
            page.evaluate('window.__tick()')
            page.wait_for_function("window.TPA_HQ_READONLY_STATE().components.rail==='not_installed'")
            page.wait_for_function("document.querySelectorAll('.review-pin.rail').length===0")
            self.assertEqual(page.locator('.review-pin.bus').count(),2)
            self.assertIn('hidden',page.locator('#map-selection').get_attribute('class'))
            # Reattach rail then return an invalid point: only rail marks unavailable.
            page.evaluate("window.__registry.rail='ready';window.__errors.rail='bad'")
            page.evaluate('window.__tick()')
            page.wait_for_function("window.TPA_HQ_READONLY_STATE().components.rail==='unavailable'")
            self.assertEqual(page.locator('.review-pin.rail').count(),0)
            self.assertEqual(page.locator('.review-pin.bus').count(),2)
            self.assertEqual(page.locator('#bus-chip strong').inner_text(),'MOCK')
            # Recover rail without modifying the bus frame or HQ code.
            page.evaluate("window.__errors.rail=''")
            page.evaluate('window.__tick()')
            page.wait_for_function("window.TPA_HQ_READONLY_STATE().components.rail==='ready'")
            page.wait_for_function("document.querySelectorAll('.review-pin.rail').length===2")
            # A slow/hung rail request cannot stop another bus refresh.
            page.evaluate("window.__errors.rail='hang';window.__frames.bus.entities[0].label='MOCK Bus Updated'")
            page.evaluate('window.__tick()')
            page.wait_for_function("document.querySelector('.review-pin.bus')?.title?.includes('Updated')")
            page.wait_for_function("window.TPA_HQ_READONLY_STATE().components.rail==='unavailable'")
            page.wait_for_function("document.querySelectorAll('.review-pin.rail.stale').length===2")
            self.assertEqual(page.locator('.review-pin.bus').count(),2)
            self.assertEqual(page.locator('#rail-chip strong').inner_text(),'UNAVAILABLE')
            self.assertIn('stale last-known',page.locator('#rail-source-status').inner_text())
            page.evaluate('document.getElementById("map-wait").classList.add("hidden")')
            page.screenshot(path=str(DIR/'v4/docs/hq/SOCKET-MOCK-RAIL-FAILED-BUS-OK.png'))
            # Injected markup is inert text, never executable HTML.
            page.evaluate("window.__errors.rail='';window.__frames.rail.entities[0].label='<img src=x onerror=alert(99)>'")
            page.evaluate('window.__tick()')
            page.wait_for_function("window.TPA_HQ_READONLY_STATE().components.rail==='ready'")
            page.wait_for_function("document.querySelector('.review-pin.rail')?.title?.includes('<img')")
            page.locator('.review-pin.rail').first.click(force=True)
            self.assertIn('<img',page.locator('#map-selection').inner_text())
            self.assertEqual(page.locator('#map-selection img').count(),0)
            self.assertEqual(errors,[],errors)
            browser.close()

if __name__=='__main__':unittest.main(verbosity=2)
