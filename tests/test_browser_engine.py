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
    assert "crownDualRadius" in body, "crown is not rebuilt"
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
