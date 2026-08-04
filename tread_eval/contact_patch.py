"""Contact patch definition -- import a measured one, or generate one.

The contact patch is the window through which every other metric is computed, so
this module treats it as a first-class input rather than a hidden assumption.

A patch is **any closed shape**
------------------------------
:class:`ContactPatch` stores an outline polygon plus an optional pressure field.
Nothing downstream assumes an ellipse: the sweep only ever asks for a rasterised
mask, so a scanned footprint with ragged edges, a peanut-shaped one, or a plain
rectangle all work identically.

Where a patch can come from, in order of how much you should trust it
--------------------------------------------------------------------
1. :func:`measured_patch` -- a real footprint at this lean angle: an outline you
   imported, optionally with a measured pressure map.  Nothing is assumed.
2. :func:`interpolated_patch` -- two measured footprints bracketing this lean
   angle, blended.
3. :func:`transferred_patch` -- a measured footprint at *some other* lean angle
   (in practice 0 deg, because a static upright footprint is easy to capture and
   a leaned one is not), rescaled by the lean trend the parametric model
   predicts.  The measured shape is kept; only the change with lean is modelled.
4. :func:`rhyne_patch` -- generated from load and inflation pressure, using the
   same empirical relations as the reference stiffness tool.
5. :func:`winkler_patch` -- generated from the crown profile and load by elastic
   foundation contact.  The only source that is lean-native from first
   principles, and therefore the last-resort default.

:class:`CPLibrary` applies exactly that order and records, per lean angle, which
rung it landed on, so a report can never quietly present a modelled patch as a
measured one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .schema import CrownProfile

Array = np.ndarray


# ---------------------------------------------------------------------------
# pressure fields
# ---------------------------------------------------------------------------
@dataclass
class PressureGrid:
    """A measured or computed pressure map, sampled on a regular grid.

    ``x`` is circumferential offset from the patch centre (mm), ``y`` lateral
    position (mm), ``p`` pressure in MPa with shape ``(len(y), len(x))``.
    """

    x: Array
    y: Array
    p: Array

    def sample(self, x_rel: Array, y: Array) -> Array:
        """Bilinear sample onto an arbitrary grid; zero outside the map."""
        xi = np.interp(x_rel, self.x, np.arange(self.x.size), left=-1, right=-1)
        yi = np.interp(y, self.y, np.arange(self.y.size), left=-1, right=-1)
        out = np.zeros((y.size, x_rel.size), dtype=float)
        valid_x = xi >= 0
        valid_y = yi >= 0
        if not valid_x.any() or not valid_y.any():
            return out
        x0 = np.clip(np.floor(xi).astype(int), 0, self.x.size - 1)
        x1 = np.clip(x0 + 1, 0, self.x.size - 1)
        y0 = np.clip(np.floor(yi).astype(int), 0, self.y.size - 1)
        y1 = np.clip(y0 + 1, 0, self.y.size - 1)
        tx = np.clip(xi - x0, 0.0, 1.0)
        ty = np.clip(yi - y0, 0.0, 1.0)
        block = (
            self.p[np.ix_(y0, x0)] * (1 - ty)[:, None] * (1 - tx)[None, :]
            + self.p[np.ix_(y0, x1)] * (1 - ty)[:, None] * tx[None, :]
            + self.p[np.ix_(y1, x0)] * ty[:, None] * (1 - tx)[None, :]
            + self.p[np.ix_(y1, x1)] * ty[:, None] * tx[None, :]
        )
        out[np.ix_(valid_y, valid_x)] = block[np.ix_(valid_y, valid_x)]
        return out

    def total_load(self) -> float:
        dx = float(np.mean(np.diff(self.x))) if self.x.size > 1 else 0.0
        dy = float(np.mean(np.diff(self.y))) if self.y.size > 1 else 0.0
        return float(self.p.sum()) * dx * dy

    def outline(self, threshold_fraction: float = 0.02) -> Array:
        """Boundary of the loaded region, as the convex hull of pressed cells.

        A hull rather than a marching-squares contour: a measured map is noisy
        at its edge, and the hull is stable under that noise.  Import an explicit
        outline alongside the map when the true shape is concave.
        """
        thr = threshold_fraction * float(self.p.max()) if self.p.size else 0.0
        ys, xs = np.nonzero(self.p > thr)
        if xs.size < 3:
            return np.zeros((0, 2))
        pts = np.column_stack([self.x[xs], self.y[ys]])
        return _convex_hull(pts)


def _convex_hull(pts: Array) -> Array:
    """Andrew's monotone chain."""
    p = np.unique(pts, axis=0)
    if len(p) < 3:
        return p
    p = p[np.lexsort((p[:, 1], p[:, 0]))]

    def half(points):
        stack: list = []
        for pt in points:
            while len(stack) >= 2:
                (ax, ay), (bx, by) = stack[-2], stack[-1]
                if (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax) <= 0:
                    stack.pop()
                else:
                    break
            stack.append(tuple(pt))
        return stack

    lower = half(p)
    upper = half(p[::-1])
    return np.asarray(lower[:-1] + upper[:-1], dtype=float)


# ---------------------------------------------------------------------------
# the patch
# ---------------------------------------------------------------------------
@dataclass
class ContactPatch:
    """A contact patch of arbitrary shape, in unwrapped tread coordinates (mm).

    ``outline`` vertices are ``(x_relative_to_patch_centre, y_lateral)``.  The
    patch is placed on the tyre by the sweep, which slides it in x; the lateral
    position is baked into the outline.
    """

    gamma_deg: float
    outline: Array
    tread_half_width: float
    source: str = "winkler"
    provenance: str = ""
    pressure_grid: PressureGrid | None = None

    # Analytic descriptors.  Populated by the parametric generators and used by
    # them for the pressure field; for a measured patch they are measurements of
    # the imported shape, kept so every patch reports the same vocabulary.
    a: float = 0.0  # semi-length, mm
    b: float = 0.0  # semi-width, mm
    y_center: float = 0.0
    delta: float = 0.0
    peak_pressure: float = 0.0
    r_eff: float = 320.0
    r_lat: float = 120.0
    normal_load: float = float("nan")
    path_radius: float = float("inf")
    slip_angle_deg: float = 0.0
    analytic_pressure: bool = True  # False when only the grid defines pressure
    clipped: bool = False
    meta: dict = field(default_factory=dict)

    # -- construction helpers -------------------------------------------
    def __post_init__(self) -> None:
        self.outline = np.asarray(self.outline, dtype=float).reshape(-1, 2)
        if self.outline.size and self.tread_half_width > 0:
            before = self.outline[:, 1].copy()
            self.outline[:, 1] = np.clip(
                self.outline[:, 1], -self.tread_half_width, self.tread_half_width
            )
            if not np.allclose(before, self.outline[:, 1]):
                self.clipped = True

    # -- geometry -------------------------------------------------------
    def area(self) -> float:
        p = self.outline
        if len(p) < 3:
            return 0.0
        x, y = p[:, 0], p[:, 1]
        return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

    def perimeter(self) -> float:
        p = self.outline
        if len(p) < 2:
            return 0.0
        d = np.roll(p, -1, axis=0) - p
        return float(np.hypot(d[:, 0], d[:, 1]).sum())

    def bbox(self) -> tuple[float, float, float, float]:
        p = self.outline
        return float(p[:, 0].min()), float(p[:, 0].max()), float(p[:, 1].min()), float(p[:, 1].max())

    def length(self) -> float:
        x0, x1, _, _ = self.bbox()
        return x1 - x0

    def width(self) -> float:
        _, _, y0, y1 = self.bbox()
        return y1 - y0

    def centroid(self) -> tuple[float, float]:
        p = self.outline
        x, y = p[:, 0], p[:, 1]
        cross = x * np.roll(y, -1) - np.roll(x, -1) * y
        a = 0.5 * float(cross.sum())
        if abs(a) < 1e-12:
            return float(x.mean()), float(y.mean())
        cx = float(((x + np.roll(x, -1)) * cross).sum()) / (6 * a)
        cy = float(((y + np.roll(y, -1)) * cross).sum()) / (6 * a)
        return cx, cy

    def contains(self, x: Array, y: Array) -> Array:
        """Point-in-polygon test, broadcast over ``x`` and ``y``."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        x, y = np.broadcast_arrays(x, y)
        inside = np.zeros(x.shape, dtype=bool)
        p = self.outline
        n = len(p)
        for i in range(n):
            x0, y0 = p[i]
            x1, y1 = p[(i + 1) % n]
            if y0 == y1:
                continue
            straddles = (y0 > y) != (y1 > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                x_cross = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            inside ^= straddles & (x < x_cross)
        return inside & (np.abs(y) <= self.tread_half_width + 1e-9)

    # -- pressure -------------------------------------------------------
    def pressure(self, x: Array, y: Array) -> Array:
        """Pressure field in MPa; zero outside the patch."""
        if self.pressure_grid is not None:
            x = np.asarray(x, dtype=float)
            y = np.asarray(y, dtype=float)
            if x.ndim == 1 and y.ndim == 1:
                p = self.pressure_grid.sample(x, y)
                return np.where(self.contains(x[None, :], y[:, None]), p, 0.0)
            p = np.zeros(np.broadcast(x, y).shape)
            return p
        return self._analytic_pressure(x, y)

    def _analytic_pressure(self, x: Array, y: Array) -> Array:
        if self.delta <= 0 or self.r_eff <= 0 or self.r_lat <= 0:
            return np.where(self.contains(x, y), self.peak_pressure, 0.0)
        u = self.delta - np.asarray(x) ** 2 / (2 * self.r_eff) - (
            np.asarray(y) - self.y_center
        ) ** 2 / (2 * self.r_lat)
        scale = self.peak_pressure / max(self.delta, 1e-9)
        return np.where(self.contains(x, y), np.maximum(u, 0.0) * scale, 0.0)

    # -- rasterisation --------------------------------------------------
    def masks(self, grid) -> tuple[Array, Array]:
        """Rasterise onto a :class:`~tread_eval.raster.Grid`.

        Returns ``(binary, pressure)`` of shape ``(grid.ny, grid.nx)``, with the
        patch centred on column 0 and wrapping around the seam -- which is what
        makes the sweep's correlation index map straight onto rotation angle.

        Scanline fill of the outline polygon, so any closed shape rasterises:
        the sweep never learns whether the patch was an ellipse or a scan.
        """
        ny, nx, dx = grid.ny, grid.nx, grid.dx
        binary = np.zeros((ny, nx), dtype=bool)
        yv = grid.y
        p = self.outline
        if len(p) < 3:
            return binary.astype(np.float32), np.zeros((ny, nx), dtype=np.float32)

        y0e = p[:, 1]
        y1e = np.roll(p[:, 1], -1)
        x0e = p[:, 0]
        x1e = np.roll(p[:, 0], -1)
        lo = np.minimum(y0e, y1e)
        hi = np.maximum(y0e, y1e)

        rows = np.flatnonzero((yv >= lo.min()) & (yv <= hi.max()))
        for r in rows:
            yy = yv[r]
            hit = (yy >= lo) & (yy < hi) & (y0e != y1e)
            if not hit.any():
                continue
            t = (yy - y0e[hit]) / (y1e[hit] - y0e[hit])
            xs = np.sort(x0e[hit] + t * (x1e[hit] - x0e[hit]))
            for i in range(0, len(xs) - 1, 2):
                c0 = int(math.ceil(xs[i] / dx - 0.5))
                c1 = int(math.ceil(xs[i + 1] / dx - 0.5))
                if c1 <= c0:
                    continue
                cols = np.arange(c0, c1) % nx
                binary[r, cols] = True

        x_rel = grid.x_rel()
        if self.pressure_grid is not None:
            press = self.pressure_grid.sample(x_rel, yv)
        else:
            press = self._analytic_pressure(x_rel[None, :], yv[:, None])
        press = np.where(binary, np.maximum(press, 0.0), 0.0)
        return binary.astype(np.float32), press.astype(np.float32)

    # -- kinematics -----------------------------------------------------
    def travel_direction_deg(self, x: Array | float) -> Array:
        """Instantaneous travel direction at longitudinal offset ``x``, degrees.

        A leaning tyre follows a curved path, so the patch spins about the
        vertical at ``V / R_path``; a point ``x`` ahead of the centre travels at
        ``atan(x / R_path)`` to the centreline.
        """
        if not np.isfinite(self.path_radius) or self.path_radius <= 0:
            base = np.zeros_like(np.asarray(x, dtype=float))
        else:
            base = np.degrees(np.arctan2(np.asarray(x, dtype=float), self.path_radius))
        return base + self.slip_angle_deg

    def nominal_area(self) -> float:
        return self.area()

    def describe(self) -> str:
        return (
            f"{self.gamma_deg:g} deg: {self.source} -- {self.length():.1f} x {self.width():.1f} mm, "
            f"{self.area():.0f} mm^2, centre y={self.centroid()[1]:.1f} mm"
        )


# ---------------------------------------------------------------------------
# shape primitives
# ---------------------------------------------------------------------------
def ellipse_outline(a: float, b: float, y_center: float, n: int = 181) -> Array:
    t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([a * np.cos(t), y_center + b * np.sin(t)])


def superellipse_outline(a: float, b: float, y_center: float, exponent: float = 3.0, n: int = 181) -> Array:
    """Rounded rectangle.  A real footprint is squarer than an ellipse: exponent
    2 is an ellipse, higher is squarer, and 3 to 4 matches a measured tyre
    footprint reasonably."""
    t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    ct, st = np.cos(t), np.sin(t)
    x = a * np.sign(ct) * np.abs(ct) ** (2.0 / exponent)
    y = b * np.sign(st) * np.abs(st) ** (2.0 / exponent)
    return np.column_stack([x, y_center + y])


# ---------------------------------------------------------------------------
# generator 1: Winkler elastic foundation on the crown
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CPParams:
    """Settings for the parametric generators."""

    vertical_load: float = 1500.0  # Fz0, N, upright static load on this tyre
    foundation_modulus: float = 0.47  # k_f, N/mm^3 (~E_tread / effective depth)
    wheel_radius: float = 320.0  # R, mm, free radius at the crown centre
    load_rises_with_lean: bool = True  # Fz = Fz0 / cos(gamma), steady cornering
    lateral_slip_angle: float = 0.0  # deg, added to the kinematic travel direction
    # Rhyne inputs -- only used by rhyne_patch
    inflation_kpa: float = 250.0
    section_width_mm: float = 130.0
    aspect_ratio: float = 80.0
    rim_diameter_in: float = 17.0
    tyre_type: str = "2w"  # "2w" | "pcr" | "tbr" | "lt"
    shape_exponent: float = 3.0  # superellipse squareness for generated outlines


DEFAULT_CP_PARAMS = CPParams()


def winkler_patch(
    gamma_deg: float,
    crown: CrownProfile,
    params: CPParams = DEFAULT_CP_PARAMS,
    tread_width: float | None = None,
    n_outline: int = 181,
) -> ContactPatch:
    """Elastic-foundation contact on a doubly curved crown.

    ``u(x,y) = delta - x^2/(2 R_eff) - (y-y_c)^2/(2 R_lat)`` makes the patch an
    ellipse with ``a = sqrt(2 R_eff delta)``, ``b = sqrt(2 R_lat delta)``, and
    requiring the pressure to carry the load closes it:
    ``delta = sqrt(Fz / (k_f pi sqrt(R_eff R_lat)))``.

    Lean enters geometrically: the contact point walks to where the tread
    tangent is horizontal, the much smaller lateral radius there narrows the
    patch, and the normal load rises as ``Fz/cos(gamma)``.  This is the only
    generator that produces the lean trend from first principles, which is why
    it is also what :func:`transferred_patch` borrows that trend from.
    """
    half_width = (tread_width if tread_width is not None else (crown.y[-1] - crown.y[0])) / 2.0
    y_c = crown.contact_lateral_position(gamma_deg)
    r_lat = float(np.asarray(crown.local_radius(y_c)).ravel()[0])
    r_eff = max(50.0, params.wheel_radius - float(np.asarray(crown.drop(y_c)).ravel()[0]))

    g = math.radians(gamma_deg)
    fz = params.vertical_load / max(math.cos(g), 0.2) if params.load_rises_with_lean else params.vertical_load
    delta = math.sqrt(fz / (params.foundation_modulus * math.pi * math.sqrt(r_eff * r_lat)))
    a = math.sqrt(2.0 * r_eff * delta)
    b = math.sqrt(2.0 * r_lat * delta)

    return ContactPatch(
        gamma_deg=gamma_deg,
        outline=ellipse_outline(a, b, y_c, n_outline),
        tread_half_width=half_width,
        source="winkler",
        provenance="generated: Winkler elastic foundation on the crown profile",
        a=a,
        b=b,
        y_center=y_c,
        delta=delta,
        peak_pressure=params.foundation_modulus * delta,
        r_eff=r_eff,
        r_lat=r_lat,
        normal_load=fz,
        path_radius=math.inf if gamma_deg <= 1e-9 else r_eff / math.tan(g),
        slip_angle_deg=params.lateral_slip_angle,
        meta={"crown_measured": crown.measured},
    )


# ---------------------------------------------------------------------------
# generator 2: Rhyne empirical footprint from load and inflation
# ---------------------------------------------------------------------------
_RHYNE_K_LOAD = {"pcr": 1.10, "tbr": 1.05, "lt": 1.08, "2w": 1.10}
_RHYNE_RATIO = {"pcr": 1.15, "tbr": 1.45, "lt": 1.25, "2w": 1.60}
_RHYNE_K_WC = {"pcr": 0.82, "tbr": 0.86, "lt": 0.84, "2w": 0.68}


def rhyne_patch(
    gamma_deg: float,
    crown: CrownProfile,
    params: CPParams = DEFAULT_CP_PARAMS,
    tread_width: float | None = None,
    n_outline: int = 181,
) -> ContactPatch:
    """Footprint from load and inflation pressure (Rhyne 2005; Pacejka 2012).

    Same relations as ``computeContactPatch()`` in the reference stiffness tool:
    contact area is the load divided by an inflation-derived contact pressure,
    and the length/width split comes from a tyre-type aspect ratio.  A ``2w``
    row is added to the tool's PCR/TBR/LT table because a motorcycle footprint
    is markedly longer and narrower than a car one; those two constants are the
    only guessed numbers here, and they are the ones to calibrate first if you
    have a static footprint to compare against.

    Lean is handled by moving the patch to the crown's contact point and
    reusing the Winkler length/width trend, since the Rhyne relations are
    upright-only.
    """
    half_width = (tread_width if tread_width is not None else (crown.y[-1] - crown.y[0])) / 2.0
    ty = params.tyre_type if params.tyre_type in _RHYNE_K_LOAD else "2w"

    g = math.radians(gamma_deg)
    fz = params.vertical_load / max(math.cos(g), 0.2) if params.load_rises_with_lean else params.vertical_load
    p_contact = (params.inflation_kpa * 1e-3) * _RHYNE_K_LOAD[ty]  # MPa
    if p_contact <= 0 or fz <= 0:
        raise ValueError("rhyne_patch needs positive load and inflation pressure")
    area = fz / p_contact

    ratio = _RHYNE_RATIO[ty]  # length / width
    wc_from_section = _RHYNE_K_WC[ty] * params.section_width_mm
    wc_from_area = math.sqrt(area / ratio)
    wc = min(wc_from_section, wc_from_area)
    lc = area / max(wc, 1e-6)

    # Lean: take the position from the crown, and the shape trend from Winkler.
    y_c = crown.contact_lateral_position(gamma_deg)
    if gamma_deg > 1e-9:
        up = winkler_patch(0.0, crown, params, tread_width)
        at = winkler_patch(gamma_deg, crown, params, tread_width)
        lc *= at.a / max(up.a, 1e-9)
        wc *= at.b / max(up.b, 1e-9)

    a, b = lc / 2.0, wc / 2.0
    r_eff = max(50.0, params.wheel_radius - float(np.asarray(crown.drop(y_c)).ravel()[0]))
    outline = superellipse_outline(a, b, y_c, params.shape_exponent, n_outline)
    patch = ContactPatch(
        gamma_deg=gamma_deg,
        outline=outline,
        tread_half_width=half_width,
        source="rhyne",
        provenance=(
            f"generated: Rhyne empirical footprint from {params.vertical_load:.0f} N at "
            f"{params.inflation_kpa:.0f} kPa ({ty})"
        ),
        a=a,
        b=b,
        y_center=y_c,
        r_eff=r_eff,
        r_lat=float(np.asarray(crown.local_radius(y_c)).ravel()[0]),
        normal_load=fz,
        path_radius=math.inf if gamma_deg <= 1e-9 else r_eff / math.tan(g),
        slip_angle_deg=params.lateral_slip_angle,
        meta={"contact_pressure_mpa": p_contact, "nominal_area_mm2": area},
    )
    # Uniform pressure over the actual (clipped) area, so the load still balances.
    actual = patch.area()
    patch.peak_pressure = fz / actual if actual > 0 else 0.0
    patch.delta = 0.0  # flat pressure -- no parabolic field for this generator
    return patch


# ---------------------------------------------------------------------------
# source 3: measured
# ---------------------------------------------------------------------------
def measured_patch(
    gamma_deg: float,
    outline: Array,
    tread_width: float,
    pressure_grid: PressureGrid | None = None,
    normal_load: float | None = None,
    path_radius: float | None = None,
    slip_angle_deg: float = 0.0,
    label: str = "measured footprint",
) -> ContactPatch:
    """Wrap an imported footprint outline (any shape) as a contact patch.

    The outline is re-centred on its own circumferential centroid, so the
    imported coordinate origin does not matter; the lateral position is kept
    exactly as imported, because that *is* the measurement.
    """
    out = np.asarray(outline, dtype=float).reshape(-1, 2)
    if len(out) < 3:
        raise ValueError("a contact patch outline needs at least 3 vertices")
    tmp = ContactPatch(gamma_deg=gamma_deg, outline=out, tread_half_width=tread_width / 2.0)
    cx, cy = tmp.centroid()
    out = out.copy()
    out[:, 0] -= cx

    if pressure_grid is not None:
        pressure_grid = PressureGrid(x=pressure_grid.x - cx, y=pressure_grid.y, p=pressure_grid.p)

    patch = ContactPatch(
        gamma_deg=gamma_deg,
        outline=out,
        tread_half_width=tread_width / 2.0,
        source="measured",
        provenance=label,
        pressure_grid=pressure_grid,
        y_center=cy,
        analytic_pressure=pressure_grid is None,
        slip_angle_deg=slip_angle_deg,
        meta={"imported_centroid_x": cx},
    )
    patch.a = patch.length() / 2.0
    patch.b = patch.width() / 2.0
    if normal_load is not None:
        patch.normal_load = normal_load
    elif pressure_grid is not None:
        patch.normal_load = pressure_grid.total_load()
    if path_radius is not None:
        patch.path_radius = path_radius
    area = patch.area()
    if pressure_grid is not None:
        patch.peak_pressure = float(pressure_grid.p.max())
    elif np.isfinite(patch.normal_load) and area > 0:
        patch.peak_pressure = patch.normal_load / area
    return patch


# ---------------------------------------------------------------------------
# source 4: transferred / interpolated
# ---------------------------------------------------------------------------
def transferred_patch(
    base: ContactPatch,
    gamma_deg: float,
    crown: CrownProfile,
    params: CPParams = DEFAULT_CP_PARAMS,
    tread_width: float | None = None,
) -> ContactPatch:
    """Carry a measured footprint to a lean angle that was not measured.

    The realistic situation: a static upright footprint is easy to capture, a
    leaned one needs a rig almost nobody has.  So keep the measured *shape* --
    which is the part a model gets wrong -- and take only the *change* with lean
    from the parametric model:

        length  scales by  a(gamma) / a(gamma_base)
        width   scales by  b(gamma) / b(gamma_base)
        centre  moves to   the crown's contact point at gamma

    The result is honest about what it is: ``source`` is ``"transferred"`` and
    the provenance names the lean angle it came from.  If the measured shape is
    strongly non-elliptical this is a much better estimate than regenerating the
    patch from scratch, because only the scaling is modelled.
    """
    half_width = (tread_width if tread_width is not None else 2 * base.tread_half_width) / 2.0
    ref = winkler_patch(base.gamma_deg, crown, params, half_width * 2)
    tgt = winkler_patch(gamma_deg, crown, params, half_width * 2)

    sx = tgt.a / max(ref.a, 1e-9)
    sy = tgt.b / max(ref.b, 1e-9)
    _, base_cy = base.centroid()

    out = base.outline.copy()
    out[:, 0] *= sx
    out[:, 1] = (out[:, 1] - base_cy) * sy + tgt.y_center

    grid = None
    if base.pressure_grid is not None:
        pg = base.pressure_grid
        # Pressure scales with load over area so the transferred map still
        # integrates to the target normal load.
        area_ratio = (sx * sy) if sx * sy > 0 else 1.0
        scale = (tgt.normal_load / max(ref.normal_load, 1e-9)) / area_ratio
        grid = PressureGrid(x=pg.x * sx, y=(pg.y - base_cy) * sy + tgt.y_center, p=pg.p * scale)

    patch = ContactPatch(
        gamma_deg=gamma_deg,
        outline=out,
        tread_half_width=half_width,
        source="transferred",
        provenance=(
            f"{base.source} patch at {base.gamma_deg:g} deg, rescaled to {gamma_deg:g} deg by the "
            f"Winkler lean trend (length x{sx:.3f}, width x{sy:.3f})"
        ),
        pressure_grid=grid,
        y_center=tgt.y_center,
        delta=0.0 if grid is not None else tgt.delta,
        r_eff=tgt.r_eff,
        r_lat=tgt.r_lat,
        normal_load=tgt.normal_load,
        path_radius=tgt.path_radius,
        slip_angle_deg=params.lateral_slip_angle,
        analytic_pressure=grid is None,
        meta={"base_gamma": base.gamma_deg, "scale_x": sx, "scale_y": sy},
    )
    patch.a = patch.length() / 2.0
    patch.b = patch.width() / 2.0
    area = patch.area()
    patch.peak_pressure = (
        float(grid.p.max()) if grid is not None else (tgt.normal_load / area if area > 0 else 0.0)
    )
    return patch


def interpolated_patch(
    low: ContactPatch, high: ContactPatch, gamma_deg: float, tread_width: float
) -> ContactPatch:
    """Blend two measured patches that bracket ``gamma_deg``.

    Outlines are resampled to a common angular parametrisation about their own
    centroids before blending, so the interpolation morphs the shape rather than
    pairing up vertices that happen to share an index.
    """
    span = high.gamma_deg - low.gamma_deg
    t = 0.0 if abs(span) < 1e-9 else (gamma_deg - low.gamma_deg) / span
    t = float(np.clip(t, 0.0, 1.0))

    n = 181
    ra, ca = _radial_profile(low, n)
    rb, cb = _radial_profile(high, n)
    r = (1 - t) * ra + t * rb
    cy = (1 - t) * ca[1] + t * cb[1]
    ang = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    out = np.column_stack([r * np.cos(ang), cy + r * np.sin(ang)])

    patch = ContactPatch(
        gamma_deg=gamma_deg,
        outline=out,
        tread_half_width=tread_width / 2.0,
        source="interpolated",
        provenance=(
            f"interpolated between the {low.source} patch at {low.gamma_deg:g} deg and the "
            f"{high.source} patch at {high.gamma_deg:g} deg (t={t:.2f})"
        ),
        y_center=cy,
        normal_load=(1 - t) * low.normal_load + t * high.normal_load,
        r_eff=(1 - t) * low.r_eff + t * high.r_eff,
        r_lat=(1 - t) * low.r_lat + t * high.r_lat,
        slip_angle_deg=low.slip_angle_deg,
        meta={"t": t, "between": [low.gamma_deg, high.gamma_deg]},
    )
    patch.a = patch.length() / 2.0
    patch.b = patch.width() / 2.0
    area = patch.area()
    patch.peak_pressure = patch.normal_load / area if area > 0 and np.isfinite(patch.normal_load) else 0.0
    return patch


def _radial_profile(patch: ContactPatch, n: int) -> tuple[Array, tuple[float, float]]:
    """Radius vs. angle about the patch centroid, resampled to ``n`` angles."""
    cx, cy = patch.centroid()
    p = patch.outline - np.array([cx, cy])
    ang = np.arctan2(p[:, 1], p[:, 0]) % (2 * np.pi)
    rad = np.hypot(p[:, 0], p[:, 1])
    order = np.argsort(ang)
    ang, rad = ang[order], rad[order]
    ang_ext = np.concatenate([ang - 2 * np.pi, ang, ang + 2 * np.pi])
    rad_ext = np.concatenate([rad, rad, rad])
    target = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    return np.interp(target, ang_ext, rad_ext), (cx, cy)


# ---------------------------------------------------------------------------
# resolution: which source for which lean angle
# ---------------------------------------------------------------------------
@dataclass
class CPLibrary:
    """Resolves a contact patch for any lean angle, from whatever is available.

    Register known patches -- measured footprints or user-specified shapes --
    with :meth:`add_measured`; ask for any lean angle with :meth:`patch_for`.
    The fallback order is fixed and documented at the top of this module, and
    every returned patch carries the provenance of the rung it came from.
    """

    crown: CrownProfile
    tread_width: float
    params: CPParams = DEFAULT_CP_PARAMS
    generator: str = "winkler"  # "winkler" | "rhyne"
    measured: dict[float, ContactPatch] = field(default_factory=dict)
    match_tolerance: float = 0.5  # deg
    allow_transfer: bool = True

    def add_measured(self, patch: ContactPatch) -> None:
        self.measured[float(patch.gamma_deg)] = patch

    def generate(self, gamma_deg: float) -> ContactPatch:
        if self.generator == "rhyne":
            return rhyne_patch(gamma_deg, self.crown, self.params, self.tread_width)
        return winkler_patch(gamma_deg, self.crown, self.params, self.tread_width)

    def patch_for(self, gamma_deg: float) -> ContactPatch:
        gammas = sorted(self.measured)

        # 1. measured at this angle
        for g in gammas:
            if abs(g - gamma_deg) <= self.match_tolerance:
                return self.measured[g]

        if gammas and self.allow_transfer:
            below = [g for g in gammas if g < gamma_deg]
            above = [g for g in gammas if g > gamma_deg]
            # 2. bracketed by two measurements
            if below and above:
                return interpolated_patch(
                    self.measured[below[-1]], self.measured[above[0]], gamma_deg, self.tread_width
                )
            # 3. transfer from the nearest measurement
            nearest = min(gammas, key=lambda g: abs(g - gamma_deg))
            return transferred_patch(
                self.measured[nearest], gamma_deg, self.crown, self.params, self.tread_width
            )

        # 4/5. generate
        return self.generate(gamma_deg)

    def coverage(self, lean_angles: Sequence[float]) -> list[dict]:
        """Per-lean-angle report of which source was used -- for the UI."""
        rows = []
        for g in lean_angles:
            patch = self.patch_for(g)
            rows.append(
                {
                    "gamma_deg": g,
                    "source": patch.source,
                    "provenance": patch.provenance,
                    "measured": patch.source == "measured",
                }
            )
        return rows


def max_supported_lean(crown: CrownProfile) -> float:
    """Largest lean angle the crown profile can actually reach, degrees."""
    return float(np.degrees(np.max(crown.tangent_angle(crown.y))))


# Backwards-compatible aliases -----------------------------------------------
def contact_patch(
    gamma_deg: float,
    crown: CrownProfile,
    params: CPParams = DEFAULT_CP_PARAMS,
    tread_width: float | None = None,
) -> ContactPatch:
    """Default generated patch (Winkler).  Kept as the historical entry point."""
    return winkler_patch(gamma_deg, crown, params, tread_width)


def contact_patch_from_footprint(
    gamma_deg: float,
    length: float,
    width: float,
    y_center: float,
    tread_width: float,
    peak_pressure: float = 0.9,
    r_eff: float = 320.0,
    r_lat: float = 120.0,
    exponent: float = 2.0,
) -> ContactPatch:
    """Build a patch from measured footprint *dimensions* rather than an outline."""
    a, b = length / 2.0, width / 2.0
    g = math.radians(gamma_deg)
    patch = ContactPatch(
        gamma_deg=gamma_deg,
        outline=superellipse_outline(a, b, y_center, exponent),
        tread_half_width=tread_width / 2.0,
        source="measured",
        provenance=f"measured footprint dimensions {length:.1f} x {width:.1f} mm",
        a=a,
        b=b,
        y_center=y_center,
        peak_pressure=peak_pressure,
        r_eff=r_eff,
        r_lat=r_lat,
        path_radius=math.inf if gamma_deg <= 1e-9 else r_eff / math.tan(g),
        delta=0.0,
    )
    return patch
