/* TPA v4 HQ only: MapLibre GL JS + OpenFreeMap, same-origin resources.
 * The five transport connections accept versioned JSON *data*, never scripts.
 * No supplier/feed logic, motion algorithms, Admin controls or external URLs.
 */
(()=>{'use strict';
const IDS=Object.freeze(['stops','air','sea','rail','bus','road','stations','busstations']);   // 4.52.0: stations and their live boards; 4.53.0: bus stops (first, so they sit under the buses); 4.54.0: bus stations
const SOCKETS=Object.freeze(Object.fromEntries(IDS.map(id=>[id,Object.freeze({id,contractVersion:'1.0',type:'data-only',path:'/api/v4/components/'+id})])));
Object.defineProperty(window,'TPA_HQ_SOCKETS',{value:SOCKETS,writable:false,configurable:false});
const $=id=>document.getElementById(id),root=$('map'),canvas=$('geo-canvas'),ctx=canvas.getContext('2d');
// 4.9.0 (owner 1 Nov 16:55: "a user page ... to see what a standard user would see without all the admin
// things"): https://…/user (or ?view=user) hides the test and admin bits; the map, layers, search, welcome,
// bus details and credits are the same. Pretend (MOCK) icons still carry their on-map warning.
// 4.11.0 (owner 1 Nov 18:47-18:49): a Kids mode - "Fun icons that play sounds when you press on them ... Screen
// much less busy so little people would enjoy just playing with the map" ("5 noises per type"). https://…/kids
// (or ?view=kids). It is the user page plus: friendly pictures, a sound and a speech bubble (read aloud) when a
// vehicle is tapped, big picture buttons, "I can see …" counting, "Ride along!", and a grown-ups button that
// must be held for 3 seconds. No coffee box (we never ask children for money) and nothing is collected.
const KIDS=location.pathname==='/kids'||new URLSearchParams(location.search).get('view')==='kids';
// 4.94.0 (Rob 19 Nov 12:41, from plane enthusiasts' feedback: "a plane only one would not use our allotted data for all the
// other vehicles and maps"): tpalive.co.uk/planes is the map with aircraft only - buses, trains, stations, stops and
// traffic are never asked for, so no TomTom pictures, bus or train files. Rob: address only (no link), Easter egg as is.
const PLANES=!KIDS&&(location.pathname==='/planes'||new URLSearchParams(location.search).get('view')==='planes');
// 4.20.0 (owner 5 Nov 15:26-16:41): the live server makes the main address the user page (data-default-view on the
// page); the owner's full page, with "Report this", is /full (or ?view=full).
const VIEW_ASKED=new URLSearchParams(location.search).get('view');
const FULL_VIEW=!KIDS&&!PLANES&&(location.pathname==='/full'||VIEW_ASKED==='full');
const USER_VIEW=KIDS||PLANES||(!FULL_VIEW&&(location.pathname==='/user'||VIEW_ASKED==='user'||document.documentElement.dataset.defaultView==='user'));
if(KIDS){document.documentElement.classList.add('kids-view');}
if(PLANES){document.documentElement.classList.add('planes-view');}
if(USER_VIEW){document.documentElement.classList.add('user-view');document.title=KIDS?'TPA Kids':PLANES?'TPA Planes · Live UK aircraft on a map':'TPA · Live UK trains, buses and planes on one map';
  const h=document.querySelector('.search-panel h2');if(h)h.textContent='FIND A PLACE';
  const m=document.querySelector('.search-panel .micro');if(m)m.textContent='';}
const HOME={lon:-0.423,lat:53.842,z:11};
// 4.38.0 (owner 10 Nov 23:23): the map stays on the UK and the airspace around it - Channel Islands to Shetland,
// all of Ireland, the North Sea edge - so it never wanders off to Singapore or loads far-off map tiles.
const UK_BOX={south:48,west:-14,north:62,east:6};
let minZ=3;   // the smallest zoom that still has the whole box on screen (set once the map knows its size)
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
// 4.73.1 (Rob 18 Nov 00:30: "bus stops off as default, but turn onable in the tracking layers ... they overcomplicate the
// screen for the users who wont need them"): these layers start switched off; a visitor who switches one on keeps it on
// in that browser (and off again if they switch it off).
const OFF_AT_START=['stops'];
// 4.94.0 (Rob 19 Nov 12:45, "remember them"): every layer the visitor switched on or off is remembered for next time (it
// was bus stops only), so someone who hides buses doesn't load them again each visit. The planes page shows aircraft
// only, and Kids mode always starts with everything (neither remembers).
function layerOnAtStart(id){
  if(PLANES)return id==='air';
  if(!KIDS)try{const v=localStorage.getItem('tpa-layer-on-'+id);if(v==='1')return true;if(v==='0')return false;}catch(_){}
  return !OFF_AT_START.includes(id);}
const ASK_WHEN_SHOWN=new Set();   // 4.94.0: layers hidden at start are only asked for once they're switched on
const state={live:Object.fromEntries(IDS.map(id=>[id,false])),notice:Object.fromEntries(IDS.map(id=>[id,''])),overlays:Object.create(null),parked:Object.fromEntries(IDS.map(id=>[id,false])),lon:HOME.lon,lat:HOME.lat,z:HOME.z,visible:Object.fromEntries(IDS.map(id=>[id,layerOnAtStart(id)])),status:Object.fromEntries(IDS.map(id=>[id,'not_installed'])),frames:Object.create(null),geo:null,selected:null,renderer:'loading',mapErrors:0};
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
function toast(message,ms){const el=$('toast');el.textContent=message;el.classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.add('hidden'),ms||3000);}
function clamp(n,a,b){return Math.max(a,Math.min(b,n));}
function merc(lon,lat,z){const size=256*2**z,r=clamp(lat,-85.051128,85.051128)*Math.PI/180;return [(lon+180)/360*size,(1-Math.log(Math.tan(r)+1/Math.cos(r))/Math.PI)/2*size];}
function inverse(px,py,z){const size=256*2**z,y=Math.PI*(1-2*py/size);return {lon:px/size*360-180,lat:180/Math.PI*Math.atan(Math.sinh(y))};}
function dims(){return {w:root.clientWidth,h:root.clientHeight};}
// 4.51.0 (owner 12 Nov 15:08-15:09, Galaxy A06: the bus box covered the map so the followed bus could not be seen; "we
// definitely need to work on making the phone experience as good as the pc"): on phones the box is a pull-up panel along
// the bottom, the map keeps the vehicle above it, stop codes are tidied away, credits sit behind a (c) button, and busy
// zoomed-out views are calmer.
const PHONE_W=760;
function isPhone(){return root.clientWidth<=PHONE_W&&!KIDS;}
function sheetHeight(){return isPhone()&&!selected.classList.contains('hidden')?selected.getBoundingClientRect().height:0;}
function focusY(h){const s=sheetHeight();return s?Math.max(60,(h-s)/2):h/2;}      // where a followed vehicle is kept
// numeric stop codes (NaPTAN, e.g. "(22000624)") mean nothing to a passenger; "(Stop RD)" and "(Bay 22)" stay
function tidyName(s){return String(s).replace(/\s\((?=[0-9A-Z]*\d{4})[0-9A-Z]{6,14}\)/g,'');}
function phoneCap(z){return z<bubbleZoom()?PHONE_CAP_OUT:PHONE_CAP;}
window.TPA_HQ_PHONE={tidyName:s=>tidyName(s),cap:z=>phoneCap(z),join:()=>PHONE_JOIN};
function pixel(lon,lat){if(map){let p=map.latLngToContainerPoint([lat,lon]);return [p.x,p.y];}
const d=dims(),p=merc(lon,lat,state.z),c=merc(state.lon,state.lat,state.z);return [p[0]-c[0]+d.w/2,p[1]-c[1]+d.h/2];}
// 4.1.12 (owner 31 Oct 22:11: "Icons could be smaller for buses and trains ... a zoom in to max or nearly
// max zoom could have larger icons"): bus and train icons are sized by zoom level.
// 4.1.16 (owner 1 Nov 00:28: "knowing which part of the icon should be central to the road/track it is
// on ... the middle/bottom of the numberboard under the bus/train"): for buses and trains the exact map
// position is the bottom-middle of the number board, with the icon standing above it like a pin.
// Other icons stay centred. (Board top 30 px + board 16 px, scaled, + 8 px of fixed borders; measured.)
// 4.59.0 (owner 13 Nov 23:11, stops "in peoples houses"): a bus stop stands on the foot of its pole, like a real stop.
function anchorOf(component){return component==='bus'||component==='rail'?'translate(-50%,calc(var(--pin-s,1) * -46px - 8px))':component==='stops'?'translate(-50%,-98%)':'translate(-50%,-50%)';}
// 4.14.0 (owner 3 Nov 20:35: smaller icons, "smaller when zoomed out"): small at town level, today's size close in
function pinScale(z){return (KIDS?1.6:1)*(z<11.5?0.5:z<12.5?0.56:z<13.5?0.64:z<14.5?0.74:z<15.5?0.86:z<16.5?1:1.1);}
function syncView(){if(map){let c=map.getCenter();state.lon=c.lng;state.lat=c.lat;state.z=map.getZoom();}
  const ps=String(pinScale(state.z));if(markerLayer.style.getPropertyValue('--pin-s')!==ps)markerLayer.style.setProperty('--pin-s',ps);paint();}
function gotoPlace(lon,lat,z,label){stopFollow();if(map)map.setView([lat,lon],z,{animate:false});state.lon=lon;state.lat=lat;state.z=z;selected.classList.add('hidden');state.selected=null;$('map-place').textContent=(label||'SELECTED LOCATION').toUpperCase();$('sidebar').classList.remove('open');paint();}
// 4.76.0 (Rob 18 Nov 03:47: at night the Beverley start view was empty - "i cant find anything on the map"; he chose
// "Whole of Britain"): on the real site visitors start on the whole UK (town bubbles everywhere, planes overhead), and Home
// goes back there. START_UK comes from the server (data-start="uk") for tpalive.co.uk and the test site only.
const START_UK=document.documentElement.dataset.start==='uk';
const UK_HOME=[[50.0,-7.6],[58.6,1.8]];   // south-west, north-east: Cornwall and Northern Ireland to Shetland's south and East Anglia
function fitUK(animate){if(!map||typeof map.fitBounds!=='function')return false;map.fitBounds(UK_HOME,{animate:!!animate,padding:[10,10]});return true;}
function home(){if(START_UK){if(!fitUK(true))gotoPlace(-2.9,54.3,6,'UNITED KINGDOM');$('map-place').textContent='UNITED KINGDOM';toast('Home · the whole of the UK');return;}
  gotoPlace(HOME.lon,HOME.lat,HOME.z,'BEVERLEY, EAST YORKSHIRE');toast('Home · Beverley, East Yorkshire');}
function fitUK(){gotoPlace(-3.0,54.55,5.5,'UNITED KINGDOM');}
function zoom(delta){if(map)map.setZoom(clamp(map.getZoom()+delta,minZ,18));else{state.z=clamp(state.z+delta,3,17);paint();}}
function drawFallback(){const {w,h}=dims(),scale=Math.min(2,window.devicePixelRatio||1);if(canvas.width!==Math.round(w*scale)||canvas.height!==Math.round(h*scale)){canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);}ctx.setTransform(scale,0,0,scale,0,0);ctx.clearRect(0,0,w,h);if(!state.geo||ready)return;
ctx.fillStyle='#e8eeea';for(const shape of state.geo.land){if(shape.length<3)continue;ctx.beginPath();shape.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.closePath();ctx.fill();}
ctx.strokeStyle='#86acb1';ctx.lineWidth=1;for(const seg of state.geo.coastlines){ctx.beginPath();seg.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.stroke();}
for(const [name,lon,lat] of POI){const [x,y]=pixel(lon,lat);if(x<0||x>w||y<0||y>h)continue;if(state.z<7&&!['Beverley','Hull','York','Leeds','London','Edinburgh','Glasgow','Cardiff','Belfast'].includes(name))continue;ctx.font='600 12px system-ui';ctx.strokeStyle='#e8eeea';ctx.lineWidth=3;ctx.strokeText(name,x+5,y-5);ctx.fillStyle='#2b555c';ctx.fillText(name,x+5,y-5);}}
// 4.34.0 (owner 10 Nov 22:32: "a button to filter between normal and just military, or both"); kept in this browser
// 4.83.0 (Rob 18 Nov 10:30, ticked: a visitor who once chose "Military" saw only a few planes on every later visit): the
// planes filter is no longer remembered; every visit starts on All (an old remembered choice is removed).
let airShow='all';try{localStorage.removeItem('tpa-air-show');}catch(_){}
// 4.35.0 (owner 23:21): police / air ambulance / coastguard under a filter of their own, and in "All" (and Non-military)
function isMilitary(e){return e.variant==='military'||(typeof e.variant==='string'&&e.variant.startsWith('mil-'));}
function isService(e){return typeof e.variant==='string'&&/^(police|ambulance|coastguard)(-plane)?$/.test(e.variant);}
// 4.45.0 (owner 11 Nov 16:31, option B): airport towers, service vehicles and fire engines (they carry transmitters too) are
// never drawn or counted as planes; they show only when zoomed right in (an airport fills the screen), in any filter but Military.
const GROUND_ZOOM=13;
function isGround(e){return e.component==='air'&&typeof e.variant==='string'&&/^ground-(emer|gnd|twr)$/.test(e.variant);}
function airShown(e){if(isGround(e))return state.z>=GROUND_ZOOM&&airShow!=='military';
  return e.component!=='air'||airShow==='all'||(airShow==='military'?isMilitary(e):airShow==='services'?isService(e):!isMilitary(e));}
function listEntities(){return IDS.flatMap(id=>{
  if(!state.visible[id]||state.status[id]==='not_installed')return [];
  const entities=(state.frames[id]?.entities||[]).filter(airShown);
  // A failed/stale component may keep its last-known markers, but never appear current.
  // 4.72.0 (Rob 17 Nov 22:56-22:59: grey "looks more like we have lost a connection"; "better to have something that looks like
  // it is attempting to get data"): a late layer's vehicles carry on as they were (the words still say so) for LATE_GRACE_MS,
  // then they look for their data (lostState) and in the end fade away. layerLate = how long the layer has been late (ms).
  if(state.status[id]==='ready'){delete LATE[id];return entities;}
  if(!LATE[id])LATE[id]=Date.now();
  const lateFor=Date.now()-LATE[id];
  return entities.map(e=>({...e,quality:'stale',ownQuality:e.quality,layerLate:lateFor}));
});}
// 4.72.0: no grey vehicles. 0 = normal; 1 = lost, looking for its data (4.72.1: looks normal - Rob 23:49, a pulse "draws the
// user towards" what we want to steer them away from; class "searching" is a marker only); 2 = gone (faded away). A vehicle is "lost" when its own engine says stale (a bus silent 5 min, a train with nothing to go on, a
// cancelled train) or its whole layer has been late for over LATE_GRACE_MS. It searches for up to SEARCH_MS (1 min), then fades away.
// The selected or followed vehicle never fades away (its box stays useful); it keeps searching.
const LATE={},LATE_GRACE_MS=Number(window.TPA_HQ_TEST_LATE_GRACE_MS)||120000,SEARCH_MS=Number(window.TPA_HQ_TEST_SEARCH_MS)||60000,SEARCHING=new Map();let GONE=new Set();   // (the TEST values: tests only)
window.TPA_HQ_SEARCHING=()=>({searching:[...SEARCHING.keys()].filter(k=>!GONE.has(k)),gone:[...GONE],late:{...LATE}});
// 4.83.0 (Rob ticked 18 Nov 10:30): a train at the end of its journey has simply arrived - no "checking for its latest
// update" or "stale" for it
// 4.83.0 (Rob ticked 18 Nov 10:30 "Hide non-UK buses"): National Express's Dublin coaches come through the same national
// feed. Only the island of Ireland needs a test: there, only Northern Ireland (a rough outline) is the UK.
const NI_OUTLINE=[[-5.43,54.40],[-5.45,55.25],[-6.10,55.40],[-6.95,55.25],[-7.25,55.08],[-7.40,54.95],[-7.55,54.75],[-7.90,54.70],
  [-8.17,54.47],[-7.85,54.21],[-7.55,54.12],[-7.25,54.20],[-7.00,54.40],[-6.65,54.07],[-6.27,54.10],[-6.10,54.00],[-5.90,54.05]];
function inUK(lon,lat){
  if(!(lon<-5.9&&lat>51.3&&lat<55.45)||(lon>-6.0&&lat>55.2))return true;   // not the island of Ireland (Kintyre, Cornwall, Wales stay)
  let inside=false;for(let i=0,j=NI_OUTLINE.length-1;i<NI_OUTLINE.length;j=i++){const [xi,yi]=NI_OUTLINE[i],[xj,yj]=NI_OUTLINE[j];
    if((yi>lat)!==(yj>lat)&&lon<(xj-xi)*(lat-yi)/(yj-yi)+xi)inside=!inside;}
  return inside;
}
// 4.83.0 (Rob ticked "No stop letters in titles"): "Bus 14 to City Square F" -> "Bus 14 to City Square" (the box still says
// which stop); a letter, a letter and number, or "Stand C" at the very end
function busTitle(l){const s=String(l||'Bus').slice(0,160),m=s.match(/^(.* to .+?)\s+(?:(?:Stand|Stop|Bay)\s+)?[A-Z]{1,2}\d{0,2}$/);return m?m[1]:s;}
function finishedTrain(e){return !!e&&e.component==='rail'&&/ · terminated at /.test(String(e.label||''));}
function isLost(e){const own=e.layerLate===undefined?e.quality:e.ownQuality;return own==='stale'||(e.layerLate!==undefined&&e.layerLate>LATE_GRACE_MS);}
// 4.72.1 (Rob 17 Nov 23:47, Hull: "as soon as i opened I have pulsing" - a train that had finished at Hull, a train being carried
// on to Cottingham, a quiet bus): only a vehicle that is SITTING STILL with old information searches. One that has finished
// its journey ("terminated at ...") stands normally at its platform; one still being carried on by its estimate looks normal.
// The search clock starts when its information went old (its last report + LOST_AFTER for its layer), not when this page
// opened, so a long-silent vehicle fades away almost at once instead of pulsing for 3 minutes on arrival.
const LOST_AFTER={bus:300,rail:600};   // seconds after its last report that its engine calls it stale (bus 5 min, rail 10 min)
function finished(e){return typeof e.label==='string'&&/(^|· )terminated at /.test(e.label);}
function lostState(e,k,keepIt,pos,now){
  if(!isLost(e)||finished(e)){SEARCHING.delete(k);return 0;}
  const here=estimate(e,now),ahead=here&&estimate(e,now+3000);   // (estimate only: whereNow would change the vehicle's blends)
  if(ahead&&Math.hypot(...metresXY(here,ahead))>3){SEARCHING.delete(k);return 0;}   // still being carried on: looks normal
  if(!SEARCHING.has(k)){let start=now;const ob=Date.parse(e.observedAt);
    if(e.layerLate===undefined&&Number.isFinite(ob))start=Math.min(now,ob+1000*(LOST_AFTER[e.component]||120));
    SEARCHING.set(k,start);if(SEARCHING.size>20000)SEARCHING.delete(SEARCHING.keys().next().value);}
  return now-SEARCHING.get(k)>SEARCH_MS&&!keepIt?2:1;
}

// ICON REVIEW ONLY. The SVG shapes are fixed HQ artwork reproduced from the
// legacy approved map marker paths; component JSON supplies DATA, never HTML.
// This review build is physically separate from the working HQ 4.0.8 checkpoint.
// 4.15.0 (owner 3 Nov 21:08: "All in house"): TPA's own drawings - a British double-decker (destination blind
// lit amber), a modern British train front (amber headcode panel), a top-down plane (turns to its heading) and a
// small ferry (amber funnel band). [shape, amber detail]; flat shapes so they stay crisp at 20 px and cost nothing.
const ICON_PATHS=Object.freeze({
  air:['M16 1.8c1.25 0 2.05 1.7 2.05 3.7V12l10.95 6.6v3.1L18.05 18.6V25l3.6 2.7v2.5L16 28.7l-5.65 1.5v-2.5l3.6-2.7v-6.4L3 21.7v-3.1L13.95 12V5.5c0-2 .8-3.7 2.05-3.7Z',''],
  sea:['M3 19h26l-3.4 7.3a1.8 1.8 0 0 1-1.6 1.1H8a1.8 1.8 0 0 1-1.6-1.1ZM7 12.5h17V18H7ZM9 14.2h2.6v2H9ZM13 14.2h2.6v2H13ZM17 14.2h2.6v2H17ZM10 8.5h8V12h-8ZM20.2 5.5h3.6V12h-3.6Z','M20.2 7.2h3.6v1.6h-3.6Z'],
  rail:['M3 11h18.5c3.6 0 6.2 1.6 7.7 4.6l.9 1.8c.6 1.3-.3 2.6-1.7 2.6H3ZM5 13.2h3v3H5ZM10 13.2h3v3h-3ZM15 13.2h3v3h-3ZM21 13.2h2.6c1.5 0 2.8.8 3.5 2.2l.4.8H21ZM5.4 21.2a1.7 1.7 0 1 0 3.4 0ZM9.8 21.2a1.7 1.7 0 1 0 3.4 0ZM19.4 21.2a1.7 1.7 0 1 0 3.4 0ZM23.8 21.2a1.7 1.7 0 1 0 3.4 0ZM2 23.4h28v1.3H2Z','M3.6 17.4h24.6v1.1H3.6Z'],   // 4.15.1 (owner 21:56): side-on, so trains and buses differ at a glance
  // 4.52.0: TPA's own station: a small station building (clock in the gable) beside the track. No National Rail sign.
  stations:['M3 13 16 4l13 9v2H3ZM6 15.6h20V26H6ZM13.6 26v-5a2.4 2.4 0 0 1 4.8 0v5ZM8.6 18h3v3h-3ZM20.4 18h3v3h-3ZM2 27.6h28v2H2Z','M16 8.6a1.9 1.9 0 1 1 0 3.8 1.9 1.9 0 0 1 0-3.8Z'],
  // 4.54.0: TPA's own bus station: a canopy on two posts over a bus front; the windscreen is amber
  busstations:['M2 9.5 16 3l14 6.5V12H2ZM3.6 12h2.2v15H3.6ZM26.2 12h2.2v15h-2.2ZM10 13.6h12a2 2 0 0 1 2 2V25a1 1 0 0 1-1 1h-1v2h-3v-2h-6v2h-3v-2H9a1 1 0 0 1-1-1v-9.4a2 2 0 0 1 2-2ZM2 28.6h28v1.8H2Z','M10.2 15.6h11.6v4.2H10.2ZM10.4 22.2h2.2v1.6h-2.2ZM19.4 22.2h2.2v1.6h-2.2Z'],
  // 4.53.0: TPA's own bus stop: a flag on a pole (no operator or council signs); the little bus on the flag is amber
  stops:['M9 3h14a2.5 2.5 0 0 1 2.5 2.5v8a2.5 2.5 0 0 1-2.5 2.5H9a2.5 2.5 0 0 1-2.5-2.5v-8A2.5 2.5 0 0 1 9 3ZM15 16h2v11h-2ZM11 27.2h10v2H11Z','M11.4 6h9.2a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-.6v1h-1.6v-1h-4.8v1H12v-1h-.6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Z'],
  bus:['M9 3h14a3 3 0 0 1 3 3v20a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6a3 3 0 0 1 3-3ZM9 5h14v3H9ZM8 10h7.5v5H8ZM16.5 10H24v5h-7.5ZM8 17.5h16V23H8ZM9 24.4h3V26H9ZM20 24.4h3V26h-3ZM7 28h4v2.5H7ZM21 28h4v2.5h-4ZM3.6 10.5h2.4v4.5H3.6ZM26 10.5h2.4v4.5H26Z','M9.7 5.7h12.6v1.6H9.7Z']
});
// 4.16.0 (owner 5 Nov 00:16-00:18, "graphics work for the train types", "i like that!"): TPA's own drawing for each kind
// of train (rail engine 0.5.0 sends it as "variant", from the headcode's first digit). Side-on, facing right; the amber
// patch is the yellow warning panel British trains carry on the front. Unknown kinds keep the plain train.
const RAIL_KINDS=Object.freeze({express: ['M2 12h17c4.5 0 8.3 2.2 10.6 5.6l.6.9c.5.8-.1 1.7-1 1.7H2ZM4 13.8h12.8v2.3H4ZM19.4 13.8c2.4.2 4.4 1.1 5.9 2.3h-5.9ZM4.3 21.4a1.6 1.6 0 1 0 3.2 0ZM8.3 21.4a1.6 1.6 0 1 0 3.2 0ZM19.3 21.4a1.6 1.6 0 1 0 3.2 0ZM23.3 21.4a1.6 1.6 0 1 0 3.2 0ZM2 23.4h28v1.3H2Z',
            'M26.6 17.2c1.2.4 2.3 1 3 1.6l.6.9c.3.4.1.8-.2 1H26.6Z'],
  stopping: ['M3 10.5h23.4c1.4 0 2.6 1.2 2.6 2.6V20H3ZM5 12.4h3.2v3.3H5ZM12.2 12.4h3.2v3.3h-3.2ZM19.4 12.4h3.2v3.3h-3.2ZM9.4 12h1.5v7.2H9.4ZM16.6 12h1.5v7.2h-1.5ZM24.4 12.4h2.8v3.3h-2.8ZM4.6 21.4a1.6 1.6 0 1 0 3.2 0ZM8.6 21.4a1.6 1.6 0 1 0 3.2 0ZM20.2 21.4a1.6 1.6 0 1 0 3.2 0ZM24.2 21.4a1.6 1.6 0 1 0 3.2 0ZM2 23.4h28v1.3H2Z',
             'M27.4 16.6H29V20h-1.6Z'],
  freight: ['M2 18h16.4v1.7H2ZM2.6 11.4h7v6.1h-7ZM10.6 11.4h7v6.1h-7ZM19 11h9c1.1 0 2 .9 2 2v7H19ZM25.8 12.7h2.7v2.7h-2.7ZM20.6 12.9h3.6v1H20.6ZM20.6 14.9h3.6v1H20.6ZM3.4 21.4a1.6 1.6 0 1 0 3.2 0ZM13.4 21.4a1.6 1.6 0 1 0 3.2 0ZM19.8 21.4a1.6 1.6 0 1 0 3.2 0ZM25.4 21.4a1.6 1.6 0 1 0 3.2 0ZM2 23.4h28v1.3H2Z',
            'M28.5 16.4H30V20h-1.5Z'],
  engine: ['M7 11h18c1.1 0 2 .9 2 2v7H5v-7c0-1.1.9-2 2-2ZM6.8 12.8h2.7v2.7H6.8ZM22.5 12.8h2.7v2.7h-2.7ZM11.6 13h8.8v1h-8.8ZM11.6 15h8.8v1h-8.8ZM6.6 21.4a1.6 1.6 0 1 0 3.2 0ZM10.4 21.4a1.6 1.6 0 1 0 3.2 0ZM18.4 21.4a1.6 1.6 0 1 0 3.2 0ZM22.2 21.4a1.6 1.6 0 1 0 3.2 0ZM2 23.4h28v1.3H2Z',
           'M25.5 16.5H27V20h-1.5Z'],
  empty: ['M3 10.5h23.4c1.4 0 2.6 1.2 2.6 2.6V20H3ZM4.6 12.1v6.3h22.8v-5.3c0-.6-.4-1-1-1ZM4.6 21.4a1.6 1.6 0 1 0 3.2 0ZM8.6 21.4a1.6 1.6 0 1 0 3.2 0ZM20.2 21.4a1.6 1.6 0 1 0 3.2 0ZM24.2 21.4a1.6 1.6 0 1 0 3.2 0ZM2 23.4h28v1.3H2Z',
          '']});
// 4.17.0 (owner 5 Nov stage 3): TPA's own side-on drawings of the common British train classes, shown in a train's box
// once the timetable says what it is (rail engine 0.8.0 sends picture "c800" etc.). 100x26, facing right; amber = the
// yellow warning panel. Drawn in-house (v4/design/train-classes), no photos or logos.
const BUS_PICTURES={dd:'Picture of a double-decker bus',sd:'Picture of a single-decker bus',coach:'Picture of a coach',mini:'Picture of a minibus'};   // 4.29.0
// 4.89.0 (Rob 18 Nov 18:17-18:31, "Find Geoff"): one bus per nation each day (picked by the server, the same for
// everyone) has Geoff driving. Only its picture changes, and only in its box; everything else about it stays true.
let GEOFF={ids:new Set(),date:''};
async function loadGeoff(){try{const r=await fetch('/api/v4/geoff');if(!r.ok)return;const j=await r.json();
  if(j&&Array.isArray(j.ids)&&typeof j.date==='string')GEOFF={ids:new Set(j.ids.filter(x=>typeof x==='string').slice(0,8)),date:j.date};}catch(_){}}
function geoffDay(d){const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(d||'');if(!m)return 'today';
  return (+m[3])+' '+['January','February','March','April','May','June','July','August','September','October','November','December'][(+m[2])-1];}
const TRAIN_PHOTOS=new Set(['c800','c390','c43','c220','c150','c158','c170','c195','c345','c700','c755','c387','c66',   // 4.21.0
  'c165','c777','c350','c385','c323','c185','c395','c380','c333','celectric','cdiesel']);   // 4.24.0 (owner 6 Nov 10:24-10:58): second batch + a generic electric train
// 4.24.0: the second batch has no drawing of its own; if a picture fails to load, the nearest first-batch drawing stands in
const TRAIN_ALIAS=Object.freeze({c165:'c150',c777:'c755',c350:'c387',c385:'c800',c323:'c150',c185:'c158',c395:'c800',c380:'c387',c333:'c195',celectric:'c345',cdiesel:'c170'});
const TRAIN_CLASSES=Object.freeze({
  c800:["M2 8.6Q2 7 3.6 7H68C79 7 89.5 11 95.6 16.8L96 19.4H2ZM77.5 9.3C82.6 9.7 87.2 11.6 90.4 14.4H77.5ZM6 10.2h4.2v3.8h-4.2ZM12.6 10.2h4.2v3.8h-4.2ZM19.2 10.2h4.2v3.8h-4.2ZM25.8 10.2h4.2v3.8h-4.2ZM32.4 10.2h4.2v3.8h-4.2ZM39 10.2h4.2v3.8h-4.2ZM45.6 10.2h4.2v3.8h-4.2ZM52.2 10.2h4.2v3.8h-4.2ZM58.8 10.2h4.2v3.8h-4.2ZM66.5 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM54.1 20.6a1.6 1.6 0 1 0 3.2 0ZM58.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M91.6 15.6C93.4 16.6 94.8 17.6 95.6 18.6L95.8 19.4H91.6Z"],
  c390:["M2 9.4Q2 6.6 4.8 6.6H72C82 6.6 89 9.4 93.4 13.4Q96.4 16.2 96.6 19.4H2ZM79 8.8C84.6 9.2 88.6 11 91.2 13.6H79ZM6 10.4h3.6v3.2h-3.6ZM12 10.4h3.6v3.2h-3.6ZM18 10.4h3.6v3.2h-3.6ZM24 10.4h3.6v3.2h-3.6ZM30 10.4h3.6v3.2h-3.6ZM36 10.4h3.6v3.2h-3.6ZM42 10.4h3.6v3.2h-3.6ZM48 10.4h3.6v3.2h-3.6ZM54 10.4h3.6v3.2h-3.6ZM60 10.4h3.6v3.2h-3.6ZM68 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM56.1 20.6a1.6 1.6 0 1 0 3.2 0ZM60.7 20.6a1.6 1.6 0 1 0 3.2 0ZM28 6.6l2.6-2.8h7.6l.9.9h-7.6L29.6 6.6ZM34 3.8h1v-1h3v1h1v.6h-5ZM1 22.9h98v1.2H1Z","M92.6 14.2C94.8 16 96 17.6 96.4 19.4H92.6Z"],
  c43:["M2 7.6H73L95.4 15.6V19.4H2ZM78.4 9.3L88.4 13H78.4ZM8 9.6h16v1.1H8ZM8 11.8h16v1.1H8ZM8 14h16v1.1H8ZM28 9.6h6v6h-6ZM70 9.4h1.3v9H70ZM7.1 20.6a1.6 1.6 0 1 0 3.2 0ZM11.7 20.6a1.6 1.6 0 1 0 3.2 0ZM56.1 20.6a1.6 1.6 0 1 0 3.2 0ZM60.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M89 14.2L95.4 15.6V19.4H89Z"],
  c220:["M2 8.4Q2 7 3.4 7H71C81.6 7 90 10.4 94 14.8Q96 17 96 19.4H2ZM78 9C83.2 9.4 87.8 11.2 90.8 14H78ZM6 10.2h3.8v3.8h-3.8ZM12.2 10.2h3.8v3.8h-3.8ZM18.4 10.2h3.8v3.8h-3.8ZM24.6 10.2h3.8v3.8h-3.8ZM30.8 10.2h3.8v3.8h-3.8ZM37 10.2h3.8v3.8h-3.8ZM43.2 10.2h3.8v3.8h-3.8ZM49.4 10.2h3.8v3.8h-3.8ZM55.6 10.2h3.8v3.8h-3.8ZM61.8 10.2h3.8v3.8h-3.8ZM68.5 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM56.1 20.6a1.6 1.6 0 1 0 3.2 0ZM60.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M92.2 15.4C94.6 16.8 95.8 18 96 19.4H92.2Z"],
  c150:["M2 7.6H93.6Q96 7.6 96 10V19.4H2ZM87.6 9.6h3.4v4.4h-3.4ZM92 9.6h2.6v4.4H92ZM5 10.2h4.4v3.8h-4.4ZM12.4 10.2h4.4v3.8h-4.4ZM19.8 10.2h4.4v3.8h-4.4ZM34.6 10.2h4.4v3.8h-4.4ZM42 10.2h4.4v3.8h-4.4ZM49.4 10.2h4.4v3.8h-4.4ZM64.2 10.2h4.4v3.8h-4.4ZM71.6 10.2h4.4v3.8h-4.4ZM27.5 9.6h1.3v9h-1.3ZM58 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM74.1 20.6a1.6 1.6 0 1 0 3.2 0ZM78.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M86.6 15.4H96v4H86.6Z"],
  c158:["M2 7.6H89.4L96 10.4V19.4H2ZM86.8 9.4h2.4l5 2.2v2.6h-7.4ZM5 10.2h4.4v3.8h-4.4ZM12.4 10.2h4.4v3.8h-4.4ZM19.8 10.2h4.4v3.8h-4.4ZM27.2 10.2h4.4v3.8h-4.4ZM34.6 10.2h4.4v3.8h-4.4ZM42 10.2h4.4v3.8h-4.4ZM49.4 10.2h4.4v3.8h-4.4ZM56.8 10.2h4.4v3.8h-4.4ZM64.2 10.2h4.4v3.8h-4.4ZM71.6 10.2h4.4v3.8h-4.4ZM82.8 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM74.1 20.6a1.6 1.6 0 1 0 3.2 0ZM78.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M86.6 15.4H96v4H86.6Z"],
  c170:["M2 7.6H85C91 7.6 94.8 10.6 96 14.4V19.4H2ZM84.4 9.4C88.6 9.6 91.6 11.4 93.2 13.8H84.4ZM5 10.2h4.6v3.8h-4.6ZM12 10.2h4.6v3.8h-4.6ZM26 10.2h4.6v3.8h-4.6ZM33 10.2h4.6v3.8h-4.6ZM40 10.2h4.6v3.8h-4.6ZM47 10.2h4.6v3.8h-4.6ZM54 10.2h4.6v3.8h-4.6ZM61 10.2h4.6v3.8h-4.6ZM68 10.2h4.6v3.8h-4.6ZM75 10.2h4.6v3.8h-4.6ZM22 9.6h1.3v9h-1.3ZM80.6 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM72.1 20.6a1.6 1.6 0 1 0 3.2 0ZM76.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M88.6 15.2H95.8C96 16.4 96 17.8 96 19.4H88.6Z"],
  c195:["M2 7.6H83C89.6 7.6 95.4 12 96 19.4H2ZM83 9.4C87.6 9.8 91.2 12.2 92.8 15.4H83ZM5 10.2h4.4v3.8h-4.4ZM11.6 10.2h4.4v3.8h-4.4ZM18.2 10.2h4.4v3.8h-4.4ZM31.4 10.2h4.4v3.8h-4.4ZM38 10.2h4.4v3.8h-4.4ZM44.6 10.2h4.4v3.8h-4.4ZM57.8 10.2h4.4v3.8h-4.4ZM64.4 10.2h4.4v3.8h-4.4ZM71 10.2h4.4v3.8h-4.4ZM24 9.6h1.3v9h-1.3ZM52 9.6h1.3v9h-1.3ZM79.4 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM70.1 20.6a1.6 1.6 0 1 0 3.2 0ZM74.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M88 16.4H94.6C95.4 17.4 95.8 18.4 96 19.4H88Z"],
  c345:["M2 7.6H87.6Q92.6 7.6 94.6 11.6L96 19.4H2ZM86 9.4h5.6q1.4 0 2 1.6l.8 3.6H86ZM5 10h5.4v4.6h-5.4ZM13.6 10h5.4v4.6h-5.4ZM22.2 10h5.4v4.6h-5.4ZM30.8 10h5.4v4.6h-5.4ZM48 10h5.4v4.6h-5.4ZM56.6 10h5.4v4.6h-5.4ZM73.8 10h5.4v4.6h-5.4ZM20 9.6h1.3v9h-1.3ZM44 9.6h1.3v9h-1.3ZM68 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM74.1 20.6a1.6 1.6 0 1 0 3.2 0ZM78.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M86 16H95.4L96 19.4H86Z"],
  c700:["M2 7.6H84.6C91 7.6 95 11.2 96 16V19.4H2ZM84 9.4C88.4 9.8 91.6 11.8 93.2 14.6H84ZM5 10h5.2v4.4h-5.2ZM13.2 10h5.2v4.4h-5.2ZM29.6 10h5.2v4.4h-5.2ZM37.8 10h5.2v4.4h-5.2ZM54.2 10h5.2v4.4h-5.2ZM62.4 10h5.2v4.4h-5.2ZM70.6 10h5.2v4.4h-5.2ZM22 9.6h1.3v9h-1.3ZM50 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM72.1 20.6a1.6 1.6 0 1 0 3.2 0ZM76.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M88.4 16H95.6C95.8 17 96 18 96 19.4H88.4Z"],
  c755:["M2 7.6H83.4C91.6 7.6 96 12 96 17.2V19.4H2ZM82.8 9.4C87.8 9.6 91.4 11.6 93 14.4H82.8ZM5 10.2h4.8v3.8h-4.8ZM12.4 10.2h4.8v3.8h-4.8ZM27.2 10.2h4.8v3.8h-4.8ZM34.6 10.2h4.8v3.8h-4.8ZM42 10.2h4.8v3.8h-4.8ZM49.4 10.2h4.8v3.8h-4.8ZM56.8 10.2h4.8v3.8h-4.8ZM64.2 10.2h4.8v3.8h-4.8ZM71.6 10.2h4.8v3.8h-4.8ZM24 9.6h1.3v9h-1.3ZM79.6 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM72.1 20.6a1.6 1.6 0 1 0 3.2 0ZM76.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M88 16H95.8C96 16.8 96 17.6 96 19.4H88Z"],
  c387:["M2 7.6H89C93.4 7.6 95.6 9.8 96 12.6V19.4H2ZM87.6 9.4h3.6v4.4h-3.6ZM92.2 9.6c1.4.4 2.4 1.6 2.8 4.2h-2.8ZM5 10.2h4.6v3.8h-4.6ZM12.6 10.2h4.6v3.8h-4.6ZM20.2 10.2h4.6v3.8h-4.6ZM35.4 10.2h4.6v3.8h-4.6ZM43 10.2h4.6v3.8h-4.6ZM50.6 10.2h4.6v3.8h-4.6ZM65.8 10.2h4.6v3.8h-4.6ZM73.4 10.2h4.6v3.8h-4.6ZM28 9.6h1.3v9h-1.3ZM58 9.6h1.3v9h-1.3ZM6.1 20.6a1.6 1.6 0 1 0 3.2 0ZM10.7 20.6a1.6 1.6 0 1 0 3.2 0ZM74.1 20.6a1.6 1.6 0 1 0 3.2 0ZM78.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M86.8 15.6H96v3.8H86.8Z"],
  c66:["M2 9H91.4Q93.4 9 94.2 10.4L96 13V19.4H2ZM20 9V7.4h22V9ZM48 9V8h10v1ZM88 10.6h3.6l1.8 2.8H88ZM4 10.6h3.6v2.8H4ZM12 11h18v1H12ZM12 13h18v1H12ZM12 15h18v1H12ZM40 11h16v5H40ZM60 11h18v1H60ZM60 13h18v1H60ZM84 10.4h1.2v8H84ZM12.1 20.6a1.6 1.6 0 1 0 3.2 0ZM16.7 20.6a1.6 1.6 0 1 0 3.2 0ZM72.1 20.6a1.6 1.6 0 1 0 3.2 0ZM76.7 20.6a1.6 1.6 0 1 0 3.2 0ZM1 22.9h98v1.2H1Z","M90 15H96v4.4H90Z"]
});
// 4.34.0 (owner 10 Nov 22:19: "simple stand ins" until his own): a top-down drawing for each kind of plane, nose up, so it
// still turns to its heading. The air engine (0.6.1) sends the kind as "variant"; an unknown kind keeps the usual plane.
const AIR_KINDS_ALIAS={};
const AIR_KINDS=Object.freeze({
  airliner:['M16 1.8c1.25 0 2.05 1.7 2.05 3.7V12l10.95 6.6v3.1L18.05 18.6V25l3.6 2.7v2.5L16 28.7l-5.65 1.5v-2.5l3.6-2.7v-6.4L3 21.7v-3.1L13.95 12V5.5c0-2 .8-3.7 2.05-3.7Z',''],
  widebody:['M16 .8c1.6 0 2.5 2 2.5 4.4v6.4l12.7 7.5v3.4l-12.7-4.1v6.8l4.4 3.1v2.6L16 29.4l-6.9 1.5v-2.6l4.4-3.1v-6.8L.8 22.5v-3.4l12.7-7.5V5.2C13.5 2.8 14.4.8 16 .8Z','M7 15.6h2.6v3.6H7ZM22.4 15.6H25v3.6h-2.6Z'],
  turboprop:['M15 2.4h2c.8 0 1.3 1.2 1.3 2.6v5.4H29c.6 0 1 .4 1 1v2.4c0 .6-.4 1-1 1H18.3v9h4.4c.5 0 .8.3.8.8v1.6c0 .5-.3.8-.8.8H9.3c-.5 0-.8-.3-.8-.8v-1.6c0-.5.3-.8.8-.8h4.4v-9H3c-.6 0-1-.4-1-1v-2.4c0-.6.4-1 1-1h10.7V5c0-1.4.5-2.6 1.3-2.6Z','M6.4 8.2h4.4v1.4H6.4ZM21.2 8.2h4.4v1.4h-4.4Z'],
  bizjet:['M16 2c.9 0 1.6 1.4 1.6 3.2V13l9.4 6v2.4l-9.4-3.4v5.4l3.4 2.4v2l-5-1.2-5 1.2v-2l3.4-2.4V18L5 21.4V19l9.4-6V5.2C14.4 3.4 15.1 2 16 2Z','M12.4 19.6h1.8v3.6h-1.8ZM17.8 19.6h1.8v3.6h-1.8Z'],
  light:['M15.2 4.4h1.6c.5 0 .8.6.8 1.4v3.6H28c.6 0 1 .4 1 1v2c0 .6-.4 1-1 1H17.6v9.2H22c.5 0 .8.3.8.8v1.4c0 .5-.3.8-.8.8H10c-.5 0-.8-.3-.8-.8v-1.4c0-.5.3-.8.8-.8h4.4v-9.2H4c-.6 0-1-.4-1-1v-2c0-.6.4-1 1-1h10.4V5.8c0-.8.3-1.4.8-1.4Z','M12.6 2.6h6.8v1.3h-6.8Z'],
  helicopter:['M16 6.4c2.8 0 4.6 2.5 4.6 5.8s-1.8 6.4-4.6 6.4-4.6-3.1-4.6-6.4 1.8-5.8 4.6-5.8ZM15.2 18h1.6v9.4h-1.6ZM12.6 26.4h6.8v1.6h-6.8Z','M3.6 10.8l24.8 2.4-.1 1.3-24.8-2.4ZM16.6 1.2l-1.2 24.8-1.3-.1 1.2-24.8Z'],
  cargo:['M16 1.8c1.25 0 2.05 1.7 2.05 3.7V12l10.95 6.6v3.1L18.05 18.6V25l3.6 2.7v2.5L16 28.7l-5.65 1.5v-2.5l3.6-2.7v-6.4L3 21.7v-3.1L13.95 12V5.5c0-2 .8-3.7 2.05-3.7Z','M14.7 13h2.6v4.4h-2.6Z'],
  military:['M15 2.4h2c.8 0 1.3 1.2 1.3 2.6v5.4H29c.6 0 1 .4 1 1v2.4c0 .6-.4 1-1 1H18.3v9h4.4c.5 0 .8.3.8.8v1.6c0 .5-.3.8-.8.8H9.3c-.5 0-.8-.3-.8-.8v-1.6c0-.5.3-.8.8-.8h4.4v-9H3c-.6 0-1-.4-1-1v-2.4c0-.6.4-1 1-1h10.7V5c0-1.4.5-2.6 1.3-2.6Z','M3.6 8.2H7v1.4H3.6ZM8.4 8.2h3.4v1.4H8.4ZM20.2 8.2h3.4v1.4h-3.4ZM25 8.2h3.4v1.4H25Z'],
  // 4.35.0 (owner 23:21: "Split for military ... include military helicopters"; police / air ambulance / coastguard)
  'mil-fighter':['M16 1.5c.9 0 1.5 1.6 1.5 3.6v6.3l11 12.4v2.4l-11-4.4v2.6l3.3 3.2v1.9L16 28l-4.8 1.5v-1.9l3.3-3.2v-2.6l-11 4.4v-2.4l11-12.4V5.1c0-2 .6-3.6 1.5-3.6Z',''],
  'mil-tanker':['M16 .8c1.6 0 2.5 2 2.5 4.4v6.4l12.7 7.5v3.4l-12.7-4.1v6.8l4.4 3.1v2.6L16 29.4l-6.9 1.5v-2.6l4.4-3.1v-6.8L.8 22.5v-3.4l12.7-7.5V5.2C13.5 2.8 14.4.8 16 .8Z','M15.4 29h1.2v2.6h-1.2ZM4.6 21.4h1.2v3H4.6ZM26.2 21.4h1.2v3h-1.2Z'],
  'mil-transport':null,'mil-helicopter':null,
  'mil-surveillance':['M16 1.8c1.25 0 2.05 1.7 2.05 3.7V12l10.95 6.6v3.1L18.05 18.6V25l3.6 2.7v2.5L16 28.7l-5.65 1.5v-2.5l3.6-2.7v-6.4L3 21.7v-3.1L13.95 12V5.5c0-2 .8-3.7 2.05-3.7Z','M16 15.2a3.6 3.6 0 1 1 0 7.2 3.6 3.6 0 0 1 0-7.2Z'],
  police:null,ambulance:null,coastguard:null,
  glider:['M15.4 4h1.2c.4 0 .6.8.6 1.8V12h13.3v1.6H17.2v12.2h3.6v1.4h-9.6v-1.4h3.6V13.6H1.5V12h13.3V5.8c0-1 .2-1.8.6-1.8Z',''],
  other:['M16 4a8 8 0 1 1 0 16 8 8 0 0 1 0-16ZM14 21h4v5h-4Z','']});
for(const [k,v] of [['mil-transport','military'],['mil-helicopter','helicopter'],['police','helicopter'],['ambulance','helicopter'],['coastguard','helicopter'],
                     ['police-plane','turboprop'],['ambulance-plane','turboprop'],['coastguard-plane','turboprop'],['balloon','other']])
  AIR_KINDS_ALIAS[k]=v;
function airArt(v){const a=Object.hasOwn(AIR_KINDS_ALIAS,v)?AIR_KINDS_ALIAS[v]:v;return Object.hasOwn(AIR_KINDS,a)&&AIR_KINDS[a]?AIR_KINDS[a]:null;}
function artPair(e){return (e.component==='rail'&&typeof e.variant==='string'&&Object.hasOwn(RAIL_KINDS,e.variant)&&RAIL_KINDS[e.variant])||
  (e.component==='air'&&typeof e.variant==='string'&&airArt(e.variant))||ICON_PATHS[e.component];}
const DEMO_ICONS=Object.freeze({air:'AIR',sea:'SEA',rail:'TRAIN',bus:'BUS',road:'ROAD'});
function iconFor(id){return {air:'✈',sea:'⛴',rail:'🚂',bus:'🚌',road:'≋',stations:'🚉',stops:'🚏',busstations:'🚍'}[id]||'•';}
// 4.15.1 (owner 3 Nov 22:01: "get the whole site glittered", all in house): the side menu, the vehicle box, the welcome
// tiles and the logo use TPA's own drawings too. The map picture is ours as well; traffic keeps its plain wave sign.
const UI_PATHS=Object.freeze({map:['M3.5 7.2 11 4l10 3.2L28.5 4v21L21 28.2 11 25l-7.5 3.2ZM10.4 5.6v18.9h1.2V5.6ZM20.4 8.3v18.9h1.2V8.3Z','']});
function ownIcon(id){const pair=ICON_PATHS[id]||UI_PATHS[id];if(!pair)return document.createTextNode(iconFor(id));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('focusable','false');
  svg.setAttribute('aria-hidden','true');svg.setAttribute('class','own-icon');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',pair[0]);path.setAttribute('fill-rule','evenodd');svg.append(path);
  if(pair[1]){const acc=document.createElementNS('http://www.w3.org/2000/svg','path');acc.setAttribute('d',pair[1]);acc.setAttribute('class','acc');svg.append(acc);}
  return svg;}
// 4.87.0 (Rob 18 Nov 16:26: "these icons need to be the same as the ones on the board itself please. then it's also a key
// to the board"): the Tracking layers menu (and the welcome board) show the very pictures the map uses, so the menu is
// also the map's key. Traffic keeps its wave sign and ships their drawing (neither has a map picture).
function menuPic(id){const img=el('img','pic-icon menu-pic');img.src=MENU_PIC[id];img.alt='';img.decoding='async';img.draggable=false;
  img.addEventListener('error',()=>img.replaceWith(ICON_PATHS[id]?ownIcon(id):document.createTextNode(iconFor(id))),{once:true});return img;}
function ownIconsInPage(){for(const t of document.querySelectorAll('.layer-toggle[data-component]')){const id=t.dataset.component,box=t.querySelector('.layer-icon');
    if(box&&MENU_PIC[id]){box.classList.add('has-pic');box.replaceChildren(menuPic(id));}
    else if(box&&ICON_PATHS[id]){box.replaceChildren(ownIcon(id));}}
  const logo=document.querySelector('.brand-plane');if(logo)logo.replaceChildren(ownIcon('air'));}
// 4.11.0: the kids pictures (our own drawings, with friendly faces). Fixed text, no data inside.
const KID_ART={
  bus:'<svg viewBox="0 0 64 48" focusable="false"><rect x="4" y="5" width="56" height="32" rx="9" fill="#ffc531" stroke="#b77f00" stroke-width="2"/><rect x="9" y="10" width="31" height="12" rx="3" fill="#bfe9ff"/><rect x="44" y="10" width="12" height="15" rx="3" fill="#bfe9ff"/><rect x="5" y="26" width="54" height="3" fill="#e5533d"/><circle cx="47.5" cy="16" r="2.6" fill="#fff"/><circle cx="53" cy="16" r="2.6" fill="#fff"/><circle cx="48" cy="16.4" r="1.4" fill="#222"/><circle cx="53.5" cy="16.4" r="1.4" fill="#222"/><path d="M46 32 q4.5 3.5 9 0" stroke="#7a3b00" stroke-width="2" fill="none" stroke-linecap="round"/><circle cx="17" cy="38" r="6" fill="#333"/><circle cx="17" cy="38" r="2.4" fill="#ccc"/><circle cx="47" cy="38" r="6" fill="#333"/><circle cx="47" cy="38" r="2.4" fill="#ccc"/></svg>',
  rail:'<svg viewBox="0 0 64 48" focusable="false"><circle cx="49" cy="4" r="3" fill="#fff" opacity=".9"/><circle cx="54" cy="2.5" r="2" fill="#fff" opacity=".8"/><rect x="43" y="6" width="8" height="12" rx="2" fill="#3a3a3a"/><rect x="4" y="5" width="20" height="30" rx="4" fill="#2e9e5b" stroke="#1d6b3c" stroke-width="2"/><rect x="8" y="9" width="12" height="10" rx="2" fill="#bfe9ff"/><rect x="22" y="15" width="34" height="20" rx="5" fill="#3cb371" stroke="#1d6b3c" stroke-width="2"/><circle cx="53" cy="25" r="9" fill="#f6ead0" stroke="#1d6b3c" stroke-width="2"/><circle cx="50.5" cy="23" r="1.8" fill="#222"/><circle cx="55.5" cy="23" r="1.8" fill="#222"/><path d="M49.5 28 q3.5 3 7 0" stroke="#7a3b00" stroke-width="1.8" fill="none" stroke-linecap="round"/><circle cx="14" cy="39" r="6" fill="#d93a2b"/><circle cx="31" cy="39" r="6" fill="#d93a2b"/><circle cx="46" cy="39" r="6" fill="#d93a2b"/><circle cx="14" cy="39" r="2" fill="#ffd"/><circle cx="31" cy="39" r="2" fill="#ffd"/><circle cx="46" cy="39" r="2" fill="#ffd"/></svg>',
  air:'<svg viewBox="0 0 64 48" focusable="false"><path d="M7 24 L3 8 L17 21 Z" fill="#ff6b6b" stroke="#c74343" stroke-width="1.5" stroke-linejoin="round"/><ellipse cx="32" cy="26" rx="27" ry="9.5" fill="#fff" stroke="#3d8bd9" stroke-width="2"/><path d="M25 27 L38 43 L45 43 L39 27 Z" fill="#3d8bd9"/><circle cx="20" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="26" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="32" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="50" cy="21.5" r="3.2" fill="#fff" stroke="#3d8bd9" stroke-width="1.2"/><circle cx="50.8" cy="21.8" r="1.6" fill="#222"/><path d="M48 29 q4 3 8 0" stroke="#7a3b00" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>'
};
function kidFlip(e){return e.component==='air'&&e.position?.headingDeg!=null&&e.position.headingDeg>180?'scaleX(-1)':'';}
const PIC_ICON=Object.freeze({bus:'/assets/icons/bus.webp',rail:'/assets/icons/train.webp',
  stations:'/assets/icons/trainstation.webp',busstations:'/assets/icons/busstation.webp',
  stops:'/assets/icons/busstop.webp'});   // 4.54.2: the owner's bus stop icon (12 Nov 22:50)   // 4.21.2; 4.54.1: the owner's station icons (12 Nov 21:25)
const MENU_PIC=Object.freeze({...PIC_ICON,air:'/assets/planes/airliner.webp'});   // 4.87.0: the menu = the map's key (see menuPic)
// 4.36.0 (owner 11 Nov 00:09-00:11: his own glossy top-down plane icons, "all central to the icon to allow for rotation"):
// the kind (air engine "variant") -> his picture; a kind the page does not know keeps the stand-in drawing. A plane whose
// kind is not known shows the airliner. Balloons stay upright (they point no particular way).
const PLANE_PIC=Object.freeze({airliner:'airliner',widebody:'widebody',turboprop:'turboprop',bizjet:'bizjet',light:'light',
  helicopter:'helicopter',cargo:'cargo',glider:'glider',balloon:'balloon','mil-fighter':'fighter',
  // 4.37.0 (owner 11 Nov 00:32, his second ten): military and emergency services; other military (trainers) the light aircraft
  'mil-tanker':'tanker','mil-transport':'miltransport','mil-helicopter':'milheli','mil-surveillance':'surveillance',military:'light',
  'mil-tiltrotor':'miltransport',tiltrotor:'turboprop',gyroplane:'light',   // 4.95.0 (air 0.11.0 kinds; normally drawn as shapes)
  police:'policeheli',ambulance:'ambulanceheli',coastguard:'coastguardheli','police-plane':'policeplane','ambulance-plane':'ambulanceplane',
  'coastguard-plane':'coastguardplane','ground-emer':'icon-emer','ground-gnd':'icon-gnd','ground-twr':'icon-twr'});   // 4.45.0
function planePic(e){if(e.component!=='air')return null;if(e.variant==null)return 'airliner';
  return typeof e.variant==='string'&&Object.hasOwn(PLANE_PIC,e.variant)?PLANE_PIC[e.variant]:null;}
// 4.84.0 (Rob 18 Nov 12:05-12:20: "having a box round the planes makes them too big"; "keeping our cartoony ones for the
// airliners ... but using the spitfire and unusual ones especially military"; "a shadow under them to give the impression
// of height"): planes have no box. A plane whose type code has a free top-down shape (VRS Custom Markers, CC0; see
// /assets/planes/SHAPES-LICENCE.txt) is drawn as that shape; everything else keeps Rob's own picture. The shapes file
// is only asked for once planes with a type code arrive (about 48 KB, kept a day).
let PLANE_SHAPES=null,planeShapesAsked=0;
function planeShape(e){
  if(KIDS||e.component!=='air'||typeof e.typeCode!=='string'||isGround(e))return null;
  if(!PLANE_SHAPES){
    if(Date.now()-planeShapesAsked>60000){planeShapesAsked=Date.now();
      fetch('/assets/planes/shapes.json?v=4.93.0').then(r=>r.ok?r.json():null)
        .then(j=>{if(j&&j.codes&&j.shapes)PLANE_SHAPES=j;}).catch(()=>{});}
    return null;}
  // 4.93.0 (Rob 19 Nov 12:17-12:29): TPA's own shapes for military types the free set lacks; milCodes (A330, 737, King
  // Air, H135/H145, Tutor...) only for military planes - civil ones keep Rob's pictures, as he chose on 18 Nov
  const mil=PLANE_SHAPES.milCodes&&isMilitary(e)&&Object.hasOwn(PLANE_SHAPES.milCodes,e.typeCode)?PLANE_SHAPES.milCodes[e.typeCode]:null;
  const k=mil||(Object.hasOwn(PLANE_SHAPES.codes,e.typeCode)?PLANE_SHAPES.codes[e.typeCode]:null)
    ||({'mil-tiltrotor':'V22',tiltrotor:'V22',gyroplane:'GYRO'})[e.variant]||null;   // 4.95.0: by kind when the type has none
  return k&&Object.hasOwn(PLANE_SHAPES.shapes,k)?k:null;}
function typeShapeSvg(k,e){
  const s=PLANE_SHAPES.shapes[k],svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox',s.vb);svg.setAttribute('focusable','false');svg.setAttribute('class','type-shape');svg.dataset.shape=k;
  const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',s.d);svg.append(p);
  if(e.position?.headingDeg!=null)svg.style.transform='rotate('+e.position.headingDeg+'deg)';
  return svg;}
// The higher it flies, the further away, softer and fainter its shadow (on the ground: right underneath it).
function planeShadow(art,ft){
  if(ft==null){for(const n of ['--shx','--shy','--shb','--sha'])art.style.removeProperty(n);return;}
  const o=Math.min(1+ft/6500,7);
  art.style.setProperty('--shx',(o*0.7).toFixed(1)+'px');art.style.setProperty('--shy',o.toFixed(1)+'px');
  art.style.setProperty('--shb',(1+ft/9000).toFixed(1)+'px');art.style.setProperty('--sha',Math.max(0.45-ft/90000,0.12).toFixed(2));}
// 4.85.0 (Rob 18 Nov 13:20, the plane Easter egg: "something somewhere that unlocks the uk only view, as our adsb
// coverage should be global"; he chose: click the TPA plane logo 5 times, planes "wherever you look", this visit only).
// Unlocked, the map may leave the UK box and the planes engine is asked with world=1: it then also fetches planes round
// wherever the visitor looks (rationed by the engine). Nothing is saved: a refresh or a new visit is the UK again.
// Five more clicks lock it again. Not advertised anywhere.
let WORLD_PLANES=false,UK_LIMITS=null,eggClicks=[];
function setWorldPlanes(on){
  if(!map||WORLD_PLANES===on)return;
  WORLD_PLANES=on;
  try{if(typeof map.setMaxBounds==='function')map.setMaxBounds(on?null:UK_LIMITS);}catch(_){}
  delete fetchedBox.air;if(state.live.air)getComponent('air');
  if(on)toast('✈ World planes unlocked! Scroll anywhere on Earth and zoom in to see the planes there. Click the logo 5 more times to go back to the UK.',10000);
  else{const c=map.getCenter();if(!inBox(c.lng,c.lat,UK_BOX))home();toast('Back to the UK planes',4000);}}
function inBox(lon,lat,b){return lat>=b.south&&lat<=b.north&&lon>=b.west&&lon<=b.east;}
function eggClick(){const t=Date.now();eggClicks=eggClicks.filter(x=>t-x<3000);eggClicks.push(t);
  if(eggClicks.length>=5){eggClicks=[];setWorldPlanes(!WORLD_PLANES);}}
// 4.92.0: the way a turning plane points now (its reported heading plus the turn so far); others: as reported
function headingNow(e,now){const p=e.position,m=state.live[e.component]?e.motion:null;if(p?.headingDeg==null)return null;
  if(!m||m.mode!=='heading'||!m.turn||!m.turnS)return p.headingDeg;
  const t=Math.max(0,Math.min((now-Date.parse(e.observedAt))/1000,m.turnS,m.maxSeconds));
  return Math.round(((p.headingDeg+m.turn*t)%360+360)%360*10)/10;}
function planeTurn(e,hd){if(e.variant==='ground-twr')return '';
  if(hd!=null&&hd!==e.position?.headingDeg)e={...e,position:{...e.position,headingDeg:hd}};
  // 4.45.0: the owner's van and fire engine icons are drawn front-down, so they turn half a turn further
  const extra=e.variant==='ground-emer'||e.variant==='ground-gnd'?180:0;
  return e.variant!=='balloon'&&e.position?.headingDeg!=null?'rotate('+((e.position.headingDeg+extra)%360)+'deg)':'';}
// 4.39.0 (owner 11 Nov 13:04, traffic: incident markers and roadworks happening now): TPA's own road-sign style icons,
// drawn here (no outside pictures): warning triangles for incidents, the red ring for a closed road.
const PLANE_PHOTOS=new Set(['chinook','a400m','c17','c5m','falcon900','a320','b737','b787','b777','a220','atr72','tutor','prefect','cessna','piper','typhoon','kingair','a330','a350','embraer','robinson','trainer',
  'a380','b747','b767','apache','wildcat','twinotter','global','falcon','texan','l159','emer','gnd','twr']);   // 4.45.0: the fourth batch and airport vehicles   // 4.43.0
// 4.43.0: when the type has no picture of its own, a typical picture of its kind, tagged as such; no kind known: a faded
// airliner tagged "Type not known" (as for buses). Kinds without a picture yet show none.
const PLANE_KIND_PHOTOS={airliner:['airliner','Typical airliner'],widebody:['widebody','Typical large airliner'],
  turboprop:['turboprop','Typical propeller airliner'],bizjet:['bizjet','Typical business jet'],light:['light','Typical light aircraft'],
  helicopter:['helicopter','Typical helicopter'],cargo:['cargo','Typical cargo plane'],'mil-transport':['miltransport','Typical military transport'],
  glider:['glider','Typical glider'],'mil-helicopter':['milheli','Typical military helicopter'],police:['police','Typical police helicopter'],
  ambulance:['ambulance','Typical air ambulance'],coastguard:['coastguard','Typical coastguard helicopter']};
const TRI='<path d="M16 2.5 30.5 28H1.5Z" fill="#c8102e"/><path d="M16 8.2 25.6 25H6.4Z" fill="#fff"/>';
const ROAD_ART={
  roadworks:TRI+'<circle cx="17.6" cy="13.2" r="1.5" fill="#111"/><path d="M17 15.2 14.4 18.4 12.6 22.6M17 15.2l1.5 4.2 1.7 3.2M16.3 16.8l-3.5 1M14 18.6 9.6 15.2" stroke="#111" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M19.4 23.2h4.6l-2.3-3z" fill="#111"/>',
  accident:TRI+'<path d="M9.6 21.6l3-4.6 4.2.8.6 3.2zM22.4 21.6l-3-4.6-4.2.8-.6 3.2z" fill="#111"/><path d="M16 12.4v2.8" stroke="#111" stroke-width="1.6" stroke-linecap="round"/>',
  'broken-down':TRI+'<path d="M10 21.4v-2.6l1.8-3.4h8.4l1.8 3.4v2.6z" fill="#111"/><circle cx="12.6" cy="22" r="1.3" fill="#111"/><circle cx="19.4" cy="22" r="1.3" fill="#111"/>',
  'lane-closed':TRI+'<path d="M12.6 23V15.6l2.8-3M19.4 23V12.6" stroke="#111" stroke-width="1.8" fill="none" stroke-linecap="round"/>',
  hazard:TRI+'<path d="M16 12.4v6.4" stroke="#111" stroke-width="2.4" stroke-linecap="round"/><circle cx="16" cy="22.2" r="1.4" fill="#111"/>',
  closed:'<circle cx="16" cy="16" r="14" fill="#c8102e"/><circle cx="16" cy="16" r="9.4" fill="#fff"/>'};
function buildArtwork(e){
  const icon=document.createElement('span');icon.className='review-art';icon.setAttribute('aria-hidden','true');
  if(e.component==='road'){icon.classList.add('road-sign');icon.innerHTML='<svg viewBox="0 0 32 32" focusable="false">'+(ROAD_ART[e.variant]||ROAD_ART.hazard)+'</svg>';return icon;}
  if(KIDS&&KID_ART[e.component]){
    icon.classList.add('kid-art');icon.innerHTML=KID_ART[e.component];
    const svg=icon.querySelector('svg');if(svg)svg.style.transform=kidFlip(e);   // planes face the way they fly
  }else if(ICON_PATHS[e.component]){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('focusable','false');
    const [shape,detail]=artPair(e);
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',shape);path.setAttribute('fill-rule','evenodd');svg.append(path);
    if(detail){const acc=document.createElementNS('http://www.w3.org/2000/svg','path');acc.setAttribute('d',detail);acc.setAttribute('class','acc');svg.append(acc);}
    // Ships always point up on-screen. Course remains as data, not an icon rotation.
    if(e.component==='air'&&e.position?.headingDeg!=null)svg.style.transform='rotate('+e.position.headingDeg+'deg)';
    // 4.21.2 (owner 5 Nov 20:11-20:15, option B): buses and trains show the owner's front-on picture inside their frame,
    // so trains no longer need mirroring; the drawing comes back only if the picture cannot load.
    const plane=planePic(e),shapeKey=planeShape(e);
    if(shapeKey)icon.append(typeShapeSvg(shapeKey,e));   // 4.84.0: the unusual types' own top-down shape
    else if(PIC_ICON[e.component]||plane){const img=document.createElement('img');img.className='pic-icon'+(plane?' plane-pic p-'+plane:'');
      img.src=plane?'/assets/planes/'+plane+'.webp':PIC_ICON[e.component];img.alt='';
      if(plane)img.style.transform=planeTurn(e);
      img.decoding='async';img.draggable=false;img.addEventListener('error',()=>img.replaceWith(svg),{once:true});icon.append(img);}
    else icon.append(svg);
  }else{icon.textContent='≋';}
  if(e.component==='rail'||e.component==='bus'||e.component==='stations'||(e.component==='stops'&&e.badge)){   // 4.53.0: a stop shows its letter, if it has one
    const badge=document.createElement('span');badge.className='review-route';
    // Only an alphanumeric suffix is used. Sample route/headcode is not a transport algorithm.
    badge.textContent=e.badge||String(e.id.split('-').pop()||'MOCK').replace(/[^a-zA-Z0-9]/g,'').slice(0,8)||'MOCK';
    icon.append(badge);
  }
  return icon;
}
let detailsTimer=0;
// 4.33.0 (owner 10 Nov 21:24: planes are "used much more by dedicated plane enthusiasts ... information available by click,
// but not all at once, so the casual map user isnt overwhelmed"): rows an engine marks 'more' sit behind "More details".
// Closed at first; once opened it stays open for the next box (kept in this browser, if it allows).
let moreOpen=(()=>{try{return localStorage.getItem('tpa-more-details')==='1';}catch(_){return false;}})();
async function loadDetails(e,box){
  clearTimeout(detailsTimer);
  if(!state.live[e.component])return;
  try{
    const r=await fetch(`/api/v4/components/${e.component}/details?id=${encodeURIComponent(e.id)}`,{cache:'no-store'});
    if(!r.ok){box.replaceChildren();return;}      // an engine without details: nothing extra
    const d=await r.json();
    if(!state.selected||state.selected.id!==e.id||!box.isConnected)return;
    const rows=[],more=[];
    // 4.95.0 (Rob 19 Nov 13:44, the enthusiasts' extras are for /planes only): rows marked 'pro' show only there
    const wanted=(Array.isArray(d.rows)?d.rows:[]).filter(row=>PLANES||row.pro!==true);
    if(e.component==='air'&&PLANES)trailFrom(e,d);
    for(const row of wanted.slice(0,PLANES?48:24)){
      const line=document.createElement('div');line.className='detail-row';
      const k=document.createElement('span');k.className='detail-label';k.textContent=String(row.label||'').slice(0,30);
      const v=document.createElement('span');v.className='detail-value';v.textContent=String(row.value||'').slice(0,120);
      // 4.71.0 (tester 17 Nov 19:30, a lane closure: "the panel itself didn't answer 'where exactly?'"): the road's number and
      // name from the street map, and the nearest town, in place of only "Main road (trunk)"
      if(e.component==='road'&&row.label==='Road'&&e.position)v.textContent=roadWhere(v.textContent,
        roadFor(keyOf(e),e.position.lon,e.position.lat,ROAD_CLASS[v.textContent]||'',window.TPA_MAP_ROADS?.(e.position.lon,e.position.lat)||[],map?.getZoom?.()??state.z),
        townNear(e.position.lon,e.position.lat)).slice(0,120);
      // 4.32.0 (owner 14:19 "match ... registration plates to buses"): a number plate looks like one
      if(row.label==='Number plate'&&/^[A-Z0-9 ]{2,10}$/.test(v.textContent))v.classList.add('plate');
      line.append(k,v);(row.more===true?more:rows).push(line);}
    if(more.length){
      const btn=document.createElement('button');btn.type='button';btn.className='detail-more-btn';
      const sect=document.createElement('div');sect.className='detail-more';sect.id='tpa-detail-more';sect.append(...more);
      btn.setAttribute('aria-controls',sect.id);
      const show=()=>{btn.textContent=moreOpen?'Fewer details \u25b4':'More details \u25be';btn.setAttribute('aria-expanded',String(moreOpen));sect.hidden=!moreOpen;};
      btn.addEventListener('click',ev=>{ev.stopPropagation();moreOpen=!moreOpen;try{localStorage.setItem('tpa-more-details',moreOpen?'1':'0');}catch(_){}show();});
      show();rows.push(btn,sect);}
    if(typeof d.note==='string'&&d.note){const n=document.createElement('small');n.className='detail-note';n.textContent=d.note.slice(0,160);rows.push(n);}
    // 4.23.0 (owner 6 Nov 09:40-09:41: "This is the Northern (time) service from (start) to (destination). Stopping at ...
    // those already done in grey, those not yet in green ... then next stop"; "if the stopping at list is large, make it a
    // scrollable list, within the box"). Generic: any engine may send a summary and its stops (done / here / due).
    const stops=Array.isArray(d.stops)?d.stops.filter(s=>s&&typeof s.name==='string'&&['done','here','due'].includes(s.state)).slice(0,120):[];   // names up to 80 letters, wrapped
    const trip=[];
    if(typeof d.summary==='string'&&d.summary){const p=document.createElement('p');p.className='detail-summary';p.textContent=tidyName(d.summary.slice(0,200));trip.push(p);}
    // 4.33.0: what it is doing ("Coming in to land at Leeds Bradford") and how far along ("About halfway: ...")
    for(const [key,cls] of [['doing','detail-doing'],['progress','detail-progress']])
      if(typeof d[key]==='string'&&d[key]){const p=document.createElement('p');p.className=cls;p.textContent=d[key].slice(0,120);trip.push(p);}
    if(stops.length>=2){
      const when=s=>s.expected&&s.expected!==s.time?`${s.time||''} (expected ${s.expected})`:(s.time||'');
      const here=stops.find(s=>s.state==='here'),next=stops.find(s=>s.state==='due');
      const line=document.createElement('p');line.className='detail-next';
      if(here){const b=document.createElement('b');b.textContent='Now at ';line.append(b,tidyName(here.name));}
      if(next){const b=document.createElement('b');b.textContent=(here?' · ':'')+'Next stop: ';line.append(b,`${tidyName(next.name)}, ${when(next)}`.replace(/, $/,''));}
      if(!here&&!next){const b=document.createElement('b');b.textContent='Journey finished';line.append(b);}
      trip.push(line);
      const head=document.createElement('div');head.className='detail-stops-head';head.textContent='Calling at';trip.push(head);
      const list=document.createElement('ol');list.className='detail-stops';list.setAttribute('aria-label','Calling at');
      for(const s of stops){const li=document.createElement('li');li.className='stop '+s.state;
        const t=document.createElement('span');t.className='stop-time';t.textContent=s.time||'';
        const n=document.createElement('span');n.className='stop-name';n.textContent=tidyName(s.name.slice(0,80));
        li.append(t,n);
        if(s.state==='due'&&s.expected&&s.expected!==s.time){const x=document.createElement('span');x.className='stop-exp';x.textContent='exp '+s.expected;li.append(x);}
        if(s.state==='here'){const x=document.createElement('span');x.className='stop-exp';x.textContent='here now';li.append(x);}
        list.append(li);}
      trip.push(list);
      // the stop that matters (where it is, or the next one) scrolled into view inside the list, never the page
      requestAnimationFrame(()=>{const cur=list.querySelector('.stop.here')||list.querySelector('.stop.due');
        if(cur)list.scrollTop=Math.max(0,cur.offsetTop-24);});
    }
    rows.unshift(...trip);
    // 4.52.0 (owner 12 Nov 17:03-17:25: live departure boards, departures and arrivals, calling points, station alerts)
    if(d.board&&Array.isArray(d.board.tabs)&&d.board.tabs.length)rows.unshift(buildBoard(e,d.board));
    // 4.16.0 (owner 5 Nov 00:19: "a picture of the type in an information window when you click on each"): only TPA's own drawings
    const drawn=typeof d.picture==='string'&&Object.hasOwn(TRAIN_ALIAS,d.picture)?TRAIN_ALIAS[d.picture]:d.picture;
    const wide=e.component==='rail'&&typeof d.picture==='string'&&Object.hasOwn(TRAIN_CLASSES,drawn);
    const pic=e.component==='rail'&&typeof d.picture==='string'?(wide?TRAIN_CLASSES[drawn]:Object.hasOwn(RAIL_KINDS,d.picture)?RAIL_KINDS[d.picture]:null):null;
    if(pic){const box2=document.createElement('div');box2.className='detail-picture '+e.component+(wide?' wide':'');
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',wide?'0 2 100 23':'0 6 32 21');svg.setAttribute('aria-hidden','true');
      const a=document.createElementNS('http://www.w3.org/2000/svg','path');a.setAttribute('d',pic[0]);a.setAttribute('fill-rule','evenodd');svg.append(a);
      if(pic[1]){const b=document.createElementNS('http://www.w3.org/2000/svg','path');b.setAttribute('d',pic[1]);b.setAttribute('class','acc');svg.append(b);}
      // 4.21.0 (owner 5 Nov 17:20-19:15: "More detailed, instead of a cheese wedge"): TPA's own detailed picture of each
      // class (made by the owner, neutral colours, no logos), shown bigger on a light panel; the drawing stays if it fails.
      if(wide&&TRAIN_PHOTOS.has(d.picture)){const img=document.createElement('img');img.src='/assets/trains/'+d.picture+'.webp';
        img.alt='Picture of the train type: '+(rows.map(r=>r.textContent).find(t=>t.startsWith('Train type'))||'').replace('Train type','').trim();
        img.decoding='async';img.addEventListener('error',()=>{box2.classList.remove('photo');img.replaceWith(svg);},{once:true});
        box2.classList.add('photo');box2.append(img);}
      else box2.append(svg);
      rows.unshift(box2);}
    // 4.22.0 (owner 5 Nov 20:51-21:04: "just a generic bus pic for the info box as it looks a bit barren"): the owner's own
    // picture (Geoff driving a TPA bus, no operator logos) on the same light panel as the trains; left out if it fails.
    // 4.29.0 (owner 10 Nov 11:07-11:17: "the same standard as the trains, with nicely rendered ones"): the owner's four
    // generic pictures - the engine names one when it knows the kind (a coach company); otherwise a typical double decker.
    if(e.component==='bus'){const box3=document.createElement('div');box3.className='detail-picture bus photo';
      // 4.30.0 (owner 10 Nov 11:49: "every bus seems to be a double decker"; option A): when the kind is not known, a faded
      // picture with a "Type not known" tag - never a guess dressed up as fact.
      const known=typeof d.picture==='string'&&Object.hasOwn(BUS_PICTURES,d.picture),kind=known?d.picture:'sd',geoff=GEOFF.ids.has(e.id);
      const img=document.createElement('img');img.src=geoff?'/assets/buses/geoff.webp':'/assets/buses/'+kind+'.webp';
      img.alt=geoff?'Geoff the cat driving a TPA bus':known?BUS_PICTURES[kind]:'Picture of a bus (type not known)';img.decoding='async';
      if(geoff){box3.classList.add('geoff');const tag=document.createElement('span');tag.className='pic-tag geoff-tag';tag.textContent="You've found Geoff on "+geoffDay(GEOFF.date)+'!';box3.append(tag);}
      else if(!known){box3.classList.add('unknown');const tag=document.createElement('span');tag.className='pic-tag';tag.textContent='Type not known';box3.append(tag);}
      img.addEventListener('error',()=>box3.remove(),{once:true});box3.append(img);rows.unshift(box3);}
    // 4.43.0 (owner 11 Nov 15:43: "I would like to add plane pictures ... static ones"): TPA's own picture of the plane's type
    // (made by the owner, neutral colours, no logos) on the same light panel as trains and buses; none when the type has
    // no picture yet - never a picture of the wrong type. Left out if it fails to load.
    if(e.component==='air'){
      const own=typeof d.photo==='string'&&PLANE_PHOTOS.has(d.photo),kind=!own&&typeof d.picture==='string'&&Object.hasOwn(PLANE_KIND_PHOTOS,d.picture)?PLANE_KIND_PHOTOS[d.picture]:null;
      const unknown=!own&&!kind&&!d.picture;
      if(own||kind||unknown){const box4=document.createElement('div');box4.className='detail-picture air photo';
        const img=document.createElement('img');img.src='/assets/planes/box/'+(own?d.photo:kind?kind[0]:'airliner')+'.webp';img.decoding='async';
        img.alt=own?'Picture of the aircraft type: '+(typeof d.photoName==='string'?d.photoName:d.photo):kind?kind[1]:'Picture of an aircraft (type not known)';
        if(kind||unknown){const tag=document.createElement('span');tag.className='pic-tag';tag.textContent=kind?kind[1]:'Type not known';box4.append(tag);}
        if(unknown)box4.classList.add('unknown');
        img.addEventListener('error',()=>box4.remove(),{once:true});box4.append(img);rows.unshift(box4);}}
    const keepScroll=[selected.scrollTop,box.querySelector('.board-list')?.scrollTop||0];
    box.replaceChildren(...rows);
    selected.scrollTop=keepScroll[0];const bl=box.querySelector('.board-list');if(bl)bl.scrollTop=keepScroll[1];
    // 4.77.0: the small picture beside Follow me! is the same picture as the big one (kept, not rebuilt, when it is unchanged)
    const th=selected.querySelector('.follow-thumb'),tp=box.querySelector('.detail-picture');
    if(th){const img=tp?.querySelector('img'),svg=tp?.querySelector('svg'),key=img?img.getAttribute('src'):svg?'svg:'+(d.picture||''):'';
      if(th.dataset.key!==key){th.dataset.key=key;th.replaceChildren();
        if(img){const s2=document.createElement('img');s2.src=img.getAttribute('src');s2.alt='';s2.decoding='async';
          s2.addEventListener('error',()=>{th.hidden=true;},{once:true});th.append(s2);}
        else if(svg)th.append(svg.cloneNode(true));}
      const plate=selected.querySelector('.follow-plate');
      if(plate){const v=box.querySelector('.detail-value.plate')||[...box.querySelectorAll('.detail-label')].find(x=>/^(Number plate|Registration)$/.test(x.textContent.trim()))?.nextElementSibling;
        const txt=(v?.textContent||'').trim();plate.textContent=txt;plate.hidden=!txt||txt.length>12||/not known/i.test(txt);plate.classList.toggle('air',e.component==='air');}
      th.hidden=!key;th.className='follow-thumb '+e.component+(tp?.classList.contains('unknown')?' unknown':'')+(svg&&!img?' drawn':'');}
  }catch(err){}
  // the next stop changes as the vehicle moves: ask again while it stays selected (a station's board: every 30 s)
  detailsTimer=setTimeout(()=>{if(state.selected&&state.selected.id===e.id&&box.isConnected)loadDetails(e,box);},e.component==='stations'||e.component==='stops'||e.component==='busstations'?30000:20000);
}
// 4.52.0: a station's live board. Tabs (Departures | Arrivals), the station's alerts, then one line per train: time,
// where to (or from), platform, on time / expected / cancelled. Tap a train for its calling points and any reason given.
// What is open stays open when the board refreshes.
const boardUI={id:null,tab:0,open:new Set(),alerts:false};
function buildBoard(e,b){
  if(boardUI.id!==e.id){boardUI.id=e.id;boardUI.tab=0;boardUI.open=new Set();boardUI.alerts=false;}
  const tabsList=b.tabs.filter(t=>t&&typeof t.name==='string'&&Array.isArray(t.rows)).slice(0,3);
  if(boardUI.tab>=tabsList.length)boardUI.tab=0;
  const bus=e.component==='stops'||e.component==='busstations';   // 4.53.0: a bus stop's board; 4.54.0: a bus station's
  const station=e.component==='busstations';
  if(boardUI.id!==e.id||!station)boardUI.route='';
  const wrap=el('div','board'+(bus?' bus-board':'')+(station?' bus-station':''));const redraw=()=>{const list=wrap.querySelector('.board-list'),top=list?list.scrollTop:0;
    const fresh=buildBoard(e,b);wrap.replaceWith(fresh);const l2=fresh.querySelector('.board-list');if(l2)l2.scrollTop=top;};
  // 4.53.0 (owner 12 Nov 18:40: "Stop letter + direction", "Routes from this stop"): above the tabs
  if(bus&&typeof b.about==='string'&&b.about)wrap.append(el('p','board-about',b.about.slice(0,120)));
  if(bus&&Array.isArray(b.routes)&&b.routes.length){const rr=el('div','board-routes');rr.setAttribute('aria-label',station?'Show one route only':'Routes from this stop');
    const names=b.routes.slice(0,60).filter(r=>typeof r==='string'&&r).map(r=>r.slice(0,12));
    if(station){   // 4.54.0 (owner 12 Nov 20:22, "Filter by route"): tap a route to see only its buses; tap again (or All) for every bus
      if(boardUI.route&&!names.includes(boardUI.route))boardUI.route='';
      const pick=r=>{const c=el('button','route-chip route-pick'+(r===''?' all':''),r===''?'All':r);c.type='button';c.setAttribute('aria-pressed',String(boardUI.route===r));
        c.onclick=ev=>{ev.stopPropagation();boardUI.route=boardUI.route===r?'':r;boardUI.open=new Set();redraw();};return c;};
      rr.append(pick(''),...names.map(pick));}
    else for(const r of names)rr.append(el('span','route-chip',r));
    wrap.append(rr);}
  const head=el('div','board-head'),tabs=el('div','board-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label',station?'Bus station board':bus?'Bus stop board':'Station board');
  tabsList.forEach((t,i)=>{const n=station&&boardUI.route?t.rows.filter(r=>r&&r.route===boardUI.route).length:t.rows.length,btn=el('button','board-tab',t.name.slice(0,20)+(n?' ('+n+')':''));btn.type='button';btn.setAttribute('role','tab');
    btn.setAttribute('aria-selected',String(i===boardUI.tab));btn.onclick=ev=>{ev.stopPropagation();if(boardUI.tab!==i){boardUI.tab=i;redraw();}};tabs.append(btn);});
  head.append(tabs);
  if(typeof b.updated==='string'&&/^\d\d:\d\d$/.test(b.updated))head.append(el('span','board-updated','Updated '+b.updated));
  wrap.append(head);
  const alerts=(Array.isArray(b.alerts)?b.alerts:[]).filter(a=>typeof a==='string'&&a).slice(0,5);
  if(alerts.length){const box=el('div','board-alerts'+(boardUI.alerts?' open':''));
    for(const a of alerts){const p=el('p','board-alert');const i=el('span','board-alert-mark','!');i.setAttribute('aria-hidden','true');p.append(i,a.slice(0,400));box.append(p);}
    const long=alerts.length>1||alerts[0].length>110;
    if(long){const t=el('button','board-alert-more',boardUI.alerts?'Show less':'Read all '+(alerts.length>1?alerts.length+' messages':'of this message'));t.type='button';
      t.onclick=ev=>{ev.stopPropagation();boardUI.alerts=!boardUI.alerts;redraw();};box.append(t);}
    else box.classList.add('open');
    wrap.append(box);}
  const tab=tabsList[boardUI.tab]||tabsList[0],arr=/arriv/i.test(tab.name);
  if(!tab.rows.length){wrap.append(el('p','board-empty',typeof tab.empty==='string'&&tab.empty?tab.empty.slice(0,80):'Nothing due.'));return wrap;}
  if(bus)return busBoard(e,b,wrap,tab,arr,redraw,station);
  const cols=el('div','board-cols');cols.setAttribute('aria-hidden','true');
  cols.append(el('span','',arr?'Due':'Time'),el('span','',arr?'From':'To'),el('span','','Plat'),el('span','',arr?'Expected':'Expected'));wrap.append(cols);
  const list=el('ul','board-list');
  for(const r of tab.rows.slice(0,20)){
    if(!r||typeof r.time!=='string'||typeof r.place!=='string')continue;
    const key=tab.name+'|'+r.time+'|'+r.place,open=boardUI.open.has(key);
    const li=el('li','board-item '+(['ontime','late','cancelled'].includes(r.state)?r.state:'unknown')+(open?' open':''));
    const btn=el('button','board-row');btn.type='button';btn.setAttribute('aria-expanded',String(open));
    const place=el('span','b-place');place.append(el('span','b-name',tidyName(r.place.slice(0,80))));
    const sub=[typeof r.via==='string'?r.via:'',typeof r.operator==='string'?r.operator:''].filter(Boolean).join(' · ');
    if(sub)place.append(el('small','b-sub',sub.slice(0,90)));
    btn.append(el('span','b-time',r.time.slice(0,5)),place,el('span','b-plat',typeof r.platform==='string'?r.platform.slice(0,4):''),el('span','b-status',typeof r.status==='string'?r.status.slice(0,20):''));
    btn.setAttribute('aria-label',`${r.time} ${arr?'from':'to'} ${r.place}${r.platform?', platform '+r.platform:''}${r.status?', '+r.status:''}. ${open?'Hide':'Show'} ${arr?'previous':'calling'} stops`);
    btn.onclick=ev=>{ev.stopPropagation();if(boardUI.open.has(key))boardUI.open.delete(key);else boardUI.open.add(key);redraw();};
    li.append(btn);
    if(open){const more=el('div','board-more');
      if(typeof r.reason==='string'&&r.reason)more.append(el('p','b-reason',r.reason.slice(0,160)));
      const calls=(Array.isArray(r.calls)?r.calls:[]).filter(c=>c&&typeof c.name==='string').slice(0,60);
      if(calls.length){more.append(el('div','b-calls-head',arr?'Calling before here at':'Calling at'));
        const ol=el('ol','b-calls');
        for(const c of calls){const s=el('li','b-call'+(c.expected==='Cancelled'?' cancelled':''));
          s.append(el('span','b-call-time',typeof c.time==='string'?c.time:''),el('span','b-call-name',tidyName(c.name.slice(0,60))));
          const x=typeof c.expected==='string'?c.expected:'';
          if(x&&x!=='On time'&&x!==c.time)s.append(el('span','b-call-exp',/^\d\d:\d\d$/.test(x)?(arr?'at ':'exp ')+x:x));
          ol.append(s);}
        more.append(ol);}
      else if(!more.childNodes.length)more.append(el('p','b-reason','No stops listed for this train.'));
      li.append(more);}
    list.append(li);}
  wrap.append(list);
  return wrap;
}
// 4.53.0 (owner 12 Nov 18:40, bus stop boards): one line per bus - route number, where it is going (and the company),
// its timetable time, and "2 min" / "Due" when TPA can see it live (amber when running late) or "Timetable" when it has
// not been seen yet. Tap a bus for how it is running, its next stops, and "Show on map" for a live one.
// 4.54.0 (owner 12 Nov 20:22, bus stations "like the train ones"): a bus station's board reads like a railway station's -
// time, where to (with the route number in front), the stand, and "Due" - and can show one route only.
function busBoard(e,b,wrap,tab,arr,redraw,station){
  const rows=tab.rows.slice(0,60).filter(r=>r&&typeof r.time==='string'&&typeof r.place==='string'&&(!station||!boardUI.route||r.route===boardUI.route));
  if(!rows.length){wrap.append(el('p','board-empty','No route '+boardUI.route+' buses in the next 2 hours.'));return wrap;}
  const cols=el('div','board-cols');cols.setAttribute('aria-hidden','true');
  if(station)cols.append(el('span','',arr?'Due':'Time'),el('span','',arr?'From':'To'),el('span','','Stand'),el('span','',arr?'Arriving':'Due'));
  else cols.append(el('span','','Route'),el('span','',arr?'From':'To'),el('span','',arr?'Due':'Time'),el('span','',arr?'Arriving':'Due'));
  wrap.append(cols);
  const list=el('ul','board-list');
  for(const r of rows){
    const route=typeof r.route==='string'?r.route.slice(0,12):'';
    const key=tab.name+'|'+r.time+'|'+route+'|'+r.place,open=boardUI.open.has(key);
    const li=el('li','board-item '+(['ontime','late','cancelled'].includes(r.state)?r.state:'unknown')+(r.live===true?' live':' timetable')+(open?' open':''));
    const btn=el('button','board-row');btn.type='button';btn.setAttribute('aria-expanded',String(open));
    const place=el('span','b-place');
    if(station){const line=el('span','b-name');if(route)line.append(el('span','route-chip in-row',route));line.append(tidyName(r.place.slice(0,80)));place.append(line);}
    else place.append(el('span','b-name',tidyName(r.place.slice(0,80))));
    if(typeof r.operator==='string'&&r.operator)place.append(el('small','b-sub',r.operator.slice(0,40)));
    const status=typeof r.status==='string'?r.status.slice(0,20):'';
    const stand=typeof r.platform==='string'?r.platform.slice(0,4):'';
    if(station)btn.append(el('span','b-time',r.time.slice(0,5)),place,el('span','b-plat',stand),el('span','b-status',status));
    else btn.append(el('span','b-time b-route',route||'—'),place,el('span','b-plat b-clock',r.time.slice(0,5)),el('span','b-status',status));
    btn.setAttribute('aria-label',`${route?'Route '+route+' ':''}${arr?'from':'to'} ${r.place}, ${r.time}${stand?', stand '+stand:''}${status?', '+(status==='Timetable'?'timetable time, not seen live yet':status):''}. ${open?'Hide':'Show'} ${arr?'previous':(station?'calling':'next')} stops`);
    btn.onclick=ev=>{ev.stopPropagation();if(boardUI.open.has(key))boardUI.open.delete(key);else boardUI.open.add(key);redraw();};
    li.append(btn);
    if(open){const more=el('div','board-more');
      if(typeof r.reason==='string'&&r.reason)more.append(el('p','b-reason',r.reason.slice(0,160)));
      if(typeof r.bus==='string'&&/^bus:[A-Za-z0-9:_.-]{1,124}$/.test(r.bus)&&Number.isFinite(r.lat)&&Number.isFinite(r.lon)){
        const go=el('button','b-show','Show on map');go.type='button';
        go.onclick=ev=>{ev.stopPropagation();const label=(route?'Bus '+route:'Bus')+' to '+r.place.slice(0,80);
          const v={component:'bus',id:r.bus,label,badge:route.replace(/[^A-Za-z0-9]/g,'').slice(0,8)||null,quality:'observed',
            observedAt:new Date(serverNow()).toISOString(),position:{lon:r.lon,lat:r.lat,headingDeg:null}};
          gotoPlace(r.lon,r.lat,Math.max(16,state.z||16),label);selectEntity(v);};
        more.append(go);}
      const calls=(Array.isArray(r.calls)?r.calls:[]).filter(c=>c&&typeof c.name==='string').slice(0,60);
      if(calls.length){more.append(el('div','b-calls-head',arr?'Calling before here at':station?'Calling at':'Next stops'));
        const ol=el('ol','b-calls');
        for(const c of calls){const s=el('li','b-call');
          s.append(el('span','b-call-time',typeof c.time==='string'?c.time:''),el('span','b-call-name',tidyName(c.name.slice(0,60))));
          const x=typeof c.expected==='string'?c.expected:'';
          if(x&&x!==c.time)s.append(el('span','b-call-exp',/^\d\d:\d\d$/.test(x)?'exp '+x:x));
          ol.append(s);}
        more.append(ol);}
      li.append(more);}
    list.append(li);}
  wrap.append(list);
  return wrap;
}
// 4.17.1 (owner 5 Nov 11:42, train 1R62: title "departed Manchester Piccadilly" while the box said "Arrived at Manchester
// Oxford Road"): an open box's title follows the vehicle's latest description, like the rest of the box.
function liveTitle(id,f){const s=state.selected;if(!s||KIDS||s.component!==id)return;
  const e=f.entities.find(x=>x.id===s.id);if(!e||typeof e.label!=='string'||e.label===s.label)return;
  s.label=e.label;const t=selected.querySelector('.box-title');
  if(t&&t.lastChild&&t.lastChild.nodeType===3)t.lastChild.textContent=' '+e.label;}
function selectEntity(e){
  if(KIDS)return kidsBubble(e);
  state.selected=e;
  const h=document.createElement('strong');h.className='box-title';h.append(ownIcon(e.component),document.createTextNode(' '+e.label));
  const live=!!state.live[e.component];
  const seen=new Date(e.observedAt).toLocaleTimeString('en-GB',{hour12:false});
  const info=document.createElement('small');info.textContent=infoText(e,!!whereNow(e,serverNow())?.est);void seen;
  // 4.4.1 (owner 1 Nov 13:59: the data-source lines "look busy and cluttered. Simple experience for the end
  // user"): live vehicles' sources stay in the map's credit line and "Data sources & credits", not here.
  // Pretend vehicles keep their warning.
  const warning=document.createElement('small');warning.className=live?'live-source':'review-no-live';warning.textContent=live?'':'FICTIONAL DEMONSTRATION — NOT AN ACTUAL VEHICLE';
  if(live)warning.hidden=true;
  const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close target details');
  close.onclick=()=>{selected.classList.add('hidden');state.selected=null;paint();};
  // 4.4.0 (owner 1 Nov 13:42): more about this vehicle from its own engine (start, end, next stop for a bus)
  const more=document.createElement('div');more.className='vehicle-details';
  // 4.10.0 (owner 1 Nov 17:49): "Follow me!" - also here, for phones and tablets (no right-click)
  const fb=e.component==='road'||e.component==='stations'||e.component==='stops'||e.component==='busstations'?null:document.createElement('button');   // 4.39.0: roadworks and incidents stay put (4.52.0: and stations, 4.53.0: and bus stops)
  if(fb){fb.type='button';fb.className='follow-button';fb.onclick=()=>{if(isFollowing(e))stopFollow();else startFollow(e);};}
  const rb=REPORT_ON?el('button','report-button','Report this'):null;
  if(rb){rb.type='button';rb.onclick=()=>copyReport(e);}
  // 4.51.0: on phones the box is a pull-up panel; this bar (or a swipe) shows more or less of it. Short each time it opens.
  const grab=document.createElement('button');grab.type='button';grab.className='sheet-grab';
  const setOpen=open=>{selected.classList.toggle('open',open);grab.setAttribute('aria-expanded',String(open));grab.setAttribute('aria-label',open?'Show less':'Show more');};
  grab.onclick=ev=>{ev.stopPropagation();setOpen(!selected.classList.contains('open'));};setOpen(false);
  selected.classList.toggle('station-box',e.component==='stations'||e.component==='stops'||e.component==='busstations');   // 4.52.0: a wider box for a departure board
  // 4.71.0 (tester 17 Nov 19:30: "easy to miss ... a long way down"): Follow me! straight under the name
  // 4.77.0 (owner 18 Nov 03:56, on his phone: "a small pic of the bus next to follow me would look nice"): a small copy of
  // the vehicle's picture beside Follow me!, filled in when its details arrive. Shown on phones in the short panel only (the
  // big picture is further down there); hidden when the panel is pulled up and the big one is in view.
  $('toast')?.classList.add('hidden');   // 4.77.0: a note (such as the night note) never sits over the vehicle's panel
  const frow=fb?el('div','follow-row'):null;
  // 4.77.0 (owner 18 Nov 04:02: "one of the most wow bits of info is the registration number"): the number plate (a plane's
  // registration) under the small picture, when it is known
  if(frow){const fp=el('span','follow-pic'),th=el('span','follow-thumb'),pl=el('span','follow-plate');th.hidden=true;pl.hidden=true;
    th.setAttribute('aria-hidden','true');fp.append(th,pl);frow.append(fb,fp);}
  selected.replaceChildren(...[grab,h,info,frow,more,rb,warning,close].filter(Boolean));if(!isFollowing(e))stopFollow();updateFollowUI();selected.classList.remove('hidden');
  if(isPhone())requestAnimationFrame(()=>keepAboveSheet(e));
  $('map-place').textContent=e.label.toUpperCase();paint();
  loadDetails(e,more);
}
// 4.1.0 smooth movement. Markers are kept and moved, not rebuilt. Between real reports a
// live vehicle with a motion hint is carried forward (capped by the engine's maxSeconds),
// and a fresh report is reached by a short slide, never a jump. Any position that is not
// exactly the last real report is marked and described as ESTIMATED.
const REDUCED=!!(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches);
const BLEND_MS=2000,pins=new Map(),shown=new Map(),blends=new Map(),lastReport=new Map();
const corrections=new Map();window.TPA_HQ_CORRECTIONS=id=>corrections.get(id)||0;   // 4.48.0: how often a vehicle's correction was (re)started (tests read it)
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
let glideBudget=GLIDE_MAX,frameAvg=16,lastFrame=0,glideCount=0,frameBase=16.7;
// 4.72.0 (measured 17 Nov 22:50: on a PC drawing ~30 frames a second the budget fell 600 -> 42 -> 15 in 3 minutes and never
// came back, so most icons stepped once a second): "slow" is judged against THIS device's own steady pace (frameBase, a
// floor that drops at once and creeps up slowly), not a fixed 60-frames-a-second screen. Gliding itself is done by the
// graphics chip, so a steady 30 frames a second is fine; only frames well behind the device's own pace cut the budget, and
// it grows back as soon as the device keeps its pace again.
function glideJudge(g,dt){
  g.base=Math.min(50,dt<g.base?g.base*0.7+dt*0.3:g.base+(dt-g.base)*0.0003);   // never takes slower than 20 a second as "its pace"
  g.avg=g.avg*0.98+dt*0.02;
  if(g.avg>g.base*1.6+6&&g.budget>GLIDE_MIN){g.budget=Math.max(GLIDE_MIN,Math.floor(g.budget*0.85));g.avg=g.base*1.3;}
  else if(g.avg<g.base*1.25+2&&g.budget<GLIDE_MAX&&g.full)g.budget=Math.min(GLIDE_MAX,g.budget+10);
  return g;}
window.TPA_HQ_GLIDE_JUDGE=(dts,budget)=>{const g={base:16.7,avg:16,budget:budget||GLIDE_MAX,full:true};for(const dt of dts)glideJudge(g,Math.min(100,dt));return g;};   // tests
function watchFrames(ts){
  requestAnimationFrame(watchFrames);
  if(lastFrame&&glideCount&&document.visibilityState==='visible'&&!mapMoving){
    // 4.1.5: only sustained slowness counts (a single busy moment, e.g. fresh data arriving, doesn't).
    const g=glideJudge({base:frameBase,avg:frameAvg,budget:glideBudget,full:glideCount>=glideBudget},Math.min(100,ts-lastFrame));
    frameBase=g.base;frameAvg=g.avg;glideBudget=g.budget;
  }
  lastFrame=ts;
}
requestAnimationFrame(watchFrames);
window.TPA_HQ_GLIDE_STATUS=()=>({budget:glideBudget,gliding:glideCount,frameMs:Math.round(frameAvg*10)/10,deviceMs:Math.round(frameBase*10)/10});
function keyOf(e){return e.component+':'+e.id;}
// 4.13.3 (owner 3 Nov 19:11: "Your clock is 6.5 seconds behind"): vehicles are moved by the SERVER's time. Each answer
// carries it; the difference from this device's clock is taken from the quickest recent answers (least delay).
// 4.15.3 (owner 4 Nov 10:42: "Some people might have clocks out of kilter by much more", "belt and braces"): the
// server's time is kept on the browser's own stopwatch (performance.now), which no clock sync, sleep or hand-set
// clock can move. Each answer is a reading; the quickest recent ones are trusted, and a reading that disagrees by more
// than 1.5 s (a computer back from sleep) starts afresh. The device's clock is used only until the first answer.
const CLOCK={off:null,samples:[]};
function noteServerTime(h,sent,got){const s=Number(h);if(!(s>1e12)||got<sent)return;
  const sample=[got-sent,s-(sent+got)/2];
  if(CLOCK.off!==null&&sample[0]<1500&&Math.abs(sample[1]-CLOCK.off)>1500)CLOCK.samples=[];
  CLOCK.samples.push(sample);if(CLOCK.samples.length>12)CLOCK.samples.shift();
  const best=[...CLOCK.samples].sort((a,b)=>a[0]-b[0]).slice(0,5).map(x=>x[1]).sort((a,b)=>a-b);CLOCK.off=best[best.length>>1];}
function serverNow(){return (CLOCK.off===null?Date.now():performance.now()+CLOCK.off)+(Number(window.TPA_HQ_TEST_SERVER_SHIFT)||0);}   // (the shift: tests only, to imitate time passing on the server)
window.TPA_HQ_CLOCK=()=>({offsetMs:Math.round(serverNow()-Date.now()),samples:CLOCK.samples.length});
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
  if(m.mode==='heading'&&m.turn&&m.turnS){
    // 4.92.0 (Rob 19 Nov 11:33: curve planes round their turns; air 0.10.0 measured which planes' turns can be trusted):
    // the heading changes at turn degrees a second for turnS seconds after the report, then the plane carries on straight
    const R=6371000,v=m.speedMps,w=m.turn*Math.PI/180,h0=p.headingDeg*Math.PI/180,ta=Math.min(t,m.turnS),h1=h0+w*ta;
    const east=v/w*(Math.cos(h0)-Math.cos(h1))+v*(t-ta)*Math.sin(h1),north=v/w*(Math.sin(h1)-Math.sin(h0))+v*(t-ta)*Math.cos(h1);
    return {lon:p.lon+east/(R*Math.cos(p.lat*Math.PI/180))*180/Math.PI,lat:p.lat+north/R*180/Math.PI,est:t>=1};
  }
  if(m.mode==='heading'){
    const d=travelled(),h=p.headingDeg*Math.PI/180,R=6371000;
    return {lon:p.lon+d*Math.sin(h)/(R*Math.cos(p.lat*Math.PI/180))*180/Math.PI,lat:p.lat+d*Math.cos(h)/R*180/Math.PI,est:t>=1};
  }
  const total=(m.arriveAtMs-t0)/1000,f0=total>0?Math.min(1,t/total):1;
  // 4.19.0 (owner 5 Nov 14:04, 1D77 into Manchester Victoria: "sped up and stopped abruptly"): optional easing - a train
  // picks up speed leaving a station it stopped at and slows into one it stops at; still there at the same time
  const f=m.ease==='both'?0.5-0.5*Math.cos(Math.PI*f0):m.ease==='out'?Math.sin(f0*Math.PI/2):m.ease==='in'?1-Math.cos(f0*Math.PI/2):f0;
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
const WAIT_MAX_MS=20000,CATCHUP_SHARE=0.4,CATCHUP_MAX_MS=30000,RAIL_SLIDE_MAX=150,PLANE_GLIDE_MAX=8000;
// 4.42.0: a real aircraft (the air engine names its kind) or anything in the air layer moving faster than 60 m/s
function isPlane(e,speed){return e.component==='air'&&(!!e.variant||speed>60);}
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
    // 4.48.0 (owner 11 Nov 21:26: planes "speed up then slow down, then appear a bit slow motion then speed up again ... a
    // cycle somewhere around the 6 second mark"): the page asks every 5 s but a plane's report changes only every ~12 s.
    // Each ask with the SAME report restarted the correction from the icon's current spot (gentle start, gentle end), so
    // the speed went up and down in waves. The same report as last time now leaves the correction already under way alone.
    {const sig=e.observedAt+'|'+e.position.lon+'|'+e.position.lat;if(lastReport.get(k)===sig&&blends.has(k))continue;lastReport.set(k,sig);
     if(corrections.size>5000)corrections.clear();corrections.set(e.id,(corrections.get(e.id)||0)+1);}
    // 4.2.1: a replayed ("timed") vehicle is never slid across a big gap: its track is where it really went,
    // so a big difference means it was seen somewhere else (a jump) - fade it in there.
    if(e.motion&&e.motion.mode==='timed'){const [tx,ty]=metresXY(was,next);
      // 4.63.2: N1 test addresses only - the followed bus is eased across (camera with it), never faded
      if(Math.hypot(tx,ty)>30&&!REDUCED&&BUS_N1.on&&BUS_N1.show==='n1'&&follow?.key===k){blends.set(k,{dlon:was.lon-next.lon,dlat:was.lat-next.lat,t0:now,dur:BLEND_MS});continue;}
      if(Math.hypot(tx,ty)>30&&!REDUCED)blends.set(k,{hop:true,jump:true,t0:now});else blends.delete(k);continue;}   // 4.13.2: the same track again: no correction (it slowed every bus down at each new answer)
    const ahead=estimate(e,now+1000),[mx,my]=ahead?metresXY(next,ahead):[0,0],speed=Math.hypot(mx,my);
    const [gx,gy]=metresXY(was,next),gap=Math.hypot(gx,gy);
    // 4.19.0 (owner 14:09, 1P77 into Manchester Victoria: "zoomed across the map ... not on visible train lines"): a train is
    // never slid in a straight line over more than a short way (that cuts across buildings, not along the track): further
    // than that it fades out and back in where it really is. Other layers keep their own rules.
    if(e.component==='rail'&&gap>RAIL_SLIDE_MAX&&!REDUCED){blends.set(k,{hop:true,t0:now});continue;}
    // 4.42.0 (owner 11 Nov 15:00: "instead of refreshing the icons, just have them stay on screen but glide to the new
    // accurate position"): planes have no buildings to cut across, so they always glide to the reported place - never
    // stopping in mid-air, never fading. The correction is spread out so it adds or takes at most ~40% of the plane's
    // own speed (at most 20 s); only a plane found more than PLANE_GLIDE_MAX away (a new reading far off) fades in there.
    if(isPlane(e,speed)&&!REDUCED){
      if(gap>PLANE_GLIDE_MAX){blends.set(k,{hop:true,t0:now});continue;}
      const dur=speed>5?Math.min(20000,Math.max(BLEND_MS,1.5*gap/(CATCHUP_SHARE*speed)*1000)):Math.min(5000,Math.max(BLEND_MS,800+gap*1.5));
      blends.set(k,{dlon:was.lon-next.lon,dlat:was.lat-next.lat,t0:now,dur});continue;}
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
  // 4.40.0 (owner 11 Nov 13:43): roadworks and incidents do not move - "Reported position" read oddly
  if(e.component==='road')return 'Checked at '+seen.slice(0,5)+' · live from TomTom';
  if(e.component==='stations')return 'Railway station'+(e.badge?' · code '+e.badge:'');   // 4.52.0
  if(e.component==='stops')return 'Bus stop'+(e.badge?' '+e.badge:'')+' · live times where buses are seen';   // 4.53.0
  if(e.component==='busstations')return ({coach:'Coach station',interchange:'Interchange'}[e.variant]||'Bus station')+' · live times where buses are seen';   // 4.54.0
  if(e.motion&&e.motion.mode==='timed'&&e.motion.fresh)return 'Approx position from its last report at '+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');   // 4.62.0 test modes; 4.77.0 (owner 18 Nov 03:58): "approx position from last doodah" reads friendlier than "About here now"
  if(e.motion&&e.motion.mode==='timed'&&e.motion.forecast)return 'Approx position from its timetable and its last report at '+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');   // 4.61.0
  if(e.motion&&e.motion.mode==='timed'&&!e.motion.delay)return 'Approx position from its last report at '+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');   // 4.64.0 (N1, no line ahead)
  if(e.motion&&e.motion.mode==='timed')return 'Approx position from its last report at '+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');
  // 4.77.0 (owner 18 Nov 03:59: "same rule for planes and trains"): every carried-on position reads "Approx position from its last report at ..."
  if(state.frames[e.component]?.scheduled&&e.quality==='estimated')return 'Timetable position · not live';   // 4.25.0
  if(est)return 'Approx position from its last report at '+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');
  return (e.quality==='observed'?'Reported position at ':'Last reported at ')+seen+(e.quality==='stale'&&!finishedTrain(e)?' · checking for its latest update':'');
}
// Phone-sized screens show at most this many vehicles per layer (engines already send the
// ones nearest the middle of the view first); hundreds of overlapping icons can't be read.
const PHONE_CAP=100,PHONE_CAP_OUT=60,PHONE_JOIN=1.6;   // 4.75.0: 150 -> 100 (calmer phones)
   // 4.51.0: zoomed out (below 13) on a phone, at most 60 of each layer; grouped sooner
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
  for(const st of stacks.values()){
    const q=map.project([st.lat,st.lon],e.zoom),x=q.x-c.x+size.x/2,y=q.y-c.y+size.y/2;
    st.b.classList.remove('glide');
    const tf=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) ${anchorOf(st.component)}`;st.b.style.transform=tf;st.tf=tf;
  }
}
function zoomAnimEnd(){if(!zoomAnim)return;zoomAnim=false;markerLayer.classList.remove('zoom-anim');paint();}
// 4.95.0 (Rob 19 Nov 13:44, "Trail behind the selected plane", planes page): the engine keeps each plane's last 10 minutes
// of reports (air 0.11.0, details.trail); the line runs through them to where the plane is drawn now.
const TRAIL={id:'',pts:[],line:null,svg:null};
function trailFrom(e,d){
  if(!Array.isArray(d.trail))return;
  const pts=d.trail.filter(p=>Array.isArray(p)&&validNum(p[0])&&validNum(p[1])&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90).slice(-200).map(p=>[p[1],p[0]]);
  TRAIL.id=e.id;TRAIL.pts=pts;}
function syncTrail(){
  if(!PLANES||!map||!window.L||typeof L.polyline!=='function')return;
  const sel=state.selected,pin=sel&&sel.component==='air'?pins.get('air:'+sel.id):null;
  const want=!!sel&&sel.component==='air'&&TRAIL.id===sel.id&&TRAIL.pts.length>=1&&!!pin&&pin.lat!=null&&state.visible.air;
  if(!want){if(TRAIL.line){TRAIL.line.remove();TRAIL.line=null;}if(!sel||sel.id!==TRAIL.id){TRAIL.id='';TRAIL.pts=[];}return;}
  const ll=[...TRAIL.pts,[pin.lat,pin.lon]];
  // its own SVG layer (the map draws on a canvas): a crisp line that sits under the planes
  if(!TRAIL.line){
    // its own pane above the map (and the traffic colours' 450), below the planes (they sit on the page's own layer)
    if(!map.getPane('tpa-trail')&&typeof map.createPane==='function'){const pn=map.createPane('tpa-trail');pn.style.zIndex='460';pn.style.pointerEvents='none';}
    TRAIL.svg=TRAIL.svg||(typeof L.svg==='function'?L.svg({pane:'tpa-trail'}):undefined);
    TRAIL.line=L.polyline(ll,{color:'#ffb21e',weight:3,opacity:.85,interactive:false,className:'plane-trail',renderer:TRAIL.svg}).addTo(map);}
  else TRAIL.line.setLatLngs(ll);}
function markers(){
  if(zoomAnim)return;      // icons are travelling with the zoom animation; placed again when it ends
  // 4.1.8 (owner 31 Oct 13:50: after moving the map, moving icons jumped back to their old spot
  // on the screen and glided across): if the view changed since the icons were last placed,
  // place every icon instantly this time; gliding is only ever for the vehicle's own movement.
  let viewMoved=false,ox=0,oy=0;
  // 4.10.0: while following a vehicle the map slides under it every frame. Icons then stay put on the icon
  // layer (which slides with the map), so they keep gliding instead of being re-placed each second.
  if(map&&follow&&paintRef&&paintRef.z===map.getZoom()){const p=map.latLngToContainerPoint(paintRef.ll);ox=p.x-paintRef.x;oy=p.y-paintRef.y;
    markerLayer.style.transform=Math.abs(ox)+Math.abs(oy)>0.05?`translate(${ox.toFixed(1)}px,${oy.toFixed(1)}px)`:'';}
  else if(map){const ll=map.getCenter(),c=map.latLngToContainerPoint(ll);paintRef={ll,z:map.getZoom(),x:c.x,y:c.y};markerLayer.style.transform='';
    const r=map.latLngToContainerPoint([54,-2]),sig=map.getZoom()+'|'+r.x.toFixed(1)+'|'+r.y.toFixed(1);viewMoved=sig!==viewSig;viewSig=sig;}
  lastFull=performance.now();lastDrawAt=lastFull;
  const d=dims(),now=serverNow(),keep=new Set(),M=40,cap=d.w<760?(KIDS?PHONE_CAP:phoneCap(map?map.getZoom():state.z)):Infinity,perLayer=Object.create(null);moving=0;
  // Which vehicles may glide this second: the nearest to the middle of the screen, up to the budget.
  {const cx=d.w/2,cy=d.h/2,list=[];
   for(const [k,p] of pins)if(p.tf&&p.e&&state.live[p.e.component]&&(p.e.motion||blends.has(k))){const m=p.tf.match(/translate\(([-0-9.]+)px,([-0-9.]+)px\)/);if(m)list.push([k===follow?.key?-1:(m[1]-cx)**2+(m[2]-cy)**2,k]);}
   // 4.1.5: a vehicle that is already gliding keeps gliding (no "taking turns"); free places go
   // to the vehicles nearest the middle of the screen.
   list.sort((a,b)=>a[0]-b[0]);
   const next=new Set();for(const [,k] of list)if(glideOK.has(k)&&next.size<glideBudget)next.add(k);
   for(const [,k] of list){if(next.size>=glideBudget)break;next.add(k);}
   glideOK=next;glideCount=glideOK.size;}
  const items=[],present=new Set(),goneNow=new Set();
  for(const e of listEntities()){
    if(!e.position)continue;
    if((bubbleLayer(e.component)||bubbleQuiet(e.component))&&!(follow&&follow.key===keyOf(e))
       &&!(state.selected&&state.selected.id===e.id&&state.selected.component===e.component))continue;   // 4.67.0: in a town bubble
    const k=keyOf(e),pos=whereNow(e,now);if(!pos)continue;present.add(k);
    const lost=lostState(e,k,(follow&&follow.key===k)||(state.selected&&state.selected.id===e.id&&state.selected.component===e.component),pos,now);   // 4.72.0
    if(lost===2){goneNow.add(k);continue;}
    if(pos.est||blends.has(k)||(state.live[e.component]&&e.motion))moving++;
    shown.set(k,{lon:pos.lon,lat:pos.lat});
    const [x,y]=pixel(pos.lon,pos.lat);
    const followed=!!follow&&follow.key===k;if(followed)follow.e=e;
    // 4.18.2 (owner 13:34: back from another window, the followed train "had carried on its route but left me where i was"):
    // the followed vehicle is never dropped for being off screen - the map goes to it
    if((x<-M||y<-M||x>d.w+M||y>d.h+M)&&!followed)continue;
    items.push({e,k,pos,x:x-ox,y:y-oy,searching:lost===1});
  }
  drawBubbles(ox,oy);   // 4.67.0
  // 4.8.0 (owner 1 Nov 13:40, York at zoom 13: a pile of buses on top of each other): icons that would sit
  // on top of each other are shown as one "N buses" badge, which splits as you zoom in.
  const grouped=groupStacks(items);fanOut(items,grouped,now);
  GONE=goneNow;   // 4.72.0
  for(const {e,k,pos,x,y,searching} of items){
    if(grouped.has(k))continue;
    if((perLayer[e.component]=(perLayer[e.component]||0)+1)>cap)continue;
    keep.add(k);
    const active=!!state.selected&&state.selected.id===e.id&&state.selected.component===e.component;
    const prefix=state.live[e.component]?'':'MOCK ';
    const glideWanted=!REDUCED&&!mapMoving&&!!pin0(k)&&(pos.est||blends.has(k)||(state.live[e.component]&&!!e.motion));
    const canGlide=glideWanted&&glideOK.has(k)&&!viewMoved,glide=canGlide&&!pos.hop;
    // 4.18.0 (owner 5 Nov 12:09: 1D86 going left and 1M08 going right "both give the impression of travelling right"):
    // a side-on train faces the way it is going (west-ish: mirrored), and keeps facing that way when it stops
    if(e.component==='rail'&&!KIDS){const a=estimate(e,now+2000);if(a){const mx=metresXY(pos,a)[0];if(Math.abs(mx)>2){if(FACE.size>5000)FACE.clear();FACE.set(k,mx<0);}}}
    const faceLeft=e.component==='rail'&&FACE.get(k)===true;
    const cls=`vehicle-pin review-pin ${e.component} ${e.quality}${e.component==='air'&&isMilitary(e)?' k-military':''}${e.component==='air'&&isService(e)?' k-'+e.variant.split('-')[0]:''}${isGround(e)?' k-ground':''}${active?' selected-pin':''}${pos.est?' est-pos':''}${searching?' searching':''}${glide?' glide':''}${faceLeft?' face-left':''}`;
    // 4.1.11 (owner 31 Oct 22:08: icons "look weirdish, like they are re rendered. They need to stay
    // the same"): an icon is built once and kept; only an aircraft's existing icon turns to its heading.
    const art=e.component+'|'+(e.badge||'')+'|'+(e.variant||'')+'|'+e.id+(e.component==='air'?'|'+(planeShape(e)||''):'');   // 4.84.0
    let pin=pins.get(k);
    if(!pin){const b=document.createElement('button');b.type='button';pin={b,art:''};pins.set(k,pin);markerLayer.append(b);}
    const b=pin.b;pin.e=e;pin.lat=pos.lat;pin.lon=pos.lon;pin.active=active;
    if(pin.art!==art){b.replaceChildren(buildArtwork(e));pin.art=art;pin.ft=undefined;}
    if(e.component==='air'){const svg=b.querySelector('svg,.plane-pic'),hd=headingNow(e,now),rot=KIDS?kidFlip(e):svg?.classList?.contains('plane-pic')?planeTurn(e,hd):hd!=null?'rotate('+hd+'deg)':'';   // 4.92.0: hd
      if(svg&&svg.style.transform!==rot)svg.style.transform=rot;
      // 4.84.0: height shadow, only touched when the height changes
      const ft=!KIDS&&typeof e.altFt==='number'&&e.altFt>=0?e.altFt:null;
      if(pin.ft!==ft){pin.ft=ft;const a=b.querySelector('.review-art');if(a)planeShadow(a,ft);}}
    if(b.className!==cls)b.className=cls;
    if(pos.hop){b.classList.remove('hop');void b.offsetWidth;b.classList.add('hop');}
    // 4.83.0: plain words on hover ("stale" was an internal word; a finished train has arrived)
    const title=prefix+e.label+' · '+(finishedTrain(e)?'arrived':pos.est?'estimated position':e.quality==='stale'?'waiting for its next report':e.quality==='observed'?'reported position':'estimated position');
    if(b.title!==title){b.title=title;b.setAttribute('aria-label',prefix+e.label+' '+(pos.est?'estimated position':e.quality)+(active?' selected':''));}
    if(b.getAttribute('aria-pressed')!==String(active))b.setAttribute('aria-pressed',String(active));
    // Moved with a transform (no page re-layout), which phones handle on the graphics chip.
    const tf=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) ${anchorOf(e.component)}${active?' scale(1.17)':''}`;
    if(pos.hop&&canGlide&&!pos.jump){
      // reappear one update back along its way and glide on at once, in step with everything else
      // (no standing still until the next update)
      const back=estimate(e,now-glideMs);let from=null;
      if(back){const [bx0,by0]=pixel(back.lon,back.lat),bx=bx0-ox,by=by0-oy;from=[bx,by];
        b.style.transform=`translate(${bx.toFixed(1)}px,${by.toFixed(1)}px) ${anchorOf(e.component)}${active?' scale(1.17)':''}`;
        void b.offsetWidth;b.classList.add('glide');}
      b.style.transform=tf;pin.tf=tf;
      if(follow?.key===k)followTo(x,y,!!from,from);
    }else if(pin.tf!==tf){b.style.transform=tf;pin.tf=tf;if(follow?.key===k)followTo(x,y,glide,null);}
    b.onclick=KIDS?()=>kidsTap(e,b):()=>{if(active){state.selected=null;selected.classList.add('hidden');paint();}else selectEntity(e);};
  }
  // 4.72.0: a vehicle that has searched long enough fades away gently (no grey ghost, no sudden vanish)
  for(const [k,pin] of pins)if(!keep.has(k)){noteVanish(k,pin,present,d);if(GONE.has(k)&&!REDUCED)fadeAway(pin.b);else pin.b.remove();pins.delete(k);}
  drawStacks(viewMoved);
  if(shown.size>keep.size*4+4000)for(const k of shown.keys())if(!keep.has(k))shown.delete(k);
  updateSelectedInfo(now);
  syncTrail();   // 4.95.0
}
// 4.8.0: grouping stacked icons. Generic: any layer whose engine sends groupStacks: true, by how close the
// icons are on the screen.
// - only below GROUP_BELOW_ZOOM (from there on every vehicle is drawn on its own);
// - at least GROUP_MIN icons closer than about one icon's width; the selected vehicle is never grouped;
// - a vehicle stays in its group until it is clearly away from it (join/leave distances differ), and a group
//   keeps its id while its first member stays in it, so badges don't flicker or jump between frames.
const GROUP_BELOW_ZOOM=16,GROUP_MIN=3,GROUP_JOIN=32,GROUP_LEAVE=44;
const stacks=new Map();          // group id -> {b, component, n, lat, lon, x, y, members, tf}
let stackOf=new Map(),stackNow=[];  // vehicle key -> group id (last paint); groups to draw this paint
// 4.15.4 (owner 4 Nov 11:30: trains piled at a station - "you can only click on ... the top one"): vehicles that sit
// on (almost) the same spot and are not in a badge are spread into a small ring round that spot, so each one can be
// seen and clicked. Drawing only - their real positions are unchanged; the ring keeps its order (by id), so it is calm.
const FAN_NEAR=6,FAN={off:new Map(),t:0},FACE=new Map();   // FACE: train key -> true when last seen going west-ish
function fanOut(items,grouped,now){
  // trains only (owner 4 Nov 11:30, trains piled at a station): buses are signed off as they are, and other layers
  // use a vehicle on the same spot as a marker. Only trains standing still (at a platform): two passing do not jump apart.
  const still=it=>{const a=estimate(it.e,now+1000);if(!a)return true;const [dx,dy]=metresXY(it.pos,a);return Math.hypot(dx,dy)<0.5;};
  const free=items.filter(it=>it.e.component==='rail'&&!grouped.has(it.k)&&still(it)),used=new Set(),want=new Map();
  for(const a of free){
    if(used.has(a.k))continue;
    const pile=free.filter(b=>!used.has(b.k)&&Math.abs(b.x-a.x)<=FAN_NEAR&&Math.abs(b.y-a.y)<=FAN_NEAR);
    if(pile.length<2)continue;
    pile.sort((p,q)=>p.k<q.k?-1:1);
    const cx=pile.reduce((t,p)=>t+p.x,0)/pile.length,cy=pile.reduce((t,p)=>t+p.y,0)/pile.length;
    const r=Math.max(26,9*pile.length)*Math.max(0.8,pinScale(state.z));
    pile.forEach((p,i)=>{const ang=-Math.PI/2+2*Math.PI*i/pile.length;want.set(p.k,[cx+r*Math.cos(ang)-p.x,cy+r*Math.sin(ang)-p.y]);used.add(p.k);});
  }
  // 4.17.2 (owner 5 Nov 11:54, Piccadilly: 2U42 and 1M26 on neighbouring platforms partly on top of each other): trains
  // whose icons (or headcode boards) overlap are nudged just far enough apart, the shorter way, staying by their platforms
  // 4.17.3 (owner 12:02, Piccadilly zoom 18: 1M26, ringed apart from a train on the same platform, then sat over 2H25 on
  // the next platform): the ringed trains are nudged along with the rest, starting from their places in the ring.
  // 4.18.1 (owner 12:14, Piccadilly zoom 18: "now looks a bit too spread out"): each train claims only its icon and headcode
  // board, and neighbours may touch - no extra gap - so a busy station stays close to its real shape.
  const W=40*pinScale(state.z)*0.95,H=54*pinScale(state.z)*0.95,rest=free,at=new Map(rest.map(it=>{const w=want.get(it.k)||[0,0];return [it.k,[it.x+w[0],it.y+w[1]]];}));
  // (only zoomed in to street level and with up to 80 standing trains in view: zoomed out, grouping does this job)
  for(let round=0;round<12&&state.z>=14&&rest.length<=80;round++){let moved=false;
    for(let i=0;i<rest.length;i++)for(let j=i+1;j<rest.length;j++){
      const p=at.get(rest[i].k),q=at.get(rest[j].k),dx=q[0]-p[0],dy=q[1]-p[1],ox=W-Math.abs(dx),oy=H-Math.abs(dy);
      if(ox<=0||oy<=0)continue;moved=true;
      if(ox*1.4<oy){const m=(ox/2)*(dx<0?-1:1);p[0]-=m;q[0]+=m;}else{const m=(oy/2)*(dy<0?-1:1)||oy/2;p[1]-=m;q[1]+=m;}}
    if(!moved)break;}
  for(const it of rest){const p=at.get(it.k);if(Math.abs(p[0]-it.x)>0.3||Math.abs(p[1]-it.y)>0.3)want.set(it.k,[p[0]-it.x,p[1]-it.y]);}
  // the ring opens and closes smoothly (about a quarter of a second), so a train arriving at a busy platform slides aside, never jumps
  const t=performance.now(),f=FAN.t?1-Math.exp(-Math.min(1000,t-FAN.t)/120):1;FAN.t=t;
  for(const it of items){
    const w=want.get(it.k)||[0,0],c=FAN.off.get(it.k)||[0,0];
    const o=[c[0]+(w[0]-c[0])*f,c[1]+(w[1]-c[1])*f];
    if(!want.has(it.k)&&Math.hypot(o[0],o[1])<0.3){FAN.off.delete(it.k);continue;}
    FAN.off.set(it.k,o);it.x+=o[0];it.y+=o[1];
  }
}
function groupStacks(items){
  const out=new Set();stackNow=[];
  const z=map?map.getZoom():state.z;
  if(!map||z>=GROUP_BELOW_ZOOM){stackOf=new Map();return out;}
  const k=isPhone()?PHONE_JOIN:1,s=pinScale(z),join=GROUP_JOIN*s*k,leave=GROUP_LEAVE*s*k,sel=state.selected;   // 4.51.0: sooner on phones
  const byLayer=new Map();
  for(const it of items){
    if(state.frames[it.e.component]?.groupStacks!==true)continue;   // each engine switches it on (bus first)
    if(sel&&sel.id===it.e.id&&sel.component===it.e.component)continue;
    let l=byLayer.get(it.e.component);if(!l)byLayer.set(it.e.component,l=[]);l.push(it);
  }
  const next=new Map();
  for(const [component,list] of byLayer){
    // last paint's group leaders go first, so groups keep their place; then left to right (stable order)
    list.sort((a,b)=>((stackOf.get(b.k)===b.k)-(stackOf.get(a.k)===a.k))||(a.x-b.x)||(a.y-b.y)||(a.k<b.k?-1:1));
    const seeds=[],cell=leave,grid=new Map(),key=(cx,cy)=>cx+','+cy;
    for(const it of list){
      const cx=Math.floor(it.x/cell),cy=Math.floor(it.y/cell),prev=stackOf.get(it.k);let best=null,bd=Infinity;
      for(let i=-1;i<=1;i++)for(let j=-1;j<=1;j++)for(const sd of grid.get(key(cx+i,cy+j))||[]){
        const dd=Math.hypot(sd.x-it.x,sd.y-it.y),r=prev===sd.k?leave:join;
        if(dd<=r&&dd<bd){bd=dd;best=sd;}
      }
      if(best){best.members.push(it);continue;}
      const sd={k:it.k,x:it.x,y:it.y,members:[it]};seeds.push(sd);
      const g=key(cx,cy);let c=grid.get(g);if(!c)grid.set(g,c=[]);c.push(sd);
    }
    for(const sd of seeds){
      if(sd.members.length<GROUP_MIN)continue;
      let x=0,y=0,lat=0,lon=0;
      for(const m of sd.members){x+=m.x;y+=m.y;lat+=m.pos.lat;lon+=m.pos.lon;out.add(m.k);next.set(m.k,sd.k);}
      const n=sd.members.length;
      stackNow.push({id:component+'|'+sd.k,component,n,x:x/n,y:y/n,lat:lat/n,lon:lon/n,
        members:sd.members.map(m=>[m.pos.lat,m.pos.lon])});
    }
  }
  stackOf=next;return out;
}
function drawStacks(viewMoved){
  const keepIds=new Set();
  for(const g of stackNow){
    keepIds.add(g.id);
    let st=stacks.get(g.id);
    if(!st){const b=document.createElement('button');b.type='button';st={b,tf:'',label:''};stacks.set(g.id,st);markerLayer.append(b);}
    const b=st.b,isNew=!st.tf;Object.assign(st,g);
    const label=g.n+' '+layerName(g.component);
    if(st.label!==label){
      const num=document.createElement('b');num.textContent=String(g.n);
      const word=document.createElement('span');word.textContent=layerName(g.component);
      b.replaceChildren(num,' ',word);st.label=label;
      b.title=label+' here · click to zoom in';b.setAttribute('aria-label',label+' here, zoom in to see each one');
    }
    const cls=`vehicle-pin stack-pin ${g.component}${!isNew&&!viewMoved&&!REDUCED&&!mapMoving?' glide':''}`;
    if(b.className!==cls)b.className=cls;
    const tf=`translate(${g.x.toFixed(1)}px,${g.y.toFixed(1)}px) ${anchorOf(g.component)}`;
    if(st.tf!==tf){b.style.transform=tf;st.tf=tf;}
    b.onclick=()=>zoomToStack(st);
  }
  for(const [id,st] of stacks)if(!keepIds.has(id)){st.b.remove();stacks.delete(id);}
}
function zoomToStack(st){
  if(!map)return;
  const b=L.latLngBounds(st.members);
  map.fitBounds(b.pad(0.2),{maxZoom:GROUP_BELOW_ZOOM,padding:[60,60]});
}
// 4.10.0 (owner 1 Nov 17:49: "right clicking would bring up 'follow me!' and lock that icon to the centre of
// the map with the map moving around it ... clicking the icon again to deselect also stops the follow").
// Generic: any vehicle on any layer. HQ already knows where it is drawing the icon at every moment (it glides
// in a straight line from where it was to its new place, over glideMs), so each frame the map is slid by the
// tiny gap between that point and the middle of the map. Grabbing the map, choosing another vehicle, closing
// its box (or Escape) or the vehicle leaving the map stops following. Zooming keeps it in the middle.
// 4.51.0: a vehicle picked on a phone is slid into view above the panel (never hidden behind it)
function keepAboveSheet(e){
  if(!map||!isPhone()||follow)return;const k=keyOf(e),pin=pins.get(k);if(!pin)return;
  const r=pin.b.getBoundingClientRect(),m=root.getBoundingClientRect(),y=r.top+r.height/2-m.top,top=m.height-sheetHeight();
  if(y>top-24||y<70)map.panBy([0,y-focusY(m.height)],{animate:true,duration:0.35});
}
// swipe up on the panel's top for more, down for less (phones)
(()=>{let y0=null;
  selected.addEventListener('touchstart',ev=>{y0=isPhone()&&ev.touches.length===1&&ev.touches[0].clientY-selected.getBoundingClientRect().top<70?ev.touches[0].clientY:null;},{passive:true});
  selected.addEventListener('touchend',ev=>{if(y0==null)return;const dy=ev.changedTouches[0].clientY-y0;y0=null;
    const g=selected.querySelector('.sheet-grab');if(!g)return;
    if(dy<-35&&!selected.classList.contains('open'))g.click();else if(dy>35&&selected.classList.contains('open'))g.click();},{passive:true});
})();
// the panel being up is known to the page (phones tuck the credits away under it)
new MutationObserver(()=>document.body.classList.toggle('sheet-up',!selected.classList.contains('hidden'))).observe(selected,{attributes:true,attributeFilter:['class']});
// 4.51.0: on phones the credits sit behind a small (c) button
// 4.65.1: on a PC a click on the one-line credits shows them in full (and again hides them)
(()=>{const c=$('map-credit');if(c)c.addEventListener('click',()=>{if(window.innerWidth<=760)return;
  document.body.classList.toggle('credits-open');});})();
// 4.69.0 (Rob 17 Nov 16:25: "the layers still take up too much of the menu"; he chose: the whole section folds away):
// the Tracking layers switches fold up under their heading - folded to begin with; the visitor's choice is remembered
(()=>{const b=$('layers-fold'),p=b?.closest('.layers-panel');if(!b||!p)return;
  const set=open=>{p.classList.toggle('folded',!open);b.setAttribute('aria-expanded',String(open));};
  let open=false;try{open=localStorage.getItem('tpa-layers-open')==='1';}catch(_){}
  set(open);
  p.querySelector('.fold-title').addEventListener('click',()=>{const now=p.classList.contains('folded');set(now);
    try{localStorage.setItem('tpa-layers-open',now?'1':'0');}catch(_){}});})();
(()=>{const t=$('credit-toggle');if(!t)return;t.addEventListener('click',()=>{const on=!document.body.classList.contains('credits-open');
  document.body.classList.toggle('credits-open',on);t.setAttribute('aria-expanded',String(on));});
  // 4.66.0 (Rob 17 Nov: the credits bar "takes up way too much of the screen"): on every screen the credits are a small
  // button (street map, plus traffic when shown); the full line opens above it and closes again on a tap anywhere else
  document.addEventListener('pointerdown',ev=>{if(!document.body.classList.contains('credits-open'))return;
    if(ev.target.closest&&(ev.target.closest('#credit-toggle')||ev.target.closest('#map-credit')))return;
    document.body.classList.remove('credits-open');t.setAttribute('aria-expanded','false');},true);})();
let follow=null,followPanning=false,followMenu=null;
function isFollowing(e){return !!follow&&follow.comp===e.component&&follow.id===e.id;}
function followAt(now){if(!follow?.to)return null;const f=follow.dur>0?Math.min(1,(now-follow.t0)/follow.dur):1;
  return [follow.from[0]+(follow.to[0]-follow.from[0])*f,follow.from[1]+(follow.to[1]-follow.from[1])*f];}
function followTo(x,y,glide,from){if(!follow)return;const now=performance.now(),cur=from||followAt(now)||[x,y];
  follow.from=glide?cur:[x,y];follow.to=[x,y];follow.t0=now;follow.dur=glide?glideMs:0;}
function startFollow(e){
  if(!map||!e.position)return;closeFollowMenu();
  if(!state.selected||state.selected.id!==e.id||state.selected.component!==e.component)selectEntity(e);
  const k=keyOf(e),p=pins.get(k),m=p?.tf?.match(/translate\(([-0-9.]+)px,([-0-9.]+)px\)/);
  follow={key:k,comp:e.component,id:e.id,from:null,to:null,t0:0,dur:0,started:performance.now(),lastAsk:0};
  if(m){follow.from=follow.to=[+m[1],+m[2]];}
  map.options.scrollWheelZoom='center';map.options.touchZoom='center';map.options.doubleClickZoom='center';
  updateFollowUI();paint();
  if(BUS_N1.on&&e.component==='bus')getComponent('bus');   // 4.63.2: N1 knows at once which bus is followed (never faded)
}
// 4.18.2: coming back to the tab while following, the map goes straight to where the vehicle is now (it kept going)
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible'||!follow||!follow.e||!map)return;
  const p=estimate(currentEntity({component:follow.comp,id:follow.id})||follow.e,serverNow());if(!p)return;   // 4.64.2: its newest data
  map.setView([p.lat,p.lon],map.getZoom(),{animate:false});follow.from=follow.to=null;follow.started=performance.now();
  refetchLiveView();paint();
});
// 4.64.2 (Rob 15 Nov 22:56: followed Bus 43, "went to make a brew. When i came back, there it wasn't"): Follow Me moved the
// camera only when the page drew a frame. With the screen asleep or the window covered no frames are drawn, but the bus data
// kept coming: the bus drove out of the view, dropped out of the data and Follow Me ended. While nothing is being drawn the
// map is now moved to the followed vehicle every 2 s by a timer, so it stays in view and in the data; drawn again, it is
// already there and followFrame carries on smoothly.
function followKeepUp(){
  if(!follow||!map||zoomAnim||performance.now()-lastRaf<1500)return;   // being drawn: followFrame does it
  const e=currentEntity({component:follow.comp,id:follow.id})||follow.e;if(!e)return;
  const p=estimate(e,serverNow());if(!p)return;
  followPanning=true;try{map.setView([p.lat,p.lon],map.getZoom(),{animate:false});}finally{followPanning=false;}
  follow.from=follow.to=null;follow.started=performance.now();follow.keptUp=(follow.keptUp||0)+1;
  if(performance.now()-follow.lastAsk>1000){follow.lastAsk=performance.now();refetchLiveView();}
}
setInterval(followKeepUp,2000);
window.TPA_HQ_FOLLOW=()=>({on:!!follow,id:follow?.id||null,keptUp:follow?.keptUp||0});
function stopFollow(){
  if(!follow)return;follow=null;settleMap();
  if(map){map.options.scrollWheelZoom=true;map.options.touchZoom=true;map.options.doubleClickZoom=true;}
  updateFollowUI();paint();refetchLiveView();
}
function updateFollowUI(){
  const fb=selected.querySelector('.follow-button');
  if(fb){const on=!!follow&&!!state.selected&&isFollowing(state.selected);fb.textContent=KIDS?(on?'⏹ Stop riding':'🚀 Ride along!'):on?'■ Stop following':'Follow me!';
    fb.setAttribute('aria-pressed',String(on));fb.hidden=!map;}
  root.classList.toggle('following',!!follow);
}
function followFrame(now){
  if(!follow||!map||zoomAnim)return;
  const sel=state.selected;
  if(!sel||sel.id!==follow.id||sel.component!==follow.comp||selected.classList.contains('hidden')||!pins.has(follow.key)){stopFollow();return;}
  const p=followAt(now);if(!p)return;
  let ox=0,oy=0;if(paintRef&&paintRef.z===map.getZoom()){const q=map.latLngToContainerPoint(paintRef.ll);ox=q.x-paintRef.x;oy=q.y-paintRef.y;}
  const size=map.getSize(),dx=p[0]+ox-size.x/2,dy=p[1]+oy-focusY(size.y);   // 4.51.0: above the phone panel
  if(Math.abs(dx)+Math.abs(dy)<0.002)return;
  const f=now-follow.started<900?0.12:1;          // glide in when following starts, then stay locked on
  followPanning=true;try{slideMap(dx*f,dy*f);}finally{followPanning=false;}
  if(now-follow.lastAsk>1000){follow.lastAsk=now;refetchLiveView();}   // vehicles for the new area, about once a second
}
// 4.10.1 (owner 1 Nov 18:20: "Could the scrolling be smoother?"): Leaflet's panBy rounds every move to whole
// pixels, so a slow vehicle moved the map in 1-pixel jerks. Slide by fractions of a pixel instead (the same
// steps panBy takes, without the rounding); the browser draws fractional positions smoothly.
function slideMap(dx,dy){
  if(typeof map._rawPanBy==='function'&&typeof map._getMapPanePos==='function'&&window.L?.point){
    map.fire('movestart');map._rawPanBy(L.point(dx,dy));map.fire('move').fire('moveend');
  }else map.panBy([dx,dy],{animate:false});
}
// When following stops, put the map back on a whole pixel so map pictures stay sharp.
function settleMap(){
  if(!map||typeof map._getMapPanePos!=='function')return;
  const p=map._getMapPanePos(),fx=p.x-Math.round(p.x),fy=p.y-Math.round(p.y);
  if(Math.abs(fx)>0.001||Math.abs(fy)>0.001){followPanning=true;try{slideMap(fx,fy);}finally{followPanning=false;}}
}
function closeFollowMenu(){if(followMenu){followMenu.remove();followMenu=null;}}
function openFollowMenu(e,x,y){
  closeFollowMenu();
  const menu=document.createElement('div');menu.className='follow-menu';menu.setAttribute('role','menu');
  const item=document.createElement('button');item.type='button';item.setAttribute('role','menuitem');
  item.textContent=isFollowing(e)?'■ Stop following':'Follow me!';
  item.onclick=ev=>{ev.stopPropagation();if(isFollowing(e))stopFollow();else startFollow(e);closeFollowMenu();};
  menu.append(item);document.body.append(menu);followMenu=menu;
  const r=menu.getBoundingClientRect();
  menu.style.left=Math.max(4,Math.min(x,innerWidth-r.width-4))+'px';menu.style.top=Math.max(4,Math.min(y,innerHeight-r.height-4))+'px';
  item.focus();
}
document.addEventListener('pointerdown',ev=>{if(followMenu&&!followMenu.contains(ev.target))closeFollowMenu();},true);
document.addEventListener('keydown',ev=>{if(ev.key==='Escape')closeFollowMenu();});
markerLayer.addEventListener('contextmenu',ev=>{
  const b=ev.target.closest('.review-pin');if(!b||!map)return;
  let hit=null;for(const p of pins.values())if(p.b===b){hit=p.e;break;}
  if(!hit)return;
  ev.preventDefault();
  if(!state.selected||state.selected.id!==hit.id||state.selected.component!==hit.component)selectEntity(hit);
  openFollowMenu(hit,ev.clientX,ev.clientY);
});
// 4.12.0 (owner 1 Nov 20:02, bus sign-off): count vehicles that disappear while on screen - their engine stopped
// sending them while the map was showing them (not ones that moved off screen, went into a group or whose layer
// was switched off) - so "buses vanish" becomes a number, shown in #speed and in every report.
const VANISH={total:0,byLayer:Object.create(null),byKey:new Map(),recent:[]};window.TPA_HQ_VANISH=()=>({total:VANISH.total});   // 4.72.0: tests read it
function fadeAway(b){b.classList.add('leaving');b.style.pointerEvents='none';b.removeAttribute('aria-pressed');setTimeout(()=>b.remove(),1700);}   // 4.72.0
function noteVanish(k,pin,present,d){
  if(present.has(k)||!pin.e||zoomAnim||!state.visible[pin.e.component]||!state.live[pin.e.component]||pin.lat==null)return;
  const [x,y]=pixel(pin.lon,pin.lat);if(x<0||y<0||x>d.w||y>d.h)return;
  const c=pin.e.component;VANISH.total++;VANISH.byLayer[c]=(VANISH.byLayer[c]||0)+1;VANISH.byKey.set(k,(VANISH.byKey.get(k)||0)+1);
  VANISH.recent.push({id:pin.e.id,label:pin.e.label,at:Date.now()});if(VANISH.recent.length>20)VANISH.recent.shift();
}
// 4.12.0: "Report this" (test phase; site-config "report": true) - one click copies everything needed to trace
// what that vehicle did, to paste to Claude. The server command is generic: the server kit knows the layers.
let REPORT_ON=false;
function reportText(e){
  const now=new Date(),pos=whereNow(e,serverNow())||e.position,k=keyOf(e);
  const t=x=>x.toLocaleTimeString('en-GB',{hour12:false});
  const v=VANISH.byKey.get(k)||0;
  return ['TPA report · '+($('zoom-level')?.closest('.map-bottom')?.querySelector('.preview-stamp')?.textContent||'HQ').replace(' · TEST VERSION','')+' · '+now.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'})+' '+t(now),
    e.label+'  ('+e.id+')',
    'Shown at '+pos.lat.toFixed(5)+', '+pos.lon.toFixed(5)+(pos.est?' · estimated position':'')+' · zoom '+state.z.toFixed(1)+' · last reported '+t(new Date(e.observedAt)),
    'Vanished on screen this visit: '+VANISH.total+' vehicles'+(v?' (this one '+v+' times)':''),
    'What I saw: ',
    'Server check (paste into Cloud Shell): ssh -i ~/.ssh/tpa_v4_server ubuntu@150.230.114.191 "sudo tpa-trace \''+e.id.replace(/'/g,'')+'\'"'].join('\n');
}
async function copyReport(e){
  const text=reportText(e);
  try{await navigator.clipboard.writeText(text);toast('Report copied · paste it to Claude, with what you saw');return text;}
  catch(_){const {back,card}=makeDialog('tpa-report','Report');const ta=el('textarea','report-text');ta.value=text;ta.rows=8;ta.readOnly=true;
    const close=el('button','tpa-primary','Close');close.type='button';close.onclick=()=>{closeDialog(back);back.remove();};
    card.append(el('p','','Copy this and paste it to Claude:'),ta,close);openDialog(back);ta.select();return text;}
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
  if(follow)followFrame(performance.now());
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
// 4.72.0 (Rob 17 Nov: "Calm note if data late"): plain, calm words; the full owner page still shows the engine's own notice
function lateWords(id,notice){const late=LATE[id]?Date.now()-LATE[id]:0;
  const calm=late>LATE_GRACE_MS?'Looking for the latest '+layerName(id)+' updates…':'Updates running a little late · carrying on meanwhile';
  return FULL_VIEW&&notice?calm+' — '+notice:calm;}
function layerName(id){return ($(id+'-toggle').closest('.layer-toggle')?.querySelector('strong')?.textContent||id).toLowerCase();}
const kidsSeen=Object.create(null);
function counts(){const d=dims();const anyLive=IDS.some(id=>state.live[id]&&!state.parked[id]);
for(const id of IDS){let entries=state.frames[id]?.entities;
let count=(entries||[]).filter(e=>{if(!e.position||isGround(e)||GONE.has(keyOf(e)))return false;   // 4.45.0: airport vehicles are not planes; 4.72.0: faded away
let [x,y]=pixel(e.position.lon,e.position.lat);return x>=0&&x<=d.w&&y>=0&&y<=d.h;}).length;
const bubbled=bubbleLayer(id);if(bubbled)count=countInView(id);   // 4.67.0: every one in view, as the town bubbles count
kidsSeen[id]=state.live[id]&&state.visible[id]&&!state.parked[id]&&state.status[id]==='ready'&&!(state.frames[id]?.notice||'').startsWith('Zoom in')?count:0;
const status=$(id+'-source-status'),chip=$(id+'-chip').querySelector('strong'),n=$(id+'-count');
if(state.parked[id]){status.textContent='Unavailable · coming soon';chip.textContent='SOON';if(n)n.textContent='—';continue;}
if(state.live[id]){
  // 4.0.10: a real engine is connected. Plain wording; never the word MOCK.
  const overlay=state.frames[id]?.overlay,detail=FULL_VIEW&&state.frames[id]?.noticeDetail?' — '+state.frames[id].noticeDetail:'',
    notice=bubbled?'':bubbleQuiet(id)?'Zoom in to see '+layerName(id):(state.frames[id]?(state.frames[id].notice||''):(state.notice[id]||''))+detail,scheduled=state.frames[id]?.scheduled===true;   // 4.70.0: stations wait for zoom 13
  const tooFar=overlay&&state.z<overlay.minZoom;
  status.textContent=!state.visible[id]?'Hidden':
    state.status[id]==='ready'?(tooFar?'Zoom in to see '+layerName(id):notice||(overlay?'Live · showing on map':count+' in view')):
    state.status[id]==='stale'?lateWords(id,notice):   // 4.72.0: calm words while a layer's data is late
    (notice||'Unavailable · will retry');
  chip.textContent=!state.visible[id]?'OFF':state.status[id]==='ready'?(scheduled?'TIMETABLE':'LIVE'):state.status[id]==='stale'?'CATCHING UP':'OFF AIR';
  if(n)n.textContent=(entries||bubbled)&&!overlay&&!bubbleQuiet(id)?String(count):'—';continue;}
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
let layersKnown=false;    // 4.7.1: set once HQ's list of layers (the manifest) has arrived
function prepWanted(id){
  if(id==='map')return true;
  if(!state.live[id]||state.parked[id]||!state.visible[id])return false;
  if(state.status[id]!=='ready'&&state.status[id]!=='stale')return false;     // not answering: don't hold anyone up
  if(id==='road')return !!state.frames.road?.overlay;   // 4.86.0: zoomed out too (see prepReady)
  return true;
}
function prepReady(id){
  if(id==='map')return 'street map drawn' in SPEED.first||state.renderer==='fallback'||state.renderer==='leaflet-degraded';
  // 4.86.0 (Rob 18 Nov 16:06 "the traffic layer always says delayed on the start screen"): since 4.76.0 visits start on
  // the whole UK, further out than the traffic colours' zoom, so 'traffic shown' could never happen there and the board
  // said DELAYED after 6.5 s although the traffic engine had answered. Zoomed out, an engine that answered 'ready' is ready;
  // zoomed in, the colours must really be on the map. A slow or quiet engine still says DELAYED.
  if(id==='road'){const o=state.frames.road?.overlay;
    return 'traffic shown' in SPEED.first||(!!o&&state.z<o.minZoom&&state.status.road==='ready'&&(SPEED.ask.road?.n||0)>0);}
  return (SPEED.ask[id]?.n||0)>0;
}
function makeReadyList(){
  const box=el('div','prep-list');box.setAttribute('aria-live','polite');
  const items={};for(const [id,icon,name] of PREP_ITEMS){const row=el('span','prep-item');row.dataset.id=id;
    const pic=el('span','prep-icon',(ICON_PATHS[id]||UI_PATHS[id])?'':icon);if(ICON_PATHS[id]||UI_PATHS[id])pic.append(ownIcon(id));row.append(pic,el('span','prep-name',name),el('span','prep-tick','…'));items[id]=row;box.append(row);}
  prepLists.add({box,items,t0:performance.now()});updatePrep();return box;
}
function updatePrep(){
  let all=true;
  const now=performance.now();
  for(const list of prepLists){const {box,items}=list;
    if(!box.isConnected&&box.dataset.started)continue;box.dataset.started='1';
    // 4.7.2 (owner 1 Nov 16:00: "still no animation on the welcome screen"): everything was usually ready before
    // anyone could see it, so the tiles were green at once. Each tile now pulses for a moment, then turns green in
    // turn (0.6 s, 1.4 s, 2.2 s ...) - never before its layer is really ready, so green is always true.
    let n=0;
    for(const [id] of PREP_ITEMS){const row=items[id],want=prepWanted(id);
      const ok=!want||(prepReady(id)&&now>=list.t0+600+800*n);if(want)n++;
      row.hidden=!want;row.classList.toggle('done',ok);const t=row.querySelector('.prep-tick');if(t)t.textContent=ok?'✓':'…';if(!ok)all=false;
      if(list.flaps)flapState(list,id,row,want,ok,now);}
  }
  return all;
}
// 4.26.0 (owner 6 Nov 12:53-12:56: "I really love the flappy coming soon thing on the holding page ... Can that be done
// on the intro 5 seconds page ... for each of the models loading ... with a fun twist? loading turns to arrived or ready.
// they spin until ready?"): on the welcome screen "Preparing streams" is a split-flap departure board, like the holding
// page's. A row per layer: lamp, icon, name, flaps. Loading: a loading word, fluttering, lamp blinking. Really ready
// (same rules and the same one-after-another timing as before): the flaps spin and settle on a ready word, lamp green.
// Honest: DELAYED when a layer is slow or not answering, TIMETABLE when trains come from the timetable.
const FLAP_N=14,FLAP_CHARS='ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const FLAP_WORDS=Object.freeze({map:['LOADING','ARRIVED'],road:['WARMING UP','GREEN LIGHT'],bus:['AT THE STOP','ALL ABOARD'],
  rail:['BOARDING','ON TIME'],air:['TAXIING','CLEARED TO FLY']});
const FLAP_SLOW_MS=6500;
const REDUCE_MOTION=matchMedia('(prefers-reduced-motion: reduce)').matches;
function prepIcon(id){
  if(MENU_PIC[id])return menuPic(id);   // 4.87.0: the map's own pictures (planes too)
  return (ICON_PATHS[id]||UI_PATHS[id])?ownIcon(id):document.createTextNode(id==='road'?'≋':'');
}
function makeFlapBoard(){
  const box=el('div','prep-list flapboard');box.setAttribute('role','status');box.setAttribute('aria-live','polite');
  const items={},cells={};
  for(const [id,,name] of PREP_ITEMS){
    const row=el('div','prep-item fb-row');row.dataset.id=id;row.dataset.state='loading';
    const pic=el('span','prep-icon fb-icon');pic.append(prepIcon(id));
    const flaps=el('span','fb-flaps');flaps.setAttribute('aria-hidden','true');cells[id]=[];
    for(let i=0;i<FLAP_N;i++){const c=el('span','fb-flap');flaps.append(c);cells[id].push({c,spin:0,target:' '});}
    row.append(el('span','fb-lamp'),pic,el('span','prep-name fb-name',name),flaps,el('span','prep-tick fb-said','…'),el('span','fb-said fb-words',''));
    items[id]=row;box.append(row);
  }
  const list={box,items,t0:performance.now(),flaps:cells,words:{}};
  prepLists.add(list);
  for(const [id] of PREP_ITEMS)flapWord(list,id,FLAP_WORDS[id][0],true);
  updatePrep();
  // the flaps' own clock: turns over while the welcome screen is open (also when it is opened again later)
  const timer=setInterval(()=>{if(!box.isConnected){clearInterval(timer);return;}if(!box.offsetParent)return;updatePrep();flapTick(list);},REDUCE_MOTION?400:90);
  return box;
}
function flapWord(list,id,word,first){
  if(list.words[id]===word)return;list.words[id]=word;
  const text=word.padEnd(FLAP_N).slice(0,FLAP_N);
  list.flaps[id].forEach((f,i)=>{f.target=text[i];
    if(REDUCE_MOTION){f.spin=0;f.c.textContent=text[i];}
    else f.spin=(first?2:3)+i+((Math.random()*3)|0);});   // left to right, like the holding page
  const row=list.items[id],name=row.querySelector('.fb-name').textContent;
  row.querySelector('.fb-words').textContent=name+': '+word.toLowerCase()+'. ';
}
function flapState(list,id,row,want,ok,now){
  // On the board: the map, and any layer that has said hello (the layer list says it is up) or has answered. A layer that
  // is not connected at all stays off (owner 1 Nov); one that is slow or goes quiet says DELAYED instead of vanishing.
  const live=id==='map'||(state.live[id]&&!state.parked[id]&&state.visible[id]&&!(id==='road'&&!state.frames.road?.overlay));
  list.seen=list.seen||{};if(want||id==='map'||state.announced?.[id])list.seen[id]=true;
  const shown=live&&list.seen[id]===true,ready=want&&ok;
  row.hidden=!shown;
  if(shown&&!ready){row.classList.remove('done');const t=row.querySelector('.prep-tick');if(t)t.textContent='…';}   // never ticked early
  const st=ready?(id==='rail'&&state.frames.rail?.scheduled?'timetable':'ready'):now-list.t0>FLAP_SLOW_MS?'late':'loading';
  row.dataset.state=st;
  flapWord(list,id,st==='ready'?FLAP_WORDS[id][1]:st==='timetable'?'TIMETABLE':st==='late'?'DELAYED':FLAP_WORDS[id][0],false);
}
function flapTick(list){
  if(REDUCE_MOTION)return;
  for(const [id,cells] of Object.entries(list.flaps)){
    const row=list.items[id];if(row.hidden)continue;
    if(row.dataset.state==='loading'&&Math.random()<0.35){const f=cells[(Math.random()*FLAP_N)|0];if(!f.spin)f.spin=2+((Math.random()*3)|0);}
    for(const f of cells){
      if(f.spin>0){f.spin--;f.c.textContent=f.spin?FLAP_CHARS[(Math.random()*FLAP_CHARS.length)|0]:f.target;
        f.c.classList.remove('turn');void f.c.offsetWidth;f.c.classList.add('turn');}
      else if(f.c.textContent!==f.target)f.c.textContent=f.target;}
  }
}
function startPreparing(showPanel){
  const t0=performance.now();let panel=null;
  if(showPanel){panel=el('div','prep-panel');panel.setAttribute('role','status');
    panel.append(el('strong','prep-title','Preparing streams…'),makeReadyList());document.body.append(panel);}
  // 4.7.1 (owner 1 Nov 15:42: the streams "part doesnt work on the welcome screen"): 4.7.0 stopped updating as soon
  // as everything it knew about was ready - often just the map, before HQ had learnt which layers exist. Now
  // "all ready" only counts once the layer list has arrived, and the row keeps updating for 30 s.
  const timer=setInterval(()=>{
    const done=updatePrep()&&layersKnown,waited=performance.now()-t0;
    if(panel&&(done||waited>PREP_MAX_MS)){const p=panel;panel=null;if(done)p.querySelector('.prep-title').textContent='All set';
      p.classList.add('fading');setTimeout(()=>p.remove(),done?700:400);}
    if(waited>30000)clearInterval(timer);
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
  L1.push('Vanished on screen   '+VANISH.total+'  (buses '+(VANISH.byLayer.bus||0)+', trains '+(VANISH.byLayer.rail||0)+', aircraft '+(VANISH.byLayer.air||0)+')'+
    (VANISH.recent.length?'   last: '+VANISH.recent[VANISH.recent.length-1].label.slice(0,40)+' at '+new Date(VANISH.recent[VANISH.recent.length-1].at).toLocaleTimeString('en-GB',{hour12:false}):''));
  box.textContent=L1.join('\n');
}
setInterval(speedPanel,1000);
function paint(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;drawFallback();markers();counts();syncOverlays();liveCredit();$('zoom-level').textContent='ZOOM '+state.z.toFixed(1);});}
function validNum(n){return typeof n==='number'&&Number.isFinite(n);}
function checkFrame(id,f){if(!f||typeof f!=='object'||Array.isArray(f)||f.component!==id||f.contractVersion!=='1.0'||!['ready','stale','unavailable','not_installed'].includes(f.health)||typeof f.receivedAt!=='string'||!Number.isFinite(Date.parse(f.receivedAt))||typeof f.expiresAt!=='string'||!Number.isFinite(Date.parse(f.expiresAt))||Date.parse(f.expiresAt)<Date.parse(f.receivedAt)||typeof f.attribution!=='string'||f.attribution.length>256||!Array.isArray(f.entities)||f.entities.length>10000)throw Error('Invalid component v1 envelope');
const ids=new Set(),entities=[];for(const e of f.entities){if(!e||typeof e.id!=='string'||!e.id||e.id.length>128||ids.has(e.id)||typeof e.label!=='string'||e.label.length>160||!['observed','estimated','stale'].includes(e.quality)||!Number.isFinite(Date.parse(e.observedAt)))throw Error('Invalid entity');ids.add(e.id);
// 4.34.0 (owner 10 Nov 22:32: "hide them is for regular users, the admin/dev page should show them"): planes whose owners
// asked trackers to hide them are left out of the public page; the owner's /full page shows them.
if(e.private===true&&USER_VIEW)continue;
const p=e.position;if(p!==null&&(!p||!validNum(p.lon)||!validNum(p.lat)||Math.abs(p.lon)>180||Math.abs(p.lat)>90||(p.headingDeg!=null&&(!validNum(p.headingDeg)||p.headingDeg<0||p.headingDeg>360))))throw Error('Invalid point');const position=p===null?null:{lon:p.lon,lat:p.lat,headingDeg:p.headingDeg??null};
entities.push({component:id,id:e.id,label:e.label,quality:e.quality,observedAt:e.observedAt,badge:typeof e.badge==='string'&&/^[A-Za-z0-9]{1,8}$/.test(e.badge)?e.badge:null,variant:typeof e.variant==='string'&&/^[a-z0-9-]{1,30}$/.test(e.variant)?e.variant:null,private:e.private===true,position,motion:checkMotion(e.motion,position),...(id==='air'&&typeof e.typeCode==='string'&&/^[A-Z0-9]{2,4}$/.test(e.typeCode)?{typeCode:e.typeCode}:{}),...(id==='air'&&Number.isInteger(e.altFt)&&e.altFt>=0&&e.altFt<=60000?{altFt:e.altFt}:{})});/* 4.84.0: type code and height, checked */}
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
const pollMs=Number.isInteger(f.nextPollAfterMs)?clamp(f.nextPollAfterMs,2000,120000):30000;   // 4.40.0: 2 s floor (was 5 s), for 'still loading'
// 4.25.0 (owner 5 Nov 22:05, "belt and braces"): a layer may say it is showing its timetable (scheduled) rather than live
// reports, and give the real reason for a break (noticeDetail) - shown only on the owner's full page. Generic.
const noticeDetail=typeof f.noticeDetail==='string'?f.noticeDetail.replace(/[\u0000-\u001f\u007f]/g,' ').slice(0,200):'';
return {component:id,health:f.health==='ready'&&Date.parse(f.expiresAt)<serverNow()?'stale':f.health,entities,attribution:f.attribution,notice,overlay,pollMs,groupStacks:f.groupStacks===true,scheduled:f.scheduled===true,noticeDetail};}
// 4.1.0 optional motion hint from an engine. HQ only does generic arithmetic with it
// (straight-line "carry on at this speed" or "head for this point by this time");
// anything malformed is ignored and the vehicle simply stays at its reported position.
function checkMotion(m,p){
  if(!m||typeof m!=='object'||!p)return null;
  if(m.mode==='heading'&&validNum(m.speedMps)&&m.speedMps>0&&m.speedMps<=350&&validNum(m.maxSeconds)&&m.maxSeconds>0&&m.maxSeconds<=300&&p.headingDeg!=null)
    // 4.1.3 optional slowToStop: ease from the reported speed to a standstill at maxSeconds.
    return {mode:'heading',speedMps:m.speedMps,maxSeconds:m.maxSeconds,slow:m.slowToStop===true,
      // 4.92.0 optional turnDegPerSec (+ turnSeconds): curve round a turn; never with slowToStop (planes on the ground)
      turn:m.slowToStop!==true&&validNum(m.turnDegPerSec)&&Math.abs(m.turnDegPerSec)>=0.05&&Math.abs(m.turnDegPerSec)<=8?m.turnDegPerSec:0,
      turnS:validNum(m.turnSeconds)&&m.turnSeconds>0&&m.turnSeconds<=30?m.turnSeconds:0};
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
    return {mode:'path',pts,cum,arriveAtMs:Date.parse(m.arriveAt),maxSeconds:m.maxSeconds,speedMps:speed,slow:speed!==null&&m.slowToStop===true,
      ease:speed===null&&['in','out','both'].includes(m.ease)?m.ease:null};
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
    return {mode:'timed',pts,times,delay,jumps,forecast:m.forecast===true,fresh:m.fresh===true};
  }
  return null;
}
// Only same-origin data. Abort slow snapshots; one spoke must not delay another.
async function requestBounded(url,maxBytes,timeoutMs){
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),timeoutMs);
  try{
    const sent=performance.now(),response=await fetch(url,{cache:'no-store',signal:ctl.signal});
    // 4.28.0: an answer kept and handed out again (by Cloudflare, after launch) carries an old server time: not for the clock
    const kept=Number(response.headers.get('age'))>0||/hit/i.test(response.headers.get('cf-cache-status')||'');
    if(!kept)noteServerTime(response.headers.get('x-tpa-now'),sent,performance.now());
    if(!response.ok || Number(response.headers.get('content-length'))>maxBytes)throw Error('unavailable');
    const body=await response.text();
    if(new TextEncoder().encode(body).byteLength>maxBytes)throw Error('oversize');
    return JSON.parse(body);
  }finally{clearTimeout(timer);}
}
// ---- 4.56.0 Plan B: published bus tiles ("publish, don't answer"; owner 13 Nov 2026 19:15 "Now please") ----------
// The bus engine writes /tiles/bus/meta.json, tiles s/<row>_<col>.json (street views) and b/<row>_<col>.json (city
// views) every 10 s, and route lines routes/<build>/<line>.json once per timetable; Caddy hands them out (later
// Cloudflare). This builds the same frame the engine used to send, so everything else on the page is unchanged.
// 4.56.1 (owner 13 Nov 20:11: only 24% of buses placed on a route in the published tiles - his call first): tiles only
// with ?tiles=1 for now; the default stays the old per-visitor answer.
// 4.59.0 (owner 13 Nov 23:11: "movement is great"): the tile way is the normal way for everyone; ?tiles=0 is the way back.
const BUS_TILES={on:new URLSearchParams(location.search).get('tiles')!=='0',used:false,lines:new Map(),asking:new Map()};
// 4.61.0 (forecast plan Stage 3; Rob 14 Nov 2026 21:55 "Yes, build it now"; bus 0.22.0's forecast `f`): with ?forecast=1
// a bus on a timetabled journey is shown about where it is NOW (its forecast, no delay) instead of ~2 min behind; the
// normal map is unchanged. prev = each bus's last forecast marks, so a new forecast never slides a bus back
// (bus-replay.js forecastTrack). Counts of how each new frame met the bus: window.TPA_BUS_FORECAST().
// 4.80.0 (Rob 18 Nov 05:54, after watching ?forecast=1 against the normal map: "forecast one is miles better"; 05:44 "our
// server knows where they are going, and the individual user should be given a LIVE set of map data"): the server's own
// forecast is the normal map for timetabled buses (forecast plan Stage 4). ?forecast=0 is the way back to N1 (4.64.0-4.79.0).
const BUS_FORECAST=(()=>{const q=new URLSearchParams(location.search),m=q.get('motion');
  return {on:q.get('forecast')!=='0'&&!['c4','c3','replay','n1','compare'].includes(m),prev:new Map(),last:new Map(),how:{}};})();
// 4.82.0: a town-level forecast path [[seconds after o, lon, lat], ...] as a track from now. Carries on from where the bus
// is drawn (prev = its last track): drawn ahead of the new path -> waits there (up to 20 s, never backwards); drawn behind
// -> catches up within 3-30 s (at most 2.5x the time it is behind); more than 150 m off the path -> starts afresh.
function townPathTrack(g,o,now,prev){
  if(!Array.isArray(g)||g.length<2)return null;
  const T=[],P=[];
  for(const q of g){if(!Array.isArray(q)||q.length!==3||!validNum(q[0])||!validNum(q[1])||!validNum(q[2]))return null;
    const t=o+q[0];if(T.length&&t<T[T.length-1])return null;T.push(t);P.push([q[1],q[2]]);}
  const r1=x=>Math.round(x*10)/10,lerp=(a,b,f)=>[a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f];
  const at=t=>{if(t<=T[0])return P[0];const n=T.length;if(t>=T[n-1])return P[n-1];let k=1;while(T[k]<t)k++;
    return lerp(P[k-1],P[k],(t-T[k-1])/Math.max(1e-9,T[k]-T[k-1]));};
  const metres=(a,b)=>{const c=Math.cos(a[1]*Math.PI/180);return Math.hypot((b[0]-a[0])*111320*c,(b[1]-a[1])*110540);};
  const from=t0=>{const pts=[at(t0)],times=[r1(Math.max(now,t0))];for(let k=0;k<T.length;k++)if(T[k]>t0){pts.push(P[k]);times.push(r1(T[k]));}
    return {pts,times};};
  const end=now>=T[T.length-1];
  let drawn=null;
  if(prev&&Array.isArray(prev.points)&&prev.points.length&&Array.isArray(prev.times)){const pt=prev.points,tt=prev.times;
    if(now<=tt[0])drawn=pt[0];else if(now>=tt[tt.length-1])drawn=pt[pt.length-1];
    else{let k=1;while(tt[k]<now)k++;drawn=lerp(pt[k-1],pt[k],(now-tt[k-1])/Math.max(1e-9,tt[k]-tt[k-1]));}}
  const here=at(now);
  if(drawn&&metres(drawn,here)>10){
    let best=Infinity,tp=now;                         // where on the new path the drawn bus is (nearest point, its time)
    for(let k=1;k<T.length;k++){const a=P[k-1],b=P[k],c=Math.cos(a[1]*Math.PI/180),ax=a[0]*111320*c,ay=a[1]*110540,
      dx=b[0]*111320*c-ax,dy=b[1]*110540-ay,px=drawn[0]*111320*c-ax,py=drawn[1]*110540-ay,L2=dx*dx+dy*dy,
      u=L2>1e-9?Math.max(0,Math.min(1,(px*dx+py*dy)/L2)):0,d=Math.hypot(px-u*dx,py-u*dy);
      if(d<best){best=d;tp=T[k-1]+(T[k]-T[k-1])*u;}}
    if(best<=150){
      if(tp>now+0.5){const until=Math.min(tp,now+20);       // drawn ahead: wait where it is until the path catches up
        const rest=from(until);if(tp<=now+20)return {position:drawn,points:[drawn,drawn,...rest.pts.slice(1)],times:[r1(now),r1(until),...rest.times.slice(1)],waiting:false,how:'waiting'};
        return {position:drawn,points:[drawn],times:[r1(now)],waiting:true,how:'waiting'};}
      if(tp<now-0.5&&!end){const join=now+Math.max(3,Math.min(30,(now-tp)*2.5));   // drawn behind: catch up
        const rest=from(join);return {position:drawn,points:[drawn,...rest.pts],times:[r1(now),...rest.times.map((t,i)=>i?t:r1(join))],waiting:false,how:'catching up'};}
    }
  }
  if(end){const q=P[P.length-1];return {position:q,points:[q],times:[r1(now)],waiting:true,how:'arrived'};}
  const f=from(now);return {position:f.pts[0],points:f.pts,times:f.times,waiting:false,how:'on time'};
}
window.TPA_BUS_FORECAST=()=>({on:BUS_FORECAST.on,kept:BUS_FORECAST.prev.size,how:{...BUS_FORECAST.how}});
// 4.62.0 (Rob 15 Nov 2026 01:48: live test of the motion study's candidates - TEST ADDRESSES ONLY): ?motion=c4 or
// ?motion=c3 shows each bus on its route line at its newest report carried on carefully (bus-replay.js freshTrack, the
// study's exact rules); ?motion=replay is today's replay with the same measuring switched on. No ?motion = today's page,
// unchanged (nothing below runs). Buses driven along roads, stepping buses and the zoomed-out view keep today's replay
// in every mode. Measuring: window.TPA_BUS_MOTION() (counts, time spent building tracks), TPA_BUS_MOTION_NOW() (where each
// route bus is drawn now, in metres along its line).
const BUS_MOTION=(()=>{const q=new URLSearchParams(location.search).get('motion');
  return {mode:['c4','c3','replay'].includes(q)?q:null,prev:new Map(),now:new Map(),frames:0,ms:0,points:0,how:{},buses:{}};})();
window.TPA_BUS_MOTION=()=>({mode:BUS_MOTION.mode,frames:BUS_MOTION.frames,buildMs:Math.round(BUS_MOTION.ms*10)/10,
  msPerFrame:BUS_MOTION.frames?Math.round(BUS_MOTION.ms/BUS_MOTION.frames*100)/100:null,points:BUS_MOTION.points,
  how:JSON.parse(JSON.stringify(BUS_MOTION.how)),buses:{...BUS_MOTION.buses}});
window.TPA_BUS_MOTION_NOW=()=>{const R=window.TPA_BUS_REPLAY,t=serverNow()/1000,out=[];
  for(const [id,b] of BUS_MOTION.now)out.push([id,b.london,b.kind,b.marks?R.alongAt(b.marks,t):(b.r?R.alongAt(b.r,t-b.d):null),b.s,b.d,b.marks?0:b.d]);return out;};
// 4.63.0 (Rob 15 Nov 2026 14:52: "an isolated, reversible browser test mode for N1 on the test site only ... simultaneous
// live comparisons of N1, C4 and the original replay, using the same incoming data and matching vehicles") - TEST
// ADDRESSES ONLY. ?motion=n1 draws every bus with the clean-sheet engine N1 (assets/bus-fresh.js, loaded only here).
// ?motion=compare works out replay, C4 and N1 for every bus from the SAME tile read and shows one of them
// (&show=n1 | c4 | replay, default n1); the other two are kept for measuring. No ?motion, or any other value: none of
// this runs (BUS_N1.on is false) and the page is exactly 4.62.0. Zoomed out (city tiles) every mode keeps today's replay.
// 4.64.0 (Rob 15 Nov 2026 21:07: "lets get n1 moving. It has proved its worthiness"): N1 is now how buses move on the
// NORMAL address too. Today's replay stays one address away: ?motion=replay (and the other test modes ?motion=c4 / c3 and
// ?forecast=1 keep their own engines). (4.72.2: N1 at town level too.)
const BUS_N1=(()=>{const q=new URLSearchParams(location.search),m=q.get('motion'),sh=q.get('show');
  return {on:!['c4','c3','replay'].includes(m)&&(m==='n1'||m==='compare'||q.get('forecast')==='0'),compare:m==='compare',show:m==='compare'?(['n1','c4','replay'].includes(sh)?sh:'n1'):'n1',
    engine:null,tState:null,focus:null,lines:new Map(),c4prev:new Map(),now:new Map(),items:[],frames:0,ms:{replay:0,c4:0,n1:0},loading:false};})();
setTimeout(()=>{if(BUS_N1.on)n1Ready();},0);   // 4.64.0: start fetching N1 at once, so the first bus read already uses it
function n1Ready(){
  if(!BUS_N1.on)return false;
  if(window.TPA_FRESH&&!BUS_N1.engine)BUS_N1.engine=new window.TPA_FRESH.FreshEngine();
  if(BUS_N1.engine)return true;
  if(!BUS_N1.loading){BUS_N1.loading=true;const s=document.createElement('script');s.src='/assets/bus-fresh.js'+((document.querySelector('script[src^="/assets/hq.js"]')?.getAttribute('src')||'').match(/\?v=[0-9A-Za-z.-]+$/)?.[0]||'');   /* 4.75.1: same version tag as the page */s.defer=true;document.head.append(s);}
  return false;
}
function n1Line(sid,sh){                          // N1's own line type, made once from the same route line the page has
  if(BUS_N1.lines.has(sid))return BUS_N1.lines.get(sid);
  let L=null;try{if(sh)L=new window.TPA_FRESH.Line(sh.lon.map((x,i)=>[x,sh.lat[i]]));}catch(_){L=null;}
  if(L){BUS_N1.lines.set(sid,L);if(BUS_N1.lines.size>3000)BUS_N1.lines.delete(BUS_N1.lines.keys().next().value);}   // 4.64.0: "no line yet" is not remembered
  return L;
}
// One tile read: bring N1's icons up to now (one step a second, with what it already knew), give it the new read, then
// plan the next 25 s for the page without changing its state. Returns {id: [[t, lon, lat, jump, how, onLineAhead], ...]}.
function n1Frame(now,items,lines){
  const E=BUS_N1.engine;
  if(BUS_N1.tState===null||now-BUS_N1.tState>60)BUS_N1.tState=Math.floor(now);
  while(BUS_N1.tState+1<=now){BUS_N1.tState+=1;E.step(BUS_N1.tState,1);}
  // 4.63.1 (live test 15 Nov 16:50: N1's only backward nudges were at the 10-second reads, 2 in 266 there vs 0 in 9,660
  // elsewhere): bring the icons right up to NOW before the new read, so the new track starts exactly where the old one
  // has the bus at this moment (before, it restarted from up to 1 s earlier and could pull a slowing bus back a few metres)
  if(now-BUS_N1.tState>0.001){E.step(now,now-BUS_N1.tState);BUS_N1.tState=now;}
  // 4.63.2 (Rob 15 Nov 18:00, Follow Me in Hull: the bus "briefly vanishes or slips out of view" just after some reads - N1
  // faded it across a gap and the camera jumped with it): the followed bus is never faded; N1 glides it instead. Set after
  // the catch-up steps above, so they still replay exactly the track already on screen.
  E.focus=BUS_N1.focus=follow&&follow.comp==='bus'?follow.id:null;
  E.frame(now,items,sid=>n1Line(sid,lines.get(sid)));
  return E.plan(BUS_N1.tState,25);
}
function n1Track(plan,kl,noFade){                  // the page's "timed" track; a fade is two points 0.1 s apart (4.63.2: never for the followed bus)
  if(!Array.isArray(plan)||!plan.length)return null;
  const pts=[],times=[];
  for(let k=0;k<plan.length;k++){const q=plan[k];
    if(q[3]&&k>0&&!noFade){pts.push(pts[pts.length-1]);times.push(Math.round((q[0]-0.1)*10)/10);}
    pts.push([q[1],q[2]]);times.push(Math.round(q[0]*10)/10);}
  for(let k=1;k<times.length;k++)times[k]=Math.max(times[k],times[k-1]);
  const R=window.TPA_BUS_REPLAY,p2=R.keepLeft(pts,kl||0,pts[0][1]);
  return {position:p2[0],points:p2,times,predictive:!!plan[0][5],how:plan[0][4]};
}
function trackAt(tr,t){const T=tr.times,P=tr.points;if(t<=T[0])return P[0];let i=1;while(i<T.length&&T[i]<t)i++;
  if(i>=T.length)return P[P.length-1];const q=T[i]>T[i-1]?(t-T[i-1])/(T[i]-T[i-1]):1,a=P[i-1],b=P[i];return [a[0]+(b[0]-a[0])*q,a[1]+(b[1]-a[1])*q];}
window.TPA_BUS_COMPARE=()=>({on:BUS_N1.on,compare:BUS_N1.compare,show:BUS_N1.show,loaded:!!BUS_N1.engine,frames:BUS_N1.frames,focus:BUS_N1.focus||null,
  msPerFrame:Object.fromEntries(Object.entries(BUS_N1.ms).map(([k,v])=>[k,BUS_N1.frames?Math.round(v/BUS_N1.frames*100)/100:null])),
  buses:BUS_N1.now.size,n1:BUS_N1.engine?{buses:BUS_N1.engine.buses.size,learned:BUS_N1.engine.learn.n,stats:BUS_N1.engine.stats}:null});
// where each engine draws each bus at server time t (seconds), and what the last tile read said (for measuring only)
window.TPA_BUS_COMPARE_AT=t=>{const out=[];for(const [id,b] of BUS_N1.now)out.push([id,b.london,b.kind,b.s,trackAt(b.replay,t),b.c4?trackAt(b.c4,t):null,b.n1?trackAt(b.n1,t):null,b.how,b.d]);return out;};
window.TPA_BUS_COMPARE_ITEMS=()=>BUS_N1.items.map(it=>[it.i,it.p,it.s||'',Array.isArray(it.r)?it.r:null]);
async function tileJSON(url,timeoutMs,missingOk){
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),timeoutMs);
  try{const r=await fetch(url,{signal:ctl.signal});
    if(r.status===404&&missingOk)return null;
    if(!r.ok)throw Error('tile '+r.status);
    const body=await r.text();if(body.length>3000000)throw Error('oversize');return JSON.parse(body);}
  finally{clearTimeout(timer);}
}
function busLine(build,sid){
  const key=build+'/'+sid;
  if(BUS_TILES.lines.has(key))return Promise.resolve(BUS_TILES.lines.get(key));
  if(BUS_TILES.asking.has(key))return BUS_TILES.asking.get(key);
  const safe=sid.replace(/[^A-Za-z0-9_.-]/g,'_').slice(0,120);
  const p=tileJSON(`/tiles/bus/routes/${encodeURIComponent(build)}/${encodeURIComponent(safe)}.json`,5000,true).then(d=>{
    let sh=null;try{if(d&&Array.isArray(d.c))sh=new window.TPA_BUS_REPLAY.Shape(d.c);}catch(_){sh=null;}
    BUS_TILES.lines.set(key,sh);BUS_TILES.asking.delete(key);
    if(BUS_TILES.lines.size>3000)BUS_TILES.lines.delete(BUS_TILES.lines.keys().next().value);   // oldest first
    return sh;},()=>{BUS_TILES.asking.delete(key);return null;});
  BUS_TILES.asking.set(key,p);return p;
}
// 4.59.1 (owner 13 Nov 23:50, the tile page "teleporting"): a bus's stretch of road is worked out once and kept while it
// is unchanged, instead of again every 10 s.
const ROAD_SHAPES=new Map();
function roadShape(it){
  const w=it.w,sig=w.length+':'+w[0]+':'+w[w.length-1],got=ROAD_SHAPES.get(it.i);
  if(got&&got.sig===sig)return got.sh;
  const sh=new window.TPA_BUS_REPLAY.Shape(w);ROAD_SHAPES.delete(it.i);ROAD_SHAPES.set(it.i,{sig,sh});
  if(ROAD_SHAPES.size>2000)ROAD_SHAPES.delete(ROAD_SHAPES.keys().next().value);
  return sh;
}
// ---- 4.67.0 town bubbles ------------------------------------------------------------------------------------------------
// Rob 16 Nov 11:40 (declutter: "grouped buses/trains per city that separate as you zoom in"; one bubble per town; planes
// always separate) and 17 Nov 10:50 (they separate at zoom 13). Below zoom 13 buses and trains are one bubble per town,
// counted by their engines (counts.json: every vehicle on the map, by 0.1 degree square - zoomed out, a view only gets part
// of the vehicles, so the page cannot count them itself). Railway stations, bus stations and stops wait for zoom 13 too.
// Names: the street map's own city and town labels (cities only when far out). Bubbles that would overlap join the bigger.
// ?bubbles=0 is the old way; without fresh counts (e.g. an engine without them) every vehicle is drawn as before.
const BUBBLE_LAYERS=['bus','rail'],BUBBLE_QUIET=['stations','busstations','stops'];
// 4.75.0 (Rob 18 Nov 00:47: "cutting down on the detail on the phones and tablets to still offer a very usable experience
// but not an overcrowded one"): on phones and tablets (a touch screen, or 1,024 px wide or less) the town bubbles split
// into separate buses and trains one zoom later (14 instead of 13), and stations come with them; phones also show fewer
// vehicles at once (PHONE_CAP). Computers are unchanged.
function smallScreen(){return !KIDS&&(root.clientWidth<=1024||(window.matchMedia&&matchMedia('(pointer:coarse)').matches));}
// 4.75.2 (Rob 18 Nov 03:36, on his phone after launch: tapping a town bubble "only shows stations"): at zoom 14 a phone
// shows a patch only ~2 km wide, so at night the few buses of a town bubble were off screen. Bubbles split at 13 on every
// screen again; phones stay calmer by showing at most PHONE_CAP of a layer, and a tap now centres on where the town's
// vehicles actually are (their counted squares), not on the town's name.
// 4.82.0 (Rob 18 Nov 06:17: buses and trains "group a bit too quickly"; users "will be zooming in and out a lot"): they
// separate one level further out, at 12.
function bubbleZoom(){return 12;}
const BUBBLES_ON=!KIDS&&new URLSearchParams(location.search).get('bubbles')!=='0';
const COUNTS={bus:null,rail:null,asked:-1};
const BUBBLE_WORD={bus:['bus','buses'],rail:['train','trains']};
let bubbleSig='';
const bubbleBox=document.createElement('div');bubbleBox.className='bubble-layer';markerLayer.append(bubbleBox);
async function fetchCounts(){
  if(!BUBBLES_ON||!map||map.getZoom()>=bubbleZoom())return;
  const slot=Math.floor(serverNow()/10000);if(COUNTS.asked===slot)return;COUNTS.asked=slot;
  await Promise.all(BUBBLE_LAYERS.filter(id=>state.visible[id]).map(async id=>{   // 4.94.0: only layers that are shown
    try{const c=await tileJSON(`/tiles/${id}/counts.json?r=${slot}`,4000,true);
      if(c&&c.v===1&&validNum(c.cell)&&c.cell>0&&Array.isArray(c.c)&&validNum(c.at)){COUNTS[id]=c;bubbleSig='';paint();}}catch(_){}}));
}
setInterval(fetchCounts,2000);setTimeout(fetchCounts,300);
function countsFresh(id){const c=COUNTS[id];return !!c&&serverNow()/1000-c.at<=60;}
function bubbleLayer(id){return BUBBLES_ON&&!!map&&map.getZoom()<bubbleZoom()&&BUBBLE_LAYERS.includes(id)&&countsFresh(id);}
function bubblesShowing(){return BUBBLE_LAYERS.some(bubbleLayer);}
// while its town bubbles show, a layer's vehicles are not downloaded at all (the browser's biggest saving zoomed out) -
// unless one of them is chosen or followed, which stays on the map
function bubbleSkips(id){const skip=bubbleLayer(id)&&state.selected?.component!==id&&follow?.comp!==id;BUBBLE_SKIPPED[id]=skip;return skip;}
// 4.67.4 (Rob 14:11: zoomed in on Beverley "there wasnt any, then waited a while and 4 or 5 appeared"): the last answer
// was the empty bubble one, which "covered" the bigger zoomed-out view, so zooming in asked for nothing until the next 10 s
// round. A layer that was in bubbles is asked again as soon as it leaves them.
const BUBBLE_SKIPPED=Object.create(null);
// 4.73.0 (tester 17 Nov ~19:30: the close-up round York station was too busy; Rob 18 Nov 00:19 "all"): bus stop icons wait
// for zoom 15 (stations and bus stations still show from 13); a chosen stop stays
const STOPS_FROM_ZOOM=15;
function bubbleQuiet(id){if(id==='stops'&&BUBBLES_ON&&map&&map.getZoom()<STOPS_FROM_ZOOM)return true;return BUBBLE_QUIET.includes(id)&&bubblesShowing();}
function countInView(id){
  const c=COUNTS[id];if(!c||!map)return 0;const b=map.getBounds(),S=b.getSouth(),N=b.getNorth(),W=b.getWest(),E=b.getEast();let n=0;
  for(const [r,col,k] of c.c){const lat=(r+0.5)*c.cell,lon=(col+0.5)*c.cell;if(lat>=S&&lat<=N&&lon>=W&&lon<=E)n+=k;}
  return n;
}
// 4.67.1 (17 Nov, live: Leeds's trains came out as "Rothwell" - the middle of Leeds's 0.1 degree square is nearer
// Rothwell): a city counts as twice as near as a town
function nearestPlace(lat,lon,places){
  const k=Math.cos(lat*Math.PI/180);let best=null,bd=Infinity;
  for(const p of places){const dd=((p.lat-lat)**2+((p.lon-lon)*k)**2)/(p.city?4:1);if(dd<bd){bd=dd;best=p;}}
  return best;
}
// far out (below 9) cities only; below 10 also the bigger towns (the street map's rank 12 or better): 4.67.3 (17 Nov 14:10,
// live at zoom 9: 570 buses around Wigan named "Ashton-in-Makerfield", rank 14; Wigan is 11)
function bubblePlaces(places,z){
  if(z<9&&places.some(p=>p.city))return places.filter(p=>p.city);
  if(z<10){const big=places.filter(p=>p.city||(p.rank||99)<=12);if(big.length)return big;}
  return places;
}
function kmApart(a1,o1,a2,o2){const k=Math.cos(a1*Math.PI/180);return 111.2*Math.sqrt((a2-a1)**2+((o2-o1)*k)**2);}
// how far a 0.1 degree square may be from the place it is counted in: far out a city takes its whole area
function bubbleReach(z){return z<9?60:z<10?25:8;}   // 8 km: a place inside or touching its 11 km square
function moreImportant(a,b){return (a.city?0:1000)+(a.rank||99)<(b.city?0:1000)+(b.rank||99);}
// 4.71.0: where a road incident is - the nearest named road on the street map (one of the same kind counts as 2.5 times
// nearer; none further than 80 m), and the nearest town within 15 km
const ROAD_CLASS={'Motorway':'motorway','Motorway slip road':'motorway','Main road (trunk)':'trunk','Main road slip road':'trunk',
  'A road':'primary','A road slip road':'primary','B road':'secondary','B road link':'secondary','Local road':'tertiary',
  'Street':'minor','Service road':'service','Track':'track'};
function nearestRoad(lon,lat,cls,roads,maxM=80){
  const kx=Math.cos(lat*Math.PI/180)*111195,ky=111195;let best=null,bs=Infinity;
  for(const r of roads||[])for(const l of r.lines||[])for(let i=0;i+1<l.length;i++){
    const ax=(l[i][0]-lon)*kx,ay=(l[i][1]-lat)*ky,vx=(l[i+1][0]-lon)*kx-ax,vy=(l[i+1][1]-lat)*ky-ay;
    const t=Math.max(0,Math.min(1,-(ax*vx+ay*vy)/((vx*vx+vy*vy)||1e-9))),dist=Math.hypot(ax+vx*t,ay+vy*t),score=r.cls===cls?dist:dist*2.5;
    if(score<bs){bs=score;best={ref:r.ref||'',name:r.name||'',metres:Math.round(dist)};}}
  return bs<=maxM?best:null;
}
// 4.71.3 (Rob 17 Nov 21:59, Leeds: the same roadworks said "A58(M) Inner Ring Road" zoomed in and only "Motorway" zoomed
// out): zoomed out, the street map's road lines are simplified and can sit further from the incident, so the reach grows
// (80 m from zoom 15, 160 m at 13-14, 250 m further out), and a road once found for an incident is kept for the visit.
const ROAD_FOUND=new Map();
function roadFor(id,lon,lat,cls,roads,zoom){
  const maxM=zoom>=15?80:zoom>=13?160:250,found=nearestRoad(lon,lat,cls,roads,maxM);
  if(found){ROAD_FOUND.set(id,found);if(ROAD_FOUND.size>300)ROAD_FOUND.delete(ROAD_FOUND.keys().next().value);return found;}
  return ROAD_FOUND.get(id)||null;
}
function roadWhere(category,road,town){let t=road?[road.ref,road.name].filter(Boolean).join(' '):(category||'');if(town)t+=(t?', ':'')+'near '+town;return t;}
function townNear(lon,lat){
  const places=(window.TPA_MAP_PLACES?.()||[]).concat(POI.map(([name,plon,plat])=>({name,lon:plon,lat:plat,city:false})));
  const b=nearestPlace(lat,lon,places);return b&&kmApart(lat,lon,b.lat,b.lon)<=15?b.name:'';
}
window.TPA_HQ_ROADS={nearest:(lon,lat,cls,roads,maxM)=>nearestRoad(lon,lat,cls,roads,maxM),roadFor:(id,lon,lat,cls,roads,zoom)=>roadFor(id,lon,lat,cls,roads,zoom),where:(c,r,t)=>roadWhere(c,r,t),town:(lon,lat)=>townNear(lon,lat)};
window.TPA_HQ_BUBBLES={nearest:(lat,lon,places)=>nearestPlace(lat,lon,places)?.name||'',moreImportant:(a,b)=>moreImportant(a,b),reach:z=>bubbleReach(z),places:(pl,z)=>bubblePlaces(pl,z).map(p=>p.name)};
function bubbleWords(g){
  return BUBBLE_LAYERS.filter(id=>g[id]>0).map(id=>g[id].toLocaleString('en-GB')+' '+BUBBLE_WORD[id][g[id]===1?0:1]);
}
function drawBubbles(ox,oy){
  if(!bubblesShowing()){if(bubbleBox.childElementCount)bubbleBox.replaceChildren();bubbleSig='';return;}
  const z=map.getZoom(),b=map.getBounds(),d=dims(),W=b.getWest(),E=b.getEast(),S=b.getSouth(),N=b.getNorth();
  const sig=[z.toFixed(3),W.toFixed(5),N.toFixed(5),d.w,d.h,ox.toFixed(1),oy.toFixed(1),bubbleTipState()].concat(BUBBLE_LAYERS.map(id=>bubbleLayer(id)&&state.visible[id]?COUNTS[id].at:'-')).join('|');
  if(sig===bubbleSig)return;bubbleSig=sig;
  const pl=(N-S)*0.2,pw=(E-W)*0.2,inside=(lat,lon)=>lat>S-pl&&lat<N+pl&&lon>W-pw&&lon<E+pw;
  // 4.67.4 (Rob 14:11: "beverley ... 139 buses in the bubble. I zoomed in and there wasnt any"): the street map only gives
  // the names in the pieces of map it has loaded, so near the edge of the screen a big place (Hull) could be missing and its
  // buses went to the next name along (Beverley). The page's own list of main places is always added, and a square only
  // joins a place close enough to be its own (bubbleReach); further out it is a bubble of its own.
  const fromMap=(window.TPA_MAP_PLACES?.()||[]).filter(p=>inside(p.lat,p.lon)),known=new Set(fromMap.map(p=>p.name));
  let places=fromMap.length?fromMap.concat(POI.filter(([name])=>!known.has(name)).map(([name,lon,lat])=>({name,lon,lat,city:false,rank:10})).filter(p=>inside(p.lat,p.lon)))
    :POI.map(([name,lon,lat])=>({name,lon,lat,city:true})).filter(p=>inside(p.lat,p.lon));
  places=bubblePlaces(places,z);
  const groups=new Map();
  for(const id of BUBBLE_LAYERS){if(!bubbleLayer(id)||!state.visible[id])continue;const c=COUNTS[id];
    for(const [r,col,n] of c.c){const lat=(r+0.5)*c.cell,lon=(col+0.5)*c.cell;if(!inside(lat,lon)||!(n>0)||!inUK(lon,lat))continue;
      let best=nearestPlace(lat,lon,places),near='';
      // 4.70.0 (Rob chose 17 Nov 19:11): a square too far from any place to be its own is "near" the nearest one
      if(best&&kmApart(lat,lon,best.lat,best.lon)>bubbleReach(z)){near=best.name;best=null;}
      const key=best?best.name+'|'+best.lon.toFixed(3)+'|'+best.lat.toFixed(3):'g'+Math.floor(lat/0.5)+'_'+Math.floor(lon/0.5);
      let g=groups.get(key);
      if(!g){g={name:best?best.name:'',near,city:!!best?.city,rank:best?.rank||99,lat:best?best.lat:0,lon:best?best.lon:0,wl:0,wn:0,ww:0,bus:0,rail:0};groups.set(key,g);}
      g[id]+=n;g.wl+=lat*n;g.wn+=lon*n;g.ww+=n;}}
  const list=[...groups.values()].sort((a,b)=>(b.bus+b.rail)-(a.bus+a.rail)),gapX=isPhone()?120:132,gapY=isPhone()?52:46,kept=[];
  for(const g of list){
    if(!g.name){g.lat=g.wl/g.ww;g.lon=g.wn/g.ww;}
    const [x,y]=pixel(g.lon,g.lat);g.x=x-ox;g.y=y-oy;
    const near=kept.find(k=>Math.abs(k.x-g.x)<gapX&&Math.abs(k.y-g.y)<gapY);
    if(near){near.bus+=g.bus;near.rail+=g.rail;near.wl+=g.wl;near.wn+=g.wn;near.ww+=g.ww;
      // 4.67.1: a city names it; 4.67.2 (17 Nov 13:25, live: Leeds and Bradford joined as "Bradford"): of two cities, the
      // more important one on the street map (its own rank: Leeds before Bradford)
      if(g.name&&moreImportant(g,near)){near.name=g.name;near.city=g.city;near.rank=g.rank;near.lat=g.lat;near.lon=g.lon;near.x=g.x;near.y=g.y;}
      continue;}
    kept.push(g);}
  // 4.83.0 (Rob ticked "One bubble per name": "near Edinburgh" showed 3 times at the UK view): bubbles with the same name
  // within 120 km of each other are one bubble (the bigger keeps its place; a tap goes to the middle of all of them)
  const merged=[],byTitle=new Map();
  for(const g of kept){const t=g.name||(g.near?'near '+g.near:'');
    const c0=g.ww>0?[g.wl/g.ww,g.wn/g.ww]:[g.lat,g.lon],k=t?(byTitle.get(t)||[]).find(o=>kmApart(c0[0],c0[1],o.c[0],o.c[1])<=120):null;
    if(k){k.g.bus+=g.bus;k.g.rail+=g.rail;k.g.wl+=g.wl;k.g.wn+=g.wn;k.g.ww+=g.ww;continue;}
    if(t){if(!byTitle.has(t))byTitle.set(t,[]);byTitle.get(t).push({g,c:c0});}
    merged.push(g);}
  const out=[];
  for(const g of merged){
    if(g.x<-80||g.y<-40||g.x>d.w+80||g.y>d.h+40)continue;
    const words=bubbleWords(g);if(!words.length)continue;
    const btn=document.createElement('button');btn.type='button';btn.className='town-bubble'+(g.bus+g.rail>=100?' big':'');
    btn.style.transform=`translate(${g.x.toFixed(1)}px,${g.y.toFixed(1)}px) translate(-50%,-50%)`;
    const title=g.name||(g.near?'near '+g.near:'');
    if(title)btn.append(el('strong',g.name?'':'near',title));
    btn.append(el('span','',words.join(' · ')));
    btn.setAttribute('aria-label',(title?title+': ':'')+words.join(' and ')+'. Zoom in to see each one.');
    btn.title='Zoom in to see each one';
    btn.onclick=ev=>{ev.stopPropagation();bubbleTipDone();stopFollow();map.setView(g.ww>0?[g.wl/g.ww,g.wn/g.ww]:[g.lat,g.lon],bubbleZoom());};
    out.push(btn);}
  // 4.68.0 (tester, 17 Nov 16:09: "I had to try clicking the label to discover what it did"; Rob chose a one-time tip):
  // the first time bubbles show (welcome closed), a short tip under the biggest one on screen, once per browser
  if(out.length&&bubbleTipState()==='show'){
    const top=kept.find(g=>g.x>=0&&g.y>=0&&g.x<=d.w&&g.y<=d.h&&bubbleWords(g).length);
    if(top){BUBBLE_TIP.shown=true;try{localStorage.setItem(BUBBLE_TIP_KEY,'1');}catch(_){}
      clearTimeout(BUBBLE_TIP.timer);BUBBLE_TIP.timer=setTimeout(bubbleTipDone,12000);
      // 4.70.0 (Rob chose 17 Nov 19:11; seen 16:20: the tip under Hull sat over Hessle): above the town when another
      // bubble is just below it
      const below=kept.some(k=>k!==top&&Math.abs(k.x-top.x)<170&&k.y>top.y&&k.y-top.y<100);
      const tip=el('div','bubble-tip'+(below?' above':''));tip.setAttribute('role','note');
      tip.style.transform=below?`translate(${top.x.toFixed(1)}px,${(top.y-30).toFixed(1)}px) translate(-50%,-100%)`
        :`translate(${top.x.toFixed(1)}px,${(top.y+30).toFixed(1)}px) translate(-50%,0)`;
      const x=el('button','bubble-tip-close','×');x.type='button';x.setAttribute('aria-label','Close tip');
      x.onclick=ev=>{ev.stopPropagation();bubbleTipDone();};
      tip.append(el('span','','Tap a town to see its buses and trains'),x);out.push(tip);}}
  bubbleBox.replaceChildren(...out);
}
const BUBBLE_TIP_KEY='tpa-bubble-tip-seen',BUBBLE_TIP={shown:false,done:false,timer:0};
function bubbleTipState(){
  if(BUBBLE_TIP.done)return 'done';
  if(BUBBLE_TIP.shown)return 'show';
  if(!$('tpa-welcome')?.classList.contains('hidden'))return 'wait';    // not under the welcome screen
  try{if(localStorage.getItem(BUBBLE_TIP_KEY)){BUBBLE_TIP.done=true;return 'done';}}catch(_){}
  return 'show';
}
function bubbleTipDone(){if(BUBBLE_TIP.done)return;BUBBLE_TIP.done=true;clearTimeout(BUBBLE_TIP.timer);
  try{localStorage.setItem(BUBBLE_TIP_KEY,'1');}catch(_){}bubbleSig='';paint();}
const BUS_TILES_MAX_AGE_S=180;
async function busTileFrame(){
  const R=window.TPA_BUS_REPLAY;if(!R)throw Error('no replay');
  const slot=Math.floor(serverNow()/10000);
  const meta=await tileJSON('/tiles/bus/meta.json?r='+slot,4000,false);
  // 4.69.1 (Rob 17 Nov 17:28: "loading the map and getting greyed out buses"): at the evening peak (21,400 buses) the bus
  // engine sometimes takes 30-80 s for a round [measured 17:30], so files over 60 s old greyed every bus. The buses keep
  // moving on their own tracks meanwhile; they only grey after 3 minutes without new files (4 since their download).
  if(!meta||meta.v!==1||!validNum(meta.updatedAt)||serverNow()/1000-meta.updatedAt>BUS_TILES_MAX_AGE_S)throw Error('tiles old');
  const iso=s=>new Date(s*1000).toISOString(),got=meta.fetchedAt||meta.updatedAt;
  const frame={component:'bus',contractVersion:'1.0',health:['ready','stale','unavailable'].includes(meta.health)?meta.health:'stale',
    observedAt:iso(got),receivedAt:iso(got),expiresAt:iso(got+BUS_TILES_MAX_AGE_S+60),nextPollAfterMs:10000,attribution:String(meta.attribution||'').slice(0,256),
    groupStacks:true,entities:[]};
  if(meta.notice)frame.notice=String(meta.notice).slice(0,120);
  BUS_TILES.current=Date.parse(frame.expiresAt)>=serverNow();   // 4.69.1: what the page will treat as current (not greyed)
  if(bubbleSkips('bus'))return frame;   // 4.67.0: in town bubbles
  let w=-12.5,s=49,e=4.5,n=61.8;
  if(map&&typeof map.getBounds==='function'){const b=map.getBounds();w=b.getWest();s=b.getSouth();e=b.getEast();n=b.getNorth();}
  if(n-s>meta.maxSpan){frame.notice='Zoom in to see buses';return frame;}
  const small=n-s<=meta.smallBelowSpan,size=small?meta.small:meta.big,dir=small?'s':'b';
  // 4.79.0 (Rob 18 Nov 04:41, live: "When i just move around the map by dragging, each of the new buses to my screen then
  // fetches its information, causing pauses"): the page counts a strip a fifth of the view wide around the screen as
  // already fetched (fetchedBox, currentBox(0.2)), but buses were only ever collected ON the screen. So after a short drag
  // the buses in the newly shown strip were missing until the next 10 s check, then popped in. Buses are now collected for
  // the view plus that same strip (trains already were: viewBox), their route lines arrive before they are on screen, and
  // frame.covered tells the page exactly what was collected. Buses on screen always come first.
  let W=w,S=s,E=e,N=n;
  if(map&&typeof map.getBounds==='function'){const b=map.getBounds().pad(0.2);W=b.getWest();S=b.getSouth();E=b.getEast();N=b.getNorth();}
  const tileSpan=(s0,w0,n0,e0)=>{const r0=Math.floor(s0/size[0]),r1=Math.floor(n0/size[0]),c0=Math.floor(w0/size[1]),c1=Math.floor(e0/size[1]);return [r0,r1,c0,c1,(r1-r0+1)*(c1-c0+1)];};
  let span=tileSpan(S,W,N,E);
  if(span[4]>80){W=w;S=s;E=e;N=n;span=tileSpan(s,w,n,e);}   // a very wide screen: just the view, as before
  const [r0,r1,c0,c1,count]=span;
  if(count>80)throw Error('too many tiles');
  const asks=[];for(let r=r0;r<=r1;r++)for(let c=c0;c<=c1;c++)asks.push(tileJSON(`/tiles/bus/${dir}/${r}_${c}.json?r=${slot}`,6000,true));
  const items=new Map();for(const t of await Promise.all(asks))if(Array.isArray(t))for(const it of t)if(it&&typeof it.i==='string'&&!items.has(it.i))items.set(it.i,it);
  // 4.60.0 (bus 0.20.0, owner 14 Nov 00:34): each bus carries its own delay (d) - just far enough behind its reports
  const now=serverNow()/1000,delay0=validNum(meta.delay)?meta.delay:90,dOf=it=>validNum(it.d)&&it.d>=0&&it.d<=600?it.d:delay0;
  const inView=q=>q[1]>=w&&q[1]<=e&&q[2]>=s&&q[2]<=n,inArea=q=>q[1]>=W&&q[1]<=E&&q[2]>=S&&q[2]<=N;
  const cy=(s+n)/2,cx=(w+e)/2,chosen=[];let onScreen=0;
  for(const it of items.values()){const p=Array.isArray(it.p)?it.p:[];if(!p.length)continue;const last=p[p.length-1];
    if(!inUK(last[1],last[2]))continue;   // 4.83.0
    const recent=now-dOf(it)-120,followed=!!(follow&&follow.comp==='bus'&&it.i===follow.id);
    const seen=inView(last)||p.some(q=>q[0]>=recent&&inView(q))||followed;   // 4.64.2: never drop the followed bus
    if(!seen&&!(inArea(last)||p.some(q=>q[0]>=recent&&inArea(q))))continue;
    if(seen)onScreen++;
    chosen.push([(seen?0:1e9)+(last[2]-cy)**2+(last[1]-cx)**2,it]);}   // on screen first, then the strip around it
  chosen.sort((a,b)=>a[0]-b[0]);
  const max=Number.isInteger(meta.maxPerView)?meta.maxPerView:400;
  if(onScreen>max&&!frame.notice)frame.notice=`Showing nearest ${max} · zoom in for all`;
  frame.covered=chosen.length>max?{w,s,e,n}:{w:W,s:S,e:E,n:N};   // the strip was cut short: only the screen counts as had
  const pick=chosen.slice(0,max).map(x=>x[1]);
  // route lines for the street view (each fetched once, then kept): wait a little for new ones, never long
  const lines=new Map();
  if(small&&meta.build){const want=[...new Set(pick.filter(it=>typeof it.s==='string'&&Array.isArray(it.r)).map(it=>it.s))];
    await Promise.race([Promise.all(want.map(sid=>busLine(meta.build,sid).then(sh=>lines.set(sid,sh)))),new Promise(ok=>setTimeout(ok,2500))]);}
  const t0=BUS_MOTION.mode?performance.now():0;if(BUS_MOTION.mode)BUS_MOTION.now.clear();
  let n1Plan=null;                                   // 4.63.0 test addresses only (BUS_N1.on false on every other address)
  // 4.72.2 (Rob 18 Nov 00:04, X47 at Beverley: "this bus moved 3 times around the same area"): town level (city tiles, zoom 13)
  // kept the replay ~1.5 min behind while street level showed N1 "now", so a bus jumped between the two at every zoom change.
  // N1 now runs at town level too: city tiles carry each bus's reports (no route line), so N1 uses a route line it already
  // knows for that route (borrowed), else goes to the newest report - about "now", the same place at every zoom.
  if(BUS_N1.on&&n1Ready()){const a=performance.now();try{n1Plan=n1Frame(now,pick,lines);}catch(err){n1Plan=null;console.warn('N1',err);}
    BUS_N1.ms.n1+=performance.now()-a;BUS_N1.frames++;if(BUS_N1.compare){BUS_N1.now.clear();BUS_N1.items=pick;}}
  for(const it of pick){
    const tA=n1Plan?performance.now():0;
    let tr=null,fc=false,fr=false,n1d=false;const sh=small&&it.s?lines.get(it.s):null,kl=validNum(meta.keepLeft)?meta.keepLeft:0,delay=dOf(it);
    if((BUS_MOTION.mode==='c4'||BUS_MOTION.mode==='c3')&&sh&&Array.isArray(it.r)&&typeof R.freshTrack==='function'){
      const pv=BUS_MOTION.prev.get(it.i),ft=R.freshTrack(sh,it.r,now,kl,pv&&pv.s===it.s?pv:null,BUS_MOTION.mode);
      if(ft){tr=ft;fr=true;BUS_MOTION.prev.delete(it.i);BUS_MOTION.prev.set(it.i,{s:it.s,marks:ft.marks,aheads:ft.aheads});
        if(BUS_MOTION.prev.size>3000)BUS_MOTION.prev.delete(BUS_MOTION.prev.keys().next().value);
        const g=it.i.startsWith('bus:TFLO:')?'london':'elsewhere',H=BUS_MOTION.how[g]||(BUS_MOTION.how[g]={});H[ft.first]=(H[ft.first]||0)+1;}}
    if(!BUS_MOTION.mode&&BUS_FORECAST.on&&sh&&Array.isArray(it.f)&&typeof R.forecastTrack==='function'){
      const pv=BUS_FORECAST.prev.get(it.i),ft=R.forecastTrack(sh,it.f,now,kl,pv&&pv.s===it.s?pv.marks:null);
      if(ft){tr=ft;fc=true;BUS_FORECAST.prev.delete(it.i);BUS_FORECAST.prev.set(it.i,{s:it.s,marks:ft.marks});
        BUS_FORECAST.how[ft.how]=(BUS_FORECAST.how[ft.how]||0)+1;
        if(BUS_FORECAST.prev.size>3000)BUS_FORECAST.prev.delete(BUS_FORECAST.prev.keys().next().value);}}
    // 4.82.0 (bus 0.26.0; Rob 18 Nov 06:12 "the buses started moving, then they JUMPED to where they were meant to be";
    // 06:16 "All levels need to be warm"): the town tiles (zoom 12-13, no route line) carry the server's forecast as
    // positions (g), so a bus is at the same place at every zoom from the first second.
    if(!tr&&!BUS_MOTION.mode&&BUS_FORECAST.on&&!sh&&Array.isArray(it.g)&&validNum(it.o)){
      const gt=townPathTrack(it.g,it.o,now,BUS_FORECAST.last.get(it.i));
      if(gt){tr=gt;fc=true;BUS_FORECAST.how['town '+gt.how]=(BUS_FORECAST.how['town '+gt.how]||0)+1;}}
    if(fc&&tr&&Array.isArray(tr.points)&&Array.isArray(tr.times)){BUS_FORECAST.last.delete(it.i);BUS_FORECAST.last.set(it.i,{points:tr.points,times:tr.times});
      if(BUS_FORECAST.last.size>3000)BUS_FORECAST.last.delete(BUS_FORECAST.last.keys().next().value);}
    if(!tr&&sh&&Array.isArray(it.r))tr=R.track(sh,it.r,now,delay,kl);
    // 4.58.0 (bus 0.19.0): a bus not on a timetabled route brings its own way along the roads
    if(!tr&&small&&Array.isArray(it.w)&&it.w.length>=2&&Array.isArray(it.r)){try{tr=R.track(roadShape(it),it.r,now,delay,kl,it.jm);}catch(_){tr=null;}}
    if(!tr)tr=R.straight(it.p,now,delay,it.j===1);
    if(!tr)continue;
    if(n1Plan){                                      // 4.63.0: N1 (and, comparing, C4) from the same read
      BUS_N1.ms.replay+=performance.now()-tA;
      const kl0=validNum(meta.keepLeft)?meta.keepLeft:0;let c4=null;
      if(BUS_N1.compare&&sh&&Array.isArray(it.r)){const b=performance.now();
        const pv=BUS_N1.c4prev.get(it.i);c4=R.freshTrack(sh,it.r,now,kl0,pv&&pv.s===it.s?pv:null,'c4');
        if(c4){BUS_N1.c4prev.delete(it.i);BUS_N1.c4prev.set(it.i,{s:it.s,marks:c4.marks,aheads:c4.aheads});
          if(BUS_N1.c4prev.size>3000)BUS_N1.c4prev.delete(BUS_N1.c4prev.keys().next().value);}
        BUS_N1.ms.c4+=performance.now()-b;}
      const b=performance.now(),n1=n1Track(n1Plan[it.i],kl0,it.i===BUS_N1.focus);BUS_N1.ms.n1+=performance.now()-b;
      if(BUS_N1.compare)BUS_N1.now.set(it.i,{london:it.i.startsWith('bus:TFLO:'),kind:sh&&Array.isArray(it.r)?'route':(Array.isArray(it.w)?'road':'step'),
        s:it.s||'',replay:tr,c4,n1,how:n1?n1.how:null,d:delay});
      if(BUS_N1.show==='n1'&&n1){tr=n1;fr=n1.predictive;n1d=true;}
      else if(BUS_N1.show==='c4'&&c4){tr=c4;fr=true;}
    }
    if(BUS_MOTION.mode){const london=it.i.startsWith('bus:TFLO:'),kind=sh&&Array.isArray(it.r)?'route':(Array.isArray(it.w)?'road':'step');
      BUS_MOTION.now.set(it.i,{london,kind,marks:fr?tr.marks:null,r:!fr&&kind==='route'?it.r:null,d:delay,s:it.s||''});
      const k=(london?'london':'elsewhere')+' '+kind+(fr?' fresh':' replay');BUS_MOTION.buses[k]=(BUS_MOTION.buses[k]||0)+1;BUS_MOTION.points+=tr.points.length;}
    const r6=v=>Math.round(v*1e6)/1e6,pos=tr.position;
    frame.entities.push({id:it.i,label:busTitle(it.l),badge:it.b||null,
      position:{lon:r6(pos[0]),lat:r6(pos[1]),headingDeg:null},quality:now-it.o>300?'stale':'observed',observedAt:iso(it.o),
      motion:{mode:'timed',points:tr.points.map(q=>[r6(q[0]),r6(q[1])]),times:tr.times,delaySeconds:fc||fr||n1d?0:delay,forecast:fc,fresh:fr}});}
  if(BUS_MOTION.mode){BUS_MOTION.frames++;BUS_MOTION.ms+=performance.now()-t0;}
  return frame;
}
// ---- 4.65.0: published train files (rail 1.6.0; Rob 16 Nov 2026 07:25 "yes that's what I meant, let's proceed") ------
// The load test at 00:14 on 16 Nov was healthy at 250 viewers; at 500 the train engine was the limit, because it still
// worked out an answer for every viewer. Now, like the buses, it writes /tiles/rail/meta.json and squares s/<row>_<col>.json
// (street views) and b/<row>_<col>.json (city views) every 10 s; Caddy hands them out. Each item is {e: the train exactly
// as the old answer gave it, k: [lat, lon] where it reported, x: [s, w, n, e] the stretch it is on, tt: 1 timetable train}.
// This picks the same trains the old answer did for the same view (viewBox), nearest first, timetable trains filling any
// room. ?railtiles=0 is the way back; files missing or over a minute old fall back to the old answer by themselves.
const RAIL_TILES={on:new URLSearchParams(location.search).get('railtiles')!=='0',used:false,last:null};
window.TPA_RAIL_TILES=()=>({on:RAIL_TILES.on,used:RAIL_TILES.used,last:RAIL_TILES.last});   // last: the view, trains and notice of the last read (tests)
async function railTileFrame(){
  const slot=Math.floor(serverNow()/10000);
  const meta=await tileJSON('/tiles/rail/meta.json?r='+slot,4000,false);
  if(!meta||meta.v!==1||!validNum(meta.updatedAt)||serverNow()/1000-meta.updatedAt>60)throw Error('tiles old');
  const iso=t=>new Date(t*1000).toISOString(),at=meta.updatedAt;
  const frame={component:'rail',contractVersion:'1.0',health:['ready','stale','unavailable'].includes(meta.health)?meta.health:'stale',
    observedAt:iso(at),receivedAt:iso(at),expiresAt:iso(at+90),nextPollAfterMs:Math.round(1000*(validNum(meta.every)?meta.every:10)),
    attribution:String(meta.attribution||'').slice(0,256),groupStacks:true,entities:[]};
  if(meta.scheduled===true)frame.scheduled=true;
  if(typeof meta.noticeDetail==='string'&&meta.noticeDetail)frame.noticeDetail=meta.noticeDetail;
  const {w,s,e,n}=viewBox();
  if(bubbleSkips('rail'))return frame;   // 4.67.0: in town bubbles
  if(!validNum(meta.maxSpan)||n-s>meta.maxSpan){frame.notice='Zoom in to see trains';RAIL_TILES.last={box:[s,w,n,e],ids:[],notice:frame.notice};return frame;}
  if(meta.notice)frame.notice=String(meta.notice).slice(0,120);
  const small=n-s<=meta.smallBelowSpan,size=small?meta.small:meta.big,dir=small?'s':'b';
  if(!Array.isArray(size)||!validNum(size[0])||!validNum(size[1])||size[0]<=0||size[1]<=0)throw Error('meta');
  const r0=Math.floor(s/size[0]),r1=Math.floor(n/size[0]),c0=Math.floor(w/size[1]),c1=Math.floor(e/size[1]);
  if((r1-r0+1)*(c1-c0+1)>80)throw Error('too many tiles');
  const asks=[];for(let r=r0;r<=r1;r++)for(let c=c0;c<=c1;c++)asks.push(tileJSON(`/tiles/rail/${dir}/${r}_${c}.json?r=${slot}`,6000,true));
  const items=new Map();
  for(const t of await Promise.all(asks))if(Array.isArray(t))for(const it of t)
    if(it&&it.e&&typeof it.e.id==='string'&&Array.isArray(it.k)&&validNum(it.k[0])&&validNum(it.k[1])&&!items.has(it.e.id))items.set(it.e.id,it);
  const cy=(s+n)/2,cx=(w+e)/2,live=[],booked=[];
  const fid=follow&&follow.comp==='rail'?follow.id:null;
  for(const it of items.values()){
    const [lat,lon]=it.k,x=Array.isArray(it.x)&&it.x.length===4?it.x:null;
    const inside=(s<=lat&&lat<=n&&w<=lon&&lon<=e)||(x&&x[0]<=n&&x[2]>=s&&x[1]<=e&&x[3]>=w);
    if(!inside&&it.e.id!==fid)continue;        // as the old answer: where it reported, or the stretch it is on, in view
    (it.tt===1?booked:live).push([(lat-cy)**2+(lon-cx)**2,it.e]);}
  live.sort((a,b)=>a[0]-b[0]);booked.sort((a,b)=>a[0]-b[0]);
  const max=Number.isInteger(meta.maxPerView)?meta.maxPerView:400;
  frame.entities=live.slice(0,max).map(q=>q[1]);
  frame.entities.push(...booked.slice(0,Math.max(0,max-frame.entities.length)).map(q=>q[1]));
  if(!frame.entities.length&&meta.listening===true)frame.notice='Listening · trains appear as they report at stations';
  else if(live.length>max&&!meta.notice)frame.notice=`Showing nearest ${max} · zoom in for all`;
  RAIL_TILES.last={box:[s,w,n,e],ids:frame.entities.map(q=>q.id),notice:frame.notice||''};
  return frame;
}
window.TPA_BUS_TILES=()=>({on:BUS_TILES.on,used:BUS_TILES.used,lines:BUS_TILES.lines.size,current:BUS_TILES.current});
function deselectComponent(id){
  if(state.selected?.component===id){state.selected=null;selected.classList.add('hidden');}
}
// 4.0.10: each engine is asked only for what is on screen (plus a margin).
// 4.28.0 (launch list A1): rounded out to the server's shared squares (as serve_hq.area_query), with the square size,
// so viewers looking at the same place ask exactly the same question - one answer can then serve them all.
const SLOT_MS=4000;   // = serve_hq.SHARE_SECONDS
// 4.65.0: the area the page asks about (padded by a fifth, snapped outward to a grid) - shared by the per-viewer answer
// and the published train files, so both pick exactly the same trains and say "Zoom in" at the same point
function viewBox(){
  let w=-12.5,s=49,e=4.5,n=61.8;
  if(map&&typeof map.getBounds==='function'){const b=map.getBounds().pad(0.2);w=b.getWest();s=b.getSouth();e=b.getEast();n=b.getNorth();}
  s=clamp(s,-85,85);n=clamp(n,-85,85);w=clamp(w,-180,180);e=clamp(e,-180,180);
  const g=Math.min(4,Math.max(1/1024,2**Math.floor(Math.log2(Math.max(n-s,1e-4)/4)))),gl=g*2;
  s=Math.max(-85,Math.floor(s/g)*g);n=Math.min(85,Math.ceil(n/g)*g);w=Math.max(-180,Math.floor(w/gl)*gl);e=Math.min(180,Math.ceil(e/gl)*gl);
  return {w,s,e,n,g};
}
function viewQuery(){
  const {w,s,e,n,g}=viewBox(),f=v=>v.toFixed(4);
  // and the 4-second time slot by the server's clock: a new question every slot, so a kept answer is never old,
  // however long a cache in between might keep it (Cloudflare's free plan may keep things for hours)
  return `west=${f(w)}&south=${f(s)}&east=${f(e)}&north=${f(n)}&cell=${g}&t=${Math.floor(serverNow()/SLOT_MS)}`;
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
    const stamp=Math.floor(serverNow()/1000/o.refreshSeconds);   // 4.15.3: the server's 10-minute periods, as the road engine keeps them
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
let refetchTimer=0;const hasItems=Object.create(null);
function refetchLiveView(){clearTimeout(refetchTimer);refetchTimer=setTimeout(()=>{
  const v=currentBox(0);
  // 4.47.0 (owner 11 Nov 20:02: "When i zoom out and back in, the roadworks that were there have to reload"): a layer that
  // draws a picture overlay AND map items (traffic: the flow colours plus roadworks signs) is asked again when the view
  // changes too. Before, only overlay-free layers were, so the signs waited for traffic's next 60-second check.
  for(const id of IDS)if(state.live[id]&&!state.parked[id]&&(!state.frames[id]?.overlay||hasItems[id])&&(state.status[id]==='ready'||state.status[id]==='stale')){
    // 4.1.0: nothing to fetch if the last answer already covered this whole view.
    const b=fetchedBox[id];
    if(v&&b&&b.complete&&v.w>=b.w&&v.e<=b.e&&v.s>=b.s&&v.n<=b.n&&!(BUBBLE_SKIPPED[id]&&!bubbleLayer(id)))continue;
    getComponent(id);
  }
},300);}   // 4.1.13: was 0.8 s - a new area's vehicles are asked for sooner
// 4.79.0 (Rob 18 Nov 04:41: dragging "doesnt flow well"): during a long drag the new area stayed empty until the mouse was
// let go. Buses and trains (ready-made files, no work for the server) are now collected while the map is still being
// dragged, once the view leaves what was collected - at most about once a second, one ask per layer at a time.
let dragCheckAt=0;
function refreshWhileDragging(){
  const t=performance.now();if(t-dragCheckAt<900)return;dragCheckAt=t;
  const v=currentBox(0);if(!v)return;
  for(const id of ['bus','rail']){
    if(!state.live[id]||state.parked[id]||!(state.status[id]==='ready'||state.status[id]==='stale'))continue;
    if((seqSent[id]||0)>(seqDone[id]||0)&&t-(lastFetch[id]||0)<3000)continue;   // already asking
    const b=fetchedBox[id];
    if(b&&b.complete&&v.w>=b.w&&v.e<=b.e&&v.s>=b.s&&v.n<=b.n)continue;
    getComponent(id);
  }
}
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
  // 4.94.0 (found building the planes page, 19 Nov): a layer switched off was still asked for whenever the map moved,
  // was dragged or the tab came back (all those ask every live layer). Now a hidden layer is never asked; switching it
  // on asks at once (the toggle), then its usual polling carries on.
  if(!state.visible[id]){ASK_WHEN_SHOWN.add(id);return;}
  const seq=seqSent[id]=(seqSent[id]||0)+1,box=currentBox(0.2);lastFetch[id]=Date.now();
  try{
    const asked=performance.now();
    // 4.56.0 (Plan B, owner 13 Nov 2026): buses come from ready-made tile files when the server publishes them
    // (the page moves them itself); the old per-visitor answer stays as the way back (?tiles=0, or tiles missing/old).
    let raw=null;
    if(id==='bus'&&BUS_TILES.on){try{raw=await busTileFrame();BUS_TILES.used=true;}catch(_){raw=null;BUS_TILES.used=false;}}
    // 4.65.0: trains the same way (rail 1.6.0); the old per-visitor answer stays as the way back (?railtiles=0, or files missing/old)
    if(id==='rail'&&RAIL_TILES.on){try{raw=await railTileFrame();RAIL_TILES.used=true;}catch(_){raw=null;RAIL_TILES.used=false;}}
    const f=checkFrame(id,raw||await requestBounded(`/api/v4/components/${id}/snapshot?${viewQuery()}${id==='air'&&WORLD_PLANES?'&world=1':''}`,1200000,6000));   // 4.85.0
    {const a=SPEED.ask[id]||(SPEED.ask[id]={n:0,sum:0,last:0});a.last=performance.now()-asked;a.n++;a.sum+=a.last;if(f.entities.length){speedMark(id+' shown');hasItems[id]=true;}}
    if(seq<(seqDone[id]||0))return;
    seqDone[id]=seq;
    // 4.79.0: a tile frame says exactly what it collected (busTileFrame: the view and the strip round it)
    const cov=raw&&raw.covered&&[raw.covered.w,raw.covered.s,raw.covered.e,raw.covered.n].every(validNum)?raw.covered:null;
    fetchedBox[id]=box?{...(cov?{w:cov.w,s:cov.s,e:cov.e,n:cov.n}:box),complete:!f.notice}:null;
    if(state.live[id])schedulePoll(id,f.pollMs);
    noteNewFrame(f,serverNow(),freshStart[id]);freshStart[id]=false;
    // Once a new manifest has removed a component, ignore late responses.
    if(state.status[id]==='not_installed'||state.parked[id])return;
    if(f.health==='not_installed'){delete state.frames[id];deselectComponent(id);}
    else {state.frames[id]=f;liveTitle(id,f);}
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
  toggle.disabled=parked;toggle.checked=!parked&&layerOnAtStart(id);state.visible[id]=toggle.checked;
  row.classList.toggle('parked',parked);
  row.setAttribute('aria-disabled',parked?'true':'false');
  row.title=parked?'Coming soon':'';
}
async function discover(){
  if(discovering)return;discovering=true;
  try{
    const data=await requestBounded('/api/v4/manifest',32768,5000);
    if(data.contractVersion!=='1.0'||!Array.isArray(data.components)||data.components.length>8)throw Error('manifest-contract');
    // 4.39.0: the version label shows the real HQ version (it said 4.32.0 since then: written by hand in the page)
    {const v=typeof data.hq==='string'&&/^\d+\.\d+\.\d+/.exec(data.hq),st=document.querySelector('.preview-stamp');if(v&&st&&st.textContent!=='HQ '+v[0]+' · TEST VERSION')st.textContent='HQ '+v[0]+' · TEST VERSION';}
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
        (state.announced||(state.announced={}))[id]=true;   // 4.26.0: said hello - on the welcome board even before its first answer
        // Start all five requests independently rather than awaiting each in series.
        if(state.status[id]==='not_installed')state.status[id]='unavailable';
        if(!(state.live[id]&&pollTimers[id]&&Date.now()-(lastFetch[id]||0)<25000))requests.push(getComponent(id));
      }else if(entry?.status==='stale'||entry?.status==='unavailable'){
        state.status[id]=entry.status==='stale'?'stale':'unavailable';deselectComponent(id);
      }else{
        state.status[id]='not_installed';delete state.frames[id];deselectComponent(id);
      }
    }
    paint();layersKnown=true;
    await Promise.allSettled(requests);
  }catch(_){
    layersKnown=true;    // 4.7.1: no layer list - nothing more to wait for
    // Manifest outage: display prior frames as stale, never silently claim live.
    for(const id of IDS)if(state.status[id]==='ready'||state.status[id]==='stale'){
      state.status[id]='unavailable';deselectComponent(id);
    }
  }finally{discovering=false;paint();}
}
function search(query){let q=query.trim().toLowerCase(),out=$('target-list');out.replaceChildren();stationSearch(q);if(!q)return;/* 4.81.0 (Rob 18 Nov 06:05): no repeat of the box's own hint; the empty list folds away */const places=POI.filter(([name])=>name.toLowerCase().includes(q)).slice(0,10).map(([name,lon,lat])=>({name,kind:'Place',action:()=>gotoPlace(lon,lat,11,name)}));const targets=listEntities().filter(e=>e.position&&(e.label.toLowerCase().includes(q)||e.id.toLowerCase().includes(q))).slice(0,10).map(e=>({name:e.label,kind:e.component.toUpperCase(),action:()=>{gotoPlace(e.position.lon,e.position.lat,13,e.label);selectEntity(e);}}));if(q==='uk'||'united kingdom'.includes(q))places.unshift({name:'United Kingdom overview',kind:'Region',action:fitUK});const items=[...places,...targets];if(!items.length){const p=document.createElement('p');p.className='empty';const wait=q.length>=2&&stationSearchable();p.classList.toggle('searching',wait);p.textContent=wait?'Searching…':'Nothing found by that name.';out.append(p);return;}for(const item of items){const b=document.createElement('button');b.type='button';b.className='search-result';const name=document.createElement('strong');name.textContent=item.name;const type=document.createElement('small');type.textContent=item.kind;b.append(name,type);b.onclick=()=>{item.action();$('search').value=item.name;closeResults();};out.append(b);}}
// 4.52.0 (owner 12 Nov 17:25: open a board by "search by name"): stations from the stations engine, by name or
// three-letter code, at the top of the results. Choosing one goes there and opens its board.
let stationAsk=0,stationTimer=0;
// 4.54.0 (owner 12 Nov 20:22, bus stations found by "Search by name"): railway stations first, then bus stations.
// 4.71.0 (tester 17 Nov 19:30: "Heathrow" first said "Nothing found by that name", then three stations appeared; and the
// list stayed open after a choice): "Searching…" until the stations have answered, and the list closes once one is chosen.
function stationSearchable(){return ['busstations','stations'].some(c=>state.live[c]&&!state.parked[c]&&state.visible[c]);}
function closeResults(){stationAsk++;clearTimeout(stationTimer);$('target-list').replaceChildren();}
// 4.73.0 (17 Nov 22:45: "Hull Paragon" found nothing - the station list calls it "Hull"): people type the name they know,
// often with extra words. If the full words find nothing, try again without "station"/"railway"/"interchange"-type words,
// then with the last word dropped, one at a time (never below one real word); "kings" also tried as "king's".
function searchVariants(q){const out=[q],add=v=>{v=v.replace(/\s+/g,' ').trim();if(v.length>=2&&!out.includes(v))out.push(v);};
  add(q.replace(/\b([a-z]{2,}[^s\W])s\b/g,"$1's"));   // "kings cross" -> "king's cross"
  const plain=q.replace(/\b(railway|rail|train|bus|coach)?\s*(station|stn|interchange)\b/g,' ').replace(/\b(the|railway)\b/g,' ');add(plain);
  let w=plain.replace(/\s+/g,' ').trim().split(' ');while(w.length>1){w=w.slice(0,-1);if(w.join(' ').length>=3)add(w.join(' '));}
  return out.slice(0,5);}
function stationSearch(q){clearTimeout(stationTimer);const ask=++stationAsk;
  if(q.length<2)return;
  stationTimer=setTimeout(async()=>{
    const out=$('target-list');let found=false;
    for(const [comp,word] of [['busstations','Bus station'],['stations','Station']]){
    if(!state.live[comp]||state.parked[comp]||!state.visible[comp])continue;
    try{let d=null;
      for(const v of searchVariants(q)){const r=await fetch('/api/v4/components/'+comp+'/search?q='+encodeURIComponent(v.slice(0,60)));if(!r.ok)break;
        d=await r.json();if(ask!==stationAsk)return;if(Array.isArray(d.results)&&d.results.length)break;}
      if(!d||!Array.isArray(d.results)||!d.results.length)continue;
      found=true;out.querySelector('p.empty')?.remove();out.querySelectorAll('.station-result.'+comp).forEach(n=>n.remove());
      const first=out.firstChild;
      for(const x of d.results.slice(0,8)){
        if(!x||typeof x.id!=='string'||typeof x.label!=='string'||!Number.isFinite(x.lat)||!Number.isFinite(x.lon))continue;
        const b=document.createElement('button');b.type='button';b.className='search-result station-result '+comp;
        const name=document.createElement('strong');name.textContent=x.label.slice(0,80);
        const type=document.createElement('small');type.textContent=word+(typeof x.badge==='string'?' · '+x.badge:'');b.append(name,type);
        b.onclick=()=>{const e={component:comp,id:x.id,label:x.label.slice(0,80),badge:typeof x.badge==='string'?x.badge:null,variant:comp==='stations'?'station':'bus',
            quality:'observed',observedAt:new Date(serverNow()).toISOString(),position:{lon:x.lon,lat:x.lat,headingDeg:null}};
          gotoPlace(x.lon,x.lat,15,x.label);$('search').value=x.label;selectEntity(e);closeResults();};
        out.insertBefore(b,first);}
    }catch(_){}}
    if(ask===stationAsk&&!found){const p=out.querySelector('p.empty.searching');if(p){p.classList.remove('searching');p.textContent='Nothing found by that name.';}}
  },250);}
for(const id of IDS){$(id+'-toggle').checked=state.visible[id];$(id+'-toggle').addEventListener('change',()=>{state.visible[id]=$(id+'-toggle').checked;
  if(!PLANES&&!KIDS)try{localStorage.setItem('tpa-layer-on-'+id,state.visible[id]?'1':'0');}catch(_){}   // 4.94.0: every layer
  if(state.visible[id]&&ASK_WHEN_SHOWN.delete(id)&&!state.parked[id])getComponent(id);   // 4.94.0: first ask, now it's wanted
  paint();});}
// 4.94.0: the planes page has no other layers to switch
if(PLANES)for(const l of document.querySelectorAll('.layer-toggle'))if(l.dataset.component!=='air')l.style.display='none';
if(PLANES){const s=document.getElementById('search');if(s)s.placeholder='Place or flight…';
  // 4.95.0: "Vehicles in view" counts aircraft only (the other tiles stayed as dashes)
  for(const id of ['sea','rail','bus']){const c=document.getElementById(id+'-count')?.parentElement;if(c)c.style.display='none';}
  const g=document.querySelector('.stats-grid');if(g)g.classList.add('planes-only');}
function clock(){$('localclock').textContent=new Date().toLocaleTimeString('en-GB',{hour12:false});}clock();setInterval(clock,1000);
$('home-button').onclick=home;$('zoom-in').onclick=()=>zoom(1);$('zoom-out').onclick=()=>zoom(-1);$('menu-button').onclick=()=>setMenu(!$('sidebar').classList.contains('open'));$('sidebar-close').onclick=()=>setMenu(false);$('sidebar-backdrop').onclick=()=>setMenu(false);$('search').addEventListener('input',e=>search(e.target.value));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('sidebar').classList.remove('open');selected.classList.add('hidden');}});
window.addEventListener('resize',()=>{if(map)map.invalidateSize();paint();});
// Fallback drag/scroll remains usable only if MapLibre is unavailable.
root.addEventListener('wheel',e=>{if(map)return;e.preventDefault();zoom(e.deltaY<0?1:-1);},{passive:false});
root.addEventListener('pointerdown',e=>{if(map||e.button!==0||e.target.closest('button,input'))return;pan={x:e.clientX,y:e.clientY};});
root.addEventListener('pointermove',e=>{if(map||!pan)return;const dx=e.clientX-pan.x,dy=e.clientY-pan.y;pan={x:e.clientX,y:e.clientY};const c=merc(state.lon,state.lat,state.z),p=inverse(c[0]-dx,c[1]-dy,state.z);state.lon=clamp(p.lon,UK_BOX.west,UK_BOX.east);state.lat=clamp(p.lat,UK_BOX.south,UK_BOX.north);paint();});
root.addEventListener('pointerup',()=>{pan=null;});root.addEventListener('pointercancel',()=>{pan=null;});
root.tabIndex=0;root.addEventListener('keydown',e=>{if(map)return;if(e.key==='+'||e.key==='=')zoom(1);if(e.key==='-')zoom(-1);});
async function diagnose(){try{const r=await fetch('/hq-map-status',{cache:'no-store'});if(!r.ok)return;const data=await r.json();let error=data.upstreamErrors||{};let message=error['blocked-map-path']?.code||error.openfreemap?.code||error['maplibre-library']?.code||data.browser?.lastCode||'map-resource-unavailable';showNotice('OpenFreeMap map unavailable ('+message+'). UK geographic fallback active.',true);}catch(_){showNotice('Map connection unavailable. UK geographic fallback active.',true);}}
function beginMap(){
if(!window.L||!L.map){state.renderer='fallback';reportBrowser('library-missing','leaflet','Leaflet absent');diagnose();return;}
try{
  // Historical behaviour: Leaflet owns zoom/pan/markers; MapLibre only paints Liberty.
  // 4.38.0: dragging stops at the edge of the UK box (only where the map library offers it; a map without it still works)
  const ukBounds=typeof L.latLngBounds==='function'?L.latLngBounds([UK_BOX.south,UK_BOX.west],[UK_BOX.north,UK_BOX.east]):null;
  map=L.map('vector-map',{zoomControl:false,preferCanvas:true,minZoom:3,maxZoom:18,
    ...(ukBounds?{maxBounds:ukBounds,maxBoundsViscosity:1.0}:{}),
    attributionControl:false}).setView([HOME.lat,HOME.lon],HOME.z);
  if(START_UK){fitUK(false);$('map-place').textContent='UNITED KINGDOM';const hb=$('home-button')?.querySelector('span');if(hb)hb.textContent='HOME: UK';}   // 4.76.0
  // 4.81.0 (Rob 18 Nov 06:05: "a refresh on a page should take you back to where your last place was in the browser"): the
  // view is kept for this browser tab only (sessionStorage), so a refresh goes back there; a new visit still starts on the UK.
  try{const v=JSON.parse(sessionStorage.getItem('tpa-view')||'null');
    if(v&&validNum(v.lat)&&validNum(v.lon)&&validNum(v.z)&&v.z>=3&&v.z<=18){map.setView([v.lat,v.lon],v.z,{animate:false});$('map-place').textContent='';}}catch(_){}
  map.on('moveend',()=>{try{const c=map.getCenter();sessionStorage.setItem('tpa-view',JSON.stringify({lat:+c.lat.toFixed(5),lon:+c.lng.toFixed(5),z:map.getZoom()}));}catch(_){}});
  // 4.38.0: zooming out stops once the whole UK box is on screen (a phone stops one step further out than a PC).
  const fitMinZoom=()=>{try{if(!ukBounds||typeof map.getBoundsZoom!=='function')return;minZ=clamp(map.getBoundsZoom(ukBounds,false),3,10);if(typeof map.setMinZoom==='function')map.setMinZoom(minZ);}catch(_){}};
  fitMinZoom();if(typeof map.on==='function')map.on('resize',fitMinZoom);
  UK_LIMITS=ukBounds;document.querySelector('.sidebar .brand-mark')?.addEventListener('click',eggClick);   // 4.85.0: the plane Easter egg
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
  map.on('movestart',()=>{if(!followPanning)SPEED.mapFrom=performance.now();});
  map.on('zoomend',zoomAnimEnd);
  map.on('zoomend',applyOverlayOpacity);
  map.on('moveend',()=>{if(!followPanning)refetchLiveView();});
  map.on('drag',refreshWhileDragging);   // 4.79.0
  map.on('dragstart',()=>{if(follow)stopFollow();});   // 4.10.0: grabbing the map stops following
  // 4.90.0 (Rob 18 Nov 18:45: "when a dialogue/info box is open for any vehicle, can it auto close when you click on the map
  // area, unless its doing a follow me?"): a click on empty map closes the box, unless following. Leaflet sends no click after
  // a drag, and vehicles sit on their own layer so clicking one never reaches the map. Waits 0.3 s so a double-click (zoom) keeps it.
  let mapClickTimer=0;
  map.on('click',()=>{clearTimeout(mapClickTimer);if(!state.selected||follow)return;
    mapClickTimer=setTimeout(()=>{if(!state.selected||follow)return;selected.classList.add('hidden');state.selected=null;paint();},300);});
  map.on('dblclick',()=>clearTimeout(mapClickTimer));
  // 4.1.2: while the map moves, markers follow it instantly (no gliding lag).
  if(typeof map.on==='function')map.on('movestart zoomstart',()=>{if(followPanning)return;mapMoving=true;for(const p of pins.values())p.b.classList.remove('glide');});
  if(typeof map.on==='function')map.on('moveend zoomend',()=>{if(followPanning)return;mapMoving=false;paint();});
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
const MAP_PIECE_ERRORS=[];Object.defineProperty(window,'TPA_MAP_PIECE_ERRORS',{value:()=>MAP_PIECE_ERRORS.slice(),writable:false});
window.TPA_HQ_VECTOR_READY=()=>{speedMark('street map drawn');ready=true;state.renderer='leaflet-maplibre';state.mapErrors=0;
  document.body.classList.add('vector-ready');notice.classList.add('hidden');syncView();};
// 4.71.2 (Rob 17 Nov 2026 21:42-21:50: "the cover thing" - part of the street map blank, then the plain outline map over
// everything, right after a refresh until it happened again). Any street map error, even one piece of map that failed to
// download, put the plain outline map on top of the street map for the rest of the visit. Once the street map has drawn,
// a failed piece is only noted: the street map stays, and MapLibre fills the gap from the pieces it has.
window.TPA_HQ_VECTOR_ERROR=(message)=>{state.mapErrors++;const t=String(message||'Unknown source error');
  if(state.renderer==='leaflet-maplibre'&&('street map drawn' in SPEED.first)){MAP_PIECE_ERRORS.push({at:new Date().toISOString(),message:t.slice(0,160)});if(MAP_PIECE_ERRORS.length>20)MAP_PIECE_ERRORS.shift();return;}
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
// 4.75.0 (Rob 18 Nov 2026 00:44: "a suggestions box that people can either anonymously write, or be directed to our email"):
// a short form, saved on our server with no name needed (POST /api/v4/suggest), plus an "email us" link for a reply.
// Spam guard: a hidden field people never fill in, and the form must have been open a few seconds.
function buildSuggest(){
  const {back,card}=makeDialog('tpa-suggest','Got an idea? Tell us');card.classList.add('suggest-card');
  const intro=el('p','suggest-text','Ideas, likes, dislikes or something that looks wrong: we read everything. No name needed.');
  const kind=el('select','suggest-kind');kind.setAttribute('aria-label','What is it about?');
  for(const [v,t] of [['idea','An idea'],['problem','Something looks wrong'],['other','Something else']]){const o=el('option','',t);o.value=v;kind.append(o);}
  const text=el('textarea','suggest-box');text.maxLength=1000;text.rows=5;text.placeholder='Type here…';text.setAttribute('aria-label','Your suggestion');
  const trap=el('input','suggest-trap');trap.type='text';trap.tabIndex=-1;trap.autocomplete='off';trap.setAttribute('aria-hidden','true');trap.name='website';
  const status=el('p','suggest-status');status.setAttribute('role','status');
  const send=el('button','tip-button suggest-send','Send');send.type='button';
  let opened=0;
  send.onclick=async()=>{const t=text.value.trim();if(t.length<3){status.textContent='Please type a little more first.';return;}
    send.disabled=true;status.textContent='Sending…';
    try{const r=await fetch('/api/v4/suggest',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({kind:kind.value,text:t.slice(0,1000),website:trap.value,openMs:Math.round(performance.now()-opened)})});
      if(r.ok){text.value='';status.textContent='Thank you! It has reached us.';}
      else status.textContent=r.status===429?'Thanks, we have plenty from you for now. Please try again later.':'Sorry, that didn\'t send. Please try again, or email us.';}
    catch(_){status.textContent='Sorry, that didn\'t send. Please try again, or email us.';}
    send.disabled=false;};
  const mail=el('a','tpa-link-button suggest-mail','Or email us if you would like a reply');mail.href='mailto:enquiries@tpalive.co.uk?subject=TPA%20suggestion';
  const close=el('button','tpa-link-button support-close','Close');close.type='button';close.onclick=()=>closeDialog(back);
  card.append(intro,kind,text,trap,send,status,mail,close);
  back.addEventListener('tpa-open',()=>{opened=performance.now();});
  return back;
}
function makeDialog(id,title){
  const back=el('div','tpa-dialog-back hidden');back.id=id;
  const card=el('div','tpa-dialog');card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');
  const h=el('h2','tpa-dialog-title',title);h.id=id+'-title';card.setAttribute('aria-labelledby',h.id);
  card.append(h);back.append(card);document.body.append(back);
  back.addEventListener('click',e=>{if(e.target===back)closeDialog(back);});
  return {back,card};
}
function openDialog(back){lastFocus=document.activeElement;back.classList.remove('hidden');
  for(const list of prepLists)if(back.contains(list.box))list.t0=performance.now();   // 4.7.2: the tiles play again each time
  const enter=back._enter,wait=Number(back.dataset.enterAfter||0);
  if(enter&&wait>0){clearTimeout(back._enterTimer);enterWaiting(enter,wait);
    back._enterTimer=setTimeout(()=>{enterReady(enter);enter.focus();},wait*1000);}
  back.querySelector('.tpa-primary:not(.waiting),button:not([disabled]),a')?.focus();}
function closeDialog(back){back.classList.add('hidden');if(back.id==='tpa-welcome'){try{localStorage.setItem(WELCOME_KEY,'1');}catch(_){}nightNote();}lastFocus?.focus?.();}
// 4.76.0 (Rob 18 Nov 03:47, chose "a friendly night note"): in the small hours (UK time) most buses and trains have stopped,
// so a friendly line says so once, when the welcome screen closes
let nightNoted=false;
function ukHour(){try{return Number(new Date().toLocaleString('en-GB',{timeZone:'Europe/London',hour:'2-digit',hour12:false}))%24;}catch(_){return new Date().getHours();}}
function nightNote(){if(nightNoted||KIDS)return;const h=ukHour();if(!(h>=0&&h<5))return;nightNoted=true;
  setTimeout(()=>!state.selected&&toast("It's the small hours, so most buses and trains are tucked up for the night. Night buses are still running in the big cities, and planes come and go all night.",12000),1200);}
document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.tpa-dialog-back:not(.hidden)').forEach(closeDialog);});
function buildCredits(list){
  const {back,card}=makeDialog('tpa-credits','Data sources & credits');
  const items=el('ul','credit-list');
  for(const c of (Array.isArray(list)?list.slice(0,20):[])){
    const layer=str(c?.layer,40),text=str(c?.text,400);if(!layer||!text)continue;
    if(c?.kidsOnly===true&&!KIDS)continue;   // 4.21.2 (owner 5 Nov 20:17): Kids sounds credited only in Kids mode, where they play
    const li=el('li','credit-item');li.append(el('strong','',layer));
    const status=str(c?.status,30);if(status)li.append(el('span','credit-status',status));
    li.append(el('p','',text));
    const url=safeLink(c?.url);if(url){const a=el('a','credit-link',url.replace(/^https:\/\//,'').replace(/\/$/,''));a.href=url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);}
    items.append(li);
  }
  const close=el('button','tpa-primary','Close');close.type='button';close.onclick=()=>closeDialog(back);
  card.append(items,close);return back;
}
// 4.57.0 (owner 13 Nov 2026 20:32, voluntary donations): a small "Support TPA" entry always at the top of the menu
// opens this panel - why contributions help and one clear link. Only ever opened by the visitor: no prompts, no
// timers, nothing over the live map. Text from site-config.json "support"; the link is the tip link.
function buildSupport(tip,sup){
  const url=safeLink(tip?.url);if(!url)return null;
  const {back,card}=makeDialog('tpa-support',str(sup?.title,40)||'Support TPA');card.classList.add('support-card');
  const body=el('div','support-body');
  const pic=typeof tip?.picture==='string'&&/^\/assets\/[a-z0-9-]{1,40}\.(?:jpg|png)$/.test(tip.picture)?tip.picture:'';
  if(pic){const img=el('img','support-picture');img.src=pic;img.alt=str(tip?.pictureAlt,80)||'';img.width=88;img.height=88;img.loading='lazy';body.append(img);}
  body.append(el('p','support-text',str(sup?.text,400)||'TPA is free for everyone. If you find it useful, a small contribution helps cover hosting, servers, data services and development.'));
  card.append(body);
  const a=el('a','tip-button support-button','☕ '+(str(sup?.button,60)||'Buy Rob a coffee (or Geoff some treats)'));a.href=url;a.target='_blank';a.rel='noopener noreferrer';
  card.append(a);
  const note=str(sup?.note,200);if(note)card.append(el('small','support-note',note));
  const close=el('button','tpa-link-button support-close','Close');close.type='button';close.onclick=()=>closeDialog(back);
  card.append(close);
  return back;
}
// 4.66.0 (first-impressions visit, 17 Nov: "I wasn't sure how long I needed to wait"): the button is on screen from the
// start, saying "Getting ready…" and filling up over the wait, then becomes Let's go!
function enterWaiting(enter,wait){
  enter.classList.add('waiting');enter.disabled=true;enter.removeAttribute('aria-hidden');
  const fill=el('span','enter-fill');fill.style.animationDuration=wait+'s';
  enter.replaceChildren(fill,el('span','enter-text','Getting ready…'));}
function enterReady(enter){enter.classList.remove('waiting');enter.disabled=false;enter.textContent=enter.dataset.label||"Let's go!";}
function buildWelcome(w,tip,credits){
  const {back,card}=makeDialog('tpa-welcome',str(w?.title,80)||'Welcome to TPA');
  card.classList.add('welcome-card');
  card.prepend(welcomeParade());
  const sub=str(w?.subtitle,120);if(sub)card.append(el('p','tpa-dialog-sub',sub));
  // 4.7.2 (owner 1 Nov 16:00: "two pages long ... needs to be just one page, smaller text, it's not why they're
  // here anyway"): two columns on wide screens - what TPA is and Let's go! on the left, coffee and small print
  // on the right - and smaller type throughout.
  const cols=el('div','welcome-cols'),main=el('div','welcome-main'),side=el('div','welcome-side');cols.append(main,side);card.append(cols);
  for(const line of (Array.isArray(w?.lines)?w.lines.slice(0,8):[])){const t=str(line,300);if(t)main.append(el('p','tpa-dialog-line',t));}
  // 4.75.0 (Rob 18 Nov 00:47): a quick note, on phones and tablets only
  if(smallScreen())main.append(el('p','welcome-device-note',"TPA works best on a computer, where there's room to see everything. Phones and tablets work well too, with a little less detail on screen, and we're making them even better."));
  for(const sec of (Array.isArray(w?.sections)?w.sections.slice(0,4):[])){
    const head=str(sec?.heading,60),items=(Array.isArray(sec?.items)?sec.items.slice(0,6):[]).map(t=>str(t,240)).filter(Boolean);
    if(!head||!items.length)continue;
    const box=el('div','welcome-section');box.append(el('h3','',head));const ul=el('ul','');
    for(const t of items)ul.append(el('li','',t));box.append(ul);main.append(box);}
  const enter=el('button','tpa-primary',str(w?.enterLabel,40)||'Enter the map');enter.type='button';enter.onclick=()=>closeDialog(back);
  // 4.7.0: the streams pulse and turn green; Let's go! appears after enterAfterSeconds (5) from opening
  const ready=el('div','welcome-ready');ready.append(el('small','prep-caption','Preparing streams'),makeFlapBoard());   // 4.26.0
  const wait=Number.isFinite(w?.enterAfterSeconds)?clamp(w.enterAfterSeconds,0,10):0;
  enter.dataset.label=enter.textContent;
  if(wait>0){enterWaiting(enter,wait);back.dataset.enterAfter=String(wait);}
  back._enter=enter;
  main.append(ready,enter);
  const tipBtn=tipControl(tip);
  if(tipBtn){const tipBox=el('div','tip-box');
    const pic=typeof tip?.picture==='string'&&/^\/assets\/[a-z0-9-]{1,40}\.(?:jpg|png)$/.test(tip.picture)?tip.picture:'';
    if(pic){const img=el('img','tip-picture');img.src=pic;img.alt=str(tip?.pictureAlt,80)||'';img.width=72;img.height=72;img.loading='eager';tipBox.append(img);}
    const said=str(tip?.text,240);if(said)tipBox.append(el('p','tip-text',said));
    tipBox.append(tipBtn);const note=str(tip?.note,160);if(note)tipBox.append(el('small','',note));side.append(tipBox);}
  const small=(Array.isArray(w?.smallPrint)?w.smallPrint.slice(0,6):[]).map(t=>str(t,300)).filter(Boolean);
  if(small.length){const sp=el('div','welcome-small');for(const t of small)sp.append(el('p','',t));side.append(sp);}
  const more=el('button','tpa-link-button','Data sources & credits');more.type='button';more.onclick=()=>{closeDialog(back);openDialog(credits);};
  side.append(more);return back;
}
// 4.1.15 (owner 31 Oct 23:47: "no mention of bods data"): the map's own credit line names the live data
// sources of the layers being shown, from site-config.json's short credits (data, not code).
const shortCredit=Object.create(null);
function liveCredit(){const box=$('map-credit');if(!box)return;
  const live=IDS.filter(id=>state.live[id]&&state.visible[id]&&!state.parked[id]&&(state.status[id]==='ready'||state.status[id]==='stale'))
    .map(id=>shortCredit[layerName(id)]).filter((c,i,all)=>c&&all.indexOf(c)===i);   // 4.54.0: each source once (bus stops and stations share theirs)
  const text='© OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors'+(live.length?' · Live data: '+live.join(' · '):'');
  if(box.textContent!==text){box.textContent=text;box.title=text;}
  // 4.66.0: the small credits button - the street map's credit, and TomTom's while the traffic is on the map
  const t=$('credit-toggle');if(t&&!KIDS){const road=state.live.road&&state.visible.road&&!state.parked.road&&(state.status.road==='ready'||state.status.road==='stale');
    const short='© OpenStreetMap'+(road?' · © TomTom':'');if(t.dataset.short!==short){t.dataset.short=short;t.textContent=short;}}}   // 4.65.1: the whole line on hover (PC: one line, "..." if long)
// ---- 4.11.0 Kids mode ------------------------------------------------------------------------------------
const KID_WORD={bus:['bus','buses'],rail:['train','trains'],air:['plane','planes']};
let kidsMuted=false;try{kidsMuted=localStorage.getItem('tpa-kids-muted')==='1';}catch(_){}
// 4.11.1 (owner 1 Nov 19:10: "it only needs to say 'I'm going to Beverley!' or something"): the sound's own word
// in big letters, then one short line. Only the short line is read out, after the sound has finished.
function kidsLine(e){
  const label=String(e.label||'');
  if(!state.live[e.component])return "I'm a pretend one!";
  if(e.component==='bus'){
    const to=(label.match(/ to (.+)$/)||[])[1];
    return to?"I'm going to "+to.replace(/\s*\(.*\)$/,'').trim()+'!':(e.badge?"I'm the number "+e.badge+' bus!':"I'm a bus!");
  }
  if(e.component==='rail'){
    if(/cancelled/.test(label))return "I'm having a rest today.";
    const next=(label.match(/next: ([^·]+)/)||[])[1],left=(label.match(/departed ([^·]+)/)||[])[1],at=(label.match(/(?:arrived at|passed|terminated at) ([^·]+)/)||[])[1];
    if(next)return "I'm going to "+next.trim()+'!';
    if(left)return "I've just left "+left.trim()+'!';
    if(at)return "I'm at "+at.trim()+'!';
    return "I'm a train!";
  }
  if(e.component==='air'){
    if(/on the ground/.test(label))return "I'm at the airport!";
    const ft=Number(((label.match(/([\d,]+) ft/)||[])[1]||'').replace(/,/g,''));
    if(!ft)return "I'm flying in the sky!";
    if(ft<3000)return "I'm coming in to land!";
    if(ft<15000)return "I'm up in the clouds!";
    return "I'm way above the clouds!";
  }
  return 'Hello!';
}
// 4.11.1 (owner 1 Nov 19:10: "Not sure on the computer voice though"): use the most natural British voice the
// device has (Edge's "Natural" voices, Maisie being a child's voice; Chrome's Google UK English; Apple's UK
// voices), and a separate button turns the voice off while keeping the sounds.
let kidsVoiceOff=false;try{kidsVoiceOff=localStorage.getItem('tpa-kids-voice')==='off';}catch(_){}
const VOICE_LIKE=[/Maisie.*Natural/i,/Libby.*Natural/i,/Sonia.*Natural/i,/en-GB.*Natural|Natural.*en-GB/i,/Google UK English Female/i,/Serena|Kate|Stephanie|Martha/i,/Google UK English/i,/Daniel/i];
function kidsVoice(){
  try{const all=speechSynthesis.getVoices()||[];
    for(const re of VOICE_LIKE){const v=all.find(x=>re.test(x.name)&&/^en/i.test(x.lang||'en'));if(v)return v;}
    return all.find(x=>/^en-GB/i.test(x.lang))||all.find(x=>/^en/i.test(x.lang))||null;}catch(_){return null;}
}
let kidsSayTimer=0;
function kidsSay(text,afterSeconds){
  clearTimeout(kidsSayTimer);
  if(kidsMuted||kidsVoiceOff||!window.speechSynthesis||typeof SpeechSynthesisUtterance!=='function')return;
  kidsSayTimer=setTimeout(()=>{try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(text);const v=kidsVoice();if(v)u.voice=v;
    u.lang=v?.lang||'en-GB';u.rate=0.95;u.pitch=1.1;speechSynthesis.speak(u);}catch(_){}},Math.round(Math.min(2.6,Math.max(0,afterSeconds||0))*1000));
}
function kidsBubble(e,sound){
  state.selected=e;
  const lead=el('p','kids-lead',(sound||{bus:'Beep beep',rail:'Choo choo',air:'Whoosh'}[e.component]||'Hello')+'!');
  const text=el('p','kids-say',kidsLine(e));
  const again=el('button','kids-again','🔊 Again!');again.type='button';
  again.onclick=()=>{const name=window.TPAKidsSounds?.play(e.component,kidsMuted);if(name)lead.textContent=name+'!';text.textContent=kidsLine(e);kidsSay(text.textContent,window.TPAKidsSounds?.last?.seconds);};
  const fb=document.createElement('button');fb.type='button';fb.className='follow-button';
  fb.onclick=()=>{if(isFollowing(e))stopFollow();else startFollow(e);};
  const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close');
  close.onclick=()=>{selected.classList.add('hidden');state.selected=null;stopFollow();paint();};
  const row=el('div','kids-buttons');row.append(again,fb);
  selected.classList.add('kids-bubble');selected.replaceChildren(lead,text,row,close);selected.classList.remove('hidden');
  if(!isFollowing(e))stopFollow();updateFollowUI();paint();
  return text.textContent;
}
function kidsTap(e,b){
  const name=window.TPAKidsSounds?.play(e.component,kidsMuted)||null;
  b.classList.remove('kid-boing');void b.offsetWidth;b.classList.add('kid-boing');
  kidsSay(kidsBubble(e,name),window.TPAKidsSounds?.last?.seconds);
}
function kidsCountText(){
  const parts=[];
  for(const id of ['bus','rail','air']){const n=kidsSeen[id]||0;if(n>0)parts.push(n+' '+KID_WORD[id][n===1?0:1]);}
  if(!parts.length)return 'Zoom in to find buses, trains and planes!';
  return 'I can see '+(parts.length>1?parts.slice(0,-1).join(', ')+' and '+parts[parts.length-1]:parts[0])+'!';
}
function buildKidsBar(){
  const bar=el('div','kids-bar');bar.setAttribute('aria-label','Kids buttons');
  for(const [id,pic,word] of [['bus','🚌','Buses'],['rail','🚂','Trains'],['air','✈️','Planes']]){
    const b=el('button','kids-toggle',pic);b.type='button';b.dataset.layer=id;b.setAttribute('aria-label',word);b.title=word;
    const box=$(id+'-toggle');
    const sync=()=>{b.setAttribute('aria-pressed',String(!!box?.checked));};
    b.onclick=()=>{if(box){box.click();sync();window.TPAKidsSounds?.play(id,kidsMuted);}};
    box?.addEventListener('change',sync);sync();bar.append(b);
  }
  const mute=el('button','kids-toggle kids-mute','');mute.type='button';
  const syncMute=()=>{mute.textContent=kidsMuted?'🔇':'🔊';mute.setAttribute('aria-label',kidsMuted?'Sound off':'Sound on');mute.setAttribute('aria-pressed',String(!kidsMuted));};
  mute.onclick=()=>{kidsMuted=!kidsMuted;try{localStorage.setItem('tpa-kids-muted',kidsMuted?'1':'0');}catch(_){}if(kidsMuted)try{speechSynthesis.cancel();}catch(_){}syncMute();};
  syncMute();bar.append(mute);
  const voice=el('button','kids-toggle kids-voice','🗣️');voice.type='button';
  const syncVoice=()=>{voice.setAttribute('aria-label',kidsVoiceOff?'Talking off':'Talking on');voice.title=voice.getAttribute('aria-label');voice.setAttribute('aria-pressed',String(!kidsVoiceOff));};
  voice.onclick=()=>{kidsVoiceOff=!kidsVoiceOff;try{localStorage.setItem('tpa-kids-voice',kidsVoiceOff?'off':'on');}catch(_){}if(kidsVoiceOff)try{speechSynthesis.cancel();}catch(_){}syncVoice();};
  syncVoice();bar.append(voice);
  const count=el('div','kids-count');count.setAttribute('role','status');count.id='kids-count';
  // grown-ups: hold for 3 seconds to leave (little fingers won't do it by accident)
  const grown=el('button','kids-grownups','');grown.type='button';grown.id='kids-grownups';
  grown.append(el('span','kids-grownups-fill'),el('span','kids-grownups-text','Grown-ups: hold'));
  let holdTimer=0;
  const start=ev=>{ev.preventDefault();grown.classList.add('holding');clearTimeout(holdTimer);holdTimer=setTimeout(()=>{location.href='/';},3000);};
  const stop=()=>{grown.classList.remove('holding');clearTimeout(holdTimer);};
  grown.addEventListener('pointerdown',start);for(const t of ['pointerup','pointerleave','pointercancel'])grown.addEventListener(t,stop);
  grown.addEventListener('keydown',ev=>{if((ev.key==='Enter'||ev.key===' ')&&!ev.repeat)start(ev);});grown.addEventListener('keyup',stop);
  root.append(bar,count,grown);
  setInterval(()=>{const t=kidsCountText();if(count.textContent!==t)count.textContent=t;},1000);
}
function buildKidsWelcome(w,credits){
  const {back,card}=makeDialog('tpa-kids-welcome','TPA Kids');
  card.classList.add('kids-welcome');
  const pics=el('div','kids-welcome-pics');
  for(const id of ['bus','rail','air']){const s=el('span','kid-art kids-wave');s.innerHTML=KID_ART[id];pics.append(s);}
  const hello=el('p','kids-hello','Tap a bus, a train or a plane to say hello!');
  const go=el('button','tpa-primary kids-go',"Let's play!");go.type='button';
  go.onclick=()=>{window.TPAKidsSounds?.unlock();window.TPAKidsSounds?.play('bus',kidsMuted);closeDialog(back);};
  const small=el('p','kids-small','For grown-ups: these are real buses, trains and planes, shown as closely as the open data allows. '+
    'For fun only - not for planning journeys or safety. Nothing about you is collected: no cookies, adverts or tracking.');
  if(credits){const cb=el('button','kids-credits','Sounds & data credits');cb.type='button';cb.onclick=()=>{closeDialog(back);openDialog(credits);};small.append(' ',cb);}
  card.append(pics,hello,go,small);
  return back;
}
if(KIDS)buildKidsBar();
async function loadSiteConfig(){
  let cfg;
  try{cfg=await requestBounded('/data/site-config.json',65536,5000);}catch(_){return;}
  if(!cfg||typeof cfg!=='object'||cfg.configVersion!==1)return;
  for(const c of Array.isArray(cfg.credits)?cfg.credits:[]){const l=str(c?.layer,40).toLowerCase(),sh=str(c?.short,40);if(l&&sh)shortCredit[l]=sh;}
  liveCredit();
  REPORT_ON=cfg.report===true&&!USER_VIEW;      // 4.12.0: the "Report this" button, for the testing phase; 4.20.0: full (full) page only
  const credits=buildCredits(cfg.credits);
  let welcome=null;try{welcome=buildWelcome(cfg.welcome,cfg.tip,credits);}catch(_){}   // 4.5.0: a welcome problem never costs the side menu
  const about=el('section','panel about-panel');about.setAttribute('aria-label','About TPA');
  const title=el('div','section-title');title.append(el('h2','','ABOUT TPA'),el('span','micro','FREE, ALWAYS'));
  const creditsBtn=el('button','tpa-link-button','Data sources & credits');creditsBtn.type='button';creditsBtn.id='credits-button';creditsBtn.onclick=()=>openDialog(credits);
  const welcomeBtn=welcome?el('button','tpa-link-button','Show welcome screen'):null;
  if(welcomeBtn){welcomeBtn.type='button';welcomeBtn.id='welcome-button';welcomeBtn.onclick=()=>openDialog(welcome);}
  // 4.57.0: the coffee link now lives in the Support TPA panel (entry at the top of the menu), not twice in the menu
  const support=KIDS?null:buildSupport(cfg.tip,cfg.support);
  // 4.74.0 (launch, Rob 18 Nov 2026 01:24-01:26): the plain-English privacy page
  const privacyLink=KIDS?null:el('a','tpa-link-button','Privacy');if(privacyLink){privacyLink.href='/privacy';privacyLink.target='_blank';privacyLink.rel='noopener';privacyLink.id='privacy-link';}
  // 4.75.0 (tidy-up after launch, Rob 18 Nov 01:50): the help guide and the suggestions box
  const helpLink=KIDS?null:el('a','tpa-link-button','How to use TPA');if(helpLink){helpLink.href='/help';helpLink.target='_blank';helpLink.rel='noopener';helpLink.id='help-link';}
  const suggest=KIDS?null:buildSuggest();
  const suggestBtn=suggest?el('button','tpa-link-button','Got an idea? Tell us'):null;
  if(suggestBtn){suggestBtn.type='button';suggestBtn.id='suggest-button';suggestBtn.onclick=()=>{suggest.dispatchEvent(new Event('tpa-open'));openDialog(suggest);};}
  about.append(...[title,support?null:tipControl(cfg.tip),helpLink,suggestBtn,creditsBtn,welcomeBtn,privacyLink].filter(Boolean));
  $('sidebar').insertBefore(about,document.querySelector('.sidebar-footer'));
  if(support){const entry=el('button','support-entry','☕ '+(str(cfg.support?.entry,30)||'Support TPA'));entry.type='button';entry.id='support-button';
    entry.setAttribute('aria-haspopup','dialog');entry.onclick=()=>openDialog(support);
    const brand=document.querySelector('#sidebar .brand');if(brand)brand.after(entry);else $('sidebar').prepend(entry);}
  let seen=false;try{seen=localStorage.getItem(WELCOME_KEY)==='1';}catch(_){}
  if(cfg.welcome?.everyVisit===true)seen=false;      // 4.7.0 (owner 1 Nov 15:22): the welcome screen every visit
  if(KIDS){try{openDialog(buildKidsWelcome(cfg.welcome,credits));}catch(_){}return;}
  if(welcome&&cfg.welcome?.enabled===true&&!seen)openDialog(welcome);
  startPreparing(!(welcome&&cfg.welcome?.enabled===true&&!seen));   // 4.6.0: returning visitors get the short panel
}
if(!KIDS)ownIconsInPage();
if(!PLANES){setTimeout(loadGeoff,3000);setInterval(loadGeoff,600000);}   // 4.89.0: Find Geoff (a tiny list, every 10 minutes)
for(const b of document.querySelectorAll('.air-filter button')){
  b.setAttribute('aria-pressed',String(b.dataset.air===airShow));
  b.addEventListener('click',()=>{airShow=b.dataset.air;
    for(const o of document.querySelectorAll('.air-filter button'))o.setAttribute('aria-pressed',String(o===b));paint();});}
paint();discover();beginMap();setInterval(discover,30000);
// 4.0.11: also check every 10 s, but only while a live layer is still connecting.
setInterval(()=>{if(IDS.some(id=>state.live[id]&&!state.parked[id]&&state.status[id]!=='ready'))discover();},10000);
loadSiteConfig();
// 4.0.13: one place that opens/closes the phone layers menu and keeps the button state right.
// 4.46.0 (owner 11 Nov 18:58: "can the left hand menu be closable, with arrows to open it again, it takes up a fair chunk of
// the screen for those of us who like a screen with things moving about on it"): on wider screens a tab on the menu's edge hides
// it (the map takes the whole width) and brings it back; remembered in this browser. Phones keep their own LAYERS menu.
function setMenuHidden(hide,save){
  const app=document.querySelector('.app');if(!app)return;app.classList.toggle('menu-hidden',hide);
  const t=$('menu-tab');if(t){t.textContent=hide?'▶':'◀';t.setAttribute('aria-expanded',hide?'false':'true');
    t.setAttribute('aria-label',hide?'Show the layers menu':'Hide the layers menu');t.title=hide?'Show the menu':'Hide the menu';}
  if(save){try{localStorage.setItem('tpa-menu-hidden',hide?'1':'0');}catch(_){}}
  if(map&&typeof map.invalidateSize==='function')map.invalidateSize({pan:false});
  paint();}
if($('menu-tab')){$('menu-tab').onclick=()=>setMenuHidden(!document.querySelector('.app').classList.contains('menu-hidden'),true);
  let was=false;try{was=localStorage.getItem('tpa-menu-hidden')==='1';}catch(_){}
  if(was)setMenuHidden(true,false);}
function setMenu(open){$('sidebar').classList.toggle('open',open);$('menu-button').setAttribute('aria-expanded',open?'true':'false');}
new MutationObserver(()=>$('menu-button').setAttribute('aria-expanded',$('sidebar').classList.contains('open')?'true':'false')).observe($('sidebar'),{attributes:true,attributeFilter:['class']});
})();
