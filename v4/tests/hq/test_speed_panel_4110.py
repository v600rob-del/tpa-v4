"""HQ 4.1.10 speed panel: with #speed on the address, the page shows how long each part took to
appear in this browser (street map, traffic, each vehicle layer, server answer times).
Without #speed nothing is shown. Measuring only; nothing is sent anywhere."""
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class SpeedPanel(base.Motion):
    def test_panel_only_with_hash_and_shows_times(self):
        with base.sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{self.port}/')
            time.sleep(2.5)
            self.assertEqual(page.locator('#speed-panel').count(), 0)
            page.goto(f'http://127.0.0.1:{self.port}/#speed')
            page.locator('#tpa-welcome .tpa-primary').click()
            self.wait(page, "document.querySelectorAll('.review-pin.air').length >= 1")
            time.sleep(2.5)
            text = page.locator('#speed-panel').inner_text()
            self.assertIn('TPA SPEED', text)
            line = next(l for l in text.splitlines() if l.startswith('Aircraft'))
            self.assertRegex(line, r'first shown +\d+\.\d s +server answer last \d+ ms avg \d+ ms \(\d+\)')
            self.assertEqual(page.locator('#speed-panel').evaluate('e => getComputedStyle(e).pointerEvents'), 'none')
            self.assertEqual(errors, [])
            browser.close()

    test_1_vehicles_glide_and_say_estimated = test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free = None
    test_3_page_files_are_rechecked_every_visit = test_4_wheel_and_drag_work_over_icons = None
    test_5_no_taking_turns_when_more_move_than_may_glide = test_6_slow_device_glide_never_pauses = None


if __name__ == '__main__':
    unittest.main()
