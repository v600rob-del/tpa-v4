# Train Engine 0.2: memory + timing points, with HQ 4.0.11

Built 30 Oct 2026 on `tpa-v4/rail-engine-0.1` @ `16fae4b` (trains confirmed live on the owner's PC, 14:12; owner: "this needs to be a near to live website as possible").
**HQ code is unchanged.** Changed: the rail engine and its tests, the launcher wording, README-FIRST, and the Trains credit text in `site-config.json`. The road, bus and air engines are untouched.

## 1. Remembers the last 30 minutes
- Trains are saved to `%LOCALAPPDATA%\TPA\rail-engine\trains-last-30-min.json` at most once a minute, only when something changed. Writes are atomic (tmp + replace), and the file is also saved on a clean exit. A hard close loses at most about 1 minute.
- On start the saved trains are reloaded, keeping their **real report times**. Over 10 minutes old shows faded; over 30 minutes is dropped. Every field is re-checked: bad IDs, positions, headcodes or types are skipped, and a damaged file is ignored.
- `/status` reports `trainsRestoredAtStart`.

## 2. Junctions, loops and yards (timing points)
- In the first live run, 26% of reports were at places that are not stations. These are now placed using a bundled `v4/engines/rail/data/timing-points.csv`. It holds 11,018 STANOX locations and takes no download at runtime.
- Built by `v4/engines/rail/tools/build_timing_points.py` from the open YA TIPLOC list (Jnction/YA_Tiploc_List @ fb3c1e1). It keeps GB coordinates only and drops 33 codes whose sources disagree by more than 2 km. Per-row sources and licences are in `data/SOURCES.txt`: OGL (Network Rail, NaPTAN), CC BY 2.0 UK (RDG) and CC BY-SA 4.0 (woodpecker, GB Railway Data).
- **Stations from the CORPUS+NaPTAN method always win.** Timing points only fill gaps. They are labelled "passed {place}", e.g. "Train 9Z99 · passed Hessle Road Jn. · on time".
- `/status` adds `timingPointsKnown` and `movementsAtTimingPoints`. The attribution and credits mention the timing-point source.

## 3. Launcher wording
The Network Rail prompts now say "login email" and "password" instead of "key". The hint now reads: "you will see stars, or nothing at all. Both are normal". (Get-Key takes a 4th argument for the word shown.)

## Tests (workspace)
- Engines 45 OK (rail 13: +bundled file loads, +timing points, +restart memory incl. hard kill/old/damaged entries, +damaged memory file). HQ 39 OK (2 skipped).
- Launcher (PowerShell 7): four engines + HQ start; rail 0.2 reports 11,018 timing points; new wording shown.

**Not proven here:** the real Network Rail feed (no access from the workspace), how many real reports the timing points now catch (the owner's `/status` will show it), and Windows PowerShell 5.1.
**Before going public:** confirm the timing-point attribution wording (the YA repo's own LICENSE is GPL-3.0 for its code; the data rows carry the licences above), and wait for Network Rail's reply.
NOT Golden.
