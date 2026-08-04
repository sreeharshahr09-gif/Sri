"""Source-agnostic data model for a rolled-out (2D unwrapped, 360 deg) tread pattern.

Everything downstream of this module -- rasterisation, the contact-patch sweep,
metrics, the HTML report -- consumes only the types defined here. The synthetic
generator (``tread_eval.synthetic``) and any future DXF parser must both emit a
``Pattern``; nothing else in the pipeline is allowed to care which one produced it.

Coordinate convention
---------------------
``x`` is circumferential, in mm, measured from an arbitrary origin, wrapping at
``Pattern.tyre_circumference``.  ``y`` is lateral, in mm, measured from the tread
centreline, so it runs from ``-tread_width/2`` to ``+tread_width/2``.  Positive
``y`` is the right-hand side of the tyre when looking in the direction of travel.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Iterable, Literal, Sequence

import numpy as np

Zone = Literal["shoulder", "intermediate", "center"]
ZONES: tuple[str, ...] = ("center", "intermediate", "shoulder")


@dataclass
class Pitch:
    """One circumferential pitch (repeat unit) of the pattern."""

    id: str
    circumferential_start: float  # mm
    circumferential_length: float  # mm

    @property
    def circumferential_end(self) -> float:
        return self.circumferential_start + self.circumferential_length


@dataclass
class Block:
    """A single tread element (land area) of the unwrapped pattern.

    ``polygon`` is an open ring of ``(x_circumferential, y_lateral)`` vertices in
    mm.  Blocks may cross the ``x = circumference`` seam; the rasteriser splits
    them, callers do not need to pre-split.
    """

    id: str
    pitch_id: str
    polygon: list[tuple[float, float]]
    zone: Zone
    height: float  # mm, block height above groove base
    draft_angle: float  # deg, per-side mould draft
    nsd: float  # sipe/void density proxy, 0 = no sipes

    # --- derived geometry -------------------------------------------------
    def area(self) -> float:
        """Signed-area magnitude of the polygon (shoelace), mm^2."""
        p = np.asarray(self.polygon, dtype=float)
        x, y = p[:, 0], p[:, 1]
        return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

    def centroid(self) -> tuple[float, float]:
        p = np.asarray(self.polygon, dtype=float)
        x, y = p[:, 0], p[:, 1]
        cross = x * np.roll(y, -1) - np.roll(x, -1) * y
        a = 0.5 * float(cross.sum())
        if abs(a) < 1e-12:
            return float(x.mean()), float(y.mean())
        cx = float(((x + np.roll(x, -1)) * cross).sum()) / (6 * a)
        cy = float(((y + np.roll(y, -1)) * cross).sum()) / (6 * a)
        return cx, cy

    def extents(self) -> tuple[float, float]:
        """(circumferential extent, lateral extent) of the bounding box, mm."""
        p = np.asarray(self.polygon, dtype=float)
        return float(np.ptp(p[:, 0])), float(np.ptp(p[:, 1]))

    def edges(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        pts = self.polygon
        return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _cumtrapz0(f: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Cumulative trapezoid starting at zero, same length as the input."""
    return np.concatenate([[0.0], np.cumsum(0.5 * (f[1:] + f[:-1]) * np.diff(x))])


@dataclass
class CrownProfile:
    """Tread arc in the tyre cross-section, parametrised by arc length.

    The lateral coordinate ``y`` used everywhere in this pipeline is *developed*
    (arc-length) width -- that is what a flattened mould drawing gives you, and
    it is what the unwrapped pattern's ``y`` means.  So the profile is stored as
    a curve parametrised by that same arc length:

        phi(y)  surface tangent angle (rad), odd in y, 0 at the crown centre
        z(y)    drop below the crown centre (mm)
        y_proj(y)  projected (chord-plane) lateral position (mm)
        r(y)    local radius of curvature (mm)

    with ``dphi/dy = 1/r``, ``dz/dy = sin(phi)``, ``dy_proj/dy = cos(phi)``.
    Keeping arc length as the parameter is what makes the three consumers agree:
    the lean model inverts ``phi``, the curvature correction is ``dy/dy_proj``,
    and the contact model reads ``r`` -- all from one curve, no differentiating
    of ``z`` against the wrong variable.

    ``PLACEHOLDER``: default profiles come from a dual-radius guess.  Swap in a
    measured cross-section (Section 10) via :meth:`from_cross_section`.
    """

    y: np.ndarray  # mm, arc length from the crown centre, ascending
    phi: np.ndarray  # rad, surface tangent angle
    z: np.ndarray  # mm, drop below the crown centre
    y_proj: np.ndarray  # mm, projected lateral position
    r: np.ndarray  # mm, local radius of curvature
    measured: bool = False

    # -- constructors ---------------------------------------------------
    @classmethod
    def from_radius_profile(cls, y: Sequence[float], r: Sequence[float], measured: bool = False) -> "CrownProfile":
        ya = np.asarray(y, dtype=float)
        ra = np.asarray(r, dtype=float)
        order = np.argsort(ya)
        ya, ra = ya[order], ra[order]
        phi = _cumtrapz0(1.0 / np.maximum(ra, 1e-9), ya)
        phi -= float(np.interp(0.0, ya, phi))
        z = _cumtrapz0(np.sin(phi), ya)
        z -= float(np.interp(0.0, ya, z))
        y_proj = _cumtrapz0(np.cos(phi), ya)
        y_proj -= float(np.interp(0.0, ya, y_proj))
        return cls(y=ya, phi=phi, z=z, y_proj=y_proj, r=ra, measured=measured)

    @classmethod
    def from_cross_section(cls, y_projected: Sequence[float], z: Sequence[float], n: int = 801) -> "CrownProfile":
        """Build from a measured cross-section given as projected (y, z) pairs.

        This is the production entry point: a contour-gauge or 3D-scan trace of
        the real tyre.  The measured coordinates are projected, so arc length is
        integrated out of them here rather than assumed.
        """
        yp = np.asarray(y_projected, dtype=float)
        zz = np.asarray(z, dtype=float)
        order = np.argsort(yp)
        yp, zz = yp[order], zz[order]
        zz = zz - float(np.interp(0.0, yp, zz))
        dz = np.gradient(zz, yp)
        phi = np.arctan(dz)
        s = _cumtrapz0(np.sqrt(1.0 + dz * dz), yp)
        s -= float(np.interp(0.0, yp, s))
        d2z = np.gradient(dz, yp)
        kappa = np.abs(d2z) / np.power(1.0 + dz * dz, 1.5)
        r = 1.0 / np.maximum(kappa, 1e-9)
        # Resample onto a uniform arc-length grid so downstream interpolation is well behaved.
        s_u = np.linspace(s.min(), s.max(), n)
        return cls(
            y=s_u,
            phi=np.interp(s_u, s, phi),
            z=np.interp(s_u, s, zz),
            y_proj=np.interp(s_u, s, yp),
            r=np.interp(s_u, s, r),
            measured=True,
        )

    @classmethod
    def dual_radius(
        cls,
        tread_width: float,
        r_center: float = 125.0,
        r_shoulder: float = 55.0,
        break_fraction: float = 0.50,
        n: int = 801,
    ) -> "CrownProfile":
        """PLACEHOLDER crown: a large-radius crown blending into a tight shoulder.

        ``break_fraction`` is where (as a fraction of the developed half width)
        the crown radius starts transitioning to the shoulder radius.  The
        transition is smoothstepped so curvature has no step -- the lean model
        inverts the tangent angle, and a kink there would put a jump in the
        contact-point-vs-lean relationship that no tyre has.
        """
        half = tread_width / 2.0
        y = np.linspace(-half, half, n)
        t = np.clip((np.abs(y) / half - break_fraction) / max(1e-6, 1.0 - break_fraction), 0.0, 1.0)
        blend = t * t * (3.0 - 2.0 * t)
        radius = r_center * (1.0 - blend) + r_shoulder * blend
        return cls.from_radius_profile(y, radius, measured=False)

    @classmethod
    def flat(cls, tread_width: float, n: int = 801) -> "CrownProfile":
        y = np.linspace(-tread_width / 2, tread_width / 2, n)
        big = np.full_like(y, 1e9)
        return cls.from_radius_profile(y, big, measured=False)

    # -- queries --------------------------------------------------------
    def tangent_angle(self, y: np.ndarray | float) -> np.ndarray:
        """Surface tangent angle vs. the road plane when upright, radians.

        The lean model inverts this: at lean ``gamma`` the contact point sits
        where the tread tangent has rotated to horizontal, i.e. ``phi == gamma``.
        """
        return np.interp(y, self.y, self.phi)

    def local_radius(self, y: np.ndarray | float) -> np.ndarray:
        return np.interp(y, self.y, self.r)

    def drop(self, y: np.ndarray | float) -> np.ndarray:
        return np.interp(y, self.y, self.z)

    def projected(self, y: np.ndarray | float) -> np.ndarray:
        return np.interp(y, self.y, self.y_proj)

    def slope(self, y: np.ndarray | float) -> np.ndarray:
        """dz/dy_proj at ``y`` -- the visual slope of the cross-section."""
        return np.tan(self.tangent_angle(y))

    def arc_length_factor(self, y: np.ndarray | float) -> np.ndarray:
        """Curvature correction factor of Section 8: developed mm per projected mm.

        A pattern digitised in projection (an ink impression, a photograph)
        compresses the shoulder: one projected millimetre there covers
        ``1 / cos(phi)`` mm of actual tread surface, so a flat unwrap
        under-weights shoulder area and shoulder stiffness.  Multiply lateral
        pixel areas by this to undo it.  OFF by default -- a mould DXF is
        already developed, and applying it there would double-count.
        """
        return 1.0 / np.maximum(np.cos(self.tangent_angle(y)), 1e-6)

    def contact_lateral_position(self, gamma_deg: float) -> float:
        """Lateral (developed) position of the contact point at lean ``gamma_deg``."""
        target = math.radians(gamma_deg)
        if float(np.max(np.abs(self.phi))) < 1e-9:
            return 0.0
        if target <= self.phi.min():
            return float(self.y[0])
        if target >= self.phi.max():
            return float(self.y[-1])
        return float(np.interp(target, self.phi, self.y))


@dataclass
class Pattern:
    """A complete rolled-out pattern, the single input to the whole pipeline."""

    tyre_circumference: float  # mm
    tread_width: float  # mm
    pitches: list[Pitch]
    blocks: list[Block]
    crown_radius_profile: CrownProfile | None = None
    name: str = "pattern"
    source: str = "synthetic"  # "synthetic" | "dxf" | "digitised"
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def crown(self) -> CrownProfile:
        if self.crown_radius_profile is None:
            self.crown_radius_profile = CrownProfile.flat(self.tread_width)
        return self.crown_radius_profile

    def zone_blocks(self, zone: str) -> list[Block]:
        return [b for b in self.blocks if b.zone == zone]

    def gross_area(self) -> float:
        return self.tyre_circumference * self.tread_width

    def land_area(self) -> float:
        return float(sum(b.area() for b in self.blocks))

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means the pattern is sane."""
        problems: list[str] = []
        if self.tyre_circumference <= 0 or self.tread_width <= 0:
            problems.append("circumference and tread width must be positive")
        half = self.tread_width / 2 + 1e-6
        for b in self.blocks:
            if len(b.polygon) < 3:
                problems.append(f"block {b.id}: polygon has < 3 vertices")
                continue
            ys = [p[1] for p in b.polygon]
            if min(ys) < -half or max(ys) > half:
                problems.append(f"block {b.id}: polygon exceeds tread width")
            if b.height <= 0:
                problems.append(f"block {b.id}: non-positive height")
        # Pitch sequence must close the circumference -- the ink-and-paper
        # digitisation failure mode of Section 8 shows up exactly here.
        if self.pitches:
            total = sum(p.circumferential_length for p in self.pitches)
            err = abs(total - self.tyre_circumference) / self.tyre_circumference
            if err > 1e-3:
                problems.append(
                    f"pitch lengths sum to {total:.2f} mm but circumference is "
                    f"{self.tyre_circumference:.2f} mm ({err * 100:.2f}% closure error)"
                )
        land_ratio = self.land_area() / self.gross_area()
        if not 0.15 < land_ratio < 0.98:
            problems.append(f"implausible overall land ratio {land_ratio:.3f}")
        return problems

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = {
            "tyre_circumference": self.tyre_circumference,
            "tread_width": self.tread_width,
            "name": self.name,
            "source": self.source,
            "meta": self.meta,
            "pitches": [asdict(p) for p in self.pitches],
            "blocks": [asdict(b) for b in self.blocks],
        }
        if self.crown_radius_profile is not None:
            c = self.crown_radius_profile
            d["crown_radius_profile"] = {
                "y": c.y.tolist(),
                "phi": c.phi.tolist(),
                "z": c.z.tolist(),
                "y_proj": c.y_proj.tolist(),
                "r": c.r.tolist(),
                "measured": c.measured,
            }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Pattern":
        crown = None
        if d.get("crown_radius_profile"):
            c = d["crown_radius_profile"]
            crown = CrownProfile(
                y=np.asarray(c["y"], float),
                phi=np.asarray(c["phi"], float),
                z=np.asarray(c["z"], float),
                y_proj=np.asarray(c["y_proj"], float),
                r=np.asarray(c["r"], float),
                measured=bool(c.get("measured", False)),
            )
        return cls(
            tyre_circumference=float(d["tyre_circumference"]),
            tread_width=float(d["tread_width"]),
            pitches=[Pitch(**p) for p in d["pitches"]],
            blocks=[
                Block(
                    id=b["id"],
                    pitch_id=b["pitch_id"],
                    polygon=[tuple(pt) for pt in b["polygon"]],
                    zone=b["zone"],
                    height=b["height"],
                    draft_angle=b["draft_angle"],
                    nsd=b["nsd"],
                )
                for b in d["blocks"]
            ],
            crown_radius_profile=crown,
            name=d.get("name", "pattern"),
            source=d.get("source", "unknown"),
            meta=d.get("meta", {}),
        )

    def save_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh)

    @classmethod
    def load_json(cls, path: str) -> "Pattern":
        with open(path) as fh:
            return cls.from_dict(json.load(fh))


def zone_bounds(tread_width: float, center_frac: float = 0.34, intermediate_frac: float = 0.72) -> dict[str, tuple[float, float]]:
    """Lateral |y| bands for the three zones, as fractions of the half width.

    Fraction-of-width rather than fixed mm bands, so the zoning generalises
    across tyre sizes (Section 11 open question -- resolved this way here, and
    kept in one place so it is a one-line change if that decision is revisited).
    """
    half = tread_width / 2.0
    return {
        "center": (0.0, center_frac * half),
        "intermediate": (center_frac * half, intermediate_frac * half),
        "shoulder": (intermediate_frac * half, half),
    }


def classify_zone(y_abs: float, tread_width: float, **kw) -> str:
    bounds = zone_bounds(tread_width, **kw)
    for zone in ("center", "intermediate", "shoulder"):
        lo, hi = bounds[zone]
        if y_abs <= hi:
            return zone
    return "shoulder"
