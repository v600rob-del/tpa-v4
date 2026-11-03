"""TPA bus replay (bus engine 0.7.0, step 2 of "stops and timetables").

Owner, 1 Nov 2026 11:37: "option 1. remember the goal is about the user experience".
Option 1 = "replay the truth": a bus matched to its timetabled journey is shown a little behind real
time (REPLAY seconds, default 90), travelling along its real route (the timetable's route shape)
between the positions it really reported, at the times it reported them. Every movement is one the
bus really made, so there is nothing to guess and nothing to correct: no wrong turns, no waiting for
a guess to be caught up, no rushing.

Why ~90 s: on the server (1 Nov 09:54) buses reported every 41 s (9 in 10 within 62 s) and reports
reach us 20-40 s late, plus up to 30 s until our next download. The replay must stay behind the
newest report it has, or a bus has to stand at its last known spot until the next report comes in.

Pure arithmetic, no I/O: the engine supplies shapes and reports.
"""
from __future__ import annotations

import math
from array import array
from bisect import bisect_left, bisect_right

M_PER_DEG = 111195.0
MAX_OFF_ROUTE_M = 40.0      # a report further than this from the route is not on it (diversion, depot, wrong match)
MAX_SPEED = 35.0            # m/s: a report needing more than this along the route is not believed
MAX_POINTS = 200            # HQ accepts up to 200 points


class Shape:
    """A route line in local metres, with the distance along it at each point."""
    __slots__ = ('lon', 'lat', 'x', 'y', 'cum', 'k')

    def __init__(self, lonlat):
        pts = [p for i, p in enumerate(lonlat) if i == 0 or p != lonlat[i - 1]]
        if len(pts) < 2:
            raise ValueError('shape too short')
        self.k = math.cos(math.radians(pts[0][1])) * M_PER_DEG
        self.lon, self.lat = array('d', (p[0] for p in pts)), array('d', (p[1] for p in pts))
        self.x = array('d', (p[0] * self.k for p in pts))
        self.y = array('d', (p[1] * M_PER_DEG for p in pts))
        self.cum = array('d', [0.0])
        for i in range(1, len(pts)):
            self.cum.append(self.cum[-1] + math.hypot(self.x[i] - self.x[i - 1], self.y[i] - self.y[i - 1]))

    @property
    def length(self):
        return self.cum[-1]

    def project(self, lon, lat, lo=0.0, hi=None):
        """(distance along, distance off) of the nearest point on the route between lo and hi metres
        along it; None if the route is further than MAX_OFF_ROUTE_M away there."""
        hi = self.length if hi is None else hi
        px, py = lon * self.k, lat * M_PER_DEG
        i0 = max(1, bisect_right(self.cum, max(0.0, lo)))
        i1 = min(len(self.cum) - 1, bisect_left(self.cum, hi) + 1)
        best = None
        for i in range(i0, i1 + 1):
            ax, ay, bx, by = self.x[i - 1], self.y[i - 1], self.x[i], self.y[i]
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            f = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
            d = math.hypot(ax + vx * f - px, ay + vy * f - py)
            if best is None or d < best[1] - 1e-9:
                best = (self.cum[i - 1] + f * (self.cum[i] - self.cum[i - 1]), d)
        if best is None or best[1] > MAX_OFF_ROUTE_M:
            return None
        return best

    def point_at(self, m):
        m = max(0.0, min(self.length, m))
        i = max(1, min(len(self.cum) - 1, bisect_left(self.cum, m)))
        seg = self.cum[i] - self.cum[i - 1]
        f = 0.0 if seg <= 0 else (m - self.cum[i - 1]) / seg
        return (self.lon[i - 1] + (self.lon[i] - self.lon[i - 1]) * f, self.lat[i - 1] + (self.lat[i] - self.lat[i - 1]) * f)

    def vertices_between(self, a, b):
        """Distances along the route of the route's own corners strictly between a and b."""
        i, j = bisect_right(self.cum, a), bisect_left(self.cum, b)
        return [self.cum[k] for k in range(i, j)]


def follow(shape, reports, known=None):
    """Place each report [(time, lon, lat), ...] (oldest first) on the route. `known` is the earlier
    result [(time, m), ...]: only newer reports are added. A bus never goes backwards along its route
    (GPS jitter at a stop counts as standing still). Returns the new list, or None when the newest
    report is off the route (the engine then moves this bus the ordinary way)."""
    out = list(known or [])
    for t, lon, lat in reports:
        if out and t <= out[-1][0]:
            continue
        if out:
            pt, pm = out[-1]
            hi = pm + max(300.0, MAX_SPEED * (t - pt))
            got = shape.project(lon, lat, pm - 30.0, hi)
            if got is None:
                return None
            out.append((t, max(pm, got[0])))
        else:
            got = shape.project(lon, lat)
            if got is None:
                return None
            out.append((t, got[0]))
    return out


def _keep_left(points, metres, lat):
    """Shift a line `metres` to the left of its direction of travel (UK traffic keeps left), so buses
    going opposite ways along the same road are drawn on their own sides (owner 1 Nov 01:03)."""
    if metres <= 0 or len(points) < 2:
        return points
    k = math.cos(math.radians(lat)) * M_PER_DEG
    out = []
    for i, (x, y) in enumerate(points):
        a, b = points[max(0, i - 1)], points[min(len(points) - 1, i + 1)]
        dx, dy = (b[0] - a[0]) * k, (b[1] - a[1]) * M_PER_DEG
        L = math.hypot(dx, dy)
        if L == 0:
            out.append((x, y)); continue
        out.append((x - dy / L * metres / k, y + dx / L * metres / M_PER_DEG))
    return out


JUMP_SECONDS = 0.1          # a jump in a track: two points this close in time (HQ 4.2.1 draws it as a fade)


def road_route(legs):
    """0.7.1, for buses not matched to a timetable: one line made of the drives between consecutive
    reports (from the road map). legs = [(t_a, t_b, [(lon, lat), ...] or None)], oldest first; None =
    no drive found, so the bus is shown jumping (fading) to its next report rather than going a made-up
    way. Returns (Shape, placed [(t, m)], jumps {index of the report a jump starts from}) or None."""
    pts, placed, jumps = [], [], set()
    for i, (ta, tb, way) in enumerate(legs):
        if not way or len(way) < 2:
            return None
        if not pts:
            pts.append(tuple(way[0]))
            placed.append((ta, None))
        elif tuple(way[0]) != pts[-1]:
            pts.append(tuple(way[0]))                 # a small join where two drives meet
        for q in way[1:]:
            if tuple(q) != pts[-1]:
                pts.append(tuple(q))
        placed.append((tb, len(pts) - 1))
        if getattr(way, 'jump', False):
            jumps.add(i)
    if len(pts) < 2:
        return None
    sh = Shape(pts)
    # report positions as distances along the line (point indices -> metres)
    out = []
    for t, idx in placed:
        out.append((t, sh.cum[0] if idx is None else sh.cum[min(idx, len(sh.cum) - 1)]))
    return sh, out, jumps


class Jump(list):
    """A two-point 'way' (where the bus was, where it was next seen) to be shown as a jump."""
    jump = True


def track(shape, placed, now, delay, keep_left=0.0, jumps=frozenset()):
    """Where to show the bus now, and its way ahead with clock times, when it is replayed `delay`
    seconds behind its reports. placed = [(report time, m along route), ...].
    Returns dict(position=(lon, lat), points=[(lon, lat)], times=[epoch s], waiting=bool) or None.
    waiting = the replay has caught up with the newest report (the bus stands there until more come)."""
    if not placed:
        return None
    shown = now - delay                         # the moment being shown
    seq = [(t, m) for t, m in placed]
    if shown >= seq[-1][0]:
        p = shape.point_at(seq[-1][1])
        return {'position': p, 'points': [p], 'times': [round(now, 1)], 'waiting': len(seq) > 0}
    if shown <= seq[0][0]:
        start_t, start_m, first = shown, seq[0][1], 0
    else:
        i = next(k for k in range(1, len(seq)) if seq[k][0] > shown)
        (t0, m0), (t1, m1) = seq[i - 1], seq[i]
        start_m = m0 if (i - 1) in jumps else m0 + (m1 - m0) * (shown - t0) / (t1 - t0)
        start_t, first = shown, i
    rest = seq[first:]
    # distance along -> clock time, straight between reports (the bus's average pace between them)
    tm = [(start_t, start_m, first - 1)] + [(t, m, first + k) for k, (t, m) in enumerate(rest)]
    marks = []
    for (ta, ma, _), (tb, mb, ib) in zip(tm, tm[1:]):
        if not marks:
            marks.append((ta, ma))
        if (ib - 1) in jumps:                   # stays where it was last seen, then is seen at the next report
            if tb - JUMP_SECONDS > ta:
                marks.append((tb - JUMP_SECONDS, ma))
        elif mb > ma:
            for m in shape.vertices_between(ma, mb):
                marks.append((ta + (tb - ta) * (m - ma) / (mb - ma), m))
        marks.append((tb, mb))
    if len(marks) > MAX_POINTS:                 # keep the reports themselves, thin the corners between
        keep = {0, len(marks) - 1} | {k for k, x in enumerate(marks) if any(x[0] == r[0] for r in rest)}
        keep |= {k for k in range(len(marks) - 1) if marks[k + 1][0] - marks[k][0] <= JUMP_SECONDS + 1e-6}   # both ends of a jump
        step = (len(marks) - 1) / (MAX_POINTS - len(keep))
        keep |= {round(j * step) for j in range(MAX_POINTS - len(keep))}
        marks = [marks[k] for k in sorted(keep)][:MAX_POINTS]
    pts = [shape.point_at(m) for _, m in marks]
    pts = _keep_left(pts, keep_left, pts[0][1])
    times = [round(t + delay, 1) for t, _ in marks]
    for k in range(1, len(times)):              # never backwards in time after rounding
        times[k] = max(times[k], times[k - 1])
    return {'position': pts[0], 'points': pts, 'times': times, 'waiting': False}


def from_now(r, now):
    """0.8.4 (owner 3 Nov 12:32: load test, the bus engine was half the server's work): a track worked out a few
    seconds ago, cut to start at `now` - the same as working it out again, for a fraction of the cost. The track's
    clock times do not change; only the part already driven is dropped and the start point is moved along."""
    if r is None:
        return None
    pts, times = r['points'], r['times']
    if len(times) < 2 or now <= times[0]:
        return r
    if now >= times[-1]:
        return dict(r, position=pts[-1], points=[pts[-1]], times=[round(now, 1)], waiting=True)
    i = next(k for k in range(1, len(times)) if times[k] > now)
    t0, t1 = times[i - 1], times[i]
    f = 1.0 if t1 <= t0 else (now - t0) / (t1 - t0)
    (x0, y0), (x1, y1) = pts[i - 1], pts[i]
    p = (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
    return dict(r, position=p, points=[p] + list(pts[i:]), times=[round(now, 1)] + list(times[i:]), waiting=False)
