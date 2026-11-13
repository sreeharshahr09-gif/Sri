"""Sidewall deflection analysis for a 2D laser profile scan: inflated vs loaded.

Data model
----------
The scanner logs two channels per sample:
  x : laser distance reading (mm). Smaller x = surface closer to the sensor,
      i.e. further axially outward. 300.15 (anything >= ``DROPOUT``) = no return.
  z : vertical stage position (mm).
The stage runs one way for the inflated scan, reverses, and runs back for the
loaded scan. Pass 1 = inflated, pass 2 = loaded.

Coordinates used in the analysis
--------------------------------
  u = x_band - x     outward offset of the surface relative to the rigid band
                     at the top of the scan (positive = stands proud of it)
  s                  arc length along the sidewall, measured from the band edge
  theta              tangent angle from vertical (deg), + = surface leaning out
  kappa              curvature (1/mm), + = convex (bulging outward)

Because cords make the sidewall almost inextensible along the meridian,
equal ``s`` from a common anchor ~ the same material point in both scans.
That is what the "matched" quantities use.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter

DROPOUT = 250.0  # sensor out-of-range code is 300.15; anything above this is invalid


# --------------------------------------------------------------------------- I/O
def load_scan(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (x, z) sample arrays.

    Accepts the two-row, tab-separated layout copied from Excel (row 1 = laser x,
    row 2 = stage z, blank/space cells ignored) or a two-column file (x, z) with
    an optional header, separated by tabs, commas or whitespace.
    """
    rows = [r for r in Path(path).read_text().splitlines() if r.strip()]

    def nums(cells):
        out = []
        for c in cells:
            c = c.strip()
            if not c:
                continue
            try:
                out.append(float(c))
            except ValueError:
                pass  # header / label cell
        return np.asarray(out, float)

    if len(rows) == 2:
        x, z = (nums(r.split("\t")) for r in rows)
    else:
        sep = "\t" if "\t" in rows[0] else ("," if "," in rows[0] else None)
        pairs = [nums(r.split(sep)) for r in rows]
        pairs = [p for p in pairs if len(p) >= 2]
        x = np.array([p[0] for p in pairs])
        z = np.array([p[1] for p in pairs])
    if len(x) != len(z):
        raise ValueError(f"x has {len(x)} samples but z has {len(z)}")
    return x, z


# ----------------------------------------------------------------- passes/cleaning
@dataclass
class Pass:
    name: str
    z_raw: np.ndarray
    x_raw: np.ndarray
    z: np.ndarray            # cleaned, sorted by z
    x: np.ndarray
    n_dropout: int
    n_hold: int


def split_passes(x: np.ndarray, z: np.ndarray) -> tuple[slice, slice]:
    """Split at the stage turnaround (the extreme of z reached mid-record)."""
    i = int(np.argmin(z)) if z[0] > z[len(z) // 2] else int(np.argmax(z))
    return slice(0, i), slice(i, len(z))


def clean_pass(name: str, x: np.ndarray, z: np.ndarray, drop_holds: bool = True) -> Pass:
    """Remove dropouts and sample-and-hold repeats, sort by z.

    The laser updates slower than the stage encoder is logged, so a reading is
    often repeated 2-3 times against advancing z. Only the first sample of each
    run is kept; the repeats carry a stale x against a new z.
    """
    ok = x < DROPOUT
    hold = np.r_[False, x[1:] == x[:-1]] & ok
    keep = ok & ~hold if drop_holds else ok
    o = np.argsort(z[keep])
    return Pass(name, z, x, z[keep][o], x[keep][o], int((~ok).sum()), int(hold.sum()))


def noise_floor(p: Pass, z0: float, z1: float, window: int = 15) -> float:
    m = (p.z > z0) & (p.z < z1)
    return float(np.std(p.x[m] - savgol_filter(p.x[m], window, 2)))


# ------------------------------------------------------ rigid band and deflection
@dataclass
class BandShift:
    x_band: float          # laser reading of the rigid band (mm)
    edge_inflated: float   # z where the inflated sidewall meets the band
    edge_loaded: float     # same feature in the loaded scan (= edge_inflated - delta)
    delta: float           # vertical translation of the band, inflated -> loaded (mm)
    delta_edge: float      # independent estimate from the band-level crossing
    texture_corr: float    # correlation of band texture at the chosen shift
    x_offset: float        # axial shift of the band at that delta (≈0 for rigid)


def _crossing_up(p: Pass, level: float, z_start: float) -> float:
    """First z above z_start where the smoothed profile reaches ``level``."""
    xs = savgol_filter(p.x, 9, 2)
    idx = np.where((p.z > z_start) & (xs >= level))[0]
    return float(p.z[idx[0]])


def find_band_shift(inf: Pass, load: Pass, top_mm: float = 30.0,
                    search: float = 8.0, tol: float = 1.0) -> BandShift:
    """Locate the textured rigid band at the top of the scan and how far it moved.

    1. Band level = median reading over the top ``top_mm`` of the inflated scan.
    2. Edge = where each profile climbs from its widest point up to that level.
    3. Refine the shift by correlating the band's surface texture between the
       scans within +/- ``search`` mm of the edge-based estimate.
    """
    x_band = float(np.median(inf.x[inf.z > inf.z.max() - top_mm]))
    z_mw_i = float(inf.z[np.argmin(savgol_filter(inf.x, 15, 2))])
    z_mw_l = float(load.z[np.argmin(savgol_filter(load.x, 15, 2))])
    e_i = _crossing_up(inf, x_band - tol, z_mw_i)
    e_l = _crossing_up(load, x_band - tol, z_mw_l)
    d_edge = e_i - e_l

    zg = np.arange(e_i + 4.0, inf.z.max() - 1.0, 0.25)
    a = np.interp(zg, inf.z, inf.x)
    best = (-2.0, d_edge, 0.0)
    for d in np.arange(d_edge - search, d_edge + search + 1e-9, 0.25):
        if zg[0] - d < load.z.min():
            continue
        b = np.interp(zg - d, load.z, load.x)
        r = np.corrcoef(a, b)[0, 1]
        if r > best[0]:
            best = (float(r), float(d), float(np.mean(b - a)))
    r, d, off = best
    return BandShift(x_band, e_i, e_i - d, d, d_edge, r, off)


# --------------------------------------------------------------- smooth profiles
@dataclass
class Profile:
    """Smoothed sidewall profile sampled on a fine z grid (bottom -> band edge)."""
    name: str
    z: np.ndarray
    u: np.ndarray            # outward offset vs band (mm)
    du: np.ndarray           # du/dz
    theta: np.ndarray        # tangent angle from vertical (deg)
    kappa: np.ndarray        # curvature (1/mm), + convex
    s: np.ndarray            # arc length from band edge (mm), 0 at the top
    sigma: float             # std of raw - smoothed u (sensor noise + texture)
    resid: np.ndarray = field(repr=False)    # raw - smoothed u at the raw samples
    resid_z: np.ndarray = field(repr=False)  # z of those samples


def fit_profile(p: Pass, x_band: float, z_lo: float, z_hi: float,
                step: float = 0.25, window_mm: float = 45.0, u_window_mm: float = 5.0) -> Profile:
    """Resample to a uniform grid, then Savitzky-Golay.

    ``u`` is lightly smoothed (``u_window_mm``) so displacements keep their shape.
    Slope and curvature use ``window_mm``: they are resolved at that length scale.
    Below ~40 mm the curvature is dominated by surface texture (lettering, ~1 mm
    bumps over ~20 mm give ±15 1/m), not by bending of the sidewall.
    """
    m = (p.z >= z_lo) & (p.z <= z_hi)
    zz, uu = p.z[m], x_band - p.x[m]
    z = np.arange(zz.min(), zz.max() + 1e-9, step)
    ul = np.interp(z, zz, uu)

    def sg(w_mm, deriv=0):
        w = max(5, int(round(w_mm / step)) | 1)
        return savgol_filter(ul, w, 2, deriv=deriv, delta=step, mode="interp")

    u, du, d2u = sg(u_window_mm), sg(window_mm, 1), sg(window_mm, 2)
    g = np.sqrt(1.0 + du**2)
    seg = np.r_[0.0, np.cumsum(0.5 * (g[1:] + g[:-1]) * np.diff(z))]
    s = seg[-1] - seg
    resid = uu - np.interp(zz, z, u)
    return Profile(p.name, z, u, du, np.degrees(np.arctan(du)), -d2u / g**3, s,
                   float(np.std(resid)), resid, zz)


# ----------------------------------------------------------- material matching
@dataclass
class Matched:
    """Inflated vs loaded compared at the same arc length from the band edge."""
    s: np.ndarray
    z_inf: np.ndarray
    z_load: np.ndarray
    u_inf: np.ndarray
    u_load: np.ndarray
    du: np.ndarray           # outward displacement (mm)
    dz: np.ndarray           # vertical displacement (mm)
    dtheta: np.ndarray       # rotation (deg)
    kappa_inf: np.ndarray
    kappa_load: np.ndarray
    dkappa: np.ndarray       # curvature change (1/mm)


def match_by_arclength(pi: Profile, pl: Profile, ds: float = 0.25) -> Matched:
    s = np.arange(0.0, min(pi.s.max(), pl.s.max()), ds)

    def at(p, arr):  # p.s decreases with z; flip for np.interp
        return np.interp(s, p.s[::-1], arr[::-1])

    zi, zl = at(pi, pi.z), at(pl, pl.z)
    ui, ul = at(pi, pi.u), at(pl, pl.u)
    ki, kl = at(pi, pi.kappa), at(pl, pl.kappa)
    return Matched(s, zi, zl, ui, ul, ul - ui, zl - zi,
                   at(pl, pl.theta) - at(pi, pi.theta), ki, kl, kl - ki)


# --------------------------------------------------------------------- metrics
def _local_extrema(y: np.ndarray, x: np.ndarray, n: int = 3, min_sep: float = 15.0):
    """Largest |y| local extrema separated by at least min_sep in x."""
    order = np.argsort(-np.abs(y))
    picked: list[int] = []
    for i in order:
        if 0 < i < len(y) - 1 and all(abs(x[i] - x[j]) >= min_sep for j in picked):
            if abs(y[i]) >= abs(y[i - 1]) and abs(y[i]) >= abs(y[i + 1]):
                picked.append(int(i))
        if len(picked) == n:
            break
    return [(float(x[i]), float(y[i])) for i in picked]


def _trim_ends(y: np.ndarray, x: np.ndarray, margin: float):
    """Drop the half-window at each end, where the derivative filter is one-sided."""
    m = (x >= x[0] + margin) & (x <= x[-1] - margin)
    return y[m], x[m]


def zone_table(mt: Matched, names=("rim-side", "middle", "shoulder-side"), h_mm: float = 5.0):
    """Split the matched sidewall into thirds of arc length (from the band edge)."""
    edges = np.linspace(0, mt.s[-1], len(names) + 1)
    ds = np.gradient(mt.s)
    bulge_tot = np.sum(np.clip(mt.du, 0, None) * ds)
    energy_tot = np.sum(mt.dkappa**2 * ds)
    rows = []
    for k, nm in enumerate(names):
        m = (mt.s >= edges[k]) & (mt.s < edges[k + 1] + (k == len(names) - 1))
        rows.append(dict(
            zone=nm,
            s_from=float(edges[k]), s_to=float(edges[k + 1]),
            z_inflated_from=float(mt.z_inf[m][0]), z_inflated_to=float(mt.z_inf[m][-1]),
            mean_outward_mm=float(np.mean(mt.du[m])),
            max_outward_mm=float(np.max(mt.du[m])),
            mean_drop_mm=float(-np.mean(mt.dz[m])),
            mean_abs_rotation_deg=float(np.mean(np.abs(mt.dtheta[m]))),
            peak_abs_dkappa_per_m=float(1000 * np.max(np.abs(mt.dkappa[m]))),
            peak_surface_strain_pct=float(100 * h_mm * np.max(np.abs(mt.dkappa[m]))),
            share_of_bulge_pct=float(100 * np.sum(np.clip(mt.du[m], 0, None) * ds[m]) / bulge_tot),
            share_of_bending_energy_pct=float(100 * np.sum(mt.dkappa[m] ** 2 * ds[m]) / energy_tot),
        ))
    return rows


@dataclass
class Result:
    passes: dict
    band: BandShift
    inf: Profile
    load: Profile
    matched: Matched
    naive_z: np.ndarray
    naive_du: np.ndarray
    metrics: dict
    zones: list


def analyze(path: str | Path, h_mm: float = 5.0, load_kN: float | None = None,
            rim_end: str = "top", window_mm: float = 45.0) -> Result:
    x, z = load_scan(path)
    s1, s2 = split_passes(x, z)
    inf = clean_pass("inflated", x[s1], z[s1])
    load = clean_pass("loaded", x[s2], z[s2])

    band = find_band_shift(inf, load)
    pi = fit_profile(inf, band.x_band, inf.z.min(), band.edge_inflated, window_mm=window_mm)
    pl = fit_profile(load, band.x_band, load.z.min(), band.edge_loaded, window_mm=window_mm)
    mt = match_by_arclength(pi, pl)

    # naive delta at equal stage height (the "same z" comparison)
    zc = np.arange(max(pi.z[0], pl.z[0]), min(pi.z[-1], pl.z[-1]), 0.25)
    nd = np.interp(zc, pl.z, pl.u) - np.interp(zc, pi.z, pi.u)

    i_mw, l_mw = int(np.argmax(pi.u)), int(np.argmax(pl.u))
    k_mt = int(np.argmax(mt.du))
    k_nv = int(np.argmax(nd))
    span_i = band.edge_inflated - pi.z[0]

    zones = zone_table(
        mt,
        names=("rim-side", "middle", "shoulder-side") if rim_end == "top"
        else ("shoulder-side", "middle", "rim-side"),
        h_mm=h_mm,
    )
    metrics = dict(
        samples_total=int(len(x)),
        inflated=dict(samples=int(len(inf.x_raw)), dropouts=inf.n_dropout, held_repeats=inf.n_hold,
                      z_range=[float(inf.z.min()), float(inf.z.max())]),
        loaded=dict(samples=int(len(load.x_raw)), dropouts=load.n_dropout, held_repeats=load.n_hold,
                    z_range=[float(load.z.min()), float(load.z.max())]),
        noise_floor_mm=dict(inflated=pi.sigma, loaded=pl.sigma,
                            band_texture_inflated=float(np.std(
                                inf.x[inf.z > band.edge_inflated + 4]
                                - savgol_filter(inf.x[inf.z > band.edge_inflated + 4], 15, 2)))),
        band=asdict(band),
        vertical_deflection_mm=band.delta,
        vertical_stiffness_N_per_mm=(1000 * load_kN / band.delta) if load_kN else None,
        max_width=dict(
            inflated_z=float(pi.z[i_mw]), inflated_u=float(pi.u[i_mw]),
            inflated_height_frac=float((pi.z[i_mw] - pi.z[0]) / span_i),
            loaded_z=float(pl.z[l_mw]), loaded_u=float(pl.u[l_mw]),
            growth_mm=float(pl.u[l_mw] - pi.u[i_mw]),
            point_drop_mm=float(pi.z[i_mw] - pl.z[l_mw]),
        ),
        bulge_per_mm_deflection=float((pl.u[l_mw] - pi.u[i_mw]) / band.delta),
        naive_same_z=dict(peak_mm=float(nd[k_nv]), at_z=float(zc[k_nv]),
                          area_mm2=float(np.trapezoid(np.clip(nd, 0, None), zc))),
        matched=dict(peak_outward_mm=float(mt.du[k_mt]), at_s=float(mt.s[k_mt]),
                     inflated_z_of_that_point=float(mt.z_inf[k_mt]),
                     drop_at_band_mm=float(-mt.dz[0]), drop_at_bottom_mm=float(-mt.dz[-1])),
        arc_length=dict(inflated_mm=float(pi.s.max()), loaded_visible_mm=float(pl.s.max()),
                        deficit_mm=float(pi.s.max() - pl.s.max()),
                        deficit_pct=float(100 * (pi.s.max() - pl.s.max()) / pi.s.max())),
        hinges=[dict(s=s_, z_inflated=float(np.interp(s_, mt.s, mt.z_inf)),
                     dkappa_per_m=1000 * k_, surface_strain_pct=100 * h_mm * abs(k_))
                for s_, k_ in _local_extrema(*_trim_ends(mt.dkappa, mt.s, window_mm / 2))],
        assumptions=dict(rim_end=rim_end, surface_offset_from_ply_mm=h_mm, load_kN=load_kN,
                         derivative_window_mm=window_mm),
    )
    return Result(dict(inflated=inf, loaded=load), band, pi, pl, mt, zc, nd, metrics, zones)
