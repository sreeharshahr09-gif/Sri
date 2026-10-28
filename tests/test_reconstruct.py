"""Tests for the manual-loop-builder reconstruction path.

Builds a synthetic 'full symmetric section' DXF (two mirrored component
rectangles either side of a centreline) with everything on layer 0 as OPEN
segments, then checks that reconstruction recovers the faces and that assembling
+ analysing one half gives correct Pappus numbers.
"""

import math

import ezdxf
import pytest

from tyre_analysis.reconstruct import reconstruct, assemble_component
from tyre_analysis.pipeline import analyse_components


def _open_rect(msp, x0, y0, x1, y1):
    """Draw a rectangle as four separate LINE entities (not a closed polyline)."""
    for a, b in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
        msp.add_line(a, b)


@pytest.fixture
def full_section(tmp_path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    axis = 100.0
    # right-half component: rectangle x in [120,140], y in [0,10]
    _open_rect(msp, 120, 0, 140, 10)
    # mirrored left-half component: x in [60,80]
    _open_rect(msp, 60, 0, 80, 10)
    # a drawn vertical centreline at x=100
    msp.add_line((100, -5), (100, 20))
    path = tmp_path / "full.dxf"
    doc.saveas(path)
    return path, axis


def test_reconstruct_finds_faces(full_section):
    path, axis = full_section
    rec = reconstruct(path, min_area=0.5)
    # two component rectangles -> at least two faces
    assert len(rec.faces) >= 2
    # axis suggestion near the drawn centreline
    assert abs(rec.suggested_axis_x - axis) < 2.0
    right = rec.faces_on_side(axis, "right")
    left = rec.faces_on_side(axis, "left")
    assert len(right) >= 1 and len(left) >= 1


def test_assemble_and_analyse_one_half(full_section):
    path, axis = full_section
    rec = reconstruct(path, min_area=0.5)
    right = rec.faces_on_side(axis, "right")
    # largest right face is the 20x10 rectangle
    face = max(right, key=lambda f: f.area)
    poly = assemble_component(rec.faces, [face.id])
    df, geoms, warns = analyse_components("t", {"COMP": poly}, axis_x=axis, sg_table={"COMP": 1.0})
    row = df.iloc[0]
    # rectangle x in [120,140] -> area 200, centroid x=130, dist=30
    assert row["area_mm2"] == pytest.approx(200.0, rel=1e-6)
    assert row["centroid_distance_mm"] == pytest.approx(30.0, rel=1e-6)
    # Pappus volume = 2*pi*30*200 mm3 -> cm3
    vol_mm3 = 2 * math.pi * 30 * 200
    assert row["volume_cm3"] == pytest.approx(vol_mm3 / 1000.0, rel=1e-6)
    assert not row["axis_crosses"]


def test_both_sides_crosses_axis(full_section):
    """Assembling faces from both halves must flag an axis crossing."""
    path, axis = full_section
    rec = reconstruct(path, min_area=0.5)
    faces = sorted(rec.faces, key=lambda f: f.area, reverse=True)[:2]
    poly = assemble_component(rec.faces, [f.id for f in faces])
    df, _, warns = analyse_components("t", {"BOTH": poly}, axis_x=axis)
    assert df.iloc[0]["axis_crosses"]
    assert any("axis" in w for w in warns)
