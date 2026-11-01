"""Road traffic engine 0.1 + HQ 4.0.10 tests.

A PRETEND TomTom server on 127.0.0.1 stands in for TomTom; it serves tiny PNGs.
The real road engine and real HQ server run as separate processes, exactly as
on the owner's PC. This proves engine behaviour, safety limits, failure
isolation and HQ display. It does NOT prove TomTom's real service or key.
"""
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

PACKAGE = Path(__file__).resolve().parents[3]
ENGINE = PACKAGE / 'v4/engines/road/road_engine.py'
HQ = PACKAGE / 'v4/hq/serve_hq.py'
TEST_KEY = 'PRETEND-TEST-KEY-7f3a9c'
OPEN = build_opener(ProxyHandler({})).open


def png(rgb=(200, 40, 40)):
    """A real 1x1 PNG made with the standard library."""
    def chunk(kind, data):
        return len(data).to_bytes(4, 'big') + kind + data + zlib.crc32(kind + data).to_bytes(4, 'big')
    ihdr = (1).to_bytes(4, 'big') * 2 + bytes([8, 2, 0, 0, 0])
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(rgb))) + chunk(b'IEND', b'')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def tile_for(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


class FakeTomTom:
    """Records every request so tests can count real upstream calls."""
    def __init__(self, status=200, delay=0.0):
        self.requests, self.status, self.delay = [], status, delay
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({'path': self.path, 'key': self.headers.get('TomTom-Api-Key')})
                time.sleep(outer.delay)
                if outer.status != 200:
                    self.send_response(outer.status); self.send_header('Content-Length', '0'); self.end_headers(); return
                body = png()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Cache-Control', 'public, max-age=300')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def template(self):
        return f'http://127.0.0.1:{self.port}/tile/{{z}}/{{x}}/{{y}}'

    def close(self):
        self.server.shutdown()


def start(cmd, env, ready_url, out):
    proc = subprocess.Popen([sys.executable, str(cmd)], env=env, stdout=out, stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            with OPEN(ready_url, timeout=.3):
                return proc
        except Exception:
            time.sleep(.1)
    proc.terminate()
    raise RuntimeError('did not start: ' + str(cmd))


def wait_until(page, expression, timeout=10.0):
    """Poll with page.evaluate (HQ's strict security policy blocks eval-based waiting)."""
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(expression):
            return
        time.sleep(0.15)
    raise AssertionError('timed out waiting for: ' + expression)


def get(url):
    try:
        with OPEN(url, timeout=20) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read()
    except HTTPError as e:
        return e.code, '', e.read()


class RoadEngine(unittest.TestCase):
    def engine(self, fake=None, key=TEST_KEY, cap=None):
        port = free_port()
        data = Path(tempfile.mkdtemp(prefix='tpa-road-test-'))
        self.addCleanup(shutil.rmtree, data, True)
        env = dict(os.environ, TPA_ROAD_ENGINE_PORT=str(port), TPA_ROAD_DATA_DIR=str(data))
        env.pop('TOMTOM_API_KEY', None)
        if key:
            env['TOMTOM_API_KEY'] = key
        if fake:
            env['TPA_ROAD_TOMTOM_URL'] = fake.template
        if cap is not None:
            env['TPA_ROAD_MONTHLY_CAP'] = str(cap)
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        proc = start(ENGINE, env, f'http://127.0.0.1:{port}/status', log)
        self.addCleanup(proc.wait, 5)
        self.addCleanup(proc.terminate)
        return f'http://127.0.0.1:{port}', log

    def fake(self, **kw):
        f = FakeTomTom(**kw)
        self.addCleanup(f.close)
        return f

    def test_status_and_frame_contract(self):
        base, _ = self.engine(self.fake())
        status = json.loads(get(base + '/status')[2])
        self.assertEqual((status['component'], status['health'], status['live'], status['keyConfigured']), ('road', 'ready', True, True))
        self.assertNotIn(TEST_KEY, json.dumps(status))
        frame = json.loads(get(base + '/snapshot')[2])
        self.assertEqual((frame['component'], frame['contractVersion'], frame['health']), ('road', '1.0', 'ready'))
        self.assertEqual(frame['entities'], [])
        self.assertEqual(frame['overlay']['tiles'], '/api/v4/components/road/tiles/{z}/{x}/{y}.png')
        self.assertEqual((frame['overlay']['minZoom'], frame['overlay']['maxZoom']), (8, 17))
        self.assertIn('TomTom', frame['attribution'])

    def test_tile_fetched_once_then_shared_from_cache(self):
        fake = self.fake()
        base, _ = self.engine(fake)
        x, y = tile_for(53.842, -0.423, 12)  # Beverley
        for _ in range(4):
            status, ctype, body = get(f'{base}/tiles/12/{x}/{y}.png')
            self.assertEqual((status, ctype), (200, 'image/png'))
            self.assertTrue(body.startswith(b'\x89PNG'))
        self.assertEqual(len(fake.requests), 1)
        # Key travels only in the header, never in the address.
        self.assertEqual(fake.requests[0]['key'], TEST_KEY)
        self.assertNotIn(TEST_KEY, fake.requests[0]['path'])
        self.assertIn('style=dark', fake.requests[0]['path'])

    def test_many_viewers_same_tile_one_upstream_request(self):
        fake = self.fake(delay=0.4)
        base, _ = self.engine(fake)
        x, y = tile_for(53.745, -0.334, 13)  # Hull
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: get(f'{base}/tiles/13/{x}/{y}.png')[0], range(8)))
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(fake.requests), 1)

    def test_outside_uk_and_wrong_zoom_never_reach_tomtom(self):
        fake = self.fake()
        base, _ = self.engine(fake)
        px, py = tile_for(48.858, 2.294, 12)   # Paris
        nx, ny = tile_for(40.71, -74.0, 10)    # New York
        bx, by = tile_for(53.842, -0.423, 5)   # Beverley, but zoomed out too far
        for path in (f'/tiles/12/{px}/{py}.png', f'/tiles/10/{nx}/{ny}.png', f'/tiles/5/{bx}/{by}.png', '/tiles/18/1/1.png', '/tiles/12/99999/1.png'):
            self.assertEqual(get(base + path)[0], 204, path)
        self.assertEqual(fake.requests, [])

    def test_no_key_means_zero_requests(self):
        fake = self.fake()
        base, _ = self.engine(fake, key='')
        x, y = tile_for(53.842, -0.423, 12)
        self.assertEqual(get(f'{base}/tiles/12/{x}/{y}.png')[0], 204)
        status = json.loads(get(base + '/status')[2])
        self.assertEqual(status['health'], 'unavailable')
        self.assertEqual(status['notice'], 'TomTom key not set up on this PC')
        self.assertEqual(fake.requests, [])

    def test_monthly_safety_cap_pauses_layer(self):
        fake = self.fake()
        base, _ = self.engine(fake, cap=2)
        tiles = [tile_for(53.842, -0.423, 12), tile_for(53.745, -0.334, 12), tile_for(53.96, -1.08, 12)]
        codes = [get(f'{base}/tiles/12/{x}/{y}.png')[0] for x, y in tiles]
        self.assertEqual(codes, [200, 200, 204])
        self.assertEqual(len(fake.requests), 2)
        status = json.loads(get(base + '/status')[2])
        self.assertEqual(status['health'], 'unavailable')
        self.assertIn('safety limit', status['notice'])

    def test_rejected_key_reported_plainly_and_never_logged(self):
        fake = self.fake(status=403)
        base, log = self.engine(fake)
        for i in range(6):
            x, y = tile_for(53.842 + i * 0.05, -0.423, 12)
            self.assertEqual(get(f'{base}/tiles/12/{x}/{y}.png')[0], 204)
        status = json.loads(get(base + '/status')[2])
        self.assertEqual((status['health'], status['notice']), ('unavailable', 'TomTom did not accept the key'))
        log.seek(0)
        output = log.read().decode('utf-8', 'replace')
        self.assertIn('tomtom-http-403', output)
        self.assertNotIn(TEST_KEY, output)


try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
VENDOR = Path(os.environ.get('TPA_TEST_VENDOR_DIR', '/nonexistent'))


@unittest.skipUnless(sync_playwright and (VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class HQWithRoadEngine(unittest.TestCase):
    """Real HQ + real road engine + pretend TomTom, in real Chromium."""
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeTomTom()
        cls.data = Path(tempfile.mkdtemp(prefix='tpa-road-hq-'))
        # Seed HQ's own vendor cache with the pinned libraries (workspace has no CDN access).
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css',
                           '/assets/vendor/maplibre-gl.js': 'maplibre/dist/maplibre-gl.js', '/assets/vendor/maplibre-gl.css': 'maplibre/dist/maplibre-gl.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():  # remember only what this test added, to remove it afterwards
                shutil.copyfile(VENDOR / src, target)
                cls.seeded.append(target)
        cls.engine_port, cls.hq_port = free_port(), free_port()
        env = dict(os.environ, TPA_ROAD_ENGINE_PORT=str(cls.engine_port), TPA_ROAD_DATA_DIR=str(cls.data),
                   TOMTOM_API_KEY=TEST_KEY, TPA_ROAD_TOMTOM_URL=cls.fake.template)
        cls.log = tempfile.TemporaryFile()
        cls.engine = start(ENGINE, env, f'http://127.0.0.1:{cls.engine_port}/status', cls.log)
        hq_env = dict(os.environ, TPA_HQ_PORT=str(cls.hq_port), TPA_HQ_LAN_TEST='0', TPA_HQ_PARKED='sea',
                      TPA_HQ_ENGINES=f'road=http://127.0.0.1:{cls.engine_port}', TPA_HQ_DISABLE_MAP_NETWORK='1')
        hq_env.pop('TOMTOM_API_KEY', None)
        hq_env.pop('TPA_HQ_FIXTURE_DIR', None)
        cls.hq_log = tempfile.TemporaryFile()
        cls.hq = start(HQ, hq_env, f'http://127.0.0.1:{cls.hq_port}/api/v4/manifest', cls.hq_log)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.hq, cls.engine):
            if p.poll() is None:
                p.terminate(); p.wait(5)
        cls.fake.close()
        shutil.rmtree(cls.data, True)
        for f in cls.seeded:  # leave HQ's shared cache exactly as we found it
            f.unlink(missing_ok=True)

    def test_1_manifest_shows_road_live_others_untouched(self):
        data = json.loads(get(f'http://127.0.0.1:{self.hq_port}/api/v4/manifest')[2])
        by = {c['id']: c for c in data['components']}
        self.assertEqual(by['road'], {'id': 'road', 'status': 'ready', 'live': True})
        self.assertEqual(by['sea']['status'], 'unavailable')
        for other in ('air', 'rail', 'bus'):
            self.assertEqual(by[other]['status'], 'not_installed')
        self.assertTrue(data['transportFeedsEnabled'])

    def test_2_hq_relays_tiles_and_rejects_bad_paths(self):
        x, y = tile_for(53.842, -0.423, 12)
        base = f'http://127.0.0.1:{self.hq_port}/api/v4/components'
        status, ctype, body = get(f'{base}/road/tiles/12/{x}/{y}.png')
        self.assertEqual((status, ctype), (200, 'image/png'))
        self.assertTrue(body.startswith(b'\x89PNG'))
        for bad in ('/bus/tiles/12/1/1.png', '/road/tiles/12/1/../1.png', '/road/tiles/x/1/1.png', '/road/tiles/12/1/1.jpg', '/sea/tiles/12/1/1.png'):
            self.assertIn(get(base + bad)[0], (404, 204), bad)

    def test_3_browser_draws_live_traffic_layer(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            for label, w, h in (('DESKTOP', 1440, 900), ('MOBILE', 390, 844)):
                page = browser.new_page(viewport={'width': w, 'height': h})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                tiles = []
                page.on('response', lambda r: tiles.append(r.status) if '/api/v4/components/road/tiles/' in r.url else None)
                page.goto(f'http://127.0.0.1:{self.hq_port}/')
                page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
                page.locator('#tpa-welcome .tpa-primary').click()
                wait_until(page, "window.TPA_HQ_READONLY_STATE && window.TPA_HQ_READONLY_STATE().components.road==='ready'")
                wait_until(page, "document.querySelectorAll('.leaflet-tpa-socket-overlays-pane img').length>0")
                page.wait_for_timeout(800)
                self.assertEqual(page.locator('#road-chip strong').inner_text(), 'LIVE', label)
                self.assertEqual(page.locator('#road-source-status').inner_text(), 'Live · showing on map', label)
                self.assertNotIn('MOCK', page.locator('.mock-banner').inner_text())
                self.assertIn('LIVE: ROAD TRAFFIC', page.locator('.mock-banner').inner_text())
                self.assertEqual(page.locator('#sea-chip strong').inner_text(), 'SOON')
                self.assertEqual(page.locator('#bus-chip strong').inner_text(), 'EMPTY')
                self.assertIn(200, tiles)
                self.assertIn('TomTom', page.locator('.leaflet-control-attribution').inner_text())
                # Turning the layer off removes the pictures; on brings them back.
                page.evaluate("document.getElementById('road-toggle').click()")
                page.wait_for_timeout(300)
                self.assertEqual(page.locator('.leaflet-tpa-socket-overlays-pane img').count(), 0)
                self.assertEqual(page.locator('#road-chip strong').inner_text(), 'OFF')
                page.evaluate("document.getElementById('road-toggle').click()")
                wait_until(page, "document.querySelectorAll('.leaflet-tpa-socket-overlays-pane img').length>0")
                # Zoomed right out, traffic is not requested and the user is told why.
                for _ in range(6):  # one click at a time, like a person
                    page.evaluate("document.getElementById('zoom-out').click()")
                    page.wait_for_timeout(450)
                self.assertLess(page.evaluate('window.TPA_HQ_READONLY_STATE().zoom'), 8)
                self.assertEqual(page.locator('#road-source-status').inner_text(), 'Zoom in to see road traffic', label)
                if label == 'DESKTOP':
                    page.evaluate("document.getElementById('home-button').click()")
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.0.10-ROAD-LIVE-TEST-PRETEND-TOMTOM.png'))
                self.assertEqual(errors, [], label)
                page.close()
            browser.close()

    def test_4_engine_crash_affects_only_road(self):
        """Stop the road engine: HQ keeps running, road says so, other sockets unchanged."""
        self.engine.terminate(); self.engine.wait(5)
        data = json.loads(get(f'http://127.0.0.1:{self.hq_port}/api/v4/manifest')[2])
        by = {c['id']: c for c in data['components']}
        self.assertEqual(by['road'], {'id': 'road', 'status': 'unavailable', 'live': True, 'notice': 'Engine not running'})
        self.assertEqual(by['sea']['status'], 'unavailable')
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/api/v4/components/road/snapshot')[0], 503)
        x, y = tile_for(53.842, -0.423, 12)
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/api/v4/components/road/tiles/12/{x}/{y}.png')[0], 204)
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/')[0], 200)
        self.hq_log.seek(0)
        self.assertNotIn(TEST_KEY, self.hq_log.read().decode('utf-8', 'replace'))


if __name__ == '__main__':
    unittest.main()
