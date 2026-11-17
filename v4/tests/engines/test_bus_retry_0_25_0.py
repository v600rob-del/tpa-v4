"""Bus 0.25.0 (17 Nov 2026, 19:20-19:30: BODS was slow to answer and seven downloads gave up after 60 s; the engine
then waited up to 150 s between tries, so buses stayed old for over six minutes. tpa-bods-check at 19:36 showed the
server's own connection to BODS was fine). After failures the engine now tries again within 30 s while people are
watching, so the map catches up as soon as BODS answers again. It still slows down a little after failures, and
still waits the normal time after a good download."""
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Retry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        old = dict(os.environ)
        os.environ.update(TPA_BUS_DATA_DIR=self.tmp.name, TPA_BUS_REPLAY_SECONDS='0')
        try:
            self.bus = load('bus_retry_0250', 'v4/engines/bus/bus_engine.py')
        finally:
            os.environ.clear(); os.environ.update(old)
        self.bus.KEY, self.bus.MODE, self.bus.ALWAYS_ON = 'k', 'bulk', True

    def tearDown(self):
        self.tmp.cleanup()

    def due_after(self, errors, watched=True):
        """The shortest gap (whole seconds) after the last try before the engine tries BODS again."""
        bus, now = self.bus, time.time()
        for gap in range(0, 400):
            with bus.LOCK:
                bus.STATE.update(lastDemandAt=now - (5 if watched else 3600), lastAttemptAt=now - gap,
                                 consecutiveErrors=errors)
            if bus.poll_decision(now)[0]:
                return gap
        return None

    def test_version(self):
        self.assertEqual(self.bus.ENGINE_VERSION, '0.25.0')

    def test_normal_pace_unchanged(self):
        self.assertEqual(self.due_after(0), self.bus.POLL_SECONDS)
        self.assertEqual(self.due_after(0, watched=False), self.bus.IDLE_POLL_SECONDS)

    def test_watched_retries_within_30_seconds_however_many_failures(self):
        for errors in (1, 2, 3, 5, 7, 20):
            self.assertLessEqual(self.due_after(errors), 30, f'{errors} failures in a row')

    def test_still_eases_off_a_little_after_failures(self):
        self.assertGreater(self.due_after(3), self.due_after(1))

    def test_nobody_watching_eases_off_no_more_than_two_minutes(self):
        self.assertLessEqual(self.due_after(20, watched=False), 120)


if __name__ == '__main__':
    unittest.main()
