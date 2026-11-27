"""TPA v4 HQ MapLibre/OpenFreeMap development preview (localhost only).

Only Oracle/HQ server is allowed to fetch external map resources. Browser receives
same-origin assets. No transport feeds, keys, Admin, engine code, or Oracle changes.
MapLibre is pinned to historically verified 5.24.0 and cached by the server;
OpenFreeMap upstream is confined to an allowlisted host and paths. Not a final
commercial map infrastructure / rights or performance approval.
"""
from __future__ import annotations

from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen
import hashlib
import ipaddress
import gzip
import zlib
import json
import mimetypes
mimetypes.add_type('image/webp','.webp')   # 4.21.0: train pictures
mimetypes.add_type('application/manifest+json','.webmanifest')   # 4.112.0: the app file
import os
import re
import socket
import ssl
import tempfile
import math
import threading
import time
import html as htmllib

ROOT = Path(__file__).resolve().parent
COMPONENTS = ('air', 'sea', 'rail', 'bus', 'road', 'stations', 'stops', 'busstations')   # 4.52.0: stations and their live boards; 4.53.0: bus stops; 4.54.0: bus stations
PLANE_BOX = ('chinook','a400m','c17','c5m','falcon900','a320','b737','b787','b777','a220','atr72','tutor','prefect','cessna','piper','typhoon','kingair','airliner','widebody','turboprop','bizjet','light','helicopter','cargo','miltransport','a330','a350','embraer','robinson','trainer','glider','milheli','police','ambulance','coastguard','a380','b747','b767','apache','wildcat','twinotter','global','falcon','texan','l159','emer','gnd','twr')   # 4.43.0: plane box pictures
PUBLIC = {'/', '/index.html', '/assets/hq.css', '/assets/tpa-theme.css', '/assets/hq.js', '/assets/tpa-basemap-loader.js', '/assets/transport-vector-layer.js', '/data/uk-geography.json', '/data/site-config.json', '/assets/geoff.jpg', '/user', '/kids', '/full', '/planes', '/assets/kids-sounds.js', '/assets/bus-replay.js', '/assets/bus-fresh.js'} | {'/assets/kids/'+f for f in ('bus-horn.mp3','bus-horn2.mp3','bus-engine.mp3','train-chuff.mp3','train-whistle.mp3','train-horn.mp3','train-track.mp3','plane-flyby.mp3','plane-takeoff.mp3','CREDITS.txt')} | {'/assets/icons/bus.webp', '/assets/icons/train.webp', '/assets/buses/bus.webp', '/assets/icons/busstation.webp', '/assets/icons/trainstation.webp', '/assets/icons/busstop.webp'} | {'/assets/buses/'+k+'.webp' for k in ('dd','sd','coach','mini')} | {'/assets/planes/box/'+k+'.webp' for k in PLANE_BOX} | {'/assets/planes/'+k+'.webp' for k in ('airliner','widebody','turboprop','bizjet','light','helicopter','cargo','fighter','glider','balloon','tanker','miltransport','milheli','surveillance','policeheli','policeplane','ambulanceheli','ambulanceplane','coastguardheli','coastguardplane','icon-emer','icon-gnd','icon-twr')} | {'/assets/trains/'+k+'.webp' for k in ('c800','c390','c43','c220','c150','c158','c170','c195','c345','c700','c755','c387','c66','c165','c777','c350','c385','c323','c185','c395','c380','c333','celectric','cdiesel')}   # 4.11.1: kids recordings   # 4.9.0: /user = the page as a standard user sees it   # 4.7.0: Geoff (owner's cat) on the welcome screen
# 4.14.0 (owner 3 Nov 20:35: "more professional", its own look): TPA's own signage type, served from here (no outside requests)
PUBLIC |= {'/assets/planes/shapes.json', '/assets/planes/SHAPES-LICENCE.txt'}   # 4.84.0: top-down shapes of unusual plane types (CC0)
PUBLIC |= {'/assets/buses/geoff.webp'}   # 4.89.0: Geoff driving (Find Geoff)
PUBLIC |= {'/assets/fonts/'+f for f in ('Barlow-Regular.woff','Barlow-Medium.woff','Barlow-SemiBold.woff','BarlowSemiCondensed-SemiBold.woff','BarlowSemiCondensed-Bold.woff','OFL.txt')}
# 4.112.0 (Rob 26 Nov 23:37 "do step 1 now please"): TPA installable as an app (Chrome "Install", phones "Add to Home
# Screen") - the app file, icons, and a helper whose only job is a "no internet" page (it never stores live data).
PUBLIC |= {'/manifest.webmanifest', '/sw.js', '/offline.html'} | {'/assets/app/'+f for f in ('icon-192.png','icon-512.png','icon-96.png','icon-maskable-512.png','apple-touch-icon.png','favicon-32.png')}
# Preview: the page only offers the app with ?app=1 (the installed app always opens with ?app=1). True = everyone.
# 4.114.0 (Rob 27 Nov 00:39 "update for all users"): everyone. The Kids page is left out (the app opens the main map,
# which would surprise a child); it still offers it with ?app=1.
APP_FOR_ALL = True
APP_TAGS = (b'<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"/>\n'
            b'  <link rel="icon" type="image/png" sizes="32x32" href="/assets/app/favicon-32.png"/>\n'
            b'  <link rel="apple-touch-icon" href="/assets/app/apple-touch-icon.png"/>\n'
            b'  <meta name="apple-mobile-web-app-title" content="TPA"/>\n'
            b'  <meta name="mobile-web-app-capable" content="yes"/>\n'
            b'  <meta name="apple-mobile-web-app-status-bar-style" content="black"/>\n</head>')


def app_page(raw, query, path=''):
    if (APP_FOR_ALL and path != '/kids') or 'app=1' in query.split('&'):
        return raw.replace(b'</head>', APP_TAGS, 1)
    return raw


HQ_VERSION = '4.115.1'   # 4.114.1: bustimes.org credit (site-config only); 4.115.0: real photos open bigger on a click; 4.115.1: no text over small photos
TILES_DIR = os.environ.get('TPA_HQ_TILES_DIR', '').strip()   # 4.56.0: tests only
# 4.89.0 (Rob 18 Nov 18:17-18:31, "Find Geoff"): one Geoff bus per nation each day, picked from the bus engine's
# published city files (read only; the bus engine is not asked anything). See geoff.py.
import sys as _sys   # 4.90.0: find geoff.py beside this file however serve_hq is loaded (some checks load it from elsewhere)
if str(Path(__file__).resolve().parent) not in _sys.path: _sys.path.insert(0, str(Path(__file__).resolve().parent))
import geoff as GEOFF_MOD   # noqa: E402
GEOFF = GEOFF_MOD.Picker(os.environ.get('TPA_HQ_GEOFF_TILES', '').strip() or TILES_DIR or '/run/tpa-bus-tiles',
                         Path(tempfile.gettempdir()) / 'geoff-today.json')
# 4.106.0 (Rob 24 Nov 23:12-23:39): real bus photos by number plate from Wikimedia Commons (free, credited), kept in our own
# folder and served from our own address; Rob's pictures where there is none. See photos.py. TPA_HQ_PHOTO_API: tests only
# (a pretend Commons on 127.0.0.1). 4.107.0: shown to everyone (?photos=0 turns them off); never for a plate that may
# have been on another bus (photos.trusted).
import photos as PHOTOS_MOD   # noqa: E402
_photo_api = os.environ.get('TPA_HQ_PHOTO_API', '').strip()
if _photo_api and not re.match(r'^http://127\.0\.0\.1:\d+/', _photo_api):
    raise SystemExit('TPA_HQ_PHOTO_API must point at 127.0.0.1 (testing only)')
PHOTOS = PHOTOS_MOD.Finder(os.environ.get('TPA_HQ_PHOTO_DIR', '').strip() or (Path(tempfile.gettempdir()) / 'tpa-photos'),
                           api=_photo_api or PHOTOS_MOD.API)
# 4.108.0 (Rob 26 Nov 17:28 "yeah this sounds excellent!"): real plane photos by registration, the same way, own folder;
# 4.109.0 (Rob 19:14): for everyone (?planephotos=0 turns them off). See photos.PlaneFinder.
# 4.110.0 (Rob 26 Nov 21:03-21:08 "is there any way of introducing the satellite image map"; "ok esri it is for a start"):
# satellite photos from Esri (ArcGIS Location Platform free tier: 2M basemap tiles a month, paying switched off). The map
# key lives only on the server (TPA_HQ_SAT_KEY, systemd drop-in tpa-hq.service.d/satellite.conf), never in the code; it
# is locked at Esri to tpalive.co.uk, and the page sends just the site's name to Esri ("origin" referrer) so the lock works.
SAT_KEY=os.environ.get('TPA_HQ_SAT_KEY','').strip()
if not re.fullmatch(r'[A-Za-z0-9._-]{20,600}',SAT_KEY):SAT_KEY=''
SAT_HOSTS='https://ibasemaps-api.arcgis.com https://static-map-tiles-api.arcgis.com'
def sat_config():
    if not SAT_KEY:return None
    return {'imagery':'https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token='+SAT_KEY,
            'labels':'https://static-map-tiles-api.arcgis.com/arcgis/rest/services/static-basemap-tiles-service/v1/arcgis/imagery/labels/static/tile/{z}/{y}/{x}?token='+SAT_KEY,
            'attribution':'Powered by <a href="https://www.esri.com/" target="_blank" rel="noopener">Esri</a> | Esri, Maxar, Earthstar Geographics, and the GIS User Community'}
def plane_type_name(pid):   # 4.108.1: the box's "Aircraft" line for a plane, from the planes engine (warm-up only); 4.108.2: and its operator
    raw,_=engine_get('air','/details?'+urlencode({'id':pid}),262144,10)
    rows={str(r.get('label','')):str(r.get('value','')) for r in (json.loads(raw).get('rows') or []) if isinstance(r,dict)}
    return rows.get('Aircraft',''),rows.get('Operator') or rows.get('Airline') or ''
PLANE_PHOTOS = PHOTOS_MOD.PlaneFinder(os.environ.get('TPA_HQ_PLANE_PHOTO_DIR', '').strip() or (Path(tempfile.gettempdir()) / 'tpa-plane-photos'),
                                      api=_photo_api or PHOTOS_MOD.API, namer=plane_type_name)
PLANE_WARM_EVERY = float(os.environ.get('TPA_HQ_PLANE_WARM_EVERY', '') or 300)   # tests only: shorter
def plane_warm_loop():   # 4.108.1 (Rob 26 Nov 18:11 "pre look up is a good idea"): every 5 min, planes over the UK not yet looked up
    time.sleep(min(45, PLANE_WARM_EVERY))
    while True:
        try:
            raw,_=engine_get('air','/snapshot?west=-12&south=48&east=5&north=61.5',4_000_000,10)
            planes=[]
            for e in json.loads(raw).get('entities') or []:
                if not isinstance(e,dict):continue
                # the planes engine's label is "<flight> · <registration>", or one of them alone (air label_for): the
                # second part when there are two; one alone only if it has a hyphen (G-EZTZ, never a flight like EZY12AB)
                parts=[p.strip() for p in str(e.get('label','')).split(' · ')]
                reg=parts[1] if len(parts)==2 else parts[0] if len(parts)==1 and '-' in parts[0] else ''
                if not PHOTOS_MOD.reg_norm(reg):reg=''
                if reg:planes.append((reg,e.get('id',''),e.get('typeCode','')))
            PLANE_PHOTOS.warm(planes)
        except Exception:
            pass
        time.sleep(PLANE_WARM_EVERY)
def photo_warm_loop():   # 4.106.1: every 10 min, plates on the map never looked up join the slow background queue
    while True:
        time.sleep(30)
        try:
            PHOTOS.warm(PHOTOS_MOD.plates_from_tiles(os.environ.get('TPA_HQ_GEOFF_TILES', '').strip() or TILES_DIR or '/run/tpa-bus-tiles'))
        except Exception:
            pass
        time.sleep(570)
def geoff_loop():
    while True:
        try:GEOFF.refresh()
        except Exception as exc:print('Geoff picker:',type(exc).__name__,exc,flush=True)
        time.sleep(GEOFF_MOD.EVERY)
# 4.0.10: engines are separate programs on this PC. TPA_HQ_ENGINES maps a socket
# to a loopback engine, e.g. "road=http://127.0.0.1:8782,bus=http://127.0.0.1:8781".
# 4.13.1 (owner 3 Nov 16:00): or an engine on a PRIVATE network address (the bus engine on its own Ampere server).
# HQ relays data only (JSON frames, PNG overlay tiles); it contains no supplier
# logic, keys or transport-specific code. An engine failing affects only its socket.
ENGINES = {}
for _pair in os.environ.get('TPA_HQ_ENGINES', '').split(','):
    _name, _, _url = _pair.strip().partition('=')
    # 4.53.0: an engine may serve a second layer under that layer's own name (bus stops: "stops=http://127.0.0.1:8781/stops");
    # no other path is accepted
    _m = re.fullmatch(r'http://(127\.0\.0\.1|10(\.\d{1,3}){3}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}|192\.168(\.\d{1,3}){2}):\d{2,5}(?P<path>/[a-z]{1,20})?', _url.strip())
    if _name in COMPONENTS and _m and _m.group('path') in (None, '/' + _name):
        ENGINES[_name] = _url.strip()
# 4.0.9: owner decision 30 Oct 2026. A component can be "parked" by server
# setting (e.g. TPA_HQ_PARKED=sea). HQ code is unchanged per component: the
# socket simply reports unavailable/coming soon and serves no data, even if a
# fixture exists. Unknown names are ignored, never errors.
PARKED = frozenset(x.strip() for x in os.environ.get('TPA_HQ_PARKED', '').split(',') if x.strip() in COMPONENTS)
# 4.0.12: server mode. HQ still listens on 127.0.0.1 only; a local web server
# (Caddy) in front of it handles HTTPS and the test password. TPA_HQ_PUBLIC_ORIGIN
# (exactly "https://host.name") lets HQ accept that one forwarded Host from the
# loopback proxy and build map URLs for it. Unset = unchanged PC behaviour.
PUBLIC_ORIGIN = ''
PUBLIC_HOST = ''
_po = os.environ.get('TPA_HQ_PUBLIC_ORIGIN', '').strip()
if _po:
    _m = re.fullmatch(r'https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)', _po)
    if not _m:
        raise SystemExit('TPA_HQ_PUBLIC_ORIGIN must be exactly https://host.name')
    PUBLIC_ORIGIN, PUBLIC_HOST = _po, _m.group(1)
# 4.74.1 (LAUNCH, 18 Nov 2026 01:3x: tpalive.co.uk answered "Development preview limited to home LAN"): more public
# addresses the same front door forwards (TPA_HQ_EXTRA_ORIGINS, space- or comma-separated, each exactly https://host.name)
EXTRA_ORIGINS = {}
for _eo in re.split(r'[\s,]+', os.environ.get('TPA_HQ_EXTRA_ORIGINS', '').strip()):
    if not _eo:
        continue
    _m2 = re.fullmatch(r'https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)', _eo)
    if not _m2:
        raise SystemExit('TPA_HQ_EXTRA_ORIGINS: each must be exactly https://host.name')
    EXTRA_ORIGINS[_m2.group(1)] = _eo
CACHE_DIR = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8'
FIXTURE_DIR = os.environ.get('TPA_HQ_FIXTURE_DIR')
DISABLE_MAP_NETWORK = os.environ.get('TPA_HQ_DISABLE_MAP_NETWORK', '0') == '1'
# 4.1.7 (owner 31 Oct: map loading slow on the small server): with TPA_HQ_BASEMAP=direct the
# visitor's browser fetches the OpenFreeMap street map straight from OpenFreeMap's fast network
# (its normal, intended use: "no limits on map views or requests"). The route through HQ stays
# as an automatic backup if the direct route fails. Default "via-hq" keeps the PC unchanged.
BASEMAP = os.environ.get('TPA_HQ_BASEMAP', 'via-hq').strip().lower()
# 4.20.0 (owner 5 Nov 15:26-16:41): the live server sets TPA_HQ_DEFAULT_VIEW=user, so the main address is the clean
# user page; /full (or ?view=full) is the owner's full page with "Report this". Unset = / stays the full page.
DEFAULT_VIEW = 'user' if os.environ.get('TPA_HQ_DEFAULT_VIEW', '').strip().lower() == 'user' else 'full'
if BASEMAP not in ('direct', 'via-hq'):
    raise SystemExit('TPA_HQ_BASEMAP must be "direct" or "via-hq"')
OFM = 'https://tiles.openfreemap.org'
# OpenFreeMap's offline-download guidance limits concurrent requests to four;
# use this bound for ordinary on-demand viewport requests too. No bulk prefetch.
UPSTREAM_SLOTS = threading.BoundedSemaphore(4)
LOCK = threading.Lock()
FREQUENCY = deque()
MAX_PER_MINUTE = 160  # development guard; *not* final production capacity
ERRORS = {}
NEGATIVE_CACHE = {}
BROWSER_HISTORY = deque(maxlen=12)
BROWSER = {'lastCode': 'none', 'lastStage':'none', 'lastDetail':'none', 'count': 0}
LAST_FAILURE = {'path':'none','category':'none','code':'none'}
RESOURCE_COUNTS = {'style': 0, 'tilejson': 0, 'vector': 0, 'sprite': 0, 'naturalEarth': 0, 'font': 0, 'rejected': 0}

ASSET_VERSION = '5.24.0'
VENDOR = {
    '/assets/vendor/leaflet.js': (
      ('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
       'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'), 'application/javascript; charset=utf-8'),
    '/assets/vendor/leaflet.css': (
      ('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
       'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css'), 'text/css; charset=utf-8'),
    '/assets/vendor/maplibre-gl.js': (
      ('https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.js',
       'https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js'), 'application/javascript; charset=utf-8'),
    '/assets/vendor/maplibre-gl.css': (
      ('https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.css',
       'https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css'), 'text/css; charset=utf-8'),
    '/assets/vendor/leaflet-maplibre-gl.js': (
      ('https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js',
       'https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js'), 'application/javascript; charset=utf-8'),
}

# The actual OpenFreeMap Liberty style uses /sprites/ofm_.../ofm(.json/.png)
# and /natural_earth/ne2sr/{z}/{x}/{y}.png. Rejecting either path caused
# silent local HTTP 404s in 4.0.4; earlier tests only exercised imagined
# /styles/liberty/sprite paths. Keep the allowlist resource-specific.
# Planet TileJSON may be plain /planet or versioned /planet/latest or
# /planet/YYYYMMDD_HHMMSS_pt. TileJSON advertises the exact versioned tile
# path; earlier HQ allowlist only permitted /planet/z/x/y.pbf and silently
# blocked legitimate versioned tiles. See OpenFreeMap upstream FAQ.
PLANET_VERSION = r'(?:latest|[0-9]{8}_[0-9]{6}_[a-z]{2,3})'
PLANET_META = re.compile(rf'^/planet(?:/{PLANET_VERSION})?$')
PLANET_TILE = re.compile(rf'^/planet(?:/{PLANET_VERSION})?/[0-9]{{1,2}}/[0-9]{{1,7}}/[0-9]{{1,7}}\.pbf$')
OPENFREEMAP_PATH = re.compile(
    r'^/(?:styles/(?:liberty|bright|dark|positron|fiord)(?:/(?:style\.json|sprite(?:@2x)?\.(?:png|json)))?'
    r'|sprites/[A-Za-z0-9_-]{1,70}/[A-Za-z0-9_-]{1,70}(?:@2x)?\.(?:png|json)'
    r'|natural_earth/ne2sr/[0-9]{1,2}/[0-9]{1,7}/[0-9]{1,7}\.png'
    r'|fonts/[A-Za-z0-9,%+ _-]{1,140}/[0-9]{1,5}-[0-9]{1,5}\.pbf'
    r'|data/[A-Za-z0-9_/-]{1,120}\.(?:pbf|json|png))$'
)

MIME_BY_EXT = {'.js':'application/javascript; charset=utf-8', '.css':'text/css; charset=utf-8',
               '.json':'application/json; charset=utf-8', '.png':'image/png', '.pbf':'application/x-protobuf'}

def certificate_context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def fetch_remote(url, limit):
    if DISABLE_MAP_NETWORK:
        raise RuntimeError('network-disabled')
    req = Request(url, headers={'User-Agent':'TPA-HQ-v4-local-preview/4.0.8 (tpaprojectuk@gmail.com)',
                                'Accept':'*/*'})
    with urlopen(req, timeout=9, context=certificate_context()) as result:
        if result.status != 200:
            raise RuntimeError('source-http-' + str(result.status))
        raw = result.read(limit+1)
        if len(raw) > limit or not raw:
            raise ValueError('empty-or-oversized-resource')
        # Vector PBFs are sometimes delivered already gzip-compressed despite
        # the client not sending Accept-Encoding:gzip. urllib does not decode
        # Content-Encoding by itself. Serve bounded uncompressed protobuf so
        # the browser/worker can decode it without a missing gzip header.
        if raw.startswith(b'\x1f\x8b'):
            dec=zlib.decompressobj(16+zlib.MAX_WBITS)
            uncompressed=dec.decompress(raw, 8_000_001)
            if not dec.eof or dec.unconsumed_tail or len(uncompressed)>8_000_000:
                raise ValueError('oversized-gzip-resource')
            raw=uncompressed
        return raw


LOOPBACK = build_opener(ProxyHandler({}))  # engine calls never go through any proxy


def engine_get(comp, path, limit, timeout):
    """Bounded read from this socket's engine on 127.0.0.1. Raises on any problem."""
    with LOOPBACK.open(ENGINES[comp] + path, timeout=timeout) as res:
        if res.status != 200:
            raise RuntimeError('engine-http-' + str(res.status))
        raw = res.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('engine-oversized')
        return raw, res.headers.get('Content-Type', '')


BOARD_STATES = ('ontime', 'late', 'cancelled', 'unknown')
ROUTE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 &+./-]{0,11}')
BUS_ID = re.compile(r'bus:[A-Za-z0-9:_.-]{1,124}')


def clean_board(b):
    """4.52.0: a station board from an engine, kept to plain text in a fixed shape (or None).
    {updated, alerts:[text], tabs:[{name, empty, rows:[{time, place, via, platform, operator, status, state, reason,
    calls:[{name, time, expected}]}]}]}
    4.53.0 (bus stop boards): also {about, routes:[route]} for the board, and for a row its route number, whether it is
    live, and the live bus (its id and where it is) for "Show on map"."""
    if not isinstance(b, dict) or not isinstance(b.get('tabs'), list):
        return None
    def t(v, n):
        return re.sub(r'\s+', ' ', v).strip()[:n] if isinstance(v, str) else ''
    def hm(v):
        v = t(v, 5)
        return v if re.fullmatch(r'[0-2]\d:[0-5]\d', v) else ''
    tabs = []
    for tab in b['tabs'][:3]:
        if not isinstance(tab, dict) or not t(tab.get('name'), 20):
            continue
        rows = []
        for r in (tab.get('rows') if isinstance(tab.get('rows'), list) else [])[:60]:   # 4.54.0: a bus station has more (was 20)
            if not isinstance(r, dict) or not hm(r.get('time')) or not t(r.get('place'), 80):
                continue
            row = {'time': hm(r['time']), 'place': t(r['place'], 80),
                   'state': r.get('state') if r.get('state') in BOARD_STATES else 'unknown'}
            for k, n in (('via', 60), ('platform', 4), ('operator', 40), ('status', 20), ('reason', 160)):
                if t(r.get(k), n):
                    row[k] = t(r[k], n)
            calls = []
            for c in (r.get('calls') if isinstance(r.get('calls'), list) else [])[:60]:
                if isinstance(c, dict) and t(c.get('name'), 60):
                    item = {'name': t(c['name'], 60)}
                    if hm(c.get('time')):
                        item['time'] = hm(c['time'])
                    if t(c.get('expected'), 20):
                        item['expected'] = t(c['expected'], 20)
                    calls.append(item)
            if calls:
                row['calls'] = calls
            if isinstance(r.get('route'), str) and ROUTE_NAME.fullmatch(r['route'].strip()):
                row['route'] = r['route'].strip()
            if r.get('live') is True:
                row['live'] = True
            lat, lon = r.get('lat'), r.get('lon')
            if isinstance(r.get('bus'), str) and BUS_ID.fullmatch(r['bus']) and \
                    all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)) and 49 <= lat <= 61.5 and -9 <= lon <= 2.5:
                row.update(bus=r['bus'], lat=float(lat), lon=float(lon))
            rows.append(row)
        tabs.append({'name': t(tab['name'], 20), 'empty': t(tab.get('empty'), 80), 'rows': rows})
    if not tabs:
        return None
    out = {'tabs': tabs, 'alerts': [t(a, 400) for a in (b.get('alerts') if isinstance(b.get('alerts'), list) else [])[:5] if t(a, 400)]}
    if hm(b.get('updated')):
        out['updated'] = hm(b['updated'])
    if t(b.get('about'), 120):
        out['about'] = t(b['about'], 120)
    routes = [r.strip() for r in (b.get('routes') if isinstance(b.get('routes'), list) else [])[:60]
              if isinstance(r, str) and ROUTE_NAME.fullmatch(r.strip())]
    if routes:
        out['routes'] = routes
    return out


def engine_manifest_entry(comp):
    try:
        raw, _ = engine_get(comp, '/status', 32768, 1.5)
        data = json.loads(raw)
        health = data.get('health') if isinstance(data, dict) else None
        status = health if health in ('ready', 'stale', 'unavailable') else 'unavailable'
        notice = data.get('notice') if isinstance(data.get('notice'), str) else ''
    except Exception:
        status, notice = 'unavailable', 'Engine not running'
    entry = {'id': comp, 'status': status, 'live': True}
    if notice:
        entry['notice'] = notice[:80]
    return entry


def bbox_query(query):
    """Only four plain numbers are passed on to an engine."""
    values = {}
    for part in query.split('&'):
        k, _, v = part.partition('=')
        if k in ('west', 'south', 'east', 'north') and re.fullmatch(r'-?\d{1,3}(\.\d{1,7})?', v):
            values[k] = v
    if len(values) != 4:
        return ''
    # 4.85.0 (Rob 18 Nov 13:20, the plane Easter egg): one more plain flag may go along - world=1, "this visitor has
    # unlocked planes beyond the UK". Engines that don't know it ignore it.
    flag = '&world=1' if re.search(r'(?:^|&)world=1(?:&|$)', query or '') else ''
    return '?' + '&'.join(f'{k}={values[k]}' for k in ('west', 'south', 'east', 'north')) + flag


# 4.13.0 (owner 1 Nov 21:00 load test: 25 viewers fine, 50 too many; 3 Nov "lets go" on shared areas):
# viewers looking at roughly the same place share one answer. Each request's area is widened to a fixed grid
# (cells about a quarter of the view, so nearby viewers land on the same area), and an engine's answer for
# that area is kept for SHARE_SECONDS and handed to everyone who asks for it; if several ask at once, the
# engine is asked once and the others wait for that answer. Generic: HQ knows nothing about any transport.
SHARE_SECONDS = 4.0
SHARED: dict = {}
INFLIGHT: dict = {}
SHARED_LOCK = threading.Lock()
SHARE_STATS = {'asked': 0, 'shared': 0}


CELL_SIZES = {2.0 ** -k for k in range(0, 11)} | {2.0, 4.0}     # 1/1024 .. 4 degrees, as the formula below gives
# 4.28.0 (launch list A1): the launch switch. Off (the default, and always on the password test site): no answer is
# labelled shareable. On: answers for a page's shared square may be kept by Cloudflare for SHARE_SECONDS, and pictures
# and fonts for a day - but never anything that came with a password (Authorization header).
PUBLIC_CACHE = os.environ.get('TPA_HQ_PUBLIC_CACHE', '') == '1'
MEDIA_KEEP = ('.webp', '.jpg', '.png', '.woff', '.woff2', '.mp3')


def current_slot(query):
    """4.28.0: the page's 4-second time slot (t) is this one or a neighbour - only then may the answer be kept."""
    m = re.search(r'(?:^|&)t=(\d{1,12})(?:&|$)', query or '')
    return bool(m) and abs(int(m.group(1)) - int(time.time() / SHARE_SECONDS)) <= 2


def area_query(query):
    """The request's view widened to the shared grid; '' if it has no proper view."""
    q = bbox_query(query)
    if not q:
        return ''
    v = dict(part.split('=') for part in q[1:].split('&'))
    w, s, e, n = (float(v[k]) for k in ('west', 'south', 'east', 'north'))
    flag = '&world=1' if v.get('world') == '1' else ''      # 4.85.0: kept apart in the shared answers too
    if not (s < n and w < e):
        return q
    g = min(4.0, max(1 / 1024, 2.0 ** math.floor(math.log2(max(n - s, 1e-4) / 4))))
    # 4.28.0: a page that has already rounded its view to our squares says which size (cell); keep it exactly
    # (allowing for the 4 decimal places it sends), so the same question is the same area - for Cloudflare too
    cell = re.search(r'(?:^|&)cell=([0-9.]{1,14})(?:&|$)', query or '')
    tol = 0.0
    if cell:
        try:
            c = float(cell.group(1))
            if c in CELL_SIZES:
                g, tol = c, 0.2
        except ValueError:
            pass
    gl = g * 2                                    # a degree of longitude is about half as long here
    s2, n2 = max(-85.0, math.floor(s / g + tol) * g), min(85.0, math.ceil(n / g - tol) * g)
    w2, e2 = max(-180.0, math.floor(w / gl + tol) * gl), min(180.0, math.ceil(e / gl - tol) * gl)
    # 4.55.0 (owner 13 Nov 13:23, go-live rule 1): the square size goes to the engine too, so an engine can answer from
    # squares it has already prepared (the bus engine does from 0.15.0); engines that don't know cell= ignore it
    return f'?west={w2:.4f}&south={s2:.4f}&east={e2:.4f}&north={n2:.4f}&cell={g!r}' + flag


LONG_SHARE_SECONDS = 8.0   # 4.50.0: the most an answer is shared for


def keep_seconds(raw):
    """4.41.0 (owner 11 Nov 14:22: York roadworks took about 8 s to appear): an answer that says "ask again soon"
    (still loading, nextPollAfterMs under 4 s) is shared for half that time, not the full SHARE_SECONDS - otherwise the
    page's quick second look got the same "still loading" answer back.
    4.50.0 (health check 12 Nov, idea 2): the bus engine was the limit at 100 visitors while pages only ask for buses
    every 10 s, so an answer is shared for up to 80% of the engine's own asking pace, at most 8 s (buses and trains 8 s;
    planes, asked every 5 s, stay at 4 s)."""
    m = re.search(rb'"nextPollAfterMs":\s*(\d{1,6})', raw[:4096] if isinstance(raw, (bytes, bytearray)) else b'')
    if not m:
        return SHARE_SECONDS
    poll = int(m.group(1)) / 1000
    if poll < SHARE_SECONDS:
        return max(0.5, poll / 2)
    return min(LONG_SHARE_SECONDS, max(SHARE_SECONDS, round(poll * 0.8, 1)))


def shared_snapshot(comp, query, fetch):
    """One engine answer per (layer, area) every SHARE_SECONDS, shared by everyone asking for it."""
    key = (comp, area_query(query))
    with SHARED_LOCK:
        SHARE_STATS['asked'] += 1
        hit = SHARED.get(key)
        if hit and hit[0] > time.monotonic():
            SHARE_STATS['shared'] += 1
            return hit[1]
        ev = INFLIGHT.get(key)
        owner = ev is None
        if owner:
            ev = INFLIGHT[key] = threading.Event()
    if not owner:
        ev.wait(7)
        with SHARED_LOCK:
            hit = SHARED.get(key)
            if hit:
                SHARE_STATS['shared'] += 1
                return hit[1]
        raise RuntimeError('shared-answer-missing')
    try:
        raw = fetch(key[1])
        with SHARED_LOCK:
            now = time.monotonic()
            SHARED[key] = (now + keep_seconds(raw), raw)
            if len(SHARED) > 600:
                for k in [k for k, (t, _) in SHARED.items() if t <= now] or list(SHARED)[:200]:
                    SHARED.pop(k, None)
        return raw
    finally:
        with SHARED_LOCK:
            INFLIGHT.pop(key, None)
        ev.set()


def sanitized_error(exc):
    if isinstance(exc, HTTPError):return f'source-http-{exc.code}'
    if isinstance(exc, (ssl.SSLError, ssl.SSLCertVerificationError)):return 'certificate-error'
    if isinstance(exc, (TimeoutError, socket.timeout)):return 'source-timeout'
    if isinstance(exc, URLError):
        reason=exc.reason
        if isinstance(reason,socket.gaierror) or 'getaddrinfo' in str(reason).lower(): return 'dns-error'
        if isinstance(reason,ssl.SSLError):return 'certificate-error'
        if isinstance(reason,(TimeoutError,socket.timeout)):return 'source-timeout'
        return 'connection-error'
    if isinstance(exc,RuntimeError) and str(exc)=='network-disabled':return 'network-disabled'
    if isinstance(exc,ValueError):return 'invalid-source-resource'
    return 'connection-error'


def record_error(category, code, path='none'):
    with LOCK:
        ERRORS[category] = {'code':code, 'whenUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        LAST_FAILURE.update(path=path[:160],category=category,code=code)


def resource_kind(relative):
    if relative.startswith('/styles/'):return 'style'
    if PLANET_META.fullmatch(relative):return 'tilejson'
    if PLANET_TILE.fullmatch(relative):return 'vector'
    if relative.startswith('/sprites/'):return 'sprite'
    if relative.startswith('/natural_earth/'):return 'naturalEarth'
    if relative.startswith('/fonts/'):return 'font'
    return 'rejected'


def allowed_map_path(path):
    """No proxying arbitrary URLs or encoded traversal/schemes/supplier calls."""
    if '%2f' in path.lower() or '%5c' in path.lower() or '\\' in path or '//' in path:
        return False
    raw=unquote(path)
    if '..' in raw or '?' in raw or '#' in raw or len(raw)>240:
        return False
    return bool(OPENFREEMAP_PATH.fullmatch(raw) or PLANET_META.fullmatch(raw) or PLANET_TILE.fullmatch(raw))


def rewrite_style(raw):
    """Change only official OpenFreeMap absolute hosts to our own origin."""
    obj=json.loads(raw)
    def visit(value):
        if isinstance(value,str):
            for prefix in ('https://tiles.openfreemap.org','http://tiles.openfreemap.org'):
                if value.startswith(prefix+'/'):
                    return '/basemap/openfreemap' + value[len(prefix):]
            return value
        if isinstance(value,list):return [visit(v) for v in value]
        if isinstance(value,dict):return {k:visit(v) for k,v in value.items()}
        return value
    return json.dumps(visit(obj),separators=(',',':')).encode('utf-8')


def local_request_origin(host, server_port, bound_address='127.0.0.1'):
    """Accept only this server's exact listen address and port, never arbitrary Host.

    The returned URL is used in MapLibre worker resource paths; it MUST be
    absolute and use the tablet-visible origin rather than localhost.
    """
    if bound_address == '127.0.0.1':
        if PUBLIC_HOST and host == PUBLIC_HOST:
            return PUBLIC_ORIGIN
        if host in EXTRA_ORIGINS:
            return EXTRA_ORIGINS[host]
        match=re.fullmatch(r'(127\.0\.0\.1|localhost):([0-9]{1,5})',host)
        if match is None or int(match.group(2))!=server_port:return None
        return f'http://{match.group(1)}:{server_port}'
    # A LAN preview binds exactly one RFC1918 IPv4 interface, not all adapters.
    if host != f'{bound_address}:{server_port}':return None
    return f'http://{bound_address}:{server_port}'


# 4.75.0 (Rob 18 Nov 2026 00:44-00:47: an anonymous suggestions box): what visitors send is kept on the server, one line
# each ({at, kind, text}), never their address. Guards: a hidden field robots fill in, the form open a few seconds, at most
# SUGGEST_PER_HOUR from one visitor (kept in memory only, by a scrambled form of the address Cloudflare/Caddy pass on)
# and SUGGEST_PER_DAY in all. The owner reads them at /suggestions on the password-protected test site only.
SUGGEST_FILE = Path(os.environ.get('TPA_HQ_SUGGEST_FILE') or (Path(tempfile.gettempdir()) / 'tpa-suggestions.jsonl'))
SUGGEST_PER_HOUR, SUGGEST_PER_DAY, SUGGEST_MAX = 5, 300, 1000
SUGGEST_SEEN = {}
SUGGEST_DAY = {'day': '', 'n': 0}
SUGGEST_LOCK = threading.Lock()


def suggest_accept(obj, who, now=None):
    """(status, message): 200 saved, 400 not a suggestion, 429 too many."""
    now = time.time() if now is None else now
    if not isinstance(obj, dict):
        return 400, 'bad'
    text, kind = obj.get('text'), obj.get('kind')
    if obj.get('website'):                       # the hidden field: only robots fill it in - pretend it worked
        return 200, 'ok'
    if not isinstance(text, str) or not isinstance(kind, str) or kind not in ('idea', 'problem', 'other'):
        return 400, 'bad'
    text = ''.join(ch for ch in text if ch == '\n' or (ch.isprintable() and ch not in '\x0b\x0c')).strip()[:SUGGEST_MAX]
    if len(text) < 3:
        return 400, 'bad'
    if not isinstance(obj.get('openMs'), (int, float)) or obj['openMs'] < 2500:   # typed and sent in under 2.5 s: a robot
        return 200, 'ok'
    key = hashlib.sha256(('tpa-suggest|' + (who or '')).encode()).hexdigest()[:16]
    with SUGGEST_LOCK:
        day = time.strftime('%Y-%m-%d', time.gmtime(now))
        if SUGGEST_DAY['day'] != day:
            SUGGEST_DAY.update(day=day, n=0)
        recent = [t for t in SUGGEST_SEEN.get(key, []) if now - t < 3600]
        if len(recent) >= SUGGEST_PER_HOUR or SUGGEST_DAY['n'] >= SUGGEST_PER_DAY:
            return 429, 'too many'
        recent.append(now)
        SUGGEST_SEEN[key] = recent
        if len(SUGGEST_SEEN) > 5000:
            SUGGEST_SEEN.clear()
        SUGGEST_DAY['n'] += 1
        try:
            SUGGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SUGGEST_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'at': time.strftime('%Y-%m-%d %H:%M', time.localtime(now)), 'kind': kind, 'text': text},
                                   ensure_ascii=False) + '\n')
        except OSError:
            return 503, 'not saved'
    return 200, 'ok'


def suggestions_page():
    try:
        lines = SUGGEST_FILE.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    rows = []
    for ln in reversed(lines[-500:]):
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        word = {'idea': 'Idea', 'problem': 'Looks wrong', 'other': 'Other'}.get(o.get('kind'), 'Other')
        rows.append(f"<li><b>{htmllib.escape(word)}</b> <small>{htmllib.escape(str(o.get('at', '')))}</small><br>"
                    f"{htmllib.escape(str(o.get('text', ''))).replace(chr(10), '<br>')}</li>")
    body = ''.join(rows) or '<li>No suggestions yet.</li>'
    return ('<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>TPA suggestions</title><style>body{background:#05080e;color:#edf3f9;font:16px/1.5 system-ui,Arial,sans-serif;'
            'max-width:760px;margin:0 auto;padding:24px}li{margin:0 0 16px;padding:10px 14px;background:#0a111c;border-radius:8px;'
            'list-style:none}small{color:#8597ad}b{color:#ffb21e}ul{padding:0}</style></head><body>'
            f'<h1>Suggestions from visitors ({len(rows)})</h1><p>Newest first. Only you can see this page.</p><ul>{body}</ul></body></html>').encode('utf-8')


# 4.75.1 (after launch, 18 Nov 03:3x: Cloudflare told browsers to keep /assets/hq.js for 4 hours, so a visitor who came
# before an update kept the old page script): the page's own scripts and styles are asked for as ?v=<HQ version>, so every
# update is a new address for browsers and Cloudflare alike. The files themselves are the same; HQ ignores the ?v=.
def versioned_assets(raw):
    tag = ('?v=' + re.sub(r'[^0-9A-Za-z.-]', '', HQ_VERSION)).encode()
    return re.sub(rb'((?:src|href|data-[a-z]+)="/assets/[A-Za-z0-9_./-]+\.(?:js|css))"', lambda m: m.group(1) + tag + b'"', raw)


def allowed_preview_client(client_address, bound_address):
    """No public clients: loopback for legacy tests, or same /24 private LAN."""
    try:
        client=ipaddress.ip_address(client_address)
        bound=ipaddress.ip_address(bound_address)
    except ValueError:return False
    if not isinstance(client,ipaddress.IPv4Address) or not isinstance(bound,ipaddress.IPv4Address):
        return False
    if bound.is_loopback:return client.is_loopback
    return is_rfc1918(bound) and client in ipaddress.ip_network(f'{bound}/24',strict=False)


def is_rfc1918(address):
    private=(ipaddress.ip_network('10.0.0.0/8'),
             ipaddress.ip_network('172.16.0.0/12'),
             ipaddress.ip_network('192.168.0.0/16'))
    return isinstance(address,ipaddress.IPv4Address) and any(address in net for net in private)


def find_lan_ip():
    """Find the PC's LAN address without making a network request."""
    override=os.environ.get('TPA_HQ_LAN_IP','').strip()
    if override:
        try:addr=ipaddress.ip_address(override)
        except ValueError:raise RuntimeError('TPA_HQ_LAN_IP is not valid IPv4')
        if not is_rfc1918(addr):raise RuntimeError('LAN address must be private RFC1918 IPv4')
        return str(addr)
    candidates=[]
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.connect(('1.1.1.1',80))  # UDP connect chooses route; sends NO packets
            candidates.append(sock.getsockname()[0])
    except OSError:pass
    try:candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:pass
    for candidate in candidates:
        try:addr=ipaddress.ip_address(candidate)
        except ValueError:continue
        if is_rfc1918(addr):return str(addr)
    raise RuntimeError('No home LAN IP detected. Connect PC to Wi-Fi/Ethernet first.')


def absolutize_map_urls(raw, origin):
    """Make cached, same-origin basemap URLs suitable for MapLibre workers.

    MapLibre constructs Request(tileURL) inside a worker, where relative URLs
    can have no valid document base. Cache stores ORIGIN-NEUTRAL relative JSON;
    replace only our allowed /basemap/openfreemap/... values at response time.
    This prevents hardcoded development ports being stored in cached TileJSON.
    """
    data=json.loads(raw)
    prefix='/basemap/openfreemap/'
    def visit(value):
        if isinstance(value,str) and value.startswith(prefix):
            return origin + value
        if isinstance(value,list): return [visit(v) for v in value]
        if isinstance(value,dict):return {k:visit(v) for k,v in value.items()}
        return value
    return json.dumps(visit(data),separators=(',',':')).encode('utf-8')


def load_cached_resource(category, key, urls, limit, ttl, json_transform=False):
    filename=hashlib.sha256(key.encode('utf-8')).hexdigest()
    path=CACHE_DIR/category/filename
    try:
        if path.is_file() and time.time()-path.stat().st_mtime<ttl:
            return path.read_bytes()
    except OSError:pass
    now=time.monotonic()
    with LOCK:
        fail=NEGATIVE_CACHE.get(key)
        if fail and now-fail<22:raise RuntimeError('retry-backoff')
        while FREQUENCY and now-FREQUENCY[0]>60:FREQUENCY.popleft()
        if len(FREQUENCY)>=MAX_PER_MINUTE:
            raise RuntimeError('request-rate-limited')
        FREQUENCY.append(now)
    if not UPSTREAM_SLOTS.acquire(timeout=4):raise RuntimeError('server-busy')
    try:
        last=None
        for url in urls:
            try:
                raw=fetch_remote(url,limit)
                if json_transform:raw=rewrite_style(raw)
                path.parent.mkdir(parents=True,exist_ok=True)
                tmp=path.with_name(path.name+'.'+str(threading.get_ident())+'.tmp')
                tmp.write_bytes(raw)
                os.replace(tmp,path)
                return raw
            except (HTTPError,URLError,ssl.SSLError,TimeoutError,ValueError,RuntimeError,OSError) as exc:
                last=exc
        with LOCK:NEGATIVE_CACHE[key]=time.monotonic()
        raise last if last else RuntimeError('no-source')
    finally:UPSTREAM_SLOTS.release()


class HQHandler(SimpleHTTPRequestHandler):
    server_version = 'TPA-HQ-OpenFreeMap-Leaflet-MapLibre/4.0.8'
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def end_headers(self):
        for name,value in (
            ('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),
            ('Referrer-Policy','same-origin'),('Cross-Origin-Resource-Policy','same-origin'),
            ('Permissions-Policy','geolocation=(), camera=(), microphone=()'),
            ('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             f"img-src 'self' data: blob:{' '+OFM if BASEMAP=='direct' else ''}{' '+SAT_HOSTS if SAT_KEY else ''}; font-src 'self'; manifest-src 'self'; "
             f"connect-src 'self'{' '+OFM if BASEMAP=='direct' else ''}; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")):
            self.send_header(name,value)
        # 4.1.0: the page's own files are re-checked on every visit (a quick "not modified"
        # answer if unchanged), so an update shows straight away instead of up to 30 min later.
        if urlsplit(self.path).path in PUBLIC and not getattr(self,'_cache_set',False):
            here=urlsplit(self.path).path
            if PUBLIC_CACHE and (here.endswith(MEDIA_KEEP) or here=='/assets/planes/shapes.json') and not self.headers.get('Authorization'):   # 4.84.0: shapes kept a day like pictures (the page asks with ?v=)
                self.send_header('Cache-Control','public, max-age=86400')     # 4.28.0: pictures, fonts, sounds
            else:
                self.send_header('Cache-Control','no-cache')
        super().end_headers()
    def reply(self,status,raw,mime,cache=None):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control',cache or ('private, max-age=1800' if status==200 else 'no-store'));self._cache_set=True
        # 4.13.3 (owner 3 Nov 19:11: his computer's clock was 6.5 s slow, so replayed buses only hopped): the server's
        # time goes with every answer; the page moves vehicles by it, never by the viewer's own clock
        self.send_header('X-TPA-Now',f'{time.time()*1000:.0f}')
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def json_reply(self,status,obj):self.reply(status,json.dumps(obj,separators=(',',':')).encode('utf8'),'application/json; charset=utf-8')
    def do_GET(self):
        split=urlsplit(self.path);path=split.path
        bound_address=self.server.server_address[0]
        origin=local_request_origin(self.headers.get('Host',''),self.server.server_port,bound_address)
        if not allowed_preview_client(self.client_address[0],bound_address) or origin is None:
            return self.send_error(403,'Development preview limited to home LAN')
        # 4.56.0: on the server Caddy hands out /tiles/ itself (published bus tiles); this is only for builders' tests
        if path.startswith('/tiles/') and TILES_DIR:
            rel=path[len('/tiles/'):]
            if not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',rel) or '..' in rel:return self.send_error(404)
            f=Path(TILES_DIR)/rel
            if not f.is_file():return self.send_error(404)
            raw=f.read_bytes();self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','public, max-age=10');self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if path=='/hq-map-status':
            return self.json_reply(200,{'version':HQ_VERSION,'renderer':'Leaflet 1.9.4 + MapLibre GL 5.24.0 + bridge 0.1.4',
                'source':'OpenFreeMap','mode':'MOCK-icon-review-only','networkDisabled':DISABLE_MAP_NETWORK,'photos':PHOTOS.status(),'planePhotos':PLANE_PHOTOS.status(),
                'upstreamErrors':dict(ERRORS),'lastFailedResource':dict(LAST_FAILURE),'resourceCounts':dict(RESOURCE_COUNTS),'browser':dict(BROWSER),'browserHistory':list(BROWSER_HISTORY),'vendorCached':{k:(CACHE_DIR/'vendor'/hashlib.sha256(k.encode()).hexdigest()).is_file() for k in VENDOR},'mapLibreAssetCached':(CACHE_DIR/'vendor'/hashlib.sha256('/assets/vendor/maplibre-gl.js'.encode()).hexdigest()).is_file()})
        if path=='/api/v4/photo':   # 4.106.0: a photo of this bus? found / none / looking (ask again shortly)
            ans=PHOTOS.answer((parse_qs(split.query).get('reg') or [''])[0][:20])
            return self.reply(200,json.dumps(ans,separators=(',',':')).encode('utf8'),'application/json; charset=utf-8',
                              'no-store' if ans['state']=='looking' else 'public, max-age=3600')
        if path=='/api/v4/planephoto':   # 4.108.0: a photo of this plane? (registration + the type the box shows)
            q=parse_qs(split.query)
            ans=PLANE_PHOTOS.answer((q.get('reg') or [''])[0][:12],(q.get('type') or [''])[0][:80],(q.get('code') or [''])[0][:6],(q.get('op') or [''])[0][:40])
            return self.reply(200,json.dumps(ans,separators=(',',':')).encode('utf8'),'application/json; charset=utf-8',
                              'no-store' if ans['state']=='looking' else 'public, max-age=3600')
        if path.startswith('/api/v4/planephoto/img/'):
            got=PLANE_PHOTOS.image(path[len('/api/v4/planephoto/img/'):])
            if not got:return self.send_error(404)
            return self.reply(200,got[0],got[1],'public, max-age=86400')
        if path.startswith('/api/v4/photo/img/'):
            got=PHOTOS.image(path[len('/api/v4/photo/img/'):])
            if not got:return self.send_error(404)
            return self.reply(200,got[0],got[1],'public, max-age=86400')
        if path=='/api/v4/geoff':   # 4.89.0: today's Geoff buses (shared by everyone; a minute in caches)
            with GEOFF.lock:ans=GEOFF.answer_locked()
            return self.reply(200,json.dumps(ans,separators=(',',':')).encode('utf8'),'application/json; charset=utf-8','public, max-age=60')
        if path=='/api/v4/manifest':
            def entry(i):
                if i in PARKED:return {'id':i,'status':'unavailable','parked':True}
                if i in ENGINES:return engine_manifest_entry(i)
                return {'id':i,'status':'ready' if FIXTURE_DIR and (Path(FIXTURE_DIR)/(i+'.json')).exists() else 'not_installed'}
            return self.json_reply(200,{'hq':HQ_VERSION+('-LIVE-TEST' if ENGINES else '-ICON-REVIEW-MOCK'),'contractVersion':'1.0',
                'transportFeedsEnabled':bool(ENGINES),'mockIconReview':not ENGINES,
                'components':[entry(i) for i in COMPONENTS]})
        if path.startswith('/api/v4/components/'):
            parts=path.split('/')
            # Overlay picture tiles from an engine: /api/v4/components/<id>/tiles/<z>/<x>/<y>.png
            if len(parts)==9 and parts[:4]==['','api','v4','components'] and parts[4] in ENGINES and parts[4] not in PARKED and parts[5]=='tiles' \
                    and parts[6].isdigit() and parts[7].isdigit() and re.fullmatch(r'\d{1,7}\.png',parts[8]) and len(parts[6])<=2:
                try:
                    raw,_=engine_get(parts[4],'/tiles/'+'/'.join(parts[6:9]),1_000_000,15)
                except Exception:
                    raw=b''
                if raw and not raw.startswith(b'\x89PNG\r\n\x1a\n'):raw=b''
                if not raw:
                    self.send_response(204);self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(raw)))
                # 4.1.12: each picture's address carries its 10-minute refresh stamp, so the browser keeps it
                self.send_header('Cache-Control','private, max-age=600');self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
                return
            # 4.4.0 (owner 1 Nov 13:42: a clicked bus should show "the start and end destination, then which stop
            # is next"): more details about one vehicle, asked from its own engine only when it is clicked.
            # Generic: plain rows of label/value text; HQ knows nothing about buses.
            # 4.52.0 (owner 12 Nov 17:25, "search by name"): an engine may answer searches (stations by name or code).
            # Generic: up to 8 results of id, label, short code and position; HQ knows nothing about stations.
            if len(parts)==6 and parts[:4]==['','api','v4','components'] and parts[4] in ENGINES and parts[4] not in PARKED and parts[5]=='search':
                q=(parse_qs(split.query).get('q') or [''])[0]
                if len(q)>60 or any(ord(c)<32 or ord(c)==127 for c in q):
                    return self.json_reply(400,{'error':'bad search'})
                try:
                    raw,_=engine_get(parts[4],'/search?'+urlencode({'q':q}),65536,3)
                    d=json.loads(raw)
                    out=[]
                    for r in (d.get('results') or [])[:8] if isinstance(d,dict) else []:
                        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not isinstance(r.get('label'),str):continue
                        if not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}',r['id']):continue
                        lat,lon=r.get('lat'),r.get('lon')
                        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in (lat,lon)) or not (49<=lat<=61.5 and -9<=lon<=2.5):continue
                        item={'id':r['id'],'label':r['label'][:80],'lat':float(lat),'lon':float(lon)}
                        if isinstance(r.get('badge'),str) and re.fullmatch(r'[A-Za-z0-9]{1,8}',r['badge']):item['badge']=r['badge']
                        out.append(item)
                    return self.reply(200,json.dumps({'component':parts[4],'results':out},separators=(',',':')).encode(),
                                      'application/json; charset=utf-8','public, max-age=0, s-maxage=300' if PUBLIC_CACHE and not self.headers.get('Authorization') else 'no-store')
                except Exception:
                    return self.json_reply(503,{'component':parts[4],'results':[]})
            if len(parts)==6 and parts[:4]==['','api','v4','components'] and parts[4] in ENGINES and parts[4] not in PARKED and parts[5]=='details':
                want=(parse_qs(split.query).get('id') or [''])[0]
                if not want or len(want)>140 or any(ord(c)<32 or ord(c)==127 for c in want):
                    return self.json_reply(400,{'error':'bad id'})
                try:
                    raw,_=engine_get(parts[4],'/details?'+urlencode({'id':want}),262144,10)   # 4.52.0: a station board is bigger and may wait for the board service
                    d=json.loads(raw)
                    # 4.33.0 (owner 10 Nov 21:24): up to 24 rows; a row marked 'more' goes behind "More details"
                    # 4.95.0: up to 48, and a row marked 'pro' (the enthusiasts' extras: shown only on /planes)
                    rows=[{'label':str(r.get('label',''))[:30],'value':str(r.get('value',''))[:120],**({'more':True} if r.get('more') is True else {}),
                           **({'pro':True} if r.get('pro') is True else {})}
                          for r in (d.get('rows') or [])[:48] if isinstance(r,dict)]
                    body={'component':parts[4],'id':want,'rows':rows}
                    if isinstance(d.get('note'),str):body['note']=d['note'][:160]
                    if isinstance(d.get('picture'),str) and re.fullmatch(r'[a-z0-9-]{1,30}',d['picture']):body['picture']=d['picture']   # 4.16.0
                    # 4.43.0 (owner 11 Nov 15:43): TPA's own picture of a plane type, and its name
                    if isinstance(d.get('photo'),str) and re.fullmatch(r'[a-z0-9-]{1,30}',d['photo']):
                        body['photo']=d['photo']
                        if isinstance(d.get('photoName'),str):body['photoName']=d['photoName'][:60]
                    # 4.95.0 (air 0.11.0): a plane's last 10 minutes, [lon, lat] pairs (the planes page draws it)
                    if isinstance(d.get('trail'),list):
                        tr=[[round(float(q[0]),5),round(float(q[1]),5)] for q in d['trail'][:200]
                            if isinstance(q,list) and len(q)==2 and all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in q)
                            and abs(q[0])<=180 and abs(q[1])<=90]
                        if len(tr)>=2:body['trail']=tr
                    # 4.23.0 (owner 6 Nov 09:40): generic journey - one sentence and the stops in order, each done / here / due
                    if isinstance(d.get('summary'),str) and d['summary'].strip():body['summary']=d['summary'][:200]
                    for k in ('doing','progress'):                     # 4.33.0: what a plane is doing, how far along it is
                        if isinstance(d.get(k),str) and d[k].strip():body[k]=d[k][:120]
                    if isinstance(d.get('stops'),list):
                        stops=[]
                        for s in d['stops'][:120]:
                            if not isinstance(s,dict) or s.get('state') not in ('done','here','due') or not isinstance(s.get('name'),str) or not s['name'].strip():continue
                            item={'name':s['name'][:80],'state':s['state']}   # 4.32.0: 80 (owner 14:17: names were cut mid-word)
                            for k in ('time','expected'):
                                if isinstance(s.get(k),str) and re.fullmatch(r'[0-2]\d:[0-5]\d',s[k]):item[k]=s[k]
                            stops.append(item)
                        if len(stops)>=2:body['stops']=stops
                    board=clean_board(d.get('board'))   # 4.52.0: a live station board (departures, arrivals, alerts)
                    if board:body['board']=board
                    return self.json_reply(200,body)
                except Exception:
                    return self.json_reply(503,{'component':parts[4],'id':want,'rows':[],'note':'Details not available right now.'})
            if len(parts)==6 and parts[:4]==['','api','v4','components'] and parts[4] in COMPONENTS and parts[5] in ('status','snapshot'):
                comp=parts[4]
                if comp in PARKED:
                    return self.json_reply(503,{'component':comp,'health':'unavailable','parked':True,'message':'Coming soon; parked by owner; no data'})
                if comp in ENGINES and parts[5]=='snapshot':
                    def fetch(area,comp=comp):
                        raw,_=engine_get(comp,'/snapshot'+area,1_200_000,6)
                        frame=json.loads(raw)
                        if not isinstance(frame,dict) or frame.get('component')!=comp or frame.get('contractVersion')!='1.0':
                            raise ValueError('engine-contract')
                        return raw
                    try:
                        raw=shared_snapshot(comp,split.query,fetch)      # 4.13.0: one answer per area, shared
                        share=PUBLIC_CACHE and not self.headers.get('Authorization') and re.search(r'(?:^|&)cell=',split.query or '') \
                            and current_slot(split.query)
                        return self.reply(200,raw,'application/json; charset=utf-8',
                                          f'public, max-age=0, s-maxage={max(1, int(keep_seconds(raw)))}' if share else 'no-store')   # 4.28.0
                    except Exception:
                        return self.json_reply(503,{'component':comp,'health':'unavailable','message':'Engine not running or not responding'})
                if FIXTURE_DIR and parts[5]=='snapshot':
                    fixture=Path(FIXTURE_DIR)/(comp+'.json')
                    if fixture.is_file() and fixture.stat().st_size<=1200000:
                        try:return self.json_reply(200,json.loads(fixture.read_text(encoding='utf-8')))
                        except (OSError,ValueError):pass
                return self.json_reply(503,{'component':comp,'health':'not_installed','message':'Component absent; no live data'})
            return self.send_error(404,'No such component')
        if path in VENDOR:
            urls,mime=VENDOR[path]
            try:
                raw=load_cached_resource('vendor',path,urls,5_000_000,30*86400)
                # Fail closed on empty or obvious HTML error pages cached as JS.
                if raw.lstrip()[:20].lower().startswith((b'<!doctype html',b'<html')):
                    raise ValueError('HTML where pinned asset expected')
                return self.reply(200,raw,mime)
            except Exception as exc:
                record_error('vendor',sanitized_error(exc),path)
                return self.send_error(502,'Pinned map asset unavailable; check hq-map-status')
        if path=='/hq-basemap':
            via='/basemap/openfreemap/styles/liberty'
            ans={'mode':BASEMAP,'style':OFM+'/styles/liberty' if BASEMAP=='direct' else via,'fallback':via if BASEMAP=='direct' else None}
            if SAT_KEY:ans['sat']=sat_config()   # 4.110.0: only when the server has the Esri key
            return self.json_reply(200,ans)
        if path.startswith('/basemap/openfreemap/'):
            relative=path[len('/basemap/openfreemap'):]
            if split.query or not allowed_map_path(relative):
                with LOCK: RESOURCE_COUNTS['rejected']+=1
                record_error('blocked-map-path','disallowed-resource',relative[:160])
                return self.send_error(404,'Map resource not allowed')
            with LOCK: RESOURCE_COUNTS[resource_kind(relative)]+=1
            # The public instance supports style URLs ending in /styles/liberty and
            # /styles/liberty/style.json; leave both available through proxy.
            is_json=relative.endswith('.json') or relative in ('/styles/liberty','/styles/bright','/styles/dark','/styles/positron','/styles/fiord') or bool(PLANET_META.fullmatch(relative))
            mime='application/json; charset=utf-8' if is_json else MIME_BY_EXT.get(Path(relative).suffix,'application/octet-stream')
            try:
                raw=load_cached_resource('openfreemap',relative,[f'https://tiles.openfreemap.org{relative}'],
                    3_000_000 if is_json else 1_600_000,
                    3600 if is_json else 14*86400,json_transform=is_json)
                # 4.0.7 incident: JSON's root-relative tile templates fail
                # when MapLibre workers call new Request('/basemap/...').
                # Respond with exact same-origin absolute URLs, not remote hosts.
                if is_json:raw=absolutize_map_urls(raw,origin)
                if not is_json and relative.endswith('.png') and not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('invalid png')
                return self.reply(200,raw,mime)
            except Exception as exc:
                record_error('openfreemap',sanitized_error(exc),relative[:160])
                return self.send_error(502,'Map resource unavailable; check hq-map-status')
        # 4.75.0: the owner's list of suggestions - only on the password-protected test site's address
        if path=='/suggestions':
            if not PUBLIC_HOST or self.headers.get('Host','')!=PUBLIC_HOST:return self.send_error(404,'Not here')
            raw=suggestions_page()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self._cache_set=True;self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if path in ('/','/planes') and DEFAULT_VIEW=='user':   # 4.94.0: the planes page too
            raw=app_page(versioned_assets((ROOT/'index.html').read_bytes().replace(b'<html ',b'<html data-default-view="user" ',1)),split.query,path)
            # 4.76.0 (Rob 18 Nov 03:47 "i cant find anything on the map"; chose "Whole of Britain"): on the real site
            # addresses visitors start on the whole UK, not on Beverley (builders' tests on 127.0.0.1 keep Beverley)
            host=self.headers.get('Host','')
            if host and (host==PUBLIC_HOST or host in EXTRA_ORIGINS):raw=raw.replace(b'<html ',b'<html data-start="uk" ',1)
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-cache');self._cache_set=True;self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if path in ('/','/user','/kids','/full','/planes','/index.html'):   # 4.75.1: the page itself, with version-tagged scripts
            raw=app_page(versioned_assets((ROOT/'index.html').read_bytes()),split.query,path)
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-cache');self._cache_set=True;self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        if path in PUBLIC:
            self.path='/index.html' if path in ('/','/user','/kids','/full','/planes') else path
            return super().do_GET()
        return self.send_error(404,'HQ standard-only resource allowlist')
    def do_POST(self):
        # Minimal local-only diagnostics: fixed error codes, no user-generated
        # text, no coordinates, no credentials and no content persistence.
        if urlsplit(self.path).path == '/api/v4/suggest':
            return self.post_suggest()
        if urlsplit(self.path).path != '/hq-browser-diagnostic':
            return self.send_error(405,'Read-only HQ')
        bound_address=self.server.server_address[0]
        if not allowed_preview_client(self.client_address[0],bound_address) or local_request_origin(self.headers.get('Host',''),self.server.server_port,bound_address) is None:
            return self.send_error(403,'Home LAN preview only')
        if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
            return self.send_error(415,'JSON required')
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>400:return self.send_error(413,'Diagnostic too large')
            obj=json.loads(self.rfile.read(n))
            code=obj.get('code') if isinstance(obj,dict) else None
            if code not in ('library-missing','library-resource-error','script-runtime-error',
                             'webgl-unsupported','map-constructor-error','map-style-error',
                             'map-vector-error','map-resource-error','map-loaded','bridge-missing',
                             'vector-layer-attached','map-resource-request-failed','map-canvas-refit'):
                return self.send_error(400,'Unknown diagnostic code')
            stage=obj.get('stage','none')
            detail=obj.get('detail','none')
            if not isinstance(stage,str) or not re.fullmatch(r'[a-z0-9-]{1,40}',stage):return self.send_error(400,'Invalid stage')
            if not isinstance(detail,str) or not re.fullmatch(r'[A-Za-z0-9 .,;:_()!?/+-]{1,180}',detail):return self.send_error(400,'Invalid detail')
        except (TypeError,ValueError):return self.send_error(400,'Invalid diagnostic')
        with LOCK:
            BROWSER['lastCode']=code
            BROWSER['lastStage']=stage
            BROWSER['lastDetail']=detail
            BROWSER['count']=min(100000,BROWSER['count']+1)
            BROWSER_HISTORY.append({'code':code,'stage':stage,'detail':detail})
        return self.json_reply(200,{'accepted':True})
    do_PUT=do_DELETE=do_PATCH=lambda self:self.send_error(405,'Read-only HQ')
    def post_suggest(self):   # 4.75.0
        bound_address=self.server.server_address[0]
        if not allowed_preview_client(self.client_address[0],bound_address) or local_request_origin(self.headers.get('Host',''),self.server.server_port,bound_address) is None:
            return self.send_error(403,'Not here')
        if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
            return self.json_reply(415,{'ok':False})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>6000:return self.json_reply(413,{'ok':False})
            obj=json.loads(self.rfile.read(n).decode('utf-8'))
        except (TypeError,ValueError,UnicodeDecodeError):return self.json_reply(400,{'ok':False})
        who=(self.headers.get('CF-Connecting-IP') or (self.headers.get('X-Forwarded-For') or '').split(',')[0]).strip()
        status,_=suggest_accept(obj,who)
        return self.json_reply(status,{'ok':status==200})
    def do_HEAD(self):return self.send_error(405,'Read-only HQ')
    def log_message(self,fmt,*args):
        if not self.path.startswith('/basemap/openfreemap/'):super().log_message(fmt,*args)


class HQServer(ThreadingHTTPServer):
    """4.15.2 (owner 3 Nov 23:10, 250-viewer load test: the website server could not connect to HQ in 3 s): let up to
    1,024 connections wait to be let in (Python's default is 5; each is answered in milliseconds)."""
    request_queue_size = 1024
    daemon_threads = True


def serve(port=8775, lan=False):
    address=find_lan_ip() if lan else '127.0.0.1'
    threading.Thread(target=geoff_loop,daemon=True).start()   # 4.89.0
    threading.Thread(target=photo_warm_loop,daemon=True).start()   # 4.106.1: bus photos looked up ahead of clicks
    if 'air' in ENGINES and 'air' not in PARKED:threading.Thread(target=plane_warm_loop,daemon=True).start()   # 4.108.1
    with HQServer((address,port),HQHandler) as server:
        url=f'http://{address}:{port}/'
        print('\n'+'='*68,flush=True)
        print(f'TPA HQ {HQ_VERSION} — '+('TEST VERSION WITH LIVE LAYERS, NOT GOLDEN' if ENGINES else 'MOCK ICON REVIEW, NOT LIVE OR GOLDEN'),flush=True)
        print('On this Windows PC only, open:',flush=True)
        print('     '+url,flush=True)
        if PARKED:print('Parked (shown as coming soon): '+', '.join(sorted(PARKED)),flush=True)
        if ENGINES:print('Live engines connected: '+', '.join(f'{k} ({v})' for k,v in sorted(ENGINES.items())),flush=True)
        print('\nKeep this window open. No firewall access required (localhost only).',flush=True)
        print('No Oracle changes, no Admin, nothing public. Layers not connected to an engine stay empty or MOCK.',flush=True)
        print('Press Ctrl+C to stop.\n'+'='*68+'\n',flush=True)
        if os.environ.get('TPA_HQ_OPEN_BROWSER','0')=='1':
            # Opens only after the port is bound, so the page never loads too early.
            import webbrowser
            threading.Timer(1.0,lambda:webbrowser.open(url)).start()
        server.serve_forever()

if __name__=='__main__':serve(int(os.environ.get('TPA_HQ_PORT','8775')),lan=os.environ.get('TPA_HQ_LAN_TEST','0')=='1')
