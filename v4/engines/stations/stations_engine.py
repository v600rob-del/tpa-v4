"""TPA v4 STATIONS ENGINE 0.1 — separate process, railway stations and their live boards.

What it does (owner 12 Nov 2026 17:03: "live railway departure boards ... this definitely for me (a user) will make
this THE go to site"; choices: rail boards first, departures AND arrivals, calling points, station alerts, opened by
tapping a station or searching by name)
- Knows every National Rail station (name, three-letter code, position) from a list kept with this program
  (stations.json, from davwheat/uk-railway-stations, Open Database Licence; attribution below).
- /snapshot: the stations inside the map view (only when zoomed in), as still markers.
- /search?q=: stations by name or code, for the search box.
- /details?id=stations:YRK: that station's live board (departures, arrivals, platforms, expected times,
  cancellations, calling points, station alerts) from National Rail's Live Arrival and Departure Boards, through the
  Rail Data Marketplace. Each station's board is fetched once and shared with every viewer for BOARD_SECONDS.

Safety
- No key -> zero requests; the boards say they are not switched on yet. The address and key are read from the
  environment only (TPA_STATIONS_LDB_URL, TPA_STATIONS_LDB_KEY); the key is sent in a request header, never in
  logs, files or answers.
- A daily safety limit on board requests (TPA_STATIONS_DAILY_CAP), far below the Marketplace's fair-use limit.
- Listens on 127.0.0.1 only. Read-only. Standard library only.
"""
from __future__ import annotations

import html
import json
import os
import re
import ssl
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

ENGINE_VERSION = '0.1.0'
COMPONENT = 'stations'
CONTRACT = '1.0'
HERE = Path(__file__).resolve().parent
ATTRIBUTION = 'Live boards: National Rail Enquiries (Rail Data Marketplace) · Station list © David Wheatley, Trainline EU and contributors (ODbL)'

KEY = (os.environ.get('TPA_STATIONS_LDB_KEY') or '').strip()
BOARD_URL = (os.environ.get('TPA_STATIONS_LDB_URL') or '').strip()
if BOARD_URL and not re.fullmatch(r'https://[A-Za-z0-9.-]+(:\d{2,5})?/[A-Za-z0-9._~/{}-]*', BOARD_URL) \
        and not re.fullmatch(r'http://127\.0\.0\.1:\d{2,5}/[A-Za-z0-9._~/{}-]*', BOARD_URL):
    BOARD_URL = ''                    # anything odd: switched off rather than guessed
LIVE = bool(KEY and BOARD_URL)
ROWS = max(1, min(150, int(os.environ.get('TPA_STATIONS_ROWS', '10') or 10)))
BOARD_SECONDS = max(5, int(os.environ.get('TPA_STATIONS_BOARD_SECONDS', '45') or 45))   # one board shared this long
DAILY_CAP = max(0, int(os.environ.get('TPA_STATIONS_DAILY_CAP', '50000') or 50000))
SHOW_SPAN = 1.2          # degrees of latitude: stations appear from about county level inwards
MAX_PER_VIEW = 400
DATA = Path(os.environ.get('TPA_STATIONS_DATA_DIR') or (Path(tempfile.gettempdir()) / 'TPA' / 'stations-engine'))
USAGE_FILE = DATA / 'board-requests-today.json'

LOCK = threading.Lock()
STATS = {'boardRequests': 0, 'boardErrors': 0, 'consecutiveErrors': 0, 'lastError': '' if LIVE else 'no-key',
         'lastSuccessAt': 0.0, 'shared': 0}
BOARDS: dict = {}          # CRS -> (good until, board dict or None, note)
FETCHING: dict = {}        # CRS -> threading.Event (one fetch per station at a time)


def load_stations() -> tuple[dict, list]:
    raw = json.loads((HERE / 'stations.json').read_text(encoding='utf-8'))
    by, out = {}, []
    for crs, name, lat, lon in raw['stations']:
        s = {'crs': crs, 'name': name, 'lat': float(lat), 'lon': float(lon), 'low': name.lower()}
        by[crs] = s
        out.append(s)
    return by, out


STATIONS, ALL = load_stations()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


def today() -> str:
    return time.strftime('%Y-%m-%d', time.gmtime())


def load_usage() -> dict:
    try:
        d = json.loads(USAGE_FILE.read_text(encoding='utf-8'))
        if isinstance(d, dict) and isinstance(d.get('requests'), int) and isinstance(d.get('day'), str):
            return d
    except (OSError, ValueError):
        pass
    return {'day': today(), 'requests': 0}


USAGE = load_usage()


def take_request() -> bool:
    """Count one board request against today's safety limit; False when the limit is reached."""
    with LOCK:
        if USAGE['day'] != today():
            USAGE['day'], USAGE['requests'] = today(), 0
        if USAGE['requests'] >= DAILY_CAP:
            return False
        USAGE['requests'] += 1
        STATS['boardRequests'] += 1
        n = USAGE['requests']
    if n % 20 == 1:
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            tmp = USAGE_FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(USAGE), encoding='utf-8')
            tmp.replace(USAGE_FILE)
        except OSError:
            pass
    return True


# ---- reading the board ---------------------------------------------------------------------------------------
def g(d, *names):
    """A field by any of its spellings (the Marketplace's JSON uses camelCase; older copies used other cases)."""
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d:
            return d[n]
    low = {k.lower(): v for k, v in d.items() if isinstance(k, str)}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def txt(v, n: int) -> str:
    return re.sub(r'\s+', ' ', v).strip()[:n] if isinstance(v, str) else ''


def hhmm(v) -> str:
    v = txt(v, 8)
    return v if re.fullmatch(r'[0-2]\d:[0-5]\d', v) else ''


def places(lst) -> tuple[str, str]:
    names, vias = [], []
    for p in lst if isinstance(lst, list) else []:
        n = txt(g(p, 'locationName'), 60)
        if n:
            names.append(n)
            via = txt(g(p, 'via'), 60)
            if via:
                vias.append(via)
    return ' & '.join(names)[:80], ' · '.join(vias)[:60]


def calls(lst) -> list:
    out = []
    for block in lst if isinstance(lst, list) else []:
        for c in g(block, 'callingPoint') or []:
            name = txt(g(c, 'locationName'), 60)
            if not name:
                continue
            item = {'name': name, 'time': hhmm(g(c, 'st'))}
            et, at = txt(g(c, 'et'), 20), txt(g(c, 'at'), 20)
            item['expected'] = at or et
            if g(c, 'isCancelled') is True or et == 'Cancelled':
                item['expected'] = 'Cancelled'
            out.append(item)
        break                        # the first list is this train's own route (a train that splits has more)
    return out[:60]


def status_of(planned: str, expected: str, cancelled: bool) -> tuple[str, str]:
    """(words, state) for a board line, the way station screens say it."""
    if cancelled or expected == 'Cancelled':
        return 'Cancelled', 'cancelled'
    if expected in ('On time', '') and planned:
        return ('On time', 'ontime') if expected else ('', 'unknown')
    if expected == 'Delayed':
        return 'Delayed', 'late'
    if hhmm(expected):
        return 'Exp ' + expected, ('ontime' if expected == planned else 'late')
    return txt(expected, 20), 'unknown'


def board_from(raw: dict) -> dict:
    """The Marketplace's answer -> TPA's plain board (two tabs, alerts)."""
    deps, arrs = [], []
    for s in g(raw, 'trainServices') or []:
        if not isinstance(s, dict):
            continue
        cancelled = g(s, 'isCancelled') is True
        reason = txt(g(s, 'cancelReason'), 160) if cancelled else txt(g(s, 'delayReason'), 160)
        common = {'platform': txt(g(s, 'platform'), 4), 'operator': txt(g(s, 'operator'), 40)}
        if reason:
            common['reason'] = reason
        std, sta = hhmm(g(s, 'std')), hhmm(g(s, 'sta'))
        if std:
            place, via = places(g(s, 'destination'))
            words, state = status_of(std, txt(g(s, 'etd'), 20), cancelled)
            row = {'time': std, 'place': place or 'Destination not given', 'status': words, 'state': state, **common,
                   'calls': calls(g(s, 'subsequentCallingPoints'))}
            if via:
                row['via'] = via
            deps.append(row)
        if sta:
            place, via = places(g(s, 'origin'))
            words, state = status_of(sta, txt(g(s, 'eta'), 20), cancelled)
            row = {'time': sta, 'place': place or 'Start not given', 'status': words, 'state': state, **common,
                   'calls': calls(g(s, 'previousCallingPoints'))}
            arrs.append(row)
    alerts = []
    for m in g(raw, 'nrccMessages') or []:
        v = m if isinstance(m, str) else g(m, 'Value', 'value', 'message')
        if isinstance(v, str):
            plain = re.sub(r'(?is)<(script|style)\b.*?(</\1\s*>|$)', ' ', v)   # never the words inside a script
            plain = html.unescape(re.sub(r'<[^>]*>', ' ', plain))
            plain = txt(plain, 400)
            if plain and plain not in alerts:
                alerts.append(plain)
    board = {'tabs': [{'name': 'Departures', 'empty': 'No departures in the next two hours.', 'rows': deps[:20]},
                      {'name': 'Arrivals', 'empty': 'No arrivals in the next two hours.', 'rows': arrs[:20]}],
             'alerts': alerts[:5]}
    stamp = txt(g(raw, 'generatedAt'), 40)
    try:
        t = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        try:
            from zoneinfo import ZoneInfo
            t = t.astimezone(ZoneInfo('Europe/London'))
        except Exception:  # pragma: no cover
            pass
        board['updated'] = t.strftime('%H:%M')
    except ValueError:
        pass
    return board


def board_address(crs: str) -> str:
    base = BOARD_URL
    if '{crs}' in base or '{CRS}' in base:
        url = base.replace('{crs}', crs).replace('{CRS}', crs)
    else:
        url = base.rstrip('/') + '/' + quote(crs)
    return url + ('&' if '?' in url else '?') + f'numRows={ROWS}'


def fetch_board(crs: str) -> tuple[dict | None, str]:
    """(board, note). Never raises."""
    if not LIVE:
        return None, 'Live boards are not switched on yet.'
    if not take_request():
        return None, "Today's share of live board requests is used up. Back tomorrow."
    req = Request(board_address(crs), headers={'x-apikey': KEY, 'Accept': 'application/json',
                                               'User-Agent': 'TPA-stations-engine/' + ENGINE_VERSION})
    try:
        with urlopen(req, timeout=8, context=ssl.create_default_context()) as res:
            raw = res.read(3_000_001)
        if len(raw) > 3_000_000:
            raise ValueError('oversized')
        board = board_from(json.loads(raw))
        with LOCK:
            STATS['consecutiveErrors'] = 0
            STATS['lastError'] = ''
            STATS['lastSuccessAt'] = time.time()
        return board, ''
    except HTTPError as exc:
        code = f'http-{exc.code}'
    except (URLError, OSError, TimeoutError):
        code = 'no-answer'
    except ValueError:
        code = 'bad-answer'
    with LOCK:
        STATS['boardErrors'] += 1
        STATS['consecutiveErrors'] += 1
        STATS['lastError'] = code
    if code in ('http-401', 'http-403'):
        return None, 'The live board service did not accept our key.'
    return None, 'The live board is not answering right now. Try again in a minute.'


def get_board(crs: str) -> tuple[dict | None, str]:
    """This station's board, shared: at most one request per station per BOARD_SECONDS."""
    while True:
        now = time.time()
        with LOCK:
            hit = BOARDS.get(crs)
            if hit and hit[0] > now:
                STATS['shared'] += 1
                return hit[1], hit[2]
            wait = FETCHING.get(crs)
            if wait is None:
                FETCHING[crs] = threading.Event()
                break
        wait.wait(10)
    try:
        board, note = fetch_board(crs)
        keep = BOARD_SECONDS if board is not None or not LIVE else 15     # a failure is tried again sooner
        with LOCK:
            BOARDS[crs] = (time.time() + keep, board, note)
            if len(BOARDS) > 3000:
                for k in [k for k, v in BOARDS.items() if v[0] <= time.time()]:
                    del BOARDS[k]
        return board, note
    finally:
        with LOCK:
            FETCHING.pop(crs).set()


# ---- answers ---------------------------------------------------------------------------------------------------
def parse_view(query: str):
    q = {k: v[0] for k, v in parse_qs(query or '').items()}
    try:
        w, s, e, n = (float(q[k]) for k in ('west', 'south', 'east', 'north'))
    except (KeyError, ValueError):
        return None
    return (s, w, n, e) if -90 <= s < n <= 90 and -180 <= w < e <= 180 else None


def entity(st: dict, now: float) -> dict:
    return {'id': 'stations:' + st['crs'], 'label': st['name'], 'badge': st['crs'], 'variant': 'station',
            'quality': 'observed', 'observedAt': iso(now),
            'position': {'lon': st['lon'], 'lat': st['lat'], 'headingDeg': None}}


def snapshot(view=None) -> dict:
    now = time.time()
    ents, notice = [], ''
    if view is not None:
        s, w, n, e = view
        if n - s > SHOW_SPAN:
            notice = 'Zoom in to see railway stations'
        else:
            inside = [st for st in ALL if s <= st['lat'] <= n and w <= st['lon'] <= e]
            clat, clon = (s + n) / 2, (w + e) / 2
            inside.sort(key=lambda st: (st['lat'] - clat) ** 2 + (st['lon'] - clon) ** 2)
            ents = [entity(st, now) for st in inside[:MAX_PER_VIEW]]
    frame = {'component': COMPONENT, 'contractVersion': CONTRACT, 'health': 'ready',
             'observedAt': iso(now), 'receivedAt': iso(now), 'expiresAt': iso(now + 3600),
             'nextPollAfterMs': 300000, 'attribution': ATTRIBUTION, 'entities': ents}
    if notice:
        frame['notice'] = notice
    return frame


def search(q: str) -> dict:
    q = re.sub(r'\s+', ' ', q or '').strip().lower()[:60]
    if len(q) < 2:
        return {'results': []}
    exact = STATIONS.get(q.upper()) if len(q) == 3 and q.isalpha() else None
    starts, words, inside = [], [], []
    for st in ALL:
        if st is exact:
            continue
        if st['low'].startswith(q):
            starts.append(st)
        elif any(w.startswith(q) for w in re.split(r'[\s(/-]+', st['low'])):
            words.append(st)
        elif q in st['low']:
            inside.append(st)
    found = ([exact] if exact else []) + sorted(starts, key=lambda s: len(s['name'])) + words + inside
    return {'results': [{'id': 'stations:' + st['crs'], 'label': st['name'], 'badge': st['crs'],
                         'lat': st['lat'], 'lon': st['lon']} for st in found[:8]]}


def details(eid: str) -> dict:
    crs = eid[9:] if eid.startswith('stations:') else eid
    st = STATIONS.get(crs.upper()) if re.fullmatch(r'[A-Za-z]{3}', crs) else None
    if not st:
        return {'rows': []}
    board, note = get_board(st['crs'])
    out = {'id': eid, 'rows': [{'label': 'Station code', 'value': st['crs'], 'more': True},
                               {'label': 'Source', 'value': 'National Rail Enquiries live boards', 'more': True}]}
    if board is not None:
        out['board'] = board
    if note:
        out['note'] = note
    return out


def health() -> tuple[str, str]:
    if not LIVE:
        return 'ready', 'Station list ready · live boards not switched on yet'
    if STATS['consecutiveErrors'] >= 5:
        if STATS['lastError'] in ('http-401', 'http-403'):
            return 'stale', 'Live boards: the key was not accepted'
        return 'stale', 'Live boards not answering · retrying'
    return 'ready', ''


class Handler(BaseHTTPRequestHandler):
    server_version = 'TPA-Stations-Engine'
    sys_version = ''

    def json(self, status: int, obj: dict) -> None:
        raw = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.client_address[0] != '127.0.0.1':
            return self.json(403, {'error': 'local only'})
        split = urlsplit(self.path)
        path, q = split.path, {k: v[0] for k, v in parse_qs(split.query).items()}
        if path == '/status':
            h, notice = health()
            with LOCK:
                return self.json(200, {'component': COMPONENT, 'engine': 'tpa-stations-engine',
                                       'engineVersion': ENGINE_VERSION, 'contractVersion': CONTRACT, 'live': True,
                                       'health': h, 'notice': notice, 'stations': len(ALL), 'boardsSwitchedOn': LIVE,
                                       'lastError': STATS['lastError'],
                                       'lastSuccessAt': iso(STATS['lastSuccessAt']) if STATS['lastSuccessAt'] else None,
                                       'boardRequestsThisRun': STATS['boardRequests'], 'sharedAnswers': STATS['shared'],
                                       'boardRequestsToday': USAGE['requests'] if USAGE['day'] == today() else 0,
                                       'dailySafetyCap': DAILY_CAP, 'boardSeconds': BOARD_SECONDS})
        if path == '/snapshot':
            return self.json(200, snapshot(parse_view(split.query)))
        if path == '/search':
            return self.json(200, search(q.get('q', '')))
        if path == '/details':
            return self.json(200, details(q.get('id', '')[:140]))
        return self.json(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.json(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    port = int(os.environ.get('TPA_STATIONS_ENGINE_PORT', '8785'))
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        print('=' * 60, flush=True)
        print(f'TPA STATIONS ENGINE {ENGINE_VERSION} on http://127.0.0.1:{port}/ (this PC only)', flush=True)
        print(f'Stations known: {len(ALL)}', flush=True)
        print('Live boards: ' + ('switched on' if LIVE else 'NOT switched on (no key or address set)'), flush=True)
        print('=' * 60, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
