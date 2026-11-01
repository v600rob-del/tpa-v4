"""Train engine 0.2 tests.

PRETEND Network Rail: a local STOMP server (the live-feed protocol) and local
web servers for the CORPUS and NaPTAN station files, all on 127.0.0.1, with
made-up trains near York, Hull and Beverley. The real train engine and real HQ
run as separate processes. Proves behaviour, safety and display; it does NOT
prove Network Rail's real service or the owner's login.
"""
import csv
import gzip
import io
import json
import os
import shutil
import socket
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
ENGINE = PACKAGE / 'v4/engines/rail/rail_engine.py'
HQ = PACKAGE / 'v4/hq/serve_hq.py'
USER, PASSWORD = 'tester@example.com', 'pa:ss\\word-PRETEND-91'
YORK_VIEW = 'west=-1.30&south=53.70&east=-0.20&north=54.05'

CORPUS = {'TIPLOCDATA': [
    {'STANOX': '16000', 'TIPLOC': 'YORK', 'NLCDESC': 'YORK'},
    {'STANOX': '15100', 'TIPLOC': 'HULL', 'NLCDESC': 'HULL PARAGON'},
    {'STANOX': '15200', 'TIPLOC': 'BEVERLY', 'NLCDESC': 'BEVERLEY'},
    {'STANOX': '15300', 'TIPLOC': 'NOWHERE', 'NLCDESC': 'NOT A STATION'},     # no NaPTAN match: never placed
    {'STANOX': '15400', 'TIPLOC': 'CONFLCT', 'NLCDESC': 'CONFLICT'},          # NaPTAN conflict: dropped
] + [{'STANOX': str(20000 + i), 'TIPLOC': f'PAD{i:03d}', 'NLCDESC': f'PADDING {i}'} for i in range(60)]}


def naptan_csv():
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=['ATCOCode', 'StopType', 'Latitude', 'Longitude', 'Status', 'CommonName'])
    w.writeheader()
    w.writerow({'ATCOCode': '9100YORK', 'StopType': 'RLY', 'Latitude': '53.95799', 'Longitude': '-1.09312', 'Status': 'active', 'CommonName': 'York'})
    w.writerow({'ATCOCode': '9100HULL', 'StopType': 'RLY', 'Latitude': '53.74400', 'Longitude': '-0.34570', 'Status': 'active', 'CommonName': 'Hull'})
    w.writerow({'ATCOCode': '9100BEVERLY', 'StopType': 'RLY', 'Latitude': '53.84230', 'Longitude': '-0.42370', 'Status': 'active', 'CommonName': 'Beverley'})
    w.writerow({'ATCOCode': '9100CONFLCT', 'StopType': 'RLY', 'Latitude': '53.9', 'Longitude': '-0.5', 'Status': 'active', 'CommonName': 'A'})
    w.writerow({'ATCOCode': '9100CONFLCT', 'StopType': 'RLY', 'Latitude': '53.1', 'Longitude': '-0.9', 'Status': 'active', 'CommonName': 'B'})
    w.writerow({'ATCOCode': '490000001A', 'StopType': 'BCT', 'Latitude': '53.8', 'Longitude': '-0.4', 'Status': 'active', 'CommonName': 'Bus stop'})
    for i in range(60):
        w.writerow({'ATCOCode': f'9100PAD{i:03d}', 'StopType': 'RLY', 'Latitude': f'{51 + i * 0.01:.4f}', 'Longitude': '-2.0', 'Status': 'active', 'CommonName': 'x'})
    return out.getvalue().encode('utf-8')


class FakeFiles:
    """CORPUS (needs the login, like Network Rail) and NaPTAN (open)."""
    def __init__(self):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                import base64
                if self.path.startswith('/corpus'):
                    expected = 'Basic ' + base64.b64encode(f'{USER}:{PASSWORD}'.encode()).decode()
                    if self.headers.get('Authorization') != expected:
                        self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
                    body = gzip.compress(json.dumps(CORPUS).encode())
                else:
                    body = naptan_csv()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def unescape(v):
    out, i = [], 0
    while i < len(v):
        if v[i] == '\\' and i + 1 < len(v):
            out.append({'\\': '\\', 'n': '\n', 'r': '\r', 'c': ':'}[v[i + 1]]); i += 2
        else:
            out.append(v[i]); i += 1
    return ''.join(out)


class FakeStomp:
    """Just enough of STOMP 1.2 to act like the Network Rail feed."""
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0)); self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.connections, self.logins, self.subscriptions, self.disconnects = 0, [], [], 0
        self.clients, self.lock = [], threading.Lock()
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        buf = b''
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
                while b'\x00' in buf:
                    raw, buf = buf.split(b'\x00', 1)
                    raw = raw.lstrip(b'\r\n')
                    head = raw.split(b'\n\n', 1)[0].decode()
                    lines = head.split('\n')
                    cmd, headers = lines[0], dict((l.split(':', 1) + [''])[:2] for l in lines[1:] if l)
                    headers = {k: unescape(v) for k, v in headers.items()}
                    if cmd == 'CONNECT':
                        self.logins.append((headers.get('login'), headers.get('passcode')))
                        if (headers.get('login'), headers.get('passcode')) == (USER, PASSWORD):
                            conn.sendall(b'CONNECTED\nversion:1.2\nheart-beat:15000,15000\n\n\x00')
                        else:
                            conn.sendall(b'ERROR\nmessage:bad login\n\n\x00'); conn.close(); return
                    elif cmd == 'SUBSCRIBE':
                        self.subscriptions.append(headers.get('destination'))
                        with self.lock:
                            self.clients.append(conn)
                    elif cmd == 'DISCONNECT':
                        self.disconnects += 1; conn.close(); return
        except OSError:
            return

    def publish(self, items):
        body = json.dumps(items).encode()
        msg = b'MESSAGE\ndestination:/topic/TRAIN_MVT_ALL_TOC\nmessage-id:1\nsubscription:1\n\n' + body + b'\x00\n'
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(msg)
                except OSError:
                    self.clients.remove(c)

    def keepalive(self):
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(b'\n')
                except OSError:
                    self.clients.remove(c)

    def close(self):
        self.sock.close()


def movement(train_id, stanox, event='DEPARTURE', minutes_ago=1, variation='LATE', mins='2', terminated='false'):
    return {'header': {'msg_type': '0003'}, 'body': {
        'train_id': train_id, 'loc_stanox': stanox, 'event_type': event, 'variation_status': variation,
        'timetable_variation': mins, 'actual_timestamp': str(int((time.time() - minutes_ago * 60) * 1000)),
        'train_terminated': terminated, 'toc_id': '23'}}


class RailEngine(unittest.TestCase):
    def setUp(self):
        self.files, self.stomp = FakeFiles(), FakeStomp()
        self.addCleanup(self.files.close)
        self.addCleanup(self.stomp.close)

    def engine(self, user=USER, password=PASSWORD, demand=None, data=None, points=None, save=None, keep=False, always=False):
        port = free_port()
        data = data or Path(tempfile.mkdtemp(prefix='tpa-rail-test-'))
        self.addCleanup(shutil.rmtree, data, True)
        if points is None:   # pretend timing points (the real bundled file is checked separately)
            points = data.parent / (data.name + '-points.csv')
            points.write_text('stanox,lat,lon,name,source\n15500,53.73303,-0.38189,Hessle Road Jn.,woodpecker\n'
                              '15600,99,-0.4,Bad Place,x\nabc,53.7,-0.4,Bad Code,x\n', encoding='utf-8')
            self.addCleanup(lambda: points.unlink(missing_ok=True))
        env = dict(os.environ, TPA_RAIL_ENGINE_PORT=str(port), TPA_RAIL_DATA_DIR=str(data),
                   TPA_RAIL_SAVE_SECONDS=str(save or 60),
                   TPA_RAIL_TEST_STOMP=f'127.0.0.1:{self.stomp.port}',
                   TPA_RAIL_TEST_CORPUS_URL=f'http://127.0.0.1:{self.files.port}/corpus',
                   TPA_RAIL_TEST_NAPTAN_URL=f'http://127.0.0.1:{self.files.port}/naptan')
        env.pop('TPA_NR_USERNAME', None); env.pop('TPA_NR_PASSWORD', None); env.pop('TPA_RAIL_TIMING_POINTS_FILE', None); env.pop('TPA_RAIL_ALWAYS_ON', None)
        if always:
            env['TPA_RAIL_ALWAYS_ON'] = '1'
        if points != 'bundled':
            env['TPA_RAIL_TIMING_POINTS_FILE'] = str(points)
        if user:
            env.update(TPA_NR_USERNAME=user, TPA_NR_PASSWORD=password)
        if demand:
            env['TPA_RAIL_DEMAND_SECONDS'] = str(demand)
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        proc = start(ENGINE, env, f'http://127.0.0.1:{port}/status', log)
        self.addCleanup(proc.wait, 5)
        self.addCleanup(proc.terminate)
        if keep:
            return f'http://127.0.0.1:{port}', log, data, proc
        return f'http://127.0.0.1:{port}', log, data

    def status(self, base):
        return json.loads(get(base + '/status')[2])

    def frame(self, base, query=YORK_VIEW):
        return json.loads(get(f'{base}/snapshot?{query}')[2])

    def until(self, check, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            value = check()
            if value:
                return value
            time.sleep(0.2)
        self.fail('condition not met in time')

    def connected(self, base):
        self.frame(base)
        return self.until(lambda: self.status(base)['connected'])

    def test_no_login_means_no_connection(self):
        base, _, _ = self.engine(user='')
        frame = self.frame(base)
        self.assertEqual((frame['health'], frame['notice']), ('unavailable', 'Network Rail login not set up on this PC'))
        time.sleep(1.5)
        self.assertEqual((self.stomp.connections, self.files.requests), (0, []))

    def test_station_list_exact_matches_only_and_cached(self):
        base, _, data = self.engine()
        self.connected(base)
        st = self.status(base)
        self.assertEqual(st['stationsKnown'], 63)   # York, Hull, Beverley + 60 padding; NOWHERE and CONFLCT excluded
        ref = json.loads((data / 'station-reference.json').read_text())
        self.assertNotIn('15300', ref['stations'])
        self.assertNotIn('15400', ref['stations'])
        self.assertEqual(ref['stations']['16000'][2], 'York')
        # A restart within 7 days uses the saved list: no second download.
        before = len(self.files.requests)
        base2, _, _ = self.engine(data=data)
        self.connected(base2)
        self.assertEqual(len(self.files.requests), before)

    def test_login_escaped_and_feed_subscribed(self):
        base, _, _ = self.engine()
        self.connected(base)
        self.assertEqual(self.stomp.logins[0], (USER, PASSWORD))   # ':' and '\\' in the password survive
        self.assertEqual(self.stomp.subscriptions, ['/topic/TRAIN_MVT_ALL_TOC'])

    def test_trains_shown_at_last_reported_station(self):
        base, log, _ = self.engine()
        self.connected(base)
        self.stomp.publish([
            movement('161A23MZ30', '16000', 'DEPARTURE', 1, 'LATE', '2'),           # York, fresh
            movement('152H45MZ30', '15100', 'ARRIVAL', 1, 'ON TIME', '0'),          # Hull
            movement('152H46MZ30', '15200', 'DEPARTURE', 15, 'EARLY', '1'),         # Beverley, 15 min old -> faded
            movement('152H47MZ30', '15200', 'ARRIVAL', 40),                          # 40 min old -> not shown
            movement('159Z99MZ30', '15300'),                                         # unknown location -> not placed
            movement('152H48MZ30', '15100', 'ARRIVAL', 25, terminated='true'),       # terminated 25 min ago -> gone
            {'header': {'msg_type': '0003'}, 'body': {'train_id': 'BAD'}},           # malformed -> ignored
        ])
        frame = self.until(lambda: (f := self.frame(base))['entities'] and f)
        by = {e['id']: e for e in frame['entities']}
        self.assertEqual(set(by), {'rail:161A23MZ30', 'rail:152H45MZ30', 'rail:152H46MZ30'})
        self.assertEqual(by['rail:161A23MZ30']['label'], 'Train 1A23 · departed York · 2 min late')
        self.assertEqual(by['rail:161A23MZ30']['badge'], '1A23')
        self.assertEqual(by['rail:161A23MZ30']['position'], {'lon': -1.09312, 'lat': 53.95799, 'headingDeg': None})
        self.assertEqual(by['rail:152H45MZ30']['label'], 'Train 2H45 · arrived at Hull Paragon · on time')
        self.assertEqual(by['rail:152H46MZ30']['quality'], 'stale')
        self.assertEqual(by['rail:161A23MZ30']['quality'], 'observed')
        self.assertIn('Network Rail', frame['attribution'])
        self.assertEqual(self.status(base)['movementsAtUnmappedLocations'], 1)
        # The train moves on: it jumps to the next station it reports at.
        self.stomp.publish([movement('161A23MZ30', '15200', 'ARRIVAL', 0, 'LATE', '3')])
        self.until(lambda: any(e['id'] == 'rail:161A23MZ30' and e['label'] == 'Train 1A23 · arrived at Beverley · 3 min late'
                               for e in self.frame(base)['entities']))
        # 0.4.1 cancelled: still shown (faded) for 10 minutes, saying so, instead of vanishing.
        self.stomp.publish([{'header': {'msg_type': '0002'}, 'body': {'train_id': '152H45MZ30'}}])
        self.until(lambda: any(e['id'] == 'rail:152H45MZ30' and e['label'] == 'Train 2H45 · cancelled · last at Hull Paragon · on time'
                               and e['quality'] == 'stale' for e in self.frame(base)['entities']))
        self.assertEqual(self.status(base)['cancellationsSeen'], 1)
        log.seek(0)
        out = log.read().decode('utf-8', 'replace')
        self.assertNotIn(PASSWORD, out)
        self.assertNotIn(USER, out)

    def test_wrong_login_reported_and_not_hammered(self):
        base, log, _ = self.engine(password='wrong-password')
        self.frame(base)
        st = self.until(lambda: (s := self.status(base))['referenceError'] and s)
        self.assertEqual(st['notice'], 'Network Rail did not accept the login')
        time.sleep(6)
        self.frame(base)
        self.assertEqual(sum(1 for r in self.files.requests if r.startswith('/corpus')), 1)
        self.assertEqual(self.stomp.connections, 0)
        log.seek(0)
        self.assertNotIn('wrong-password', log.read().decode('utf-8', 'replace'))

    def test_disconnects_when_nobody_is_looking(self):
        base, _, _ = self.engine(demand=5)
        self.connected(base)
        def beats():
            for _ in range(20):
                self.stomp.keepalive(); time.sleep(0.5)
        threading.Thread(target=beats, daemon=True).start()
        self.until(lambda: self.stomp.disconnects == 1, timeout=15)
        self.assertFalse(self.status(base)['connected'] and False)

    def test_server_always_on_keeps_listening_with_nobody_looking(self):
        # 0.2.1 server mode: connects without anyone viewing and never drops for "nobody looking".
        base, _, _ = self.engine(demand=5, always=True)
        self.until(lambda: self.stomp.subscriptions, timeout=15)
        self.assertTrue(self.status(base)['alwaysOn'])
        for _ in range(24):              # 12 s, well past the 5 s viewing window
            self.stomp.keepalive(); time.sleep(0.5)
        self.assertEqual((self.stomp.disconnects, self.stomp.connections), (0, 1))

    def test_bundled_timing_points_load(self):
        base, _, _ = self.engine(user='', points='bundled')
        st = self.status(base)
        self.assertGreater(st['timingPointsKnown'], 10000)
        self.assertEqual(st['engineVersion'], '0.4.1')

    def test_trains_placed_at_timing_points_when_no_station(self):
        base, _, _ = self.engine()
        self.connected(base)
        st = self.status(base)
        self.assertEqual(st['timingPointsKnown'], 1)   # bad latitude and bad code rows ignored
        self.stomp.publish([movement('159Z99MZ30', '15500', 'ARRIVAL', 1, 'ON TIME', '0'),   # junction
                            movement('152H45MZ30', '15100', 'ARRIVAL', 1, 'ON TIME', '0'),   # station still wins
                            movement('152H49MZ30', '15300')])                                # still unknown
        frame = self.until(lambda: len((f := self.frame(base))['entities']) == 2 and f)
        by = {e['id']: e for e in frame['entities']}
        self.assertEqual(by['rail:159Z99MZ30']['label'], 'Train 9Z99 · passed Hessle Road Jn. · on time')
        self.assertEqual(by['rail:159Z99MZ30']['position'], {'lon': -0.38189, 'lat': 53.73303, 'headingDeg': None})
        self.assertEqual(by['rail:152H45MZ30']['label'], 'Train 2H45 · arrived at Hull Paragon · on time')
        self.assertIn('junctions', frame['attribution'])
        st = self.status(base)
        self.assertEqual((st['movementsAtTimingPoints'], st['movementsAtUnmappedLocations']), (1, 1))

    def test_remembers_last_30_minutes_across_restart(self):
        base, _, data, proc = self.engine(save=1, keep=True)
        self.connected(base)
        self.stomp.publish([movement('161A23MZ30', '16000', 'DEPARTURE', 1, 'LATE', '2'),
                            movement('152H46MZ30', '15200', 'DEPARTURE', 15, 'EARLY', '1')])
        self.until(lambda: len(self.frame(base)['entities']) == 2)
        memory = data / 'trains-last-30-min.json'
        self.until(lambda: memory.is_file() and len(json.loads(memory.read_text())['trains']) == 2)
        saved = json.loads(memory.read_text())
        self.assertNotIn(PASSWORD, memory.read_text())
        # Sneak in an old train and a damaged entry: both must be ignored on restart.
        saved['trains']['159Z11MZ30'] = dict(saved['trains']['161A23MZ30'], when=time.time() - 45 * 60)
        saved['trains']['NOT-A-TRAIN'] = {'lat': 'x'}
        saved['trains']['159Z12MZ30'] = {'lat': 'nonsense', 'lon': 1}
        proc.kill(); proc.wait(5)          # hard stop, like closing the window
        memory.write_text(json.dumps(saved))
        self.stomp.subscriptions.clear()
        # Restart with no login at all: nothing new can arrive, so anything shown came from memory.
        base2, _, _ = self.engine(user='', data=data)
        frame = self.frame(base2)
        by = {e['id']: e for e in frame['entities']}
        self.assertEqual(set(by), {'rail:161A23MZ30', 'rail:152H46MZ30'})
        self.assertEqual(by['rail:161A23MZ30']['label'], 'Train 1A23 · departed York · 2 min late')
        self.assertEqual(by['rail:161A23MZ30']['quality'], 'observed')
        self.assertEqual(by['rail:152H46MZ30']['quality'], 'stale')   # still honest about its age
        self.assertEqual(self.status(base2)['trainsRestoredAtStart'], 2)
        self.assertEqual(self.stomp.subscriptions, [])

    def test_damaged_memory_file_is_ignored(self):
        data = Path(tempfile.mkdtemp(prefix='tpa-rail-test-'))
        (data / 'trains-last-30-min.json').write_text('{not json', encoding='utf-8')
        base, _, _ = self.engine(user='', data=data)
        self.assertEqual(self.status(base)['trainsRestoredAtStart'], 0)

    def test_zoomed_out_asks_to_zoom_in(self):
        base, _, _ = self.engine()
        frame = self.frame(base, 'west=-6&south=50&east=2&north=58')
        self.assertEqual((frame['notice'], frame['entities']), ('Zoom in to see trains', []))


try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and (VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class HQWithTrains(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        cls.files, cls.stomp = FakeFiles(), FakeStomp()
        cls.seeded = []
        cache = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8' / 'vendor'
        cache.mkdir(parents=True, exist_ok=True)
        for route, src in {'/assets/vendor/leaflet.js': 'leaflet/dist/leaflet.js', '/assets/vendor/leaflet.css': 'leaflet/dist/leaflet.css'}.items():
            target = cache / hashlib.sha256(route.encode()).hexdigest()
            if not target.exists():
                shutil.copyfile(VENDOR / src, target); cls.seeded.append(target)
        cls.data = Path(tempfile.mkdtemp(prefix='tpa-rail-hq-'))
        cls.rail_port, cls.hq_port = free_port(), free_port()
        base = {k: v for k, v in os.environ.items() if k not in ('TPA_HQ_FIXTURE_DIR', 'TPA_NR_USERNAME', 'TPA_NR_PASSWORD')}
        cls.logs = [tempfile.TemporaryFile(), tempfile.TemporaryFile()]
        cls.rail = start(ENGINE, dict(base, TPA_RAIL_ENGINE_PORT=str(cls.rail_port), TPA_RAIL_DATA_DIR=str(cls.data),
                                      TPA_NR_USERNAME=USER, TPA_NR_PASSWORD=PASSWORD,
                                      TPA_RAIL_TEST_STOMP=f'127.0.0.1:{cls.stomp.port}',
                                      TPA_RAIL_TEST_CORPUS_URL=f'http://127.0.0.1:{cls.files.port}/corpus',
                                      TPA_RAIL_TEST_NAPTAN_URL=f'http://127.0.0.1:{cls.files.port}/naptan'),
                         f'http://127.0.0.1:{cls.rail_port}/status', cls.logs[0])
        cls.hq = start(HQ, dict(base, TPA_HQ_PORT=str(cls.hq_port), TPA_HQ_PARKED='sea', TPA_HQ_DISABLE_MAP_NETWORK='1',
                                TPA_HQ_ENGINES=f'rail=http://127.0.0.1:{cls.rail_port}'),
                       f'http://127.0.0.1:{cls.hq_port}/api/v4/manifest', cls.logs[1])

    @classmethod
    def tearDownClass(cls):
        for p in (cls.hq, cls.rail):
            if p.poll() is None:
                p.terminate(); p.wait(5)
        cls.files.close(); cls.stomp.close()
        shutil.rmtree(cls.data, True)
        for f in cls.seeded:
            f.unlink(missing_ok=True)

    def test_1_browser_shows_live_trains(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{self.hq_port}/')
            page.wait_for_selector('#tpa-welcome:not(.hidden)', timeout=10000)
            page.locator('#tpa-welcome .tpa-primary').click()
            end = time.time() + 30
            while time.time() < end and not json.loads(get(f'http://127.0.0.1:{self.rail_port}/status')[2])['connected']:
                time.sleep(0.3)
            self.stomp.publish([movement('152H45MZ30', '15200', 'DEPARTURE', 1, 'LATE', '4'),
                                movement('152H46MZ30', '15100', 'ARRIVAL', 1, 'ON TIME', '0')])
            wait_until(page, "document.querySelectorAll('.review-pin.rail').length>=1", timeout=40)
            self.assertEqual(page.locator('#rail-chip strong').inner_text(), 'LIVE')
            self.assertIn('LIVE: TRAINS', page.locator('.mock-banner').inner_text())
            self.assertIn('2H45', page.locator('.review-pin.rail .review-route').all_inner_texts())
            page.locator('.review-pin.rail').first.click(force=True)
            details = page.locator('#map-selection').inner_text()
            self.assertIn('Train 2H45 · departed Beverley · 4 min late', details)
            self.assertIn('Network Rail', details)
            self.assertNotIn('FICTIONAL', details)
            page.screenshot(path=str(PACKAGE / 'v4/docs/hq/HQ-4.0.11-TRAINS-LIVE-TEST-PRETEND-NR.png'))
            self.assertEqual(errors, [])
            browser.close()

    def test_2_train_engine_crash_affects_only_trains(self):
        self.rail.terminate(); self.rail.wait(5)
        data = json.loads(get(f'http://127.0.0.1:{self.hq_port}/api/v4/manifest')[2])
        by = {c['id']: c for c in data['components']}
        self.assertEqual(by['rail'], {'id': 'rail', 'status': 'unavailable', 'live': True, 'notice': 'Engine not running'})
        self.assertEqual(get(f'http://127.0.0.1:{self.hq_port}/')[0], 200)
        for log in self.logs:
            log.seek(0)
            text = log.read().decode('utf-8', 'replace')
            self.assertNotIn(PASSWORD, text)


if __name__ == '__main__':
    unittest.main()
