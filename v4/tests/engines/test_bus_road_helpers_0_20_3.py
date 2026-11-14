"""Bus 0.20.3 - fix C (owner 14 Nov 2026 11:25 "Lets get it done"): the drives along the roads for buses not on a
timetable route (36 of every 60 processor-seconds in the daytime, server 11:14) are worked out by helper programs, each
on its own processor. Checked: the published tiles are EXACTLY the same with and without helpers; a helper that dies
or hangs changes nothing on the map (the engine does that work itself) and is restarted; switched off with
TPA_BUS_ROAD_HELPERS=0; the helpers use the same leg code as the engine."""
import json
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import osm_roads_fixture as fx  # noqa: E402
from test_bus_replay import rm as road_map, town_map  # noqa: E402
from test_bus_sharing_0_15_1 import fresh_engine  # noqa: E402

# straight roads of the pretend town (metres east/north of its origin) that buses drive along
ROUTES = [((-900, 0), (1900, 0)), ((1900, 0), (-900, 0)), ((0, -700), (0, 700)), ((2000, 100), (2000, 950)),
          ((2100, 0), (2950, 0))]


class NoMatchTT:
    meta = {'builtAt': 1794600000}

    def match(self, *a):
        return []                         # no bus is on a timetable route: every one drives along the roads

    def shape_id_of(self, trip):
        return ''


def setup_engine(tiles_dir, road_path, helpers, n_buses, now, seed=3):
    os.environ['TPA_BUS_TILES_DIR'] = tiles_dir
    os.environ['TPA_BUS_ROAD_HELPERS'] = str(helpers)
    try:
        m = fresh_engine()
    finally:
        del os.environ['TPA_BUS_TILES_DIR'], os.environ['TPA_BUS_ROAD_HELPERS']
    m.TIMETABLE['tt'] = NoMatchTT()
    m.ROADS['map'] = road_map.RoadMap(road_path)
    m.ROADS['path'] = road_path
    rnd = random.Random(seed)
    vs = []
    for i in range(n_buses):
        (x0, y0), (x1, y1) = ROUTES[i % len(ROUTES)]
        f0 = rnd.random() * 0.4
        hist = []
        for k in range(5):                                       # a report about every 50 s, ~150 m apart
            f = min(1.0, f0 + k * 0.12)
            lon, lat = fx.ll(x0 + (x1 - x0) * f + rnd.uniform(-6, 6), y0 + (y1 - y0) * f + rnd.uniform(-6, 6))
            hist.append((now - 210 + k * 50, lat, lon))
        t, lat, lon = hist[-1]
        vs.append({'key': f'OP:{i}', 'route': str(i % 7), 'destination': 'Town', 'lat': lat, 'lon': lon, 'bearing': None,
                   'recorded': t, 'speed': 5.0, 'origin': 'A', 'dep': '2026-11-14T08:00:00', 'dest': 'B', 'jref': ''})
        m.HISTORY[f'OP:{i}'] = hist
    m.STATE['vehicles'] = vs
    m.STATE['fetchedAt'] = now
    return m, now


def tiles(d):
    out = {}
    for f in sorted((Path(d) / 'bus').glob('[sb]/*.json')):
        out[f.parent.name + '/' + f.name] = sorted(json.dumps(it, sort_keys=True) for it in json.loads(f.read_text()))
    return out


class RoadHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.now = int(time.time())                               # every run in this class: the same reports
        cls.dir = tempfile.TemporaryDirectory()
        cls.road = cls.dir.name + '/roads-osm.sqlite'
        town_map(cls.road)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def run_round(self, helpers, n=120, break_helpers=None):
        d = tempfile.mkdtemp(dir=self.dir.name)
        m, t = setup_engine(d, self.road, helpers, n, self.now)
        self.addCleanup(lambda: m.HELPERS['pool'] and m.HELPERS['pool'].close())
        self.addCleanup(m.ROADS['map'].close)
        if break_helpers:
            pool = m.road_helpers_for_round()
            break_helpers(pool)
        r = m.publish_round()
        return m, r, tiles(d)

    def test_tiles_exactly_the_same_with_and_without_helpers(self):
        _, r0, without = self.run_round(0)
        m, r2, with_ = self.run_round(2)
        self.assertGreater(len(without), 3)
        self.assertEqual(with_, without)
        self.assertEqual(r0['helperBuses'], 0)
        self.assertIsNone(r0['roadHelpers'])
        self.assertEqual(r2['helperBuses'], 120)                  # every bus needed drives, worked out by the helpers
        self.assertEqual(r2['roadHelpers']['legsDone'], r2['helperLegs'])
        self.assertGreaterEqual(r2['helperLegs'], 120 * 4)
        self.assertEqual(r2['buses'], 120)
        items = [json.loads(x) for v in with_.values() for x in v]
        self.assertTrue(any('w' in it for it in items))           # really driven along the roads

    def test_second_round_needs_no_helper_work(self):
        m, r1, _ = self.run_round(2, n=40)
        r2 = m.publish_round()
        self.assertEqual(r2['helperLegs'], 0)                     # drives kept, as before

    def test_a_dead_helper_changes_nothing_on_the_map_and_is_restarted(self):
        _, _, without = self.run_round(0)

        def kill_one(pool):
            pool._alive(0).kill(); pool.procs[0].wait()
        m, r, with_ = self.run_round(2, break_helpers=kill_one)
        self.assertEqual(with_, without)
        self.assertGreaterEqual(m.HELPERS['pool'].stats['restarts'], 1)
        self.assertGreater(r['roadHelpers']['legsDone'], 0)

    def test_a_stuck_helper_is_stopped_and_its_share_done_by_the_engine(self):
        _, _, without = self.run_round(0)

        def stall(pool):
            import subprocess
            pool.procs[1] = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE)
        d = tempfile.mkdtemp(dir=self.dir.name)
        m, t = setup_engine(d, self.road, 2, 120, self.now)
        self.addCleanup(lambda: m.HELPERS['pool'] and m.HELPERS['pool'].close())
        self.addCleanup(m.ROADS['map'].close)
        stall(m.road_helpers_for_round())
        jobs_timeout = m.road_helpers.RoadHelpers.legs.__defaults__
        m.road_helpers.RoadHelpers.legs.__defaults__ = (2.0,)
        try:
            r = m.publish_round()
        finally:
            m.road_helpers.RoadHelpers.legs.__defaults__ = jobs_timeout
        self.assertEqual(tiles(d), without)
        self.assertEqual(r['roadHelpers']['error'], 'helper too slow')
        self.assertLess(r['roadHelpers']['legsDone'], r['helperLegs'])

    def test_helpers_and_engine_use_the_same_leg_code(self):
        m, _, _ = self.run_round(0, n=1)
        rmap = m.ROADS['map']
        a = (0, *reversed(fx.ll(-500, 3)))
        b = (50, *reversed(fx.ll(-100, -2)))
        here = m._leg({}, a, b, None)
        pool = m.road_helpers.RoadHelpers(self.road, 1)
        try:
            there = m.way_of(pool.legs([(0, a, b, None)])[0])
        finally:
            pool.close()
        self.assertEqual(there, here)
        self.assertGreater(len(here), 1)
        self.assertIs(rmap, m.ROADS['map'])


if __name__ == '__main__':
    unittest.main()
