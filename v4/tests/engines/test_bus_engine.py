"""Bus engine 0.2 + HQ 4.0.11 tests.

A PRETEND BODS server on 127.0.0.1 serves made-up SIRI-VM data (fictional
buses near Beverley and Hull). The real bus engine, road engine and HQ run as
separate processes, as on the owner's PC. Proves engine behaviour, safety,
failure isolation and display. It does NOT prove BODS's real service or key.
"""
import html
import io
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
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_road_engine import FakeTomTom, OPEN, VENDOR, free_port, get, start, wait_until  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[3]
ENGINE = PACKAGE / 'v4/engines/bus/bus_engine.py'
ROAD = PACKAGE / 'v4/engines/road/road_engine.py'
HQ = PACKAGE / 'v4/hq/serve_hq.py'
KEY = 'PRETEND-BODS-KEY-5e1d'
BEVERLEY_VIEW = 'west=-0.60&south=53.75&east=-0.25&north=53.93'


def stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def activity(ref, line, dest, lat, lon, minutes_ago=0.5, operator='EYMS', bearing='90'):
    line, dest = html.escape(line), html.escape(dest)  # like real XML: special characters escaped
    return f"""<VehicleActivity><RecordedAtTime>{stamp(minutes_ago)}</RecordedAtTime><MonitoredVehicleJourney>
<LineRef>{line}</LineRef><PublishedLineName>{line}</PublishedLineName><OperatorRef>{operator}</OperatorRef>
<DestinationName>{dest}</DestinationName><VehicleLocation><Longitude>{lon}</Longitude><Latitude>{lat}</Latitude></VehicleLocation>
<Bearing>{bearing}</Bearing><VehicleRef>{ref}</VehicleRef></MonitoredVehicleJourney></VehicleActivity>"""


def siri(extra=()):
    body = [
        activity('V101', '121', 'Hull Interchange', 53.842, -0.430),
        activity('V102', 'X46', 'York', 53.850, -0.410),
        activity('V103', '246', 'Bridlington', 53.860, -0.450, minutes_ago=10),   # stale
        activity('V104', '57', 'Nowhere', 53.845, -0.420, minutes_ago=45),        # too old: dropped
        activity('V105', '9', 'Hull', 53.745, -0.334),                            # Hull, outside the Beverley view
        activity('V107', '1', 'York Station', 53.958, -1.093),                    # York, far from Beverley
        activity('V101', '121', 'Duplicate', 53.842, -0.430),                     # duplicate vehicle
        activity('V106', '<script>alert(1)</script>', 'Beverley <b>bold</b>', 53.838, -0.428),
        '<VehicleActivity><RecordedAtTime>not-a-time</RecordedAtTime></VehicleActivity>',
        *extra,
    ]
    return ('<?xml version="1.0" encoding="UTF-8"?><Siri xmlns="http://www.siri.org.uk/siri" version="2.0">'
            '<ServiceDelivery><VehicleMonitoringDelivery>' + ''.join(body) +
            '</VehicleMonitoringDelivery></ServiceDelivery></Siri>').encode('utf-8')


def siri_in_area(south, west, north, east):
    import re as _re
    text = siri().decode('utf-8')
    head, rest = text.split('<VehicleActivity>', 1)
    parts = ('<VehicleActivity>' + rest).split('</VehicleActivity>')
    tail = parts.pop()
    kept = []
    for part in parts:
        lat = _re.search(r'<Latitude>([-0-9.]+)</Latitude>', part)
        lon = _re.search(r'<Longitude>([-0-9.]+)</Longitude>', part)
        if lat and lon and south <= float(lat.group(1)) <= north and west <= float(lon.group(1)) <= east:
            kept.append(part + '</VehicleActivity>')
    return (head + ''.join(kept) + tail).encode('utf-8')


class FakeBODS:
    def __init__(self, status=200, payload=None):
        self.requests, self.status, self.payload = [], status, payload
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                split = urlsplit(self.path)
                outer.requests.append({'path': split.path, 'query': parse_qs(split.query)})
                if outer.status != 200:
                    self.send_response(outer.status); self.send_header('Content-Length', '0'); self.end_headers(); return
                xml = outer.payload or siri()
                box = parse_qs(split.query).get('boundingBox')
                if box and not outer.payload:  # like real BODS: only buses inside the requested area
                    w, so, e, n = (float(x) for x in box[0].split(','))
                    xml = siri_in_area(so, w, n, e)
                if split.path.startswith('/bulk'):
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr('siri.xml', xml)
                    body, ctype = buf.getvalue(), 'application/zip'
                else:
                    body, ctype = xml, 'application/xml'
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class BusEngine(unittest.TestCase):
    def engine(self, fake=None, key=KEY, mode='area', poll=10, demand=None):
        port = free_port()
        env = dict(os.environ, TPA_BUS_ENGINE_PORT=str(port), TPA_BUS_MODE=mode, TPA_BUS_POLL_SECONDS=str(poll))
        env.pop('TPA_BODS_API_KEY', None)
        if key:
            env['TPA_BODS_API_KEY'] = key
        if fake:
            env['TPA_BUS_TEST_AREA_URL'] = f'http://127.0.0.1:{fake.port}/datafeed/'
            env['TPA_BUS_TEST_BULK_URL'] = f'http://127.0.0.1:{fake.port}/bulk_archive'
        if demand:
            env['TPA_BUS_DEMAND_SECONDS'] = str(demand)
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        proc = start(ENGINE, env, f'http://127.0.0.1:{port}/status', log)
        self.addCleanup(proc.wait, 5)
        self.addCleanup(proc.terminate)
        return f'http://127.0.0.1:{port}', log

    def fake(self, **kw):
        f = FakeBODS(**kw)
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

    def test_no_key_means_zero_requests(self):
        fake = self.fake()
        base, _ = self.engine(fake, key='')
        frame = self.frame(base)
        self.assertEqual((frame['health'], frame['notice'], frame['entities']), ('unavailable', 'Bus key not set up on this PC', []))
        time.sleep(1.5)
        self.assertEqual(fake.requests, [])

    def test_area_mode_buses_in_view(self):
        fake = self.fake()
        base, log = self.engine(fake)
        frame = self.until(lambda: (f := self.frame(base))['entities'] and f)
        self.assertEqual((frame['component'], frame['contractVersion'], frame['health']), ('bus', '1.0', 'ready'))
        self.assertIn('Bus Open Data Service (BODS)', frame['attribution'])
        by_id = {e['id']: e for e in frame['entities']}
        self.assertEqual(set(by_id), {'bus:EYMS:V101', 'bus:EYMS:V102', 'bus:EYMS:V103', 'bus:EYMS:V106'})
        self.assertEqual(by_id['bus:EYMS:V101']['label'], 'Bus 121 to Hull Interchange')
        self.assertEqual(by_id['bus:EYMS:V101']['badge'], '121')
        self.assertEqual(by_id['bus:EYMS:V102']['badge'], 'X46')
        self.assertEqual(by_id['bus:EYMS:V101']['quality'], 'observed')
        self.assertEqual(by_id['bus:EYMS:V103']['quality'], 'stale')       # 10 minutes old
        self.assertEqual(by_id['bus:EYMS:V101']['position']['headingDeg'], 90.0)
        self.assertEqual(by_id['bus:EYMS:V106']['badge'], 'scriptal')      # only letters and numbers survive
        # Asked BODS once, for the viewed area plus a margin (west,south,east,north), key as BODS expects.
        self.assertEqual(len(fake.requests), 1)
        q = fake.requests[0]['query']
        self.assertEqual(q['api_key'], [KEY])
        w, s, e, n = (float(x) for x in q['boundingBox'][0].split(','))
        self.assertTrue(w < -0.60 and s < 53.75 and e > -0.25 and n > 53.93, q)
        self.assertLess(n - s, 3.1)
        log.seek(0)
        self.assertNotIn(KEY, log.read().decode('utf-8', 'replace'))
        status = json.loads(get(base + '/status')[2])
        self.assertEqual((status['health'], status['mode']), ('ready', 'area'))
        self.assertNotIn(KEY, json.dumps(status))

    def test_zoomed_out_view_asks_to_zoom_in(self):
        fake = self.fake()
        base, _ = self.engine(fake)
        frame = self.frame(base, 'west=-5&south=50&east=2&north=56')
        self.assertEqual((frame['notice'], frame['entities']), ('Zoom in to see buses', []))

    def test_many_requests_one_download_per_poll(self):
        fake = self.fake()
        base, _ = self.engine(fake, poll=30)
        self.until(lambda: self.frame(base)['entities'])
        for _ in range(15):
            self.frame(base)
            time.sleep(0.1)
        self.assertEqual(len(fake.requests), 1)

    def test_new_area_loads_quickly_without_waiting_for_poll(self):
        fake = self.fake()
        base, _ = self.engine(fake, poll=60)
        self.until(lambda: self.frame(base)['entities'])
        york = 'west=-1.20&south=53.90&east=-1.00&north=54.00'   # outside the first download's margin
        self.assertFalse(any(e['id'] == 'bus:EYMS:V107' for e in self.frame(base, york)['entities']))
        self.until(lambda: any(e['id'] == 'bus:EYMS:V107' for e in self.frame(base, york)['entities']), timeout=12)
        self.assertEqual(len(fake.requests), 2)  # well before the 60-second poll

    def test_stops_downloading_when_nobody_is_looking(self):
        fake = self.fake()
        base, _ = self.engine(fake, poll=10, demand=5)
        self.until(lambda: self.frame(base)['entities'])
        time.sleep(17)  # well past the poll interval, but nobody viewing
        self.assertEqual(len(fake.requests), 1)

    def test_bulk_mode_whole_country_zip(self):
        fake = self.fake()
        base, _ = self.engine(fake, mode='bulk')
        frame = self.until(lambda: (f := self.frame(base))['entities'] and f)
        self.assertEqual(len(frame['entities']), 4)
        self.assertEqual(fake.requests[0]['path'], '/bulk_archive')
        self.assertNotIn('boundingBox', fake.requests[0]['query'])
        hull = 'west=-0.45&south=53.70&east=-0.20&north=53.80'
        self.assertTrue(any(e['id'] == 'bus:EYMS:V105' for e in self.frame(base, hull)['entities']))
        self.assertEqual(len(fake.requests), 1)  # whole country already covered

    def test_rejected_key(self):
        fake = self.fake(status=403)
        base, log = self.engine(fake)
        self.frame(base)
        status = self.until(lambda: (s := json.loads(get(base + '/status')[2]))['lastError'] and s)
        self.assertEqual((status['health'], status['notice']), ('unavailable', 'BODS did not accept the key'))
        log.seek(0)
        out = log.read().decode('utf-8', 'replace')
        self.assertIn('bods-http-403', out)
        self.assertNotIn(KEY, out)

    def test_hostile_xml_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><Siri>&b;</Siri>'
        fake = self.fake(payload=bomb)
        base, _ = self.engine(fake)
        self.frame(base)
        status = self.until(lambda: (s := json.loads(get(base + '/status')[2]))['lastError'] and s)
        self.assertIn('xml-declarations-not-allowed', status['lastError'])
        self.assertEqual(self.frame(base)['entities'], [])

    def test_busy_view_limited_to_400_nearest(self):
        extra = [activity(f'M{i}', '10', 'Town', 53.76 + (i % 30) * 0.005, -0.55 + (i // 30) * 0.02) for i in range(450)]
        fake = self.fake(payload=siri(extra))
        base, _ = self.engine(fake)
        frame = self.until(lambda: (f := self.frame(base))['entities'] and f)
        self.assertEqual(len(frame['entities']), 400)
        self.assertEqual(frame['notice'], 'Showing nearest 400 · zoom in for all')


try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and (VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class HQWithBusAndRoad(unittest.TestCase):
    """Real HQ + real bus engine + real road engine, pretend suppliers, real Chromium."""
    @classmethod
    def setUpClass(cls):
        import hashlib
        cls.bods, cls.tomtom = FakeBODS(), FakeTomTom()
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():
                shutil.copyfile(VENDOR / src, target); cls.seeded.append(target)
        cls.data = Path(tempfile.mkdtemp(prefix='tpa-bus-hq-'))
        cls.bus_port, cls.road_port, cls.hq_port = free_port(), free_port(), free_port()
        base = {k: v for k, v in os.environ.items() if k not in ('TPA_BODS_API_KEY', 'TOMTOM_API_KEY', 'TPA_HQ_FIXTURE_DIR')}
        cls.logs = [tempfile.TemporaryFile() for _ in range(3)]
        cls.bus = start(ENGINE, dict(base, TPA_BUS_ENGINE_PORT=str(cls.bus_port), TPA_BODS_API_KEY=KEY, TPA_BUS_POLL_SECONDS='10',
                                     TPA_BUS_TEST_AREA_URL=f'http://127.0.0.1:{cls.bods.port}/datafeed/'),
                        f'http://127.0.0.1:{cls.bus_port}/status', cls.logs[0])
        cls.road = start(ROAD, dict(base, TPA_ROAD_ENGINE_PORT=str(cls.road_port), TOMTOM_API_KEY='PRETEND-TT', TPA_ROAD_DATA_DIR=str(cls.data),
                                    TPA_ROAD_TOMTOM_URL=cls.tomtom.template), f'http://127.0.0.1:{cls.road_port}/status', cls.logs[1])
        cls.hq = start(HQ, dict(base, TPA_HQ_PORT=str(cls.hq_port), TPA_HQ_PARKED='sea', TPA_HQ_DISABLE_MAP_NETWORK='1',
                                TPA_HQ_ENGINES=f'road=http://127.0.0.1:{cls.road_port},bus=http://127.0.0.1:{cls.bus_port}'),
                       f'http://127.0.0.1:{cls.hq_port}/api/v4/manifest', cls.logs[2])

    @classmethod
    def tearDownClass(cls):
        for p in (cls.hq, cls.bus, cls.road):
            if p.poll() is None:
                p.terminate(); p.wait(5)
        cls.bods.close(); cls.tomtom.close()
        shutil.rmtree(cls.data, True)
        for f in cls.seeded:
            f.unlink(missing_ok=True)

    def test_1_browser_shows_live_buses_and_traffic(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            for label, w, h in (('DESKTOP', 1440, 900), ('MOBILE', 390, 844)):
                page = browser.new_page(viewport={'width': w, 'height': h})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{self.hq_port}/')
                page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
                page.locator('#tpa-welcome .tpa-primary').click()
                # Buses connect in the background; HQ re-checks every few seconds until they appear.
                wait_until(page, "document.querySelectorAll('.review-pin.bus').length>=3", timeout=40)
                self.assertEqual(page.locator('#bus-chip strong').inner_text(), 'LIVE')
                self.assertEqual(page.locator('#road-chip strong').inner_text(), 'LIVE')
                self.assertRegex(page.locator('#bus-source-status').inner_text(), r'^\d+ in view$')
                self.assertIn('LIVE: BUSES, ROAD TRAFFIC', page.locator('.mock-banner').inner_text())
                badges = page.locator('.review-pin.bus .review-route').all_inner_texts()
                self.assertIn('121', badges)
                self.assertEqual(page.locator('.review-pin.bus.stale').count(), 1)  # 10-minute-old report shown faded
                titles = [page.locator('.review-pin.bus').nth(i).get_attribute('title') for i in range(page.locator('.review-pin.bus').count())]
                self.assertTrue(all(not t.startswith('MOCK') for t in titles), titles)
                self.assertTrue(any('Beverley <b>bold</b>' in t for t in titles))  # shown as plain text, never as HTML
                self.assertEqual(page.locator('.map-selection b').count(), 0)
                pin = page.locator('.review-pin.bus', has=page.locator('.review-route', has_text='121')).first
                pin.click(force=True)
                details = page.locator('#map-selection').inner_text()
                self.assertIn('Bus 121 to Hull Interchange', details)
                self.assertIn('Reported position at', details)
                self.assertIn('Bus Open Data Service', details)
                self.assertNotIn('FICTIONAL', details)
                if label == 'DESKTOP':
                    page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.0.11-BUSES-LIVE-TEST-PRETEND-BODS.png'))
                self.assertEqual(errors, [], label)
                page.close()
            browser.close()

    def test_2_bus_engine_crash_affects_only_buses(self):
        self.bus.terminate(); self.bus.wait(5)
        data = json.loads(get(f'http://127.0.0.1:{self.hq_port}/api/v4/manifest')[2])
        by = {c['id']: c for c in data['components']}
        self.assertEqual(by['bus'], {'id': 'bus', 'status': 'unavailable', 'live': True, 'notice': 'Engine not running'})
        self.assertEqual(by['road']['status'], 'ready')
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/api/v4/components/bus/snapshot?{BEVERLEY_VIEW}')[0], 503)
        for log in self.logs:
            log.seek(0)
            self.assertNotIn(KEY, log.read().decode('utf-8', 'replace'))


if __name__ == '__main__':
    unittest.main()
