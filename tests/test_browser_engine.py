"""The standalone browser app: engine parity, self-tests, and a clean build.

The browser tool ships its own JavaScript port of the pipeline (``app/engine.js``).
These tests keep that port honest:

* ``app/selftest.js`` cross-checks the JS stiffness against the verbatim v6.4
  reference and the JS DXF importer against the known Tramplr geometry;
* the sweep is compared directly against the verified Python sweep on the same
  pattern and grid, so the aggregate curves the page draws match Python to a
  tight tolerance;
* the built ``tread_tool.html`` is verified to be genuinely self-contained
  (no external script/style/font/image URLs) and within the size budget.

Node is required; the tests skip cleanly if it is not on the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "app")
DXF = os.path.join(REPO, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")


def _node() -> str:
    for cand in ("/opt/node22/bin/node", "node"):
        if shutil.which(cand) or os.path.exists(cand):
            return cand
    pytest.skip("node not available")


def test_engine_selftest_passes():
    """JS stiffness == reference, FFT correlation == brute force, DXF == known geometry."""
    node = _node()
    proc = subprocess.run([node, os.path.join(APP, "selftest.js")], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "checks passed" in proc.stdout


def _python_sweep(pattern, nx, ny, spec_dict, stiff, cp_params, gamma, stride):
    from tread_eval.raster import Grid, rasterise
    from tread_eval.stiffness import StiffnessParams
    from tread_eval.cp_shapes import ShapeSpec, shape_patch
    from tread_eval.contact_patch import CPParams
    from tread_eval.sweep import sweep_lean

    grid = Grid(nx=nx, ny=ny, dx=pattern.tyre_circumference / nx, dy=pattern.tread_width / ny,
                circumference=pattern.tyre_circumference, tread_width=pattern.tread_width)
    sp = StiffnessParams(shore_a=stiff["shore_a"], poisson=stiff["poisson"], mode=stiff["mode"])
    pack = rasterise(pattern, grid, stiffness_params=sp)
    spec = ShapeSpec(**{**spec_dict, "gamma_deg": gamma})
    cpp = CPParams(vertical_load=cp_params["vertical_load"], wheel_radius=cp_params["wheel_radius"],
                   load_rises_with_lean=cp_params["load_rises_with_lean"])
    patch = shape_patch(spec, pattern.crown(), pattern.tread_width, cpp)
    res = sweep_lean(pattern, pack, gamma, patch_override=patch)
    s = slice(None, None, stride)
    return {
        "contact_area": res.contact_area[s], "kx": res.kx[s], "ky": res.ky[s], "kz": res.kz[s],
        "block_count": res.block_count[s], "patch_area": res.patch_area,
    }


def test_js_python_sweep_parity():
    """The JS sweep reproduces the verified Python sweep on the same pattern + grid."""
    node = _node()
    from tread_eval.dxf import load_pattern, BlockDefaults

    pattern, _ = load_pattern(DXF, BlockDefaults(height=8.5, draft_angle=3.0))
    nx, ny = 512, 64  # power-of-two nx for the radix-2 JS FFT
    spec = {"shape": "rounded", "length": 90.0, "width": 50.0, "corner_radius": 12.0}
    stiff = {"shore_a": 60.0, "poisson": 0.49, "mode": "parallel", "bulk_modulus": 1100, "n_slices": 40}
    cp = {"vertical_load": 1500.0, "wheel_radius": 320.0, "load_rises_with_lean": True}
    gamma, stride = 0.0, 8

    py = _python_sweep(pattern, nx, ny, spec, stiff, cp, gamma, stride)

    req = {"pattern": pattern.to_dict(), "nx": nx, "ny": ny, "spec": spec, "stiffParams": stiff,
           "cpParams": cp, "gamma": gamma, "stride": stride, "discreteSamples": 60}
    proc = subprocess.run([node, os.path.join(APP, "parity.js")], input=json.dumps(req),
                          capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)

    # patch geometry must agree closely (both build the same rounded rect)
    assert abs(js["patch_area"] - py["patch_area"]) / py["patch_area"] < 1e-3

    for key in ("contact_area", "kx", "ky", "kz", "block_count"):
        a = np.asarray(js[key], dtype=float)
        b = np.asarray(py[key], dtype=float)
        assert a.shape == b.shape, f"{key} length mismatch {a.shape} vs {b.shape}"
        scale = float(np.max(np.abs(b))) or 1.0
        max_err = float(np.max(np.abs(a - b))) / scale
        assert max_err < 2e-3, f"{key} parity: max relative error {max_err:.2e}"


def test_order_spectrum_definition_matches_python():
    """Both engines must report the SAME quantity under the label 'amplitude'.

    The JS used to return absolute amplitude while Python returns a fraction of
    the mean, so the browser's order chart and the report's order chart showed
    different numbers on identically-labelled axes.
    """
    node = _node()
    from tread_eval.metrics import order_spectrum

    n, mean, amp, order = 2048, 50.0, 2.0, 12
    sig = mean + amp * np.sin(2 * np.pi * order * np.arange(n) / n)

    py = order_spectrum(sig, max_order=40)
    script = (
        "const E=require('./app/engine.js');"
        f"const n={n},mean={mean},amp={amp},order={order};"
        "const s=new Float64Array(n);"
        "for(let i=0;i<n;i++)s[i]=mean+amp*Math.sin(2*Math.PI*order*i/n);"
        "process.stdout.write(JSON.stringify(E.orderSpectrum(s,40)));"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)

    assert js["orders"] == [float(o) for o in py["orders"]] or js["orders"] == list(py["orders"])
    a_js = np.asarray(js["amplitude"], dtype=float)
    a_py = np.asarray(py["amplitude"], dtype=float)
    assert np.max(np.abs(a_js - a_py)) < 1e-9, "order spectra disagree between engines"
    # and the value is the documented one: a fraction of the mean
    k = list(py["orders"]).index(order)
    assert a_py[k] == pytest.approx(amp / mean, rel=1e-9)
    assert a_js[k] == pytest.approx(amp / mean, rel=1e-9)


def test_build_is_self_contained():
    """The built page must embed everything -- no external script/style/font/image URLs."""
    import build_app

    with tempfile.TemporaryDirectory() as d:
        out = build_app.build(os.path.join(d, "tread_tool.html"))
        html = open(out, encoding="utf-8").read()
        size = os.path.getsize(out)

    assert size < 16_000_000, f"page is {size / 1e6:.1f} MB, over the 16 MB budget"
    # No network dependencies: check actual HTML *tags* rather than raw
    # substrings -- the embedded Plotly library contains http URL string
    # literals (its unused mapbox feature) that are never fetched.  What must
    # never appear is a tag that pulls an external resource on load.
    import re

    external_tag = re.compile(
        r"<(?:script|link|img|iframe|source)\b[^>]*\b(?:src|href)\s*=\s*[\"']?\s*(?:https?:)?//",
        re.IGNORECASE,
    )
    m = external_tag.search(html)
    assert m is None, f"page references an external resource in markup: {html[m.start():m.start()+80]!r}"
    # the stylesheet is inline, and any @import must not reach out to the network
    assert "@import url(http" not in html and "@import url('http" not in html and '@import url("http' not in html
    # key pieces are present
    assert "TreadEngine" in html
    assert 'id="engine-src"' in html and 'id="worker-src"' in html
    assert "Plotly" in html
    assert "How to read this report" in html  # the guide is embedded
    assert "SECTION" in html and "ENTITIES" in html  # the sample DXF is embedded
    assert "loadPattern" in html and "sweepLean" in html


def test_no_dead_data_in_the_worker_to_ui_pipeline():
    """Anything the worker computes and ships must be rendered or exported.

    The discrete block count cost 54% of the sweep and was never displayed; the
    pressure-weighted area was a whole extra kernel transform per lean whose
    result was dropped on the floor.  This test keeps the pipeline honest.
    """
    worker = open(os.path.join(APP, "worker.js"), encoding="utf-8").read()
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()

    shipped = ["contact_area", "land_ratio", "kx", "ky", "kz", "block_count",
               "centroid_y", "zone_area", "block_count_discrete", "theta_discrete",
               "theta_deg", "patch_area", "shape"]
    for field in shipped:
        assert field in worker, f"{field} is expected to be shipped by the worker"
        assert field in ui, f"{field} is shipped but never read by the UI -- dead transport"

    # and the redundant correlation must stay gone from the JS engine
    engine = open(os.path.join(APP, "engine.js"), encoding="utf-8").read()
    assert "pressure_area" not in engine, "the pressure-weighted correlation is redundant for a flat patch"


def test_no_orphan_control_ids_in_the_page():
    """Every id the UI reads must exist in the template, and every input in the
    template must be read.  A hidden input nobody can reach used to drive the
    load model while the visible checkbox beside it did something else."""
    import re

    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()

    template_ids = set(re.findall(r'<(?:input|select|button|canvas)[^>]*\bid="([^"]+)"', tpl))
    # ids reach the UI three ways: $("x"), the num("x") helper, and the
    # id lists used to attach change handlers.
    read_ids = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', ui))
    read_ids |= set(re.findall(r'\bnum\("([A-Za-z0-9_]+)"\)', ui))
    read_ids |= set(re.findall(r'"([A-Za-z0-9_]+)"', ui)) & template_ids

    missing = {i for i in read_ids if i not in template_ids and f'id="{i}"' not in tpl}
    assert not missing, f"UI reads ids that the template does not define: {sorted(missing)}"

    interactive = {i for i in template_ids
                   if re.search(rf'<(?:input|select)[^>]*\bid="{re.escape(i)}"', tpl)}
    unread = {i for i in interactive if i not in read_ids}
    assert not unread, f"template defines inputs nothing reads: {sorted(unread)}"


def test_load_checkbox_drives_the_load_model():
    """The visible box is labelled 'load rises with lean'; it must actually set
    load_rises_with_lean, not a hidden twin."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'load_rises_with_lean: $("cpAutoLoad").checked' in ui
    assert 'id="loadLean"' not in tpl, "the hidden twin input is gone"
    assert "loadLean" not in ui


def test_export_paths_exist_and_are_wired():
    """The pipeline must not end at the screen."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    for fn in ("exportCSV", "exportJSON", "exportSummary"):
        assert f"function {fn}(" in ui, f"{fn} is missing"
    for btn in ("exportCsv", "exportJson", "exportTxt"):
        assert f'id="{btn}"' in tpl, f"{btn} button missing from the template"
        assert f'on($("{btn}"), "click"' in ui, f"{btn} has no click handler"
    # the CSV must carry the settings that produced it
    assert "# Tread pattern evaluation" in ui
    assert "gamma_deg" in ui and "centroid_y_mm" in ui
    # exports must be gated on having results
    assert "refreshExportButtons" in ui


def test_pattern_derived_inputs_are_reconciled_before_every_run():
    """The crown and the pitch division are derived from inputs, not from the
    DXF.  They used to be baked in at import, so typing a new crown radius and
    pressing Run recomputed the whole lean sweep against the *old* crown -- and
    the crown decides where the contact point sits at every lean angle."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    assert "function reconcilePattern(" in ui
    assert "rebuildIfLoaded" not in ui, "the old depth-only rebuild must be gone"
    # run() must reconcile before dispatching to the worker
    run_body = ui[ui.index("function run()"):ui.index("function populateGammaSelect")]
    assert "reconcilePattern();" in run_body
    assert run_body.index("reconcilePattern();") < run_body.index("postMessage")
    # and it must rebuild all three derived things
    body = ui[ui.index("function reconcilePattern("):ui.index("// ---- banner")]
    assert "buildCrown" in body, "crown is not rebuilt"
    # every crown input has to reach it, including the multi-arc spec -- a crown
    # rebuilt from only some of them is the same bug in a smaller form
    for field in ("crown_arcs", "crown_r_center", "crown_r_shoulder", "crown_break"):
        assert field in body, f"{field} does not reach the rebuilt crown"
    assert "estimatePitchCount" in body or "nPitches" in body, "pitch division is not rebuilt"
    assert "b.height" in body, "block depths are not reapplied"


def test_every_input_carries_an_explanation():
    """The brief: a new user must complete the workflow unaided.  Coverage was
    4/26 -- most controls had no explanation of what they were or what they
    affected, and several (crown radius, sipe depth) are not guessable."""
    import re

    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    sidebar = tpl[tpl.index('<aside class="sidebar">'):tpl.index("</aside>")]
    fields = re.findall(r'<div class="field"([^>]*)>(.*?)(?=<div class="field"|</div>\s*</div>)',
                        sidebar, re.S)
    missing = []
    for attrs, body in fields:
        m = re.search(r'id="([A-Za-z0-9_]+)"', body)
        if not m:
            continue
        title = re.search(r'title="([^"]{10,})"', attrs)
        if not title:
            missing.append(m.group(1))
    assert not missing, f"inputs with no tooltip: {missing}"


def test_axis_titles_carry_units():
    """Every plotted axis must name its quantity and its unit."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    for expected in [
        "rotation angle θ (deg)", "lean angle γ (deg)", "lateral y (mm)",
        "contact area (mm²)", "circumferential x (mm)",
        "order (events per revolution)", "amplitude (fraction of mean)",
    ]:
        assert expected in ui, f"axis title missing or missing its unit: {expected!r}"


def test_results_chrome_is_hidden_until_there_are_results():
    """Tabs and the lean selector describe results.  Shown before a run they
    lead to blank panels, which reads as a broken page rather than an empty one."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'id="resultsArea"' in tpl
    assert 'id="resultsArea" style="display:none"' in tpl, "must start hidden"
    assert "function showResultsChrome(" in ui
    # hidden again whenever results are invalidated
    assert ui.count("showResultsChrome(false)") >= 2
    # the editor must be painted at startup so its empty-state hint is seen
    init = ui[ui.index("function init()"):]
    assert "drawEditor();" in init


def test_first_run_gives_numbered_instructions():
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    hint = tpl[tpl.index('id="emptyHint"'):tpl.index('id="banner"')]
    assert "Start here" in hint
    assert hint.count("<li>") >= 4, "the workflow should be spelled out as steps"
    for word in ("Load", "Run"):
        assert word in hint


def test_exported_settings_are_frozen_at_run_time():
    """An exported file must describe the inputs that actually produced its
    numbers.  Read live, the settings block described whatever was in the boxes
    at export time: edit NSD after a run and the file claimed 4.0 mm had
    produced results computed at 8.5 mm -- which makes the self-describing
    header worse than no header at all."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    assert "function captureInputs(" in ui
    # frozen when the run is dispatched, before the worker is handed anything
    run_body = ui[ui.index("function run()"):ui.index("function populateGammaSelect")]
    assert "state.ranInputs = captureInputs();" in run_body
    assert run_body.index("state.ranInputs") < run_body.index("postMessage")
    # and the exports read the frozen copy, not the live controls
    snap = ui[ui.index("function settingsSnapshot("):ui.index("function exportCSV(")]
    assert "state.ranInputs" in snap
    for live in ("readDefaults()", "readStiffParams()", "readSpec()", "readCpParams()"):
        assert live not in snap, f"settingsSnapshot still reads {live} live"


# --- tyre class, divisions, comparison, PDF ---------------------------
def test_tyre_class_changes_the_physics_not_just_the_labels():
    """A truck crown is flat almost to the edge; a motorcycle crown is curved
    from the centreline out.  The break fraction sets the tread tangent at every
    lateral position, so it decides the contact point at each lean and the
    maximum lean the tyre can reach at all -- running one class on another's
    profile is a real error, not a rounding one."""
    node = _node()
    script = (
        "const E=require('./app/engine.js');"
        "const out={};"
        "for (const t of ['2w','pcr','tbr']) {"
        "  const c=E.tyreClass(t);"
        "  const crown=E.crownDualRadius(250, t==='2w'?125:(t==='pcr'?700:1500),"
        "                                     t==='2w'?55:(t==='pcr'?90:120), c.crown_break);"
        "  out[t]={brk:c.crown_break, zc:c.zone_center, zi:c.zone_intermediate,"
        "          rise:c.load_rises_with_lean, maxLean:E.maxSupportedLean(crown)};"
        "}"
        "process.stdout.write(JSON.stringify(out));"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)

    # the crown gets progressively flatter from 2W to TBR
    assert r["2w"]["brk"] < r["pcr"]["brk"] < r["tbr"]["brk"]
    # which shows up as a steadily smaller reachable lean
    assert r["2w"]["maxLean"] > r["pcr"]["maxLean"] > r["tbr"]["maxLean"]
    assert r["2w"]["maxLean"] > 25, "a motorcycle must reach a real lean angle"
    assert r["tbr"]["maxLean"] < 12, "a truck barely cambers"
    # only the motorcycle carries the Fz/cos(gamma) load rise
    assert r["2w"]["rise"] is True
    assert r["pcr"]["rise"] is False and r["tbr"]["rise"] is False
    # shoulder is a smaller share of the half width as the tyre gets bigger
    assert r["2w"]["zi"] < r["pcr"]["zi"] < r["tbr"]["zi"]


def test_bands_partition_the_total_exactly():
    """Bands are a restriction of the same correlation, not a second
    calculation, so they must sum to the whole-tread answer bit for bit."""
    node = _node()
    script = (
        "const fs=require('fs'),E=require('./app/engine.js');"
        "const {pattern}=E.loadPattern(fs.readFileSync('" + DXF + "','utf8'),{height:8.5,draft_angle:3},{});"
        "const g=E.makeGrid(pattern,1024,74);"
        "const pack=E.rasterise(pattern,g,{shore_a:60,poisson:0.49,mode:'parallel'},false);"
        "const edges=E.evenBandEdges(pattern.tread_width,5);"
        "const r=E.sweepLean(pattern,pack,0,{shape:'rounded',length:90,width:50,corner_radius:12},"
        "  {vertical_load:1500,wheel_radius:320,load_rises_with_lean:true},null,60,edges);"
        "const sum=k=>r.bands.reduce((a,b)=>a+E.fluctuationStats(b[k]).mean,0);"
        "process.stdout.write(JSON.stringify({n:r.bands.length,"
        "  area:[sum('contact_area'),E.fluctuationStats(r.contact_area).mean],"
        "  kz:[sum('kz'),E.fluctuationStats(r.kz).mean],"
        "  kx:[sum('kx'),E.fluctuationStats(r.kx).mean]}));"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert r["n"] == 5
    for key in ("area", "kz", "kx"):
        band_sum, total = r[key]
        assert abs(band_sum - total) < 1e-6 * max(abs(total), 1.0), f"{key}: {band_sum} vs {total}"


def test_band_edges_are_validated():
    node = _node()
    script = (
        "const E=require('./app/engine.js');const o={};"
        "const t=(k,f)=>{try{f();o[k]=null}catch(e){o[k]=e.message}};"
        "t('outside',()=>E.validateBandEdges([-100,0,100],159));"
        "t('unsorted',()=>E.validateBandEdges([-79.5,10,-10,79.5],159));"
        "t('single',()=>E.validateBandEdges([0],159));"
        "t('toomany',()=>E.validateBandEdges(Array.from({length:40},(_,i)=>-79.5+i*4),159));"
        "t('ok',()=>E.validateBandEdges([-79.5,-20,20,79.5],159));"
        "process.stdout.write(JSON.stringify(o));"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert "outside the tread" in (r["outside"] or "")
    assert "must increase" in (r["unsorted"] or "")
    assert "at least two" in (r["single"] or "")
    assert "at most 24" in (r["toomany"] or "")
    assert r["ok"] is None


def test_in_session_comparison_needs_no_files():
    """Comparing two designs must not require exporting and re-importing."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'id="addCompareSide"' in tpl, "the in-session add button must sit beside Run"
    assert "function addCurrentToComparison(" in ui
    assert "state.compare.push(" in ui
    assert 'on($("addCompareSide"), "click"' in ui
    # runs are removable one by one, and the count is visible
    assert "cmp-del" in ui and 'id="cmpCount"' in tpl
    # loading JSON stays available, but is not the only route
    assert "function loadComparisonFiles(" in ui


def test_pdf_report_is_available_offline_and_marked_internal():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert "function exportPDF(" in ui
    assert 'id="exportPdf"' in tpl and 'on($("exportPdf"), "click"' in ui
    assert "INTERNAL USE ONLY" in ui and "Apollo" in ui
    # project details and the creation date must reach the cover
    for field in ("Project", "Tread / design", "Tyre type", "Designer", "Created"):
        assert field in ui, f"PDF cover is missing {field}"
    # jsPDF is vendored, not fetched
    assert os.path.exists(os.path.join(APP, "vendor_jspdf.js"))
    import build_app
    with tempfile.TemporaryDirectory() as d:
        html = open(build_app.build(os.path.join(d, "t.html")), encoding="utf-8").read()
    assert "jspdf" in html.lower(), "jsPDF must be inlined into the single file"


def test_project_metadata_reaches_every_export():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    assert "function projectMeta(" in ui
    snap = ui[ui.index("function settingsSnapshot("):ui.index("function exportCSV(")]
    assert "project: base.project" in snap, "project details must be in the frozen snapshot"


def test_theta_figures_share_one_zoom():
    """The sweep rows and the rolled-out pattern show the same revolution, so
    zooming one must zoom all of them -- otherwise a zoomed row cannot be read
    against the blocks underneath it."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    # subplot axes linked inside the stack figure
    assert 'matches: i === 0 ? undefined : "x"' in ui
    # the strip is a separate figure, so it is synced explicitly both ways
    assert "function linkThetaFigures(" in ui
    assert "syncFrom(stack, strip);" in ui and "syncFrom(strip, stack);" in ui
    assert 'plotly_relayout' in ui
    # a double-click autorange must reset both, not just the one clicked
    assert 'ev["xaxis.autorange"]' in ui
    # both figures start from the same stored range
    assert ui.count("state.thetaRange || [0, 360]") >= 2
    # and their plot areas are aligned, or the same angle lands at a different
    # screen x in each figure and the crosshair appears to jump
    assert "margin: { l: 78, r: 16, t: 24, b: 40 }" in ui, "strip margin must match the stack's l=78"


def test_crosshair_tracks_across_every_chart():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    css = open(os.path.join(APP, "style.css"), encoding="utf-8").read()
    assert "function moveCursor(" in ui and "function updateCursorReadout(" in ui
    # each figure needs its OWN positioned host, or the two cursor divs collide
    assert tpl.count('class="plot-host"') >= 2
    assert ".plot-host { position: relative; }" in css
    assert ".xcursor" in css
    # the readout must be shown with a real display type: the CSS default is
    # none, so clearing the inline style leaves it hidden
    assert 'box.style.display = "flex";' in ui
    assert 'id="cursorReadout"' in tpl
    # driven from the pointer, not from plotly_hover, so it tracks continuously
    assert 'addEventListener("mousemove"' in ui and 'addEventListener("mouseleave"' in ui


def test_order_chart_explains_itself():
    """The designer could not tell what the order chart showed."""
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    panel = tpl[tpl.index('id="panel-orders"'):tpl.index('id="panel-zones"')]
    assert 'class="explain"' in panel
    body = panel.lower()
    # it must define the axes in plain words, not restate their labels
    assert "how many times per wheel revolution" in body
    assert "percentage of the average" in body
    # and say what to look for
    for cue in ("tonal", "spread", "pitch sequence"):
        assert cue in body, f"the explanation never mentions {cue!r}"
    # the marked bars must be explained
    assert "pitch count" in body and "geometric repeat" in body
    assert panel.count("<li>") >= 6


# ---------------------------------------------------------------------------
# DXF import robustness
# ---------------------------------------------------------------------------
# The importer used to work on some drawings and not others for reasons that had
# nothing to do with the tread: a stray blank line ahead of the first group code,
# an outline drawn as an old-style POLYLINE, a corner left 5 um open by a trim,
# or simply the order the entities happened to sit in the file.  Each case below
# is one of those, reduced to the smallest drawing that shows it.

TIEBAR_DXF = os.path.join(REPO, "data", "tbr_ribs_tiebars.dxf")


def _dxf(body: list[str]) -> str:
    return "\n".join(["0", "SECTION", "2", "ENTITIES"] + body + ["0", "ENDSEC", "0", "EOF"]) + "\n"


def _line(x1: float, y1: float, x2: float, y2: float) -> list[str]:
    return ["0", "LINE", "8", "0", "10", repr(float(x1)), "20", repr(float(y1)),
            "11", repr(float(x2)), "21", repr(float(y2))]


def _rect(x0: float, y0: float, w: float, h: float) -> list[str]:
    return (_line(x0, y0, x0 + w, y0) + _line(x0 + w, y0, x0 + w, y0 + h)
            + _line(x0 + w, y0 + h, x0, y0 + h) + _line(x0, y0 + h, x0, y0))


def _js_import(node: str, text: str, opts: str = "{min_block_area:0.5}") -> dict:
    """Run the browser engine's loadPattern on ``text`` and return a digest."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "case.dxf")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const r = E.loadPattern(fs.readFileSync({json.dumps(p)}, 'utf8'), {{height: 8}}, {opts});
process.stdout.write(JSON.stringify({{
  blocks: r.report.n_blocks,
  tiebars: r.report.n_tiebars,
  areas: r.pattern.blocks.map(b => E.polygonArea(b.polygon)).sort((a, b) => a - b),
  tiebar_areas: r.pattern.tiebars.map(t => t.area),
  warnings: r.report.warnings,
  unsupported: r.report.unsupported_types,
}}));
"""
        res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stderr[:2000]
        return json.loads(res.stdout)


def test_group_code_alignment_survives_a_leading_blank_line():
    """A DXF group is two lines: an integer code, then its value.

    Assuming line 0 of the file is the first code meant that one stray blank
    line -- or a UTF-8 BOM -- shifted every pair by one, the codes became values,
    and a perfectly good drawing imported as "no usable entities".
    """
    node = _node()
    good = _dxf(_rect(0, 0, 20, 10) + _rect(30, 0, 20, 10))
    for label, text in [
        ("clean", good),
        ("leading blank line", "\n" + good),
        ("BOM", "﻿" + good),
        ("CRLF", good.replace("\n", "\r\n")),
    ]:
        got = _js_import(node, text)
        assert got["blocks"] == 2, f"{label}: {got}"
        assert got["areas"] == pytest.approx([200.0, 200.0]), label


def test_old_style_polyline_vertices_are_read():
    """POLYLINE keeps its geometry in the VERTEX entities that follow it.

    Reading 10/20 off the POLYLINE record itself yielded a single dummy point at
    the origin, which was then dropped with no message -- the block simply was
    not there.
    """
    node = _node()
    pl = ["0", "POLYLINE", "8", "0", "66", "1", "70", "1", "10", "0.0", "20", "0.0"]
    for x, y in [(0, 0), (20, 0), (20, 10), (0, 10)]:
        pl += ["0", "VERTEX", "8", "0", "10", repr(float(x)), "20", repr(float(y))]
    pl += ["0", "SEQEND", "8", "0"]
    got = _js_import(node, _dxf(pl + _rect(30, 0, 20, 10)))
    assert got["blocks"] == 2, got
    assert got["areas"] == pytest.approx([200.0, 200.0])


def test_lwpolyline_bulges_become_arcs():
    """A bulge is an arc, and chording it away changes the plan area.

    The 42 code only appears on vertices that actually bulge, so it cannot be
    paired with the 10/20 list positionally -- the groups have to be read in file
    order.  Here one 90 deg bulge adds a circular segment of exactly
    r^2/2*(theta - sin theta) = 100*(pi/2 - 1) = 57.08 mm^2 to a 200 mm^2
    rectangle; the flattened polygon is inscribed, so it lands just under that.
    """
    node = _node()
    lw = ["0", "LWPOLYLINE", "8", "0", "90", "4", "70", "1",
          "10", "0.0", "20", "0.0", "42", "0.4142135624",
          "10", "20.0", "20", "0.0",
          "10", "20.0", "20", "10.0",
          "10", "0.0", "20", "10.0"]
    got = _js_import(node, _dxf(lw + _rect(30, 0, 20, 10)))
    assert got["blocks"] == 2, got
    exact = 200.0 + 100.0 * (np.pi / 2 - 1)
    assert got["areas"][1] == pytest.approx(exact, rel=2e-3)
    assert got["areas"][1] < exact, "an inscribed flattening cannot exceed the true arc"


def test_insert_references_are_expanded():
    """A tread drawn once and arrayed with INSERT used to import as nothing."""
    node = _node()
    blocks = (["0", "SECTION", "2", "BLOCKS", "0", "BLOCK", "2", "BLK",
               "10", "0.0", "20", "0.0", "70", "0"] + _rect(0, 0, 20, 10)
              + ["0", "ENDBLK", "0", "ENDSEC"])
    ents = ["0", "SECTION", "2", "ENTITIES",
            "0", "INSERT", "2", "BLK", "10", "0.0", "20", "0.0",
            "0", "INSERT", "2", "BLK", "10", "30.0", "20", "0.0",
            "0", "ENDSEC", "0", "EOF"]
    got = _js_import(node, "\n".join(blocks + ents) + "\n")
    assert got["blocks"] == 2, got
    assert got["areas"] == pytest.approx([200.0, 200.0])
    assert any("INSERT" in w for w in got["warnings"])


def test_a_five_micron_corner_gap_no_longer_loses_the_block():
    """CAD exports routinely leave corners a few microns open after a trim.

    At the old 1e-3 mm weld tolerance a 5 um gap dropped the whole outline with
    only a "chain did not close" line to show for it.
    """
    node = _node()
    body = (_line(0, 0, 20, 0) + _line(20, 0, 20, 10) + _line(20, 10, 0, 10)
            + _line(0, 10.005, 0, 0) + _rect(30, 0, 20, 10))
    got = _js_import(node, _dxf(body))
    assert got["blocks"] == 2, got
    # ...and a gap far larger than any rounding error is still refused, loudly.
    body_wide = (_line(0, 0, 20, 0) + _line(20, 0, 20, 10) + _line(20, 10, 0, 10)
                 + _line(0, 10.5, 0, 0) + _rect(30, 0, 20, 10))
    wide = _js_import(node, _dxf(body_wide))
    assert wide["blocks"] == 1
    assert any("did not close" in w for w in wide["warnings"])


def test_loop_finding_does_not_depend_on_entity_order():
    """Two ribs abutting on a shared wall.

    The old greedy walker took whichever attached segment happened to be unused
    first, so at the shared wall it either traced the two 200 mm^2 blocks or ran
    straight through into one merged 400 mm^2 blob -- decided purely by the order
    the entities sat in the file.  Three of six shuffles gave the wrong answer.
    """
    node = _node()
    import itertools
    ents = [_line(0, 0, 20, 0), _line(20, 0, 20, 10), _line(20, 10, 0, 10), _line(0, 10, 0, 0),
            _line(20, 0, 40, 0), _line(40, 0, 40, 10), _line(40, 10, 20, 10), _line(20, 10, 20, 0)]
    seen = set()
    for perm in itertools.islice(itertools.permutations(range(8)), 0, 5040, 700):
        body = [tok for i in perm for tok in ents[i]]
        got = _js_import(node, _dxf(body))
        seen.add((got["blocks"], tuple(round(a, 6) for a in got["areas"])))
    assert seen == {(2, (200.0, 200.0))}, f"entity order changed the result: {seen}"


def test_unreadable_entity_types_are_reported_not_dropped():
    """A drawing whose outlines are 3DSOLIDs is not an empty tyre."""
    node = _node()
    body = _rect(0, 0, 20, 10) + _rect(30, 0, 20, 10) + ["0", "3DSOLID", "8", "0", "1", "junk"]
    got = _js_import(node, _dxf(body))
    assert got["unsupported"].get("3DSOLID") == 1
    assert any("3DSOLID" in w for w in got["warnings"])


def test_the_real_drawing_is_unchanged_by_the_new_loop_finder():
    """168 blocks, 3 seam-wrapped, land ratio 0.690057 -- exactly as before.

    Every outline in the Tramplr plan is drawn as its own closed loop with no
    shared edges, so the arrangement has nothing to disambiguate and must return
    the same geometry the greedy walker did.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const r = E.loadPattern(fs.readFileSync({json.dumps(DXF)}, 'latin1'), {{height: 8.5, draft_angle: 3}}, {{}});
process.stdout.write(JSON.stringify({{n: r.report.n_blocks, w: r.report.n_wrapped,
  land: r.report.land_ratio, tb: r.report.n_tiebars}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stderr[:2000]
    got = json.loads(res.stdout)
    assert got["n"] == 168 and got["w"] == 3
    assert got["land"] == pytest.approx(0.690057, abs=1e-6)
    assert got["tb"] == 0, "no outline in this drawing shares an edge, so there are no tie bars"


# ---------------------------------------------------------------------------
# tie bars
# ---------------------------------------------------------------------------


def test_tie_bars_are_found_between_blocks_without_outlines_of_their_own():
    """A tie bar's long sides ARE the neighbouring blocks' groove walls.

    There is no closed outline to find, which is why chain-walking could never
    see one.  As a face of the planar arrangement it falls out directly: the rib
    drawing has 4 ribs x 20 blocks and a bar bridging each of the 19 lateral
    grooves in the two centre ribs.
    """
    node = _node()
    with open(TIEBAR_DXF, encoding="utf-8") as fh:
        got = _js_import(node, fh.read(), opts="{}")
    assert got["blocks"] == 80, got
    assert got["tiebars"] == 38, got
    # each bar is the 6 mm groove width by the 14 mm bar width
    assert got["tiebar_areas"] == pytest.approx([84.0] * 38, abs=1e-6)
    assert any("tie bar" in w for w in got["warnings"])


def test_a_tie_bar_carries_nothing_until_the_tread_has_worn_down_to_it():
    """Groove bottom at z = 0; the block top is at NSD - w, the bar top at h.

    So the bar reaches the road only once w >= NSD - h.  Below that it must be
    absent from the contact model entirely -- a sub-surface bar cannot touch the
    road, and quietly including it would overstate the area of every new tyre.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const {{pattern}} = E.loadPattern(fs.readFileSync({json.dumps(TIEBAR_DXF)}, 'utf8'), {{height: 16}}, {{}});
const out = [10, 16, 20].map(nsd => {{
  pattern.tiebars.forEach(t => {{ t.nsd = nsd; t.height = 0.55 * nsd; }});
  pattern.blocks.forEach(b => {{ b.height = nsd; }});
  const at = E.tiebarEngagementWear(pattern.tiebars[0]);
  return [nsd, at,
          E.effectiveBlocks(pattern, at - 0.01).length,
          E.effectiveBlocks(pattern, at + 0.01).length];
}});
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    for nsd, at, below, above in json.loads(res.stdout):
        assert at == pytest.approx(0.45 * nsd), f"engagement wear should be NSD - h at NSD {nsd}"
        assert below == 80, f"a bar below the surface must not be in the block list (NSD {nsd})"
        assert above == 118, f"all 38 bars must join once worn past (NSD {nsd})"


def test_engaging_the_tie_bars_adds_land_and_damps_the_fluctuation():
    """What a tie bar is actually for.

    Bridging the lateral grooves ties consecutive blocks together, so the
    circumferential variation in contact area and stiffness falls.  If the model
    did not reproduce that, it would not be modelling a tie bar.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const {{pattern}} = E.loadPattern(fs.readFileSync({json.dumps(TIEBAR_DXF)}, 'utf8'), {{height: 16, draft_angle: 2}}, {{}});
const sp = {{shore_a: 65, poisson: 0.49, mode: 'parallel', bulk_modulus: 1100, n_slices: 16, sipe_model: 'layered'}};
const spec = {{shape: 'rectangle', length: 200, width: 200, rotation: 0, y_center: 0,
               gamma_deg: 0, load_N: null, scale_with_lean: false}};
const cpp = {{vertical_load: 25000, wheel_radius: 500, load_rises_with_lean: false}};
function at(w) {{
  const blocks = E.effectiveBlocks(pattern, w);
  const pat = Object.assign({{}}, pattern, {{blocks: blocks}});
  const grid = E.makeGrid(pat, 1024, 96);
  const pack = E.rasterise(pat, grid, sp, false, null);
  const r = E.sweepLean(pat, pack, 0, spec, cpp, new E.MapFFTCache(pack), 90, null);
  const a = E.fluctuationStats(r.contact_area), x = E.fluctuationStats(r.kx);
  return {{n: blocks.length, area: a.mean, cov: a.cov, kxCov: x.cov}};
}}
process.stdout.write(JSON.stringify([at(7.0), at(7.4)]));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[:2000]
    before, after = json.loads(res.stdout)
    assert before["n"] == 80 and after["n"] == 118, "the bars engage between 7.0 and 7.4 mm wear"
    assert after["area"] > before["area"], "engaged tie bars are land and must add contact area"
    assert after["cov"] < before["cov"], "tie bars must damp the contact-area fluctuation"
    assert after["kxCov"] < before["kxCov"], "tie bars must damp the longitudinal stiffness ripple"


def test_wear_shortens_every_block():
    """Kx ~ 1/L^3 for a guided beam, so a worn tread is markedly stiffer."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const p = {{blocks: [{{id: 'B', polygon: [[0,0],[10,0],[10,10],[0,10]], zone: 'center',
   height: 10, draft_angle: 0, n_lateral_sipes: 0, sipes: []}}], tiebars: []}};
process.stdout.write(JSON.stringify([0, 2, 5].map(w => E.effectiveBlocks(p, w)[0].height)));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    assert json.loads(res.stdout) == [10, 8, 5]


def test_wear_that_removes_the_tread_is_refused():
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const p = {{blocks: [{{height: 8, polygon: [[0,0],[1,0],[1,1]]}}], tiebars: []}};
try {{ E.validateWear(9, p); process.stdout.write('NO ERROR'); }}
catch (e) {{ process.stdout.write(e.message); }}
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    assert "removes every block" in res.stdout and "8.00 mm" in res.stdout


def test_the_tie_bar_editor_lets_every_bar_be_set_individually():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    # a per-bar height field, an include/exclude box and a force-contact override
    for field in ("'height'", "'enabled'", "'force_contact'"):
        assert "data-tbfield=" in ui and field in ui, field
    # bulk helpers, so 38 bars do not have to be typed one at a time
    for control in ("tbApplyAll", "tbEnableAll", "tbDisableAll"):
        assert f'id="{control}"' in tpl and control in ui


def test_the_tie_bar_editor_is_an_input_not_a_result():
    """Which regions are bars, how tall each is, and how worn the tread is all
    feed the sweep.  Putting the editor among the result tabs meant running a
    throwaway sweep to see what was detected, then setting the values that
    sweep depended on, then running again."""
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'data-tab="tiebars"' not in tpl, "tie bars must not be a results tab"
    assert 'id="panel-tiebars"' not in tpl
    setup = tpl[tpl.index('<aside class="sidebar">'):tpl.index("</aside>")]
    for control in ("wear", "tiebarHeight", "tbTable", "tbPlot", "tbApplyAll"):
        assert f'id="{control}"' in setup, f"{control} belongs in the setup panel"
    # and it must come before Run, which is what it gates
    assert setup.index('id="grp-wear"') < setup.index('id="runBtn"')


def test_tie_bars_are_drawn_on_the_rolled_out_pattern():
    """The sweep counted engaged bars as land, but nothing drew them, so a step
    in the curves had no visible cause on the pattern below."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    strip = ui[ui.index("function renderPatternStrip()"):ui.index("function renderLeanHeatmap()")]
    assert "tiebars" in strip, "the pattern strip must draw tie bars"
    editor = ui[ui.index("function drawEditor()"):]
    editor = editor[:editor.index("function ") + editor[editor.index("function ") + 1:].index("\n  function ")]
    assert "tiebars" in editor, "the patch preview must draw tie bars"
    # ...and the strip must show the state the RUN used, not the live inputs,
    # or an edit after a run would repaint bars as land the curves never had
    assert "function ranEngaged(" in ui
    assert "engaged_ids" in ui
    worker = open(os.path.join(APP, "worker.js"), encoding="utf-8").read()
    assert "engaged_ids" in worker


# ---------------------------------------------------------------------------
# compound modulus
# ---------------------------------------------------------------------------


def test_modulus_can_be_entered_directly_and_is_always_displayed():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    for control in ("modulusMode", "eModulus", "gentK", "compoundReadout"):
        assert f'id="{control}"' in tpl, control
    # E, G and k must be shown, not just consumed
    assert "compoundProperties" in ui
    for sym in ("<b>E</b>", "<b>G</b>", "<b>k</b>"):
        assert sym in ui, sym
    # and every export must record which of the two paths produced them
    assert "function compoundLine(" in ui
    assert ui.count("compoundLine(s") >= 2


def test_direct_modulus_rejects_a_units_mistake():
    """6890 is 6.89 N/mm^2 written in kPa.  Silently accepting it would scale
    every stiffness by a thousand."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const bad = [];
for (const v of [6890, 0, -1, 'x']) {{
  try {{ E.validateCompound({{modulus_mode: 'direct', e_modulus: v, gent_k: 0.64}}); bad.push(v); }}
  catch (e) {{ if (v === 6890 && !/N\\/mm\\^2/.test(e.message)) bad.push('no units hint'); }}
}}
E.validateCompound({{modulus_mode: 'direct', e_modulus: 9.5, gent_k: 0.64}});
process.stdout.write(JSON.stringify(bad));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    assert json.loads(res.stdout) == [], "some invalid modulus was accepted"


def test_js_and_python_agree_on_the_interpolated_shore_curve():
    node = _node()
    from tread_eval.stiffness import shore_e, shore_k

    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const s = [];
for (let v = 28; v <= 74; v += 0.5) s.push([E.shoreE(v), E.shoreK(v)]);
process.stdout.write(JSON.stringify(s));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    got = json.loads(res.stdout)
    for i, (e, k) in enumerate(got):
        s = 28 + i * 0.5
        assert e == pytest.approx(shore_e(s), rel=1e-12), f"E disagrees at Shore {s}"
        assert k == pytest.approx(shore_k(s), rel=1e-12), f"k disagrees at Shore {s}"


def test_every_chart_sees_the_tie_bars():
    """The user's question: is the tie-bar effect in *all* the graphs?

    Everything downstream of the raster shares one block list, so if the zone
    split, the rib bands and the per-block stiffness summary all move when the
    bars engage, then so does every chart drawn from them.  This asserts that
    rather than assuming it.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const {{pattern}} = E.loadPattern(fs.readFileSync({json.dumps(TIEBAR_DXF)}, 'utf8'), {{height: 16, draft_angle: 2}}, {{}});
const sp = {{shore_a: 65, poisson: 0.49, mode: 'parallel', bulk_modulus: 1100, n_slices: 16, sipe_model: 'layered'}};
const spec = {{shape: 'rectangle', length: 200, width: 190, rotation: 0, y_center: 0,
               gamma_deg: 0, load_N: null, scale_with_lean: false}};
const cpp = {{vertical_load: 25000, wheel_radius: 500, load_rises_with_lean: false}};
const edges = [-102, -34, 34, 102];
function at(w) {{
  const pat = Object.assign({{}}, pattern, {{blocks: E.effectiveBlocks(pattern, w)}});
  const grid = E.makeGrid(pat, 1024, 96);
  const pack = E.rasterise(pat, grid, sp, false, null);
  const r = E.sweepLean(pat, pack, 0, spec, cpp, new E.MapFFTCache(pack), 90, edges);
  const mean = a => E.fluctuationStats(a).mean;
  return {{
    area: mean(r.contact_area), kz: mean(r.kz), kx: mean(r.kx), ky: mean(r.ky),
    blocks: mean(r.block_count), land: mean(r.land_ratio),
    zones: Object.keys(r.zone_area).sort().map(z => mean(r.zone_area[z])),
    bands: r.bands.map(b => mean(b.contact_area)),
    bandKz: r.bands.map(b => mean(b.kz)),
    orders: E.orderSpectrum(r.kz, 40).amplitude.reduce((s, v) => s + v, 0),
    stiffN: pack.stiffness.length,
  }};
}}
process.stdout.write(JSON.stringify([at(7.0), at(7.4)]));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[:2000]
    before, after = json.loads(res.stdout)

    # the per-block stiffness table (Diagnostics) gains the engaged bars
    assert after["stiffN"] == before["stiffN"] + 38

    # the theta-sweep rows: area, all three stiffnesses, block count, land ratio
    for key in ("area", "kz", "kx", "ky", "blocks", "land"):
        assert after[key] > before[key], f"{key} did not respond to the tie bars"

    # the Zones chart -- the bars are in the centre ribs, so the centre zone
    # must grow and the shoulder must not
    zb, za = before["zones"], after["zones"]
    assert any(a > b for a, b in zip(za, zb)), "no zone changed"
    assert all(a >= b - 1e-9 for a, b in zip(za, zb)), "a zone lost area when bars engaged"

    # the Bands chart -- and the bands must still sum to the whole
    assert sum(after["bands"]) > sum(before["bands"])
    assert sum(after["bands"]) == pytest.approx(after["area"], rel=1e-9)
    assert sum(after["bandKz"]) == pytest.approx(after["kz"], rel=1e-9)
    assert any(a > b for a, b in zip(after["bandKz"], before["bandKz"]))

    # the Order-content chart is a transform of the same Kz signal
    assert after["orders"] != pytest.approx(before["orders"], rel=1e-6)


def test_the_contact_patch_band_is_drawn_and_is_x_only():
    """The patch outline on the rolled-out pattern, and a band of the same
    circumferential extent through every sweep row above it.

    Dragging is along theta only.  Lateral position is set by the crown and the
    lean angle (or the y-centre field), so letting it be shoved sideways on this
    chart would silently contradict the physics that put it there.
    """
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    css = open(os.path.join(APP, "style.css"), encoding="utf-8").read()

    assert "function drawPatchBand(" in ui
    assert "cpband" in ui and "cpoutline" in ui
    assert ".cpband" in css and ".cpoutline" in css
    # translucent, or it would hide the curves it is supposed to sit over
    band = css[css.index("svg.cpov .cpband"):css.index("svg.cpov .cpband:hover")]
    assert "fill-opacity" in band and float(band.split("fill-opacity:")[1].split(";")[0]) < 0.3

    # the drag reads clientX and never clientY
    drag = ui[ui.index("function onPatchDrag("):ui.index("function onPatchDrop(")]
    assert "clientX" in drag
    assert "clientY" not in drag, "the patch drag must ignore the pointer's y"

    # an exact angle can be typed as well as dragged
    assert 'id="patchTheta"' in tpl and "setPatchTheta" in ui

    # the band spans the whole stack, not one row: the overlay is sized from the
    # figure's plot area, which covers every subplot
    draw = ui[ui.index("function drawPatchBand("):ui.index("function updatePatchLabel(")]
    assert "g.height" in draw and "thetaStack" in draw and "patternStrip" in draw
    # and it is redrawn whenever Plotly re-lays the figure out
    assert "plotly_afterplot" in ui


def test_the_patch_band_survives_the_gestures_it_intercepts():
    """The band takes the pointer, so it owns two things Plotly would have done.

    A double-click that lands on it must still reset the zoom, and a plain click
    must not redraw the overlay -- swapping the band out between the two halves
    of a double-click is exactly what stopped the reset from working.
    """
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    assert "function resetThetaZoom(" in ui
    assert 'svg.addEventListener("dblclick"' in ui, "bind to the overlay, not the rebuilt rects"
    assert 'svg.addEventListener("pointerdown"' in ui
    drop = ui[ui.index("function onPatchDrop("):ui.index("function linkThetaFigures(")]
    assert "if (moved) drawPatchBand();" in drop, "a plain click must not redraw the band"
    # preventDefault on pointerdown would suppress the synthesised dblclick
    grab = ui[ui.index("function onPatchGrab("):ui.index("function onPatchDrag(")]
    assert "ev.preventDefault()" not in grab


# ---------------------------------------------------------------------------
# tie-bar coupling network ("Tier 2")
# ---------------------------------------------------------------------------


def test_coupling_audit_passes():
    """The full audit of the coupled network.

    Everywhere else in the engine a block is an independent spring, which is what
    makes the theta sweep an FFT.  The coupled solve deliberately breaks that
    assumption, so it carries its own audit: linear algebra against closed forms,
    the assembled system against equilibrium, a three-node case against a hand
    solution, and every physical statement the feature rests on.
    """
    node = _node()
    proc = subprocess.run([node, os.path.join(APP, "couplingaudit.js")],
                          capture_output=True, text=True, cwd=REPO, timeout=600)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert "checks passed" in proc.stdout


def test_coupling_is_reported_but_never_folded_into_the_main_curves():
    """The coupled solve uses a force-controlled definition; the theta sweep uses
    a parallel sum.  They are different measurements, so the coupled result is
    shown beside the sweep rather than silently replacing it, and the gain
    between coupled and uncoupled -- both measured the same way -- is the number
    to read."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'data-tab="coupling"' in tpl and 'id="panel-coupling"' in tpl
    assert "function renderCoupling(" in ui
    # the main sweep rows must not be scaled by any coupling factor
    stack = ui[ui.index("function renderThetaStack("):ui.index("function renderPatternStrip(")]
    for token in ("coupling", "gain_kx", "gain_ky"):
        assert token not in stack, f"the theta sweep must not be adjusted by {token}"
    # both curves, all three stiffnesses, and the assumptions stated on the page
    for label in ("Kx uncoupled", "Kx coupled", "Kxy uncoupled", "Kxy coupled"):
        assert label in ui, label
    assert "What this model assumes" in tpl
    # and it reaches every export
    assert "function couplingLine(" in ui and ui.count("couplingLine(s") >= 3
    assert "tiebar_coupling" in ui


def test_coupling_never_changes_the_contact_area():
    """The whole point: a sub-surface bar stiffens without touching the road."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const {{pattern}} = E.loadPattern(fs.readFileSync({json.dumps(TIEBAR_DXF)}, 'utf8'), {{height: 16, draft_angle: 2}}, {{}});
const sp = {{shore_a: 65, poisson: 0.49, mode: 'parallel', bulk_modulus: 1100, n_slices: 16, sipe_model: 'layered'}};
const spec = {{shape: 'rectangle', length: 200, width: 190, rotation: 0, y_center: 0,
               gamma_deg: 0, load_N: null, scale_with_lean: false}};
const cpp = {{vertical_load: 25000, wheel_radius: 500, load_rises_with_lean: false}};
const pat = Object.assign({{}}, pattern, {{blocks: E.effectiveBlocks(pattern, 0)}});
const grid = E.makeGrid(pat, 1024, 96);
const pack = E.rasterise(pat, grid, sp, false, null);
const patch = E.shapePatch(spec, pattern.crown, pattern.tread_width, cpp);
const sweep = E.sweepLean(pat, pack, 0, spec, cpp, new E.MapFFTCache(pack), 90, null);
const cs = E.couplingSweep(pattern, pack, patch, 0, sp, 360);
let worstArea = 0;
for (let s = 0; s < cs.theta_deg.length; s++) {{
  const j = Math.round(cs.theta_deg[s] / 360 * grid.nx) % grid.nx;
  worstArea = Math.max(worstArea, Math.abs(cs.contact_area[s] - sweep.contact_area[j]) / sweep.contact_area[j]);
}}
process.stdout.write(JSON.stringify({{
  worstArea: worstArea, gainKx: cs.gain_kx, gainKy: cs.gain_ky,
  submerged: cs.n_submerged, engaged: cs.n_engaged, links: cs.n_links,
}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[:2000]
    got = json.loads(res.stdout)
    # at zero wear every bar is below the surface...
    assert got["submerged"] == 38 and got["engaged"] == 0
    assert got["links"] == 76
    # ...so the contact area is bit-identical to the uncoupled sweep...
    assert got["worstArea"] < 1e-12, f"coupling moved the contact area by {got['worstArea']:.2e}"
    # ...while the stiffness is genuinely raised.
    assert got["gainKx"] > 1.02, got["gainKx"]
    assert got["gainKy"] > 1.01, got["gainKy"]


def test_land_percentage_has_its_own_row_in_the_theta_stack():
    """How much of the patch is rubber, as the patch travels round the tyre.

    Contact area alone cannot answer that: it moves when the patch changes size
    as well as when the pattern under it changes.  Normalising by the patch area
    separates the two.
    """
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    stack = ui[ui.index("function renderThetaStack("):ui.index("function patternStripTitle(")]
    assert "Land in patch" in stack and 'axis: "Land (%)"' in stack
    assert "land_ratio[li] * 100" in stack, "must be shown as a percentage"
    assert "landMean" in stack, "the mean belongs on the row, so the swing can be read off it"
    # the row height must scale with the row count, or adding one squeezes the rest
    assert "120 * rows.length" in stack
    # and the value is reported at the cursor and under the patch band
    assert '["land", (r.land_ratio[i] * 100).toFixed(1) + "%"]' in ui
    assert "% land)" in ui


def test_land_ratio_is_contact_area_over_patch_area():
    """The definition, checked against the two quantities it is built from."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const fs = require('fs');
const {{pattern}} = E.loadPattern(fs.readFileSync({json.dumps(DXF)}, 'latin1'), {{height: 8.5, draft_angle: 3}}, {{}});
const sp = {{shore_a: 60, poisson: 0.49, mode: 'parallel', bulk_modulus: 1100, n_slices: 16, sipe_model: 'layered'}};
const spec = {{shape: 'rounded', length: 90, width: 50, corner_radius: 12, rotation: 0,
               y_center: 0, gamma_deg: 0, load_N: null, scale_with_lean: false}};
const cpp = {{vertical_load: 1500, wheel_radius: 320, load_rises_with_lean: false}};
const grid = E.makeGrid(pattern, 1024, 74);
const pack = E.rasterise(pattern, grid, sp, false, null);
const r = E.sweepLean(pattern, pack, 0, spec, cpp, new E.MapFFTCache(pack), 90, null);
let worst = 0, lo = 1, hi = 0;
for (let i = 0; i < r.land_ratio.length; i++) {{
  worst = Math.max(worst, Math.abs(r.land_ratio[i] - r.contact_area[i] / r.patch_area));
  lo = Math.min(lo, r.land_ratio[i]); hi = Math.max(hi, r.land_ratio[i]);
}}
process.stdout.write(JSON.stringify({{worst: worst, lo: lo, hi: hi}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stderr[:2000]
    got = json.loads(res.stdout)
    assert got["worst"] < 1e-15, f"land ratio is not contact area / patch area ({got['worst']:.2e})"
    # a real tread ripples but never leaves 0..1
    assert 0.0 < got["lo"] < got["hi"] < 1.0
    assert got["hi"] - got["lo"] > 0.005, "a real pattern must show some ripple in land"


# ---------------------------------------------------------------------------
# tread arc profile (crown) and camber
# ---------------------------------------------------------------------------


def test_single_arc_crown_matches_the_closed_form():
    """A constant-radius crown has phi(y) = y/R exactly, so the tangent angle at
    the tread edge -- which is the maximum lean the tyre can reach -- is
    half_width / R.  Everything about camber in this tool is derived from that
    integral, so it is the right thing to pin."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const out = [];
for (const [w, r] of [[220, 800], [159, 300], [180, 1200], [140, 90]]) {{
  const c = E.crownMultiArc(w, [{{r: r}}]);
  out.push([w, r, E.maxSupportedLean(c), E.crownDrop(c, w / 2), E.crownLocalRadius(c, w / 4)]);
}}
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    import math

    for w, r, max_lean, drop, r_quarter in json.loads(res.stdout):
        # phi_edge = (half width) / R, in degrees
        assert max_lean == pytest.approx(math.degrees((w / 2) / r), rel=1e-6), (w, r)
        # a circular arc drops R(1 - cos(phi)) at the edge
        assert drop == pytest.approx(r * (1 - math.cos((w / 2) / r)), rel=1e-4), (w, r)
        # and the radius is that radius everywhere
        assert r_quarter == pytest.approx(r, rel=1e-12)


def test_multi_arc_is_piecewise_constant_curvature_with_tangent_continuity():
    """What a tread arc specification actually is.

    Radius is piecewise constant and jumps at each breakpoint -- that IS the
    spec.  The tangent angle must NOT jump, because phi is the integral of 1/r
    and an integral of a bounded function is continuous however abruptly the
    function steps.  That is what makes the arcs meet tangentially.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const w = 220, half = 110;
const c = E.crownMultiArc(w, [{{r: 800, to: 45}}, {{r: 300, to: 88}}, {{r: 90}}]);
const probe = (y) => [E.crownLocalRadius(c, y), E.crownTangentAngle(c, y) * 180 / Math.PI];
process.stdout.write(JSON.stringify({{
  before45: probe(44.0), after45: probe(46.0),
  before88: probe(87.0), after88: probe(89.0),
  centre: probe(0), edge: probe(half),
  maxLean: E.maxSupportedLean(c),
  arcs: c.arcs,
}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    g = json.loads(res.stdout)

    # radius steps at the breakpoints
    assert g["before45"][0] == pytest.approx(800, rel=1e-9)
    assert g["after45"][0] == pytest.approx(300, rel=1e-9)
    assert g["before88"][0] == pytest.approx(300, rel=1e-9)
    assert g["after88"][0] == pytest.approx(90, rel=1e-9)

    # ...but the tangent angle does not.  Across a 2 mm window straddling a
    # breakpoint the angle may change by at most what the tighter arc can turn
    # in that distance, with a little slack for the sampling grid.
    import math

    for lo, hi, tighter in ((g["before45"], g["after45"], 300.0), (g["before88"], g["after88"], 90.0)):
        step = abs(hi[1] - lo[1])
        assert step < math.degrees(2.0 / tighter) * 1.3, f"tangent angle jumped by {step:.3f} deg"

    assert g["centre"][1] == pytest.approx(0.0, abs=1e-12), "phi is zero at the centreline by definition"
    assert g["maxLean"] == pytest.approx(g["edge"][1], rel=1e-9)
    assert [a["radius"] for a in g["arcs"]] == [800, 300, 90]
    assert g["arcs"][-1]["to_mm"] == pytest.approx(110.0)


def test_the_arc_spec_parser_and_its_error_messages():
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const w = 200;                       // half width 100 mm
const ok = {{}}, bad = {{}};
for (const t of ['300', '125@0.45, 55', '800@45mm, 300@88mm, 90', '800@45%, 300@80%, 90', ' 300 @ 0.6 ; 90 ']) {{
  try {{ ok[t] = E.parseCrownArcs(t, w).map(a => [a.r, Number.isFinite(a.to) ? +a.to.toFixed(3) : null]); }}
  catch (e) {{ ok[t] = 'THREW: ' + e.message; }}
}}
for (const t of ['', 'abc', '800@45mm', '125@0.45, 55@0.9', '800', '-100', '5', '800@120mm, 90', '800@60mm, 300@30mm, 90',
                 '100@0.2, 100@0.4, 100@0.5, 100@0.6, 100@0.7, 100@0.8, 100@0.9, 100@0.95, 100']) {{
  try {{ const r = E.parseCrownArcs(t, w); bad[t] = r === null ? 'null' : 'ACCEPTED'; }}
  catch (e) {{ bad[t] = e.message; }}
}}
process.stdout.write(JSON.stringify({{ok: ok, bad: bad}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    g = json.loads(res.stdout)

    assert g["ok"]["300"] == [[300, None]]
    # a breakpoint <= 1 is a fraction of the half width, > 1 is millimetres
    assert g["ok"]["125@0.45, 55"] == [[125, 45.0], [55, None]]
    assert g["ok"]["800@45mm, 300@88mm, 90"] == [[800, 45.0], [300, 88.0], [90, None]]
    assert g["ok"]["800@45%, 300@80%, 90"] == [[800, 45.0], [300, 80.0], [90, None]]
    # whitespace and semicolons are tolerated
    assert g["ok"][" 300 @ 0.6 ; 90 "] == [[300, 60.0], [90, None]]

    assert g["bad"][""] == "null", "an empty spec means 'use the two-radius fields'"
    assert g["bad"]["800"] == "ACCEPTED", "a lone radius is a valid single-arc crown"
    for spec, needle in [
        ("abc", "could not read"),
        ("800@45mm", "must not carry a breakpoint"),
        ("125@0.45, 55@0.9", "must not carry a breakpoint"),
        ("-100", "positive"),
        ("5", "implausibly tight"),
        ("800@120mm, 90", "beyond the tread edge"),
        ("800@60mm, 300@30mm, 90", "must increase"),
    ]:
        assert needle in g["bad"][spec], f"{spec!r} -> {g['bad'][spec]!r}"
    assert "at most" in g["bad"]["100@0.2, 100@0.4, 100@0.5, 100@0.6, 100@0.7, 100@0.8, 100@0.9, 100@0.95, 100"]


def test_camber_walks_the_contact_point_along_the_profile():
    """The whole camber model in one check.

    The contact point at lean gamma is where the tread tangent has turned to
    gamma.  It must therefore move outboard monotonically with lean, the local
    lateral radius there must follow the profile, and past the steepest tangent
    the profile reaches there must be no contact point at all.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const c = E.crownMultiArc(220, [{{r: 800, to: 45}}, {{r: 300, to: 88}}, {{r: 90}}]);
const rows = [];
for (let g = 0; g <= 25; g += 1) {{
  const y = E.crownContactLateral(c, g);
  rows.push([g, y, E.crownLocalRadius(c, y), E.crownDrop(c, y)]);
}}
process.stdout.write(JSON.stringify({{rows: rows, maxLean: E.maxSupportedLean(c)}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    g = json.loads(res.stdout)
    rows, max_lean = g["rows"], g["maxLean"]

    assert rows[0][1] == pytest.approx(0.0, abs=1e-9), "upright, the tyre touches on the centreline"
    reachable = [r for r in rows if r[0] <= max_lean]
    for a, b in zip(reachable, reachable[1:]):
        assert b[1] > a[1], f"contact point did not move outboard from {a[0]} to {b[0]} deg"
        assert b[3] >= a[3] - 1e-9, "the contact point must sit progressively lower"
    # the local radius collapses as the contact point reaches the shoulder arc
    assert reachable[0][2] == pytest.approx(800, rel=1e-6)
    assert reachable[-1][2] < 400, "the shoulder arc should be in contact near max lean"
    # past the steepest tangent the point saturates at the tread edge
    beyond = [r for r in rows if r[0] > max_lean + 1]
    if beyond:
        assert all(r[1] == pytest.approx(110.0, abs=0.2) for r in beyond)


def test_a_tighter_shoulder_arc_raises_the_reachable_lean():
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const out = [90, 150, 300, 800].map(rs =>
  [rs, E.maxSupportedLean(E.crownMultiArc(220, [{{r: 800, to: 60}}, {{r: rs}}]))]);
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    out = json.loads(res.stdout)
    leans = [v for _, v in out]
    assert leans == sorted(leans, reverse=True), out
    assert leans[0] > 2 * leans[-1], "a 90 mm shoulder must reach far more lean than an 800 mm one"


def test_buildCrown_dispatches_and_leaves_the_default_untouched():
    """Adding the arc path must not move a single existing number."""
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const w = 159;
const viaBuild = E.buildCrown(w, {{crown_r_center: 125, crown_r_shoulder: 55, crown_break: 0.45}});
const direct = E.crownDualRadius(w, 125, 55, 0.45);
let worst = 0;
for (let i = 0; i < direct.y.length; i++) {{
  worst = Math.max(worst, Math.abs(viaBuild.phi[i] - direct.phi[i]), Math.abs(viaBuild.z[i] - direct.z[i]));
}}
const arcs = E.buildCrown(w, {{crown_arcs: [{{r: 300}}], crown_r_center: 125}});
process.stdout.write(JSON.stringify({{
  worst: worst, defaultHasArcs: viaBuild.arcs === undefined,
  arcPathUsed: !!arcs.arcs && arcs.arcs[0].radius === 300,
  emptyArcsFallsBack: E.buildCrown(w, {{crown_arcs: [], crown_r_center: 125}}).arcs === undefined,
}}));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    g = json.loads(res.stdout)
    assert g["worst"] == 0.0, "the default crown path changed"
    assert g["defaultHasArcs"], "the blended crown must not claim to be an arc profile"
    assert g["arcPathUsed"], "an explicit arc spec must override the two-radius fields"
    assert g["emptyArcsFallsBack"]


def test_the_crown_is_visible_and_recorded():
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    tpl = open(os.path.join(APP, "template.html"), encoding="utf-8").read()
    assert 'id="crownArcs"' in tpl and 'id="crownArcInfo"' in tpl and 'id="crownPlot"' in tpl
    assert "function renderCrownProfile(" in ui
    # the chart must show the profile, the radius and where contact lands
    prof = ui[ui.index("function renderCrownProfile("):ui.index("function renderPatchPreview(")]
    for token in ("crown.z", "crown.r", "crownContactLateral", "maxSupportedLean"):
        assert token in prof, token
    # a half-typed spec must never throw out of the paint path
    rec = ui[ui.index("function reconcilePattern("):ui.index("// ---- banner")]
    assert "try { arcs = readCrownArcs(" in rec, "reconcilePattern runs on every keystroke"
    # and the resolved crown reaches every export
    assert "function crownSummary(" in ui and "function crownLine(" in ui
    assert ui.count("crownLine(s") >= 3


def test_a_blank_crown_field_falls_back_to_the_tyre_class_not_to_a_motorcycle():
    """A blank crown is not a flat crown, and it is not a 2W crown either.

    The fallback used to be a hard-coded 125/55 whatever class was selected, so
    a 220 mm truck tread with the crown fields left blank was evaluated with
    47 mm of edge drop and 63 degrees of reachable lean.  Nothing on screen said
    so.  The class default is the only defensible guess when nothing is typed.
    """
    node = _node()
    script = f"""
const E = require({json.dumps(os.path.join(APP, 'engine.js'))});
const w = 220, half = 110;
const out = {{}};
for (const t of ['2w', 'pcr', 'tbr']) {{
  const c = E.buildCrown(w, {{tyre_class: t}});
  out[t] = [E.crownLocalRadius(c, 0), E.crownLocalRadius(c, half), E.maxSupportedLean(c)];
}}
// a caller that names no class must be exactly as it was before
const a = E.buildCrown(w, {{}}), b = E.crownDualRadius(w, 125, 55, 0.45);
let worst = 0;
for (let i = 0; i < b.y.length; i++) worst = Math.max(worst, Math.abs(a.phi[i] - b.phi[i]));
// an explicit value always wins over the class default
const explicit = E.buildCrown(w, {{tyre_class: 'tbr', crown_r_center: 400}});
out.explicit = E.crownLocalRadius(explicit, 0);
out.unchanged = worst;
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    g = json.loads(res.stdout)

    assert g["2w"][0] == pytest.approx(125) and g["2w"][1] == pytest.approx(55)
    assert g["pcr"][0] == pytest.approx(700) and g["pcr"][1] == pytest.approx(90)
    assert g["tbr"][0] == pytest.approx(1500) and g["tbr"][1] == pytest.approx(120)
    # a truck crown must reach far less lean than a motorcycle one
    assert g["tbr"][2] < g["pcr"][2] < g["2w"][2]
    assert g["tbr"][2] < 10 and g["2w"][2] > 50
    assert g["unchanged"] == 0.0, "naming no class must not change the old behaviour"
    assert g["explicit"] == pytest.approx(400), "a typed radius must beat the class default"


def test_the_resolved_crown_is_never_silent():
    """Which crown you got decides every lean angle in the sweep, so a blank
    field must never read as 'nothing set'."""
    ui = open(os.path.join(APP, "ui.js"), encoding="utf-8").read()
    info = ui[ui.index("function syncCrownArcInfo("):ui.index("function readWear(")]
    # it names the radii actually in use, whether typed or defaulted
    assert "crownLocalRadius" in info and "R centre" in info and "R edge" in info
    assert "a blank crown is not a flat one" in info
    assert "maxSupportedLean" in info, "the reachable lean is the number most likely to surprise"
    # and it refreshes when the class changes, because the class IS the fallback
    init = ui[ui.index("function init()"):]
    tyre = init[init.index('on($("tyreType")'):]
    assert "syncCrownArcInfo();" in tyre[:200]
    preset = init[init.index('on($("applyPreset")'):]
    assert "syncCrownArcInfo();" in preset[:220]
    # the class radii live in one place, not two
    assert "crownCenter: cls.crown_r_center" in ui
    assert "crownCenter: 125" not in ui and "crownCenter: 1500" not in ui
