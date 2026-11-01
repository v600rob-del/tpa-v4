"""Scale check for the bus road map (bus engine 0.4.0) on a pretend city as dense as central London:
a 20 km x 20 km grid of wiggly streets every 70 m (~160,000 road links, several times denser than
Britain's average). Measures build time and peak memory, then 400 buses in one view (the most a
view shows), cold and warm.  Run:  python3 v4/tests/load/bus_roads_scale_check.py
Real numbers to compare with the server limit (bus engine MemoryMax 300 MB, shared with the helper).
"""
import importlib.util
import io
import math
import os
import random
import resource
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PACKAGE / 'v4/tests/engines'))
import os_roads_fixture as fx  # noqa: E402

spec = importlib.util.spec_from_file_location('rm', PACKAGE / 'v4/engines/bus/road_map.py')
rm = importlib.util.module_from_spec(spec); spec.loader.exec_module(rm)

SIZE, STEP = 20000, 70
E0, N0 = 520000, 170000


def wiggle(a, b, rnd):
    pts = [a]
    for k in range(1, 5):
        f = k / 5
        pts.append((a[0] + (b[0] - a[0]) * f + rnd.uniform(-4, 4), a[1] + (b[1] - a[1]) * f + rnd.uniform(-4, 4)))
    return pts + [b]


def city():
    rnd = random.Random(1)
    n = SIZE // STEP
    for i in range(n):
        for j in range(n):
            x, y = E0 + i * STEP, N0 + j * STEP
            yield wiggle((x, y), (x + STEP, y), rnd), 'Unclassified', '', f'Street {j}'
            yield wiggle((x, y), (x, y + STEP), rnd), 'Unclassified', '', f'Avenue {i}'


def rss():
    return [int(l.split()[1]) // 1024 for l in open('/proc/self/status') if l.startswith('VmRSS')][0]


def main():
    d = tempfile.mkdtemp()
    roads = list(city())
    print(f'pretend city: {len(roads):,} road links')
    with zipfile.ZipFile(d + '/os.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('oproad_essh_gb/data/TQ_RoadLink.shp', fx._shp([r[0] for r in roads]))
        z.writestr('oproad_essh_gb/data/TQ_RoadLink.dbf', fx._dbf(fx._rows(roads)))
    del roads
    import gc; gc.collect()
    print(f'zip {os.path.getsize(d + "/os.zip") / 1e6:.1f} MB')
    # build in a child process, like the engine does, and measure its peak memory
    code = (f"import importlib.util,resource,time;s=importlib.util.spec_from_file_location('r',{str(PACKAGE / 'v4/engines/bus/road_map.py')!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);t=time.time();"
            f"n=m.build({d + '/os.zip'!r},{d + '/roads.sqlite'!r});"
            "print(n,round(time.time()-t,1),[int(l.split()[1])//1024 for l in open('/proc/self/status') if l.startswith('VmHWM')][0])")
    links, secs, peak = subprocess.check_output([sys.executable, '-c', code], text=True).split()
    links, secs = int(links), float(secs)
    print(f'build: {links:,} pieces in {secs:.0f} s, peak memory {peak} MB, store {os.path.getsize(d + "/roads.sqlite") / 1e6:.1f} MB')
    per = secs / (2 * (SIZE // STEP) ** 2)
    print(f'  -> whole of GB (~4 M links) at this rate: ~{per * 4e6 / 60:.0f} min here; the server CPU is '
          'slower (allow 3-5x). It runs at low priority, and only when OS publishes a new edition.')
    before = rss()
    m = rm.RoadMap(d + '/roads.sqlite')
    rnd = random.Random(2)
    buses = []
    cx, cy = E0 + SIZE / 2, N0 + SIZE / 2
    for _ in range(400):   # 400 buses spread over a ~7 km view, on streets, heading along them
        i, j = rnd.randrange(-50, 50), rnd.randrange(-50, 50)
        x, y = cx + i * STEP + rnd.uniform(5, 60), cy + j * STEP
        buses.append((rm.osgb_to_wgs84(x, y), rnd.choice((90, 270)), rnd.uniform(3, 12) * 75))
    for label in ('cold', 'warm'):
        t = time.time(); ok = 0
        for (lon, lat), b, dist in buses:
            ok += m.ahead(lon, lat, b, dist) is not None
        dt = time.time() - t
        print(f'{label}: 400 buses in {dt * 1000:.0f} ms ({dt * 2.5:.1f} ms each), {ok} on roads, {len(m.tiles)} tiles held')
    after = rss()
    print(f'engine memory for the road map: ~{after - before} MB (cap {m.max_tiles} tiles)')
    # 0.4.1 worst case: the widest view that still follows roads (0.12 deg tall) packed with 400
    # buses, two rounds of reports. Every needed area must fit in memory (no reloading each round).
    m2 = rm.RoadMap(d + '/roads.sqlite')
    base = rss()
    span_m = 0.12 * 111195
    rows = []
    for r in range(2):
        rnd = random.Random(5)
        t = time.time(); loads0 = m2.tile_loads
        for _ in range(400):
            x = cx + rnd.uniform(-span_m * 0.8, span_m * 0.8); y = cy + rnd.uniform(-span_m / 2, span_m / 2)
            y = N0 + round((y - N0) / STEP) * STEP + r * 0.0          # on a street
            m2.ahead(*rm.osgb_to_wgs84(x + r * 80, y), 90, 8 * 75)  # next round: 80 m further on
        rows.append((time.time() - t, m2.tile_loads - loads0))
    for r, (dt, loads) in enumerate(rows):
        print(f'widest road view, round {r + 1}: {dt * 1000:.0f} ms for 400 buses, {loads} area loads, {len(m2.tiles)} held')
    print(f'road map memory in that view: ~{rss() - base} MB (cap {m2.max_tiles} areas)')


if __name__ == '__main__':
    main()
