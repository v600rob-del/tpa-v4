"""Aircraft engine 0.1 tests.

A PRETEND ADSB.lol server on 127.0.0.1 serves made-up aircraft (fictional
flights over East Yorkshire). The real aircraft engine and real HQ run as
separate processes. Proves engine behaviour, safety and display; it does NOT
prove ADSB.lol's real service.
"""
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_road_engine import VENDOR, free_port, get, start, wait_until  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[3]
ENGINE = PACKAGE / 'v4/engines/air/air_engine.py'
HQ = PACKAGE / 'v4/hq/serve_hq.py'
BEVERLEY_VIEW = 'west=-0.80&south=53.65&east=-0.05&north=54.05'

AIRCRAFT = [
    {'hex': '4ca1b2', 'flight': 'RYR12AB ', 'r': 'EI-ABC', 't': 'B738', 'alt_baro': 36000, 'track': 135.5, 'lat': 53.85, 'lon': -0.42, 'seen_pos': 1.2},
    {'hex': '406f01', 'flight': '', 'r': 'G-HULL', 't': 'C172', 'alt_baro': 2500, 'track': 270, 'lat': 53.80, 'lon': -0.30, 'seen_pos': 5},
    {'hex': '400abc', 'flight': 'EZY9XY', 'r': 'G-EZAA', 't': 'A320', 'alt_baro': 'ground', 'gs': 0, 'lat': 53.83, 'lon': -0.50, 'seen_pos': 2},
    {'hex': '43c111', 'flight': 'RRR001', 'r': 'ZZ336', 't': 'A400', 'alt_baro': 18000, 'track': 10, 'lat': 53.90, 'lon': -0.60, 'seen_pos': 45},  # stale
    {'hex': '3c6abc', 'flight': 'DLH4', 'r': 'D-AIXX', 't': 'A359', 'alt_baro': 38000, 'track': 300, 'lat': 53.88, 'lon': -0.35, 'seen_pos': 120},  # dropped
    {'hex': 'nothex', 'flight': 'BAD', 'lat': 53.86, 'lon': -0.40},                                     # bad id: ignored
    {'hex': '4ca1b2', 'flight': 'DUPLICATE', 'lat': 53.86, 'lon': -0.41},                                 # duplicate
    {'hex': '4ca999', 'flight': '<img src=x>', 't': 'B744', 'alt_baro': 30000, 'track': 90, 'lat': 53.87, 'lon': -0.45, 'seen_pos': 3},
    {'hex': '4ca777', 'flight': 'YORK1', 'alt_baro': 9000, 'track': 45, 'lat': 53.96, 'lon': -1.08, 'seen_pos': 2},   # York, near view edge
    {'hex': '4ca555', 'flight': 'SCOT1', 'alt_baro': 9000, 'track': 0, 'lat': 55.95, 'lon': -3.19, 'seen_pos': 2},    # Edinburgh, far away
]


def nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3440.065 * 2 * math.asin(min(1, math.sqrt(a)))


class FakeADSB:
    """Like ADSB.lol /v2/point: only aircraft within the requested circle."""
    def __init__(self, status=200):
        self.requests, self.status = [], status
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                m = re.match(r'^/v2/point/([-0-9.]+)/([-0-9.]+)/(\d+)$', self.path)
                outer.requests.append({'path': self.path, 'ua': self.headers.get('User-Agent', '')})
                if outer.status != 200 or not m:
                    self.send_response(outer.status if outer.status != 200 else 404)
                    self.send_header('Content-Length', '0'); self.end_headers(); return
                lat, lon, r = float(m.group(1)), float(m.group(2)), int(m.group(3))
                ac = [a for a in AIRCRAFT if nm(lat, lon, a['lat'], a['lon']) <= r]
                body = json.dumps({'ac': ac, 'msg': 'No error', 'now': int(time.time() * 1000), 'total': len(ac)}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
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
        return f'http://127.0.0.1:{self.port}/v2/point/{{lat}}/{{lon}}/{{radius}}'

    def close(self):
        self.server.shutdown()


class AirEngine(unittest.TestCase):
    def engine(self, fake, poll=10, demand=None):
        port = free_port()
        env = dict(os.environ, TPA_AIR_ENGINE_PORT=str(port), TPA_AIR_POLL_SECONDS=str(poll), TPA_AIR_TEST_URL=fake.template)
        if demand:
            env['TPA_AIR_DEMAND_SECONDS'] = str(demand)
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        proc = start(ENGINE, env, f'http://127.0.0.1:{port}/status', log)
        self.addCleanup(proc.wait, 5)
        self.addCleanup(proc.terminate)
        return f'http://127.0.0.1:{port}'

    def fake(self, **kw):
        f = FakeADSB(**kw)
        self.addCleanup(f.close)
        return f

    def frame(self, base, query=BEVERLEY_VIEW):
        return json.loads(get(f'{base}/snapshot?{query}')[2])

    def until(self, check, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            value = check()
            if value:
                return value
            time.sleep(0.2)
        self.fail('condition not met in time')

    def test_nothing_fetched_until_someone_views(self):
        fake = self.fake()
        self.engine(fake)
        time.sleep(1.5)
        self.assertEqual(fake.requests, [])

    def test_aircraft_in_view_labels_and_rules(self):
        fake = self.fake()
        base = self.engine(fake)
        frame = self.until(lambda: (f := self.frame(base))['entities'] and f)
        self.assertEqual((frame['component'], frame['contractVersion'], frame['health']), ('air', '1.0', 'ready'))
        self.assertIn('ADSB.lol', frame['attribution'])
        by_id = {e['id']: e for e in frame['entities']}
        self.assertEqual(set(by_id), {'air:4ca1b2', 'air:406f01', 'air:400abc', 'air:43c111', 'air:4ca999'})
        self.assertEqual(by_id['air:4ca1b2']['label'], 'RYR12AB · B738 · 36,000 ft · EI-ABC')
        self.assertEqual(by_id['air:406f01']['label'], 'G-HULL · C172 · 2,500 ft')
        self.assertEqual(by_id['air:400abc']['label'], 'EZY9XY · A320 · on the ground · G-EZAA')
        self.assertEqual(by_id['air:4ca1b2']['position']['headingDeg'], 135.5)
        self.assertIsNone(by_id['air:400abc']['position']['headingDeg'])
        self.assertEqual(by_id['air:4ca1b2']['quality'], 'observed')
        self.assertEqual(by_id['air:43c111']['quality'], 'stale')            # 45 s since last position
        self.assertTrue(by_id['air:4ca999']['label'].startswith('<img src=x'))  # plain text, trimmed; HQ never renders it as HTML
        # One circle covering the view, within ADSB.lol's 250 nm limit.
        self.assertEqual(len(fake.requests), 1)
        lat, lon, r = re.match(r'^/v2/point/([-0-9.]+)/([-0-9.]+)/(\d+)$', fake.requests[0]['path']).groups()
        self.assertAlmostEqual(float(lat), 53.85, places=2)
        self.assertTrue(25 <= int(r) <= 250)
        self.assertIn('TPA-Air-Engine', fake.requests[0]['ua'])

    def test_zoomed_out_asks_to_zoom_in(self):
        fake = self.fake()
        base = self.engine(fake)
        frame = self.frame(base, 'west=-10&south=49&east=2&north=60')
        self.assertEqual((frame['notice'], frame['entities']), ('Zoom in to see aircraft', []))
        time.sleep(1)
        self.assertEqual(fake.requests, [])

    def test_one_request_per_poll_however_many_viewers(self):
        fake = self.fake()
        base = self.engine(fake, poll=30)
        self.until(lambda: self.frame(base)['entities'])
        for _ in range(20):
            self.frame(base)
            time.sleep(0.05)
        self.assertEqual(len(fake.requests), 1)

    def test_new_area_loads_quickly(self):
        fake = self.fake()
        base = self.engine(fake, poll=60)
        self.until(lambda: self.frame(base)['entities'])
        edinburgh = 'west=-3.4&south=55.85&east=-3.0&north=56.05'
        self.until(lambda: any(e['id'] == 'air:4ca555' for e in self.frame(base, edinburgh)['entities']), timeout=12)
        self.assertEqual(len(fake.requests), 2)

    def test_slow_down_request_pauses_five_minutes(self):
        fake = self.fake(status=429)
        base = self.engine(fake, poll=10)
        self.frame(base)
        status = self.until(lambda: (s := json.loads(get(base + '/status')[2]))['lastError'] and s)
        self.assertEqual(status['notice'], 'ADSB.lol asked us to slow down · paused 5 min')
        for _ in range(12):  # keep viewing for 12+ seconds: no further requests during the pause
            self.frame(base)
            time.sleep(1.1)
        self.assertEqual(len(fake.requests), 1)

    def test_stops_when_nobody_is_looking(self):
        fake = self.fake()
        base = self.engine(fake, poll=10, demand=5)
        self.until(lambda: self.frame(base)['entities'])
        time.sleep(17)
        self.assertEqual(len(fake.requests), 1)


try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and (VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class HQWithAircraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        cls.fake = FakeADSB()
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():
                shutil.copyfile(VENDOR / src, target); cls.seeded.append(target)
        cls.air_port, cls.hq_port = free_port(), free_port()
        base = {k: v for k, v in os.environ.items() if k != 'TPA_HQ_FIXTURE_DIR'}
        cls.logs = [tempfile.TemporaryFile(), tempfile.TemporaryFile()]
        cls.air = start(ENGINE, dict(base, TPA_AIR_ENGINE_PORT=str(cls.air_port), TPA_AIR_POLL_SECONDS='10', TPA_AIR_TEST_URL=cls.fake.template),
                        f'http://127.0.0.1:{cls.air_port}/status', cls.logs[0])
        cls.hq = start(HQ, dict(base, TPA_HQ_PORT=str(cls.hq_port), TPA_HQ_PARKED='sea', TPA_HQ_DISABLE_MAP_NETWORK='1',
                                TPA_HQ_ENGINES=f'air=http://127.0.0.1:{cls.air_port}'),
                       f'http://127.0.0.1:{cls.hq_port}/api/v4/manifest', cls.logs[1])

    @classmethod
    def tearDownClass(cls):
        for p in (cls.hq, cls.air):
            if p.poll() is None:
                p.terminate(); p.wait(5)
        cls.fake.close()
        for f in cls.seeded:
            f.unlink(missing_ok=True)

    def test_1_browser_shows_live_aircraft_pointing_the_right_way(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            for label, w, h in (('DESKTOP', 1440, 900), ('MOBILE', 390, 844)):
                page = browser.new_page(viewport={'width': w, 'height': h})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{self.hq_port}/')
                page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
                page.locator('#tpa-welcome .tpa-primary').click()
                wait_until(page, "document.querySelectorAll('.review-pin.air').length>=3", timeout=40)
                self.assertEqual(page.locator('#air-chip strong').inner_text(), 'LIVE')
                self.assertIn('LIVE: AIRCRAFT', page.locator('.mock-banner').inner_text())
                rotations = page.evaluate("[...document.querySelectorAll('.review-pin.air svg')].map(s=>s.style.transform)")
                self.assertIn('rotate(135.5deg)', rotations)
                if label == 'DESKTOP':  # on a phone screen the faded aircraft is just off the edge
                    self.assertEqual(page.locator('.review-pin.air.stale').count(), 1)
                self.assertEqual(page.locator('.marker-layer img').count(), 0)  # hostile flight name never became an image
                titles = page.evaluate("[...document.querySelectorAll('.review-pin.air')].map(b=>b.title)")
                self.assertTrue(any(t.startswith('RYR12AB · B738 · 36,000 ft') for t in titles), titles)
                self.assertTrue(all(not t.startswith('MOCK') for t in titles))
                page.locator('.review-pin.air[title^="RYR12AB"]').first.click(force=True)
                details = page.locator('#map-selection').inner_text()
                self.assertIn('RYR12AB · B738 · 36,000 ft', details)
                self.assertIn('Reported position at', details)
                self.assertIn('ADSB.lol', details)
                if label == 'DESKTOP':
                    page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.0.11-AIRCRAFT-LIVE-TEST-PRETEND-ADSB.png'))
                self.assertEqual(errors, [], label)
                page.close()
            browser.close()

    def test_2_air_engine_crash_affects_only_aircraft(self):
        self.air.terminate(); self.air.wait(5)
        data = json.loads(get(f'http://127.0.0.1:{self.hq_port}/api/v4/manifest')[2])
        by = {c['id']: c for c in data['components']}
        self.assertEqual(by['air'], {'id': 'air', 'status': 'unavailable', 'live': True, 'notice': 'Engine not running'})
        self.assertEqual(by['sea']['status'], 'unavailable')
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/')[0], 200)


if __name__ == '__main__':
    unittest.main()
