/* Audit of the slip response ("Level 2"): C_kappa, C_alpha, C_mz and the
 * pneumatic trail.
 *
 * These are the first quantities in the tool that are FORCES rather than
 * stiffnesses, so they rest on a physical model (the brush model) that the rest
 * of the engine does not.  Everything below is checked against a closed form, an
 * independently-computed quantity, or a physical statement that must hold
 * whatever the numbers turn out to be -- never against a previous run of the
 * same code.
 *
 * Run:  node app/slipaudit.js
 */
"use strict";
const E = require("./engine.js");
const fs = require("fs");
const path = require("path");

const DATA = path.join(__dirname, "..", "data");
let fails = 0, checks = 0;
function ck(name, cond, extra) {
  checks++;
  if (!cond) fails++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (extra ? "   " + extra : ""));
}
function section(t) { console.log("\n" + t); }
const rel = (a, b) => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), 1e-12);

const SP = { shore_a: 60, poisson: 0.49, mode: "parallel", bulk_modulus: 1100,
             n_slices: 20, sipe_model: "layered" };
const PARAMS = { vertical_load: 4000, wheel_radius: 320, load_rises_with_lean: true };

// A tread with no grooves at all: one rectangular block covering the whole
// developed surface.  Every pixel then carries the same stiffness, which is the
// only case where the brush model has a closed form to check against.
function uniformPattern(circ, width, nsd) {
  const eps = 1e-6;                       // keep the outline off the raster edge
  const poly = [[eps, -width / 2 + eps], [circ - eps, -width / 2 + eps],
                [circ - eps, width / 2 - eps], [eps, width / 2 - eps]];
  return {
    tyre_circumference: circ, tread_width: width, pitches: [circ],
    blocks: [{ id: "B000", polygon: poly, height: nsd, zone: "center",
               draft_angle: 0, shore_a: null, sipes: [], n_lateral_sipes: 0 }],
    tiebars: [], crown: E.buildCrown(width, {}), meta: {},
  };
}

function packOf(pattern, nx, ny) {
  return E.rasterise(pattern, E.makeGrid(pattern, nx, ny), SP, false, {});
}

function dxf(file, nsd) {
  return E.loadPattern(fs.readFileSync(path.join(DATA, file), "utf8"),
                       { height: nsd, shore_a: 60, draft_angle: 0 }, {}).pattern;
}

// The contact length the RASTER actually has, which is a whole number of pixels
// and so is never exactly the length that was asked for. Every closed form below
// is stated against this rather than against the nominal figure -- otherwise the
// check is measuring the rounding of the patch, not the physics.
function rasterLength(pattern, patch, grid) {
  const sk = E.slipKernels(E.patchMasks(patch, grid), grid);
  let mx = 0;
  for (let r = 0; r < grid.ny; r++) if (sk.row_length[r] > mx) mx = sk.row_length[r];
  return mx;
}

function rectSpec(length, width) {
  return { shape: "rectangle", length: length, width: width, gamma_deg: 0,
           scale_with_lean: false, y_center: 0 };
}

// The moment sums, computed directly from the raster with no FFT anywhere:
// place the patch at theta and add up k*s over every pixel it covers.
function directMoments(pattern, pack, patch, thetaDeg) {
  const grid = pack.grid, nx = grid.nx, ny = grid.ny;
  const masks = E.patchMasks(patch, grid);
  const sk = E.slipKernels(masks, grid);
  const j = Math.round((thetaDeg / 360) * nx) % nx;
  let cK = 0, cA = 0, mz = 0, ky = 0, kx = 0, kxy = 0, area = 0;
  for (let r = 0; r < ny; r++) {
    const base = r * nx;
    for (let c = 0; c < nx; c++) {
      if (!(masks.binary[base + c] > 0)) continue;
      // kernel column c sits over pattern column c + j
      const pc = base + ((c + j) % nx);
      cK += pack.kx[pc] * sk.s[base + c];
      cA += pack.ky[pc] * sk.s[base + c];
      mz += pack.ky[pc] * sk.su[base + c];
      kx += pack.kx[pc]; ky += pack.ky[pc]; area += pack.area[pc];
      // The cross term is the only SIGNED map, so it is the only one where a
      // sign or conjugation slip in the transform could survive: every other
      // quantity here is non-negative and would not notice.
      kxy += pack.kxy[pc];
    }
  }
  return { c_kappa: cK, c_alpha: cA, c_mz: -mz, kx: kx, ky: ky, kxy: kxy, area: area,
           trail: cA > 1 ? -mz / cA : 0 };
}

const mean = (a) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return a.length ? s / a.length : 0; };

// =====================================================================
section("1. the kernel itself: what s and s*u actually contain");
// =====================================================================
{
  const p = uniformPattern(1000, 200, 8);
  const grid = E.makeGrid(p, 1024, 64);
  const patch = E.shapePatch(rectSpec(120, 150), p.crown, p.tread_width, PARAMS);
  const masks = E.patchMasks(patch, grid);
  const sk = E.slipKernels(masks, grid);
  const u = E.gridXRel(grid), nx = grid.nx, ny = grid.ny;

  let sMin = Infinity, sMax = -Infinity, outside = 0, rows = 0;
  for (let r = 0; r < ny; r++) {
    let any = false;
    for (let c = 0; c < nx; c++) {
      const i = r * nx + c;
      if (masks.binary[i] > 0) {
        any = true;
        if (sk.s[i] < sMin) sMin = sk.s[i];
        if (sk.s[i] > sMax) sMax = sk.s[i];
      } else if (sk.s[i] !== 0 || sk.su[i] !== 0) outside++;
    }
    if (any) rows++;
  }
  ck("s is zero everywhere outside the patch", outside === 0, outside + " stray pixels");
  ck("s is strictly positive inside the patch", sMin > 0, "min " + sMin.toFixed(4) + " mm");
  const Lr = rasterLength(p, patch, grid);
  ck("s never exceeds the contact length", sMax <= Lr + 1e-9,
     "max " + sMax.toFixed(3) + " mm of " + Lr.toFixed(3));
  ck("s spans half a pixel to half a pixel short of the far edge",
     Math.abs(sMin - grid.dx / 2) < 1e-6 && Math.abs(sMax - (Lr - grid.dx / 2)) < 1e-6,
     "dx/2 = " + (grid.dx / 2).toFixed(4) + " mm");

  // Entry is the +x edge: the pixel with the LARGEST u must have the SMALLEST s.
  let iMinS = -1, iMaxS = -1;
  const row = (ny >> 1) * nx;
  for (let c = 0; c < nx; c++) {
    const i = row + c;
    if (!(masks.binary[i] > 0)) continue;
    if (iMinS < 0 || sk.s[i] < sk.s[iMinS]) iMinS = i;
    if (iMaxS < 0 || sk.s[i] > sk.s[iMaxS]) iMaxS = i;
  }
  ck("the leading edge is the +x end of the patch", u[iMinS - row] > u[iMaxS - row],
     "s smallest at u = " + u[iMinS - row].toFixed(1) +
     " mm, largest at u = " + u[iMaxS - row].toFixed(1) + " mm");

  ck("the patch centre is measured, not assumed", Math.abs(sk.u_center) < grid.dx,
     "u_center " + sk.u_center.toExponential(2) + " mm");
  ck("every covered row has the same contact length on a rectangle",
     new Set(Array.from(sk.row_length).filter((v) => v > 0).map((v) => v.toFixed(6))).size === 1);
}

// =====================================================================
section("2. closed form: uniform land under a rectangular patch");
// =====================================================================
// With uniform stiffness per unit area the sums integrate exactly:
//   C_alpha / Ky = mean of s over [0, L] = L/2 = a
//   trail        = a/3
// Both are the textbook brush results, and neither involves this code.
{
  const p = uniformPattern(1000, 200, 8);
  const pack = packOf(p, 1024, 64);
  for (const L of [60, 120, 200]) {
    const spec = rectSpec(L, 150);
    const a = rasterLength(p, E.shapePatch(spec, p.crown, p.tread_width, PARAMS), pack.grid) / 2;
    const r = E.sweepLean(p, pack, 0, spec, PARAMS, null, 90, null);
    const ca = mean(r.c_alpha), ck2 = mean(r.c_kappa), ky = mean(r.ky), kxm = mean(r.kx);
    ck(`C_alpha = Ky * a at L = ${L} mm`, rel(ca, ky * a) < 2e-3,
       `${ca.toFixed(1)} vs ${(ky * a).toFixed(1)} N/rad`);
    ck(`C_kappa = Kx * a at L = ${L} mm`, rel(ck2, kxm * a) < 2e-3,
       `${ck2.toFixed(1)} vs ${(kxm * a).toFixed(1)} N`);
    ck(`pneumatic trail = a/3 at L = ${L} mm`, rel(mean(r.pneumatic_trail), a / 3) < 3e-3,
       `${mean(r.pneumatic_trail).toFixed(3)} vs ${(a / 3).toFixed(3)} mm`);
    ck(`C_mz = C_alpha * t at L = ${L} mm`,
       rel(mean(r.c_mz), ca * mean(r.pneumatic_trail)) < 3e-3);
  }
}

// =====================================================================
section("3. the FFT route agrees with a direct summation");
// =====================================================================
// The sweep gets these curves from two circular cross-correlations. This adds
// the same numbers up by hand at a few angles, with no transform involved.
{
  const p = dxf("tbr_ribs_tiebars.dxf", 16);
  const pack = packOf(p, 2048, 128);
  const spec = rectSpec(180, 190);
  const patch = E.shapePatch(spec, p.crown, p.tread_width, PARAMS);
  const r = E.sweepLean(p, pack, 0, spec, PARAMS, null, 90, null);
  const nx = pack.grid.nx;
  let worstA = 0, worstK = 0, worstM = 0, worstT = 0;
  for (const th of [0, 37.5, 91.2, 180, 274.6]) {
    const j = Math.round((th / 360) * nx) % nx;
    const d = directMoments(p, pack, patch, (j * 360) / nx);
    worstA = Math.max(worstA, rel(d.c_alpha, r.c_alpha[j]));
    worstK = Math.max(worstK, rel(d.c_kappa, r.c_kappa[j]));
    worstM = Math.max(worstM, rel(d.c_mz, r.c_mz[j]));
    worstT = Math.max(worstT, Math.abs(d.trail - r.pneumatic_trail[j]));
  }
  ck("C_alpha: FFT = direct summation", worstA < 1e-9, "worst rel " + worstA.toExponential(2));
  ck("C_kappa: FFT = direct summation", worstK < 1e-9, "worst rel " + worstK.toExponential(2));
  ck("C_mz: FFT = direct summation", worstM < 1e-8, "worst rel " + worstM.toExponential(2));
  ck("trail: FFT = direct summation", worstT < 1e-9, "worst abs " + worstT.toExponential(2) + " mm");
}

// The cross stiffness needs its own fixture and its own check.
//
// Kxy is the ONLY map that carries a sign through the correlation. Every other
// quantity in this engine -- area, Kx, Ky, Kz, C_alpha, C_kappa -- is
// non-negative and is clamped at zero to absorb round-off, so a sign error or a
// conjugation slip in the transform would leave all of them untouched and would
// pass every other check in this suite. The mirror-symmetry check in the units
// audit works on a single BLOCK, before the transform. This is the only place
// the signed quantity meets the FFT and is checked against arithmetic.
//
// The rib fixture above has no cross term at all, so it is useless here: this
// runs on the motorcycle sample, whose angled lugs give it a real one.
{
  const p = dxf("130_80R17_Tramplr_XR_tread_plan.dxf", 8.5);
  const pack = packOf(p, 2048, 128);
  const spec = rectSpec(90, 50);
  const patch = E.shapePatch(spec, p.crown, p.tread_width, PARAMS);
  const r = E.sweepLean(p, pack, 0, spec, PARAMS, null, 90, null);
  const nx = pack.grid.nx;

  const peak = Math.max.apply(null, Array.prototype.map.call(r.kxy, Math.abs));
  ck("the fixture carries a real cross term, so this check is not vacuous",
     peak > 0.5, "peak |Kxy| = " + peak.toFixed(3) + " N/mm");
  ck("and it takes BOTH signs, so a flipped sign could not hide as an offset",
     Array.prototype.some.call(r.kxy, (v) => v < -0.1) &&
     Array.prototype.some.call(r.kxy, (v) => v > 0.1));

  let worstXY = 0, worstKx = 0, worstKy = 0, worstAr = 0, sameSign = true;
  for (const th of [0, 37.5, 91.2, 180, 274.6]) {
    const j = Math.round((th / 360) * nx) % nx;
    const d = directMoments(p, pack, patch, (j * 360) / nx);
    // Absolute, not relative: the cross term passes through zero, and a
    // relative error against a value near zero is meaningless.
    worstXY = Math.max(worstXY, Math.abs(d.kxy - r.kxy[j]));
    worstKx = Math.max(worstKx, rel(d.kx, r.kx[j]));
    worstKy = Math.max(worstKy, rel(d.ky, r.ky[j]));
    worstAr = Math.max(worstAr, rel(d.area, r.contact_area[j]));
    if (Math.abs(d.kxy) > 0.1 && d.kxy * r.kxy[j] <= 0) sameSign = false;
  }
  ck("Kxy: FFT = direct summation", worstXY < 1e-8,
     "worst abs " + worstXY.toExponential(2) + " N/mm against a " + peak.toFixed(2) + " N/mm peak");
  ck("and with the same SIGN at every angle tested", sameSign);
  ck("Kx, Ky and contact area agree on the same tread to the same precision",
     worstKx < 1e-9 && worstKy < 1e-9 && worstAr < 1e-9,
     "worst rel " + Math.max(worstKx, worstKy, worstAr).toExponential(2));
}

// =====================================================================
section("4. bounds and signs that must hold on any pattern");
// =====================================================================
{
  const p = dxf("tbr_ribs_tiebars.dxf", 16);
  const pack = packOf(p, 2048, 128);
  const L = 180;
  const r = E.sweepLean(p, pack, 0, rectSpec(L, 190), PARAMS, null, 90, null);
  const n = r.c_alpha.length;
  let negA = 0, negK = 0, overA = 0, badT = 0, worstRatio = 0;
  for (let i = 0; i < n; i++) {
    if (r.c_alpha[i] < 0) negA++;
    if (r.c_kappa[i] < 0) negK++;
    // every element's s is at most the contact length, so the moment is at most
    // the plain sum times that length
    if (r.c_alpha[i] > r.ky[i] * L * (1 + 1e-9)) overA++;
    if (!(r.pneumatic_trail[i] > 0 && r.pneumatic_trail[i] < L / 2)) badT++;
    worstRatio = Math.max(worstRatio, r.c_alpha[i] / Math.max(r.ky[i] * L, 1e-9));
  }
  ck("C_alpha is never negative", negA === 0);
  ck("C_kappa is never negative", negK === 0);
  ck("C_alpha <= Ky * contact length at every angle", overA === 0,
     "worst ratio " + worstRatio.toFixed(3));
  ck("the trail is positive and inside the patch at every angle", badT === 0,
     "range " + Math.min.apply(null, Array.from(r.pneumatic_trail)).toFixed(2) + ".." +
     Math.max.apply(null, Array.from(r.pneumatic_trail)).toFixed(2) + " mm");

  // The point of the whole feature: this is not Ky wearing a different hat.
  const ratio = [];
  for (let i = 0; i < n; i++) ratio.push(r.c_alpha[i] / Math.max(r.ky[i], 1e-9));
  const rs = E.fluctuationStats(ratio);
  ck("C_alpha is not proportional to Ky", rs.cov > 1e-3,
     "C_alpha/Ky varies by " + (rs.cov * 100).toFixed(2) + "% over a revolution");
  const sa = E.fluctuationStats(r.c_alpha), sy = E.fluctuationStats(r.ky);
  console.log("       Ky fluctuates " + (sy.cov * 100).toFixed(2) +
              "%, C_alpha " + (sa.cov * 100).toFixed(2) + "% over one revolution");
}

// =====================================================================
section("5. rolling direction: a fore-aft asymmetric tread is not symmetric");
// =====================================================================
// Mirroring the pattern in x reverses which end of every block meets the
// leading edge first. Ky cannot see that: it is a plain sum, so mirroring the
// tread simply mirrors its curve in theta. C_alpha must see it, because s is
// measured from the leading edge and mirroring swaps which edge that is.
//
// Note what is NOT a valid check here: the MEAN over theta. The mean of a
// circular correlation is (mean of the map) x (sum of the kernel), so it cannot
// depend on how the pattern is arranged at all -- mirrored or shuffled, the mean
// C_alpha is identical. The information is entirely in the curve.
{
  // A deliberately one-sided tread: a long ramp then a step.
  const C = 900, W = 180, nb = 12;
  const blocks = [];
  for (let i = 0; i < nb; i++) {
    const x0 = (i * C) / nb + 2, x1 = ((i + 1) * C) / nb - 2;
    blocks.push({ id: "B" + i, height: 10, zone: "center", draft_angle: 0, shore_a: null,
                  sipes: [], n_lateral_sipes: 0,
                  polygon: [[x0, -W / 2 + 2], [x1, -W / 2 + 2], [x1, 0], [x0, W / 2 - 2]] });
  }
  const saw = { tyre_circumference: C, tread_width: W, pitches: [C / nb], blocks: blocks,
                tiebars: [], crown: E.buildCrown(W, {}), meta: {} };
  const mirror = Object.assign({}, saw, {
    blocks: blocks.map((b, i) => Object.assign({}, b, {
      polygon: b.polygon.map((q) => [C - q[0], q[1]]).slice().reverse(), id: "M" + i })),
  });
  const spec = rectSpec(70, 170);
  const packA = packOf(saw, 1024, 96);
  const A = E.sweepLean(saw, packA, 0, spec, PARAMS, null, 90, null);
  const B = E.sweepLean(mirror, packOf(mirror, 1024, 96), 0, spec, PARAMS, null, 90, null);
  const n = A.ky.length;

  // Mirroring x -> C-x maps column c to (-c) mod nx, so a direction-blind curve
  // must satisfy  f_mirror[j] = f_original[-j].  Measured against each curve's
  // own fluctuation, so "unchanged" means unchanged compared with how much the
  // quantity moves at all -- not compared with its mean, which is huge.
  const devAgainstMirror = (a, b) => {
    let worst = 0;
    for (let j = 0; j < n; j++) worst = Math.max(worst, Math.abs(b[j] - a[(n - j) % n]));
    return worst / Math.max(E.fluctuationStats(a).range, 1e-12);
  };
  const dKy = devAgainstMirror(A.ky, B.ky);
  const dCa = devAgainstMirror(A.c_alpha, B.c_alpha);
  ck("Ky is direction-blind: mirroring the tread just mirrors its curve",
     dKy < 0.05, "worst deviation " + (100 * dKy).toFixed(2) + "% of the Ky swing");
  ck("C_alpha is direction-sensitive: mirroring is NOT a mirror of its curve",
     dCa > 0.5, "worst deviation " + (100 * dCa).toFixed(0) + "% of the C_alpha swing");
  ck("the mean over theta cannot tell them apart", rel(mean(A.c_alpha), mean(B.c_alpha)) < 1e-9,
     "mean C_alpha identical either way -- the direction lives in the curve");

  // And the physical form of that sensitivity: because the s-weighted kernel's
  // centroid sits a trail's-worth behind the patch centre, the C_alpha curve is
  // the Ky curve DELAYED by exactly that distance. Found here by cross-
  // correlation, with no knowledge of where it should be.
  const pitchPx = Math.round(1024 / nb);
  const centred = (a) => { const m = mean(a); return a.map ? a.map((v) => v - m) : Array.from(a, (v) => v - m); };
  const ca = centred(A.c_alpha), ky = centred(A.ky);
  let bestLag = 0, best = -Infinity;
  for (let lag = -pitchPx >> 1; lag <= pitchPx >> 1; lag++) {
    let s = 0;
    for (let j = 0; j < n; j++) s += ca[j] * ky[((j - lag) % n + n) % n];
    if (s > best) { best = s; bestLag = lag; }
  }
  const lagMm = (bestLag * C) / n, trail = mean(A.pneumatic_trail);
  ck("the C_alpha curve lags Ky by the pneumatic trail",
     Math.abs(lagMm - trail) < 1.5 * (C / n),
     "lag " + lagMm.toFixed(2) + " mm vs trail " + trail.toFixed(2) + " mm (grid " + (C / n).toFixed(2) + " mm)");
  ck("the lag is a delay, not an advance", bestLag > 0,
     "rubber near the exit carries the force, so the peak arrives late");
}

// =====================================================================
section("6. an elliptical patch has its own closed form, and it is not a/3");
// =====================================================================
// Row half-length a(y) = a*sqrt(1 - (y/b)^2). With uniform stiffness density
// the row integrals give, over the whole patch,
//   C_alpha / Ky = 8a/(3*pi) = 0.8488 a       (the rectangle gives a)
//   t            = 3*pi*a/32 = 0.2945 a       (the rectangle gives a/3)
// The shoulders of the patch are short, so they are worth less than the middle
// twice over -- once in the sum and once in the moment.
{
  const p = uniformPattern(1000, 200, 8);
  const pack = packOf(p, 1024, 64);
  const L = 140, Wp = 150, a = L / 2;
  const el = { shape: "ellipse", length: L, width: Wp, gamma_deg: 0, scale_with_lean: false, y_center: 0 };
  const r = E.sweepLean(p, pack, 0, el, PARAMS, null, 90, null);
  const ratio = mean(r.c_alpha) / mean(r.ky), t = mean(r.pneumatic_trail);
  ck("C_alpha / Ky = 8a/(3pi) on an ellipse", rel(ratio, (8 * a) / (3 * Math.PI)) < 5e-3,
     ratio.toFixed(3) + " vs " + ((8 * a) / (3 * Math.PI)).toFixed(3) + " mm");
  ck("the trail is 3*pi*a/32 on an ellipse", rel(t, (3 * Math.PI * a) / 32) < 5e-3,
     t.toFixed(3) + " vs " + ((3 * Math.PI * a) / 32).toFixed(3) + " mm");
  ck("both sit below the rectangle's a and a/3", ratio < a && t < a / 3,
     "a = " + a + " mm, a/3 = " + (a / 3).toFixed(2) + " mm");
}

// =====================================================================
section("7. lean, wear and bands stay consistent");
// =====================================================================
{
  const p = dxf("tbr_ribs_tiebars.dxf", 16);
  const pack = packOf(p, 2048, 128);
  const edges = E.evenBandEdges(p.tread_width, 4);
  const r = E.sweepLean(p, pack, 0, rectSpec(180, 190), PARAMS, null, 90, edges);
  let worst = 0;
  for (let i = 0; i < r.c_alpha.length; i++) {
    let s = 0;
    for (const b of r.bands) s += b.c_alpha[i];
    worst = Math.max(worst, rel(s, r.c_alpha[i]));
  }
  ck("the bands' C_alpha adds up to the total", worst < 1e-9, "worst rel " + worst.toExponential(2));
  let worstK = 0;
  for (let i = 0; i < r.c_kappa.length; i++) {
    let s = 0;
    for (const b of r.bands) s += b.c_kappa[i];
    worstK = Math.max(worstK, rel(s, r.c_kappa[i]));
  }
  ck("the bands' C_kappa adds up to the total", worstK < 1e-9, "worst rel " + worstK.toExponential(2));

  // Wear shortens every block, which stiffens it, so the tread's share of the
  // cornering stiffness rises. Nothing about the brush weighting changes.
  const worn = E.patternAtWear(p, 8);
  const rw = E.sweepLean(worn, packOf(worn, 2048, 128), 0, rectSpec(180, 190), PARAMS, null, 90, null);
  ck("wearing the tread raises C_alpha", mean(rw.c_alpha) > mean(r.c_alpha),
     mean(r.c_alpha).toFixed(0) + " -> " + mean(rw.c_alpha).toFixed(0) + " N/rad at 8 mm wear");
  ck("wear leaves the trail essentially alone",
     rel(mean(rw.pneumatic_trail), mean(r.pneumatic_trail)) < 0.05,
     mean(r.pneumatic_trail).toFixed(3) + " -> " + mean(rw.pneumatic_trail).toFixed(3) +
     " mm (geometry of the patch, not of the blocks)");
}

// =====================================================================
section("8. resolution independence");
// =====================================================================
// A patch rasterises to a whole number of pixels, so the contact length it
// actually gets is never quite the length that was asked for -- 120 mm becomes
// 119.14 mm on one grid and 121.09 on another. C_alpha goes as the SQUARE of
// that length, so its absolute value moves by a percent or so between rasters
// exactly as the contact area does, and chasing that is chasing the patch, not
// the moment. What must be raster-independent is the shape of the moment:
// C_alpha measured against that raster's own contact length.
{
  const p = uniformPattern(1000, 200, 8);
  const spec = rectSpec(120, 150);
  const at = (nx, ny) => {
    const pack = packOf(p, nx, ny);
    const r = E.sweepLean(p, pack, 0, spec, PARAMS, null, 90, null);
    const a = rasterLength(p, E.shapePatch(spec, p.crown, p.tread_width, PARAMS), pack.grid) / 2;
    return { ca: mean(r.c_alpha), t: mean(r.pneumatic_trail), a: a,
             norm: mean(r.c_alpha) / (mean(r.ky) * a), tn: mean(r.pneumatic_trail) / a };
  };
  const g = [at(512, 32), at(1024, 64), at(2048, 128)];
  ck("C_alpha / (Ky * a) = 1 on every raster",
     g.every((v) => rel(v.norm, 1) < 1e-6), g.map((v) => v.norm.toFixed(9)).join(" / "));
  ck("trail / a = 1/3 on every raster",
     g.every((v) => rel(v.tn, 1 / 3) < 1e-3), g.map((v) => v.tn.toFixed(6)).join(" / "));
  ck("the absolute value tracks the raster's own contact length, nothing else",
     g.every((v) => rel(v.ca / (v.a * v.a), g[1].ca / (g[1].a * g[1].a)) < 2e-3),
     "half-lengths " + g.map((v) => v.a.toFixed(3)).join(" / ") + " mm");
}

console.log("\n" + (fails ? fails + " of " + checks + " checks FAILED" : checks + " checks passed"));
process.exitCode = fails ? 1 : 0;
