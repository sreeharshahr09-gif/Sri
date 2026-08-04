"""Geometry: block measures, crown profile, rasterisation."""

import math

import numpy as np
import pytest

from tread_eval.raster import Grid, make_grid, rasterise, rasterise_polygon, split_at_seam
from tread_eval.schema import Block, CrownProfile, Pattern, Pitch, classify_zone, zone_bounds
from tread_eval.synthetic import SyntheticParams, generate_pattern


def square(x0, y0, s, **kw):
    kw.setdefault("id", "b")
    kw.setdefault("pitch_id", "p")
    kw.setdefault("zone", "center")
    kw.setdefault("height", 8.0)
    kw.setdefault("draft_angle", 3.0)
    kw.setdefault("nsd", 1.0)
    return Block(polygon=[(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)], **kw)


# --- block measures ---------------------------------------------------
def test_block_area_and_centroid():
    b = square(10.0, -4.0, 6.0)
    assert b.area() == pytest.approx(36.0)
    cx, cy = b.centroid()
    assert (cx, cy) == pytest.approx((13.0, -1.0))
    assert b.extents() == pytest.approx((6.0, 6.0))


def test_block_area_is_orientation_independent():
    b = square(0.0, 0.0, 3.0)
    reversed_b = Block(
        id="r", pitch_id="p", polygon=list(reversed(b.polygon)), zone="center",
        height=8.0, draft_angle=3.0, nsd=1.0,
    )
    assert reversed_b.area() == pytest.approx(b.area())


# --- crown profile ----------------------------------------------------
def test_crown_tangent_angle_matches_radius_integral():
    """phi(y) must be the integral of 1/R along arc length, not atan(dz/dy).

    Getting this wrong makes the lateral radius rise toward the shoulder
    instead of falling, which inverts the whole lean model.
    """
    w, r = 160.0, 100.0
    crown = CrownProfile.from_radius_profile(np.linspace(-w / 2, w / 2, 801), np.full(801, r))
    for y in (10.0, 30.0, 60.0):
        assert float(crown.tangent_angle(y)) == pytest.approx(y / r, rel=1e-3)
        assert float(crown.local_radius(y)) == pytest.approx(r, rel=1e-6)


def test_constant_radius_crown_is_a_circle():
    r = 90.0
    crown = CrownProfile.from_radius_profile(np.linspace(-70, 70, 1201), np.full(1201, r))
    y = 50.0
    # Arc-length parametrisation of a circle: projected y = R sin(s/R), drop = R(1-cos(s/R)).
    assert float(crown.projected(y)) == pytest.approx(r * math.sin(y / r), rel=1e-4)
    assert float(crown.drop(y)) == pytest.approx(r * (1 - math.cos(y / r)), rel=1e-3)


def test_contact_point_walks_outboard_with_lean():
    crown = CrownProfile.dual_radius(159.0)
    positions = [crown.contact_lateral_position(g) for g in (0, 10, 20, 30, 40)]
    assert positions[0] == pytest.approx(0.0, abs=1e-6)
    assert all(b > a for a, b in zip(positions, positions[1:]))
    # and it is the exact inverse of the tangent angle
    for g in (5.0, 17.0, 33.0):
        y = crown.contact_lateral_position(g)
        assert math.degrees(float(crown.tangent_angle(y))) == pytest.approx(g, abs=0.05)


def test_lateral_radius_falls_toward_the_shoulder():
    crown = CrownProfile.dual_radius(159.0, r_center=125.0, r_shoulder=55.0)
    assert float(crown.local_radius(0.0)) > float(crown.local_radius(70.0))
    assert float(crown.local_radius(0.0)) == pytest.approx(125.0, rel=0.02)


def test_arc_length_factor_is_one_at_the_crown_and_grows_outboard():
    crown = CrownProfile.dual_radius(159.0)
    assert float(crown.arc_length_factor(0.0)) == pytest.approx(1.0, abs=1e-6)
    assert float(crown.arc_length_factor(70.0)) > 1.2
    assert float(CrownProfile.flat(159.0).arc_length_factor(70.0)) == pytest.approx(1.0, abs=1e-4)


def test_cross_section_round_trip():
    """A profile built from radii, re-measured from its own projected trace."""
    ref = CrownProfile.dual_radius(150.0, r_center=110.0, r_shoulder=60.0)
    rebuilt = CrownProfile.from_cross_section(ref.y_proj, ref.z)
    assert rebuilt.measured
    for y in (0.0, 25.0, 55.0):
        assert float(rebuilt.tangent_angle(y)) == pytest.approx(float(ref.tangent_angle(y)), abs=0.01)


# --- rasterisation ----------------------------------------------------
def _grid(nx=800, ny=200, circ=400.0, width=100.0):
    return Grid(nx=nx, ny=ny, dx=circ / nx, dy=width / ny, circumference=circ, tread_width=width)


def _fill_area(poly, grid):
    rows, cols = rasterise_polygon(poly, grid)
    return float((cols[:, 1] - cols[:, 0]).sum()) * grid.pixel_area if len(rows) else 0.0


def test_rasterised_area_matches_analytic():
    grid = _grid()
    poly = [(50.0, -20.0), (110.0, -20.0), (110.0, 15.0), (50.0, 15.0)]
    assert _fill_area(poly, grid) == pytest.approx(60.0 * 35.0, rel=2e-3)


def test_rasterised_triangle_area():
    grid = _grid()
    poly = [(20.0, -30.0), (140.0, -30.0), (80.0, 20.0)]
    assert _fill_area(poly, grid) == pytest.approx(0.5 * 120.0 * 50.0, rel=5e-3)


def test_seam_split_preserves_area():
    circ = 400.0
    poly = [(390.0, -10.0), (410.0, -10.0), (410.0, 10.0), (390.0, 10.0)]
    pieces = split_at_seam(poly, circ)
    assert len(pieces) == 2
    total = 0.0
    for p in pieces:
        arr = np.asarray(p)
        assert arr[:, 0].min() >= -1e-9 and arr[:, 0].max() <= circ + 1e-9
        x, y = arr[:, 0], arr[:, 1]
        total += 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    assert total == pytest.approx(20.0 * 20.0)


def test_polygon_entirely_inside_is_not_split():
    poly = [(10.0, 0.0), (30.0, 0.0), (30.0, 5.0)]
    assert split_at_seam(poly, 400.0) == [poly]


def test_pack_land_area_matches_polygon_area():
    pattern = generate_pattern(SyntheticParams(seed=7))
    pack = rasterise(pattern, make_grid(pattern, 0.3))
    assert float(pack.area.sum()) == pytest.approx(pattern.land_area(), rel=5e-3)


def test_curvature_correction_only_inflates_shoulder_area():
    pattern = generate_pattern(SyntheticParams(seed=7))
    grid = make_grid(pattern, 0.5)
    plain = rasterise(pattern, grid, curvature_correction=False)
    corrected = rasterise(pattern, grid, curvature_correction=True)
    assert float(corrected.area.sum()) > float(plain.area.sum())
    # centre rows are untouched, shoulder rows gain
    mid = grid.ny // 2
    assert corrected.lateral_weight[mid] == pytest.approx(1.0, abs=1e-3)
    assert corrected.lateral_weight[0] > 1.2
    assert corrected.zone_area["center"].sum() == pytest.approx(plain.zone_area["center"].sum(), rel=0.02)
    assert corrected.zone_area["shoulder"].sum() > 1.15 * plain.zone_area["shoulder"].sum()


# --- zoning -----------------------------------------------------------
def test_zone_bounds_are_contiguous_and_fraction_based():
    for width in (120.0, 159.0, 200.0):
        b = zone_bounds(width)
        assert b["center"][0] == 0.0
        assert b["center"][1] == b["intermediate"][0]
        assert b["intermediate"][1] == b["shoulder"][0]
        assert b["shoulder"][1] == pytest.approx(width / 2)
    # scaling the tyre scales the boundaries with it
    assert zone_bounds(200.0)["center"][1] == pytest.approx(2 * zone_bounds(100.0)["center"][1])
    assert classify_zone(1.0, 159.0) == "center"
    assert classify_zone(79.0, 159.0) == "shoulder"


# --- generated pattern integrity --------------------------------------
def test_generated_pattern_validates_and_closes():
    for mode in ("random", "tonal_group", "uniform"):
        pattern = generate_pattern(SyntheticParams(pitch_mode=mode))
        assert pattern.validate() == []
        total = sum(p.circumferential_length for p in pattern.pitches)
        assert total == pytest.approx(pattern.tyre_circumference, rel=1e-9)
        assert 0.4 < pattern.land_area() / pattern.gross_area() < 0.85


def test_generator_is_deterministic():
    a = generate_pattern(SyntheticParams(seed=99))
    b = generate_pattern(SyntheticParams(seed=99))
    assert [x.polygon for x in a.blocks] == [x.polygon for x in b.blocks]
    assert generate_pattern(SyntheticParams(seed=1)).blocks[5].nsd != a.blocks[5].nsd


def test_pattern_json_round_trip(tmp_path):
    pattern = generate_pattern(SyntheticParams(seed=3))
    path = tmp_path / "p.json"
    pattern.save_json(str(path))
    back = Pattern.load_json(str(path))
    assert back.tyre_circumference == pytest.approx(pattern.tyre_circumference)
    assert len(back.blocks) == len(pattern.blocks)
    assert back.land_area() == pytest.approx(pattern.land_area())
    assert float(back.crown().local_radius(30.0)) == pytest.approx(float(pattern.crown().local_radius(30.0)))


def test_validate_catches_a_pitch_sequence_that_does_not_close():
    pattern = generate_pattern(SyntheticParams(seed=3))
    pattern.pitches.append(Pitch(id="extra", circumferential_start=0.0, circumferential_length=50.0))
    problems = pattern.validate()
    assert any("closure error" in p for p in problems)


def test_validate_catches_a_block_off_the_tread():
    pattern = generate_pattern(SyntheticParams(seed=3))
    pattern.blocks.append(square(0.0, pattern.tread_width, 5.0, id="oops"))
    assert any("exceeds tread width" in p for p in pattern.validate())
