"""HQ 4.1.13 (owner, 31 Oct 2026 22:46): "When i tab to another window and go back to the map, it
always has a catch up, and thats when the buses and trains move about wrong."

Browsers pause animation in hidden tabs. Real Chromium, pretend engine (test_corrections_4109's):
the page is made hidden for 10 s; meanwhile a corrected report puts LATE 300 m behind where the old
estimate had it. On coming back, LATE must carry on at its own speed at once - not stand waiting for
seconds (4.1.12 treated the return as a correction and waited).
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402
import test_corrections_4109 as corr  # noqa: E402

# Stop only the drawing (animation frames), as a browser does for a covered window; timers and
# network carry on. Frames asked for meanwhile are held and released on resume.
STOP_DRAWING = """() => { const real = window.__realRaf || (window.__realRaf = window.requestAnimationFrame.bind(window));
  const held = []; window.requestAnimationFrame = cb => { held.push(cb); return 0; };
  window.__resumeDraw = () => { window.requestAnimationFrame = real; held.splice(0).forEach(cb => real(cb)); }; }"""

HIDE = """hidden => { Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  document.dispatchEvent(new Event('visibilitychange')); }"""


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class ReturnToTab(base.Motion):
    @classmethod
    def setUpClass(cls):
        real = base.FakeEngine
        base.FakeEngine = corr.CorrectingEngine
        try:
            super().setUpClass()
        finally:
            base.FakeEngine = real

    def test_coming_back_to_the_tab_is_a_fresh_start(self):
        self.away_and_back('hidden tab')

    def test_coming_back_to_a_frozen_page(self):
        self.away_and_back('frozen')

    def test_coming_back_to_a_window_that_was_covered(self):
        """4.1.14 (owner 31 Oct 23:18: "went to another browser window ... waited almost 30 seconds"):
        the browser stops drawing a covered window without saying the page is hidden, while the page
        keeps fetching reports. Imitated exactly: drawing stopped, everything else running, and LATE's
        new report puts it 900 m behind (4.1.13 then made it wait ~18 s after coming back)."""
        self.away_and_back('not drawn')

    def away_and_back(self, how):
        with base.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            self.engine.version = 1
            page.goto(f'http://127.0.0.1:{self.port}/')
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length >= 3")
            for _ in range(3):
                page.locator('#zoom-in').click(); time.sleep(0.4)
            time.sleep(3)
            a = page.evaluate(corr.FILM, 1)
            px_per_m = (a[-1][1] - a[0][1]) / ((a[-1][0] - a[0][0]) / 1000 * corr.SPEED)
            self.assertGreater(px_per_m, 0.05)
            cdp = page.context.new_cdp_session(page)
            self.engine.late_shift = -900 if how == 'not drawn' else -300
            if how == 'not drawn':
                page.evaluate(STOP_DRAWING)
            elif how == 'frozen':
                cdp.send('Page.setWebLifecycleState', {'state': 'frozen'})
            else:
                page.evaluate(HIDE, True)                   # tab to another window
            self.engine.switched = time.time(); self.engine.version = 2
            time.sleep(10)
            if how == 'not drawn':
                page.evaluate('window.__resumeDraw()')
            elif how == 'frozen':
                cdp.send('Page.setWebLifecycleState', {'state': 'active'})
            else:
                page.evaluate(HIDE, False)                  # and back
            film = page.evaluate(corr.FILM, 6)
            t = [f[0] / 1000 for f in film]
            late = [f[1] for f in film]
            # after the first second back, LATE moves at its own speed: never still for over a second
            still_from, worst = None, 0.0
            for i in range(1, len(late)):
                if t[i] < 1.0:
                    continue
                if abs(late[i] - late[i - 1]) < 0.05:
                    still_from = t[i - 1] if still_from is None else still_from
                    worst = max(worst, t[i] - still_from)
                else:
                    still_from = None
            self.assertLess(worst, 1.0, f'{how}: LATE stood waiting for {worst:.1f} s after coming back')
            moved = (late[-1] - late[int(len(late) / 3)]) / px_per_m / (t[-1] - t[int(len(late) / 3)])
            self.assertGreater(moved, corr.SPEED * 0.6, f'LATE crawling at {moved:.0f} m/s after coming back')
            self.assertEqual(errors, [])
            browser.close()

    test_1_vehicles_glide_and_say_estimated = test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free = None
    test_3_page_files_are_rechecked_every_visit = test_4_wheel_and_drag_work_over_icons = None
    test_5_no_taking_turns_when_more_move_than_may_glide = test_6_slow_device_glide_never_pauses = None


if __name__ == '__main__':
    unittest.main()
