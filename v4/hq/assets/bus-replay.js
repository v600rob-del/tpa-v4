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
  // ---- HQ 4.61.0 (forecast plan Stage 3; Rob 14 Nov 2026 21:55 "Yes, build it now"): a bus shown about where it is NOW.
  // The engine (bus 0.22.0) publishes each timetabled bus's forecast f = [[clock time, metres along its line], ...] from
  // its newest report forward. The page moves the bus along it in real time (no delay), with the page's own rules for a
  // new forecast (as 4.1.9/4.1.14 for estimates):
  //  - shown AHEAD of the new forecast: it never slides back - it waits where it is until the forecast catches up
  //    (at most HOLD_MAX_S; further than that it is put where it should be, which the page fades);
  //  - shown BEHIND: it catches up gradually, at most CATCHUP_SHARE faster than its own pace, within CATCHUP_MAX_S
  //    (further than that: put where it should be, faded).
  const HOLD_MAX_S=20,CATCHUP_SHARE=0.4,CATCHUP_MAX_S=30,SAME_M=1;
  function alongAt(seq,t){                       // metres at time t on [[t,m],...] (held before the first / after the last)
    if(t<=seq[0][0])return seq[0][1];
    for(let k=1;k<seq.length;k++)if(t<=seq[k][0]){const [t0,m0]=seq[k-1],[t1,m1]=seq[k];return t1>t0?m0+(m1-m0)*(t-t0)/(t1-t0):m1;}
    return seq[seq.length-1][1];
  }
  function timeAt(seq,m){                        // first time the sequence reaches m metres (null if it never does)
    if(m<=seq[0][1])return seq[0][0];
    for(let k=1;k<seq.length;k++)if(seq[k][1]>=m){const [t0,m0]=seq[k-1],[t1,m1]=seq[k];return m1>m0?t0+(t1-t0)*(m-m0)/(m1-m0):t1;}
    return null;
  }
  function paceAt(seq,t){                        // metres a second at time t (the next stretch with any movement)
    for(let k=1;k<seq.length;k++)if(seq[k][0]>t&&seq[k][1]>seq[k-1][1])return (seq[k][1]-seq[k-1][1])/Math.max(1e-6,seq[k][0]-seq[k-1][0]);
    return 0;
  }
  function forecastMarks(f,now,prev){
    const want=alongAt(f,now),later=f.filter(q=>q[0]>now);
    const was=Array.isArray(prev)&&prev.length?alongAt(prev,now):null;
    if(was!==null&&was>want+SAME_M){                     // ahead: wait here until the forecast reaches this spot
      const tc=timeAt(f,was);
      if(tc!==null&&tc-now<=HOLD_MAX_S){const out=[[now,was]];if(tc>now)out.push([tc,was]);
        for(const q of f)if(q[0]>tc&&q[1]>=was)out.push([q[0],q[1]]);return {marks:out,how:'waiting'};}
      return {marks:[[now,want]].concat(later),how:'faded'};
    }
    if(was!==null&&was<want-SAME_M){                     // behind: catch up gently
      const pace=Math.max(3,paceAt(f,now)),gap=want-was,dur=gap/(CATCHUP_SHARE*pace);
      if(dur<=CATCHUP_MAX_S){const te=now+Math.max(5,dur),out=[[now,was],[te,alongAt(f,te)]];
        for(const q of f)if(q[0]>te)out.push([q[0],q[1]]);return {marks:out,how:'catching up'};}
      return {marks:[[now,want]].concat(later),how:'faded'};
    }
    return {marks:[[now,want]].concat(later),how:'on time'};
  }
  // The track for the page: points along the line through every corner, at their clock times. `prev` = this bus's marks
  // from the page's last frame on the same line (or null).
  function forecastTrack(shape,f,now,keepLeftM,prev){
    if(!Array.isArray(f)||f.length<2)return null;
    for(let k=0;k<f.length;k++){const q=f[k];if(!Array.isArray(q)||q.length!==2||!isFinite(q[0])||!isFinite(q[1])||(k&&(q[0]<=f[k-1][0]||q[1]<f[k-1][1])))return null;}
    const {marks,how}=forecastMarks(f,now,prev);
    if(marks.length===1||now>=f[f.length-1][0]){const p=shape.pointAt(marks[marks.length-1][1]);
      return {position:p,points:[p],times:[r1(now)],marks:[[now,marks[marks.length-1][1]]],how:'arrived',waiting:true};}
    let out=[];
    for(let z=0;z+1<marks.length;z++){const [ta,ma]=marks[z],[tb,mb]=marks[z+1];
      if(!out.length)out.push([ta,ma]);
      if(mb>ma)for(const m of shape.verticesBetween(ma,mb))out.push([ta+(tb-ta)*(m-ma)/(mb-ma),m]);
      out.push([tb,mb]);}
    if(out.length>MAX_POINTS){const step=(out.length-1)/(MAX_POINTS-1),keep=[];for(let j=0;j<MAX_POINTS;j++)keep.push(out[Math.round(j*step)]);out=keep;}
    let pts=out.map(x=>shape.pointAt(x[1]));
    pts=keepLeft(pts,keepLeftM||0,pts[0][1]);
    const times=out.map(x=>r1(x[0]));
    for(let k=1;k<times.length;k++)times[k]=Math.max(times[k],times[k-1]);
    return {position:pts[0],points:pts,times,marks,how,waiting:false};
  }
  // ---- HQ 4.62.0 (Rob 15 Nov 2026 01:48: live test of the motion study's "fresh" candidates; test addresses only).
  // The exact rules of v4/tools/motion_study.py (simulate, 'fresh'): a bus on its route line is shown at its newest report
  // carried on at a share of its own recent speed (reports within 2 min, at most 15 m/s), for at most maxS seconds and
  // maxM metres, then held. Each second: more than 150 m from that target -> put there (a jump: the page fades it);
  // behind -> catches up at most 50% faster than its pace (at least +2 m/s); more than 30 m ahead -> waits, and after
  // 20 s is put back (a fade); a few metres ahead -> stays put. Never drawn going backwards otherwise.
  // C4: 60% of its speed, 4 min; C3: 75%, 3 min (no distance limit in either: 2,000 m).
  const FRESH={c4:[0.6,240,2000],c3:[0.75,180,2000]},F_WINDOW=120,F_CAP=15,F_JUMP=150,F_HOLD=20,F_CATCH=0.5,F_AHEAD=30,F_HORIZON=25;
  function freshTarget(placed,tau,careful,maxS,maxM){
    const [T,m]=placed[placed.length-1];let v=0;
    const back=placed.filter(k=>T-k[0]<=F_WINDOW&&k[0]<T);
    if(back.length&&T>back[0][0])v=Math.max(0,Math.min(F_CAP,(m-back[0][1])/(T-back[0][0])));
    return [m+Math.min(maxM,careful*v*Math.max(0,Math.min(tau-T,maxS))),v];
  }
  // One frame: positions from `now` for F_HORIZON s (one a second, as the study), starting from where the bus was drawn a
  // second ago (prev = this bus's last frame on the same line: {marks, aheadSince}). Returns the page's track (points
  // through every corner, jumps as two points 0.1 s apart so the page fades them) and the state for the next frame.
  function freshTrack(shape,placed,now,keepLeftM,prev,which){
    const cfg=FRESH[which];if(!cfg||!Array.isArray(placed)||!placed.length)return null;
    const [careful,maxS,maxM]=cfg;
    let cur=prev&&Array.isArray(prev.marks)&&prev.marks.length?alongAt(prev.marks,now-1):null;
    let ahead=null;                                   // waiting-ahead clock as it stood a second ago (last frame's record)
    if(prev&&Array.isArray(prev.aheads))for(const [t,a] of prev.aheads){if(t<=now-1)ahead=a;else break;}
    const marks=[],aheads=[],how={};let first=null;
    for(let k=0;k<=F_HORIZON;k++){
      const tau=now+k,[target,v]=freshTarget(placed,tau,careful,maxS,maxM),was=cur;let what;
      if(cur===null||Math.abs(target-cur)>F_JUMP){cur=target;ahead=null;what=was===null?'first sight':'jump';}
      else if(target>cur){const pace=Math.max(v*careful,0);cur=Math.min(target,cur+Math.max(pace*(1+F_CATCH),pace+2));ahead=null;what='moving';}
      else if(target<cur-F_AHEAD){if(ahead===null){ahead=tau;what='waiting';}else if(tau-ahead>=F_HOLD){cur=target;ahead=null;what='put back';}else what='waiting';}
      else{ahead=null;what='held';}
      if(k===0)first=what;
      if(was!==null&&k>0&&Math.abs(cur-was)>F_AHEAD)marks.push([tau-0.1,was]);     // a jump inside the frame: drawn as a fade
      marks.push([tau,cur]);aheads.push([tau,ahead]);how[what]=(how[what]||0)+1;
    }
    let out=[];
    for(let z=0;z+1<marks.length;z++){const [ta,ma]=marks[z],[tb,mb]=marks[z+1];
      if(!out.length)out.push([ta,ma]);
      if(mb>ma&&tb-ta>0.15)for(const m of shape.verticesBetween(ma,mb))out.push([ta+(tb-ta)*(m-ma)/(mb-ma),m]);
      out.push([tb,mb]);}
    if(out.length>MAX_POINTS)out=out.slice(0,MAX_POINTS);
    let pts=out.map(x=>shape.pointAt(x[1]));
    pts=keepLeft(pts,keepLeftM||0,pts[0][1]);
    const times=out.map(x=>Math.round(x[0]*10)/10);
    for(let k=1;k<times.length;k++)times[k]=Math.max(times[k],times[k-1]);
    return {position:pts[0],points:pts,times,marks,aheads,first,how};
  }
  const api={Shape,track,straight,keepLeft,forecastTrack,forecastMarks,alongAt,freshTrack,FRESH};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.TPA_BUS_REPLAY=api;
})(typeof window!=='undefined'?window:globalThis);
