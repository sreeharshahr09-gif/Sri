"""
Circumferential strain field on the inner liner of a 10.00R20 truck tire
rolling through a flat-road contact patch, plus the piezoelectric charge
waveform it induces in a surface-bonded patch.

This is a first-order *analytical* model intended for sensing / energy-harvesting
signal simulation. It is not a finite-element solution and makes no claim to FE
accuracy; what it does guarantee is internal physical consistency:

  * the contact patch length follows from vertical equilibrium (load / pressure),
  * the peak liner strain follows from belt flattening (curvature change), not
    from a free-floating constant,
  * the strain history integrates to zero around a closed revolution,
  * the field is C-infinity everywhere (no clipping, no hard zeroing),
  * the piezo charge integrates strain over the real patch footprint rather than
    sampling a single point.

Sign convention
---------------
Strain is measured *relative to the free-rolling inflated state*, i.e. the
static inflation hoop strain is taken as the zero reference. Positive =
circumferential tension. Only this AC component matters for a piezo harvester.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------- #
#  Parameters
# --------------------------------------------------------------------------- #


@dataclass
class TireParams:
    """Tire, operating point and geometry. All SI units."""

    # --- operating point (the adjustable parameters, requirement F) ---
    load: float = 40_000.0             # N, static vertical load on the tire
    inflation_pressure: float = 827e3  # Pa
    speed: float = 40 / 3.6            # m/s, forward velocity (40 km/h)

    # --- tire geometry ---
    inflated_radius: float = 0.53      # m, loaded/inflated rolling radius
    section_width: float = 0.27        # m, overall section width

    # Fraction of section width that actually carries tread in contact. The
    # sidewall bulge is part of `section_width` but never touches the road, so
    # the contact width is always meaningfully smaller than the section width.
    contact_width_ratio: float = 0.78

    # Mean contact pressure exceeds inflation pressure because the carcass and
    # bead carry part of the load in bending. 1.1-1.3 is the usual measured
    # range for radial truck tires.
    pressure_ratio: float = 1.15

    # Distance from the neutral axis of the belt/carcass package to the inner
    # liner. This is what converts a curvature change into a liner strain, and
    # it is the single physical knob that sets the peak strain magnitude.
    # Calibrated so the nominal 40 kN / 827 kPa case lands near 2500 ue.
    neutral_axis_offset: float = 1.38e-3  # m

    # Bending stiffness per unit width of the belt package (N*m). Sets how
    # sharply the belt can bend into and out of the flat, and therefore how
    # completely it conforms to the road. ~400 N*m corresponds to E*t^3/12 for
    # a ~15 mm belt package at ~1 GPa, which is the right order for a truck
    # tire. Calibrate against measured data if you have it.
    belt_bending_stiffness: float = 394.0

    # --- shape parameters of the analytical strain profile ---
    tension_flatness: int = 3        # super-Gaussian order of the tensile plateau
    width_flatness: int = 2          # super-Gaussian order of the width profile
    compression_ratio: float = 0.35  # entry/exit compression peak / tension peak
    compression_spread: float = 0.20 # compression lobe sigma, as a fraction of L

    # --- overrides: set to a number to pin the value instead of deriving it ---
    contact_patch_length: float | None = None  # m
    peak_strain: float | None = None           # microstrain

    # ---------------------------------------------------------------- derived
    @property
    def contact_width(self) -> float:
        """Width of the tread actually in contact with the road (m)."""
        return self.section_width * self.contact_width_ratio

    @property
    def contact_area(self) -> float:
        """Contact area from vertical equilibrium: A = F / (k_p * p)."""
        return self.load / (self.pressure_ratio * self.inflation_pressure)

    @property
    def L(self) -> float:
        """Contact patch length (m).

        Derived from equilibrium unless explicitly overridden. Width is set by
        the tread, so all of the load-driven growth goes into the *length* --
        this is why increasing load mainly lengthens the strain pulse.
        """
        if self.contact_patch_length is not None:
            return self.contact_patch_length
        return self.contact_area / self.contact_width

    @property
    def belt_relaxation_length(self) -> float:
        """Length over which the belt bends into or out of the flat (m).

        The belt behaves like a beam under tension on an elastic foundation.
        Its tension per unit width is the inflation membrane load N = p * R, so
        the characteristic bending length is lambda = sqrt(D / N). Raising the
        inflation pressure tightens the belt and *shortens* lambda, letting it
        snap flatter over a shorter distance.
        """
        return np.sqrt(
            self.belt_bending_stiffness / (self.inflation_pressure * self.inflated_radius)
        )

    @property
    def conformity(self) -> float:
        """How completely the belt flattens at the patch centre, in [0, 1).

        The belt cannot reach zero curvature instantly at the patch edge; it
        relaxes toward flat over ~lambda. If the patch is long compared with
        lambda the centre is fully flat and conformity -> 1. If the patch is
        short -- a lightly loaded or heavily inflated tire -- the belt never
        fully flattens and the peak strain falls short of h/R.
        """
        return 1.0 - np.exp(-self.L / (2 * self.belt_relaxation_length))

    @property
    def eps_peak(self) -> float:
        """Peak tensile liner strain (microstrain).

        Physical basis: inside the contact patch the belt is flattened, so its
        curvature changes from 1/R to ~0, and a fibre offset h from the neutral
        axis sees h * delta_kappa = h / R. That ceiling is pure geometry -- the
        road is flat no matter how hard you press on it -- so peak strain does
        NOT scale with load the way patch length does.

        It is not perfectly load-independent either, though. The ceiling is
        reached only insofar as the belt actually conforms, so

            eps_peak = (h / R) * conformity(load, pressure).

        The result is a *saturating* dependence: strong below rated load, where
        a short patch cannot flatten the belt, and nearly flat above it. Over
        25-55 kN the peak moves about 13% while the patch length more than
        doubles. Pressure enters twice and the two effects partly cancel --
        higher pressure shortens the patch (less conformity) but also tightens
        the belt (shorter lambda, more conformity) -- leaving a net swing of
        only ~1% over 650-1000 kPa, in the direction of lower strain at higher
        pressure.
        """
        if self.peak_strain is not None:
            return self.peak_strain
        return self.neutral_axis_offset / self.inflated_radius * self.conformity * 1e6

    @property
    def circumference(self) -> float:
        return 2 * np.pi * self.inflated_radius

    @property
    def rotation_frequency(self) -> float:
        """Wheel rotation frequency (Hz) -- the fundamental harvester frequency."""
        return self.speed / self.circumference

    @property
    def patch_residence_time(self) -> float:
        """Time a material point spends inside the contact patch (s)."""
        return self.L / self.speed


@dataclass
class PiezoParams:
    """Piezoelectric patch. All SI units."""

    d33: float = 550e-12          # C/N
    youngs_modulus: float = 24.4e9  # Pa
    patch_length: float = 132e-3  # m, dimension along the ROLLING direction
    patch_width: float = 10e-3    # m, dimension across the tread
    relative_permittivity: float = 1700.0
    thickness: float = 0.2e-3     # m

    @property
    def area(self) -> float:
        return self.patch_length * self.patch_width

    @property
    def capacitance(self) -> float:
        """Parallel-plate capacitance.

        WARNING: valid only for a through-thickness-electroded (d31-mode) patch.
        For an interdigitated-electrode d33-mode device (e.g. an MFC) the
        relevant geometry is the electrode finger pitch, not the film thickness,
        and this expression does not apply. See AUDIT.md, finding P2.
        """
        return (
            self.relative_permittivity * 8.854e-12 * self.area / self.thickness
        )


# --------------------------------------------------------------------------- #
#  Strain field
# --------------------------------------------------------------------------- #


def circumferential_profile(s: np.ndarray, p: TireParams) -> np.ndarray:
    """Normalised strain shape along the rolling direction, peak = 1.

    `s` is arc-length position relative to the contact patch centre (m), and may
    span the whole revolution.

    The profile is built from three physically distinct pieces:

    1. TENSILE PLATEAU (inside the contact patch). The belt is flattened
       against the road, curvature drops from 1/R to ~0, and the inner liner --
       sitting inside the neutral axis -- is stretched. Because the patch is
       genuinely flat over most of its length, this is a flat-topped
       super-Gaussian, NOT a narrow bell: a plain Gaussian would wrongly imply
       the strain peaks only at the exact patch centre.

    2. ENTRY / EXIT COMPRESSION LOBES (just OUTSIDE the patch). Immediately
       before entry and after exit the carcass buckles locally as it bends into
       and out of the flat: curvature there is *higher* than 1/R, which reverses
       the sign and puts the liner into compression. These lobes are centred
       outside +-L/2, which is where they physically occur.

    3. DISTRIBUTED OFFSET (over the free circumference). A material point on a
       closed loop must return to its original length after one revolution, so
       the strain must satisfy the kinematic constraint

           integral of eps ds around the loop = 0.

       The tensile pulse is large but short; it is balanced by a small
       compressive offset spread over the entire remaining circumference. This
       offset is computed, not tuned.
    """
    L = p.L
    a = L / 2.0                       # tensile plateau half-width
    m = p.tension_flatness
    sigma_c = p.compression_spread * L
    s0 = a + sigma_c                  # compression lobe centres, just outside the patch

    tension = np.exp(-((np.abs(s) / a) ** (2 * m)))
    compression = np.exp(-((np.abs(s) - s0) ** 2) / (2 * sigma_c**2))

    local = tension - p.compression_ratio * compression

    # Enforce the closed-loop constraint by adding a uniform offset.
    grid = np.linspace(-p.circumference / 2, p.circumference / 2, 200_001)
    local_grid = np.exp(-((np.abs(grid) / a) ** (2 * m))) - p.compression_ratio * np.exp(
        -((np.abs(grid) - s0) ** 2) / (2 * sigma_c**2)
    )
    offset = -np.trapezoid(local_grid, grid) / p.circumference

    profile = local + offset
    return profile / (local_grid + offset).max()


def width_profile(y: np.ndarray, p: TireParams) -> np.ndarray:
    """Normalised strain shape across the tread width, peak = 1 at y = 0.

    Strain is greatest near the centreline, where the belt is stiffest and the
    flattening is most complete, and falls off toward the shoulders, which are
    free to roll away from the road and carry far less curvature change. A
    super-Gaussian tied to the *contact* width (not the section width) makes the
    strain die out at the shoulders instead of leaving an unphysical ~46% of the
    peak sitting at the tread edge.
    """
    b = p.contact_width / 2.0
    return np.exp(-((np.abs(y) / b) ** (2 * p.width_flatness)))


def strain_field(s: np.ndarray, y: np.ndarray, p: TireParams) -> np.ndarray:
    """Circumferential strain (microstrain) at arc position `s`, width position `y`.

    Separable: eps(s, y) = eps_peak * f(s) * g(y). Separability is an
    approximation -- in reality the patch is slightly shorter at the shoulders
    than at the centreline -- but it is accurate to first order and keeps the
    field smooth and cheap to evaluate.
    """
    return p.eps_peak * circumferential_profile(s, p) * width_profile(y, p)


# --------------------------------------------------------------------------- #
#  Piezoelectric response
# --------------------------------------------------------------------------- #


def charge_waveform(
    s_patch: np.ndarray, p: TireParams, pz: PiezoParams, n_quad: int = 401
) -> np.ndarray:
    """Charge (C) on the patch as its centre passes arc position `s_patch`.

    Uses the prescribed constitutive relation

        Q = d33 * E * A * eps

    but with `eps` taken as the strain AVERAGED OVER THE PATCH FOOTPRINT rather
    than sampled at a single point. This matters a great deal here: the patch is
    132 mm long and the contact patch is of the same order, so the patch is
    never uniformly strained and a point sample overstates the charge
    substantially.
    """
    ds = np.linspace(-pz.patch_length / 2, pz.patch_length / 2, n_quad)
    dy = np.linspace(-pz.patch_width / 2, pz.patch_width / 2, 21)
    S, Y = np.meshgrid(ds, dy, indexing="ij")

    mean_strain = np.array(
        [strain_field(S + sc, Y, p).mean() for sc in np.atleast_1d(s_patch)]
    )
    return pz.d33 * pz.youngs_modulus * pz.area * mean_strain / 1e6


def current_waveform(
    q: np.ndarray, s: np.ndarray, p: TireParams
) -> np.ndarray:
    """Short-circuit current i = dQ/dt (A).

    The arc-length axis converts to time through the rolling speed, which is
    where the `speed` parameter finally enters the electrical result: the same
    strain field at twice the speed produces twice the current.
    """
    t = s / p.speed
    return np.gradient(q, t)


# --------------------------------------------------------------------------- #
#  Self-checks
# --------------------------------------------------------------------------- #


def consistency_report(p: TireParams, pz: PiezoParams) -> dict:
    """Quantitative checks that the model satisfies its own physics."""
    s = np.linspace(-p.circumference / 2, p.circumference / 2, 400_001)
    f = strain_field(s, 0.0, p)

    tension = np.trapezoid(np.clip(f, 0, None), s)
    compression = abs(np.trapezoid(np.clip(f, None, 0), s))

    q = charge_waveform(np.linspace(-0.5, 0.5, 1201), p, pz)
    i = current_waveform(q, np.linspace(-0.5, 0.5, 1201), p)

    return {
        "contact_patch_length_mm": p.L * 1e3,
        "contact_width_mm": p.contact_width * 1e3,
        "contact_area_cm2": p.contact_area * 1e4,
        "mean_contact_pressure_kPa": p.load / p.contact_area / 1e3,
        "peak_strain_ue": f.max(),
        "peak_compression_ue": f.min(),
        "compression_over_tension": abs(f.min()) / f.max(),
        "closed_loop_integral_ue_mm": np.trapezoid(f, s) * 1e3,
        "tension_over_compression_area": tension / compression,
        "shoulder_strain_fraction": float(width_profile(np.array(p.section_width / 2), p)),
        "rotation_hz": p.rotation_frequency,
        "patch_residence_ms": p.patch_residence_time * 1e3,
        "q_peak_uC": q.max() * 1e6,
        "q_pp_uC": (q.max() - q.min()) * 1e6,
        "i_peak_mA": np.abs(i).max() * 1e3,
        "v_peak_V": q.max() / pz.capacitance,
        "energy_per_rev_uJ": (q.max() - q.min()) ** 2 / (2 * pz.capacitance) * 1e6,
        "power_uW": (q.max() - q.min()) ** 2
        / (2 * pz.capacitance)
        * p.rotation_frequency
        * 1e6,
    }


if __name__ == "__main__":
    p, pz = TireParams(), PiezoParams()
    for k, v in consistency_report(p, pz).items():
        print(f"{k:34s} {v: .4g}")
