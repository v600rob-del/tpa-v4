"""TPA bus timetables and stops (bus engine 0.6.0).

Owner, 1 Nov 2026 09:05-09:57: "having more points of reference for a buses journey will give us
higher accuracy in proximity predictions and icon travel"; "if we have the framework right, with as
many set points to work from as we can get, the accuracy and end user experience will be vastly
improved". 0.5.9 measured that 94% of buses send the stop their journey started from and its
scheduled departure time, so almost every bus can be matched to its timetabled journey.

Sources (both free, no key):
- Bus timetables: DfT Bus Open Data Service (BODS) GTFS, whole country
  (https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all/, ~1.3 GB zipped, ~7.5 GB
  unzipped on 30 Sep 2026). Open Government Licence. Includes route shapes.
- Bus stops: DfT NaPTAN (https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv), every stop
  in Great Britain with its exact position, name, indicator ("Stop B", "opp") and the way it faces.
  Open Government Licence.

Everything is kept on disk in timetable.sqlite and read only when needed, so it costs the bus
engine almost no memory. The build runs as a separate low-priority program (like the road map):

    python3 timetable.py fetch <data dir>       download (resumable) and build, if over 7 days old

Step 1 (0.6.0) changes nothing on the map: the engine only reports how many buses it can match.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import sys
import time
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo('Europe/London')
except Exception:            # no time zone data: treat as UTC (an hour out in summer, never a crash)
    LONDON = timezone.utc

GTFS_URL = 'https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all/'
NAPTAN_URL = 'https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv'
USER_AGENT = 'TPA-live-map/0.6 (bus engine timetables; tpaprojectuk@gmail.com)'
FORMAT = 1
MAX_GTFS_BYTES = 4_000_000_000     # safety limit for the zip (1.3 GB in Sep 2026)
MAX_NAPTAN_BYTES = 600_000_000
MAX_DAYS = 7                       # rebuilt when older than this (BODS publishes daily)
RETRY_WAITS = (10, 30, 60, 120, 300, 600)
CHUNK = 1 << 20
BATCH = 20_000
csv.field_size_limit(1 << 20)


# ---------------------------------------------------------------- downloading
def _patiently(job, waits=None):
    from urllib.error import URLError
    import http.client
    waits = RETRY_WAITS if waits is None else waits
    for wait in list(waits) + [None]:
        try:
            return job()
        except HTTPError as exc:
            if exc.code not in (408, 425, 429, 500, 502, 503, 504) or wait is None:
                raise
        except (URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
            if wait is None or isinstance(exc, (FileNotFoundError, PermissionError)):
                raise
        time.sleep(wait)


def download(url, path, limit, status=lambda **kw: None, label=''):
    """Download url to path. A cut-off download carries on where it stopped next time (HTTP Range)
    when the server allows it; otherwise it starts again. Nothing is kept half-written at `path`."""
    part = path + '.part'

    def once():
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {'User-Agent': USER_AGENT}
        if have:
            headers['Range'] = f'bytes={have}-'
        with urlopen(Request(url, headers=headers), timeout=180) as r:
            if have and r.status != 206:
                have = 0                       # server sent the whole file: start again
            total = r.headers.get('Content-Length')
            total = (int(total) + have) if total and total.isdigit() else None
            if total and total > limit:
                raise ValueError(f'{label or "download"} too big ({total // 1_000_000} MB)')
            got, last = have, 0.0
            with open(part, 'ab' if have else 'wb') as f:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    f.write(block)
                    got += len(block)
                    if got > limit:
                        raise ValueError(f'{label or "download"} too big')
                    if time.time() - last >= 5:
                        last = time.time()
                        status(state='downloading', what=label, mb=got // 1_000_000,
                               **({'percent': min(99, 100 * got // total)} if total else {}))
            if total and got < total:
                raise ConnectionError('partial download')
        os.replace(part, path)
    _patiently(once)
    return path


# ---------------------------------------------------------------- times
def gtfs_seconds(text):
    """'25:10:00' -> 90600 (GTFS times may pass midnight); None if not a time."""
    try:
        h, m, s = (int(x) for x in text.strip().split(':'))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


def service_day_and_seconds(iso_text):
    """A SIRI time ('2026-11-01T09:05:00+00:00') -> (local date 'YYYYMMDD', seconds after local midnight)."""
    try:
        t = datetime.fromisoformat(iso_text.strip().replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    local = t.astimezone(LONDON)
    return local.strftime('%Y%m%d'), local.hour * 3600 + local.minute * 60 + local.second


# ---------------------------------------------------------------- building
SCHEMA = (
    'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)',
    'CREATE TABLE stops (stop_id TEXT PRIMARY KEY, lat REAL, lon REAL, name TEXT, indicator TEXT, bearing TEXT, source TEXT)',
    'CREATE TABLE agency (agency_id TEXT PRIMARY KEY, name TEXT, noc TEXT)',
    'CREATE TABLE routes (route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT)',
    'CREATE TABLE calendar (service_id TEXT PRIMARY KEY, days TEXT, start TEXT, finish TEXT)',
    'CREATE TABLE calendar_dates (service_id TEXT, date TEXT, kind INTEGER)',
    'CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, shape_id TEXT, headsign TEXT, journey_code TEXT, block_id TEXT)',
    'CREATE TABLE trip_stops (trip_id TEXT, part INTEGER, data BLOB)',
    'CREATE TABLE trip_ends (trip_id TEXT PRIMARY KEY, first_stop TEXT, first_dep INTEGER, last_stop TEXT, last_arr INTEGER, stops INTEGER)',
    'CREATE TABLE shapes (shape_id TEXT, part INTEGER, data BLOB)',
)
INDEXES = (
    'CREATE INDEX trip_ends_start ON trip_ends (first_stop, first_dep)',
    'CREATE INDEX trip_stops_trip ON trip_stops (trip_id, part)',
    'CREATE INDEX shapes_id ON shapes (shape_id, part)',
    'CREATE INDEX calendar_dates_sid ON calendar_dates (service_id, date)',
)


def _table(zf, name, *cols):
    """Fast reader for the two huge files (stop_times, shapes): yields lists holding just the wanted
    columns (in the order asked; '' where the file has no such column). About twice as fast as dicts."""
    member = next((m for m in zf.namelist() if m.rsplit('/', 1)[-1].lower() == name), None)
    if member is None:
        return
    with zf.open(member) as raw:
        r = csv.reader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
        head = next(r, [])
        idx = [head.index(c) if c in head else None for c in cols]
        if all(i is not None for i in idx):
            get = __import__('operator').itemgetter(*idx)
            for row in r:
                try:
                    yield get(row)
                except IndexError:
                    continue
        else:
            for row in r:
                yield tuple(row[i] if i is not None and i < len(row) else '' for i in idx)


def _rows(zf, name):
    """Rows of one CSV file inside the zip, as dicts, read a little at a time."""
    member = next((m for m in zf.namelist() if m.rsplit('/', 1)[-1].lower() == name), None)
    if member is None:
        return
    with zf.open(member) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))


class Builder:
    def __init__(self, out_path, status=lambda **kw: None):
        self.out, self.tmp, self.status = out_path, out_path + '.building', status
        if os.path.exists(self.tmp):
            os.remove(self.tmp)
        self.db = sqlite3.connect(self.tmp)
        for q in ('PRAGMA journal_mode=OFF', 'PRAGMA synchronous=OFF', 'PRAGMA temp_store=FILE', 'PRAGMA cache_size=-8000') + SCHEMA:
            self.db.execute(q)
        self.counts, self.last = {}, 0.0

    def tell(self, **kw):
        if time.time() - self.last >= 5:
            self.last = time.time()
            self.status(state='building', **kw)

    def insert(self, sql, rows, label):
        batch, n = [], 0
        for row in rows:
            batch.append(row)
            if len(batch) >= BATCH:
                self.db.executemany(sql, batch); n += len(batch); batch = []
                self.tell(step=label, rows=n)
        if batch:
            self.db.executemany(sql, batch); n += len(batch)
        self.db.commit()
        self.counts[label] = self.counts.get(label, 0) + n
        return n

    def naptan(self, csv_path):
        def rows():
            with open(csv_path, encoding='utf-8-sig', newline='') as f:
                for r in csv.DictReader(f):
                    if (r.get('Status') or 'active').strip().lower() not in ('active', 'act', ''):
                        continue
                    try:
                        lat, lon = float(r.get('Latitude') or 'x'), float(r.get('Longitude') or 'x')
                    except ValueError:
                        continue
                    if not (49 < lat < 61.5 and -9 < lon < 2.5):
                        continue
                    yield ((r.get('ATCOCode') or '').strip(), lat, lon, (r.get('CommonName') or '').strip()[:80],
                           (r.get('Indicator') or '').strip()[:30], (r.get('Bearing') or '').strip()[:3], 'naptan')
        return self.insert('INSERT OR IGNORE INTO stops VALUES (?,?,?,?,?,?,?)', (r for r in rows() if r[0]), 'stopsNaptan')

    def gtfs(self, zip_path):
        with zipfile.ZipFile(zip_path) as zf:
            def stops():
                for r in _rows(zf, 'stops.txt'):
                    try:
                        lat, lon = float(r.get('stop_lat') or 'x'), float(r.get('stop_lon') or 'x')
                    except ValueError:
                        continue
                    sid = (r.get('stop_id') or '').strip()
                    if sid:
                        yield sid, lat, lon, (r.get('stop_name') or '').strip()[:80], '', '', 'gtfs'
            self.insert('INSERT OR IGNORE INTO stops VALUES (?,?,?,?,?,?,?)', stops(), 'stopsOnlyInTimetable')
            self.insert('INSERT OR REPLACE INTO agency VALUES (?,?,?)',
                        ((r.get('agency_id', ''), (r.get('agency_name') or '')[:80], (r.get('agency_noc') or '')[:10])
                         for r in _rows(zf, 'agency.txt')), 'agencies')
            self.insert('INSERT OR REPLACE INTO routes VALUES (?,?,?)',
                        ((r.get('route_id', ''), r.get('agency_id', ''), (r.get('route_short_name') or '')[:12])
                         for r in _rows(zf, 'routes.txt')), 'routes')
            days = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
            self.insert('INSERT OR REPLACE INTO calendar VALUES (?,?,?,?)',
                        ((r.get('service_id', ''), ''.join('1' if (r.get(d) or '0').strip() == '1' else '0' for d in days),
                          r.get('start_date', ''), r.get('end_date', '')) for r in _rows(zf, 'calendar.txt')), 'services')
            self.insert('INSERT INTO calendar_dates VALUES (?,?,?)',
                        ((r.get('service_id', ''), r.get('date', ''), int(r.get('exception_type') or 0))
                         for r in _rows(zf, 'calendar_dates.txt')), 'serviceExceptions')
            self.insert('INSERT OR REPLACE INTO trips VALUES (?,?,?,?,?,?,?)',
                        ((r.get('trip_id', ''), r.get('route_id', ''), r.get('service_id', ''), r.get('shape_id', ''),
                          (r.get('trip_headsign') or '')[:80], (r.get('vehicle_journey_code') or '')[:30],
                          (r.get('block_id') or '')[:30]) for r in _rows(zf, 'trips.txt')), 'trips')
            self._stop_times(zf)
            self._shapes(zf)

    def _stop_times(self, zf):
        """stop_times.txt is tens of millions of rows. Each trip's stops are stored together, packed
        ("seq,stop_id,arrive,depart" lines, compressed). The file is normally grouped by trip; if a trip
        turns up again later it gets another part, joined up when read. Nothing per trip is kept in
        memory (millions of trips would not fit in the bus engine's allowance)."""
        self.db.execute('CREATE TABLE te_parts (trip_id TEXT, fseq INTEGER, fstop TEXT, fdep INTEGER, lseq INTEGER, lstop TEXT, larr INTEGER, n INTEGER)')
        part = [0]
        cur, buf = None, []

        def flush():
            if cur is None or not buf:
                return
            part[0] += 1
            first = min(buf); last = max(buf)
            yield ('S', cur, part[0], zlib.compress('\n'.join(','.join(map(str, x)) for x in buf).encode(), 6))
            yield ('E', cur, first[0], first[1], first[3], last[0], last[1], last[2], len(buf))

        times = {}                                    # '09:05:00' -> 32700 (few thousand different times)

        def secs(text):
            v = times.get(text, False)
            if v is False:
                v = gtfs_seconds(text)
                if len(times) < 200_000:
                    times[text] = v
            return v

        def packed():
            nonlocal cur, buf
            for tid, a, d, sid, sq in _table(zf, 'stop_times.txt', 'trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'):
                if tid != cur:
                    yield from flush()
                    cur, buf = tid, []
                try:
                    seq = int(sq)
                except ValueError:
                    continue
                arr, dep = secs(a), secs(d)
                if arr is None:
                    arr = dep
                if dep is None:
                    dep = arr
                if ',' in sid or ' ' in sid:
                    sid = sid.strip().replace(',', ' ')
                buf.append((seq, sid, -1 if arr is None else arr, -1 if dep is None else dep))
            yield from flush()

        batch_s, batch_e, n = [], [], 0
        for row in packed():
            (batch_s if row[0] == 'S' else batch_e).append(row[1:])
            if len(batch_s) >= BATCH // 4:
                self.db.executemany('INSERT INTO trip_stops VALUES (?,?,?)', batch_s)
                self.db.executemany('INSERT INTO te_parts VALUES (?,?,?,?,?,?,?,?)', batch_e)
                n += len(batch_s); batch_s, batch_e = [], []
                self.tell(step='journeys', rows=n)
        self.db.executemany('INSERT INTO trip_stops VALUES (?,?,?)', batch_s)
        self.db.executemany('INSERT INTO te_parts VALUES (?,?,?,?,?,?,?,?)', batch_e)
        self.db.commit()
        self.tell(step='journey ends')
        self.db.execute('CREATE INDEX te_trip ON te_parts (trip_id, fseq)')
        self.db.execute(
            'INSERT INTO trip_ends SELECT a.trip_id, '
            '(SELECT fstop FROM te_parts b WHERE b.trip_id = a.trip_id ORDER BY fseq LIMIT 1), '
            '(SELECT fdep FROM te_parts b WHERE b.trip_id = a.trip_id ORDER BY fseq LIMIT 1), '
            '(SELECT lstop FROM te_parts b WHERE b.trip_id = a.trip_id ORDER BY lseq DESC LIMIT 1), '
            '(SELECT larr FROM te_parts b WHERE b.trip_id = a.trip_id ORDER BY lseq DESC LIMIT 1), '
            'SUM(n) FROM te_parts a GROUP BY a.trip_id')
        self.db.execute('DROP TABLE te_parts')
        self.db.commit()
        self.counts['tripsWithStops'] = self.db.execute('SELECT COUNT(*) FROM trip_ends').fetchone()[0]
        self.counts['stopTimes'] = self.db.execute('SELECT COALESCE(SUM(stops), 0) FROM trip_ends').fetchone()[0]

    def _shapes(self, zf):
        """Route shapes: the line each route follows, packed as lon/lat in millionths of a degree."""
        part = [0]
        cur, buf = None, []

        def flush():
            if cur is None or not buf:
                return
            part[0] += 1
            buf.sort()
            yield cur, part[0], zlib.compress('\n'.join(f'{s},{x},{y}' for s, x, y in buf).encode(), 6)

        def packed():
            nonlocal cur, buf
            for sid, sq, lo, la in _table(zf, 'shapes.txt', 'shape_id', 'shape_pt_sequence', 'shape_pt_lon', 'shape_pt_lat'):
                if sid != cur:
                    yield from flush()
                    cur, buf = sid, []
                try:
                    buf.append((int(sq), round(float(lo) * 1e6), round(float(la) * 1e6)))
                except ValueError:
                    continue
            yield from flush()
        self.insert('INSERT INTO shapes VALUES (?,?,?)', packed(), 'shapeParts')
        self.counts['shapes'] = self.db.execute('SELECT COUNT(DISTINCT shape_id) FROM shapes').fetchone()[0]

    def finish(self, meta):
        if not self.counts.get('tripsWithStops'):
            self.db.close(); os.remove(self.tmp)
            raise ValueError('the timetable download contained no journeys')
        self.status(state='building', step='indexing')
        for q in INDEXES:
            self.db.execute(q)
        meta = dict(meta, format=FORMAT, builtAt=int(time.time()), counts=self.counts)
        self.db.executemany('INSERT INTO meta VALUES (?, ?)', ((k, json.dumps(v)) for k, v in meta.items()))
        self.db.commit(); self.db.close()
        os.replace(self.tmp, self.out)
        return self.counts


def fetch_and_build(data_dir, gtfs_url=GTFS_URL, naptan_url=NAPTAN_URL, status=lambda **kw: None, max_days=MAX_DAYS):
    """Download the timetables and stops (if timetable.sqlite is older than max_days) and build it.
    The downloads are deleted afterwards (the zip is ~1.3 GB). True if rebuilt."""
    os.makedirs(data_dir, exist_ok=True)
    out = os.path.join(data_dir, 'timetable.sqlite')
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < max_days * 86400:
        status(state='up-to-date')
        return False
    gz, nz = os.path.join(data_dir, 'timetable-gtfs.zip'), os.path.join(data_dir, 'naptan-stops.csv')
    try:
        naptan_ok = True
        try:
            download(naptan_url, nz, MAX_NAPTAN_BYTES, status, 'bus stops (NaPTAN)')
        except Exception as exc:          # the timetable's own stop list is used instead
            naptan_ok = False
            status(state='warning', warning=('bus stops: ' + type(exc).__name__ + ' ' + str(exc))[:120])
        download(gtfs_url, gz, MAX_GTFS_BYTES, status, 'bus timetables (BODS)')
        b = Builder(out, status)
        if naptan_ok:
            b.naptan(nz)
        b.gtfs(gz)
        counts = b.finish({'source': 'BODS timetables (GTFS) + NaPTAN', 'licence':
                           'Bus timetables and stops: Department for Transport, Open Government Licence v3.0',
                           'naptan': naptan_ok})
        status(state='built', **{k: v for k, v in counts.items() if k in ('tripsWithStops', 'stopsNaptan', 'shapes')})
        return True
    finally:
        for p in (gz, nz):
            if os.path.exists(p):
                os.remove(p)


# ---------------------------------------------------------------- reading (used by the engine)
class Timetable:
    """Read-only view of timetable.sqlite. Small, on-demand lookups only."""

    def __init__(self, path):
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
        self.db.execute('PRAGMA cache_size=-4000')      # ~4 MB of SQLite page cache at most
        self.meta = {k: json.loads(v) for k, v in self.db.execute('SELECT key, value FROM meta')}
        if self.meta.get('format') != FORMAT:
            raise ValueError('timetable format unknown - will rebuild')
        import threading
        self.lock = threading.Lock()
        self.runs = {}                                   # (service_id, date) -> bool, small

    def close(self):
        self.db.close()

    def runs_on(self, service_id, date):
        key = (service_id, date)
        if key in self.runs:
            return self.runs[key]
        kinds = [k for (k,) in self.db.execute('SELECT kind FROM calendar_dates WHERE service_id=? AND date=?', key)]
        if 1 in kinds:
            ok = True
        elif 2 in kinds:
            ok = False
        else:
            row = self.db.execute('SELECT days, start, finish FROM calendar WHERE service_id=?', (service_id,)).fetchone()
            ok = bool(row) and row[1] <= date <= row[2] and \
                row[0][datetime.strptime(date, '%Y%m%d').weekday()] == '1'
        if len(self.runs) > 20000:
            self.runs.clear()
        self.runs[key] = ok
        return ok

    def match(self, origin_ref, departure_iso, line='', destination_ref='', operator='', journey_ref=''):
        """The timetabled journeys a bus could be running, from the stop it started at and its scheduled
        departure time (sent with each BODS report), narrowed by route number, end stop, bus company
        (NOC) and the company's own journey code. One trip_id = matched; several = unsure (never
        guessed); [] = not found. A journey that started after midnight on the previous service day
        (GTFS '24:30:00') is found too. 0.7.1: when nothing leaves at exactly that time, a journey of the
        same route number leaving within 2 minutes is accepted if it is the only one."""
        when = service_day_and_seconds(departure_iso) if departure_iso else None
        if not origin_ref or when is None:
            return []
        date, secs = when
        prev = (datetime.strptime(date, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')

        def look(offsets):
            out = []
            with self.lock:
                for day, base in ((date, secs), (prev, secs + 86400)):
                    for tid, sid, short, last, noc, code in self.db.execute(
                            'SELECT e.trip_id, t.service_id, r.short_name, e.last_stop, a.noc, t.journey_code FROM trip_ends e '
                            'JOIN trips t ON t.trip_id = e.trip_id LEFT JOIN routes r ON r.route_id = t.route_id '
                            'LEFT JOIN agency a ON a.agency_id = r.agency_id '
                            'WHERE e.first_stop = ? AND e.first_dep BETWEEN ? AND ?', (origin_ref, base + offsets[0], base + offsets[1])):
                        if self.runs_on(sid, day):
                            out.append((tid, (short or '').lower(), last or '', (noc or '').upper(), code or ''))
            return out

        def narrow(cands):
            for k, want in ((1, (line or '').lower()), (2, destination_ref), (3, (operator or '').upper()), (4, journey_ref)):
                if len(cands) > 1 and want:
                    same = [c for c in cands if c[k] == want]
                    cands = same or cands
            return cands
        cands = narrow(look((0, 0)))
        if not cands and line:
            near = [c for c in look((-120, 120)) if c[1] == line.lower()]
            near = narrow(near)
            cands = near if len({c[0] for c in near}) == 1 else []
        return sorted({c[0] for c in cands})

    def stops_of(self, trip_id):
        """[(stop_id, lat, lon, name, indicator, arrive_s, depart_s)] in order."""
        with self.lock:
            rows = []
            for (data,) in self.db.execute('SELECT data FROM trip_stops WHERE trip_id=? ORDER BY part', (trip_id,)):
                for line in zlib.decompress(data).decode().split('\n'):
                    seq, sid, arr, dep = line.split(',')
                    rows.append((int(seq), sid, int(arr), int(dep)))
            rows.sort()
            out = []
            for _, sid, arr, dep in rows:
                s = self.db.execute('SELECT lat, lon, name, indicator FROM stops WHERE stop_id=?', (sid,)).fetchone()
                out.append((sid,) + (tuple(s) if s else (None, None, '', '')) + (arr, dep))
            return out

    def company_name(self, noc):
        """The bus company's name for a National Operator Code (from the timetable), or ''."""
        with self.lock:
            row = self.db.execute('SELECT name FROM agency WHERE noc=? LIMIT 1', ((noc or '').upper(),)).fetchone()
        return (row[0] or '') if row else ''

    def trip_info(self, trip_id):
        """(route number, bus company name) for one journey; '' where the timetable has none."""
        with self.lock:
            row = self.db.execute('SELECT r.short_name, a.name FROM trips t LEFT JOIN routes r ON r.route_id = t.route_id '
                                  'LEFT JOIN agency a ON a.agency_id = r.agency_id WHERE t.trip_id=?', (trip_id,)).fetchone()
        return (row[0] or '', row[1] or '') if row else ('', '')

    def shape_id_of(self, trip_id):
        """0.17.0: the route line's own id for a journey ('' if none). Many journeys share one line."""
        with self.lock:
            row = self.db.execute('SELECT shape_id FROM trips WHERE trip_id=?', (trip_id,)).fetchone()
        return (row[0] or '') if row else ''

    def leaving_near(self, origin_ref, departure_iso, minutes=40, limit=8):
        """0.18.1 (diagnostic): journeys leaving this stop within +-minutes of the time: [(route, HH:MM, company code)]."""
        when = service_day_and_seconds(departure_iso) if departure_iso else None
        if not origin_ref or when is None:
            return []
        date, secs = when
        out = []
        with self.lock:
            for tid, sid, short, noc, dep in self.db.execute(
                    'SELECT e.trip_id, t.service_id, r.short_name, a.noc, e.first_dep FROM trip_ends e JOIN trips t ON t.trip_id = e.trip_id '
                    'LEFT JOIN routes r ON r.route_id = t.route_id LEFT JOIN agency a ON a.agency_id = r.agency_id '
                    'WHERE e.first_stop = ? AND e.first_dep BETWEEN ? AND ? ORDER BY e.first_dep LIMIT ?',
                    (origin_ref, secs - 60 * minutes, secs + 60 * minutes, limit * 4)):
                if self.runs_on(sid, date):
                    out.append((short or '', f'{dep // 3600 % 24:02d}:{dep % 3600 // 60:02d}', noc or ''))
        return out[:limit]

    def stop_known(self, stop_id):
        with self.lock:
            return self.db.execute('SELECT 1 FROM stops WHERE stop_id=? LIMIT 1', (stop_id,)).fetchone() is not None

    def routes_of_company(self, noc, limit=5):
        with self.lock:
            return [r[0] for r in self.db.execute('SELECT DISTINCT r.short_name FROM routes r JOIN agency a ON a.agency_id = r.agency_id '
                                                  'WHERE a.noc = ? LIMIT ?', ((noc or '').upper(), limit))]

    def has_shape(self, shape_id):
        """0.18.0: whether the timetable really holds this route line (some journeys name one that is missing)."""
        with self.lock:
            return self.db.execute('SELECT 1 FROM shapes WHERE shape_id=? LIMIT 1', (shape_id,)).fetchone() is not None

    def shape_by_id(self, shape_id):
        """0.17.0: a route line [(lon, lat), ...] by its own id, [] if unknown."""
        with self.lock:
            pts = []
            for (data,) in self.db.execute('SELECT data FROM shapes WHERE shape_id=? ORDER BY part', (shape_id,)):
                pts += [tuple(int(v) for v in line.split(',')) for line in zlib.decompress(data).decode().split('\n')]
        pts.sort()
        return [(x / 1e6, y / 1e6) for _, x, y in pts]

    def shape_of(self, trip_id):
        """The route's line [(lon, lat), ...], or [] if the timetable has none for this journey."""
        with self.lock:
            row = self.db.execute('SELECT shape_id FROM trips WHERE trip_id=?', (trip_id,)).fetchone()
            if not row or not row[0]:
                return []
            pts = []
            for (data,) in self.db.execute('SELECT data FROM shapes WHERE shape_id=? ORDER BY part', (row[0],)):
                pts += [tuple(int(v) for v in line.split(',')) for line in zlib.decompress(data).decode().split('\n')]
            pts.sort()
            return [(x / 1e6, y / 1e6) for _, x, y in pts]


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'fetch':
        g = os.environ.get('TPA_BUS_TIMETABLE_TEST_URL', '').strip() or GTFS_URL
        n = os.environ.get('TPA_BUS_NAPTAN_TEST_URL', '').strip() or NAPTAN_URL
        for u in (g, n):
            if u not in (GTFS_URL, NAPTAN_URL) and not u.startswith('http://127.0.0.1:'):
                raise SystemExit('Test address must be 127.0.0.1')
        try:
            os.nice(19)
        except OSError:
            pass

        def report(**kw):
            print('TIMETABLE ' + json.dumps(kw), flush=True)
        try:
            fetch_and_build(sys.argv[2], g, n, report, float(os.environ.get('TPA_BUS_TIMETABLE_DAYS', MAX_DAYS)))
        except Exception as exc:   # one clear line for the engine; never a traceback wall
            report(state='error', error=(type(exc).__name__ + ': ' + str(exc))[:120])
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(2)
