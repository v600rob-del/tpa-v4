"""Builds v4/engines/rail/data/timing-points.csv from the YA TIPLOC list.

Source: https://github.com/Jnction/YA_Tiploc_List (tiplocs-merged-stanox.csv).
Each row keeps its original data source so attribution stays traceable.
Rows are kept only if: STANOX is 3-6 digits, coordinates are in Great Britain,
and all rows sharing that STANOX lie within 2 km of each other (else dropped:
we would rather show nothing than the wrong place).
Usage: python build_timing_points.py <tiplocs-merged-stanox.csv> <out.csv>
"""
import csv
import math
import re
import sys

KEEP_SOURCES = {'NAPTAN', 'openRailData', 'TPS', 'BPLAN', 'CORPUS', 'ATOC', 'railmap', 'woodpecker'}


def km(a, b):
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def name_of(raw):
    n = re.sub(r'\s+', ' ', raw or '').strip()
    n = re.sub(r'\s+Rail Station$', '', n, flags=re.I)
    return (n.title() if n.isupper() else n)[:60]


def main(src, out):
    groups = {}
    for r in csv.DictReader(open(src, encoding='utf-8-sig')):
        stanox = (r.get('STANOX') or '').strip()
        if not re.fullmatch(r'\d{3,6}', stanox) or r.get('dataSource') not in KEEP_SOURCES:
            continue
        try:
            lat, lon = float(r['stop_lat']), float(r['stop_lon'])
        except (ValueError, TypeError, KeyError):
            continue
        if not (49.8 < lat < 61.0 and -8.7 < lon < 2.0):
            continue
        groups.setdefault(stanox, []).append((round(lat, 5), round(lon, 5), name_of(r.get('Description')), r['dataSource']))
    kept = dropped = 0
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['stanox', 'lat', 'lon', 'name', 'source'])
        for stanox in sorted(groups, key=int):
            rows = groups[stanox]
            if any(km(rows[0], x) > 2 for x in rows[1:]):
                dropped += 1
                continue
            w.writerow([stanox, *rows[0]])
            kept += 1
    print('kept', kept, 'dropped (conflicting)', dropped)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
