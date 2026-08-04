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

  const tabs = ["lean", "orders", "zones", "patch", "diag", "guide"];
  for (const t of tabs) {
    await page.click(`.tabs button[data-tab="${t}"]`);
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(outDir, `tab-${t}.png`), fullPage: false });
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
