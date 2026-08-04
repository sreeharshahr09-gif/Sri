/* tread_eval browser engine
 * ==========================
 * A dependency-free JavaScript port of the verified Python pipeline
 * (tread_eval/*.py), which is itself cross-checked to ~1e-11 against the
 * Tread_Pattern_Stiffness_Estimation_Tool_v6.4 reference.
 *
 * This single file is the whole compute core: DXF import, rasterisation, the
 * theta x gamma FFT sweep, the Okonieski beam-mechanics stiffness, the standard
 * contact-patch shapes and the summary metrics.  It runs unchanged in a browser
 * Web Worker and under Node (for the parity tests in tests/), so the numbers the
 * page shows are the same numbers the test suite checks.
 *
 * Coordinate convention (identical to the Python schema):
 *   x  circumferential, mm, 0..circumference, wrapping at the seam
 *   y  lateral, mm, from -tread_width/2 to +tread_width/2 (0 = tread centreline)
 *   length = circumferential extent, width = lateral extent.
 */
(function (root, factory) {
  const mod = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = mod;
  else root.TreadEngine = mod;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // =====================================================================
  // 1. polygon geometry -- ports of ensureCCW / polygonProps / offsetPoly
  //    (verbatim behaviour of verify/tool_v64_reference.js)
  // =====================================================================
  function ensureCCW(pts) {
    let s = 0;
    const n = pts.length;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      s += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1];
    }
    return s < 0 ? pts.slice().reverse() : pts;
  }

  function polygonProps(vertsIn) {
    const n = vertsIn.length;
    if (n < 3) return null;
    const verts = ensureCCW(vertsIn);
    let As = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      As += verts[i][0] * verts[j][1] - verts[j][0] * verts[i][1];
    }
    As *= 0.5;
    const A = Math.abs(As);
    if (A < 1e-12) return null;
    let cx = 0, cy = 0, Ixxo = 0, Iyyo = 0, Ixyo = 0, perimeter = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const xi = verts[i][0], yi = verts[i][1], xj = verts[j][0], yj = verts[j][1];
      const cross = xi * yj - xj * yi;
      cx += (xi + xj) * cross;
      cy += (yi + yj) * cross;
      Ixxo += (yi * yi + yi * yj + yj * yj) * cross;
      Iyyo += (xi * xi + xi * xj + xj * xj) * cross;
      Ixyo += (xi * yj + 2 * xi * yi + 2 * xj * yj + xj * yi) * cross;
      perimeter += Math.hypot(xj - xi, yj - yi);
    }
    cx /= 6 * As;
    cy /= 6 * As;
    Ixxo /= 12;
    Iyyo /= 12;
    Ixyo /= 24;
    return {
      A: A,
      cx: cx,
      cy: cy,
      Ixx: Math.abs(Ixxo - As * cy * cy),
      Iyy: Math.abs(Iyyo - As * cx * cx),
      Ixy: Ixyo - As * cx * cy,
      perimeter: perimeter,
    };
  }

  function polygonPerimeter(verts) {
    const p = polygonProps(verts);
    return p ? p.perimeter : 0;
  }

  function polygonArea(verts) {
    const n = verts.length;
    if (n < 3) return 0;
    let s = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      s += verts[i][0] * verts[j][1] - verts[j][0] * verts[i][1];
    }
    return Math.abs(0.5 * s);
  }

  function polygonCentroid(verts) {
    const n = verts.length;
    let a = 0, cx = 0, cy = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const cross = verts[i][0] * verts[j][1] - verts[j][0] * verts[i][1];
      a += cross;
      cx += (verts[i][0] + verts[j][0]) * cross;
      cy += (verts[i][1] + verts[j][1]) * cross;
    }
    a *= 0.5;
    if (Math.abs(a) < 1e-12) {
      let mx = 0, my = 0;
      for (const p of verts) { mx += p[0]; my += p[1]; }
      return [mx / n, my / n];
    }
    return [cx / (6 * a), cy / (6 * a)];
  }

  function offsetPoly(vertsIn, draftDeg, z) {
    if (!draftDeg || z < 1e-12) return vertsIn;
    const pts = ensureCCW(vertsIn), n = pts.length;
    const normals = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const dx = pts[j][0] - pts[i][0], dy = pts[j][1] - pts[i][1];
      const len = Math.hypot(dx, dy);
      normals.push(len > 1e-12 ? [dy / len, -dx / len] : [0, 0]);
    }
    const off = z * Math.tan((draftDeg * Math.PI) / 180);
    const edges = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      edges.push([
        [pts[i][0] + off * normals[i][0], pts[i][1] + off * normals[i][1]],
        [pts[j][0] + off * normals[i][0], pts[j][1] + off * normals[i][1]],
      ]);
    }
    const out = [];
    for (let i = 0; i < n; i++) {
      const iPrev = (i - 1 + n) % n;
      const p1 = edges[iPrev][0], p2 = edges[iPrev][1];
      const p3 = edges[i][0], p4 = edges[i][1];
      const d1 = [p2[0] - p1[0], p2[1] - p1[1]], d2 = [p4[0] - p3[0], p4[1] - p3[1]];
      const cr = d1[0] * d2[1] - d1[1] * d2[0];
      if (Math.abs(cr) < 1e-12) {
        out.push([0.5 * (p2[0] + p3[0]), 0.5 * (p2[1] + p3[1])]);
      } else {
        const t = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / cr;
        out.push([p1[0] + t * d1[0], p1[1] + t * d1[1]]);
      }
    }
    let sOut = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      sOut += out[i][0] * out[j][1] - out[j][0] * out[i][1];
    }
    if (0.5 * sOut <= 1e-9) return pts;
    return out;
  }

  function invertSymMat2(M) {
    const det = M.xx * M.yy - M.xy * M.xy;
    if (Math.abs(det) < 1e-18) return { xx: 0, yy: 0, xy: 0 };
    return { xx: M.yy / det, yy: M.xx / det, xy: -M.xy / det };
  }

  function clipPoly(poly, lp1, lp2, keepPos) {
    const n = poly.length, dx = lp2[0] - lp1[0], dy = lp2[1] - lp1[1];
    const sides = poly.map((v) => dx * (v[1] - lp1[1]) - dy * (v[0] - lp1[0]));
    const res = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n, si = sides[i], sj = sides[j], vi = poly[i], vj = poly[j];
      const inI = keepPos ? si > 1e-10 : si < -1e-10, onI = Math.abs(si) <= 1e-10;
      const inJ = keepPos ? sj > 1e-10 : sj < -1e-10;
      if (inI || onI) res.push([vi[0], vi[1]]);
      if ((inI && !inJ && Math.abs(sj) > 1e-10) || (!inI && !onI && inJ)) {
        const d1 = [vj[0] - vi[0], vj[1] - vi[1]], cr = d1[0] * dy - d1[1] * dx;
        if (Math.abs(cr) > 1e-12) {
          let t = ((lp1[0] - vi[0]) * dy - (lp1[1] - vi[1]) * dx) / cr;
          t = Math.max(0, Math.min(1, t));
          res.push([vi[0] + t * d1[0], vi[1] + t * d1[1]]);
        }
      }
    }
    return res.length >= 3 ? res : [];
  }

  function splitBySipe(poly, p1, p2, width) {
    const dx = p2[0] - p1[0], dy = p2[1] - p1[1], L = Math.hypot(dx, dy);
    if (L < 1e-12) return [poly];
    const nx = (-dy / L) * width / 2, ny = (dx / L) * width / 2;
    const above = clipPoly(poly, [p1[0] + nx, p1[1] + ny], [p2[0] + nx, p2[1] + ny], true);
    const below = clipPoly(poly, [p1[0] - nx, p1[1] - ny], [p2[0] - nx, p2[1] - ny], false);
    const r = [];
    if (above.length >= 3) r.push(above);
    if (below.length >= 3) r.push(below);
    return r.length ? r : [poly];
  }

  function sipeClippedLength(poly, s) {
    function clippedSegLen(p1, p2) {
      if (!poly || poly.length < 3) return 0;
      const dx = p2[0] - p1[0], dy = p2[1] - p1[1];
      const segLen = Math.hypot(dx, dy);
      if (segLen < 1e-12) return 0;
      let signedA = 0;
      const n = poly.length;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        signedA += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1];
      }
      const ccw = signedA > 0;
      let tIn = 0, tOut = 1;
      for (let i = 0; i < n; i++) {
        const v1 = poly[i], v2 = poly[(i + 1) % n];
        const ex = v2[0] - v1[0], ey = v2[1] - v1[1];
        const outNx = ccw ? ey : -ey;
        const outNy = ccw ? -ex : ex;
        const pVal = outNx * (p1[0] - v1[0]) + outNy * (p1[1] - v1[1]);
        const dVal = outNx * dx + outNy * dy;
        if (Math.abs(dVal) < 1e-10) {
          if (pVal > 1e-10) return 0;
        } else {
          const t = -pVal / dVal;
          if (dVal > 0) { if (t < tOut) tOut = t; }
          else { if (t > tIn) tIn = t; }
          if (tIn > tOut + 1e-10) return 0;
        }
      }
      if (tOut <= tIn) return 0;
      return (Math.min(1, tOut) - Math.max(0, tIn)) * segLen;
    }
    if (s.points && s.points.length >= 2) {
      let total = 0;
      for (let i = 0; i < s.points.length - 1; i++) total += clippedSegLen(s.points[i], s.points[i + 1]);
      return total;
    } else if (s.p1 && s.p2) {
      return clippedSegLen(s.p1, s.p2);
    }
    return 0;
  }

  function netContactArea(verts, sipes) {
    const props = polygonProps(verts);
    if (!props) return 0;
    let removed = 0;
    for (const s of sipes || []) {
      if ((s.depth || 0) > 0 && (s.width || 0) > 0) removed += sipeClippedLength(verts, s) * (s.width || 0);
    }
    return Math.max(0, props.A - removed);
  }

  // =====================================================================
  // 2. stiffness -- Okonieski beam mechanics (ports of beamKMatrix / effectiveK / computeKz)
  // =====================================================================
  const SHORE_E_TABLE = { 30: 1.5, 40: 2.5, 50: 4.0, 60: 6.89, 70: 12.0 };
  const SHORE_K_TABLE = { 30: 0.93, 40: 0.85, 50: 0.73, 60: 0.64, 70: 0.57 };

  function nearestShoreKey(s) {
    const keys = Object.keys(SHORE_E_TABLE).map(Number);
    return keys.reduce((best, k) => (Math.abs(k - s) < Math.abs(best - s) ? k : best), keys[0]);
  }
  function shoreE(s) { const v = SHORE_E_TABLE[nearestShoreKey(+s)]; return v == null ? 6.89 : v; }
  function shoreK(s) { const v = SHORE_K_TABLE[nearestShoreKey(+s)]; return v == null ? 0.64 : v; }
  function calcG(E, nu) { return E / (2 * (1 + nu)); }

  function beamKMatrix(A, Ixx, Iyy, Ixy, L, E, G, mode) {
    if (A <= 0 || L <= 0 || Ixx <= 0 || Iyy <= 0) return { xx: 0, yy: 0, xy: 0 };
    const detI = Ixx * Iyy - Ixy * Ixy;
    if (detI <= 1e-15) return { xx: 0, yy: 0, xy: 0 };
    const cB = mode === "parallel" ? (L * L * L) / (12 * E) : (L * L * L) / (3 * E);
    const cS = mode === "parallel" ? L / (G * A) : (6 * L) / (5 * G * A);
    const Cxx = cB * Ixx / detI + cS;
    const Cyy = cB * Iyy / detI + cS;
    const Cxy = -cB * Ixy / detI;
    const detC = Cxx * Cyy - Cxy * Cxy;
    if (detC <= 1e-18) return { xx: 0, yy: 0, xy: 0 };
    return { xx: Cyy / detC, yy: Cxx / detC, xy: -Cxy / detC };
  }

  // Build evenly-spaced lateral sipes from a count (schema.Block.effective_sipes).
  function effectiveSipes(block) {
    if (block.sipes && block.sipes.length) return block.sipes;
    const nLat = block.n_lateral_sipes || 0;
    if (nLat <= 0) return [];
    const p = block.polygon;
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const q of p) { xmin = Math.min(xmin, q[0]); xmax = Math.max(xmax, q[0]); ymin = Math.min(ymin, q[1]); ymax = Math.max(ymax, q[1]); }
    const ext = Math.max(xmax - xmin, ymax - ymin) * 0.2;
    const depthFrac = block.sipe_depth_fraction == null ? 0.6 : block.sipe_depth_fraction;
    const depth = Math.min(depthFrac * block.height, block.height);
    const width = block.sipe_width == null ? 0.5 : block.sipe_width;
    const out = [];
    for (let i = 0; i < nLat; i++) {
      const f = (i + 1) / (nLat + 1);
      const xc = xmin + f * (xmax - xmin);
      out.push({ p1: [xc, ymin - ext], p2: [xc, ymax + ext], depth: depth, width: width });
    }
    return out;
  }

  function effectiveK(verts, nsd, E, nu, draft, mode, sipes, nSlices) {
    const G = calcG(E, nu);
    if (nsd <= 0 || E <= 0 || G <= 0 || verts.length < 3) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: 1 };
    const hasDraft = draft && Math.abs(draft) > 1e-12;
    function polyAt(z) { return hasDraft ? offsetPoly(verts, draft, nsd - z) : verts; }
    sipes = sipes || [];

    if (!sipes.length) {
      const L = nsd, nSl = nSlices || 40, dz = L / nSl;
      let CxxB = 0, CyyB = 0, CxyB = 0, Sh = 0, nValid = 0;
      for (let i = 0; i < nSl; i++) {
        const z = (i + 0.5) * dz;
        const p = polygonProps(polyAt(z));
        if (!p || p.A < 1e-9) continue;
        const Ixy = p.Ixy || 0;
        const detI = p.Ixx * p.Iyy - Ixy * Ixy;
        if (detI <= 1e-15) continue;
        const w = mode === "parallel" ? (z - L / 2) * (z - L / 2) : (L - z) * (L - z);
        CxxB += (w * p.Ixx) / detI * dz;
        CyyB += (w * p.Iyy) / detI * dz;
        CxyB += (w * -Ixy) / detI * dz;
        Sh += dz / p.A;
        nValid++;
      }
      if (nValid === 0) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: 1 };
      const shearFactor = mode === "parallel" ? 1 : 6 / 5;
      const Cxx = CxxB / E + (shearFactor * Sh) / G;
      const Cyy = CyyB / E + (shearFactor * Sh) / G;
      const Cxy = CxyB / E;
      const detC = Cxx * Cyy - Cxy * Cxy;
      if (detC <= 1e-18) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: 1 };
      const Kx = Cyy / detC, Ky = Cxx / detC;
      let Kxy = -Cxy / detC;
      if (Math.abs(Kxy) < 1e-6 * Math.max(Kx, Ky)) Kxy = 0;
      return { Kx: Kx, Ky: Ky, Kxy: Kxy, nSubs: 1 };
    }

    const transSet = new Set([0, nsd]);
    for (const s of sipes) { const h = nsd - s.depth; if (h > 0 && h < nsd) transSet.add(h); }
    const trans = Array.from(transSet).sort((a, b) => a - b);
    let Ctot = { xx: 0, yy: 0, xy: 0 };
    let anyValid = false, nSubs = 1;
    for (let li = 0; li < trans.length - 1; li++) {
      const zB = trans[li], zT = trans[li + 1], lH = zT - zB;
      if (lH < 1e-6) continue;
      const active = sipes.filter((s) => s.depth >= nsd - zB - 1e-6);
      let subs = [polyAt((zB + zT) / 2)];
      for (const s of active) {
        const next = [];
        for (const sp of subs) next.push(...splitBySipe(sp, s.p1, s.p2, s.width));
        subs = next;
      }
      subs = subs.filter((sp) => { const p = polygonProps(sp); return p && p.A > 1.0; });
      nSubs = Math.max(nSubs, subs.length);
      let Klayer = { xx: 0, yy: 0, xy: 0 };
      for (const sp of subs) {
        const p = polygonProps(sp);
        if (!p || p.A < 1e-9) continue;
        const Ks = beamKMatrix(p.A, p.Ixx, p.Iyy, p.Ixy || 0, lH, E, G, mode);
        Klayer.xx += Ks.xx; Klayer.yy += Ks.yy; Klayer.xy += Ks.xy;
      }
      const Clayer = invertSymMat2(Klayer);
      if (Clayer.xx > 0 && Clayer.yy > 0) { Ctot.xx += Clayer.xx; Ctot.yy += Clayer.yy; Ctot.xy += Clayer.xy; anyValid = true; }
    }
    if (!anyValid) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: nSubs };
    const Ktot = invertSymMat2(Ctot);
    let Kxy = Math.abs(Ktot.xy) < 1e-6 * Math.max(Ktot.xx, Ktot.yy) ? 0 : Ktot.xy;
    return { Kx: Ktot.xx, Ky: Ktot.yy, Kxy: Kxy, nSubs: nSubs };
  }

  function computeKz(verts, nsd, E, gentK, sipes, bulkModulus) {
    const props = polygonProps(verts);
    if (!props) return { Kz: 0, S: 0, Eeff: 0, Anet: 0 };
    nsd = Math.max(nsd, 0.1);
    E = Math.max(E, 0.01);
    const Kb = bulkModulus || 1100;
    const Anet = netContactArea(verts, sipes || []);
    const S = Anet / (nsd * Math.max(props.perimeter, 1e-9));
    const EeffUncorr = E * (1 + 2 * gentK * S * S);
    const Eeff = EeffUncorr / (1 + EeffUncorr / Kb);
    return { Kz: (Eeff * Anet) / nsd, S: S, Eeff: Eeff, Anet: Anet };
  }

  // params: {shore_a, poisson, mode, bulk_modulus, n_slices, e_override, k_override}
  function blockStiffness(block, params) {
    const verts = block.polygon;
    const props = polygonProps(verts);
    if (!props || block.height <= 0)
      return { kx: 0, ky: 0, kxy: 0, kz: 0, area: 0, netArea: 0, perimeter: 0, shapeFactor: 0, eEff: 0, slenderness: 0, nSubs: 0 };
    let E, k;
    if (block.shore_a != null) { E = shoreE(block.shore_a); k = shoreK(block.shore_a); }
    else {
      E = params.e_override != null ? params.e_override : shoreE(params.shore_a);
      k = params.k_override != null ? params.k_override : shoreK(params.shore_a);
    }
    const sipes = effectiveSipes(block);
    const sh = effectiveK(verts, block.height, E, params.poisson, block.draft_angle || 0, params.mode, sipes, params.n_slices || 40);
    const kzr = computeKz(verts, block.height, E, k, sipes, params.bulk_modulus || 1100);
    // slenderness = height / min plan dimension
    let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
    for (const q of verts) { xmin = Math.min(xmin, q[0]); xmax = Math.max(xmax, q[0]); ymin = Math.min(ymin, q[1]); ymax = Math.max(ymax, q[1]); }
    const lx = xmax - xmin, ly = ymax - ymin;
    return {
      kx: sh.Kx, ky: sh.Ky, kxy: sh.Kxy, kz: kzr.Kz,
      area: props.A, netArea: kzr.Anet, perimeter: props.perimeter,
      shapeFactor: kzr.S, eEff: kzr.Eeff,
      slenderness: block.height / Math.max(Math.min(lx, ly), 1e-6), nSubs: sh.nSubs,
    };
  }

  // =====================================================================
  // 3. DXF import (port of tread_eval/dxf.py)
  // =====================================================================
  function readDxfEntities(text) {
    const raw = text.split(/\r\n|\r|\n/);
    const pairs = [];
    for (let i = 0; i + 1 < raw.length; i += 2) pairs.push([raw[i].trim(), raw[i + 1].trim()]);
    const entities = [];
    let inEntities = false, current = null;
    for (let idx = 0; idx < pairs.length; idx++) {
      const code = pairs[idx][0], value = pairs[idx][1];
      if (code === "0" && value === "SECTION" && idx + 1 < pairs.length && pairs[idx + 1][1] === "ENTITIES") { inEntities = true; continue; }
      if (code === "0" && value === "ENDSEC" && inEntities) { inEntities = false; continue; }
      if (!inEntities) continue;
      if (code === "0") { current = { type: value }; entities.push(current); }
      else if (current) { (current[code] = current[code] || []).push(value); }
    }
    return entities;
  }

  function flattenArc(cx, cy, r, a0, a1, sagitta) {
    sagitta = sagitta || 0.02;
    if (a1 <= a0) a1 += 360;
    const sweep = ((a1 - a0) * Math.PI) / 180;
    if (r <= 0 || sweep <= 0) return [];
    const ratio = Math.max(-1, Math.min(1, 1 - sagitta / r));
    const dtheta = r > sagitta ? 2 * Math.acos(ratio) : sweep;
    const nSeg = Math.max(2, Math.ceil(sweep / Math.max(dtheta, 1e-6)));
    const out = [];
    for (let i = 0; i <= nSeg; i++) {
      const a = ((a0 + ((a1 - a0) * i) / nSeg) * Math.PI) / 180;
      out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
    return out;
  }

  function entitiesToSegments(entities, sagitta) {
    const segments = [];
    for (const e of entities) {
      try {
        if (e.type === "LINE") {
          const p = [[+e["10"][0], +e["20"][0]], [+e["11"][0], +e["21"][0]]];
          if (Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]) < 1e-9) continue;
          segments.push(p);
        } else if (e.type === "ARC") {
          const pts = flattenArc(+e["10"][0], +e["20"][0], +e["40"][0], +e["50"][0], +e["51"][0], sagitta);
          if (pts.length >= 2) segments.push(pts);
        } else if (e.type === "CIRCLE") {
          const pts = flattenArc(+e["10"][0], +e["20"][0], +e["40"][0], 0, 360, sagitta);
          if (pts.length >= 3) segments.push(pts);
        } else if (e.type === "LWPOLYLINE" || e.type === "POLYLINE") {
          const xs = (e["10"] || []).map(Number), ys = (e["20"] || []).map(Number);
          const pts = [];
          for (let i = 0; i < Math.min(xs.length, ys.length); i++) pts.push([xs[i], ys[i]]);
          if (pts.length >= 2) {
            const closed = (parseInt((e["70"] || ["0"])[0], 10) & 1) === 1;
            if (closed && Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) > 1e-9) pts.push(pts[0]);
            segments.push(pts);
          }
        }
      } catch (err) { /* skip malformed entity */ }
    }
    return segments;
  }

  function buildLoops(segments, tol) {
    tol = tol || 1e-4;
    const key = (p) => Math.round(p[0] / tol) + "," + Math.round(p[1] / tol);
    const adjacency = new Map();
    const add = (k, v) => { if (!adjacency.has(k)) adjacency.set(k, []); adjacency.get(k).push(v); };
    for (let i = 0; i < segments.length; i++) { add(key(segments[i][0]), [i, 0]); add(key(segments[i][segments[i].length - 1]), [i, 1]); }
    const used = new Array(segments.length).fill(false);
    const closed = [], openChains = [];
    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    for (let i = 0; i < segments.length; i++) {
      if (used[i]) continue;
      let chain = segments[i].slice();
      used[i] = true;
      while (true) {
        const cand = (adjacency.get(key(chain[chain.length - 1])) || []).find((je) => !used[je[0]]);
        if (!cand) break;
        used[cand[0]] = true;
        const seg = cand[1] === 0 ? segments[cand[0]] : segments[cand[0]].slice().reverse();
        chain = chain.concat(seg.slice(1));
      }
      while (true) {
        const cand = (adjacency.get(key(chain[0])) || []).find((je) => !used[je[0]]);
        if (!cand) break;
        used[cand[0]] = true;
        const seg = cand[1] === 0 ? segments[cand[0]].slice().reverse() : segments[cand[0]];
        chain = seg.slice(0, seg.length - 1).concat(chain);
      }
      if (dist(chain[0], chain[chain.length - 1]) < tol * 10) closed.push(chain.slice(0, chain.length - 1));
      else openChains.push(chain);
    }
    return { closed: closed, openChains: openChains };
  }

  function closeAcrossSeam(openChains, circumference, tol) {
    tol = tol || 1e-3;
    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    const minX = (c) => Math.min.apply(null, c.map((p) => p[0]));
    const maxX = (c) => Math.max.apply(null, c.map((p) => p[0]));
    let left = openChains.filter((c) => minX(c) < tol);
    const right = openChains.filter((c) => maxX(c) > circumference - tol);
    left = left.filter((c) => right.indexOf(c) === -1);
    const usedRight = new Set();
    const loops = [], leftovers = [];
    const byY = (a, b) => a[1] - b[1];
    for (const lc of left) {
      const lEnds = [lc[0], lc[lc.length - 1]].sort(byY);
      let matched = null;
      for (let ri = 0; ri < right.length; ri++) {
        if (usedRight.has(ri)) continue;
        const shifted = right[ri].map((p) => [p[0] - circumference, p[1]]);
        const rEnds = [shifted[0], shifted[shifted.length - 1]].sort(byY);
        if (dist(lEnds[0], rEnds[0]) < tol && dist(lEnds[1], rEnds[1]) < tol) { matched = [ri, shifted]; break; }
      }
      if (!matched) { leftovers.push(lc); continue; }
      usedRight.add(matched[0]);
      let shifted = matched[1];
      if (dist(lc[lc.length - 1], shifted[0]) > dist(lc[lc.length - 1], shifted[shifted.length - 1])) shifted = shifted.slice().reverse();
      let loop = lc.concat(shifted.slice(1));
      if (dist(loop[0], loop[loop.length - 1]) < tol) loop = loop.slice(0, loop.length - 1);
      loops.push(loop);
    }
    for (let ri = 0; ri < right.length; ri++) if (!usedRight.has(ri)) leftovers.push(right[ri]);
    const others = openChains.filter((c) => left.indexOf(c) === -1 && right.indexOf(c) === -1);
    return { loops: loops, leftover: leftovers.concat(others) };
  }

  function gcd(a, b) { while (b) { [a, b] = [b, a % b]; } return a; }

  function pointSetsMatch(a, b, tol) {
    for (const p of b) {
      let best = Infinity;
      for (const q of a) { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); if (d < best) best = d; }
      if (best > tol) return false;
    }
    return true;
  }

  function geometricRepeat(blocks, circumference, tol) {
    tol = tol || 0.05;
    if (blocks.length < 4) return null;
    const pts = blocks.map((b) => { const c = polygonCentroid(b.polygon); return [((c[0] % circumference) + circumference) % circumference, c[1]]; });
    let best = null;
    const maxN = Math.min(201, blocks.length + 1);
    for (let n = 2; n < maxN; n++) {
      const shift = circumference / n;
      const moved = pts.map((p) => [(p[0] + shift) % circumference, p[1]]);
      if (pointSetsMatch(pts, moved, tol)) best = shift;
    }
    return best;
  }

  function estimatePitchCount(blocks, circumference) {
    if (!blocks.length) return 1;
    const n = 4096;
    const density = new Float64Array(n);
    for (const b of blocks) {
      const c = polygonCentroid(b.polygon);
      const cx = ((c[0] % circumference) + circumference) % circumference;
      density[Math.floor((cx / circumference) * n) % n] += polygonArea(b.polygon);
    }
    let mean = 0; for (let i = 0; i < n; i++) mean += density[i]; mean /= n;
    for (let i = 0; i < n; i++) density[i] -= mean;
    const maxOrder = 200;
    const spec = rfftMag(density, n).slice(0, maxOrder + 1);
    const body = spec.slice(1);
    let peak = 0; for (const v of body) if (v > peak) peak = v;
    if (peak <= 0) return 1;
    const strong = [];
    for (let i = 0; i < body.length; i++) if (body[i] > 0.35 * peak) strong.push(i + 1);
    let fundamental = strong[0];
    for (let i = 1; i < strong.length; i++) fundamental = gcd(fundamental, strong[i]);
    return Math.max(1, fundamental);
  }

  // rfft magnitude of a real signal length n (n need not be power of 2 -- uses DFT via chirp only if needed)
  function rfftMag(sig, n) {
    // For the pitch-count spectrum n is 4096 (power of 2), so radix-2 is fine.
    const re = new Float64Array(n), im = new Float64Array(n);
    for (let i = 0; i < n; i++) re[i] = sig[i];
    fftRadix2(re, im, false);
    const half = (n >> 1) + 1;
    const out = new Float64Array(half);
    for (let i = 0; i < half; i++) out[i] = Math.hypot(re[i], im[i]);
    return out;
  }

  // params: {height, draft_angle, n_lateral_sipes, sipe_depth_fraction, shore_a}
  function loadPattern(text, defaults, opts) {
    defaults = Object.assign({ height: 8.0, draft_angle: 3.0, n_lateral_sipes: 0, sipe_depth_fraction: 0.6, shore_a: null,
      height_by_zone: {}, draft_by_zone: {}, sipes_by_zone: {} }, defaults || {});
    opts = opts || {};
    const entities = readDxfEntities(text);
    let segments = entitiesToSegments(entities, 0.02);
    const warnings = [];
    if (!segments.length) throw new Error("no usable LINE/ARC/POLYLINE entities found in DXF");

    let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
    for (const s of segments) for (const p of s) { xMin = Math.min(xMin, p[0]); xMax = Math.max(xMax, p[0]); yMin = Math.min(yMin, p[1]); yMax = Math.max(yMax, p[1]); }
    const circ = opts.circumference || xMax - xMin;
    const width = opts.tread_width || yMax - yMin;

    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    const kept = [];
    for (const s of segments) {
      if (s.length === 2 && dist(s[0], s[1]) > 0.5 * circ) continue;
      kept.push(s.map((p) => [p[0] - xMin, p[1] - yMin]));
    }
    if (kept.length !== segments.length) warnings.push("ignored " + (segments.length - kept.length) + " full-span construction line(s)");
    segments = kept;

    const bl = buildLoops(segments);
    const seam = closeAcrossSeam(bl.openChains, circ);
    if (seam.leftover.length) warnings.push(seam.leftover.length + " chain(s) did not close and were discarded");

    const minBlockArea = opts.min_block_area || 5.0;
    let loops = bl.closed.concat(seam.loops);
    const small = loops.filter((c) => polygonArea(c) < minBlockArea);
    loops = loops.filter((c) => polygonArea(c) >= minBlockArea);

    const half = width / 2;
    loops.sort((a, b) => Math.min.apply(null, a.map((p) => p[0])) - Math.min.apply(null, b.map((p) => p[0])));
    const blocks = [];
    for (let i = 0; i < loops.length; i++) {
      const poly = loops[i].map((p) => [p[0], p[1] - half]);
      let cy = 0; for (const p of poly) cy += p[1]; cy /= poly.length;
      const zone = classifyZone(Math.abs(cy), width);
      blocks.push({
        id: "D" + String(i).padStart(4, "0"), pitch_id: "", polygon: poly, zone: zone,
        height: defaults.height_by_zone[zone] != null ? defaults.height_by_zone[zone] : defaults.height,
        draft_angle: defaults.draft_by_zone[zone] != null ? defaults.draft_by_zone[zone] : defaults.draft_angle,
        n_lateral_sipes: defaults.sipes_by_zone[zone] != null ? defaults.sipes_by_zone[zone] : defaults.n_lateral_sipes,
        sipe_depth_fraction: defaults.sipe_depth_fraction, sipe_width: 0.5, shore_a: defaults.shore_a, sipes: [],
      });
    }

    const nPitches = opts.n_pitches && opts.n_pitches > 0 ? opts.n_pitches
      : opts.pitch_length && opts.pitch_length > 0 ? Math.max(1, Math.round(circ / opts.pitch_length))
      : estimatePitchCount(blocks, circ);
    const pitchLen = circ / nPitches;
    const pitches = [];
    for (let i = 0; i < nPitches; i++) pitches.push({ id: "P" + String(i).padStart(3, "0"), circumferential_start: i * pitchLen, circumferential_length: pitchLen });

    const repeat = geometricRepeat(blocks, circ);
    if (repeat && Math.abs(repeat - pitchLen) > 0.01)
      warnings.push("pitch spacing " + pitchLen.toFixed(3) + " mm is a sub-multiple of the " + repeat.toFixed(3) + " mm geometric repeat -- lateral bands are staggered");

    for (const b of blocks) {
      const c = polygonCentroid(b.polygon);
      const cx = ((c[0] % circ) + circ) % circ;
      const pit = pitches.find((p) => p.circumferential_start <= cx && cx < p.circumferential_start + p.circumferential_length);
      b.pitch_id = pit ? pit.id : pitches.length ? pitches[pitches.length - 1].id : "";
    }

    let land = 0; for (const b of blocks) land += polygonArea(b.polygon);
    const uniform = repeat ? Math.abs(circ / repeat - Math.round(circ / repeat)) < 1e-3 && Math.round(circ / repeat) >= 2 : false;
    if (uniform) warnings.push("every repeat is geometrically identical -- this drawing carries no pitch modulation; supply the production pitch sequence for a real order analysis");

    const pattern = {
      tyre_circumference: circ, tread_width: width, pitches: pitches, blocks: blocks,
      crown: crownDualRadius(width, opts.crown_r_center, opts.crown_r_shoulder),
      name: opts.name || "pattern", source: "dxf",
      meta: { geometric_repeat_mm: repeat, uniform_array: uniform },
    };
    const report = {
      n_entities: entities.length, n_segments: segments.length, n_closed: bl.closed.length,
      n_wrapped: seam.loops.length, n_discarded_open: seam.leftover.length, n_discarded_small: small.length,
      circumference: circ, tread_width: width, land_ratio: land / (circ * width), n_blocks: blocks.length,
      warnings: warnings,
    };
    return { pattern: pattern, report: report };
  }

  // =====================================================================
  // 4. zones + crown profile (port of tread_eval/schema.py)
  // =====================================================================
  function zoneBounds(treadWidth, centerFrac, intermediateFrac) {
    centerFrac = centerFrac || 0.34; intermediateFrac = intermediateFrac || 0.72;
    const half = treadWidth / 2;
    return { center: [0, centerFrac * half], intermediate: [centerFrac * half, intermediateFrac * half], shoulder: [intermediateFrac * half, half] };
  }
  const ZONES = ["center", "intermediate", "shoulder"];
  function classifyZone(yAbs, treadWidth) {
    const b = zoneBounds(treadWidth);
    for (const z of ZONES) if (yAbs <= b[z][1]) return z;
    return "shoulder";
  }

  function cumtrapz0(f, x) {
    const out = new Float64Array(f.length);
    out[0] = 0;
    for (let i = 1; i < f.length; i++) out[i] = out[i - 1] + 0.5 * (f[i] + f[i - 1]) * (x[i] - x[i - 1]);
    return out;
  }
  function interp(xq, xs, ys) {
    if (xq <= xs[0]) return ys[0];
    if (xq >= xs[xs.length - 1]) return ys[ys.length - 1];
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= xq) lo = mid; else hi = mid; }
    const t = (xq - xs[lo]) / (xs[hi] - xs[lo]);
    return ys[lo] + t * (ys[hi] - ys[lo]);
  }

  function crownFromRadiusProfile(y, r, measured) {
    const n = y.length;
    const phi = cumtrapz0(r.map((v) => 1 / Math.max(v, 1e-9)), y);
    const phi0 = interp(0, y, phi); for (let i = 0; i < n; i++) phi[i] -= phi0;
    const z = cumtrapz0(phi.map(Math.sin), y);
    const z0 = interp(0, y, z); for (let i = 0; i < n; i++) z[i] -= z0;
    const yProj = cumtrapz0(phi.map(Math.cos), y);
    const yp0 = interp(0, y, yProj); for (let i = 0; i < n; i++) yProj[i] -= yp0;
    return { y: y, phi: phi, z: z, y_proj: yProj, r: r, measured: !!measured };
  }
  function crownDualRadius(treadWidth, rCenter, rShoulder, breakFraction, n) {
    rCenter = rCenter || 125; rShoulder = rShoulder || 55; breakFraction = breakFraction || 0.5; n = n || 801;
    const half = treadWidth / 2;
    const y = new Float64Array(n), r = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      y[i] = -half + (2 * half * i) / (n - 1);
      let t = (Math.abs(y[i]) / half - breakFraction) / Math.max(1e-6, 1 - breakFraction);
      t = Math.max(0, Math.min(1, t));
      const blend = t * t * (3 - 2 * t);
      r[i] = rCenter * (1 - blend) + rShoulder * blend;
    }
    return crownFromRadiusProfile(y, r, false);
  }
  function crownTangentAngle(crown, y) { return interp(y, crown.y, crown.phi); }
  function crownLocalRadius(crown, y) { return interp(y, crown.y, crown.r); }
  function crownDrop(crown, y) { return interp(y, crown.y, crown.z); }
  function crownContactLateral(crown, gammaDeg) {
    const target = (gammaDeg * Math.PI) / 180;
    let maxAbs = 0; for (const p of crown.phi) maxAbs = Math.max(maxAbs, Math.abs(p));
    if (maxAbs < 1e-9) return 0;
    const phiMin = Math.min.apply(null, Array.from(crown.phi)), phiMax = Math.max.apply(null, Array.from(crown.phi));
    if (target <= phiMin) return crown.y[0];
    if (target >= phiMax) return crown.y[crown.y.length - 1];
    // phi is monotonic increasing in y for a sensible crown
    return interp(target, crown.phi, crown.y);
  }

  // =====================================================================
  // 5. FFT -- radix-2 complex + rfft/irfft helpers
  // =====================================================================
  function fftRadix2(re, im, inverse) {
    const n = re.length;
    if (n <= 1) return;
    // bit reversal
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { const tr = re[i]; re[i] = re[j]; re[j] = tr; const ti = im[i]; im[i] = im[j]; im[j] = ti; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = ((inverse ? 2 : -2) * Math.PI) / len;
      const wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cwr = 1, cwi = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = i + k + len / 2;
          const tr = re[b] * cwr - im[b] * cwi;
          const ti = re[b] * cwi + im[b] * cwr;
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
          const ncwr = cwr * wr - cwi * wi; cwi = cwr * wi + cwi * wr; cwr = ncwr;
        }
      }
    }
    if (inverse) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
  }

  // Real FFT of one row -> returns {re, im} half-spectra of length n/2+1
  function rfftRow(row, n) {
    const re = new Float64Array(n), im = new Float64Array(n);
    for (let i = 0; i < n; i++) re[i] = row[i];
    fftRadix2(re, im, false);
    const half = (n >> 1) + 1;
    return { re: re.subarray(0, half), im: im.subarray(0, half) };
  }

  // irfft of a half spectrum {re, im} (length n/2+1) -> real array length n
  function irfft(hre, him, n) {
    const re = new Float64Array(n), im = new Float64Array(n);
    const half = (n >> 1) + 1;
    for (let i = 0; i < half; i++) { re[i] = hre[i]; im[i] = him[i]; }
    for (let i = 1; i < half - 1; i++) { re[n - i] = hre[i]; im[n - i] = -him[i]; }
    fftRadix2(re, im, true);
    return re; // real part
  }

  // =====================================================================
  // 6. rasterisation (port of tread_eval/raster.py)
  // =====================================================================
  function makeGrid(pattern, nx, ny) {
    return {
      nx: nx, ny: ny,
      dx: pattern.tyre_circumference / nx, dy: pattern.tread_width / ny,
      circumference: pattern.tyre_circumference, tread_width: pattern.tread_width,
    };
  }
  function gridY(grid) { const y = new Float64Array(grid.ny); for (let i = 0; i < grid.ny; i++) y[i] = -grid.tread_width / 2 + (i + 0.5) * grid.dy; return y; }
  function gridXRel(grid) { const x = new Float64Array(grid.nx); const h = grid.nx >> 1; for (let i = 0; i < grid.nx; i++) x[i] = (((i + h) % grid.nx) - h) * grid.dx; return x; }
  function gridThetaDeg(grid) { const t = new Float64Array(grid.nx); for (let i = 0; i < grid.nx; i++) t[i] = (i * 360) / grid.nx; return t; }

  function clipHalfplaneX(poly, keepBelow, bound) {
    if (!poly.length) return [];
    const out = [];
    const n = poly.length;
    const inside = keepBelow ? (pt) => pt[0] <= bound : (pt) => pt[0] >= bound;
    for (let i = 0; i < n; i++) {
      const cur = poly[i], nxt = poly[(i + 1) % n];
      const ci = inside(cur), ni = inside(nxt);
      if (ci) out.push(cur);
      if (ci !== ni) {
        const dx = nxt[0] - cur[0];
        const t = Math.abs(dx) < 1e-12 ? 0 : (bound - cur[0]) / dx;
        out.push([bound, cur[1] + t * (nxt[1] - cur[1])]);
      }
    }
    return out;
  }

  function splitAtSeam(poly, circumference) {
    let xmin = Infinity, xmax = -Infinity;
    for (const p of poly) { xmin = Math.min(xmin, p[0]); xmax = Math.max(xmax, p[0]); }
    if (xmin >= 0 && xmax <= circumference) return [poly];
    const pieces = [];
    const shift = -Math.floor(xmin / circumference) * circumference;
    let work = poly.map((p) => [p[0] + shift, p[1]]);
    let k = 0;
    while (k < 8) {
      let mx = -Infinity; for (const p of work) mx = Math.max(mx, p[0]);
      if (mx <= 1e-9) break;
      const piece = clipHalfplaneX(clipHalfplaneX(work, true, circumference), false, 0);
      if (piece.length >= 3) pieces.push(piece);
      work = work.map((p) => [p[0] - circumference, p[1]]);
      let mx2 = -Infinity; for (const p of work) mx2 = Math.max(mx2, p[0]);
      if (mx2 <= 1e-9) break;
      k++;
    }
    return pieces.filter((p) => p.length >= 3);
  }

  // Scanline-fill one polygon into `land` (Float32Array ny*nx) and `labels` (Int32Array), tagging with `idx`.
  function fillPolygon(poly, grid, y0, land, labels, idx) {
    const ny = grid.ny, nx = grid.nx, dx = grid.dx, dy = grid.dy;
    let ymin = Infinity, ymax = -Infinity;
    for (const p of poly) { ymin = Math.min(ymin, p[1]); ymax = Math.max(ymax, p[1]); }
    let rLo = Math.max(0, Math.floor((ymin - y0) / dy - 0.5));
    let rHi = Math.min(ny - 1, Math.ceil((ymax - y0) / dy - 0.5));
    if (rHi < rLo) return;
    const n = poly.length;
    const xs = [];
    for (let r = rLo; r <= rHi; r++) {
      const yc = y0 + (r + 0.5) * dy;
      xs.length = 0;
      for (let i = 0; i < n; i++) {
        const ya = poly[i][1], yb = poly[(i + 1) % n][1];
        const lo = Math.min(ya, yb), hi = Math.max(ya, yb);
        if (yc >= lo && yc < hi && ya !== yb) {
          const t = (yc - ya) / (yb - ya);
          xs.push(poly[i][0] + t * (poly[(i + 1) % n][0] - poly[i][0]));
        }
      }
      if (xs.length < 2) continue;
      xs.sort((a, b) => a - b);
      const rowBase = r * nx;
      for (let i = 0; i + 1 < xs.length; i += 2) {
        let c0 = Math.ceil(xs[i] / dx - 0.5), c1 = Math.ceil(xs[i + 1] / dx - 0.5);
        c0 = Math.max(0, Math.min(nx, c0)); c1 = Math.max(0, Math.min(nx, c1));
        for (let c = c0; c < c1; c++) { land[rowBase + c] = 1; labels[rowBase + c] = idx; }
      }
    }
  }

  function rasterise(pattern, grid, stiffParams, curvatureCorrection) {
    const ny = grid.ny, nx = grid.nx, N = ny * nx;
    const y0 = -grid.tread_width / 2;
    const yv = gridY(grid);
    const pixelArea = grid.dx * grid.dy;

    const lateralWeight = new Float64Array(ny);
    if (curvatureCorrection) for (let r = 0; r < ny; r++) lateralWeight[r] = 1 / Math.max(Math.cos(crownTangentAngle(pattern.crown, yv[r])), 1e-6);
    else lateralWeight.fill(1);
    const rowArea = new Float64Array(ny);
    for (let r = 0; r < ny; r++) rowArea[r] = lateralWeight[r] * pixelArea;

    const land = new Float32Array(N);
    const labels = new Int32Array(N).fill(-1);
    const nBlocks = pattern.blocks.length;

    const stiff = new Array(nBlocks);
    for (let i = 0; i < nBlocks; i++) stiff[i] = blockStiffness(pattern.blocks[i], stiffParams);

    for (let idx = 0; idx < nBlocks; idx++)
      for (const piece of splitAtSeam(pattern.blocks[idx].polygon, pattern.tyre_circumference))
        fillPolygon(piece, grid, y0, land, labels, idx);

    const blockPixelCount = new Int32Array(nBlocks);
    for (let i = 0; i < N; i++) { const l = labels[i]; if (l >= 0) blockPixelCount[l]++; }

    const kxPer = new Float64Array(nBlocks), kyPer = new Float64Array(nBlocks), kzPer = new Float64Array(nBlocks), fracPer = new Float64Array(nBlocks);
    for (let i = 0; i < nBlocks; i++) {
      const n = blockPixelCount[i];
      if (n <= 0) continue;
      kxPer[i] = stiff[i].kx / n; kyPer[i] = stiff[i].ky / n; kzPer[i] = stiff[i].kz / n; fracPer[i] = 1 / n;
    }

    const area = new Float32Array(N), kx = new Float32Array(N), ky = new Float32Array(N), kz = new Float32Array(N), blockFrac = new Float32Array(N), yMoment = new Float32Array(N);
    const bounds = zoneBounds(pattern.tread_width);
    const zoneArea = { center: new Float32Array(N), intermediate: new Float32Array(N), shoulder: new Float32Array(N) };
    const rowZone = new Array(ny);
    for (let r = 0; r < ny; r++) { const ay = Math.abs(yv[r]); for (const z of ZONES) { if (ay >= bounds[z][0] && ay < bounds[z][1] + 1e-9) { rowZone[r] = z; break; } } }

    for (let r = 0; r < ny; r++) {
      const base = r * nx, ra = rowArea[r], yy = yv[r], zoneMap = zoneArea[rowZone[r]];
      for (let c = 0; c < nx; c++) {
        const i = base + c, l = labels[i];
        if (land[i] > 0) {
          area[i] = ra; yMoment[i] = ra * yy;
          if (zoneMap) zoneMap[i] = ra;
          if (l >= 0) { kx[i] = kxPer[l]; ky[i] = kyPer[l]; kz[i] = kzPer[l]; blockFrac[i] = fracPer[l]; }
        }
      }
    }

    return {
      grid: grid, land: land, area: area, kx: kx, ky: ky, kz: kz, blockFrac: blockFrac,
      zoneArea: zoneArea, yMoment: yMoment, labels: labels, blockPixelCount: blockPixelCount,
      stiffness: stiff, curvatureCorrection: !!curvatureCorrection,
    };
  }

  // =====================================================================
  // 7. contact-patch shapes (port of cp_shapes.py + ContactPatch)
  // =====================================================================
  const SHAPES = ["rectangle", "rounded", "stadium", "ellipse", "superellipse", "trapezoid", "diamond"];

  function rectangleOutline(length, width) {
    const a = length / 2, b = width / 2;
    return [[-a, -b], [a, -b], [a, b], [-a, b]];
  }
  function roundedOutline(length, width, cornerRadius, nArc) {
    nArc = nArc || 12;
    const a = length / 2, b = width / 2;
    const r = Math.max(0, Math.min(cornerRadius, Math.min(a, b)));
    if (r <= 1e-9) return rectangleOutline(length, width);
    const centres = [[a - r, -(b - r)], [a - r, b - r], [-(a - r), b - r], [-(a - r), -(b - r)]];
    const starts = [-90, 0, 90, 180];
    const pts = [];
    for (let ci = 0; ci < 4; ci++) {
      const cx = centres[ci][0], cy = centres[ci][1], start = starts[ci];
      for (let k = 0; k <= nArc; k++) {
        const t = ((start + (90 * k) / nArc) * Math.PI) / 180;
        pts.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
      }
    }
    return pts;
  }
  function stadiumOutline(length, width, nArc) { return roundedOutline(length, width, Math.min(length, width) / 2, nArc || 24); }
  function ellipseShape(length, width, n) {
    n = n || 181; const pts = [];
    for (let i = 0; i < n; i++) { const t = (2 * Math.PI * i) / n; pts.push([(length / 2) * Math.cos(t), (width / 2) * Math.sin(t)]); }
    return pts;
  }
  function superellipseShape(length, width, exponent, n) {
    n = n || 181; const e = Math.max(0.2, exponent || 3); const pts = [];
    for (let i = 0; i < n; i++) {
      const t = (2 * Math.PI * i) / n, ct = Math.cos(t), st = Math.sin(t);
      pts.push([(length / 2) * Math.sign(ct) * Math.pow(Math.abs(ct), 2 / e), (width / 2) * Math.sign(st) * Math.pow(Math.abs(st), 2 / e)]);
    }
    return pts;
  }
  function trapezoidOutline(length, width, taper) {
    const a = length / 2, b = width / 2, t = Math.max(-0.99, Math.min(0.99, taper == null ? 0.3 : taper));
    const lead = b * (1 + t), trail = b * (1 - t);
    return [[-a, -trail], [a, -lead], [a, lead], [-a, trail]];
  }
  function diamondOutline(length, width) { const a = length / 2, b = width / 2; return [[-a, 0], [0, -b], [a, 0], [0, b]]; }

  function shapeOutline(spec) {
    const s = (spec.shape || "rounded").toLowerCase();
    let out;
    if (s === "rectangle") out = rectangleOutline(spec.length, spec.width);
    else if (s === "rounded") out = roundedOutline(spec.length, spec.width, spec.corner_radius);
    else if (s === "stadium") out = stadiumOutline(spec.length, spec.width);
    else if (s === "ellipse") out = ellipseShape(spec.length, spec.width);
    else if (s === "superellipse") out = superellipseShape(spec.length, spec.width, spec.exponent);
    else if (s === "trapezoid") out = trapezoidOutline(spec.length, spec.width, spec.taper);
    else if (s === "diamond") out = diamondOutline(spec.length, spec.width);
    else throw new Error("unknown contact patch shape " + spec.shape);
    if (Math.abs(spec.rotation || 0) > 1e-9) {
      const th = ((spec.rotation || 0) * Math.PI) / 180, c = Math.cos(th), sn = Math.sin(th);
      out = out.map((p) => [p[0] * c - p[1] * sn, p[0] * sn + p[1] * c]);
    }
    return out;
  }

  function describeSpec(spec) {
    const s = (spec.shape || "rounded").toLowerCase();
    let extra = "";
    if (s === "rounded") extra = ", corner r=" + fmt(spec.corner_radius) + " mm";
    else if (s === "superellipse") extra = ", exponent " + fmt(spec.exponent);
    else if (s === "trapezoid") extra = ", taper " + (spec.taper >= 0 ? "+" : "") + fmt(spec.taper);
    if (Math.abs(spec.rotation || 0) > 1e-9) extra += ", rotated " + fmt(spec.rotation) + " deg";
    return s + " " + fmt(spec.length) + " x " + fmt(spec.width) + " mm" + extra;
  }
  function fmt(v) { return Number(v.toFixed(4)).toString(); }

  // Build a placed ContactPatch object from a shape spec.
  // params: {vertical_load, wheel_radius, load_rises_with_lean}
  function shapePatch(spec, crown, treadWidth, params) {
    params = Object.assign({ vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: true }, params || {});
    const gamma = spec.gamma_deg || 0;
    const yC = spec.y_center != null ? spec.y_center : crownContactLateral(crown, gamma);
    let out = shapeOutline(spec).map((p) => [p[0], p[1] + yC]);
    // clip to tread half width
    const half = treadWidth / 2;
    let clipped = false;
    out = out.map((p) => { const cy = Math.max(-half, Math.min(half, p[1])); if (cy !== p[1]) clipped = true; return [p[0], cy]; });

    const g = (gamma * Math.PI) / 180;
    let load = spec.load_N;
    if (load == null) load = params.load_rises_with_lean ? params.vertical_load / Math.max(Math.cos(g), 0.2) : params.vertical_load;
    const rEff = Math.max(50, params.wheel_radius - crownDrop(crown, yC));

    const patch = {
      gamma_deg: gamma, outline: out, tread_half_width: half, source: "shape",
      provenance: "user-specified shape: " + describeSpec(spec) + (spec.label ? " -- " + spec.label : ""),
      y_center: yC, r_eff: rEff, r_lat: crownLocalRadius(crown, yC), normal_load: load,
      path_radius: gamma <= 1e-9 ? Infinity : rEff / Math.tan(g), delta: 0, clipped: clipped,
    };
    patch.a = patchLength(patch) / 2;
    patch.b = patchWidth(patch) / 2;
    const area = patchArea(patch);
    patch.peak_pressure = area > 0 ? load / area : 0;
    return patch;
  }

  function patchArea(patch) { return polygonArea(patch.outline); }
  function patchPerimeter(patch) { return polygonPerimeter(patch.outline); }
  function patchBbox(patch) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const p of patch.outline) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    return [x0, x1, y0, y1];
  }
  function patchLength(patch) { const b = patchBbox(patch); return b[1] - b[0]; }
  function patchWidth(patch) { const b = patchBbox(patch); return b[3] - b[2]; }

  function patchContains(patch, x, y) {
    const p = patch.outline, n = p.length;
    let inside = false;
    for (let i = 0; i < n; i++) {
      const x0 = p[i][0], y0 = p[i][1], x1 = p[(i + 1) % n][0], y1 = p[(i + 1) % n][1];
      if (y0 === y1) continue;
      const straddles = (y0 > y) !== (y1 > y);
      if (straddles) { const xCross = ((x1 - x0) * (y - y0)) / (y1 - y0) + x0; if (x < xCross) inside = !inside; }
    }
    return inside && Math.abs(y) <= patch.tread_half_width + 1e-9;
  }

  // Rasterise the patch onto the grid, centred on column 0 with wraparound.
  // Returns {binary: Float32Array, pressure: Float32Array} (ny*nx).
  function patchMasks(patch, grid) {
    const ny = grid.ny, nx = grid.nx, dx = grid.dx, N = ny * nx;
    const binary = new Float32Array(N), pressure = new Float32Array(N);
    const p = patch.outline;
    if (p.length < 3) return { binary: binary, pressure: pressure };
    const yv = gridY(grid);
    let loMin = Infinity, hiMax = -Infinity;
    for (let i = 0; i < p.length; i++) { const y0 = p[i][1], y1 = p[(i + 1) % p.length][1]; loMin = Math.min(loMin, Math.min(y0, y1)); hiMax = Math.max(hiMax, Math.max(y0, y1)); }
    const xs = [];
    for (let r = 0; r < ny; r++) {
      const yy = yv[r];
      if (yy < loMin || yy > hiMax) continue;
      xs.length = 0;
      for (let i = 0; i < p.length; i++) {
        const y0 = p[i][1], y1 = p[(i + 1) % p.length][1];
        const lo = Math.min(y0, y1), hi = Math.max(y0, y1);
        if (yy >= lo && yy < hi && y0 !== y1) { const t = (yy - y0) / (y1 - y0); xs.push(p[i][0] + t * (p[(i + 1) % p.length][0] - p[i][0])); }
      }
      if (xs.length < 2) continue;
      xs.sort((a, b) => a - b);
      const rowBase = r * nx;
      for (let i = 0; i + 1 < xs.length; i += 2) {
        const c0 = Math.ceil(xs[i] / dx - 0.5), c1 = Math.ceil(xs[i + 1] / dx - 0.5);
        if (c1 <= c0) continue;
        for (let c = c0; c < c1; c++) { const col = ((c % nx) + nx) % nx; binary[rowBase + col] = 1; }
      }
    }
    // flat pressure = peak_pressure inside
    const peak = patch.peak_pressure || 0;
    for (let i = 0; i < N; i++) if (binary[i] > 0) pressure[i] = peak;
    return { binary: binary, pressure: pressure };
  }

  // =====================================================================
  // 8. the sweep (port of tread_eval/sweep.py)
  // =====================================================================
  // Cache of forward row-transforms of a weight map.  Stores re/im as flat
  // Float64Array of length ny*(nx/2+1).
  function MapFFTCache(pack) { this.pack = pack; this.cache = {}; }
  MapFFTCache.prototype.get = function (key, map) {
    if (this.cache[key]) return this.cache[key];
    const grid = this.pack.grid, ny = grid.ny, nx = grid.nx, half = (nx >> 1) + 1;
    const re = new Float64Array(ny * half), im = new Float64Array(ny * half);
    const row = new Float64Array(nx);
    for (let r = 0; r < ny; r++) {
      const base = r * nx;
      for (let c = 0; c < nx; c++) row[c] = map[base + c];
      const f = rfftRow(row, nx);
      re.set(f.re, r * half); im.set(f.im, r * half);
    }
    const out = { re: re, im: im, half: half, ny: ny };
    this.cache[key] = out;
    return out;
  };

  // Correlate a cached map spectrum with a kernel spectrum (also cached per call).
  function correlate(mapSpec, kerRe, kerIm, nx) {
    const half = mapSpec.half, ny = mapSpec.ny;
    const accRe = new Float64Array(half), accIm = new Float64Array(half);
    for (let r = 0; r < ny; r++) {
      const base = r * half;
      for (let f = 0; f < half; f++) {
        const mr = mapSpec.re[base + f], mi = mapSpec.im[base + f];
        const kr = kerRe[base + f], ki = kerIm[base + f];
        // map * conj(kernel)
        accRe[f] += mr * kr + mi * ki;
        accIm[f] += mi * kr - mr * ki;
      }
    }
    return irfft(accRe, accIm, nx);
  }

  function kernelSpectrum(map, grid) {
    const ny = grid.ny, nx = grid.nx, half = (nx >> 1) + 1;
    const re = new Float64Array(ny * half), im = new Float64Array(ny * half);
    const row = new Float64Array(nx);
    for (let r = 0; r < ny; r++) {
      const base = r * nx;
      for (let c = 0; c < nx; c++) row[c] = map[base + c];
      const f = rfftRow(row, nx);
      re.set(f.re, r * half); im.set(f.im, r * half);
    }
    return { re: re, im: im };
  }

  function maxClamp(arr) { for (let i = 0; i < arr.length; i++) if (arr[i] < 0) arr[i] = 0; return arr; }

  function discreteBlockCount(pack, patch, nSamples, threshold) {
    nSamples = nSamples || 360; threshold = threshold == null ? 0.5 : threshold;
    const grid = pack.grid, nx = grid.nx, ny = grid.ny;
    const halfCols = Math.ceil(patch.a / grid.dx) + 1;
    const width = Math.min(nx, 2 * halfCols + 1);
    const yv = gridY(grid);
    const xRel = new Float64Array(width);
    const cols = new Int32Array(width);
    for (let i = 0; i < width; i++) { cols[i] = i - (width >> 1); xRel[i] = cols[i] * grid.dx; }
    // precompute inside mask (ny x width)
    const inside = new Uint8Array(ny * width);
    for (let r = 0; r < ny; r++) for (let i = 0; i < width; i++) if (patchContains(patch, xRel[i], yv[r])) inside[r * width + i] = 1;
    const nBlocks = pack.blockPixelCount.length;
    const thetas = new Float64Array(nSamples), counts = new Float64Array(nSamples);
    const hits = new Float64Array(nBlocks);
    for (let s = 0; s < nSamples; s++) {
      const th = (360 * s) / nSamples;
      thetas[s] = th;
      const j = Math.round((th / 360) * nx);
      hits.fill(0);
      let touched = false;
      const touchedList = [];
      for (let r = 0; r < ny; r++) {
        const rb = r * width, lb = r * nx;
        for (let i = 0; i < width; i++) {
          if (!inside[rb + i]) continue;
          const col = (((cols[i] + j) % nx) + nx) % nx;
          const lab = pack.labels[lb + col];
          if (lab >= 0) { if (hits[lab] === 0) touchedList.push(lab); hits[lab]++; touched = true; }
        }
      }
      if (!touched) continue;
      let cnt = 0;
      for (const lab of touchedList) if (hits[lab] / Math.max(pack.blockPixelCount[lab], 1) > threshold) cnt++;
      counts[s] = cnt;
    }
    return { theta: thetas, count: counts };
  }

  function shapeMetrics(patch, binary, grid) {
    const px = grid.dx * grid.dy;
    let areaPix = 0; for (let i = 0; i < binary.length; i++) if (binary[i] > 0) areaPix++;
    const area = areaPix * px;
    const per = patchPerimeter(patch);
    // occupied length/width
    const ny = grid.ny, nx = grid.nx;
    const yv = gridY(grid), xRel = gridXRel(grid);
    let colMin = Infinity, colMax = -Infinity, rowMin = Infinity, rowMax = -Infinity;
    for (let r = 0; r < ny; r++) { const base = r * nx; for (let c = 0; c < nx; c++) if (binary[base + c] > 0) { if (xRel[c] < colMin) colMin = xRel[c]; if (xRel[c] > colMax) colMax = xRel[c]; if (yv[r] < rowMin) rowMin = yv[r]; if (yv[r] > rowMax) rowMax = yv[r]; } }
    const length = colMax >= colMin ? colMax - colMin + grid.dx : 0;
    const wdt = rowMax >= rowMin ? rowMax - rowMin + grid.dy : 0;
    return {
      area_mm2: area, perimeter_mm: per, compactness: area > 0 ? (per * per) / area : NaN,
      aspect_ratio: wdt > 0 ? length / wdt : NaN, length_mm: length, width_mm: wdt,
      peak_pressure_mpa: patch.peak_pressure, mean_pressure_mpa: area > 0 ? (patch.peak_pressure * areaPix * px) / area : NaN,
      y_center_mm: patch.y_center, clipped: patch.clipped ? 1 : 0,
    };
  }

  function sweepLean(pattern, pack, gammaDeg, spec, params, cache, discreteSamples) {
    const grid = pack.grid, nx = grid.nx;
    cache = cache || new MapFFTCache(pack);
    const useSpec = Object.assign({}, spec, { gamma_deg: gammaDeg });
    const patch = shapePatch(useSpec, pattern.crown, pattern.tread_width, params);
    const masks = patchMasks(patch, grid);
    const kb = kernelSpectrum(masks.binary, grid);
    const kp = kernelSpectrum(masks.pressure, grid);

    const contactArea = maxClamp(correlate(cache.get("area", pack.area), kb.re, kb.im, nx));
    const kx = maxClamp(correlate(cache.get("kx", pack.kx), kb.re, kb.im, nx));
    const ky = maxClamp(correlate(cache.get("ky", pack.ky), kb.re, kb.im, nx));
    const kz = maxClamp(correlate(cache.get("kz", pack.kz), kb.re, kb.im, nx));
    const blockCount = maxClamp(correlate(cache.get("block_frac", pack.blockFrac), kb.re, kb.im, nx));
    const yMoment = correlate(cache.get("y_moment", pack.yMoment), kb.re, kb.im, nx);
    const pressureArea = maxClamp(correlate(cache.get("area", pack.area), kp.re, kp.im, nx));
    const zoneArea = {};
    for (const z of ZONES) zoneArea[z] = maxClamp(correlate(cache.get("zone_" + z, pack.zoneArea[z]), kb.re, kb.im, nx));

    const shape = shapeMetrics(patch, masks.binary, grid);
    const patchAreaVal = shape.area_mm2;
    const centroidY = new Float64Array(nx), landRatio = new Float64Array(nx);
    for (let i = 0; i < nx; i++) {
      centroidY[i] = contactArea[i] > 1e-9 ? yMoment[i] / Math.max(contactArea[i], 1e-9) : patch.y_center;
      landRatio[i] = patchAreaVal > 0 ? contactArea[i] / patchAreaVal : 0;
    }
    const disc = discreteBlockCount(pack, patch, discreteSamples || 360);
    let patchLoad = 0; for (let i = 0; i < masks.pressure.length; i++) patchLoad += masks.pressure[i];
    patchLoad *= grid.dx * grid.dy;

    return {
      gamma_deg: gammaDeg, patch: { source: patch.source, provenance: patch.provenance, y_center: patch.y_center, a: patch.a, b: patch.b, clipped: patch.clipped, outline: patch.outline, normal_load: patch.normal_load, peak_pressure: patch.peak_pressure },
      theta_deg: gridThetaDeg(grid), contact_area: contactArea, land_ratio: landRatio,
      kx: kx, ky: ky, kz: kz, block_count: blockCount, centroid_y: centroidY,
      zone_area: zoneArea, pressure_area: pressureArea,
      block_count_discrete: disc.count, theta_discrete: disc.theta,
      patch_area: patchAreaVal, patch_perimeter: shape.perimeter_mm, patch_load: patchLoad, shape: shape,
    };
  }

  function sweep(pattern, pack, leanAngles, spec, params, discreteSamples) {
    const cache = new MapFFTCache(pack);
    return leanAngles.map((g) => sweepLean(pattern, pack, g, spec, params, cache, discreteSamples));
  }

  // =====================================================================
  // 9. summary metrics (subset of tread_eval/metrics.py)
  // =====================================================================
  function fluctuationStats(arr) {
    const n = arr.length;
    if (!n) return { mean: 0, std: 0, cov: 0, min: 0, max: 0, range: 0, ptp_pct: 0 };
    let mean = 0, mn = Infinity, mx = -Infinity;
    for (let i = 0; i < n; i++) { mean += arr[i]; if (arr[i] < mn) mn = arr[i]; if (arr[i] > mx) mx = arr[i]; }
    mean /= n;
    let v = 0; for (let i = 0; i < n; i++) v += (arr[i] - mean) * (arr[i] - mean);
    const std = Math.sqrt(v / n);
    return { mean: mean, std: std, cov: mean !== 0 ? std / Math.abs(mean) : 0, min: mn, max: mx, range: mx - mn, ptp_pct: mean !== 0 ? (100 * (mx - mn)) / Math.abs(mean) : 0 };
  }

  // Order spectrum of a signal sampled uniformly over one revolution.
  // Returns {orders:[1..maxOrder], amplitude:[...]} where amplitude is 2*|X_k|/N.
  function orderSpectrum(signal, maxOrder) {
    const n = signal.length;
    // resample to nearest power of 2 for the radix-2 FFT
    const p2 = 1 << Math.ceil(Math.log2(n));
    const re = new Float64Array(p2), im = new Float64Array(p2);
    let mean = 0; for (let i = 0; i < n; i++) mean += signal[i]; mean /= n;
    for (let i = 0; i < p2; i++) { const src = Math.min(n - 1, Math.round((i * n) / p2)); re[i] = signal[src] - mean; }
    fftRadix2(re, im, false);
    maxOrder = Math.min(maxOrder || 60, p2 >> 1);
    const orders = [], amplitude = [];
    for (let k = 1; k <= maxOrder; k++) { orders.push(k); amplitude.push((2 * Math.hypot(re[k], im[k])) / p2); }
    return { orders: orders, amplitude: amplitude };
  }

  function dominantOrders(spec, topN) {
    const idx = spec.orders.map((o, i) => i);
    idx.sort((a, b) => spec.amplitude[b] - spec.amplitude[a]);
    return idx.slice(0, topN || 5).map((i) => ({ order: spec.orders[i], amplitude: spec.amplitude[i] }));
  }

  // =====================================================================
  return {
    // geometry
    ensureCCW, polygonProps, polygonPerimeter, polygonArea, polygonCentroid, offsetPoly, clipPoly, splitBySipe, sipeClippedLength, netContactArea,
    // stiffness
    SHORE_E_TABLE, SHORE_K_TABLE, shoreE, shoreK, calcG, beamKMatrix, effectiveK, computeKz, blockStiffness, effectiveSipes,
    // dxf
    readDxfEntities, entitiesToSegments, buildLoops, closeAcrossSeam, loadPattern, estimatePitchCount, geometricRepeat,
    // zones + crown
    zoneBounds, classifyZone, ZONES, crownDualRadius, crownFromRadiusProfile, crownTangentAngle, crownLocalRadius, crownDrop, crownContactLateral,
    // fft
    fftRadix2, rfftRow, irfft,
    // raster
    makeGrid, gridY, gridXRel, gridThetaDeg, splitAtSeam, rasterise,
    // shapes + patch
    SHAPES, shapeOutline, describeSpec, shapePatch, patchMasks, patchContains, patchArea, patchPerimeter, patchLength, patchWidth,
    // sweep
    MapFFTCache, sweepLean, sweep,
    // metrics
    fluctuationStats, orderSpectrum, dominantOrders,
  };
});
