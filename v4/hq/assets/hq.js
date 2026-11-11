/* TPA v4 HQ only: MapLibre GL JS + OpenFreeMap, same-origin resources.
 * The five transport connections accept versioned JSON *data*, never scripts.
 * No supplier/feed logic, motion algorithms, Admin controls or external URLs.
 */
(()=>{'use strict';
const IDS=Object.freeze(['air','sea','rail','bus','road']);
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
// 4.20.0 (owner 5 Nov 15:26-16:41): the live server makes the main address the user page (data-default-view on the
// page); the owner's full page, with "Report this", is /full (or ?view=full).
const VIEW_ASKED=new URLSearchParams(location.search).get('view');
const FULL_VIEW=!KIDS&&(location.pathname==='/full'||VIEW_ASKED==='full');
const USER_VIEW=KIDS||(!FULL_VIEW&&(location.pathname==='/user'||VIEW_ASKED==='user'||document.documentElement.dataset.defaultView==='user'));
if(KIDS){document.documentElement.classList.add('kids-view');}
if(USER_VIEW){document.documentElement.classList.add('user-view');document.title=KIDS?'TPA Kids':'TPA · live UK transport map';
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
// 4.14.0 (owner 3 Nov 20:35: smaller icons, "smaller when zoomed out"): small at town level, today's size close in
function pinScale(z){return (KIDS?1.6:1)*(z<11.5?0.5:z<12.5?0.56:z<13.5?0.64:z<14.5?0.74:z<15.5?0.86:z<16.5?1:1.1);}
function syncView(){if(map){let c=map.getCenter();state.lon=c.lng;state.lat=c.lat;state.z=map.getZoom();}
  const ps=String(pinScale(state.z));if(markerLayer.style.getPropertyValue('--pin-s')!==ps)markerLayer.style.setProperty('--pin-s',ps);paint();}
function gotoPlace(lon,lat,z,label){stopFollow();if(map)map.setView([lat,lon],z,{animate:false});state.lon=lon;state.lat=lat;state.z=z;selected.classList.add('hidden');state.selected=null;$('map-place').textContent=(label||'SELECTED LOCATION').toUpperCase();$('sidebar').classList.remove('open');paint();}
function home(){gotoPlace(HOME.lon,HOME.lat,HOME.z,'BEVERLEY, EAST YORKSHIRE');toast('Home · Beverley, East Yorkshire');}
function fitUK(){gotoPlace(-3.0,54.55,5.5,'UNITED KINGDOM');}
function zoom(delta){if(map)map.setZoom(clamp(map.getZoom()+delta,minZ,18));else{state.z=clamp(state.z+delta,3,17);paint();}}
function drawFallback(){const {w,h}=dims(),scale=Math.min(2,window.devicePixelRatio||1);if(canvas.width!==Math.round(w*scale)||canvas.height!==Math.round(h*scale)){canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);}ctx.setTransform(scale,0,0,scale,0,0);ctx.clearRect(0,0,w,h);if(!state.geo||ready)return;
ctx.fillStyle='#e8eeea';for(const shape of state.geo.land){if(shape.length<3)continue;ctx.beginPath();shape.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.closePath();ctx.fill();}
ctx.strokeStyle='#86acb1';ctx.lineWidth=1;for(const seg of state.geo.coastlines){ctx.beginPath();seg.forEach(([lon,lat],i)=>{let p=pixel(lon,lat);i?ctx.lineTo(...p):ctx.moveTo(...p);});ctx.stroke();}
for(const [name,lon,lat] of POI){const [x,y]=pixel(lon,lat);if(x<0||x>w||y<0||y>h)continue;if(state.z<7&&!['Beverley','Hull','York','Leeds','London','Edinburgh','Glasgow','Cardiff','Belfast'].includes(name))continue;ctx.font='600 12px system-ui';ctx.strokeStyle='#e8eeea';ctx.lineWidth=3;ctx.strokeText(name,x+5,y-5);ctx.fillStyle='#2b555c';ctx.fillText(name,x+5,y-5);}}
// 4.34.0 (owner 10 Nov 22:32: "a button to filter between normal and just military, or both"); kept in this browser
let airShow=(()=>{try{const v=localStorage.getItem('tpa-air-show');return ['all','normal','military','services'].includes(v)?v:'all';}catch(_){return 'all';}})();
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
  return state.status[id]==='ready'?entities:entities.map(e=>({...e,quality:'stale'}));
});}
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
function iconFor(id){return {air:'✈',sea:'⛴',rail:'🚂',bus:'🚌',road:'≋'}[id]||'•';}
// 4.15.1 (owner 3 Nov 22:01: "get the whole site glittered", all in house): the side menu, the vehicle box, the welcome
// tiles and the logo use TPA's own drawings too. The map picture is ours as well; traffic keeps its plain wave sign.
const UI_PATHS=Object.freeze({map:['M3.5 7.2 11 4l10 3.2L28.5 4v21L21 28.2 11 25l-7.5 3.2ZM10.4 5.6v18.9h1.2V5.6ZM20.4 8.3v18.9h1.2V8.3Z','']});
function ownIcon(id){const pair=ICON_PATHS[id]||UI_PATHS[id];if(!pair)return document.createTextNode(iconFor(id));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('focusable','false');
  svg.setAttribute('aria-hidden','true');svg.setAttribute('class','own-icon');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',pair[0]);path.setAttribute('fill-rule','evenodd');svg.append(path);
  if(pair[1]){const acc=document.createElementNS('http://www.w3.org/2000/svg','path');acc.setAttribute('d',pair[1]);acc.setAttribute('class','acc');svg.append(acc);}
  return svg;}
function ownIconsInPage(){for(const t of document.querySelectorAll('.layer-toggle[data-component]')){const id=t.dataset.component,box=t.querySelector('.layer-icon');
    if(box&&ICON_PATHS[id]){box.replaceChildren(ownIcon(id));}}
  const logo=document.querySelector('.brand-plane');if(logo)logo.replaceChildren(ownIcon('air'));}
// 4.11.0: the kids pictures (our own drawings, with friendly faces). Fixed text, no data inside.
const KID_ART={
  bus:'<svg viewBox="0 0 64 48" focusable="false"><rect x="4" y="5" width="56" height="32" rx="9" fill="#ffc531" stroke="#b77f00" stroke-width="2"/><rect x="9" y="10" width="31" height="12" rx="3" fill="#bfe9ff"/><rect x="44" y="10" width="12" height="15" rx="3" fill="#bfe9ff"/><rect x="5" y="26" width="54" height="3" fill="#e5533d"/><circle cx="47.5" cy="16" r="2.6" fill="#fff"/><circle cx="53" cy="16" r="2.6" fill="#fff"/><circle cx="48" cy="16.4" r="1.4" fill="#222"/><circle cx="53.5" cy="16.4" r="1.4" fill="#222"/><path d="M46 32 q4.5 3.5 9 0" stroke="#7a3b00" stroke-width="2" fill="none" stroke-linecap="round"/><circle cx="17" cy="38" r="6" fill="#333"/><circle cx="17" cy="38" r="2.4" fill="#ccc"/><circle cx="47" cy="38" r="6" fill="#333"/><circle cx="47" cy="38" r="2.4" fill="#ccc"/></svg>',
  rail:'<svg viewBox="0 0 64 48" focusable="false"><circle cx="49" cy="4" r="3" fill="#fff" opacity=".9"/><circle cx="54" cy="2.5" r="2" fill="#fff" opacity=".8"/><rect x="43" y="6" width="8" height="12" rx="2" fill="#3a3a3a"/><rect x="4" y="5" width="20" height="30" rx="4" fill="#2e9e5b" stroke="#1d6b3c" stroke-width="2"/><rect x="8" y="9" width="12" height="10" rx="2" fill="#bfe9ff"/><rect x="22" y="15" width="34" height="20" rx="5" fill="#3cb371" stroke="#1d6b3c" stroke-width="2"/><circle cx="53" cy="25" r="9" fill="#f6ead0" stroke="#1d6b3c" stroke-width="2"/><circle cx="50.5" cy="23" r="1.8" fill="#222"/><circle cx="55.5" cy="23" r="1.8" fill="#222"/><path d="M49.5 28 q3.5 3 7 0" stroke="#7a3b00" stroke-width="1.8" fill="none" stroke-linecap="round"/><circle cx="14" cy="39" r="6" fill="#d93a2b"/><circle cx="31" cy="39" r="6" fill="#d93a2b"/><circle cx="46" cy="39" r="6" fill="#d93a2b"/><circle cx="14" cy="39" r="2" fill="#ffd"/><circle cx="31" cy="39" r="2" fill="#ffd"/><circle cx="46" cy="39" r="2" fill="#ffd"/></svg>',
  air:'<svg viewBox="0 0 64 48" focusable="false"><path d="M7 24 L3 8 L17 21 Z" fill="#ff6b6b" stroke="#c74343" stroke-width="1.5" stroke-linejoin="round"/><ellipse cx="32" cy="26" rx="27" ry="9.5" fill="#fff" stroke="#3d8bd9" stroke-width="2"/><path d="M25 27 L38 43 L45 43 L39 27 Z" fill="#3d8bd9"/><circle cx="20" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="26" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="32" cy="23.5" r="1.9" fill="#9fd3ff"/><circle cx="50" cy="21.5" r="3.2" fill="#fff" stroke="#3d8bd9" stroke-width="1.2"/><circle cx="50.8" cy="21.8" r="1.6" fill="#222"/><path d="M48 29 q4 3 8 0" stroke="#7a3b00" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>'
};
function kidFlip(e){return e.component==='air'&&e.position?.headingDeg!=null&&e.position.headingDeg>180?'scaleX(-1)':'';}
const PIC_ICON=Object.freeze({bus:'/assets/icons/bus.webp',rail:'/assets/icons/train.webp'});   // 4.21.2
// 4.36.0 (owner 11 Nov 00:09-00:11: his own glossy top-down plane icons, "all central to the icon to allow for rotation"):
// the kind (air engine "variant") -> his picture; a kind the page does not know keeps the stand-in drawing. A plane whose
// kind is not known shows the airliner. Balloons stay upright (they point no particular way).
const PLANE_PIC=Object.freeze({airliner:'airliner',widebody:'widebody',turboprop:'turboprop',bizjet:'bizjet',light:'light',
  helicopter:'helicopter',cargo:'cargo',glider:'glider',balloon:'balloon','mil-fighter':'fighter',
  // 4.37.0 (owner 11 Nov 00:32, his second ten): military and emergency services; other military (trainers) the light aircraft
  'mil-tanker':'tanker','mil-transport':'miltransport','mil-helicopter':'milheli','mil-surveillance':'surveillance',military:'light',
  police:'policeheli',ambulance:'ambulanceheli',coastguard:'coastguardheli','police-plane':'policeplane','ambulance-plane':'ambulanceplane',
  'coastguard-plane':'coastguardplane','ground-emer':'icon-emer','ground-gnd':'icon-gnd','ground-twr':'icon-twr'});   // 4.45.0
function planePic(e){if(e.component!=='air')return null;if(e.variant==null)return 'airliner';
  return typeof e.variant==='string'&&Object.hasOwn(PLANE_PIC,e.variant)?PLANE_PIC[e.variant]:null;}
function planeTurn(e){if(e.variant==='ground-twr')return '';
  // 4.45.0: the owner's van and fire engine icons are drawn front-down, so they turn half a turn further
  const extra=e.variant==='ground-emer'||e.variant==='ground-gnd'?180:0;
  return e.variant!=='balloon'&&e.position?.headingDeg!=null?'rotate('+((e.position.headingDeg+extra)%360)+'deg)':'';}
// 4.39.0 (owner 11 Nov 13:04, traffic: incident markers and roadworks happening now): TPA's own road-sign style icons,
// drawn here (no outside pictures): warning triangles for incidents, the red ring for a closed road.
const PLANE_PHOTOS=new Set(['a320','b737','b787','b777','a220','atr72','tutor','prefect','cessna','piper','typhoon','kingair','a330','a350','embraer','robinson','trainer',
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
    const plane=planePic(e);
    if(PIC_ICON[e.component]||plane){const img=document.createElement('img');img.className='pic-icon'+(plane?' plane-pic p-'+plane:'');
      img.src=plane?'/assets/planes/'+plane+'.webp':PIC_ICON[e.component];img.alt='';
      if(plane)img.style.transform=planeTurn(e);
      img.decoding='async';img.draggable=false;img.addEventListener('error',()=>img.replaceWith(svg),{once:true});icon.append(img);}
    else icon.append(svg);
  }else{icon.textContent='≋';}
  if(e.component==='rail'||e.component==='bus'){
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
    for(const row of Array.isArray(d.rows)?d.rows.slice(0,24):[]){
      const line=document.createElement('div');line.className='detail-row';
      const k=document.createElement('span');k.className='detail-label';k.textContent=String(row.label||'').slice(0,30);
      const v=document.createElement('span');v.className='detail-value';v.textContent=String(row.value||'').slice(0,120);
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
    if(typeof d.summary==='string'&&d.summary){const p=document.createElement('p');p.className='detail-summary';p.textContent=d.summary.slice(0,200);trip.push(p);}
    // 4.33.0: what it is doing ("Coming in to land at Leeds Bradford") and how far along ("About halfway: ...")
    for(const [key,cls] of [['doing','detail-doing'],['progress','detail-progress']])
      if(typeof d[key]==='string'&&d[key]){const p=document.createElement('p');p.className=cls;p.textContent=d[key].slice(0,120);trip.push(p);}
    if(stops.length>=2){
      const when=s=>s.expected&&s.expected!==s.time?`${s.time||''} (expected ${s.expected})`:(s.time||'');
      const here=stops.find(s=>s.state==='here'),next=stops.find(s=>s.state==='due');
      const line=document.createElement('p');line.className='detail-next';
      if(here){const b=document.createElement('b');b.textContent='Now at ';line.append(b,here.name);}
      if(next){const b=document.createElement('b');b.textContent=(here?' · ':'')+'Next stop: ';line.append(b,`${next.name}, ${when(next)}`.replace(/, $/,''));}
      if(!here&&!next){const b=document.createElement('b');b.textContent='Journey finished';line.append(b);}
      trip.push(line);
      const head=document.createElement('div');head.className='detail-stops-head';head.textContent='Calling at';trip.push(head);
      const list=document.createElement('ol');list.className='detail-stops';list.setAttribute('aria-label','Calling at');
      for(const s of stops){const li=document.createElement('li');li.className='stop '+s.state;
        const t=document.createElement('span');t.className='stop-time';t.textContent=s.time||'';
        const n=document.createElement('span');n.className='stop-name';n.textContent=s.name.slice(0,80);
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
      const known=typeof d.picture==='string'&&Object.hasOwn(BUS_PICTURES,d.picture),kind=known?d.picture:'sd';
      const img=document.createElement('img');img.src='/assets/buses/'+kind+'.webp';img.alt=known?BUS_PICTURES[kind]:'Picture of a bus (type not known)';img.decoding='async';
      if(!known){box3.classList.add('unknown');const tag=document.createElement('span');tag.className='pic-tag';tag.textContent='Type not known';box3.append(tag);}
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
    box.replaceChildren(...rows);
  }catch(err){}
  // the next stop changes as the vehicle moves: ask again while it stays selected
  detailsTimer=setTimeout(()=>{if(state.selected&&state.selected.id===e.id&&box.isConnected)loadDetails(e,box);},20000);
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
  const fb=e.component==='road'?null:document.createElement('button');   // 4.39.0: roadworks and incidents stay put
  if(fb){fb.type='button';fb.className='follow-button';fb.onclick=()=>{if(isFollowing(e))stopFollow();else startFollow(e);};}
  const rb=REPORT_ON?el('button','report-button','Report this'):null;
  if(rb){rb.type='button';rb.onclick=()=>copyReport(e);}
  selected.replaceChildren(...[h,info,more,fb,rb,warning,close].filter(Boolean));if(!isFollowing(e))stopFollow();updateFollowUI();selected.classList.remove('hidden');
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
    // 4.2.1: a replayed ("timed") vehicle is never slid across a big gap: its track is where it really went,
    // so a big difference means it was seen somewhere else (a jump) - fade it in there.
    if(e.motion&&e.motion.mode==='timed'){const [tx,ty]=metresXY(was,next);if(Math.hypot(tx,ty)>30&&!REDUCED)blends.set(k,{hop:true,jump:true,t0:now});else blends.delete(k);continue;}   // 4.13.2: the same track again: no correction (it slowed every bus down at each new answer)
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
  if(e.motion&&e.motion.mode==='timed')return 'Following its reported journey'+(e.motion.delay?' about '+(e.motion.delay>=90?Math.round(e.motion.delay/60*2)/2+' min':e.motion.delay+' s'):'')+' behind · last reported '+seen+(e.quality==='stale'?' · may have moved':'');
  if(state.frames[e.component]?.scheduled&&e.quality==='estimated')return 'Timetable position · not live';   // 4.25.0
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
  for(const st of stacks.values()){
    const q=map.project([st.lat,st.lon],e.zoom),x=q.x-c.x+size.x/2,y=q.y-c.y+size.y/2;
    st.b.classList.remove('glide');
    const tf=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) ${anchorOf(st.component)}`;st.b.style.transform=tf;st.tf=tf;
  }
}
function zoomAnimEnd(){if(!zoomAnim)return;zoomAnim=false;markerLayer.classList.remove('zoom-anim');paint();}
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
  const d=dims(),now=serverNow(),keep=new Set(),M=40,cap=d.w<760?PHONE_CAP:Infinity,perLayer=Object.create(null);moving=0;
  // Which vehicles may glide this second: the nearest to the middle of the screen, up to the budget.
  {const cx=d.w/2,cy=d.h/2,list=[];
   for(const [k,p] of pins)if(p.tf&&p.e&&state.live[p.e.component]&&(p.e.motion||blends.has(k))){const m=p.tf.match(/translate\(([-0-9.]+)px,([-0-9.]+)px\)/);if(m)list.push([k===follow?.key?-1:(m[1]-cx)**2+(m[2]-cy)**2,k]);}
   // 4.1.5: a vehicle that is already gliding keeps gliding (no "taking turns"); free places go
   // to the vehicles nearest the middle of the screen.
   list.sort((a,b)=>a[0]-b[0]);
   const next=new Set();for(const [,k] of list)if(glideOK.has(k)&&next.size<glideBudget)next.add(k);
   for(const [,k] of list){if(next.size>=glideBudget)break;next.add(k);}
   glideOK=next;glideCount=glideOK.size;}
  const items=[],present=new Set();
  for(const e of listEntities()){
    if(!e.position)continue;
    const k=keyOf(e),pos=whereNow(e,now);if(!pos)continue;present.add(k);
    if(pos.est||blends.has(k)||(state.live[e.component]&&e.motion))moving++;
    shown.set(k,{lon:pos.lon,lat:pos.lat});
    const [x,y]=pixel(pos.lon,pos.lat);
    const followed=!!follow&&follow.key===k;if(followed)follow.e=e;
    // 4.18.2 (owner 13:34: back from another window, the followed train "had carried on its route but left me where i was"):
    // the followed vehicle is never dropped for being off screen - the map goes to it
    if((x<-M||y<-M||x>d.w+M||y>d.h+M)&&!followed)continue;
    items.push({e,k,pos,x:x-ox,y:y-oy});
  }
  // 4.8.0 (owner 1 Nov 13:40, York at zoom 13: a pile of buses on top of each other): icons that would sit
  // on top of each other are shown as one "N buses" badge, which splits as you zoom in.
  const grouped=groupStacks(items);fanOut(items,grouped,now);
  for(const {e,k,pos,x,y} of items){
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
    const cls=`vehicle-pin review-pin ${e.component} ${e.quality}${e.component==='air'&&isMilitary(e)?' k-military':''}${e.component==='air'&&isService(e)?' k-'+e.variant.split('-')[0]:''}${isGround(e)?' k-ground':''}${active?' selected-pin':''}${pos.est?' est-pos':''}${glide?' glide':''}${faceLeft?' face-left':''}`;
    // 4.1.11 (owner 31 Oct 22:08: icons "look weirdish, like they are re rendered. They need to stay
    // the same"): an icon is built once and kept; only an aircraft's existing icon turns to its heading.
    const art=e.component+'|'+(e.badge||'')+'|'+(e.variant||'')+'|'+e.id;
    let pin=pins.get(k);
    if(!pin){const b=document.createElement('button');b.type='button';pin={b,art:''};pins.set(k,pin);markerLayer.append(b);}
    const b=pin.b;pin.e=e;pin.lat=pos.lat;pin.lon=pos.lon;pin.active=active;
    if(pin.art!==art){b.replaceChildren(buildArtwork(e));pin.art=art;}
    if(e.component==='air'){const svg=b.querySelector('svg,.plane-pic'),rot=KIDS?kidFlip(e):svg?.classList?.contains('plane-pic')?planeTurn(e):e.position.headingDeg!=null?'rotate('+e.position.headingDeg+'deg)':'';
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
      const back=estimate(e,now-glideMs);let from=null;
      if(back){const [bx0,by0]=pixel(back.lon,back.lat),bx=bx0-ox,by=by0-oy;from=[bx,by];
        b.style.transform=`translate(${bx.toFixed(1)}px,${by.toFixed(1)}px) ${anchorOf(e.component)}${active?' scale(1.17)':''}`;
        void b.offsetWidth;b.classList.add('glide');}
      b.style.transform=tf;pin.tf=tf;
      if(follow?.key===k)followTo(x,y,!!from,from);
    }else if(pin.tf!==tf){b.style.transform=tf;pin.tf=tf;if(follow?.key===k)followTo(x,y,glide,null);}
    b.onclick=KIDS?()=>kidsTap(e,b):()=>{if(active){state.selected=null;selected.classList.add('hidden');paint();}else selectEntity(e);};
  }
  for(const [k,pin] of pins)if(!keep.has(k)){noteVanish(k,pin,present,d);pin.b.remove();pins.delete(k);}
  drawStacks(viewMoved);
  if(shown.size>keep.size*4+4000)for(const k of shown.keys())if(!keep.has(k))shown.delete(k);
  updateSelectedInfo(now);
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
  const s=pinScale(z),join=GROUP_JOIN*s,leave=GROUP_LEAVE*s,sel=state.selected;
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
}
// 4.18.2: coming back to the tab while following, the map goes straight to where the vehicle is now (it kept going)
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState!=='visible'||!follow||!follow.e||!map)return;
  const p=estimate(follow.e,serverNow());if(!p)return;
  map.setView([p.lat,p.lon],map.getZoom(),{animate:false});follow.from=follow.to=null;follow.started=performance.now();
  refetchLiveView();paint();
});
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
  const size=map.getSize(),dx=p[0]+ox-size.x/2,dy=p[1]+oy-size.y/2;
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
const VANISH={total:0,byLayer:Object.create(null),byKey:new Map(),recent:[]};
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
function layerName(id){return ($(id+'-toggle').closest('.layer-toggle')?.querySelector('strong')?.textContent||id).toLowerCase();}
const kidsSeen=Object.create(null);
function counts(){const d=dims();const anyLive=IDS.some(id=>state.live[id]&&!state.parked[id]);
for(const id of IDS){let entries=state.frames[id]?.entities;
let count=(entries||[]).filter(e=>{if(!e.position||isGround(e))return false;   // 4.45.0: airport vehicles are not planes
let [x,y]=pixel(e.position.lon,e.position.lat);return x>=0&&x<=d.w&&y>=0&&y<=d.h;}).length;
kidsSeen[id]=state.live[id]&&state.visible[id]&&!state.parked[id]&&state.status[id]==='ready'&&!(state.frames[id]?.notice||'').startsWith('Zoom in')?count:0;
const status=$(id+'-source-status'),chip=$(id+'-chip').querySelector('strong'),n=$(id+'-count');
if(state.parked[id]){status.textContent='Unavailable · coming soon';chip.textContent='SOON';if(n)n.textContent='—';continue;}
if(state.live[id]){
  // 4.0.10: a real engine is connected. Plain wording; never the word MOCK.
  const overlay=state.frames[id]?.overlay,detail=FULL_VIEW&&state.frames[id]?.noticeDetail?' — '+state.frames[id].noticeDetail:'',
    notice=(state.frames[id]?(state.frames[id].notice||''):(state.notice[id]||''))+detail,scheduled=state.frames[id]?.scheduled===true;
  const tooFar=overlay&&state.z<overlay.minZoom;
  status.textContent=!state.visible[id]?'Hidden':
    state.status[id]==='ready'?(tooFar?'Zoom in to see '+layerName(id):notice||(overlay?'Live · showing on map':count+' in view')):
    state.status[id]==='stale'?(notice||'Delayed · showing last update'):
    (notice||'Unavailable · will retry');
  chip.textContent=!state.visible[id]?'OFF':state.status[id]==='ready'?(scheduled?'TIMETABLE':'LIVE'):state.status[id]==='stale'?'DELAYED':'OFF AIR';
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
let layersKnown=false;    // 4.7.1: set once HQ's list of layers (the manifest) has arrived
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
  if(PIC_ICON[id]){const img=el('img','pic-icon');img.src=PIC_ICON[id];img.alt='';img.decoding='async';img.draggable=false;
    img.addEventListener('error',()=>img.replaceWith(ownIcon(id)),{once:true});return img;}
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
entities.push({component:id,id:e.id,label:e.label,quality:e.quality,observedAt:e.observedAt,badge:typeof e.badge==='string'&&/^[A-Za-z0-9]{1,8}$/.test(e.badge)?e.badge:null,variant:typeof e.variant==='string'&&/^[a-z0-9-]{1,30}$/.test(e.variant)?e.variant:null,private:e.private===true,position,motion:checkMotion(e.motion,position)});}
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
    return {mode:'timed',pts,times,delay,jumps};
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
function deselectComponent(id){
  if(state.selected?.component===id){state.selected=null;selected.classList.add('hidden');}
}
// 4.0.10: each engine is asked only for what is on screen (plus a margin).
// 4.28.0 (launch list A1): rounded out to the server's shared squares (as serve_hq.area_query), with the square size,
// so viewers looking at the same place ask exactly the same question - one answer can then serve them all.
const SLOT_MS=4000;   // = serve_hq.SHARE_SECONDS
function viewQuery(){
  let w=-12.5,s=49,e=4.5,n=61.8;
  if(map&&typeof map.getBounds==='function'){const b=map.getBounds().pad(0.2);w=b.getWest();s=b.getSouth();e=b.getEast();n=b.getNorth();}
  s=clamp(s,-85,85);n=clamp(n,-85,85);w=clamp(w,-180,180);e=clamp(e,-180,180);
  const g=Math.min(4,Math.max(1/1024,2**Math.floor(Math.log2(Math.max(n-s,1e-4)/4)))),gl=g*2;
  s=Math.max(-85,Math.floor(s/g)*g);n=Math.min(85,Math.ceil(n/g)*g);w=Math.max(-180,Math.floor(w/gl)*gl);e=Math.min(180,Math.ceil(e/gl)*gl);
  const f=v=>v.toFixed(4);
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
function search(query){let q=query.trim().toLowerCase(),out=$('target-list');out.replaceChildren();if(!q){const p=document.createElement('p');p.className='empty';p.textContent='Search a UK place. Vehicle targets will appear when independently connected.';out.append(p);return;}const places=POI.filter(([name])=>name.toLowerCase().includes(q)).slice(0,10).map(([name,lon,lat])=>({name,kind:'Place',action:()=>gotoPlace(lon,lat,11,name)}));const targets=listEntities().filter(e=>e.position&&(e.label.toLowerCase().includes(q)||e.id.toLowerCase().includes(q))).slice(0,10).map(e=>({name:e.label,kind:e.component.toUpperCase(),action:()=>{gotoPlace(e.position.lon,e.position.lat,13,e.label);selectEntity(e);}}));if(q==='uk'||'united kingdom'.includes(q))places.unshift({name:'United Kingdom overview',kind:'Region',action:fitUK});const items=[...places,...targets];if(!items.length){const p=document.createElement('p');p.className='empty';p.textContent='No matching place or connected target.';out.append(p);return;}for(const item of items){const b=document.createElement('button');b.type='button';b.className='search-result';const name=document.createElement('strong');name.textContent=item.name;const type=document.createElement('small');type.textContent=item.kind;b.append(name,type);b.onclick=()=>{item.action();$('search').value=item.name;};out.append(b);}}
for(const id of IDS){$(id+'-toggle').addEventListener('change',()=>{state.visible[id]=$(id+'-toggle').checked;paint();});}
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
  // 4.38.0: zooming out stops once the whole UK box is on screen (a phone stops one step further out than a PC).
  const fitMinZoom=()=>{try{if(!ukBounds||typeof map.getBoundsZoom!=='function')return;minZ=clamp(map.getBoundsZoom(ukBounds,false),3,10);if(typeof map.setMinZoom==='function')map.setMinZoom(minZ);}catch(_){}};
  fitMinZoom();if(typeof map.on==='function')map.on('resize',fitMinZoom);
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
  map.on('dragstart',()=>{if(follow)stopFollow();});   // 4.10.0: grabbing the map stops following
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
function openDialog(back){lastFocus=document.activeElement;back.classList.remove('hidden');
  for(const list of prepLists)if(back.contains(list.box))list.t0=performance.now();   // 4.7.2: the tiles play again each time
  const enter=back._enter,wait=Number(back.dataset.enterAfter||0);
  if(enter&&wait>0){clearTimeout(back._enterTimer);enter.classList.add('waiting');enter.disabled=true;enter.setAttribute('aria-hidden','true');
    back._enterTimer=setTimeout(()=>{enter.classList.remove('waiting');enter.disabled=false;enter.removeAttribute('aria-hidden');enter.focus();},wait*1000);}
  back.querySelector('.tpa-primary:not(.waiting),button:not([disabled]),a')?.focus();}
function closeDialog(back){back.classList.add('hidden');if(back.id==='tpa-welcome'){try{localStorage.setItem(WELCOME_KEY,'1');}catch(_){}}lastFocus?.focus?.();}
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
  for(const sec of (Array.isArray(w?.sections)?w.sections.slice(0,4):[])){
    const head=str(sec?.heading,60),items=(Array.isArray(sec?.items)?sec.items.slice(0,6):[]).map(t=>str(t,240)).filter(Boolean);
    if(!head||!items.length)continue;
    const box=el('div','welcome-section');box.append(el('h3','',head));const ul=el('ul','');
    for(const t of items)ul.append(el('li','',t));box.append(ul);main.append(box);}
  const enter=el('button','tpa-primary',str(w?.enterLabel,40)||'Enter the map');enter.type='button';enter.onclick=()=>closeDialog(back);
  // 4.7.0: the streams pulse and turn green; Let's go! appears after enterAfterSeconds (5) from opening
  const ready=el('div','welcome-ready');ready.append(el('small','prep-caption','Preparing streams'),makeFlapBoard());   // 4.26.0
  const wait=Number.isFinite(w?.enterAfterSeconds)?clamp(w.enterAfterSeconds,0,10):0;
  if(wait>0){enter.classList.add('waiting');enter.disabled=true;enter.setAttribute('aria-hidden','true');back.dataset.enterAfter=String(wait);}
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
    .map(id=>shortCredit[layerName(id)]).filter(Boolean);
  const text='© OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors'+(live.length?' · Live data: '+live.join(' · '):'');
  if(box.textContent!==text)box.textContent=text;}
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
  about.append(...[title,tipControl(cfg.tip),creditsBtn,welcomeBtn].filter(Boolean));
  $('sidebar').insertBefore(about,document.querySelector('.sidebar-footer'));
  let seen=false;try{seen=localStorage.getItem(WELCOME_KEY)==='1';}catch(_){}
  if(cfg.welcome?.everyVisit===true)seen=false;      // 4.7.0 (owner 1 Nov 15:22): the welcome screen every visit
  if(KIDS){try{openDialog(buildKidsWelcome(cfg.welcome,credits));}catch(_){}return;}
  if(welcome&&cfg.welcome?.enabled===true&&!seen)openDialog(welcome);
  startPreparing(!(welcome&&cfg.welcome?.enabled===true&&!seen));   // 4.6.0: returning visitors get the short panel
}
if(!KIDS)ownIconsInPage();
for(const b of document.querySelectorAll('.air-filter button')){
  b.setAttribute('aria-pressed',String(b.dataset.air===airShow));
  b.addEventListener('click',()=>{airShow=b.dataset.air;try{localStorage.setItem('tpa-air-show',airShow);}catch(_){}
    for(const o of document.querySelectorAll('.air-filter button'))o.setAttribute('aria-pressed',String(o===b));paint();});}
paint();discover();beginMap();setInterval(discover,30000);
// 4.0.11: also check every 10 s, but only while a live layer is still connecting.
setInterval(()=>{if(IDS.some(id=>state.live[id]&&!state.parked[id]&&state.status[id]!=='ready'))discover();},10000);
loadSiteConfig();
// 4.0.13: one place that opens/closes the phone layers menu and keeps the button state right.
function setMenu(open){$('sidebar').classList.toggle('open',open);$('menu-button').setAttribute('aria-expanded',open?'true':'false');}
new MutationObserver(()=>$('menu-button').setAttribute('aria-expanded',$('sidebar').classList.contains('open')?'true':'false')).observe($('sidebar'),{attributes:true,attributeFilter:['class']});
})();
