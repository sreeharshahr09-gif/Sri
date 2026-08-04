/* tread_tool -- main-thread application logic.
 * Uses window.TreadEngine (same engine the worker runs) for the live patch
 * editor and metrics, and Plotly (embedded) for the charts. */
(function () {
  "use strict";
  var E = window.TreadEngine;
  var Plotly = window.Plotly;

  // ---- application state ----------------------------------------------
  var state = {
    pattern: null,       // parsed Pattern
    report: null,        // DXF import report
    results: null,       // array of per-lean results from the worker
    stiffness: null,     // per-zone stiffness summary
    grid: null,          // {nx, ny, dx, dy} actually used
    gammaShown: 0,       // which lean the theta-stack shows
    heatMetric: "kz",
    orderMetric: "kz",
    worker: null,
    editorTheta: 0,      // reference viewing angle for the editor window (deg)
    running: false,
  };

  var DEFAULT_LEANS = [0, 5, 10, 15, 20, 25, 30, 35, 40];

  // ---- element helpers -------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function num(id) { return parseFloat($(id).value); }
  function on(el, ev, fn) { el.addEventListener(ev, fn); }

  // ---- theme -----------------------------------------------------------
  function currentTheme() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("tread_theme", theme); } catch (e) {}
    $("themeToggle").textContent = theme === "light" ? "☾ Dark" : "☀ Light";
    drawEditor();
    if (state.results) renderAll();
  }
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem("tread_theme"); } catch (e) {}
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    $("themeToggle").textContent = currentTheme() === "light" ? "☾ Dark" : "☀ Light";
    on($("themeToggle"), "click", function () { applyTheme(currentTheme() === "light" ? "dark" : "light"); });
  }

  function plotTheme() {
    return {
      paper_bgcolor: cssVar("--panel"),
      plot_bgcolor: cssVar("--panel"),
      font: { color: cssVar("--ink"), size: 12 },
      grid: cssVar("--grid"),
      accent: cssVar("--accent"),
      accent2: cssVar("--accent-2"),
      good: cssVar("--good"), warn: cssVar("--warn"), bad: cssVar("--bad"),
      inkDim: cssVar("--ink-dim"),
    };
  }

  // ---- reading controls into spec / params -----------------------------
  function readSpec() {
    return {
      shape: $("shape").value,
      length: num("cpLength"),
      width: num("cpWidth"),
      corner_radius: num("cpCorner"),
      exponent: num("cpExp"),
      taper: num("cpTaper"),
      rotation: num("cpRot"),
      y_center: $("cpAutoY").checked ? null : num("cpY"),
      gamma_deg: 0,
      load_N: null,
      label: "",
      scale_with_lean: $("cpScaleLean").checked,
    };
  }
  function readStiffParams() {
    return {
      shore_a: num("shore"), poisson: num("poisson"), mode: $("mode").value,
      bulk_modulus: 1100, n_slices: 40, sipe_model: $("sipeModel").value,
    };
  }
  function readCpParams() {
    // The visible checkbox drives the real parameter. It used to be wired to a
    // hidden input that was always checked, so the box the user could see was
    // labelled "load rises with lean" while doing something else entirely.
    return { vertical_load: num("cpLoad"), wheel_radius: num("wheelR"),
             load_rises_with_lean: $("cpAutoLoad").checked };
  }
  function readDefaults() {
    return { height: num("nsd"), draft_angle: num("draft"), n_lateral_sipes: parseInt($("sipes").value, 10), sipe_depth_fraction: num("sipeDepth"), shore_a: null };
  }

  // ---- shape parameter visibility --------------------------------------
  function syncShapeFields() {
    var s = $("shape").value;
    $("rowCorner").style.display = s === "rounded" ? "" : "none";
    $("rowExp").style.display = s === "superellipse" ? "" : "none";
    $("rowTaper").style.display = s === "trapezoid" ? "" : "none";
  }

  // ---- DXF loading -----------------------------------------------------
  function buildPatternFromText(text, name) {
    var defaults = readDefaults();
    var opts = { name: name };
    var cr = $("crownCenter").value, csh = $("crownShoulder").value;
    if (cr) opts.crown_r_center = parseFloat(cr);
    if (csh) opts.crown_r_shoulder = parseFloat(csh);
    var np = $("nPitches").value; if (np) opts.n_pitches = parseInt(np, 10);
    var out = E.loadPattern(text, defaults, opts);
    state.pattern = out.pattern;
    state.report = out.report;
    state.results = null;
    state.editorTheta = 0;
    refreshExportButtons();
    renderBanner();
    drawEditor();
    $("emptyHint").style.display = "none";
    refreshValidation();
  }

  function loadFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try { buildPatternFromText(String(reader.result), file.name.replace(/\.dxf$/i, "")); }
      catch (err) {
        // Drop the old pattern: leaving it loaded makes the banner describe one
        // tyre while the user believes they are looking at another.
        state.pattern = null; state.results = null; refreshExportButtons();
        $("banner").style.display = "none";
        $("emptyHint").style.display = "";
        $("emptyHint").innerHTML = "<b>Could not read that DXF.</b><br>" + escapeHtml(err.message);
        drawEditor();
        refreshValidation();
      }
    };
    reader.readAsText(file);
  }

  // Rebuild the pattern when depth defaults change (they are baked into blocks).
  function rebuildIfLoaded() {
    if (!state.pattern) return;
    // Re-apply per-zone depth defaults onto existing blocks without re-parsing.
    var d = readDefaults();
    for (var i = 0; i < state.pattern.blocks.length; i++) {
      var b = state.pattern.blocks[i];
      b.height = d.height; b.draft_angle = d.draft_angle;
      b.n_lateral_sipes = d.n_lateral_sipes; b.sipe_depth_fraction = d.sipe_depth_fraction;
    }
  }

  // ---- banner ----------------------------------------------------------
  function renderBanner() {
    var b = $("banner");
    if (!state.pattern) { b.style.display = "none"; return; }
    b.style.display = "";
    var r = state.report, p = state.pattern;
    var warn = r.warnings && r.warnings.length;
    b.className = "banner" + (warn ? " warn" : "");
    var html = "<span class='src'>" + p.source.toUpperCase() + "</span><b>" + escapeHtml(p.name) + "</b> — " +
      r.n_blocks + " blocks (" + r.n_wrapped + " wrapped), " +
      p.tyre_circumference.toFixed(1) + " × " + p.tread_width.toFixed(1) + " mm, land ratio " + r.land_ratio.toFixed(3) +
      ", " + p.pitches.length + " pitches";
    if (p.meta && p.meta.geometric_repeat_mm) html += ", geometric repeat " + p.meta.geometric_repeat_mm.toFixed(1) + " mm";
    if (warn) {
      html += "<ul>";
      for (var i = 0; i < r.warnings.length; i++) html += "<li>" + escapeHtml(r.warnings[i]) + "</li>";
      html += "</ul>";
    }
    b.innerHTML = html;
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // ---- the draggable patch editor --------------------------------------
  var editor = { canvas: null, ctx: null, drag: null, scale: 1, ox: 0, oy: 0, winX: 200 };

  function editorSetup() {
    editor.canvas = $("editor");
    editor.ctx = editor.canvas.getContext("2d");
    on(editor.canvas, "pointerdown", editorDown);
    on(editor.canvas, "pointermove", editorMove);
    on(editor.canvas, "pointerup", editorUp);
    on(editor.canvas, "pointerleave", editorUp);
    window.addEventListener("resize", function () { drawEditor(); });
  }

  // data(mm) -> canvas(px). x is circumferential relative to window centre.
  function mapX(x) { return editor.ox + x * editor.scale; }
  function mapY(y) { return editor.oy - y * editor.scale; }
  function invX(px) { return (px - editor.ox) / editor.scale; }
  function invY(py) { return (editor.oy - py) / editor.scale; }

  function drawEditor() {
    if (!editor.ctx) return;
    var c = editor.canvas, ctx = editor.ctx;
    var rect = c.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    c.width = Math.round(rect.width * dpr); c.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var W = rect.width, H = rect.height;
    ctx.clearRect(0, 0, W, H);

    if (!state.pattern) {
      ctx.fillStyle = cssVar("--ink-dim"); ctx.font = "13px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("Load a DXF to place the contact patch", W / 2, H / 2);
      return;
    }
    var p = state.pattern, tw = p.tread_width, C = p.tyre_circumference;
    var spec = readSpec();
    var patch;
    try {
      patch = E.shapePatch(Object.assign({}, spec, { gamma_deg: state.gammaShown }), p.crown, tw, readCpParams());
      setSpecError("");
    } catch (err) {
      // Half-typed or invalid geometry: say why, keep the last good drawing off
      // the screen rather than throwing inside a paint handler.
      setSpecError(err.message);
      ctx.fillStyle = cssVar("--bad"); ctx.font = "12px sans-serif"; ctx.textAlign = "center";
      wrapText(ctx, err.message, W / 2, H / 2 - 10, W - 24, 15);
      return;
    }

    // window: circumferential span sized to the patch, vertical = full tread + margin
    var patchLen = E.patchLength(patch);
    editor.winX = Math.max(160, patchLen * 2.2);
    var winY = tw * 1.08;
    var sx = (W - 20) / editor.winX, sy = (H - 20) / winY;
    editor.scale = Math.min(sx, sy);
    editor.ox = W / 2; editor.oy = H / 2;

    var xref = (state.editorTheta / 360) * C;

    // tread band
    ctx.fillStyle = cssVar("--panel-2");
    ctx.fillRect(mapX(-editor.winX / 2), mapY(tw / 2), editor.winX * editor.scale, tw * editor.scale);
    // centreline
    ctx.strokeStyle = cssVar("--edge"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(mapX(-editor.winX / 2), mapY(0)); ctx.lineTo(mapX(editor.winX / 2), mapY(0)); ctx.stroke();

    // blocks intersecting the window
    var zoneColor = { center: cssVar("--accent"), intermediate: cssVar("--good"), shoulder: cssVar("--accent-2") };
    ctx.globalAlpha = 0.32;
    for (var bi = 0; bi < p.blocks.length; bi++) {
      var blk = p.blocks[bi];
      var pieces = E.splitAtSeam(blk.polygon, C);
      for (var pc = 0; pc < pieces.length; pc++) {
        // also try shifted by +/- C so wrap shows in the window
        for (var shift = -C; shift <= C; shift += C) {
          var poly = pieces[pc];
          var xmin = Infinity, xmax = -Infinity;
          for (var v = 0; v < poly.length; v++) { var xx = poly[v][0] + shift - xref; if (xx < xmin) xmin = xx; if (xx > xmax) xmax = xx; }
          if (xmax < -editor.winX / 2 || xmin > editor.winX / 2) continue;
          ctx.fillStyle = zoneColor[blk.zone] || cssVar("--accent");
          ctx.beginPath();
          for (var v2 = 0; v2 < poly.length; v2++) {
            var X = mapX(poly[v2][0] + shift - xref), Y = mapY(poly[v2][1]);
            if (v2 === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
          }
          ctx.closePath(); ctx.fill();
        }
      }
    }
    ctx.globalAlpha = 1;

    // patch outline
    ctx.strokeStyle = cssVar("--accent"); ctx.lineWidth = 2;
    ctx.fillStyle = hexA(cssVar("--accent"), 0.16);
    ctx.beginPath();
    for (var i = 0; i < patch.outline.length; i++) {
      var pt = patch.outline[i];
      var X2 = mapX(pt[0]), Y2 = mapY(pt[1]);
      if (i === 0) ctx.moveTo(X2, Y2); else ctx.lineTo(X2, Y2);
    }
    ctx.closePath(); ctx.fill(); ctx.stroke();

    // handles
    var yc = patch.y_center, a = patch.a, b = patch.b;
    editor._handles = {
      center: [0, yc], east: [a, yc], north: [0, yc + b],
    };
    ctx.fillStyle = cssVar("--accent");
    for (var hk in editor._handles) {
      var hh = editor._handles[hk];
      ctx.beginPath(); ctx.arc(mapX(hh[0]), mapY(hh[1]), 5, 0, 2 * Math.PI); ctx.fill();
    }
    // labels
    ctx.fillStyle = cssVar("--ink-dim"); ctx.font = "11px sans-serif"; ctx.textAlign = "left";
    ctx.fillText(E.describeSpec(spec) + "  |  y_c=" + yc.toFixed(1) + " mm  |  γ=" + state.gammaShown + "°", 8, 14);
  }

  // Every numeric input, checked on the main thread before anything runs.
  // An empty or nonsense field used to sail through as NaN and come back as
  // "Mean vertical Kz: NaN" -- a wrong answer presented as an answer.
  var FIELD_RULES = [
    ["nsd", "NSD (block height)", "mm", function (v) { return v > 0; }, "must be greater than 0"],
    ["draft", "Draft angle", "deg", function (v) { return Math.abs(v) < 90; }, "must be between -90 and 90"],
    ["sipes", "Lateral sipes", "", function (v) { return v >= 0 && v <= 50 && v % 1 === 0; }, "must be a whole number from 0 to 50"],
    ["sipeDepth", "Sipe depth fraction", "", function (v) { return v >= 0 && v <= 1; }, "must be between 0 and 1"],
    ["shore", "Shore A hardness", "", function (v) { return v > 0 && v < 100; }, "must be between 0 and 100"],
    ["poisson", "Poisson ratio", "", function (v) { return v > 0 && v < 0.5; }, "must be between 0 and 0.5 (rubber is ~0.49)"],
    ["cpLoad", "Vertical load", "N", function (v) { return v > 0; }, "must be greater than 0"],
    ["wheelR", "Wheel radius", "mm", function (v) { return v > 0; }, "must be greater than 0"],
    ["cpLength", "Patch length", "mm", function (v) { return v > 0; }, "must be greater than 0"],
    ["cpWidth", "Patch width", "mm", function (v) { return v > 0; }, "must be greater than 0"],
  ];
  var OPTIONAL_RULES = [
    ["crownCenter", "Crown radius (centre)", "mm", function (v) { return v > 0; }, "must be greater than 0"],
    ["crownShoulder", "Crown radius (shoulder)", "mm", function (v) { return v > 0; }, "must be greater than 0"],
    ["nPitches", "Pitch count", "", function (v) { return v >= 1 && v % 1 === 0; }, "must be a whole number of 1 or more"],
  ];

  function collectInputErrors() {
    var errs = [];
    FIELD_RULES.forEach(function (r) {
      var el = $(r[0]);
      if (!el) return;
      var raw = el.value.trim();
      if (raw === "") { errs.push(r[1] + " is required"); return; }
      var v = parseFloat(raw);
      if (!isFinite(v)) { errs.push(r[1] + " must be a number"); return; }
      if (!r[3](v)) errs.push(r[1] + " " + r[4] + (r[2] ? " (" + r[2] + ")" : ""));
    });
    OPTIONAL_RULES.forEach(function (r) {
      var el = $(r[0]);
      if (!el) return;
      var raw = el.value.trim();
      if (raw === "") return;              // blank means "use the default"
      var v = parseFloat(raw);
      if (!isFinite(v)) { errs.push(r[1] + " must be a number"); return; }
      if (!r[3](v)) errs.push(r[1] + " " + r[4]);
    });
    if (!$("cpAutoY").checked) {
      var y = parseFloat($("cpY").value);
      if (!isFinite(y)) errs.push("Lateral centre y must be a number");
    }
    // shape-specific geometry, from the same validator the engine uses
    if (!errs.length) {
      try { E.validateSpec(readSpec()); }
      catch (err) { errs.push(err.message); }
    }
    return errs;
  }

  // Single place that decides whether the tool is in a runnable state.
  function refreshValidation() {
    var errs = collectInputErrors();
    var el = $("specError");
    if (el) {
      el.innerHTML = errs.length
        ? "<b>Fix before running:</b><ul style='margin:4px 0 0;padding-left:18px'>" +
          errs.map(function (e) { return "<li>" + escapeHtml(e) + "</li>"; }).join("") + "</ul>"
        : "";
      el.style.display = errs.length ? "" : "none";
    }
    $("runBtn").disabled = !state.pattern || errs.length > 0 || state.running;
    return errs;
  }

  function setSpecError(msg) {
    // kept for the editor's own paint-time failures; folds into the same panel
    var el = $("specError");
    if (!el || !msg) { refreshValidation(); return; }
    el.innerHTML = escapeHtml(msg);
    el.style.display = "";
    $("runBtn").disabled = true;
  }

  function wrapText(ctx, text, cx, cy, maxWidth, lineHeight) {
    var words = String(text).split(" "), line = "", lines = [];
    for (var i = 0; i < words.length; i++) {
      var test = line ? line + " " + words[i] : words[i];
      if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = words[i]; }
      else line = test;
    }
    if (line) lines.push(line);
    var y = cy - ((lines.length - 1) * lineHeight) / 2;
    for (var j = 0; j < lines.length; j++) ctx.fillText(lines[j], cx, y + j * lineHeight);
  }

  function hexA(hex, a) {
    hex = hex.trim();
    if (hex[0] !== "#") return hex;
    var r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), bb = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + bb + "," + a + ")";
  }

  function editorDown(ev) {
    if (!state.pattern) return;
    editor.canvas.setPointerCapture(ev.pointerId);
    var rect = editor.canvas.getBoundingClientRect();
    var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    var best = null, bestD = 14;
    for (var hk in editor._handles) {
      var hh = editor._handles[hk];
      var d = Math.hypot(px - mapX(hh[0]), py - mapY(hh[1]));
      if (d < bestD) { bestD = d; best = hk; }
    }
    editor.drag = best || "pan";
    editor.canvas.classList.add("dragging");
  }

  function editorMove(ev) {
    if (!editor.drag) return;
    var rect = editor.canvas.getBoundingClientRect();
    var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    var dx = invX(px), dy = invY(py);
    if (editor.drag === "center") {
      $("cpAutoY").checked = false; $("cpY").disabled = false;
      $("cpY").value = clamp(dy, -state.pattern.tread_width / 2, state.pattern.tread_width / 2).toFixed(1);
    } else if (editor.drag === "east") {
      $("cpLength").value = Math.max(6, Math.abs(dx) * 2).toFixed(1);
    } else if (editor.drag === "north") {
      var yc = $("cpAutoY").checked ? E.crownContactLateral(state.pattern.crown, state.gammaShown) : num("cpY");
      $("cpWidth").value = Math.max(6, Math.abs(dy - yc) * 2).toFixed(1);
    }
    drawEditor();
  }

  function editorUp(ev) {
    if (!editor.drag) return;
    editor.drag = null;
    editor.canvas.classList.remove("dragging");
    // a shape change invalidates results -> offer re-run
    markStale();
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function markStale() {
    if (state.results) $("runBtn").textContent = "▶ Re-run (inputs changed)";
  }

  // ---- run: worker orchestration ---------------------------------------
  function makeWorker() {
    var engineSrc = $("engine-src").textContent;
    var workerSrc = $("worker-src").textContent;
    var blob = new Blob([engineSrc + "\n" + workerSrc], { type: "text/javascript" });
    return new Worker(URL.createObjectURL(blob));
  }

  function run() {
    if (!state.pattern || state.running) return;
    if (refreshValidation().length) return;   // never compute on invalid input
    rebuildIfLoaded();
    state.running = true;
    $("overlay").classList.add("on");
    $("progress").textContent = "";
    var nx = parseInt($("quality").value, 10);
    var ny = Math.max(48, Math.round(state.pattern.tread_width / (state.pattern.tyre_circumference / nx)));
    // keep transported arrays reasonable: stride so ~1024 points per curve
    var stride = Math.max(1, Math.round(nx / 1024));
    if (state.worker) state.worker.terminate();
    state.worker = makeWorker();
    state.worker.onmessage = function (ev) {
      var m = ev.data;
      if (m.type === "progress") { $("progress").textContent = "lean " + m.done + " / " + m.total; }
      else if (m.type === "done") {
        var bad = resultsAreFinite(m.results);
        if (bad) {
          failRun("the sweep produced non-finite values in '" + bad + "'. This is a bug — " +
            "please report the DXF and the settings that triggered it.");
          return;
        }
        state.results = m.results; state.stiffness = m.stiffness; state.grid = m.grid;
        state.notes = m.notes || []; state.maxLean = m.maxLean;
        state.running = false; $("overlay").classList.remove("on");
        $("runBtn").textContent = "▶ Run";
        $("timing").textContent = "computed in " + (m.timing.total / 1000).toFixed(1) + " s (raster " + m.timing.raster + " ms), grid " + m.grid.nx + "×" + m.grid.ny;
        populateGammaSelect();
        renderAll();
      } else if (m.type === "error") {
        failRun(m.message);
      }
    };
    // Without these a worker that dies outside its own try/catch (a parse
    // failure, an out-of-memory kill) never posts anything, and the overlay
    // spins forever with no way back.
    state.worker.onerror = function (ev) {
      ev.preventDefault();
      failRun(ev.message || "the compute worker stopped unexpectedly");
    };
    state.worker.onmessageerror = function () {
      failRun("the compute worker sent a message that could not be read");
    };
    state.worker.postMessage({
      cmd: "sweep", pattern: state.pattern, gridNx: nx, gridNy: ny,
      stiffParams: readStiffParams(), cpParams: readCpParams(), spec: readSpec(),
      leans: DEFAULT_LEANS, discreteSamples: 360, curvatureCorrection: $("curv").checked, stride: stride,
    });
  }

  // One exit path for every way a run can fail, so the UI can never be left
  // spinning with no explanation.
  function failRun(message) {
    if (state.worker) { state.worker.terminate(); state.worker = null; }
    state.running = false;
    $("overlay").classList.remove("on");
    $("runBtn").textContent = "▶ Run";
    var el = $("specError");
    if (el) {
      el.innerHTML = "<b>Compute failed:</b> " + escapeHtml(String(message));
      el.style.display = "";
    }
    refreshValidation();
  }

  // Results that arrive non-finite are a bug, not a finding -- say so rather
  // than drawing "NaN" on a chart as though it were a measurement.
  function resultsAreFinite(results) {
    for (var i = 0; i < results.length; i++) {
      var r = results[i];
      var keys = ["contact_area", "kx", "ky", "kz", "block_count"];
      for (var k = 0; k < keys.length; k++) {
        var a = r[keys[k]];
        for (var j = 0; j < a.length; j++) if (!isFinite(a[j])) return keys[k];
      }
    }
    return null;
  }

  function populateGammaSelect() {
    var sel = $("gammaSel"); sel.innerHTML = "";
    for (var i = 0; i < state.results.length; i++) {
      var o = document.createElement("option");
      o.value = i; o.textContent = state.results[i].gamma_deg + "°";
      sel.appendChild(o);
    }
    sel.value = 0; state.gammaShown = state.results[0].gamma_deg;
  }

  // ---- rendering all charts --------------------------------------------
  function currentResult() {
    if (!state.results) return null;
    for (var i = 0; i < state.results.length; i++) if (state.results[i].gamma_deg === state.gammaShown) return state.results[i];
    return state.results[0];
  }

  function renderAll() {
    if (!state.results) return;
    renderNotes();
    refreshExportButtons();
    renderCards();
    renderThetaStack();
    renderPatternStrip();
    renderLeanHeatmap();
    renderOrders();
    renderZones();
    renderPatchPreview();
    renderDiagnostics();
    drawEditor();
  }

  // Physics caveats raised by the compute pass (skipped leans, clipping,
  // out-of-range compound).  Shown above the charts so a number is never read
  // without the reason it might not mean what it looks like.
  function renderNotes() {
    var el = $("notes");
    if (!state.notes || !state.notes.length) { el.style.display = "none"; el.innerHTML = ""; return; }
    el.style.display = "";
    el.className = "banner warn";
    var html = "<b>Physics notes</b><ul>";
    for (var i = 0; i < state.notes.length; i++) html += "<li>" + escapeHtml(state.notes[i]) + "</li>";
    el.innerHTML = html + "</ul>";
  }

  function renderCards() {
    var r = currentResult();
    var kz = E.fluctuationStats(r.kz), ca = E.fluctuationStats(r.contact_area), bc = E.fluctuationStats(r.block_count);
    var spec = E.orderSpectrum(r.kz, 60), dom = E.dominantOrders(spec, 1)[0];
    var cards = [
      ["Patch area", r.patch_area.toFixed(0), "mm²"],
      ["Mean contact area", ca.mean.toFixed(0), "mm² (" + (100 * ca.mean / r.patch_area).toFixed(0) + "% land)"],
      ["Mean vertical Kz", kz.mean.toFixed(0), "N/mm"],
      ["Kz fluctuation", (kz.cov * 100).toFixed(1), "% CoV over θ"],
      ["Blocks in patch", bc.mean.toFixed(1), "avg"],
      ["Dominant Kz order", dom ? String(dom.order) : "–", "per rev"],
    ];
    var html = "";
    for (var i = 0; i < cards.length; i++)
      html += "<div class='card'><div class='k'>" + cards[i][0] + "</div><div class='v'>" + cards[i][1] + "</div><div class='u'>" + cards[i][2] + "</div></div>";
    $("cards").innerHTML = html;
  }

  function renderThetaStack() {
    var r = currentResult(), th = plotTheme();
    var x = r.theta_deg;
    var rows = [
      { y: r.contact_area, name: "Contact area", unit: "mm²", color: th.accent },
      { y: r.kz, name: "Kz (vertical)", unit: "N/mm", color: th.good },
      { y: r.kx, name: "Kx (long.)", unit: "N/mm", color: th.accent2 },
      { y: r.ky, name: "Ky (lateral)", unit: "N/mm", color: th.bad },
      // The discrete count is sampled on its own theta grid, so it rides along
      // as a second trace on this row rather than being resampled.
      { y: r.block_count, name: "Blocks in patch", unit: "count", color: th.inkDim,
        extra: { x: r.theta_discrete, y: r.block_count_discrete,
                 name: "blocks >50% in", color: th.accent, shape: "hv" } },
      { y: r.centroid_y, name: "Contact centroid", unit: "mm from centreline", color: th.accent2 },
    ];
    var data = [], layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      showlegend: false, margin: { l: 64, r: 16, t: 24, b: 40 },
      height: 560, grid: { rows: rows.length, columns: 1, pattern: "independent", roworder: "top to bottom" },
      title: { text: "In-patch aggregates vs rotation angle θ  (γ = " + r.gamma_deg + "°)", font: { size: 13 } },
    };
    for (var i = 0; i < rows.length; i++) {
      var xa = "x" + (i + 1), ya = "y" + (i + 1);
      data.push({ x: x, y: rows[i].y, xaxis: xa, yaxis: ya, type: "scatter", mode: "lines", line: { color: rows[i].color, width: 1.5 }, name: rows[i].name });
      if (rows[i].extra && rows[i].extra.y && rows[i].extra.y.length) {
        data.push({ x: rows[i].extra.x, y: rows[i].extra.y, xaxis: xa, yaxis: ya,
          type: "scatter", mode: "lines",
          line: { color: rows[i].extra.color, width: 1.2, shape: rows[i].extra.shape, dash: "dot" },
          name: rows[i].extra.name });
      }
      layout["xaxis" + (i + 1)] = { gridcolor: th.grid, zeroline: false, range: [0, 360], showticklabels: i === rows.length - 1, title: i === rows.length - 1 ? { text: "rotation angle θ (deg)", font: { size: 11 } } : undefined, tickvals: [0, 45, 90, 135, 180, 225, 270, 315, 360] };
      layout["yaxis" + (i + 1)] = { gridcolor: th.grid, zeroline: false, title: { text: rows[i].name + " (" + rows[i].unit + ")", font: { size: 10 } } };
    }
    Plotly.react($("thetaStack"), data, layout, { responsive: true, displayModeBar: false });
  }

  function renderPatternStrip() {
    var p = state.pattern, th = plotTheme(), C = p.tyre_circumference;
    var shapes = [], zoneColor = { center: th.accent, intermediate: th.good, shoulder: th.accent2 };
    for (var bi = 0; bi < p.blocks.length; bi++) {
      var blk = p.blocks[bi];
      var pieces = E.splitAtSeam(blk.polygon, C);
      for (var pc = 0; pc < pieces.length; pc++) {
        var poly = pieces[pc], path = "";
        for (var v = 0; v < poly.length; v++) {
          var TH = (poly[v][0] / C) * 360;
          path += (v === 0 ? "M" : "L") + TH.toFixed(3) + "," + poly[v][1].toFixed(3) + " ";
        }
        path += "Z";
        shapes.push({ type: "path", path: path, xref: "x", yref: "y", fillcolor: hexA(zoneColor[blk.zone] || th.accent, 0.55), line: { width: 0 } });
      }
    }
    var layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 64, r: 16, t: 24, b: 40 }, height: 200, shapes: shapes,
      title: { text: "Rolled-out tread pattern (same θ axis)", font: { size: 13 } },
      xaxis: { range: [0, 360], gridcolor: th.grid, zeroline: false, title: { text: "rotation angle θ (deg)", font: { size: 11 } }, tickvals: [0, 45, 90, 135, 180, 225, 270, 315, 360] },
      yaxis: { range: [-p.tread_width / 2, p.tread_width / 2], gridcolor: th.grid, zeroline: false, title: { text: "lateral y (mm)", font: { size: 10 } }, scaleanchor: undefined },
    };
    Plotly.react($("patternStrip"), [{ x: [0], y: [0], type: "scatter", mode: "markers", marker: { opacity: 0 }, hoverinfo: "skip" }], layout, { responsive: true, displayModeBar: false });
  }

  function renderLeanHeatmap() {
    var th = plotTheme();
    var metric = state.heatMetric;
    var x = state.results[0].theta_deg;
    var y = [], z = [];
    for (var i = 0; i < state.results.length; i++) { y.push(state.results[i].gamma_deg); z.push(state.results[i][metric]); }
    var label = { kz: "Kz (N/mm)", kx: "Kx (N/mm)", ky: "Ky (N/mm)", contact_area: "Contact area (mm²)", block_count: "Blocks", land_ratio: "Land ratio" }[metric];
    var data = [{ z: z, x: x, y: y, type: "heatmap", colorscale: "Viridis", colorbar: { title: { text: label, font: { size: 11 } } } }];
    var layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 60, r: 20, t: 30, b: 44 }, height: 420,
      title: { text: label + " across rotation θ and lean γ", font: { size: 13 } },
      xaxis: { title: { text: "rotation angle θ (deg)", font: { size: 11 } }, range: [0, 360] },
      yaxis: { title: { text: "lean angle γ (deg)", font: { size: 11 } } },
    };
    Plotly.react($("leanHeat"), data, layout, { responsive: true, displayModeBar: false });
  }

  function renderOrders() {
    var r = currentResult(), th = plotTheme();
    var metric = state.orderMetric;
    var spec = E.orderSpectrum(r[metric], 60);
    var colors = spec.orders.map(function (o) {
      if (state.pattern.pitches && o === state.pattern.pitches.length) return th.bad;
      if (state.pattern.meta && state.pattern.meta.geometric_repeat_mm && o === Math.round(state.pattern.tyre_circumference / state.pattern.meta.geometric_repeat_mm)) return th.accent2;
      return th.accent;
    });
    var data = [{ x: spec.orders, y: spec.amplitude, type: "bar", marker: { color: colors } }];
    var label = { kz: "Kz", kx: "Kx", ky: "Ky", contact_area: "contact area", block_count: "block count" }[metric];
    var layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 60, r: 16, t: 40, b: 44 }, height: 380,
      title: { text: "Order content of " + label + " (γ = " + r.gamma_deg + "°)  — red bar = pitch count, orange = geometric repeat", font: { size: 12 } },
      xaxis: { title: { text: "order (events per revolution)", font: { size: 11 } }, gridcolor: th.grid },
      yaxis: { title: { text: "amplitude (fraction of mean)", font: { size: 11 } }, gridcolor: th.grid, tickformat: ".1%" },
    };
    Plotly.react($("orders"), data, layout, { responsive: true, displayModeBar: false });
  }

  function renderZones() {
    var r = currentResult(), th = plotTheme();
    var x = r.theta_deg;
    var data = [];
    var colors = { center: th.accent, intermediate: th.good, shoulder: th.accent2 };
    for (var z in r.zone_area)
      data.push({ x: x, y: r.zone_area[z], type: "scatter", mode: "lines", stackgroup: "one", name: z, line: { width: 0.5, color: colors[z] }, fillcolor: hexA(colors[z], 0.55) });
    var layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 60, r: 16, t: 40, b: 44 }, height: 380, legend: { orientation: "h" },
      title: { text: "Zone contact area vs θ (γ = " + r.gamma_deg + "°)", font: { size: 13 } },
      xaxis: { title: { text: "rotation angle θ (deg)", font: { size: 11 } }, range: [0, 360], gridcolor: th.grid },
      yaxis: { title: { text: "contact area (mm²)", font: { size: 11 } }, gridcolor: th.grid },
    };
    Plotly.react($("zones"), data, layout, { responsive: true, displayModeBar: false });
  }

  function renderPatchPreview() {
    var r = currentResult(), th = plotTheme(), p = state.pattern;
    var out = r.patch.outline;
    var xs = out.map(function (q) { return q[0]; }).concat([out[0][0]]);
    var ys = out.map(function (q) { return q[1]; }).concat([out[0][1]]);
    var data = [
      { x: [-p.tread_width, p.tread_width], y: [0, 0], type: "scatter", mode: "lines", line: { color: th.grid, dash: "dot" }, hoverinfo: "skip", showlegend: false },
      { x: xs, y: ys, type: "scatter", mode: "lines", fill: "toself", line: { color: th.accent, width: 2 }, fillcolor: hexA(th.accent, 0.18), name: "patch @ γ=" + r.gamma_deg + "°" },
    ];
    var layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 60, r: 16, t: 40, b: 44 }, height: 420,
      title: { text: r.patch.provenance, font: { size: 12 } },
      xaxis: { title: { text: "circumferential x (mm)", font: { size: 11 } }, gridcolor: th.grid, zeroline: false },
      yaxis: { title: { text: "lateral y (mm)", font: { size: 11 } }, gridcolor: th.grid, zeroline: false, scaleanchor: "x", scaleratio: 1, range: [-p.tread_width / 2, p.tread_width / 2] },
    };
    Plotly.react($("patchPrev"), data, layout, { responsive: true, displayModeBar: false });
    var s = r.shape;
    $("patchStats").innerHTML =
      row("Shape", r.patch.provenance) +
      row("Area", s.area_mm2.toFixed(0) + " mm²") +
      row("Length × width", s.length_mm.toFixed(1) + " × " + s.width_mm.toFixed(1) + " mm") +
      row("Perimeter", s.perimeter_mm.toFixed(1) + " mm") +
      row("Compactness (P²/A)", s.compactness.toFixed(2)) +
      row("Aspect ratio", s.aspect_ratio.toFixed(2)) +
      row("Lateral centre y", s.y_center_mm.toFixed(1) + " mm") +
      row("Normal load", r.patch.normal_load.toFixed(0) + " N") +
      row("Mean pressure", s.mean_pressure_mpa.toFixed(3) + " MPa") +
      row("Clipped to tread edge", s.clipped ? "yes" : "no");
  }
  function row(k, v) { return "<tr><th>" + k + "</th><td class='num'>" + v + "</td></tr>"; }

  function renderDiagnostics() {
    var st = state.stiffness;
    var html = "<table class='metrics'><tr><th>Zone</th><th>Blocks</th><th>Kx mean</th><th>Ky mean</th><th>Kz mean</th><th>Kz CoV</th><th>Area mean</th></tr>";
    var zones = ["center", "intermediate", "shoulder"];
    for (var i = 0; i < zones.length; i++) {
      var z = st.zones[zones[i]];
      html += "<tr><td>" + zones[i] + "</td><td class='num'>" + z.kx.n + "</td><td class='num'>" + z.kx.mean.toFixed(0) +
        "</td><td class='num'>" + z.ky.mean.toFixed(0) + "</td><td class='num'>" + z.kz.mean.toFixed(0) +
        "</td><td class='num'>" + (z.kz.cov * 100).toFixed(1) + "%</td><td class='num'>" + z.area.mean.toFixed(0) + " mm²</td></tr>";
    }
    var o = st.overall;
    html += "<tr><td><b>overall</b></td><td class='num'>" + o.kx.n + "</td><td class='num'>" + o.kx.mean.toFixed(0) +
      "</td><td class='num'>" + o.ky.mean.toFixed(0) + "</td><td class='num'>" + o.kz.mean.toFixed(0) +
      "</td><td class='num'>" + (o.kz.cov * 100).toFixed(1) + "%</td><td class='num'>" + o.area.mean.toFixed(0) + " mm²</td></tr>";
    html += "</table>";

    // build flags
    var r = currentResult();
    var kzf = E.fluctuationStats(r.kz), kyf = E.fluctuationStats(r.ky);
    var flags = [];
    var centerKz = st.zones.center.kz.mean, shoulderKz = st.zones.shoulder.kz.mean;
    var imbalance = shoulderKz > 0 && centerKz > 0 ? Math.abs(shoulderKz - centerKz) / ((shoulderKz + centerKz) / 2) : 0;
    flags.push(flag(kzf.cov < 0.05 ? "good" : kzf.cov < 0.12 ? "warn" : "bad", "Kz fluctuation " + (kzf.cov * 100).toFixed(1) + "% over θ"));
    flags.push(flag(imbalance < 0.25 ? "good" : imbalance < 0.5 ? "warn" : "bad", "centre/shoulder Kz imbalance " + (imbalance * 100).toFixed(0) + "%"));
    var dom = E.dominantOrders(E.orderSpectrum(r.kz, 60), 3);
    var domTxt = dom.map(function (d) { return "O" + d.order; }).join(", ");
    flags.push(flag("good", "dominant Kz orders: " + domTxt));
    if (state.pattern.meta && state.pattern.meta.uniform_array) flags.push(flag("warn", "uniform array — no pitch modulation in the drawing"));
    $("flags").innerHTML = flags.join(" ");
    $("diagTable").innerHTML = html;
  }
  function flag(kind, text) { return "<span class='flag " + kind + "'>" + text + "</span>"; }

  // ---- export ----------------------------------------------------------
  // The pipeline used to end at the screen: results could be read but never
  // taken anywhere. Both formats below are self-describing so a file still
  // means something months later, without the page that produced it.
  function download(filename, text, mime) {
    var blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function safeName() {
    var n = (state.pattern && state.pattern.name) || "tread";
    return n.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "tread";
  }

  function settingsSnapshot() {
    return {
      pattern: {
        name: state.pattern.name, source: state.pattern.source,
        circumference_mm: state.pattern.tyre_circumference,
        tread_width_mm: state.pattern.tread_width,
        n_blocks: state.pattern.blocks.length,
        n_pitches: state.pattern.pitches.length,
        geometric_repeat_mm: state.pattern.meta ? state.pattern.meta.geometric_repeat_mm : null,
      },
      block_defaults: readDefaults(),
      compound_and_boundary: readStiffParams(),
      contact_patch: readSpec(),
      load: readCpParams(),
      analysis: {
        lean_angles_deg: state.results.map(function (r) { return r.gamma_deg; }),
        grid: state.grid,
        curvature_correction: $("curv").checked,
        max_supported_lean_deg: state.maxLean,
      },
      import_report: state.report,
      physics_notes: state.notes || [],
    };
  }

  function exportCSV() {
    if (!state.results) return;
    var s = settingsSnapshot();
    var lines = [];
    // A header block, commented, so the numbers are never orphaned from how
    // they were produced.
    lines.push("# 2W tread pattern evaluation - theta x gamma sweep");
    lines.push("# generated: " + new Date().toISOString());
    lines.push("# pattern: " + s.pattern.name + " (" + s.pattern.n_blocks + " blocks, " +
      s.pattern.circumference_mm.toFixed(2) + " x " + s.pattern.tread_width_mm.toFixed(2) + " mm)");
    lines.push("# NSD " + s.block_defaults.height + " mm, draft " + s.block_defaults.draft_angle +
      " deg, sipes " + s.block_defaults.n_lateral_sipes + ", Shore A " + s.compound_and_boundary.shore_a +
      ", boundary " + s.compound_and_boundary.mode + ", sipe model " + s.compound_and_boundary.sipe_model);
    lines.push("# patch: " + E.describeSpec(s.contact_patch) +
      ", scale with lean " + (s.contact_patch.scale_with_lean ? "on" : "off") +
      ", Fz " + s.load.vertical_load + " N" + (s.load.load_rises_with_lean ? " (rises with lean)" : " (constant)"));
    lines.push("# grid: " + s.analysis.grid.nx + " x " + s.analysis.grid.ny +
      " (dx " + s.analysis.grid.dx.toFixed(4) + " mm, dy " + s.analysis.grid.dy.toFixed(4) + " mm)");
    (s.physics_notes || []).forEach(function (n) { lines.push("# note: " + n.replace(/\s+/g, " ")); });
    lines.push([
      "gamma_deg", "theta_deg", "contact_area_mm2", "land_ratio",
      "kx_N_per_mm", "ky_N_per_mm", "kz_N_per_mm",
      "block_count_effective", "centroid_y_mm",
      "zone_center_mm2", "zone_intermediate_mm2", "zone_shoulder_mm2",
    ].join(","));
    for (var i = 0; i < state.results.length; i++) {
      var r = state.results[i];
      for (var j = 0; j < r.theta_deg.length; j++) {
        lines.push([
          r.gamma_deg, r.theta_deg[j].toFixed(4), r.contact_area[j].toFixed(4),
          r.land_ratio[j].toFixed(6), r.kx[j].toFixed(4), r.ky[j].toFixed(4), r.kz[j].toFixed(4),
          r.block_count[j].toFixed(4), r.centroid_y[j].toFixed(4),
          r.zone_area.center[j].toFixed(4), r.zone_area.intermediate[j].toFixed(4),
          r.zone_area.shoulder[j].toFixed(4),
        ].join(","));
      }
    }
    download(safeName() + "_sweep.csv", lines.join("\n"), "text/csv;charset=utf-8");
  }

  function exportJSON() {
    if (!state.results) return;
    var payload = {
      format: "tread_eval.sweep",
      format_version: 1,
      generated: new Date().toISOString(),
      settings: settingsSnapshot(),
      block_stiffness_summary: state.stiffness,
      results: state.results,
    };
    download(safeName() + "_run.json", JSON.stringify(payload, null, 1), "application/json");
  }

  function exportSummary() {
    if (!state.results) return;
    var s = settingsSnapshot();
    var out = ["2W TREAD PATTERN EVALUATION", "=".repeat(60), ""];
    out.push("Pattern : " + s.pattern.name);
    out.push("Geometry: " + s.pattern.circumference_mm.toFixed(1) + " x " +
      s.pattern.tread_width_mm.toFixed(1) + " mm, " + s.pattern.n_blocks + " blocks, " +
      s.pattern.n_pitches + " pitches");
    out.push("Blocks  : NSD " + s.block_defaults.height + " mm, draft " + s.block_defaults.draft_angle +
      " deg, " + s.block_defaults.n_lateral_sipes + " sipes, Shore A " + s.compound_and_boundary.shore_a);
    out.push("Patch   : " + E.describeSpec(s.contact_patch));
    out.push("");
    out.push("PER LEAN ANGLE");
    out.push(["gamma", "area_mean", "area_CoV%", "Kz_mean", "Kz_CoV%", "Kx_mean", "Ky_mean", "blocks"]
      .map(function (h) { return h.padStart(11); }).join(""));
    state.results.forEach(function (r) {
      var a = E.fluctuationStats(r.contact_area), kz = E.fluctuationStats(r.kz);
      var kx = E.fluctuationStats(r.kx), ky = E.fluctuationStats(r.ky), bc = E.fluctuationStats(r.block_count);
      out.push([
        r.gamma_deg + "°", a.mean.toFixed(0), (a.cov * 100).toFixed(2), kz.mean.toFixed(0),
        (kz.cov * 100).toFixed(2), kx.mean.toFixed(0), ky.mean.toFixed(0), bc.mean.toFixed(2),
      ].map(function (v) { return String(v).padStart(11); }).join(""));
    });
    if ((s.physics_notes || []).length) {
      out.push("", "PHYSICS NOTES");
      s.physics_notes.forEach(function (n) { out.push(" - " + n.replace(/\s+/g, " ")); });
    }
    if (s.import_report && s.import_report.warnings && s.import_report.warnings.length) {
      out.push("", "IMPORT WARNINGS");
      s.import_report.warnings.forEach(function (n) { out.push(" - " + n.replace(/\s+/g, " ")); });
    }
    download(safeName() + "_summary.txt", out.join("\n"), "text/plain;charset=utf-8");
  }

  function refreshExportButtons() {
    var on = !!state.results;
    ["exportCsv", "exportJson", "exportTxt"].forEach(function (id) {
      var el = $(id); if (el) el.disabled = !on;
    });
  }

  // ---- tabs ------------------------------------------------------------
  function initTabs() {
    var btns = document.querySelectorAll(".tabs button");
    for (var i = 0; i < btns.length; i++) {
      (function (btn) {
        on(btn, "click", function () {
          document.querySelectorAll(".tabs button").forEach(function (b) { b.classList.remove("on"); });
          document.querySelectorAll(".panel").forEach(function (p) { p.classList.remove("on"); });
          btn.classList.add("on");
          $("panel-" + btn.dataset.tab).classList.add("on");
          // Plotly needs a resize nudge when a hidden plot becomes visible
          if (state.results) window.dispatchEvent(new Event("resize"));
        });
      })(btns[i]);
    }
  }

  // ---- wire up ---------------------------------------------------------
  function init() {
    initTheme();
    initTabs();
    editorSetup();

    on($("fileInput"), "change", function (e) { if (e.target.files[0]) loadFile(e.target.files[0]); });
    on($("sampleBtn"), "click", function () {
      var t = $("sample-dxf").textContent;
      buildPatternFromText(t, "130/80R17 Tramplr XR (sample)");
    });
    on($("runBtn"), "click", run);
    on($("exportCsv"), "click", exportCSV);
    on($("exportJson"), "click", exportJSON);
    on($("exportTxt"), "click", exportSummary);
    on($("shape"), "change", function () { syncShapeFields(); drawEditor(); markStale(); });

    ["cpLength", "cpWidth", "cpCorner", "cpExp", "cpTaper", "cpRot", "cpY", "cpLoad"].forEach(function (id) {
      on($(id), "input", function () { refreshValidation(); drawEditor(); markStale(); });
    });
    on($("cpAutoY"), "change", function () { $("cpY").disabled = $("cpAutoY").checked; drawEditor(); markStale(); });
    on($("cpScaleLean"), "change", function () { drawEditor(); markStale(); });
    on($("cpAutoLoad"), "change", function () { drawEditor(); markStale(); });
    ["nsd", "draft", "sipes", "sipeDepth", "shore", "poisson", "mode", "sipeModel", "quality", "curv", "wheelR", "crownCenter", "crownShoulder", "nPitches"].forEach(function (id) {
      on($(id), "input", function () { refreshValidation(); markStale(); });
      on($(id), "change", function () { refreshValidation(); markStale(); });
    });

    on($("gammaSel"), "change", function () { state.gammaShown = state.results[parseInt(this.value, 10)].gamma_deg; renderAll(); });
    on($("heatMetric"), "change", function () { state.heatMetric = this.value; renderLeanHeatmap(); });
    on($("orderMetric"), "change", function () { state.orderMetric = this.value; renderOrders(); });

    syncShapeFields();
    refreshValidation();
    $("cpY").disabled = $("cpAutoY").checked;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
