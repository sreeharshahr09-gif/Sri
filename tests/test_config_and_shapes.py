"""The config file and the standard contact-patch shapes."""

from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

import build_report
from tread_eval.config import Config, write_example
from tread_eval.contact_patch import CPParams
from tread_eval.cp_shapes import SHAPES, ShapeSpec, shape_patch, specs_from_config
from tread_eval.schema import CrownProfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAMPLR = os.path.join(REPO, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")


def crown():
    return CrownProfile.dual_radius(159.0)


# --- shapes ------------------------------------------------------------
@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_builds_a_sane_polygon(shape):
    # taper=0 so the trapezoid's bounding box matches its stated width -- for
    # that shape `width` is the *mean* width, which the taper test covers.
    spec = ShapeSpec(shape=shape, length=90.0, width=50.0, taper=0.0)
    out = spec.outline()
    assert len(out) >= 3
    assert np.ptp(out[:, 0]) == pytest.approx(90.0, rel=0.02)
    assert np.ptp(out[:, 1]) == pytest.approx(50.0, rel=0.05)
    # symmetric about its own origin (the vertex sample need not be, so compare
    # the extents rather than the mean of the vertices)
    assert out[:, 0].min() == pytest.approx(-out[:, 0].max(), abs=0.05)
    assert out[:, 1].min() == pytest.approx(-out[:, 1].max(), abs=0.05)


def test_rectangle_area_is_exact():
    patch = shape_patch(ShapeSpec(shape="rectangle", length=90, width=50), crown(), 159.0)
    assert patch.area() == pytest.approx(90 * 50, rel=1e-9)


def test_ellipse_area_is_exact():
    patch = shape_patch(ShapeSpec(shape="ellipse", length=90, width=50), crown(), 159.0)
    assert patch.area() == pytest.approx(math.pi * 45 * 25, rel=1e-3)


def test_shape_areas_are_ordered_as_expected():
    """For the same bounding box: rectangle > rounded > ellipse > diamond."""
    def area(shape, **kw):
        return shape_patch(ShapeSpec(shape=shape, length=90, width=50, **kw), crown(), 159.0).area()

    assert area("rectangle") > area("rounded", corner_radius=12) > area("ellipse") > area("diamond")


def test_corner_radius_is_clamped_to_a_stadium():
    """An over-large corner radius must degrade, not self-intersect."""
    huge = shape_patch(ShapeSpec(shape="rounded", length=90, width=50, corner_radius=999), crown(), 159.0)
    stadium = shape_patch(ShapeSpec(shape="stadium", length=90, width=50), crown(), 159.0)
    assert huge.area() == pytest.approx(stadium.area(), rel=0.02)
    assert huge.area() > 0


def test_zero_corner_radius_is_a_rectangle():
    r = shape_patch(ShapeSpec(shape="rounded", length=90, width=50, corner_radius=0), crown(), 159.0)
    assert r.area() == pytest.approx(90 * 50, rel=1e-9)


def test_trapezoid_taper_keeps_the_mean_width():
    flat = shape_patch(ShapeSpec(shape="trapezoid", length=90, width=50, taper=0.0), crown(), 159.0)
    tapered = shape_patch(ShapeSpec(shape="trapezoid", length=90, width=50, taper=0.4), crown(), 159.0)
    assert tapered.area() == pytest.approx(flat.area(), rel=1e-6)
    # but one end really is wider than the other
    out = tapered.outline
    lead = out[np.argmax(out[:, 0]), 1]
    assert abs(lead) > 0


def test_superellipse_exponent_controls_squareness():
    def area(e):
        return shape_patch(ShapeSpec(shape="superellipse", length=90, width=50, exponent=e), crown(), 159.0).area()

    assert area(2.0) < area(4.0) < 90 * 50


def test_rotation_turns_the_shape():
    spec = ShapeSpec(shape="rectangle", length=90, width=30, rotation=90.0)
    out = spec.outline()
    # a 90 deg turn swaps the extents
    assert np.ptp(out[:, 0]) == pytest.approx(30.0, abs=1e-6)
    assert np.ptp(out[:, 1]) == pytest.approx(90.0, abs=1e-6)


def test_shape_follows_the_crown_contact_point_by_default():
    c = crown()
    up = shape_patch(ShapeSpec(shape="rounded", gamma_deg=0.0), c, 159.0)
    lean = shape_patch(ShapeSpec(shape="rounded", gamma_deg=30.0), c, 159.0)
    assert up.y_center == pytest.approx(0.0, abs=1e-6)
    assert lean.y_center == pytest.approx(c.contact_lateral_position(30.0), abs=1e-6)


def test_explicit_y_center_overrides_the_crown():
    p = shape_patch(ShapeSpec(shape="rounded", gamma_deg=30.0, y_center=12.0), crown(), 159.0)
    assert p.y_center == pytest.approx(12.0)


def test_shape_pressure_balances_the_load():
    p = shape_patch(
        ShapeSpec(shape="rectangle", length=90, width=50, load_N=1800.0), crown(), 159.0
    )
    assert p.peak_pressure * p.area() == pytest.approx(1800.0, rel=1e-9)


def test_shape_patch_reports_its_own_provenance():
    p = shape_patch(ShapeSpec(shape="rounded", length=92, width=50, corner_radius=12), crown(), 159.0)
    assert p.source == "shape"
    assert "rounded" in p.provenance and "92" in p.provenance
    assert p.meta["spec"]["corner_radius"] == 12


def test_unknown_shape_is_rejected():
    with pytest.raises(ValueError, match="unknown contact patch shape"):
        ShapeSpec(shape="banana").outline()


def test_unknown_shape_setting_is_rejected():
    with pytest.raises(ValueError, match="unknown contact-patch shape settings"):
        specs_from_config([{"shape": "rounded", "lenght": 90}])  # typo


def test_shape_rasterises_through_the_normal_pipeline():
    from tread_eval.raster import Grid

    grid = Grid(nx=1600, ny=400, dx=0.25, dy=0.25, circumference=400.0, tread_width=100.0)
    patch = shape_patch(ShapeSpec(shape="rounded", length=80, width=40, corner_radius=10),
                        CrownProfile.dual_radius(100.0), 100.0)
    binary, pressure = patch.masks(grid)
    assert float(binary.sum()) * grid.pixel_area == pytest.approx(patch.area(), rel=0.01)
    assert float(pressure.max()) == pytest.approx(patch.peak_pressure, rel=0.01)


# --- config ------------------------------------------------------------
def test_load_field_is_not_shadowed_by_a_method():
    """`load` is a config section; a same-named method would become its default."""
    cfg = Config()
    assert cfg.load.vertical_N == 1500.0
    assert cfg.cp_params().vertical_load == 1500.0


def test_defaults_round_trip(tmp_path):
    cfg = Config()
    path = tmp_path / "c.json"
    cfg.save(str(path))
    back = Config.from_file(str(path))
    assert back.analysis.lean_angles == cfg.analysis.lean_angles
    assert back.compound.shore_a == cfg.compound.shore_a
    assert back.pattern.nsd_mm == cfg.pattern.nsd_mm


def test_import_key_is_not_mangled_by_the_round_trip(tmp_path):
    cfg = Config()
    cfg.contact_patch.import_ = {"path": "fp.csv", "gamma_deg": 0}
    path = tmp_path / "c.json"
    cfg.save(str(path))
    raw = json.loads(path.read_text())
    assert "import" in raw["contact_patch"] and "import_" not in raw["contact_patch"]
    assert Config.from_file(str(path)).contact_patch.import_["path"].endswith("fp.csv")


def test_unknown_section_is_an_error():
    with pytest.raises(ValueError, match="unknown config section"):
        Config.from_dict({"pattern": {}, "tyres": {}})


def test_unknown_setting_is_an_error():
    with pytest.raises(ValueError, match="unknown setting"):
        Config.from_dict({"pattern": {"nsd_mmm": 8.0}})


def test_comment_keys_are_allowed():
    cfg = Config.from_dict({
        "_comment": "notes for a human",
        "pattern": {"_comment": "more notes", "nsd_mm": 9.0},
    })
    assert cfg.pattern.nsd_mm == 9.0


def test_paths_resolve_relative_to_the_config_file(tmp_path):
    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / "plan.dxf").write_text("x")
    (sub / "c.json").write_text(json.dumps({"pattern": {"dxf": "plan.dxf"}}))
    cfg = Config.from_file(str(sub / "c.json"))
    assert cfg.pattern.dxf == str(sub / "plan.dxf")
    assert os.path.exists(cfg.pattern.dxf)


def test_example_config_is_valid_and_points_at_the_sample(tmp_path):
    path = tmp_path / "example.json"
    write_example(str(path))
    cfg = Config.from_file(str(path))
    assert cfg.name
    assert cfg.contact_patch.shapes  # demonstrates the shape feature
    assert cfg.contact_patch.shape_specs()  # and they parse
    if os.path.exists(TRAMPLR):
        assert os.path.exists(cfg.pattern.dxf), "example must point at a DXF that exists"


def test_crown_comes_from_the_config():
    cfg = Config()
    cfg.tyre.crown_r_center_mm = 90.0
    c = cfg.tyre.crown(159.0)
    assert float(c.local_radius(0.0)) == pytest.approx(90.0, rel=0.02)


def test_cp_params_are_assembled_from_several_sections():
    cfg = Config()
    cfg.load.vertical_N = 1800.0
    cfg.tyre.inflation_kpa = 300.0
    cfg.load.rises_with_lean = False
    p = cfg.cp_params()
    assert p.vertical_load == 1800.0
    assert p.inflation_kpa == 300.0
    assert p.load_rises_with_lean is False


# --- CLI wiring --------------------------------------------------------
def test_command_line_overrides_the_config(tmp_path):
    path = tmp_path / "c.json"
    Config().save(str(path))
    args = build_report.parse_args(["--config", str(path), "--nsd", "11", "--lean", "0,25"])
    cfg = build_report.apply_overrides(Config.from_file(str(path)), args)
    assert cfg.pattern.nsd_mm == 11.0
    assert cfg.analysis.lean_angles == [0.0, 25.0]


def test_absent_flags_do_not_clobber_the_config(tmp_path):
    path = tmp_path / "c.json"
    cfg = Config()
    cfg.pattern.nsd_mm = 7.25
    cfg.compound.shore_a = 65.0
    cfg.save(str(path))
    args = build_report.parse_args(["--config", str(path)])
    merged = build_report.apply_overrides(Config.from_file(str(path)), args)
    assert merged.pattern.nsd_mm == 7.25
    assert merged.compound.shore_a == 65.0


def test_shape_flags_build_a_shape_spec():
    args = build_report.parse_args(
        ["--cp-shape", "rounded", "--cp-length", "95", "--cp-width", "48", "--cp-corner-radius", "8"]
    )
    cfg = build_report.apply_overrides(Config(), args)
    assert len(cfg.contact_patch.shapes) == 1
    spec = cfg.contact_patch.shape_specs()[0]
    assert (spec.shape, spec.length, spec.width, spec.corner_radius) == ("rounded", 95.0, 48.0, 8.0)


def test_cp_import_flags_populate_the_import_block():
    args = build_report.parse_args(
        ["--cp", "fp.dxf", "--cp-gamma", "25", "--cp-load", "1700", "--cp-y", "45", "--cp-units", "cm"]
    )
    cfg = build_report.apply_overrides(Config(), args)
    imp = cfg.contact_patch.import_
    assert imp["path"] == "fp.dxf" and imp["gamma_deg"] == 25.0
    assert imp["load_N"] == 1700.0 and imp["y_center"] == 45.0 and imp["units"] == "cm"


@pytest.mark.skipif(not os.path.exists(TRAMPLR), reason="sample DXF not present")
def test_end_to_end_config_run(tmp_path):
    cfg = Config()
    cfg.name = "test tyre"
    cfg.pattern.dxf = TRAMPLR
    cfg.pattern.nsd_mm = 8.5
    cfg.analysis.lean_angles = [0.0, 30.0]
    cfg.analysis.resolution_mm = 1.2
    cfg.output.path = str(tmp_path / "r.html")
    cfg.output.plotly = "cdn"
    cfg.output.theta_points = 60
    cfg.contact_patch.shapes = [
        {"gamma_deg": 0, "shape": "rounded", "length": 92, "width": 50, "corner_radius": 12}
    ]
    path = tmp_path / "c.json"
    cfg.save(str(path))

    assert build_report.main(["--config", str(path), "--quiet"]) == 0
    html = (tmp_path / "r.html").read_text()
    assert "test tyre" in html
    assert "user-specified shape" in html
