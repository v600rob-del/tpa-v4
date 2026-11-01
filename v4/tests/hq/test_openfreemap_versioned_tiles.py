"""Real OpenFreeMap FAQ URL-shape regression (mock bytes, NOT genuine street rendering).

OpenFreeMap serves /planet (TileJSON) and the TileJSON advertises
/planet/20261029_001001_pt/{z}/{x}/{y}.pbf, or /planet/latest/...
These URLs were rejected by the 4.0.5 proxy after MapLibre loaded the source.
"""
from pathlib import Path
import importlib.util
import json
import tempfile
import threading
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('tpa406',Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class VersionedOpenFreeMap(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old_cache=s.CACHE_DIR;s.CACHE_DIR=Path(self.tmp.name)
  s.ERRORS.clear();s.NEGATIVE_CACHE.clear();s.FREQUENCY.clear()
  for k in s.RESOURCE_COUNTS:s.RESOURCE_COUNTS[k]=0
  self.server=s.ThreadingHTTPServer(('127.0.0.1',0),s.HQHandler)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.base=f'http://127.0.0.1:{self.server.server_port}'
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
  s.CACHE_DIR=self.old_cache;self.tmp.cleanup()
 def get(self,path):
  with urlopen(self.base+path,timeout=4) as r:return r.status,r.headers,r.read()
 def test_live_form_tilejson_rewritten_and_tiles_accepted(self):
  version='20261029_001001_pt';raws=[]
  def mock_fetch(url,limit):
   raws.append(url)
   if url.endswith('/styles/liberty'):
    return json.dumps({'version':8,'sources':{'planet':{'type':'vector','url':'https://tiles.openfreemap.org/planet'}}}).encode()
   if url.endswith('/planet') or url.endswith('/planet/latest') or url.endswith('/planet/'+version):
    return json.dumps({'tilejson':'3.0.0','tiles':[f'https://tiles.openfreemap.org/planet/{version}/{{z}}/{{x}}/{{y}}.pbf','https://tiles.openfreemap.org/planet/latest/{z}/{x}/{y}.pbf']}).encode()
   if '.pbf' in url:return b'\x1a\x00MOCK_NOT_GENUINE_MAP_BYTES'
   raise AssertionError('Not expected: '+url)
  with patch.object(s,'fetch_remote',side_effect=mock_fetch):
   style=json.loads(self.get('/basemap/openfreemap/styles/liberty')[2])
   self.assertEqual(style['sources']['planet']['url'],self.base+'/basemap/openfreemap/planet')
   for meta in ['/planet','/planet/latest','/planet/'+version]:
    status,headers,body=self.get('/basemap/openfreemap'+meta)
    self.assertEqual(status,200,meta)
    self.assertIn('application/json',headers['Content-Type'])
    transformed=json.loads(body)
    self.assertTrue(all(v.startswith(self.base+'/basemap/openfreemap/planet/') for v in transformed['tiles']))
    for template in transformed['tiles']:
     tile=template.replace('{z}','12').replace('{x}','2038').replace('{y}','1290')
     status,headers,pbf=self.get(tile[len(self.base):])
     self.assertEqual(status,200,tile)
     self.assertIn('protobuf',headers['Content-Type'])
     self.assertTrue(pbf.startswith(b'\x1a\x00'))
  self.assertGreaterEqual(len(raws),6)
  d=json.loads(self.get('/hq-map-status')[2]);self.assertEqual(d['resourceCounts']['rejected'],0)
  self.assertGreaterEqual(d['resourceCounts']['vector'],2)
  self.assertGreaterEqual(d['resourceCounts']['tilejson'],3)
  self.assertNotIn('blocked-map-path',d['upstreamErrors'])
 def test_unrelated_provider_and_invalid_version_rejected(self):
  for p in ['/planet/not-a-valid-version/12/2038/1290.pbf',
            '/planet/latest/12/2038/1290.exe','/planet/20261029_001001_pt/../../secret.pbf',
            '/planet/latest/12/2038/1290.pbf/another']:
   with self.subTest(p=p):
    self.assertFalse(s.allowed_map_path(p))
  with self.assertRaises(HTTPError) as cm:self.get('/basemap/openfreemap/planet/not-a-valid-version/12/2038/1290.pbf')
  self.assertEqual(cm.exception.code,404)
 def test_official_url_forms_from_provider_faq(self):
  for p in ['/planet/latest','/planet/latest/9/276/167.pbf','/planet/20250507_001001_pt/9/276/167.pbf']:
   self.assertTrue(s.allowed_map_path(p),p)
  self.assertEqual(s.resource_kind('/planet/latest'),'tilejson')
  self.assertEqual(s.resource_kind('/planet/20250507_001001_pt/9/276/167.pbf'),'vector')

if __name__=='__main__':unittest.main(verbosity=2)
