"""TPA v4 AIRCRAFT ENGINE 0.1 — separate process, air lane only.

Data: ADSB.lol, a free community aircraft-tracking network (data licensed
under the Open Database License, ODbL; credit shown in TPA). No key is needed
today; ADSB.lol has said a key will be required in future (available by
feeding them data). TPA_ADSBLOL_API_KEY is read if set, ready for that day.

How it fetches (like the bus engine's "area" mode):
- asks ADSB.lol once for a circle covering the area being viewed (plus a
  margin, at most 250 nautical miles, the service maximum);
- at most every POLL_SECONDS (default 30 s since 0.7.0), and ONLY while someone is viewing;
- every viewer is served from the same shared copy;
- if ADSB.lol says "slow down" (429) or refuses, it pauses (5 min, longer if it happens again)
  and then asks more slowly until things are calm (0.7.0).

Honesty: aircraft are shown where they last reported (never invented or
animated). Reports over 30 s old are marked stale; over 90 s old they are
dropped (the owner's v3 rule). Ground traffic is labelled "on the ground".
127.0.0.1 only. Read-only. Standard library only.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sqlite3
import ssl
import tarfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ENGINE_VERSION = '0.7.0'
COMPONENT = 'air'
CONTRACT = '1.0'
ADSBLOL_POINT = 'https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}'
ATTRIBUTION = 'Aircraft data: ADSB.lol (Open Database License)'

# 0.7.0 (owner 11 Nov 10:26: ADSB.lol said "slow down" and the map had no planes all day): normal pace 30 s (was 20 s).
# Planes still glide smoothly between updates. (Below 10 s is allowed only against the test stand-in.)
_POLL_FLOOR = 1 if os.environ.get('TPA_AIR_TEST_URL', '').strip() else 10
POLL_SECONDS = max(_POLL_FLOOR, int(os.environ.get('TPA_AIR_POLL_SECONDS', '30') or 30))
DEMAND_WINDOW = max(5, int(os.environ.get('TPA_AIR_DEMAND_SECONDS', '180') or 180))
STALE_AFTER = 30
DROP_AFTER = 90              # owner's v3 rule
FRAME_TTL = 60
MAX_DOWNLOAD = 12_000_000
MAX_PER_VIEW = 400
MAX_RADIUS_NM = 250          # ADSB.lol maximum
# 0.3.0 (owner 1 Nov 14:13, #speed panel: aircraft first shown after 5.4 s, everything else within 2.4 s,
# because aircraft were only asked for once someone opened the map): with TPA_AIR_ALWAYS_ON=1 the engine
# keeps one copy of the skies over most of Britain fresh even when nobody is watching, so the first
# visitor sees aircraft at once. One request every IDLE_POLL_SECONDS (30 s) - same kind of load as one viewer.
# 0.7.0 (owner 11 Nov): no longer asks while nobody is watching; TPA_AIR_ALWAYS_ON=1 now only means "ask for the whole UK".
ALWAYS_ON = os.environ.get('TPA_AIR_ALWAYS_ON', '0').strip() == '1'
IDLE_POLL_SECONDS = max(20, int(os.environ.get('TPA_AIR_IDLE_POLL_SECONDS', '30') or 30))
HOME_VIEW = (50.0, -5.7, 55.8, 1.8)   # south, west, north, east: England, Wales and southern Scotland
MAX_VIEW_SPAN = 9.0          # degrees of latitude; wider views ask the user to zoom in
COOLDOWN_SECONDS = max(1, int(os.environ.get('TPA_AIR_COOLDOWN_SECONDS', '300') or 300))   # after 401/403/429/451, as in v3
# 0.7.0 back off gently (owner 11 Nov): each "slow down" in a row raises slowLevel (at most 3). The pause grows
# (5, 10, 15 min, or longer if ADSB.lol's Retry-After says so), and afterwards the engine asks at a slower pace:
# every 1 min at level 1, every 2 min at levels 2 and 3. After CALM_SECONDS (10 min) with no "slow down" it steps
# back down one level, until it is at the normal pace again.
SLOW_PACES = tuple(max(1, int(x)) for x in (os.environ.get('TPA_AIR_SLOW_SECONDS', '60,120') or '60,120').split(',') if x.strip().isdigit()) or (60, 120)
CALM_SECONDS = max(1, int(os.environ.get('TPA_AIR_CALM_SECONDS', '600') or 600))
MAX_SLOW_LEVEL = 3
# 0.4.0 (3 Nov load tests: ADSB.lol answered 429 "slow down" while pretend viewers watched twelve cities): on the server
# (always on) the engine asks for the whole UK in two fixed circles each round and serves every viewer from them, so the
# number of requests no longer depends on how many places are watched: 2 per round, at most 6 a minute.
NATIONAL = ((52.3, -2.2, 250), (56.8, -4.0, 250))   # England, Wales and the south / Scotland and Northern Ireland

KEY = (os.environ.get('TPA_ADSBLOL_API_KEY') or '').strip()
_test = os.environ.get('TPA_AIR_TEST_URL', '').strip()
if _test and not re.match(r'^http://127\.0\.0\.1:\d+/.*\{lat\}.*\{lon\}.*\{radius\}', _test):
    raise SystemExit('TPA_AIR_TEST_URL must point at 127.0.0.1 (testing only)')
POINT_URL = _test or ADSBLOL_POINT

# 0.5.0 (owner 4 Nov 22:51, planes sign-off: "we need more plane info when clicked"): airline, flight number, usual
# route, full aircraft name, operator, registration, year built - from Virtual Radar Server's standing data (public
# domain, CC0; https://github.com/vradarserver/standing-data), downloaded by this engine once a month and kept on our
# own server as a small database. Looked up only when a plane is clicked: no outside request per click, no new limits.
_sd = os.environ.get('TPA_AIR_STANDING_URL', '').strip()
if _sd and not _sd.startswith('http://127.0.0.1:'):
    raise SystemExit('TPA_AIR_STANDING_URL must point at 127.0.0.1 (testing only)')
STANDING_URL = _sd or 'https://codeload.github.com/vradarserver/standing-data/tar.gz/refs/heads/main'
_dd = os.environ.get('TPA_AIR_DATA_DIR', '').strip()
DATA_DIR = _dd or ('/var/lib/tpa/air' if os.path.isdir('/var/lib/tpa/air') and os.access('/var/lib/tpa/air', os.W_OK) else '')
STANDING_MAX_AGE = 30 * 86400
STANDING_MAX_DOWNLOAD = 120_000_000
STANDING = {'path': '', 'builtAt': 0.0, 'error': '', 'rows': {}}

LOCK = threading.Lock()
STATE = {'aircraft': [], 'fetchedAt': 0.0, 'circle': None, 'lastAttemptAt': 0.0, 'lastError': '',
         'lastDemandAt': 0.0, 'wanted': None, 'downloadsThisRun': 0, 'downloadBytes': 0,
         'cooldownUntil': 0.0, 'consecutiveErrors': 0, 'slowLevel': 0, 'lastSlowDownAt': 0.0}


def pace() -> int:
    """0.7.0: seconds between rounds right now (normal, or slower after a "slow down"). Call with LOCK held."""
    level = STATE['slowLevel']
    if not level:
        return POLL_SECONDS
    return max(POLL_SECONDS, SLOW_PACES[min(level, len(SLOW_PACES)) - 1])


def calm_check(now: float) -> None:
    """0.7.0: after a calm spell with no "slow down", step back towards the normal pace. Call with LOCK held."""
    if STATE['slowLevel'] and now - STATE['lastSlowDownAt'] >= CALM_SECONDS:
        STATE['slowLevel'] -= 1
        STATE['lastSlowDownAt'] = now
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
    if circle == 'national':
        return True
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
        flags = int(finite(a.get('dbFlags')) or 0)
        out.append({'hex': hexid, 'lat': round(lat, 5), 'lon': round(lon, 5), 'speed': speed,
                    'heading': round(heading % 360, 1) if heading is not None else None,
                    'flight': clean(a.get('flight'), 10), 'reg': clean(a.get('r'), 12), 'type': clean(a.get('t'), 8),
                    'ground': alt == 'ground', 'alt': int(alt) if finite(alt) is not None else None,
                    'reported': fetched - age,
                    # 0.5.0: shown only when the plane is clicked
                    'knots': round(gs) if gs is not None and 0 <= gs < 1000 else None,
                    'climb': finite(a.get('baro_rate', a.get('geom_rate'))),
                    'squawk': clean(a.get('squawk'), 4), 'emergency': clean(a.get('emergency'), 20).lower(),
                    'desc': clean(a.get('desc'), 60), 'operator': clean(a.get('ownOp'), 60), 'year': clean(a.get('year'), 4),
                    'category': clean(a.get('category'), 2).upper(),
                    # 0.6.2 (owner 22:32): ADSB.lol's database flags - 1 military, 4 privacy address (PIA), 8 owner asked trackers
                    # to hide it (LADD) - plus airspeed, the height set on the autopilot and how the position was found
                    'military': bool(flags & 1), 'private': bool(flags & 12),
                    'ias': finite(a.get('ias')), 'tas': finite(a.get('tas')),
                    'selAlt': finite(a.get('nav_altitude_mcp', a.get('nav_altitude_fms'))),
                    'source': clean(a.get('type'), 12).lower()})
    return out


def context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def fetch_national() -> None:
    """0.4.0: both UK circles, merged (aircraft seen in both kept once, freshest report)."""
    got, started, size = {}, time.time(), 0
    for circle in NATIONAL:
        part = fetch_part(circle)
        if part is None:
            return                   # failed (already recorded): keep the last good copy
        raw_len, aircraft = part
        size += raw_len
        for a in aircraft:
            if a['hex'] not in got or a['reported'] > got[a['hex']]['reported']:
                got[a['hex']] = a
    for a in got.values():
        plan_landing(a)
    with LOCK:
        # 0.5.4 (owner 4 Nov 23:44-23:45, Manchester: a plane "vanished then reappeared a couple of times on the runway",
        # "straight down the runway"): near the ground receivers lose planes now and then, and ADSB.lol leaves a plane
        # out of an answer while it has nothing fresh. A plane missing from one answer is kept where it was last
        # reported (fading as usual) until DROP_AFTER (90 s) has passed since that report, instead of vanishing.
        for a in STATE['aircraft']:
            if a['hex'] not in got and started - a['reported'] <= a.get('keep', DROP_AFTER):
                got[a['hex']] = a
        STATE.update(aircraft=list(got.values()), fetchedAt=started, circle='national', lastError='', consecutiveErrors=0,
                     downloadBytes=size)
        calm_check(time.time())
        STATE['downloadsThisRun'] += 1
    print(f'ADSB.lol OK: {len(got)} aircraft over the UK, {size//1024} KB', flush=True)


def fetch_part(circle):
    """One circle; (bytes, aircraft) or None after recording the failure."""
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
        return len(raw), parse(payload, started)
    except HTTPError as exc:
        _fail(f'adsblol-http-{exc.code}', exc.code in (401, 403, 429, 451), retry_after(exc))
    except (URLError, TimeoutError, OSError) as exc:
        _fail('network-' + type(exc).__name__, False)
    except ValueError as exc:
        _fail('bad-data-' + str(exc)[:30], False)
    return None


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
            calm_check(time.time())
            STATE['downloadsThisRun'] += 1
        print(f'ADSB.lol OK: {len(aircraft)} aircraft within {radius} nm, {len(raw)//1024} KB', flush=True)
    except HTTPError as exc:
        _fail(f'adsblol-http-{exc.code}', exc.code in (401, 403, 429, 451), retry_after(exc))
    except (URLError, TimeoutError, OSError) as exc:
        _fail('network-' + type(exc).__name__, False)
    except ValueError as exc:
        _fail('bad-data-' + str(exc)[:30], False)


def retry_after(exc) -> int:
    """0.7.0: ADSB.lol's own "try again after N seconds", if it sent one (capped at 1 hour)."""
    try:
        value = int(str((exc.headers or {}).get('Retry-After', '')).strip())
    except (TypeError, ValueError, AttributeError):
        return 0
    return max(0, min(3600, value))


def _fail(code: str, long_pause: bool, wait_asked: int = 0) -> None:
    with LOCK:
        STATE['lastError'] = code[:60]
        STATE['consecutiveErrors'] += 1
        if long_pause:
            now = time.time()
            STATE['slowLevel'] = min(MAX_SLOW_LEVEL, STATE['slowLevel'] + 1)
            STATE['lastSlowDownAt'] = now
            STATE['cooldownUntil'] = now + max(COOLDOWN_SECONDS * STATE['slowLevel'], wait_asked)
    print('ADSB.lol fetch failed:', code, flush=True)


def poller() -> None:
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        now = time.time()
        if ALWAYS_ON:                          # 0.4.0: the whole UK, at a fixed pace
            with LOCK:
                # 0.7.0 (owner 11 Nov): only while someone is looking at the map - no more asking all day and night.
                viewed = now - STATE['lastDemandAt'] < DEMAND_WINDOW
                cooling = now < STATE['cooldownUntil']
                base = pace()
                wait = base * min(6, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else base
                go = viewed and not cooling and now - STATE['lastAttemptAt'] >= wait
                if go:
                    STATE['lastAttemptAt'] = now
            if go:
                fetch_national()
            continue
        with LOCK:
            viewed = now - STATE['lastDemandAt'] < DEMAND_WINDOW
            wanted = STATE['wanted'] if (viewed and STATE['wanted'] is not None) or not ALWAYS_ON else HOME_VIEW
            if ALWAYS_ON and wanted is HOME_VIEW:
                viewed = False             # nobody has asked for an area yet: keep most of Britain fresh
            cooling = now < STATE['cooldownUntil']
            base = pace()
            wait = base * min(6, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else base
            due = now - STATE['lastAttemptAt'] >= wait
            uncovered = wanted is not None and not circle_covers(STATE['circle'], wanted) and now - STATE['lastAttemptAt'] >= 3
            if not viewed and ALWAYS_ON:
                due = now - STATE['lastAttemptAt'] >= max(wait, IDLE_POLL_SECONDS)
                uncovered = False
            go = (viewed or ALWAYS_ON) and wanted is not None and not cooling and (due or uncovered)
            circle = circle_for(wanted) if go else None
            if go:
                STATE['lastAttemptAt'] = now
        if go:
            fetch_once(circle)


def health(now: float) -> tuple[str, str]:
    if now < STATE['cooldownUntil']:
        code = STATE['lastError']
        mins = max(1, round((STATE['cooldownUntil'] - now) / 60))
        msg = (f'ADSB.lol asked us to slow down · paused {mins} min' if code == 'adsblol-http-429'
               else f'ADSB.lol refused the request · paused {mins} min')
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


WANTED = {'airlines': 'airline', 'airports': 'airport', 'routes': 'route', 'aircraft': 'aircraft'}
SCHEMA = """
    CREATE TABLE airline(icao TEXT PRIMARY KEY, name TEXT, iata TEXT);
    CREATE TABLE airport(icao TEXT PRIMARY KEY, name TEXT, iata TEXT, place TEXT, country TEXT, lat REAL, lon REAL);
    CREATE TABLE route(callsign TEXT PRIMARY KEY, number TEXT, airline TEXT, airports TEXT);
    CREATE TABLE aircraft(hex TEXT PRIMARY KEY, reg TEXT, model TEXT, operator TEXT, private INTEGER, year TEXT, type TEXT, maker TEXT);"""
STANDING_FILE = 'standing-v2.sqlite'     # 0.6.2: v2 adds the maker; the old file is used until the new one is built


def build_standing(stream, out_path: str) -> dict:
    """Reads the standing-data archive as it downloads (never all in memory) into a small SQLite file."""
    tmp = out_path + '.new'
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    counts = dict.fromkeys(WANTED.values(), 0)
    with tarfile.open(fileobj=stream, mode='r|gz') as tar:
        for m in tar:
            parts = m.name.split('/')
            if not m.isfile() or not m.name.endswith('.csv') or len(parts) < 3 or parts[1] not in WANTED or parts[2] != 'schema-01':
                continue
            kind = WANTED[parts[1]]
            for r in csv.DictReader(io.StringIO(tar.extractfile(m).read(8_000_000).decode('utf-8-sig', 'replace'))):   # one small file at a time
                def g(k, n=80):
                    return (r.get(k) or '').strip()[:n]
                if kind == 'airline' and g('ICAO'):
                    db.execute('INSERT OR REPLACE INTO airline VALUES (?,?,?)', (g('ICAO', 4).upper(), g('Name'), g('IATA', 3)))
                elif kind == 'airport' and g('ICAO'):
                    db.execute('INSERT OR REPLACE INTO airport VALUES (?,?,?,?,?,?,?)', (g('ICAO', 4).upper(), g('Name'), g('IATA', 3), g('Location'),
                               g('CountryISO2', 2), finite(r.get('Latitude')), finite(r.get('Longitude'))))
                elif kind == 'route' and g('Callsign'):
                    db.execute('INSERT OR REPLACE INTO route VALUES (?,?,?,?)',
                               (g('Callsign', 10).upper(), g('Number', 8), g('AirlineCode', 4).upper(), g('AirportCodes', 60).upper()))
                elif kind == 'aircraft' and g('ICAO'):
                    db.execute('INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?)', (g('ICAO', 6).lower(), g('Registration', 12), g('ManufacturerAndModel'),
                               g('Operator'), 1 if g('IsPrivateOperator') == '1' else 0, g('YearBuilt', 4), g('ModelICAO', 4).upper(), g('Manufacturer', 60)))
                else:
                    continue
                counts[kind] += 1
    db.commit()
    db.close()
    if counts['airline'] < 1 or counts['airport'] < 1:
        os.remove(tmp)
        raise ValueError('standing data looked empty')
    os.replace(tmp, out_path)
    return counts


def standing_keeper() -> None:
    """Keeps the standing data at most a month old; tries again a day later if a download fails."""
    path = os.path.join(DATA_DIR, STANDING_FILE)
    old = os.path.join(DATA_DIR, 'standing.sqlite')
    if not os.path.exists(path) and os.path.exists(old):
        STANDING.update(path=old, builtAt=os.path.getmtime(old))      # routes and names keep working while v2 is built
    while True:
        age = time.time() - os.path.getmtime(path) if os.path.exists(path) else None
        if age is not None:
            STANDING.update(path=path, builtAt=os.path.getmtime(path))
        if age is None or age > STANDING_MAX_AGE:
            try:
                req = Request(STANDING_URL, headers={'User-Agent': f'TPA-Air-Engine/{ENGINE_VERSION}'})
                with urlopen(req, timeout=120, context=None if STANDING_URL.startswith('http:') else context()) as r:
                    if int(r.headers.get('Content-Length') or 0) > STANDING_MAX_DOWNLOAD:
                        raise ValueError('standing data too big')
                    counts = build_standing(r, path)
                os.utime(path)
                STANDING.update(path=path, builtAt=time.time(), error='', rows=counts)
                continue
            except Exception as exc:   # aircraft keep working without it, just with fewer details
                STANDING['error'] = (type(exc).__name__ + ': ' + str(exc))[:120]
                time.sleep(86400)
                continue
        time.sleep(min(86400, max(60, STANDING_MAX_AGE - age)))


def lookup(sql: str, args: tuple):
    if not STANDING['path']:
        return None
    try:
        db = sqlite3.connect(f"file:{STANDING['path']}?mode=ro", uri=True, timeout=2)
        try:
            return db.execute(sql, args).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None


def airport_name(icao: str) -> str:
    row = lookup('SELECT name, iata, place FROM airport WHERE icao=?', (icao,))
    if not row:
        return icao
    name = re.sub(r'\s+(International\s+)?Airport$', '', row[0] or icao).strip() or icao
    # 0.6.5 (owner 11 Nov 00:42: "Sharm el-Sheikh Sharm El Sheikh"): compared without capitals, hyphens or spaces
    squash = lambda x: re.sub(r'[^a-z]', '', (x or '').lower())
    if row[2] and squash(row[2]) not in squash(name):
        name = f'{row[2]} {name}'
    return f'{name} ({row[1]})' if row[1] else name


ROUTE_SLACK_NM = 80


def off_route_nm(lat, lon, a, b) -> float:
    """How far a point is from the great-circle flight path between two airports (nautical miles)."""
    R = 3440.065
    p1, l1, p2, l2, p3, l3 = (math.radians(x) for x in (a[0], a[1], b[0], b[1], lat, lon))
    def dist(pa, la, pb, lb):
        h = math.sin((pb - pa) / 2) ** 2 + math.cos(pa) * math.cos(pb) * math.sin((lb - la) / 2) ** 2
        return 2 * math.asin(min(1.0, math.sqrt(h)))
    def bearing(pa, la, pb, lb):
        return math.atan2(math.sin(lb - la) * math.cos(pb), math.cos(pa) * math.sin(pb) - math.sin(pa) * math.cos(pb) * math.cos(lb - la))
    d13, d12 = dist(p1, l1, p3, l3), dist(p1, l1, p2, l2)
    xt = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(bearing(p1, l1, p3, l3) - bearing(p1, l1, p2, l2)))))
    at = math.acos(max(-1.0, min(1.0, math.cos(d13) / max(1e-12, math.cos(xt)))))
    if math.cos(bearing(p1, l1, p3, l3) - bearing(p1, l1, p2, l2)) < 0 or at > d12:   # beyond either end: distance to that airport
        return R * min(d13, dist(p2, l2, p3, l3))
    return R * abs(xt)


def route_plausible(lat, lon, stops) -> bool:
    spots = []
    for icao in stops:
        row = lookup('SELECT lat, lon FROM airport WHERE icao=?', (icao,))
        if not row or row[0] is None or row[1] is None:
            return False                 # cannot check it: not shown
        spots.append(row)
    return any(off_route_nm(lat, lon, spots[i], spots[i + 1]) <= ROUTE_SLACK_NM for i in range(len(spots) - 1))


COMPASS = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west']
EMERGENCY = {'7700': 'General emergency (squawking 7700)', '7600': 'Radio failure (squawking 7600)',
             '7500': 'Unlawful interference (squawking 7500)'}


def details(entity_id: str) -> dict:
    out = {'component': COMPONENT, 'id': entity_id, 'rows': []}
    hexid = entity_id[4:] if entity_id.startswith('air:') else ''
    with LOCK:
        a = next((x for x in STATE['aircraft'] if x['hex'] == hexid), None)
    if a is None:
        out['note'] = 'This aircraft is no longer being picked up.'
        return out
    rows = out['rows']

    # 0.6.0 (owner 10 Nov 21:24: "information available by click, but not all at once, so the casual map user isnt
    # overwhelmed"): rows marked 'more' are for enthusiasts, behind "More details" in the box.
    def add(label, value, more=True):
        if value:
            rows.append({'label': label, 'value': str(value)[:120], **({'more': True} if more else {})})
    sq = a.get('squawk') or ''
    if sq in EMERGENCY:
        add('Emergency', EMERGENCY[sq], more=False)
    elif a.get('emergency') and a['emergency'] != 'none':
        add('Emergency', 'Declared (' + a['emergency'] + ')', more=False)
    call = (a.get('flight') or '').upper()
    route = lookup('SELECT number, airline, airports FROM route WHERE callsign=?', (call,)) if call else None
    code = (route[1] if route and route[1] else call[:3]) if re.match(r'^[A-Z]{3}\d', call) else ''
    airline = lookup('SELECT name, iata FROM airline WHERE icao=?', (code,)) if code else None
    flight_name = call
    if airline:
        add('Airline', airline[0])
        number = route[0] if route and route[0] else call[3:]
        add('Flight', f'{airline[1]} {number} ({call})' if airline[1] and re.match(r'^\d', number) else call)
        if airline[1] and re.match(r'^\d', number):
            flight_name = airline[1] + number
    elif call:
        add('Flight', call)
    shown_route = False
    stops = []
    if route and route[2] and route[2] != 'UNKNOWN':
        stops = [x for x in route[2].split('-') if re.fullmatch(r'[A-Z0-9]{3,4}', x)][:5]
        # 0.5.3 (owner 4 Nov 23:36: TOM4MJ landed at Manchester, the box said Gatwick to Corfu): flight numbers are reused,
        # so the usual route can be out of date. It is shown only if the plane is actually on (or near) that route.
        if len(stops) >= 2 and route_plausible(a['lat'], a['lon'], stops):
            add('From', airport_name(stops[0]))
            if len(stops) > 2:
                add('Via', ', '.join(airport_name(x) for x in stops[1:-1]))
            add('To', airport_name(stops[-1]))
            shown_route = True
    known = lookup('SELECT reg, model, operator, private, year, type FROM aircraft WHERE hex=?', (a['hex'],)) or ('', '', '', 0, '', '')
    if a['type'] and known[5] and known[5] != a['type'].upper():
        known = ('', '', '', 0, '', '')            # out of date: the plane says it is something else
    # ADSB.lol's name is in capitals ("BOEING 777-300ER"): "Boeing 777-300ER", model numbers left alone
    desc = ' '.join(w.capitalize() if w.isalpha() and w.isupper() and len(w) > 2 else w for w in (a.get('desc') or '').split())
    aircraft = desc or known[1] or TYPE_NAMES.get((a['type'] or '').upper()) or a['type']
    add('Aircraft', aircraft, more=False)
    operator = a.get('operator') or ('Private' if known[3] else known[2])
    if operator and not (airline and operator.lower() == airline[0].lower()):
        add('Operator', operator)
    add('Registration', a['reg'] or known[0])
    add('Built', a.get('year') or known[4])
    if a['ground']:
        add('Height', 'On the ground')
    elif a['alt'] is not None:
        climb = a.get('climb')
        how = 'level' if climb is None or abs(climb) < 300 else (f'climbing {abs(climb):,.0f} ft/min' if climb > 0 else f'descending {abs(climb):,.0f} ft/min')
        if a['alt'] < LOW_FEET:
            how = 'landing' if climb is not None and climb <= -300 else 'taking off' if climb is not None and climb >= 300 else ''
            add('Height', f'Below {LOW_FEET:,} ft' + (f' · {how}' if how else ''))
        else:
            add('Height', f"{a['alt']:,} ft · {how}")
    if a.get('knots') is not None and (not a['ground'] or a['knots'] >= 10):
        add('Speed', f"{round(a['knots'] * 1.15078):,} mph · {a['knots']:,} knots")
    if a['heading'] is not None and not a['ground']:
        add('Heading', f"{COMPASS[int((a['heading'] + 22.5) % 360 // 45)]} ({round(a['heading']) % 360}°)")
    kind, kind_words, kind_from = plane_kind(a)
    # 0.6.2 (owner 22:32 "For everyone: Kind"): the kind is shown to everyone; where it came from is in More details
    add('Kind', kind_words, more=False)
    if kind_from:
        add('Kind from', kind_from[0].upper() + kind_from[1:])
    add('Type code', (a['type'] or known[5] or '').upper())
    maker = lookup('SELECT maker FROM aircraft WHERE hex=?', (a['hex'],))
    if maker and maker[0] and known[1]:
        add('Manufacturer', maker[0])
    speeds = [f'{round(a[k]):,} knots {k.upper()}' for k in ('ias', 'tas') if a.get(k) is not None and 0 < a[k] < 1000]
    add('Airspeed', ' · '.join(speeds))
    if a.get('selAlt') is not None and 0 < a['selAlt'] < 60000 and not a['ground']:
        add('Selected height', f"{int(round(a['selAlt'] / 100) * 100):,} ft (set on the autopilot)")
    add('Position from', SOURCES.get(a.get('source') or '', ''))
    if a.get('private'):
        add('Privacy', "The owner asked trackers to hide it: shown on this page only")
    if kind:
        out['picture'] = kind
    add('Squawk', sq)
    add('ICAO code', a['hex'].upper())
    cat = a.get('category') or ''
    add('Kind code', f'{cat} ({CATEGORY[cat]})' if cat in CATEGORY else cat)
    ago = max(0, round(time.time() - a['reported'])) if a.get('reported') else None
    if ago is not None:
        add('Last report', f'{ago} second{"" if ago == 1 else "s"} ago')
    # 0.6.0: the sentence, what it is doing, and how far along it is - for everyone
    reg = a['reg'] or known[0]
    if airline and flight_name:
        who = f'{airline[0]} flight {flight_name}'
    elif call and call.replace('-', '') != (reg or '').replace('-', ''):
        who = f'flight {call}'
    elif aircraft:
        who = f'{"an" if aircraft[:1].upper() in "AEIOU" else "a"} {aircraft}' + (f' ({reg})' if reg else '')
    else:
        who = f'aircraft {reg or a["hex"].upper()}'
    doing = doing_words(a, stops if shown_route else [])
    trip = ''
    if shown_route:
        # 0.6.6 (owner 11 Nov 01:04, EZY58JU near Bedford: "it just looked strange that it hadn't set off from liverpool but was
        # reporting it did"): the route is the one the flight number usually flies, so it is said that way - unless the plane is
        # plainly on it (just taken off from, coming in to land at, or on the ground at one of its airports).
        ends = {short_airport(stops[0]), short_airport(stops[-1])}
        sure = any(doing.startswith(p) and doing[len(p):] in ends for p in ('Just taken off from ', 'Coming in to land at ', 'On the ground at '))
        trip = (', from ' if sure else ', which usually flies from ') + short_airport(stops[0]) \
            + (f' via {", ".join(short_airport(x) for x in stops[1:-1])}' if len(stops) > 2 else '') + f' to {short_airport(stops[-1])}'
    out['summary'] = f'This is {who}{trip}.'
    if doing:
        out['doing'] = doing
    if shown_route and len(stops) == 2 and not a['ground']:
        progress = progress_words(a, stops)
        if progress:
            out['progress'] = progress
    if shown_route:
        out['note'] = 'Route: the one this flight number usually flies (Virtual Radar Server data).'
    return out


# 0.6.1 plane kinds (owner 10 Nov 21:24: "plane kinds"; 22:19 the owner makes the box pictures). Type codes are ICAO's.
WIDEBODY = {'A332', 'A333', 'A338', 'A339', 'A342', 'A343', 'A345', 'A346', 'A359', 'A35K', 'A388', 'B744', 'B748', 'B762', 'B763',
            'B764', 'B772', 'B77L', 'B77W', 'B778', 'B779', 'B788', 'B789', 'B78X', 'MD11', 'A306', 'A30B', 'A310', 'IL96', 'B74S'}
AIRLINER = {'A318', 'A319', 'A320', 'A321', 'A19N', 'A20N', 'A21N', 'B731', 'B732', 'B733', 'B734', 'B735', 'B736', 'B737', 'B738',
            'B739', 'B37M', 'B38M', 'B39M', 'B3XM', 'B752', 'B753', 'B712', 'E170', 'E175', 'E190', 'E195', 'E290', 'E295', 'BCS1',
            'BCS3', 'CRJ1', 'CRJ2', 'CRJ7', 'CRJ9', 'CRJX', 'RJ85', 'RJ1H', 'B461', 'B462', 'B463', 'F70', 'F100', 'E135', 'E145',
            'SU95', 'C919', 'MD82', 'MD83', 'MD87', 'MD88', 'MD90'}
TURBOPROP = {'AT43', 'AT45', 'AT46', 'AT72', 'AT73', 'AT75', 'AT76', 'DH8A', 'DH8B', 'DH8C', 'DH8D', 'SF34', 'SB20', 'JS31', 'JS32',
             'JS41', 'D328', 'B190', 'F50', 'SW4', 'L410', 'DHC6', 'BN2P', 'TRIS', 'C212'}
BIZJET = {'C25A', 'C25B', 'C25C', 'C25M', 'C501', 'C510', 'C525', 'C550', 'C560', 'C56X', 'C650', 'C680', 'C68A', 'C700', 'C750',
          'CL30', 'CL35', 'CL60', 'GL5T', 'GL6T', 'GL7T', 'GLEX', 'GLF4', 'GLF5', 'GLF6', 'G150', 'G280', 'E35L', 'E50P', 'E55P',
          'E545', 'E550', 'F2TH', 'F900', 'FA7X', 'FA8X', 'FA6X', 'FA50', 'LJ35', 'LJ40', 'LJ45', 'LJ60', 'LJ75', 'H25B', 'H25C',
          'PC24', 'PRM1', 'HDJT', 'SF50', 'BE40', 'ASTR', 'GALX'}
LIGHT = {'C150', 'C152', 'C162', 'C170', 'C172', 'C177', 'C182', 'C206', 'C210', 'C208', 'PA18', 'PA22', 'PA24', 'PA28', 'P28A',
         'P28B', 'P28R', 'P28T', 'PA32', 'PA34', 'PA38', 'PA44', 'PA46', 'DA20', 'DA40', 'DA42', 'DA62', 'SR20', 'SR22', 'BE33',
         'BE35', 'BE36', 'BE55', 'BE58', 'BE9L', 'BE20', 'B350', 'PC12', 'TBM7', 'TBM8', 'TBM9', 'AA5', 'RV6', 'RV7', 'RV8', 'RV9',
         'RV10', 'C42', 'EV97', 'TOBA', 'TB20', 'TAMP', 'M20P', 'M20T', 'J3', 'CUB', 'DR40', 'AP22', 'P92', 'SIRA', 'G115'}
HELICOPTER = {'EC20', 'EC30', 'EC35', 'EC45', 'EC55', 'EC75', 'EC25', 'AS50', 'AS55', 'AS65', 'AS32', 'A109', 'A119', 'A139', 'A149',
              'A169', 'A189', 'AW09', 'B06', 'B407', 'B429', 'B412', 'R22', 'R44', 'R66', 'S76', 'S92', 'H160', 'H175', 'NH90',
              'EH10', 'MI8', 'GAZL', 'LYNX', 'H64', 'H47', 'H60', 'EXPL', 'B505'}
GLIDER = {'GLID', 'DG80', 'DUOD', 'ASK2', 'ASW2', 'LS8', 'DISC', 'VENT'}
CARGO_AIRLINES = {'DHK', 'BCS', 'DAE', 'DHX', 'FDX', 'UPS', 'CLX', 'GTI', 'ABX', 'ATN', 'ABR', 'SWN', 'BOX', 'GEC', 'CKS', 'NCA',
                  'MPH', 'TAY', 'QAC', 'CAO', 'CKK', 'KZR', 'ICV', 'AHK', 'LCO', 'MSX', 'NPT', 'EXS'}
# 0.6.3 (owner 10 Nov 23:21: "Split for military please and include military helicopters. I would also like police/air
# ambulance/coastguard under a seperate filter"). Military by type code; emergency services by the callsigns the CAA gives
# them (CAP 413 / SARG policy 130): Police UKP, Helimed HLE, Coastguard SRD, British Rescue SRG.
MIL_FIGHTER = {'EUFI', 'F35', 'F35A', 'F35B', 'F35C', 'TOR', 'F16', 'F15', 'F15E', 'F18H', 'F18S', 'F22', 'HAWK', 'T45', 'RFAL', 'A10',
               'JAS39', 'GRIF', 'M2000', 'M346', 'L39', 'F5', 'F4', 'MIR2', 'AJET', 'T38', 'F2', 'SU27', 'MG29'}
MIL_TANKER = {'K35R', 'K35E', 'KC10', 'K46', 'KC46', 'A332', 'A3ST', 'MRTT', 'KC2', 'K767', 'B762'}
MIL_TRANSPORT = {'A400', 'C17', 'C130', 'C30J', 'C295', 'CN35', 'C160', 'C5M', 'C5', 'C27J', 'KC39', 'C2', 'B462', 'B752', 'D228',
                 'BE20', 'B350', 'C750', 'GLF5', 'GLEX', 'A319', 'A320', 'A321', 'B737', 'B738', 'C40', 'C32', 'A124', 'IL76', 'C12'}
MIL_SURVEILLANCE = {'E3TF', 'E3CF', 'E737', 'E7', 'P8', 'RC35', 'R135', 'U2', 'RQ4', 'Q4', 'MQ9', 'Q9', 'E8', 'P3', 'E2', 'SHAD', 'E6',
                    'GLF4', 'G550', 'MQ1', 'HRON', 'WK45', 'PROT'}
SERVICES = {'UKP': ('police', 'Police'), 'HLE': ('ambulance', 'Air ambulance'), 'SRD': ('coastguard', 'Coastguard'),
            'SRG': ('coastguard', 'Coastguard')}
KIND_WORDS = {'military': 'Military', 'mil-fighter': 'Military fast jet', 'mil-tanker': 'Military tanker',
              'mil-transport': 'Military transport', 'mil-helicopter': 'Military helicopter', 'mil-surveillance': 'Military surveillance', 'cargo': 'Cargo plane', 'helicopter': 'Helicopter', 'glider': 'Glider', 'widebody': 'Large airliner',
              'airliner': 'Airliner', 'turboprop': 'Propeller airliner', 'bizjet': 'Business jet', 'light': 'Light aircraft'}


def plane_kind(a) -> tuple:
    """(kind, words, where it came from) - or ('', 'Not known', '') when there is nothing to go on. Never a guess."""
    t, cat, call = (a.get('type') or '').upper(), a.get('category') or '', (a.get('flight') or '').upper()
    if re.match(r'^[A-Z]{3}\d', call) and call[:3] in SERVICES:
        # 0.6.4 (owner 23:53: "the police for example use planes and helicopters"): a plane gets "-plane" (its own icon)
        kind, words = SERVICES[call[:3]]
        if cat != 'A7' and t not in HELICOPTER and (t or cat):
            kind, words = kind + '-plane', words + ' plane'
        return kind, words, 'from its callsign'
    reported = 'from what it reports'
    if a.get('military'):
        if cat == 'A7' or t in HELICOPTER:
            kind = 'mil-helicopter'
        else:
            kind = next((k for k, codes in (('mil-fighter', MIL_FIGHTER), ('mil-surveillance', MIL_SURVEILLANCE),
                                            ('mil-tanker', MIL_TANKER), ('mil-transport', MIL_TRANSPORT)) if t in codes), '')
            kind = kind or ('mil-fighter' if cat == 'A6' else 'military')
        return kind, KIND_WORDS[kind], 'from the aircraft database' + (' and its type code' if t and kind != 'military' else '')
    if cat == 'A7' or t in HELICOPTER:
        return 'helicopter', KIND_WORDS['helicopter'], reported if cat == 'A7' else 'from its type code'
    if cat == 'B1' or t in GLIDER:
        return 'glider', KIND_WORDS['glider'], reported if cat == 'B1' else 'from its type code'
    if cat == 'B2':
        return 'balloon', 'Balloon', reported          # 0.6.4: the owner's balloon icon
    if cat in ('B6', 'B7'):
        return 'other', {'B6': 'Drone', 'B7': 'Space vehicle'}[cat], reported
    if cat == 'B4':
        return 'light', 'Microlight', reported
    if re.match(r'^[A-Z]{3}\d', call) and call[:3] in CARGO_AIRLINES:
        return 'cargo', KIND_WORDS['cargo'], 'cargo airline'
    for kind, codes in (('widebody', WIDEBODY), ('airliner', AIRLINER), ('turboprop', TURBOPROP), ('bizjet', BIZJET), ('light', LIGHT)):
        if t in codes:
            return kind, KIND_WORDS[kind], 'from its type code'
    by_cat = {'A1': 'light', 'A3': 'airliner', 'A4': 'airliner', 'A5': 'widebody'}.get(cat)
    if by_cat:
        return by_cat, KIND_WORDS[by_cat], reported
    return '', 'Not known', ''


# 0.6.5 (owner 11 Nov 00:42: "Aircraft A20N"): names for the commonest type codes, when the plane sends none
TYPE_NAMES = {'A318': 'Airbus A318', 'A319': 'Airbus A319', 'A320': 'Airbus A320', 'A321': 'Airbus A321', 'A19N': 'Airbus A319neo',
              'A20N': 'Airbus A320neo', 'A21N': 'Airbus A321neo', 'BCS1': 'Airbus A220-100', 'BCS3': 'Airbus A220-300',
              'A332': 'Airbus A330-200', 'A333': 'Airbus A330-300', 'A339': 'Airbus A330-900neo', 'A359': 'Airbus A350-900',
              'A35K': 'Airbus A350-1000', 'A388': 'Airbus A380', 'B737': 'Boeing 737-700', 'B738': 'Boeing 737-800',
              'B739': 'Boeing 737-900', 'B38M': 'Boeing 737 MAX 8', 'B39M': 'Boeing 737 MAX 9', 'B752': 'Boeing 757-200',
              'B763': 'Boeing 767-300', 'B772': 'Boeing 777-200', 'B77W': 'Boeing 777-300ER', 'B788': 'Boeing 787-8',
              'B789': 'Boeing 787-9', 'B78X': 'Boeing 787-10', 'B744': 'Boeing 747-400', 'B748': 'Boeing 747-8',
              'E190': 'Embraer E190', 'E195': 'Embraer E195', 'E75L': 'Embraer E175', 'E290': 'Embraer E190-E2',
              'E295': 'Embraer E195-E2', 'DH8D': 'De Havilland Dash 8-400', 'AT76': 'ATR 72-600', 'AT75': 'ATR 72-500',
              'AT45': 'ATR 42-500', 'SF34': 'Saab 340', 'CRJ9': 'Bombardier CRJ900', 'C172': 'Cessna 172', 'PA28': 'Piper PA-28',
              'EC35': 'Airbus H135', 'EC45': 'Airbus H145', 'A139': 'Leonardo AW139', 'A169': 'Leonardo AW169',
              'A189': 'Leonardo AW189', 'S92': 'Sikorsky S-92', 'EUFI': 'Eurofighter Typhoon', 'A400': 'Airbus A400M Atlas',
              'C17': 'Boeing C-17 Globemaster III', 'C30J': 'Lockheed C-130J Hercules', 'H47': 'Boeing CH-47 Chinook',
              'P8': 'Boeing P-8 Poseidon', 'E3TF': 'Boeing E-3 Sentry', 'K35R': 'Boeing KC-135 Stratotanker'}


# ADS-B "emitter category" (what kind of aircraft the plane says it is)
CATEGORY = {'A1': 'light aircraft', 'A2': 'small aircraft', 'A3': 'large aircraft', 'A4': 'large aircraft, strong wake',
            'A5': 'heavy aircraft', 'A6': 'high performance', 'A7': 'helicopter', 'B1': 'glider', 'B2': 'balloon',
            'B3': 'parachutists', 'B4': 'microlight', 'B6': 'drone', 'B7': 'space vehicle', 'C1': 'emergency vehicle',
            'C2': 'airport vehicle', 'C3': 'obstacle'}
NEAR_AIRPORT_NM = 12
SOURCES = {'adsb_icao': "The plane's own GPS (ADS-B)", 'adsb_icao_nt': "The plane's own GPS (ADS-B)", 'adsb_other': "The plane's own GPS (ADS-B)",
           'mlat': 'Worked out by ground stations (MLAT)', 'tisb_icao': 'Passed on by air traffic radar (TIS-B)', 'tisb_other': 'Passed on by air traffic radar (TIS-B)',
           'tisb_trackfile': 'Passed on by air traffic radar (TIS-B)', 'adsr_icao': 'Relayed (ADS-R)', 'adsr_other': 'Relayed (ADS-R)',
           'adsc': 'By satellite (ADS-C)', 'mode_s': 'No position from the plane itself', 'other': ''}


def short_airport(icao: str) -> str:
    return re.sub(r' \([A-Z0-9]{3}\)$', '', airport_name(icao))


def airport_spot(icao: str):
    row = lookup('SELECT lat, lon FROM airport WHERE icao=?', (icao,))
    return row if row and row[0] is not None and row[1] is not None else None


def nearest_airport(lat, lon, within_nm, prefer=()):
    """(icao) of a passenger airport within reach: one of `prefer` (the flight's own airports) first, else the nearest."""
    for icao in prefer:
        spot = airport_spot(icao)
        if spot and nm_between(lat, lon, spot[0], spot[1]) <= within_nm * 2:
            return icao
    k = math.cos(math.radians(lat)) ** 2
    row = lookup("SELECT icao, lat, lon FROM airport WHERE iata != '' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                 "ORDER BY (lat - ?) * (lat - ?) + (lon - ?) * (lon - ?) * ? LIMIT 1",
                 (lat - 0.5, lat + 0.5, lon - 0.8, lon + 0.8, lat, lat, lon, lon, k))
    if row and row[1] is not None and nm_between(lat, lon, row[1], row[2]) <= within_nm:
        return row[0]
    return None


def doing_words(a, stops) -> str:
    """0.6.0: what the plane is doing, in words: cruising, climbing, coming in to land at ..., on the ground at ..."""
    alt, climb = a['alt'], a.get('climb')
    if a['ground']:
        here = nearest_airport(a['lat'], a['lon'], 3)
        return f'On the ground at {short_airport(here)}' if here else 'On the ground'
    if alt is None:
        return ''
    up, down = climb is not None and climb >= 300, climb is not None and climb <= -300
    if alt < APPROACH_FEET and (up or down):
        here = nearest_airport(a['lat'], a['lon'], NEAR_AIRPORT_NM, (stops[:1] if up else stops[-1:]))
        if here:
            return f'Just taken off from {short_airport(here)}' if up else f'Coming in to land at {short_airport(here)}'
    if alt < LOW_FEET:
        return f'Flying low, below {LOW_FEET:,} ft'
    if up:
        return f'Climbing through {alt:,} ft'
    if down:
        return f'Descending through {alt:,} ft'
    return f'Cruising at {alt:,} ft' if alt >= 20000 else f'Flying level at {alt:,} ft'


def _miles(nm: float) -> str:
    m = nm * 1.15078
    if m < 5:
        return 'under 5'
    return f'{int(round(m / (10 if m >= 100 else 5)) * (10 if m >= 100 else 5)):,}'


def progress_words(a, stops) -> str:
    """0.6.0: "About halfway: 210 miles flown, 190 to go" (straight-line miles from and to the two airports)."""
    start, end = airport_spot(stops[0]), airport_spot(stops[-1])
    if not start or not end:
        return ''
    flown, left = nm_between(start[0], start[1], a['lat'], a['lon']), nm_between(a['lat'], a['lon'], end[0], end[1])
    if flown + left <= 0:
        return ''
    f = flown / (flown + left)
    for limit, words in ((0.1, 'Just set off'), (0.3, 'About a quarter of the way'), (0.43, 'About a third of the way'),
                         (0.57, 'About halfway'), (0.7, 'About two-thirds of the way'), (0.9, 'About three-quarters of the way'),
                         (2, 'Nearly there')):
        if f < limit:
            break
    return f'{words}: {_miles(flown)} miles flown, {_miles(left)} to go'


LOW_FEET = 1000
APPROACH_FEET = 4000


def near_ground(a) -> bool:
    return a['ground'] or (a['alt'] is not None and a['alt'] < LOW_FEET)


def airports_near_home() -> list:
    """Airports with passenger codes in and around the British Isles (from the standing data), kept in memory."""
    if STANDING.get('nearFor') != STANDING['path']:
        rows = []
        if STANDING['path']:
            try:
                db = sqlite3.connect(f"file:{STANDING['path']}?mode=ro", uri=True, timeout=2)
                rows = db.execute("SELECT lat, lon FROM airport WHERE iata != '' AND lat BETWEEN 48 AND 62 AND lon BETWEEN -12 AND 4").fetchall()
                db.close()
            except sqlite3.Error:
                rows = []
        STANDING.update(near=[r for r in rows if r[0] is not None and r[1] is not None], nearFor=STANDING['path'])
    return STANDING.get('near') or []


def plan_landing(a) -> None:
    """0.5.5 (owner 4 Nov 23:51, EZY29QN at Manchester: "seemed to land but not slow down, then vanished for a couple of
    seconds and returned" - its last report was low on the approach, then nothing for two minutes). A plane low down,
    coming down and heading for an airport is carried on at its speed to the airport and eased to a stop just past
    the middle of the airfield, as a landing plane does, instead of flying on through it. It is kept there (fading)
    until it is heard again, for up to 5 minutes."""
    if 'landing' in a:
        return
    a['landing'] = None
    # 0.5.6 (owner 23:59, DHK457 into East Midlands: last heard at "2,200 ft" about 3 miles out, then "stayed at 160mph until
    # the end of the runway"): the height planes send is set for standard air pressure, so on a low-pressure night a plane
    # on its final approach can read well over 1,000 ft. Any plane under APPROACH_FEET coming down towards an airport counts.
    climb = a.get('climb')
    # 0.5.7 (owner 5 Nov 00:05, RYR2CF at Liverpool: "exact to the runway, ran to end at 131mph then appeared again mid
    # runway"): last heard just as it touched down - levelling off (not "coming down" any more), and already over the
    # airfield. Very low and not climbing counts as landing too, and an airfield just behind still counts.
    coming_down = climb is not None and climb <= -300
    touching = a['alt'] is not None and a['alt'] < LOW_FEET and (climb is None or climb <= 0)
    if a['ground'] or a['alt'] is None or a['alt'] >= APPROACH_FEET or not (coming_down or touching) \
            or a['heading'] is None or not a.get('speed') or a['speed'] < 30:
        return
    h = math.radians(a['heading'])
    best = None
    for lat, lon in airports_near_home():
        if abs(lat - a['lat']) > 0.25 or abs(lon - a['lon']) > 0.4:
            continue
        north = (lat - a['lat']) * 111195
        east = (lon - a['lon']) * 111195 * math.cos(math.radians(a['lat']))
        along = east * math.sin(h) + north * math.cos(h)
        across = abs(east * math.cos(h) - north * math.sin(h))
        reach = 22000 if coming_down else 3000     # levelling off: only right over the airfield
        if -2500 < along < reach and across < max(1500, along * 0.25) and (best is None or abs(along) < abs(best)):
            best = along
    if best is None:
        return
    # the roll ends a little past the middle of the airfield - or, already past it, after braking (about 1.5 m/s each second)
    length = max(best + 600, a['speed'] ** 2 / 3.0)
    R = 6371000
    end = [round(a['lon'] + length * math.sin(h) / (R * math.cos(math.radians(a['lat']))) * 180 / math.pi, 6),
           round(a['lat'] + length * math.cos(h) / R * 180 / math.pi, 6)]
    hold = round(6 * length / (5 * a['speed']), 1)     # steady for two-thirds of it, then easing to a stop at the end
    a['landing'] = {'mode': 'path', 'points': [[a['lon'], a['lat']], end], 'arriveAt': iso(a['reported'] + hold),
                    'maxSeconds': hold, 'speedMps': a['speed'], 'slowToStop': True}
    a['keep'] = min(300, max(DROP_AFTER, hold + 60))


def label_for(a) -> str:
    # 0.5.8 (owner 5 Nov 00:32-00:38: the title said 34,550 ft while the box below said 26,550 ft - "do we need the
    # altitude in the title?"): the title is just who the plane is. Height, speed and the full aircraft name are in the
    # box, which stays up to date while it is open.
    name = a['flight'] or a['reg'] or a['hex'].upper()
    parts = [name]
    if a['reg'] and a['reg'] != name:
        parts.append(a['reg'])
    return ' · '.join(parts)


def snapshot(view) -> dict:
    now = time.time()
    small = view is not None and view[2] - view[0] <= MAX_VIEW_SPAN
    if small:                       # 0.7.0: a zoomed-out map shows no planes, so it does not count as watching
        note_demand(view)
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
        if age > a.get('keep', DROP_AFTER) or not (s <= a['lat'] <= n and w <= a['lon'] <= e):
            continue
        inside.append(((a['lat'] - clat) ** 2 + (a['lon'] - clon) ** 2, a, age))
    inside.sort(key=lambda t: t[0])
    for _, a, age in inside[:MAX_PER_VIEW]:
        entity = {'id': 'air:' + a['hex'], 'label': label_for(a)[:160],
                  'position': {'lon': a['lon'], 'lat': a['lat'], 'headingDeg': a['heading']},
                  'quality': 'stale' if age > STALE_AFTER else 'observed', 'observedAt': iso(a['reported'])}
        kind = plane_kind(a)[0]
        if kind:
            entity['variant'] = kind          # 0.6.1: its own map icon
        if a.get('private'):
            entity['private'] = True          # 0.6.2 (owner 22:32): not drawn on the public page; the owner's /full shows it
        # 0.2.0: speed + track let the map carry the aircraft forward smoothly between
        # reports (shown as "estimated"), for at most a minute after the real report.
        if a['heading'] is not None and a.get('speed') and a['speed'] >= 5:
            if a.get('landing'):
                entity['motion'] = a['landing']
            elif a['ground']:
                # 0.5.1 (owner 4 Nov 23:21, Newcastle: after landing it "reversed a bit up the runway"): a plane landing
                # brakes hard, so carrying it on at its last speed put it too far down the runway, then pulled it back.
                # On the runway (25 m/s or more): eased to a stop about where a braking plane stops. Taxiing: steady, easing
                # off only near the end. A plane that is really further on is caught up forwards; one behind is waited for.
                # 0.5.2 (owner 23:28: "it keeps stopping for a couple of seconds under 1000ft"): planes still in the air
                # are carried on at their speed as before - only the ground roll is eased.
                hold = max(6.0, min(25.0, round(0.3 * a['speed'], 1))) if a['speed'] >= 25 else 25.0
                entity['motion'] = {'mode': 'heading', 'speedMps': a['speed'], 'maxSeconds': hold, 'slowToStop': True}
            else:
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
        if split.path == '/status':        # 0.7.0: a health check is not a visitor watching planes
            now = time.time()
            with LOCK:
                h, notice = health(now)
                return self.reply(200, {'component': COMPONENT, 'engine': 'tpa-air-engine', 'engineVersion': ENGINE_VERSION,
                                        'contractVersion': CONTRACT, 'live': True, 'health': h, 'notice': notice,
                                        'keyConfigured': bool(KEY), 'lastError': STATE['lastError'],
                                        'lastSuccessAt': iso(STATE['fetchedAt']) if STATE['fetchedAt'] else None,
                                        'aircraftInLastDownload': len(STATE['aircraft']), 'lastDownloadKB': STATE['downloadBytes'] // 1024,
                                        'adsblolRequestsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS, 'alwaysOn': ALWAYS_ON,
                                        'slowLevel': STATE['slowLevel'], 'pollingEverySeconds': pace(),
                                        'standingData': {'ready': bool(STANDING['path']), 'builtAt': iso(STANDING['builtAt']) if STANDING['builtAt'] else None,
                                                         'error': STANDING['error'], 'rows': STANDING['rows']}})
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        if split.path == '/details':
            return self.reply(200, details((parse_qs(split.query).get('id') or [''])[0][:140]))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_AIR_ENGINE_PORT', '8783'))
    threading.Thread(target=poller, daemon=True).start()
    if DATA_DIR:
        threading.Thread(target=standing_keeper, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA AIRCRAFT ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print('Source: ADSB.lol (free, no key needed today).', flush=True)
        print(f'Asks ADSB.lol at most every {POLL_SECONDS}s, only while someone is looking at the map'
              + (' (the whole UK in two circles).' if ALWAYS_ON else '.') + ' Slows down further if ADSB.lol asks.', flush=True)
        print('Keep this window open. Close it to stop aircraft only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
