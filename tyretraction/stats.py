"""Statistical analysis of the landmark data (before any machine learning)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from . import schema as S


def overview(df):
    """Tests / configurations / tyres / compounds per size, plus surfaces."""
    g = df.groupby("size_id")
    t = pd.DataFrame({
        "tests": g.size(),
        "dry": g["SurfaceType"].apply(lambda s: int((s.str.lower() == "dry").sum())),
        "wet": g["SurfaceType"].apply(lambda s: int((s.str.lower() == "wet").sum())),
        "configs": g["config_id"].nunique(),
        "tyres": g["tyre_id"].nunique(),
        "compounds": g["compound_id"].nunique(),
    }).sort_values("tests", ascending=False)
    t["share_tests"] = t["tests"] / t["tests"].sum()
    t["share_configs"] = t["configs"] / t["configs"].sum()
    return t


def confounding(df):
    """Compound x size test counts and how many compounds span >1 size."""
    tab = pd.crosstab(df["compound_id"], df["size_id"])
    spans = (tab > 0).sum(axis=1)
    return tab, int((spans > 1).sum()), len(spans)


def ceiling(df, targets, group="config_id"):
    """Repeat scatter and the best R² any model can reach.

    within SD = pooled SD of tests around their configuration mean
    (configurations with >= 2 tests). Per-test ceiling = 1 - within²/total².
    Per-config ceiling: R² attainable when predicting configuration means
    (the average of n repeats has within²/n scatter)."""
    rows = {}
    for t in targets:
        d = df[[group, t]].dropna()
        n = d.groupby(group)[t].transform("size")
        d = d[n >= 2]
        if d[group].nunique() < 2:
            continue
        dev = d[t] - d.groupby(group)[t].transform("mean")
        dof = len(d) - d[group].nunique()
        within = float(np.sqrt((dev ** 2).sum() / dof))
        total = float(df[t].std())
        cm = df.groupby(group)[t].agg(["mean", "size"])
        var_means = cm["mean"].var()
        noise_in_means = (within ** 2 / cm["size"]).mean()
        rows[t] = dict(within_sd=within, total_sd=total,
                       ceiling_r2_test=max(0.0, 1 - within ** 2 / total ** 2),
                       ceiling_r2_config=max(0.0, 1 - noise_in_means / var_means) if var_means > 0 else np.nan,
                       n_configs=int(d[group].nunique()), n_tests=len(d))
    return pd.DataFrame(rows).T


def _ols_r2(y, Z):
    Z = np.column_stack([np.ones(len(y)), Z]) if Z.size else np.ones((len(y), 1))
    beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    res = y - Z @ beta
    return 1 - res.var() / y.var()


def _onehot(s):
    return pd.get_dummies(s.astype(str), drop_first=True, dtype=float).to_numpy()


def variance_ladder(df, X, targets, day_level=None):
    """Share of each target's variance attributable to each block of
    explanations, using a fair (Shapley / LMG) split: the in-sample R² gain of
    a block is averaged over every order in which blocks can be added, so
    variance shared between correlated blocks (e.g. conditions that changed
    together with the test day) is split between them instead of going to
    whichever block happens to be listed first.

    Blocks: surface | test conditions | test day (reference-tyre level, if
    available) | tyre identity, shared fairly; then configuration (what is
    specific to one tyre-condition combination) added last, because it nests
    the tyre and surface blocks. The remainder is scatter between repeats. 'Tyre identity' is what a model must infer from
    compound / footprint / size data for an untested tyre."""
    from itertools import combinations
    from math import factorial

    cond = [c for c in S.OPERATING if c in X and X[c].notna().mean() > 0.8]
    out = {}
    for t in targets:
        ok = df[t].notna() & X[cond].notna().all(axis=1)
        if day_level is not None:
            ok &= day_level[t].notna() if t in day_level else ok
        y = df.loc[ok, t].to_numpy(float)
        if len(y) < 20 or y.var() == 0:
            continue
        dry = X.loc[ok, "IsDry"].to_numpy()[:, None]
        C = X.loc[ok, cond].to_numpy(float)
        C = (C - C.mean(0)) / np.where(C.std(0) > 0, C.std(0), 1)
        blocks = {"surface": dry, "conditions": np.column_stack([C, C * dry])}
        if day_level is not None and t in day_level:
            blocks["test_day"] = day_level.loc[ok, t].to_numpy(float)[:, None]
        blocks["tyre"] = _onehot(df.loc[ok, "tyre_id"])
        cfg = _onehot(df.loc[ok, "config_id"])
        names = list(blocks)
        r2 = {(): 0.0}
        for k in range(1, len(names) + 1):
            for sub in combinations(names, k):
                r2[sub] = max(0.0, _ols_r2(y, np.column_stack([blocks[b] for b in sub])))
        p = len(names)
        share = {}
        for b in names:
            rest = [n for n in names if n != b]
            v = 0.0
            for k in range(len(rest) + 1):
                w = factorial(k) * factorial(p - k - 1) / factorial(p)
                for sub in combinations(rest, k):
                    with_b = tuple(n for n in names if n in sub or n == b)
                    v += w * (r2[with_b] - r2[tuple(n for n in names if n in sub)])
            share[b] = max(v, 0.0)
        base = r2[tuple(names)]
        # configuration dummies nest tyre and surface, so they are added last:
        # what is specific to a tyre-condition combination beyond the
        # additive blocks above.
        full = max(base, _ols_r2(y, np.column_stack([blocks[b] for b in names] + [cfg])))
        share["configuration"] = full - base
        out[t] = dict(**share, repeat_scatter=1 - full, n=len(y))
    cols = ["surface", "conditions", "test_day", "tyre", "configuration", "repeat_scatter", "n"]
    return pd.DataFrame(out).T.reindex(columns=[c for c in cols if any(c in v for v in out.values())])


UNITS = {"Load_N": (1000, "per +1000 N"), "IP_kPa": (10, "per +10 kPa"),
         "Speed_kph": (10, "per +10 km/h"), "Tsurface_C": (10, "per +10 °C"),
         "Tair_C": (10, "per +10 °C")}


def condition_effects(df, X, target):
    """Effect of each test condition on the target, *within the same tyre*
    (tyre fixed effects), per surface. Only conditions that actually vary
    within tyres are estimable; the rest are returned as NaN. Returns a table
    with effect per practical unit and its 95% confidence interval."""
    rows = []
    for surf, g in df.groupby("SurfaceType"):
        idx = g.index[g[target].notna()]
        if len(idx) < 15:
            continue
        y = df.loc[idx, target].to_numpy(float)
        T = _onehot(df.loc[idx, "tyre_id"])
        for c, (unit, label) in UNITS.items():
            if c not in X:
                continue
            x = X.loc[idx, c].to_numpy(float)
            others = [o for o in UNITS if o != c and o in X and X.loc[idx, o].notna().all()]
            # within-tyre variation of this condition
            w = x - pd.Series(x).groupby(df.loc[idx, "tyre_id"].to_numpy()).transform("mean").to_numpy()
            if np.nanstd(w) < 1e-9 or np.isnan(x).any():
                rows.append(dict(surface=surf, condition=c, label=label, effect=np.nan, lo=np.nan,
                                 hi=np.nan, note="does not vary within tyres"))
                continue
            Z = np.column_stack([np.ones(len(y)), x / unit, X.loc[idx, others].to_numpy(float), T])
            beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
            res = y - Z @ beta
            dof = max(len(y) - np.linalg.matrix_rank(Z), 1)
            cov = np.linalg.pinv(Z.T @ Z) * (res @ res) / dof
            se = float(np.sqrt(max(cov[1, 1], 0)))
            q = sps.t.ppf(0.975, dof)
            rows.append(dict(surface=surf, condition=c, label=label, effect=float(beta[1]),
                             lo=float(beta[1] - q * se), hi=float(beta[1] + q * se), note=""))
    return pd.DataFrame(rows)


def correlations(df, X, targets, feats):
    """Spearman correlation of features with targets at CONFIGURATION level
    (one row per configuration, so repeat tests do not inflate the evidence),
    computed within each surface and averaged.
    Also returns the number of distinct values behind each feature: DMA data
    is constant per compound, so its effective sample size is the number of
    compounds, not the number of tests."""
    cm = (pd.concat([df[["config_id", "SurfaceType"] + targets], X[feats]], axis=1)
          .groupby("config_id").agg({**{c: "mean" for c in targets + feats}, "SurfaceType": "first"}))
    R = pd.DataFrame(index=feats, columns=targets, dtype=float)
    P = R.copy()
    # within each surface, combined as an n-weighted average: the dry/wet gap
    # would otherwise dominate every correlation
    for f in feats:
        for t in targets:
            rs, ws = [], []
            for _, g in cm.groupby("SurfaceType"):
                ok = g[[f, t]].dropna()
                if len(ok) >= 6 and ok[f].nunique() > 2 and ok[t].nunique() > 2:
                    rs.append(sps.spearmanr(ok[f], ok[t])[0]); ws.append(len(ok))
            if rs:
                R.loc[f, t] = float(np.average(rs, weights=ws))
                n = sum(ws)
                z = np.arctanh(np.clip(R.loc[f, t], -0.999, 0.999)) * np.sqrt(max(n - 3, 1))
                P.loc[f, t] = float(2 * sps.norm.sf(abs(z)))
    distinct = pd.Series({f: int(X[f].nunique()) for f in feats})
    return R, P, distinct
