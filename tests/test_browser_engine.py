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
