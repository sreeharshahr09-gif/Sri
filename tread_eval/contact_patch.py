"""Contact patch as a function of lean angle -- SWAP POINT 3 of the brief.

PLACEHOLDER.  There is no footprint-vs-lean measurement behind any number here.
The whole lean dependence lives in :func:`contact_patch` so that when indoor
footprint data or FEA results arrive, that one function is replaced (or fed by
:func:`contact_patch_from_footprint`) and nothing downstream changes.

Model actually implemented here
------------------------------
Winkler (elastic-foundation) contact between a rigid road plane and a doubly
curved tyre crown.  With the surface approximated locally as a paraboloid of
longitudinal radius ``R_eff`` and lateral radius ``R_lat``, the interference is

    u(x, y) = delta - x^2 / (2 R_eff) - (y - y_c)^2 / (2 R_lat)

so contact is the ellipse ``u > 0`` with semi-axes ``a = sqrt(2 R_eff delta)``
and ``b = sqrt(2 R_lat delta)``, and the pressure is ``p = k_f * u``.  Requiring
the pressure to carry the vertical load closes the system:

    Fz = k_f * delta * pi * a * b / 2   =>   delta = sqrt(Fz / (k_f pi sqrt(R_eff R_lat)))

Lean enters through three routes, all of them geometric rather than fitted:

1. the contact point walks laterally to where the tread tangent is horizontal,
   which the crown profile determines (:meth:`CrownProfile.contact_lateral_position`);
2. ``R_lat`` at that new position is much smaller than at the crown, so the
   patch gets narrower and (because delta rises) slightly longer -- this is the
   mechanism behind the "fewer blocks in contact at lean" diagnostic;
3. the normal load rises as ``Fz0 / cos(gamma)`` in steady-state cornering.

The patch is clipped to the tread edge, so at high lean part of the ellipse
falls off the tyre and the effective patch is a truncated ellipse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .schema import CrownProfile


@dataclass(frozen=True)
class CPParams:
    """PLACEHOLDER contact-patch model constants."""

    vertical_load: float = 1500.0  # Fz0, N, upright static load on this tyre
    foundation_modulus: float = 0.47  # k_f, N/mm^3 (~E_tread / effective depth)
    wheel_radius: float = 320.0  # R, mm, free radius at the crown centre
    load_rises_with_lean: bool = True  # Fz = Fz0 / cos(gamma), steady cornering
    lateral_slip_angle: float = 0.0  # deg, added to the kinematic travel direction


DEFAULT_CP_PARAMS = CPParams()


@dataclass
class ContactPatch:
    """One contact patch, fully described in unwrapped tread coordinates (mm)."""

    gamma_deg: float
    y_center: float  # lateral centre of the patch, mm
    a: float  # semi-length, circumferential, mm
    b: float  # semi-width, lateral, mm
    delta: float  # foundation interference at the centre, mm
    peak_pressure: float  # MPa
    r_eff: float  # effective rolling radius at the contact point, mm
    r_lat: float  # lateral radius of curvature at the contact point, mm
    normal_load: float  # N
    tread_half_width: float
    path_radius: float  # mm, turn radius implied by lean (inf when upright)
    slip_angle_deg: float = 0.0
    clipped: bool = False  # True when the ellipse runs off the tread edge
    source: str = "parametric-winkler-placeholder"
    meta: dict = field(default_factory=dict)

    # -- geometry -------------------------------------------------------
    def contains(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Boolean membership for points given relative to the patch centre in x."""
        inside = (x / self.a) ** 2 + ((y - self.y_center) / self.b) ** 2 <= 1.0
        return inside & (np.abs(y) <= self.tread_half_width)

    def pressure(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Winkler pressure field, MPa; zero outside the patch."""
        u = self.delta - x**2 / (2 * self.r_eff) - (y - self.y_center) ** 2 / (2 * self.r_lat)
        p = np.where(self.contains(x, y), np.maximum(u, 0.0), 0.0)
        return p * 0.0 if self.delta <= 0 else p * (self.peak_pressure / max(self.delta, 1e-9))

    def outline(self, n: int = 181) -> np.ndarray:
        """Closed outline of the (possibly clipped) patch, shape (m, 2)."""
        t = np.linspace(0.0, 2 * np.pi, n)
        x = self.a * np.cos(t)
        y = self.y_center + self.b * np.sin(t)
        y = np.clip(y, -self.tread_half_width, self.tread_half_width)
        return np.column_stack([x, y])

    def nominal_area(self) -> float:
        """Analytic ellipse area, mm^2 (before tread-edge clipping)."""
        return math.pi * self.a * self.b

    def travel_direction_deg(self, x: np.ndarray | float) -> np.ndarray:
        """Instantaneous travel direction at longitudinal offset ``x``, degrees.

        Measured from the circumferential axis.  A leaned tyre follows a curved
        path, so the contact patch spins about the vertical at ``V / R_path``;
        a material point ``x`` ahead of the patch centre therefore travels at
        ``atan(x / R_path)`` to the centreline.  This is why groove angles must
        be scored against travel direction and not against the tyre axis.
        """
        if not np.isfinite(self.path_radius) or self.path_radius <= 0:
            base = np.zeros_like(np.asarray(x, dtype=float))
        else:
            base = np.degrees(np.arctan2(np.asarray(x, dtype=float), self.path_radius))
        return base + self.slip_angle_deg

    # -- raster ---------------------------------------------------------
    def masks(self, x_rel: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Rasterise onto a grid.

        ``x_rel`` is the signed circumferential offset of each column from the
        patch centre (already wrapped into [-C/2, C/2)), ``y`` the lateral
        coordinate of each row.  Returns ``(binary_mask, pressure_mask)`` of
        shape ``(len(y), len(x_rel))``.
        """
        xx = x_rel[None, :]
        yy = y[:, None]
        inside = self.contains(xx, yy)
        u = self.delta - xx**2 / (2 * self.r_eff) - (yy - self.y_center) ** 2 / (2 * self.r_lat)
        press = np.where(inside, np.maximum(u, 0.0) * (self.peak_pressure / max(self.delta, 1e-9)), 0.0)
        return inside, press


def contact_patch(
    gamma_deg: float,
    crown: CrownProfile,
    params: CPParams = DEFAULT_CP_PARAMS,
    tread_width: float | None = None,
) -> ContactPatch:
    """PLACEHOLDER footprint at lean angle ``gamma_deg``.

    <<< SWAP POINT: replace with measured footprint-vs-lean data (see
    :func:`contact_patch_from_footprint`) >>>
    """
    half_width = (tread_width if tread_width is not None else (crown.y[-1] - crown.y[0])) / 2.0

    y_c = crown.contact_lateral_position(gamma_deg)
    clipped_contact = abs(abs(y_c) - half_width) < 1e-6 and gamma_deg > 0

    r_lat = float(np.asarray(crown.local_radius(y_c)).ravel()[0])
    # Rolling radius shrinks by the crown drop at the contact point.
    r_eff = max(50.0, params.wheel_radius - float(np.asarray(crown.drop(y_c)).ravel()[0]))

    g = math.radians(gamma_deg)
    fz = params.vertical_load / max(math.cos(g), 0.2) if params.load_rises_with_lean else params.vertical_load

    delta = math.sqrt(fz / (params.foundation_modulus * math.pi * math.sqrt(r_eff * r_lat)))
    a = math.sqrt(2.0 * r_eff * delta)
    b = math.sqrt(2.0 * r_lat * delta)
    peak_pressure = params.foundation_modulus * delta  # MPa

    # Steady-state cornering path radius implied by this lean angle.
    path_radius = math.inf if gamma_deg <= 1e-9 else r_eff / math.tan(g)

    return ContactPatch(
        gamma_deg=gamma_deg,
        y_center=y_c,
        a=a,
        b=b,
        delta=delta,
        peak_pressure=peak_pressure,
        r_eff=r_eff,
        r_lat=r_lat,
        normal_load=fz,
        tread_half_width=half_width,
        path_radius=path_radius,
        slip_angle_deg=params.lateral_slip_angle,
        clipped=clipped_contact or (abs(y_c) + b > half_width),
        meta={"crown_measured": crown.measured},
    )


def contact_patch_from_footprint(
    gamma_deg: float,
    length: float,
    width: float,
    y_center: float,
    tread_width: float,
    peak_pressure: float = 0.9,
    r_eff: float = 320.0,
    r_lat: float = 120.0,
) -> ContactPatch:
    """Build a patch directly from measured footprint dimensions.

    This is the entry point for Section 11's open question -- once real
    lean-specific footprints exist they can be fed in here (one call per lean
    angle) and the parametric model above is bypassed entirely.
    """
    a, b = length / 2.0, width / 2.0
    delta = peak_pressure / 0.47
    g = math.radians(gamma_deg)
    return ContactPatch(
        gamma_deg=gamma_deg,
        y_center=y_center,
        a=a,
        b=b,
        delta=delta,
        peak_pressure=peak_pressure,
        r_eff=r_eff,
        r_lat=r_lat,
        normal_load=float("nan"),
        tread_half_width=tread_width / 2.0,
        path_radius=math.inf if gamma_deg <= 1e-9 else r_eff / math.tan(g),
        clipped=abs(y_center) + b > tread_width / 2.0,
        source="measured-footprint",
    )


def max_supported_lean(crown: CrownProfile) -> float:
    """Largest lean angle the crown profile can actually reach, degrees.

    Beyond this the contact point is pinned at the tread edge and the model is
    extrapolating -- worth reporting rather than silently clamping.
    """
    return float(np.degrees(np.max(crown.tangent_angle(crown.y))))
