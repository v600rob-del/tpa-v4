"""TPA bus stop boards (bus engine 0.13.0).

Owner, 12 Nov 2026 17:03 ("live departure boards ... will make this THE go to site"), rail first, then bus stops.
His choices for bus stop boards (12 Nov 18:40):
- each line: time, route number, where it is going, the bus company, a live "due in X min" (with how late or early)
  for buses TPA can see, and timetable-only buses too, clearly marked as timetable;
- tap a departure: its next stops and times, and "show it on the map" (live buses);
- at the top: the stop's letter and direction, the routes that call there, and an Arrivals tab (buses finishing there);
- opened by tapping the stop on the map (no search by stop name). Bus stations are a separate job, later.

BODS sends no predictions for stops, so TPA works them out itself: the timetable says which journeys call at a stop
and when; a live bus matched to its journey (the engine already does this) says how late that journey is running,
from where its newest report puts it along its stops.

The timetable (timetable.sqlite) keeps each journey's stops packed together, which is right for following a bus but
cannot answer "what calls at this stop?". So a second file, stops.sqlite, is built from it by this program at low
priority, after each new timetable (weekly, at night):

    python3 stop_boards.py build <data dir>

    calls  (stop, secs, trip, flags)   every call at every stop: stop = the timetable's stops rowid, secs = time after
                                       the service day's midnight (GTFS: may pass 24:00), trip = trips rowid,
                                       flags 1 = journey starts here, 2 = journey ends here
    stops  (stop, atco, cell, lat, lon, name, indicator, bearing, routes)   only stops something calls at

Nothing here downloads anything. Standard library only.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import zlib
from array import array
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo('Europe/London')
except Exception:            # no time zone data: UTC (an hour out in summer, never a crash)
    LONDON = timezone.utc

FORMAT = 5                   # 2: bus stations (0.14.0); 3: bays marked as street stops count too (0.14.1); 4: stand codes in names, close stations merged (0.14.3); 5: stand labels off names (0.14.4)
CELL = 50                    # cells of 1/50 degree (~2 km north-south) for "stops in view"
BATCH = 50_000
VIEW_SPAN = 0.06             # stops are shown only when the view is under ~6.5 km tall (street level)
MAX_STOPS = 400
AHEAD = 2 * 3600             # departures up to 2 hours ahead
LOOKBACK = 20 * 60           # ...and up to 20 minutes back (a late bus is still to come)
ROWS = 20
STATION_ROWS = 50            # a bus station's board: more buses (the route filter picks from them)
STATION_SPAN = 1.2           # bus stations show from about county level, like railway stations
SCREEN_PX = 800              # 0.14.2: a typical map height in screen pixels (PC ~900, phone ~840)
STAND_GAP_PX = 34            # 0.14.2: a station's stands are shown once they would sit a whole stop pin apart on screen (owner 22:32: Hull's still piled up at max zoom)
HUB_REACH_M = 400            # a stand belongs to the bus station point (NaPTAN 'BST') this close
CLUSTER_M = 250              # stands with the same name and place this close make one station when there is no BST
CALLS_SHOWN = 60
LIVE_FRESH = 300             # a bus report older than this is not used for "due in"
NEAR_ROUTE_M = 600           # a live bus further than this from its journey's stops is not placed on it
SAME_PLACE_M = 60            # stretches this much further away than the nearest still count (a route passing twice)
LATE_STATE_S = 120           # "late" (amber) from 2 minutes late
EARLY_LIMIT_S = 10 * 60
COMPASS = {'N': 'north', 'NE': 'north-east', 'E': 'east', 'SE': 'south-east', 'S': 'south', 'SW': 'south-west',
           'W': 'west', 'NW': 'north-west'}


def cell_of(lat, lon):
    return int(math.floor(lat * CELL)) * 100_000 + int(math.floor((lon + 20) * CELL))


# ---------------------------------------------------------------- building (a low-priority child of the bus engine)
def build(data_dir, status=lambda **kw: None, naptan_csv=None):
    """Build stops.sqlite from timetable.sqlite. Returns counts. Nothing half-built is ever left at stops.sqlite.
    0.14.0: with the NaPTAN stop list (naptan_csv), bus station stands are grouped into bus stations."""
    src = os.path.join(data_dir, 'timetable.sqlite')
    out = os.path.join(data_dir, 'stops.sqlite')
    tmp, raw_path = out + '.building', out + '.raw'
    for p in (tmp, raw_path):
        if os.path.exists(p):
            os.remove(p)
    tt = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    built_at = json.loads((tt.execute("SELECT value FROM meta WHERE key='builtAt'").fetchone() or ['0'])[0])
    stop_n = {sid: n for n, sid in tt.execute('SELECT rowid, stop_id FROM stops')}
    raw = sqlite3.connect(raw_path)
    for q in ('PRAGMA journal_mode=OFF', 'PRAGMA synchronous=OFF', 'PRAGMA temp_store=FILE', 'PRAGMA cache_size=-16000',
              'CREATE TABLE raw (stop INTEGER, secs INTEGER, trip INTEGER, flags INTEGER)'):
        raw.execute(q)
    route_names, route_ix = [''], {'': 0}
    trip_route = array('i')                            # trips rowid -> its route name (index into route_names)
    batch, n_calls, n_trips, last = [], 0, 0, 0.0

    def flush_trip(trip_n, route, rows):
        nonlocal n_calls, n_trips
        if not rows:
            return
        rows.sort()
        n_trips += 1
        ix = route_ix.get(route)
        if ix is None:
            ix = route_ix[route] = len(route_names)
            route_names.append(route)
        if trip_n >= len(trip_route):
            trip_route.extend([0] * (trip_n + 1 - len(trip_route) + 100_000))
        trip_route[trip_n] = ix
        end = len(rows) - 1
        for i, (seq, sid, arr, dep) in enumerate(rows):
            stop = stop_n.get(sid)
            if stop is None:
                continue
            secs = arr if i == end else dep
            if secs < 0:
                secs = dep if secs == arr else arr
            if secs < 0:
                continue
            batch.append((stop, secs, trip_n, (1 if i == 0 else 0) | (2 if i == end else 0)))
        n_calls += len(rows)

    cur_trip, cur_route, rows = None, '', []
    for trip_n, route, data in tt.execute(
            'SELECT t.rowid, r.short_name, ts.data FROM trip_stops ts JOIN trips t ON t.trip_id = ts.trip_id '
            'LEFT JOIN routes r ON r.route_id = t.route_id ORDER BY ts.trip_id, ts.part'):
        if trip_n != cur_trip:
            flush_trip(cur_trip, cur_route, rows)
            cur_trip, cur_route, rows = trip_n, (route or '').strip(), []
        for line in zlib.decompress(data).decode().split('\n'):
            seq, sid, arr, dep = line.split(',')
            rows.append((int(seq), sid, int(arr), int(dep)))
        if len(batch) >= BATCH:
            raw.executemany('INSERT INTO raw VALUES (?,?,?,?)', batch); batch.clear()
            if time.time() - last >= 5:
                last = time.time()
                status(state='building', step='calls', rows=n_calls)
    flush_trip(cur_trip, cur_route, rows)
    if batch:
        raw.executemany('INSERT INTO raw VALUES (?,?,?,?)', batch); batch.clear()
    raw.commit(); raw.close()
    del stop_n
    if not n_calls:
        os.remove(raw_path)
        raise ValueError('the timetable has no stop calls')
    status(state='building', step='sorting', rows=n_calls)
    db = sqlite3.connect(tmp)
    for q in ('PRAGMA journal_mode=OFF', 'PRAGMA synchronous=OFF', 'PRAGMA temp_store=FILE', 'PRAGMA cache_size=-8000',
              'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)',
              'CREATE TABLE calls (stop INTEGER, secs INTEGER, trip INTEGER, flags INTEGER, PRIMARY KEY (stop, secs, trip)) WITHOUT ROWID',
              'CREATE TABLE stops (stop INTEGER PRIMARY KEY, atco TEXT, cell INTEGER, lat REAL, lon REAL, name TEXT, '
              'indicator TEXT, bearing TEXT, routes TEXT)'):
        db.execute(q)
    db.execute('ATTACH DATABASE ? AS r', (raw_path,))
    db.execute('INSERT OR IGNORE INTO calls SELECT stop, secs, trip, flags FROM r.raw ORDER BY stop, secs, trip')
    db.commit()
    db.execute('DETACH DATABASE r')
    os.remove(raw_path)
    status(state='building', step='stops')

    def routes_by_stop():
        """(stop, 'routes') for every stop something calls at, read in stop order (the calls are kept that way)."""
        cur, names = None, set()
        for stop, trip in db.cursor().execute('SELECT stop, trip FROM calls'):
            if stop != cur:
                if cur is not None:
                    yield cur, ' '.join(sorted(names, key=route_order))[:400]
                cur, names = stop, set()
            ix = trip_route[trip] if trip < len(trip_route) else 0
            if ix:
                names.add(route_names[ix])
        if cur is not None:
            yield cur, ' '.join(sorted(names, key=route_order))[:400]

    rows_out = []                                      # written as they are read (another table, same connection)
    place = tt.cursor()
    for stop, routes in routes_by_stop():
        got = place.execute('SELECT stop_id, lat, lon, name, indicator, bearing FROM stops WHERE rowid=?', (stop,)).fetchone()
        if not got or got[1] is None or got[2] is None or not (49 < got[1] < 61.5 and -9 < got[2] < 2.5):
            continue
        sid, lat, lon, name, ind, bearing = got
        rows_out.append((stop, sid, cell_of(lat, lon), lat, lon, name or '', ind or '', bearing or '', routes))
        if len(rows_out) >= BATCH:
            db.executemany('INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?)', rows_out); rows_out.clear()
    if rows_out:
        db.executemany('INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?)', rows_out)
    db.commit()
    db.execute('CREATE INDEX stops_cell ON stops (cell)')
    db.execute('CREATE UNIQUE INDEX stops_atco ON stops (atco)')
    status(state='building', step='bus stations')
    stations_error = ''
    for q in ('CREATE TABLE stations (code TEXT PRIMARY KEY, name TEXT, low TEXT, lat REAL, lon REAL, kind TEXT, '
              'stands INTEGER, routes TEXT, locality TEXT)',
              'CREATE TABLE station_stands (code TEXT, stop INTEGER)'):
        db.execute(q)
    if naptan_csv:
        try:
            add_stations(db, read_naptan(naptan_csv))
        except Exception as exc:     # the boards for single stops still work without stations
            stations_error = (type(exc).__name__ + ': ' + str(exc))[:120]
    else:
        stations_error = 'no NaPTAN stop list'
    db.execute('CREATE INDEX station_stands_code ON station_stands (code)')
    db.execute('CREATE INDEX station_stands_stop ON station_stands (stop)')
    db.commit()
    counts = {'calls': db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],
              'stops': db.execute('SELECT COUNT(*) FROM stops').fetchone()[0], 'journeys': n_trips,
              'busStations': db.execute('SELECT COUNT(*) FROM stations').fetchone()[0]}
    meta = {'format': FORMAT, 'timetableBuiltAt': built_at, 'builtAt': int(time.time()), 'counts': counts,
            'stationsError': stations_error}
    db.executemany('INSERT INTO meta VALUES (?, ?)', ((k, json.dumps(v)) for k, v in meta.items()))
    db.commit(); db.close(); tt.close()
    os.replace(tmp, out)
    return counts


def route_order(name):
    """'2' < '10' < '10A' < 'X46' < 'Skylink': numbers by size, then letters."""
    m = re.match(r'(\D*)(\d+)(.*)', name)
    return (1, m.group(1).lower(), int(m.group(2)), m.group(3).lower()) if m and len(m.group(1)) <= 2 else (2, name.lower(), 0, '')


STAND_TYPES = ('BCS', 'BCQ')          # NaPTAN: a stand/bay in a bus or coach station (fixed or variable)
HUB_TYPES = ('BST',)                   # NaPTAN: a bus or coach station's access area (the station itself)


def read_naptan(path):
    """The stands and station points from the NaPTAN stop list (CSV), read a line at a time."""
    import csv
    stands, hubs = {}, []
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            kind = (r.get('StopType') or '').strip().upper()
            if kind not in STAND_TYPES + HUB_TYPES and not (kind == 'BCT' and street_bay(r.get('CommonName'), r.get('Indicator'))):
                continue
            if (r.get('Status') or 'active').strip().lower() not in ('active', 'act', ''):
                continue
            try:
                lat, lon = float(r.get('Latitude') or 'x'), float(r.get('Longitude') or 'x')
            except ValueError:
                continue
            if not (49 < lat < 61.5 and -9 < lon < 2.5):
                continue
            atco = (r.get('ATCOCode') or '').strip()
            name = (r.get('CommonName') or '').replace('_', ' ').strip()[:80]
            place = (r.get('LocalityName') or '').strip()[:60]
            if not re.fullmatch(r'[A-Za-z0-9]{2,20}', atco):
                continue
            if kind in HUB_TYPES:
                hubs.append((atco, name, place, lat, lon))
            else:
                stands[atco] = (name, place, lat, lon)
    return stands, hubs


STATION_WORDS = re.compile(r'(?i)\b(bus station|bs|bus stn|bus sta|coach station|coach stn|interchange|bus park)\b')


def street_bay(common, indicator):
    """0.14.1 (owner 12 Nov 21:25, Beverley: its six bays are in NaPTAN as street stops, "Beverley BS", "Bay A"..."Bay F"):
    a street stop counts as a station stand when it is a bay, stand or stance AND its name says it is at a bus or coach
    station or an interchange."""
    return bool(re.match(r'(?i)\s*(bay|stand|stance)\b', indicator or '')) and bool(STATION_WORDS.search(common or ''))


def tidy_station(common):
    """'Beverley BS' / 'Beverley Bs' / 'Beverley Bus Stn' -> 'Beverley Bus Station'.
    0.14.3 (owner 13 Nov 00:04, Doncaster stacked 30 times): a stand's own code on the end of the name goes -
    'Doncaster Frenchgate Interchange/A1' -> 'Doncaster Frenchgate Interchange'."""
    common = re.sub(r'\s+', ' ', (common or '').replace('_', ' ')).strip()
    common = re.sub(r'\s*/\s*[A-Za-z]{0,2}\d{0,3}[A-Za-z]?$', '', common).strip() or common
    # 0.14.4 (owner 13 Nov 10:36, the stations check: "Bridgend Bus Station Stand 11", "Merthyr Bus Station 1",
    # "Newport Friars Walk 1" ... "15" - a stand's own label on the end of the name goes, so a station's stands share one name)
    for _ in range(2):
        cut = STAND_TAIL.sub('', common).strip()
        m = BARE_NUMBER.search(cut)
        if m and not re.search(r'(?i)\b(terminal|terminals|junction|gate|j)$', cut[:m.start()]):
            cut = cut[:m.start()].strip()
        cut = re.sub(r'(?i)\b(bus station|coach station|interchange|bus stn|bs)\s+[A-Z]\d?$', r'\1', cut).strip()
        if not cut or cut == common:
            break
        common = cut
    common = re.sub(r'(?i)\bintc\b', 'Interchange', common)          # 0.14.5: "Doncaster Intc"
    return re.sub(r'(?i)\b(bs|bus stn|bus sta)\b', 'Bus Station', common)


# 0.14.4: "Stand 11", "Stand1", "Stance 3", "Bay A4", "Setdown Stand Z", "Arrival Stand", "Night Std", "Alighting Only"
STAND_TAIL = re.compile(r'(?i)\s+(?:(?:setdown|set down|arrivals?|departures?|night)\s+)?(?:stand|stance|bay|std)'
                        r'(?:\s+(?:no\.?\s*)?[a-z]{1,2}\d{0,3}[a-z]?|\s*\d{1,3}[a-z]?)?$|\s+alighting only$')
BARE_NUMBER = re.compile(r'\s+\d{1,3}[a-z]?$', re.I)


def station_name(common, place):
    """'Bus Station' in Beverley -> 'Beverley Bus Station'; 'Hull Paragon Interchange' stays as it is.
    0.14.4: stands named only by their town ("Annan Stance 1") -> 'Annan Bus Stances'."""
    raw = common or ''
    common = tidy_station(common) or 'Bus Station'
    if place and common.lower() == place.lower():
        common = f'{place} Bus ' + ('Stances' if re.search(r'(?i)\bstance', raw) else 'Stands')
    first = (place or '').split(' ')[0].lower()
    if place and place.lower() not in common.lower() and not (first and re.search(r'\b' + re.escape(first) + r'\b', common.lower())):
        return f'{place} {common}'[:80]
    return common[:80]


def station_kind(name):
    low = name.lower()
    return 'coach' if 'coach' in low else 'interchange' if 'interchange' in low else 'bus'


def _metres(a_lat, a_lon, b_lat, b_lon):
    x = (b_lon - a_lon) * 111_320.0 * math.cos(math.radians((a_lat + b_lat) / 2))
    return math.hypot(x, (b_lat - a_lat) * 110_574.0)


def group_stations(stands, hubs):
    """[{code, name, lat, lon, locality, stands: [atco]}]. stands: {atco: (name, place, lat, lon)} for stands that buses
    call at; hubs: [(atco, name, place, lat, lon)] NaPTAN station points. Each stand joins the nearest station point
    within HUB_REACH_M; the rest are grouped by name and place, stands within CLUSTER_M of each other."""
    groups = {h[0]: {'code': h[0], 'name': station_name(h[1], h[2]), 'lat': h[3], 'lon': h[4], 'locality': h[2], 'stands': []}
              for h in hubs}
    cell = {}
    for h in hubs:
        cell.setdefault((round(h[3] * 50), round(h[4] * 30)), []).append(h)
    loose = []
    for atco, (name, place, lat, lon) in stands.items():
        best = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for h in cell.get((round(lat * 50) + dy, round(lon * 30) + dx), ()):
                    d = _metres(lat, lon, h[3], h[4])
                    if d <= HUB_REACH_M and (best is None or d < best[0]):
                        best = (d, h[0])
        if best:
            groups[best[1]]['stands'].append(atco)
        else:
            loose.append((atco, name, place, lat, lon))
    by_name = {}
    for x in loose:      # 0.14.1: "Beverley BS", "Beverley Bs" and "Beverley Bus Station" are one name
        by_name.setdefault((station_name(x[1], x[2]).lower(), x[2].lower()), []).append(x)
    for (_, _), members in by_name.items():
        clusters = []
        for m in members:
            home = next((c for c in clusters if any(_metres(m[3], m[4], o[3], o[4]) <= CLUSTER_M for o in c)), None)
            if home is None:
                clusters.append([m])
            else:
                home.append(m)
        for c in clusters:
            names = [station_name(o[1], o[2]) for o in c]
            name = max(set(names), key=names.count)
            c.sort()
            groups['X' + c[0][0]] = {'code': ('X' + c[0][0])[:20], 'name': name, 'lat': sum(o[3] for o in c) / len(c),
                                     'lon': sum(o[4] for o in c) / len(c), 'locality': c[0][2], 'stands': [o[0] for o in c],
                                     # one lonely stand is just a stop - unless it is right beside a station (0.14.3)
                                     'lonely': len(c) < 2 and not KEEP_ALONE.search(name)}
    return merge_close([g for g in groups.values() if g['stands']], stands)


MERGE_M = 120                # 0.14.3: stations whose stands are this close to each other's are one station
# 0.14.4 (owner 13 Nov 10:45, every box ticked): one stand on its own is still a station when its name says so - coach
# stations and parks, park and rides, travel centres and termini as well as bus stations and interchanges
KEEP_ALONE = re.compile(r'(?i)bus station|coach station|interchange|bus park|coach park|coach facility|park[ -]?(and|&)[ -]?ride'
                        r'|travel centre|terminus')
STATION_SAYS = re.compile(r'(?i)\b(bus station|coach station|interchange)\b')
PLAIN_WORDS = re.compile(r'(?i)\b(bus|coach|station|interchange|metro|city|town|centre|center|upon|on|the|and|of)\b|[^a-z ]')


def own_words(g):
    """0.14.4: what is left of a station's name without its town and the usual words - 'Haymarket', 'Eldon Square'."""
    low = g['name'].lower()
    for w in (g.get('locality') or '').lower().split(' '):
        if w:
            low = re.sub(r'\b' + re.escape(w) + r'\b', ' ', low)
    return ' '.join(PLAIN_WORDS.sub(' ', low).split())


def two_stations(gi, gj):
    """0.14.4 (owner 13 Nov 10:45: Newcastle's Haymarket and Eldon Square bus stations, ~150 m apart, are two stations):
    both names say bus station or interchange, and each has its own different name. 'Dundee City Centre Bus Station' and
    'Dundee City Centre Seagate Bus Station', or 'Wallsend Interchange' and 'Wallsend Metro Interchange', still join."""
    if gi.get('lonely') or gj.get('lonely') or not (STATION_SAYS.search(gi['name']) and STATION_SAYS.search(gj['name'])):
        return False
    a, b = own_words(gi), own_words(gj)
    return bool(a and b and a not in b and b not in a)


def merge_close(groups, stands):
    """0.14.3 (owner 13 Nov 00:04: Doncaster Frenchgate Interchange showed as a stack of stations): the safety net -
    groups with stands within MERGE_M of each other's become one station, named and placed after the biggest."""
    pos = {a: (v[2], v[3]) for a, v in stands.items()}
    parent = list(range(len(groups)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            gi, gj = groups[i], groups[j]
            if abs(gi['lat'] - gj['lat']) > 0.02 or abs(gi['lon'] - gj['lon']) > 0.03:
                continue
            if two_stations(gi, gj):
                continue
            if any(_metres(*pos[a], *pos[b]) <= MERGE_M for a in gi['stands'] if a in pos for b in gj['stands'] if b in pos):
                parent[root(j)] = root(i)
    merged = {}
    for i, g in enumerate(groups):
        merged.setdefault(root(i), []).append(g)
    out = []
    for parts in merged.values():
        if all(g.get('lonely') for g in parts):
            continue
        parts.sort(key=lambda g: (bool(g.get('lonely')), -len(g['stands']), g['code']))
        main = dict(parts[0])
        main.pop('lonely', None)
        main['stands'] = [a for g in parts for a in g['stands']]
        out.append(main)
    return out


def add_stations(db, naptan):
    """Write the bus stations (and which stops are their stands) into the stop index being built."""
    stands, hubs = naptan
    called = {}
    for n, atco, routes in db.execute('SELECT stop, atco, routes FROM stops'):
        if atco in stands:
            called[atco] = (n, routes)
    groups = group_stations({a: v for a, v in stands.items() if a in called}, hubs)
    for g in groups:
        names = set()
        for a in g['stands']:
            names.update(r for r in called[a][1].split(' ') if r)
        db.execute('INSERT OR REPLACE INTO stations VALUES (?,?,?,?,?,?,?,?,?)',
                   (g['code'], g['name'], g['name'].lower(), g['lat'], g['lon'], station_kind(g['name']), len(g['stands']),
                    ' '.join(sorted(names, key=route_order))[:600], g['locality']))
        db.executemany('INSERT INTO station_stands VALUES (?,?)', ((g['code'], called[a][0]) for a in g['stands']))


def needs_build(data_dir, tt_built_at):
    """True when stops.sqlite is missing, of an unknown format, or made from a different timetable."""
    path = os.path.join(data_dir, 'stops.sqlite')
    if not os.path.exists(path):
        return True
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            meta = {k: json.loads(v) for k, v in db.execute('SELECT key, value FROM meta')}
        finally:
            db.close()
    except Exception:
        return True
    if meta.get('format') != FORMAT or meta.get('timetableBuiltAt') != tt_built_at:
        return True
    # 0.14.0: the NaPTAN stop list could not be fetched last time: try again after a few hours
    return bool(meta.get('stationsError')) and time.time() - meta.get('builtAt', 0) > 6 * 3600


# ---------------------------------------------------------------- reading
class StopIndex:
    def __init__(self, path):
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
        self.db.execute('PRAGMA cache_size=-4000')
        self.meta = {k: json.loads(v) for k, v in self.db.execute('SELECT key, value FROM meta')}
        if self.meta.get('format') != FORMAT:
            raise ValueError('stop index format unknown - will rebuild')
        self.lock = threading.Lock()

    def close(self):
        self.db.close()

    def in_view(self, s, w, n, e, limit=MAX_STOPS):
        """Stops inside the view, nearest the middle first."""
        lat0, lat1 = int(math.floor(s * CELL)), int(math.floor(n * CELL))
        lon0, lon1 = int(math.floor((w + 20) * CELL)), int(math.floor((e + 20) * CELL))
        cells = [a * 100_000 + b for a in range(lat0, lat1 + 1) for b in range(lon0, lon1 + 1)]
        if len(cells) > 400:
            return []
        out = []
        with self.lock:
            for i in range(0, len(cells), 200):
                part = cells[i:i + 200]
                out += self.db.execute(f'SELECT atco, lat, lon, name, indicator, bearing FROM stops WHERE cell IN ({",".join("?" * len(part))}) '
                                       'AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?', part + [s, n, w, e]).fetchall()
        cl, co = (s + n) / 2, (w + e) / 2
        out.sort(key=lambda r: (r[1] - cl) ** 2 + (r[2] - co) ** 2)
        return out[:limit]

    def stop(self, atco):
        with self.lock:
            return self.db.execute('SELECT stop, atco, lat, lon, name, indicator, bearing, routes FROM stops WHERE atco=?',
                                   (atco,)).fetchone()

    def load_stations(self):
        with self.lock:
            self.stations = self.db.execute('SELECT code, name, low, lat, lon, kind, stands, routes, locality FROM stations').fetchall()
            rows = self.db.execute('SELECT t.code, s.atco, s.lat, s.lon FROM station_stands t JOIN stops s ON s.stop = t.stop').fetchall()
        by_station = {}
        for code, atco, lat, lon in rows:
            by_station.setdefault(code, []).append((atco, lat, lon))
        # 0.14.2 (owner 12 Nov 22:20-22:31, Beverley's six bays piled under the station icon; Hull's 38 in a long smear): how
        # far apart each station's stands are (the typical gap to the nearest other stand), so the map can leave them out
        # while they would overlap on screen and show them once zoomed in far enough for them to sit apart.
        self.stand_gap = {}
        for code, pts in by_station.items():
            gaps = sorted(min(_metres(a[1], a[2], b[1], b[2]) for b in pts if b is not a) for a in pts) if len(pts) > 1 else [1e9]
            gap = gaps[len(gaps) // 2]
            for atco, _, _ in pts:
                self.stand_gap[atco] = gap
        return len(self.stations)

    def stand_shown(self, atco, view_height_deg):
        """False for a station's stand while it would sit on top of its neighbours: closer than STAND_GAP_PX on a screen
        about SCREEN_PX tall showing this much of the map. Stops that are not stands are always shown."""
        gap = getattr(self, 'stand_gap', {}).get(atco)
        if gap is None:
            return True
        metres_per_px = view_height_deg * 110_574.0 / SCREEN_PX
        return gap / max(metres_per_px, 1e-6) >= STAND_GAP_PX

    def stations_in_view(self, s, w, n, e, limit=MAX_STOPS):
        cl, co = (s + n) / 2, (w + e) / 2
        got = [r for r in getattr(self, 'stations', ()) if s <= r[3] <= n and w <= r[4] <= e]
        got.sort(key=lambda r: (r[3] - cl) ** 2 + (r[4] - co) ** 2)
        return got[:limit]

    def search_stations(self, q, limit=8):
        """By name: names starting with it first, then a word starting with it, then anywhere in the name."""
        q = re.sub(r'\s+', ' ', q or '').strip().lower()[:60]
        if len(q) < 2:
            return []
        starts, words, inside = [], [], []
        for r in getattr(self, 'stations', ()):
            low = r[2]
            if low.startswith(q):
                starts.append(r)
            elif any(w.startswith(q) for w in re.split(r'[\s(/-]+', low)):
                words.append(r)
            elif q in low:
                inside.append(r)
        return (sorted(starts, key=lambda r: (len(r[1]), r[1])) + sorted(words, key=lambda r: (-r[6], r[1])) + inside)[:limit]

    def station(self, code):
        with self.lock:
            row = self.db.execute('SELECT code, name, lat, lon, kind, stands, routes, locality FROM stations WHERE code=?', (code,)).fetchone()
            if row is None:
                return None, []
            stands = self.db.execute('SELECT s.stop, s.atco, s.indicator FROM station_stands t JOIN stops s ON s.stop = t.stop '
                                     'WHERE t.code=?', (code,)).fetchall()
        return row, stands

    def calls(self, stop, lo, hi):
        with self.lock:
            return self.db.execute('SELECT secs, trip, flags FROM calls WHERE stop=? AND secs BETWEEN ? AND ? ORDER BY secs',
                                   (stop, lo, hi)).fetchall()


# ---------------------------------------------------------------- words
def clock(secs):
    secs = int(round(secs)) % 86400
    return f'{secs // 3600:02d}:{secs % 3600 // 60:02d}'


PLACE_WORDS = {'opp': 'opposite', 'adj': 'next to', 'o/s': 'outside', 'nr': 'near', 'os': 'outside'}


def place_word(indicator):
    """NaPTAN's short words ('opp', 'adj', 'o/s') in plain English; anything else as given."""
    ind = (indicator or '').strip()
    return PLACE_WORDS.get(ind.lower().rstrip('.'), ind)


def stop_title(name, indicator):
    name = (name or 'Bus stop').replace('_', ' ').strip()
    ind = place_word(indicator)
    if ind.lower() in ('at', 'in', 'on'):         # 0.14.4 (owner 13 Nov 10:39: "Doncaster Interchange (at)")
        return name
    return f'{name} ({ind})' if ind and ind.lower() not in name.lower() else name


def badge_of(indicator):
    """The letter or number on the stop's flag: 'Stop B' -> 'B', 'Stand 12' -> '12', 'Bay 3a' -> '3A', 'B' -> 'B'.
    'opp', 'adj', 'o/s' and other words -> None."""
    ind = (indicator or '').strip()
    m = re.fullmatch(r'(?i:stop|stand|stance|bay)\s*(?:no\.?\s*)?([A-Za-z]{1,2}\d{0,2}|\d{1,3}[A-Za-z]?)', ind) \
        or re.fullmatch(r'([A-Z]{1,2}\d{0,2}|\d{1,3}[A-Za-z]?)', ind)
    if not m or m.group(1).upper() in ('OPP', 'ADJ', 'NR', 'OS', 'BY', 'IN', 'AT', 'ON'):
        return None
    return m.group(1).upper()


BOARD_BYTES = 200_000          # 0.14.4: HQ takes at most 256 KB of details; a board must fit with room to spare


def fit_board(board):
    """0.14.4 (owner 13 Nov 10:39: Doncaster Frenchgate Interchange's board was empty in the daytime): ~100 buses, each
    with up to 60 stops, came to ~400 KB and HQ turned it away. While a board is too big, each bus keeps fewer of its
    stops: the next ones for a departure, the last ones before here for an arrival."""
    for keep in (CALLS_SHOWN, 30, 15, 8, 4, 0):
        for tab in board['tabs']:
            arriving = tab['name'] == 'Arrivals'
            for r in tab['rows']:
                if 'calls' in r and len(r['calls']) > keep:
                    r['calls'] = (r['calls'][len(r['calls']) - keep:] if arriving else r['calls'][:keep]) if keep else []
        if len(json.dumps(board, separators=(',', ':'))) <= BOARD_BYTES:
            break
    return board


def company_word(name):
    """0.10.1 (owner 10 Nov 13:15): Stagecoach is one name nationwide; regional company names mean nothing to passengers."""
    name = (name or '').strip()
    return 'Stagecoach' if name.lower().startswith('stagecoach') else name


def running_words(late_s):
    m = round(late_s / 60)
    return 'about on time' if abs(m) <= 1 else f'about {m} min late' if m > 0 else f'about {-m} min early'


# ---------------------------------------------------------------- where a live bus is along its journey
def _xy(lat, lon, lat0):
    return lon * 111_320.0 * math.cos(math.radians(lat0)), lat * 110_574.0


def place_on_journey(stops, lat, lon, report_clock):
    """Where a bus reported at (lat, lon) at `report_clock` (seconds on the journey's own clock) is along its stops.
    Returns (progress, late_seconds) - progress 2.5 = halfway from the 3rd stop to the 4th - or None when the report
    is not near the journey's stops. A route can pass the same place twice; the stretch whose timetabled time is
    closest to the report wins."""
    cands = []
    px, py = _xy(lat, lon, lat)
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if a[1] is None or b[1] is None:
            continue
        ax, ay = _xy(a[1], a[2], lat)
        bx, by = _xy(b[1], b[2], lat)
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        f = 0.0 if ll < 1 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        d = math.hypot(ax + f * dx - px, ay + f * dy - py)
        if d > NEAR_ROUTE_M:
            continue
        t_a, t_b = a[6], b[5]
        due = t_a + f * (t_b - t_a)
        cands.append((d, abs(report_clock - due), i + f, report_clock - due))
    if not cands:
        return None
    # the stretches the report is nearest to (GPS is often 5-20 m out); of those, the one whose time fits best
    nearest = min(c[0] for c in cands)
    _, _, progress, late = min((c for c in cands if c[0] <= nearest + SAME_PLACE_M), key=lambda c: c[1])
    if progress <= 0.001 and report_clock < stops[0][6]:
        late = 0.0                 # waiting at the start: it leaves on time
    return progress, max(late, -EARLY_LIMIT_S)


# ---------------------------------------------------------------- the board
class BoardMaker:
    """Works out one stop's board. `tt` is the bus engine's Timetable (its db and lock are shared, read-only)."""

    def __init__(self, tt, index):
        self.tt, self.index = tt, index
        self.trips = OrderedDict()       # trips rowid -> facts, small
        self.journeys = OrderedDict()    # trip_id -> its stops

    def _trip(self, trip_n):
        if trip_n in self.trips:
            return self.trips[trip_n]
        tt = self.tt
        with tt.lock:
            row = tt.db.execute(
                'SELECT t.trip_id, t.service_id, t.headsign, r.short_name, a.name, e.first_stop, e.last_stop, e.first_dep '
                'FROM trips t LEFT JOIN routes r ON r.route_id = t.route_id LEFT JOIN agency a ON a.agency_id = r.agency_id '
                'LEFT JOIN trip_ends e ON e.trip_id = t.trip_id WHERE t.rowid = ?', (trip_n,)).fetchone()
            if row:
                ends = {}
                for sid in (row[5], row[6]):
                    s = tt.db.execute('SELECT name, indicator FROM stops WHERE stop_id=?', (sid,)).fetchone() if sid else None
                    ends[sid] = (s[0] or '').replace('_', ' ').strip() if s else ''
        facts = None
        if row:
            facts = {'trip': row[0], 'service': row[1], 'headsign': (row[2] or '').strip(), 'route': (row[3] or '').strip(),
                     'company': company_word(row[4]), 'from': ends.get(row[5], ''), 'to': ends.get(row[6], ''),
                     'start': row[7]}
        self.trips[trip_n] = facts
        while len(self.trips) > 5000:
            self.trips.popitem(last=False)
        return facts

    def _stops(self, trip_id):
        if trip_id in self.journeys:
            self.journeys.move_to_end(trip_id)
            return self.journeys[trip_id]
        got = self.tt.stops_of(trip_id)
        self.journeys[trip_id] = got
        while len(self.journeys) > 600:
            self.journeys.popitem(last=False)
        return got

    def _clock_now(self, now):
        local = datetime.fromtimestamp(now, LONDON)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight, midnight.timestamp(), now - midnight.timestamp()

    def _window(self, stop_n, midnight, s_now):
        """(secs, trip facts, flags, base) for every call at one stop from LOOKBACK ago to AHEAD from now, on journeys
        that run - yesterday's late ones (GTFS times past 24:00), today's, and tomorrow's early ones."""
        for shift in (-1, 0, 1):
            day = (midnight + timedelta(days=shift)).strftime('%Y%m%d')
            base = -shift * 86400                      # today's clock -> that service day's clock
            lo, hi = s_now + base - LOOKBACK, s_now + base + AHEAD
            if hi < 0 or lo > 36 * 3600:
                continue
            for secs, trip_n, flags in self.index.calls(stop_n, int(lo), int(hi)):
                f = self._trip(trip_n)
                if f is not None and self.tt.runs_on(f['service'], day):
                    yield secs, f, flags, base

    def board(self, atco, now, live):
        """{'title', 'about', 'routes', 'tabs': [Departures, Arrivals]} for one stop, or None if it is unknown.
        live: {trip_id: {'key', 'lat', 'lon', 'recorded'}} - buses near the stop matched to their journeys."""
        st = self.index.stop(atco)
        if st is None:
            return None
        stop_n, atco, lat, lon, name, indicator, bearing, routes = st
        midnight, midnight_ts, s_now = self._clock_now(now)
        deps, arrs, seen = [], [], set()
        for secs, f, flags, base in self._window(stop_n, midnight, s_now):
            if (f['trip'], secs) in seen:
                continue
            seen.add((f['trip'], secs))
            arriving = bool(flags & 2)
            row = self._row(f, secs, arriving, base, s_now, now, midnight_ts, atco, live)
            if row is not None:
                (arrs if arriving else deps).append(row)
        deps.sort(key=lambda r: r.pop('_order')); arrs.sort(key=lambda r: r.pop('_order'))
        deps, arrs = deps[:ROWS], arrs[:ROWS]
        about = []
        if badge_of(indicator):
            about.append(indicator.strip()[:30] if not re.fullmatch(r'[A-Z0-9]{1,4}', indicator.strip()) else 'Stop ' + indicator.strip())
        if COMPASS.get((bearing or '').upper()):
            about.append('buses head ' + COMPASS[bearing.upper()])
        towards = []
        for r in deps:
            if r['place'] not in towards:
                towards.append(r['place'])
        if towards:
            about.append('towards ' + ', '.join(towards[:2]))
        return fit_board({'title': stop_title(name, indicator)[:80], 'badge': badge_of(indicator),
                          'about': ' · '.join(about)[:120],
                          'routes': [r for r in (routes or '').split(' ') if r][:40],
                          'updated': clock(s_now),
                          'tabs': [{'name': 'Departures', 'empty': 'No buses due in the next 2 hours.', 'rows': deps},
                                   {'name': 'Arrivals', 'empty': 'No journeys finish here in the next 2 hours.', 'rows': arrs}]})

    def station_board(self, code, now, live):
        """0.14.0 (owner 12 Nov 20:22: bus stations "need to be like the train ones"): one board for all of a station's
        stands, with the stand each bus uses. Departures: every bus leaving (not those finishing here); Arrivals: every
        bus arriving (not those starting here), as on a railway station's board. A journey calling at two stands is
        shown once: leaving from the last one, arriving at the first."""
        st, stands = self.index.station(code)
        if st is None:
            return None
        code, name, lat, lon, kind, n_stands, routes, place = st
        midnight, midnight_ts, s_now = self._clock_now(now)
        leave, come = {}, {}
        for stop_n, atco, indicator in stands:
            for secs, f, flags, base in self._window(stop_n, midnight, s_now):
                key = (f['trip'], base)
                if not flags & 2 and (key not in leave or secs > leave[key][0]):
                    leave[key] = (secs, f, base, atco, indicator)
                if not flags & 1 and (key not in come or secs < come[key][0]):
                    come[key] = (secs, f, base, atco, indicator)
        tabs = []
        for picks, arriving in ((leave, False), (come, True)):
            rows = []
            for secs, f, base, atco, indicator in picks.values():
                row = self._row(f, secs, arriving, base, s_now, now, midnight_ts, atco, live)
                if row is not None:
                    stand = badge_of(indicator)
                    if stand:
                        row['platform'] = stand[:4]
                    rows.append(row)
            rows.sort(key=lambda r: r.pop('_order'))
            tabs.append(rows[:STATION_ROWS])
        word = {'coach': 'Coach station', 'interchange': 'Interchange'}.get(kind, 'Bus station')
        about = f'{word} · {n_stands} stand{"s" if n_stands != 1 else ""}'
        board = {'title': name[:80], 'about': about[:120], 'routes': [r for r in (routes or '').split(' ') if r][:60],
                 'updated': clock(s_now),
                 'tabs': [{'name': 'Departures', 'empty': 'No buses due in the next 2 hours.', 'rows': tabs[0]},
                          {'name': 'Arrivals', 'empty': 'No buses arriving in the next 2 hours.', 'rows': tabs[1]}]}
        return fit_board(board)

    def _row(self, f, secs, arriving, base, s_now, now, midnight_ts, atco, live):
        sched_today = secs - base                      # on today's clock
        place = (f['from'] if arriving else (f['headsign'] or f['to'])) or ('Bus' if arriving else 'Not given')
        place = tidy_station(place) or place   # 0.14.5 (owner 13 Nov 11:50): "Worksop Bus Station Bay F" -> "Worksop Bus Station"
        row = {'time': clock(sched_today), 'place': place[:80], 'state': 'unknown', 'status': 'Timetable'}
        if f['route']:
            row['route'] = f['route'][:12]
        if f['company']:
            row['operator'] = f['company'][:40]
        bus = live.get(f['trip'])
        stops = None
        k = None
        if bus is not None and now - bus['recorded'] <= LIVE_FRESH:
            stops = self._stops(f['trip'])
            k = next((i for i, s in enumerate(stops) if s[0] == atco and secs in (s[5], s[6])), None)
            if k is None:
                k = next((i for i, s in enumerate(stops) if s[0] == atco), None)
            report_clock = bus['recorded'] - midnight_ts + base
            got = place_on_journey(stops, bus['lat'], bus['lon'], report_clock) if k is not None else None
            if got is not None:
                progress, late = got
                if progress > k + 0.02 or (progress >= k and arriving):
                    return None                        # already been and gone
                expected = max(sched_today + late, s_now)
                mins = int((expected - s_now) // 60)
                row.update(status='Due' if mins <= 0 else f'{mins} min', state='late' if late >= LATE_STATE_S else 'ontime',
                           live=True, reason='Live: ' + running_words(late) + '.', bus=('bus:' + bus['key'])[:128],
                           lat=round(bus['lat'], 6), lon=round(bus['lon'], 6))
                row['_order'] = expected
                row['calls'] = self._calls(stops, k, arriving, late)
                return row
        if sched_today < s_now - 60:
            return None                                # timetable only, and its time has gone
        row['reason'] = 'Timetable time: this bus has not been seen live yet.'
        row['_order'] = sched_today
        if stops is None:
            stops = self._stops(f['trip'])
            k = next((i for i, s in enumerate(stops) if s[0] == atco and secs in (s[5], s[6])), None)
        if k is not None:
            row['calls'] = self._calls(stops, k, arriving, None)
        return row

    def _calls(self, stops, k, arriving, late):
        part = stops[max(0, k - CALLS_SHOWN):k] if arriving else stops[k + 1:k + 1 + CALLS_SHOWN]
        out = []
        for s in part:
            booked = s[5] if s[5] >= 0 else s[6]
            item = {'name': stop_title(s[3], s[4])[:60], 'time': clock(booked) if booked >= 0 else ''}
            if late is not None and abs(late) >= LATE_STATE_S and booked >= 0:
                item['expected'] = clock(booked + late)
            out.append(item)
        return out


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'build':
        try:
            os.nice(19)
        except OSError:
            pass

        def report(**kw):
            print('STOPS ' + json.dumps(kw), flush=True)
        naptan = None
        try:     # 0.14.0: the NaPTAN stop list, for bus stations (the boards for single stops work without it)
            import importlib.util
            spec = importlib.util.spec_from_file_location('tpa_bus_timetable_sb', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'timetable.py'))
            timetable = importlib.util.module_from_spec(spec); spec.loader.exec_module(timetable)
            url = os.environ.get('TPA_BUS_NAPTAN_TEST_URL', '').strip() or timetable.NAPTAN_URL
            if url != timetable.NAPTAN_URL and not url.startswith('http://127.0.0.1:'):
                raise ValueError('test address must be 127.0.0.1')
            naptan = timetable.download(url, os.path.join(sys.argv[2], 'naptan-stations.csv'), timetable.MAX_NAPTAN_BYTES,
                                        report, 'bus stops (NaPTAN)')
        except Exception as exc:
            report(state='warning', warning=('bus stations: ' + type(exc).__name__ + ' ' + str(exc))[:120])
        try:
            report(state='built', **build(sys.argv[2], report, naptan))
        except Exception as exc:   # one clear line for the engine; never a traceback wall
            report(state='error', error=(type(exc).__name__ + ': ' + str(exc))[:120])
            sys.exit(1)
        finally:
            for p in (naptan, os.path.join(sys.argv[2], 'naptan-stations.csv.part')):
                if p and os.path.exists(p):
                    os.remove(p)
    else:
        print(__doc__)
        sys.exit(2)
