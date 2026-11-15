// TPA "fresh engine" (experiment N1, 15 Nov 2026). Written from scratch for Rob's question: what would we build today?
// Isolated: not used by the live site, the replay or the C3/C4 test modes. Pure arithmetic, no network.
//
// Idea in one line: work out where each bus most likely is NOW (estimation), and separately move its icon there
// smoothly (display). The two are kept apart so the icon never has to jump just because the estimate changed.
//
// ESTIMATION
//  * Evidence = the bus's own reports (GPS time + place), only once the page could have them.
//  * The path ahead: the bus's timetable route line if it has one; otherwise a route line BORROWED from another bus of
//    the same operator and route number, but only if this bus's own recent reports lie on it (within 30 m, in order,
//    and it has moved along it); otherwise no path ahead is known.
//  * With a path ahead: newest report + a learned advance. How far buses really go in the next t seconds is learned
//    continuously from the reports themselves (same region, similar recent speed, similar time since report, whether the
//    bus was moving at its last report). No fixed "60% of speed" guess. A bus with no speed evidence is not moved on.
//    Never carried on more than HORIZON s after its newest report (then held: the data can't support more).
//  * Without a path ahead: no prediction at all - the icon goes to the newest report along the road it actually drove
//    (or straight if the two reports are close; a fade if they are not).
// DISPLAY
//  * Each bus has an icon position along its path and an icon speed. Each second the speed is steered towards the
//    estimate (time constant TC), within believable limits (acceleration, top speed, catch-up share). The icon never
//    moves backwards: if the estimate falls back behind it, the icon slows and stops until the estimate passes.
//  * Fades only when smooth movement can't fix it: a gap too big to catch up within CATCH_MAX_S, shown well ahead for
//    too long, or a stretch the bus can't be drawn along.
(function(root){
'use strict';
const MPD=111195;
const P0={
  q:0.5,             // learned advance: the median of what similar buses really did (chosen on the tuning half)
  horizon:240,       // s after the newest report: carried on at most this long, then held
  tauBins:[30,60,100,160],
  vBins:[0.5,2,4,6,9],
  minCell:30, cellCap:600,
  priorShare:0.5,    // until enough is learned: half its recent speed
  ratio:false,       // learn a share of the bus's own recent speed (true) or a speed for its speed band (false)
  speedWindow:120, movedM:10,
  TC:12,             // s: how quickly the icon's speed is steered towards the estimate
  accUp:1.0, accDown:2.0,       // m/s per s (a bus pulls away at ~1 m/s2)
  vmaxShare:2, vmaxAdd:5, vmaxAbs:20,
  catchMaxS:60, jumpGap:500,    // further behind than this can't be caught up believably -> fade
  aheadFadeM:80, aheadFadeS:30, // shown this far ahead for this long -> fade back
  glideTC:15,                   // (fallback pace when far behind)
  jitShare:1.2,                 // no path ahead: arrive at the newest report about when the next one is expected
                                // (this share of the bus's usual time between reports after it reached us)
  stepGlideMax:80,              // no road known: glide straight only between reports this close
  borrowTol:30, borrowMinMove:30, borrowKeepTol:45,
  continuityM:25,
  focusCatchS:6,                // HQ 4.63.2: the followed bus (Follow Me) never fades - it closes a big gap a sixth a second
};
// ---------- geometry: a line [[lon,lat],...] with distances (metres) along it
class Line{
  constructor(ll){
    const p=[];for(const q of ll){if(!Array.isArray(q)||!isFinite(q[0])||!isFinite(q[1]))continue;
      if(!p.length||q[0]!==p[p.length-1][0]||q[1]!==p[p.length-1][1])p.push([+q[0],+q[1]]);}
    if(p.length<1)throw Error('empty line');if(p.length===1)p.push(p[0].slice());
    this.k=Math.cos(p[0][1]*Math.PI/180)*MPD;this.lon=p.map(q=>q[0]);this.lat=p.map(q=>q[1]);
    this.x=this.lon.map(v=>v*this.k);this.y=this.lat.map(v=>v*MPD);
    this.cum=[0];for(let i=1;i<p.length;i++)this.cum.push(this.cum[i-1]+Math.hypot(this.x[i]-this.x[i-1],this.y[i]-this.y[i-1]));
  }
  get length(){return this.cum[this.cum.length-1];}
  at(m){const c=this.cum;m=Math.max(0,Math.min(this.length,m));let lo=1,hi=c.length-1;
    while(lo<hi){const h=(lo+hi)>>1;if(c[h]<m)lo=h+1;else hi=h;}
    const i=lo,s=c[i]-c[i-1],f=s>0?(m-c[i-1])/s:0;
    return [this.lon[i-1]+(this.lon[i]-this.lon[i-1])*f,this.lat[i-1]+(this.lat[i]-this.lat[i-1])*f];}
  // nearest point with lo <= m <= hi: {m, dist}
  project(lon,lat,lo=-Infinity,hi=Infinity){
    const px=lon*this.k,py=lat*MPD,c=this.cum;let best={m:null,dist:Infinity};
    for(let i=1;i<c.length;i++){
      if(c[i]<lo||c[i-1]>hi)continue;
      const ax=this.x[i-1],ay=this.y[i-1],dx=this.x[i]-ax,dy=this.y[i]-ay,L2=dx*dx+dy*dy;
      let f=L2>0?((px-ax)*dx+(py-ay)*dy)/L2:0;f=Math.max(0,Math.min(1,f));
      let m=c[i-1]+f*(c[i]-c[i-1]);if(m<lo||m>hi){m=Math.max(lo,Math.min(hi,m));const q=this.at(m);const d=Math.hypot((q[0]*this.k)-px,(q[1]*MPD)-py);if(d<best.dist)best={m,dist:d};continue;}
      const d=Math.hypot(ax+f*dx-px,ay+f*dy-py);if(d<best.dist)best={m,dist:d};}
    return best;}
}
function metres(a,b){const k=Math.cos(a[1]*Math.PI/180)*MPD;return Math.hypot((b[0]-a[0])*k,(b[1]-a[1])*MPD);}
function bin(v,edges){let i=0;while(i<edges.length&&v>=edges[i])i++;return i;}
function quantile(arr,q){const a=arr.slice().sort((x,y)=>x-y);if(!a.length)return null;const p=q*(a.length-1),i=Math.floor(p),f=p-i;return i+1<a.length?a[i]+(a[i+1]-a[i])*f:a[i];}

// ---------- learning how far buses really go (per region)
class Learner{
  constructor(P){this.P=P;this.cells=new Map();this.n=0;this.cache=new Map();}
  key(g,vb,mv,tb){return g+'|'+vb+'|'+mv+'|'+tb;}
  add(g,v,moved,tau,ve){const P=this.P;if(!(tau>0)||!isFinite(ve))return;
    const k=this.key(g,bin(v,P.vBins),moved?1:0,bin(tau,P.tauBins));let a=this.cells.get(k);if(!a)this.cells.set(k,a=[]);
    a.push(P.ratio&&v>=P.vBins[0]?ve/v:ve);if(a.length>P.cellCap)a.shift();this.n++;this.cache.clear();}
  // effective speed (m/s) to carry on with over the next tau seconds
  speed(g,v,moved,tau){const P=this.P,vb=bin(v,P.vBins),tb=bin(tau,P.tauBins),ck=this.key(g,vb,moved?1:0,tb);
    let c=this.cache.get(ck);if(c===undefined){c=this.share(g,vb,moved,tb);this.cache.set(ck,c);}   // learned values change only when reports arrive
    if(c===null)return {ve:P.priorShare*v,learned:false};
    return {ve:P.ratio&&v>=P.vBins[0]?c*v:c,learned:true};}
  share(g,vb,moved,tb){const P=this.P;
    let a=this.cells.get(this.key(g,vb,moved?1:0,tb));
    if(!a||a.length<P.minCell){// back off: same speed band, any time since report / moving state
      const pool=[];for(const mv of [0,1])for(let t=0;t<=P.tauBins.length;t++){const c=this.cells.get(this.key(g,vb,mv,t));if(c)pool.push(...c);}
      if(pool.length>=P.minCell)a=pool;else return null;}
    return Math.max(0,quantile(a,P.q));}
}

// ---------- the engine
class FreshEngine{
  constructor(opts){this.P=Object.assign({},P0,opts||{});this.buses=new Map();this.lib=new Map();this.learn=new Learner(this.P);
    this.stats={borrowTried:0,borrowOk:0,hops:{},frames:0,focusSaves:0};this.focus=null;}
  // One page read: the tile items (exactly what the page gets) and a function giving a route line (Line) by its id.
  frame(now,items,lineOf){
    const P=this.P,seen=new Set();this.stats.frames++;
    for(const it of items){
      if(!it||typeof it.i!=='string'||!Array.isArray(it.p)||!it.p.length)continue;
      seen.add(it.i);
      let B=this.buses.get(it.i);
      if(!B){const parts=it.i.split(':');B={id:it.i,op:parts[1]||'',badge:String(it.b||''),g:it.i.startsWith('bus:TFLO:')?'L':'E',
        fixes:new Map(),path:null,d:null};this.buses.set(it.i,B);}
      if(it.b)B.badge=String(it.b);
      for(const q of it.p)if(Array.isArray(q)&&isFinite(q[0])&&!B.fixes.has(q[0]))B.fixes.set(q[0],[q[1],q[2],now]);
      if(B.fixes.size>60){const ks=[...B.fixes.keys()].sort((a,b)=>a-b);for(const k of ks.slice(0,ks.length-60))B.fixes.delete(k);}
      const sh=it.s&&Array.isArray(it.r)?lineOf(it.s):null;
      if(sh){const key=B.op+'|'+B.badge;if(B.badge){let L=this.lib.get(key);if(!L)this.lib.set(key,L=new Map());L.set(it.s,sh);}
        this.setPath(B,{kind:'route',key:'r:'+it.s,line:sh,predictive:true,fx:it.r.map(q=>[q[0],q[1]]),hops:[]});}
      else this.otherPath(B,it);
    }
    for(const id of [...this.buses.keys()])if(!seen.has(id))this.buses.delete(id);   // the page stops drawing it too
  }
  setPath(B,path){
    const old=B.path;
    // the engine keeps its own memory of a bus's reports on the same line (tiles carry only the last few minutes)
    if(old&&old.key===path.key&&path.kind!=='road'&&path.kind!=='step'){const have=new Set(path.fx.map(q=>q[0]));
      for(const q of old.fx)if(!have.has(q[0]))path.fx.push(q);}
    path.fx.sort((a,b)=>a[0]-b[0]);if(path.fx.length>40)path.fx=path.fx.slice(-40);
    // learning: every new report on the same predictive path teaches how far buses go after earlier reports
    if(path.predictive&&old&&old.key===path.key){
      const had=new Set(old.fx.map(q=>q[0]));
      for(const [Tj,mj] of path.fx){if(had.has(Tj))continue;
        for(let i=0;i<path.fx.length;i++){const [Ti,mi]=path.fx[i];const tau=Tj-Ti;if(!(Ti<Tj)||tau<5||tau>this.P.horizon+60)continue;
          const f=this.features(path.fx,i);if(f.v===null)continue;this.learn.add(B.g,f.v,f.moved,tau,(mj-mi)/tau);}}}
    B.path=path;
  }
  // speed over the last SPEED_WINDOW s before report i, and whether it moved since the report before
  features(fx,i){const P=this.P,[T,m]=fx[i];let v=null,moved=false;
    for(let k=0;k<i;k++){if(T-fx[k][0]<=P.speedWindow){v=Math.max(0,Math.min(25,(m-fx[k][1])/(T-fx[k][0])));break;}}
    if(i>0)moved=m-fx[i-1][1]>=P.movedM;return {v,moved};}
  otherPath(B,it){
    const P=this.P,ts=[...B.fixes.keys()].sort((a,b)=>a-b);
    // 1) borrow a route line from a bus of the same operator and route number, if this bus's reports lie on it
    const L=B.badge?this.lib.get(B.op+'|'+B.badge):null;
    if(L&&ts.length>=2){
      const recent=ts.filter(t=>ts[ts.length-1]-t<=300).slice(-5);
      let best=null;
      for(const [sid,line] of L){
        const keep=B.path&&B.path.key==='b:'+sid;
        let prev=null,ok=true,sum=0;const fx=[];
        for(const t of recent){const [lo,la]=B.fixes.get(t);
          const pr=prev===null?line.project(lo,la):line.project(lo,la,prev-30,prev+3000);
          if(!(pr.dist<=(keep?P.borrowKeepTol:P.borrowTol))){ok=false;break;}
          if(prev!==null&&pr.m<prev-20){ok=false;break;}
          prev=Math.max(prev===null?-Infinity:prev,pr.m);fx.push([t,prev]);sum+=pr.dist;}
        if(!ok)continue;
        if(!keep&&fx[fx.length-1][1]-fx[0][1]<P.borrowMinMove)continue;   // must have moved along it (gives the direction)
        if(line.length-fx[fx.length-1][1]<50)continue;
        const score=sum/fx.length-(keep?10:0);
        if(!best||score<best.score)best={score,sid,line,fx};
      }
      this.stats.borrowTried++;
      if(best){this.stats.borrowOk++;this.setPath(B,{kind:'borrowed',key:'b:'+best.sid,line:best.line,predictive:true,fx:best.fx,hops:[]});return;}
    }
    // 2) the road it drove (published with the bus), no path ahead
    if(Array.isArray(it.w)&&it.w.length>=2&&Array.isArray(it.r)&&it.r.length){
      let line=null;try{line=new Line(it.w);}catch(_){line=null;}
      if(line){const hops=(Array.isArray(it.jm)?it.jm:[]).filter(j=>j>=0&&j+1<it.r.length).map(j=>[it.r[j][1],it.r[j+1][1]]);
        this.setPath(B,{kind:'road',key:'w:'+it.w.length+':'+it.w[0]+':'+it.w[it.w.length-1],line,predictive:false,fx:it.r.map(q=>[q[0],q[1]]),hops});return;}
    }
    // 3) only its reports: straight between them when close, faded when not
    const pts=it.p.filter(q=>Array.isArray(q)).sort((a,b)=>a[0]-b[0]);
    let line=null;try{line=new Line(pts.map(q=>[q[1],q[2]]));}catch(_){line=null;}
    if(!line)return;
    const fx=[],hops=[];let m=0;
    for(let k=0;k<pts.length;k++){if(k>0){const d=metres([pts[k-1][1],pts[k-1][2]],[pts[k][1],pts[k][2]]);if(d>P.stepGlideMax)hops.push([m,m+d]);m+=d;}fx.push([pts[k][0],m]);}
    this.setPath(B,{kind:'step',key:'s:'+pts[0][0]+':'+pts.length+':'+pts[pts.length-1][0],line,predictive:false,fx,hops});
  }
  // where the estimate says the bus is at time t (metres along its path), how fast it is going, and why
  target(B,t){
    const P=this.P,path=B.path,fx=path.fx,n=fx.length-1,[Tn,mn]=fx[n];
    if(!path.predictive){let v=0;for(let k=n;k>0;k--){const dt=fx[k][0]-fx[k-1][0];if(dt>0&&fx[k][1]>fx[k-1][1]){v=(fx[k][1]-fx[k-1][1])/dt;break;}}
      // when the newest report reached us, and this bus's usual time between reports
      if(!B.gapC||B.gapC[0]!==Tn){const ts=[...B.fixes.keys()].sort((a,b)=>a-b),gaps=[];for(let k=Math.max(1,ts.length-9);k<ts.length;k++)gaps.push(ts[k]-ts[k-1]);
        B.gapC=[Tn,gaps.length?quantile(gaps,0.5):30];}
      const gap=B.gapC[1],fz=B.fixes.get(Tn),Un=fz&&fz[2]!==undefined?fz[2]:t;
      return {m:mn,v:0,glideV:Math.min(P.vmaxAbs,v),mn,how:'to newest report',Tn,arriveBy:Un+P.jitShare*Math.min(120,Math.max(10,gap))};}
    const tau=Math.max(0,t-Tn),f=this.features(fx,n);
    if(f.v===null)return {m:mn,v:0,mn,how:'held (no speed yet)',Tn};
    const tt=Math.min(tau,P.horizon),{ve,learned}=this.learn.speed(B.g,f.v,f.moved,Math.max(1,tt));
    const m=Math.min(path.line.length,mn+ve*tt);
    return {m,v:tau<P.horizon&&m<path.line.length?ve:0,mn,how:tau>=P.horizon?'held (no recent report)':(learned?'estimated':'estimated (prior)'),Tn};
  }
  // advance every icon by dt seconds to time t; returns nothing - read positions with where()
  step(t,dt=1){
    const P=this.P;
    for(const B of this.buses.values()){
      if(!B.path)continue;
      const path=B.path,tg=this.target(B,t);
      let d=B.d,hop=false,why='';
      if(d&&d.key!==path.key){             // a new path (new journey, new stretch of road, borrowed line...)
        const pr=path.line.project(d.pos[0],d.pos[1],-Infinity,Math.max(tg.m,tg.mn)+100);
        if(pr.dist<=P.continuityM){d={...d,key:path.key,m:pr.m,aheadSince:null};B.switched=true;}else{d=null;hop=true;why='new path ('+(B.prevKind||'?')+'>'+path.kind+')';}
      }
      B.prevKind=path.kind;
      if(!d){d={key:path.key,m:tg.m,v:tg.v,aheadSince:null,pos:null};B.d=d;d.pos=path.line.at(d.m);d.jump=hop;d.how=tg.how;if(hop&&!this.planning)this.stats.hops[why]=(this.stats.hops[why]||0)+1;continue;}
      B.d=d;d.jump=false;
      let goal=tg.m;
      // a stretch it can't be drawn along: go up to its start, then fade across
      const F=B.id===this.focus;            // HQ 4.63.2 (Rob 15 Nov 18:00, Follow Me in Hull): the followed bus is never faded
      if(!F)for(const [a,b] of path.hops){if(d.m<=a+0.5&&goal>a){if(d.m>=a-0.5&&goal>=b){d.m=b;d.jump=true;why='no way between reports';}else goal=Math.min(goal,a);break;}}
      const gap=goal-d.m;
      if(!d.jump){
        if(path.predictive){
          const base=tg.v,vmax=Math.min(P.vmaxAbs,Math.max(base*P.vmaxShare,base+P.vmaxAdd));
          if(F&&(gap>P.jumpGap||(gap>60&&gap/Math.max(0.5,vmax-base)>P.catchMaxS))){this.focusStep(d,gap,base,dt);}
          else if(gap>P.jumpGap||(gap>60&&gap/Math.max(0.5,vmax-base)>P.catchMaxS)){d.m=goal;d.v=base;d.jump=true;d.aheadSince=null;why='too far behind';}
          else if(!F&&gap<-P.aheadFadeM&&d.aheadSince!==null&&t-d.aheadSince>=P.aheadFadeS){d.m=goal;d.v=base;d.jump=true;d.aheadSince=null;why='ahead too long';}   // (followed: waits)
          else{
            if(gap<-P.aheadFadeM){if(d.aheadSince===null)d.aheadSince=t;}else d.aheadSince=null;
            let want=Math.max(0,Math.min(vmax,base+gap/P.TC));
            const dv=want-d.v;d.v+=Math.max(-P.accDown*dt,Math.min(P.accUp*dt,dv));d.v=Math.max(0,d.v);
            d.m=Math.min(path.line.length,d.m+d.v*dt);
          }
        }else{
          // no path ahead: travel to the newest report at the bus's own pace (faster if far behind), never past it
          if(F&&gap>P.jumpGap)this.focusStep(d,gap,0,dt);
          else if(gap>P.jumpGap){d.m=goal;d.v=0;d.jump=true;why='too far behind';}
          else{const left=tg.arriveBy-t,fast=Math.min(P.vmaxAbs,Math.max(tg.glideV*P.vmaxShare,tg.glideV+P.vmaxAdd,gap/P.glideTC));
            const want=gap<=0.5?0:(left>2?Math.min(fast,gap/left):fast);
            const dv=want-d.v;d.v+=Math.max(-P.accDown*dt,Math.min(P.accUp*dt,dv));d.v=Math.max(0,d.v);
            d.m=Math.min(goal,d.m+d.v*dt);if(d.m>=goal-0.05)d.v=Math.min(d.v,want);}
        }
      }
      if(d.jump&&!this.planning){const k=path.kind+': '+why;this.stats.hops[k]=(this.stats.hops[k]||0)+1;}
      const was=d.pos;d.pos=path.line.at(d.m);
      if(was&&!d.jump){// self-check: did the icon move backwards along its own line this second?
        const a=path.line.at(Math.max(0,d.m-5)),b=path.line.at(Math.min(path.line.length,d.m+5)),k=Math.cos(d.pos[1]*Math.PI/180)*MPD;
        const tx=(b[0]-a[0])*k,ty=(b[1]-a[1])*MPD,L=Math.hypot(tx,ty),dx=(d.pos[0]-was[0])*k,dy=(d.pos[1]-was[1])*MPD;
        if(L>0&&(dx*tx+dy*ty)/L<-2&&!this.planning){const kk=path.kind+(B.switched?' after new path':'');this.stats.backs=this.stats.backs||{};this.stats.backs[kk]=(this.stats.backs[kk]||0)+1;}}
      B.switched=false;
      d.how=d.m>tg.mn+2?(tg.how):(path.predictive?'on reported path':'on reported path');
      if(tg.how.startsWith('held'))d.how=tg.how;
    }
  }
  // HQ 4.63.2: the followed bus, far behind, closes the gap smoothly (forwards only, never a fade)
  focusStep(d,gap,base,dt){const move=Math.max(d.v*dt,Math.max(0,gap)*Math.min(1,dt/this.P.focusCatchS));d.m+=Math.min(move,Math.max(0,gap));d.v=Math.max(base,Math.min(move/Math.max(dt,0.001),this.P.vmaxAbs));
    if(!this.planning)this.stats.focusSaves++;}
  // HQ 4.63.0: positions for the next H seconds from t0 (one a second) WITHOUT changing the engine's state, so the page
  // can be given a track to follow until the next tile read. {id: [[t, lon, lat, jump, how, predictive], ...]}
  plan(t0,H){
    const saved=[];for(const B of this.buses.values())saved.push([B,B.d?{...B.d}:null,B.switched,B.prevKind]);
    this.planning=true;const out={};
    const take=t=>{for(const B of this.buses.values()){const w=this.where(B.id);if(!w)continue;
      (out[B.id]||(out[B.id]=[])).push([t,w.lon,w.lat,w.jump,w.how,!!(B.path&&B.path.predictive)]);}};
    try{if(![...this.buses.values()].every(B=>B.d))this.step(t0,0);take(t0);for(let k=1;k<=H;k++){this.step(t0+k,1);take(t0+k);}}
    finally{this.planning=false;for(const [B,d,sw,pk] of saved){B.d=d;B.switched=sw;B.prevKind=pk;}}
    return out;}
  where(id){const B=this.buses.get(id);if(!B||!B.d||!B.d.pos)return null;
    return {lon:B.d.pos[0],lat:B.d.pos[1],jump:!!B.d.jump,how:B.d.how,kind:B.path.kind,newest:B.path.fx[B.path.fx.length-1][0]};}
}
const api={FreshEngine,Line,Learner,P0,metres,quantile};
if(typeof module==='object'&&module.exports)module.exports=api;else root.TPA_FRESH=api;
})(typeof window!=='undefined'?window:globalThis);
