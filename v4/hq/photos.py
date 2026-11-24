"""HQ 4.106.0 - real bus photos in the box, found by number plate (Rob 24 Nov 2026 23:12-23:39: "adding real bus pictures
to our windows first, mine as a fallback. But matched by the registration number"; 23:24 "Sticking with free, and where it
isn't possible we use my pics instead"; 23:39 "now please"). See claude/TPA-BUS-PHOTOS-BY-PLATE-2026-11-24.

Measured 24 Nov 23:20: 9 of 100 London buses on the map had at least one photo on Wikimedia Commons, where every file is
free to reuse with a credit. Flickr had far more, but nearly all "all rights reserved" - so Commons only, for now.

How it works:
  - answer(reg): what the page asks when a box with a number plate opens. Kept answers are given at once; otherwise the
    plate joins a queue and the answer is "looking" (the page asks again a few seconds later).
  - One plate at a time, at most one lookup every GAP_S seconds, so a busy evening can never flood Commons.
  - A photo is only taken when the plate is in its file name or in one of its categories (not just somewhere in its
    text), its licence is a free one, and it is a JPEG or PNG. The newest photo is preferred (fewer old paint schemes).
  - The photo is fetched once at box size and kept here, then served from our own address: visitors' browsers never
    contact another site, and the page's security rules need no change.
  - Kept: a found photo 30 days, "none found" 7 days, a failed lookup 1 hour.
  - 4.106.1 (Rob 23:54 "couldnt find one, clicked on a lot"; 144 looked up, ~5 of his clicks found but none seen: the page
    gave up after ~24 s while the queue was long): a plate someone has just clicked goes to the FRONT of the queue; and
    warm() looks up, slowly in the background (one every WARM_GAP_S), the plates of buses on the map that have never been
    looked up, so most answers are ready before anyone clicks.
"""
from __future__ import annotations

import html
import json
import re
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = 'https://commons.wikimedia.org/w/api.php'
USER_AGENT = 'TPA-live-map/4.106 (https://tpalive.co.uk; tpaprojectuk@gmail.com) photo-by-plate'
GAP_S = 1.0
QUEUE_MAX = 300
WARM_MAX = 600                 # 4.106.1: background lookups waiting at most
WARM_GAP_S = 15.0              # 4.106.1: one background lookup every 15 s (~240 an hour), clicked ones go first
FOUND_KEEP_S = 30 * 86400
NONE_KEEP_S = 7 * 86400
ERROR_KEEP_S = 3600
THUMB_W = 640
MAX_BYTES = 800_000
CREDIT_MAX = 80
FREE = re.compile(r'^\s*(cc[ -]?by(?:-sa)?(?:[ -][\d.]+)?(?:\s+\w+)?|cc0(?:\s+[\d.]+)?|public\s+domain|pd(?:-[\w-]+)?)\s*$', re.I)
STD = re.compile(r'^([A-Z]{2}\d{2})([A-Z]{3})$')          # today's UK plates: LK65 ENW


def norm(reg) -> str:
    """'lk65 enw' -> 'LK65ENW'; '' unless it looks like a plate (2-8 letters and digits, with both)."""
    s = re.sub(r'[^A-Z0-9]', '', str(reg or '').upper())
    return s if 2 <= len(s) <= 8 and re.search(r'\d', s) and re.search(r'[A-Z]', s) else ''


def pretty(n: str) -> str:
    m = STD.match(n)
    return f'{m.group(1)} {m.group(2)}' if m else n


def plate_pattern(n: str):
    """The plate as it is written in titles and categories: 'LK65 ENW', 'LK65ENW', 'LK65_ENW', 'LK65-ENW' - on its own,
    not inside a longer string of letters and digits."""
    m = STD.match(n)
    body = re.escape(m.group(1)) + r'[ _-]?' + re.escape(m.group(2)) if m else re.escape(n)
    return re.compile(r'(?<![A-Z0-9])' + body + r'(?![A-Z0-9])', re.I)


def text(v) -> str:
    """Commons' extmetadata values are small bits of HTML: plain text, one line."""
    s = re.sub(r'<[^>]+>', ' ', str(v or ''))
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()


def default_fetch(url: str, limit: int) -> bytes:
    try:
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        ctx = ssl.create_default_context()
    req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with urlopen(req, timeout=15, context=ctx) as res:
        if res.status != 200:
            raise RuntimeError(f'http-{res.status}')
        raw = res.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('too-large')
    return raw


class Finder:
    def __init__(self, folder, api: str = API, fetch=None, gap: float = GAP_S, start: bool = True, warm_gap: float = WARM_GAP_S):
        self.dir = Path(folder)
        self.api = api
        self.fetch = fetch or default_fetch
        self.gap = gap
        self.warm_gap = warm_gap
        self.last_warm = 0.0
        self.lock = threading.Lock()
        self.queue: list[str] = []                # clicked: first, one every `gap` seconds
        self.later: list[str] = []                # 4.106.1: background (warm), one every WARM_GAP_S
        self.busy: set[str] = set()               # being looked up right now
        self.memo: dict[str, dict] = {}
        self.counts = {'looked': 0, 'found': 0, 'none': 0, 'errors': 0}
        self.wake = threading.Event()
        self.thread = None
        self.start = start

    # ---- what the page asks
    def answer(self, reg) -> dict:
        n = norm(reg)
        if not n:
            return {'state': 'none'}
        rec = self.record(n)
        now = time.time()
        if rec:
            keep = FOUND_KEEP_S if rec.get('state') == 'found' else NONE_KEEP_S if rec.get('state') == 'none' else ERROR_KEEP_S
            if now - float(rec.get('at', 0)) <= keep:
                return self.public(n, rec)
        self.enqueue(n)
        if rec and rec.get('state') == 'found':
            return self.public(n, rec)              # an old photo is still shown while it is looked up again
        return {'state': 'looking'}

    def public(self, n, rec) -> dict:
        if rec.get('state') != 'found':
            return {'state': 'none'}
        return {'state': 'found', 'src': '/api/v4/photo/img/' + rec['file'], 'credit': rec.get('credit', ''),
                'licence': rec.get('licence', ''), 'page': rec.get('page', ''), 'plate': pretty(n)}

    def image(self, name: str):
        """(bytes, mime) of a kept photo, or None."""
        if not re.fullmatch(r'[A-Z0-9]{2,8}\.(jpg|png)', name or ''):
            return None
        p = self.dir / name
        if not p.is_file():
            return None
        return p.read_bytes(), 'image/png' if name.endswith('.png') else 'image/jpeg'

    def status(self) -> dict:
        with self.lock:
            return dict(self.counts, waiting=len(self.queue), background=len(self.later))

    def warm(self, regs) -> int:
        """4.106.1: plates of buses on the map: any never looked up (or due again) join the background queue. How many joined."""
        added = 0
        for reg in regs:
            n = norm(reg)
            if not n:
                continue
            rec = self.record(n)
            if rec and time.time() - float(rec.get('at', 0)) <= (FOUND_KEEP_S if rec.get('state') == 'found' else NONE_KEEP_S):
                continue
            with self.lock:
                if n in self.queue or n in self.later or n in self.busy or len(self.later) >= WARM_MAX:
                    continue
                self.later.append(n)
                added += 1
        if added:
            self.kick()
        return added

    # ---- keeping
    def record(self, n):
        with self.lock:
            if n in self.memo:
                return self.memo[n]
        try:
            rec = json.loads((self.dir / f'{n}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        with self.lock:
            self.memo[n] = rec
        return rec

    def save(self, n, rec):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f'{n}.json.tmp'
            tmp.write_text(json.dumps(rec), encoding='utf-8')
            tmp.replace(self.dir / f'{n}.json')
        except OSError:
            pass
        with self.lock:
            self.memo[n] = rec
            if len(self.memo) > 5000:
                self.memo.pop(next(iter(self.memo)))

    def enqueue(self, n):
        """Someone has just opened this bus: to the front of the queue (4.106.1)."""
        with self.lock:
            if n in self.busy:
                return
            if n in self.later:
                self.later.remove(n)
            if n in self.queue:
                self.queue.remove(n)
            elif len(self.queue) >= QUEUE_MAX:
                self.queue.pop()
            self.queue.insert(0, n)
        self.kick()

    def kick(self):
        with self.lock:
            if self.start and self.thread is None:
                self.thread = threading.Thread(target=self.work, daemon=True)
                self.thread.start()
        self.wake.set()

    def next_plate(self, now):
        """The next plate to look up: a clicked one first; a background one only every warm_gap seconds."""
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
            if self.later and now - self.last_warm >= self.warm_gap:
                self.last_warm = now
                return self.later.pop(0)
        return None

    def work(self):
        while True:
            n = self.next_plate(time.time())
            if n is None:
                self.wake.wait(1.0)
                self.wake.clear()
                continue
            self.look(n)
            time.sleep(self.gap)

    # ---- looking on Commons
    def call(self, **params):
        return json.loads(self.fetch(self.api + '?' + urlencode(dict(params, format='json', formatversion='2')), 2_000_000))

    def look(self, n):
        with self.lock:
            self.counts['looked'] += 1
            self.busy.add(n)
        try:
            rec = self.find(n)
        except Exception as exc:                  # never breaks the page: tried again in an hour
            rec = {'state': 'error', 'at': time.time(), 'why': type(exc).__name__}
        finally:
            with self.lock:
                self.busy.discard(n)
        key = 'found' if rec['state'] == 'found' else 'none' if rec['state'] == 'none' else 'errors'
        with self.lock:
            self.counts[key] += 1
        self.save(n, rec)
        return rec

    def find(self, n):
        pat = plate_pattern(n)
        got = self.call(action='query', list='search', srnamespace=6, srlimit=20,
                        srsearch=f'"{pretty(n)}" OR "{n}"')
        titles = [x['title'] for x in (got.get('query') or {}).get('search', []) if x.get('title')]
        if not titles:
            return {'state': 'none', 'at': time.time()}
        info = self.call(action='query', prop='imageinfo|categories', titles='|'.join(titles[:20]),
                         iiprop='url|extmetadata|timestamp|mime', iiurlwidth=THUMB_W, cllimit='max')
        best = []
        for page in (info.get('query') or {}).get('pages', []):
            title = page.get('title', '')
            cats = [c.get('title', '') for c in page.get('categories', [])]
            if not (pat.search(title) or any(pat.search(c) for c in cats)):
                continue                              # the plate is only somewhere in the text: maybe another bus
            ii = (page.get('imageinfo') or [{}])[0]
            meta = ii.get('extmetadata') or {}
            lic = text((meta.get('LicenseShortName') or {}).get('value'))
            if not FREE.match(lic) or text((meta.get('NonFree') or {}).get('value')).lower() == 'true':
                continue
            if ii.get('mime') not in ('image/jpeg', 'image/png') or not ii.get('thumburl'):
                continue
            when = text((meta.get('DateTimeOriginal') or {}).get('value')) or ii.get('timestamp', '')
            year = re.search(r'(19|20)\d\d', when)
            best.append((year.group(0) if year else '0000', when, title, ii, meta, lic))
        if not best:
            return {'state': 'none', 'at': time.time()}
        best.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, _, title, ii, meta, lic = best[0]
        raw = self.fetch(ii['thumburl'], MAX_BYTES)
        ext = 'png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'jpg' if raw[:3] == b'\xff\xd8\xff' else ''
        if not ext:
            return {'state': 'none', 'at': time.time()}
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f'{n}.{ext}.tmp'
        tmp.write_bytes(raw)
        tmp.replace(self.dir / f'{n}.{ext}')
        artist = text((meta.get('Artist') or {}).get('value')) or 'Unknown photographer'
        return {'state': 'found', 'at': time.time(), 'file': f'{n}.{ext}', 'title': title,
                'credit': artist[:CREDIT_MAX], 'licence': lic, 'page': ii.get('descriptionurl', '')}


PLATE_ID = re.compile(r'^[A-Z]{2}\d{2}[A-Z]{3}$')


def plates_from_tiles(root) -> list:
    """4.106.1: number plates of the buses in the bus engine's published town-level files (<root>/bus/b/*.json): the last
    part of a bus's id when it is a modern plate (London buses, National Express, Bee Network send their plate)."""
    out = []
    for f in sorted(Path(root, 'bus', 'b').glob('*.json')):
        try:
            items = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for it in items if isinstance(items, list) else []:
            tail = str(it.get('i', '') if isinstance(it, dict) else '').split(':')[-1].upper()
            if PLATE_ID.match(tail):
                out.append(tail)
    return out
