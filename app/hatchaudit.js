/* Audit of HATCH tie-bar import, hole-aware geometry and the DXF round trip.
 *
 * Three things get checked here, and each one can be wrong quietly:
 *
 *   1. A region with a hole. Its area, centroid and second moments have closed
 *      forms; its stiffness must fall, its shape factor must fall, and with no
 *      holes it must reduce EXACTLY to the solid-polygon path that every
 *      existing number was computed with.
 *   2. Reading a HATCH. Both boundary styles, arc and ellipse edges, nesting,
 *      colours by entity / by layer / by true colour, and INSERT expansion.
 *   3. What the importer does with them: merging with the automatic detector,
 *      linking to blocks, riding the pitch replication, and coming back
 *      unchanged through patternToDxf.
 *
 * Run:  node app/hatchaudit.js
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

const DEF = { height: 12, shore_a: 60, draft_angle: 3 };
const PARAMS = { poisson: 0.49, mode: "cantilever", shore_a: 60, n_slices: 40, bulk_modulus: 1100 };
const load = (file, opts) =>
  E.loadPattern(fs.readFileSync(path.join(DATA, file), "utf8"), DEF, opts || {});
const read = (file) => E.readDxfEntities(fs.readFileSync(path.join(DATA, file), "utf8"));

const rect = (w, h, cx, cy) => [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
                                [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]];
const hole = (w, h, cx, cy) => rect(w, h, cx, cy).slice().reverse();

// ---------------------------------------------------------------------------
section("1. a region with a hole, against the closed form");
// ---------------------------------------------------------------------------
{
  const W = 40, H = 30, hw = 10, hh = 10;
  const outer = rect(W, H, 0, 0);
  const p = E.regionProps(outer, [hole(hw, hh, 0, 0)]);
  ck("area is outer minus hole", rel(p.A, W * H - hw * hh) < 1e-12,
     p.A + " vs " + (W * H - hw * hh));
  ck("a concentric hole leaves the centroid alone",
     Math.abs(p.cx) < 1e-12 && Math.abs(p.cy) < 1e-12);
  ck("Ixx = bh^3/12 of each, subtracted",
     rel(p.Ixx, W * H * H * H / 12 - hw * hh * hh * hh / 12) < 1e-12, String(p.Ixx));
  ck("Iyy = hb^3/12 of each, subtracted",
     rel(p.Iyy, H * W * W * W / 12 - hw * hh * hh * hh / 12) < 1e-12, String(p.Iyy));
  ck("a hole ADDS perimeter -- its wall is a free surface",
     rel(p.perimeter, 2 * (W + H) + 4 * hw) < 1e-12, p.perimeter + " mm");

  // Off-centre: the parallel-axis term is where this goes wrong if it goes
  // wrong at all, so it is computed by hand here and compared.
  const q = E.regionProps(outer, [hole(hw, hh, 10, 0)]);
  const A = W * H - hw * hh, cx = (W * H * 0 - hw * hh * 10) / A;
  const IyyO = H * W * W * W / 12 - (hw * hh * hh * hh / 12 + hw * hh * 100);
  ck("an off-centre hole moves the centroid the right way",
     rel(q.cx, cx) < 1e-12, q.cx.toFixed(9) + " vs " + cx.toFixed(9));
  ck("and Iyy follows the parallel-axis theorem",
     rel(q.Iyy, IyyO - A * cx * cx) < 1e-12, q.Iyy.toFixed(6));

  // The reduction that protects every number the tool already produced.
  const a = E.regionProps(outer, []), b = E.polygonProps(outer);
  ck("with no holes the region path IS the polygon path, bit for bit",
     ["A", "cx", "cy", "Ixx", "Iyy", "Ixy", "perimeter"].every((k) => a[k] === b[k]));
  ck("regionArea with no holes equals polygonArea",
     E.regionArea(outer, []) === E.polygonArea(outer));
  ck("regionCentroid with no holes equals polygonCentroid",
     E.regionCentroid(outer, []).join() === E.polygonCentroid(outer).join());
}

// ---------------------------------------------------------------------------
section("2. stiffness of a region with a hole");
// ---------------------------------------------------------------------------
{
  const outer = rect(40, 30, 0, 0), h = [hole(10, 10, 0, 0)];
  const solid = E.effectiveK(outer, 8, 6.89, 0.49, 0, "cantilever", [], 40, "layered");
  const viaRegion = E.effectiveKRegion(outer, [], 8, 6.89, 0.49, 0, "cantilever", 40);
  ck("no holes: the region solver reproduces effectiveK exactly",
     solid.Kx === viaRegion.Kx && solid.Ky === viaRegion.Ky, solid.Kx.toFixed(9));
  const kzSolid = E.computeKz(outer, 8, 6.89, 0.64, [], 1100);
  const kzRegion = E.computeKzRegion(outer, [], 8, 6.89, 0.64, [], 1100);
  ck("no holes: computeKzRegion reproduces computeKz exactly",
     kzSolid.Kz === kzRegion.Kz && kzSolid.S === kzRegion.S, kzSolid.Kz.toFixed(6));

  const holed = E.effectiveKRegion(outer, h, 8, 6.89, 0.49, 0, "cantilever", 40);
  ck("a hole makes the block less stiff in shear", holed.Kx < viaRegion.Kx,
     viaRegion.Kx.toFixed(2) + " -> " + holed.Kx.toFixed(2) + " N/mm");
  const kzHoled = E.computeKzRegion(outer, h, 8, 6.89, 0.64, [], 1100);
  ck("and less stiff vertically, by more than area alone", kzHoled.Kz < kzRegion.Kz,
     kzRegion.Kz.toFixed(1) + " -> " + kzHoled.Kz.toFixed(1) + " N/mm");
  ck("because the hole cuts the shape factor too", kzHoled.S < kzRegion.S,
     kzRegion.S.toFixed(4) + " -> " + kzHoled.S.toFixed(4));

  // Draft closes a hole toward the base while it opens the outside, so a
  // drafted holed block is stiffer than the same block with no draft.
  const drafted = E.effectiveKRegion(outer, h, 8, 6.89, 0.49, 3, "cantilever", 40);
  ck("mould draft tapers the hole the other way and stiffens the block",
     drafted.Kx > holed.Kx, holed.Kx.toFixed(2) + " -> " + drafted.Kx.toFixed(2));

  // blockStiffness must dispatch on holes and on nothing else.
  const bs0 = E.blockStiffness({ polygon: outer, height: 8, draft_angle: 0 }, PARAMS);
  const bs1 = E.blockStiffness({ polygon: outer, holes: [], height: 8, draft_angle: 0 }, PARAMS);
  const bs2 = E.blockStiffness({ polygon: outer, holes: h, height: 8, draft_angle: 0 }, PARAMS);
  ck("an empty holes array is the same as no holes array",
     bs0.kx === bs1.kx && bs0.kz === bs1.kz && bs0.area === bs1.area);
  ck("a holed block reports its NET area", rel(bs2.area, 40 * 30 - 100) < 1e-12,
     bs2.area + " mm²");
  ck("and its stiffness is lower than the solid one", bs2.kz < bs0.kz && bs2.kx < bs0.kx);
}

// ---------------------------------------------------------------------------
section("3. the raster punches the hole out too");
// ---------------------------------------------------------------------------
{
  const blk = {
    id: "D0", zone: "center", height: 8, draft_angle: 0, sipes: [], n_lateral_sipes: 0,
    polygon: [[20, -15], [60, -15], [60, 15], [20, 15]],
    holes: [[[35, -5], [35, 5], [45, 5], [45, -5]]],
  };
  const base = { tyre_circumference: 100, tread_width: 60, pitches: [], tiebars: [],
                 crown: E.buildCrown(60, { tyre_class: "2w" }) };
  const grid = E.makeGrid(Object.assign({ blocks: [blk] }, base), 2048, 200);
  const sum = (pat) => {
    const r = E.rasterise(pat, grid, PARAMS, false, {});
    let a = 0; for (let i = 0; i < r.area.length; i++) a += r.area[i];
    return a;
  };
  const withHole = sum(Object.assign({ blocks: [blk] }, base));
  const noHole = sum(Object.assign({ blocks: [Object.assign({}, blk, { holes: [] })] }, base));
  ck("the raster loses the hole's area, to within one pixel row",
     Math.abs(withHole - (40 * 30 - 100)) < 5, withHole.toFixed(2) + " vs 1100 mm²");
  ck("and the same block without the hole rasters solid",
     Math.abs(noHole - 40 * 30) < 5, noHole.toFixed(2) + " vs 1200 mm²");
  ck("the difference is the hole", Math.abs((noHole - withHole) - 100) < 5,
     (noHole - withHole).toFixed(2) + " mm²");
}

// ---------------------------------------------------------------------------
section("4. reading a HATCH out of a DXF");
// ---------------------------------------------------------------------------
{
  const stats = {};
  const hs = E.tiebarHatches(read("hatch_only.dxf"), 0.02, stats);
  ck("every hatch on the TIEBAR layer is found", hs.length === 12, hs.length + " regions");
  ck("both boundary styles are read -- polyline and edge list",
     hs.filter((r) => Math.abs(E.polygonArea(r.polygon) - 192) < 1e-9).length === 12);
  ck("holes are separated from the outer boundary",
     hs.reduce((s, r) => s + r.holes.length, 0) === 3, stats.tiebar_hatch_holes + " holes");
  ck("a hatched bar's net area is outer minus hole",
     hs.filter((r) => r.holes.length).every((r) => rel(E.regionArea(r.polygon, r.holes), 168) < 1e-9));
  ck("a hole is wound opposite to its outer boundary",
     hs.filter((r) => r.holes.length).every((r) => {
       const s = (poly) => { let a = 0; for (let i = 0; i < poly.length; i++) { const p = poly[i], q = poly[(i + 1) % poly.length]; a += p[0] * q[1] - q[0] * p[1]; } return a; };
       return s(r.polygon) > 0 && s(r.holes[0]) < 0;
     }));

  // An arc edge carries its own direction flag; read it wrong and the loop
  // crosses itself, which shows up immediately as a collapsed area.
  const arc = E.tiebarHatches(read("hatch_arc.dxf"), 0.02, {});
  const stadium = 16 * 8 + Math.PI * 16;
  ck("an arc-edge boundary closes into the right shape",
     arc.length === 1 && rel(E.polygonArea(arc[0].polygon), stadium) < 3e-3,
     E.polygonArea(arc[0].polygon).toFixed(3) + " vs " + stadium.toFixed(3) + " mm²");

  // Colour: entity true colour beats entity ACI beats the layer.
  const css = hs.map((r) => r.color.css);
  ck("a true-colour hatch keeps its exact 24-bit colour",
     css.indexOf("#1e90ff") >= 0, "#1e90ff");
  ck("an ACI hatch resolves through the colour index", css.indexOf("#00ff00") >= 0, "ACI 3 -> #00ff00");
  ck("a hatch with no colour of its own inherits the TIEBAR layer's",
     css.indexOf("#ff00ff") >= 0 &&
     hs.filter((r) => r.color.css === "#ff00ff").every((r) => r.color.source === "TIEBAR layer"));
  ck("ACI 7 is white and 250-255 are the grey ramp",
     E.aciColorCss(7) === "#ffffff" && E.aciColorCss(250) === "#333333" && E.aciColorCss(255) === "#ffffff");
  ck("a true colour converts to CSS by its bytes",
     E.dxfTrueColorCss(0x1E90FF) === "#1e90ff" && E.dxfTrueColorCss(0) === "#000000");
}

// ---------------------------------------------------------------------------
section("5. a hatch inside a block, reached through INSERT");
// ---------------------------------------------------------------------------
{
  // Two ribs and one bar, drawn once in a BLOCK and inserted three times 100 mm
  // apart. The hatch is on layer 0 inside the definition, so it has to inherit
  // the INSERT's layer the way CAD resolves it.
  const g = [];
  const put = (c, v) => g.push(String(c), String(v));
  put(0, "SECTION"); put(2, "TABLES"); put(0, "TABLE"); put(2, "LAYER");
  put(0, "LAYER"); put(2, "TIEBAR"); put(62, 6); put(0, "ENDTAB"); put(0, "ENDSEC");
  put(0, "SECTION"); put(2, "BLOCKS");
  put(0, "BLOCK"); put(2, "PITCH"); put(10, 0); put(20, 0);
  put(0, "HATCH"); put(100, "AcDbEntity"); put(8, "0"); put(100, "AcDbHatch");
  put(2, "SOLID"); put(70, 1); put(91, 1);
  put(92, 3); put(72, 0); put(73, 1); put(93, 4);
  [[10, 0], [30, 0], [30, 8], [10, 8]].forEach((p) => { put(10, p[0]); put(20, p[1]); });
  put(97, 0);
  put(0, "ENDBLK"); put(0, "ENDSEC");
  put(0, "SECTION"); put(2, "ENTITIES");
  for (let i = 0; i < 3; i++) {
    put(0, "INSERT"); put(8, "TIEBAR"); put(2, "PITCH");
    put(10, i * 100); put(20, 0); put(41, 1); put(42, 1); put(50, 0);
  }
  put(0, "ENDSEC"); put(0, "EOF");

  const ents = E.readDxfEntities(g.join("\n") + "\n");
  const hs = E.tiebarHatches(ents, 0.02, {});
  ck("an INSERTed hatch is expanded once per reference", hs.length === 3, hs.length + " regions");
  ck("each copy lands where its INSERT put it",
     hs.map((r) => E.polygonCentroid(r.polygon)[0].toFixed(1)).join(" ") === "20.0 120.0 220.0",
     hs.map((r) => E.polygonCentroid(r.polygon)[0].toFixed(1)).join(" "));
  ck("a hatch on layer 0 inside a block inherits the INSERT's layer",
     hs.every((r) => r.layer === "TIEBAR"));
  ck("and resolves its colour through that layer",
     hs.every((r) => r.color.css === "#ff00ff"));
}

// ---------------------------------------------------------------------------
section("6. importing a drawing whose bars are hatched");
// ---------------------------------------------------------------------------
{
  // hatch_only: nothing but the hatches identifies the bars. This is the case
  // the geometric detector cannot do at all.
  const only = load("hatch_only.dxf");
  ck("the detector finds nothing on its own", only.report.n_tiebars_detected === 0);
  ck("and the hatches supply every bar", only.report.n_tiebars === 12 &&
     only.report.n_tiebars_explicit === 12, only.report.n_tiebars + " bars");
  ck("each one is marked as drawn, not inferred",
     only.pattern.tiebars.every((t) => t.source === "hatch"));
  ck("a bar hatched with a hole keeps it through the import",
     only.pattern.tiebars.filter((t) => t.holes.length).length === 3);
  ck("and reports its net area", only.pattern.tiebars.filter((t) => t.holes.length)
     .every((t) => rel(t.area, 168) < 1e-9));

  // Linking is the point of finding them: a bar that touches nothing couples
  // nothing. The hatch was drawn independently of the block outlines, so the
  // exact-endpoint match cannot work and the collinear fallback must.
  ck("every hatched bar is bonded to two blocks",
     only.pattern.tiebars.every((t) => t.links.length === 2),
     only.pattern.tiebars.filter((t) => t.links.length === 2).length + " of 12");
  ck("the bonded wall is the full length of the bar's side",
     only.pattern.tiebars.every((t) => t.links.every((l) => rel(l.wall_length, 24) < 1e-6)));
  ck("and the span is half the groove it bridges",
     only.pattern.tiebars.every((t) => t.links.every((l) => rel(l.span, 4) < 1e-6)));
  ck("the two blocks it links sit on opposite sides of the groove",
     only.pattern.tiebars.every((t) => {
       const ys = t.links.map((l) => E.polygonCentroid(only.pattern.blocks[l.index].polygon)[1]);
       return (ys[0] - t.centroid_y) * (ys[1] - t.centroid_y) < 0;
     }));

  // hatch_tiebars: the same bars are ALSO closed by linework, so both routes
  // find them and the merge must not double-count.
  const both = load("hatch_tiebars.dxf");
  ck("a bar found both ways is counted once", both.report.n_tiebars === 12,
     both.report.n_tiebars + " bars from 12 hatches + 12 detected faces");
  ck("and the merge is reported honestly", both.report.n_tiebars_merged === 12 &&
     both.report.n_tiebars_detected === 12 && both.report.n_tiebars_detected_retained === 0);
  ck("the hatched definition wins, so the holes survive the merge",
     both.pattern.tiebars.filter((t) => t.holes.length).length === 3);
  ck("the two drawings give the same tread", both.report.n_blocks === only.report.n_blocks &&
     rel(both.pattern.tyre_circumference, only.pattern.tyre_circumference) < 1e-12);
  ck("a drawing with no hatches at all is untouched",
     load("tbr_ribs_tiebars.dxf").report.n_tiebars_explicit === 0 &&
     load("tbr_ribs_tiebars.dxf").report.n_tiebars === 38);
}

// ---------------------------------------------------------------------------
section("7. hatched bars ride the pitch replication");
// ---------------------------------------------------------------------------
{
  const one = load("hatch_pitch.dxf");
  ck("one drawn pitch carries one bar", one.report.n_tiebars === 1);

  const x8 = load("hatch_pitch.dxf", { pitch: { base_length: 60, length: 60, count: 8 } });
  ck("eight pitches carry eight bars", x8.report.n_tiebars === 8, x8.report.n_tiebars + " bars");
  ck("all of them explicit", x8.report.n_tiebars_explicit === 8);
  const xs = x8.pattern.tiebars.map((t) => t.centroid_x);
  ck("one per pitch, evenly spaced",
     xs.every((v, i) => i === 0 || rel(v - xs[i - 1], 60) < 1e-9),
     xs.map((v) => v.toFixed(1)).join(" "));
  ck("every copy is the same bar",
     new Set(x8.pattern.tiebars.map((t) => t.area.toFixed(9))).size === 1,
     x8.pattern.tiebars[0].area.toFixed(3) + " mm²");
  ck("and every copy is bonded", x8.pattern.tiebars.every((t) => t.links.length === 2));

  // The two scaling conventions treat the bar differently, and both are
  // defensible -- which is exactly why the tool is told which one to use.
  const seq = { base_length: 60, sequence: [60, 70, 80] };
  const uni = load("hatch_pitch.dxf", { pitch: Object.assign({ scaling: "uniform" }, seq) });
  const grv = load("hatch_pitch.dxf", { pitch: Object.assign({ scaling: "groove_only" }, seq) });
  const uniA = uni.pattern.tiebars.map((t) => t.area);
  const grvA = grv.pattern.tiebars.map((t) => t.area);
  ck("uniform scaling stretches the bar with its pitch",
     rel(uniA[1] / uniA[0], 70 / 60) < 1e-9 && rel(uniA[2] / uniA[0], 80 / 60) < 1e-9,
     uniA.map((v) => v.toFixed(1)).join(" "));
  ck("groove-only scaling leaves it alone -- a bar is land, not void",
     new Set(grvA.map((v) => v.toFixed(9))).size === 1, grvA.map((v) => v.toFixed(1)).join(" "));
  ck("both conventions give the same circumference",
     rel(uni.pattern.tyre_circumference, 210) < 1e-9 &&
     rel(grv.pattern.tyre_circumference, 210) < 1e-9);
}

// ---------------------------------------------------------------------------
section("8. writing it back out");
// ---------------------------------------------------------------------------
{
  const a = load("hatch_only.dxf");
  const dxf = E.patternToDxf(a.pattern);
  const b = E.loadPattern(dxf, DEF, {});
  const fingerprint = (p) => JSON.stringify({
    circ: +p.tyre_circumference.toFixed(9), width: +p.tread_width.toFixed(9),
    blocks: p.blocks.length, bars: p.tiebars.length,
    land: +p.blocks.reduce((s, x) => s + E.regionArea(x.polygon, x.holes), 0).toFixed(9),
    barArea: +p.tiebars.reduce((s, x) => s + x.area, 0).toFixed(9),
    holes: p.tiebars.reduce((s, x) => s + x.holes.length, 0),
    colours: [...new Set(p.tiebars.map((x) => x.color && x.color.css))].sort().join(","),
    centroids: p.tiebars.map((t) => t.centroid_x.toFixed(6) + "," + t.centroid_y.toFixed(6)).sort().join(" "),
  });
  ck("the exported DXF re-imports to the same tread", fingerprint(a.pattern) === fingerprint(b.pattern),
     b.report.n_blocks + " blocks, " + b.report.n_tiebars + " bars");
  ck("every bar comes back as a hatch, not as a guess",
     b.report.n_tiebars_explicit === 12 && b.pattern.tiebars.every((t) => t.source === "hatch"));
  ck("holes survive the round trip",
     b.pattern.tiebars.filter((t) => t.holes.length).length === 3);
  ck("a true colour is not quantised to the nearest ACI on the way out",
     dxf.indexOf("\n420\n") >= 0 && b.pattern.tiebars.some((t) => t.color.css === "#1e90ff"));
  ck("the file names its layers", /\nTIEBAR\n/.test(dxf) && /\nTREAD\n/.test(dxf));
  ck("one HATCH entity per bar", (dxf.match(/\nHATCH\n/g) || []).length === 12);

  // A tread with no tie bars at all still writes and reads back.
  const plain = load("tbr_ribs_tiebars.dxf");
  const back = E.loadPattern(E.patternToDxf(plain.pattern), DEF, {});
  ck("a detected-bar tread also round-trips, and its bars come back explicit",
     back.report.n_blocks === plain.report.n_blocks && back.report.n_tiebars === plain.report.n_tiebars,
     back.report.n_blocks + " blocks, " + back.report.n_tiebars + " bars");
}

// ---------------------------------------------------------------------------
section("9. the sweep sees them");
// ---------------------------------------------------------------------------
{
  const p = load("hatch_only.dxf").pattern;
  const sweepArea = (wear) => {
    const worn = E.patternAtWear(p, wear);
    const grid = E.makeGrid(worn, 2048, 128);
    const r = E.rasterise(worn, grid, PARAMS, false, {});
    let a = 0; for (let i = 0; i < r.area.length; i++) a += r.area[i];
    return a;
  };
  const dry = sweepArea(0), wet = sweepArea(7);
  ck("below their engagement wear the bars carry nothing",
     E.tiebarEngaged(p.tiebars[0], 0) === false);
  ck("past it they do", E.tiebarEngaged(p.tiebars[0], 7) === true);
  ck("and engaging them adds contact area", wet > dry,
     dry.toFixed(0) + " -> " + wet.toFixed(0) + " mm²");
  // The area added is the bars' NET area, holes excluded, minus what wear took
  // off the blocks -- so the holed bars must add less than the solid ones.
  const netBars = p.tiebars.reduce((s, t) => s + t.area, 0);
  const grossBars = p.tiebars.reduce((s, t) => s + E.polygonArea(t.polygon), 0);
  ck("the bars' net area is less than their outlines", netBars < grossBars,
     netBars.toFixed(0) + " vs " + grossBars.toFixed(0) + " mm²");

  const net = E.buildCouplingNetwork(p, 7, PARAMS);
  ck("the coupling network takes them as nodes", net.nodes.length === 32 + 12,
     net.nodes.length + " nodes");
  ck("with one link per bonded wall", net.links.length === 24, net.links.length + " links");
}

console.log("\n" + (fails ? fails + " CHECK(S) FAILED of " + checks : checks + " checks passed"));
process.exit(fails ? 1 : 0);
