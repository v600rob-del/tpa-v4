# TPA v4 HQ 4.0.8 — MapLibre worker URL repair

**29 October 2026 — local preview / NOT APPROVED / NOT DEPLOYED / NOT A STREET-RENDERING PASS**

## Owner's decisive Windows evidence, 4.0.7

- `browser.lastStage`: `maplibre-event`
- `browser.lastCode`: `map-vector-error`
- `browser.lastDetail`: `Failed to construct  Request : Failed to parse URL from /basemap/openfreemap/planet/20261025_080012_pt/8/126/81.pbf`
- `resourceCounts.vector`: 0; `upstreamErrors`: empty.

This identifies the immediate failure. The HQ Python proxy changed valid OpenFreeMap resource strings to **root-relative paths** (`/basemap/openfreemap/...`) inside the Liberty style and versioned TileJSON. MapLibre's worker tried to construct `Request` from those strings without a usable base URL. The request failed in the browser before reaching HQ, leaving no upstream error and zero vector requests. The old library, browser and provider were not the cause of this *reported* error.

## Exact bounded fix

1. Preserve historical **Leaflet 1.9.4 + MapLibre GL JS 5.24.0 + leaflet-maplibre-gl 0.1.4** loader, buffered adapter, and OpenFreeMap Liberty style. The HQ standard page and five empty v1 data-only sockets are unchanged.
2. Continue to cache rewritten JSON in an origin-neutral format (safe root-relative placeholders). **At HTTP response time**, change only `/basemap/openfreemap/...` strings to full URLs with the exact vetted local host and bound server port, e.g. `http://127.0.0.1:8772/basemap/openfreemap/planet/...`.
3. Apply it to all style, TileJSON, sprite, font and other allowed embedded map resource strings. Never rewrite arbitrary domains. Keep map supplier calls server-side only.
4. Strictly validate `Host` as `localhost:<actual-server-port>` or `127.0.0.1:<actual-server-port>` before creating any absolute URLs. Reject foreign hosts and mismatched ports. The HTTP cache does not persist host-specific URLs, so different local hostnames cannot poison one another.
5. Use separate Windows launch port **8772**; preserve 4.0.7 unmodified.

## Evidence and test limits

- **Real Chromium dedicated Web Worker:** `new Request('/basemap/openfreemap/planet/20261025_080012_pt/8/126/81.pbf')` fails with the owner's exact error; `new Request('http://127.0.0.1:<test-port>/basemap/...')` succeeds. Screenshot `HQ-4.0.8-REAL-CHROMIUM-WORKER-URL-TEST.png` is labelled worker-URL-only, not a map screenshot.
- **Real local Python HTTP requests with fixture source:** returned JSON contains full same-origin Liberty, TileJSON, sprite and glyph addresses; GET of the exact owner-failing versioned vector path returns 200 and increments the server's vector counter. PBF bytes are deliberately MOCK, not a real vector tile.
- **Cache and security:** requesting via both permitted local hostnames uses one source fetch and returns matching origins; foreign/mismatched Host headers are rejected.
- Previous suite covers local-only vendor routes, no Admin assets, component sockets, invalid URLs, gzip bounds and mocked desktop/tablet/mobile Leaflet navigation. Rerun all tests from the source folder.
- Browser access to localhost is blocked in this development environment (`net::ERR_BLOCKED_BY_ADMINISTRATOR`), and OpenFreeMap/jsdelivr DNS is inaccessible. Therefore **genuine OpenFreeMap street rendering, real mobile interaction, licensing/capacity, and Oracle production isolation are NOT verified.** The owner's Windows browser is the real acceptance test. No new supplier subscription or key.

## Test command

From extracted ZIP root: `python -m unittest discover -s v4/tests/hq -p "test_*.py" -v`

## Owner action

Extract the ZIP to a **new** folder, launch `START-TPA-V4-HQ.bat` (port 8772), and confirm whether Beverley streets load when zooming in/out. On failure, send `http://127.0.0.1:8772/hq-map-status` as before. No Oracle/legacy production changes, no HQ golden approval inferred.
