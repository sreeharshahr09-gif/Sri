"""Analytic validation of the geometry / physics core.

A rectangle in x in [a, a+w], y in [0, h] revolved about the axis x=0 produces
a hollow cylinder (washer) of inner radius a, outer radius a+w, height h. Every
quantity the tool computes has a known closed form for this shape, so we check
against it directly -- no DXF needed.
"""

import math

import pytest

from tyre_analysis.geometry import compute_geometry
from tyre_analysis.physics import compute_physics


def rectangle(a, w, h):
    return [(a, 0.0), (a + w, 0.0), (a + w, h), (a, h), (a, 0.0)]


@pytest.mark.parametrize("a,w,h", [(50.0, 20.0, 10.0), (100.0, 5.0, 40.0)])
def test_rectangle_revolution(a, w, h):
    axis_x = 0.0
    R, r = a + w, a
    geom = compute_geometry("TEST", [rectangle(a, w, h)], axis_x)

    # Area & centroid
    assert geom.area_mm2 == pytest.approx(w * h, rel=1e-9)
    assert geom.centroid_x == pytest.approx(a + w / 2, rel=1e-9)
    assert geom.centroid_y == pytest.approx(h / 2, rel=1e-9)

    sg = 1.2  # g/cm3
    res = compute_physics(geom, axis_x, sg)

    # Volume of hollow cylinder: pi (R^2 - r^2) h, reported in cm3
    vol_mm3 = math.pi * (R ** 2 - r ** 2) * h
    assert res.volume_cm3 == pytest.approx(vol_mm3 / 1000.0, rel=1e-6)

    # Surface of revolution: two lateral cylinders + two annular washers
    surf = (
        2 * math.pi * R * h
        + 2 * math.pi * r * h
        + 2 * math.pi * (R ** 2 - r ** 2)
    )
    assert res.surface_area_mm2 == pytest.approx(surf, rel=1e-6)

    # Weight
    assert res.weight_g == pytest.approx(vol_mm3 / 1000.0 * sg, rel=1e-6)

    # Mass polar MOI of hollow cylinder: 1/2 m (R^2 + r^2)
    mass_g = vol_mm3 / 1000.0 * sg
    moi = 0.5 * mass_g * (R ** 2 + r ** 2)
    assert res.polar_moi_g_mm2 == pytest.approx(moi, rel=1e-6)

    # Radius of gyration: sqrt((R^2+r^2)/2)
    assert res.radius_of_gyration_mm == pytest.approx(math.sqrt((R ** 2 + r ** 2) / 2), rel=1e-6)


def test_winding_order_independence():
    """CW vs CCW input must not change area or moments (sign-flip guard)."""
    axis_x = 0.0
    ccw = rectangle(50.0, 20.0, 10.0)
    cw = list(reversed(ccw))
    g1 = compute_geometry("A", [ccw], axis_x)
    g2 = compute_geometry("A", [cw], axis_x)
    assert g1.area_mm2 == pytest.approx(g2.area_mm2, rel=1e-12)
    assert g1.r3_moment_mm5 == pytest.approx(g2.r3_moment_mm5, rel=1e-9)


def test_hole_subtracts():
    """An inner loop inside an outer loop is treated as a hole."""
    axis_x = 0.0
    outer = rectangle(50.0, 40.0, 40.0)
    inner = rectangle(60.0, 20.0, 20.0)  # fully inside outer
    geom = compute_geometry("H", [outer, inner], axis_x)
    assert geom.n_holes == 1
    assert geom.area_mm2 == pytest.approx(40 * 40 - 20 * 20, rel=1e-9)


def test_axis_crossing_flagged():
    axis_x = 60.0  # inside the rectangle x-range [50,70]
    geom = compute_geometry("X", [rectangle(50.0, 20.0, 10.0)], axis_x)
    res = compute_physics(geom, axis_x, 1.1)
    assert res.axis_crosses is True
