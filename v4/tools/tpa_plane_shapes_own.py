#!/usr/bin/env python3
"""TPA's own top-down plane shapes, for military types the free VRS Custom Markers set does not have.

HQ 4.93.0 (Rob 19 Nov 2026, 12:17: "Military helicopters and planes are sometimes having the right icon, other times
not"; 12:29 "yes please" to drawing the missing ones). Measured with the look-only v4/server/tpa-plane-kinds: no plane
changed icon, but Chinook / Apache / Merlin / Hawk / A400M had a real top-down shape while Wildcat, Jupiter, Tutor,
Voyager, Shadow and military 737s fell back to Rob's cartoon pictures, because the free set has no shape for them.

Each shape is drawn here from simple pieces (fuselage outline, wings, tail, engines, rotor blades) at the aircraft's
real proportions in metres, nose up, then scaled into the same ~10-unit box as the free set. The map paints the fill
over the outline, so overlapping pieces show as one silhouette. Drawn by Claude for TPA; free to use (CC0), like the set.

Usage:  python3 v4/tools/tpa_plane_shapes_own.py            -> prints the shapes as JSON
        python3 v4/tools/tpa_plane_shapes_own.py --sheet F  -> also writes an HTML preview sheet to F
        python3 v4/tools/tpa_plane_shapes_own.py --into v4/hq/assets/planes/shapes.json   -> adds them to the map's file
(build_plane_shapes.py does the --into step itself after rebuilding the free set.)
"""
import json
import math
import sys


def mirror(poly):
    return [(-x, y) for x, y in reversed(poly)]


def body(profile):
    """[(y, half width), ...] from nose to tail -> a symmetric outline."""
    right = [(w, y) for y, w in profile]
    return right + [(-w, y) for y, w in reversed(profile)]


def ellipse(cx, cy, rx, ry, n=28):
    return [(cx + rx * math.sin(2 * math.pi * i / n), cy - ry * math.cos(2 * math.pi * i / n)) for i in range(n)]


def wing(y_root, root_chord, half_span, sweep_deg, tip_chord, x_root=0.0):
    """Right wing: leading edge at the root at y_root, swept back by sweep_deg (leading edge)."""
    dy = half_span * math.tan(math.radians(sweep_deg))
    return [(x_root, y_root), (x_root + half_span, y_root + dy), (x_root + half_span, y_root + dy + tip_chord),
            (x_root, y_root + root_chord)]


def pair(poly):
    return [poly, mirror(poly)]


def blade(cx, cy, angle_deg, length, width, root=0.3):
    """A rotor blade from the hub out to `length`, pointing at angle_deg (0 = straight ahead, clockwise)."""
    a = math.radians(angle_deg)
    ux, uy = math.sin(a), -math.cos(a)
    px, py = -uy, ux
    pts = []
    for d, w in ((root, width * 0.5), (length - width, width * 0.5)):
        pts.append((cx + ux * d + px * w, cy + uy * d + py * w))
    tip = [(cx + ux * (length - width + width * math.sin(t)) + px * width * 0.5 * math.cos(t),
            cy + uy * (length - width + width * math.sin(t)) + py * width * 0.5 * math.cos(t))
           for t in [i * math.pi / 8 for i in range(1, 8)]]
    back = [(cx + ux * d - px * w, cy + uy * d - py * w) for d, w in ((length - width, width * 0.5), (root, width * 0.5))]
    return [pts[0], pts[1]] + tip + back


def rotor(cx, cy, radius, blades, width, start_deg=45, paddle=False):
    out = [blade(cx, cy, start_deg + i * 360 / blades, radius, width) for i in range(blades)]
    if paddle:      # the Lynx / Wildcat's BERP blade tips: wider near the end
        out += [blade(cx, cy, start_deg + i * 360 / blades, radius, width * 1.9, root=radius * 0.8) for i in range(blades)]
    return out + [ellipse(cx, cy, width * 1.1, width * 1.1, 16)]


def bar(cx, cy, half_len, half_w, angle_deg=90):
    """A thin bar centred at (cx, cy): propellers, skids."""
    a = math.radians(angle_deg)
    ux, uy = math.sin(a), -math.cos(a)
    px, py = -uy, ux
    return [(cx - ux * half_len - px * half_w, cy - uy * half_len - py * half_w),
            (cx + ux * half_len - px * half_w, cy + uy * half_len - py * half_w),
            (cx + ux * half_len + px * half_w, cy + uy * half_len + py * half_w),
            (cx - ux * half_len + px * half_w, cy - uy * half_len + py * half_w)]


def jet_engine(cx, cy, length, radius):
    return body([(cy, radius * 0.75), (cy + 0.15 * length, radius), (cy + 0.8 * length, radius * 0.92),
                 (cy + length, radius * 0.6)]) if cx == 0 else [(x + cx, y) for x, y in
                                                                  body([(cy, radius * 0.75), (cy + 0.15 * length, radius),
                                                                        (cy + 0.8 * length, radius * 0.92),
                                                                        (cy + length, radius * 0.6)])]


# ----------------------------------------------------------------------------------------------- the aircraft (metres)
def wildcat():
    """AgustaWestland AW159 Wildcat (ICAO LYNX): 4-blade rotor 12.8 m, 15.2 m long, slim tail boom, tail rotor left."""
    p = [body([(0.0, 0.0), (0.3, 0.55), (1.0, 0.9), (2.0, 1.05), (5.4, 1.05), (6.1, 0.55), (7.0, 0.32), (11.4, 0.22),
               (12.4, 0.2), (12.9, 0.1)])]
    p += [bar(-1.15, 3.6, 0.9, 0.22, 0), bar(1.15, 3.6, 0.9, 0.22, 0)]          # sponsons (wheels)
    p += pair([(0.15, 10.6), (1.25, 10.9), (1.25, 11.35), (0.15, 11.5)])        # tailplane
    p += [bar(-0.45, 12.2, 1.1, 0.12, 0)]                                        # tail rotor (left side)
    p += rotor(0.0, 4.0, 6.4, 4, 0.5, start_deg=40, paddle=True)
    return p


def puma():
    """Aerospatiale SA330 Puma (ICAO PUMA): 4-blade rotor 15 m, 18.2 m long, wide cabin, wheel sponsons, tail rotor right."""
    p = [body([(0.0, 0.0), (0.35, 0.7), (1.2, 1.2), (2.2, 1.35), (7.2, 1.35), (8.2, 0.8), (9.2, 0.5), (13.6, 0.3),
               (14.6, 0.26), (15.1, 0.1)])]
    p += [bar(-1.85, 5.6, 1.4, 0.55, 0), bar(1.85, 5.6, 1.4, 0.55, 0)]          # sponsons (wheels)
    p += [ellipse(0, 3.3, 0.85, 1.6)]                                            # engines on top
    p += pair([(0.15, 12.6), (1.4, 12.9), (1.4, 13.35), (0.15, 13.5)])          # tailplane (one side on the real one)
    p += [bar(0.5, 14.5, 1.5, 0.12, 0)]                                          # tail rotor (right side)
    p += rotor(0.0, 4.6, 7.5, 4, 0.6, start_deg=30)
    return p


def h145():
    """Airbus H135 / H145 (Juno / Jupiter; ICAO EC35, EC45): 4 blades, Fenestron tail, skids."""
    p = [body([(0.0, 0.0), (0.3, 0.55), (1.0, 0.85), (2.0, 0.95), (4.6, 0.95), (5.4, 0.5), (6.2, 0.3), (9.4, 0.24),
               (10.0, 0.4), (10.9, 0.42), (11.2, 0.2)])]
    p += [bar(-1.3, 3.2, 2.1, 0.13, 0), bar(1.3, 3.2, 2.1, 0.13, 0)]            # skids
    p += [bar(-0.8, 2.1, 0.55, 0.09, 90), bar(0.8, 2.1, 0.55, 0.09, 90),
          bar(-0.8, 4.3, 0.55, 0.09, 90), bar(0.8, 4.3, 0.55, 0.09, 90)]        # skid cross tubes
    p += [ellipse(0.0, 10.45, 0.42, 0.75)]                                       # Fenestron (ducted tail fan)
    p += pair([(0.2, 8.9), (1.3, 9.0), (1.4, 9.35), (1.3, 9.5), (0.2, 9.45)])   # tailplane with end plates
    p += rotor(0.0, 3.4, 5.5, 4, 0.45, start_deg=50)
    return p


def black_hawk():
    """Sikorsky UH-60 Black Hawk (ICAO H60): 4-blade rotor 16.4 m, 19.8 m long, flat wide cabin, big stabilator."""
    p = [body([(0.0, 0.0), (0.4, 0.7), (1.3, 1.1), (2.4, 1.18), (8.6, 1.18), (9.6, 0.6), (10.6, 0.45), (14.6, 0.3),
               (15.4, 0.25), (15.9, 0.1)])]
    p += pair([(0.2, 14.4), (2.2, 14.6), (2.2, 15.4), (0.2, 15.6)])             # stabilator (4.4 m)
    p += [bar(0.55, 15.0, 1.6, 0.13, 0)]                                         # tail rotor (right, canted)
    p += [ellipse(-0.75, 4.6, 0.38, 1.3), ellipse(0.75, 4.6, 0.38, 1.3)]       # engines
    p += rotor(0.0, 5.4, 8.2, 4, 0.62, start_deg=60)
    return p


def tutor():
    """Grob G115 Tutor (ICAO G115): low straight wing 10 m, 7.4 m long, two-blade propeller."""
    p = [body([(0.35, 0.0), (0.45, 0.42), (1.2, 0.55), (2.6, 0.58), (4.2, 0.45), (6.4, 0.18), (7.2, 0.12)])]
    p += pair(wing(2.1, 1.65, 5.0, 3, 1.05))
    p += pair([(0.0, 6.2), (1.7, 6.5), (1.7, 7.05), (0.0, 7.25)])
    p += [bar(0, 0.3, 0.95, 0.08, 90), ellipse(0, 0.3, 0.2, 0.2, 12)]          # propeller and spinner
    return p


def shadow():
    """Beechcraft King Air 350 (Shadow R1; ICAO B350): 17.6 m span, 14.2 m long, two turboprops, T-tail, winglets."""
    p = [body([(0.0, 0.0), (0.4, 0.45), (1.4, 0.78), (2.8, 0.82), (8.8, 0.82), (11.4, 0.5), (13.6, 0.22), (14.2, 0.1)])]
    p += pair(wing(4.6, 2.5, 8.6, 4, 1.15, x_root=0.6))
    p += pair([(8.6, 5.15), (8.85, 5.0), (8.9, 5.9), (8.65, 6.1)])              # winglets
    p += [[(x + 2.85, y) for x, y in body([(2.6, 0.0), (3.0, 0.42), (3.8, 0.5), (6.8, 0.45), (8.0, 0.15)])]]
    p += [[(x - 2.85, y) for x, y in body([(2.6, 0.0), (3.0, 0.42), (3.8, 0.5), (6.8, 0.45), (8.0, 0.15)])]]
    p += [bar(2.85, 2.6, 1.3, 0.09, 90), bar(-2.85, 2.6, 1.3, 0.09, 90)]        # propellers
    p += pair([(0.0, 12.6), (2.8, 13.2), (2.8, 13.8), (0.0, 14.0)])             # T-tail
    return p


def voyager():
    """Airbus A330-200 / Voyager KC2-KC3 (ICAO A332, military only): 60.3 m span, 58.8 m long, refuelling pods."""
    p = [body([(0.0, 0.0), (0.8, 1.4), (2.5, 2.5), (5.0, 2.82), (44.0, 2.82), (52.0, 1.9), (57.6, 0.7), (58.8, 0.2)])]
    p += pair(wing(21.0, 11.0, 30.0, 31, 2.6))
    p += pair([(29.4, 38.4), (30.4, 37.6), (30.6, 39.2), (29.6, 40.5)])        # wingtip fences
    p += [jet_engine(10.0, 20.6, 7.4, 1.9), jet_engine(-10.0, 20.6, 7.4, 1.9)]
    p += [jet_engine(22.5, 31.0, 4.6, 0.75), jet_engine(-22.5, 31.0, 4.6, 0.75)]   # refuelling pods (Voyager)
    p += pair(wing(48.0, 7.0, 9.7, 33, 2.4))
    return p


def b737():
    """Boeing 737 / P-8 Poseidon (ICAO B737, B738, B739, P8; military only): 35.8 m span, 39.5 m long."""
    p = [body([(0.0, 0.0), (0.6, 1.1), (2.0, 1.75), (4.0, 1.88), (30.0, 1.88), (35.0, 1.3), (38.8, 0.45), (39.5, 0.15)])]
    p += pair(wing(14.8, 7.4, 17.9, 27, 1.5))
    p += pair([(17.9, 24.0), (18.4, 23.4), (18.5, 25.3), (18.0, 25.6)])        # winglets
    p += [jet_engine(5.0, 12.4, 4.8, 1.3), jet_engine(-5.0, 12.4, 4.8, 1.3)]
    p += pair(wing(32.4, 5.2, 6.4, 32, 1.8))
    return p


def hercules():
    """Lockheed C-130 Hercules (ICAO C130, C30J): 40.4 m high straight wing, four turboprops, 29.8 m long."""
    p = [body([(0.0, 0.0), (0.4, 1.2), (1.6, 1.9), (3.2, 2.15), (19.0, 2.15), (24.0, 1.5), (28.6, 0.6), (29.8, 0.3)])]
    p += pair(wing(9.8, 4.9, 20.2, 2, 2.6))
    for x in (5.2, 10.6):
        for s in (1, -1):
            p += [[(xx + s * x, yy) for xx, yy in body([(8.2, 0.0), (8.7, 0.55), (9.6, 0.68), (14.4, 0.55), (15.4, 0.2)])]]
            p += [bar(s * x, 8.2, 2.05, 0.15, 90)]
    p += pair(wing(24.6, 4.6, 8.0, 6, 2.6))
    return p


SHAPES = {'TPA_WILDCAT': wildcat, 'TPA_PUMA': puma, 'TPA_H145': h145, 'TPA_H60': black_hawk, 'TPA_TUTOR': tutor,
          'TPA_B350': shadow, 'TPA_A332': voyager, 'TPA_B737': b737, 'TPA_C130': hercules}
# type code -> shape, for every plane with that code
CODES = {'LYNX': 'TPA_WILDCAT', 'PUMA': 'TPA_PUMA', 'H60': 'TPA_H60', 'C130': 'TPA_C130', 'C30J': 'TPA_C130',
         'P8': 'TPA_B737'}
# type code -> shape, only when the plane is military (civil ones keep Rob's pictures, his 18 Nov choice)
MIL_CODES = {'EC35': 'TPA_H145', 'EC45': 'TPA_H145', 'AS32': 'TPA_PUMA', 'G115': 'TPA_TUTOR', 'B350': 'TPA_B350',
             'BE20': 'TPA_B350', 'A332': 'TPA_A332', 'B737': 'TPA_B737', 'B738': 'TPA_B737', 'B739': 'TPA_B737'}


def orient(poly):
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))
    return poly if area > 0 else list(reversed(poly))


def build(fn, box=9.8):
    polys = [orient(p) for p in fn()]
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    s = box / max(w, h)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    out = []
    for p in polys:
        pts = [(5 + (x - cx) * s, 5 + (y - cy) * s) for x, y in p]
        out.append('M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x, y in pts) + ' Z')
    side = box * 1.06                     # the same margin round the plane as the free set's shapes
    pad = 5 - side / 2
    return {'vb': f'{pad:.2f} {pad:.2f} {side:.2f} {side:.2f}', 'd': ' '.join(out)}


OWN_NOTE = ('Shapes starting TPA_ (Wildcat, Puma, Juno/Jupiter, Black Hawk, Tutor, Shadow, Voyager, 737/Poseidon, '
            'Hercules) were drawn for TPA (v4/tools/tpa_plane_shapes_own.py), also CC0. milCodes are used only for '
            'military planes.')


def merge_into(path):
    """Add TPA's own shapes to shapes.json (the free set's codes win where both have one)."""
    data = json.loads(open(path, encoding='utf-8').read())
    for k, f in SHAPES.items():
        data['shapes'][k] = build(f)
    for c, k in CODES.items():
        data['codes'].setdefault(c, k)
    data['milCodes'] = dict(sorted(MIL_CODES.items()))
    data['own'] = OWN_NOTE
    open(path, 'w', encoding='utf-8').write(json.dumps(data, separators=(',', ':')))
    return data


def main():
    shapes = {k: build(f) for k, f in SHAPES.items()}
    if '--sheet' in sys.argv:
        names = {'TPA_WILDCAT': 'Wildcat', 'TPA_PUMA': 'Puma', 'TPA_H145': 'Juno / Jupiter', 'TPA_H60': 'Black Hawk',
                 'TPA_TUTOR': 'Tutor', 'TPA_B350': 'Shadow (King Air)', 'TPA_A332': 'Voyager (A330)',
                 'TPA_B737': '737 / Poseidon', 'TPA_C130': 'Hercules'}
        cells = ''.join(f'<div class=c><svg viewBox="{v["vb"]}"><path d="{v["d"]}"/></svg><b>{names[k]}</b></div>'
                        for k, v in shapes.items())
        try:
            free = json.load(open(__file__.rsplit('/tools/', 1)[0] + '/hq/assets/planes/shapes.json'))['shapes']
        except OSError:
            free = {}
        small = ''.join(f'<svg class=s viewBox="{v["vb"]}"><path d="{v["d"]}"/></svg>' for v in shapes.values())
        others = ''.join(f'<svg class=s viewBox="{free[k]["vb"]}"><path d="{free[k]["d"]}"/></svg>'
                         for k in ('EH101', 'CH47', 'H64', 'V22', 'HAWK', 'A400', 'C17', 'B707', 'T6') if k in free)
        cells += ('<div style="margin:10px 6px;background:#c9d8c0;padding:10px">At map size - new: ' + small +
                  ' &nbsp; free set: ' + others + '</div>')
        open(sys.argv[sys.argv.index('--sheet') + 1], 'w').write(
            '<html><body style="margin:0;padding:8px;background:#e8ecef;font:14px sans-serif"><style>.c{display:inline-block;'
            'width:150px;text-align:center;margin:6px}svg{width:130px;height:130px;overflow:visible}svg.s{width:34px;height:34px;margin:0 6px;vertical-align:middle}path{fill:#8f9f52;'
            'stroke:#0b1620;stroke-width:1.1px;vector-effect:non-scaling-stroke;paint-order:stroke}</style>' + cells + '</body></html>')
    if '--into' in sys.argv:
        data = merge_into(sys.argv[sys.argv.index('--into') + 1])
        print(f"{len(data['shapes'])} shapes, {len(data['codes'])} codes, {len(data['milCodes'])} military-only codes")
        return
    print(json.dumps({'shapes': shapes, 'codes': CODES, 'milCodes': MIL_CODES}))


if __name__ == '__main__':
    main()
