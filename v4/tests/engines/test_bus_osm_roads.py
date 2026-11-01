"""Bus engine 0.5.0 (owner, 31 Oct 2026 20:27: "the map detail doesnt match where the buses are
driving in a lot of cases"): buses follow OpenStreetMap's roads - the same data the map is drawn
from - read from Geofabrik's free road files. Ordnance Survey stays as the backup.

Pretend Geofabrik files (osm_roads_fixture.py) on 127.0.0.1. Checks: roads crossing part-way along,
one-way streets, a roundabout (straight-on exit), a bend where a service road goes straight on,
footpaths ignored, the old (0.4) map format still readable, and the engine's download and build.
"""
import importlib.util
import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import osm_roads_fixture as fx  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rm = load('road_map_osm_t', 'v4/engines/bus/road_map.py')


def metres(a, b):
    k = math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111195


def path_len(p):
    return sum(metres(p[i], p[i + 1]) for i in range(len(p) - 1))


def to_m(p):
    return ((p[0] - fx.ORIGIN[0]) * fx.K, (p[1] - fx.ORIGIN[1]) * 111195)


def dist_to_roads(pt, roads=fx.TOWN):
    best = 1e9
    for pts, fc, *_ in roads:
        if fc == 'footway':
            continue
        q = [fx.ll(*p) for p in pts]
        for a, b in zip(q, q[1:]):
            k = math.cos(math.radians(pt[1]))
            ax, ay, bx, by = (a[0] - pt[0]) * k, a[1] - pt[1], (b[0] - pt[0]) * k, b[1] - pt[1]
            vx, vy = bx - ax, by - ay
            f = max(0, min(1, -(ax * vx + ay * vy) / ((vx * vx + vy * vy) or 1e-18)))
            best = min(best, math.hypot(ax + vx * f, ay + vy * f) * 111195)
    return best


class OsmTown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        zp = cls.dir.name + '/gm.zip'
        with open(zp, 'wb') as f:
            f.write(fx.shapefile_zip())
        w = rm.TileWriter(cls.dir.name + '/roads-osm.sqlite')
        for pts, name, c, flags in rm.osm_shp_links(zp):
            w.add(pts, name, c, flags, simplify_m=0)
        cls.links = w.finish('test', '© OpenStreetMap contributors (ODbL)')
        cls.m = rm.RoadMap(cls.dir.name + '/roads-osm.sqlite')

    @classmethod
    def tearDownClass(cls):
        cls.m.close()
        cls.dir.cleanup()

    def walk(self, x, y, bearing, metres_):
        return self.m.ahead(*fx.ll(x, y), bearing, metres_)

    def on_roads(self, p):
        for pt in p:
            self.assertLess(dist_to_roads(pt), 2, to_m(pt))

    def test_straight_over_a_crossing_part_way_along(self):
        p = self.walk(-500, 0, 90, 900)
        self.on_roads(p)
        self.assertAlmostEqual(path_len(p), 900, delta=10)
        self.assertLess(metres(p[-1], fx.ll(400, 0)), 10)

    def test_turns_where_a_road_meets_part_way_along(self):
        p = self.walk(0, -600, 0, 900)          # up Cross Street, straight over Oxford Road
        self.on_roads(p)
        self.assertLess(metres(p[-1], fx.ll(0, 300)), 10)

    def test_one_way_street(self):
        self.assertIsNone(self.walk(500, 300, 0, 300))            # northbound up a southbound street: no
        p = self.walk(500, 450, 180, 600)                          # southbound: fine, down to Oxford Road ...
        self.on_roads(p)
        end = to_m(p[-1])
        self.assertLess(abs(end[1]) + abs(end[0] - 500), 3)        # ... where left/right is a toss-up: it waits there
        p = self.walk(200, 0, 90, 600)                             # along Oxford Road past it: never turns up it
        self.assertLess(abs(to_m(p[-1])[1]), 3)

    def test_roundabout_straight_on(self):
        p = self.walk(1500, 0, 90, 1000)
        self.on_roads(p)
        end = to_m(p[-1])
        self.assertGreater(end[0], 2200, f'left the roundabout the wrong way: ended at {end}')
        self.assertLess(abs(end[1]), 3)
        # UK: drive on the left, round clockwise - arriving from the west the bus turns left and goes
        # round the NORTH side, never the south (which would be against the one-way roundabout)
        self.assertTrue(any(to_m(q)[1] > 20 for q in p), 'did not go round the north side')
        self.assertFalse(any(to_m(q)[1] < -5 for q in p), 'went round the wrong way')

    def test_follows_main_road_round_a_bend_not_into_service_road(self):
        p = self.walk(5200, 0, 90, 600)
        self.on_roads(p)
        end = to_m(p[-1])
        self.assertGreater(end[1], 50, f'went into the service road: {end}')

    def test_footpaths_are_not_roads(self):
        self.assertIsNone(self.walk(7150, 0, 90, 100))

    def test_old_ordnance_survey_map_still_works(self):
        """The server's 0.4 map (format 1) keeps working until the OpenStreetMap one is built."""
        path = self.dir.name + '/old.sqlite'
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE tiles (k INTEGER, d BLOB)'); db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
        buf = {}
        for lid, (a, b) in enumerate((((-1000, 0), (0, 0)), ((0, 0), (1000, 0))), 1):
            us = [(round(x * 1e6), round(y * 1e6)) for x, y in (fx.ll(*a), fx.ll(*b))]
            rec = rm.REC.pack(lid, 7, 2, 2) + b''.join(struct.pack('<ii', *u) for u in us)
            for t in {rm.tile_of(*us[0]), rm.tile_of(*us[-1])}:
                buf.setdefault(t, bytearray()).extend(rec)
        db.executemany('INSERT INTO tiles VALUES (?,?)', ((rm.tile_key(*t), zlib.compress(bytes(b))) for t, b in buf.items()))
        db.executemany('INSERT INTO meta VALUES (?,?)', (('format', '1'), ('links', '2'), ('licence', '"Contains OS data"')))
        db.commit(); db.close()
        old = rm.RoadMap(path)
        p = old.ahead(*fx.ll(-500, 0), 90, 900)
        self.assertLess(metres(p[-1], fx.ll(400, 0)), 10)
        old.close()

    def test_geofabrik_list_uses_sub_regions_when_needed(self):
        got = rm.geofabrik_downloads(fx.geofabrik_index('https://download.geofabrik.de/europe'))
        self.assertEqual([r for r, _ in got], ['greater-manchester', 'west-yorkshire', 'scotland', 'wales'])
        with self.assertRaises(ValueError):
            rm.geofabrik_downloads(fx.geofabrik_index('https://evil.example.com'))


class FakeGeofabrik:
    """Pretend download.geofabrik.de. Like the real server it answers range requests (partial
    downloads) unless ranges=False. England is one big file (as on the real server)."""
    def __init__(self, missing=(), ranges=True, zip64=False, busy=0, cut=0):
        self.hits, self.missing, self.ranges, self.sent = [], set(missing), ranges, 0
        self.busy, self.cut = busy, cut          # answer 503 this many times; cut this many big transfers half-way
        self.payload = fx.shapefile_zip(buildings_mb=3, zip64=zip64)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if self.path == '/index-v1-nogeom.json':
                    body = json.dumps(fx.geofabrik_index(outer.base)).encode()
                elif self.path.endswith('-free.shp.zip') and outer.busy > 0:
                    outer.busy -= 1
                    self.send_response(503); self.send_header('Content-Length', '0'); self.end_headers(); return
                elif self.path.endswith('-free.shp.zip') and not any(m in self.path for m in outer.missing):
                    body = outer.payload
                    rng = self.headers.get('Range', '')
                    if outer.ranges and rng.startswith('bytes='):
                        a, _, b = rng[6:].partition('-')
                        start, end = (len(body) - int(b), len(body) - 1) if a == '' else (int(a), int(b) if b else len(body) - 1)
                        start = max(0, start); part = body[start:end + 1]
                        if outer.cut > 0 and len(part) > 2000:      # the connection drops half-way
                            outer.cut -= 1
                            self.send_response(206)
                            self.send_header('Content-Range', f'bytes {start}-{start + len(part) - 1}/{len(body)}')
                            self.send_header('Content-Length', str(len(part))); self.end_headers()
                            half = part[:len(part) // 2]; outer.sent += len(half); self.wfile.write(half)
                            self.wfile.flush(); self.connection.shutdown(2); return
                        self.send_response(206)
                        self.send_header('Content-Range', f'bytes {start}-{start + len(part) - 1}/{len(body)}')
                        self.send_header('Content-Length', str(len(part))); self.end_headers()
                        outer.sent += len(part); self.wfile.write(part); return
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
                outer.sent += len(body); self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class Engine(unittest.TestCase):
    def engine(self, index, data_dir):
        old = dict(os.environ)
        os.environ.update(TPA_BUS_ROAD_MAP='download', TPA_BUS_ROAD_SOURCE='osm', TPA_BUS_DATA_DIR=data_dir,
                          TPA_BUS_OSM_TEST_INDEX=index)
        try:
            return load('bus_osm_e', 'v4/engines/bus/bus_engine.py')
        finally:
            os.environ.clear(); os.environ.update(old)

    def test_builds_from_geofabrik_and_buses_follow_it(self):
        fake = FakeGeofabrik()
        with tempfile.TemporaryDirectory() as d:
            bus = self.engine(fake.base + '/index-v1-nogeom.json', d)
            self.assertTrue(bus.refresh_road_map('fetch-osm'))
            self.assertEqual(sorted({p for p in fake.hits if p.endswith('.zip')}),
                             ['/greater-manchester-latest-free.shp.zip', '/scotland-latest-free.shp.zip',
                              '/wales-latest-free.shp.zip', '/west-yorkshire-latest-free.shp.zip'])
            self.assertEqual(sorted(os.listdir(d)), ['roads-osm.sqlite'])          # downloads not kept
            self.assertTrue(bus.open_road_map())
            self.assertEqual(bus.ROADS['source'], 'OpenStreetMap')
            t0 = time.time() - 30
            a, b = fx.ll(-600, 0), fx.ll(-450, 0)
            old = [{'key': 'G:1', 'lat': a[1], 'lon': a[0], 'bearing': 90.0, 'recorded': t0 - 15}]
            new = [{'key': 'G:1', 'lat': b[1], 'lon': b[0], 'bearing': 90.0, 'recorded': t0, 'route': '50', 'destination': 'Town'}]
            bus.add_motion(new, old)
            with bus.LOCK:
                bus.STATE.update(vehicles=new, fetchedAt=time.time(), coverage=bus.GB)
            frame = bus.snapshot((b[1] - 0.02, b[0] - 0.03, b[1] + 0.02, b[0] + 0.03))
            m = frame['entities'][0]['motion']
            self.assertEqual(m['mode'], 'path')
            for pt in m['points']:
                self.assertLess(dist_to_roads(pt), 2.6)   # 0.5.7: 2 m to the left of the line (keep left)
            self.assertIn('OpenStreetMap', frame['attribution'])
            # next day: still fresh, nothing downloaded again
            fake.hits.clear()
            self.assertTrue(bus.refresh_road_map('fetch-osm'))
            self.assertEqual(fake.hits, [])
            bus.ROADS['map'].close()
        fake.close()

    def test_only_the_roads_part_is_downloaded(self):
        """Server 31 Oct 20:59: the England file was over the 3 GB limit. Only the roads layer is
        fetched now (partial downloads), including from the >4 GB zip format; a server without
        partial downloads still works (whole file)."""
        for ranges, zip64 in ((True, False), (True, True), (False, False)):
            fake = FakeGeofabrik(ranges=ranges, zip64=zip64)
            with tempfile.TemporaryDirectory() as d:
                bus = self.engine(fake.base + '/index-v1-nogeom.json', d)
                self.assertTrue(bus.refresh_road_map('fetch-osm'), (ranges, zip64, bus.STATE['roadMapError']))
                per_file = fake.sent / 4
                if ranges:
                    self.assertLess(per_file, len(fake.payload) / 10, f'downloaded too much: {per_file:.0f} of {len(fake.payload)}')
                else:
                    self.assertGreaterEqual(per_file, len(fake.payload))
                self.assertEqual(sorted(os.listdir(d)), ['roads-osm.sqlite'])
                m = rm.RoadMap(d + '/roads-osm.sqlite')
                p = m.ahead(*fx.ll(-500, 0), 90, 900)
                self.assertLess(metres(p[-1], fx.ll(400, 0)), 10)
                m.close()
            fake.close()

    def test_busy_server_and_broken_connection(self):
        """Server 31 Oct 21:27: Geofabrik answered 503 part-way and the 47% done was thrown away.
        Now: wait and try again, and carry on from where it stopped (nothing downloaded twice)."""
        fake = FakeGeofabrik(busy=2, cut=2)
        real = rm.RETRY_WAITS, rm.PACE_SECONDS
        rm.RETRY_WAITS, rm.PACE_SECONDS = (0.05, 0.05, 0.05, 0.05), 0
        try:
            with tempfile.TemporaryDirectory() as d:
                seen = []
                self.assertTrue(rm.fetch_osm_and_build(d, fake.base + '/index-v1-nogeom.json', status=lambda **kw: seen.append(kw)))
                self.assertEqual((fake.busy, fake.cut), (0, 0))           # both troubles really happened
                self.assertLess(fake.sent / 4, len(fake.payload) / 10)    # resumed, not restarted
                self.assertEqual(sorted(os.listdir(d)), ['roads-osm.sqlite'])
                self.assertTrue(any(k.get('state') == 'downloading' and 'MB' in k.get('mb', '') for k in seen))
        finally:
            rm.RETRY_WAITS, rm.PACE_SECONDS = real
            fake.close()
        # a server that keeps refusing: gives up in the end with a clear message
        fake = FakeGeofabrik(busy=99)
        rm.RETRY_WAITS, rm.PACE_SECONDS = (0.01, 0.01), 0
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(Exception) as ctx:
                    rm.fetch_osm_and_build(d, fake.base + '/index-v1-nogeom.json')
                self.assertIn('503', str(ctx.exception))
                self.assertEqual(os.listdir(d), [])
        finally:
            rm.RETRY_WAITS, rm.PACE_SECONDS = real
            fake.close()

    def test_failed_download_keeps_the_backup_map(self):
        fake = FakeGeofabrik(missing=('wales',))
        with tempfile.TemporaryDirectory() as d:
            # the Ordnance Survey backup is already there
            import os_roads_fixture as ofx
            with open(d + '/os.zip', 'wb') as f:
                f.write(ofx.shapefile_zip())
            rm.build(d + '/os.zip', d + '/roads.sqlite'); os.remove(d + '/os.zip')
            bus = self.engine(fake.base + '/index-v1-nogeom.json', d)
            self.assertFalse(bus.refresh_road_map('fetch-osm'))
            self.assertIn('HTTPError', bus.STATE['roadMapError'])
            self.assertEqual(sorted(os.listdir(d)), ['roads.sqlite'])               # nothing half-built left
            self.assertTrue(bus.open_road_map())
            self.assertEqual(bus.ROADS['source'], 'Ordnance Survey')
            bus.ROADS['map'].close()
        fake.close()


if __name__ == '__main__':
    unittest.main()
