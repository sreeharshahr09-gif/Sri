"""Predicting whole µ-slip curves for held-out tyres, three ways:

  L  landmarks -> Magic Formula: predict peak µ, slip at peak, µ slide and
     µ lock, convert exactly to B, C, D, E (C per surface) and assemble.
  P  parameters directly: predict log B, log D, E of the stabilised fits
     (C fixed per surface) plus µ lock, and assemble.
  F  curve shape (functional PCA): learn the main shapes of the measured
     curves, predict how much of each shape a tyre has, rebuild the curve.
     No Magic Formula involved; B, C, D, E are obtained afterwards by fitting.

All three are scored against the measured (binned) curves of held-out tests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import pacejka as pj
from . import schema as S
from .curves import GRID
from .evaluation import _train_set, make_splits, run_one
from .models import make_model

ROUTES = {"L": "Landmarks → Magic Formula", "P": "Magic Formula parameters directly",
          "F": "Curve shapes (no Magic Formula)"}
LM = ["mu_peak", "SR_at_mupeak", "mu_slide", "mu_lock"]


def _pca(M, var=0.995, kmax=6):
    mean = M.mean(axis=0)
    U, s, Vt = np.linalg.svd(M - mean, full_matrices=False)
    frac = np.cumsum(s ** 2) / np.sum(s ** 2)
    k = int(min(kmax, np.searchsorted(frac, var) + 1))
    return mean, Vt[:k]


def score_curves(measured, predicted):
    """Per-test curve RMSE and R² over slip bins that hold measured data."""
    rm, r2 = [], []
    for y, p in zip(measured, predicted):
        ok = ~np.isnan(y) & ~np.isnan(p)
        if ok.sum() < 8:
            rm.append(np.nan); r2.append(np.nan); continue
        e = y[ok] - p[ok]
        rm.append(float(np.sqrt(np.mean(e ** 2))))
        ss = np.sum((y[ok] - y[ok].mean()) ** 2)
        r2.append(float(1 - np.sum(e ** 2) / ss) if ss > 0 else np.nan)
    return np.array(rm), np.array(r2)


def predict_routes(ds, case, model, feats="all", weighting="none", mode="raw", seed=0):
    """Out-of-fold curves for routes L, P, F. Returns {route: (n, len(GRID))}
    plus the landmark OOF predictions."""
    folds, note = make_splits(ds.df, case, seed)
    n = len(ds.df)
    cur = ds.curves
    C_row = cur["C_row"]
    lm = {}
    for t in LM:
        if t in ds.Y[mode] and ds.Y[mode][t].notna().sum() > 10:
            lm[t] = run_one(ds, case, t, model, feats, weighting, mode, seed, folds=folds,
                            model_params={"n_jobs": 1})[1]
    out = {}
    if all(t in lm for t in LM):
        L = pd.DataFrame({t: lm[t] for t in LM})
        good = L.notna().all(axis=1).to_numpy()
        P = pj.params_from_landmark_table(L[good], C_row[good])[pj.PARAMS].to_numpy()
        curves = np.full((n, len(GRID)), np.nan)
        curves[good] = pj.assemble_curves(P, L.loc[good, "mu_lock"].to_numpy())
        out["L"] = curves
    # route P
    pt = {}
    for t in ("P_logB", "P_logD", "P_E"):
        if t in ds.Y[mode] and ds.Y[mode][t].notna().sum() > 10:
            pt[t] = run_one(ds, case, t, model, feats, weighting, mode, seed, folds=folds,
                            model_params={"n_jobs": 1})[1]
    if len(pt) == 3 and "mu_lock" in lm:
        P = np.column_stack([np.exp(pt["P_logB"]), C_row, np.exp(pt["P_logD"]),
                             np.clip(pt["P_E"], pj.LO[3], pj.HI[3])])
        good = ~np.isnan(P).any(axis=1) & ~np.isnan(lm["mu_lock"])
        curves = np.full((n, len(GRID)), np.nan)
        curves[good] = pj.assemble_curves(P[good], lm["mu_lock"][good])
        out["P"] = curves
    # route F
    Mfull = cur["smooth"][mode]
    complete = ~np.isnan(Mfull).any(axis=1)
    if complete.sum() > 20:
        cols = [c for c in S.FEATURE_SETS[feats] if c in ds.X.columns]
        curves = np.full((n, len(GRID)), np.nan)
        for k, (tr, te) in enumerate(folds):
            trc = tr[complete[tr]]
            if len(trc) < 10:
                continue
            mean, V = _pca(Mfull[trc])
            Z = np.full((n, len(V)), np.nan)
            Z[trc] = (Mfull[trc] - mean) @ V.T
            Zhat = np.zeros((len(te), len(V)))
            for j in range(len(V)):
                Xtr, ztr, w = _train_set(ds, tr, Z[:, j], weighting, cols)
                Zhat[:, j] = make_model(model, seed=seed + k, n_jobs=1).fit(Xtr, ztr, w).predict(ds.X.iloc[te][cols])
            curves[te] = mean + Zhat @ V
        out["F"] = curves
    return out, lm, note


def evaluate_routes(ds, case, model, feats="all", weighting="none", mode="raw", seed=0):
    """Curve-level scores per route. Returns (summary DataFrame, curves dict)."""
    preds, lm, note = predict_routes(ds, case, model, feats, weighting, mode, seed)
    measured = ds.curves["binned"][mode]
    rows = []
    for r, P in preds.items():
        rm, r2 = score_curves(measured, P)
        ok = ~np.isnan(rm)
        if ok.sum() == 0:
            continue
        rows.append(dict(case=case, route=r, model=model, weighting=weighting, mode=mode,
                         n_curves=int(ok.sum()), rmse_median=float(np.nanmedian(rm)),
                         rmse_p90=float(np.nanpercentile(rm, 90)),
                         r2_median=float(np.nanmedian(r2)), r2_p10=float(np.nanpercentile(r2, 10))))
        preds[r] = dict(curves=P, rmse=rm, r2=r2)
    return pd.DataFrame(rows), preds
