# TPA v4 HQ OpenFreeMap Liberty — 4.0.5 (local development)

Date: 29 October 2026 (UK). Status: **unapproved; not installed on Oracle; not live-data connected**.

## Owner's real evidence

4.0.4 on unchanged Windows browser showed `OpenFreeMap map unavailable (map-resource-unavailable). UK geographic fallback active.` The `/hq-map-status` record was `{"version":"4.0.4","renderer":"MapLibre GL JS","source":"OpenFreeMap","mode":"development-only","networkDisabled":false,"upstreamErrors":{},"mapLibreAssetCached":true}`. **This is a failed real-world acceptance test.** Nothing indicates the browser changed.

## Source defects reproduced in 4.0.4

- The actual OpenFreeMap Liberty style references `https://tiles.openfreemap.org/sprites/ofm_f384/ofm` for its sprites and `https://tiles.openfreemap.org/natural_earth/ne2sr/{z}/{x}/{y}.png` for shaded low-zoom geography. The former allowlist had neither route. When the browser requested them, the server responded `404` *before recording any upstream error*, making `upstreamErrors:{}` misleading.
- Previous tests used the invented `/styles/liberty/sprite.png` fixture path instead, so they failed to catch this mismatch.
- Real `.pbf` vector tiles can be gzip-encoded; the old proxy always stripped upstream content-encoding headers, without decompressing the data. This could make workers misparse a compressed tile. 4.0.5 expands at most 8MB of gzip data safely before serving it.
- A cached MapLibre JS file indicates only server availability, not that the browser successfully initialised the library. In 4.0.5, the browser now reports bounded codes (`library-missing`, `map-style-error`, `map-resource-error`, `map-loaded`, etc.) to a localhost-only, development-diagnostic endpoint. No credentials or raw URLs are sent.

## 4.0.5 changes

- Limited proxy allowlist for real OpenFreeMap sprites and Natural Earth; retained source-specific host/path controls and cache/request budget. No loosening to arbitrary upstream URLs.
- Rejection counters and classified browser error counters in GET `/hq-map-status`.
- Bounded gzip decoding, no TLS verification bypass.
- Start script uses localhost port **8769**, so 4.0.4 on 8768 and earlier checkpoints are not replaced.
- Only HQ source/tests/docs changed. No Admin, engine, shared gateway, supplier credential or Oracle change.

## Tests actually performed

- 7 existing local HTTP/security tests — PASS.
- 4 existing proxy tests — PASS.
- 1 existing mock HTTP vector resource fixture — PASS.
- 4 **new** regression tests: production-shaped Liberty sprites, terrain, font, tile and style resources; disallowed-path diagnostics; bounded browser diagnostics; mock gzip vector decode — PASS.
- Chromium tests at desktop 1440×900, tablet 820×1180 and mobile 390×844: fallback pan, zoom, Beverley Home, Hull search and no JS errors — PASS.
- Mock MapLibre browser test at all three screen sizes: Liberty style URL and controls — PASS.
- Mock valid bus / invalid rail / malicious label display — PASS; fixture evidence, **NOT** process isolation.
- Node JS syntax and Python syntax checks — PASS.

**Not proven:** Real OpenFreeMap CDN resources from the user's Windows host, real WebGL vector street display, end-to-end on-device zoom, production license and capacity, gateway security, OS service isolation. The HQ golden gate is still **CLOSED**. One Windows check after this fix is necessary.

## Windows test

Extract to a **new folder**, start `START-TPA-V4-HQ.bat`, open `http://127.0.0.1:8769/`, pan/zoom around Beverley, check streets, Home and Hull. If map fails, paste output from `http://127.0.0.1:8769/hq-map-status`; it now includes `resourceCounts` and `browser` codes in addition to `upstreamErrors`.
