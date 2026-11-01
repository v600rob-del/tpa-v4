# TPA v4 HQ historical-map recovery — candidate 4.0.7 (29 Oct 2026)

**Status: NOT approved / NOT locked / NOT deployed / genuine OpenFreeMap streets NOT verified.**

## Verified historic comparison (private GitHub)

- Commit `3289ff3fc3401bbc4e700cd38e5c34259312647d`: Leaflet 1.9.4, MapLibre GL JS 5.24.0, `@maplibre/maplibre-gl-leaflet` 0.1.4; `L.map('map',...)`; vector layer with Liberty style `https://tiles.openfreemap.org/styles/liberty`.
- Commit `f24e905cb34255d16cb78721102aedec9ee402a9`: same Leaflet owner and Liberty style; pinned locally served vendors `/static/vendor/*`, dedicated `tpa-basemap-loader.js` and `transport-vector-layer.js` buffered adapter. The latter is unchanged between the two source commits (GitHub blob SHA `0252cdacd8f359a0633d7eef087faa06450bc87e`).
- HQ 4.0.6 changed to standalone `new maplibregl.Map`, dropped Leaflet + adapter, switched MapLibre to 5.6.1, and introduced a restrictive same-origin style/vector proxy. User's Windows test showed `map-vector-error`. The **underlying runtime message was never supplied**, so *its immediate root cause is unconfirmed*. Earlier bugs in specific proxy allowlists are source-proven, but do not prove that all current failures were those bugs.

## What candidate 4.0.7 changes

1. Returns Leaflet ownership of map, drag, zoom, lat/lon projection and selection. Does not copy v3 feed code.
2. Restores MapLibre JS v5.24.0 + Leaflet 1.9.4 + `leaflet-maplibre-gl` 0.1.4 vendor pins; own-origin server downloads/cache of these fixed library URLs on first use, no direct browser third-party connections.
3. Restores the dedicated async basemap-loader sequence and historical buffered adapter behaviour. Proxy retains Liberty style, resource limits, same-origin JSON rewrite and proper credits.
4. Retains v4 standard page appearance, Home=Beverley, search, five empty sockets and no Admin/engine scripts.
5. Adds localhost-only diagnostics: `browserHistory`, `browser.lastStage`, `browser.lastDetail`, `lastFailedResource`, resource counts and vendor cache status; report is bounded, with no stored coordinates or usernames.
6. Keeps geographic fallback on map errors without substituting alternative provider.

## Tests actually performed

- `python -m unittest discover -s v4/tests/hq -p 'test_*.py' -q` = **20 tests passed** (7.728s) including proxy URL validation, compression, JSON rewriting, forbidden asset/auth paths and browser diagnostic endpoint.
- Actual Chromium engine executes HQ JS, restored loader and buffered adapter on desktop 1440×900, tablet 820×1180, mobile 390×844 **with mocked Leaflet/MapLibre vendor objects**. Zoom, Home, Hull search, five sockets and zero browser runtime errors passed. Screenshot prominently labelled MOCK; it does not show genuine street tiles.
- Previous v4.0.6 tests dependent on a direct standalone MapLibre map were preserved in `v4/docs/hq/previous-406-tests` rather than silently rewritten as if equivalent.
- Local Python/Node syntax and ZIP integrity verified.

## Not proved / no claim of success

The test container cannot resolve `tiles.openfreemap.org`, jsdelivr or unpkg; browser navigation to localhost is blocked by sandbox policy. Thus no real Leaflet+MapLibre+Liberty WebGL street map has been exercised end-to-end and **the exact owner's 4.0.6 `map-vector-error` is still not attributable to a specific network response or browser exception**. No production isolation or mobile device certification, no deployment/licence approval, no golden HQ sign-off.

## Next owner test (only if approved as a diagnostic candidate)

Extract ZIP into a new folder, run `START-TPA-V4-HQ.bat`; port 8771. If streets do not load, open `http://127.0.0.1:8771/hq-map-status` and copy full JSON. Use `browserHistory`, `lastFailedResource`, `upstreamErrors` to identify actual failing URL/error; do not guess browser settings. v3 Oracle untouched.
