"""Pretend Geofabrik "free shapefile" downloads (gis_osm_roads_free_1 layer, same field names as
Geofabrik's: osm_id, code, fclass, name, ref, oneway, maxspeed, layer, bridge, tunnel), in lon/lat
degrees, for an invented town near Manchester. Positions are given in metres east/north of ORIGIN.
OpenStreetMap roads may meet part-way along a road (not only at the ends), as in the real data."""
import io
import math
import os
import struct
import zipfile

ORIGIN = (-2.24, 53.48)
K = 111195 * math.cos(math.radians(ORIGIN[1]))


def ll(x, y):
    return (ORIGIN[0] + x / K, ORIGIN[1] + y / 111195)


def ring(cx, cy, r, n=24):
    """A roundabout drawn clockwise (UK traffic) as one closed one-way road, starting at its west point."""
    pts = [(cx + r * math.cos(math.radians(180 - 360 * i / n)), cy + r * math.sin(math.radians(180 - 360 * i / n))) for i in range(n)]
    return pts + [pts[0]]


TOWN = [
    # (points in metres, fclass, name, ref, oneway F/T/B)
    ([(-1000, 0), (0, 0), (500, 0), (1000, 0)], 'primary', 'Oxford Road', 'A34', 'B'),     # crossed part-way along
    ([(0, -800), (0, 0), (0, 800)], 'residential', 'Cross Street', '', 'B'),
    ([(500, 600), (500, 300), (500, 0)], 'secondary', 'Deansgate', '', 'F'),               # one-way, southbound only
    ([(1000, 0), (1960, 0)], 'primary', 'Oxford Road', 'A34', 'B'),
    (ring(2000, 0, 40), 'primary', '', '', 'F'),                                           # roundabout
    ([(2040, 0), (3000, 0)], 'primary', 'Wilmslow Road', 'B5117', 'B'),                     # straight-on exit
    ([(2000, 40), (2000, 1000)], 'primary', 'North Road', 'A6', 'B'),
    ([(2000, -40), (2000, -1000)], 'primary', 'South Road', 'A5103', 'B'),
    ([(5000, 0), (5500, 0), (5900, 231)], 'primary', 'Main Street', 'A57', 'B'),           # bends 30 degrees left
    ([(5500, 0), (6000, 0)], 'service', '', '', 'B'),                                       # car park lane straight on
    ([(7000, 0), (7300, 0)], 'footway', 'Path', '', 'B'),                                   # not a road: ignored
]
FIELDS = [('osm_id', 'C', 12), ('code', 'N', 4), ('fclass', 'C', 28), ('name', 'C', 100), ('ref', 'C', 20),
          ('oneway', 'C', 1), ('maxspeed', 'N', 3), ('layer', 'N', 4), ('bridge', 'C', 1), ('tunnel', 'C', 1)]


def _shp(lines):
    recs = []
    for i, pts in enumerate(lines, 1):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        content = struct.pack('<i4d2i', 3, min(xs), min(ys), max(xs), max(ys), 1, len(pts)) + struct.pack('<i', 0)
        content += b''.join(struct.pack('<2d', *p) for p in pts)
        recs.append(struct.pack('>2i', i, len(content) // 2) + content)
    body = b''.join(recs)
    head = struct.pack('>7i', 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack('<2i', 1000, 3) + struct.pack('<8d', *([0] * 8))
    return head + body


def _dbf(rows):
    rl = 1 + sum(l for _, _, l in FIELDS)
    hl = 32 + 32 * len(FIELDS) + 1
    out = struct.pack('<4BIHH20x', 3, 126, 10, 31, len(rows), hl, rl)
    for name, t, l in FIELDS:
        out += name.encode().ljust(11, b'\0') + t.encode() + b'\0' * 4 + bytes([l, 0]) + b'\0' * 14
    out += b'\r'
    for r in rows:
        out += b' ' + b''.join((str(r.get(n, '')).encode('utf-8')[:l].rjust(l) if t == 'N' else str(r.get(n, '')).encode('utf-8')[:l].ljust(l))
                               for n, t, l in FIELDS)
    return out + b'\x1a'


def shapefile_zip(roads=TOWN, buildings_mb=0, zip64=False):
    """Like Geofabrik's <region>-latest-free.shp.zip: many layers; we only need the roads one.
    buildings_mb adds a big other layer (as in the real files); zip64 writes the >4 GB zip format."""
    lines = [[ll(*p) for p in pts] for pts, *_ in roads]
    rows = [{'osm_id': 1000 + i, 'code': 5100 + i, 'fclass': fc, 'name': name, 'ref': ref, 'oneway': one,
             'maxspeed': 30, 'layer': 0, 'bridge': 'F', 'tunnel': 'F'} for i, (_, fc, name, ref, one) in enumerate(roads)]
    mem = io.BytesIO()
    real = zipfile.ZIP64_LIMIT
    if zip64:
        zipfile.ZIP64_LIMIT = 100            # pretend every member is huge: zip64 records everywhere
    try:
        z = zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED)
        z.writestr('README', 'OpenStreetMap data (c) OpenStreetMap contributors, ODbL (test copy)')
        z.writestr('gis_osm_buildings_a_free_1.shp', os.urandom(buildings_mb * 1_000_000) if buildings_mb else b'\0' * 100)
        z.writestr('gis_osm_roads_free_1.shp', _shp(lines))
        z.writestr('gis_osm_roads_free_1.dbf', _dbf(rows))
        z.writestr('gis_osm_roads_free_1.cpg', 'UTF-8')
        z.writestr('gis_osm_roads_free_1.prj', 'GEOGCS["GCS_WGS_1984"]')
        z.writestr('gis_osm_water_a_free_1.shp', b'\1' * 1000)
        z.close()
    finally:
        zipfile.ZIP64_LIMIT = real
    return mem.getvalue()


def geofabrik_index(base):
    """Like https://download.geofabrik.de/index-v1-nogeom.json: England has no shapefile of its
    own (too big), so its sub-regions' files are used; Scotland and Wales have their own."""
    def f(i, parent, shp=True):
        urls = {'pbf': f'{base}/{i}-latest.osm.pbf'}
        if shp:
            urls['shp'] = f'{base}/{i}-latest-free.shp.zip'
        return {'type': 'Feature', 'properties': {'id': i, 'parent': parent, 'name': i.title(), 'urls': urls}}
    return {'type': 'FeatureCollection', 'features': [
        f('europe', None, False), f('united-kingdom', 'europe', False), f('england', 'united-kingdom', False),
        f('greater-manchester', 'england'), f('west-yorkshire', 'england'),
        f('scotland', 'united-kingdom'), f('wales', 'united-kingdom'), f('france', 'europe')]}
