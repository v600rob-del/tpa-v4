"""HQ 4.1.0 smooth movement, in real Chromium against a PRETEND engine whose vehicles move.

Proves: vehicles with a motion hint glide between reports and are marked/described as
ESTIMATED; vehicles without one never move; a train heading for its next stop stops there;
a new real report is reached by a short slide, not a jump; markers are reused, not rebuilt;
each layer is re-asked at its engine's pace; small pans inside the area already fetched
cost no new request; the page's own files are re-checked on every visit (no-cache).
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

PACKAGE = Path(__file__).resolve().parents[3]
HQ = PACKAGE / 'v4/hq/serve_hq.py'
VENDOR = Path(os.environ.get('TPA_TEST_VENDOR_DIR', '/nonexistent'))
OPEN = build_opener(ProxyHandler({})).open


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


class FakeEngine:
    """A pretend aircraft engine. self.entities is a function of time; requests are counted."""
    def __init__(self, poll_ms=5000):
        self.requests, self.poll_ms, self.t0 = [], poll_ms, time.time()
        self.version = 1
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith('/status'):
                    body = {'component': 'air', 'health': 'ready', 'notice': ''}
                else:
                    outer.requests.append(time.time())
                    body = outer.frame()
                raw = json.dumps(body).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def frame(self):
        now = time.time()
        if not self.requests[:-1]:      # the clock for every pretend report starts at the first request
            self.t0 = now
        jet = {'id': 'air:jet', 'label': 'JET1 · B738', 'quality': 'observed', 'observedAt': iso(self.t0),
               'position': {'lon': -0.50, 'lat': 53.84, 'headingDeg': 90.0},
               'motion': {'mode': 'heading', 'speedMps': 250, 'maxSeconds': 60}}
        still = {'id': 'air:still', 'label': 'STILL · C172', 'quality': 'observed', 'observedAt': iso(self.t0),
                 'position': {'lon': -0.42, 'lat': 53.80, 'headingDeg': 0.0}}
        # "toward" (as trains use): from A, reach B 3 s after the report, then stay at B.
        train = {'id': 'air:toward', 'label': 'TOWARD', 'quality': 'observed', 'observedAt': iso(self.t0),
                 'position': {'lon': -0.40, 'lat': 53.87, 'headingDeg': None},
                 'motion': {'mode': 'toward', 'lon': -0.36, 'lat': 53.87, 'arriveAt': iso(self.t0 + 3), 'maxSeconds': 60}}
        # "path" (as trains use along the track): east first, then north. Must not cut the corner.
        path = {'id': 'air:path', 'label': 'PATH', 'quality': 'observed', 'observedAt': iso(self.t0),
                'position': {'lon': -0.60, 'lat': 53.76, 'headingDeg': None},
                'motion': {'mode': 'path', 'points': [[-0.60, 53.76], [-0.56, 53.76], [-0.56, 53.80]],
                           'arriveAt': iso(self.t0 + 4), 'maxSeconds': 60}}
        # slowToStop (as buses use): eases to a standstill at maxSeconds (here 3 s).
        slow = {'id': 'air:slow', 'label': 'SLOW', 'quality': 'observed', 'observedAt': iso(self.t0),
                'position': {'lon': -0.62, 'lat': 53.86, 'headingDeg': 90.0},
                'motion': {'mode': 'heading', 'speedMps': 300, 'maxSeconds': 3, 'slowToStop': True}}
        bad = {'id': 'air:bad', 'label': 'BAD MOTION', 'quality': 'observed', 'observedAt': iso(self.t0),
               'position': {'lon': -0.45, 'lat': 53.82, 'headingDeg': 45.0},
               'motion': {'mode': 'heading', 'speedMps': 99999, 'maxSeconds': 60}}
        if self.version == 2:   # a new real report for "still": it has moved east
            still = dict(still, observedAt=iso(now), position={'lon': -0.38, 'lat': 53.80, 'headingDeg': 0.0})
        return {'component': 'air', 'contractVersion': '1.0', 'health': 'ready', 'observedAt': iso(now),
                'receivedAt': iso(now), 'expiresAt': iso(now + 60), 'nextPollAfterMs': self.poll_ms,
                'attribution': 'PRETEND aircraft for tests', 'entities': [jet, still, train, path, slow, bad]}

    def close(self):
        self.server.shutdown()


@unittest.skipUnless(sync_playwright and (VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class Motion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():
                shutil.copyfile(VENDOR / src, target); cls.seeded.append(target)
        cls.engine = FakeEngine(poll_ms=5000)
        cls.port = free_port()
        base = {k: v for k, v in os.environ.items() if not k.startswith('TPA_HQ_')}
        cls.hq = subprocess.Popen([sys.executable, str(HQ)], env=dict(base, TPA_HQ_PORT=str(cls.port), TPA_HQ_PARKED='sea',
                                  TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_ENGINES=f'air=http://127.0.0.1:{cls.engine.port}'),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                OPEN(f'http://127.0.0.1:{cls.port}/api/v4/manifest', timeout=.3); break
            except Exception:
                time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.hq.terminate(); cls.hq.wait(5); cls.engine.close()
        for f in cls.seeded:
            f.unlink(missing_ok=True)

    def pos(self, page, id_):
        return page.evaluate("""id => { for (const b of document.querySelectorAll('.review-pin.air')) {
            if (b.title.startsWith(id)) { const m = b.style.transform.match(/translate\(([-0-9.]+)px, ?([-0-9.]+)px\)/);
                return [parseFloat(m[1]), parseFloat(m[2]), b.className, b.dataset.mark || '']; }
        } return null; }""", id_)

    def rendered(self, page, id_):
        """Where the marker is actually drawn right now (includes the smooth animation)."""
        return page.evaluate("""id => { for (const b of document.querySelectorAll('.review-pin.air')) {
            if (b.title.startsWith(id)) { const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, b.className]; }
        } return null; }""", id_)

    def wait(self, page, js, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if page.evaluate(js):
                return
            time.sleep(0.2)
        self.fail('timed out waiting for ' + js)

    def test_1_vehicles_glide_and_say_estimated(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length===6")
            p1 = self.rendered(page, 'PATH')
            # Part-way along its first (eastward) stretch it has moved east, not yet north.
            xs = []
            while time.time() < self.engine.t0 + 1.4:
                xs.append(self.rendered(page, 'PATH')); time.sleep(0.05)
            east = [r for r in xs if r[0] > p1[0] + 1]
            self.assertTrue(east, 'path: going east first')
            self.assertLess(abs(east[0][1] - p1[1]), 1.5, 'path: must not cut the corner')
            a1, s1, t1, b1 = self.pos(page, 'JET1'), self.pos(page, 'STILL'), self.pos(page, 'TOWARD'), self.pos(page, 'BAD')
            # Tag the jet's marker: if markers were rebuilt the tag would vanish.
            page.evaluate("[...document.querySelectorAll('.review-pin.air')].find(b=>b.title.startsWith('JET1')).dataset.mark='kept'")
            time.sleep(2.5)
            a2, s2, t2, b2 = self.pos(page, 'JET1'), self.pos(page, 'STILL'), self.pos(page, 'TOWARD'), self.pos(page, 'BAD')
            self.assertGreater(a2[0] - a1[0], 3, 'jet should glide east')            # heading 90 = east
            self.assertLess(abs(a2[1] - a1[1]), 1.5, 'jet should not drift north/south')
            self.assertIn('est-pos', a2[2])
            # 4.1.4: it LOOKS normal (solid outline, same as a reported position)...
            styles = page.evaluate("""() => [...document.querySelectorAll('.review-pin.air')].map(b => getComputedStyle(b).borderTopStyle)""")
            self.assertEqual(set(styles), {'solid'})
            # ...but the words still say so.
            self.assertIn('estimated position', page.locator('.review-pin.air[title^="JET1"]').get_attribute('title'))
            self.assertEqual(a2[3], 'kept', 'marker was rebuilt instead of moved')
            self.assertEqual((s1[0], s1[1]), (s2[0], s2[1]), 'a vehicle without a motion hint must not move')
            self.assertNotIn('est-pos', s2[2])
            self.assertEqual((b1[0], b1[1]), (b2[0], b2[1]), 'a nonsense motion hint must be ignored')
            # "toward": reached B 3 s after the report and then holds there.
            time.sleep(1.5)
            t3 = self.pos(page, 'TOWARD'); time.sleep(0.6); t4 = self.pos(page, 'TOWARD')
            # slowToStop: stopped by now, and short of where full speed would have taken it (half the distance).
            sl1 = self.pos(page, 'SLOW'); time.sleep(0.6); sl2 = self.pos(page, 'SLOW')
            self.assertEqual((sl1[0], sl1[1]), (sl2[0], sl2[1]), 'slowToStop: should have come to a stop')
            # "path": after ~4 s it is at the end; on the way it went east before north (screen y falls later).
            p_end = self.rendered(page, 'PATH')
            self.assertGreater(p_end[0], p1[0] + 3, 'path: should have gone east')
            self.assertLess(p_end[1], p1[1] - 3, 'path: then north (up the screen)')
            self.assertGreater(t3[0], t1[0] + 3)
            self.assertEqual((t3[0], t3[1]), (t4[0], t4[1]), 'should stop at its next point')
            # Details say ESTIMATED with the real report time.
            page.locator('.review-pin.air[title^="JET1"]').click(force=True)
            details = page.locator('#map-selection').inner_text()
            self.assertIn('Estimated position · last reported', details)
            page.locator('.review-pin.air[title^="STILL"]').click(force=True)
            self.assertIn('Reported position at', page.locator('#map-selection').inner_text())
            page.locator('#map-selection button').click()
            # 4.1.2 fluid: in one second on screen the jet passes through many positions
            # (drawn every frame), not two or three jumps.
            frames = page.evaluate("""() => new Promise(done => {
                const b = [...document.querySelectorAll('.review-pin.air')].find(b => b.title.startsWith('JET1'));
                const xs = []; const t0 = performance.now();
                (function f() { xs.push(Math.round(b.getBoundingClientRect().left * 10) / 10);
                  if (performance.now() - t0 < 1000) requestAnimationFrame(f); else done(xs); })(); })""")
            self.assertGreaterEqual(len(set(frames)), 20, f'only {len(set(frames))} distinct positions in 1 s')
            # A new real report: the marker slides through in-between positions, then arrives.
            self.engine.version = 2
            n0 = len(self.engine.requests)
            start = self.rendered(page, 'STILL')
            seen_est, xs = False, []
            for _ in range(60):
                time.sleep(0.1)
                r = self.rendered(page, 'STILL')
                xs.append(r[0]); seen_est = seen_est or 'est-pos' in r[2]
                if len(xs) > 25 and xs[-1] == xs[-5] and xs[-1] > start[0] + 5:
                    break
            self.assertTrue(seen_est, 'a sliding marker counts as estimated')
            final = xs[-1]
            self.assertGreater(final, start[0] + 5, 'arrives at the new report')
            self.assertTrue(any(start[0] + 1 < x < final - 1 for x in xs), 'slides, does not jump')
            self.assertNotIn('est-pos', self.pos(page, 'STILL')[2], 'after the slide it is at the real report again')
            self.assertGreaterEqual(len(self.engine.requests), n0 + 1)
            self.assertEqual(errors, [])
            page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.1.0-SMOOTH-MOVEMENT-PRETEND.png'))
            browser.close()

    def test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1200, 'height': 800})
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length===6")
            n0 = len(self.engine.requests)
            time.sleep(11)
            n1 = len(self.engine.requests)
            self.assertGreaterEqual(n1 - n0, 2, 'every ~5 s as the engine asked')
            self.assertLessEqual(n1 - n0, 4, 'not more often than asked')
            # A small pan inside the area already fetched: no extra request beyond the pace.
            page.mouse.move(600, 400); page.mouse.down(); page.mouse.move(640, 420, steps=5); page.mouse.up()
            time.sleep(1.2)
            n2 = len(self.engine.requests)
            self.assertLessEqual(n2 - n1, 1)
            # A big zoom-out leaves the fetched area: asked again within about a second.
            before = len(self.engine.requests)
            page.locator('#zoom-out').click(); time.sleep(0.4); page.locator('#zoom-out').click()
            time.sleep(1.6)
            self.assertGreater(len(self.engine.requests), before)
            browser.close()

    def test_4_wheel_and_drag_work_over_icons(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1200, 'height': 800})
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length===6")
            x, y = self.rendered(page, 'STILL')[:2]
            # Scroll wheel with the pointer on an icon zooms the map.
            z0 = page.locator('#zoom-level').inner_text()
            page.mouse.move(x, y); page.mouse.wheel(0, -300); time.sleep(1.0)
            self.assertNotEqual(page.locator('#zoom-level').inner_text(), z0, 'wheel over an icon should zoom')
            # Click-and-drag starting on an icon moves the map (the icon moves with it) and opens nothing.
            x, y = self.rendered(page, 'STILL')[:2]
            # (hold still briefly before letting go: a quick release makes Leaflet carry the map on a little
            # further - "flick" - which the icon correctly follows, but which isn't what this checks)
            page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 120, y + 60, steps=12); time.sleep(0.3); page.mouse.up()
            time.sleep(0.5)
            x2, y2 = self.rendered(page, 'STILL')[:2]
            self.assertAlmostEqual(x2 - x, 120, delta=12)
            self.assertAlmostEqual(y2 - y, 60, delta=12)
            self.assertTrue(page.locator('#map-selection').is_hidden(), 'a drag must not open details')
            # A plain click still opens the details.
            page.mouse.click(x2, y2); time.sleep(0.3)
            self.assertIn('STILL', page.locator('#map-selection').inner_text())
            browser.close()

    def test_5_no_taking_turns_when_more_move_than_may_glide(self):
        # Phone-sized screen: at most 60 icons glide. With 120 moving, the SAME ones keep gliding.
        many = FakeEngine(poll_ms=30000)
        import random
        rnd = random.Random(5)
        base_frame = many.frame
        def frame():
            f = base_frame()
            f['entities'] = [{'id': f'air:m{i}', 'label': f'M{i}', 'quality': 'observed', 'observedAt': iso(many.t0),
                              'position': {'lon': -0.47 + rnd.random() * 0.1, 'lat': 53.82 + rnd.random() * 0.05, 'headingDeg': rnd.random() * 360},
                              'motion': {'mode': 'heading', 'speedMps': 20 + rnd.random() * 60, 'maxSeconds': 300}} for i in range(120)]
            return f
        many.frame = frame
        port = free_port()
        base = {k: v for k, v in os.environ.items() if not k.startswith('TPA_HQ_')}
        hq = subprocess.Popen([sys.executable, str(HQ)], env=dict(base, TPA_HQ_PORT=str(port), TPA_HQ_PARKED='sea',
                              TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_ENGINES=f'air=http://127.0.0.1:{many.port}'),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                try:
                    OPEN(f'http://127.0.0.1:{port}/api/v4/manifest', timeout=.3); break
                except Exception:
                    time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                page.goto(f'http://127.0.0.1:{port}/')
                page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
                page.locator('#tpa-welcome .tpa-primary').click()
                self.wait(page, "document.querySelectorAll('.review-pin.air').length>=70")
                time.sleep(2.5)
                snap = lambda: set(page.evaluate("[...document.querySelectorAll('.review-pin.air.glide')].map(b=>b.title)"))
                sets = []
                for _ in range(4):
                    sets.append(snap()); time.sleep(1.1)
                self.assertTrue(all(0 < len(x) <= 60 for x in sets), [len(x) for x in sets])
                kept = len(set.intersection(*sets)) / max(1, len(sets[0]))
                self.assertGreaterEqual(kept, 0.9, f'only {kept:.0%} kept gliding: icons are taking turns')
                browser.close()
        finally:
            hq.terminate(); hq.wait(5); many.close()

    def test_6_slow_device_glide_never_pauses(self):
        # If updates come every ~2.5 s (busy device), each glide must last ~2.5 s too: no move-pause-move.
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1200, 'height': 800})
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length===6")
            self.engine.t0 = time.time(); self.engine.version = 1       # fresh report: the jet glides for 60 s
            page.evaluate("window.TPA_HQ_TEST_MIN_GAP=2500")   # imitate a device that can only update every 2.5 s
            self.wait(page, "(()=>{const b=[...document.querySelectorAll('.review-pin.air')].find(b=>b.title.startsWith('JET1'));return b&&b.classList.contains('glide')})()")
            time.sleep(3)
            xs = page.evaluate("""() => new Promise(done => {
                const b = [...document.querySelectorAll('.review-pin.air')].find(b => b.title.startsWith('JET1'));
                const xs = []; const t0 = performance.now();
                (function f() { xs.push(b.getBoundingClientRect().left);
                  if (performance.now() - t0 < 3000) requestAnimationFrame(f); else done(xs); })(); })""")
            glide = page.evaluate("getComputedStyle(document.querySelector('.marker-layer')).getPropertyValue('--glide')")
            # the jet keeps moving: no run of 0.4 s (24 frames) without movement
            still, worst = 0, 0
            for i in range(1, len(xs)):
                still = still + 1 if abs(xs[i] - xs[i - 1]) < 0.01 else 0
                worst = max(worst, still)
            self.assertLess(worst, 24, f'paused for {worst} frames (glide {glide})')
            browser.close()

    def test_3_page_files_are_rechecked_every_visit(self):
        for path in ('/', '/assets/hq.js', '/assets/hq.css', '/data/site-config.json'):
            with OPEN(f'http://127.0.0.1:{self.port}{path}', timeout=3) as r:
                self.assertEqual(r.headers.get('Cache-Control'), 'no-cache', path)


if __name__ == '__main__':
    unittest.main()
