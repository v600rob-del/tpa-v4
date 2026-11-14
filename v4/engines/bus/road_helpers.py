"""TPA v4 bus engine: ROAD HELPERS (bus 0.20.3).

Owner 14 Nov 2026 11:25 ("Lets get it done"), fix C: in the daytime (18,000 buses) the bus engine used one processor
flat out and a round of published tiles took ~66 s instead of 10 (server 11:14: 36 of every 60 processor-seconds went
on working out the drive along the roads between two reports of buses not on a timetable route). A Python program
uses one processor at a time, so that work is shared out to helper programs, each on its own processor.

One drive ("leg") needs only the road map (a file on disk) and the reports: compute_leg() below is the ONE
implementation, used by the engine itself (bus_engine._leg) and by the helpers, so the map shows exactly the same.

Helper protocol (stdin/stdout of `python3 road_helpers.py <road map file>`): 4-byte length + pickled list of jobs
[(job id, a, b, before)], answered by 4-byte length + pickled list [(job id, way)] where way is ('w', [(lon, lat)...])
or ('j', [(lon, lat), (lon, lat)]) for a jump. Pickle only ever passes between the engine and its own helpers.
"""
from __future__ import annotations

import importlib.util
import math
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HDR = struct.Struct('<I')
LEG_MAX_M = 3000.0
PLACE_M = 20.0
HELPER_TILES = 250                     # each helper's own road-map memory (tiles kept)
HELPER_MB = 150                        # ... and at most this many MB of road map in memory (the engine's own: 200)


def metres(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742000 * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return round(math.degrees(math.atan2(y, x)) % 360, 1)


def compute_leg(rm, a, b, before, leg_max_m=LEG_MAX_M, place_m=PLACE_M):
    """The drive from report a to report b (each (t, lat, lon)); `before` is the report before a or None.
    Returns ('w', points) along the roads, or ('j', [from, to]) to be shown as a jump. Exceptions from the road map
    propagate (the caller records them and falls back)."""
    (ta, la, oa), (tb, lb, ob) = a, b
    straight = metres(la, oa, lb, ob)
    brg = None
    if before is not None and metres(before[1], before[2], la, oa) >= 10:
        brg = bearing(before[1], before[2], la, oa)
    way = None
    error = None
    try:
        if straight < 15:
            p = rm.place(oa, la, brg, place_m) if rm else None
            way = [(p[0], p[1]), (p[0], p[1])] if p else [(oa, la), (oa, la)]
        elif rm is not None and straight <= leg_max_m:
            got = rm.between(oa, la, brg, ob, lb, max_m=min(leg_max_m, max(300.0, 2.5 * straight + 150)))
            way = [tuple(q) for q in got] if got else None
    except Exception as exc:   # one odd bus must never break the layer
        error = exc
    if way is None:
        pa = (rm.place(oa, la, brg, place_m) if rm else None) or (oa, la)
        pb = (rm.place(ob, lb, None, place_m) if rm else None) or (ob, lb)
        return ('j', [tuple(pa), tuple(pb)]), error
    return ('w', way), error


# ---------------------------------------------------------------- the helper program
def _read_msg(f):
    head = f.read(HDR.size)
    if len(head) < HDR.size:
        return None
    n = HDR.unpack(head)[0]
    body = f.read(n)
    if len(body) < n:
        return None
    return pickle.loads(body)


def _write_msg(f, obj):
    body = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    f.write(HDR.pack(len(body)) + body)
    f.flush()


def helper_main(road_path: str) -> None:
    spec = importlib.util.spec_from_file_location('tpa_road_map_helper', HERE / 'road_map.py')
    road_map = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(road_map)
    try:
        os.nice(5)                       # the engine's own work (downloads, boards) comes first
    except OSError:
        pass
    rm = road_map.RoadMap(road_path, max_tiles=HELPER_TILES, max_bytes=HELPER_MB * 1_000_000)
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while True:
        jobs = _read_msg(inp)
        if jobs is None:
            return
        res = []
        for jid, a, b, before in jobs:
            try:
                way, _ = compute_leg(rm, a, b, before)
            except Exception:            # the engine works this one out itself
                way = None
            res.append((jid, way))
        _write_msg(out, res)


# ---------------------------------------------------------------- the engine's side
class RoadHelpers:
    """A few helper programs sharing the road-drive work. legs(jobs) -> {job id: way}; anything a helper could not do
    in time is simply missing (the engine then does it itself, as before 0.20.3). A helper that fails is restarted."""

    def __init__(self, road_path: str, count: int, python: str = sys.executable):
        self.road_path, self.count, self.python = road_path, max(0, int(count)), python
        self.procs: list = [None] * self.count
        self.lock = threading.Lock()
        self.stats = {'helpers': self.count, 'rounds': 0, 'legsSent': 0, 'legsDone': 0, 'restarts': 0, 'lastWallSeconds': 0.0,
                      'error': ''}

    def _start(self, i):
        p = subprocess.Popen([self.python, str(HERE / 'road_helpers.py'), self.road_path], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
        self.procs[i] = p
        return p

    def _alive(self, i):
        p = self.procs[i]
        if p is None or p.poll() is not None:
            if p is not None:
                self.stats['restarts'] += 1
                self._kill(i)
            p = self._start(i)
        return p

    def _kill(self, i):
        p = self.procs[i]
        self.procs[i] = None
        if p is not None:
            try:
                p.kill(); p.wait(timeout=5)
            except Exception:
                pass
            for f in (p.stdin, p.stdout):
                try:
                    f.close()
                except Exception:
                    pass

    def legs(self, jobs: list, timeout: float = 20.0) -> dict:
        """jobs: [(job id, a, b, before)]. Shared out evenly; all helpers work at once."""
        if not jobs or not self.count:
            return {}
        t0 = time.monotonic()
        out: dict = {}
        with self.lock:
            parts = [jobs[i::self.count] for i in range(self.count)]
            busy = {}
            for i, part in enumerate(parts):
                if not part:
                    continue
                try:
                    p = self._alive(i)
                    _write_msg(p.stdin, part)
                    busy[p.stdout.fileno()] = (i, p)
                except Exception as exc:
                    self.stats['error'] = ('send: ' + type(exc).__name__)[:80]
                    self._kill(i)
            deadline = t0 + timeout
            while busy:
                left = deadline - time.monotonic()
                if left <= 0:
                    for i, _ in busy.values():   # too slow: stopped, restarted next round; the engine does the rest
                        self.stats['error'] = 'helper too slow'
                        self._kill(i)
                    break
                ready, _, _ = select.select(list(busy), [], [], left)
                for fd in ready:
                    i, p = busy.pop(fd)
                    try:
                        res = _read_msg(p.stdout)
                    except Exception:
                        res = None
                    if res is None:
                        self.stats['error'] = 'helper stopped'
                        self._kill(i)
                        continue
                    for jid, way in res:
                        if way is not None:
                            out[jid] = way
            self.stats['rounds'] += 1
            self.stats['legsSent'] += len(jobs)
            self.stats['legsDone'] += len(out)
            self.stats['lastWallSeconds'] = round(time.monotonic() - t0, 2)
        return out

    def close(self):
        with self.lock:
            for i in range(self.count):
                self._kill(i)


if __name__ == '__main__':
    helper_main(sys.argv[1])
