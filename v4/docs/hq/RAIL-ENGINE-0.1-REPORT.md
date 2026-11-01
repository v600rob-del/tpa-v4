# Train Engine 0.1 (Network Rail TRUST), with HQ 4.0.11

Built 30 Oct 2026 on the owner-confirmed aircraft version (`tpa-v4/air-engine-0.1` @ `f9d7696`). **HQ code is unchanged**; only `site-config.json` credits changed (Trains → "Live in test", NaPTAN credit, "shown at last station" note; official link left blank until Network Rail reply).

## Separate train engine — `v4/engines/rail/rail_engine.py`
Own process on 127.0.0.1:8784. Standard library only, including a minimal STOMP 1.2 client (no `stomp.py` install needed, unlike v3).
- Live feed: `publicdatafeeds.networkrail.co.uk:61618`, `/topic/TRAIN_MVT_ALL_TOC` (v3 settings). Login from `TPA_NR_USERNAME`/`TPA_NR_PASSWORD`; STOMP header values escaped (`\`, `:`, newlines). Never logged.
- Connects only while viewed and disconnects 10 min after the last view; heartbeats every 15 s; reconnects if silent >60 s. Backoff 15 s → 5 min. A login rejection → no retries for 30 min.
- Station positions: v3 method, ported. CORPUS (with login; S3 redirects never receive the login) STANOX→TIPLOC, matched exactly to NaPTAN `9100`+TIPLOC rail stations; conflicts dropped; ≥50 required. Cached 7 days at `%LOCALAPPDATA%\TPA\rail-engine\station-reference.json`.
- Movements (0003): train shown at the station of its last mapped report. Label "Train {headcode} · departed/arrived at/passed {station} · N min late/early/on time" (headcode from the TRUST id pattern, v3 rule). Up to 10 min → normal; 10–30 min → stale; >30 min dropped; terminated → gone after 5 min; cancellations (0002) removed. Unmapped locations are counted, never shown. Positions are never interpolated.
- Max 400 per view; views taller than 4° latitude → "Zoom in to see trains". While connected with nothing yet: "Listening · trains appear as they report at stations".

## Launcher
Adds hidden prompts for the Network Rail email and password (optionally saved, DPAPI: `nr-email.xml`, `nr-password.xml`; FORGET removes them). Start-Engine now takes a set of secrets per engine. Four engines start; each secret goes only to its engine.

## Tests (workspace, 30 Oct 2026)
- HQ 39 OK (2 skipped); engines 41 OK: road 11, bus 12, air 9, **rail 9**.
- Rail tests (pretend STOMP server + pretend CORPUS/NaPTAN): no login → no connection; exact-match station list and 7-day cache; login escaping; subscription; trains at last station with correct labels, badge, stale rules; jumping on the next report; cancellation; terminated; unmapped; malformed; wrong login reported and not retried; disconnects when nobody is looking; zoom notice; real browser with LIVE trains; engine crash affects only trains; no secrets in logs.
- Launcher (PowerShell 7): TomTom→road only, BODS→bus only, NR email+password→rail only; none in HQ or air; nothing printed; all stop together.

**Not proven here:** the real Network Rail feed, CORPUS download and the owner's login and subscription (no access from the workspace); real NaPTAN; Windows PowerShell 5.1. The owner's Network Rail account must still be subscribed to TRAIN_MVT_ALL_TOC (it was for v3).
**Licence:** Network Rail's reply is still awaited. Private PC testing only; going public waits for their answer.
NOT Golden.
