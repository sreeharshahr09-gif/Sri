"""DXF ingestion.

Reads closed curves (LWPOLYLINE / POLYLINE / SPLINE / ARC+LINE loops /
CIRCLE / ELLIPSE) from a DXF, grouped by layer name == component name, and
flattens them to point loops using tolerance-based flattening (NOT a fixed
segment count -- see CLAUDE.md: fixed-nseg sampling is the fragile approach we
deliberately avoid).

Regions / ACIS blobs are intentionally unsupported: export raw curves, not
REGIONs, or ezdxf/shapely cannot read the geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf import path as ezpath

# Flattening tolerance in drawing units (mm). Tight enough that curved bulges
# and splines are captured accurately; adaptive, so straight edges stay cheap.
DEFAULT_FLATTEN_TOL = 0.01

# Entity types we know how to turn into a closed loop.
_SUPPORTED = {
    "LWPOLYLINE",
    "POLYLINE",
    "SPLINE",
    "CIRCLE",
    "ELLIPSE",
    "ARC",
    "LINE",
}


@dataclass
class ComponentLoop:
    """One closed boundary loop belonging to a component (layer)."""

    layer: str
    coords: list[tuple[float, float]]
    source: str  # entity dxftype that produced it
    closed: bool = True


@dataclass
class LoadWarning:
    layer: str
    message: str


@dataclass
class LoadResult:
    loops: list[ComponentLoop] = field(default_factory=list)
    warnings: list[LoadWarning] = field(default_factory=list)
    skipped_acis: bool = False

    def by_layer(self) -> dict[str, list[ComponentLoop]]:
        out: dict[str, list[ComponentLoop]] = {}
        for loop in self.loops:
            out.setdefault(loop.layer, []).append(loop)
        return out


def _flatten_entity(entity, tol: float) -> list[tuple[float, float]] | None:
    """Flatten a single entity to a 2D point list, or None if unsupported."""
    dxftype = entity.dxftype()
    if dxftype not in _SUPPORTED:
        return None
    try:
        p = ezpath.make_path(entity)
    except (TypeError, ValueError):
        return None
    pts = [(v.x, v.y) for v in p.flattening(distance=tol)]
    return pts if len(pts) >= 2 else None


def _close(pts: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], bool]:
    """Ensure the loop is explicitly closed; report whether it already was."""
    if len(pts) < 3:
        return pts, False
    first, last = pts[0], pts[-1]
    already = abs(first[0] - last[0]) < 1e-9 and abs(first[1] - last[1]) < 1e-9
    if not already:
        pts = pts + [first]
    return pts, already


def load_dxf(
    filename: str | Path,
    flatten_tol: float = DEFAULT_FLATTEN_TOL,
    join_loose_edges: bool = True,
) -> LoadResult:
    """Load *filename* and return flattened closed loops grouped by layer.

    Each closed curve becomes a :class:`ComponentLoop`. Loose ARC/LINE
    segments on a layer (the raw-geometry case, before AutoCAD ``BOUNDARY``)
    are stitched into loops when *join_loose_edges* is True.
    """
    result = LoadResult()
    doc = ezdxf.readfile(str(filename))
    msp = doc.modelspace()

    loose_by_layer: dict[str, list] = {}

    for entity in msp:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer

        if dxftype in ("REGION", "3DSOLID", "BODY", "SURFACE"):
            # ACIS SAT/SAB -- not parseable without an ACIS kernel.
            result.skipped_acis = True
            result.warnings.append(
                LoadWarning(layer, f"Skipped {dxftype} (ACIS blob; export raw curves instead)")
            )
            continue

        if dxftype in ("ARC", "LINE"):
            # Defer: may be part of a loose boundary that needs stitching.
            loose_by_layer.setdefault(layer, []).append(entity)
            continue

        pts = _flatten_entity(entity, flatten_tol)
        if pts is None:
            continue
        pts, _ = _close(pts)
        if len(pts) >= 4:  # 3 distinct points + closing point
            result.loops.append(ComponentLoop(layer=layer, coords=pts, source=dxftype))

    if join_loose_edges:
        for layer, entities in loose_by_layer.items():
            _stitch_loose(layer, entities, flatten_tol, result)

    return result


def _stitch_loose(layer, entities, tol, result: LoadResult) -> None:
    """Stitch loose ARC/LINE segments on a layer into closed loops."""
    segments: list[list[tuple[float, float]]] = []
    for e in entities:
        pts = _flatten_entity(e, tol)
        if pts:
            segments.append(pts)
    if not segments:
        return

    loops = _chain_segments(segments)
    made_loop = False
    for loop in loops:
        loop, closed = _close(loop)
        if closed and len(loop) >= 4:
            result.loops.append(ComponentLoop(layer=layer, coords=loop, source="ARC/LINE"))
            made_loop = True
    if not made_loop:
        result.warnings.append(
            LoadWarning(layer, "Loose ARC/LINE segments did not form a closed loop; skipped")
        )


def _chain_segments(
    segments: list[list[tuple[float, float]]], tol: float = 1e-6
) -> list[list[tuple[float, float]]]:
    """Greedily chain segments end-to-end into polylines."""

    def close(a, b):
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    remaining = [list(s) for s in segments]
    chains: list[list[tuple[float, float]]] = []

    while remaining:
        chain = remaining.pop(0)
        extended = True
        while extended:
            extended = False
            for i, seg in enumerate(remaining):
                if close(chain[-1], seg[0]):
                    chain.extend(seg[1:]); remaining.pop(i); extended = True; break
                if close(chain[-1], seg[-1]):
                    chain.extend(reversed(seg[:-1])); remaining.pop(i); extended = True; break
                if close(chain[0], seg[-1]):
                    chain[:0] = seg[:-1]; remaining.pop(i); extended = True; break
                if close(chain[0], seg[0]):
                    chain[:0] = list(reversed(seg[1:])); remaining.pop(i); extended = True; break
        chains.append(chain)
    return chains
