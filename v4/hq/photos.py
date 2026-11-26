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
  - 4.107.0 (Rob 26 Nov 16:36 "yes": photos for everyone, with the wrong-bus fix first): personalised plates move from bus
    to bus (A1EYD's Commons photo is an older East Yorkshire vehicle, no. 61; today A1EYD is double decker 20096). So only
    plates that stay with one bus get a photo (trusted()): today's plates (LK65 ENW) and the long serial plates new buses
    are given (LTZ 1495: three letters, four digits). Older-style and short plates (A1 EYD, A123 BCD, ABC 123D, EYM 1)
    are never looked up and never shown - TPA's own picture instead - including any photo already kept for one.
"""
from __future__ import annotations

import hashlib
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


SERIAL = re.compile(r'^[A-Z]{3}\d{4}$')              # 4.107.0: LTZ1495 - issued in a series to new buses


def trusted(n: str) -> bool:
    """4.107.0: is a photo found by this plate safely THIS bus? Today's plates and long serial plates only (see above)."""
    return bool(STD.match(n) or SERIAL.match(n))


RULES = 2       # 4.106.2: older "none" answers (made before older plates were matched) are looked up again


def groups(n: str) -> list:
    """'LTZ1495' -> ['LTZ', '1495']; 'A123BCD' -> ['A', '123', 'BCD']: the runs of letters and of digits."""
    return re.findall(r'[A-Z]+|\d+', n)


def pretty(n: str) -> str:
    """How the plate is written: 'LK65 ENW' (today's plates); 4.106.2 (Rob 00:47 "still none": 11 of 18 central London buses
    clicked were New Routemasters, 'LTZ 1495') older ones with a space where letters and digits meet ('LTZ 1495', 'A123 BCD')."""
    m = STD.match(n)
    if m:
        return f'{m.group(1)} {m.group(2)}'
    g = groups(n)
    if len(g) == 3 and g[0].isalpha():
        return f'{g[0]}{g[1]} {g[2]}'                 # prefix plates: A123 BCD
    return ' '.join(g)


def plate_pattern(n: str):
    """The plate as it is written in titles and categories - 'LK65 ENW', 'LK65ENW', 'LK65_ENW', 'LTZ 1495', 'LTZ1495' - with
    or without a space (or _ or -) where letters and digits meet, on its own, not inside a longer string."""
    m = STD.match(n)
    parts = [m.group(1), m.group(2)] if m else groups(n)
    body = r'[ _-]?'.join(re.escape(x) for x in parts)
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
        if not n or not trusted(n):                     # 4.107.0: a plate that may have been on another bus: no photo
            return {'state': 'none'}
        rec = self.record(n)
        now = time.time()
        if rec:
            keep = FOUND_KEEP_S if rec.get('state') == 'found' else NONE_KEEP_S if rec.get('state') == 'none' else ERROR_KEEP_S
            if rec.get('state') == 'none' and rec.get('rules', 1) < RULES:
                keep = -1                                # 4.106.2: made with the older matching: look again
            if now - float(rec.get('at', 0)) <= keep:
                return self.public(n, rec)
        self.enqueue(n)
        if rec and rec.get('state') == 'found':
            return self.public(n, rec)              # an old photo is still shown while it is looked up again
        return {'state': 'looking'}

    def public(self, n, rec) -> dict:
        if rec.get('state') != 'found' or not trusted(n):
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
            if not n or not trusted(n):                 # 4.107.0
                continue
            rec = self.record(n)
            if rec and time.time() - float(rec.get('at', 0)) <= (FOUND_KEEP_S if rec.get('state') == 'found' else NONE_KEEP_S) \
                    and not (rec.get('state') == 'none' and rec.get('rules', 1) < RULES):
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
        rec['rules'] = RULES
        key = 'found' if rec['state'] == 'found' else 'none' if rec['state'] == 'none' else 'errors'
        with self.lock:
            self.counts[key] += 1
        self.save(n, rec)
        return rec

    def find(self, n):
        pat = plate_pattern(n)
        got = self.call(action='query', list='search', srnamespace=6, srlimit=20,
                        srsearch=f'"{pretty(n)}" OR "{n}"' + (f' OR "{" ".join(groups(n))}"' if " ".join(groups(n)) not in (pretty(n), n) else ''))
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


PLATE_ID = re.compile(r'^(?!TMP)(?:[A-Z]{2}\d{2}[A-Z]{3}|[A-Z]{1,3}\d{1,4}|[A-Z]\d{1,3}[A-Z]{3}|[A-Z]{3}\d{1,3}[A-Z])$')   # today's, and (4.106.2) LTZ1495, A123BCD, ABC123D


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


# ---------------------------------------------------------------------------------------------------------------------
# HQ 4.108.0 - real PLANE photos by registration (Rob 26 Nov 2026 17:26 "how about plane photos?"; 17:28 "yeah this sounds
# excellent!"). Measured 17:27-17:32: 113 of 142 planes on the map had photos on Commons, filed in a category per plane,
# "Category:G-EZTZ (aircraft)". See claude/TPA-PLANE-PHOTOS-MEASURE-2026-11-26.
#   - Only files in that plane's own category are considered (never a text search).
#   - Wrong-plane guard: the photo's title or categories must name the type we already know for the plane (the box's
#     "Aircraft" line, e.g. "Airbus A320-214" -> A320; "BAE Systems Hawk T2" -> T2). One file filed under Hawk T2 ZK020 was
#     titled "Hawk T1". When no type is known, no photo.
#   - Close-ups (tail fin, cockpit, cabin, engine, wing...) are skipped; the newest whole-plane photo wins.
#   - Kept like bus photos, in their own folder, served from our own address with the photographer's credit.
# ---------------------------------------------------------------------------------------------------------------------
REG = re.compile(r'^[A-Z0-9]{1,2}-[A-Z0-9]{2,5}$|^[A-Z0-9]{3,7}$')
CLOSE_UP = re.compile(r'tail ?fin|\btail\b|\bfin\b|cockpit|flight ?deck|cabin|interior|\bseats?\b|engine|nacelle|\bwings?\b|winglet|'
                      r'\bnose\b|undercarriage|landing gear|\bgear\b|wheel|detail|close.?up|\blogo\b|\bdoor\b|galley|instrument|'
                      r'propeller|rotor head|\bmodel\b|wreck|crash|accident|\bdiorama\b', re.I)
GFDL = re.compile(r'^\s*gfdl(?:[ -][\d.]+)?\s*$', re.I)   # Commons' older free licence (credit and licence named)


def free_plane(lic: str) -> bool:
    return bool(FREE.match(lic) or GFDL.match(lic))
ICAO_WORDS = {'A19N': 'A319', 'A20N': 'A320', 'A21N': 'A321', 'B37M': '737', 'B38M': '737', 'B39M': '737', 'B3XM': '737',
              'B77W': '777', 'B77L': '777', 'B78X': '787', 'A35K': 'A350', 'A359': 'A350', 'A388': 'A380', 'A339': 'A330',
              'A338': 'A330', 'BCS1': 'A220', 'BCS3': 'A220', 'E75L': '175', 'E75S': '175', 'E35L': 'Legacy'}


# 4.108.1 (Rob 18:11 "How can we increase the number"): the same type written another way on Commons. Measured: 3 of
# 116 planes had photos turned down only for this ("Embraer 190" for the box's "Embraer E190").
TYPE_ALIASES = {'h135': ['ec135'], 'h145': ['ec145', 'bk117'], 'h125': ['as350'], 'h130': ['ec130'], 'h120': ['ec120'],
                'h155': ['ec155'], 'h175': ['ec175'], 'c99': ['145'], 'erj': ['145'], 'aw169': ['az169'], 'aw189': ['az189']}


def reg_norm(reg) -> str:
    """'g-eztz' -> 'G-EZTZ'; '' unless it looks like a registration or a military serial (with a letter)."""
    raw = str(reg or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9-]{3,9}', raw):
        return ''
    ok = REG.match(raw) and re.search(r'[A-Z]', raw) and ('-' in raw or re.search(r'\d', raw))   # G-EZTZ, N787UA, ZK020
    return raw if ok else ''


def reg_key(n: str) -> str:
    return n.replace('-', '_')


def type_keys(name, code='') -> list:
    """4.108.0: the model words, any of which a photo may name: the first model number (type_key) and a mark like T2 or
    FGR4 ("British Aerospace Hawk 128 T.2" -> ['128', 't2'], so a "Hawk T2" photo counts and a "Hawk T1" one does not)."""
    first = type_key(name, code)
    marks = [m.lower() for m in re.findall(r'(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,2}(?![A-Za-z0-9])', re.sub(r'[.]', '', str(name or ''))[:80])]
    keys = [k for k in [first] + marks if k]
    for k in list(keys):                          # 4.108.1: other spellings of the same type (measured 18:15)
        keys += TYPE_ALIASES.get(k, [])
        if re.fullmatch(r'e\d{3}', k):            # Embraer E190 = Embraer 190 = ERJ-190
            keys.append(k[1:])
    return list(dict.fromkeys(keys))


def type_key(name, code='') -> str:
    """The word a photo of this plane must mention: the first model number in the type name ("Airbus A320-214" -> a320,
    "Boeing 787-9 Dreamliner" -> 787, "Embraer ERJ-190-100SR" -> 190, "BAE Systems Hawk T2" -> t2, "Cessna 172S" -> 172);
    without a number, the last long word ("Eurofighter Typhoon" -> typhoon); without a name, the ICAO type code
    (A20N -> a320, B738 -> 737). '' when nothing usable."""
    name = re.sub(r'[.]', '', str(name or ''))[:80]
    for tok in re.split(r'[^A-Za-z0-9]+', name):
        m = re.match(r'[A-Za-z]{0,3}\d+', tok)
        if m and re.search(r'\d', tok):
            return m.group(0).lower()
    words = [w for w in re.findall(r'[A-Za-z]{4,}', name) if w.lower() not in ('aircraft', 'systems', 'aerospace', 'helicopter')]
    if words:
        return words[-1].lower()
    code = re.sub(r'[^A-Z0-9]', '', str(code or '').upper())[:4]
    if code in ICAO_WORDS:
        return ICAO_WORDS[code].lower()
    m = re.fullmatch(r'B7(\d)\d', code)
    if m:
        return f'7{m.group(1)}7'
    m = re.fullmatch(r'(A3\d)\d', code)
    if m:
        return code.lower()
    return ''


def names_type(text: str, key: str) -> bool:
    """Does this text (a photo's title and categories) name the model `key`? 'a320' in "A320-214", "A320neo"; '787' in
    "Boeing 787-9", "B787" but not inside a longer number ("48787"); 't2' in "Hawk T.2"."""
    if not key:
        return False
    hay = re.sub(r'[.]', '', text.lower())
    return re.search(r'(?<![0-9])' + re.escape(key) + (r'(?![0-9])' if key[-1].isdigit() else r'(?![a-z])'), hay) is not None


# 4.108.2 (Rob 26 Nov 18:21 "yes a"): a plane with no photo of its own may show a photo of ANOTHER plane of the same
# operator and type, always labelled on the picture "Not this plane - one like it". Measured 18:15: 7 of the 20 planes with
# nothing of their own were brand-new airliners (Ryanair 737 MAX, easyJet A320neo, Emirates A350, Air France A220).
# Rules: the photo's title or categories must name the operator AND the type; a neo or MAX only by a neo or MAX photo and
# the older ones never by one; whole plane, free licence, newest. One answer per operator + type, shared by every plane.
SUFFIX = {'uk', 'europe', 'shuttle', 'international', 'ltd', 'limited', 'ireland', 'plc', 'regional'}


def operator_names(op) -> list:
    """'British Airways Shuttle' -> ['British Airways Shuttle', 'British Airways'] (same colours); '' -> []."""
    op = re.sub(r'\s+', ' ', re.sub(r'[^A-Za-z0-9 .&-]', ' ', str(op or ''))).strip()[:40]
    if len(op) < 3 or op.lower() in ('private', 'unknown'):
        return []
    out, words = [op], op.split(' ')
    while len(words) > 1 and words[-1].lower().rstrip('.') in SUFFIX:
        words = words[:-1]
        out.append(' '.join(words))
    return list(dict.fromkeys(out))


def generation(name, code='') -> str:
    """'max' / 'neo' / '' - the newer models look different, so a photo must be the same generation."""
    t = f'{name} {code}'.lower()
    if 'max' in t or re.search(r'\bb3[789x]m\b', t):
        return 'max'
    if 'neo' in t or re.search(r'\ba(19|20|21|33|39)n\b', t) or re.search(r'-2\d\dn\b', t):
        return 'neo'
    return ''


MAKERS = {'airbus', 'boeing', 'embraer', 'agustawestland', 'leonardo', 'british', 'aerospace', 'systems', 'avions',
          'transport', 'regional', 'bombardier', 'cessna', 'piper', 'beechcraft', 'dassault', 'gulfstream', 'pilatus',
          'eurocopter', 'sikorsky', 'westland', 'lockheed', 'martin', 'mcdonnell', 'douglas', 'aerospatiale', 'helicopters',
          'aircraft', 'diamond', 'tecnam', 'cirrus', 'textron', 'raytheon', 'hawker', 'fokker', 'saab', 'antonov', 'ilyushin',
          'tupolev', 'mitsubishi', 'honda', 'daher', 'socata', 'robinson', 'schweizer', 'agusta', 'bell'}


def model_words(name) -> list:
    """4.108.2: the type's own name words, e.g. "AgustaWestland Wildcat AH.1" -> ['wildcat'] (makers and marks left out).
    Measured 18:40: for an Army Wildcat the "one like it" search found an Apache AH1 - "AH1" alone is not enough."""
    return [w.lower() for w in re.findall(r'(?<![A-Za-z0-9])[A-Za-z]{4,}(?![A-Za-z0-9])', str(name or ''))
            if w.lower() not in MAKERS and w.lower() not in ('max', 'neo', 'dreamliner', 'jumbo')]


def names_operator(text: str, op: str) -> bool:
    return re.search(r'(?<![a-z0-9])' + re.escape(op.lower()) + r'(?![a-z0-9])', text.lower()) is not None


PLANE_WARM_GAP_S = 5.0          # 4.108.1: one background plane lookup every 5 s (~720 an hour, two Commons asks each)
PLANE_WARM_MAX = 800


class PlaneFinder(Finder):
    def __init__(self, folder, namer=None, **kw):
        kw.setdefault('warm_gap', PLANE_WARM_GAP_S)
        super().__init__(folder, **kw)
        self.types: dict[str, str] = {}           # registration -> the type word asked with it
        self.ids: dict[str, tuple] = {}           # 4.108.1: registration -> (plane id, ICAO type code) for warm-up
        self.namer = namer                        # 4.108.1: plane id -> the box's "Aircraft" line (asks the planes engine)
        self.alike_for: dict[str, tuple] = {}     # 4.108.2: registration -> (operator, type name, code) for "one like it"
        self.alike: dict[str, dict] = {}          # 4.108.2: operator + type -> the shared "one like it" answer

    def answer(self, reg, typ='', code='', op='') -> dict:
        n, key = reg_norm(reg), ' '.join(type_keys(typ, code))
        if not n or not key:
            return {'state': 'none'}
        with self.lock:
            self.alike_for[n] = (str(op or '')[:40], str(typ or '')[:80], str(code or '')[:4])
            if len(self.alike_for) > 5000:
                self.alike_for.pop(next(iter(self.alike_for)))
        rec = self.record(reg_key(n))
        now = time.time()
        if rec and rec.get('type') == key:
            keep = FOUND_KEEP_S if rec.get('state') == 'found' and not rec.get('like') else NONE_KEEP_S \
                if rec.get('state') in ('none', 'found') else ERROR_KEEP_S   # 4.108.2: "one like it" asked again after a week
            if now - float(rec.get('at', 0)) <= keep:
                return self.public(n, rec)
        with self.lock:
            self.types[n] = key
            if len(self.types) > 5000:
                self.types.pop(next(iter(self.types)))
        self.enqueue(n)
        if rec and rec.get('state') == 'found' and rec.get('type') == key:
            return self.public(n, rec)
        return {'state': 'looking'}

    def public(self, n, rec) -> dict:
        if rec.get('state') != 'found':
            return {'state': 'none'}
        out = {'state': 'found', 'src': '/api/v4/planephoto/img/' + rec['file'], 'credit': rec.get('credit', ''),
               'licence': rec.get('licence', ''), 'page': rec.get('page', ''), 'plate': n}
        if rec.get('like'):
            out['like'] = str(rec['like'])[:90]   # 4.108.2: not this plane - the page labels it
        return out

    def image(self, name: str):
        if not re.fullmatch(r'[A-Z0-9_]{3,15}\.(jpg|png)', name or ''):
            return None
        p = self.dir / name
        if not p.is_file():
            return None
        return p.read_bytes(), 'image/png' if name.endswith('.png') else 'image/jpeg'

    def warm(self, planes) -> int:
        """4.108.1 (Rob 18:11 "pre look up is a good idea"): planes on the map, as (registration, id, type code): any not
        looked up yet (or due again) join the background queue, newest first in, one every PLANE_WARM_GAP_S."""
        added = 0
        now = time.time()
        try:                                      # never fill the disk: no look-ahead with under 3 GB free (clicks still work)
            import shutil
            self.dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.dir).free < 3 * 1024 ** 3:
                return 0
        except OSError:
            return 0
        for reg, pid, code in planes:
            n = reg_norm(reg)
            if not n or not pid:
                continue
            rec = self.record(reg_key(n))
            if rec and now - float(rec.get('at', 0)) <= (FOUND_KEEP_S if rec.get('state') == 'found' and not rec.get('like')
                                                          else NONE_KEEP_S if rec.get('state') in ('none', 'found') else ERROR_KEEP_S):
                continue
            with self.lock:
                self.ids[n] = (str(pid)[:60], str(code or '')[:4])
                if len(self.ids) > 5000:
                    self.ids.pop(next(iter(self.ids)))
                if n in self.queue or n in self.later or n in self.busy or len(self.later) >= PLANE_WARM_MAX:
                    continue
                self.later.append(n)
                added += 1
        if added:
            self.kick()
        return added

    def record(self, n):
        return super().record(reg_key(n) if '-' in n else n)

    def save(self, n, rec):
        return super().save(reg_key(n), rec)

    def look(self, n):
        rec = super().look(n)
        return rec

    def find(self, n):
        key = self.types.get(n, '')
        op, typ, code = self.alike_for.get(n, ('', '', ''))
        if not key and n in self.ids and self.namer:       # 4.108.1: a warm-up plane: its type from the planes engine
            pid, code = self.ids[n]
            got = self.namer(pid)
            typ, op = (got if isinstance(got, tuple) else (got, ''))
            key = ' '.join(type_keys(typ, code))
        out = self._find(n, key)
        if out.get('state') == 'none' and key and op:      # 4.108.2: no photo of its own: one like it, if there is one
            out = dict(self.like_it(op, typ, code, key), at=time.time())
        out['type'] = key
        return out

    def like_it(self, op, typ, code, key) -> dict:
        """4.108.2: a photo of another plane of the same operator and type (shared by all of them, kept like the rest)."""
        gen = generation(typ, code)
        akey = re.sub(r'[^A-Z0-9]', '', f'{op}|{key}|{gen}'.upper())[:40]
        name = 'L_' + hashlib.sha1(akey.encode()).hexdigest()[:12].upper()
        rec = self.alike.get(name) or self.record(name)
        if rec and time.time() - float(rec.get('at', 0)) <= (FOUND_KEEP_S if rec.get('state') == 'found' else NONE_KEEP_S):
            return {k: v for k, v in rec.items() if k != 'at'}
        rec = {'state': 'none'}
        for who in operator_names(op):
            first = key.split()[0]
            got = self.call(action='query', generator='search', gsrsearch=f'"{who}" {first}' + (f' {gen}' if gen else ''),
                            gsrnamespace=6, gsrlimit=40, prop='imageinfo|categories', iiprop='url|extmetadata|timestamp|mime',
                            iiurlwidth=THUMB_W, cllimit='max', clshow='!hidden')
            pages = []
            for p in (got.get('query') or {}).get('pages', []):
                said = ' '.join([p.get('title', '')] + [c.get('title', '') for c in p.get('categories', [])])
                if not names_operator(said, who):
                    continue
                said_gen = 'max' if re.search(r'\bmax\b', said, re.I) else 'neo' if re.search(r'neo\b', said, re.I) else ''
                if said_gen != gen:
                    continue                      # a 737-800 never stands in for a MAX, nor a MAX for a 737-800
                words = model_words(typ)
                if words and not any(re.search(r'(?<![a-z])' + w, said.lower()) for w in words):
                    continue                      # a Wildcat by a Wildcat, never an Apache
                pages.append(p)
            best = self._pick(pages, key)
            if best:
                kept = self._keep(name, best)
                if kept.get('state') == 'found':
                    shown = re.sub(r'\s+', ' ', f'{op} {typ}'.strip())[:80]
                    rec = dict(kept, like=f'Not this plane: one like it ({shown})')
                    break
        rec['at'] = time.time()
        super().save(name, rec)
        self.alike[name] = rec
        if len(self.alike) > 2000:
            self.alike.pop(next(iter(self.alike)))
        return {k: v for k, v in rec.items() if k != 'at'}

    def _find(self, n, key):
        if not key:
            return {'state': 'none', 'at': time.time()}
        info = self.call(action='query', generator='categorymembers', gcmtitle=f'Category:{n} (aircraft)', gcmtype='file',
                         gcmlimit='50', prop='imageinfo|categories', iiprop='url|extmetadata|timestamp|mime',
                         iiurlwidth=THUMB_W, cllimit='max', clshow='!hidden')
        best = self._pick((info.get('query') or {}).get('pages', []), key)
        if not best:
            # 4.108.1: no folder of its own (or nothing usable in it): files whose NAME has the registration, same rules
            pat = re.compile(r'(?<![A-Z0-9])' + r'[ _-]?'.join(re.escape(x) for x in n.split('-')) + r'(?![A-Z0-9])', re.I)
            got = self.call(action='query', generator='search', gsrsearch=f'"{n}"', gsrnamespace=6, gsrlimit=20,
                            prop='imageinfo|categories', iiprop='url|extmetadata|timestamp|mime', iiurlwidth=THUMB_W,
                            cllimit='max', clshow='!hidden')
            best = self._pick([p for p in (got.get('query') or {}).get('pages', []) if pat.search(p.get('title', ''))], key)
        return self._keep(n, best)

    def _pick(self, pages, key):
        best = []
        for page in pages:
            title = page.get('title', '')
            cats = [c.get('title', '') for c in page.get('categories', [])]
            if CLOSE_UP.search(re.sub(r'^File:|\.\w+$', '', title)):
                continue
            ii = (page.get('imageinfo') or [{}])[0]
            meta = ii.get('extmetadata') or {}
            said = ' '.join([title] + cats + [text((meta.get('ImageDescription') or {}).get('value'))[:300]])
            if not any(names_type(said, k) for k in key.split()):
                continue                              # wrong-plane guard
            lic = text((meta.get('LicenseShortName') or {}).get('value'))
            if not free_plane(lic) or text((meta.get('NonFree') or {}).get('value')).lower() == 'true':
                continue
            if ii.get('mime') not in ('image/jpeg', 'image/png') or not ii.get('thumburl'):
                continue
            when = text((meta.get('DateTimeOriginal') or {}).get('value'))
            year = re.search(r'(?:19|20)\d\d', when) or re.search(r'(?:19|20)\d\d', title) or re.search(r'(?:19|20)\d\d', ii.get('timestamp', ''))
            best.append((year.group(0) if year else '0000', ii.get('timestamp', ''), title, ii, meta, lic))
        return best

    def _keep(self, n, best):
        if not best:
            return {'state': 'none', 'at': time.time()}
        best.sort(key=lambda x: (x[0], x[1]), reverse=True)
        _, _, title, ii, meta, lic = best[0]
        raw = self.fetch(ii['thumburl'], MAX_BYTES)
        ext = 'png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'jpg' if raw[:3] == b'\xff\xd8\xff' else ''
        if not ext:
            return {'state': 'none', 'at': time.time()}
        k = reg_key(n)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f'{k}.{ext}.tmp'
        tmp.write_bytes(raw)
        tmp.replace(self.dir / f'{k}.{ext}')
        artist = text((meta.get('Artist') or {}).get('value')) or 'Unknown photographer'
        return {'state': 'found', 'at': time.time(), 'file': f'{k}.{ext}', 'title': title,
                'credit': artist[:CREDIT_MAX], 'licence': lic, 'page': ii.get('descriptionurl', '')}
