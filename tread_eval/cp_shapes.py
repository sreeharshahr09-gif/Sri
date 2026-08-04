"""Standard contact-patch shapes, defined by the numbers you would measure.

Instead of importing a traced footprint or trusting a contact model, you can say
"it's a 92 x 50 mm rounded rectangle with a 12 mm corner radius" and get exactly
that.  Useful when you have footprint *dimensions* from a datasheet or a test
report but not the outline itself, and for sensitivity studies where you want to
change one dimension at a time and see what it does.

Every shape is built as a polygon and handed to the same
:class:`~tread_eval.contact_patch.ContactPatch` as any other source, so the rest
of the pipeline treats a hand-specified rectangle exactly like a pressure-film
scan.

Shapes and their parameters
---------------------------
============= =====================================================
``rectangle``  ``length``, ``width``
``rounded``    ``length``, ``width``, ``corner_radius``
``stadium``    ``length``, ``width``  (semicircular ends)
``ellipse``    ``length``, ``width``
``superellipse`` ``length``, ``width``, ``exponent`` (2 = ellipse, 4 = squarish)
``trapezoid``  ``length``, ``width``, ``taper`` (-1..1, leading vs trailing width)
``diamond``    ``length``, ``width``
============= =====================================================

All of them also accept ``y_center`` (lateral position, mm), ``rotation`` (deg,
positive turns the leading edge toward +y) and ``load_N``.

``length`` is circumferential (along the direction of travel) and ``width`` is
lateral (across the tread) -- the same convention as everywhere else in the tool.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .contact_patch import ContactPatch, CPParams, lean_scale_factors
from .schema import CrownProfile

Array = np.ndarray

SHAPES = ("rectangle", "rounded", "stadium", "ellipse", "superellipse", "trapezoid", "diamond")


# ---------------------------------------------------------------------------
# outline builders -- all return vertices centred on (0, 0)
# ---------------------------------------------------------------------------
def rectangle_outline(length: float, width: float) -> Array:
    a, b = length / 2.0, width / 2.0
    return np.array([(-a, -b), (a, -b), (a, b), (-a, b)], dtype=float)


def rounded_outline(length: float, width: float, corner_radius: float, n_arc: int = 12) -> Array:
    """Rectangle with rounded corners.

    ``corner_radius`` is clamped to half the shorter side, at which point the
    shape becomes a stadium -- so an over-large radius degrades sensibly rather
    than producing self-intersecting geometry.
    """
    a, b = length / 2.0, width / 2.0
    r = max(0.0, min(corner_radius, min(a, b)))
    if r <= 1e-9:
        return rectangle_outline(length, width)
    pts: list[tuple[float, float]] = []
    # corner centres, counter-clockwise from the trailing-right corner
    centres = [(a - r, -(b - r)), (a - r, b - r), (-(a - r), b - r), (-(a - r), -(b - r))]
    starts = [-90.0, 0.0, 90.0, 180.0]
    for (cx, cy), start in zip(centres, starts):
        for ang in np.linspace(start, start + 90.0, n_arc + 1):
            t = math.radians(ang)
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return np.asarray(pts, dtype=float)


def stadium_outline(length: float, width: float, n_arc: int = 24) -> Array:
    """A rectangle capped with semicircles on the leading and trailing ends."""
    return rounded_outline(length, width, min(length, width) / 2.0, n_arc)


def ellipse_shape(length: float, width: float, n: int = 181) -> Array:
    t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([(length / 2.0) * np.cos(t), (width / 2.0) * np.sin(t)])


def superellipse_shape(length: float, width: float, exponent: float = 3.0, n: int = 181) -> Array:
    t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    ct, st = np.cos(t), np.sin(t)
    e = max(0.2, float(exponent))
    return np.column_stack([
        (length / 2.0) * np.sign(ct) * np.abs(ct) ** (2.0 / e),
        (width / 2.0) * np.sign(st) * np.abs(st) ** (2.0 / e),
    ])


def trapezoid_outline(length: float, width: float, taper: float = 0.3) -> Array:
    """Wider at one end than the other.

    ``taper`` runs -1 to 1: positive makes the **leading** edge (+x) wider, which
    is the sense a real footprint usually takes under braking.  ``width`` is the
    mean width, so area stays put as taper changes.
    """
    a, b = length / 2.0, width / 2.0
    t = float(np.clip(taper, -0.99, 0.99))
    lead, trail = b * (1.0 + t), b * (1.0 - t)
    return np.array([(-a, -trail), (a, -lead), (a, lead), (-a, trail)], dtype=float)


def diamond_outline(length: float, width: float) -> Array:
    a, b = length / 2.0, width / 2.0
    return np.array([(-a, 0.0), (0.0, -b), (a, 0.0), (0.0, b)], dtype=float)


# ---------------------------------------------------------------------------
@dataclass
class ShapeSpec:
    """A contact patch stated as a standard shape plus dimensions."""

    shape: str = "rounded"
    length: float = 90.0  # mm, circumferential
    width: float = 50.0  # mm, lateral
    corner_radius: float = 10.0  # mm, `rounded` only
    exponent: float = 3.0  # `superellipse` only
    taper: float = 0.3  # `trapezoid` only
    y_center: float | None = None  # None = follow the crown's contact point
    rotation: float = 0.0  # deg
    gamma_deg: float = 0.0
    load_N: float | None = None
    label: str = ""
    scale_with_lean: bool = True
    """Let the stated dimensions describe the **upright** patch and scale from there.

    A real 2W footprint narrows sharply as the bike leans, because the contact
    point walks onto the shoulder where the lateral crown radius collapses.  With
    this on, ``length`` and ``width`` are taken as the patch at ``gamma = 0`` and
    carried to other lean angles by
    :func:`~tread_eval.contact_patch.lean_scale_factors` -- the same Winkler
    trend a transferred measured footprint uses.

    Turn it off to hold the stated dimensions at every lean, which is what you
    want for a sensitivity study (change one number, see what it does) or when
    you have measured footprints at each lean and are stating them directly.
    Off, the patch keeps its full width until it runs off the tread edge and is
    clipped there -- so check the ``clipped`` flag before reading those numbers.
    """

    def validate(self) -> None:
        """Raise :class:`ValueError` with a specific message for unusable geometry.

        Called by :func:`shape_patch`.  Without it a negative width silently
        produces a valid-looking patch (the shoelace area takes an absolute
        value) and a NaN dimension propagates all the way to the charts.
        """
        if self.shape.lower() not in SHAPES:
            raise ValueError(
                f"unknown contact patch shape {self.shape!r}; expected one of {', '.join(SHAPES)}"
            )
        for name in ("length", "width"):
            v = float(getattr(self, name))
            if not math.isfinite(v):
                raise ValueError(f"contact patch {name} must be a finite number, got {v!r}")
            if v <= 0.0:
                raise ValueError(
                    f"contact patch {name} must be positive, got {v:g} mm "
                    f"({name} is the {'circumferential' if name == 'length' else 'lateral'} size)"
                )
        for name in ("corner_radius", "exponent", "taper", "rotation", "gamma_deg"):
            v = float(getattr(self, name))
            if not math.isfinite(v):
                raise ValueError(f"contact patch {name} must be a finite number, got {v!r}")
        if self.corner_radius < 0.0:
            raise ValueError(f"corner_radius must be >= 0, got {self.corner_radius:g} mm")
        if self.exponent <= 0.0:
            raise ValueError(f"superellipse exponent must be positive, got {self.exponent:g}")
        if not -1.0 < self.taper < 1.0:
            raise ValueError(f"trapezoid taper must lie strictly between -1 and 1, got {self.taper:g}")
        if self.load_N is not None:
            if not math.isfinite(self.load_N) or self.load_N <= 0.0:
                raise ValueError(f"contact patch load_N must be positive, got {self.load_N!r} N")
        if self.y_center is not None and not math.isfinite(self.y_center):
            raise ValueError(f"contact patch y_center must be a finite number, got {self.y_center!r}")

    def outline(self) -> Array:
        s = self.shape.lower()
        if s == "rectangle":
            out = rectangle_outline(self.length, self.width)
        elif s == "rounded":
            out = rounded_outline(self.length, self.width, self.corner_radius)
        elif s == "stadium":
            out = stadium_outline(self.length, self.width)
        elif s == "ellipse":
            out = ellipse_shape(self.length, self.width)
        elif s == "superellipse":
            out = superellipse_shape(self.length, self.width, self.exponent)
        elif s == "trapezoid":
            out = trapezoid_outline(self.length, self.width, self.taper)
        elif s == "diamond":
            out = diamond_outline(self.length, self.width)
        else:
            raise ValueError(f"unknown contact patch shape {self.shape!r}; expected one of {SHAPES}")

        if abs(self.rotation) > 1e-9:
            th = math.radians(self.rotation)
            c, s_ = math.cos(th), math.sin(th)
            out = out @ np.array([[c, s_], [-s_, c]])
        return out

    def describe(self) -> str:
        s = self.shape.lower()
        extra = ""
        if s == "rounded":
            extra = f", corner r={self.corner_radius:g} mm"
        elif s == "superellipse":
            extra = f", exponent {self.exponent:g}"
        elif s == "trapezoid":
            extra = f", taper {self.taper:+g}"
        if abs(self.rotation) > 1e-9:
            extra += f", rotated {self.rotation:g} deg"
        return f"{s} {self.length:g} x {self.width:g} mm{extra}"

    def to_dict(self) -> dict:
        return {
            "shape": self.shape, "length": self.length, "width": self.width,
            "corner_radius": self.corner_radius, "exponent": self.exponent,
            "taper": self.taper, "y_center": self.y_center, "rotation": self.rotation,
            "gamma_deg": self.gamma_deg, "load_N": self.load_N, "label": self.label,
            "scale_with_lean": self.scale_with_lean,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ShapeSpec":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(
                f"unknown contact-patch shape settings {sorted(unknown)}; "
                f"expected any of {sorted(known)}"
            )
        return cls(**d)


def shape_patch(
    spec: ShapeSpec,
    crown: CrownProfile,
    tread_width: float,
    params: CPParams | None = None,
) -> ContactPatch:
    """Build a :class:`ContactPatch` from a shape specification.

    Lateral position defaults to the crown's contact point for that lean angle,
    which is the physically sensible place for it -- state ``y_center`` only when
    you know better.
    """
    params = params or CPParams()
    spec.validate()
    gamma = spec.gamma_deg
    y_c = spec.y_center if spec.y_center is not None else crown.contact_lateral_position(gamma)

    out = spec.outline().copy()

    # Carry the stated (upright) dimensions to this lean angle.  Done on the
    # outline rather than on `length`/`width` so it works for every shape,
    # including the rotated and tapered ones.
    scale_note = ""
    if spec.scale_with_lean and abs(gamma) > 1e-9:
        s_len, s_wid = lean_scale_factors(crown, gamma, params)
        out[:, 0] *= s_len
        out[:, 1] *= s_wid
        scale_note = f", scaled to {gamma:g} deg lean (x{s_len:.3f} long, x{s_wid:.3f} wide)"

    out[:, 1] += y_c

    g = math.radians(gamma)
    load = spec.load_N
    if load is None:
        load = params.vertical_load / max(math.cos(g), 0.2) if params.load_rises_with_lean else params.vertical_load
    r_eff = max(50.0, params.wheel_radius - float(np.asarray(crown.drop(y_c)).ravel()[0]))

    patch = ContactPatch(
        gamma_deg=gamma,
        outline=out,
        tread_half_width=tread_width / 2.0,
        source="shape",
        provenance=f"user-specified shape: {spec.describe()}{scale_note}"
        + (f" -- {spec.label}" if spec.label else ""),
        y_center=y_c,
        r_eff=r_eff,
        r_lat=float(np.asarray(crown.local_radius(y_c)).ravel()[0]),
        normal_load=load,
        path_radius=math.inf if gamma <= 1e-9 else r_eff / math.tan(g),
        slip_angle_deg=params.lateral_slip_angle,
        delta=0.0,  # flat pressure -- a stated shape carries no pressure profile
        meta={"spec": spec.to_dict()},
    )
    patch.a = patch.length() / 2.0
    patch.b = patch.width() / 2.0
    area = patch.area()
    # A patch clipped away to nothing is not a thin patch -- it is a patch that
    # missed the tyre.  Left alone it sweeps to contact area zero at every angle
    # and reports a confident-looking page of zeros.
    if area <= 1e-9:
        raise ValueError(
            f"the contact patch does not overlap the tread: it is centred at "
            f"y = {y_c:.1f} mm on a tread running -{tread_width / 2:.1f} to "
            f"+{tread_width / 2:.1f} mm, so clipping leaves no area. Move the patch "
            f"laterally, make it wider, or check the lean angle."
        )
    # Uniform pressure over the actual (post-clip) area, so the load still balances.
    patch.peak_pressure = load / area if area > 0 else 0.0
    return patch


def specs_from_config(entries: Sequence[dict], default_gamma: float = 0.0) -> list[ShapeSpec]:
    """Parse a list of shape dicts from a config file."""
    specs = []
    for i, entry in enumerate(entries):
        d = dict(entry)
        d.setdefault("gamma_deg", default_gamma)
        try:
            specs.append(ShapeSpec.from_dict(d))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"contact_patch.shapes[{i}]: {exc}") from exc
    return specs
