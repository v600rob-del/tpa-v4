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
import random
import html
import re
import signal
import ssl
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError, iterparse

ENGINE_VERSION = '0.12.1'
COMPONENT = 'bus'
CONTRACT = '1.0'
BODS_AREA_URL = 'https://data.bus-data.dft.gov.uk/api/v1/datafeed/'
BODS_BULK_URL = 'https://data.bus-data.dft.gov.uk/avl/download/bulk_archive'
ATTRIBUTION = "Bus data sourced via the Department for Transport's Bus Open Data Service (BODS)"

MODE = 'bulk' if os.environ.get('TPA_BUS_MODE', 'area').strip().lower() == 'bulk' else 'area'
# 0.8.12 (owner 3 Nov 19:56): the page asks every 10 s (5 s since 0.8.10; the 18:50 recording showed every track lasting
# 23 s or more after each answer once the viewer-clock problem was fixed in HQ 4.13.3)
# 0.8.10 (owner 3 Nov 17:14): every 15 s (was 30) - replayed tracks were running out before the next report arrived
POLL_SECONDS = max(10, int(os.environ.get('TPA_BUS_POLL_SECONDS', '15') or 15))
# 0.5.5 (owner 31 Oct 23:47: "waiting a long time for the bus level to even load, and then longer for
# movement"): on the server, keep downloading even when nobody is looking (as the train engine does), so
# positions and speeds are already up to date when someone opens the map. Every 60 s when unwatched.
ALWAYS_ON = os.environ.get('TPA_BUS_ALWAYS_ON', '0').strip() == '1'
IDLE_POLL_SECONDS = max(POLL_SECONDS, int(os.environ.get('TPA_BUS_IDLE_POLL_SECONDS', '60') or 60))
DEMAND_WINDOW = max(5, int(os.environ.get('TPA_BUS_DEMAND_SECONDS', '180') or 180))  # fetch only while viewed recently
STALE_AFTER = 5 * 60         # report older than this -> marked stale
DROP_AFTER = 30 * 60         # report older than this -> forgotten
# 0.8.3 (owner 1 Nov 20:25-20:26): a bus not heard from for over 10 minutes has almost always finished or
# switched its tracker off, so it is no longer drawn (it is still remembered, and drawn again if it reports)
SHOW_FOR = 10 * 60
FRAME_TTL = 90               # HQ treats a frame as stale after this
MAX_DOWNLOAD = 32_000_000    # same safety size as v3
MAX_XML_BYTES = 600_000_000
MAX_PER_VIEW = 400           # keeps the map responsive on phones and tablets
REPLAY_MARGIN_DEG = 0.02      # 0.8.2: ~2 km - how far a bus can get beyond the view while still shown inside it
REPLAY_LOOKBACK = 120         # 0.8.2: seconds of reports before the replayed moment that may still be drawn
MAX_VIEW_SPAN = 1.6          # degrees of latitude; wider views ask the user to zoom in
AREA_PADDING = 0.15          # degrees added around the viewed area in "area" mode
MAX_AREA_SPAN = 3.0          # never ask BODS for an area bigger than this (degrees)
GB = (49.8, -8.7, 61.0, 2.0)  # south, west, north, east
GLIDE_SECONDS = 90           # how long a bus is carried forward after its last report
STEADY_SECONDS = 60          # ...at steady speed, then easing to a stop (HQ "slowToStop")
ROAD_MODE = os.environ.get('TPA_BUS_ROAD_MAP', 'off').strip().lower()      # 'download' on the server
TIMETABLE_MODE = os.environ.get('TPA_BUS_TIMETABLE', 'off').strip().lower()     # 'download' on the server
DATA_DIR = Path(os.environ.get('TPA_BUS_DATA_DIR', '') or Path(__file__).resolve().parent / 'data')
ROAD_CHECK_DAYS = 1          # daily look: OS asks whether a new edition exists (tiny); OpenStreetMap is rebuilt when 45 days old
# 0.5.0 (owner 31 Oct 20:27: "the map detail doesnt match where the buses are driving"): with
# TPA_BUS_ROAD_SOURCE=osm (the server) buses follow OpenStreetMap's roads - the same data the map
# is drawn from - built from Geofabrik's free road files. The Ordnance Survey map stays as backup.
ROAD_SOURCE = 'osm' if os.environ.get('TPA_BUS_ROAD_SOURCE', 'os').strip().lower() == 'osm' else 'os'
ROAD_RETRY_HOURS = 6
ROAD_VIEW_SPAN = 0.12        # follow roads only when zoomed in to town/city level (view under ~13 km tall);
                             # further out a bus is a dot and the straight glide looks the same (0.4.1)
INLINE_BUDGET = 0.25
NEW_AREA_BUDGET = 1.0        # 0.5.4: first look at a new area
# 0.5.6 (owner 1 Nov 00:18: "this needs to LOOK right"): road positions are also prepared for areas
# looked at from further out (a whole city), so zooming in finds every bus already on its road.
PREP_VIEW_SPAN = 0.6         # views up to about 65 km tall
PREP_MAX = 900               # buses prepared per download, nearest the middle of what's being looked at first         # seconds a snapshot may spend working out roads; the rest wait for the helper thread

KEY = (os.environ.get('TPA_BODS_API_KEY') or '').strip()
_test_area = os.environ.get('TPA_BUS_TEST_AREA_URL', '').strip()
_test_bulk = os.environ.get('TPA_BUS_TEST_BULK_URL', '').strip()
_test_roads = os.environ.get('TPA_BUS_ROADS_TEST_API', '').strip()
_test_osm = os.environ.get('TPA_BUS_OSM_TEST_INDEX', '').strip()
_test_tt = os.environ.get('TPA_BUS_TIMETABLE_TEST_URL', '').strip()
_test_naptan = os.environ.get('TPA_BUS_NAPTAN_TEST_URL', '').strip()
for _u in (_test_area, _test_bulk, _test_roads, _test_osm, _test_tt, _test_naptan):
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
    'timetable': 'off' if TIMETABLE_MODE != 'download' else 'starting', 'timetableDetail': '', 'timetableError': '',
}
WAKE = threading.Event()

_spec = importlib.util.spec_from_file_location('tpa_bus_road_map', Path(__file__).resolve().parent / 'road_map.py')
road_map = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(road_map)
_spec = importlib.util.spec_from_file_location('tpa_bus_timetable', Path(__file__).resolve().parent / 'timetable.py')
timetable = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(timetable)
# 0.6.0 (owner 1 Nov 09:57, step 1 of "stops and timetables"): bus stops (NaPTAN) and timetables (BODS)
# are downloaded and kept on disk; the engine only counts how many buses it can match. Nothing on the map changes.
TIMETABLE = {'tt': None}
MATCH_EVERY = max(1, int(os.environ.get('TPA_BUS_MATCH_SECONDS', '300') or 300))
MATCH_SAMPLE = 400
_spec = importlib.util.spec_from_file_location('tpa_bus_replay', Path(__file__).resolve().parent / 'replay.py')
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)
_spec = importlib.util.spec_from_file_location('tpa_bus_fleet_lists', Path(__file__).resolve().parent / 'fleet_lists.py')
fleet_lists = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fleet_lists)
# 0.11.0 (owner 10 Nov 13:33-13:36: "Proper identification for bus types"): published fleet lists (fleet_lists.py).
# TfL's is on; East Yorkshire's waits for their permission (TPA_BUS_FLEET_LISTS=tfl,ey).
FLEET_LISTS = [n for n in (os.environ.get('TPA_BUS_FLEET_LISTS', 'tfl') or '').replace(' ', '').lower().split(',')
               if n in fleet_lists.SOURCES]
FLEET_STATUS: dict = {}
# 0.7.0 (owner 1 Nov 11:37: "option 1. remember the goal is about the user experience"): buses matched
# to their timetabled journey are replayed REPLAY_SECONDS behind real time along their real route,
# between the positions they really reported (replay.py). Unmatched buses move as before.
REPLAY_SECONDS = max(0, int(os.environ.get('TPA_BUS_REPLAY_SECONDS', '90') or 0))
TRIPS: dict = {}             # bus key -> (journey details, trip_id or None)
FOLLOW: dict = {}            # bus key -> (trip_id, [(report time, metres along route)]) or (trip_id, None) = off route
SHAPES = OrderedDict()       # trip_id -> replay.Shape or None (no shape in the timetable); newest last
SHAPES_KEEP = 1500
JOURNEY_WAKE = threading.Event()
JOURNEY_LOCK = threading.RLock()   # TRIPS, FOLLOW and SHAPES (worker thread and page requests)
JOURNEY_MAX = 900            # buses worked on per round, nearest what people are looking at first
# 0.7.1 (owner 1 Nov 12:36 "yes": all buses on one clock): buses not matched to a timetable are replayed
# the same way, along the road map's shortest drive between each pair of their reports (or shown
# fading across when no drive fits), so no bus on screen is guessed any more.
LEGS: dict = {}              # bus key -> {report time a: (report time b, way)}
LEG_MAX_M = 3000.0
ROADS = {'map': None, 'licence': '', 'source': ''}
PATHS: dict = {}             # bus key -> (report time, motion or None)
PLACED: dict = {}            # 0.5.7: bus key -> (report time, [lon, lat] on its road, or None)
KEEP_LEFT_M = 2.0            # 0.5.7: buses drawn on the left-hand side of two-way roads (UK traffic keeps left)
PLACE_M = 20.0               # 0.5.7: a bus reported this close to a road is drawn on it (GPS is often 5-20 m out)
PENDING: deque = deque()
PENDING_SET: set = set()
PATH_WAKE = threading.Event()
# 0.4.1 (server log 30 Oct: downloads took 5 s normally, 17-42 s and timed out while road work ran):
# the BODS download always comes first. Road work waits while it runs, and a map rebuild is paused.
FETCHING = threading.Event()
BUILDER = {'proc': None, 'tt': None}     # road map / timetable builders (paused during each bus download)
TIMING = {'fetch': None, 'pathMs': None}
VIEWS: list = []             # (time, view) recently looked at closely enough for roads (0.4.2)
# 0.5.8 (owner 1 Nov 01:21: after an update buses took ~2 minutes to move again, "instead of waiting"):
# recent reports and each bus's movement history are kept on disk and reloaded at start, like the
# train engine does. Saved when the engine is stopped (every update restarts it) and every few minutes.
MEMORY_FILE = DATA_DIR / 'buses-last-reports.json'
SAVE_EVERY = max(1, int(os.environ.get('TPA_BUS_SAVE_SECONDS', '300') or 300))
SAVED = {'at': 0.0}
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


_CTRL = re.compile(r'[\x00-\x1f\x7f]+')


def clean(text: str, limit: int) -> str:
    if not text:
        return ''
    return (text if text.isprintable() else _CTRL.sub(' ', text)).strip()[:limit]


FIELDS = ('Latitude', 'Longitude', 'LineRef', 'PublishedLineName', 'VehicleRef', 'OperatorRef',
          'DestinationName', 'RecordedAtTime', 'Bearing') + ('DatedVehicleJourneyRef', 'OriginRef', 'DestinationRef',
          'OriginAimedDepartureTime', 'BlockRef')
# 0.5.9 (owner 1 Nov 09:05: "having more points of reference for a buses journey will give us higher
# accuracy"): count which buses carry the details needed to find their timetabled journey (bit flags in
# 'jd'). Only counted for now - nothing is shown or changed.
JOURNEY_BITS = {'DatedVehicleJourneyRef': 1, 'OriginRef': 2, 'DestinationRef': 4, 'OriginAimedDepartureTime': 8, 'BlockRef': 16}


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


def merge_twins(buses: list) -> list:
    """0.8.11 (owner 3 Nov, north Hull): some companies send each bus twice - as "SCEM-80296" and "80296" for operator
    SCEM. They are one bus: keep only its newest report, under the plain number."""
    best = {}
    for v in buses:
        op, _, veh = v['key'].partition(':')
        if veh.startswith(op + '-') and len(veh) > len(op) + 1:
            v = dict(v, key=f'{op}:{veh[len(op) + 1:]}')
        k = v['key']
        if k not in best or v['recorded'] > best[k]['recorded']:
            best[k] = v
    return list(best.values()) if len(best) < len(buses) else buses


def parse_siri_xml(stream) -> list[dict]:
    """Stream-parse SIRI-VM XML without loading the whole file into memory (the careful reader; until 0.8.6 the
    only one - now kept as the reference the fast reader is tested against)."""
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
                    'destination': clean(vals.get('DestinationName', ''), 80).replace('_', ' '), 'recorded': when,
                    'jd': sum(bit for name, bit in JOURNEY_BITS.items() if vals.get(name)),
                    'origin': clean(vals.get('OriginRef', ''), 20), 'dep': clean(vals.get('OriginAimedDepartureTime', ''), 32),
                    'dest': clean(vals.get('DestinationRef', ''), 20), 'jref': clean(vals.get('DatedVehicleJourneyRef', ''), 30)})
    return merge_twins(out)


# 0.8.6 (owner 3 Nov 13:57, processor meter: reading the national bus file every 30 s was two-thirds of this
# engine's work): a lean reader that scans the text for the 14 fields used, instead of building every XML element.
# Same results as parse_siri_xml (tested side by side), same protection against DOCTYPE/ENTITY tricks (GuardedStream),
# read in 1 MB pieces so memory stays small.
_ACT_END = re.compile(rb'</(?:[A-Za-z_][\w.-]*:)?VehicleActivity\s*>')
_ACT_START = re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?VehicleActivity(?=[\s>/])')
_FIELD = re.compile(rb'<(?:[A-Za-z_][\w.-]*:)?(' + b'|'.join(f.encode() for f in FIELDS) + rb')(?=[\s>])[^>]*(?<!/)>([^<]*)<')


_FIELD_TAGS: dict = {}


def _field_tags(prefix: bytes):
    tags = _FIELD_TAGS.get(prefix)
    if tags is None:
        tags = _FIELD_TAGS[prefix] = [(f, b'<' + prefix + f.encode() + b'>', b'<' + prefix + f.encode()) for f in FIELDS]
    return tags


def _raw_field(block: bytes, tag: bytes) -> bytes:
    i = block.find(tag)
    if i < 0:
        return b''
    j = i + len(tag)
    return block[j:block.find(b'<', j)]


_LAST_READ: dict = {}         # (operator, vehicle, time) as written in the file -> the bus as read last time
_THIS_READ: dict = {}


def _activity(block: bytes, out: list, seen: set, prefix: bytes = b'') -> None:
    # unchanged since the last download (same bus, same report time): the bus as read last time
    p = b'<' + prefix
    same = (_raw_field(block, p + b'OperatorRef>'), _raw_field(block, p + b'VehicleRef>'), _raw_field(block, p + b'RecordedAtTime>'))
    if same[1] and same[2]:
        prev = _LAST_READ.get(same)
        if prev is not None:
            _THIS_READ[same] = prev
            if prev['key'] not in seen:
                seen.add(prev['key'])
                out.append(dict(prev))
            return
    vals = {}
    for name, tag, opener in _field_tags(prefix):
        i = block.find(tag)
        if i >= 0:
            j = i + len(tag)
            text = block[j:block.find(b'<', j)].strip()
        else:
            if block.find(opener) < 0:
                continue                      # not in this bus's report
            m = next((m for m in _FIELD.finditer(block) if m.group(1).decode() == name), None)   # a tag with attributes (rare)
            if m is None:
                continue
            text = m.group(2).strip()
        if text:
            vals[name] = html.unescape(text.decode('utf-8', 'replace')) if b'&' in text else text.decode('utf-8', 'replace')
    try:
        lat, lon = float(vals.get('Latitude', 'nan')), float(vals.get('Longitude', 'nan'))
    except ValueError:
        return
    if not (GB[0] <= lat <= GB[2] and GB[1] <= lon <= GB[3]):
        return
    when = parse_time(vals.get('RecordedAtTime', ''))
    operator, vehicle = clean(vals.get('OperatorRef', ''), 30), clean(vals.get('VehicleRef', ''), 60)
    if when is None or not vehicle:
        return
    key = f'{operator}:{vehicle}'
    if key in seen:
        return
    seen.add(key)
    bearing = None
    try:
        b = float(vals.get('Bearing', ''))
        if math.isfinite(b) and 0 <= b <= 360:
            bearing = round(b, 1)
    except ValueError:
        pass
    v = {'key': key, 'lat': round(lat, 6), 'lon': round(lon, 6), 'bearing': bearing,
         'route': clean(vals.get('PublishedLineName') or vals.get('LineRef') or '', 12),
         'destination': clean(vals.get('DestinationName', ''), 80).replace('_', ' '), 'recorded': when,
         'jd': sum(bit for name, bit in JOURNEY_BITS.items() if vals.get(name)),
         'origin': clean(vals.get('OriginRef', ''), 20), 'dep': clean(vals.get('OriginAimedDepartureTime', ''), 32),
         'dest': clean(vals.get('DestinationRef', ''), 20), 'jref': clean(vals.get('DatedVehicleJourneyRef', ''), 30)}
    out.append(v)
    if same[1] and same[2]:
        _THIS_READ[same] = dict(v)


def parse_siri(stream) -> list[dict]:
    """The bus file, read the lean way (0.8.6). Malformed files give what can be read, like a cut-short download."""
    global _LAST_READ, _THIS_READ
    reader = io.BufferedReader(GuardedStream(stream))
    out, seen, rest, any_xml = [], set(), b'', False
    _THIS_READ = {}
    while True:
        chunk = reader.read(1 << 20)
        data = rest + chunk
        any_xml = any_xml or b'<' in data
        parts = _ACT_END.split(data)
        rest = parts.pop()                    # not finished yet: carried on to the next piece
        for part in parts:
            k = part.rfind(b'VehicleActivity')
            if k < 1:
                continue
            m = _ACT_START.match(part, part.rfind(b'<', 0, k))
            if m:
                lt = m.start() + 1
                _activity(part[m.end():], out, seen, part[lt:k])
        if not chunk:
            break
        if len(rest) > 8 << 20:              # no VehicleActivity end for 8 MB: not a bus file
            raise ValueError('bad-xml')
    if not any_xml:
        raise ValueError('bad-xml')
    _LAST_READ = _THIS_READ               # only buses in this file are kept for next time
    return merge_twins(out)


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


MEASURE_KEEP = 3000          # 0.5.9: recent guesses kept for the accuracy figures
MEASURE = {'errors': [], 'gaps': [], 'offsets': []}


def _point_along(points, metres_to_go):
    """The point `metres_to_go` along a [lon, lat] path (its end if the path is shorter)."""
    for a, b in zip(points, points[1:]):
        d = metres(a[1], a[0], b[1], b[0])
        if d >= metres_to_go and d > 0:
            f = metres_to_go / d
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        metres_to_go -= d
    return points[-1][0], points[-1][1]


def _glide_metres(speed, seconds):
    """How far HQ carries a bus `seconds` after its report: steady, then easing to a stop ("slowToStop")."""
    ease = GLIDE_SECONDS - STEADY_SECONDS
    t = max(0.0, min(seconds, GLIDE_SECONDS))
    if t <= STEADY_SECONDS:
        return speed * t
    tau = t - STEADY_SECONDS
    return speed * (STEADY_SECONDS + tau - tau * tau / (2 * ease))


def measure_guesses(new: list, old: list) -> None:
    """0.5.9 (owner 1 Nov 09:14: "smooth movement for short periods then pauses, often followed by speedy
    movements"): when a bus's next report arrives, how far from it was the position we were showing
    for that moment? Ahead = we had it further on than it really was. Also how often each bus reports,
    and how far its reported position was from the road we drew it on. Counted only; nothing changes."""
    before = {v['key']: v for v in old}
    for v in new:
        p = before.get(v['key'])
        if p is None or v['recorded'] <= p['recorded']:
            continue
        dt = v['recorded'] - p['recorded']
        MEASURE['gaps'].append(dt)
        if dt > 120 or not p.get('speed') or p.get('bearing') is None:
            continue
        known = PATHS.get(p['key'])
        motion = known[1] if known and known[0] == p['recorded'] else None
        go = _glide_metres(p['speed'], dt)
        if motion and motion.get('mode') == 'path' and len(motion['points']) >= 2:
            start = motion['points'][0]
            MEASURE['offsets'].append(metres(p['lat'], p['lon'], start[1], start[0]))
            lon, lat = _point_along(motion['points'], go)
            way = 'road'
        else:
            b = math.radians(p['bearing'])
            lat = p['lat'] + go * math.cos(b) / 111_320
            lon = p['lon'] + go * math.sin(b) / (111_320 * max(0.2, math.cos(math.radians(p['lat']))))
            way = 'straight'
        err = metres(lat, lon, v['lat'], v['lon'])
        # ahead or behind: compare distance from the old report along the way the bus went
        ahead = metres(p['lat'], p['lon'], lat, lon) > metres(p['lat'], p['lon'], v['lat'], v['lon'])
        MEASURE['errors'].append((err, ahead, way))
    for k in MEASURE:
        del MEASURE[k][:-MEASURE_KEEP]


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))])


def measure_summary(vehicles: list, now: float) -> dict:
    """0.5.9: the figures shown by tpa-status as 'measure' (and on /status)."""
    errs = MEASURE['errors']
    fresh = [v for v in vehicles if now - v['recorded'] <= DROP_AFTER]
    jd = [v.get('jd', 0) for v in fresh]
    share = (lambda bits: round(100 * sum(1 for x in jd if x & bits == bits) / len(jd)) if jd else None)
    return {'guesses': len(errs),
            'guessOutMedianM': _pct([e[0] for e in errs], 0.5), 'guessOut90pcM': _pct([e[0] for e in errs], 0.9),
            'guessAheadPct': round(100 * sum(1 for e in errs if e[1]) / len(errs)) if errs else None,
            'guessOnRoadPct': round(100 * sum(1 for e in errs if e[2] == 'road') / len(errs)) if errs else None,
            'reportGapMedianS': _pct(MEASURE['gaps'], 0.5), 'reportGap90pcS': _pct(MEASURE['gaps'], 0.9),
            'roadOffsetMedianM': _pct(MEASURE['offsets'], 0.5), 'roadOffset90pcM': _pct(MEASURE['offsets'], 0.9),
            'busesFresh': len(fresh),
            'journeyRefPct': share(1), 'originRefPct': share(2), 'destinationRefPct': share(4),
            'originDeparturePct': share(8), 'blockRefPct': share(16), 'timetableMatchablePct': share(2 | 8)}


_PAUSED: list = []


def busy_reading() -> None:
    """0.8.9 (owner 3 Nov 16:40: all buses faded, then sped on - BODS downloads were slow and timing out): the rest
    of the engine is paused only while a download is being READ (processor work), not while waiting for BODS to
    answer (no processor needed). Before, a slow BODS kept the engine "busy" nearly all the time, buses' new
    reports were not placed on their routes, and every bus fell back to the old way of moving."""
    if FETCHING.is_set():
        return
    FETCHING.set()
    for proc in (BUILDER['proc'], BUILDER['tt']):
        if proc is not None and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGSTOP); _PAUSED.append(proc)
            except OSError:
                pass


def fetch_once(area) -> None:
    """One BODS download; everything else in this engine is paused while it is read, so that gets the processor."""
    paused = _PAUSED
    started = time.time()
    try:
        _fetch_once(area)
    finally:
        while paused:
            proc = paused.pop()
            try:
                os.kill(proc.pid, signal.SIGCONT)
            except OSError:
                pass
        FETCHING.clear()
        TIMING['fetch'] = round(time.time() - started, 1)
        prepare_paths()
        PATH_WAKE.set()
        JOURNEY_WAKE.set()
        save_memory()


def save_memory(force: bool = False) -> bool:
    """0.5.8: keep the last reports (up to 30 minutes old) and movement history on disk."""
    now = time.time()
    if not force and now - SAVED['at'] < SAVE_EVERY:
        return False
    with LOCK:
        if not STATE['fetchedAt']:
            return False
        vehicles = [v for v in STATE['vehicles'] if now - v['recorded'] <= DROP_AFTER]
        live = {v['key'] for v in vehicles}
        history = {k: [list(r) for r in h] for k, h in HISTORY.items() if k in live}
        body = {'savedAt': now, 'engineVersion': ENGINE_VERSION, 'mode': MODE, 'fetchedAt': STATE['fetchedAt'],
                'coverage': STATE['coverage'], 'vehicles': vehicles, 'history': history}
    SAVED['at'] = now
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(body, separators=(',', ':')), encoding='utf-8')
        tmp.replace(MEMORY_FILE)
        return True
    except OSError as exc:
        print('Could not save bus memory:', type(exc).__name__, flush=True)
        return False


def load_memory() -> int:
    """0.5.8: reload the last reports at start; buses still recent enough are shown and keep moving."""
    try:
        saved = json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    now = time.time()
    if not isinstance(saved, dict) or saved.get('mode') != MODE or not isinstance(saved.get('vehicles'), list):
        return 0
    keep = [v for v in saved['vehicles'] if isinstance(v, dict) and isinstance(v.get('recorded'), (int, float))
            and now - v['recorded'] <= DROP_AFTER and isinstance(v.get('key'), str)]
    if not keep:
        return 0
    live = {v['key'] for v in keep}
    with LOCK:
        STATE.update(vehicles=keep, fetchedAt=float(saved.get('fetchedAt') or 0),
                     coverage=tuple(saved['coverage']) if saved.get('coverage') else None)
        HISTORY.clear()
        for k, h in (saved.get('history') or {}).items():
            if k in live and isinstance(h, list):
                HISTORY[k] = [tuple(r) for r in h if isinstance(r, list) and len(r) == 3][-HISTORY_KEEP:]
        STATE['busesWithSpeed'] = sum(1 for v in keep if v.get('speed'))
    print(f'Bus memory reloaded: {len(keep)} buses from {round(now - saved.get("savedAt", now))} s ago', flush=True)
    return len(keep)


def _stop(signum, frame):
    save_memory(force=True)
    raise SystemExit(0)


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
        # 0.5.4: a margin of half the view on every side for close-up views, so panning nearby lands on
        # buses whose road paths are already prepared. 0.5.6: wider views (a whole city) too, as they are.
        areas = [((s - (n - s) / 2, w - (e - w) / 2, n + (n - s) / 2, e + (e - w) / 2) if n - s <= ROAD_VIEW_SPAN else (s, w, n, e),
                  ((s + n) / 2, (w + e) / 2), n - s <= ROAD_VIEW_SPAN) for s, w, n, e in views]
        picks = []
        for v in STATE['vehicles']:
            moving = v.get('speed') and v.get('bearing') is not None and now - v['recorded'] <= STALE_AFTER
            if not moving and now - v['recorded'] > DROP_AFTER:
                continue
            best = None
            for (s, w, n, e), (cy, cx), close in areas:
                if s <= v['lat'] <= n and w <= v['lon'] <= e:
                    rank = (0 if close else 1, (v['lat'] - cy) ** 2 + (v['lon'] - cx) ** 2)
                    best = rank if best is None or rank < best else best
            if best is None:
                continue
            known = PATHS.get(v['key']) if moving else PLACED.get(v['key'])
            if known and known[0] == v['recorded']:
                continue
            if (v['key'], v['recorded']) not in PENDING_SET:
                picks.append((best, v))
        picks.sort(key=lambda p: p[0])                    # close-up views first, then nearest the middle
        chosen = [v for _, v in picks[:max(0, min(PREP_MAX, 3000 - len(PENDING)))]]
        # worked through area by area, so each piece of road map is loaded once
        tile = road_map.tile_of
        chosen.sort(key=lambda v: tile(round(v['lon'] * 1e6), round(v['lat'] * 1e6)))
        for v in chosen:
            PENDING_SET.add((v['key'], v['recorded']))
            PENDING.append(v)


def _fetch_once(area) -> None:
    """One BODS download. Errors keep the previous snapshot (shown with its true age)."""
    started = time.time()
    try:
        if MODE == 'bulk':
            raw = download(BULK_URL + '?' + urlencode({'api_key': KEY}))
            busy_reading()
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
            busy_reading()
            vehicles = parse_siri(io.BytesIO(raw))
            coverage = area
        ages = sorted(started - v['recorded'] for v in vehicles if v['recorded'] <= started)
        # 0.5.9: buses whose last report is over 30 minutes old are never shown, so they are not kept
        # (the national file holds ~16,000 of them on a Sunday morning: memory for nothing).
        in_file = len(vehicles)
        vehicles = [v for v in vehicles if started - v['recorded'] <= DROP_AFTER]
        # 0.8.6: worked out BEFORE taking the lock - it takes seconds on the server, and every viewer's answer
        # waits for the lock (the 6 s bus answers in the 3 Nov load tests)
        with LOCK:
            old = STATE['vehicles']
        add_motion(vehicles, old)
        measure_guesses(vehicles, old)
        with LOCK:
            STATE['busesInFile'] = in_file
            live = {v['key'] for v in vehicles}
            for k in [k for k in PATHS if k not in live]:
                del PATHS[k]
            for k in [k for k in PLACED if k not in live]:
                del PLACED[k]
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


def poll_decision(now: float):
    """(go, area): whether to download BODS now, and for which area (area mode only)."""
    with LOCK:
        wanted = STATE['wanted']
        watched = now - STATE['lastDemandAt'] < DEMAND_WINDOW
        viewed = watched or ALWAYS_ON
        pace = POLL_SECONDS if watched else IDLE_POLL_SECONDS
        # After repeated failures, back off gently (up to 5 minutes) rather than hammer BODS.
        wait = pace * min(10, 2 ** max(0, STATE['consecutiveErrors'] - 1)) if STATE['consecutiveErrors'] else pace
        due = now - STATE['lastAttemptAt'] >= wait
        uncovered = MODE == 'area' and wanted is not None and not covers(STATE['coverage'], wanted) \
            and now - STATE['lastAttemptAt'] >= 3  # a newly viewed area need not wait the full 30 s
        go = viewed and (due or uncovered) and (MODE == 'bulk' or wanted is not None)
        area = padded(wanted) if (go and MODE == 'area') else None
        if go:
            STATE['lastAttemptAt'] = now
    return go, area


def poller() -> None:
    while True:
        WAKE.wait(timeout=5)
        WAKE.clear()
        if not KEY:
            continue
        go, area = poll_decision(time.time())
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
             'nextPollAfterMs': 10000, 'attribution': ATTRIBUTION + (' · roads: ' + ROADS['licence'] if ROADS['licence'] else '')
             + (' · bus routes: BODS timetables, Department for Transport (OGL)' if REPLAY_SECONDS and TIMETABLE['tt'] is not None else ''),
             'entities': [],
             # 0.8.1 (owner 1 Nov 13:40): HQ shows buses stacked on top of each other as one "N buses" badge
             'groupStacks': True}
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
    # 0.8.2 (owner 1 Nov 20:02, hunting vanishing buses): a bus is drawn REPLAY_SECONDS behind real time, so
    # it is sent when the stretch it is being replayed along touches the view - not only when its NEWEST
    # report does. Before, a bus that had just driven out of the view in real life was left out while its
    # icon was still in the middle of the street being watched, and vanished.
    near_lat, near_lon = REPLAY_MARGIN_DEG, REPLAY_MARGIN_DEG / max(0.2, math.cos(math.radians(clat)))
    recent = now - REPLAY_SECONDS - REPLAY_LOOKBACK
    for v in vehicles:
        age = now - v['recorded']
        if age > SHOW_FOR:
            continue
        if not (south <= v['lat'] <= north and west <= v['lon'] <= east):
            if not (REPLAY_SECONDS and age <= STALE_AFTER and south - near_lat <= v['lat'] <= north + near_lat
                    and west - near_lon <= v['lon'] <= east + near_lon):
                continue
            hist = HISTORY.get(v['key']) or ()
            if not any(t >= recent and south <= la <= north and west <= lo <= east for t, la, lo in hist[-12:]):
                continue
        inside.append(((v['lat'] - clat) ** 2 + (v['lon'] - clon) ** 2, v, age))
    inside.sort(key=lambda t: t[0])  # nearest the middle of the screen first
    # 0.5.4: for an area nobody was looking at, take up to a second to work out road paths before
    # answering, rather than sending straight lines first and road paths a few seconds later
    with LOCK:
        known_area = any(s0 <= south and w0 <= west and n0 >= north and e0 >= east
                         for _, (s0, w0, n0, e0) in ((t, (s - (n - s) / 2, w - (e - w) / 2, n + (n - s) / 2, e + (e - w) / 2))
                                                     for t, (s, w, n, e) in VIEWS if now - t < VIEW_MEMORY))
    deadline = time.monotonic() + (INLINE_BUDGET if known_area else NEW_AREA_BUDGET)
    roads = north - south <= ROAD_VIEW_SPAN
    if north - south <= PREP_VIEW_SPAN:
        with LOCK:
            VIEWS.append((now, view))
            del VIEWS[:-50]
    why = {'road': 0, 'straight-pending': 0, 'straight-no-road-match': 0, 'straight-zoomed-out': 0, 'still-no-speed': 0,
           'placed-on-road': 0, 'replay': 0, 'replay-waiting': 0, 'replay-pending': 0, 'replay-roads': 0}
    if REPLAY_SECONDS and not known_area:
        JOURNEY_WAKE.set()
    for _, v, age in inside[:MAX_PER_VIEW]:
        route = v['route'] or 'Bus'
        label = (f'Bus {route}' if v['route'] else 'Bus') + (f' to {v["destination"]}' if v['destination'] else '')
        entity = {
            'id': ('bus:' + v['key'])[:128], 'label': label[:160],
            'badge': re.sub(r'[^A-Za-z0-9]', '', v['route'])[:8] or None,
            'position': {'lon': v['lon'], 'lat': v['lat'], 'headingDeg': v['bearing']},
            'quality': 'stale' if age > STALE_AFTER else 'observed', 'observedAt': iso(v['recorded'])}
        r = replay_for(v, now, deadline) if age <= STALE_AFTER else None
        if r is not None:
            # 0.7.0: replayed along its real route, REPLAY_SECONDS behind, between its real reports
            entity['position']['lon'], entity['position']['lat'] = round(r['position'][0], 6), round(r['position'][1], 6)
            entity['motion'] = {'mode': 'timed', 'points': [[round(a, 6), round(b, 6)] for a, b in r['points']],
                                'times': r['times'], 'delaySeconds': REPLAY_SECONDS}
            why['replay-waiting' if r['waiting'] else 'replay-roads' if r.get('road') else 'replay'] += 1
            frame['entities'].append(entity)
            continue
        if REPLAY_SECONDS and TIMETABLE['tt'] is not None and v.get('origin') and v.get('dep') and v['key'] not in TRIPS:
            why['replay-pending'] += 1          # not worked out yet: moved the old way until it is
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
        # 0.5.7: every bus near a road is drawn on it, on the left for its direction - moving or not
        m = entity.get('motion')
        if m and m['mode'] == 'path':
            entity['position']['lon'], entity['position']['lat'] = m['points'][0]
        elif roads and ROADS['map'] is not None:
            p = placed_for(v, deadline)
            if p:
                entity['position']['lon'], entity['position']['lat'] = p
                why['placed-on-road'] += 1
        frame['entities'].append(entity)
    with LOCK:
        STATE['lastView'] = why
    if not frame['entities'] and MODE == 'area' and not covers(coverage, view):
        frame['notice'] = 'Loading buses for this area…'
    elif len(inside) > MAX_PER_VIEW and not notice:
        frame['notice'] = f'Showing nearest {MAX_PER_VIEW} · zoom in for all'
    return frame


def best_road_file():
    """OpenStreetMap roads when chosen and built; otherwise the Ordnance Survey map (backup)."""
    osm, os_ = DATA_DIR / 'roads-osm.sqlite', DATA_DIR / 'roads.sqlite'
    if ROAD_SOURCE == 'osm' and osm.exists():
        return osm, 'OpenStreetMap'
    return (os_, 'Ordnance Survey') if os_.exists() else (None, '')


def open_road_map() -> bool:
    path, source = best_road_file()
    if path is None:
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
        ROADS['source'], ROADS['path'] = source, str(path)
        STATE['roadMap'] = 'ready'
        STATE['roadMapDetail'] = f"{source}: {new.meta.get('links', 0)} road pieces"
        PATHS.clear()
        PLACED.clear()
    if old:
        old.close()
    print('Road map ready:', source, STATE['roadMapDetail'], flush=True)
    return True


def refresh_road_map(kind: str = 'fetch') -> bool:
    """Run road_map.py fetch / fetch-osm as a low-priority child; True if it finished without error."""
    cmd = [sys.executable, str(Path(__file__).resolve().parent / 'road_map.py'), kind, str(DATA_DIR)]
    env = dict(os.environ)
    if _test_roads:
        env['TPA_BUS_ROADS_TEST_API'] = _test_roads
    if _test_osm:
        env['TPA_BUS_OSM_TEST_INDEX'] = _test_osm
    label = 'OpenStreetMap ' if kind == 'fetch-osm' else ''
    last_logged = None
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
                    detail = label + (f"{info['percent']}%" if 'percent' in info else '') + \
                        (f" {info['mb']}" if 'mb' in info else '') + \
                        (f" {info['linksRead']} roads read" if 'linksRead' in info else '') + (f" ({info['region']})" if 'region' in info else '')
                    if ROADS['map'] is None:
                        STATE['roadMap'] = st
                    STATE['roadMapDetail'] = (st + ' ' + detail).strip() if ROADS['map'] else detail
            if st in ('built', 'error') or (st, info.get('region')) != last_logged:
                last_logged = (st, info.get('region'))        # one log line per step and region
                print('Road map:', label + line[8:].strip(), flush=True)
    BUILDER['proc'] = None
    return ok and proc.returncode == 0


def road_keeper() -> None:
    open_road_map()
    if ROAD_MODE != 'download':
        return
    time.sleep(float(os.environ.get('TPA_BUS_ROAD_START_DELAY', '30')))   # let the server settle after a restart
    while True:
        ok = refresh_road_map('fetch')                    # Ordnance Survey: kept as the backup
        if ROAD_SOURCE == 'osm':
            ok = refresh_road_map('fetch-osm') and ok     # OpenStreetMap: what buses follow
        if ok:
            with LOCK:
                STATE['roadMapError'] = ''
        path, _ = best_road_file()
        cur = ROADS['map']
        if path is not None and (cur is None or ROADS.get('path') != str(path) or path.stat().st_mtime > cur.meta.get('builtAt', 0) + 60):
            open_road_map()
        with LOCK:
            if ROADS['map'] is not None:
                STATE['roadMap'] = 'ready'
                STATE['roadMapDetail'] = f"{ROADS['source']}: {ROADS['map'].meta.get('links', 0)} road pieces"
            elif not ok:
                STATE['roadMap'] = 'unavailable'
        time.sleep((ROAD_CHECK_DAYS * 86400) if ok else ROAD_RETRY_HOURS * 3600)


def open_timetable() -> bool:
    path = DATA_DIR / 'timetable.sqlite'
    if not path.exists():
        return False
    try:
        new = timetable.Timetable(str(path))
    except Exception as exc:   # damaged or old format: the next check rebuilds it
        with LOCK:
            STATE['timetableError'] = ('open: ' + type(exc).__name__ + ' ' + str(exc))[:100]
        return False
    old = TIMETABLE['tt']
    c = new.meta.get('counts', {})
    with LOCK:
        TIMETABLE['tt'] = new
        GROUP_MEMO.clear()          # 0.10.0: company names may have changed with the new timetable
        STATE['timetable'] = 'ready'
        STATE['timetableDetail'] = (f"{c.get('tripsWithStops', 0)} journeys, {c.get('stopsNaptan', 0) + c.get('stopsOnlyInTimetable', 0)} stops, "
                                    f"{c.get('shapes', 0)} route shapes, built {iso(new.meta.get('builtAt', 0))}")
    if old:
        old.close()
    print('Timetable ready:', STATE['timetableDetail'], flush=True)
    return True


def refresh_timetable() -> bool:
    """Run timetable.py fetch as a low-priority child; True if it finished without error."""
    cmd = [sys.executable, str(Path(__file__).resolve().parent / 'timetable.py'), 'fetch', str(DATA_DIR)]
    env = dict(os.environ)
    if _test_tt:
        env['TPA_BUS_TIMETABLE_TEST_URL'] = _test_tt
    if _test_naptan:
        env['TPA_BUS_NAPTAN_TEST_URL'] = _test_naptan
    ok, last_logged = True, None
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env) as proc:
        BUILDER['tt'] = proc
        for line in proc.stdout:
            if not line.startswith('TIMETABLE '):
                continue
            try:
                info = json.loads(line[10:])
            except ValueError:
                continue
            st = info.get('state', '')
            with LOCK:
                if st == 'error':
                    STATE['timetableError'] = str(info.get('error', ''))[:120]
                    ok = False
                elif st == 'warning':
                    STATE['timetableError'] = str(info.get('warning', ''))[:120]
                elif st in ('downloading', 'building'):
                    detail = ' '.join(str(x) for x in (st, info.get('what', ''), info.get('step', ''),
                                                        f"{info['percent']}%" if 'percent' in info else '',
                                                        f"{info['mb']} MB" if 'mb' in info else '',
                                                        f"{info['rows']} rows" if 'rows' in info else '') if x)
                    if TIMETABLE['tt'] is None:
                        STATE['timetable'] = st
                        STATE['timetableDetail'] = detail
                    else:
                        STATE['timetableUpdate'] = detail
            key = (st, info.get('what'), info.get('step'))
            if st in ('built', 'error', 'warning') or key != last_logged:
                last_logged = key
                print('Timetable:', line[10:].strip(), flush=True)
    BUILDER['tt'] = None
    with LOCK:
        STATE.pop('timetableUpdate', None)
    return ok and proc.returncode == 0


def timetable_keeper() -> None:
    open_timetable()
    if TIMETABLE_MODE != 'download':
        return
    time.sleep(float(os.environ.get('TPA_BUS_TIMETABLE_START_DELAY', '120')))   # let buses and roads settle first
    while True:
        if TIMETABLE['tt'] is not None:
            # 0.7.0 (1 Nov 11:06: the first build slowed the bus downloads from 6 s to 36 s and squeezed
            # the other layers): once there is a timetable, refreshes only happen in the night.
            time.sleep(seconds_until_night())
        ok = refresh_timetable()
        if ok:
            with LOCK:
                STATE['timetableError'] = ''
        path, cur = DATA_DIR / 'timetable.sqlite', TIMETABLE['tt']
        if path.exists() and (cur is None or path.stat().st_mtime > cur.meta.get('builtAt', 0) + 60):
            open_timetable()
        if TIMETABLE['tt'] is None and not ok:
            with LOCK:
                STATE['timetable'] = 'unavailable'
        if not ok:
            time.sleep(ROAD_RETRY_HOURS * 3600)


NIGHT_HOUR = 2               # UK time: the weekly timetable refresh starts at 02:00 (done long before morning)


def seconds_until_night(now: float | None = None) -> float:
    """Seconds until the next 02:00 UK time (at least 60)."""
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, timetable.LONDON)
    nxt = local.replace(hour=NIGHT_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= local:
        nxt = datetime.fromtimestamp(nxt.timestamp() + 86400 + 3600, timetable.LONDON).replace(hour=NIGHT_HOUR, minute=0, second=0, microsecond=0)
    return max(60.0, nxt.timestamp() - now)


def count_matches() -> dict | None:
    """0.6.0: how many buses (a sample of up to 400 with journey details) the timetable can match.
    Counted only. matched = exactly one timetabled journey; unsure = several (never guessed)."""
    tt = TIMETABLE['tt']
    if tt is None:
        return None
    now = time.time()
    with LOCK:
        fresh = [v for v in STATE['vehicles'] if now - v['recorded'] <= STALE_AFTER]
    usable = [v for v in fresh if v.get('origin') and v.get('dep')]
    sample = random.sample(usable, min(MATCH_SAMPLE, len(usable)))
    res = {'matched': 0, 'unsure': 0, 'notFound': 0}
    t0 = time.perf_counter()
    for v in sample:
        try:
            got = tt.match(*match_args(v))
        except Exception as exc:   # one odd bus must never break the layer
            with LOCK:
                STATE['timetableError'] = ('match: ' + type(exc).__name__)[:100]
            got = []
        res['matched' if len(got) == 1 else 'unsure' if got else 'notFound'] += 1
    n = len(sample)
    return {'busesChecked': n, 'withJourneyDetailsPct': round(100 * len(usable) / len(fresh)) if fresh else None,
            'matchedPct': round(100 * res['matched'] / n) if n else None,
            'unsurePct': round(100 * res['unsure'] / n) if n else None,
            'notFoundPct': round(100 * res['notFound'] / n) if n else None,
            'msPerBus': round((time.perf_counter() - t0) * 1000 / n, 1) if n else None, 'at': iso(now)}


def match_counter() -> None:
    while True:
        time.sleep(MATCH_EVERY)
        if FETCHING.is_set():
            time.sleep(10)
        r = count_matches()
        if r is not None:
            with LOCK:
                STATE['timetableMatch'] = r


def journey_sig(v: dict) -> tuple:
    return (v.get('origin'), v.get('dep'), v.get('route'), v.get('dest'), v.get('jref'))


def match_args(v: dict) -> tuple:
    """0.7.1: the bus company (the first part of the bus's key, its NOC) and its own journey code help
    tell apart journeys that leave the same stop at the same time."""
    return (v['origin'], v['dep'], v.get('route', ''), v.get('dest', ''), v['key'].split(':', 1)[0], v.get('jref', ''))


def shape_for(trip_id):
    if trip_id in SHAPES:
        SHAPES.move_to_end(trip_id)
        return SHAPES[trip_id]
    tt = TIMETABLE['tt']
    try:
        pts = tt.shape_of(trip_id) if tt else []
        sh = replay.Shape(pts) if len(pts) >= 2 else None
    except Exception:          # one odd journey must never break the layer
        sh = None
    SHAPES[trip_id] = sh
    while len(SHAPES) > SHAPES_KEEP:
        SHAPES.popitem(last=False)
    return sh


def follow_bus(v: dict) -> None:
    """Match this bus to its journey (once per journey) and place its new reports on the route."""
    with JOURNEY_LOCK:
        _follow_bus(v)


def _follow_bus(v: dict) -> None:
    tt = TIMETABLE['tt']
    if tt is None or not REPLAY_SECONDS:
        return
    sig = journey_sig(v)
    known = TRIPS.get(v['key'])
    if known is None or known[0] != sig:
        tid = None
        if v.get('origin') and v.get('dep'):
            try:
                got = tt.match(*match_args(v))
                tid = got[0] if len(got) == 1 else None
            except Exception as exc:   # one odd bus must never break the layer
                with LOCK:
                    STATE['timetableError'] = ('match: ' + type(exc).__name__)[:100]
        known = TRIPS[v['key']] = (sig, tid)
        FOLLOW.pop(v['key'], None)
    tid = known[1]
    if not tid:
        return
    sh = shape_for(tid)
    if sh is None:
        return
    with LOCK:
        hist = list(HISTORY.get(v['key']) or [(v['recorded'], v['lat'], v['lon'])])
    was = FOLLOW.get(v['key'])
    prev = was[1] if was and was[0] == tid and was[1] else None
    placed = replay.follow(sh, [(t, lon, lat) for t, lat, lon in hist], prev)
    if placed is not None and not prev:
        placed = placed[-replay.MAX_POINTS:]
    FOLLOW[v['key']] = (tid, placed[-12:] if placed else None)


def journey_worker() -> None:
    """0.7.0: after each download (and when a new area is looked at), match and place the buses in
    the areas people are looking at, nearest first; everything else waits until someone looks."""
    while True:
        JOURNEY_WAKE.wait(timeout=30)
        JOURNEY_WAKE.clear()
        if not REPLAY_SECONDS:
            continue
        while FETCHING.is_set():
            time.sleep(0.2)
        now = time.time()
        with LOCK:
            views = [w for t, w in VIEWS if now - t < VIEW_MEMORY]
            vehicles = STATE['vehicles']
        if not views:
            continue
        picks = []
        for v in vehicles:
            if now - v['recorded'] > DROP_AFTER:
                continue
            best = None
            for s0, w0, n0, e0 in views:
                hs, hw = (n0 - s0) / 2, (e0 - w0) / 2
                if s0 - hs <= v['lat'] <= n0 + hs and w0 - hw <= v['lon'] <= e0 + hw:
                    d = (v['lat'] - (s0 + n0) / 2) ** 2 + (v['lon'] - (w0 + e0) / 2) ** 2
                    best = d if best is None or d < best else best
            if best is not None:
                f = FOLLOW.get(v['key'])
                if f and f[1] and f[1][-1][0] >= v['recorded'] and TRIPS.get(v['key'], (None,))[0] == journey_sig(v):
                    continue          # already up to date
                legs = LEGS.get(v['key'])
                if legs and any(b[0] >= v['recorded'] for b in legs.values()) and TRIPS.get(v['key'], (None,))[0] == journey_sig(v):
                    continue
                picks.append((best, v))
        picks.sort(key=lambda p: p[0])
        t0 = time.monotonic()
        for _, v in picks[:JOURNEY_MAX]:
            if FETCHING.is_set():
                break
            follow_bus(v)
            f = FOLLOW.get(v['key'])
            if not (f and f[1]) and ROADS['map'] is not None:
                road_legs(v, time.time(), None)        # 0.7.1: unmatched buses replayed along the roads
        with JOURNEY_LOCK, LOCK:          # always this order (follow_bus takes LOCK inside JOURNEY_LOCK)
            live = {v['key'] for v in STATE['vehicles']}
            for d in (TRIPS, FOLLOW, LEGS):
                for k in [k for k in d if k not in live]:
                    del d[k]
            STATE['journeyRound'] = {'buses': min(len(picks), JOURNEY_MAX), 'waiting': max(0, len(picks) - JOURNEY_MAX),
                                     'seconds': round(time.monotonic() - t0, 1)}


def _leg(v: dict, a, b, before):
    """The drive from report a to report b (each (t, lat, lon)); `before` is the report before a or None."""
    rm = ROADS['map']
    (ta, la, oa), (tb, lb, ob) = a, b
    straight = metres(la, oa, lb, ob)
    bearing = None
    if before is not None and metres(before[1], before[2], la, oa) >= 10:
        bearing = _bearing(before[1], before[2], la, oa)
    way = None
    try:
        if straight < 15:
            p = rm.place(oa, la, bearing, PLACE_M) if rm else None
            way = [(p[0], p[1]), (p[0], p[1])] if p else [(oa, la), (oa, la)]
        elif rm is not None and straight <= LEG_MAX_M:
            got = rm.between(oa, la, bearing, ob, lb, max_m=min(LEG_MAX_M, max(300.0, 2.5 * straight + 150)))
            way = [tuple(q) for q in got] if got else None
    except Exception as exc:   # one odd bus must never break the layer
        with LOCK:
            STATE['roadMapError'] = ('leg: ' + type(exc).__name__)[:100]
    if way is None:
        pa = (rm.place(oa, la, bearing, PLACE_M) if rm else None) or (oa, la)
        pb = (rm.place(ob, lb, None, PLACE_M) if rm else None) or (ob, lb)
        way = replay.Jump([tuple(pa), tuple(pb)])
    return way


STAND_IN: set = set()       # 0.8.11: buses whose road track this moment stops short of their newest report


def road_legs(v: dict, now: float, deadline: float | None):
    """The legs between this bus's reports that the replay can still show; computes missing ones while
    time allows (deadline None = no limit). None if any is still missing."""
    with LOCK:
        hist = list(HISTORY.get(v['key']) or [])
    if not hist or hist[-1][0] < v['recorded']:
        hist.append((v['recorded'], v['lat'], v['lon']))
    keep_from = now - REPLAY_SECONDS - 120
    idx = [i for i in range(len(hist)) if i == len(hist) - 1 or hist[i + 1][0] >= keep_from]
    if not idx:
        return None
    first = idx[0]
    with JOURNEY_LOCK:
        known = LEGS.setdefault(v['key'], {})
    legs = []
    for i in range(first, len(hist) - 1):
        a, b = hist[i], hist[i + 1]
        got = known.get(a[0])
        if got is None or got[0] != b[0]:
            if deadline is not None and (time.monotonic() >= deadline or FETCHING.is_set()):
                if legs:
                    STAND_IN.add(v['key'])
                return legs or None      # 0.8.9: carry on along the legs already worked out (the newest comes later)
            got = known[a[0]] = (b[0], _leg(v, a, b, hist[i - 1] if i > 0 else None))
        legs.append((a[0], b[0], got[1]))
    for t in [t for t in known if t < hist[first][0]]:
        del known[t]
    if not legs:                         # a single report: the bus stands where it was seen
        p = (hist[-1][2], hist[-1][1])
        legs = [(hist[-1][0], hist[-1][0] + 0.001, [p, p])]
    return legs


def road_replay_for(v: dict, now: float, deadline: float | None):
    legs = road_legs(v, now, deadline)
    if not legs:
        return None
    try:
        made = replay.road_route(legs)
    except ValueError:
        made = None
    if made is None:                     # it has not moved: it stands where it was seen
        p = legs[-1][2][-1]
        return {'position': p, 'points': [p], 'times': [round(now, 1)], 'waiting': False, 'road': True}
    sh, placed, jumps = made
    r = replay.track(sh, placed, now, REPLAY_SECONDS, KEEP_LEFT_M, jumps)
    if r is not None:
        r['road'] = True
        if v['key'] in STAND_IN:
            r['standIn'] = True
    STAND_IN.discard(v['key'])
    return r


# 0.8.0 (owner 1 Nov 13:42: "ideally, the end user would get the start and end destination, then which
# stop is next"): HQ asks for a clicked bus's details (/details?id=bus:<key>). Timetabled times only.
STOPS_ALONG = OrderedDict()   # trip_id -> [(metres along route, name, indicator, arrive s, depart s)]


def _clock(secs: int) -> str:
    if secs is None or secs < 0:
        return ''
    secs %= 86400
    return f'{secs // 3600:02d}:{secs % 3600 // 60:02d}'


def _stop_name(name: str, indicator: str) -> str:
    name = (name or 'a stop').replace('_', ' ')
    return f'{name} ({indicator})' if indicator and indicator.lower() not in name.lower() else name


def stops_along(trip_id: str, sh):
    """This journey's stops in order, each placed along its route line (cached)."""
    with JOURNEY_LOCK:
        if trip_id in STOPS_ALONG:
            STOPS_ALONG.move_to_end(trip_id)
            return STOPS_ALONG[trip_id]
    tt = TIMETABLE['tt']
    rows = tt.stops_of(trip_id) if tt else []
    out, last = [], 0.0
    for sid, lat, lon, name, ind, arr, dep in rows:
        m = None
        if sh is not None and lat is not None:
            got = sh.project(lon, lat, max(0.0, last - 50), None)
            if got is not None:
                m = last = max(last, got[0])
        out.append((m, name, ind, arr, dep))
    with JOURNEY_LOCK:
        STOPS_ALONG[trip_id] = out
        while len(STOPS_ALONG) > 300:
            STOPS_ALONG.popitem(last=False)
    return out


def trace(entity_id: str) -> dict:
    """0.8.2 (owner 1 Nov 20:02: sign-off testing): everything this engine knows about one bus, so a reported
    problem ("this bus vanished / went the wrong way") can be traced. Only reachable on the server itself."""
    key = entity_id[4:] if entity_id.startswith('bus:') else entity_id
    now = time.time()
    with LOCK:
        v = next((dict(x) for x in STATE['vehicles'] if x['key'] == key), None)
        hist = list(HISTORY.get(key) or [])
        fetched = STATE['fetchedAt']
    out = {'id': 'bus:' + key, 'engineVersion': ENGINE_VERSION, 'askedAt': iso(now), 'lastDownloadAt': iso(fetched) if fetched else None}
    if v is None:
        out['state'] = 'not in the latest reports (finished, or not reporting for over 30 minutes)'
        return out
    age = now - v['recorded']
    out['state'] = 'dropped (no report for over 30 min)' if age > DROP_AFTER else 'not drawn (no report for over 10 min)' if age > SHOW_FOR else 'stale (no report for over 5 min): shown standing' if age > STALE_AFTER else 'reporting'
    out['latest'] = {k: v.get(k) for k in ('route', 'destination', 'lat', 'lon', 'speed', 'bearing', 'origin', 'dep', 'dest', 'jd')}
    out['latest']['reportedAt'] = iso(v['recorded']); out['latest']['ageSeconds'] = round(age)
    out['reports'] = [{'at': iso(t), 'agoSeconds': round(now - t), 'lat': la, 'lon': lo} for t, la, lo in hist[-12:]]
    with JOURNEY_LOCK:
        known, f = TRIPS.get(key), FOLLOW.get(key)
    out['timetable'] = ('matched to journey ' + str(known[1])) if known and known[1] else ('no match' if known else 'not worked out yet')
    out['following'] = None if not f else ('off its route' if f[1] is None else f'{len(f[1])} reports placed along its route')
    try:
        r = replay_for(v, now, time.monotonic() + 0.5) if age <= STALE_AFTER else None
    except Exception as exc:   # tracing must never break the layer
        r, out['replayError'] = None, type(exc).__name__
    if r is not None:
        out['shownAt'] = {'lon': round(r['position'][0], 6), 'lat': round(r['position'][1], 6),
                          'how': 'waiting for its next report' if r['waiting'] else 'along the roads' if r.get('road') else 'along its timetabled route',
                          'secondsBehind': REPLAY_SECONDS}
    else:
        out['shownAt'] = {'lon': v['lon'], 'lat': v['lat'], 'how': 'at its newest report (not replayed)'}
    return out


# 0.9.1 (owner 10 Nov 11:12-11:17): no bus in the national feed says what kind it is (tpa-bus-kinds: 28,232 buses, no
# seats, no vehicle type), so the kind comes from companies that only run one kind. HQ shows the owner's matching picture
# (dd / sd / coach / mini); anything else gets HQ's faded "Type not known" picture.
KIND_BY_OPERATOR = {'NATX': ('coach', 'Coach')}      # National Express: coaches only
KIND_NOT_GIVEN = 'Not known'
# 0.10.0 (owner 10 Nov 11:49 "every bus seems to be a double decker" -> 12:03 "yes, proceed"): Stagecoach and First number
# every bus by its kind, the same way across the country (their national numbering schemes, as the public fleet lists
# group them: Bus Lists on the Web, 10 Nov 2026). Only a plain 5-digit fleet number counts; anything else is "not known".
FLEET_SCHEMES = {
    'stagecoach': {'1': ('dd', 'Double decker'), '2': ('sd', 'Single decker'), '3': ('sd', 'Midi single decker'),
                   '4': ('sd', 'Minibus or small single decker'), '5': ('coach', 'Coach'),
                   '6': ('sd', 'Electric or hydrogen midi single decker'), '7': ('sd', 'Electric or hydrogen single decker'),
                   '8': ('dd', 'Electric or hydrogen double decker')},
    'first': {'1': ('sd', 'Articulated single decker (bendy bus)'), '2': ('coach', 'Coach'), '3': ('dd', 'Double decker'),
              '4': ('sd', 'Midi single decker'), '5': ('sd', 'Minibus or small single decker'), '6': ('sd', 'Single decker')},
}
# 0.12.0 (owner 10 Nov 16:17 "Yes to 1" = Nottingham): Nottingham City Transport's fleet numbers, from the owner's
# check at 16:24 - all 258 live buses matched to their timetabled journey (ticket number + route), whose timetable names
# the kind. Only ranges actually seen; journeys and buses disagreed twice (211, 270), as expected when a depot sends a
# different bus from the one planned, so the number (the real bus) is used rather than the journey (the plan).
FLEET_RANGES = {
    # 0.12.1 (owner 16:43, NCT's own list: 201 is a 12 m Yutong E12, 356 an Optare Solo): the timetable's SMSD / MIDI
    # codes don't say the size of the real bus, so every Nottingham single decker is just "Single decker"
    'NCTR': [(96, 97, 'dd', 'Double decker'), (201, 222, 'sd', 'Single decker'), (251, 292, 'sd', 'Single decker'),
             (355, 358, 'sd', 'Single decker'), (401, 542, 'dd', 'Double decker'), (601, 676, 'dd', 'Double decker'),
             (1002, 1016, 'dd', 'Double decker')],
}
# companies seen in the owner's checks (10 Nov), used when the timetable cannot name the company
GROUP_BY_NOC = {**{n: 'stagecoach' for n in ('SCEM', 'SCSO', 'SCCU', 'SCNE', 'SDVN', 'SCNH', 'SCEK', 'SYRK', 'SCCM', 'SSWL',
                                             'SCGL', 'SCMY')},
                **{n: 'first' for n in ('FBRI', 'FLDS', 'FSYO', 'FESX', 'FECS', 'FYOR')}}
GROUP_MEMO: dict = {}


def bus_group(noc: str) -> str:
    """'stagecoach', 'first' or '' for a company code: its timetable name first, then the companies we have seen."""
    if noc not in GROUP_MEMO:
        name = ''
        try:
            name = (TIMETABLE['tt'].company_name(noc) if TIMETABLE['tt'] is not None else '').strip().lower()
        except Exception:
            name = ''
        group = 'stagecoach' if name.startswith('stagecoach') else 'first' if re.match(r'first( |$)', name) else ''
        if len(GROUP_MEMO) > 2000:
            GROUP_MEMO.clear()
        GROUP_MEMO[noc] = group or GROUP_BY_NOC.get(noc, '')
    return GROUP_MEMO[noc]


def bus_kind(key: str) -> tuple:
    """(picture, words for the box, more rows) for one bus: dd / sd / coach / mini / '' (not known)."""
    noc, _, vehicle = key.partition(':')
    noc = noc.upper()
    whose, row = fleet_lists.lookup(noc, vehicle, FLEET_LISTS)
    if row:                                        # 0.11.0: an exact match in a published fleet list
        if not row.get('plate') and noc == 'TFLO':  # 0.11.1: a London bus's id IS its plate (and older kept copies lack it)
            row = dict(row, plate=fleet_lists.plate(vehicle))
        row = dict(row, model=fleet_lists.tidy_model(row.get('model', '')))
        more = [{'label': label, 'value': str(row.get(k) or '')[:60]}
                for label, k in (('Model', 'model'), ('Power', 'power'), ('Fleet number', 'fleet'), ('Number plate', 'plate'),
                                 ('First registered', 'year'))
                if row.get(k)]
        return row['picture'], f"{row['kind']} (from {whose}'s fleet list)", more
    if noc in KIND_BY_OPERATOR:
        return KIND_BY_OPERATOR[noc] + ([],)
    if noc in FLEET_RANGES and vehicle.isdigit():
        for low, high, picture, words in FLEET_RANGES[noc]:
            if low <= int(vehicle) <= high:
                return picture, f'{words} (from its fleet number)', []
        return '', KIND_NOT_GIVEN, []
    scheme = FLEET_SCHEMES.get(bus_group(noc))
    fleet = vehicle.rsplit('-', 1)[-1]            # First sends "FBRI-30057"
    if scheme and re.fullmatch(r'\d{5}', fleet) and fleet[0] in scheme:
        picture, words = scheme[fleet[0]]
        return picture, f'{words} (from its fleet number)', []
    return '', KIND_NOT_GIVEN, []


def fleet_keeper() -> None:
    """0.11.0: the fleet lists kept on disk are used at once; on the server they are refreshed about weekly."""
    fleet_lists.load(DATA_DIR)
    if TIMETABLE_MODE != 'download' or not FLEET_LISTS:
        return
    time.sleep(float(os.environ.get('TPA_BUS_FLEET_START_DELAY', '300')))

    def note(**kw):
        with LOCK:
            FLEET_STATUS[kw['name']] = {'buses': kw['rows'], 'error': kw['error']}
    while True:
        fleet_lists.refresh(DATA_DIR, FLEET_LISTS, download, note)
        time.sleep(6 * 3600)


def details(entity_id: str) -> dict:
    out = {'component': COMPONENT, 'id': entity_id, 'rows': []}
    key = entity_id[4:] if entity_id.startswith('bus:') else ''
    with LOCK:
        v = next((x for x in STATE['vehicles'] if x['key'] == key), None)
    if v is None:
        out['note'] = 'This bus is no longer reporting.'
        return out
    picture, kind, more = bus_kind(key)
    if picture:
        out['picture'] = picture
    out['rows'].append({'label': 'Bus type', 'value': kind})
    out['rows'].extend(more)
    with JOURNEY_LOCK:
        trip = (TRIPS.get(key) or (None, None))[1]
        f = FOLLOW.get(key)
        sh = SHAPES.get(trip) if trip else None
    if not trip or TIMETABLE['tt'] is None:
        if v.get('destination'):
            out['rows'].append({'label': 'To', 'value': v['destination'][:80]})
        out['note'] = 'Its timetable could not be matched, so stops and times are not shown.'
        return out
    try:
        stops = stops_along(trip, sh)
    except Exception as exc:   # one odd journey must never break the layer
        with LOCK:
            STATE['timetableError'] = ('details: ' + type(exc).__name__)[:100]
        stops = []
    if not stops:
        out['note'] = 'Its timetable has no stops listed.'
        return out
    # 0.9.0 (owner 10 Nov 10:18: "get a similar look to the trains dialogue that opens when clicked"): a sentence
    # saying which bus it is, and its stops in order - done / here / due - drawn by HQ like a train's calling points.
    route, company = ('', '')
    try:
        route, company = TIMETABLE['tt'].trip_info(trip)
    except Exception:
        pass
    route = route or v.get('route') or ''
    # 0.10.1 (owner 10 Nov 13:15, a Hull bus: "adding east midland would confuse a Hull user"): Stagecoach is one name
    # nationwide; its regional company names ("Stagecoach East Midlands" runs Hull) mean nothing to passengers.
    if company.lower().startswith('stagecoach'):
        company = 'Stagecoach'
    first, last = stops[0], stops[-1]
    who = (company + ' ' if company else '') + (f'number {route} ' if route else '')
    out['summary'] = (f'This is the {who}{"coach" if picture == "coach" else "bus"}, the {_clock(first[4])} from {_stop_name(first[1], first[2])} '
                      f'to {_stop_name(last[1], last[2])}.')[:200]
    # the moment it is shown at (REPLAY_SECONDS behind its reports), on its journey's own clock
    shown_clock = None
    try:
        started = datetime.fromisoformat(str(v.get('dep') or '').replace('Z', '+00:00')).timestamp()
        shown_clock = first[4] + (time.time() - REPLAY_SECONDS - started)
    except (TypeError, ValueError):
        pass
    # where the bus is shown now along its route
    shown_m = None
    if f and f[1] and sh is not None:
        r = replay.track(sh, f[1], time.time(), REPLAY_SECONDS)
        if r is not None:
            lon, lat = r['position']
            # look near its own reports first (a route can pass the same place twice)
            got = sh.project(lon, lat, max(0.0, f[1][0][1] - 1), f[1][-1][1] + 1) or sh.project(lon, lat)
            shown_m = got[0] if got else None
    states = []
    if shown_m is not None:
        for st in stops:
            if st[0] is None:
                states.append(None)
            else:
                states.append('done' if st[0] < shown_m - 15 else 'here' if st[0] <= shown_m + 15 else 'due')
        # a stop that could not be placed on the route takes the state of the next one that could
        nxt_state = 'due'
        for i in range(len(states) - 1, -1, -1):
            if states[i] is None:
                states[i] = 'done' if nxt_state in ('done', 'here') else 'due'
            else:
                nxt_state = states[i]
    elif shown_clock is not None:
        states = ['done' if (st[4] if i == 0 else st[3]) < shown_clock else 'due' for i, st in enumerate(stops)]
    else:
        states = ['due'] * len(stops)
    # how late: where it is against when the timetable has it there (only when it is on its route)
    late = None
    known = [st for st in stops if st[0] is not None]
    if shown_m is not None and shown_clock is not None and known:
        if shown_m <= known[0][0] + 15:
            due_then = known[0][4]
        elif shown_m >= known[-1][0]:
            due_then = known[-1][3]
        else:
            due_then = None
            for a, b in zip(known, known[1:]):
                if a[0] <= shown_m < b[0]:
                    if shown_m <= a[0] + 15 or b[0] - a[0] < 1:
                        due_then = a[4]
                    else:
                        due_then = a[4] + (b[3] - a[4]) * (shown_m - a[0]) / (b[0] - a[0])
                    break
        if due_then is not None and abs(shown_clock - due_then) <= 90 * 60:
            late = round((shown_clock - due_then) / 60)
    if late is not None:
        out['rows'].append({'label': 'Running', 'value': 'About on time' if abs(late) <= 1 else
                            f'About {late} min late' if late > 0 else f'About {-late} min early'})
    calls = []
    for i, (st, state) in enumerate(zip(stops, states)):
        booked = st[4] if i == 0 else st[3]
        stop = {'name': _stop_name(st[1], st[2])[:80], 'state': state, 'time': _clock(booked)}   # 0.11.1: 80, wrapped by HQ
        if state == 'due' and late is not None and abs(late) >= 2:
            stop['expected'] = _clock(booked + late * 60)
        calls.append(stop)
    if len(calls) > 120:     # a very long route: keep the part around where the bus is
        at = next((i for i, c in enumerate(calls) if c['state'] != 'done'), len(calls))
        cut = max(0, min(at - 10, len(calls) - 120))
        calls = calls[cut:cut + 120]
    out['stops'] = calls
    out['note'] = ('Times are from the timetable; expected times are estimated from where the bus is on its route.'
                   if late is not None else 'Times are from the timetable; the bus may be early or late.')
    return out


# 0.8.4 (owner 3 Nov 12:32, load test: this engine was half the server's work): a bus's replayed track only
# changes when it sends a new report (about once a minute), so it is worked out once and, for up to
# REPLAY_MEMO_SECONDS or until the next report, handed to every viewer cut to the current moment.
REPLAY_MEMO: dict = {}
REPLAY_MEMO_SECONDS = 15.0


def replay_for(v: dict, now: float, deadline: float):
    """The replayed track for this bus, or None (moved the ordinary way)."""
    if not REPLAY_SECONDS:
        return None
    m = REPLAY_MEMO.get(v['key'])
    if m and m[0] == v['recorded'] and 0 <= now - m[1] < REPLAY_MEMO_SECONDS:
        STATE['replayMemoHits'] = STATE.get('replayMemoHits', 0) + 1
        return replay.from_now(m[2], now)
    r = _replay_for(v, now, deadline)
    if r is not None and r.pop('standIn', False):
        return r                     # 0.8.11: a stand-in is not kept, so the next answer carries the new report
    if r is not None:
        if len(REPLAY_MEMO) > 40000:
            REPLAY_MEMO.clear()
        REPLAY_MEMO[v['key']] = (v['recorded'], now, r)
        STATE['replayMemoMade'] = STATE.get('replayMemoMade', 0) + 1
    return r


def _replay_for(v: dict, now: float, deadline: float):
    if not REPLAY_SECONDS:
        return None
    if TIMETABLE['tt'] is None:
        return road_replay_for(v, now, deadline) if ROADS['map'] is not None else None
    f = FOLLOW.get(v['key'])
    sig = journey_sig(v)
    fresh = f and f[1] and f[1][-1][0] >= v['recorded'] and TRIPS.get(v['key'], (None,))[0] == sig
    if not fresh and v.get('origin') and v.get('dep') and time.monotonic() < deadline and not FETCHING.is_set():
        follow_bus(v)                       # a new area: work it out now rather than switch later
        f = FOLLOW.get(v['key'])
        fresh = f and f[1] and f[1][-1][0] >= v['recorded']
    sh = None
    same_trip = f and f[1] and TRIPS.get(v['key'], (None,))[0] == sig
    if fresh or same_trip:
        # 0.8.9: a new report not placed yet (the engine is busy reading a download): carry on along the part of
        # the route already placed - the moment shown is 90 s ago, so that part nearly always still covers it
        with JOURNEY_LOCK:
            sh = SHAPES.get(f[0])
    if sh is not None:
        r = replay.track(sh, f[1], now, REPLAY_SECONDS, KEEP_LEFT_M)
        if r is not None and not fresh:
            r['standIn'] = True      # 0.8.11: up to an older report only - never remembered (see replay_for)
        return r
    known = TRIPS.get(v['key'])
    if v.get('origin') and v.get('dep') and (known is None or known[0] != journey_sig(v)):
        return None                  # not worked out yet: may still match its timetable
    if ROADS['map'] is None:
        return None
    return road_replay_for(v, now, deadline)          # 0.7.1: unmatched - along the road map instead


def road_motion(v: dict):
    """Path along the road ahead for this report, or None (straight-line movement is used instead)."""
    rm = ROADS['map']
    if rm is None or not v.get('speed') or v.get('bearing') is None:
        return None
    ease = GLIDE_SECONDS - STEADY_SECONDS
    metres = v['speed'] * (STEADY_SECONDS + ease / 2)
    t0 = time.perf_counter()
    try:
        pts = rm.ahead(v['lon'], v['lat'], v['bearing'], metres, KEEP_LEFT_M)
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
            p = None if m else place(v)
            with LOCK:
                PATHS[v['key']] = (v['recorded'], m)
                if not m:
                    PLACED[v['key']] = (v['recorded'], p)


def place(v: dict):
    """0.5.7: where on its road to draw a bus that is not following a road path (standing, or no path
    found): on the road, left-hand side for its direction; None if no road within PLACE_M."""
    rm = ROADS['map']
    if rm is None:
        return None
    try:
        return rm.place(v['lon'], v['lat'], v.get('bearing'), PLACE_M, KEEP_LEFT_M)
    except Exception as exc:   # one odd bus must never break the layer
        with LOCK:
            STATE['roadMapError'] = ('place: ' + type(exc).__name__)[:100]
        return None


def placed_for(v: dict, deadline: float):
    with LOCK:
        known = PLACED.get(v['key'])
    if known and known[0] == v['recorded']:
        return known[1]
    if time.monotonic() < deadline and not FETCHING.is_set():
        p = place(v)
        with LOCK:
            PLACED[v['key']] = (v['recorded'], p)
        return p
    with LOCK:
        if (v['key'], v['recorded']) not in PENDING_SET:
            PENDING_SET.add((v['key'], v['recorded']))
            PENDING.append(v)
    PATH_WAKE.set()
    return None


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
        if not client_allowed(self.client_address[0]):
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
                                        'busesInLastDownload': STATE.get('busesInFile', len(STATE['vehicles'])), 'lastDownloadKB': STATE['downloadBytes'] // 1024,
                                        'reportAgeMedianSeconds': STATE['reportAgeMedian'], 'busesWithSpeed': STATE['busesWithSpeed'],
                                        'bodsDownloadsThisRun': STATE['downloadsThisRun'], 'pollSeconds': POLL_SECONDS, 'alwaysOn': ALWAYS_ON,
                                        'roadMap': STATE['roadMap'], 'roadMapDetail': STATE['roadMapDetail'],
                                        'roadMapSource': ROADS['source'],
                                        'roadMapError': STATE['roadMapError'],
                                        'roadPathsKnown': sum(1 for x in PATHS.values() if x[1]),
                                        'roadTilesLoaded': len(ROADS['map'].tiles) if ROADS['map'] else 0,
                                        'lastFetchSeconds': TIMING['fetch'], 'roadPathMs': TIMING['pathMs'],
                                        'roadCacheMB': round(ROADS['map'].bytes / 1e6) if ROADS['map'] else 0,
                                        'roadCacheLimitMB': round(ROADS['map'].max_bytes / 1e6) if ROADS['map'] else 0,
                                        'measure': measure_summary(STATE['vehicles'], now),
                                        'timetable': STATE.get('timetable'), 'timetableDetail': STATE.get('timetableDetail'),
                                        'timetableUpdate': STATE.get('timetableUpdate'), 'timetableError': STATE.get('timetableError'),
                                        'timetableMatch': STATE.get('timetableMatch'), 'replaySeconds': REPLAY_SECONDS,
                                        'fleetLists': {'on': FLEET_LISTS, 'have': fleet_lists.counts(), 'last': dict(FLEET_STATUS)},
                                        'journeyRound': STATE.get('journeyRound'),
                                        'replayMemo': {'made': STATE.get('replayMemoMade', 0), 'reused': STATE.get('replayMemoHits', 0)},
                                        'cpuSeconds': meter_summary(),
                                        'lastView': STATE.get('lastView')})
        if split.path == '/details':
            want = (parse_qs(split.query).get('id') or [''])[0][:140]
            return self.reply(200, details(want))
        if split.path == '/trace':
            want = (parse_qs(split.query).get('id') or [''])[0][:140]
            return self.reply(200, trace(want))
        if split.path == '/snapshot':
            return self.reply(200, snapshot(parse_bbox(split.query)))
        return self.reply(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        self.reply(405, {'error': 'read only'})

    def log_message(self, fmt, *args):
        pass


# 0.8.5 (owner 3 Nov 13:16: load test still ~25 viewers after 0.8.4 - measure, don't guess): processor seconds
# spent in each part of this engine since it started, shown in /status and tpa-status. Parts can sit inside
# others ("answers" includes "routes", "routes" includes "road routes" and "matching now"), so they do not add up.
METER: dict = {}
METER_STARTED = time.process_time()


def _metered(name, fn):
    def wrapped(*a, **k):
        t0 = time.thread_time()
        try:
            return fn(*a, **k)
        finally:
            METER[name] = METER.get(name, 0.0) + time.thread_time() - t0
    wrapped.__name__ = getattr(fn, '__name__', name)
    return wrapped


snapshot = _metered('answers', snapshot)
_replay_for = _metered('routes', _replay_for)
road_replay_for = _metered('road routes', road_replay_for)
_follow_bus = _metered('matching', _follow_bus)
fetch_once = _metered('download', fetch_once)
save_memory = _metered('saving', save_memory)
motion_for = _metered('old-style moves', motion_for)
placed_for = _metered('placing on roads', placed_for)
details = _metered('bus boxes', details)
Handler.reply = _metered('packaging', Handler.reply)


def meter_summary() -> dict:
    out = {k: round(v, 1) for k, v in sorted(METER.items(), key=lambda kv: -kv[1])}
    out['engine total'] = round(time.process_time() - METER_STARTED, 1)
    return out


# 0.8.8 (owner 3 Nov 15:55: the main server got 403 "local only" from the bus server): on a bus-only server the engine
# also answers the main server's private address (TPA_BUS_ALLOW) and its own private address (tpa-status there).
# Only private addresses count; Oracle's rule set and the server firewall also only let the main server in.
_PRIVATE = re.compile(r'(127\.0\.0\.1|10(\.\d{1,3}){3}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}|192\.168(\.\d{1,3}){2})')
BIND = os.environ.get('TPA_BUS_BIND', '127.0.0.1').strip() or '127.0.0.1'
if not _PRIVATE.fullmatch(BIND):
    BIND = '127.0.0.1'                            # only this machine or a private network, never the internet
ALLOWED_CLIENTS = {'127.0.0.1', BIND} | {a for a in os.environ.get('TPA_BUS_ALLOW', '').split() if _PRIVATE.fullmatch(a)}


def client_allowed(ip: str) -> bool:
    return ip in ALLOWED_CLIENTS


def main() -> None:
    port = int(os.environ.get('TPA_BUS_ENGINE_PORT', '8781'))
    load_memory()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=road_keeper, daemon=True).start()
    threading.Thread(target=timetable_keeper, daemon=True).start()
    threading.Thread(target=fleet_keeper, daemon=True).start()
    threading.Thread(target=match_counter, daemon=True).start()
    threading.Thread(target=journey_worker, daemon=True).start()
    threading.Thread(target=path_worker, daemon=True).start()
    # 0.8.7 (owner 3 Nov 14:41): on its own Ampere server the engine listens on that server's PRIVATE address, so the
    # main server can ask it (both servers' firewalls only allow the main server's private address in)
    bind = BIND
    with ThreadingHTTPServer((bind, port), Handler) as server:
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
