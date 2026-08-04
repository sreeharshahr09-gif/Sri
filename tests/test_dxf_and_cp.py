"""DXF import and the contact-patch definition / fallback chain."""

from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from tread_eval.contact_patch import (
    CPLibrary,
    CPParams,
    ContactPatch,
    PressureGrid,
    ellipse_outline,
    interpolated_patch,
    measured_patch,
    rhyne_patch,
    superellipse_outline,
    transferred_patch,
    winkler_patch,
)
from tread_eval.cp_io import build_library, load_cp_any, load_cp_manifest, load_outline_csv
from tread_eval.dxf import (
    BlockDefaults,
    build_loops,
    close_across_seam,
    entities_to_segments,
    load_pattern,
    read_dxf_entities,
)
from tread_eval.raster import make_grid, rasterise
from tread_eval.schema import CrownProfile
from tread_eval.sweep import sweep_lean

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAMPLR = os.path.join(REPO, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")
needs_dxf = pytest.mark.skipif(not os.path.exists(TRAMPLR), reason="sample DXF not present")


# ---------------------------------------------------------------- DXF
def _square_dxf(tmp_path, name="sq.dxf"):
    """A minimal DXF: one closed square made of four LINE entities."""
    pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    body = []
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        body += ["0", "LINE", "8", "0", "10", str(a[0]), "20", str(a[1]),
                 "11", str(b[0]), "21", str(b[1])]
    lines = ["0", "SECTION", "2", "ENTITIES"] + body + ["0", "ENDSEC", "0", "EOF"]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_minimal_dxf_round_trips_to_one_block(tmp_path):
    path = _square_dxf(tmp_path)
    entities = read_dxf_entities(path)
    assert len(entities) == 4
    segments = entities_to_segments(entities)
    closed, open_chains = build_loops(segments)
    assert len(closed) == 1 and not open_chains
    area = 0.5 * abs(
        sum(closed[0][i][0] * closed[0][(i + 1) % 4][1] - closed[0][(i + 1) % 4][0] * closed[0][i][1]
            for i in range(4))
    )
    assert area == pytest.approx(100.0)


def test_arc_flattening_respects_the_sagitta_tolerance():
    entity = [{"type": "ARC", "10": ["0"], "20": ["0"], "40": ["50"], "50": ["0"], "51": ["90"]}]
    pts = np.asarray(entities_to_segments(entity, sagitta=0.02)[0])
    radii = np.hypot(pts[:, 0], pts[:, 1])
    assert np.allclose(radii, 50.0, atol=1e-9)
    # mid-chord sag must stay under tolerance
    mids = 0.5 * (pts[:-1] + pts[1:])
    sag = 50.0 - np.hypot(mids[:, 0], mids[:, 1])
    assert sag.max() < 0.021


def test_seam_wrapping_joins_two_halves_of_one_block():
    circ = 100.0
    left = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    right = [(95.0, 5.0), (100.0, 5.0), (100.0, 0.0), (95.0, 0.0)]
    # open chains as the stitcher would leave them: ends on the two seams
    a = [(0.0, 5.0), (10.0, 5.0), (10.0, 0.0), (0.0, 0.0)]
    b = [(100.0, 0.0), (95.0, 0.0), (95.0, 5.0), (100.0, 5.0)]
    loops, leftover = close_across_seam([a, b], circ)
    assert len(loops) == 1 and not leftover
    xs = [p[0] for p in loops[0]]
    assert min(xs) < 0.0  # the wrapped half sits at negative x


def test_unrelated_open_chains_are_not_fused():
    a = [(0.0, 5.0), (10.0, 5.0)]
    b = [(100.0, 40.0), (90.0, 40.0)]  # nowhere near a's lateral position
    loops, leftover = close_across_seam([a, b], 100.0)
    assert not loops and len(leftover) == 2


@needs_dxf
def test_tramplr_dxf_imports_cleanly():
    pattern, report = load_pattern(TRAMPLR, BlockDefaults(height=8.5, draft_angle=3.0))
    assert report.n_discarded_open == 0
    assert len(pattern.blocks) == 168
    assert report.n_wrapped == 3
    assert pattern.tyre_circumference == pytest.approx(2193.403, abs=0.01)
    assert pattern.tread_width == pytest.approx(159.0, abs=0.01)
    assert 0.6 < report.land_ratio < 0.75
    assert pattern.validate() == []


@needs_dxf
def test_tramplr_geometric_repeat_is_detected():
    pattern, _ = load_pattern(TRAMPLR, BlockDefaults(height=8.5))
    repeat = pattern.meta["geometric_repeat_mm"]
    assert repeat == pytest.approx(182.784, abs=0.01)
    assert pattern.tyre_circumference / repeat == pytest.approx(12.0, abs=1e-3)
    assert pattern.meta["uniform_array"] is True


@needs_dxf
def test_dxf_block_attributes_come_from_the_caller():
    defaults = BlockDefaults(
        height=9.0, draft_angle=4.0,
        height_by_zone={"shoulder": 7.0}, sipes_by_zone={"center": 2},
    )
    pattern, _ = load_pattern(TRAMPLR, defaults)
    shoulder = [b for b in pattern.blocks if b.zone == "shoulder"]
    center = [b for b in pattern.blocks if b.zone == "center"]
    assert all(b.height == 7.0 for b in shoulder)
    assert all(b.height == 9.0 for b in center)
    assert all(b.n_lateral_sipes == 2 for b in center)
    # and the assumption is recorded, not silently baked in
    assert pattern.meta["assumed"]["height_mm"] == 9.0


@needs_dxf
def test_explicit_pitch_count_overrides_inference():
    pattern, _ = load_pattern(TRAMPLR, BlockDefaults(height=8.5), n_pitches=12)
    assert len(pattern.pitches) == 12
    assert pattern.pitches[0].circumferential_length == pytest.approx(2193.403 / 12, abs=0.01)


# ----------------------------------------------------- contact patch shapes
def _crown():
    return CrownProfile.dual_radius(159.0)


def test_patch_accepts_an_arbitrary_shape():
    """A deliberately lumpy, concave outline must rasterise and measure sanely."""
    t = np.linspace(0, 2 * np.pi, 120, endpoint=False)
    r = 30.0 + 6.0 * np.cos(5 * t)  # five-lobed star -- strongly concave
    outline = np.column_stack([r * np.cos(t), r * np.sin(t)])
    patch = measured_patch(0.0, outline, tread_width=159.0)
    assert patch.area() > 0
    assert patch.contains(np.array([0.0]), np.array([0.0]))[0]
    # a point in a notch between lobes is outside
    far = 34.0
    ang = np.pi / 5  # between lobes
    assert not patch.contains(np.array([far * math.cos(ang)]), np.array([far * math.sin(ang)]))[0]


def test_arbitrary_shape_rasterises_to_the_right_area():
    from tread_eval.raster import Grid

    grid = Grid(nx=2000, ny=400, dx=0.25, dy=0.25, circumference=500.0, tread_width=100.0)
    outline = superellipse_outline(40.0, 20.0, 0.0, exponent=4.0, n=361)
    patch = measured_patch(0.0, outline, tread_width=100.0)
    binary, _ = patch.masks(grid)
    assert float(binary.sum()) * grid.pixel_area == pytest.approx(patch.area(), rel=5e-3)


def test_patch_mask_wraps_around_the_seam():
    from tread_eval.raster import Grid

    grid = Grid(nx=400, ny=100, dx=1.0, dy=1.0, circumference=400.0, tread_width=100.0)
    patch = measured_patch(0.0, ellipse_outline(30.0, 15.0, 0.0), tread_width=100.0)
    binary, _ = patch.masks(grid)
    # the patch straddles column 0, so both the first and last columns are lit
    assert binary[:, 0].any() and binary[:, -1].any()
    assert not binary[:, grid.nx // 2].any()


def test_pressure_grid_sampling_and_load():
    x = np.linspace(-40, 40, 81)
    y = np.linspace(-20, 20, 41)
    xx, yy = np.meshgrid(x, y)
    p = np.maximum(0.0, 1.0 - (xx / 40) ** 2 - (yy / 20) ** 2)
    grid = PressureGrid(x=x, y=y, p=p)
    assert grid.sample(np.array([0.0]), np.array([0.0]))[0, 0] == pytest.approx(1.0)
    assert grid.sample(np.array([100.0]), np.array([0.0]))[0, 0] == 0.0
    # integral of the paraboloid over the ellipse is pi*a*b/2
    assert grid.total_load() == pytest.approx(math.pi * 40 * 20 / 2, rel=0.02)


def test_pressure_map_gives_an_outline():
    x = np.linspace(-30, 30, 61)
    y = np.linspace(-15, 15, 31)
    xx, yy = np.meshgrid(x, y)
    p = np.where((xx / 25) ** 2 + (yy / 12) ** 2 < 1, 0.5, 0.0)
    hull = PressureGrid(x=x, y=y, p=p).outline()
    assert len(hull) >= 3
    assert np.ptp(hull[:, 0]) == pytest.approx(50, abs=3)
    assert np.ptp(hull[:, 1]) == pytest.approx(24, abs=3)


# ----------------------------------------------------- generators
def test_rhyne_patch_area_follows_load_over_inflation():
    crown = _crown()
    params = CPParams(vertical_load=1500.0, inflation_kpa=250.0, tyre_type="2w",
                      load_rises_with_lean=False)
    p = rhyne_patch(0.0, crown, params, 159.0)
    expected = 1500.0 / (0.250 * 1.10)
    assert p.meta["nominal_area_mm2"] == pytest.approx(expected, rel=1e-9)
    # doubling the load doubles the nominal area
    p2 = rhyne_patch(0.0, crown, CPParams(vertical_load=3000.0, inflation_kpa=250.0,
                                          tyre_type="2w", load_rises_with_lean=False), 159.0)
    assert p2.meta["nominal_area_mm2"] == pytest.approx(2 * expected, rel=1e-9)


def test_rhyne_and_winkler_both_narrow_with_lean():
    crown = _crown()
    for gen in (winkler_patch, rhyne_patch):
        up = gen(0.0, crown, DEFAULT := CPParams(), 159.0)
        lean = gen(40.0, crown, DEFAULT, 159.0)
        assert lean.width() < up.width()
        assert lean.centroid()[1] > up.centroid()[1]


# ----------------------------------------------------- fallback chain
def test_transfer_keeps_the_measured_shape_and_only_scales_it():
    crown = _crown()
    t = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    r = 20.0 + 4.0 * np.cos(3 * t)  # a distinctly non-elliptical shape
    base = measured_patch(0.0, np.column_stack([r * np.cos(t), r * np.sin(t)]), 159.0)
    # 20 deg keeps the whole patch on the tread; see the clipping test below for
    # what happens when it does not.
    moved = transferred_patch(base, 20.0, crown, CPParams(), 159.0)

    assert moved.source == "transferred"
    assert "0 deg" in moved.provenance

    # The transfer is a pure anisotropic scale plus a lateral shift, and nothing
    # else -- so undoing the recorded scaling must recover the measured outline
    # vertex for vertex. That is the whole claim: the shape is measured data and
    # only its scaling with lean is modelled.
    sx, sy = moved.meta["scale_x"], moved.meta["scale_y"]
    assert sx != pytest.approx(sy)  # genuinely anisotropic, not a plain zoom
    undone = moved.outline.copy()
    undone[:, 0] /= sx
    undone[:, 1] = (undone[:, 1] - moved.y_center) / sy + base.centroid()[1]
    assert np.allclose(undone, base.outline, atol=1e-6)

    # and it has in fact moved outboard
    assert moved.centroid()[1] > base.centroid()[1] + 10
    assert not moved.clipped


def test_transfer_to_extreme_lean_is_clipped_and_says_so():
    """Past a point the patch runs off the tread, and that must be visible."""
    crown = _crown()
    base = measured_patch(0.0, ellipse_outline(45.0, 30.0, 0.0), 159.0)
    moved = transferred_patch(base, 40.0, crown, CPParams(), 159.0)
    half = 159.0 / 2
    assert moved.clipped
    assert moved.outline[:, 1].max() <= half + 1e-9
    # a genuine flat run along the tread edge, not a single tangent point
    on_edge = np.count_nonzero(np.isclose(moved.outline[:, 1], half))
    assert on_edge > 3
    # and it carries less area than the same patch would on an infinitely wide tread
    unclipped = transferred_patch(base, 40.0, crown, CPParams(), 1000.0)
    assert moved.area() < unclipped.area()


def test_interpolated_patch_sits_between_its_neighbours():
    a = measured_patch(0.0, ellipse_outline(40.0, 25.0, 0.0), 159.0)
    b = measured_patch(40.0, ellipse_outline(50.0, 15.0, 60.0), 159.0)
    mid = interpolated_patch(a, b, 20.0, 159.0)
    assert mid.source == "interpolated"
    assert a.width() > mid.width() > b.width()
    assert a.centroid()[1] < mid.centroid()[1] < b.centroid()[1]


def test_library_prefers_measured_then_interpolates_then_transfers_then_generates():
    crown = _crown()
    lib = CPLibrary(crown=crown, tread_width=159.0, params=CPParams())

    # nothing measured -> generated everywhere
    assert lib.patch_for(0.0).source == "winkler"
    assert lib.patch_for(30.0).source == "winkler"

    lib.add_measured(measured_patch(0.0, ellipse_outline(40.0, 25.0, 0.0), 159.0))
    assert lib.patch_for(0.0).source == "measured"
    assert lib.patch_for(0.3).source == "measured"  # within the match tolerance
    assert lib.patch_for(30.0).source == "transferred"

    lib.add_measured(measured_patch(40.0, ellipse_outline(48.0, 16.0, 62.0), 159.0))
    assert lib.patch_for(20.0).source == "interpolated"
    assert lib.patch_for(40.0).source == "measured"


def test_transfer_can_be_disabled():
    lib = CPLibrary(crown=_crown(), tread_width=159.0, params=CPParams(), allow_transfer=False)
    lib.add_measured(measured_patch(0.0, ellipse_outline(40.0, 25.0, 0.0), 159.0))
    assert lib.patch_for(0.0).source == "measured"
    assert lib.patch_for(30.0).source == "winkler"


def test_library_coverage_reports_every_lean_angle():
    lib = CPLibrary(crown=_crown(), tread_width=159.0, params=CPParams())
    lib.add_measured(measured_patch(0.0, ellipse_outline(40.0, 25.0, 0.0), 159.0))
    rows = lib.coverage([0.0, 15.0, 30.0])
    assert [r["source"] for r in rows] == ["measured", "transferred", "transferred"]
    assert rows[0]["measured"] is True and rows[1]["measured"] is False
    assert all(r["provenance"] for r in rows)


# ----------------------------------------------------- import I/O
def test_outline_csv_import_with_header_and_units(tmp_path):
    path = tmp_path / "fp.csv"
    path.write_text("x_mm,y_mm\n-4,0\n4,0\n4,2\n-4,2\n")
    pts = load_outline_csv(str(path))
    assert pts.shape == (4, 2)
    assert np.ptp(pts[:, 0]) == pytest.approx(8.0)
    # units convert
    assert np.ptp(load_outline_csv(str(path), units="cm")[:, 0]) == pytest.approx(80.0)


def test_manifest_import_builds_a_library(tmp_path):
    for gamma, (a, b, yc) in {0: (40, 25, 0), 30: (46, 18, 55)}.items():
        out = ellipse_outline(a, b, yc, 60)
        (tmp_path / f"fp{gamma}.csv").write_text(
            "\n".join(f"{x:.4f},{y:.4f}" for x, y in out)
        )
    manifest = {
        "units": "mm",
        "patches": [
            {"gamma_deg": 0, "outline": "fp0.csv", "load_N": 1500},
            {"gamma_deg": 30, "outline": "fp30.csv", "load_N": 1700},
        ],
    }
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(manifest))

    result = load_cp_manifest(str(mpath), tread_width=159.0)
    assert len(result.patches) == 2
    assert [p.gamma_deg for p in result.patches] == [0.0, 30.0]
    assert result.patches[0].normal_load == 1500

    lib = build_library(_crown(), 159.0, CPParams(), imported=result)
    assert lib.patch_for(0.0).source == "measured"
    assert lib.patch_for(15.0).source == "interpolated"
    assert lib.patch_for(45.0).source == "transferred"


def test_import_of_a_single_outline_places_it_at_the_given_lean(tmp_path):
    path = tmp_path / "fp.csv"
    path.write_text("\n".join(f"{x:.4f},{y:.4f}" for x, y in ellipse_outline(40, 25, 0, 60)))
    res = load_cp_any(str(path), tread_width=159.0, gamma_deg=15.0, load_n=1600.0)
    assert len(res.patches) == 1
    assert res.patches[0].gamma_deg == 15.0
    assert res.patches[0].normal_load == 1600.0


def test_imported_outline_is_recentred_circumferentially(tmp_path):
    """The importer must not care where the origin of the scan was."""
    shifted = ellipse_outline(40, 25, 0, 60) + np.array([137.0, 0.0])
    path = tmp_path / "fp.csv"
    path.write_text("\n".join(f"{x:.4f},{y:.4f}" for x, y in shifted))
    patch = load_cp_any(str(path), tread_width=159.0).patches[0]
    assert patch.centroid()[0] == pytest.approx(0.0, abs=1e-6)


# ----------------------------------------------------- end to end
@needs_dxf
def test_sweep_runs_with_an_imported_patch():
    from tread_eval.synthetic import SyntheticParams, generate_pattern

    pattern = generate_pattern(SyntheticParams(seed=2))
    pack = rasterise(pattern, make_grid(pattern, 0.8))
    lib = CPLibrary(crown=pattern.crown(), tread_width=pattern.tread_width, params=CPParams())
    lib.add_measured(
        measured_patch(0.0, superellipse_outline(45.0, 24.0, 0.0, 3.5), pattern.tread_width,
                       normal_load=1500.0)
    )
    r0 = sweep_lean(pattern, pack, 0.0, library=lib)
    r30 = sweep_lean(pattern, pack, 30.0, library=lib)
    assert r0.patch.source == "measured"
    assert r30.patch.source == "transferred"
    assert r0.contact_area.mean() > 0
    assert r0.contact_area.max() <= r0.patch_area * 1.001
