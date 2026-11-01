"""HQ 4.1.7 (owner, 31 Oct 2026): "when you zoom in or out the icons stay where they were until
you stop and then move to their positions, giving the feeling of a mad scramble".

Real Chromium, pretend engine. An icon is filmed frame by frame while the map zooms:
- 4.1.6: it stands still for the whole zoom animation, then jumps (one big step);
- 4.1.7: it travels with the zoom (many small steps) and ends exactly where it belongs.
While the map is dragged, the icon moves exactly with the mouse.
Also: the street map can come straight from OpenFreeMap (TPA_HQ_BASEMAP=direct), and the
page's security rules then allow exactly that one extra address.
"""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_motion_4100 as base  # noqa: E402

FILM = """async label => {
  const pin = [...document.querySelectorAll('.review-pin.air')].find(x => x.title.startsWith(label));
  const at = () => { const r = pin.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  const out = [at()], t0 = performance.now();
  document.getElementById('zoom-in').click();
  while (performance.now() - t0 < 700) { await new Promise(r => requestAnimationFrame(r)); out.push(at()); }
  return out;
}"""


class StillEngine(base.FakeEngine):
    def frame(self):
        now = time.time()
        fixed = {'id': 'air:fixed', 'label': 'FIXED', 'quality': 'observed', 'observedAt': base.iso(now),
                 'position': {'lon': -0.36, 'lat': 53.80, 'headingDeg': None}}
        if not self.requests[:-1]:
            self.t0 = now
        mover = {'id': 'air:mover', 'label': 'MOVER', 'quality': 'observed', 'observedAt': base.iso(self.t0),
                 'position': {'lon': -0.40, 'lat': 53.86, 'headingDeg': 90.0},
                 'motion': {'mode': 'heading', 'speedMps': 20, 'maxSeconds': 300}}
        return {'component': 'air', 'contractVersion': '1.0', 'health': 'ready', 'observedAt': base.iso(now),
                'receivedAt': base.iso(now), 'expiresAt': base.iso(now + 60), 'nextPollAfterMs': 60000,
                'attribution': 'PRETEND for tests', 'entities': [fixed, mover]}


@unittest.skipUnless(base.sync_playwright and (base.VENDOR / 'leaflet/dist/leaflet.js').is_file(),
                     'needs Chromium and a local copy of Leaflet 1.9.4 (TPA_TEST_VENDOR_DIR)')
class ZoomDrag(base.Motion):
    @classmethod
    def setUpClass(cls):
        real = base.FakeEngine
        base.FakeEngine = StillEngine
        try:
            super().setUpClass()
        finally:
            base.FakeEngine = real

    def open(self, p):
        browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{self.port}/')
        page.locator('#tpa-welcome .tpa-primary').click()
        self.wait(page, "document.querySelectorAll('.review-pin.air').length >= 1")
        time.sleep(0.5)
        return browser, page, errors

    def test_icons_travel_with_the_zoom(self):
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            for attempt in range(2):          # zoom in twice
                film = page.evaluate(FILM, 'FIXED')
                start, end = film[0], film[-1]
                total = abs(end[0] - start[0]) + abs(end[1] - start[1])
                self.assertGreater(total, 60, film)            # the zoom really moved it
                steps = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(film, film[1:])]
                moving = [s for s in steps if s > 0.5]
                self.assertGreaterEqual(len(moving), 4, f'icon jumped instead of travelling: {steps}')
                self.assertLess(max(steps), total * 0.6, f'one big jump: {steps}')
                time.sleep(0.4)
                settled = self.rendered(page, 'FIXED')
                self.assertLess(abs(settled[0] - end[0]) + abs(settled[1] - end[1]), 2)   # no correction afterwards
            self.assertEqual(errors, [])
            browser.close()

    def test_icons_move_exactly_with_a_drag(self):
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            x0, y0 = 1000, 300           # on the map (the side menu covers the left of the page)
            before = self.rendered(page, 'FIXED')
            page.mouse.move(x0, y0)
            page.mouse.down()
            worst = 0
            for i in range(1, 21):
                page.mouse.move(x0 + 10 * i, y0 + 5 * i)
                page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
                now = self.rendered(page, 'FIXED')
                worst = max(worst, abs(now[0] - before[0] - 10 * i) + abs(now[1] - before[1] - 5 * i))
            page.mouse.up()
            self.assertLess(worst, 3, f'icon lagged the drag by {worst:.1f} px')
            time.sleep(0.6)
            after = self.rendered(page, 'FIXED')
            self.assertLess(abs(after[0] - before[0] - 200) + abs(after[1] - before[1] - 100), 3)
            self.assertEqual(errors, [])
            browser.close()

    def test_moving_icons_do_not_jump_back_after_a_drag(self):
        """Owner 31 Oct 13:50: "moving the map causes them to reset from the last on screen, not on
        map, position" - after letting go, moving icons jumped back to their old screen spot and
        glided across. Filmed for 2 s after the drag: no jumps, and it stays where the drag put it."""
        with base.sync_playwright() as p:
            browser, page, errors = self.open(p)
            self.wait(page, "[...document.querySelectorAll('.review-pin.air')].some(b => b.title.startsWith('MOVER'))")
            time.sleep(2.5)                     # let it glide normally first
            gap = lambda: (lambda m, f: (m[0] - f[0], m[1] - f[1]))(self.rendered(page, 'MOVER'), self.rendered(page, 'FIXED'))
            # quick flick, slow drag, drag then pause before letting go (no flick-on), several directions
            for dx, dy, steps, pause, hold in ((220, 90, 10, 0, 0), (-160, 140, 30, 0.02, 0), (180, -120, 12, 0, 0.12),
                                               (-200, -60, 6, 0.03, 0.3), (150, 150, 20, 0.01, 0.05)):
                g0 = gap()                      # MOVER relative to the still FIXED icon
                page.mouse.move(1000, 300); page.mouse.down()
                for i in range(1, steps + 1):
                    page.mouse.move(1000 + dx * i / steps, 300 + dy * i / steps)
                    if pause:
                        time.sleep(pause)
                time.sleep(hold)
                page.mouse.up()
                film = page.evaluate("""() => new Promise(done => {
                  const pin = l => [...document.querySelectorAll('.review-pin.air')].find(x => x.title.startsWith(l));
                  const c = b => { const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
                  const m = pin('MOVER'), f = pin('FIXED'), out = [], t0 = performance.now();
                  (function step() { const a = c(m), b = c(f); out.push([a[0] - b[0], a[1] - b[1]]);
                    if (performance.now() - t0 < 2000) requestAnimationFrame(step); else done(out); })();
                })""")
                worst = max(abs(g[0] - g0[0]) + abs(g[1] - g0[1]) for g in film)
                # the mover drifts ~1 px/s on its own; a jump back would be ~ the drag distance
                print(f'drag {dx},{dy} steps={steps} pause={pause} hold={hold}: worst {worst:.0f} px')
                self.assertLess(worst, 15, f'moving icon left its place on the map after the drag by {worst:.0f} px')
            self.assertEqual(errors, [])
            browser.close()

    def test_street_map_route_setting(self):
        import json
        csp = lambda h: h.get('Content-Security-Policy', '')
        with base.OPEN(f'http://127.0.0.1:{self.port}/hq-basemap', timeout=3) as r:
            self.assertEqual(json.load(r)['mode'], 'via-hq')            # PC default unchanged
        with base.OPEN(f'http://127.0.0.1:{self.port}/', timeout=3) as r:
            self.assertNotIn('openfreemap', csp(r.headers))
        port = base.free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith('TPA_HQ_')}
        hq = subprocess.Popen([sys.executable, str(base.HQ)], env=dict(env, TPA_HQ_PORT=str(port), TPA_HQ_PARKED='sea',
                              TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_BASEMAP='direct'),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                try:
                    base.OPEN(f'http://127.0.0.1:{port}/api/v4/manifest', timeout=.3); break
                except Exception:
                    time.sleep(.1)
            with base.OPEN(f'http://127.0.0.1:{port}/hq-basemap', timeout=3) as r:
                cfg = json.load(r)
            self.assertEqual(cfg, {'mode': 'direct', 'style': 'https://tiles.openfreemap.org/styles/liberty',
                                   'fallback': '/basemap/openfreemap/styles/liberty'})
            with base.OPEN(f'http://127.0.0.1:{port}/', timeout=3) as r:
                policy = csp(r.headers)
            self.assertIn("connect-src 'self' https://tiles.openfreemap.org;", policy)
            self.assertIn("img-src 'self' data: blob: https://tiles.openfreemap.org;", policy)
            self.assertIn("script-src 'self';", policy)        # nothing else loosened
        finally:
            hq.terminate(); hq.wait(5)
        bad = subprocess.run([sys.executable, str(base.HQ)], env=dict(env, TPA_HQ_PORT=str(base.free_port()), TPA_HQ_BASEMAP='cdn'),
                             capture_output=True, text=True, timeout=10)
        self.assertNotEqual(bad.returncode, 0)

    def test_direct_street_map_falls_back_through_hq(self):
        """Page logic with stand-in map libraries (as test_recovered_map_browser): the direct
        OpenFreeMap style is used; if it fails before the map loads, the route through HQ is used."""
        import json, re
        import test_recovered_map_browser as rec
        HQD = rec.HQ
        bridge = rec.BRIDGE.replace("once(n,f){if(n==='load')f()},on(n,f){this['_'+n]=f}",
                                    "once(n,f){if(n==='load')this.__load=f},on(n,f){this['_'+n]=f},setStyle(u){window.__setStyle=u}")
        bridge = bridge.replace("window.__style=this.options.style;", "window.__style=this.options.style;window.__gl=gl;")
        html = (HQD / 'index.html').read_text()
        html = re.sub(r'<script[^>]*\bsrc="[^"]+"[^>]*></script>', '', html)
        html = re.sub(r'<link[^>]*\bhref="/assets/vendor/[^\"]+"[^>]*>', '', html)
        geo = json.loads((HQD / 'data/uk-geography.json').read_text())
        with base.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 900}, bypass_csp=True); errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{self.port}/hq-map-status')      # a same-origin page to work in
            page.set_content(html)
            page.evaluate("""geo=>{window.fetch=uri=>{const u=String(uri);const body=u.startsWith('/data/')?geo:
              u.startsWith('/hq-basemap')?{mode:'direct',style:'https://tiles.openfreemap.org/styles/liberty',fallback:'/basemap/openfreemap/styles/liberty'}:
              u.startsWith('/hq-map-status')?{upstreamErrors:{},browser:{lastCode:'none'}}:
              {contractVersion:'1.0',components:['air','sea','rail','bus','road'].map(id=>({id,status:'not_installed'}))};
              return Promise.resolve(new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}}));};}""", geo)
            page.add_script_tag(content=rec.LEAFLET)
            page.evaluate("""()=>{let style=document.createElement('style');style.id='tpa-maplibre-css';document.head.append(style);let c=document.createElement('script');c.id='tpa-basemap-loader';c.dataset.maplibre='/assets/vendor/maplibre-gl.js';c.dataset.bridge='/assets/vendor/leaflet-maplibre-gl.js';c.dataset.vector='/assets/transport-vector-layer.js';document.head.append(c);let l=document.createElement('script');l.id='tpa-leaflet-js';document.head.append(l);}""")
            page.evaluate("""({maplibre,bridge,adapter})=>{const original=document.head.append.bind(document.head);document.head.append=function(el){if(el.tagName==='SCRIPT'&&el.src){let code=el.src.includes('leaflet-maplibre-gl')?bridge:el.src.includes('transport-vector-layer')?adapter:maplibre;(0,eval)(code);Promise.resolve().then(()=>el.dispatchEvent(new Event('load')));return;}return original(el);};}""",
                          {'maplibre': rec.MAPLIBRE, 'bridge': bridge, 'adapter': (HQD / 'assets/transport-vector-layer.js').read_text()})
            page.evaluate("window.__reports=[];window.TPA_HQ_MAP_REPORT=(c,s,d)=>window.__reports.push([c,s,d])")
            page.add_script_tag(content=(HQD / 'assets/tpa-basemap-loader.js').read_text())
            page.add_script_tag(content=(HQD / 'assets/hq.js').read_text())
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' else None)
            try:
                page.wait_for_function('!!(window.__gl && window.__gl._error)', timeout=8000)
            except Exception:
                self.fail(f"map never attached: {errors} style={page.evaluate('window.__style')} renderer={page.evaluate('window.TPA_HQ_READONLY_STATE&&window.TPA_HQ_READONLY_STATE().renderer')} gl={page.evaluate('window.__gl?Object.keys(window.__gl):null')} reports={page.evaluate('window.__reports||null')}")
            self.assertEqual(page.evaluate('window.__style'), 'https://tiles.openfreemap.org/styles/liberty')
            page.evaluate("window.__gl._error({error:{message:'Failed to fetch style'}})")
            self.assertEqual(page.evaluate('window.__setStyle'), f'http://127.0.0.1:{self.port}/basemap/openfreemap/styles/liberty')
            page.evaluate("window.__setStyle=null; window.__gl._error({error:{message:'second'}})")
            self.assertIsNone(page.evaluate('window.__setStyle'))      # only switches once
            self.assertEqual(errors, [])
            browser.close()

    test_1_vehicles_glide_and_say_estimated = test_2_layer_is_asked_at_its_engine_pace_and_small_pans_are_free = None
    test_3_page_files_are_rechecked_every_visit = test_4_wheel_and_drag_work_over_icons = None
    test_5_no_taking_turns_when_more_move_than_may_glide = test_6_slow_device_glide_never_pauses = None


if __name__ == '__main__':
    unittest.main()
