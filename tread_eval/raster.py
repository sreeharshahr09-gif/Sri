"""Rasterisation of the unwrapped pattern onto a fine grid.

The grid is the common currency of the pipeline: once the pattern is a stack of
weight maps, every contact-patch quantity (area, stiffness, block count, zone
balance, centroid) becomes the same circular cross-correlation along the
circumferential axis, which :mod:`tread_eval.sweep` does with one FFT per map.

Grid convention
---------------
Rows are lateral (``y``, from ``-W/2`` to ``+W/2``), columns are circumferential
(``x``, from 0 to the circumference, wrapping).  Pixel centres, not corners.
Target resolution is 0.2-0.5 mm so sipes and narrow grooves survive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .schema import Block, Pattern, ZONES, zone_bounds
from .stiffness import BlockStiffness, StiffnessParams, DEFAULT_PARAMS, stiffness_table


@dataclass
class Grid:
    """Raster geometry."""

    nx: int
    ny: int
    dx: float  # mm per column
    dy: float  # mm per row
    circumference: float
    tread_width: float

    @property
    def x(self) -> np.ndarray:
        return (np.arange(self.nx) + 0.5) * self.dx

    @property
    def y(self) -> np.ndarray:
        return -self.tread_width / 2.0 + (np.arange(self.ny) + 0.5) * self.dy

    def x_rel(self) -> np.ndarray:
        """Signed offset of each column from column 0, wrapped to +-C/2.

        Used to place a contact-patch kernel at circumferential origin 0 so the
        correlation output index maps straight onto rotation angle.
        """
        idx = np.arange(self.nx)
        return ((idx + self.nx // 2) % self.nx - self.nx // 2) * self.dx

    @property
    def pixel_area(self) -> float:
        return self.dx * self.dy

    @property
    def theta_deg(self) -> np.ndarray:
        return np.arange(self.nx) * (360.0 / self.nx)


def make_grid(pattern: Pattern, resolution: float = 0.35) -> Grid:
    # A non-positive resolution used to fall through the max() below and quietly
    # produce a 64-column grid -- a garbage-resolution run that looked normal.
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError(f"raster resolution must be a positive number of mm, got {resolution!r}")
    if not math.isfinite(pattern.tyre_circumference) or pattern.tyre_circumference <= 0:
        raise ValueError(f"tyre circumference must be positive, got {pattern.tyre_circumference!r}")
    if not math.isfinite(pattern.tread_width) or pattern.tread_width <= 0:
        raise ValueError(f"tread width must be positive, got {pattern.tread_width!r}")
    nx = max(64, int(round(pattern.tyre_circumference / resolution)))
    ny = max(16, int(round(pattern.tread_width / resolution)))
    return Grid(
        nx=nx,
        ny=ny,
        dx=pattern.tyre_circumference / nx,
        dy=pattern.tread_width / ny,
        circumference=pattern.tyre_circumference,
        tread_width=pattern.tread_width,
    )


# ---------------------------------------------------------------------------
# polygon handling
# ---------------------------------------------------------------------------
def _clip_halfplane(poly: list[tuple[float, float]], keep_below: bool, bound: float) -> list[tuple[float, float]]:
    """Sutherland-Hodgman clip against ``x <= bound`` or ``x >= bound``."""
    if not poly:
        return []
    out: list[tuple[float, float]] = []
    n = len(poly)
    inside = (lambda pt: pt[0] <= bound) if keep_below else (lambda pt: pt[0] >= bound)
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        cur_in, nxt_in = inside(cur), inside(nxt)
        if cur_in:
            out.append(cur)
        if cur_in != nxt_in:
            dx = nxt[0] - cur[0]
            t = 0.0 if abs(dx) < 1e-12 else (bound - cur[0]) / dx
            out.append((bound, cur[1] + t * (nxt[1] - cur[1])))
    return out


def split_at_seam(poly: list[tuple[float, float]], circumference: float) -> list[list[tuple[float, float]]]:
    """Split a polygon that crosses ``x = 0`` or ``x = C`` into wrapped pieces.

    Returned pieces all lie inside ``[0, C]``.  Blocks are allowed to straddle
    the seam in the schema, so this is where that promise is kept.
    """
    xs = [p[0] for p in poly]
    if min(xs) >= 0.0 and max(xs) <= circumference:
        return [poly]

    pieces: list[list[tuple[float, float]]] = []
    # Shift the polygon by whole circumferences until it is fully to the right
    # of 0, then peel off one circumference-wide slab at a time.
    shift = -np.floor(min(xs) / circumference) * circumference
    work = [(x + shift, y) for x, y in poly]
    xs = [p[0] for p in work]
    k = 0
    while k < 8 and max(p[0] for p in work) > 1e-9:
        lo, hi = 0.0, circumference
        piece = _clip_halfplane(_clip_halfplane(work, True, hi), False, lo)
        if len(piece) >= 3:
            pieces.append(piece)
        work = [(x - circumference, y) for x, y in work]
        if max(p[0] for p in work) <= 1e-9:
            break
        k += 1
    return [p for p in pieces if len(p) >= 3]


def rasterise_polygon(poly: list[tuple[float, float]], grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Scanline fill of one polygon.

    Returns ``(row_indices, col_index_pairs)`` implicitly as a flat list of
    (row, col_start, col_end) spans, expressed as two arrays: rows and a
    ``(n, 2)`` array of half-open column ranges.  Working in spans rather than
    pixel lists keeps the accumulation cheap for a few hundred blocks on a
    multi-megapixel grid.
    """
    p = np.asarray(poly, dtype=float)
    y_min, y_max = p[:, 1].min(), p[:, 1].max()
    y0 = -grid.tread_width / 2.0
    r_lo = max(0, int(np.floor((y_min - y0) / grid.dy - 0.5)))
    r_hi = min(grid.ny - 1, int(np.ceil((y_max - y0) / grid.dy - 0.5)))
    if r_hi < r_lo:
        return np.empty(0, dtype=np.int32), np.empty((0, 2), dtype=np.int32)

    rows = np.arange(r_lo, r_hi + 1)
    yc = y0 + (rows + 0.5) * grid.dy  # scanline y positions

    ya = p[:, 1][:, None]
    yb = np.roll(p[:, 1], -1)[:, None]
    xa = p[:, 0][:, None]
    xb = np.roll(p[:, 0], -1)[:, None]

    # Half-open edge rule (ya <= y < yb or yb <= y < ya) so shared edges between
    # touching blocks neither double-count nor leave gaps.
    lo = np.minimum(ya, yb)
    hi = np.maximum(ya, yb)
    crosses = (yc[None, :] >= lo) & (yc[None, :] < hi) & (ya != yb)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (yc[None, :] - ya) / (yb - ya)
        xint = xa + t * (xb - xa)
    xint = np.where(crosses, xint, np.inf)

    xint = np.sort(xint, axis=0)
    n_cross = crosses.sum(axis=0)
    max_pairs = int(n_cross.max()) // 2 if n_cross.size else 0
    if max_pairs == 0:
        return np.empty(0, dtype=np.int32), np.empty((0, 2), dtype=np.int32)

    span_rows: list[np.ndarray] = []
    span_cols: list[np.ndarray] = []
    for k in range(max_pairs):
        x_enter = xint[2 * k]
        x_exit = xint[2 * k + 1]
        valid = np.isfinite(x_exit)
        if not valid.any():
            continue
        # Pixel-centre sampling: a column is inside when its centre is covered.
        c0 = np.ceil((x_enter[valid] / grid.dx) - 0.5).astype(np.int64)
        c1 = np.ceil((x_exit[valid] / grid.dx) - 0.5).astype(np.int64)
        c0 = np.clip(c0, 0, grid.nx)
        c1 = np.clip(c1, 0, grid.nx)
        keep = c1 > c0
        if not keep.any():
            continue
        span_rows.append(rows[valid][keep])
        span_cols.append(np.column_stack([c0[keep], c1[keep]]))

    if not span_rows:
        return np.empty(0, dtype=np.int32), np.empty((0, 2), dtype=np.int32)
    return (
        np.concatenate(span_rows).astype(np.int32),
        np.concatenate(span_cols, axis=0).astype(np.int32),
    )


# ---------------------------------------------------------------------------
# weight maps
# ---------------------------------------------------------------------------
@dataclass
class RasterPack:
    """All the per-pixel weight maps the sweep needs.

    Every map is ``(ny, nx)`` float32.  Correlating any of them with the contact
    patch kernel yields the corresponding in-patch aggregate as a function of
    rotation angle, which is why they are all built here in one pass.
    """

    grid: Grid
    land: np.ndarray  # 1 inside rubber (area weight applied separately)
    area: np.ndarray  # pixel area, curvature-corrected if enabled
    kx: np.ndarray  # per-pixel Kx density: block Kx spread over its pixels
    ky: np.ndarray
    kz: np.ndarray  # per-pixel Kz density (vertical compression)
    block_frac: np.ndarray  # 1/npix of the owning block -> sums to block count
    zone_area: dict[str, np.ndarray]
    y_moment: np.ndarray  # area * y, for the in-patch lateral centroid
    labels: np.ndarray  # int32 block index per pixel, -1 for void
    block_pixel_count: np.ndarray
    stiffness: dict[str, BlockStiffness]
    lateral_weight: np.ndarray  # per-row area multiplier (curvature correction)
    curvature_correction: bool

    def total_land_area(self) -> float:
        return float(self.area.sum())


def rasterise(
    pattern: Pattern,
    grid: Grid | None = None,
    resolution: float = 0.35,
    curvature_correction: bool = False,
    stiffness_params: StiffnessParams = DEFAULT_PARAMS,
) -> RasterPack:
    """Burn a pattern into the weight maps.

    ``curvature_correction`` is OFF by default, per Section 8 of the brief: a
    mould DXF of one's own tyre is already correctly flattened, so re-applying an
    arc-length factor would double-count.  Turn it on for digitised competitor
    patterns that were captured flat.
    """
    grid = grid or make_grid(pattern, resolution)
    ny, nx = grid.ny, grid.nx

    crown = pattern.crown()
    if curvature_correction:
        lateral_weight = np.asarray(crown.arc_length_factor(grid.y), dtype=np.float64)
    else:
        lateral_weight = np.ones(ny, dtype=np.float64)
    row_area = (lateral_weight * grid.pixel_area).astype(np.float32)

    land = np.zeros((ny, nx), dtype=np.float32)
    labels = np.full((ny, nx), -1, dtype=np.int32)

    stiff = stiffness_table(pattern.blocks, stiffness_params)
    block_pixel_count = np.zeros(len(pattern.blocks), dtype=np.int64)

    for idx, block in enumerate(pattern.blocks):
        for piece in split_at_seam(block.polygon, pattern.tyre_circumference):
            rows, cols = rasterise_polygon(piece, grid)
            for r, (c0, c1) in zip(rows, cols):
                land[r, c0:c1] = 1.0
                labels[r, c0:c1] = idx
    # Count pixels per block from the labels, so overlaps (shared edges) are
    # attributed exactly once and the per-block densities below stay consistent.
    flat = labels.ravel()
    owned = flat[flat >= 0]
    counts = np.bincount(owned, minlength=len(pattern.blocks))
    block_pixel_count[: len(counts)] = counts

    area = land * row_area[:, None]

    # Per-pixel densities: dividing a block quantity by its pixel count means the
    # correlation returns sum_b (fraction of block b inside patch) * quantity_b.
    kx_per_px = np.zeros(len(pattern.blocks), dtype=np.float32)
    ky_per_px = np.zeros(len(pattern.blocks), dtype=np.float32)
    kz_per_px = np.zeros(len(pattern.blocks), dtype=np.float32)
    frac_per_px = np.zeros(len(pattern.blocks), dtype=np.float32)
    for idx, block in enumerate(pattern.blocks):
        n = block_pixel_count[idx]
        if n <= 0:
            continue
        s = stiff[block.id]
        kx_per_px[idx] = s.kx / n
        ky_per_px[idx] = s.ky / n
        kz_per_px[idx] = s.kz / n
        frac_per_px[idx] = 1.0 / n

    safe = np.maximum(labels, 0)
    inside = labels >= 0
    kx = np.where(inside, kx_per_px[safe], 0.0).astype(np.float32)
    ky = np.where(inside, ky_per_px[safe], 0.0).astype(np.float32)
    kz = np.where(inside, kz_per_px[safe], 0.0).astype(np.float32)
    block_frac = np.where(inside, frac_per_px[safe], 0.0).astype(np.float32)

    # Zone area is a question about *where the rubber is*, so it is split by the
    # lateral position of each pixel, not by which block owns it. Splitting by
    # block would let a wide block whose centroid sits in the centre band
    # contribute area out in the intermediate band -- which pushed the centre
    # zone's land ratio above 100% on a real pattern with large blocks.
    bounds = zone_bounds(pattern.tread_width)
    zone_area: dict[str, np.ndarray] = {}
    abs_y = np.abs(grid.y)
    for zone in ZONES:
        lo, hi = bounds[zone]
        rows = ((abs_y >= lo) & (abs_y < hi + 1e-9)).astype(np.float32)
        zone_area[zone] = (land * (row_area * rows)[:, None]).astype(np.float32)

    y_moment = (area * grid.y[:, None]).astype(np.float32)

    return RasterPack(
        grid=grid,
        land=land,
        area=area,
        kx=kx,
        ky=ky,
        kz=kz,
        block_frac=block_frac,
        zone_area=zone_area,
        y_moment=y_moment,
        labels=labels,
        block_pixel_count=block_pixel_count,
        stiffness=stiff,
        lateral_weight=lateral_weight,
        curvature_correction=curvature_correction,
    )
