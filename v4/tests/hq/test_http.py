"""Local HTTP tests. No live providers or Oracle touched; external map networking disabled."""
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import json
ROOT=Path(__file__).resolve().parents[2]/'hq'

class HQPreviewHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock=socket.socket();sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1];sock.close()
        env=dict(os.environ,TPA_HQ_PORT=str(cls.port),TPA_HQ_DISABLE_MAP_NETWORK='1')
        cls.proc=subprocess.Popen([sys.executable,str(ROOT/'serve_hq.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for i in range(40):
            try:
                with urlopen(f'http://127.0.0.1:{cls.port}/api/v4/manifest',timeout=.3):break
            except Exception:time.sleep(.1)
        else:raise RuntimeError('Preview did not start')
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=5)
    def get(self,path):
        try:
            with urlopen(f'http://127.0.0.1:{self.port}{path}',timeout=4) as r:return r.status,dict(r.headers),r.read()
        except HTTPError as e:return e.code,dict(e.headers),e.read()
    def test_standard_routes(self):
        for path in ('/','/index.html','/assets/hq.css','/assets/hq.js','/data/uk-geography.json'):
            with self.subTest(path=path):self.assertEqual(self.get(path)[0],200)
    def test_no_admin_exposure(self):
        for path in ('/admin','/admin.html','/assets/admin.js','/assets/admin.css','/server.py','/serve_hq.py','/data/../serve_hq.py','/contracts/component-v1.schema.json'):
            with self.subTest(path=path):self.assertEqual(self.get(path)[0],404)
    def test_5_empty_sockets(self):
        status,headers,payload=self.get('/api/v4/manifest')
        data=json.loads(payload);self.assertEqual(status,200)
        self.assertEqual(data['contractVersion'],'1.0')
        self.assertEqual([c['id'] for c in data['components']],['air','sea','rail','bus','road'])
        self.assertFalse(data['transportFeedsEnabled'])
        for id in ('air','sea','rail','bus','road'):
            s,h,b=self.get(f'/api/v4/components/{id}/snapshot?west=-1&south=52&east=1&north=54')
            self.assertEqual(s,503);self.assertEqual(json.loads(b)['health'],'not_installed')
    def test_local_basemap_and_no_outbound_in_test(self):
        status,headers,payload=self.get('/basemap/openfreemap/styles/liberty')
        self.assertEqual(status,502)
        status,headers,payload=self.get('/assets/vendor/maplibre-gl.js')
        self.assertEqual(status,502)
        status,headers,payload=self.get('/hq-map-status')
        self.assertTrue(json.loads(payload)['networkDisabled'])
        data=json.loads(self.get('/data/uk-geography.json')[2])
        self.assertGreater(len(data['coastlines']),10)
        self.assertGreater(len(data['land']),10)
        self.assertIn('GSHHG',data['source'])
    def test_approved_map_resources_only(self):
        for path in ('/basemap/openfreemap/planet/11/1/2.pbf',
                     '/basemap/openfreemap/styles/liberty/style.json',
                     '/basemap/openfreemap/styles/liberty/sprite@2x.png',
                     '/basemap/openfreemap/fonts/Noto%20Sans%20Regular/0-255.pbf'):
            self.assertEqual(self.get(path)[0],502,path)
        for path in ('/basemap/openfreemap/../../hq/serve_hq.py',
                     '/basemap/openfreemap/planet/../../file',
                     '/basemap/openfreemap/other-provider/script.js',
                     '/basemap/openfreemap/styles/unknown/style.json',
                     '/assets/vendor/unapproved.js',
                     '/basemap/tiles/11/1/2.png'):
            self.assertEqual(self.get(path)[0],404,path)
    def test_security_headers_and_read_only(self):
        status,h,payload=self.get('/')
        self.assertEqual(h['X-Content-Type-Options'],'nosniff')
        self.assertIn("connect-src 'self'",h['Content-Security-Policy'])
        self.assertIn("script-src 'self'",h['Content-Security-Policy'])
        self.assertIn("worker-src 'self' blob:",h['Content-Security-Policy'])
        self.assertEqual(self.get('/static/vendor/maplibre-gl.js')[0],404)
    def test_no_external_browser_dependencies(self):
        html=(ROOT/'index.html').read_text()
        self.assertNotIn('https://',html)
        self.assertNotIn('http://',html)
        self.assertNotIn('admin.js',html)
        self.assertNotIn('<iframe',html)
        js=(ROOT/'assets/hq.js').read_text()
        self.assertNotIn("style: 'https://tiles.openfreemap.org",js) # credits only; map style is same-origin
        # SVG DOM namespace is an identifier, not a browser network dependency.
        self.assertNotIn('http://',js.replace('http://www.w3.org/2000/svg',''))

if __name__=='__main__':unittest.main(verbosity=2)
