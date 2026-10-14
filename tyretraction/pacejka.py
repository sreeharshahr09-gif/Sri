"""Pacejka Magic Formula: fitting measured curves, and converting between
B, C, D, E and the engineering landmarks (peak µ, slip at peak, µ slide).

The Magic Formula is fitted on slip 0 - 0.90 only. Real braking curves drop
sharply in the last ~10% of slip as the wheel locks; the Magic Formula
flattens out there instead and would overestimate µ lock by ~0.07-0.10.
µ lock is therefore kept as its own landmark, and full curves are assembled
as Magic Formula up to 0.90 + a blend to µ lock (see `assemble_curves`).

    µ(s) = D · sin(C · atan(z)),   z = B·s − E·(B·s − atan(B·s))

With 1 < C < 2 and E ≤ 1 the curve has a single peak, and
  • D is exactly the peak µ,
  • B·C·D is the initial slope,
  • the peak sits where C·atan(z) = π/2, i.e. z = tan(π / 2C),
  • far past the peak µ falls toward D·sin(Cπ/2).
The landmark → B,C,D,E conversion below uses these identities, so it is exact
and needs no iterative curve fitting.

Note on C: the initial slope barely constrains C (very different C values
give slopes within ~1%), so C cannot be recovered from landmarks; one C per
surface (median of the free fits) is used instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares

from . import schema as S
from .curves import GRID, bin_curve

PARAMS = ["B", "C", "D", "E"]
LO = np.array([1.0, 1.02, 0.1, -5.0])
HI = np.array([120.0, 2.4, 2.0, 1.0])
X_SLIDE = S.SLIDE_SLIP
X_LOCK = (S.LOCK_SLIP_MIN + 1.0) / 2      # representative slip of the µ-lock window


def mf(s, B, C, D, E):
    Bs = B * s
    return D * np.sin(C * np.arctan(Bs - E * (Bs - np.arctan(Bs))))


def mf_matrix(P, grid=GRID):
    """Curves for many parameter rows. P: (n, 4) [B, C, D, E] -> (n, len(grid))."""
    P = np.asarray(P, float)
    Bs = P[:, :1] * grid[None, :]
    z = Bs - P[:, 3:4] * (Bs - np.arctan(Bs))
    return P[:, 2:3] * np.sin(P[:, 1:2] * np.arctan(z))


def _r2(y, yhat):
    ok = ~(np.isnan(y) | np.isnan(yhat))
    y, yhat = y[ok], yhat[ok]
    ss = np.sum((y - y.mean()) ** 2)
    return float(1 - np.sum((y - yhat) ** 2) / ss) if ss > 0 else np.nan


# ----------------------------------------------------------------------------
# Fitting measured curves
# ----------------------------------------------------------------------------
def fit_curve(sr, mu, C_fixed=None):
    """Fit B, C, D, E (or B, D, E with C fixed) to one measured curve.

    The fit uses slip 0 - 0.90 of the slip-binned curve, so dense and sparse
    parts of the sweep count equally and the lock-up drop is excluded. Multi-start bounded least squares with a robust loss.
    Returns dict(B, C, D, E, fit_r2, fit_rmse)."""
    b = bin_curve(sr, mu)
    ok = ~np.isnan(b) & (GRID <= X_SLIDE + 1e-9)
    s, m = GRID[ok], b[ok]
    if len(s) < 8:
        return dict(B=np.nan, C=np.nan, D=np.nan, E=np.nan, fit_r2=np.nan, fit_rmse=np.nan)
    D0 = float(np.clip(m.max(), 0.15, 1.9))
    sp = float(max(s[np.argmax(m)], 0.02))
    free_C = C_fixed is None

    def unpack(p):
        return (p[0], p[1], p[2], p[3]) if free_C else (p[0], C_fixed, p[1], p[2])

    def resid(p):
        return mf(s, *unpack(p)) - m

    lo = LO if free_C else LO[[0, 2, 3]]
    hi = HI if free_C else HI[[0, 2, 3]]
    best = None
    for C0 in ((1.4, 1.8) if free_C else (C_fixed,)):
        zp = np.tan(np.pi / (2 * C0))
        for E0 in (-0.5, 0.3, 0.8):
            B0 = float(np.clip(zp / sp * (1.2 if E0 > 0 else 0.9), lo[0] + 1e-3, hi[0] - 1e-3))
            p0 = [B0, C0, D0, E0] if free_C else [B0, D0, E0]
            p0 = np.clip(p0, lo + 1e-6, hi - 1e-6)
            try:
                r = least_squares(resid, p0, bounds=(lo, hi), loss="soft_l1", f_scale=0.03,
                                  max_nfev=400)
            except Exception:
                continue
            if best is None or r.cost < best.cost:
                best = r
    if best is None:
        return dict(B=np.nan, C=np.nan, D=np.nan, E=np.nan, fit_r2=np.nan, fit_rmse=np.nan)
    B, C, D, E = unpack(best.x)
    fit = mf(s, B, C, D, E)
    return dict(B=B, C=C, D=D, E=E, fit_r2=_r2(m, fit), fit_rmse=float(np.sqrt(np.mean((fit - m) ** 2))))


def fit_all(curves, ids, C_by_id=None, n_jobs=-1):
    """Fit every curve. C_by_id: optional {ID: fixed C}. DataFrame indexed by ID."""
    from joblib import Parallel, delayed

    def one(sid):
        sr, mu = curves[sid]
        return sid, fit_curve(sr, mu, None if C_by_id is None else C_by_id[sid])

    out = Parallel(n_jobs=n_jobs)(delayed(one)(sid) for sid in ids if sid in curves)
    return pd.DataFrame({sid: r for sid, r in out}).T.rename_axis(S.ID_COL).astype(float)


# ----------------------------------------------------------------------------
# Landmarks <-> parameters
# ----------------------------------------------------------------------------
def landmarks_from_params(P):
    """Peak µ, slip at peak, µ slide (µ at 0.90 slip) and initial slope of the
    Magic-Formula curve for each parameter row [B, C, D, E]."""
    P = np.asarray(P, float)
    fine = np.linspace(0, X_SLIDE, 1801)
    M = mf_matrix(P, fine)
    i = np.argmax(M, axis=1)
    return pd.DataFrame({"mu_peak": M[np.arange(len(P)), i], "SR_at_mupeak": fine[i],
                         "mu_slide": M[:, -1], "init_slope": P[:, 0] * P[:, 1] * P[:, 2]})


def assemble_curves(P, mu_lock, grid=GRID):
    """Full 0-1 slip curves: Magic Formula up to 0.90, then a straight blend to
    µ lock at the centre of the lock window, flat beyond. P: (n, 4); mu_lock: (n,)."""
    M = mf_matrix(P, grid)
    end = mf_matrix(P, np.array([X_SLIDE]))[:, 0]
    mu_lock = np.asarray(mu_lock, float)
    t = np.clip((grid - X_SLIDE) / (X_LOCK - X_SLIDE), 0, 1)
    tail = end[:, None] + (mu_lock[:, None] - end[:, None]) * t[None, :]
    use_tail = (grid > X_SLIDE)[None, :] & ~np.isnan(mu_lock)[:, None]
    return np.where(use_tail, tail, M)


def _solve_BE(xp, r, C, xl=X_SLIDE):
    """Given slip at peak xp, ratio r = µ(xl)/peak and shape C, find B and E.

    Peak condition fixes E as a function of B; the µ(xl) condition is then a
    1-D root in B. If the requested drop is outside what this C can produce,
    the closest achievable curve is returned (flagged by a residual)."""
    zp = np.tan(np.pi / (2 * C))
    ang = (np.pi - np.arcsin(np.clip(r, 1e-3, 0.999))) / C
    zl = np.tan(min(ang, np.pi / 2 - 1e-6)) if ang < np.pi / 2 else np.inf

    def E_of(B):
        a = B * xp
        return (a - zp) / (a - np.arctan(a))

    def g(logB):
        B = np.exp(logB)
        E = np.clip(E_of(B), LO[3], HI[3])
        a = B * xl
        z = a - E * (a - np.arctan(a))
        return np.arctan(z) - np.arctan(zl)

    # Scan B over the range where E stays within its bounds, then refine the
    # root (or the closest approach) between neighbouring scan points.
    lt = np.linspace(np.log(LO[0]), np.log(HI[0]), 400)
    Es = np.array([E_of(np.exp(t)) for t in lt])
    valid = (Es >= LO[3]) & (Es <= HI[3])
    if not valid.any():
        valid[:] = True
    lt = lt[valid]
    gs = np.array([g(t) for t in lt])
    sign = np.where(np.sign(gs[:-1]) * np.sign(gs[1:]) < 0)[0]
    try:
        if len(sign):
            # several roots are possible; take the one with the smallest |E|
            roots = [brentq(g, lt[j], lt[j + 1], xtol=1e-9) for j in sign]
            logB = min(roots, key=lambda t: abs(E_of(np.exp(t))))
        else:
            logB = lt[int(np.argmin(np.abs(gs)))]
    except Exception:
        logB = lt[int(np.argmin(np.abs(gs)))]
    B = float(np.exp(logB))
    return B, float(np.clip(E_of(B), LO[3], HI[3])), float(abs(g(logB)))


def params_from_landmarks(mu_peak, sr_peak, mu_slide, C=1.65):
    """Exact Magic-Formula parameters for given landmarks and C.

    D = mu_peak; B and E place the peak at sr_peak and µ(0.90) at mu_slide.
    Returns (B, C, D, E, residual); residual > 0 means the requested drop is
    not reachable with this C and the closest curve was returned."""
    D = float(mu_peak)
    xp = float(np.clip(sr_peak, 0.01, 0.6))
    r = float(np.clip(mu_slide / D, 0.05, 0.999))
    B, E, res = _solve_BE(xp, r, float(C))
    return B, float(C), D, E, res


def params_from_landmark_table(L, C_values):
    """Row-wise conversion. L: DataFrame[mu_peak, SR_at_mupeak, mu_slide];
    C_values: per-row C. Returns DataFrame[B, C, D, E, solve_residual]."""
    out = []
    for k, (mp, sp, ms) in enumerate(L[["mu_peak", "SR_at_mupeak", "mu_slide"]].to_numpy(float)):
        if np.isnan([mp, sp, ms]).any() or mp <= 0:
            out.append((np.nan,) * 5)
        else:
            out.append(params_from_landmarks(mp, sp, ms, C=float(C_values[k])))
    return pd.DataFrame(out, columns=PARAMS + ["solve_residual"], index=L.index)


# ----------------------------------------------------------------------------
# Parameter stability across repeat tests
# ----------------------------------------------------------------------------
def repeatability_within(values, groups, strata, min_repeats=2):
    """ICC computed separately within each stratum (e.g. surface) and averaged
    (weighted by tests), so a dry/wet difference does not count as 'repeatable'."""
    d = pd.DataFrame({"v": np.asarray(values, float), "g": np.asarray(groups), "s": np.asarray(strata)})
    vals, w = [], []
    for _, x in d.groupby("s"):
        r = repeatability(x["v"].to_numpy(), x["g"].to_numpy(), min_repeats)
        if np.isfinite(r):
            vals.append(r); w.append(len(x))
    return float(np.average(vals, weights=w)) if vals else np.nan


def repeatability(values, groups, min_repeats=2):
    """One-way ANOVA intraclass correlation ICC(1): the share of variance that
    is *between* configurations. Near 1 = repeats agree, the number describes
    the tyre/condition. Near 0 = the number mostly reflects test-to-test (or
    fitting) scatter and cannot be predicted."""
    d = pd.DataFrame({"v": values, "g": groups}).dropna()
    n = d.groupby("g")["v"].transform("size")
    d = d[n >= min_repeats]
    if d["g"].nunique() < 3:
        return np.nan
    k = d.groupby("g").size()
    grand = d["v"].mean()
    ssb = (k * (d.groupby("g")["v"].mean() - grand) ** 2).sum()
    ssw = ((d["v"] - d.groupby("g")["v"].transform("mean")) ** 2).sum()
    a, N = len(k), len(d)
    msb, msw = ssb / (a - 1), ssw / (N - a)
    k0 = (N - (k ** 2).sum() / N) / (a - 1)
    return float((msb - msw) / (msb + (k0 - 1) * msw))
