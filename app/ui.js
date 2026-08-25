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
    compare: [],         // runs held for comparison (this session + loaded files)
    bandEdges: null,     // rib cuts the last run used
    bandMetric: "contact_area",
    thetaRange: null,    // shared x-range across the sweep rows and the pattern
    cplRange: null,      // and the coupling tab's own, kept apart from it
    reportSections: {},  // report sections the user has switched off
    measured: null,      // imported footprint outline, when there is one
    stackRows: {},       // sweep rows switched off by the user (key -> false)
    pinStrip: true,      // keep the rolled-out pattern at the foot of the window
    compareMetric: "kz",
  };

  var DEFAULT_LEANS = [0, 5, 10, 15, 20, 25, 30, 35, 40];

  // Typical values per tyre class. A TBR crown is nearly flat where a 2W one is
  // tightly curved, and the loads differ by more than an order of magnitude, so
  // running a truck tread on motorcycle defaults is not a small error.
  // Lean angles differ too: a truck barely leans, a motorcycle lives at 40 deg.
  var TYRE_PRESETS = {
    "2w":  { wheelR: 320, cpLoad: 1500,
             cpLength: 90,  cpWidth: 50,  cpCorner: 12, nsd: 8.5,
             leans: [0, 5, 10, 15, 20, 25, 30, 35, 40], label: "2W — motorcycle" },
    "pcr": { wheelR: 315, cpLoad: 4000,
             cpLength: 140, cpWidth: 160, cpCorner: 25, nsd: 8.0,
             leans: [0, 2, 4, 6, 8, 10], label: "PCR — passenger car" },
    "tbr": { wheelR: 520, cpLoad: 26000,
             cpLength: 230, cpWidth: 250, cpCorner: 30, nsd: 16.0,
             leans: [0, 2, 4, 6, 8], label: "TBR — truck & bus" },
  };

  // The crown radii a preset writes are the same ones a blank field falls back
  // to, so they live in the engine's class table and are read from there rather
  // than kept in a second copy that can drift.
  function presetFor(name) {
    var t = TYRE_PRESETS[name];
    if (!t) return null;
    var cls = E.tyreClass(name);
    return Object.assign({}, t, { crownCenter: cls.crown_r_center, crownShoulder: cls.crown_r_shoulder });
  }

  function currentLeans() {
    var t = TYRE_PRESETS[$("tyreType").value];
    return (t && t.leans) || DEFAULT_LEANS;
  }

  // Class physics that must reach the engine on every run, preset or not.
  function tyreClassPhysics() { return E.tyreClass($("tyreType").value); }

  function applyTyrePreset() {
    var t = presetFor($("tyreType").value);
    if (!t) return;
    ["crownCenter", "crownShoulder", "wheelR", "cpLoad", "cpLength", "cpWidth", "cpCorner", "nsd"]
      .forEach(function (k) { if ($(k)) $(k).value = t[k]; });
    // The load model differs by class: a motorcycle's normal load really does
    // grow as Fz/cos(lean); a car or truck barely cambers, and its cornering
    // load comes from weight transfer, which this model does not carry.
    $("cpAutoLoad").checked = !!tyreClassPhysics().load_rises_with_lean;
    $("crownBreak").value = tyreClassPhysics().crown_break;
    refreshValidation(); drawEditor(); markStale();
  }

  // Band edges: either evenly spaced, or the internal cuts the user typed with
  // the two tread edges added on.
  function readCrownBreak() {
    var v = parseFloat($("crownBreak").value);
    return isFinite(v) ? v : tyreClassPhysics().crown_break;
  }

  function readBandEdges() {
    if (!state.pattern) return null;
    var n = parseInt($("nBands").value, 10);
    if (!isFinite(n) || n < 1) return null;
    var half = state.pattern.tread_width / 2;
    var raw = $("bandEdges").value.trim();
    if (raw === "") return E.evenBandEdges(state.pattern.tread_width, Math.min(n, 24));
    var inner = raw.split(",").map(function (v) { return parseFloat(v.trim()); })
                   .filter(function (v) { return isFinite(v); })
                   .sort(function (a, b) { return a - b; });
    return E.validateBandEdges([-half].concat(inner, [half]), state.pattern.tread_width);
  }

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
    var m = measuredSpec();
    if (m) {
      m.rotation = num("cpRot");
      m.y_center = $("cpAutoY").checked ? null : num("cpY");
      m.gamma_deg = 0;
      m.load_N = null;
      m.label = "";
      m.scale_with_lean = $("cpScaleLean").checked;
      return m;
    }
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
      modulus_mode: $("modulusMode").value,
      e_modulus: num("eModulus"), gent_k: num("gentK"),
    };
  }

  // Show what the stiffness model is actually using. E was previously derived
  // from Shore A behind the scenes and never displayed, so there was no way to
  // tell what modulus the numbers rested on.
  function syncCompoundFields() {
    var direct = $("modulusMode").value === "direct";
    $("rowShore").style.display = direct ? "none" : "";
    $("rowE").style.display = direct ? "" : "none";
    $("rowK").style.display = direct ? "" : "none";
    // Moving into direct mode, seed the fields from the hardness currently set
    // so the switch does not change the answer until you edit something.
    if (direct && !$("eModulus").dataset.touched) {
      var s = num("shore");
      if (isFinite(s)) {
        $("eModulus").value = E.shoreE(s).toFixed(3);
        $("gentK").value = E.shoreK(s).toFixed(3);
      }
    }
    var box = $("compoundReadout");
    if (!box) return;
    try {
      var cp = E.compoundProperties(readStiffParams());
      box.innerHTML =
        "<b>E</b> " + cp.E.toFixed(3) + " N/mm² &nbsp;·&nbsp; <b>G</b> " + cp.G.toFixed(3) +
        " N/mm² &nbsp;·&nbsp; <b>k</b> " + cp.k.toFixed(3) +
        "<br><span style='opacity:.8'>" + escapeHtml(cp.source) +
        ". G = E / 2(1+ν). Bending uses E, shear uses G, and Kz uses E(1+2kS²) with the bulk correction.</span>";
    } catch (err) { box.textContent = ""; }
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
  // The tread arc spec, when the designer has typed one. Returns null to fall
  // back to the two-radius fields. Throws on a malformed spec so the caller can
  // put the message in front of the user rather than silently ignoring it.
  function readCrownArcs(treadWidth) {
    if (crownMode() !== "arcs") return null;
    var el = $("crownArcs");
    if (!el || !el.value.trim()) return null;
    var w = treadWidth || (state.pattern ? state.pattern.tread_width : 0);
    if (!(w > 0)) return null;
    return E.parseCrownArcs(el.value, w);
  }

  function crownMode() { return $("crownMode") ? $("crownMode").value : "arcs"; }

  // The drop profile, when the designer is working the way they actually do:
  // width and drop known, radii to be found. Same shape as readCrownArcs --
  // null to fall through, throws so the message reaches the user.
  function readCrownDrops(treadWidth) {
    if (crownMode() !== "drops") return null;
    var el = $("crownDrops");
    if (!el || !el.value.trim()) return null;
    var w = treadWidth || (state.pattern ? state.pattern.tread_width : 0);
    if (!(w > 0)) return null;
    return E.parseCrownDrops(el.value, w);
  }

  // Which crown inputs are live, and which have been overridden. The two-radius
  // cells are still read when nothing else is typed, and a user who has typed an
  // arc spec above them has no way of knowing they are now inert -- which is
  // exactly the confusion this greys out.
  function syncCrownFields() {
    var mode = crownMode();
    if ($("rowCrownArcs")) $("rowCrownArcs").style.display = mode === "arcs" ? "" : "none";
    if ($("rowCrownDrops")) $("rowCrownDrops").style.display = mode === "drops" ? "" : "none";
    var overridden = mode === "drops"
      ? !!($("crownDrops") && $("crownDrops").value.trim())
      : !!($("crownArcs") && $("crownArcs").value.trim());
    ["crownCenter", "crownShoulder", "crownBreak"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.disabled = overridden;
      var row = el.closest ? el.closest(".field") : null;
      if (row) row.style.opacity = overridden ? 0.45 : "";
      if (row) row.title = overridden
        ? "Overridden by the " + (mode === "drops" ? "drop profile" : "tread arc radii") + " above."
        : row.getAttribute("data-title") || row.title;
    });
  }

  // Show what the typed arcs actually resolve to, and what they imply. The
  // maximum reachable lean is the number most likely to surprise: it is fixed by
  // the profile, not by anything the user can set elsewhere.
  function syncCrownArcInfo() {
    var box = $("crownArcInfo");
    if (!box) return;
    var w = state.pattern ? state.pattern.tread_width : 0;
    if (!(w > 0)) { box.innerHTML = "<i>Load a tread plan to resolve the arc profile.</i>"; return; }
    try {
      var arcs = readCrownArcs(w);
      var drops = readCrownDrops(w);
      var crown = E.buildCrown(w, {
        tyre_class: $("tyreType").value,
        crown_arcs: arcs,
        crown_drops: drops,
        crown_r_center: $("crownCenter").value.trim() === "" ? undefined : num("crownCenter"),
        crown_r_shoulder: $("crownShoulder").value.trim() === "" ? undefined : num("crownShoulder"),
        crown_break: readCrownBreak(),
      });
      var half = w / 2;
      var desc;
      if (drops) {
        // The solved radii are the answer to "what arcs give me this drop?", so
        // they are put in front of the designer rather than kept internal.
        desc = "<b>from drops</b> · solved <b>" + crown.arcs.map(function (a, i) {
          return "R" + (i + 1) + " " + a.radius.toFixed(1) + " mm to " +
            (i === crown.arcs.length - 1 ? "the edge" : a.to_mm.toFixed(1) + " mm");
        }).join(" · ") + "</b><br>drop " + crown.drops.map(function (d) {
          return d.asked_mm.toFixed(2) + " mm at " + d.at_mm.toFixed(1) + " mm";
        }).join(" · ");
      } else if (arcs) {
        desc = "<b>true arcs</b> · " + arcs.map(function (a, i) {
          return "<b>R" + (i + 1) + "</b> " + a.r + " mm to " +
            (i === arcs.length - 1 ? "the edge" : a.to.toFixed(1) + " mm");
        }).join(" · ");
      } else {
        // Never let a blank field read as "nothing set". A crown is never flat,
        // and which one you got decides every lean angle in the sweep.
        var blank = $("crownCenter").value.trim() === "" && $("crownShoulder").value.trim() === "";
        var cls = E.tyreClass($("tyreType").value);
        desc = "<b>two-radius blend</b> · <b>R centre</b> " + E.crownLocalRadius(crown, 0).toFixed(0) +
          " mm · <b>R edge</b> " + E.crownLocalRadius(crown, half).toFixed(0) + " mm" +
          (blank ? " <i>(from the " + $("tyreType").value.toUpperCase() +
                   " defaults — nothing typed, and a blank crown is not a flat one)</i>" : "");
      }
      box.innerHTML = desc +
        "<br>half width " + half.toFixed(1) + " mm · edge drop " + E.crownDrop(crown, half).toFixed(2) +
        " mm · <b>max reachable lean " + E.maxSupportedLean(crown).toFixed(1) + "°</b>";
      setSpecError("");
    } catch (err) {
      box.innerHTML = "<b style='color:var(--bad)'>" + escapeHtml(err.message) + "</b>";
    }
  }

  function crownSummary() {
    if (!state.pattern || !state.pattern.crown) return null;
    var c = state.pattern.crown, half = state.pattern.tread_width / 2;
    return {
      source: c.from_drops ? "drop profile (radii solved)"
            : c.arcs ? "tread arc specification" : "two-radius blend",
      arcs: c.arcs || null,
      drops: c.drops || null,
      r_center_mm: E.crownLocalRadius(c, 0),
      r_edge_mm: E.crownLocalRadius(c, half),
      edge_drop_mm: E.crownDrop(c, half),
      max_reachable_lean_deg: E.maxSupportedLean(c),
    };
  }

  function crownLine(s) {
    var c = s.crown;
    if (!c) return "no crown resolved";
    var head = c.arcs
      ? (c.drops ? "from drops (" + c.drops.map(function (d) {
            return d.asked_mm.toFixed(2) + " mm at " + d.at_mm.toFixed(1) + " mm"; }).join(", ") + ") -> " : "") +
        c.arcs.map(function (a, i) { return "R" + (i + 1) + " " + a.radius.toFixed(1) + " mm to " + a.to_mm.toFixed(1) + " mm"; }).join(", ")
      : "two-radius blend, " + c.r_center_mm.toFixed(0) + " mm centre to " + c.r_edge_mm.toFixed(0) + " mm edge";
    return head + "; edge drop " + c.edge_drop_mm.toFixed(2) + " mm; max reachable lean " +
      c.max_reachable_lean_deg.toFixed(1) + " deg";
  }

  function readWear() { var v = num("wear"); return isFinite(v) && v > 0 ? v : 0; }
  function readTiebarFrac() { var v = num("tiebarHeight"); return isFinite(v) && v > 0 ? Math.min(1, v) : 0.55; }
  function readWeldTol() { var v = num("weldTol"); return isFinite(v) && v > 0 ? v : 0.01; }
  function readTiebarAreaFrac() { var v = num("tiebarFrac"); return isFinite(v) && v > 0 ? v : 0.5; }

  // ---- shape parameter visibility --------------------------------------
  function syncShapeFields() {
    var s = $("shape").value, measured = s === "measured";
    $("rowCorner").style.display = s === "rounded" ? "" : "none";
    $("rowExp").style.display = s === "superellipse" ? "" : "none";
    $("rowTaper").style.display = s === "trapezoid" ? "" : "none";
    if ($("cpMeasuredBox")) $("cpMeasuredBox").style.display = measured ? "" : "none";
    // Length and width describe an idealised shape; an imported footprint has
    // its own. Greyed rather than hidden, so it stays obvious that the size now
    // comes from the file.
    ["cpLength", "cpWidth"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.disabled = measured;
      var row = el.closest ? el.closest(".field") : null;
      if (row) row.style.opacity = measured ? 0.45 : "";
    });
    renderMeasuredInfo();
  }

  // ---- measured contact patch ------------------------------------------
  // A traced footprint is the real thing: not convex, not symmetric, and not
  // the size the idealised shape guessed. Nothing downstream assumes otherwise
  // -- the outline goes through the same placement, clipping, pressure and
  // sweep as any other patch.
  function measuredSpec() {
    var m = state.measured;
    if (!m || $("shape").value !== "measured") return null;
    return {
      shape: "measured",
      outline: m.placed,
      measured_at_deg: m.measured_at,
      source_name: m.name,
      tread_width_hint: state.pattern ? state.pattern.tread_width : 0,
    };
  }

  function placeMeasured() {
    var m = state.measured;
    if (!m) return;
    var w = state.pattern ? state.pattern.tread_width : 0;
    if (!(w > 0)) { m.placed = m.raw; m.warnings = []; return; }
    var yc = $("cpAutoY").checked ? null : num("cpY");
    var res = E.placePatchOutline(m.raw, w, $("cpLateral").value, yc);
    m.placed = res.outline;
    m.warnings = res.warnings;
    m.metrics = E.validatePatchOutline(m.placed, w);
  }

  function renderMeasuredInfo() {
    var box = $("cpMeasuredInfo");
    if (!box) return;
    var m = state.measured;
    if ($("shape").value !== "measured") { box.innerHTML = ""; return; }
    if (!m) {
      box.innerHTML = "<b>No footprint loaded.</b> The run is blocked until you load one, or pick an " +
        "idealised shape above — a patch set to 'measured' with nothing behind it would silently " +
        "become whatever the length and width happened to say.";
      return;
    }
    if (!state.pattern) { box.innerHTML = "<i>Load a tread plan first — the tread width decides where the footprint sits.</i>"; return; }
    try {
      placeMeasured();
      var k = m.metrics;
      var html = "<b>" + escapeHtml(m.name) + "</b> — " + k.length.toFixed(1) + " x " +
        k.width.toFixed(1) + " mm, <b>" + k.area.toFixed(0) + " mm²</b>, " +
        m.raw.length + " points, measured at " + m.measured_at + "°";
      var lo = Infinity, hi = -Infinity;
      for (var i = 0; i < m.placed.length; i++) { if (m.placed[i][1] < lo) lo = m.placed[i][1]; if (m.placed[i][1] > hi) hi = m.placed[i][1]; }
      html += "<br>sits at y " + lo.toFixed(1) + " to " + hi.toFixed(1) + " mm on a ±" +
        (state.pattern.tread_width / 2).toFixed(1) + " mm tread";
      (m.warnings || []).forEach(function (w) {
        html += "<br><span style='color:var(--warn)'>" + escapeHtml(w) + "</span>";
      });
      box.innerHTML = html;
    } catch (err) {
      box.innerHTML = "<b style='color:var(--bad)'>" + escapeHtml(err.message) + "</b>";
    }
  }

  function loadFootprint(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var units = $("cpUnits").value;
        var raw = E.loadPatchOutline(String(reader.result), file.name, units);
        state.measured = {
          raw: raw, placed: raw, name: file.name, units: units,
          measured_at: num("cpMeasuredAt") || 0, warnings: [],
        };
        // A footprint was taken at ONE load and lean. Scaling it to another is
        // an extrapolation, so it is switched off rather than left on by
        // inheritance from whatever the idealised shape was doing.
        if ($("cpScaleLean").checked) {
          $("cpScaleLean").checked = false;
          state.measured.warnings.push("'scale patch with lean' was switched off: this footprint was " +
            "measured at one load and lean, so carrying it to another is an extrapolation. Tick it " +
            "again if that is what you want.");
        }
        renderMeasuredInfo(); refreshValidation(); drawEditor(); markStale();
      } catch (err) {
        state.measured = null;
        var box = $("cpMeasuredInfo");
        if (box) box.innerHTML = "<b style='color:var(--bad)'>Could not read " +
          escapeHtml(file.name) + ": " + escapeHtml(err.message) + "</b>";
        refreshValidation(); markStale();
      }
    };
    reader.readAsText(file);
  }

  // ---- DXF loading -----------------------------------------------------
  function buildPatternFromText(text, name) {
    var defaults = readDefaults();
    var opts = { name: name };
    var cr = $("crownCenter").value, csh = $("crownShoulder").value;
    if (cr) opts.crown_r_center = parseFloat(cr);
    if (csh) opts.crown_r_shoulder = parseFloat(csh);
    var np = $("nPitches").value; if (np) opts.n_pitches = parseInt(np, 10);
    var ph = tyreClassPhysics();
    // No crown_arcs or crown_drops here on purpose: their stations can be
    // fractions of the tread width, which is not known until this DXF has been
    // read. The crown is rebuilt from every input by reconcilePattern() before
    // each run.
    opts.crown_break = readCrownBreak();
    opts.tyre_class = $("tyreType").value;
    opts.zone_center = ph.zone_center; opts.zone_intermediate = ph.zone_intermediate;
    opts.weld_tolerance = readWeldTol();
    opts.tiebar_area_fraction = readTiebarAreaFrac();
    opts.tiebar_height_fraction = readTiebarFrac();
    var pitch = readPitchSpec();
    if (pitch) opts.pitch = pitch;
    var out = E.loadPattern(text, defaults, opts);
    state.pattern = out.pattern;
    state.report = out.report;
    state.results = null;
    state.editorTheta = 0;
    refreshExportButtons();
    showResultsChrome(false);
    renderBanner();
    renderTiebars();
    renderPitchInfo();
    syncCrownArcInfo();
    drawEditor();
    $("emptyHint").style.display = "none";
    refreshValidation();
  }

  // "A=32.4, B=36.0" -> {A: 32.4, B: 36}. Tolerant of spaces, semicolons and
  // newlines, because this gets pasted out of a spreadsheet.
  function parsePitchLengths(txt) {
    var out = {}, n = 0;
    String(txt || "").split(/[,;\n]+/).forEach(function (part) {
      var m = part.match(/^\s*([A-Za-z])\s*[=:]\s*([0-9.]+)\s*$/);
      if (m) { out[m[1].toUpperCase()] = parseFloat(m[2]); n++; }
    });
    return n ? out : null;
  }

  // The pitch specification, or null when the DXF is a whole tread.
  // Deliberately returns whatever the user has typed without filling gaps: the
  // engine refuses an incomplete spec with a message that says what is missing,
  // and that message is better than any guess made here.
  function readPitchSpec() {
    if (!$("pitchOn") || !$("pitchOn").checked) return null;
    var spec = {};
    var base = parseFloat($("pitchBase").value);
    if (isFinite(base) && base > 0) spec.base_length = base;
    var snap = parseFloat($("pitchSnap").value);
    if (isFinite(snap) && snap > 0) spec.snap_tolerance = snap;
    if ($("pitchMode").value === "sequence") {
      spec.lengths = parsePitchLengths($("pitchLens").value);
      spec.sequence = $("pitchSeq").value;
      spec.scaling = $("pitchScale").value || undefined;
    } else {
      spec.length = isFinite(base) && base > 0 ? base : undefined;
      spec.count = parseInt($("pitchCount").value, 10);
    }
    return spec;
  }

  // The pitch settings rebuild the geometry, which only happens on import. Say
  // so rather than let the page show a tread built from the previous settings.
  function markPitchStale() {
    if (!state.pattern) return;
    var el = $("pitchInfo");
    if (el) el.innerHTML = "<b style='color:var(--warn)'>Re-load the DXF to apply this.</b> " +
      "The pitch settings build the tread geometry, so they take effect at import, not at run.";
  }

  function syncPitchFields() {
    var on = $("pitchOn") && $("pitchOn").checked;
    if ($("pitchBox")) $("pitchBox").style.display = on ? "" : "none";
    var seq = $("pitchMode") && $("pitchMode").value === "sequence";
    ["rowPitchLens", "rowPitchSeq", "rowPitchScale"].forEach(function (id) {
      if ($(id)) $(id).style.display = seq ? "" : "none";
    });
    if ($("rowPitchCount")) $("rowPitchCount").style.display = seq ? "none" : "";
    renderPitchInfo();
  }

  // What the sequence adds up to, before anything is imported -- so a typo in
  // the sequence shows as a wrong circumference rather than as a strange sweep.
  function renderPitchInfo() {
    var el = $("pitchInfo");
    if (!el) return;
    var rep = state.report && state.report.pitch;
    if (rep) {
      var c = rep.closure;
      el.innerHTML = "<b>" + rep.n_pitches + " pitches</b> of " +
        (rep.lengths_mm.length ? Math.min.apply(null, rep.lengths_mm).toFixed(2) + "–" +
          Math.max.apply(null, rep.lengths_mm).toFixed(2) : "?") + " mm from a " +
        rep.base_length_mm.toFixed(2) + " mm drawing → <b>" + rep.circumference_mm.toFixed(1) +
        " mm</b> circumference. Scaling: " + escapeHtml(rep.scaling) + ".<br>Boundary: " +
        (!c.reaches_far ? "nothing reaches the far edge — a clean groove at every join"
         : c.ambiguous ? "<b style='color:var(--warn)'>" + c.n_left + " point(s) on one edge, " + c.n_right + " on the other</b>"
         : c.closes ? "closes to " + c.max_gap_mm.toFixed(4) + " mm"
         : "<b style='color:var(--warn)'>out by " + c.max_gap_mm.toFixed(4) + " mm</b>") +
        (rep.snapped ? " — snapped, " + rep.snapped.moved + " point(s) moved" : "");
      return;
    }
    var spec = readPitchSpec();
    if (!spec) { el.innerHTML = ""; return; }
    try {
      var lens = E.pitchInstanceLengths(spec);
      var tot = 0; for (var i = 0; i < lens.length; i++) tot += lens[i];
      el.innerHTML = lens.length + " pitch(es) totalling <b>" + tot.toFixed(1) +
        " mm</b> circumference. Load the DXF to build it.";
    } catch (err) {
      el.innerHTML = "<span style='color:var(--warn)'>" + escapeHtml(err.message) + "</span>";
    }
  }

  function loadFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try { buildPatternFromText(String(reader.result), file.name.replace(/\.dxf$/i, "")); }
      catch (err) {
        // Drop the old pattern: leaving it loaded makes the banner describe one
        // tyre while the user believes they are looking at another.
        state.pattern = null; state.results = null; refreshExportButtons(); showResultsChrome(false);
        $("banner").style.display = "none";
        $("emptyHint").style.display = "";
        $("emptyHint").innerHTML = "<b>Could not read that DXF.</b><br>" + escapeHtml(err.message);
        drawEditor();
        refreshValidation();
      }
    };
    reader.readAsText(file);
  }

  // Bring the loaded pattern back into line with every input that derives from
  // it, without re-parsing the DXF.
  //
  // This used to update only the block depths. The crown radii and the pitch
  // count were baked in at import, so typing a new crown radius and pressing
  // Run silently recomputed with the *old* crown -- and the crown sets where
  // the contact point sits at every lean, so the whole lean sweep was wrong
  // with nothing on screen to say so. Everything derived is rebuilt here.
  function reconcilePattern() {
    if (!state.pattern) return;
    var p = state.pattern;

    // 1. depth attributes, which live on each block
    var d = readDefaults();
    for (var i = 0; i < p.blocks.length; i++) {
      var b = p.blocks[i];
      b.height = d.height; b.draft_angle = d.draft_angle;
      b.n_lateral_sipes = d.n_lateral_sipes; b.sipe_depth_fraction = d.sipe_depth_fraction;
    }

    // 1b. tie bars track the NSD too -- a bar's engagement point is NSD minus
    // its own height, so changing the depth silently moves every bar unless the
    // stored NSD moves with it. Heights the user has set by hand are kept; the
    // rest follow the fraction.
    var frac = readTiebarFrac();
    for (var t = 0; t < (p.tiebars || []).length; t++) {
      var tb = p.tiebars[t];
      var nsd = d.height_by_zone && d.height_by_zone[tb.zone] != null ? d.height_by_zone[tb.zone] : d.height;
      tb.nsd = nsd;
      if (!tb.height_set_by_user) tb.height = Math.min(nsd, frac * nsd);
      else tb.height = Math.min(nsd, tb.height);
    }

    // 2. the crown profile, which sets the contact point at every lean
    var rc = $("crownCenter").value.trim(), rs = $("crownShoulder").value.trim();
    // A half-typed arc spec throws, and this runs on every keystroke via
    // drawEditor. Fall back to the two-radius fields until it parses; the
    // message is already in front of the user and the run is already blocked.
    var arcs = null, drops = null;
    try { arcs = readCrownArcs(p.tread_width); } catch (err) { arcs = null; }
    try { drops = readCrownDrops(p.tread_width); } catch (err) { drops = null; }
    p.crown = E.buildCrown(p.tread_width, {
      tyre_class: $("tyreType").value,
      crown_arcs: arcs,
      crown_drops: drops,
      crown_r_center: rc === "" ? undefined : parseFloat(rc),
      crown_r_shoulder: rs === "" ? undefined : parseFloat(rs),
      crown_break: readCrownBreak(),
    });

    // 3. the pitch division, which the order chart marks against
    var npRaw = $("nPitches").value.trim();
    var n = npRaw === "" ? E.estimatePitchCount(p.blocks, p.tyre_circumference)
                         : parseInt(npRaw, 10);
    n = Math.max(1, n || 1);
    var len = p.tyre_circumference / n;
    p.pitches = [];
    for (var k = 0; k < n; k++) {
      p.pitches.push({ id: "P" + String(k).padStart(3, "0"),
                       circumferential_start: k * len, circumferential_length: len });
    }
    for (var j = 0; j < p.blocks.length; j++) {
      var c = E.polygonCentroid(p.blocks[j].polygon);
      var cx = ((c[0] % p.tyre_circumference) + p.tyre_circumference) % p.tyre_circumference;
      p.blocks[j].pitch_id = p.pitches[Math.min(p.pitches.length - 1, Math.floor(cx / len))].id;
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
    if (r.n_tiebars) html += ", " + r.n_tiebars + " tie bar" + (r.n_tiebars > 1 ? "s" : "") +
      (r.n_tiebars_explicit ? " (" + r.n_tiebars_explicit + " from TIEBAR HATCH" +
        (r.n_tiebar_hatch_holes ? ", " + r.n_tiebar_hatch_holes + " with holes" : "") + ")" : "");
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
    reconcilePattern();   // the preview must reflect the crown the user just typed
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

    // Tie bars in the same window, so the patch can be placed knowing where
    // they are: filled once they have worn into contact, outlined while they
    // are still below the surface.
    var edWear = readWear();
    for (var tbi = 0; tbi < (p.tiebars || []).length; tbi++) {
      var tbar = p.tiebars[tbi];
      if (tbar.enabled === false) continue;
      var tbOn = E.tiebarEngaged(tbar, edWear);
      var tbColor = tiebarDisplayColor(tbar);
      var tpieces = E.splitAtSeam(tbar.polygon, C);
      for (var tpc = 0; tpc < tpieces.length; tpc++) {
        for (var tshift = -C; tshift <= C; tshift += C) {
          var tpoly = tpieces[tpc];
          var txmin = Infinity, txmax = -Infinity;
          for (var tv = 0; tv < tpoly.length; tv++) {
            var txx = tpoly[tv][0] + tshift - xref;
            if (txx < txmin) txmin = txx; if (txx > txmax) txmax = txx;
          }
          if (txmax < -editor.winX / 2 || txmin > editor.winX / 2) continue;
          ctx.beginPath();
          canvasLoop(ctx, tpoly, tshift - xref, mapX, mapY);
          // A hole goes into the same path and the fill uses the even-odd rule,
          // so a bar drawn with a stone ejector through it reads as one.
          for (var thi = 0; thi < (tbar.holes || []).length; thi++)
            for (var hp = 0, hpieces = E.splitAtSeam(tbar.holes[thi], C); hp < hpieces.length; hp++)
              canvasLoop(ctx, hpieces[hp], tshift - xref, mapX, mapY);
          ctx.globalAlpha = tbOn ? 0.7 : 0.85;
          if (tbOn) { ctx.fillStyle = tbColor; ctx.fill("evenodd"); }
          else ctx.setLineDash([3, 3]);
          ctx.lineWidth = 1; ctx.strokeStyle = tbColor; ctx.stroke();
          ctx.setLineDash([]); ctx.globalAlpha = 0.32;
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
    ["wear", "Tread worn", "mm", function (v) { return v >= 0; }, "cannot be negative"],
    ["tiebarHeight", "Tie-bar height", "frac of NSD", function (v) { return v > 0 && v <= 1; }, "must be between 0 and 1"],
    ["weldTol", "Weld tolerance", "mm", function (v) { return v > 0 && v <= 1; }, "must be between 0 and 1"],
    ["tiebarFrac", "Tie-bar area limit", "", function (v) { return v > 0 && v <= 1; }, "must be between 0 and 1"],
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
      try { E.validateCompound(readStiffParams()); }
      catch (err) { errs.push(err.message); }
      try { readCrownDrops(state.pattern ? state.pattern.tread_width : 0); }
      catch (err) { errs.push(err.message); }
      try { readCrownArcs(state.pattern ? state.pattern.tread_width : 0); }
      catch (err) { errs.push(err.message); }
      var w = readWear(), nsd = num("nsd");
      if (isFinite(nsd) && nsd > 0 && w >= nsd)
        errs.push("Tread worn (" + w + " mm) must be less than the NSD (" + nsd + " mm) — that much wear removes every block");
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
    reconcilePattern();
    state.running = true;
    state.ranInputs = captureInputs();   // frozen for the export
    try { state.ranBands = readBandEdges(); }
    catch (err) { failRun(err.message); return; }
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
        state.bandEdges = m.bandEdges || null;
        state.compound = m.compound || null; state.wear = m.wear || null;
        state.coupling = m.coupling || null;
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
      zoneFracs: { center: tyreClassPhysics().zone_center, intermediate: tyreClassPhysics().zone_intermediate },
      leans: currentLeans(), discreteSamples: 360, bandEdges: state.ranBands, curvatureCorrection: $("curv").checked, stride: stride,
      wear: readWear(), coupling: true, couplingSamples: 720,
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

  // Tabs and the lean selector describe results. Before a run they lead to
  // blank panels, which reads as a broken page rather than an empty one.
  // The tabs all describe a sweep; shown before one they lead to blank panels.
  // The tie-bar editor is deliberately NOT among them -- it is an input, and it
  // lives in the setup grid above.
  function showResultsChrome(on) {
    var el = $("resultsArea");
    if (el) el.style.display = on ? "" : "none";
  }

  function renderAll() {
    if (!state.results) { showResultsChrome(false); return; }
    showResultsChrome(true);
    renderNotes();
    refreshExportButtons();
    refreshCompareButtons();
    renderCards();
    renderThetaStack();
    renderPatternStrip();
    renderLeanHeatmap();
    renderOrders();
    renderZones();
    renderCrownProfile();
    renderPatchPreview();
    renderBands();
    renderTiebars();
    renderCoupling();
    renderCompare();
    renderDiagnostics();
    drawEditor();
    // Last, deliberately. Which sections a report CAN carry depends on which
    // charts exist, and they do not exist until the lines above have drawn
    // them -- run this at the top with the other chrome and every chart reads
    // as unavailable.
    renderReportChips();
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
      ["Mean Kx (longitudinal)", E.fluctuationStats(r.kx).mean.toFixed(0), "N/mm"],
      ["Mean Ky (lateral)", E.fluctuationStats(r.ky).mean.toFixed(0), "N/mm"],
      ["Kz fluctuation", (kz.cov * 100).toFixed(1), "% CoV over θ"],
      ["Blocks in patch", bc.mean.toFixed(1), "avg"],
      ["Dominant Kz order", dom ? String(dom.order) : "–", "per rev"],
    ];
    if (r.c_alpha && r.c_alpha.length) {
      var caS = E.fluctuationStats(r.c_alpha), trS = E.fluctuationStats(r.pneumatic_trail);
      cards.push(["Mean Cα (tread share)", caS.mean.toFixed(0), "N/rad — " +
        (caS.mean * Math.PI / 180).toFixed(0) + " N per degree of slip"]);
      cards.push(["Cα fluctuation", (caS.cov * 100).toFixed(1), "% CoV over θ"]);
      cards.push(["Mean Cκ", E.fluctuationStats(r.c_kappa).mean.toFixed(0), "N per unit slip ratio"]);
      cards.push(["Pneumatic trail", trS.mean.toFixed(1), "mm behind patch centre"]);
    }
    var html = "";
    for (var i = 0; i < cards.length; i++)
      html += "<div class='card'><div class='k'>" + cards[i][0] + "</div><div class='v'>" + cards[i][1] + "</div><div class='u'>" + cards[i][2] + "</div></div>";
    $("cards").innerHTML = html;
  }

  // The row chips. Rendered from the same list renderThetaStack() builds, so a
  // row can never exist without a chip or a chip without a row -- and the labels
  // carry the units, since that is what tells Ck (N) from Kx (N/mm).
  function renderRowChips(all, shown) {
    var host = $("rowToggles");
    if (!host) return;
    var on = {};
    for (var i = 0; i < shown.length; i++) on[shown[i].key] = true;
    var html = "";
    for (var k = 0; k < all.length; k++) {
      var rw = all[k];
      html += "<span class='rowchip" + (on[rw.key] ? " on" : "") + "' data-row='" +
        escapeHtml(rw.key) + "' style='" + (on[rw.key] ? "color:" + rw.color + ";" : "") +
        "' title='" + escapeHtml(rw.axis) + "'>" + escapeHtml(rw.axis) + "</span>";
    }
    host.innerHTML = html;
  }

  function toggleStackRow(key) {
    // Refuse to switch off the last row that is on. Without this the stack
    // silently falls back to showing the first row while its chip still reads
    // "on", so clicking that chip appears to do nothing -- the control and what
    // is on screen would be saying different things.
    var keys = state.stackRowKeys || [];
    if (state.stackRows[key] !== false) {
      var others = 0;
      for (var i = 0; i < keys.length; i++)
        if (keys[i] !== key && state.stackRows[keys[i]] !== false) others++;
      if (!others) return;
    }
    state.stackRows[key] = state.stackRows[key] === false;
    renderThetaStack();
    // The rows changed height, so the band's pixel geometry did too.
    setTimeout(function () { moveCursor(null); drawPatchBand(); }, 60);
  }

  // Sticky-bottom, so the tread stays on screen for the whole height of the
  // stack. It is a class on the wrapper rather than a style on the strip
  // because sticky needs an ancestor that spans both figures.
  function applyStripPin() {
    var wrap = document.querySelector(".stack-wrap");
    if (!wrap) return;
    wrap.classList.toggle("pinned", state.pinStrip !== false);
    setTimeout(function () { moveCursor(null); drawPatchBand(); }, 60);
  }

  function renderThetaStack() {
    var r = currentResult(), th = plotTheme();
    var x = r.theta_deg;
    // Land percentage: how much of the contact patch is rubber rather than
    // groove at this instant. It is the contact area normalised by the patch,
    // so it separates "the patch got smaller" from "the pattern under it got
    // emptier" -- the row above can move for either reason, this one only for
    // the second. The dotted line is the mean, so the swing is read directly.
    var landPct = [], landMean = 0;
    for (var li = 0; li < r.land_ratio.length; li++) {
      landPct.push(r.land_ratio[li] * 100);
      landMean += r.land_ratio[li] * 100;
    }
    landMean = landPct.length ? landMean / landPct.length : 0;

    var all = [
      { key: "area", y: r.contact_area, name: "Contact area", axis: "Contact area (mm²)", color: th.accent },
      { key: "land", y: landPct, name: "Land in patch", axis: "Land (%)", color: "#9b6bff",
        extra: { x: [x[0], x[x.length - 1]], y: [landMean, landMean],
                 name: "mean " + landMean.toFixed(1) + "%", color: th.inkDim } },
      { key: "kz", y: r.kz, name: "Kz (vertical)", axis: "Kz (N/mm)", color: th.good },
      { key: "kx", y: r.kx, name: "Kx (longitudinal)", axis: "Kx (N/mm)", color: th.accent2 },
      { key: "ky", y: r.ky, name: "Ky (lateral)", axis: "Ky (N/mm)", color: th.bad },
      // The discrete count is sampled on its own theta grid, so it rides along
      // as a second trace on this row rather than being resampled.
      { key: "blocks", y: r.block_count, name: "Blocks in patch", axis: "Blocks", color: th.inkDim,
        extra: { x: r.theta_discrete, y: r.block_count_discrete,
                 name: "blocks >50% in", color: th.accent, shape: "hv" } },
      { key: "centroid", y: r.centroid_y, name: "Contact centroid", axis: "Centroid y (mm)", color: th.accent2 },
    ];
    // Slip response. These are the first rows on this page that are forces
    // rather than stiffnesses, and they are NOT rescaled versions of the two
    // rows above: they weight each element by how far it has been dragged since
    // it entered the patch, so rubber near the exit counts for far more. They
    // can move opposite to Kx and Ky, which is the reason they are here.
    if (r.c_alpha && r.c_alpha.length) {
      all.push({ key: "c_alpha", y: r.c_alpha, name: "Cα — cornering (tread only)", axis: "Cα (N/rad)", color: th.bad });
      all.push({ key: "c_kappa", y: r.c_kappa, name: "Cκ — longitudinal slip", axis: "Cκ (N)", color: th.accent2 });
      var tMean = 0;
      for (var ti = 0; ti < r.pneumatic_trail.length; ti++) tMean += r.pneumatic_trail[ti];
      tMean = r.pneumatic_trail.length ? tMean / r.pneumatic_trail.length : 0;
      all.push({ key: "trail", y: r.pneumatic_trail, name: "Pneumatic trail", axis: "Trail t (mm)", color: th.good,
        extra: { x: [x[0], x[x.length - 1]], y: [tMean, tMean],
                 name: "mean " + tMean.toFixed(2) + " mm", color: th.inkDim } });
    }
    // Only the rows the user has left switched on, and never none of them.
    // toggleStackRow() will not let the last one be switched off, so this is a
    // guard against stale state from a run that carried different rows -- and it
    // writes the fallback back into the state so the chips cannot disagree with
    // what is drawn.
    state.stackRowKeys = all.map(function (rw) { return rw.key; });
    var rows = all.filter(function (rw) { return state.stackRows[rw.key] !== false; });
    if (!rows.length) { rows = [all[0]]; state.stackRows[all[0].key] = true; }
    renderRowChips(all, rows);
    var data = [], layout = {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      showlegend: true,
      legend: { orientation: "h", y: -0.10, yanchor: "top", x: 0.5, xanchor: "center", font: { size: 10 } },
      margin: { l: 78, r: 16, t: 34, b: 104 },
      // ~120 px a row, so adding one does not squeeze the rest.
      height: 120 * rows.length + 60,
      grid: { rows: rows.length, columns: 1, pattern: "independent", roworder: "top to bottom" },
      title: { text: "In-patch aggregates vs rotation angle θ  (γ = " + r.gamma_deg + "°)" +
               (rows.length < all.length ? "  —  " + rows.length + " of " + all.length + " rows shown" : ""),
               font: { size: 13 } },
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
      layout["xaxis" + (i + 1)] = {
        gridcolor: th.grid, zeroline: false,
        // Every row shows the same revolution, so they share one x-axis:
        // zooming any row zooms them all. The rolled-out pattern is its own
        // figure and is kept in step by linkThetaFigures().
        matches: i === 0 ? undefined : "x",
        range: (state.thetaRange || [0, 360]).slice(),
        showticklabels: i === rows.length - 1,
        title: i === rows.length - 1 ? { text: "rotation angle θ (deg)", font: { size: 11 } } : undefined,
        tickvals: [0, 45, 90, 135, 180, 225, 270, 315, 360],
      };
      layout["yaxis" + (i + 1)] = { gridcolor: th.grid, zeroline: false,
        title: { text: rows[i].axis, font: { size: 10 }, standoff: 6 },
        automargin: true };
    }
    Plotly.react($("thetaStack"), data, layout, { responsive: true, displayModeBar: false });
  }

  // Whether a bar was in contact FOR THE RUN ON SCREEN. Editing a height after
  // a run must not repaint the strip as though the sweep had used the new value
  // -- the curves above it did not. Falls back to the live state before any run,
  // where the strip is only a preview.
  function ranEngaged(tb) {
    if (state.wear && state.wear.engaged_ids) return state.wear.engaged_ids.indexOf(tb.id) >= 0;
    return E.tiebarEngaged(tb, readWear());
  }

  // Deliberately outside the zone palette (blue / green / amber): a tie bar
  // sitting inside a green intermediate rib has to stay visible. Used for every
  // bar the tool found for itself; a bar the designer hatched keeps the colour
  // they drew it in, which is what makes one family of bars tellable from
  // another at a glance.
  var TIEBAR_COLOR = "#9b6bff";

  function tiebarDisplayColor(tb) {
    return tb && tb.color && tb.color.css ? tb.color.css : TIEBAR_COLOR;
  }

  function anyHatchedBars() {
    return (state.pattern && state.pattern.tiebars || []).some(function (t) { return t.source === "hatch"; });
  }

  // One region -- outer boundary plus holes -- as a single Plotly path string,
  // seam-split and optionally on the theta axis. Holes are wound the other way
  // so Plotly's even-odd fill leaves them empty.
  function regionPlotPath(region, C, thetaScale) {
    var loops = [region.polygon].concat(region.holes || []), path = "";
    for (var li = 0; li < loops.length; li++) {
      var loop = li === 0 ? E.ensureCCW(loops[li]) : E.ensureCCW(loops[li]).slice().reverse();
      var pieces = E.splitAtSeam(loop, C);
      for (var pi = 0; pi < pieces.length; pi++) {
        var q = pieces[pi];
        for (var vi = 0; vi < q.length; vi++) {
          var xx = thetaScale ? (q[vi][0] / C) * 360 : q[vi][0];
          path += (vi === 0 ? "M" : "L") + xx.toFixed(3) + "," + q[vi][1].toFixed(3) + " ";
        }
        path += "Z ";
      }
    }
    return path.trim();
  }

  // The canvas counterpart: trace one loop, shifted in x, into the current path.
  function canvasLoop(ctx, loop, dx, mapX, mapY) {
    for (var i = 0; i < loop.length; i++) {
      var X = mapX(loop[i][0] + dx), Y = mapY(loop[i][1]);
      if (i === 0) ctx.moveTo(X, Y); else ctx.lineTo(X, Y);
    }
    ctx.closePath();
  }

  function patternStripTitle(wear) {
    var tb = (state.pattern.tiebars || []).filter(function (t) { return t.enabled !== false; });
    if (!tb.length) return "Rolled-out tread pattern (same θ axis)";
    var on = tb.filter(ranEngaged).length;
    return "Rolled-out tread pattern (same θ axis) — " +
      (anyHatchedBars() ? "coloured = hatched tie bars, violet = detected, " : "violet = tie bars, ") +
      on + " of " + tb.length +
      " in contact at " + (+wear).toFixed(1) + " mm wear" +
      (on < tb.length ? " (dotted outline = still below the surface)" : "");
  }

  // The rolled-out tread as Plotly shapes. Shared by the sweep tab's strip and
  // the coupling tab's, so the two can never drift apart: same geometry, same
  // theta axis, same tie-bar state -- only the emphasis differs.
  //   dimBlocks  blocks drawn faint, so the bars and their links read first
  //   links      draw the bonded links the network solver actually assembled
  function patternStripShapes(opts) {
    opts = opts || {};
    var p = state.pattern, th = plotTheme(), C = p.tyre_circumference;
    var shapes = [], zoneColor = { center: th.accent, intermediate: th.good, shoulder: th.accent2 };
    var blockAlpha = opts.dimBlocks ? 0.20 : 0.55;
    for (var bi = 0; bi < p.blocks.length; bi++) {
      var blk = p.blocks[bi];
      shapes.push({ type: "path", path: regionPlotPath(blk, C, true), xref: "x", yref: "y",
        fillcolor: hexA(zoneColor[blk.zone] || th.accent, blockAlpha), line: { width: 0 } });
    }
    // Tie bars, on the same strip as the blocks. The sweep already counts the
    // engaged ones as land -- the worker merges them into the block list before
    // rasterising -- but with nothing drawn there was no way to see it, and a
    // step in the curves at some wear had no visible cause on the pattern.
    // Engaged bars are filled like land; bars still below the surface are drawn
    // as outlines so you can see where they sit and what is about to arrive.
    for (var ti = 0; ti < (p.tiebars || []).length; ti++) {
      var tb = p.tiebars[ti];
      if (tb.enabled === false) continue;
      var engaged = ranEngaged(tb);
      var tc = tiebarDisplayColor(tb);
      shapes.push({ type: "path", path: regionPlotPath(tb, C, true), xref: "x", yref: "y",
        fillcolor: engaged ? hexA(tc, 0.85) : "rgba(0,0,0,0)",
        line: { width: 1.1, color: tc, dash: engaged ? undefined : "dot" } });
    }
    // The bonded links, drawn exactly as the solver assembled them: one line per
    // shared wall, from the bar's centre out to that wall. Submerged bars carry
    // links too -- that is the whole point of the tab.
    if (opts.links) shapes = shapes.concat(couplingLinkShapes(C));
    // The designer's rib cuts, drawn where they actually fall on the tread.
    if (state.bandEdges) {
      for (var be = 0; be < state.bandEdges.length; be++) {
        shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, yref: "y",
          y0: state.bandEdges[be], y1: state.bandEdges[be],
          line: { color: th.ink || cssVar("--ink"), width: 1.5 } });
      }
    }
    return shapes;
  }

  // Block centroids, which the pattern does not carry (only the bars do).
  // Cached against the pattern object, so re-rendering the strip on a theme
  // change or a lean change does not re-integrate a few hundred outlines.
  var blockCentroids = { of: null, list: null };

  function blockCentroid(i) {
    var p = state.pattern;
    if (blockCentroids.of !== p) {
      blockCentroids.of = p;
      blockCentroids.list = p.blocks.map(function (b) { return E.polygonCentroid(b.polygon); });
    }
    return blockCentroids.list[i];
  }

  // A link bonds two outlines that share a wall, so across θ it spans about one
  // pitch. Anything wider is the seam wrap-around and is dropped rather than
  // streaked across the whole strip.
  var LINK_MAX_SPAN_DEG = 45;

  // One line per bonded wall, from the tie bar to whatever it is bonded to.
  // These are the solver's own links, read straight off tb.links -- so a bar
  // whose outline misses the groove wall shows no line here, which is exactly
  // why it contributes no coupling stiffness above.
  function couplingLinkShapes(C) {
    var p = state.pattern, bars = p.tiebars || [], out = [];
    for (var i = 0; i < bars.length; i++) {
      var tb = bars[i];
      if (tb.enabled === false || !tb.links) continue;
      for (var k = 0; k < tb.links.length; k++) {
        var lk = tb.links[k], oc;
        if (lk.kind === "block") {
          if (!p.blocks[lk.index]) continue;
          oc = blockCentroid(lk.index);
        } else {
          var ob = bars[lk.index];
          if (!ob || ob.enabled === false) continue;
          oc = [ob.centroid_x, ob.centroid_y];
        }
        var x0 = (tb.centroid_x / C) * 360, x1 = (oc[0] / C) * 360;
        if (Math.abs(x1 - x0) > LINK_MAX_SPAN_DEG) continue;
        out.push({ type: "line", xref: "x", yref: "y",
          x0: x0, y0: tb.centroid_y, x1: x1, y1: oc[1],
          line: { color: hexA(tiebarDisplayColor(tb), 0.6), width: 1.2 } });
      }
    }
    return out;
  }

  function stripLayout(th, title, range) {
    var p = state.pattern;
    return {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 78, r: 16, t: 24, b: 40 }, height: 220,
      title: { text: title, font: { size: 13 } },
      xaxis: { range: range.slice(), gridcolor: th.grid, zeroline: false, title: { text: "rotation angle θ (deg)", font: { size: 11 } }, tickvals: [0, 45, 90, 135, 180, 225, 270, 315, 360] },
      yaxis: { range: [-p.tread_width / 2, p.tread_width / 2], gridcolor: th.grid, zeroline: false, title: { text: "lateral y (mm)", font: { size: 10 } }, scaleanchor: undefined },
    };
  }

  // Plotly needs a trace to lay out the axes even when everything drawn is a
  // shape, so both strips carry one invisible point.
  var STRIP_TRACE = [{ x: [0], y: [0], type: "scatter", mode: "markers", marker: { opacity: 0 }, hoverinfo: "skip" }];

  function renderPatternStrip() {
    var th = plotTheme();
    var tbWear = state.wear ? state.wear.mm : readWear();
    var layout = stripLayout(th, patternStripTitle(tbWear), state.thetaRange || [0, 360]);
    layout.shapes = patternStripShapes({});
    Plotly.react($("patternStrip"), STRIP_TRACE, layout, { responsive: true, displayModeBar: false })
      .then(linkThetaFigures);
  }

  // The same tread under the coupling curve. Without it the network plot is a
  // pair of lines with no way to see which part of the pattern is under the
  // patch when the gain moves.
  function renderCouplingStrip(c) {
    var gd = $("cplStrip");
    if (!gd) return;
    var th = plotTheme();
    var bars = (state.pattern.tiebars || []).filter(function (t) { return t.enabled !== false; });
    var title = "Rolled-out tread pattern (same θ axis) — " + c.n_links + " bonded link(s) across " +
      bars.length + " tie bar(s), " + c.n_components + " independent group(s)";
    var layout = stripLayout(th, title, state.cplRange || [0, 360]);
    layout.shapes = patternStripShapes({ dimBlocks: true, links: true });
    Plotly.react(gd, STRIP_TRACE, layout, { responsive: true, displayModeBar: false })
      .then(linkCouplingFigures);
  }

  // Zoom on either coupling figure moves both. Deliberately its own range state
  // rather than sharing thetaRange with the sweep tab: zooming one tab must not
  // silently re-frame another.
  function linkCouplingFigures() {
    if (cplLink.hooked) return;
    var plot = $("cplPlot"), strip = $("cplStrip");
    if (!plot || !strip) return;
    // The two figures are drawn in order but resolve independently, so the
    // curve above may not be a Plotly graph yet when the strip below finishes.
    if (typeof plot.on !== "function" || typeof strip.on !== "function") {
      setTimeout(linkCouplingFigures, 60);
      return;
    }
    cplLink.hooked = true;
    function syncFrom(src, dst) {
      src.on("plotly_relayout", function (ev) {
        if (cplLink.syncing || !ev) return;
        var lo = ev["xaxis.range[0]"], hi = ev["xaxis.range[1]"];
        if (lo == null && Array.isArray(ev["xaxis.range"])) { lo = ev["xaxis.range"][0]; hi = ev["xaxis.range"][1]; }
        var reset = ev["xaxis.autorange"] === true;
        if (lo == null && !reset) return;
        state.cplRange = reset ? [0, 360] : [lo, hi];
        cplLink.syncing = true;
        Plotly.relayout(dst, { "xaxis.range": state.cplRange.slice() })
          .then(function () { cplLink.syncing = false; })
          .catch(function () { cplLink.syncing = false; });
      });
    }
    syncFrom(plot, strip);
    syncFrom(strip, plot);
  }

  // One place for the units of every sweep quantity, so a curve, a colour bar,
  // a band chart and an export can never disagree about what a number is.
  var METRIC_LABEL = {
    kz: "Kz (N/mm)", kx: "Kx (N/mm)", ky: "Ky (N/mm)",
    contact_area: "Contact area (mm²)", block_count: "Blocks", land_ratio: "Land ratio",
    c_alpha: "Cα (N/rad)", c_kappa: "Cκ (N)", c_mz: "Cmz (N·mm/rad)",
    pneumatic_trail: "Pneumatic trail (mm)",
  };
  var ORDER_LABEL = {
    kz: "Kz", kx: "Kx", ky: "Ky", contact_area: "contact area", block_count: "block count",
    c_alpha: "Cα (cornering)", c_kappa: "Cκ (longitudinal)",
  };

  function renderLeanHeatmap() {
    var th = plotTheme();
    var metric = state.heatMetric;
    var x = state.results[0].theta_deg;
    var y = [], z = [];
    for (var i = 0; i < state.results.length; i++) { y.push(state.results[i].gamma_deg); z.push(state.results[i][metric]); }
    var label = METRIC_LABEL[metric];
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
    var label = ORDER_LABEL[metric];
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

  // The crown profile, drawn from the same arrays the sweep integrates: the
  // section shape, the local radius, and where the tyre touches at every lean
  // in the run. A tread arc spec is otherwise a line of text the user has to
  // take on trust.
  function renderCrownProfile() {
    var host = $("crownPlot");
    if (!host || !state.pattern) return;
    var th = plotTheme(), crown = state.pattern.crown;
    var y = Array.prototype.slice.call(crown.y);
    var z = Array.prototype.slice.call(crown.z);
    var r = Array.prototype.slice.call(crown.r);

    // Contact points at the lean angles this run actually covered.
    var cy = [], cz = [], ct = [];
    for (var i = 0; i < (state.results || []).length; i++) {
      var g = state.results[i].gamma_deg;
      var yc = E.crownContactLateral(crown, g);
      cy.push(yc); cz.push(E.crownDrop(crown, yc)); ct.push("γ = " + g + "°");
    }

    var data = [
      { x: y, y: z, type: "scatter", mode: "lines", name: "crown section",
        line: { color: th.accent, width: 2.5 }, yaxis: "y" },
      { x: y, y: r, type: "scatter", mode: "lines", name: "local radius (right)",
        line: { color: th.accent2, width: 1.4, dash: "dot" }, yaxis: "y2" },
    ];
    if (cy.length) {
      data.push({ x: cy, y: cz, text: ct, type: "scatter", mode: "markers+text",
                  name: "contact point at each lean", textposition: "bottom center",
                  textfont: { size: 9, color: th.inkDim },
                  marker: { size: 9, color: th.good, symbol: "circle-open", line: { width: 2 } },
                  yaxis: "y" });
    }
    // Breakpoints, when the profile came from an explicit arc spec.
    var shapes = [];
    if (crown.arcs && crown.arcs.length > 1) {
      for (var k = 0; k < crown.arcs.length - 1; k++) {
        for (var sgn = -1; sgn <= 1; sgn += 2) {
          shapes.push({ type: "line", x0: sgn * crown.arcs[k].to_mm, x1: sgn * crown.arcs[k].to_mm,
                        yref: "paper", y0: 0, y1: 1,
                        line: { color: th.inkDim, width: 1, dash: "dash" } });
        }
      }
    }
    var title = crown.arcs
      ? "Tread arc profile — " + crown.arcs.map(function (a, i) { return "R" + (i + 1) + " " + a.radius + " mm"; }).join(" / ") +
        ", breaks dashed"
      : "Tread crown profile — two-radius blend";
    Plotly.react(host, data, {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 70, r: 70, t: 40, b: 76 }, height: 340, shapes: shapes,
      legend: { orientation: "h", y: -0.22, yanchor: "top", x: 0.5, xanchor: "center", font: { size: 10 } },
      title: { text: title + "  ·  max reachable lean " + E.maxSupportedLean(crown).toFixed(1) + "°",
               font: { size: 13 } },
      xaxis: { title: { text: "lateral y — developed arc length from the centreline (mm)", font: { size: 11 } },
               gridcolor: th.grid, zeroline: true, zerolinecolor: th.grid },
      yaxis: { title: { text: "drop below centreline (mm)", font: { size: 11 } },
               gridcolor: th.grid, autorange: "reversed" },
      yaxis2: { title: { text: "local radius (mm)", font: { size: 11 } }, overlaying: "y", side: "right",
                showgrid: false, rangemode: "tozero",
                tickfont: { color: th.accent2 }, titlefont: { color: th.accent2 } },
    }, { responsive: true, displayModeBar: false });
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
    if (state.wear && state.wear.n_tiebars_total)
      flags.push(flag(state.wear.n_tiebars_engaged ? "good" : "warn",
        state.wear.n_tiebars_engaged + " of " + state.wear.n_tiebars_total + " tie bars in contact at " +
        state.wear.mm.toFixed(1) + " mm wear"));
    if (state.wear && state.wear.mm > 0) flags.push(flag("good", "tread worn " + state.wear.mm.toFixed(1) + " mm"));
    $("flags").innerHTML = flags.join(" ");

    // What the compound resolved to, and what the importer actually saw. Both
    // used to be invisible: E was derived from Shore A behind the scenes, and a
    // DXF entity the reader could not handle was dropped without a word.
    if (state.compound) {
      html += "<h3 style='font-size:14px;margin:18px 0 6px'>Compound as used</h3>" +
        "<table class='metrics'>" +
        "<tr><td>Young's modulus E</td><td class='num'>" + state.compound.E.toFixed(4) + " N/mm²</td></tr>" +
        "<tr><td>Shear modulus G = E / 2(1+ν)</td><td class='num'>" + state.compound.G.toFixed(4) + " N/mm²</td></tr>" +
        "<tr><td>Gent shape coefficient k</td><td class='num'>" + state.compound.k.toFixed(4) + "</td></tr>" +
        "<tr><td>Poisson ν</td><td class='num'>" + state.compound.nu + "</td></tr>" +
        "<tr><td>Bulk modulus (incompressibility cap)</td><td class='num'>" + state.compound.bulk_modulus + " N/mm²</td></tr>" +
        "<tr><td>Source</td><td>" + escapeHtml(state.compound.source) + "</td></tr></table>";
    }
    var rep = state.report;
    if (rep && rep.entity_types) {
      var types = Object.keys(rep.entity_types).sort(function (a, b) { return rep.entity_types[b] - rep.entity_types[a]; });
      html += "<h3 style='font-size:14px;margin:18px 0 6px'>DXF import</h3><table class='metrics'>" +
        "<tr><td>entities read</td><td class='num'>" + rep.n_entities + "</td></tr>" +
        "<tr><td>polyline chains after flattening</td><td class='num'>" + rep.n_segments + "</td></tr>" +
        "<tr><td>INSERT references expanded</td><td class='num'>" + (rep.n_inserts || 0) + "</td></tr>" +
        "<tr><td>weld tolerance</td><td class='num'>" + (rep.weld_tolerance || 0) + " mm</td></tr>" +
        "<tr><td>arrangement edges / splits</td><td class='num'>" + (rep.n_arrangement_edges || 0) +
          " / " + (rep.n_arrangement_splits || 0) + "</td></tr>" +
        "<tr><td>enclosed regions found</td><td class='num'>" + (rep.n_faces || 0) + "</td></tr>" +
        "<tr><td>&nbsp;&nbsp;→ blocks / seam-wrapped / tie bars</td><td class='num'>" + rep.n_blocks +
          " / " + rep.n_wrapped + " / " + (rep.n_tiebars || 0) + "</td></tr>" +
        "<tr><td title='Tie bars found by the geometric detector, kept after the explicit " +
          "hatches took priority, drawn as a HATCH on the TIEBAR layer, and found both ways.'>" +
          "&nbsp;&nbsp;→ bars: detected / kept / hatched / both</td><td class='num'>" +
          (rep.n_tiebars_detected || 0) + " / " + (rep.n_tiebars_detected_retained || 0) + " / " +
          (rep.n_tiebars_explicit || 0) + " / " + (rep.n_tiebars_merged || 0) + "</td></tr>" +
        "<tr><td>&nbsp;&nbsp;→ HATCH entities read / holes in them</td><td class='num'>" +
          (rep.n_tiebar_hatches || 0) + " / " + (rep.n_tiebar_hatch_holes || 0) + "</td></tr>" +
        "<tr><td>discarded: open chains / below min area</td><td class='num'>" + rep.n_discarded_open +
          " / " + rep.n_discarded_small + "</td></tr>" +
        "<tr><td>entity types</td><td>" + escapeHtml(types.map(function (t) { return t + "×" + rep.entity_types[t]; }).join(", ")) + "</td></tr>";
      var uns = Object.keys(rep.unsupported_types || {});
      html += "<tr><td>not read as geometry</td><td>" +
        (uns.length ? "<b style='color:var(--warn)'>" + escapeHtml(uns.map(function (t) { return t + "×" + rep.unsupported_types[t]; }).join(", ")) + "</b>" : "none") +
        "</td></tr></table>";
    }
    $("diagTable").innerHTML = html;
  }
  function flag(kind, text) { return "<span class='flag " + kind + "'>" + text + "</span>"; }

  // ---- linked θ figures: shared zoom + a crosshair through everything ---
  //
  // The sweep rows and the rolled-out pattern are separate Plotly figures but
  // they show one revolution, so they are held on the same x-range and a single
  // vertical line follows the cursor through every row and down onto the
  // pattern. That is what turns "there is a dip near 140°" into "that dip is
  // under this block".
  //
  // The line is a positioned div rather than a Plotly shape on purpose: at
  // mousemove rates, relayouting a figure that carries ~170 block polygons is
  // far too slow to track a cursor, and a div costs nothing.
  var linkState = { hooked: false, syncing: false };
  var cplLink = { hooked: false, syncing: false };

  function plotGeom(gd) {
    if (!gd || !gd._fullLayout || !gd._fullLayout._size) return null;
    var sz = gd._fullLayout._size, ax = gd._fullLayout.xaxis;
    if (!ax || !ax.range) return null;
    var ya = gd._fullLayout.yaxis;
    return { left: sz.l, width: sz.w, top: sz.t,
             height: Math.max(0, gd.clientHeight - sz.t - sz.b),
             r0: ax.range[0], r1: ax.range[1],
             y0: ya && ya.range ? ya.range[0] : null,
             y1: ya && ya.range ? ya.range[1] : null };
  }
  function xToPixel(gd, x) {
    var g = plotGeom(gd);
    if (!g || g.r1 === g.r0) return null;
    return g.left + ((x - g.r0) / (g.r1 - g.r0)) * g.width;
  }
  function pixelToX(gd, px) {
    var g = plotGeom(gd);
    if (!g || !g.width) return null;
    return g.r0 + ((px - g.left) / g.width) * (g.r1 - g.r0);
  }

  function cursorEl(gd) {
    var host = gd.parentNode;
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    var line = host.querySelector(".xcursor");
    if (!line) { line = document.createElement("div"); line.className = "xcursor"; host.appendChild(line); }
    return line;
  }

  function moveCursor(theta) {
    [$("thetaStack"), $("patternStrip")].forEach(function (gd) {
      if (!gd || !gd._fullLayout) return;
      var line = cursorEl(gd), g = plotGeom(gd);
      var px = theta == null ? null : xToPixel(gd, theta);
      if (px == null || !g || px < g.left - 1 || px > g.left + g.width + 1) { line.style.display = "none"; return; }
      line.style.display = "block";
      line.style.left = px + "px";
      line.style.top = g.top + "px";
      line.style.height = g.height + "px";
    });
    updateCursorReadout(theta);
  }

  // Every value at the hovered angle, so a peak or a dip can be read straight
  // off instead of eyeballed against six separate y-axes.
  function updateCursorReadout(theta) {
    var box = $("cursorReadout");
    if (!box) return;
    var r = currentResult();
    if (theta == null || !r || !r.theta_deg.length) { box.style.display = "none"; return; }
    var n = r.theta_deg.length;
    var i = Math.max(0, Math.min(n - 1, Math.round((theta / 360) * n)));
    var fields = [
      ["θ", r.theta_deg[i].toFixed(1) + "°"],
      ["area", r.contact_area[i].toFixed(0) + " mm²"],
      ["land", (r.land_ratio[i] * 100).toFixed(1) + "%"],
      ["Kz", r.kz[i].toFixed(0)],
      ["Kx", r.kx[i].toFixed(0)],
      ["Ky", r.ky[i].toFixed(0)],
      ["blocks", r.block_count[i].toFixed(2)],
      ["centroid", r.centroid_y[i].toFixed(2) + " mm"],
    ];
    box.style.display = "flex";
    box.innerHTML = fields.map(function (f) {
      return "<span class='ck'>" + f[0] + "</span><span class='cv'>" + f[1] + "</span>";
    }).join("");
  }

  // ---- the contact-patch band ------------------------------------------
  //
  // The patch outline drawn on the rolled-out pattern where it actually sits,
  // and a translucent band of the same circumferential extent carried up
  // through every sweep row -- so "the dip at 140 deg" and "these blocks are
  // what is under the patch there" are one picture instead of two.
  //
  // Drag it along theta. Lateral position is NOT draggable here: it is set by
  // the crown and the lean angle (or by the y-centre field), and letting it be
  // shoved sideways on this chart would silently contradict the physics that
  // put it there. Use 3 - Contact patch for that.
  //
  // Built as an SVG overlay over the plot area rather than Plotly shapes: the
  // strip carries a few hundred block polygons and relayouting it at drag rates
  // cannot keep up with a cursor. Dragging only moves this overlay.
  var SVGNS = "http://www.w3.org/2000/svg";
  var patchDrag = { active: false, grabTheta: 0, startTheta: 0 };

  function patchOverlay(gd) {
    var host = gd.parentNode;
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    var svg = host.querySelector("svg.cpov");
    if (!svg) {
      svg = document.createElementNS(SVGNS, "svg");
      svg.setAttribute("class", "cpov");
      // The band itself takes the pointer; everything else must fall through to
      // Plotly so drag-to-zoom still works on the rest of the chart.
      svg.style.pointerEvents = "none";
      // Bound to the overlay, not to the band rects: the rects are rebuilt on
      // every redraw, so a listener on one of them would not survive between
      // the two halves of a double-click.
      svg.addEventListener("pointerdown", function (ev) {
        if (ev.target.classList && ev.target.classList.contains("cpband")) onPatchGrab(ev);
      });
      svg.addEventListener("dblclick", function (ev) {
        if (ev.target.classList && ev.target.classList.contains("cpband")) resetThetaZoom(ev);
      });
      host.appendChild(svg);
    }
    svg.__gd = gd;
    return svg;
  }

  function patchThetaSpanDeg() {
    var r = currentResult();
    if (!r || !r.patch || !r.patch.outline || !r.patch.outline.length) return null;
    var C = state.pattern.tyre_circumference;
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < r.patch.outline.length; i++) {
      var x = r.patch.outline[i][0];
      if (x < lo) lo = x;
      if (x > hi) hi = x;
    }
    return [(lo / C) * 360, (hi / C) * 360];
  }

  function clampPatchTheta(t) {
    t = ((t % 360) + 360) % 360;
    return t;
  }

  // Draw the band (both figures) and the outline (pattern strip only).
  function drawPatchBand() {
    var span = patchThetaSpanDeg();
    var figs = [$("thetaStack"), $("patternStrip")];
    if (!span) {
      figs.forEach(function (gd) { if (gd && gd._fullLayout) patchOverlay(gd).innerHTML = ""; });
      return;
    }
    if (state.patchTheta == null) state.patchTheta = 180;
    var centre = clampPatchTheta(state.patchTheta);
    var r = currentResult(), C = state.pattern.tyre_circumference;

    figs.forEach(function (gd) {
      if (!gd || !gd._fullLayout) return;
      var svg = patchOverlay(gd), g = plotGeom(gd);
      svg.innerHTML = "";
      if (!g || !g.width) return;
      svg.setAttribute("width", g.width);
      svg.setAttribute("height", g.height);
      svg.style.left = g.left + "px";
      svg.style.top = g.top + "px";
      var isStrip = gd === $("patternStrip");

      // The patch can straddle the seam, so draw it at theta and at theta +- 360
      // and let the overlay's own bounds clip. That is also what the tyre does.
      for (var k = -1; k <= 1; k++) {
        var c = centre + k * 360;
        var xa = xToPixel(gd, c + span[0]), xb = xToPixel(gd, c + span[1]);
        if (xa == null || xb == null) continue;
        var left = Math.min(xa, xb) - g.left, w = Math.abs(xb - xa);
        if (left + w < -2 || left > g.width + 2) continue;

        var rect = document.createElementNS(SVGNS, "rect");
        rect.setAttribute("class", "cpband");
        rect.setAttribute("x", left);
        rect.setAttribute("y", 0);
        rect.setAttribute("width", w);
        rect.setAttribute("height", g.height);
        rect.style.pointerEvents = "all";
        rect.style.cursor = patchDrag.active ? "grabbing" : "ew-resize";
        svg.appendChild(rect);

        if (isStrip && g.y0 != null && r.patch.outline.length > 2) {
          var path = document.createElementNS(SVGNS, "path");
          var d = "";
          for (var i = 0; i < r.patch.outline.length; i++) {
            var p = r.patch.outline[i];
            var px = xToPixel(gd, c + (p[0] / C) * 360) - g.left;
            var py = g.height * (1 - (p[1] - g.y0) / (g.y1 - g.y0));
            d += (i === 0 ? "M" : "L") + px.toFixed(2) + "," + py.toFixed(2) + " ";
          }
          path.setAttribute("class", "cpoutline");
          path.setAttribute("d", d + "Z");
          svg.appendChild(path);
        }
      }
    });
    updatePatchLabel(centre);
  }

  function updatePatchLabel(centre) {
    var row = $("patchThetaRow"), el = $("patchThetaLabel"), box = $("patchTheta");
    if (!row || !el) return;
    var r = currentResult();
    if (!r) { row.style.display = "none"; return; }
    row.style.display = "block";
    if (box && box !== document.activeElement) box.value = centre.toFixed(1);
    var n = r.theta_deg.length;
    var i = Math.max(0, Math.min(n - 1, Math.round((centre / 360) * n) % n));
    el.innerHTML = " — contact <b>" + r.contact_area[i].toFixed(0) + "</b> mm² (<b>" +
      (r.land_ratio[i] * 100).toFixed(1) + "%</b> land), Kz <b>" +
      r.kz[i].toFixed(0) + "</b>, Kx <b>" + r.kx[i].toFixed(0) + "</b>, Ky <b>" +
      r.ky[i].toFixed(0) + "</b>, " + r.block_count[i].toFixed(2) + " blocks in the patch" +
      " · <span class='hint'>drag the shaded band, or type an angle</span>";
  }

  // Used by the number box and by the browser smoke test.
  function setPatchTheta(t) {
    if (!isFinite(t)) return;
    state.patchTheta = clampPatchTheta(t);
    drawPatchBand();
  }

  function resetThetaZoom(ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    state.thetaRange = [0, 360];
    linkState.syncing = true;
    Promise.all([$("thetaStack"), $("patternStrip")].map(function (gd) {
      return gd && gd._fullLayout ? Plotly.relayout(gd, { "xaxis.range": [0, 360] }) : null;
    })).then(function () { linkState.syncing = false; drawPatchBand(); })
      .catch(function () { linkState.syncing = false; });
  }

  function onPatchGrab(ev) {
    var gd = ev.target.ownerSVGElement && ev.target.ownerSVGElement.__gd;
    if (!gd) return;
    var t = pixelToX(gd, ev.clientX - gd.getBoundingClientRect().left);
    if (t == null) return;
    // Deliberately NOT preventDefault: that suppresses the synthesised click
    // and dblclick, and dblclick on the band is how the zoom is reset.
    // stopPropagation is enough to keep Plotly from starting a rubber band.
    // Text selection is handled by the user-select below.
    ev.stopPropagation();
    patchDrag.active = true;
    patchDrag.moved = false;
    patchDrag.gd = gd;
    patchDrag.grabTheta = t;
    patchDrag.startTheta = clampPatchTheta(state.patchTheta == null ? 180 : state.patchTheta);
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", onPatchDrag);
    window.addEventListener("pointerup", onPatchDrop);
  }

  function onPatchDrag(ev) {
    if (!patchDrag.active) return;
    var gd = patchDrag.gd;
    var t = pixelToX(gd, ev.clientX - gd.getBoundingClientRect().left);
    if (t == null) return;
    // x only: the pointer's y is read and discarded on purpose.
    if (Math.abs(t - patchDrag.grabTheta) < 1e-9 && !patchDrag.moved) return;
    patchDrag.moved = true;
    state.patchTheta = clampPatchTheta(patchDrag.startTheta + (t - patchDrag.grabTheta));
    drawPatchBand();
    moveCursor(state.patchTheta);
  }

  function onPatchDrop() {
    var moved = patchDrag.moved;
    patchDrag.active = false;
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onPatchDrag);
    window.removeEventListener("pointerup", onPatchDrop);
    // A plain click must leave the overlay alone: redrawing it here would swap
    // the band out between the two halves of a double-click.
    if (moved) drawPatchBand();
  }

  function linkThetaFigures() {
    var stack = $("thetaStack"), strip = $("patternStrip");
    if (!stack || !strip) return;
    moveCursor(null);
    drawPatchBand();
    if (linkState.hooked) return;   // survives every redraw; wired once
    linkState.hooked = true;

    // Zoom on either figure moves both, including a double-click reset.
    function syncFrom(src, dst) {
      src.on("plotly_relayout", function (ev) {
        if (linkState.syncing || !ev) return;
        // An interactive zoom emits the indexed keys; a programmatic relayout
        // emits the whole array. Accept either.
        var lo = ev["xaxis.range[0]"], hi = ev["xaxis.range[1]"];
        if (lo == null && Array.isArray(ev["xaxis.range"])) {
          lo = ev["xaxis.range"][0]; hi = ev["xaxis.range"][1];
        }
        var reset = ev["xaxis.autorange"] === true;
        if (lo == null && !reset) return;
        state.thetaRange = reset ? [0, 360] : [lo, hi];
        linkState.syncing = true;
        Plotly.relayout(dst, { "xaxis.range": state.thetaRange.slice() })
          .then(function () { linkState.syncing = false; moveCursor(null); drawPatchBand(); })
          .catch(function () { linkState.syncing = false; });
      });
    }
    syncFrom(stack, strip);
    syncFrom(strip, stack);

    // Driven from the raw pointer rather than plotly_hover, so the line tracks
    // continuously and does not need the cursor to be near a trace.
    [stack, strip].forEach(function (gd) {
      gd.addEventListener("mousemove", function (ev) {
        if (patchDrag.active) return;
        moveCursor(pixelToX(gd, ev.clientX - gd.getBoundingClientRect().left));
      });
      gd.addEventListener("mouseleave", function () {
        if (!patchDrag.active) moveCursor(null);
      });
      // The band's pixel geometry is tied to the plot area, so it has to be
      // recomputed whenever Plotly re-lays it out -- a window resize, the
      // responsive reflow when a tab becomes visible, an autoscale.
      gd.on("plotly_afterplot", drawPatchBand);
    });
    window.addEventListener("resize", function () { setTimeout(drawPatchBand, 60); });
  }

  // ---- bands (ribs) ----------------------------------------------------
  var BAND_LABEL = { contact_area: "contact area (mm²)", kz: "Kz (N/mm)", kx: "Kx (N/mm)",
                     ky: "Ky (N/mm)", block_count: "blocks in patch",
                     c_alpha: "Cα (N/rad)", c_kappa: "Cκ (N)" };

  function bandName(b) { return "y " + b.y_lo.toFixed(1) + " to " + b.y_hi.toFixed(1) + " mm"; }

  function renderBands() {
    var r = currentResult();
    var has = r && r.bands && r.bands.length;
    $("bandsEmpty").style.display = has ? "none" : "";
    $("bandMetricRow").style.display = has ? "" : "none";
    if (!has) { Plotly.purge($("bandsPlot")); $("bandsTable").innerHTML = ""; return; }

    var th = plotTheme(), metric = state.bandMetric || "contact_area";
    var palette = [th.accent, th.good, th.accent2, th.bad, th.inkDim,
                   "#9b6bff", "#00b3a4", "#e2679a", "#8a9a5b", "#c26b1f"];
    var data = r.bands.map(function (b, i) {
      return { x: r.theta_deg, y: b[metric], type: "scatter", mode: "lines",
               name: bandName(b), line: { color: palette[i % palette.length], width: 1.4 } };
    });
    Plotly.react($("bandsPlot"), data, {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 72, r: 16, t: 40, b: 88 }, height: 460,
      legend: { orientation: "h", y: -0.16, yanchor: "top", x: 0.5, xanchor: "center", font: { size: 10 } },
      title: { text: BAND_LABEL[metric] + " per band vs θ  (γ = " + r.gamma_deg + "°)", font: { size: 13 } },
      xaxis: { title: { text: "rotation angle θ (deg)", font: { size: 11 } }, range: [0, 360], gridcolor: th.grid },
      yaxis: { title: { text: BAND_LABEL[metric], font: { size: 11 } }, gridcolor: th.grid },
    }, { responsive: true, displayModeBar: false });

    var rows = "<table class='metrics'><tr><th>Band</th><th>width</th><th>mean area</th>" +
      "<th>mean Kx</th><th>mean Ky</th><th>mean Kz</th><th>Kz CoV</th><th>share of Kz</th></tr>";
    var totKz = 0;
    r.bands.forEach(function (b) { totKz += E.fluctuationStats(b.kz).mean; });
    r.bands.forEach(function (b) {
      var a = E.fluctuationStats(b.contact_area), z = E.fluctuationStats(b.kz);
      rows += "<tr><td>" + escapeHtml(bandName(b)) + "</td><td class='num'>" + b.width_mm.toFixed(1) +
        " mm</td><td class='num'>" + a.mean.toFixed(0) + " mm²</td><td class='num'>" +
        E.fluctuationStats(b.kx).mean.toFixed(0) + "</td><td class='num'>" +
        E.fluctuationStats(b.ky).mean.toFixed(0) + "</td><td class='num'>" + z.mean.toFixed(0) +
        "</td><td class='num'>" + (z.cov * 100).toFixed(1) + "%</td><td class='num'>" +
        (totKz > 0 ? (100 * z.mean / totKz).toFixed(1) : "0.0") + "%</td></tr>";
    });
    $("bandsTable").innerHTML = rows + "</table>" +
      "<div class='hint' style='margin-top:6px'>Bands sum exactly to the whole-tread total — they are a partition of the same correlation, not a second calculation.</div>";
  }

  // ---- tie bars --------------------------------------------------------
  // Everything the arrangement found between blocks is listed here, whether or
  // not the auto rule liked it: a bar you can see but cannot reach is worse than
  // no detection at all. Editing a row writes straight onto the pattern, so the
  // next Run picks it up.
  function tiebarList() { return (state.pattern && state.pattern.tiebars) || []; }

  function renderTiebars() {
    var tb = tiebarList();
    var empty = $("tbEmpty"), body = $("tbBody");
    if (!empty || !body) return;
    empty.style.display = tb.length ? "none" : "";
    body.style.display = tb.length ? "" : "none";
    if (!tb.length) { Plotly.purge($("tbPlot")); $("tbTable").innerHTML = ""; return; }

    var wear = readWear();
    refreshTiebarSummary();
    drawTiebarPlan();

    renderTiebarGroups();

    var rows = "<table class='metrics'><tr><th>Bar</th><th>zone</th><th>θ (deg)</th><th>y (mm)</th>" +
      "<th>area (mm²)</th><th>NSD (mm)</th><th>height (mm)</th><th>engages at wear</th>" +
      "<th>state</th>" +
      "<th title='Is this region a tie bar at all? Untick to exclude it completely — no contact " +
      "area, no coupling stiffness, not in the network. Use it for a region the detector picked " +
      "up that is really open groove.'>include</th></tr>";
    var circ = state.pattern.tyre_circumference;
    tb.forEach(function (t, i) {
      var engaged = E.tiebarEngaged(t, wear);
      var at = E.tiebarEngagementWear(t);
      var theta = ((t.centroid_x % circ) + circ) % circ / circ * 360;
      var provenance = (t.source === "hatch"
        ? "drawn as a HATCH on the " + (t.layer || "TIEBAR") + " layer" +
          (t.merged_automatic ? ", and found by the detector too" : "")
        : "found by area and adjacency") +
        ((t.holes || []).length ? " · " + t.holes.length + " hole(s)" : "");
      rows += "<tr class='" + (t.enabled === false ? "off" : engaged ? "engaged" : "") + "'>" +
        "<td title='" + escapeHtml(provenance) + "'>" +
        "<span style='display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;" +
        "vertical-align:middle;background:" + escapeHtml(tiebarDisplayColor(t)) + "'></span>" +
        escapeHtml(t.id) + "</td>" +
        "<td>" + escapeHtml(t.zone) + "</td>" +
        "<td class='num'>" + theta.toFixed(1) + "</td>" +
        "<td class='num'>" + t.centroid_y.toFixed(1) + "</td>" +
        "<td class='num'>" + t.area.toFixed(1) + "</td>" +
        "<td class='num'>" + t.nsd.toFixed(2) + "</td>" +
        "<td class='num'><input type='number' step='0.1' min='0.1' data-tb='" + i +
          "' data-tbfield='height' value='" + (+t.height).toFixed(2) + "' /></td>" +
        "<td class='num'>" + at.toFixed(2) + " mm</td>" +
        "<td><span class='tb-state " + (engaged ? "on" : "off") + "'>" +
          (engaged ? "in contact" : "below surface") + "</span></td>" +
        "<td><input type='checkbox' data-tb='" + i + "' data-tbfield='enabled'" +
          (t.enabled === false ? "" : " checked") + " /></td></tr>";
    });
    $("tbTable").innerHTML = rows + "</table>" + "<div class='hint tb-legend' style='margin-top:6px'>" +
      "<b>include</b> — is this a tie bar at all? Untick and it is excluded from everything: no contact " +
      "area, no coupling, not in the network. For a region that is really open groove.<br>" +
      "<b>engages at wear</b> — the tread has to wear this far down before the bar touches the road. " +
      "A bar that reaches the surface as moulded has height = NSD and engages at 0." +
      "</div>";

    var inputs = $("tbTable").querySelectorAll("[data-tb]");
    for (var k = 0; k < inputs.length; k++) on(inputs[k], "change", onTiebarEdit);
  }

  // ---- tie-bar groups --------------------------------------------------
  // 150 bars is transcription, not design. A mould repeats the same bar, so the
  // list collapses to one row per distinct bar and one edit sets the family.
  // The individual list stays available underneath for overriding a single bar.
  function tiebarGroupMode() {
    var el = $("tbGroupMode");
    return el ? el.value : "shape_position";
  }

  function currentTiebarGroups() {
    return E.groupTiebars(tiebarList(), tiebarGroupMode());
  }

  function renderTiebarGroups() {
    var host = $("tbGroupTable");
    if (!host) return;
    var tb = tiebarList();
    if (!tb.length) { host.innerHTML = ""; return; }
    var groups = currentTiebarGroups();
    state.tiebarGroups = groups;
    var wear = readWear();

    if (tiebarGroupMode() === "none") {
      host.innerHTML = "<div class='hint'>Grouping off — set each bar in the list below.</div>";
      return;
    }

    var rows = "<table class='metrics'><tr><th>Group</th><th>bars</th><th>zone</th>" +
      "<th>shape (circ × lat)</th><th>area (mm²)</th><th>y range (mm)</th><th>NSD (mm)</th>" +
      "<th>height (mm)</th><th>engages at wear</th><th>state</th>" +
      "<th title='Is this region a tie bar at all? Untick to exclude it completely — no contact " +
      "area, no coupling stiffness, not in the network. Use it for a region the detector picked " +
      "up that is really open groove.'>include</th></tr>";
    groups.forEach(function (g, i) {
      // A group is "in contact" only if it is uniform; a mixed group says so
      // rather than picking one member's answer and presenting it as the truth.
      var eng = g.members.filter(function (k) { return E.tiebarEngaged(tb[k], wear); }).length;
      var at = g.mixed_height ? null : Math.max(0, g.nsd - g.height);
      var state1 = eng === 0 ? ["off", "below surface"]
        : eng === g.count ? ["on", "in contact"]
        : ["off", eng + " of " + g.count + " in contact"];
      rows += "<tr class='" + (g.n_enabled === 0 ? "off" : eng === g.count ? "engaged" : "") + "'>" +
        "<td><b>" + g.id + "</b></td>" +
        "<td class='num'>" + g.count + "</td>" +
        "<td>" + escapeHtml(g.zone) + "</td>" +
        "<td class='num'>" + g.span_mm.toFixed(1) + " × " + g.width_mm.toFixed(1) + "</td>" +
        "<td class='num'>" + g.area_mm2.toFixed(1) + "</td>" +
        "<td class='num'>" + g.y_range[0].toFixed(1) + " … " + g.y_range[1].toFixed(1) + "</td>" +
        "<td class='num'>" + (+g.nsd).toFixed(2) + "</td>" +
        "<td class='num'><input type='number' step='0.1' min='0.1' data-tg='" + i + "' data-tgfield='height'" +
          (g.mixed_height ? " placeholder='mixed' value=''" : " value='" + (+g.height).toFixed(2) + "'") + " /></td>" +
        "<td class='num'>" + (at == null ? "<i>mixed</i>" : at.toFixed(2) + " mm") + "</td>" +
        "<td><span class='tb-state " + state1[0] + "'>" + state1[1] + "</span></td>" +
        "<td><input type='checkbox' data-tg='" + i + "' data-tgfield='enabled'" +
          (g.n_enabled > 0 ? " checked" : "") +
          (g.n_enabled > 0 && g.n_enabled < g.count ? " data-mixed='1'" : "") + " /></td></tr>";
    });
    host.innerHTML = rows + "</table>" +
      "<div class='hint' style='margin-top:6px'>" + tb.length + " bar(s) in <b>" + groups.length +
      "</b> group(s). Editing a group row sets every bar in it. A bar changed on its own shows the " +
      "group as <i>mixed</i> until the group is set again.</div>" + "<div class='hint tb-legend' style='margin-top:6px'>" +
      "<b>include</b> — is this a tie bar at all? Untick and it is excluded from everything: no contact " +
      "area, no coupling, not in the network. For a region that is really open groove.<br>" +
      "<b>engages at wear</b> — the tread has to wear this far down before the bar touches the road. " +
      "A bar that reaches the surface as moulded has height = NSD and engages at 0." +
      "</div>";

    var inputs = host.querySelectorAll("[data-tg]");
    for (var k = 0; k < inputs.length; k++) on(inputs[k], "change", onTiebarGroupEdit);
  }

  function onTiebarGroupEdit(ev) {
    var el = ev.target;
    var gi = parseInt(el.getAttribute("data-tg"), 10);
    var field = el.getAttribute("data-tgfield");
    var g = (state.tiebarGroups || [])[gi];
    if (!g) return;
    var changes = {};
    if (field === "height") {
      var v = parseFloat(el.value);
      if (!isFinite(v) || v <= 0) {
        // Put the field back rather than rebuilding: the user may still be in
        // it, and a rebuild would take the element out from under them.
        el.value = g.mixed_height ? "" : (+g.height).toFixed(2);
        return;
      }
      changes.height = v;
    } else {
      changes[field] = !!el.checked;
    }
    E.applyToTiebarGroup(tiebarList(), g, changes);
    refreshTiebarGroupsIfIdle();
    refreshTiebarRows();
    drawEditor();
    markStale();
  }

  function refreshTiebarSummary() {
    var tb = tiebarList(), wear = readWear(), nOn = 0, nEngaged = 0;
    tb.forEach(function (t) {
      if (t.enabled !== false) nOn++;
      if (E.tiebarEngaged(t, wear)) nEngaged++;
    });
    var el = $("tbSummary");
    if (el) el.innerHTML = tb.length + " found · " + nOn + " enabled · <b>" + nEngaged +
      "</b> in contact at " + wear.toFixed(1) + " mm wear";
  }

  // Update a row's derived cells in place.
  //
  // Never rebuild the table for a value change. Two reasons, both real:
  // rebuilding from a change handler tears out the element that fired the
  // event, which the browser refuses partway through the blur it is already
  // running; and typing a new NSD then clicking straight onto a tie-bar height
  // field fires NSD's change at blur, so the table was rebuilt under the cursor
  // and the click landed on a node that no longer existed. A full rebuild
  // belongs only where the SET of bars changes -- an import.
  function refreshTiebarRow(i) {
    var t = tiebarList()[i];
    var table = $("tbTable");
    var row = table && table.querySelector("tr:nth-child(" + (i + 2) + ")");
    if (!t || !row || row.cells.length < 9) return;
    var wear = readWear(), engaged = E.tiebarEngaged(t, wear), at = E.tiebarEngagementWear(t);
    row.className = t.enabled === false ? "off" : engaged ? "engaged" : "";
    row.cells[5].textContent = (+t.nsd).toFixed(2);
    var hIn = row.cells[6].firstChild;
    // Leave the box alone while it has focus, or a redraw would fight the typing.
    if (hIn && hIn !== document.activeElement) hIn.value = (+t.height).toFixed(2);
    row.cells[7].textContent = at.toFixed(2) + " mm";
    var chip = row.cells[8].firstChild;
    chip.className = "tb-state " + (engaged ? "on" : "off");
    chip.textContent = engaged ? "in contact" : "below surface";
  }

  function refreshTiebarRows() {
    var n = tiebarList().length;
    for (var i = 0; i < n; i++) refreshTiebarRow(i);
    refreshTiebarSummary();
    drawTiebarPlan();
  }

  // A per-bar edit can make a group mixed, so the group table has to follow.
  //
  // In place, never by rebuilding. Typing a new NSD and then clicking straight
  // onto a group field fires NSD's change at blur, and a rebuild at that moment
  // destroys the node the click is landing on -- the same failure the
  // individual table had. Group membership only changes when the grouping mode
  // or the drawing changes, so a rebuild is only needed if the shape of the
  // table actually differs.
  function refreshTiebarGroupsIfIdle() {
    var host = $("tbGroupTable");
    if (!host || !host.querySelector("table")) { renderTiebarGroups(); return; }
    var groups = E.groupTiebars(tiebarList(), tiebarGroupMode());
    var prev = state.tiebarGroups || [];
    var sameShape = groups.length === prev.length && groups.every(function (g, i) {
      return g.count === prev[i].count && g.members[0] === prev[i].members[0];
    });
    if (!sameShape) { renderTiebarGroups(); return; }
    state.tiebarGroups = groups;
    var tb = tiebarList(), wear = readWear();
    groups.forEach(function (g, i) {
      var row = host.querySelector("tr:nth-child(" + (i + 2) + ")");
      if (!row || row.cells.length < 11) return;
      var eng = g.members.filter(function (k) { return E.tiebarEngaged(tb[k], wear); }).length;
      var at = g.mixed_height ? null : Math.max(0, g.nsd - g.height);
      row.className = g.n_enabled === 0 ? "off" : eng === g.count ? "engaged" : "";
      row.cells[6].textContent = (+g.nsd).toFixed(2);
      var hIn = row.cells[7].firstChild;
      if (hIn && hIn !== document.activeElement) {
        if (g.mixed_height) { hIn.value = ""; hIn.placeholder = "mixed"; }
        else { hIn.value = (+g.height).toFixed(2); hIn.placeholder = ""; }
      }
      row.cells[8].innerHTML = at == null ? "<i>mixed</i>" : at.toFixed(2) + " mm";
      var chip = row.cells[9].firstChild;
      chip.className = "tb-state " + (eng === g.count && eng > 0 ? "on" : "off");
      chip.textContent = eng === 0 ? "below surface"
        : eng === g.count ? "in contact" : eng + " of " + g.count + " in contact";
      var useBox = row.cells[10].firstChild;
      if (useBox && useBox !== document.activeElement) useBox.checked = g.n_enabled > 0;
    });
  }

  function onTiebarEdit(ev) {
    var el = ev.target, i = parseInt(el.getAttribute("data-tb"), 10);
    var field = el.getAttribute("data-tbfield");
    var t = tiebarList()[i];
    if (!t) return;
    if (field === "height") {
      var v = parseFloat(el.value);
      if (!isFinite(v) || v <= 0) { el.value = (+t.height).toFixed(2); return; }
      // A bar taller than the block it sits between is not a tie bar.
      t.height = Math.min(v, t.nsd);
      t.height_set_by_user = true;
      if (t.height !== v) el.value = t.height.toFixed(2);
    } else {
      t[field] = !!el.checked;
    }
    refreshTiebarRow(i);
    refreshTiebarSummary();
    refreshTiebarGroupsIfIdle();
    drawTiebarPlan();
    drawEditor();          // the patch preview shows the bars too
    markStale();
  }

  function applyTiebarHeights(frac) {
    tiebarList().forEach(function (t) {
      t.height = Math.min(t.nsd, frac * t.nsd);
      t.height_set_by_user = true;
    });
    refreshTiebarRows();
    refreshTiebarGroupsIfIdle();
    drawEditor();
  }

  function setAllTiebars(enabled) {
    tiebarList().forEach(function (t) { t.enabled = enabled; });
    var boxes = $("tbTable").querySelectorAll("input[data-tbfield='enabled']");
    for (var i = 0; i < boxes.length; i++) boxes[i].checked = enabled;
    refreshTiebarRows();
    refreshTiebarGroupsIfIdle();
    drawEditor();
  }

  // The rolled-out plan with the bars picked out, so a candidate can be checked
  // against the drawing rather than taken on trust.
  function drawTiebarPlan() {
    var p = state.pattern, th = plotTheme(), tb = tiebarList();
    var wear = readWear();
    var shapes = [];
    function poly(region, fill, line, width) {
      var d = regionPlotPath(region, p.tyre_circumference, false);
      shapes.push({ type: "path", path: d, fillcolor: fill, line: { color: line, width: width || 0.5 }, layer: "below" });
    }
    for (var i = 0; i < p.blocks.length; i++) poly(p.blocks[i], th.grid, th.inkDim, 0.4);
    for (var j = 0; j < tb.length; j++) {
      var t = tb[j];
      var on_ = t.enabled !== false;
      var eng = E.tiebarEngaged(t, wear);
      var tc = tiebarDisplayColor(t);
      poly(t, !on_ ? "rgba(128,128,128,0.25)" : eng ? hexA(tc, 0.85) : "rgba(0,0,0,0)",
           !on_ ? th.inkDim : tc, 1.2);
    }
    var labels = {
      x: tb.map(function (t) { return t.centroid_x; }),
      y: tb.map(function (t) { return t.centroid_y; }),
      text: tb.map(function (t) { return t.id; }),
      customdata: tb.map(function (t) {
        return [t.area.toFixed(1), (+t.height).toFixed(2), t.nsd.toFixed(2), E.tiebarEngagementWear(t).toFixed(2),
                t.source === "hatch" ? "drawn on the TIEBAR layer" : "found by area and adjacency",
                (t.holes || []).length];
      }),
      mode: "markers", type: "scatter", marker: { size: 5, color: th.ink || th.accent },
      hovertemplate: "%{text}<br>area %{customdata[0]} mm²<br>height %{customdata[1]} of %{customdata[2]} mm" +
                     "<br>engages at %{customdata[3]} mm wear" +
                     "<br>%{customdata[4]} · %{customdata[5]} hole(s)<extra></extra>",
      showlegend: false,
    };
    Plotly.react($("tbPlot"), [labels], {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 56, r: 12, t: 30, b: 42 }, height: 240, shapes: shapes,
      title: { text: "tie bars on the rolled-out plan — " +
                     (anyHatchedBars() ? "hatched bars keep their drawn colour; " : "") +
                     "filled = in contact at " + wear.toFixed(1) +
                     " mm wear, outline only = still below the surface, grey = excluded", font: { size: 11 } },
      xaxis: { title: { text: "circumferential position (mm)", font: { size: 11 } },
               range: [0, p.tyre_circumference], gridcolor: th.grid },
      yaxis: { title: { text: "lateral y (mm)", font: { size: 11 } },
               range: [-p.tread_width / 2, p.tread_width / 2], gridcolor: th.grid,
               scaleanchor: "x", scaleratio: 1 },
    }, { responsive: true, displayModeBar: false });
  }

  // ---- tie-bar coupling (the network solve) ----------------------------
  function card(k, v, u) {
    return "<div class='card'><div class='k'>" + escapeHtml(k) + "</div><div class='v'>" +
      escapeHtml(v) + "</div><div class='u'>" + escapeHtml(u) + "</div></div>";
  }

  function currentCoupling() {
    if (!state.coupling || !state.results) return null;
    for (var i = 0; i < state.results.length; i++)
      if (state.results[i].gamma_deg === state.gammaShown) return state.coupling[i] || null;
    return state.coupling[0] || null;
  }

  function renderCoupling() {
    var c = currentCoupling();
    var empty = $("cplEmpty"), body = $("cplBody");
    if (!empty || !body) return;
    empty.style.display = c ? "none" : "";
    body.style.display = c ? "" : "none";
    if (!c) {
      Plotly.purge($("cplPlot"));
      if ($("cplStrip")) Plotly.purge($("cplStrip"));
      $("cplCards").innerHTML = ""; $("cplTable").innerHTML = "";
      return;
    }

    var th = plotTheme();
    var mean = function (a) { var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return a.length ? s / a.length : 0; };
    var kxc = mean(c.kx_coupled), kxu = mean(c.kx_uncoupled);
    var kyc = mean(c.ky_coupled), kyu = mean(c.ky_uncoupled);
    var kxyc = mean(c.kxy_coupled), kxyu = mean(c.kxy_uncoupled);

    $("cplCards").innerHTML = [
      card("Kx gain", (c.gain_kx).toFixed(3) + "×", "circumferential"),
      card("Ky gain", (c.gain_ky).toFixed(3) + "×", "lateral"),
      card("Mean Kxy", kxyc.toFixed(1), "N/mm — cross term"),
      card("Bars in the network", String(c.n_engaged + c.n_submerged), c.n_submerged + " below the surface"),
      card("Bonded links", String(c.n_links), c.n_components + " independent group(s)"),
      card("Tread worn", (c.wear_mm || 0).toFixed(1), "mm"),
    ].join("");

    // Kxy is an order of magnitude smaller than Kx/Ky, so it gets its own axis.
    // On a shared one it is a flat line on the zero gridline and tells you
    // nothing -- which is the opposite of the point.
    var series = [
      { y: c.kx_uncoupled, name: "Kx uncoupled", color: th.accent, dash: "dot", axis: "y" },
      { y: c.kx_coupled, name: "Kx coupled", color: th.accent, dash: undefined, axis: "y" },
      { y: c.ky_uncoupled, name: "Ky uncoupled", color: th.bad, dash: "dot", axis: "y" },
      { y: c.ky_coupled, name: "Ky coupled", color: th.bad, dash: undefined, axis: "y" },
      { y: c.kxy_uncoupled, name: "Kxy uncoupled (right)", color: th.accent2, dash: "dot", axis: "y2" },
      { y: c.kxy_coupled, name: "Kxy coupled (right)", color: th.accent2, dash: undefined, axis: "y2" },
    ];
    var data = series.map(function (s) {
      return { x: c.theta_deg, y: s.y, type: "scatter", mode: "lines", name: s.name, yaxis: s.axis,
               line: { color: s.color, width: s.dash ? 1.2 : 2, dash: s.dash } };
    });
    var kxyAll = c.kxy_coupled.concat(c.kxy_uncoupled);
    var kxyMax = Math.max(1, Math.max.apply(null, kxyAll.map(Math.abs))) * 1.25;
    Plotly.react($("cplPlot"), data, {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 76, r: 76, t: 40, b: 88 }, height: 460,
      legend: { orientation: "h", y: -0.18, yanchor: "top", x: 0.5, xanchor: "center", font: { size: 10 } },
      title: { text: "Network stiffness vs θ — dotted = independent springs, solid = bonded network  (γ = " +
                     (currentResult() ? currentResult().gamma_deg : 0) + "°)", font: { size: 13 } },
      xaxis: { title: { text: "rotation angle θ (deg)", font: { size: 11 } },
               range: (state.cplRange || [0, 360]).slice(), gridcolor: th.grid,
               tickvals: [0, 45, 90, 135, 180, 225, 270, 315, 360] },
      yaxis: { title: { text: "Kx, Ky (N/mm)", font: { size: 11 } }, gridcolor: th.grid },
      yaxis2: { title: { text: "Kxy (N/mm)", font: { size: 11 }, standoff: 6 },
                overlaying: "y", side: "right", range: [-kxyMax, kxyMax],
                zeroline: true, zerolinecolor: th.grid, showgrid: false,
                tickfont: { color: th.accent2 }, titlefont: { color: th.accent2 } },
    }, { responsive: true, displayModeBar: false });

    renderCouplingStrip(c);

    var row = function (label, u, cc) {
      var g = u !== 0 ? cc / u : 1;
      return "<tr><td>" + label + "</td><td class='num'>" + u.toFixed(1) + "</td><td class='num'>" +
        cc.toFixed(1) + "</td><td class='num'>" + (cc - u).toFixed(1) + "</td><td class='num'>" +
        (u !== 0 ? (100 * (g - 1)).toFixed(2) + "%" : "—") + "</td></tr>";
    };
    $("cplTable").innerHTML =
      "<table class='metrics' style='max-width:640px'><tr><th>Mean over θ</th><th>independent</th>" +
      "<th>bonded network</th><th>difference</th><th>gain</th></tr>" +
      row("Kx (circumferential)", kxu, kxc) + row("Ky (lateral)", kyu, kyc) +
      row("Kxy (cross)", kxyu, kxyc) + "</table>" +
      "<div class='hint' style='margin-top:6px'>Both columns are the same measurement on the same tread; only the " +
      "bonded links differ. Contact area is identical in both — a sub-surface bar touches nothing.</div>";
  }

  // ---- design comparison ----------------------------------------------
  // Entries are whole runs: {label, settings, results}. A run added from this
  // session and a run loaded from an exported JSON file are the same shape, so
  // they compare on equal terms and survive across sessions.
  function compareLabel(settings) {
    var p = settings.project || {};
    return [p.tread, p.project, p.size].filter(Boolean).join(" · ") ||
           (settings.pattern && settings.pattern.name) || "run";
  }

  function addCurrentToComparison() {
    if (!state.results) return;
    state.compare.push({
      label: compareLabel(settingsSnapshot()) + "  (" + new Date().toLocaleTimeString() + ")",
      settings: settingsSnapshot(),
      results: state.results,
    });
    renderCompare();
  }

  function loadComparisonFiles(files) {
    var pending = files.length;
    for (var i = 0; i < files.length; i++) {
      (function (file) {
        var fr = new FileReader();
        fr.onload = function () {
          try {
            var j = JSON.parse(String(fr.result));
            if (j.format !== "tread_eval.sweep" || !j.results)
              throw new Error("not a tread_eval run export");
            state.compare.push({ label: compareLabel(j.settings || {}) + "  [" + file.name + "]",
                                 settings: j.settings || {}, results: j.results });
          } catch (err) {
            alert("Could not read " + file.name + ": " + err.message);
          }
          if (--pending === 0) renderCompare();
        };
        fr.readAsText(file);
      })(files[i]);
    }
  }

  function refreshCompareButtons() {
    var n = (state.compare || []).length;
    var badge = $("cmpCount");
    if (badge) badge.textContent = n ? "(" + n + " held)" : "";
    var side = $("addCompareSide");
    if (side) side.disabled = !state.results;
  }

  function renderCompare() {
    var list = state.compare || [];
    refreshCompareButtons();
    $("compareMetricRow").style.display = list.length ? "" : "none";
    if (!list.length) {
      $("compareList").innerHTML = "<div class='banner'><b>Nothing to compare yet.</b><br>" +
        "Run a design, press <b>+ Add this design to comparison</b> under the Run button, then load the next " +
        "DXF, run it and add that too. Designs stay in this browser session — no files needed. " +
        "(Loading previously exported JSON runs also works, for designs from another day or machine.)</div>";
      Plotly.purge($("comparePlot")); $("compareTable").innerHTML = "";
      return;
    }
    $("compareList").innerHTML = "<table class='metrics'><tr><th>#</th><th>Design</th><th>Tyre</th>" +
      "<th>NSD</th><th>Shore</th><th>leans</th><th></th></tr>" + list.map(function (e, i) {
        var s = e.settings || {}, p = s.project || {}, bd = s.block_defaults || {};
        return "<tr><td>" + (i + 1) + "</td><td>" + escapeHtml(e.label) + "</td><td>" +
          escapeHtml(p.tyre_type_label || p.tyre_type || "—") + "</td><td class='num'>" +
          (bd.height != null ? bd.height : "—") + "</td><td class='num'>" +
          ((s.compound_and_boundary || {}).shore_a != null ? s.compound_and_boundary.shore_a : "—") +
          "</td><td class='num'>" + e.results.length + "</td>" +
          "<td><button class='btn secondary cmp-del' data-i='" + i + "' style='padding:2px 8px'>remove</button></td></tr>";
      }).join("") + "</table>";
    [].forEach.call($("compareList").querySelectorAll(".cmp-del"), function (btn) {
      btn.addEventListener("click", function () {
        state.compare.splice(parseInt(btn.dataset.i, 10), 1);
        renderCompare(); refreshCompareButtons();
      });
    });

    var th = plotTheme(), metric = state.compareMetric || "kz";
    var palette = [th.accent, th.good, th.accent2, th.bad, th.inkDim, "#9b6bff", "#00b3a4", "#e2679a"];
    // Compare at the lowest lean every run shares, so the curves are commensurate.
    var common = list.map(function (e) { return e.results.map(function (r) { return r.gamma_deg; }); })
                     .reduce(function (a, b) { return a.filter(function (g) { return b.indexOf(g) >= 0; }); });
    var gamma = common.length ? Math.min.apply(null, common) : 0;
    // A run exported before a metric existed simply does not carry it. Drop it
    // from the chart with a note rather than plotting an undefined series.
    var missing = 0;
    var data = list.map(function (e, i) {
      var r = e.results.find(function (x) { return x.gamma_deg === gamma; }) || e.results[0];
      if (!r[metric]) { missing++; return null; }
      return { x: r.theta_deg, y: r[metric], type: "scatter", mode: "lines",
               name: e.label.slice(0, 42), line: { color: palette[i % palette.length], width: 1.4 } };
    }).filter(Boolean);
    Plotly.react($("comparePlot"), data, {
      paper_bgcolor: th.paper_bgcolor, plot_bgcolor: th.plot_bgcolor, font: th.font,
      margin: { l: 72, r: 16, t: 40, b: 92 }, height: 460,
      legend: { orientation: "h", y: -0.18, yanchor: "top", x: 0.5, xanchor: "center", font: { size: 10 } },
      title: { text: METRIC_LABEL[metric] + " compared at γ = " + gamma + "°" +
               (common.length ? "" : "  (runs share no lean angle — first of each shown)") +
               (missing ? "  — " + missing + " run(s) predate this metric and are not shown" : ""),
               font: { size: 13 } },
      xaxis: { title: { text: "rotation angle θ (deg)", font: { size: 11 } }, range: [0, 360], gridcolor: th.grid },
      yaxis: { title: { text: METRIC_LABEL[metric], font: { size: 11 } }, gridcolor: th.grid },
    }, { responsive: true, displayModeBar: false });

    var base = null;
    var rows = "<table class='metrics'><tr><th>Design</th><th>mean</th><th>CoV</th><th>min</th>" +
      "<th>max</th><th>vs first</th></tr>";
    list.forEach(function (e) {
      var r = e.results.find(function (x) { return x.gamma_deg === gamma; }) || e.results[0];
      if (!r[metric]) {
        rows += "<tr><td>" + escapeHtml(e.label) +
          "</td><td colspan='5' class='hint'>run predates this metric</td></tr>";
        return;
      }
      var st = E.fluctuationStats(r[metric]);
      if (base === null) base = st.mean;
      var d = base ? (100 * (st.mean - base) / base) : 0;
      rows += "<tr><td>" + escapeHtml(e.label) + "</td><td class='num'>" + st.mean.toFixed(1) +
        "</td><td class='num'>" + (st.cov * 100).toFixed(2) + "%</td><td class='num'>" +
        st.min.toFixed(1) + "</td><td class='num'>" + st.max.toFixed(1) + "</td><td class='num'>" +
        (d === 0 ? "—" : (d > 0 ? "+" : "") + d.toFixed(1) + "%") + "</td></tr>";
    });
    $("compareTable").innerHTML = rows + "</table>";
  }

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

  // The inputs exactly as they were when Run was pressed.
  //
  // Read live, this used to describe whatever was in the boxes at export time,
  // which is not necessarily what produced the numbers beside it: edit NSD
  // after a run and the file claimed 4.0 mm had produced results computed at
  // 8.5 mm. An exported file has to be able to stand on its own months later,
  // so the settings are frozen at dispatch and the outputs are merged in when
  // the worker replies.
  function projectMeta() {
    return {
      project: $("projName").value.trim(),
      tread: $("treadName").value.trim(),
      tyre_type: $("tyreType").value,
      tyre_type_label: (TYRE_PRESETS[$("tyreType").value] || {}).label || $("tyreType").value,
      size: $("tyreSize").value.trim(),
      designer: $("designer").value.trim(),
    };
  }

  function captureInputs() {
    return {
      project: projectMeta(),
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
      curvature_correction: $("curv").checked,
      crown: crownSummary(),
      wear_and_tiebars: {
        wear_mm: readWear(),
        default_height_fraction: readTiebarFrac(),
        weld_tolerance_mm: readWeldTol(),
        tiebar_area_fraction: readTiebarAreaFrac(),
        tiebars: tiebarList().map(function (t) {
          return { id: t.id, zone: t.zone, area_mm2: t.area, nsd_mm: t.nsd, height_mm: t.height,
                   engages_at_wear_mm: E.tiebarEngagementWear(t), enabled: t.enabled !== false,
                   centroid_x_mm: t.centroid_x, centroid_y_mm: t.centroid_y,
                   // How the bar was identified travels with it: a number read
                   // off a colour the designer drew is not the same evidence as
                   // one the area heuristic guessed at.
                   source: t.source || "automatic", layer: t.layer || null,
                   color: t.color || null, n_holes: (t.holes || []).length };
        }),
      },
      import_report: state.report,
    };
  }

  function settingsSnapshot() {
    var base = state.ranInputs || captureInputs();
    return {
      project: base.project,
      pattern: base.pattern,
      block_defaults: base.block_defaults,
      compound_and_boundary: base.compound_and_boundary,
      contact_patch: base.contact_patch,
      load: base.load,
      wear_and_tiebars: base.wear_and_tiebars,
      // The crown captureInputs() recorded for the run on screen. It was being
      // dropped here, so every export -- the CSV header, the JSON, the text
      // summary and the PDF cover -- has been saying "no crown resolved" while
      // the page showed the crown correctly. The crown decides the contact point
      // at every lean, so a run recorded without it cannot be reproduced.
      crown: base.crown || crownSummary(),
      compound_resolved: state.compound || null,
      tiebar_coupling: couplingSummary(),
      analysis: {
        lean_angles_deg: state.results.map(function (r) { return r.gamma_deg; }),
        grid: state.grid,
        curvature_correction: base.curvature_correction,
        max_supported_lean_deg: state.maxLean,
      },
      import_report: base.import_report,
      physics_notes: state.notes || [],
    };
  }

  function exportCSV() {
    if (!state.results) return;
    var s = settingsSnapshot();
    var lines = [];
    // A header block, commented, so the numbers are never orphaned from how
    // they were produced.
    lines.push("# Tread pattern evaluation - theta x gamma sweep");
    lines.push("# generated: " + new Date().toISOString());
    lines.push("# pattern: " + s.pattern.name + " (" + s.pattern.n_blocks + " blocks, " +
      s.pattern.circumference_mm.toFixed(2) + " x " + s.pattern.tread_width_mm.toFixed(2) + " mm)");
    lines.push("# NSD " + s.block_defaults.height + " mm, draft " + s.block_defaults.draft_angle +
      " deg, sipes " + s.block_defaults.n_lateral_sipes + ", sipe model " + s.compound_and_boundary.sipe_model);
    lines.push("# compound: " + compoundLine(s));
    lines.push("# wear: " + tiebarLine(s));
    lines.push("# tie-bar coupling: " + couplingLine(s));
    lines.push("# crown: " + crownLine(s));
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
      "c_kappa_N", "c_alpha_N_per_rad", "c_mz_Nmm_per_rad", "pneumatic_trail_mm",
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
          slipAt(r, "c_kappa", j), slipAt(r, "c_alpha", j),
          slipAt(r, "c_mz", j), slipAt(r, "pneumatic_trail", j),
        ].join(","));
      }
    }
    download(safeName() + "_sweep.csv", lines.join("\n"), "text/csv;charset=utf-8");
  }

  // A run loaded from an older exported JSON has no slip columns; emit a blank
  // rather than "undefined" or a zero that would read as a measurement.
  function slipAt(r, key, j) {
    return r[key] && r[key][j] != null ? r[key][j].toFixed(4) : "";
  }

  // ---- saving the work, not just the answer ----------------------------
  //
  // Everything above exports a RUN: the numbers and the settings that produced
  // them, for reading. A project file is the other thing -- the imported tread
  // itself, its tie bars with their colours and holes, and every box on the
  // page -- so the work can be put down and picked up. It is the only export
  // that can be loaded back in.
  //
  // The control list is read off the page rather than written out by hand,
  // because a hand-written list silently stops covering the controls added
  // after it. Anything with an id inside the setup panel is a setting.
  function projectControlIds() {
    var side = document.querySelector("aside.sidebar");
    if (!side) return [];
    var els = side.querySelectorAll("input[id], select[id], textarea[id]"), out = [];
    for (var i = 0; i < els.length; i++) {
      // A file input holds a file, not a value, and cannot be restored anyway.
      if (els[i].type === "file" || els[i].disabled && els[i].type === "button") continue;
      out.push(els[i].id);
    }
    return out;
  }

  function captureProjectControls() {
    var out = {};
    projectControlIds().forEach(function (id) {
      var el = $(id); if (!el) return;
      out[id] = el.type === "checkbox" ? !!el.checked : el.value;
    });
    return out;
  }

  function restoreProjectControls(values) {
    values = values || {};
    projectControlIds().forEach(function (id) {
      var el = $(id); if (!el || values[id] == null) return;
      if (el.type === "checkbox") el.checked = !!values[id]; else el.value = values[id];
    });
  }

  // The pattern as plain JSON. The crown is left out on purpose: it is a pair
  // of long derived arrays rebuilt from the crown controls, which are saved
  // above, so storing it would bloat the file and let it disagree with them.
  function projectPatternSnapshot() {
    var p = state.pattern;
    return JSON.parse(JSON.stringify({
      tyre_circumference: p.tyre_circumference, tread_width: p.tread_width,
      pitches: p.pitches || [], blocks: p.blocks || [], tiebars: p.tiebars || [],
      name: p.name, source: p.source, meta: p.meta || {},
    }));
  }

  function exportProject() {
    if (!state.pattern) return;
    reconcilePattern();
    var payload = {
      format: "tread_eval.project", format_version: 1, generated: new Date().toISOString(),
      controls: captureProjectControls(),
      pattern: projectPatternSnapshot(),
      report: state.report,
      measured: state.measured ? {
        raw: state.measured.raw, name: state.measured.name, units: state.measured.units,
        measured_at: state.measured.measured_at, lateral: state.measured.lateral,
      } : null,
      ran_inputs: state.ranInputs || null,
      runtime: state.results ? {
        results: state.results, stiffness: state.stiffness, grid: state.grid,
        notes: state.notes || [], max_lean: state.maxLean, band_edges: state.bandEdges,
        compound: state.compound || null, wear: state.wear || null, coupling: state.coupling || null,
      } : null,
    };
    download(safeName() + "_project.json", JSON.stringify(payload, null, 1), "application/json");
  }

  function loadProjectFile(file) {
    var fr = new FileReader();
    fr.onload = function () {
      try {
        var j = JSON.parse(String(fr.result));
        if (j.format !== "tread_eval.project" || !j.pattern)
          throw new Error("not a tread_eval project file");
        restoreProjectControls(j.controls);
        var p = JSON.parse(JSON.stringify(j.pattern));
        p.blocks = p.blocks || []; p.tiebars = p.tiebars || []; p.pitches = p.pitches || [];
        p.blocks.forEach(function (b) { b.holes = b.holes || []; });
        // Recompute rather than trust: area and centroid are derived from the
        // loops, and a file edited by hand must not be able to state one thing
        // and draw another.
        p.tiebars.forEach(function (t) {
          t.holes = t.holes || []; t.source = t.source || "automatic";
          var c = E.regionCentroid(t.polygon, t.holes);
          t.centroid_x = c[0]; t.centroid_y = c[1]; t.area = E.regionArea(t.polygon, t.holes);
        });
        state.pattern = p;
        state.report = j.report || { warnings: [], n_blocks: p.blocks.length,
          n_tiebars: p.tiebars.length, n_wrapped: 0, land_ratio: 0 };
        state.measured = j.measured ? Object.assign({ placed: j.measured.raw }, j.measured) : null;
        reconcilePattern();
        E.linkTiebars(p.blocks, p.tiebars);
        var rt = j.runtime || null;
        state.results = rt && rt.results ? rt.results : null;
        state.stiffness = rt ? rt.stiffness : null; state.grid = rt ? rt.grid : null;
        state.notes = rt ? rt.notes || [] : []; state.maxLean = rt ? rt.max_lean : null;
        state.bandEdges = rt ? rt.band_edges : null; state.compound = rt ? rt.compound : null;
        state.wear = rt ? rt.wear : null; state.coupling = rt ? rt.coupling : null;
        state.ranInputs = j.ran_inputs || null; state.editorTheta = 0;
        syncShapeFields(); syncCompoundFields(); syncCrownFields(); syncCrownArcInfo();
        renderMeasuredInfo(); renderPitchInfo();
        renderBanner(); renderTiebars(); drawEditor();
        $("emptyHint").style.display = "none";
        refreshValidation(); refreshExportButtons();
        if (state.results) { populateGammaSelect(); renderAll(); } else showResultsChrome(false);
      } catch (err) {
        alert("Could not load " + file.name + ": " + err.message);
      }
    };
    fr.readAsText(file);
  }

  // The tread as the tool understood it, for taking back into CAD: blocks as
  // closed polylines, every tie bar as a colour-filled HATCH on the TIEBAR
  // layer -- so a bar the tool inferred comes back as a bar that was drawn.
  function exportPatternDxf() {
    if (!state.pattern) return;
    reconcilePattern();
    download(safeName() + "_tread_with_tiebars.dxf", E.patternToDxf(state.pattern),
             "application/dxf;charset=utf-8");
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
    var out = ["TREAD PATTERN EVALUATION", "=".repeat(60), ""];
    out.push("Pattern : " + s.pattern.name);
    out.push("Geometry: " + s.pattern.circumference_mm.toFixed(1) + " x " +
      s.pattern.tread_width_mm.toFixed(1) + " mm, " + s.pattern.n_blocks + " blocks, " +
      s.pattern.n_pitches + " pitches");
    out.push("Blocks  : NSD " + s.block_defaults.height + " mm, draft " + s.block_defaults.draft_angle +
      " deg, " + s.block_defaults.n_lateral_sipes + " sipes");
    out.push("Compound: " + compoundLine(s));
    out.push("Wear    : " + tiebarLine(s));
    out.push("Coupling: " + couplingLine(s));
    out.push("Crown   : " + crownLine(s));
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
    if (state.results[0].c_alpha) {
      out.push("");
      out.push("SLIP RESPONSE (brush model, TREAD SHARE ONLY -- the carcass is a");
      out.push("second spring in series and usually the larger one, so compare");
      out.push("designs on these numbers rather than reading them as tyre data.)");
      out.push(["gamma", "Ca_N/rad", "Ca_CoV%", "Ck_N", "Ck_CoV%", "trail_mm", "Fy@1deg_N"]
        .map(function (h) { return h.padStart(12); }).join(""));
      state.results.forEach(function (r) {
        var ca = E.fluctuationStats(r.c_alpha), ckk = E.fluctuationStats(r.c_kappa);
        var tr = E.fluctuationStats(r.pneumatic_trail);
        out.push([
          r.gamma_deg + "°", ca.mean.toFixed(0), (ca.cov * 100).toFixed(2),
          ckk.mean.toFixed(0), (ckk.cov * 100).toFixed(2), tr.mean.toFixed(2),
          (ca.mean * Math.PI / 180).toFixed(0),
        ].map(function (v) { return String(v).padStart(12); }).join(""));
      });
    }
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

  // ---- what goes in a report -------------------------------------------
  //
  // One list, used by the PDF and by the interactive review pack, so the two can
  // never offer different contents. A design review wants four charts and a
  // headline, not thirteen sections -- and which four changes every meeting, so
  // it is the user's choice rather than a fixed running order.
  var REPORT_SECTIONS = [
    { key: "cover",    label: "Cover & settings" },
    { key: "summary",  label: "Headline numbers" },
    { key: "perlean",  label: "Per-lean table" },
    { key: "notes",    label: "Physics notes" },
    { key: "stack",    label: "θ sweep",             plot: "thetaStack" },
    { key: "pattern",  label: "Rolled-out pattern",  plot: "patternStrip", strip: true },
    { key: "lean",     label: "Lean map",            plot: "leanHeat" },
    { key: "orders",   label: "Order content",       plot: "orders" },
    { key: "zones",    label: "Zone area",           plot: "zones" },
    { key: "bands",    label: "Ribs",                plot: "bandsPlot" },
    { key: "coupling", label: "Tie-bar coupling",    plot: "cplPlot" },
    { key: "compare",  label: "Comparison",          plot: "comparePlot" },
    { key: "patch",    label: "Contact patch",       plot: "patchPrev" },
  ];

  function reportOn(key) { return state.reportSections[key] !== false; }

  // A section with a chart is only offered when that chart exists: the coupling
  // tab is empty without tie bars, the comparison without a second run.
  function sectionAvailable(sec) {
    if (!sec.plot) return true;
    var el = $(sec.plot);
    return !!(el && el.data && el.data.length && el.querySelector(".plot-container"));
  }

  function selectedSections() {
    return REPORT_SECTIONS.filter(function (sec) { return sectionAvailable(sec) && reportOn(sec.key); });
  }

  function renderReportChips() {
    var host = $("reportSections");
    if (!host) return;
    var html = "";
    for (var i = 0; i < REPORT_SECTIONS.length; i++) {
      var sec = REPORT_SECTIONS[i], avail = sectionAvailable(sec);
      html += "<span class='rowchip" + (avail && reportOn(sec.key) ? " on" : "") +
        (avail ? "" : " off") + "' data-sec='" + escapeHtml(sec.key) + "'" +
        (avail ? "" : " title='nothing to include — this chart has not been produced'") +
        ">" + escapeHtml(sec.label) + "</span>";
    }
    host.innerHTML = html;
    var n = selectedSections().length;
    var lbl = $("reportCount");
    if (lbl) lbl.textContent = n + " of " + REPORT_SECTIONS.filter(sectionAvailable).length + " included";
  }

  function toggleReportSection(key) {
    var sec = REPORT_SECTIONS.filter(function (s) { return s.key === key; })[0];
    if (!sec || !sectionAvailable(sec)) return;
    state.reportSections[key] = state.reportSections[key] === false;
    renderReportChips();
  }

  // The rolled-out tread is six to fourteen times longer than it is wide. At
  // page width that is a ribbon a centimetre tall with nothing readable in it,
  // which is what the design office complained about. Cut it into segments
  // stacked down the page, each at an aspect a block can actually be seen in.
  function stripSegmentCount() {
    if (!state.pattern) return 1;
    var aspect = state.pattern.tyre_circumference / state.pattern.tread_width;
    return Math.max(1, Math.min(8, Math.round(aspect / 3.0)));
  }

  // Render the strip over a given theta range on an OFFSCREEN figure. The
  // visible one is left alone: relayouting it mid-export would flicker, and an
  // export that throws would strand it at the wrong zoom.
  function captureStrip(range, wpx, hpx) {
    var host = document.createElement("div");
    host.style.cssText = "position:fixed;left:-10000px;top:0;width:" + wpx + "px;height:" + hpx + "px";
    document.body.appendChild(host);
    var th = plotTheme();
    var layout = stripLayout(th, "", range);
    layout.shapes = patternStripShapes({});
    layout.height = hpx;
    layout.margin = { l: 64, r: 12, t: 8, b: 34 };
    return Plotly.newPlot(host, STRIP_TRACE, layout, { staticPlot: true })
      .then(function () { return Plotly.toImage(host, { format: "png", width: wpx, height: hpx, scale: 2 }); })
      .then(function (uri) { Plotly.purge(host); host.remove(); return uri; })
      .catch(function (err) { try { Plotly.purge(host); host.remove(); } catch (e) {} throw err; });
  }

  // ---- PDF report ------------------------------------------------------
  // Charts go in as PNGs rendered by Plotly itself (Plotly.toImage works
  // offline), so the report shows exactly what is on screen rather than a
  // redrawn approximation. jsPDF is vendored, so no network is involved.
  var CONFIDENTIAL = "INTERNAL USE ONLY — Apollo Tyres. Not for external distribution.";

  // jsPDF's built-in fonts are WinAnsi, which has no Greek: theta and gamma came
  // out as a comma and a superscript three, on the cover of every report and on
  // every chart caption. Embedding a Unicode font would add a few hundred KB to
  // each file for four letters, so they are spelled out instead -- in the PDF's
  // own text only. The charts are images and keep the real symbols.
  var PDF_SUBST = [
    [/θ/g, "theta"], [/γ/g, "gamma"], [/α/g, "alpha"], [/κ/g, "kappa"],
    [/Δ/g, "delta"], [/ν/g, "nu"], [/μ/g, "u"], [/Ω/g, "ohm"],
    [/—/g, "-"], [/–/g, "-"], [/·/g, " - "], [/×/g, "x"], [/…/g, "..."],
    [/²/g, "^2"], [/³/g, "^3"], [/≥/g, ">="], [/≤/g, "<="], [/≈/g, "~"],
    [/[\u2018\u2019]/g, "'"], [/[\u201c\u201d]/g, '"'],
  ];
  function pdfText(v) {
    var out = String(v);
    for (var i = 0; i < PDF_SUBST.length; i++) out = out.replace(PDF_SUBST[i][0], PDF_SUBST[i][1]);
    return out;
  }

  // One phrasing of the compound and the wear state, shared by every export so
  // a CSV, a JSON and a PDF of the same run can never disagree about them.
  function compoundLine(s) {
    var c = s.compound_and_boundary || {}, r = s.compound_resolved;
    var base = c.modulus_mode === "direct"
      ? "E entered directly"
      : "Shore A " + c.shore_a + " (Gent table)";
    if (r) base += ": E " + r.E.toFixed(3) + " N/mm2, G " + r.G.toFixed(3) + " N/mm2, k " + r.k.toFixed(3);
    return base + ", Poisson " + c.poisson + ", " + c.mode + " boundary";
  }
  function tiebarLine(s) {
    var w = s.wear_and_tiebars || {};
    var bars = w.tiebars || [];
    var wear = +(w.wear_mm || 0);
    if (!bars.length) return "tread worn " + wear.toFixed(2) + " mm; no tie bars in this drawing";
    var engaged = bars.filter(function (t) {
      return t.enabled && wear >= t.engages_at_wear_mm - 1e-9;
    }).length;
    var hs = bars.map(function (t) { return t.height_mm; });
    var lo = Math.min.apply(null, hs), hi = Math.max.apply(null, hs);
    return "tread worn " + wear.toFixed(2) + " mm; " + bars.length + " tie bar(s) of height " +
      (lo === hi ? lo.toFixed(2) : lo.toFixed(2) + "-" + hi.toFixed(2)) + " mm, " +
      engaged + " in contact";
  }

  // What the network solve found, in one shape every export can use.
  function couplingSummary() {
    if (!state.coupling) return null;
    var out = [];
    for (var i = 0; i < state.coupling.length; i++) {
      var c = state.coupling[i];
      if (!c) continue;
      var mean = function (a) { var t = 0; for (var k = 0; k < a.length; k++) t += a[k]; return a.length ? t / a.length : 0; };
      out.push({
        gamma_deg: state.results[i].gamma_deg,
        n_nodes: c.n_nodes, n_links: c.n_links, n_components: c.n_components,
        n_bars_submerged: c.n_submerged, n_bars_engaged: c.n_engaged,
        gain_kx: c.gain_kx, gain_ky: c.gain_ky,
        mean_kx_uncoupled: mean(c.kx_uncoupled), mean_kx_coupled: mean(c.kx_coupled),
        mean_ky_uncoupled: mean(c.ky_uncoupled), mean_ky_coupled: mean(c.ky_coupled),
        mean_kxy_uncoupled: mean(c.kxy_uncoupled), mean_kxy_coupled: mean(c.kxy_coupled),
      });
    }
    return out.length ? out : null;
  }

  function couplingLine(s) {
    var c = s.tiebar_coupling;
    if (!c || !c.length) return "no tie-bar coupling (no bars bonded to two or more blocks)";
    var a = c[0];
    return a.n_links + " bonded link(s) over " + a.n_nodes + " elements in " + a.n_components +
      " group(s); at gamma " + a.gamma_deg + " deg the network stiffens Kx by " +
      ((a.gain_kx - 1) * 100).toFixed(2) + "% and Ky by " + ((a.gain_ky - 1) * 100).toFixed(2) +
      "%, mean Kxy " + a.mean_kxy_coupled.toFixed(2) + " N/mm. Contact area is unchanged.";
  }

  function exportPDF() {
    if (!state.results || !window.jspdf) return;
    var btn = $("exportPdf"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Building…";

    var doc = new window.jspdf.jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
    // Substitute once, at the boundary, rather than at seventy call sites.
    var rawText = doc.text.bind(doc), rawSplit = doc.splitTextToSize.bind(doc);
    doc.text = function (t, x, y, o) {
      return rawText(Array.isArray(t) ? t.map(pdfText) : pdfText(t), x, y, o);
    };
    doc.splitTextToSize = function (t, w, o) { return rawSplit(pdfText(t), w, o); };
    var W = 210, H = 297, M = 14, y = 0;
    var s0 = settingsSnapshot(), proj = s0.project || {};
    var created = new Date();

    function footer(pageNo) {
      doc.setFontSize(7); doc.setTextColor(120);
      doc.text(CONFIDENTIAL, M, H - 8);
      doc.text("page " + pageNo, W - M, H - 8, { align: "right" });
      doc.setTextColor(0);
    }
    function newPage() { doc.addPage(); footer(doc.getNumberOfPages()); return M + 6; }

    // ---- cover ----
    // The cover always carries the identification and the confidentiality mark;
    // only the settings block and the notes are optional. A report page with no
    // idea which tyre it describes is worse than a long one.
    doc.setFontSize(20); doc.text("Tread Pattern Evaluation", M, M + 10);
    doc.setFontSize(11); doc.setTextColor(90);
    doc.text("Contact and stiffness across rotation angle θ and lean angle γ", M, M + 18);
    doc.setTextColor(0);
    doc.setDrawColor(180); doc.line(M, M + 22, W - M, M + 22);

    y = M + 32;
    var rows = [
      ["Project", proj.project || "—"],
      ["Tread / design", proj.tread || s0.pattern.name || "—"],
      ["Tyre type", proj.tyre_type_label || "—"],
      ["Size", proj.size || "—"],
      ["Designer", proj.designer || "—"],
      ["Created", created.toLocaleString()],
      ["", ""],
      ["Geometry", s0.pattern.circumference_mm.toFixed(1) + " x " + s0.pattern.tread_width_mm.toFixed(1) +
        " mm, " + s0.pattern.n_blocks + " blocks, " + s0.pattern.n_pitches + " pitches"],
      ["Block depth", "NSD " + s0.block_defaults.height + " mm, draft " + s0.block_defaults.draft_angle +
        " deg, " + s0.block_defaults.n_lateral_sipes + " sipes"],
      ["Compound", compoundLine(s0)],
      ["Sipe model", s0.compound_and_boundary.sipe_model],
      ["Wear state", tiebarLine(s0)],
      ["Tie-bar coupling", couplingLine(s0)],
      ["Tread arc / crown", crownLine(s0)],
      ["Contact patch", E.describeSpec(s0.contact_patch)],
      ["Load", s0.load.vertical_load + " N" + (s0.load.load_rises_with_lean ? " (rises with lean)" : " (constant)")],
      ["Lean angles", s0.analysis.lean_angles_deg.join("°, ") + "°"],
      ["Grid", s0.analysis.grid.nx + " x " + s0.analysis.grid.ny],
    ];
    if (!reportOn("cover")) rows = rows.slice(0, 6);
    doc.setFontSize(9);
    rows.forEach(function (rw) {
      if (!rw[0] && !rw[1]) { y += 3; return; }
      doc.setTextColor(110); doc.text(String(rw[0]), M, y);
      doc.setTextColor(0);
      doc.text(doc.splitTextToSize(String(rw[1]), W - M - 52), M + 40, y);
      y += Math.max(5, 4.6 * doc.splitTextToSize(String(rw[1]), W - M - 52).length);
    });

    if (reportOn("notes") && (s0.physics_notes || []).length) {
      y += 4; doc.setFontSize(10); doc.text("Physics notes", M, y); y += 5;
      doc.setFontSize(8); doc.setTextColor(90);
      s0.physics_notes.forEach(function (n) {
        var lines = doc.splitTextToSize("• " + n.replace(/\s+/g, " "), W - 2 * M);
        doc.text(lines, M, y); y += lines.length * 3.8 + 1.5;
      });
      doc.setTextColor(0);
    }

    doc.setFillColor(245, 232, 232); doc.rect(M, H - 34, W - 2 * M, 12, "F");
    doc.setFontSize(9); doc.setTextColor(150, 30, 30);
    doc.text(CONFIDENTIAL, W / 2, H - 26, { align: "center" });
    doc.setTextColor(0);
    footer(1);

    // ---- headline numbers ----
    var r0 = currentResult();
    if (reportOn("summary") || reportOn("perlean")) {
    y = newPage();
    doc.setFontSize(13); doc.text("Summary — γ = " + r0.gamma_deg + "°", M, y); y += 8;
    doc.setFontSize(9);
    var cards = [
      ["Patch area", r0.patch_area.toFixed(0) + " mm²"],
      ["Mean contact area", E.fluctuationStats(r0.contact_area).mean.toFixed(0) + " mm²"],
      ["Mean Kz", E.fluctuationStats(r0.kz).mean.toFixed(0) + " N/mm"],
      ["Mean Kx", E.fluctuationStats(r0.kx).mean.toFixed(0) + " N/mm"],
      ["Mean Ky", E.fluctuationStats(r0.ky).mean.toFixed(0) + " N/mm"],
      ["Kz fluctuation", (E.fluctuationStats(r0.kz).cov * 100).toFixed(2) + " % CoV"],
      ["Blocks in patch", E.fluctuationStats(r0.block_count).mean.toFixed(2)],
    ];
    if (r0.c_alpha && r0.c_alpha.length) {
      var caR = E.fluctuationStats(r0.c_alpha);
      cards.push(["Mean Cα (tread only)", caR.mean.toFixed(0) + " N/rad  (" +
        (caR.mean * Math.PI / 180).toFixed(0) + " N per degree of slip)"]);
      cards.push(["Cα fluctuation", (caR.cov * 100).toFixed(2) + " % CoV"]);
      cards.push(["Mean Cκ", E.fluctuationStats(r0.c_kappa).mean.toFixed(0) + " N per unit slip ratio"]);
      cards.push(["Pneumatic trail", E.fluctuationStats(r0.pneumatic_trail).mean.toFixed(2) + " mm"]);
    }
    if (reportOn("summary")) {
      cards.forEach(function (c) {
        doc.setTextColor(110); doc.text(c[0], M, y);
        doc.setTextColor(0); doc.text(c[1], M + 55, y); y += 5.4;
      });
      y += 4;
    }
    if (reportOn("perlean")) {
      doc.setFontSize(11); doc.text("Per lean angle", M, y); y += 6;
      doc.setFontSize(8);
      var hdr = ["γ", "area mean", "area CoV", "Kz mean", "Kz CoV", "Kx mean", "Ky mean", "blocks"];
      var colX = [M, M + 16, M + 42, M + 62, M + 86, M + 106, M + 130, M + 154];
      doc.setTextColor(110);
      hdr.forEach(function (h, i) { doc.text(h, colX[i], y); });
      doc.setTextColor(0); y += 4;
      state.results.forEach(function (r) {
        var a = E.fluctuationStats(r.contact_area), z = E.fluctuationStats(r.kz);
        var vals = [r.gamma_deg + "°", a.mean.toFixed(0), (a.cov * 100).toFixed(2) + "%",
                    z.mean.toFixed(0), (z.cov * 100).toFixed(2) + "%",
                    E.fluctuationStats(r.kx).mean.toFixed(0), E.fluctuationStats(r.ky).mean.toFixed(0),
                    E.fluctuationStats(r.block_count).mean.toFixed(2)];
        vals.forEach(function (v, i) { doc.text(String(v), colX[i], y); });
        y += 4.2;
      });
    }
    }

    // ---- the charts, only the ones asked for ----
    var plots = selectedSections().filter(function (sec) { return sec.plot; });

    // Place an image inside the space left on the page WITHOUT distorting it.
    // The old code clamped the height against the page and left the width at
    // full bleed, so anything taller than a page -- the ten-row sweep, for one
    // -- was squashed vertically.
    function placeImage(uri, wpx, hpx, yy) {
      var maxW = W - 2 * M, maxH = H - yy - 20;
      var k = Math.min(maxW / wpx, maxH / hpx);
      var w = wpx * k, h = hpx * k;
      doc.addImage(uri, "PNG", M + (maxW - w) / 2, yy, w, h, undefined, "FAST");
      return yy + h;
    }

    var i = 0;
    function nextPlot() {
      if (i >= plots.length) { finish(); return; }
      var sec = plots[i], el = $(sec.plot);
      // The rolled-out tread gets its own treatment: cut into segments stacked
      // down the page so a block is big enough to look at.
      if (sec.strip && state.pattern) {
        var nSeg = stripSegmentCount();
        var yy = newPage();
        doc.setFontSize(12);
        doc.text("Rolled-out tread pattern" + (nSeg > 1 ? " — " + nSeg + " segments of " +
          (360 / nSeg).toFixed(0) + "° each" : ""), M, yy);
        yy += 5;
        var avail = (H - yy - 24) / nSeg;
        var boxW = W - 2 * M, boxH = avail - 6;
        // Render each segment at the aspect of the box it goes into, so it fills
        // the text column exactly and is neither distorted nor inset.
        var segW = 1200, segH = Math.max(140, Math.round((segW * boxH) / boxW));
        var seg = 0;
        (function nextSeg() {
          if (seg >= nSeg) { i++; nextPlot(); return; }
          var a = (360 * seg) / nSeg, b = (360 * (seg + 1)) / nSeg;
          captureStrip([a, b], segW, segH).then(function (uri) {
            doc.setFontSize(8); doc.setTextColor(110);
            doc.text("theta " + a.toFixed(0) + "° to " + b.toFixed(0) + "°", M, yy + 3);
            doc.setTextColor(0);
            doc.addImage(uri, "PNG", M, yy + 4, boxW, boxH, undefined, "FAST");
            yy += avail;
            seg++; nextSeg();
          }).catch(function () { seg++; nextSeg(); });
        })();
        return;
      }
      var wpx = Math.max(700, el.clientWidth || 900);
      var hpx = Math.max(320, el.clientHeight || 460);
      Plotly.toImage(el, { format: "png", width: wpx, height: hpx, scale: 2 })
        .then(function (uri) {
          var yy = newPage();
          doc.setFontSize(12); doc.text(sec.label, M, yy); yy += 5;
          placeImage(uri, wpx, hpx, yy);
          i++; nextPlot();
        })
        .catch(function () { i++; nextPlot(); });
    }

    function finish() {
      var name = [proj.project, proj.tread].filter(Boolean).join("_") || safeName();
      doc.save(name.replace(/[^A-Za-z0-9._-]+/g, "_") + "_report.pdf");
      btn.disabled = false; btn.textContent = old;
    }
    nextPlot();
  }

  // ---- interactive review pack ----------------------------------------
  //
  // A PDF is a picture of a chart. In a design review the question is always
  // "what is it at 140 degrees?", and a picture cannot answer it.
  //
  // So write out a single HTML file carrying only the ticked sections, with the
  // figures STILL LIVE -- hover, zoom, read values off them. It has no inputs,
  // no compute engine, no DXF and no way back to them: it is a read-only view of
  // one run, which is exactly what leaves the department. Plotly is lifted out of
  // this page and inlined into it, so the file opens by double-click with
  // nothing installed and no network.
  function escapeForScript(json) {
    // A closing script tag inside a JSON string would end the host element
    // early, so "<" is escaped. The same hazard is why this comment does not
    // spell the tag out: this file is inlined into a script element itself.
    return json.replace(/</g, "\\u003c").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
  }

  function plotlySource() {
    var el = document.getElementById("plotly-src");
    return el ? el.textContent : "";
  }

  // The figure as Plotly holds it, trimmed to what a viewer needs to redraw it.
  function figureSpec(sec) {
    var gd = $(sec.plot);
    if (!gd || !gd.data || !gd.data.length) return null;
    var layout = JSON.parse(JSON.stringify(gd.layout || {}));
    // The strip is drawn almost entirely as shapes, and the shapes live on the
    // layout, so they travel with it. What does NOT travel is the patch band --
    // it is an SVG overlay this page draws for dragging, not a Plotly object --
    // so it is added here as a shape, since where the patch sits is the first
    // thing anyone asks in a review.
    if (sec.strip && state.patchTheta != null) {
      var span = patchThetaSpanDeg();
      if (span) {
        layout.shapes = (layout.shapes || []).concat([{
          type: "rect", xref: "x", yref: "paper",
          x0: clampPatchTheta(state.patchTheta) + span[0],
          x1: clampPatchTheta(state.patchTheta) + span[1],
          y0: 0, y1: 1,
          fillcolor: "rgba(90,150,255,0.13)",
          line: { color: "rgba(90,150,255,0.55)", width: 1, dash: "dash" },
        }]);
      }
      layout.height = 320;
    }
    layout.autosize = true;
    delete layout.width;
    return { title: sec.label, data: JSON.parse(JSON.stringify(gd.data)), layout: layout };
  }

  function exportPack() {
    if (!state.results) return;
    var btn = $("exportPack"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Building…";
    try {
      var s0 = settingsSnapshot(), proj = s0.project || {}, r0 = currentResult();
      var secs = selectedSections();
      var figs = [];
      for (var i = 0; i < secs.length; i++) {
        if (!secs[i].plot) continue;
        var f = figureSpec(secs[i]);
        if (f) figs.push(f);
      }

      var rows = [
        ["Project", proj.project || "—"],
        ["Tread / design", proj.tread || s0.pattern.name || "—"],
        ["Tyre type", proj.tyre_type_label || "—"],
        ["Size", proj.size || "—"],
        ["Designer", proj.designer || "—"],
        ["Created", new Date().toLocaleString()],
      ];
      if (reportOn("cover")) {
        rows = rows.concat([
          ["Geometry", s0.pattern.circumference_mm.toFixed(1) + " × " + s0.pattern.tread_width_mm.toFixed(1) +
            " mm, " + s0.pattern.n_blocks + " blocks, " + s0.pattern.n_pitches + " pitches"],
          ["Block depth", "NSD " + s0.block_defaults.height + " mm, draft " + s0.block_defaults.draft_angle +
            "°, " + s0.block_defaults.n_lateral_sipes + " sipes"],
          ["Compound", compoundLine(s0)],
          ["Wear state", tiebarLine(s0)],
          ["Tie-bar coupling", couplingLine(s0)],
          ["Tread arc / crown", crownLine(s0)],
          ["Contact patch", E.describeSpec(s0.contact_patch)],
          ["Load", s0.load.vertical_load + " N" + (s0.load.load_rises_with_lean ? " (rises with lean)" : " (constant)")],
          ["Lean angles", s0.analysis.lean_angles_deg.join("°, ") + "°"],
        ]);
      }

      var cards = [];
      if (reportOn("summary")) {
        cards = [
          ["Patch area", r0.patch_area.toFixed(0), "mm²"],
          ["Mean contact area", E.fluctuationStats(r0.contact_area).mean.toFixed(0), "mm²"],
          ["Mean Kz", E.fluctuationStats(r0.kz).mean.toFixed(0), "N/mm"],
          ["Kz fluctuation", (E.fluctuationStats(r0.kz).cov * 100).toFixed(2), "% CoV over θ"],
          ["Blocks in patch", E.fluctuationStats(r0.block_count).mean.toFixed(2), "avg"],
        ];
        if (r0.c_alpha && r0.c_alpha.length) {
          var ca = E.fluctuationStats(r0.c_alpha);
          cards.push(["Mean Cα (tread only)", ca.mean.toFixed(0), "N/rad"]);
          cards.push(["Cα fluctuation", (ca.cov * 100).toFixed(2), "% CoV over θ"]);
          cards.push(["Pneumatic trail", E.fluctuationStats(r0.pneumatic_trail).mean.toFixed(2), "mm"]);
        }
      }

      var perLean = null;
      if (reportOn("perlean")) {
        perLean = state.results.map(function (r) {
          var a = E.fluctuationStats(r.contact_area), z = E.fluctuationStats(r.kz);
          return [r.gamma_deg + "°", a.mean.toFixed(0), (a.cov * 100).toFixed(2) + "%",
                  z.mean.toFixed(0), (z.cov * 100).toFixed(2) + "%",
                  E.fluctuationStats(r.kx).mean.toFixed(0),
                  E.fluctuationStats(r.ky).mean.toFixed(0),
                  E.fluctuationStats(r.block_count).mean.toFixed(2)];
        });
      }

      var payload = {
        title: [proj.project, proj.tread].filter(Boolean).join(" · ") || s0.pattern.name || "Tread evaluation",
        gamma: r0.gamma_deg,
        rows: rows, cards: cards,
        per_lean: perLean,
        notes: reportOn("notes") ? (s0.physics_notes || []) : [],
        figures: figs,
        confidential: CONFIDENTIAL,
      };

      var html = reviewPackHtml(payload, plotlySource());
      var name = [proj.project, proj.tread].filter(Boolean).join("_") || safeName();
      download(name.replace(/[^A-Za-z0-9._-]+/g, "_") + "_review.html", html, "text/html;charset=utf-8");
    } catch (err) {
      alert("Could not build the review pack: " + err.message);
    }
    btn.disabled = false; btn.textContent = old;
  }

  function reviewPackHtml(payload, plotlyJs) {
    var head = "<!doctype html><html lang='en'><head><meta charset='utf-8'>" +
      "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
      "<title>" + escapeHtml(payload.title) + " — review pack</title><style>" +
      "body{margin:0;background:#f4f6f9;color:#16202b;font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
      ".wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}" +
      "h1{font-size:22px;margin:0 0 2px}.sub{color:#5b6b7d;margin:0 0 18px}" +
      "table.meta{border-collapse:collapse;margin:0 0 18px;width:100%}" +
      "table.meta td{padding:4px 10px 4px 0;vertical-align:top;border-bottom:1px solid #e3e9ef}" +
      "table.meta td.k{color:#5b6b7d;width:150px}" +
      ".cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:0 0 20px}" +
      ".card{background:#fff;border:1px solid #e3e9ef;border-radius:8px;padding:10px 12px}" +
      ".card .k{font-size:11px;color:#5b6b7d;text-transform:uppercase;letter-spacing:.5px}" +
      ".card .v{font-size:20px;font-weight:650}.card .u{font-size:12px;color:#5b6b7d}" +
      "table.num{border-collapse:collapse;font-size:13px;margin:0 0 20px}" +
      "table.num th,table.num td{border-bottom:1px solid #e3e9ef;padding:5px 12px;text-align:right}" +
      "table.num th:first-child,table.num td:first-child{text-align:left}" +
      "table.num th{color:#5b6b7d;font-weight:600}" +
      ".fig{background:#fff;border:1px solid #e3e9ef;border-radius:10px;padding:12px;margin:0 0 16px}" +
      ".fig h2{font-size:15px;margin:0 0 8px}" +
      ".notes{background:#fff8e6;border:1px solid #f0dca8;border-radius:8px;padding:10px 14px;margin:0 0 20px}" +
      ".notes li{margin:3px 0}" +
      ".conf{background:#f7e6e6;color:#96201e;border-radius:6px;padding:9px 14px;margin:22px 0 0;text-align:center;font-size:13px}" +
      ".hint{color:#5b6b7d;font-size:12.5px}" +
      "</style></head><body><div class='wrap'>";

    var body = "<h1>" + escapeHtml(payload.title) + "</h1>" +
      "<p class='sub'>Tread pattern evaluation — contact and stiffness across rotation angle θ" +
      " · shown at lean γ = " + payload.gamma + "°</p>";

    body += "<table class='meta'>";
    payload.rows.forEach(function (r) {
      body += "<tr><td class='k'>" + escapeHtml(r[0]) + "</td><td>" + escapeHtml(String(r[1])) + "</td></tr>";
    });
    body += "</table>";

    if (payload.cards.length) {
      body += "<div class='cards'>";
      payload.cards.forEach(function (c) {
        body += "<div class='card'><div class='k'>" + escapeHtml(c[0]) + "</div><div class='v'>" +
          escapeHtml(c[1]) + "</div><div class='u'>" + escapeHtml(c[2]) + "</div></div>";
      });
      body += "</div>";
    }

    if (payload.per_lean) {
      body += "<table class='num'><tr><th>γ</th><th>area mean</th><th>area CoV</th><th>Kz mean</th>" +
        "<th>Kz CoV</th><th>Kx mean</th><th>Ky mean</th><th>blocks</th></tr>";
      payload.per_lean.forEach(function (row) {
        body += "<tr>" + row.map(function (v) { return "<td>" + escapeHtml(v) + "</td>"; }).join("") + "</tr>";
      });
      body += "</table>";
    }

    if (payload.notes.length) {
      body += "<div class='notes'><b>Physics notes</b><ul>";
      payload.notes.forEach(function (n) { body += "<li>" + escapeHtml(n.replace(/\s+/g, " ")) + "</li>"; });
      body += "</ul></div>";
    }

    for (var i = 0; i < payload.figures.length; i++)
      body += "<div class='fig'><h2>" + escapeHtml(payload.figures[i].title) +
        "</h2><div id='fig" + i + "'></div></div>";

    body += "<p class='hint'>The charts above are live: hover for values, drag to zoom, double-click to reset. " +
      "This file contains one run and nothing else — no inputs, no geometry and no way to recompute.</p>";
    body += "<div class='conf'>" + escapeHtml(payload.confidential) + "</div>";

    var boot = "<script>" + plotlyJs + "<\/script><script>var FIGS=" +
      escapeForScript(JSON.stringify(payload.figures)) + ";" +
      "for(var i=0;i<FIGS.length;i++){Plotly.newPlot('fig'+i,FIGS[i].data,FIGS[i].layout," +
      "{responsive:true,displayModeBar:false});}<\/script>";

    return head + body + "</div>" + boot + "</body></html>";
  }

  function refreshExportButtons() {
    var on = !!state.results;
    ["exportCsv", "exportJson", "exportTxt", "exportPdf", "exportPack"].forEach(function (id) {
      var el = $(id); if (el) el.disabled = !on;
    });
    // The project file and the DXF describe the TREAD, not the run, so they are
    // available the moment a pattern is loaded -- before anything is computed,
    // and still there if the run failed.
    var hasPattern = !!state.pattern;
    ["saveProject", "exportDxf"].forEach(function (id) {
      var el = $(id); if (el) el.disabled = !hasPattern;
    });
    renderReportChips();
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
          if (state.results || btn.dataset.tab === "tiebars") window.dispatchEvent(new Event("resize"));
        });
      })(btns[i]);
    }
  }

  // ---- wire up ---------------------------------------------------------
  function init() {
    initTheme();
    initTabs();
    editorSetup();
    if ($("pinStrip")) state.pinStrip = $("pinStrip").checked;
    applyStripPin();
    syncPitchFields();
    syncCrownFields();

    on($("fileInput"), "change", function (e) { if (e.target.files[0]) loadFile(e.target.files[0]); });
    on($("sampleBtn"), "click", function () {
      var t = $("sample-dxf").textContent;
      buildPatternFromText(t, "130/80R17 Tramplr XR (sample)");
    });
    on($("runBtn"), "click", run);
    on($("exportCsv"), "click", exportCSV);
    on($("exportJson"), "click", exportJSON);
    on($("exportTxt"), "click", exportSummary);
    on($("exportPdf"), "click", exportPDF);
    on($("exportPack"), "click", exportPack);
    on($("saveProject"), "click", exportProject);
    on($("exportDxf"), "click", exportPatternDxf);
    on($("loadProject"), "change", function (e) {
      if (e.target.files[0]) loadProjectFile(e.target.files[0]);
      e.target.value = "";
    });
    var secChips = $("reportSections");
    if (secChips) on(secChips, "click", function (ev) {
      var chip = ev.target.closest ? ev.target.closest(".rowchip") : null;
      if (chip && chip.dataset.sec) toggleReportSection(chip.dataset.sec);
    });
    on($("applyPreset"), "click", function () {
      applyTyrePreset(); updateLeanLoadReference(); syncCrownArcInfo(); drawEditor();
    });
    // The class sets the crown the blank fields fall back to, so changing it
    // changes the crown -- and the readout has to say so straight away.
    on($("tyreType"), "change", function () {
      syncCrownArcInfo(); refreshValidation(); drawEditor(); markStale();
    });
    on($("modulusMode"), "change", function () { syncCompoundFields(); refreshValidation(); markStale(); });
    ["eModulus", "gentK"].forEach(function (id) {
      on($(id), "input", function () { this.dataset.touched = "1"; syncCompoundFields(); refreshValidation(); markStale(); });
    });
    on($("tbApplyAll"), "click", function () {
      var f = parseFloat($("tbAllFrac").value);
      if (isFinite(f) && f > 0 && f <= 1) { applyTiebarHeights(f); markStale(); }
    });
    on($("tbGroupMode"), "change", function () { renderTiebarGroups(); });
    on($("tbEnableAll"), "click", function () { setAllTiebars(true); markStale(); });
    on($("tbDisableAll"), "click", function () { setAllTiebars(false); markStale(); });
    on($("bandMetric"), "change", function () { state.bandMetric = this.value; renderBands(); });

    // Row chips: delegated, because the chips are rebuilt on every render.
    var chips = $("rowToggles");
    if (chips) on(chips, "click", function (ev) {
      var chip = ev.target.closest ? ev.target.closest(".rowchip") : null;
      if (chip && chip.dataset.row) toggleStackRow(chip.dataset.row);
    });
    on($("pinStrip"), "change", function () { state.pinStrip = this.checked; applyStripPin(); });

    // Pitch replication. Changing any of these changes the tread itself, so the
    // DXF has to be read again -- unlike NSD or the compound, which only change
    // what is computed from an already-imported drawing.
    on($("crownMode"), "change", function () {
      syncCrownFields(); syncCrownArcInfo(); refreshValidation(); drawEditor(); markStale();
    });
    ["crownDrops"].forEach(function (id) {
      on($(id), "input", function () { syncCrownFields(); syncCrownArcInfo(); refreshValidation(); drawEditor(); markStale(); });
      on($(id), "change", function () { syncCrownFields(); syncCrownArcInfo(); refreshValidation(); drawEditor(); markStale(); });
    });
    on($("pitchOn"), "change", function () { syncPitchFields(); markPitchStale(); });
    on($("pitchMode"), "change", function () { syncPitchFields(); markPitchStale(); });
    ["pitchBase", "pitchCount", "pitchLens", "pitchSeq", "pitchScale", "pitchSnap"].forEach(function (id) {
      on($(id), "input", function () { renderPitchInfo(); markPitchStale(); });
      on($(id), "change", function () { renderPitchInfo(); markPitchStale(); });
    });
    on($("compareMetric"), "change", function () { state.compareMetric = this.value; renderCompare(); });
    on($("addCompare"), "click", addCurrentToComparison);
    on($("addCompareSide"), "click", function () {
      addCurrentToComparison();
      document.querySelector('.tabs button[data-tab="compare"]').click();
    });
    on($("clearCompare"), "click", function () { state.compare = []; renderCompare(); });
    on($("loadCompare"), "change", function (e) { if (e.target.files.length) loadComparisonFiles(e.target.files); e.target.value = ""; });
    ["nBands", "bandEdges"].forEach(function (id) {
      on($(id), "input", function () { markStale(); });
    });
    ["projName", "treadName", "tyreSize", "designer"].forEach(function (id) {
      on($(id), "input", function () { /* metadata only -- no recompute needed */ });
    });
    renderCompare();
    on($("shape"), "change", function () { syncShapeFields(); refreshValidation(); drawEditor(); markStale(); });
    on($("cpFile"), "change", function (e) { if (e.target.files[0]) loadFootprint(e.target.files[0]); });
    on($("cpClear"), "click", function () {
      state.measured = null;
      if ($("cpFile")) $("cpFile").value = "";
      renderMeasuredInfo(); refreshValidation(); drawEditor(); markStale();
    });
    ["cpUnits", "cpLateral", "cpMeasuredAt"].forEach(function (id) {
      on($(id), "change", function () {
        // Units change what the file MEANS, so it has to be re-read.
        if (id === "cpUnits" && state.measured) {
          var was = state.measured.units, now = $("cpUnits").value;
          var k = (E.CP_UNIT_SCALE[now] || 1) / (E.CP_UNIT_SCALE[was] || 1);
          state.measured.raw = state.measured.raw.map(function (p) { return [p[0] * k, p[1] * k]; });
          state.measured.units = now;
        }
        if (id === "cpMeasuredAt" && state.measured) state.measured.measured_at = num("cpMeasuredAt") || 0;
        renderMeasuredInfo(); refreshValidation(); drawEditor(); markStale();
      });
    });

    ["cpLength", "cpWidth", "cpCorner", "cpExp", "cpTaper", "cpRot", "cpY", "cpLoad"].forEach(function (id) {
      on($(id), "input", function () { refreshValidation(); drawEditor(); markStale(); });
    });
    on($("cpAutoY"), "change", function () { $("cpY").disabled = $("cpAutoY").checked; drawEditor(); markStale(); });
    on($("cpScaleLean"), "change", function () { drawEditor(); markStale(); });
    on($("cpAutoLoad"), "change", function () { drawEditor(); markStale(); });
    ["nsd", "draft", "sipes", "sipeDepth", "shore", "poisson", "mode", "sipeModel", "quality", "curv",
     "wheelR", "crownCenter", "crownShoulder", "crownArcs", "nPitches", "wear",
     "tiebarHeight", "weldTol", "tiebarFrac"].forEach(function (id) {
      on($(id), "input", function () { refreshValidation(); syncCompoundFields(); syncCrownFields(); syncCrownArcInfo(); drawEditor(); reconcileAndRedrawTiebars(); markStale(); });
      on($(id), "change", function () { refreshValidation(); syncCompoundFields(); syncCrownFields(); syncCrownArcInfo(); drawEditor(); reconcileAndRedrawTiebars(); markStale(); });
    });
    on($("cpLoad"), "input", updateLeanLoadReference);

    on($("gammaSel"), "change", function () { state.gammaShown = state.results[parseInt(this.value, 10)].gamma_deg; renderAll(); });
    on($("patchTheta"), "input", function () { setPatchTheta(parseFloat(this.value)); });
    on($("heatMetric"), "change", function () { state.heatMetric = this.value; renderLeanHeatmap(); });
    on($("orderMetric"), "change", function () { state.orderMetric = this.value; renderOrders(); });

    syncShapeFields();
    syncCompoundFields();
    syncCrownArcInfo();
    updateLeanLoadReference();
    renderTiebars();
    // Paint once at startup: without this the canvas stayed blank until some
    // input happened to change, so the "load a DXF" hint inside it never showed.
    drawEditor();
    refreshValidation();
    $("cpY").disabled = $("cpAutoY").checked;
  }

  // NSD, the tie-bar fraction and the wear state all move the bars, so the
  // editor has to follow the inputs live. In place -- see refreshTiebarRow.
  function reconcileAndRedrawTiebars() {
    if (!state.pattern || !tiebarList().length) return;
    var d = readDefaults(), frac = readTiebarFrac();
    if (!isFinite(d.height) || d.height <= 0) return;
    tiebarList().forEach(function (t) {
      t.nsd = d.height;
      t.height = t.height_set_by_user ? Math.min(t.height, d.height) : Math.min(d.height, frac * d.height);
    });
    refreshTiebarRows();
    refreshTiebarGroupsIfIdle();
  }

  // ---- lean-load reference (display only) -------------------------------
  function updateLeanLoadReference() {
    var baseEl = $("cpLoad"), body = $("leanLoadRows"), label = $("leanLoadBase");
    if (!baseEl || !body || !label) return;
    var fz = parseFloat(baseEl.value);
    if (!isFinite(fz)) fz = 0;
    label.textContent = Math.round(fz).toString();
    var angles = [0, 20, 30, 40, 45], rows = [];
    for (var i = 0; i < angles.length; i++) {
      var g = angles[i], c = Math.cos((g * Math.PI) / 180);
      var resultant = c ? fz / c : NaN;
      rows.push("<tr><td>" + g + "°</td><td>" + c.toFixed(3) + "</td><td>" +
                (isFinite(resultant) ? Math.round(resultant).toLocaleString() : "—") + "</td></tr>");
    }
    body.innerHTML = rows.join("");
  }

  // A read-only window on the last run, for the browser smoke test and for
  // anyone debugging from the console. Returns numbers only -- nothing here can
  // change what the page computed.
  window.__ttState = function () {
    if (!state.results) return null;
    var r = currentResult();
    var a = E.fluctuationStats(r.contact_area), z = E.fluctuationStats(r.kz);
    return {
      area: a.mean, cov: a.cov, kz: z.mean, kzCov: z.cov,
      gamma: r.gamma_deg, nLeans: state.results.length,
      blocks: state.pattern.blocks.length,
      tiebars: tiebarList().length,
      engaged: state.wear ? state.wear.n_tiebars_engaged : 0,
      wear: state.wear ? state.wear.mm : 0,
      E: state.compound ? state.compound.E : null,
      G: state.compound ? state.compound.G : null,
      k: state.compound ? state.compound.k : null,
      patchTheta: state.patchTheta == null ? null : clampPatchTheta(state.patchTheta),
      crown: crownSummary(),
      hasCrown: !!(state.pattern && state.pattern.crown),
    };
  };
  // The whole result for the lean on screen, so a test can check a curve rather
  // than a summary of it. Same object the charts read; nothing is recomputed.
  window.__ttResult = function () { return state.results ? currentResult() : null; };
  // The imported tread itself -- geometry, tie bars, colours, holes -- so a test
  // can check what came out of a drawing rather than what the charts made of it.
  window.__ttPattern = function () { return state.pattern; };
  window.__ttSetPatchTheta = setPatchTheta;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
