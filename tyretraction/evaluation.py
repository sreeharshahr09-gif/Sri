"""Honest evaluation: case-specific train/test splits, imbalance handling,
metrics, prediction intervals and the experiment grid.

Cases (what "untested" means):
  random  Random split of individual tests. Shown for comparison ONLY: repeats
          of the same tyre and condition land in both train and test, so the
          model is rewarded for memorising - this is how the earlier tool got
          its R² of 0.91-0.94.
  A       Tested tyre, new operating condition: whole configurations are held
          out; scored only where the same tyre was tested at other conditions.
  B       Known compound, new size: one size held out at a time; scored on
          compounds that were tested in another size.
  C       New compound: whole compound groups held out (every size).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, KFold

from . import schema as S
from .models import make_model

CASES = ["random", "A", "B", "C"]
CASE_LABELS = {
    "random": "Random split (leaky - comparison only)",
    "A": "A: tested tyre, new condition",
    "B": "B: known compound, new size",
    "C": "C: new compound",
}
WEIGHTINGS = ["none", "balanced", "config_mean"]
WEIGHT_LABELS = {
    "none": "Every test counts once",
    "balanced": "Sizes re-weighted (1/sqrt(n))",
    "balanced_full": "Sizes re-weighted (1/n)",
    "config_mean": "Repeats averaged first",
}


@dataclass
class Dataset:
    df: pd.DataFrame                    # master rows with keys, index 0..n-1
    X: pd.DataFrame                     # features, same index
    Y: dict                             # mode -> DataFrame of targets ("raw", "corrected")
    curves: dict = field(default_factory=dict)   # optional curve material


# ----------------------------------------------------------------------------
# Splits
# ----------------------------------------------------------------------------
def make_splits(df, case, seed=0, n_folds=5):
    """List of (train_positions, test_positions) and a note. Positions index df."""
    n = len(df)
    pos = np.arange(n)
    folds, note = [], ""
    if case == "random":
        folds = list(KFold(n_folds, shuffle=True, random_state=seed).split(pos))
    elif case == "A":
        g = df["config_id"].to_numpy()
        k = min(n_folds, len(set(g)))
        for tr, te in GroupKFold(k, shuffle=True, random_state=seed).split(pos, groups=g):
            seen = set(df["tyre_id"].iloc[tr])
            te = te[df["tyre_id"].iloc[te].isin(seen).to_numpy()]
            if len(te):
                folds.append((tr, te))
        if not folds:
            note = "No tyre was tested at more than one condition, so case A cannot be assessed."
    elif case == "B":
        for s in sorted(df["size_id"].unique()):
            te = pos[(df["size_id"] == s).to_numpy()]
            tr = pos[(df["size_id"] != s).to_numpy()]
            seen = set(df["compound_id"].iloc[tr])
            te = te[df["compound_id"].iloc[te].isin(seen).to_numpy()]
            if len(te):
                folds.append((tr, te))
        if not folds:
            note = "No compound was tested in more than one size, so case B cannot be assessed."
    elif case == "C":
        g = df["compound_id"].to_numpy()
        k = min(n_folds, len(set(g)))
        if k >= 2:
            folds = list(GroupKFold(k, shuffle=True, random_state=seed).split(pos, groups=g))
        else:
            note = "Fewer than two compounds."
    else:
        raise ValueError(case)
    return folds, note


# ----------------------------------------------------------------------------
# Training-set handling for unbalanced data
# ----------------------------------------------------------------------------
def _train_set(ds, tr, y, weighting, feats):
    Xtr, ytr = ds.X.iloc[tr][feats], y[tr]
    ok = ~np.isnan(ytr)
    Xtr, ytr, rows = Xtr[ok], ytr[ok], ds.df.iloc[tr][ok]
    if weighting == "none":
        return Xtr, ytr, None
    if weighting in ("balanced", "balanced_full"):
        a = 0.5 if weighting == "balanced" else 1.0
        cnt = rows["size_id"].map(rows["size_id"].value_counts()).to_numpy(float)
        w = cnt ** -a
        return Xtr, ytr, w / w.mean()
    if weighting == "config_mean":
        key = rows["config_id"].to_numpy()
        Xg = Xtr.groupby(key).mean()
        yg = pd.Series(ytr, index=Xtr.index).groupby(key).mean().reindex(Xg.index)
        return Xg, yg.to_numpy(), None
    raise ValueError(weighting)


# ----------------------------------------------------------------------------
# One experiment
# ----------------------------------------------------------------------------
def _r2(y, p):
    ss = np.sum((y - y.mean()) ** 2)
    return float(1 - np.sum((y - p) ** 2) / ss) if ss > 0 and len(y) > 2 else np.nan


def run_one(ds, case, target, model, feats="all", weighting="none", mode="raw", seed=0,
            alpha=0.1, folds=None, model_params=None):
    """Out-of-fold predictions and metrics for one experiment."""
    cols = [c for c in S.FEATURE_SETS[feats] if c in ds.X.columns] if isinstance(feats, str) else feats
    y = ds.Y[mode][target].to_numpy(float)
    if folds is None:
        folds, note = make_splits(ds.df, case, seed)
    else:
        note = ""
    oof = np.full(len(y), np.nan)
    fold_id = np.full(len(y), -1)
    for k, (tr, te) in enumerate(folds):
        Xtr, ytr, w = _train_set(ds, tr, y, weighting, cols)
        if len(ytr) < 5:
            continue
        m = make_model(model, seed=seed + k, **(model_params or {})).fit(Xtr, ytr, w)
        te = te[~np.isnan(y[te])]
        if len(te):
            oof[te] = m.predict(ds.X.iloc[te][cols])
            fold_id[te] = k
    ok = ~np.isnan(oof) & ~np.isnan(y)
    res = dict(case=case, target=target, model=model, feats=feats if isinstance(feats, str) else "custom",
               weighting=weighting, mode=mode, seed=seed, n_scored=int(ok.sum()), note=note)
    if ok.sum() < 5:
        return res, oof
    yt, yp = y[ok], oof[ok]
    res.update(r2=_r2(yt, yp), rmse=float(np.sqrt(np.mean((yt - yp) ** 2))),
               mae=float(np.mean(np.abs(yt - yp))))
    # configuration-level score: compare averages of repeats
    cfg = ds.df["config_id"].to_numpy()[ok]
    cm = pd.DataFrame({"y": yt, "p": yp, "c": cfg}).groupby("c").mean()
    res["r2_config"] = _r2(cm.y.to_numpy(), cm.p.to_numpy())
    # cross-conformal interval: half-width from the OTHER folds' residuals
    r = np.abs(yt - yp)
    fid = fold_id[ok]
    cover, width = [], []
    for k in np.unique(fid):
        other = r[fid != k]
        if len(other) < 10:
            continue
        q = np.quantile(other, 1 - alpha)
        cover += list(r[fid == k] <= q)
        width.append(2 * q)
    res["interval_coverage"] = float(np.mean(cover)) if cover else np.nan
    res["interval_width"] = float(np.mean(width)) if width else np.nan
    res["interval_halfwidth_all"] = float(np.quantile(r, 1 - alpha))
    return res, oof


def per_group_rmse(ds, target, mode, oof, by="size_id"):
    y = ds.Y[mode][target].to_numpy(float)
    ok = ~np.isnan(oof) & ~np.isnan(y)
    d = pd.DataFrame({"g": ds.df[by].to_numpy()[ok], "e": (y - oof)[ok]})
    return d.groupby("g")["e"].agg(rmse=lambda e: float(np.sqrt(np.mean(e ** 2))), bias="mean", n="size")


# ----------------------------------------------------------------------------
# Experiment grid with a resumable ledger
# ----------------------------------------------------------------------------
LEDGER_COLS = ["key", "case", "target", "model", "feats", "weighting", "mode", "seed", "n_scored",
               "r2", "r2_config", "rmse", "mae", "interval_coverage", "interval_width",
               "interval_halfwidth_all", "note", "status", "error", "secs"]


def exp_key(e):
    return "|".join(str(e[k]) for k in ("case", "target", "model", "feats", "weighting", "mode", "seed"))


def default_grid(modes, targets, extended=False):
    grid = []
    feats = ["all", "conditions", "physics"] if extended else ["all"]
    weights = WEIGHTINGS + (["balanced_full"] if extended else [])
    seeds = [0, 1, 2] if extended else [0]
    for case in CASES:
        for t in targets:
            for mode in modes:
                for model in ["surface_mean", "ridge", "knn", "rf", "lgbm", "gp"]:
                    for f in feats:
                        for w in weights:
                            if model == "surface_mean" and (f != "all" or w == "balanced_full"):
                                continue
                            for sd in seeds:
                                if sd > 0 and case in ("B",):
                                    continue        # B folds are deterministic
                                grid.append(dict(case=case, target=t, model=model, feats=f,
                                                 weighting=w, mode=mode, seed=sd))
    return grid


def run_grid(ds, grid, out_dir, hours=None, n_jobs=-1, log=print):
    """Run experiments not yet in the ledger. Results are appended after each
    batch, so an interrupted run resumes where it stopped. Out-of-fold
    predictions are stored per experiment (oof.joblib)."""
    import joblib
    from joblib import Parallel, delayed

    os.makedirs(out_dir, exist_ok=True)
    ledger = os.path.join(out_dir, "experiments.csv")
    oof_path = os.path.join(out_dir, "oof.joblib")
    done = set(pd.read_csv(ledger)["key"]) if os.path.exists(ledger) else set()
    store = joblib.load(oof_path) if os.path.exists(oof_path) else {}
    todo = [e for e in grid if exp_key(e) not in done]
    t_end = time.time() + hours * 3600 if hours else None
    log(f"experiments: {len(grid)} in grid, {len(done)} done, {len(todo)} to run")

    def one(e):
        t0 = time.time()
        try:
            res, oof = run_one(ds, e["case"], e["target"], e["model"], e["feats"], e["weighting"],
                               e["mode"], e["seed"], model_params={"n_jobs": 1})
            res.update(status="ok", error="")
        except Exception as ex:      # one failing experiment never stops the run
            res, oof = dict(e), None
            res.update(status="error", error=str(ex)[:300])
        res["key"], res["secs"] = exp_key(e), time.time() - t0
        return res, oof

    batch = max(8, 4 * (os.cpu_count() or 1))
    for i in range(0, len(todo), batch):
        if t_end and time.time() > t_end:
            log("time budget reached; stopping (rerun to resume)")
            break
        out = Parallel(n_jobs=n_jobs)(delayed(one)(e) for e in todo[i:i + batch])
        recs = []
        for res, oof in out:
            recs.append({c: res.get(c, np.nan) for c in LEDGER_COLS})
            if oof is not None:
                store[res["key"]] = oof.astype(np.float32)
        pd.DataFrame(recs, columns=LEDGER_COLS).to_csv(
            ledger, mode="a", header=not os.path.exists(ledger), index=False)
        joblib.dump(store, oof_path)
        log(f"  {min(i + batch, len(todo))}/{len(todo)} experiments")
    return pd.read_csv(ledger), store
