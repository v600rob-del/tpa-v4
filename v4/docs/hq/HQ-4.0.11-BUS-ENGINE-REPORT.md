# HQ 4.0.11 + Bus Engine 0.2 (BODS)

Built 30 Oct 2026 on the owner-confirmed road version (`tpa-v4/road-engine-0.1` @ `291de0e`).

## Separate bus engine — `v4/engines/bus/bus_engine.py`
Own process on 127.0.0.1:8781. Standard library only. Data: BODS SIRI-VM. BODS confirmed in writing (ticket BSD-1280) that free third-party use and a shared server cache are fine.
- **Mode `area` (default, PC test):** asks `https://data.bus-data.dft.gov.uk/api/v1/datafeed/?api_key=…&boundingBox=W,S,E,N` (the v3-proven format) for the viewed area plus 0.15°, capped at 3°. At most every 30 s; a newly viewed uncovered area is fetched within ~3 s. Small downloads, suited to home broadband.
- **Mode `bulk` (future server):** `https://data.bus-data.dft.gov.uk/avl/download/bulk_archive?api_key=…` (zip containing siri.xml), whole GB, once per poll, as BODS recommended for national maps. Not for home broadband.
- Fetches only while viewed (an HQ status or snapshot request in the last 3 min). After errors it backs off up to 5 min.
- Streaming XML parse. DOCTYPE/ENTITY rejected (blocks entity-expansion attacks). 32 MB download cap. Duplicate vehicles removed. GB bounds only.
- Positions are as reported, never animated. Over 5 min old → `stale` (faded); over 30 min → dropped. At most 400 per view, nearest the screen centre first; views taller than 1.6° latitude → "Zoom in to see buses".
- Key from `TPA_BODS_API_KEY` only; never logged or returned (the URL holding it is never printed).
- Reports "ready · Connecting to BODS…" before the first data, so HQ sends the viewed area (avoids a start-up deadlock).

## HQ 4.0.11 (general)
- Re-checks every 10 s while any live socket isn't ready (the original 30 s timer is unchanged).
- A live frame with a notice and no entities is re-asked after 5 s.
- The status line uses the latest frame's notice, falling back to the manifest's.
- Credits: Buses → "Live in test".

## Launcher
Asks for TomTom and BODS keys separately, hidden; each can optionally be saved encrypted (DPAPI: `tomtom-key.xml`, `bods-key.xml`). Each key is put into the environment only for the instant its own engine starts. `FORGET-SAVED-KEY.bat` removes both.

## Tests (workspace, 30 Oct 2026)
- HQ 39 OK (2 skipped), road 11 OK, **bus 12 OK** (pretend BODS that filters by area like the real one).
- Bus tests cover: no key → zero requests; buses in view, labels, badges, stale marking, dropping old reports, duplicates, hostile text as plain text; correct boundingBox order and key parameter; key never logged; zoom-in notice; one download per poll however many requests; new area fetched quickly; stops when nobody is looking; bulk zip mode; rejected key; hostile XML refused; 400-per-view limit; real browser with live buses + traffic; bus engine crash affects only buses.
- Launcher (PowerShell 7, pretend keys): TomTom key only in the road engine, BODS key only in the bus engine, neither in HQ; nothing printed; all three programs stop together.

**Not proven here:** the real BODS service and the owner's real key (no BODS access from the workspace); Windows PowerShell 5.1.
NOT Golden.
