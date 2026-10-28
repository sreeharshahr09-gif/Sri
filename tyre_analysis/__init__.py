"""Tyre cross-section analysis toolkit.

Parses 2D DXF tyre cross-section profiles (one closed curve per component,
layer name == component name), and computes per-component area, centroid,
volume and surface area (Pappus's theorems), weight, and the mass polar
moment of inertia of the revolved solid.

See ``CLAUDE.md`` (project brief) for the full design rationale.
"""

from .dxf_ingest import load_dxf, ComponentLoop
from .geometry import ComponentGeometry, compute_geometry
from .sg_table import DEFAULT_SG_TABLE, lookup_sg
from .pipeline import analyse_files, analyse_loops, COLUMNS

__all__ = [
    "load_dxf",
    "ComponentLoop",
    "ComponentGeometry",
    "compute_geometry",
    "DEFAULT_SG_TABLE",
    "lookup_sg",
    "analyse_files",
    "analyse_loops",
    "COLUMNS",
]
