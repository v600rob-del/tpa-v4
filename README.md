# TPA v4 - live UK transport map (server code)

TPA (Trains, Planes and Automobiles (and Boats)) is a free, live map of UK transport.
This repository holds only the code the TPA server downloads and runs:

- `v4/hq` - the map page (HQ). It only shows what the engines send it.
- `v4/engines` - one separate program per transport type (road traffic, buses, aircraft, trains).
- `v4/server` - the self-installing server setup (Ubuntu 24.04) and its Oracle Cloud Shell script.

No keys, passwords or personal settings are stored here. They are typed in privately on the
server (`sudo tpa-setup`) and never committed.

Data credits are shown on the site. See also `v4/engines/rail/data/SOURCES.txt` for the
train timing-point locations, which are shared under CC BY-SA 4.0 with the attributions listed there.

Copyright (c) 2026 Rob Burnett. All rights reserved unless a file says otherwise.
