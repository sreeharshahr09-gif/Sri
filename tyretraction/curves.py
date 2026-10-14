"""Turning raw µ-slip points into clean curves, landmarks and shape descriptors."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter

from . import schema as S

GRID = np.round(np.arange(0.0, 1.0 + 1e-9, S.SLIP_GRID_STEP), 6)


def bin_curve(sr, mu, grid=GRID):
    """Average raw points into slip bins centred on `grid` (NaN where empty).
    Binning gives every part of the slip range equal weight, whatever the rig's
    sampling density."""
    half = (grid[1] - grid[0]) / 2
    idx = np.clip(np.round((sr - grid[0]) / (2 * half)).astype(int), 0, len(grid) - 1)
    ok = (sr >= grid[0] - half) & (sr <= grid[-1] + half)
    s = np.bincount(idx[ok], weights=mu[ok], minlength=len(grid))
    n = np.bincount(idx[ok], minlength=len(grid))
    out = np.full(len(grid), np.nan)
    out[n > 0] = s[n > 0] / n[n > 0]
    return out


def _spike_mask(sr, mu, grid=GRID):
    """True for isolated outlying points beyond the steep initial rise.

    Each point is compared with the median of its 5 slip-ordered neighbours
    (itself included); robust even when the braking sweep has only ~50 points
    and a single spike would drag any smoother along with it."""
    n = len(mu)
    if n < 7:
        return np.zeros(n, bool)
    pad = np.r_[np.full(2, mu[0]), mu, np.full(2, mu[-1])]
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad, 5), axis=1)
    res = mu - med
    far = sr >= S.INIT_SLOPE_MAX_SLIP
    if far.sum() < 5:
        return np.zeros(n, bool)
    mad = np.median(np.abs(res[far] - np.median(res[far]))) * 1.4826
    return far & (np.abs(res) > max(6 * mad, 0.05))


def smooth_on_grid(sr, mu, grid=GRID):
    """Binned, gap-filled (inside the measured range only) and lightly smoothed
    curve on the common grid, with isolated spikes removed first. Outside the
    measured slip range stays NaN."""
    spikes = _spike_mask(sr, mu, grid)
    return _smooth(sr[~spikes], mu[~spikes], grid)


def _smooth(sr, mu, grid=GRID):
    b = bin_curve(sr, mu, grid)
    half = (grid[1] - grid[0]) / 2
    inside = (grid >= sr.min() - half) & (grid <= sr.max() + half)
    good = ~np.isnan(b)
    if good.sum() < 5:
        return np.full(len(grid), np.nan)
    filled = np.interp(grid, grid[good], b[good])
    filled[~inside] = np.nan
    seg = np.where(inside)[0]
    if len(seg) >= 9:
        sm = savgol_filter(filled[seg], 9, 2, mode="interp")
        # keep the steep initial rise unsmoothed: smoothing would flatten it
        steep = grid[seg] < S.INIT_SLOPE_MAX_SLIP
        sm[steep] = filled[seg][steep]
        filled[seg] = sm
    return filled


def curve_descriptors(sr, mu, grid=GRID):
    """Landmarks and quality descriptors of one measured curve."""
    d = {"n_points": len(sr), "n_brake_points": int((sr > 0.005).sum()), "sr_min": float(sr.min()) if len(sr) else np.nan,
         "sr_max": float(sr.max()) if len(sr) else np.nan}
    if len(sr) < 5:
        return d
    sm = smooth_on_grid(sr, mu, grid)
    if np.all(np.isnan(sm)):
        return d
    i = int(np.nanargmax(sm))
    d["mu_peak"] = float(sm[i])
    d["SR_at_mupeak"] = float(grid[i])
    lock = mu[sr >= S.LOCK_SLIP_MIN]
    d["mu_lock"] = float(lock.mean()) if len(lock) >= 3 else np.nan
    low = sr <= S.INIT_SLOPE_MAX_SLIP
    if low.sum() < 4:
        low = np.zeros(len(sr), bool); low[:min(6, len(sr))] = True
    d["init_slope"] = float(np.polyfit(sr[low], mu[low], 1)[0]) if np.ptp(sr[low]) > 0 else np.nan
    for pct in (5, 10, 20):
        d[f"mu_at_{pct:02d}"] = float(np.interp(pct / 100, grid, sm))
    d["mu_slide"] = (float(np.interp(S.SLIDE_SLIP, grid, sm))
                     if sr.max() >= S.SLIDE_SLIP - 0.02 else np.nan)
    # noise / spikes are judged past the steep initial rise, where a slip bin
    # spans a large change in µ and residuals are not meaningful
    far = sr >= S.INIT_SLOPE_MAX_SLIP
    resid = (mu - np.interp(sr, grid[~np.isnan(sm)], sm[~np.isnan(sm)]))[far]
    if len(resid) < 5:
        resid = np.zeros(5)
    d["noise_sd"] = float(np.std(resid))
    d["n_spikes"] = int(_spike_mask(sr, mu, grid).sum())
    gaps = np.diff(sr)
    d["max_slip_gap"] = float(gaps.max()) if len(gaps) else np.nan
    d["mu_max_raw"] = float(mu.max())
    d["mu_min_raw"] = float(mu.min())
    d["drop_ratio"] = d["mu_lock"] / d["mu_peak"] if d["mu_peak"] else np.nan
    return d


def describe_all(curves):
    """Descriptors for every curve: DataFrame indexed by ID."""
    rows = {sid: curve_descriptors(sr, mu) for sid, (sr, mu) in curves.items()}
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis(S.ID_COL)


def curve_matrix(curves, ids, grid=GRID):
    """Smoothed curves on the common grid, one row per ID (NaN beyond range)."""
    M = np.full((len(ids), len(grid)), np.nan)
    for k, sid in enumerate(ids):
        if sid in curves:
            sr, mu = curves[sid]
            M[k] = smooth_on_grid(sr, mu, grid)
    return M


def binned_matrix(curves, ids, grid=GRID):
    """Binned (unsmoothed) curves on the common grid - the scoring reference."""
    M = np.full((len(ids), len(grid)), np.nan)
    for k, sid in enumerate(ids):
        if sid in curves:
            M[k] = bin_curve(*curves[sid], grid)
    return M


def despike(sr, mu, grid=GRID):
    """Raw points with isolated spikes removed."""
    keep = ~_spike_mask(sr, mu, grid)
    return sr[keep], mu[keep]
