"""Train engine helper: routes along the real railway (Network Rail track model, OGL v3.0).

Given two places, returns the line the track actually follows between them, so the map
can glide a train along the rails instead of cutting across fields. Standard library only;
loads data/rail-network.json.gz (built by tools/build_rail_network.py). Any problem (file
missing, no route, odd route) returns None and the engine falls back safely.
"""
import gzip
import heapq
import json
import math
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent / 'data' / 'rail-network.json.gz'
CELL = 0.02          # degrees, for finding the nearest junction quickly
MAX_SNAP_M = 1500    # a place further than this from any track gets no route (metres)
MAX_EXPANSIONS = 80000
MAX_POINTS = 160
# 0.4.2 (owner 1 Nov 17:01: trains "slightly of[f] the railway indication line"): stations and timing points
# are often a few to tens of metres beside the track (station building, forecourt). A train is drawn at the
# nearest point ON the track when that is within this distance, and its route starts and ends there.
ON_TRACK_M = 300


def metres(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(min(1.0, math.sqrt(a)))


class TrackRouter:
    def __init__(self, path=DEFAULT_FILE):
        with gzip.open(path, 'rb') as f:
            data = json.loads(f.read(40_000_000))
        self.nodes = [(float(a), float(b)) for a, b in data['nodes']]
        self.geom, self.cum, self.adj = [], [], [[] for _ in self.nodes]
        self.grid = {}
        for a, b, _length, mid in data['edges']:
            a, b = int(a), int(b)
            if not (0 <= a < len(self.nodes) and 0 <= b < len(self.nodes)):
                continue
            line = [self.nodes[a]] + [(float(x), float(y)) for x, y in mid] + [self.nodes[b]]
            cum = [0.0]
            for i in range(len(line) - 1):
                cum.append(cum[-1] + metres(*line[i], *line[i + 1]))
            k = len(self.geom)
            self.geom.append((a, b, line))
            self.cum.append(cum)
            self.adj[a].append((k, b, cum[-1]))
            self.adj[b].append((k, a, cum[-1]))
            # index every piece of track in the grid squares it passes through
            for i in range(len(line) - 1):
                (x1, y1), (x2, y2) = line[i], line[i + 1]
                steps = max(1, int(max(abs(x2 - x1), abs(y2 - y1)) / (CELL / 2)))
                seen = set()
                for t in range(steps + 1):
                    c = (int((x1 + (x2 - x1) * t / steps) // CELL), int((y1 + (y2 - y1) * t / steps) // CELL))
                    if c not in seen:
                        seen.add(c)
                        self.grid.setdefault(c, []).append((k, i))
        self.cache = {}

    def snap(self, lon, lat):
        """Nearest point on any track: (edge, distance along it, point) or None."""
        gx, gy = int(lon // CELL), int(lat // CELL)
        kx = 111320 * math.cos(math.radians(lat))
        best, bd = None, MAX_SNAP_M
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k, i in self.grid.get((gx + dx, gy + dy), ()):
                    (x1, y1), (x2, y2) = self.geom[k][2][i], self.geom[k][2][i + 1]
                    ax, ay, bx, by = (x1 - lon) * kx, (y1 - lat) * 110540, (x2 - lon) * kx, (y2 - lat) * 110540
                    vx, vy = bx - ax, by - ay
                    vv = vx * vx + vy * vy
                    t = 0.0 if vv == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / vv))
                    d = math.hypot(ax + t * vx, ay + t * vy)
                    if d < bd:
                        seg = self.cum[k][i + 1] - self.cum[k][i]
                        best, bd = (k, self.cum[k][i] + t * seg, (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)), d
        return best

    def place(self, lon, lat):
        """(lon, lat) moved onto the nearest track if within ON_TRACK_M, else None."""
        s = self.snap(lon, lat)
        if s and metres(lon, lat, *s[2]) <= ON_TRACK_M:
            return (round(s[2][0], 6), round(s[2][1], 6))
        return None

    def part(self, k, d1, d2):
        """Shape of edge k between distances d1 and d2 along it (either direction)."""
        line, cum = self.geom[k][2], self.cum[k]
        lo, hi = min(d1, d2), max(d1, d2)
        def at(d):
            for i in range(len(cum) - 1):
                if cum[i + 1] >= d:
                    seg = cum[i + 1] - cum[i] or 1e-9
                    t = (d - cum[i]) / seg
                    (x1, y1), (x2, y2) = line[i], line[i + 1]
                    return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            return line[-1]
        pts = [at(lo)] + [line[i] for i in range(len(line)) if lo < cum[i] < hi] + [at(hi)]
        return pts if d1 <= d2 else pts[::-1]

    def route(self, a, b):
        """a, b = (lon, lat). Returns [(lon, lat), ...] from a to b along the track, or None."""
        key = (round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4))
        if key in self.cache:
            return self.cache[key]
        try:
            result = self._route(a, b)
        except (IndexError, ValueError, ZeroDivisionError):
            result = None
        if len(self.cache) > 20000:
            self.cache.clear()
        self.cache[key] = result
        return result

    def _route(self, a, b):
        sa, sb = self.snap(*a), self.snap(*b)
        if not sa or not sb:
            return None
        straight = metres(*a, *b)
        (ka, da, pa), (kb, db, pb) = sa, sb
        # 0.4.2: start and end on the track itself (as the standing train is drawn), not beside it
        if metres(*a, *pa) <= ON_TRACK_M:
            a = pa
        if metres(*b, *pb) <= ON_TRACK_M:
            b = pb
        if ka == kb:                                   # both on the same piece of track
            pts = [tuple(a)] + self.part(ka, da, db) + [tuple(b)]
            return finish(pts, straight)
        A, B = -1, -2                                  # temporary start and end points
        ea, eb = self.geom[ka], self.geom[kb]
        goal = pb
        dist, prev = {A: 0.0}, {}
        heap = [(metres(*pa, *goal), 0.0, A)]
        ends_b = {eb[0]: db, eb[1]: self.cum[kb][-1] - db}
        expansions = 0
        while heap:
            _, d, n = heapq.heappop(heap)
            if n == B:
                break
            if d > dist.get(n, math.inf):
                continue
            expansions += 1
            if expansions > MAX_EXPANSIONS or d > straight * 3 + 5000:
                return None
            if n == A:
                steps = [(('a',), ea[0], da), (('a',), ea[1], self.cum[ka][-1] - da)]
            else:
                steps = [((k,), m, length) for k, m, length in self.adj[n]]
                if n in ends_b:
                    steps.append((('b',), B, ends_b[n]))
            for via, m, length in steps:
                nd = d + length
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = (n, via[0])
                    h = 0.0 if m == B else metres(*self.nodes[m], *goal)
                    heapq.heappush(heap, (nd + h, nd, m))
        if B not in dist or dist[B] > straight * 2.5 + 3000:
            return None
        chain, n = [], B
        while n != A:
            p, via = prev[n]
            if via == 'b':
                chain.append(self.part(kb, 0 if ea is None else (0 if p == eb[0] else self.cum[kb][-1]), db))
            elif via == 'a':
                chain.append(self.part(ka, da, 0 if n == ea[0] else self.cum[ka][-1]))
            else:
                g0, g1, line = self.geom[via]
                chain.append(line if g0 == p else line[::-1])
            n = p
        pts = [tuple(a)]
        for piece in reversed(chain):
            pts.extend(piece)
        pts.append(tuple(b))
        return finish(pts, straight)


def finish(pts, straight):
    out = [pts[0]]
    for p in pts[1:]:
        if metres(*out[-1], *p) > 1:
            out.append(p)
    return simplify(out, MAX_POINTS)


def simplify(pts, limit):
    """Thin a line to at most `limit` points, keeping its shape (Douglas-Peucker, growing tolerance)."""
    tol = 5.0
    while True:
        keep = _dp(pts, tol)
        if len(keep) <= limit:
            return [(round(x, 5), round(y, 5)) for x, y in keep]
        tol *= 2


def _dp(pts, tol_m):
    if len(pts) < 3:
        return list(pts)
    k = math.cos(math.radians(pts[0][1]))
    xy = [(x * 111320 * k, y * 110540) for x, y in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, -1
        for m in range(i + 1, j):
            x, y = xy[m]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / length
            if d > best:
                best, bi = d, m
        if best > tol_m:
            keep[bi] = True
            stack += [(i, bi), (bi, j)]
    return [p for p, kk in zip(pts, keep) if kk]
