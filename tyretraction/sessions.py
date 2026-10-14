"""Test sessions and reference-tyre (day) correction.

Grip testing on a real surface drifts from session to session (water film,
surface temperature, polishing, contamination). Labs run a reference tyre at
regular intervals for exactly this reason. In the Apollo master the reference
tyre is the configuration tested in many separate blocks of ~6 trials,
interleaved with candidate tyres. Assuming Sl No follows test order:

  * a "block" = consecutive tests (by Sl No) of the same configuration;
  * the reference level at any test = reference block means interpolated
    in Sl No order between the bracketing reference blocks;
  * day-corrected value = value x (overall reference mean / local reference level).

Corrected values describe the tyre on an average test day; that is what a
prediction for an untested tyre should target, because the day is not a tyre
property and can only be measured, never predicted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import schema as S

CORRECTABLE = ["mu_peak", "mu_slide", "mu_lock"]


def blocks(df):
    """Runs of consecutive tests (in Sl No order) of the same configuration."""
    d = df.sort_values(S.ID_COL)
    bid = (d["config_id"] != d["config_id"].shift()).cumsum()
    return pd.Series(bid.to_numpy(), index=d.index).reindex(df.index)


def detect_reference(df, min_blocks=3, override=None):
    """Reference tyre per surface: the tyre whose tests are spread over the most
    separate blocks (at least `min_blocks`). `override` = tyre_id string used
    for every surface. Returns {surface: tyre_id}."""
    b = blocks(df)
    out = {}
    for surf, g in df.groupby("SurfaceType"):
        if override is not None:
            if (g["tyre_id"] == override).any():
                out[surf] = override
            continue
        nb = g.assign(block=b[g.index]).groupby("tyre_id")["block"].nunique()
        if len(nb) and nb.max() >= min_blocks and nb.idxmax() is not None:
            out[surf] = nb.idxmax()
    return out


def _level(x, mids, firsts, vals, method):
    o = np.argsort(mids)
    mids, firsts, vals = np.asarray(mids)[o], np.asarray(firsts)[o], np.asarray(vals)[o]
    if method == "interp":
        return np.interp(x, mids, vals)
    if method == "previous":       # session = from the start of a reference run to the next one
        i = np.clip(np.searchsorted(firsts, x, side="right") - 1, 0, len(firsts) - 1)
    else:                          # nearest reference run
        i = np.abs(x[:, None] - mids[None, :]).argmin(axis=1)
    return vals[i]


def reference_levels(df, values, ref, method="interp"):
    """For every test: interpolated reference level of each value column.

    df: master with keys; values: DataFrame indexed like df with value columns;
    ref: {surface: tyre_id}. Returns (levels DataFrame, ref block table)."""
    b = blocks(df)
    lev = pd.DataFrame(np.nan, index=df.index, columns=values.columns)
    tabs = []
    for surf, tyre in ref.items():
        on = df["SurfaceType"] == surf
        is_ref = on & (df["tyre_id"] == tyre)
        rb = (pd.DataFrame({S.ID_COL: df.loc[is_ref, S.ID_COL], "block": b[is_ref]})
              .join(values[is_ref]).groupby("block")
              .agg(first=(S.ID_COL, "min"), last=(S.ID_COL, "max"), n=(S.ID_COL, "size"),
                   **{c: (c, "mean") for c in values.columns}))
        rb["mid"] = (rb["first"] + rb["last"]) / 2
        rb["surface"] = surf
        tabs.append(rb)
        x = df.loc[on, S.ID_COL].to_numpy(float)
        for c in values.columns:
            ok = rb[c].notna()
            if ok.sum() >= 1:
                lev.loc[on, c] = _level(x, rb.loc[ok, "mid"].to_numpy(), rb.loc[ok, "first"].to_numpy(),
                                        rb.loc[ok, c].to_numpy(), method)
    return lev, (pd.concat(tabs) if tabs else pd.DataFrame())


def correction_factors(df, targets, ref, method="interp"):
    """Multiplicative day-correction factor per test and µ target.
    1.0 where no reference exists for the test's surface."""
    vals = targets[[c for c in CORRECTABLE if c in targets]].reindex(df[S.ID_COL]).set_axis(df.index)
    lev, rb = reference_levels(df, vals, ref, method)
    fac = pd.DataFrame(1.0, index=df.index, columns=vals.columns)
    for surf in ref:
        on = df["SurfaceType"] == surf
        sub = rb[rb.surface == surf]
        for c in vals.columns:
            grand = sub[c].mean()
            f = grand / lev.loc[on, c]
            fac.loc[on, c] = f.where(np.isfinite(f), 1.0)
    fac.index = df[S.ID_COL].to_numpy()
    lev.index = df[S.ID_COL].to_numpy()
    return fac, lev, rb


def evidence(df, targets, ref, method="interp"):
    """Do candidate tyres move with the reference tyre? One row per candidate
    block: block mean and the reference level at that time. A strong positive
    correlation means the session effect is real and worth correcting."""
    b = blocks(df)
    vals = targets[[c for c in CORRECTABLE if c in targets]].reindex(df[S.ID_COL]).set_axis(df.index)
    lev, _ = reference_levels(df, vals, ref, method)
    is_ref = pd.Series(False, index=df.index)
    for surf, tyre in ref.items():
        is_ref |= (df["SurfaceType"] == surf) & (df["tyre_id"] == tyre)
    cand = df.index[~is_ref & df["SurfaceType"].isin(list(ref))]
    rows = (pd.DataFrame({"block": b[cand], "surface": df.loc[cand, "SurfaceType"],
                          "first": df.loc[cand, S.ID_COL]})
            .join(vals.loc[cand]).join(lev.loc[cand].add_prefix("ref_")))
    agg = {"surface": ("surface", "first"), "first": ("first", "min"), "n": ("first", "size")}
    for c in vals.columns:
        agg[c] = (c, "mean"); agg["ref_" + c] = ("ref_" + c, "mean")
    tab = rows.groupby("block").agg(**agg)
    # correlation WITHIN each surface (centred per surface), so the dry/wet
    # difference cannot masquerade as a session effect
    stats = {}
    for c in vals.columns:
        ok = tab[["surface", c, "ref_" + c]].dropna()
        if len(ok) < 5:
            stats[c] = np.nan
            continue
        a = ok[c] - ok.groupby("surface")[c].transform("mean")
        b = ok["ref_" + c] - ok.groupby("surface")["ref_" + c].transform("mean")
        stats[c] = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan
    return tab, stats


def choose_method(df, targets, ref):
    """Pick the reference-level rule whose levels track the candidates best
    (highest mean evidence correlation). Returns (method, {method: stats})."""
    res = {m: evidence(df, targets, ref, m)[1] for m in ("interp", "previous", "nearest")}
    score = {m: np.nanmean(list(v.values())) for m, v in res.items()}
    return max(score, key=lambda m: -np.inf if np.isnan(score[m]) else score[m]), res
