#!/usr/bin/env python3
"""Build v4/hq/assets/planes/shapes.json from the free VRS Custom Markers set.

HQ 4.84.0 (Rob 18 Nov 2026, 12:05-12:20): planes lose their box. TPA's own pictures stay for airliners and everyday
planes; the unusual ones - "the spitfire and unusual ones especially military" - get a real top-down shape of their type.

Source: https://github.com/rikgale/VRSCustomMarkers (MyMarkers1.html), licensed CC0 1.0 (no conditions; credited anyway).
Only shapes matched by an ICAO type code are taken. Left out: boats, ground vehicles and towers (TPA has its own airport
icons), and the regional airliner shapes (E145/E135 = ERJ and CRJ), because airliners keep TPA's own pictures.

Usage:  python3 v4/tools/build_plane_shapes.py /path/to/VRSCustomMarkers/MyMarkers1.html <source-commit>
Writes: v4/hq/assets/planes/shapes.json  (and prints a short summary)
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LEAVE_OUT = {'E145', 'E135', 'TWR', 'GroundVehicle', 'BOAT1', 'BOATMD', 'BOATLG', 'BOATSM', 'BOATCR', 'BOATAC',
             'BOATYT', 'BOATPC'}
NUM = re.compile(r'-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?')


def short(d: str) -> str:
    """Numbers to 2 decimal places (the shapes are drawn in a box about 10.6 units across, so 0.01 is far below a pixel)."""
    def fix(m):
        v = round(float(m.group(0)), 2)
        s = ('%.2f' % v).rstrip('0').rstrip('.')
        return '0' if s in ('-0', '') else s
    return re.sub(r'\s+', ' ', NUM.sub(fix, d)).strip()


def bbox(d: str):
    """Box round a path from its points and control points (a curve never leaves its control points' box, so this is
    safe; at worst a hair loose). Arcs count their end point plus their radius."""
    toks = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|' + NUM.pattern, d)
    xs, ys, x, y, sx, sy, cmd, i = [], [], 0.0, 0.0, 0.0, 0.0, 'M', 0
    take = {'M': 2, 'L': 2, 'T': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'A': 7, 'Z': 0}

    def add(px, py):
        xs.append(px); ys.append(py)
    while i < len(toks):
        if re.fullmatch('[A-Za-z]', toks[i]):
            cmd = toks[i]; i += 1
            if cmd in 'Zz':
                x, y = sx, sy
                continue
        n = take[cmd.upper()]
        v = [float(t) for t in toks[i:i + n]]; i += n
        rel = cmd.islower()
        U = cmd.upper()
        if U == 'H':
            x = x + v[0] if rel else v[0]
        elif U == 'V':
            y = y + v[0] if rel else v[0]
        elif U == 'A':
            ex, ey = (x + v[5], y + v[6]) if rel else (v[5], v[6])
            r = max(abs(v[0]), abs(v[1]))
            add(ex - r, ey - r); add(ex + r, ey + r)
            x, y = ex, ey
        else:
            pts = [(v[k] + (x if rel else 0), v[k + 1] + (y if rel else 0)) for k in range(0, n, 2)]
            for px, py in pts[:-1]:
                add(px, py)
            x, y = pts[-1]
            if U == 'M':
                sx, sy = x, y
                cmd = 'l' if rel else 'L'        # numbers after a move are lines
        add(x, y)
    return min(xs), min(ys), max(xs), max(ys)


def main(src: str, commit: str) -> None:
    text = Path(src).read_text(encoding='utf-8')
    codes = {}
    for block in text.split('new VRS.AircraftMarker({')[1:]:
        m = re.search(r'embeddedSvg:\s*VRS\.EmbeddedSvgs\.Marker_(\w+)', block)
        at = block.find('matches')
        if not m or at < 0:
            continue
        key = m.group(1).replace('Type', '', 1) if m.group(1).startswith('Type') else m.group(1)
        body = block[at:block.find('}', at + 30) + 1]
        for c in re.findall(r"modelIcao\.val\s*===?\s*'([A-Z0-9]{2,4})'", body):
            codes.setdefault(c, key)          # the first marker that matches wins, as in VRS
    shapes = {}
    for key in sorted(set(codes.values()) - LEAVE_OUT):
        name = 'Marker_Type' + key if ('VRS.EmbeddedSvgs.Marker_Type' + key + ' ') in text else 'Marker_' + key
        i = text.find('VRS.EmbeddedSvgs.' + name + ' ')
        if i < 0:
            continue
        q = text.find('"', text.find('svg:', i))
        j = q + 1
        while not (text[j] == '"' and text[j - 1] != '\\'):
            j += 1
        svg = ET.fromstring(json.loads(text[q:j + 1]))
        ns = '{http://www.w3.org/2000/svg}'
        paths = [p for p in svg.iter(ns + 'path')]
        if len(paths) != 1:
            continue                          # all the plane shapes are one path; anything else is left out
        parent = {c: p for p in svg.iter() for c in p}
        trs, node = [], paths[0]
        while node is not svg:
            if node.get('transform'):
                trs.insert(0, node.get('transform'))
            node = parent[node]
        tr = ' '.join(t for t in trs if t.replace(' ', '') not in ('translate(0,0)', 'translate(0)'))
        if tr:
            continue                          # none of the plane shapes are moved; a moved one would need its box redone
        d = short(paths[0].get('d'))
        x0, y0, x1, y1 = bbox(d)
        side = max(x1 - x0, y1 - y0) * 1.06     # cropped square round the plane, so every shape fills the icon
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        shape = {'vb': ' '.join(short('%.3f' % n) for n in (cx - side / 2, cy - side / 2, side, side)), 'd': d}
        shapes[key] = shape
    out = {'about': 'Top-down plane shapes from VRS Custom Markers by rikgale (github.com/rikgale/VRSCustomMarkers), '
                    'forked from shish0r/VRSCustomMarkers. CC0 1.0: free for any use. Thank you!',
           'source': 'https://github.com/rikgale/VRSCustomMarkers', 'sourceCommit': commit, 'licence': 'CC0-1.0',
           'codes': {c: k for c, k in sorted(codes.items()) if k in shapes}, 'shapes': shapes}
    dest = Path(__file__).resolve().parents[1] / 'hq' / 'assets' / 'planes' / 'shapes.json'
    dest.write_text(json.dumps(out, separators=(',', ':')), encoding='utf-8')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tpa_plane_shapes_own                    # HQ 4.93.0: TPA's own military shapes, added after the free set
    tpa_plane_shapes_own.merge_into(dest)
    print(f'{len(shapes)} shapes, {len(out["codes"])} type codes, {dest.stat().st_size:,} bytes -> {dest}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
