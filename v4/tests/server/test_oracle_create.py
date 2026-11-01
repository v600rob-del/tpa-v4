"""Oracle Cloud Shell create script: money/safety guards, tested against a PRETEND
"oci" tool (no real Oracle account is touched). Every oci call is logged so the
tests can prove what would and would not be changed.
"""
import base64
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
SCRIPT = PACKAGE / 'v4/server/oracle-create-tpa-server.sh'
COMMIT = 'a' * 40
T = 'ocid1.tenancy.oc1..pretend'

FAKE_OCI = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
sc = json.load(open(os.environ['FAKE_SCENARIO']))
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(a) + '\n')
def out(x):
    # Like the real oci tool: an EMPTY list prints nothing at all (just a hint on stderr).
    if isinstance(x.get('data'), list) and not x['data']:
        sys.stderr.write('Query returned empty result, no output to show.\n'); sys.exit(0)
    print(json.dumps(x)); sys.exit(0)
for word in sc.get('fail_on', []):
    if ' '.join(a).startswith(word):
        sys.stderr.write('ServiceError: NotAuthorizedOrNotFound (pretend)\n'); sys.exit(1)
def arg(name):
    return a[a.index(name) + 1] if name in a else None
cmd = ' '.join(x for x in a[:4] if not x.startswith('-'))
if cmd.startswith('iam region-subscription list'):
    out({'data': [{'region-name': 'uk-london-1', 'is-home-region': True}]})
if cmd.startswith('compute instance list-vnics'):
    out({'data': [{'public-ip': '203.0.113.7'}]})
if cmd.startswith('compute instance list'):
    out({'data': sc['instances']})
if cmd.startswith('iam availability-domain list'):
    out({'data': [{'name': 'XyZ:UK-LONDON-1-AD-1'}, {'name': 'XyZ:UK-LONDON-1-AD-2'}]})
if cmd.startswith('bv boot-volume list'):
    out({'data': sc['boot'].get(arg('--availability-domain'), [])})
if cmd.startswith('bv volume list'):
    out({'data': sc.get('volumes', [])})
if cmd.startswith('network vcn list'):
    out({'data': [{'id': 'ocid1.vcn.pretend', 'display-name': 'DEEP-THOUGHT-42-VCN', 'lifecycle-state': 'AVAILABLE'}]})
if cmd.startswith('network subnet list'):
    out({'data': [{'id': 'ocid1.subnet.pretend', 'lifecycle-state': 'AVAILABLE', 'prohibit-public-ip-on-vnic': False}]})
if cmd.startswith('limits resource-availability get'):
    if sc.get('limits_error'): sys.exit(1)
    out({'data': {'available': sc['available'].get(arg('--availability-domain'), 0)}})
if cmd.startswith('compute shape list'):
    out({'data': [{'shape': 'VM.Standard.E2.1.Micro'}] if arg('--availability-domain').endswith('AD-2') else [{'shape': 'VM.Standard.A1.Flex'}]})
if cmd.startswith('compute image list'):
    out({'data': [{'id': 'img-min', 'display-name': 'Canonical-Ubuntu-24.04-Minimal-2026.10.01-0'},
                  {'id': 'img-arm', 'display-name': 'Canonical-Ubuntu-24.04-aarch64-2026.10.01-0'},
                  {'id': 'img-ok', 'display-name': 'Canonical-Ubuntu-24.04-2026.10.01-0'}]})
if cmd.startswith('network nsg list'):
    out({'data': sc.get('nsgs', [])})
if cmd.startswith('network nsg create'):
    out({'data': {'id': 'ocid1.nsg.pretend'}})
if cmd.startswith('network nsg rules'):
    out({'data': {}})
if cmd.startswith('compute instance launch'):
    if sc.get('launch_fails'): sys.exit(2)
    out({'data': {'id': 'ocid1.instance.new'}})
sys.stderr.write('PRETEND oci: unexpected call ' + ' '.join(a) + '\n'); sys.exit(9)
'''
FAKE_SSH = '#!/bin/bash\necho "ssh $*" >> "$FAKE_LOG.ssh"\nexit 0\n'
FAKE_GETENT = ('#!/bin/bash\n# Fails (like a name that does not exist yet) for the first FAKE_DNS_MISSES calls.\n'
               'n=$(cat "$FAKE_LOG.dns" 2>/dev/null || echo 0); echo $((n+1)) > "$FAKE_LOG.dns"\n'
               '[ "$n" -lt "${FAKE_DNS_MISSES:-0}" ] && exit 2\n'
               'echo "203.0.113.7     STREAM test.tpalive.co.uk"\n')
FAKE_KEYGEN = ('#!/bin/bash\n# Like Oracle Cloud Shell (FIPS mode): ed25519 refused, RSA allowed.\n'
               'while [ $# -gt 0 ]; do [ "$1" = -f ] && f=$2; [ "$1" = -t ] && t=$2; shift; done\n'
               '[ "$t" = ed25519 ] && { echo "ED25519 keys are not allowed in FIPS mode"; exit 1; }\n'
               'echo priv > "$f"; echo "ssh-$t AAAAPRETEND tpa" > "$f.pub"\n')


def micro(name, state='RUNNING', shape='VM.Standard.E2.1.Micro'):
    return {'id': 'ocid1.instance.' + name, 'display-name': name, 'shape': shape, 'lifecycle-state': state}


V3 = [micro('instance-20261027-2116', shape='VM.Standard.A1.Flex'), micro('TPA-SSH-RECOVERY', shape='VM.Standard.A1.Flex'),
      micro('DEEP-THOUGHT-42', state='TERMINATED')]
BOOT = {'XyZ:UK-LONDON-1-AD-2': [{'size-in-gbs': 50, 'lifecycle-state': 'AVAILABLE'}, {'size-in-gbs': 50, 'lifecycle-state': 'AVAILABLE'},
                                 {'size-in-gbs': 100, 'lifecycle-state': 'TERMINATED'}]}


class OracleCreate(unittest.TestCase):
    def run_script(self, scenario, answer='YES\n', region='uk-london-1', have_key=False, dns_misses=0):
        d = Path(tempfile.mkdtemp(prefix='tpa-oci-'))
        bin_ = d / 'bin'; bin_.mkdir(); home = d / 'home'; (home / '.ssh').mkdir(parents=True)
        for name, body in (('oci', FAKE_OCI), ('ssh', FAKE_SSH), ('getent', FAKE_GETENT), ('ssh-keygen', FAKE_KEYGEN)):
            (bin_ / name).write_text(body); (bin_ / name).chmod(0o755)
        (d / 'scenario.json').write_text(json.dumps(scenario))
        if have_key:   # a re-run: Cloud Shell kept its key from the first run
            (home / '.ssh/tpa_v4_server').write_text('priv'); (home / '.ssh/tpa_v4_server.pub').write_text('ssh-rsa AAAA tpa')
        log = d / 'calls.log'
        env = dict(os.environ, PATH=f'{bin_}:{os.environ["PATH"]}', HOME=str(home), OCI_TENANCY=T, OCI_REGION=region,
                   FAKE_SCENARIO=str(d / 'scenario.json'), FAKE_LOG=str(log), FAKE_DNS_MISSES=str(dns_misses), TPA_DNS_WAIT='0')
        r = subprocess.run(['bash', str(SCRIPT), COMMIT], input=answer, env=env, capture_output=True, text=True, timeout=60)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        ssh = (Path(str(log) + '.ssh').read_text() if Path(str(log) + '.ssh').exists() else '')
        return r, calls, ssh

    def changing(self, calls):
        return [c for c in calls if any(w in c for w in ('launch', 'create', 'add', 'update', 'delete', 'terminate', 'stop', 'action', 'remove'))]

    def base(self, **kw):
        sc = {'instances': list(V3), 'boot': BOOT, 'available': {'XyZ:UK-LONDON-1-AD-1': 0, 'XyZ:UK-LONDON-1-AD-2': 1}}
        sc.update(kw)
        return sc

    def test_happy_path_creates_only_the_free_server(self):
        r, calls, ssh = self.run_script(self.base())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        changes = self.changing(calls)
        self.assertEqual([c[:3] for c in changes], [['network', 'nsg', 'create'], ['network', 'nsg', 'rules'], ['compute', 'instance', 'launch']])
        launch = changes[-1]
        get = lambda k: launch[launch.index(k) + 1]
        self.assertEqual(get('--shape'), 'VM.Standard.E2.1.Micro')
        self.assertEqual(get('--boot-volume-size-in-gbs'), '50')
        self.assertEqual(get('--availability-domain'), 'XyZ:UK-LONDON-1-AD-2')
        self.assertEqual(get('--image-id'), 'img-ok')            # not Minimal, not ARM
        self.assertEqual(get('--display-name'), 'tpa-v4-server')
        self.assertEqual(json.loads(get('--nsg-ids')), ['ocid1.nsg.pretend'])
        meta = json.loads(get('--metadata'))
        boot = base64.b64decode(meta['user_data']).decode()
        self.assertIn(f'checkout --quiet --detach {COMMIT}', boot)
        self.assertIn(f'install.sh {COMMIT}', boot)
        self.assertNotRegex(boot.lower(), 'password|api_key|tomtom_api|bods_api|nr_user')   # no secrets in user-data
        self.assertTrue(meta['ssh_authorized_keys'].startswith('ssh-rsa'))
        self.assertIn('sudo TPA_SETUP_HOST=test.tpalive.co.uk tpa-setup', ssh)
        self.assertIn('Finished. Open https://test.tpalive.co.uk/', r.stdout)

    def test_never_touches_existing_servers(self):
        _, calls, _ = self.run_script(self.base())
        for c in calls:
            self.assertFalse(any('instance-20261027-2116' in x or 'RECOVERY' in x for x in c), c)
            self.assertFalse({'terminate', 'delete', 'update', 'action', 'stop'} & set(c), c)

    def test_stops_if_two_free_micro_servers_exist(self):
        sc = self.base(instances=V3 + [micro('other-1'), micro('other-2')])
        r, calls, _ = self.run_script(sc)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('free limit is 2', r.stderr)
        self.assertEqual(self.changing(calls), [])

    def test_stops_if_disks_would_exceed_free_200gb(self):
        boot = {'XyZ:UK-LONDON-1-AD-2': [{'size-in-gbs': 100, 'lifecycle-state': 'AVAILABLE'}, {'size-in-gbs': 47, 'lifecycle-state': 'AVAILABLE'}]}
        r, calls, _ = self.run_script(self.base(boot=boot, volumes=[{'size-in-gbs': 10, 'lifecycle-state': 'AVAILABLE'}]))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('would go over the free 200 GB', r.stderr)
        self.assertEqual(self.changing(calls), [])

    def test_nothing_created_unless_owner_types_yes(self):
        for answer in ('no\n', 'yes\n', '\n', ''):
            r, calls, _ = self.run_script(self.base(), answer=answer)
            self.assertNotEqual(r.returncode, 0, answer)
            self.assertEqual(self.changing(calls), [], answer)

    def test_wrong_region_stops(self):
        r, calls, _ = self.run_script(self.base(), region='eu-frankfurt-1')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('home region is uk-london-1', r.stderr)
        self.assertEqual(self.changing(calls), [])

    def test_no_free_allowance_stops(self):
        r, calls, _ = self.run_script(self.base(available={}))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.changing(calls), [])

    def test_limits_unreadable_falls_back_to_place_offering_free_shape(self):
        r, calls, _ = self.run_script(self.base(limits_error=True))
        self.assertEqual(r.returncode, 0, r.stderr)
        launch = self.changing(calls)[-1]
        self.assertEqual(launch[launch.index('--availability-domain') + 1], 'XyZ:UK-LONDON-1-AD-2')

    def test_rerun_with_existing_server_does_not_create_another(self):
        r, calls, ssh = self.run_script(self.base(instances=V3 + [micro('tpa-v4-server')]), answer='', have_key=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.changing(calls), [])
        self.assertIn('already exists', r.stdout)
        self.assertIn('tpa-setup', ssh)

    def test_rerun_without_cloud_shell_key_stops_plainly(self):
        r, calls, _ = self.run_script(self.base(instances=V3 + [micro('tpa-v4-server')]), answer='')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Cloud Shell key', r.stderr)
        self.assertEqual(self.changing(calls), [])

    def test_real_oracle_style_empty_lists_work(self):
        # 30 Oct real run: empty boot-volume lists in two places, no volumes, no rule sets yet.
        boot = {'XyZ:UK-LONDON-1-AD-2': [{'size-in-gbs': 47, 'lifecycle-state': 'AVAILABLE'}, {'size-in-gbs': 47, 'lifecycle-state': 'AVAILABLE'}]}
        r, calls, _ = self.run_script(self.base(boot=boot, volumes=[]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('94 GB used', r.stdout)
        self.assertEqual(self.changing(calls)[-1][:3], ['compute', 'instance', 'launch'])

    def test_a_failed_money_check_stops_instead_of_counting_zero(self):
        for word in ('bv boot-volume list', 'bv volume list', 'compute instance list', 'iam availability-domain list'):
            r, calls, _ = self.run_script(self.base(fail_on=[word]))
            self.assertNotEqual(r.returncode, 0, word)
            self.assertIn('An Oracle command failed', r.stderr, word)
            self.assertEqual(self.changing(calls), [], word)

    def test_rerun_after_rule_set_was_made_reuses_it(self):
        # 30 Oct second real run: the rule set was created, then the (ed25519) key failed.
        sc = self.base(nsgs=[{'id': 'ocid1.nsg.existing', 'display-name': 'tpa-v4-web', 'lifecycle-state': 'AVAILABLE'}])
        r, calls, _ = self.run_script(sc)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        changes = self.changing(calls)
        self.assertEqual([c[:3] for c in changes], [['compute', 'instance', 'launch']])
        launch = changes[-1]
        self.assertEqual(json.loads(launch[launch.index('--nsg-ids') + 1]), ['ocid1.nsg.existing'])

    def test_waits_while_site_name_does_not_exist_yet(self):
        # 30 Oct third real run: the lookup failed (name not visible yet) and the script quit silently.
        r, calls, ssh = self.run_script(self.base(instances=V3 + [micro('tpa-v4-server')]), answer='', have_key=True, dns_misses=2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('test.tpalive.co.uk points at 203.0.113.7', r.stdout)
        self.assertIn('tpa-setup', ssh)

    def test_launch_failure_is_reported_plainly(self):
        r, _, _ = self.run_script(self.base(launch_fails=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('out of capacity', r.stderr)


if __name__ == '__main__':
    unittest.main()
