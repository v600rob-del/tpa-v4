"""Makes small pretend "OS Open Roads" downloads (Shapefile zip or GeoPackage zip) for tests.
Same file layout and field names as the real product; the roads are an invented little town
in British National Grid metres near E 530000, N 180000 (London)."""
import io
import sqlite3
import struct
import zipfile

TOWN = [
    # (points [(E, N), ...], class, road number, name)
    ([(529000, 180000), (530000, 180000)], 'A Road', 'A1000', 'High Street'),
    ([(530000, 180000), (531000, 180000)], 'A Road', 'A1000', 'High Street'),
    ([(530000, 179000), (530000, 180000)], 'Unclassified', '', 'Cross Road'),
    ([(530000, 180000), (530000, 181000)], 'Unclassified', '', 'Cross Road'),
    ([(531000, 180000), (531300, 180000), (531500, 180200), (531500, 180600)], 'B Road', 'B200', 'Bend Lane'),
    ([(529500, 181000), (530000, 181000)], 'Unclassified', '', 'Top Road'),
    ([(530000, 181000), (530500, 181000)], 'Unclassified', '', 'Top Road'),
    # a long country road (3 km, one link) to check long roads are still found mid-way
    ([(527000, 178000), (527000, 181000)], 'B Road', 'B300', 'Long Lane'),
]
FIELDS = [('identifier', 38), ('class', 30), ('roadNumber', 10), ('name1', 60), ('formOfWay', 40),
          ('startNode', 38), ('endNode', 38)]


def _shp(lines):
    recs = []
    for i, pts in enumerate(lines, 1):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        content = struct.pack('<i4d2i', 3, min(xs), min(ys), max(xs), max(ys), 1, len(pts)) + struct.pack('<i', 0)
        content += b''.join(struct.pack('<2d', *p) for p in pts)
        recs.append(struct.pack('>2i', i, len(content) // 2) + content)
    body = b''.join(recs)
    head = struct.pack('>7i', 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack('<2i', 1000, 3) + struct.pack('<8d', 0, 0, 0, 0, 0, 0, 0, 0)
    return head + body


def _dbf(rows):
    rl = 1 + sum(l for _, l in FIELDS)
    hl = 32 + 32 * len(FIELDS) + 1
    out = struct.pack('<4BIHH20x', 3, 126, 10, 30, len(rows), hl, rl)
    for name, l in FIELDS:
        out += name[:10].encode().ljust(11, b'\0') + b'C' + b'\0' * 4 + bytes([l, 0]) + b'\0' * 14
    out += b'\r'
    for r in rows:
        out += b' ' + b''.join(str(r.get(n, '')).encode('latin-1')[:l].ljust(l) for n, l in FIELDS)
    return out + b'\x1a'


def _rows(roads):
    return [{'identifier': f'L{i}', 'class': c, 'roadNumber': num, 'name1': name, 'formOfWay': 'Single Carriageway'}
            for i, (_, c, num, name) in enumerate(roads)]


def shapefile_zip(roads=TOWN, squares=('TQ',)):
    """Like the real Shapefile download: one RoadLink file pair per 100 km square."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('oproad_essh_gb/doc/licence.txt', 'Open Government Licence (test copy)')
        share = max(1, len(roads) // len(squares))
        for n, sq in enumerate(squares):
            part = roads[n * share:(n + 1) * share] if n < len(squares) - 1 else roads[n * share:]
            z.writestr(f'oproad_essh_gb/data/{sq}_RoadLink.shp', _shp([r[0] for r in part]))
            z.writestr(f'oproad_essh_gb/data/{sq}_RoadLink.dbf', _dbf(_rows(part)))
            z.writestr(f'oproad_essh_gb/data/{sq}_RoadNode.shp', b'')
    return mem.getvalue()


def geopackage_zip(tmp_path, roads=TOWN):
    """Like the real GeoPackage download: one .gpkg with a road_link table."""
    db = sqlite3.connect(tmp_path)
    db.execute('CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)')
    db.execute('CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)')
    db.execute("INSERT INTO gpkg_contents VALUES ('road_node', 'features'), ('road_link', 'features')")
    db.execute("INSERT INTO gpkg_geometry_columns VALUES ('road_link', 'geometry')")
    db.execute('CREATE TABLE road_link (fid INTEGER, geometry BLOB, road_classification TEXT, '
               'road_classification_number TEXT, name_1 TEXT, form_of_way TEXT, start_node TEXT, end_node TEXT)')
    for i, (pts, c, num, name) in enumerate(roads):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        # GP header with an XY envelope (flags: little-endian, envelope type 1), then WKB LineString Z
        head = b'GP' + bytes([0, 0b00000011]) + struct.pack('<i', 27700) + struct.pack('<4d', min(xs), max(xs), min(ys), max(ys))
        wkb = struct.pack('<BII', 1, 1002, len(pts)) + b''.join(struct.pack('<3d', p[0], p[1], 0.0) for p in pts)
        db.execute('INSERT INTO road_link VALUES (?,?,?,?,?,?,?,?)', (i, head + wkb, c, num, name, 'Single Carriageway', 'a', 'b'))
    db.commit(); db.close()
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(tmp_path, 'Data/oproad_gb.gpkg')
    return mem.getvalue()
