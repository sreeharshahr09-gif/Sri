#!/usr/bin/env python3
"""Tyre Cross-Section Analysis Tool — single-file edition.

Everything (DXF ingestion, geometry, Pappus physics, validation, raw-drawing
reconstruction + manual loop builder, and the Streamlit UI) lives in this one
file. See the project brief for the full rationale.

RUN IT:

    python tyre_tool.py

That's it. On first run it installs any missing dependencies (ezdxf, shapely,
numpy, pandas, plotly, streamlit) and relaunches itself under Streamlit in your
browser. You can also run `streamlit run tyre_tool.py` directly if you prefer.

Headless CLI (no browser):

    python tyre_tool.py --cli validate  file1.dxf file2.dxf
    python tyre_tool.py --cli analyse --axis-x 0 baseline.dxf iter1.dxf -o out.csv
    python tyre_tool.py --make-samples          # write demo DXFs to ./sample_data
"""

from __future__ import annotations

import math
import sys
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

REQUIREMENTS = ["ezdxf>=1.1", "shapely>=2.0", "numpy>=1.24",
                "pandas>=2.0", "plotly>=5.18", "streamlit>=1.30"]
_IMPORT_NAMES = ["ezdxf", "shapely", "numpy", "pandas", "plotly", "streamlit"]


# --------------------------------------------------------------------------- #
# Bootstrap: install deps + relaunch under Streamlit when run with plain python
# --------------------------------------------------------------------------- #
def _under_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _missing_deps() -> list[str]:
    missing = []
    for mod, spec in zip(_IMPORT_NAMES, REQUIREMENTS):
        try:
            __import__(mod)
        except ImportError:
            missing.append(spec)
    return missing


def _install_missing() -> None:
    missing = _missing_deps()
    if not missing:
        return
    print(f"Installing missing dependencies: {', '.join(missing)}")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--no-warn-script-location", *missing]
        )
    except subprocess.CalledProcessError:
        here = Path(__file__).resolve()
        sys.stderr.write(
            "\n"
            "--------------------------------------------------------------------\n"
            "Dependency install FAILED.\n"
            "On Windows this is almost always the 260-character path limit — common\n"
            "with the Microsoft Store build of Python, whose package folder is very\n"
            "deep. Streamlit ships a deeply-nested file that then can't be written.\n"
            "\n"
            "Fix A (no admin) — install into a virtual env at a SHORT path:\n"
            "    python -m venv C:\\tyre\n"
            "    C:\\tyre\\Scripts\\activate\n"
            f"    python \"{here}\"\n"
            "\n"
            "Fix B (one-time, admin PowerShell) — enable long paths, then retry:\n"
            "    New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' \\\n"
            "        -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force\n"
            "\n"
            "Tip: installing python.org's Python (not the Store version) also avoids\n"
            "this, because it installs to a short path like C:\\Python312.\n"
            "--------------------------------------------------------------------\n"
        )
        raise SystemExit(1)


# --------------------------------------------------------------------------- #
# EARLY GUARD — runs BEFORE the third-party imports below, so `python
# tyre_tool.py` on a machine without the libraries installs them first (and,
# for the GUI, relaunches itself under Streamlit) instead of crashing on
# `import ezdxf`. Skipped entirely when this file is imported as a module.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _install_missing()
    _CLI_INVOCATION = len(sys.argv) > 1 and sys.argv[1] in ("--cli", "--make-samples")
    if not _under_streamlit() and not _CLI_INVOCATION:
        # Plain `python tyre_tool.py`: hand off to Streamlit, then exit when it does.
        print("Launching Streamlit UI… (Ctrl+C to stop)")
        subprocess.check_call(
            [sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve())]
        )
        sys.exit(0)


# ========================================================================== #
# CORE LIBRARY  (importable; no Streamlit needed)
# ========================================================================== #
import ezdxf                                    # noqa: E402
from ezdxf import path as ezpath                # noqa: E402
from shapely.geometry import (                  # noqa: E402
    Polygon, MultiPolygon, LineString,
)
from shapely.geometry.polygon import orient     # noqa: E402
from shapely.ops import polygonize, unary_union  # noqa: E402
import pandas as pd                             # noqa: E402

MM3_PER_CM3 = 1000.0
DEFAULT_FLATTEN_TOL = 0.01

DEFAULT_SG_TABLE: dict[str, float] = {
    "TREAD": 1.15, "SIDEWALL": 1.10, "APEX": 1.12, "INNERLINER": 1.20,
    "BELT-1": 1.30, "BELT-2": 1.30, "PLY-1": 1.25, "PLY-2": 1.25,
    "BEAD": 7.85, "BEAD-FILLER": 1.12, "CHAFER": 1.18,
}
FALLBACK_SG = 1.10

COLUMNS = [
    "file", "component", "area_mm2", "centroid_x", "centroid_y",
    "centroid_distance_mm", "volume_cm3", "weight_g", "surface_area_mm2",
    "polar_MOI", "radius_of_gyration_mm", "sg", "n_holes", "axis_crosses",
    "sg_matched", "pct_of_total_weight",
]


def normalise_key(name: str) -> str:
    return name.strip().upper()


def lookup_sg(layer_name, table=None, fallback=FALLBACK_SG):
    src = DEFAULT_SG_TABLE if table is None else table
    lut = {normalise_key(k): float(v) for k, v in src.items()}
    key = normalise_key(layer_name)
    if key in lut:
        return lut[key], True
    if fallback is None:
        raise KeyError(f"No specific gravity for layer {layer_name!r}")
    return fallback, False


# ----- geometry ------------------------------------------------------------ #
@dataclass
class ComponentGeometry:
    layer: str
    area_mm2: float
    centroid_x: float
    centroid_y: float
    boundary_length_mm: float
    boundary_centroid_x: float
    boundary_centroid_y: float
    r3_moment_mm5: float
    n_holes: int
    polygon: "Polygon | MultiPolygon"

    def area_centroid_distance(self, axis_x):
        return abs(self.centroid_x - axis_x)

    def boundary_centroid_distance(self, axis_x):
        return abs(self.boundary_centroid_x - axis_x)


def build_polygon(loops):
    raw = [Polygon(l) for l in loops if len(l) >= 4]
    raw = [p for p in raw if p.is_valid and p.area > 0]
    if not raw:
        raise ValueError("No valid polygon could be built from the supplied loops")
    if len(raw) == 1:
        return orient(raw[0], sign=1.0)
    raw.sort(key=lambda p: p.area, reverse=True)
    shells, holes = [], {}
    for poly in raw:
        placed = False
        for idx, shell in enumerate(shells):
            if shell.contains(poly.representative_point()):
                holes.setdefault(idx, []).append(poly.exterior.coords[:])
                placed = True
                break
        if not placed:
            shells.append(poly)
    parts = [Polygon(shell.exterior.coords[:], holes.get(idx, []))
             for idx, shell in enumerate(shells)]
    poly = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return orient(poly, sign=1.0) if isinstance(poly, Polygon) else poly


def _iter_rings(polygon):
    polys = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
    for p in polys:
        p = orient(p, sign=1.0)
        yield p.exterior.coords[:], False
        for interior in p.interiors:
            yield interior.coords[:], True


def boundary_centroid(polygon):
    sum_len = sum_x = sum_y = 0.0
    for coords, _ in _iter_rings(polygon):
        for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg == 0:
                continue
            sum_len += seg
            sum_x += seg * (x0 + x1) * 0.5
            sum_y += seg * (y0 + y1) * 0.5
    if sum_len == 0:
        c = polygon.centroid
        return c.x, c.y, 0.0
    return sum_x / sum_len, sum_y / sum_len, sum_len


def r3_area_moment(polygon, axis_x):
    """integral(|x-axis_x|^3 dA) via Green's theorem boundary line integral."""
    total = 0.0
    for coords, _ in _iter_rings(polygon):
        ring = 0.0
        for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
            u0, u1 = x0 - axis_x, x1 - axis_x
            dy, du = y1 - y0, (x1 - axis_x) - (x0 - axis_x)
            edge = u0 ** 4 if abs(du) < 1e-14 else (u1 ** 5 - u0 ** 5) / (5.0 * du)
            ring += dy * edge / 4.0
        total += ring
    return abs(total)


def axis_crossing(polygon, axis_x, tol=1e-9):
    xs = [x for coords, _ in _iter_rings(polygon) for x, _ in coords]
    mn, mx = min(xs) - axis_x, max(xs) - axis_x
    return (mn < -tol) and (mx > tol), mn, mx


def _geom_from_polygon(name, polygon, axis_x):
    if isinstance(polygon, Polygon):
        polygon = orient(polygon, sign=1.0)
    c = polygon.centroid
    bcx, bcy, blen = boundary_centroid(polygon)
    n_holes = (len(polygon.interiors) if isinstance(polygon, Polygon)
               else sum(len(p.interiors) for p in polygon.geoms))
    return ComponentGeometry(name, abs(polygon.area), c.x, c.y, blen, bcx, bcy,
                             r3_area_moment(polygon, axis_x), n_holes, polygon)


def geometry_from_polygon(name, polygon, axis_x):
    return _geom_from_polygon(name, polygon, axis_x)


def compute_geometry(layer, loops, axis_x):
    return _geom_from_polygon(layer, build_polygon(loops), axis_x)


# ----- physics ------------------------------------------------------------- #
@dataclass
class ComponentResult:
    layer: str
    area_mm2: float
    centroid_x: float
    centroid_y: float
    centroid_distance_mm: float
    volume_cm3: float
    weight_g: float
    surface_area_mm2: float
    polar_moi_g_mm2: float
    radius_of_gyration_mm: float
    sg: float
    sg_matched: bool
    axis_crosses: bool


def compute_physics(geom, axis_x, sg, sg_matched=True):
    d_area = geom.area_centroid_distance(axis_x)
    d_arc = geom.boundary_centroid_distance(axis_x)
    volume_cm3 = (2.0 * math.pi * d_area * geom.area_mm2) / MM3_PER_CM3
    surface = 2.0 * math.pi * d_arc * geom.boundary_length_mm
    weight = volume_cm3 * sg
    rho = sg / MM3_PER_CM3
    polar = 2.0 * math.pi * rho * geom.r3_moment_mm5
    rog = math.sqrt(polar / weight) if weight > 0 else 0.0
    crosses, _, _ = axis_crossing(geom.polygon, axis_x)
    return ComponentResult(geom.layer, geom.area_mm2, geom.centroid_x, geom.centroid_y,
                           d_area, volume_cm3, weight, surface, polar, rog,
                           sg, sg_matched, crosses)


# ----- DXF ingestion (layer path) ----------------------------------------- #
@dataclass
class ComponentLoop:
    layer: str
    coords: list
    source: str


@dataclass
class LoadResult:
    loops: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


_SUPPORTED = {"LWPOLYLINE", "POLYLINE", "SPLINE", "CIRCLE", "ELLIPSE", "ARC", "LINE"}
_ACIS = {"REGION", "3DSOLID", "BODY", "SURFACE"}
_SKIP = {"MTEXT", "TEXT", "DIMENSION", "INSERT", "HATCH", "LEADER"}


def _flatten_entity(entity, tol):
    if entity.dxftype() not in _SUPPORTED:
        return None
    try:
        p = ezpath.make_path(entity)
    except (TypeError, ValueError):
        return None
    pts = [(v.x, v.y) for v in p.flattening(distance=tol)]
    return pts if len(pts) >= 2 else None


def _close(pts):
    if len(pts) < 3:
        return pts, False
    a, b = pts[0], pts[-1]
    already = abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9
    return (pts if already else pts + [pts[0]]), already


def load_dxf(filename, flatten_tol=DEFAULT_FLATTEN_TOL):
    res = LoadResult()
    doc = ezdxf.readfile(str(filename))
    for entity in doc.modelspace():
        dxftype = entity.dxftype()
        layer = entity.dxf.layer
        if dxftype in _ACIS:
            res.warnings.append(f"{layer}: skipped {dxftype} (ACIS blob)")
            continue
        pts = _flatten_entity(entity, flatten_tol)
        if pts is None:
            continue
        pts, _ = _close(pts)
        if len(pts) >= 4:
            res.loops.append(ComponentLoop(layer, pts, dxftype))
    return res


# ----- validation ---------------------------------------------------------- #
@dataclass
class ConformanceReport:
    filename: str
    dxf_version: str
    acad_release: str
    layers: dict = field(default_factory=dict)  # name -> {counts, closed, open}
    has_acis: bool = False
    all_on_layer_zero: bool = False
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def conforms(self):
        return not self.problems

    @property
    def component_layers(self):
        return [n for n in self.layers if n not in ("0", "Defpoints")]


def _is_closed(entity, tol=1e-6):
    t = entity.dxftype()
    if t == "CIRCLE":
        return True
    if t == "ELLIPSE":
        return abs((entity.dxf.end_param - entity.dxf.start_param) - 2 * math.pi) < 1e-3
    if t == "LWPOLYLINE" and entity.closed:
        return True
    if t == "POLYLINE" and entity.is_closed:
        return True
    if t == "SPLINE" and getattr(entity, "closed", False):
        return True
    try:
        pts = list(ezpath.make_path(entity).flattening(0.1))
        if len(pts) >= 3:
            a, b = pts[0], pts[-1]
            return abs(a.x - b.x) < tol and abs(a.y - b.y) < tol
    except Exception:
        pass
    return False


def validate_dxf(filename):
    doc = ezdxf.readfile(str(filename))
    rep = ConformanceReport(Path(filename).name, doc.dxfversion, doc.acad_release)
    for entity in doc.modelspace():
        t = entity.dxftype()
        layer = entity.dxf.layer
        if t in _ACIS:
            rep.has_acis = True
            continue
        if t not in _SUPPORTED:
            continue
        lr = rep.layers.setdefault(layer, {"counts": {}, "closed": 0, "open": 0})
        lr["counts"][t] = lr["counts"].get(t, 0) + 1
        if _is_closed(entity):
            lr["closed"] += 1
        else:
            lr["open"] += 1
    comp = rep.component_layers
    rep.all_on_layer_zero = (not comp and any(k in rep.layers for k in ("0", "Defpoints")))
    if rep.dxf_version < "AC1015":
        rep.problems.append(f"DXF {rep.dxf_version} older than R2000; arcs may be legacy POLYLINE chains.")
    if rep.has_acis:
        rep.problems.append("Contains REGION/3DSOLID (ACIS blobs) — export raw closed curves instead.")
    if rep.all_on_layer_zero:
        rep.problems.append("All geometry on layer '0' — put each component on its own named layer "
                            "(use the Manual Loop Builder mode for files like this).")
    for name in comp:
        lr = rep.layers[name]
        if lr["closed"] == 0:
            rep.problems.append(f"Layer '{name}' has no closed loop; run AutoCAD BOUNDARY.")
        elif lr["open"]:
            rep.notes.append(f"Layer '{name}' mixes {lr['closed']} closed + {lr['open']} loose curve(s).")
    return rep


def format_report(rep):
    out = [f"File: {rep.filename}  ({rep.dxf_version} / {rep.acad_release})",
           f"Conforms: {'YES' if rep.conforms else 'NO'}", "Layers with geometry:"]
    for name, lr in sorted(rep.layers.items()):
        counts = ", ".join(f"{k}:{v}" for k, v in sorted(lr["counts"].items()))
        out.append(f"  {name!r}: [{counts}]  closed={lr['closed']} open={lr['open']}")
    if rep.problems:
        out.append("Problems:")
        out += [f"  - {p}" for p in rep.problems]
    if rep.notes:
        out.append("Notes:")
        out += [f"  - {n}" for n in rep.notes]
    return "\n".join(out)


# ----- reconstruction (manual builder path) -------------------------------- #
@dataclass
class Face:
    id: int
    polygon: "Polygon"
    area: float
    rep_x: float
    rep_y: float
    cx: float
    cy: float


@dataclass
class Reconstruction:
    filename: str
    curves: list
    faces: list
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    suggested_axis_x: float
    vertical_axis_lines: list = field(default_factory=list)

    def faces_on_side(self, axis_x, side):
        if side == "both":
            return self.faces
        if side == "right":
            return [f for f in self.faces if f.rep_x > axis_x]
        return [f for f in self.faces if f.rep_x < axis_x]


def _flatten_curves(msp, tol):
    curves = []
    for e in msp:
        if e.dxftype() in _SKIP:
            continue
        try:
            pts = [(v.x, v.y) for v in ezpath.make_path(e).flattening(tol)]
        except Exception:
            continue
        if len(pts) >= 2:
            curves.append(pts)
    return curves


def _detect_vertical_axes(msp, tol=0.5):
    xs = []
    for e in msp.query("LINE"):
        s, t = e.dxf.start, e.dxf.end
        if abs(s.x - t.x) <= tol and abs(t.y - s.y) > tol:
            xs.append(round((s.x + t.x) / 2, 3))
    return sorted(set(xs))


def reconstruct(filename, flatten_tol=0.05, min_area=1.0, dedupe=True):
    doc = ezdxf.readfile(str(filename))
    msp = doc.modelspace()
    curves = _flatten_curves(msp, flatten_tol)
    merged = unary_union([LineString(c) for c in curves if len(c) >= 2])
    kept, seen = [], set()
    for poly in polygonize(merged):
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
        rp, c = poly.representative_point(), poly.centroid
        faces.append(Face(i, poly, poly.area, rp.x, rp.y, c.x, c.y))
    xs = [x for c in curves for x, _ in c]
    ys = [y for c in curves for _, y in c]
    xmin, xmax = (min(xs), max(xs)) if xs else (0.0, 0.0)
    ymin, ymax = (min(ys), max(ys)) if ys else (0.0, 0.0)
    vaxes = _detect_vertical_axes(msp)
    mid = (xmin + xmax) / 2
    suggested = min(vaxes, key=lambda x: abs(x - mid)) if vaxes else mid
    return Reconstruction(Path(filename).name, curves, faces, xmin, xmax, ymin, ymax,
                          suggested, vaxes)


def assemble_component(faces, face_ids):
    by_id = {f.id: f for f in faces}
    polys = [by_id[i].polygon for i in face_ids if i in by_id]
    if not polys:
        raise ValueError("No faces selected")
    merged = unary_union(polys)
    if merged.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Selected faces did not union into an area ({merged.geom_type})")
    return merged


# ----- pipeline ------------------------------------------------------------ #
def _row(file_label, geom, res):
    return {
        "file": file_label, "component": geom.layer, "area_mm2": res.area_mm2,
        "centroid_x": res.centroid_x, "centroid_y": res.centroid_y,
        "centroid_distance_mm": res.centroid_distance_mm, "volume_cm3": res.volume_cm3,
        "weight_g": res.weight_g, "surface_area_mm2": res.surface_area_mm2,
        "polar_MOI": res.polar_moi_g_mm2, "radius_of_gyration_mm": res.radius_of_gyration_mm,
        "sg": res.sg, "n_holes": geom.n_holes, "axis_crosses": res.axis_crosses,
        "sg_matched": res.sg_matched,
    }


def _finalise(rows):
    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "pct_of_total_weight"])
    total = df["weight_g"].sum()
    df["pct_of_total_weight"] = (df["weight_g"] / total * 100.0) if total else 0.0
    return df[COLUMNS]


def analyse_loops(file_label, loops, axis_x, sg_table=None, fallback_sg=FALLBACK_SG):
    grouped = {}
    for loop in loops:
        grouped.setdefault(loop.layer, []).append(loop.coords)
    rows, geoms, warns = [], [], []
    for layer, layer_loops in grouped.items():
        try:
            geom = compute_geometry(layer, layer_loops, axis_x)
        except ValueError as exc:
            warns.append(f"{layer}: {exc}")
            continue
        sg, matched = lookup_sg(layer, sg_table, fallback_sg)
        if not matched:
            warns.append(f"{layer}: no SG entry; using fallback {sg}")
        res = compute_physics(geom, axis_x, sg, matched)
        if res.axis_crosses:
            warns.append(f"{layer}: crosses rotation axis — Pappus invalid")
        geoms.append(geom)
        rows.append(_row(file_label, geom, res))
    return _finalise(rows), geoms, warns


def analyse_components(file_label, components, axis_x, sg_table=None, fallback_sg=FALLBACK_SG):
    rows, geoms, warns = [], [], []
    for name, poly in components.items():
        geom = geometry_from_polygon(name, poly, axis_x)
        sg, matched = lookup_sg(name, sg_table, fallback_sg)
        if not matched:
            warns.append(f"{name}: no SG entry; using fallback {sg}")
        res = compute_physics(geom, axis_x, sg, matched)
        if res.axis_crosses:
            warns.append(f"{name}: crosses axis — pick faces on ONE side only")
        geoms.append(geom)
        rows.append(_row(file_label, geom, res))
    return _finalise(rows), geoms, warns


def analyse_files(files, axis_x, sg_table=None, fallback_sg=FALLBACK_SG, flatten_tol=0.01):
    frames, geoms_by_file, all_warnings = [], {}, {}
    for label, path in files.items():
        ax = axis_x[label] if isinstance(axis_x, dict) else axis_x
        load = load_dxf(path, flatten_tol=flatten_tol)
        df, geoms, warns = analyse_loops(label, load.loops, ax, sg_table, fallback_sg)
        frames.append(df)
        geoms_by_file[label] = geoms
        msgs = list(load.warnings) + warns
        if msgs:
            all_warnings[label] = msgs
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    return combined, geoms_by_file, all_warnings


# ----- sample generator ---------------------------------------------------- #
def _sample_baseline():
    return {
        "TREAD": [(150, 300, 0), (230, 300, 0), (230, 318, -0.15), (150, 318, 0)],
        "BELT-1": [(152, 292, 0), (228, 292, 0), (228, 299, 0), (152, 299, 0)],
        "BELT-2": [(155, 284, 0), (225, 284, 0), (225, 291, 0), (155, 291, 0)],
        "SIDEWALL": [(150, 300, 0), (158, 300, 0.2), (172, 150, 0), (172, 120, 0),
                     (160, 120, 0), (160, 150, -0.2), (146, 300, 0)],
        "PLY-1": [(156, 300, 0), (159, 300, 0.15), (168, 130, 0), (168, 118, 0),
                  (164, 118, 0), (164, 130, -0.15), (153, 300, 0)],
        "APEX": [(160, 118, 0), (170, 118, 0), (163, 175, 0)],
        "BEAD": [(160, 108, 0.414), (170, 108, 0.414), (170, 118, 0.414), (160, 118, 0.414)],
    }


def _sample_iter1():
    b = _sample_baseline()
    b["TREAD"] = [(150, 300, 0), (230, 300, 0), (230, 313, -0.15), (150, 313, 0)]
    b["APEX"] = [(161, 120, 0), (169, 120, 0), (164, 182, 0)]
    return b


def write_profile(components, path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for layer_name, verts in components.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name)
        pts = [(x, y, 0.0, 0.0, bulge) for (x, y, bulge) in verts]
        msp.add_lwpolyline(pts, format="xyseb", close=True, dxfattribs={"layer": layer_name})
    doc.saveas(str(path))


def make_samples(out_dir="sample_data"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_profile(_sample_baseline(), out / "baseline.dxf")
    write_profile(_sample_iter1(), out / "iteration1.dxf")
    return out / "baseline.dxf", out / "iteration1.dxf"


# ========================================================================== #
# CLI
# ========================================================================== #
def _cli(argv):
    import argparse
    p = argparse.ArgumentParser(prog="tyre_tool.py --cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("validate")
    pv.add_argument("files", nargs="+")
    pv.add_argument("--strict", action="store_true")
    pa = sub.add_parser("analyse")
    pa.add_argument("files", nargs="+")
    pa.add_argument("--axis-x", type=float, default=0.0)
    pa.add_argument("--flatten-tol", type=float, default=0.01)
    pa.add_argument("-o", "--output")
    pa.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "validate":
        bad = False
        for f in args.files:
            rep = validate_dxf(f)
            print(format_report(rep), "\n")
            bad = bad or not rep.conforms
        return 1 if (bad and args.strict) else 0

    files = {Path(f).stem: f for f in args.files}
    bad = [l for l, pth in files.items() if not validate_dxf(pth).conforms]
    if bad and not args.force:
        print(f"Non-conforming: {', '.join(bad)} (use --force or the Manual Loop Builder).",
              file=sys.stderr)
        return 1
    df, _, warns = analyse_files(files, args.axis_x, flatten_tol=args.flatten_tol)
    for lbl, msgs in warns.items():
        for m in msgs:
            print(f"[warn] {lbl}: {m}", file=sys.stderr)
    show = ["file", "component", "area_mm2", "weight_g", "centroid_distance_mm",
            "volume_cm3", "surface_area_mm2", "polar_MOI", "pct_of_total_weight"]
    print(df[show].round(3).to_string(index=False))
    print("\nTire totals:\n" + df.groupby("file")["weight_g"].sum().round(2).to_string())
    if args.output:
        df.to_csv(args.output, index=False)
        print(f"\nWrote {args.output}")
    return 0


# ========================================================================== #
# STREAMLIT UI
# ========================================================================== #
def _run_ui():
    import json
    import tempfile
    import streamlit as st
    import plotly.graph_objects as go

    _PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
                "#B279A2", "#EECA3B", "#9D755D", "#BAB0AC", "#FF9DA6"]

    def comp_colors(components):
        comps = list(dict.fromkeys(components))
        return {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(comps)}

    def rgba(hex_color, alpha):
        h = hex_color.lstrip("#")
        return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{alpha})"

    def _ring_xy(geom):
        polys = [geom.polygon] if isinstance(geom.polygon, Polygon) else list(geom.polygon.geoms)
        for pp in polys:
            x, y = pp.exterior.xy
            yield list(x), list(y)

    def overlay_fig(geoms_by_file, baseline, axis_x):
        fig = go.Figure()
        colors = comp_colors([g.layer for gs in geoms_by_file.values() for g in gs])
        for file, geoms in geoms_by_file.items():
            base = file == baseline
            for g in geoms:
                for xs, ys in _ring_xy(g):
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, mode="lines", name=f"{g.layer} ({file})", legendgroup=file,
                        line=dict(color=colors[g.layer], width=2.5 if base else 1.2,
                                  dash="solid" if base else "dot"),
                        fill=None if base else "toself",
                        fillcolor=None if base else rgba(colors[g.layer], 0.18),
                        hovertemplate=f"{g.layer} · {file}<extra></extra>"))
        ax = axis_x if isinstance(axis_x, (int, float)) else list(axis_x.values())[0]
        fig.add_vline(x=ax, line=dict(color="#888", dash="dash"), annotation_text="axis")
        fig.update_layout(title="Cross-section overlay", xaxis_title="x (mm)", yaxis_title="y (mm)",
                          yaxis_scaleanchor="x", height=620, legend=dict(font=dict(size=9)))
        return fig

    def centroid_fig(df, geoms_by_file, baseline):
        fig = go.Figure()
        colors = comp_colors(df["component"].unique())
        for g in geoms_by_file.get(baseline, []):
            for xs, ys in _ring_xy(g):
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", showlegend=False,
                                         line=dict(color="#ccc", width=1), hoverinfo="skip"))
        base = df[df["file"] == baseline].set_index("component")
        wmax = df["weight_g"].max() or 1.0
        for file, g in df.groupby("file", sort=False):
            fig.add_trace(go.Scatter(
                x=g["centroid_x"], y=g["centroid_y"], mode="markers", name=file,
                marker=dict(size=8 + 30 * (g["weight_g"] / wmax),
                            color=[colors[c] for c in g["component"]],
                            line=dict(width=2 if file != baseline else 0, color="#333")),
                text=g["component"],
                hovertemplate="%{text} · " + file + "<br>x=%{x:.1f} y=%{y:.1f}<extra></extra>"))
            if file != baseline:
                for _, row in g.iterrows():
                    if row["component"] in base.index:
                        b = base.loc[row["component"]]
                        fig.add_annotation(x=row["centroid_x"], y=row["centroid_y"],
                                           ax=b["centroid_x"], ay=b["centroid_y"],
                                           xref="x", yref="y", axref="x", ayref="y",
                                           showarrow=True, arrowhead=3, arrowwidth=1.5,
                                           arrowcolor=colors[row["component"]])
        fig.update_layout(title="Centroid map (size=weight, arrows=shift)", xaxis_title="x (mm)",
                          yaxis_title="y (mm)", yaxis_scaleanchor="x", height=620)
        return fig

    def moi_fig(df):
        fig = go.Figure()
        for file, g in df.groupby("file", sort=False):
            fig.add_trace(go.Bar(x=g["component"], y=g["polar_MOI"], name=file))
        fig.update_layout(title="Polar MOI by component (r³-driven)", barmode="group",
                          xaxis_title="component", yaxis_title="polar MOI (g·mm²)", height=460)
        return fig

    def waterfall_fig(df, baseline, final):
        b = df[df["file"] == baseline].set_index("component")["weight_g"]
        f = df[df["file"] == final].set_index("component")["weight_g"]
        comps = list(dict.fromkeys(list(b.index) + list(f.index)))
        deltas = [f.get(c, 0.0) - b.get(c, 0.0) for c in comps]
        fig = go.Figure(go.Waterfall(
            orientation="v", measure=["relative"] * len(comps) + ["total"],
            x=comps + ["NET Δ"], y=deltas + [0],
            decreasing=dict(marker=dict(color="#2EA043")),
            increasing=dict(marker=dict(color="#E45756")),
            totals=dict(marker=dict(color="#4C78A8")),
            text=[f"{d:+.0f}" for d in deltas] + [f"{sum(deltas):+.0f}"], textposition="outside"))
        fig.update_layout(title=f"Weight waterfall: {baseline} → {final} (g)",
                          yaxis_title="Δ weight (g)", height=480)
        return fig

    def face_picker_fig(rec, faces, axis_x, side, assignments, colors):
        fig = go.Figure()
        for c in rec.curves:
            xs, ys = zip(*c)
            fig.add_trace(go.Scatter(x=list(xs), y=list(ys), mode="lines",
                                     line=dict(color="rgba(120,120,120,0.35)", width=0.6),
                                     hoverinfo="skip", showlegend=False))
        face_comp = {fid: comp for comp, ids in assignments.items() for fid in ids}
        mx, my, mtext, mcustom, mcolor = [], [], [], [], []
        for f in faces:
            comp = face_comp.get(f.id)
            fill = rgba(colors.get(comp, "#888"), 0.45) if comp else "rgba(200,200,200,0.25)"
            line = colors.get(comp, "#666") if comp else "rgba(150,150,150,0.6)"
            xs, ys = f.polygon.exterior.xy
            fig.add_trace(go.Scatter(x=list(xs), y=list(ys), mode="lines", fill="toself",
                                     fillcolor=fill, line=dict(color=line, width=1),
                                     hoverinfo="skip", showlegend=False))
            mx.append(f.rep_x); my.append(f.rep_y)
            mtext.append(f"#{f.id} · {f.area:.0f} mm²" + (f" · {comp}" if comp else ""))
            mcustom.append(f.id); mcolor.append(colors.get(comp, "#c00") if comp else "#333")
        fig.add_trace(go.Scatter(x=mx, y=my, mode="markers", name="faces",
                                 marker=dict(size=9, color=mcolor, line=dict(width=1, color="white")),
                                 text=mtext, customdata=mcustom,
                                 hovertemplate="%{text}<extra>lasso/box to pick</extra>"))
        fig.add_vline(x=axis_x, line=dict(color="#0a84ff", dash="dash"), annotation_text="axis")
        fig.update_layout(title=f"Pick faces — {side} half ({len(faces)} candidates)",
                          xaxis_title="x (mm)", yaxis_title="y (mm)", yaxis_scaleanchor="x",
                          height=720, dragmode="lasso", clickmode="event+select")
        return fig

    def spool(upload):
        tmp = Path(tempfile.gettempdir()) / f"tyre_{upload.name}"
        tmp.write_bytes(upload.getbuffer())
        return tmp

    # ------------------------------------------------------------------ #
    st.set_page_config(page_title="Tyre Cross-Section Analysis", layout="wide")
    st.title("Tyre Cross-Section Analysis")
    mode = st.sidebar.radio("Mode", ["Layer-based (conforming files)", "Manual loop builder (raw drawings)"])
    st.sidebar.divider()
    if st.sidebar.button("Generate demo DXFs → ./sample_data"):
        b, i = make_samples()
        st.sidebar.success(f"Wrote {b} and {i}")

    # =============================== MODE 1 =========================== #
    if mode.startswith("Layer"):
        st.caption("For files where each component is one closed curve on its own named layer.")
        uploads = st.sidebar.file_uploader("DXF files (1–5)", type=["dxf"], accept_multiple_files=True)
        axis_x = st.sidebar.number_input("Rotation axis X (mm)", value=0.0, step=1.0)
        centroid_tol = st.sidebar.number_input("Centroid-shift amber tolerance (mm)", value=1.0, step=0.5)
        flatten_tol = st.sidebar.number_input("Flatten tolerance (mm)", value=0.01, step=0.001, format="%.3f")
        if not uploads:
            st.info("Upload DXF files, or click **Generate demo DXFs** in the sidebar and upload "
                    "sample_data/baseline.dxf + iteration1.dxf.")
            st.stop()
        uploads = uploads[:5]
        paths = {u.name: spool(u) for u in uploads}
        labels = list(paths)
        baseline = st.sidebar.selectbox("Baseline", labels)

        st.header("Conformance")
        reports = {l: validate_dxf(p) for l, p in paths.items()}
        cols = st.columns(len(labels))
        for col, l in zip(cols, labels):
            with col:
                st.metric(l, "✅" if reports[l].conforms else "⛔")
                with st.expander("details", expanded=not reports[l].conforms):
                    st.code(format_report(reports[l]))
        conforming = [l for l in labels if reports[l].conforms]
        if not conforming:
            st.error("No conforming files. Use the **Manual loop builder** mode for raw drawings.")
            st.stop()
        if baseline not in conforming:
            baseline = conforming[0]

        st.header("Specific gravity (g/cm³)")
        seed = {normalise_key(k): v for k, v in DEFAULT_SG_TABLE.items()}
        for l in conforming:
            for lay in reports[l].component_layers:
                seed.setdefault(normalise_key(lay), 1.10)
        sg_df = pd.DataFrame([{"component": k, "sg_g_cm3": v} for k, v in sorted(seed.items())])
        edited = st.data_editor(sg_df, num_rows="dynamic", use_container_width=True)
        sg_table = {normalise_key(r["component"]): float(r["sg_g_cm3"])
                    for _, r in edited.iterrows() if r["component"]}

        frames, geoms_by_file = [], {}
        for l in conforming:
            load = load_dxf(paths[l], flatten_tol=flatten_tol)
            df_i, geoms, _ = analyse_loops(l, load.loops, axis_x, sg_table)
            frames.append(df_i); geoms_by_file[l] = geoms
        df = pd.concat(frames, ignore_index=True)

        st.header("Tire-level summary")
        scols = st.columns(len(conforming))
        base_total = df[df["file"] == baseline]["weight_g"].sum()
        for col, l in zip(scols, conforming):
            g = df[df["file"] == l]
            with col:
                st.subheader(l + (" (baseline)" if l == baseline else ""))
                st.metric("Total weight (g)", f"{g['weight_g'].sum():.1f}",
                          None if l == baseline else f"{g['weight_g'].sum()-base_total:+.1f}")
                st.metric("Total polar MOI", f"{g['polar_MOI'].sum():.3e}")
                cg = (g["centroid_distance_mm"] * g["weight_g"]).sum() / (g["weight_g"].sum() or 1)
                st.metric("CG offset (mm)", f"{cg:.2f}")

        st.header("Component comparison")
        wide = df.pivot_table(index="component", columns="file", values="weight_g", sort=False)
        dist = df.pivot_table(index="component", columns="file", values="centroid_distance_mm", sort=False)
        files = [baseline] + [f for f in conforming if f != baseline]
        table = pd.DataFrame(index=wide.index)
        for f in files:
            table[f"{f} · weight_g"] = wide.get(f)
            if f != baseline:
                table[f"Δw {f}"] = wide[f] - wide[baseline]
                table[f"Δcg {f}"] = dist[f] - dist[baseline]
        st.dataframe(table.style.format("{:.1f}", na_rep="–"), use_container_width=True)
        st.caption("Δw = weight change vs baseline; Δcg = centroid-distance shift.")

        st.header("Graphics")
        c1, c2 = st.columns(2)
        c1.plotly_chart(overlay_fig(geoms_by_file, baseline, axis_x), use_container_width=True)
        c2.plotly_chart(centroid_fig(df, geoms_by_file, baseline), use_container_width=True)
        st.plotly_chart(moi_fig(df), use_container_width=True)
        others = [l for l in conforming if l != baseline]
        if others:
            final = st.selectbox("Waterfall target", others, index=len(others) - 1)
            st.plotly_chart(waterfall_fig(df, baseline, final), use_container_width=True)

    # =============================== MODE 2 =========================== #
    else:
        st.caption("For raw drawings (all on layer 0, open curves). Reconstruct → pick faces → compute.")
        upload = st.file_uploader("Non-conforming DXF", type=["dxf"])
        if not upload:
            st.info("Upload a raw full-section DXF.")
            st.stop()
        c1, c2, c3 = st.columns(3)
        flatten_tol = c1.number_input("Flatten tol (mm)", 0.001, 1.0, 0.05, 0.01, format="%.3f")
        min_area = c2.number_input("Min face area (mm²)", 0.0, 100.0, 1.0, 0.5)
        dedupe = c3.checkbox("Merge duplicate faces", value=True)

        @st.cache_data(show_spinner="Reconstructing…")
        def _rec(data, name, tol, ma, dd):
            tmp = Path(tempfile.gettempdir()) / f"manual_{name}"
            tmp.write_bytes(data)
            return reconstruct(tmp, flatten_tol=tol, min_area=ma, dedupe=dd)

        rec = _rec(upload.getbuffer().tobytes(), upload.name, flatten_tol, min_area, dedupe)
        st.success(f"{len(rec.curves)} curves → {len(rec.faces)} candidate faces. "
                   f"x[{rec.xmin:.0f},{rec.xmax:.0f}] y[{rec.ymin:.0f},{rec.ymax:.0f}].")

        a1, a2 = st.columns([2, 1])
        axis_x = a1.number_input("Rotation axis X (mm)", value=float(round(rec.suggested_axis_x, 3)),
                                 step=1.0, help=f"Auto-suggested {rec.suggested_axis_x:.2f}.")
        side = a2.selectbox("Work on half", ["right", "left", "both"])
        faces = rec.faces_on_side(axis_x, side)

        skey = f"assign::{upload.name}"
        if skey not in st.session_state:
            st.session_state[skey] = {}
        assignments = st.session_state[skey]

        with st.expander("Load / save assignments"):
            load = st.file_uploader("Load JSON", type=["json"], key="loadassign")
            if load is not None and st.button("Apply loaded"):
                st.session_state[skey] = {k: list(map(int, v)) for k, v in json.load(load).items()}
                st.rerun()
            st.download_button("Save assignments", json.dumps(assignments, indent=2),
                               file_name=f"{Path(upload.name).stem}_assignments.json")

        colors = comp_colors(list(assignments) or ["_"])
        event = st.plotly_chart(face_picker_fig(rec, faces, axis_x, side, assignments, colors),
                                use_container_width=True, on_select="rerun", key="facepick")
        selected = []
        if event and getattr(event, "selection", None):
            for pt in event["selection"]["points"]:
                cd = pt.get("customdata")
                if cd is not None:
                    selected.append(int(cd if not isinstance(cd, list) else cd[0]))
        selected = sorted(set(selected))
        st.write(f"**Selected faces:** {selected or '—'} "
                 f"({sum(f.area for f in faces if f.id in selected):.0f} mm²)")

        b1, b2, b3 = st.columns([2, 1, 1])
        name = b1.text_input("Component name", placeholder="TREAD, SIDEWALL, BEAD…")
        if b2.button("➕ Assign", disabled=not (selected and name)):
            key = normalise_key(name)
            assignments[key] = sorted(set(assignments.get(key, [])) | set(selected))
            st.rerun()
        if b3.button("🗑 Clear all"):
            st.session_state[skey] = {}
            st.rerun()

        for comp, ids in list(assignments.items()):
            cc1, cc2, cc3 = st.columns([2, 3, 1])
            cc1.markdown(f"**{comp}**")
            cc2.caption(f"faces {ids} · {sum(f.area for f in rec.faces if f.id in ids):.0f} mm²")
            if cc3.button("remove", key=f"rm_{comp}"):
                del assignments[comp]; st.rerun()

        st.subheader("Specific gravity (g/cm³)")
        seed = {normalise_key(k): v for k, v in DEFAULT_SG_TABLE.items()}
        for comp in assignments:
            seed.setdefault(comp, 1.10)
        sg_df = pd.DataFrame([{"component": k, "sg_g_cm3": v} for k, v in sorted(seed.items())])
        edited = st.data_editor(sg_df, num_rows="dynamic", use_container_width=True, key="sgman")
        sg_table = {normalise_key(r["component"]): float(r["sg_g_cm3"])
                    for _, r in edited.iterrows() if r["component"]}

        if st.button("📊 Compute results", type="primary", disabled=not assignments):
            components, errs = {}, []
            for comp, ids in assignments.items():
                try:
                    components[comp] = assemble_component(rec.faces, ids)
                except ValueError as e:
                    errs.append(f"{comp}: {e}")
            for e in errs:
                st.error(e)
            if components:
                base = Path(upload.name).stem
                df, geoms, warns = analyse_components(base, components, axis_x, sg_table)
                for w in warns:
                    st.warning(w)
                show = ["component", "area_mm2", "centroid_distance_mm", "volume_cm3",
                        "weight_g", "surface_area_mm2", "polar_MOI", "pct_of_total_weight"]
                st.dataframe(df[show].round(2), use_container_width=True)
                st.metric("Total weight (one half, g)", f"{df['weight_g'].sum():.1f}")
                st.plotly_chart(overlay_fig({base: geoms}, base, axis_x), use_container_width=True)
                st.plotly_chart(moi_fig(df), use_container_width=True)
                st.download_button("Download results CSV", df.to_csv(index=False),
                                   file_name=f"{base}_manual_results.csv", mime="text/csv")


# ========================================================================== #
# Entry point
# ========================================================================== #
if __name__ == "__main__":
    if _under_streamlit():
        _run_ui()
    elif len(sys.argv) > 1 and sys.argv[1] == "--cli":
        raise SystemExit(_cli(sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--make-samples":
        b, i = make_samples()
        print(f"Wrote {b} and {i}")
    # Plain `python tyre_tool.py` is handled by the early guard above, which
    # relaunches under Streamlit and exits before reaching this point.
