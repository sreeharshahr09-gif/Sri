"""Diagnostics computed from the theta x gamma sweep and from the pattern itself.

Nothing here does heavy lifting -- :mod:`tread_eval.sweep` has already reduced
the pattern to curves.  This module turns those curves into the numbers a tread
designer would actually argue about, and into flags.

Flag thresholds are PLACEHOLDERS.  They are gathered in :data:`THRESHOLDS` so
they can be recalibrated in one place once there is correlation data against
real noise/wear measurements; nothing else in the module hard-codes a limit.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Sequence

import numpy as np

from .contact_patch import ContactPatch
from .raster import RasterPack
from .schema import Pattern, ZONES, zone_bounds
from .sweep import LeanResult

THRESHOLDS = {
    "area_cov_watch": 0.05,  # coefficient of variation of in-patch area over 360 deg
    "area_cov_flag": 0.10,
    "area_maxmin_flag": 1.35,
    "order_amplitude_watch": 0.03,  # single-order amplitude as a fraction of the mean
    "order_amplitude_flag": 0.06,
    "stiffness_gradient_flag": 3.0,  # local |dK/dtheta| in units of sigma
    "anisotropy_span_flag": 0.35,  # peak-to-peak of Kx/Ky as a fraction of its mean
    "block_count_min_watch": 3.0,  # below this, the patch barely averages anything
    "centroid_residual_flag": 3.0,  # mm of pattern-induced lateral wander
    "shoulder_phase_watch": 0.15,  # |left-right| shoulder phase, fraction of a pitch
    "slenderness_flag": 1.4,  # block height / min plan dimension
    "pitch_step_flag": 1.25,  # largest adjacent pitch-length ratio
    "sequence_tonality_watch": 0.60,  # spectral concentration of the pitch sequence, 0-1
    "sequence_tonality_flag": 0.78,
    "zone_imbalance_flag": 0.12,  # |left - right| land ratio difference
}


# ---------------------------------------------------------------------------
# generic signal helpers
# ---------------------------------------------------------------------------
def fluctuation_stats(sig: np.ndarray) -> dict[str, float]:
    sig = np.asarray(sig, dtype=float)
    mean = float(sig.mean())
    if mean <= 0 or not np.isfinite(mean):
        return {"mean": mean, "std": float("nan"), "cov": float("nan"), "max_min": float("nan"), "p2p_pct": float("nan")}
    lo, hi = float(sig.min()), float(sig.max())
    return {
        "mean": mean,
        "std": float(sig.std()),
        "cov": float(sig.std() / mean),
        "min": lo,
        "max": hi,
        "max_min": (hi / lo) if lo > 0 else float("inf"),
        "p2p_pct": 100.0 * (hi - lo) / mean,
    }


def order_spectrum(sig: np.ndarray, max_order: int = 80) -> dict[str, list[float]]:
    """Amplitude per rotation order, normalised by the signal mean.

    Order ``k`` means ``k`` events per wheel revolution -- exactly the axis a
    noise engineer reads pitch sequences on.  Amplitudes are single-sided, so
    order ``k`` with amplitude 0.04 means a +-4% modulation of the mean at
    ``k`` per rev.
    """
    sig = np.asarray(sig, dtype=float)
    n = sig.size
    spec = np.fft.rfft(sig - sig.mean()) / n * 2.0
    amp = np.abs(spec)
    mean = float(sig.mean())
    orders = np.arange(amp.size)
    keep = (orders >= 1) & (orders <= max_order)
    norm = amp[keep] / mean if mean > 0 else amp[keep]
    return {"orders": orders[keep].astype(float).tolist(), "amplitude": norm.tolist()}


def dominant_orders(spec: dict[str, list[float]], top: int = 5) -> list[dict[str, float]]:
    amp = np.asarray(spec["amplitude"])
    orders = np.asarray(spec["orders"])
    if amp.size == 0:
        return []
    idx = np.argsort(amp)[::-1][:top]
    return [{"order": float(orders[i]), "amplitude": float(amp[i])} for i in idx]


def anomaly_bands(theta: np.ndarray, sig: np.ndarray, n_sigma: float = 3.0) -> list[dict[str, float]]:
    """Contiguous theta ranges where the signal changes abnormally fast.

    Gradient-based rather than level-based: a tread designer cares about an
    abrupt step in stiffness as a block leaves contact, not about a smooth
    high-or-low region.
    """
    sig = np.asarray(sig, dtype=float)
    if sig.size < 8:
        return []
    grad = np.abs(np.gradient(sig))
    sigma = float(np.std(grad))
    if sigma <= 0:
        return []
    hot = grad > (float(np.mean(grad)) + n_sigma * sigma)
    if not hot.any():
        return []
    bands: list[dict[str, float]] = []
    idx = np.flatnonzero(hot)
    start = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i != prev + 1:
            bands.append({"start": float(theta[start]), "end": float(theta[prev]), "peak": float(grad[start : prev + 1].max())})
            start = i
        prev = i
    bands.append({"start": float(theta[start]), "end": float(theta[prev]), "peak": float(grad[start : prev + 1].max())})
    # Merge the wrap-around pair if the signal is hot at both ends.
    if len(bands) > 1 and bands[0]["start"] <= theta[0] + 1e-9 and bands[-1]["end"] >= theta[-1] - 1e-9:
        bands[0]["start"] = bands[-1]["start"] - 360.0
        bands.pop()
    return bands


# ---------------------------------------------------------------------------
# pattern-level (contact-patch independent)
# ---------------------------------------------------------------------------
def zone_distribution(pattern: Pattern, pack: RasterPack, n_bins: int = 48) -> dict:
    """Land/sea by lateral bin and by zone, independent of the contact patch."""
    grid = pack.grid
    land_per_row = pack.land.sum(axis=1) * grid.dx  # mm of rubber per row, per rev
    row_span = grid.circumference
    ratio_per_row = land_per_row / row_span

    edges = np.linspace(-pattern.tread_width / 2, pattern.tread_width / 2, n_bins + 1)
    which = np.clip(np.digitize(grid.y, edges) - 1, 0, n_bins - 1)
    binned = np.zeros(n_bins)
    counts = np.zeros(n_bins)
    np.add.at(binned, which, ratio_per_row)
    np.add.at(counts, which, 1.0)
    binned = np.where(counts > 0, binned / np.maximum(counts, 1), 0.0)

    # Area per unit circumferential length, per zone (the "mass distribution").
    zones = {}
    for z in ZONES:
        area = float(pack.zone_area[z].sum())
        blocks = pattern.zone_blocks(z)
        # Gross area of the zone bands, both sides, using the curvature weights.
        lo, hi = zone_bounds(pattern.tread_width)[z]
        rows = (np.abs(grid.y) >= lo) & (np.abs(grid.y) < hi + 1e-9)
        gross = float((pack.lateral_weight[rows]).sum()) * grid.dy * grid.circumference
        zones[z] = {
            "land_area_mm2": area,
            "gross_area_mm2": gross,
            "land_ratio": area / gross if gross > 0 else float("nan"),
            "area_per_mm_circumference": area / grid.circumference,
            "n_blocks": len(blocks),
            "mean_block_area": float(np.mean([b.area() for b in blocks])) if blocks else 0.0,
        }

    left = grid.y < 0
    right = grid.y > 0
    left_ratio = float(ratio_per_row[left].mean())
    right_ratio = float(ratio_per_row[right].mean())

    return {
        "bin_centers": (0.5 * (edges[1:] + edges[:-1])).tolist(),
        "bin_land_ratio": binned.tolist(),
        "zones": zones,
        "overall_land_ratio": float(pack.area.sum() / (grid.circumference * grid.tread_width)),
        "left_land_ratio": left_ratio,
        "right_land_ratio": right_ratio,
        "lateral_imbalance": abs(left_ratio - right_ratio),
    }


def pitch_sequence_analysis(pattern: Pattern, pack: RasterPack) -> dict:
    """Pitch length statistics, sequence order content and shoulder phasing."""
    lengths = np.array([p.circumferential_length for p in pattern.pitches], dtype=float)
    n = lengths.size
    result: dict = {
        "lengths": lengths.tolist(),
        "n_pitches": int(n),
        "mean": float(lengths.mean()) if n else float("nan"),
        "min": float(lengths.min()) if n else float("nan"),
        "max": float(lengths.max()) if n else float("nan"),
        "unique": sorted({round(float(v), 3) for v in lengths}),
        "closure_error_mm": float(lengths.sum() - pattern.tyre_circumference),
    }
    if n >= 4:
        ratios = lengths / np.roll(lengths, 1)
        ratios = np.maximum(ratios, 1.0 / ratios)
        result["max_adjacent_ratio"] = float(ratios.max())
        spec = np.abs(np.fft.rfft(lengths - lengths.mean())) / n * 2.0
        norm = spec / lengths.mean()
        result["sequence_spectrum"] = {
            "orders": np.arange(spec.size).astype(float).tolist(),
            "amplitude": norm.tolist(),
        }
        # A sequence that repeats a fixed group concentrates energy on the
        # repeat order -- the classic tonal-noise failure mode.
        k = int(np.argmax(norm[1:]) + 1) if norm.size > 1 else 0
        result["dominant_sequence_order"] = k
        result["dominant_sequence_amplitude"] = float(norm[k]) if k else 0.0
        result["modulation_index"] = float((lengths.max() - lengths.min()) / lengths.mean())
        # Peak height alone does not separate a tonal sequence from a random one
        # -- both can have a similar tallest bar. What distinguishes them is how
        # concentrated the energy is. Crest factor divided by its own maximum
        # (sqrt of the number of orders) gives a bounded 0-1 concentration:
        # 1.0 = every bit of energy on one order, ~0.5 = spread like noise.
        body = norm[1:]
        rms = float(np.sqrt(np.mean(body**2))) if body.size else 0.0
        result["sequence_tonality"] = (
            float(body.max() / rms / np.sqrt(body.size)) if rms > 0 and body.size else 0.0
        )

    result.update(_shoulder_phase(pattern, pack))
    return result


def _shoulder_phase(pattern: Pattern, pack: RasterPack) -> dict:
    """Circumferential phase offset between the two shoulders.

    A deliberate stagger is normal design practice; what matters is knowing what
    it actually is, because zero offset stacks both shoulders' impacts on the
    same order and doubles the excitation.
    """
    grid = pack.grid
    lo, hi = zone_bounds(pattern.tread_width)["shoulder"]
    left_rows = (grid.y <= -lo) & (grid.y >= -hi - 1e-9)
    right_rows = (grid.y >= lo) & (grid.y <= hi + 1e-9)
    if not left_rows.any() or not right_rows.any():
        return {"shoulder_phase_mm": float("nan"), "shoulder_phase_pitches": float("nan")}

    a = pack.land[left_rows].sum(axis=0)
    b = pack.land[right_rows].sum(axis=0)
    a = a - a.mean()
    b = b - b.mean()
    if a.std() <= 0 or b.std() <= 0:
        return {"shoulder_phase_mm": 0.0, "shoulder_phase_pitches": 0.0}
    xc = np.fft.irfft(np.fft.rfft(b) * np.conj(np.fft.rfft(a)), n=grid.nx)
    lag = int(np.argmax(xc))
    if lag > grid.nx // 2:
        lag -= grid.nx
    mm = lag * grid.dx
    mean_pitch = float(np.mean([p.circumferential_length for p in pattern.pitches])) if pattern.pitches else float("nan")
    # Fold onto [-0.5, 0.5] pitches: an offset of one whole pitch is no offset.
    pitches = mm / mean_pitch if mean_pitch and np.isfinite(mean_pitch) else float("nan")
    if np.isfinite(pitches):
        pitches = float(pitches - round(pitches))
        # Report the offset in mm consistent with the folded pitch fraction --
        # a raw lag of one whole pitch is no offset at all, and printing it as
        # "+91 mm, 0.00 pitch" reads like a contradiction.
        mm = pitches * mean_pitch
    return {
        "shoulder_phase_mm": float(mm),
        "shoulder_phase_pitches": pitches,
        "shoulder_correlation": float(np.max(xc) / (a.std() * b.std() * grid.nx)),
    }


def groove_angle_profile(pattern: Pattern, patch: ContactPatch, n_bins: int = 24) -> dict:
    """Groove angle vs. the instantaneous rolling direction, by lateral position.

    Two numbers per lateral bin:

    ``angle_to_travel``  the angle between the groove-defining edges and the
    direction of travel at the patch centre.  90 deg is a purely lateral groove,
    0 deg a purely circumferential one.

    ``spin_spread``  how much that angle swings as a block traverses the patch.
    A leaned tyre corners, so the patch spins about the vertical; a point ``x``
    ahead of the patch centre travels at ``atan(x / R_path)`` to the centreline.
    The same groove therefore meets the flow at ``angle_to_travel +- spin_spread``
    between entering and leaving contact.  This is the reason the brief insists
    the metric be travel-relative and not axis-relative: at zero lean the spread
    is zero and the distinction does not exist, and at lean it is several degrees.
    """
    half = pattern.tread_width / 2.0
    edges = np.linspace(-half, half, n_bins + 1)
    centers = 0.5 * (edges[1:] + edges[:-1])
    sums = np.zeros(n_bins)
    weights = np.zeros(n_bins)

    for block in pattern.blocks:
        pts = np.asarray(block.polygon, dtype=float)
        d = np.roll(pts, -1, axis=0) - pts
        mid = pts + 0.5 * d
        length = np.hypot(d[:, 0], d[:, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            # Angle of the edge from the circumferential axis, folded to [0, 90].
            ang = np.degrees(np.arctan2(np.abs(d[:, 1]), np.abs(d[:, 0])))
        # Only lateral-running edges define the lateral grooves whose angle the
        # designer sweeps from crown to shoulder; circumferential edges are the
        # rib walls and would just drag the average toward zero.
        lateral_edge = ang > 45.0
        if not lateral_edge.any():
            continue
        idx = np.clip(np.digitize(mid[lateral_edge, 1], edges) - 1, 0, n_bins - 1)
        np.add.at(sums, idx, ang[lateral_edge] * length[lateral_edge])
        np.add.at(weights, idx, length[lateral_edge])

    angle = np.where(weights > 0, sums / np.maximum(weights, 1e-9), np.nan)

    # Half-length of the patch at each lateral position -> local spin spread.
    with np.errstate(invalid="ignore"):
        inside = 1.0 - ((centers - patch.y_center) / patch.b) ** 2
    a_local = patch.a * np.sqrt(np.clip(inside, 0.0, 1.0))
    spread = np.abs(np.asarray(patch.travel_direction_deg(a_local)) - patch.slip_angle_deg)
    spread = np.where(a_local > 0, spread, np.nan)

    return {
        "bin_centers": centers.tolist(),
        "angle_to_travel": [None if not np.isfinite(v) else float(v) for v in angle],
        "spin_spread": [None if not np.isfinite(v) else float(v) for v in spread],
        "in_patch": (a_local > 0).tolist(),
        "gamma_deg": patch.gamma_deg,
        "path_radius": None if not np.isfinite(patch.path_radius) else float(patch.path_radius),
    }


def wear_proxies(pattern: Pattern, pack: RasterPack, results: Sequence[LeanResult]) -> dict:
    """Slenderness, sipe density and in-contact block-count fluctuation."""
    per_zone: dict[str, dict] = {}
    for z in ZONES:
        blocks = pattern.zone_blocks(z)
        if not blocks:
            continue
        sl = np.array([pack.stiffness[b.id].slenderness for b in blocks])
        n_sipes = np.array([len(b.effective_sipes()) for b in blocks])
        area = np.array([b.area() for b in blocks])
        per_zone[z] = {
            "slenderness_mean": float(sl.mean()),
            "slenderness_max": float(sl.max()),
            "slenderness_p90": float(np.percentile(sl, 90)),
            "sipes_mean": float(n_sipes.mean()),
            "area_mean": float(area.mean()),
            "area_min": float(area.min()),
            "n_blocks": len(blocks),
        }
    block_fluct = {
        f"{r.gamma_deg:g}": fluctuation_stats(r.block_count) for r in results
    }
    worst = max((b["slenderness_p90"] for b in per_zone.values()), default=float("nan"))
    return {"per_zone": per_zone, "block_count_fluctuation": block_fluct, "worst_slenderness_p90": worst}


# ---------------------------------------------------------------------------
# sweep-level
# ---------------------------------------------------------------------------
def centroid_decomposition(result: LeanResult) -> dict:
    """Split contact-patch centroid wander into geometric and residual parts.

    The geometric part is where the patch centre has to be: lean plus crown
    profile put it there, and it is not a design flaw.  The residual is the
    lateral pull the *pattern* adds on top -- an asymmetric block layout tugging
    the effective centre of pressure sideways as the tyre rotates.  Only the
    residual is actionable, so they are reported separately rather than as one
    "centroid moves a lot" number.
    """
    geometric = float(result.patch.y_center)
    residual = result.centroid_y - geometric
    # The residual splits again: a constant bias (the rubber sits inboard of the
    # patch centre -- at high lean mostly because the patch is truncated by the
    # tread edge, which is still geometry) and the part that varies with theta,
    # which is the only genuinely pattern-induced component.
    bias = float(residual.mean())
    return {
        "gamma_deg": result.gamma_deg,
        "geometric_mm": geometric,
        "residual_bias_mm": bias,
        "residual_wander_rms_mm": float(residual.std()),
        "residual_p2p_mm": float(residual.max() - residual.min()),
        "residual": residual,
    }


def lean_summary(results: Sequence[LeanResult]) -> list[dict]:
    """One row per lean angle -- the backbone of the 2W-specific charts."""
    rows = []
    for r in results:
        area = fluctuation_stats(r.contact_area)
        kx = fluctuation_stats(r.kx)
        ky = fluctuation_stats(r.ky)
        blocks = fluctuation_stats(r.block_count)
        cen = centroid_decomposition(r)
        aniso = r.kx / np.maximum(r.ky, 1e-9)
        zone_mean = {z: float(v.mean()) for z, v in r.zone_area.items()}
        total_zone = sum(zone_mean.values()) or 1.0
        rows.append(
            {
                "gamma_deg": r.gamma_deg,
                "patch_area": r.patch_area,
                "patch_length": r.shape["length_mm"],
                "patch_width": r.shape["width_mm"],
                "patch_clipped": bool(r.shape["clipped"]),
                "compactness": r.shape["compactness"],
                "aspect_ratio": r.shape["aspect_ratio"],
                "pressure_skew_x": r.shape["pressure_skew_x"],
                "pressure_skew_y": r.shape["pressure_skew_y"],
                "peak_pressure": r.shape["peak_pressure_mpa"],
                "area_mean": area["mean"],
                "area_cov": area["cov"],
                "area_max_min": area["max_min"],
                "land_ratio_mean": float(r.land_ratio.mean()),
                "kx_mean": kx["mean"],
                "kx_cov": kx["cov"],
                "ky_mean": ky["mean"],
                "ky_cov": ky["cov"],
                "anisotropy_mean": float(aniso.mean()),
                "anisotropy_p2p": float(aniso.max() - aniso.min()),
                "block_count_mean": blocks["mean"],
                "block_count_min": blocks.get("min", float("nan")),
                "block_count_cov": blocks["cov"],
                "block_count_discrete_mean": float(r.block_count_discrete.mean()),
                "centroid_geometric_mm": cen["geometric_mm"],
                "centroid_residual_bias_mm": cen["residual_bias_mm"],
                "centroid_residual_wander_rms_mm": cen["residual_wander_rms_mm"],
                "centroid_residual_p2p_mm": cen["residual_p2p_mm"],
                "zone_share": {z: zone_mean[z] / total_zone for z in zone_mean},
                # Reported as shares rather than a shoulder/centre ratio: at
                # upright the shoulder share is zero and at full lean the centre
                # share is, so the ratio is 0 or infinite exactly where the
                # question ("which ribs carry the load?") is most interesting.
                "center_share": zone_mean.get("center", 0.0) / total_zone,
                "shoulder_share": zone_mean.get("shoulder", 0.0) / total_zone,
                "dominant_orders": dominant_orders(order_spectrum(r.contact_area)),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------
@dataclass
class Flag:
    id: str
    severity: str  # "ok" | "watch" | "flag"
    title: str
    value: str
    detail: str


def _sev(value: float, watch: float, flag: float) -> str:
    if not np.isfinite(value):
        return "ok"
    if value >= flag:
        return "flag"
    if value >= watch:
        return "watch"
    return "ok"


def build_flags(
    pattern: Pattern,
    results: Sequence[LeanResult],
    summary: Sequence[dict],
    zones: dict,
    pitch: dict,
    wear: dict,
) -> list[dict]:
    """Turn the metric tables into a ranked list of design flags.

    Thresholds are placeholders (:data:`THRESHOLDS`); the flags are still useful
    as a relative ranking between design variants even before calibration.
    """
    flags: list[Flag] = []
    t = THRESHOLDS

    worst_area = max(summary, key=lambda r: r["area_cov"])
    flags.append(
        Flag(
            id="area_fluctuation",
            severity=_sev(worst_area["area_cov"], t["area_cov_watch"], t["area_cov_flag"]),
            title="Contact area fluctuation over 360 deg",
            value=f"CoV {worst_area['area_cov'] * 100:.1f}% at {worst_area['gamma_deg']:g} deg lean",
            detail=(
                f"max/min {worst_area['area_max_min']:.2f}. Area swing through a revolution drives "
                f"vertical force variation; the worst lean angle is reported."
            ),
        )
    )

    all_orders = [(r["gamma_deg"], o) for r in summary for o in r["dominant_orders"][:1]]
    if all_orders:
        g, top = max(all_orders, key=lambda p: p[1]["amplitude"])
        flags.append(
            Flag(
                id="order_content",
                severity=_sev(top["amplitude"], t["order_amplitude_watch"], t["order_amplitude_flag"]),
                title="Dominant rotation order in contact area",
                value=f"order {top['order']:.0f} at {top['amplitude'] * 100:.1f}% (lean {g:g} deg)",
                detail=(
                    "A single concentrated order is what makes a pitch sequence tonal. Compare against "
                    "the pitch-sequence spectrum: an order equal to the number of repeats means the "
                    "sequence is repeating a fixed group."
                ),
            )
        )

    worst_block = min(summary, key=lambda r: r["block_count_mean"])
    sev = "flag" if worst_block["block_count_mean"] < t["block_count_min_watch"] * 0.6 else (
        "watch" if worst_block["block_count_mean"] < t["block_count_min_watch"] else "ok"
    )
    flags.append(
        Flag(
            id="block_count",
            severity=sev,
            title="Blocks in contact",
            value=f"{worst_block['block_count_mean']:.1f} effective at {worst_block['gamma_deg']:g} deg lean",
            detail=(
                "Few blocks in the patch means little spatial averaging, so every pitch transition shows up "
                "in the force signal. This is the 2W-specific risk: the patch shrinks with lean."
            ),
        )
    )

    worst_res = max(summary, key=lambda r: r["centroid_residual_p2p_mm"])
    flags.append(
        Flag(
            id="centroid_residual",
            severity=_sev(worst_res["centroid_residual_p2p_mm"], t["centroid_residual_flag"] * 0.5, t["centroid_residual_flag"]),
            title="Pattern-induced centroid wander",
            value=f"{worst_res['centroid_residual_p2p_mm']:.2f} mm peak-to-peak at {worst_res['gamma_deg']:g} deg lean",
            detail=(
                f"Geometric lateral shift from lean ({worst_res['centroid_geometric_mm']:.1f} mm) is excluded -- "
                "this is only the part the block layout adds, which is the part a designer can change."
            ),
        )
    )

    worst_aniso = max(summary, key=lambda r: r["anisotropy_p2p"] / max(r["anisotropy_mean"], 1e-9))
    span = worst_aniso["anisotropy_p2p"] / max(worst_aniso["anisotropy_mean"], 1e-9)
    flags.append(
        Flag(
            id="anisotropy",
            severity=_sev(span, t["anisotropy_span_flag"] * 0.6, t["anisotropy_span_flag"]),
            title="Kx/Ky anisotropy swing",
            value=f"{span * 100:.0f}% of mean at {worst_aniso['gamma_deg']:g} deg lean (mean ratio {worst_aniso['anisotropy_mean']:.2f})",
            detail="A ratio that moves through the revolution means the tyre's response changes with rotational position.",
        )
    )

    flags.append(
        Flag(
            id="lateral_imbalance",
            severity=_sev(zones["lateral_imbalance"], t["zone_imbalance_flag"] * 0.5, t["zone_imbalance_flag"]),
            title="Left/right land ratio imbalance",
            value=f"{zones['lateral_imbalance'] * 100:.1f} points ({zones['left_land_ratio']:.3f} vs {zones['right_land_ratio']:.3f})",
            detail="Asymmetric land distribution shows up as different behaviour leaning left vs right.",
        )
    )

    ph = pitch.get("shoulder_phase_pitches", float("nan"))
    if np.isfinite(ph):
        near_zero = abs(ph) < t["shoulder_phase_watch"]
        flags.append(
            Flag(
                id="shoulder_phase",
                severity="watch" if near_zero else "ok",
                title="Left/right shoulder phasing",
                value=f"{ph:+.2f} pitch ({pitch.get('shoulder_phase_mm', float('nan')):+.1f} mm)",
                detail=(
                    "In phase: both shoulders strike together and their excitation adds on the same order. "
                    "Out of phase: the impacts interleave."
                    if near_zero
                    else "The shoulders are staggered, so their impacts interleave rather than stacking."
                ),
            )
        )

    tonality = pitch.get("sequence_tonality")
    if tonality is not None:
        flags.append(
            Flag(
                id="pitch_tonality",
                severity=_sev(tonality, t["sequence_tonality_watch"], t["sequence_tonality_flag"]),
                title="Pitch sequence tonality",
                value=f"{tonality:.2f} concentration (peak order {pitch.get('dominant_sequence_order')})",
                detail=(
                    "How concentrated the pitch-length spectrum is, on a 0-1 scale. Around 0.5 means the "
                    "sequence spreads its energy across many orders like noise; approaching 1 means it "
                    "repeats a fixed group and dumps everything onto one order."
                ),
            )
        )

    if "max_adjacent_ratio" in pitch:
        flags.append(
            Flag(
                id="pitch_step",
                severity=_sev(pitch["max_adjacent_ratio"], t["pitch_step_flag"] * 0.9, t["pitch_step_flag"]),
                title="Largest adjacent pitch-length step",
                value=f"{pitch['max_adjacent_ratio']:.2f}x",
                detail="Large jumps between neighbouring pitches spread the spectrum but can be felt as a beat.",
            )
        )

    ws = wear.get("worst_slenderness_p90", float("nan"))
    flags.append(
        Flag(
            id="slenderness",
            severity=_sev(ws, t["slenderness_flag"] * 0.75, t["slenderness_flag"]),
            title="Block slenderness (90th percentile)",
            value=f"{ws:.2f} height/min-plan-dimension",
            detail="Slender blocks squirm, and squirm shows up as heel-and-toe wear and as lost stiffness.",
        )
    )

    clipped = [r["gamma_deg"] for r in summary if r["patch_clipped"]]
    if clipped:
        flags.append(
            Flag(
                id="patch_clipped",
                severity="watch",
                title="Contact patch runs off the tread edge",
                value=f"at {', '.join(f'{g:g}' for g in clipped)} deg lean",
                detail=(
                    "The parametric patch extends past the edge of the pattern, so contact area at those lean "
                    "angles is truncated by the tread width. Expected near maximum lean, but it means those "
                    "points depend on the placeholder patch model more than the others."
                ),
            )
        )

    order = {"flag": 0, "watch": 1, "ok": 2}
    flags.sort(key=lambda f: order[f.severity])
    return [asdict(f) for f in flags]
