"""Per-block tread stiffness -- Okonieski beam-mechanics model.

**No longer a placeholder.**  This is a port of the model in
``Tread_Pattern_Stiffness_Estimation_Tool_v6.4.html`` (functions ``effectiveK``,
``beamKMatrix``, ``computeKz``, ``polygonProps``, ``offsetPoly``), which
implements the Okonieski et al. (2003) framework with Gent (1959) compression
and the Gent-Lindley shape factor.  ``verify/`` extracts those functions
verbatim from the HTML tool and ``tests/test_stiffness_vs_tool.py`` runs both
implementations over randomised geometry and asserts they agree, so this file
cannot drift from the reference without a test failing.

What the model does
-------------------
**Shear (Kx, Ky, Kxy)** -- Castigliano slice integration along the block
height.  At height ``z`` the cross-section is the draft-tapered polygon; its
second-moment tensor gives a bending compliance weighted by the local bending
moment arm, and its area gives a shear compliance:

    C_bend = (1/E) ∫ w(z) · I⁻¹(z) dz        w(z) = (L−z)²      free end
                                             w(z) = (z−L/2)²    parallel end
    C_shear = κ/G ∫ dz / A(z)                κ = 6/5 (free), 1 (parallel)

    K = (C_bend + C_shear·1)⁻¹     (a full 2×2 inverse, so Ixy couples x and y)

Sipes split the block into sub-blocks that act as parallel springs within a
layer, and layers stack in series along the height.

**Compression (Kz)** -- Gent shape factor ``S = A_net/(NSD·P)``, effective
compression modulus ``E_eff = E(1 + 2kS²)`` with the bulk-compressibility
correction ``E_eff/(1 + E_eff/K_b)``, then ``Kz = E_eff·A/NSD``.

Terminology
-----------
``NSD`` is **non-skid depth** -- the block height in mm.  It is the same
quantity as ``Block.height``; the reference tool calls it ``nsd`` and this
module keeps that name only inside the ported functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .schema import Block, Sipe

# Gent, "Engineering with Rubber", Table 8.1 -- Shore A to Young's modulus
# (N/mm^2) and to the Gent shape-factor coefficient k.
SHORE_E_TABLE: dict[int, float] = {30: 1.50, 40: 2.50, 50: 4.00, 60: 6.89, 70: 12.00}
SHORE_K_TABLE: dict[int, float] = {30: 0.93, 40: 0.85, 50: 0.73, 60: 0.64, 70: 0.57}

Mode = str  # "free" | "parallel"


SHORE_MIN, SHORE_MAX = min(SHORE_E_TABLE), max(SHORE_E_TABLE)


def nearest_shore_key(shore: float) -> int:
    return min(SHORE_E_TABLE, key=lambda k: abs(k - shore))


def shore_range_warning(shore: float) -> str | None:
    """Message describing the clamp when ``shore`` falls outside the lookup table.

    Gent's table covers Shore A 30-70.  Outside that the nearest entry is used,
    so a hard 95A compound is silently evaluated as 70A -- a factor of two or
    more error in E.  Returning the message rather than warning here lets the
    caller decide where it belongs (report banner, CLI stderr, browser banner).
    """
    if SHORE_MIN <= shore <= SHORE_MAX:
        return None
    key = nearest_shore_key(shore)
    return (
        f"Shore A {shore:g} is outside the validated Gent table ({SHORE_MIN}-{SHORE_MAX} A); "
        f"properties of {key} A were used (E={SHORE_E_TABLE[key]} N/mm^2, k={SHORE_K_TABLE[key]}). "
        f"Supply an explicit modulus override for compounds outside this range."
    )


def shore_e(shore: float) -> float:
    return SHORE_E_TABLE.get(nearest_shore_key(shore), 6.89)


def shore_k(shore: float) -> float:
    return SHORE_K_TABLE.get(nearest_shore_key(shore), 0.64)


def calc_g(e: float, nu: float) -> float:
    return e / (2.0 * (1.0 + nu))


@dataclass(frozen=True)
class StiffnessParams:
    """Compound and boundary-condition settings for the whole pattern."""

    shore_a: float = 60.0
    poisson: float = 0.49
    mode: Mode = "parallel"
    """Boundary condition at the block's road-facing face.

    ``parallel`` -- the top face is held flat by friction and translates without
    rotating.  That is the correct condition for a block that is gripping, which
    is every block this tool aggregates, so it is the default here.
    ``free`` -- an unconstrained cantilever tip; the reference tool's UI offers
    both and lists this one first.  Both are verified against the reference.
    """
    bulk_modulus: float = 1100.0  # K_b, N/mm^2 -- rubber is nearly incompressible
    n_slices: int = 40  # Castigliano slices along the height
    e_override: float | None = None  # N/mm^2, bypasses the Shore table
    k_override: float | None = None  # Gent k, bypasses the Shore table
    sipe_model: str = "layered"
    """How a partially-siped block is decomposed.  ``layered`` | ``continuous``.

    ``layered`` (default) reproduces the reference tool exactly: the block is cut
    into layers at each sipe root, every layer is solved as an independently
    *guided* beam, and the layers are stacked in series.  Keep this when the
    numbers must match ``Tread_Pattern_Stiffness_Estimation_Tool_v6.4``.

    It carries a known artifact.  Guided-beam bending compliance goes as
    ``L^3``, so two layers of ``L/2`` in series are four times stiffer in
    bending than one layer of ``L``.  The artificial zero-rotation constraint
    the decomposition imposes at each sipe root therefore *stiffens* the block
    more than the sipe softens it, and a shallow sipe comes out stiffer than no
    sipe at all -- by up to +2% (parallel) or +4% (free) around a sipe depth of
    a quarter to a half of NSD.  Sipes deeper than about 0.6 NSD are past the
    crossover and soften correctly.

    ``continuous`` removes the artifact by treating the block as one beam whose
    section varies with height: at each Castigliano slice the active sipes split
    the section, the sub-blocks act as parallel springs (their second moments
    add), and the moment-weighted compliance is integrated over the full height
    exactly as the unsiped path does.  A zero-depth sipe becomes an exact no-op
    and stiffness falls monotonically with sipe depth.
    """

    def youngs(self) -> float:
        return self.e_override if self.e_override is not None else shore_e(self.shore_a)

    def gent_k(self) -> float:
        return self.k_override if self.k_override is not None else shore_k(self.shore_a)

    def shear(self) -> float:
        return calc_g(self.youngs(), self.poisson)


DEFAULT_PARAMS = StiffnessParams()


@dataclass(frozen=True)
class BlockStiffness:
    """Stiffness of one block, all in N/mm."""

    kx: float  # circumferential shear (braking / traction)
    ky: float  # lateral shear (cornering)
    kxy: float  # shear coupling; non-zero only for polygons with Ixy != 0
    kz: float  # vertical compression
    area: float  # mm^2, gross plan area
    net_area: float  # mm^2, after sipe slots
    perimeter: float
    shape_factor: float  # Gent S
    e_eff: float  # N/mm^2, effective compression modulus
    slenderness: float  # height / min plan dimension -- wear proxy
    n_sub_blocks: int


# ---------------------------------------------------------------------------
# geometry helpers -- ports of polygonProps / ensureCCW / offsetPoly
# ---------------------------------------------------------------------------
def ensure_ccw(verts: np.ndarray) -> np.ndarray:
    v = np.asarray(verts, dtype=float)
    x, y = v[:, 0], v[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    s = 0.5 * float(np.sum(x * yn - xn * y))
    return v[::-1] if s < 0 else v


@dataclass(frozen=True)
class PolyProps:
    area: float
    cx: float
    cy: float
    ixx: float
    iyy: float
    ixy: float
    perimeter: float


def polygon_props(verts: Sequence[Sequence[float]]) -> PolyProps | None:
    """Area, centroid, centroidal second moments and perimeter.

    ``ixx`` is the second moment about the centroidal x-axis (built from the y
    coordinates) and resists deflection in y; ``iyy`` is its mirror.  Vertices
    are forced counter-clockwise first: with clockwise input the signed area
    flips and ``ixy`` silently changes sign, which would corrupt the coupling
    term for any non-symmetric block.
    """
    v = np.asarray(verts, dtype=float)
    if v.shape[0] < 3:
        return None
    v = ensure_ccw(v)
    x, y = v[:, 0], v[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a_signed = 0.5 * float(cross.sum())
    area = abs(a_signed)
    if area < 1e-12:
        return None
    cx = float(np.sum((x + xn) * cross)) / (6.0 * a_signed)
    cy = float(np.sum((y + yn) * cross)) / (6.0 * a_signed)
    ixx_o = float(np.sum((y**2 + y * yn + yn**2) * cross)) / 12.0
    iyy_o = float(np.sum((x**2 + x * xn + xn**2) * cross)) / 12.0
    ixy_o = float(np.sum((x * yn + 2 * x * y + 2 * xn * yn + xn * y) * cross)) / 24.0
    perimeter = float(np.sum(np.hypot(xn - x, yn - y)))
    return PolyProps(
        area=area,
        cx=cx,
        cy=cy,
        ixx=abs(ixx_o - a_signed * cy * cy),
        iyy=abs(iyy_o - a_signed * cx * cx),
        ixy=ixy_o - a_signed * cx * cy,
        perimeter=perimeter,
    )


def polygon_perimeter(verts: Sequence[Sequence[float]]) -> float:
    v = np.asarray(verts, dtype=float)
    d = np.roll(v, -1, axis=0) - v
    return float(np.sum(np.hypot(d[:, 0], d[:, 1])))


def offset_poly(verts: Sequence[Sequence[float]], draft_deg: float, z: float) -> np.ndarray:
    """Draft-tapered cross-section: offset every edge outward by ``z·tan(draft)``.

    ``z`` is measured down from the tread surface, so ``z = 0`` is the top face
    and ``z = NSD`` the base.  Positive draft makes the base wider.  Vertices
    come out as intersections of the offset edges, so sharp corners stay sharp.
    If the offset eats the section (extreme negative draft), the original
    polygon is returned rather than an inverted one.
    """
    v = ensure_ccw(np.asarray(verts, dtype=float))
    if not draft_deg or z < 1e-12:
        return v
    n = len(v)
    nxt = np.roll(v, -1, axis=0)
    d = nxt - v
    length = np.hypot(d[:, 0], d[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        normals = np.column_stack([d[:, 1] / length, -d[:, 0] / length])
    normals[~np.isfinite(normals)] = 0.0
    off = z * math.tan(math.radians(draft_deg))
    e0 = v + off * normals
    e1 = nxt + off * normals

    out = np.empty_like(v)
    for i in range(n):
        ip = (i - 1) % n
        p1, p2 = e0[ip], e1[ip]
        p3, p4 = e0[i], e1[i]
        d1, d2 = p2 - p1, p4 - p3
        cr = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cr) < 1e-12:
            out[i] = 0.5 * (p2 + p3)
        else:
            t = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / cr
            out[i] = p1 + t * d1

    x, y = out[:, 0], out[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    if 0.5 * float(np.sum(x * yn - xn * y)) <= 1e-9:
        return v  # section inverted -- draft too negative for this geometry
    return out


def invert_sym2(m: tuple[float, float, float]) -> tuple[float, float, float]:
    """Invert a symmetric 2x2 given as (xx, yy, xy)."""
    xx, yy, xy = m
    det = xx * yy - xy * xy
    if abs(det) < 1e-18:
        return 0.0, 0.0, 0.0
    return yy / det, xx / det, -xy / det


# ---------------------------------------------------------------------------
# sipe handling -- ports of splitBySipe / computeNetContactAreaFromSipes
# ---------------------------------------------------------------------------
def _split_by_line(poly: np.ndarray, p1, p2, width: float) -> list[np.ndarray]:
    """Cut a polygon with an infinite line, returning the pieces.

    ``width`` insets each piece away from the cut, which is how a sipe slot of
    finite thickness removes material.
    """
    p1 = np.asarray(p1, float)
    p2 = np.asarray(p2, float)
    d = p2 - p1
    n = np.array([-d[1], d[0]], dtype=float)
    ln = float(np.hypot(*n))
    if ln < 1e-12:
        return [poly]
    n /= ln
    half = width / 2.0
    pieces = []
    for sign in (1.0, -1.0):
        clipped = _clip_halfplane(poly, n * sign, float(np.dot(n * sign, p1)) + half)
        if len(clipped) >= 3:
            pieces.append(clipped)
    return pieces or [poly]


def _clip_halfplane(poly: np.ndarray, normal: np.ndarray, offset: float) -> np.ndarray:
    """Sutherland-Hodgman clip to {p : normal·p >= offset}."""
    out: list[np.ndarray] = []
    n = len(poly)
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        dc = float(np.dot(normal, cur)) - offset
        dn = float(np.dot(normal, nxt)) - offset
        if dc >= 0:
            out.append(cur)
        if (dc >= 0) != (dn >= 0):
            t = dc / (dc - dn) if abs(dc - dn) > 1e-15 else 0.0
            out.append(cur + t * (nxt - cur))
    return np.asarray(out, dtype=float) if out else np.empty((0, 2))


def _clipped_seg_len(p1: np.ndarray, p2: np.ndarray, poly: np.ndarray) -> float:
    """Length of segment p1-p2 inside the polygon, by Liang-Barsky half-plane clipping.

    Ported exactly from the reference tool.  Note the assumption it carries:
    Liang-Barsky treats the polygon as the intersection of its edge half-planes,
    which is only the polygon itself when it is **convex**.  For a concave block
    this over-reports the inside length (it clips to the convex hull), so the
    sipe slot area -- and therefore Kz -- is slightly conservative there.  Kept
    as-is deliberately so this module agrees with the reference tool; the shear
    stiffness path does a proper polygon split and is unaffected.
    """
    if poly is None or len(poly) < 3:
        return 0.0
    dx, dy = float(p2[0] - p1[0]), float(p2[1] - p1[1])
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-12:
        return 0.0

    x, y = poly[:, 0], poly[:, 1]
    signed_a = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    ccw = signed_a > 0

    t_in, t_out = 0.0, 1.0
    n = len(poly)
    for i in range(n):
        v1, v2 = poly[i], poly[(i + 1) % n]
        ex, ey = float(v2[0] - v1[0]), float(v2[1] - v1[1])
        out_nx, out_ny = (ey, -ex) if ccw else (-ey, ex)
        p_val = out_nx * (p1[0] - v1[0]) + out_ny * (p1[1] - v1[1])
        d_val = out_nx * dx + out_ny * dy
        if abs(d_val) < 1e-10:
            if p_val > 1e-10:
                return 0.0
        else:
            t = -p_val / d_val
            if d_val > 0:
                t_out = min(t_out, t)
            else:
                t_in = max(t_in, t)
            if t_in > t_out + 1e-10:
                return 0.0
    if t_out <= t_in:
        return 0.0
    return max(0.0, min(1.0, t_out) - max(0.0, t_in)) * seg_len


def _sipe_clipped_length(poly: np.ndarray, sipe: Sipe) -> float:
    """Length of the sipe line that actually lies inside the polygon."""
    pts = [np.asarray(p, dtype=float) for p in sipe.polyline()]
    if len(pts) < 2:
        return 0.0
    return sum(_clipped_seg_len(a, b, poly) for a, b in zip(pts[:-1], pts[1:]))


def net_contact_area(verts: np.ndarray, sipes: Sequence[Sipe]) -> float:
    """Gross plan area minus the sipe slot areas at the tread surface.

    Net = gross - sum(clipped sipe length * slot width).  Only sipes with both
    positive depth and positive width remove material.
    """
    v = np.asarray(verts, dtype=float)
    props = polygon_props(v)
    if props is None:
        return 0.0
    slot = sum(
        _sipe_clipped_length(v, s) * s.width
        for s in sipes
        if s.depth > 0 and s.width > 0
    )
    return max(props.area - slot, 0.0)


# ---------------------------------------------------------------------------
# the model -- ports of beamKMatrix / effectiveK / computeKz
# ---------------------------------------------------------------------------
def beam_k_matrix(
    area: float, ixx: float, iyy: float, ixy: float, length: float, e: float, g: float, mode: Mode
) -> tuple[float, float, float]:
    """Stiffness matrix (Kxx, Kyy, Kxy) of one prismatic sub-block, N/mm."""
    if area <= 0 or length <= 0 or ixx <= 0 or iyy <= 0:
        return 0.0, 0.0, 0.0
    det_i = ixx * iyy - ixy * ixy
    if det_i <= 1e-15:
        return 0.0, 0.0, 0.0
    cb = (length**3) / (12.0 * e) if mode == "parallel" else (length**3) / (3.0 * e)
    cs = length / (g * area) if mode == "parallel" else (6.0 * length) / (5.0 * g * area)
    cxx = cb * ixx / det_i + cs
    cyy = cb * iyy / det_i + cs
    cxy = -cb * ixy / det_i
    det_c = cxx * cyy - cxy * cxy
    if det_c <= 1e-18:
        return 0.0, 0.0, 0.0
    return cyy / det_c, cxx / det_c, -cxy / det_c


def effective_k(
    verts: Sequence[Sequence[float]],
    nsd: float,
    e: float,
    nu: float,
    draft: float = 0.0,
    mode: Mode = "parallel",
    sipes: Sequence[Sipe] = (),
    n_slices: int = 40,
    sipe_model: str = "layered",
) -> tuple[float, float, float, int]:
    """Shear stiffness (Kx, Ky, Kxy, n_sub_blocks) of one block, N/mm."""
    g = calc_g(e, nu)
    v = np.asarray(verts, dtype=float)
    if nsd <= 0 or e <= 0 or g <= 0 or v.shape[0] < 3:
        return 0.0, 0.0, 0.0, 1

    has_draft = bool(draft) and abs(draft) > 1e-12

    def poly_at(z: float) -> np.ndarray:
        return offset_poly(v, draft, nsd - z) if has_draft else v

    if sipes and sipe_model == "continuous":
        return _effective_k_continuous(v, poly_at, nsd, e, g, mode, sipes, n_slices)

    # --- no sipes: Castigliano slice integration over the tapered beam ---
    if not sipes:
        dz = nsd / n_slices
        cxx_b = cyy_b = cxy_b = sh = 0.0
        n_valid = 0
        for i in range(n_slices):
            z = (i + 0.5) * dz
            p = polygon_props(poly_at(z))
            if p is None or p.area < 1e-9:
                continue
            det_i = p.ixx * p.iyy - p.ixy * p.ixy
            if det_i <= 1e-15:
                continue
            w = (z - nsd / 2.0) ** 2 if mode == "parallel" else (nsd - z) ** 2
            cxx_b += w * p.ixx / det_i * dz
            cyy_b += w * p.iyy / det_i * dz
            cxy_b += w * (-p.ixy) / det_i * dz
            sh += dz / p.area
            n_valid += 1
        if n_valid == 0:
            return 0.0, 0.0, 0.0, 1
        shear_factor = 1.0 if mode == "parallel" else 6.0 / 5.0
        cxx = cxx_b / e + shear_factor * sh / g
        cyy = cyy_b / e + shear_factor * sh / g
        cxy = cxy_b / e
        det_c = cxx * cyy - cxy * cxy
        if det_c <= 1e-18:
            return 0.0, 0.0, 0.0, 1
        kx = cyy / det_c
        ky = cxx / det_c
        kxy = -cxy / det_c
        if abs(kxy) < 1e-6 * max(kx, ky):
            kxy = 0.0
        return kx, ky, kxy, 1

    # --- with sipes: sub-blocks in parallel within a layer, layers in series ---
    transitions = {0.0, nsd}
    for s in sipes:
        h = nsd - s.depth
        if 0.0 < h < nsd:
            transitions.add(h)
    trans = sorted(transitions)

    ctot = [0.0, 0.0, 0.0]
    any_valid = False
    n_subs = 1
    for z_bot, z_top in zip(trans[:-1], trans[1:]):
        lh = z_top - z_bot
        if lh < 1e-6:
            continue
        active = [s for s in sipes if s.depth >= (nsd - z_bot) - 1e-6]
        subs = [poly_at(0.5 * (z_bot + z_top))]
        for s in active:
            nxt: list[np.ndarray] = []
            for sp in subs:
                nxt.extend(_split_polygon_by_sipe(sp, s))
            subs = nxt
        subs = [sp for sp in subs if (pp := polygon_props(sp)) is not None and pp.area > 1.0]
        n_subs = max(n_subs, len(subs))

        klayer = [0.0, 0.0, 0.0]
        for sp in subs:
            p = polygon_props(sp)
            if p is None or p.area < 1e-9:
                continue
            kxx, kyy, kxy = beam_k_matrix(p.area, p.ixx, p.iyy, p.ixy, lh, e, g, mode)
            klayer[0] += kxx
            klayer[1] += kyy
            klayer[2] += kxy
        clayer = invert_sym2((klayer[0], klayer[1], klayer[2]))
        if clayer[0] > 0 and clayer[1] > 0:
            ctot[0] += clayer[0]
            ctot[1] += clayer[1]
            ctot[2] += clayer[2]
            any_valid = True

    if not any_valid:
        return 0.0, 0.0, 0.0, n_subs
    kx, ky, kxy = invert_sym2((ctot[0], ctot[1], ctot[2]))
    if abs(kxy) < 1e-6 * max(kx, ky):
        kxy = 0.0
    return kx, ky, kxy, n_subs


def _effective_k_continuous(
    verts: np.ndarray,
    poly_at,
    nsd: float,
    e: float,
    g: float,
    mode: Mode,
    sipes: Sequence[Sipe],
    n_slices: int,
) -> tuple[float, float, float, int]:
    """Siped block as one beam of height-varying section (no layer stacking).

    At height ``z`` the sipes that reach that high split the section into
    sub-blocks.  Above a sipe root those sub-blocks bend as independent parallel
    springs, so their second moments and areas **add**; below it the section is
    whole.  The moment-weighted compliance is then integrated over the whole
    height in one pass -- the same integral the unsiped path evaluates:

        C_bend = (1/E) int w(z) . I_eff(z)^-1 dz
        C_shear = kappa/G int dz / A_eff(z)

    Because there is only ever one beam, no artificial zero-rotation constraint
    is introduced at a sipe root.  That is the whole difference from the
    ``layered`` model, and it is what makes a zero-depth sipe an exact no-op and
    stiffness fall monotonically with sipe depth.

    The section only changes where a sipe root sits, so the split is computed
    once per distinct set of active sipes rather than once per slice.
    """
    dz = nsd / n_slices
    cxx_b = cyy_b = cxy_b = sh = 0.0
    n_valid = 0
    n_subs = 1
    cache: dict[tuple[int, ...], None] = {}
    split_cache: dict[tuple[int, ...], list[np.ndarray]] = {}

    for i in range(n_slices):
        z = (i + 0.5) * dz
        # A sipe is present at height z when it reaches down at least to z,
        # i.e. its root (nsd - depth) lies below z.
        key = tuple(j for j, s in enumerate(sipes) if s.depth >= (nsd - z) - 1e-12)
        base = poly_at(z)
        if key not in split_cache or _has_draft_variation(poly_at, nsd):
            subs = [base]
            for j in key:
                nxt: list[np.ndarray] = []
                for sp in subs:
                    nxt.extend(_split_polygon_by_sipe(sp, sipes[j]))
                subs = nxt
            subs = [sp for sp in subs if (pp := polygon_props(sp)) is not None and pp.area > 1e-9]
            if not _has_draft_variation(poly_at, nsd):
                split_cache[key] = subs
        else:
            subs = split_cache[key]
        if not subs:
            continue
        n_subs = max(n_subs, len(subs))

        # Parallel springs: second moments and areas add.
        ixx = iyy = ixy = area = 0.0
        for sp in subs:
            p = polygon_props(sp)
            if p is None or p.area < 1e-12:
                continue
            ixx += p.ixx
            iyy += p.iyy
            ixy += p.ixy
            area += p.area
        if area <= 1e-12:
            continue
        det_i = ixx * iyy - ixy * ixy
        if det_i <= 1e-15:
            continue
        w = (z - nsd / 2.0) ** 2 if mode == "parallel" else (nsd - z) ** 2
        cxx_b += w * ixx / det_i * dz
        cyy_b += w * iyy / det_i * dz
        cxy_b += w * (-ixy) / det_i * dz
        sh += dz / area
        n_valid += 1

    if n_valid == 0:
        return 0.0, 0.0, 0.0, n_subs
    shear_factor = 1.0 if mode == "parallel" else 6.0 / 5.0
    cxx = cxx_b / e + shear_factor * sh / g
    cyy = cyy_b / e + shear_factor * sh / g
    cxy = cxy_b / e
    det_c = cxx * cyy - cxy * cxy
    if det_c <= 1e-18:
        return 0.0, 0.0, 0.0, n_subs
    kx = cyy / det_c
    ky = cxx / det_c
    kxy = -cxy / det_c
    if abs(kxy) < 1e-6 * max(kx, ky):
        kxy = 0.0
    return kx, ky, kxy, n_subs


def _has_draft_variation(poly_at, nsd: float) -> bool:
    """True when the section changes with height, so splits cannot be cached."""
    a, b = poly_at(0.0), poly_at(nsd)
    return a.shape != b.shape or not np.allclose(a, b)


def _split_polygon_by_sipe(poly: np.ndarray, sipe: Sipe) -> list[np.ndarray]:
    pts = sipe.polyline()
    if len(pts) < 2:
        return [poly]
    if len(pts) == 2:
        return _split_by_line(poly, pts[0], pts[1], sipe.width)
    pieces = [poly]
    for a, b in zip(pts[:-1], pts[1:]):
        nxt: list[np.ndarray] = []
        for p in pieces:
            nxt.extend(_split_by_line(p, a, b, sipe.width))
        pieces = nxt
    return pieces


def compute_kz(
    verts: Sequence[Sequence[float]],
    nsd: float,
    e: float,
    gent_k: float,
    sipes: Sequence[Sipe] = (),
    bulk_modulus: float = 1100.0,
) -> tuple[float, float, float, float]:
    """Vertical compression stiffness: (Kz, shape factor S, E_eff, A_net)."""
    v = np.asarray(verts, dtype=float)
    props = polygon_props(v)
    if props is None:
        return 0.0, 0.0, 0.0, 0.0
    nsd = max(nsd, 0.1)
    e = max(e, 0.01)
    a_net = net_contact_area(v, sipes)
    s = a_net / (nsd * max(props.perimeter, 1e-9))
    e_eff_uncorr = e * (1.0 + 2.0 * gent_k * s * s)
    e_eff = e_eff_uncorr / (1.0 + e_eff_uncorr / bulk_modulus)
    return e_eff * a_net / nsd, s, e_eff, a_net


# ---------------------------------------------------------------------------
def block_stiffness(block: Block, params: StiffnessParams = DEFAULT_PARAMS) -> BlockStiffness:
    """Full stiffness of one block.  Per-block Shore overrides the pattern default."""
    verts = np.asarray(block.polygon, dtype=float)
    props = polygon_props(verts)
    if props is None or block.height <= 0:
        return BlockStiffness(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    if block.shore_a is not None:
        e = shore_e(block.shore_a)
        k = shore_k(block.shore_a)
    else:
        e = params.youngs()
        k = params.gent_k()

    sipes = block.effective_sipes()
    kx, ky, kxy, n_subs = effective_k(
        verts, block.height, e, params.poisson, block.draft_angle,
        params.mode, sipes, params.n_slices, params.sipe_model,
    )
    kz, s, e_eff, a_net = compute_kz(verts, block.height, e, k, sipes, params.bulk_modulus)

    lx, ly = block.extents()
    return BlockStiffness(
        kx=float(kx),
        ky=float(ky),
        kxy=float(kxy),
        kz=float(kz),
        area=float(props.area),
        net_area=float(a_net),
        perimeter=float(props.perimeter),
        shape_factor=float(s),
        e_eff=float(e_eff),
        slenderness=float(block.height / max(min(lx, ly), 1e-6)),
        n_sub_blocks=int(n_subs),
    )


def stiffness_table(blocks: Sequence[Block], params: StiffnessParams = DEFAULT_PARAMS) -> dict[str, BlockStiffness]:
    return {b.id: block_stiffness(b, params) for b in blocks}
