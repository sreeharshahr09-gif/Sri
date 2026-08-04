"""Physics validation against closed-form hand calculations.

Everything here is checked against an equation worked out by hand, not against
a previous run of this code -- so these tests catch a physics regression that a
self-consistent golden-value test would happily bless.

The reference case is a plain rectangular block, chosen because every quantity
in the model has a closed form for it:

    block 20 mm (circumferential) x 10 mm (lateral), NSD 8 mm,
    Shore 60 A  ->  E = 6.89 N/mm^2, Gent k = 0.64, nu = 0.49
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tread_eval.contact_patch import (CPParams, lean_scale_factors, max_supported_lean,
                                      winkler_patch)
from tread_eval.cp_shapes import ShapeSpec, shape_patch
from tread_eval.schema import Block, CrownProfile
from tread_eval.stiffness import (SHORE_E_TABLE, StiffnessParams, block_stiffness, calc_g,
                                  effective_k, shore_e, shore_k, shore_range_warning)

L, W, H = 20.0, 10.0, 8.0          # mm: circumferential, lateral, NSD
E = shore_e(60)                     # 6.89 N/mm^2
NU = 0.49
G = calc_g(E, NU)
K_GENT = shore_k(60)                # 0.64
RECT = [(0.0, 0.0), (L, 0.0), (L, W), (0.0, W)]


def _block(**kw) -> Block:
    kw.setdefault("height", H)
    return Block(id="R", pitch_id="", polygon=RECT, zone="center", **kw)


# --- compression: Gent shape factor ------------------------------------
def test_kz_matches_gent_hand_calculation():
    """Kz = E_eff*A/NSD with E_eff = E(1+2kS^2)/(1+E_eff/Kb), S = A/(NSD*P)."""
    area, perim = L * W, 2 * (L + W)
    s = area / (H * perim)
    e_eff_uncorr = E * (1 + 2 * K_GENT * s * s)
    e_eff = e_eff_uncorr / (1 + e_eff_uncorr / 1100.0)
    kz_hand = e_eff * area / H

    got = block_stiffness(_block(), StiffnessParams(shore_a=60, poisson=NU))
    assert got.shape_factor == pytest.approx(s, rel=1e-12)
    assert got.e_eff == pytest.approx(e_eff, rel=1e-12)
    assert got.kz == pytest.approx(kz_hand, rel=1e-12)
    assert got.kz == pytest.approx(208.928315, rel=1e-6)  # the worked number


def test_shape_factor_grows_when_the_block_gets_shallower():
    """S = A/(NSD*P): halving the depth doubles the shape factor, so Kz rises."""
    deep = block_stiffness(_block(height=8.0), StiffnessParams(shore_a=60))
    shallow = block_stiffness(_block(height=4.0), StiffnessParams(shore_a=60))
    assert shallow.shape_factor == pytest.approx(2 * deep.shape_factor, rel=1e-12)
    assert shallow.kz > deep.kz


# --- shear: Castigliano ------------------------------------------------
def _hand_shear(mode: str) -> tuple[float, float]:
    """Closed-form Kx, Ky for a prismatic block (no draft, no sipes)."""
    area = L * W
    iyy = W * L**3 / 12.0   # resists deflection in x (circumferential)
    ixx = L * W**3 / 12.0   # resists deflection in y (lateral)
    if mode == "parallel":
        cb, cs = H**3 / (12.0 * E), H / (G * area)
    else:
        cb, cs = H**3 / (3.0 * E), 6.0 * H / (5.0 * G * area)
    return 1.0 / (cb / iyy + cs), 1.0 / (cb / ixx + cs)


@pytest.mark.parametrize("mode", ["parallel", "free"])
def test_shear_stiffness_matches_closed_form(mode):
    """Kx must use Iyy = int x^2 dA and Ky must use Ixx -- the index convention
    is the single easiest thing to get backwards in this model."""
    kx_hand, ky_hand = _hand_shear(mode)
    got = block_stiffness(_block(), StiffnessParams(shore_a=60, poisson=NU, mode=mode))
    # tolerance covers the 40-slice midpoint quadrature, not a modelling gap
    assert got.kx == pytest.approx(kx_hand, rel=3e-4)
    assert got.ky == pytest.approx(ky_hand, rel=3e-4)


def test_free_end_is_softer_than_parallel_end():
    """A cantilever with a free tip deflects more than one held flat."""
    par = block_stiffness(_block(), StiffnessParams(shore_a=60, mode="parallel"))
    free = block_stiffness(_block(), StiffnessParams(shore_a=60, mode="free"))
    assert free.kx < par.kx and free.ky < par.ky


def test_quadrature_converges_to_the_closed_form():
    """Refining the slice count must walk the answer onto the exact integral."""
    kx_hand, ky_hand = _hand_shear("parallel")
    errs = []
    for n in (10, 40, 160, 640):
        kx, ky, _, _ = effective_k(np.asarray(RECT, float), H, E, NU, 0.0, "parallel", (), n)
        errs.append(abs(ky - ky_hand) / ky_hand)
    assert errs == sorted(errs, reverse=True), "error must fall as slices are added"
    assert errs[-1] < 1e-5


def test_a_block_long_in_x_is_stiffer_in_x():
    """Directionality sanity: I ~ L^3 in the direction of the load."""
    long_x = block_stiffness(
        Block(id="a", pitch_id="", polygon=[(0, 0), (30, 0), (30, 6), (0, 6)], zone="center", height=H),
        StiffnessParams(shore_a=60))
    assert long_x.kx > long_x.ky


def test_positive_draft_stiffens_the_block():
    """Positive mould draft widens the base, adding material below the surface."""
    plain = block_stiffness(_block(draft_angle=0.0), StiffnessParams(shore_a=60))
    drafted = block_stiffness(_block(draft_angle=4.0), StiffnessParams(shore_a=60))
    assert drafted.kx > plain.kx and drafted.ky > plain.ky


# --- contact patch -----------------------------------------------------
def test_winkler_pressure_field_carries_the_stated_load():
    """The whole generator is closed by force balance: integral p.dA must equal Fz."""
    crown = CrownProfile.dual_radius(159.0)
    params = CPParams(vertical_load=1500.0, load_rises_with_lean=False)
    for gamma in (0.0, 20.0):
        patch = winkler_patch(gamma, crown, params, 159.0)
        xs = np.linspace(-patch.a * 1.02, patch.a * 1.02, 1201)
        ys = np.linspace(patch.y_center - patch.b * 1.02, patch.y_center + patch.b * 1.02, 1201)
        load = patch.pressure(xs[None, :], ys[:, None]).sum() * (xs[1] - xs[0]) * (ys[1] - ys[0])
        assert load == pytest.approx(patch.normal_load, rel=3e-3)


def test_contact_point_sits_where_the_tangent_equals_the_lean():
    """On a constant-radius crown the contact point is exactly the arc s = R*gamma."""
    radius = 300.0
    crown = CrownProfile.from_radius_profile(np.linspace(-60, 60, 801), np.full(801, radius))
    for gamma in (5.0, 10.0):  # both within this crown's reach
        assert crown.contact_lateral_position(gamma) == pytest.approx(
            math.radians(gamma) * radius, rel=1e-3)


def test_max_supported_lean_is_the_steepest_tangent():
    """Half-width 60 mm at R = 300 mm can only reach 60/300 rad."""
    crown = CrownProfile.from_radius_profile(np.linspace(-60, 60, 801), np.full(801, 300.0))
    assert max_supported_lean(crown) == pytest.approx(math.degrees(60.0 / 300.0), rel=1e-3)


def test_contact_position_saturates_beyond_the_reachable_lean():
    """Past the limit there is no contact point; it pins to the tread edge."""
    crown = CrownProfile.from_radius_profile(np.linspace(-60, 60, 801), np.full(801, 300.0))
    assert crown.contact_lateral_position(80.0) == pytest.approx(60.0)


# --- lean scaling of a stated shape ------------------------------------
def test_lean_scale_factors_are_unity_at_the_reference_angle():
    crown = CrownProfile.dual_radius(159.0)
    s_len, s_wid = lean_scale_factors(crown, 0.0, CPParams())
    assert s_len == pytest.approx(1.0) and s_wid == pytest.approx(1.0)


def test_lean_scaling_narrows_the_patch_as_the_crown_radius_collapses():
    """The physical signature of a 2W tyre: the patch gets narrower and longer
    with lean, because R_lat collapses at the shoulder while R_eff barely moves.

    Two competing effects set the width, and both belong in the answer:
    ``b = sqrt(2 R_lat delta)`` falls with the shoulder radius (125 -> 59 mm,
    a factor 0.69) but ``delta`` *rises* because the load grows as Fz/cos(gamma)
    over a smaller footprint (a factor 1.18).  Net narrowing at 40 deg is
    therefore ~19%, not the ~31% the radius alone would suggest.  Any further
    loss of area at high lean is tread-edge clipping, which is a separate
    geometric effect reported separately.
    """
    crown = CrownProfile.dual_radius(159.0)
    params = CPParams()
    s_len, s_wid = lean_scale_factors(crown, 40.0, params)
    assert 0.70 < s_wid < 0.85, f"expected ~19% narrowing at 40 deg, got x{s_wid:.3f}"
    assert s_len > 1.0, f"patch must lengthen as R_eff drops, got x{s_len:.3f}"
    # Two regimes, and the trend is deliberately NOT monotonic. While the
    # contact point is still on the crown the radius barely moves, so the rising
    # load wins and the patch grows a little; once it reaches the shoulder
    # transition the collapsing radius takes over and it narrows for good.
    widths = [lean_scale_factors(crown, g, params)[1] for g in (0.0, 10.0, 20.0, 30.0, 40.0)]
    assert max(widths) < 1.05, f"crown-region growth must stay small: {widths}"
    shoulder = widths[2:]  # 20 deg onward, past the break fraction
    assert shoulder == sorted(shoulder, reverse=True), f"must narrow past the shoulder break: {widths}"
    # and it must track the Winkler generator it is derived from, exactly
    ref, tgt = winkler_patch(0.0, crown, params, 159.0), winkler_patch(40.0, crown, params, 159.0)
    assert s_wid == pytest.approx(tgt.b / ref.b, rel=1e-9)
    assert s_len == pytest.approx(tgt.a / ref.a, rel=1e-9)


def test_shape_patch_scales_with_lean_by_default():
    crown = CrownProfile.dual_radius(159.0)
    base = dict(shape="rounded", length=90.0, width=50.0, corner_radius=12.0)
    upright = shape_patch(ShapeSpec(**base, gamma_deg=0.0), crown, 159.0, CPParams())
    leaned = shape_patch(ShapeSpec(**base, gamma_deg=40.0), crown, 159.0, CPParams())
    assert leaned.width() < 0.75 * upright.width()
    assert "scaled to 40 deg lean" in leaned.provenance


def test_shape_patch_can_hold_its_stated_size():
    """Opting out must give back exactly the stated dimensions at every lean."""
    crown = CrownProfile.dual_radius(159.0)
    spec = ShapeSpec(shape="rectangle", length=60.0, width=30.0, gamma_deg=20.0,
                     scale_with_lean=False)
    patch = shape_patch(spec, crown, 159.0, CPParams())
    assert patch.length() == pytest.approx(60.0)
    assert patch.width() == pytest.approx(30.0)
    assert "scaled to" not in patch.provenance


# --- input validation --------------------------------------------------
@pytest.mark.parametrize("kw,fragment", [
    (dict(length=0.0), "length must be positive"),
    (dict(length=-5.0), "length must be positive"),
    (dict(width=-50.0), "width must be positive"),
    (dict(length=float("nan")), "must be a finite number"),
    (dict(width=float("inf")), "must be a finite number"),
    (dict(corner_radius=-3.0), "corner_radius must be >= 0"),
    (dict(exponent=0.0), "exponent must be positive"),
    (dict(taper=1.0), "taper must lie strictly between"),
    (dict(load_N=-10.0), "load_N must be positive"),
    (dict(shape="hexagon"), "unknown contact patch shape"),
])
def test_invalid_shape_geometry_is_rejected_with_a_specific_message(kw, fragment):
    """A negative width used to produce a valid-looking 4500 mm^2 patch, because
    the shoelace area takes an absolute value.  It must fail loudly instead."""
    crown = CrownProfile.dual_radius(159.0)
    spec = ShapeSpec(**{**dict(shape="rounded", length=90.0, width=50.0), **kw})
    with pytest.raises(ValueError, match=fragment.replace("(", r"\(")):
        shape_patch(spec, crown, 159.0, CPParams())


def test_negative_load_is_rejected_with_a_readable_message():
    """Used to surface as a bare 'math domain error' from sqrt()."""
    crown = CrownProfile.dual_radius(159.0)
    with pytest.raises(ValueError, match="vertical load must be"):
        winkler_patch(0.0, crown, CPParams(vertical_load=-100.0), 159.0)


def test_zero_foundation_modulus_is_rejected():
    crown = CrownProfile.dual_radius(159.0)
    with pytest.raises(ValueError, match="foundation modulus"):
        winkler_patch(0.0, crown, CPParams(foundation_modulus=0.0), 159.0)


# --- compound range ----------------------------------------------------
def test_shore_inside_the_table_raises_no_warning():
    for shore in (30, 45, 60, 70):
        assert shore_range_warning(shore) is None


@pytest.mark.parametrize("shore,used", [(95, 70), (20, 30)])
def test_shore_outside_the_table_warns_and_names_the_substitute(shore, used):
    """Silently evaluating a 95A compound as 70A is a >2x error in E."""
    msg = shore_range_warning(shore)
    assert msg is not None
    assert str(used) in msg and "outside the validated Gent table" in msg
    assert str(SHORE_E_TABLE[used]) in msg
