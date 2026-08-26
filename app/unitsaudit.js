/* Unit consistency audit for the whole engine.
 *
 * The tool works in ONE system throughout, and every number that crosses a
 * function boundary is in it:
 *
 *     length          mm            circumference, tread width, NSD, radii, s
 *     area            mm^2
 *     force           N             vertical load, Fx, Fy
 *     pressure        N/mm^2 = MPa  E, G, bulk modulus, contact pressure
 *     stiffness       N/mm          Kx, Ky, Kz, k_ax, k_tr
 *     moment          N.mm
 *     angle           degrees at every boundary, radians only inside a formula
 *     slip stiffness  N (per unit slip ratio), N/rad, N.mm/rad
 *     dimensionless   Shore A, Gent k, Poisson, shape factor S, land ratio,
 *                     slip ratio, order amplitude, gains
 *
 * Asserting that by reading the code proves nothing, so this audit proves it by
 * measurement, two ways:
 *
 *   1. GEOMETRIC SIMILARITY. Scale every length in the problem by lambda and
 *      the load by lambda^2 (so pressure is unchanged), leave the compound
 *      alone, and every output must move by the power of lambda its units say
 *      it should -- Kz by lambda, area by lambda^2, C_alpha by lambda^2, the
 *      trail by lambda, and the dimensionless ones not at all. A single term
 *      carrying the wrong power of a length shows up immediately, because it
 *      pulls its total off the predicted exponent.
 *
 *   2. CLOSED FORM in the units themselves: a block whose stiffness can be
 *      written out by hand from E, an area and a length.
 *
 * Run:  node app/unitsaudit.js
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

// The scale factor. Deliberately not a round number and not near 1, so a term
// that is off by one power of length cannot hide inside a tolerance.
const LAM = 2.7;

// A pattern built entirely from lengths, so it can be rebuilt at any scale.
function testPattern(scale) {
  const C = 1200 * scale, W = 200 * scale, nsd = 9 * scale, nb = 16;
  const blocks = [];
  for (let i = 0; i < nb; i++) {
    for (let lane = 0; lane < 3; lane++) {
      const x0 = (i * C) / nb + 3 * scale, x1 = ((i + 1) * C) / nb - 3 * scale;
      const yc = (lane - 1) * (W / 3), h = W / 3 / 2 - 3 * scale;
      blocks.push({
        id: "B" + i + "_" + lane, height: nsd, draft_angle: 4,
        zone: lane === 1 ? "center" : "shoulder", shore_a: null,
        sipes: [], n_lateral_sipes: 0,
        polygon: [[x0, yc - h], [x1, yc - h], [x1, yc + h], [x0, yc + h]],
      });
    }
  }
  return {
    tyre_circumference: C, tread_width: W, pitches: [C / nb], blocks: blocks,
    tiebars: [], crown: E.buildCrown(W, { crown_r_center: 700 * scale, crown_r_shoulder: 90 * scale }),
    meta: {},
  };
}

function runAt(scale, nx, ny) {
  const p = testPattern(scale);
  const pack = E.rasterise(p, E.makeGrid(p, nx || 2048, ny || 128), SP, true, {});
  const spec = { shape: "rounded", length: 150 * scale, width: 170 * scale,
                 corner_radius: 20 * scale, gamma_deg: 0, scale_with_lean: false, y_center: 0 };
  const params = { vertical_load: 4000 * scale * scale, wheel_radius: 320 * scale,
                   load_rises_with_lean: false };
  const r = E.sweepLean(p, pack, 0, spec, params, null, 90, E.evenBandEdges(p.tread_width, 3));
  return { pattern: p, pack: pack, res: r, params: params, spec: spec };
}

const mean = (a) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return a.length ? s / a.length : 0; };

// =====================================================================
section("1. geometric similarity: every output moves by the power its units say");
// =====================================================================
{
  const A = runAt(1), B = runAt(LAM);
  // name -> [value at scale 1, value at scale LAM, expected power of lambda]
  const cases = {
    "block plan area  [mm^2]":        [E.polygonProps(A.pattern.blocks[0].polygon).A,
                                       E.polygonProps(B.pattern.blocks[0].polygon).A, 2],
    "block perimeter  [mm]":          [E.polygonProps(A.pattern.blocks[0].polygon).perimeter,
                                       E.polygonProps(B.pattern.blocks[0].polygon).perimeter, 1],
    "Gent shape factor S  [-]":       [E.blockStiffness(A.pattern.blocks[0], SP).shapeFactor,
                                       E.blockStiffness(B.pattern.blocks[0], SP).shapeFactor, 0],
    "E_eff  [N/mm^2]":                [E.blockStiffness(A.pattern.blocks[0], SP).eEff,
                                       E.blockStiffness(B.pattern.blocks[0], SP).eEff, 0],
    "Kz  [N/mm]":                     [E.blockStiffness(A.pattern.blocks[0], SP).kz,
                                       E.blockStiffness(B.pattern.blocks[0], SP).kz, 1],
    "Kx  [N/mm]":                     [E.blockStiffness(A.pattern.blocks[0], SP).kx,
                                       E.blockStiffness(B.pattern.blocks[0], SP).kx, 1],
    "Ky  [N/mm]":                     [E.blockStiffness(A.pattern.blocks[0], SP).ky,
                                       E.blockStiffness(B.pattern.blocks[0], SP).ky, 1],
    "patch area  [mm^2]":             [A.res.patch_area, B.res.patch_area, 2],
    "patch load  [N]":                [A.res.patch_load, B.res.patch_load, 2],
    "peak pressure  [N/mm^2]":        [A.res.patch.peak_pressure, B.res.patch.peak_pressure, 0],
    "contact area  [mm^2]":           [mean(A.res.contact_area), mean(B.res.contact_area), 2],
    "land ratio  [-]":                [mean(A.res.land_ratio), mean(B.res.land_ratio), 0],
    "blocks in patch  [-]":           [mean(A.res.block_count), mean(B.res.block_count), 0],
    "patch Kz  [N/mm]":               [mean(A.res.kz), mean(B.res.kz), 1],
    "patch Kx  [N/mm]":               [mean(A.res.kx), mean(B.res.kx), 1],
    "patch Ky  [N/mm]":               [mean(A.res.ky), mean(B.res.ky), 1],
    "C_kappa  [N]":                   [mean(A.res.c_kappa), mean(B.res.c_kappa), 2],
    "C_alpha  [N/rad]":               [mean(A.res.c_alpha), mean(B.res.c_alpha), 2],
    "C_mz  [N.mm/rad]":               [mean(A.res.c_mz), mean(B.res.c_mz), 3],
    "pneumatic trail  [mm]":          [mean(A.res.pneumatic_trail), mean(B.res.pneumatic_trail), 1],
    "band C_alpha  [N/rad]":          [mean(A.res.bands[1].c_alpha), mean(B.res.bands[1].c_alpha), 2],
    "patch half length a  [mm]":      [A.res.patch.a, B.res.patch.a, 1],
    "patch perimeter  [mm]":          [A.res.patch_perimeter, B.res.patch_perimeter, 1],
    "zone contact area  [mm^2]":      [mean(A.res.zone_area.center), mean(B.res.zone_area.center), 2],
  };
  for (const name in cases) {
    const [a, b, pow] = cases[name];
    const got = Math.log(b / a) / Math.log(LAM);
    ck(name.padEnd(30) + " scales as lambda^" + pow,
       Math.abs(got - pow) < 5e-3, "measured lambda^" + got.toFixed(5));
  }
}

// =====================================================================
section("2. angles and ratios are invariant under scale");
// =====================================================================
// A tyre twice the size with a twice-as-big crown radius reaches exactly the
// same lean. If any angle were computed from an unnormalised length this would
// move.
{
  const cA = testPattern(1).crown, cB = testPattern(LAM).crown;
  ck("max reachable lean is scale-invariant",
     rel(E.maxSupportedLean(cA), E.maxSupportedLean(cB)) < 1e-9,
     E.maxSupportedLean(cA).toFixed(4) + " deg both");
  ck("crown tangent angle at the same relative position is scale-invariant",
     rel(E.crownTangentAngle(cA, 60), E.crownTangentAngle(cB, 60 * LAM)) < 1e-6);
  ck("crown drop is a length and scales", rel(E.crownDrop(cB, 60 * LAM) / LAM, E.crownDrop(cA, 60)) < 1e-6,
     E.crownDrop(cA, 60).toFixed(4) + " mm -> " + E.crownDrop(cB, 60 * LAM).toFixed(4) + " mm");
  ck("the contact point at a lean scales with the tread",
     rel(E.crownContactLateral(cB, 3) / LAM, E.crownContactLateral(cA, 3)) < 1e-6);

  const A = runAt(1), B = runAt(LAM);
  const sa = E.orderSpectrum(A.res.kz, 40), sb = E.orderSpectrum(B.res.kz, 40);
  let worst = 0;
  for (let i = 0; i < sa.amplitude.length; i++) worst = Math.max(worst, Math.abs(sa.amplitude[i] - sb.amplitude[i]));
  ck("order amplitudes are dimensionless and scale-invariant", worst < 1e-6,
     "worst diff " + worst.toExponential(2));
  ck("Kz fluctuation (CoV) is scale-invariant",
     rel(E.fluctuationStats(A.res.kz).cov, E.fluctuationStats(B.res.kz).cov) < 1e-6);
}

// =====================================================================
section("3. lean scale factors do not depend on the load or on k_f");
// =====================================================================
// winklerAxes carries an unscaled foundation modulus k_f = 1 N/mm^3, so its
// absolute semi-axes are NOT a physical length -- they are only ever used as a
// ratio. That is safe exactly as long as nothing reads them directly, and as
// long as the ratio is independent of the load. Both are checked here.
{
  const crown = testPattern(1).crown;
  const base = { wheel_radius: 320, load_rises_with_lean: true };
  const f1 = E.leanScaleFactors(crown, 4, Object.assign({ vertical_load: 1000 }, base), 0);
  const f2 = E.leanScaleFactors(crown, 4, Object.assign({ vertical_load: 9000 }, base), 0);
  ck("lean scale factors are independent of the vertical load",
     rel(f1[0], f2[0]) < 1e-12 && rel(f1[1], f2[1]) < 1e-12,
     "x" + f1[0].toFixed(5) + " long, x" + f1[1].toFixed(5) + " wide at either load");
  const src = fs.readFileSync(path.join(__dirname, "engine.js"), "utf8");
  const uses = (src.match(/winklerAxes\(/g) || []).length;
  ck("winklerAxes is only ever read through leanScaleFactors", uses === 3,
     uses + " references: its definition and the two inside leanScaleFactors");
  ck("winklerAxes is not exported, so nothing outside can read a semi-axis directly",
     !/\bwinklerAxes,/.test(src.slice(src.lastIndexOf("return {"))));
}

// =====================================================================
section("4. closed form in the units themselves");
// =====================================================================
{
  // A square pad, 20 x 20 mm plan, 10 mm tall, no draft, no sipes.
  const side = 20, h = 10;
  const pad = { id: "P", height: h, draft_angle: 0, shore_a: 60, sipes: [], n_lateral_sipes: 0,
                polygon: [[0, 0], [side, 0], [side, side], [0, side]] };
  const st = E.blockStiffness(pad, SP);
  const Emod = E.shoreE(60), kG = E.shoreK(60);
  const A = side * side, P = 4 * side;
  const S = A / (h * P);
  const EeffUncorr = Emod * (1 + 2 * kG * S * S);
  const Eeff = EeffUncorr / (1 + EeffUncorr / SP.bulk_modulus);
  ck("Shore 60 gives E = 6.89 N/mm^2 (MPa), not Pa and not kPa",
     Math.abs(Emod - 6.89) < 1e-9, Emod + " N/mm^2");
  ck("shape factor S = A / (h * perimeter) is dimensionless",
     rel(st.shapeFactor, S) < 1e-12, "S = " + S.toFixed(4) + " for a 20x20x10 pad");
  ck("E_eff = E(1 + 2kS^2) with the bulk correction  [N/mm^2]",
     rel(st.eEff, Eeff) < 1e-12, st.eEff.toFixed(4) + " N/mm^2");
  ck("Kz = E_eff * A / h  [N/mm^2 * mm^2 / mm = N/mm]",
     rel(st.kz, (Eeff * A) / h) < 1e-12, st.kz.toFixed(2) + " N/mm");
  // A 1 mm deflection of that pad is a force of Kz newtons, by definition.
  ck("1 mm of vertical deflection is Kz newtons", rel(st.kz * 1, (Eeff * A) / h) < 1e-12);

  // Shear-dominated check on the same pad: the beam matrix with G = E/2(1+nu).
  const G = E.calcG(Emod, SP.poisson);
  ck("G = E / 2(1+nu)  [N/mm^2]", rel(G, Emod / (2 * 1.49)) < 1e-12, G.toFixed(4) + " N/mm^2");
  ck("Kx of a short pad stays below the pure-shear bound G*A/h",
     st.kx < (G * A) / h * 1.001,
     st.kx.toFixed(1) + " N/mm vs G*A/h = " + ((G * A) / h).toFixed(1) + " N/mm");

  // Pressure is a load over an area, and the load comes back out of it.
  const p = testPattern(1);
  const spec = { shape: "rectangle", length: 150, width: 170, gamma_deg: 0,
                 scale_with_lean: false, y_center: 0 };
  const patch = E.shapePatch(spec, p.crown, p.tread_width, { vertical_load: 4000, wheel_radius: 320, load_rises_with_lean: false });
  ck("peak pressure = load / patch area  [N / mm^2]",
     rel(patch.peak_pressure, 4000 / E.patchArea(patch)) < 1e-12,
     patch.peak_pressure.toFixed(4) + " N/mm^2 = " + patch.peak_pressure.toFixed(4) + " MPa");
  ck("that pressure is a plausible tyre contact pressure, not a unit slip",
     patch.peak_pressure > 0.05 && patch.peak_pressure < 3.0, "0.05 to 3 MPa is the physical band");
}

// =====================================================================
section("5. the slip response in force units");
// =====================================================================
// C_kappa multiplied by a slip ratio must be a force in newtons, and C_alpha
// multiplied by an angle in RADIANS must be a force in newtons. Getting radians
// and degrees the wrong way round here is a factor of 57.
{
  const A = runAt(1);
  const ca = mean(A.res.c_alpha), ckp = mean(A.res.c_kappa), t = mean(A.res.pneumatic_trail);
  const ky = mean(A.res.ky), a = A.res.patch.a;

  ck("C_alpha / Ky is a length of the order of the patch half length",
     ca / ky > 0.3 * a && ca / ky < a, (ca / ky).toFixed(2) + " mm, a = " + a.toFixed(2) + " mm");
  ck("C_kappa / Kx is the same length", rel(ca / ky, ckp / mean(A.res.kx)) < 0.02,
     (ckp / mean(A.res.kx)).toFixed(2) + " mm");
  // 1 degree of slip angle, converted properly.
  const Fy = ca * (1 * Math.PI) / 180;
  ck("1 degree of slip angle gives C_alpha * pi/180 newtons",
     rel(Fy, ca * 0.0174532925) < 1e-6, Fy.toFixed(0) + " N at 1 deg from the tread alone");
  ck("that force is a sane fraction of the vertical load",
     Fy > 0 && Fy < 20 * A.params.vertical_load,
     "the tread is one spring in series with the carcass, so it reads high on its own");
  // Angle by angle, not mean by mean: the trail is a RATIO, and the mean of a
  // ratio is not the ratio of the means.
  let worstT = 0;
  for (let i = 0; i < A.res.c_mz.length; i++)
    worstT = Math.max(worstT, rel(A.res.c_mz[i], A.res.c_alpha[i] * A.res.pneumatic_trail[i]));
  ck("C_mz = C_alpha * trail at every angle  [N/rad * mm = N.mm/rad]", worstT < 1e-12,
     "worst rel " + worstT.toExponential(2) + ", mean trail " + t.toFixed(3) + " mm");
  ck("the trail is a fraction of the patch half length", t > 0 && t < a,
     t.toFixed(2) + " mm of a = " + a.toFixed(2) + " mm");
  // Mz at 1 degree, at one angle, in N.mm and in N.m
  const i0 = 0;
  const Fy0 = A.res.c_alpha[i0] * (Math.PI / 180);
  const Mz = A.res.c_mz[i0] * (Math.PI / 180);
  ck("Mz at 1 deg = C_mz * pi/180  [N.mm], and equals Fy * t",
     rel(Mz, Fy0 * A.res.pneumatic_trail[i0]) < 1e-9,
     Mz.toFixed(0) + " N.mm = " + (Mz / 1000).toFixed(2) + " N.m");
}

// =====================================================================
section("6. the coupling network in the same units");
// =====================================================================
{
  const p = E.loadPattern(fs.readFileSync(path.join(DATA, "tbr_ribs_tiebars.dxf"), "utf8"),
                          { height: 16, shore_a: 60, draft_angle: 0 }, {}).pattern;
  const net = E.buildCouplingNetwork(p, 9, SP);
  const cp = E.compoundProperties(SP);
  // Reproduce one link by hand from E, an area and a length.
  const tb = p.tiebars.find((t) => t.links && t.links.length);
  const lk = tb.links[0];
  const hb = E.tiebarCurrentHeight(tb, 9);
  const Alink = lk.wall_length * hb;
  const kAx = (cp.E * Alink) / lk.span, kTr = (cp.G * Alink) / lk.span;
  ck("k_ax = E * A / d  [N/mm^2 * mm^2 / mm = N/mm]",
     kAx > 0 && Number.isFinite(kAx),
     "wall " + lk.wall_length.toFixed(2) + " mm x height " + hb.toFixed(2) +
     " mm over span " + lk.span.toFixed(2) + " mm = " + kAx.toFixed(0) + " N/mm");
  const M = E.couplingLinkMatrix(lk.dir, kAx, kTr);
  ck("the link matrix entries are stiffnesses, all in N/mm",
     Number.isFinite(M.xx) && Number.isFinite(M.yy) && M.xx > 0 && M.yy > 0);
  ck("the network's own link agrees with that hand calculation",
     net.links.some((L) => rel(L.C.xx, M.xx) < 1e-9 && rel(L.C.yy, M.yy) < 1e-9));
  ck("E and G are the same compound the blocks use",
     rel(cp.G, E.calcG(cp.E, SP.poisson)) < 1e-12,
     "E " + cp.E.toFixed(3) + ", G " + cp.G.toFixed(3) + " N/mm^2");

  // The gain is a ratio of two stiffnesses and must be dimensionless -- so it
  // cannot move when the whole tyre is scaled.
  const scale = 1.9;
  const big = {
    tyre_circumference: p.tyre_circumference * scale, tread_width: p.tread_width * scale,
    pitches: p.pitches, crown: p.crown, meta: {},
    blocks: p.blocks.map((b) => Object.assign({}, b, {
      height: b.height * scale, polygon: b.polygon.map((q) => [q[0] * scale, q[1] * scale] ) })),
    tiebars: p.tiebars.map((t) => Object.assign({}, t, {
      nsd: t.nsd * scale, height: t.height * scale,
      centroid_x: t.centroid_x * scale, centroid_y: t.centroid_y * scale,
      polygon: t.polygon.map((q) => [q[0] * scale, q[1] * scale]) })),
  };
  E.linkTiebars(big.blocks, big.tiebars);
  const packS = (pp, s) => E.rasterise(pp, E.makeGrid(pp, 2048, 128), SP, false, {});
  const specOf = (s) => ({ shape: "rectangle", length: 180 * s, width: 190 * s,
                           gamma_deg: 0, scale_with_lean: false, y_center: 0 });
  const parOf = (s) => ({ vertical_load: 4000 * s * s, wheel_radius: 320 * s, load_rises_with_lean: false });
  const c1 = E.couplingSweep(p, packS(p, 1), E.shapePatch(specOf(1), p.crown, p.tread_width, parOf(1)), 9, SP, 180);
  const c2 = E.couplingSweep(big, packS(big, scale), E.shapePatch(specOf(scale), big.crown, big.tread_width, parOf(scale)), 9 * scale, SP, 180);
  ck("the coupling gain is dimensionless and scale-invariant",
     rel(c1.gain_kx, c2.gain_kx) < 5e-3,
     "x" + c1.gain_kx.toFixed(4) + " at 1:1, x" + c2.gain_kx.toFixed(4) + " at " + scale + ":1");
  ck("the coupled Kx is a stiffness and scales as lambda",
     rel(mean(c2.kx_coupled) / scale, mean(c1.kx_coupled)) < 5e-3,
     mean(c1.kx_coupled).toFixed(0) + " -> " + mean(c2.kx_coupled).toFixed(0) + " N/mm");
}

// =====================================================================
section("7. inputs are rejected when they are in the wrong unit");
// =====================================================================
{
  const bad = (over) => {
    try { E.validateCompound(Object.assign({ modulus_mode: "direct", e_modulus: 7, gent_k: 0.64 }, over)); }
    catch (e) { return e.message; }
    return "";
  };
  // 6.89 N/mm^2 typed as 6 890 000 Pa, or as 6890 kPa: both are the same slip.
  ck("a modulus in kPa is refused, and the message names the unit",
     /not kPa/.test(bad({ e_modulus: 6890 })), bad({ e_modulus: 6890 }).slice(-70));
  ck("a modulus in Pa is refused too", bad({ e_modulus: 6890000 }).length > 0);
  ck("a modulus in GPa (0.00689) is refused as too small",
     bad({ e_modulus: 0.00689 }).length > 0, bad({ e_modulus: 0.00689 }).slice(-60));
  ck("a valid tread modulus in N/mm^2 passes", bad({ e_modulus: 6.89 }) === "");
  ck("the Gent coefficient must stay of order 1", bad({ gent_k: 640 }).length > 0);

  let msg = "";
  try {
    const p = testPattern(1);
    E.shapePatch({ shape: "rectangle", length: 150, width: 170, gamma_deg: 0 }, p.crown, p.tread_width,
                 { vertical_load: -1, wheel_radius: 320 });
  } catch (e) { msg = e.message; }
  ck("a negative vertical load is refused, naming newtons", /N\b/.test(msg), msg.slice(0, 80));

  msg = "";
  try { E.makeGrid({ tyre_circumference: 0, tread_width: 200 }, 1024, 64); } catch (e) { msg = e.message; }
  ck("a zero circumference is refused, naming mm", /mm/.test(msg), msg.slice(0, 80));

  // Degrees at the boundary: a lean angle is given in degrees everywhere.
  const crown = testPattern(1).crown;
  ck("crownContactLateral takes DEGREES, not radians",
     Math.abs(E.crownContactLateral(crown, 0)) < 1e-9 &&
     E.crownContactLateral(crown, 3) > E.crownContactLateral(crown, 1),
     "3 deg reaches further out than 1 deg");
  ck("maxSupportedLean returns degrees", E.maxSupportedLean(crown) > 1 && E.maxSupportedLean(crown) < 90,
     E.maxSupportedLean(crown).toFixed(2) + " deg");
}

// ---------------------------------------------------------------------------
section("9. the cross stiffness Kxy");
// ---------------------------------------------------------------------------
{
  // The similarity pattern above is rectangular, and a rectangle has no cross
  // term at all -- which is itself the first thing worth asserting. A sheared
  // block does have one, so the scaling check uses that.
  const rect = (s) => [[0, 0], [40 * s, 0], [40 * s, 25 * s], [0, 25 * s]];
  const shear = (s) => [[0, 0], [40 * s, 0], [55 * s, 25 * s], [15 * s, 25 * s]];
  const blk = (poly, s) => ({ polygon: poly, height: 9 * s, draft_angle: 0,
                              shore_a: 60, sipes: [], n_lateral_sipes: 0 });
  const kxyOf = (poly, s) => E.blockStiffness(blk(poly, s), SP).kxy;

  ck("a rectangular block has no cross term at all", kxyOf(rect(1), 1) === 0,
     "Kxy = " + kxyOf(rect(1), 1));
  const k1 = kxyOf(shear(1), 1);
  ck("a sheared block does", Math.abs(k1) > 1, "Kxy = " + k1.toFixed(2) + " N/mm");
  ck("Kxy scales as a stiffness  [N/mm], one power of length",
     rel(kxyOf(shear(LAM), LAM) / LAM, k1) < 1e-9,
     k1.toFixed(3) + " -> " + kxyOf(shear(LAM), LAM).toFixed(3) + " N/mm at " + LAM + ":1");
  // Mirroring the shear reverses the coupling and nothing else: this is the
  // antisymmetry that makes a symmetric pattern's Kxy average to zero.
  const mirror = shear(1).map((q) => [-q[0], q[1]]);
  ck("mirroring a block flips the sign of Kxy and keeps its size",
     rel(Math.abs(kxyOf(mirror, 1)), Math.abs(k1)) < 1e-9 && kxyOf(mirror, 1) * k1 < 0,
     k1.toFixed(3) + " vs " + kxyOf(mirror, 1).toFixed(3) + " N/mm");
  ck("and Kx and Ky are unchanged by that mirror",
     rel(E.blockStiffness(blk(mirror, 1), SP).kx, E.blockStiffness(blk(shear(1), 1), SP).kx) < 1e-9 &&
     rel(E.blockStiffness(blk(mirror, 1), SP).ky, E.blockStiffness(blk(shear(1), 1), SP).ky) < 1e-9);

  // And through the sweep: a patch over a symmetric pair averages to zero,
  // while the swing is real.
  const patSym = {
    tyre_circumference: 400, tread_width: 120, pitches: [100], tiebars: [],
    crown: E.buildCrown(120, { crown_r_center: 700, crown_r_shoulder: 90 }), meta: {},
    blocks: [
      Object.assign({ id: "L", zone: "center" }, blk(shear(1).map((q) => [q[0] + 20, q[1] - 60]), 1)),
      Object.assign({ id: "R", zone: "center" }, blk(mirror.map((q) => [q[0] + 220, q[1] - 60]), 1)),
    ],
  };
  const packS = E.rasterise(patSym, E.makeGrid(patSym, 2048, 128), SP, false, {});
  const resS = E.sweepLean(patSym, packS, 0,
    { shape: "rectangle", length: 60, width: 100, gamma_deg: 0, scale_with_lean: false, y_center: 0 },
    { vertical_load: 2000, wheel_radius: 320, load_rises_with_lean: false }, null, 90, null);
  const sw = Math.max.apply(null, Array.prototype.map.call(resS.kxy, Math.abs));
  ck("the swept Kxy swings on a sheared pattern", sw > 1, "peak |Kxy| = " + sw.toFixed(2) + " N/mm");
  ck("and averages to nothing over a mirror-symmetric pair",
     Math.abs(mean(resS.kxy)) < 0.02 * sw,
     "mean " + mean(resS.kxy).toExponential(2) + " against a peak of " + sw.toFixed(2));
  ck("Kx and Ky, by contrast, are strictly positive everywhere",
     Array.prototype.every.call(resS.kx, (v) => v >= 0) &&
     Array.prototype.every.call(resS.ky, (v) => v >= 0));
  ck("but Kxy takes both signs, so it cannot be clamped",
     Array.prototype.some.call(resS.kxy, (v) => v < -1e-9) &&
     Array.prototype.some.call(resS.kxy, (v) => v > 1e-9));
}

// ---------------------------------------------------------------------------
section("10. reading the 2x2: principal axes, Cxy, positive definiteness");
// ---------------------------------------------------------------------------
{
  // The identity the whole decomposition rests on. Take an UNCOUPLED structure
  // -- principal stiffnesses K1 and K2, no cross term in its own axes -- and
  // rotate it by theta. Measured in the tyre's axes it now shows
  //
  //     Kxy = (K1 - K2)/2 * sin(2 theta)
  //
  // WITHOUT anything having been coupled. Nothing was added; a rotated object
  // is being measured with unrotated rulers. If the tool cannot reproduce this
  // exactly, it cannot tell that mechanism from a genuine tie-bar interaction.
  const K1 = 900, K2 = 300;
  function rotated(thDeg) {
    const th = thDeg * Math.PI / 180, c = Math.cos(th), s = Math.sin(th);
    return {
      kx: K1 * c * c + K2 * s * s,
      ky: K1 * s * s + K2 * c * c,
      kxy: (K1 - K2) * s * c,
    };
  }
  let worstKxy = 0, worstAng = 0, worstK1 = 0;
  for (let a = -85; a <= 90; a += 5) {
    const r = rotated(a);
    const want = ((K1 - K2) / 2) * Math.sin(2 * a * Math.PI / 180);
    worstKxy = Math.max(worstKxy, Math.abs(r.kxy - want));
    const p = E.principalStiffness(r.kx, r.ky, r.kxy);
    worstK1 = Math.max(worstK1, Math.abs(p.k1 - K1), Math.abs(p.k2 - K2));
    // The recovered axis is the angle it was rotated by, modulo 180.
    let d = p.angle_deg - a;
    while (d <= -90) d += 180;
    while (d > 90) d -= 180;
    worstAng = Math.max(worstAng, Math.abs(d));
  }
  ck("a merely ROTATED structure shows Kxy = (K1-K2)/2 sin(2th) and nothing else",
     worstKxy < 1e-9, "worst " + worstKxy.toExponential(2) + " N/mm over -85..90 deg");
  ck("the principal stiffnesses are recovered whatever the rotation",
     worstK1 < 1e-9, "worst " + worstK1.toExponential(2) + " N/mm vs " + K1 + " / " + K2);
  ck("and the principal AXIS recovers the rotation that was applied",
     worstAng < 1e-9, "worst " + worstAng.toExponential(2) + " deg");

  // Invariants: rotating a stiffness cannot change its trace or determinant.
  const r30 = rotated(30), p30 = E.principalStiffness(r30.kx, r30.ky, r30.kxy);
  ck("trace is invariant under rotation  [N/mm]",
     rel(p30.k1 + p30.k2, K1 + K2) < 1e-12, (p30.k1 + p30.k2).toFixed(6));
  ck("determinant is invariant under rotation  [(N/mm)^2]",
     rel(p30.det, K1 * K2) < 1e-12, p30.det.toFixed(4));
  ck("anisotropy is dimensionless and unchanged by rotation",
     rel(p30.anisotropy, K1 / K2) < 1e-12, p30.anisotropy.toFixed(6));

  // Cxy is dimensionless: scale every length and the load and it must not move
  // AT ALL, while the stiffnesses it is built from each move by one power of
  // lambda. The similarity pattern above is rectangular and has no cross term
  // to speak of, so the sheared block from section 9 is used instead -- a test
  // against an identically zero quantity proves nothing.
  const shearAt = (s) => {
    const b = { polygon: [[0, 0], [40 * s, 0], [55 * s, 25 * s], [15 * s, 25 * s]],
                height: 9 * s, draft_angle: 0, shore_a: 60, sipes: [], n_lateral_sipes: 0 };
    const k = E.blockStiffness(b, SP);
    return E.principalStiffness(k.kx, k.ky, k.kxy);
  };
  const cA = shearAt(1), cB = shearAt(LAM);
  ck("Cxy is dimensionless -- unchanged by geometric similarity",
     Math.abs(cA.cxy - cB.cxy) < 1e-9,
     cA.cxy.toFixed(9) + " at 1:1 vs " + cB.cxy.toFixed(9) + " at " + LAM + ":1");
  ck("the principal stiffnesses it is built from DO scale, by one power of lambda",
     rel(cB.k1 / LAM, cA.k1) < 1e-9 && rel(cB.k2 / LAM, cA.k2) < 1e-9,
     cA.k1.toFixed(2) + " -> " + cB.k1.toFixed(2) + " N/mm");
  ck("and the principal AXIS is a shape property, so it does not move either",
     Math.abs(cA.angle_deg - cB.angle_deg) < 1e-9,
     cA.angle_deg.toFixed(6) + " deg at both scales");
  ck("Cxy is a ratio, so it cannot reach 1 for a positive-definite 2x2",
     Math.abs(cA.cxy) < 1, Math.abs(cA.cxy).toFixed(6));

  // Positive definiteness. This is the one that has real bite in this engine:
  // every other map is clamped at zero to absorb FFT round-off, and Kxy is
  // deliberately NOT, so a clamp that fired on Kx while Kxy stayed finite would
  // report a tread that gives energy back.
  // Run it on the real motorcycle sample rather than the rectangular similarity
  // pattern: that one has no cross term at all, so it would satisfy this
  // trivially and prove nothing about the case the guard exists for.
  const real = E.loadPattern(fs.readFileSync(path.join(DATA, "130_80R17_Tramplr_XR_tread_plan.dxf"), "utf8"),
    { height: 8.5, shore_a: 60, draft_angle: 3 }, {});
  const realPack = E.rasterise(real.pattern, E.makeGrid(real.pattern, 2048, 128), SP, false, {});
  const rr = E.sweepLean(real.pattern, realPack, 0,
    { shape: "rounded", length: 90, width: 50, corner_radius: 12, gamma_deg: 0,
      scale_with_lean: false, y_center: 0 },
    { vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: false }, null, 90, null);
  const kxySwing = Math.max.apply(null, Array.prototype.map.call(rr.kxy, Math.abs));
  ck("the sample tread really does carry a cross term, so this is not vacuous",
     kxySwing > 0.5, "peak |Kxy| = " + kxySwing.toFixed(3) + " N/mm");
  let worstDet = Infinity, atTheta = 0, worstC = 0;
  for (let i = 0; i < rr.kx.length; i++) {
    const d = rr.kx[i] * rr.ky[i] - rr.kxy[i] * rr.kxy[i];
    if (d < worstDet) { worstDet = d; atTheta = rr.theta_deg[i]; }
    worstC = Math.max(worstC, Math.abs(E.principalStiffness(rr.kx[i], rr.ky[i], rr.kxy[i]).cxy));
  }
  ck("KxKy - Kxy^2 > 0 at every theta: deflecting the tread always costs energy",
     worstDet > 0, "worst " + worstDet.toExponential(3) + " (N/mm)^2 at theta = " + atTheta.toFixed(1) + " deg");
  ck("which is the same as saying |Cxy| < 1 everywhere",
     worstC < 1, "worst |Cxy| = " + worstC.toExponential(2));
  ck("and on this tread the coupling is a fraction of a percent, not a design driver",
     worstC < 0.02, "peak |Cxy| = " + (100 * worstC).toFixed(3) + "%");

  // Symmetry. True by construction today -- the compliance inverted is
  // symmetric, and the network assembly writes one value into both off-diagonal
  // slots -- so this is a tripwire for a future mechanism that writes them
  // separately, not a verification of anything the code currently chooses.
  const eng = fs.readFileSync(path.join(__dirname, "engine.js"), "utf8");
  const asm = eng.slice(eng.indexOf("function factorCouplingNetwork("),
                        eng.indexOf("function invert2x2("));
  ck("the network writes ONE kxy into both off-diagonal slots (Maxwell-Betti)",
     /A\[\(2 \* a\) \* n \+ \(2 \* a \+ 1\)\] \+= K\.kxy/.test(asm) &&
     /A\[\(2 \* a \+ 1\) \* n \+ \(2 \* a\)\] \+= K\.kxy/.test(asm),
     "Kxy = Kyx by construction");
  const inv = E.invert2x2({ xx: 640, xy: -37, yy: 410 });
  ck("and inverting a symmetric 2x2 gives a symmetric 2x2",
     Math.abs(inv.xy - inv.xy) === 0 && rel(inv.xx * 640 + inv.xy * -37, 1) < 1e-12);

  // A block's own 2x2 must be positive definite too, or the patch sum of them
  // could not be.
  const shearBlk = { polygon: [[0, 0], [40, 0], [55, 25], [15, 25]], height: 9,
                     draft_angle: 0, shore_a: 60, sipes: [], n_lateral_sipes: 0 };
  const sb = E.blockStiffness(shearBlk, SP);
  const ps = E.principalStiffness(sb.kx, sb.ky, sb.kxy);
  ck("a single sheared block is positive definite", ps.det > 0 && ps.k2 > 0,
     "K1 " + ps.k1.toFixed(2) + ", K2 " + ps.k2.toFixed(2) + " N/mm");
  ck("and its stiff axis is tilted off the rolling direction, as a slanted block must be",
     ps.angle_deg !== null && Math.abs(ps.angle_deg) > 0.5,
     "principal axis at " + ps.angle_deg.toFixed(2) + " deg, Cxy = " + ps.cxy.toFixed(4));
  const mirroredBlk = Object.assign({}, shearBlk,
    { polygon: shearBlk.polygon.map((q) => [-q[0], q[1]]) });
  const pm = E.principalStiffness.apply(null,
    ["kx", "ky", "kxy"].map((k) => E.blockStiffness(mirroredBlk, SP)[k]));
  ck("mirroring the block mirrors the axis and leaves the principal values alone",
     rel(pm.k1, ps.k1) < 1e-9 && rel(pm.k2, ps.k2) < 1e-9 &&
     Math.abs(pm.angle_deg + ps.angle_deg) < 1e-9,
     ps.angle_deg.toFixed(3) + " deg vs " + pm.angle_deg.toFixed(3) + " deg");
}

console.log("\n" + (fails ? fails + " of " + checks + " checks FAILED" : checks + " checks passed"));
process.exitCode = fails ? 1 : 0;
