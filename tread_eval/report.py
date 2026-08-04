"""Assemble the analysis into one self-contained interactive HTML file.

The Python side computes everything and hands the browser a finished JSON
payload; the JavaScript only draws.  That split is deliberate -- it keeps the
numerics testable in Python, and it means the deliverable is a single file that
opens with no server, no network and no build step.

Plotly is embedded rather than linked, so the file works offline and behind a
locked-down proxy.  The bundle is taken from the installed ``plotly`` package
(a build-time dependency only) or from an explicit path.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import json
import math
import os
from typing import Any, Sequence

import numpy as np

from . import markdown as _md
from . import metrics as M
from .contact_patch import CPParams, max_supported_lean
from .raster import RasterPack
from .schema import Pattern, ZONES, zone_bounds
from .stiffness import StiffnessParams
from .sweep import LeanResult

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
GUIDE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "GUIDE.md")


# ---------------------------------------------------------------------------
def _round(obj: Any, nd: int = 4) -> Any:
    """Recursively round floats, so the embedded JSON stays a sane size."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return round(obj, nd)
    if isinstance(obj, (np.floating, np.integer)):
        return _round(float(obj), nd)
    if isinstance(obj, np.ndarray):
        return _round(obj.tolist(), nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v, nd) for v in obj]
    return obj


def _decimate_polygon(poly: Sequence[tuple[float, float]], tol: float = 0.15) -> list[list[float]]:
    """Drop vertices that lie within ``tol`` mm of the chord they sit on.

    The synthetic generator subdivides curved edges finely; at report resolution
    most of those points are invisible, and they are the bulk of the payload.
    """
    pts = [list(map(float, p)) for p in poly]
    if len(pts) <= 4:
        return pts
    keep = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = keep[-1]
        bx, by = pts[i + 1]
        px, py = pts[i]
        dx, dy = bx - ax, by - ay
        den = math.hypot(dx, dy)
        dist = abs(dy * px - dx * py + bx * ay - by * ax) / den if den > 1e-9 else 0.0
        if dist > tol:
            keep.append(pts[i])
    keep.append(pts[-1])
    return keep


def _cp_provenance(results) -> str:
    """One word for how the contact patches were obtained across the sweep."""
    sources = {r.patch.source for r in results}
    if sources == {"measured"}:
        return "measured"
    if "measured" in sources:
        return "partly-measured"
    if sources & {"transferred", "interpolated"}:
        return "derived-from-measured"
    return "generated"


def _display_stride(n: int, target: int) -> int:
    return max(1, int(round(n / max(target, 1))))


# ---------------------------------------------------------------------------
def build_payload(
    pattern: Pattern,
    pack: RasterPack,
    results: Sequence[LeanResult],
    cp_params: CPParams,
    stiffness_params: StiffnessParams,
    theta_points: int = 720,
    notes: str = "",
    config=None,
) -> dict:
    """Everything the browser needs, as plain JSON-able data."""
    grid = pack.grid
    stride = _display_stride(grid.nx, theta_points)

    summary = M.lean_summary(results)
    zones = M.zone_distribution(pattern, pack)
    pitch = M.pitch_sequence_analysis(pattern, pack)
    wear = M.wear_proxies(pattern, pack, results)
    flags = M.build_flags(pattern, results, summary, zones, pitch, wear)

    leans = []
    for r in results:
        d = r.as_dict(stride)
        d["spectra"] = {
            "area": M.order_spectrum(r.contact_area),
            "kx": M.order_spectrum(r.kx),
            "block_count": M.order_spectrum(r.block_count),
        }
        d["anomalies"] = {
            "area": M.anomaly_bands(r.theta_deg, r.contact_area),
            "kx": M.anomaly_bands(r.theta_deg, r.kx),
            "ky": M.anomaly_bands(r.theta_deg, r.ky),
        }
        d["groove"] = M.groove_angle_profile(pattern, r.patch)
        d["centroid"] = {
            k: v for k, v in M.centroid_decomposition(r).items() if k != "residual"
        }
        d["anisotropy"] = (r.kx / np.maximum(r.ky, 1e-9))[::stride].tolist()
        leans.append(d)

    blocks = []
    for b in pattern.blocks:
        poly = _decimate_polygon(b.polygon)
        s = pack.stiffness[b.id]
        blocks.append(
            {
                "id": b.id,
                "zone": b.zone,
                "pitch": b.pitch_id,
                "poly": poly,
                "kx": s.kx,
                "ky": s.ky,
                "kxy": s.kxy,
                "kz": s.kz,
                "area": s.area,
                "net_area": s.net_area,
                "shape_factor": s.shape_factor,
                "h": b.height,
                "draft": b.draft_angle,
                "sipes": len(b.effective_sipes()),
                "slender": s.slenderness,
            }
        )

    crown = pattern.crown()
    cstride = max(1, crown.y.size // 200)

    payload = {
        "meta": {
            "name": pattern.name,
            "source": pattern.source,
            "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "circumference": pattern.tyre_circumference,
            "tread_width": pattern.tread_width,
            "n_blocks": len(pattern.blocks),
            "n_pitches": len(pattern.pitches),
            "resolution_mm": grid.dx,
            "grid": [grid.ny, grid.nx],
            "curvature_correction": pack.curvature_correction,
            "crown_measured": crown.measured,
            "max_supported_lean": max_supported_lean(crown),
            "pattern_meta": pattern.meta,
            "notes": notes,
            "cp_params": {
                "vertical_load_N": cp_params.vertical_load,
                "foundation_modulus": cp_params.foundation_modulus,
                "wheel_radius_mm": cp_params.wheel_radius,
                "load_rises_with_lean": cp_params.load_rises_with_lean,
                "slip_angle_deg": cp_params.lateral_slip_angle,
            },
            "stiffness_params": {
                "shore_a": stiffness_params.shore_a,
                "youngs_modulus_MPa": stiffness_params.youngs(),
                "shear_modulus_MPa": stiffness_params.shear(),
                "gent_k": stiffness_params.gent_k(),
                "poisson": stiffness_params.poisson,
                "boundary_mode": stiffness_params.mode,
                "bulk_modulus_MPa": stiffness_params.bulk_modulus,
                "model": "Okonieski et al. (2003) beam mechanics + Gent (1959) compression",
                "verified_against": "Tread_Pattern_Stiffness_Estimation_Tool_v6.4",
            },
            "zone_bounds": zone_bounds(pattern.tread_width),
            "thresholds": M.THRESHOLDS,
            # The full configuration, so the report is self-documenting and the
            # run can be reproduced from the file alone.
            "config": (config.to_dict() if config is not None else None),
            # Per-input provenance, so the report can state exactly which parts
            # are measured and which are modelled rather than carrying a blanket
            # "everything is a placeholder" disclaimer that stops being true.
            "provenance": {
                "geometry": (
                    "measured" if pattern.source == "dxf"
                    else ("imported" if pattern.source not in ("synthetic",) else "synthetic")
                ),
                "geometry_detail": (
                    f"imported from {os.path.basename(pattern.meta.get('dxf_path', ''))}"
                    if pattern.source == "dxf" else "generated placeholder pattern"
                ),
                "block_depth": (
                    "assumed" if pattern.source == "dxf" else "synthetic"
                ),
                "stiffness": "model",
                "crown": "measured" if crown.measured else "assumed",
                "contact_patch": _cp_provenance(results),
                "thresholds": "uncalibrated",
            },
        },
        "pattern": {
            "blocks": blocks,
            "pitch_starts": [p.circumferential_start for p in pattern.pitches],
            "pitch_lengths": [p.circumferential_length for p in pattern.pitches],
            "crown": {
                "y": crown.y[::cstride].tolist(),
                "z": crown.z[::cstride].tolist(),
                "y_proj": crown.y_proj[::cstride].tolist(),
                "r": crown.r[::cstride].tolist(),
                "phi_deg": np.degrees(crown.phi[::cstride]).tolist(),
            },
        },
        "leans": leans,
        "summary": summary,
        "zones": zones,
        "pitch": pitch,
        "wear": wear,
        "flags": flags,
        "zone_order": list(ZONES),
    }
    return _round(payload, 4)


# ---------------------------------------------------------------------------
def _find_plotly(explicit: str | None = None) -> tuple[str, str]:
    """Locate a plotly bundle. Returns (javascript_source, provenance)."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(ASSETS, "plotly.min.js"))
    try:  # the pip package ships the minified bundle as package data
        import plotly  # noqa: PLC0415

        candidates.append(os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js"))
    except Exception:  # pragma: no cover - plotly is a build-time dep only
        pass
    for path in candidates:
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read(), path
    raise FileNotFoundError(
        "Could not find plotly.min.js. Install the build dependency with "
        "`pip install plotly`, or pass --plotly-js /path/to/plotly.min.js, "
        "or use --plotly cdn for a non-self-contained file."
    )


CDN_TAG = '<script src="https://cdn.plot.ly/plotly-3.0.0.min.js" charset="utf-8"></script>'


def render_html(payload: dict, plotly_mode: str = "embed", plotly_js: str | None = None) -> str:
    with open(os.path.join(ASSETS, "template.html"), encoding="utf-8") as fh:
        template = fh.read()
    with open(os.path.join(ASSETS, "app.css"), encoding="utf-8") as fh:
        css = fh.read()
    with open(os.path.join(ASSETS, "app.js"), encoding="utf-8") as fh:
        js = fh.read()

    if plotly_mode == "cdn":
        plotly_block = CDN_TAG
        provenance = "CDN (file is NOT self-contained)"
    else:
        src, provenance = _find_plotly(plotly_js)
        plotly_block = "<script>" + src + "</script>"

    # The reader's guide is authored once in GUIDE.md and converted here, so
    # the repo copy and the in-report tab cannot drift apart.
    guide_html = ""
    if os.path.exists(GUIDE):
        with open(GUIDE, encoding="utf-8") as fh:
            guide_html = _md.render(fh.read())

    data = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    # Guard against the payload closing the enclosing <script> element.
    data = data.replace("</", "<\\/")

    return (
        template.replace("/*__CSS__*/", css)
        .replace("/*__DATA__*/", data)
        .replace("/*__APP__*/", js)
        .replace("<!--__PLOTLY__-->", plotly_block)
        .replace("<!--__GUIDE__-->", guide_html)
        # Escaped: the pattern name comes from a filename or a DXF, and it is
        # substituted into markup, not into the JSON payload.
        .replace("__TITLE__", _html.escape(f"{payload['meta']['name']} - 2W tread pattern evaluation"))
        .replace("__PLOTLY_PROVENANCE__", _html.escape(os.path.basename(provenance)))
    )


def write_report(
    path: str,
    pattern: Pattern,
    pack: RasterPack,
    results: Sequence[LeanResult],
    cp_params: CPParams,
    stiffness_params: StiffnessParams,
    theta_points: int = 720,
    plotly_mode: str = "embed",
    plotly_js: str | None = None,
    notes: str = "",
    config=None,
) -> dict:
    payload = build_payload(
        pattern, pack, results, cp_params, stiffness_params, theta_points, notes, config
    )
    html = render_html(payload, plotly_mode, plotly_js)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return payload
