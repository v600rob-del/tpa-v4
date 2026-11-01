"""Server fit check (30 Oct 2026): all four engines + HQ with national-size PRETEND feeds; prints memory/CPU. Run from the repo root."""
import sys, os, io, json, gzip, csv, time, zipfile, random, threading, tempfile, subprocess, urllib.request
REPO='/home/claude/tpa-cloud-dev-lab'
sys.path.insert(0, REPO+'/v4/tests/engines')
import test_rail_engine as R
import test_bus_engine as B
import test_air_engine as A
from test_road_engine import FakeTomTom, free_port, start, tile_for
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
random.seed(1)
OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
def get(u, t=30):
    with OPEN(u, timeout=t) as r: return r.read()

# ---- rail: national-size CORPUS (~55k) and NaPTAN (~2.6k)
R.CORPUS = {'TIPLOCDATA': [{'STANOX': f'{10000+i:05d}', 'TIPLOC': f'T{i:06d}', 'NLCDESC': f'PLACE NUMBER {i} JUNCTION', 'NLC': str(100000+i), '3ALPHA':'', 'UIC':'', 'NLCDESC16':'PLACE'} for i in range(55000)]}
def naptan():
    o=io.StringIO(); w=csv.DictWriter(o,fieldnames=['ATCOCode','StopType','Latitude','Longitude','Status','CommonName']); w.writeheader()
    for i in range(0,55000,21): w.writerow({'ATCOCode':f'9100T{i:06d}','StopType':'RLY','Latitude':f'{50.5+random.random()*7:.5f}','Longitude':f'{-5+random.random()*6.5:.5f}','Status':'active','CommonName':f'Station {i}'})
    return o.getvalue().encode()
R.naptan_csv = naptan
files, stomp = R.FakeFiles(), R.FakeStomp()

# ---- bus: national bulk zip ~ 35k vehicles
acts=[B.activity(f'V{i}', str(i%300), 'Town Centre', f'{50.5+random.random()*7:.5f}', f'{-5+random.random()*6.5:.5f}') for i in range(35000)]
xml=B.siri(acts)
xml = xml.encode() if isinstance(xml,str) else xml
zb=io.BytesIO()
with zipfile.ZipFile(zb,'w',zipfile.ZIP_DEFLATED) as z: z.writestr('siri.xml', xml)
ZIP=zb.getvalue(); print('bus xml MB', round(len(xml)/1e6,1), 'zip MB', round(len(ZIP)/1e6,1))
class BH(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length',str(len(ZIP))); self.end_headers(); self.wfile.write(ZIP)
    def log_message(self,*a): pass
bs=ThreadingHTTPServer(('127.0.0.1',0),BH); threading.Thread(target=bs.serve_forever,daemon=True).start()

air=A.FakeADSB()
tt=FakeTomTom()
log=open(os.devnull,'w')
data=tempfile.mkdtemp()
P={k:free_port() for k in ['road','bus','air','rail','hq']}
base=dict(os.environ); [base.pop(k,None) for k in list(base) if k.startswith('TPA_')]
E=REPO+'/v4/engines'
procs={}
procs['road']=start(E+'/road/road_engine.py', dict(base,TPA_ROAD_ENGINE_PORT=str(P['road']),TOMTOM_API_KEY='x',TPA_ROAD_TOMTOM_URL=tt.template,LOCALAPPDATA=data), f"http://127.0.0.1:{P['road']}/status", log)
procs['bus']=start(E+'/bus/bus_engine.py', dict(base,TPA_BUS_ENGINE_PORT=str(P['bus']),TPA_BODS_API_KEY='x',TPA_BUS_MODE='bulk',TPA_BUS_TEST_BULK_URL=f'http://127.0.0.1:{bs.server_address[1]}/bulk',LOCALAPPDATA=data), f"http://127.0.0.1:{P['bus']}/status", log)
procs['air']=start(E+'/air/air_engine.py', dict(base,TPA_AIR_ENGINE_PORT=str(P['air']),TPA_AIR_POLL_SECONDS='20',TPA_AIR_TEST_URL=air.template,LOCALAPPDATA=data), f"http://127.0.0.1:{P['air']}/status", log)
procs['rail']=start(E+'/rail/rail_engine.py', dict(base,TPA_RAIL_ENGINE_PORT=str(P['rail']),TPA_RAIL_DATA_DIR=data+'/rail',TPA_NR_USERNAME=R.USER,TPA_NR_PASSWORD=R.PASSWORD,
     TPA_RAIL_TEST_STOMP=f'127.0.0.1:{stomp.port}',TPA_RAIL_TEST_CORPUS_URL=f'http://127.0.0.1:{files.port}/corpus',TPA_RAIL_TEST_NAPTAN_URL=f'http://127.0.0.1:{files.port}/naptan'), f"http://127.0.0.1:{P['rail']}/status", log)
procs['hq']=start(REPO+'/v4/hq/serve_hq.py', dict(base,TPA_HQ_PORT=str(P['hq']),TPA_HQ_PARKED='sea',TPA_HQ_ENGINES=','.join(f"{k}=http://127.0.0.1:{P[k]}" for k in ['road','bus','air','rail'])), f"http://127.0.0.1:{P['hq']}/", log)
def mem(pid):
    d={}
    for l in open(f'/proc/{pid}/status'):
        if l.startswith(('VmRSS','VmHWM')): k,v=l.split(':'); d[k]=int(v.split()[0])//1024
    return d
def cpu(pid):
    f=open(f'/proc/{pid}/stat').read().rsplit(')',1)[1].split(); return (int(f[11])+int(f[12]))/os.sysconf('SC_CLK_TCK')
views=['west=-1.30&south=53.70&east=-0.20&north=54.05','west=-0.5&south=51.3&east=0.3&north=51.7','west=-2.6&south=53.3&east=-2.0&north=53.6']
def browse():
    for v in views:
        for k in ['bus','air','rail','road']:
            try: get(f"http://127.0.0.1:{P['hq']}/api/v4/components/{k}/snapshot?{v}")
            except Exception as e: pass
# road: ask ~600 distinct tiles
def tiles():
    n=0
    for z in (10,11,12):
        for i in range(200):
            lat=50.5+random.random()*7; lon=-5+random.random()*6.5; x,y=tile_for(lat,lon,z)
            try: get(f"http://127.0.0.1:{P['hq']}/api/v4/components/road/tiles/{z}/{x}/{y}.png"); n+=1
            except Exception: pass
    return n
browse(); time.sleep(3)
# rail: publish 25k trains in batches of 30 (like the real feed)
t0=time.time()
for b in range(0,25000,30):
    stomp.publish([R.movement(f'{(b+j)%90+10:02d}1A{((b+j)//90)%100:02d}'+chr(65+((b+j)//9000)%26)+'A01', f'{10000+random.randint(0,54999):05d}') for j in range(30)])
print('rail publish secs', round(time.time()-t0,1))
c0={k:cpu(p.pid) for k,p in procs.items()}
T=90; end=time.time()+T; nt=0
while time.time()<end:
    browse(); nt+=tiles() if nt<600 else 0; time.sleep(5)
c1={k:cpu(p.pid) for k,p in procs.items()}
st=json.loads(get(f"http://127.0.0.1:{P['rail']}/status")); bs_=json.loads(get(f"http://127.0.0.1:{P['bus']}/status"))
print('rail trainsTracked',st.get('trainsTracked'),'stationsKnown',st.get('stationsKnown'),'| bus', bs_.get('busesInLastDownload'), 'buses')
tot=0
for k,p in procs.items():
    m=mem(p.pid); tot+=m['VmHWM']; print(f"{k:5} RSS {m['VmRSS']:4} MB  peak {m['VmHWM']:4} MB  CPU {100*(c1[k]-c0[k])/T:5.1f}% of one core over {T}s")
print('TOTAL peak MB', tot, 'tiles fetched', nt)
for p in procs.values(): p.terminate()
