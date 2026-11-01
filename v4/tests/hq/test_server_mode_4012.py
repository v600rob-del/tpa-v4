"""HQ 4.0.12 server mode: HQ still listens on 127.0.0.1 only, but accepts the one
public host name a local HTTPS proxy forwards (TPA_HQ_PUBLIC_ORIGIN), and nothing else.
Unset = the PC behaviour is unchanged (covered by all the older tests).
"""
import http.client
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
HQ = PACKAGE / 'v4/hq/serve_hq.py'
FIXTURES = PACKAGE / 'v4/tests/hq/fixtures/icon_review'
PUBLIC = 'tpa-test.example.org'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def request(port, path, host):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    c.putrequest('GET', path, skip_host=True)
    c.putheader('Host', host)
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


class ServerMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, TPA_HQ_PORT=str(cls.port), TPA_HQ_LAN_TEST='0', TPA_HQ_PARKED='sea',
                   TPA_HQ_FIXTURE_DIR=str(FIXTURES), TPA_HQ_DISABLE_MAP_NETWORK='1', TPA_HQ_OPEN_BROWSER='0',
                   TPA_HQ_PUBLIC_ORIGIN='https://' + PUBLIC)
        cls.proc = subprocess.Popen([sys.executable, str(HQ)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                if request(cls.port, '/api/v4/manifest', f'127.0.0.1:{cls.port}')[0] == 200:
                    break
            except OSError:
                time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    def test_public_host_from_proxy_is_served(self):
        status, body = request(self.port, '/', PUBLIC)
        self.assertEqual(status, 200)
        self.assertIn(b'<title>TPA HQ 4.', body)
        self.assertEqual(request(self.port, '/api/v4/manifest', PUBLIC)[0], 200)

    def test_other_host_names_still_refused(self):
        for host in ('evil.example.com', PUBLIC + ':443', 'x.' + PUBLIC, PUBLIC.upper() + 'X', ''):
            self.assertEqual(request(self.port, '/', host)[0], 403, host)

    def test_pc_address_still_works(self):
        self.assertEqual(request(self.port, '/', f'127.0.0.1:{self.port}')[0], 200)

    def test_still_listens_on_loopback_only(self):
        # Linux listening sockets (state 0A); the address is little-endian hex.
        listening = [line.split()[1] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                     if line.split()[3] == '0A' and line.split()[1].endswith(':%04X' % self.port)]
        self.assertEqual(listening, ['0100007F:%04X' % self.port])

    def test_bad_public_origin_refuses_to_start(self):
        for bad in ('http://plain.example.org', 'https://a.b/path', 'https://localhost', 'https://x.org:8443', 'https://bad host.org'):
            env = dict(os.environ, TPA_HQ_PORT=str(free_port()), TPA_HQ_PUBLIC_ORIGIN=bad, TPA_HQ_OPEN_BROWSER='0')
            r = subprocess.run([sys.executable, str(HQ)], env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn('TPA_HQ_PUBLIC_ORIGIN', r.stderr, bad)


if __name__ == '__main__':
    unittest.main()
