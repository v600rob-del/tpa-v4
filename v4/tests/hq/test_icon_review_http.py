"""Real local HTTP test for static five-lane MOCK fixtures, NOT live data."""
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError

BASE=Path(__file__).resolve().parents[2]/'hq'
FIX=Path(__file__).resolve().parent/'fixtures/icon_review'
spec=importlib.util.spec_from_file_location('mock_review_server',BASE/'serve_hq.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class MockHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=mod.FIXTURE_DIR
        mod.FIXTURE_DIR=str(FIX)
        mod.DISABLE_MAP_NETWORK=True
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),mod.HQHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.origin=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3)
        mod.FIXTURE_DIR=cls.old
    def read(self,path):
        with urlopen(self.origin+path,timeout=3) as response:
            self.assertEqual(response.status,200)
            return json.loads(response.read())
    def test_real_mock_http_all_five_connections(self):
        manifest=self.read('/api/v4/manifest')
        self.assertTrue(manifest['mockIconReview'])
        self.assertFalse(manifest['transportFeedsEnabled'])
        self.assertEqual([x['id'] for x in manifest['components']],['air','sea','rail','bus','road'])
        self.assertTrue(all(x['status']=='ready' for x in manifest['components']))
        total=0
        for component in ['air','sea','rail','bus','road']:
            frame=self.read(f'/api/v4/components/{component}/snapshot?west=-1&south=53&east=0&north=54')
            self.assertEqual(frame['component'],component)
            self.assertIn('MOCK',frame['attribution'])
            total+=len(frame['entities'])
        self.assertEqual(total,9)
    def test_cannot_access_admin_or_outside(self):
        for path in ['/admin','/etc/passwd','/assets/admin.js','/api/v4/components/air/write']:
            with self.assertRaises(HTTPError):urlopen(self.origin+path,timeout=3)

if __name__=='__main__':unittest.main()
