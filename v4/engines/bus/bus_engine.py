"""TPA v4 BUS ENGINE 0.2 — separate process, bus lane only.

Data: Department for Transport Bus Open Data Service (BODS), SIRI-VM live
vehicle locations. BODS confirmed in writing (ticket BSD-1280, Oct 2026) that
free use in a third-party app is fine and that a shared server cache is the
recommended approach.

Two ways of fetching, chosen by TPA_BUS_MODE:
- "area" (default, for testing on a home PC): asks BODS only for the area
  people are looking at, plus a margin, at most every POLL_SECONDS (30 s).
  Small downloads; this is how the owner's v3 site worked.
- "bulk" (for a proper server with many visitors across the country): one
  whole-GB compressed file at most every POLL_SECONDS, as BODS recommended
  for national maps. Large downloads; not for home broadband.

In both modes the engine fetches ONLY while someone is viewing (HQ asked in
the last few minutes), and every viewer is served from the same shared copy.

Safety: no key -> zero requests. Key read from TPA_BODS_API_KEY only, never
logged, stored or returned. 127.0.0.1 only. Read-only. Positions are shown
where BODS last reported them (never invented or animated); older reports are
marked stale, very old ones dropped. Standard library only.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import ssl
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError, iterparse

ENGINE_VERSION = '0.3.0'
COMPONENT = 'bus'
CONTRACT = '1.0'
BODS_AREA_URL = 'https://data.bus-data.dft.gov.uk/api/v1/datafeed/'
BODS_BULK_URL = 'https://data.bus-data.dft.gov.uk/avl/download/bulk_archive'
ATTRIBUTION = "Bus data sourced via the Department for Transport's Bus Open Data Service (BODS)"

MODE = 'bulk' if os.environ.get('TPA_BUS_MODE', 'area').strip().lower() == 'bulk' else 'area'
POLL_SECONDS = max(10, int(os.environ.get('TPA_BUS_POLL_SECONDS', '30') or 30))
DEMAND_WINDOW = max(5, int(os.environ.get('TPA_BUS_DEMAND_SECONDS', '180') or 180))  # fetch only while viewed recently
STALE_AFTER = 5 * 60         # report older than this -> marked stale
DROP_AFTER = 30 * 60         # report older than this -> not shown
FRAME_TTL = 90               # HQ treats a frame as stale after this
MAX_DOWNLOAD = 32_000_000    # same safety size as v3
MAX_XML_BYTES = 600_000_000
MAX_PER_VIEW = 400           # keeps the map responsive on phones and tablets
MAX_VIEW_SPAN = 1.6          # degrees of latitude; wider views ask the user to zoom in
AREA_PADDING = 0.15          # degrees added around the viewed area in "area" mode
MAX_AREA_SPAN = 3.0          # never ask BODS for an area bigger than this (degrees)
GB = (49.8, -8.7, 61.0, 2.0)  # south, west, north, east

KEY = (os.environ.get('TPA_BODS_API_KEY') or '').strip()
_test_area = os.environ.get('TPA_BUS_TEST_AREA_URL', '').strip()
_test_bulk = os.environ.get('TPA_BUS_TEST_BULK_URL', '').strip()
for _u in (_test_area, _test_bulk):
    if _u and not re.match(r'^http://127\.0\.0\.1:\d+/', _u):
        raise SystemExit('Test URLs must point at 127.0.0.1 only')
AREA_URL = _test_area or BODS_AREA_URL
BULK_URL = _test_bulk or BODS_BULK_URL

LOCK = threading.Lock()
STATE = {
    'vehicles': [], 'fetchedAt': 0.0, 'coverage': None, 'lastAttemptAt': 0.0,
    'lastError': '' if KEY else 'no-key', 'lastDemandAt': 0.0, 'wanted': None,
    'downloadBytes': 0, 'downloadsThisRun': 0, 'consecutiveErrors': 0,
}
WAKE = threading.Event()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_time(text: str) -> float | None:
    try:
        t = datetime.fromisoformat(text.strip().replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.timestamp()
    except (ValueError, AttributeError):
        return None


def clean(text: str, limit: int) -> str:
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', text or '').strip()[:limit]


FIELDS = ('Latitude', 'Longitude', 'LineRef', 'PublishedLineName', 'VehicleRef', 'OperatorRef',
          'DestinationName', 'RecordedAtTime', 'Bearing')


class GuardedStream(io.RawIOBase):
    """Refuses XML with DOCTYPE/ENTITY declarations (blocks 'billion laughs' tricks)."""
    def __init__(self, raw):
        self.raw, self.head, self.checked = raw, b'', False

    def readable(self):
        return True

    def readinto(self, buf):
        data = self.raw.read(len(buf))
        if not self.checked:
            self.head += data[:4096]
            if b'<!DOCTYPE' in self.head.upper() or b'<!ENTITY' in self.head.upper():
                raise ValueError('xml-declarations-not-allowed')
            if len(self.head) >= 4096 or not data:
                self.checked = True
        buf[:len(data)] = data
        return len(data)


def parse_siri(stream) -> list[dict]:
    """Stream-parse SIRI-VM XML without loading the whole file into memory."""
    out, seen = [], set()
    for _event, elem in iterparse(io.BufferedReader(GuardedStream(stream)), events=('end',)):
        if elem.tag.rsplit('}', 1)[-1] != 'VehicleActivity':
            continue
        vals = {}
        for child in elem.iter():
            name = child.tag.rsplit('}', 1)[-1]
            if name in FIELDS and child.text and name not in vals:
                vals[name] = child.text.strip()
        elem.clear()
        try:
            lat, lon = float(vals.get('Latitude', 'nan')), float(vals.get('Longitude', 'nan'))
        except ValueError:
            continue
        if not (GB[0] <= lat <= GB[2] and GB[1] <= lon <= GB[3]):
            continue
        when = parse_time(vals.get('RecordedAtTime', ''))
        operator, vehicle = clean(vals.get('OperatorRef', ''), 30), clean(vals.get('VehicleRef', ''), 60)
        if when is None or not vehicle:
            continue
        key = f'{operator}:{vehicle}'
        if key in seen:
            continue
        seen.add(key)
        bearing = None
        try:
            b = float(vals.get('Bearing', ''))
            if math.isfinite(b) and 0 <= b <= 360:
                bearing = round(b, 1)
        except ValueError:
            pass
        out.append({'key': key, 'lat': round(lat, 6), 'lon': round(lon, 6), 'bearing': bearing,
                    'route': clean(vals.get('PublishedLineName') or vals.get('LineRef') or '', 12),
                    'destination': clean(vals.get('DestinationName', ''), 80), 'recorded': when})
    return out


def context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def download(url: str) -> bytes:
    req = Request(url, headers={'User-Agent': f'TPA-Bus-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)',
                                'Accept': 'application/xml, application/zip, */*'})
    with urlopen(req, timeout=60, context=context()) as res:
        if int(res.headers.get('Content-Length', '0') or 0) > MAX_DOWNLOAD:
            raise ValueError('too-large')
        raw = res.read(MAX_DOWNLOAD + 1)
    if len(raw) > MAX_DOWNLOAD:
        raise ValueError('too-large')
    return raw


def metres(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(min(1.0, math.sqrt(a)))


def add_motion(new: list, old: list) -> None:
    """0.3.0: BODS gives direction but not speed. Speed comes from the same bus's last two
    real reports (5 s to 5 min apart, 0.5-35 m/s); otherwise the bus simply has no motion."""
    before = {v['key']: v for v in old}
    for v in new:
        p = before.get(v['key'])
        if not p:
            continue
        if v['recorded'] == p['recorded']:          # same report downloaded again: keep what we knew
            if p.get('speed'):
                v['speed'] = p['speed']
                if v['bearing'] is None:
                    v['bearing'] = p['bearing']
            continue
        dt = v['recorded'] - p['recorded']
        if not 5 <= dt <= 300:
            continue
        dist = metres(p['lat'], p['lon'], v['lat'], v['lon'])
        speed = dist / dt
        if dist < 10 or not 0.5 <= speed <= 35:
            continue
        v['speed'] = round(speed, 2)
        if v['bearing'] is None:
            y = math.sin(math.radians(v['lon'] - p['lon'])) * math.cos(math.radians(v['lat']))
            x = (math.cos(math.radians(p['lat'])) * math.sin(math.radians(v['lat'])) -
                 math.sin(math.radians(p['lat'])) * math.cos(math.radians(v['lat'])) * math.cos(math.radians(v['lon'] - p['lon'])))
            v['bearing'] = round(math.degrees(math.atan2(y, x)) % 360, 1)


def fetch_once(area) -> None:
    """One BODS download. Errors keep the previous snapshot (shown with its true age)."""
    started = time.time()
    try:
        if MODE == 'bulk':
            raw = download(BULK_URL + '?' + urlencode({'api_key': KEY}))
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith('.xml')]
                if not names or zf.getinfo(names[0]).file_size > MAX_XML_BYTES:
                    raise ValueError('bad-archive')
                with zf.open(names[0]) as xml:
                    vehicles = parse_siri(xml)
            coverage = GB
        else:
            s, w, n, e = area
            raw = download(AREA_URL + '?' + urlencode({'api_key': KEY, 'boundingBox': f'{w:.5f},{s:.5f},{e:.5f},{n:.5f}'}))
            vehicles = parse_siri(io.BytesIO(raw))
            coverage = area
        with LOCK:
            add_motion(vehicles, STATE['vehicles'])
            STATE.update(vehicles=vehicles, fetchedAt=started, coverage=coverage, lastError='',
                         downloadBytes=len(raw), consecutiveErrors=0)
            STATE['downloadsThisRun'] += 1
        print(f'BODS {MODE} OK: {len(vehicles)} buses, {len(raw)//1024} KB, {time.time()-started:.1f}s', flush=True)
    except HTTPError as exc:
        _fail(f'bods-http-{exc.code}')
    except (URLError, TimeoutError, OSError) as exc:
        _fail('network-' + type(exc).__name__)
    except (zipfile.BadZipFile, ParseError, ValueError) as exc:
        _fail('bad-data-' + (str(exc)[:30] if isinstance(exc, ValueError) else type(exc).__name__))


def _fail(code: str) -> None:
    with LOCK:  # never the URL: it contains the key
        STATE['lastError'] = code[:60]
        STATE['consecutiveErrors'] += 1
    print('BODS fetch failed:', code, flush=True)


def covers(outer, inner) -> bool:
    return bool(outer) and outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def padded(view):
    s, w, n, e = view
    s, w, n, e = s - AREA_PADDING, w - AREA_PADDING, n + AREA_PADDING, e + AREA_PADDING
    if n - s > MAX_AREA_SPAN:  # keep requests modest, centred on the view
        c = (n + s) / 2; s, n = c - MAX_AREA_SPAN / 2, c + MAX_AREA_SPAN / 2
    if e - w > MAX_AREA_SPAN * 1.6:
        c = (e + w) / 2; w, e = c - MAX_AREA_SPAN * 0.8, c + MAX_AREA_SPAN * 0.8
    return max(GB[0], s), max(GB[1], w), min(GB[2], n), min(GB[3], e)


def poller() -> None:
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        if not KEY:
            continue
        now = time.time()
        with LOCK:
            wanted = STATE['wanted']
            viewed = now - STATE['lastDemandAt'] < DEMAND_WINDOW
            # After repeated failures, back off gently (up to 5 minutes) rather than hammer BODS.
            wait = POLL_SECONDS * min(10, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else POLL_SECONDS
            due = now - STATE['lastAttemptAt'] >= wait
            uncovered = MODE == 'area' and wanted is not None and not covers(STATE['coverage'], wanted) \
                and now - STATE['lastAttemptAt'] >= 3  # a newly viewed area need not wait the full 30 s
            go = viewed and (due or uncovered) and (MODE == 'bulk' or wanted is not None)
            area = padded(wanted) if (go and MODE == 'area') else None
            if go:
                STATE['lastAttemptAt'] = now
        if go:
            fetch_once(area)


def health(now: float) -> tuple[str, str]:
    if not KEY:
        return 'unavailable', 'Bus key not set up on this PC'
    if STATE['lastError'] in ('bods-http-401', 'bods-http-403'):
        return 'unavailable', 'BODS did not accept the key'
    if not STATE['fetchedAt']:
        if STATE['consecutiveErrors']:
            return 'unavailable', 'BODS not responding · retrying'
        # "ready" while connecting, so HQ asks for the viewed area (area mode needs to know it).
        return 'ready', 'Connecting to BODS…'
    if now - STATE['fetchedAt'] > FRAME_TTL:
        return 'stale', 'BODS update delayed · showing last positions'
    return 'ready', ''


def note_demand(view=None) -> None:
    with LOCK:
        STATE['lastDemandAt'] = time.time()
        if view is not None:
            STATE['wanted'] = view
    WAKE.set()


def snapshot(view) -> dict:
    now = time.time()
    if view is not None and view[2] - view[0] <= MAX_VIEW_SPAN:
        note_demand(view)
    else:
        note_demand()
    with LOCK:
        vehicles, fetched, coverage = STATE['vehicles'], STATE['fetchedAt'], STATE['coverage']
        h, notice = health(now)
    received = fetched or now
    frame = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
             'observedAt': iso(received), 'receivedAt': iso(received), 'expiresAt': iso(received + FRAME_TTL),
             'nextPollAfterMs': 15000, 'attribution': ATTRIBUTION, 'entities': []}
    if view is None or view[2] - view[0] > MAX_VIEW_SPAN:
        frame['notice'] = 'Zoom in to see buses'
        return frame
    if notice:
        frame['notice'] = notice
    if not fetched:
        return frame
    south, west, north, east = view
    clat, clon = (south + north) / 2, (west + east) / 2
    inside = []
    for v in vehicles:
        age = now - v['recorded']
        if age > DROP_AFTER or not (south <= v['lat'] <= north and west <= v['lon'] <= east):
            continue
        inside.append(((v['lat'] - clat) ** 2 + (v['lon'] - clon) ** 2, v, age))
    inside.sort(key=lambda t: t[0])  # nearest the middle of the screen first
    for _, v, age in inside[:MAX_PER_VIEW]:
        route = v['route'] or 'Bus'
        label = (f'Bus {route}' if v['route'] else 'Bus') + (f' to {v["destination"]}' if v['destination'] else '')
        entity = {
            'id': ('bus:' + v['key'])[:128], 'label': label[:160],
            'badge': re.sub(r'[^A-Za-z0-9]', '', v['route'])[:8] or None,
            'position': {'lon': v['lon'], 'lat': v['lat'], 'headingDeg': v['bearing']},
            'quality': 'stale' if age > STALE_AFTER else 'observed', 'observedAt': iso(v['recorded'])}
        if v.get('speed') and v['bearing'] is not None:
            # Roads bend, so a bus is only carried forward for 30 s after its real report.
            entity['motion'] = {'mode': 'heading', 'speedMps': v['speed'], 'maxSeconds': 30}
        frame['entities'].append(entity)
    if not frame['entities'] and MODE == 'area' and not covers(coverage, view):
        frame['notice'] = 'Loading buses for this area…'
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
    server_version = 'TPA-Bus-Engine'
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
            note_demand()  # HQ only asks while a page is open, so this counts as "being viewed"
            now = time.time()
            with LOCK:
                h, notice = health(now)
                return self.reply(200, {'component': COMPONENT, 'engine': 'tpa-bus-engine', 'engineVersion': ENGINE_VERSION,
                                        'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice, 'mode': MODE,
                                        'keyConfigured': bool(KEY), 'lastError': STATE['lastError'],
                                        'lastSuccessAt': iso(STATE['fetchedAt']) if STATE['fetchedAt'] else None,
                                        'busesInLastDownload': len(STATE['vehicles']), 'lastDownloadKB': STATE['downloadBytes'] // 1024,
                                        'bodsDownloadsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS})
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_BUS_ENGINE_PORT', '8781'))
    threading.Thread(target=poller, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA BUS ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('BODS key: ' + ('set for this session' if KEY else 'NOT SET - no bus data will be fetched'), flush=True)
        print(f'Mode: {MODE}. Asks BODS at most every {POLL_SECONDS}s, only while the map is open.', flush=True)
        print('Keep this window open. Close it to stop buses only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
