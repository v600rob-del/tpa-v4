"""Browser load check for HQ 4.1.0 smooth movement: python3 motion_perf_check.py <vehicles>. Needs TPA_TEST_VENDOR_DIR."""
import sys, time, random, os, subprocess, hashlib, shutil, tempfile
sys.path.insert(0,'/home/claude/tpa-cloud-dev-lab/v4/tests/hq')
os.environ.setdefault('TPA_TEST_VENDOR_DIR','')
import test_motion_4100 as T
from playwright.sync_api import sync_playwright
N=int(sys.argv[1]); random.seed(2)
class Big(T.FakeEngine):
    def frame(self):
        now=time.time(); ents=[]
        for i in range(N):
            ents.append({'id':f'air:{i}','label':f'A{i}','quality':'observed','observedAt':T.iso(now-1),
              'position':{'lon':-0.75+random.random()*0.7,'lat':53.70+random.random()*0.28,'headingDeg':random.random()*360},
              'motion':{'mode':'heading','speedMps':20+random.random()*200,'maxSeconds':60}})
        return {'component':'air','contractVersion':'1.0','health':'ready','observedAt':T.iso(now),'receivedAt':T.iso(now),
                'expiresAt':T.iso(now+60),'nextPollAfterMs':10000,'attribution':'PRETEND','entities':ents}
cache=os.path.join(tempfile.gettempdir(),'tpa-v4-hq-icons-lan-4.0.8','vendor'); os.makedirs(cache,exist_ok=True); seeded=[]
for route,src in {'/assets/vendor/leaflet.js':'leaflet/dist/leaflet.js','/assets/vendor/leaflet.css':'leaflet/dist/leaflet.css'}.items():
    t=os.path.join(cache,hashlib.sha256(route.encode()).hexdigest())
    if not os.path.exists(t): shutil.copyfile(os.path.join(os.environ['TPA_TEST_VENDOR_DIR'],src),t); seeded.append(t)
eng=Big(); port=T.free_port()
hq=subprocess.Popen([sys.executable,str(T.HQ)],env=dict({k:v for k,v in os.environ.items() if not k.startswith('TPA_HQ_')},TPA_HQ_PORT=str(port),TPA_HQ_PARKED='sea',TPA_HQ_DISABLE_MAP_NETWORK='1',TPA_HQ_ENGINES=f'air=http://127.0.0.1:{eng.port}'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1.5)
with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for label,vp,mob in (('desktop',(1440,900),False),('phone',(390,844),True)):
        pg=b.new_page(viewport={'width':vp[0],'height':vp[1]},is_mobile=mob,has_touch=mob)
        cdp=pg.context.new_cdp_session(pg); cdp.send('Performance.enable')
        if mob: cdp.send('Emulation.setCPUThrottlingRate',{'rate':4})
        pg.goto(f'http://127.0.0.1:{port}/'); pg.wait_for_selector('#tpa-welcome:not(.hidden)'); pg.locator('#tpa-welcome .tpa-primary').click()
        t0=time.time()
        while time.time()-t0<15 and pg.evaluate("document.querySelectorAll('.review-pin.air').length")<10: time.sleep(0.2)
        time.sleep(2)
        m=lambda: {x['name']:x['value'] for x in cdp.send('Performance.getMetrics')['metrics']}
        a=m(); w0=time.time(); time.sleep(6); z=m(); w=time.time()-w0
        n=pg.evaluate("document.querySelectorAll('.review-pin.air').length"); print("glide", pg.evaluate("window.TPA_HQ_GLIDE_STATUS()"))
        print({k: round(100*(z[k]-a[k])/w) for k in ("ScriptDuration","RecalcStyleDuration","LayoutDuration","TaskDuration") if k in z}); print(f"{label}{' (CPU slowed 4x)' if mob else ''}: {n} markers on screen, main thread busy {100*(z['TaskDuration']-a['TaskDuration'])/w:.0f}% while gliding")
        pg.close()
    b.close()
hq.terminate(); eng.close()
for t in seeded: os.remove(t)
