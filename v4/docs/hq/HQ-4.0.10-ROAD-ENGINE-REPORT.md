# HQ 4.0.10 + Road Traffic Engine 0.1 (TomTom)

Built 30 Oct 2026 on HQ 4.0.9 (`tpa-v4/hq-4.0.9-welcome` @ `39b37c4`). Owner request: road traffic first, as a quick live win.

## Separate road engine — `v4/engines/road/road_engine.py`
Own process on 127.0.0.1:8782. Standard library only. Reuses the owner-proven v3 TomTom settings: Orbis raster traffic-flow tiles, apiVersion 2, `style=dark`, zoom 8–17, cache ≤480 s, 12,000 requests/month safety cap (persisted in `%LOCALAPPDATA%\TPA\road-engine`).
- Key from `TOMTOM_API_KEY` only, sent as the `TomTom-Api-Key` header (never in the URL), never logged or returned. No key → zero TomTom requests.
- One shared fetch per tile (concurrent viewers wait for the same request); memory cache of 600 tiles.
- Only tiles touching the UK are ever requested; other places or zooms return "nothing" (204).
- Health/notice: key missing; key rejected (401/403); monthly cap reached; TomTom not responding.
- Serves `/status`, `/snapshot` (contract v1.0, no entities, with an `overlay` description) and `/tiles/z/x/y.png`.

## HQ 4.0.10 (general, nothing TomTom-specific)
- Server: `TPA_HQ_ENGINES=id=http://127.0.0.1:port,...` connects any socket to its engine. HQ relays JSON frames and PNG overlay tiles only (bounded size and time), never through any proxy. A missing or crashed engine marks only that socket "Engine not running".
- Contract v1.0 gains optional extras: frame `notice`, frame `overlay` (raster tiles from that socket's own same-origin path only), entity `badge`. Old frames still work unchanged.
- Page: live sockets show LIVE / DELAYED / OFF AIR and plain wording, never "MOCK"; pretend sockets keep their MOCK labels. The banner and sidebar say which layers are live. Picture layers sit in their own map pane above streets and below icons. Overlay credit text is escaped. Engines are asked only for the visible area, and re-asked after the map moves. "not_installed" now reads "Not connected yet".

## PC launcher — `START-TPA.bat` → `v4/launch/start-tpa.ps1`
Asks for the TomTom key hidden. It can optionally save it with Windows DPAPI (`Export-Clixml`, this Windows user only; `FORGET-SAVED-KEY.bat` removes it). The key is given only to the road engine process and removed from the launcher before HQ starts. Closing TPA stops the engine. Works with paths containing spaces. Windows PowerShell 5.1 compatible (ASCII only).

## Tests (workspace, real Chromium, 30 Oct 2026)
- HQ suite: 39 run, OK, 2 skipped (unchanged from 4.0.9).
- `v4/tests/engines/test_road_engine.py`: 11 OK, run against a pretend TomTom server. Covers: contract; fetch-once cache; 8 simultaneous viewers → 1 request; no requests outside the UK or at wrong zooms; no key → zero requests; monthly cap; rejected key; key never in URL or logs; real HQ relay; real browser draws the layer, LIVE labels, toggle off/on, "zoom in" message; road engine killed → only road affected.
- Launcher (PowerShell 7 in the workspace, pretend key): the key is in the road engine's environment only, not HQ's, never printed; both programs stop together; folder with a space works.

**Not proven here:** the real TomTom service and the owner's real key (workspace has no TomTom access), and Windows PowerShell 5.1 itself. Browser tests used Leaflet 1.9.4 and MapLibre 5.24.0 from their official GitHub releases (no CDN in the workspace); the MapLibre bridge was absent, so the street map fell back to the built-in geography in screenshots.

NOT Golden. MOCK icons are not included in this package; unconnected layers stay empty.
