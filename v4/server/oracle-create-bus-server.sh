#!/bin/bash
# Run INSIDE Oracle Cloud Shell (the >_ terminal icon at the top of cloud.oracle.com):
#     bash oracle-create-bus-server.sh <commit>             create the bus server and start its bus engine
#     bash oracle-create-bus-server.sh <commit> --connect   later (timetable ready): the main server uses it for buses
#     bash oracle-create-bus-server.sh <commit> --disconnect   undo: the main server runs its own bus engine again
#
# Kit 4.14 (owner, 3 Nov 2026 13:58 "Dont forget we have more processing power available"; 14:41 "yes"):
# ONE Always Free Ampere server "tpa-v4-bus" (VM.Standard.A1.Flex, 1 OCPU, 6 GB, 50 GB disk (Oracle minimum)) that runs only the bus
# engine - the hungriest part - for the main server "tpa-v4-server", over the private network.
#
# Money guards - it stops and changes nothing if any check fails:
#  - only the Always Free Ampere shape, only in your home region, only 1 OCPU and 6 GB;
#  - stops unless the free Ampere allowance (2 OCPUs + 12 GB since 15 Jun 2026) has 1 OCPU + 6 GB left;
#  - stops if the new 50 GB disk would take block storage over the free 200 GB;
#  - asks you to type YES before creating anything.
# It never changes, stops or deletes any existing server or disk (the live v3 site is only read). The only other
# thing it adds is a free network rule set "tpa-v4-bus": SSH (key only) and the bus engine's port 8781 from the
# main server's PRIVATE address only. Bus data key: copied from the main server to the new one inside a pipe -
# never shown on screen.
set -euo pipefail

COMMIT="${1:-}"
STEP="${2:-create}"
REPO="${TPA_REPO:-https://github.com/v600rob-del/tpa-v4.git}"
NAME=tpa-v4-bus
MAIN=tpa-v4-server
SHAPE=VM.Standard.A1.Flex
OCPUS=1
MEM_GB=6
A1_FREE_OCPU=2
A1_FREE_GB=12
NSG_NAME=tpa-v4-bus
DISK_GB=50
FREE_STORAGE_GB=200
KEY="$HOME/.ssh/tpa_v4_server"
TRIES="${TPA_CAPACITY_TRIES:-30}"
WAIT="${TPA_CAPACITY_WAIT:-60}"

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m  %s\n' "$*"; }
stop() { printf '\n\033[1;31mSTOPPED - nothing was changed by this step.\033[0m\n%s\nPlease copy this message to Claude.\n' "$*" >&2; exit 1; }
js()   { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
ERR="$(mktemp)"; trap 'rm -f "$ERR"' EXIT
ocij() {
    local out
    if ! out="$(oci "$@" 2>"$ERR")"; then
        stop "An Oracle command failed (oci $1 $2 $3): $(tr -s '\n' ' ' < "$ERR" | cut -c1-400)"
    fi
    [[ -n "${out//[[:space:]]/}" ]] || out='{"data":[]}'
    printf '%s\n' "$out"
}

[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || stop "Usage: bash oracle-create-bus-server.sh <40-character code version> [--connect|--disconnect|--whole-site]"
[[ "$STEP" == create || "$STEP" == --connect || "$STEP" == --disconnect || "$STEP" == --whole-site ]] || stop "Unknown step: $STEP"
command -v oci >/dev/null || stop "This must be run inside Oracle Cloud Shell (the oci tool is missing)."
T="${OCI_TENANCY:-}"
[[ "$T" == ocid1.tenancy.* ]] || stop "Cannot find your Oracle account id (OCI_TENANCY). Are you in Oracle Cloud Shell?"
[[ -f "$KEY" && -f "$KEY.pub" ]] || stop "The Cloud Shell login key $KEY is missing (it is made when the main server was created)."

say "1  Checking your Oracle account (read-only)"
HOME_REGION="$(ocij iam region-subscription list --tenancy-id "$T" | js 'print([r["region-name"] for r in d["data"] if r.get("is-home-region")][0])')"
REGION="${OCI_REGION:-$HOME_REGION}"
[[ "$REGION" == "$HOME_REGION" ]] || stop "Cloud Shell is set to $REGION but your home region is $HOME_REGION. Switch region (top right) to $HOME_REGION and run again."
INSTANCES="$(ocij compute instance list -c "$T" --all)"
echo "$INSTANCES" | js '
for i in d.get("data", []):
    if i["lifecycle-state"] not in ("TERMINATED", "TERMINATING"):
        sc = i.get("shape-config") or {}
        print("      existing: %-28s %-24s %s OCPU %s GB  %s" % (i["display-name"], i["shape"], sc.get("ocpus", "?"), sc.get("memory-in-gbs", "?"), i["lifecycle-state"]))'
find_id() { echo "$INSTANCES" | js "print(next((i['id'] for i in d.get('data', []) if i['display-name']=='$1' and i['lifecycle-state'] not in ('TERMINATED','TERMINATING')), ''))"; }
MAIN_ID="$(find_id "$MAIN")"
[[ -n "$MAIN_ID" ]] || stop "Cannot find the main TPA v4 server '$MAIN'."
vnic() {  # instance-id field
    ocij compute instance list-vnics --instance-id "$1" | js "print(next((v.get('$2') or '' for v in d['data']), ''))"
}
MAIN_PRIVATE="$(vnic "$MAIN_ID" private-ip)"; MAIN_PUBLIC="$(vnic "$MAIN_ID" public-ip)"; MAIN_SUBNET="$(vnic "$MAIN_ID" subnet-id)"
[[ "$MAIN_PRIVATE" =~ ^(10|172|192)\. && -n "$MAIN_PUBLIC" && -n "$MAIN_SUBNET" ]] || stop "Could not read the main server's network details."
ok "main server $MAIN: private $MAIN_PRIVATE, public $MAIN_PUBLIC"
SSHO=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes)
MAIN_SSH=(ssh "${SSHO[@]}" "ubuntu@$MAIN_PUBLIC")
BUS_ID="$(find_id "$NAME")"

if [[ "$STEP" == --disconnect ]]; then
    say "Switching the main server back to its own bus engine"
    "${MAIN_SSH[@]}" "sudo rm -f /etc/tpa/remote.env && sudo tpa-update $COMMIT" || stop "The main server did not switch back."
    say "Done: the main server runs its own bus engine again (it needs a few minutes to warm up). The bus server was not touched."
    exit 0
fi

if [[ "$STEP" == --whole-site ]]; then
    # kit 4.15 (owner 3 Nov 23:18: "I'd sooner get the ampere stuff done"): the WHOLE v4 test site on the Ampere server
    # (far stronger than the free micro). Its frozen bus engine is kept and still answers the micro, which stays a
    # working standby: switching back is pointing test.tpalive.co.uk at the micro again. Nothing on v3 is touched.
    [[ -n "$BUS_ID" ]] || stop "There is no Ampere server '$NAME'."
    BUS_PUBLIC="$(vnic "$BUS_ID" public-ip)"; BUS_SUBNET="$(vnic "$BUS_ID" subnet-id)"
    [[ -n "$BUS_PUBLIC" ]] || stop "The Ampere server has no public address."
    BUS_SSH=(ssh "${SSHO[@]}" "ubuntu@$BUS_PUBLIC")
    VCN="$(ocij network subnet get --subnet-id "$BUS_SUBNET" | js 'print(d["data"]["vcn-id"])')"
    NSG="$(ocij network nsg list -c "$T" --vcn-id "$VCN" --all | js "print(next((n['id'] for n in d['data'] if n['display-name']=='$NSG_NAME' and n['lifecycle-state']=='AVAILABLE'), ''))")"
    [[ -n "$NSG" ]] || stop "Cannot find the rule set '$NSG_NAME'."
    say "2  Letting web visitors in (ports 80 and 443) on the rule set $NSG_NAME"
    have="$(ocij network nsg rules list --nsg-id "$NSG" --all | js "print(' '.join(str(r.get('tcp-options',{}).get('destination-port-range',{}).get('min','')) for r in d['data'] if r.get('direction')=='INGRESS'))")"
    add=""
    for port in 80 443; do
        [[ " $have " == *" $port "* ]] && continue
        add="${add:+$add,}{\"direction\":\"INGRESS\",\"protocol\":\"6\",\"source\":\"0.0.0.0/0\",\"sourceType\":\"CIDR_BLOCK\",\"description\":\"TPA website\",\"tcpOptions\":{\"destinationPortRange\":{\"min\":$port,\"max\":$port}}}"
    done
    if [[ -n "$add" ]]; then ocij network nsg rules add --nsg-id "$NSG" --security-rules "[$add]" >/dev/null; ok "ports 80 and 443 open"; else ok "ports 80 and 443 already open"; fi
    say "3  Copying the keys, logins and test password from the main server (inside a pipe - never shown)"
    "${MAIN_SSH[@]}" "sudo sh -c 'cd /etc/tpa && tar -cf - \$(ls road.env rail.env web.env hq.env 2>/dev/null)'" \
        | "${BUS_SSH[@]}" "sudo tar -C /etc/tpa --no-same-owner -xf - && sudo chmod 600 /etc/tpa/*.env" || stop "The keys could not be copied."
    ok "keys copied"
    say "4  Installing the whole site on the Ampere server (buses stay frozen; 5-15 minutes)"
    "${BUS_SSH[@]}" "echo all | sudo tee /etc/tpa/role >/dev/null && sudo tpa-update $COMMIT" || stop "The install on the Ampere server did not finish (see above)."
    say "Finished. The whole test site now runs on the Ampere server too; visitors still go to the micro until one change:"
    echo "  Where tpalive.co.uk's addresses are managed, change the record  test.tpalive.co.uk  to:  $BUS_PUBLIC"
    echo "  (the micro is $MAIN_PUBLIC - changing it back is the way back). The certificate is fetched by itself."
    echo "  Health of the Ampere server:  ssh -i $KEY ubuntu@$BUS_PUBLIC sudo tpa-status"
    exit 0
fi

if [[ "$STEP" == --connect ]]; then
    [[ -n "$BUS_ID" ]] || stop "There is no bus server '$NAME' yet. Run without --connect first."
    BUS_PRIVATE="$(vnic "$BUS_ID" private-ip)"
    say "Checking the bus server answers the main server (private network)"
    st="$("${MAIN_SSH[@]}" "curl -fsS --max-time 5 http://$BUS_PRIVATE:8781/status" 2>/dev/null)" || stop "The main server cannot reach the bus engine at $BUS_PRIVATE:8781 yet."
    tt="$(printf '%s' "$st" | js 'print(d.get("timetable") or "")')"
    [[ "$tt" == ready || "${TPA_CONNECT_ANYWAY:-}" == 1 ]] || stop "The bus server's timetable is '$tt', not ready yet (it takes about an hour after creation). Try again later."
    ok "bus engine at $BUS_PRIVATE answers; timetable ready"
    say "Switching the main server to the bus server"
    "${MAIN_SSH[@]}" "echo TPA_REMOTE_BUS=$BUS_PRIVATE | sudo install -m 600 /dev/stdin /etc/tpa/remote.env && sudo tpa-update $COMMIT" \
        || stop "The main server did not switch (undo any time: bash oracle-create-bus-server.sh $COMMIT --disconnect)"
    say "Done: buses now come from the Ampere server. Undo any time: bash oracle-create-bus-server.sh $COMMIT --disconnect"
    exit 0
fi

if [[ -n "$BUS_ID" ]]; then
    ok "$NAME already exists - not creating another one"
else
    read -r A1_OCPU A1_GB <<< "$(echo "$INSTANCES" | js '
o = g = 0.0
for i in d.get("data", []):
    if i["shape"] == "VM.Standard.A1.Flex" and i["lifecycle-state"] not in ("TERMINATED", "TERMINATING"):
        sc = i.get("shape-config") or {}
        if sc.get("ocpus") is None or sc.get("memory-in-gbs") is None:
            o = g = 999
        o += float(sc.get("ocpus") or 0); g += float(sc.get("memory-in-gbs") or 0)
print(o, g)')"
    python3 -c "import sys; sys.exit(0 if $A1_OCPU + $OCPUS <= $A1_FREE_OCPU and $A1_GB + $MEM_GB <= $A1_FREE_GB else 1)" \
        || stop "The free Ampere allowance ($A1_FREE_OCPU OCPUs, $A1_FREE_GB GB) has $A1_OCPU OCPUs and $A1_GB GB in use - not enough left for $OCPUS OCPU and $MEM_GB GB."
    ok "free Ampere allowance: $A1_OCPU of $A1_FREE_OCPU OCPUs and $A1_GB of $A1_FREE_GB GB in use; $OCPUS OCPU and $MEM_GB GB more stays free"
    ADS="$(ocij iam availability-domain list -c "$T" | js 'print(" ".join(a["name"] for a in d["data"]))')"
    [[ -n "$ADS" ]] || stop "Could not read the availability domains."
    USED=0
    for ad in $ADS; do
        b="$(ocij bv boot-volume list -c "$T" --availability-domain "$ad" --all | js 'print(int(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING"))))')"
        USED=$(( USED + b ))
    done
    v="$(ocij bv volume list -c "$T" --all | js 'print(int(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING"))))')"
    USED=$(( USED + v ))
    (( USED + DISK_GB <= FREE_STORAGE_GB )) || stop "Disks already use ${USED} GB; adding ${DISK_GB} GB would go over the free ${FREE_STORAGE_GB} GB."
    ok "disk space: ${USED} GB used, ${DISK_GB} GB more stays within the free ${FREE_STORAGE_GB} GB"

    IMAGE="$(ocij compute image list -c "$T" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC --all | js 'print(next((i["id"] for i in d["data"] if "Minimal" not in i["display-name"] and "aarch64" in i["display-name"]), ""))')"
    [[ -n "$IMAGE" ]] || stop "Cannot find Oracle's Ubuntu 24.04 Arm (aarch64) image for the Ampere server."
    ok "Ubuntu 24.04 Arm image found"
    VCN="$(ocij network subnet get --subnet-id "$MAIN_SUBNET" | js 'print(d["data"]["vcn-id"])')"

    say "2  Ready to create the free Ampere bus server"
    echo "      name:   $NAME"
    echo "      type:   $SHAPE, $OCPUS OCPU, $MEM_GB GB (Always Free)"
    echo "      disk:   $DISK_GB GB (inside the free 200 GB)"
    echo "      runs:   only the bus engine, for $MAIN ($MAIN_PRIVATE) over the private network"
    echo "      code:   $REPO @ ${COMMIT:0:12}"
    echo "      Existing servers, disks and networks are not touched."
    read -r -p "  Type YES (capitals) to create it, anything else to stop: " answer
    [[ "$answer" == YES ]] || stop "You chose not to create it."

    NSG="$(ocij network nsg list -c "$T" --vcn-id "$VCN" --all | js "print(next((n['id'] for n in d['data'] if n['display-name']=='$NSG_NAME' and n['lifecycle-state']=='AVAILABLE'), ''))")"
    if [[ -z "$NSG" ]]; then
        NSG="$(ocij network nsg create -c "$T" --vcn-id "$VCN" --display-name "$NSG_NAME" --wait-for-state AVAILABLE | js 'print(d["data"]["id"])')"
        ocij network nsg rules add --nsg-id "$NSG" --security-rules "[
          {\"direction\":\"INGRESS\",\"protocol\":\"6\",\"source\":\"0.0.0.0/0\",\"sourceType\":\"CIDR_BLOCK\",\"description\":\"SSH from Cloud Shell (key only)\",\"tcpOptions\":{\"destinationPortRange\":{\"min\":22,\"max\":22}}},
          {\"direction\":\"INGRESS\",\"protocol\":\"6\",\"source\":\"$MAIN_PRIVATE/32\",\"sourceType\":\"CIDR_BLOCK\",\"description\":\"TPA bus engine, main server only\",\"tcpOptions\":{\"destinationPortRange\":{\"min\":8781,\"max\":8781}}}]" >/dev/null
        ok "rule set $NSG_NAME created (SSH, and port 8781 from $MAIN_PRIVATE only)"
    fi

    USERDATA="$(cat <<EOF | base64 -w0
#!/bin/bash
set -euo pipefail
exec >>/var/log/tpa-firstboot.log 2>&1
echo "TPA bus server first boot \$(date -u)"
install -d -m 0700 /etc/tpa
hostname -I | tr ' ' '\n' | grep -E '^(10|172|192)\.' | head -n1 > /etc/tpa/bus-bind
echo "$MAIN_PRIVATE" > /etc/tpa/bus-allow
for i in 1 2 3 4 5; do rm -rf /opt/tpa/src; git clone --quiet $REPO /opt/tpa/src && break; sleep 20; done
git -C /opt/tpa/src checkout --quiet --detach $COMMIT
[ "\$(git -C /opt/tpa/src rev-parse HEAD)" = "$COMMIT" ]
TPA_ROLE=bus bash /opt/tpa/src/v4/server/install.sh $COMMIT
EOF
)"
    META="$(python3 -c 'import json,sys; print(json.dumps({"ssh_authorized_keys": open(sys.argv[1]).read().strip(), "user_data": sys.argv[2]}))' "$KEY.pub" "$USERDATA")"

    say "3  Creating the server (Oracle often says 'out of capacity' for free Ampere servers: retrying for up to $(( TRIES * WAIT / 60 )) minutes)"
    BUS_ID=""
    for try in $(seq 1 "$TRIES"); do
        for ad in $ADS; do
            if BUS_ID="$(oci compute instance launch -c "$T" --availability-domain "$ad" --shape "$SHAPE" \
                    --shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEM_GB}" \
                    --display-name "$NAME" --image-id "$IMAGE" --subnet-id "$MAIN_SUBNET" --assign-public-ip true \
                    --nsg-ids "[\"$NSG\"]" --boot-volume-size-in-gbs "$DISK_GB" --metadata "$META" \
                    --wait-for-state RUNNING --max-wait-seconds 900 2>"$ERR" | js 'print(d["data"]["id"])')" && [[ -n "$BUS_ID" ]]; then
                break 2
            fi
            BUS_ID=""
            grep -qi 'capacity' "$ERR" || stop "Oracle did not create the server. Oracle said: $(tr -s '\n' ' ' < "$ERR" | cut -c1-400)"
        done
        printf '  try %s of %s: out of capacity everywhere - waiting %s s\n' "$try" "$TRIES" "$WAIT"
        sleep "$WAIT"
    done
    [[ -n "$BUS_ID" ]] || stop "Oracle stayed 'out of capacity' for free Ampere servers. Nothing was created - try again later (evenings and early mornings are often better)."
    ok "bus server created and running"
fi

BUS_PUBLIC="$(vnic "$BUS_ID" public-ip)"; BUS_PRIVATE="$(vnic "$BUS_ID" private-ip)"
[[ -n "$BUS_PUBLIC" && -n "$BUS_PRIVATE" ]] || stop "The bus server has no address yet."
ok "bus server: public $BUS_PUBLIC, private $BUS_PRIVATE"
BUS_SSH=(ssh "${SSHO[@]}" "ubuntu@$BUS_PUBLIC")

say "4  Waiting for the bus server to install TPA by itself (5-15 minutes)"
for i in $(seq 1 90); do
    if "${BUS_SSH[@]}" test -f /var/lib/tpa/installed 2>/dev/null; then ok "TPA installed (bus only)"; break; fi
    (( i == 90 )) && { "${BUS_SSH[@]}" sudo tail -n 25 /var/log/tpa-firstboot.log 2>/dev/null || true; stop "TPA did not finish installing in 15 minutes (log above)."; }
    printf '.'; sleep 10
done

say "5  Copying the bus data key from the main server (inside a pipe - never shown)"
"${MAIN_SSH[@]}" sudo cat /etc/tpa/bus.env | "${BUS_SSH[@]}" 'sudo install -m 600 -o root -g root /dev/stdin /etc/tpa/bus.env && sudo systemctl restart tpa-bus' \
    || stop "The bus data key could not be copied."
ok "key copied; bus engine restarted"

say "Finished step one. The bus server is downloading the bus timetable and road map (about an hour)."
echo "  Nothing has changed for the main server or the website yet."
echo "  In about an hour, connect it (this checks the timetable is ready first):"
echo "      bash oracle-create-bus-server.sh $COMMIT --connect"
echo "  Bus server health any time:  ssh -i $KEY ubuntu@$BUS_PUBLIC sudo tpa-status"
