"""Single-file HTML report: every graph comes with how to read it, what this
data shows, and what it means. All numbers in the text are computed."""
from __future__ import annotations

import base64
import html
import io as _io
import os
from datetime import date

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import plots as P
from . import schema as S
from .evaluation import CASE_LABELS, exp_key, make_splits, per_group_rmse, _train_set
from .models import MODEL_LABELS, make_model

E = html.escape


def _img(fig):
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _f(x, d=2):
    return "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def _pct(x):
    return "–" if x is None or not np.isfinite(x) else f"{100 * x:.0f}%"


class Doc:
    def __init__(self):
        self.parts, self.toc, self.nfig = [], [], 0

    def h2(self, anchor, title):
        self.toc.append((anchor, title))
        self.parts.append(f'<h2 id="{anchor}">{E(title)}</h2>')

    def h3(self, title):
        self.parts.append(f"<h3>{E(title)}</h3>")

    def p(self, text):
        self.parts.append(f"<p>{text}</p>")

    def html(self, raw):
        self.parts.append(raw)

    def fig(self, fig, title, read, shows, means):
        self.nfig += 1
        self.parts.append(
            f'<figure><div class="ftitle">Figure {self.nfig}. {E(title)}</div>'
            f'<img alt="{E(title)}" src="data:image/png;base64,{_img(fig)}">'
            f'<figcaption><p><b>How to read it.</b> {read}</p><p><b>What your data shows.</b> {shows}</p>'
            f'<p><b>What it means.</b> {means}</p></figcaption></figure>')

    def table(self, df, caption=None, max_rows=40, fmt=None):
        d = df.head(max_rows).copy()
        for c in d.columns:
            if c in ("ID", S.ID_COL):
                d[c] = d[c].map(lambda v: "" if pd.isna(v) else str(int(v)))
        if fmt:
            for c, f in fmt.items():
                if c in d:
                    d[c] = d[c].map(lambda v: f.format(v) if isinstance(v, (int, float, np.floating)) and np.isfinite(v) else ("" if pd.isna(v) else v))
        t = d.to_html(index=False, escape=True, border=0, na_rep="")
        if caption:
            i = t.index(">") + 1
            t = t[:i] + f"<caption>{E(caption)}</caption>" + t[i:]
        more = (f'<p class="note">Showing {max_rows} of {len(df)} rows; the full table is in the output folder.</p>'
                if len(df) > max_rows else "")
        self.parts.append(f'<div class="tw">{t}</div>{more}')


CSS = """
:root{--bg:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--line:#e1e0d9;--accent:#2a78d6;
--err:#d03b3b;--warn:#b07a00;--ok:#006300;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#898781;--line:#2c2c2a;--accent:#3987e5;--err:#e66767;--warn:#eda100;--ok:#0ca30c;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--line:#2c2c2a;
--accent:#3987e5;--err:#e66767;--warn:#eda100;--ok:#0ca30c;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1060px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:26px;margin:8px 0 4px}h2{font-size:20px;margin:44px 0 10px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 6px}.sub{color:var(--ink2);margin:0 0 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .l{color:var(--ink2);font-size:12.5px}.tile .v{font-size:24px;font-weight:600;margin-top:2px}
.tile .d{color:var(--muted);font-size:12px}
figure{margin:18px 0 26px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
figure img{display:block;max-width:100%;height:auto;margin:0 auto;background:#fcfcfb;border-radius:6px}
.ftitle{font-weight:600;margin-bottom:8px}figcaption p{margin:8px 0;color:var(--ink)}figcaption b{color:var(--ink)}
.tw{overflow-x:auto;margin:10px 0 18px}table{border-collapse:collapse;font-size:13px;width:100%}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--ink2);font-weight:600}td{font-variant-numeric:tabular-nums}caption{text-align:left;color:var(--ink2);padding:4px 0}
.note{color:var(--ink2);font-size:13px}.box{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:8px;padding:10px 14px;margin:14px 0}.box.warn{border-left-color:var(--warn)}.box.err{border-left-color:var(--err)}
nav ol{columns:2;color:var(--ink2);padding-left:0;list-style:none}nav a{color:var(--accent);text-decoration:none}
code{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:1px 4px;font-size:13px}
ul.rec li{margin:6px 0}
@media (max-width:640px){nav ol{columns:1}.tile .v{font-size:20px}}
"""


# ----------------------------------------------------------------------------
# Extra analyses used only by the report
# ----------------------------------------------------------------------------
def _imbalance(R, ds):
    M = R["models"]
    best, oof = M["best"], M["oof"]
    mode = "raw"
    for case in ("C", "B", "A"):
        if (case, "mu_peak", mode) not in best.index:
            continue
        model = best.loc[(case, "mu_peak", mode)].model
        if model == "surface_mean":
            model = "ridge"
        out = {}
        for w in ("none", "balanced", "config_mean"):
            k = exp_key(dict(case=case, target="mu_peak", model=model, feats="all", weighting=w, mode=mode, seed=0))
            if k in oof:
                out[w] = per_group_rmse(ds, "mu_peak", mode, oof[k].astype(float))
        if len(out) >= 2 and len(list(out.values())[0]) >= 2:
            return case, model, out
    return None, None, {}


def _importance(R, ds, case, mode, n_rep=3):
    best = R["models"]["best"]
    if (case, "mu_peak", mode) not in best.index:
        return None
    b = best.loc[(case, "mu_peak", mode)]
    if b.model == "surface_mean":
        return None
    cols = [c for c in S.FEATURE_SETS[b.feats] if c in ds.X.columns]
    y = ds.Y[mode]["mu_peak"].to_numpy(float)
    folds, _ = make_splits(ds.df, case, 0)
    rng = np.random.default_rng(0)
    gains = {c: [] for c in cols}
    for k, (tr, te) in enumerate(folds):
        te = te[np.isfinite(y[te])]
        if len(te) < 5:
            continue
        Xtr, ytr, w = _train_set(ds, tr, y, b.weighting, cols)
        m = make_model(b.model, seed=k).fit(Xtr, ytr, w)
        Xte = ds.X.iloc[te][cols].copy()
        base = np.sqrt(np.mean((m.predict(Xte) - y[te]) ** 2))
        for c in cols:
            for _ in range(n_rep):
                Xp = Xte.copy()
                Xp[c] = rng.permutation(Xp[c].to_numpy())
                gains[c].append(np.sqrt(np.mean((m.predict(Xp) - y[te]) ** 2)) - base)
    imp = pd.DataFrame({c: (np.mean(v), np.std(v)) for c, v in gains.items() if v}, index=["mean", "sd"]).T
    return imp, b


def _test_plan(R, ds, top=5):
    df = ds.df
    tab = pd.crosstab(df["compound_id"], df["size_id"])
    one = tab[(tab > 0).sum(axis=1) == 1]
    if not len(one):
        return pd.DataFrame()
    sizes = df["size_id"].value_counts().index.tolist()
    rows = []
    for comp in one.sum(axis=1).sort_values(ascending=False).index[:top]:
        has = one.columns[one.loc[comp] > 0][0]
        other = [s for s in sizes if s != has][:2]
        rows.append({"compound": comp, "tested in": has, "tests so far": int(one.loc[comp].sum()),
                     "suggested extra sizes": ", ".join(other)})
    return pd.DataFrame(rows)


def _example_inputs(ds):
    df = ds.df
    ex = []
    ref = df["tyre_id"].value_counts().index[0]
    cnt = df["tyre_id"].value_counts()
    cand = df[df["tyre_id"].isin(cnt.index[1:])]
    if len(cand):
        r = cand.iloc[len(cand) // 2].copy()
        loads = sorted(df["Load_N"].dropna().unique())
        r["Load_N"] = loads[len(loads) // 2] if r["Load_N"] != loads[len(loads) // 2] else loads[0]
        r["name"] = f"{r['TyreSize']} {r['Pattern']} {r['Group']} at {r['Load_N']:.0f} N"
        ex.append(r)
    r = df[df["tyre_id"] != ref].iloc[0].copy()
    r["Group"] = "NEW-COMPOUND"
    r["Pattern"] = "NEW-PATTERN"
    r["name"] = f"new compound on {r['TyreSize']} ({r['SurfaceType']})"
    ex.append(r)
    return pd.DataFrame(ex)


# ----------------------------------------------------------------------------
def build_report(R, out_dir, title="Tyre µ-slip traction analysis"):
    ds = R["dataset"]
    df = ds.df
    st, M = R["stats"], R["models"]
    flags, sess, fits = R["flags"], R["sessions"], R["fits"]
    best = M["best"]
    targets = st["targets"]
    has_curves = bool(R.get("clean_curves")) and len(fits) > 0
    curves_raw = R.get("clean_curves", {})
    ov = st["overview"]
    ceil = st["ceiling"]["raw"]
    mode_best = "corrected" if "corrected" in ds.Y else "raw"
    doc = Doc()

    # ------------------------------------------------------------ summary
    def best_r2(case, t="mu_peak", mode="raw"):
        return best.loc[(case, t, mode)].r2 if (case, t, mode) in best.index else np.nan

    n_err = int((flags.severity == "error").sum())
    n_warn = int((flags.severity == "warning").sum())
    tiles = [
        ("Tests analysed", f"{len(df)}", f"{df['size_id'].nunique()} sizes · {df['compound_id'].nunique()} compounds"),
        ("Curve files used", f"{R.get('n_curves_used', 0)}", "matched by ID" if has_curves else "master-only run"),
        ("Data issues", f"{n_err} errors", f"{n_warn} warnings"),
        ("Best possible R², peak µ", _f(ceil.loc["mu_peak", "ceiling_r2_test"]) if "mu_peak" in ceil.index else "–",
         "per test, set by repeat scatter"),
        ("Leaky random split, peak µ", _f(best_r2("random")), "what a naive test reports"),
    ]
    for c in ("A", "B", "C"):
        tiles.append((f"Case {c}, peak µ R²", _f(best_r2(c, mode=mode_best)),
                      CASE_LABELS[c].split(": ")[1] + ("" if mode_best == "raw" else ", day-corrected")))
    doc.html('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="l">{E(a)}</div><div class="v">{E(b)}</div><div class="d">{E(c)}</div></div>'
        for a, b, c in tiles) + "</div>")

    key = []
    if "mu_peak" in ceil.index:
        key.append(f"Repeat tests of the same tyre under the same conditions differ by about "
                   f"<b>{_f(ceil.loc['mu_peak', 'within_sd'], 3)}</b> in peak µ. That alone caps the R² of any model "
                   f"at <b>{_f(ceil.loc['mu_peak', 'ceiling_r2_test'])}</b> for single tests "
                   f"(<b>{_f(ceil.loc['mu_peak', 'ceiling_r2_config'])}</b> for averages of repeats).")
    if "SR_at_mupeak" in ceil.index:
        key.append(f"Slip at peak µ is mostly measurement scatter (ceiling R² "
                   f"{_f(ceil.loc['SR_at_mupeak', 'ceiling_r2_test'])}); predict it as a typical value with a wide range.")
    if sess.get("reference"):
        lad = st["ladder"]
        day = lad.loc["mu_peak", "test_day"] if "test_day" in lad and "mu_peak" in lad.index else np.nan
        key.append(f"A reference tyre is run between candidates. Its level drifts between sessions, and candidate "
                   f"tyres move with it (r = {_f(sess['evidence'].get('mu_peak'))}). The test day accounts for about "
                   f"<b>{_pct(day)}</b> of the variation in peak µ. Results are therefore also given day-corrected.")
    for c in ("A", "B", "C"):
        r_ = best_r2(c, mode=mode_best)
        if np.isfinite(r_):
            key.append(f"{CASE_LABELS[c]}: peak µ R² <b>{_f(r_)}</b> on held-out tyres.")
        else:
            note = make_splits(df, c)[1]
            key.append(f"{CASE_LABELS[c]}: <i>not assessable on this data.</i> {E(note)}")
    doc.html('<div class="box"><b>Key findings</b><ul>' + "".join(f"<li>{k}</li>" for k in key) + "</ul></div>")

    # ------------------------------------------------------------ 1 data
    doc.h2("data", "1. The data")
    mr = R.get("match_report")
    if mr:
        doc.p(f"The master has <b>{mr['n_master']}</b> distinct IDs; <b>{mr['n_files']}</b> curve files were found and "
              f"<b>{mr['n_matched']}</b> matched an ID (rule: the number at the start of the file name). "
              f"{len(mr['master_without_file'])} master rows have no file, {len(mr['files_without_master'])} files have no "
              f"master row, {len(mr['duplicate_file_ids'])} IDs have more than one file.")
    big = ov.iloc[0]
    doc.fig(P.inventory(ov), "Tests per tyre size",
            "Each bar is one tyre size; its length is the number of tests, split into wet and dry. The label also gives "
            "the number of distinct configurations (tyre × surface × load × pressure × speed).",
            f"<b>{ov.index[0]}</b> has {int(big.tests)} tests ({_pct(big.share_tests)} of all), while the smallest size "
            f"has {int(ov.tests.min())}. Many tests are repeats: {len(df)} tests come from only "
            f"{df['config_id'].nunique()} configurations.",
            "A model trained on all tests sees the big sizes far more often. Section 5 measures whether that makes "
            "predictions for the rare sizes worse, and what helps.")
    tab, n_multi, n_comp = st["confounding"]
    doc.fig(P.confounding_map(tab), "Compound groups across sizes",
            "Rows are compound groups, columns are sizes; a coloured cell means that compound was tested in that size "
            "(the number is the test count). Empty cells are combinations never tested.",
            f"<b>{n_multi} of {n_comp}</b> compound groups were tested in more than one size.",
            "When a compound only ever appears in one size, the data cannot separate 'this compound grips more' from "
            "'this size grips more'. Predicting a known compound in a new size (case B) needs compounds tested in "
            "several sizes; section 8 lists which tests would help most." if n_multi < 3 else
            "Compounds tested in several sizes let the model separate compound and size effects (case B).")
    doc.fig(P.condition_coverage(df), "Test-condition coverage",
            "Histograms of load, inflation pressure, speed and surface temperature for wet and dry tests.",
            "Load " + f"{df.Load_N.min():.0f}-{df.Load_N.max():.0f} N, pressure {df.IP_kPa.min():.0f}-{df.IP_kPa.max():.0f} kPa, "
            f"speed {df.Speed_kph.min():.0f}-{df.Speed_kph.max():.0f} km/h.",
            "Predictions are only trustworthy inside these ranges; the predictor warns when an input is outside them.")

    # ------------------------------------------------------------ 2 quality
    doc.h2("quality", "2. Data quality and recommendations")
    doc.p("Every master row and curve file was checked. <b>Errors</b> make a value unusable (it is excluded or "
          "replaced); <b>warnings</b> are usable but worth a look; <b>info</b> items were corrected automatically. "
          "The full list is in <code>data_quality_flags.csv</code>.")
    if (flags.check == "linkage_suspect").any():
        doc.html('<div class="box err"><b>File linkage looks wrong.</b> Most curve files disagree with their master '
                 'rows. Check that the number at the start of each file name is the Sl No (or set <code>--id-regex</code>) '
                 'before using any curve-based result.</div>')
    doc.fig(P.qc_summary(flags), "Data-quality findings",
            "One bar per type of finding, coloured by severity (red error, amber warning, grey info).",
            "; ".join(f"{E(k.replace('_', ' '))}: {v}" for k, v in flags.groupby("check").size().sort_values(ascending=False).head(8).items()) + ".",
            "Errors are already excluded from the analysis. Fixing them at the source (master typos, missing or "
            "unreadable files, duplicate IDs) recovers those tests for future runs.")
    if has_curves:
        doc.fig(P.qc_examples(R.get("raw_curves", curves_raw), flags, R["desc"]), "What flagged curves look like",
                "Raw points of one example per finding type, next to a typical clean curve (blue).",
                "Use these to judge whether a flag is a real problem on your rig.",
                "Spikes are removed automatically; curves that stop before lock lose µ lock; noisy curves still "
                "count but carry more scatter.")
        doc.fig(P.master_vs_curve(R["master_raw"], R["desc"]),
                "Master landmarks vs values read from the curve files",
                "Each point is one test. x: value read from its curve file; y: value stored in the master. Points on "
                "the diagonal agree; the grey band is ±0.05. Red points disagree and are labelled with their ID.",
                f"{int((flags.check == 'master_curve_mismatch').sum())} disagreements.",
                "A disagreement means a typo in the master, a wrongly named file, or a different processing rule. "
                "The analysis uses the curve value because it is defined the same way for every test.")
    if "mu_peak" in df:
        doc.fig(P.repeats_strip(df.assign(mu_peak=ds.Y["raw"]["mu_peak"]), "mu_peak", flags),
                "Spread of repeat tests",
                "Each row is one configuration tested 3+ times; dots are individual tests, the black tick is the median. "
                "Red dots are flagged as outliers (more than 4× the typical repeat scatter from their median).",
                f"{int((flags.check == 'repeat_outlier').sum())} tests flagged as repeat outliers.",
                "Check the test log for these (surface condition, tyre damage, rig problems). They are kept in the "
                "analysis; remove them at the source if a cause is found.")
    top = flags[flags.severity.isin(["error", "warning"]) & (flags.check != "nonnumeric_cell")]
    if len(top):
        doc.h2("qc-table", "2a. Items to review")
        doc.table(top[["ID", "check", "severity", "detail", "action"]], max_rows=40)

    # ------------------------------------------------------------ 3 statistics
    doc.h2("stats", "3. Statistical analysis")
    doc.fig(P.distributions(df, ds.Y["raw"], targets), "Distribution of the landmarks",
            "Histograms of each landmark for wet and dry tests.",
            "; ".join(f"{S.TARGET_LABELS.get(t, t)}: median {ds.Y['raw'][t].median():.3f}" for t in targets) + ".",
            "Dry/wet is usually the largest single difference; models must get it right before anything else.")
    c = ceil
    doc.fig(P.ceiling_bars(c), "Best achievable accuracy (noise ceiling)",
            "For each landmark: the highest R² any model could reach, given how much repeat tests of the same "
            "configuration disagree. Blue: predicting a single test; orange: predicting the average of the repeats.",
            "; ".join(f"{S.TARGET_LABELS.get(t, t)} {_f(c.loc[t, 'ceiling_r2_test'])} / {_f(c.loc[t, 'ceiling_r2_config'])}"
                      for t in c.index) + " (single test / average).",
            "A target of R² ≥ 0.9 is only meaningful where the ceiling is above 0.9. Below it, the honest goal is to "
            "get close to the ceiling, and to reduce test scatter (more repeats, reference-tyre correction, denser "
            "slip sampling near the peak).")
    lad = st["ladder"]
    if len(lad):
        r0 = lad.loc["mu_peak"] if "mu_peak" in lad.index else lad.iloc[0]
        doc.fig(P.ladder(lad), "Where the variation comes from",
                "Each bar splits a landmark's total variation into parts. Shared variation between correlated "
                "parts (e.g. conditions that changed with the test day) is divided fairly (Shapley split). "
                "Grey is scatter between repeat tests, which no model can predict.",
                "Peak µ: " + ", ".join(f"{lab.split(' (')[0].lower()} {_pct(r0.get(k, np.nan))}" for k, lab in P.LADDER_COLS
                                       if k in lad.columns) + ".",
                "Test conditions and dry/wet are learnable in every case. The 'tyre' part is what must be predicted "
                "from compound, footprint and size data for an untested tyre: the larger it is, the more those inputs "
                "matter. A large 'test day' part means session drift should be corrected with the reference tyre.")
    eff = st["effects"]
    show_t = [t for t in ("mu_peak", "mu_lock") if t in eff]
    if show_t:
        sig = []
        for t in show_t:
            e = eff[t].dropna(subset=["effect"])
            for _, r in e.iterrows():
                if r.lo > 0 or r.hi < 0:
                    sig.append(f"{S.TARGET_LABELS[t]} {r.effect:+.3f} {r.label} {r.condition.split('_')[0].lower()} ({r.surface})")
        nv = sorted(set(eff[show_t[0]].loc[eff[show_t[0]].note != "", "condition"]))
        doc.fig(P.effects_plot(eff, show_t), "Effect of test conditions (same tyre)",
                "Each dot is how much the landmark changes per practical step of one condition, comparing tests of "
                "the same tyre only, with the other conditions held fixed. Bars are 95% ranges; a bar crossing zero "
                "means no clear effect.",
                ("Clear effects: " + "; ".join(sig[:8]) + "." if sig else "No condition has a clear effect.") +
                (f" Not estimable (never varied within a tyre): {', '.join(nv)}." if nv else ""),
                "These are the trends the models can rely on for new operating points (case A). Conditions never "
                "varied within a tyre can only be learned across different tyres, where they are mixed up with "
                "compound and size.")
    Rc, Pc, dist = st["correlations"]
    if "mu_peak" in Rc:
        topc = Rc["mu_peak"].dropna().abs().sort_values(ascending=False).head(4)
        doc.fig(P.correlation_map(Rc, dist), "Correlation of inputs with results",
                "Spearman correlation between each input and each landmark, one point per configuration (so repeats "
                "do not inflate it). Blue = higher input goes with lower result, red = higher with higher. The number "
                "after each input is how many distinct values it has: compound (DMA) data has one value per compound.",
                "Strongest with peak µ: " + ", ".join(f"{k} ({Rc.loc[k, 'mu_peak']:+.2f})" for k in topc.index) + ".",
                "Correlation is not cause: inputs that changed together (e.g. temperature and test day) share their "
                "correlation. Inputs with few distinct values rest on few compounds and need care.")
    if sess.get("reference"):
        rf = sess["reference"]
        fac = sess["factors"]["mu_peak"]
        doc.fig(P.sessions_plot(df, ds.Y["raw"], sess), "Test-day drift and the reference tyre",
                "Left: peak µ of the reference tyre in test order (Sl No); the line joins its block averages. Right: "
                "each candidate block's average against the reference level at the time it was tested.",
                f"Reference tyre: {', '.join(f'{k}: {E(v)}' for k, v in rf.items())}. Candidates follow the reference "
                f"with r = {_f(sess['evidence'].get('mu_peak'))} (peak µ) and r = {_f(sess['evidence'].get('mu_lock'))} "
                f"(µ lock). Day corrections range {fac.min():.3f}-{fac.max():.3f} (rule: '{sess['method']}').",
                "Part of the differences between tyres is just the day they were tested. Day-corrected values "
                "(value × overall reference average / reference level that day) compare tyres on an equal footing and "
                "are what the predictor targets. This assumes Sl No follows test order.")
    if has_curves:
        doc.fig(P.mean_curves(df, ds.curves["smooth"]["raw"]), "Typical measured curves",
                "Median µ at each slip for wet and dry tests, with the band holding 80% of tests.",
                "Curves rise steeply to a knee at a few percent slip, peak, decline slowly, then drop sharply in the "
                "last ~10% of slip as the wheel locks.",
                "The final drop is why the Magic Formula is fitted only up to 90% slip and µ lock is kept as its "
                "own landmark (section 4).")

    # ------------------------------------------------------------ 4 Pacejka
    if has_curves:
        doc.h2("pacejka", "4. Pacejka (Magic Formula) fitting")
        fr = fits["free"]
        Ct = fits["C_table"]
        doc.p("Each curve was fitted with µ = D·sin(C·atan(B·s − E·(B·s − atan(B·s)))) on slip 0-0.90, once with "
              f"B, C, D, E free and once with C fixed per surface (median of the free fits: "
              f"{', '.join(f'{k} {v:.2f}' for k, v in Ct.items())}). D equals peak µ, B·C·D the initial slope.")
        doc.fig(P.fit_quality(fr, curves_raw, None), "How well the Magic Formula fits",
                "Left: fit error of every curve. Right: the best, a typical and the worst fit (points measured, "
                "line fitted).",
                f"Median fit error {fr.fit_rmse.median():.3f} µ; {int((fr.fit_r2 < 0.97).sum())} curves fit poorly "
                "(R² < 0.97).",
                "Where the fit misses, it is usually the sharp knee at 2-3% slip; the formula's shape is smoother "
                "than real curves there.")
        icc = st.get("icc", pd.Series(dtype=float))
        if len(icc):
            def g(k):
                return icc.get(k, np.nan)
            doc.fig(P.icc_bars(icc), "Which numbers repeat between repeat tests",
                    "Repeatability (intraclass correlation) of each fitted parameter and each landmark: 1 = repeat "
                    "tests give the same value, 0 = the value is pure scatter.",
                    f"Free fit: B {_f(g(('free', 'B')))}, C {_f(g(('free', 'C')))}, D {_f(g(('free', 'D')))}, "
                    f"E {_f(g(('free', 'E')))}. Landmarks: peak µ {_f(g(('landmark', 'mu_peak')))}, slip at peak "
                    f"{_f(g(('landmark', 'SR_at_mupeak')))}, µ lock {_f(g(('landmark', 'mu_lock')))}.",
                    "Parameters with low repeatability cannot be predicted well: much of their value is how the "
                    "optimiser traded B, C and E against each other on that particular test. Predicting the "
                    "repeatable landmarks and converting them exactly to B, C, D, E (route L) avoids this; route P "
                    "predicts B, D, E directly and is compared in section 6.")
            doc.fig(P.param_tradeoff(fr, df), "Parameter trade-offs",
                    "Each dot is one curve's fitted parameters.",
                    f"Correlation C-E {fr[['C', 'E']].corr().iloc[0, 1]:+.2f}, B-E {fr[['B', 'E']].corr().iloc[0, 1]:+.2f}.",
                    "Strongly correlated parameters compensate for each other: several combinations draw nearly the "
                    "same curve. That is the identifiability problem.")

    # ------------------------------------------------------------ 5 splits
    doc.h2("splits", "5. Train-test split and unbalanced sizes")
    doc.p("<b>How the data is split decides what the score means.</b> A random split puts repeat tests of the same "
          "tyre in both training and test, so the model only has to recognise a tyre it has already seen. "
          "Each case below hides exactly what would be unknown in practice:")
    doc.html("<ul><li><b>A, tested tyre at a new condition</b>: whole configurations are hidden; scored only where the "
             "same tyre was tested at other conditions.</li><li><b>B, known compound in a new size</b>: one size at a "
             "time is hidden; scored on compounds also tested in another size.</li><li><b>C, new compound</b>: whole "
             "compound groups are hidden, in every size.</li></ul>")
    cr = ceil.loc["mu_peak", "ceiling_r2_test"] if "mu_peak" in ceil.index else np.nan
    doc.fig(P.leak_bars(best, cr), "The same data scored four ways",
            "Bars: R² for peak µ of the best model in each case (model name under the value). Dashed: the ceiling.",
            f"Random split {_f(best_r2('random'))}; " + ", ".join(
                f"case {c} {_f(best_r2(c))}" for c in ("A", "B", "C")) + " (raw values).",
            "The random split overstates accuracy for untested tyres. Quote the case that matches your question.")
    doc.fig(P.imbalance_share(ov), "Tests vs information per size",
            "For each size: its share of all tests (blue) and of distinct configurations (orange).",
            f"{ov.index[0]}: {_pct(ov.iloc[0].share_tests)} of tests but {_pct(ov.iloc[0].share_configs)} of configurations.",
            "Repeats add weight without adding new information about how tyres differ.")
    case_i, model_i, per = _imbalance(R, ds)
    doc.h3("Does the model learn more from the majority sizes?")
    if per:
        base = per["none"]
        small = (base["n"] <= base["n"].median())
        names = {"none": "every test counts once", "balanced": "sizes re-weighted",
                 "config_mean": "repeats averaged first"}
        sm, lg = {}, {}
        for w, t in per.items():
            sm[w] = t.loc[small.reindex(t.index).fillna(False).astype(bool), "rmse"].mean()
            lg[w] = t.loc[~small.reindex(t.index).fillna(True).astype(bool), "rmse"].mean()
        txt = "; ".join(f"{names[w]}: smaller sizes RMSE {sm[w]:.3f}, larger sizes {lg[w]:.3f}" for w in per)
        gap = sm["none"] - lg["none"]
        best_w = min(sm, key=sm.get)
        concl = (f"With every test counted once, the smaller sizes are predicted worse than the larger ones "
                 f"(RMSE {sm['none']:.3f} vs {lg['none']:.3f}). " if gap > 0.003 else
                 "Smaller sizes are not predicted noticeably worse than larger ones here. ")
        if best_w != "none" and sm["none"] - sm[best_w] > 0.002:
            concl += (f"'{names[best_w].capitalize()}' lowers the smaller sizes' error to {sm[best_w]:.3f}"
                      f" (larger sizes: {lg[best_w]:.3f}).")
        else:
            concl += ("Neither re-weighting nor averaging repeats reduces the smaller sizes' error on this data: "
                      "their larger error comes mainly from having few tyres to learn from, not from being "
                      "outvoted. More tests on those sizes is the remedy.")
        doc.fig(P.imbalance_error(per, f"Error per size, case {case_i}, {MODEL_LABELS.get(model_i, model_i)}"),
                "Prediction error per size, three ways of handling imbalance",
                "Each dot is one size: x = how many tests it has (log scale), y = the prediction error for its tyres "
                "when they were hidden. Colours compare training on every test, re-weighting sizes, and averaging "
                "repeats first.",
                txt + ".",
                "Models minimise the average error over training rows, so sizes and tyres with many rows pull the fit "
                "toward themselves. " + concl + " The grid in section 6 tries all three options for every model and "
                "keeps whichever is best per case and landmark.")
    else:
        doc.p("Not enough sizes could be assessed to compare imbalance handling on this data.")

    # ------------------------------------------------------------ 6 models
    doc.h2("models", "6. Model results")
    led = M["ledger"]
    for t in [t for t in ("mu_peak", "mu_lock", "mu_slide", "SR_at_mupeak") if t in set(led.target)]:
        ct = ceil.loc[t, "ceiling_r2_test"] if t in ceil.index else np.nan
        bt = best.reset_index()
        bt = bt[(bt.target == t) & (bt["mode"] == "raw") & (bt.case != "random")]
        doc.fig(P.leaderboard(led, t, "raw", ct), f"{S.TARGET_LABELS[t]}: models compared",
                "For each case, the best R² of each model across the three imbalance options. Grey is the dry/wet "
                "average baseline; dashed is the ceiling.",
                "; ".join(f"case {r.case}: {MODEL_LABELS[r.model]} {_f(r.r2)} (RMSE {_f(r.rmse, 3)})"
                          for r in bt.itertuples()) + ".",
                "A model is only useful where it clearly beats the baseline. Gaps between models are often within "
                "noise when few tyres are held out.")
    tb = best.reset_index()
    tb = tb[tb.case != "random"][["case", "target", "mode", "model", "weighting", "r2", "r2_config", "rmse",
                                   "interval_coverage", "interval_width", "n_scored"]]
    tb["model"] = tb["model"].map(MODEL_LABELS)
    doc.h3("Best model per case and landmark")
    doc.table(tb.sort_values(["case", "target", "mode"]), max_rows=60,
              fmt={c: "{:.3f}" for c in ("r2", "r2_config", "rmse", "interval_coverage", "interval_width")})
    doc.p("<span class='note'>r2: per test; r2_config: on averages of repeats; interval_coverage: share of held-out "
          "tests inside the 90% range (should be close to 0.90); interval_width: width of that range in µ.</span>")
    y = ds.Y[mode_best]["mu_peak"].to_numpy(float)
    pr, hw = {}, {}
    for c in ("A", "B", "C"):
        if (c, "mu_peak", mode_best) in best.index:
            b = best.loc[(c, "mu_peak", mode_best)]
            k = exp_key(dict(case=c, target="mu_peak", model=b.model, feats=b.feats, weighting=b.weighting,
                             mode=mode_best, seed=0))
            if k in M["oof"]:
                pr[c] = M["oof"][k].astype(float)
                hw[c] = b.interval_halfwidth_all
    if pr:
        doc.fig(P.parity(y, pr, hw, f"Peak µ: predicted vs measured for hidden tyres ({mode_best})"),
                "Predicted vs measured peak µ",
                "Each dot is one test whose tyre (case A: configuration; B: size; C: compound) was hidden from "
                "training. The diagonal is perfect prediction; the shaded band is the 90% prediction range.",
                "; ".join(f"case {c}: ±{hw[c]:.3f}" for c in hw) + " (90% range half-width).",
                "The band width is the practical precision to expect for an untested tyre in each case.")
    imp_case = next((c for c in ("C", "B", "A") if (c, "mu_peak", mode_best) in best.index), None)
    if imp_case:
        res = _importance(R, ds, imp_case, mode_best)
        if res is not None:
            imp, b = res
            topi = imp.sort_values("mean", ascending=False).head(5)
            dom = ""
            if len(topi) > 1 and topi["mean"].iloc[0] > 3 * max(topi["mean"].iloc[1], 1e-9):
                dom = (f" {topi.index[0]} dominates (+{topi['mean'].iloc[0]:.3f}) and is left out of the chart so "
                       "the other inputs are visible.")
                imp = imp.drop(index=topi.index[0])
            doc.fig(P.importance_bars(imp, f"What the case-{imp_case} model relies on (peak µ)"),
                    "Input importance",
                    "How much the error on hidden tyres grows when one input is scrambled. Longer bar = the model "
                    "leans on it more. Whiskers show variation across folds.",
                    "Top inputs: " + ", ".join(f"{k} (+{v:.3f})" for k, v in topi["mean"].items()) + "." + dom,
                    "Inputs at the top must be measured for a new tyre; when they are missing the predictor fills "
                    "typical values and its accuracy falls back toward the baseline.")
    cs = M["curve_summary"]
    if len(cs):
        doc.fig(P.route_bars(cs, mode_best), "Whole-curve accuracy by method",
                "Median error between predicted and measured curves of hidden tests, for three ways of building the "
                "curve: landmarks → Magic Formula (L), B, D, E predicted directly (P), and learned curve shapes (F).",
                "; ".join(f"case {c}: best {cs[(cs.case == c) & (cs['mode'] == mode_best)].sort_values('rmse_median').iloc[0].route} "
                          f"({cs[(cs.case == c) & (cs['mode'] == mode_best)].rmse_median.min():.3f})"
                          for c in ("A", "B", "C") if ((cs.case == c) & (cs['mode'] == mode_best)).any()) + ".",
                "The predictor uses route L, so the curve passes exactly through the predicted landmarks and the "
                "reported B, C, D, E describe that same curve. Routes P and F are shown for comparison; if one is "
                "clearly better on your data, train the predictor with route_policy='best'.")
        ccase = next((c for c in ("C", "B", "A") if (c, mode_best) in M["curve_preds"]), None)
        if ccase:
            preds = M["curve_preds"][(ccase, mode_best)]
            sub = cs[(cs.case == ccase) & (cs["mode"] == mode_best)].sort_values("rmse_median")
            route = sub.iloc[0].route
            pdict = preds[route]
            meas = ds.curves["binned"][mode_best]
            band = np.nanquantile(np.abs(meas - pdict["curves"]), 0.9, axis=0)
            band = pd.Series(band).interpolate(limit_direction="both").to_numpy()
            raw = curves_raw if mode_best == "raw" else {
                sid: (sr, mu * sess["factors"]["mu_peak"].get(sid, 1.0)) for sid, (sr, mu) in curves_raw.items()}
            doc.fig(P.curve_examples(meas, pdict, band, df[S.ID_COL].tolist(), raw,
                                     f"Hidden-tyre curves, case {ccase}, route {route}"),
                    "Predicted vs measured curves",
                    "Six hidden tests from best to worst prediction: grey points measured, blue line predicted, shaded "
                    "90% band.",
                    f"Median curve error {pdict['rmse'][np.isfinite(pdict['rmse'])].mean():.3f} µ (mean over tests).",
                    "The band is wide where tests scatter most: around the knee and in the lock-up zone.")

    # ------------------------------------------------------------ 7 predictor
    doc.h2("predictor", "7. Predicting an untested tyre")
    doc.p("The saved predictor (<code>predictor.joblib</code>) decides the case for each new tyre (A if the same "
          "size+pattern+compound was tested, B if the compound was tested in another size, C otherwise), uses the "
          "best model for that case, and returns the landmarks with 90% ranges, B, C, D, E and the full curve, "
          "built from the landmarks so that all of them agree exactly. "
          + ("Predictions are for an <b>average test day</b> (day-corrected)." if mode_best == "corrected" else ""))
    try:
        from .predictor import TractionPredictor
        pred = TractionPredictor.load(os.path.join(out_dir, "predictor.joblib"))
        ex = _example_inputs(ds)
        for r in pred.predict(ex):
            rows = [(S.TARGET_LABELS[t], f"{v:.3f}", f"{r['ranges'][t][0]:.3f} – {r['ranges'][t][1]:.3f}")
                    for t, v in r["landmarks"].items()]
            doc.fig(P.prediction_card(r), f"Example prediction: {r['name']}",
                    "Predicted curve with its 90% band; the dot marks the predicted peak.",
                    f"Case {r['case']}, curve method {r['route']}. " + "; ".join(f"{a} {b} (90%: {c})" for a, b, c in rows)
                    + ". B, C, D, E = " + ", ".join(f"{k} {v:.3f}" for k, v in r["params"].items()) + ".",
                    ("Warnings: " + "; ".join(E(w) for w in r["warnings"]) + "." if r["warnings"] else
                     "Inputs are inside the tested ranges."))
    except Exception as ex:  # report must not fail on the demo
        doc.p(f"<span class='note'>Example prediction unavailable: {E(str(ex))}</span>")
    doc.table(R["predictor_summary"].assign(model=lambda d: d.model.map(MODEL_LABELS)),
              "Models used by the predictor", fmt={"r2": "{:.3f}", "halfwidth": "{:.3f}"})

    # ------------------------------------------------------------ 8 recommendations
    doc.h2("recs", "8. Recommendations")
    recs = []
    if n_err:
        recs.append(f"<b>Fix the {n_err} data errors at the source</b> (see section 2a): duplicate IDs, out-of-range "
                    "master values, unreadable files. Each fix returns a test to the analysis.")
    if "mu_peak" in ceil.index:
        wsd = ceil.loc["mu_peak", "within_sd"]
        recs.append(f"<b>Repeat scatter is the hard limit.</b> Peak µ repeats differ by {wsd:.3f}. The average of n "
                    f"repeats is uncertain by {wsd:.3f}/√n: {wsd / np.sqrt(3):.3f} for 3, {wsd / np.sqrt(6):.3f} for 6. "
                    "Report and model averages of repeats, and judge models against the ceiling, not against 1.0.")
    if sess.get("reference"):
        recs.append("<b>Use the reference tyre to correct for the test day.</b> Candidates move with the reference "
                    f"(r = {_f(sess['evidence'].get('mu_peak'))}). Keep running it at least once per session, ideally "
                    "before and after each candidate block.")
    else:
        recs.append("<b>Run a reference tyre in every test session.</b> No reference tyre was detected; without it "
                    "day-to-day surface drift stays in the data as noise.")
    if "SR_at_mupeak" in ceil.index and ceil.loc["SR_at_mupeak", "ceiling_r2_test"] < 0.5:
        recs.append("<b>Slip at peak µ is dominated by scatter.</b> The rig records only a few points between 3% and "
                    "30% slip, so the peak location is coarse. Denser sampling or a slower sweep around the peak would "
                    "make it predictable; until then use typical values per surface with the reported range.")
    if not make_splits(df, "A")[0]:
        recs.append("<b>Test a few tyres at 2-3 loads or speeds.</b> Every tyre was tested at a single condition, so "
                    "the effect of load/speed for a given tyre (case A) cannot be learned or checked.")
    plan = _test_plan(R, ds)
    if n_multi < max(3, 0.2 * n_comp) and len(plan):
        recs.append("<b>Break the compound-size link.</b> Testing a few existing compounds in additional sizes "
                    "is the cheapest way to make case B (and C) predictable. Suggested first tests below.")
    recs.append("<b>Always measure DMA and footprint for a new tyre</b> before predicting: they carry the compound "
                "information, and the predictor's accuracy for new compounds depends on them.")
    recs.append("<b>Quote accuracy per case</b> (A/B/C) and never from a random split of tests.")
    doc.html('<ul class="rec">' + "".join(f"<li>{r}</li>" for r in recs) + "</ul>")
    if len(plan) and n_multi < max(3, 0.2 * n_comp):
        doc.table(plan, "Suggested tests: existing compounds in additional sizes")

    # ------------------------------------------------------------ 9 method
    doc.h2("method", "9. Method notes")
    doc.html(
        "<ul><li>Curve files are matched to master rows by the number at the start of the file name; slip in percent "
        "and negative sign conventions are corrected automatically; isolated spikes are removed.</li>"
        "<li>Landmarks are read from each curve the same way for every test: peak µ and its slip from the lightly "
        "smoothed curve, µ slide at 90% slip, µ lock as the mean µ above 95% slip, initial slope below 3% slip. Master "
        "values are used where no usable curve exists.</li>"
        "<li>Magic Formula fits use slip 0-0.90 (the lock-up drop beyond is not Magic-Formula shaped). Predicted curves "
        "are Magic Formula up to 0.90, then a straight blend to the predicted µ lock.</li>"
        "<li>All scores are on tyres hidden from training. 90% ranges are cross-conformal: the range for each fold is "
        "set from the errors on the other folds, and its coverage is checked.</li>"
        "<li>Every model was run with three imbalance options (as is, sizes re-weighted 1/√n, repeats averaged per "
        "configuration); the best per case and landmark is kept. Picking the best of many set-ups flatters it "
        "slightly: treat differences below ~0.02 in R² as noise.</li></ul>")
    doc.p(f"<span class='note'>Run time {R.get('elapsed_s', 0) / 60:.1f} min. Generated {date.today().isoformat()} "
          f"by tyretraction from {E(R['inputs']['master'])}.</span>")

    nav = "<nav><ol>" + "".join(f'<li><a href="#{a}">{E(t)}</a></li>' for a, t in doc.toc) + "</ol></nav>"
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Traction analysis report</title>'
            f"<style>{CSS}</style></head><body><main><h1>{E(title)}</h1>"
            f'<p class="sub">{len(df)} tests · {df["size_id"].nunique()} sizes · {df["compound_id"].nunique()} compounds · '
            f'{R.get("n_curves_used", 0)} curves</p>{nav}{"".join(doc.parts)}</main></body></html>')
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
