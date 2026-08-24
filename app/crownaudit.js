/* Audit of the crown profile: arcs, drops, and the solver between them.
 *
 * The crown decides the contact point at every lean, the patch size there, the
 * effective rolling radius and the maximum lean the tyre can reach at all. It
 * can now be given either way round -- as arc radii, or as the DROP a designer
 * actually works from -- and the two must describe the same profile.
 *
 * Everything here is checked against a closed form, against the other
 * representation, or against a statement that must hold whatever the numbers
 * turn out to be.
 *
 * Run:  node app/crownaudit.js
 */
"use strict";
const E = require("./engine.js");

let fails = 0, checks = 0;
function ck(name, cond, extra) {
  checks++;
  if (!cond) fails++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (extra ? "   " + extra : ""));
}
function section(t) { console.log("\n" + t); }
const rel = (a, b) => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), 1e-12);
const deg = (r) => (r * 180) / Math.PI;
function threw(fn) { try { fn(); return ""; } catch (e) { return e.message; } }

// =====================================================================
section("1. a single arc, against the closed form");
// =====================================================================
// One arc of radius R over a developed half width w:
//     phi(w) = w/R          z(w) = R (1 - cos(w/R))        yproj = R sin(w/R)
{
  const W = 200, half = W / 2;
  for (const R of [90, 300, 800, 1500]) {
    const c = E.crownMultiArc(W, [{ r: R, to: NaN }]);
    const phi = half / R;
    ck("R = " + String(R).padStart(4) + ": tangent at the edge is w/R",
       rel(E.crownTangentAngle(c, half), phi) < 1e-12, deg(phi).toFixed(4) + " deg");
    ck("R = " + String(R).padStart(4) + ": drop at the edge is R(1 - cos w/R)",
       rel(E.crownDrop(c, half), R * (1 - Math.cos(phi))) < 1e-12,
       (R * (1 - Math.cos(phi))).toFixed(5) + " mm");
    ck("R = " + String(R).padStart(4) + ": max reachable lean is that tangent",
       rel(E.maxSupportedLean(c), deg(phi)) < 1e-9, deg(phi).toFixed(3) + " deg");
  }
}

// =====================================================================
section("2. the crown is symmetric and continuous across the centreline");
// =====================================================================
{
  const W = 204;
  const c = E.crownMultiArc(W, [{ r: 800, to: 45 }, { r: 300, to: 88 }, { r: 90, to: NaN }]);
  let worstZ = 0, worstPhi = 0;
  for (let y = 0; y <= 102; y += 0.37) {
    worstZ = Math.max(worstZ, Math.abs(E.crownDrop(c, y) - E.crownDrop(c, -y)));
    worstPhi = Math.max(worstPhi, Math.abs(E.crownTangentAngle(c, y) + E.crownTangentAngle(c, -y)));
  }
  ck("drop is even in y", worstZ < 1e-12, "worst " + worstZ.toExponential(2) + " mm");
  ck("tangent angle is odd in y", worstPhi < 1e-12, "worst " + worstPhi.toExponential(2) + " rad");
  ck("both are zero on the centreline",
     Math.abs(E.crownDrop(c, 0)) < 1e-12 && Math.abs(E.crownTangentAngle(c, 0)) < 1e-12);
  // tangent continuity across a breakpoint: phi is an integral, so it cannot jump
  const eps = 1e-4;
  for (const bp of [45, 88]) {
    const jump = Math.abs(E.crownTangentAngle(c, bp + eps) - E.crownTangentAngle(c, bp - eps));
    ck("tangent is continuous through the breakpoint at " + bp + " mm", jump < 1e-5,
       "jump " + jump.toExponential(2) + " rad");
  }
  // The local radius is stored on the sample grid and read back by
  // interpolation, so it is probed a whole cell either side rather than a
  // micron -- within one cell of a breakpoint it reads as a blend of the two.
  ck("curvature DOES step at a breakpoint — that is what a multi-arc profile is",
     Math.abs(E.crownLocalRadius(c, 46) - E.crownLocalRadius(c, 44)) > 100,
     E.crownLocalRadius(c, 44).toFixed(0) + " -> " + E.crownLocalRadius(c, 46).toFixed(0) + " mm");
}

// =====================================================================
section("3. drops -> radii, against the closed form");
// =====================================================================
{
  const W = 200, half = W / 2;
  for (const R of [90, 300, 800, 1500]) {
    const d = R * (1 - Math.cos(half / R));
    const arcs = E.crownArcsFromDrops(W, [{ z: d, to: NaN }]);
    ck("a drop of " + d.toFixed(4) + " mm solves back to R = " + R,
       rel(arcs[0].r, R) < 1e-9, arcs[0].r.toFixed(6) + " mm");
  }
  // the solver's own formula, checked independently
  const R = 250, phi0 = 0.2, L = 30;
  const z = E.arcDropAfter(R, phi0, L);
  ck("arcDropAfter is R[cos phi0 - cos(phi0 + L/R)]",
     rel(z, R * (Math.cos(phi0) - Math.cos(phi0 + L / R))) < 1e-15);
  ck("solveArcRadius inverts it", rel(E.solveArcRadius(z, phi0, L, 1), R) < 1e-9,
     E.solveArcRadius(z, phi0, L, 1).toFixed(6) + " vs " + R);
}

// =====================================================================
section("4. round trip: arcs -> drops -> arcs");
// =====================================================================
// The strongest check here. Build a crown from known radii, read the drops off
// it, solve the radii back, and they must be the ones we started with.
{
  const cases = [
    { W: 200, spec: [{ r: 800, to: 45 }, { r: 300, to: 78 }, { r: 90, to: NaN }] },
    { W: 204, spec: [{ r: 1500, to: 87 }, { r: 120, to: NaN }] },
    { W: 159, spec: [{ r: 125, to: 36 }, { r: 80, to: 62 }, { r: 55, to: NaN }] },
    { W: 240, spec: [{ r: 2000, to: NaN }] },
  ];
  for (const cs of cases) {
    const half = cs.W / 2;
    const c0 = E.crownMultiArc(cs.W, cs.spec);
    const drops = cs.spec.map((a, i) => ({
      z: E.crownDrop(c0, i === cs.spec.length - 1 ? half : a.to),
      to: i === cs.spec.length - 1 ? NaN : a.to,
    }));
    const back = E.crownArcsFromDrops(cs.W, drops);
    let worst = 0;
    for (let i = 0; i < back.length; i++) worst = Math.max(worst, rel(back[i].r, cs.spec[i].r));
    ck("W " + cs.W + ", " + cs.spec.length + " arc(s): radii recovered",
       worst < 1e-9, "worst rel " + worst.toExponential(2) + " on " +
       cs.spec.map((a) => a.r).join("/"));
    // and the profile built from those drops is the same profile
    const c1 = E.crownFromDrops(cs.W, drops);
    let wz = 0, wp = 0;
    for (let y = 0; y <= half; y += half / 97) {
      wz = Math.max(wz, Math.abs(E.crownDrop(c1, y) - E.crownDrop(c0, y)));
      wp = Math.max(wp, Math.abs(E.crownTangentAngle(c1, y) - E.crownTangentAngle(c0, y)));
    }
    ck("W " + cs.W + ": the two profiles agree point by point",
       wz < 1e-9 && wp < 1e-12, "worst drop diff " + wz.toExponential(2) + " mm");
    ck("W " + cs.W + ": and so does the reachable lean",
       rel(E.maxSupportedLean(c1), E.maxSupportedLean(c0)) < 1e-12,
       E.maxSupportedLean(c0).toFixed(4) + " deg");
  }
}

// =====================================================================
section("5. the built crown hits the drops it was asked for");
// =====================================================================
{
  const W = 204;
  const drops = [{ z: 1.5, to: 40 }, { z: 5.0, to: 80 }, { z: 11.0, to: NaN }];
  const c = E.crownFromDrops(W, drops);
  let worst = 0;
  for (const d of c.drops) worst = Math.max(worst, Math.abs(d.achieved_mm - d.asked_mm));
  ck("every station lands on its drop", worst < 1e-9,
     "worst " + (worst * 1e6).toFixed(3) + " nm over " + c.drops.length + " stations");
  ck("the achieved drops are reported, not assumed",
     c.drops.every((d) => Number.isFinite(d.achieved_mm) && Number.isFinite(d.asked_mm)));
  ck("the solved radii come back with the crown",
     c.arcs && c.arcs.length === 3 && c.arcs.every((a) => a.radius > 0),
     c.arcs.map((a) => a.radius.toFixed(1)).join(" / ") + " mm");
  // The tangent must steepen outboard -- that is forced, since every drop is
  // larger than the one before. The RADII need not fall monotonically: these
  // drops imply a flatter middle arc, which is a real shape and worth seeing.
  ck("the tangent steepens outboard, which is forced",
     E.crownTangentAngle(c, 40) < E.crownTangentAngle(c, 80) &&
     E.crownTangentAngle(c, 80) < E.crownTangentAngle(c, 102),
     [40, 80, 102].map((y) => deg(E.crownTangentAngle(c, y)).toFixed(2)).join(" -> ") + " deg");
  ck("radii need not fall monotonically, and this set does not",
     c.arcs[1].radius > c.arcs[0].radius,
     "a drop set that looks progressive can imply a flatter middle arc");
  const norm = E.crownFromDrops(W, [{ z: 1.0, to: 40 }, { z: 4.0, to: 80 }, { z: 12.0, to: NaN }]);
  ck("a crown that really does tighten outboard solves that way too",
     norm.arcs[0].radius > norm.arcs[1].radius && norm.arcs[1].radius > norm.arcs[2].radius,
     norm.arcs.map((a) => a.radius.toFixed(0)).join(" / ") + " mm");
  ck("the crown is flagged as drop-derived", c.from_drops === true);
}

// =====================================================================
section("6. scale invariance");
// =====================================================================
// Double the tread and double every drop and the radii double with them, while
// the tangent angles -- and so the reachable lean -- do not move at all.
{
  const lam = 2.7;
  const drops = [{ z: 1.5, to: 40 }, { z: 5.0, to: 80 }, { z: 11.0, to: NaN }];
  const big = drops.map((d) => ({ z: d.z * lam, to: Number.isFinite(d.to) ? d.to * lam : NaN }));
  const a = E.crownArcsFromDrops(204, drops), b = E.crownArcsFromDrops(204 * lam, big);
  let worst = 0;
  for (let i = 0; i < a.length; i++) worst = Math.max(worst, rel(b[i].r / lam, a[i].r));
  ck("radii scale with the tyre", worst < 1e-9, "worst rel " + worst.toExponential(2));
  ck("the reachable lean does not",
     rel(E.maxSupportedLean(E.crownFromDrops(204, drops)),
         E.maxSupportedLean(E.crownFromDrops(204 * lam, big))) < 1e-9,
     E.maxSupportedLean(E.crownFromDrops(204, drops)).toFixed(4) + " deg either way");
}

// =====================================================================
section("7. the parser");
// =====================================================================
{
  const W = 200;
  ck("a bare number is a single arc to the edge",
     JSON.stringify(E.parseCrownDrops("9.5", W)) === '[{"z":9.5,"to":null}]');
  ck("a station <= 1 is a fraction of the half width",
     E.parseCrownDrops("1.2@0.45, 9.5", W)[0].to === 45);
  ck("a station > 1 is millimetres", E.parseCrownDrops("1.2@36, 9.5", W)[0].to === 36);
  ck("mm can be written explicitly", E.parseCrownDrops("1.2@36mm, 9.5", W)[0].to === 36);
  ck("so can a percentage", E.parseCrownDrops("1.2@45%, 9.5", W)[0].to === 45);
  ck("blank text is no profile at all", E.parseCrownDrops("", W) === null);
  ck("the same station conventions as the arc parser",
     E.parseCrownArcs("300@0.45, 90", W)[0].to === E.parseCrownDrops("1@0.45, 9", W)[0].to);
}

// =====================================================================
section("8. refusals");
// =====================================================================
{
  const W = 200;
  const m = (t) => threw(() => E.parseCrownDrops(t, W));
  ck("drops must increase outboard", /must be greater than the one before/.test(m("4@45, 2")),
     m("4@45, 2").slice(0, 62) + "...");
  ck("the last drop carries no station", /must not carry a station/.test(m("1.2@45, 9.5@90")));
  ck("a station beyond the tread edge is refused", /at or beyond the tread edge/.test(m("1.2@150, 9.5")));
  ck("stations must increase", /must increase along the half width/.test(m("1@60, 2@30, 9")));
  ck("a negative drop is refused", /positive number of mm/.test(m("-2")));
  ck("a drop bigger than the half width is refused, naming the unit",
     /check the units/.test(m("140")), m("140").slice(-60));
  ck("unreadable text is refused with the format", /Use drop@station/.test(m("wide@thing")));

  // geometry the solver cannot honour
  const flat = threw(() => E.crownArcsFromDrops(W, [{ z: 20, to: 50 }, { z: 20.0001, to: NaN }]));
  ck("a station that needs less drop than a straight continuation is refused",
     /dead straight continuation/.test(flat), flat.slice(0, 80) + "...");
  const fold = threw(() => E.crownArcsFromDrops(W, [{ z: 95, to: NaN }]));
  ck("a drop that needs a fold rather than a crown is refused",
     /a fold rather than a crown/.test(fold), fold.slice(0, 80) + "...");

  // and the two ways of saying it cannot both be given
  const both = threw(() => E.buildCrown(W, {
    crown_arcs: [{ r: 300, to: NaN }], crown_drops: [{ z: 9, to: NaN }] }));
  ck("arcs and drops together are refused rather than silently ranked",
     /two ways of saying the same thing/.test(both), both.slice(0, 70) + "...");
}

// =====================================================================
section("9. nothing else about the crown changed");
// =====================================================================
// The two-radius blend and the class fallbacks are untouched by any of this.
{
  const W = 204;
  const blend = E.buildCrown(W, {});
  ck("a blank crown still falls back to the class default, not to flat",
     E.crownLocalRadius(blend, 0) === 125 && E.crownDrop(blend, 102) > 0,
     "R centre " + E.crownLocalRadius(blend, 0) + " mm (2W default)");
  for (const cls of ["2w", "pcr", "tbr"]) {
    const c = E.buildCrown(W, { tyre_class: cls });
    const t = E.tyreClass(cls);
    ck("class " + cls + " still uses its own crown",
       rel(E.crownLocalRadius(c, 0), t.crown_r_center) < 1e-9,
       "R centre " + t.crown_r_center + " mm, max lean " + E.maxSupportedLean(c).toFixed(1) + " deg");
  }
  ck("the two-radius blend is still a blend, not a step",
     E.crownLocalRadius(E.buildCrown(W, { tyre_class: "tbr" }), 95) < 1500 &&
     E.crownLocalRadius(E.buildCrown(W, { tyre_class: "tbr" }), 95) > 120,
     "R at 95 mm is between the two");
}

console.log("\n" + (fails ? fails + " of " + checks + " checks FAILED" : checks + " checks passed"));
process.exitCode = fails ? 1 : 0;
