"""Importing user-defined contact patches.

Three input shapes are accepted, because that is what people actually have:

**Outline** -- a closed curve, from a traced ink footprint, a photograph you
digitised, or CAD.  ``.csv`` (two columns, ``x,y`` in mm), ``.json``, or ``.dxf``
(largest closed loop).  Any shape at all: nothing downstream assumes convexity
or symmetry.

**Pressure map** -- a regular grid of pressures, from a pressure-film scan or an
FEA export.  ``.csv`` as a bare matrix (with optional header row/column of
coordinates), or ``.json``.  The loaded outline is derived from the map if you
do not supply one separately.

**Manifest** -- a ``.json`` listing several of the above, one per lean angle,
plus the load each was taken at.  This is the format to use once you have more
than one footprint.

Coordinates
-----------
``x`` is circumferential.  The outline is always re-centred on its own centroid,
so where the origin sat in your CAD file does not matter.

``y`` is lateral, measured from the tread centreline.  A footprint traced in CAD
usually has an arbitrary origin here too, so the lateral placement is decided by
``lateral``:

* ``"auto"`` (default) -- if the outline already lies within the tread, its
  coordinates are taken as real tread positions.  If it does not, it is moved so
  its lateral centre sits at ``y_center`` (or the centreline), and a warning says
  so.  This is what makes a footprint exported from CAD "just work".
* ``"centre"`` -- always move the lateral centre to ``y_center`` (or 0).
* ``"absolute"`` -- trust the file's ``y`` coordinates exactly.

For a **leaned** footprint the lateral position is real information, so pass
``y_center`` explicitly (``--cp-y``) or use ``absolute``.

``--cp-units`` handles files in cm or inches.
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass

import numpy as np

from .contact_patch import ContactPatch, CPLibrary, PressureGrid, measured_patch
from .schema import CrownProfile

UNIT_SCALE = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


@dataclass
class CPImportResult:
    patches: list[ContactPatch]
    warnings: list[str]

    def summary(self) -> str:
        lines = [f"imported {len(self.patches)} contact patch definition(s)"]
        for p in self.patches:
            lines.append(f"  {p.describe()}")
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
def place_outline(
    outline: np.ndarray,
    tread_width: float,
    lateral: str = "auto",
    y_center: float | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Decide where an imported outline sits across the tread.

    Returns the placed outline and any warnings.  Getting this wrong is silent
    and destructive -- an outline outside the tread is clipped flat to the edge
    and reports zero width -- so the decision is explicit and always reported.
    """
    out = np.asarray(outline, dtype=float).reshape(-1, 2).copy()
    half = tread_width / 2.0
    warnings: list[str] = []
    lo, hi = float(out[:, 1].min()), float(out[:, 1].max())
    cy = 0.5 * (lo + hi)
    fits = lo >= -half - 1e-9 and hi <= half + 1e-9

    if lateral == "absolute":
        if not fits:
            warnings.append(
                f"lateral='absolute' but the outline spans y {lo:.1f}..{hi:.1f} mm, outside the "
                f"+-{half:.1f} mm tread -- it will be clipped at the tread edge"
            )
        return out, warnings

    if lateral == "auto" and fits and y_center is None:
        return out, warnings  # the file already carries real tread coordinates

    target = 0.0 if y_center is None else float(y_center)
    out[:, 1] += target - cy
    if lateral == "auto":
        warnings.append(
            f"the outline spanned y {lo:.1f}..{hi:.1f} mm, which is not a position on a "
            f"{tread_width:.1f} mm tread, so it was re-centred to y={target:.1f} mm. "
            "Pass an explicit lateral position (--cp-y) if the footprint is leaned."
        )
    return out, warnings


def _scale(points: np.ndarray, units: str) -> np.ndarray:
    factor = UNIT_SCALE.get(units)
    if factor is None:
        raise ValueError(f"unknown unit {units!r}; expected one of {sorted(UNIT_SCALE)}")
    return points * factor


def load_outline_csv(path: str, units: str = "mm") -> np.ndarray:
    """Two numeric columns, x then y.  Any header row is skipped."""
    pts: list[list[float]] = []
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            try:
                pts.append([float(row[0]), float(row[1])])
            except ValueError:
                continue  # header or comment
    if len(pts) < 3:
        raise ValueError(f"{path}: need at least 3 outline points, found {len(pts)}")
    arr = np.asarray(pts, dtype=float)
    if np.hypot(*(arr[0] - arr[-1])) < 1e-9:
        arr = arr[:-1]  # drop the repeated closing vertex
    return _scale(arr, units)


def load_outline_dxf(path: str, units: str = "mm") -> np.ndarray:
    """Largest closed loop in a DXF -- the footprint boundary."""
    from .dxf import build_loops, entities_to_segments, read_dxf_entities

    segments = entities_to_segments(read_dxf_entities(path))
    closed, _ = build_loops(segments)
    if not closed:
        raise ValueError(f"{path}: no closed loop found to use as a footprint outline")

    def area(loop):
        a = np.asarray(loop, dtype=float)
        x, y = a[:, 0], a[:, 1]
        return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

    return _scale(np.asarray(max(closed, key=area), dtype=float), units)


def load_pressure_csv(
    path: str, units: str = "mm", pressure_units: str = "MPa", dx: float | None = None, dy: float | None = None
) -> PressureGrid:
    """A pressure matrix.

    If the first row and first column are coordinates (detected by the top-left
    cell being empty or non-numeric) they are used directly.  Otherwise the grid
    is assumed uniform and ``dx``/``dy`` give the cell size in mm -- without
    them the map has no scale and the import fails rather than guessing.
    """
    rows: list[list[str]] = []
    with open(path, newline="") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"{path}: empty pressure file")

    def num(cell: str) -> float | None:
        try:
            return float(cell)
        except (ValueError, TypeError):
            return None

    has_coords = num(rows[0][0]) is None and len(rows) > 1 and num(rows[1][0]) is not None
    if has_coords:
        xs = np.asarray([num(c) for c in rows[0][1:]], dtype=float)
        ys = np.asarray([num(r[0]) for r in rows[1:]], dtype=float)
        p = np.asarray([[num(c) or 0.0 for c in r[1:]] for r in rows[1:]], dtype=float)
        xs, ys = _scale(xs, units), _scale(ys, units)
    else:
        p = np.asarray([[num(c) or 0.0 for c in r] for r in rows], dtype=float)
        if dx is None or dy is None:
            raise ValueError(
                f"{path}: pressure matrix has no coordinate header, so --cp-pressure-dx and "
                "--cp-pressure-dy are required to give it a scale"
            )
        xs = (np.arange(p.shape[1]) - (p.shape[1] - 1) / 2.0) * dx
        ys = (np.arange(p.shape[0]) - (p.shape[0] - 1) / 2.0) * dy

    scale = {"MPa": 1.0, "kPa": 1e-3, "bar": 0.1, "psi": 0.00689476, "N/mm2": 1.0}.get(pressure_units)
    if scale is None:
        raise ValueError(f"unknown pressure unit {pressure_units!r}")
    return PressureGrid(x=xs, y=ys, p=p * scale)


def load_outline_json(path: str, units: str = "mm") -> np.ndarray:
    with open(path) as fh:
        data = json.load(fh)
    pts = data.get("outline", data) if isinstance(data, dict) else data
    arr = np.asarray(pts, dtype=float).reshape(-1, 2)
    if len(arr) < 3:
        raise ValueError(f"{path}: need at least 3 outline points")
    return _scale(arr, units)


def load_outline(path: str, units: str = "mm") -> np.ndarray:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return load_outline_csv(path, units)
    if ext == ".json":
        return load_outline_json(path, units)
    if ext in (".dxf", ".txt"):
        return load_outline_dxf(path, units)
    raise ValueError(f"{path}: unsupported outline format {ext!r} (use .csv, .json or .dxf)")


# ---------------------------------------------------------------------------
def load_cp_manifest(
    path: str, tread_width: float, lateral: str = "auto"
) -> CPImportResult:
    """Load several footprints from a JSON manifest.

    ::

        {
          "units": "mm",
          "pressure_units": "MPa",
          "patches": [
            {"gamma_deg": 0,  "outline": "fp_00.csv", "load_N": 1500},
            {"gamma_deg": 25, "pressure": "fp_25.csv", "load_N": 1650},
            {"gamma_deg": 40, "outline": "fp_40.dxf", "pressure": "fp_40.csv"}
          ]
        }

    Paths are resolved relative to the manifest.  Every entry needs at least one
    of ``outline`` or ``pressure``.
    """
    with open(path) as fh:
        data = json.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    units = data.get("units", "mm")
    p_units = data.get("pressure_units", "MPa")
    dx, dy = data.get("pressure_dx"), data.get("pressure_dy")

    patches: list[ContactPatch] = []
    warnings: list[str] = []
    for i, entry in enumerate(data.get("patches", [])):
        gamma = float(entry.get("gamma_deg", 0.0))
        outline = None
        grid = None
        if entry.get("outline"):
            outline = load_outline(os.path.join(base, entry["outline"]), units)
        if entry.get("pressure"):
            grid = load_pressure_csv(
                os.path.join(base, entry["pressure"]), units, p_units,
                entry.get("pressure_dx", dx), entry.get("pressure_dy", dy),
            )
        if outline is None and grid is None:
            warnings.append(f"patch entry {i} has neither an outline nor a pressure map -- skipped")
            continue
        if outline is None:
            outline = grid.outline()
            warnings.append(
                f"{gamma:g} deg: outline derived from the pressure map's loaded area "
                "(convex hull); supply an explicit outline if the footprint is concave"
            )
        outline, placed_warnings = place_outline(
            outline, tread_width, entry.get("lateral", lateral), entry.get("y_center")
        )
        warnings += [f"{gamma:g} deg: {w}" for w in placed_warnings]
        patches.append(
            measured_patch(
                gamma_deg=gamma,
                outline=outline,
                tread_width=tread_width,
                pressure_grid=grid,
                normal_load=entry.get("load_N"),
                label=entry.get("label", f"measured footprint at {gamma:g} deg"),
            )
        )
    return CPImportResult(patches=sorted(patches, key=lambda p: p.gamma_deg), warnings=warnings)


def load_cp_any(
    path: str,
    tread_width: float,
    gamma_deg: float = 0.0,
    units: str = "mm",
    pressure_units: str = "MPa",
    dx: float | None = None,
    dy: float | None = None,
    load_n: float | None = None,
    as_pressure: bool = False,
    lateral: str = "auto",
    y_center: float | None = None,
) -> CPImportResult:
    """Import whatever the user pointed at: manifest, outline, or pressure map."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "patches" in data:
            return load_cp_manifest(path, tread_width, lateral)

    warnings: list[str] = []
    grid = None
    if as_pressure:
        grid = load_pressure_csv(path, units, pressure_units, dx, dy)
        outline = grid.outline()
        warnings.append("outline derived from the pressure map's loaded area (convex hull)")
        label = f"measured pressure map at {gamma_deg:g} deg"
    else:
        outline = load_outline(path, units)
        label = f"measured outline at {gamma_deg:g} deg ({os.path.basename(path)})"

    placed, placed_warnings = place_outline(outline, tread_width, lateral, y_center)
    warnings += placed_warnings
    if grid is not None:
        # keep the pressure map with its outline
        grid = PressureGrid(x=grid.x, y=grid.y + (placed[:, 1].mean() - outline[:, 1].mean()), p=grid.p)

    patch = measured_patch(
        gamma_deg, placed, tread_width, pressure_grid=grid, normal_load=load_n, label=label,
    )
    return CPImportResult([patch], warnings)


# ---------------------------------------------------------------------------
def build_library(
    crown: CrownProfile,
    tread_width: float,
    params,
    generator: str = "winkler",
    imported: CPImportResult | None = None,
    allow_transfer: bool = True,
) -> CPLibrary:
    lib = CPLibrary(
        crown=crown,
        tread_width=tread_width,
        params=params,
        generator=generator,
        allow_transfer=allow_transfer,
    )
    for patch in (imported.patches if imported else []):
        lib.add_measured(patch)
    return lib


def write_example_manifest(path: str) -> None:
    """Write a commented example manifest, so the format is discoverable."""
    example = {
        "_comment": "Contact patch manifest. Paths are relative to this file.",
        "units": "mm",
        "pressure_units": "MPa",
        "patches": [
            {"gamma_deg": 0, "outline": "footprint_00deg.csv", "load_N": 1500,
             "label": "static footprint, 250 kPa, 150 kg"},
            {"gamma_deg": 25, "outline": "footprint_25deg.csv", "load_N": 1650},
        ],
    }
    with open(path, "w") as fh:
        json.dump(example, fh, indent=2)
