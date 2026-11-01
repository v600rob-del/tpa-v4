"""Regression for actual OpenFreeMap Liberty URLs, not a simulated production deployment.

The archived Liberty JSON uses /sprites/ofm_f384/ofm and
/natural_earth/ne2sr/{z}/{x}/{y}.png. Both were rejected in 4.0.4.
"""
import gzip
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SRC=Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py'
spec=importlib.util.spec_from_file_location('tpa405_server',SRC)
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class ActualLibertyPaths(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.old_cache=s.CACHE_DIR;s.CACHE_DIR=Path(self.tmp.name)
  s.FREQUENCY.clear();s.ERRORS.clear();s.NEGATIVE_CACHE.clear()
  for k in s.RESOURCE_COUNTS:s.RESOURCE_COUNTS[k]=0
  s.BROWSER.update(lastCode='none',count=0)
  self.srv=s.ThreadingHTTPServer(('127.0.0.1',0),s.HQHandler)
  self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
  self.base=f'http://127.0.0.1:{self.srv.server_port}'
 def tearDown(self):
  self.srv.shutdown();self.srv.server_close();self.thread.join(timeout=3)
  s.CACHE_DIR=self.old_cache;self.tmp.cleanup()
 def request(self,path):
  with urlopen(self.base+path,timeout=4) as r:return r.status,dict(r.headers),r.read()
 def test_real_liberty_style_all_resources_and_counter(self):
  # Provider-like Liberty URLs; fixture bytes deliberately not real map data.
  style={'version':8,'sources':{'natural':{'type':'raster','tiles':['https://tiles.openfreemap.org/natural_earth/ne2sr/{z}/{x}/{y}.png']},'streets':{'type':'vector','url':'https://tiles.openfreemap.org/planet'}},'sprite':'https://tiles.openfreemap.org/sprites/ofm_f384/ofm','glyphs':'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf'}
  seen=[]
  def fake_fetch(url,limit):
   seen.append(url)
   if url.endswith('/styles/liberty'):return json.dumps(style).encode()
   if url.endswith('.json'):return b'{}'
   if url.endswith('.png'):return b'\x89PNG\r\n\x1a\n' + b'MOCK'
   if url.endswith('.pbf'):return b'\x1a\x00MOCK'
   if url.endswith('/planet'):return json.dumps({'tiles':['https://tiles.openfreemap.org/planet/{z}/{x}/{y}.pbf']}).encode()
   raise AssertionError('Unexpected '+url)
  with patch.object(s,'fetch_remote',side_effect=fake_fetch):
   status,_,raw=self.request('/basemap/openfreemap/styles/liberty');self.assertEqual(status,200)
   parsed=json.loads(raw)
   self.assertEqual(parsed['sprite'],self.base+'/basemap/openfreemap/sprites/ofm_f384/ofm')
   self.assertEqual(parsed['sources']['natural']['tiles'][0],self.base+'/basemap/openfreemap/natural_earth/ne2sr/{z}/{x}/{y}.png')
   for path in ('/basemap/openfreemap/sprites/ofm_f384/ofm.json',
                '/basemap/openfreemap/sprites/ofm_f384/ofm@2x.png',
                '/basemap/openfreemap/natural_earth/ne2sr/4/7/5.png',
                '/basemap/openfreemap/planet',
                '/basemap/openfreemap/planet/8/123/85.pbf',
                '/basemap/openfreemap/fonts/Open%20Sans%20Regular,Arial%20Unicode%20MS%20Regular/0-255.pbf'):
    status,headers,body=self.request(path)
    self.assertEqual(status,200,path)
   self.assertEqual(len(seen),7)
  d=json.loads(self.request('/hq-map-status')[2]);self.assertEqual(d['resourceCounts']['rejected'],0)
  self.assertEqual(d['resourceCounts']['sprite'],2)
  self.assertEqual(d['resourceCounts']['naturalEarth'],1)
  self.assertEqual(d['resourceCounts']['font'],1)
 def test_disallowed_path_appears_in_diagnostic(self):
  with self.assertRaises(HTTPError) as cm:self.request('/basemap/openfreemap/private-resource.js')
  self.assertEqual(cm.exception.code,404)
  d=json.loads(self.request('/hq-map-status')[2]);self.assertEqual(d['resourceCounts']['rejected'],1)
  self.assertEqual(d['upstreamErrors']['blocked-map-path']['code'],'disallowed-resource')
 def test_browser_diagnostic_is_local_readable_and_bounded(self):
  url=self.base+'/hq-browser-diagnostic'
  for code in ('library-missing','map-style-error'):
   request=Request(url,data=json.dumps({'code':code}).encode(),headers={'Content-Type':'application/json'},method='POST')
   with urlopen(request,timeout=3) as response:self.assertEqual(response.status,200)
  d=json.loads(self.request('/hq-map-status')[2]);self.assertEqual(d['browser']['lastCode'],'map-style-error');self.assertEqual(d['browser']['count'],2)
  bad=Request(url,data=b'{"code":"/user/secrets"}',headers={'Content-Type':'application/json'},method='POST')
  with self.assertRaises(HTTPError) as cm:urlopen(bad,timeout=3)
  self.assertEqual(cm.exception.code,400)
 def test_gzipped_pbf_is_bounded_and_decoded(self):
  compressed=gzip.compress(b'\x1a\x00SAMPLE_PBF')
  class Resp:
   status=200
   def __enter__(self):return self
   def __exit__(self,*args):return False
   def read(self,n):return compressed
  with patch.object(s,'urlopen',return_value=Resp()):
   self.assertEqual(s.fetch_remote('https://tiles.openfreemap.org/planet/8/0/0.pbf',100),b'\x1a\x00SAMPLE_PBF')

if __name__=='__main__':unittest.main(verbosity=2)
