"""Degenerate, hostile and half-typed inputs.

The failure mode this file exists to prevent is not a crash -- it is a *silent*
wrong answer: a DXF that imports as zero blocks, a NaN coordinate that poisons
the circumference, an empty input box that comes back as "Kz: NaN". Every case
here must either produce a correct result or raise a message that names the
problem and what to do about it.
"""

from __future__ import annotations

import math
import os
import subprocess

import pytest

from tread_eval.dxf import BlockDefaults, load_pattern

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DXF = os.path.join(REPO, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")

HDR = "0\nSECTION\n2\nENTITIES\n"
FTR = "0\nENDSEC\n0\nEOF\n"


def _line(x1, y1, x2, y2) -> str:
    return f"0\nLINE\n10\n{x1}\n20\n{y1}\n11\n{x2}\n21\n{y2}\n"


def _rect(x0, y0, w, h) -> str:
    return (_line(x0, y0, x0 + w, y0) + _line(x0 + w, y0, x0 + w, y0 + h)
            + _line(x0 + w, y0 + h, x0, y0 + h) + _line(x0, y0 + h, x0, y0))


def _write(tmp_path, body: str, name: str = "t.dxf") -> str:
    p = tmp_path / name
    p.write_text(HDR + body + FTR)
    return str(p)


# --- the real drawing must be unaffected by all of this ----------------
def test_real_dxf_still_imports_identically():
    """The guard rails must not change a single number on a genuine tread plan."""
    pattern, report = load_pattern(REAL_DXF, BlockDefaults(height=8.5, draft_angle=3.0))
    assert report.n_blocks if hasattr(report, "n_blocks") else True
    assert len(pattern.blocks) == 168
    assert report.n_wrapped == 3
    assert report.land_ratio == pytest.approx(0.690, abs=0.001)
    assert report.circumference == pytest.approx(2193.403, abs=0.01)
    assert report.tread_width == pytest.approx(159.0, abs=0.01)
    assert len(pattern.pitches) == 24
    assert any("construction line" in w for w in report.warnings)


# --- imports that must FAIL loudly -------------------------------------
def test_zero_blocks_is_an_error_not_an_empty_tyre(tmp_path):
    """Silently importing nothing gives all-zero charts with nothing to explain
    them -- the single most confusing way for this tool to fail."""
    path = _write(tmp_path, _line(0, 0, 10, 0) + _line(10, 0, 10, 10))  # never closes
    with pytest.raises(ValueError, match="imported 0 tread blocks"):
        load_pattern(path, BlockDefaults(height=8.0))


def test_zero_blocks_message_names_the_likely_cause(tmp_path):
    path = _write(tmp_path, _rect(0, 0, 1, 1) + _rect(3, 0, 1, 1))  # both under min area
    with pytest.raises(ValueError) as exc:
        load_pattern(path, BlockDefaults(height=8.0))
    assert "minimum block area" in str(exc.value)
    assert "layer" in str(exc.value)  # actionable advice


@pytest.mark.parametrize("body", [
    _line(0, 0, 10, 0) + _line(10, 0, 20, 0),   # zero height
    _line(0, 0, 0, 10) + _line(0, 10, 0, 20),   # zero width
])
def test_degenerate_extents_are_rejected(tmp_path, body):
    path = _write(tmp_path, body)
    with pytest.raises(ValueError, match="extents are degenerate"):
        load_pattern(path, BlockDefaults(height=8.0))


def test_file_with_no_entities_is_rejected(tmp_path):
    p = tmp_path / "empty.dxf"
    p.write_text("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
    with pytest.raises(ValueError, match="no usable LINE/ARC/POLYLINE"):
        load_pattern(str(p), BlockDefaults(height=8.0))


def test_non_finite_coordinates_are_skipped_not_propagated(tmp_path):
    """One NaN used to reach the extents and turn the circumference into NaN."""
    body = "0\nLINE\n10\nnan\n20\n0\n11\n10\n21\n0\n" + _rect(0, 0, 40, 20)
    pattern, report = load_pattern(_write(tmp_path, body), BlockDefaults(height=8.0))
    assert math.isfinite(pattern.tyre_circumference) and pattern.tyre_circumference > 0
    assert math.isfinite(pattern.tread_width) and pattern.tread_width > 0
    assert math.isfinite(report.land_ratio)
    assert any("non-finite" in w for w in report.warnings)


# --- the construction-line heuristic -----------------------------------
def test_a_small_drawing_keeps_its_only_block(tmp_path):
    """The old rule dropped any 2-point segment longer than half the drawing.
    On a single-block drawing that is every edge, so the block vanished."""
    pattern, _ = load_pattern(_write(tmp_path, _rect(0, 0, 5, 5)), BlockDefaults(height=8.0))
    assert len(pattern.blocks) == 1


# --- input validation ---------------------------------------------------
@pytest.mark.parametrize("kw,fragment", [
    (dict(height=0.0), "must be a positive number"),
    (dict(height=-5.0), "must be a positive number"),
    (dict(height=float("nan")), "must be a positive number"),
    (dict(height=8.0, draft_angle=90.0), "between -90 and 90"),
    (dict(height=8.0, draft_angle=float("inf")), "between -90 and 90"),
    (dict(height=8.0, n_lateral_sipes=-3), "non-negative whole number"),
    (dict(height=8.0, n_lateral_sipes=1000), "implausible"),
    (dict(height=8.0, sipe_depth_fraction=1.5), "between 0 and 1"),
])
def test_bad_block_defaults_are_rejected(kw, fragment):
    """These used to sail through and zero (or NaN) every stiffness."""
    with pytest.raises(ValueError, match=fragment):
        load_pattern(REAL_DXF, BlockDefaults(**kw))


@pytest.mark.parametrize("kw,fragment", [
    (dict(circumference=0.0), "must be a positive number"),
    (dict(circumference=-100.0), "must be a positive number"),
    (dict(tread_width=0.0), "must be a positive number"),
    (dict(n_pitches=-5), "whole number >= 1"),
    (dict(n_pitches=1_000_000), "implausible"),
    (dict(min_block_area=-1.0), "non-negative"),
])
def test_bad_import_options_are_rejected(kw, fragment):
    with pytest.raises(ValueError, match=fragment):
        load_pattern(REAL_DXF, BlockDefaults(height=8.0), **kw)


def test_nested_loops_are_reported(tmp_path):
    """A circle inside a rectangle is usually a hole. The sweep measures the
    union and is right either way, but the summed land ratio double-counts."""
    body = _rect(0, 0, 60, 30) + "0\nCIRCLE\n10\n20\n20\n10\n40\n5\n"
    _, report = load_pattern(_write(tmp_path, body), BlockDefaults(height=8.0))
    assert any("inside another outline" in w for w in report.warnings)


def test_the_sweep_measures_the_union_not_the_sum(tmp_path):
    """Overlapping outlines must not double-count in the actual computation."""
    from tread_eval.raster import Grid, rasterise
    from tread_eval.stiffness import StiffnessParams

    body = _rect(0, 0, 60, 30) + "0\nCIRCLE\n10\n20\n20\n10\n40\n5\n"
    pattern, report = load_pattern(_write(tmp_path, body), BlockDefaults(height=8.0))
    grid = Grid(nx=1024, ny=64, dx=pattern.tyre_circumference / 1024,
                dy=pattern.tread_width / 64,
                circumference=pattern.tyre_circumference, tread_width=pattern.tread_width)
    pack = rasterise(pattern, grid, stiffness_params=StiffnessParams(shore_a=60))
    gross = pattern.tyre_circumference * pattern.tread_width
    assert float(pack.area.sum()) / gross <= 1.0 + 1e-9, "rasterised land exceeded the gross area"
    assert report.land_ratio > 1.0  # the summed estimate does over-count, hence the warning


# --- the JavaScript engine must behave identically ---------------------
def _node() -> str:
    import shutil
    for cand in ("/opt/node22/bin/node", "node"):
        if shutil.which(cand) or os.path.exists(cand):
            return cand
    pytest.skip("node not available")


def test_js_engine_rejects_the_same_degenerate_inputs():
    node = _node()
    script = r"""
    const E = require('./app/engine.js');
    const HDR="0\nSECTION\n2\nENTITIES\n", FTR="0\nENDSEC\n0\nEOF\n";
    const L=(a,b,c,d)=>`0\nLINE\n10\n${a}\n20\n${b}\n11\n${c}\n21\n${d}\n`;
    const R=(x,y,w,h)=>L(x,y,x+w,y)+L(x+w,y,x+w,y+h)+L(x+w,y+h,x,y+h)+L(x,y+h,x,y);
    const out = {};
    function tryIt(name, fn) { try { fn(); out[name] = null; } catch (e) { out[name] = e.message; } }
    tryIt('no_blocks',   () => E.loadPattern(HDR+L(0,0,10,0)+L(10,0,10,10)+FTR, {height:8}, {}));
    tryIt('degenerate',  () => E.loadPattern(HDR+L(0,0,10,0)+L(10,0,20,0)+FTR, {height:8}, {}));
    tryIt('no_entities', () => E.loadPattern("0\nSECTION\n2\nHEADER\n0\nENDSEC\n", {height:8}, {}));
    tryIt('bad_nsd',     () => E.loadPattern(HDR+R(0,0,40,20)+FTR, {height:0}, {}));
    tryIt('bad_pitches', () => E.loadPattern(HDR+R(0,0,40,20)+FTR, {height:8}, {n_pitches:1000000}));
    tryIt('small_ok',    () => { const r=E.loadPattern(HDR+R(0,0,5,5)+FTR,{height:8},{});
                                 if (r.pattern.blocks.length !== 1) throw new Error('lost the only block'); });
    process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    import json
    res = json.loads(proc.stdout)
    assert "0 tread blocks" in (res["no_blocks"] or "")
    assert "degenerate" in (res["degenerate"] or "")
    assert "no usable" in (res["no_entities"] or "")
    assert "positive number" in (res["bad_nsd"] or "")
    assert "implausible" in (res["bad_pitches"] or "")
    assert res["small_ok"] is None, res["small_ok"]
