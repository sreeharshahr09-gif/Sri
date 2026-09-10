/* Audit of the STEP reader and the land/sea measurement it feeds.
 *
 * A land ratio read off a 3D model is a number nobody can eyeball. It looks
 * equally plausible whether the reader measured the tread or the mould, whether
 * it read millimetres or inches, and whether it silently dropped a third of the
 * faces on its way. So every number below is checked against something that was
 * worked out WITHOUT the reader:
 *
 *   1. Part 21 grammar        strings with escaped quotes and semicolons in
 *                             them, block comments, complex instances -- the
 *                             three things a regex-based reader gets wrong.
 *   2. Closed forms           a full circle by the arc integral, a cubic
 *                             B-spline through collinear controls.
 *   3. Hand-computed fixtures every land area in data/*.step was worked out on
 *                             paper by data/make_step.py and printed there.
 *   4. An independent method  the same areas again by point sampling on the
 *                             developed outlines, which shares no code with the
 *                             Green's-theorem path that produced them.
 *   5. Negative controls      checks that FAIL when the geometry is changed in
 *                             a way whose effect is known, so a check that
 *                             passes is evidence and not a tautology.
 *
 * Run:  node app/stepaudit.js
 */
"use strict";
const S = require("./stepio.js");
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
const read = (f) => S.readModel(fs.readFileSync(path.join(DATA, f), "utf8"));

// The hand-computed truth, repeated here rather than imported: a fixture and
// its expected value agreeing because they came from the same variable would
// check nothing at all.
const PLATE_LAND = 6000 - 2 * 200 - Math.PI * 25;      // 5521.4601837...
const PLATE_PERIM = 320 + 2 * 60 + 2 * Math.PI * 5;
const BLOCKS_LAND = 100 * ((60 - 4 * 4) / 5) * 5;      // 4400
const CYL_DEV = 300 * (30 * Math.PI / 180);            // 157.0796... mm of arc
const CYL_LAND = (30 + 35 + 45 + 48) * CYL_DEV;        // 24818.582...
const NSD = 8;                                          // 10 mm top, 2 mm floor

// ---------------------------------------------------------------------------
section("1. Part 21 grammar: the parts a regex gets wrong");
// ---------------------------------------------------------------------------
{
  // Every trap in one file: a semicolon inside a string, a doubled quote, a
  // block comment carrying both, a complex instance, and the $ and * slots.
  const text = [
    "ISO-10303-21;",
    "HEADER;",
    "FILE_NAME('a;b','2026-01-01',(''),(''),'','','');",
    "ENDSEC;",
    "DATA;",
    "/* a comment; with a semicolon and a #99 = FAKE(); inside it */",
    "#1 = CARTESIAN_POINT('O''Brien; and co',(0.,0.,0.));",
    "#2 = DIRECTION('',(0.,0.,1.));",
    "#3 = ( NAMED_UNIT(*) LENGTH_UNIT() SI_UNIT(.MILLI.,.METRE.) );",
    "#4 = VERTEX_POINT($,#1);",
    "ENDSEC;",
    "END-ISO-10303-21;",
  ].join("\n");
  const p = S.parse(text);
  ck("a semicolon inside a string does not end the statement",
     p.byId[1] && p.byId[1].type === "CARTESIAN_POINT");
  ck("a doubled quote becomes one apostrophe, and does not end the string",
     p.byId[1] && p.byId[1].args[0] === "O'Brien; and co",
     JSON.stringify(p.byId[1] && p.byId[1].args[0]));
  ck("the point's coordinates survive it",
     p.byId[1] && p.byId[1].args[1].length === 3);
  ck("a block comment is skipped whole, fake instances and all",
     p.byId[99] === undefined && p.order.length === 4, p.order.join(","));
  ck("a complex instance parses into its parts",
     p.byId[3] && p.byId[3].parts && p.byId[3].parts.length === 3);
  ck("and each part is reachable by name",
     p.byId[3].parts.map((q) => q.type).join(",") === "NAMED_UNIT,LENGTH_UNIT,SI_UNIT");
  ck("$ is a null slot, not a name", p.byId[4] && p.byId[4].args[0] === null);
  ck("* is a derived slot and keeps its place",
     p.byId[3].parts[0].args[0] && p.byId[3].parts[0].args[0].star === true);
  ck("enumerations keep their dotted form",
     p.byId[2].args[1].length === 3 && p.byId[3].parts[2].args[0].e === "MILLI");

  // A file that is not Part 21 at all must say so rather than measure nothing.
  let threw = "";
  try { S.parse("solid mesh\nfacet normal 0 0 1\nendsolid\n"); } catch (e) { threw = e.message; }
  ck("a non-STEP file is refused with a reason", /no DATA section/.test(threw), threw.slice(0, 40));
}

// ---------------------------------------------------------------------------
section("2. closed forms, where the reader claims to be exact");
// ---------------------------------------------------------------------------
{
  // The arc term of the contour integral, over a full turn, is twice the disc.
  // This is what makes a circular groove opening exact instead of polygonised.
  const R = 7.5;
  const twice = S.arcTwiceArea(3, -2, R, 0, 2 * Math.PI);
  ck("a full circle by the arc integral is exactly pi R^2",
     rel(twice / 2, Math.PI * R * R) < 1e-14, (twice / 2).toFixed(10));
  // Off-centre: the cx, cy terms must cancel over a closed loop, or every hole
  // away from the origin is measured wrong in a way that looks like noise.
  const off = S.arcTwiceArea(1234.5, -987.6, R, 0, 2 * Math.PI);
  ck("and does not depend on where the circle sits", rel(off, twice) < 1e-12);
  // Two halves must add up to the whole -- the property a sign error breaks.
  const h1 = S.arcTwiceArea(3, -2, R, 0, Math.PI), h2 = S.arcTwiceArea(3, -2, R, Math.PI, 2 * Math.PI);
  ck("and it is additive over a split", rel(h1 + h2, twice) < 1e-12);
  // NEGATIVE CONTROL: a quarter arc is not a full one. Without this the three
  // checks above would also pass on a function that ignored its angles.
  ck("a quarter turn is NOT the full circle (the check can fail)",
     rel(S.arcTwiceArea(3, -2, R, 0, Math.PI / 2), twice) > 0.5);

  // de Boor on a cubic Bezier: an open uniform knot vector of degree 3 with
  // four control points IS a Bezier, so the closed form is available.
  const cps = [[0, 0], [1, 4], [5, 4], [6, 0]];
  const kv = [0, 0, 0, 0, 1, 1, 1, 1];
  const bez = (t) => {
    const u = 1 - t;
    return [0, 1].map((d) => u * u * u * cps[0][d] + 3 * u * u * t * cps[1][d] +
                             3 * u * t * t * cps[2][d] + t * t * t * cps[3][d]);
  };
  let worst = 0;
  for (let i = 0; i <= 20; i++) {
    const t = i / 20, got = S.deBoor(kv, cps, null, 3, t), want = bez(t);
    worst = Math.max(worst, Math.abs(got[0] - want[0]), Math.abs(got[1] - want[1]));
  }
  ck("de Boor reproduces the cubic Bezier it reduces to", worst < 1e-12, "max err " + worst.toExponential(2));
}

// ---------------------------------------------------------------------------
section("3. the fixtures, against areas computed on paper");
// ---------------------------------------------------------------------------
const models = {};
for (const f of ["step_plate_holes", "step_blocks", "step_band_cyl",
                 "step_spline_edge", "step_inch", "step_toroidal"])
  models[f] = read(f + ".step");

function landOf(name, keys) {
  const m = models[name];
  const sel = keys || [S.suggestTread(m.groups).key];
  return S.measure(m.groups, sel, {});
}
{
  const plate = landOf("step_plate_holes");
  ck("plate with two voids and a round hole: land is exact",
     rel(plate.land_mm2, PLATE_LAND) < 1e-12, plate.land_mm2.toFixed(6) + " vs " + PLATE_LAND.toFixed(6));
  ck("its perimeter counts the hole walls too",
     rel(plate.edge_length_mm, PLATE_PERIM) < 1e-12, plate.edge_length_mm.toFixed(4));
  ck("three holes are reported as holes", plate.n_holes === 3, String(plate.n_holes));
  ck("the reference area falls back to the equivalent bounded area, 100 x 60",
     rel(plate.envelope_mm2, 6000) < 1e-12 && plate.envelope_source !== "stated");
  ck("so the land ratio is land / 6000",
     rel(plate.land_ratio, PLATE_LAND / 6000) < 1e-12, (100 * plate.land_ratio).toFixed(4) + "%");
  ck("and sea is the rest of the envelope, not zero",
     rel(plate.sea_mm2, 6000 - PLATE_LAND) < 1e-12, plate.sea_mm2.toFixed(4));

  const blocks = landOf("step_blocks");
  ck("five block bands on one plane: land is exact",
     rel(blocks.land_mm2, BLOCKS_LAND) < 1e-12, blocks.land_mm2.toFixed(6));
  ck("they group into ONE surface family of five faces",
     blocks.n_faces === 5 && blocks.selected.length === 1, blocks.n_faces + " faces");
  ck("with no holes anywhere", blocks.n_holes === 0);

  const cyl = landOf("step_band_cyl");
  ck("a cylindrical band develops to its true surface area",
     rel(cyl.land_mm2, CYL_LAND) < 1e-9, cyl.land_mm2.toFixed(6) + " vs " + CYL_LAND.toFixed(6));
  ck("the developed arc is R times the angle, not the chord",
     rel(cyl.bbox.w, CYL_DEV) < 1e-9, cyl.bbox.w.toFixed(6) + " mm");
  ck("and the axial extent is the band width", rel(cyl.bbox.h, 200) < 1e-9, cyl.bbox.h.toFixed(4));

  const spl = landOf("step_spline_edge");
  ck("a B-spline edge through collinear controls is the straight edge",
     rel(spl.land_mm2, 1000) < 1e-9, spl.land_mm2.toFixed(9));

  const tor = landOf("step_toroidal");
  ck("the measurable face of the toroidal fixture is exact",
     rel(tor.land_mm2, 2000) < 1e-12, tor.land_mm2.toFixed(6));
  ck("the torus face is NOT quietly dropped from the count",
     models.step_toroidal.n_unmeasured === 1, String(models.step_toroidal.n_unmeasured));
  ck("it is reported by name in a warning",
     models.step_toroidal.warnings.some((w) => /TOROIDAL_SURFACE/.test(w)),
     (models.step_toroidal.warnings[0] || "").slice(0, 60));
}

// ---------------------------------------------------------------------------
section("4. units: the same solid, written in inches");
// ---------------------------------------------------------------------------
{
  const inch = models.step_inch;
  ck("the conversion-based unit is read as inches",
     rel(inch.unit.mm, 25.4) < 1e-12 && /INCH/.test(inch.unit.name),
     inch.unit.name + " x" + inch.unit.mm);
  const a = landOf("step_inch"), b = landOf("step_plate_holes");
  ck("and the land area comes out the same as the millimetre file",
     rel(a.land_mm2, b.land_mm2) < 1e-12, a.land_mm2.toFixed(6));
  ck("lengths scale by k and areas by k^2, so the ratio is unchanged",
     rel(a.land_ratio, b.land_ratio) < 1e-12);
  ck("the groove depth comes back in millimetres",
     rel(S.measure(inch.groups, [S.suggestTread(inch.groups).key],
          { floorKey: S.suggestFloor(inch.groups, S.suggestTread(inch.groups)).key }).groove_depth_mm,
         NSD) < 1e-9);
  // NEGATIVE CONTROL: if the unit were ignored the areas would differ by 645.
  ck("reading it as millimetres would be out by 25.4^2 (the check can fail)",
     rel(a.land_mm2 / (25.4 * 25.4), b.land_mm2) > 0.9);
}

// ---------------------------------------------------------------------------
section("5. an independent measurement of the same areas");
// ---------------------------------------------------------------------------
//
// The areas above all come out of one contour integral. This measures them
// again by sampling points against the developed outlines -- no Green's
// theorem, no arc terms, no shared code beyond the point lists themselves.
{
  function inPoly(pts, x, y) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const xi = pts[i][0], yi = pts[i][1], xj = pts[j][0], yj = pts[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function sampledArea(m, step) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    const faces = [];
    m.selected.forEach((g) => g.faces.forEach((f) => {
      faces.push(f);
      f.loops.forEach((L) => L.pts.forEach((p) => {
        if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0];
        if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1];
      }));
    }));
    let hits = 0, total = 0;
    // Offset by half a cell so a sample never lands exactly on an edge.
    for (let x = x0 + step / 2; x < x1; x += step)
      for (let y = y0 + step / 2; y < y1; y += step) {
        total++;
        for (let f = 0; f < faces.length; f++) {
          let out = null, holed = false;
          for (let L = 0; L < faces[f].loops.length; L++) {
            const lp = faces[f].loops[L];
            if (lp.outer) out = inPoly(lp.pts, x, y);
            else if (inPoly(lp.pts, x, y)) holed = true;
          }
          if (out && !holed) { hits++; break; }
        }
      }
    return (hits / total) * (x1 - x0) * (y1 - y0);
  }
  const plate = landOf("step_plate_holes");
  const est = sampledArea(plate, 0.1);
  ck("point sampling agrees with the plate's contour integral",
     rel(est, plate.land_mm2) < 2e-3, est.toFixed(2) + " vs " + plate.land_mm2.toFixed(2));
  const cyl = landOf("step_band_cyl");
  const estC = sampledArea(cyl, 0.4);
  ck("and with the developed cylinder's",
     rel(estC, cyl.land_mm2) < 2e-3, estC.toFixed(1) + " vs " + cyl.land_mm2.toFixed(1));
  const blocks = landOf("step_blocks");
  ck("and with the block bands'",
     rel(sampledArea(blocks, 0.1), blocks.land_mm2) < 2e-3);
}

// ---------------------------------------------------------------------------
section("6. which surface is the tread");
// ---------------------------------------------------------------------------
{
  const m = models.step_blocks;
  const t = S.suggestTread(m.groups);
  ck("the outermost surface is suggested, not the largest",
     rel(t.info.offset, 10) < 1e-9, t.label);
  // NEGATIVE CONTROL: on this fixture the floor IS the bigger family, so a
  // largest-area rule would pick it. Check that it would have been wrong.
  const biggest = m.groups.slice().sort((a, b) => b.area - a.area)[0];
  ck("picking by area would have picked the floor (so the rule matters)",
     biggest !== t && rel(biggest.area, 6000) < 1e-9, biggest.label);
  const f = S.suggestFloor(m.groups, t);
  ck("and the floor below it is offered as the groove bottom",
     f && rel(f.info.offset, 2) < 1e-9, f && f.label);

  const cy = models.step_band_cyl;
  ck("a one-family cylindrical model suggests its only surface",
     S.suggestTread(cy.groups) === cy.groups[0] && cy.groups.length === 1);
  ck("and offers no floor, rather than inventing one",
     S.suggestFloor(cy.groups, cy.groups[0]) === null);
}

// ---------------------------------------------------------------------------
section("7. groove depth, and what it refuses to report");
// ---------------------------------------------------------------------------
{
  const m = models.step_plate_holes;
  const t = S.suggestTread(m.groups), f = S.suggestFloor(m.groups, t);
  const good = S.measure(m.groups, [t.key], { floorKey: f.key });
  ck("depth between the two planes is the modelled 8 mm",
     rel(good.groove_depth_mm, NSD) < 1e-12, good.groove_depth_mm.toFixed(6) + " mm");
  ck("with no complaint attached", good.groove_depth_note === null);
  ck("void volume is sea x depth, and says it is an upper bound",
     rel(good.groove_volume_mm3, good.sea_mm2 * NSD) < 1e-12, good.groove_volume_mm3.toFixed(1) + " mm³");

  // The two picks the wrong way round. This is the easy mistake -- a floor
  // looks like a tread in a family list -- and it must not produce a plausible
  // depth of the right size and the wrong meaning.
  const back = S.measure(m.groups, [f.key], { floorKey: t.key });
  ck("swapping tread and floor reports no depth at all", back.groove_depth_mm === null);
  ck("and says which way round they are", /upside down/.test(back.groove_depth_note),
     back.groove_depth_note.slice(0, 50));

  // Non-parallel surfaces have no single separation, and saying so beats
  // reporting the distance between two arbitrary points on them.
  const top = { kind: "plane", info: { normal: [0, 0, 1], offset: 10 } };
  const side = { kind: "plane", info: { normal: [1, 0, 0], offset: 0 } };
  ck("a wall is not a floor of the tread", S.familyOffsetBelow(top, side) === null);
  ck("a cylinder is not a floor of a plane",
     S.familyOffsetBelow(top, { kind: "cylinder", info: { axis: [1, 0, 0], radius: 5 } }) === null);
  ck("a deeper plane is, and by the right amount",
     rel(S.familyOffsetBelow(top, { kind: "plane", info: { normal: [0, 0, 1], offset: 3.5 } }), 6.5) < 1e-12);
}

// ---------------------------------------------------------------------------
section("8. the envelope the ratio is taken against");
// ---------------------------------------------------------------------------
{
  const m = models.step_plate_holes;
  const key = S.suggestTread(m.groups).key;
  const box = S.measure(m.groups, [key], {});
  const stated = S.measure(m.groups, [key], { width: 120, height: 80 });
  ck("a stated envelope replaces the bounding box",
     rel(stated.envelope_mm2, 9600) < 1e-12 && stated.envelope_source === "stated");
  ck("the land does not change with it -- only what it is divided by",
     rel(stated.land_mm2, box.land_mm2) < 1e-12);
  ck("so a bigger envelope gives a smaller ratio",
     rel(stated.land_ratio, PLATE_LAND / 9600) < 1e-12,
     (100 * stated.land_ratio).toFixed(3) + "% vs " + (100 * box.land_ratio).toFixed(3) + "%");
  ck("land + sea is the envelope, exactly",
     rel(stated.land_mm2 + stated.sea_mm2, stated.envelope_mm2) < 1e-12);
  ck("edge density is perimeter over that same envelope",
     rel(stated.edge_density, stated.edge_length_mm / 9600) < 1e-12,
     stated.edge_density.toFixed(6) + " mm/mm²");
  // Half the plate ticked: exactly half the faces is not available on a
  // single-face fixture, so this checks the multi-family path on the blocks.
  const b = models.step_blocks;
  const all = S.suggestTread(b.groups);
  ck("selecting nothing measures nothing, rather than everything",
     S.measure(b.groups, [], {}) === null);
  ck("selecting both families sums them",
     rel(S.measure(b.groups, b.groups.map((g) => g.key), {}).land_mm2,
         b.groups.reduce((s, g) => s + g.area, 0)) < 1e-12);
  ck("and one family alone is just that one", rel(S.measure(b.groups, [all.key], {}).land_mm2,
     BLOCKS_LAND) < 1e-12);
}

// ---------------------------------------------------------------------------
section("9. the whole pipeline on each fixture, as the page runs it");
// ---------------------------------------------------------------------------
{
  const expect = {
    step_plate_holes: PLATE_LAND, step_blocks: BLOCKS_LAND, step_band_cyl: CYL_LAND,
    step_spline_edge: 1000, step_inch: PLATE_LAND, step_toroidal: 2000,
  };
  Object.keys(expect).forEach((f) => {
    const m = models[f], t = S.suggestTread(m.groups);
    const fl = S.suggestFloor(m.groups, t);
    const r = S.measure(m.groups, [t.key], { floorKey: fl ? fl.key : null });
    ck(f.padEnd(17) + " " + t.label.padEnd(30) +
       " land " + r.land_mm2.toFixed(4).padStart(11) +
       "  ratio " + (100 * r.land_ratio).toFixed(2).padStart(6) + "%" +
       "  depth " + (r.groove_depth_mm == null ? "  –  " : r.groove_depth_mm.toFixed(2)),
       rel(r.land_mm2, expect[f]) < 1e-9);
    // Every drawn outline must close and be finite, or the page draws nothing
    // and says nothing about why.
    let bad = 0;
    r.selected.forEach((g) => g.faces.forEach((fc) => fc.loops.forEach((L) => {
      if (L.pts.length < 3) bad++;
      L.pts.forEach((p) => { if (!isFinite(p[0]) || !isFinite(p[1])) bad++; });
    })));
    ck("  its drawn outlines are finite and closed", bad === 0, bad + " bad point(s)");
  });
}

// ---------------------------------------------------------------------------
section("10. the equivalent bounded area, which is the denominator");
// ---------------------------------------------------------------------------
//
// The one part of the calculation that is a convention rather than a
// measurement, so it gets checked on shapes whose hull is known by hand.
{
  const sq = [[0, 0], [40, 0], [40, 20], [0, 20]];
  ck("the hull of a rectangle is the rectangle",
     rel(S.polygonArea(S.convexHull(sq)), 800) < 1e-12);

  // Rotated 30 degrees: the hull still measures 800, a bounding box measures
  // 1666. This is the whole argument for using one and not the other.
  const th = Math.PI / 6, c = Math.cos(th), s30 = Math.sin(th);
  const rot = sq.map((p) => [p[0] * c - p[1] * s30, p[0] * s30 + p[1] * c]);
  const hullA = S.polygonArea(S.convexHull(rot));
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  rot.forEach((p) => { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]);
                       y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); });
  const boxA = (x1 - x0) * (y1 - y0);
  ck("and is unchanged when that rectangle is rotated", rel(hullA, 800) < 1e-9, hullA.toFixed(6));
  ck("where a bounding box would have grown by 108%",
     rel(boxA, (40 * c + 20 * s30) * (40 * s30 + 20 * c)) < 1e-9 && boxA / hullA > 2,
     boxA.toFixed(1) + " vs " + hullA.toFixed(1) + " mm²");

  // Concave: the hull spans the notch, which is the point -- a groove inside
  // the pick is sea, and sea belongs in the denominator.
  const ell = [[0, 0], [30, 0], [30, 10], [10, 10], [10, 30], [0, 30]];
  const ellHull = S.polygonArea(S.convexHull(ell));
  ck("the hull of an L spans its notch", rel(ellHull, 30 * 30 - 20 * 20 / 2) < 1e-12,
     ellHull.toFixed(2) + " vs the L's own " + 500);
  ck("and is bigger than the L but smaller than its box",
     ellHull > 500 && ellHull < 900, ellHull.toFixed(1));

  // Degenerate input must not throw or invent an area.
  ck("two points bound no area", S.polygonArea(S.convexHull([[0, 0], [1, 1]])) === 0);
}

// ---------------------------------------------------------------------------
section("11. picking part of a pattern, face by face");
// ---------------------------------------------------------------------------
//
// The question is usually about PART of a tread -- a rib, a block row, one
// pitch -- and every one of those sits on the same plane as the rest of the
// tread top. So the measurement has to work on the faces picked, not on whole
// surfaces, and these are the numbers that follows from that.
{
  const m = models.step_blocks;
  const tread = S.suggestTread(m.groups);
  const bands = tread.faces;                       // five 100 x 8.8 bands
  ck("the fixture is the five bands it should be", bands.length === 5);

  const one = S.measureFaces([bands[0]], m.groups, {});
  ck("one band alone measures its own area", rel(one.land_mm2, 880) < 1e-12, one.land_mm2.toFixed(4));
  ck("and is 100% land -- a solid rectangle has no sea in it",
     rel(one.land_ratio, 1) < 1e-12, (100 * one.land_ratio).toFixed(2) + "%");

  // Two bands with a 4 mm groove between them: the groove is INSIDE the
  // bounded outline, so it is sea. 100 x (8.8 + 4 + 8.8) = 2160 mm².
  const two = S.measureFaces([bands[0], bands[1]], m.groups, {});
  ck("two neighbouring bands bound the groove between them",
     rel(two.envelope_mm2, 100 * (8.8 + 4 + 8.8)) < 1e-9, two.envelope_mm2.toFixed(4));
  ck("so their land is the rubber and the groove is the sea",
     rel(two.land_mm2, 1760) < 1e-12 && rel(two.sea_mm2, 400) < 1e-9,
     two.land_mm2.toFixed(1) + " land, " + two.sea_mm2.toFixed(1) + " sea");
  ck("which is a land ratio of 81.48%", rel(two.land_ratio, 1760 / 2160) < 1e-12,
     (100 * two.land_ratio).toFixed(2) + "%");
  // NEGATIVE CONTROL: the two-band answer must NOT be the whole-tread answer,
  // or face-level picking is not doing anything.
  const all = S.measureFaces(bands, m.groups, {});
  ck("and is not the same as taking the whole tread (picking does something)",
     Math.abs(two.land_ratio - all.land_ratio) > 0.05,
     (100 * two.land_ratio).toFixed(2) + "% vs " + (100 * all.land_ratio).toFixed(2) + "%");

  ck("the whole set of faces equals the whole family",
     rel(all.land_mm2, S.measure(m.groups, [tread.key], {}).land_mm2) < 1e-12);
  ck("land is additive over the faces picked",
     rel(bands.reduce((s2, b) => s2 + S.measureFaces([b], m.groups, {}).land_mm2, 0),
         all.land_mm2) < 1e-12);
  ck("picking no faces measures nothing", S.measureFaces([], m.groups, {}) === null);
  ck("a face that could not be measured cannot be picked into a total",
     S.measureFaces(models.step_toroidal.faces.filter((f) => f.area == null),
                    models.step_toroidal.groups, {}) === null);

  // The pick knows which surfaces it touches -- that is what the page labels
  // it with, and what the groove depth is measured from.
  ck("the pick reports the surface its faces lie on",
     all.families.length === 1 && all.families[0].n === 5,
     JSON.stringify(all.families.map((f) => f.n)));
  ck("and the depth is found without being asked for",
     rel(all.groove_depth_mm, NSD) < 1e-12, all.groove_depth_mm.toFixed(3) + " mm");
}

// ---------------------------------------------------------------------------
section("12. every face is drawable, including the ones it cannot measure");
// ---------------------------------------------------------------------------
//
// The viewport draws the model from these outlines. A face missing from them
// is a hole in the picture, and a tread whose fillets are missing does not
// look like the engineer's tread.
{
  Object.keys(models).forEach((f) => {
    const m = models[f];
    const drawable = m.faces.filter((r) => r.shell && r.shell.length);
    let bad = 0, pts = 0;
    m.faces.forEach((r) => (r.shell || []).forEach((L) => {
      if (L.pts.length < 3) bad++;
      L.pts.forEach((p) => { pts++; if (!isFinite(p[0]) || !isFinite(p[1]) || !isFinite(p[2])) bad++; });
    }));
    ck(f.padEnd(17) + " " + String(drawable.length).padStart(2) + "/" +
       String(m.faces.length).padEnd(2) + " faces drawable, " + String(pts).padStart(5) + " points",
       drawable.length === m.faces.length && bad === 0);
  });
  const tor = models.step_toroidal;
  ck("the torus face is drawn even though it cannot be measured",
     tor.faces.filter((r) => r.area == null)[0].shell.length > 0);
  // A display outline is for looking at, not for measuring: it must be far
  // coarser than the area path or a big model is a slideshow.
  const holeFace = models.step_plate_holes.faces.filter((r) => r.holes === 3)[0];
  const drawPts = holeFace.shell.reduce((s2, L) => s2 + L.pts.length, 0);
  const areaPts = holeFace.loops.reduce((s2, L) => s2 + L.pts.length, 0);
  ck("and is coarser than the outline the area was integrated from",
     drawPts < areaPts, drawPts + " display points vs " + areaPts + " measurement points");
}

console.log("\n" + (fails ? fails + " CHECK(S) FAILED of " + checks : checks + " checks passed"));
process.exit(fails ? 1 : 0);
