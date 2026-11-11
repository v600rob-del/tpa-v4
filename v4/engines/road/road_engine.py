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
import gzip
import json
import math
import os
import re
import ssl
import struct
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

ENGINE_VERSION = '0.4.2'
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
ATTRIBUTION = 'Traffic flow and incidents © TomTom'

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



# ---------------------------------------------------------------------------------------------------------------
# 0.4.0 (owner 11 Nov 13:04, traffic: "Incident markers", "Roadworks" - "only current roadworks, not planned, as we are
# showing a 'what's happening now' page" - and "Scotland & Wales too"): TomTom's own incident data for the whole UK,
# from its Orbis Traffic Incidents vector tiles (on the owner's key; MyTomTom counts vector tiles in their own monthly
# allowance, separate from the traffic pictures above). Accidents, closures, lane closures, broken-down vehicles,
# hazards and roadworks in effect now (TomTom's "present" - never future/planned). Plain queues are left to the
# coloured roads. Asked only for the area someone is looking at, shared, within a monthly budget and a daily fair share.
# ---------------------------------------------------------------------------------------------------------------
INC_TILE = 'https://api.tomtom.com/maps/orbis/traffic/incidents/vector/tile/{z}/{x}/{y}?apiVersion=2'
_inc_override = os.environ.get('TPA_ROAD_INC_TEST_URL', '').strip()
if _inc_override and not re.match(r'^http://127\.0\.0\.1:\d+/.*\{z\}/\{x\}/\{y\}', _inc_override):
    raise SystemExit('TPA_ROAD_INC_TEST_URL is for local testing only (127.0.0.1)')
INC_URL = _inc_override or INC_TILE
# The owner's test on the server (11 Nov 13:07, Leeds): TomTom thins incident tiles by road importance as they cover more
# ground - zoom 7: motorway roadworks only; 8: motorways and trunk roads; 12: B roads and local roads too ("Closed ·
# Roadworks" on tertiary roads). So the engine asks for the tiles covering the area being looked at, at the most detailed
# zoom that needs no more than INC_MAX_TILES of them (at most INC_TOP_ZOOM), keeps each for INC_KEEP_SECONDS and shares
# it with every viewer - like the traffic pictures. Zoomed right out (it would need tiles above zoom INC_LOW_ZOOM): none.
INC_TOP_ZOOM, INC_LOW_ZOOM = 12, 7
INC_MAX_TILES = max(1, int(os.environ.get('TPA_ROAD_INC_MAX_TILES', '16') or 16))
INC_KEEP_SECONDS = max(1 if _inc_override else 120, int(os.environ.get('TPA_ROAD_INC_KEEP_SECONDS', '300') or 300))
INC_MONTHLY = max(0, int(os.environ.get('TPA_ROAD_INC_MONTHLY', '100000') or 100000))   # half TomTom's 200,000
INC_ATTRIBUTES = ('tags(id,icon_category,description,magnitude_of_delay,delay_in_seconds,road_category,start_time,'
                  'end_time,display_class)')
INC_USAGE_FILE = DATA / 'tomtom-incidents-usage.json'
# TomTom's icon categories -> what TPA shows (kind used by the map icon, words for people). Plain queues ('jam') are not
# shown as markers: the coloured roads already show them.
INC_KINDS = {'accident': ('accident', 'Accident'), 'brokenDownVehicle': ('broken-down', 'Broken-down vehicle'),
             'roadClosed': ('closed', 'Road closed'), 'laneClosed': ('lane-closed', 'Lane closed'),
             'roadWorks': ('roadworks', 'Roadworks'), 'fog': ('hazard', 'Fog'), 'rain': ('hazard', 'Heavy rain'),
             'ice': ('hazard', 'Ice'), 'wind': ('hazard', 'Strong wind'), 'flooding': ('hazard', 'Flooding'),
             'dangerousConditions': ('hazard', 'Dangerous conditions'), 'unknown': ('hazard', 'Incident')}
INC_RANK = ['closed', 'accident', 'broken-down', 'lane-closed', 'hazard', 'roadworks']   # first wins the icon
MAGNITUDE = {'minor': 'Slow traffic', 'moderate': 'Queuing traffic', 'major': 'Stationary traffic',
             'undefined': 'Road closed or delay not known', 'unknown': ''}
ROAD_WORDS = {'motorway': 'Motorway', 'motorway_link': 'Motorway slip road', 'trunk': 'Main road (trunk)',
              'trunk_link': 'Main road slip road', 'primary': 'A road', 'primary_link': 'A road slip road',
              'secondary': 'B road', 'secondary_link': 'B road link', 'tertiary': 'Local road', 'tertiary_link': 'Local road',
              'street': 'Street', 'service': 'Service road', 'track': 'Track'}
INC_CACHE: 'OrderedDict[tuple, tuple[list, float]]' = OrderedDict()    # (z,x,y) -> (incidents, keep until)
INC_ITEMS: dict = {}                                                   # id -> incident (for the details box)
INC_QUEUE: list = []          # 0.4.1: newest first; each entry (key, last asked for)
INC_PENDING: dict = {}        # key -> last asked for
INC_WORKERS = 4               # 0.4.1: three tiles at a time; 0.4.2: four (TomTom allows at least 5 requests a second)
INC_ABANDONED = 1 if os.environ.get('TPA_ROAD_INC_TEST_ABANDON') and _inc_override else 20   # seconds: a queued tile nobody has asked for again is dropped (the view moved on)
INC = {'requests': 0, 'lastError': '', 'consecutiveErrors': 0, 'pausedUntil': 0.0, 'lastSuccessAt': 0.0}
INC_WAKE = threading.Event()


def load_inc_usage() -> dict:
    try:
        data = json.loads(INC_USAGE_FILE.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('month'), str) and isinstance(data.get('requests'), int):
            return data
    except (OSError, ValueError):
        pass
    return {'month': month(), 'requests': 0}


INC_USAGE = load_inc_usage()


def inc_share_used_up() -> bool:
    return DAILY_SHARE and INC_USAGE.get('day') == today() and \
        INC_USAGE['requests'] - INC_USAGE.get('dayStart', 0) >= daily_share(INC_MONTHLY, INC_USAGE.get('dayStart', 0), time.time())


def inc_take() -> bool:
    """Count one incident tile against the monthly budget and today's fair share (kept on disk). Call with LOCK held."""
    if INC_USAGE['month'] != month():
        INC_USAGE.update(month=month(), requests=0)
    if INC_USAGE.get('day') != today():
        INC_USAGE.update(day=today(), dayStart=INC_USAGE['requests'])
    if INC_USAGE['requests'] >= INC_MONTHLY or inc_share_used_up():
        return False
    INC_USAGE['requests'] += 1
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = INC_USAGE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(INC_USAGE), encoding='utf-8')
        tmp.replace(INC_USAGE_FILE)
    except OSError:
        pass
    return True


def tiles_for_view(view):
    """(zoom, [(x, y)...]) for a view, or (None, []) when zoomed too far out."""
    s, w, n, e = view
    s, w, n, e = max(s, UK[0]), max(w, UK[1]), min(n, UK[2]), min(e, UK[3])
    if s >= n or w >= e:
        return None, []
    for z in range(INC_TOP_ZOOM, INC_LOW_ZOOM - 1, -1):
        k = 2 ** z
        def tx(lon):
            return min(k - 1, int((lon + 180) / 360 * k))
        def ty(lat):
            return min(k - 1, int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * k))
        xs, ys = range(tx(w), tx(e) + 1), range(ty(n), ty(s) + 1)
        if len(xs) * len(ys) <= INC_MAX_TILES:
            return z, [(x, y) for x in xs for y in ys]
    return None, []


# --- a small Mapbox Vector Tile reader (TomTom's tiles are in this format; standard library only) ---------------
def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7
        if s > 63:
            raise ValueError('bad-varint')


def _fields(b):
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, t = key >> 3, key & 7
        if t == 0:
            v, i = _varint(b, i)
        elif t == 1:
            v = b[i:i + 8]; i += 8
        elif t == 2:
            ln, i = _varint(b, i); v = b[i:i + ln]; i += ln
        elif t == 5:
            v = b[i:i + 4]; i += 4
        else:
            raise ValueError('bad-wire-type')
        if i > n:
            raise ValueError('truncated')
        yield f, t, v


def _packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i); out.append(v)
    return out


def _zz(n):
    return (n >> 1) ^ -(n & 1)


def _value(b):
    for f, t, v in _fields(b):
        if f == 1:
            return v.decode('utf-8', 'replace')
        if f == 2:
            return struct.unpack('<f', v)[0]
        if f == 3:
            return struct.unpack('<d', v)[0]
        if f == 4:
            return v - (1 << 64) if v >= (1 << 63) else v
        if f == 5:
            return v
        if f == 6:
            return _zz(v)
        if f == 7:
            return bool(v)
    return None


def _first_point(cmds):
    """The first position in a feature's geometry (enough for a marker)."""
    if len(cmds) >= 3 and cmds[0] & 7 == 1 and cmds[0] >> 3 >= 1:
        return _zz(cmds[1]), _zz(cmds[2])
    return None


def read_tile(raw: bytes) -> dict:
    """{layer name: (extent, [(props, (x, y) or None), ...])}"""
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    layers = {}
    for f, t, v in _fields(raw):
        if f != 3 or t != 2:
            continue
        name, extent, keys, values, feats = '', 4096, [], [], []
        for lf, _lt, lv in _fields(v):
            if lf == 1:
                name = lv.decode('utf-8', 'replace')
            elif lf == 5:
                extent = lv or 4096
            elif lf == 3:
                keys.append(lv.decode('utf-8', 'replace'))
            elif lf == 4:
                values.append(_value(lv))
            elif lf == 2:
                feats.append(lv)
        out = []
        for fb in feats:
            tags, geom = [], []
            for ff, _ft, fv in _fields(fb):
                if ff == 2:
                    tags = _packed(fv)
                elif ff == 4:
                    geom = _packed(fv)
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)
                     if tags[k] < len(keys) and tags[k + 1] < len(values)}
            out.append((props, _first_point(geom)))
        layers[name] = (extent, out)
    return layers


def tile_point(z, x, y, extent, px, py):
    n = 2 ** z
    lon = (x + px / extent) / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n))))
    return lat, lon


def incident_from(props, lat, lon, now) -> dict | None:
    cats = [str(props[k]) for k in sorted(props) if k == 'icon_category' or k.startswith('icon_category_')]
    words = [str(props[k]) for k in sorted(props) if (k == 'description' or k.startswith('description_')) and props[k]]
    kinds = [INC_KINDS[c] for c in cats if c in INC_KINDS]
    if not kinds:
        return None                               # e.g. only 'jam': the coloured roads already show it
    kinds.sort(key=lambda kw: INC_RANK.index(kw[0]))
    iid = re.sub(r'[^A-Za-z0-9_-]', '', str(props.get('id') or ''))[:64]
    if not iid:
        iid = f'{round(lat, 4)}_{round(lon, 4)}_{kinds[0][0]}'
    delay = props.get('delay_in_seconds')
    delay = int(delay) if isinstance(delay, (int, float)) and 0 < delay < 86400 * 7 else None
    return {'id': iid, 'kind': kinds[0][0], 'what': [kw[1] for kw in kinds], 'words': [w[:120] for w in words[:4]],
            'lat': round(lat, 5), 'lon': round(lon, 5), 'delay': delay,
            'magnitude': str(props.get('magnitude_of_delay') or '')[:12], 'road': str(props.get('road_category') or '')[:20],
            'start': str(props.get('start_time') or '')[:25], 'end': str(props.get('end_time') or '')[:25], 'seen': now}


def fetch_inc_tile(z: int, x: int, y: int) -> bytes:
    url = INC_URL.format(z=z, x=x, y=y)
    req = Request(url, headers={'TomTom-Api-Key': KEY, 'TomTom-Api-Version': '2', 'Accept': 'application/vnd.mapbox-vector-tile',
                                'Accept-Language': 'en-GB', 'Attributes': INC_ATTRIBUTES,
                                'User-Agent': f'TPA-Road-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)'})
    with urlopen(req, timeout=15, context=context()) as res:
        raw = res.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('too-large')
    return raw


def incidents_in_tile(z, x, y, raw, now) -> list:
    found = []
    for name, (extent, feats) in read_tile(raw).items():
        if 'point' not in name.lower():
            continue
        for props, pt in feats:
            if pt is None:
                continue
            lat, lon = tile_point(z, x, y, extent, *pt)
            item = incident_from(props, lat, lon, now)
            if item:
                found.append(item)
    return found


def inc_next():
    """The newest wanted tile, dropping ones for views the viewer has moved away from. Call with LOCK held."""
    now = time.time()
    while INC_QUEUE:
        key = INC_QUEUE.pop(0)
        asked = INC_PENDING.get(key)
        if asked is None:
            continue
        if now - asked > INC_ABANDONED:
            INC_PENDING.pop(key, None)
            continue
        return key
    return None


def inc_worker() -> None:
    """Fetches the incident tiles viewers are waiting for - the newest view first (0.4.1, owner 11 Nov 13:47: "it does
    take a while for the roadworks to appear": zooming in had queued tiles for every level passed on the way)."""
    while True:
        INC_WAKE.wait(timeout=1)
        while True:
            with LOCK:
                key = inc_next()
                if key is None:
                    INC_WAKE.clear()
                    break
                if time.time() < INC['pausedUntil'] or not inc_take():
                    INC_PENDING.clear(); INC_QUEUE.clear()
                    if not INC['lastError']:
                        INC['lastError'] = 'incident-budget'
                    break
                INC['requests'] += 1
            now = time.time()
            try:
                items = incidents_in_tile(*key, fetch_inc_tile(*key), now)
                with LOCK:
                    INC_CACHE[key] = (items, now + INC_KEEP_SECONDS)
                    INC_CACHE.move_to_end(key)
                    while len(INC_CACHE) > 2000:
                        INC_CACHE.popitem(last=False)
                    for it in items:
                        INC_ITEMS[it['id']] = it
                    if len(INC_ITEMS) > 20000:
                        INC_ITEMS.clear()
                    INC.update(lastError='', consecutiveErrors=0, lastSuccessAt=now)
            except HTTPError as exc:
                with LOCK:
                    INC['lastError'] = f'tomtom-http-{exc.code}'
                    INC['consecutiveErrors'] += 1
                    if exc.code in (401, 403, 429):
                        INC['pausedUntil'] = time.time() + (900 if exc.code == 429 else 6 * 3600)
                print('TomTom incident tile failed:', INC['lastError'], flush=True)   # never the key or URL
            except (URLError, TimeoutError, OSError, ValueError, IndexError, struct.error) as exc:
                with LOCK:
                    INC['lastError'] = 'incident-tile-' + type(exc).__name__
                    INC['consecutiveErrors'] += 1
            finally:
                with LOCK:
                    INC_PENDING.pop(key, None)
            time.sleep(0.05)


def same_job(a, b) -> bool:
    """0.4.1 (owner's screenshot 11 Nov 13:43, Bilton Main Road: one set of roadworks shown as two signs): TomTom gives each
    direction of a two-way road its own start point. Same kind, same times and within about 400 m = one job, one sign."""
    return a['kind'] == b['kind'] and a['start'] == b['start'] and a['end'] == b['end'] and \
        abs(a['lat'] - b['lat']) < 0.0036 and abs(a['lon'] - b['lon']) < 0.006


def inc_entities(view, now) -> tuple[list, str, bool]:
    """(map markers for this view, notice, still loading)."""
    if view is None or not KEY or not INC_MONTHLY:
        return [], '', False
    z, tiles = tiles_for_view(view)
    if z is None:
        return [], '', False
    s, w, n, e = view
    seen, out, loading, ask = set(), [], False, False
    # 0.4.2 (owner 11 Nov 14:22, York at zoom 13: about 8 s): the tiles nearest the middle of the view are fetched first
    # (put at the front of the queue last, so the middle ends up first)
    k = 2 ** z
    cx = ((w + e) / 2 + 180) / 360 * k
    cy = (1 - math.log(math.tan(math.radians((s + n) / 2)) + 1 / math.cos(math.radians((s + n) / 2))) / math.pi) / 2 * k
    tiles = sorted(tiles, key=lambda t: -((t[0] + 0.5 - cx) ** 2 + (t[1] + 0.5 - cy) ** 2))
    with LOCK:
        for x, y in tiles:
            key = (z, x, y)
            hit = INC_CACHE.get(key)
            if hit:
                for it in hit[0]:
                    if it['id'] not in seen and s <= it['lat'] <= n and w <= it['lon'] <= e:
                        seen.add(it['id']); out.append(it)
            if (not hit or hit[1] <= now) and now >= INC['pausedUntil']:
                if key in INC_PENDING:                # still wanted: move it to the front
                    try:
                        INC_QUEUE.remove(key)
                    except ValueError:
                        pass                          # already being fetched
                    else:
                        INC_QUEUE.insert(0, key)
                else:
                    INC_QUEUE.insert(0, key)
                INC_PENDING[key] = now; ask = True
                loading = True
    if ask:
        INC_WAKE.set()
    clat, clon = (s + n) / 2, (w + e) / 2
    out.sort(key=lambda it: (it['lat'] - clat) ** 2 + (it['lon'] - clon) ** 2)
    merged = []
    for it in out:
        twin = next((i for i, m in enumerate(merged[-200:], max(0, len(merged) - 200)) if same_job(it, m)), None)
        if twin is None:
            merged.append(it)
        elif it['delay'] and not merged[twin]['delay']:
            merged[twin] = it                       # keep the one that says how long the delay is
    ents = []
    for it in merged[:INC_MAX_PER_VIEW]:
        label = it['what'][0] + (f' · {round(it["delay"] / 60)} min delay' if it['delay'] and it['delay'] >= 60 else '')
        ents.append({'id': 'road:' + it['id'], 'label': label[:160], 'variant': it['kind'], 'quality': 'observed',
                     'observedAt': iso(it['seen']), 'position': {'lon': it['lon'], 'lat': it['lat'], 'headingDeg': None}})
    note = 'Zoom in to see local roadworks too' if z < 10 else ''   # county level and further out: main roads only
    return ents, note, loading


INC_MAX_PER_VIEW = 300


def parse_view(query: str):
    q = {}
    for part in (query or '').split('&'):
        k, _, v = part.partition('=')
        q[k] = v
    try:
        w, s, e, n = (float(q[k]) for k in ('west', 'south', 'east', 'north'))
    except (KeyError, ValueError):
        return None
    ok = -90 <= s < n <= 90 and -180 <= w < e <= 180
    return (s, w, n, e) if ok else None


def when_text(stamp: str) -> str:
    try:
        t = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return ''
    try:
        from zoneinfo import ZoneInfo
        t = t.astimezone(ZoneInfo('Europe/London'))
    except Exception:  # pragma: no cover
        pass
    today_ = datetime.now(t.tzinfo).date()
    if t.date() == today_:
        return t.strftime('Today %H:%M')
    return f"{t.day} {t.strftime('%b')} {t.strftime('%H:%M')}" if t.year == today_.year else f"{t.day} {t.strftime('%b %Y')}"


def inc_details(eid: str) -> dict:
    iid = eid[5:] if eid.startswith('road:') else eid
    with LOCK:
        it = INC_ITEMS.get(iid)
    if not it:
        return {'rows': []}
    what = ' · '.join(dict.fromkeys(it['what']))
    rows = [{'label': 'What', 'value': what}]
    words = [w for w in dict.fromkeys(it['words']) if w and w.lower() not in what.lower()]
    if words:
        rows.append({'label': 'Details', 'value': ' · '.join(words)[:120]})
    if it['road'] in ROAD_WORDS:
        rows.append({'label': 'Road', 'value': ROAD_WORDS[it['road']]})
    if MAGNITUDE.get(it['magnitude']):
        rows.append({'label': 'Traffic', 'value': MAGNITUDE[it['magnitude']]})
    if it['delay']:
        m = max(1, round(it['delay'] / 60))
        rows.append({'label': 'Delay', 'value': f'About {m} min' if m < 90 else f'About {round(m / 60, 1):g} hours'})
    if when_text(it['start']):
        rows.append({'label': 'Started', 'value': when_text(it['start'])})
    if when_text(it['end']):
        rows.append({'label': 'Expected to end', 'value': when_text(it['end'])})
    rows.append({'label': 'Source', 'value': 'TomTom traffic (live)', 'more': True})
    summary = {'roadworks': 'Roadworks in progress here now.', 'closed': 'The road is closed here.',
               'accident': 'An accident has been reported here.', 'lane-closed': 'A lane is closed here.',
               'broken-down': 'A broken-down vehicle has been reported here.'}.get(it['kind'], f'{it["what"][0]} reported here.')
    return {'id': eid, 'summary': summary, 'rows': rows}


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


def snapshot(view=None) -> dict:
    now = time.time()
    h, notice = health()
    ents, inc_note, loading = inc_entities(view, now) if h != 'unavailable' else ([], '', False)
    frame = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
             'observedAt': iso(now), 'receivedAt': iso(now), 'expiresAt': iso(now + 300),
             'nextPollAfterMs': 2000 if loading else 60000, 'attribution': ATTRIBUTION, 'entities': ents,
             'overlay': {'type': 'raster', 'tiles': f'/api/v4/components/{COMPONENT}/tiles/{{z}}/{{x}}/{{y}}.png',
                         'minZoom': MIN_ZOOM, 'maxZoom': MAX_ZOOM, 'opacity': 0.85, 'refreshSeconds': REFRESH_SECONDS,   # owner 31 Oct 22:29: every 10 minutes is plenty
                         'tileSize': TILE_SIZE,
                         'opacityByZoom': [[0, 0.85], [CITY_ZOOM, 0.6], [STREET_ZOOM, 0.5], [STREET_ZOOM + 1, 0.4]],
                         'hideColours': FREE_FLOW.get(STYLE, []), 'hideFromZoom': STREET_ZOOM, 'hideTolerance': 40}}
    if notice or inc_note:
        frame['notice'] = notice or inc_note
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
        split = urlsplit(self.path)
        path = split.path
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
                                       'monthlySafetyCap': MONTHLY_CAP,
                                       'incidents': {'tilesThisRun': INC['requests'], 'lastError': INC['lastError'],
                                                     'tilesThisMonth': INC_USAGE['requests'] if INC_USAGE['month'] == month() else 0,
                                                     'monthlyBudget': INC_MONTHLY, 'known': len(INC_ITEMS),
                                                     'pausedForSeconds': max(0, round(INC['pausedUntil'] - time.time()))}})
        if path == '/snapshot':
            return self.json(200, snapshot(parse_view(split.query)))
        if path == '/details':
            want = ''
            for part in split.query.split('&'):
                k, _, v = part.partition('=')
                if k == 'id':
                    from urllib.parse import unquote
                    want = unquote(v)[:140]
            return self.json(200, inc_details(want))
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
    for _ in range(INC_WORKERS):
        threading.Thread(target=inc_worker, daemon=True).start()
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
