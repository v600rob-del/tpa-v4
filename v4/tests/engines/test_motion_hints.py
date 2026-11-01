"""Motion hints (air 0.2.0, bus 0.3.0, rail 0.3.0): engines tell HQ how a vehicle is
moving so the map can glide it between real reports, labelled "estimated".
Pure checks of the engines' own functions; no network, no keys.
"""
import importlib.util
import os
import time
import unittest
from datetime import datetime
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]


def load(name, rel):
    for k in [k for k in os.environ if k.startswith('TPA_')]:
        os.environ.pop(k)
    spec = importlib.util.spec_from_file_location(name, PACKAGE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ts(iso):
    return datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp()


YORK_VIEW = (53.70, -1.30, 54.05, -0.20)   # south, west, north, east


class Air(unittest.TestCase):
    def test_flying_aircraft_get_speed_and_heading_ground_ones_do_not(self):
        air = load('air_m', 'v4/engines/air/air_engine.py')
        now = time.time()
        rows = air.parse({'ac': [
            {'hex': '4ca1b2', 'flight': 'RYR1', 'alt_baro': 36000, 'track': 135.5, 'gs': 450, 'lat': 53.85, 'lon': -0.42, 'seen_pos': 1},
            {'hex': '400abc', 'flight': 'EZY9', 'alt_baro': 'ground', 'track': 90, 'gs': 12, 'lat': 53.83, 'lon': -0.50, 'seen_pos': 1},
            {'hex': '406f01', 'flight': 'GHUL', 'alt_baro': 2500, 'track': 270, 'lat': 53.80, 'lon': -0.30, 'seen_pos': 1},      # no speed
            {'hex': '406f02', 'flight': 'BAD1', 'alt_baro': 2500, 'track': 270, 'gs': 'fast', 'lat': 53.81, 'lon': -0.31, 'seen_pos': 1},
        ]}, now)
        with air.LOCK:
            air.STATE.update(aircraft=rows, fetchedAt=now, circle=(53.85, -0.75, 60))
        frame = air.snapshot(YORK_VIEW)
        by = {e['id']: e for e in frame['entities']}
        self.assertEqual(by['air:4ca1b2']['motion'], {'mode': 'heading', 'speedMps': 231.5, 'maxSeconds': 60})
        self.assertNotIn('motion', by['air:400abc'])      # on the ground
        self.assertNotIn('motion', by['air:406f01'])      # no speed reported
        self.assertNotIn('motion', by['air:406f02'])      # nonsense speed ignored
        self.assertEqual(frame['nextPollAfterMs'], 10000)


class Bus(unittest.TestCase):
    def test_speed_from_the_same_bus_last_two_reports(self):
        bus = load('bus_m', 'v4/engines/bus/bus_engine.py')
        t0 = time.time() - 40
        old = [{'key': 'EYMS:1', 'lat': 53.8420, 'lon': -0.4300, 'bearing': 90.0, 'recorded': t0},
               {'key': 'EYMS:2', 'lat': 53.8500, 'lon': -0.4100, 'bearing': None, 'recorded': t0},
               {'key': 'EYMS:3', 'lat': 53.8600, 'lon': -0.4500, 'bearing': 0.0, 'recorded': t0},
               {'key': 'EYMS:4', 'lat': 53.8000, 'lon': -0.4000, 'bearing': 0.0, 'recorded': t0}]
        new = [{'key': 'EYMS:1', 'lat': 53.8420, 'lon': -0.4240, 'bearing': 90.0, 'recorded': t0 + 30},   # ~395 m in 30 s
               {'key': 'EYMS:2', 'lat': 53.8530, 'lon': -0.4100, 'bearing': None, 'recorded': t0 + 30},   # north, no bearing given
               {'key': 'EYMS:3', 'lat': 53.8600, 'lon': -0.4500, 'bearing': 0.0, 'recorded': t0 + 30},    # standing still
               {'key': 'EYMS:4', 'lat': 53.9000, 'lon': -0.4000, 'bearing': 0.0, 'recorded': t0 + 30},    # 11 km in 30 s: nonsense
               {'key': 'EYMS:5', 'lat': 53.8450, 'lon': -0.4200, 'bearing': 45.0, 'recorded': t0 + 30}]   # first sighting
        bus.add_motion(new, old)
        by = {v['key']: v for v in new}
        self.assertAlmostEqual(by['EYMS:1']['speed'], 13.2, delta=0.3)
        self.assertAlmostEqual(by['EYMS:2']['bearing'], 0.0, delta=1)
        self.assertGreater(by['EYMS:2']['speed'], 10)
        for k in ('EYMS:3', 'EYMS:4', 'EYMS:5'):
            self.assertNotIn('speed', by[k], k)
        # The same report downloaded again keeps its speed.
        again = [dict(by['EYMS:1'], speed=None)]
        again[0].pop('speed')
        bus.add_motion(again, new)
        self.assertEqual(again[0]['speed'], by['EYMS:1']['speed'])
        for v in new:
            v.update(route='121', destination='Hull')
        with bus.LOCK:
            bus.STATE.update(vehicles=new, fetchedAt=time.time(), coverage=bus.GB)
        frame = bus.snapshot((53.80, -0.50, 53.90, -0.35))
        ents = {e['id']: e for e in frame['entities']}
        self.assertEqual(ents['bus:EYMS:1']['motion']['mode'], 'heading')
        self.assertEqual(ents['bus:EYMS:1']['motion']['maxSeconds'], 90)
        self.assertIs(ents['bus:EYMS:1']['motion']['slowToStop'], True)
        self.assertNotIn('motion', ents['bus:EYMS:5'])
        self.assertEqual(frame['nextPollAfterMs'], 15000)


class TrackRoutes(unittest.TestCase):
    """The real bundled track map (Network Rail track model, OGL)."""
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('router_m', PACKAGE / 'v4/engines/rail/track_router.py')
        cls.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.m)
        cls.r = cls.m.TrackRouter()

    def length(self, p):
        return sum(self.m.metres(*p[i], *p[i + 1]) for i in range(len(p) - 1))

    def test_real_journeys_follow_the_track(self):
        for (a, b, lo, hi) in (((-0.43386, 53.95586), (-0.4237, 53.8423), 12.6, 15),    # Hutton Cranswick -> Beverley (owner's example)
                               ((-0.4237, 53.8423), (-0.3457, 53.744), 12.1, 15),       # Beverley -> Hull
                               ((-0.1233, 51.5320), (-0.2502, 52.5748), 116, 130)):     # London King's Cross -> Peterborough
            p = self.r.route(a, b)
            self.assertIsNotNone(p, (a, b))
            self.assertEqual(tuple(p[0]), (round(a[0], 5), round(a[1], 5)))
            self.assertEqual(tuple(p[-1]), (round(b[0], 5), round(b[1], 5)))
            self.assertLessEqual(len(p), 160)
            km = self.length(p) / 1000
            self.assertTrue(lo <= km <= hi, (a, b, km))

    def test_route_hugs_the_line(self):
        # Every point of the Hutton Cranswick -> Beverley route is on (within 60 m of) the track.
        p = self.r.route((-0.43386, 53.95586), (-0.4237, 53.8423))
        for pt in p[1:-1]:
            k, _, near = self.r.snap(*pt)
            self.assertLess(self.m.metres(*pt, *near), 60, pt)

    def test_no_track_nearby_means_no_route(self):
        self.assertIsNone(self.r.route((-0.43386, 53.95586), (-4.5, 50.0)))      # sea off Cornwall


class Rail(unittest.TestCase):
    def setUp(self):
        self.rail = load('rail_m', 'v4/engines/rail/rail_engine.py')
        with self.rail.LOCK:
            self.rail.STATE['stations'] = {'16000': (53.95799, -1.09312, 'York'), '15100': (53.744, -0.3457, 'Hull Paragon'),
                                           '15200': (53.8423, -0.4237, 'Beverley')}
            self.rail.STATE['points'] = {'15500': (53.73303, -0.38189, 'Hessle Road Jn.'), '00705': (55.0, -3.0, 'Scottish Jn')}

    def move(self, tid, stanox, event, nxt=None, run=None, ago=1.0, terminated='false'):
        body = {'train_id': tid, 'loc_stanox': stanox, 'event_type': event, 'variation_status': 'ON TIME',
                'timetable_variation': '0', 'actual_timestamp': str(int((time.time() - ago * 60) * 1000)),
                'train_terminated': terminated}
        if nxt is not None:
            body['next_report_stanox'] = nxt
        if run is not None:
            body['next_report_run_time'] = run
        self.rail.handle_message({'header': {'msg_type': '0003'}, 'body': body}, time.time())

    def test_departing_train_glides_to_its_next_report_point(self):
        r = self.rail
        self.move('161A23MZ30', '16000', 'DEPARTURE', '15200', '35')            # York -> Beverley, 35 min
        self.move('152H45MZ30', '15100', 'ARRIVAL', '15500', '3')               # standing at Hull: no glide
        self.move('152H46MZ30', '15200', 'DEPARTURE', '99999', '5')             # next place unknown: no glide
        self.move('152H47MZ30', '15200', 'DEPARTURE', '15100', '0')             # no run time: no glide
        self.move('152H48MZ30', '15100', 'DEPARTURE', '15500', '4', terminated='true')
        self.move('152H49MZ30', '15200', 'DEPARTURE', '15200', '5')             # "next" is the same place
        # 0.4.0: no route worked out yet -> the train waits (no straight-line short cut) and the leg is queued.
        with r.LOCK:
            r.STATE['trackLoaded'] = True
        frame = r.snapshot(YORK_VIEW)
        by = {e['id']: e for e in frame['entities']}
        self.assertNotIn('motion', by['rail:161A23MZ30'])
        key = r.leg_key(r.STATE['trains']['161A23MZ30'])
        self.assertIn(key, r.PENDING_SET)
        # Once the route along the track is known, the train follows it.
        path = [[-1.09312, 53.95799], [-0.9, 53.9], [-0.4237, 53.8423]]
        with r.LOCK:
            r.ROUTES[key] = path
        frame = r.snapshot(YORK_VIEW)
        by = {e['id']: e for e in frame['entities']}
        m = by['rail:161A23MZ30']['motion']
        self.assertEqual((m['mode'], m['points'], m['maxSeconds']), ('path', path, 35 * 60 + 120))
        self.assertAlmostEqual(ts(m['arriveAt']) - ts(by['rail:161A23MZ30']['observedAt']), 35 * 60, delta=1)
        # No route possible: stays put.
        with r.LOCK:
            r.ROUTES[key] = False
        self.assertNotIn('motion', {e['id']: e for e in r.snapshot(YORK_VIEW)['entities']}['rail:161A23MZ30'])
        self.assertEqual(by['rail:161A23MZ30']['label'], 'Train 1A23 · departed York · on time · next: Beverley')
        for tid in ('152H45MZ30', '152H46MZ30', '152H47MZ30', '152H49MZ30'):
            self.assertNotIn('motion', by['rail:' + tid], tid)
        self.assertNotIn('next:', by['rail:152H45MZ30']['label'])
        self.assertEqual(frame['nextPollAfterMs'], 15000)
        self.assertIn('Network Rail Infrastructure Limited', frame['attribution'])

    def test_finished_cancelled_and_incoming_trains_do_not_vanish(self):
        r = self.rail
        self.move('152H48MZ30', '15100', 'ARRIVAL', ago=12, terminated='true')      # finished 12 min ago: still there
        self.move('152H50MZ30', '15100', 'ARRIVAL', ago=22, terminated='true')      # 22 min: gone
        self.move('161A23MZ30', '16000', 'DEPARTURE', '15200', '35')                # York -> Beverley
        r.handle_message({'header': {'msg_type': '0002'}, 'body': {'train_id': '161A23MZ30'}}, time.time())
        by = {e['id']: e for e in r.snapshot(YORK_VIEW)['entities']}
        self.assertIn('rail:152H48MZ30', by)
        self.assertNotIn('rail:152H50MZ30', by)
        c = by['rail:161A23MZ30']
        self.assertEqual((c['quality'], 'motion' in c), ('stale', False))
        self.assertIn('cancelled · last at York', c['label'])
        self.assertLess(time.time() - ts(c['observedAt']), 120)          # real report time kept (1 min ago)
        self.assertGreater(time.time() - ts(c['observedAt']), 30)
        r.handle_message({'header': {'msg_type': '0005'}, 'body': {'train_id': '161A23MZ30'}}, time.time())
        self.assertNotIn('cancelled', {e['id']: e for e in r.snapshot(YORK_VIEW)['entities']}['rail:161A23MZ30']['label'])
        # A train outside the view but heading for a place inside it is sent too.
        self.move('159Z11MZ30', '16000', 'DEPARTURE', '15200', '35')
        beverley_only = (53.80, -0.50, 53.88, -0.35)
        ids = {e['id'] for e in r.snapshot(beverley_only)['entities']}
        self.assertIn('rail:159Z11MZ30', ids)

    def test_codes_without_leading_zero_still_match(self):
        r = self.rail
        self.move('161A23MZ30', '705', 'ARRIVAL')        # feed or list without the leading zeros
        self.assertEqual(r.STATE['trains']['161A23MZ30']['station'], 'Scottish Jn')

    def test_memory_keeps_motion_and_rejects_nonsense(self):
        r = self.rail
        import json
        import tempfile
        d = Path(tempfile.mkdtemp())
        r.MEMORY_FILE = d / 'm.json'
        now = time.time()
        good = {'lat': 53.95799, 'lon': -1.09312, 'station': 'York', 'when': now - 60, 'event': 'DEPARTURE', 'variation': '',
                'minutes': 0, 'headcode': '1A23', 'terminated': False, 'point': False,
                'nlat': 53.8423, 'nlon': -0.4237, 'nname': 'Beverley', 'run': 35}
        bad = dict(good, nlat=99.0, run=500)
        old = {k: v for k, v in good.items() if k not in ('nlat', 'nlon', 'nname', 'run')}   # file from 0.2.x
        r.MEMORY_FILE.write_text(json.dumps({'trains': {'161A23MZ30': good, '161A24MZ30': bad, '161A25MZ30': old}}))
        with r.LOCK:
            r.STATE['trains'] = {}
        self.assertEqual(r.restore_memory(), 3)
        t = r.STATE['trains']
        self.assertEqual((t['161A23MZ30']['run'], t['161A23MZ30']['nname']), (35, 'Beverley'))
        self.assertEqual((t['161A24MZ30']['run'], t['161A24MZ30']['nlat']), (0, 0.0))
        self.assertEqual(t['161A25MZ30']['run'], 0)


if __name__ == '__main__':
    unittest.main()
