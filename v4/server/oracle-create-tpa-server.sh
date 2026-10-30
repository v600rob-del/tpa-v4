#!/bin/bash
# Run INSIDE Oracle Cloud Shell (the >_ terminal icon at the top of cloud.oracle.com):
#     bash oracle-create-tpa-server.sh <commit> [site-name]
#
# Creates ONE Always Free server "tpa-v4-server" (VM.Standard.E2.1.Micro) for TPA v4,
# then asks for the keys/logins and a private website password (all hidden).
# Safe to run again: if the server already exists it just carries on from where it got to.
#
# Money guards - it stops and changes nothing if any check fails:
#  - only the Always Free shape VM.Standard.E2.1.Micro, only in your home region;
#  - stops if two Always Free micro servers already exist (the free allowance);
#  - stops if the new 50 GB disk would take block storage over the free 200 GB;
#  - asks you to type YES before creating anything.
# It never changes, stops or deletes any existing server, disk or network. The only
# other thing it adds is a free network rule set "tpa-v4-web" (lets the web in on 80/443,
# attached to the new server only).
set -euo pipefail

COMMIT="${1:-}"
HOST="${2:-test.tpalive.co.uk}"
REPO="${TPA_REPO:-https://github.com/v600rob-del/tpa-v4.git}"
NAME=tpa-v4-server
SHAPE=VM.Standard.E2.1.Micro
VCN_NAME="${TPA_VCN_NAME:-DEEP-THOUGHT-42-VCN}"
NSG_NAME=tpa-v4-web
DISK_GB=50
FREE_STORAGE_GB=200
KEY="$HOME/.ssh/tpa_v4_server"

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m  %s\n' "$*"; }
stop() { printf '\n\033[1;31mSTOPPED - nothing was changed by this step.\033[0m\n%s\nPlease copy this message to Claude.\n' "$*" >&2; exit 1; }
js()   { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
ERR="$(mktemp)"; trap 'rm -f "$ERR"' EXIT
# Run one oci command and print its JSON. Oracle's tool prints NOTHING when a list is
# empty, so that becomes an empty list. Any real error stops the script (a money check
# must never be skipped because a command failed).
ocij() {
    local out
    if ! out="$(oci "$@" 2>"$ERR")"; then
        stop "An Oracle command failed (oci $1 $2 $3): $(tr -s '\n' ' ' < "$ERR" | cut -c1-400)"
    fi
    [[ -n "${out//[[:space:]]/}" ]] || out='{"data":[]}'
    printf '%s\n' "$out"
}

[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || stop "Usage: bash oracle-create-tpa-server.sh <40-character code version> [site-name]"
[[ "$HOST" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] || stop "Not a valid site name: $HOST"
command -v oci >/dev/null || stop "This must be run inside Oracle Cloud Shell (the oci tool is missing)."
T="${OCI_TENANCY:-}"
[[ "$T" == ocid1.tenancy.* ]] || stop "Cannot find your Oracle account id (OCI_TENANCY). Are you in Oracle Cloud Shell?"

say "1/7  Checking your Oracle account (read-only)"
HOME_REGION="$(ocij iam region-subscription list --tenancy-id "$T" | js 'print([r["region-name"] for r in d["data"] if r.get("is-home-region")][0])')"
REGION="${OCI_REGION:-$HOME_REGION}"
[[ "$REGION" == "$HOME_REGION" ]] || stop "Cloud Shell is set to $REGION but your home region is $HOME_REGION. Always Free servers must be in the home region. Switch region (top right) to $HOME_REGION and run again."
ok "home region $HOME_REGION"

INSTANCES="$(ocij compute instance list -c "$T" --all)"
echo "$INSTANCES" | js '
for i in d.get("data", []):
    if i["lifecycle-state"] != "TERMINATED":
        print("      existing: %-28s %-24s %s" % (i["display-name"], i["shape"], i["lifecycle-state"]))'
EXISTING="$(echo "$INSTANCES" | js "print(next((i['id'] for i in d.get('data', []) if i['display-name']=='$NAME' and i['lifecycle-state'] not in ('TERMINATED','TERMINATING')), ''))")"

if [[ -n "$EXISTING" ]]; then
    ok "$NAME already exists - not creating another one"
    INSTANCE="$EXISTING"
else
    MICROS="$(echo "$INSTANCES" | js "print(sum(1 for i in d.get('data', []) if i['shape']=='$SHAPE' and i['lifecycle-state'] not in ('TERMINATED','TERMINATING')))")"
    (( MICROS < 2 )) || stop "You already have $MICROS Always Free micro servers (the free limit is 2). Not creating another."
    ok "Always Free micro servers in use: $MICROS of 2"

    ADS="$(ocij iam availability-domain list -c "$T" | js 'print(" ".join(a["name"] for a in d["data"]))')"
    USED=0
    for ad in $ADS; do
        b="$(ocij bv boot-volume list -c "$T" --availability-domain "$ad" --all | js 'print(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING")))')"
        USED=$(( USED + b ))
    done
    v="$(ocij bv volume list -c "$T" --all | js 'print(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING")))')"
    USED=$(( USED + v ))
    (( USED + DISK_GB <= FREE_STORAGE_GB )) || stop "Disks already use ${USED} GB; adding ${DISK_GB} GB would go over the free ${FREE_STORAGE_GB} GB."
    ok "disk space: ${USED} GB used, ${DISK_GB} GB more stays within the free ${FREE_STORAGE_GB} GB"

    say "2/7  Finding the free network ($VCN_NAME) and a place with free capacity"
    VCN="$(ocij network vcn list -c "$T" --all | js "print(next((v['id'] for v in d['data'] if v['display-name']=='$VCN_NAME' and v['lifecycle-state']=='AVAILABLE'), ''))")"
    [[ -n "$VCN" ]] || stop "Cannot find the network '$VCN_NAME'."
    SUBNET="$(ocij network subnet list -c "$T" --vcn-id "$VCN" --all | js "print(next((s['id'] for s in d['data'] if s['lifecycle-state']=='AVAILABLE' and not s.get('prohibit-public-ip-on-vnic')), ''))")"
    [[ -n "$SUBNET" ]] || stop "The network '$VCN_NAME' has no public subnet."
    ok "network and public subnet found"

    AD=""; answered=0
    for ad in $ADS; do
        if avail="$(oci limits resource-availability get -c "$T" --service-name compute --limit-name vm-standard-e2-1-micro-count --availability-domain "$ad" 2>/dev/null | js 'print(int(d["data"].get("available") or 0))')"; then
            answered=1
            if (( avail > 0 )); then AD="$ad"; break; fi
        fi
    done
    if [[ -z "$AD" && $answered == 0 ]]; then   # limits not readable: use the first place that offers the free shape
        for ad in $ADS; do
            if oci compute shape list -c "$T" --availability-domain "$ad" --all 2>/dev/null | js "sys.exit(0 if any(s['shape']=='$SHAPE' for s in d['data']) else 1)"; then AD="$ad"; break; fi
        done
    fi
    [[ -n "$AD" ]] || stop "Oracle reports no free micro server allowance available in any availability domain."
    ok "free micro server place: $AD"

    IMAGE="$(ocij compute image list -c "$T" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC --all | js 'print(next((i["id"] for i in d["data"] if "Minimal" not in i["display-name"] and "aarch64" not in i["display-name"]), ""))')"
    [[ -n "$IMAGE" ]] || stop "Cannot find Oracle's Ubuntu 24.04 image for the free micro server."
    ok "Ubuntu 24.04 image found"

    say "3/7  Ready to create the free server"
    echo "      name:   $NAME"
    echo "      type:   $SHAPE (Always Free)"
    echo "      disk:   $DISK_GB GB (inside the free 200 GB)"
    echo "      place:  $AD"
    echo "      code:   $REPO @ ${COMMIT:0:12}"
    echo "      Existing servers, disks and networks are not touched."
    read -r -p "  Type YES (capitals) to create it, anything else to stop: " answer
    [[ "$answer" == YES ]] || stop "You chose not to create it."

    NSG="$(ocij network nsg list -c "$T" --vcn-id "$VCN" --all | js "print(next((n['id'] for n in d['data'] if n['display-name']=='$NSG_NAME' and n['lifecycle-state']=='AVAILABLE'), ''))")"
    if [[ -z "$NSG" ]]; then
        NSG="$(ocij network nsg create -c "$T" --vcn-id "$VCN" --display-name "$NSG_NAME" --wait-for-state AVAILABLE | js 'print(d["data"]["id"])')"
        ocij network nsg rules add --nsg-id "$NSG" --security-rules '[
          {"direction":"INGRESS","protocol":"6","source":"0.0.0.0/0","sourceType":"CIDR_BLOCK","description":"TPA web (HTTP, for the certificate and redirect)","tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
          {"direction":"INGRESS","protocol":"6","source":"0.0.0.0/0","sourceType":"CIDR_BLOCK","description":"TPA web (HTTPS)","tcpOptions":{"destinationPortRange":{"min":443,"max":443}}},
          {"direction":"INGRESS","protocol":"6","source":"0.0.0.0/0","sourceType":"CIDR_BLOCK","description":"SSH from Cloud Shell (key only)","tcpOptions":{"destinationPortRange":{"min":22,"max":22}}}]' >/dev/null
        ok "web rule set $NSG_NAME created (ports 80, 443, 22)"
    fi

    [[ -f "$KEY" ]] || ssh-keygen -q -t ed25519 -N '' -C tpa-cloud-shell -f "$KEY"
    USERDATA="$(cat <<EOF | base64 -w0
#!/bin/bash
set -euo pipefail
exec >>/var/log/tpa-firstboot.log 2>&1
echo "TPA first boot \$(date -u)"
for i in 1 2 3 4 5; do rm -rf /opt/tpa/src; git clone --quiet $REPO /opt/tpa/src && break; sleep 20; done
git -C /opt/tpa/src checkout --quiet --detach $COMMIT
[ "\$(git -C /opt/tpa/src rev-parse HEAD)" = "$COMMIT" ]
bash /opt/tpa/src/v4/server/install.sh $COMMIT
EOF
)"
    META="$(python3 -c 'import json,sys; print(json.dumps({"ssh_authorized_keys": open(sys.argv[1]).read().strip(), "user_data": sys.argv[2]}))' "$KEY.pub" "$USERDATA")"

    say "4/7  Creating the server (2-5 minutes)"
    INSTANCE="$(oci compute instance launch -c "$T" --availability-domain "$AD" --shape "$SHAPE" \
        --display-name "$NAME" --image-id "$IMAGE" --subnet-id "$SUBNET" --assign-public-ip true \
        --nsg-ids "[\"$NSG\"]" --boot-volume-size-in-gbs "$DISK_GB" --metadata "$META" \
        --wait-for-state RUNNING --max-wait-seconds 900 2>"$ERR" | js 'print(d["data"]["id"])')" \
        || stop "Oracle did not create the server (often 'out of capacity' for free servers - try again later). Oracle said: $(tr -s '\n' ' ' < "$ERR" | cut -c1-400)"
    ok "server created and running"
fi

IP="$(ocij compute instance list-vnics --instance-id "$INSTANCE" | js 'print(next((v.get("public-ip") or "" for v in d["data"]), ""))')"
[[ -n "$IP" ]] || stop "The server has no public address."
ok "server address: $IP"
[[ -f "$KEY" ]] || stop "The Cloud Shell key $KEY is missing, so Cloud Shell cannot log in to the server."
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes "ubuntu@$IP")

say "5/7  Waiting for the server to install TPA by itself (5-15 minutes)"
for i in $(seq 1 90); do
    if "${SSH[@]}" test -f /var/lib/tpa/installed 2>/dev/null; then ok "TPA installed"; break; fi
    (( i == 90 )) && { "${SSH[@]}" sudo tail -n 25 /var/log/tpa-firstboot.log 2>/dev/null || true; stop "TPA did not finish installing in 15 minutes (log above)."; }
    printf '.'; sleep 10
done

say "6/7  Point the site name at the server (one-time, in Cloudflare)"
current="$(getent ahostsv4 "$HOST" 2>/dev/null | awk 'NR==1{print $1}')"
if [[ "$current" != "$IP" ]]; then
    cat <<EOF
  In Cloudflare (dash.cloudflare.com) > tpalive.co.uk > DNS > Records > Add record:
      Type: A     Name: ${HOST%%.*}     IPv4 address: $IP
      Proxy status: click the orange cloud so it turns GREY ("DNS only")
  Then click Save. This window checks every 15 seconds and carries on by itself.
EOF
    for i in $(seq 1 120); do
        current="$(getent ahostsv4 "$HOST" 2>/dev/null | awk 'NR==1{print $1}')"
        [[ "$current" == "$IP" ]] && break
        (( i == 120 )) && stop "$HOST still does not point at $IP after 30 minutes. Run this script again once it does."
        printf '.'; sleep 15
    done
fi
ok "$HOST points at $IP"

say "7/7  Keys, logins and the private website password (all hidden)"
ssh -t -i "$KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$IP" sudo "TPA_SETUP_HOST=$HOST" tpa-setup

say "Finished. Open https://$HOST/  (user name: tpa, password: the one you chose)"
echo "  If the page does not open straight away, wait 2 minutes: the free HTTPS certificate is being issued."
echo "  Health check any time: in Cloud Shell run   ssh -i $KEY ubuntu@$IP tpa-status"
