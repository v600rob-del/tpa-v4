# TPA v4 HQ — five data-only socket containment QA (4.0.8 derived)

**Status:** LOCAL MOCK COMPONENT INTERFACE TEST; NOT HQ GOLDEN, NOT production failure isolation.

## Protected owner-accepted checkpoints
- Owner-confirmed working OpenFreeMap/Leaflet/MapLibre HQ 4.0.8: SHA256 `67c0d2b33602fc102ddb3ecd884cd3684a1aa6d33faedac47724056b75b8615f` (original ZIP preserved).
- Owner-accepted upright ship/icon design checkpoint (PC-only review): SHA256 `56b6876c349eedce2bcc4aa92921d857763588e6c09661a05cc2935ed394278c` (original ZIP preserved).
- This candidate is separate and uses local port **8776**. No Oracle v3, Admin, supplier, engine, gateway, browser map library or basemap code change.

## HQ-only change
`v4/hq/assets/hq.js` now rechecks same-origin `/api/v4/manifest` every 30 seconds (not only at startup) and launches independent, bounded per-component snapshot reads concurrently. Snapshots have a 5-second timeout and 1.2-MB UTF-8 byte cap; manifest has a 32-KB limit and same timeout. Each component can become ready, unavailable, stale or not_installed, independently. Detaching a component clears only its markers and selection. Invalid data and fetch timeouts mark only the affected component unavailable; last-known markers, if retained, are **visibly stale**. No component-supplied JS, CSS, HTML or movement logic is executed. This reuses draft `contractVersion: 1.0`; **no gateway or contract schema file is changed.**

## Actual tests
- Full local HQ suite: **34 tests passed**, including real Chromium DOM tests with **MOCK** network responses and map geography.
- Dynamic Chromium fixture test: attach all five layers -> nine fictional markers; detach rail -> buses remain; malformed rail location -> rail unavailable, buses unchanged; rail recovery; rail fetch hangs -> bus updates during rail timeout, rail last-known turns stale; untrusted HTML label displayed as literal text, no DOM injection; no JavaScript errors.
- Existing test suite includes server HTTP/security/asset tests and fixture-only desktop/tablet/mobile browser tests. None are production isolation tests.
- Real-browser screenshot: `SOCKET-MOCK-RAIL-FAILED-BUS-OK.png` — screenshot shows offline fallback geography and **mock** markers only. Not proof of street-map rendering or live transport.

## Source integrity
These **six core map/design files** are byte-for-byte unchanged versus the approved upright-boats icon review:
- `v4/hq/index.html` SHA256 `7420cf2e9776f863d1dd0b4ccdf96d8f018aae96494e242fab2767815b05e77d`
- `v4/hq/assets/hq.css` SHA256 `8def482bd20eb2f2347bfbc9d94bc0832196d15b424154da7d37369e7379b592`
- `v4/hq/assets/tpa-basemap-loader.js` SHA256 `c8a9a516339548de7207eab19dc45a6265e79072191c5590b4d5567c1e17662c`
- `v4/hq/assets/transport-vector-layer.js` SHA256 `3d89f5136e9365bb19bf3f4b7026c6d5cba9c5d5f587b136a11c763e9721e225`
- `v4/hq/serve_hq.py` SHA256 `15c77bb4986bf63e91ddba3167a77cb889f686060c7966eae141f4b882934271`
- `v4/hq/data/uk-geography.json` SHA256 `683feded09c0d0be7f60cfac6a5aef64b5de9d3dec5cc7fd367093dd0d915487`

## Outstanding gates before owner HQ golden sign-off
- Control Desk/shared gateway: authenticated manifest, production RBAC, exact v1 schema/quotas, per-client bounding, source time and input rejection contracts. Draft `observedAt` on envelope remains optional; no shared contract change in this work.
- Real process/deploy isolation on nonproduction Oracle-equivalent Linux: separate OS service user and release directory, forbidden write/restart, kill/timeout/rollback, and proof unaffected components continue using exact versions.
- Browser actual map delivery on deployment network, external resource egress, licensing/capacity and full restore drill; user final explicit HQ sign-off.

**Owner action:** None for this MOCK technical QA step. PC-only owner testing remains default. Maintain original 4.0.8 working map unchanged.
