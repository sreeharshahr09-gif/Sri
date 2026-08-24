/* Audit of pitch replication, multi-pitch sequences and closure diagnostics.
 *
 * One drawn pitch becomes the whole rolled-out tread. That is a geometry
 * transform standing in front of every number the tool produces, so it gets its
 * own audit rather than riding on the importer's.  Everything here is checked
 * against a hand-computed quantity, against the same tread drawn out in full,
 * or against a statement that must hold whatever the numbers turn out to be.
 *
 * Run:  node app/pitchaudit.js
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

const DEF = { height: 16, shore_a: 60, draft_angle: 2 };
const load = (file, pitch) =>
  E.loadPattern(fs.readFileSync(path.join(DATA, file), "utf8"), DEF, pitch ? { pitch: pitch } : {});
function failsWith(file, pitch) {
  try { load(file, pitch); return ""; } catch (e) { return e.message; }
}
// Circumferential extent of every block, tallied.
function blockLengths(pattern) {
  const t = {};
  for (const b of pattern.blocks) {
    let lo = Infinity, hi = -Infinity;
    for (const q of b.polygon) { if (q[0] < lo) lo = q[0]; if (q[0] > hi) hi = q[0]; }
    const L = (hi - lo).toFixed(3);
    t[L] = (t[L] || 0) + 1;
  }
  return t;
}

const SEQ25 = "ABCBAABCBAABCBAABCBAABCBA";     // 10 A, 10 B, 5 C
const LENS = { A: 34, B: 40, C: 46 };           // 10*34 + 10*40 + 5*46 = 970

// =====================================================================
section("1. the sequence itself");
// =====================================================================
{
  ck("count form gives n copies of one length",
     E.pitchInstanceLengths({ length: 33.2, count: 4 }).join(",") === "33.2,33.2,33.2,33.2");
  const seq = E.pitchInstanceLengths({ lengths: LENS, sequence: SEQ25 });
  let tot = 0; for (const v of seq) tot += v;
  ck("a named sequence resolves letter by letter", seq.length === 25 && tot === 970,
     seq.length + " pitches, " + tot + " mm");
  ck("a literal list of lengths is taken as given",
     E.pitchInstanceLengths({ sequence: [30, 35, 40] }).join(",") === "30,35,40");
  ck("lower case and punctuation in the sequence are ignored",
     E.pitchInstanceLengths({ lengths: LENS, sequence: "a-b c" }).join(",") === "34,40,46");
  let msg = "";
  try { E.pitchInstanceLengths({ lengths: { A: 30 }, sequence: "ABD" }); } catch (e) { msg = e.message; }
  ck("a letter with no length is refused, and named", /'B'|'D'/.test(msg), msg.slice(0, 80));
  msg = "";
  try { E.pitchInstanceLengths({ length: 30, count: 0 }); } catch (e) { msg = e.message; }
  ck("a zero pitch count is refused", msg.length > 0);
}

// =====================================================================
section("2. replication == the same tread drawn out in full");
// =====================================================================
// The strongest check available: six pitches laid out by hand in the DXF,
// against six built by the replicator. The two routes share nothing but the
// importer, so agreement is not self-confirming.
{
  const whole = load("pitch_whole_x6.dxf", null);
  const built = load("pitch_base_tbr.dxf", { base_length: 40, length: 40, count: 6 });
  ck("same circumference",
     rel(whole.pattern.tyre_circumference, built.pattern.tyre_circumference) < 1e-12,
     whole.pattern.tyre_circumference.toFixed(3) + " mm");
  ck("same tread width", rel(whole.pattern.tread_width, built.pattern.tread_width) < 1e-12);
  ck("same block count", whole.pattern.blocks.length === built.pattern.blocks.length,
     whole.pattern.blocks.length + " blocks either way");
  ck("same land ratio", rel(whole.report.land_ratio, built.report.land_ratio) < 1e-9,
     whole.report.land_ratio.toFixed(9));
  ck("same set of block lengths",
     JSON.stringify(blockLengths(whole.pattern)) === JSON.stringify(blockLengths(built.pattern)),
     JSON.stringify(blockLengths(built.pattern)));
}

// =====================================================================
section("3. uniform scaling");
// =====================================================================
// Scaling the whole pitch scales land and circumference together, so the land
// ratio cannot move. Block lengths must land exactly on 30 * L/40.
{
  const r = load("pitch_blocks_tbr.dxf",
                 { base_length: 40, lengths: LENS, sequence: SEQ25, scaling: "uniform" });
  ck("circumference is the sequence total", rel(r.pattern.tyre_circumference, 970) < 1e-12,
     r.pattern.tyre_circumference.toFixed(3) + " mm");
  ck("every pitch is present", r.pattern.pitches.length === 25);
  const base = load("pitch_blocks_tbr.dxf", null);
  // the drawn block is 30 mm of the 40 mm pitch, so 30 * 34/40 = 25.5 etc.
  const want = { "25.500": 40, "30.000": 40, "34.500": 20 };
  ck("block lengths are the drawn length x (pitch / base pitch)",
     JSON.stringify(blockLengths(r.pattern)) === JSON.stringify(want),
     JSON.stringify(blockLengths(r.pattern)));
  // land = 170 mm of block height x 30 mm x (L/40), summed = 127.5 * 970
  const land = 127.5 * 970, area = 970 * base.pattern.tread_width;
  ck("land ratio matches the hand calculation", rel(r.report.land_ratio, land / area) < 1e-6,
     r.report.land_ratio.toFixed(6) + " vs " + (land / area).toFixed(6));
}

// =====================================================================
section("4. groove-only scaling, and why the tool will not choose");
// =====================================================================
{
  const g = load("pitch_blocks_tbr.dxf",
                 { base_length: 40, lengths: LENS, sequence: SEQ25, scaling: "groove_only" });
  const u = load("pitch_blocks_tbr.dxf",
                 { base_length: 40, lengths: LENS, sequence: SEQ25, scaling: "uniform" });
  ck("blocks keep their circumferential length",
     JSON.stringify(blockLengths(g.pattern)) === JSON.stringify({ "30.000": 100 }),
     JSON.stringify(blockLengths(g.pattern)));
  ck("the circumference is the same either way",
     g.pattern.tyre_circumference === u.pattern.tyre_circumference);
  ck("the land ratio is NOT -- which is the whole reason for the choice",
     Math.abs(g.report.land_ratio - u.report.land_ratio) > 1e-3,
     "groove_only " + g.report.land_ratio.toFixed(5) + " vs uniform " + u.report.land_ratio.toFixed(5));
  // 170 mm of block height x 30 mm x 25 pitches, over the whole tread
  const land = 170 * 30 * 25, area = 970 * g.pattern.tread_width;
  ck("groove-only land matches the hand calculation", rel(g.report.land_ratio, land / area) < 1e-6,
     g.report.land_ratio.toFixed(6) + " vs " + (land / area).toFixed(6));

  const msg = failsWith("pitch_blocks_tbr.dxf",
                        { base_length: 40, lengths: LENS, sequence: SEQ25 });
  ck("a stretching sequence with no convention named is refused",
     /scaling convention has to be stated/.test(msg), msg.slice(0, 70) + "...");
  ck("the refusal names both conventions",
     /uniform/.test(msg) && /groove_only/.test(msg));
  // a pitch whose land covers every x has no groove to put the length in
  let deg = "";
  try { E.pitchStretchMap(40, 46, [[0, 40]]); } catch (e) { deg = e.message; }
  ck("groove-only is refused when there is no groove band to stretch",
     /needs circumferential gaps/.test(deg), deg.slice(0, 76) + "...");
  let shrink = "";
  try { E.pitchStretchMap(40, 20, [[0, 30]]); } catch (e) { shrink = e.message; }
  ck("groove-only is refused when the blocks alone are longer than the pitch",
     /would have to overlap/.test(shrink), shrink.slice(0, 76) + "...");
}

// =====================================================================
section("5. closure diagnostics");
// =====================================================================
{
  // clean: continuous ribs crossing both boundaries, lined up exactly
  const ok = load("pitch_base_tbr.dxf", { base_length: 40, length: 40, count: 24 });
  ck("a pitch that tiles reports closure", ok.report.pitch.closure.closes &&
     ok.report.pitch.closure.max_gap_mm === 0, "gap 0 mm");

  // 0.4 mm out of line: must refuse, and say by how much and where
  const bad = failsWith("pitch_open_tbr.dxf", { base_length: 40, length: 40, count: 24 });
  ck("a non-closing pitch is refused", /does not close/.test(bad));
  ck("the refusal states the measured gap", /0\.4000 mm/.test(bad), "0.4000 mm");
  ck("the refusal locates the mismatches in y", /->/.test(bad) && /158\.000/.test(bad));
  ck("the refusal states the snap tolerance that would fix it",
     /snap tolerance of at least 0\.4/.test(bad));
  ck("nothing is built from a pattern that does not close", bad.length > 0);

  // with a snap limit it is repaired, and says so
  const snapped = load("pitch_open_tbr.dxf",
                       { base_length: 40, length: 40, count: 24, snap_tolerance: 0.5 });
  ck("a snap limit closes it", snapped.pattern.blocks.length === ok.pattern.blocks.length,
     snapped.pattern.blocks.length + " blocks, same as the clean drawing");
  ck("the snap is reported, with how many points moved",
     snapped.report.pitch.snapped && snapped.report.pitch.snapped.moved === 8,
     snapped.report.pitch.snapped.moved + " points");
  ck("the run says the tread is no longer exactly the drawing",
     snapped.report.warnings.some((w) => /not exactly the drawing/.test(w)));
  ck("snapping restores the land ratio", rel(snapped.report.land_ratio, ok.report.land_ratio) < 1e-9,
     snapped.report.land_ratio.toFixed(6));
  // and a snap limit below the gap must NOT quietly close it
  const tooTight = failsWith("pitch_open_tbr.dxf",
                             { base_length: 40, length: 40, count: 24, snap_tolerance: 0.1 });
  ck("a snap limit smaller than the gap still refuses", /does not close/.test(tooTight));

  // unequal boundary counts: cannot be told from a legitimate blocked pattern
  const amb = load("pitch_broken_tbr.dxf", { base_length: 40, length: 40, count: 24 });
  ck("unequal boundary points are reported, not guessed at",
     amb.report.warnings.some((w) => /different numbers of outline points/.test(w)));
  ck("and the unpaired cut line is called out",
     amb.report.warnings.some((w) => /no matching outline at the other end/.test(w)));

  // a blocked pattern whose land stops before the far boundary needs nothing to
  // meet there: the join is a lateral groove
  const triv = load("pitch_blocks_tbr.dxf", { base_length: 40, length: 40, count: 10 });
  ck("a pitch that does not reach the far boundary closes with a groove at the join",
     !triv.report.pitch.closure.reaches_far && triv.report.pitch.closure.closes);
  ck("and that is stated rather than left silent",
     triv.report.warnings.some((w) => /clean lateral groove at every join/.test(w)));
}

// =====================================================================
section("6. cut lines, and the ribs that depend on them");
// =====================================================================
// The lines where the pitch was cut out of the tread are not tread edges. Left
// in, they sit inside a continuous rib at every join and chop it into one block
// per pitch -- which changes the rib's shape factor and so its Kz.
{
  const r = load("pitch_base_tbr.dxf", { base_length: 40, length: 40, count: 24 });
  ck("the cut lines were found and removed",
     r.report.warnings.some((w) => /cut line\(s\) were removed/.test(w)));
  // 2 continuous shoulder ribs + 4 intermediate blocks x 24 pitches
  ck("continuous ribs survive as single blocks, not one per pitch",
     r.pattern.blocks.length === 24 * 4 + 2, r.pattern.blocks.length + " blocks");
  const long = r.pattern.blocks.filter((b) => {
    let lo = Infinity, hi = -Infinity;
    for (const q of b.polygon) { if (q[0] < lo) lo = q[0]; if (q[0] > hi) hi = q[0]; }
    return hi - lo > 900;
  });
  ck("both ribs run the whole circumference", long.length === 2,
     long.length + " block(s) longer than 900 mm");
  ck("land ratio is the hand figure", rel(r.report.land_ratio, 0.75) < 1e-9,
     r.report.land_ratio.toFixed(9));
}

// =====================================================================
section("7. the pitch length is an input, not a measurement");
// =====================================================================
{
  const msg = failsWith("pitch_blocks_tbr.dxf", { length: 40, count: 10 });
  ck("asking for a length that differs from the drawing requires stating the pitch",
     /pitch length has to be given/.test(msg), msg.slice(0, 76) + "...");
  const asDrawn = load("pitch_blocks_tbr.dxf", { length: 30, count: 10 });
  ck("but repeating the drawing exactly as it stands needs nothing stated",
     rel(asDrawn.pattern.tyre_circumference, 300) < 1e-12,
     asDrawn.pattern.tyre_circumference.toFixed(1) + " mm from a 30 mm drawing x 10");
  const short = failsWith("pitch_base_tbr.dxf", { base_length: 20, length: 20, count: 10 });
  ck("a pitch length shorter than the drawing is refused",
     /shorter than the drawing/.test(short), short.slice(0, 80) + "...");
  const slack = load("pitch_blocks_tbr.dxf", { base_length: 50, length: 50, count: 10 });
  ck("slack beyond the drawing becomes groove, and is reported",
     slack.report.warnings.some((w) => /becomes groove at the trailing end/.test(w)) &&
     rel(slack.pattern.tyre_circumference, 500) < 1e-12,
     slack.pattern.tyre_circumference.toFixed(1) + " mm");
}

// =====================================================================
section("8. what the rest of the tool sees");
// =====================================================================
{
  const r = load("pitch_blocks_tbr.dxf",
                 { base_length: 40, lengths: LENS, sequence: SEQ25, scaling: "uniform" });
  const p = r.pattern;
  ck("pitches carry their real starts and lengths, not an average",
     p.pitches.length === 25 &&
     Math.abs(p.pitches[0].circumferential_length - 34) < 1e-9 &&
     Math.abs(p.pitches[2].circumferential_length - 46) < 1e-9,
     "P000 " + p.pitches[0].circumferential_length + " mm, P002 " + p.pitches[2].circumferential_length + " mm");
  let run = 0;
  for (const pt of p.pitches) {
    ck.silent = true;
    if (Math.abs(pt.circumferential_start - run) > 1e-9) { ck("pitch starts are contiguous", false); break; }
    run += pt.circumferential_length;
  }
  ck("pitch starts run end to end with no gap", Math.abs(run - p.tyre_circumference) < 1e-9,
     run.toFixed(3) + " mm");
  ck("every block is tagged with the pitch it belongs to",
     p.blocks.every((b) => /^P\d{3}$/.test(b.pitch_id)));
  const perPitch = {};
  for (const b of p.blocks) perPitch[b.pitch_id] = (perPitch[b.pitch_id] || 0) + 1;
  ck("the blocks are spread evenly over the pitches",
     Object.keys(perPitch).length === 25 &&
     Object.values(perPitch).every((n) => n === 4),
     "4 blocks in each of " + Object.keys(perPitch).length + " pitches");

  // and the sweep runs on it exactly as on any other tread
  const grid = E.makeGrid(p, 2048, 128);
  const pack = E.rasterise(p, grid, { shore_a: 60, poisson: 0.49, mode: "parallel",
                                      bulk_modulus: 1100, n_slices: 20 }, false, {});
  const spec = { shape: "rectangle", length: 180, width: 180, gamma_deg: 0,
                 scale_with_lean: false, y_center: 0 };
  const sw = E.sweepLean(p, pack, 0, spec, { vertical_load: 26000, wheel_radius: 500,
                                             load_rises_with_lean: false }, null, 90, null);
  const mean = (a) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return s / a.length; };
  ck("the sweep runs on a replicated tread and produces finite curves",
     sw.contact_area.every(Number.isFinite) && mean(sw.contact_area) > 0 &&
     sw.c_alpha.every(Number.isFinite),
     "mean contact " + mean(sw.contact_area).toFixed(0) + " mm², Ca " + mean(sw.c_alpha).toFixed(0) + " N/rad");
  // The order spectrum of a sequenced tread must NOT be a single clean spike at
  // the pitch count -- spreading that spike is the entire point of pitching.
  const uni = load("pitch_blocks_tbr.dxf",
                   { base_length: 40, length: 38.8, count: 25, scaling: "uniform" });
  const packU = E.rasterise(uni.pattern, E.makeGrid(uni.pattern, 2048, 128),
                            { shore_a: 60, poisson: 0.49, mode: "parallel", bulk_modulus: 1100, n_slices: 20 }, false, {});
  const swU = E.sweepLean(uni.pattern, packU, 0, spec, { vertical_load: 26000, wheel_radius: 500,
                                                         load_rises_with_lean: false }, null, 90, null);
  const peak = (s) => Math.max.apply(null, s.amplitude);
  const pSeq = peak(E.orderSpectrum(sw.contact_area, 60));
  const pUni = peak(E.orderSpectrum(swU.contact_area, 60));
  ck("a pitch sequence spreads the order peak that a single pitch concentrates",
     pSeq < pUni, "peak order amplitude " + (100 * pSeq).toFixed(2) + "% sequenced vs " +
     (100 * pUni).toFixed(2) + "% single-length");
}

console.log("\n" + (fails ? fails + " of " + checks + " checks FAILED" : checks + " checks passed"));
process.exitCode = fails ? 1 : 0;
