#!/bin/bash
# TPA v4 server installer (Ubuntu 24.04, e.g. Oracle Always Free VM.Standard.E2.1.Micro).
#
# Run as root from the folder this file is in (the setup script does this):
#     bash install.sh <commit>
# Safe to run again (it only changes what is missing or different).
#
# What it does
#  - adds 1 GB of swap (the free machine has 1 GB of memory);
#  - copies this code to /opt/tpa/releases/<commit> and points /opt/tpa/current at it;
#  - one locked-down Linux user and one service per engine, plus HQ and the web front door;
#  - downloads Caddy (the HTTPS front door) from its official release, checking its fingerprint;
#  - opens ports 80 and 443 in the machine's own firewall.
# It never handles keys or passwords: "sudo tpa-setup" asks for those, hidden.
set -euo pipefail
umask 022

COMMIT="${1:-}"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: install.sh <40-character commit>" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "install.sh must run as root" >&2; exit 2; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ -f "$SRC/v4/hq/serve_hq.py" ]] || { echo "cannot find the TPA code next to install.sh" >&2; exit 2; }

CADDY_VERSION=2.11.7
CADDY_SHA512=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7
ENGINES=(road bus air rail hq)
say() { echo "[tpa-install] $*"; }

# ---- swap ------------------------------------------------------------------------
if ! swapon --show=NAME --noheadings | grep -q . && [[ ! -f /swapfile ]]; then
    say "adding 1 GB swap"
    if fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile; then
        grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    else
        say "WARNING: could not add swap (carrying on without it)"; swapoff /swapfile 2>/dev/null || true; rm -f /swapfile
    fi
fi

# ---- tools that should already be on Ubuntu --------------------------------------
for tool in python3 git curl tar sha512sum iptables; do
    command -v "$tool" >/dev/null || { say "installing missing tools"; apt-get update -qq; apt-get install -y -qq python3 git curl tar coreutils iptables; break; }
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ needed" >&2; exit 1; }

# ---- code (read-only for the engines) --------------------------------------------
REL="/opt/tpa/releases/$COMMIT"
if [[ ! -f "$REL/v4/hq/serve_hq.py" ]]; then
    say "installing code $COMMIT"
    mkdir -p "$REL.tmp"
    (cd "$SRC" && tar -c --exclude=.git v4) | tar -x -C "$REL.tmp"
    chown -R root:root "$REL.tmp"; chmod -R go-w "$REL.tmp"
    mv "$REL.tmp" "$REL"
fi
ln -sfn "$REL" /opt/tpa/current.new && mv -T /opt/tpa/current.new /opt/tpa/current
install -d -m 0755 /usr/local/sbin /usr/local/bin
install -m 0755 "$REL/v4/server/tpa-setup" /usr/local/sbin/tpa-setup
install -m 0755 "$REL/v4/server/tpa-status" /usr/local/sbin/tpa-status
install -m 0755 "$REL/v4/server/tpa-update" /usr/local/sbin/tpa-update

# ---- users, data folders, settings folder ------------------------------------------
for e in "${ENGINES[@]}"; do
    id "tpa-$e" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "tpa-$e"
    install -d -m 0700 -o "tpa-$e" -g "tpa-$e" "/var/lib/tpa/$e"
done
id caddy >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -m 0700 -o caddy -g caddy /var/lib/caddy
install -d -m 0755 /var/lib/tpa
install -d -m 0700 -o root -g root /etc/tpa      # keys live here; only root (systemd) reads them

# ---- Caddy (HTTPS + the test password) ---------------------------------------------
if [[ "$(/usr/local/bin/caddy version 2>/dev/null | cut -d' ' -f1)" != "v$CADDY_VERSION" ]]; then
    say "downloading Caddy $CADDY_VERSION"
    tmp="$(mktemp -d)"
    curl -fsSL --retry 3 -o "$tmp/caddy.tar.gz" \
        "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
    echo "$CADDY_SHA512  $tmp/caddy.tar.gz" | sha512sum -c --quiet - || { echo "Caddy download fingerprint WRONG - stopping" >&2; rm -rf "$tmp"; exit 1; }
    tar -xzf "$tmp/caddy.tar.gz" -C "$tmp" caddy
    install -m 0755 "$tmp/caddy" /usr/local/bin/caddy
    rm -rf "$tmp"
fi
install -d -m 0755 /etc/tpa-web    # no secrets in here; the web front door reads it
install -m 0644 "$REL/v4/server/Caddyfile" /etc/tpa-web/Caddyfile

# ---- services ----------------------------------------------------------------------
unit() {  # name description port-env port extra-environment memory-limit keys-file
    local name=$1 desc=$2 penv=$3 port=$4 extra=$5 mem=$6 keys=$7 script=$8
    cat > "/etc/systemd/system/tpa-$name.service" <<EOF
[Unit]
Description=TPA $desc
After=network-online.target
Wants=network-online.target

[Service]
User=tpa-$name
Group=tpa-$name
Environment=$penv=$port
$extra
${keys:+EnvironmentFile=-/etc/tpa/$keys}
ExecStart=/usr/bin/python3 /opt/tpa/current/$script
Restart=always
RestartSec=5
MemoryMax=$mem
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=/var/lib/tpa/$name
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
}
unit road "road traffic engine (TomTom)" TPA_ROAD_ENGINE_PORT 8782 \
    "Environment=TPA_ROAD_DATA_DIR=/var/lib/tpa/road" 200M road.env v4/engines/road/road_engine.py
unit bus "bus engine (BODS, whole-country mode)" TPA_BUS_ENGINE_PORT 8781 \
    "Environment=TPA_BUS_MODE=bulk TPA_BUS_ROAD_MAP=download TPA_BUS_ROAD_SOURCE=osm TPA_BUS_DATA_DIR=/var/lib/tpa/bus" 300M bus.env v4/engines/bus/bus_engine.py
unit air "aircraft engine (ADSB.lol)" TPA_AIR_ENGINE_PORT 8783 \
    "" 150M "" v4/engines/air/air_engine.py
unit rail "train engine (Network Rail), listening all the time" TPA_RAIL_ENGINE_PORT 8784 \
    "Environment=TPA_RAIL_DATA_DIR=/var/lib/tpa/rail TPA_RAIL_ALWAYS_ON=1" 300M rail.env v4/engines/rail/rail_engine.py
unit hq "HQ map page" TPA_HQ_PORT 8778 \
    "Environment=TPA_HQ_PARKED=sea TPA_HQ_LAN_TEST=0 TPA_HQ_OPEN_BROWSER=0 TPA_HQ_BASEMAP=direct TMPDIR=/var/lib/tpa/hq
Environment=TPA_HQ_ENGINES=road=http://127.0.0.1:8782,bus=http://127.0.0.1:8781,air=http://127.0.0.1:8783,rail=http://127.0.0.1:8784" \
    200M hq.env v4/hq/serve_hq.py

cat > /etc/systemd/system/tpa-web.service <<'EOF'
[Unit]
Description=TPA web front door (Caddy: HTTPS certificate + test password)
After=network-online.target tpa-hq.service
Wants=network-online.target
# Starts only after "sudo tpa-setup" has saved the site name and password.
ConditionPathExists=/etc/tpa/web.env

[Service]
User=caddy
Group=caddy
EnvironmentFile=/etc/tpa/web.env
Environment=XDG_DATA_HOME=/var/lib/caddy XDG_CONFIG_HOME=/var/lib/caddy
ExecStart=/usr/local/bin/caddy run --config /etc/tpa-web/Caddyfile --adapter caddyfile
Restart=always
RestartSec=5
MemoryMax=150M
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=/var/lib/caddy

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet tpa-road tpa-bus tpa-air tpa-rail tpa-hq tpa-web
systemctl restart tpa-road tpa-bus tpa-air tpa-rail tpa-hq
[[ -f /etc/tpa/web.env ]] && systemctl restart tpa-web || true

# ---- this machine's own firewall (Oracle's Ubuntu image blocks everything but SSH) --
# The parts talk to each other on 127.0.0.1, so loopback must always be allowed.
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || { iptables -I INPUT 1 -i lo -j ACCEPT; say "allowed loopback"; }
for p in 80 443; do
    if ! iptables -C INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT 2>/dev/null; then
        pos="$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')"
        if [[ -n "$pos" ]]; then iptables -I INPUT "$pos" -p tcp --dport "$p" -m state --state NEW -j ACCEPT
        else iptables -A INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT; fi
        say "opened port $p"
    fi
done
if [[ -d /etc/iptables ]]; then iptables-save > /etc/iptables/rules.v4; fi

echo "$COMMIT" > /var/lib/tpa/installed
if [[ -f /etc/tpa/web.env ]]; then say "done: code $COMMIT installed (keys and settings kept)"; else say "done: code $COMMIT installed. Next: sudo tpa-setup"; fi
