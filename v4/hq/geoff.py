"""Find Geoff (HQ 4.89.0; Rob 18 Nov 2026, 18:17-18:31).

Rob: "every day a bus or train picture (just one) is swapped with a picture with Geoff driving. It will only be shown if
you click on that particular vehicle ... The rest of the details for that vehicle and journey remain accurate. The picture
would have geoff driving and the text 'You've found Geoff on *date!'". He chose: exactly one per nation (England,
Scotland, Wales, Northern Ireland) each day, buses only for now.

How: every few minutes this reads the bus engine's published city files (the same files visitors get; the bus engine is
not asked anything and stays untouched), finds every bus showing now and which nation it is in, and picks one per nation:
the live bus with the smallest hash of (UK date, bus id), so the pick is the same for everyone. A pick is kept all day
while the bus keeps running; if it has not been seen for KEEP_MISSING seconds, the next one is picked. The day's picks
are saved (survive an HQ restart). Nothing here changes what a bus is or where it is: the page only swaps the picture.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo('Europe/London')
except Exception:  # pragma: no cover - zoneinfo is in Python 3.9+
    UK = None

NATIONS = ('england', 'scotland', 'wales', 'northern-ireland')
EVERY = 300            # look again every 5 minutes
KEEP_MISSING = 1800    # a Geoff bus not seen for 30 minutes is replaced (it has finished for the day)
FRESH = 600            # only files written in the last 10 minutes count

# The page's own Northern Ireland outline (hq.js NI_OUTLINE, 4.83.0) and Ireland test, so both agree exactly
NI_OUTLINE = [[-5.43, 54.40], [-5.45, 55.25], [-6.10, 55.40], [-6.95, 55.25], [-7.25, 55.08], [-7.40, 54.95], [-7.55, 54.75],
              [-7.90, 54.70], [-8.17, 54.47], [-7.85, 54.21], [-7.55, 54.12], [-7.25, 54.20], [-7.00, 54.40], [-6.65, 54.07],
              [-6.27, 54.10], [-6.10, 54.00], [-5.90, 54.05]]
# Rough borders (an Easter egg, not a boundary map): (lat, lon) along each border, south to north
SCOTLAND_BORDER = [(54.97, -3.20), (54.97, -3.10), (54.99, -3.02), (55.05, -2.85), (55.18, -2.55), (55.30, -2.25), (55.60, -2.25), (55.82, -2.03)]
WALES_BORDER = [(51.55, -2.60), (51.82, -2.65), (52.07, -3.10), (52.33, -3.05), (52.65, -3.12), (53.00, -2.97), (53.35, -3.05)]


def _inside(lon, lat, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _border_lon(line, lat):
    """The border's longitude at this latitude (straight between its points)."""
    for (a_lat, a_lon), (b_lat, b_lon) in zip(line, line[1:]):
        if a_lat <= lat <= b_lat:
            f = (lat - a_lat) / (b_lat - a_lat) if b_lat != a_lat else 0
            return a_lon + (b_lon - a_lon) * f
    return None


def _border_lat(line, lon):
    """The Scottish border's latitude at this longitude (it runs west to east as it goes north)."""
    pts = sorted(line, key=lambda p: p[1])
    for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:]):
        if a_lon <= lon <= b_lon:
            f = (lon - a_lon) / (b_lon - a_lon) if b_lon != a_lon else 0
            return a_lat + (b_lat - a_lat) * f
    return None


def nation(lon: float, lat: float):
    """'england', 'scotland', 'wales', 'northern-ireland', or None (outside the UK, e.g. the Republic of Ireland)."""
    if not (49.8 <= lat <= 61.5 and -8.7 <= lon <= 2.0):
        return None
    if lon < -5.9 and 51.3 < lat < 55.45 and not (lon > -6.0 and lat > 55.2):      # the island of Ireland (as the page)
        return 'northern-ireland' if _inside(lon, lat, NI_OUTLINE) else None
    if lon < -5.0 and 54.0 <= lat <= 55.45 and _inside(lon, lat, NI_OUTLINE):     # east of Belfast Lough
        return 'northern-ireland'
    if lat >= 55.82:
        return 'scotland'
    if lat >= 54.97:
        if lon < -3.20:
            return 'scotland'
        b = _border_lat(SCOTLAND_BORDER, lon)
        if b is None:
            return 'england'
        return 'scotland' if lat > b else 'england'
    if 51.35 <= lat <= 53.45:
        b = _border_lon(WALES_BORDER, lat)
        if b is not None and lon < b and lon > -5.4:
            return 'wales'
        if lat < 51.55 and lon < -2.95 and lat > 51.35 and lon > -5.4 and not (lat < 51.48 and lon > -3.25):
            return 'wales'                                                       # Cardiff, Newport, Swansea, Gower
    return 'england'


def uk_date(now: float) -> str:
    return (datetime.fromtimestamp(now, UK) if UK else datetime.utcfromtimestamp(now)).strftime('%Y-%m-%d')


def _rank(day: str, bus_id: str) -> str:
    return hashlib.sha256(f'{day}|{bus_id}'.encode()).hexdigest()


_ID = re.compile(r'"i":"(bus:[^"]{1,128})"')


def live_buses(tiles_root: Path, now: float) -> dict:
    """{bus id: (lon, lat)} from the bus engine's published city files (folder bus/b)."""
    out = {}
    folder = Path(tiles_root) / 'bus' / 'b'
    try:
        files = list(folder.glob('*.json'))
    except OSError:
        return out
    for f in files:
        try:
            if now - f.stat().st_mtime > FRESH:
                continue
            items = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for it in items if isinstance(items, list) else ():
            if not isinstance(it, dict):
                continue
            bid, p = it.get('i'), it.get('p')
            if not (isinstance(bid, str) and bid.startswith('bus:') and isinstance(p, list) and p):
                continue
            last = p[-1]
            if isinstance(last, list) and len(last) >= 3 and all(isinstance(v, (int, float)) for v in last[1:3]):
                out[bid] = (float(last[1]), float(last[2]))
    return out


class Picker:
    def __init__(self, tiles_root, state_file):
        self.tiles_root = Path(tiles_root) if tiles_root else None
        self.state_file = Path(state_file)
        self.lock = threading.Lock()
        self.state = {'date': '', 'picks': {}}
        self.checked = 0.0
        try:
            s = json.loads(self.state_file.read_text())
            if isinstance(s, dict) and isinstance(s.get('picks'), dict):
                self.state = {'date': str(s.get('date', '')), 'picks': s['picks']}
        except (OSError, ValueError):
            pass

    def refresh(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        day = uk_date(now)
        live = live_buses(self.tiles_root, now) if self.tiles_root else {}
        with self.lock:
            if self.state['date'] != day:
                self.state = {'date': day, 'picks': {}}
            picks = self.state['picks']
            best: dict = {}
            for bid, (lon, lat) in live.items():
                n = nation(lon, lat)
                if n:
                    r = _rank(day, bid)
                    if n not in best or r < best[n][0]:
                        best[n] = (r, bid)
            for n in NATIONS:
                cur = picks.get(n)
                if cur and cur.get('id') in live:
                    cur['seen'] = now
                    continue
                if cur and now - float(cur.get('seen', 0)) < KEEP_MISSING:
                    continue                                   # not in the files just now; give it time
                if n in best:
                    picks[n] = {'id': best[n][1], 'seen': now, 'since': now}
                else:
                    picks.pop(n, None)
            self.checked = now
            try:
                tmp = self.state_file.with_name('.' + self.state_file.name + '.tmp')
                tmp.write_text(json.dumps(self.state, separators=(',', ':')))
                os.replace(tmp, self.state_file)
            except OSError:
                pass
            return self.answer_locked()

    def answer_locked(self) -> dict:
        return {'date': self.state['date'], 'ids': sorted(p['id'] for p in self.state['picks'].values() if p.get('id'))}

    def answer(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if now - self.checked >= EVERY or uk_date(now) != self.state.get('date'):
            return self.refresh(now)
        with self.lock:
            return self.answer_locked()
