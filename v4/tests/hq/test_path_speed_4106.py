"""HQ 4.1.6: a "path" with speedMps (+ slowToStop) is travelled at that speed, easing to a stop,
instead of being spread evenly until arriveAt. Bus engine 0.4.0 uses it to glide buses along
the real road ahead. Real Chromium, pretend engine (test_motion_4100's FakeEngine).

The pretend bus does 300 m/s for 2 s then eases to a stop over 1 s: it must stop 750 m along
its 6.6 km road (where a still marker REFMID sits), not run to the end of the road (REFEND).
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402

LAT = 53.90
METRES_TO_STOP = 300 * (2 + 0.5)
MID_LON = -0.60 + METRES_TO_STOP / (111195 * 0.5892)   # cos(53.9 deg) = 0.5892


class RoadEngine(base.FakeEngine):
    def frame(self):
        now = time.time()
        if not self.requests[:-1]:
            self.t0 = now
        road = {'id': 'air:road', 'label': 'ROAD', 'quality': 'observed', 'observedAt': base.iso(self.t0),
                'position': {'lon': -0.60, 'lat': LAT, 'headingDeg': 90.0},
                'motion': {'mode': 'path', 'points': [[-0.60, LAT], [-0.55, LAT], [-0.50, LAT]],
                           'arriveAt': base.iso(self.t0 + 3), 'maxSeconds': 3, 'speedMps': 300, 'slowToStop': True}}
        plain = {'id': 'air:plain', 'label': 'PLAIN', 'quality': 'observed', 'observedAt': base.iso(self.t0),
                 'position': {'lon': -0.60, 'lat': LAT - 0.02, 'headingDeg': None},
                 'motion': {'mode': 'path', 'points': [[-0.60, LAT - 0.02], [-0.50, LAT - 0.02]],
                            'arriveAt': base.iso(self.t0 + 3), 'maxSeconds': 3}}
        mid = {'id': 'air:mid', 'label': 'REFMID', 'quality': 'observed', 'observedAt': base.iso(self.t0),
               'position': {'lon': MID_LON, 'lat': LAT, 'headingDeg': None}}
        end = {'id': 'air:end', 'label': 'REFEND', 'quality': 'observed', 'observedAt': base.iso(self.t0),
               'position': {'lon': -0.50, 'lat': LAT - 0.02, 'headingDeg': None}}
        return {'component': 'air', 'contractVersion': '1.0', 'health': 'ready', 'observedAt': base.iso(now),
                'receivedAt': base.iso(now), 'expiresAt': base.iso(now + 60), 'nextPollAfterMs': 60000,
                'attribution': 'PRETEND for tests', 'entities': [road, plain, mid, end]}


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class PathSpeed(base.Motion):
    @classmethod
    def setUpClass(cls):
        real = base.FakeEngine
        base.FakeEngine = RoadEngine
        try:
            super().setUpClass()
        finally:
            base.FakeEngine = real

    def test_road_path_travelled_at_speed_and_eases_to_a_stop(self):
        with base.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length >= 4")
            time.sleep(5)   # well past the 3 s glide
            road, mid = self.rendered(page, 'ROAD'), self.rendered(page, 'REFMID')
            plain, end = self.rendered(page, 'PLAIN'), self.rendered(page, 'REFEND')
            self.assertLess(abs(road[0] - mid[0]), 3, (road, mid))       # stopped 750 m along, not at the end
            self.assertLess(abs(road[1] - mid[1]), 3, (road, mid))
            self.assertGreater(end[0] - road[0], 50)
            self.assertLess(abs(plain[0] - end[0]), 3, (plain, end))     # without a speed: old behaviour, end by arriveAt
            # and it stays stopped
            time.sleep(1.5)
            again = self.rendered(page, 'ROAD')
            self.assertLess(abs(again[0] - road[0]), 1)
            self.assertEqual(errors, [])
            browser.close()

    # the inherited Motion tests are run by test_motion_4100 itself
    test_1_vehicles_glide_and_say_estimated = test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free = None
    test_3_page_files_are_rechecked_every_visit = test_4_wheel_and_drag_work_over_icons = None
    test_5_no_taking_turns_when_more_move_than_may_glide = test_6_slow_device_glide_never_pauses = None


if __name__ == '__main__':
    unittest.main()
