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
import json
import math
import os
import re
import socket
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ENGINE_VERSION = '0.3.0'
COMPONENT = 'rail'
CONTRACT = '1.0'
ATTRIBUTION = 'Train movements: contains information of Network Rail Infrastructure Limited (Data Feeds Licence) · stations: DfT NaPTAN (OGL) · timing points: YA TIPLOC list (CC BY-SA 4.0 / OGL)'

FEED_HOST, FEED_PORT = 'publicdatafeeds.networkrail.co.uk', 61618
FEED_TOPIC = '/topic/TRAIN_MVT_ALL_TOC'
CORPUS_URL = 'https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS'
NAPTAN_URL = 'https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=910&dataFormat=csv'
MAX_CORPUS, MAX_NAPTAN = 22_000_000, 20_000_000
REFERENCE_MAX_AGE = 7 * 86400

FRESH_FOR = 10 * 60          # normal for 10 min after a report
DROP_AFTER = 30 * 60         # v3 MAX_AGE
TERMINATED_KEEP = 5 * 60
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
if _t_stomp and not re.fullmatch(r'127\.0\.0\.1:\d{2,5}', _t_stomp):
    raise SystemExit('TPA_RAIL_TEST_STOMP must be 127.0.0.1:port')
for _u in (_t_corpus, _t_naptan):
    if _u and not re.match(r'^http://127\.0\.0\.1:\d+/', _u):
        raise SystemExit('Test URLs must point at 127.0.0.1')
if _t_stomp:
    FEED_HOST, FEED_PORT = '127.0.0.1', int(_t_stomp.split(':')[1])
CORPUS_SRC = _t_corpus or CORPUS_URL
NAPTAN_SRC = _t_naptan or NAPTAN_URL
DATA = Path(os.environ.get('TPA_RAIL_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'TPA' / 'rail-engine'))
REFERENCE_FILE = DATA / 'station-reference.json'
MEMORY_FILE = DATA / 'trains-last-30-min.json'
SAVE_EVERY = max(1, int(os.environ.get('TPA_RAIL_SAVE_SECONDS', '60') or 60))
TIMING_POINTS_FILE = Path(os.environ.get('TPA_RAIL_TIMING_POINTS_FILE') or (Path(__file__).resolve().parent / 'data' / 'timing-points.csv'))

LOCK = threading.Lock()
STATE = {'stations': None, 'referenceError': '', 'trains': {}, 'connected': False, 'connectedAt': 0.0,
         'lastMessageAt': 0.0, 'lastError': '' if HAVE_LOGIN else 'no-login', 'authRejectedUntil': 0.0,
         'lastDemandAt': 0.0, 'messages': 0, 'movements': 0, 'unplaced': 0, 'bytes': 0,
         'points': {}, 'atPoints': 0, 'restored': 0, 'dirty': False, 'savedAt': 0.0}
WAKE = threading.Event()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def clean(text, limit: int) -> str:
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', str(text or '')).strip()[:limit]


# ---------------------------------------------------------------- station reference (v3 method)
def _download(url: str, limit: int, login: str | None = None) -> bytes:
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
    with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as res:
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
                'nlat': float, 'nlon': float, 'nname': str, 'run': int}   # 0.3.0: next place + minutes to it


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
        if kind == '0002':  # cancellation: remove
            if trains.pop(train_id, None):
                STATE['dirty'] = True
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
        terminated = str(body.get('train_terminated', '')).lower() == 'true'
        # 0.3.0: after a departure (or pass), TRUST says where the train reports next and
        # how many minutes that should take. The map glides it there and stops (estimated).
        nxt, run = None, 0
        if event == 'DEPARTURE' and not terminated:
            nstanox = clean(body.get('next_report_stanox'), 6).zfill(5)
            nxt = (STATE['stations'] or {}).get(nstanox) or STATE['points'].get(nstanox)
            try:
                run = int(str(body.get('next_report_run_time', '0')).strip() or 0)
            except ValueError:
                run = 0
            if not nxt or not 0 < run <= 120 or km(station[0], station[1], nxt[0], nxt[1]) < 0.2:
                nxt, run = None, 0
        trains[train_id] = {'lat': station[0], 'lon': station[1], 'station': station[2], 'when': when,
                            'event': event if event in ('ARRIVAL', 'DEPARTURE') else 'REPORT',
                            'variation': variation, 'minutes': minutes, 'headcode': match.group(1) if match else '',
                            'terminated': terminated, 'point': at_point,
                            'nlat': nxt[0] if nxt else 0.0, 'nlon': nxt[1] if nxt else 0.0,
                            'nname': nxt[2] if nxt else '', 'run': run}
        STATE['dirty'] = True
        if len(trains) > MAX_TRAINS:
            oldest = sorted(trains, key=lambda k: trains[k]['when'])[:len(trains) - MAX_TRAINS]
            for k in oldest:
                trains.pop(k, None)


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
                with LOCK:
                    STATE['messages'] += 1
                for item in items if isinstance(items, list) else [items]:
                    if isinstance(item, dict):
                        handle_message(item, time.time())
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
                STATE['lastError'] = str(exc)
                STATE['authRejectedUntil'] = time.time() + 1800
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


def describe(t: dict) -> str:
    what = 'passed' if t.get('point') else {'ARRIVAL': 'arrived at', 'DEPARTURE': 'departed'}.get(t['event'], 'passed')
    parts = [f"Train {t['headcode']}" if t['headcode'] else 'Train', f"{what} {t['station']}"]
    if t['terminated']:
        parts[1] = f"terminated at {t['station']}"
    if t['variation'] == 'LATE' and t['minutes']:
        parts.append(f"{t['minutes']} min late")
    elif t['variation'] == 'EARLY' and t['minutes']:
        parts.append(f"{t['minutes']} min early")
    elif t['variation'] == 'ON TIME':
        parts.append('on time')
    if t.get('run') and t.get('nname') and t['event'] == 'DEPARTURE' and not t['terminated']:
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
                 'nextPollAfterMs': 15000, 'attribution': ATTRIBUTION, 'entities': []}
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
        if age > DROP_AFTER or (t['terminated'] and age > TERMINATED_KEEP):
            continue
        if s <= t['lat'] <= n and w <= t['lon'] <= e:
            inside.append(((t['lat'] - clat) ** 2 + (t['lon'] - clon) ** 2, tid, t, age))
    inside.sort(key=lambda x: x[0])
    for _, tid, t, age in inside[:MAX_PER_VIEW]:
        entity = {'id': 'rail:' + tid, 'label': describe(t)[:160], 'badge': t['headcode'] or None,
                  'position': {'lon': t['lon'], 'lat': t['lat'], 'headingDeg': None},
                  'quality': 'observed' if age <= FRESH_FOR else 'stale', 'observedAt': iso(t['when'])}
        if t.get('run') and t['event'] == 'DEPARTURE' and not t['terminated']:
            entity['motion'] = {'mode': 'toward', 'lon': t['nlon'], 'lat': t['nlat'],
                                'arriveAt': iso(t['when'] + t['run'] * 60), 'maxSeconds': t['run'] * 60 + 120}
        frame_out['entities'].append(entity)
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
                                        'movementsReceived': STATE['movements'], 'movementsAtUnmappedLocations': STATE['unplaced'],
                                        'feedKBThisRun': STATE['bytes'] // 1024})
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_RAIL_ENGINE_PORT', '8784'))
    points = load_timing_points()
    with LOCK:
        STATE['points'] = points
    restored = restore_memory()
    threading.Thread(target=feed_loop, daemon=True).start()
    threading.Thread(target=memory_loop, daemon=True).start()
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
