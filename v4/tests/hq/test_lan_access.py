"""Local-only integration checks for LAN tablet bridge; no actual tablet/Internet."""
import importlib.util
import socket
import http.client
import threading
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SRC=Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py'
spec=importlib.util.spec_from_file_location('hq_tablet_lan',SRC)
hq=importlib.util.module_from_spec(spec);spec.loader.exec_module(hq)

class TabletLanSecurity(unittest.TestCase):
    def test_exact_host(self):
        ip='192.168.1.12'
        self.assertEqual(hq.local_request_origin(f'{ip}:8773',8773,ip),f'http://{ip}:8773')
        for host in ('127.0.0.1:8773','localhost:8773',f'{ip}:8772',
                     f'{ip}:8773.evil.example','evil.example:8773'):
            self.assertIsNone(hq.local_request_origin(host,8773,ip),host)
    def test_same_private_subnet_only(self):
        self.assertTrue(hq.allowed_preview_client('192.168.1.50','192.168.1.12'))
        self.assertTrue(hq.allowed_preview_client('10.0.0.23','10.0.0.1'))
        for ip in ('192.168.2.50','8.8.8.8','127.0.0.1','not-an-ip'):
            self.assertFalse(hq.allowed_preview_client(ip,'192.168.1.12'))
        self.assertFalse(hq.allowed_preview_client('10.0.0.2','8.8.8.8'))
        self.assertTrue(hq.allowed_preview_client('127.0.0.1','127.0.0.1'))
        self.assertFalse(hq.allowed_preview_client('192.168.0.2','127.0.0.1'))
    def test_lan_address_selection_override(self):
        with patch.dict(hq.os.environ,{'TPA_HQ_LAN_IP':'192.168.5.44'}):
            self.assertEqual(hq.find_lan_ip(),'192.168.5.44')
        for bad in ('8.8.8.8','127.0.0.1','100.64.4.2','garbage'):
            with self.subTest(bad=bad),patch.dict(hq.os.environ,{'TPA_HQ_LAN_IP':bad}):
                with self.assertRaises(RuntimeError):hq.find_lan_ip()
    def test_real_lan_ip_http_and_tilejson_url(self):
        """Run actual HTTP bound to a local interface; fake only upstream bytes."""
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.connect(('1.1.1.1',80)) # chooses interface without sending traffic
            lan=sock.getsockname()[0]
        if not hq.is_rfc1918(hq.ipaddress.ip_address(lan)):
            self.skipTest('No RFC1918 private LAN interface in test machine')
        style={'version':8,'sources':{'osm':{'type':'vector','url':'https://tiles.openfreemap.org/planet/latest'}},
               'glyphs':'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf','layers':[]}
        tilejson={'tiles':['https://tiles.openfreemap.org/planet/20261025_080012_pt/{z}/{x}/{y}.pbf']}
        def fake_fetch(url,limit):
            if url.endswith('/styles/liberty'):return json.dumps(style).encode()
            if url.endswith('/planet/latest'):return json.dumps(tilejson).encode()
            if url.endswith('/planet/20261025_080012_pt/8/126/81.pbf'):return b'fake-pbf-test'
            raise ValueError('mock-only-resource')
        with tempfile.TemporaryDirectory() as tmp,patch.object(hq,'CACHE_DIR',Path(tmp)),patch.object(hq,'fetch_remote',fake_fetch):
            server=hq.ThreadingHTTPServer((lan,0),hq.HQHandler)
            thr=threading.Thread(target=server.serve_forever,daemon=True);thr.start()
            try:
                port=server.server_port
                def req(path,host=None):
                    con=http.client.HTTPConnection(lan,port,timeout=5)
                    con.request('GET',path,headers={'Host':host or f'{lan}:{port}'})
                    res=con.getresponse();data=res.read();status=res.status;con.close()
                    return status,data
                self.assertEqual(req('/')[0],200)
                self.assertEqual(req('/api/v4/manifest')[0],200)
                self.assertEqual(req('/assets/hq.js')[0],200)
                self.assertEqual(req('/admin')[0],404)
                self.assertEqual(req('/',host=f'{lan}:9999')[0],403)
                r,body=req('/basemap/openfreemap/styles/liberty')
                self.assertEqual(r,200);parsed=json.loads(body)
                self.assertEqual(parsed['sources']['osm']['url'],f'http://{lan}:{port}/basemap/openfreemap/planet/latest')
                r,body=req('/basemap/openfreemap/planet/latest')
                self.assertEqual(r,200);parsed=json.loads(body)
                self.assertEqual(parsed['tiles'][0],f'http://{lan}:{port}/basemap/openfreemap/planet/20261025_080012_pt/{{z}}/{{x}}/{{y}}.pbf')
                self.assertEqual(req('/basemap/openfreemap/planet/20261025_080012_pt/8/126/81.pbf'),(200,b'fake-pbf-test'))
                status,data=req('/hq-map-status');self.assertEqual(status,200)
                diag=json.loads(data)
                self.assertGreaterEqual(diag['resourceCounts']['vector'],1)
            finally:
                server.shutdown();server.server_close();thr.join(timeout=3)

if __name__=='__main__':unittest.main(verbosity=2)
