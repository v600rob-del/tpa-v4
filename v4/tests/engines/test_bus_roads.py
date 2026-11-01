"""Bus engine 0.4.0 road map (owner, 30 Oct 2026: "we need it to appear like we have GPS when we
don't, so a smooth lifelike movement"). Between reports a bus glides along its real road
(Ordnance Survey "OS Open Roads"), not in a straight line across buildings.
Uses a small pretend OS download (os_roads_fixture.py) and a pretend OS Downloads API on 127.0.0.1.
"""
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_roads_fixture as fx  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rm = load('road_map_t', 'v4/engines/bus/road_map.py')


def save(path, data):
    with open(path, 'wb') as f:
        f.write(data)


def ll(E, N):
    return rm.osgb_to_wgs84(E, N)


def metres(a, b):
    k = math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111195


def path_len(p):
    return sum(metres(p[i], p[i + 1]) for i in range(len(p) - 1))


def dist_to_roads(pt, roads=fx.TOWN):
    best = 1e9
    for pts, *_ in roads:
        q = [ll(*p) for p in pts]
        for a, b in zip(q, q[1:]):
            k = math.cos(math.radians(pt[1]))
            ax, ay, bx, by = (a[0] - pt[0]) * k, a[1] - pt[1], (b[0] - pt[0]) * k, b[1] - pt[1]
            vx, vy = bx - ax, by - ay
            f = max(0, min(1, -(ax * vx + ay * vy) / ((vx * vx + vy * vy) or 1e-18)))
            best = min(best, math.hypot(ax + vx * f, ay + vy * f) * 111195)
    return best


class Build(unittest.TestCase):
    def test_both_os_download_formats_give_the_same_map(self):
        with tempfile.TemporaryDirectory() as d:
            save(d + '/shp.zip', fx.shapefile_zip(squares=('TQ', 'TL')))
            save(d + '/gpkg.zip', fx.geopackage_zip(d + '/t.gpkg'))
            a = rm.build(d + '/shp.zip', d + '/a.sqlite')
            b = rm.build(d + '/gpkg.zip', d + '/b.sqlite')
            self.assertEqual(a, b)
            self.assertGreater(a, len(fx.TOWN))   # the 3 km road is cut into 400 m pieces
            m = rm.RoadMap(d + '/a.sqlite')
            self.assertIn('Contains OS data', m.meta['licence'])
            self.assertFalse(os.path.exists(d + '/roads-extract.gpkg'))   # temporary copy tidied away
            m.close()

    def test_fast_conversion_matches_exact_formula(self):
        g = rm.FastGrid()
        for E, N in ((530123.45, 180987.65), (100001, 900002), (654321, 300000), (399999.9, 1199999)):
            a, b = g(E, N), rm.osgb_to_wgs84(E, N)
            self.assertLess(metres(a, b), 0.25, (E, N))


class Ahead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        save(cls.dir.name + '/os.zip', fx.shapefile_zip())
        rm.build(cls.dir.name + '/os.zip', cls.dir.name + '/roads.sqlite')
        cls.m = rm.RoadMap(cls.dir.name + '/roads.sqlite')

    @classmethod
    def tearDownClass(cls):
        cls.m.close()
        cls.dir.cleanup()

    def walk(self, E, N, bearing, metres_, offset=(0, 0)):
        lon, lat = ll(E + offset[0], N + offset[1])
        return self.m.ahead(lon, lat, bearing, metres_)

    def assert_on_roads(self, p):
        for pt in p:
            self.assertLess(dist_to_roads(pt), 3, pt)

    def test_straight_on_over_the_crossroads(self):
        p = self.walk(529500, 180000, 90, 1200)
        self.assert_on_roads(p)
        self.assertAlmostEqual(path_len(p), 1200, delta=15)
        self.assertLess(metres(p[-1], ll(530700, 180000)), 15)

    def test_follows_the_bend_instead_of_cutting_across(self):
        p = self.walk(529500, 180000, 90, 2000)
        self.assert_on_roads(p)
        self.assertAlmostEqual(path_len(p), 2000, delta=20)
        # 1500 m to the bend start, then 300 m east and 200 m up the diagonal
        self.assertLess(metres(p[-1], ll(531300 + 200 / math.sqrt(2), 180000 + 200 / math.sqrt(2))), 15)

    def test_reported_slightly_off_the_road_and_slightly_wrong_heading(self):
        p = self.walk(529500, 180000, 70, 600, offset=(0, 20))   # 20 m beside the road, 20 degrees off
        self.assertLess(dist_to_roads(p[0]), 3)
        self.assertLess(metres(p[-1], ll(530100, 180000)), 25)

    def test_stops_at_a_dead_end_and_never_u_turns(self):
        p = self.walk(529500, 180000, 270, 900)
        self.assertAlmostEqual(path_len(p), 500, delta=15)
        self.assertLess(metres(p[-1], ll(529000, 180000)), 10)
        p = self.walk(529500, 180000, 90, 9000)
        self.assertLess(metres(p[-1], ll(531500, 180600)), 10)      # end of Bend Lane

    def test_waits_at_a_t_junction(self):
        """0.5.3: left or right are equally likely - don't guess; wait at the junction for the next report."""
        p = self.walk(530000, 180500, 0, 800)
        self.assert_on_roads(p)
        self.assertLess(metres(p[-1], ll(530000, 181000)), 5)

    def test_found_partway_along_a_long_road(self):
        p = self.walk(527000, 179500, 0, 600)
        self.assert_on_roads(p)
        self.assertLess(metres(p[-1], ll(527000, 180100)), 15)

    def test_no_road_nearby_or_going_the_wrong_way(self):
        self.assertIsNone(self.walk(528500, 179500, 0, 300))           # in a field
        self.assertIsNone(self.walk(529500, 180000, 0, 300, offset=(0, 30)))   # heading across the road
        self.assertIsNone(self.m.ahead(*ll(529500, 180000), None, 300))

    def test_memory_is_capped(self):
        small = rm.RoadMap(self.dir.name + '/roads.sqlite', max_tiles=3)
        for E in range(526000, 532000, 500):
            small.ahead(*ll(E, 180000), 90, 300)
        self.assertLessEqual(len(small.tiles), 3)
        small.close()


class FakeOS:
    """Pretend OS Downloads API + file host on 127.0.0.1."""
    def __init__(self, payload, md5=None):
        self.payload, self.hits = payload, {'list': 0, 'file': 0}
        self.md5 = md5 or hashlib.md5(payload).hexdigest()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith('/downloads'):
                    outer.hits['list'] += 1
                    body = json.dumps([
                        {'area': 'GB', 'format': 'GML', 'url': outer.base + '/no.zip', 'fileName': 'x.zip'},
                        {'area': 'GB', 'format': 'ESRI® Shapefile', 'url': outer.base + '/oproad_essh_gb.zip',
                         'fileName': 'oproad_essh_gb.zip', 'size': len(outer.payload), 'md5': outer.md5}]).encode()
                elif self.path == '/oproad_essh_gb.zip':
                    outer.hits['file'] += 1
                    body = outer.payload
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class Engine(unittest.TestCase):
    """The bus engine downloads the map by itself, then sends road paths to HQ."""
    def engine(self, api, data_dir):
        old = dict(os.environ)
        os.environ.update(TPA_BUS_ROAD_MAP='download', TPA_BUS_DATA_DIR=data_dir, TPA_BUS_ROADS_TEST_API=api + '/products/OpenRoads')
        try:
            return load('bus_roads_e', 'v4/engines/bus/bus_engine.py')
        finally:
            os.environ.clear(); os.environ.update(old)

    def buses(self, bus):
        t0 = time.time() - 30
        lon, lat = ll(529500, 180000)
        lon2, lat2 = ll(529650, 180000)
        old = [{'key': 'OP:1', 'lat': lat, 'lon': lon, 'bearing': 90.0, 'recorded': t0 - 15}]
        new = [{'key': 'OP:1', 'lat': lat2, 'lon': lon2, 'bearing': 90.0, 'recorded': t0, 'route': '25', 'destination': 'Town'}]
        bus.add_motion(new, old)
        with bus.LOCK:
            bus.STATE.update(vehicles=new, fetchedAt=time.time(), coverage=bus.GB)
        return bus.snapshot((lat - 0.02, lon - 0.03, lat + 0.02, lon + 0.03))

    def test_downloads_builds_and_glides_buses_along_roads(self):
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertEqual(bus.STATE['roadMap'], 'starting')
            m = self.buses(bus)['entities'][0]['motion']
            self.assertEqual(m['mode'], 'heading')                    # before the map exists: straight on
            self.assertTrue(bus.refresh_road_map())
            self.assertTrue(bus.open_road_map())
            self.assertEqual(bus.STATE['roadMap'], 'ready')
            self.assertFalse(os.path.exists(d + '/roads-download.zip'))   # the 1 GB download is not kept
            frame = self.buses(bus)
            m = frame['entities'][0]['motion']
            self.assertEqual(m['mode'], 'path')
            self.assertEqual(m['maxSeconds'], 90)
            self.assertIs(m['slowToStop'], True)
            self.assertAlmostEqual(m['speedMps'], 10.0, delta=0.3)
            self.assertAlmostEqual(path_len(m['points']), 10.0 * 75, delta=40)   # 60 s steady + 30 s easing
            for pt in m['points']:
                self.assertLess(dist_to_roads(pt), 3)
            self.assertIn('Contains OS data', frame['attribution'])
            # 0.4.1: zoomed out past town level, buses keep the cheap straight glide (no road work)
            lon, lat = ll(529650, 180000)
            wide = bus.snapshot((lat - 0.1, lon - 0.15, lat + 0.1, lon + 0.15))
            self.assertEqual(wide['entities'][0]['motion']['mode'], 'heading')
            self.assertLessEqual(len(frame['attribution']), 256)
            # A week later: OS has nothing new, so only the tiny list is fetched again.
            self.assertTrue(bus.refresh_road_map())
            self.assertEqual(fake.hits, {'list': 2, 'file': 1})
            bus.ROADS['map'].close()
        fake.close()

    def test_damaged_download_is_refused_and_buses_still_move(self):
        fake = FakeOS(fx.shapefile_zip(), md5='0' * 32)
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertFalse(bus.refresh_road_map())
            self.assertFalse(bus.open_road_map())
            self.assertIn('checksum', bus.STATE['roadMapError'])
            self.assertEqual(os.listdir(d), [])
            self.assertEqual(self.buses(bus)['entities'][0]['motion']['mode'], 'heading')
        fake.close()

    def test_bus_download_comes_first(self):
        """Server log 30 Oct: downloads took 5 s normally but 17-42 s (and timed out) while road work ran."""
        import subprocess
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertTrue(bus.refresh_road_map()); self.assertTrue(bus.open_road_map())
            # a map rebuild running in the background is paused during the download, then resumed
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            bus.BUILDER['proc'] = child
            seen = {}

            def fake_fetch(area):
                seen['state'] = open(f'/proc/{child.pid}/stat').read().split(')')[1].split()[0]
                seen['fetching'] = bus.FETCHING.is_set()
                lon, lat = ll(529650, 180000)
                v = {'key': 'OP:9', 'lat': lat, 'lon': lon, 'bearing': 90.0, 'recorded': time.time(), 'speed': 8.0}
                seen['inline'] = bus.motion_for(v, time.monotonic() + 5)['mode']     # no road work now: queued
            bus._fetch_once = fake_fetch
            bus.fetch_once(None)
            time.sleep(0.2)
            after = open(f'/proc/{child.pid}/stat').read().split(')')[1].split()[0]
            child.kill(); child.wait()
            self.assertEqual(seen['state'], 'T')            # stopped while downloading
            self.assertIn(after, ('S', 'R'))                # running again afterwards
            self.assertTrue(seen['fetching'])
            self.assertEqual(seen['inline'], 'heading')
            self.assertFalse(bus.FETCHING.is_set())
            self.assertEqual(len(bus.PENDING), 1)           # done by the helper thread after the download
            self.assertIsNotNone(bus.TIMING['fetch'])
            bus.ROADS['map'].close()
        fake.close()

    def test_buses_going_the_same_way_are_drawn_on_the_same_side(self):
        """0.5.7 (owner 1 Nov 01:03, Birmingham, The Priory Queensway, screenshot): "both buses were
        travelling in the same direction" but one was drawn on each side of the road, so they looked to
        be going opposite ways. GPS reports are often 5-20 m out. Both - moving or standing - must be
        drawn on the road, on the left-hand side for their direction (UK traffic keeps left)."""
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertTrue(bus.refresh_road_map()); self.assertTrue(bus.open_road_map())
            now = time.time() - 20
            k = 111195 * math.cos(math.radians(ll(529500, 180000)[1]))

            def report(key, E, N, bearing, speed=None):
                lon, lat = ll(E, N)
                v = {'key': key, 'lat': lat, 'lon': lon, 'bearing': bearing, 'recorded': now, 'route': key[-2:], 'destination': ''}
                if speed:
                    v['speed'] = speed
                return v
            vs = [report('A:94', 529400, 180008, 90.0, 8.0),      # eastbound, GPS 8 m north of the line
                  report('A:51', 529600, 179992, 90.0, 8.0),      # eastbound, GPS 8 m south of the line
                  report('S:94', 529400, 180007, 90.0),           # eastbound, standing at a stop
                  report('S:51', 529600, 179993, 90.0),
                  report('W:12', 529500, 180009, 270.0),          # westbound, standing, GPS north
                  report('F:99', 528500, 179500, 0.0)]            # in a field, 500 m from any road
            with bus.LOCK:
                bus.STATE.update(vehicles=vs, fetchedAt=time.time(), coverage=bus.GB)
            lon, lat = ll(529500, 180000)
            frame = bus.snapshot((lat - 0.01, lon - 0.015, lat + 0.01, lon + 0.015))
            at = {e['id'].split(':', 1)[1]: e for e in frame['entities']}
            A, B = ll(529000, 180000), ll(531000, 180000)                  # High Street (grid lines aren't due east)

            def side(lon_, lat_):                                          # metres to the left of A->B (north)
                kx = math.cos(math.radians(lat_)) * 111195
                ux, uy = (B[0] - A[0]) * kx, (B[1] - A[1]) * 111195
                px, py = (lon_ - A[0]) * kx, (lat_ - A[1]) * 111195
                return (ux * py - uy * px) / math.hypot(ux, uy)
            north = lambda e: side(e['position']['lon'], e['position']['lat'])
            for key in ('A:94', 'A:51', 'S:94', 'S:51'):
                self.assertAlmostEqual(north(at[key]), 2.0, delta=0.5, msg=f'{key} drawn {north(at[key]):.1f} m north of the road line')
            self.assertAlmostEqual(north(at['W:12']), -2.0, delta=0.5)   # westbound keeps to the other side
            for key in ('A:94', 'A:51'):                                   # and moves along that side
                m = at[key]['motion']
                self.assertEqual(m['mode'], 'path')
                for pt in m['points']:
                    if pt[0] < ll(529990, 180000)[0]:                         # before the crossroads
                        self.assertAlmostEqual(side(*pt), 2.0, delta=0.5)
                self.assertEqual(m['points'][0], [at[key]['position']['lon'], at[key]['position']['lat']])
            far = at['F:99']['position']                                   # nowhere near a road: left alone
            self.assertEqual((far['lon'], far['lat']), (vs[-1]['lon'], vs[-1]['lat']))
            self.assertEqual(bus.STATE['lastView']['placed-on-road'], 3)
            bus.ROADS['map'].close()
        fake.close()

    def test_speed_and_direction_from_recent_reports(self):
        """Server check 31 Oct 00:22: 74 of 184 buses had no movement (last two reports at a stop),
        and one bus said 159 degrees while really going 271."""
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine('http://127.0.0.1:1', d)
            t0 = time.time() - 120
            track = [(0, 529000), (30, 529300), (60, 529300), (75, 529300)]   # 300 m, then 45 s at a stop
            old = []
            for dt, E in track:
                lon, lat = ll(E, 180000)
                new = [{'key': 'X:1', 'lat': lat, 'lon': lon, 'bearing': 159.0, 'recorded': t0 + dt}]
                bus.add_motion(new, old)
                old = new
            v = old[0]
            self.assertAlmostEqual(v['speed'], 300 / 75, delta=0.2)     # keeps its usual pace through the stop
            self.assertAlmostEqual(v['bearing'], 90, delta=3)           # where it really went, not 159
            # a bus parked for good: no movement once the last 3 minutes show it standing still
            lon, lat = ll(529300, 180000)
            for dt in (150, 200, 260):
                new = [{'key': 'X:1', 'lat': lat, 'lon': lon, 'bearing': None, 'recorded': t0 + dt}]
                bus.add_motion(new, old)
                old = new
            self.assertNotIn('speed', old[0])
            # a bus company direction that agrees with the movement is kept (fresher at corners)
            lon2, lat2 = ll(529000, 181000)
            lon3, lat3 = ll(529000, 181200)
            a = [{'key': 'X:2', 'lat': lat2, 'lon': lon2, 'bearing': 20.0, 'recorded': t0}]
            b = [{'key': 'X:2', 'lat': lat3, 'lon': lon3, 'bearing': 20.0, 'recorded': t0 + 20}]
            bus.add_motion(a, []); bus.add_motion(b, a)
            self.assertEqual(b[0]['bearing'], 20.0)

    def test_road_path_ready_first_time_no_switching(self):
        """Server check 31 Oct: 12 buses switched from straight to road movement on the same report."""
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertTrue(bus.refresh_road_map()); self.assertTrue(bus.open_road_map())
            bus.INLINE_BUDGET = bus.NEW_AREA_BUDGET = 0   # pretend the server is far too busy to work anything out on request
            threading.Thread(target=bus.path_worker, daemon=True).start()
            frame = self.buses(bus)          # someone looks at the area (straight: nothing prepared yet)
            self.assertEqual(frame['entities'][0]['motion']['mode'], 'heading')
            self.assertEqual(bus.STATE['lastView']['straight-pending'], 1)
            # the next download brings a new report for the bus; it is prepared straight away
            t1 = time.time() - 10
            lon, lat = ll(529800, 180000)
            with bus.LOCK:
                v = dict(bus.STATE['vehicles'][0], lat=lat, lon=lon, recorded=t1)
                bus.add_motion([v], bus.STATE['vehicles'])
                bus.STATE['vehicles'] = [v]
            bus._fetch_once = lambda area: None
            bus.fetch_once(None)
            for _ in range(50):
                if not bus.PENDING:
                    break
                time.sleep(0.05)
            time.sleep(0.1)
            lat0, lon0 = lat, lon
            frames = [bus.snapshot((lat0 - 0.02, lon0 - 0.03, lat0 + 0.02, lon0 + 0.03)) for _ in range(3)]
            self.assertEqual([f['entities'][0]['motion']['mode'] for f in frames], ['path'] * 3)
            self.assertEqual(bus.STATE['lastView']['road'], 1)
            # a report older than 5 minutes stays where it was seen
            with bus.LOCK:
                bus.STATE['vehicles'][0]['recorded'] = time.time() - 400
            self.assertNotIn('motion', bus.snapshot((lat0 - 0.02, lon0 - 0.03, lat0 + 0.02, lon0 + 0.03))['entities'][0])
            bus.ROADS['map'].close()
        fake.close()

    def test_nearby_and_new_areas_get_road_paths_first_time(self):
        """0.5.4 (owner 31 Oct 22:46: moving "a couple of tiles" looked wrong): road paths are prepared
        for a margin around what is being viewed, and a never-viewed area gets them on its first answer."""
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertTrue(bus.refresh_road_map()); self.assertTrue(bus.open_road_map())
            t0 = time.time() - 30
            def moving(key, E):
                a, b = ll(E - 150, 180000), ll(E, 180000)
                old = [{'key': key, 'lat': a[1], 'lon': a[0], 'bearing': 90.0, 'recorded': t0 - 15}]
                new = [{'key': key, 'lat': b[1], 'lon': b[0], 'bearing': 90.0, 'recorded': t0, 'route': '1', 'destination': 'X'}]
                bus.add_motion(new, old)
                return new[0]
            near, far = moving('N:1', 529300), moving('F:1', 529800)     # 500 m apart along High Street
            with bus.LOCK:
                bus.STATE.update(vehicles=[near, far], fetchedAt=time.time(), coverage=bus.GB)
            bus.INLINE_BUDGET = 0                                          # pretend a busy server
            view = lambda v, h: (v['lat'] - h, v['lon'] - h * 1.6, v['lat'] + h, v['lon'] + h * 1.6)
            f1 = bus.snapshot(view(near, 0.003))                           # a never-viewed area: worked out at once
            self.assertEqual([e['motion']['mode'] for e in f1['entities']], ['path'])
            bus.prepare_paths()                                            # after the next download ...
            while bus.PENDING:                                             # ... the helper prepares the margin
                v = bus.PENDING.popleft(); bus.PENDING_SET.discard((v['key'], v['recorded']))
                bus.PATHS[v['key']] = (v['recorded'], bus.road_motion(v))
            self.assertEqual(bus.PATHS['F:1'][1]['mode'], 'path')         # just outside the view: ready
            bus.NEW_AREA_BUDGET = 0
            f2 = bus.snapshot(view(far, 0.003))                            # pan there: road path first time
            self.assertEqual([e['motion']['mode'] for e in f2['entities']], ['path'])
            bus.ROADS['map'].close()
        fake.close()

    def test_city_view_prepares_roads_before_zooming_in(self):
        """0.5.6 (owner 1 Nov 00:18: "this needs to LOOK right"): looking at a whole city from further
        out prepares every moving bus's road in the background (nearest the middle first, up to a
        limit), so zooming in finds them already on their roads."""
        fake = FakeOS(fx.shapefile_zip())
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base, d)
            self.assertTrue(bus.refresh_road_map()); self.assertTrue(bus.open_road_map())
            t0 = time.time() - 30
            vs = []
            for i, E in enumerate((529300, 529600, 530400)):
                a, b = ll(E - 150, 180000), ll(E, 180000)
                old = [{'key': f'C:{i}', 'lat': a[1], 'lon': a[0], 'bearing': 90.0, 'recorded': t0 - 15}]
                new = [{'key': f'C:{i}', 'lat': b[1], 'lon': b[0], 'bearing': 90.0, 'recorded': t0, 'route': str(i), 'destination': 'X'}]
                bus.add_motion(new, old); vs.append(new[0])
            with bus.LOCK:
                bus.STATE.update(vehicles=vs, fetchedAt=time.time(), coverage=bus.GB)
            c = ll(529600, 180000)
            city = (c[1] - 0.2, c[0] - 0.32, c[1] + 0.2, c[0] + 0.32)              # ~45 km tall: a whole city
            frame = bus.snapshot(city)
            self.assertTrue(all(e['motion']['mode'] == 'heading' for e in frame['entities']))   # zoomed out: simple
            bus.PREP_MAX = 2
            bus.prepare_paths()
            self.assertEqual(sorted(v['key'] for v in bus.PENDING), ['C:0', 'C:1'])   # the two nearest the middle
            while bus.PENDING:
                v = bus.PENDING.popleft(); bus.PENDING_SET.discard((v['key'], v['recorded']))
                bus.PATHS[v['key']] = (v['recorded'], bus.road_motion(v))
            bus.INLINE_BUDGET = bus.NEW_AREA_BUDGET = 0                             # nothing worked out on the spot
            close = bus.snapshot((c[1] - 0.003, c[0] - 0.005, c[1] + 0.003, c[0] + 0.005))   # zoom in
            self.assertEqual({e['id']: e['motion']['mode'] for e in close['entities']}, {'bus:C:0': 'path', 'bus:C:1': 'path'})
            bus.ROADS['map'].close()
        fake.close()

    def test_always_on_keeps_buses_fresh_when_nobody_is_looking(self):
        """0.5.5 (owner 31 Oct 23:47: slow to load, then slow to move): with TPA_BUS_ALWAYS_ON the
        engine keeps downloading when nobody is watching (every 60 s), every 30 s when watched."""
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine('http://127.0.0.1:1', d)
            bus.KEY, bus.MODE = 'k', 'bulk'
            now = time.time()
            with bus.LOCK:
                bus.STATE.update(lastDemandAt=now - 3600, lastAttemptAt=now - 45, consecutiveErrors=0)
            bus.ALWAYS_ON = False
            self.assertEqual(bus.poll_decision(now)[0], False)          # old behaviour: nobody watching, no download
            bus.ALWAYS_ON = True
            self.assertEqual(bus.poll_decision(now)[0], False)          # unwatched: every 60 s, 45 s isn't due
            with bus.LOCK:
                bus.STATE['lastAttemptAt'] = now - 61
            self.assertEqual(bus.poll_decision(now)[0], True)
            with bus.LOCK:
                bus.STATE.update(lastDemandAt=now - 10, lastAttemptAt=now - 31)
            self.assertEqual(bus.poll_decision(now)[0], True)           # watched: every 30 s

    def test_test_address_must_be_local(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                self.engine('http://example.com', d)


if __name__ == '__main__':
    unittest.main()
