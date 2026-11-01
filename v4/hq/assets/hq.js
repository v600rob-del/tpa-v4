/* TPA v4 HQ only: MapLibre GL JS + OpenFreeMap, same-origin resources.
 * The five transport connections accept versioned JSON *data*, never scripts.
 * No supplier/feed logic, motion algorithms, Admin controls or external URLs.
 */
(()=>{'use strict';
const IDS=Object.freeze(['air','sea','rail','bus','road']);
const SOCKETS=Object.freeze(Object.fromEntries(IDS.map(id=>[id,Object.freeze({id,contractVersion:'1.0',type:'data-only',path:'/api/v4/components/'+id})])));
Object.defineProperty(window,'TPA_HQ_SOCKETS',{value:SOCKETS,writable:false,configurable:false});
const $=id=>document.getElementById(id),root=$('map'),canvas=$('geo-canvas'),ctx=canvas.getContext('2d');
const HOME={lon:-0.423,lat:53.842,z:11};
const POI=[
['Beverley',-0.423,53.842],['Hull',-0.334,53.745],['York',-1.082,53.960],['Leeds',-1.549,53.801],
['Bridlington',-0.202,54.084],['Scarborough',-0.401,54.284],['Goole',-0.869,53.702],['Doncaster',-1.134,53.523],
['Sheffield',-1.470,53.381],['Lincoln',-0.540,53.231],['Nottingham',-1.152,52.953],['Manchester',-2.244,53.483],
['Liverpool',-2.984,53.408],['Newcastle',-1.617,54.978],['Middlesbrough',-1.235,54.576],['Birmingham',-1.892,52.486],
['London',-0.128,51.508],['Cambridge',0.121,52.205],['Norwich',1.299,52.628],['Oxford',-1.255,51.753],
['Bristol',-2.588,51.454],['Cardiff',-3.179,51.481],['Swansea',-3.944,51.621],['Exeter',-3.534,50.718],
['Plymouth',-4.142,50.375],['Southampton',-1.404,50.909],['Brighton',-0.137,50.822],['Edinburgh',-3.189,55.953],
['Glasgow',-4.252,55.864],['Aberdeen',-2.094,57.149],['Dundee',-2.974,56.462],['Inverness',-4.226,57.477],
['Stirling',-3.935,56.116],['Perth',-3.432,56.395],['Belfast',-5.930,54.597],['Derry',-7.311,54.997]
];
const state={live:Object.fromEntries(IDS.map(id=>[id,false])),notice:Object.fromEntries(IDS.map(id=>[id,''])),overlays:Object.create(null),parked:Object.fromEntries(IDS.map(id=>[id,false])),lon:HOME.lon,lat:HOME.lat,z:HOME.z,visible:Object.fromEntries(IDS.map(id=>[id,true])),status:Object.fromEntries(IDS.map(id=>[id,'not_installed'])),frames:Object.create(null),geo:null,selected:null,renderer:'loading',mapErrors:0};
let map=null,ready=false,toastTimer=0,pending=false,pan=null;
const selected=$('map-selection');
const markerLayer=document.createElement('div');markerLayer.className='marker-layer';root.append(markerLayer);
const notice=$('map-wait');
const reported=new Set();
function reportBrowser(code,stage='general',detail='none'){
  const safeDetail=String(detail).replace(/https?:\/\/[^ ]+/g,'URL').replace(/[^A-Za-z0-9 .,;:_()!?/+\-]/g,' ').slice(0,180)||'none';
  const safeStage=String(stage).replace(/[^a-z0-9-]/g,'-').slice(0,40)||'general';
  // Keep latest useful detail; do not let a generic error overwrite it.
  fetch('/hq-browser-diagnostic',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,stage:safeStage,detail:safeDetail})}).catch(()=>{});
}
window.TPA_HQ_MAP_REPORT=reportBrowser;
window.addEventListener('error',e=>{
  if(e.target&&e.target!==window&&['SCRIPT','LINK'].includes(e.target.tagName))reportBrowser('library-resource-error','dom-resource',e.target.getAttribute('src')||e.target.getAttribute('href')||'none');
  else if(e.target===window)reportBrowser('script-runtime-error','uncaught',String(e.message||'Unknown runtime error'));
},true);
window.addEventListener('unhandledrejection',e=>reportBrowser('script-runtime-error','rejection',String(e.reason?.message||e.reason||'Unknown rejection')));

function showNotice(message,warning=false){notice.textContent=message;notice.classList.remove('hidden');notice.classList.toggle('offline',warning);}
function toast(message){const el=$('toast');el.textContent=message;el.classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.add('hidden'),3000);}
function clamp(n,a,b){return Math.max(a,Math.min(b,n));}
function merc(lon,lat,z){const size=256*2**z,r=clamp(lat,-85.051128,85.051128)*Math.PI/180;return [(lon+180)/360*size,(1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*size];}
function inverse(px,py,z){const size=256*2**z,y=Math.PI*(1-2*py/size);return {lon:px/size*360-180,lat:180/Math.PI*Math.atan(Math.sinh(y))};}
function dims(){return {w:root.clientWidth,h:root.clientHeight};}
function pixel(lon,lat){if(map){let p=map.latLngToContainerPoint([lat,lon]);return [p.x,p.y];}
const d=dims(),p=merc(lon,lat,state.z),c=merc(state.lon,state.lat,state.z);return [p[0]-c[0]+d.w/2,p[1]-c[1]+d.h/2];}
// 4.1.12 (owner 31 Oct 22:11: "Icons could be smaller for buses and trains ... a zoom in to max or nearly
// max zoom could have larger icons"): bus and train icons are sized by zoom level.
// 4.1.16 (owner 1 Nov 00:28: "knowing which part of the icon should be central to the road/track it is
// on ... the middle/bottom of the numberboard under the bus/train"): for buses and trains the exact map
// position is the bottom-middle of the number board, with the icon standing above it like a pin.
// Other icons stay centred. (Board top 30 px + board 16 px, scaled, + 8 px of fixed borders; measured.)
function anchorOf(component){return component==='bus'||component==='rail'?'translate(-50%,calc(var(--pin-s,1) * -46px - 8px))':'translate(-50%,-50%)';}
function pinScale(z){return z<12.5?0.7:z<14.5?0.82:z<16.5?1:1.2;}
function syncView(){if(map){let c=map.getCenter();state.lon=c.lng;state.lat=c.lat;state.z=map.getZoom();}
  const ps=String(pinScale(state.z));if(markerLayer.style.getPropertyValue('--pin-s')!==ps)markerLayer.style.setProperty('--pin-s',ps);paint();}
function gotoPlace(lon,lat,z,label){if(map)map.setView([lat,lon],z,{animate:false});state.lon=lon;state.lat=lat;state.z=z;selected.classList.add('hidden');state.selected=null;$('map-place').textContent=(label||'SELECTED LOCATION').toUpperCase();$('sidebar').classList.remove('open');paint();}
function home(){gotoPlace(HOME.lon,HOME.lat,HOME.z,'BEVERLEY, EAST YORKSHIRE');toast('Home · Beverley, East Yorkshire');}
function fitUK(){gotoPlace(-3.0,54.55,5.5,'UNITED KINGDOM');}
function zoom(delta){if(map)map.setZoom(clamp(map.getZoom()+delta,3,18));else{state.z=clamp(state.z+delta,3,17);paint();}}
function drawFallback(){const {w,h}=dims(),scale=Math.min(2,window.devicePixelRatio||1);if(canvas.width!==Math.round(w*scale)||canvas.height!==Math.round(h*scale)){canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);}ctx.setTransform(scale,0,0,scale,0,0);ctx.clearRect(0,0,w,h);if(!state.geo||ready)return;
ctx.fillStyle='#e8eeea';for(const shape of state.geo.land){if(shape.length<3)continue;ctx.beginPath();shape.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.closePath();ctx.fill();}
ctx.strokeStyle='#86acb1';ctx.lineWidth=1;for(const seg of state.geo.coastlines){ctx.beginPath();seg.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.stroke();}
for(const [name,lon,lat] of POI){const [x,y]=pixel(lon,lat);if(x<0||x>w||y<0||y>h)continue;if(state.z<7&&!['Beverley','Hull','York','Leeds','London','Edinburgh','Glasgow','Cardiff','Belfast'].includes(name))continue;ctx.font='600 12px system-ui';ctx.strokeStyle='#e8eeea';ctx.lineWidth=3;ctx.strokeText(name,x+5,y-5);ctx.fillStyle='#2b555c';ctx.fillText(name,x+5,y-5);}}
function listEntities(){return IDS.flatMap(id=>{
  if(!state.visible[id]||state.status[id]==='not_installed')return [];
  const entities=state.frames[id]?.entities||[];
  // A failed/stale component may keep its last-known markers, but never appear current.
  return state.status[id]==='ready'?entities:entities.map(e=>({...e,quality:'stale'}));
});}
// ICON REVIEW ONLY. The SVG shapes are fixed HQ artwork reproduced from the
// legacy approved map marker paths; component JSON supplies DATA, never HTML.
// This review build is physically separate from the working HQ 4.0.8 checkpoint.
const ICON_PATHS=Object.freeze({
  air:'M16 2c1.3 0 2 2 2 4v8l10 8v4l-10-5v6l3 2v2l-5-1.5L11 31v-2l3-2v-6l-10 5v-4l10-8V6c0-2 0.7-4 2-4Z',
  sea:'m16 3 2 3v5h6v4l4 2v3l-4 7H8l-4-7v-3l4-2v-4h6V6l2-3Zm-7 17 2 5h10l2-5H9Zm5-6H9v3h5v-3Zm9 0h-5v3h5v-3Z',
  rail:'M8 3h16a5 5 0 0 1 5 5v15a4 4 0 0 1-4 4l3 3h-5l-3-3h-8l-3 3H4l3-3a4 4 0 0 1-4-4V8a5 5 0 0 1 5-5Zm1 5v10h14V8H9Zm1 13a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm12 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z'
});
const DEMO_ICONS=Object.freeze({air:'AIR',sea:'SEA',rail:'TRAIN',bus:'BUS',road:'ROAD'});
function iconFor(id){return {air:'✈',sea:'⛴',rail:'🚂',bus:'🚌',road:'≋'}[id]||'•';}
function buildArtwork(e){
  const icon=document.createElement('span');icon.className='review-art';icon.setAttribute('aria-hidden','true');
  if(ICON_PATHS[e.component]){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('focusable','false');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',ICON_PATHS[e.component]);svg.append(path);
    // Ships always point up on-screen. Course remains as data, not an icon rotation.
    if(e.component==='air'&&e.position?.headingDeg!=null)svg.style.transform='rotate('+e.position.headingDeg+'deg)';
    icon.append(svg);
  }else{icon.textContent=e.component==='bus'?'🚌':'≋';}
  if(e.component==='rail'||e.component==='bus'){
    const badge=document.createElement('span');badge.className='review-route';
    // Only an alphanumeric suffix is used. Sample route/headcode is not a transport algorithm.
    badge.textContent=e.badge||String(e.id.split('-').pop()||'MOCK').replace(/[^a-zA-Z0-9]/g,'').slice(0,8)||'MOCK';
    icon.append(badge);
  }
  return icon;
}
let detailsTimer=0;
async function loadDetails(e,box){
  clearTimeout(detailsTimer);
  if(!state.live[e.component])return;
  try{
    const r=await fetch(`/api/v4/components/${e.component}/details?id=${encodeURIComponent(e.id)}`,{cache:'no-store'});
    if(!r.ok){box.replaceChildren();return;}      // engines without details (aircraft, trains for now): nothing extra
    const d=await r.json();
    if(!state.selected||state.selected.id!==e.id||!box.isConnected)return;
    const rows=[];
    for(const row of Array.isArray(d.rows)?d.rows.slice(0,12):[]){
      const line=document.createElement('div');line.className='detail-row';
      const k=document.createElement('span');k.className='detail-label';k.textContent=String(row.label||'').slice(0,30);
      const v=document.createElement('span');v.className='detail-value';v.textContent=String(row.value||'').slice(0,120);
      line.append(k,v);rows.push(line);}
    if(typeof d.note==='string'&&d.note){const n=document.createElement('small');n.className='detail-note';n.textContent=d.note.slice(0,160);rows.push(n);}
    box.replaceChildren(...rows);
  }catch(err){}
  // the next stop changes as the vehicle moves: ask again while it stays selected
  detailsTimer=setTimeout(()=>{if(state.selected&&state.selected.id===e.id&&box.isConnected)loadDetails(e,box);},20000);
}
function selectEntity(e){
  state.selected=e;
  const h=document.createElement('strong');h.textContent=iconFor(e.component)+' '+e.label;
  const live=!!state.live[e.component];
  const seen=new Date(e.observedAt).toLocaleTimeString('en-GB',{hour12:false});
  const info=document.createElement('small');info.textContent=infoText(e,!!whereNow(e,Date.now())?.est);void seen;
  // 4.4.1 (owner 1 Nov 13:59: the data-source lines "look busy and cluttered. Simple experience for the end
  // user"): live vehicles' sources stay in the map's credit line and "Data sources & credits", not here.
  // Pretend vehicles keep their warning.
  const warning=document.createElement('small');warning.className=live?'live-source':'review-no-live';warning.textContent=live?'':'FICTIONAL DEMONSTRATION — NOT AN ACTUAL VEHICLE';
  if(live)warning.hidden=true;
  const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close target details');
  close.onclick=()=>{selected.classList.add('hidden');state.selected=null;paint();};
  // 4.4.0 (owner 1 Nov 13:42): more about this vehicle from its own engine (start, end, next stop for a bus)
  const more=document.createElement('div');more.className='vehicle-details';
  selected.replaceChildren(h,info,more,warning,close);selected.classList.remove('hidden');
  $('map-place').textContent=e.label.toUpperCase();paint();
  loadDetails(e,more);
}
// 4.1.0 smooth movement. Markers are kept and moved, not rebuilt. Between real reports a
// live vehicle with a motion hint is carried forward (capped by the engine's maxSeconds),
// and a fresh report is reached by a short slide, never a jump. Any position that is not
// exactly the last real report is marked and described as ESTIMATED.
const REDUCED=!!(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches);
const BLEND_MS=2000,pins=new Map(),shown=new Map(),blends=new Map();
// 4.1.2 fluid movement: positions are worked out about once a second and the browser
// animates each marker smoothly between them (a linear CSS transition on its transform,
// drawn by the graphics side at full frame rate, which costs almost nothing). While the
// map itself is being dragged or zoomed the animation is switched off so markers stay
// pinned to the map.
const GLIDE_MS=1000;
let moving=0,mapMoving=false;
// The page checks how smoothly it is drawing. While it keeps up, every moving vehicle on
// screen glides; if frames start taking too long, only the vehicles nearest the middle of
// the screen glide and the rest step once a second, until it is comfortable again.
// Phones start (and stay) with fewer gliding vehicles to save battery; the rest step once a second.
const GLIDE_MAX=(window.innerWidth||1000)<760?60:600,GLIDE_MIN=15;
let glideBudget=GLIDE_MAX,frameAvg=16,lastFrame=0,glideCount=0;
function watchFrames(ts){
  requestAnimationFrame(watchFrames);
  if(lastFrame&&glideCount&&document.visibilityState==='visible'&&!mapMoving){
    // 4.1.5: only sustained slowness counts (a single busy moment, e.g. fresh data arriving, doesn't).
    const dt=Math.min(100,ts-lastFrame);frameAvg=frameAvg*0.98+dt*0.02;
    if(frameAvg>34&&glideBudget>GLIDE_MIN){glideBudget=Math.max(GLIDE_MIN,Math.floor(glideBudget*0.85));frameAvg=22;}
    else if(frameAvg<20&&glideBudget<GLIDE_MAX&&glideCount>=glideBudget){glideBudget=Math.min(GLIDE_MAX,glideBudget+10);}
  }
  lastFrame=ts;
}
requestAnimationFrame(watchFrames);
window.TPA_HQ_GLIDE_STATUS=()=>({budget:glideBudget,gliding:glideCount,frameMs:Math.round(frameAvg*10)/10});
function keyOf(e){return e.component+':'+e.id;}
function estimate(e,now){
  const p=e.position,m=state.live[e.component]?e.motion:null;
  if(!p)return null;
  // 4.2.0 "timed": be at each point at its own clock time (the engine replays a real journey).
  if(m&&m.mode==='timed'){
    const T=m.times,P=m.pts;if(now<=T[0])return {lon:P[0][0],lat:P[0][1],est:true};
    let i=1;while(i<T.length&&T[i]<now)i++;if(i>=T.length)return {lon:P[P.length-1][0],lat:P[P.length-1][1],est:true};
    const q=T[i]>T[i-1]?(now-T[i-1])/(T[i]-T[i-1]):1,a=P[i-1],b=P[i];
    return {lon:a[0]+(b[0]-a[0])*q,lat:a[1]+(b[1]-a[1])*q,est:true};
  }
  const t0=Date.parse(e.observedAt);let t=(now-t0)/1000;
  if(!m||!(t>0))return {lon:p.lon,lat:p.lat,est:false};
  t=Math.min(t,m.maxSeconds);
  // slowToStop (4.1.5): steady speed for the first two-thirds, then ease to a stop over the last third.
  const travelled=()=>{const T0=m.maxSeconds*2/3,E=m.maxSeconds-T0,tau=Math.max(0,t-T0);
    return m.slow?m.speedMps*(Math.min(t,T0)+tau-tau*tau/(2*E)):m.speedMps*t;};
  if(m.mode==='heading'){
    const d=travelled(),h=p.headingDeg*Math.PI/180,R=6371000;
    return {lon:p.lon+d*Math.sin(h)/(R*Math.cos(p.lat*Math.PI/180))*180/Math.PI,lat:p.lat+d*Math.cos(h)/R*180/Math.PI,est:t>=1};
  }
  const total=(m.arriveAtMs-t0)/1000,f=total>0?Math.min(1,t/total):1;
  if(m.mode==='path'){
    const end=m.cum[m.cum.length-1],want=m.speedMps?Math.min(end,travelled()/111195):f*end;let i=1;while(i<m.cum.length-1&&m.cum[i]<want)i++;
    const seg=m.cum[i]-m.cum[i-1]||1e-12,q=Math.max(0,Math.min(1,(want-m.cum[i-1])/seg)),a=m.pts[i-1],b=m.pts[i];
    return {lon:a[0]+(b[0]-a[0])*q,lat:a[1]+(b[1]-a[1])*q,est:t>=1};
  }
  return {lon:p.lon+(m.lon-p.lon)*f,lat:p.lat+(m.lat-p.lat)*f,est:t>=1};
}
// 4.1.9 (owner 31 Oct 14:04: icons "wander off when they get a new updated position"; server check:
// estimates typically 93 m out, half ahead and half behind, because buses stop at stops and lights).
// A new report is reached the way a real vehicle would:
//  - estimate had got AHEAD  -> the icon waits where it is (like a bus at a stop) until the vehicle's
//    new estimate catches up, then carries on: never backwards (up to 45 s, then a normal slide);
//  - estimate had fallen BEHIND -> the icon catches up gradually at a believable extra speed;
//  - sideways or unclear -> the short 2 s slide as before.
// 4.1.14 (owner 31 Oct 23:18: waited "almost 30 seconds", then "their little speeding thing"): a vehicle
// waits at most 20 s and catches up at most 40% faster than its own speed; beyond that it fades in.
const WAIT_MAX_MS=20000,CATCHUP_SHARE=0.4,CATCHUP_MAX_MS=30000;
function metresXY(a,b){const k=Math.cos(a.lat*Math.PI/180);return [(b.lon-a.lon)*k*111195,(b.lat-a.lat)*111195];}
// 4.2.1: a "timed" track may hold a jump (two points 0.2 s or less apart and over 30 m apart: the engine
// could not tell which way the vehicle went between two reports). It is drawn as a fade, never a slide.
const timedSeen=new Map();
function timedJump(k,m,now){
  const was=timedSeen.get(k);timedSeen.set(k,now);if(timedSeen.size>5000)timedSeen.clear();
  if(was===undefined||!m.jumps.length)return false;
  return m.jumps.some(t=>t>was&&t<=now);
}
function whereNow(e,now){
  const k=keyOf(e),target=estimate(e,now);if(!target)return null;
  if(e.motion&&e.motion.mode==='timed'&&state.live[e.component]&&timedJump(k,e.motion,now)&&!REDUCED){blends.delete(k);return {...target,est:true,hop:true,jump:true};}
  const b=blends.get(k);
  if(b){
    if(b.hop){blends.delete(k);return {...target,est:true,hop:true,jump:!!b.jump};}
    if(b.wait&&!REDUCED){
      const [dx,dy]=metresXY(b,target);
      if(dx*b.ux+dy*b.uy<0&&now-b.t0<WAIT_MAX_MS)return {lon:b.lon,lat:b.lat,est:true};
      if(dx*b.ux+dy*b.uy>=0){blends.delete(k);return target;}
      blends.set(k,{dlon:b.lon-target.lon,dlat:b.lat-target.lat,t0:now,dur:BLEND_MS});return {lon:b.lon,lat:b.lat,est:true};
    }
    // The difference from the estimate shrinks to nothing, so the icon keeps the vehicle's own
    // speed throughout (no stop-start) and just runs a little faster or slower until it matches.
    const f=REDUCED?1:(now-b.t0)/(b.dur||BLEND_MS);
    if(f<1){const r=1-f*f*(3-2*f);return {lon:target.lon+b.dlon*r,lat:target.lat+b.dlat*r,est:true};}
    blends.delete(k);}
  return target;
}
function noteNewFrame(f,now,fresh){
  // A vehicle already on screen goes from where it is shown to its new position (4.1.9: naturally).
  for(const e of f.entities){const k=keyOf(e),was=shown.get(k);if(!was||!e.position)continue;
    const next=estimate(e,now);if(!next||(Math.abs(next.lon-was.lon)<=1e-6&&Math.abs(next.lat-was.lat)<=1e-6))continue;
    // not being drawn (or just back): no corrections against frozen spots - apply directly, fading in if far
    if(fresh||notDrawing()){const [fx,fy]=metresXY(was,next);if(Math.hypot(fx,fy)>20&&!REDUCED)blends.set(k,{hop:true,t0:now});else blends.delete(k);continue;}
    // 4.2.1: a replayed ("timed") vehicle is never slid across a big gap: its track is where it really went,
    // so a big difference means it was seen somewhere else (a jump) - fade it in there.
    if(e.motion&&e.motion.mode==='timed'){const [tx,ty]=metresXY(was,next);if(Math.hypot(tx,ty)>30&&!REDUCED)blends.set(k,{hop:true,jump:true,t0:now});else blends.set(k,{dlon:was.lon-next.lon,dlat:was.lat-next.lat,t0:now,dur:BLEND_MS});continue;}
    const ahead=estimate(e,now+1000),[mx,my]=ahead?metresXY(next,ahead):[0,0],speed=Math.hypot(mx,my);
    const [gx,gy]=metresXY(was,next),gap=Math.hypot(gx,gy);
    if(e.motion&&speed>0.3&&gap>3){
      const ux=mx/speed,uy=my/speed,along=gx*ux+gy*uy,across=Math.abs(gx*uy-gy*ux);
      if(along<0&&across<40&&-along/speed*1000<=WAIT_MAX_MS){blends.set(k,{lon:was.lon,lat:was.lat,t0:now,wait:true,ux,uy});continue;}
      const catchMs=1.5*gap/(CATCHUP_SHARE*speed)*1000;   // the ease in/out peaks at 1.5x the average: 40% is the most, ever
      if(along>0&&across<40&&catchMs<=CATCHUP_MAX_MS){blends.set(k,{dlon:was.lon-next.lon,dlat:was.lat-next.lat,t0:now,dur:Math.max(BLEND_MS,catchMs)});continue;}
      // 4.1.12 (owner 31 Oct 22:11: buses "go off course ... sometimes moving a fair distance quickly"):
      // a moving vehicle that turns out to be somewhere else entirely (not along its way, over 60 m) is
      // not flown across the buildings; it fades out and reappears in its real place.
      if((gap>60||across<40)&&!REDUCED){blends.set(k,{hop:true,t0:now});continue;}   // too far to wait for or catch up with
    }
    blends.set(k,{dlon:was.lon-next.lon,dlat:was.lat-next.lat,t0:now,dur:BLEND_MS});}
}
function infoText(e,est){
  const seen=new Date(e.observedAt).toLocaleTimeString('en-GB',{hour12:false});
  if(!state.live[e.component])return e.component.toUpperCase()+' · '+e.quality.toUpperCase()+' · STATIC MOCK POSITION';
  if(e.motion&&e.motion.mode==='timed')return 'Following its reported journey'+(e.motion.delay?' about '+(e.motion.delay>=90?Math.round(e.motion.delay/60*2)/2+' min':e.motion.delay+' s'):'')+' behind · last reported '+seen+(e.quality==='stale'?' · may have moved':'');
  if(est)return 'Estimated position · last reported '+seen+(e.quality==='stale'?' · may have moved':'');
  return (e.quality==='observed'?'Reported position at ':'Last reported at ')+seen+(e.quality==='stale'?' · may have moved':'');
}
// Phone-sized screens show at most this many vehicles per layer (engines already send the
// ones nearest the middle of the view first); hundreds of overlapping icons can't be read.
const PHONE_CAP=150;
let glideOK=new Set();
// 4.1.7 (owner 31 Oct: "when you zoom in or out the icons stay where they were until you stop and
// then move to their positions, giving the feeling of a mad scramble"):
// - dragging: the whole icon layer is shifted with the map in one step (no re-placing every icon);
// - zooming: when the map starts its zoom animation, every icon is sent to its place in the new
//   view with the map's own timing, so icons travel with the map instead of freezing then rushing.
let paintRef=null,zoomAnim=false,lastFull=0,zoomGuard=0,viewSig='',lastDrawAt=0;
function shiftLayer(){
  if(!map||!paintRef||zoomAnim||map.getZoom()!==paintRef.z)return false;
  const p=map.latLngToContainerPoint(paintRef.ll),dx=p.x-paintRef.x,dy=p.y-paintRef.y;
  markerLayer.style.transform=Math.abs(dx)+Math.abs(dy)>0.05?`translate(${dx.toFixed(1)}px,${dy.toFixed(1)}px)`:'';
  return true;
}
function zoomAnimStart(e){
  if(!map||typeof map.project!=='function')return;
  zoomAnim=true;markerLayer.style.transform='';clearTimeout(zoomGuard);zoomGuard=setTimeout(zoomAnimEnd,900);
  if(!REDUCED)markerLayer.classList.add('zoom-anim');
  const size=map.getSize(),c=map.project(e.center,e.zoom);
  for(const p of pins.values()){
    if(p.lat==null)continue;
    const q=map.project([p.lat,p.lon],e.zoom),x=q.x-c.x+size.x/2,y=q.y-c.y+size.y/2;
    p.b.classList.remove('glide');
    const tf=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) ${anchorOf(p.e?.component)}${p.active?' scale(1.17)':''}`;
    p.b.style.transform=tf;p.tf=tf;
  }
}
function zoomAnimEnd(){if(!zoomAnim)return;zoomAnim=false;markerLayer.classList.remove('zoom-anim');paint();}
function markers(){
  if(zoomAnim)return;      // icons are travelling with the zoom animation; placed again when it ends
  // 4.1.8 (owner 31 Oct 13:50: after moving the map, moving icons jumped back to their old spot
  // on the screen and glided across): if the view changed since the icons were last placed,
  // place every icon instantly this time; gliding is only ever for the vehicle's own movement.
  let viewMoved=false;
  if(map){const ll=map.getCenter(),c=map.latLngToContainerPoint(ll);paintRef={ll,z:map.getZoom(),x:c.x,y:c.y};markerLayer.style.transform='';
    const r=map.latLngToContainerPoint([54,-2]),sig=map.getZoom()+'|'+r.x.toFixed(1)+'|'+r.y.toFixed(1);viewMoved=sig!==viewSig;viewSig=sig;}
  lastFull=performance.now();lastDrawAt=lastFull;
  const d=dims(),now=Date.now(),keep=new Set(),M=40,cap=d.w<760?PHONE_CAP:Infinity,perLayer=Object.create(null);moving=0;
  // Which vehicles may glide this second: the nearest to the middle of the screen, up to the budget.
  {const cx=d.w/2,cy=d.h/2,list=[];
   for(const [k,p] of pins)if(p.tf&&p.e&&state.live[p.e.component]&&(p.e.motion||blends.has(k))){const m=p.tf.match(/translate\(([-0-9.]+)px,([-0-9.]+)px\)/);if(m)list.push([(m[1]-cx)**2+(m[2]-cy)**2,k]);}
   // 4.1.5: a vehicle that is already gliding keeps gliding (no "taking turns"); free places go
   // to the vehicles nearest the middle of the screen.
   list.sort((a,b)=>a[0]-b[0]);
   const next=new Set();for(const [,k] of list)if(glideOK.has(k)&&next.size<glideBudget)next.add(k);
   for(const [,k] of list){if(next.size>=glideBudget)break;next.add(k);}
   glideOK=next;glideCount=glideOK.size;}
  for(const e of listEntities()){
    if(!e.position)continue;
    const k=keyOf(e),pos=whereNow(e,now);if(!pos)continue;
    if(pos.est||blends.has(k)||(state.live[e.component]&&e.motion))moving++;
    shown.set(k,{lon:pos.lon,lat:pos.lat});
    const [x,y]=pixel(pos.lon,pos.lat);
    if(x<-M||y<-M||x>d.w+M||y>d.h+M)continue;
    if((perLayer[e.component]=(perLayer[e.component]||0)+1)>cap)continue;
    keep.add(k);
    const active=!!state.selected&&state.selected.id===e.id&&state.selected.component===e.component;
    const prefix=state.live[e.component]?'':'MOCK ';
    const glideWanted=!REDUCED&&!mapMoving&&!!pin0(k)&&(pos.est||blends.has(k)||(state.live[e.component]&&!!e.motion));
    const canGlide=glideWanted&&glideOK.has(k)&&!viewMoved,glide=canGlide&&!pos.hop;
    const cls=`vehicle-pin review-pin ${e.component} ${e.quality}${active?' selected-pin':''}${pos.est?' est-pos':''}${glide?' glide':''}`;
    // 4.1.11 (owner 31 Oct 22:08: icons "look weirdish, like they are re rendered. They need to stay
    // the same"): an icon is built once and kept; only an aircraft's existing icon turns to its heading.
    const art=e.component+'|'+(e.badge||'')+'|'+e.id;
    let pin=pins.get(k);
    if(!pin){const b=document.createElement('button');b.type='button';pin={b,art:''};pins.set(k,pin);markerLayer.append(b);}
    const b=pin.b;pin.e=e;pin.lat=pos.lat;pin.lon=pos.lon;pin.active=active;
    if(pin.art!==art){b.replaceChildren(buildArtwork(e));pin.art=art;}
    if(e.component==='air'){const svg=b.querySelector('svg'),rot=e.position.headingDeg!=null?'rotate('+e.position.headingDeg+'deg)':'';
      if(svg&&svg.style.transform!==rot)svg.style.transform=rot;}
    if(b.className!==cls)b.className=cls;
    if(pos.hop){b.classList.remove('hop');void b.offsetWidth;b.classList.add('hop');}
    const title=prefix+e.label+' · '+(pos.est?'estimated position':e.quality);
    if(b.title!==title){b.title=title;b.setAttribute('aria-label',prefix+e.label+' '+(pos.est?'estimated position':e.quality)+(active?' selected':''));}
    if(b.getAttribute('aria-pressed')!==String(active))b.setAttribute('aria-pressed',String(active));
    // Moved with a transform (no page re-layout), which phones handle on the graphics chip.
    const tf=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) ${anchorOf(e.component)}${active?' scale(1.17)':''}`;
    if(pos.hop&&canGlide&&!pos.jump){
      // reappear one update back along its way and glide on at once, in step with everything else
      // (no standing still until the next update)
      const back=estimate(e,now-glideMs);
      if(back){const [bx,by]=pixel(back.lon,back.lat);
        b.style.transform=`translate(${bx.toFixed(1)}px,${by.toFixed(1)}px) ${anchorOf(e.component)}${active?' scale(1.17)':''}`;
        void b.offsetWidth;b.classList.add('glide');}
      b.style.transform=tf;pin.tf=tf;
    }else if(pin.tf!==tf){b.style.transform=tf;pin.tf=tf;}
    b.onclick=()=>{if(active){state.selected=null;selected.classList.add('hidden');paint();}else selectEntity(e);};
  }
  for(const [k,pin] of pins)if(!keep.has(k)){pin.b.remove();pins.delete(k);}
  if(shown.size>keep.size*4+4000)for(const k of shown.keys())if(!keep.has(k))shown.delete(k);
  updateSelectedInfo(now);
}
function pin0(k){const p=pins.get(k);return p&&p.tf?p:null;}
function currentEntity(sel){const f=state.frames[sel.component];return f?.entities.find(x=>x.id===sel.id)||null;}
function updateSelectedInfo(now){
  if(!state.selected||selected.classList.contains('hidden'))return;
  const e=currentEntity(state.selected);if(!e)return;
  const info=selected.querySelector('small');if(!info)return;
  const pos=whereNow(e,now),text=infoText(e,!!pos?.est);if(info.textContent!==text)info.textContent=text;
}
// Up to ten updates a second while something on screen is moving and the page is visible.
// The pace adapts to the device: if an update takes longer (busy map, slow phone), updates
// come less often (down to about three a second) so the page always stays responsive.
let lastTick=0,tickCost=0,glideMs=GLIDE_MS;
let lastRaf=0;
// Icons not placed for a while although things are moving = the page wasn't being drawn.
function notDrawing(){return document.visibilityState!=='visible'||(lastDrawAt>0&&performance.now()-lastDrawAt>Math.max(3000,glideMs*2.5));}
function motionLoop(ts){
  requestAnimationFrame(motionLoop);
  {const was=lastRaf;lastRaf=performance.now();if(was&&lastRaf-was>1500)resumeDrawing();}
  if(document.visibilityState==='visible'&&(moving||blends.size)&&notDrawing())resumeDrawing();
  if(document.visibilityState!=='visible'||!moving&&!blends.size)return;
  // (TPA_HQ_TEST_MIN_GAP: tests only, to imitate a slow device.)
  const gap=REDUCED?2000:Math.max(GLIDE_MS,tickCost*12,Number(window.TPA_HQ_TEST_MIN_GAP)||0);
  if(ts-lastTick<gap)return;
  // 4.1.5: each glide lasts exactly as long as the real gap between updates, so a busy
  // device never shows "move, pause, move" (the animation can't finish early and wait).
  const real=lastTick?Math.min(4000,ts-lastTick):GLIDE_MS;
  if(Math.abs(real-glideMs)>glideMs*0.1){glideMs=Math.round(real);markerLayer.style.setProperty('--glide',glideMs+'ms');}
  lastTick=ts;const t0=performance.now();markers();tickCost=tickCost*0.7+(performance.now()-t0)*0.3;
}
requestAnimationFrame(motionLoop);
function layerName(id){return ($(id+'-toggle').closest('.layer-toggle')?.querySelector('strong')?.textContent||id).toLowerCase();}
function counts(){const d=dims();const anyLive=IDS.some(id=>state.live[id]&&!state.parked[id]);
for(const id of IDS){let entries=state.frames[id]?.entities;
let count=(entries||[]).filter(e=>{if(!e.position)return false;let [x,y]=pixel(e.position.lon,e.position.lat);return x>=0&&x<=d.w&&y>=0&&y<=d.h;}).length;
const status=$(id+'-source-status'),chip=$(id+'-chip').querySelector('strong'),n=$(id+'-count');
if(state.parked[id]){status.textContent='Unavailable · coming soon';chip.textContent='SOON';if(n)n.textContent='—';continue;}
if(state.live[id]){
  // 4.0.10: a real engine is connected. Plain wording; never the word MOCK.
  const overlay=state.frames[id]?.overlay,notice=state.frames[id]?(state.frames[id].notice||''):(state.notice[id]||'');
  const tooFar=overlay&&state.z<overlay.minZoom;
  status.textContent=!state.visible[id]?'Hidden':
    state.status[id]==='ready'?(tooFar?'Zoom in to see '+layerName(id):notice||(overlay?'Live · showing on map':count+' in view')):
    state.status[id]==='stale'?(notice||'Delayed · showing last update'):
    (notice||'Unavailable · will retry');
  chip.textContent=!state.visible[id]?'OFF':state.status[id]==='ready'?'LIVE':state.status[id]==='stale'?'DELAYED':'OFF AIR';
  if(n)n.textContent=entries&&!overlay?String(count):'—';continue;}
status.textContent=!state.visible[id]?'Hidden':state.status[id]==='ready'?count+' MOCK in view':state.status[id]==='unavailable'&&entries?.length?'UNAVAILABLE · stale last-known MOCK':state.status[id]==='stale'&&entries?.length?'STALE · last-known MOCK':({not_installed:'Not connected yet',unavailable:'Unavailable',stale:'Delayed'}[state.status[id]]||state.status[id]);
chip.textContent=!state.visible[id]?'OFF':state.status[id]==='ready'?'MOCK':state.status[id]==='not_installed'?'EMPTY':state.status[id].toUpperCase();
if(n)n.textContent=entries?String(count):'—';}
const anyMock=IDS.some(id=>!state.live[id]&&!state.parked[id]&&state.frames[id]?.entities?.length);
document.querySelector('.onair strong').textContent=anyLive?(anyMock?'TEST · LIVE + MOCK LAYERS':'TEST VERSION · LIVE DATA'):'MOCK ICON REVIEW · NOT LIVE';
const banner=document.querySelector('.mock-banner');
if(banner){if(!banner.dataset.original)banner.dataset.original=banner.textContent;
  const liveNames=IDS.filter(id=>state.live[id]&&!state.parked[id]).map(layerName);
  banner.textContent=anyLive?'TEST VERSION · LIVE: '+liveNames.join(', ').toUpperCase()+(anyMock?' · OTHER ICONS ARE PRETEND':' · OTHER LAYERS NOT CONNECTED YET'):banner.dataset.original;
  // 4.3.0 (owner 1 Nov 13:11: "can you get rid of this to somewhere off map?"): when everything shown is
  // real, the notice moves to the side menu under the status line; while any pretend icons are on the
  // map it stays on the map, so they can never be mistaken for live ones.
  const onair=document.querySelector('.onair'),toMenu=anyLive&&!anyMock;
  if(toMenu&&onair&&!banner.classList.contains('in-sidebar')){window.__tpaBannerHome=banner.parentNode;onair.after(banner);banner.classList.add('in-sidebar');}
  else if(!toMenu&&banner.classList.contains('in-sidebar')&&window.__tpaBannerHome){window.__tpaBannerHome.append(banner);banner.classList.remove('in-sidebar');}}
const statsTitle=document.querySelector('.stats-panel h2');if(statsTitle){if(!statsTitle.dataset.original)statsTitle.dataset.original=statsTitle.textContent;statsTitle.textContent=anyLive?'VEHICLES IN VIEW':statsTitle.dataset.original;}
const statsMicro=document.querySelector('.stats-panel .micro');if(statsMicro){if(!statsMicro.dataset.original)statsMicro.dataset.original=statsMicro.textContent;statsMicro.textContent=anyLive?'TEST VERSION':statsMicro.dataset.original;}
const note=document.querySelector('.stats-panel .panel-note');if(note){if(!note.dataset.original)note.dataset.original=note.textContent;note.textContent=anyLive?'Live layers show real reported positions. Layers marked MOCK are pretend.':note.dataset.original;}
}
// 4.1.10 speed panel (owner 31 Oct: "Map needs to be almost instant, tomtom layer next ... then the
// vehicles feeds"). Open the site with #speed on the end of the address to see, live, how long each
// part took to appear in THIS browser. Measuring only; nothing is sent anywhere.
const SPEED={first:Object.create(null),ask:Object.create(null),trafficFrom:0,trafficLast:null,mapFrom:0,mapLast:null};
function speedMark(name){if(!(name in SPEED.first))SPEED.first[name]=performance.now();}
// 4.6.0 (owner 1 Nov 15:00: "a 5 second timer with 'Preparing streams' ... buys the browser time to build
// that user experience"): a list that ticks each layer the moment it is really ready on this screen.
// Never waits longer than needed; the panel for returning visitors gives up after PREP_MAX_MS.
const PREP_MAX_MS=5000;
const PREP_ITEMS=[['map','🗺','Map'],['road','≋','Traffic'],['bus','🚌','Buses'],['rail','🚂','Trains'],['air','✈','Planes']];
const prepLists=new Set();
function prepWanted(id){
  if(id==='map')return true;
  if(!state.live[id]||state.parked[id]||!state.visible[id])return false;
  if(state.status[id]!=='ready'&&state.status[id]!=='stale')return false;     // not answering: don't hold anyone up
  if(id==='road'){const o=state.frames.road?.overlay;return !!o&&state.z>=o.minZoom;}
  return true;
}
function prepReady(id){
  if(id==='map')return 'street map drawn' in SPEED.first||state.renderer==='fallback'||state.renderer==='leaflet-degraded';
  if(id==='road')return 'traffic shown' in SPEED.first;
  return (SPEED.ask[id]?.n||0)>0;
}
function makeReadyList(){
  const box=el('div','prep-list');box.setAttribute('aria-live','polite');
  const items={};for(const [id,icon,name] of PREP_ITEMS){const row=el('span','prep-item');row.dataset.id=id;
    row.append(el('span','prep-icon',icon),el('span','prep-name',name),el('span','prep-tick','…'));items[id]=row;box.append(row);}
  prepLists.add({box,items});updatePrep();return box;
}
function updatePrep(){
  let all=true;
  for(const {box,items} of prepLists){
    if(!box.isConnected&&box.dataset.started)continue;box.dataset.started='1';
    for(const [id] of PREP_ITEMS){const row=items[id],want=prepWanted(id),ok=!want||prepReady(id);
      row.hidden=!want;row.classList.toggle('done',ok);const t=row.querySelector('.prep-tick');t.textContent=ok?'✓':'…';if(!ok)all=false;}
  }
  return all;
}
function startPreparing(showPanel){
  const t0=performance.now();let panel=null;
  if(showPanel){panel=el('div','prep-panel');panel.setAttribute('role','status');
    panel.append(el('strong','prep-title','Preparing streams…'),makeReadyList());document.body.append(panel);}
  const timer=setInterval(()=>{
    const done=updatePrep(),waited=performance.now()-t0;
    if(panel&&(done||waited>PREP_MAX_MS)){const p=panel;panel=null;if(done)p.querySelector('.prep-title').textContent='All set';
      p.classList.add('fading');setTimeout(()=>p.remove(),done?700:400);}
    if((done&&!panel)||waited>30000)clearInterval(timer);
  },200);
}
window.TPA_HQ_SPEED_MARK=speedMark;
window.TPA_HQ_MAP_IDLE=()=>{speedMark('street map complete');if(SPEED.mapFrom){SPEED.mapLast=performance.now()-SPEED.mapFrom;SPEED.mapFrom=0;}};
function speedPanel(){
  if(!/speed/i.test(location.hash))return;
  let box=$('speed-panel');
  if(!box){box=document.createElement('pre');box.id='speed-panel';
    Object.assign(box.style,{position:'fixed',left:'8px',bottom:'8px',zIndex:9999,margin:0,padding:'8px 10px',background:'#071323ee',color:'#bff3ff',
      font:'12px/1.45 ui-monospace,Consolas,monospace',borderRadius:'8px',pointerEvents:'none',whiteSpace:'pre',maxWidth:'96vw',overflow:'hidden'});
    document.body.append(box);}
  const s=v=>v==null?'—':(v/1000).toFixed(1)+' s',ms=v=>v==null?'—':Math.round(v)+' ms';
  const nav=performance.getEntriesByType?.('navigation')?.[0],res=n=>performance.getEntriesByType?.('resource')?.find(r=>r.name.includes(n));
  const L1=['TPA SPEED (this browser, since the page started)',
    'Page arrived          '+s(nav?.responseEnd)+'   map library '+ms(res('maplibre-gl.js')?.duration)+' ('+Math.round((res('maplibre-gl.js')?.transferSize||0)/1024)+' KB)',
    'Street map drawn      '+s(SPEED.first['street map drawn'])+'   complete '+s(SPEED.first['street map complete'])+'   after last move '+ms(SPEED.mapLast),
    'Traffic first shown   '+s(SPEED.first['traffic shown'])+'   after last move '+ms(SPEED.trafficLast)];
  for(const id of ['bus','rail','air']){const a=SPEED.ask[id];
    L1.push((id==='bus'?'Buses':id==='rail'?'Trains':'Aircraft').padEnd(10)+' first shown '+s(SPEED.first[id+' shown'])+'   server answer last '+ms(a?.last)+' avg '+ms(a?.n?a.sum/a.n:null)+' ('+(a?.n||0)+')');}
  box.textContent=L1.join('\n');
}
setInterval(speedPanel,1000);
function paint(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;drawFallback();markers();counts();syncOverlays();liveCredit();$('zoom-level').textContent='ZOOM '+state.z.toFixed(1);});}
function validNum(n){return typeof n==='number'&&Number.isFinite(n);}
function checkFrame(id,f){if(!f||typeof f!=='object'||Array.isArray(f)||f.component!==id||f.contractVersion!=='1.0'||!['ready','stale','unavailable','not_installed'].includes(f.health)||typeof f.receivedAt!=='string'||!Number.isFinite(Date.parse(f.receivedAt))||typeof f.expiresAt!=='string'||!Number.isFinite(Date.parse(f.expiresAt))||Date.parse(f.expiresAt)<Date.parse(f.receivedAt)||typeof f.attribution!=='string'||f.attribution.length>256||!Array.isArray(f.entities)||f.entities.length>10000)throw Error('Invalid component v1 envelope');
const ids=new Set(),entities=[];for(const e of f.entities){if(!e||typeof e.id!=='string'||!e.id||e.id.length>128||ids.has(e.id)||typeof e.label!=='string'||e.label.length>160||!['observed','estimated','stale'].includes(e.quality)||!Number.isFinite(Date.parse(e.observedAt)))throw Error('Invalid entity');ids.add(e.id);
const p=e.position;if(p!==null&&(!p||!validNum(p.lon)||!validNum(p.lat)||Math.abs(p.lon)>180||Math.abs(p.lat)>90||(p.headingDeg!=null&&(!validNum(p.headingDeg)||p.headingDeg<0||p.headingDeg>360))))throw Error('Invalid point');const position=p===null?null:{lon:p.lon,lat:p.lat,headingDeg:p.headingDeg??null};
entities.push({component:id,id:e.id,label:e.label,quality:e.quality,observedAt:e.observedAt,badge:typeof e.badge==='string'&&/^[A-Za-z0-9]{1,8}$/.test(e.badge)?e.badge:null,position,motion:checkMotion(e.motion,position)});}
// 4.0.10 optional extras: a short notice, and a see-through picture layer served
// only from this socket's own same-origin tile path. Anything else is ignored.
const notice=typeof f.notice==='string'?f.notice.slice(0,120):'';
let overlay=null;const o=f.overlay;
if(o&&typeof o==='object'&&o.type==='raster'&&o.tiles===`/api/v4/components/${id}/tiles/{z}/{x}/{y}.png`&&Number.isInteger(o.minZoom)&&Number.isInteger(o.maxZoom)&&o.minZoom>=0&&o.maxZoom<=22&&o.minZoom<=o.maxZoom&&validNum(o.opacity)&&o.opacity>0&&o.opacity<=1)
  overlay={tiles:o.tiles,minZoom:o.minZoom,maxZoom:o.maxZoom,opacity:o.opacity,refreshSeconds:Number.isInteger(o.refreshSeconds)?clamp(o.refreshSeconds,60,3600):300,
    tileSize:o.tileSize===512?512:256,
    // 4.3.0 optional (owner 1 Nov 13:06: thick traffic lines "give the impression buses are not quite on the
    // roads"): a different see-through level from a zoom level on, and colours made fully clear from a zoom
    // level on (e.g. free-flowing traffic, so only hold-ups are drawn close in). Generic: HQ knows no colours.
    opacitySteps:Array.isArray(o.opacityByZoom)?o.opacityByZoom.filter(q=>Array.isArray(q)&&q.length===2&&Number.isInteger(q[0])&&q[0]>=0&&q[0]<=22&&validNum(q[1])&&q[1]>=0&&q[1]<=1).slice(0,8).sort((a,b)=>a[0]-b[0]):[],
    hide:Array.isArray(o.hideColours)&&Number.isInteger(o.hideFromZoom)&&o.hideFromZoom>=0&&o.hideFromZoom<=22?
      {from:o.hideFromZoom,tol:validNum(o.hideTolerance)?clamp(o.hideTolerance,0,120):40,
       rgb:o.hideColours.filter(c=>typeof c==='string'&&/^#[0-9a-fA-F]{6}$/.test(c)).slice(0,12).map(c=>[1,3,5].map(i=>parseInt(c.slice(i,i+2),16)))}:null};
const pollMs=Number.isInteger(f.nextPollAfterMs)?clamp(f.nextPollAfterMs,5000,120000):30000;
return {component:id,health:f.health==='ready'&&Date.parse(f.expiresAt)<Date.now()?'stale':f.health,entities,attribution:f.attribution,notice,overlay,pollMs};}
// 4.1.0 optional motion hint from an engine. HQ only does generic arithmetic with it
// (straight-line "carry on at this speed" or "head for this point by this time");
// anything malformed is ignored and the vehicle simply stays at its reported position.
function checkMotion(m,p){
  if(!m||typeof m!=='object'||!p)return null;
  if(m.mode==='heading'&&validNum(m.speedMps)&&m.speedMps>0&&m.speedMps<=350&&validNum(m.maxSeconds)&&m.maxSeconds>0&&m.maxSeconds<=300&&p.headingDeg!=null)
    // 4.1.3 optional slowToStop: ease from the reported speed to a standstill at maxSeconds.
    return {mode:'heading',speedMps:m.speedMps,maxSeconds:m.maxSeconds,slow:m.slowToStop===true};
  if(m.mode==='toward'&&validNum(m.lon)&&validNum(m.lat)&&Math.abs(m.lon)<=180&&Math.abs(m.lat)<=90&&typeof m.arriveAt==='string'&&Number.isFinite(Date.parse(m.arriveAt))&&validNum(m.maxSeconds)&&m.maxSeconds>0&&m.maxSeconds<=7800&&Math.abs(m.lon-p.lon)<1.5&&Math.abs(m.lat-p.lat)<1)
    return {mode:'toward',lon:m.lon,lat:m.lat,arriveAtMs:Date.parse(m.arriveAt),maxSeconds:m.maxSeconds};
  // 4.1.1 "path": follow these points (e.g. along the railway) and arrive by arriveAt.
  if(m.mode==='path'&&Array.isArray(m.points)&&m.points.length>=2&&m.points.length<=200&&typeof m.arriveAt==='string'&&Number.isFinite(Date.parse(m.arriveAt))&&validNum(m.maxSeconds)&&m.maxSeconds>0&&m.maxSeconds<=7800){
    const pts=[];
    for(const q of m.points){if(!Array.isArray(q)||q.length!==2||!validNum(q[0])||!validNum(q[1])||Math.abs(q[0]-p.lon)>2||Math.abs(q[1]-p.lat)>2)return null;pts.push([q[0],q[1]]);}
    if(Math.abs(pts[0][0]-p.lon)>0.05||Math.abs(pts[0][1]-p.lat)>0.05)return null;
    const k=Math.cos(p.lat*Math.PI/180),cum=[0];
    for(let i=1;i<pts.length;i++)cum.push(cum[i-1]+Math.hypot((pts[i][0]-pts[i-1][0])*k,pts[i][1]-pts[i-1][1]));
    // 4.1.6 optional speedMps (+ slowToStop): travel along the path at that speed (easing to a
    // stop like "heading") instead of spreading the path evenly until arriveAt. Buses on roads.
    const speed=validNum(m.speedMps)&&m.speedMps>0&&m.speedMps<=350?m.speedMps:null;
    return {mode:'path',pts,cum,arriveAtMs:Date.parse(m.arriveAt),maxSeconds:m.maxSeconds,speedMps:speed,slow:speed!==null&&m.slowToStop===true};
  }
  // 4.2.0 "timed" (bus engine 0.7.0, owner 1 Nov 11:37 "option 1"): follow these points, being at each
  // one at its own clock time (seconds since 1970). Lets an engine replay a vehicle's real reported
  // journey a little behind real time, so nothing on screen is a guess that later needs correcting.
  if(m.mode==='timed'&&Array.isArray(m.points)&&Array.isArray(m.times)&&m.points.length>=1&&m.points.length<=200&&m.times.length===m.points.length){
    const pts=[],times=[];
    for(let i=0;i<m.points.length;i++){const q=m.points[i],t=m.times[i];
      if(!Array.isArray(q)||q.length!==2||!validNum(q[0])||!validNum(q[1])||Math.abs(q[0]-p.lon)>2||Math.abs(q[1]-p.lat)>2||!validNum(t))return null;
      if(i&&t<times[i-1]/1000)return null;pts.push([q[0],q[1]]);times.push(t*1000);}
    if(Math.abs(pts[0][0]-p.lon)>0.05||Math.abs(pts[0][1]-p.lat)>0.05||times[times.length-1]-times[0]>7800000)return null;
    const delay=validNum(m.delaySeconds)&&m.delaySeconds>=0&&m.delaySeconds<=600?Math.round(m.delaySeconds):0;
    const jumps=[],kk=Math.cos(p.lat*Math.PI/180);
    for(let i=1;i<pts.length;i++)if(times[i]-times[i-1]<=200&&Math.hypot((pts[i][0]-pts[i-1][0])*kk,pts[i][1]-pts[i-1][1])*111195>30)jumps.push(times[i]);
    return {mode:'timed',pts,times,delay,jumps};
  }
  return null;
}
// Only same-origin data. Abort slow snapshots; one spoke must not delay another.
async function requestBounded(url,maxBytes,timeoutMs){
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),timeoutMs);
  try{
    const response=await fetch(url,{cache:'no-store',signal:ctl.signal});
    if(!response.ok || Number(response.headers.get('content-length'))>maxBytes)throw Error('unavailable');
    const body=await response.text();
    if(new TextEncoder().encode(body).byteLength>maxBytes)throw Error('oversize');
    return JSON.parse(body);
  }finally{clearTimeout(timer);}
}
function deselectComponent(id){
  if(state.selected?.component===id){state.selected=null;selected.classList.add('hidden');}
}
// 4.0.10: each engine is asked only for what is on screen (plus a margin).
function viewQuery(){
  let w=-12.5,s=49,e=4.5,n=61.8;
  if(map&&typeof map.getBounds==='function'){const b=map.getBounds().pad(0.2);w=b.getWest();s=b.getSouth();e=b.getEast();n=b.getNorth();}
  const f=v=>clamp(v,-180,180).toFixed(4);
  return `west=${f(w)}&south=${clamp(s,-85,85).toFixed(4)}&east=${f(e)}&north=${clamp(n,-85,85).toFixed(4)}`;
}
// 4.0.10: a socket may supply a see-through picture layer (e.g. coloured roads).
// HQ only places it on the map; the engine decides what the pictures show.
let overlayPane=null;
function overlayOpacity(o,z){let op=o.opacity;for(const [zz,v] of o.opacitySteps||[])if(z>=zz)op=v;return op;}
function clearColours(canvas,hide){
  const ctx=canvas.getContext('2d',{willReadFrequently:true}),img=ctx.getImageData(0,0,canvas.width,canvas.height),d=img.data,cs=hide.rgb,tol=hide.tol;
  for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;const r=d[i],g=d[i+1],b=d[i+2];
    for(const c of cs)if(Math.abs(r-c[0])+Math.abs(g-c[1])+Math.abs(b-c[2])<=tol){d[i+3]=0;break;}}
  ctx.putImageData(img,0,0);
}
function makeTileLayer(url,opts,hide){
  if(!hide||!hide.rgb.length||typeof L.TileLayer?.extend!=='function')return L.tileLayer(url,opts);
  const Clearing=L.TileLayer.extend({createTile(coords,done){
    const canvas=document.createElement('canvas'),size=this.getTileSize();canvas.width=size.x;canvas.height=size.y;
    const img=new Image();
    img.onload=()=>{try{const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,size.x,size.y);if(coords.z>=hide.from)clearColours(canvas,hide);}catch(e){}done(null,canvas);};
    img.onerror=e=>done(e,canvas);img.src=this.getTileUrl(coords);return canvas;}});
  return new Clearing(url,opts);
}
function applyOverlayOpacity(){
  if(!map)return;const z=map.getZoom();
  for(const id of IDS){const have=state.overlays[id],o=state.frames[id]?.overlay;if(have&&o&&!have.next)have.layer.setOpacity(overlayOpacity(o,z));}
}
function syncOverlays(){
  // Only a real Leaflet map can hold picture layers; anything else keeps working without them.
  if(!map||!window.L||typeof map.createPane!=='function'||typeof L.tileLayer!=='function')return;
  if(!overlayPane){overlayPane=map.createPane('tpa-socket-overlays');overlayPane.style.zIndex='450';overlayPane.style.pointerEvents='none';}
  for(const id of IDS){
    const o=state.frames[id]?.overlay,have=state.overlays[id];
    const want=!!o&&state.visible[id]&&!state.parked[id]&&(state.status[id]==='ready'||state.status[id]==='stale');
    if(!want){if(have){map.removeLayer(have.layer);if(have.next)map.removeLayer(have.next.layer);delete state.overlays[id];}continue;}
    const stamp=Math.floor(Date.now()/1000/o.refreshSeconds);
    const url=o.tiles+'?v='+stamp;
    if(have&&(have.url===url||have.next?.url===url))continue;
    // 4.1.11 (owner 31 Oct 22:08: traffic "loading in chunks whenever it likes"):
    // - pictures load while the map is still moving, and more are kept just off-screen;
    // - 512-pixel pictures when the engine offers them (a quarter as many requests);
    // - a refresh loads the new pictures underneath and only then removes the old ones,
    //   instead of blanking the whole layer and reloading it.
    const big=o.tileSize===512;
    const layer=makeTileLayer(url,{pane:'tpa-socket-overlays',minZoom:o.minZoom,maxNativeZoom:o.maxZoom,maxZoom:18,opacity:overlayOpacity(o,map.getZoom()),
      tileSize:big?512:256,zoomOffset:big?-1:0,updateWhenIdle:false,updateWhenZooming:false,keepBuffer:3,crossOrigin:false,
      // Leaflet shows attribution as HTML, so escape the engine's plain text first.
      attribution:String(state.frames[id]?.attribution||'').replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';')},o.hide);
    layer.on('loading',()=>{SPEED.trafficFrom=performance.now();});
    layer.on('load',()=>{speedMark('traffic shown');if(SPEED.trafficFrom){SPEED.trafficLast=performance.now()-SPEED.trafficFrom;SPEED.trafficFrom=0;}});
    if(!have){layer.addTo(map);state.overlays[id]={layer,url};continue;}
    if(have.next)map.removeLayer(have.next.layer);
    have.next={layer,url};
    layer.setOpacity(0);                           // invisible until every picture has arrived
    layer.once('load',()=>{const cur=state.overlays[id];if(!cur||cur.next?.layer!==layer)return;
      layer.setOpacity(overlayOpacity(o,map.getZoom()));map.removeLayer(cur.layer);state.overlays[id]={layer,url};});
    layer.addTo(map);
  }
}
let refetchTimer=0;
function refetchLiveView(){clearTimeout(refetchTimer);refetchTimer=setTimeout(()=>{
  const v=currentBox(0);
  for(const id of IDS)if(state.live[id]&&!state.parked[id]&&!state.frames[id]?.overlay&&(state.status[id]==='ready'||state.status[id]==='stale')){
    // 4.1.0: nothing to fetch if the last answer already covered this whole view.
    const b=fetchedBox[id];
    if(v&&b&&b.complete&&v.w>=b.w&&v.e<=b.e&&v.s>=b.s&&v.n<=b.n)continue;
    getComponent(id);
  }
},300);}   // 4.1.13: was 0.8 s - a new area's vehicles are asked for sooner
// 4.1.0: each live layer is asked again at the pace its engine suggests (nextPollAfterMs),
// only while the page is visible; a reply older than one already shown is ignored.
const pollTimers=Object.create(null),lastFetch=Object.create(null),seqSent=Object.create(null),seqDone=Object.create(null),fetchedBox=Object.create(null);
function schedulePoll(id,ms){clearTimeout(pollTimers[id]);pollTimers[id]=setTimeout(()=>{pollTimers[id]=0;
  if(!state.live[id]||state.parked[id])return;
  // 4.1.14 (owner 31 Oct 23:19: "If we can keep a hidden window alive or at least pretending to be"):
  // keep collecting reports while the page is hidden, so coming back shows up-to-date positions at once.
  if(!state.visible[id]){schedulePoll(id,ms);return;}
  getComponent(id);},ms);}
// 4.1.13 (owner 31 Oct 22:46: after tabbing away and back "it always has a catch up, and thats when
// the buses and trains move about wrong"). Browsers pause animation in hidden tabs, so on return the
// icons were far behind and every difference was treated as a correction (waiting, racing). Now coming
// back is a fresh start: icons are placed where they should be now, at once, and the first new reports
// after returning are applied directly (anything far off fades in at its real place).
// 4.1.14: the same when the page simply wasn't being drawn (another window in front, minimised, the
// computer asleep): HQ notices the gap in drawing itself, whatever the browser says about visibility.
const freshStart=Object.create(null);
function resumeDrawing(){
  blends.clear();viewSig='';
  for(const id of IDS){freshStart[id]=true;
    if(state.live[id]&&!state.parked[id]&&state.status[id]!=='not_installed'&&Date.now()-(lastFetch[id]||0)>4000)getComponent(id);}
  paint();}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')resumeDrawing();});
function currentBox(pad){if(!map||typeof map.getBounds!=='function')return null;const b=pad?map.getBounds().pad(pad):map.getBounds();return {w:b.getWest(),s:b.getSouth(),e:b.getEast(),n:b.getNorth()};}
async function getComponent(id){
  const seq=seqSent[id]=(seqSent[id]||0)+1,box=currentBox(0.2);lastFetch[id]=Date.now();
  try{
    const asked=performance.now();
    const f=checkFrame(id,await requestBounded(`/api/v4/components/${id}/snapshot?${viewQuery()}`,1200000,6000));
    {const a=SPEED.ask[id]||(SPEED.ask[id]={n:0,sum:0,last:0});a.last=performance.now()-asked;a.n++;a.sum+=a.last;if(f.entities.length)speedMark(id+' shown');}
    if(seq<(seqDone[id]||0))return;
    seqDone[id]=seq;
    fetchedBox[id]=box?{...box,complete:!f.notice}:null;
    if(state.live[id])schedulePoll(id,f.pollMs);
    noteNewFrame(f,Date.now(),freshStart[id]);freshStart[id]=false;
    // Once a new manifest has removed a component, ignore late responses.
    if(state.status[id]==='not_installed'||state.parked[id])return;
    if(f.health==='not_installed'){delete state.frames[id];deselectComponent(id);}
    else state.frames[id]=f;
    state.status[id]=f.health;
    if(f.health!=='ready')deselectComponent(id);
    // 4.0.11: a live socket that is still loading this area is asked again soon,
    // so new things appear within seconds instead of at the next 30-second check.
    if(state.live[id]&&!f.overlay&&f.notice&&!f.entities.length)scheduleSoon(id);
  }catch(_){
    state.status[id]='unavailable';deselectComponent(id);
  }
  paint();
}
const soonTimers=Object.create(null);
function scheduleSoon(id){if(soonTimers[id])return;soonTimers[id]=setTimeout(()=>{soonTimers[id]=0;if(state.live[id]&&!state.parked[id])getComponent(id);},5000);}
let discovering=false;
function setParked(id,parked){
  if(state.parked[id]===parked)return;
  state.parked[id]=parked;
  const toggle=$(id+'-toggle'),row=toggle.closest('.layer-toggle');
  toggle.disabled=parked;toggle.checked=!parked;state.visible[id]=!parked;
  row.classList.toggle('parked',parked);
  row.setAttribute('aria-disabled',parked?'true':'false');
  row.title=parked?'Coming soon':'';
}
async function discover(){
  if(discovering)return;discovering=true;
  try{
    const data=await requestBounded('/api/v4/manifest',32768,5000);
    if(data.contractVersion!=='1.0'||!Array.isArray(data.components)||data.components.length>5)throw Error('manifest-contract');
    const seen=new Set();
    for(const entry of data.components){
      if(!entry||!IDS.includes(entry.id)||seen.has(entry.id)||!['ready','unavailable','not_installed','stale'].includes(entry.status))throw Error('manifest-invalid');
      seen.add(entry.id);
    }
    const requests=[];
    for(const id of IDS){
      const entry=data.components.find(e=>e.id===id);
      // 4.0.9: a socket the server reports as parked is "coming soon": no data,
      // toggle off and locked. Same code for every socket; nothing is hard-wired.
      setParked(id,entry?.parked===true);
      state.live[id]=entry?.live===true;
      state.notice[id]=typeof entry?.notice==='string'?entry.notice.slice(0,80):'';
      if(state.parked[id]){
        state.status[id]='unavailable';delete state.frames[id];deselectComponent(id);
      }else if(entry?.status==='ready'){
        // Start all five requests independently rather than awaiting each in series.
        if(state.status[id]==='not_installed')state.status[id]='unavailable';
        if(!(state.live[id]&&pollTimers[id]&&Date.now()-(lastFetch[id]||0)<25000))requests.push(getComponent(id));
      }else if(entry?.status==='stale'||entry?.status==='unavailable'){
        state.status[id]=entry.status==='stale'?'stale':'unavailable';deselectComponent(id);
      }else{
        state.status[id]='not_installed';delete state.frames[id];deselectComponent(id);
      }
    }
    paint();
    await Promise.allSettled(requests);
  }catch(_){
    // Manifest outage: display prior frames as stale, never silently claim live.
    for(const id of IDS)if(state.status[id]==='ready'||state.status[id]==='stale'){
      state.status[id]='unavailable';deselectComponent(id);
    }
  }finally{discovering=false;paint();}
}
function search(query){let q=query.trim().toLowerCase(),out=$('target-list');out.replaceChildren();if(!q){const p=document.createElement('p');p.className='empty';p.textContent='Search a UK place. Vehicle targets will appear when independently connected.';out.append(p);return;}const places=POI.filter(([name])=>name.toLowerCase().includes(q)).slice(0,10).map(([name,lon,lat])=>({name,kind:'Place',action:()=>gotoPlace(lon,lat,11,name)}));const targets=listEntities().filter(e=>e.position&&(e.label.toLowerCase().includes(q)||e.id.toLowerCase().includes(q))).slice(0,10).map(e=>({name:e.label,kind:e.component.toUpperCase(),action:()=>{gotoPlace(e.position.lon,e.position.lat,13,e.label);selectEntity(e);}}));if(q==='uk'||'united kingdom'.includes(q))places.unshift({name:'United Kingdom overview',kind:'Region',action:fitUK});const items=[...places,...targets];if(!items.length){const p=document.createElement('p');p.className='empty';p.textContent='No matching place or connected target.';out.append(p);return;}for(const item of items){const b=document.createElement('button');b.type='button';b.className='search-result';const name=document.createElement('strong');name.textContent=item.name;const type=document.createElement('small');type.textContent=item.kind;b.append(name,type);b.onclick=()=>{item.action();$('search').value=item.name;};out.append(b);}}
for(const id of IDS){$(id+'-toggle').addEventListener('change',()=>{state.visible[id]=$(id+'-toggle').checked;paint();});}
function clock(){$('localclock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false});}clock();setInterval(clock,1000);
$('home-button').onclick=home;$('zoom-in').onclick=()=>zoom(1);$('zoom-out').onclick=()=>zoom(-1);$('menu-button').onclick=()=>setMenu(!$('sidebar').classList.contains('open'));$('sidebar-close').onclick=()=>setMenu(false);$('sidebar-backdrop').onclick=()=>setMenu(false);$('search').addEventListener('input',e=>search(e.target.value));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('sidebar').classList.remove('open');selected.classList.add('hidden');}});
window.addEventListener('resize',()=>{if(map)map.invalidateSize();paint();});
// Fallback drag/scroll remains usable only if MapLibre is unavailable.
root.addEventListener('wheel',e=>{if(map)return;e.preventDefault();zoom(e.deltaY<0?1:-1);},{passive:false});
root.addEventListener('pointerdown',e=>{if(map||e.button!==0||e.target.closest('button,input'))return;pan={x:e.clientX,y:e.clientY};});
root.addEventListener('pointermove',e=>{if(map||!pan)return;const dx=e.clientX-pan.x,dy=e.clientY-pan.y;pan={x:e.clientX,y:e.clientY};const c=merc(state.lon,state.lat,state.z),p=inverse(c[0]-dx,c[1]-dy,state.z);state.lon=clamp(p.lon,-180,180);state.lat=clamp(p.lat,-80,80);paint();});
root.addEventListener('pointerup',()=>{pan=null;});root.addEventListener('pointercancel',()=>{pan=null;});
root.tabIndex=0;root.addEventListener('keydown',e=>{if(map)return;if(e.key==='+'||e.key==='=')zoom(1);if(e.key==='-')zoom(-1);});
async function diagnose(){try{const r=await fetch('/hq-map-status',{cache:'no-store'});if(!r.ok)return;const data=await r.json();let error=data.upstreamErrors||{};let message=error['blocked-map-path']?.code||error.openfreemap?.code||error['maplibre-library']?.code||data.browser?.lastCode||'map-resource-unavailable';showNotice('OpenFreeMap map unavailable ('+message+'). UK geographic fallback active.',true);}catch(_){showNotice('Map connection unavailable. UK geographic fallback active.',true);}}
function beginMap(){
if(!window.L||!L.map){state.renderer='fallback';reportBrowser('library-missing','leaflet','Leaflet absent');diagnose();return;}
try{
  // Historical behaviour: Leaflet owns zoom/pan/markers; MapLibre only paints Liberty.
  map=L.map('vector-map',{zoomControl:false,preferCanvas:true,minZoom:3,maxZoom:18,worldCopyJump:true,
    attributionControl:false}).setView([HOME.lat,HOME.lon],HOME.z);
  L.control.attribution({position:'bottomright',prefix:false}).addTo(map);
  state.renderer='leaflet-waiting';
  // 4.1.7: a plain drag only shifts the icon layer (cheap); the icons are fully re-placed at most
  // four times a second while dragging, so ones coming into view appear promptly.
  map.on('move',()=>{
    const c=map.getCenter();state.lon=c.lng;state.lat=c.lat;
    if(!shiftLayer()||performance.now()-lastFull>250)syncView();
  });
  map.on('zoom zoomend',()=>syncView());
  map.on('zoomanim',zoomAnimStart);
  map.on('movestart',()=>{SPEED.mapFrom=performance.now();});
  map.on('zoomend',zoomAnimEnd);
  map.on('zoomend',applyOverlayOpacity);
  map.on('moveend',refetchLiveView);
  // 4.1.2: while the map moves, markers follow it instantly (no gliding lag).
  if(typeof map.on==='function')map.on('movestart zoomstart',()=>{mapMoving=true;for(const p of pins.values())p.b.classList.remove('glide');});
  if(typeof map.on==='function')map.on('moveend zoomend',()=>{mapMoving=false;paint();});
  // 4.1.2: the mouse wheel and click-and-drag work over vehicle icons too (owner, 30 Oct):
  // they are handed to the map underneath. A plain click still opens the icon's details.
  const mapBox=typeof map.getContainer==='function'?map.getContainer():null;
  if(mapBox){
  markerLayer.addEventListener('wheel',ev=>{
    ev.preventDefault();
    mapBox.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,clientX:ev.clientX,clientY:ev.clientY,screenX:ev.screenX,screenY:ev.screenY,deltaX:ev.deltaX,deltaY:ev.deltaY,deltaZ:ev.deltaZ,deltaMode:ev.deltaMode,ctrlKey:ev.ctrlKey,shiftKey:ev.shiftKey,altKey:ev.altKey,metaKey:ev.metaKey}));
  },{passive:false});
  let downAt=null;
  markerLayer.addEventListener('click',ev=>{
    // A press that turned into a map drag is not a click on the icon.
    if(downAt&&Math.hypot(ev.clientX-downAt[0],ev.clientY-downAt[1])>6){ev.stopPropagation();ev.preventDefault();}
    downAt=null;
  },true);
  markerLayer.addEventListener('mousedown',ev=>{
    if(ev.button!==0)return;
    downAt=[ev.clientX,ev.clientY];
    mapBox.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:ev.clientX,clientY:ev.clientY,screenX:ev.screenX,screenY:ev.screenY,button:0,buttons:1}));
  });
  }
  map.whenReady(()=>syncView());
  if(!window.TPA_BASEMAP?.attach){
    throw Error('TPA baseline loader not ready');
  }
  // 4.1.7: the server says whether the street map comes straight from OpenFreeMap (with the route
  // through HQ as a backup) or through HQ; anything unexpected means "through HQ" as before.
  const via='/basemap/openfreemap/styles/liberty';
  fetch('/hq-basemap',{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null).then(cfg=>{
  const direct=cfg&&cfg.mode==='direct'&&cfg.style==='https://tiles.openfreemap.org/styles/liberty';
  window.TPA_BASEMAP.attach(map,{style:direct?cfg.style:via,fallbackStyle:direct?via:null,
    attribution:'<a href="https://openfreemap.org/" target="_blank" rel="noopener">OpenFreeMap</a> | <a href="https://openmaptiles.org/" target="_blank" rel="noopener">&copy; OpenMapTiles</a> | <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>'});});
}catch(e){state.renderer='fallback';reportBrowser('map-constructor-error','leaflet-init',String(e?.message||e));map?.remove?.();map=null;diagnose();}}
window.TPA_HQ_VECTOR_READY=()=>{speedMark('street map drawn');ready=true;state.renderer='leaflet-maplibre';state.mapErrors=0;
  document.body.classList.add('vector-ready');notice.classList.add('hidden');syncView();};
window.TPA_HQ_VECTOR_ERROR=(message)=>{state.mapErrors++;const t=String(message||'Unknown source error');
  // A source error does not destroy Leaflet controls or the five sockets.
  // Keep fallback geography; never turn it invisible until the actual basemap loads.
  if(state.mapErrors>=1){ready=false;state.renderer='leaflet-degraded';document.body.classList.remove('vector-ready');paint();diagnose();}
};
fetch('/data/uk-geography.json').then(r=>r.ok?r.json():Promise.reject()).then(data=>{if(!Array.isArray(data.land)||!Array.isArray(data.coastlines))throw Error('geography');state.geo=data;paint();}).catch(()=>{showNotice('Local geography unavailable; map network still being tried.',true);});
Object.defineProperty(window,'TPA_HQ_READONLY_STATE',{value:()=>Object.freeze({lat:state.lat,lon:state.lon,zoom:state.z,renderer:state.renderer,mapErrors:state.mapErrors,geographyLoaded:!!state.geo,components:Object.freeze({...state.status})}),writable:false});
/* 4.0.9 welcome screen, optional tip link and Data Sources & Credits.
 * Text and links come only from /data/site-config.json (same origin, data only).
 * If the file is missing or invalid, the map works exactly as 4.0.8 did.
 * Built with textContent only: no HTML from the config is ever injected. */
const WELCOME_KEY='tpa-welcome-seen-v2';   // 4.5.0: new welcome screen, shown once more to everyone
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);return n;}
function safeLink(u){if(typeof u!=='string'||!u)return '';try{const x=new URL(u);return x.protocol==='https:'?x.href:'';}catch(_){return '';}}
function str(v,max){return typeof v==='string'&&v.length<=max?v:'';}
function tipControl(tip){
  const label='☕ '+(str(tip?.label,60)||'Buy me a coffee'),url=safeLink(tip?.url);
  if(url){const a=el('a','tip-button',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
  // 4.5.1 (owner 1 Nov 14:39: "add the buy me a coffee ... so i can see what it looks like"): with
  // "preview": true and no link yet, the button is shown but does nothing ("link coming soon").
  if(tip?.preview===true){const b=el('button','tip-button',label);b.type='button';b.disabled=true;b.title='Link coming soon';
    b.append(el('span','tip-soon',' · link coming soon'));return b;}
  return null;     // 4.5.0 (owner 1 Nov 14:15: "we can add the coffee part later"): no link yet = no button
}
// 4.5.0 (owner 1 Nov 14:13: the welcome screen should "obviously be fun"): a little parade of the map's
// own vehicle icons across the top (still for anyone who asks their device for less motion).
function welcomeParade(){
  const strip=el('div','welcome-parade');strip.setAttribute('aria-hidden','true');
  ['air','rail','bus','sea','air','bus'].forEach((c,i)=>{
    const pin=el('span','parade-pin '+c);pin.style.animationDelay=(-i*2.3)+'s';
    const art=buildArtwork({component:c,id:'parade',badge:'TPA',position:{headingDeg:c==='air'?90:null}});
    art.querySelector('.review-route')?.remove();          // no route number on the parade
    pin.append(art);strip.append(pin);});
  return strip;
}
let lastFocus=null;
function makeDialog(id,title){
  const back=el('div','tpa-dialog-back hidden');back.id=id;
  const card=el('div','tpa-dialog');card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');
  const h=el('h2','tpa-dialog-title',title);h.id=id+'-title';card.setAttribute('aria-labelledby',h.id);
  card.append(h);back.append(card);document.body.append(back);
  back.addEventListener('click',e=>{if(e.target===back)closeDialog(back);});
  return {back,card};
}
function openDialog(back){lastFocus=document.activeElement;back.classList.remove('hidden');back.querySelector('.tpa-primary,button,a')?.focus();}
function closeDialog(back){back.classList.add('hidden');if(back.id==='tpa-welcome'){try{localStorage.setItem(WELCOME_KEY,'1');}catch(_){}}lastFocus?.focus?.();}
document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.tpa-dialog-back:not(.hidden)').forEach(closeDialog);});
function buildCredits(list){
  const {back,card}=makeDialog('tpa-credits','Data sources & credits');
  const items=el('ul','credit-list');
  for(const c of (Array.isArray(list)?list.slice(0,20):[])){
    const layer=str(c?.layer,40),text=str(c?.text,400);if(!layer||!text)continue;
    const li=el('li','credit-item');li.append(el('strong','',layer));
    const status=str(c?.status,30);if(status)li.append(el('span','credit-status',status));
    li.append(el('p','',text));
    const url=safeLink(c?.url);if(url){const a=el('a','credit-link',url.replace(/^https:\/\//,'').replace(/\/$/,''));a.href=url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);}
    items.append(li);
  }
  const close=el('button','tpa-primary','Close');close.type='button';close.onclick=()=>closeDialog(back);
  card.append(items,close);return back;
}
function buildWelcome(w,tip,credits){
  const {back,card}=makeDialog('tpa-welcome',str(w?.title,80)||'Welcome to TPA');
  card.classList.add('welcome-card');
  card.prepend(welcomeParade());
  const sub=str(w?.subtitle,120);if(sub)card.append(el('p','tpa-dialog-sub',sub));
  for(const line of (Array.isArray(w?.lines)?w.lines.slice(0,8):[])){const t=str(line,300);if(t)card.append(el('p','tpa-dialog-line',t));}
  // 4.5.0: "what to expect" and other short sections, then the small print (all text from site-config.json)
  for(const sec of (Array.isArray(w?.sections)?w.sections.slice(0,4):[])){
    const head=str(sec?.heading,60),items=(Array.isArray(sec?.items)?sec.items.slice(0,6):[]).map(t=>str(t,240)).filter(Boolean);
    if(!head||!items.length)continue;
    const box=el('div','welcome-section');box.append(el('h3','',head));const ul=el('ul','');
    for(const t of items)ul.append(el('li','',t));box.append(ul);card.append(box);}
  const enter=el('button','tpa-primary',str(w?.enterLabel,40)||'Enter the map');enter.type='button';enter.onclick=()=>closeDialog(back);
  card.append(enter);
  const ready=el('div','welcome-ready');ready.append(el('small','prep-caption','Getting your map ready'),makeReadyList());card.append(ready);   // 4.6.0
  const tipBtn=tipControl(tip);
  if(tipBtn){const tipBox=el('div','tip-box');const said=str(tip?.text,240);if(said)tipBox.append(el('p','tip-text',said));
    tipBox.append(tipBtn);const note=str(tip?.note,160);if(note)tipBox.append(el('small','',note));card.append(tipBox);}
  const small=(Array.isArray(w?.smallPrint)?w.smallPrint.slice(0,6):[]).map(t=>str(t,300)).filter(Boolean);
  if(small.length){const sp=el('div','welcome-small');for(const t of small)sp.append(el('p','',t));card.append(sp);}
  const more=el('button','tpa-link-button','Data sources & credits');more.type='button';more.onclick=()=>{closeDialog(back);openDialog(credits);};
  card.append(more);return back;
}
// 4.1.15 (owner 31 Oct 23:47: "no mention of bods data"): the map's own credit line names the live data
// sources of the layers being shown, from site-config.json's short credits (data, not code).
const shortCredit=Object.create(null);
function liveCredit(){const box=$('map-credit');if(!box)return;
  const live=IDS.filter(id=>state.live[id]&&state.visible[id]&&!state.parked[id]&&(state.status[id]==='ready'||state.status[id]==='stale'))
    .map(id=>shortCredit[layerName(id)]).filter(Boolean);
  const text='© OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors'+(live.length?' · Live data: '+live.join(' · '):'');
  if(box.textContent!==text)box.textContent=text;}
async function loadSiteConfig(){
  let cfg;
  try{cfg=await requestBounded('/data/site-config.json',65536,5000);}catch(_){return;}
  if(!cfg||typeof cfg!=='object'||cfg.configVersion!==1)return;
  for(const c of Array.isArray(cfg.credits)?cfg.credits:[]){const l=str(c?.layer,40).toLowerCase(),sh=str(c?.short,40);if(l&&sh)shortCredit[l]=sh;}
  liveCredit();
  const credits=buildCredits(cfg.credits);
  let welcome=null;try{welcome=buildWelcome(cfg.welcome,cfg.tip,credits);}catch(_){}   // 4.5.0: a welcome problem never costs the side menu
  const about=el('section','panel about-panel');about.setAttribute('aria-label','About TPA');
  const title=el('div','section-title');title.append(el('h2','','ABOUT TPA'),el('span','micro','FREE, ALWAYS'));
  const creditsBtn=el('button','tpa-link-button','Data sources & credits');creditsBtn.type='button';creditsBtn.id='credits-button';creditsBtn.onclick=()=>openDialog(credits);
  const welcomeBtn=welcome?el('button','tpa-link-button','Show welcome screen'):null;
  if(welcomeBtn){welcomeBtn.type='button';welcomeBtn.id='welcome-button';welcomeBtn.onclick=()=>openDialog(welcome);}
  about.append(...[title,tipControl(cfg.tip),creditsBtn,welcomeBtn].filter(Boolean));
  $('sidebar').insertBefore(about,document.querySelector('.sidebar-footer'));
  let seen=false;try{seen=localStorage.getItem(WELCOME_KEY)==='1';}catch(_){}
  if(welcome&&cfg.welcome?.enabled===true&&!seen)openDialog(welcome);
  startPreparing(!(welcome&&cfg.welcome?.enabled===true&&!seen));   // 4.6.0: returning visitors get the short panel
}
paint();discover();beginMap();setInterval(discover,30000);
// 4.0.11: also check every 10 s, but only while a live layer is still connecting.
setInterval(()=>{if(IDS.some(id=>state.live[id]&&!state.parked[id]&&state.status[id]!=='ready'))discover();},10000);
loadSiteConfig();
// 4.0.13: one place that opens/closes the phone layers menu and keeps the button state right.
function setMenu(open){$('sidebar').classList.toggle('open',open);$('menu-button').setAttribute('aria-expanded',open?'true':'false');}
new MutationObserver(()=>$('menu-button').setAttribute('aria-expanded',$('sidebar').classList.contains('open')?'true':'false')).observe($('sidebar'),{attributes:true,attributeFilter:['class']});
})();
