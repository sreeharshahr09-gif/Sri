"""End-to-end pipeline: DXF files -> one long tidy dataframe.

All comparisons/totals/deltas downstream are groupby/pivot operations on this
single table; nothing is special-cased per file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .dxf_ingest import load_dxf, ComponentLoop
from .geometry import compute_geometry, ComponentGeometry
from .physics import compute_physics, ComponentResult
from .sg_table import lookup_sg

COLUMNS = [
    "file",
    "component",
    "area_mm2",
    "centroid_x",
    "centroid_y",
    "centroid_distance_mm",
    "volume_cm3",
    "weight_g",
    "surface_area_mm2",
    "polar_MOI",
    "radius_of_gyration_mm",
    "sg",
    "n_holes",
    "axis_crosses",
    "sg_matched",
    "pct_of_total_weight",
]


@dataclass
class FileWarnings:
    file: str
    messages: list[str] = field(default_factory=list)


def _group_loops(loops: list[ComponentLoop]) -> dict[str, list[list[tuple[float, float]]]]:
    grouped: dict[str, list[list[tuple[float, float]]]] = {}
    for loop in loops:
        grouped.setdefault(loop.layer, []).append(loop.coords)
    return grouped


def analyse_loops(
    file_label: str,
    loops: list[ComponentLoop],
    axis_x: float,
    sg_table: dict[str, float] | None = None,
    fallback_sg: float | None = None,
) -> tuple[pd.DataFrame, list[ComponentGeometry], list[str]]:
    """Compute per-component results for one file's loops.

    Returns ``(dataframe, geometries, warnings)``. The dataframe holds one row
    per component with the columns in :data:`COLUMNS` (percentage filled in by
    the caller once all files are combined, but also computed per-file here).
    """
    warnings: list[str] = []
    rows: list[dict] = []
    geoms: list[ComponentGeometry] = []

    for layer, layer_loops in _group_loops(loops).items():
        try:
            geom = compute_geometry(layer, layer_loops, axis_x)
        except ValueError as exc:
            warnings.append(f"{layer}: {exc}")
            continue
        sg, matched = lookup_sg(layer, sg_table, fallback=fallback_sg if fallback_sg is not None else 1.10)
        if not matched:
            warnings.append(f"{layer}: no SG table entry; using fallback {sg} g/cm3")
        res: ComponentResult = compute_physics(geom, axis_x, sg, matched)
        if res.axis_crosses:
            warnings.append(
                f"{layer}: region crosses the rotation axis -- Pappus volume/surface/MOI are invalid"
            )
        geoms.append(geom)
        rows.append(
            {
                "file": file_label,
                "component": layer,
                "area_mm2": res.area_mm2,
                "centroid_x": res.centroid_x,
                "centroid_y": res.centroid_y,
                "centroid_distance_mm": res.centroid_distance_mm,
                "volume_cm3": res.volume_cm3,
                "weight_g": res.weight_g,
                "surface_area_mm2": res.surface_area_mm2,
                "polar_MOI": res.polar_moi_g_mm2,
                "radius_of_gyration_mm": res.radius_of_gyration_mm,
                "sg": res.sg,
                "n_holes": geom.n_holes,
                "axis_crosses": res.axis_crosses,
                "sg_matched": res.sg_matched,
            }
        )

    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "pct_of_total_weight"])
    total_w = df["weight_g"].sum()
    df["pct_of_total_weight"] = (df["weight_g"] / total_w * 100.0) if total_w else 0.0
    return df[COLUMNS], geoms, warnings


def analyse_files(
    files: dict[str, str | Path],
    axis_x: float | dict[str, float],
    sg_table: dict[str, float] | None = None,
    fallback_sg: float | None = None,
    flatten_tol: float = 0.01,
) -> tuple[pd.DataFrame, dict[str, list[ComponentGeometry]], list[FileWarnings]]:
    """Analyse a mapping of ``label -> dxf path`` into one combined dataframe.

    *axis_x* may be a single float (same axis X for every file) or a dict
    ``label -> axis_x`` when the axis must be picked per file.
    """
    frames: list[pd.DataFrame] = []
    geoms_by_file: dict[str, list[ComponentGeometry]] = {}
    all_warnings: list[FileWarnings] = []

    for label, path in files.items():
        ax = axis_x[label] if isinstance(axis_x, dict) else axis_x
        load = load_dxf(path, flatten_tol=flatten_tol)
        fw = FileWarnings(file=label)
        fw.messages.extend(w.message for w in load.warnings)
        df, geoms, warns = analyse_loops(label, load.loops, ax, sg_table, fallback_sg)
        fw.messages.extend(warns)
        frames.append(df)
        geoms_by_file[label] = geoms
        if fw.messages:
            all_warnings.append(fw)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return combined, geoms_by_file, all_warnings
