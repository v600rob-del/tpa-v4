"""Bus 0.19.1 (owner 13 Nov 2026 23:11, London: "the bus stops being in peoples houses"): stops are drawn on the
pavement of their nearest road, on the side the stop list says, instead of 15-30 m back among the houses."""
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os_roads_fixture as fx  # noqa: E402
from test_bus_roads import rm, save, ll  # noqa: E402
from test_bus_sharing_0_15_1 import fresh_engine  # noqa: E402


def metres(a, b):   # (lat, lon)
    return math.hypot((a[1] - b[1]) * math.cos(math.radians(a[0])), a[0] - b[0]) * 111195


class Kerb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        save(cls.dir.name + '/r.zip', fx.shapefile_zip(squares=('TQ', 'TL')))
        rm.build(cls.dir.name + '/r.zip', cls.dir.name + '/roads.sqlite')

    def setUp(self):
        self.m = fresh_engine()
        self.m.ROADS['map'] = self.m.road_map.RoadMap(self.dir.name + '/roads.sqlite')
        self.m.STOP_KERB.clear()

    def tearDown(self):
        self.m.ROADS['map'].close()

    def stop(self, E, N):
        lon, lat = ll(E, N)
        return lat, lon

    def test_a_stop_set_back_among_the_houses_moves_to_the_pavement(self):
        road = self.stop(529500, 180000)                      # High Street runs east-west along N 180000
        for side in (25, -25):
            here = self.stop(529500, 180000 + side)
            got = self.m.stop_on_kerb('S%d' % side, *here)
            self.assertAlmostEqual(metres(got, road), self.m.STOP_KERB_M, delta=1.5)
            self.assertEqual(got[0] > road[0], side > 0)       # still on its own side of the road
        self.assertEqual(self.m.STOP_KERB_STATS['moved'], 2)
        self.assertAlmostEqual(self.m.STOP_KERB_STATS['furthestM'], 19, delta=2)

    def test_close_stops_and_far_stops_are_left_alone(self):
        near = self.stop(529500, 180005)
        far = self.stop(529500, 180200)                       # nothing within 40 m (a bus station, a lay-by...)
        self.assertEqual(self.m.stop_on_kerb('N', *near), near)
        self.assertEqual(self.m.stop_on_kerb('F', *far), far)

    def test_worked_out_once_and_again_when_the_stop_moves(self):
        here = self.stop(529500, 180025)
        a = self.m.stop_on_kerb('X', *here)
        self.m.ROADS['map'] = None                             # no road map: the kept answer is still used
        self.assertEqual(self.m.stop_on_kerb('X', *here), a)
        moved = self.stop(529600, 180025)
        self.assertEqual(self.m.stop_on_kerb('X', *moved), moved)
        self.m.ROADS['map'] = self.m.road_map.RoadMap(self.dir.name + '/roads.sqlite')


class SharpRoads:
    """A pretend road map with a narrow-angled junction: no drive with ordinary turns, one with the sharp turn."""
    def __init__(self):
        self.asked = []

    def between(self, lon1, lat1, bearing, lon2, lat2, max_m=1500.0, snap_m=25.0, max_turn=110.0):
        self.asked.append((bearing, max_turn, max_m))
        if max_turn <= 110:
            return None
        return [[lon1, lat1], [lon1 + 0.0005, lat1 + 0.0009], [lon2, lat2]]


class SharpTurnLegs(unittest.TestCase):
    """Bus 0.19.1 (owner 13 Nov 23:16, a 380 in Charlton went "right across some houses"): a stop pair the ordinary
    drive cannot join (a sharp turn at a narrow junction) is tried again allowing it, before being joined straight."""
    def test_second_try_allows_the_sharp_turn(self):
        m = fresh_engine()
        m.ROADS['map'] = r = SharpRoads()
        a, b = ('K', -0.0300, 51.4800), ('L', -0.0290, 51.4790)
        got = m.stop_leg(a, b)
        self.assertEqual(len(got), 3)                          # along the roads, not straight across
        self.assertEqual(len(r.asked), 2)
        self.assertIsNone(r.asked[1][0])                       # faces whichever way the road goes
        self.assertGreater(r.asked[1][1], 150)
        self.assertGreater(r.asked[1][2], r.asked[0][2])        # and may go further round
        self.assertEqual(m.GEN['legsSharp'], 1)

    def test_kept_drives_are_worked_out_again(self):
        m = fresh_engine()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'legs.sqlite'
            old = m.LegStore(p, 'roads-a')
            old.db.execute("UPDATE meta SET v='roads-a' WHERE k='roads'"); old.db.commit()   # as 0.19.0 left it
            old.put('K>L', []); old.flush()
            old.db.execute("UPDATE meta SET v='roads-a' WHERE k='roads'"); old.db.commit()
            old.db.close()
            self.assertIsNone(m.LegStore(p, 'roads-a').get('K>L'))


if __name__ == '__main__':
    unittest.main()
