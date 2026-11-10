"""Published bus fleet lists: what kind of bus each one is (bus engine 0.11.0).

Owner 10 Nov 13:33-13:36: "Proper identification for bus types"; "I would love a user to click on a bus and it give
them the exact data (where we can) of the bus type etc". The live feed never says (tpa-bus-kinds), but some fleet
lists are published, keyed by what the live feed does send:

  tfl  Transport for London's annual fleet list (.xlsx): number plate -> deck, chassis, body, power, fleet number, year.
       London buses (TFLO) send their number plate. 93% matched (owner's check 13:37). Terms: TfL Transport Data
       Service terms allow reuse and combining (owner 12:33); credit "Powered by TfL Open Data".
  ey   East Yorkshire's fleet page: fleet number -> type, model, electric, number plate. EYMS sends fleet numbers.
       The page says (c) East Yorkshire Motor Services: OFF until they give permission (TPA_BUS_FLEET_LISTS=tfl,ey).

Each list is downloaded about once a week, kept on disk (a failed download keeps the old copy), and looked up in
memory, so clicking a bus costs nothing. Only an exact match counts; anything else stays "not known".
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path
from xml.etree.ElementTree import iterparse

TFL_URL = 'https://content.tfl.gov.uk/fleet-list-annual-audit-report-31-march-2026.xlsx'   # renamed each year
EY_URL = 'https://www.eastyorkshirebuses.co.uk/fleet-list'
# 0.12.5 (owner 19:47-19:50): Nottingham City Transport's page uses the same website kit as East Yorkshire's (owner's
# peek: 552 rows with data-more-info, "vehicle-information__plate", "Single decker bus"), so the same reader reads it.
# Test site only until permission (launch list: "Must do before launch").
NCT_URL = 'https://www.nctx.co.uk/fleet-list'
SOURCES = {'tfl': ('TFLO', 'TfL'), 'ey': ('EYMS', 'East Yorkshire'),
           'nct': ('NCTR', 'Nottingham City Transport')}   # list -> (company code, whose list)
MAX_BYTES = 50_000_000
REFRESH_DAYS = 7
LOCK = threading.Lock()
LISTS: dict = {}            # 'tfl' -> {'fetchedAt': t, 'rows': {id: {...}}}


def norm(text: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', (text or '').upper())


def plate(text: str) -> str:
    """'LJ09KRO' -> 'LJ09 KRO' (today's style of plate); others as written."""
    n = norm(text)
    return f'{n[:4]} {n[4:]}' if re.fullmatch(r'[A-Z]{2}\d{2}[A-Z]{3}', n) else (text or '').strip().upper()[:10]


def _local(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def xlsx_rows(raw: bytes) -> list:
    """Rows (lists of text) of the first worksheet of an .xlsx, standard library only."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        shared = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            for _e, el in iterparse(zf.open('xl/sharedStrings.xml')):
                if _local(el.tag) == 'si':
                    shared.append(''.join(t.text or '' for t in el.iter() if _local(t.tag) == 't'))
                    el.clear()
        sheet = sorted(n for n in zf.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml$', n))[0]
        rows = []
        for _e, el in iterparse(zf.open(sheet)):
            if _local(el.tag) != 'row':
                continue
            cells = {}
            for c in el:
                if _local(c.tag) != 'c':
                    continue
                col = 0
                for ch in re.sub(r'\d', '', c.get('r', '')):
                    col = col * 26 + ord(ch) - 64
                v = next((x.text or '' for x in c.iter() if _local(x.tag) in ('v', 't')), '')
                if c.get('t') == 's' and v.isdigit() and int(v) < len(shared):
                    v = shared[int(v)]
                cells[col - 1] = v.strip()
            if cells:
                rows.append([cells.get(i, '') for i in range(max(cells) + 1)])
            el.clear()
    return rows


def model_words(body: str, chassis: str) -> str:
    """0.11.2 (owner 14:49, T321: "ADL Enviro 400 body on a ADL Enviro 400"): one name when the bus is built in one
    piece; "an" before a vowel."""
    body, chassis = body.strip(), chassis.strip()
    if not body or not chassis or body.lower() == chassis.lower():
        return body or chassis
    # 0.11.3 (owner 15:01, 36652: "ADL Enviro 200 MMC body on an ADL Enviro 200"): one name inside the other = one bus
    if body.lower().startswith(chassis.lower()) or chassis.lower().startswith(body.lower()):
        return max(body, chassis, key=len)
    return f"{body} body on {'an' if chassis[:1].upper() in 'AEIOU' else 'a'} {chassis}"


def tidy_model(model: str) -> str:
    """The same for a model already kept on disk by 0.11.0/0.11.1."""
    m = re.fullmatch(r'(.+) body on an? (.+)', model or '')
    return model_words(m.group(1), m.group(2)) if m else (model or '')


def parse_tfl(raw: bytes) -> dict:
    """number plate -> {picture, kind, model, power, fleet, year}."""
    rows = xlsx_rows(raw)
    head_at = next((i for i, r in enumerate(rows) if any(x.strip().lower() == 'registration number' for x in r)), None)
    if head_at is None:
        raise ValueError('no "Registration Number" column')
    head = [x.strip().lower() for x in rows[head_at]]

    def col(*names):
        return next((i for i, x in enumerate(head) if any(x.startswith(n) for n in names)), None)
    c = {'reg': col('registration number'), 'fleet': col('fleet number'), 'year': col('calendar year'),
         'deck': col('double or single'), 'power': col('bus type'), 'chassis': col('chassis'), 'body': col('body')}
    get = lambda r, k: (r[c[k]].strip() if c[k] is not None and c[k] < len(r) else '')
    out = {}
    for r in rows[head_at + 1:]:
        reg, deck = norm(get(r, 'reg')), get(r, 'deck').lower()
        if not reg or deck not in ('double', 'single'):
            continue
        body, chassis = get(r, 'body')[:40], get(r, 'chassis')[:40]
        out[reg] = {'picture': 'dd' if deck == 'double' else 'sd',
                    'kind': 'Double decker' if deck == 'double' else 'Single decker',
                    'model': model_words(body, chassis),
                    'power': get(r, 'power')[:20], 'fleet': get(r, 'fleet')[:12], 'plate': plate(get(r, 'reg')),
                    'year': get(r, 'year')[:4] if re.fullmatch(r'\d{4}', get(r, 'year')[:4]) else ''}
    return out


EY_KINDS = {'double decker bus': ('dd', 'Double decker'), 'single decker bus': ('sd', 'Single decker'),
            'midibus': ('sd', 'Midi single decker'), 'minibus': ('mini', 'Minibus'), 'coach': ('coach', 'Coach'),
            'open top bus': ('dd', 'Open-top double decker')}


def _text(fragment: str) -> str:
    import html as _html
    return ' '.join(_html.unescape(re.sub(r'<[^>]+>', ' ', fragment or '')).split())


def _kind_of(words: str):
    """'Double decker bus', 'Single Decker', 'Midibus', 'Open top bus' ... -> (picture, kind) or None."""
    w = words.lower()
    if 'open top' in w or 'open-top' in w:
        return 'dd', 'Open-top double decker'
    for key, value in (('double', ('dd', 'Double decker')), ('midi', ('sd', 'Midi single decker')),
                       ('mini', ('mini', 'Minibus')), ('coach', ('coach', 'Coach')), ('single', ('sd', 'Single decker'))):
        if key in w:
            return value
    return None


def parse_ey(raw: bytes) -> dict:
    """fleet number -> {picture, kind, model, power, plate}, from a fleet page in East Yorkshire's website kit (also
    Nottingham City Transport's). 0.12.6 (owner's status 20:12: only 217 of ~287 East Yorkshire and 85 of ~276
    Nottingham buses read): read each row cell by cell, so extra labels, entities or tags in a cell don't lose the bus.
    How many buses were skipped is kept in parse_ey.skipped."""
    text = raw.decode('utf-8', 'replace')
    out, skipped = {}, 0
    for chunk in re.findall(r'<tr\b.*?</tr>', text, re.S):
        button = re.search(r'fleet-list__more-info-button[^>]*data-more-info="([^"]{1,12})"|'
                           r'data-more-info="([^"]{1,12})"[^>]*fleet-list__more-info-button', chunk)
        if not button:
            continue
        fleet = (button.group(1) or button.group(2)).strip()
        found = None
        for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', chunk, re.S):
            em = re.search(r'<em\b[^>]*>(.*?)</em>', cell, re.S)
            if em and _kind_of(_text(em.group(1))):
                found = (_text(cell[:em.start()]), _kind_of(_text(em.group(1))))
                break
        if not found:              # 0.12.7 (owner's peek 20:38): bus 402's cell is just "Double decker bus" - no model
            for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', chunk, re.S):
                words = _text(cell)
                if re.fullmatch(r'[A-Za-z -]{4,30}', words) and _kind_of(words):
                    found = ('', _kind_of(words))
                    break
        if not found or not re.fullmatch(r'[A-Za-z0-9]{1,12}', fleet):
            skipped += 1
            continue
        model, (picture, kind) = found
        plate = re.search(r'vehicle-information__plate"[^>]*>(.*?)<', chunk, re.S)
        out[fleet] = {'picture': picture, 'kind': kind, 'model': model[:40],
                      'power': 'Electric' if 'electric bus' in chunk.lower() else '',
                      'plate': _text(plate.group(1))[:10] if plate else ''}
    parse_ey.skipped = skipped
    if len(out) < 10:
        raise ValueError(f'only {len(out)} buses read - page layout changed?')
    return out


parse_ey.skipped = 0
PARSER_VERSION = 3       # 0.12.6: a kept list read by an older reader is read again at once; 3: 0.12.7 (no-model rows)


PARSERS = {'tfl': (lambda: TFL_URL, parse_tfl), 'ey': (lambda: EY_URL, parse_ey), 'nct': (lambda: NCT_URL, parse_ey)}


def load(data_dir: Path) -> None:
    try:
        saved = json.loads((Path(data_dir) / 'fleet-lists.json').read_text())
    except (OSError, ValueError):
        return
    with LOCK:
        LISTS.clear()
        LISTS.update({k: v for k, v in saved.items() if k in SOURCES and isinstance(v.get('rows'), dict)})


def save(data_dir: Path) -> None:
    path = Path(data_dir) / 'fleet-lists.json'
    with LOCK:
        text = json.dumps(LISTS)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(text)
    tmp.replace(path)


def refresh(data_dir: Path, enabled, download, status=lambda **kw: None) -> None:
    """Download each enabled list older than REFRESH_DAYS. A failure keeps the copy we have."""
    for name in enabled:
        with LOCK:
            have = LISTS.get(name)
        if have and have.get('parser') == PARSER_VERSION and time.time() - have.get('fetchedAt', 0) < REFRESH_DAYS * 86400:
            continue
        url, parse = PARSERS[name]
        try:
            rows = parse(download(url()))
            skipped = getattr(parse, 'skipped', 0) if parse is parse_ey else 0
            with LOCK:
                LISTS[name] = {'fetchedAt': time.time(), 'rows': rows, 'parser': PARSER_VERSION, 'skipped': skipped}
            save(data_dir)
            status(name=name, rows=len(rows), error='', skipped=skipped)
        except Exception as exc:     # never breaks the layer; the old copy stays
            status(name=name, rows=len((have or {}).get('rows', {})), error=type(exc).__name__ + ': ' + str(exc)[:80])


def lookup(noc: str, vehicle: str, enabled) -> tuple:
    """(whose list, details) for one bus, or (None, None). Exact matches only."""
    for name in enabled:
        code, whose = SOURCES.get(name, ('', ''))
        if code != noc.upper():
            continue
        with LOCK:
            rows = (LISTS.get(name) or {}).get('rows') or {}
        key = norm(vehicle) if name == 'tfl' else vehicle.strip()
        if key in rows:
            return whose, rows[key]
    return None, None


def counts() -> dict:
    with LOCK:
        return {k: {'buses': len(v.get('rows') or {}), 'skipped': v.get('skipped', 0), 'fetchedAt': v.get('fetchedAt', 0)}
                for k, v in LISTS.items()}
