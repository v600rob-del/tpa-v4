# TPA v4 HQ 4.0.4 — OpenFreeMap + MapLibre (29 October 2026)

## Scope and preserve

New **standard HQ only**, in `v4/hq/**`, `v4/tests/hq/**`, `v4/docs/hq/**`. No v3/Oracle modifications; Admin and five engine codepaths untouched. The 4.0.3 and prior original preview ZIPs remain separate intact checkpoints. Owner has NOT accepted/frozen HQ. GitHub #38 is the coordination record. This local ZIP is not a GitHub commit or Oracle deployment.

## Actual renderer and route

Replaced legacy custom OSM raster image tiles with a **MapLibre GL JS v5.6.1** browser renderer and **OpenFreeMap Liberty** style (matching the original v3 map choice found in `app/web/main.js`). The local server requests pinned MapLibre library assets from a fixed CDN list on demand and caches them. It fetches style/TileJSON/tiles/sprites/glyphs exclusively from the official `tiles.openfreemap.org` origin, serves everything via HQ's own localhost URLs, and rewrites OpenFreeMap style and TileJSON resource URLs to that same origin. The browser has CSP `connect-src 'self'`; no CDN or feed links run in the standard page. HTTPS certificate verification is always enabled; outbound map requests have a four-request concurrency cap, cache, error backoff and a development request-rate guard, with no all-session tile cap.

All 5 component sockets are empty by default, validated v1.0 data-only snapshots are read independently and rendered as untrusted text. No movement modelling, provider credentials, Admin scripts or controls. Home: Beverley. UK pan/zoom, Hull/places search, layer controls and mobile sidebar retained. Built-in GSHHG/NOAA geographic fallback retained for connectivity failure or repeated vector-resource errors, not as substitute for the requested vector streets.

## Tests actually run

- `python -m unittest test_http test_openfreemap_proxy test_http_mock_vector -v`: **12 PASS**. Local HTTP/security routes, empty sockets, CSP, disallowed paths and mock vendor/style/vector tile + cache checks. **MOCK source**, not genuine OpenFreeMap connectivity.
- `python test_browser.py`: desktop/tablet/mobile geographical fallback zoom, Beverley Home, Hull search, mouse pan, no JS exceptions — PASS in isolated Chromium. The browser tool blocks navigating to localhost, so HTML/CSS/JS were injected in a sandbox.
- `python test_maplibre_browser_mock.py`: desktop/tablet/mobile with **FAKE MapLibre object**: server-only Liberty style path, OpenFreeMap URL rewriting, zoom/Home/Hull search, zero JS errors — PASS. This does **not** prove real WebGL renderer or real street tiles.
- `python test_fixture_browser.py`: valid bus MOCK visible, invalid rail MOCK isolated, HTML-injection string not executed — PASS. Not genuine process/release isolation.
- `node --check v4/hq/assets/hq.js`; `python -m py_compile v4/hq/serve_hq.py` — PASS.

## Not proven — important

Container DNS/network access is blocked, so this build **cannot be verified against the genuine MapLibre CDN or OpenFreeMap live style/tiles** here. It requires an internet-connected Windows test using `START-TPA-V4-HQ.bat`. If the full vector renderer fails, the geographic fallback and diagnostic status remain; use `http://127.0.0.1:8768/hq-map-status` for server-side error codes. Local previews do not establish final licence rights, geographic coverage, bandwidth capacity, live map success, production security, actual mobile touch behaviour, or process/deployment isolation. Full approval requires owner visual check and Control Desk gates.

## One Windows acceptance test

Extract into a new directory; double-click `START-TPA-V4-HQ.bat` (port 8768). Verify real **streets and roads** around Beverley at zoom 12–15, then zoom out/in, search Hull and press Home. If no roads appear, open `/hq-map-status` and report the `upstreamErrors` object. Preserve all prior version folders.
