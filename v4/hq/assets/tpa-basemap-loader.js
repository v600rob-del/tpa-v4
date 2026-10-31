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
const finish=()=>{timings.basemapLoaded=performance.now();if(status)status.hidden=true;window.TPA_HQ_VECTOR_READY?.();report('map-loaded','liberty-style','Vector style loaded');};
if(gl.loaded())finish();else gl.once('load',finish);
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
