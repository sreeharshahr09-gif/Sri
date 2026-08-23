/* Two end-to-end cases through the BUILT page, checked for physical sanity.
 *
 * The audits prove the formulae; this proves the shipping artefact. Two very
 * different tyres are driven through the real UI -- import, inputs, run, every
 * tab, every export -- and the numbers that come out are checked against what
 * the physics says they must be, not against a stored baseline.
 *
 * Run:  node app/casecheck.js
 */
"use strict";
const { chromium } = require("/opt/node22/lib/node_modules/playwright/index.js");
const path = require("path");
const fs = require("fs");
const os = require("os");

let fails = 0, checks = 0;
function ck(name, cond, extra) {
  checks++;
  if (!cond) fails++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (extra ? "   " + extra : ""));
}
const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
const cov = (a) => { const m = mean(a); return Math.sqrt(mean(a.map((v) => (v - m) * (v - m)))) / Math.abs(m); };

async function runCase(page, errors, cfg) {
  console.log("\n" + cfg.title);
  console.log("-".repeat(cfg.title.length));

  await page.selectOption("#tyreType", cfg.tyreType);
  await page.click("#applyPreset");
  await page.waitForTimeout(200);
  if (cfg.dxf) {
    await page.setInputFiles("#fileInput", cfg.dxf);
  } else {
    await page.click("#sampleBtn");
  }
  await page.waitForTimeout(700);
  for (const [sel, val] of Object.entries(cfg.fields || {})) await page.fill(sel, String(val));
  for (const [sel, val] of Object.entries(cfg.selects || {})) await page.selectOption(sel, String(val));
  for (const sel of cfg.checks || []) {
    if (!(await page.isChecked(sel))) await page.click(sel);
  }
  await page.waitForTimeout(300);

  const banner = (await page.textContent("#banner")).replace(/\s+/g, " ");
  console.log("  import: " + banner.slice(0, 120));

  await page.click("#runBtn");
  await page.waitForSelector("#overlay.on", { timeout: 8000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 120000 });
  console.log("  " + (await page.textContent("#timing")));

  const leans = await page.evaluate(() =>
    Array.from(document.getElementById("gammaSel").options).map((o) => o.value));
  const out = { gammas: [] };
  for (const g of leans) {
    await page.selectOption("#gammaSel", g);
    await page.waitForTimeout(250);
    const r = await page.evaluate(() => {
      const x = window.__ttResult();
      if (!x) return null;
      return {
        gamma: x.gamma_deg, patch_area: x.patch_area,
        patch_area_outline: x.patch_area_outline, a: x.patch.a, b: x.patch.b,
        y_center: x.patch.y_center, load: x.patch.normal_load, pressure: x.patch.peak_pressure,
        contact_area: x.contact_area, land: x.land_ratio, kz: x.kz, kx: x.kx, ky: x.ky,
        c_alpha: x.c_alpha, c_kappa: x.c_kappa, c_mz: x.c_mz, trail: x.pneumatic_trail,
        bands: x.bands ? x.bands.map((bd) => ({ y_lo: bd.y_lo, y_hi: bd.y_hi, c_alpha: bd.c_alpha })) : null,
      };
    });
    out.gammas.push(r);
  }
  return out;
}

// Physical statements every case must satisfy, whatever the tyre.
function checkCase(tag, out, opts) {
  const g0 = out.gammas[0];

  // -- units and magnitudes -------------------------------------------------
  ck(tag + ": contact pressure is 0.05-3 MPa",
     g0.pressure > 0.05 && g0.pressure < 3.0, g0.pressure.toFixed(3) + " N/mm²");
  // The pressure comes from the exact outline; the sweep measures a whole
  // number of pixels. Both invariants are checked, and so is the gap between
  // them -- past a percent the grid is too coarse and every area is biased.
  ck(tag + ": load = pressure x OUTLINE area, back out to the input",
     Math.abs(g0.pressure * g0.patch_area_outline - g0.load) / g0.load < 1e-9,
     g0.load.toFixed(0) + " N");
  const quant = Math.abs(g0.patch_area / g0.patch_area_outline - 1);
  ck(tag + ": the raster patch is within 1% of its outline",
     quant < 0.01, (100 * quant).toFixed(3) + "% quantisation on this grid");
  ck(tag + ": land ratio is a fraction",
     Math.min(...g0.land) > 0 && Math.max(...g0.land) <= 1,
     (100 * mean(g0.land)).toFixed(1) + "% mean land");
  ck(tag + ": contact area never exceeds the patch",
     Math.max(...g0.contact_area) <= g0.patch_area * (1 + 1e-9));

  // -- the slip response ----------------------------------------------------
  const lenA = mean(g0.c_alpha) / mean(g0.ky);
  const lenK = mean(g0.c_kappa) / mean(g0.kx);
  ck(tag + ": Cα/Ky is a length inside (0, a]",
     lenA > 0 && lenA <= g0.a * (1 + 1e-9),
     lenA.toFixed(2) + " mm, a = " + g0.a.toFixed(2) + " mm");
  ck(tag + ": Cκ/Kx is the same length (same kernel, different map)",
     Math.abs(lenA - lenK) / lenA < 0.05, lenK.toFixed(2) + " mm");
  ck(tag + ": the trail is behind the centre and inside the patch",
     Math.min(...g0.trail) > 0 && Math.max(...g0.trail) < g0.a,
     mean(g0.trail).toFixed(2) + " mm of a = " + g0.a.toFixed(2) + " mm");
  ck(tag + ": Cmz = Cα x trail at every angle",
     g0.c_alpha.every((v, i) => Math.abs(v * g0.trail[i] - g0.c_mz[i]) <= 1e-6 * Math.abs(g0.c_mz[i]) + 1e-9));
  ck(tag + ": Cα is not Ky rescaled",
     cov(g0.c_alpha.map((v, i) => v / g0.ky[i])) > 1e-4,
     "Cα/Ky swings " + (100 * cov(g0.c_alpha.map((v, i) => v / g0.ky[i]))).toFixed(2) + "% per revolution");
  console.log("       Ky " + (100 * cov(g0.ky)).toFixed(2) + "% CoV, Cα " +
              (100 * cov(g0.c_alpha)).toFixed(2) + "%, trail " + (100 * cov(g0.trail)).toFixed(2) + "%");
  console.log("       Cα " + mean(g0.c_alpha).toFixed(0) + " N/rad = " +
              (mean(g0.c_alpha) * Math.PI / 180).toFixed(0) + " N/deg (tread share)");

  if (g0.bands) {
    let worst = 0;
    for (let i = 0; i < g0.c_alpha.length; i++) {
      let s = 0;
      for (const bd of g0.bands) s += bd.c_alpha[i];
      worst = Math.max(worst, Math.abs(s - g0.c_alpha[i]) / g0.c_alpha[i]);
    }
    ck(tag + ": the ribs' Cα adds up to the tread's Cα", worst < 1e-9,
       "worst rel " + worst.toExponential(2) + " over " + g0.bands.length + " ribs");
    const share = g0.bands.map((bd) => mean(bd.c_alpha) / mean(g0.c_alpha));
    console.log("       cornering force by rib: " + share.map((v) => (100 * v).toFixed(1) + "%").join(" / "));
  }

  // -- across the lean sweep ------------------------------------------------
  if (out.gammas.length > 1) {
    const last = out.gammas[out.gammas.length - 1];
    ck(tag + ": leaning walks the patch outboard",
     Math.abs(last.y_center) > Math.abs(g0.y_center) + 1e-9,
     "y centre " + g0.y_center.toFixed(1) + " -> " + last.y_center.toFixed(1) + " mm at " + last.gamma + "°");
    ck(tag + ": every lean keeps a positive trail and Cα",
       out.gammas.every((r) => Math.min(...r.trail) > 0 && Math.min(...r.c_alpha) > 0));
    console.log("       Cα across lean: " +
      out.gammas.map((r) => r.gamma + "°:" + mean(r.c_alpha).toFixed(0)).join("  "));
  }
  if (opts && opts.expectLeanDrop) {
    ck(tag + ": unreachable leans were dropped rather than extrapolated",
       out.gammas.length < opts.requested,
       out.gammas.length + " of " + opts.requested + " leans reachable on this crown");
  }
}

(async () => {
  const outDir = path.join(__dirname, "..", "out", "shots");
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1500, height: 1050 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.goto("file://" + path.join(__dirname, "..", "tread_tool.html"), { waitUntil: "load" });

  // ---- CASE A: motorcycle, curved crown, real lean sweep, no tie bars ----
  const A = await runCase(page, errors, {
    title: "CASE A — 2W 130/80R17, curved crown, lean sweep, new tyre",
    tyreType: "2w", dxf: null,
    fields: { "#nsd": 8.5, "#draft": 3, "#shore": 62, "#wear": 0 },
  });
  checkCase("A", A);
  await page.screenshot({ path: path.join(outDir, "case-A-stack.png"), fullPage: false });

  // ---- CASE B: truck ribs, flat crown, tie bars engaged, part worn, ribs ----
  const B = await runCase(page, errors, {
    title: "CASE B — TBR rib pattern, flat crown, 38 tie bars, 9 mm worn, 4 ribs",
    tyreType: "tbr", dxf: path.join(__dirname, "..", "data", "tbr_ribs_tiebars.dxf"),
    fields: { "#nsd": 16, "#draft": 2, "#shore": 66, "#wear": 9, "#nBands": 4 },
  });
  checkCase("B", B);
  await page.screenshot({ path: path.join(outDir, "case-B-stack.png"), fullPage: false });

  // ---- the two cases must differ in the way the physics says they should ----
  console.log("\nA vs B");
  console.log("------");
  const a0 = A.gammas[0], b0 = B.gammas[0];
  ck("the truck tread is far stiffer vertically than the motorcycle tread",
     mean(b0.kz) > 3 * mean(a0.kz),
     "Kz " + mean(a0.kz).toFixed(0) + " -> " + mean(b0.kz).toFixed(0) + " N/mm");
  ck("the truck crown reaches much less lean than the motorcycle crown",
     B.gammas.length < A.gammas.length ||
     Math.max(...B.gammas.map((r) => r.gamma)) < Math.max(...A.gammas.map((r) => r.gamma)),
     "max reachable " + Math.max(...A.gammas.map((r) => r.gamma)) + "° vs " +
     Math.max(...B.gammas.map((r) => r.gamma)) + "°");
  ck("the trail scales with the patch, not with the pattern",
     Math.abs(mean(a0.trail) / a0.a - mean(b0.trail) / b0.a) < 0.12,
     "t/a = " + (mean(a0.trail) / a0.a).toFixed(3) + " vs " + (mean(b0.trail) / b0.a).toFixed(3) +
     " (a third is the brush value for a rectangle)");
  ck("a rib tread fluctuates less over a revolution than a blocked one",
     cov(b0.c_alpha) < cov(a0.c_alpha),
     "Cα CoV " + (100 * cov(a0.c_alpha)).toFixed(2) + "% vs " + (100 * cov(b0.c_alpha)).toFixed(2) + "%");

  // ---- exports on the second case ----
  const dl = fs.mkdtempSync(path.join(os.tmpdir(), "case-"));
  for (const [id, label] of [["exportCsv", "CSV"], ["exportJson", "JSON"], ["exportTxt", "summary"]]) {
    const [d] = await Promise.all([page.waitForEvent("download"), page.click("#" + id)]);
    const f = path.join(dl, d.suggestedFilename());
    await d.saveAs(f);
    const txt = fs.readFileSync(f, "utf8");
    if (label === "CSV") {
      const head = txt.split("\n").find((l) => l.startsWith("gamma_deg,"));
      ck("CSV carries the slip columns with their units",
         /c_alpha_N_per_rad/.test(head) && /pneumatic_trail_mm/.test(head));
      const row = txt.split("\n").find((l) => /^\d/.test(l)).split(",");
      ck("CSV slip cells are populated numbers", row.slice(-4).every((v) => v !== "" && isFinite(+v)),
         row.slice(-4).join(" | "));
    }
    if (label === "JSON") {
      const j = JSON.parse(txt);
      ck("JSON run carries the slip arrays",
         !!(j.results[0].c_alpha && j.results[0].c_alpha.length === j.results[0].theta_deg.length));
    }
    if (label === "summary") {
      ck("the summary states the slip response and its caveat",
         /SLIP RESPONSE/.test(txt) && /series/.test(txt));
      ck("the summary gives Fy per degree", /Fy@1deg_N/.test(txt));
    }
    console.log("  export " + label + ": " + d.suggestedFilename() + " (" + (txt.length / 1024).toFixed(0) + " KB)");
  }

  ck("no page errors in either case", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  console.log("\n" + (fails ? fails + " of " + checks + " checks FAILED" : checks + " checks passed"));
  process.exitCode = fails ? 1 : 0;
})();
