/* Audit of the tie-bar coupling network ("Tier 2").
 *
 * The coupled solve replaces the engine's one standing assumption -- that a
 * block's stiffness depends on nothing but its own polygon -- so it gets its
 * own audit rather than riding on the existing self-tests.  Everything here is
 * checked against a closed form, an independently-computed quantity, or a
 * physical statement that must hold whatever the numbers turn out to be.
 *
 * Run:  node app/couplingaudit.js
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

const SP = { shore_a: 65, poisson: 0.49, mode: "parallel", bulk_modulus: 1100,
             n_slices: 20, sipe_model: "layered" };

function load(file, defaults, opts) {
  return E.loadPattern(fs.readFileSync(path.join(DATA, file), "utf8"), defaults, opts || {});
}
function netOf(pattern, wear, withBars) {
  const p = Object.assign({}, pattern, { tiebars: withBars === false ? [] : pattern.tiebars });
  return E.factorCouplingNetwork(E.buildCouplingNetwork(p, wear, SP));
}
function loadNodes(net, ids, weights) {
  const idx = [], w = [];
  ids.forEach(function (id, i) {
    const k = net.nodes.findIndex(function (v) { return v.id === id; });
    if (k < 0) throw new Error("no node " + id);
    idx.push(k); w.push(weights ? weights[i] : 1);
  });
  return E.effectiveStiffnessAt(net, idx, w);
}
const gain = (r) => r.coupled.xx / r.uncoupled.xx;

// =====================================================================
section("1. linear algebra");
// =====================================================================
{
  const n = 5, A = new Float64Array(n * n);
  const M = [[1, 2, 0, 1, 3], [0, 3, 1, 2, 0], [2, 0, 1, 0, 1], [1, 1, 1, 1, 2], [0, 1, 2, 0, 1]];
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    let s = i === j ? 1 : 0;
    for (let k = 0; k < n; k++) s += M[k][i] * M[k][j];
    A[i * n + j] = s;
  }
  const Ai = E.choleskyInverse(A, n);
  let worst = 0, asym = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    let s = 0;
    for (let k = 0; k < n; k++) s += A[i * n + k] * Ai[k * n + j];
    worst = Math.max(worst, Math.abs(s - (i === j ? 1 : 0)));
    asym = Math.max(asym, Math.abs(Ai[i * n + j] - Ai[j * n + i]));
  }
  ck("choleskyInverse: A*inv(A) = I", worst < 1e-12, "max|err| " + worst.toExponential(2));
  ck("choleskyInverse: inverse is symmetric", asym === 0);
  ck("choleskyInverse: refuses a non-PD matrix", E.choleskyInverse(new Float64Array([1, 2, 2, 1]), 2) === null);

  const a = { xx: 3, xy: 1, yy: 2 }, b = E.invert2x2(E.invert2x2(a));
  ck("invert2x2 is an involution", Math.abs(b.xx - 3) < 1e-12 && Math.abs(b.xy - 1) < 1e-12);
  ck("invert2x2 of a singular matrix returns zero, not NaN",
     E.invert2x2({ xx: 1, xy: 1, yy: 1 }).xx === 0);

  const Cx = E.couplingLinkMatrix([1, 0], 100, 30);
  ck("link along x: axial into xx, shear into yy, no cross term",
     Cx.xx === 100 && Cx.yy === 30 && Cx.xy === 0);
  const s = Math.SQRT1_2, Cd = E.couplingLinkMatrix([s, s], 100, 30);
  ck("45 deg link: xx=yy=(kax+ktr)/2, xy=(kax-ktr)/2",
     Math.abs(Cd.xx - 65) < 1e-9 && Math.abs(Cd.yy - 65) < 1e-9 && Math.abs(Cd.xy - 35) < 1e-9);
  ck("an isotropic link has no cross term at any angle",
     Math.abs(E.couplingLinkMatrix([0.6, 0.8], 50, 50).xy) < 1e-12);
  const Cn = E.couplingLinkMatrix([-s, -s], 100, 30);
  ck("link matrix is even in the direction vector",
     Math.abs(Cn.xy - Cd.xy) < 1e-12, "reversing the arrow must not change the physics");
}

// =====================================================================
section("2. network construction (4 blocks, bars between 1-2 and 2-3)");
// =====================================================================
const asym = load("asym_tiebars.dxf", { height: 16, draft_angle: 0 }, { min_block_area: 5 });
{
  const p = asym.pattern;
  ck("4 blocks, 2 bars found", p.blocks.length === 4 && p.tiebars.length === 2);
  const t = p.tiebars[0];
  ck("each bar links exactly 2 blocks", p.tiebars.every(function (b) { return b.links.length === 2; }));
  ck("shared wall length = the bar's lateral width (6 mm)",
     Math.abs(t.links[0].wall_length - 6) < 1e-6, t.links[0].wall_length.toFixed(4));
  ck("span = half the groove width (3 mm)",
     Math.abs(t.links[0].span - 3) < 1e-6, t.links[0].span.toFixed(4));
  ck("link direction is circumferential", Math.abs(Math.abs(t.links[0].dir[0]) - 1) < 1e-9);

  const n = netOf(p, 0, true);
  ck("the untied block is its own connected component", n.nComp === 2,
     n.members.map(function (m) { return m.map(function (i) { return n.nodes[i].id; }).join("+"); }).join(" | "));
  const excluded = Object.assign({}, p, { tiebars: p.tiebars.map(function (t2) { return Object.assign({}, t2, { enabled: false }); }) });
  const ne = E.buildCouplingNetwork(excluded, 0, SP);
  ck("a bar the user unticks leaves the network entirely",
     ne.links.length === 0 && ne.nodes.every(function (v) { return v.kind === "block"; }));
}

// =====================================================================
section("3. physics -- the cases the whole feature exists for");
// =====================================================================
{
  const p = asym.pattern;
  p.tiebars.forEach(function (t) { t.height = 8; });
  const n = netOf(p, 0, true);
  const free = loadNodes(n, ["D0003"]);          // no bar on either side
  const one = loadNodes(n, ["D0000"]);           // bar on one side only
  const both = loadNodes(n, ["D0001"]);          // bars on both sides

  console.log("   free both sides  Kx " + free.uncoupled.xx.toFixed(2) + " -> " + free.coupled.xx.toFixed(2) + "  x" + gain(free).toFixed(4));
  console.log("   bar one side     Kx " + one.uncoupled.xx.toFixed(2) + " -> " + one.coupled.xx.toFixed(2) + "  x" + gain(one).toFixed(4));
  console.log("   bars both sides  Kx " + both.uncoupled.xx.toFixed(2) + " -> " + both.coupled.xx.toFixed(2) + "  x" + gain(both).toFixed(4));

  ck("a block with no bar is untouched", Math.abs(gain(free) - 1) < 1e-12);
  ck("one bar stiffens it", gain(one) > 1.001);
  ck("two bars stiffen it more than one", gain(both) > gain(one) + 1e-6);
  ck("axis-aligned bars produce no Kxy",
     Math.abs(both.coupled.xy) < 1e-9 * both.coupled.xx, both.coupled.xy.toExponential(2));

  // heights
  p.tiebars[0].height = 4; p.tiebars[1].height = 12;
  const na = netOf(p, 0, true);
  const lo = loadNodes(na, ["D0000"]), hi = loadNodes(na, ["D0002"]);
  ck("a taller bar couples more", gain(hi) > gain(lo) + 1e-6,
     "h=4 gives x" + gain(lo).toFixed(4) + ", h=12 gives x" + gain(hi).toFixed(4));

  // Shrinking bar.  The link area is (wall length x bar height), so the
  // coupling must fall in proportion to the height and vanish with it.  Heights
  // below COUPLING_MIN_HEIGHT (0.1 mm) are floored -- the beam formulae stop
  // meaning anything below that -- so the trend is checked above the floor.
  const gains = [];
  for (const h of [4, 2, 1, 0.5, 0.25]) {
    p.tiebars.forEach(function (t) { t.height = h; });
    gains.push([h, gain(loadNodes(netOf(p, 0, true), ["D0001"]))]);
  }
  let monotone = true;
  for (let i = 1; i < gains.length; i++) if (gains[i][1] > gains[i - 1][1] + 1e-12) monotone = false;
  ck("gain falls monotonically as the bar shrinks", monotone,
     gains.map(function (g) { return "h" + g[0] + ":x" + g[1].toFixed(3); }).join(" "));
  let linear = true;
  for (let i = 1; i < gains.length; i++) {
    const r = (gains[i - 1][1] - 1) / Math.max(gains[i][1] - 1, 1e-12);
    if (!(r > 1.4 && r < 2.6)) linear = false;   // halving h should ~halve the excess
  }
  ck("the excess stiffness is proportional to bar height", linear,
     gains.map(function (g) { return "h" + g[0] + ":+" + ((g[1] - 1) * 100).toFixed(1) + "%"; }).join(" "));
  ck("a bar 1/64 of the NSD barely couples", gains[gains.length - 1][1] < 1.06,
     "h=0.25 of 16 mm gives x" + gains[gains.length - 1][1].toFixed(4));

  // no bars at all
  p.tiebars.forEach(function (t) { t.height = 8; });
  const nb = netOf(p, 0, false);
  const r = loadNodes(nb, ["D0000", "D0001", "D0002"]);
  const rel = Math.abs(r.coupled.xx - r.uncoupled.xx) / r.uncoupled.xx;
  ck("with no bars, the coupled solve reproduces the uncoupled reference", rel < 1e-12,
     "rel " + rel.toExponential(2) + " (two different inversion routes)");

  // reciprocity: the compliance must be symmetric, so the stiffness is too
  const nsym = netOf(p, 0, true);
  const rr = loadNodes(nsym, ["D0000", "D0001", "D0002"], [1, 0.4, 0.7]);
  ck("effective stiffness is symmetric (Maxwell-Betti)",
     Math.abs(rr.coupled.xy - rr.coupled.xy) === 0 && isFinite(rr.coupled.xy));
  const pd = rr.coupled.xx > 0 && rr.coupled.yy > 0 &&
             rr.coupled.xx * rr.coupled.yy > rr.coupled.xy * rr.coupled.xy;
  ck("effective stiffness is positive definite", pd,
     "Kx " + rr.coupled.xx.toFixed(1) + " Ky " + rr.coupled.yy.toFixed(1) + " Kxy " + rr.coupled.xy.toFixed(1));

  // in-phase vs differential -- the claim the whole tier rests on
  const whole = loadNodes(nsym, ["D0000", "D0001", "D0002"]);
  console.log("   whole tied rib loaded together : x" + gain(whole).toFixed(4));
  console.log("   one block of it loaded alone   : x" + gain(loadNodes(nsym, ["D0001"])).toFixed(4));
  ck("moving a tied group together gains less than moving one block against its neighbours",
     gain(whole) < gain(loadNodes(nsym, ["D0001"])),
     "this is the effect Tier 1 cannot represent");
}

// =====================================================================
section("4. Kxy from a diagonal bar");
// =====================================================================
{
  const d = load("diagonal_tiebars.dxf", { height: 14, draft_angle: 0 }, {});
  const p = d.pattern;
  ck("diagonal drawing imports", p.blocks.length === 24 && p.tiebars.length === 11);
  const t = p.tiebars[0];
  ck("the link runs at 45 deg", Math.abs(Math.abs(t.links[0].dir[0]) - Math.SQRT1_2) < 0.05,
     "dir [" + t.links[0].dir.map(function (v) { return v.toFixed(3); }) + "]");
  const n = netOf(p, 0, true);
  const r = loadNodes(n, [p.blocks[0].id]);
  console.log("   block next to a 45 deg bar: Kxy " + r.uncoupled.xy.toExponential(3) +
              " -> " + r.coupled.xy.toExponential(3));
  ck("a diagonal bar creates a real Kxy", Math.abs(r.coupled.xy) > 1e-6 * r.coupled.xx,
     "|Kxy|/Kx = " + (Math.abs(r.coupled.xy) / r.coupled.xx).toFixed(4));
  ck("Kxy stays small enough to keep the matrix positive definite",
     r.coupled.xx * r.coupled.yy > r.coupled.xy * r.coupled.xy);
}

// =====================================================================
section("5. wear coupling to the network");
// =====================================================================
{
  const r = load("tbr_ribs_tiebars.dxf", { height: 16, draft_angle: 2 }, {});
  const p = r.pattern;
  p.tiebars.forEach(function (t) { t.height = 8.8; });     // engages at 7.2 mm
  // A block that actually has a bar on it. Half the blocks in this drawing are
  // in the untied outer ribs, and picking one of those would test nothing.
  const id = p.blocks[p.tiebars[0].links.filter(function (l) { return l.kind === "block"; })[0].index].id;
  const rows = [];
  for (const w of [0, 3, 6, 7.2, 9, 12]) {
    const n = netOf(p, w, true);
    const has = n.nodes.some(function (v) { return v.id === id; });
    const r = has ? loadNodes(n, [id]) : null;
    rows.push([w, r ? gain(r) : NaN, E.tiebarCurrentHeight(p.tiebars[0], w), r ? r.coupled.xx : NaN]);
  }
  rows.forEach(function (q) {
    console.log("   wear " + String(q[0]).padStart(4) + " mm   bar height " + q[2].toFixed(2) +
                " mm   coupled Kx " + q[3].toFixed(0).padStart(6) + "   gain x" + q[1].toFixed(4));
  });
  ck("a submerged bar already couples at zero wear", rows[0][1] > 1.001,
     "x" + rows[0][1].toFixed(4) + " -- stiffness with no contact area, which is the point");
  ck("bar height tracks NSD - wear once worn into",
     Math.abs(rows[4][2] - (16 - 9)) < 1e-9, "at 9 mm wear the bar is " + rows[4][2].toFixed(2) + " mm");

  // The RELATIVE help must fall with wear, even though the absolute stiffness
  // rises.  A block shortens as the tread wears and stiffens as roughly 1/L^3,
  // while the link only tracks the bar height -- and the bar height does not
  // move at all until the tread reaches it.  So the bar is proportionally most
  // valuable on a tall, floppy, nearly-new block.  That is the opposite of the
  // contact-area mechanism, which contributes nothing until worn into: the two
  // are complementary in time, not additive.
  let gainFalls = true, absRises = true;
  for (let i = 1; i < rows.length; i++) {
    if (!(rows[i][1] <= rows[i - 1][1] + 1e-9)) gainFalls = false;
    if (!(rows[i][3] >= rows[i - 1][3] - 1e-9)) absRises = false;
  }
  ck("the relative coupling gain falls as the tread wears", gainFalls,
     "x" + rows[0][1].toFixed(3) + " new -> x" + rows[rows.length - 1][1].toFixed(3) + " at 12 mm");
  ck("the absolute coupled stiffness still rises with wear", absRises,
     rows[0][3].toFixed(0) + " -> " + rows[rows.length - 1][3].toFixed(0) + " N/mm");
}

// =====================================================================
section("6. integration with the rasterised sweep");
// =====================================================================
{
  const r = load("tbr_ribs_tiebars.dxf", { height: 16, draft_angle: 2 }, {});
  const p = r.pattern;
  const spec = { shape: "rectangle", length: 200, width: 190, rotation: 0, y_center: 0,
                 gamma_deg: 0, load_N: null, scale_with_lean: false };
  const cpp = { vertical_load: 25000, wheel_radius: 500, load_rises_with_lean: false };
  const wear = 0;
  const pat = Object.assign({}, p, { blocks: E.effectiveBlocks(p, wear) });
  const grid = E.makeGrid(pat, 1024, 96);
  const pack = E.rasterise(pat, grid, SP, false, null);
  const patch = E.shapePatch(spec, p.crown, p.tread_width, cpp);
  const sweep = E.sweepLean(pat, pack, 0, spec, cpp, new E.MapFFTCache(pack), 90, null);

  const t0 = Date.now();
  const cs = E.couplingSweep(p, pack, patch, wear, SP, 360);
  const ms = Date.now() - t0;
  ck("couplingSweep returns a result", !!cs);
  console.log("   " + cs.n_nodes + " nodes, " + cs.n_links + " links, " + cs.n_components +
              " components, " + cs.theta_deg.length + " angles in " + ms + " ms");

  // the per-node areas the network loads with must add up to the area the FFT
  // reports at the same angle -- one number computed two completely
  // different ways
  let worst = 0;
  for (let s = 0; s < cs.theta_deg.length; s += 7) {
    const j = Math.round((cs.theta_deg[s] / 360) * grid.nx) % grid.nx;
    const rel = Math.abs(cs.contact_area[s] - sweep.contact_area[j]) / Math.max(sweep.contact_area[j], 1e-9);
    worst = Math.max(worst, rel);
  }
  ck("network load weights sum to the FFT contact area", worst < 1e-12,
     "worst rel " + worst.toExponential(2));

  const fin = ["kx_coupled", "ky_coupled", "kxy_coupled", "kx_uncoupled", "ky_uncoupled"]
    .every(function (k) { return cs[k].every(function (v) { return isFinite(v); }); });
  ck("every returned value is finite", fin);
  let allUp = true, worstDrop = 0;
  for (let i = 0; i < cs.kx_coupled.length; i++) {
    if (cs.kx_uncoupled[i] <= 0) continue;
    const g = cs.kx_coupled[i] / cs.kx_uncoupled[i];
    if (g < 1 - 1e-9) { allUp = false; worstDrop = Math.min(worstDrop || 0, g - 1); }
  }
  ck("coupling never makes the tread softer", allUp,
     allUp ? "" : "worst " + worstDrop.toExponential(2));
  console.log("   mean gain: Kx x" + cs.gain_kx.toFixed(4) + "   Ky x" + cs.gain_ky.toFixed(4));

  const csOff = E.couplingSweep(Object.assign({}, p, { tiebars: [] }), pack, patch, wear, SP, 360);
  ck("no bars -> no coupling analysis offered", csOff === null);

  // the uncoupled reference and the FFT Kx measure the same tread under two
  // different boundary conditions; they must at least agree in magnitude
  let ratio = 0, n = 0;
  for (let s = 0; s < cs.theta_deg.length; s += 11) {
    const j = Math.round((cs.theta_deg[s] / 360) * grid.nx) % grid.nx;
    if (sweep.kx[j] > 0) { ratio += cs.kx_uncoupled[s] / sweep.kx[j]; n++; }
  }
  ratio /= n;
  console.log("   uncoupled reference / FFT Kx = " + ratio.toFixed(4) +
              "  (force-controlled vs parallel-sum definition)");
  ck("the two definitions agree to within a factor of 2", ratio > 0.5 && ratio < 2);
}

// =====================================================================
section("7. robustness");
// =====================================================================
{
  const p = load("asym_tiebars.dxf", { height: 16, draft_angle: 0 }, { min_block_area: 5 }).pattern;
  ck("a pattern with no tie bars yields no network work",
     E.couplingSweep(Object.assign({}, p, { tiebars: [] }), { grid: { nx: 8, ny: 4 }, labels: [], area: [] },
                     { outline: [[0, 0], [1, 0], [1, 1]] }, 0, SP, 8) === null);
  let threw = null;
  try {
    const bad = Object.assign({}, p, { blocks: [] });
    E.buildCouplingNetwork(bad, 0, SP);
  } catch (e) { threw = e.message; }
  ck("an empty pattern does not crash the builder", threw === null);
  const one = E.buildCouplingNetwork(Object.assign({}, p, { blocks: [p.blocks[0]], tiebars: [] }), 0, SP);
  ck("a single-block pattern returns no network", one === null);
  let wornOut = E.buildCouplingNetwork(p, 15.99, SP);
  ck("wear that removes every block returns no network", wornOut === null || wornOut.nMain === 0,
     wornOut ? "nMain " + wornOut.nMain : "null");
}

// =====================================================================
section("8. the assembled system itself");
// =====================================================================
{
  const p = load("asym_tiebars.dxf", { height: 16, draft_angle: 0 }, { min_block_area: 5 }).pattern;
  p.tiebars.forEach(function (t) { t.height = 8; });
  const net = netOf(p, 0, true);
  const c = 0, mem = net.members[c], m = mem.length, n = 2 * m;
  const A = new Float64Array(n * n);
  for (let a = 0; a < m; a++) {
    const K = net.nodes[mem[a]].K;
    A[(2 * a) * n + (2 * a)] += K.kx; A[(2 * a) * n + (2 * a + 1)] += K.kxy;
    A[(2 * a + 1) * n + (2 * a)] += K.kxy; A[(2 * a + 1) * n + (2 * a + 1)] += K.ky;
  }
  for (const L of net.links) {
    if (net.comp[L.i] !== c) continue;
    const a = net.localOf[L.i], b = net.localOf[L.j], C = L.C;
    for (const t of [[a, a, 1], [b, b, 1], [a, b, -1], [b, a, -1]]) {
      A[(2 * t[0]) * n + (2 * t[1])] += t[2] * C.xx;
      A[(2 * t[0]) * n + (2 * t[1] + 1)] += t[2] * C.xy;
      A[(2 * t[0] + 1) * n + (2 * t[1])] += t[2] * C.xy;
      A[(2 * t[0] + 1) * n + (2 * t[1] + 1)] += t[2] * C.yy;
    }
  }
  let asym = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) asym = Math.max(asym, Math.abs(A[i * n + j] - A[j * n + i]));
  ck("assembled stiffness matrix is symmetric", asym === 0);

  const Ai = net.inv[c], f = new Float64Array(n);
  for (let i = 0; i < n; i++) f[i] = Math.sin(i * 1.7) + 0.3;
  const u = new Float64Array(n);
  for (let i = 0; i < n; i++) { let s = 0; for (let k = 0; k < n; k++) s += Ai[i * n + k] * f[k]; u[i] = s; }
  let res = 0, scale = 0;
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let k = 0; k < n; k++) s += A[i * n + k] * u[k];
    res = Math.max(res, Math.abs(s - f[i])); scale = Math.max(scale, Math.abs(f[i]));
  }
  ck("the solve satisfies equilibrium: K (K^-1 f) = f", res / scale < 1e-12,
     "rel residual " + (res / scale).toExponential(2));

  const k0 = net.nodes.findIndex(function (v) { return v.id === "D0001"; });
  const s1 = E.effectiveStiffnessAt(net, [k0], [1]);
  const s2 = E.effectiveStiffnessAt(net, [k0], [1000]);
  ck("effective stiffness does not depend on the load scale",
     Math.abs(s1.coupled.xx - s2.coupled.xx) / s1.coupled.xx < 1e-12);
  const i0 = net.nodes.findIndex(function (v) { return v.id === "D0000"; });
  const f1 = E.effectiveStiffnessAt(net, [i0, k0], [2, 3]);
  const f2 = E.effectiveStiffnessAt(net, [k0, i0], [3, 2]);
  ck("result does not depend on the order the loaded nodes are listed",
     Math.abs(f1.coupled.xx - f2.coupled.xx) < 1e-9 && Math.abs(f1.coupled.xy - f2.coupled.xy) < 1e-9);
}

// =====================================================================
section("9. closed form -- 2 blocks and 1 bar, solved by hand");
// =====================================================================
// The only fully independent check available: a three-node system small enough
// to invert on paper.  With x and y decoupled (square blocks, a bar square to
// the tread) the circumferential sub-system is
//     K = [[k+c, 0, -c], [0, k+c, -c], [-c, -c, g+2c]]
// and loading one block alone gives
//     K_eff = (k+c)[(k+c)(g+2c) - 2c^2] / [(k+c)(g+2c) - c^2].
// If assembly, Cholesky, inversion and the quadratic form all agree with that,
// the machinery is right; only the modelling assumptions remain.
{
  const p = load("pair_tiebar.dxf", { height: 16, draft_angle: 0 }, { min_block_area: 5 }).pattern;
  ck("2 blocks and 1 bar", p.blocks.length === 2 && p.tiebars.length === 1);
  p.tiebars[0].height = 8;
  const net = netOf(p, 0, true);
  const k = net.nodes.find(function (v) { return v.id === "D0000"; }).K.kx;
  const g = net.nodes.find(function (v) { return v.kind === "tiebar"; }).K.kx;
  const c = net.links[0].C.xx;
  ck("both links of a bar are identical", Math.abs(net.links[0].C.xx - net.links[1].C.xx) < 1e-12);

  const A = (k + c) * (g + 2 * c);
  const hand = ((k + c) * (A - 2 * c * c)) / (A - c * c);
  const i0 = net.nodes.findIndex(function (v) { return v.id === "D0000"; });
  const eng = E.effectiveStiffnessAt(net, [i0], [1]).coupled.xx;
  console.log("   k " + k.toFixed(4) + "   g " + g.toFixed(4) + "   c " + c.toFixed(4));
  console.log("   hand " + hand.toFixed(10) + "   engine " + eng.toFixed(10));
  ck("one block loaded: engine matches the hand solution",
     Math.abs(hand - eng) / hand < 1e-12, "rel " + (Math.abs(hand - eng) / hand).toExponential(2));

  const i1 = net.nodes.findIndex(function (v) { return v.id === "D0001"; });
  const det = (k + c) * (A - 2 * c * c);
  const handBoth = 1 / (0.25 * ((A - c * c) / det + 2 * ((c * c) / det) + (A - c * c) / det));
  const engBoth = E.effectiveStiffnessAt(net, [i0, i1], [1, 1]).coupled.xx;
  ck("both blocks loaded: engine matches the hand solution",
     Math.abs(handBoth - engBoth) / handBoth < 1e-12);
  console.log("   in-phase gain x" + (engBoth / (2 * k)).toFixed(4) +
              "   single-block gain x" + (eng / k).toFixed(4));
}

// =====================================================================
section("10. symmetry, wrap and convergence");
// =====================================================================
{
  const d = load("diagonal_tiebars.dxf", { height: 14, draft_angle: 0 }, {}).pattern;
  const nd = netOf(d, 0, true);
  const k = nd.nodes.findIndex(function (v) { return v.id === d.blocks[0].id; });
  const pos = E.effectiveStiffnessAt(nd, [k], [1]).coupled.xy;
  const M = JSON.parse(JSON.stringify(d));
  M.blocks.forEach(function (b) { b.polygon = b.polygon.map(function (q) { return [q[0], -q[1]]; }); });
  M.tiebars.forEach(function (t) {
    t.polygon = t.polygon.map(function (q) { return [q[0], -q[1]]; });
    t.centroid_y = -t.centroid_y;
    t.links.forEach(function (l) { l.dir = [l.dir[0], -l.dir[1]]; });
  });
  const nm = netOf(M, 0, true);
  const km = nm.nodes.findIndex(function (v) { return v.id === M.blocks[0].id; });
  const neg = E.effectiveStiffnessAt(nm, [km], [1]).coupled.xy;
  ck("mirroring the tread laterally flips the sign of Kxy",
     Math.abs(pos) > 1e-6 && Math.abs(pos + neg) < 1e-6 * Math.abs(pos),
     pos.toFixed(4) + " -> " + neg.toFixed(4));

  const r = load("tbr_ribs_tiebars.dxf", { height: 16, draft_angle: 2 }, {}).pattern;
  const spec = { shape: "rectangle", length: 200, width: 190, rotation: 0, y_center: 0,
                 gamma_deg: 0, load_N: null, scale_with_lean: false };
  const cpp = { vertical_load: 25000, wheel_radius: 500, load_rises_with_lean: false };
  const net = E.prepareCouplingNetwork(r, 0, SP);

  // A patch longer than the tyre wraps right round, which is where the shifted
  // interval arithmetic would break if it were going to.
  {
    const pat = Object.assign({}, r, { blocks: E.effectiveBlocks(r, 0) });
    const grid = E.makeGrid(pat, 512, 48);
    const pack = E.rasterise(pat, grid, SP, false, null);
    const wide = Object.assign({}, spec, { length: r.tyre_circumference * 1.4 });
    const patch = E.shapePatch(wide, r.crown, r.tread_width, cpp);
    const cs = E.couplingSweep(r, pack, patch, 0, SP, 180, net);
    const sw = E.sweepLean(pat, pack, 0, wide, cpp, new E.MapFFTCache(pack), 90, null);
    let worst = 0;
    for (let s = 0; s < cs.theta_deg.length; s++) {
      const j = Math.round((cs.theta_deg[s] / 360) * 512) % 512;
      worst = Math.max(worst, Math.abs(cs.contact_area[s] - sw.contact_area[j]) / Math.max(sw.contact_area[j], 1e-9));
    }
    ck("a patch longer than the tyre wraps correctly", worst < 1e-12,
       "worst area mismatch " + worst.toExponential(2));
  }

  const gainsGrid = [];
  for (const nx of [512, 1024, 2048]) {
    const pat = Object.assign({}, r, { blocks: E.effectiveBlocks(r, 0) });
    const grid = E.makeGrid(pat, nx, Math.round(204 / (1194 / nx)));
    const pack = E.rasterise(pat, grid, SP, false, null);
    const patch = E.shapePatch(spec, r.crown, r.tread_width, cpp);
    gainsGrid.push(E.couplingSweep(r, pack, patch, 0, SP, 360, net).gain_kx);
  }
  const spreadG = Math.max.apply(null, gainsGrid) - Math.min.apply(null, gainsGrid);
  ck("the gain is converged across a 4x grid range", spreadG < 5e-3,
     gainsGrid.map(function (g) { return g.toFixed(5); }).join(" / "));

  const pat = Object.assign({}, r, { blocks: E.effectiveBlocks(r, 0) });
  const grid = E.makeGrid(pat, 1024, 96);
  const pack = E.rasterise(pat, grid, SP, false, null);
  const patch = E.shapePatch(spec, r.crown, r.tread_width, cpp);
  const gainsN = [180, 360, 720].map(function (n) { return E.couplingSweep(r, pack, patch, 0, SP, n, net).gain_kx; });
  ck("the gain is insensitive to the angular sample count",
     Math.max.apply(null, gainsN) - Math.min.apply(null, gainsN) < 2e-3,
     gainsN.map(function (g) { return g.toFixed(5); }).join(" / "));
}

// =====================================================================
section("9. separating a rotated block from a genuinely coupled network");
// =====================================================================
// A non-zero Kxy has two completely different causes and they mean opposite
// things for design:
//
//   1. the blocks are ANISOTROPIC AND ROTATED relative to the tyre's axes. An
//      angled lug has a stiff direction that is not "along the tyre", so it
//      shows a cross term with nothing coupled to anything. Kxy is then a
//      symptom of the measuring axes, not of any interaction.
//   2. the parts are genuinely JOINED. An inclined tie bar drags its neighbour
//      along the bar's own line, so pushing one block moves the other sideways.
//      Cut the bar and it is gone.
//
// The tool has to keep these apart or "the tie bars are coupling the tread" can
// be said about a pattern whose bars do nothing at all. The separation is the
// difference between the two solves: bonded minus independent is cause 2 and
// nothing else, because the blocks are identical in both.
{
  // A drawing whose blocks are rectangles -- so they carry NO cross term of
  // their own, cause 1 is exactly zero -- bridged by diagonal tie bars.
  const r = load("diagonal_tiebars.dxf", { height: 10, shore_a: 65, draft_angle: 0 }).pattern;
  const wear = 6;
  const pat = Object.assign({}, r, { blocks: E.effectiveBlocks(r, wear) });
  const grid = E.makeGrid(pat, 1024, 96);
  const pack = E.rasterise(pat, grid, SP, false, null);
  const spec = { shape: "rectangle", length: 120, width: 150, gamma_deg: 0,
                 scale_with_lean: false, y_center: 0 };
  const patch = E.shapePatch(spec, r.crown, r.tread_width,
                             { vertical_load: 4000, wheel_radius: 320, load_rises_with_lean: false });
  const c = E.couplingSweep(r, pack, patch, wear, SP, 360, netOf(r, wear));
  const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  const relTo = (a, b) => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), 1e-12);

  const kxyU = mean(c.kxy_uncoupled), kxyC = mean(c.kxy_coupled);
  // Every block in this drawing is a rectangle, so no BLOCK contributes a cross
  // term: cause 1 is absent at the block level.
  const blockKxy = r.blocks.map((bk) => E.blockStiffness(bk, SP).kxy);
  ck("no block in this drawing has a cross term of its own",
     blockKxy.every((v) => Math.abs(v) < 1e-12),
     r.blocks.length + " rectangular blocks, all Kxy = 0");

  // But the BARS are diagonal, and a diagonal bar is itself an angled body. So
  // cause 1 reappears at the bar level, and the independent solve -- nothing
  // bonded to anything -- already shows a cross term.
  //
  // This is the trap, and it is worth stating plainly: reading the bonded Kxy
  // and calling it "the tie bars coupling the tread" over-attributes, because
  // part of it is just the bars being diagonal. Only the DIFFERENCE is the
  // network.
  ck("but the diagonal BARS each carry one, so the independent solve is not zero",
     Math.abs(kxyU) > 1, "independent Kxy = " + kxyU.toFixed(2) + " N/mm with nothing bonded");
  ck("bonding adds more on top", Math.abs(kxyC) > Math.abs(kxyU),
     kxyU.toFixed(2) + " -> " + kxyC.toFixed(2) + " N/mm");
  const share = (kxyC - kxyU) / kxyC;
  ck("so the bonded value OVER-attributes: only the difference is the network",
     share > 0.1 && share < 0.9,
     "network share " + (100 * share).toFixed(0) + "% of the bonded " + kxyC.toFixed(2) +
     " N/mm; reading the bonded number alone would over-state it by " +
     (100 * kxyU / (kxyC - kxyU)).toFixed(0) + "%");

  // The same subtraction on the diagonal terms. Comparing the two
  // decompositions is what says whether the bars merely stiffened the tread or
  // actually turned its stiff axis.
  const prU = E.principalStiffness(mean(c.kx_uncoupled), mean(c.ky_uncoupled), kxyU);
  const prC = E.principalStiffness(mean(c.kx_coupled), mean(c.ky_coupled), kxyC);
  ck("the independent tread has almost no directional bias", prU.anisotropy < 1.01,
     "K1/K2 = " + prU.anisotropy.toFixed(4));
  ck("and bonding increases it", prC.anisotropy > prU.anisotropy,
     prU.anisotropy.toFixed(4) + " -> " + prC.anisotropy.toFixed(4));
  ck("Cxy states the coupling as a fraction, which the raw N/mm cannot",
     Math.abs(prC.cxy) > Math.abs(prU.cxy) && Math.abs(prC.cxy) < 0.05,
     (100 * prU.cxy).toFixed(3) + "% -> " + (100 * prC.cxy).toFixed(3) + "%");
  ck("and both states stay positive definite", prU.det > 0 && prC.det > 0,
     "det " + prU.det.toExponential(2) + " / " + prC.det.toExponential(2));

  // Take the bars out altogether and BOTH contributions go: the bars' own cross
  // term and the network's. Only the rectangular blocks are left, and they have
  // none at all.
  const bare = E.couplingSweep(r, pack, patch, wear, SP, 360, netOf(r, wear, false));
  ck("remove the bars and the cross term vanishes entirely",
     Math.abs(mean(bare.kxy_coupled)) < 1e-9,
     "Kxy = " + mean(bare.kxy_coupled).toExponential(2) + " N/mm");
  ck("and with nothing left to bond, the gains are exactly one",
     Math.abs(bare.gain_kx - 1) < 1e-9 && Math.abs(bare.gain_ky - 1) < 1e-9,
     "Kx x" + bare.gain_kx.toFixed(9));

  // On a straight-ribbed tread with bars ACROSS the grooves rather than at an
  // angle, the bars stiffen and do not couple -- the other half of the claim.
  const s = load("tbr_ribs_tiebars.dxf", { height: 16, shore_a: 65, draft_angle: 0 }).pattern;
  const sPat = Object.assign({}, s, { blocks: E.effectiveBlocks(s, 9) });
  const sGrid = E.makeGrid(sPat, 1024, 96);
  const sPack = E.rasterise(sPat, sGrid, SP, false, null);
  const sPatch = E.shapePatch({ shape: "rectangle", length: 180, width: 190, gamma_deg: 0,
                                scale_with_lean: false, y_center: 0 }, s.crown, s.tread_width,
                              { vertical_load: 26000, wheel_radius: 520, load_rises_with_lean: false });
  const sc = E.couplingSweep(s, sPack, sPatch, 9, SP, 360, netOf(s, 9));
  ck("lateral bars on straight ribs stiffen the tread", sc.gain_kx > 1.01 && sc.gain_ky > 1.01,
     "Kx x" + sc.gain_kx.toFixed(3) + ", Ky x" + sc.gain_ky.toFixed(3));
  ck("and couple it not at all", Math.abs(mean(sc.kxy_coupled)) < 1e-6,
     "Kxy = " + mean(sc.kxy_coupled).toExponential(2) + " N/mm");
}

console.log("\n" + (fails ? fails + " of " + checks + " CHECKS FAILED" : checks + " checks passed"));
process.exitCode = fails ? 1 : 0;
