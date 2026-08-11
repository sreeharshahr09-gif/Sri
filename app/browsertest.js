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

  // ---- tie bars: detection, per-bar editing, and the wear gate ------------
  // Driven on a purpose-built rib pattern, because the bundled 2W sample has no
  // tie bars -- and the fact that it reports none is itself part of the check.
  {
    if (!/^\s*$/.test(await page.textContent("#tbCount")))
      errors.push("the 2W sample has no tie bars but the tab shows a count");

    const tbDxf = path.join(__dirname, "..", "data", "tbr_ribs_tiebars.dxf");
    await page.setInputFiles("#fileInput", tbDxf);
    await page.waitForTimeout(600);
    await page.fill("#nsd", "16");
    await page.waitForTimeout(200);
    await page.click('.tabs button[data-tab="tiebars"]').catch(() => {});
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
    await page.click('.tabs button[data-tab="tiebars"]');
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
