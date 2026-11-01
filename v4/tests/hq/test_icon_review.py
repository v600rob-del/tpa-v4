"""Icon artwork approval test ONLY: genuine Chromium DOM, mocked network/MapLibre.
This is not proof of real street rendering, real vehicles or OS service isolation.
"""
import hashlib
import json
from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve()
PACKAGE=HERE.parents[3]
BASE=PACKAGE/'v4/hq'
FIXTURES=PACKAGE/'v4/tests/hq/fixtures/icon_review'
HTML=(BASE/'index.html').read_text(encoding='utf8')
CSS=(BASE/'assets/hq.css').read_text(encoding='utf8')
JS=(BASE/'assets/hq.js').read_text(encoding='utf8')
GEO=json.loads((BASE/'data/uk-geography.json').read_text(encoding='utf8'))
RESP={'/data/uk-geography.json':GEO,'/api/v4/manifest':{
  'contractVersion':'1.0','mockIconReview':True,
  'components':[{'id':name,'status':'ready'} for name in ('air','sea','rail','bus','road')]
}}
for name in ('air','sea','rail','bus','road'):
  RESP[f'/api/v4/components/{name}/snapshot']=json.loads((FIXTURES/(name+'.json')).read_text(encoding='utf8'))
CLEAN_HTML=HTML.replace('<link rel="stylesheet" href="/assets/hq.css"/>','').replace('<script src="/assets/hq.js" defer></script>','')

class TestIconReview(unittest.TestCase):
    def test_fixture_schema_static_and_labeled(self):
        self.assertIn('MOCK ICON REVIEW',HTML)
        self.assertIn('NO LIVE DATA',HTML)
        self.assertEqual(len(RESP)-2,5)
        count=0
        for name in ('air','sea','rail','bus','road'):
            frame=RESP[f'/api/v4/components/{name}/snapshot']
            self.assertEqual(frame['component'],name)
            self.assertEqual(frame['contractVersion'],'1.0')
            self.assertIn('MOCK',frame['attribution'])
            self.assertTrue(frame['entities'])
            for e in frame['entities']:
                self.assertIn('MOCK',e['label'])
                self.assertIn('headingDeg',e['position'])
                count+=1
        self.assertEqual(count,9)

    def test_historical_map_assets_not_modified(self):
        old=Path('/mnt/data/TPA-V4-HQ-TABLET-LAN-TEST-4.0.8/v4/hq')
        if not old.is_dir():self.skipTest('golden precursor not mounted')
        for relative in ('assets/tpa-basemap-loader.js','assets/transport-vector-layer.js','data/uk-geography.json'):
            self.assertEqual(hashlib.sha256((BASE/relative).read_bytes()).digest(),
                             hashlib.sha256((old/relative).read_bytes()).digest(),relative)

    def test_review_browser_desktop_tablet_mobile(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
            for label,width,height in (('DESKTOP',1440,900),('TABLET',700,1024),('MOBILE',390,844)):
                page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(CLEAN_HTML)
                page.add_style_tag(content=CSS)
                page.evaluate('''responses=>{
                  window.fetch=(url)=>{let key=String(url).split('?')[0];let obj=responses[key];
                    return Promise.resolve(new Response(JSON.stringify(obj||{error:'not-found'}),
                      {status:obj?200:503,headers:{'Content-Type':'application/json'}}));};
                }''',RESP)
                page.add_script_tag(content=JS)
                page.wait_for_function('window.TPA_HQ_READONLY_STATE && Object.values(window.TPA_HQ_READONLY_STATE().components).every(v=>v==="ready")',timeout=12000)
                page.wait_for_timeout(150)
                visible=page.locator('.review-pin').count()
                self.assertGreaterEqual(visible,7,(label,visible))
                self.assertEqual(page.locator('.mock-banner').count(),1)
                self.assertEqual(page.locator('#air-chip strong').inner_text(),'MOCK')
                self.assertEqual(page.locator('#road-chip strong').inner_text(),'MOCK')
                self.assertEqual(page.locator('.review-pin.air svg').count(),2)
                self.assertEqual(page.locator('.review-pin.sea svg').count(),2)
                self.assertEqual(page.locator('.review-pin.rail .review-route').count(),2)
                self.assertEqual(page.locator('.review-pin.bus .review-route').count(),2)
                self.assertEqual(page.locator('.review-pin.road').count(),1)
                page.locator('.review-pin.rail').first.click(force=True)
                page.wait_for_timeout(80)
                self.assertEqual(page.locator('.review-pin.selected-pin').count(),1)
                self.assertIn('FICTIONAL DEMONSTRATION',page.locator('#map-selection').inner_text())
                if label=='DESKTOP':
                    page.evaluate('document.querySelector("#map-wait").classList.add("hidden")')
                    page.screenshot(path=str(PACKAGE/'v4/docs/hq/ICON-REVIEW-SELECTED-MOCK-DESKTOP.png'))
                page.locator('.review-pin.rail').first.click(force=True)
                page.wait_for_timeout(80)
                self.assertEqual(page.locator('.review-pin.selected-pin').count(),0)
                if width<=760:
                    page.locator('#menu-button').click()
                    page.wait_for_timeout(280)
                    self.assertIn('open',page.locator('#sidebar').get_attribute('class'))
                page.evaluate('document.getElementById("bus-toggle").click()')
                page.wait_for_timeout(90)
                self.assertEqual(page.locator('.review-pin.bus').count(),0)
                page.evaluate('document.getElementById("bus-toggle").click()')
                page.wait_for_timeout(90)
                self.assertEqual(page.locator('.review-pin.bus').count(),2)
                page.locator('#search').fill('MOCK Train 2R91')
                page.locator('.search-result').first.click(force=True)
                page.wait_for_timeout(150)
                self.assertEqual(page.locator('.review-pin.selected-pin.rail').count(),1)
                self.assertIn('MOCK TRAIN 2R91',page.locator('#map-place').inner_text())
                page.locator('#home-button').click(force=True)
                page.wait_for_timeout(90)
                self.assertTrue(abs(page.evaluate('window.TPA_HQ_READONLY_STATE().lat')-53.842)<0.01)
                page.evaluate('document.querySelector("#map-wait").classList.add("hidden"); document.querySelector("#toast").classList.add("hidden")')
                if label=='DESKTOP':
                    page.screenshot(path=str(PACKAGE/'v4/docs/hq/ICON-REVIEW-MOCK-DESKTOP.png'))
                else:
                    page.screenshot(path=str(PACKAGE/f'v4/docs/hq/ICON-REVIEW-MOCK-{label}.png'))
                self.assertFalse(errors,(label,errors))
                print(f'{label}: {visible} mock markers, historical SVGs, road symbol, select/unselect, filter, search, Home PASS; screenshot is OFFLINE MOCK')
                page.close()
            browser.close()

if __name__=='__main__': unittest.main(verbosity=2)
