/* Exact historical TPA buffered Leaflet/MapLibre adapter from f24e905c. */
(()=>{'use strict';
const Bridge=L.MaplibreGL;
const BufferedVectorLayer=Bridge.extend({
_update(event){
if(event?.type==='move'&&!this._zooming&&this._map&&this._glMap){
const map=this._map,center=this._glMap.getCenter();
if(this._glMap.getZoom()===map.getZoom()-1){
const delta=map.project(map.getCenter()).subtract(map.project([center.lat,center.lng]));
const reserve=map.getSize().multiplyBy(this.options.padding*0.8);
if(reserve.x>0&&reserve.y>0&&Math.abs(delta.x)<reserve.x&&Math.abs(delta.y)<reserve.y)return;
}}
return Bridge.prototype._update.call(this,event);
},
_pinchZoom(event){
const origin=this._map.containerPointToLayerPoint([0,0]);
const topLeft=origin.subtract(this._map.getSize().multiplyBy(this.options.padding));
L.DomUtil.setPosition(this._container,this._roundPoint(topLeft));
return Bridge.prototype._pinchZoom.call(this,event);
},
_animateZoom(event){
const map=this._map,center=this._glMap.getCenter();
const scale=map.getZoomScale(event.zoom);
const point=map._latLngToNewLayerPoint([center.lat,center.lng],event.zoom,event.center)
.subtract(this.getSize().multiplyBy(scale/2))
.subtract(L.DomUtil.getPosition(this._container));
L.DomUtil.setTransform(this._glMap._actualCanvas,point,scale);
}
});
Object.defineProperty(window,'TPA_VECTOR_LAYER',{value:options=>new BufferedVectorLayer(options),writable:false,configurable:false});
})();
