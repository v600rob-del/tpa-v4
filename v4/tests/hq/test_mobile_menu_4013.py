"""HQ 4.0.13 phone layers menu (owner, 30 Oct 2026: "the layers button becomes invisible
when the layers menu is up"). On a phone the open menu covers the LAYERS button, so the
menu must have its own CLOSE button and tapping the map beside it must close it too.
Real Chromium at real phone sizes; mock fixtures only (page behaviour, not live data).
"""
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

PACKAGE = Path(__file__).resolve().parents[3]
HQ = PACKAGE / 'v4/hq'
FIXTURES = PACKAGE / 'v4/tests/hq/fixtures/icon_review'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def topmost_is(page, selector):
    """True if the element is on screen and nothing covers its centre (a finger could tap it)."""
    return page.evaluate("""sel => { const el = document.querySelector(sel); if (!el) return false;
        const r = el.getBoundingClientRect(); if (!r.width || !r.height) return false;
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return false;
        const hit = document.elementFromPoint(x, y); return !!hit && (hit === el || el.contains(hit)); }""", selector)


class MobileMenu(unittest.TestCase):
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

    def open_page(self, browser, w, h):
        page = browser.new_page(viewport={'width': w, 'height': h}, has_touch=w < 800, is_mobile=w < 800)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{self.port}/')
        page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=8000)
        page.locator('#tpa-welcome .tpa-primary').click()
        return page, errors

    def test_phone_menu_can_always_be_closed(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            for w, h in ((390, 844), (360, 740), (430, 932), (700, 1000)):
                page, errors = self.open_page(browser, w, h)
                size = f'{w}x{h}'
                self.assertTrue(topmost_is(page, '#menu-button'), size)
                self.assertFalse(topmost_is(page, '#sidebar-close'), size)   # off-screen while the menu is shut
                # Open: the menu shows its own CLOSE button, reachable by a finger.
                page.locator('#menu-button').tap()
                page.wait_for_timeout(400)
                self.assertIn('open', page.locator('#sidebar').get_attribute('class'))
                self.assertEqual(page.locator('#menu-button').get_attribute('aria-expanded'), 'true')
                self.assertTrue(topmost_is(page, '#sidebar-close'), size)
                page.locator('#sidebar-close').tap()
                page.wait_for_timeout(400)
                self.assertNotIn('open', page.locator('#sidebar').get_attribute('class'), size)
                self.assertEqual(page.locator('#menu-button').get_attribute('aria-expanded'), 'false')
                self.assertTrue(topmost_is(page, '#menu-button'), size)
                # Open again, close by tapping the map beside the menu.
                page.locator('#menu-button').tap()
                page.wait_for_timeout(400)
                box = page.locator('#sidebar').bounding_box()
                self.assertLess(box['x'] + box['width'], w - 30, f'{size}: no map strip left to tap')
                page.touchscreen.tap(w - 15, h // 2)
                page.wait_for_timeout(400)
                self.assertNotIn('open', page.locator('#sidebar').get_attribute('class'), size)
                # Close still works after scrolling the menu down (the button stays at the top).
                page.locator('#menu-button').tap(); page.wait_for_timeout(400)
                page.evaluate("document.getElementById('sidebar').scrollTop = 99999")
                page.wait_for_timeout(200)
                self.assertTrue(topmost_is(page, '#sidebar-close'), size + ' after scroll')
                page.locator('#sidebar-close').tap(); page.wait_for_timeout(400)
                self.assertNotIn('open', page.locator('#sidebar').get_attribute('class'), size)
                # Layer switches still work inside the menu.
                page.locator('#menu-button').tap(); page.wait_for_timeout(400)
                page.locator('label[for="bus-toggle"]').tap()
                self.assertFalse(page.locator('#bus-toggle').is_checked(), size)
                self.assertEqual(errors, [], size)
                if (w, h) == (390, 844):
                    page.wait_for_timeout(600)   # let the switch finish sliding
                    page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.0.13-PHONE-MENU-OPEN.png'))
                page.close()
            browser.close()

    def test_desktop_unchanged(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page, errors = self.open_page(browser, 1440, 900)
            self.assertTrue(page.locator('#sidebar-close').is_hidden())
            self.assertTrue(page.locator('#sidebar-backdrop').is_hidden())
            self.assertTrue(page.locator('#menu-button').is_hidden())
            self.assertTrue(page.locator('#sidebar').is_visible())
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    unittest.main()
