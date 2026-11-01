# TPA v4 - live UK transport map (server code)

TPA (Trains, Planes and Automobiles (and Boats)) is a free, live map of UK transport.
This repository holds only the code the TPA server downloads and runs:

- `v4/hq` - the map page (HQ). It only shows what the engines send it.
- `v4/engines` - one separate program per transport type (road traffic, buses, aircraft, trains).
- `v4/server` - the self-installing server setup (Ubuntu 24.04) and its Oracle Cloud Shell script.

No keys, passwords or personal settings are stored here. They are typed in privately on the
server (`sudo tpa-setup`) and never committed.

## Server commands

| Command | What it does |
|---|---|
| `sudo tpa-status` | Health check: every part, memory, versions of each layer |
| `sudo tpa-update <commit>` | Update everything (layers that are frozen are kept) |
| `sudo tpa-update <layer> <commit>` | Update or roll back one layer only: `hq`, `bus`, `rail`, `air` or `road` |
| `sudo tpa-update freeze <layer>` / `unfreeze <layer>` | Protect a signed-off layer from any update |
| `sudo tpa-update show` | Which version each layer is running |
| `sudo tpa-trace '<vehicle id>'` | Everything the server knows about one vehicle (from the map's "Report this") |
| `python3 tpa-loadtest` | Run from Oracle Cloud Shell: how many viewers the site can serve (read-only) |

Data credits are shown on the site. See also `v4/engines/rail/data/SOURCES.txt` for the
train timing-point locations, which are shared under CC BY-SA 4.0 with the attributions listed there.

Copyright (c) 2026 Rob Burnett. All rights reserved unless a file says otherwise.
