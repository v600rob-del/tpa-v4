"""HQ 4.0.9 checks: welcome screen, optional tip link, Data Sources & Credits,
and a server-parked socket ("coming soon"). Real Chromium against the real
local HQ server with the owner's actual START settings (sea parked).
Mock fixtures only: this proves page behaviour, never live transport.
"""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

PACKAGE = Path(__file__).resolve().parents[3]
HQ = PACKAGE / 'v4/hq'
FIXTURES = PACKAGE / 'v4/tests/hq/fixtures/icon_review'
IDS = ('air', 'sea', 'rail', 'bus', 'road')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class ParkedServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, TPA_HQ_PORT=str(cls.port), TPA_HQ_LAN_TEST='0', TPA_HQ_PARKED='sea',
                   TPA_HQ_FIXTURE_DIR=str(FIXTURES), TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_OPEN_BROWSER='0')
        cls.proc = subprocess.Popen([sys.executable, str(HQ / 'serve_hq.py')], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                with urlopen(f'http://127.0.0.1:{cls.port}/api/v4/manifest', timeout=.3):
                    break
            except Exception:
                time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    def get(self, path):
        try:
            with urlopen(f'http://127.0.0.1:{self.port}{path}', timeout=3) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def test_manifest_parks_sea_only(self):
        status, data = self.get('/api/v4/manifest')
        self.assertEqual(status, 200)
        self.assertRegex(data['hq'], r'^4\.\d+\.\d+-ICON-REVIEW-MOCK$')
        self.assertFalse(data['transportFeedsEnabled'])
        by_id = {c['id']: c for c in data['components']}
        self.assertEqual(list(by_id), list(IDS))
        self.assertEqual(by_id['sea'], {'id': 'sea', 'status': 'unavailable', 'parked': True})
        for other in ('air', 'rail', 'bus', 'road'):
            self.assertEqual(by_id[other]['status'], 'ready')
            self.assertNotIn('parked', by_id[other])

    def test_parked_sea_serves_no_data_even_with_fixture(self):
        self.assertTrue((FIXTURES / 'sea.json').is_file())
        status, data = self.get('/api/v4/components/sea/snapshot')
        self.assertEqual(status, 503)
        self.assertTrue(data['parked'])
        self.assertNotIn('entities', data)
        status, data = self.get('/api/v4/components/bus/snapshot')
        self.assertEqual(status, 200)
        self.assertTrue(data['entities'])

    def test_site_config_served_and_honest(self):
        status, cfg = self.get('/data/site-config.json')
        self.assertEqual(status, 200)
        self.assertEqual(cfg['configVersion'], 1)
        self.assertTrue(cfg['welcome']['enabled'])
        joined = ' '.join(cfg['welcome']['lines']).lower()
        self.assertIn('free', joined)
        self.assertIn('pretend', joined)  # never implies mock icons are live
        self.assertEqual(cfg['tip']['url'], '')  # no tip account linked yet
        bods = [c for c in cfg['credits'] if c['layer'] == 'Buses'][0]
        self.assertIn("Bus Open Data Service (BODS)", bods['text'])
        self.assertEqual(bods['url'], 'https://www.bus-data.dft.gov.uk')

    def test_browser_welcome_tip_credits_and_parked_sea(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            for label, w, h in (('DESKTOP', 1440, 900), ('TABLET', 768, 1024), ('MOBILE', 390, 844)):
                page = browser.new_page(viewport={'width': w, 'height': h})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{self.port}/')
                # Welcome appears on first visit, with honest wording and a disabled tip link.
                page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=8000)
                self.assertIn('Welcome to TPA', page.locator('#tpa-welcome').inner_text())
                self.assertIn('pretend', page.locator('#tpa-welcome').inner_text())
                tip = page.locator('#tpa-welcome .tip-button')
                self.assertTrue(tip.is_disabled(), label)
                self.assertIn('coming soon', tip.inner_text())
                # Welcome -> credits route works.
                page.locator('#tpa-welcome .tpa-link-button').click()
                page.wait_for_selector('#tpa-credits:not(.hidden)')
                self.assertTrue(page.locator('#tpa-welcome').is_hidden())
                credits = page.locator('#tpa-credits').inner_text()
                self.assertIn('Bus Open Data Service (BODS)', credits)
                self.assertIn('OpenStreetMap', credits)
                link = page.locator('#tpa-credits a.credit-link').first
                self.assertEqual(link.get_attribute('target'), '_blank')
                self.assertIn('noopener', link.get_attribute('rel'))
                page.keyboard.press('Escape')
                self.assertTrue(page.locator('#tpa-credits').is_hidden())
                # Remembered: a reload does not show the welcome again...
                page.reload()
                page.wait_for_selector('#welcome-button', state='attached', timeout=8000)
                page.wait_for_timeout(300)
                self.assertTrue(page.locator('#tpa-welcome').is_hidden(), label)
                # ...but it can be reopened from the sidebar.
                page.evaluate("document.getElementById('welcome-button').click()")
                page.wait_for_selector('#tpa-welcome:not(.hidden)')
                page.locator('#tpa-welcome .tpa-primary').click()
                self.assertTrue(page.locator('#tpa-welcome').is_hidden())
                # Ships parked: locked toggle, "coming soon", no ship markers; others unaffected.
                page.wait_for_function("window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().components.bus==='ready'", timeout=8000)
                state = page.evaluate('window.TPA_HQ_READONLY_STATE().components')
                self.assertEqual(state['sea'], 'unavailable')
                for other in ('air', 'rail', 'bus', 'road'):
                    self.assertEqual(state[other], 'ready', (label, other))
                self.assertTrue(page.locator('#sea-toggle').is_disabled())
                self.assertFalse(page.locator('#sea-toggle').is_checked())
                self.assertEqual(page.locator('#sea-source-status').inner_text(), 'Unavailable · coming soon')
                self.assertEqual(page.locator('#sea-chip strong').inner_text(), 'SOON')
                self.assertEqual(page.locator('.review-pin.sea').count(), 0)
                self.assertGreater(page.locator('.review-pin.bus').count(), 0)
                self.assertEqual(errors, [], label)
                page.close()
            browser.close()


class NoConfigMeans408Behaviour(unittest.TestCase):
    """If the settings file is absent, the page behaves exactly like 4.0.8: no overlay."""
    def test_missing_config_shows_no_welcome(self):
        html = (HQ / 'index.html').read_text(encoding='utf8')
        css = (HQ / 'assets/hq.css').read_text(encoding='utf8')
        js = (HQ / 'assets/hq.js').read_text(encoding='utf8')
        clean = html.replace('<link rel="stylesheet" href="/assets/hq.css"/>', '').replace('<script src="/assets/hq.js" defer></script>', '')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content(clean)
            page.add_style_tag(content=css)
            page.evaluate("window.fetch=()=>Promise.resolve(new Response('{}',{status:503}))")
            page.add_script_tag(content=js)
            page.wait_for_timeout(500)
            self.assertEqual(page.locator('#tpa-welcome').count(), 0)
            self.assertEqual(page.locator('.about-panel').count(), 0)
            self.assertFalse(page.locator('#sea-toggle').is_disabled())
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    unittest.main()
