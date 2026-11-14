"""Bus 0.20.0 (owner 14 Nov 2026 00:34, option 1): each bus is shown just far enough behind its reports that there is
always a next report to glide towards (London's reports arrive ~80 s late, about one a minute: at a fixed 90 s buses
caught up, waited, and jumped when the next report came)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bus_sharing_0_15_1 import fresh_engine  # noqa: E402


class Delay(unittest.TestCase):
    def setUp(self):
        self.m = fresh_engine()

    def reports(self, key, gap, late, n=8, t0=1_000_000.0):
        self.m.HISTORY[key] = [(t0 + i * gap, 51.5, 0.0) for i in range(n)]
        self.m.LATE[key] = [late] * n

    def test_prompt_reports_stay_at_the_minimum(self):
        self.reports('A', gap=15, late=10)
        self.assertEqual(self.m.delay_target('A'), self.m.DELAY_MIN)

    def test_london_style_reports_are_shown_far_enough_behind(self):
        self.reports('L', gap=58, late=79)
        d = self.m.delay_target('L')
        self.assertGreaterEqual(d, 79 + 58 + 20)              # never catches up with its newest report
        self.assertLessEqual(d, self.m.DELAY_MAX)

    def test_never_more_than_the_maximum(self):
        self.reports('S', gap=200, late=300)
        self.assertEqual(self.m.delay_target('S'), self.m.DELAY_MAX)

    def test_a_new_bus_starts_at_its_target_then_changes_gently(self):
        self.reports('L', gap=58, late=79)
        first = self.m.step_delay('L')
        self.assertEqual(first, self.m.delay_target('L'))
        self.reports('L', gap=15, late=10)                    # its reports become prompt
        seen = [self.m.step_delay('L') for _ in range(5)]
        self.assertEqual(seen, [first - 1 - k for k in range(5)])   # a second per round, no lurch
        self.reports('L', gap=100, late=120)
        up = self.m.step_delay('L')
        self.assertEqual(up, seen[-1] + 3)

    def test_lateness_is_noted_only_for_new_reports(self):
        old = [{'key': 'A', 'recorded': 100.0}]
        new = [{'key': 'A', 'recorded': 100.0}, {'key': 'B', 'recorded': 150.0}]
        self.m.note_lateness(new, old, 200.0)
        self.assertNotIn('A', self.m.LATE)
        self.assertEqual(self.m.LATE['B'], [50.0])
        self.assertEqual(self.m.delay_of('B'), self.m.REPLAY_SECONDS)   # no delay worked out yet: the usual

    def test_tiles_keep_the_reports_the_replay_needs(self):
        now = 10_000.0
        fast = [(now - 400 + 10 * i, i) for i in range(40)]  # every 10 s
        kept = self.m.keep_recent(fast, now)
        self.assertLessEqual(kept[0][0], now - self.m.DELAY_MAX - 60)  # reaches back past the longest delay
        self.assertEqual(kept[-1], fast[-1])
        self.assertLessEqual(len(kept), 30)
        slow = [(now - 60 * (9 - i), i) for i in range(10)]   # once a minute
        kept = self.m.keep_recent(slow, now)
        self.assertGreaterEqual(len(kept), self.m.PUBLISH_KEEP)
        self.assertLessEqual(kept[0][0], now - self.m.DELAY_MAX - 60)

    def test_nothing_known_yet_is_provisional(self):
        """0.20.2: after a restart a bus waits at the minimum only until its lateness is known, then takes its delay."""
        self.m.HISTORY['N'] = [(1_000_000.0, 51.5, 0.0)]
        self.assertIsNone(self.m.delay_target('N'))
        self.assertEqual(self.m.step_delay('N'), self.m.DELAY_MIN)
        self.reports('N', gap=58, late=79)
        took = self.m.step_delay('N')
        self.assertEqual(took, self.m.delay_target('N'))                 # straight to it, once
        self.reports('N', gap=15, late=10)
        self.assertEqual(self.m.step_delay('N'), took - 1)                # then gently

    def test_summary(self):
        s = self.m.delay_summary([90, 90, 90, 180, 200])
        self.assertEqual(s['typical'], 90)
        self.assertEqual(s['max'], 200)
        self.assertEqual(s['atMinimumPct'], 60)


if __name__ == '__main__':
    unittest.main()
