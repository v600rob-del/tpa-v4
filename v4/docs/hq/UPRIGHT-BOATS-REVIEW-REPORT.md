# Upright boats — HQ mock review

Owner request: keep boats upright; all subsequent owner testing on PC unless specifically necessary on tablet.

Changed only one rendering conditional in `v4/hq/assets/hq.js`: heading rotation now applies solely to aircraft, not ships. The exact heading data of both ships remain 80 and 225 degrees. This preserves selection, geographic position, existing map library and five data-only sockets.

Windows launcher: separate localhost-only port 8775. Prior 4.0.8 and icon review are preserved.

Mock-only Chromium desktop tests verify two upright ships with nonzero heading, aircraft still rotated, ship highlighting and Home. The development browser uses mock data/fallback; actual OpenFreeMap streets in this new review remain subject to owner PC check.
