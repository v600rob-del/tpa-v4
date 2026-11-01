# Server kit 0.1 (HQ 4.0.12, train engine 0.2.1)

Built 30 Oct 2026 on `tpa-v4/rail-engine-0.2` @ `ec94a87`. The owner agreed the order (train fixes → server → Admin), and chose option A on 30 Oct: a public copy of the v4 code that the server downloads itself.

## Why it is built this way
- Claude's workspace cannot log in to Oracle machines, so the server **sets itself up**. A first-boot script clones the public code at a fixed commit, checks it is exactly that commit, and runs `install.sh`.
- Keys and logins never go into the creation script, Oracle metadata or chat. The owner types them hidden into `sudo tpa-setup`, run from Oracle Cloud Shell over SSH (key only).
- **Private while Network Rail haven't replied:** the site is behind a password (user `tpa`), and pages carry `noindex`.

## What changed in the code
- **HQ 4.0.12:** `TPA_HQ_PUBLIC_ORIGIN=https://host`. HQ still listens on 127.0.0.1 only and now also accepts that one Host name forwarded by the local front door. Bad values refuse to start. Unset means PC behaviour is unchanged.
- **Train engine 0.2.1:** `TPA_RAIL_ALWAYS_ON=1` listens 24/7 (server only). `/status` shows `alwaysOn`.
- **New `v4/server/`:**
  - `install.sh`: swap, code into `/opt/tpa/releases/<commit>` behind a `current` link, one locked-down user and one systemd service per engine plus HQ, Caddy 2.11.7 (sha512-checked), and ports 80/443 opened in the machine firewall.
  - `tpa-setup`: hidden prompts. Enter keeps a saved value. The website password is stored only as bcrypt.
  - `tpa-status`: plain-English health check with no secrets.
  - `Caddyfile`: automatic HTTPS, password, security headers.
  - `oracle-create-tpa-server.sh`: run in Cloud Shell.
- **Money guards in the Oracle script:**
  - Always Free shape `VM.Standard.E2.1.Micro` only, in the home region only.
  - Stops at 2 existing micros.
  - Stops if disks would exceed 200 GB.
  - Uses the Oracle free-allowance check (falls back to "which place offers the free shape").
  - Owner must type YES.
  - It creates only the server and the NSG `tpa-v4-web` (80/443/22), and never changes, stops or deletes anything existing.
- **Site name:** `test.tpalive.co.uk`. The owner adds one Cloudflare DNS record (grey cloud). The free `sslip.io` service isn't on the Public Suffix List, so its shared certificate limit is often used up, which is why it isn't used.

## Evidence (workspace)
- **Fit check, national-size fake feeds:** 35,000 buses, 55,000-entry CORPUS, 25,000 train reports, 600 road tiles. Peak about 230 MB RAM in total; about 5% of one fast core. Script: `v4/tests/load/server_fit_check.py`.
- **Real install on an Ubuntu 24.04 machine with systemd** (a container built from this workspace's own Ubuntu 24.04, with Python 3.12 and Oracle's firewall rules copied in):
  - All 6 services active.
  - Each engine sees only its own key, cannot read the other key files or data folders, and nothing secret is logged.
  - The Network Rail password with quotes, backslash and `$` is kept exactly.
  - Ports 80/443 were inserted before Oracle's REJECT rule and saved.
  - The front door answers 401 without the password, 401 with a wrong password, and the HQ page/manifest with it. HTTP redirects to HTTPS. HSTS, noindex and the CSP are present.
  - Real Chromium through the front door: HQ 4.0.12 loads with no page errors. Layers honestly report their feeds unreachable (that machine has no internet).
  - After a reboot all services come back. After a train engine crash it restarts by itself and the others are unaffected.
  - In use, about 160 MB.
- **Tests:**
  - Oracle script against a pretend `oci`: 11 OK (happy path, existing servers untouched, 2-micro stop, 200 GB stop, YES required, wrong region, no allowance, limits fallback, re-run, missing key, launch failure).
  - HQ: 44 OK (2 skipped), including 5 new server-mode tests.
  - Engines: 46 OK (rail 15, including always-on).

**Not proven here:** the real Oracle API calls (`oci` flags were written from documentation and checked against a pretend tool), a real Let's Encrypt certificate, real feeds on the server, and the 1/8-CPU machine's speed. The first real run will show these, and the script stops with a plain message on any problem.
NOT Golden.
