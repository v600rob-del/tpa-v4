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
                  python3 road_map.py fetch <data dir>        (Ordnance Survey -> roads.sqlite)
                  python3 road_map.py fetch-osm <data dir>    (OpenStreetMap via Geofabrik -> roads-osm.sqlite)
"""
from __future__ import annotations

import hashlib
import http.client
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

FORMAT_VERSION = 2                     # 2: one-way flags; pieces stored in every tile they touch
OS_API = 'https://api.os.uk/downloads/v1/products/OpenRoads'
USER_AGENT = 'TPA-live-map/0.5 (bus engine road map; tpaprojectuk@gmail.com)'
TILE_LON, TILE_LAT = 0.02, 0.0125     # about 1.4 km x 1.4 km in Britain
MAX_PIECE_M = 400.0                    # long roads are cut into pieces so nearby tiles always hold them
SIMPLIFY_M = 1.5
SNAP_M = 50.0                          # a bus further than this from any road is left alone
MAX_TURN = 110.0                       # never U-turn: stop instead
MAX_POINTS = 150                       # HQ accepts up to 200
UNSURE = 20                            # two ways on scoring this close: don't guess, wait at the junction
CLASSES = {'motorway': 1, 'a road': 2, 'b road': 3, 'classified unnumbered': 4,
           'unclassified': 5, 'not classified': 6, 'unknown': 7}
REC = struct.Struct('<IIBH')           # format 1 (OS, 0.4.x)
REC2 = struct.Struct('<IIBBH')         # format 2
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


class TileWriter:
    """Writes road pieces into the SQLite tile store (format 2).
    Each piece is stored in every tile any of its points falls in, so a tile always knows every
    road through any point inside it (OpenStreetMap roads can meet part-way along, not only at ends).
    Record: id, name key, class, flags (1 = one-way along the points, 2 = one-way against,
    4 = roundabout), points."""

    def __init__(self, out_path):
        self.out, self.tmp = out_path, out_path + '.building'
        if os.path.exists(self.tmp):
            os.remove(self.tmp)
        self.db = sqlite3.connect(self.tmp)
        for q in ('PRAGMA journal_mode=OFF', 'PRAGMA synchronous=OFF', 'PRAGMA temp_store=MEMORY',
                  'CREATE TABLE tiles (k INTEGER, d BLOB)', 'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)'):
            self.db.execute(q)
        self.buf, self.held, self.next_id, self.links = {}, 0, 1, 0

    def add(self, lonlat, name, cls, flags=0, simplify_m=SIMPLIFY_M):
        """One road line in lon/lat degrees (simplified, cut into <= MAX_PIECE_M pieces). OpenStreetMap
        lines are not simplified (simplify_m=0): any point may be where another road joins."""
        if len(lonlat) < 2:
            return
        k = math.cos(math.radians(lonlat[0][1])) * M_PER_DEG
        metric = [(lo * k, la * M_PER_DEG) for lo, la in lonlat]
        keep = simplify(metric, simplify_m) if simplify_m else metric
        for piece in pieces(keep):
            us = [(round(x / k * 1e6), round(y / M_PER_DEG * 1e6)) for x, y in piece]
            us = [p for i, p in enumerate(us) if i == 0 or p != us[i - 1]]
            if len(us) < 2:
                continue
            rec = bytearray(REC2.pack(self.next_id, name, cls, flags, len(us)))
            for x, y in us:
                rec += struct.pack('<ii', x, y)
            self.next_id += 1; self.links += 1
            for t in {tile_of(x, y) for x, y in us}:
                self.buf.setdefault(t, bytearray()).extend(rec)
                self.held += len(rec)
        if self.held > 40_000_000:
            self.flush()

    def flush(self):
        self.db.executemany('INSERT INTO tiles VALUES (?, ?)',
                            ((tile_key(*t), zlib.compress(bytes(b), 6)) for t, b in self.buf.items()))
        self.db.commit()
        self.buf, self.held = {}, 0

    def finish(self, source, licence):
        self.flush()
        if self.links == 0:
            self.db.close(); os.remove(self.tmp)
            raise ValueError('the download contained no roads')
        self.db.execute('CREATE INDEX tk ON tiles (k)')
        meta = {'format': FORMAT_VERSION, 'links': self.links, 'builtAt': int(time.time()), 'source': source, 'licence': licence}
        self.db.executemany('INSERT INTO meta VALUES (?, ?)', ((k, json.dumps(v)) for k, v in meta.items()))
        self.db.commit(); self.db.close()
        os.replace(self.tmp, self.out)
        return self.links


def build(zip_path, out_path, progress=None, source=''):
    """OS Open Roads download -> road map (written to a temporary name, then swapped in)."""
    w, conv, done = TileWriter(out_path), FastGrid(), 0
    for parts, row in os_links(zip_path, os.path.dirname(os.path.abspath(out_path))):
        done += 1
        if progress and done % 50000 == 0:
            progress(done)
        cls, name = link_info(row)
        for part in parts:
            if len(part) >= 2:
                w.add([conv(E, N) for E, N in part], name, cls)
    return w.finish(source, 'Contains OS data (c) Crown copyright and database right ' + time.strftime('%Y'))


# ---------------------------------------------------------------- OpenStreetMap roads (Geofabrik)
# 0.5.0 (owner 31 Oct 20:27: "the map detail doesnt match where the buses are driving"): the map
# is drawn from OpenStreetMap, so buses now follow OpenStreetMap's roads too. Geofabrik publishes
# OpenStreetMap ready-split into layers as free shapefiles (ODbL; "© OpenStreetMap contributors");
# we read only the roads layer, in lon/lat already, with each road's one-way direction.
GEOFABRIK_INDEX = 'https://download.geofabrik.de/index-v1-nogeom.json'
OSM_REGIONS = ('england', 'scotland', 'wales')
OSM_CLASSES = {'motorway': 1, 'motorway_link': 1, 'trunk': 2, 'trunk_link': 2, 'primary': 3, 'primary_link': 3,
               'secondary': 4, 'secondary_link': 4, 'tertiary': 5, 'tertiary_link': 5, 'unclassified': 6,
               'residential': 7, 'living_street': 8, 'busway': 10, 'bus_guideway': 10, 'service': 9, 'road': 6}
SERVICE = 9


def osm_shp_links(zip_path):
    """Yield (lonlat points, name key, class, flags) for each road in a Geofabrik shapefile zip."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        shp = next((n for n in names if re.search(r'roads_free_1\.shp$', n, re.I)), None)
        if not shp or shp[:-4] + '.dbf' not in names:
            raise ValueError('no roads layer in ' + os.path.basename(zip_path))
        with zf.open(shp) as fs, zf.open(shp[:-4] + '.dbf') as fd:
            yield from _roads_from_streams(fs, fd)


def geofabrik_downloads(index, regions=OSM_REGIONS):
    """Shapefile downloads covering the regions: the region's own file if Geofabrik offers one,
    otherwise its sub-regions' files (Geofabrik leaves big regions out of the shapefile service)."""
    feats = [f.get('properties', {}) for f in index.get('features', [])]
    by_parent = {}
    for f in feats:
        by_parent.setdefault(f.get('parent'), []).append(f)
    by_id = {f.get('id'): f for f in feats}
    out = []

    def want(rid, depth=0):
        f = by_id.get(rid)
        if f is None or depth > 4:
            return
        url = (f.get('urls') or {}).get('shp')
        if url:
            out.append((rid, url))
        else:
            for child in sorted(by_parent.get(rid, []), key=lambda c: c.get('id', '')):
                want(child.get('id'), depth + 1)
    for r in regions:
        want(r)
    for _, url in out:
        if not str(url).startswith(('https://download.geofabrik.de/', 'http://127.0.0.1:')):
            raise ValueError('unexpected download address')
    if not out:
        raise ValueError('Geofabrik list has no road files for ' + ', '.join(regions))
    return out


RETRY_WAITS = (10, 30, 60, 120, 300)       # seconds to wait before trying again (0.5.2)
PACE_SECONDS = 1.0                         # a short pause before each download request


def patiently(job, waits=None):
    """Run job(); if the server is busy or the connection breaks, wait and try again.
    0.5.2 (server 31 Oct 21:27: Geofabrik answered 503 part-way and 47% was thrown away)."""
    from urllib.error import HTTPError, URLError
    waits = RETRY_WAITS if waits is None else waits
    for wait in list(waits) + [None]:
        try:
            return job()
        except HTTPError as exc:
            if exc.code not in (408, 425, 429, 500, 502, 503, 504) or wait is None:
                raise
            retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
            wait = max(wait, int(retry_after)) if retry_after.isdigit() and int(retry_after) <= 900 else wait
        except (URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:   # incl. a read cut short
            if wait is None or isinstance(exc, OSError) and 'partial download' in str(exc):
                raise
        time.sleep(wait)


class RemoteZip:
    """Read just the parts we need of a big zip file on a web server (HTTP range requests).
    Geofabrik's England file holds every OpenStreetMap layer (buildings, land use, ...) and is
    several GB; we need only the roads layer. A zip keeps its table of contents at the end, so we
    read that, then fetch only the roads files. 0.5.1 (server 31 Oct 20:59: the whole England file
    was over the 3 GB safety limit)."""

    def __init__(self, url):
        self.url = url
        tail, total = self._range(-65557)
        if total is None:
            raise OSError('server does not offer partial downloads')
        self.size = total
        i = tail.rfind(b'PK\x05\x06')
        if i < 0:
            raise ValueError('not a zip file')
        n_entries, cd_size, cd_off = struct.unpack_from('<HII', tail, i + 10)
        if cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or n_entries == 0xFFFF:      # ZIP64 (files over 4 GB)
            j = tail.rfind(b'PK\x06\x07', 0, i)
            if j < 0:
                raise ValueError('zip64 locator missing')
            (z64_off,) = struct.unpack_from('<Q', tail, j + 8)
            z64, _ = self._range(z64_off, z64_off + 56)
            if z64[:4] != b'PK\x06\x06':
                raise ValueError('zip64 record missing')
            cd_size, cd_off = struct.unpack_from('<QQ', z64, 40)
        if cd_size > 50_000_000:
            raise ValueError('zip table of contents unexpectedly large')
        cd, _ = self._range(cd_off, cd_off + cd_size)
        self.members, pos = {}, 0
        while pos + 46 <= len(cd) and cd[pos:pos + 4] == b'PK\x01\x02':
            method, = struct.unpack_from('<H', cd, pos + 10)
            csize, usize, nlen, elen, clen = struct.unpack_from('<IIHHH', cd, pos + 20)
            (off,) = struct.unpack_from('<I', cd, pos + 42)
            name = cd[pos + 46:pos + 46 + nlen].decode('utf-8', 'replace')
            extra = cd[pos + 46 + nlen:pos + 46 + nlen + elen]
            e = 0
            while e + 4 <= len(extra):                    # zip64 sizes/offset live in extra field 0x0001
                tag, ln = struct.unpack_from('<HH', extra, e)
                if tag == 1:
                    vals, q = [], e + 4
                    for v in (usize, csize, off):
                        if v == 0xFFFFFFFF:
                            vals.append(struct.unpack_from('<Q', extra, q)[0]); q += 8
                        else:
                            vals.append(v)
                    usize, csize, off = vals
                e += 4 + ln
            self.members[name] = (method, csize, usize, off)
            pos += 46 + nlen + elen + clen

    def _range(self, start, end=None):
        """Bytes [start, end) (start < 0: the last -start bytes). Returns (bytes, total size or None)."""
        rng = f'bytes={start}' if start < 0 else f'bytes={start}-{end - 1}'

        def once():
            with urlopen(Request(self.url, headers={'User-Agent': USER_AGENT, 'Range': rng}), timeout=120) as r:
                if r.status != 206:
                    return b'', None
                m = re.search(r'/(\d+)$', r.headers.get('Content-Range', ''))
                return r.read(), int(m.group(1)) if m else None
        return patiently(once)

    def save(self, name, path, progress=lambda got, total: None):
        """Download one member's compressed bytes to `path`; returns (method, uncompressed size)."""
        method, csize, usize, off = self.members[name]
        if method not in (0, 8):
            raise ValueError('unsupported compression in ' + name)
        if csize > 3_000_000_000:
            raise ValueError(f'{name} unexpectedly large ({csize // 1_000_000} MB)')
        head, _ = self._range(off, off + 30)
        if head[:4] != b'PK\x03\x04':
            raise ValueError('zip entry header missing')
        nlen, elen = struct.unpack_from('<HH', head, 26)
        start = off + 30 + nlen + elen
        got = 0
        with open(path, 'wb') as f:
            def more():                           # continues from where it got to after any break
                nonlocal got
                time.sleep(PACE_SECONDS)
                with urlopen(Request(self.url, headers={'User-Agent': USER_AGENT,
                                                        'Range': f'bytes={start + got}-{start + csize - 1}'}), timeout=120) as r:
                    if r.status != 206:
                        raise OSError('server ignored the partial download request')
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk); got += len(chunk)
                        progress(got, csize)
                if got < csize:
                    raise ConnectionError(f'{name}: download cut short ({got} of {csize} bytes)')
            patiently(more)
        if got != csize:
            raise OSError(f'{name}: download wrong size ({got} of {csize} bytes)')
        return method, usize


class Inflate:
    """Read a saved zip member (stored or deflated) as an ordinary file, decompressing as we go.
    Keeps a read position instead of copying the buffer on every small read."""

    def __init__(self, path, method):
        self.f = open(path, 'rb')
        self.z = zlib.decompressobj(-15) if method == 8 else None
        self.buf, self.pos = b'', 0

    def _more(self):
        chunk = self.f.read(1 << 20)
        if not chunk:
            if self.z is not None:
                tail = self.z.flush(); self.z = None
                if tail:
                    self.buf = self.buf[self.pos:] + tail; self.pos = 0
                    return True
            return False
        data = self.z.decompress(chunk) if self.z is not None else chunk
        self.buf = self.buf[self.pos:] + data; self.pos = 0
        return True

    def read(self, n=-1):
        if n < 0:
            while self._more():
                pass
            out, self.buf, self.pos = self.buf[self.pos:], b'', 0
            return out
        while len(self.buf) - self.pos < n and self._more():
            pass
        out = self.buf[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def close(self):
        self.f.close()


def _roads_from_streams(fs, fd, rows=None):
    for parts, row in zip(shp_records(fs), dbf_records(fd)):
        if rows is not None:
            rows[0] += 1
        cls = OSM_CLASSES.get(row.get('fclass', ''), 0)
        if not cls or not parts:
            continue
        one = row.get('oneway', '').upper()
        flags = 1 if one == 'F' else 2 if one == 'T' else 0
        key = (row.get('ref') or row.get('name') or '').strip().lower()
        name = (zlib.crc32(key.encode()) or 1) if key else 0
        for part in parts:
            ring = 4 if flags and len(part) > 3 and part[0] == part[-1] else 0
            yield part, name, cls, flags | ring


def fetch_osm_and_build(data_dir, index_url=GEOFABRIK_INDEX, status=lambda **kw: None, max_age_days=45):
    """Fetch the roads layer of each Geofabrik file (only those parts, when the server allows; else
    the whole file) one region at a time, and build roads-osm.sqlite. Skipped if the current one is
    younger than max_age_days. Every temporary file is deleted. True if rebuilt."""
    os.makedirs(data_dir, exist_ok=True)
    out = os.path.join(data_dir, 'roads-osm.sqlite')
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < max_age_days * 86400:
        status(state='up-to-date')
        return False
    status(state='checking')
    files = geofabrik_downloads(patiently(lambda: _get_json(index_url)))
    w, done = TileWriter(out), 0
    tmp = [os.path.join(data_dir, n) for n in ('osm-download.zip', 'osm-roads.shp.z', 'osm-roads.dbf.z')]
    last = [0.0]

    def tell(**kw):                                   # at most one progress line every 5 seconds
        if time.time() - last[0] >= 5 or kw.get('force'):
            last[0] = time.time(); kw.pop('force', None); status(**kw)
    try:
        for n, (rid, url) in enumerate(files, 1):
            region = f'{rid} ({n} of {len(files)})'
            try:
                rz = RemoteZip(url)
                shp = next((m for m in rz.members if re.search(r'roads_free_1\.shp$', m, re.I)), None)
                if not shp or shp[:-4] + '.dbf' not in rz.members:
                    raise ValueError('no roads layer in ' + rid)
                kinds = []
                for member, path in ((shp, tmp[1]), (shp[:-4] + '.dbf', tmp[2])):
                    kinds.append(rz.save(member, path, lambda got, total: tell(
                        state='downloading', percent=round(got * 100 / max(1, total)), region=region,
                        mb=f'{got // 1_000_000} of {total // 1_000_000} MB')))
                fs, fd = Inflate(tmp[1], kinds[0][0]), Inflate(tmp[2], kinds[1][0])
                head = fd.read(32); total_rows = struct.unpack_from('<I', head, 4)[0]
                fd.close(); fd = Inflate(tmp[2], kinds[1][0])
                streams = (fs, fd)
            except OSError as exc:
                if 'partial' not in str(exc):
                    raise
                # the server does not do partial downloads: fetch the whole file instead
                with urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=120) as r, open(tmp[0], 'wb') as f:
                    size = int(r.headers.get('Content-Length') or 0); got = 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk); got += len(chunk)
                        tell(state='downloading', percent=round(got * 100 / size) if size else 0, region=region, mb=f'{got // 1_000_000} MB')
                        if got > 12_000_000_000:
                            raise ValueError(f'download unexpectedly large ({got // 1_000_000} MB)')
                zf = zipfile.ZipFile(tmp[0])
                shp = next((m for m in zf.namelist() if re.search(r'roads_free_1\.shp$', m, re.I)), None)
                if not shp:
                    raise ValueError('no roads layer in ' + rid)
                with zf.open(shp[:-4] + '.dbf') as fdh:
                    total_rows = struct.unpack_from('<I', fdh.read(32), 4)[0]
                streams = (zf.open(shp), zf.open(shp[:-4] + '.dbf'))
            rows = [0]
            for pts, name, cls, flags in _roads_from_streams(*streams, rows=rows):
                w.add(pts, name, cls, flags, simplify_m=0)
                done += 1
                if done % 2000 == 0:
                    tell(state='building', percent=min(99, round(rows[0] * 100 / max(1, total_rows))), region=region, linksRead=done)
            for st in streams:
                st.close()
            tell(state='building', percent=100, region=region, linksRead=done, force=True)
            for t in tmp:
                if os.path.exists(t):
                    os.remove(t)
    except BaseException:
        try:
            w.db.close(); os.remove(w.tmp)
        except OSError:
            pass
        raise
    finally:
        for t in tmp:
            if os.path.exists(t):
                os.remove(t)
    links = w.finish('OpenStreetMap roads via Geofabrik (' + ', '.join(r for r, _ in files[:6]) + ('…' if len(files) > 6 else '') + ')',
                     '© OpenStreetMap contributors (ODbL)')
    status(state='built', links=links)
    return True


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


CELL_LON, CELL_LAT = TILE_LON / 8, TILE_LAT / 8    # ~170 m street-index cells (8 x 8 per tile)


def cells_of(x0, y0, x1, y1):
    """Street-index cells overlapping a box given in micro-degrees."""
    cx0, cx1 = math.floor(x0 * 1e-6 / CELL_LON), math.floor(x1 * 1e-6 / CELL_LON)
    cy0, cy1 = math.floor(y0 * 1e-6 / CELL_LAT), math.floor(y1 * 1e-6 / CELL_LAT)
    return [(cx, cy) for cx in range(cx0, cx1 + 1) for cy in range(cy0, cy1 + 1)]


class Tile:
    """One map area in memory: road pieces (compact arrays), a street index for finding the road
    nearest a point, and the junction index: every point where two or more pieces meet."""
    __slots__ = ('links', 'nodes', 'size', 'cells', 'points')

    def __init__(self, blobs, fmt=FORMAT_VERSION):
        self.links, self.nodes, self.size, self.cells, self.points = {}, {}, 0, {}, 0
        rec = REC2 if fmt >= 2 else REC
        seen = {}
        for blob in blobs:
            raw = zlib.decompress(blob)
            self.size += len(raw)
            pos = 0
            while pos < len(raw):
                if fmt >= 2:
                    lid, name, cls, flags, n = rec.unpack_from(raw, pos)
                else:
                    (lid, name, cls, n), flags = rec.unpack_from(raw, pos), 0
                pos += rec.size
                pts = array('i'); pts.frombytes(raw[pos:pos + 8 * n]); pos += 8 * n
                if sys.byteorder != 'little':
                    pts.byteswap()
                if lid in self.links:
                    continue
                xs, ys = pts[0::2], pts[1::2]
                bb = array('i', (min(xs), min(ys), max(xs), max(ys)))
                self.links[lid] = (name, cls, pts, bb, flags)
                self.points += n
                for c in cells_of(*bb):
                    self.cells.setdefault(c, []).append(lid)
                for i in range(n):
                    key = (pts[2 * i] << 32) | (pts[2 * i + 1] & 0xFFFFFFFF)
                    seen.setdefault(key, []).append((lid, i))
        # keep only real meeting points (and piece ends) - most points are just bends in a road
        self.nodes = {k: v for k, v in seen.items()
                      if len(v) > 1 or v[0][1] == 0 or v[0][1] == len(self.links[v[0][0]][2]) // 2 - 1}


def _allowed(flags, step):
    return not (flags & 1 and step < 0) and not (flags & 2 and step > 0)


class RoadMap:
    def __init__(self, path, max_tiles=150, max_points=2_500_000):
        self.path, self.max_tiles, self.max_points = path, max_tiles, max_points
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self.tiles = OrderedDict()
        self.meta = {k: json.loads(v) for k, v in self.db.execute('SELECT key, value FROM meta')}
        self.fmt = self.meta.get('format')
        if self.fmt not in (1, 2):
            raise ValueError('road map format unknown - will rebuild')
        self.tile_loads, self.points = 0, 0

    def close(self):
        self.db.close()

    def tile(self, tx, ty):
        with self.lock:
            t = self.tiles.get((tx, ty))
            if t is not None:
                self.tiles.move_to_end((tx, ty))
                return t
            rows = [r[0] for r in self.db.execute('SELECT d FROM tiles WHERE k=?', (tile_key(tx, ty),))]
            t = Tile(rows, self.fmt)
            self.tiles[(tx, ty)] = t
            self.points += t.points
            self.tile_loads += 1
            while len(self.tiles) > 1 and (len(self.tiles) > self.max_tiles or self.points > self.max_points):
                self.points -= self.tiles.popitem(last=False)[1].points
            return t

    def _tile_at(self, x, y):
        return self.tile(*tile_of(x, y))

    def snap(self, lon, lat, bearing):
        """Nearest road piece within SNAP_M that the bus may drive along in the direction it is going.
        Returns (lid, link, seg, forward, x_u, y_u) or None."""
        xu, yu = round(lon * 1e6), round(lat * 1e6)
        tx, ty = tile_of(xu, yu)
        kx = math.cos(math.radians(lat)) * M_PER_DEG * 1e-6
        ky = M_PER_DEG * 1e-6
        pad_x, pad_y = SNAP_M / kx, SNAP_M / ky
        near = cells_of(xu - pad_x, yu - pad_y, xu + pad_x, yu + pad_y)
        rx, ry = (MAX_PIECE_M + SNAP_M) / kx, (MAX_PIECE_M + SNAP_M) / ky
        x0, y0 = tx * TILE_LON * 1e6, ty * TILE_LAT * 1e6
        xs = [0] + ([-1] if xu - x0 < rx else []) + ([1] if x0 + TILE_LON * 1e6 - xu < rx else [])
        ys = [0] + ([-1] if yu - y0 < ry else []) + ([1] if y0 + TILE_LAT * 1e6 - yu < ry else [])
        if self.fmt >= 2:
            xs, ys = [0], [0]        # format 2 keeps every piece in every tile it touches
        seen, best = set(), None
        for dx in xs:
            for dy in ys:
                t = self.tile(tx + dx, ty + dy)
                for lid in (lid for c in near for lid in t.cells.get(c, ())):
                    if lid in seen:
                        continue
                    seen.add(lid)
                    link = t.links[lid]
                    name, cls, pts, bb, flags = link
                    if xu < bb[0] - pad_x or xu > bb[2] + pad_x or yu < bb[1] - pad_y or yu > bb[3] + pad_y:
                        continue
                    for i in range(0, len(pts) - 2, 2):
                        ax, ay = (pts[i] - xu) * kx, (pts[i+1] - yu) * ky
                        bx, by = (pts[i+2] - xu) * kx, (pts[i+3] - yu) * ky
                        vx, vy = bx - ax, by - ay
                        L2 = vx * vx + vy * vy
                        if L2 == 0:
                            continue
                        f = max(0.0, min(1.0, -(ax * vx + ay * vy) / L2))
                        d = math.hypot(ax + vx * f, ay + vy * f)
                        if d > SNAP_M:
                            continue
                        seg_b = _bearing(0, 0, vx, vy)
                        for forward in (True, False):
                            if not _allowed(flags, 1 if forward else -1):
                                continue
                            turn = _diff(bearing, seg_b if forward else (seg_b + 180) % 360)
                            if turn > 75:
                                continue
                            score = d + 0.5 * turn + (10 if cls == SERVICE else 0)
                            if best is None or score < best[0]:
                                best = (score, lid, link, i // 2, forward,
                                        round(pts[i] + (pts[i+2] - pts[i]) * f), round(pts[i+1] + (pts[i+3] - pts[i+1]) * f))
        return best[1:] if best else None

    def ahead(self, lon, lat, bearing, metres):
        """Points [[lon, lat], ...] along the road for `metres`, or None if no road fits.
        At each meeting point the bus takes the most natural way on: the smallest turn, staying on the
        same road (name or number), keeping roughly the direction it had 100 m back (so it goes round a
        roundabout and leaves by the natural exit), avoiding service roads, obeying one-way streets,
        never turning back. It stops at a dead end."""
        if bearing is None or not metres > 0:
            return None
        s = self.snap(lon, lat, bearing)
        if s is None:
            return None
        lid, link, seg, forward, xu, yu = s
        kx = math.cos(math.radians(lat)) * M_PER_DEG * 1e-6
        ky = M_PER_DEG * 1e-6
        out, run = [(xu, yu)], [0.0]
        left = metres
        name, cls, pts, bb, flags = link
        step = 1 if forward else -1
        vi = seg + 1 if forward else seg
        visited = {lid}

        def heading_back(back_m):
            target = run[-1] - back_m
            j = len(run) - 1
            while j > 0 and run[j] > target:
                j -= 1
            (ax, ay), (bx, by) = out[j], out[-1]
            if ax == bx and ay == by:
                return None
            return _bearing(0, 0, (bx - ax) * kx, (by - ay) * ky)

        for _ in range(4000):
            px, py = pts[2 * vi], pts[2 * vi + 1]
            cx, cy = out[-1]
            L = math.hypot((px - cx) * kx, (py - cy) * ky)
            if L >= left:
                f = left / L if L else 0
                out.append((round(cx + (px - cx) * f), round(cy + (py - cy) * f)))
                return self._finish(out)
            if L > 0:
                out.append((px, py)); run.append(run[-1] + L)
            left -= L
            n = len(pts) // 2
            nxt = vi + step
            others = self._tile_at(px, py).nodes.get((px << 32) | (py & 0xFFFFFFFF), ())
            if 0 <= nxt < n and all(o[0] == lid for o in others):
                vi = nxt                        # just a bend in this road
                continue
            came = heading_back(15) or bearing
            ref = heading_back(100) or came
            options = []
            if 0 <= nxt < n:
                options.append((lid, (name, cls, pts, bb, flags), step, nxt))
            for lid2, j in others:
                if lid2 == lid:
                    continue
                link2 = self._tile_at(px, py).links.get(lid2)
                if link2 is None:
                    continue
                n2 = len(link2[2]) // 2
                for st2 in (1, -1):
                    if 0 <= j + st2 < n2 and _allowed(link2[4], st2):
                        options.append((lid2, link2, st2, j + st2))
            best, second, seen_next = None, None, {}
            for olid, olink, ostep, onext in options:
                p2 = olink[2]
                nk = (p2[2 * onext], p2[2 * onext + 1])
                ob = _bearing(0, 0, (p2[2 * onext] - px) * kx, (p2[2 * onext + 1] - py) * ky)
                turn = _diff(came, ob)
                if turn > MAX_TURN:
                    continue
                on_ring = flags & 4
                score = turn + (1.0 if on_ring else 0.5) * _diff(ref, ob)
                if not (olink[4] & 4) and (olid == lid or (name and olink[0] == name)):
                    score -= 30                 # stay on the same road (not for going round a roundabout)
                if olink[1] == SERVICE and cls != SERVICE:
                    score += 40                 # buses rarely turn into service roads
                if olid in visited and olid != lid:
                    score += 60
                # the same road listed twice (overlapping region files, a road cut into pieces) is one way on
                if nk in seen_next and seen_next[nk] <= score:
                    continue
                seen_next[nk] = score
                cand = (score, olid, olink, ostep, onext, nk)
                if best is None or score < best[0]:
                    if best is not None and best[5] != nk:
                        second = best
                    best = cand
                elif best[5] != nk and (second is None or score < second[0]):
                    second = cand
            if best is None:
                break                           # dead end, or only a U-turn: the bus stops here
            # 0.5.3 (owner 31 Oct 22:11: buses "go off course ... it looks like theyre lost"): where two
            # ways on are about equally likely (a T-junction, a fork), don't guess - the bus waits at the
            # junction until its next real report shows which way it went. Roundabouts still guess.
            if second is not None and second[0] - best[0] < UNSURE and not (flags & 4) and not (best[2][4] & 4):
                break
            _, lid, link, step, vi, _ = best
            visited.add(lid)
            name = link[0] or name
            _, cls, pts, bb, flags = link
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
    elif len(sys.argv) == 3 and sys.argv[1] in ('fetch', 'fetch-osm'):
        osm = sys.argv[1] == 'fetch-osm'
        api = os.environ.get('TPA_BUS_OSM_TEST_INDEX' if osm else 'TPA_BUS_ROADS_TEST_API', '').strip() or (GEOFABRIK_INDEX if osm else OS_API)
        if api not in (OS_API, GEOFABRIK_INDEX) and not api.startswith('http://127.0.0.1:'):
            raise SystemExit('Test address must be 127.0.0.1')
        try:
            os.nice(19)
        except OSError:
            pass

        def report(**kw):
            print('ROADMAP ' + json.dumps(kw), flush=True)
        try:
            (fetch_osm_and_build if osm else fetch_and_build)(sys.argv[2], api, report)
        except Exception as exc:   # one clear line for the engine to show; never a traceback wall
            report(state='error', error=(type(exc).__name__ + ': ' + str(exc))[:120])
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(2)
