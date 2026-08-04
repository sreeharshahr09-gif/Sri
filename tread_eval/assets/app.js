/* 2W tread pattern evaluation -- browser side.
   All numbers arrive precomputed in the JSON payload; this file only draws.  */
(function () {
"use strict";

const D = JSON.parse(document.getElementById("payload").textContent);
const META = D.meta;
const C = META.circumference;
const HALF_W = META.tread_width / 2;

/* Chart colours come from the stylesheet, so light and dark themes are
   defined in exactly one place and the plots cannot drift from the chrome. */
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
let ZONE_COLOR = {};
let LEAN_COLORS = [];
let THEME = {};
function readTheme() {
  ZONE_COLOR = {
    center: cssVar("--center", "#7cc4ff"),
    intermediate: cssVar("--intermediate", "#ffd166"),
    shoulder: cssVar("--shoulder", "#ff8fa3")
  };
  THEME = {
    text: cssVar("--text", "#e3eaf2"),
    muted: cssVar("--muted", "#8fa0b3"),
    grid: cssVar("--grid", "#1f2937"),
    axis: cssVar("--axis", "#2b3947"),
    accent: cssVar("--accent", "#4dd0e1"),
    flag: cssVar("--flag", "#ff6b6b"),
    ok: cssVar("--ok", "#5ed6a0"),
    watch: cssVar("--watch", "#ffc857"),
    patchLine: cssVar("--patch-line", "#ffffff"),
    panel: cssVar("--panel", "#141b24")
  };
  const dark = isDark();
  LEAN_COLORS = dark
    ? ["#5ec9e8", "#6ba9f0", "#a186e8", "#d96fc0", "#f2726f", "#ff9f45", "#ffd166"]
    : ["#0d7f8f", "#2f6fb5", "#6a4fb8", "#a8327f", "#c62828", "#b35c00", "#8a6d0b"];
}
function isDark() {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit) return explicit === "dark";
  return !window.matchMedia("(prefers-color-scheme: light)").matches;
}
const leanColor = (i) => LEAN_COLORS[i % LEAN_COLORS.length];

const state = {
  gamma: 0,
  theta: 0,
  playing: false,
  timer: null,
  overlays: { pressure: true, travel: true, centroid: true },
  tab: "pattern"
};

const $ = (sel) => document.querySelector(sel);
const fmt = (v, n = 2) => (v === null || v === undefined || !isFinite(v) ? "–" : Number(v).toFixed(n));
const pct = (v, n = 1) => (v === null || !isFinite(v) ? "–" : (100 * v).toFixed(n) + "%");

/* ------------------------------------------------------------------ */
/* plotly defaults                                                     */
/* ------------------------------------------------------------------ */
function axisBase() {
  return {
    gridcolor: THEME.grid, zerolinecolor: THEME.axis, linecolor: THEME.axis,
    tickfont: { size: 10.5, color: THEME.muted },
    automargin: true
  };
}
/* Plotly 3 removed the `titlefont` axis attribute; a title is now {text, font}.
   Every axis goes through here so that stays true in one place. */
function ax(title, extra) {
  const a = Object.assign(axisBase(), extra || {});
  if (title) a.title = { text: title, font: { size: 11.5, color: THEME.muted } };
  return a;
}
function layout(extra) {
  return Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: THEME.text, size: 11.5 },
    margin: { l: 58, r: 18, t: 10, b: 40 },
    hovermode: "closest",
    showlegend: true,
    legend: { orientation: "h", y: 1.14, x: 0, font: { size: 10.5 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: ax(),
    yaxis: ax()
  }, extra || {});
}
const CONFIG = { displayModeBar: false, responsive: true };

/* ------------------------------------------------------------------ */
/* header                                                              */
/* ------------------------------------------------------------------ */
function renderBanner() {
  const pv = META.provenance || {};
  const el = $("#placeholderBanner");
  const modelled = [];
  const measured = [];
  (pv.geometry === "measured" ? measured : modelled).push(
    pv.geometry === "measured" ? "tread geometry (from DXF)" : "tread geometry (synthetic)");
  if (pv.block_depth === "assumed") modelled.push("non-skid depth and draft (assumed, not in the drawing)");
  if (pv.crown === "measured") measured.push("crown profile"); else modelled.push("crown profile (guessed)");
  if (pv.contact_patch === "measured") measured.push("contact patch (measured at every lean angle)");
  else if (pv.contact_patch === "partly-measured") measured.push("contact patch (measured, rescaled to unmeasured lean angles)");
  else if (pv.contact_patch === "derived-from-measured") modelled.push("contact patch (derived from a measurement)");
  else modelled.push("contact patch (generated)");

  const stiffNote =
    "Block stiffness is the Okonieski beam model, verified number-for-number against " +
    "Tread Pattern Stiffness Estimation Tool v6.4.";
  el.innerHTML =
    "<strong>What is measured, what is modelled.</strong><span>" +
    (measured.length ? "<b>From data:</b> " + measured.join("; ") + ". " : "") +
    (modelled.length ? "<b>Modelled or assumed:</b> " + modelled.join("; ") + ". " : "") +
    stiffNote +
    ' Flag thresholds are uncalibrated. <a href="#tab-about" data-goto="about">Details &rarr;</a></span>';
}

function renderHeader() {
  $("#subtitle").textContent =
    META.name + " · " + META.source + " source · generated " + META.generated;

  const chips = [
    ["circumference", fmt(C, 1) + " mm"],
    ["tread width", fmt(META.tread_width, 1) + " mm"],
    ["blocks", META.n_blocks],
    ["pitches", META.n_pitches],
    ["raster", META.grid[1] + "×" + META.grid[0] + " @ " + fmt(META.resolution_mm, 2) + " mm"],
    ["max lean", fmt(META.max_supported_lean, 1) + "°"],
    ["curvature corr.", META.curvature_correction ? "ON" : "off"]
  ];
  $("#metaChips").innerHTML = chips
    .map((c) => '<span class="chip">' + c[0] + " <b>" + c[1] + "</b></span>")
    .join("");

  const nFlag = D.flags.filter((f) => f.severity === "flag").length;
  $("#footerMeta").textContent =
    D.leans.length + " lean angles × 360° · " +
    D.flags.length + " checks, " + nFlag + " flagged";
}

/* ------------------------------------------------------------------ */
/* tabs + controls                                                     */
/* ------------------------------------------------------------------ */
const TABS = [
  ["pattern", "Pattern &amp; patch"],
  ["contact", "Contact &amp; stiffness"],
  ["orders", "Order content"],
  ["lean", "Lean sweep"],
  ["zones", "Zones"],
  ["grooves", "Grooves"],
  ["patch", "Contact patch"],
  ["diagnostics", "Diagnostics"],
  ["about", "About"]
];

function renderTabs() {
  const nFlag = D.flags.filter((f) => f.severity === "flag").length;
  $("#tabs").innerHTML = TABS.map(([id, label]) => {
    const badge = id === "diagnostics" && nFlag ? '<span class="badge">' + nFlag + "</span>" : "";
    return '<button data-tab="' + id + '">' + label + badge + "</button>";
  }).join("");
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  document.body.addEventListener("click", (e) => {
    const a = e.target.closest("[data-goto]");
    if (a) { e.preventDefault(); showTab(a.dataset.goto); }
  });
}

function showTab(id) {
  state.tab = id;
  TABS.forEach(([t]) => { $("#tab-" + t).hidden = t !== id; });
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
  // Plotly needs a resize nudge for plots drawn while their tab was hidden.
  document.querySelectorAll("#tab-" + id + " .plot").forEach((el) => {
    if (el.data) Plotly.Plots.resize(el);
  });
  // The rotation slider only means something where theta is on screen.
  $("#controls").style.display = (id === "pattern" || id === "contact") ? "" : "none";
}

/* ------------------------------------------------------------------ */
/* theme                                                               */
/* ------------------------------------------------------------------ */
const THEME_KEY = "treadeval-theme";

function applyTheme(mode) {
  if (mode === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* private mode */ }
  document.querySelectorAll("#themeToggle button").forEach((b) =>
    b.classList.toggle("on", b.dataset.theme === mode));
  readTheme();
  redrawAll();
}

function renderTheme() {
  let saved = "auto";
  try { saved = localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { /* ignore */ }
  $("#themeToggle").innerHTML = [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]]
    .map(([v, label]) => '<button data-theme="' + v + '">' + label + "</button>").join("");
  $("#themeToggle").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-theme]");
    if (b) applyTheme(b.dataset.theme);
  });
  // Follow the OS while in auto mode.
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) { readTheme(); redrawAll(); }
  });
  applyTheme(saved);
}

function redrawAll() {
  if (!window.__treadReady) return;
  drawPattern();
  drawCrown();
  drawOverview();
  drawContactTab();
  drawOrders();
  drawLeanTab();
  drawZones();
  drawGrooves();
  drawPatchTab();
  onThetaChange();
}

function renderControls() {
  $("#leanButtons").innerHTML = D.leans
    .map((l, i) => '<button data-lean="' + i + '" style="color:' + leanColor(i) + '">' +
      fmt(l.gamma_deg, 0) + "°</button>")
    .join("");
  $("#leanButtons").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-lean]");
    if (!b) return;
    state.gamma = +b.dataset.lean;
    syncLeanButtons();
    onLeanChange();
  });
  syncLeanButtons();

  const slider = $("#thetaSlider");
  slider.addEventListener("input", () => { state.theta = +slider.value; onThetaChange(); });
  $("#playBtn").addEventListener("click", togglePlay);

  document.querySelectorAll("[data-overlay]").forEach((b) => {
    b.addEventListener("click", () => {
      const k = b.dataset.overlay;
      state.overlays[k] = !state.overlays[k];
      b.classList.toggle("on", state.overlays[k]);
      updatePattern();
    });
  });
}

function syncLeanButtons() {
  document.querySelectorAll("#leanButtons button").forEach((b) => {
    b.classList.toggle("on", +b.dataset.lean === state.gamma);
  });
}

function togglePlay() {
  state.playing = !state.playing;
  $("#playBtn").innerHTML = state.playing ? "&#10074;&#10074;" : "&#9654;";
  if (state.timer) clearInterval(state.timer);
  if (state.playing) {
    state.timer = setInterval(() => {
      state.theta = (state.theta + 1.5) % 360;
      $("#thetaSlider").value = state.theta;
      onThetaChange();
    }, 90);
  }
}

/* ------------------------------------------------------------------ */
/* helpers over the payload                                            */
/* ------------------------------------------------------------------ */
const curLean = () => D.leans[state.gamma];
const thetaMM = () => (state.theta / 360) * C;

function thetaIndex(lean, thetaDeg) {
  const arr = lean.theta_deg;
  const step = 360 / arr.length;
  return Math.min(arr.length - 1, Math.max(0, Math.round(thetaDeg / step)));
}

function sampleAt(lean, key, thetaDeg) {
  return lean[key][thetaIndex(lean, thetaDeg)];
}

/* block bounding boxes, computed once, so the pattern window can be filtered
   cheaply on every slider tick instead of redrawing all 260-odd polygons. */
const BBOX = D.pattern.blocks.map((b) => {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const p of b.poly) {
    if (p[0] < x0) x0 = p[0];
    if (p[0] > x1) x1 = p[0];
    if (p[1] < y0) y0 = p[1];
    if (p[1] > y1) y1 = p[1];
  }
  return { x0, x1, y0, y1 };
});

/* ------------------------------------------------------------------ */
/* 1. pattern map                                                      */
/* ------------------------------------------------------------------ */
const ZONES = D.zone_order;

function windowHalfWidth() {
  return Math.max(115, 1.45 * curLean().patch.a * 2);
}

function patternTraces() {
  const hw = windowHalfWidth();
  const cx = thetaMM();
  const lo = cx - hw, hi = cx + hw;
  const acc = {};
  ZONES.forEach((z) => (acc[z] = { x: [], y: [] }));

  for (let i = 0; i < D.pattern.blocks.length; i++) {
    const bb = BBOX[i], blk = D.pattern.blocks[i];
    for (const off of [-C, 0, C]) {
      if (bb.x1 + off < lo || bb.x0 + off > hi) continue;
      const a = acc[blk.zone];
      for (const p of blk.poly) { a.x.push(p[0] + off); a.y.push(p[1]); }
      a.x.push(blk.poly[0][0] + off); a.y.push(blk.poly[0][1]);
      a.x.push(null); a.y.push(null);
    }
  }

  const traces = ZONES.map((z) => ({
    x: acc[z].x, y: acc[z].y, name: z, type: "scatter", mode: "lines",
    fill: "toself", fillcolor: ZONE_COLOR[z] + "33",
    line: { color: ZONE_COLOR[z], width: 1 },
    hoverinfo: "skip", showlegend: true
  }));

  const L = curLean(), P = L.patch;

  // patch outline
  const ox = P.outline.map((p) => p[0] + cx);
  const oy = P.outline.map((p) => p[1]);
  traces.push({
    x: ox, y: oy, name: "contact patch", type: "scatter", mode: "lines",
    line: { color: THEME.patchLine, width: 2 }, hoverinfo: "skip"
  });

  // pressure contours: p/p0 = 1 - (x/a)^2 - ((y-yc)/b)^2
  if (state.overlays.pressure) {
    [0.25, 0.5, 0.75].forEach((f, k) => {
      const s = Math.sqrt(1 - f);
      const xs = [], ys = [];
      for (let t = 0; t <= 72; t++) {
        const a = (t / 72) * 2 * Math.PI;
        const yv = P.y_center + P.b * s * Math.sin(a);
        if (Math.abs(yv) > HALF_W) continue;
        xs.push(cx + P.a * s * Math.cos(a));
        ys.push(yv);
      }
      traces.push({
        x: xs, y: ys, type: "scatter", mode: "lines",
        name: (f * 100).toFixed(0) + "% p₀",
        line: { color: THEME.patchLine, width: 1, dash: "dot" }, opacity: 0.55 - k * 0.12,
        hoverinfo: "skip", showlegend: k === 0
      });
    });
  }

  // instantaneous travel direction inside the patch
  if (state.overlays.travel) {
    const xs = [], ys = [];
    const R = P.path_radius;
    [-0.62, 0, 0.62].forEach((fr) => {
      const x0 = P.a * fr;
      const psi = R ? Math.atan2(x0, R) : 0;
      const len = 17;
      xs.push(cx + x0 - (len / 2) * Math.cos(psi), cx + x0 + (len / 2) * Math.cos(psi), null);
      ys.push(P.y_center - (len / 2) * Math.sin(psi), P.y_center + (len / 2) * Math.sin(psi), null);
    });
    traces.push({
      x: xs, y: ys, type: "scatter", mode: "lines", name: "travel direction",
      line: { color: THEME.accent, width: 2 }, hoverinfo: "skip"
    });
  }

  // measured centre of contact vs. the patch's own geometric centre
  if (state.overlays.centroid) {
    const cy = sampleAt(L, "centroid_y", state.theta);
    traces.push({
      x: [cx, cx], y: [P.y_center, cy], type: "scatter", mode: "markers",
      name: "centroid: geometric / actual",
      marker: { color: [THEME.muted, THEME.flag], size: [8, 10], symbol: ["circle-open", "x"] },
      hovertemplate: "y = %{y:.2f} mm<extra></extra>"
    });
  }
  return traces;
}

function patternLayout() {
  const hw = windowHalfWidth(), cx = thetaMM();
  return layout({
    margin: { l: 52, r: 12, t: 8, b: 38 },
    xaxis: ax("circumferential position (mm)", { range: [cx - hw, cx + hw], constrain: "domain" }),
    yaxis: ax("lateral (mm)", {
      range: [-HALF_W, HALF_W], scaleanchor: "x", scaleratio: 1, constrain: "domain"
    }),
    legend: { orientation: "h", y: 1.1, x: 0, font: { size: 10 } },
    shapes: zoneShapes()
  });
}

function zoneShapes() {
  const b = META.zone_bounds;
  const out = [];
  [b.center[1], b.intermediate[1]].forEach((v) => {
    [-v, v].forEach((yy) => out.push({
      type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: yy, y1: yy,
      line: { color: THEME.axis, width: 1, dash: "dot" }, layer: "below"
    }));
  });
  return out;
}

function drawPattern() { Plotly.newPlot("patternPlot", patternTraces(), patternLayout(), CONFIG); }
function updatePattern() { Plotly.react("patternPlot", patternTraces(), patternLayout(), CONFIG); }

/* crown cross-section ------------------------------------------------ */
function drawCrown() {
  const cr = D.pattern.crown, P = curLean().patch;
  const yc = P.y_center;
  const iNear = cr.y.reduce((best, v, i) => (Math.abs(v - yc) < Math.abs(cr.y[best] - yc) ? i : best), 0);
  const yp = cr.y_proj[iNear], zp = cr.z[iNear], phi = (cr.phi_deg[iNear] * Math.PI) / 180;
  const roadLen = 46;

  const traces = [
    {
      x: cr.y_proj, y: cr.z, type: "scatter", mode: "lines", name: "crown",
      line: { color: THEME.muted, width: 2 },
      hovertemplate: "y' %{x:.1f} mm<br>drop %{y:.2f} mm<extra></extra>"
    },
    {
      x: [yp - roadLen * Math.cos(phi), yp + roadLen * Math.cos(phi)],
      y: [zp - roadLen * Math.sin(phi), zp + roadLen * Math.sin(phi)],
      type: "scatter", mode: "lines", name: "road plane",
      line: { color: THEME.accent, width: 2, dash: "dash" }, hoverinfo: "skip"
    },
    {
      x: [yp], y: [zp], type: "scatter", mode: "markers", name: "contact point",
      marker: { color: THEME.flag, size: 9 }, hoverinfo: "skip"
    }
  ];
  Plotly.react("crownPlot", traces, layout({
    margin: { l: 46, r: 10, t: 6, b: 34 },
    showlegend: false,
    xaxis: ax("projected lateral (mm)"),
    yaxis: ax("drop (mm)", { autorange: "reversed", scaleanchor: "x", scaleratio: 1 })
  }), CONFIG);
}

function renderPatchStats() {
  const L = curLean(), P = L.patch, S = L.shape;
  const rows = [
    ["lean γ", fmt(L.gamma_deg, 0) + "°"],
    ["patch length", fmt(S.length_mm, 1) + " mm"],
    ["patch width", fmt(S.width_mm, 1) + " mm"],
    ["patch area", fmt(L.patch_area, 0) + " mm²"],
    ["lateral centre", fmt(P.y_center, 1) + " mm"],
    ["peak pressure", fmt(P.peak_pressure, 2) + " MPa"],
    ["normal load", fmt(P.normal_load, 0) + " N"],
    ["lateral radius", fmt(P.r_lat, 0) + " mm"],
    ["path radius", P.path_radius ? fmt(P.path_radius, 0) + " mm" : "∞ (upright)"],
    ["compactness", fmt(S.compactness, 1)],
    ["aspect L/W", fmt(S.aspect_ratio, 2)],
    ["clipped by tread edge", P.clipped ? "YES" : "no"]
  ];
  $("#patchStats").innerHTML = rows
    .map((r) => '<span class="k">' + r[0] + '</span><span class="v">' + r[1] + "</span>")
    .join("");
}

/* full-revolution overview ------------------------------------------ */
function drawOverview() {
  const traces = D.leans.map((l, i) => ({
    x: l.theta_deg, y: l.contact_area, type: "scatter", mode: "lines",
    name: fmt(l.gamma_deg, 0) + "°",
    line: { color: leanColor(i), width: i === state.gamma ? 2.2 : 1 },
    opacity: i === state.gamma ? 1 : 0.32,
    hovertemplate: "θ %{x:.1f}°<br>%{y:.0f} mm²<extra>" + fmt(l.gamma_deg, 0) + "°</extra>"
  }));
  Plotly.react("overviewPlot", traces, layout({
    margin: { l: 58, r: 18, t: 26, b: 38 },
    xaxis: ax("rotation angle θ (deg)", { range: [0, 360], dtick: 45 }),
    yaxis: ax("contact area (mm²)"),
    shapes: [cursorShape()].concat(pitchTicks())
  }), CONFIG);
}

function cursorShape() {
  return {
    type: "line", x0: state.theta, x1: state.theta, yref: "paper", y0: 0, y1: 1,
    line: { color: THEME.patchLine, width: 1.4 }
  };
}

function pitchTicks() {
  return D.pattern.pitch_starts.map((s) => ({
    type: "line", x0: (s / C) * 360, x1: (s / C) * 360, yref: "paper", y0: 0, y1: 0.055,
    line: { color: THEME.muted, width: 1 }
  }));
}

/* ------------------------------------------------------------------ */
/* 2. contact + stiffness families                                     */
/* ------------------------------------------------------------------ */
function familyTraces(key, unit) {
  return D.leans.map((l, i) => ({
    x: l.theta_deg, y: l[key], type: "scatter", mode: "lines",
    name: fmt(l.gamma_deg, 0) + "°",
    line: { color: leanColor(i), width: i === state.gamma ? 2.2 : 1.1 },
    opacity: i === state.gamma ? 1 : 0.3,
    hovertemplate: "θ %{x:.1f}°<br>%{y:.3s} " + unit + "<extra>" + fmt(l.gamma_deg, 0) + "°</extra>"
  }));
}

function anomalyShapes(kind) {
  const bands = (curLean().anomalies || {})[kind] || [];
  return bands.map((b) => ({
    type: "rect", x0: b.start, x1: Math.max(b.end, b.start + 0.6), yref: "paper", y0: 0, y1: 1,
    fillcolor: THEME.flag, opacity: 0.16, line: { width: 0 }, layer: "below"
  }));
}

function drawFamily(div, key, unit, title, kind) {
  Plotly.react(div, familyTraces(key, unit), layout({
    margin: { l: 62, r: 16, t: 28, b: 38 },
    xaxis: ax("rotation angle θ (deg)", { range: [0, 360], dtick: 60 }),
    yaxis: ax(title),
    shapes: (kind ? anomalyShapes(kind) : []).concat([cursorShape()])
  }), CONFIG);
}

function drawContactTab() {
  drawFamily("areaPlot", "contact_area", "mm2", "contact area (mm²)", "area");
  drawFamily("kxPlot", "kx", "N/mm", "Kₓ (N/mm)", "kx");
  drawFamily("kyPlot", "ky", "N/mm", "Kᵧ (N/mm)", "ky");
  drawFamily("anisoPlot", "anisotropy", "", "Kₓ / Kᵧ");
  drawFamily("blockPlot", "block_count", "", "effective blocks in contact");
}

/* ------------------------------------------------------------------ */
/* 3. order content                                                    */
/* ------------------------------------------------------------------ */
function drawOrders() {
  const traces = D.leans.map((l, i) => ({
    x: l.spectra.area.orders, y: l.spectra.area.amplitude, type: "bar",
    name: fmt(l.gamma_deg, 0) + "°",
    marker: { color: leanColor(i), opacity: i === state.gamma ? 1 : 0.42 },
    hovertemplate: "order %{x}<br>%{y:.3f} of mean<extra>" + fmt(l.gamma_deg, 0) + "°</extra>"
  }));
  Plotly.react("orderPlot", traces, layout({
    barmode: "group", bargap: 0.12, bargroupgap: 0.05,
    margin: { l: 62, r: 16, t: 28, b: 40 },
    xaxis: ax("rotation order (events per revolution)", { range: [0.5, 45] }),
    yaxis: ax("amplitude / mean area", { tickformat: ".1%" })
  }), CONFIG);

  const P = D.pitch;
  Plotly.react("pitchSeqPlot", [{
    x: P.lengths.map((_, i) => i), y: P.lengths, type: "bar",
    marker: { color: THEME.accent }, name: "pitch length",
    hovertemplate: "pitch %{x}<br>%{y:.2f} mm<extra></extra>"
  }], layout({
    showlegend: false,
    margin: { l: 58, r: 16, t: 12, b: 40 },
    xaxis: ax("pitch index around the tyre"),
    yaxis: ax("length (mm)"),
    shapes: [{
      type: "line", xref: "paper", x0: 0, x1: 1, y0: P.mean, y1: P.mean,
      line: { color: THEME.muted, width: 1, dash: "dash" }
    }]
  }), CONFIG);

  if (P.sequence_spectrum) {
    Plotly.react("pitchSpecPlot", [{
      x: P.sequence_spectrum.orders, y: P.sequence_spectrum.amplitude, type: "bar",
      marker: { color: leanColor(2) },
      hovertemplate: "order %{x}<br>%{y:.3f}<extra></extra>"
    }], layout({
      showlegend: false,
      margin: { l: 58, r: 16, t: 12, b: 40 },
      xaxis: ax("order (repeats per revolution)", { range: [0.5, Math.min(40, P.n_pitches / 2 + 1)] }),
      yaxis: ax("amplitude / mean pitch", { tickformat: ".1%" })
    }), CONFIG);
  }

  const polar = D.leans.map((l, i) => {
    const mean = l.contact_area.reduce((a, b) => a + b, 0) / l.contact_area.length;
    return {
      type: "scatterpolar", mode: "lines",
      r: l.contact_area.map((v) => v / mean).concat([l.contact_area[0] / mean]),
      theta: l.theta_deg.concat([360]),
      name: fmt(l.gamma_deg, 0) + "°",
      line: { color: leanColor(i), width: i === state.gamma ? 2.2 : 1 },
      opacity: i === state.gamma ? 1 : 0.4,
      hovertemplate: "θ %{theta:.0f}°<br>%{r:.3f}× mean<extra></extra>"
    };
  });
  Plotly.react("polarPlot", polar, layout({
    margin: { l: 30, r: 30, t: 30, b: 30 },
    polar: {
      bgcolor: "rgba(0,0,0,0)",
      radialaxis: { gridcolor: THEME.grid, linecolor: THEME.axis, tickfont: { size: 9.5, color: THEME.muted }, angle: 90 },
      angularaxis: { direction: "clockwise", rotation: 90, gridcolor: THEME.grid, linecolor: THEME.axis, tickfont: { size: 9.5, color: THEME.muted } }
    }
  }), CONFIG);
}

/* ------------------------------------------------------------------ */
/* 4. lean sweep                                                       */
/* ------------------------------------------------------------------ */
function drawLeanTab() {
  const S = D.summary;
  const g = S.map((r) => r.gamma_deg);
  const series = [
    ["area_cov", "contact area", leanColor(0)],
    ["kx_cov", "Kₓ", ZONE_COLOR.intermediate],
    ["ky_cov", "Kᵧ", ZONE_COLOR.shoulder],
    ["block_count_cov", "blocks in contact", leanColor(2)]
  ].map(([k, name, color]) => ({
    x: g, y: S.map((r) => r[k]), type: "scatter", mode: "lines+markers", name: name,
    line: { color: color, width: 2 }, marker: { size: 7 },
    hovertemplate: "γ %{x}°<br>CoV %{y:.2%}<extra>" + name + "</extra>"
  }));
  Plotly.react("varLeanPlot", series, layout({
    margin: { l: 62, r: 16, t: 30, b: 42 },
    xaxis: ax("lean angle γ (deg)"),
    yaxis: ax("coefficient of variation over 360°", { tickformat: ".1%", rangemode: "tozero" })
  }), CONFIG);

  Plotly.react("blockLeanPlot", [
    {
      x: g, y: S.map((r) => r.block_count_mean), type: "scatter", mode: "lines+markers",
      name: "effective (area-weighted)", line: { color: leanColor(0), width: 2 }, marker: { size: 7 }
    },
    {
      x: g, y: S.map((r) => r.block_count_discrete_mean), type: "scatter", mode: "lines+markers",
      name: "discrete (>50% inside)", line: { color: leanColor(2), width: 2, dash: "dot" }, marker: { size: 7 }
    },
    {
      x: g, y: S.map((r) => r.block_count_min), type: "scatter", mode: "lines",
      name: "worst θ", line: { color: THEME.flag, width: 1, dash: "dash" }
    }
  ], layout({
    margin: { l: 58, r: 16, t: 30, b: 42 },
    xaxis: ax("lean angle γ (deg)"),
    yaxis: ax("blocks in the contact patch", { rangemode: "tozero" })
  }), CONFIG);

  Plotly.react("shapeLeanPlot", [
    { x: g, y: S.map((r) => r.patch_length), type: "scatter", mode: "lines+markers", name: "length", line: { color: leanColor(0), width: 2 } },
    { x: g, y: S.map((r) => r.patch_width), type: "scatter", mode: "lines+markers", name: "width", line: { color: ZONE_COLOR.intermediate, width: 2 } },
    { x: g, y: S.map((r) => r.patch_area), type: "scatter", mode: "lines+markers", name: "area", yaxis: "y2", line: { color: ZONE_COLOR.shoulder, width: 2, dash: "dot" } }
  ], layout({
    margin: { l: 58, r: 62, t: 30, b: 42 },
    xaxis: ax("lean angle γ (deg)"),
    yaxis: ax("mm", { rangemode: "tozero" }),
    yaxis2: ax("mm²", { overlaying: "y", side: "right", rangemode: "tozero" })
  }), CONFIG);

  Plotly.react("centroidPlot", [
    {
      x: g, y: S.map((r) => r.centroid_geometric_mm), type: "scatter", mode: "lines+markers",
      name: "geometric (lean + crown)", line: { color: THEME.muted, width: 2 }, marker: { size: 7 }
    },
    {
      x: g, y: S.map((r) => r.centroid_residual_p2p_mm), type: "bar",
      name: "residual, peak-to-peak", marker: { color: THEME.flag }, yaxis: "y2"
    },
    {
      x: g, y: S.map((r) => r.centroid_residual_wander_rms_mm), type: "bar",
      name: "residual wander, RMS", marker: { color: THEME.watch }, yaxis: "y2"
    },
    {
      x: g, y: S.map((r) => r.centroid_residual_bias_mm), type: "bar",
      name: "residual bias (constant offset)", marker: { color: THEME.muted }, yaxis: "y2"
    }
  ], layout({
    barmode: "group",
    margin: { l: 58, r: 66, t: 30, b: 42 },
    xaxis: ax("lean angle γ (deg)"),
    yaxis: ax("geometric shift (mm)"),
    yaxis2: ax("residual (mm)", { overlaying: "y", side: "right", rangemode: "tozero" })
  }), CONFIG);

  const cols = [
    ["γ (deg)", (r) => fmt(r.gamma_deg, 0)],
    ["patch L×W (mm)", (r) => fmt(r.patch_length, 1) + " × " + fmt(r.patch_width, 1)],
    ["patch area (mm²)", (r) => fmt(r.patch_area, 0)],
    ["land in patch", (r) => pct(r.land_ratio_mean)],
    ["area CoV", (r) => pct(r.area_cov)],
    ["area max/min", (r) => fmt(r.area_max_min, 2)],
    ["blocks", (r) => fmt(r.block_count_mean, 2)],
    ["Kₓ mean", (r) => fmt(r.kx_mean, 0)],
    ["Kᵧ mean", (r) => fmt(r.ky_mean, 0)],
    ["Kₓ/Kᵧ", (r) => fmt(r.anisotropy_mean, 3)],
    ["top order", (r) => (r.dominant_orders[0] ? r.dominant_orders[0].order + " @ " + pct(r.dominant_orders[0].amplitude) : "–")],
    ["centre share", (r) => pct(r.center_share, 0)],
    ["shoulder share", (r) => pct(r.shoulder_share, 0)],
    ["clipped", (r) => (r.patch_clipped ? "yes" : "no")]
  ];
  $("#leanTable").innerHTML = table(cols.map((c) => c[0]), S.map((r) => cols.map((c) => c[1](r))));
}

function table(head, rows) {
  return "<table><thead><tr>" + head.map((h) => "<th>" + h + "</th>").join("") +
    "</tr></thead><tbody>" +
    rows.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("") +
    "</tbody></table>";
}

/* ------------------------------------------------------------------ */
/* 5. zones                                                            */
/* ------------------------------------------------------------------ */
function zoneOf(y) {
  const b = META.zone_bounds, a = Math.abs(y);
  if (a <= b.center[1]) return "center";
  if (a <= b.intermediate[1]) return "intermediate";
  return "shoulder";
}

function drawZones() {
  const Z = D.zones;
  Plotly.react("zoneBinPlot", [{
    x: Z.bin_centers, y: Z.bin_land_ratio, type: "bar",
    marker: { color: Z.bin_centers.map((y) => ZONE_COLOR[zoneOf(y)]) },
    hovertemplate: "y %{x:.1f} mm<br>land %{y:.1%}<extra></extra>"
  }], layout({
    showlegend: false,
    margin: { l: 62, r: 16, t: 12, b: 42 },
    xaxis: ax("lateral position (mm)"),
    yaxis: ax("land ratio", { tickformat: ".0%", range: [0, 1] }),
    shapes: [{
      type: "line", xref: "paper", x0: 0, x1: 1, y0: Z.overall_land_ratio, y1: Z.overall_land_ratio,
      line: { color: THEME.patchLine, width: 1, dash: "dash" }
    }]
  }), CONFIG);

  const g = D.summary.map((r) => r.gamma_deg);
  Plotly.react("zoneSharePlot", ZONES.map((z) => ({
    x: g, y: D.summary.map((r) => r.zone_share[z]), type: "bar", name: z,
    marker: { color: ZONE_COLOR[z] },
    hovertemplate: "γ %{x}°<br>%{y:.1%}<extra>" + z + "</extra>"
  })), layout({
    barmode: "stack",
    margin: { l: 58, r: 16, t: 30, b: 42 },
    xaxis: ax("lean angle γ (deg)", { type: "category" }),
    yaxis: ax("share of in-patch rubber", { tickformat: ".0%" })
  }), CONFIG);

  const rows = ZONES.map((z) => {
    const s = D.zones.zones[z];
    return [z, fmt(s.land_ratio * 100, 1) + "%", fmt(s.area_per_mm_circumference, 1),
      s.n_blocks, fmt(s.mean_block_area, 0)];
  });
  $("#zoneTable").innerHTML = table(
    ["zone", "land ratio", "mm² per mm circ.", "blocks", "mean block area (mm²)"], rows) +
    '<p class="hint" style="margin-top:8px">Overall land ratio ' + pct(D.zones.overall_land_ratio) +
    " · left/right imbalance " + fmt(D.zones.lateral_imbalance * 100, 2) + " points.</p>";
}

/* ------------------------------------------------------------------ */
/* 6. grooves                                                          */
/* ------------------------------------------------------------------ */
function drawGrooves() {
  const base = D.leans[0].groove;
  const traces = [];

  D.leans.forEach((l, i) => {
    const gr = l.groove;
    const xs = [], up = [], dn = [];
    for (let k = 0; k < gr.bin_centers.length; k++) {
      if (!gr.in_patch[k] || gr.angle_to_travel[k] === null || gr.spin_spread[k] === null) continue;
      xs.push(gr.bin_centers[k]);
      up.push(gr.angle_to_travel[k] + gr.spin_spread[k]);
      dn.push(gr.angle_to_travel[k] - gr.spin_spread[k]);
    }
    if (!xs.length) return;
    traces.push({
      x: xs.concat(xs.slice().reverse()), y: up.concat(dn.slice().reverse()),
      type: "scatter", mode: "lines", fill: "toself",
      fillcolor: leanColor(i) + (i === state.gamma ? "55" : "22"),
      line: { color: leanColor(i), width: i === state.gamma ? 1.4 : 0.6 },
      name: fmt(l.gamma_deg, 0) + "° spin swing",
      hoverinfo: "skip"
    });
  });

  traces.push({
    x: base.bin_centers, y: base.angle_to_travel, type: "scatter", mode: "lines+markers",
    name: "groove angle (pattern)", line: { color: THEME.patchLine, width: 2.2 }, marker: { size: 5 },
    hovertemplate: "y %{x:.0f} mm<br>%{y:.1f}° to travel<extra></extra>"
  });

  Plotly.react("groovePlot", traces, layout({
    margin: { l: 62, r: 16, t: 30, b: 42 },
    xaxis: ax("lateral position (mm)", { range: [-HALF_W, HALF_W] }),
    yaxis: ax("angle to instantaneous travel direction (deg)", { range: [0, 100] }),
    shapes: zoneShapesVertical()
  }), CONFIG);

  const byZone = {};
  D.pattern.blocks.forEach((b) => { (byZone[b.zone] = byZone[b.zone] || []).push(b); });
  Plotly.react("blockScatterPlot", ZONES.map((z) => ({
    x: (byZone[z] || []).map((b) => b.area),
    y: (byZone[z] || []).map((b) => b.kx),
    text: (byZone[z] || []).map((b) => b.id + "<br>h " + fmt(b.h, 2) + " mm, draft " + fmt(b.draft, 1) +
      "°, " + b.sipes + " sipes"),
    mode: "markers", type: "scatter", name: z,
    marker: { color: ZONE_COLOR[z], size: 7, opacity: 0.75, line: { width: 0 } },
    hovertemplate: "%{text}<br>area %{x:.0f} mm²<br>Kₓ %{y:.0f} N/mm<extra></extra>"
  })), layout({
    margin: { l: 62, r: 16, t: 30, b: 42 },
    xaxis: ax("block plan area (mm²)"),
    yaxis: ax("block Kₓ (N/mm)")
  }), CONFIG);
}

function zoneShapesVertical() {
  const b = META.zone_bounds, out = [];
  [b.center[1], b.intermediate[1]].forEach((v) => {
    [-v, v].forEach((xx) => out.push({
      type: "line", x0: xx, x1: xx, yref: "paper", y0: 0, y1: 1,
      line: { color: THEME.axis, width: 1, dash: "dot" }, layer: "below"
    }));
  });
  return out;
}

/* ------------------------------------------------------------------ */
/* 7. contact patch definition                                          */
/* ------------------------------------------------------------------ */
function drawPatchTab() {
  // Outlines of every lean angle's patch, drawn in tread coordinates.
  const EDGE = 1.15 * Math.max(...D.leans.map((l) => l.shape.length_mm)) / 2;
  const traces = D.leans.map((l, i) => {
    const o = l.patch.outline;
    return {
      x: o.map((p) => p[0]).concat([o[0][0]]),
      y: o.map((p) => p[1]).concat([o[0][1]]),
      type: "scatter", mode: "lines", fill: "toself",
      fillcolor: leanColor(i) + (i === state.gamma ? "44" : "18"),
      line: { color: leanColor(i), width: i === state.gamma ? 2.4 : 1.2,
              dash: l.patch.measured ? "solid" : "dot" },
      name: fmt(l.gamma_deg, 0) + "° · " + l.patch.source,
      hovertemplate: "x %{x:.1f}<br>y %{y:.1f} mm<extra>" + fmt(l.gamma_deg, 0) + "°</extra>"
    };
  });
  traces.push({
    x: [-EDGE, EDGE, null, -EDGE, EDGE], y: [-HALF_W, -HALF_W, null, HALF_W, HALF_W],
    type: "scatter", mode: "lines", name: "tread edge",
    line: { color: THEME.muted, width: 1, dash: "dash" }, hoverinfo: "skip"
  });
  Plotly.react("patchOutlinePlot", traces, layout({
    margin: { l: 58, r: 16, t: 30, b: 42 },
    xaxis: ax("circumferential offset from patch centre (mm)"),
    yaxis: ax("lateral (mm)", { scaleanchor: "x", scaleratio: 1 })
  }), CONFIG);

  // Where each lean angle's patch came from.
  const rows = D.leans.map((l) => [
    fmt(l.gamma_deg, 0) + "°",
    '<span class="src src-' + l.patch.source + '">' + l.patch.source + "</span>",
    fmt(l.shape.length_mm, 1) + " × " + fmt(l.shape.width_mm, 1),
    fmt(l.patch_area, 0),
    fmt(l.patch.y_center, 1),
    fmt(l.patch.peak_pressure, 2),
    l.patch.normal_load === null ? "–" : fmt(l.patch.normal_load, 0),
    l.patch.clipped ? "yes" : "no",
    l.patch.provenance || "–"
  ]);
  $("#patchSourceTable").innerHTML = table(
    ["γ", "source", "L × W (mm)", "area (mm²)", "centre y (mm)", "peak p (MPa)",
     "load (N)", "clipped", "how this patch was obtained"], rows);

  const measured = D.leans.filter((l) => l.patch.measured).length;
  const note = measured === D.leans.length
    ? "Every lean angle uses a measured footprint."
    : measured
      ? measured + " of " + D.leans.length + " lean angles use a measured footprint; the rest are " +
        "derived from it or generated. Dotted outlines above are not measured."
      : "No measured footprint was supplied, so every patch is generated from the model. " +
        "Importing even a single upright footprint improves all of them — see below.";
  $("#patchCoverageNote").textContent = note;

  $("#patchHowTo").innerHTML = `
    <p>The contact patch is an <strong>input</strong>, not a fixed assumption. It can be any closed
    shape: the sweep only ever rasterises the outline, so a traced footprint with ragged edges works
    exactly like a generated ellipse.</p>
    <h3>Import a measured footprint</h3>
    <p>An outline (<code>.csv</code> of <code>x,y</code> in mm, <code>.json</code>, or <code>.dxf</code>),
    a pressure map (<code>.csv</code> matrix from pressure film or FEA), or both:</p>
    <pre>python build_report.py --cp footprint_00deg.csv --cp-gamma 0 --cp-load 1500</pre>
    <p>For several lean angles, list them in a manifest and pass that instead:</p>
    <pre>python build_report.py --cp footprints.json</pre>
    <h3>Generate one from inputs you already have</h3>
    <pre>python build_report.py --cp-model rhyne --inflation 250 --load 1500 --section-width 130</pre>
    <p><code>rhyne</code> derives the footprint from load and inflation pressure — the same relations
    the stiffness tool uses. <code>winkler</code> (the default) derives it from the crown profile by
    elastic-foundation contact, and is the only model that produces the lean trend from first
    principles.</p>
    <h3>When a measurement is missing for a lean angle</h3>
    <p>A static upright footprint is easy to capture; a leaned one needs a rig almost nobody has.
    So the tool falls back in a fixed order, and labels every patch with the rung it landed on:</p>
    <ol>
      <li><strong>measured</strong> — a footprint at that exact lean angle.</li>
      <li><strong>interpolated</strong> — two measured footprints bracket it; the shapes are morphed.</li>
      <li><strong>transferred</strong> — a measured footprint at another lean angle, rescaled by the
      length, width and centroid trend the model predicts. The measured <em>shape</em> is kept, and only
      the <em>change</em> with lean is modelled — which is the part a model gets right and a
      measurement cannot give you.</li>
      <li><strong>generated</strong> — no measurement anywhere; the parametric model supplies everything.</li>
    </ol>
    <p>That ordering is why importing one upright footprint is worth so much: it upgrades every lean
    angle from <em>generated</em> to <em>transferred</em>.</p>`;
}

/* ------------------------------------------------------------------ */
/* 8. diagnostics                                                      */
/* ------------------------------------------------------------------ */
function drawDiagnostics() {
  $("#flagList").innerHTML = D.flags.map((f) =>
    '<div class="flag sev-' + f.severity + '">' +
      '<div class="bar"></div>' +
      "<div><h3>" + f.title + ' <span class="pill ' + f.severity + '">' + f.severity + "</span></h3>" +
      "<p>" + f.detail + "</p></div>" +
      '<div class="val">' + f.value + "</div>" +
    "</div>").join("");

  const P = D.pitch;
  const prows = [
    ["pitch count", P.n_pitches],
    ["mean length", fmt(P.mean, 2) + " mm"],
    ["min / max", fmt(P.min, 2) + " / " + fmt(P.max, 2) + " mm"],
    ["distinct lengths", P.unique.length],
    ["modulation index", fmt(P.modulation_index, 3)],
    ["largest adjacent step", fmt(P.max_adjacent_ratio, 3) + "×"],
    ["dominant sequence order", P.dominant_sequence_order + " @ " + pct(P.dominant_sequence_amplitude)],
    ["spectral concentration", fmt(P.sequence_tonality, 3) + "  (0.5 ≈ noise-like, 1 = single order)"],
    ["closure error", fmt(P.closure_error_mm, 4) + " mm"],
    ["shoulder phase", fmt(P.shoulder_phase_pitches, 3) + " pitch (" + fmt(P.shoulder_phase_mm, 1) + " mm)"]
  ];
  $("#pitchTable").innerHTML = table(["quantity", "value"], prows.map((r) => [r[0], r[1]]));

  const W = D.wear.per_zone;
  const wrows = Object.keys(W).map((z) => [
    z, fmt(W[z].slenderness_mean, 3), fmt(W[z].slenderness_p90, 3),
    fmt(W[z].sipes_mean, 2), fmt(W[z].area_mean, 0), fmt(W[z].area_min, 0), W[z].n_blocks
  ]);
  $("#wearTable").innerHTML = table(
    ["zone", "slenderness mean", "p90", "mean sipes", "mean area", "min area", "blocks"], wrows) +
    '<p class="hint" style="margin-top:8px">Slenderness is block height divided by its smallest plan dimension; ' +
    "higher means more squirm.</p>";
}

/* ------------------------------------------------------------------ */
/* 8. about                                                            */
/* ------------------------------------------------------------------ */
function drawAbout() {
  const m = META;
  const pv = m.provenance || {};
  $("#aboutBody").innerHTML = `
    <p>Different parts of this analysis rest on very different footings. They are listed
    separately below rather than behind one blanket disclaimer, because treating a verified
    model and a guessed constant the same way helps nobody.</p>

    <h3>Verified against the reference tool</h3>
    <p><strong>Block stiffness</strong> (<code>tread_eval/stiffness.py</code>) is the Okonieski
    et al. (2003) beam-mechanics model with Gent (1959) compression: Castigliano slice integration
    of the draft-tapered section for K<sub>x</sub>/K<sub>y</sub>/K<sub>xy</sub>, and the
    Gent&ndash;Lindley shape factor with bulk correction for K<sub>z</sub>. It is a port of
    <code>effectiveK()</code> and <code>computeKz()</code> from
    <em>Tread Pattern Stiffness Estimation Tool v6.4</em>. The test suite executes that tool's own
    JavaScript and compares both implementations over randomised block geometry &mdash; prismatic,
    drafted, siped, and drafted&#8209;and&#8209;siped &mdash; agreeing to about 1 part in 10<sup>11</sup>.</p>

    <h3>Exact, given their inputs</h3>
    <p>Rasterisation, the FFT &theta;&times;&gamma; sweep, zone binning, order analysis, the
    centroid decomposition and the groove-angle-to-travel geometry involve no fitted constants.
    Feed real geometry in and these numbers are real.</p>

    <h3>${pv.geometry === "measured" ? "Measured" : "Placeholder"} &mdash; tread geometry</h3>
    <p>${pv.geometry === "measured"
        ? "Imported from a tread-plan DXF: block outlines are the drawing's own geometry. " +
          "What the drawing cannot carry is depth &mdash; non-skid depth, draft angle and sipe " +
          "layout were supplied as inputs, and they drive stiffness strongly, so they are worth " +
          "getting right before reading absolute K values."
        : "A generated placeholder pattern. Import a real tread plan with <code>--dxf</code>."}</p>

    <h3>${pv.contact_patch === "measured" ? "Measured" : pv.contact_patch === "partly-measured" ? "Partly measured" : "Modelled"} &mdash; contact patch</h3>
    <p>See the <a href="#tab-patch" data-goto="patch">Contact patch</a> tab for exactly which lean
    angle used which source. The patch can be any closed shape, imported as an outline or a pressure
    map; where no measurement exists it is generated, and where a measurement exists at a different
    lean angle it is rescaled rather than replaced.</p>

    <h3>Assumed &mdash; crown profile</h3>
    <p>${m.crown_measured
        ? "A measured cross-section is in use."
        : "A dual-radius guess, not a measured cross-section. It sets where the contact point sits " +
          "at each lean angle and how much the patch narrows, so it matters as much as the patch " +
          "model itself. Supply a measured section to remove this assumption."}</p>

    <h3>Uncalibrated &mdash; flag thresholds</h3>
    <p>The pass/watch/flag limits are engineering guesses, not correlated against measured noise or
    wear. The <em>ranking</em> between two designs is far more trustworthy than any single verdict.</p>

    <h3>Curvature correction</h3>
    <p>Currently ${m.curvature_correction ? "ON" : "OFF"}. Leave it off for mould DXFs, which are
    already developed correctly; turn it on for competitor patterns digitised flat, where the
    shoulder is compressed by the projection.</p>`;

  const rows = [
    ["pattern", m.name + " (" + m.source + ")"],
    ["circumference", fmt(C, 2) + " mm"],
    ["tread width", fmt(m.tread_width, 2) + " mm"],
    ["blocks / pitches", m.n_blocks + " / " + m.n_pitches],
    ["raster", m.grid[1] + " × " + m.grid[0] + " px at " + fmt(m.resolution_mm, 3) + " mm"],
    ["lean angles", D.leans.map((l) => fmt(l.gamma_deg, 0) + "°").join(", ")],
    ["max lean the crown supports", fmt(m.max_supported_lean, 1) + "°"],
    ["curvature correction", m.curvature_correction ? "ON" : "off"],
    ["vertical load (upright)", fmt(m.cp_params.vertical_load_N, 0) + " N"],
    ["load rises with lean", m.cp_params.load_rises_with_lean ? "yes (Fz/cosγ)" : "no"],
    ["foundation modulus", fmt(m.cp_params.foundation_modulus, 3) + " N/mm³"],
    ["wheel radius", fmt(m.cp_params.wheel_radius_mm, 0) + " mm"],
    ["shear modulus G", fmt(m.stiffness_params.shear_modulus_MPa, 2) + " MPa"],
    ["Young's modulus E", fmt(m.stiffness_params.youngs_modulus_MPa, 2) + " MPa"],
    ["generated", m.generated]
  ];
  if (m.notes) rows.push(["notes", m.notes]);
  $("#configTable").innerHTML = table(["setting", "value"], rows);
}

/* ------------------------------------------------------------------ */
/* update orchestration                                                */
/* ------------------------------------------------------------------ */
function onThetaChange() {
  $("#thetaReadout").textContent =
    fmt(state.theta, 1) + "°  (" + fmt(thetaMM(), 1) + " mm)  ·  area " +
    fmt(sampleAt(curLean(), "contact_area", state.theta), 0) + " mm²";
  updatePattern();
  const cur = [cursorShape()];
  Plotly.relayout("overviewPlot", { shapes: cur.concat(pitchTicks()) });
  if (state.tab === "contact") {
    ["areaPlot:area", "kxPlot:kx", "kyPlot:ky", "anisoPlot:", "blockPlot:"].forEach((s) => {
      const [div, kind] = s.split(":");
      Plotly.relayout(div, { shapes: (kind ? anomalyShapes(kind) : []).concat(cur) });
    });
  }
}

function onLeanChange() {
  drawCrown();
  renderPatchStats();
  updatePattern();
  drawOverview();
  drawContactTab();
  drawOrders();
  drawGrooves();
  drawPatchTab();
  onThetaChange();
}

function init() {
  readTheme();
  renderHeader();
  renderBanner();
  renderTabs();
  renderTheme();
  renderControls();
  drawPattern();
  drawCrown();
  renderPatchStats();
  drawOverview();
  drawContactTab();
  drawOrders();
  drawLeanTab();
  drawZones();
  drawGrooves();
  drawPatchTab();
  drawDiagnostics();
  drawAbout();
  showTab("pattern");
  window.__treadReady = true;
  onThetaChange();
  window.addEventListener("resize", () => {
    document.querySelectorAll(".plot").forEach((el) => { if (el.data) Plotly.Plots.resize(el); });
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
})();
