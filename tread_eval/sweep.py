"""The theta x gamma sweep: slide the contact patch around the tyre, at each lean.

The whole sweep is circular cross-correlation along the circumferential axis.
For any per-pixel weight map ``W`` and contact-patch kernel ``K``,

    c[j] = sum_{x,y} W[y, x] * K[y, x - j]
         = irfft( rfft(W, axis=1) * conj(rfft(K, axis=1)) ).sum(axis=0)

gives the in-patch aggregate of ``W`` at every one of the ``nx`` rotation
positions in a single FFT pair, instead of one masked sum per angle.  Because the
lean sweep multiplies the angular work by the number of lean angles, this is the
difference between seconds and minutes.

Index ``j`` is the column the patch centre sits on, so ``theta = 360 * j / nx``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .contact_patch import CPParams, ContactPatch, DEFAULT_CP_PARAMS, contact_patch
from .raster import Grid, RasterPack
from .schema import Pattern, ZONES


@dataclass
class LeanResult:
    """Everything computed at one lean angle, as functions of rotation angle."""

    gamma_deg: float
    patch: ContactPatch
    theta_deg: np.ndarray
    contact_area: np.ndarray  # mm^2 of rubber inside the patch
    land_ratio: np.ndarray  # contact_area / patch area
    kx: np.ndarray  # N/mm, summed over blocks in contact
    ky: np.ndarray
    block_count: np.ndarray  # effective (sum of per-block area fractions)
    block_count_discrete: np.ndarray  # blocks with >50% of their area inside
    theta_discrete: np.ndarray
    centroid_y: np.ndarray  # lateral centroid of the rubber in contact, mm
    zone_area: dict[str, np.ndarray]
    pressure_area: np.ndarray  # pressure-weighted contact area, N (sum p*dA)
    patch_area: float  # mm^2, clipped patch area
    patch_perimeter: float
    patch_load: float  # N, integral of the pressure field
    shape: dict[str, float] = field(default_factory=dict)

    def as_dict(self, stride: int = 1) -> dict:
        s = slice(None, None, stride)
        return {
            "gamma_deg": self.gamma_deg,
            "theta_deg": self.theta_deg[s].tolist(),
            "contact_area": self.contact_area[s].tolist(),
            "land_ratio": self.land_ratio[s].tolist(),
            "kx": self.kx[s].tolist(),
            "ky": self.ky[s].tolist(),
            "block_count": self.block_count[s].tolist(),
            "block_count_discrete": self.block_count_discrete.tolist(),
            "theta_discrete": self.theta_discrete.tolist(),
            "centroid_y": self.centroid_y[s].tolist(),
            "zone_area": {z: self.zone_area[z][s].tolist() for z in self.zone_area},
            "pressure_area": self.pressure_area[s].tolist(),
            "patch_area": self.patch_area,
            "patch_perimeter": self.patch_perimeter,
            "patch_load": self.patch_load,
            "patch": {
                "y_center": self.patch.y_center,
                "a": self.patch.a,
                "b": self.patch.b,
                "delta": self.patch.delta,
                "peak_pressure": self.patch.peak_pressure,
                "r_eff": self.patch.r_eff,
                "r_lat": self.patch.r_lat,
                "normal_load": self.patch.normal_load,
                "path_radius": (None if not np.isfinite(self.patch.path_radius) else self.patch.path_radius),
                "clipped": bool(self.patch.clipped),
                "outline": self.patch.outline(121).tolist(),
                "source": self.patch.source,
            },
            "shape": self.shape,
        }


def _correlate(map_fft: np.ndarray, kernel_fft: np.ndarray, nx: int) -> np.ndarray:
    """Sum over rows of the per-row circular cross-correlation."""
    acc = np.einsum("yf,yf->f", map_fft, kernel_fft.conj())
    return np.fft.irfft(acc, n=nx)


class MapFFTCache:
    """Forward transforms of the weight maps, computed once for the whole sweep.

    The weight maps are a property of the pattern, not of the lean angle, so
    transforming them inside the lean loop would redo identical work at every
    gamma.  Only the patch kernel changes with lean.
    """

    def __init__(self, pack: RasterPack):
        self.pack = pack
        self._cache: dict[str, np.ndarray] = {}

    def get(self, key: str, weight_map: np.ndarray) -> np.ndarray:
        if key not in self._cache:
            self._cache[key] = np.fft.rfft(weight_map.astype(np.float32), axis=1)
        return self._cache[key]


def _patch_kernels(patch: ContactPatch, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Binary and pressure kernels, centred on column 0 with wraparound."""
    binary, pressure = patch.masks(grid.x_rel(), grid.y)
    return binary.astype(np.float32), pressure.astype(np.float32)


def _polygon_perimeter(pts: np.ndarray) -> float:
    """Perimeter of a closed polygon.

    Used in preference to counting exposed mask pixel edges: the staircase of a
    rasterised ellipse overstates its perimeter by up to 4/pi, which would make
    the compactness metric meaningless as an absolute number.  The patch outline
    is known analytically (including the tread-edge clip), so use it.
    """
    d = np.diff(np.vstack([pts, pts[:1]]), axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _discrete_block_count(
    pack: RasterPack, patch: ContactPatch, n_samples: int = 360, threshold: float = 0.5
) -> tuple[np.ndarray, np.ndarray]:
    """Number of blocks with more than ``threshold`` of their area in the patch.

    Done by windowed label counting rather than FFT, because "is this block
    mostly in contact" is a per-block question the correlation cannot answer.
    Only the columns the patch actually spans are touched, so this stays cheap.
    """
    grid = pack.grid
    half_cols = int(np.ceil(patch.a / grid.dx)) + 1
    width = min(grid.nx, 2 * half_cols + 1)
    offsets = (np.arange(width) - width // 2) * grid.dx
    yy = grid.y[:, None]
    inside = patch.contains(offsets[None, :], yy)

    n_blocks = len(pack.block_pixel_count)
    thetas = np.linspace(0.0, 360.0, n_samples, endpoint=False)
    counts = np.zeros(n_samples, dtype=np.float64)
    cols = np.arange(width) - width // 2
    for i, th in enumerate(thetas):
        j = int(round(th / 360.0 * grid.nx))
        take = (cols + j) % grid.nx
        window = pack.labels[:, take]
        sel = window[inside & (window >= 0)]
        if sel.size == 0:
            continue
        hits = np.bincount(sel, minlength=n_blocks)
        frac = hits / np.maximum(pack.block_pixel_count, 1)
        counts[i] = float(np.count_nonzero(frac > threshold))
    return thetas, counts


def _shape_metrics(patch: ContactPatch, binary: np.ndarray, pressure: np.ndarray, grid: Grid) -> dict[str, float]:
    """Metrics of the patch itself -- independent of where it sits on the pattern."""
    px = grid.pixel_area
    area = float(binary.sum()) * px
    # Deduplicate the clamped run along the tread edge before measuring, so a
    # clipped patch does not accumulate perimeter from coincident points.
    outline = patch.outline(721)
    keep = np.concatenate([[True], np.hypot(*(np.diff(outline, axis=0).T)) > 1e-9])
    per = _polygon_perimeter(outline[keep])
    x_rel = grid.x_rel()
    xx = np.broadcast_to(x_rel[None, :], binary.shape)
    yy = np.broadcast_to(grid.y[:, None], binary.shape)

    w = pressure.astype(np.float64)
    tot = float(w.sum())

    def _skew(vals: np.ndarray) -> float:
        if tot <= 0:
            return 0.0
        mu = float((w * vals).sum()) / tot
        var = float((w * (vals - mu) ** 2).sum()) / tot
        if var <= 1e-12:
            return 0.0
        return float((w * (vals - mu) ** 3).sum()) / tot / var**1.5

    occupied_cols = np.flatnonzero(binary.any(axis=0))
    occupied_rows = np.flatnonzero(binary.any(axis=1))
    length = (np.ptp(x_rel[occupied_cols]) + grid.dx) if occupied_cols.size else 0.0
    width = (np.ptp(grid.y[occupied_rows]) + grid.dy) if occupied_rows.size else 0.0

    return {
        "area_mm2": area,
        "perimeter_mm": per,
        "compactness": (per * per / area) if area > 0 else float("nan"),
        "aspect_ratio": (length / width) if width > 0 else float("nan"),
        "length_mm": float(length),
        "width_mm": float(width),
        "pressure_skew_x": _skew(xx),
        "pressure_skew_y": _skew(yy),
        "peak_pressure_mpa": float(patch.peak_pressure),
        "mean_pressure_mpa": (tot * px / area) if area > 0 else float("nan"),
        "y_center_mm": float(patch.y_center),
        "clipped": float(bool(patch.clipped)),
    }


def sweep_lean(
    pattern: Pattern,
    pack: RasterPack,
    gamma_deg: float,
    cp_params: CPParams = DEFAULT_CP_PARAMS,
    discrete_samples: int = 360,
    patch_override: ContactPatch | None = None,
    cache: "MapFFTCache | None" = None,
) -> LeanResult:
    """Full 360 deg rotation sweep at one lean angle."""
    grid = pack.grid
    cache = cache or MapFFTCache(pack)
    patch = patch_override or contact_patch(gamma_deg, pattern.crown(), cp_params, pattern.tread_width)
    binary, pressure = _patch_kernels(patch, grid)

    kb = np.fft.rfft(binary, axis=1)
    kp = np.fft.rfft(pressure, axis=1)

    def corr(key: str, weight_map: np.ndarray, kernel_fft: np.ndarray) -> np.ndarray:
        return _correlate(cache.get(key, weight_map), kernel_fft, grid.nx)

    contact_area = corr("area", pack.area, kb)
    kx = corr("kx", pack.kx, kb)
    ky = corr("ky", pack.ky, kb)
    block_count = corr("block_frac", pack.block_frac, kb)
    y_moment = corr("y_moment", pack.y_moment, kb)
    pressure_area = corr("area", pack.area, kp)
    zone_area = {z: corr(f"zone_{z}", pack.zone_area[z], kb) for z in ZONES}

    shape = _shape_metrics(patch, binary, pressure, grid)
    patch_area = shape["area_mm2"]

    contact_area = np.maximum(contact_area, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        centroid_y = np.where(contact_area > 1e-9, y_moment / np.maximum(contact_area, 1e-9), patch.y_center)
    land_ratio = contact_area / patch_area if patch_area > 0 else np.zeros_like(contact_area)

    theta_d, count_d = _discrete_block_count(pack, patch, discrete_samples)

    return LeanResult(
        gamma_deg=gamma_deg,
        patch=patch,
        theta_deg=grid.theta_deg,
        contact_area=contact_area,
        land_ratio=land_ratio,
        kx=np.maximum(kx, 0.0),
        ky=np.maximum(ky, 0.0),
        block_count=np.maximum(block_count, 0.0),
        block_count_discrete=count_d,
        theta_discrete=theta_d,
        centroid_y=centroid_y,
        zone_area={z: np.maximum(v, 0.0) for z, v in zone_area.items()},
        pressure_area=np.maximum(pressure_area, 0.0),
        patch_area=patch_area,
        patch_perimeter=shape["perimeter_mm"],
        patch_load=float(pressure.sum()) * grid.pixel_area,
        shape=shape,
    )


def sweep(
    pattern: Pattern,
    pack: RasterPack,
    lean_angles: list[float],
    cp_params: CPParams = DEFAULT_CP_PARAMS,
    discrete_samples: int = 360,
) -> list[LeanResult]:
    cache = MapFFTCache(pack)
    return [sweep_lean(pattern, pack, g, cp_params, discrete_samples, cache=cache) for g in lean_angles]
