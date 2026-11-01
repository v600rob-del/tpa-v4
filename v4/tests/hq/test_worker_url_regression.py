"""Reproduction of owner 4.0.7 error using real Chromium worker Request.

This tests URL construction + HTTP plumbing with mock PBF, NOT genuine
OpenFreeMap streets/WebGL rendering or production isolation.
"""
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request as HTTPRequest, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from playwright.sync_api import sync_playwright

SOURCE=Path(__file__).resolve().parents[2]/'hq'/'serve_hq.py'
spec=importlib.util.spec_from_file_location('workerurl_hq408',SOURCE)
hq=importlib.util.module_from_spec(spec);spec.loader.exec_module(hq)

class OwnerVectorURLRegression(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.playwright=sync_playwright().start()
  cls.browser=cls.playwright.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 @classmethod
 def tearDownClass(cls):
  cls.browser.close();cls.playwright.stop()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.oldcache=hq.CACHE_DIR;hq.CACHE_DIR=Path(self.tmp.name)
  hq.FREQUENCY.clear();hq.NEGATIVE_CACHE.clear();hq.ERRORS.clear()
  for key in hq.RESOURCE_COUNTS:hq.RESOURCE_COUNTS[key]=0
  self.srv=hq.ThreadingHTTPServer(('127.0.0.1',0),hq.HQHandler)
  self.worker=threading.Thread(target=self.srv.serve_forever,daemon=True);self.worker.start()
  self.origin=f'http://127.0.0.1:{self.srv.server_port}'
  self.localhost=f'http://localhost:{self.srv.server_port}'
 def tearDown(self):
  self.srv.shutdown();self.srv.server_close();self.worker.join(timeout=3)
  hq.CACHE_DIR=self.oldcache;self.tmp.cleanup()
 def fixture(self,url,limit):
  if url.endswith('/styles/liberty'):
   return json.dumps({'version':8,'sprite':'https://tiles.openfreemap.org/sprites/ofm_f384/ofm',
    'glyphs':'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    'sources':{'planet':{'type':'vector','url':'https://tiles.openfreemap.org/planet'}}}).encode()
  if url.endswith('/planet'):
   return json.dumps({'tilejson':'3.0.0','tiles':['https://tiles.openfreemap.org/planet/20261025_080012_pt/{z}/{x}/{y}.pbf']}).encode()
  if url.endswith('/planet/20261025_080012_pt/8/126/81.pbf'):
   return b'\x1a\x00MOCK_BYTES_NOT_REAL_VECTOR_TILE'
  raise AssertionError('Unexpected network request '+url)
 def load(self,origin,path):
  with urlopen(origin+path,timeout=4) as r:return json.loads(r.read())
 def test_worker_parses_and_fetches_same_origin_urls(self):
  with patch.object(hq,'fetch_remote',side_effect=self.fixture):
   style=self.load(self.origin,'/basemap/openfreemap/styles/liberty')
   tilejson=self.load(self.origin,'/basemap/openfreemap/planet')
   self.assertEqual(style['sources']['planet']['url'],self.origin+'/basemap/openfreemap/planet')
   tile=tilejson['tiles'][0].replace('{z}','8').replace('{x}','126').replace('{y}','81')
   self.assertEqual(tile,self.origin+'/basemap/openfreemap/planet/20261025_080012_pt/8/126/81.pbf')
   self.assertNotIn('tiles.openfreemap.org',json.dumps(style)+json.dumps(tilejson))
   with self.browser.new_page() as page:
    page.set_content('<!doctype html><html><body><p>Real Chromium worker URL parsing</p></body></html>')
    # New Request() is the same Web API MapLibre used when the 4.0.7 error arose;
    # run in a REAL dedicated Chrome worker, with a blob: worker origin.
    result=page.evaluate('''async ({bad,good})=>{
      const source=`onmessage=e=>{let out={};for(const [kind,url] of Object.entries(e.data)){
        try{let request=new Request(url);out[kind]={ok:true,url:request.url};}
        catch(err){out[kind]={ok:false,error:err.message};}
      }postMessage(out);}`;
      const blob=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
      try{return await new Promise((resolve,reject)=>{let w=new Worker(blob);w.onmessage=e=>{w.terminate();resolve(e.data)};
         w.onerror=e=>reject(Error(e.message));w.postMessage({bad,good});});}
      finally{URL.revokeObjectURL(blob)}
    }''',{'bad':'/basemap/openfreemap/planet/20261025_080012_pt/8/126/81.pbf','good':tile})
    self.assertFalse(result['bad']['ok'],result)
    self.assertTrue(result['good']['ok'],result)
    self.assertEqual(result['good']['url'],tile)
    page.set_content('''<!doctype html><html><head><style>
      body {background:#071220;color:#e7f1fc;font-family:Arial,sans-serif;margin:0;padding:32px}
      h1 {font-size:27px;margin:0 0 12px}p {color:#b4c6d9;font-size:14px;line-height:1.6}
      .box {margin:18px 0;padding:24px;background:#122338;border:1px solid #31475c;border-radius:12px}
      .pass {color:#5ce2b0;font-weight:700}.fail {color:#ffac84;font-weight:700}
      code {display:block;padding-top:13px;font-size:12px;color:#e6f4ff;overflow-wrap:anywhere}
      .sub {letter-spacing:.15em;color:#78b4d9;font-size:11px;font-weight:700}
      </style></head><body>
      <div class='sub'>TPA V4 HQ · 4.0.8 · REAL CHROMIUM WORKER TEST</div>
      <h1>Map vector URL regression</h1>
      <p>This screenshot proves worker URL construction only. MapLibre street tiles are NOT rendered or validated here.</p>
      <div class='box'><span class='fail'>BEFORE: RELATIVE URL REJECTED</span><code id='bad'></code></div>
      <div class='box'><span class='pass'>AFTER: ABSOLUTE LOCAL URL ACCEPTED</span><code id='good'></code></div>
      <p>Source data mocked for local HTTP server test; Oracle v3 and all transport engines untouched.</p>
      </body></html>''')
    page.evaluate('''({bad,good})=>{document.querySelector('#bad').textContent=bad;document.querySelector('#good').textContent=good;}''',{
      'bad':result['bad']['error'], 'good':result['good']['url']})
    page.screenshot(path=str(Path(__file__).resolve().parents[2]/'docs/hq/HQ-4.0.8-REAL-CHROMIUM-WORKER-URL-TEST.png'),full_page=True)
    # Chromium networking into localhost is blocked by this laboratory;
    # verify the matching proxy request with the real Python HTTP client.
   with urlopen(tile,timeout=4) as r:
    self.assertEqual(r.status,200)
    self.assertIn('protobuf',r.headers.get('Content-Type'))
    self.assertGreater(len(r.read()),2)
   with urlopen(self.origin+'/hq-map-status') as r:
    diagnostics=json.load(r)
   self.assertGreaterEqual(diagnostics['resourceCounts']['vector'],1)
   self.assertEqual(diagnostics['resourceCounts']['rejected'],0)
 def test_port_neutral_cache_and_host_restriction(self):
  with patch.object(hq,'fetch_remote',side_effect=self.fixture) as mocked:
   a=self.load(self.origin,'/basemap/openfreemap/styles/liberty')
   b=self.load(self.localhost,'/basemap/openfreemap/styles/liberty')
   self.assertEqual(mocked.call_count,1,'only one provider style fetch across both local hostnames')
   self.assertTrue(a['sprite'].startswith(self.origin+'/basemap/openfreemap/'))
   self.assertTrue(b['sprite'].startswith(self.localhost+'/basemap/openfreemap/'))
  for badhost in ('evil.example','localhost:9999','127.0.0.1:1','127.0.0.1:80@evil.example'):
   req=HTTPRequest(self.origin+'/basemap/openfreemap/styles/liberty',headers={'Host':badhost})
   with self.subTest(badhost=badhost):
    with self.assertRaises(HTTPError) as cm:urlopen(req,timeout=3)
    self.assertEqual(cm.exception.code,403)

if __name__=='__main__':unittest.main(verbosity=2)
