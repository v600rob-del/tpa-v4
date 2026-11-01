# HQ 4.0.9 — welcome screen, tip button, credits, parked ships

Built 30 Oct 2026 from the exact 4.0.8 socket-containment QA backup
(branch `tpa-v4/hq-4.0.8-socket-qa-backup`, commit `6d526c2`). Owner request: functionality first, polish later.

## What changed
- `v4/hq/data/site-config.json` (new): welcome text, tip link (empty = "link coming soon"), credits list. Data only; the page builds it with `textContent`, so no HTML from the file is ever injected. Only `https:` links are accepted.
- `v4/hq/assets/hq.js`: welcome dialog (shown once per browser, remembered in localStorage, reopenable from the sidebar), "About TPA" sidebar panel, credits dialog, Escape/backdrop close. **Parked socket support**: if the manifest marks a component `parked:true`, its toggle is locked off, it shows "Unavailable · coming soon" with chip "SOON", and any late data for it is ignored. This logic is identical for all five sockets.
- `v4/hq/serve_hq.py`: version 4.0.9; serves `/data/site-config.json`; `TPA_HQ_PARKED` setting (comma list) makes the manifest report a component as `unavailable` + `parked`, and its snapshot returns 503 with no data **even if a fixture exists**; optional `TPA_HQ_OPEN_BROWSER=1` opens the browser after the port is bound.
- `hq.css`: styles for the above. `index.html`: version labels only.
- `START-TPA.bat` (replaces `START-TPA-V4-HQ.bat`): port 8777, `TPA_HQ_PARKED=sea`, opens browser, plain-English messages.

## Unchanged by design
Map stack (Leaflet 1.9.4 + MapLibre 5.24.0 + bridge 0.1.4, OpenFreeMap Liberty), icons, five-socket contract v1.0, strict CSP, localhost-only serving, fixtures. Without the settings file or the parked setting, HQ behaves exactly as 4.0.8 (proven by test).

## Tests (real Chromium, workspace, 30 Oct 2026)
39 run, all OK, 2 skipped (same skips as 4.0.8): the original 34 unchanged plus 5 new in `test_welcome_parked_409.py`.
MOCK only: proves page behaviour, not live transport. Street map tiles could not be downloaded in the build workspace (network allowlist), so screenshots show the built-in fallback geography; on the owner's PC the OpenFreeMap streets load as in 4.0.8.

Screenshots: `HQ-4.0.9-WELCOME-DESKTOP.png`, `HQ-4.0.9-WELCOME-MOBILE.png`, `HQ-4.0.9-MAP-DESKTOP.png`, `HQ-4.0.9-CREDITS-DESKTOP.png`.

NOT Golden. Awaiting owner review.
