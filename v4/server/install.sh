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
caddy_expected_sha() {  # arch checksums-file -> that arch's sha512, only if the file holds our pinned amd64 one
    grep -qx "$CADDY_SHA512  caddy_${CADDY_VERSION}_linux_amd64.tar.gz" "$2" || return 1
    local h; h="$(awk -v f="caddy_${CADDY_VERSION}_linux_$1.tar.gz" '$2==f{print $1}' "$2")"
    [[ "$h" =~ ^[0-9a-f]{128}$ ]] || return 1
    echo "$h"
}
# Kit 4.14 (owner 3 Nov 14:41: "yes" to moving the bus engine onto the free Ampere server):
#  - /etc/tpa/role = bus      this machine runs ONLY the bus engine (set up by oracle-create-bus-server.sh); it listens
#                             on this machine's private address (/etc/tpa/bus-bind) for the main server only (/etc/tpa/bus-allow)
#  - /etc/tpa/remote.env      on the main server: TPA_REMOTE_BUS=<that private address> - HQ asks it for buses and the
#                             main server's own bus engine is switched off. Remove the file and update again to undo.
PRIVATE_IP='^(10(\.[0-9]{1,3}){3}|172\.(1[6-9]|2[0-9]|3[01])(\.[0-9]{1,3}){2}|192\.168(\.[0-9]{1,3}){2})$'
ROLE="${TPA_ROLE:-$(cat /etc/tpa/role 2>/dev/null || echo all)}"
[[ "$ROLE" == all || "$ROLE" == bus ]] || { echo "unknown role: $ROLE" >&2; exit 2; }
REMOTE_BUS="$(sed -n 's/^TPA_REMOTE_BUS=//p' /etc/tpa/remote.env 2>/dev/null | head -n1 || true)"
[[ -z "$REMOTE_BUS" || "$REMOTE_BUS" =~ $PRIVATE_IP ]] || { echo "TPA_REMOTE_BUS in /etc/tpa/remote.env is not a private address" >&2; exit 2; }
if [[ $ROLE == bus ]]; then
    ENGINES=(bus)
    BUS_BIND="$(cat /etc/tpa/bus-bind 2>/dev/null)"; BUS_ALLOW="$(cat /etc/tpa/bus-allow 2>/dev/null)"
    [[ "$BUS_BIND" =~ $PRIVATE_IP && "$BUS_ALLOW" =~ $PRIVATE_IP ]] || { echo "bus-only server: /etc/tpa/bus-bind and /etc/tpa/bus-allow must hold private addresses" >&2; exit 2; }
    REMOTE_BUS=""
elif [[ -s /etc/tpa/bus-bind && -s /etc/tpa/bus-allow ]]; then
    # kit 4.15: the former bus server now runs the whole site; its bus engine still answers the other (standby) server
    BUS_BIND="$(cat /etc/tpa/bus-bind)"; BUS_ALLOW="$(cat /etc/tpa/bus-allow)"
    [[ "$BUS_BIND" =~ $PRIVATE_IP && "$BUS_ALLOW" =~ $PRIVATE_IP ]] || { echo "/etc/tpa/bus-bind and /etc/tpa/bus-allow must hold private addresses" >&2; exit 2; }
    REMOTE_BUS=""
fi

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
# Server kit 4.11 (owner 1 Nov 17:44, "protect that at all costs"): each layer runs from its own link, so one
# layer can be updated or rolled back on its own (sudo tpa-update <layer> <commit>). A layer the owner has
# frozen (signed off) keeps the version it has; every other layer moves to this one.
FROZEN=/etc/tpa/frozen
is_frozen() { [[ -f "$FROZEN" ]] && grep -qx "$1" "$FROZEN"; }
install -d -m 0755 /opt/tpa/layers
for e in "${ENGINES[@]}"; do
    if is_frozen "$e" && [[ -L "/opt/tpa/layers/$e" ]]; then
        say "$e is FROZEN: kept at $(basename "$(readlink "/opt/tpa/layers/$e")")"
    else
        ln -sfn "$REL" "/opt/tpa/layers/$e.new" && mv -T "/opt/tpa/layers/$e.new" "/opt/tpa/layers/$e"
    fi
done
install -d -m 0755 /usr/local/sbin /usr/local/bin
install -m 0755 "$REL/v4/server/tpa-setup" /usr/local/sbin/tpa-setup
install -m 0755 "$REL/v4/server/tpa-status" /usr/local/sbin/tpa-status
install -m 0755 "$REL/v4/server/tpa-update" /usr/local/sbin/tpa-update
install -m 0755 "$REL/v4/server/tpa-trace" /usr/local/sbin/tpa-trace      # kit 4.12: trace one reported vehicle

# ---- users, data folders, settings folder ------------------------------------------
for e in "${ENGINES[@]}"; do
    id "tpa-$e" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "tpa-$e"
    install -d -m 0700 -o "tpa-$e" -g "tpa-$e" "/var/lib/tpa/$e"
done
if [[ $ROLE == all ]]; then
id caddy >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
install -d -m 0700 -o caddy -g caddy /var/lib/caddy
fi
install -d -m 0755 /var/lib/tpa
install -d -m 0700 -o root -g root /etc/tpa      # keys live here; only root (systemd) reads them
echo "$ROLE" > /etc/tpa/role     # kit 4.15: remembered both ways (a bus server can become the whole site)

# ---- Caddy (HTTPS + the test password) ---------------------------------------------
if [[ $ROLE == all ]]; then
if [[ "$(/usr/local/bin/caddy version 2>/dev/null | cut -d' ' -f1)" != "v$CADDY_VERSION" ]]; then
    say "downloading Caddy $CADDY_VERSION"
    tmp="$(mktemp -d)"
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    base="https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION"
    want="$CADDY_SHA512"
    if [[ "$arch" != amd64 ]]; then
        # kit 4.15 (owner 3 Nov 23:18: the whole site onto the Ampere server): Caddy's own fingerprint list, trusted only
        # because it contains the amd64 fingerprint pinned above
        curl -fsSL --retry 3 -o "$tmp/sums.txt" "$base/caddy_${CADDY_VERSION}_checksums.txt"
        want="$(caddy_expected_sha "$arch" "$tmp/sums.txt")" || { echo "Caddy fingerprint list not trusted - stopping" >&2; rm -rf "$tmp"; exit 1; }
    fi
    curl -fsSL --retry 3 -o "$tmp/caddy.tar.gz" "$base/caddy_${CADDY_VERSION}_linux_${arch}.tar.gz"
    echo "$want  $tmp/caddy.tar.gz" | sha512sum -c --quiet - || { echo "Caddy download fingerprint WRONG - stopping" >&2; rm -rf "$tmp"; exit 1; }
    tar -xzf "$tmp/caddy.tar.gz" -C "$tmp" caddy
    install -m 0755 "$tmp/caddy" /usr/local/bin/caddy
    rm -rf "$tmp"
fi
install -d -m 0755 /etc/tpa-web    # no secrets in here; the web front door reads it
install -m 0644 "$REL/v4/server/Caddyfile" /etc/tpa-web/Caddyfile
# kit 4.17 (owner 5 Nov 16:41): HQ unit gets TPA_HQ_DEFAULT_VIEW=user - the test site's main address is the user page, /full the full one
# kit 4.16 (owner 5 Nov 15:50): tpalive.co.uk shows a "coming soon" page until launch (no password, read-only files)
if [[ -d "$REL/v4/web/coming-soon" ]]; then rm -rf /opt/tpa/soon && cp -r "$REL/v4/web/coming-soon" /opt/tpa/soon && chmod -R a+rX /opt/tpa/soon; fi
fi

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
ExecStart=/usr/bin/python3 /opt/tpa/layers/$name/$script
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
declare -A BEFORE
for e in "${ENGINES[@]}"; do BEFORE[$e]="$(sha256sum "/etc/systemd/system/tpa-$e.service" 2>/dev/null | cut -d' ' -f1 || true)"; done   # kit 4.14: a brand-new server has none yet
BUS_ENV="Environment=TPA_BUS_MODE=bulk TPA_BUS_ALWAYS_ON=1 TPA_BUS_ROAD_MAP=download TPA_BUS_ROAD_SOURCE=osm TPA_BUS_TIMETABLE=download TPA_BUS_DATA_DIR=/var/lib/tpa/bus"
if [[ $ROLE == bus ]]; then
    unit bus "bus engine on its own Ampere server, for the main server only" TPA_BUS_ENGINE_PORT 8781 \
        "$BUS_ENV TPA_BUS_BIND=$BUS_BIND TPA_BUS_ALLOW=$BUS_ALLOW" 3G bus.env v4/engines/bus/bus_engine.py
else
unit road "road traffic engine (TomTom)" TPA_ROAD_ENGINE_PORT 8782 \
    "Environment=TPA_ROAD_DATA_DIR=/var/lib/tpa/road" 200M road.env v4/engines/road/road_engine.py
if [[ -n "${BUS_BIND:-}" ]]; then
unit bus "bus engine (BODS, whole-country mode), also answering the standby server" TPA_BUS_ENGINE_PORT 8781 \
    "$BUS_ENV TPA_BUS_BIND=$BUS_BIND TPA_BUS_ALLOW=$BUS_ALLOW" 3G bus.env v4/engines/bus/bus_engine.py
else
unit bus "bus engine (BODS, whole-country mode)" TPA_BUS_ENGINE_PORT 8781 \
    "$BUS_ENV" 500M bus.env v4/engines/bus/bus_engine.py
fi
unit air "aircraft engine (ADSB.lol)" TPA_AIR_ENGINE_PORT 8783 \
    "Environment=TPA_AIR_ALWAYS_ON=1" 150M air.env v4/engines/air/air_engine.py   # air.env: FlyItalyADSB key (11 Nov)
unit rail "train engine (Network Rail), listening all the time" TPA_RAIL_ENGINE_PORT 8784 \
    "Environment=TPA_RAIL_DATA_DIR=/var/lib/tpa/rail TPA_RAIL_ALWAYS_ON=1" 450M rail.env v4/engines/rail/rail_engine.py   # 450M: owner 12 Nov 2026 (was 300M; the feed was held back at the ceiling)
unit hq "HQ map page" TPA_HQ_PORT 8778 \
    "Environment=TPA_HQ_PARKED=sea TPA_HQ_LAN_TEST=0 TPA_HQ_OPEN_BROWSER=0 TPA_HQ_BASEMAP=direct TPA_HQ_DEFAULT_VIEW=user TMPDIR=/var/lib/tpa/hq
Environment=TPA_HQ_ENGINES=road=http://127.0.0.1:8782,bus=http://${REMOTE_BUS:-${BUS_BIND:-127.0.0.1}}:8781,air=http://127.0.0.1:8783,rail=http://127.0.0.1:8784" \
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
fi
systemctl daemon-reload
RUN=("${ENGINES[@]}")
if [[ $ROLE == bus ]]; then
    systemctl enable --quiet tpa-bus
elif [[ -n "$REMOTE_BUS" ]]; then          # buses come from their own server: this one's bus engine is switched off
    RUN=(road air rail hq)
    systemctl disable --now --quiet tpa-bus 2>/dev/null || true
    systemctl enable --quiet tpa-road tpa-air tpa-rail tpa-hq tpa-web
    say "buses come from their own server at $REMOTE_BUS (this server's bus engine is off)"
else
    systemctl enable --quiet tpa-road tpa-bus tpa-air tpa-rail tpa-hq tpa-web
fi
# restart every layer that moved; a frozen layer only if its service settings changed (it still runs its own code)
restart=()
for e in "${RUN[@]}"; do
    if ! is_frozen "$e"; then restart+=("tpa-$e")
    elif [[ "${BEFORE[$e]}" != "$(sha256sum "/etc/systemd/system/tpa-$e.service" | cut -d' ' -f1)" ]]; then
        say "$e is FROZEN but its service settings changed: restarting it (its code is unchanged)"; restart+=("tpa-$e")
    fi
done
[[ ${#restart[@]} -gt 0 ]] && systemctl restart "${restart[@]}"
[[ $ROLE == all && -f /etc/tpa/web.env ]] && systemctl restart tpa-web || true

# ---- this machine's own firewall (Oracle's Ubuntu image blocks everything but SSH) --
# The parts talk to each other on 127.0.0.1, so loopback must always be allowed.
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || { iptables -I INPUT 1 -i lo -j ACCEPT; say "allowed loopback"; }
open_port() {  # port [from-address]
    local rule=(-p tcp --dport "$1" -m state --state NEW -j ACCEPT)
    [[ -n "${2:-}" ]] && rule=(-p tcp -s "$2" --dport "$1" -m state --state NEW -j ACCEPT)
    if ! iptables -C INPUT "${rule[@]}" 2>/dev/null; then
        pos="$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')"
        if [[ -n "$pos" ]]; then iptables -I INPUT "$pos" "${rule[@]}"; else iptables -A INPUT "${rule[@]}"; fi
        say "opened port $1${2:+ for $2 only}"
    fi
}
if [[ $ROLE == bus ]]; then open_port 8781 "$BUS_ALLOW"; else open_port 80; open_port 443; [[ -n "${BUS_BIND:-}" ]] && open_port 8781 "$BUS_ALLOW"; fi
if [[ -d /etc/iptables ]]; then iptables-save > /etc/iptables/rules.v4; fi

echo "$COMMIT" > /var/lib/tpa/installed
if [[ $ROLE == bus ]]; then say "done: bus-only server, code $COMMIT installed"; elif [[ -f /etc/tpa/web.env ]]; then say "done: code $COMMIT installed (keys and settings kept)"; else say "done: code $COMMIT installed. Next: sudo tpa-setup"; fi
