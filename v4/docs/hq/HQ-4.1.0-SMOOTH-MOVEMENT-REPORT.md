# HQ 4.1.0: smooth movement (+ air 0.2.0, bus 0.3.0, rail 0.3.0)

Built 30 Oct 2026 on `tpa-v4/server-0.1` @ `48ebea2`. Owner: "get the vehicles of any type moving smoothly first... Speed at all layers will be key to success, and smoothness too."

## How it works (contract stays 1.0; new fields are optional)
- **The engine says how a vehicle moves.** It adds an optional `entity.motion`; HQ only does generic arithmetic with it.
  - `{"mode":"heading","speedMps":n,"maxSeconds":n}`: carry on along `position.headingDeg` at that speed.
    - Aircraft use their ADSB.lol ground speed and track, capped at 60 s. Not used on the ground.
    - Buses use a speed worked out from the same bus's last two real reports (5 s to 5 min apart, 0.5–35 m/s), capped at 30 s because roads bend. Bearing comes from the data, or is worked out from the two reports.
  - `{"mode":"toward","lon","lat","arriveAt","maxSeconds"}`: head in a straight line for a point, arrive at `arriveAt`, then stay.
    - Trains: after a DEPARTURE (or pass), TRUST's `next_report_stanox` and `next_report_run_time` give the next station or timing point and the minutes to it. Trains standing at a platform (ARRIVAL) don't move.
  - Malformed hints are ignored. Speed is limited to ≤350 m/s and "toward" to ≤1.5°/1° away.
- **Honest labelling.** Any position that isn't exactly the last real report (carried forward, or sliding to a new report) gets a dashed outline. Its details say **"Estimated position · last reported hh:mm:ss"**. Mock layers never move.
- **New reports.** A vehicle already on screen slides to its new position over 1.2 s instead of jumping. `prefers-reduced-motion` turns the slide off.
- **Speed.**
  - Markers are reused and moved with an inline transform, not rebuilt.
  - Up to 10 updates/s, slowing to about 3/s on slow devices (it measures itself), only while the tab is visible and something is moving.
  - Phone-width screens show at most 150 vehicles per layer, nearest the middle first.
  - Each layer is re-asked at its engine's `nextPollAfterMs`: air 10 s, bus 15 s, rail 15 s (was 30 s for all). This only happens while the page is visible.
  - A pan or zoom inside the area already fetched (with a 20% margin) costs no request.
  - A reply older than one already shown is ignored.
- **Updates show at once.** The page's own files are sent `Cache-Control: no-cache`, so phones no longer keep an old copy for 30 minutes.
- **Credits.** Trains now use Network Rail's licence wording ("Contains information of Network Rail Infrastructure Limited licensed under the following licence…") with the licence link. All three vehicle credits say positions are estimated between reports.
- **Rail bonus fix.** Station and timing-point codes are now matched in one 5-digit form. 1,597 timing points with a leading zero (mostly Scotland) were previously never matched.

## Evidence (workspace)
- **`v4/tests/hq/test_motion_4100.py`** (real Chromium, pretend moving engine) checks that:
  - the jet glides east (heading 90°), is dashed, and its details say Estimated;
  - a vehicle without a hint and one with a nonsense hint stay still;
  - "toward" reaches its point at `arriveAt` and stops there;
  - a new report gives an in-between (estimated) position, then the real one;
  - the marker element is reused;
  - the layer is re-asked every ~5 s as told (2–4 times in 11 s);
  - a small pan costs nothing, and a big zoom-out refetches;
  - `no-cache` is sent on the page files.

  Stable over repeated runs.
- **`v4/tests/engines/test_motion_hints.py`**: air speed/ground rules, bus speed from two reports (and its nonsense guards), rail next point/run time, rules for arrival/unknown/zero/terminated/same place, the label "next: Beverley", the leading-zero match, and saved memory keeping motion while rejecting bad values.
- **Totals:** HQ 49 OK (2 skipped), engines 51 OK, server 15 OK.
- **Load** (`v4/tests/load/motion_perf_check.py`, headless Chromium, main thread busy while gliding):

  | Screen | Vehicles on screen | Main thread busy |
  |---|---|---|
  | Desktop | 400 | 8% |
  | Desktop | 1,200 | 10–11% |
  | Phone with CPU slowed 4× | 150 (limit) | 29–30% |

  The phone was 59% with about 580 markers before the limit and transforms.

**Not proven here:** real feeds. In particular, how close real `next_report_run_time` straight lines look against real track, and how often bus speeds can be worked out from real BODS data. The owner's eyes on https://test.tpalive.co.uk will tell.
NOT Golden.

# Update 4.1.1 / train engine 0.4.0: trains follow the track (30 Oct, 19:25)
**Owner:** with 4.1.0 live, a train (2R26, Hutton Cranswick → Beverley) glided in a straight line beside the railway. Also "the close X is in the way of the text".

## Trains along the track
- `data/rail-network.json.gz` (1.1 MB) is built by `tools/build_rail_network.py` from Network Rail's track model.
  - Source: NetworkLinks, an EIR release under OGL v3.0, via openraildata/network-rail-gis.
  - Build: pieces joined at shared ends, simplified to within 8 m, converted to WGS84 (tested against the OS worked example).
  - Result: 36,394 junctions, 48,219 sections. The tool rebuilds the identical file.
- **`track_router.py`:**
  - Joins the track at the nearest point on the line (within 1.5 km), not only at junctions. Quiet lines have junctions miles apart.
  - Finds the shortest path (A*) between the two places.
  - Rejects detours over 2.5× the straight distance + 3 km.
  - Returns at most 160 points.
  - Real routes: Hutton Cranswick→Beverley 13.2 km (straight 12.6), Beverley→Hull 13.3, King's Cross→Peterborough 122.6.
  - About 1 ms per leg here. Loads in 0.6 s; about 85 MB.
- **Engine 0.4.0:**
  - Routes are worked out in a background thread, newest first.
  - The frame sends `motion {"mode":"path","points":[[lon,lat],...],"arriveAt","maxSeconds"}`.
  - Until a route is known, or if none is possible, the train **waits at its last reported place**. There is no straight-line short cut any more.
  - `/status` adds `trackMapLoaded`, `trackRoutesKnown`, `trackRoutesWaiting`, `trackError`.
- **HQ 4.1.1:** new generic `path` mode. HQ follows the points by distance and arrives at `arriveAt`. The first point must be within 0.05° of the reported position, and every point within 2°.
- **Credits/attribution:** "Railway lines: Network Rail track model (OGL)". `data/SOURCES.txt` updated.

## Details box
- The title keeps 30 px clear of the × button. The button has a proper 34 px tap area.

## Tests
- Browser: a path vehicle goes east first and doesn't cut the corner, then ends north-east.
- Engines: real routes (lengths and ends), every route point within 60 m of the track, no route out to sea, the train waits before a route is known and follows it once known, no route means it stays put.
- Totals: HQ 49 OK (2 skipped), engines 54 OK, server 15 OK. Phone load check unchanged (~27%).

# Update 4.1.2 / train engine 0.4.1 (30 Oct, ~20:10)
**Owner:** "I need fluid movement… This will be the standard for movement of all levels". Also: trains can disappear; the scroll wheel stops over an icon.

## Fluid movement (the standard for every layer; it lives in HQ, so ships will get it too)
- Positions are worked out about once a second. The browser animates each moving icon between them with a 1 s linear transform transition, drawn every frame.
  - Test: at least 20 distinct on-screen positions per second; typically about 60.
- While the map itself is dragged or zoomed, gliding is switched off, so icons stay pinned to the map.
- **The page measures its own smoothness.**
  - If frames average over 30 ms, fewer icons glide: those nearest the middle of the screen keep gliding and the rest step once a second. The budget recovers when frames are fast again.
  - Starting budget: 600 gliding icons on computers, 60 on phone-sized screens (battery).
  - `window.TPA_HQ_GLIDE_STATUS()` shows `{budget, gliding, frameMs}`.
- **Load** (headless Chromium, which has **no graphics chip**, so a worst case):
  - Computer: 400 icons at 32% busy and 60 fps. 1,200 icons self-limit to about 90 gliding at 18% busy and 60 fps.
  - Phone CPU slowed 4×: 150 icons with 60 gliding at 46% busy and 60 fps.
  - Without animation the same scenes cost 3% / 16%. Real devices composite on the GPU, so they should do better. **To check on the owner's PC and phone.**
- `prefers-reduced-motion` turns gliding off.

## Wheel and drag over icons
- The mouse wheel over an icon zooms the map. Click-and-drag starting on an icon pans the map, and the click that ends a drag doesn't open details.
- A plain click still opens details. Guarded for a map without `getContainer`, so the older recovery test still passes.

## Trains disappearing (rail 0.4.1)
- Finished trains stay for 20 min (was 5), as "terminated at X". They usually still stand in the platform.
- Cancelled trains (TRUST 0002) are no longer removed at once. They stay for 10 min, faded, as "cancelled · last at X", with no gliding. The last real report time is kept, not replaced by the cancellation time.
- Reinstatement (0005) clears the cancellation. `/status` adds `cancellationsSeen` and `reinstatementsSeen`.
- A train outside the requested area whose next reporting point is inside it is now sent too, so it glides in instead of popping up later.
- Still expected (true to the data): trains not heard from for 30 min drop off, and icons leave as they move off-screen.

## Tests
- `test_motion_4100`: 4 tests, adding fluidity, path corner (from what's drawn), wheel/drag/click. Stable over 3 runs.
- `test_motion_hints`: adds finished, cancelled, reinstated and heading-into-view cases.
- Totals: HQ 50 OK (2 skipped), engines 55 OK, server 15 OK.

# Update 4.1.3 / bus engine 0.3.1 (30 Oct, ~20:25)
**Owner:** "buses move smoothly, but i suspect only when they get updated data". Then: "we need it to appear like we have gps when we dont, so a smooth lifelike movement".

- **Cause:** BODS reports reach the server 20–40 s late (fetched every 30 s), and buses were carried forward for only 30 s from the report. Most had used up their glide before being drawn.
- **Bus 0.3.1:** `motion {"mode":"heading", ..., "maxSeconds":90, "slowToStop":true}`.
  - `/status` adds `reportAgeMedianSeconds` (median age of reports at download, for tuning) and `busesWithSpeed`.
  - `tpa-status` shows both, plus `trackMapLoaded` and `trackError`.
- **HQ 4.1.3:** generic optional `slowToStop` on `heading`. Distance is `v·(t − t²/2T)`, easing from the reported speed to a standstill at `maxSeconds` (total = half the full-speed distance), so a bus on a bend doesn't run far off.
- **Tests:** a browser case where a slowToStop vehicle comes to rest; an engine assertion for the bus hint. HQ 50 OK (2 skipped), engines 55 OK, server 15 OK.
- **Next (proposed):** buses follow the actual roads (road shapes for the viewed area, cached), so their movement looks GPS-like.

# Update 4.1.4 (30 Oct, ~20:30)
**Owner** (asked whether to keep the dashed outline): "look normal please".
- Live in-between positions now look exactly like reported ones: solid outline.
- Honesty is kept in the words. The details box says "Estimated position · last reported hh:mm:ss", and the hover text and screen-reader label say "estimated position".
- The class was renamed `.est-pos`. The older `.estimated` dashed styling remains only for MOCK fixtures whose data quality is "estimated".
- Test: every icon draws a solid outline, and the moving jet's title still says "estimated position". HQ 50 OK (2 skipped).

# Update 4.1.5 (30 Oct, ~22:30)
**Owner:** "the buses are smooth but not continuous, they move in turns".

- **Reproduced.** If a device can only prepare an update every 2.5 s (busy map), each icon glided for the fixed 1 s, then stood still for 1.5 s (91 frames). That's "move, pause, move".
- **Fix:** each glide now lasts exactly the real time since the previous update (`--glide` on the marker layer, set only when it changes by >10%), so the animation never finishes early and waits. Verified: no run of 24+ still frames at a 2.5 s update gap.
  - `window.TPA_HQ_TEST_MIN_GAP` is a test-only hook used to imitate a slow device.
- Also:
  - A vehicle that is gliding keeps gliding (no reshuffle each second).
  - The budget is cut only on sustained slowness (avg >34 ms, cut ×0.85; recovers +10).
  - `slowToStop` keeps a steady speed for the first two-thirds and eases to a stop over the last third. Buses: steady 60 s, then 30 s ease.
  - Corrections to a new report slide over 2 s (was 1.2 s).
- **Tests:** test_5 (no taking turns: ≥90% of gliding icons keep gliding with 120 moving and a phone budget of 60) and test_6 (no pauses on a slow device: fails on 4.1.4, passes on 4.1.5). HQ 52 OK (2 skipped), engines 55 OK.
- **Load:** desktop 400 icons at 30%; slowed phone 150 shown / 60 gliding at 39%; 60 fps.

# Update 4.1.6 + bus engine 0.4.0 (30 Oct, ~23:30): buses follow the roads
**Owner** chose option 1, "buses follow the roads", after 4.1.5 was confirmed ("they do").

- **Road data choice** (checked before building):
  - Public OpenStreetMap Overpass: its manual says apps for the general public should not depend on the public instances. Rejected.
  - OpenFreeMap tiles: its terms forbid collecting data automatically (outside small offline downloads). Rejected.
  - BODS timetable route shapes: 1.3 GB a day, and it isn't known whether shapes are included. Rejected for now.
  - **Chosen: Ordnance Survey "OS Open Roads".** Every public road in Great Britain, as a routing network. OS OpenData under the Open Government Licence v3.0, free, no key, updated twice a year. Credited in the bus layer and the site credits: "Contains OS data © Crown copyright and database right".
- **Bus engine 0.4.0 (`road_map.py`):**
  - With `TPA_BUS_ROAD_MAP=download` (the server sets it), a low-priority helper process asks the OS Downloads API which file is current. It downloads the whole-GB Shapefile (or the GeoPackage), checks its md5, and builds `/var/lib/tpa/bus/roads.sqlite`: 1.4 km tiles, roads cut into pieces of 400 m or less. The ~1 GB download is deleted afterwards.
  - After that, it re-checks with one tiny request per week, and rebuilds only when OS publishes a new edition.
  - Between reports, a bus is matched to the road it is on: within 50 m, with direction within 75° of its heading. It is then walked along the road for its speed × 75 s (60 s steady + 30 s easing). At junctions it takes the most natural way: the smallest turn, preferring the same road name or number, never a U-turn. It stops at dead ends.
  - The engine sends `motion {"mode":"path", points, arriveAt, maxSeconds:90, speedMps, slowToStop:true}`. Without the map, or when a bus isn't clearly on a road, it falls back to 0.3's straight-line `heading`.
  - Path work is capped at 0.25 s per request; the rest is done by a background thread and appears on the next poll. Paths are cached per report.
  - `/status` adds `roadMap` (off/starting/checking/downloading/building/ready/unavailable), `roadMapDetail`, `roadMapError`, `roadPathsKnown`, `roadTilesLoaded`.
- **HQ 4.1.6:** `path` accepts an optional `speedMps` (+ `slowToStop`). It is travelled at that speed and eased to a stop like `heading`, instead of being spread evenly until `arriveAt`. Without `speedMps`, the old behaviour is unchanged (trains).
- **Tests:**
  - `test_bus_roads.py` (13): both OS formats give the same map; conversion accuracy; straight on at crossroads; follows a bend; reported 20 m off the road / 20° off; dead end; T-junction; found partway along a 3 km road; no road nearby → nothing; memory cap; engine downloads/builds/glides; weekly re-check fetches only the list; a damaged download is refused and buses still move; test addresses must be local.
  - `test_path_speed_4106.py`: stops 750 m along a 6.6 km path. **Fails on 4.1.5, passes on 4.1.6.**
  - Totals: engines 68 OK, HQ 53 OK (2 skipped), server 15 OK.
- **Load** (`v4/tests/load/bus_roads_scale_check.py`, a pretend city as dense as central London, 162k road links):
  - Build: 5 s here at 31 MB peak. All of Great Britain (~4 M links) is estimated at ~2 min here; allow 3–5× on the server.
  - 400 buses in one view: ~1.1 ms each.
  - Road memory capped at 150 tiles: ~70 MB worst case.

# Bus engine 0.4.1 (30 Oct, ~23:50): road work must never slow the bus download
**Server log after installing 0.4.0:** every BODS download took ~5 s before the update. During the map build, and while the owner viewed buses, downloads took 17–42 s and some timed out (`network-TimeoutError`), so the layer went "stale". Server load was low (0.10) and CPU steal was 0, so the cause was inside the bus engine.
- **Cause:** Python runs one thing at a time per program. Road work competed with reading the 26,000-bus file. At wide zoom, 400 buses needed ~3,600 road-map areas against a 150-area cache, so areas were reloaded from disk every 30 s (≈4 ms each here, ~20–40 ms on the server).
- **Fixes:**
  - Road paths only when zoomed in to town/city level (view ≤ 0.12° tall, ~13 km). Further out, the straight glide is used; it looks the same at that scale.
  - A street index inside each area (≈170 m cells): finding a bus's road went from 1.27 ms to 0.05 ms.
  - Each bus loads only the areas that could hold its road (usually 1, at most 4; was always 9). The cache is 250 areas.
  - **The download comes first:** while BODS is downloading, road work waits, and a map rebuild (child process) is paused with SIGSTOP/SIGCONT.
- **Measured:** the widest road view packed with 400 buses over the pretend dense city: first round 0.8 s / 181 area loads, next round 53 ms / 1 load. ~80 MB worst case.
- **Status:**
  - `/status` adds `lastFetchSeconds` and `roadPathMs`.
  - `tpa-status` shows them, plus each service's memory ("N MB of M MB").
- **Tests:** download priority (rebuild stopped during the download, then resumed; no road work inline during the download); zoomed out → straight glide. Engines 69 OK, HQ 53 (2 skipped; two timing-sensitive browser tests occasionally wobble under full-suite load and pass alone), server 15 OK.

# Bus engine 0.4.2 (31 Oct, ~00:50): owner "buses are not on the roads, they seem to be wandering about"
**Server check** (central Manchester, two looks 20 s apart):
- Of 184 bus entries, 54 were on roads, 56 straight and 74 not moving at all.
- 12 buses changed movement on the same report.
- Some buses gave no direction (operator BNGN). One said 159° but went 271°.

**Fixes:**
- **Road path ready first time.** After each download, every moving bus in areas viewed in the last 3 minutes is queued for its road path at once, instead of only when HQ asks. No more straight-then-road switch.
- **Speed from the last ~3 minutes of the bus's own reports** (distance / time, stops included): through a short stop the bus keeps its usual pace instead of freezing and then jumping; a bus parked for 3 minutes comes to a halt. One impossible hop (>40 m/s) cancels it.
- **Direction = where the bus really went** (its latest 10 m+ movement), unless the operator's direction agrees within 60°.
- Reports older than 5 minutes ("may have moved") are not carried forward.
- `/status` `lastView` counts why buses in the last view are on roads or not: `road`, `straight-pending`, `straight-no-road-match`, `straight-zoomed-out`, `still-no-speed`. `tpa-status` shows it.

**Tests:**
- Through a 45 s stop the speed holds at 300 m / 75 s; a wrong operator direction is replaced; a parked bus stops; an agreeing operator direction is kept.
- With no time allowed on request, the next report still arrives as a road path on all three looks; an old report stays put.
- Engines 71 OK. One unidentified engine-suite failure happened once (in a run without the map library) and did not recur in 4 full runs + 5 runs of the road tests.

# HQ 4.1.7 (31 Oct, ~11:00): icons move with the map; street map straight from OpenFreeMap
**Owner, 31 Oct morning:**
- "Load times for the map data are poor, especially tomtom, with a large amount of what feels like lag when moving the map or zooming".
- "when you zoom in or out the icons stay where they were until you stop and then move to their positions, giving the feeling of a mad scramble".
- Agreed order: zoom scramble + fast street map → vanishing vehicles → cheaper traffic tiles → (later) bigger server.

- **Zoom scramble: reproduced.** Filmed frame by frame on 4.1.6, an icon stood still for 16–17 frames of the zoom animation, then jumped 194 px.
  - Fix: on Leaflet's `zoomanim`, every icon is sent to its place in the new view, with Leaflet's own 0.25 s easing (`.marker-layer.zoom-anim`). Regular placement waits until `zoomend`; a guard ends it after 0.9 s whatever happens.
- **Dragging:** a plain drag now shifts the whole icon layer in one step (`shiftLayer`), instead of re-placing every icon every frame. Full re-placement happens at most 4×/s while dragging.
- **Street map:** with `TPA_HQ_BASEMAP=direct` (set on the server by install.sh), the browser loads the OpenFreeMap Liberty style and tiles straight from `tiles.openfreemap.org` (OpenFreeMap's intended use; "no limits on map views or requests").
  - The page's security policy adds exactly that host to `connect-src` and `img-src`; nothing else is loosened.
  - If the direct route fails before the map loads, the page switches once to the route through HQ (`/basemap/openfreemap/...`, unchanged).
  - `GET /hq-basemap` tells the page which route to use. The PC default is `via-hq` (unchanged).
- **Tests** (`test_zoom_drag_4107.py`, 4):
  - icons travel with the zoom (**fails on 4.1.6, passes on 4.1.7**);
  - icons move exactly with a drag (<3 px);
  - the route setting and security rules are correct;
  - direct → fallback switches once.
  - Totals: HQ 57 OK (2 skipped), engines 71 OK, server 15 OK.

# HQ 4.1.8 (31 Oct, ~14:20): moving icons stay on the map after a drag
**Owner, 13:50 (on 4.1.7):** "buses and trains work well when viewer is stationary, moving the map causes them to reset from the last on screen, not on map, position".

- **Reproduced.** After a slow drag, a gliding icon left its place on the map by 51 px (measured against a still icon), then crept back.
  - Cause: the last bit of a drag only shifted the icon layer, and nothing re-placed the icons when the drag ended. The next once-a-second update put the layer back *and* turned gliding on in the same step, so moving icons glided from their old screen spot.
- **Fix:**
  - Icons are re-placed the moment a drag or zoom ends.
  - Whenever the view has changed since the last placement, every icon is placed instantly. Gliding is only ever for the vehicle's own movement.
- **Tests:**
  - `test_moving_icons_do_not_jump_back_after_a_drag`: 5 drag styles (flick, slow, drag-and-hold, short, diagonal); worst 1–2 px. **Fails on 4.1.7 (51 px), passes on 4.1.8.**
  - The boat-highlight test now waits for HQ's next frame instead of checking in the same instant (6/6 alone, full suite OK).
  - HQ 58 OK (2 skipped), engines 71 OK, server 15 OK.

# HQ 4.1.9 (31 Oct, ~14:30): natural corrections when a new report arrives
**Owner, 14:04:** "The only time they wander off is when they get a new updated position."
**Server check (Manchester, 317 buses):**
- estimates were typically 93 m out (worst 10%: 230 m), 152 ahead and 136 behind;
- new reports landed a median 23 m from our road line.
HQ slid them there in a straight 2 s line, often backwards, at ~100 mph.

- **Estimate got ahead:** the icon **waits** where it is until the new estimate passes it, then carries on. Never backwards; after 45 s at most it falls back to a normal slide.
- **Estimate fell behind:** the icon keeps the vehicle's speed and **catches up gradually**: about 10 m/s extra on average, spread over 2–30 s (median 93 m → ~9 s).
  - All slides now shrink the *difference* from the estimate (`dlon/dlat`), so the speed is continuous: no stop-start.
- Sideways or unclear: the 2 s slide, as before.
- **Test:** `test_corrections_4109.py`, real Chromium, two vehicles at 50 m/s, a report 300 m behind and one 300 m ahead.
  - **4.1.8 fails** (backwards at 110–115 m/s; dash at 196–335 m/s).
  - 4.1.9 passes 5/5 (1-second windows: never backwards, waits then resumes, never under 25 or over 75 m/s).
  - HQ 59 OK (2 skipped), engines 71, server 15.

# HQ 4.1.10 (31 Oct, ~14:45): speed panel (measuring before speeding up)
**Owner, 14:21:** "Map needs to be almost instant, tomtom layer next, again that should be very fast, then the vehicles feeds need investigating."
- With `#speed` at the end of the address, a small panel shows, for that browser:
  - when the page arrived, plus the map library's load time and size;
  - when the street map was first drawn and fully complete, and how long it took to complete after the last move;
  - when traffic first showed, and how long the last view's traffic took;
  - for each vehicle layer, when it first showed, plus the last and average server answer times.
- Measuring only; nothing is sent anywhere.
- Without `#speed`, nothing changes. Test `test_speed_panel_4110.py`. HQ 60 OK (2 skipped).

# Bus engine 0.5.0 (31 Oct, ~21:30): buses follow OpenStreetMap's roads (the roads the map draws)
**Owner, 20:27:** "the map detail doesnt match where the buses are driving in a lot of cases". **20:28:** "yes, excellent idea".

- **Cause:** the street map is drawn from OpenStreetMap, but buses followed Ordnance Survey's lines, which OS describes as "the generalised alignment of the road carriageway" (simplified, with roundabouts as single points). There was also a few metres of grid-conversion offset.
- **Source:** Geofabrik's free OpenStreetMap shapefiles (ODbL, "© OpenStreetMap contributors"), roads layer only (`gis_osm_roads_free_1`: fclass, name, ref, oneway F/T/B), already in lon/lat.
  - The list comes from `index-v1-nogeom.json`. A region's own file is used if Geofabrik offers one, otherwise its sub-regions' files (England, Scotland, Wales).
  - Files are downloaded one at a time and each is deleted after reading. Rebuilt when 45 days old; checked daily.
  - Why not the .pbf: the standard tools (osmium-tool, pyosmium) can't be installed or tested in the workspace (Boost missing; PyPI unreachable today), and a pure-Python .pbf reader can't hold Great Britain's node index in the server's memory.
- **Map format 2:**
  - One-way flags; each road piece is stored in every tile it touches, so junctions are found anywhere along a road (OpenStreetMap roads meet part-way, not just at the ends).
  - OpenStreetMap lines are not simplified: any point may be a junction (found by test: simplifying removed junction points and roundabout exits).
  - Format 1 (the server's existing OS map) is still read.
- **Walker:**
  - meeting points anywhere; one-way streets obeyed (when snapping and turning);
  - a closed one-way loop = roundabout (flag 4): go round clockwise and leave by the exit nearest the direction 100 m back;
  - same road preferred (not on roundabouts); service roads avoided (+40); no U-turns; footpaths are not roads.
- **Engine:** `TPA_BUS_ROAD_SOURCE=osm` (server) prefers `roads-osm.sqlite`; the Ordnance Survey map stays as backup and is used until the OpenStreetMap one is built, or if a build fails. `/status` and `tpa-status` show `roadMapSource`.
- **Tests** (`test_bus_osm_roads.py`, 10): straight over a crossing part-way along; up a side road; one-way (refused wrong way, never turned into); roundabout (straight-on exit, round the north side); main road bend vs service road; footpath ignored; format-1 map still works; Geofabrik sub-region list (and a non-Geofabrik address refused); engine download/build/glide plus no re-download when fresh; a failed download keeps the OS backup with nothing half-built.
  - Engines 81 OK, HQ 60 (2 skipped), server 15.
- **Scale** (pretend dense city, OSM-style, 975k points): 3.4 µs/point to build (GB ≈ 40 M points ≈ 2 min here, ~1 h on the server, every 45 days); 0.2 ms per bus warm; 150 tiles ≈ 61 MB.

# Bus engine 0.5.1 (31 Oct, ~21:20): fetch only the roads part of Geofabrik's files
**Server, 20:49–20:59:** "downloading OpenStreetMap 0%" for 10 minutes, then `ValueError: download unexpectedly large`. Geofabrik offers England as **one** shapefile zip with every layer (buildings, land use, …), over the 3 GB safety limit.
- **RemoteZip:** reads the zip's table of contents from its end (HTTP range requests; ZIP64 supported for files over 4 GB), then downloads only `gis_osm_roads_free_1.shp` and `.dbf` (3 GB limit each), decompressing as it reads (`Inflate`, position-based: 40 MB in 8-byte reads takes 1.6 s).
- A server without partial downloads falls back to the whole file (12 GB limit; disk has 44 GB free).
- **Progress:** MB downloaded and % of rows built, per region ("england (1 of 3)"), at most every 5 s. The log gets one line per step and region.
- **Tests:**
  - partial download of a zip with a 3 MB other layer fetches <10% of the file;
  - the same with the ZIP64 format;
  - whole-file fallback when the server ignores ranges.
  - Engines OK (82), server 15.

# Bus engine 0.5.2 (31 Oct, ~21:45): patient, resumable downloads
**Server, 21:27:** "it got to 47% quickly then i left it a while". The status then showed `roadMapError=HTTPError: HTTP Error 503: Service Unavailable`: the first refusal ended the attempt and threw the progress away.
- `patiently()`: on 408/425/429/5xx, broken connections or reads cut short, wait 10 s → 30 s → 1 min → 2 min → 5 min (or the server's Retry-After, up to 15 min) and try again.
- Member downloads **resume** from the byte reached (Range from `start+got`). A 1 s pause before each data request. Geofabrik's list request is also retried.
- **Test:** a pretend server that answers 503 twice and drops two transfers half-way: completes, both troubles really occurred, resumed rather than restarted (<10% of the file sent per region). A server that always refuses gives up with "503" in the message, leaving nothing behind. Engines 83 OK, server 15.

# HQ 4.1.11–4.1.12 + road engine 0.2.0 + bus engine 0.5.3 (31 Oct, ~22:40)
**Owner 22:03:** bus engine on the OpenStreetMap road map (5,404,043 pieces; about 30 min from install to ready; bus engine 158 MB of 300).
**Owner 22:08:** "buses are still sporadic. smooth movement but the icons look weirdish, like they are re rendered. They need to stay the same. Also we need to get the load time way down on the tomtom layer, it's loading in chunks whener it likes".
**Owner 22:11:** buses "occasionally go off course ... it looks like theyre lost, sometimes moving a fair distance quickly. Icons could be smaller for buses and trains ... a zoom in to max or nearly max zoom could have larger icons".
**Owner 22:29:** "tomtom doesnt need to be as 'live' ... could update every ten minutes".

- **Icons kept:** an icon is built once (key: layer, badge, id). Only an aircraft's existing icon turns to its heading. *Test: the same page element across new reports; 4.1.10 rebuilt the bus icon.*
- **Traffic:**
  - Road engine 0.2.0 serves 512 px TomTom pictures (`tileSize` in the overlay; HQ uses `zoomOffset -1`): a quarter of the requests and of the TomTom allowance.
  - HQ loads them while the map moves (`updateWhenIdle:false`, `keepBuffer:3`).
  - A refresh loads the new layer invisibly underneath and swaps it in once complete. *Test, with 0.6 s slow pictures: 4.1.10 went from 31 visible pictures to 0 at refresh; 4.1.11 never blanks.*
  - Refresh every 10 minutes (`refreshSeconds 600`); the browser keeps pictures 10 min (`max-age=600`, address stamped by the refresh period); the engine cache is up to 10 min where TomTom's own max-age allows.
- **Lost-looking buses:**
  - Bus 0.5.3: at a junction where the two best ways on score within 20 points (T-junction, fork), the bus **waits at the junction** instead of guessing. Roundabouts still choose. The same road listed twice (overlapping region files, road pieces) counts as one way on (found by test: duplicates made it stop needlessly).
  - HQ 4.1.12: a moving vehicle found >60 m away and not along its way **fades in at its real place** instead of flying across buildings. It reappears one update back and glides on at once, so there is no pause (found by test_6). Vehicles without motion still slide as before (found by test_1).
- **Icon sizes:** bus and train icons are 70% of the old size at zoom <12.5, 82% to 14.5, 100% to 16.5, then 120%. Aircraft unchanged. *Test: 28 / 33 / 48 px.*
- Totals: HQ 63 OK (2 skipped), engines 83 OK, server 15 OK.

# HQ 4.1.13 + bus engine 0.5.4 (31 Oct, ~23:20): coming back to the tab; panning a couple of tiles
**Owner, 22:46:** "When i tab to another window and go back to the map, it always has a catch up, and thats when the buses and trains move about wrong. It does a similar thing when i move across the map a couple of tiles." **22:47:** "when you look out of a window at people walking around they dont freeze when you look back inside".
- Browsers pause drawing in hidden tabs (no page can override that); positions are always worked out from the time anyway.
- **HQ: coming back is a fresh start.** All corrections are dropped and every icon is placed at once where it should be now. The first new reports after returning are applied directly: within 20 m, nothing to do; further away, the icon fades in at its real place. *Test (page hidden 10 s, a report 300 m behind arrives meanwhile): 4.1.12 stood waiting 2.0 s after coming back; 4.1.13 carries on at once.*
- **HQ:** a new area's vehicles are asked for 0.3 s after moving (was 0.8 s).
- **Bus 0.5.4:**
  - road paths are prepared for a margin of half the view on every side of viewed areas;
  - a never-viewed area gets up to 1 s to work out road paths before answering (instead of straight lines first, road paths later).
  - *Test: a bus 500 m outside the view is ready; a new area answers with road paths with no other time allowed.*
- **Test fix:** `test_4_wheel_and_drag_work_over_icons` now holds still before letting go. Its occasional failures were Leaflet's flick carrying the map (and, correctly, the icon) on further than the mouse moved. 6/6 alone.
- Totals: HQ 64 OK (2 skipped), engines 84 OK, server 15 OK.

# HQ 4.1.14 (31 Oct, ~23:45): keep a hidden window "alive" (or pretending to be)
**Owner, 23:18 (on 4.1.13, Sheffield):** "went to another browser window, and when i went back i waited almost 30 seconds for them to move again, when they did their little speeding thing". **23:19:** "If we can keep a hidden windo alive or at least pretending to be i think it will make a huge difference".
- **Cause:** a covered window often isn't reported as "hidden"; the browser just stops drawing it. The page kept fetching, and each new report was compared with the icons' frozen positions: "wait" (up to 45 s) or "catch up" (the speeding).
- **Fixes:**
  - HQ keeps collecting reports while hidden (it used to pause).
  - It **notices for itself when it hasn't been drawing** (no icon placement for max(3 s, 2.5 update gaps) while things move, or a gap in animation frames) and treats drawing again as a fresh start: icons are placed where they should be now, and the first reports after are applied directly.
  - Reports arriving while not drawing are applied directly, never as corrections against frozen spots.
- **Gentler corrections overall:** wait at most 20 s; catch up at most 40% faster than the vehicle's own speed at any moment. Anything beyond that fades in at its real place.
- **Tests** (`test_return_4113.py`, 3 ways of being away: hidden tab, frozen page, covered window). The covered-window case is imitated exactly: drawing stopped, timers and network running, a report 900 m behind arriving meanwhile. **4.1.13 stood still ≥5 s after coming back; 4.1.14 carries on at once.**
  - HQ 66 OK (2 skipped), engines 84, server 15.

# HQ 4.1.15 + bus engine 0.5.5 (31 Oct, ~23:55): live-data credits on the map; buses always ready
**Owner, 23:47:** "no mention of bods data etc should that be there as i seem to be waiting a long time for the bus level to even load, and then longer for movement".
- **Credits:** the map's own credit line now adds "Live data: …" with the short names of the live layers being shown (from `site-config.json` `credits[].short`: ADSB.lol, BODS (DfT), Network Rail, TomTom); it wraps on narrow screens. *Test: the line names ADSB.lol · BODS (DfT) · TomTom and drops BODS when buses are switched off.*
- **Bus 0.5.5 always on** (`TPA_BUS_ALWAYS_ON=1` on the server, like the train engine): BODS is downloaded every 30 s while watched and every 60 s when nobody is looking. Speeds (which need recent pairs of reports) and positions are then ready the moment someone opens the map. Before, the engine stopped 3 minutes after the last viewer, so opening the map meant one download before buses appeared and two before they moved. *Test: `poll_decision()` timings.*
- HQ 67 OK (2 skipped), engines 85, server 15.

# Bus engine 0.5.6 (1 Nov, ~00:35): roads prepared for whole-city views
**Owner, 23:51:** "what if..... when a bus location is recieved, it maps at the closest zoom level automatically, then from launch it would already be in the right place when a user zoomed in?" **00:18:** "yes please ... this needs to LOOK right and that's something only a user can see."
- Every moving bus in the whole country every minute (~7,500 paths) is too much for the 1/8-processor server. Instead, views up to 0.6° tall (a whole city) are recorded too, and after each download their moving buses' road paths are prepared in the background.
  - Close-up views (with a half-view margin) come first, then city views nearest the middle.
  - At most 900 per download, worked through tile by tile so each piece of road map loads once.
  - Zoomed out, icons still use the simple straight glide (no visible difference at that scale). Zooming in finds paths ready.
- Road map cache raised to 350 tiles / 4 M points (room for a city). To check on the server: bus memory and `lastFetchSeconds` after viewing a city.
- *Test: a ~45 km city view; the 2 buses nearest the middle are prepared (limit 2); zooming in gives road paths with no time allowed for on-the-spot work.* Engines 86 OK, server 15.

# HQ 4.1.16 (1 Nov, ~00:45): buses and trains stand on the foot of their number board
**Owner, 00:28:** "one thing that will really help the human eye is knowing which part of the icon should be central to the road/track it is on, for this i propose the middle/bottom of the numberboard under the bus/train."
- For buses and trains the map point is now the **bottom-middle of the number board**, with the icon standing above it like a pin (`anchorOf()`: `translate(-50%, calc(var(--pin-s) * -46px - 8px))`). Selected icons grow around that point (`transform-origin`), so it stays on the road or track. Aircraft and others stay centred.
- *Test: an aircraft placed exactly at a bus's position. The aircraft's centre and the bus board's bottom-middle agree within 1.5 px at zoom 11, at zoom 13 selected, and at zoom 17 selected.* The fixed part of the offset was measured (6 → 8 px). Picture: `HQ-4.1.16-BUS-STANDS-ON-BOARD-FOOT.png` (red cross = the map point).
- HQ 68 OK (2 skipped).

# Bus engine 0.5.7 (1 Nov, ~01:30): buses drawn on their road, keeping left
**Owner, 01:02–01:03 (Birmingham, The Priory Queensway, screenshot):** two buses (94 and 51) drawn one on each side of the road; "both buses were travelling in the same direction". Server at the time: `lastView` road 0, still-no-speed 1, so they were drawn at their raw GPS reports, which are often 5–20 m out.
- **Every bus within 20 m of a road is now drawn on it**, whether moving, standing, or moving without a worked-out road path. A bus further from any road (a depot, a yard) is left where it was reported.
- **Keep left:** on two-way roads, buses are drawn 2 m to the left of the road line for their direction, as UK traffic keeps left. Two buses going the same way share a side; opposite directions are on opposite sides. One-way roads and roundabouts are drawn on their own line. Road paths are shifted the same way, and a bus's position is now the first point of its road path, so nothing jumps sideways when movement starts.
- Placements are worked out with the road paths (inline within the same time budget, otherwise in the background helper) and cached per report. `lastView` gains `placed-on-road`.
- *Test (`test_buses_going_the_same_way_are_drawn_on_the_same_side`): eastbound buses reported 8 m north and 8 m south of a road (moving and standing), one westbound, one in a field. **0.5.6: drawn 8 m north and 8 m south (opposite sides). 0.5.7: all eastbound 2.0 ± 0.5 m north; westbound 2 m south; the field bus untouched.***
