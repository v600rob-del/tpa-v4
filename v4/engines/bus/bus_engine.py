"""TPA v4 BUS ENGINE 0.4 — separate process, bus lane only.

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

0.4.0 road map: with TPA_BUS_ROAD_MAP=download (the server sets this) the engine fetches the
free Ordnance Survey "OS Open Roads" map in a low-priority helper (road_map.py, ~1 GB about
twice a year, rebuilt only when OS publishes a new edition) and, between reports, sends each
bus's road ahead as a "path" so HQ glides it along the real road. Without the road map, or
when a bus isn't clearly on a road, the straight-line "heading" movement of 0.3 is used.
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import os
import re
import signal
import ssl
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError, iterparse

ENGINE_VERSION = '0.4.2'
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
GLIDE_SECONDS = 90           # how long a bus is carried forward after its last report
STEADY_SECONDS = 60          # ...at steady speed, then easing to a stop (HQ "slowToStop")
ROAD_MODE = os.environ.get('TPA_BUS_ROAD_MAP', 'off').strip().lower()      # 'download' on the server
DATA_DIR = Path(os.environ.get('TPA_BUS_DATA_DIR', '') or Path(__file__).resolve().parent / 'data')
ROAD_CHECK_DAYS = 7          # ask OS whether a new edition exists (a tiny request) once a week
ROAD_RETRY_HOURS = 6
ROAD_VIEW_SPAN = 0.12        # follow roads only when zoomed in to town/city level (view under ~13 km tall);
                             # further out a bus is a dot and the straight glide looks the same (0.4.1)
INLINE_BUDGET = 0.25         # seconds a snapshot may spend working out roads; the rest wait for the helper thread

KEY = (os.environ.get('TPA_BODS_API_KEY') or '').strip()
_test_area = os.environ.get('TPA_BUS_TEST_AREA_URL', '').strip()
_test_bulk = os.environ.get('TPA_BUS_TEST_BULK_URL', '').strip()
_test_roads = os.environ.get('TPA_BUS_ROADS_TEST_API', '').strip()
for _u in (_test_area, _test_bulk, _test_roads):
    if _u and not re.match(r'^http://127\.0\.0\.1:\d+/', _u):
        raise SystemExit('Test URLs must point at 127.0.0.1 only')
AREA_URL = _test_area or BODS_AREA_URL
BULK_URL = _test_bulk or BODS_BULK_URL

LOCK = threading.Lock()
STATE = {
    'vehicles': [], 'fetchedAt': 0.0, 'coverage': None, 'lastAttemptAt': 0.0,
    'lastError': '' if KEY else 'no-key', 'lastDemandAt': 0.0, 'wanted': None,
    'downloadBytes': 0, 'downloadsThisRun': 0, 'consecutiveErrors': 0, 'reportAgeMedian': None, 'busesWithSpeed': 0,
    'roadMap': 'off' if ROAD_MODE != 'download' else 'starting', 'roadMapDetail': '', 'roadMapError': '',
}
WAKE = threading.Event()

_spec = importlib.util.spec_from_file_location('tpa_bus_road_map', Path(__file__).resolve().parent / 'road_map.py')
road_map = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(road_map)
ROADS = {'map': None, 'licence': ''}
PATHS: dict = {}             # bus key -> (report time, motion or None)
PENDING: deque = deque()
PENDING_SET: set = set()
PATH_WAKE = threading.Event()
# 0.4.1 (server log 30 Oct: downloads took 5 s normally, 17-42 s and timed out while road work ran):
# the BODS download always comes first. Road work waits while it runs, and a map rebuild is paused.
FETCHING = threading.Event()
BUILDER = {'proc': None}
TIMING = {'fetch': None, 'pathMs': None}
VIEWS: list = []             # (time, view) recently looked at closely enough for roads (0.4.2)
VIEW_MEMORY = 180


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


HISTORY: dict = {}            # bus key -> recent reports [(recorded, lat, lon), ...] (0.4.2)
SPEED_WINDOW = 180           # seconds of recent reports used for a bus's speed
HISTORY_KEEP = 8


def _bearing(lat1, lon1, lat2, lon2) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return round(math.degrees(math.atan2(y, x)) % 360, 1)


def add_motion(new: list, old: list) -> None:
    """Speed and direction for each bus from its own recent reports.
    0.3.0 used only the last two reports, so a bus whose last two reports were at a stop froze,
    then jumped 100-200 m (server check 31 Oct 00:22: 74 of 184 buses had no movement).
    0.4.2: speed = distance covered over the last ~3 minutes of reports / time taken (stops
    included, so a bus keeps its usual pace through a short stop and slows to a halt when it is
    really parked). Direction = where it really went, unless the bus company's own direction
    agrees within 60 degrees (that one is more up to date at corners). Limits: 0.5-35 m/s."""
    before = {v['key']: v for v in old}
    for v in new:
        h = HISTORY.get(v['key'])
        if h is None:
            p = before.get(v['key'])
            h = HISTORY[v['key']] = [(p['recorded'], p['lat'], p['lon'])] if p else []
        if not h or v['recorded'] > h[-1][0]:
            h.append((v['recorded'], v['lat'], v['lon']))
            del h[:-HISTORY_KEEP]
        recent = [r for r in h if v['recorded'] - r[0] <= SPEED_WINDOW and r[0] <= v['recorded']]
        if len(recent) < 2 and len(h) >= 2 and v['recorded'] - h[-2][0] <= 300:
            recent = h[-2:]
        if len(recent) < 2:
            continue
        span = recent[-1][0] - recent[0][0]
        dist = sum(metres(a[1], a[2], b[1], b[2]) for a, b in zip(recent, recent[1:]))
        if span < 5 or dist < 10:
            continue
        speed = dist / span
        if not 0.5 <= speed <= 35 or any(metres(a[1], a[2], b[1], b[2]) / max(1, b[0] - a[0]) > 40 for a, b in zip(recent, recent[1:])):
            continue
        went = None
        for a, b in zip(reversed(h[:-1]), reversed(h)):   # the latest real movement of 10 m or more
            if v['recorded'] - b[0] > 300:
                break
            if metres(a[1], a[2], b[1], b[2]) >= 10:
                went = _bearing(a[1], a[2], b[1], b[2])
                break
        v['speed'] = round(speed, 2)
        if went is not None and (v['bearing'] is None or min(abs(v['bearing'] - went) % 360, 360 - abs(v['bearing'] - went) % 360) > 60):
            v['bearing'] = went


def fetch_once(area) -> None:
    """One BODS download, with everything else in this engine paused so it gets the processor."""
    FETCHING.set()
    proc = BUILDER['proc']
    paused = False
    if proc is not None and proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGSTOP); paused = True
        except OSError:
            pass
    started = time.time()
    try:
        _fetch_once(area)
    finally:
        if paused:
            try:
                os.kill(proc.pid, signal.SIGCONT)
            except OSError:
                pass
        FETCHING.clear()
        TIMING['fetch'] = round(time.time() - started, 1)
        prepare_paths()
        PATH_WAKE.set()


def prepare_paths() -> None:
    """0.4.2: work out the road ahead for every moving bus in areas people are looking at as soon
    as the reports arrive, so HQ gets the road path first time (server check 31 Oct: 12 buses
    switched from straight to road movement on the same report, which looks like a sideways slide)."""
    if ROADS['map'] is None:
        return
    now = time.time()
    with LOCK:
        VIEWS[:] = [(t, w) for t, w in VIEWS if now - t < VIEW_MEMORY]
        views = [w for _, w in VIEWS]
        if not views:
            return
        for v in STATE['vehicles']:
            if not v.get('speed') or v.get('bearing') is None or now - v['recorded'] > DROP_AFTER:
                continue
            if not any(s - 0.01 <= v['lat'] <= n + 0.01 and w - 0.01 <= v['lon'] <= e + 0.01 for s, w, n, e in views):
                continue
            known = PATHS.get(v['key'])
            if known and known[0] == v['recorded']:
                continue
            if (v['key'], v['recorded']) not in PENDING_SET and len(PENDING) < 3000:
                PENDING_SET.add((v['key'], v['recorded']))
                PENDING.append(v)


def _fetch_once(area) -> None:
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
        ages = sorted(started - v['recorded'] for v in vehicles if v['recorded'] <= started)
        with LOCK:
            add_motion(vehicles, STATE['vehicles'])
            live = {v['key'] for v in vehicles}
            for k in [k for k in PATHS if k not in live]:
                del PATHS[k]
            for k in [k for k in HISTORY if k not in live]:
                del HISTORY[k]
            STATE['reportAgeMedian'] = round(ages[len(ages) // 2]) if ages else None
            STATE['busesWithSpeed'] = sum(1 for v in vehicles if v.get('speed'))
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
             'nextPollAfterMs': 15000, 'attribution': ATTRIBUTION + (' · roads: ' + ROADS['licence'] if ROADS['licence'] else ''),
             'entities': []}
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
    deadline = time.monotonic() + INLINE_BUDGET
    roads = north - south <= ROAD_VIEW_SPAN
    if roads:
        with LOCK:
            VIEWS.append((now, view))
            del VIEWS[:-50]
    why = {'road': 0, 'straight-pending': 0, 'straight-no-road-match': 0, 'straight-zoomed-out': 0, 'still-no-speed': 0}
    for _, v, age in inside[:MAX_PER_VIEW]:
        route = v['route'] or 'Bus'
        label = (f'Bus {route}' if v['route'] else 'Bus') + (f' to {v["destination"]}' if v['destination'] else '')
        entity = {
            'id': ('bus:' + v['key'])[:128], 'label': label[:160],
            'badge': re.sub(r'[^A-Za-z0-9]', '', v['route'])[:8] or None,
            'position': {'lon': v['lon'], 'lat': v['lat'], 'headingDeg': v['bearing']},
            'quality': 'stale' if age > STALE_AFTER else 'observed', 'observedAt': iso(v['recorded'])}
        if v.get('speed') and v['bearing'] is not None and age <= STALE_AFTER:   # 0.4.2: old reports stay put
            # 0.3.1: reports reach us 20-40 s late, so buses are carried forward up to 90 s, easing
            # to a stop, and corrected by a short slide when the next real report arrives.
            # 0.4.0: along the real road ahead when the road map knows it.
            entity['motion'] = motion_for(v, deadline, roads)
            if entity['motion']['mode'] == 'path':
                why['road'] += 1
            elif not roads or ROADS['map'] is None:
                why['straight-zoomed-out'] += 1
            else:
                with LOCK:
                    known = PATHS.get(v['key'])
                why['straight-no-road-match' if known and known[0] == v['recorded'] else 'straight-pending'] += 1
        else:
            why['still-no-speed'] += 1
        frame['entities'].append(entity)
    with LOCK:
        STATE['lastView'] = why
    if not frame['entities'] and MODE == 'area' and not covers(coverage, view):
        frame['notice'] = 'Loading buses for this area…'
    elif len(inside) > MAX_PER_VIEW and not notice:
        frame['notice'] = f'Showing nearest {MAX_PER_VIEW} · zoom in for all'
    return frame


def open_road_map() -> bool:
    path = DATA_DIR / 'roads.sqlite'
    if not path.exists():
        return False
    try:
        new = road_map.RoadMap(str(path))
    except Exception as exc:   # damaged or old format: the next check rebuilds it
        with LOCK:
            STATE['roadMapError'] = ('open: ' + type(exc).__name__ + ' ' + str(exc))[:100]
        return False
    old = ROADS['map']
    with LOCK:
        ROADS['map'] = new
        ROADS['licence'] = str(new.meta.get('licence', ''))
        STATE['roadMap'] = 'ready'
        STATE['roadMapDetail'] = f"{new.meta.get('links', 0)} road pieces"
        PATHS.clear()
    if old:
        old.close()
    print('Road map ready:', STATE['roadMapDetail'], flush=True)
    return True


def refresh_road_map() -> bool:
    """Run road_map.py fetch as a low-priority child; True if it finished without error."""
    cmd = [sys.executable, str(Path(__file__).resolve().parent / 'road_map.py'), 'fetch', str(DATA_DIR)]
    env = dict(os.environ)
    if _test_roads:
        env['TPA_BUS_ROADS_TEST_API'] = _test_roads
    ok = True
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env) as proc:
        BUILDER['proc'] = proc
        for line in proc.stdout:
            if not line.startswith('ROADMAP '):
                continue
            try:
                info = json.loads(line[8:])
            except ValueError:
                continue
            st = info.get('state', '')
            with LOCK:
                if st == 'error':
                    STATE['roadMapError'] = str(info.get('error', ''))[:120]
                    ok = False
                elif st in ('downloading', 'building', 'checking'):
                    detail = f"{info['percent']}%" if 'percent' in info else (f"{info['linksRead']} roads read" if 'linksRead' in info else '')
                    if ROADS['map'] is None:
                        STATE['roadMap'] = st
                    STATE['roadMapDetail'] = (st + ' ' + detail).strip() if ROADS['map'] else detail
            if st in ('downloading', 'building') and info.get('percent', 0) in (0, 25, 50, 75) or st in ('built', 'error'):
                print('Road map:', line[8:].strip(), flush=True)
    BUILDER['proc'] = None
    return ok and proc.returncode == 0


def road_keeper() -> None:
    open_road_map()
    if ROAD_MODE != 'download':
        return
    time.sleep(float(os.environ.get('TPA_BUS_ROAD_START_DELAY', '30')))   # let the server settle after a restart
    while True:
        ok = refresh_road_map()
        if ok:
            with LOCK:
                STATE['roadMapError'] = ''
            stamp = (DATA_DIR / 'roads.sqlite')
            if ROADS['map'] is None or (stamp.exists() and stamp.stat().st_mtime > ROADS['map'].meta.get('builtAt', 0) + 60):
                open_road_map()
            elif ROADS['map'] is not None:
                with LOCK:
                    STATE['roadMap'] = 'ready'
                    STATE['roadMapDetail'] = f"{ROADS['map'].meta.get('links', 0)} road pieces"
        else:
            with LOCK:
                if ROADS['map'] is None:
                    STATE['roadMap'] = 'unavailable'
        time.sleep((ROAD_CHECK_DAYS * 86400) if ok else ROAD_RETRY_HOURS * 3600)


def road_motion(v: dict):
    """Path along the road ahead for this report, or None (straight-line movement is used instead)."""
    rm = ROADS['map']
    if rm is None or not v.get('speed') or v.get('bearing') is None:
        return None
    ease = GLIDE_SECONDS - STEADY_SECONDS
    metres = v['speed'] * (STEADY_SECONDS + ease / 2)
    t0 = time.perf_counter()
    try:
        pts = rm.ahead(v['lon'], v['lat'], v['bearing'], metres)
    except Exception as exc:   # one odd bus must never break the layer
        with LOCK:
            STATE['roadMapError'] = ('path: ' + type(exc).__name__)[:100]
        return None
    ms = (time.perf_counter() - t0) * 1000
    TIMING['pathMs'] = round(ms if TIMING['pathMs'] is None else TIMING['pathMs'] * 0.98 + ms * 0.02, 2)
    if not pts or len(pts) < 2:
        return None
    return {'mode': 'path', 'points': [[round(a, 6), round(b, 6)] for a, b in pts],
            'arriveAt': iso(v['recorded'] + GLIDE_SECONDS), 'maxSeconds': GLIDE_SECONDS,
            'speedMps': v['speed'], 'slowToStop': True}


def path_worker() -> None:
    while True:
        PATH_WAKE.wait(timeout=30)
        PATH_WAKE.clear()
        while PENDING:
            while FETCHING.is_set():          # the bus download comes first
                time.sleep(0.2)
            with LOCK:
                if not PENDING:
                    break
                v = PENDING.popleft()
                PENDING_SET.discard((v['key'], v['recorded']))
            m = road_motion(v)
            with LOCK:
                PATHS[v['key']] = (v['recorded'], m)


def motion_for(v: dict, deadline: float, roads: bool = True):
    """The best movement for this bus: its road ahead if known (or quick to work out), else straight on."""
    heading = {'mode': 'heading', 'speedMps': v['speed'], 'maxSeconds': GLIDE_SECONDS, 'slowToStop': True}
    if ROADS['map'] is None or not roads:
        return heading
    with LOCK:
        known = PATHS.get(v['key'])
    if known and known[0] == v['recorded']:
        return known[1] or heading
    if time.monotonic() < deadline and not FETCHING.is_set():
        m = road_motion(v)
        with LOCK:
            PATHS[v['key']] = (v['recorded'], m)
        return m or heading
    with LOCK:
        if (v['key'], v['recorded']) not in PENDING_SET:
            PENDING_SET.add((v['key'], v['recorded']))
            PENDING.append(v)
    PATH_WAKE.set()
    return heading


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
                                        'reportAgeMedianSeconds': STATE['reportAgeMedian'], 'busesWithSpeed': STATE['busesWithSpeed'],
                                        'bodsDownloadsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS,
                                        'roadMap': STATE['roadMap'], 'roadMapDetail': STATE['roadMapDetail'],
                                        'roadMapError': STATE['roadMapError'],
                                        'roadPathsKnown': sum(1 for x in PATHS.values() if x[1]),
                                        'roadTilesLoaded': len(ROADS['map'].tiles) if ROADS['map'] else 0,
                                        'lastFetchSeconds': TIMING['fetch'], 'roadPathMs': TIMING['pathMs'],
                                        'lastView': STATE.get('lastView')})
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
    threading.Thread(target=road_keeper, daemon=True).start()
    threading.Thread(target=path_worker, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA BUS ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('BODS key: ' + ('set for this session' if KEY else 'NOT SET - no bus data will be fetched'), flush=True)
        print(f'Mode: {MODE}. Asks BODS at most every {POLL_SECONDS}s, only while the map is open.', flush=True)
        print('Road map: ' + ('downloads OS Open Roads when needed' if ROAD_MODE == 'download' else 'off (buses glide in straight lines)'), flush=True)
        print('Keep this window open. Close it to stop buses only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
