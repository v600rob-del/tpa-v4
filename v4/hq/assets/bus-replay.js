// TPA bus replay in the browser (HQ 4.56.0, bus engine 0.17.0 - "publish, don't answer", Plan B).
// Owner 13 Nov 2026: the server publishes each bus's reports placed along its route; every phone works out the smooth
// movement for the buses on its own screen. This is the same arithmetic as the bus engine's replay.py (Shape.point_at,
// Shape.vertices_between, _keep_left, track) so a bus moves exactly as it did when the server drew it.
// Pure arithmetic, no network. Used by hq.js (window.TPA_BUS_REPLAY) and by the tests (Node: module.exports).
(function(root){
  'use strict';
  const M_PER_DEG=111195.0, MAX_POINTS=200;
  function bisectLeft(a,x){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<x)lo=m+1;else hi=m;}return lo;}
  function bisectRight(a,x){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(x<a[m])hi=m;else lo=m+1;}return lo;}
  // A route line [[lon,lat],...] with the distance along it (metres) at each corner.
  function Shape(lonlat){
    const pts=[];for(let i=0;i<lonlat.length;i++){const p=lonlat[i];if(i===0||p[0]!==lonlat[i-1][0]||p[1]!==lonlat[i-1][1])pts.push(p);}
    if(pts.length<2)throw Error('shape too short');
    this.k=Math.cos(pts[0][1]*Math.PI/180)*M_PER_DEG;
    this.lon=pts.map(p=>p[0]);this.lat=pts.map(p=>p[1]);
    const x=this.lon.map(v=>v*this.k),y=this.lat.map(v=>v*M_PER_DEG);
    this.cum=[0];for(let i=1;i<pts.length;i++)this.cum.push(this.cum[i-1]+Math.hypot(x[i]-x[i-1],y[i]-y[i-1]));
  }
  Shape.prototype.length=function(){return this.cum[this.cum.length-1];};
  Shape.prototype.pointAt=function(m){
    m=Math.max(0,Math.min(this.length(),m));
    const c=this.cum,i=Math.max(1,Math.min(c.length-1,bisectLeft(c,m)));
    const seg=c[i]-c[i-1],f=seg<=0?0:(m-c[i-1])/seg;
    return [this.lon[i-1]+(this.lon[i]-this.lon[i-1])*f,this.lat[i-1]+(this.lat[i]-this.lat[i-1])*f];
  };
  // corners strictly between a and b (a hair of tolerance: a report exactly on a corner is not counted twice)
  Shape.prototype.verticesBetween=function(a,b){const i=bisectRight(this.cum,a+1e-6),j=bisectLeft(this.cum,b-1e-6),out=[];for(let k=i;k<j;k++)out.push(this.cum[k]);return out;};
  function keepLeft(points,metres,lat){
    if(metres<=0||points.length<2)return points;
    const k=Math.cos(lat*Math.PI/180)*M_PER_DEG,out=[];
    for(let i=0;i<points.length;i++){const [x,y]=points[i],a=points[Math.max(0,i-1)],b=points[Math.min(points.length-1,i+1)];
      const dx=(b[0]-a[0])*k,dy=(b[1]-a[1])*M_PER_DEG,L=Math.hypot(dx,dy);
      if(L===0){out.push([x,y]);continue;}
      out.push([x-dy/L*metres/k,y+dx/L*metres/M_PER_DEG]);}
    return out;
  }
  const r1=v=>Math.round(v*10)/10;
  // placed = [[report time (s), metres along], ...] oldest first; jumps = indices of reports a jump starts from (bus 0.19.0:
  // no drive found between two reports - stands, then is seen at the next). Same result as replay.track.
  function track(shape,placed,now,delay,keepLeftM,jumps){
    const J=new Set(Array.isArray(jumps)?jumps:[]);
    if(!placed||!placed.length)return null;
    const shown=now-delay,seq=placed;
    if(shown>=seq[seq.length-1][0]){const p=shape.pointAt(seq[seq.length-1][1]);return {position:p,points:[p],times:[r1(now)],waiting:true};}
    let startT,startM,first;
    if(shown<=seq[0][0]){startT=shown;startM=seq[0][1];first=0;}
    else{let i=1;while(!(seq[i][0]>shown))i++;
      const [t0,m0]=seq[i-1],[t1,m1]=seq[i];startM=J.has(i-1)?m0:m0+(m1-m0)*(shown-t0)/(t1-t0);startT=shown;first=i;}
    const rest=seq.slice(first),tm=[[startT,startM,first-1]].concat(rest.map((q,k)=>[q[0],q[1],first+k]));
    let marks=[];
    for(let z=0;z+1<tm.length;z++){const [ta,ma]=tm[z],[tb,mb,ib]=tm[z+1];
      if(!marks.length)marks.push([ta,ma]);
      if(J.has(ib-1)){if(tb-0.1>ta)marks.push([tb-0.1,ma]);}
      else if(mb>ma)for(const m of shape.verticesBetween(ma,mb))marks.push([ta+(tb-ta)*(m-ma)/(mb-ma),m]);
      marks.push([tb,mb]);}
    if(marks.length>MAX_POINTS){
      const keep=new Set([0,marks.length-1]);
      marks.forEach((x,k)=>{if(rest.some(r=>x[0]===r[0]))keep.add(k);});
      for(let k=0;k+1<marks.length;k++)if(marks[k+1][0]-marks[k][0]<=0.1+1e-6)keep.add(k);
      const room=MAX_POINTS-keep.size,step=(marks.length-1)/Math.max(1,room);
      for(let j=0;j<room;j++)keep.add(Math.round(j*step));
      marks=[...keep].sort((a,b)=>a-b).map(k=>marks[k]).slice(0,MAX_POINTS);
    }
    let pts=marks.map(x=>shape.pointAt(x[1]));
    pts=keepLeft(pts,keepLeftM||0,pts[0][1]);
    const times=marks.map(x=>r1(x[0]+delay));
    for(let k=1;k<times.length;k++)times[k]=Math.max(times[k],times[k-1]);
    return {position:pts[0],points:pts,times,waiting:false};
  }
  // Straight between published positions [[t,lon,lat],...] (zoomed-out tiles: no route line needed at that scale).
  // step=true (buses not matched to a timetable, owner 13 Nov 16:54): stands where it was seen, then is seen at the
  // next report (a jump, which the page fades) - never driven across where a bus can't go.
  function straight(p,now,delay,step){
    if(!p||!p.length)return null;
    const shown=now-delay,n=p.length;
    if(shown>=p[n-1][0]){const q=[p[n-1][1],p[n-1][2]];return {position:q,points:[q],times:[r1(now)],waiting:true};}
    let i=0;while(i<n&&p[i][0]<=shown)i++;          // p[i] = the first report still ahead of the moment shown
    let start;
    if(i===0)start=[p[0][1],p[0][2]];
    else if(step)start=[p[i-1][1],p[i-1][2]];
    else{const a=p[i-1],b=p[i],f=(shown-a[0])/Math.max(1e-9,b[0]-a[0]);start=[a[1]+(b[1]-a[1])*f,a[2]+(b[2]-a[2])*f];}
    const pts=[start],times=[r1(now)];
    for(let k=i;k<n;k++){
      if(step&&k>0){const before=k===i?start:[p[k-1][1],p[k-1][2]];pts.push(before);times.push(r1(p[k][0]+delay-0.1));}
      pts.push([p[k][1],p[k][2]]);times.push(r1(p[k][0]+delay));}
    for(let k=1;k<times.length;k++)times[k]=Math.max(times[k],times[k-1]);
    return {position:start,points:pts.slice(0,MAX_POINTS),times:times.slice(0,MAX_POINTS),waiting:false};
  }
  const api={Shape,track,straight,keepLeft};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.TPA_BUS_REPLAY=api;
})(typeof window!=='undefined'?window:globalThis);
