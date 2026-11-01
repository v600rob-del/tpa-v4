# TPA v4 HQ — Stage A kickoff (CONTROL DESK)

**Date:** 2026-10-29 (UK)  
**Status:** IN DEVELOPMENT — HQ NOT APPROVED, NOT LOCKED, NOT DEPLOYED  
**Tracking:** https://github.com/v600rob-del/tpa-cloud-dev-lab/issues/38  
**Working branch:** `tpa-v4/hq-map` from `tpa-v4/integration`. Main and the live TPA v3 Oracle instance MUST remain unchanged.

## Immutable starting evidence

- Original unapproved two-page preview archive: `TPA-V4-HQ-STANDARD-AND-ADMIN-29-OCT-2026.zip`.
- Archive SHA-256: `ee5e32f686a0b44be7f11483d4f7380b3a6dc8a006ea28cf52ab7136f7acbb05`.
- Existing HQ `index.html`, `assets/hq.css`, `assets/hq.js` and all Admin preview files must be preserved as comparison references, not overwritten.
- Independent reproduction on 2026-10-29: preview's 24 individual file checksums all match; 11 Python local HTTP/unit tests pass. This is MOCK/PREVIEW evidence, **not** production role security, process isolation, a real street map or genuine transport operation.
- Source scope guard: 8 tests pass. Cross-lane rail→bus path is correctly rejected in local test, but path checks are **not** OS/service isolation proof.

## Stage A — HQ STANDARD ONLY (current authorisation)

1. Work in `v4/hq/**`, `v4/tests/hq/**`, `v4/docs/hq/**` on `tpa-v4/hq-map`.
2. Reproduce established HQ appearance/icons and standard-page layout. Do **not** put Admin assets, scripts or controls into standard HTML.
3. Replace geographic map placeholder with an actual UK pan/zoom street map; make Home return to Beverley, confirm mobile/tablet/touch behavior and target selection UI. Separate rendering from live supplier logic.
4. Browser must fetch only its own origin; map assets must eventually be served by TPA's authorised server. No external third-party scripts, fonts, imagery or direct tile URLs.
5. **Do not misuse OpenStreetMap's public tile service for bulk/offline fetching.** Evaluate a legally distributable self-hosted OSM-derived UK map data source or properly licensed equivalent, retain credits and a size/quota plan. Map source is still a decision, not a granted licence.
6. Keep five EMPTY sockets visible, no fabricated vehicles, no source API keys, no supplier connections and no vehicle-motion algorithms.
7. Record desktop and mobile screenshots, actual browser pan/zoom/Home tests, network requests/egress checks, performance/a11y results, exact source commit and clean rollback.
8. Do not change `v4/admin/**`, `v4/gateway/**`, `v4/contracts/**`, any engine, or legacy `app/**`. Contract/gateway modifications must be separately requested through Issue #38.

## Nonproduction test location

- Immediate candidate: local loopback-only preview, e.g. `127.0.0.1:8765`, built from an **isolated working directory**.
- Later integration proofs must run in a separate nonproduction lab with independent process identities/permissions; **not** on the live v3 Oracle site.
- This local target is NOT evidence of Oracle isolation.

## Subsequent gates (not authorised for cross-lane edits)

A. **HQ standard map + visual review** (now).  
B. Control Desk separately reviews versioned data contract + same-origin gateway fixtures and fault containment.  
C. Admin lane adds production-grade server-side RBAC and read-only diagnostics.  
D. Nonproduction OS permission, denied-write, crash, restart, rollback and gateway outage tests.  
E. Owner explicitly approves desktop/mobile HQ, security and integration; then and only then pin `TPA HQ GOLDEN` to exact checksum/SHA.  
F. Introduce engines individually; BUS proposed first, then remaining lanes only with explicit scope and genuine live-source acceptance.

## Planned branch / folder ownership

- Shared staging: `tpa-v4/integration`; integration changes require Control Desk review.
- HQ: `tpa-v4/hq-map` => `v4/hq/**`, `v4/tests/hq/**`, `v4/docs/hq/**`.
- Future, create only when their gates open: `tpa-v4/admin`, `tpa-v4/air`, `tpa-v4/sea`, `tpa-v4/rail`, `tpa-v4/bus`, `tpa-v4/road`.
- Shared gateway and contracts: separately reviewed branch/task, never a hidden spoke edit.

## Review block

No map implementation, actual browser test of a new candidate, contract validation, production RBAC, live-provider coverage, paid supplier use, actual process isolation or owner approval has yet been shown. This file authorises **only** the scoped HQ Stage A kickoff and states its test gates.

**Next HQ-lane instruction:** import a copy of the verified standard preview into `v4/hq/**` on the HQ branch; deliver a genuinely navigable same-origin UK basemap candidate with screenshots and test logs; leave original preview and Admin unchanged. Report SHA and tests to Issue #38.
