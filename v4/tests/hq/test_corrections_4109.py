"""HQ 4.1.9 (owner, 31 Oct 2026 14:04): icons "wander off when they get a new updated position".
Server check: bus estimates were typically 93 m out when the real report arrived, half ahead and
half behind, and HQ slid them there in a straight 2 s line (often backwards, at ~100 mph).

Real Chromium, pretend engine, zoomed in to street level. Two vehicles head east at 50 m/s.
After a few seconds a corrected report arrives:
- LATE: really 300 m behind our estimate -> the icon must WAIT (never go backwards), then carry on;
- EARLY: really 300 m ahead -> the icon must catch up GRADUALLY (no dash), then carry on.
"""
import math
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402

LAT = 53.842
K = 111195 * math.cos(math.radians(LAT))
SPEED = 50.0


class CorrectingEngine(base.FakeEngine):
    def frame(self):
        now = time.time()
        if not self.requests[:-1]:
            self.t0 = now
        ents = []
        for label, lon0, lat, shift in (('LATE', -0.440, LAT + 0.003, getattr(self, 'late_shift', -300)), ('EARLY', -0.440, LAT - 0.003, 300),
                                        ('ASTRAY', -0.440, LAT - 0.009, 0)):
            t, lon = self.t0, lon0
            if self.version == 2:                       # corrected report: where it really is now
                t = self.switched
                lon = lon0 + (SPEED * (t - self.t0) + shift) / K
                if label == 'ASTRAY':
                    lat = lat + 400 / 111195            # 4.1.12: really 400 m to the side (it went another way)
            ents.append({'id': 'air:' + label.lower(), 'label': label, 'quality': 'observed', 'observedAt': base.iso(t),
                         'position': {'lon': lon, 'lat': lat, 'headingDeg': 90.0},
                         'motion': {'mode': 'heading', 'speedMps': SPEED, 'maxSeconds': 300}})
        return {'component': 'air', 'contractVersion': '1.0', 'health': 'ready', 'observedAt': base.iso(now),
                'receivedAt': base.iso(now), 'expiresAt': base.iso(now + 60), 'nextPollAfterMs': self.poll_ms,
                'attribution': 'PRETEND for tests', 'entities': ents}


FILM = """seconds => new Promise(done => {
  const pin = l => [...document.querySelectorAll('.review-pin.air')].find(x => x.title.startsWith(l));
  const x = b => { const r = b.getBoundingClientRect(); return r.left + r.width / 2; };
  const out = [], t0 = performance.now();
  const y = b => { const r = b.getBoundingClientRect(); return r.top + r.height / 2; };
  (function f() { out.push([performance.now() - t0, x(pin('LATE')), x(pin('EARLY')), y(pin('ASTRAY'))]);
    if (performance.now() - t0 < seconds * 1000) requestAnimationFrame(f); else done(out); })();
})"""


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class Corrections(base.Motion):
    @classmethod
    def setUpClass(cls):
        real = base.FakeEngine
        base.FakeEngine = CorrectingEngine
        try:
            super().setUpClass()
        finally:
            base.FakeEngine = real

    def test_late_vehicle_waits_and_early_one_catches_up(self):
        with base.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            self.engine.version = 1
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length >= 3")
            for _ in range(3):                         # street level: ~3 m per screen pixel
                page.locator('#zoom-in').click(); time.sleep(0.4)
            time.sleep(3)
            px_per_m = None
            a = page.evaluate(FILM, 1)
            px_per_m = (a[-1][1] - a[0][1]) / ((a[-1][0] - a[0][0]) / 1000 * SPEED)
            self.assertGreater(px_per_m, 0.05, 'vehicles not moving on screen')
            self.engine.switched = time.time()
            self.engine.version = 2                    # the corrected report arrives at HQ's next ask (<= 5 s)
            film = page.evaluate(FILM, 16)
            self.astray_y = [f[3] for f in film]
            t = [f[0] / 1000 for f in film]
            late = [f[1] for f in film]
            early = [f[2] for f in film]

            def window_speeds(xs):        # m/s over one-second windows (what the eye sees)
                out, j = [], 0
                for i in range(len(t)):
                    while t[i] - t[j] > 1.0:
                        j += 1
                    if t[i] - t[j] >= 0.95:
                        out.append((t[i], (xs[i] - xs[j]) / px_per_m / (t[i] - t[j])))
                return out
            # LATE: never backwards; it waits for a while, then carries on at its own speed
            ls = window_speeds(late)
            self.assertGreater(min(v for _, v in ls), -3, f'LATE went backwards: {min(v for _, v in ls):.0f} m/s')
            waited = sum(1 for _, v in ls if abs(v) < 2)
            self.assertGreater(waited, 60, 'LATE never waited')
            self.assertGreater(ls[-1][1], SPEED * 0.6, f'LATE did not carry on: {ls[-1][1]:.0f} m/s')
            # ASTRAY (4.1.12): found 400 m to the side - never shown in between (no flying across
            # buildings): every frame is near the old line or the new one, and it fades in there
            ys = page.evaluate("""() => { const b = [...document.querySelectorAll('.review-pin.air')].find(x => x.title.startsWith('ASTRAY'));
                                   const r = b.getBoundingClientRect(); return r.top + r.height / 2; }""")
            self.assertTrue(self.astray_y, 'ASTRAY not filmed')
            mid = [y for y in self.astray_y if abs(y - self.astray_y[0]) > 6 and abs(y - ys) > 6]
            self.assertLess(len(mid), 3, f'ASTRAY flew across: {len(mid)} frames in between')
            # EARLY: never stops, never dashes; catching up adds at most a little speed
            es = window_speeds(early)
            self.assertGreater(min(v for _, v in es), SPEED * 0.5, f'EARLY slowed to {min(v for _, v in es):.0f} m/s')
            self.assertLess(max(v for _, v in es), SPEED + 25, f'EARLY dashed at {max(v for _, v in es):.0f} m/s')
            self.assertEqual(errors, [])
            browser.close()

    test_1_vehicles_glide_and_say_estimated = test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free = None
    test_3_page_files_are_rechecked_every_visit = test_4_wheel_and_drag_work_over_icons = None
    test_5_no_taking_turns_when_more_move_than_may_glide = test_6_slow_device_glide_never_pauses = None


if __name__ == '__main__':
    unittest.main()
