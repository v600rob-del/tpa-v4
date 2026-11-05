"""TPA v4 TRAIN ENGINE 0.2 — separate process, rail lane only.

Live data: Network Rail Open Data TRUST train movements (STOMP feed
/topic/TRAIN_MVT_ALL_TOC on publicdatafeeds.networkrail.co.uk:61618, as used by
the owner's v3 site). TRUST reports a train when it arrives at, departs from or
passes a location (STANOX code). It is NOT GPS. Trains are therefore shown AT
THE LAST STATION THEY REPORTED, labelled with what happened there, and jump to
the next station when they report again. Nothing is invented or animated.

Station positions: Network Rail CORPUS (STANOX -> TIPLOC) matched exactly to
DfT NaPTAN rail stations (ATCO 9100+TIPLOC) — the v3-proven method. No
name-guessing; codes with conflicting locations are dropped. Built once and
cached for 7 days. NaPTAN: Open Government Licence v3.0.

Timing points (0.2): reports at junctions, loops and yards (not stations) are
placed using the bundled data/timing-points.csv (from the open "YA TIPLOC
list", each row tagged with its original source; see data/SOURCES.txt).
Stations from the method above always win; timing points only fill gaps.

Memory (0.2): the trains seen in the last 30 minutes are saved to this PC every
minute and reloaded on start, so a restart shows trains straight away (each
still labelled with the time it really reported).

Behaviour:
- Connects to the live feed ONLY while someone is viewing (and for 10 minutes
  after), then disconnects. Roughly a steady trickle of data while connected.
- Trains reported in the last 10 min: normal; 10–30 min: faded; >30 min: gone.
  Cancelled trains are removed; terminated trains disappear after 5 minutes.
- Login (email + password for the Network Rail Open Data website) is read from
  TPA_NR_USERNAME / TPA_NR_PASSWORD only; never logged, stored or returned.
- 127.0.0.1 only. Read-only. Standard library only (no installs).
"""
from __future__ import annotations

import base64
import csv
import gzip
import io
import zlib
import json
import math
import os
import re
import socket
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ENGINE_VERSION = '0.8.2'
COMPONENT = 'rail'
CONTRACT = '1.0'
ATTRIBUTION = ('Trains: contains information of Network Rail Infrastructure Limited (Data Feeds Licence) · platforms © OpenStreetMap contributors · '
               'track: Network Rail (OGL) · stations: DfT NaPTAN (OGL) · junctions: YA TIPLOC list (CC BY-SA 4.0)')
assert len(ATTRIBUTION) <= 256   # 0.7.0: HQ refuses a longer credit line (it once hid every train)

FEED_HOST, FEED_PORT = 'publicdatafeeds.networkrail.co.uk', 61618
FEED_TOPIC = '/topic/TRAIN_MVT_ALL_TOC'
# 0.6.0 (owner 5 Nov 09:48-10:03: signalling feed + platforms; the new Network Rail portal gives "global access to all
# data feeds", and the server's login downloads SMART with 206): the signalling (TD) feed says each time a train steps
# from one signal section ("berth") to the next. Network Rail's SMART file says which steps mean "arriving at / leaving
# station X", how many seconds before or after the official report that happens, and at which PLATFORM. So between
# stations a train's arrival is re-timed signal by signal, it arrives on the map as it pulls in (not when the report
# comes), and the box says which platform. Same login, same connection; nothing new to pay for or sign up to.
TD_TOPIC = '/topic/TD_ALL_SIG_AREA'
TD_ON = os.environ.get('TPA_RAIL_TD', '1').strip() != '0'
SMART_URL = 'https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=SMART'
MAX_SMART = 150_000_000
# 0.8.0 (owner 5 Nov 00:16-00:19 + 10:55 stage 3: a picture of the actual type of train in its box): each night the
# timetable (SCHEDULE, the full daily file) is read AS IT DOWNLOADS and only today's and tomorrow's trains are kept, each
# with what it is booked to be (power type + "timing load", which for units is the class number). Network Rail's
# "activated" message (0001) links a running train to its timetable entry, so its box can say "Class 800".
SCHEDULE_URL = 'https://publicdatafeeds.networkrail.co.uk/ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full'
MAX_SCHEDULE_GZ = 1_500_000_000
# 0.7.0 (owner 4 Nov 11:44 "place the trains on the platforms they are due at or actually on", 5 Nov 10:20 stage 2): where
# each platform is, from OpenStreetMap (railway=platform with its number in ref/local_ref), fetched once a month from the
# public Overpass service and kept on our own server. A train standing at a station with a known platform number is drawn
# on the track beside that platform instead of on the station's dot.
# 0.7.1 (owner 10:39: "platforms-http-502" - the public Overpass service was too busy for one Britain-wide question):
# Britain is asked for in five strips, from the main service or one of two other free copies of it, and a failure is
# tried again after 30 minutes (not 6 hours).
OVERPASS_URLS = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter',
                 'https://overpass.kumi.systems/api/interpreter']
OVERPASS_STRIPS = [(49.8, 51.5), (51.5, 52.5), (52.5, 53.5), (53.5, 55.0), (55.0, 61.0)]


def overpass_query(south: float, north: float) -> str:
    box = f'{south},-8.7,{north},2.0'
    return (f'[out:json][timeout:180];(way["railway"="platform"]({box});relation["railway"="platform"]({box});'
            f'way["public_transport"="platform"]["train"="yes"]({box}););out center tags;')
MAX_PLATFORMS_FILE = 80_000_000
PLATFORM_REACH_KM = 0.6
CORPUS_URL = 'https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS'
NAPTAN_URL = 'https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=910&dataFormat=csv'
MAX_CORPUS, MAX_NAPTAN = 22_000_000, 20_000_000
REFERENCE_MAX_AGE = 7 * 86400

FRESH_FOR = 10 * 60          # normal for 10 min after a report
DROP_AFTER = 30 * 60         # v3 MAX_AGE
TERMINATED_KEEP = 20 * 60    # 0.4.1: a train that has finished usually still stands in the platform
CANCELLED_KEEP = 10 * 60
MAX_TRAINS = 20000
MAX_PER_VIEW = 400
MAX_VIEW_SPAN = 4.0          # degrees of latitude
DEMAND_WINDOW = max(5, int(os.environ.get('TPA_RAIL_DEMAND_SECONDS', '600') or 600))
HEARTBEAT_MS = 15000
# 0.2.1: on a server set TPA_RAIL_ALWAYS_ON=1 to listen 24/7, so the map is
# already full when someone opens it. Off by default (PC: only while viewed).
ALWAYS_ON = os.environ.get('TPA_RAIL_ALWAYS_ON', '0').strip() == '1'

USERNAME = (os.environ.get('TPA_NR_USERNAME') or '').strip()
PASSWORD = os.environ.get('TPA_NR_PASSWORD') or ''
HAVE_LOGIN = bool(USERNAME and PASSWORD)

# Test hooks: only this PC (127.0.0.1) may stand in for Network Rail / NaPTAN.
_t_stomp = os.environ.get('TPA_RAIL_TEST_STOMP', '').strip()
_t_corpus = os.environ.get('TPA_RAIL_TEST_CORPUS_URL', '').strip()
_t_naptan = os.environ.get('TPA_RAIL_TEST_NAPTAN_URL', '').strip()
_t_smart = os.environ.get('TPA_RAIL_TEST_SMART_URL', '').strip()
_t_overpass = os.environ.get('TPA_RAIL_TEST_OVERPASS_URL', '').strip()
_t_schedule = os.environ.get('TPA_RAIL_TEST_SCHEDULE_URL', '').strip()
if _t_stomp and not re.fullmatch(r'127\.0\.0\.1:\d{2,5}', _t_stomp):
    raise SystemExit('TPA_RAIL_TEST_STOMP must be 127.0.0.1:port')
for _u in (_t_corpus, _t_naptan, _t_smart, _t_overpass, _t_schedule):
    if _u and not re.match(r'^http://127\.0\.0\.1:\d+/', _u):
        raise SystemExit('Test URLs must point at 127.0.0.1')
if _t_stomp:
    FEED_HOST, FEED_PORT = '127.0.0.1', int(_t_stomp.split(':')[1])
CORPUS_SRC = _t_corpus or CORPUS_URL
NAPTAN_SRC = _t_naptan or NAPTAN_URL
SMART_SRC = _t_smart or SMART_URL
SCHEDULE_SRC = _t_schedule or SCHEDULE_URL
OVERPASS_SRCS = [_t_overpass] if _t_overpass else OVERPASS_URLS
DATA = Path(os.environ.get('TPA_RAIL_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'TPA' / 'rail-engine'))
REFERENCE_FILE = DATA / 'station-reference.json'
MEMORY_FILE = DATA / 'trains-last-30-min.json'
SMART_FILE = DATA / 'smart-steps.json'
PLATFORMS_FILE = DATA / 'platforms.json'
CLASSES_FILE = DATA / 'booked-classes.json'
SAVE_EVERY = max(1, int(os.environ.get('TPA_RAIL_SAVE_SECONDS', '60') or 60))
TIMING_POINTS_FILE = Path(os.environ.get('TPA_RAIL_TIMING_POINTS_FILE') or (Path(__file__).resolve().parent / 'data' / 'timing-points.csv'))

LOCK = threading.Lock()
STATE = {'stations': None, 'referenceError': '', 'trains': {}, 'connected': False, 'connectedAt': 0.0,
         'lastMessageAt': 0.0, 'lastError': '' if HAVE_LOGIN else 'no-login', 'authRejectedUntil': 0.0,
         'lastDemandAt': 0.0, 'messages': 0, 'movements': 0, 'unplaced': 0, 'bytes': 0,
         'points': {}, 'atPoints': 0, 'restored': 0, 'dirty': False, 'savedAt': 0.0,
         'trackLoaded': False, 'trackError': '', 'cancelledSeen': 0, 'reinstatedSeen': 0,
         'smartSteps': 0, 'smartError': '', 'smartBuiltAt': 0.0, 'tdMessages': 0, 'tdUseful': 0, 'tdMatched': 0,
         'tdRetimed': 0, 'tdArrived': 0, 'tdPlatforms': 0, 'tdBlockedUntil': 0.0, 'tdThisSession': False,
         'platformsKnown': 0, 'platformsError': '', 'platformsBuiltAt': 0.0, 'drawnOnPlatform': 0,
         'bookedDays': [], 'bookedTrains': 0, 'scheduleError': '', 'scheduleBuiltAt': 0.0, 'activations': 0, 'withClass': 0}
BOOKED = {}      # 0.8.0: 'YYYY-MM-DD' -> {train uid: [power type, timing load]}
ACTIVATED = {}   # 0.8.0: TRUST train id -> (train uid, day it set off)
PLATFORMS = {}   # 0.7.0: (round(lat,2), round(lon,2)) -> [(lat, lon, refs), ...]
SPOTS = {}       # (stanox, platform) -> (lat, lon) or None
BY_HEAD = {}   # 0.6.0: headcode -> train ids (kept loosely; checked on use)
SMART = {'steps': {}, 'interposes': {}}   # (area, from, to) / (area, to) -> (stanox, event, platform, offset seconds)
WAKE = threading.Event()
# 0.4.0: trains glide ALONG THE TRACK. Routes between a train's last place and its next
# reporting point come from Network Rail's own track model (data/rail-network.json.gz,
# OGL). They are worked out in the background; until one is ready (or if none can be
# found) the train simply waits at its last reported place: it never cuts across fields.
TRACK_FILE = Path(os.environ.get('TPA_RAIL_TRACK_FILE') or (Path(__file__).resolve().parent / 'data' / 'rail-network.json.gz'))
ROUTES = {}            # (lat, lon, nlat, nlon) -> [[lon, lat], ...] or False (no route)
PENDING = []           # legs waiting to be worked out (newest last)
PENDING_SET = set()
ROUTE_WAKE = threading.Event()
ROUTER = None
PLACED = {}            # 0.4.2: (lon, lat) of a station/timing point -> the nearest point on the track


def on_track(lon: float, lat: float):
    """Where to draw a train standing at (lon, lat): on the track itself once the track map is loaded."""
    router = ROUTER
    if router is None:
        return lon, lat
    key = (lon, lat)
    got = PLACED.get(key)
    if got is None:
        try:
            got = router.place(lon, lat) or key
        except Exception:
            got = key
        if len(PLACED) > 20000:
            PLACED.clear()
        PLACED[key] = got
    return got


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def clean(text, limit: int) -> str:
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', str(text or '')).strip()[:limit]


# ---------------------------------------------------------------- station reference (v3 method)
def _download(url: str, limit: int, login: str | None = None, timeout: int = 60) -> bytes:
    expected = urlsplit(url).hostname
    test = expected == '127.0.0.1'

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlsplit(newurl)
            host = target.hostname or ''
            ok = host == expected or (expected == 'publicdatafeeds.networkrail.co.uk' and
                                      re.fullmatch(r'(?:[a-z0-9-]+\.)*s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com', host))
            if not ok or (target.scheme != 'https' and not test) or target.username or target.password:
                raise ValueError('unapproved-redirect')
            new = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new and host != expected:  # never send the Network Rail login to S3
                new.remove_header('Authorization')
            return new

    headers = {'User-Agent': f'TPA-Train-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)',
               'Accept': 'application/json, text/csv, application/zip, */*'}
    if login:
        headers['Authorization'] = 'Basic ' + base64.b64encode(login.encode('utf-8')).decode('ascii')
    opener = urllib.request.build_opener(SafeRedirect()) if not test else urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as res:
        data = res.read(limit + 1)
    if len(data) > limit:
        raise ValueError('oversized')
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            data = g.read(limit + 1)
        if len(data) > limit:
            raise ValueError('oversized')
    return data


def build_lookup(corpus_payload: bytes, naptan_payload: bytes) -> dict:
    corpus = json.loads(corpus_payload)
    records = corpus.get('TIPLOCDATA') if isinstance(corpus, dict) else corpus
    if not isinstance(records, list):
        raise ValueError('corpus-schema')
    if naptan_payload.startswith(b'PK\x03\x04'):
        with zipfile.ZipFile(io.BytesIO(naptan_payload)) as z:
            files = [i for i in z.infolist() if i.filename.lower().endswith('.csv') and i.file_size <= MAX_NAPTAN]
            if not files:
                raise ValueError('naptan-schema')
            naptan_payload = z.read(([i for i in files if 'stop' in i.filename.lower()] or files)[0])
    rows = csv.DictReader(io.StringIO(naptan_payload.decode('utf-8-sig')))
    if not {'ATCOCode', 'StopType', 'Latitude', 'Longitude'}.issubset(set(rows.fieldnames or [])):
        raise ValueError('naptan-schema')
    naptan, conflicting = {}, set()
    for row in rows:
        atco = str(row.get('ATCOCode') or '').strip().upper()
        if not atco.startswith('9100') or str(row.get('StopType') or '').strip().upper() != 'RLY':
            continue
        if str(row.get('Status') or 'active').lower() not in ('active', ''):
            continue
        code = atco[4:]
        try:
            lat, lon = float(row['Latitude']), float(row['Longitude'])
        except (ValueError, TypeError, KeyError):
            continue
        if not re.fullmatch(r'[A-Z0-9]{4,8}', code) or not (math.isfinite(lat) and math.isfinite(lon) and 49 < lat < 61 and -9 < lon < 3):
            continue
        loc = (round(lat, 6), round(lon, 6))
        if code in naptan and naptan[code] != loc:
            conflicting.add(code)
        else:
            naptan[code] = loc
    for code in conflicting:
        naptan.pop(code, None)
    stations, conflicts = {}, set()
    for item in records:
        if not isinstance(item, dict):
            continue
        stanox = str(item.get('STANOX') or '').strip()
        tiploc = str(item.get('TIPLOC') or '').strip().upper()
        if not re.fullmatch(r'\d{3,6}', stanox) or tiploc not in naptan:
            continue
        lat, lon = naptan[tiploc]
        name = clean(item.get('NLCDESC') or item.get('NLCDESC16') or tiploc, 60).title() or tiploc
        if stanox in stations and (stations[stanox][0], stations[stanox][1]) != (lat, lon):
            conflicts.add(stanox)
        else:
            stations.setdefault(stanox, (lat, lon, name))
    for code in conflicts:
        stations.pop(code, None)
    return stations


def load_reference() -> None:
    """Use the cached station list if under 7 days old; otherwise rebuild it."""
    try:
        cached = json.loads(REFERENCE_FILE.read_text(encoding='utf-8'))
        if time.time() - cached.get('builtAt', 0) < REFERENCE_MAX_AGE and len(cached.get('stations', {})) >= 50:
            with LOCK:
                STATE['stations'] = {k.zfill(5): tuple(v) for k, v in cached['stations'].items()}
                STATE['referenceError'] = ''
            return
    except (OSError, ValueError, AttributeError):
        pass
    try:
        corpus = _download(CORPUS_SRC, MAX_CORPUS, USERNAME + ':' + PASSWORD)
    except urllib.error.HTTPError as exc:
        return _ref_fail(f'corpus-http-{exc.code}')
    except Exception as exc:
        return _ref_fail('corpus-' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)[:30])
    try:
        naptan = _download(NAPTAN_SRC, MAX_NAPTAN)
    except Exception as exc:
        return _ref_fail('naptan-' + type(exc).__name__)
    try:
        stations = build_lookup(corpus, naptan)
    except Exception as exc:
        return _ref_fail('match-' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)[:30])
    if len(stations) < 50:
        return _ref_fail('too-few-stations')
    with LOCK:
        STATE['stations'], STATE['referenceError'] = {k.zfill(5): v for k, v in stations.items()}, ''
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = REFERENCE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'builtAt': time.time(), 'stations': stations}), encoding='utf-8')
        tmp.replace(REFERENCE_FILE)
    except OSError:
        pass
    print(f'Station list ready: {len(stations)} Network Rail locations placed at NaPTAN stations', flush=True)


def _ref_fail(code: str) -> None:
    with LOCK:
        STATE['referenceError'] = code
    print('Station list failed:', code, flush=True)


# ---------------------------------------------------------------- timing points (bundled, no download)
def load_timing_points(path: Path = TIMING_POINTS_FILE) -> dict:
    points = {}
    try:
        with open(path, encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                stanox = str(row.get('stanox') or '').strip()
                try:
                    lat, lon = float(row['lat']), float(row['lon'])
                except (ValueError, TypeError, KeyError):
                    continue
                stanox = stanox.zfill(5)   # the feed always sends 5 digits (e.g. Scotland 0xxxx)
                if re.fullmatch(r'\d{5,6}', stanox) and math.isfinite(lat) and math.isfinite(lon) and 49 < lat < 61 and -9 < lon < 3:
                    points[stanox] = (lat, lon, clean(row.get('name'), 60) or 'a timing point')
    except OSError:
        return {}
    return points


# ---------------------------------------------------------------- memory of the last 30 minutes
TRAIN_FIELDS = {'lat': float, 'lon': float, 'station': str, 'when': float, 'event': str, 'variation': str,
                'minutes': int, 'headcode': str, 'terminated': bool, 'point': bool,
                'nlat': float, 'nlon': float, 'nname': str, 'run': int, 'cancelled': bool, 'cancelledAt': float,
                'toc': str,   # 0.5.0: the operator's two-digit code
                'stanox': str, 'nstanox': str, 'platform': str, 'platformAt': str, 'arriveBy': float,   # 0.6.0
                'uid': str, 'day': str}   # 0.8.0: its timetable entry   # 0.3.0: next place + minutes to it


def save_memory(force: bool = False) -> None:
    now = time.time()
    with LOCK:
        if not (STATE['dirty'] or force) or (not force and now - STATE['savedAt'] < SAVE_EVERY):
            return
        keep = {k: v for k, v in STATE['trains'].items() if now - v['when'] < DROP_AFTER}
        STATE['dirty'], STATE['savedAt'] = False, now
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'savedAt': now, 'engineVersion': ENGINE_VERSION, 'trains': keep}, separators=(',', ':')), encoding='utf-8')
        tmp.replace(MEMORY_FILE)
    except OSError:
        pass


def restore_memory() -> int:
    """Reload trains saved in the last 30 minutes. Anything odd in the file is skipped."""
    now = time.time()
    try:
        saved = json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
        items = saved.get('trains', {}) if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return 0
    restored = {}
    for tid, t in list(items.items())[:MAX_TRAINS] if isinstance(items, dict) else []:
        if not (isinstance(tid, str) and re.fullmatch(r'[0-9A-Z]{10}', tid) and isinstance(t, dict)):
            continue
        try:
            item = {k: (cast(t.get(k)) if k in t else (False if cast is bool else cast())) for k, cast in TRAIN_FIELDS.items()}
        except (TypeError, ValueError):
            continue
        item['station'] = clean(item['station'], 60)
        item['headcode'] = item['headcode'] if re.fullmatch(r'[0-9][A-Z][0-9]{2}', item['headcode']) else ''
        item['event'] = item['event'] if item['event'] in ('ARRIVAL', 'DEPARTURE', 'REPORT') else 'REPORT'
        item['variation'] = clean(item['variation'], 12).upper()
        item['terminated'] = t.get('terminated') is True
        item['point'] = t.get('point') is True
        item['cancelled'] = t.get('cancelled') is True
        item['toc'] = item['toc'] if re.fullmatch(r'\d{2}', item['toc']) else ''
        for k in ('stanox', 'nstanox', 'platformAt'):
            item[k] = item[k] if re.fullmatch(r'\d{5,6}', item[k]) else ''
        item['platform'] = item['platform'] if re.fullmatch(r'[0-9A-Z]{1,3}', item['platform']) else ''
        item['uid'] = item['uid'] if re.fullmatch(r'[A-Z0-9 ]{6}', item['uid']) else ''
        item['day'] = item['day'] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', item['day']) else ''
        item['nname'] = clean(item['nname'], 60)
        if not (0 < item['run'] <= 120 and 49 < item['nlat'] < 61 and -9 < item['nlon'] < 3):
            item.update(nlat=0.0, nlon=0.0, nname='', run=0)
        if not (now - DROP_AFTER < item['when'] < now + 180 and 49 < item['lat'] < 61 and -9 < item['lon'] < 3):
            continue
        restored[tid] = item
    with LOCK:
        for tid, item in restored.items():
            current = STATE['trains'].get(tid)
            if not current or current['when'] < item['when']:
                STATE['trains'][tid] = item
                if item['headcode']:
                    BY_HEAD.setdefault(item['headcode'], set()).add(tid)
        STATE['restored'] = len(restored)
    return len(restored)


def memory_loop() -> None:
    while True:
        time.sleep(5)
        save_memory()


# ---------------------------------------------------------------- TRUST movements
TRUST_ID = re.compile(r'^\d{2}([0-9][A-Z][0-9]{2})[A-Z0-9]{2}(0[1-9]|[12][0-9]|3[01])$')


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(a)))


def handle_message(item: dict, now: float) -> None:
    header, body = item.get('header') or {}, item.get('body') or {}
    if not isinstance(header, dict) or not isinstance(body, dict):
        return
    kind = str(header.get('msg_type', ''))
    train_id = clean(body.get('train_id'), 12).upper()
    if not re.fullmatch(r'[0-9A-Z]{10}', train_id):
        return
    with LOCK:
        trains = STATE['trains']
        if kind == '0002':  # 0.4.1: cancellation: shown as "cancelled" (no movement) for 10 minutes, then removed
            t = trains.get(train_id)
            if t:
                t.update(cancelled=True, cancelledAt=now, run=0)   # the last REPORT time is kept as it was
                STATE['cancelledSeen'] += 1
                STATE['dirty'] = True
            return
        if kind == '0005':  # reinstatement: carries on as normal
            t = trains.get(train_id)
            if t and t.get('cancelled'):
                t['cancelled'] = False
                STATE['reinstatedSeen'] += 1
                STATE['dirty'] = True
            return
        if kind == '0001':  # 0.8.0: activated - which timetable entry this running train is
            uid = clean(body.get('train_uid'), 6).upper()
            day = clean(body.get('tp_origin_timestamp') or body.get('schedule_start_date'), 10)
            if re.fullmatch(r'[A-Z0-9 ]{6}', uid):
                ACTIVATED[train_id] = (uid, day if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) else '')
                STATE['activations'] += 1
                if len(ACTIVATED) > 60000:
                    for k in list(ACTIVATED)[:20000]:
                        ACTIVATED.pop(k, None)
                t = trains.get(train_id)
                if t:
                    t.update(uid=ACTIVATED[train_id][0], day=ACTIVATED[train_id][1])
            return
        if kind != '0003':
            return
        STATE['movements'] += 1
        stanox = clean(body.get('loc_stanox'), 6).zfill(5)   # one 5-digit form everywhere
        station = (STATE['stations'] or {}).get(stanox)
        at_point = False
        if not station:
            station = STATE['points'].get(stanox)
            at_point = bool(station)
        if not station:
            STATE['unplaced'] += 1
            return
        if at_point:
            STATE['atPoints'] += 1
        try:
            when = int(body.get('actual_timestamp')) / 1000
        except (TypeError, ValueError):
            when = now
        if not (now - DROP_AFTER < when < now + 180):
            return
        event = str(body.get('event_type', '')).upper()
        variation = str(body.get('variation_status', '')).upper()
        try:
            minutes = abs(int(str(body.get('timetable_variation', '0')).strip() or 0))
        except ValueError:
            minutes = 0
        match = TRUST_ID.fullmatch(train_id)
        toc = clean(body.get('toc_id'), 2)
        toc = toc if re.fullmatch(r'\d{2}', toc) else ''
        terminated = str(body.get('train_terminated', '')).lower() == 'true'
        # 0.3.0: after a departure (or pass), TRUST says where the train reports next and
        # how many minutes that should take. The map glides it there and stops (estimated).
        nxt, run, nstanox = None, 0, ''
        if event == 'DEPARTURE' and not terminated:
            nstanox = clean(body.get('next_report_stanox'), 6).zfill(5)
            nxt = (STATE['stations'] or {}).get(nstanox) or STATE['points'].get(nstanox)
            try:
                run = int(str(body.get('next_report_run_time', '0')).strip() or 0)
            except ValueError:
                run = 0
            if not nxt or not 0 < run <= 120 or km(station[0], station[1], nxt[0], nxt[1]) < 0.2:
                nxt, run = None, 0
        platform = norm_platform(body.get('platform'))
        before = trains.get(train_id) or {}
        if not platform and before.get('platformAt') == stanox:     # the signalling feed told us first: keep it
            platform = before.get('platform', '')
        trains[train_id] = {'lat': station[0], 'lon': station[1], 'station': station[2], 'when': when,
                            'event': event if event in ('ARRIVAL', 'DEPARTURE') else 'REPORT',
                            'variation': variation, 'minutes': minutes, 'headcode': match.group(1) if match else '',
                            'terminated': terminated, 'point': at_point,
                            'nlat': nxt[0] if nxt else 0.0, 'nlon': nxt[1] if nxt else 0.0,
                            'nname': nxt[2] if nxt else '', 'run': run, 'cancelled': False, 'cancelledAt': 0.0,
                            'toc': toc, 'stanox': stanox, 'nstanox': nstanox if nxt else '',
                            'platform': platform, 'platformAt': stanox if platform else '', 'arriveBy': 0.0,
                            'uid': (ACTIVATED.get(train_id) or (before.get('uid', ''), ''))[0],
                            'day': (ACTIVATED.get(train_id) or ('', before.get('day', '')))[1]}
        if match:
            BY_HEAD.setdefault(match.group(1), set()).add(train_id)
        STATE['dirty'] = True
        if len(trains) > MAX_TRAINS:
            oldest = sorted(trains, key=lambda k: trains[k]['when'])[:len(trains) - MAX_TRAINS]
            for k in oldest:
                trains.pop(k, None)


# ---------------------------------------------------------------- 0.6.0: SMART + the signalling (TD) feed
SMART_ROW = re.compile(rb'\{[^{}]*\}')


def build_smart(payload: bytes) -> dict:
    """Keeps only the steps that mean arriving at / leaving a place, row by row (never the whole file as objects)."""
    steps, interposes = {}, {}
    for m in SMART_ROW.finditer(payload):
        try:
            r = json.loads(m.group(0))
        except ValueError:
            continue
        g = lambda k, n: clean(r.get(k), n).strip().upper()   # noqa: E731
        area, frm, to, ev, stanox = g('TD', 2), g('FROMBERTH', 4), g('TOBERTH', 4), g('EVENT', 1), g('STANOX', 6)
        if ev not in ('A', 'B', 'C', 'D') or not re.fullmatch(r'\d{3,6}', stanox) or not re.fullmatch(r'[A-Z0-9]{2}', area):
            continue
        try:
            off = int(str(r.get('BERTHOFFSET') or '0').strip().replace('+', '') or 0)
        except ValueError:
            off = 0
        if not -600 <= off <= 600:
            continue
        row = (stanox.zfill(5), ev, norm_platform(r.get('PLATFORM')), off)
        if g('STEPTYPE', 1) == 'I' and to:
            interposes[f'{area}|{to}'] = row
        elif frm and to:
            steps[f'{area}|{frm}|{to}'] = row
    return {'steps': steps, 'interposes': interposes}


def load_smart() -> bool:
    """SMART kept for a week on our own server; a fresh copy downloaded with the Network Rail login when older."""
    try:
        cached = json.loads(SMART_FILE.read_text(encoding='utf-8'))
        if time.time() - cached.get('builtAt', 0) < REFERENCE_MAX_AGE and cached.get('steps'):
            index = {'steps': {k: tuple(v) for k, v in cached['steps'].items()},
                     'interposes': {k: tuple(v) for k, v in cached.get('interposes', {}).items()}}
            SMART.update(index)
            with LOCK:
                STATE.update(smartSteps=len(index['steps']), smartError='', smartBuiltAt=cached['builtAt'])
            return True
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        index = build_smart(_download(SMART_SRC, MAX_SMART, USERNAME + ':' + PASSWORD))
    except urllib.error.HTTPError as exc:
        with LOCK:
            STATE['smartError'] = f'smart-http-{exc.code}'
        return False
    except Exception as exc:
        with LOCK:
            STATE['smartError'] = ('smart-' + type(exc).__name__)[:40]
        return False
    if len(index['steps']) < 10:
        with LOCK:
            STATE['smartError'] = 'smart-too-few'
        return False
    SMART.update(index)
    built = time.time()
    with LOCK:
        STATE.update(smartSteps=len(index['steps']), smartError='', smartBuiltAt=built)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = SMART_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'builtAt': built, **index}), encoding='utf-8')
        tmp.replace(SMART_FILE)
    except OSError:
        pass
    print(f'Signalling steps ready: {len(index["steps"])} steps that mean arriving at or leaving a place', flush=True)
    return True


def smart_loop() -> None:
    while True:
        if HAVE_LOGIN and TD_ON:
            ok = load_smart()
            time.sleep(REFERENCE_MAX_AGE if ok else 6 * 3600)
        else:
            time.sleep(3600)


PLATFORM_REF = re.compile(r'[0-9]{1,2}[A-Z]?|[A-Z]')


def norm_platform(text) -> str:
    """'04' -> '4', ' 3a' -> '3A'; anything else that is not a platform number -> ''."""
    p = clean(text, 4).strip().upper()
    p = p.lstrip('0') or p
    return p if PLATFORM_REF.fullmatch(p) else ''


def platform_refs(tags: dict) -> list:
    """'1;2', '1 & 2', '3a', 'Platform 4' -> ['1', '2'] ... (upper case)."""
    text = ' '.join(str(tags.get(k) or '') for k in ('ref', 'local_ref', 'railway:platform:ref')).upper()
    text = re.sub(r'PLATFORMS?', ' ', text)
    return sorted({norm_platform(tok) for tok in re.split(r'[^0-9A-Z]+', text) if norm_platform(tok)})[:8]


def build_platforms(payload: bytes) -> list:
    data = json.loads(payload)
    out = []
    for el in data.get('elements', []) if isinstance(data, dict) else []:
        if not isinstance(el, dict):
            continue
        c = el.get('center') or el
        try:
            lat, lon = float(c.get('lat')), float(c.get('lon'))
        except (TypeError, ValueError):
            continue
        refs = platform_refs(el.get('tags') or {})
        if refs and 49 < lat < 61 and -9 < lon < 3:
            out.append([round(lat, 6), round(lon, 6), refs])
    return out


def index_platforms(rows: list) -> None:
    grid = {}
    for lat, lon, refs in rows:
        grid.setdefault((round(lat, 2), round(lon, 2)), []).append((lat, lon, tuple(refs)))
    PLATFORMS.clear(); PLATFORMS.update(grid); SPOTS.clear()
    with LOCK:
        STATE['platformsKnown'] = len(rows)


def load_platforms() -> bool:
    try:
        cached = json.loads(PLATFORMS_FILE.read_text(encoding='utf-8'))
        if time.time() - cached.get('builtAt', 0) < 30 * 86400 and cached.get('platforms'):
            index_platforms(cached['platforms'])
            with LOCK:
                STATE.update(platformsError='', platformsBuiltAt=cached['builtAt'])
            return True
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    rows, seen = [], set()
    for south, north in OVERPASS_STRIPS:
        got, problem = None, ''
        for src in OVERPASS_SRCS:
            try:
                url = src + '?' + urllib.parse.urlencode({'data': overpass_query(south, north)})
                got = build_platforms(_download(url, MAX_PLATFORMS_FILE, timeout=240))
                break
            except urllib.error.HTTPError as exc:
                problem = f'platforms-http-{exc.code}'
            except Exception as exc:
                problem = ('platforms-' + type(exc).__name__)[:40]
            time.sleep(2)
        if got is None:
            with LOCK:
                STATE['platformsError'] = problem
            return False
        for row in got:
            key = (row[0], row[1])
            if key not in seen:
                seen.add(key)
                rows.append(row)
    if len(rows) < 2:
        with LOCK:
            STATE['platformsError'] = 'platforms-too-few'
        return False
    index_platforms(rows)
    built = time.time()
    with LOCK:
        STATE.update(platformsError='', platformsBuiltAt=built)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = PLATFORMS_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'builtAt': built, 'platforms': rows}), encoding='utf-8')
        tmp.replace(PLATFORMS_FILE)
    except OSError:
        pass
    print(f'Platforms ready: {len(rows)} numbered platforms from OpenStreetMap', flush=True)
    return True


def platforms_loop() -> None:
    while True:
        if not HAVE_LOGIN:          # no trains without the Network Rail login: nothing to place, nothing fetched
            time.sleep(3600)
            continue
        ok = load_platforms()
        time.sleep(30 * 86400 if ok else 1800)


def platform_spot(stanox: str, platform: str, lat: float, lon: float):
    """The middle of platform `platform` at the station at (lat, lon), if OpenStreetMap has exactly that number nearby."""
    key = (stanox, platform)
    if key in SPOTS:
        return SPOTS[key]
    best = None
    for dlat in (-0.01, 0, 0.01):
        for dlon in (-0.01, 0, 0.01):
            for plat_lat, plat_lon, refs in PLATFORMS.get((round(lat + dlat, 2), round(lon + dlon, 2)), ()):
                if platform in refs:
                    d = km(lat, lon, plat_lat, plat_lon)
                    if d <= PLATFORM_REACH_KM and (best is None or d < best[0]):
                        best = (d, plat_lat, plat_lon, len(refs))
    spot = None
    if best:
        spot = (best[1], best[2])
    if len(SPOTS) > 50000:
        SPOTS.clear()
    SPOTS[key] = spot
    return spot


def drawn_at(t: dict):
    """Where a train is drawn: beside its platform when it is standing at a station whose platform we know."""
    if t.get('platform') and t.get('platformAt') and t.get('platformAt') == t.get('stanox') and not (t.get('run') and t['event'] == 'DEPARTURE'):
        spot = platform_spot(t['stanox'], t['platform'], t['lat'], t['lon'])
        if spot:
            return on_track(spot[1], spot[0]), True
    return on_track(t['lon'], t['lat']), False


def uk_today(offset_days: int = 0) -> str:
    try:
        from zoneinfo import ZoneInfo
        d = datetime.now(ZoneInfo('Europe/London'))
    except Exception:
        d = datetime.now()
    return (d + timedelta(days=offset_days)).strftime('%Y-%m-%d')


STP_RANK = {'O': 3, 'N': 2, 'P': 1}
# each entry's few fields picked out by pattern (an entry carries every stop, so unpacking each one fully would cost
# minutes of the server's time every night)
SCHED_FIELD = {k: re.compile(rb'"' + k.encode() + rb'"\s*:\s*"([^"]*)"') for k in (
    'CIF_train_uid', 'CIF_stp_indicator', 'schedule_start_date', 'schedule_end_date', 'schedule_days_runs',
    'transaction_type', 'CIF_power_type', 'CIF_timing_load', 'signalling_id', 'atoc_code')}


def read_schedule(stream, days: list) -> dict:
    """Reads the SCHEDULE file line by line as it arrives (gzip or plain). Keeps, for each of `days`, each train uid's
    booked power type and timing load from the entry that applies that day (overlay > new > permanent; cancelled = none)."""
    wanted = {d: datetime.strptime(d, '%Y-%m-%d') for d in days}
    best = {d: {} for d in days}
    cancelled = {d: set() for d in days}
    unzip, gz, rest, total = None, None, b'', 0
    while True:
        chunk = stream.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_SCHEDULE_GZ:
            raise ValueError('oversized')
        if gz is None:
            gz = chunk.startswith(b'\x1f\x8b')
            unzip = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
        data = rest + (unzip.decompress(chunk) if gz else chunk)
        lines = data.split(b'\n')
        rest = lines.pop()
        if len(rest) > 2_000_000:
            raise ValueError('line-too-long')
        for line in lines:
            if b'JsonScheduleV1' not in line:
                continue
            def field(name, default=''):
                m = SCHED_FIELD[name].search(line)
                return m.group(1).decode('ascii', 'replace') if m else default
            try:
                uid = field('CIF_train_uid').strip().upper()
                stp = field('CIF_stp_indicator').strip().upper()
                start = datetime.strptime(field('schedule_start_date'), '%Y-%m-%d')
                end = datetime.strptime(field('schedule_end_date') or field('schedule_start_date'), '%Y-%m-%d')
                runs = field('schedule_days_runs', '1111111')
            except ValueError:
                continue
            if not re.fullmatch(r'[A-Z0-9 ]{6}', uid) or field('transaction_type', 'Create') != 'Create':
                continue
            for d, when in wanted.items():
                if not start <= when <= end or len(runs) != 7 or runs[when.weekday()] != '1':
                    continue
                if stp == 'C':
                    cancelled[d].add(uid)
                    continue
                rank = STP_RANK.get(stp, 0)
                have = best[d].get(uid)
                if have is None or rank > have[0]:
                    best[d][uid] = (rank, clean(field('CIF_power_type'), 3).strip().upper(), clean(field('CIF_timing_load'), 4).strip().upper(),
                                    clean(field('signalling_id'), 4).strip().upper(), clean(field('atoc_code'), 2).strip().upper())
    if gz and unzip is not None:
        rest += unzip.flush()
    out = {d: {uid: [v[1], v[2]] for uid, v in best[d].items() if uid not in cancelled[d] or v[0] > 1} for d in days}
    # 0.8.1 (owner 11:57, 1A35 left Piccadilly after the update but showed no class - Network Rail had "activated" it about
    # an hour before it left, before the update): by headcode too. A headcode whose trains that day are all booked the
    # same is enough to know what a train is without its activation (a headcode shared by different types: unknown).
    for d in days:
        heads = {}
        for uid, v in best[d].items():
            if (uid not in cancelled[d] or v[0] > 1) and re.fullmatch(r'[0-9][A-Z][0-9]{2}', v[3]):
                heads.setdefault(v[3], set()).add((v[1], v[2]))
                # 0.8.2 (owner 12:16, 1A36 Avanti out of Piccadilly: no type - other operators also run a "1A36" today with
                # other trains): headcode + operator too, which is almost always enough
                if re.fullmatch(r'[A-Z]{2}', v[4]):
                    heads.setdefault(v[3] + '|' + v[4], set()).add((v[1], v[2]))
        out[d]['#heads'] = {h: list(next(iter(kinds))) for h, kinds in heads.items() if len(kinds) == 1}
    return out


def load_schedule() -> bool:
    days = [uk_today(), uk_today(1)]
    try:
        cached = json.loads(CLASSES_FILE.read_text(encoding='utf-8'))
        if days[1] in cached.get('days', {}) and cached.get('v') == 2:   # made today by this version: use it
            BOOKED.clear(); BOOKED.update(cached['days'])
            with LOCK:
                STATE.update(bookedDays=sorted(BOOKED), bookedTrains=max(0, len(BOOKED.get(days[0], {})) - 1), scheduleError='',
                             scheduleBuiltAt=cached.get('builtAt', 0.0))
            return True
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        expected = urlsplit(SCHEDULE_SRC).hostname
        test = expected == '127.0.0.1'

        class SafeRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                host = urlsplit(newurl).hostname or ''
                ok = host == expected or re.fullmatch(r'(?:[a-z0-9-]+\.)*s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com', host)
                if not ok or (urlsplit(newurl).scheme != 'https' and not test):
                    raise ValueError('unapproved-redirect')
                new = super().redirect_request(req, fp, code, msg, headers, newurl)
                if new and host != expected:   # never send the Network Rail login to S3
                    new.remove_header('Authorization')
                return new
        handlers = [SafeRedirect()] + ([urllib.request.ProxyHandler({})] if test else [])
        req = urllib.request.Request(SCHEDULE_SRC, headers={
            'User-Agent': f'TPA-Train-Engine/{ENGINE_VERSION} (+mailto:tpaprojectuk@gmail.com)',
            'Authorization': 'Basic ' + base64.b64encode((USERNAME + ':' + PASSWORD).encode('utf-8')).decode('ascii')})
        with urllib.request.build_opener(*handlers).open(req, timeout=120) as res:
            booked = read_schedule(res, days)
    except urllib.error.HTTPError as exc:
        with LOCK:
            STATE['scheduleError'] = f'schedule-http-{exc.code}'
        return False
    except Exception as exc:
        with LOCK:
            STATE['scheduleError'] = ('schedule-' + type(exc).__name__)[:40]
        return False
    if len(booked.get(days[0], {})) < 2:
        with LOCK:
            STATE['scheduleError'] = 'schedule-empty'
        return False
    BOOKED.clear(); BOOKED.update(booked)
    built = time.time()
    with LOCK:
        STATE.update(bookedDays=sorted(BOOKED), bookedTrains=len(booked[days[0]]) - 1, scheduleError='', scheduleBuiltAt=built)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = CLASSES_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'v': 2, 'builtAt': built, 'days': booked}), encoding='utf-8')
        tmp.replace(CLASSES_FILE)
    except OSError:
        pass
    print(f'Timetable ready: {len(booked[days[0]]) - 1} trains booked today, with what each is booked to be', flush=True)
    return True


def schedule_loop() -> None:
    while True:
        if not HAVE_LOGIN:
            time.sleep(3600)
            continue
        ok = load_schedule()
        if not ok:
            time.sleep(3600)
            continue
        # next look: after 03:00 UK time (Network Rail's new file is out by then), or in an hour if the day has changed
        time.sleep(3600)


# drawings TPA has (HQ's train-class pictures) and the classes each covers
CLASS_DRAWINGS = {'800': ('800', '801', '802', '805', '807', '810'), '390': ('390',), '220': ('220', '221', '222'),
                  '150': ('150', '153', '155', '156'), '158': ('158', '159'), '170': ('168', '170', '171', '172'),
                  '195': ('195', '196', '197', '331', '397'), '345': ('345', '701', '710', '720', '730'),
                  '700': ('700', '707', '717'), '755': ('231', '745', '755', '756'), '387': ('357', '375', '377', '378', '379', '387')}
DRAWING_OF = {c: k for k, cs in CLASS_DRAWINGS.items() for c in cs}
CLASS_NAMES = {'800': 'Hitachi Azuma / IET', '390': 'Alstom Pendolino', '220': 'Bombardier Voyager', '150': 'Sprinter',
               '158': 'Express Sprinter', '170': 'Turbostar', '195': 'CAF Civity', '345': 'Aventra', '700': 'Siemens Desiro City',
               '755': 'Stadler FLIRT', '387': 'Bombardier Electrostar', '43': 'InterCity 125 (HST)'}
POWER = {'D': 'diesel locomotive', 'E': 'electric locomotive', 'ED': 'electro-diesel locomotive'}


def booked_class(t: dict):
    """(what it is, which drawing) from its timetable entry, or ('', '')."""
    uid, day = t.get('uid'), t.get('day')
    entry = None
    if uid:
        entry = (BOOKED.get(day) or {}).get(uid) or (BOOKED.get(uk_today()) or {}).get(uid)
    if not entry and t.get('headcode'):      # 0.8.1: no activation seen - its headcode (+ operator), if that tells us for sure
        heads = (BOOKED.get(uk_today()) or {}).get('#heads') or {}
        atoc = ATOC_OF.get(t.get('toc', ''), '')
        entry = (heads.get(t['headcode'] + '|' + atoc) if atoc else None) or heads.get(t['headcode'])
    if not entry or entry[0] == '#':
        return '', ''
    power, load = entry
    if power == 'HST':
        return 'Class 43 · ' + CLASS_NAMES['43'], 'c43'
    if re.fullmatch(r'\d{3}', load) and power in ('EMU', 'DMU', 'DEM', 'EML'):
        drawing = DRAWING_OF.get(load, '')
        name = CLASS_NAMES.get(drawing, '')
        return f'Class {load}' + (f' · {name}' if name else ''), ('c' + drawing if drawing else '')
    if power in POWER:
        diesel = power == 'D'
        return 'Hauled by a ' + POWER[power], ('c66' if diesel and kind_of(t)[0] == 'freight' else '')
    return '', ''


def handle_td(item: dict, now: float) -> None:
    """One signalling message: a train stepping from one berth to the next (CA) or appearing in one (CC)."""
    if not isinstance(item, dict) or len(item) != 1:
        return
    kind, msg = next(iter(item.items()))
    if kind not in ('CA_MSG', 'CC_MSG') or not isinstance(msg, dict):
        return
    area, descr = clean(msg.get('area_id'), 2).upper(), clean(msg.get('descr'), 4).upper()
    to = clean(msg.get('to'), 4).upper()
    if not re.fullmatch(r'[0-9][A-Z][0-9]{2}', descr):
        return
    if kind == 'CA_MSG':
        row = SMART['steps'].get(f"{area}|{clean(msg.get('from'), 4).upper()}|{to}")
    else:
        row = SMART['interposes'].get(f'{area}|{to}')
    if not row:
        return
    stanox, ev, plat, off = row
    try:
        when = int(msg.get('time')) / 1000
    except (TypeError, ValueError):
        when = now
    if not now - 300 < when < now + 60:
        return
    with LOCK:
        STATE['tdUseful'] += 1
        best = None
        ids = BY_HEAD.get(descr) or set()
        for tid in list(ids):
            t = STATE['trains'].get(tid)
            if not t or t.get('headcode') != descr:
                ids.discard(tid)
                continue
            if t.get('cancelled') or t.get('terminated'):
                continue
            if t.get('nstanox') == stanox or t.get('stanox') == stanox:
                if best is None or t['when'] > best[1]['when']:
                    best = (tid, t)
        if best is None:
            return
        tid, t = best
        STATE['tdMatched'] += 1
        arriving = ev in ('A', 'C')
        if arriving and plat:
            t.update(platform=plat, platformAt=stanox)
            STATE['tdPlatforms'] += 1
        if arriving and t.get('nstanox') == stanox and t['event'] == 'DEPARTURE' and t.get('run'):
            eta = when + off
            if eta > now + 5:
                if eta < t['when'] + t['run'] * 60 + 1800:      # re-timed signal by signal
                    t['arriveBy'] = eta
                    STATE['tdRetimed'] += 1
            else:                                              # pulling in now: shown at the station straight away
                station = (STATE['stations'] or {}).get(stanox) or STATE['points'].get(stanox)
                if station:
                    t.update(lat=station[0], lon=station[1], station=station[2], stanox=stanox, event='ARRIVAL',
                             when=max(t['when'], min(now, eta)), point=stanox not in (STATE['stations'] or {}),
                             nlat=0.0, nlon=0.0, nname='', nstanox='', run=0, arriveBy=0.0)
                    STATE['tdArrived'] += 1
        elif not arriving and plat and t.get('stanox') == stanox and not t.get('platform'):
            t.update(platform=plat, platformAt=stanox)
            STATE['tdPlatforms'] += 1
        STATE['dirty'] = True


def stomp_escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('\r', '\\r').replace('\n', '\\n').replace(':', '\\c')


def frame(command: str, headers: dict) -> bytes:
    lines = [command] + [f'{k}:{stomp_escape(str(v))}' for k, v in headers.items()]
    return ('\n'.join(lines) + '\n\n').encode('utf-8') + b'\x00'


def read_frames(sock, buffer: bytearray):
    """Yield (command, headers, body) for each complete frame; heartbeats are skipped."""
    while True:
        while buffer[:1] in (b'\n', b'\r'):
            del buffer[:1]
        end = buffer.find(b'\x00')
        if end < 0:
            return
        raw, rest = bytes(buffer[:end]), buffer[end + 1:]
        buffer[:] = rest
        head, _, body = raw.partition(b'\n\n')
        lines = head.decode('utf-8', 'replace').split('\n')
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(':')
            headers.setdefault(k, v)
        yield lines[0].strip(), headers, body


def feed_session() -> None:
    """One connection: connect, subscribe, read until nobody is viewing or an error occurs."""
    sock = socket.create_connection((FEED_HOST, FEED_PORT), timeout=20)
    try:
        sock.sendall(frame('CONNECT', {'accept-version': '1.2', 'host': 'publicdatafeeds.networkrail.co.uk',
                                       'login': USERNAME, 'passcode': PASSWORD,
                                       'heart-beat': f'{HEARTBEAT_MS},{HEARTBEAT_MS}'}))
        buffer = bytearray()
        connected = False
        deadline = time.time() + 20
        while not connected:
            if time.time() > deadline:
                raise TimeoutError('no-connected-frame')
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError('closed-during-login')
            buffer += chunk
            for command, headers, body in read_frames(sock, buffer):
                if command == 'ERROR':
                    raise PermissionError('login-rejected')
                if command == 'CONNECTED':
                    connected = True
        sock.sendall(frame('SUBSCRIBE', {'id': '1', 'destination': FEED_TOPIC, 'ack': 'auto'}))
        with LOCK:
            td_now = TD_ON and time.time() >= STATE['tdBlockedUntil']
            STATE['tdThisSession'] = td_now
        if td_now:
            sock.sendall(frame('SUBSCRIBE', {'id': '2', 'destination': TD_TOPIC, 'ack': 'auto'}))
        with LOCK:
            STATE.update(connected=True, connectedAt=time.time(), lastMessageAt=time.time(), lastError='')
        print('Network Rail feed: connected and listening', flush=True)
        sock.settimeout(5)
        last_beat = time.time()
        while True:
            now = time.time()
            with LOCK:
                viewed = ALWAYS_ON or now - STATE['lastDemandAt'] < DEMAND_WINDOW
                silent = now - STATE['lastMessageAt']
            if not viewed:
                sock.sendall(frame('DISCONNECT', {}))
                print('Network Rail feed: nobody viewing, disconnected', flush=True)
                return
            if silent > 60:
                raise TimeoutError('feed-silent')
            if now - last_beat >= HEARTBEAT_MS / 1000:
                sock.sendall(b'\n')
                last_beat = now
            try:
                chunk = sock.recv(262144)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError('feed-closed')
            with LOCK:
                STATE['lastMessageAt'] = time.time()
                STATE['bytes'] += len(chunk)
            buffer += chunk
            if len(buffer) > 8_000_000:
                raise ValueError('frame-too-large')
            for command, headers, body in read_frames(sock, buffer):
                if command == 'ERROR':
                    raise PermissionError('feed-error')
                if command != 'MESSAGE':
                    continue
                try:
                    items = json.loads(body.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    continue
                signalling = headers.get('subscription') == '2' or headers.get('destination', '').endswith('TD_ALL_SIG_AREA')
                with LOCK:
                    STATE['tdMessages' if signalling else 'messages'] += 1
                for item in items if isinstance(items, list) else [items]:
                    if isinstance(item, dict):
                        (handle_td if signalling else handle_message)(item, time.time())
    finally:
        with LOCK:
            STATE['connected'] = False
        try:
            sock.close()
        except OSError:
            pass


def feed_loop() -> None:
    backoff = 15
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        now = time.time()
        with LOCK:
            viewed = ALWAYS_ON or now - STATE['lastDemandAt'] < DEMAND_WINDOW
            blocked = now < STATE['authRejectedUntil']
            need_reference = STATE['stations'] is None
        if not HAVE_LOGIN or not viewed or blocked:
            continue
        if need_reference:
            load_reference()
            with LOCK:
                if STATE['stations'] is None:
                    STATE['authRejectedUntil'] = time.time() + (1800 if STATE['referenceError'] == 'corpus-http-401' else 300)
                    continue
        try:
            feed_session()
            backoff = 15
        except PermissionError as exc:
            with LOCK:
                td_refused = str(exc) == 'feed-error' and STATE['tdThisSession']
                STATE['lastError'] = str(exc)
                if td_refused:   # 0.6.0: never let the signalling feed cost us the train reports: carry on without it for 6 hours
                    STATE['tdBlockedUntil'] = time.time() + 6 * 3600
                else:
                    STATE['authRejectedUntil'] = time.time() + 1800
            if td_refused:
                print('Network Rail refused the signalling feed - carrying on with train reports only for 6 hours', flush=True)
                time.sleep(5)
            else:
                print('Network Rail feed:', exc, '- will not retry for 30 minutes', flush=True)
        except (OSError, TimeoutError, ConnectionError, ValueError) as exc:
            with LOCK:
                STATE['lastError'] = (str(exc) or type(exc).__name__)[:40]
            print('Network Rail feed problem:', STATE['lastError'], f'- retrying in {backoff}s', flush=True)
            time.sleep(backoff)
            backoff = min(300, backoff * 2)


# ---------------------------------------------------------------- serving
def health(now: float) -> tuple[str, str]:
    if not HAVE_LOGIN:
        return 'unavailable', 'Network Rail login not set up on this PC'
    if STATE['referenceError'] == 'corpus-http-401' or STATE['lastError'] == 'login-rejected':
        return 'unavailable', 'Network Rail did not accept the login'
    if STATE['stations'] is None:
        return ('unavailable', 'Station list unavailable · retrying') if STATE['referenceError'] else ('ready', 'Preparing the station list…')
    if not STATE['connected']:
        if STATE['lastError'] in ('feed-error',):
            return 'unavailable', 'Network Rail feed refused the subscription'
        return 'ready', 'Connecting to Network Rail…'
    if now - STATE['connectedAt'] < 600:
        return 'ready', ''
    return 'ready', ''


# 0.5.0 (owner 5 Nov 00:16 "graphics work for the train types"): what kind of train it is, from the first digit of its
# headcode (the national rules for train reporting numbers), and who runs it, from TRUST's operator code (the "sector
# code" in the Open Rail Data wiki's TOC Codes list). The exact class of train needs the timetable feed - later.
KINDS = {'0': ('engine', 'Engine on its own'), '1': ('express', 'Express passenger train'), '2': ('stopping', 'Stopping passenger train'),
         '3': ('special', 'Parcels, mail or special train'), '4': ('freight', 'Fast freight train'), '5': ('empty', 'Empty passenger train'),
         '6': ('freight', 'Freight train'), '7': ('freight', 'Freight train'), '8': ('freight', 'Slow freight train'),
         '9': ('stopping', 'Passenger train')}
OPERATORS = {'02': 'JSD Rail Research & Development', '04': 'DB Cargo', '05': 'DB Cargo', '06': 'Eurostar', '07': 'Rail Operations Group',
             '08': 'DB Cargo', '09': 'Freightliner', '10': 'Serco Rail Operations', '11': 'Freightliner', '12': 'Grand Union Trains',
             '13': 'Europorte Channel', '14': 'Grand Central', '15': 'Network Rail (track machines)', '16': 'Loram', '17': 'Hanson & Hall Rail Services',
             '18': 'Swanage Railway', '19': 'South Yorkshire Supertram', '20': 'TransPennine Express', '21': 'Greater Anglia', '22': 'Grand Central',
             '23': 'Northern', '25': 'Great Western Railway', '27': 'CrossCountry', '28': 'East Midlands Railway', '29': 'West Midlands Trains',
             '30': 'London Overground', '31': 'Network Rail', '33': 'Elizabeth line', '34': 'DC Rail', '35': 'Caledonian Sleeper',
             '36': 'Vintage Trains', '39': 'Harsco', '40': 'Balfour Beatty Rail', '42': 'Colas Rail', '43': 'Amey', '44': 'Carillion Rail',
             '45': 'Lumo', '46': 'SB Rail', '49': 'VolkerRail', '50': 'West Coast Railways', '51': 'North Yorkshire Moors Railway',
             '52': 'Pre Metro Operations', '53': 'SNCF Freight Services', '54': 'GB Railfreight', '55': 'Hull Trains', '56': 'Tyne and Wear Metro',
             '57': 'Go-Op', '59': 'On Route Logistics', '60': 'ScotRail', '61': 'LNER', '62': 'Fishbone Solutions', '64': 'Merseyrail',
             '65': 'Avanti West Coast', '71': 'Transport for Wales', '72': 'Legge Infrastructure Services', '74': 'Chiltern Railways',
             '79': 'c2c', '80': 'Southeastern', '84': 'South Western Railway', '85': 'Island Line', '86': 'Heathrow Express',
             '88': 'Govia Thameslink Railway', '89': 'Locomotive Services', '92': 'Network Rail', '94': 'Ffestiniog Railway',
             '95': 'Varamis Rail', '97': 'Direct Rail Services'}


def uk_clock(ts: float) -> str:
    """Times in the box are UK times, whatever the server's own time zone is."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo('Europe/London')).strftime('%H:%M')
    except Exception:
        return time.strftime('%H:%M', time.localtime(ts))


# TRUST's two-digit operator code -> the timetable's two-letter operator code (passenger operators; Open Rail Data wiki)
ATOC_OF = {'06': 'ES', '20': 'TP', '21': 'LE', '22': 'GC', '23': 'NT', '25': 'GW', '27': 'XC', '28': 'EM', '29': 'LM',
           '30': 'LO', '33': 'XR', '35': 'CS', '45': 'LD', '55': 'HT', '60': 'SR', '61': 'GR', '64': 'ME', '65': 'VT',
           '71': 'AW', '74': 'CH', '79': 'CC', '80': 'SE', '84': 'SW', '85': 'IL', '86': 'HX'}


def kind_of(t: dict):
    return KINDS.get((t.get('headcode') or ' ')[0], ('', ''))


def details(entity_id: str) -> dict:
    out = {'component': COMPONENT, 'id': entity_id, 'rows': []}
    tid = entity_id[5:] if entity_id.startswith('rail:') else ''
    with LOCK:
        t = dict(STATE['trains'].get(tid) or {})
    if not t:
        out['note'] = 'This train has not reported recently.'
        return out
    rows = out['rows']

    def add(label, value):
        if value:
            rows.append({'label': label, 'value': str(value)[:120]})
    if kind_of(t)[0]:
        out['picture'] = kind_of(t)[0]      # owner 00:19: "a picture of the type in an information window" (HQ draws it)
    what, drawing = booked_class(t)         # 0.8.0: its actual type, from the timetable
    if drawing:
        out['picture'] = drawing
    add('Train', t['headcode'])
    add('Type', kind_of(t)[1])
    add('Train type', what)
    add('Operator', OPERATORS.get(t.get('toc', '')))
    if t.get('platform') and t.get('platformAt'):
        with LOCK:
            place = ((STATE['stations'] or {}).get(t['platformAt']) or STATE['points'].get(t['platformAt']) or (0, 0, ''))[2]
        add('Platform', f"{t['platform']}" + (f' at {place}' if place else ''))
    what = 'Passed' if t.get('point') else {'ARRIVAL': 'Arrived at', 'DEPARTURE': 'Departed'}.get(t['event'], 'Passed')
    if t.get('cancelled'):
        add('Status', 'Cancelled')
    elif t['terminated']:
        what = 'Terminated at'
    add('Last report', f"{what} {t['station']} at {uk_clock(t['when'])}")
    if t['variation'] == 'LATE' and t['minutes']:
        add('Running', f"{t['minutes']} min late")
    elif t['variation'] == 'EARLY' and t['minutes']:
        add('Running', f"{t['minutes']} min early")
    elif t['variation'] == 'ON TIME':
        add('Running', 'On time')
    if t.get('run') and t.get('nname') and t['event'] == 'DEPARTURE' and not t['terminated'] and not t.get('cancelled'):
        add('Next report', f"{t['nname']}, about {uk_clock(t['when'] + t['run'] * 60)}")
    return out


def describe(t: dict) -> str:
    what = 'passed' if t.get('point') else {'ARRIVAL': 'arrived at', 'DEPARTURE': 'departed'}.get(t['event'], 'passed')
    parts = [f"Train {t['headcode']}" if t['headcode'] else 'Train', f"{what} {t['station']}"]
    if t.get('cancelled'):
        parts[1] = f"cancelled · last at {t['station']}"
    elif t['terminated']:
        parts[1] = f"terminated at {t['station']}"
    if t['variation'] == 'LATE' and t['minutes']:
        parts.append(f"{t['minutes']} min late")
    elif t['variation'] == 'EARLY' and t['minutes']:
        parts.append(f"{t['minutes']} min early")
    elif t['variation'] == 'ON TIME':
        parts.append('on time')
    if t.get('run') and t.get('nname') and t['event'] == 'DEPARTURE' and not t['terminated'] and not t.get('cancelled'):
        parts.append(f"next: {t['nname']}")
    return ' · '.join(parts)


def note_demand() -> None:
    with LOCK:
        STATE['lastDemandAt'] = time.time()
    WAKE.set()


def snapshot(view) -> dict:
    now = time.time()
    note_demand()
    with LOCK:
        h, notice = health(now)
        trains = list(STATE['trains'].items())
        connected_for = now - STATE['connectedAt'] if STATE['connected'] else 0
    frame_out = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
                 'observedAt': iso(now), 'receivedAt': iso(now), 'expiresAt': iso(now + 90),
                 'nextPollAfterMs': 15000, 'attribution': ATTRIBUTION, 'entities': [],
                 # 0.4.3 (owner 4 Nov 11:20: unreadable headcodes where trains sit on top of each other): one badge per pile
                 'groupStacks': True}
    if view is None or view[2] - view[0] > MAX_VIEW_SPAN:
        frame_out['notice'] = 'Zoom in to see trains'
        return frame_out
    if notice:
        frame_out['notice'] = notice
    s, w, n, e = view
    clat, clon = (s + n) / 2, (w + e) / 2
    inside = []
    for tid, t in trains:
        age = now - t['when']
        if age > DROP_AFTER or (t['terminated'] and age > TERMINATED_KEEP) or (t.get('cancelled') and now - t.get('cancelledAt', 0) > CANCELLED_KEEP):
            continue
        # 0.4.1: also send a train that is heading INTO this area, so it glides in instead of popping up later.
        heading_in = bool(t.get('run')) and s <= t['nlat'] <= n and w <= t['nlon'] <= e
        if (s <= t['lat'] <= n and w <= t['lon'] <= e) or heading_in:
            inside.append(((t['lat'] - clat) ** 2 + (t['lon'] - clon) ** 2, tid, t, age))
    inside.sort(key=lambda x: x[0])
    on_platform = 0
    for _, tid, t, age in inside[:MAX_PER_VIEW]:
        drawn = drawn_at(t)              # 0.7.0: beside its platform when we know it
        on_platform += drawn[1]
        entity = {'id': 'rail:' + tid, 'label': describe(t)[:160], 'badge': t['headcode'] or None,
                  'variant': kind_of(t)[0] or None,      # 0.5.0: which of TPA's train drawings to show
                  'position': dict(zip(('lon', 'lat'), drawn[0]), headingDeg=None),
                  'quality': 'observed' if age <= FRESH_FOR and not t.get('cancelled') else 'stale', 'observedAt': iso(t['when'])}
        if t.get('run') and t['event'] == 'DEPARTURE' and not t['terminated'] and not t.get('cancelled'):
            key = leg_key(t)
            with LOCK:
                path = ROUTES.get(key)
                if path is None and STATE['trackLoaded'] and key not in PENDING_SET and len(PENDING) < 5000:
                    PENDING.append(key); PENDING_SET.add(key); ROUTE_WAKE.set()
            if path:
                arrive = t.get('arriveBy') or (t['when'] + t['run'] * 60)     # 0.6.0: re-timed by the signals when known
                entity['motion'] = {'mode': 'path', 'points': path,
                                    'arriveAt': iso(arrive), 'maxSeconds': round(max(60, arrive - t['when']) + 120)}
        frame_out['entities'].append(entity)
    with LOCK:
        STATE['drawnOnPlatform'] = on_platform
    if not frame_out['entities'] and h == 'ready' and not notice and connected_for < 600:
        frame_out['notice'] = 'Listening · trains appear as they report at stations'
    elif len(inside) > MAX_PER_VIEW and not notice:
        frame_out['notice'] = f'Showing nearest {MAX_PER_VIEW} · zoom in for all'
    return frame_out


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
    server_version = 'TPA-Train-Engine'
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
                return self.reply(200, {'component': COMPONENT, 'engine': 'tpa-train-engine', 'engineVersion': ENGINE_VERSION,
                                        'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice,
                                        'loginConfigured': HAVE_LOGIN, 'connected': STATE['connected'],
                                        'stationsKnown': len(STATE['stations'] or {}), 'referenceError': STATE['referenceError'],
                                        'lastError': STATE['lastError'], 'trainsTracked': len(STATE['trains']),
                                        'timingPointsKnown': len(STATE['points']), 'movementsAtTimingPoints': STATE['atPoints'],
                                        'trainsRestoredAtStart': STATE['restored'], 'alwaysOn': ALWAYS_ON,
                                        'trackMapLoaded': STATE['trackLoaded'], 'trackRoutesKnown': sum(1 for v in ROUTES.values() if v),
                                        'trackRoutesWaiting': len(PENDING), 'trackError': STATE['trackError'],
                                        'cancellationsSeen': STATE['cancelledSeen'], 'reinstatementsSeen': STATE['reinstatedSeen'],
                                        'movementsReceived': STATE['movements'], 'movementsAtUnmappedLocations': STATE['unplaced'],
                                        'feedKBThisRun': STATE['bytes'] // 1024,
                                        'signalling': {'on': TD_ON, 'refusedUntil': iso(STATE['tdBlockedUntil']) if STATE['tdBlockedUntil'] > time.time() else None, 'smartSteps': STATE['smartSteps'], 'smartError': STATE['smartError'],
                                                       'smartBuiltAt': iso(STATE['smartBuiltAt']) if STATE['smartBuiltAt'] else None,
                                                       'messages': STATE['tdMessages'], 'useful': STATE['tdUseful'], 'matched': STATE['tdMatched'],
                                                       'retimed': STATE['tdRetimed'], 'arrivedEarly': STATE['tdArrived'], 'platforms': STATE['tdPlatforms']},
                                        'timetable': {'days': STATE['bookedDays'], 'trainsToday': STATE['bookedTrains'], 'error': STATE['scheduleError'],
                                                      'builtAt': iso(STATE['scheduleBuiltAt']) if STATE['scheduleBuiltAt'] else None,
                                                      'activationsSeen': STATE['activations']},
                                        'platformMap': {'known': STATE['platformsKnown'], 'error': STATE['platformsError'],
                                                        'builtAt': iso(STATE['platformsBuiltAt']) if STATE['platformsBuiltAt'] else None,
                                                        'drawnOnPlatformLastView': STATE['drawnOnPlatform']}})
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        if split.path == '/details':
            return self.reply(200, details((parse_qs(split.query).get('id') or [''])[0][:140]))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def leg_key(t: dict):
    return (round(t['lat'], 5), round(t['lon'], 5), round(t['nlat'], 5), round(t['nlon'], 5))


def route_loop() -> None:
    """Load the track map once, then work out queued legs, newest first."""
    global ROUTER
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('tpa_track_router', Path(__file__).resolve().parent / 'track_router.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        router = module.TrackRouter(TRACK_FILE)
    except Exception as exc:  # missing/damaged file: trains just don't glide
        with LOCK:
            STATE['trackError'] = (type(exc).__name__ + ' ' + str(exc))[:60]
        print('Track map not available - trains will not glide:', STATE['trackError'], flush=True)
        return
    ROUTER = router
    with LOCK:
        STATE['trackLoaded'] = True
    print(f'Track map ready: {len(router.nodes)} junctions', flush=True)
    while True:
        ROUTE_WAKE.wait(timeout=5)
        ROUTE_WAKE.clear()
        while True:
            with LOCK:
                if not PENDING:
                    break
                key = PENDING.pop()
                PENDING_SET.discard(key)
            path = router.route((key[1], key[0]), (key[3], key[2]))
            with LOCK:
                ROUTES[key] = [[x, y] for x, y in path] if path else False
                if len(ROUTES) > 30000:
                    for k in list(ROUTES)[:10000]:
                        ROUTES.pop(k, None)


def main() -> None:
    port = int(os.environ.get('TPA_RAIL_ENGINE_PORT', '8784'))
    points = load_timing_points()
    with LOCK:
        STATE['points'] = points
    restored = restore_memory()
    threading.Thread(target=feed_loop, daemon=True).start()
    threading.Thread(target=memory_loop, daemon=True).start()
    threading.Thread(target=route_loop, daemon=True).start()
    threading.Thread(target=smart_loop, daemon=True).start()
    threading.Thread(target=platforms_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA TRAIN ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('Network Rail login: ' + ('set for this session' if HAVE_LOGIN else 'NOT SET - no train data will be fetched'), flush=True)
        print('Listens to Network Rail only while the map is open (and 10 min after).', flush=True)
        print(f'Timing points known: {len(points)} · trains remembered from last run: {restored}', flush=True)
        print('Keep this window open. Close it to stop trains only.', flush=True)
        print('=' * 60, flush=True)
        try:
            server.serve_forever()
        finally:
            save_memory(force=True)


if __name__ == '__main__':
    main()
