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

  // The table has five rows. Snapping to the nearest of them meant 55 A and
  // 59 A both evaluated as 50 A (E = 4.0) while 61 A jumped to 60 A
  // (E = 6.89) -- a 72% step in stiffness across one Shore point, invisible to
  // the user. E rises roughly geometrically with hardness, so interpolate the
  // logarithm; that reproduces the tabulated rows exactly and moves smoothly in
  // between. Outside 30-70 A the nearest row still applies (with a warning),
  // because extrapolating a five-point fit is not evidence of anything.
  const SHORE_KEYS = [30, 40, 50, 60, 70];
  function interpShore(s, table, logScale) {
    s = +s;
    if (!Number.isFinite(s)) return table[60];
    if (s <= SHORE_KEYS[0]) return table[SHORE_KEYS[0]];
    if (s >= SHORE_KEYS[SHORE_KEYS.length - 1]) return table[SHORE_KEYS[SHORE_KEYS.length - 1]];
    for (let i = 0; i + 1 < SHORE_KEYS.length; i++) {
      const a = SHORE_KEYS[i], b = SHORE_KEYS[i + 1];
      if (s < a || s > b) continue;
      const f = (s - a) / (b - a);
      const va = table[a], vb = table[b];
      return logScale ? Math.exp(Math.log(va) + f * (Math.log(vb) - Math.log(va))) : va + f * (vb - va);
    }
    return table[nearestShoreKey(s)];
  }
  function shoreE(s) { const v = interpShore(s, SHORE_E_TABLE, true); return v == null ? 6.89 : v; }
  function shoreK(s) { const v = interpShore(s, SHORE_K_TABLE, false); return v == null ? 0.64 : v; }

  // Everything the stiffness model needs to know about the compound, resolved
  // once so the page can show it. Nothing here is hidden: either you gave E and
  // k directly, or they came off the Gent table at the hardness you typed.
  function compoundProperties(params) {
    params = params || {};
    const direct = params.modulus_mode === "direct";
    const E = direct
      ? (Number.isFinite(+params.e_modulus) ? +params.e_modulus : shoreE(params.shore_a))
      : (params.e_override != null ? +params.e_override : shoreE(params.shore_a));
    const k = direct
      ? (Number.isFinite(+params.gent_k) ? +params.gent_k : shoreK(params.shore_a))
      : (params.k_override != null ? +params.k_override : shoreK(params.shore_a));
    const nu = Number.isFinite(+params.poisson) ? +params.poisson : 0.49;
    return {
      E: E, k: k, G: calcG(E, nu), nu: nu,
      source: direct ? "entered directly" : "Gent table at Shore A " + params.shore_a,
      bulk_modulus: params.bulk_modulus || 1100,
    };
  }

  function validateCompound(params) {
    if (!params || params.modulus_mode !== "direct") return;
    const E = +params.e_modulus;
    if (!Number.isFinite(E) || E <= 0)
      throw new Error("Young's modulus E must be a positive number in N/mm^2, got " + params.e_modulus);
    if (E < 0.1 || E > 200)
      throw new Error("Young's modulus E = " + E + " N/mm^2 is outside anything a tread compound reaches " +
        "(roughly 1-20 N/mm^2). Check the units: this field is N/mm^2 (= MPa), not kPa or psi.");
    const k = +params.gent_k;
    if (!Number.isFinite(k) || k <= 0 || k > 5)
      throw new Error("the Gent shape-factor coefficient k must be a positive number of order 1 " +
        "(0.57 at 70 A to 0.93 at 30 A), got " + params.gent_k);
  }

  const SHORE_MIN = 30, SHORE_MAX = 70;
  // Gent's table covers 30-70 A. Outside it the nearest row is used, so a hard
  // 95A compound is silently evaluated as 70A -- a >2x error in E. Surface it.
  function shoreRangeWarning(s) {
    s = +s;
    if (!Number.isFinite(s)) return "Shore A must be a number, got " + s;
    if (s >= SHORE_MIN && s <= SHORE_MAX) return null;
    const key = nearestShoreKey(s);
    return "Shore A " + s + " is outside the validated Gent table (" + SHORE_MIN + "-" + SHORE_MAX +
      " A); properties of " + key + " A were used (E=" + SHORE_E_TABLE[key] + " N/mm^2, k=" +
      SHORE_K_TABLE[key] + "). Treat absolute stiffness values with caution.";
  }
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

  // Siped block as ONE beam of height-varying section (no layer stacking).
  // Above a sipe root the sub-blocks bend as independent parallel springs, so
  // their second moments and areas add; below it the section is whole. The
  // moment-weighted compliance is integrated in a single pass, exactly as the
  // unsiped path does, so no artificial zero-rotation constraint is introduced
  // at a sipe root. Mirrors tread_eval.stiffness._effective_k_continuous.
  function effectiveKContinuous(polyAt, hasDraft, nsd, E, G, mode, sipes, nSlices) {
    const dz = nsd / nSlices;
    let CxxB = 0, CyyB = 0, CxyB = 0, Sh = 0, nValid = 0, nSubs = 1;
    const splitCache = {};
    for (let i = 0; i < nSlices; i++) {
      const z = (i + 0.5) * dz;
      const active = [];
      for (let j = 0; j < sipes.length; j++) if (sipes[j].depth >= nsd - z - 1e-12) active.push(j);
      const key = active.join(",");
      let subs;
      if (!hasDraft && splitCache[key]) {
        subs = splitCache[key];
      } else {
        subs = [polyAt(z)];
        for (const j of active) {
          const next = [];
          for (const sp of subs) next.push(...splitBySipe(sp, sipes[j].p1, sipes[j].p2, sipes[j].width));
          subs = next;
        }
        subs = subs.filter((sp) => { const p = polygonProps(sp); return p && p.A > 1e-9; });
        if (!hasDraft) splitCache[key] = subs;
      }
      if (!subs.length) continue;
      nSubs = Math.max(nSubs, subs.length);
      let ixx = 0, iyy = 0, ixy = 0, area = 0;
      for (const sp of subs) {
        const p = polygonProps(sp);
        if (!p || p.A < 1e-12) continue;
        ixx += p.Ixx; iyy += p.Iyy; ixy += p.Ixy || 0; area += p.A;
      }
      if (area <= 1e-12) continue;
      const detI = ixx * iyy - ixy * ixy;
      if (detI <= 1e-15) continue;
      const w = mode === "parallel" ? (z - nsd / 2) * (z - nsd / 2) : (nsd - z) * (nsd - z);
      CxxB += (w * ixx) / detI * dz;
      CyyB += (w * iyy) / detI * dz;
      CxyB += (w * -ixy) / detI * dz;
      Sh += dz / area;
      nValid++;
    }
    if (!nValid) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: nSubs };
    const shearFactor = mode === "parallel" ? 1 : 6 / 5;
    const Cxx = CxxB / E + (shearFactor * Sh) / G;
    const Cyy = CyyB / E + (shearFactor * Sh) / G;
    const Cxy = CxyB / E;
    const detC = Cxx * Cyy - Cxy * Cxy;
    if (detC <= 1e-18) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: nSubs };
    const Kx = Cyy / detC, Ky = Cxx / detC;
    let Kxy = -Cxy / detC;
    if (Math.abs(Kxy) < 1e-6 * Math.max(Kx, Ky)) Kxy = 0;
    return { Kx: Kx, Ky: Ky, Kxy: Kxy, nSubs: nSubs };
  }

  function effectiveK(verts, nsd, E, nu, draft, mode, sipes, nSlices, sipeModel) {
    const G = calcG(E, nu);
    if (nsd <= 0 || E <= 0 || G <= 0 || verts.length < 3) return { Kx: 0, Ky: 0, Kxy: 0, nSubs: 1 };
    const hasDraft = !!(draft && Math.abs(draft) > 1e-12);
    function polyAt(z) { return hasDraft ? offsetPoly(verts, draft, nsd - z) : verts; }
    sipes = sipes || [];
    if (sipes.length && sipeModel === "continuous")
      return effectiveKContinuous(polyAt, hasDraft, nsd, E, G, mode, sipes, nSlices || 40);

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
    else { const cp = compoundProperties(params); E = cp.E; k = cp.k; }
    const sipes = effectiveSipes(block);
    const sh = effectiveK(verts, block.height, E, params.poisson, block.draft_angle || 0, params.mode, sipes, params.n_slices || 40, params.sipe_model);
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

  // ---------------------------------------------------------------------
  // wear state and tie bars
  // ---------------------------------------------------------------------
  // Put the groove bottom at z = 0. A block's top starts at z = NSD and, after
  // the tyre has worn w mm, sits at z = NSD - w; its beam length is therefore
  // NSD - w. A tie bar is a raised strip of the groove bottom of height h < NSD,
  // so its top starts at z = h -- (NSD - h) BELOW the road. It touches nothing
  // until the tread has worn that far. Once it does, it is flush with the blocks
  // and is simply more land at the same height, which is exactly why tie bars
  // are used: they arrive part-worn and stiffen the block row against the
  // heel-and-toe wear that is developing by then.
  //
  // So the whole model is: main blocks lose w off their height; a tie bar joins
  // the block list, at the blocks' current height, once w >= NSD - h. Nothing
  // else about the pipeline changes. At the default w = 0 the tie bars are drawn
  // and listed but contribute nothing, which is the truth for a new tyre.
  function tiebarEngagementWear(tb) {
    const nsd = Number.isFinite(+tb.nsd) ? +tb.nsd : 0;
    const h = Number.isFinite(+tb.height) ? +tb.height : 0;
    return Math.max(0, nsd - h);
  }

  // A bar is in contact when the tread has worn down to it, and at no other
  // time. There is deliberately no override: a bar shorter than the blocks
  // around it cannot touch the road while they do, so "in contact anyway" is
  // not a what-if, it is an impossible geometry. A bar that really does reach
  // the surface has height == NSD, which engages here at zero wear with the
  // geometry actually matching the claim.
  function tiebarEngaged(tb, wear) {
    if (tb.enabled === false) return false;
    return (wear || 0) >= tiebarEngagementWear(tb) - 1e-9;
  }

  // The block list the rest of the pipeline should see at this wear state.
  function effectiveBlocks(pattern, wear, opts) {
    wear = Number.isFinite(+wear) ? Math.max(0, +wear) : 0;
    opts = opts || {};
    const minH = 0.1;
    const out = [];
    for (const b of pattern.blocks || []) {
      const h = (b.height || 0) - wear;
      if (h < minH) continue;                      // worn past this block entirely
      out.push(wear > 0 ? Object.assign({}, b, { height: h }) : b);
    }
    const engaged = [];
    for (const tb of pattern.tiebars || []) {
      if (!tiebarEngaged(tb, wear)) continue;
      // Engaged means worn into, so the bar is flush with the blocks and has
      // their current height. There is no other way to be in contact.
      const h = Math.max(minH, (tb.nsd || 0) - wear);
      engaged.push({
        id: tb.id, pitch_id: tb.pitch_id || "", polygon: tb.polygon, zone: tb.zone,
        height: h, draft_angle: tb.draft_angle || 0,
        n_lateral_sipes: tb.n_lateral_sipes || 0,
        sipe_depth_fraction: tb.sipe_depth_fraction, sipe_width: tb.sipe_width,
        shore_a: tb.shore_a, sipes: tb.sipes || [], is_tiebar: true,
      });
    }
    return opts.separate ? { blocks: out, tiebars: engaged } : out.concat(engaged);
  }

  function validateWear(wear, pattern) {
    if (wear == null) return null;
    const w = +wear;
    if (!Number.isFinite(w) || w < 0)
      throw new Error("tread wear must be a non-negative number of mm, got " + wear);
    const maxH = (pattern && pattern.blocks || []).reduce((m, b) => Math.max(m, b.height || 0), 0);
    if (maxH > 0 && w >= maxH)
      throw new Error("tread wear of " + w + " mm removes every block (the deepest is " +
        maxH.toFixed(2) + " mm). Enter a wear less than the NSD.");
    return null;
  }

  // Apply the wear state to a pattern, returning a pattern the sweep can use
  // directly. Kept separate so the untouched original stays available to draw.
  function patternAtWear(pattern, wear) {
    if (!wear && !(pattern.tiebars || []).some((t) => tiebarEngaged(t, 0))) return pattern;
    validateWear(wear, pattern);
    return Object.assign({}, pattern, { blocks: effectiveBlocks(pattern, wear), wear_mm: wear || 0 });
  }

  // =====================================================================
  // 5b. tie-bar coupling network  ("Tier 2")
  // =====================================================================
  // Everywhere else in this engine a block is an independent spring: its
  // stiffness depends on its own polygon and nothing else, and the patch
  // aggregate is the sum of the springs it covers. That is what makes the theta
  // sweep an FFT. It is also exactly wrong for a tie bar, whose entire purpose
  // is to make neighbouring blocks NOT independent.
  //
  // So: build the real network. Blocks and tie bars are nodes, each with two
  // degrees of freedom (circumferential x, lateral y) and its own 2x2 stiffness
  // to the belt. Every bar is linked to the blocks it touches by a spring
  // representing the rubber between them. Solve the assembly and the answer
  // falls out with no special cases: a bar with a free wall on one side, two
  // bars of different heights on the same block, a whole rib tied end to end,
  // and -- critically -- blocks that move TOGETHER, where the bars carry no
  // load and correctly do nothing.
  //
  // A sub-surface bar carries no contact load (it is below the road) but is
  // still in the network, so it stiffens without adding contact area. That is
  // the effect the surface-contact model cannot represent at all.

  function polygonEdgeKeys(poly) {
    const out = [];
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length];
      const ka = a[0].toFixed(6) + "," + a[1].toFixed(6);
      const kb = b[0].toFixed(6) + "," + b[1].toFixed(6);
      out.push({
        key: ka < kb ? ka + "|" + kb : kb + "|" + ka,
        len: Math.hypot(b[0] - a[0], b[1] - a[1]),
        mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2],
      });
    }
    return out;
  }

  // Which blocks each tie bar is bonded to, and over what wall.
  //
  // Only bar-to-something links are made. Two blocks that merely abut are not
  // linked: the span between them is zero, which would make the link infinitely
  // stiff, and a tread plan with no groove between two blocks is a drawing
  // question rather than a tie bar.
  function linkTiebars(blocks, tiebars) {
    if (!tiebars || !tiebars.length) return;
    const owner = new Map();
    const put = (kind, idx, poly) => {
      for (const e of polygonEdgeKeys(poly)) {
        let l = owner.get(e.key);
        if (!l) { l = []; owner.set(e.key, l); }
        l.push({ kind: kind, idx: idx, len: e.len, mid: e.mid });
      }
    };
    for (let i = 0; i < blocks.length; i++) put("block", i, blocks[i].polygon);
    for (let i = 0; i < tiebars.length; i++) put("tiebar", i, tiebars[i].polygon);

    for (const tb of tiebars) tb.links = [];
    const acc = new Map();                        // "tbIdx|kind|idx" -> record
    owner.forEach((list) => {
      if (list.length < 2) return;
      for (let a = 0; a < list.length; a++) {
        for (let b = a + 1; b < list.length; b++) {
          const A = list[a], B = list[b];
          if (A.kind !== "tiebar" && B.kind !== "tiebar") continue;
          if (A.kind === B.kind && A.idx === B.idx) continue;        // self pair
          const t = A.kind === "tiebar" ? A : B;
          const o = A.kind === "tiebar" ? B : A;
          if (o.kind === "tiebar" && o.idx === t.idx) continue;
          const key = t.idx + "|" + o.kind + "|" + o.idx;
          let rec = acc.get(key);
          if (!rec) { rec = { tb: t.idx, kind: o.kind, idx: o.idx, len: 0, mx: 0, my: 0 }; acc.set(key, rec); }
          // Several edges can be shared where an outline is split at a node;
          // accumulate the length and take the length-weighted wall midpoint.
          rec.len += A.len;
          rec.mx += A.mid[0] * A.len;
          rec.my += A.mid[1] * A.len;
        }
      }
    });
    acc.forEach((rec) => {
      if (rec.len <= 1e-9) return;
      const tb = tiebars[rec.tb];
      const wall = [rec.mx / rec.len, rec.my / rec.len];
      const dx = tb.centroid_x - wall[0], dy = tb.centroid_y - wall[1];
      const d = Math.hypot(dx, dy);
      if (!(d > 1e-9)) return;                    // degenerate: wall through the centroid
      tb.links.push({
        kind: rec.kind, index: rec.idx,
        wall_length: rec.len, span: d, dir: [dx / d, dy / d],
      });
    });
    for (const tb of tiebars) tb.n_neighbours = tb.links.length;
  }

  // 2x2 stiffness of one bonded link, as a truss along the span plus shear
  // across it:  C = k_ax * n n^T + k_tr * (I - n n^T).
  //
  //   k_ax = E * A / d   relative motion along the span stretches the bar
  //   k_tr = G * A / d   relative motion across it shears the bar
  //
  // A is the bonded wall area (shared wall length x bar height) and d the
  // distance from that wall to the bar's centre -- about half the groove width.
  // A diagonal bar therefore couples x into y, which is where the network's
  // contribution to Kxy comes from.
  function couplingLinkMatrix(dir, kAx, kTr) {
    const nx = dir[0], ny = dir[1];
    return {
      xx: kAx * nx * nx + kTr * ny * ny,
      xy: (kAx - kTr) * nx * ny,
      yy: kAx * ny * ny + kTr * nx * nx,
    };
  }

  // Bar height above the groove bottom at this wear state: its own height until
  // the tread wears down to it, and thereafter the same as the blocks, because
  // from then on it wears with them.
  function tiebarCurrentHeight(tb, wear) {
    const nsd = +tb.nsd || 0, h = +tb.height || 0;
    return Math.min(h, Math.max(0, nsd - (wear || 0)));
  }

  // Floor on any element height, block or bar. Below this the beam formulae
  // stop meaning anything (a 0.01 mm tall block is stiffer than steel), so the
  // height is clamped. The tie-bar height input is bounded well above it.
  const COUPLING_MIN_HEIGHT = 0.1;

  // ---------------------------------------------------------------------
  // grouping tie bars
  // ---------------------------------------------------------------------
  // A mould repeats the same bar around the circumference, so a drawing with
  // 150 bars usually has two or three distinct ones. Setting each individually
  // is transcription, not design. Cluster them by what a designer would call
  // "the same bar" and the work collapses to one edit per family.
  //
  // Clustered against a representative with tolerances, not hashed into
  // buckets: two bars 0.001 mm apart in area must never land in different
  // groups because a bucket edge fell between them.
  var TIEBAR_GROUP_MODES = ["shape_position", "shape", "zone", "none"];

  function tiebarMetrics(tb) {
    const b = bbox(tb.polygon);
    return { span: b[1] - b[0], width: b[3] - b[2], area: Math.abs(tb.area), y: tb.centroid_y };
  }

  function groupTiebars(tiebars, mode, tol) {
    tiebars = tiebars || [];
    mode = TIEBAR_GROUP_MODES.indexOf(mode) >= 0 ? mode : "shape_position";
    tol = tol || {};
    const areaRel = tol.area_rel == null ? 0.02 : tol.area_rel;   // 2% of area
    const lenAbs = tol.length_mm == null ? 0.5 : tol.length_mm;   // mm
    const yAbs = tol.y_mm == null ? 1.0 : tol.y_mm;               // mm

    const idx = tiebars.map((t, i) => i);
    // Stable order so the grouping is reproducible run to run.
    const m = tiebars.map(tiebarMetrics);
    idx.sort((a, b) =>
      (m[a].area - m[b].area) || (Math.abs(m[a].y) - Math.abs(m[b].y)) ||
      (m[a].span - m[b].span) || (a - b));

    const groups = [];
    for (const i of idx) {
      const t = tiebars[i], mi = m[i];
      let found = null;
      if (mode !== "none") {
        for (const g of groups) {
          const mr = m[g.rep];
          if (tiebars[g.rep].zone !== t.zone) continue;
          if (mode !== "zone") {
            if (Math.abs(mi.area - mr.area) > areaRel * Math.max(mi.area, mr.area, 1e-9)) continue;
            if (Math.abs(mi.span - mr.span) > lenAbs) continue;
            if (Math.abs(mi.width - mr.width) > lenAbs) continue;
          }
          // Mirror pairs at +y and -y are the same mould feature, so they group
          // together; the group reports the y range so that stays visible.
          if (mode === "shape_position" && Math.abs(Math.abs(mi.y) - Math.abs(mr.y)) > yAbs) continue;
          found = g;
          break;
        }
      }
      if (found) found.members.push(i);
      else groups.push({ rep: i, members: [i] });
    }

    return groups.map(function (g, gi) {
      const mem = g.members.slice().sort((a, b) => a - b);
      const first = tiebars[g.rep], fm = m[g.rep];
      let yLo = Infinity, yHi = -Infinity, aLo = Infinity, aHi = -Infinity;
      let height = null, mixedHeight = false, nEnabled = 0;
      for (const k of mem) {
        const t = tiebars[k];
        yLo = Math.min(yLo, t.centroid_y); yHi = Math.max(yHi, t.centroid_y);
        aLo = Math.min(aLo, Math.abs(t.area)); aHi = Math.max(aHi, Math.abs(t.area));
        if (height == null) height = +t.height;
        else if (Math.abs(+t.height - height) > 1e-9) mixedHeight = true;
        if (t.enabled !== false) nEnabled++;
      }
      return {
        index: gi,
        id: "G" + String(gi).padStart(2, "0"),
        members: mem,
        count: mem.length,
        zone: first.zone,
        nsd: first.nsd,
        height: mixedHeight ? null : height,
        mixed_height: mixedHeight,
        n_enabled: nEnabled,
        area_mm2: fm.area,
        area_range: [aLo, aHi],
        span_mm: fm.span,
        width_mm: fm.width,
        y_range: [yLo, yHi],
      };
    });
  }

  // Apply one property to every bar in a group.
  function applyToTiebarGroup(tiebars, group, changes) {
    for (const k of group.members) {
      const t = tiebars[k];
      if (!t) continue;
      if (changes.height != null) {
        const h = +changes.height;
        if (Number.isFinite(h) && h > 0) { t.height = Math.min(h, +t.nsd); t.height_set_by_user = true; }
      }
      if (changes.height_fraction != null) {
        const f = +changes.height_fraction;
        if (Number.isFinite(f) && f > 0) { t.height = Math.min(+t.nsd, f * +t.nsd); t.height_set_by_user = true; }
      }
      if (changes.enabled != null) t.enabled = !!changes.enabled;
    }
    return group.members.length;
  }

  function buildCouplingNetwork(pattern, wear, params) {
    wear = Math.max(0, +wear || 0);
    const cp = compoundProperties(params);
    const minH = COUPLING_MIN_HEIGHT;
    const nodes = [];
    const idOf = {};

    // Main blocks, in the same order effectiveBlocks() emits them, so a raster
    // label maps straight onto a node.
    for (const b of pattern.blocks || []) {
      const h = (b.height || 0) - wear;
      if (h < minH) continue;
      const blk = wear > 0 ? Object.assign({}, b, { height: h }) : b;
      idOf[b.id] = nodes.length;
      nodes.push({ id: b.id, kind: "block", ref: b, K: blockStiffness(blk, params), height: h });
    }
    const nMain = nodes.length;

    // Then the engaged bars (also matching effectiveBlocks), then the rest.
    const bars = (pattern.tiebars || []).filter((t) => t.enabled !== false);
    const engaged = bars.filter((t) => tiebarEngaged(t, wear));
    const submerged = bars.filter((t) => !tiebarEngaged(t, wear));
    for (const list of [engaged, submerged]) {
      for (const tb of list) {
        const h = Math.max(minH, tiebarCurrentHeight(tb, wear));
        const el = { polygon: tb.polygon, height: h, draft_angle: tb.draft_angle || 0,
                     n_lateral_sipes: 0, sipes: [], shore_a: tb.shore_a };
        idOf[tb.id] = nodes.length;
        nodes.push({ id: tb.id, kind: "tiebar", ref: tb, K: blockStiffness(el, params), height: h,
                     engaged: tiebarEngaged(tb, wear) });
      }
    }
    if (nodes.length < 2) return null;

    const links = [];
    for (const tb of bars) {
      const ti = idOf[tb.id];
      if (ti == null) continue;
      const hb = Math.max(minH, tiebarCurrentHeight(tb, wear));
      for (const lk of tb.links || []) {
        const other = lk.kind === "block" ? (pattern.blocks[lk.index] || {}).id
                                          : ((pattern.tiebars[lk.index] || {}).id);
        const oi = idOf[other];
        if (oi == null || oi === ti) continue;    // worn away, or excluded by the user
        const A = lk.wall_length * hb;
        if (!(A > 0) || !(lk.span > 0)) continue;
        links.push({ i: ti, j: oi,
                     C: couplingLinkMatrix(lk.dir, (cp.E * A) / lk.span, (cp.G * A) / lk.span) });
      }
    }

    // Connected components: ribs do not talk to each other, so each is its own
    // small dense system. Without this a 700-block drawing would need a
    // 1400x1400 inverse instead of six 240x240 ones.
    const N = nodes.length;
    const comp = new Int32Array(N).fill(-1);
    const adj = new Array(N);
    for (let i = 0; i < N; i++) adj[i] = [];
    for (const L of links) { adj[L.i].push(L.j); adj[L.j].push(L.i); }
    let nComp = 0;
    for (let s = 0; s < N; s++) {
      if (comp[s] >= 0) continue;
      const stack = [s];
      comp[s] = nComp;
      while (stack.length) {
        const v = stack.pop();
        for (const w of adj[v]) if (comp[w] < 0) { comp[w] = nComp; stack.push(w); }
      }
      nComp++;
    }
    const members = new Array(nComp);
    for (let c = 0; c < nComp; c++) members[c] = [];
    const localOf = new Int32Array(N);
    for (let i = 0; i < N; i++) { localOf[i] = members[comp[i]].length; members[comp[i]].push(i); }

    return { nodes: nodes, links: links, nMain: nMain, comp: comp, localOf: localOf,
             members: members, nComp: nComp, compound: cp, wear: wear };
  }

  // In-place Cholesky of a symmetric positive-definite dense matrix, then the
  // explicit inverse. n is small (a rib), and the inverse is what the per-angle
  // quadratic forms below need.
  function choleskyInverse(A, n) {
    const L = new Float64Array(n * n);
    for (let j = 0; j < n; j++) {
      for (let i = j; i < n; i++) {
        let s = A[i * n + j];
        for (let k = 0; k < j; k++) s -= L[i * n + k] * L[j * n + k];
        if (i === j) {
          if (!(s > 1e-300)) return null;         // not positive definite
          L[j * n + j] = Math.sqrt(s);
        } else {
          L[i * n + j] = s / L[j * n + j];
        }
      }
    }
    // invert L (lower triangular), then A^-1 = L^-T L^-1
    const Li = new Float64Array(n * n);
    for (let i = 0; i < n; i++) {
      Li[i * n + i] = 1 / L[i * n + i];
      for (let j = 0; j < i; j++) {
        let s = 0;
        for (let k = j; k < i; k++) s += L[i * n + k] * Li[k * n + j];
        Li[i * n + j] = -s / L[i * n + i];
      }
    }
    const inv = new Float64Array(n * n);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j <= i; j++) {
        let s = 0;
        for (let k = i > j ? i : j; k < n; k++) s += Li[k * n + i] * Li[k * n + j];
        inv[i * n + j] = s; inv[j * n + i] = s;
      }
    }
    return inv;
  }

  const COUPLING_DOF_LIMIT = 1600;

  // Assemble and invert each component once. K does not depend on theta -- only
  // the load does -- so this is done a single time per wear state and every
  // angle is then three small quadratic forms.
  function factorCouplingNetwork(net) {
    const inv = new Array(net.nComp);
    for (let c = 0; c < net.nComp; c++) {
      const mem = net.members[c], m = mem.length, n = 2 * m;
      if (n > COUPLING_DOF_LIMIT)
        throw new Error(
          "the tie-bar network has a connected group of " + m + " elements (" + n +
          " degrees of freedom), past the " + COUPLING_DOF_LIMIT + " this solver handles. " +
          "Import a single pitch rather than the whole wheel."
        );
      const A = new Float64Array(n * n);
      for (let a = 0; a < m; a++) {
        const K = net.nodes[mem[a]].K;
        A[(2 * a) * n + (2 * a)] += K.kx;
        A[(2 * a) * n + (2 * a + 1)] += K.kxy;
        A[(2 * a + 1) * n + (2 * a)] += K.kxy;
        A[(2 * a + 1) * n + (2 * a + 1)] += K.ky;
      }
      for (const L of net.links) {
        if (net.comp[L.i] !== c) continue;
        const a = net.localOf[L.i], b = net.localOf[L.j], C = L.C;
        const add = (r, s, v) => { A[r * n + s] += v; };
        // K_ii += C, K_jj += C, K_ij -= C, K_ji -= C
        for (const [p, q, sgn] of [[a, a, 1], [b, b, 1], [a, b, -1], [b, a, -1]]) {
          add(2 * p, 2 * q, sgn * C.xx);
          add(2 * p, 2 * q + 1, sgn * C.xy);
          add(2 * p + 1, 2 * q, sgn * C.xy);
          add(2 * p + 1, 2 * q + 1, sgn * C.yy);
        }
      }
      const Ai = choleskyInverse(A, n);
      if (!Ai)
        throw new Error("the tie-bar network is not positive definite; a block or bar has zero stiffness");
      inv[c] = Ai;
    }
    net.inv = inv;
    return net;
  }

  function invert2x2(c) {
    const det = c.xx * c.yy - c.xy * c.xy;
    if (!(Math.abs(det) > 1e-300)) return { xx: 0, xy: 0, yy: 0 };
    return { xx: c.yy / det, xy: -c.xy / det, yy: c.xx / det };
  }

  // Effective 2x2 stiffness of whatever the patch is holding, at one angle.
  //
  // A total force F is applied to the loaded nodes in proportion to how much of
  // each is inside the patch, and the response is the load-weighted mean
  // displacement.  With q the normalised weights, the compliance is
  //   C[b][a] = sum_ij q_i q_j (K^-1)[(i,b),(j,a)]
  // which is symmetric by reciprocity, and the effective stiffness is its
  // inverse.  Removing every link makes K block diagonal and the same formula
  // collapses to sum_i q_i^2 K_i^-1 -- which is the reference the coupled result
  // is compared against, so the two are the same measurement either way.
  function effectiveStiffnessAt(net, idx, w) {
    let W = 0;
    for (let k = 0; k < w.length; k++) W += w[k];
    if (!(W > 0)) return null;
    const q = new Float64Array(w.length);
    for (let k = 0; k < w.length; k++) q[k] = w[k] / W;

    // coupled: group the loaded nodes by component and use that component's
    // inverse; nodes in different components cannot interact.
    const byComp = new Map();
    for (let k = 0; k < idx.length; k++) {
      const c = net.comp[idx[k]];
      let l = byComp.get(c);
      if (!l) { l = []; byComp.set(c, l); }
      l.push(k);
    }
    let cxx = 0, cxy = 0, cyy = 0;
    byComp.forEach((ks, c) => {
      const Ai = net.inv[c], n = 2 * net.members[c].length;
      for (let a = 0; a < ks.length; a++) {
        const ia = net.localOf[idx[ks[a]]], qa = q[ks[a]];
        for (let b = 0; b < ks.length; b++) {
          const ib = net.localOf[idx[ks[b]]], qq = qa * q[ks[b]];
          cxx += qq * Ai[(2 * ia) * n + (2 * ib)];
          cxy += qq * Ai[(2 * ia) * n + (2 * ib + 1)];
          cyy += qq * Ai[(2 * ia + 1) * n + (2 * ib + 1)];
        }
      }
    });

    // uncoupled reference: the same formula with the links removed.
    let uxx = 0, uxy = 0, uyy = 0;
    for (let k = 0; k < idx.length; k++) {
      const Ki = net.nodes[idx[k]].K;
      const Ci = invert2x2({ xx: Ki.kx, xy: Ki.kxy, yy: Ki.ky });
      const qq = q[k] * q[k];
      uxx += qq * Ci.xx; uxy += qq * Ci.xy; uyy += qq * Ci.yy;
    }

    return {
      coupled: invert2x2({ xx: cxx, xy: cxy, yy: cyy }),
      uncoupled: invert2x2({ xx: uxx, xy: uxy, yy: uyy }),
      load: W,
    };
  }

  // Run-length encode a row-major map into per-row runs of equal value.
  // The per-angle work below is then an interval intersection over a handful of
  // runs instead of a walk over every pixel the patch covers: on a 2048 x 350
  // grid that is ~1600 operations an angle rather than 112,000, and the answer
  // is identical because the runs tile the same pixels.
  function rowRunsOfLabels(labels, nx, ny) {
    const rows = new Array(ny);
    for (let r = 0; r < ny; r++) {
      const base = r * nx, s = [], e = [], l = [];
      let c = 0;
      while (c < nx) {
        const v = labels[base + c];
        if (v < 0) { c++; continue; }
        let k = c + 1;
        while (k < nx && labels[base + k] === v) k++;
        s.push(c); e.push(k); l.push(v);
        c = k;
      }
      rows[r] = { s: Int32Array.from(s), e: Int32Array.from(e), l: Int32Array.from(l) };
    }
    return rows;
  }

  function rowRunsOfMask(binary, nx, ny) {
    const rows = new Array(ny);
    for (let r = 0; r < ny; r++) {
      const base = r * nx, s = [], e = [];
      let c = 0;
      while (c < nx) {
        if (!(binary[base + c] > 0)) { c++; continue; }
        let k = c + 1;
        while (k < nx && binary[base + k] > 0) k++;
        s.push(c); e.push(k);
        c = k;
      }
      rows[r] = s.length ? { s: Int32Array.from(s), e: Int32Array.from(e) } : null;
    }
    return rows;
  }

  // First run whose end is strictly past `p`.
  function firstRunAfter(run, p) {
    let lo = 0, hi = run.e.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (run.e[m] > p) hi = m; else lo = m + 1; }
    return lo;
  }

  // Build and factorise once. The network depends on geometry, compound and
  // wear -- never on the lean angle or the patch -- so a lean sweep reuses one
  // factorisation and only the load changes.
  function prepareCouplingNetwork(pattern, wear, params) {
    const bars = (pattern.tiebars || []).filter((t) => t.enabled !== false);
    if (!bars.length) return null;
    const net = buildCouplingNetwork(pattern, wear, params);
    if (!net || !net.links.length) return null;
    return factorCouplingNetwork(net);
  }

  // The whole theta sweep of the coupled network.
  function couplingSweep(pattern, pack, patch, wear, params, nTheta, prepared) {
    const net = prepared || prepareCouplingNetwork(pattern, wear, params);
    if (!net) return null;

    const grid = pack.grid, nx = grid.nx, ny = grid.ny;
    const masks = patchMasks(patch, grid);
    const labRuns = rowRunsOfLabels(pack.labels, nx, ny);
    const patRuns = rowRunsOfMask(masks.binary, nx, ny);
    const rowsUsed = [];
    for (let r = 0; r < ny; r++) if (patRuns[r]) rowsUsed.push(r);
    if (!rowsUsed.length) return null;
    const rowArea = pack.rowArea || (function () {
      const a = new Float64Array(ny); a.fill(grid.dx * grid.dy); return a;
    })();

    nTheta = nTheta || 720;
    // Sample on a divisor of the raster so a sample lands exactly on a column,
    // making the per-node areas below add up to the FFT contact area exactly.
    const step = Math.max(1, Math.round(nx / nTheta));
    const nS = Math.floor(nx / step);
    const nNodes = net.nodes.length;
    const acc = new Float64Array(nNodes);

    const theta = new Float64Array(nS);
    const kxC = new Float64Array(nS), kyC = new Float64Array(nS), kxyC = new Float64Array(nS);
    const kxU = new Float64Array(nS), kyU = new Float64Array(nS), kxyU = new Float64Array(nS);
    const areaOf = new Float64Array(nS), nLoaded = new Float64Array(nS);

    for (let s = 0; s < nS; s++) {
      const j = s * step;
      acc.fill(0);
      for (let ri = 0; ri < rowsUsed.length; ri++) {
        // fround because the FFT path reads these areas out of a Float32Array;
        // matching its rounding keeps the two routes bit-comparable, which is
        // what makes the "areas agree with the FFT" audit check meaningful.
        const r = rowsUsed[ri], pRun = patRuns[r], lRun = labRuns[r], ra = Math.fround(rowArea[r]);
        if (!lRun.s.length) continue;
        for (let k = 0; k < pRun.s.length; k++) {
          // The patch run, shifted to where the pattern sits at this angle, then
          // wrapped into [0, nx) as at most two pieces.
          const a = pRun.s[k] + j, b = pRun.e[k] + j;
          const pieces = [];
          const a0 = a % nx, b0 = a0 + (b - a);
          if (b0 <= nx) pieces.push([a0, b0]);
          else { pieces.push([a0, nx]); pieces.push([0, b0 - nx]); }
          for (const [p, q] of pieces) {
            let m = firstRunAfter(lRun, p);
            for (; m < lRun.s.length && lRun.s[m] < q; m++) {
              const lo = lRun.s[m] > p ? lRun.s[m] : p;
              const hi = lRun.e[m] < q ? lRun.e[m] : q;
              if (hi > lo) acc[lRun.l[m]] += (hi - lo) * ra;
            }
          }
        }
      }
      const idx = [], w = [];
      let tot = 0;
      for (let k = 0; k < nNodes; k++) if (acc[k] > 0) { idx.push(k); w.push(acc[k]); tot += acc[k]; }
      theta[s] = (j * 360) / nx;
      areaOf[s] = tot;
      nLoaded[s] = idx.length;
      const r = idx.length ? effectiveStiffnessAt(net, idx, w) : null;
      if (!r) continue;
      kxC[s] = r.coupled.xx; kyC[s] = r.coupled.yy; kxyC[s] = r.coupled.xy;
      kxU[s] = r.uncoupled.xx; kyU[s] = r.uncoupled.yy; kxyU[s] = r.uncoupled.xy;
    }

    const gain = (a, b) => {
      let s = 0, n = 0;
      for (let i = 0; i < a.length; i++) if (b[i] > 0) { s += a[i] / b[i]; n++; }
      return n ? s / n : 1;
    };
    return {
      theta_deg: Array.prototype.slice.call(theta),
      kx_coupled: Array.prototype.slice.call(kxC), ky_coupled: Array.prototype.slice.call(kyC),
      kxy_coupled: Array.prototype.slice.call(kxyC),
      kx_uncoupled: Array.prototype.slice.call(kxU), ky_uncoupled: Array.prototype.slice.call(kyU),
      kxy_uncoupled: Array.prototype.slice.call(kxyU),
      contact_area: Array.prototype.slice.call(areaOf),
      n_loaded: Array.prototype.slice.call(nLoaded),
      gain_kx: gain(kxC, kxU), gain_ky: gain(kyC, kyU),
      n_nodes: net.nodes.length, n_links: net.links.length, n_components: net.nComp,
      n_engaged: net.nodes.filter(function (n) { return n.kind === "tiebar" && n.engaged; }).length,
      n_submerged: net.nodes.filter(function (n) { return n.kind === "tiebar" && !n.engaged; }).length,
      wear_mm: net.wear, compound: net.compound,
    };
  }

  // =====================================================================
  // 3. DXF import (port of tread_eval/dxf.py)
  // =====================================================================
  // A DXF group is two physical lines: an integer code, then its value. The old
  // reader assumed line 0 of the file was the first code and paired
  // (0,1),(2,3),... straight off. One stray blank line or a UTF-8 BOM ahead of
  // the first code shifts every pair by one, the codes become values, and a
  // perfectly good drawing imports as "no usable entities". That is most of the
  // "sometimes it works" -- it depends on how the file was written, not on what
  // is in it. So find the alignment by looking at the data instead of assuming.
  function dxfPairs(text) {
    let raw = String(text).replace(/^﻿/, "").split(/\r\n|\r|\n/);
    // Choose the offset (0 or 1) under which the "code" slots really do hold
    // integers. A correctly aligned file scores ~1.0; a shifted one scores ~0.
    function score(off) {
      let ok = 0, seen = 0;
      for (let i = off; i + 1 < raw.length && seen < 400; i += 2) {
        const t = raw[i].trim();
        if (t === "") continue;
        seen++;
        if (/^-?\d+$/.test(t)) ok++;
      }
      return seen ? ok / seen : 0;
    }
    let off = 0;
    if (score(1) > score(0)) off = 1;
    const pairs = [];
    for (let i = off; i + 1 < raw.length; i += 2) {
      const c = raw[i].trim();
      if (c === "") continue;
      pairs.push([c, raw[i + 1].trim()]);
    }
    return pairs;
  }

  // Read the ENTITIES section and, separately, every definition in BLOCKS so
  // that INSERT references can be expanded. A tread plan drawn as one pitch and
  // arrayed with INSERT used to import as nothing at all.
  function readDxfEntities(text) {
    const pairs = dxfPairs(text);
    const entities = [], blocks = {};
    let section = null, current = null, target = null;
    let blockName = null, blockBase = [0, 0];
    const seenTypes = {};
    for (let idx = 0; idx < pairs.length; idx++) {
      const code = pairs[idx][0], value = pairs[idx][1];
      if (code === "0" && value === "SECTION") {
        section = idx + 1 < pairs.length ? pairs[idx + 1][1] : null;
        target = section === "ENTITIES" ? entities : null;
        current = null;
        continue;
      }
      if (code === "0" && value === "ENDSEC") { section = null; target = null; current = null; continue; }
      if (section !== "ENTITIES" && section !== "BLOCKS") continue;

      if (code === "0") {
        if (section === "BLOCKS") {
          if (value === "BLOCK") { blockName = null; blockBase = [0, 0]; target = []; current = { type: "BLOCK" }; continue; }
          if (value === "ENDBLK") {
            if (blockName) blocks[blockName] = { entities: target || [], base: blockBase };
            blockName = null; target = null; current = null; continue;
          }
        }
        // POLYLINE carries its geometry in the VERTEX entities that follow it,
        // terminated by SEQEND -- the POLYLINE record itself holds only a dummy
        // 10/20/30 origin. Reading 10/20 off the POLYLINE gave a single point,
        // which was then dropped with no message at all. Fold the vertices in.
        if (value === "VERTEX" && current && current.type === "POLYLINE") {
          current.__vertexOpen = true;
          current.__vtx = current.__vtx || [];
          current.__vtx.push({});
          continue;
        }
        if (value === "SEQEND") { if (current) current.__vertexOpen = false; current = null; continue; }
        seenTypes[value] = (seenTypes[value] || 0) + 1;
        current = { type: value };
        if (target) target.push(current);
        continue;
      }
      if (!current) continue;
      if (current.__vertexOpen && current.__vtx && current.__vtx.length) {
        const v = current.__vtx[current.__vtx.length - 1];
        (v[code] = v[code] || []).push(value);
        continue;
      }
      (current[code] = current[code] || []).push(value);
      // An LWPOLYLINE's bulge (42) only appears on the vertices that have one,
      // so it cannot be paired with the 10/20 list positionally. Keep the groups
      // in file order too, and read the vertices off that.
      if (current.type === "LWPOLYLINE") (current.__seq = current.__seq || []).push([code, value]);
      if (section === "BLOCKS" && current.type === "BLOCK") {
        if (code === "2") blockName = value;
        if (code === "10") blockBase[0] = +value;
        if (code === "20") blockBase[1] = +value;
      }
    }
    entities.__blocks = blocks;
    entities.__types = seenTypes;
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

  function finitePts(pts) {
    // A malformed coordinate must not reach the extents calculation: one NaN
    // there poisons the circumference, the grid and every downstream number.
    for (const p of pts) if (!Number.isFinite(p[0]) || !Number.isFinite(p[1])) return false;
    return true;
  }

  // A polyline vertex carries a "bulge" (group 42): tan(quarter of the included
  // arc angle) of the arc that replaces the straight run to the next vertex.
  // Chording it away silently rounds every filleted block corner into a cut
  // corner, which changes the plan area the whole tool is built on.
  function bulgeArc(p0, p1, bulge, sagitta) {
    if (!bulge || Math.abs(bulge) < 1e-12) return [];
    const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
    const chord = Math.hypot(dx, dy);
    if (chord < 1e-12) return [];
    const theta = 4 * Math.atan(bulge);                 // included angle, signed
    const r = Math.abs(chord / (2 * Math.sin(theta / 2)));
    if (!Number.isFinite(r) || r <= 0) return [];
    const mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2;
    const h = Math.sqrt(Math.max(0, r * r - (chord / 2) * (chord / 2)));
    const sgn = (theta > 0 ? 1 : -1) * (Math.abs(theta) > Math.PI ? -1 : 1);
    const cx = mx - (sgn * h * dy) / chord, cy = my + (sgn * h * dx) / chord;
    let a0 = Math.atan2(p0[1] - cy, p0[0] - cx), a1 = Math.atan2(p1[1] - cy, p1[0] - cx);
    let sweep = a1 - a0;
    while (sweep <= -Math.PI * 2) sweep += Math.PI * 2;
    while (sweep > Math.PI * 2) sweep -= Math.PI * 2;
    if (theta > 0 && sweep < 0) sweep += Math.PI * 2;
    if (theta < 0 && sweep > 0) sweep -= Math.PI * 2;
    const step = r > sagitta ? 2 * Math.acos(Math.max(-1, Math.min(1, 1 - sagitta / r))) : Math.abs(sweep);
    const n = Math.max(2, Math.ceil(Math.abs(sweep) / Math.max(step, 1e-6)));
    const out = [];
    for (let i = 1; i < n; i++) {
      const a = a0 + (sweep * i) / n;
      out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
    return out;
  }

  // Flatten a SPLINE. Fit points are already on the curve, so joining them is
  // exact enough at tread scale; otherwise fall back to the control polygon,
  // which is a coarse but honest outline of where the curve runs.
  function splinePoints(e) {
    const fx = (e["11"] || []).map(Number), fy = (e["21"] || []).map(Number);
    if (fx.length >= 2) {
      const p = [];
      for (let i = 0; i < Math.min(fx.length, fy.length); i++) p.push([fx[i], fy[i]]);
      return p;
    }
    const cx = (e["10"] || []).map(Number), cy = (e["20"] || []).map(Number);
    const p = [];
    for (let i = 0; i < Math.min(cx.length, cy.length); i++) p.push([cx[i], cy[i]]);
    if (p.length >= 2 && (parseInt((e["70"] || ["0"])[0], 10) & 1) === 1) p.push(p[0]);
    return p;
  }

  function ellipsePoints(e, sagitta) {
    const cx = +(e["10"] || [0])[0], cy = +(e["20"] || [0])[0];
    const mx = +(e["11"] || [0])[0], my = +(e["21"] || [0])[0];
    const ratio = +(e["40"] || [1])[0];
    const t0 = +(e["41"] || [0])[0], t1 = +(e["42"] || [2 * Math.PI])[0];
    const a = Math.hypot(mx, my);
    if (!(a > 0)) return [];
    const b = a * ratio, rot = Math.atan2(my, mx);
    let sweep = t1 - t0;
    if (sweep <= 0) sweep += 2 * Math.PI;
    const rmax = Math.max(a, b);
    const step = rmax > sagitta ? 2 * Math.acos(Math.max(-1, Math.min(1, 1 - sagitta / rmax))) : sweep;
    const n = Math.max(3, Math.ceil(sweep / Math.max(step, 1e-6)));
    const out = [];
    for (let i = 0; i <= n; i++) {
      const t = t0 + (sweep * i) / n;
      const ex = a * Math.cos(t), ey = b * Math.sin(t);
      out.push([cx + ex * Math.cos(rot) - ey * Math.sin(rot), cy + ex * Math.sin(rot) + ey * Math.cos(rot)]);
    }
    return out;
  }

  // Entity types that legitimately carry no tread geometry. Anything outside
  // both this list and the handled list is reported, because a drawing whose
  // outlines are SPLINEs used to lose them without a word.
  const DXF_IGNORED = { TEXT: 1, MTEXT: 1, DIMENSION: 1, LEADER: 1, MLEADER: 1, POINT: 1,
    ATTDEF: 1, ATTRIB: 1, HATCH: 1, VIEWPORT: 1, IMAGE: 1, WIPEOUT: 1, TOLERANCE: 1, BLOCK: 1 };

  function entitiesToSegments(entities, sagitta, stats, blocks, depth) {
    const segments = [];
    stats = stats || {};
    if (stats.skipped == null) stats.skipped = 0;
    stats.unsupported = stats.unsupported || {};
    stats.inserts = stats.inserts || 0;
    blocks = blocks || entities.__blocks || {};
    depth = depth || 0;
    const push = (pts) => { if (finitePts(pts)) segments.push(pts); else stats.skipped++; };
    for (const e of entities) {
      try {
        if (e.type === "LINE") {
          const p = [[+e["10"][0], +e["20"][0]], [+e["11"][0], +e["21"][0]]];
          if (!finitePts(p)) { stats.skipped++; continue; }
          if (Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]) < 1e-9) continue;
          segments.push(p);
        } else if (e.type === "ARC") {
          const pts = flattenArc(+e["10"][0], +e["20"][0], +e["40"][0], +e["50"][0], +e["51"][0], sagitta);
          if (pts.length >= 2) push(pts);
        } else if (e.type === "CIRCLE") {
          const pts = flattenArc(+e["10"][0], +e["20"][0], +e["40"][0], 0, 360, sagitta);
          if (pts.length >= 3) push(pts);
        } else if (e.type === "ELLIPSE") {
          const pts = ellipsePoints(e, sagitta);
          if (pts.length >= 3) push(pts);
        } else if (e.type === "SPLINE") {
          const pts = splinePoints(e);
          if (pts.length >= 2) push(pts);
        } else if (e.type === "LWPOLYLINE" || e.type === "POLYLINE") {
          let xs, ys, bulges;
          if (e.__vtx && e.__vtx.length) {
            xs = e.__vtx.map((v) => +(v["10"] || [NaN])[0]);
            ys = e.__vtx.map((v) => +(v["20"] || [NaN])[0]);
            bulges = e.__vtx.map((v) => +(v["42"] || [0])[0]);
          } else if (e.__seq) {
            xs = []; ys = []; bulges = [];
            for (const [code, value] of e.__seq) {
              if (code === "10") { xs.push(+value); ys.push(NaN); bulges.push(0); }
              else if (code === "20" && xs.length) ys[ys.length - 1] = +value;
              else if (code === "42" && xs.length) bulges[bulges.length - 1] = +value;
            }
          } else {
            xs = (e["10"] || []).map(Number);
            ys = (e["20"] || []).map(Number);
            bulges = xs.map(() => 0);
          }
          const verts = [];
          for (let i = 0; i < Math.min(xs.length, ys.length); i++) verts.push([xs[i], ys[i]]);
          if (verts.length >= 2) {
            const closed = (parseInt((e["70"] || ["0"])[0], 10) & 1) === 1;
            const pts = [];
            const last = closed ? verts.length : verts.length - 1;
            for (let i = 0; i < last; i++) {
              const p0 = verts[i], p1 = verts[(i + 1) % verts.length];
              pts.push(p0);
              for (const q of bulgeArc(p0, p1, bulges[i] || 0, sagitta)) pts.push(q);
            }
            pts.push(closed ? verts[0] : verts[verts.length - 1]);
            push(pts);
          }
        } else if (e.type === "INSERT") {
          // A pattern drawn once and arrayed is still the pattern. Expand it,
          // honouring the block base point, scale, rotation and MINSERT array.
          const name = (e["2"] || [""])[0];
          const def = blocks[name];
          if (!def || depth > 6) { stats.unsupported["INSERT(" + name + ")"] = (stats.unsupported["INSERT(" + name + ")"] || 0) + 1; continue; }
          const ix = +(e["10"] || [0])[0], iy = +(e["20"] || [0])[0];
          const sx = +(e["41"] || [1])[0] || 1, sy = +(e["42"] || [1])[0] || 1;
          const rot = ((+(e["50"] || [0])[0]) * Math.PI) / 180;
          const nCols = Math.max(1, parseInt((e["70"] || ["1"])[0], 10) || 1);
          const nRows = Math.max(1, parseInt((e["71"] || ["1"])[0], 10) || 1);
          const dCol = +(e["44"] || [0])[0], dRow = +(e["45"] || [0])[0];
          const inner = entitiesToSegments(def.entities, sagitta, stats, blocks, depth + 1);
          const ca = Math.cos(rot), sa = Math.sin(rot);
          for (let r = 0; r < nRows; r++) {
            for (let c = 0; c < nCols; c++) {
              const ox = ix + c * dCol, oy = iy + r * dRow;
              for (const chain of inner) {
                push(chain.map((p) => {
                  const px = (p[0] - def.base[0]) * sx, py = (p[1] - def.base[1]) * sy;
                  return [ox + px * ca - py * sa, oy + px * sa + py * ca];
                }));
              }
            }
          }
          stats.inserts++;
        } else if (!DXF_IGNORED[e.type]) {
          stats.unsupported[e.type] = (stats.unsupported[e.type] || 0) + 1;
        }
      } catch (err) { stats.skipped++; }
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

  // =====================================================================
  // 3b. planar arrangement -- deterministic loops, and the regions between them
  // =====================================================================
  // buildLoops above walks a chain and, at a junction, takes whichever attached
  // segment happens to be unused first. That is a coin toss decided by the order
  // the entities sit in the DXF: shuffling two abutting rectangles returned two
  // 200 mm^2 blocks or one merged 400 mm^2 blob depending on the shuffle. It is
  // also blind to a tie bar, whose long sides ARE the neighbouring blocks' groove
  // walls -- there is no separate closed outline to find.
  //
  // Faces of the planar arrangement have neither problem. Weld the endpoints,
  // cut every segment at the points where other segments touch or cross it, then
  // at each node always leave along the first edge clockwise from the one you
  // arrived on. That traces the smallest region enclosing you and nothing else,
  // so the answer depends only on the geometry. Blocks come out as faces; a tie
  // bar comes out as its own face, bounded partly by edges it shares with the
  // blocks on either side.

  function SpatialHash(tol) {
    this.tol = tol;
    this.cell = Math.max(tol * 2, 1e-9);
    this.map = new Map();
    this.pts = [];
  }
  SpatialHash.prototype._key = function (i, j) { return i * 73856093 ^ j * 19349663; };
  // Returns an existing node id within tol, or creates one.
  SpatialHash.prototype.node = function (x, y) {
    const i = Math.floor(x / this.cell), j = Math.floor(y / this.cell);
    let best = -1, bd = this.tol;
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const b = this.map.get(this._key(i + di, j + dj));
        if (!b) continue;
        for (let k = 0; k < b.length; k++) {
          const p = this.pts[b[k]];
          const d = Math.hypot(p[0] - x, p[1] - y);
          if (d <= bd) { bd = d; best = b[k]; }
        }
      }
    }
    if (best >= 0) return best;
    const id = this.pts.length;
    this.pts.push([x, y]);
    const k = this._key(i, j);
    let b = this.map.get(k);
    if (!b) { b = []; this.map.set(k, b); }
    b.push(id);
    return id;
  };

  function segIntersect(a0, a1, b0, b1) {
    const rx = a1[0] - a0[0], ry = a1[1] - a0[1];
    const sx = b1[0] - b0[0], sy = b1[1] - b0[1];
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-15) return null;            // parallel or collinear
    const qpx = b0[0] - a0[0], qpy = b0[1] - a0[1];
    const t = (qpx * sy - qpy * sx) / den;
    const u = (qpx * ry - qpy * rx) / den;
    if (t <= 1e-9 || t >= 1 - 1e-9 || u <= 1e-9 || u >= 1 - 1e-9) return null;
    return [t, u];
  }

  // Distance from p to segment a-b, plus the parameter of the foot.
  function pointOnSeg(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const L2 = dx * dx + dy * dy;
    if (L2 < 1e-18) return null;
    const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2;
    if (t <= 1e-9 || t >= 1 - 1e-9) return null;
    const fx = a[0] + t * dx, fy = a[1] + t * dy;
    return [t, Math.hypot(p[0] - fx, p[1] - fy)];
  }

  // Uniform bucket grid over the elementary segments, so the pairwise work below
  // stays near-linear instead of quadratic on a few tens of thousands of edges.
  function segmentBuckets(segs, pts) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, total = 0;
    for (const s of segs) {
      const a = pts[s[0]], b = pts[s[1]];
      x0 = Math.min(x0, a[0], b[0]); x1 = Math.max(x1, a[0], b[0]);
      y0 = Math.min(y0, a[1], b[1]); y1 = Math.max(y1, a[1], b[1]);
      total += Math.hypot(b[0] - a[0], b[1] - a[1]);
    }
    const mean = segs.length ? total / segs.length : 1;
    const cell = Math.max(mean * 2, 1e-6);
    const map = new Map();
    const key = (i, j) => i * 73856093 ^ j * 19349663;
    for (let k = 0; k < segs.length; k++) {
      const a = pts[segs[k][0]], b = pts[segs[k][1]];
      const i0 = Math.floor(Math.min(a[0], b[0]) / cell), i1 = Math.floor(Math.max(a[0], b[0]) / cell);
      const j0 = Math.floor(Math.min(a[1], b[1]) / cell), j1 = Math.floor(Math.max(a[1], b[1]) / cell);
      for (let i = i0; i <= i1; i++) {
        for (let j = j0; j <= j1; j++) {
          const kk = key(i, j);
          let bkt = map.get(kk);
          if (!bkt) { bkt = []; map.set(kk, bkt); }
          bkt.push(k);
        }
      }
    }
    return { map: map, cell: cell, key: key, extent: [x0, x1, y0, y1] };
  }

  const ARRANGEMENT_LIMIT = 200000;

  // chains -> {faces, openChains, nodes, stats}
  function planarArrangement(chains, tol) {
    tol = tol || 1e-2;
    const hash = new SpatialHash(tol);
    let segs = [];
    for (const chain of chains) {
      for (let i = 0; i + 1 < chain.length; i++) {
        const a = hash.node(chain[i][0], chain[i][1]);
        const b = hash.node(chain[i + 1][0], chain[i + 1][1]);
        if (a !== b) segs.push([a, b]);
      }
    }
    const stats = { n_input_segments: segs.length, n_split: 0, truncated: false };
    if (!segs.length) return { faces: [], openChains: [], stats: stats, nodes: hash.pts };
    if (segs.length > ARRANGEMENT_LIMIT) {
      // Refusing beats grinding for minutes in a worker the user cannot see into.
      throw new Error(
        "this drawing flattens to " + segs.length + " segments, past the " + ARRANGEMENT_LIMIT +
        " the arrangement can handle. Import a single tread pitch rather than the whole " +
        "wheel, or raise the arc tolerance."
      );
    }

    // ---- cut every segment where another one touches or crosses it ----------
    const buckets = segmentBuckets(segs, hash.pts);
    const cuts = segs.map(() => null);
    const addCut = (k, t) => { if (!cuts[k]) cuts[k] = []; cuts[k].push(t); };
    const near = (k) => {
      const a = hash.pts[segs[k][0]], b = hash.pts[segs[k][1]];
      const c = buckets.cell;
      const i0 = Math.floor(Math.min(a[0], b[0]) / c), i1 = Math.floor(Math.max(a[0], b[0]) / c);
      const j0 = Math.floor(Math.min(a[1], b[1]) / c), j1 = Math.floor(Math.max(a[1], b[1]) / c);
      const out = new Set();
      for (let i = i0 - 1; i <= i1 + 1; i++)
        for (let j = j0 - 1; j <= j1 + 1; j++) {
          const bk = buckets.map.get(buckets.key(i, j));
          if (bk) for (const m of bk) if (m !== k) out.add(m);
        }
      return out;
    };
    for (let k = 0; k < segs.length; k++) {
      const a = hash.pts[segs[k][0]], b = hash.pts[segs[k][1]];
      for (const m of near(k)) {
        if (m < k) continue;
        const c = hash.pts[segs[m][0]], d = hash.pts[segs[m][1]];
        const x = segIntersect(a, b, c, d);
        if (x) { addCut(k, x[0]); addCut(m, x[1]); continue; }
        // A tie bar drawn to land on the middle of a groove wall makes a T, not
        // a crossing: the wall has to be cut at the endpoint that touches it.
        for (const [pt, endNode] of [[c, segs[m][0]], [d, segs[m][1]]]) {
          if (endNode === segs[k][0] || endNode === segs[k][1]) continue;
          const r = pointOnSeg(pt, a, b);
          if (r && r[1] <= tol) addCut(k, r[0]);
        }
      }
    }

    const split = [];
    for (let k = 0; k < segs.length; k++) {
      const a = hash.pts[segs[k][0]], b = hash.pts[segs[k][1]];
      if (!cuts[k]) { split.push(segs[k]); continue; }
      const ts = cuts[k].slice().sort((p, q) => p - q);
      let prev = segs[k][0];
      let lastT = 0;
      for (const t of ts) {
        if (t - lastT < 1e-9) continue;
        const n = hash.node(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t);
        if (n !== prev) { split.push([prev, n]); prev = n; lastT = t; stats.n_split++; }
      }
      if (prev !== segs[k][1]) split.push([prev, segs[k][1]]);
    }
    segs = split;

    // ---- de-duplicate: coincident outlines are drawn twice all the time -----
    const seen = new Set();
    const edges = [];
    for (const s of segs) {
      if (s[0] === s[1]) continue;
      const k = s[0] < s[1] ? s[0] + ":" + s[1] : s[1] + ":" + s[0];
      if (seen.has(k)) continue;
      seen.add(k);
      edges.push(s);
    }

    // ---- prune the tree part: a dangling edge bounds no face ----------------
    const deg = new Map();
    const bump = (n, d) => deg.set(n, (deg.get(n) || 0) + d);
    for (const e of edges) { bump(e[0], 1); bump(e[1], 1); }
    const dead = new Array(edges.length).fill(false);
    const incident = new Map();
    for (let i = 0; i < edges.length; i++) {
      for (const n of edges[i]) {
        let l = incident.get(n);
        if (!l) { l = []; incident.set(n, l); }
        l.push(i);
      }
    }
    let queue = [];
    deg.forEach((d, n) => { if (d === 1) queue.push(n); });
    while (queue.length) {
      const n = queue.pop();
      if ((deg.get(n) || 0) !== 1) continue;
      const live = (incident.get(n) || []).filter((i) => !dead[i]);
      if (!live.length) { deg.set(n, 0); continue; }
      const i = live[0];
      dead[i] = true;
      const other = edges[i][0] === n ? edges[i][1] : edges[i][0];
      deg.set(n, 0);
      bump(other, -1);
      if ((deg.get(other) || 0) === 1) queue.push(other);
    }

    // Rebuild the pruned edges into chains -- they are the open outlines, and
    // the seam-wrapping blocks live among them.
    const openChains = [];
    {
      const usedD = new Array(edges.length).fill(false);
      const adjD = new Map();
      for (let i = 0; i < edges.length; i++) {
        if (!dead[i]) continue;
        for (const n of edges[i]) {
          let l = adjD.get(n);
          if (!l) { l = []; adjD.set(n, l); }
          l.push(i);
        }
      }
      for (let i = 0; i < edges.length; i++) {
        if (!dead[i] || usedD[i]) continue;
        usedD[i] = true;
        let chain = [edges[i][0], edges[i][1]];
        for (const endIdx of [1, 0]) {
          while (true) {
            const tip = endIdx ? chain[chain.length - 1] : chain[0];
            const nxt = (adjD.get(tip) || []).find((j) => !usedD[j]);
            if (nxt == null) break;
            usedD[nxt] = true;
            const other = edges[nxt][0] === tip ? edges[nxt][1] : edges[nxt][0];
            if (endIdx) chain.push(other); else chain.unshift(other);
          }
        }
        openChains.push(chain.map((n) => hash.pts[n].slice()));
      }
    }

    // ---- half-edge face traversal ------------------------------------------
    const live = [];
    for (let i = 0; i < edges.length; i++) if (!dead[i]) live.push(edges[i]);
    const out = new Map();          // node -> [{to, half}] sorted by angle
    const halfNext = new Int32Array(live.length * 2).fill(-1);
    for (let i = 0; i < live.length; i++) {
      for (const dir of [0, 1]) {
        const from = live[i][dir], to = live[i][1 - dir];
        const p = hash.pts[from], q = hash.pts[to];
        let l = out.get(from);
        if (!l) { l = []; out.set(from, l); }
        l.push({ to: to, half: i * 2 + dir, ang: Math.atan2(q[1] - p[1], q[0] - p[0]) });
      }
    }
    out.forEach((l) => l.sort((a, b) => a.ang - b.ang));
    // For the half-edge u->v, the next one is the first edge clockwise from v->u.
    for (let i = 0; i < live.length; i++) {
      for (const dir of [0, 1]) {
        const u = live[i][dir], v = live[i][1 - dir];
        const l = out.get(v);
        const p = hash.pts[v], w = hash.pts[u];
        const back = Math.atan2(w[1] - p[1], w[0] - p[0]);
        let idx = -1;
        for (let k = 0; k < l.length; k++) if (Math.abs(l[k].ang - back) < 1e-12 && l[k].to === u) { idx = k; break; }
        if (idx < 0) { // fall back to nearest angle
          let bd = Infinity;
          for (let k = 0; k < l.length; k++) { const d = Math.abs(l[k].ang - back); if (d < bd) { bd = d; idx = k; } }
        }
        const prev = l[(idx - 1 + l.length) % l.length];
        halfNext[i * 2 + dir] = prev.half;
      }
    }

    const visited = new Uint8Array(live.length * 2);
    const faces = [];
    for (let h0 = 0; h0 < live.length * 2; h0++) {
      if (visited[h0]) continue;
      const ring = [];
      let h = h0, guard = 0;
      while (!visited[h] && guard++ < live.length * 2 + 4) {
        visited[h] = 1;
        const dir = h & 1, i = h >> 1;
        ring.push(live[i][dir]);
        h = halfNext[h];
      }
      if (ring.length < 3) continue;
      const poly = ring.map((n) => hash.pts[n].slice());
      let s = 0;
      for (let k = 0; k < poly.length; k++) {
        const a = poly[k], b = poly[(k + 1) % poly.length];
        s += a[0] * b[1] - b[0] * a[1];
      }
      // A CCW ring is an enclosed region; the CW ones are the outer boundaries
      // of each connected component and enclose nothing.
      if (s > 0) faces.push({ polygon: poly, area: s / 2, halfEdges: ring.slice() });
    }
    stats.n_edges = live.length;
    stats.n_faces = faces.length;
    stats.n_open_chains = openChains.length;
    return { faces: faces, openChains: openChains, stats: stats, nodes: hash.pts, edges: live };
  }

  // Which faces touch which, along a shared edge. A block outline drawn on its
  // own shares nothing; a tie bar necessarily shares its two long sides with the
  // blocks it bridges, and that is what tells them apart.
  function faceAdjacency(faces) {
    const owner = new Map();
    const shared = faces.map(() => new Set());
    const sharedLen = faces.map(() => 0);
    for (let f = 0; f < faces.length; f++) {
      const r = faces[f].polygon;
      for (let i = 0; i < r.length; i++) {
        const a = r[i], b = r[(i + 1) % r.length];
        const ka = a[0].toFixed(6) + "," + a[1].toFixed(6);
        const kb = b[0].toFixed(6) + "," + b[1].toFixed(6);
        const k = ka < kb ? ka + "|" + kb : kb + "|" + ka;
        const prev = owner.get(k);
        if (prev == null) { owner.set(k, f); continue; }
        if (prev === f) continue;
        shared[f].add(prev);
        shared[prev].add(f);
        const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
        sharedLen[f] += L;
        sharedLen[prev] += L;
      }
    }
    return { neighbours: shared, sharedLength: sharedLen };
  }

  function median(xs) {
    if (!xs.length) return 0;
    const s = xs.slice().sort((a, b) => a - b);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  // Split the arrangement's faces into blocks and tie-bar candidates.
  //   * a face that shares no edge with any other is a block, always -- that is
  //     every face on a drawing of separate outlines, so nothing changes there;
  //   * among faces that do share edges, one that is small relative to the
  //     typical block AND bridges two or more of them is a tie bar;
  //   * anything else that shares edges is reported as an unclassified region
  //     and treated as a block, never silently dropped.
  // The threshold is a first guess; every candidate is listed for the user to
  // confirm, reject or re-height.
  function classifyFaces(faces, areaFrac) {
    areaFrac = areaFrac == null ? 0.5 : areaFrac;
    const adj = faceAdjacency(faces);
    const isolated = [];
    for (let f = 0; f < faces.length; f++) if (!adj.neighbours[f].size) isolated.push(faces[f].area);
    const ref = median(isolated.length ? isolated : faces.map((f) => f.area));
    const cut = ref * areaFrac;
    const blocks = [], tiebars = [], unclassified = [];
    for (let f = 0; f < faces.length; f++) {
      const nb = adj.neighbours[f];
      const rec = Object.assign({}, faces[f], { neighbours: Array.from(nb) });
      if (!nb.size) { blocks.push(rec); continue; }
      if (faces[f].area < cut && nb.size >= 2) { tiebars.push(rec); continue; }
      if (faces[f].area < cut) { unclassified.push(rec); blocks.push(rec); continue; }
      blocks.push(rec);
    }
    return { blocks: blocks, tiebars: tiebars, unclassified: unclassified, referenceArea: ref, cutArea: cut };
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

  function pointInPolygon(pt, poly) {
    let inside = false;
    for (let i = 0, n = poly.length; i < n; i++) {
      const a = poly[i], b = poly[(i + 1) % n];
      if (a[1] === b[1]) continue;
      if ((a[1] > pt[1]) !== (b[1] > pt[1])) {
        const x = ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0];
        if (pt[0] < x) inside = !inside;
      }
    }
    return inside;
  }

  function bbox(poly) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const p of poly) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    return [x0, x1, y0, y1];
  }

  // How many block outlines sit wholly inside another one. Bounding boxes are
  // checked first so this stays cheap on a few hundred blocks.
  function countNestedLoops(blocks) {
    const boxes = blocks.map((b) => bbox(b.polygon));
    const cents = blocks.map((b) => polygonCentroid(b.polygon));
    let n = 0;
    for (let i = 0; i < blocks.length; i++) {
      for (let j = 0; j < blocks.length; j++) {
        if (i === j) continue;
        const bi = boxes[i], bj = boxes[j];
        if (bi[0] < bj[0] || bi[1] > bj[1] || bi[2] < bj[2] || bi[3] > bj[3]) continue;
        if (bi[1] - bi[0] >= bj[1] - bj[0] && bi[3] - bi[2] >= bj[3] - bj[2]) continue;
        if (pointInPolygon(cents[i], blocks[j].polygon)) { n++; break; }
      }
    }
    return n;
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

  // A 2D tread plan carries no depth, so these come from the caller. Bad values
  // here do not crash -- they quietly zero every stiffness, which is worse.
  function validateBlockDefaults(d) {
    if (!d) return;
    if (d.height != null) {
      if (!Number.isFinite(d.height) || d.height <= 0)
        throw new Error("NSD (block height) must be a positive number of mm, got " + d.height);
    }
    if (d.draft_angle != null) {
      if (!Number.isFinite(d.draft_angle) || Math.abs(d.draft_angle) >= 90)
        throw new Error("draft angle must be a finite angle strictly between -90 and 90 degrees, got " + d.draft_angle);
    }
    if (d.n_lateral_sipes != null) {
      if (!Number.isFinite(d.n_lateral_sipes) || d.n_lateral_sipes < 0 || d.n_lateral_sipes % 1 !== 0)
        throw new Error("lateral sipe count must be a non-negative whole number, got " + d.n_lateral_sipes);
      if (d.n_lateral_sipes > 50)
        throw new Error("lateral sipe count of " + d.n_lateral_sipes + " is implausible for a tread block (limit 50)");
    }
    if (d.sipe_depth_fraction != null) {
      if (!Number.isFinite(d.sipe_depth_fraction) || d.sipe_depth_fraction < 0 || d.sipe_depth_fraction > 1)
        throw new Error("sipe depth fraction must lie between 0 and 1 (fraction of NSD), got " + d.sipe_depth_fraction);
    }
  }

  function validateImportOptions(o) {
    if (!o) return;
    for (const k of ["circumference", "tread_width"]) {
      if (o[k] != null && (!Number.isFinite(o[k]) || o[k] <= 0))
        throw new Error(k.replace("_", " ") + " override must be a positive number of mm, got " + o[k]);
    }
    if (o.n_pitches != null && o.n_pitches !== 0) {
      if (!Number.isFinite(o.n_pitches) || o.n_pitches < 1 || o.n_pitches % 1 !== 0)
        throw new Error("pitch count must be a whole number >= 1, got " + o.n_pitches);
      if (o.n_pitches > 5000)
        throw new Error("pitch count of " + o.n_pitches + " is implausible (limit 5000)");
    }
    if (o.min_block_area != null && (!Number.isFinite(o.min_block_area) || o.min_block_area < 0))
      throw new Error("minimum block area must be a non-negative number of mm^2, got " + o.min_block_area);
  }

  // params: {height, draft_angle, n_lateral_sipes, sipe_depth_fraction, shore_a}
  function loadPattern(text, defaults, opts) {
    defaults = Object.assign({ height: 8.0, draft_angle: 3.0, n_lateral_sipes: 0, sipe_depth_fraction: 0.6, shore_a: null,
      height_by_zone: {}, draft_by_zone: {}, sipes_by_zone: {} }, defaults || {});
    opts = opts || {};
    validateBlockDefaults(defaults);
    validateImportOptions(opts);
    const entities = readDxfEntities(text);
    const stats = {};
    let segments = entitiesToSegments(entities, 0.02, stats);
    const warnings = [];
    if (!segments.length)
      throw new Error(
        "no usable LINE/ARC/POLYLINE entities found in this DXF. Check that the file " +
        "contains the tread outline as geometry (not blocks/xrefs), and that it was " +
        "exported as ASCII DXF rather than binary."
      );
    if (stats.skipped)
      warnings.push(stats.skipped + " entit(ies) had non-finite coordinates and were skipped");

    let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
    for (const s of segments) for (const p of s) { xMin = Math.min(xMin, p[0]); xMax = Math.max(xMax, p[0]); yMin = Math.min(yMin, p[1]); yMax = Math.max(yMax, p[1]); }
    const circ = opts.circumference || xMax - xMin;
    const width = opts.tread_width || yMax - yMin;
    if (!Number.isFinite(circ) || circ <= 0 || !Number.isFinite(width) || width <= 0)
      throw new Error(
        "DXF extents are degenerate: " + circ + " x " + width + " mm. The drawing must " +
        "span a positive distance in both directions; a single straight line or a file " +
        "with corrupt coordinates will produce this."
      );

    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    // A centreline or border is a lone straight segment running the whole
    // drawing. Only look for one when the drawing is big enough to have block
    // detail as well -- on a 4-segment drawing every edge spans the extents,
    // and the old 0.5x rule deleted the only block in the file.
    const canHaveConstructionLines = segments.length >= 8;
    const kept = [];
    for (const s of segments) {
      if (canHaveConstructionLines && s.length === 2 && dist(s[0], s[1]) > 0.9 * circ) continue;
      kept.push(s.map((p) => [p[0] - xMin, p[1] - yMin]));
    }
    if (kept.length !== segments.length) warnings.push("ignored " + (segments.length - kept.length) + " full-span construction line(s)");
    segments = kept;

    // Weld tolerance. CAD exports routinely leave corners a few microns apart
    // after a trim; at the old 1e-3 mm a 5 um gap lost the whole block with only
    // a "chain did not close" line to show for it. 0.01 mm is two orders below
    // the finest tread feature (a 0.5 mm sipe) and two above the usual export
    // round-off, so it closes the gaps without merging anything real.
    const weldTol = opts.weld_tolerance == null ? 0.01 : opts.weld_tolerance;
    if (!Number.isFinite(weldTol) || weldTol <= 0 || weldTol > 1.0)
      throw new Error("weld tolerance must be a positive number of mm no greater than 1.0, got " + weldTol);

    const arr = planarArrangement(segments, weldTol);
    const seam = closeAcrossSeam(arr.openChains, circ);
    if (seam.leftover.length) warnings.push(seam.leftover.length + " chain(s) did not close and were discarded");

    const minBlockArea = opts.min_block_area == null ? 5.0 : opts.min_block_area;
    const cls = classifyFaces(arr.faces, opts.tiebar_area_fraction);

    // Seam-wrapped outlines never make it into the arrangement (they are open
    // until the two halves are stitched), so they join the blocks directly.
    let loops = cls.blocks.map((f) => f.polygon).concat(seam.loops);
    const small = loops.filter((c) => polygonArea(c) < minBlockArea);
    loops = loops.filter((c) => polygonArea(c) >= minBlockArea);

    let tiebarFaces = cls.tiebars.filter((f) => f.area >= Math.min(minBlockArea, 1.0));
    // A region enclosed by a block is an interior subdivision -- a sipe drawn
    // right across, a stone ejector -- not a bar bridging two blocks.
    if (tiebarFaces.length) {
      const blockPolys = loops;
      tiebarFaces = tiebarFaces.filter((f) => {
        const c = polygonCentroid(f.polygon);
        for (const bp of blockPolys) if (pointInPolygon(c, bp)) return false;
        return true;
      });
    }
    if (cls.unclassified.length)
      warnings.push(cls.unclassified.length + " small region(s) share an edge with a block but touch only one of " +
        "them, so they were counted as blocks rather than tie bars — check 6 · Wear & tie bars if any of them is a bar");

    // An import that yields no blocks is a failed import, not an empty tyre.
    // Left to run it produces all-zero charts with nothing to explain them.
    if (!loops.length) {
      const why = [];
      if (small.length) why.push(small.length + " closed loop(s) were smaller than the " + minBlockArea + " mm^2 minimum block area");
      if (seam.leftover.length) why.push(seam.leftover.length + " outline(s) did not close — the drawing may have gaps at block corners");
      if (!arr.faces.length && !seam.loops.length) why.push("no closed outline could be stitched from the segments at all");
      throw new Error(
        "DXF imported 0 tread blocks. " + (why.length ? why.join("; ") + ". " : "") +
        "Check that the tread outlines are on the imported layer, that corners meet " +
        "exactly, and that the drawing units are millimetres."
      );
    }

    const half = width / 2;
    loops.sort((a, b) => Math.min.apply(null, a.map((p) => p[0])) - Math.min.apply(null, b.map((p) => p[0])));
    const blocks = [];
    for (let i = 0; i < loops.length; i++) {
      const poly = loops[i].map((p) => [p[0], p[1] - half]);
      let cy = 0; for (const p of poly) cy += p[1]; cy /= poly.length;
      const zone = classifyZone(Math.abs(cy), width, opts.zone_center, opts.zone_intermediate);
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

    // A loop lying inside another is usually a hole (a dimple, a stone ejector,
    // an inner detail line), not a second block. The sweep is unaffected -- the
    // rasteriser writes land pixels idempotently, so a union is what it measures
    // -- but the land ratio below sums polygon areas and would double-count it.
    const nested = countNestedLoops(blocks);
    if (nested)
      warnings.push(nested + " outline(s) lie inside another outline and are being counted as separate " +
        "blocks; if they are holes rather than blocks the land ratio below is an over-estimate " +
        "(the theta sweep itself measures the union and is unaffected)");

    let land = 0; for (const b of blocks) land += polygonArea(b.polygon);
    const uniform = repeat ? Math.abs(circ / repeat - Math.round(circ / repeat)) < 1e-3 && Math.round(circ / repeat) >= 2 : false;
    if (uniform) warnings.push("every repeat is geometrically identical -- this drawing carries no pitch modulation; supply the production pitch sequence for a real order analysis");

    // Tie bars. A bar's height is measured from the groove bottom like the
    // block's NSD, but it is lower, so on an unworn tyre its top sits
    // (NSD - height) below the road and it carries nothing. Default it to a
    // fraction of the local block NSD and let the user set each one.
    const tbFrac = opts.tiebar_height_fraction == null ? 0.55 : opts.tiebar_height_fraction;
    const tiebars = [];
    tiebarFaces.sort((a, b) => bbox(a.polygon)[0] - bbox(b.polygon)[0]);
    for (let i = 0; i < tiebarFaces.length; i++) {
      const poly = tiebarFaces[i].polygon.map((p) => [p[0], p[1] - half]);
      const c = polygonCentroid(poly);
      const zone = classifyZone(Math.abs(c[1]), width, opts.zone_center, opts.zone_intermediate);
      const nsd = defaults.height_by_zone[zone] != null ? defaults.height_by_zone[zone] : defaults.height;
      tiebars.push({
        id: "T" + String(i).padStart(3, "0"), polygon: poly, zone: zone,
        nsd: nsd, height: Math.min(nsd, tbFrac * nsd),
        draft_angle: defaults.draft_by_zone[zone] != null ? defaults.draft_by_zone[zone] : defaults.draft_angle,
        shore_a: defaults.shore_a, sipes: [], n_lateral_sipes: 0,
        sipe_depth_fraction: defaults.sipe_depth_fraction, sipe_width: 0.5,
        enabled: true,
        area: polygonArea(poly), centroid_x: c[0], centroid_y: c[1],
        n_neighbours: (tiebarFaces[i].neighbours || []).length,
      });
    }
    // Which blocks each bar is bonded to. Geometry only, so it survives every
    // later change of NSD, height or wear.
    linkTiebars(blocks, tiebars);
    const unlinked = tiebars.filter((t) => !t.links || t.links.length < 2).length;
    if (unlinked)
      warnings.push(unlinked + " tie bar(s) touch fewer than two blocks along a shared edge, so they " +
        "cannot couple anything; they will still add contact area once worn into, but contribute no " +
        "coupling stiffness. This usually means the bar's outline does not meet the groove walls exactly.");

    if (tiebars.length)
      warnings.push(tiebars.length + " tie bar(s) detected between blocks. They sit below the tread " +
        "surface, so they carry nothing until the tyre has worn past (NSD - tie-bar height) — set the " +
        "heights and the wear state in 6 · Wear & tie bars.");

    const pattern = {
      tyre_circumference: circ, tread_width: width, pitches: pitches, blocks: blocks,
      tiebars: tiebars,
      crown: buildCrown(width, opts),
      name: opts.name || "pattern", source: "dxf",
      meta: { geometric_repeat_mm: repeat, uniform_array: uniform },
    };
    const unsupported = Object.keys(stats.unsupported || {}).map((k) => k + " x" + stats.unsupported[k]);
    if (unsupported.length)
      warnings.push("entity type(s) not read as tread geometry: " + unsupported.join(", ") +
        ". If the outlines are drawn with these, the import is incomplete.");
    if (stats.inserts)
      warnings.push(stats.inserts + " INSERT reference(s) were expanded from the BLOCKS section");
    const report = {
      n_entities: entities.length, n_segments: segments.length, n_closed: cls.blocks.length,
      n_wrapped: seam.loops.length, n_discarded_open: seam.leftover.length, n_discarded_small: small.length,
      circumference: circ, tread_width: width, land_ratio: land / (circ * width), n_blocks: blocks.length,
      n_tiebars: tiebars.length, n_unclassified: cls.unclassified.length,
      n_faces: arr.faces.length, n_arrangement_edges: arr.stats.n_edges || 0,
      n_arrangement_splits: arr.stats.n_split || 0, weld_tolerance: weldTol,
      tiebar_reference_area: cls.referenceArea, tiebar_cut_area: cls.cutArea,
      entity_types: entities.__types || {}, unsupported_types: stats.unsupported || {},
      n_inserts: stats.inserts || 0,
      warnings: warnings,
    };
    return { pattern: pattern, report: report };
  }

  // =====================================================================
  // 4. zones + crown profile (port of tread_eval/schema.py)
  // =====================================================================
  // Class-dependent geometry.
  //
  // These are not cosmetic. The crown break decides where the shoulder radius
  // starts eating into the profile, which sets the tangent angle at every
  // lateral position -- and therefore the contact point at each lean and the
  // maximum lean the tyre can reach at all. A truck crown is flat almost to the
  // edge; a motorcycle crown is curved from the centreline out. Running one on
  // the other's profile is a real error, not a rounding one.
  //
  // The zone fractions follow the same logic: a TBR shoulder rib is a much
  // smaller fraction of the half width than a 2W shoulder. Typical values per
  // class; override the crown radii and break directly when you know better.
  // The crown radii here are the fallback when the crown fields are left blank.
  // They used to be a hard-coded 125/55 -- motorcycle numbers -- whatever class
  // was selected, so a blank crown on a 220 mm truck tread was evaluated with
  // 47 mm of edge drop and 63 degrees of reachable lean. A crown is never flat
  // and never class-agnostic; if nothing is typed the class default is the only
  // defensible guess.
  var TYRE_CLASS = {
    "2w":  { crown_break: 0.45, zone_center: 0.34, zone_intermediate: 0.72,
             crown_r_center: 125, crown_r_shoulder: 55,
             load_rises_with_lean: true,
             note: "motorcycle: curved crown, leans far, load grows as Fz/cos(gamma)" },
    "pcr": { crown_break: 0.72, zone_center: 0.40, zone_intermediate: 0.78,
             crown_r_center: 700, crown_r_shoulder: 90,
             load_rises_with_lean: false,
             note: "passenger car: flat crown rolling off in the outer quarter; camber is small, so cornering load comes from weight transfer rather than lean" },
    "tbr": { crown_break: 0.85, zone_center: 0.45, zone_intermediate: 0.82,
             crown_r_center: 1500, crown_r_shoulder: 120,
             load_rises_with_lean: false,
             note: "truck & bus: nearly flat crown with the shoulder only at the very edge; barely cambers" },
  };

  function tyreClass(name) { return TYRE_CLASS[String(name || "2w").toLowerCase()] || TYRE_CLASS["2w"]; }

  function zoneBounds(treadWidth, centerFrac, intermediateFrac) {
    centerFrac = centerFrac || 0.34; intermediateFrac = intermediateFrac || 0.72;
    const half = treadWidth / 2;
    return { center: [0, centerFrac * half], intermediate: [centerFrac * half, intermediateFrac * half], shoulder: [intermediateFrac * half, half] };
  }
  const ZONES = ["center", "intermediate", "shoulder"];
  function classifyZone(yAbs, treadWidth, cFrac, iFrac) {
    const b = zoneBounds(treadWidth, cFrac, iFrac);
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
  // A real tread arc specification: a run of circular arcs, each with its own
  // radius, meeting at stated breakpoints.
  //
  // crownFromRadiusProfile already takes an arbitrary r(y) and integrates it, so
  // the engine has never cared how many arcs produced the profile -- only
  // crownDualRadius, which blends exactly two of them, was the limit. This
  // builds r(y) piecewise-constant instead, which is what a drawing actually
  // specifies. Tangent continuity comes free: phi is the integral of 1/r, so it
  // is continuous however abruptly r changes. Curvature IS discontinuous at each
  // breakpoint, which is correct -- that is what a multi-arc profile is.
  //
  // Breakpoints are developed arc length from the centreline, the same y the
  // rest of the tool uses, not the projected (flat) width.
  const MAX_CROWN_ARCS = 8;

  function validateCrownArcs(arcs, treadWidth) {
    if (!arcs || !arcs.length) throw new Error("a tread arc profile needs at least one radius");
    if (arcs.length > MAX_CROWN_ARCS)
      throw new Error("at most " + MAX_CROWN_ARCS + " tread arcs are supported, got " + arcs.length);
    const half = treadWidth / 2;
    let prev = 0;
    for (let i = 0; i < arcs.length; i++) {
      const a = arcs[i];
      if (!Number.isFinite(a.r) || a.r <= 0)
        throw new Error("tread arc radius " + (i + 1) + " must be a positive number of mm, got " + a.r);
      if (a.r < 10)
        throw new Error("tread arc radius " + (i + 1) + " of " + a.r + " mm is implausibly tight for a tyre crown");
      if (i === arcs.length - 1) {
        // The last arc always runs to the tread edge, so a breakpoint on it
        // describes nothing. Silently dropping it would hide a half-written
        // spec -- "800@45mm" almost always means an arc is missing.
        if (Number.isFinite(a.to))
          throw new Error("the last tread arc runs to the tread edge, so it must not carry a breakpoint. " +
            "Write \"" + a.r + "\" on its own, or add the arc that follows it.");
        continue;
      }
      if (!Number.isFinite(a.to) || a.to <= prev)
        throw new Error("tread arc breakpoint " + (i + 1) + " must increase along the half width; got " +
          a.to + " after " + prev);
      if (a.to >= half)
        throw new Error("tread arc breakpoint " + (i + 1) + " (" + a.to.toFixed(2) +
          " mm) is at or beyond the tread edge (" + half.toFixed(2) + " mm); drop it or widen the tread");
      prev = a.to;
    }
    return true;
  }

  // "300"            one arc, constant radius
  // "125@0.45, 55"   125 mm out to 45% of the half width, then 55 mm to the edge
  // "125@36, 80@62, 55"   breakpoints in mm from the centreline
  // A value <= 1 is read as a fraction of the half width, > 1 as millimetres --
  // a fraction above 1 and a breakpoint under 1 mm are both meaningless.
  function parseCrownArcs(text, treadWidth) {
    const half = treadWidth / 2;
    const parts = String(text || "").split(/[,;]/).map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return null;
    const arcs = [];
    for (let i = 0; i < parts.length; i++) {
      const m = /^([-+0-9.eE]+)\s*(?:@\s*([-+0-9.eE]+)\s*(mm|%)?)?$/.exec(parts[i]);
      if (!m)
        throw new Error("could not read tread arc '" + parts[i] +
          "'. Use radius@breakpoint, e.g. \"125@0.45, 55\" or \"125@36mm, 55\".");
      const r = parseFloat(m[1]);
      let to = m[2] == null ? NaN : parseFloat(m[2]);
      if (m[2] != null) {
        if (m[3] === "%") to = (to / 100) * half;
        else if (m[3] === "mm") to = to;
        else to = to <= 1 ? to * half : to;
      }
      if (i < parts.length - 1 && !Number.isFinite(to))
        throw new Error("tread arc '" + parts[i] + "' needs a breakpoint (only the last arc may omit one)");
      arcs.push({ r: r, to: to });
    }
    validateCrownArcs(arcs, treadWidth);
    return arcs;
  }

  function crownMultiArc(treadWidth, arcs, n) {
    validateCrownArcs(arcs, treadWidth);
    n = n || 801;
    const half = treadWidth / 2;
    const y = new Float64Array(n), r = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      y[i] = -half + (2 * half * i) / (n - 1);
      const a = Math.abs(y[i]);
      let k = arcs.length - 1;
      for (let j = 0; j < arcs.length - 1; j++) if (a < arcs[j].to) { k = j; break; }
      r[i] = arcs[k].r;
    }
    const crown = crownFromRadiusProfile(y, r, false);
    crown.arcs = arcs.map(function (a, i) {
      return { radius: a.r, to_mm: i === arcs.length - 1 ? half : a.to };
    });
    return crown;
  }

  // One place that decides which crown a set of options asks for, so the page,
  // the CLI and the tests cannot drift apart.
  function buildCrown(treadWidth, opts) {
    opts = opts || {};
    if (opts.crown_arcs && opts.crown_arcs.length)
      return crownMultiArc(treadWidth, opts.crown_arcs);
    // Blank fields fall back to the selected class, not to a fixed pair of
    // motorcycle radii. With no class named the answer is unchanged from before.
    const cls = tyreClass(opts.tyre_class);
    const pick = (v, d) => (v == null || v === "" || !Number.isFinite(+v) ? d : +v);
    return crownDualRadius(treadWidth,
      pick(opts.crown_r_center, cls.crown_r_center),
      pick(opts.crown_r_shoulder, cls.crown_r_shoulder),
      pick(opts.crown_break, cls.crown_break));
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
  function isPowerOfTwo(n) { return Number.isInteger(n) && n > 0 && (n & (n - 1)) === 0; }

  function fftRadix2(re, im, inverse) {
    const n = re.length;
    if (n <= 1) return;
    // This is a radix-2 transform: the butterfly loop only reaches `n` when `n`
    // is a power of two.  With any other length it silently returns garbage
    // (NaN, in practice) which then propagates through every aggregate in the
    // sweep with no visible failure -- so refuse rather than corrupt.
    if (!isPowerOfTwo(n)) {
      throw new Error(
        "FFT length must be a power of two, got " + n +
        ". The circumferential grid size (nx) sets this; use 1024, 2048, 4096, ..."
      );
    }
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
    // Caught here as well as in fftRadix2 so the error names the real cause at
    // the point the caller can still fix it.
    if (!isPowerOfTwo(nx)) {
      throw new Error(
        "grid nx must be a power of two (the sweep uses a radix-2 FFT along the " +
        "circumference), got " + nx + ". Use 1024, 2048 or 4096."
      );
    }
    if (!Number.isInteger(ny) || ny < 2) {
      throw new Error("grid ny must be an integer >= 2, got " + ny);
    }
    if (!(pattern.tyre_circumference > 0) || !(pattern.tread_width > 0)) {
      throw new Error(
        "pattern must have positive circumference and tread width, got " +
        pattern.tyre_circumference + " x " + pattern.tread_width + " mm"
      );
    }
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

  function rasterise(pattern, grid, stiffParams, curvatureCorrection, zoneFracs) {
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
    zoneFracs = zoneFracs || {};
    const bounds = zoneBounds(pattern.tread_width, zoneFracs.center, zoneFracs.intermediate);
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
      rowArea: rowArea, stiffness: stiff, curvatureCorrection: !!curvatureCorrection,
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

  // Largest lean the crown can actually reach: past the steepest tread tangent
  // there is no contact point and the position saturates at the tread edge.
  function maxSupportedLean(crown) {
    let mx = 0;
    for (let i = 0; i < crown.phi.length; i++) mx = Math.max(mx, Math.abs(crown.phi[i]));
    return (mx * 180) / Math.PI;
  }

  // Winkler semi-axes at a lean angle -- the shared basis of the lean trend.
  function winklerAxes(crown, gammaDeg, params) {
    const yC = crownContactLateral(crown, gammaDeg);
    const rLat = crownLocalRadius(crown, yC);
    const rEff = Math.max(50, params.wheel_radius - crownDrop(crown, yC));
    const g = (gammaDeg * Math.PI) / 180;
    const fz = params.load_rises_with_lean
      ? params.vertical_load / Math.max(Math.cos(g), 0.2)
      : params.vertical_load;
    // k_f cancels in the ratios these axes are used for, so any positive value
    // gives the same scale factors; 1 keeps the arithmetic simple.
    const delta = Math.sqrt(fz / (1 * Math.PI * Math.sqrt(rEff * rLat)));
    return { a: Math.sqrt(2 * rEff * delta), b: Math.sqrt(2 * rLat * delta), yC: yC, rLat: rLat, rEff: rEff };
  }

  // (s_length, s_width) carrying a patch stated at gammaRef to gammaDeg.
  // Mirrors tread_eval.contact_patch.lean_scale_factors.
  function leanScaleFactors(crown, gammaDeg, params, gammaRefDeg) {
    const ref = winklerAxes(crown, gammaRefDeg || 0, params);
    const tgt = winklerAxes(crown, gammaDeg, params);
    return [tgt.a / Math.max(ref.a, 1e-9), tgt.b / Math.max(ref.b, 1e-9)];
  }

  // Reject geometry that would silently produce a valid-looking but wrong patch
  // (a negative width still has positive shoelace area; a NaN propagates).
  function validateSpec(spec) {
    if (SHAPES.indexOf(String(spec.shape || "").toLowerCase()) < 0)
      throw new Error("unknown contact patch shape '" + spec.shape + "'; expected one of " + SHAPES.join(", "));
    const dims = { length: "circumferential", width: "lateral" };
    for (const name in dims) {
      const v = Number(spec[name]);
      if (!Number.isFinite(v)) throw new Error("contact patch " + name + " must be a finite number, got " + spec[name]);
      if (v <= 0) throw new Error("contact patch " + name + " must be positive, got " + v + " mm (" + name + " is the " + dims[name] + " size)");
    }
    for (const name of ["corner_radius", "exponent", "taper", "rotation", "gamma_deg"]) {
      if (spec[name] != null && !Number.isFinite(Number(spec[name])))
        throw new Error("contact patch " + name + " must be a finite number, got " + spec[name]);
    }
    if (spec.corner_radius != null && spec.corner_radius < 0) throw new Error("corner_radius must be >= 0, got " + spec.corner_radius + " mm");
    if (spec.exponent != null && spec.exponent <= 0) throw new Error("superellipse exponent must be positive, got " + spec.exponent);
    if (spec.taper != null && !(spec.taper > -1 && spec.taper < 1)) throw new Error("trapezoid taper must lie strictly between -1 and 1, got " + spec.taper);
    if (spec.load_N != null && (!Number.isFinite(spec.load_N) || spec.load_N <= 0)) throw new Error("contact patch load must be positive, got " + spec.load_N + " N");
    if (spec.y_center != null && !Number.isFinite(spec.y_center)) throw new Error("contact patch y_center must be a finite number, got " + spec.y_center);
  }

  // Build a placed ContactPatch object from a shape spec.
  // params: {vertical_load, wheel_radius, load_rises_with_lean}
  function shapePatch(spec, crown, treadWidth, params) {
    params = Object.assign({ vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: true }, params || {});
    validateSpec(spec);
    if (!(params.vertical_load > 0) || !Number.isFinite(params.vertical_load))
      throw new Error("vertical load must be a positive finite number, got " + params.vertical_load + " N");
    const gamma = spec.gamma_deg || 0;
    const yC = spec.y_center != null ? spec.y_center : crownContactLateral(crown, gamma);
    let base = shapeOutline(spec);

    // Stated dimensions describe the UPRIGHT patch; carry them to this lean.
    let scaleNote = "";
    if (spec.scale_with_lean !== false && Math.abs(gamma) > 1e-9) {
      const sf = leanScaleFactors(crown, gamma, params, 0);
      base = base.map((p) => [p[0] * sf[0], p[1] * sf[1]]);
      scaleNote = ", scaled to " + fmt(gamma) + " deg lean (x" + sf[0].toFixed(3) + " long, x" + sf[1].toFixed(3) + " wide)";
    }
    let out = base.map((p) => [p[0], p[1] + yC]);
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
      provenance: "user-specified shape: " + describeSpec(spec) + scaleNote + (spec.label ? " -- " + spec.label : ""),
      y_center: yC, r_eff: rEff, r_lat: crownLocalRadius(crown, yC), normal_load: load,
      path_radius: gamma <= 1e-9 ? Infinity : rEff / Math.tan(g), delta: 0, clipped: clipped,
    };
    patch.a = patchLength(patch) / 2;
    patch.b = patchWidth(patch) / 2;
    const area = patchArea(patch);
    // A patch clipped away to nothing is not a thin patch -- it is a patch that
    // missed the tyre. Left alone it sweeps to zero contact area at every angle
    // and reports a confident-looking page of zeros.
    if (area <= 1e-9)
      throw new Error(
        "the contact patch does not overlap the tread: it is centred at y = " + yC.toFixed(1) +
        " mm on a tread running -" + half.toFixed(1) + " to +" + half.toFixed(1) +
        " mm, so clipping leaves no area. Move the patch laterally, make it wider, " +
        "or check the lean angle."
      );
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

  // Per-band correlation.
  //
  // The global correlation sums the per-row products over every row; a band is
  // just that same sum restricted to the rows inside it. So every band comes
  // out of ONE pass over the already-cached map transforms -- N accumulators
  // instead of one, then N inverse transforms. No extra forward FFTs, which is
  // what would otherwise make N bands N times the cost.
  //
  // `bandOfRow[r]` is the band index for grid row r, or -1 for none.
  function correlateByBand(mapSpec, kerRe, kerIm, nx, bandOfRow, nBands) {
    const half = mapSpec.half, ny = mapSpec.ny;
    const accRe = [], accIm = [];
    for (let b = 0; b < nBands; b++) { accRe.push(new Float64Array(half)); accIm.push(new Float64Array(half)); }
    for (let r = 0; r < ny; r++) {
      const bi = bandOfRow[r];
      if (bi < 0) continue;
      const base = r * half, ar = accRe[bi], ai = accIm[bi];
      for (let f = 0; f < half; f++) {
        const mr = mapSpec.re[base + f], mi = mapSpec.im[base + f];
        const kr = kerRe[base + f], ki = kerIm[base + f];
        ar[f] += mr * kr + mi * ki;
        ai[f] += mi * kr - mr * ki;
      }
    }
    const out = [];
    for (let b = 0; b < nBands; b++) out.push(irfft(accRe[b], accIm[b], nx));
    return out;
  }

  // Band edges (mm from the centreline) -> per-row band index.
  // `edges` is ascending and length nBands+1, e.g. [-79.5, -40, 0, 40, 79.5].
  function bandRowIndex(grid, edges) {
    const yv = gridY(grid);
    const idx = new Int32Array(grid.ny).fill(-1);
    for (let r = 0; r < grid.ny; r++) {
      for (let b = 0; b + 1 < edges.length; b++) {
        if (yv[r] >= edges[b] && (yv[r] < edges[b + 1] || b === edges.length - 2)) { idx[r] = b; break; }
      }
    }
    return idx;
  }

  // Evenly spaced band edges across the full tread width.
  function evenBandEdges(treadWidth, n) {
    const half = treadWidth / 2, out = [];
    for (let i = 0; i <= n; i++) out.push(-half + (2 * half * i) / n);
    return out;
  }

  function validateBandEdges(edges, treadWidth) {
    if (!Array.isArray(edges) || edges.length < 2)
      throw new Error("band edges need at least two values (one band)");
    const half = treadWidth / 2;
    for (let i = 0; i < edges.length; i++) {
      if (!Number.isFinite(edges[i]))
        throw new Error("band edge " + (i + 1) + " must be a number, got " + edges[i]);
      if (edges[i] < -half - 1e-6 || edges[i] > half + 1e-6)
        throw new Error("band edge " + edges[i] + " mm lies outside the tread (-" +
          half.toFixed(1) + " to +" + half.toFixed(1) + " mm)");
      if (i && edges[i] <= edges[i - 1])
        throw new Error("band edges must increase; " + edges[i] + " follows " + edges[i - 1]);
    }
    if (edges.length - 1 > 24) throw new Error("at most 24 bands (got " + (edges.length - 1) + ")");
    return edges;
  }

  function kernelSpectrum(map, grid) {
    const ny = grid.ny, nx = grid.nx, half = (nx >> 1) + 1;
    const re = new Float64Array(ny * half), im = new Float64Array(ny * half);
    const row = new Float64Array(nx);
    for (let r = 0; r < ny; r++) {
      const base = r * nx;
      // Every kernel here is a contact patch: most rows of the tread are outside
      // it and transform to zero, which the arrays already hold. Skipping them
      // is exact, and it is what keeps three kernels per lean affordable.
      let any = false;
      for (let c = 0; c < nx; c++) { const v = map[base + c]; row[c] = v; if (v !== 0) any = true; }
      if (!any) continue;
      const f = rfftRow(row, nx);
      re.set(f.re, r * half); im.set(f.im, r * half);
    }
    return { re: re, im: im };
  }

  function maxClamp(arr) { for (let i = 0; i < arr.length; i++) if (arr[i] < 0) arr[i] = 0; return arr; }

  // ---------------------------------------------------------------------
  // slip response: the brush model's first moment about the leading edge
  // ---------------------------------------------------------------------
  //
  // Kx and Ky answer "how hard is the rubber in the patch". They do NOT give a
  // force, because force needs slip. The brush model supplies the missing step:
  // a tread element enters the patch stuck to the road with zero deflection,
  // and while the carcass runs on it is dragged by
  //
  //     longitudinal   u = kappa * s          (kappa = slip ratio)
  //     lateral        u = tan(alpha) * s     (alpha = slip angle)
  //
  // where s is how far that element has travelled since it entered. Summing
  // element force k*u over the patch,
  //
  //     C_kappa = dFx/dkappa  = SUM k_x,i * s_i          [N]
  //     C_alpha = dFy/dalpha  = SUM k_y,i * s_i          [N/rad]
  //
  // -- the FIRST MOMENT of tread stiffness about the leading edge, not the sum.
  // Rubber near the exit is worth far more than rubber at the entry, which is
  // why this curve is not a rescaled Kx or Ky and can move the other way.
  //
  // Taking the same moment again about the patch centre gives the aligning
  // stiffness and the pneumatic trail:
  //
  //     C_mz = -SUM k_y,i * s_i * u_i         [N.mm/rad]   Mz = -C_mz * alpha
  //     t    =  C_mz / C_alpha                [mm]
  //
  // For a uniform patch of half-length a this reduces to C_alpha = 2*c_p*a^2 and
  // t = a/3, the textbook brush results -- both checked in slipaudit.js.
  //
  // Rolling direction. At angle theta the patch covers pattern x in
  // [x_c - a, x_c + a] with x_c = C*theta/360; that is what the correlation
  // means by "the patch at theta". As theta advances, a fixed point of the tread
  // enters at the +x end and leaves at the -x end, so the LEADING (entry) edge
  // is the +x edge and s runs from 0 there to the row's contact length at the
  // exit. Reversing the rolling direction mirrors s, which is exactly why a
  // directional pattern is not symmetric in these curves.
  //
  // Entry is taken at the OUTER face of the entry pixel, not its centre, so the
  // discrete sum is the midpoint rule for the integral and lands on 2*c_p*a^2
  // exactly rather than short by half a pixel per row.
  function slipKernels(masks, grid) {
    const nx = grid.nx, ny = grid.ny, N = nx * ny, dx = grid.dx;
    const u = gridXRel(grid), bin = masks.binary;
    const s = new Float32Array(N), su = new Float32Array(N);
    const entry = new Float64Array(ny).fill(NaN);
    const rowLength = new Float64Array(ny);

    // Longitudinal centre of the patch, measured rather than assumed: a rotated
    // or tapered outline need not be centred on column 0, and the trail is
    // quoted from the patch centre.
    let uSum = 0, nPix = 0;
    for (let r = 0; r < ny; r++) {
      const base = r * nx;
      for (let c = 0; c < nx; c++) if (bin[base + c] > 0) { uSum += u[c]; nPix++; }
    }
    const uc = nPix ? uSum / nPix : 0;

    for (let r = 0; r < ny; r++) {
      const base = r * nx;
      let uMax = -Infinity, uMin = Infinity, n = 0;
      for (let c = 0; c < nx; c++) {
        if (!(bin[base + c] > 0)) continue;
        if (u[c] > uMax) uMax = u[c];
        if (u[c] < uMin) uMin = u[c];
        n++;
      }
      if (!n) continue;
      const e = uMax + dx / 2;
      entry[r] = e;
      rowLength[r] = uMax - uMin + dx;
      for (let c = 0; c < nx; c++) {
        if (!(bin[base + c] > 0)) continue;
        const sv = e - u[c];
        s[base + c] = sv;
        su[base + c] = sv * (u[c] - uc);
      }
    }
    return { s: s, su: su, entry: entry, row_length: rowLength, u_center: uc, n_pixels: nPix };
  }

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

  function sweepLean(pattern, pack, gammaDeg, spec, params, cache, discreteSamples, bandEdges) {
    const grid = pack.grid, nx = grid.nx;
    cache = cache || new MapFFTCache(pack);
    const useSpec = Object.assign({}, spec, { gamma_deg: gammaDeg });
    const patch = shapePatch(useSpec, pattern.crown, pattern.tread_width, params);
    const masks = patchMasks(patch, grid);
    const kb = kernelSpectrum(masks.binary, grid);
    // No pressure-weighted correlation here. Every patch this engine builds has
    // a flat pressure field, so a pressure-weighted area is exactly
    // contact_area * peak_pressure -- it carries no information the binary
    // correlation does not already have, and it cost an extra kernel transform
    // plus a correlation per lean angle. (The Python side keeps it because it
    // can carry a measured pressure grid, where the weighting is real.)

    // The two extra kernels the slip response needs: the patch weighted by
    // distance behind its leading edge, and that weighted again by longitudinal
    // position. The MAP transforms are the same cached kx/ky ones the sums
    // above use -- only the kernel changes -- so this costs two kernel
    // transforms and three correlations per lean, not a second sweep.
    const sk = slipKernels(masks, grid);
    const ksSpec = kernelSpectrum(sk.s, grid);
    const ksuSpec = kernelSpectrum(sk.su, grid);

    const contactArea = maxClamp(correlate(cache.get("area", pack.area), kb.re, kb.im, nx));
    const kx = maxClamp(correlate(cache.get("kx", pack.kx), kb.re, kb.im, nx));
    const ky = maxClamp(correlate(cache.get("ky", pack.ky), kb.re, kb.im, nx));
    const kz = maxClamp(correlate(cache.get("kz", pack.kz), kb.re, kb.im, nx));
    const blockCount = maxClamp(correlate(cache.get("block_frac", pack.blockFrac), kb.re, kb.im, nx));
    const yMoment = correlate(cache.get("y_moment", pack.yMoment), kb.re, kb.im, nx);
    const zoneArea = {};
    for (const z of ZONES) zoneArea[z] = maxClamp(correlate(cache.get("zone_" + z, pack.zoneArea[z]), kb.re, kb.im, nx));

    // Slip response. C_kappa and C_alpha are non-negative by construction; the
    // raw aligning moment is not, and must not be clamped -- it is negative
    // whenever the resultant sits behind the patch centre, which is the normal
    // case and the whole reason a pneumatic trail exists.
    const cKappa = maxClamp(correlate(cache.get("kx", pack.kx), ksSpec.re, ksSpec.im, nx));
    const cAlpha = maxClamp(correlate(cache.get("ky", pack.ky), ksSpec.re, ksSpec.im, nx));
    const mzRaw = correlate(cache.get("ky", pack.ky), ksuSpec.re, ksuSpec.im, nx);
    const cMz = new Float64Array(nx), trail = new Float64Array(nx);
    for (let i = 0; i < nx; i++) {
      cMz[i] = -mzRaw[i];
      // Below a newton per radian the trail is a ratio of two rounding errors.
      trail[i] = cAlpha[i] > 1 ? cMz[i] / cAlpha[i] : 0;
    }

    // Per-band aggregates, if the designer has divided the tread into ribs.
    // Reuses the same cached map transforms as the totals above, so the extra
    // cost is a handful of inverse transforms rather than a second sweep.
    let bands = null;
    if (bandEdges && bandEdges.length >= 2) {
      const nB = bandEdges.length - 1;
      const rowIdx = bandRowIndex(grid, bandEdges);
      const by = (key, map) => correlateByBand(cache.get(key, map), kb.re, kb.im, nx, rowIdx, nB).map(maxClamp);
      // Same rows, s-weighted kernel: which rib actually generates the
      // cornering force, rather than which rib merely has stiff rubber.
      const bys = (key, map) => correlateByBand(cache.get(key, map), ksSpec.re, ksSpec.im, nx, rowIdx, nB).map(maxClamp);
      const bArea = by("area", pack.area), bKx = by("kx", pack.kx),
            bKy = by("ky", pack.ky), bKz = by("kz", pack.kz),
            bCount = by("block_frac", pack.blockFrac),
            bCk = bys("kx", pack.kx), bCa = bys("ky", pack.ky);
      // Geometric width of each band, so a per-mm comparison is possible.
      bands = [];
      for (let b = 0; b < nB; b++) {
        bands.push({
          index: b,
          y_lo: bandEdges[b], y_hi: bandEdges[b + 1],
          width_mm: bandEdges[b + 1] - bandEdges[b],
          contact_area: bArea[b], kx: bKx[b], ky: bKy[b], kz: bKz[b], block_count: bCount[b],
          c_kappa: bCk[b], c_alpha: bCa[b],
        });
      }
    }

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
      // Slip response (brush model). Units: C_kappa N per unit slip ratio,
      // C_alpha N/rad, C_mz N.mm/rad, trail mm. These are the TREAD's share --
      // the carcass is a second spring in series and usually the larger one --
      // so read the variation over theta, not the absolute value.
      c_kappa: cKappa, c_alpha: cAlpha, c_mz: cMz, pneumatic_trail: trail,
      zone_area: zoneArea, bands: bands,
      block_count_discrete: disc.count, theta_discrete: disc.theta,
      // patch_area is the RASTERISED area -- a whole number of pixels -- because
      // contact_area is measured the same way and the land ratio is their
      // quotient. patch_area_outline is the exact polygon area the pressure was
      // derived from. They differ by the raster quantisation, which is also why
      // patch_load (the raster integral of pressure) comes back a fraction of a
      // percent under the stated normal_load. Both are reported rather than
      // reconciled, and the worker warns if the gap grows past 1%.
      patch_area: patchAreaVal, patch_area_outline: patchArea(patch),
      patch_perimeter: shape.perimeter_mm, patch_load: patchLoad, shape: shape,
    };
  }

  function sweep(pattern, pack, leanAngles, spec, params, discreteSamples, bandEdges) {
    const cache = new MapFFTCache(pack);
    return leanAngles.map((g) => sweepLean(pattern, pack, g, spec, params, cache, discreteSamples, bandEdges));
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
  //
  // Order k means k events per wheel revolution.  Amplitude is single-sided and
  // **normalised by the signal mean**, so 0.04 at order 12 reads as "a +-4%
  // modulation of the mean, twelve times per revolution".  That is the same
  // definition as tread_eval.metrics.order_spectrum -- the two must agree,
  // because the report and this page share one reader's guide.
  function orderSpectrum(signal, maxOrder) {
    const n = signal.length;
    if (!n) return { orders: [], amplitude: [] };
    let mean = 0; for (let i = 0; i < n; i++) mean += signal[i]; mean /= n;

    let src, p2;
    if (isPowerOfTwo(n)) {
      p2 = n; src = signal;              // exact: no resampling at all
    } else {
      // Linear interpolation onto the next power of two. Nearest-neighbour
      // here put ~1% of spurious energy into unrelated orders, which on an
      // order chart reads as a pitch problem that does not exist.
      p2 = 1 << Math.ceil(Math.log2(n));
      src = new Float64Array(p2);
      for (let i = 0; i < p2; i++) {
        const t = (i * n) / p2, i0 = Math.floor(t), f = t - i0;
        src[i] = signal[i0 % n] * (1 - f) + signal[(i0 + 1) % n] * f;
      }
    }
    const re = new Float64Array(p2), im = new Float64Array(p2);
    for (let i = 0; i < p2; i++) re[i] = src[i] - mean;
    fftRadix2(re, im, false);

    maxOrder = Math.min(maxOrder || 60, p2 >> 1);
    const scale = mean > 0 ? 1 / mean : 1;
    const orders = [], amplitude = [];
    for (let k = 1; k <= maxOrder; k++) {
      orders.push(k);
      amplitude.push(((2 * Math.hypot(re[k], im[k])) / p2) * scale);
    }
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
    SHORE_E_TABLE, SHORE_K_TABLE, shoreE, shoreK, shoreRangeWarning, calcG, beamKMatrix, effectiveK, computeKz, blockStiffness, effectiveSipes,
    // dxf
    readDxfEntities, entitiesToSegments, buildLoops, closeAcrossSeam, loadPattern, estimatePitchCount, geometricRepeat,
    dxfPairs, planarArrangement, classifyFaces, faceAdjacency, bulgeArc,
    crownMultiArc, parseCrownArcs, validateCrownArcs, buildCrown, MAX_CROWN_ARCS,
    compoundProperties, validateCompound, interpShore,
    tiebarEngagementWear, tiebarEngaged, effectiveBlocks, validateWear, patternAtWear,
    groupTiebars, applyToTiebarGroup, tiebarMetrics, TIEBAR_GROUP_MODES,
    linkTiebars, polygonEdgeKeys, couplingLinkMatrix, tiebarCurrentHeight,
    buildCouplingNetwork, factorCouplingNetwork, choleskyInverse, invert2x2,
    effectiveStiffnessAt, couplingSweep, prepareCouplingNetwork,
    rowRunsOfLabels, rowRunsOfMask, firstRunAfter,
    validateBlockDefaults, validateImportOptions,
    // zones + crown
    zoneBounds, classifyZone, ZONES, crownDualRadius, TYRE_CLASS, tyreClass, crownFromRadiusProfile, crownTangentAngle, crownLocalRadius, crownDrop, crownContactLateral,
    // fft
    fftRadix2, rfftRow, irfft,
    // raster
    makeGrid, gridY, gridXRel, gridThetaDeg, splitAtSeam, rasterise,
    // shapes + patch
    SHAPES, shapeOutline, describeSpec, shapePatch, patchMasks, patchContains, patchArea, patchPerimeter, patchLength, patchWidth,
    validateSpec, leanScaleFactors, maxSupportedLean, isPowerOfTwo,
    // sweep
    MapFFTCache, sweepLean, sweep, correlateByBand, bandRowIndex, evenBandEdges, validateBandEdges,
    kernelSpectrum, correlate, slipKernels,
    // metrics
    fluctuationStats, orderSpectrum, dominantOrders,
  };
});
