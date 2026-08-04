"""Synthetic pattern generator -- SWAP POINT 1 of the brief.

PLACEHOLDER geometry.  This produces a plausible directional 2W block pattern so
the rest of the pipeline has something to chew on; it is not anyone's real tread.
When a DXF parser exists it must emit a :class:`~tread_eval.schema.Pattern` with
the same schema and everything downstream keeps working unchanged.

Defaults are taken from the reference mould drawing supplied with the brief:
a 182.78 mm repeat unit arrayed 11 times (circumference 2010.58 mm) across a
159 mm tread width.  Each repeat unit is subdivided into three pitches, which is
what makes the pitch sequence worth analysing at all.

The generated pattern deliberately has the two features that make the zoned and
lean diagnostics non-trivial:

* groove angle sweeps progressively from crown to shoulder, and the block edges
  are built by integrating that angle, so the intended angle is exactly what the
  groove-angle metric should recover;
* the two shoulders are staggered by half a pitch, which is a real design idiom
  and shows up in the left/right shoulder phase-alignment metric.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .schema import Block, CrownProfile, Pattern, Pitch, zone_bounds

# Reference drawing: 182.78 mm repeat x 11 around, 159 mm tread width.
REPEAT_LENGTH = 182.78
N_REPEATS = 11
DEFAULT_CIRCUMFERENCE = REPEAT_LENGTH * N_REPEATS  # 2010.58 mm
DEFAULT_TREAD_WIDTH = 159.0

PitchMode = str  # "random" | "tonal_group" | "uniform"


@dataclass
class SyntheticParams:
    """Knobs for the placeholder generator."""

    circumference: float = DEFAULT_CIRCUMFERENCE
    tread_width: float = DEFAULT_TREAD_WIDTH
    pitches_per_repeat: int = 3
    n_repeats: int = N_REPEATS
    pitch_mode: PitchMode = "random"
    pitch_ratios: tuple[float, ...] = (0.82, 0.91, 1.00, 1.10, 1.21)

    # groove geometry
    circumferential_groove: float = 9.0  # mm, gap between blocks along x
    lateral_groove: float = 6.5  # mm, gap between lateral bands
    center_groove: float = 5.0  # mm, groove straddling the centreline

    # groove angle sweep, crown -> shoulder (deg from the lateral axis)
    angle_crown: float = 12.0
    angle_shoulder: float = 46.0
    angle_exponent: float = 1.6

    shoulder_stagger: float = 0.3  # in pitches, applied to the +y side
    # Per-zone circumferential offset, in pitches. Aligning every lateral band
    # would make all of a pitch's blocks strike simultaneously -- a real design
    # mistake, but not one worth baking into the reference pattern.
    band_stagger: tuple[float, float, float] = (0.0, 0.24, 0.48)
    shoulder_splits: int = 2  # shoulder blocks per pitch (smaller blocks)
    intermediate_splits: int = 1

    # per-block property ranges (low, high)
    height_center: tuple[float, float] = (7.6, 8.2)
    height_intermediate: tuple[float, float] = (7.2, 7.9)
    height_shoulder: tuple[float, float] = (6.2, 7.0)
    draft_center: tuple[float, float] = (2.0, 3.5)
    draft_shoulder: tuple[float, float] = (4.0, 7.0)
    # Lateral sipes per block, by zone. The stiffness model needs sipe geometry,
    # not a density scalar, so these counts are expanded into explicit sipes.
    sipes_center: tuple[int, int] = (1, 3)
    sipes_shoulder: tuple[int, int] = (0, 1)
    sipe_depth_fraction: float = 0.6

    crown_r_center: float = 125.0
    crown_r_shoulder: float = 55.0
    crown_break_fraction: float = 0.50
    seed: int = 20260803


def _pitch_lengths(p: SyntheticParams, rng: np.random.Generator) -> list[float]:
    """Pitch sequence, normalised so it closes the circumference exactly."""
    n = p.pitches_per_repeat * p.n_repeats
    if p.pitch_mode == "uniform":
        ratios = np.ones(n)
    elif p.pitch_mode == "tonal_group":
        # One fixed group repeated verbatim -- the textbook tonal-noise mistake.
        group = np.asarray(p.pitch_ratios[: p.pitches_per_repeat], dtype=float)
        ratios = np.tile(group, p.n_repeats)[:n]
    else:
        # Semi-randomised: draw from the ratio set but forbid immediate repeats,
        # which is roughly what a real non-tonal sequencer enforces.
        pool = list(p.pitch_ratios)
        ratios_list: list[float] = []
        prev = None
        for _ in range(n):
            choices = [r for r in pool if r != prev] or pool
            pick = float(rng.choice(choices))
            ratios_list.append(pick)
            prev = pick
        ratios = np.asarray(ratios_list)
    return list(ratios / ratios.sum() * p.circumference)


def _skew_profile(p: SyntheticParams, half_width: float, n: int = 401):
    """Return (y, shift, angle_deg): the lateral shear applied to every block.

    ``angle_deg(y)`` is the intended groove angle at lateral position ``y``;
    ``shift(y)`` is its integral, so an edge built along ``x = x0 + shift(y)``
    actually has that local angle.
    """
    y = np.linspace(-half_width, half_width, n)
    frac = np.abs(y) / half_width
    angle = p.angle_crown + (p.angle_shoulder - p.angle_crown) * frac**p.angle_exponent
    # Negative: the pattern sweeps backwards toward both shoulders (a V).
    integrand = -np.tan(np.radians(angle)) * np.sign(y)
    shift = np.concatenate([[0.0], np.cumsum(0.5 * (integrand[1:] + integrand[:-1]) * np.diff(y))])
    shift -= np.interp(0.0, y, shift)
    return y, shift, angle


def _bands(p: SyntheticParams) -> list[tuple[float, float, str, int, float]]:
    """Bands as (y_low, y_high, zone, longitudinal splits, stagger), both sides."""
    bounds = zone_bounds(p.tread_width)
    c_lo, c_hi = bounds["center"]
    i_lo, i_hi = bounds["intermediate"]
    s_lo, s_hi = bounds["shoulder"]
    g = p.lateral_groove
    half_center_groove = p.center_groove / 2.0
    out: list[tuple[float, float, str, int, float]] = []
    for sign in (-1.0, 1.0):
        lo = sign * half_center_groove
        hi = sign * c_hi
        out.append((min(lo, hi), max(lo, hi), "center", 1, p.band_stagger[0]))
        lo = sign * (i_lo + g / 2)
        hi = sign * i_hi
        out.append((min(lo, hi), max(lo, hi), "intermediate", p.intermediate_splits, p.band_stagger[1]))
        lo = sign * (s_lo + g / 2)
        hi = sign * s_hi
        out.append((min(lo, hi), max(lo, hi), "shoulder", p.shoulder_splits, p.band_stagger[2]))
    return out


def _uniform(rng: np.random.Generator, rng_range: tuple[float, float]) -> float:
    return float(rng.uniform(*rng_range))


def generate_pattern(params: SyntheticParams | None = None, name: str | None = None) -> Pattern:
    """Build the placeholder pattern.  <<< SWAP POINT: DXF parser goes here >>>"""
    p = params or SyntheticParams()
    rng = np.random.default_rng(p.seed)
    half = p.tread_width / 2.0
    ys, shift, _ = _skew_profile(p, half)

    lengths = _pitch_lengths(p, rng)
    starts = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])
    pitches = [
        Pitch(id=f"P{i:03d}", circumferential_start=float(s), circumferential_length=float(l))
        for i, (s, l) in enumerate(zip(starts, lengths))
    ]

    blocks: list[Block] = []
    bands = _bands(p)
    n_sub = 6  # lateral subdivisions per block edge -> curved edges like a real mould

    for pi, pitch in enumerate(pitches):
        for bi, (y_lo, y_hi, zone, splits, band_off) in enumerate(bands):
            stagger = band_off * pitch.circumferential_length
            if (y_lo + y_hi) > 0:
                stagger += p.shoulder_stagger * pitch.circumferential_length
            seg_len = pitch.circumferential_length / splits
            for si in range(splits):
                x0 = pitch.circumferential_start + si * seg_len + stagger
                gap = p.circumferential_groove
                xa = x0 + gap / 2.0
                xb = x0 + seg_len - gap / 2.0
                if xb - xa < 4.0:
                    continue

                # Slight lateral taper so blocks are not perfect rectangles.
                taper = float(rng.uniform(0.0, 0.10)) * (xb - xa)
                yy = np.linspace(y_lo, y_hi, n_sub)
                sh = np.interp(yy, ys, shift)
                # Taper always grows outboard, so the two sides of the tyre are
                # true mirror images and the only designed left/right difference
                # is the shoulder stagger -- otherwise the symmetry metrics would
                # be reporting an artefact of the generator.
                ay = np.abs(yy)
                t = (ay - ay.min()) / max(float(np.ptp(ay)), 1e-9)
                lead = xa + sh + taper * t
                trail = xb + sh - taper * (1.0 - t)

                poly = [(float(x), float(y)) for x, y in zip(lead, yy)]
                poly += [(float(x), float(y)) for x, y in zip(trail[::-1], yy[::-1])]

                if zone == "shoulder":
                    height = _uniform(rng, p.height_shoulder)
                    draft = _uniform(rng, p.draft_shoulder)
                    n_sipes = int(rng.integers(p.sipes_shoulder[0], p.sipes_shoulder[1] + 1))
                elif zone == "intermediate":
                    height = _uniform(rng, p.height_intermediate)
                    draft = _uniform(rng, (p.draft_center[0], p.draft_shoulder[1]))
                    n_sipes = int(rng.integers(p.sipes_shoulder[0], p.sipes_center[1] + 1))
                else:
                    height = _uniform(rng, p.height_center)
                    draft = _uniform(rng, p.draft_center)
                    n_sipes = int(rng.integers(p.sipes_center[0], p.sipes_center[1] + 1))

                blocks.append(
                    Block(
                        id=f"B{pi:03d}_{bi}_{si}",
                        pitch_id=pitch.id,
                        polygon=poly,
                        zone=zone,  # type: ignore[arg-type]
                        height=height,
                        draft_angle=draft,
                        n_lateral_sipes=n_sipes,
                        sipe_depth_fraction=p.sipe_depth_fraction,
                    )
                )

    crown = CrownProfile.dual_radius(
        p.tread_width,
        r_center=p.crown_r_center,
        r_shoulder=p.crown_r_shoulder,
        break_fraction=p.crown_break_fraction,
    )

    return Pattern(
        tyre_circumference=p.circumference,
        tread_width=p.tread_width,
        pitches=pitches,
        blocks=blocks,
        crown_radius_profile=crown,
        name=name or f"synthetic-{p.pitch_mode}",
        source="synthetic",
        meta={
            "generator": "tread_eval.synthetic",
            "pitch_mode": p.pitch_mode,
            "repeat_length_mm": p.circumference / p.n_repeats,
            "n_repeats": p.n_repeats,
            "pitches_per_repeat": p.pitches_per_repeat,
            "angle_crown_deg": p.angle_crown,
            "angle_shoulder_deg": p.angle_shoulder,
            "seed": p.seed,
            "placeholder": True,
        },
    )


def intended_groove_angle(pattern: Pattern, y: np.ndarray) -> np.ndarray:
    """The angle the generator *meant* to build at each lateral position, deg.

    Only meaningful for synthetic patterns; used in the tests as ground truth
    for the measured groove-angle metric.
    """
    meta = pattern.meta
    half = pattern.tread_width / 2.0
    a0 = meta.get("angle_crown_deg", 12.0)
    a1 = meta.get("angle_shoulder_deg", 46.0)
    frac = np.abs(y) / half
    return a0 + (a1 - a0) * frac**1.6


def _self_check() -> None:  # pragma: no cover - convenience for manual runs
    pat = generate_pattern()
    print(f"{len(pat.blocks)} blocks, {len(pat.pitches)} pitches")
    print(f"land ratio {pat.land_area() / pat.gross_area():.3f}")
    print("problems:", pat.validate())


if __name__ == "__main__":  # pragma: no cover
    _self_check()
