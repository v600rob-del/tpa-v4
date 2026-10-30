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

ENGINE_VERSION = '0.1.0'
COMPONENT = 'road'
CONTRACT = '1.0'
TOMTOM_TILE = 'https://api.tomtom.com/maps/orbis/traffic/flow/raster/tile/{z}/{x}/{y}'
STYLE = re.sub(r'[^a-z-]', '', os.environ.get('TPA_ROAD_STYLE', 'dark').lower()) or 'dark'
MIN_ZOOM, MAX_ZOOM = 8, 17
CACHE_MAX_SECONDS = 480
MEMORY_TILES = 600
MONTHLY_CAP = max(0, int(os.environ.get('TPA_ROAD_MONTHLY_CAP', '12000') or 12000))
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


def take_request() -> bool:
    """Count one TomTom request against the monthly safety cap (survives restarts)."""
    if USAGE['month'] != month():
        USAGE.update(month=month(), requests=0)
    if USAGE['requests'] >= MONTHLY_CAP:
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
    url = TILE_URL.format(z=z, x=x, y=y) + f'?apiVersion=2&style={STYLE}&tileSize=256'
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
            CACHE[key] = (raw, time.time() + max(ttl, 30))
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
                         'minZoom': MIN_ZOOM, 'maxZoom': MAX_ZOOM, 'opacity': 0.85, 'refreshSeconds': 300}}
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
            if not (MIN_ZOOM <= z <= MAX_ZOOM and x < 2 ** z and y < 2 ** z) or not tile_in_uk(z, x, y):
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
