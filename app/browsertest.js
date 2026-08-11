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

    await page.click("#sampleBtn");
    await page.fill("#wear", "0");
    await page.fill("#nsd", "8.5");
    await page.waitForTimeout(200);
    await page.click("#runBtn");
    await page.waitForFunction(() => !document.querySelector("#overlay").classList.contains("on"), { timeout: 60000 });
    await page.click('.tabs button[data-tab="stack"]');
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
