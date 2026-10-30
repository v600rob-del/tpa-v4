"""TPA v4 AIRCRAFT ENGINE 0.1 — separate process, air lane only.

Data: ADSB.lol, a free community aircraft-tracking network (data licensed
under the Open Database License, ODbL; credit shown in TPA). No key is needed
today; ADSB.lol has said a key will be required in future (available by
feeding them data). TPA_ADSBLOL_API_KEY is read if set, ready for that day.

How it fetches (like the bus engine's "area" mode):
- asks ADSB.lol once for a circle covering the area being viewed (plus a
  margin, at most 250 nautical miles, the service maximum);
- at most every POLL_SECONDS (default 20 s), and ONLY while someone is viewing;
- every viewer is served from the same shared copy;
- if ADSB.lol says "slow down" (429) or refuses, it pauses for 5 minutes.

Honesty: aircraft are shown where they last reported (never invented or
animated). Reports over 30 s old are marked stale; over 90 s old they are
dropped (the owner's v3 rule). Ground traffic is labelled "on the ground".
127.0.0.1 only. Read-only. Standard library only.
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ENGINE_VERSION = '0.2.0'
COMPONENT = 'air'
CONTRACT = '1.0'
ADSBLOL_POINT = 'https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}'
ATTRIBUTION = 'Aircraft data: ADSB.lol (Open Database License)'

POLL_SECONDS = max(10, int(os.environ.get('TPA_AIR_POLL_SECONDS', '20') or 20))
DEMAND_WINDOW = max(5, int(os.environ.get('TPA_AIR_DEMAND_SECONDS', '180') or 180))
STALE_AFTER = 30
DROP_AFTER = 90              # owner's v3 rule
FRAME_TTL = 60
MAX_DOWNLOAD = 12_000_000
MAX_PER_VIEW = 400
MAX_RADIUS_NM = 250          # ADSB.lol maximum
MAX_VIEW_SPAN = 9.0          # degrees of latitude; wider views ask the user to zoom in
COOLDOWN_SECONDS = 300       # after 401/403/429/451, as in v3

KEY = (os.environ.get('TPA_ADSBLOL_API_KEY') or '').strip()
_test = os.environ.get('TPA_AIR_TEST_URL', '').strip()
if _test and not re.match(r'^http://127\.0\.0\.1:\d+/.*\{lat\}.*\{lon\}.*\{radius\}', _test):
    raise SystemExit('TPA_AIR_TEST_URL must point at 127.0.0.1 (testing only)')
POINT_URL = _test or ADSBLOL_POINT

LOCK = threading.Lock()
STATE = {'aircraft': [], 'fetchedAt': 0.0, 'circle': None, 'lastAttemptAt': 0.0, 'lastError': '',
         'lastDemandAt': 0.0, 'wanted': None, 'downloadsThisRun': 0, 'downloadBytes': 0,
         'cooldownUntil': 0.0, 'consecutiveErrors': 0}
WAKE = threading.Event()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def finite(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def clean(text, limit: int) -> str:
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', str(text or '')).strip()[:limit]


def nm_between(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.asin(min(1.0, math.sqrt(a)))


def circle_for(view):
    """Centre and radius (nm) covering the view plus a margin."""
    s, w, n, e = view
    clat, clon = (s + n) / 2, (w + e) / 2
    radius = max(nm_between(clat, clon, s, w), nm_between(clat, clon, n, e)) + 15
    return round(clat, 3), round(clon, 3), int(min(MAX_RADIUS_NM, max(25, math.ceil(radius))))


def circle_covers(circle, view) -> bool:
    if not circle:
        return False
    clat, clon, r = circle
    s, w, n, e = view
    return all(nm_between(clat, clon, la, lo) <= r for la, lo in ((s, w), (s, e), (n, w), (n, e)))


def parse(payload: dict, fetched: float) -> list[dict]:
    rows = payload.get('ac') if isinstance(payload.get('ac'), list) else payload.get('aircraft')
    out, seen = [], set()
    for a in rows if isinstance(rows, list) else []:
        if not isinstance(a, dict):
            continue
        lat, lon = finite(a.get('lat')), finite(a.get('lon'))
        hexid = clean(a.get('hex'), 12).lower()
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or not re.fullmatch(r'~?[0-9a-f]{6}', hexid):
            continue
        age = finite(a.get('seen_pos'))
        age = 0.0 if age is None else max(0.0, age)
        if age > DROP_AFTER or hexid in seen:
            continue
        seen.add(hexid)
        alt = a.get('alt_baro', a.get('alt_geom'))
        heading = finite(a.get('track'))
        if heading is None:
            heading = finite(a.get('true_heading'))
        gs = finite(a.get('gs'))   # ground speed, knots
        speed = round(gs * 0.514444, 1) if gs is not None and 0 < gs < 1000 else None
        out.append({'hex': hexid, 'lat': round(lat, 5), 'lon': round(lon, 5), 'speed': speed,
                    'heading': round(heading % 360, 1) if heading is not None else None,
                    'flight': clean(a.get('flight'), 10), 'reg': clean(a.get('r'), 12), 'type': clean(a.get('t'), 8),
                    'ground': alt == 'ground', 'alt': int(alt) if finite(alt) is not None else None,
                    'reported': fetched - age})
    return out


def context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def fetch_once(circle) -> None:
    clat, clon, radius = circle
    url = POINT_URL.format(lat=clat, lon=clon, radius=radius)
    headers = {'User-Agent': f'TPA-Air-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)', 'Accept': 'application/json'}
    if KEY:
        headers['api-auth'] = KEY
    started = time.time()
    try:
        with urlopen(Request(url, headers=headers), timeout=20, context=context()) as res:
            raw = res.read(MAX_DOWNLOAD + 1)
        if len(raw) > MAX_DOWNLOAD:
            raise ValueError('too-large')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('not-an-object')
        aircraft = parse(payload, started)
        with LOCK:
            STATE.update(aircraft=aircraft, fetchedAt=started, circle=circle, lastError='', consecutiveErrors=0,
                         downloadBytes=len(raw))
            STATE['downloadsThisRun'] += 1
        print(f'ADSB.lol OK: {len(aircraft)} aircraft within {radius} nm, {len(raw)//1024} KB', flush=True)
    except HTTPError as exc:
        _fail(f'adsblol-http-{exc.code}', exc.code in (401, 403, 429, 451))
    except (URLError, TimeoutError, OSError) as exc:
        _fail('network-' + type(exc).__name__, False)
    except ValueError as exc:
        _fail('bad-data-' + str(exc)[:30], False)


def _fail(code: str, long_pause: bool) -> None:
    with LOCK:
        STATE['lastError'] = code[:60]
        STATE['consecutiveErrors'] += 1
        if long_pause:
            STATE['cooldownUntil'] = time.time() + COOLDOWN_SECONDS
    print('ADSB.lol fetch failed:', code, flush=True)


def poller() -> None:
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        now = time.time()
        with LOCK:
            wanted = STATE['wanted']
            viewed = now - STATE['lastDemandAt'] < DEMAND_WINDOW
            cooling = now < STATE['cooldownUntil']
            wait = POLL_SECONDS * min(6, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else POLL_SECONDS
            due = now - STATE['lastAttemptAt'] >= wait
            uncovered = wanted is not None and not circle_covers(STATE['circle'], wanted) and now - STATE['lastAttemptAt'] >= 3
            go = viewed and wanted is not None and not cooling and (due or uncovered)
            circle = circle_for(wanted) if go else None
            if go:
                STATE['lastAttemptAt'] = now
        if go:
            fetch_once(circle)


def health(now: float) -> tuple[str, str]:
    if now < STATE['cooldownUntil']:
        code = STATE['lastError']
        msg = 'ADSB.lol asked us to slow down · paused 5 min' if code == 'adsblol-http-429' else 'ADSB.lol refused the request · paused 5 min'
        return ('stale' if STATE['fetchedAt'] else 'unavailable'), msg
    if not STATE['fetchedAt']:
        if STATE['consecutiveErrors']:
            return 'unavailable', 'ADSB.lol not responding · retrying'
        return 'ready', 'Connecting to ADSB.lol…'  # "ready" so HQ sends the viewed area
    if now - STATE['fetchedAt'] > FRAME_TTL:
        return 'stale', 'Aircraft update delayed · showing last positions'
    return 'ready', ''


def note_demand(view=None) -> None:
    with LOCK:
        STATE['lastDemandAt'] = time.time()
        if view is not None:
            STATE['wanted'] = view
    WAKE.set()


def label_for(a) -> str:
    name = a['flight'] or a['reg'] or a['hex'].upper()
    parts = [name]
    if a['type']:
        parts.append(a['type'])
    if a['ground']:
        parts.append('on the ground')
    elif a['alt'] is not None:
        parts.append(f"{a['alt']:,} ft")
    if a['reg'] and a['reg'] != name:
        parts.append(a['reg'])
    return ' · '.join(parts)


def snapshot(view) -> dict:
    now = time.time()
    small = view is not None and view[2] - view[0] <= MAX_VIEW_SPAN
    note_demand(view if small else None)
    with LOCK:
        aircraft, fetched, circle = STATE['aircraft'], STATE['fetchedAt'], STATE['circle']
        h, notice = health(now)
    received = fetched or now
    frame = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
             'observedAt': iso(received), 'receivedAt': iso(received), 'expiresAt': iso(received + FRAME_TTL),
             'nextPollAfterMs': 10000, 'attribution': ATTRIBUTION, 'entities': []}
    if not small:
        frame['notice'] = 'Zoom in to see aircraft'
        return frame
    if notice:
        frame['notice'] = notice
    if not fetched:
        return frame
    s, w, n, e = view
    clat, clon = (s + n) / 2, (w + e) / 2
    inside = []
    for a in aircraft:
        age = now - a['reported']
        if age > DROP_AFTER or not (s <= a['lat'] <= n and w <= a['lon'] <= e):
            continue
        inside.append(((a['lat'] - clat) ** 2 + (a['lon'] - clon) ** 2, a, age))
    inside.sort(key=lambda t: t[0])
    for _, a, age in inside[:MAX_PER_VIEW]:
        entity = {'id': 'air:' + a['hex'], 'label': label_for(a)[:160],
                  'position': {'lon': a['lon'], 'lat': a['lat'], 'headingDeg': a['heading']},
                  'quality': 'stale' if age > STALE_AFTER else 'observed', 'observedAt': iso(a['reported'])}
        # 0.2.0: speed + track let the map carry the aircraft forward smoothly between
        # reports (shown as "estimated"), for at most a minute after the real report.
        if not a['ground'] and a['heading'] is not None and a.get('speed') and a['speed'] >= 5:
            entity['motion'] = {'mode': 'heading', 'speedMps': a['speed'], 'maxSeconds': 60}
        frame['entities'].append(entity)
    if not frame['entities'] and not circle_covers(circle, view) and not notice:
        frame['notice'] = 'Loading aircraft for this area…'
    elif len(inside) > MAX_PER_VIEW and not notice:
        frame['notice'] = f'Showing nearest {MAX_PER_VIEW} · zoom in for all'
    return frame


def parse_bbox(query: str):
    q = parse_qs(query)
    try:
        w, s, e, n = (float(q[k][0]) for k in ('west', 'south', 'east', 'north'))
    except (KeyError, ValueError, IndexError):
        return None
    if not all(math.isfinite(x) for x in (s, w, n, e)) or not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        return None
    return s, w, n, e


class Handler(BaseHTTPRequestHandler):
    server_version = 'TPA-Air-Engine'
    sys_version = ''

    def reply(self, status: int, obj: dict) -> None:
        raw = json.dumps(obj, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.client_address[0] != '127.0.0.1':
            return self.reply(403, {'error': 'local only'})
        split = urlsplit(self.path)
        if split.path == '/status':
            note_demand()
            now = time.time()
            with LOCK:
                h, notice = health(now)
                return self.reply(200, {'component': COMPONENT, 'engine': 'tpa-air-engine', 'engineVersion': ENGINE_VERSION,
                                        'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice,
                                        'keyConfigured': bool(KEY), 'lastError': STATE['lastError'],
                                        'lastSuccessAt': iso(STATE['fetchedAt']) if STATE['fetchedAt'] else None,
                                        'aircraftInLastDownload': len(STATE['aircraft']), 'lastDownloadKB': STATE['downloadBytes'] // 1024,
                                        'adsblolRequestsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS})
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_AIR_ENGINE_PORT', '8783'))
    threading.Thread(target=poller, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA AIRCRAFT ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('Source: ADSB.lol (free, no key needed today).', flush=True)
        print(f'Asks ADSB.lol at most every {POLL_SECONDS}s, only while the map is open.', flush=True)
        print('Keep this window open. Close it to stop aircraft only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
