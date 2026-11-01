# TPA v4 HQ 4.0.8 — separate home-LAN tablet test adaptation

**Status:** Development test package only. NOT HQ GOLDEN, NOT deployed.

- Original 4.0.8 ZIP unchanged and archived separately; this is an *additional* preview.
- HQ's original HTML/CSS/JavaScript, map style, historical Leaflet/MapLibre adapter, geography and component contracts are byte-identical to 4.0.8.
- Only HQ server binding/access rules, separate cache folder, launcher, LAN-specific tests, report and manifest differ.
- Binds one detected RFC1918 IPv4 interface on port 8773 rather than exposing 0.0.0.0 or the Internet. Rejects Host mismatches and clients outside same /24. Does not change firewall. Windows may request private-network firewall access.
- Style and TileJSON browser-facing URLs use absolute `http://<PC LAN IP>:8773/basemap/...` to avoid the MapLibre worker failure corrected in 4.0.8.
- No browser direct requests to OpenFreeMap; HQ server alone proxies licensed map resources. No live transports, keys, Admin code, or process/release changes.
- Test: `python -m unittest discover -s v4/tests/hq -p 'test_*.py' -q` => **26 PASS** (local dev machine); includes real HTTP bound to a private interface with mocked OpenFreeMap upstream, rejected fake Host, and same-origin versioned tile URL rewrite; old HQ unit/security and Chromium MOCK desktop/tablet/mobile tests unchanged.
- **Not yet tested:** a physical Android tablet connecting through the Windows Wi-Fi network, Windows Firewall rule, genuine OpenFreeMap render on that tablet, pinch/touch effectiveness, cost/licensing review, OS engine isolation or Owner HQ approval.
- Owner procedure: fresh extract on PC -> run `START-TPA-V4-HQ.bat` -> enter printed `http://<LAN IP>:8773/` on tablet connected to same home network. No need to disturb working 4.0.8 localhost version.
