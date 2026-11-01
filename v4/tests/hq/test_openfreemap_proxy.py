"""MOCK transport tests: server path enforcement/rewrite/cache. Not a live map."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER=Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py'
spec=importlib.util.spec_from_file_location('hq_404_server',SERVER)
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class ProxyRules(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.CACHE_DIR;s.CACHE_DIR=Path(self.tmp.name)
  s.FREQUENCY.clear();s.ERRORS.clear();s.NEGATIVE_CACHE.clear()
 def tearDown(self):s.CACHE_DIR=self.old;self.tmp.cleanup()
 def test_allowlist(self):
  for p in ('/styles/liberty','/styles/liberty/style.json','/styles/liberty/sprite@2x.png',
            '/fonts/Noto Sans Regular,Arial Unicode MS Regular/0-255.pbf',
            '/planet/8/123/85.pbf','/planet'):
   self.assertTrue(s.allowed_map_path(p),p)
  for p in ('/styles/private','//evil.net','/styles/../admin','/planet/1/2/%2fetc',
            '/fonts/test/../../secret','/styles/liberty/script.js','/planet/0/0/0.js'):
   self.assertFalse(s.allowed_map_path(p),p)
 def test_style_rewriting_and_non_supplier_fields(self):
  obj={'version':8,'glyphs':'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
        'sprite':'https://tiles.openfreemap.org/styles/liberty/sprite',
        'sources':{'openmaptiles':{'url':'https://tiles.openfreemap.org/planet'}},
        'other':'https://example.org/link'}
  out=json.loads(s.rewrite_style(json.dumps(obj).encode()))
  self.assertEqual(out['sources']['openmaptiles']['url'],'/basemap/openfreemap/planet')
  self.assertEqual(out['other'],'https://example.org/link')
  self.assertEqual(out['glyphs'],'/basemap/openfreemap/fonts/{fontstack}/{range}.pbf')
 def test_cached_resource_and_fixed_upstream(self):
  raw=b'MAPLIBRE FIXTURE'
  with patch.object(s,'fetch_remote',return_value=raw) as fetch:
   got=s.load_cached_resource('vendor','my-test-resource',['https://cdn.jsdelivr.net/mock'],100,60)
   again=s.load_cached_resource('vendor','my-test-resource',['https://cdn.jsdelivr.net/mock'],100,60)
  self.assertEqual(got,raw);self.assertEqual(again,raw);self.assertEqual(fetch.call_count,1)
 def test_no_tls_bypass(self):
  import inspect
  source=inspect.getsource(s)
  self.assertNotIn('CERT_NONE',source)
  self.assertNotIn('check_hostname=False',source)
  self.assertIn('ssl.create_default_context()',source)

if __name__=='__main__':unittest.main(verbosity=2)
