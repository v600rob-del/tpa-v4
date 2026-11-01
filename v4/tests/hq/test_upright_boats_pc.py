"""PC-only real Chromium DOM artwork check, with mock network/map data."""
from pathlib import Path
import json,unittest
from playwright.sync_api import sync_playwright
DIR=Path(__file__).resolve().parents[2]/'hq'
FIX=Path(__file__).resolve().parent/'fixtures/icon_review'
HTML=(DIR/'index.html').read_text()
CSS=(DIR/'assets/hq.css').read_text()
JS=(DIR/'assets/hq.js').read_text()
RESP={'/api/v4/manifest':{'contractVersion':'1.0','components':[{'id':x,'status':'ready'} for x in ('air','sea','rail','bus','road')]},'/data/uk-geography.json':{'land':[],'coastlines':[]}}
for comp in ('air','sea','rail','bus','road'):
    RESP['/api/v4/components/'+comp+'/snapshot']=json.loads((FIX/(comp+'.json')).read_text())

class UprightBoatTest(unittest.TestCase):
    def test_fixture_heading_data_unchanged(self):
        self.assertEqual([x['position']['headingDeg'] for x in RESP['/api/v4/components/sea/snapshot']['entities']],[80,225])
        self.assertEqual([x['position']['headingDeg'] for x in RESP['/api/v4/components/air/snapshot']['entities']],[120,210])
    def test_actual_desktop_chromium_svg(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':900})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content(HTML.replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/hq.js" defer></script>',''))
            page.add_style_tag(content=CSS)
            page.evaluate("""r=>window.fetch=(u)=>{const x=r[String(u).split('?')[0]];return Promise.resolve(new Response(JSON.stringify(x||{}),{status:x?200:503,headers:{'Content-Type':'application/json'}}))}""",RESP)
            page.add_script_tag(content=JS)
            page.wait_for_function("window.TPA_HQ_READONLY_STATE && ['air','sea','rail','bus','road'].every(k=>window.TPA_HQ_READONLY_STATE().components[k]==='ready')",timeout=10000)
            page.wait_for_timeout(100)
            ship=page.locator('.review-pin.sea svg')
            plane=page.locator('.review-pin.air svg')
            self.assertEqual(ship.count(),2)
            self.assertEqual(plane.count(),2)
            self.assertEqual(ship.evaluate_all('(xs)=>xs.map(x=>x.style.transform)'),['',''])
            self.assertEqual(plane.evaluate_all('(xs)=>xs.map(x=>x.style.transform)'),['rotate(120deg)','rotate(210deg)'])
            self.assertEqual(ship.evaluate_all('(xs)=>xs.map(x=>getComputedStyle(x).transform)'),['none','none'])
            page.locator('.review-pin.sea').first.click(force=True)
            # HQ redraws on the next screen frame; wait for it rather than checking the same instant.
            page.wait_for_function("document.querySelectorAll('.review-pin.sea.selected-pin').length===1",timeout=3000)
            self.assertEqual(page.locator('.review-pin.sea.selected-pin').count(),1)
            self.assertIn('FICTIONAL DEMONSTRATION',page.locator('#map-selection').inner_text())
            self.assertEqual(page.locator('.review-pin.sea.selected-pin svg').evaluate('(x)=>x.style.transform'),'')
            self.assertEqual(page.evaluate('Object.keys(window.TPA_HQ_SOCKETS).length'),5)
            page.evaluate('document.querySelector("#map-wait").classList.add("hidden");document.querySelector("#toast").classList.add("hidden")')
            page.screenshot(path=str(DIR.parent/'docs/hq/BOATS-UPRIGHT-MOCK-PC-SELECTED.png'))
            page.locator('#home-button').click(force=True)
            self.assertLess(abs(page.evaluate('window.TPA_HQ_READONLY_STATE().lat')-53.842),.01)
            self.assertFalse(errors,errors)
            page.close();browser.close()
if __name__=='__main__':unittest.main(verbosity=2)
