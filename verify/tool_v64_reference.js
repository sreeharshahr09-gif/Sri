/* Functions extracted verbatim from Tread_Pattern_Stiffness_Estimation_Tool_v6.4.html.
   Used only to cross-check the Python port in tread_eval/stiffness.py.
   Do not edit -- this is the reference implementation. */

const SHORE_K_TABLE = { 30:0.93, 40:0.85, 50:0.73, 60:0.64, 70:0.57 };

const SHORE_E_TABLE = { 30:1.50, 40:2.50, 50:4.00, 60:6.89, 70:12.00 };

function calcG(E,nu){return E/(2*(1+nu))}

function ensureCCW(pts){
  let x=pts.map(p=>p[0]),y=pts.map(p=>p[1]);
  let xn=[...x.slice(1),x[0]],yn=[...y.slice(1),y[0]];
  let s=0.5*x.reduce((a,xi,i)=>a+xi*yn[i]-xn[i]*y[i],0);
  return s<0?[...pts].reverse():pts;
}

function polygonProps(verts){
  const n=verts.length;if(n<3)return null;
  // Enforce CCW ordering so that signed area `As` is positive.
  // Without this, CW input flips the sign of Ixy (off-diagonal) while
  // Ixx/Iyy survive due to Math.abs below — silently corrupting Kxy
  // for any non-symmetric polygon. See audit C1.
  verts=ensureCCW(verts);
  const x=verts.map(v=>v[0]),y=verts.map(v=>v[1]);
  const xn=[...x.slice(1),x[0]],yn=[...y.slice(1),y[0]];
  const cross=x.map((xi,i)=>xi*yn[i]-xn[i]*y[i]);
  const As=0.5*cross.reduce((a,b)=>a+b,0);
  const A=Math.abs(As);if(A<1e-12)return null;
  const cx=cross.reduce((a,c,i)=>a+(x[i]+xn[i])*c,0)/(6*As);
  const cy=cross.reduce((a,c,i)=>a+(y[i]+yn[i])*c,0)/(6*As);
  const Ixxo=cross.reduce((a,c,i)=>a+(y[i]**2+y[i]*yn[i]+yn[i]**2)*c,0)/12;
  const Iyyo=cross.reduce((a,c,i)=>a+(x[i]**2+x[i]*xn[i]+xn[i]**2)*c,0)/12;
  const Ixyo=cross.reduce((a,c,i)=>a+((x[i]*yn[i]) + 2*x[i]*y[i] + 2*xn[i]*yn[i] + xn[i]*y[i])*c,0)/24;
  const Ixxc=Ixxo - As*cy**2;
  const Iyyc=Iyyo - As*cx**2;
  const Ixyc=Ixyo - As*cx*cy;
  let perimeter=0;
  for(let i=0;i<n;i++) perimeter += Math.hypot(xn[i]-x[i], yn[i]-y[i]);
  return{A,cx,cy,Ixx:Math.abs(Ixxc),Iyy:Math.abs(Iyyc),Ixy:Ixyc,perimeter}
}

function polygonPerimeter(verts){
  const p=polygonProps(verts);
  return p ? p.perimeter : 0;
}

function offsetPoly(verts,draftDeg,z){
  if(!draftDeg||z<1e-12)return verts;
  let pts=ensureCCW(verts),n=pts.length;
  // Edge i is the segment from pts[i] to pts[(i+1)%n].
  // Its outward normal (for CCW polygon) is (dy, -dx)/len.
  let normals=[];
  for(let i=0;i<n;i++){let j=(i+1)%n,dx=pts[j][0]-pts[i][0],dy=pts[j][1]-pts[i][1],len=Math.sqrt(dx*dx+dy*dy);normals.push(len>1e-12?[dy/len,-dx/len]:[0,0])}
  // Per-edge offset magnitude (positive = outward = base wider than top).
  let offsets=typeof draftDeg==='number'?Array(n).fill(z*Math.tan(draftDeg*Math.PI/180)):draftDeg.map(a=>z*Math.tan(a*Math.PI/180));
  // Build each offset edge: shift both endpoints of edge i outward along edge i's normal.
  let edges=[];
  for(let i=0;i<n;i++){let j=(i+1)%n,d=offsets[i];edges.push([[pts[i][0]+d*normals[i][0],pts[i][1]+d*normals[i][1]],[pts[j][0]+d*normals[i][0],pts[j][1]+d*normals[i][1]]])}
  // Output vertex i = intersection of offset-edge (i-1) and offset-edge i.
  // Edge (i-1) runs into vertex i; edge i runs out of vertex i. Their intersection
  // is the offset of vertex i itself, so output[i] corresponds to input[i].
  let out=[];
  for(let i=0;i<n;i++){
    let iPrev=(i-1+n)%n;
    let [p1,p2]=edges[iPrev], [p3,p4]=edges[i];
    let d1=[p2[0]-p1[0],p2[1]-p1[1]], d2=[p4[0]-p3[0],p4[1]-p3[1]];
    let cr=d1[0]*d2[1]-d1[1]*d2[0];
    if(Math.abs(cr)<1e-12){
      // Parallel edges (e.g. two collinear edges): fall back to the offset of the
      // original vertex along the average of the two edge normals.
      out.push([0.5*(p2[0]+p3[0]), 0.5*(p2[1]+p3[1])]);
    } else {
      let t=((p3[0]-p1[0])*d2[1]-(p3[1]-p1[1])*d2[0])/cr;
      out.push([p1[0]+t*d1[0], p1[1]+t*d1[1]]);
    }
  }
  // Audit N1: detect self-inversion (extreme negative draft eats more than
  // half the smallest dimension and flips the polygon). polygonProps would
  // otherwise return |As| and silently use garbage Ixx/Iyy.
  let xs=out.map(p=>p[0]),ys=out.map(p=>p[1]);
  let xn2=[...xs.slice(1),xs[0]],yn2=[...ys.slice(1),ys[0]];
  let sOut=0.5*xs.reduce((a,xi,i)=>a+xi*yn2[i]-xn2[i]*ys[i],0);
  if(sOut<=1e-9){
    if(typeof console!=='undefined' && console.warn){
      console.warn('offsetPoly: cross-section inverted/collapsed at z='+z+' (draft too negative for geometry). Returning original vertices as fallback.');
    }
    return pts;
  }
  return out;
}

function invertSymMat2(M){
  const det = M.xx*M.yy - M.xy*M.xy;
  if(Math.abs(det) < 1e-18) return {xx:0, yy:0, xy:0};
  return {xx: M.yy/det, yy: M.xx/det, xy: -M.xy/det};
}

function beamKMatrix(A, Ixx, Iyy, Ixy, L, E, G, mode){
  if(A<=0 || L<=0 || Ixx<=0 || Iyy<=0) return {xx:0, yy:0, xy:0};
  const detI = Ixx*Iyy - Ixy*Ixy;
  if(detI<=1e-15) return {xx:0, yy:0, xy:0};
  const cB = mode==='parallel' ? (L*L*L)/(12*E) : (L*L*L)/(3*E);
  const cS = mode==='parallel' ? L/(G*A)        : (6*L)/(5*G*A);
  // Bending compliance = L³/(12E·det) * [[Ixx, -Ixy], [-Ixy, Iyy]]
  // Shear compliance   = (L/GA) * I   (isotropic — same in both directions)
  // NOTE: The Sheridan (1992) Eq 8.5a bending correction to shear is NOT applied here.
  // That correction was derived for a model with NO separate bending term.
  // The Okonieski model already has explicit C_bend + C_shear decomposition,
  // so the Sheridan correction would double-count bending and artificially
  // equalize Kx and Ky by collapsing the directional sensitivity.
  const Cxx = cB * Ixx/detI + cS;
  const Cyy = cB * Iyy/detI + cS;
  const Cxy = -cB * Ixy/detI;   // note MINUS sign (from inverse of K_bend)
  const detC = Cxx*Cyy - Cxy*Cxy;
  if(detC<=1e-18) return {xx:0, yy:0, xy:0};
  return {
    xx:  Cyy/detC,   // Kxx  (= paper's Kx when Ixy=0)
    yy:  Cxx/detC,   // Kyy  (= paper's Ky when Ixy=0)
    xy: -Cxy/detC    // Kxy  — SAME sign as Ixy (paper Eq 4 convention)
  };
}

function effectiveK(d){
  const G = calcG(d.E, d.nu);
  const mode = d.mode==='parallel' ? 'parallel' : 'free';
  const nLat=d.nLat||0, nLong=d.nLong||0, sipeT=d.sipeT||0.5, sd=d.sipeDepth||d.nsd;
  const verts = d.vertices || [[0,0],[d.lx,0],[d.lx,d.wy],[0,d.wy]];

  // Build sipe geometry from counts (legacy path kept for compatibility)
  let sipes = d.explicitSipes && d.explicitSipes.length ? d.explicitSipes : [];
  if(!sipes.length && (nLat>0 || nLong>0)){
    const xs=verts.map(v=>v[0]), ys=verts.map(v=>v[1]);
    const xmin=Math.min(...xs), xmax=Math.max(...xs), ymin=Math.min(...ys), ymax=Math.max(...ys);
    const ext=Math.max(xmax-xmin, ymax-ymin)*0.2;
    for(let i=0;i<nLat;i++){  let f=(i+1)/(nLat+1);  sipes.push({p1:[xmin+f*(xmax-xmin), ymin-ext], p2:[xmin+f*(xmax-xmin), ymax+ext], depth:Math.min(sd,d.nsd), width:sipeT}); }
    for(let i=0;i<nLong;i++){ let f=(i+1)/(nLong+1); sipes.push({p1:[xmin-ext, ymin+f*(ymax-ymin)], p2:[xmax+ext, ymin+f*(ymax-ymin)], depth:Math.min(sd,d.nsd), width:sipeT}); }
  }

  // Polygon cross-section at height z (0 = base, nsd = top)
  const hasDraft = d.draft && Math.abs(d.draft) > 1e-12;
  function polyAt(z){
    if(!hasDraft) return verts;
    return offsetPoly(verts, d.draft, d.nsd - z);
  }

  const nSlices = 20;

  // ─── No sipes: Castigliano slice integration over the tapered beam ───
  //   For parallel-end: bending-moment weight w(z) = (L/2 - z)²     → ∫w/I_eff dz = bending compliance
  //   For free-end:     w(z) = (L - z)²
  //   The shear integral is ∫ dz/A (scaled by 1 or 6/5 depending on mode).
  //   For a constant section this reduces identically to the paper's Eq 1 / Eq 2.
  //   For a tapered section it captures the proper variation along height —
  //   no arbitrary "harmonic mean I + arithmetic mean Ixy" averaging.
  if(!sipes.length){
    const L = d.nsd;
    const nSl = 40;
    const dz = L / nSl;
    let Cxx_b=0, Cyy_b=0, Cxy_b=0;
    let Sh=0;
    let nValid=0;
    for(let i=0;i<nSl;i++){
      const z = (i+0.5)*dz;
      const poly = polyAt(z);
      const p = polygonProps(poly);
      if(!p || p.A<1e-9) continue;
      const Ixy = p.Ixy||0;
      const detI = p.Ixx*p.Iyy - Ixy*Ixy;
      if(detI <= 1e-15) continue;
      const w = (mode==='parallel') ? (z-L/2)*(z-L/2) : (L-z)*(L-z);
      // Bending compliance contribution: C_bend = (1/E) · ∫ w · (I⁻¹) dz
      // (I⁻¹) entries:  xx = Ixx/det,  yy = Iyy/det,  xy = -Ixy/det
      Cxx_b += w *  p.Ixx /detI * dz;
      Cyy_b += w *  p.Iyy /detI * dz;
      Cxy_b += w * (-Ixy) /detI * dz;
      Sh    += dz / p.A;
      nValid++;
    }
    if(nValid===0) return {Kx:0, Ky:0, Kxy:0, G, nSubs:1};
    const shearFactor = (mode==='parallel') ? 1 : 6/5;
    const Cxx = Cxx_b/d.E + shearFactor*Sh/G;
    const Cyy = Cyy_b/d.E + shearFactor*Sh/G;
    const Cxy = Cxy_b/d.E;
    const detC = Cxx*Cyy - Cxy*Cxy;
    if(detC <= 1e-18) return {Kx:0, Ky:0, Kxy:0, G, nSubs:1};
    const Kx  = Cyy/detC;
    const Ky  = Cxx/detC;
    const Kxy_raw = -Cxy/detC;               // same sign as Ixy (paper convention)
    const Kxy = (Math.abs(Kxy_raw) < 1e-6 * Math.max(Kx, Ky)) ? 0 : Kxy_raw;
    return {Kx, Ky, Kxy, G, nSubs:1};
  }

  // ─── With sipes: layers in series, sub-blocks in parallel ───
  //   Per sub-block: compute stiffness matrix about its own centroid.
  //   Per layer: sum stiffness matrices of its sub-blocks  (parallel springs, shared rigid face).
  //   Overall:   sum layer compliance matrices              (series springs along height).
  let trans = new Set([0, d.nsd]);
  sipes.forEach(s=>{ const h=d.nsd-s.depth; if(h>0 && h<d.nsd) trans.add(h); });
  trans = Array.from(trans).sort((a,b)=>a-b);

  let Ctot = {xx:0, yy:0, xy:0};
  let anyValid = false;

  for(let li=0; li<trans.length-1; li++){
    const zB=trans[li], zT=trans[li+1], lH=zT-zB;
    if(lH<1e-6) continue;
    const active = sipes.filter(s => s.depth >= (d.nsd-zB) - 1e-6);
    // Use mid-height polygon for this layer (representative cross-section for tapered slab)
    let layerPoly = polyAt((zB+zT)/2);
    let subs = [layerPoly];
    for(const s of active){
      const hasPts = s.points && s.points.length >= 2;
      let next=[];
      if(hasPts){
        for(const sp of subs) next.push(...splitByPolylineSipe(sp, s.points, s.width));
      } else {
        for(const sp of subs) next.push(...splitBySipe(sp, s.p1, s.p2, s.width));
      }
      subs = next;
    }
    subs = subs.filter(sp => { const p=polygonProps(sp); return p && p.A>1.0; });

    // Parallel sum of sub-block stiffness matrices
    let Klayer = {xx:0, yy:0, xy:0};
    for(const sp of subs){
      const p = polygonProps(sp);
      if(!p || p.A<1e-9) continue;
      const Ks = beamKMatrix(p.A, p.Ixx, p.Iyy, p.Ixy||0, lH, d.E, G, mode);
      Klayer.xx += Ks.xx;
      Klayer.yy += Ks.yy;
      Klayer.xy += Ks.xy;
    }
    // Invert layer stiffness to get its compliance, add to series total
    const Clayer = invertSymMat2(Klayer);
    if(Clayer.xx>0 && Clayer.yy>0){
      Ctot.xx += Clayer.xx;
      Ctot.yy += Clayer.yy;
      Ctot.xy += Clayer.xy;
      anyValid = true;
    }
  }

  if(!anyValid) return {Kx:0, Ky:0, Kxy:0, G, nSubs:(nLat+1)*(nLong+1)};

  const Ktot = invertSymMat2(Ctot);
  const Kxy = (Math.abs(Ktot.xy) < 1e-6 * Math.max(Ktot.xx, Ktot.yy)) ? 0 : Ktot.xy;
  return {Kx:Ktot.xx, Ky:Ktot.yy, Kxy, G, nSubs:(nLat+1)*(nLong+1)};
}

function computeKz(d){
  const verts = d.vertices || [[0,0],[d.lx,0],[d.lx,d.wy],[0,d.wy]];
  const props = polygonProps(verts);
  if(!props) return null;
  const A_gross = props.A;                  // mm²  gross polygon area
  const P       = polygonPerimeter(verts);  // mm   perimeter
  const NSD     = Math.max(d.nsd, 0.1);    // mm
  const E       = Math.max(d.E,   0.01);   // N/mm²
  const k       = getGentK(d);             // Shore-dependent Gent coefficient
  const Kb      = 1100;                    // N/mm²  bulk modulus of rubber (≈1.1 GPa)

  // Net contact area (gross minus sipe slots)
  const A_net = computeNetContactAreaFromSipes(verts, d.explicitSipes || []);
  const A     = A_net;

  // Shape factor (Gent 1959) — uses net area and gross perimeter
  const S      = A / (NSD * Math.max(P, 1e-9));   // dimensionless
  // Effective compression modulus (Gent-Lindley, bidirectional strain, Eq 8.7 Sheridan)
  const E_eff_uncorr = E * (1 + 2 * k * S * S);   // N/mm²  without bulk correction
  // Bulk compressibility correction (significant only when S > ~3, i.e. E_eff > ~100 N/mm²)
  const E_eff = E_eff_uncorr / (1 + E_eff_uncorr / Kb);  // N/mm²  corrected
  const bulkCorrFactor = E_eff / E_eff_uncorr;            // dimensionless, typically ≈1.0 for tread blocks
  const Kz    = E_eff * A / NSD;                          // N/mm

  return { Kz, S, E_eff, E_eff_uncorr, bulkCorrFactor, k, A, A_gross, A_net, P, NSD, E, Kb };
}

function computeNetContactAreaFromSipes(verts, explicitSipes){
  // Net Area = Gross Area − Σ(sipe_clipped_length × sipe_width)
  // This is the physically correct definition: rubber area minus the thin
  // slot cut by each sipe. The polygon-splitting approach was wrong because
  // the width-offset clipping over-removed area for angled/curved sipes.
  const grossProps = polygonProps(verts);
  if(!grossProps) return 0;
  const grossA = grossProps.A;

  const sipes = (explicitSipes||[]).filter(s => (s.depth||0) > 0 && (s.width||0) > 0);
  let removedArea = 0;
  for(const s of sipes){
    const clippedLen = sipeClippedLength(verts, s);
    removedArea += clippedLen * (s.width||0);
  }
  return Math.max(0, grossA - removedArea);
}

function shoreE(s){ return SHORE_E_TABLE[nearestShoreKey(+s)] ?? 6.89; }

function shoreK(s){ return SHORE_K_TABLE[nearestShoreKey(+s)] ?? 0.64; }

function nearestShoreKey(s){
  const keys = Object.keys(SHORE_K_TABLE).map(Number);
  return keys.reduce((best,k)=> Math.abs(k-s) < Math.abs(best-s) ? k : best, keys[0]);
}

function getGentK(d){
  const shore = d.perfShore || 60;
  if(shore === 'custom') return Math.max(0.1, Math.min(1.5, d.perfKCustom || 0.64));
  return shoreK(shore);
}

function convexHull(points){
  const pts=(points||[]).map(p=>[+p[0],+p[1]]).sort((a,b)=>a[0]-b[0] || a[1]-b[1]);
  if(pts.length<=2) return pts;
  const cross=(o,a,b)=>((a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0]));
  const lower=[];
  for(const p of pts){
    while(lower.length>=2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 1e-12) lower.pop();
    lower.push(p);
  }
  const upper=[];
  for(let i=pts.length-1;i>=0;i--){
    const p=pts[i];
    while(upper.length>=2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 1e-12) upper.pop();
    upper.push(p);
  }
  upper.pop(); lower.pop();
  const hull=lower.concat(upper);
  return hull.length>=3 ? hull : pts;
}

function turningAngleExcess(points){
  const pts=simplifyPolygonForShapeMetrics(points||[]);
  const n=pts.length;
  if(n<3) return 0;
  let total=0;
  for(let i=0;i<n;i++){
    const p0=pts[(i-1+n)%n], p1=pts[i], p2=pts[(i+1)%n];
    const e1=[p1[0]-p0[0], p1[1]-p0[1]];
    const e2=[p2[0]-p1[0], p2[1]-p1[1]];
    const l1=Math.hypot(e1[0],e1[1]), l2=Math.hypot(e2[0],e2[1]);
    if(l1<1e-12 || l2<1e-12) continue;
    const cross=e1[0]*e2[1]-e1[1]*e2[0];
    const dot=e1[0]*e2[0]+e1[1]*e2[1];
    total += Math.abs(Math.atan2(cross,dot));
  }
  return Math.max(0, total/(2*Math.PI) - 1);
}

function axisAlignmentIndex(points){
  const pts=simplifyPolygonForShapeMetrics(points||[]);
  const n=pts.length;
  if(n<2) return null;
  let wsum=0, asum=0;
  for(let i=0;i<n;i++){
    const p1=pts[i], p2=pts[(i+1)%n];
    const dx=p2[0]-p1[0], dy=p2[1]-p1[1];
    const L=Math.hypot(dx,dy);
    if(L<1e-12) continue;
    const c=Math.abs(dx)/L, s=Math.abs(dy)/L;
    const align=Math.max(c,s); // 1.0 for axis-aligned, 0.707 for 45° edges
    wsum += L;
    asum += L*align;
  }
  return wsum>1e-12 ? asum/wsum : null;
}

function shapeFactor(d){
  if(!d.useShapeFactor) return {multiplier:1.0, compactness:null, concavity:null, edgeComplexity:null, axisAlignment:null, perimeter:null, mc:1.0, mq:1.0, me:1.0, md:1.0};
  const verts=(d.vertices||[]);
  const A = Math.max(d.contactArea || d.props?.A_contact || d.props?.A || 0, 0);
  const P = Math.max(d.props?.perimeter_contact || d.props?.perimeter || polygonPerimeter(verts) || 0, 0);
  if(!(A>0) || !(P>0) || verts.length<3) return {multiplier:1.0, compactness:null, concavity:null, edgeComplexity:null, axisAlignment:null, perimeter:P||null, mc:1.0, mq:1.0, me:1.0, md:1.0};
  const C = 4*Math.PI*A/(P*P);
  const alpha = Number.isFinite(d.shapeAlpha) ? d.shapeAlpha : 1.5;
  const cref  = 0.90;
  const mc = 1 + alpha*(C - cref);

  const hull=convexHull(verts);
  const hullProps=polygonProps(hull);
  const Ahull=Math.max(hullProps?.A || A, A);
  const Q=Math.max(0, Math.min(1, A/Ahull));
  const alphaQ = Number.isFinite(d.shapeAlphaQ) ? d.shapeAlphaQ : 1.8;
  const mq = 1 + alphaQ*(Q - 1);

  const Eturn = turningAngleExcess(verts);
  const alphaE = Number.isFinite(d.shapeAlphaE) ? d.shapeAlphaE : 0.18;
  const me = 1 - alphaE*Eturn;

  const Daxis = axisAlignmentIndex(verts);
  const alphaD = Number.isFinite(d.shapeAlphaD) ? d.shapeAlphaD : 1.25;
  const dref = 0.88;
  const md = Number.isFinite(Daxis) ? (1 + alphaD*(Daxis - dref)) : 1.0;

  const mRaw = mc * mq * me * md;
  const m = Math.max(0.65, Math.min(1.25, mRaw));
  return {multiplier:m, compactness:C, concavity:Q, edgeComplexity:Eturn, axisAlignment:Daxis, perimeter:P, mc, mq, me, md};
}

function splitBySipe(poly,p1,p2,width){
  let dx=p2[0]-p1[0],dy=p2[1]-p1[1],L=Math.sqrt(dx*dx+dy*dy);
  if(L<1e-12)return[poly];
  let nx=-dy/L*width/2,ny=dx/L*width/2;
  let above=clipPoly(poly,[p1[0]+nx,p1[1]+ny],[p2[0]+nx,p2[1]+ny],true);
  let below=clipPoly(poly,[p1[0]-nx,p1[1]-ny],[p2[0]-nx,p2[1]-ny],false);
  let r=[];if(above.length>=3)r.push(above);if(below.length>=3)r.push(below);
  return r.length?r:[poly];
}

function splitByPolylineSipe(poly, points, width){
  if(!points || points.length < 2) return [poly];
  if(points.length === 2){
    return splitBySipe(poly, points[0], points[1], width);
  }

  // ── Step 1: find where the polyline path crosses the polygon boundary ──
  // The polyline may start/end outside the polygon. We need the first and last
  // intersection with the polygon boundary (entry and exit points).
  // We also need to know which polygon edges they lie on.

  function segIntersect(ax,ay,bx,by, cx,cy,dx,dy){
    // Returns t in [0,1] along AB where AB intersects CD, or null
    const dxAB=bx-ax, dyAB=by-ay;
    const dxCD=dx-cx, dyCD=dy-cy;
    const denom = dxAB*dyCD - dyAB*dxCD;
    if(Math.abs(denom)<1e-10) return null;
    const t = ((cx-ax)*dyCD - (cy-ay)*dxCD) / denom;
    const u = ((cx-ax)*dyAB - (cy-ay)*dxAB) / denom;
    if(t>=-1e-9 && t<=1+1e-9 && u>=-1e-9 && u<=1+1e-9)
      return Math.max(0,Math.min(1,t));
    return null;
  }

  const n = poly.length;

  // Find all intersections of the polyline with the polygon boundary,
  // ordered along the polyline from start to end.
  let crossings = []; // {t_poly: global param, pt: [x,y], edgeIdx, t_edge}
  let polylineLen = 0;
  for(let si=0; si<points.length-1; si++){
    const p1=points[si], p2=points[si+1];
    const segLen = Math.hypot(p2[0]-p1[0], p2[1]-p1[1]);
    for(let ei=0; ei<n; ei++){
      const v1=poly[ei], v2=poly[(ei+1)%n];
      const t = segIntersect(p1[0],p1[1],p2[0],p2[1], v1[0],v1[1],v2[0],v2[1]);
      if(t !== null){
        const u_edge = segIntersect(v1[0],v1[1],v2[0],v2[1], p1[0],p1[1],p2[0],p2[1]);
        if(u_edge !== null){
          crossings.push({
            t_total: polylineLen + t*segLen,
            pt: [p1[0]+t*(p2[0]-p1[0]), p1[1]+t*(p2[1]-p1[1])],
            edgeIdx: ei,
            t_edge: Math.max(0,Math.min(1,u_edge))
          });
        }
      }
    }
    polylineLen += segLen;
  }

  // Sort crossings along polyline direction
  crossings.sort((a,b)=>a.t_total-b.t_total);

  // We need at least 2 crossings (entry + exit) to make a valid cut
  if(crossings.length < 2){
    // Polyline doesn't fully cross the polygon — fall back to straight cut
    // using first and last points
    return splitBySipe(poly, points[0], points[points.length-1], width);
  }

  const entry = crossings[0];
  const exit  = crossings[crossings.length-1];

  // ── Step 2: collect the polyline segment between entry and exit ──
  // These are the points that will form the shared cut boundary.
  let cutPts = [entry.pt];
  let insideStarted = false;
  for(let si=0; si<points.length-1; si++){
    const p1=points[si], p2=points[si+1];
    // Include p2 if it is "between" entry and exit along the polyline
    const segStart = (() => {
      let acc=0;
      for(let k=0; k<si; k++) acc+=Math.hypot(points[k+1][0]-points[k][0],points[k+1][1]-points[k][1]);
      return acc;
    })();
    const segEnd = segStart + Math.hypot(p2[0]-p1[0],p2[1]-p1[1]);
    if(segEnd > entry.t_total && segStart < exit.t_total){
      // Include p2 if it's between entry and exit
      if(segEnd <= exit.t_total + 1e-9){
        cutPts.push([...p2]);
      }
    }
  }
  cutPts.push(exit.pt);

  // ── Step 3: build the two sub-polygons by tracing polygon boundary ──
  // The polygon boundary is split at entry.edgeIdx (after t_edge) and
  // exit.edgeIdx (after t_edge). We traverse the boundary two ways —
  // from entry to exit clockwise, and from exit to entry clockwise —
  // and prepend/append the cut path.

  // Build ordered boundary vertices with insertion points
  // polygon vertices are at integer indices, insertion points at fractional
  // Build array: [ {pt, edgeIdx} ] for the polygon vertices
  const boundaryVerts = [];
  for(let i=0; i<n; i++) boundaryVerts.push({pt:[...poly[i]], edgeIdx:i-0.5});
  // Insert entry and exit into boundary in edge order
  // Entry lies on edge entry.edgeIdx between poly[entry.edgeIdx] and poly[(entry.edgeIdx+1)%n]
  // We tag them with their position along the boundary
  const entryPos = entry.edgeIdx + entry.t_edge;
  const exitPos  = exit.edgeIdx  + exit.t_edge;

  // Build left polygon: boundary from exit → entry (going forward), then cut reversed
  // Build right polygon: boundary from entry → exit (going forward), then cut

  function collectBoundarySegment(fromPos, toPos){
    // Collect polygon vertices strictly between fromPos and toPos
    // going forward (increasing edgeIdx mod n)
    const pts = [];
    let pos = fromPos;
    for(let steps=0; steps<n+1; steps++){
      const nextVertIdx = Math.floor(pos)+1; // next whole vertex
      const nextVertPos = nextVertIdx % n === 0 ? n : nextVertIdx;
      if(toPos > fromPos){
        // Normal case
        if(nextVertPos >= toPos) break;
        if(nextVertPos > fromPos) pts.push([...poly[nextVertIdx % n]]);
        pos = nextVertPos;
      } else {
        // Wrap-around case: fromPos > toPos means we cross the 0/n boundary
        if(nextVertPos >= n){
          // wrap
          const wrapped = 0;
          if(wrapped >= toPos) break;
          pts.push([...poly[0]]);
          pos = wrapped;
          // continue from 0
          for(let s2=0; s2<n; s2++){
            const nv2 = Math.floor(pos)+1;
            if(nv2 >= toPos) break;
            pts.push([...poly[nv2 % n]]);
            pos = nv2;
          }
          break;
        }
        pts.push([...poly[nextVertIdx % n]]);
        pos = nextVertPos;
        if(pos >= n) break;
      }
    }
    return pts;
  }

  // Simpler and more robust: just collect vertices by index ranges
  // Left polygon: entry.pt → [polygon vertices from (entry.edgeIdx+1) to exit.edgeIdx] → exit.pt → [cutPts reversed]
  // Right polygon: exit.pt → [polygon vertices from (exit.edgeIdx+1) to entry.edgeIdx] → entry.pt → [cutPts]

  function polyVertsFrom(startEdge, endEdge){
    // Collect poly vertices starting from vertex (startEdge+1)%n up to and including vertex endEdge
    // going forward, wrapping around if needed
    const pts=[];
    let idx = (startEdge+1) % n;
    for(let steps=0; steps<n; steps++){
      pts.push([...poly[idx]]);
      if(idx === (endEdge % n)) break;
      idx = (idx+1) % n;
    }
    return pts;
  }

  const leftVerts  = [entry.pt, ...polyVertsFrom(entry.edgeIdx, exit.edgeIdx),  exit.pt,  ...[...cutPts].reverse()];
  const rightVerts = [exit.pt,  ...polyVertsFrom(exit.edgeIdx,  entry.edgeIdx), entry.pt, ...cutPts];

  // ── Step 4: apply sipe width offset ──
  // Remove a thin slot by applying the scalar splitBySipe to each half
  // against the two offset lines of the first and last cut segments
  // This is equivalent to what the straight sipe does.
  // For simplicity, apply the width exclusion as a final clip of each half
  // against the near-side offset of the full cut path.
  let leftResult  = leftVerts.length  >= 3 ? leftVerts  : null;
  let rightResult = rightVerts.length >= 3 ? rightVerts : null;

  if(width > 0 && cutPts.length >= 2){
    // Clip left half to keep only the part further than width/2 from centreline
    // Use first cut segment as the reference clip line with +offset
    const cp0=cutPts[0], cp1=cutPts[cutPts.length-1];
    const dx=cp1[0]-cp0[0], dy=cp1[1]-cp0[1], L=Math.hypot(dx,dy);
    if(L > 1e-6){
      const nx=-dy/L*width/2, ny=dx/L*width/2;
      if(leftResult){
        const clipped = clipPoly(leftResult, [cp0[0]+nx,cp0[1]+ny], [cp1[0]+nx,cp1[1]+ny], true);
        if(clipped.length>=3) leftResult=clipped;
      }
      if(rightResult){
        const clipped = clipPoly(rightResult, [cp0[0]-nx,cp0[1]-ny], [cp1[0]-nx,cp1[1]-ny], false);
        if(clipped.length>=3) rightResult=clipped;
      }
    }
  }

  const result=[];
  if(leftResult  && leftResult.length  >= 3){ const pp=polygonProps(leftResult);  if(pp && pp.A>1e-9) result.push(leftResult);  }
  if(rightResult && rightResult.length >= 3){ const pp=polygonProps(rightResult); if(pp && pp.A>1e-9) result.push(rightResult); }
  return result.length ? result : [poly];
}

function sipeClippedLength(poly, s){
  // Clip segment p1→p2 against polygon using Liang-Barsky style.
  // Returns the length of the portion inside the (convex) polygon.
  // Works for both CCW and CW winding by using the signed-area test.
  function clippedSegLen(p1, p2){
    if(!poly || poly.length < 3) return 0;
    const dx = p2[0]-p1[0], dy = p2[1]-p1[1];
    const segLen = Math.hypot(dx, dy);
    if(segLen < 1e-12) return 0;

    // Determine polygon winding (positive signed area = CCW in standard coords)
    const signedA = (() => {
      let s=0, n=poly.length;
      for(let i=0;i<n;i++){const j=(i+1)%n;s+=poly[i][0]*poly[j][1]-poly[j][0]*poly[i][1];}
      return s;
    })();
    const ccw = signedA > 0;

    let tIn=0, tOut=1;
    const n = poly.length;
    for(let i=0;i<n;i++){
      const v1=poly[i], v2=poly[(i+1)%n];
      const ex=v2[0]-v1[0], ey=v2[1]-v1[1];
      // Outward normal for CCW polygon = (ey, -ex)
      // Inward normal = (-ey, ex)
      // Use outward normal for the standard Liang-Barsky test:
      //   p_val = outward_normal · (p1 - v1)
      //   d_val = outward_normal · (p2 - p1) = outward_normal · (dx,dy)
      // If p_val > 0: p1 is outside this edge
      const outNx = ccw ?  ey : -ey;
      const outNy = ccw ? -ex :  ex;
      const p_val = outNx*(p1[0]-v1[0]) + outNy*(p1[1]-v1[1]);
      const d_val = outNx*dx + outNy*dy;

      if(Math.abs(d_val) < 1e-10){
        // Parallel to edge: if p1 outside this edge, whole segment is outside
        if(p_val > 1e-10) return 0;
        // else inside or on boundary — continue to next edge
      } else {
        const t = -p_val / d_val;
        if(d_val > 0){
          // Segment moving toward outside (potential exit)
          if(t < tOut) tOut = t;
        } else {
          // Segment moving toward inside (potential entry)
          if(t > tIn) tIn = t;
        }
        if(tIn > tOut + 1e-10) return 0;
      }
    }
    if(tOut <= tIn) return 0;
    return Math.max(0, Math.min(1,tOut) - Math.max(0,tIn)) * segLen;
  }

  if(s.points && s.points.length >= 2){
    let total = 0;
    for(let i = 0; i < s.points.length-1; i++){
      total += clippedSegLen(s.points[i], s.points[i+1]);
    }
    return total;
  } else if(s.p1 && s.p2){
    return clippedSegLen(s.p1, s.p2);
  }
  return 0;
}

function computeContactPatch(d){
  const Fz   = (d.Fz || 0);              // N
  const pIn  = (d.inflationKPa || 0);    // kPa
  const Wsec = (d.sectionWidthMM || 0);  // mm
  const AR   = (d.aspectRatio || 0);     // %
  const Drim = (d.rimDiamIN || 0) * 25.4; // mm (rim diameter)
  const ty   = d.perfTyreType || 'pcr';
  if (!(Fz > 0) || !(pIn > 0) || !(Wsec > 0)) return null;

  const k_load_by_type = {pcr:1.10, tbr:1.05, lt:1.08}; // Rhyne 2005, Table 2 approximation
  const k_load = k_load_by_type[ty] || 1.10;
  const p_contact_MPa = (pIn * 1e-3) * k_load;          // MPa = N/mm²
  const A_contact = Fz / p_contact_MPa;                 // mm²

  const ratio_by_type = {pcr:1.15, tbr:1.45, lt:1.25};
  const ratio = ratio_by_type[ty] || 1.15;              // Lc / Wc
  const k_Wc_by_type = {pcr:0.82, tbr:0.86, lt:0.84};
  const k_Wc = k_Wc_by_type[ty] || 0.82;                // section→patch width factor

  // Width: lesser of (k_Wc × section) and (√(A/ratio)) so area is conserved.
  const Wc_from_section = k_Wc * Wsec;
  const Wc_from_area    = Math.sqrt(A_contact / ratio);
  const Wc = Math.min(Wc_from_section, Wc_from_area);
  const Lc = A_contact / Math.max(Wc, 1e-6);

  // Effective tyre radius (for context display only; not used in SWE)
  const R_outer_mm = (Drim / 2) + (AR * Wsec / 100);

  return {
    Fz, pIn_kPa: pIn, p_contact_MPa, A_contact,
    Wc, Lc, ratio, R_outer_mm,
    Wc_from_section, Wc_from_area,
    source: 'Rhyne (2005); Pacejka (2012) §9.2'
  };
}

function clipPoly(poly,lp1,lp2,keepPos){
  let n=poly.length,dx=lp2[0]-lp1[0],dy=lp2[1]-lp1[1];
  let sides=poly.map(v=>dx*(v[1]-lp1[1])-dy*(v[0]-lp1[0]));
  let res=[];
  for(let i=0;i<n;i++){let j=(i+1)%n;let si=sides[i],sj=sides[j];let vi=poly[i],vj=poly[j];
    let inI=keepPos?(si>1e-10):(si<-1e-10),onI=Math.abs(si)<=1e-10;
    let inJ=keepPos?(sj>1e-10):(sj<-1e-10);
    if(inI||onI)res.push([vi[0],vi[1]]);
    if((inI&&!inJ&&Math.abs(sj)>1e-10)||(!inI&&!onI&&inJ)){
      let d1=[vj[0]-vi[0],vj[1]-vi[1]],cr=d1[0]*dy-d1[1]*dx;
      if(Math.abs(cr)>1e-12){let t=((lp1[0]-vi[0])*dy-(lp1[1]-vi[1])*dx)/cr;t=Math.max(0,Math.min(1,t));res.push([vi[0]+t*d1[0],vi[1]+t*d1[1]])}
    }
  }
  return res.length>=3?res:[];
}

function segIntersect(ax,ay,bx,by, cx,cy,dx,dy){
    // Returns t in [0,1] along AB where AB intersects CD, or null
    const dxAB=bx-ax, dyAB=by-ay;
    const dxCD=dx-cx, dyCD=dy-cy;
    const denom = dxAB*dyCD - dyAB*dxCD;
    if(Math.abs(denom)<1e-10) return null;
    const t = ((cx-ax)*dyCD - (cy-ay)*dxCD) / denom;
    const u = ((cx-ax)*dyAB - (cy-ay)*dxAB) / denom;
    if(t>=-1e-9 && t<=1+1e-9 && u>=-1e-9 && u<=1+1e-9)
      return Math.max(0,Math.min(1,t));
    return null;
  }

module.exports = {calcG,ensureCCW,polygonProps,polygonPerimeter,offsetPoly,invertSymMat2,beamKMatrix,effectiveK,computeKz,computeNetContactAreaFromSipes,shoreE,shoreK,nearestShoreKey,getGentK,convexHull,turningAngleExcess,axisAlignmentIndex,shapeFactor,splitBySipe,splitByPolylineSipe,sipeClippedLength,computeContactPatch,clipPoly,segIntersect};
