"""TPA v4 ROAD TRAFFIC ENGINE 0.1 — separate process, road lane only.

What it does
- Fetches TomTom traffic-flow picture tiles (coloured roads) on the server,
  using the same TomTom settings proven in the owner's v3 site
  (Orbis raster flow tiles, apiVersion 2, zoom 8-17, cache up to 480 s).
- Each tile is fetched once and shared with every viewer from a cache, so
  many viewers do not mean many TomTom requests.
- Serves the tiles and a small socket-contract v1.0 description to the HQ
  server on this PC. HQ draws them as a see-through layer.

Safety
- No key -> zero TomTom requests. Key read from TOMTOM_API_KEY (environment
  only), sent in a request header, never in logs, files or responses.
- Monthly safety cap (default 12,000 TomTom requests, as in v3). At the cap
  the layer pauses until next month. TomTom's free tier blocks, not charges,
  at its own limit; this cap keeps us far below it.
- Only tiles touching Great Britain and Northern Ireland are fetched.
- Listens on 127.0.0.1 only. Read-only. Standard library only.
"""
from __future__ import annotations

import calendar
import json
import math
import os
import re
import ssl
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ENGINE_VERSION = '0.3.3'
COMPONENT = 'road'
CONTRACT = '1.0'
TOMTOM_TILE = 'https://api.tomtom.com/maps/orbis/traffic/flow/raster/tile/{z}/{x}/{y}'
STYLE = re.sub(r'[^a-z-]', '', os.environ.get('TPA_ROAD_STYLE', 'dark').lower()) or 'dark'
MIN_ZOOM, MAX_ZOOM = 8, 17
# 0.2.0 (owner 31 Oct 22:08: traffic "loading in chunks whenever it likes"): 512-pixel pictures by
# default - a quarter as many requests per screen (and a quarter of the TomTom allowance). TomTom's
# 512 picture at zoom z covers the same ground as its 256 picture at z, so HQ asks for one zoom level
# lower (Leaflet zoomOffset -1) to keep the same detail; an older HQ simply shows them at 256.
# 0.3.0 (owner 1 Nov 13:06-13:11: TomTom's thick lines "give the impression buses are not quite on the
# roads"; "what if a user was sat on a bus and wanted to know why he/she was stopped"): close in, the
# traffic is drawn see-through so the real road edges show, and free-flowing (green) roads are left clear,
# so only hold-ups are coloured. Zoomed out it is drawn as before. TomTom's own style colours
# (developer docs, Orbis raster flow tiles v2): free flow = speed at 75% or more of normal.
STREET_ZOOM = 15
CITY_ZOOM = 13     # 0.3.1 (owner 1 Nov 13:40, York at zoom 13): see-through from city level too; green still shown
FREE_FLOW = {'dark': ['#118845', '#165f36'], 'light': ['#2ae57b', '#27a55e']}
TILE_SIZE = 256 if os.environ.get('TPA_ROAD_TILE_SIZE', '512').strip() == '256' else 512
Z_SHIFT = 1 if TILE_SIZE == 512 else 0
CACHE_MAX_SECONDS = 600   # 0.2.0: up to 10 minutes, if TomTom's own max-age allows
MEMORY_TILES = 600
# 0.3.3 (owner 3 Nov 22:46, TomTom dashboard: 200,000 free traffic pictures a month): our own safety cap 150,000 (was 12,000),
# leaving 50,000 spare, and each day may use only its fair share of what is left (TPA_ROAD_DAILY_SHARE=0 switches that off)
MONTHLY_CAP = max(0, int(os.environ.get('TPA_ROAD_MONTHLY_CAP', '150000') or 150000))
DAILY_SHARE = os.environ.get('TPA_ROAD_DAILY_SHARE', '1').strip() != '0'
UK = (49.8, -8.7, 61.0, 2.0)  # south, west, north, east
ATTRIBUTION = 'Traffic flow © TomTom'

KEY = (os.environ.get('TOMTOM_API_KEY') or '').strip()
_override = os.environ.get('TPA_ROAD_TOMTOM_URL', '').strip()
if _override and not re.match(r'^http://127\.0\.0\.1:\d+/.*\{z\}/\{x\}/\{y\}', _override):
    raise SystemExit('TPA_ROAD_TOMTOM_URL is for local testing only (127.0.0.1)')
TILE_URL = _override or TOMTOM_TILE

DATA = Path(os.environ.get('TPA_ROAD_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'TPA' / 'road-engine'))
USAGE_FILE = DATA / 'tomtom-monthly-usage.json'

LOCK = threading.Lock()
CACHE: 'OrderedDict[tuple, tuple[bytes, float]]' = OrderedDict()   # (z,x,y) -> (png, expires)
INFLIGHT: dict[tuple, threading.Event] = {}
STATS = {'served': 0, 'cacheHits': 0, 'tomtomRequests': 0, 'tomtomErrors': 0, 'lastError': '' if KEY else 'no-key',
         'lastSuccessAt': 0.0, 'consecutiveErrors': 0}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def month() -> str:
    return time.strftime('%Y-%m', time.gmtime())


def load_usage() -> dict:
    try:
        data = json.loads(USAGE_FILE.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('month'), str) and isinstance(data.get('requests'), int):
            return data
    except (OSError, ValueError):
        pass
    return {'month': month(), 'requests': 0}


USAGE = load_usage()


def today() -> str:
    return time.strftime('%Y-%m-%d', time.gmtime())


def daily_share(cap: int, used_before_today: int, now: float) -> int:
    """0.3.3: today's fair share of what is left this month (the rest split evenly over the days left, today included)."""
    t = time.gmtime(now)
    days_in_month = calendar.monthrange(t.tm_year, t.tm_mon)[1]
    left = max(0, cap - used_before_today)
    return -(-left // (days_in_month - t.tm_mday + 1))


def share_used_up() -> bool:
    return DAILY_SHARE and USAGE.get('day') == today() and \
        USAGE['requests'] - USAGE.get('dayStart', 0) >= daily_share(MONTHLY_CAP, USAGE.get('dayStart', 0), time.time())


def take_request() -> bool:
    """Count one TomTom request against the monthly safety cap and today's share (survives restarts)."""
    if USAGE['month'] != month():
        USAGE.update(month=month(), requests=0)
    if USAGE.get('day') != today():
        USAGE.update(day=today(), dayStart=USAGE['requests'])
    if USAGE['requests'] >= MONTHLY_CAP or share_used_up():
        return False
    USAGE['requests'] += 1
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = USAGE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(USAGE), encoding='utf-8')
        tmp.replace(USAGE_FILE)
    except OSError:
        pass  # counting continues in memory
    return True


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    west, east = x / n * 360 - 180, (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return south, west, north, east


def tile_in_uk(z: int, x: int, y: int) -> bool:
    s, w, n, e = tile_bounds(z, x, y)
    return not (n < UK[0] or s > UK[2] or e < UK[1] or w > UK[3])


def context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def fetch_tile(z: int, x: int, y: int) -> tuple[bytes, int]:
    url = TILE_URL.format(z=z, x=x, y=y) + f'?apiVersion=2&style={STYLE}&tileSize={TILE_SIZE}'
    req = Request(url, headers={'TomTom-Api-Key': KEY, 'TomTom-Api-Version': '2', 'Accept': 'image/png',
                                'User-Agent': f'TPA-Road-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)'})
    with urlopen(req, timeout=12, context=context()) as res:
        raw = res.read(1_000_001)
        ctype = res.headers.get('Content-Type', '').lower()
        cc = res.headers.get('Cache-Control', '').lower()
    if len(raw) > 1_000_000 or not ctype.startswith('image/png') or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('not-a-png')
    m = re.search(r'max-age=(\d+)', cc)
    ttl = 0 if 'no-store' in cc else min(CACHE_MAX_SECONDS, int(m.group(1))) if m else 120
    return raw, ttl


REFRESH_SECONDS = 600       # the page asks for new pictures every 10 minutes (owner 31 Oct 22:29)


def keep_until(now: float, ttl: int) -> float:
    """0.3.2 (owner 3 Nov 19:54: 7,122 of the month's 12,000 TomTom pictures used by 3 Nov): keep a picture to the end of
    the page's 10-minute refresh period - it would not show a newer one before then. TomTom's "do not keep" still wins."""
    if ttl <= 0:
        return now + 30
    return max(now + 30, (now // REFRESH_SECONDS + 1) * REFRESH_SECONDS)


def get_tile(z: int, x: int, y: int) -> bytes | None:
    """Cached tile, or one shared TomTom request. None = nothing to show (not an error page)."""
    key = (z, x, y)
    while True:
        with LOCK:
            STATS['served'] += 1
            hit = CACHE.get(key)
            if hit and hit[1] > time.time():
                CACHE.move_to_end(key)
                STATS['cacheHits'] += 1
                return hit[0]
            if not KEY:
                return None
            wait = INFLIGHT.get(key)
            if wait is None:
                if not take_request():
                    STATS['lastError'] = 'monthly-safety-cap'
                    return hit[0] if hit else None
                INFLIGHT[key] = threading.Event()
                break
        wait.wait(15)  # another viewer is already fetching this tile; share its result
        with LOCK:
            STATS['served'] -= 1
    try:
        STATS['tomtomRequests'] += 1
        raw, ttl = fetch_tile(z, x, y)
        with LOCK:
            CACHE[key] = (raw, keep_until(time.time(), ttl))
            CACHE.move_to_end(key)
            while len(CACHE) > MEMORY_TILES:
                CACHE.popitem(last=False)
            STATS.update(lastError='', lastSuccessAt=time.time(), consecutiveErrors=0)
        return raw
    except HTTPError as exc:
        return _fail(f'tomtom-http-{exc.code}', key)
    except (URLError, TimeoutError, OSError) as exc:
        return _fail('network-' + type(exc).__name__, key)
    except ValueError as exc:
        return _fail('bad-tile-' + str(exc)[:20], key)
    finally:
        with LOCK:
            ev = INFLIGHT.pop(key, None)
        if ev:
            ev.set()


def _fail(code: str, key) -> bytes | None:
    with LOCK:
        STATS['tomtomErrors'] += 1
        STATS['consecutiveErrors'] += 1
        STATS['lastError'] = code[:60]
        old = CACHE.get(key)
    print('TomTom tile failed:', code, flush=True)  # never the key or URL
    return old[0] if old else None


def health() -> tuple[str, str]:
    if not KEY:
        return 'unavailable', 'TomTom key not set up on this PC'
    if USAGE['month'] == month() and USAGE['requests'] >= MONTHLY_CAP:
        return 'unavailable', 'Paused: monthly free-use safety limit reached'
    if share_used_up():
        return 'stale', "Today's share of the free traffic allowance used: showing the last pictures"
    if STATS['consecutiveErrors'] >= 5:
        code = STATS['lastError']
        if code in ('tomtom-http-401', 'tomtom-http-403'):
            return 'unavailable', 'TomTom did not accept the key'
        return 'stale', 'TomTom not responding · retrying'
    return 'ready', ''


def snapshot() -> dict:
    now = time.time()
    h, notice = health()
    frame = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
             'observedAt': iso(now), 'receivedAt': iso(now), 'expiresAt': iso(now + 300),
             'nextPollAfterMs': 60000, 'attribution': ATTRIBUTION, 'entities': [],
             'overlay': {'type': 'raster', 'tiles': f'/api/v4/components/{COMPONENT}/tiles/{{z}}/{{x}}/{{y}}.png',
                         'minZoom': MIN_ZOOM, 'maxZoom': MAX_ZOOM, 'opacity': 0.85, 'refreshSeconds': REFRESH_SECONDS,   # owner 31 Oct 22:29: every 10 minutes is plenty
                         'tileSize': TILE_SIZE,
                         'opacityByZoom': [[0, 0.85], [CITY_ZOOM, 0.6], [STREET_ZOOM, 0.5], [STREET_ZOOM + 1, 0.4]],
                         'hideColours': FREE_FLOW.get(STYLE, []), 'hideFromZoom': STREET_ZOOM, 'hideTolerance': 40}}
    if notice:
        frame['notice'] = notice
    return frame


TILE_PATH = re.compile(r'^/tiles/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png$')


class Handler(BaseHTTPRequestHandler):
    server_version = 'TPA-Road-Engine'
    sys_version = ''

    def send(self, status: int, raw: bytes, ctype: str, cache: str = 'no-store') -> None:
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def json(self, status: int, obj: dict) -> None:
        self.send(status, json.dumps(obj, separators=(',', ':')).encode('utf-8'), 'application/json; charset=utf-8')

    def do_GET(self):  # noqa: N802
        if self.client_address[0] != '127.0.0.1':
            return self.json(403, {'error': 'local only'})
        path = urlsplit(self.path).path
        if path == '/status':
            h, notice = health()
            with LOCK:
                return self.json(200, {'component': COMPONENT, 'engine': 'tpa-road-engine', 'engineVersion': ENGINE_VERSION,
                                       'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice,
                                       'keyConfigured': bool(KEY), 'lastError': STATS['lastError'],
                                       'lastSuccessAt': iso(STATS['lastSuccessAt']) if STATS['lastSuccessAt'] else None,
                                       'tilesServed': STATS['served'], 'cacheHits': STATS['cacheHits'],
                                       'tomtomRequestsThisRun': STATS['tomtomRequests'],
                                       'tomtomRequestsThisMonth': USAGE['requests'] if USAGE['month'] == month() else 0,
                                       'monthlySafetyCap': MONTHLY_CAP})
        if path == '/snapshot':
            return self.json(200, snapshot())
        m = TILE_PATH.match(path)
        if m:
            z, x, y = (int(v) for v in m.groups())
            if not (MIN_ZOOM - Z_SHIFT <= z <= MAX_ZOOM and x < 2 ** z and y < 2 ** z) or not tile_in_uk(z, x, y):
                return self.send(204, b'', 'image/png')
            raw = get_tile(z, x, y)
            if raw is None:
                return self.send(204, b'', 'image/png')
            return self.send(200, raw, 'image/png', 'private, max-age=60')
        return self.json(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.json(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_ROAD_ENGINE_PORT', '8782'))
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA ROAD TRAFFIC ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('TomTom key: ' + ('set for this session' if KEY else 'NOT SET - no traffic will be fetched'), flush=True)
        used = USAGE['requests'] if USAGE['month'] == month() else 0
        print(f'TomTom requests this month: {used} of safety limit {MONTHLY_CAP}', flush=True)
        print('Keep this window open. Close it to stop road traffic only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
