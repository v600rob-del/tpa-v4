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

ENGINE_VERSION = '1.5.0'
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
# 1.4.1 (launch list, owner 6 Nov 14:24): the public servers' own rules (https://wiki.openstreetmap.org/wiki/Overpass_API):
# private.coffee welcomes any project, so it is asked first; the main server (which asks commercial users to go
# elsewhere) is only the backup; kumi.systems was private.coffee's old name, the same service twice. We ask 5 times a month.
OVERPASS_URLS = ['https://overpass.private.coffee/api/interpreter', 'https://overpass-api.de/api/interpreter']
OVERPASS_BUSY_PAUSE = 30      # the servers ask for 30 seconds' rest after "too busy" (429) or "timed out" (504)
CONTACT = '+mailto:enquiries@tpalive.co.uk'
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
# 1.4.0 (owner 5 Nov 22:05, "belt and braces, Yorkshire style"): after Network Rail refuses the login, try again after
# 2, 5, then every 10 minutes (was 30), and after the live feed has been away this long, draw timetable trains instead
def _seconds_list(text, default):
    try:
        got = [max(1, int(x)) for x in str(text).split(',') if x.strip()]
        return got or default
    except ValueError:
        return default
RETRY_AFTER_REFUSAL = _seconds_list(os.environ.get('TPA_RAIL_RETRY_SECONDS', ''), [120, 300, 600])
TIMETABLE_AFTER = max(1, int(os.environ.get('TPA_RAIL_TIMETABLE_AFTER', '180') or 180))
STARTED_AT = time.time()
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
         'loginRefusals': 0, 'networkRailSaid': '', 'networkRailSaidAt': 0.0, 'liveLostAt': time.time(),   # 1.4.0
         'lastDemandAt': 0.0, 'messages': 0, 'movements': 0, 'unplaced': 0, 'bytes': 0,
         'points': {}, 'atPoints': 0, 'restored': 0, 'startsRestored': 0, 'dirty': False, 'savedAt': 0.0,
         'trackLoaded': False, 'trackError': '', 'cancelledSeen': 0, 'reinstatedSeen': 0,
         'smartSteps': 0, 'smartError': '', 'smartBuiltAt': 0.0, 'tdMessages': 0, 'tdUseful': 0, 'tdMatched': 0,
         'tdRetimed': 0, 'tdArrived': 0, 'tdPlatforms': 0, 'tdBlockedUntil': 0.0, 'tdThisSession': False,
         'platformsKnown': 0, 'platformsError': '', 'platformsBuiltAt': 0.0, 'drawnOnPlatform': 0,
         'bookedDays': [], 'bookedTrains': 0, 'scheduleError': '', 'scheduleBuiltAt': 0.0, 'activations': 0, 'placeMatched': 0, 'placeDropped': 0, 'withClass': 0,
         'tiplocs': {}, 'onTimetable': 0, 'routesToday': 0}
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

    headers = {'User-Agent': f'TPA-Train-Engine/{ENGINE_VERSION} ({CONTACT})',
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


def tiploc_codes(corpus_payload: bytes) -> dict:
    """1.0.0: Network Rail location code (TIPLOC, as in the timetable) -> STANOX, for every place CORPUS knows."""
    corpus = json.loads(corpus_payload)
    records = corpus.get('TIPLOCDATA') if isinstance(corpus, dict) else corpus
    out = {}
    for item in records if isinstance(records, list) else []:
        if isinstance(item, dict):
            stanox, tiploc = str(item.get('STANOX') or '').strip(), str(item.get('TIPLOC') or '').strip().upper()
            if re.fullmatch(r'\d{3,6}', stanox) and re.fullmatch(r'[A-Z0-9]{3,7}', tiploc):
                out[tiploc] = stanox.zfill(5)
    return out


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
        if time.time() - cached.get('builtAt', 0) < REFERENCE_MAX_AGE and len(cached.get('stations', {})) >= 50 and cached.get('tiplocs'):
            with LOCK:
                STATE['stations'] = {k.zfill(5): tuple(v) for k, v in cached['stations'].items()}
                STATE['tiplocs'] = dict(cached['tiplocs'])
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
        tiplocs = tiploc_codes(corpus)
    except Exception as exc:
        return _ref_fail('match-' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)[:30])
    if len(stations) < 50:
        return _ref_fail('too-few-stations')
    with LOCK:
        STATE['stations'], STATE['referenceError'] = {k.zfill(5): v for k, v in stations.items()}, ''
        STATE['tiplocs'] = tiplocs
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = REFERENCE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'builtAt': time.time(), 'stations': stations, 'tiplocs': tiplocs}), encoding='utf-8')
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
                'uid': str, 'day': str,   # 0.8.0: its timetable entry
                'uidBy': str,             # 1.5.0: 'place' when the entry was worked out from where and when it reported
                'arrivingAt': str}        # 0.9.0: the signals say it has arrived here; shown once the map gets it there   # 0.3.0: next place + minutes to it


def save_memory(force: bool = False) -> None:
    now = time.time()
    with LOCK:
        if not (STATE['dirty'] or force) or (not force and now - STATE['savedAt'] < SAVE_EVERY):
            return
        keep = {k: v for k, v in STATE['trains'].items() if now - v['when'] < DROP_AFTER}
        # 1.3.0 (owner 5 Nov 23:37 "very very few with the train pics in" after a restart): train starts too, so a train
        # Network Rail activated before a restart still knows its timetable (and so its type, picture and stops) after it
        days = (uk_today(-1), uk_today())
        starts = {k: list(v) for k, v in ACTIVATED.items() if v[1] in days}
        STATE['dirty'], STATE['savedAt'] = False, now
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'savedAt': now, 'engineVersion': ENGINE_VERSION, 'trains': keep, 'starts': starts},
                                  separators=(',', ':')), encoding='utf-8')
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
    starts = saved.get('starts', {}) if isinstance(saved, dict) else {}
    days, back = (uk_today(-1), uk_today()), {}
    for tid, v in list(starts.items())[:60000] if isinstance(starts, dict) else []:
        if (isinstance(tid, str) and re.fullmatch(r'[0-9A-Z]{10}', tid) and isinstance(v, list) and len(v) == 2
                and isinstance(v[0], str) and re.fullmatch(r'[A-Z0-9 ]{6}', v[0]) and v[1] in days):
            back[tid] = (v[0], v[1])
    with LOCK:
        for tid, v in back.items():
            ACTIVATED.setdefault(tid, v)
        STATE['startsRestored'] = len(back)
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
        for k in ('stanox', 'nstanox', 'platformAt', 'arrivingAt'):
            item[k] = item[k] if re.fullmatch(r'\d{5,6}', item[k]) else ''
        item['platform'] = item['platform'] if re.fullmatch(r'[0-9A-Z]{1,3}', item['platform']) else ''
        item['uid'] = item['uid'] if re.fullmatch(r'[A-Z0-9 ]{6}', item['uid']) else ''
        item['uidBy'] = 'place' if item['uidBy'] == 'place' and item['uid'] else ''
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
                STATE['dirty'] = True                 # 1.3.0: train starts are saved with the memory
                if len(ACTIVATED) > 60000:
                    for k in list(ACTIVATED)[:20000]:
                        ACTIVATED.pop(k, None)
                t = trains.get(train_id)
                if t:
                    t.update(uid=ACTIVATED[train_id][0], day=ACTIVATED[train_id][1], uidBy='')   # 1.5.0: beats a guess
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
                            'day': (ACTIVATED.get(train_id) or ('', before.get('day', '')))[1],
                            'uidBy': '' if train_id in ACTIVATED else before.get('uidBy', '')}
        find_by_place(trains[train_id])
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
            time.sleep(OVERPASS_BUSY_PAUSE if problem in ('platforms-http-429', 'platforms-http-504') else 2)
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
SCHED_STOP = re.compile(rb'\{[^{}]*"tiploc_code"[^{}]*\}')
STOP_FIELD = {k: re.compile(rb'"' + k.encode() + rb'"\s*:\s*"([^"]*)"') for k in ('tiploc_code', 'arrival', 'departure', 'pass')}


def clock_seconds(hhmm: str):
    """'1230' -> 45000, '1230H' -> 45030 (seconds after midnight), else None."""
    m = re.fullmatch(r'([01]\d|2[0-3])([0-5]\d)(H?)', hhmm.strip())
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + (30 if m.group(3) else 0)) if m else None


def booked_stops(line: bytes) -> str:
    """1.0.0: a timetable entry's places in order, as 'TIPLOC,arrive,leave;...' (seconds after midnight, the next day
    going past 86400), kept as one short string per train so a whole day fits in a few megabytes."""
    out, last = [], None
    for m in SCHED_STOP.finditer(line):
        obj = m.group(0)
        def f(k):
            g = STOP_FIELD[k].search(obj)
            return g.group(1).decode('ascii', 'replace') if g else ''
        tiploc = f('tiploc_code').strip().upper()
        arr, dep, pas = clock_seconds(f('arrival')), clock_seconds(f('departure')), clock_seconds(f('pass'))
        a, d = (pas, pas) if pas is not None else (arr if arr is not None else dep, dep if dep is not None else arr)
        if not re.fullmatch(r'[A-Z0-9]{3,7}', tiploc) or a is None:
            continue
        if last is not None:
            while a < last - 6 * 3600:
                a += 86400
                d += 86400
        if d < a:
            d += 86400
        last = d
        out.append(f'{tiploc},{a},{d}')
    return ';'.join(out[:120])


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
                                    clean(field('signalling_id'), 4).strip().upper(), clean(field('atoc_code'), 2).strip().upper(),
                                    booked_stops(line) if d == days[0] else '')
    if gz and unzip is not None:
        rest += unzip.flush()
    out = {d: {uid: [v[1], v[2]] for uid, v in best[d].items() if uid not in cancelled[d] or v[0] > 1} for d in days}
    out[days[0]]['#stops'] = {uid: v[5] for uid, v in best[days[0]].items() if v[5] and (uid not in cancelled[days[0]] or v[0] > 1)}
    # 0.8.1 (owner 11:57, 1A35 left Piccadilly after the update but showed no class - Network Rail had "activated" it about
    # an hour before it left, before the update): by headcode too. A headcode whose trains that day are all booked the
    # same is enough to know what a train is without its activation (a headcode shared by different types: unknown).
    for d in days:
        heads, uids = {}, {}
        for uid, v in best[d].items():
            if (uid not in cancelled[d] or v[0] > 1) and re.fullmatch(r'[0-9][A-Z][0-9]{2}', v[3]):
                heads.setdefault(v[3], set()).add((v[1], v[2]))
                # 0.8.2 (owner 12:16, 1A36 Avanti out of Piccadilly: no type - other operators also run a "1A36" today with
                # other trains): headcode + operator too, which is almost always enough
                if re.fullmatch(r'[A-Z]{2}', v[4]):
                    heads.setdefault(v[3] + '|' + v[4], set()).add((v[1], v[2]))
                    uids.setdefault(v[3] + '|' + v[4], []).append(uid)
        out[d]['#heads'] = {h: list(next(iter(kinds))) for h, kinds in heads.items() if len(kinds) == 1}
        if d == days[0]:   # 1.4.0: each train's headcode and operator, for timetable trains while the live feed is away
            out[d]['#headOf'] = {uid: v[3] + '|' + v[4] for uid, v in best[d].items()
                                 if v[5] and re.fullmatch(r'[0-9][A-Z][0-9]{2}', v[3]) and (uid not in cancelled[d] or v[0] > 1)}
        out[d]['#uidOf'] = {h: u[0] for h, u in uids.items() if len(u) == 1}   # 1.0.0: the one train with that headcode + operator
    return out


def load_schedule() -> bool:
    days = [uk_today(), uk_today(1)]
    try:
        cached = json.loads(CLASSES_FILE.read_text(encoding='utf-8'))
        if days[1] in cached.get('days', {}) and cached.get('v') in (3, 4):   # made today (1.4.0 adds '#headOf'; v3 still fine)
            BOOKED.clear(); BOOKED.update(cached['days'])
            with LOCK:
                STATE.update(bookedDays=sorted(BOOKED), bookedTrains=sum(1 for k in BOOKED.get(days[0], {}) if k[:1] != '#'), scheduleError='',
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
            'User-Agent': f'TPA-Train-Engine/{ENGINE_VERSION} ({CONTACT})',
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
    if len(booked.get(days[0], {})) < 4:
        with LOCK:
            STATE['scheduleError'] = 'schedule-empty'
        return False
    BOOKED.clear(); BOOKED.update(booked)
    built = time.time()
    with LOCK:
        STATE.update(bookedDays=sorted(BOOKED), bookedTrains=sum(1 for k in booked[days[0]] if k[:1] != '#'), scheduleError='', scheduleBuiltAt=built)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = CLASSES_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'v': 4, 'builtAt': built, 'days': booked}), encoding='utf-8')
        tmp.replace(CLASSES_FILE)
    except OSError:
        pass
    print(f'Timetable ready: {sum(1 for k in booked[days[0]] if k[:1] != "#")} trains booked today, with what each is booked to be and where it goes', flush=True)
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
                  '700': ('700', '707', '717'), '755': ('231', '745', '755', '756'), '387': ('357', '375', '377', '378', '379', '387'),
                  # 1.3.0 (owner 6 Nov 10:24-10:57, second batch of pictures)
                  '165': ('165', '166'), '777': ('777',), '350': ('350', '360', '444', '450'), '385': ('385',), '323': ('323',),
                  '185': ('185',), '395': ('395',), '380': ('380',), '333': ('333',)}
DRAWING_OF = {c: k for k, cs in CLASS_DRAWINGS.items() for c in cs}
CLASS_NAMES = {'800': 'Hitachi Azuma / IET', '390': 'Alstom Pendolino', '220': 'Bombardier Voyager', '150': 'Sprinter',
               '158': 'Express Sprinter', '170': 'Turbostar', '195': 'CAF Civity', '345': 'Aventra', '700': 'Siemens Desiro City',
               '755': 'Stadler FLIRT', '387': 'Bombardier Electrostar', '43': 'InterCity 125 (HST)',
               '165': 'Networker Turbo', '777': 'Stadler', '350': 'Siemens Desiro', '385': 'Hitachi AT200', '185': 'Siemens Desiro',
               '395': 'Hitachi Javelin', '380': 'Siemens Desiro', '333': 'CAF'}
POWER = {'D': 'a diesel locomotive', 'E': 'an electric locomotive', 'ED': 'an electro-diesel locomotive'}
# 1.1.1 (owner 6 Nov 09:17-09:34, 457 of 767 trains with no type): the timetable's letter and special timing loads
# (https://wiki.openraildata.com/index.php/CIF_Codes). A class number here goes through the ordinary path below;
# a tuple is (what to say, which drawing) when it covers several classes.
DMU_LOADS = {'S': ('Class 150, 153, 155 or 156 · Sprinter', 'c150'), 'V': ('Class 220 or 221 · Bombardier Voyager', 'c220'),
             'X': '159', '69': '172', 'N': ('Class 165 · Networker Turbo', 'c165'), 'T': ('Class 165 or 166 · Networker Turbo', 'c165'),
             'A': ('Class 141 to 144 · Pacer', '')}
EMU_LOADS = {'0': '380', 'E': '458', '506': '350'}
# DMU "E" means Class 158, 168, 170 or 175: named only for operators that run just one of those
DMU_E_BY_OPERATOR = {'XC': '170', 'GW': '158', 'CH': '168'}
# 1.3.0 (owner 6 Nov 10:17 count: ~9,300 electric trains a day booked with no type): an operator that runs only one kind
# (TRUST operator code -> class); otherwise say so honestly, with TPA's generic picture for electric trains
BLANK_EMU_BY_OPERATOR = {'64': '777',    # Merseyrail: all Class 777 since the 507s went
                         '88': '377'}    # Govia Thameslink: Thameslink (700) and Great Northern (717/387) are booked with
                                         # their class; the ones booked blank are Southern's Electrostars
BLANK_DMU_BY_OPERATOR = {'20': '185'}    # TransPennine Express: its diesel units are all Class 185


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
    if power in ('EMU', 'EML') and not load:
        load = BLANK_EMU_BY_OPERATOR.get(t.get('toc', ''), '')
        if not load:
            return 'Electric train (type not given)', 'celectric'
    elif power == 'DMU' and not load:
        load = BLANK_DMU_BY_OPERATOR.get(t.get('toc', ''), '')
        if not load:
            return 'Diesel train (type not given)', 'cdiesel'   # 1.3.1 (owner 11:39): TPA's generic diesel picture
    if power == 'DMU' and load == 'E':
        load = DMU_E_BY_OPERATOR.get(ATOC_OF.get(t.get('toc', ''), ''), '')
        if not load:
            return 'Class 158 or 170', 'cdiesel'
    elif power == 'DMU' and load in DMU_LOADS:
        load = DMU_LOADS[load]
    elif power in ('EMU', 'EML') and load in EMU_LOADS:
        load = EMU_LOADS[load]
    if isinstance(load, tuple):
        return load
    if power == 'HST':
        return 'Class 43 · ' + CLASS_NAMES['43'], 'c43'
    if re.fullmatch(r'\d{3}', load) and power in ('EMU', 'DMU', 'DEM', 'EML'):
        drawing = DRAWING_OF.get(load, '')
        name = CLASS_NAMES.get(drawing, '')
        return f'Class {load}' + (f' · {name}' if name else ''), ('c' + drawing if drawing else '')
    if power in POWER:
        diesel = power == 'D'
        return 'Hauled by ' + POWER[power], ('c66' if diesel and kind_of(t)[0] == 'freight' else '')
    return '', ''


FASTEST = 56.0          # m/s (125 mph): the most a train is ever shown doing
SLOWEST_CATCH_UP = 20.0 # m/s (45 mph): even a slow stretch may be caught up at this pace


def path_metres(path) -> float:
    return sum(km(a[1], a[0], b[1], b[0]) * 1000 for a, b in zip(path, path[1:]))


def believable_arrival(t: dict, now: float) -> float:
    """The soonest the train SHOWN on the map can reach its next stop at a believable speed (0 when unknown)."""
    path = ROUTES.get(leg_key(t))
    if not path or not t.get('run'):
        return 0.0
    length = path_metres(path)
    planned = t.get('arriveBy') or (t['when'] + t['run'] * 60)
    if length <= 0 or planned <= t['when']:
        return 0.0
    done = min(1.0, max(0.0, (now - t['when']) / (planned - t['when'])))
    booked = length / (t['run'] * 60)
    pace = min(FASTEST, max(SLOWEST_CATCH_UP, booked * 1.4))
    return now + length * (1 - done) / pace


def arrive_now(t: dict, stanox: str, at: float, now: float) -> bool:
    station = (STATE['stations'] or {}).get(stanox) or STATE['points'].get(stanox)
    if not station:
        return False
    t.update(lat=station[0], lon=station[1], station=station[2], stanox=stanox, event='ARRIVAL',
             when=max(t['when'], min(now, at)), point=stanox not in (STATE['stations'] or {}),
             nlat=0.0, nlon=0.0, nname='', nstanox='', run=0, arriveBy=0.0, arrivingAt='')
    STATE['tdArrived'] += 1
    return True


def settle_arrivals(now: float) -> None:
    """A train the signals said had arrived is shown arrived once the map has glided it there (0.9.0)."""
    for t in STATE['trains'].values():
        if t.get('arrivingAt') and t['event'] == 'DEPARTURE' and t.get('arriveBy') and now >= t['arriveBy']:
            if t['arrivingAt'] == t.get('nstanox'):
                arrive_now(t, t['arrivingAt'], t['arriveBy'], now)
            else:
                t['arrivingAt'] = ''


# ---------------------------------------------------------------- 1.0.0: carrying on along the booked route
# (owner 5 Nov 12:27, 1A36 south of Stockport: "sat for a minute or more then reappeared a couple of miles down the
# track" - Network Rail only reports some trains at a few points, so the map ran out of road at the last one). Once a
# train has reached the last place it was reported heading for, it carries on along its booked route at its booked
# times (shifted by how late it is), place by place, until the next real report takes over - for up to 20 minutes.
ONWARD_LIMIT = 20 * 60
STOPS_CACHE = {}


def uk_midnight(day: str) -> float:
    try:
        from zoneinfo import ZoneInfo
        return datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Europe/London')).timestamp()
    except Exception:
        return datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------- 1.5.0: which timetable entry, from where and when
# (owner 12 Nov 10:38: "a few greyed out trains on my travels"; launch list 5d part 2). A train whose start Network Rail
# told us about before we were listening has no link to its timetable entry; the headcode + operator alone only links it
# when that pair is used once that day, and many are used several times. Without its entry it cannot carry on along its
# booked route when the reports run out, so it sat at its last place and faded. Now the place it reported at and the
# time (moved by how late Network Rail says it is) pick the entry, but only a clear winner.
PLACE_WINDOW = 10 * 60      # the booked time (plus lateness) must be within 10 minutes of the report
PLACE_MARGIN = 5 * 60       # ... and the next best at least 5 minutes further off, or no guess
HEAD_INDEX = {'src': None, 'by': {}}


def entries_with_headcode(head: str) -> list:
    """Today's timetable entries carrying this headcode, as (uid, operator)."""
    src = (BOOKED.get(uk_today()) or {}).get('#headOf') or {}
    if HEAD_INDEX['src'] is not src:
        by = {}
        for uid, key in src.items():
            h, _, atoc = key.partition('|')
            by.setdefault(h, []).append((uid, atoc))
        HEAD_INDEX.update(src=src, by=by)
    return HEAD_INDEX['by'].get(head) or []


def match_by_place(t: dict) -> str:
    head, stanox = t.get('headcode'), t.get('stanox')
    if not head or not stanox:
        return ''
    found = entries_with_headcode(head)
    atoc = ATOC_OF.get(t.get('toc', ''), '')
    if atoc:                                          # an operator we know: only its own trains
        found = [f for f in found if f[1] == atoc]
    base = uk_midnight(uk_today())
    late = t.get('minutes', 0) * 60 * (1 if t.get('variation') == 'LATE' else -1 if t.get('variation') == 'EARLY' else 0)
    scored = []
    for uid, _ in found[:60]:
        gaps = [abs(t['when'] - (base + booked + late)) for tiploc, a, d in stops_of(uid)
                if STATE['tiplocs'].get(tiploc) == stanox for booked in (a, d)]
        if gaps:
            scored.append((min(gaps), uid))
    scored.sort()
    if not scored or scored[0][0] > PLACE_WINDOW or (len(scored) > 1 and scored[1][0] - scored[0][0] < PLACE_MARGIN):
        return ''
    return scored[0][1]


def find_by_place(t: dict) -> None:
    """Call with LOCK held, after a report. Keeps a guess while the train stays on its route; never touches an activation."""
    if t.get('uidBy') == 'place':
        stops = stops_of(t['uid']) if t.get('uid') else []
        if stops and index_of(stops, t.get('stanox', '')) is not None:
            return
        t.update(uid='', day='', uidBy='')            # reported somewhere its guessed entry never goes: think again
        STATE['placeDropped'] += 1
    if t.get('uid') or train_uid(t):
        return
    uid = match_by_place(t)
    if uid:
        t.update(uid=uid, day=uk_today(), uidBy='place')
        STATE['placeMatched'] += 1


def link_counts() -> dict:
    """Call with LOCK held. Trains heard in the last 10 minutes: how many know their timetable entry, and how."""
    now, out = time.time(), {'recentTrains': 0, 'recentLinked': 0, 'recentLinkedByPlace': 0, 'recentStale': 0}
    for t in STATE['trains'].values():
        age = now - t['when']
        if age > DROP_AFTER or t.get('terminated'):
            continue
        if age > FRESH_FOR:
            out['recentStale'] += train_quality(t, now) == 'stale'
            continue
        out['recentTrains'] += 1
        if t.get('uid') or train_uid(t):
            out['recentLinked'] += 1
            out['recentLinkedByPlace'] += t.get('uidBy') == 'place'
    return out


def train_quality(t: dict, now: float) -> str:
    """1.5.0: a train carrying on along its booked route between reports is an estimate (owner 30 Oct: estimates look
    normal on the map); only one with nothing to go on fades as "stale"."""
    if t.get('cancelled'):
        return 'stale'
    if now - t['when'] <= FRESH_FOR:
        return 'observed'
    return 'estimated' if t.get('vleg') else 'stale'


def train_uid(t: dict) -> str:
    if t.get('uid'):
        return t['uid']
    atoc = ATOC_OF.get(t.get('toc', ''), '')
    if not (atoc and t.get('headcode')):
        return ''
    return ((BOOKED.get(uk_today()) or {}).get('#uidOf') or {}).get(t['headcode'] + '|' + atoc, '')


def stops_of(uid: str):
    got = STOPS_CACHE.get(uid)
    if got is None:
        text = ((BOOKED.get(uk_today()) or {}).get('#stops') or {}).get(uid, '')
        got = []
        for part in text.split(';') if text else []:
            try:
                tiploc, a, d = part.split(',')
                got.append((tiploc, int(a), int(d)))
            except ValueError:
                continue
        if len(STOPS_CACHE) > 20000:
            STOPS_CACHE.clear()
        STOPS_CACHE[uid] = got
    return got


def place(tiploc: str):
    stanox = STATE['tiplocs'].get(tiploc, '')
    spot = (STATE['stations'] or {}).get(stanox) or STATE['points'].get(stanox)
    return (spot[0], spot[1], spot[2], stanox) if spot else None


def index_of(stops, stanox: str, after: int = -1):
    for i in range(after + 1, len(stops)):
        if STATE['tiplocs'].get(stops[i][0]) == stanox:
            return i
    return None


def calls_at(t: dict, stanox: str) -> bool:
    """1.1.0: does the train stop at this place (booked), as far as we know? (Without its timetable: a station, yes.)"""
    uid = train_uid(t)
    stops = stops_of(uid) if uid else []
    i = index_of(stops, stanox) if stops else None
    if i is not None:
        return i == 0 or i == len(stops) - 1 or stops[i][2] > stops[i][1]
    return stanox in (STATE['stations'] or {})


def easing(starts_still: bool, ends_still: bool):
    return 'both' if starts_still and ends_still else 'in' if starts_still else 'out' if ends_still else None


def plan_onwards(t: dict, now: float):
    """The leg of its booked route the train is on now, when real reports have run out (None otherwise)."""
    if now - t['when'] > ONWARD_LIMIT or t.get('cancelled') or t.get('terminated'):
        return None
    leg = t.get('vleg')
    if leg and now < leg['end']:
        return leg
    uid = train_uid(t)
    stops = stops_of(uid) if uid else []
    if len(stops) < 2:
        return None
    # 1.0.1 (owner 13:05, 1W16 at Stockport, zoom 18: it "sat" (no track route for its short hop to Heaton Norris Jn),
    # then jumped there - out of view, so it vanished and "Follow me" lost it): a leg the map could not glide does not
    # move the train; the next leg starts from where it is actually drawn.
    drawn = None
    if leg:                                                         # finished a timetable leg: on to the next place
        idx, reached = leg['idx'], leg['end']
        if ROUTES.get((round(leg['from'][0], 5), round(leg['from'][1], 5), round(leg['to'][0], 5), round(leg['to'][1], 5))) is False:
            drawn = (leg['from'][0], leg['from'][1], leg['fromName'])
    elif t['event'] == 'DEPARTURE' and t.get('run'):
        reached = t.get('arriveBy') or (t['when'] + t['run'] * 60)
        if now < reached or not t.get('nstanox'):
            return None                                             # still on the leg Network Rail told us about
        idx = index_of(stops, t['nstanox'], (index_of(stops, t.get('stanox', '')) or 0) - 1 if t.get('stanox') else -1)
        if not ROUTES.get(leg_key(t)):
            drawn = (t['lat'], t['lon'], t['station'])
    else:                                                           # standing, or just passed a point
        idx, reached = index_of(stops, t.get('stanox', '')), t['when']
    if idx is None or idx >= len(stops) - 1:
        return None
    base = uk_midnight(t.get('day') or uk_today())
    delay = t['minutes'] * 60 * (1 if t['variation'] == 'LATE' else -1 if t['variation'] == 'EARLY' else 0)
    here = (drawn[0], drawn[1], drawn[2], '') if drawn else place(stops[idx][0])
    j = idx + 1
    while j < len(stops) and j <= idx + 8 and place(stops[j][0]) is None:
        j += 1
    there = place(stops[j][0]) if j < len(stops) else None
    if not here or not there:
        return None
    booked = max(20, stops[j][1] - stops[idx][2])
    calls = stops[idx][2] > stops[idx][1]                           # a station it stops at: waits for its booked time
    start = max(reached, base + stops[idx][2] + delay) if calls else reached
    end = max(start + 20, base + stops[j][1] + delay)               # due at the next place at its booked time + lateness
    if end - start > 3 * booked:                                    # (never crawling: booked running time at most x3)
        end = start + booked
    if start - now > 30 * 60:
        return None
    stops_after = stops[j][2] > stops[j][1] or j == len(stops) - 1
    return {'from': [here[0], here[1]], 'fromName': here[2], 'to': [there[0], there[1]], 'toName': there[2],
            'toStanox': there[3], 'start': start, 'end': end, 'idx': j,
            'ease': easing(calls and not drawn, stops_after)}


def onward_loop() -> None:
    while True:
        time.sleep(5)
        now = time.time()
        with LOCK:
            settle_arrivals(now)
            n = 0
            for t in STATE['trains'].values():
                if now - t['when'] > ONWARD_LIMIT:
                    t.pop('vleg', None)
                    continue
                leg = plan_onwards(t, now)
                if leg:
                    t['vleg'] = leg
                    n += 1
                else:
                    t.pop('vleg', None)
            STATE['onTimetable'] = n
            STATE['routesToday'] = len(((BOOKED.get(uk_today()) or {}).get('#stops') or {}))


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
            # 0.9.0 (owner 11:42 "a heck of a sprint", 12:24 "some accuracy info that makes trains whizz about"): the map
            # never squeezes the rest of a journey into less time than a believable speed allows. A train that is
            # behind catches up at that pace and pulls in a little later on the map, instead of rocketing there.
            eta = max(eta, believable_arrival(t, now))
            if eta > now + 5:
                if eta < t['when'] + t['run'] * 60 + 1800:      # re-timed signal by signal
                    t['arriveBy'] = eta
                    t['arrivingAt'] = stanox                    # shown at the station once the map has got it there
                    STATE['tdRetimed'] += 1
            else:                                              # pulling in now: shown at the station straight away
                arrive_now(t, stanox, min(now, when + off), now)
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


def note_network_rail_said(headers: dict, body: bytes) -> None:
    """1.4.0: keep Network Rail's own words when it says no (for the owner: /status, tpa-status and the full page)."""
    parts = [clean(headers.get('message'), 120), clean(body.decode('utf-8', 'replace').replace('\n', ' '), 120)]
    said = ' · '.join(p.strip() for p in parts if p and p.strip())[:160]
    with LOCK:
        STATE['networkRailSaid'], STATE['networkRailSaidAt'] = said or '(no reason given)', time.time()
    print('Network Rail said:', said or '(no reason given)', flush=True)


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
                    note_network_rail_said(headers, body)
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
            STATE.update(connected=True, connectedAt=time.time(), lastMessageAt=time.time(), lastError='', loginRefusals=0)
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
                    note_network_rail_said(headers, body)
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
            if STATE['connected']:
                STATE['liveLostAt'] = time.time()     # 1.4.0: how long the live feed has been away
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
                else:            # 1.4.0: 2, 5, then every 10 minutes (was 30)
                    wait = RETRY_AFTER_REFUSAL[min(STATE['loginRefusals'], len(RETRY_AFTER_REFUSAL) - 1)]
                    STATE['loginRefusals'] += 1
                    STATE['authRejectedUntil'] = time.time() + wait
            if td_refused:
                print('Network Rail refused the signalling feed - carrying on with train reports only for 6 hours', flush=True)
                time.sleep(5)
            else:
                print('Network Rail feed:', exc, f'- trying again in {wait} s', flush=True)
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


def journey(t: dict, now: float):
    """1.2.0 (owner 6 Nov 09:40: "This is the Northern (time) service from (start) to (destination). Stopping at (list of
    stops, those already done in grey, those not yet in green ...) then next stop"): a passenger train's booked calls,
    each marked done / here / due, from its timetable and how far it has got. Caller holds LOCK. None if not known."""
    if (t.get('headcode') or ' ')[0] not in '129' or t.get('cancelled'):
        return None
    uid = train_uid(t)
    stops = stops_of(uid) if uid else []
    if len(stops) < 2:
        return None
    calls = [i for i in range(len(stops)) if i == 0 or i == len(stops) - 1 or stops[i][2] > stops[i][1]]
    # how far it has got: everything before `cut` is done; `here` is a call it is standing at
    leg, here = t.get('vleg'), None
    if t.get('byClock'):                                   # 1.4.0: a timetable train - where the timetable says, by the clock
        secs = now - uk_midnight(t.get('day') or uk_today())
        cut = next((i for i in range(len(stops)) if stops[i][1] > secs), len(stops))
        if 0 < cut <= len(stops) and stops[cut - 1][2] >= secs:
            here = cut - 1
    elif leg and isinstance(leg.get('idx'), int):
        cut = leg['idx']
        if now < leg.get('start', 0):                      # still waiting at the place before the next one
            cut -= 1
            here = cut
    else:
        at = index_of(stops, t.get('stanox', ''))
        if at is None:
            return None
        cut = at + 1
        if t['event'] == 'ARRIVAL' or t['terminated']:
            here = at
    if t['terminated']:
        here, cut = len(stops) - 1, len(stops) - 1
    base = uk_midnight(t.get('day') or uk_today())
    delay = t['minutes'] * 60 * (1 if t['variation'] == 'LATE' else -1 if t['variation'] == 'EARLY' else 0)

    def hhmm(secs):
        return uk_clock(base + secs)
    out = []
    for i in calls:
        spot = place(stops[i][0])
        if not spot or not spot[2]:
            continue
        booked = stops[i][2] if i == 0 else stops[i][1]
        state = 'here' if i == here else 'done' if i < cut else 'due'
        stop = {'name': spot[2][:40], 'time': hhmm(booked), 'state': state}
        if state == 'due' and abs(delay) >= 120:
            stop['expected'] = hhmm(booked + delay)
        out.append(stop)
    if len(out) < 2:
        return None
    first, last = out[0], out[-1]
    who = OPERATORS.get(t.get('toc', ''), '')
    summary = f"This is the {who + ' ' if who else ''}{first['time']} service from {first['name']} to {last['name']}."
    return {'summary': summary[:200], 'stops': out[:120]}


# ---------------------------------------------------------------- 1.4.0 timetable trains while the live feed is away
# (owner 5 Nov 22:05: "we need a fall back from this, users will think it's not so good")
ATOC_TO_TRUST = {v: k for k, v in ATOC_OF.items()} | {'SN': '88', 'TL': '88', 'GN': '88', 'SE': '80', 'SW': '84', 'ME': '64',
                                                       'SR': '60', 'AW': '71', 'CH': '74', 'CC': '79', 'GR': '61', 'VT': '65'}
TT = {'day': '', 'index': [], 'heads': {}, 'at': 0.0, 'trains': []}
TT_REFRESH = 10


def headcode_of(uid: str) -> tuple:
    """(headcode, TRUST operator code) for a booked train, or ('', '')."""
    day = BOOKED.get(uk_today()) or {}
    got = (day.get('#headOf') or {}).get(uid) or TT['heads'].get(uid) or ''
    head, _, atoc = got.partition('|')
    return head, ATOC_TO_TRUST.get(atoc, '')


def timetable_mode(now: float) -> bool:
    """Caller holds LOCK. True while the live feed should be running but has been away a while for a reason."""
    if STATE['connected'] or not HAVE_LOGIN or not (STATE['lastError'] or STATE['authRejectedUntil'] > now):
        return False
    if now - max(STATE['liveLostAt'], STARTED_AT) < TIMETABLE_AFTER:
        return False
    return bool((BOOKED.get(uk_today()) or {}).get('#stops'))


def timetable_index(today: str) -> list:
    """Once a day: (first time, last time, uid) for every booked train, read from the ends of its stops."""
    if TT['day'] == today:
        return TT['index']
    day = BOOKED.get(today) or {}
    index = []
    for uid, text in (day.get('#stops') or {}).items():
        try:
            first, last = text.split(';', 1)[0].split(','), text.rsplit(';', 1)[-1].split(',')
            index.append((int(first[2]), int(last[1]), uid))
        except (ValueError, IndexError):
            continue
    heads = {}
    if not day.get('#headOf'):            # a timetable copy made before 1.4.0: the trains whose headcode + operator are unique
        heads = {u: k for k, u in (day.get('#uidOf') or {}).items()}
    TT.update(day=today, index=index, heads=heads, at=0.0, trains=[])
    return index


def timetable_trains(now: float) -> list:
    """Caller holds LOCK. Booked passenger trains running now (by the clock) and where the timetable puts them; a train
    the live feed still has is left to the live feed. Worked out at most every TT_REFRESH seconds."""
    if now - TT['at'] < TT_REFRESH:
        return TT['trains']
    today = uk_today()
    secs = now - uk_midnight(today)
    live = {t.get('uid') for t in STATE['trains'].values() if t.get('uid') and now - t['when'] < DROP_AFTER}
    out = []
    for start, end, uid in timetable_index(today):
        if not (start <= secs <= end) or uid in live:
            continue
        head, toc = headcode_of(uid)
        if head[:1] not in ('1', '2', '9'):
            continue
        stops = stops_of(uid)
        nxt = next((i for i in range(len(stops)) if stops[i][1] > secs), None)
        if nxt is None or nxt == 0:
            continue
        prev = nxt - 1
        if stops[prev][2] >= secs:          # standing at a place, as booked
            spot = place(stops[prev][0])
            if not spot:
                continue
            out.append({'uid': uid, 'head': head, 'toc': toc, 'at': (spot[0], spot[1]), 'from': None})
            continue
        a, b = prev, nxt                    # between two places: the nearest ones we know on each side
        while a > 0 and place(stops[a][0]) is None:
            a -= 1
        while b < len(stops) - 1 and place(stops[b][0]) is None:
            b += 1
        pa, pb = place(stops[a][0]), place(stops[b][0])
        if not pa or not pb:
            continue
        t0, t1 = stops[a][2], stops[b][1]
        f = min(1.0, max(0.0, (secs - t0) / max(1, t1 - t0)))
        out.append({'uid': uid, 'head': head, 'toc': toc, 'at': (pa[0] + (pb[0] - pa[0]) * f, pa[1] + (pb[1] - pa[1]) * f),
                    'from': (pa[0], pa[1]), 'to': (pb[0], pb[1]), 'start': uk_midnight(today) + t0, 'end': uk_midnight(today) + t1,
                    'ease': easing(stops[a][2] > stops[a][1] or a == 0, stops[b][2] > stops[b][1] or b == len(stops) - 1)})
    TT.update(at=now, trains=out)
    return out


def timetable_details(entity_id: str) -> dict:
    out = {'component': COMPONENT, 'id': entity_id, 'rows': []}
    uid = entity_id[7:].replace('_', ' ')
    head, toc = headcode_of(uid)
    if not re.fullmatch(r'[A-Z0-9 ]{6}', uid) or not head:
        out['note'] = 'This train is not in today\'s timetable.'
        return out
    t = {'uid': uid, 'day': uk_today(), 'headcode': head, 'toc': toc, 'byClock': True, 'event': '', 'terminated': False,
         'minutes': 0, 'variation': '', 'cancelled': False}
    rows = out['rows']

    def add(label, value):
        if value:
            rows.append({'label': label, 'value': str(value)[:120]})
    what, drawing = booked_class(t)
    out['picture'] = drawing or kind_of(t)[0]
    with LOCK:
        trip = journey(t, time.time())
    if trip:
        out.update(trip)
    add('Train', head)
    add('Type', kind_of(t)[1])
    add('Train type', what)
    add('Operator', OPERATORS.get(toc))
    add('Status', 'Timetable, not live')
    out['note'] = 'Shown where the timetable says it should be while the live train feed takes a short break.'
    return out


def details(entity_id: str) -> dict:
    if entity_id.startswith('rail:TT'):
        return timetable_details(entity_id)
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
    with LOCK:
        trip = journey(t, time.time())
    if trip:
        out.update(trip)
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
    leg = t.get('vleg')
    if leg:
        add('Now (estimated)', f"Following its timetable towards {leg['toName']}, about {uk_clock(leg['end'])}")
    elif t.get('run') and t.get('nname') and t['event'] == 'DEPARTURE' and not t['terminated'] and not t.get('cancelled'):
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
        settle_arrivals(now)
        trains = list(STATE['trains'].items())
        connected_for = now - STATE['connectedAt'] if STATE['connected'] else 0
        scheduled = timetable_mode(now)
        booked_now = list(timetable_trains(now)) if scheduled else []
        said, retry_at = STATE['networkRailSaid'], STATE['authRejectedUntil']
        away = HAVE_LOGIN and not STATE['connected'] and bool(STATE['lastError'] or retry_at > now)
    frame_out = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': h,
                 'observedAt': iso(now), 'receivedAt': iso(now), 'expiresAt': iso(now + 90),
                 'nextPollAfterMs': 15000, 'attribution': ATTRIBUTION, 'entities': [],
                 # 0.4.3 (owner 4 Nov 11:20: unreadable headcodes where trains sit on top of each other): one badge per pile
                 'groupStacks': True}
    # 1.4.0 (owner 5 Nov 22:05): visitors get a friendly line; the real reason goes in noticeDetail (the owner's full page)
    if away:
        detail = (notice or 'Live train feed away') + (f' · Network Rail said: {said}' if said and notice else '')
        if retry_at > now:
            detail += f' · trying again at {uk_clock(retry_at)}'
        frame_out['noticeDetail'] = detail[:200]
        notice = 'Live train feed taking a short break · ' + ('showing trains from the timetable' if scheduled else 'back shortly')
    if scheduled:
        frame_out.update(health='ready', scheduled=True)
        h = 'ready'
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
        # 1.0.2 (owner 13:25, following 2D47 and zooming in: "it vanished from my screen"): zoomed right in, a train half way
        # between two places has neither end in view - so a train is sent whenever the stretch it is on crosses the view
        def crosses(a_lat, a_lon, b_lat, b_lon):
            return min(a_lat, b_lat) <= n and max(a_lat, b_lat) >= s and min(a_lon, b_lon) <= e and max(a_lon, b_lon) >= w
        heading_in = bool(t.get('run')) and crosses(t['lat'], t['lon'], t['nlat'], t['nlon'])
        leg = t.get('vleg')
        if leg:                                   # 1.0.0: following its timetable
            heading_in = crosses(leg['from'][0], leg['from'][1], leg['to'][0], leg['to'][1])
        if (s <= t['lat'] <= n and w <= t['lon'] <= e) or heading_in:
            inside.append(((t['lat'] - clat) ** 2 + (t['lon'] - clon) ** 2, tid, t, age))
    inside.sort(key=lambda x: x[0])
    on_platform = 0
    for _, tid, t, age in inside[:MAX_PER_VIEW]:
        drawn = drawn_at(t)              # 0.7.0: beside its platform when we know it
        leg = t.get('vleg')
        if leg:
            drawn = (on_track(leg['from'][1], leg['from'][0]), False)
        on_platform += drawn[1]
        entity = {'id': 'rail:' + tid, 'label': describe(t)[:160], 'badge': t['headcode'] or None,
                  'variant': kind_of(t)[0] or None,      # 0.5.0: which of TPA's train drawings to show
                  'position': dict(zip(('lon', 'lat'), drawn[0]), headingDeg=None),
                  'quality': train_quality(t, now), 'observedAt': iso(t['when'])}
        if leg and now >= leg['start']:
            key = (round(leg['from'][0], 5), round(leg['from'][1], 5), round(leg['to'][0], 5), round(leg['to'][1], 5))
            with LOCK:
                path = ROUTES.get(key)
                if path is None and STATE['trackLoaded'] and key not in PENDING_SET and len(PENDING) < 5000:
                    PENDING.append(key); PENDING_SET.add(key); ROUTE_WAKE.set()
            if path:
                entity['observedAt'] = iso(leg['start'])
                entity['motion'] = {'mode': 'path', 'points': path, 'arriveAt': iso(leg['end']),
                                    'maxSeconds': round(min(7700, leg['end'] - leg['start'] + 120))}
                if leg.get('ease'):
                    entity['motion']['ease'] = leg['ease']
        elif t.get('run') and t['event'] == 'DEPARTURE' and not t['terminated'] and not t.get('cancelled'):
            key = leg_key(t)
            with LOCK:
                path = ROUTES.get(key)
                if path is None and STATE['trackLoaded'] and key not in PENDING_SET and len(PENDING) < 5000:
                    PENDING.append(key); PENDING_SET.add(key); ROUTE_WAKE.set()
            if path:
                arrive = t.get('arriveBy') or (t['when'] + t['run'] * 60)     # 0.6.0: re-timed by the signals when known
                entity['motion'] = {'mode': 'path', 'points': path,
                                    'arriveAt': iso(arrive), 'maxSeconds': round(max(60, arrive - t['when']) + 120)}
                # 1.1.0 (owner 14:04, 1D77 into Manchester Victoria: "sped up and stopped abruptly"): picks up speed leaving
                # a station it stopped at, slows into one it stops at (booked; HQ 4.19.0 eases, older HQs ignore it)
                with LOCK:
                    ease = easing(not t.get('point') and calls_at(t, t.get('stanox', '')), calls_at(t, t.get('nstanox', '')))
                if ease:
                    entity['motion']['ease'] = ease
        frame_out['entities'].append(entity)
    # 1.4.0: timetable trains, while the live feed is away
    room = MAX_PER_VIEW - len(frame_out['entities'])
    near = []
    for b in booked_now:
        lat, lon = b['at']
        crossing = b['from'] and min(b['from'][0], b['to'][0]) <= n and max(b['from'][0], b['to'][0]) >= s \
            and min(b['from'][1], b['to'][1]) <= e and max(b['from'][1], b['to'][1]) >= w
        if (s <= lat <= n and w <= lon <= e) or crossing:
            near.append(((lat - clat) ** 2 + (lon - clon) ** 2, b))
    near.sort(key=lambda x: x[0])
    for _, b in near[:max(0, room)]:
        t = {'headcode': b['head']}
        entity = {'id': 'rail:TT' + b['uid'].replace(' ', '_'), 'label': f"Train {b['head']} · timetable, not live",
                  'badge': b['head'], 'variant': kind_of(t)[0] or None, 'quality': 'estimated', 'observedAt': iso(now)}
        if b['from'] is None:
            entity['position'] = dict(zip(('lon', 'lat'), on_track(b['at'][1], b['at'][0])), headingDeg=None)
        else:
            key = (round(b['from'][0], 5), round(b['from'][1], 5), round(b['to'][0], 5), round(b['to'][1], 5))
            with LOCK:
                path = ROUTES.get(key)
                if path is None and STATE['trackLoaded'] and key not in PENDING_SET and len(PENDING) < 5000:
                    PENDING.append(key); PENDING_SET.add(key); ROUTE_WAKE.set()
            if path:                                   # glides along the real track, as booked
                entity['position'] = dict(zip(('lon', 'lat'), on_track(b['from'][1], b['from'][0])), headingDeg=None)
                entity['observedAt'] = iso(b['start'])
                entity['motion'] = {'mode': 'path', 'points': path, 'arriveAt': iso(b['end']),
                                    'maxSeconds': round(min(7700, b['end'] - b['start'] + 120))}
                if b.get('ease'):
                    entity['motion']['ease'] = b['ease']
            else:                                      # the track route is still being worked out: where it is now
                entity['position'] = {'lon': b['at'][1], 'lat': b['at'][0], 'headingDeg': None}
        frame_out['entities'].append(entity)
    with LOCK:
        STATE['drawnOnPlatform'] = on_platform
        STATE['timetableTrainsNow'] = len(booked_now)
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
                settle_arrivals(now)
                h, notice = health(now)
                return self.reply(200, {'component': COMPONENT, 'engine': 'tpa-train-engine', 'engineVersion': ENGINE_VERSION,
                                        'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice,
                                        'loginConfigured': HAVE_LOGIN, 'connected': STATE['connected'],
                                        'stationsKnown': len(STATE['stations'] or {}), 'referenceError': STATE['referenceError'],
                                        'lastError': STATE['lastError'], 'trainsTracked': len(STATE['trains']),
                                        'loginRefusals': STATE['loginRefusals'], 'networkRailSaid': STATE['networkRailSaid'],
                                        'timetableTrainsNow': STATE.get('timetableTrainsNow', 0) if timetable_mode(time.time()) else 0,
                                        'networkRailSaidAt': iso(STATE['networkRailSaidAt']) if STATE['networkRailSaidAt'] else None,
                                        'tryingAgainAt': iso(STATE['authRejectedUntil']) if STATE['authRejectedUntil'] > time.time() else None,
                                        'timingPointsKnown': len(STATE['points']), 'movementsAtTimingPoints': STATE['atPoints'],
                                        'trainsRestoredAtStart': STATE['restored'], 'trainStartsRestored': STATE['startsRestored'], 'alwaysOn': ALWAYS_ON,
                                        'trackMapLoaded': STATE['trackLoaded'], 'trackRoutesKnown': sum(1 for v in ROUTES.values() if v),
                                        'trackRoutesWaiting': len(PENDING), 'trackError': STATE['trackError'],
                                        # 1.0.2 (owner 13:05, 1W16 sat at Stockport): how often no track route is found, with examples
                                        'trackRoutesFailed': sum(1 for v in ROUTES.values() if v is False),
                                        'trackRoutesFailedExamples': [failed_example(k) for k, v in list(ROUTES.items())[-400:] if v is False][-8:],
                                        'cancellationsSeen': STATE['cancelledSeen'], 'reinstatementsSeen': STATE['reinstatedSeen'],
                                        'movementsReceived': STATE['movements'], 'movementsAtUnmappedLocations': STATE['unplaced'],
                                        'feedKBThisRun': STATE['bytes'] // 1024,
                                        'signalling': {'on': TD_ON, 'refusedUntil': iso(STATE['tdBlockedUntil']) if STATE['tdBlockedUntil'] > time.time() else None, 'smartSteps': STATE['smartSteps'], 'smartError': STATE['smartError'],
                                                       'smartBuiltAt': iso(STATE['smartBuiltAt']) if STATE['smartBuiltAt'] else None,
                                                       'messages': STATE['tdMessages'], 'useful': STATE['tdUseful'], 'matched': STATE['tdMatched'],
                                                       'retimed': STATE['tdRetimed'], 'arrivedEarly': STATE['tdArrived'], 'platforms': STATE['tdPlatforms']},
                                        'timetable': {'days': STATE['bookedDays'], 'trainsToday': STATE['bookedTrains'], 'error': STATE['scheduleError'],
                                                      'builtAt': iso(STATE['scheduleBuiltAt']) if STATE['scheduleBuiltAt'] else None,
                                                      'activationsSeen': STATE['activations'], 'routesToday': STATE['routesToday'],
                                                      'followingTimetableNow': STATE['onTimetable'], 'placeCodesKnown': len(STATE['tiplocs']),
                                                      # 1.5.0: linked by where + when (since start), dropped again, and right now
                                                      'foundByPlace': STATE['placeMatched'], 'foundByPlaceDropped': STATE['placeDropped'],
                                                      **link_counts()},
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


def failed_example(key) -> str:
    """'Stockport -> Heaton Norris Jn' for a leg with no track route (names from the places we know)."""
    def name(lat, lon):
        for table in ((STATE['stations'] or {}), STATE['points']):
            for v in table.values():
                if abs(v[0] - lat) < 2e-5 and abs(v[1] - lon) < 2e-5:
                    return v[2]
        return f'{lat:.4f},{lon:.4f}'
    return f'{name(key[0], key[1])} -> {name(key[2], key[3])}'


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
    threading.Thread(target=onward_loop, daemon=True).start()
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
