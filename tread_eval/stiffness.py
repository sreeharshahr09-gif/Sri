"""Per-block tread stiffness -- SWAP POINT 2 of the brief.

Everything in this module is a PLACEHOLDER.  It exists so the pipeline has a
per-block ``(Kx, Ky)`` to aggregate over the contact patch; it is not claimed to
be physically calibrated.  The whole module is deliberately one function's worth
of physics behind one call, :func:`block_stiffness`, so the production swap is:

    replace the body of ``block_stiffness`` with the Winkler beam-mechanics model
    (Okonieski et al. 2003 framework -- the ``computeWinklerPressure()`` /
    ``effectiveK_winkler()`` logic from the existing HTML tread-block tool)

and nothing else in the pipeline changes.  To make that swap easy, the signature
takes the block plus a material/geometry parameter bundle and returns a small
result object, rather than a bare float.

Model actually implemented here
------------------------------
A block is treated as a short rubber column bonded top and bottom, loaded in
shear.  Two compliances add in series:

* shear of the bulk,      K_shear = G * A_eff / h,  stiffened by the shape factor
  S = A / (perimeter * h) as (1 + 2 k S^2) -- the standard bonded-rubber-pad
  correction, which is what makes a squat block much stiffer than a slender one;
* bending of the block as a cantilever, K_bend = 3 E I / h^3, with I taken about
  the axis normal to the loading direction.

Draft angle tapers the block, so the load-bearing top face is smaller than the
base -- captured through an effective area.  NSD (sipe density) is applied as a
compliance multiplier: sipes cut the block into thinner columns that shear more
easily, and they do so anisotropically, which is why ``nsd_x_factor`` and
``nsd_y_factor`` differ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .schema import Block


@dataclass(frozen=True)
class StiffnessParams:
    """Material and model constants.  PLACEHOLDER values, order-of-magnitude only."""

    shear_modulus: float = 1.8  # G, MPa (N/mm^2) -- typical tread compound
    poisson_stiffening: float = 1.0  # k in the (1 + 2 k S^2) shape-factor term
    youngs_modulus: float = 5.6  # E ~ 3G for near-incompressible rubber, MPa
    nsd_x_compliance: float = 0.55  # how strongly sipes soften circumferential shear
    nsd_y_compliance: float = 0.30  # ... and lateral shear (sipes run mostly lateral)
    max_shape_factor: float = 6.0  # clamp -- the pad formula runs away for very squat blocks


DEFAULT_PARAMS = StiffnessParams()


@dataclass(frozen=True)
class BlockStiffness:
    """Result bundle for one block."""

    kx: float  # N/mm, circumferential (braking/traction) shear stiffness
    ky: float  # N/mm, lateral (cornering) shear stiffness
    area: float  # mm^2, footprint area of the block at the tread surface
    shape_factor: float
    slenderness: float  # height / min lateral dimension -- wear proxy


def _second_moments(polygon: list[tuple[float, float]]) -> tuple[float, float]:
    """Second moments of area of the polygon about its own centroid, (Ixx, Iyy).

    ``Ixx`` resists bending that deflects the block in x (circumferential);
    ``Iyy`` resists deflection in y (lateral).
    """
    p = np.asarray(polygon, dtype=float)
    x, y = p[:, 0], p[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    a = 0.5 * cross.sum()
    if abs(a) < 1e-12:
        return 0.0, 0.0
    cx = ((x + x1) * cross).sum() / (6 * a)
    cy = ((y + y1) * cross).sum() / (6 * a)
    x, y = x - cx, y - cy
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    # Standard polygon second-moment formulae (about the centroid).
    i_about_y = float((cross * (x * x + x * x1 + x1 * x1)).sum() / 12.0)  # resists x-deflection
    i_about_x = float((cross * (y * y + y * y1 + y1 * y1)).sum() / 12.0)  # resists y-deflection
    return abs(i_about_y), abs(i_about_x)


def _perimeter(polygon: list[tuple[float, float]]) -> float:
    p = np.asarray(polygon, dtype=float)
    d = np.roll(p, -1, axis=0) - p
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def block_stiffness(block: Block, params: StiffnessParams = DEFAULT_PARAMS) -> BlockStiffness:
    """PLACEHOLDER per-block Kx / Ky.  <<< SWAP POINT: Winkler model goes here >>>

    Returns stiffnesses in N/mm for the block as a whole (not per unit area), so
    the contact-patch aggregate is a straight sum over the blocks in contact,
    weighted by how much of each block is inside the patch.
    """
    area = block.area()
    if area <= 0.0 or block.height <= 0.0:
        return BlockStiffness(0.0, 0.0, 0.0, 0.0, 0.0)

    h = block.height
    per = _perimeter(block.polygon)
    lx, ly = block.extents()

    # Draft angle shrinks the block toward the tread surface. Treat the taper as
    # a uniform inward offset of t = h * tan(draft) on every edge, and take the
    # load-bearing area as the mean of base and top faces.
    taper = h * math.tan(math.radians(max(0.0, block.draft_angle)))
    area_top = max(0.05 * area, area - per * taper + math.pi * taper * taper)
    area_eff = 0.5 * (area + area_top)

    # Shape factor: loaded area / force-free (bulging) area.
    shape_factor = min(area / max(per * h, 1e-6), params.max_shape_factor)
    bulk = params.shear_modulus * area_eff / h * (1.0 + 2.0 * params.poisson_stiffening * shape_factor**2)

    # Cantilever bending in series with the bulk shear.
    ixx, iyy = _second_moments(block.polygon)
    kbend_x = 3.0 * params.youngs_modulus * ixx / h**3
    kbend_y = 3.0 * params.youngs_modulus * iyy / h**3

    kx = 1.0 / (1.0 / bulk + 1.0 / max(kbend_x, 1e-9))
    ky = 1.0 / (1.0 / bulk + 1.0 / max(kbend_y, 1e-9))

    # Sipes / void density soften the block; lateral sipes soften circumferential
    # shear more than lateral shear, hence the two different coefficients.
    kx /= 1.0 + params.nsd_x_compliance * block.nsd
    ky /= 1.0 + params.nsd_y_compliance * block.nsd

    slenderness = h / max(min(lx, ly), 1e-6)
    return BlockStiffness(
        kx=float(kx),
        ky=float(ky),
        area=float(area),
        shape_factor=float(shape_factor),
        slenderness=float(slenderness),
    )


def stiffness_table(blocks: list[Block], params: StiffnessParams = DEFAULT_PARAMS) -> dict[str, BlockStiffness]:
    """Per-block stiffness for a whole pattern, keyed by block id."""
    return {b.id: block_stiffness(b, params) for b in blocks}
