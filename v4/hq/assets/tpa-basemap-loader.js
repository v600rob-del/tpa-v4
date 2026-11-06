/* Historic TPA f24e905c loader preserved in architecture; standard HQ only. */
(()=>{'use strict';
const conf=document.getElementById('tpa-basemap-loader'),leaflet=document.getElementById('tpa-leaflet-js'),style=document.getElementById('tpa-maplibre-css');
const timings={},connected=new WeakMap();
const status=document.getElementById('map-wait');
const report=(code,stage,detail)=>window.TPA_HQ_MAP_REPORT?.(code,stage,detail);
function loaded(el,ready){if(ready())return Promise.resolve();return new Promise((res,rej)=>{el.addEventListener('load',res,{once:true});el.addEventListener('error',()=>rej(Error('Asset did not load')),{once:true});});}
function script(src){return new Promise((res,rej)=>{const e=document.createElement('script');e.src=src;e.async=true;e.addEventListener('load',res,{once:true});e.addEventListener('error',()=>rej(Error('Asset did not load: '+src)),{once:true});document.head.append(e);});}
const css=loaded(style,()=>!!style.sheet).then(()=>{style.media='all';timings.vectorCssReady=performance.now();});
const lib=script(conf.dataset.maplibre).then(()=>{timings.vectorScriptReady=performance.now();});
// 4.27.0 (owner 6 Nov 14:26-14:43: "do we really need [3D buildings]? does it put pressure on the browser"; "keep hospitals
// petrol stations museums and anything that could be useful, where you can get a decent manhattan in york isnt one of
// those"): a calmer street map. Flat building outlines at every zoom (no 3D blocks for the graphics chip to draw) and
// only useful labels: travel, help and landmarks. No shops, cafes, bars, restaurants, hairdressers. Safe if a layer is missing.
const CALM_KEEP=['railway','bus','ferry_terminal','harbor','parking','aerialway','fuel','charging_station',
  'hospital','police','toilets','information','museum','castle','monument','attraction','zoo','stadium'];
function calmMap(gl){
  try{
    if(!gl||(gl.style&&gl.style.__tpaCalm))return;
    if(gl.getLayer('building-3d'))gl.setLayoutProperty('building-3d','visibility','none');
    if(gl.getLayer('building')){gl.setLayerZoomRange('building',13,24);
      gl.setPaintProperty('building','fill-color','hsl(35,8%,83%)');gl.setPaintProperty('building','fill-outline-color','hsl(35,6%,74%)');}
    for(const id of ['poi_r1','poi_r7','poi_r20']){if(!gl.getLayer(id))continue;const f=gl.getFilter(id);
      gl.setFilter(id,['all',...(f?[f]:[]),['match',['get','class'],CALM_KEEP,true,false]]);}
    if(gl.style)gl.style.__tpaCalm=true;
  }catch(e){report('map-calm-error','calm',String(e&&e.message||e));}
}
Object.defineProperty(window,'TPA_CALM_MAP',{value:calmMap,writable:false,configurable:false});
const ready=Promise.all([loaded(leaflet,()=>!!window.L),css,lib]).then(()=>script(conf.dataset.bridge)).then(()=>script(conf.dataset.vector));
ready.catch(e=>{report('library-resource-error','vendor-load',e.message);});
Object.defineProperty(window,'TPA_BASEMAP',{value:Object.freeze({timings:()=>({...timings}),attach(map,opts){
if(connected.has(map))return connected.get(map);
if(status){status.hidden=false;status.textContent='Loading OpenFreeMap streets…';}
const promise=ready.then(async()=>{
if(map._animatingZoom||map.touchZoom?._zooming)await new Promise(r=>map.once('zoomend',r));
if(!window.TPA_VECTOR_LAYER)throw Error('Buffer adapter not initialized');
const layer=window.TPA_VECTOR_LAYER(opts).addTo(map), gl=layer.getMaplibreMap?.();
timings.vectorLayerAttached=performance.now();report('vector-layer-attached','bridge-attached','Leaflet vector adapter attached');
if(gl){
gl.on('style.load',()=>calmMap(gl));if(gl.isStyleLoaded?.())calmMap(gl);   // 4.27.0: also after a switch to the fallback style
const finish=()=>{timings.basemapLoaded=performance.now();if(status)status.hidden=true;window.TPA_HQ_VECTOR_READY?.();report('map-loaded','liberty-style','Vector style loaded');};
if(gl.loaded())finish();else gl.once('load',finish);
if(typeof gl.on==='function')gl.on('idle',()=>window.TPA_HQ_MAP_IDLE?.());
let loadedOnce=false,switched=false;gl.once('load',()=>{loadedOnce=true;});
gl.on('error',e=>{
const m=String(e?.error?.message||e?.message||'Unknown MapLibre error');
// 4.1.7: if the direct OpenFreeMap route fails before the map has loaded, use the route through HQ.
if(!loadedOnce&&!switched&&opts.fallbackStyle){switched=true;report('map-fallback','direct-failed',m);gl.setStyle(new URL(opts.fallbackStyle,location.href).href);return;}
report(/tile|pbf|protobuf|vector/i.test(m)?'map-vector-error':'map-resource-error','maplibre-event',m);
window.TPA_HQ_VECTOR_ERROR?.(m);
});
} else throw Error('MapLibre bridge returned no map');
return layer;
}).catch(e=>{report('bridge-missing','bridge-attach',String(e.message||e));window.TPA_HQ_VECTOR_ERROR?.(String(e.message||e));return null;});
connected.set(map,promise);return promise;
}}),writable:false,configurable:false});
})();
