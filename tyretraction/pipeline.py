"""End-to-end analysis: load -> quality -> statistics -> Pacejka -> models ->
curves -> predictor. Results are collected in one dict (saved as
results.joblib) that the report renders."""
from __future__ import annotations

import os
import time

import joblib
import numpy as np
import pandas as pd

from . import curves as cv
from . import evaluation as ev
from . import io
from . import pacejka as pj
from . import quality as q
from . import schema as S
from . import sessions as ss
from . import stats as st
from .curve_models import evaluate_routes


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_and_check(master_path, curve_dir=None, id_regex=r"^\s*(\d+)", column_map=None,
                   sheet="Master File", reference=None):
    R = {"inputs": dict(master=os.path.basename(master_path), curve_dir=curve_dir, id_regex=id_regex)}
    master, info = io.load_master(master_path, sheet=sheet, column_map=column_map)
    master = io.add_keys(master)
    R["load_info"] = info
    R["master_raw"] = master
    log(f"master: {len(master)} test rows")

    curves, notes, desc = {}, pd.DataFrame(columns=[S.ID_COL, "note", "file"]), pd.DataFrame()
    match_report = None
    if curve_dir:
        matched, match_report = io.match_curve_files(curve_dir, master[S.ID_COL], id_regex)
        log(f"curve files: {match_report['n_files']} found, {match_report['n_matched']} matched")
        curves, notes = io.load_curves(matched)
        desc = cv.describe_all(curves)
    R["match_report"] = match_report

    rows = q.check_master(master, info)
    if curve_dir:
        rows += q.check_matching(match_report, notes)
        if len(desc):
            rows += q.check_curves(desc) + q.check_consistency(master, desc)
        else:
            log("no curve file could be linked to a master row: continuing with master landmarks only")
    flags0 = q.to_frame(rows)
    targets = (q.resolve_targets(master, desc, flags0) if len(desc)
               else master.drop_duplicates(S.ID_COL, keep=False).set_index(S.ID_COL)[S.MASTER_TARGETS]
               .assign(target_source="master"))
    for t in S.MASTER_TARGETS:     # master-only mode: drop out-of-range master values too
        if not len(desc):
            bad = flags0[(flags0.check == "out_of_range") & flags0.detail.str.startswith(t)].ID
            targets.loc[targets.index.isin(bad), t] = np.nan

    # one clean row per unique ID with at least a peak µ
    df = master.drop_duplicates(S.ID_COL, keep=False).set_index(S.ID_COL)
    df = df.drop(columns=[c for c in S.MASTER_TARGETS if c in df]).join(targets, how="left")
    df = df[df["mu_peak"].notna()].reset_index()
    rows += q.check_repeats(df)
    R["desc"] = desc
    R["file_notes"] = notes
    R["raw_curves"] = curves

    # clean curves (spikes removed) aligned to df rows
    clean = {sid: cv.despike(*curves[sid]) for sid in df[S.ID_COL] if sid in curves}
    R["n_curves_used"] = len(clean)

    # Pacejka fits
    fits = {}
    if clean:
        ids = [sid for sid in df[S.ID_COL] if sid in clean]
        log(f"Pacejka: fitting {len(ids)} curves (free B,C,D,E)")
        free = pj.fit_all(clean, ids)
        surf = df.set_index(S.ID_COL)["SurfaceType"]
        C_tab = free.join(surf).groupby("SurfaceType")["C"].median().to_dict()
        C_by = {sid: C_tab.get(surf[sid], np.nanmedian(list(C_tab.values()))) for sid in ids}
        log(f"Pacejka: stabilised fits with C per surface {', '.join(f'{k} {v:.2f}' for k, v in C_tab.items())}")
        stab = pj.fit_all(clean, ids, C_by_id=C_by)
        fits = dict(free=free, stab=stab, C_table=C_tab)
        rows += q.check_fits(free)
    R["fits"] = fits
    R["flags"] = q.to_frame(rows)

    # sessions / reference tyre
    ref = ss.detect_reference(df, override=reference)
    sess = {"reference": ref}
    tg = df.set_index(S.ID_COL)[[c for c in ss.CORRECTABLE if c in df]]
    if ref:
        method, ev_all = ss.choose_method(df, tg, ref)
        fac, lev, rb = ss.correction_factors(df, tg, ref, method)
        tab, ev_stats = ss.evidence(df, tg, ref, method)
        sess.update(method=method, evidence_all=ev_all, factors=fac, levels=lev, ref_blocks=rb,
                    evidence_table=tab, evidence=ev_stats)
        log(f"reference tyre(s): {ref}; level rule '{method}'; evidence {ev_stats}")
    else:
        log("no reference tyre detected (no tyre tested in >= 3 separate blocks)")
    R["sessions"] = sess
    R["df"] = df
    R["clean_curves"] = clean
    return R


def build_dataset(R):
    df = R["df"]
    X = io.build_features(df)
    Y = {"raw": df[[t for t in S.TARGETS if t in df]].astype(float).copy()}
    fits = R["fits"]
    if fits:
        st_ = fits["stab"].reindex(df[S.ID_COL])
        Y["raw"]["P_logB"] = np.log(st_["B"].to_numpy())
        Y["raw"]["P_logD"] = np.log(st_["D"].to_numpy())
        Y["raw"]["P_E"] = st_["E"].to_numpy()
    sess = R["sessions"]
    fac_peak = np.ones(len(df))
    if sess.get("reference"):
        fac = sess["factors"].reindex(df[S.ID_COL])
        Yc = Y["raw"].copy()
        for c in fac.columns:
            Yc[c] = Yc[c].to_numpy() * fac[c].to_numpy()
        fac_peak = fac["mu_peak"].to_numpy()
        if "P_logD" in Yc:
            Yc["P_logD"] = Yc["P_logD"] + np.log(fac_peak)
        Y["corrected"] = Yc
    curves = {}
    if R["clean_curves"]:
        ids = df[S.ID_COL].tolist()
        Bm = cv.binned_matrix(R["clean_curves"], ids)
        Sm = cv.curve_matrix(R["clean_curves"], ids)
        # curves that cover 0.02-0.95 slip are completed flat at both ends so the
        # curve-shape route can use them (sweeps end at ~0.99, not exactly 1)
        cover = (np.isfinite(Sm[:, (cv.GRID >= 0.02) & (cv.GRID <= 0.95)]).all(axis=1))
        Sm[cover] = pd.DataFrame(Sm[cover]).T.ffill().bfill().T.to_numpy()
        Sm[~cover] = np.nan
        surf = df["SurfaceType"].to_numpy()
        C_tab = fits["C_table"]
        curves = dict(binned={"raw": Bm}, smooth={"raw": Sm},
                      C_row=np.array([C_tab.get(s, np.nanmedian(list(C_tab.values()))) for s in surf]))
        if "corrected" in Y:
            curves["binned"]["corrected"] = Bm * fac_peak[:, None]
            curves["smooth"]["corrected"] = Sm * fac_peak[:, None]
    return ev.Dataset(df=df, X=X, Y=Y, curves=curves)


def statistics(R, ds):
    df, X = ds.df, ds.X
    tg = [t for t in S.TARGETS if t in ds.Y["raw"] and ds.Y["raw"][t].notna().sum() > 10]
    out = {"targets": tg, "overview": st.overview(df)}
    out["confounding"] = st.confounding(df)
    day = None
    if "corrected" in ds.Y:
        day = (1 / R["sessions"]["factors"] - 1).reindex(df[S.ID_COL]).set_axis(df.index)
    out["ceiling"] = {m: st.ceiling(df.assign(**ds.Y[m][tg]), tg) for m in ds.Y}
    out["ladder"] = st.variance_ladder(df.assign(**ds.Y["raw"][tg]), X, tg, day)
    out["effects"] = {t: st.condition_effects(df.assign(**ds.Y["raw"][tg]), X, t) for t in tg}
    feats = [c for c in S.FEATURE_SETS["all"] if c in X and X[c].notna().mean() > 0.5 and X[c].nunique() > 2]
    out["correlations"] = st.correlations(df.assign(**ds.Y["raw"][tg]), X, tg, feats)
    if R["fits"]:
        conf = df.set_index(S.ID_COL)["config_id"]
        surf = df.set_index(S.ID_COL)["SurfaceType"]
        icc = {}
        for name, f in (("free", R["fits"]["free"]), ("stabilised", R["fits"]["stab"])):
            for p in pj.PARAMS:
                if name == "stabilised" and p == "C":
                    continue
                icc[(name, p)] = pj.repeatability_within(f[p], conf.reindex(f.index), surf.reindex(f.index))
        for t in tg:
            icc[("landmark", t)] = pj.repeatability_within(ds.Y["raw"][t].to_numpy(), df["config_id"].to_numpy(),
                                                           df["SurfaceType"].to_numpy())
        out["icc"] = pd.Series(icc)
    return out


def modelling(R, ds, out_dir, extended=False, hours=None):
    modes = list(ds.Y)
    targets = [t for t in ["mu_peak", "SR_at_mupeak", "mu_slide", "mu_lock"]
               if t in ds.Y["raw"] and ds.Y["raw"][t].notna().sum() > 20]
    grid = ev.default_grid(modes, targets, extended)
    ledger, oof = ev.run_grid(ds, grid, out_dir, hours=hours, log=log)
    ok = ledger[ledger.status == "ok"].copy()
    # average repeated seeds of the same set-up before choosing, so the choice
    # is not just the luckiest fold assignment; then best per case x target x mode
    spec = ["case", "target", "mode", "model", "feats", "weighting"]
    num = ["n_scored", "r2", "r2_config", "rmse", "mae", "interval_coverage", "interval_width",
           "interval_halfwidth_all"]
    agg = ok.groupby(spec, as_index=False)[num].mean()
    agg["n_seeds"] = ok.groupby(spec).size().to_numpy()
    best = (agg[agg.n_scored >= 20].sort_values("r2", ascending=False)
            .groupby(["case", "target", "mode"]).head(1).set_index(["case", "target", "mode"]))
    curves = {}
    if ds.curves:
        rows = []
        for case in ["random", "A", "B", "C"]:
            for mode in modes:
                key = (case, "mu_peak", mode)
                if key not in best.index:
                    continue
                b = best.loc[key]
                log(f"curve routes: case {case}, {mode}, model {b.model}, weighting {b.weighting}")
                summ, preds = evaluate_routes(ds, case, b.model, b.feats, b.weighting, mode)
                rows.append(summ)
                curves[(case, mode)] = preds
        curve_summary = pd.concat(rows) if rows else pd.DataFrame()
    else:
        curve_summary = pd.DataFrame()
    return dict(ledger=ledger, oof=oof, best=best, curve_summary=curve_summary, curve_preds=curves,
                targets=targets)


def run(master, curve_dir=None, out_dir="out", id_regex=r"^\s*(\d+)", column_map=None,
        extended=False, hours=None, reference=None, report=True, sheet="Master File"):
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    R = load_and_check(master, curve_dir, id_regex, column_map, sheet=sheet, reference=reference)
    R["flags"].to_csv(os.path.join(out_dir, "data_quality_flags.csv"), index=False)
    ds = build_dataset(R)
    log("statistics")
    R["stats"] = statistics(R, ds)
    log("models")
    R["models"] = modelling(R, ds, out_dir, extended, hours)
    from .predictor import TractionPredictor
    log("training the deployable predictor")
    pred = TractionPredictor.train(R, ds)
    pred.save(os.path.join(out_dir, "predictor.joblib"))
    R["predictor_summary"] = pred.summary()
    R["dataset"] = ds
    R["elapsed_s"] = time.time() - t0
    joblib.dump(R, os.path.join(out_dir, "results.joblib"))
    if report:
        from .report import build_report
        path = build_report(R, out_dir)
        log(f"report: {path}")
    log(f"done in {R['elapsed_s'] / 60:.1f} min")
    return R
