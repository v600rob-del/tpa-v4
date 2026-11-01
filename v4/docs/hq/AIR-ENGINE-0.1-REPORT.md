# Aircraft Engine 0.1 (ADSB.lol), with HQ 4.0.11

Built 30 Oct 2026 on the owner-confirmed buses version (`tpa-v4/bus-engine-0.2` @ `33384ba`). **HQ code is unchanged**; only `site-config.json` credits changed (Aircraft → "Live in test"; the Planespotters line is removed until photos are built).

## Separate aircraft engine — `v4/engines/air/air_engine.py`
Own process on 127.0.0.1:8783. Standard library only.
- Source: `https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}` (the v3-proven endpoint). No key needed today; per the [adsblol/api README](https://github.com/adsblol/api), a key "will be required in the future" (obtained by feeding). `TPA_ADSBLOL_API_KEY` is read and sent as a header if set. **The header name is an assumption, to be confirmed when ADSB.lol publishes key details.**
- One circle covering the viewed area +15 nm (min 25, max 250 nm), at most every 20 s, only while viewed; a new uncovered area is fetched within ~3 s. Views taller than 9° latitude → "Zoom in to see aircraft".
- 401/403/429/451 → 5-minute pause (as v3); other errors back off.
- Rules (from v3): reports over 90 s old are dropped; over 30 s → stale (faded). Duplicates and invalid ids are ignored. Heading from `track` (or `true_heading`), so HQ rotates the icon. Label: callsign/registration · type · altitude or "on the ground" · registration.
- Credit: "Aircraft data: ADSB.lol (Open Database License)".

## Not yet included
- Aircraft photos (Planespotters, browser-on-click exception). This needs an HQ security-policy exception for Planespotters' image host and a referrer change, so it's a separate, owner-approved HQ step.
- Smooth "estimated" movement between reports (planned together with buses).

## Tests (workspace, 30 Oct 2026)
- HQ 39 OK (2 skipped); engines 32 OK: road 11, bus 12, **air 9**.
- Air tests: nothing fetched until viewed; labels and rules; heading; stale/drop; hostile text stays plain text; circle within 250 nm; zoom-out notice with no request; one request per poll; new area quickly; 429 → no requests for the pause; stops when nobody is looking; real browser shows rotated LIVE aircraft and details; engine crash affects only aircraft.
- Launcher (PowerShell 7): three engines start; TomTom key only in the road engine, BODS key only in the bus engine; no key in the air engine or HQ; nothing printed; all stop together.

**Not proven here:** the real ADSB.lol service (no access from the workspace).

Known looks item: a long label can overlap the × in the details box. Polish later.
NOT Golden.
