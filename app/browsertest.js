/* Playwright smoke test: open tread_tool.html, load the sample DXF, run the
 * sweep, verify charts render, and screenshot each tab. */
const { chromium } = require("/opt/node22/lib/node_modules/playwright/index.js");
const path = require("path");
const fs = require("fs");

(async () => {
  const outDir = path.join(__dirname, "..", "out", "shots");
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome", args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });

  const url = "file://" + path.join(__dirname, "..", "tread_tool.html");
  await page.goto(url, { waitUntil: "load" });
  console.log("loaded page");

  // load sample + run
  await page.click("#sampleBtn");
  await page.waitForTimeout(300);
  const banner = await page.textContent("#banner");
  console.log("banner:", banner.replace(/\s+/g, " ").slice(0, 160));

  await page.click("#runBtn");
  // wait for the overlay to appear then disappear (compute done)
  await page.waitForSelector("#overlay.on", { timeout: 5000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 60000 });
  const timing = await page.textContent("#timing");
  console.log("timing:", timing);

  // check theta stack rendered (Plotly injects an svg)
  const hasStack = await page.$eval("#thetaStack", (el) => !!el.querySelector(".plot-container"));
  const hasStrip = await page.$eval("#patternStrip", (el) => !!el.querySelector(".plot-container"));
  console.log("thetaStack rendered:", hasStack, " patternStrip rendered:", hasStrip);
  const cards = await page.$eval("#cards", (el) => el.textContent.replace(/\s+/g, " ").trim());
  console.log("cards:", cards.slice(0, 200));

  await page.screenshot({ path: path.join(outDir, "01-stack.png"), fullPage: true });

  // The compound readout must state E, G and k -- previously E was derived from
  // Shore A behind the scenes with nothing on screen to say what it was.
  {
    const readout = () => page.textContent("#compoundReadout");
    const atShore60 = await readout();
    if (!/E\s*6\.89/.test(atShore60.replace(/\s+/g, " ")))
      errors.push("compound readout does not show E at Shore 60: " + atShore60.slice(0, 120));
    await page.fill("#shore", "65");
    await page.waitForTimeout(120);
    const at65 = await readout();
    const e65 = parseFloat((at65.match(/E\s*([\d.]+)/) || [])[1]);
    if (!(e65 > 6.89 && e65 < 12.0))
      errors.push(`Shore 65 should interpolate E between 6.89 and 12.0, got ${e65}`);
    console.log("compound readout at Shore 65:", at65.replace(/\s+/g, " ").slice(0, 90));
    // Direct entry: the fields appear, seeded from the hardness, and drive E.
    await page.selectOption("#modulusMode", "direct");
    await page.waitForTimeout(120);
    if (await page.isVisible("#rowShore")) errors.push("Shore field still shown in direct-modulus mode");
    if (!(await page.isVisible("#rowE"))) errors.push("E field not shown in direct-modulus mode");
    await page.fill("#eModulus", "9.5");
    await page.waitForTimeout(120);
    const direct = await readout();
    if (!/E\s*9\.500/.test(direct.replace(/\s+/g, " ")))
      errors.push("direct E entry not reflected in the readout: " + direct.slice(0, 120));
    // A nonsense modulus must block the run with a message, not compute.
    await page.fill("#eModulus", "6890");
    await page.waitForTimeout(150);
    if (!(await page.isDisabled("#runBtn"))) errors.push("E = 6890 N/mm^2 did not block the run");
    const msg = await page.textContent("#specError");
    if (!/N\/mm/.test(msg)) errors.push("no units guidance when E is out of range: " + msg.slice(0, 120));
    await page.selectOption("#modulusMode", "shore");
    await page.fill("#shore", "60");
    await page.waitForTimeout(150);
  }

  // ---- slip response: the brush-model rows ---------------------------------
  // Three rows were added to the stack, and they must be real curves, not
  // rescaled copies of Ky. The engine's own audit proves the physics; this only
  // proves the page is wired to it.
  {
    const slip = await page.evaluate(() => {
      const st = window.__ttState ? window.__ttState() : null;
      const gd = document.getElementById("thetaStack");
      const rows = gd && gd._fullLayout ? Object.keys(gd._fullLayout).filter((k) => /^yaxis\d*$/.test(k)).length : 0;
      const titles = gd && gd._fullLayout
        ? Object.keys(gd._fullLayout).filter((k) => /^yaxis\d*$/.test(k))
            .map((k) => (gd._fullLayout[k].title && gd._fullLayout[k].title.text) || "")
        : [];
      return { rows: rows, titles: titles, cards: document.getElementById("cards").textContent, res: st };
    });
    console.log("theta stack rows:", slip.rows, "|", slip.titles.join(" · "));
    for (const want of ["Cα (N/rad)", "Cκ (N)", "Trail t (mm)"])
      if (!slip.titles.includes(want)) errors.push(`slip row "${want}" missing from the theta stack`);
    if (!/N\/rad/.test(slip.cards)) errors.push("the cards do not report Cα in N/rad");
    if (!/behind patch centre/.test(slip.cards)) errors.push("the cards do not report the pneumatic trail");

    const num = await page.evaluate(() => {
      const r = window.__ttResult ? window.__ttResult() : null;
      if (!r || !r.c_alpha) return null;
      const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
      const ratio = r.c_alpha.map((v, i) => v / r.ky[i]);
      const m = mean(ratio);
      const sd = Math.sqrt(mean(ratio.map((v) => (v - m) * (v - m))));
      return { ca: mean(r.c_alpha), ck: mean(r.c_kappa), t: mean(r.pneumatic_trail),
               ky: mean(r.ky), a: r.patch.a, ratioCov: sd / m,
               minCa: Math.min(...r.c_alpha), minT: Math.min(...r.pneumatic_trail) };
    });
    if (!num) {
      errors.push("the run carries no slip-response arrays");
    } else {
      console.log(`slip: Ca ${num.ca.toFixed(0)} N/rad, Ck ${num.ck.toFixed(0)} N, trail ${num.t.toFixed(2)} mm (a = ${num.a.toFixed(2)} mm)`);
      if (!(num.minCa > 0)) errors.push("Ca goes non-positive somewhere in the revolution");
      if (!(num.minT > 0 && num.t < num.a)) errors.push(`the trail is outside (0, a): ${num.t} vs a = ${num.a}`);
      // Ca/Ky is a length; on a real pattern it must be a decent fraction of a
      // and must NOT be constant, or the row would be Ky in disguise.
      const len = num.ca / num.ky;
      if (!(len > 0.2 * num.a && len < num.a))
        errors.push(`Ca/Ky = ${len.toFixed(2)} mm is not a sane fraction of a = ${num.a.toFixed(2)} mm`);
      if (!(num.ratioCov > 1e-4))
        errors.push("Ca is proportional to Ky — the leading-edge weighting is not being applied");
      console.log(`       Ca/Ky = ${len.toFixed(2)} mm, and it varies ${(num.ratioCov * 100).toFixed(2)}% over the revolution`);
    }

    // ---- ten rows is taller than a screen ---------------------------------
    // Two things stop that being a scrolling problem: the pattern is pinned to
    // the foot of the window so any curve can be read against the tread it
    // belongs to, and rows can be switched off so a quick look fits on one
    // screen. Both are checked here because both are geometry, and geometry
    // breaks silently.
    {
      await page.evaluate(() => {
        const gd = document.getElementById("thetaStack");
        window.scrollTo(0, gd.getBoundingClientRect().top + window.scrollY - 40);
      });
      await page.waitForTimeout(500);
      const pinned = await page.evaluate(() => {
        const s = document.getElementById("stripHost").getBoundingClientRect();
        const gd = document.getElementById("thetaStack").getBoundingClientRect();
        return { bottom: Math.round(s.bottom), vh: window.innerHeight,
                 stackBelowFold: gd.bottom > window.innerHeight };
      });
      console.log("pinned strip:", JSON.stringify(pinned));
      if (!pinned.stackBelowFold) errors.push("the stack fits on screen, so this check proves nothing");
      if (Math.abs(pinned.bottom - pinned.vh) > 2)
        errors.push(`the pattern is not pinned to the foot of the window: bottom ${pinned.bottom} of ${pinned.vh}`);
      // Unpinning must let it fall back below the fold.
      await page.uncheck("#pinStrip");
      await page.waitForTimeout(400);
      const loose = await page.$eval("#stripHost", (e) => Math.round(e.getBoundingClientRect().bottom));
      if (!(loose > pinned.vh)) errors.push("unpinning did not release the pattern strip");
      await page.check("#pinStrip");
      await page.waitForTimeout(400);

      // Row chips: one per row, and switching some off rebuilds a shorter stack
      // rather than squashing the rest.
      const chips = await page.$$eval("#rowToggles .rowchip", (n) => n.map((c) => c.dataset.row));
      const rowsBefore = await page.$eval("#thetaStack", (e) =>
        Object.keys(e._fullLayout).filter((k) => /^yaxis\d*$/.test(k)).length);
      console.log("row chips:", chips.join(","), "| rows:", rowsBefore);
      if (chips.length !== rowsBefore)
        errors.push(`${chips.length} chips for ${rowsBefore} rows — the two lists have drifted apart`);
      const hBefore = await page.$eval("#thetaStack", (e) => e.getBoundingClientRect().height);
      for (const k of ["land", "blocks", "centroid", "c_kappa", "trail", "kx"]) {
        await page.click(`#rowToggles .rowchip[data-row="${k}"]`);
        await page.waitForTimeout(150);
      }
      const after = await page.evaluate(() => {
        const e = document.getElementById("thetaStack");
        const ax = Object.keys(e._fullLayout).filter((k) => /^yaxis\d*$/.test(k));
        return { n: ax.length, h: e.getBoundingClientRect().height,
                 titles: ax.map((k) => (e._fullLayout[k].title || {}).text),
                 title: e._fullLayout.title.text };
      });
      console.log("trimmed to:", after.titles.join(" · "), "|", after.title.slice(-18));
      if (after.n !== rowsBefore - 6) errors.push(`turning 6 rows off left ${after.n} of ${rowsBefore}`);
      if (!(after.h < hBefore * 0.7)) errors.push("the stack did not get shorter when rows were switched off");
      if (!/4 of 10 rows shown/.test(after.title)) errors.push("the title does not say how many rows are hidden: " + after.title);
      // Trying to switch off every row must leave one on, with its chip still
      // reading "on" -- a control that says one thing while the chart shows
      // another is worse than the scrolling it was meant to fix.
      for (const k of ["area", "kz", "ky", "c_alpha"]) {
        await page.click(`#rowToggles .rowchip[data-row="${k}"]`);
        await page.waitForTimeout(150);
      }
      const floor = await page.evaluate(() => {
        const e = document.getElementById("thetaStack");
        return { rows: Object.keys(e._fullLayout).filter((k) => /^yaxis\d*$/.test(k)).length,
                 chipsOn: document.querySelectorAll("#rowToggles .rowchip.on").length };
      });
      console.log("with every chip clicked off the stack keeps", floor.rows, "row,", floor.chipsOn, "chip on");
      if (floor.rows !== 1) errors.push(`switching every row off left ${floor.rows} rows`);
      if (floor.chipsOn !== 1) errors.push(`${floor.chipsOn} chips read "on" while 1 row is drawn`);
      // Restore by reading the DOM rather than replaying the clicks: the refusal
      // above means the sequence is no longer a pure toggle.
      const off = await page.$$eval("#rowToggles .rowchip:not(.on)", (n) => n.map((c) => c.dataset.row));
      for (const k of off) {
        await page.click(`#rowToggles .rowchip[data-row="${k}"]`);
        await page.waitForTimeout(120);
      }
      const restored = await page.$eval("#thetaStack", (e) =>
        Object.keys(e._fullLayout).filter((k) => /^yaxis\d*$/.test(k)).length);
      if (restored !== rowsBefore) errors.push(`rows did not come back: ${restored} of ${rowsBefore}`);
    }

    // The metric selectors must offer them and must not blow up when picked.
    for (const [sel, val] of [["#heatMetric", "c_alpha"], ["#orderMetric", "c_kappa"], ["#compareMetric", "pneumatic_trail"]]) {
      const has = await page.$eval(sel, (e, v) => Array.from(e.options).some((o) => o.value === v), val);
      if (!has) errors.push(`${sel} does not offer ${val}`);
    }
    // The selector lives on the lean tab, so the tab has to be open first --
    // Playwright will not drive a control that is not visible.
    await page.click('.tabs button[data-tab="lean"]');
    await page.waitForTimeout(400);
    await page.selectOption("#heatMetric", "c_alpha");
    await page.waitForTimeout(500);
    const heatTitle = await page.$eval("#leanHeat", (e) => e._fullLayout.title.text);
    console.log("lean map metric:", heatTitle);
    if (!/N\/rad/.test(heatTitle)) errors.push("the lean map does not label Cα in N/rad: " + heatTitle);
    await page.selectOption("#heatMetric", "kz");
    await page.click('.tabs button[data-tab="stack"]');
    await page.waitForTimeout(300);
  }

  // ---- a measured contact patch -------------------------------------------
  // The idealised shapes are a guess at the footprint. A traced one is the
  // measurement, and it is neither convex nor symmetric -- so it has to travel
  // through the same placement, clipping, pressure and sweep as any other patch,
  // and give a different answer.
  {
    const info = () => page.$eval("#cpMeasuredInfo", (e) => e.textContent.replace(/\s+/g, " ").trim());
    const runAndRead = async () => {
      await page.click("#runBtn");
      await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 120000 });
      return page.evaluate(() => {
        const r = window.__ttResult();
        const m = (a) => a.reduce((s, v) => s + v, 0) / a.length;
        const cov = (a) => { const u = m(a); return Math.sqrt(m(a.map((v) => (v - u) * (v - u)))) / u; };
        return { src: r.patch.source, prov: r.patch.provenance, area: r.patch_area,
                 contact: m(r.contact_area), cov: cov(r.contact_area),
                 a: r.patch.a, b: r.patch.b, press: r.patch.peak_pressure, load: r.patch.normal_load };
      });
    };
    const ideal = await runAndRead();

    await page.selectOption("#shape", "measured");
    await page.waitForTimeout(250);
    if (!(await page.isDisabled("#runBtn")))
      errors.push("'measured' with no footprint loaded did not block the run");
    const noneMsg = await page.textContent("#specError");
    if (!/no footprint has been loaded/.test(noneMsg))
      errors.push("no useful message when 'measured' is selected with nothing loaded: " + noneMsg.slice(0, 90));
    const greyed = await page.evaluate(() =>
      document.getElementById("cpLength").disabled && document.getElementById("cpWidth").disabled);
    if (!greyed) errors.push("length and width stay live while the size comes from a file");

    await page.setInputFiles("#cpFile", path.join(__dirname, "..", "data", "footprints", "upright_00deg.dxf"));
    await page.waitForTimeout(500);
    console.log("footprint:", (await info()).slice(0, 150));
    if (await page.isChecked("#cpScaleLean"))
      errors.push("importing a footprint left 'scale patch with lean' on — it was measured at one lean");
    if (!/re-centred to y/.test(await info()))
      errors.push("the outline was placed without saying so");

    const meas = await runAndRead();
    console.log(`measured patch: ${meas.area.toFixed(0)} mm² (ideal ${ideal.area.toFixed(0)}),` +
      ` contact ${meas.contact.toFixed(0)}, CoV ${(100 * meas.cov).toFixed(2)}% vs ${(100 * ideal.cov).toFixed(2)}%`);
    if (meas.src !== "measured") errors.push("the patch is not marked as measured: " + meas.src);
    if (!/measured footprint/.test(meas.prov)) errors.push("provenance does not say it was measured: " + meas.prov);
    if (!/upright_00deg/.test(meas.prov)) errors.push("provenance does not name the file: " + meas.prov);
    // the real footprint is a different shape, so it must give a different answer
    if (Math.abs(meas.area - ideal.area) < 1) errors.push("the measured patch has the idealised area");
    if (Math.abs(meas.cov - ideal.cov) < 1e-4)
      errors.push("the measured footprint gave the same fluctuation as the idealised shape");
    // pressure still comes from the load over the real area
    if (Math.abs(meas.press * 4158.4 - meas.load) / meas.load > 2e-3)
      errors.push(`pressure x measured area != load: ${(meas.press * 4158.4).toFixed(1)} vs ${meas.load}`);

    // wrong units must be caught, not silently rescale every area on the page
    await page.selectOption("#cpUnits", "in");
    await page.waitForTimeout(300);
    if (!(await page.isDisabled("#runBtn"))) errors.push("a footprint read as inches did not block the run");
    if (!/Check the units/.test(await info())) errors.push("no units guidance on an implausible footprint");
    await page.selectOption("#cpUnits", "mm");
    await page.waitForTimeout(300);

    // 'as drawn' keeps the file's own y, and says when that is off the tread
    await page.selectOption("#cpLateral", "absolute");
    await page.waitForTimeout(300);
    if (!/outside the/.test(await info())) errors.push("'as drawn' did not warn that the outline is off the tread");
    if (!(await page.isDisabled("#runBtn")))
      errors.push("a patch entirely off the tread did not block the run");
    console.log("as-drawn off the tread:", (await page.textContent("#specError")).replace(/\s+/g, " ").slice(0, 100));

    await page.selectOption("#cpLateral", "auto");
    await page.click("#cpClear");
    await page.selectOption("#shape", "rounded");
    await page.waitForTimeout(250);
    if (await page.isDisabled("#runBtn")) errors.push("clearing the footprint left the run blocked");
    await page.click("#runBtn");
    await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 120000 });
  }

  const tabs = ["lean", "orders", "zones", "patch", "diag", "guide"];
  for (const t of tabs) {
    await page.click(`.tabs button[data-tab="${t}"]`);
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(outDir, `tab-${t}.png`), fullPage: false });
  }

  // Inputs that the pattern derives from must take effect without re-importing
  // the DXF. The crown used to be baked in at load time, so a new crown radius
  // was silently ignored while the lean sweep kept using the old one.
  {
    const leansOf = async () => page.evaluate(() =>
      Array.from(document.getElementById("gammaSel").options).map((o) => o.textContent).join(","));
    const rerun = async () => {
      await page.click("#runBtn");
      await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 60000 });
      return leansOf();
    };
    const before = await leansOf();
    await page.fill("#crownCenter", "300"); await page.fill("#crownShoulder", "300");
    await page.waitForTimeout(150);
    const tightened = await rerun();
    if (tightened === before) errors.push("crown radius change had no effect without re-importing the DXF");
    if (tightened.split(",").length >= before.split(",").length)
      errors.push(`a 300/300 crown should reduce the reachable leans: ${before} -> ${tightened}`);
    await page.fill("#crownCenter", ""); await page.fill("#crownShoulder", "");
    await page.waitForTimeout(150);
    const restored = await rerun();
    if (restored !== before) errors.push(`clearing the crown override should restore the default: ${before} -> ${restored}`);
    console.log(`crown reconciliation: ${before.split(",").length} leans -> ${tightened.split(",").length} -> ${restored.split(",").length}`);
  }

  // ---- the draggable contact-patch band -----------------------------------
  {
    await page.click('.tabs button[data-tab="stack"]');
    await page.evaluate(() => document.getElementById("thetaStack").scrollIntoView({ block: "start" }));
    await page.waitForTimeout(500);
    const theta = () => page.evaluate(() => window.__ttState().patchTheta);
    const xrange = () => page.evaluate(() =>
      document.getElementById("thetaStack")._fullLayout.xaxis.range.map((v) => +v.toFixed(1)));
    const bandBox = () => page.$eval("#thetaStack", (e) => {
      const n = e.parentNode.querySelector("svg.cpov .cpband");
      if (!n) return null;
      const r = n.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + 80, w: r.width };
    });
    const outlineY = () => page.$eval("#patternStrip", (e) => {
      const n = e.parentNode.querySelector("svg.cpov .cpoutline");
      if (!n) return null;
      const ys = n.getAttribute("d").split(/[ML]/).filter(Boolean).map((s) => parseFloat(s.split(",")[1]));
      return [Math.min(...ys).toFixed(2), Math.max(...ys).toFixed(2)].join("..");
    });

    // The band must exist on BOTH figures, and the outline only on the pattern.
    const bands = await page.$$eval("svg.cpov .cpband", (n) => n.length);
    const outlines = await page.$$eval("svg.cpov .cpoutline", (n) => n.length);
    if (bands !== 2) errors.push(`expected one band on each of the two figures, got ${bands}`);
    if (outlines !== 1) errors.push(`expected the patch outline on the pattern strip, got ${outlines}`);

    // Drag it, moving the pointer diagonally on purpose: theta must follow the
    // x component and the lateral position must not move at all.
    const yBefore = await outlineY();
    const t0 = await theta();
    let bb = await bandBox();
    await page.mouse.move(bb.x, bb.y);
    await page.mouse.down();
    await page.mouse.move(bb.x + 250, bb.y + 180, { steps: 12 });
    await page.mouse.up();
    await page.waitForTimeout(300);
    const t1 = await theta();
    if (!(t1 > t0 + 10)) errors.push(`dragging the band right did not advance theta: ${t0} -> ${t1}`);
    if ((await outlineY()) !== yBefore)
      errors.push(`the patch moved laterally during an x drag: ${yBefore} -> ${await outlineY()}`);
    if ((await page.inputValue("#patchTheta")) !== t1.toFixed(1))
      errors.push("the theta box did not follow the drag");
    console.log(`patch band drag: theta ${t0.toFixed(1)} -> ${t1.toFixed(1)}, lateral ${yBefore} unchanged`);

    // Typing an angle moves it too, and straddling the seam draws two pieces
    // on each figure rather than one that runs off the end.
    await page.fill("#patchTheta", "1");
    await page.waitForTimeout(300);
    const seamBands = await page.$$eval("svg.cpov .cpband", (n) => n.length);
    const seamOutlines = await page.$$eval("svg.cpov .cpoutline", (n) => n.length);
    if (seamBands !== 4 || seamOutlines !== 2)
      errors.push(`a patch across the seam should draw both halves: ${seamBands} bands, ${seamOutlines} outlines`);
    console.log(`patch across the seam: ${seamBands} band pieces, ${seamOutlines} outline pieces`);

    // Zoom: the band is pixel geometry, so it has to be recomputed, and both
    // figures must agree.
    await page.fill("#patchTheta", "180");
    await page.evaluate(() => document.getElementById("thetaStack").scrollIntoView({ block: "start" }));
    await page.waitForTimeout(400);
    const wFull = (await bandBox()).w;
    // Drag out a zoom around the band, in coordinates taken from the figure
    // rather than assumed -- earlier steps scroll the page.
    const plot = await page.$eval("#thetaStack", (e) => {
      const r = e.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    });
    // Well inside the first row's data area. Between rows sits Plotly's
    // axis-pan strip, and a drag there pans instead of zooming.
    const zy = plot.y + 80;
    await page.mouse.move(plot.x + plot.w * 0.4, zy);
    await page.mouse.down();
    await page.mouse.move(plot.x + plot.w * 0.65, zy, { steps: 12 });
    await page.mouse.up();
    await page.waitForTimeout(900);
    const wZoom = (await bandBox()).w;
    const wStrip = await page.$eval("#patternStrip", (e) =>
      e.parentNode.querySelector("svg.cpov .cpband").getBoundingClientRect().width);
    if (!(wZoom > wFull * 2)) errors.push(`the band did not widen with the zoom: ${wFull} -> ${wZoom}`);
    if (Math.abs(wZoom - wStrip) > 2) errors.push(`band widths disagree between figures: ${wZoom} vs ${wStrip}`);
    console.log(`patch band with zoom: ${wFull.toFixed(0)} px -> ${wZoom.toFixed(0)} px on both figures`);

    // Double-click ON the band still resets the zoom -- the band takes the
    // pointer, so Plotly never sees that gesture and it has to be handled here.
    bb = await bandBox();
    await page.mouse.dblclick(bb.x, bb.y);
    await page.waitForTimeout(900);
    const r = await xrange();
    if (r[0] !== 0 || r[1] !== 360) errors.push(`double-click on the band did not reset the zoom: ${r}`);
    console.log("double-click on the band resets the zoom:", r);
  }

  // ---- tie bars: detection, per-bar editing, and the wear gate ------------
  // Driven on a purpose-built rib pattern, because the bundled 2W sample has no
  // tie bars -- and the fact that it reports none is itself part of the check.
  {
    if (await page.isVisible("#tbBody"))
      errors.push("the 2W sample has no tie bars but the editor is showing rows");

    const tbDxf = path.join(__dirname, "..", "data", "tbr_ribs_tiebars.dxf");
    await page.setInputFiles("#fileInput", tbDxf);
    await page.waitForTimeout(600);
    await page.fill("#nsd", "16");
    await page.waitForTimeout(200);
    const banner2 = (await page.textContent("#banner")).replace(/\s+/g, " ");
    console.log("tie-bar drawing:", banner2.slice(0, 130));
    if (!/tie bars/.test(banner2)) errors.push("tie bars not reported in the import banner");

    // Bars are grouped by default now -- one row per distinct bar rather than
    // one per bar. Check the grouping, then open the individual list.
    const groupRows = await page.$$eval("#tbGroupTable tr", (r) => Math.max(0, r.length - 1));
    console.log("tie-bar groups:", groupRows);
    if (groupRows !== 1) errors.push(`38 identical bars should be one group, got ${groupRows}`);
    await page.click("#tbIndividual summary");
    await page.waitForTimeout(300);
    const rows = await page.$$eval("#tbTable tr", (r) => r.length - 1);
    console.log("tie bars listed:", rows);
    if (rows !== 38) errors.push(`expected 38 tie bars on the rib drawing, listed ${rows}`);

    // Height edits must stick and move the engagement point.
    const firstAt = () => page.$eval("#tbTable tr:nth-child(2) td:nth-child(8)", (e) => e.textContent.trim());
    const before = await firstAt();
    await page.fill("#tbTable tr:nth-child(2) input[data-tbfield='height']", "12");
    await page.dispatchEvent("#tbTable tr:nth-child(2) input[data-tbfield='height']", "change");
    await page.waitForTimeout(200);
    const after = await firstAt();
    if (before === after) errors.push(`editing a tie-bar height did not move its engagement wear (${before})`);
    console.log("tie bar T000 engages at:", before, "->", after, "after setting height 12 mm");

    // At zero wear nothing is in contact; past the engagement point they are.
    const engaged = async () => (await page.textContent("#tbSummary")).replace(/\s+/g, " ");
    const atZero = await engaged();
    if (!/\b0\b in contact/.test(atZero)) errors.push("tie bars reported in contact at zero wear: " + atZero);
    await page.fill("#tbAllFrac", "0.55");
    await page.click("#tbApplyAll");
    await page.fill("#wear", "9");
    await page.waitForTimeout(250);
    const atNine = await engaged();
    console.log("wear 0:", atZero, "| wear 9:", atNine);
    if (!/\b38\b in contact/.test(atNine)) errors.push("tie bars not engaged at 9 mm wear: " + atNine);

    // And the sweep must actually see them: engaging 38 bars adds contact area
    // and damps the circumferential fluctuation, which is what tie bars do.
    const sweepAt = async (wear) => {
      await page.fill("#wear", String(wear));
      await page.waitForTimeout(150);
      await page.click("#runBtn");
      await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 90000 });
      return page.evaluate(() => {
        const r = window.__ttState ? window.__ttState() : null;
        return r;
      });
    };
    const s0 = await sweepAt(7.0), s1 = await sweepAt(9.0);
    if (!s0 || !s1) {
      errors.push("could not read sweep state for the wear comparison");
    } else {
      console.log(`wear 7.0: area ${s0.area.toFixed(0)} mm2, CoV ${(s0.cov * 100).toFixed(2)}%, ${s0.engaged} bars`);
      console.log(`wear 9.0: area ${s1.area.toFixed(0)} mm2, CoV ${(s1.cov * 100).toFixed(2)}%, ${s1.engaged} bars`);
      if (s0.engaged !== 0) errors.push("bars engaged below their engagement wear");
      if (s1.engaged !== 38) errors.push("bars not engaged above their engagement wear");
      if (!(s1.area > s0.area)) errors.push("engaging tie bars did not increase contact area");
      if (!(s1.cov < s0.cov)) errors.push("engaging tie bars did not damp the contact-area fluctuation");
    }

    // The coupling tab carries the same tread below its curve: dimmed blocks,
    // the bars, and one line per bonded link. Without it the network plot is a
    // pair of curves with no way to see which part of the pattern they belong
    // to. Its x axis is tied to the curve above but NOT to the sweep tab's.
    await page.click('.tabs button[data-tab="coupling"]');
    await page.waitForTimeout(800);
    const cpl = await page.evaluate(() => {
      const e = document.getElementById("cplStrip");
      if (!e || !e._fullLayout) return null;
      const s = e._fullLayout.shapes || [];
      return {
        total: s.length,
        links: s.filter((x) => x.type === "line" && x.xref === "x").length,
        paths: s.filter((x) => x.type === "path").length,
      };
    });
    if (!cpl) {
      errors.push("the coupling tab has no rolled-out pattern below its curve");
    } else {
      console.log(`coupling strip: ${cpl.paths} outlines, ${cpl.links} bonded links`);
      if (cpl.paths < 38) errors.push(`coupling strip drew ${cpl.paths} outlines, expected the whole tread`);
      if (cpl.links < 38) errors.push(`coupling strip drew ${cpl.links} link lines, expected at least one per bar`);
      const thetaBefore = await page.$eval("#patternStrip", (e) => e._fullLayout.xaxis.range.map((v) => +v.toFixed(1)));
      await page.evaluate(() => Plotly.relayout(document.getElementById("cplPlot"), { "xaxis.range": [90, 180] }));
      await page.waitForTimeout(500);
      const stripRange = await page.$eval("#cplStrip", (e) => e._fullLayout.xaxis.range.map((v) => +v.toFixed(1)));
      const thetaAfter = await page.$eval("#patternStrip", (e) => e._fullLayout.xaxis.range.map((v) => +v.toFixed(1)));
      console.log("coupling strip follows the curve's zoom:", stripRange, "sweep tab unchanged:", thetaAfter);
      if (Math.abs(stripRange[0] - 90) > 1 || Math.abs(stripRange[1] - 180) > 1)
        errors.push(`coupling strip did not follow the zoom above it: ${stripRange}`);
      if (String(thetaAfter) !== String(thetaBefore))
        errors.push(`zooming the coupling tab re-framed the sweep tab: ${thetaBefore} -> ${thetaAfter}`);
      await page.screenshot({ path: path.join(outDir, "tab-coupling.png"), fullPage: false });
      await page.evaluate(() => Plotly.relayout(document.getElementById("cplPlot"), { "xaxis.range": [0, 360] }));
      await page.waitForTimeout(300);
    }

    await page.click("#sampleBtn");
    await page.fill("#wear", "0");
    await page.fill("#nsd", "8.5");
    await page.waitForTimeout(200);
    await page.click("#runBtn");
    await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 60000 });
    await page.click('.tabs button[data-tab="stack"]');
  }

  // ---- tie bars the designer coloured in ---------------------------------
  // A HATCH on the TIEBAR layer says "this is a tie bar" outright, which beats
  // any heuristic. The drawing used here has no linework closing the bars at
  // all, so if the hatches are not read there are no bars to find.
  {
    const hatchDxf = path.join(__dirname, "..", "data", "hatch_only.dxf");
    await page.setInputFiles("#fileInput", hatchDxf);
    await page.waitForTimeout(700);
    await page.fill("#nsd", "12");
    await page.waitForTimeout(250);
    const hb = (await page.textContent("#banner")).replace(/\s+/g, " ");
    console.log("hatch drawing:", hb.slice(0, 150));
    if (!/12 tie bars \(12 from TIEBAR HATCH/.test(hb))
      errors.push("hatched tie bars not reported in the import banner: " + hb.slice(0, 200));

    await page.click("#tbIndividual summary");
    await page.waitForTimeout(300);
    const hatchRows = await page.$$eval("#tbTable tr", (r) => r.length - 1);
    if (hatchRows !== 12) errors.push(`expected 12 hatched bars, listed ${hatchRows}`);

    // Each bar carries the colour it was drawn in, on its row and on the plan.
    const swatches = await page.$$eval("#tbTable tr td:first-child span",
      (n) => [...new Set(n.map((s) => s.style.background))]);
    console.log("tie-bar row colours:", swatches.join(" | "));
    if (swatches.length < 3)
      errors.push("hatched bars are all one colour on the table: " + swatches.join(","));
    const planFills = await page.evaluate(() => {
      const e = document.getElementById("tbPlot");
      if (!e || !e._fullLayout) return [];
      return [...new Set((e._fullLayout.shapes || []).map((s) => s.line && s.line.color))];
    });
    if (planFills.length < 4)
      errors.push("the tie-bar plan drew every bar the same colour: " + planFills.join(","));

    // A bar drawn with a hole is a hole, not a solid: its area is the net one.
    const holeInfo = await page.evaluate(() => {
      const p = window.__ttPattern ? window.__ttPattern() : null;
      if (!p) return null;
      const holed = p.tiebars.filter((t) => (t.holes || []).length);
      return { n: holed.length, area: holed.length ? holed[0].area : 0,
               solid: (p.tiebars.find((t) => !(t.holes || []).length) || {}).area };
    });
    if (holeInfo) {
      console.log(`bars with holes: ${holeInfo.n}, net area ${holeInfo.area} vs solid ${holeInfo.solid}`);
      if (holeInfo.n !== 3) errors.push(`expected 3 bars with holes, got ${holeInfo.n}`);
      if (!(holeInfo.area < holeInfo.solid)) errors.push("a bar with a hole reports the solid area");
    }

    // It runs, and the exports that describe the tread are live before any run.
    const dxfBtn = await page.$eval("#exportDxf", (e) => e.disabled);
    const prjBtn = await page.$eval("#saveProject", (e) => e.disabled);
    if (dxfBtn || prjBtn) errors.push("the DXF and project exports are disabled with a pattern loaded");

    await page.fill("#wear", "6");
    await page.waitForTimeout(150);
    await page.click("#runBtn");
    await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 90000 });
    const hs = await page.evaluate(() => (window.__ttState ? window.__ttState() : null));
    if (hs) console.log(`hatch tread swept: area ${hs.area.toFixed(0)} mm2, CoV ${(hs.cov * 100).toFixed(2)}%, ${hs.engaged} bars engaged`);

    // Round trip: save the project, reload it, and the tread must come back the
    // same -- colours, holes and all.
    const dlDir = require("fs").mkdtempSync(require("path").join(require("os").tmpdir(), "tt-prj-"));
    const before = await page.evaluate(() => {
      const p = window.__ttPattern();
      return JSON.stringify({ b: p.blocks.length, t: p.tiebars.length,
        holes: p.tiebars.reduce((s, x) => s + (x.holes || []).length, 0),
        area: +p.tiebars.reduce((s, x) => s + x.area, 0).toFixed(6),
        cols: [...new Set(p.tiebars.map((x) => x.color && x.color.css))].sort().join(",") });
    });
    const [prj] = await Promise.all([
      page.waitForEvent("download"),
      page.click("#saveProject"),
    ]);
    const prjPath = require("path").join(dlDir, prj.suggestedFilename());
    await prj.saveAs(prjPath);
    console.log("project file:", prj.suggestedFilename(),
      `(${Math.round(require("fs").statSync(prjPath).size / 1024)} KB)`);

    const [dxfOut] = await Promise.all([
      page.waitForEvent("download"),
      page.click("#exportDxf"),
    ]);
    const dxfPath = require("path").join(dlDir, dxfOut.suggestedFilename());
    await dxfOut.saveAs(dxfPath);
    const dxfText = require("fs").readFileSync(dxfPath, "utf8");
    console.log("DXF export:", dxfOut.suggestedFilename(),
      `${(dxfText.match(/\nHATCH\n/g) || []).length} HATCH entities on TIEBAR`);
    if ((dxfText.match(/\nHATCH\n/g) || []).length !== 12)
      errors.push("the exported DXF does not carry one HATCH per tie bar");

    await page.click("#sampleBtn");
    await page.waitForTimeout(400);
    await page.setInputFiles("#loadProject", prjPath);
    await page.waitForTimeout(900);
    const after = await page.evaluate(() => {
      const p = window.__ttPattern();
      return JSON.stringify({ b: p.blocks.length, t: p.tiebars.length,
        holes: p.tiebars.reduce((s, x) => s + (x.holes || []).length, 0),
        area: +p.tiebars.reduce((s, x) => s + x.area, 0).toFixed(6),
        cols: [...new Set(p.tiebars.map((x) => x.color && x.color.css))].sort().join(",") });
    });
    console.log("project round trip:", before === after ? "identical" : before + "  ->  " + after);
    if (before !== after) errors.push("reloading the project did not restore the tread");

    await page.click("#sampleBtn");
    await page.fill("#wear", "0");
    await page.fill("#nsd", "8.5");
    await page.waitForTimeout(200);
    await page.click("#runBtn");
    await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 60000 });
    await page.click('.tabs button[data-tab="stack"]');
  }

  // ---- the report: what goes in it, and does it come out undistorted -------
  {
    const chips = await page.$$eval("#reportSections .rowchip",
      (n) => n.map((c) => ({ k: c.dataset.sec, on: c.classList.contains("on"), off: c.classList.contains("off") })));
    console.log("report sections:", chips.map((c) => c.k + (c.off ? "(n/a)" : c.on ? "" : "[off]")).join(" "));
    if (!chips.length) errors.push("the report has no section list");
    // A chart section must only be offered when that chart exists
    const stackChip = chips.find((c) => c.k === "stack");
    if (!stackChip || stackChip.off) errors.push("the theta sweep is not offered as a report section");
    const cplChip = chips.find((c) => c.k === "coupling");
    if (!cplChip || !cplChip.off)
      errors.push("the coupling section is offered on a drawing with no tie bars");

    const dl2 = require("fs").mkdtempSync(require("path").join(require("os").tmpdir(), "tt-rep-"));
    const grab = async (id, tag) => {
      const [d] = await Promise.all([page.waitForEvent("download"), page.click(id)]);
      const f = require("path").join(dl2, tag + "-" + d.suggestedFilename());
      await d.saveAs(f);
      return f;
    };
    const pdfAll = await grab("#exportPdf", "all");
    const packAll = await grab("#exportPack", "all");
    const pageCount = (f) =>
      (fs.readFileSync(f).toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length;
    console.log(`report: PDF ${(fs.statSync(pdfAll).size / 1024).toFixed(0)} KB / ${pageCount(pdfAll)} pages,` +
                ` pack ${(fs.statSync(packAll).size / 1024 / 1024).toFixed(2)} MB`);
    if (pageCount(pdfAll) < 4) errors.push("the full report is only " + pageCount(pdfAll) + " pages");

    // Switching sections off must actually shorten it.
    for (const k of ["perlean", "notes", "lean", "orders", "zones", "patch", "cover"]) {
      const sel = `#reportSections .rowchip[data-sec="${k}"]`;
      if (await page.$(sel)) { await page.click(sel); await page.waitForTimeout(60); }
    }
    const pdfCut = await grab("#exportPdf", "cut");
    const packCut = await grab("#exportPack", "cut");
    console.log(`  trimmed: PDF ${pageCount(pdfAll)} -> ${pageCount(pdfCut)} pages`);
    if (!(pageCount(pdfCut) < pageCount(pdfAll)))
      errors.push("switching report sections off did not shorten the PDF");

    // The Greek that jsPDF's built-in fonts cannot encode must not reach the
    // page as punctuation: theta and gamma are spelled out in PDF text.
    const pdfTxt = fs.readFileSync(pdfCut).toString("latin1");
    if (/\(theta/.test(pdfTxt) === false && /theta/.test(pdfTxt) === false)
      errors.push("the PDF does not spell out theta, so the Greek is being emitted unencodable");

    // The review pack has to open on its own, with the charts still live and
    // nothing fetched from anywhere.
    const rv = await ctx.newPage();
    const rvErr = [];
    rv.on("pageerror", (e) => rvErr.push(e.message));
    rv.on("console", (m) => { if (m.type() === "error") rvErr.push(m.text()); });
    await rv.goto("file://" + packCut, { waitUntil: "load" });
    await rv.waitForTimeout(1500);
    const pack = await rv.evaluate(() => {
      const figs = Array.from(document.querySelectorAll("[id^=fig]"));
      return {
        figs: figs.length,
        drawn: figs.filter((f) => f.querySelector(".plot-container")).length,
        headings: Array.from(document.querySelectorAll(".fig h2")).map((h) => h.textContent),
        conf: !!document.querySelector(".conf"),
        external: performance.getEntriesByType("resource").filter((r) => !r.name.startsWith("file:")).length,
        greek: /θ/.test(document.body.textContent),
      };
    });
    console.log("  review pack:", JSON.stringify(pack));
    if (!pack.figs || pack.drawn !== pack.figs)
      errors.push(`review pack drew ${pack.drawn} of ${pack.figs} figures`);
    if (pack.external) errors.push("the review pack fetched " + pack.external + " external resource(s)");
    if (!pack.conf) errors.push("the review pack carries no confidentiality notice");
    if (!pack.greek) errors.push("the review pack lost the Greek symbols the PDF has to spell out");
    // and it is genuinely interactive, not a picture
    await rv.evaluate(() => Plotly.relayout("fig0", { "xaxis.range": [90, 180] }));
    await rv.waitForTimeout(300);
    const rng = await rv.$eval("#fig0", (e) => e._fullLayout.xaxis.range.map((v) => +v.toFixed(0)));
    if (rng[0] !== 90 || rng[1] !== 180) errors.push("the review pack's charts are not interactive: " + rng);
    console.log("  pack chart zooms:", rng, rvErr.length ? "ERRORS " + rvErr.join(" | ") : "no errors");
    if (rvErr.length) errors.push("review pack page errors: " + rvErr.join(" | "));
    await rv.close();

    // put every section back so later checks see the normal page
    for (const k of ["perlean", "notes", "lean", "orders", "zones", "patch", "cover"]) {
      const sel = `#reportSections .rowchip[data-sec="${k}"]:not(.on)`;
      if (await page.$(sel)) { await page.click(sel); await page.waitForTimeout(60); }
    }
  }

  // export: every format must download and be self-describing
  const dlDir = require("fs").mkdtempSync(require("path").join(require("os").tmpdir(), "tt-"));
  for (const [id, label] of [["exportCsv", "CSV"], ["exportJson", "JSON"], ["exportTxt", "Summary"]]) {
    if (await page.isDisabled("#" + id)) { errors.push(`${label} export still disabled after a run`); continue; }
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#" + id)]);
    const f = require("path").join(dlDir, dl.suggestedFilename());
    await dl.saveAs(f);
    const txt = fs.readFileSync(f, "utf8");
    if (!txt.length) errors.push(`${label} export was empty`);
    if (id === "exportCsv" && !/^gamma_deg,theta_deg,/m.test(txt)) errors.push("CSV lacks its column header");
    if (id === "exportJson") { const j = JSON.parse(txt); if (!j.results || !j.settings) errors.push("JSON export missing results/settings"); }
    console.log(`export ${label}: ${dl.suggestedFilename()} (${(txt.length / 1024).toFixed(0)} KB)`);
  }

  // drag the editor centre handle to move y_center, verify input updates
  // (scroll it into view first -- the mouse works in viewport coordinates, and
  // the setup sections are tall enough to push it below the fold)
  await page.evaluate(() => document.getElementById("editor").scrollIntoView({ block: "center" }));
  await page.waitForTimeout(300);
  const before = await page.inputValue("#cpY");
  const box = await page.$eval("#editor", (el) => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await page.mouse.move(box.x + box.w / 2, box.y + box.h / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.w / 2, box.y + box.h / 2 - 30, { steps: 5 });
  await page.mouse.up();
  const after = await page.inputValue("#cpY");
  console.log("drag y_center:", before, "->", after, "(auto unchecked:", !(await page.isChecked("#cpAutoY")), ")");

  console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "no page errors");
  await browser.close();
  process.exitCode = errors.length ? 1 : 0;
})();
