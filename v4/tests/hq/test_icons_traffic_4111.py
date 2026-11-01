"""HQ 4.1.11 + road engine 0.2.0 (owner, 31 Oct 2026 22:08):
- "the icons look weirdish, like they are re rendered. They need to stay the same."
- "we need to get the load time way down on the tomtom layer, it's loading in chunks whener it likes"

Real Chromium, real HQ, pretend bus/aircraft/traffic engines (the traffic one answers slowly).
1. A bus's and an aircraft's icon is the very same page element across new reports; the aircraft's
   icon turns to its new heading.
2. Traffic comes as 512-pixel pictures one zoom level down (a quarter as many requests).
3. Traffic pictures are asked for while the map is still being dragged, not only after.
4. A traffic refresh never blanks the layer: the old pictures stay until the new ones have arrived.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402


def png(w=8, h=8):
    raw = b''.join(b'\0' + b'\xff\x00\x00\xa0' * w for _ in range(h))
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


TILE = png()


class Fake:
    """One pretend engine: frame() -> snapshot; traffic pictures are slow (0.6 s)."""
    def __init__(self, frame):
        self.frame, self.tiles, self.asks = frame, [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith('/tiles/'):
                    outer.tiles.append((time.time(), self.path))
                    time.sleep(0.6)
                    self.send_response(200); self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(TILE))); self.end_headers(); self.wfile.write(TILE); return
                if self.path.startswith('/status'):
                    body = {'health': 'ready', 'notice': ''}
                else:
                    outer.asks += 1
                    body = outer.frame(outer.asks)
                raw = json.dumps(body).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def envelope(comp, entities, **extra):
    now = time.time()
    return dict({'component': comp, 'contractVersion': '1.0', 'health': 'ready', 'observedAt': base.iso(now),
                 'receivedAt': base.iso(now), 'expiresAt': base.iso(now + 300), 'nextPollAfterMs': 5000,
                 'attribution': 'PRETEND ' + comp, 'entities': entities}, **extra)


def bus_frame(n):
    return envelope('bus', [{'id': 'bus:1', 'label': 'BUSONE', 'badge': '42', 'quality': 'observed', 'observedAt': base.iso(time.time()),
                             'position': {'lon': -0.43, 'lat': 53.845, 'headingDeg': (n * 37) % 360}}])


def air_frame(n):
    return envelope('air', [{'id': 'air:1', 'label': 'PLANEONE', 'quality': 'observed', 'observedAt': base.iso(time.time()),
                             'position': {'lon': -0.40, 'lat': 53.85, 'headingDeg': (n * 50) % 360}},
                            # 4.1.16 test marker: exactly where BUSONE is (a centred icon shows the true map point)
                            {'id': 'air:mark', 'label': 'MARKER', 'quality': 'observed', 'observedAt': base.iso(time.time()),
                             'position': {'lon': -0.43, 'lat': 53.845, 'headingDeg': None}}])


def road_frame(n):
    return envelope('road', [], nextPollAfterMs=60000,
                    overlay={'type': 'raster', 'tiles': '/api/v4/components/road/tiles/{z}/{x}/{y}.png',
                             'minZoom': 8, 'maxZoom': 17, 'opacity': 0.85, 'refreshSeconds': 60, 'tileSize': 512})


VISIBLE_TRAFFIC = """() => { let n = 0;
  for (const c of document.querySelectorAll('.leaflet-tpa-socket-overlays-pane .leaflet-layer')) {
    if (parseFloat(getComputedStyle(c).opacity) <= 0.01) continue;
    n += c.querySelectorAll('img.leaflet-tile-loaded').length; }
  return n; }"""


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class IconsAndTraffic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():
                shutil.copyfile(base.VENDOR / src, target); cls.seeded.append(target)
        cls.bus, cls.air, cls.road = Fake(bus_frame), Fake(air_frame), Fake(road_frame)
        cls.port = base.free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith('TPA_HQ_')}
        engines = ','.join(f'{c}=http://127.0.0.1:{f.port}' for c, f in (('bus', cls.bus), ('air', cls.air), ('road', cls.road)))
        cls.hq = subprocess.Popen([sys.executable, str(base.HQ)], env=dict(env, TPA_HQ_PORT=str(cls.port), TPA_HQ_PARKED='sea',
                                  TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_ENGINES=engines),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                base.OPEN(f'http://127.0.0.1:{cls.port}/api/v4/manifest', timeout=.3); break
            except Exception:
                time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.hq.terminate(); cls.hq.wait(5)
        for f in (cls.bus, cls.air, cls.road):
            f.server.shutdown()
        for f in cls.seeded:
            f.unlink(missing_ok=True)

    def open(self, p):
        browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{self.port}/')
        page.locator('#tpa-welcome .tpa-primary').click()
        return browser, page, errors

    def wait(self, page, js, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if page.evaluate(js):
                return
            time.sleep(0.2)
        self.fail('timed out waiting for ' + js)

    def test_1_icons_stay_the_same(self):
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, "!!document.querySelector('.review-pin.bus') && !!document.querySelector('.review-pin.air')")
            page.evaluate("""() => { window.__art = {}; for (const b of document.querySelectorAll('.review-pin'))
                window.__art[b.title.split(' ')[0]] = b.querySelector('.review-art'); }""")
            asks = (self.bus.asks, self.air.asks)
            self.wait(page, f"true", 1)
            end = time.time() + 20
            while time.time() < end and (self.bus.asks < asks[0] + 2 or self.air.asks < asks[1] + 2):
                time.sleep(0.5)
            self.assertGreaterEqual(self.bus.asks, asks[0] + 2, 'no new reports arrived')
            time.sleep(1.2)
            same = page.evaluate("""() => { const out = {}; for (const b of document.querySelectorAll('.review-pin'))
                out[b.title.split(' ')[0]] = window.__art[b.title.split(' ')[0]] === b.querySelector('.review-art'); return out; }""")
            self.assertEqual(same.get('BUSONE'), True, 'bus icon was rebuilt')
            self.assertEqual(same.get('PLANEONE'), True, 'aircraft icon was rebuilt')
            turn = page.evaluate("document.querySelector('.review-pin.air svg').style.transform")
            self.assertEqual(turn, f'rotate({(self.air.asks * 50) % 360}deg)')       # turned to its latest heading
            self.assertEqual(errors, [])
            browser.close()

    def test_2_traffic_big_pictures_early_and_no_blanking(self):
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, VISIBLE_TRAFFIC.replace('return n; }', 'return n > 0; }'), 20)
            time.sleep(1.5)
            zooms = {int(path.split('/')[2]) for _, path in self.road.tiles}
            self.assertEqual(zooms, {10}, f'expected 512-pixel pictures one level down (map zoom 11): {zooms}')
            # pictures are asked for during a slow drag, before letting go
            self.road.tiles.clear()
            page.mouse.move(1000, 450); page.mouse.down()
            for i in range(1, 31):
                page.mouse.move(1000 - 20 * i, 450 - 8 * i); time.sleep(0.05)
            let_go = time.time(); page.mouse.up()
            time.sleep(1.5)
            self.assertTrue(any(t < let_go for t, _ in self.road.tiles), 'no traffic asked for until the drag ended')
            # refresh: jump the page clock past the next 60 s refresh and keep watching the layer
            time.sleep(1.5)
            before = page.evaluate(VISIBLE_TRAFFIC)
            self.assertGreater(before, 0)
            asked = len(self.road.tiles)
            page.clock.set_system_time(time.time() + 65)
            page.mouse.move(800, 400); page.mouse.down(); page.mouse.move(805, 402); page.mouse.up()   # a nudge repaints
            film = page.evaluate("""js => new Promise(done => { const f = eval(js), out = [], t0 = performance.now();
                (function s() { out.push(f()); if (performance.now() - t0 < 3000) requestAnimationFrame(s); else done(out); })(); })""",
                                 VISIBLE_TRAFFIC)
            self.assertGreater(len(self.road.tiles), asked, 'no refresh happened')
            self.assertGreater(min(film), 0, f'traffic blanked during the refresh: {film[:40]}')
            self.assertGreater(film[-1], 0)
            self.assertEqual(errors, [])
            browser.close()


    def test_3_bus_icons_sized_by_zoom(self):
        """4.1.12 (owner 31 Oct 22:11): smaller bus/train icons; larger at (nearly) maximum zoom."""
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, "!!document.querySelector('.review-pin.bus')")
            size = lambda c: page.evaluate(f"document.querySelector('.review-pin.{c}').offsetWidth")   # layout size
            small, plane = size('bus'), size('air')
            self.assertAlmostEqual(small, 28, delta=1.5)                 # zoom 11: 70%
            self.assertAlmostEqual(plane, 40, delta=1.5)                 # aircraft unchanged
            page.locator('#search').fill('BUSONE')                       # centre on the bus (zoom 13) ...
            page.locator('.search-result').first.click(); time.sleep(0.6)
            self.assertAlmostEqual(size('bus'), 33, delta=1.5)           # zoom 13: 82%
            for _ in range(4):                                           # ... then to zoom 17
                page.locator('#zoom-in').click(); time.sleep(0.45)
            time.sleep(0.6)
            self.assertEqual(page.evaluate('window.TPA_HQ_READONLY_STATE().zoom'), 17)
            self.assertAlmostEqual(size('bus'), 48, delta=1.5)           # maximum zoom: 120%
            self.assertEqual(errors, [])
            browser.close()


    def test_4_map_credit_names_live_sources(self):
        """4.1.15 (owner 31 Oct 23:47: "no mention of bods data"): the map credit line names the live
        sources of the layers shown, and drops one when its layer is switched off."""
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, "document.getElementById('map-credit').textContent.includes('BODS')")
            text = page.locator('#map-credit').inner_text()
            self.assertIn('Live data: ADSB.lol · BODS (DfT) · TomTom', text)
            self.assertIn('© OpenStreetMap contributors', text)
            page.locator('label[for="bus-toggle"]').click()
            self.wait(page, "!document.getElementById('map-credit').textContent.includes('BODS')", 5)
            self.assertEqual(errors, [])
            browser.close()


    def test_5_bus_stands_on_the_foot_of_its_number_board(self):
        """4.1.16 (owner 1 Nov 00:28): the bottom-middle of a bus's number board is exactly on the map
        point - at every zoom size, and when selected (enlarged)."""
        FOOT = """() => { const b = document.querySelector('.review-pin.bus .review-route').getBoundingClientRect();
            const m = [...document.querySelectorAll('.review-pin.air')].find(x => x.title.startsWith('MARKER')).getBoundingClientRect();
            return [b.left + b.width / 2 - (m.left + m.width / 2), b.bottom - (m.top + m.height / 2)]; }"""
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, "!!document.querySelector('.review-pin.bus') && document.querySelectorAll('.review-pin.air').length >= 2")
            time.sleep(1.2)
            checks = []
            dx, dy = page.evaluate(FOOT); checks.append(('zoom 11', dx, dy))
            page.locator('#search').fill('BUSONE'); page.locator('.search-result').first.click(); time.sleep(1.2)
            dx, dy = page.evaluate(FOOT); checks.append(('zoom 13, selected', dx, dy))
            for _ in range(4):
                page.locator('#zoom-in').click(); time.sleep(0.45)
            time.sleep(1.2)
            dx, dy = page.evaluate(FOOT); checks.append(('zoom 17, selected', dx, dy))
            for label, dx, dy in checks:
                self.assertLess(abs(dx), 1.5, f'{label}: board foot {dx:.1f} px sideways of the point')
                self.assertLess(abs(dy), 1.5, f'{label}: board foot {dy:.1f} px above/below the point')
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    unittest.main()
