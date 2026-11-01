# TPA v4 HQ MapLibre 4.0.6 — versioned OpenFreeMap tile repair

29 October 2026, UK. **Local development ONLY. HQ is NOT owner-approved, frozen, production-secured or deployed.**

## Genuine owner evidence

- Owner ran 4.0.5 on unchanged Windows/browser.
- Map showed `OpenFreeMap map unavailable (map-vector-error). UK geographic fallback active.`
- This is a **failed live map acceptance test**. Browser itself is not assumed broken.

## Root defect actually reproduced in code

OpenFreeMap's official documentation explicitly supports `/planet/latest` TileJSON and versioned PBF tiles such as `/planet/latest/{z}/{x}/{y}.pbf`, as well as `/planet/YYYYMMDD_HHMMSS_pt/{z}/{x}/{y}.pbf`.

The 4.0.5 HQ resource proxy admitted only `/planet` and `/planet/{z}/{x}/{y}.pbf`, therefore rejecting these **valid, commonly advertised tiles** before fetching. 4.0.5 also failed to treat a versioned TileJSON response as JSON and rewrite its absolute tile URLs back to same-origin HQ paths.

Code proof (directly importing both source versions):

| Official OpenFreeMap path | HQ 4.0.5 | HQ 4.0.6 |
| --- | --- | --- |
| `/planet/latest` | REJECT | ALLOW, JSON rewrite |
| `/planet/latest/9/276/167.pbf` | REJECT | ALLOW, vector data |
| `/planet/20250507_001001_pt/9/276/167.pbf` | REJECT | ALLOW, vector data |

This **does not establish** that these requests alone explain all errors on Windows; real 4.0.6 streets must be tested there.

## Scoped change

- Allow only provider-documented `latest` or date-stamped Planet tile versions, with bounded zoom/x/y path segments and `.pbf` extension. This does not make HQ an arbitrary HTTP proxy.
- Parse and same-origin rewrite versioned Planet TileJSON, and provide correct `application/json` response type.
- Classify versioned metadata as tilejson and versioned payloads as vector for accurate resource counters.
- Move local test server from port 8769 to port **8770**, preserve all earlier ZIPs, do not change Oracle/v3, Admin, engines or gateway contracts.
- Five sockets stay empty and data-only. MapLibre/Liberty style retained.

## Test evidence (development lab only)

- **19 Python unit, HTTP, security, and proxy integration tests PASSED.** Three new tests include authentic OpenFreeMap documentation URL patterns, end-to-end local HTTP TileJSON rewrite, mock `.pbf` delivery, and disallowed path rejection.
- Real Chromium desktop/tablet/mobile interactive **geographic fallback** tests passed: Home, Hull search, zoom, drag, zero page JavaScript errors.
- MapLibre JavaScript **MOCK** browser integration passed at three responsive sizes: controls, same-origin Liberty style and empty sockets.
- Mock bus-valid / rail-invalid containment and inert injected text passed. This is **NOT** process/deployment isolation evidence.
- Syntax check passed.

**Unproven:** genuine live OpenFreeMap tiles in a real MapLibre WebGL browser; network, HTTPS, source coverage/caching limits; real Windows/tablet/phone acceptance; OS permissions; production gateway/RBAC or independent engine isolation.

## Owner test (one action)

Extract into a new Windows folder. Run `START-TPA-V4-HQ.bat`, which opens `http://127.0.0.1:8770/`. Check whether the actual street/road map appears around Beverley. If it still fails, send the JSON at `http://127.0.0.1:8770/hq-map-status` (specifically `resourceCounts`, `upstreamErrors`, `browser`).

Official URL documentation: https://github.com/hyperknot/openfreemap/blob/main/README.md
