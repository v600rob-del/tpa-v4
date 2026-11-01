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
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen
import hashlib
import ipaddress
import gzip
import zlib
import json
import mimetypes
import os
import re
import socket
import ssl
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
COMPONENTS = ('air', 'sea', 'rail', 'bus', 'road')
PUBLIC = {'/', '/index.html', '/assets/hq.css', '/assets/hq.js', '/assets/tpa-basemap-loader.js', '/assets/transport-vector-layer.js', '/data/uk-geography.json', '/data/site-config.json'}
HQ_VERSION = '4.3.0'
# 4.0.10: engines are separate programs on this PC. TPA_HQ_ENGINES maps a socket
# to a loopback engine, e.g. "road=http://127.0.0.1:8782,bus=http://127.0.0.1:8781".
# HQ relays data only (JSON frames, PNG overlay tiles); it contains no supplier
# logic, keys or transport-specific code. An engine failing affects only its socket.
ENGINES = {}
for _pair in os.environ.get('TPA_HQ_ENGINES', '').split(','):
    _name, _, _url = _pair.strip().partition('=')
    if _name in ('air', 'sea', 'rail', 'bus', 'road') and re.fullmatch(r'http://127\.0\.0\.1:\d{2,5}', _url.strip()):
        ENGINES[_name] = _url.strip()
# 4.0.9: owner decision 30 Oct 2026. A component can be "parked" by server
# setting (e.g. TPA_HQ_PARKED=sea). HQ code is unchanged per component: the
# socket simply reports unavailable/coming soon and serves no data, even if a
# fixture exists. Unknown names are ignored, never errors.
PARKED = frozenset(x.strip() for x in os.environ.get('TPA_HQ_PARKED', '').split(',') if x.strip() in ('air', 'sea', 'rail', 'bus', 'road'))
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
CACHE_DIR = Path(tempfile.gettempdir()) / 'tpa-v4-hq-icons-lan-4.0.8'
FIXTURE_DIR = os.environ.get('TPA_HQ_FIXTURE_DIR')
DISABLE_MAP_NETWORK = os.environ.get('TPA_HQ_DISABLE_MAP_NETWORK', '0') == '1'
# 4.1.7 (owner 31 Oct: map loading slow on the small server): with TPA_HQ_BASEMAP=direct the
# visitor's browser fetches the OpenFreeMap street map straight from OpenFreeMap's fast network
# (its normal, intended use: "no limits on map views or requests"). The route through HQ stays
# as an automatic backup if the direct route fails. Default "via-hq" keeps the PC unchanged.
BASEMAP = os.environ.get('TPA_HQ_BASEMAP', 'via-hq').strip().lower()
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
    return '?' + '&'.join(f'{k}={values[k]}' for k in ('west', 'south', 'east', 'north')) if len(values) == 4 else ''


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
        match=re.fullmatch(r'(127\.0\.0\.1|localhost):([0-9]{1,5})',host)
        if match is None or int(match.group(2))!=server_port:return None
        return f'http://{match.group(1)}:{server_port}'
    # A LAN preview binds exactly one RFC1918 IPv4 interface, not all adapters.
    if host != f'{bound_address}:{server_port}':return None
    return f'http://{bound_address}:{server_port}'


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
             f"img-src 'self' data: blob:{' '+OFM if BASEMAP=='direct' else ''}; font-src 'self'; "
             f"connect-src 'self'{' '+OFM if BASEMAP=='direct' else ''}; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")):
            self.send_header(name,value)
        # 4.1.0: the page's own files are re-checked on every visit (a quick "not modified"
        # answer if unchanged), so an update shows straight away instead of up to 30 min later.
        if urlsplit(self.path).path in PUBLIC and not getattr(self,'_cache_set',False):
            self.send_header('Cache-Control','no-cache')
        super().end_headers()
    def reply(self,status,raw,mime):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','private, max-age=1800' if status==200 else 'no-store');self._cache_set=True
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
        if path=='/hq-map-status':
            return self.json_reply(200,{'version':HQ_VERSION,'renderer':'Leaflet 1.9.4 + MapLibre GL 5.24.0 + bridge 0.1.4',
                'source':'OpenFreeMap','mode':'MOCK-icon-review-only','networkDisabled':DISABLE_MAP_NETWORK,
                'upstreamErrors':dict(ERRORS),'lastFailedResource':dict(LAST_FAILURE),'resourceCounts':dict(RESOURCE_COUNTS),'browser':dict(BROWSER),'browserHistory':list(BROWSER_HISTORY),'vendorCached':{k:(CACHE_DIR/'vendor'/hashlib.sha256(k.encode()).hexdigest()).is_file() for k in VENDOR},'mapLibreAssetCached':(CACHE_DIR/'vendor'/hashlib.sha256('/assets/vendor/maplibre-gl.js'.encode()).hexdigest()).is_file()})
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
            if len(parts)==6 and parts[:4]==['','api','v4','components'] and parts[4] in COMPONENTS and parts[5] in ('status','snapshot'):
                comp=parts[4]
                if comp in PARKED:
                    return self.json_reply(503,{'component':comp,'health':'unavailable','parked':True,'message':'Coming soon; parked by owner; no data'})
                if comp in ENGINES and parts[5]=='snapshot':
                    try:
                        raw,_=engine_get(comp,'/snapshot'+bbox_query(split.query),1_200_000,6)
                        frame=json.loads(raw)
                        if not isinstance(frame,dict) or frame.get('component')!=comp or frame.get('contractVersion')!='1.0':
                            raise ValueError('engine-contract')
                        return self.reply(200,raw,'application/json; charset=utf-8')
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
            return self.json_reply(200,{'mode':BASEMAP,'style':OFM+'/styles/liberty' if BASEMAP=='direct' else via,
                                        'fallback':via if BASEMAP=='direct' else None})
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
        if path in PUBLIC:
            self.path='/index.html' if path=='/' else path
            return super().do_GET()
        return self.send_error(404,'HQ standard-only resource allowlist')
    def do_POST(self):
        # Minimal local-only diagnostics: fixed error codes, no user-generated
        # text, no coordinates, no credentials and no content persistence.
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
                             'vector-layer-attached','map-resource-request-failed'):
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
    def do_HEAD(self):return self.send_error(405,'Read-only HQ')
    def log_message(self,fmt,*args):
        if not self.path.startswith('/basemap/openfreemap/'):super().log_message(fmt,*args)


def serve(port=8775, lan=False):
    address=find_lan_ip() if lan else '127.0.0.1'
    with ThreadingHTTPServer((address,port),HQHandler) as server:
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
