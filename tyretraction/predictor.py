"""Deployable predictor for untested tyres.

For each new tyre the predictor
  1. decides which case applies: A (this tyre was tested before, new
     condition), B (its compound was tested in another size) or C (new
     compound);
  2. uses, for that case, the model / feature set / imbalance handling that
     scored best in the honest evaluation;
  3. returns peak µ, slip at peak, µ slide and µ lock with 90% ranges,
     Magic-Formula B, C, D, E, and the full 0-1 slip curve with a band;
  4. warns when inputs lie outside what has been tested.
Predictions are for an average test day when a reference tyre was available
(day-corrected training targets), otherwise for the raw test data.
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd

from . import io
from . import pacejka as pj
from . import schema as S
from .curve_models import LM, _pca
from .curves import GRID
from .evaluation import _train_set
from .models import make_model

INPUT_COLS = ["TyreSize", "Pattern", "Group", "SurfaceType", "Load_N", "IP_kPa", "Speed_kph",
              "RimWidth_in", "Tsurface_C", "Tair_C"] + S.DMA + S.FOOTPRINT


def write_template(path):
    pd.DataFrame(columns=["name"] + INPUT_COLS).to_csv(path, index=False)


class TractionPredictor:
    def __init__(self):
        self.case_models = {}      # case -> {target: dict(model, cols, halfwidth, r2, spec)}
        self.case_route = {}       # case -> dict(route, band, extra)
        self.meta = {}

    # ------------------------------------------------------------------ train
    @classmethod
    def train(cls, R, ds, route_policy="consistent"):
        self = cls()
        M = R["models"]
        best, ledger = M["best"], M["ledger"]
        mode = "corrected" if "corrected" in ds.Y else "raw"
        self.meta = dict(mode=mode, C_table=R["fits"].get("C_table", {}) if R["fits"] else {},
                         tyres=set(ds.df["tyre_id"]), compounds=set(ds.df["compound_id"]),
                         sizes=set(ds.df["size_id"]),
                         ranges={c: (float(ds.X[c].min()), float(ds.X[c].max())) for c in ds.X
                                 if ds.X[c].notna().any()},
                         reference=R["sessions"].get("reference", {}), n_train=len(ds.df))
        all_pos = np.arange(len(ds.df))
        for case in ("A", "B", "C"):
            spec_case = case
            if not any((case, t, mode) in best.index for t in LM):
                spec_case = "C"            # case not assessable on this data: use C's choice
            models = {}
            for t in LM + ["P_logB", "P_logD", "P_E"]:
                if t not in ds.Y[mode] or ds.Y[mode][t].notna().sum() < 10:
                    continue
                key = (spec_case, t if t in LM else "mu_peak", mode)
                if key not in best.index:
                    continue
                b = best.loc[key]
                cols = [c for c in S.FEATURE_SETS[b.feats] if c in ds.X.columns]
                y = ds.Y[mode][t].to_numpy(float)
                Xtr, ytr, w = _train_set(ds, all_pos, y, b.weighting, cols)
                m = make_model(b.model, seed=0).fit(Xtr, ytr, w)
                hw = float(b.interval_halfwidth_all) if t in LM else np.nan
                models[t] = dict(model=m, cols=cols, halfwidth=hw, r2=float(b.r2),
                                 spec=dict(case=spec_case, model=b.model, feats=b.feats,
                                           weighting=b.weighting))
            self.case_models[case] = models
            # curve route for this case
            # Route L by default: landmarks, B,C,D,E and the curve then agree
            # exactly. "best" picks the most accurate curve route instead.
            cs = M["curve_summary"]
            route = "L"
            band = None
            if len(cs):
                sub = cs[(cs.case == spec_case) & (cs["mode"] == mode)]
                if len(sub):
                    if route_policy == "best":
                        route = sub.sort_values("rmse_median").iloc[0].route
                    preds = M["curve_preds"].get((spec_case, mode), {})
                    if route in preds:
                        err = np.abs(ds.curves["binned"][mode] - preds[route]["curves"])
                        band = np.nanquantile(err, 0.9, axis=0)
                        band = (pd.Series(band).interpolate(limit_direction="both")
                                .rolling(9, center=True, min_periods=1).median().to_numpy())
            extra = {}
            if route == "F":
                Mfull = ds.curves["smooth"][mode]
                comp = ~np.isnan(Mfull).any(axis=1)
                mean, V = _pca(Mfull[comp])
                Z = np.full((len(ds.df), len(V)), np.nan)
                Z[comp] = (Mfull[comp] - mean) @ V.T
                b = best.loc[(spec_case, "mu_peak", mode)]
                cols = [c for c in S.FEATURE_SETS[b.feats] if c in ds.X.columns]
                ms = []
                for j in range(len(V)):
                    Xtr, ztr, w = _train_set(ds, all_pos, Z[:, j], b.weighting, cols)
                    ms.append(make_model(b.model).fit(Xtr, ztr, w))
                extra = dict(mean=mean, V=V, models=ms, cols=cols)
            self.case_route[case] = dict(route=route, band=band, extra=extra, spec_case=spec_case)
        return self

    # ---------------------------------------------------------------- predict
    def case_of(self, row):
        tyre = "|".join(str(row.get(k, "")) for k in S.TYRE_KEYS)
        if tyre in self.meta["tyres"]:
            return "A"
        if str(row.get("Group", "")) in self.meta["compounds"]:
            return "B"
        return "C"

    def _features(self, rows):
        df = rows.copy()
        for c in INPUT_COLS:
            if c not in df:
                df[c] = np.nan
        return io.build_features(df)

    def predict(self, rows):
        """rows: DataFrame (or list of dicts) with INPUT_COLS. Returns a list of
        result dicts (one per row)."""
        rows = pd.DataFrame(rows).reset_index(drop=True)
        X = self._features(rows)
        out = []
        for i, row in rows.iterrows():
            case = self.case_of(row)
            ms = self.case_models[case]
            x = X.iloc[[i]]
            lm, rng = {}, {}
            for t in LM:
                if t in ms:
                    v = float(ms[t]["model"].predict(x[ms[t]["cols"]])[0])
                    lm[t] = v
                    rng[t] = (v - ms[t]["halfwidth"], v + ms[t]["halfwidth"])
            surf = str(row.get("SurfaceType", ""))
            C = self.meta["C_table"].get(surf, np.nanmedian(list(self.meta["C_table"].values()) or [1.65]))
            rt = self.case_route[case]
            route = rt["route"]
            params = None
            curve = None
            if route == "P" and all(t in ms for t in ("P_logB", "P_logD", "P_E")) and "mu_lock" in lm:
                params = np.array([np.exp(ms["P_logB"]["model"].predict(x[ms["P_logB"]["cols"]])[0]), C,
                                   np.exp(ms["P_logD"]["model"].predict(x[ms["P_logD"]["cols"]])[0]),
                                   np.clip(ms["P_E"]["model"].predict(x[ms["P_E"]["cols"]])[0], pj.LO[3], pj.HI[3])])
                curve = pj.assemble_curves(params[None], [lm["mu_lock"]])[0]
            elif route == "F" and rt["extra"]:
                e = rt["extra"]
                z = np.array([m.predict(x[e["cols"]])[0] for m in e["models"]])
                curve = e["mean"] + z @ e["V"]
                f = pj.fit_curve(GRID, curve, C_fixed=None)
                params = np.array([f[p] for p in pj.PARAMS])
            approx = False
            if curve is None and all(t in lm for t in ("mu_peak", "SR_at_mupeak")) and \
                    ("mu_slide" in lm or "mu_lock" in lm):
                route = "L"
                slide = lm.get("mu_slide")
                if slide is None:          # master-only training: no µ slide available
                    slide, approx = lm["mu_lock"], True
                B, C_, D, E, _ = pj.params_from_landmarks(lm["mu_peak"], lm["SR_at_mupeak"], slide, C)
                params = np.array([B, C_, D, E])
                curve = pj.assemble_curves(params[None], [lm.get("mu_lock", np.nan)])[0]
            band = rt["band"] if rt["band"] is not None else np.full(len(GRID), np.nan)
            warn = self._warnings(row, X.iloc[i], case)
            if approx:
                warn.append("curve shape is approximate: trained without curve files, so µ at 90% slip "
                            "was taken equal to µ lock")
            res = dict(name=row.get("name", f"tyre {i + 1}"), case=case, route=route,
                       landmarks=lm, ranges=rng,
                       params=dict(zip(pj.PARAMS, params)) if params is not None else {},
                       curve=pd.DataFrame({"slip": GRID, "mu": curve if curve is not None else np.nan,
                                           "lo": (curve - band) if curve is not None else np.nan,
                                           "hi": (curve + band) if curve is not None else np.nan}),
                       models={t: ms[t]["spec"] for t in ms if t in LM}, warnings=warn,
                       basis="average test day" if self.meta["mode"] == "corrected" else "as tested")
            out.append(res)
        return out

    def _warnings(self, row, x, case):
        w = []
        for c, (lo, hi) in self.meta["ranges"].items():
            v = x.get(c, np.nan)
            if pd.notna(v) and (v < lo - 1e-9 or v > hi + 1e-9):
                w.append(f"{c} = {v:g} is outside the tested range {lo:g}-{hi:g}")
        missing = [c for c in S.DMA + S.FOOTPRINT if pd.isna(row.get(c, np.nan))]
        if missing and case != "A":
            w.append(f"{len(missing)} compound/footprint inputs missing (filled with typical values): "
                     + ", ".join(missing[:6]) + ("..." if len(missing) > 6 else ""))
        if str(row.get("SurfaceType", "")) not in self.meta["C_table"]:
            w.append(f"surface '{row.get('SurfaceType')}' was not in the training data")
        return w

    def summary(self):
        rows = []
        for case, ms in self.case_models.items():
            for t in LM:
                if t in ms:
                    s = ms[t]["spec"]
                    rows.append(dict(case=case, target=t, uses_case=s["case"], model=s["model"],
                                     feats=s["feats"], weighting=s["weighting"], r2=ms[t]["r2"],
                                     halfwidth=ms[t]["halfwidth"], route=self.case_route[case]["route"]))
        return pd.DataFrame(rows)

    def save(self, path):
        joblib.dump(self, path)

    @staticmethod
    def load(path):
        return joblib.load(path)
