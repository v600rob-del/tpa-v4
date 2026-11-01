#!/bin/bash
# oracle-check-free.sh - LOOK ONLY. Run in Oracle Cloud Shell. Changes nothing, creates nothing, costs nothing.
#   bash oracle-check-free.sh
# Lists every server in the account (including the live v3 ones - only read, never touched), how much of the
# free 200 GB of disk is used, and how much of the free Ampere (Arm) allowance is left - to see whether a bigger
# free TPA v4 server is possible (owner, 1 Nov 2026 21:02: "I would like to investigate option 4").
# Always Free Ampere A1 (Oracle, from 15 Jun 2026): 2 OCPUs and 12 GB of memory in total.
set -uo pipefail
A1_FREE_OCPU=2
A1_FREE_GB=12
FREE_STORAGE_GB=200
T="${OCI_TENANCY:-}"
[[ "$T" == ocid1.tenancy.* ]] || { echo "Cannot find your Oracle account id (OCI_TENANCY). Are you in Oracle Cloud Shell?"; exit 1; }
js() { python3 -c "import json,sys
raw=sys.stdin.read().strip()
d=json.loads(raw) if raw else {'data': []}
$1"; }
ocij() { oci "$@" --output json 2>/dev/null; }

echo "== Servers in this account (read only) =="
raw="$(ocij compute instance list -c "$T" --all)" || { echo "  Could not read the list of servers - no verdict given. Nothing was changed."; exit 1; }
printf '%s' "$raw" | js '
rows = [i for i in d.get("data", []) if i.get("lifecycle-state") not in ("TERMINATED", "TERMINATING")]
ocpu = gb = 0.0
for i in rows:
    sc = i.get("shape-config") or {}
    o, m = sc.get("ocpus"), sc.get("memory-in-gbs")
    name, shape, state = i.get("display-name", "?"), i.get("shape", "?"), i.get("lifecycle-state", "?")
    print("  %-28s %-24s %5s OCPU %5s GB   %s" % (name, shape, "?" if o is None else o, "?" if m is None else m, state))
    if shape == "VM.Standard.A1.Flex":
        if o is None or m is None:
            print("AMPERE_UNKNOWN")
        ocpu += float(o or 0); gb += float(m or 0)
print("AMPERE_USED %g %g" % (ocpu, gb))
' > /tmp/tpa-check.$$ || { echo "  Could not understand the list of servers - no verdict given. Nothing was changed."; rm -f /tmp/tpa-check.$$; exit 1; }
grep -v '^AMPERE_' /tmp/tpa-check.$$
grep -q '^AMPERE_USED' /tmp/tpa-check.$$ || { echo "  Could not understand the list of servers - no verdict given. Nothing was changed."; rm -f /tmp/tpa-check.$$; exit 1; }
if grep -q '^AMPERE_UNKNOWN' /tmp/tpa-check.$$; then echo "  An Ampere server's size could not be read - no verdict given. Nothing was changed."; rm -f /tmp/tpa-check.$$; exit 1; fi
read -r _ A1_OCPU A1_GB <<< "$(grep '^AMPERE_USED' /tmp/tpa-check.$$)"
rm -f /tmp/tpa-check.$$

echo
echo "== Disk (free allowance: $FREE_STORAGE_GB GB in total) =="
USED=0
ADS="$(ocij iam availability-domain list -c "$T" | js 'print(" ".join(a["name"] for a in d.get("data", [])))')"
[[ -n "$ADS" ]] || { echo "  Could not read the disk sizes - no verdict given. Nothing was changed."; exit 1; }
for ad in $ADS; do
    b="$(ocij bv boot-volume list -c "$T" --availability-domain "$ad" --all | js 'print(int(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING"))))')"
    [[ "$b" =~ ^[0-9]+$ ]] || { echo "  Could not read the disk sizes - no verdict given. Nothing was changed."; exit 1; }
    USED=$((USED + b))
done
v="$(ocij bv volume list -c "$T" --all | js 'print(int(sum(v["size-in-gbs"] for v in d.get("data", []) if v["lifecycle-state"] not in ("TERMINATED","TERMINATING"))))')"
[[ "$v" =~ ^[0-9]+$ ]] || { echo "  Could not read the disk sizes - no verdict given. Nothing was changed."; exit 1; }
USED=$((USED + v))
echo "  in use: $USED GB · left: $((FREE_STORAGE_GB - USED)) GB (a new server needs about 50 GB)"

echo
echo "== Free Ampere (Arm) allowance: $A1_FREE_OCPU OCPUs and $A1_FREE_GB GB =="
LEFT_OCPU="$(python3 -c "print(max(0, $A1_FREE_OCPU - $A1_OCPU))")"
LEFT_GB="$(python3 -c "print(max(0, $A1_FREE_GB - $A1_GB))")"
echo "  used by existing Ampere servers: $A1_OCPU OCPUs, $A1_GB GB · left: $LEFT_OCPU OCPUs, $LEFT_GB GB"
over="$(python3 -c "print(1 if $A1_OCPU > $A1_FREE_OCPU or $A1_GB > $A1_FREE_GB else 0)")"
[[ "$over" == 1 ]] && echo "  NOTE: existing Ampere servers are ABOVE today's free allowance (Oracle halved it on 15 Jun 2026)."

echo
echo "== Verdict =="
if python3 -c "import sys; sys.exit(0 if $LEFT_OCPU >= 1 and $LEFT_GB >= 6 else 1)" && [[ $((FREE_STORAGE_GB - USED)) -ge 47 ]]; then
    echo "  A free Ampere server for TPA v4 looks POSSIBLE: up to $LEFT_OCPU OCPUs and $LEFT_GB GB, with disk room."
    echo "  (Oracle may still say 'out of capacity' in London for a while - that is common and can be retried.)"
else
    echo "  A free Ampere server for TPA v4 is NOT possible right now without changing the existing servers."
fi
echo "Nothing was changed."
