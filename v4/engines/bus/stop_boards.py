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

FORMAT = 1
CELL = 50                    # cells of 1/50 degree (~2 km north-south) for "stops in view"
BATCH = 50_000
VIEW_SPAN = 0.06             # stops are shown only when the view is under ~6.5 km tall (street level)
MAX_STOPS = 400
AHEAD = 2 * 3600             # departures up to 2 hours ahead
LOOKBACK = 20 * 60           # ...and up to 20 minutes back (a late bus is still to come)
ROWS = 20
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
def build(data_dir, status=lambda **kw: None):
    """Build stops.sqlite from timetable.sqlite. Returns counts. Nothing half-built is ever left at stops.sqlite."""
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
    counts = {'calls': db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],
              'stops': db.execute('SELECT COUNT(*) FROM stops').fetchone()[0], 'journeys': n_trips}
    meta = {'format': FORMAT, 'timetableBuiltAt': built_at, 'builtAt': int(time.time()), 'counts': counts}
    db.executemany('INSERT INTO meta VALUES (?, ?)', ((k, json.dumps(v)) for k, v in meta.items()))
    db.commit(); db.close(); tt.close()
    os.replace(tmp, out)
    return counts


def route_order(name):
    """'2' < '10' < '10A' < 'X46' < 'Skylink': numbers by size, then letters."""
    m = re.match(r'(\D*)(\d+)(.*)', name)
    return (1, m.group(1).lower(), int(m.group(2)), m.group(3).lower()) if m and len(m.group(1)) <= 2 else (2, name.lower(), 0, '')


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
    return meta.get('format') != FORMAT or meta.get('timetableBuiltAt') != tt_built_at


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

    def board(self, atco, now, live, today=None):
        """{'title', 'about', 'routes', 'tabs': [Departures, Arrivals]} for one stop, or None if it is unknown.
        live: {trip_id: {'key', 'lat', 'lon', 'recorded'}} - buses near the stop matched to their journeys."""
        st = self.index.stop(atco)
        if st is None:
            return None
        stop_n, atco, lat, lon, name, indicator, bearing, routes = st
        local = datetime.fromtimestamp(now, LONDON) if today is None else today
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        day0 = midnight.strftime('%Y%m%d')
        midnight_ts = midnight.timestamp()
        s_now = now - midnight_ts                      # seconds after today's midnight
        deps, arrs, seen = [], [], set()
        for shift in (-1, 0, 1):
            day = (midnight + timedelta(days=shift)).strftime('%Y%m%d')
            base = -shift * 86400                      # today's clock -> that service day's clock
            lo, hi = s_now + base - LOOKBACK, s_now + base + AHEAD
            if hi < 0 or lo > 36 * 3600:
                continue
            for secs, trip_n, flags in self.index.calls(stop_n, int(lo), int(hi)):
                f = self._trip(trip_n)
                if f is None or (f['trip'], secs) in seen or not self.tt.runs_on(f['service'], day):
                    continue
                seen.add((f['trip'], secs))
                row = self._row(f, secs, flags, base, s_now, now, midnight_ts, atco, live)
                if row is not None:
                    (arrs if flags & 2 else deps).append(row)
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
        return {'title': stop_title(name, indicator)[:80], 'badge': badge_of(indicator),
                'about': ' · '.join(about)[:120],
                'routes': [r for r in (routes or '').split(' ') if r][:40],
                'updated': clock(s_now),
                'tabs': [{'name': 'Departures', 'empty': 'No buses due in the next 2 hours.', 'rows': deps},
                         {'name': 'Arrivals', 'empty': 'No journeys finish here in the next 2 hours.', 'rows': arrs}]}

    def _row(self, f, secs, flags, base, s_now, now, midnight_ts, atco, live):
        arriving = bool(flags & 2)
        sched_today = secs - base                      # on today's clock
        place = (f['from'] if arriving else (f['headsign'] or f['to'])) or ('Bus' if arriving else 'Not given')
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
            k = next((i for i, s in enumerate(stops) if s[0] == atco and (s[5] if arriving else s[6]) == secs), None)
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
            k = next((i for i, s in enumerate(stops) if s[0] == atco and (s[5] if arriving else s[6]) == secs), None)
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
        try:
            report(state='built', **build(sys.argv[2], report))
        except Exception as exc:   # one clear line for the engine; never a traceback wall
            report(state='error', error=(type(exc).__name__ + ': ' + str(exc))[:120])
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(2)
