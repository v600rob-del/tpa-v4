# TPA HQ 4.0.8 — independent MOCK marker artwork review

**Status:** local design review only; not live vehicle data, not owner-approved icon artwork, not HQ GOLDEN. Existing owner-confirmed 4.0.8 Windows and tablet map builds are preserved unchanged. Existing Oracle v3 untouched.

## Actual contents
- Same **Leaflet 1.9.4 + MapLibre 5.24.0 + 0.1.4 bridge + OpenFreeMap Liberty** map loader and geographic data as owner-confirmed 4.0.8.
- Dedicated home-LAN preview launcher on port **8774**, independent temporary HTTP tile/vendor cache.
- Five **static data-only JSON fixtures** (air, sea, rail, bus, road); nine intentionally fictional marker positions around Beverley. No engines, supplier API keys or real transport feeds.
- Visible `MOCK ICON REVIEW` labels in the sidebar, map and source statuses. Selection details explicitly say the vehicle is fictional.
- Historical TPA SVG paths for aircraft/ships/trains taken from `app/web/main.js` at GitHub source commit `f24e905cb34255d16cb78721102aedec9ee402a9`. Buses use the historical-style green bus/route treatment. These are **proposed visual renderings**, not yet approved.
- A road **status marker** is shown solely to review a generic road symbol. It is **not** the eventual traffic line/incident overlay, which must be evaluated separately with genuine licensed traffic data.
- A click/tap on a marker opens details and adds a yellow selection outline. Clicking again clears selection. Layer visibility and search by mock label are interactive.

## Tests performed in this environment
- `python -m unittest discover -s v4/tests/hq -p 'test_*.py' -q` → **31 tests PASS**.
- Genuine Chromium DOM/CSS/JS interactions with **mocked API responses and offline geographic fallback** on desktop (1440×900), tablet (700×1024), mobile (390×844). Nine markers visible; check SVG paths/badges and road symbol; toggle bus layer; select/unselect; search mock train and Home; no JS page errors.
- Genuine HTTP tests of all five fixture-backed same-origin component endpoints, mock manifest, standard-only path restrictions and unauthorised Admin 404s.
- Existing regression tests for map resource proxy, validation of poisoned payload, security/host restrictions and URL parsing pass.
- Historical map loader, buffered adapter and geography files have **unchanged SHA-256** relative to prior 4.0.8 tablet development copy.

## Not verified
Real OpenFreeMap street tiles, MapLibre worker in a browser reaching the internet, physical tablet tap targets or owner visual approval **on this mock build**. Genuine live transport/traffic position, movement, production isolation and Oracle deployment are not tested. The screenshot included here is explicitly an **offline MOCK** geographic fallback, not a purported screenshot of genuine street tiles.

## Restore
Close the preview's black window. Delete this **separate review folder** if desired. The working 4.0.8 builds remain intact on ports 8772 and 8773 and have not been overwritten.
