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

ENGINE_VERSION = '0.5.1'
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
# 0.3.0 (owner 1 Nov 14:13, #speed panel: aircraft first shown after 5.4 s, everything else within 2.4 s,
# because aircraft were only asked for once someone opened the map): with TPA_AIR_ALWAYS_ON=1 the engine
# keeps one copy of the skies over most of Britain fresh even when nobody is watching, so the first
# visitor sees aircraft at once. One request every IDLE_POLL_SECONDS (30 s) - same kind of load as one viewer.
ALWAYS_ON = os.environ.get('TPA_AIR_ALWAYS_ON', '0').strip() == '1'
IDLE_POLL_SECONDS = max(20, int(os.environ.get('TPA_AIR_IDLE_POLL_SECONDS', '30') or 30))
HOME_VIEW = (50.0, -5.7, 55.8, 1.8)   # south, west, north, east: England, Wales and southern Scotland
MAX_VIEW_SPAN = 9.0          # degrees of latitude; wider views ask the user to zoom in
COOLDOWN_SECONDS = 300       # after 401/403/429/451, as in v3
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
        out.append({'hex': hexid, 'lat': round(lat, 5), 'lon': round(lon, 5), 'speed': speed,
                    'heading': round(heading % 360, 1) if heading is not None else None,
                    'flight': clean(a.get('flight'), 10), 'reg': clean(a.get('r'), 12), 'type': clean(a.get('t'), 8),
                    'ground': alt == 'ground', 'alt': int(alt) if finite(alt) is not None else None,
                    'reported': fetched - age,
                    # 0.5.0: shown only when the plane is clicked
                    'knots': round(gs) if gs is not None and 0 <= gs < 1000 else None,
                    'climb': finite(a.get('baro_rate', a.get('geom_rate'))),
                    'squawk': clean(a.get('squawk'), 4), 'emergency': clean(a.get('emergency'), 20).lower(),
                    'desc': clean(a.get('desc'), 60), 'operator': clean(a.get('ownOp'), 60), 'year': clean(a.get('year'), 4)})
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
    with LOCK:
        STATE.update(aircraft=list(got.values()), fetchedAt=started, circle='national', lastError='', consecutiveErrors=0,
                     downloadBytes=size)
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
        _fail(f'adsblol-http-{exc.code}', exc.code in (401, 403, 429, 451))
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
        if ALWAYS_ON:                          # 0.4.0: the whole UK, at a fixed pace
            with LOCK:
                viewed = now - STATE['lastDemandAt'] < DEMAND_WINDOW
                cooling = now < STATE['cooldownUntil']
                wait = POLL_SECONDS * min(6, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else POLL_SECONDS
                go = not cooling and now - STATE['lastAttemptAt'] >= (wait if viewed else max(wait, IDLE_POLL_SECONDS))
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
            wait = POLL_SECONDS * min(6, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else POLL_SECONDS
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


WANTED = {'airlines': 'airline', 'airports': 'airport', 'routes': 'route', 'aircraft': 'aircraft'}
SCHEMA = """
    CREATE TABLE airline(icao TEXT PRIMARY KEY, name TEXT, iata TEXT);
    CREATE TABLE airport(icao TEXT PRIMARY KEY, name TEXT, iata TEXT, place TEXT, country TEXT, lat REAL, lon REAL);
    CREATE TABLE route(callsign TEXT PRIMARY KEY, number TEXT, airline TEXT, airports TEXT);
    CREATE TABLE aircraft(hex TEXT PRIMARY KEY, reg TEXT, model TEXT, operator TEXT, private INTEGER, year TEXT, type TEXT);"""


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
                    db.execute('INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?)', (g('ICAO', 6).lower(), g('Registration', 12), g('ManufacturerAndModel'),
                               g('Operator'), 1 if g('IsPrivateOperator') == '1' else 0, g('YearBuilt', 4), g('ModelICAO', 4).upper()))
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
    path = os.path.join(DATA_DIR, 'standing.sqlite')
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
    if row[2] and row[2].lower() not in name.lower():
        name = f'{row[2]} {name}'
    return f'{name} ({row[1]})' if row[1] else name


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

    def add(label, value):
        if value:
            rows.append({'label': label, 'value': str(value)[:120]})
    sq = a.get('squawk') or ''
    if sq in EMERGENCY:
        add('Emergency', EMERGENCY[sq])
    elif a.get('emergency') and a['emergency'] != 'none':
        add('Emergency', 'Declared (' + a['emergency'] + ')')
    call = (a.get('flight') or '').upper()
    route = lookup('SELECT number, airline, airports FROM route WHERE callsign=?', (call,)) if call else None
    code = (route[1] if route and route[1] else call[:3]) if re.match(r'^[A-Z]{3}\d', call) else ''
    airline = lookup('SELECT name, iata FROM airline WHERE icao=?', (code,)) if code else None
    if airline:
        add('Airline', airline[0])
        number = route[0] if route and route[0] else call[3:]
        add('Flight', f'{airline[1]} {number} ({call})' if airline[1] and re.match(r'^\d', number) else call)
    elif call:
        add('Flight', call)
    shown_route = False
    if route and route[2] and route[2] != 'UNKNOWN':
        stops = [x for x in route[2].split('-') if re.fullmatch(r'[A-Z0-9]{3,4}', x)][:5]
        if len(stops) >= 2:
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
    add('Aircraft', desc or known[1] or a['type'])
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
        add('Speed', f"{round(a['knots'] * 1.15078):,} mph")
    if a['heading'] is not None and not a['ground']:
        add('Heading', f"{COMPASS[int((a['heading'] + 22.5) % 360 // 45)]} ({round(a['heading']) % 360}°)")
    if shown_route:
        out['note'] = 'Route: the one this flight number usually flies (Virtual Radar Server data).'
    return out


LOW_FEET = 1000


def near_ground(a) -> bool:
    return a['ground'] or (a['alt'] is not None and a['alt'] < LOW_FEET)


def label_for(a) -> str:
    name = a['flight'] or a['reg'] or a['hex'].upper()
    parts = [name]
    if a['type']:
        parts.append(a['type'])
    if a['ground']:
        parts.append('on the ground')
    elif a['alt'] is not None:
        # 0.5.1 (owner 4 Nov 23:21 "it landed at -350ft :D"): planes send their height set for standard air pressure,
        # which near the ground can be hundreds of feet out (even below zero), so low down it is not given as a number
        parts.append(f"{a['alt']:,} ft" if a['alt'] >= LOW_FEET else f'below {LOW_FEET:,} ft')
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
        if a['heading'] is not None and a.get('speed') and a['speed'] >= 5:
            if near_ground(a):
                # 0.5.1 (owner 4 Nov 23:21, Newcastle: after landing it "reversed a bit up the runway"): a plane landing
                # brakes hard, so carrying it on at its last speed put it too far down the runway, then pulled it back.
                # Near the ground: ease to a stop over 12 s - it falls a little behind and catches up forwards instead.
                entity['motion'] = {'mode': 'heading', 'speedMps': a['speed'], 'maxSeconds': 12, 'slowToStop': True}
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
                                        'adsblolRequestsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS, 'alwaysOn': ALWAYS_ON,
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
        print(f'Asks ADSB.lol at most every {POLL_SECONDS}s while the map is open'
              + (f', and every {IDLE_POLL_SECONDS}s for most of Britain when it is not.' if ALWAYS_ON else ', only then.'), flush=True)
        print('Keep this window open. Close it to stop aircraft only.', flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
