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
