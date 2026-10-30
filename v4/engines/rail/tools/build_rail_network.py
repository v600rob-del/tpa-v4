"""Builds data/rail-network.json.gz from Network Rail's track model (NetworkLinks shapefile,
EIR release under the Open Government Licence v3.0, github.com/openraildata/network-rail-gis).
Usage: python3 build_rail_network.py <network-model folder> <output .json.gz>
Standard library only (small built-in shapefile reader + OSGB36->WGS84 conversion)."""
import struct, math
def read_dbf(path):
    f=open(path,'rb'); h=f.read(32); n,hl,rl=struct.unpack('<IHH',h[4:12]); fields=[]
    while True:
        d=f.read(32)
        if d[0]==0x0D: break
        fields.append((d[:11].split(b'\0')[0].decode(), chr(d[11]), d[16]))
    f.seek(hl); rows=[]
    for _ in range(n):
        rec=f.read(rl); pos=1; row={}
        for name,t,l in fields:
            row[name]=rec[pos:pos+l].decode('latin-1').strip(); pos+=l
        rows.append(row)
    return fields,rows
def read_shp(path):
    data=open(path,'rb').read(); pos=100; out=[]
    while pos<len(data):
        num,clen=struct.unpack('>ii',data[pos:pos+8]); pos+=8; rec=data[pos:pos+clen*2]; pos+=clen*2
        st=struct.unpack('<i',rec[:4])[0]
        if st==0: out.append(None); continue
        if st in (1,11,21): out.append([struct.unpack('<2d',rec[4:20])]); continue
        nparts,npts=struct.unpack('<2i',rec[36:44]); parts=struct.unpack(f'<{nparts}i',rec[44:44+4*nparts]); p0=44+4*nparts
        pts=[struct.unpack('<2d',rec[p0+16*i:p0+16*i+16]) for i in range(npts)]
        out.append([pts[parts[i]:(parts[i+1] if i+1<nparts else npts)] for i in range(nparts)])
    return out

def osgb_to_wgs84(E, N):
    """OSGB36 National Grid -> WGS84 lon/lat (Ordnance Survey formulas + Helmert, ~5 m)."""
    a, b = 6377563.396, 6356256.909; F0 = 0.9996012717
    lat0, lon0 = math.radians(49), math.radians(-2); N0, E0 = -100000, 400000
    e2 = 1 - (b*b)/(a*a); n = (a-b)/(a+b)
    lat, M = lat0, 0.0
    while True:
        lat = (N - N0 - M)/(a*F0) + lat
        M = b*F0*((1+n+1.25*n*n+1.25*n**3)*(lat-lat0) - (3*n+3*n*n+21/8*n**3)*math.sin(lat-lat0)*math.cos(lat+lat0)
                  + (15/8*n*n+15/8*n**3)*math.sin(2*(lat-lat0))*math.cos(2*(lat+lat0)) - 35/24*n**3*math.sin(3*(lat-lat0))*math.cos(3*(lat+lat0)))
        if abs(N - N0 - M) < 0.00001: break
    sl, cl, tl = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a*F0/math.sqrt(1-e2*sl*sl); rho = a*F0*(1-e2)/(1-e2*sl*sl)**1.5; eta2 = nu/rho - 1
    VII = tl/(2*rho*nu); VIII = tl/(24*rho*nu**3)*(5+3*tl*tl+eta2-9*tl*tl*eta2); IX = tl/(720*rho*nu**5)*(61+90*tl*tl+45*tl**4)
    X = 1/(cl*nu); XI = 1/(cl*6*nu**3)*(nu/rho+2*tl*tl); XII = 1/(cl*120*nu**5)*(5+28*tl*tl+24*tl**4)
    XIIA = 1/(cl*5040*nu**7)*(61+662*tl*tl+1320*tl**4+720*tl**6)
    dE = E - E0
    lat1 = lat - VII*dE**2 + VIII*dE**4 - IX*dE**6
    lon1 = lon0 + X*dE - XI*dE**3 + XII*dE**5 - XIIA*dE**7
    # OSGB36 lat/lon -> cartesian -> Helmert -> WGS84
    H = 0; sl, cl = math.sin(lat1), math.cos(lat1)
    nu = a/math.sqrt(1-e2*sl*sl)
    x = (nu+H)*cl*math.cos(lon1); y = (nu+H)*cl*math.sin(lon1); z = ((1-e2)*nu+H)*sl
    tx, ty, tz = 446.448, -125.157, 542.060; s = -20.4894e-6
    rx, ry, rz = [math.radians(v/3600) for v in (0.1502, 0.2470, 0.8421)]
    x2 = tx + (1+s)*x - rz*y + ry*z; y2 = ty + rz*x + (1+s)*y - rx*z; z2 = tz - ry*x + rx*y + (1+s)*z
    a2, b2 = 6378137.0, 6356752.3142; e22 = 1 - (b2*b2)/(a2*a2)
    p = math.hypot(x2, y2); lat2 = math.atan2(z2, p*(1-e22))
    for _ in range(10):
        nu2 = a2/math.sqrt(1-e22*math.sin(lat2)**2); lat2 = math.atan2(z2+e22*nu2*math.sin(lat2), p)
    return math.degrees(math.atan2(y2, x2)), math.degrees(lat2)


import sys, json, gzip, time, collections
B=sys.argv[1]; OUT=sys.argv[2]
t0=time.time()
_,rows=read_dbf(B+'/VectorLinks/NetworkLinks.dbf'); shapes=read_shp(B+'/VectorLinks/NetworkLinks.shp')
def dp(pts,tol):
    if len(pts)<3: return pts
    keep=[False]*len(pts); keep[0]=keep[-1]=True; st=[(0,len(pts)-1)]
    while st:
        i,j=st.pop(); (x1,y1),(x2,y2)=pts[i],pts[j]; dx,dy=x2-x1,y2-y1; L=math.hypot(dx,dy) or 1e-9; best,bi=-1,-1
        for k in range(i+1,j):
            x,y=pts[k]; d=abs(dy*x-dx*y+x2*y1-y2*x1)/L
            if d>best: best,bi=d,k
        if best>tol: keep[bi]=True; st+= [(i,bi),(bi,j)]
    return [p for p,k in zip(pts,keep) if k]
SNAP=3.0
grid={}; nodes=[]
def node(x,y):
    gx,gy=int(x//SNAP),int(y//SNAP)
    for ax in (gx-1,gx,gx+1):
        for ay in (gy-1,gy,gy+1):
            for nid in grid.get((ax,ay),()):
                nx,ny=nodes[nid]
                if (nx-x)**2+(ny-y)**2<=SNAP*SNAP: return nid
    nodes.append((x,y)); grid.setdefault((gx,gy),[]).append(len(nodes)-1); return len(nodes)-1
edges=[]
for rec,parts in zip(rows,shapes):
    if not parts: continue
    for pts in parts:
        if len(pts)<2: continue
        a=node(*pts[0]); b=node(*pts[-1])
        if a==b and len(pts)<3: continue
        L=sum(math.hypot(pts[i+1][0]-pts[i][0],pts[i+1][1]-pts[i][1]) for i in range(len(pts)-1))
        edges.append([a,b,L,dp(pts,8.0)[1:-1]])
print('raw',len(nodes),'nodes',len(edges),'edges',round(time.time()-t0,1),'s')
# merge chains through degree-2 nodes
adj=collections.defaultdict(list)
for i,(a,b,L,mid) in enumerate(edges): adj[a].append(i); adj[b].append(i)
alive=[True]*len(edges)
def other(e,n): return edges[e][1] if edges[e][0]==n else edges[e][0]
def oriented(e,frm):
    a,b,L,mid=edges[e]
    return (mid if a==frm else mid[::-1])
merged=[]
used=[False]*len(edges)
for n in range(len(nodes)):
    if len(adj[n])==2: continue
    for e in adj[n]:
        if used[e]: continue
        chain_pts=[]; L=0; cur=n; ce=e
        while True:
            used[ce]=True; nxt=other(ce,cur)
            chain_pts+=oriented(ce,cur); L+=edges[ce][2]
            if len(adj[nxt])!=2 or nxt==n: break
            chain_pts.append(nodes[nxt])
            e2=[x for x in adj[nxt] if x!=ce][0]
            if used[e2]: break
            cur,ce=nxt,e2
        merged.append([n,nxt,L,dp([nodes[n]]+chain_pts+[nodes[nxt]],8.0)[1:-1]])
# pure loops of degree-2 nodes are ignored
used_nodes=sorted({x for m in merged for x in m[:2]}); idx={n:i for i,n in enumerate(used_nodes)}
ll=[osgb_to_wgs84(*nodes[n]) for n in used_nodes]
out={'source':'Network Rail track model (NetworkLinks, EIR release, Open Government Licence v3.0) via github.com/openraildata/network-rail-gis',
     'nodes':[[round(lo,5),round(la,5)] for lo,la in ll],
     'edges':[[idx[a],idx[b],round(L),[ [round(v,5) for v in osgb_to_wgs84(*p)] for p in mid]] for a,b,L,mid in merged]}
raw=json.dumps(out,separators=(',',':')).encode()
gzip.open(OUT,'wb',compresslevel=9).write(raw)
print('merged',len(used_nodes),'nodes',len(merged),'edges', 'points',sum(len(m[3]) for m in merged), 'json MB',round(len(raw)/1e6,1),'gz MB',round(len(gzip.compress(raw,9))/1e6,1), round(time.time()-t0,1),'s')
