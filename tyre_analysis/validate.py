"""DXF conformance checking.

The analysis core assumes the CAD convention from the project brief: each
component is ONE closed curve on its own layer (layer name == component name),
exported as raw curves (not REGION/ACIS), units in mm.

Real exports do not always follow this. This module inspects a DXF and reports
whether it conforms, so the UI can refuse to produce misleading numbers and
tell the user exactly what to fix -- rather than silently treating a soup of
open curves on layer ``0`` as a single component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath

_CLOSABLE_CLOSED = {"CIRCLE", "ELLIPSE"}  # inherently closed when full
_ACIS = {"REGION", "3DSOLID", "BODY", "SURFACE"}
_GEOMETRY = {"LWPOLYLINE", "POLYLINE", "SPLINE", "CIRCLE", "ELLIPSE", "ARC", "LINE"}


@dataclass
class LayerReport:
    name: str
    entity_counts: dict[str, int] = field(default_factory=dict)
    closed_loops: int = 0
    open_entities: int = 0


@dataclass
class ConformanceReport:
    filename: str
    dxf_version: str
    acad_release: str
    layers: dict[str, LayerReport] = field(default_factory=dict)
    has_acis: bool = False
    all_on_layer_zero: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def conforms(self) -> bool:
        return not self.problems

    @property
    def component_layers(self) -> list[str]:
        return [n for n in self.layers if n not in ("0", "Defpoints")]


def _is_closed(entity, tol: float = 1e-6) -> bool:
    dxftype = entity.dxftype()
    if dxftype == "CIRCLE":
        return True
    if dxftype == "ELLIPSE":
        return abs((entity.dxf.end_param - entity.dxf.start_param) - 6.283185307179586) < 1e-3
    if dxftype == "LWPOLYLINE":
        if entity.closed:
            return True
    if dxftype == "POLYLINE":
        if entity.is_closed:
            return True
    if dxftype == "SPLINE":
        if getattr(entity, "closed", False):
            return True
    try:
        p = ezpath.make_path(entity)
        pts = list(p.flattening(0.1))
        if len(pts) >= 3:
            a, b = pts[0], pts[-1]
            return abs(a.x - b.x) < tol and abs(a.y - b.y) < tol
    except Exception:
        pass
    return False


def validate_dxf(filename: str | Path) -> ConformanceReport:
    doc = ezdxf.readfile(str(filename))
    msp = doc.modelspace()
    rep = ConformanceReport(
        filename=str(Path(filename).name),
        dxf_version=doc.dxfversion,
        acad_release=doc.acad_release,
    )

    for entity in msp:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer
        if dxftype in _ACIS:
            rep.has_acis = True
            continue
        if dxftype not in _GEOMETRY:
            continue
        lr = rep.layers.setdefault(layer, LayerReport(name=layer))
        lr.entity_counts[dxftype] = lr.entity_counts.get(dxftype, 0) + 1
        if _is_closed(entity):
            lr.closed_loops += 1
        else:
            lr.open_entities += 1

    comp_layers = rep.component_layers
    rep.all_on_layer_zero = (
        not comp_layers
        and any(k in rep.layers for k in ("0", "Defpoints"))
        and any(rep.layers[k].entity_counts for k in rep.layers if k in ("0", "Defpoints"))
    )

    # DXF version: R2000 (AC1015) or later so arcs serialise as LWPOLYLINE+bulge
    if rep.dxf_version < "AC1015":
        rep.problems.append(
            f"DXF version {rep.dxf_version} is older than R2000 (AC1015); "
            "arcs may serialise as legacy POLYLINE/VERTEX chains."
        )

    if rep.has_acis:
        rep.problems.append(
            "File contains REGION/3DSOLID (ACIS) entities. These are binary blobs "
            "ezdxf/shapely cannot parse -- export components as raw closed curves, not REGIONs."
        )

    if rep.all_on_layer_zero:
        rep.problems.append(
            "All geometry is on layer '0' (no component layers). The tool identifies "
            "components by layer name -- put each component on its own named layer "
            "(TREAD, SIDEWALL, APEX, BELT-1, PLY-1, BEAD, ...)."
        )

    total_closed = 0
    for name in comp_layers:
        lr = rep.layers[name]
        total_closed += lr.closed_loops
        if lr.closed_loops == 0:
            rep.problems.append(
                f"Layer '{name}' has {lr.open_entities} open curve(s) but no closed loop. "
                "Run AutoCAD BOUNDARY to make one closed curve per component."
            )
        elif lr.open_entities > 0:
            rep.notes.append(
                f"Layer '{name}' mixes {lr.closed_loops} closed loop(s) with "
                f"{lr.open_entities} loose curve(s); loose ones will be ignored or stitched."
            )

    if not comp_layers and rep.layers:
        # already reported via all_on_layer_zero; add closure note if relevant
        z = rep.layers.get("0")
        if z and z.closed_loops == 0:
            rep.notes.append("No closed curves found anywhere -- nothing to compute area on.")

    return rep


def format_report(rep: ConformanceReport) -> str:
    """Human-readable one-block summary (used by CLI and Streamlit)."""
    lines = [f"File: {rep.filename}  ({rep.dxf_version} / {rep.acad_release})"]
    lines.append(f"Conforms: {'YES' if rep.conforms else 'NO'}")
    lines.append("Layers with geometry:")
    for name, lr in sorted(rep.layers.items()):
        counts = ", ".join(f"{k}:{v}" for k, v in sorted(lr.entity_counts.items()))
        lines.append(
            f"  {name!r}: [{counts}]  closed_loops={lr.closed_loops} open={lr.open_entities}"
        )
    if rep.problems:
        lines.append("Problems:")
        lines += [f"  - {p}" for p in rep.problems]
    if rep.notes:
        lines.append("Notes:")
        lines += [f"  - {n}" for n in rep.notes]
    return "\n".join(lines)
