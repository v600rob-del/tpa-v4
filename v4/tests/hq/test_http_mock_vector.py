"""Actual localhost HTTP handler + mocked map source, with zero internet calls."""
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
spec=importlib.util.spec_from_file_location('hq_vector_mock',Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
class LocalVectorResourceMock(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old_cache=s.CACHE_DIR;self.old_disable=s.DISABLE_MAP_NETWORK;s.DISABLE_MAP_NETWORK=False;s.CACHE_DIR=Path(self.tmp.name)
  s.FREQUENCY.clear();s.NEGATIVE_CACHE.clear();s.ERRORS.clear()
  self.http=s.ThreadingHTTPServer(('127.0.0.1',0),s.HQHandler)
  self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.url='http://127.0.0.1:'+str(self.http.server_port)
 def tearDown(self):
  self.http.shutdown();self.http.server_close();self.thread.join(timeout=3);s.DISABLE_MAP_NETWORK=self.old_disable;s.CACHE_DIR=self.old_cache;self.tmp.cleanup()
 def get(self,path):
  with urlopen(self.url+path,timeout=3) as r:return r.status,dict(r.headers),r.read()
 def test_mock_style_tiles_vendor_and_cache(self):
  calls=[]
  def fixture(url,limit):
   calls.append(url)
   if '/styles/liberty' in url:
    return json.dumps({'version':8,'sprite':'https://tiles.openfreemap.org/styles/liberty/sprite',
      'glyphs':'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
      'sources':{'openmaptiles':{'url':'https://tiles.openfreemap.org/planet'}}}).encode()
   if '/planet/1/1/1.pbf' in url:return b'\x1a\x00MOCK_PBF_NOT_A_REAL_MAP'
   if url.endswith('.js'):return b'window.maplibregl = {}; /* MOCK JS, NOT REAL LIBRARY */'
   if url.endswith('.css'):return b'.mock-map{}'
   raise AssertionError('Unexpected provider URL: '+url)
  with patch.object(s,'fetch_remote',side_effect=fixture):
   for path in ('/assets/vendor/maplibre-gl.js','/assets/vendor/maplibre-gl.css',
       '/basemap/openfreemap/styles/liberty','/basemap/openfreemap/planet/1/1/1.pbf'):
    status,headers,raw=self.get(path);self.assertEqual(status,200,path)
    self.assertNotIn(b'<html',raw.lower())
   status,headers,raw=self.get('/basemap/openfreemap/styles/liberty')
   data=json.loads(raw)
   self.assertTrue(data['sprite'].startswith(self.url+'/basemap/openfreemap/'))
   self.assertEqual(data['sources']['openmaptiles']['url'],self.url+'/basemap/openfreemap/planet')
  self.assertEqual(len(calls),4,'Style served from server cache second time')

if __name__=='__main__':unittest.main(verbosity=2)
