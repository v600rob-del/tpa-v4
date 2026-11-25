"""Bus forecast - "where every bus almost is now" (bus 0.22.0, forecast plan Stage 1; Rob 14 Nov 2026 19:58: "Yes, after the
route files").

The stop boards already work out how late a bus is from its newest report and the timetable (stop_boards.place_on_journey).
This does the same sum along the bus's own route line, and carries the bus forward: it reaches each stop ahead at the
timetabled time plus that lateness, and waits at a stop where the timetable has it wait.

Everything is in metres along the route line (as the published reports `r` are) and clock seconds (epoch).

  knots(stops, first_departure)   the timetable as a distance/time line: [(metres, time)], each time later than the last
  lateness(knots, t, m)           seconds late (+) or early (-) for a bus at m metres at time t
  forecast(t, m, knots)           [(time, metres)] from the newest report forward, up to HORIZON_S ahead

Pure functions, no engine state: the page will get a mirror of this (like replay.py and bus-replay.js), tested against it.
"""
from __future__ import annotations

import math

EARLY_LIMIT_S = 600.0        # as the stop boards: never more than 10 minutes early
HORIZON_S = 300.0            # 5 minutes ahead: reports come about once a minute; keeps the tiles small
AT_STOP_M = 15.0             # a bus this close to a stop is at it (GPS is often 5-20 m out; reports are placed on the line)
MAX_SPEED_MPS = 30.0         # ~67 mph: never faster than this, whatever the timetable's rounding says
LAST_LEG_MPS = 8.0           # [A] ~18 mph: only when a timetable gives the last stop the same minute as the one before


def knots(stops, first_departure: float):
    """stops: [(metres along the line or None, arrive s, depart s)] in journey order, times in seconds on the journey's
    own timetable clock; first_departure: when the first stop's departure is, as a clock time (epoch).
    Returns [(metres, epoch)]: arrivals and (where the timetable has the bus wait) departures, metres never going back,
    times always later. Timetables often give one time to several stops in a row (whole minutes, or exact times only at
    main stops): those stops are left out here, so the bus moves through them at an even pace between the times given."""
    rows = [(m, a, d) for m, a, d in stops if m is not None]
    if not rows:
        return []
    zero = first_departure - stops[0][2]
    out = []
    for i, (m, a, d) in enumerate(rows):
        a, d = zero + a, zero + max(a, d)
        if not out:
            out.append((m, a))
        elif m < out[-1][0]:
            continue                                   # placed behind the one before (the line passes back): skip
        elif a > out[-1][1]:
            out.append((m, a))                         # reached here at its time (same place, later = a wait)
        elif i == len(rows) - 1 and m > out[-1][0]:    # the last stop has no later time: an assumed pace
            out.append((m, out[-1][1] + (m - out[-1][0]) / LAST_LEG_MPS))
        if d > out[-1][1]:
            out.append((m, d))                         # leaves later than it arrives: waits here
    return out


def _timetabled(kn, m):
    """(earliest, latest) clock time the timetable has the bus at m metres."""
    if m <= kn[0][0] + AT_STOP_M:
        return float('-inf'), max(t for mm, t in kn if mm <= kn[0][0])   # at or before the first stop: waiting to start
    for k in range(len(kn) - 1):
        (m0, t0), (m1, t1) = kn[k], kn[k + 1]
        if m1 == m0:
            if abs(m - m0) <= AT_STOP_M:
                return t0, t1                            # at a stop where it waits
            continue
        if m0 <= m <= m1:
            if m1 - m <= AT_STOP_M and k + 2 < len(kn) and kn[k + 2][0] == m1:
                return t1, kn[k + 2][1]                  # just short of a stop where it waits
            t = t0 + (m - m0) / (m1 - m0) * (t1 - t0)
            return t, t
    return kn[-1][1], float('inf')                       # past the last stop


def lateness(kn, t: float, m: float) -> float:
    """Seconds late (+) or early (-), never more than EARLY_LIMIT_S early; 0 while it is where it should be."""
    lo, hi = _timetabled(kn, m)
    late = t - lo if t < lo else (t - hi if t > hi else 0.0)
    return max(late, -EARLY_LIMIT_S)


def forecast(t: float, m: float, kn, horizon: float = HORIZON_S):
    """[(time, metres)] starting at the newest report (t, m): the timetable from here on, shifted by how late the bus
    is, until `horizon` seconds after t (the first point beyond it is kept, so the line reaches the horizon).
    Times always later, metres never going back, never faster than MAX_SPEED_MPS. Just [(t, m)] if nothing is ahead."""
    out = [(t, m)]
    if not kn:
        return out
    late = lateness(kn, t, m)
    for km, kt in kn:
        if km < m - AT_STOP_M or (km <= m + AT_STOP_M and kt + late <= out[-1][0]):
            continue                                     # behind it, or a wait already over
        pt, pm = out[-1]
        km = max(km, pm)
        when = max(kt + late, pt + max(1.0, (km - pm) / MAX_SPEED_MPS))
        out.append((when, km))
        if when > t + horizon:
            break
    return out


# ---- bus 0.29.0 (Rob 24 Nov 2026 22:14, "yes that sounds logical to me"; TPA-BUS-GUESS-CHECK-2026-11-24): measured 22:09,
# 502 buses: the forecast is already as close as any simple guess (typical 105 m in the first minute), so the ~100 m is
# what cannot be known from reports that arrive 30-40 s late every 30-50 s. Two changes:
#  1. cautious: the forecast as if the bus were CAUTIOUS_S later - as close (102 m) but shown AHEAD of the real bus 33% of
#     the time instead of 52% (a bus shown ahead has to stop or fade back; one shown behind can catch up smoothly);
#  2. bend, never snap (TPA-SERVER-LIVE-PLAN-2026-11-18 Stage C): each new path starts where the previous one had the bus,
#     follows it for LEAD_S (the page may still be playing the old one), then eases onto the new forecast: behind -> up to
#     CATCH_FACTOR times the forecast's pace (at least CATCH_MIN_EXTRA m/s more, never over CATCH_MAX_MPS); ahead -> carries on
#     at AHEAD_SHARE of the forecast's pace until the forecast reaches it. Never backwards. Only a gap over BEND_JUMP_M (a
#     diversion, a new start) is put straight onto the new forecast.
CAUTIOUS_S = 15.0
# Settings "D" (Rob 24 Nov 23:27, after tpa-forecastcheck tried four side by side at 23:23: what the page would show
# typical 170 m against today's 138 m, fades at a new report 4% against 56%, shown ahead 43% against 52%)
LEAD_S = 5.0
CATCH_FACTOR = 3.0
CATCH_MIN_EXTRA = 5.0
CATCH_MAX_MPS = 25.0          # ~56 mph
AHEAD_SHARE = 0.35
BEND_JUMP_M = 600.0
BEND_STEP_S = 5.0             # a point every 5 s while easing (a wait still shows as two points at one place)


def along(fc, t: float) -> float:
    """Metres at time t on [(time, metres)]: held before the first point and after the last."""
    if t <= fc[0][0]:
        return float(fc[0][1])
    for (t0, m0), (t1, m1) in zip(fc, fc[1:]):
        if t <= t1:
            return m0 + (m1 - m0) * ((t - t0) / (t1 - t0) if t1 > t0 else 1.0)
    return float(fc[-1][1])


def cautious(fc, wait_s: float = CAUTIOUS_S):
    """The forecast as if the bus were wait_s seconds later: it waits at its report for wait_s, then follows the same
    path that much later."""
    if not fc or len(fc) < 2 or wait_s <= 0:
        return list(fc or [])
    return [tuple(fc[0])] + [(t + wait_s, m) for t, m in fc[1:]]


def bend(prev, target, made_at: float, lead_s: float = LEAD_S, catch_factor: float = CATCH_FACTOR,
         catch_extra: float = CATCH_MIN_EXTRA, catch_max: float = CATCH_MAX_MPS, ahead_share: float = AHEAD_SHARE):
    """The path to publish: `prev` (the path published before, same journey, or None) up to made_at + lead_s, then eased
    onto `target`. Both are [(time, metres)]. Starts at target's first time (the newest report), times always later,
    metres never going back. Without prev, or with a gap over BEND_JUMP_M, it is simply target."""
    if not target or len(target) < 2 or not prev or len(prev) < 2:
        return list(target or [])
    start = float(target[0][0])
    tb = max(float(made_at) + lead_s, start)
    x = along(prev, tb)
    if abs(x - along(target, tb)) > BEND_JUMP_M:
        return list(target)
    out = [(start, along(prev, start))]
    for t, _ in prev:                                   # the old path's own corners between the report and tb
        if start < t < tb:
            out.append((float(t), along(prev, t)))
    if tb > out[-1][0]:
        out.append((tb, x))
    end = float(target[-1][0])
    t, last_kept = tb, tb
    walk = _Walker(target)                            # 0.29.3: same answers as along(target, .), times only go on
    was_aim = walk.at(t)
    while t < end - 1e-6:
        t1 = min(t + 1.0, end)
        dt = t1 - t
        aim = walk.at(t1)
        pace = max(0.0, aim - was_aim) / dt               # metres a second
        was_aim = aim
        if x < aim - 0.5:                               # behind: catch up, never past where it should be
            x = min(aim, x + dt * min(max(pace * catch_factor, pace + catch_extra), catch_max))
        elif x > aim + 0.5:                             # ahead: carry on slowly until the forecast reaches it
            x += dt * pace * ahead_share
        else:
            x = aim
        if abs(x - aim) <= 0.5:                         # back on the forecast: from here on it is the forecast
            out.append((t1, x))
            out.extend((float(tt), float(mm)) for tt, mm in target if tt > t1)
            return _tidy(out)
        if t1 - last_kept >= BEND_STEP_S:
            out.append((t1, x))
            last_kept = t1
        t = t1
    if end > out[-1][0]:
        out.append((end, x))
    return _tidy(out)


class _Walker:
    """bus 0.29.3 (Rob 25 Nov 2026 17:23 "B"; py-spy 17:21: bend 7.8% of publishing, `along` its busiest line): along(fc, t)
    for times that only go on, keeping its place instead of searching from the start every second. Exactly the same
    arithmetic and the same choice of piece (the first whose end time is not before t), so bend() gives identical paths."""
    __slots__ = ('fc', 'k', 'n')

    def __init__(self, fc):
        self.fc, self.k, self.n = fc, 0, len(fc)

    def at(self, t):
        fc = self.fc
        if t <= fc[0][0]:
            return float(fc[0][1])
        k, n = self.k, self.n
        while k + 1 < n and t > fc[k + 1][0]:
            k += 1
        self.k = k
        if k + 1 >= n:
            return float(fc[-1][1])
        t0, m0 = fc[k]
        t1, m1 = fc[k + 1]
        return m0 + (m1 - m0) * ((t - t0) / (t1 - t0) if t1 > t0 else 1.0)


def _tidy(pts):
    """Times strictly later, metres never going back."""
    out = []
    for t, m in pts:
        if out and t <= out[-1][0]:
            continue
        out.append((t, max(m, out[-1][1]) if out else m))
    return out


TOWN_HORIZON_S = 150.0       # bus 0.26.0: a town-level path covers 2.5 minutes after the newest report
TOWN_STEP_M = 50.0           # sampled every 50 m along the route line...
TOWN_TOLERANCE_M = 8.0       # ...then thinned where the road is straight (no point more than 8 m off the line)


def town_path(fc, point_at, base_t, horizon_s=TOWN_HORIZON_S, step_m=TOWN_STEP_M, tol_m=TOWN_TOLERANCE_M):
    """bus 0.26.0 (Rob 18 Nov 06:16: "All levels need to be warm"): the forecast `fc` [(time, metres)] as positions, for the
    town-level tiles that carry no route line. Returns [[seconds after base_t, lon, lat], ...] from the newest report up to
    horizon_s after it; between the forecast's own points the bus keeps an even pace, so the path is sampled every step_m
    along the line and thinned where it is straight. A wait at a stop stays two points at the same place."""
    if not fc or len(fc) < 2:
        return None
    t0 = float(fc[0][0])
    end = t0 + horizon_s
    out = []
    for k in range(len(fc) - 1):
        (ta, ma), (tb, mb) = fc[k], fc[k + 1]
        if ta >= end:
            break
        if tb > end:                                   # cut the last piece at the horizon
            mb = ma + (mb - ma) * (end - ta) / (tb - ta)
            tb = end
        seg = []
        n = max(1, int(abs(mb - ma) // step_m))
        for i in range(n + 1):
            m = ma + (mb - ma) * i / n
            t = ta + (tb - ta) * i / n
            lo, la = point_at(m)
            seg.append((t, lo, la))
        # 0.29.2: no thinning piece by piece any more - the whole path is thinned once below (_thin_timed)
        if out and seg and out[-1][0] == seg[0][0]:
            seg = seg[1:]
        out.extend(seg)
    if len(out) < 2:
        return None
    out = _thin_timed(out, tol_m)
    return [[round(t - base_t, 1), round(lo, 6), round(la, 6)] for t, lo, la in out]


def thin_line(seq, tol):
    """bus 0.29.2: [[t, metres], ...] (times always later, metres never back) with the points on a steady stretch dropped:
    a point goes only if, gliding evenly in time between the points kept either side (as the page does: bus-replay.js
    alongAt), the bus is within tol metres of it. Start, end and every pause (the same metres twice) are kept. A subset of
    seq, in order, so times stay later and metres never go back."""
    n = len(seq)
    if n <= 2:
        return seq
    keep = [False] * n
    keep[0] = keep[-1] = True
    for k in range(1, n):
        if seq[k][1] == seq[k - 1][1]:
            keep[k] = keep[k - 1] = True               # a pause: both ends stay
    fixed = [k for k in range(n) if keep[k]]
    for a, b in zip(fixed, fixed[1:]):
        stack = [(a, b)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            ti, mi = seq[i]
            tj, mj = seq[j]
            rate = (mj - mi) / (tj - ti) if tj > ti else 0.0
            worst, at = -1.0, i
            for k in range(i + 1, j):
                d = abs(mi + rate * (seq[k][0] - ti) - seq[k][1])
                if d > worst:
                    worst, at = d, k
            if worst > tol:
                keep[at] = True
                stack.append((i, at))
                stack.append((at, j))
    return [q for q, k in zip(seq, keep) if k]


def _thin_timed(pts, tol):
    """bus 0.29.2 (Rob 25 Nov 2026 12:07; py-spy 11:58: town paths 11.5% of publishing): the town path thinned once over its
    whole length, not piece by piece. A bent path (0.29.0) has a point every BEND_STEP_S while it eases, and each of those
    short pieces was thinned on its own, so a bent bus carried ~12 town points instead of ~7 (measured on the real code).
    The page glides evenly in TIME between points (hq.js townPathTrack), so a point is dropped only if the bus, moving
    evenly in time between the points kept either side, is within tol metres of where it really is at that moment. Both
    points of a wait at a stop (the same place twice) are always kept. pts: [(t, lon, lat)], times never going back."""
    n = len(pts)
    if n <= 2:
        return pts
    c = math.cos(math.radians(pts[0][2])) * 111320.0
    ts = [p[0] for p in pts]
    xs = [p[1] * c for p in pts]
    ys = [p[2] * 110540.0 for p in pts]
    keep = [False] * n
    keep[0] = keep[-1] = True
    for k in range(1, n):
        if xs[k] == xs[k - 1] and ys[k] == ys[k - 1]:
            keep[k] = keep[k - 1] = True               # a wait: both ends stay
    tol2 = tol * tol
    fixed = [k for k in range(n) if keep[k]]
    hypot = math.hypot
    for a, b in zip(fixed, fixed[1:]):
        stack = [(a, b)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            ti, ax, ay = ts[i], xs[i], ys[i]
            span = ts[j] - ti
            dx, dy = xs[j] - ax, ys[j] - ay
            worst, at = -1.0, i
            for k in range(i + 1, j):
                f = (ts[k] - ti) / span if span > 0 else 0.0
                ex, ey = ax + dx * f - xs[k], ay + dy * f - ys[k]
                d = ex * ex + ey * ey
                if d > worst:
                    worst, at = d, k
            if worst > tol2:
                keep[at] = True
                stack.append((i, at))
                stack.append((at, j))
    return [p for p, k in zip(pts, keep) if k]
