"""Reconstruct geometry from a non-conforming DXF for manual loop building.

When a drawing does not follow the one-closed-curve-per-named-layer convention
(e.g. everything on layer ``0`` as open lines/arcs/splines), we cannot key on
layers. Instead we:

1. flatten every curve,
2. node them together and ``polygonize`` into candidate closed **faces**
   (the same set AutoCAD's BOUNDARY "pick internal point" would offer),
3. let the user pick which faces make up each component (in the UI),
4. union the picked faces into a component polygon and feed it to the normal
   geometry/physics pipeline.

This module is the non-UI half: parsing, face extraction, axis detection, and
face assembly. Selection/naming happens in the Streamlit page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import polygonize, unary_union

_SKIP = {"MTEXT", "TEXT", "DIMENSION", "INSERT", "HATCH", "LEADER"}


@dataclass
class Face:
    id: int
    polygon: Polygon
    area: float
    rep_x: float
    rep_y: float
    cx: float
    cy: float


@dataclass
class Reconstruction:
    filename: str
    curves: list[list[tuple[float, float]]]  # flattened raw curves (for drawing)
    faces: list[Face]
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    suggested_axis_x: float
    vertical_axis_lines: list[float] = field(default_factory=list)

    def faces_on_side(self, axis_x: float, side: str) -> list[Face]:
        """Faces whose representative point is on one side of the axis.

        *side* is ``"right"`` (x > axis), ``"left"`` (x < axis) or ``"both"``.
        """
        if side == "both":
            return self.faces
        if side == "right":
            return [f for f in self.faces if f.rep_x > axis_x]
        return [f for f in self.faces if f.rep_x < axis_x]


def _flatten_curves(msp, tol: float) -> list[list[tuple[float, float]]]:
    curves = []
    for e in msp:
        if e.dxftype() in _SKIP:
            continue
        try:
            p = ezpath.make_path(e)
            pts = [(v.x, v.y) for v in p.flattening(tol)]
        except Exception:
            continue
        if len(pts) >= 2:
            curves.append(pts)
    return curves


def _detect_vertical_axes(msp, tol: float = 0.5) -> list[float]:
    """X-coordinates of near-vertical LINE entities (candidate rotation axes)."""
    xs = []
    for e in msp.query("LINE"):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.x - t.x) <= tol and abs(t.y - s.y) > tol:
            xs.append(round((s.x + t.x) / 2, 3))
    return sorted(set(xs))


def reconstruct(
    filename: str | Path,
    flatten_tol: float = 0.05,
    min_area: float = 1.0,
    dedupe: bool = True,
) -> Reconstruction:
    """Parse *filename* and extract candidate faces for manual assignment."""
    doc = ezdxf.readfile(str(filename))
    msp = doc.modelspace()

    curves = _flatten_curves(msp, flatten_tol)
    lines = [LineString(c) for c in curves if len(c) >= 2]
    merged = unary_union(lines)  # nodes all intersections
    raw_faces = list(polygonize(merged))

    kept: list[Polygon] = []
    seen: set[tuple] = set()
    for poly in raw_faces:
        if poly.area < min_area:
            continue
        if dedupe:
            key = (round(poly.area, 2), round(poly.centroid.x, 1), round(poly.centroid.y, 1))
            if key in seen:
                continue
            seen.add(key)
        kept.append(poly)

    kept.sort(key=lambda p: p.area, reverse=True)
    faces = []
    for i, poly in enumerate(kept):
        rp = poly.representative_point()
        c = poly.centroid
        faces.append(Face(id=i, polygon=poly, area=poly.area,
                          rep_x=rp.x, rep_y=rp.y, cx=c.x, cy=c.y))

    xs = [x for c in curves for x, _ in c]
    ys = [y for c in curves for _, y in c]
    xmin, xmax = (min(xs), max(xs)) if xs else (0.0, 0.0)
    ymin, ymax = (min(ys), max(ys)) if ys else (0.0, 0.0)

    vaxes = _detect_vertical_axes(msp)
    mid = (xmin + xmax) / 2
    # Prefer a drawn vertical line near the mid-section; else the mid-section.
    suggested = min(vaxes, key=lambda x: abs(x - mid)) if vaxes else mid

    return Reconstruction(
        filename=str(Path(filename).name),
        curves=curves,
        faces=faces,
        xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
        suggested_axis_x=suggested,
        vertical_axis_lines=vaxes,
    )


def assemble_component(faces: list[Face], face_ids: list[int]) -> Polygon | MultiPolygon:
    """Union the chosen faces into one component polygon.

    Unioning merges adjacent picked faces into a single region and fills shared
    edges; disjoint picks yield a MultiPolygon.
    """
    by_id = {f.id: f for f in faces}
    polys = [by_id[i].polygon for i in face_ids if i in by_id]
    if not polys:
        raise ValueError("No faces selected")
    merged = unary_union(polys)
    if merged.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Selected faces did not union into an area ({merged.geom_type})")
    return merged
