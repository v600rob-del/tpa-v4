"""TPA v4 bus engine: ROAD MAP (0.4.0) - lets a bus glide along its real road between reports.

Data: Ordnance Survey "OS Open Roads" (every public road in Great Britain), OS OpenData,
free for any use under the Open Government Licence v3.0. Attribution:
"Contains OS data (c) Crown copyright and database right <year>".
We chose it after checking the alternatives (30 Oct 2026):
- the public OpenStreetMap Overpass service asks that public apps do not depend on it;
- OpenFreeMap's terms forbid collecting its map data automatically.

Three jobs, standard library only:
1. fetch_and_build(): download the national file from the OS Downloads API (no key, about
   1 GB, twice a year) and turn it into a compact SQLite tile store (roads.sqlite).
   Runs as a low-priority child process of the bus engine, so the engine stays responsive.
2. RoadMap: open that store and load only the small tiles (about 1.4 km square) near buses
   currently being looked at, keeping at most a fixed number in memory.
3. RoadMap.ahead(): from a bus's reported position and direction, find its road and walk
   along it for a given distance, taking the most natural way through junctions (straight on,
   same road name). Returns map points for the HQ "path" movement, or None if unsure.

Usage (by hand):  python3 road_map.py build <OS zip> <out.sqlite>
                  python3 road_map.py fetch <data dir>
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
import threading
import time
import zipfile
import zlib
from array import array
from collections import OrderedDict
from urllib.request import Request, urlopen

FORMAT_VERSION = 1
OS_API = 'https://api.os.uk/downloads/v1/products/OpenRoads'
USER_AGENT = 'TPA-live-map/0.4 (bus engine road map; tpaprojectuk@gmail.com)'
TILE_LON, TILE_LAT = 0.02, 0.0125     # about 1.4 km x 1.4 km in Britain
MAX_PIECE_M = 400.0                    # long roads are cut into pieces so nearby tiles always hold them
SIMPLIFY_M = 1.5
SNAP_M = 50.0                          # a bus further than this from any road is left alone
MAX_TURN = 110.0                       # never U-turn: stop instead
MAX_POINTS = 150                       # HQ accepts up to 200
CLASSES = {'motorway': 1, 'a road': 2, 'b road': 3, 'classified unnumbered': 4,
           'unclassified': 5, 'not classified': 6, 'unknown': 7}
REC = struct.Struct('<IIBH')
M_PER_DEG = 111195.0


# ---------------------------------------------------------------- coordinates
def osgb_to_wgs84(E, N):
    """OSGB36 National Grid -> WGS84 lon/lat (Ordnance Survey formulas + Helmert, ~5 m)."""
    a, b = 6377563.396, 6356256.909; F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2); N0, E0 = -100000, 400000
    e2 = 1 - (b*b)/(a*a); n = (a-b)/(a+b)
    lat, M = lat0, 0.0
    while True:
        lat = (N - N0 - M)/(a*F0) + lat
        M = b*F0*((1+n+1.25*n*n+1.25*n**3)*(lat-lat0) - (3*n+3*n*n+21/8*n**3)*math.sin(lat-lat0)*math.cos(lat+lat0)
                  + (15/8*n*n+15/8*n**3)*math.sin(2*(lat-lat0))*math.cos(2*(lat+lat0)) - 35/24*n**3*math.sin(3*(lat-lat0))*math.cos(3*(lat+lat0)))
        if abs(N - N0 - M) < 0.00001:
            break
    sl, cl, tl = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a*F0/math.sqrt(1-e2*sl*sl); rho = a*F0*(1-e2)/(1-e2*sl*sl)**1.5; eta2 = nu/rho - 1
    VII = tl/(2*rho*nu); VIII = tl/(24*rho*nu**3)*(5+3*tl*tl+eta2-9*tl*tl*eta2); IX = tl/(720*rho*nu**5)*(61+90*tl*tl+45*tl**4)
    X = 1/(cl*nu); XI = 1/(cl*6*nu**3)*(nu/rho+2*tl*tl); XII = 1/(cl*120*nu**5)*(5+28*tl*tl+24*tl**4)
    XIIA = 1/(cl*5040*nu**7)*(61+662*tl*tl+1320*tl**4+720*tl**6)
    dE = E - E0
    lat1 = lat - VII*dE**2 + VIII*dE**4 - IX*dE**6
    lon1 = lon0 + X*dE - XI*dE**3 + XII*dE**5 - XIIA*dE**7
    sl, cl = math.sin(lat1), math.cos(lat1)
    nu = a/math.sqrt(1-e2*sl*sl)
    x = nu*cl*math.cos(lon1); y = nu*cl*math.sin(lon1); z = (1-e2)*nu*sl
    tx, ty, tz = 446.448, -125.157, 542.060; s = -20.4894e-6
    rx, ry, rz = [math.radians(v/3600) for v in (0.1502, 0.2470, 0.8421)]
    x2 = tx + (1+s)*x - rz*y + ry*z; y2 = ty + rz*x + (1+s)*y - rx*z; z2 = tz - ry*x + rx*y + (1+s)*z
    a2, b2 = 6378137.0, 6356752.3142; e22 = 1 - (b2*b2)/(a2*a2)
    p = math.hypot(x2, y2); lat2 = math.atan2(z2, p*(1-e22))
    for _ in range(10):
        nu2 = a2/math.sqrt(1-e22*math.sin(lat2)**2); lat2 = math.atan2(z2+e22*nu2*math.sin(lat2), p)
    return math.degrees(math.atan2(y2, x2)), math.degrees(lat2)


class FastGrid:
    """Exact conversion at the corners of 2.5 km squares, straight-line blend inside (error under
    25 cm anywhere in Britain, far below the ~5 m of the conversion itself). Much faster for millions of points.
    Same input -> same output, always (so road ends still meet exactly)."""
    CELL = 2500.0

    def __init__(self):
        self.corners = {}

    def corner(self, i, j):
        c = self.corners.get((i, j))
        if c is None:
            c = self.corners[(i, j)] = osgb_to_wgs84(i * self.CELL, j * self.CELL)
        return c

    def __call__(self, E, N):
        i, j = int(E // self.CELL), int(N // self.CELL)
        u, v = E / self.CELL - i, N / self.CELL - j
        a, b, c, d = self.corner(i, j), self.corner(i + 1, j), self.corner(i, j + 1), self.corner(i + 1, j + 1)
        lon = (a[0]*(1-u) + b[0]*u)*(1-v) + (c[0]*(1-u) + d[0]*u)*v
        lat = (a[1]*(1-u) + b[1]*u)*(1-v) + (c[1]*(1-u) + d[1]*u)*v
        return lon, lat


# ---------------------------------------------------------------- reading OS files
def _read_exact(f, n):
    out = b''
    while len(out) < n:
        chunk = f.read(n - len(out))
        if not chunk:
            break
        out += chunk
    return out


def shp_records(f):
    """Stream polyline records [[(E, N), ...], ...] from a .shp file object (no seeking)."""
    _read_exact(f, 100)
    while True:
        head = _read_exact(f, 8)
        if len(head) < 8:
            return
        _, clen = struct.unpack('>ii', head)
        rec = _read_exact(f, clen * 2)
        st = struct.unpack_from('<i', rec, 0)[0]
        if st not in (3, 13, 23):
            yield []
            continue
        nparts, npts = struct.unpack_from('<2i', rec, 36)
        parts = struct.unpack_from(f'<{nparts}i', rec, 44)
        p0 = 44 + 4 * nparts
        flat = struct.unpack_from(f'<{2*npts}d', rec, p0)
        pts = list(zip(flat[0::2], flat[1::2]))
        yield [pts[parts[i]:(parts[i+1] if i + 1 < nparts else npts)] for i in range(nparts)]


def dbf_records(f):
    """Stream attribute rows {lower-case field: text} from a .dbf file object (no seeking)."""
    head = _read_exact(f, 32)
    n, hl, rl = struct.unpack('<IHH', head[4:12])
    fields, used = [], 32
    while True:
        d = _read_exact(f, 1)
        used += 1
        if d == b'\r' or not d:
            break
        d += _read_exact(f, 31)
        used += 31
        fields.append((d[:11].split(b'\0')[0].decode('latin-1').lower(), d[16]))
    _read_exact(f, hl - used)
    for _ in range(n):
        rec = _read_exact(f, rl)
        row, pos = {}, 1
        for name, length in fields:
            row[name] = rec[pos:pos+length].decode('latin-1').strip()
            pos += length
        yield row


def gpkg_lines(blob):
    """GeoPackage geometry blob -> [[(E, N), ...], ...] for (Multi)LineString."""
    if not blob or blob[:2] != b'GP':
        return []
    flags = blob[3]
    env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get((flags >> 1) & 7, 0)
    pos = 8 + env

    def one(pos):
        bo = '<' if blob[pos] == 1 else '>'
        gtype = struct.unpack_from(bo + 'I', blob, pos + 1)[0]
        base, flag = gtype % 1000, gtype // 1000
        dims = 2 + (flag in (1, 3)) + (flag in (2, 3))
        if gtype & 0x80000000:            # EWKB Z flag
            base, dims = gtype & 0xFFFF, 3
        pos += 5
        if base == 2:
            n = struct.unpack_from(bo + 'I', blob, pos)[0]; pos += 4
            flat = struct.unpack_from(bo + f'{n*dims}d', blob, pos); pos += 8 * n * dims
            return [list(zip(flat[0::dims], flat[1::dims]))], pos
        if base == 5:
            n = struct.unpack_from(bo + 'I', blob, pos)[0]; pos += 4
            out = []
            for _ in range(n):
                lines, pos = one(pos)
                out += lines
            return out, pos
        return [], pos
    try:
        return one(pos)[0]
    except struct.error:
        return []


def _attr(row, *names):
    for n in names:
        for k, v in row.items():
            if k == n or k.startswith(n):
                if v:
                    return str(v)
    return ''


def link_info(row):
    """(class code, name key) from an OS RoadLink row, whatever the file format calls the fields."""
    cls = CLASSES.get(_attr(row, 'road_classification', 'class').strip().lower(), 0)
    name = _attr(row, 'road_classification_number', 'roadnumber', 'name_1', 'name1').strip().lower()
    return cls, (zlib.crc32(name.encode()) or 1) if name else 0


def os_links(zip_path, work_dir):
    """Yield (parts, row) for every road link in an OS Open Roads download (Shapefile or GeoPackage zip)."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        shps = sorted(n for n in names if re.search(r'roadlink\.shp$', n, re.I))
        if shps:
            for shp in shps:
                dbf = shp[:-4] + ('.dbf' if shp.endswith('.shp') else '.DBF')
                if dbf not in names:
                    continue
                with zf.open(shp) as fs, zf.open(dbf) as fd:
                    yield from zip(shp_records(fs), dbf_records(fd))
            return
        gpkgs = [n for n in names if n.lower().endswith('.gpkg')]
        if not gpkgs:
            raise ValueError('no RoadLink shapefile or GeoPackage in the download')
        path = os.path.join(work_dir, 'roads-extract.gpkg')
        with zf.open(gpkgs[0]) as src, open(path, 'wb') as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
    try:
        db = sqlite3.connect(path)
        tables = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")]
        table = next((t for t in tables if t.lower().replace('_', '') == 'roadlink'), None) or \
            next((t for t in tables if 'road_link' in t.lower() or 'roadlink' in t.lower()), None)
        if not table:
            raise ValueError('no road link table in the GeoPackage')
        geom = db.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?", (table,)).fetchone()[0]
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        cur = db.execute(f'SELECT * FROM "{table}"')
        gi = cols.index(geom)
        for r in cur:
            row = {c.lower(): r[i] for i, c in enumerate(cols) if i != gi}
            yield gpkg_lines(r[gi]), row
        db.close()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- building the tile store
def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True; stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop(); (x1, y1), (x2, y2) = pts[i], pts[j]; dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy) or 1e-9; best, bi = -1.0, -1
        for k in range(i + 1, j):
            x, y = pts[k]; d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / L
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True; stack += [(i, bi), (bi, j)]
    return [p for p, k in zip(pts, keep) if k]


def pieces(pts, limit=MAX_PIECE_M):
    """Cut a line into pieces no longer than `limit` metres (cutting at or between points)."""
    dense = [pts[0]]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        steps = int(L // limit)
        for s in range(1, steps + 1):
            f = s * limit / L
            if f < 1 - 1e-9:
                dense.append((x1 + (x2 - x1) * f, y1 + (y2 - y1) * f))
        dense.append((x2, y2))
    out, cur, run = [], [dense[0]], 0.0
    for p in dense[1:]:
        L = math.hypot(p[0] - cur[-1][0], p[1] - cur[-1][1])
        if run + L > limit + 1e-6 and len(cur) > 1:
            out.append(cur); cur, run = [cur[-1]], 0.0
        cur.append(p); run += L
    if len(cur) > 1:
        out.append(cur)
    return out


def tile_of(lon_u, lat_u):
    return (math.floor(lon_u * 1e-6 / TILE_LON), math.floor(lat_u * 1e-6 / TILE_LAT))


def tile_key(tx, ty):
    return (tx + 100000) * 1000000 + (ty + 100000)


def build(zip_path, out_path, progress=None, source=''):
    """OS download -> roads.sqlite (written to a temporary name, then swapped in)."""
    tmp = out_path + '.building'
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute('PRAGMA journal_mode=OFF'); db.execute('PRAGMA synchronous=OFF'); db.execute('PRAGMA temp_store=MEMORY')
    db.execute('CREATE TABLE tiles (k INTEGER, d BLOB)')
    db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    conv = FastGrid()
    buf, held, next_id, links, done = {}, 0, 1, 0, 0

    def flush():
        nonlocal buf, held
        db.executemany('INSERT INTO tiles VALUES (?, ?)',
                       ((tile_key(*t), zlib.compress(bytes(b), 6)) for t, b in buf.items()))
        db.commit()
        buf, held = {}, 0

    for parts, row in os_links(zip_path, os.path.dirname(os.path.abspath(out_path))):
        done += 1
        if progress and done % 50000 == 0:
            progress(done)
        cls, name = link_info(row)
        for part in parts:
            if len(part) < 2:
                continue
            for piece in pieces(simplify(part, SIMPLIFY_M)):
                ll = [conv(E, N) for E, N in piece]
                us = [(round(lo * 1e6), round(la * 1e6)) for lo, la in ll]
                rec = bytearray(REC.pack(next_id, name, cls, len(us)))
                for x, y in us:
                    rec += struct.pack('<ii', x, y)
                next_id += 1; links += 1
                for t in {tile_of(*us[0]), tile_of(*us[-1])}:
                    buf.setdefault(t, bytearray()).extend(rec)
                    held += len(rec)
        if held > 40_000_000:
            flush()
    flush()
    if links == 0:
        db.close(); os.remove(tmp)
        raise ValueError('the download contained no roads')
    db.execute('CREATE INDEX tk ON tiles (k)')
    meta = {'format': FORMAT_VERSION, 'links': links, 'builtAt': int(time.time()), 'source': source,
            'licence': 'Contains OS data (c) Crown copyright and database right ' + time.strftime('%Y')}
    db.executemany('INSERT INTO meta VALUES (?, ?)', ((k, json.dumps(v)) for k, v in meta.items()))
    db.commit(); db.close()
    os.replace(tmp, out_path)
    return links


# ---------------------------------------------------------------- downloading (OS Downloads API)
def _get_json(url):
    with urlopen(Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'}), timeout=60) as r:
        return json.loads(r.read(5_000_000).decode('utf-8'))


def choose_download(items):
    """Pick the whole-GB Shapefile (streams without unpacking), else the GeoPackage."""
    gb = [i for i in items if isinstance(i, dict) and str(i.get('area', '')).upper() == 'GB' and i.get('url')]
    for want in ('shape', 'geopackage'):
        for i in gb:
            if want in str(i.get('format', '')).lower():
                return i
    raise ValueError('OS Open Roads download list has no GB Shapefile or GeoPackage')


def fetch_and_build(data_dir, api=OS_API, status=lambda **kw: None):
    """Download OS Open Roads if it changed, then build roads.sqlite. Returns True if rebuilt."""
    os.makedirs(data_dir, exist_ok=True)
    out = os.path.join(data_dir, 'roads.sqlite')
    stamp = os.path.join(data_dir, 'roads-source.json')
    status(state='checking')
    item = choose_download(_get_json(api + '/downloads'))
    if not str(item['url']).startswith(('https://', 'http://127.0.0.1:')):
        raise ValueError('unexpected download address')
    ident = {'fileName': item.get('fileName'), 'md5': item.get('md5'), 'size': item.get('size')}
    try:
        old = json.load(open(stamp))
    except (OSError, ValueError):
        old = None
    if old == ident and os.path.exists(out):
        status(state='up-to-date')
        return False
    part = os.path.join(data_dir, 'roads-download.zip')
    size = int(item.get('size') or 0)
    md5 = hashlib.md5()
    got = 0
    with urlopen(Request(item['url'], headers={'User-Agent': USER_AGENT}), timeout=120) as r, open(part, 'wb') as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk); md5.update(chunk); got += len(chunk)
            if size:
                status(state='downloading', percent=min(99, got * 100 // size))
            if got > 4_000_000_000:
                raise ValueError('download unexpectedly large')
    try:
        if item.get('md5') and md5.hexdigest().lower() != str(item['md5']).lower():
            raise ValueError('download damaged (checksum mismatch)')
        status(state='building', linksRead=0)
        links = build(part, out, progress=lambda n: status(state='building', linksRead=n),
                      source=f"OS Open Roads {item.get('fileName', '')}")
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    json.dump(ident, open(stamp, 'w'))
    status(state='built', links=links)
    return True


# ---------------------------------------------------------------- using the map
def _bearing(ax, ay, bx, by):
    return math.degrees(math.atan2(bx - ax, by - ay)) % 360


def _diff(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


class Tile:
    __slots__ = ('links', 'nodes', 'size')

    def __init__(self, blobs):
        self.links, self.nodes, self.size = {}, {}, 0
        for blob in blobs:
            raw = zlib.decompress(blob)
            self.size += len(raw)
            pos = 0
            while pos < len(raw):
                lid, name, cls, n = REC.unpack_from(raw, pos); pos += REC.size
                pts = array('i'); pts.frombytes(raw[pos:pos + 8 * n]); pos += 8 * n
                if sys.byteorder != 'little':
                    pts.byteswap()
                if lid in self.links:
                    continue
                xs, ys = pts[0::2], pts[1::2]
                # compact: one small array per road piece (about 4x smaller than Python tuples)
                self.links[lid] = (name, cls, pts, array('i', (min(xs), min(ys), max(xs), max(ys))))
                self.nodes.setdefault((pts[0], pts[1]), []).append((lid, True))
                self.nodes.setdefault((pts[-2], pts[-1]), []).append((lid, False))


class RoadMap:
    def __init__(self, path, max_tiles=150):
        self.path, self.max_tiles = path, max_tiles
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self.tiles = OrderedDict()
        self.meta = {k: json.loads(v) for k, v in self.db.execute('SELECT key, value FROM meta')}
        if self.meta.get('format') != FORMAT_VERSION:
            raise ValueError('road map format changed - will rebuild')
        self.tile_loads = 0

    def close(self):
        self.db.close()

    def tile(self, tx, ty):
        with self.lock:
            t = self.tiles.get((tx, ty))
            if t is not None:
                self.tiles.move_to_end((tx, ty))
                return t
            rows = [r[0] for r in self.db.execute('SELECT d FROM tiles WHERE k=?', (tile_key(tx, ty),))]
            t = Tile(rows)
            self.tiles[(tx, ty)] = t
            self.tile_loads += 1
            while len(self.tiles) > self.max_tiles:
                self.tiles.popitem(last=False)
            return t

    def snap(self, lon, lat, bearing):
        """Nearest road piece within SNAP_M whose direction fits the bus's heading.
        Returns (lid, seg, frac, forward, x_u, y_u, link) or None."""
        xu, yu = round(lon * 1e6), round(lat * 1e6)
        tx, ty = tile_of(xu, yu)
        kx = math.cos(math.radians(lat)) * M_PER_DEG * 1e-6
        ky = M_PER_DEG * 1e-6
        pad_x, pad_y = SNAP_M / kx, SNAP_M / ky
        seen, best = set(), None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for lid, (name, cls, pts, bb) in self.tile(tx + dx, ty + dy).links.items():
                    if lid in seen or xu < bb[0] - pad_x or xu > bb[2] + pad_x or yu < bb[1] - pad_y or yu > bb[3] + pad_y:
                        continue
                    seen.add(lid)
                    for i in range(0, len(pts) - 2, 2):
                        ax, ay = (pts[i] - xu) * kx, (pts[i+1] - yu) * ky
                        bx, by = (pts[i+2] - xu) * kx, (pts[i+3] - yu) * ky
                        vx, vy = bx - ax, by - ay
                        L2 = vx * vx + vy * vy
                        f = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / L2))
                        d = math.hypot(ax + vx * f, ay + vy * f)
                        if d > SNAP_M or L2 == 0:
                            continue
                        seg_b = _bearing(0, 0, vx, vy)
                        for forward in (True, False):
                            turn = _diff(bearing, seg_b if forward else (seg_b + 180) % 360)
                            if turn > 75:
                                continue
                            score = d + 0.5 * turn
                            if best is None or score < best[0]:
                                best = (score, lid, i // 2, f, forward,
                                        round(pts[i] + (pts[i+2] - pts[i]) * f), round(pts[i+1] + (pts[i+3] - pts[i+1]) * f),
                                        (name, cls, pts, bb))
        if best is None:
            return None
        return best[1:]

    def ahead(self, lon, lat, bearing, metres):
        """Points [[lon, lat], ...] along the road for `metres`, or None if no road fits."""
        if bearing is None or not metres > 0:
            return None
        s = self.snap(lon, lat, bearing)
        if s is None:
            return None
        lid, seg, frac, forward, xu, yu, link = s
        kx = math.cos(math.radians(lat)) * M_PER_DEG * 1e-6
        ky = M_PER_DEG * 1e-6
        out = [(xu, yu)]
        left = metres
        name = link[0]
        pts = link[2]
        n = len(pts) // 2
        # vertices still ahead on this piece
        order = list(range(seg + 1, n)) if forward else list(range(seg, -1, -1))
        visited = {lid}
        for _ in range(80):
            for vi in order:
                px, py = pts[2*vi], pts[2*vi+1]
                cx, cy = out[-1]
                L = math.hypot((px - cx) * kx, (py - cy) * ky)
                if L >= left:
                    f = left / L if L else 0
                    out.append((round(cx + (px - cx) * f), round(cy + (py - cy) * f)))
                    return self._finish(out)
                if L > 0:
                    out.append((px, py))
                left -= L
            # at the end node of this piece: pick the most natural way on
            end = (pts[-2], pts[-1]) if forward else (pts[0], pts[1])
            if len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                came = _bearing(0, 0, (bx - ax) * kx, (by - ay) * ky)
            else:
                came = bearing
            best = None
            for nlid, at_start in self.tile(*tile_of(*end)).nodes.get(end, ()):
                if nlid == lid:
                    continue
                cand = self.tile(*tile_of(*end)).links[nlid]
                cp = cand[2]
                if at_start:
                    nb = _bearing(0, 0, (cp[2] - cp[0]) * kx, (cp[3] - cp[1]) * ky)
                else:
                    nb = _bearing(0, 0, (cp[-4] - cp[-2]) * kx, (cp[-3] - cp[-1]) * ky)
                turn = _diff(came, nb)
                score = turn - (30 if name and cand[0] == name else 0) + (90 if nlid in visited else 0)
                if turn <= MAX_TURN and (best is None or score < best[0]):
                    best = (score, nlid, at_start, cand)
            if best is None:
                break                       # dead end or only a U-turn: the bus stops here
            _, lid, forward, link = best
            visited.add(lid)
            name = link[0] or name
            pts = link[2]
            n = len(pts) // 2
            order = list(range(1, n)) if forward else list(range(n - 2, -1, -1))
        return self._finish(out) if len(out) >= 2 else None

    @staticmethod
    def _finish(out):
        if len(out) > MAX_POINTS:
            step = (len(out) - 1) / (MAX_POINTS - 1)
            out = [out[round(i * step)] for i in range(MAX_POINTS)]
        return [[x / 1e6, y / 1e6] for x, y in out]


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'build':
        t0 = time.time()
        print('links', build(sys.argv[2], sys.argv[3], progress=lambda n: print('read', n, flush=True)),
              'in', round(time.time() - t0), 's')
    elif len(sys.argv) == 3 and sys.argv[1] == 'fetch':
        api = os.environ.get('TPA_BUS_ROADS_TEST_API', '').strip() or OS_API
        if api != OS_API and not api.startswith('http://127.0.0.1:'):
            raise SystemExit('Test address must be 127.0.0.1')
        try:
            os.nice(19)
        except OSError:
            pass

        def report(**kw):
            print('ROADMAP ' + json.dumps(kw), flush=True)
        try:
            fetch_and_build(sys.argv[2], api, report)
        except Exception as exc:   # one clear line for the engine to show; never a traceback wall
            report(state='error', error=(type(exc).__name__ + ': ' + str(exc))[:120])
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(2)
