"""Bus 0.21.0 (London no-match sample, 14 Nov 2026 00:30: 5 of 6 unmatched TfL buses said "started at stop X at hh:mm"
where no timetabled journey STARTS at X then, though X is known). Such a bus is matched to the journey of its route that
PASSES THROUGH X at that time - exact time first, then within 2 minutes, only when one journey fits; never guessed.
A timetable built before 0.21.0 (no route indexes) gets them when opened."""
import sqlite3
import tempfile
import unittest

from test_bus_timetable import FakeFiles, gtfs_zip, tt

MON = '2026-11-02T{}Z'          # a Monday, UK on GMT


class PassesThrough(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.files = FakeFiles(gtfs_zip())
        tt.RETRY_WAITS = (0.1, 0.1, 0.1)
        assert tt.fetch_and_build(cls.dir.name, cls.files.gtfs_url, cls.files.naptan_url, lambda **kw: None)
        cls.path = cls.dir.name + '/timetable.sqlite'
        cls.t = tt.Timetable(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.t.close()
        cls.files.server.shutdown()
        cls.dir.cleanup()

    def test_mid_route_start_is_matched_to_the_journey_passing_that_stop(self):
        # T2 (route 50) calls at Digbeth 4300C at 09:09; T1 (also 50) at 09:10; T3 (60) at 09:06
        self.assertEqual(self.t.match('4300C', MON.format('09:09:00'), '50'), ['T2'])
        self.assertEqual(self.t.match('4300C', MON.format('09:10:00'), '50'), ['T1'])
        self.assertEqual(self.t.match('4300C', MON.format('09:06:00'), '60'), ['T3'])

    def test_a_minute_or_two_out_only_when_one_fits(self):
        self.assertEqual(self.t.match('4300D', MON.format('09:15:30'), '50'), ['T2'])
        self.assertEqual(self.t.match('4300C', MON.format('09:09:30'), '50'), [])      # T1 and T2 both within 2 min
        self.assertEqual(self.t.match('4300C', MON.format('09:09:30'), '50', '4300D'), ['T2'])  # end stop decides
        self.assertEqual(self.t.match('4300D', MON.format('09:17:00'), '50'), [])      # too far out

    def test_needs_the_route_number_and_the_right_day(self):
        self.assertEqual(self.t.match('4300C', MON.format('09:09:00'), ''), [])
        self.assertEqual(self.t.match('4300C', MON.format('09:09:00'), '99'), [])
        self.assertEqual(self.t.match('4300C', '2026-11-07T09:09:00Z', '50'), [])     # Saturday: T2 doesn't run

    def test_starting_stop_matches_unchanged(self):
        self.assertEqual(self.t.match('4300A', MON.format('09:00:00'), '50', '4300D'), ['T2'])
        self.assertEqual(self.t.match('4300A', MON.format('09:00:00'), '50', ''), ['T1', 'T2'])

    def test_older_timetable_gets_the_route_indexes_when_opened(self):
        db = sqlite3.connect(self.path)
        db.execute('DROP INDEX routes_short'); db.execute('DROP INDEX trips_route'); db.commit(); db.close()
        t = tt.Timetable(self.path)
        try:
            names = {r[0] for r in t.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({'routes_short', 'trips_route'} <= names)
            self.assertEqual(t.match('4300C', MON.format('09:09:00'), '50'), ['T2'])
        finally:
            t.close()


if __name__ == '__main__':
    unittest.main()
