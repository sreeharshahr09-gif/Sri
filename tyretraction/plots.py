"""Figures for the report. Each function returns a matplotlib Figure.

Style: one recessive grid, thin marks, a fixed colour order (validated for
colour-vision deficiency), legends whenever there are two or more series,
text in neutral ink - never in the series colour."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from . import schema as S  # noqa: E402
from .curves import GRID  # noqa: E402

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRIDC, AXIS, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"error": "#d03b3b", "warning": "#fab219", "info": MUTED}
SURF_COL = {"Wet": CAT[0], "Dry": CAT[1]}
CASE_COL = {"random": MUTED, "A": CAT[0], "B": CAT[1], "C": CAT[2]}
SEQ = LinearSegmentedColormap.from_list("seq", ["#f4f8fd", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
DIV = LinearSegmentedColormap.from_list("div", ["#1c5cab", "#86b6ef", "#f0efec", "#ef8a8a", "#b52e2e"])

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "font.family": "DejaVu Sans", "font.size": 9.5, "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.labelcolor": INK2, "axes.edgecolor": AXIS,
    "axes.linewidth": 0.8, "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
    "axes.grid": True, "grid.color": GRIDC, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "legend.fontsize": 8.5, "lines.linewidth": 2.0, "lines.solid_capstyle": "round",
    "axes.prop_cycle": matplotlib.cycler(color=CAT),
})


def _surfaces(df):
    return [s for s in ("Wet", "Dry") if s in set(df["SurfaceType"])] + \
           sorted(set(df["SurfaceType"]) - {"Wet", "Dry"})


def _empty(msg, size=(7, 2)):
    fig, ax = plt.subplots(figsize=size)
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", color=INK2, wrap=True)
    return fig


# ----------------------------------------------------------------------------
# Inventory
# ----------------------------------------------------------------------------
def inventory(ov):
    """Tests per size, split by surface; configurations annotated."""
    ov = ov.sort_values("tests")
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(ov) + 1.4))
    left = np.zeros(len(ov))
    for s, col in (("wet", SURF_COL["Wet"]), ("dry", SURF_COL["Dry"])):
        v = ov[s].to_numpy()
        if v.sum() == 0:
            continue
        ax.barh(ov.index, v, left=left, color=col, height=0.6, label=s.capitalize(),
                edgecolor=SURF, linewidth=1.5)
        left += v
    for i, (n, c) in enumerate(zip(ov["tests"], ov["configs"])):
        ax.text(n + ov["tests"].max() * 0.01, i, f"{n} tests · {c} configs", va="center",
                color=INK2, fontsize=8.5)
    ax.set_xlim(0, ov["tests"].max() * 1.32)
    ax.set_xlabel("number of tests")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right")
    ax.set_title("How many tests each tyre size has")
    fig.tight_layout()
    return fig


def confounding_map(tab):
    if tab.shape[0] > 45:
        tab = tab.loc[tab.sum(axis=1).sort_values(ascending=False).index[:45]]
    tab = tab.loc[:, tab.sum().sort_values(ascending=False).index]
    order = tab.apply(lambda r: (-(r > 0).sum(), -r.max()), axis=1).sort_values().index
    tab = tab.loc[order]
    fig, ax = plt.subplots(figsize=(min(1.0 * tab.shape[1] + 3, 12), 0.24 * tab.shape[0] + 1.8))
    im = ax.imshow(np.where(tab.to_numpy() > 0, tab.to_numpy(), np.nan), cmap=SEQ, aspect="auto")
    ax.set_xticks(range(tab.shape[1]), tab.columns, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(tab.shape[0]), tab.index, fontsize=7.5)
    ax.grid(False)
    for (i, j), v in np.ndenumerate(tab.to_numpy()):
        if v > 0:
            ax.text(j, i, int(v), ha="center", va="center", fontsize=6.5,
                    color="white" if v > tab.to_numpy().max() * 0.5 else INK)
    ax.set_xlabel("tyre size")
    ax.set_ylabel("compound group")
    fig.colorbar(im, ax=ax, shrink=0.6, label="tests")
    ax.set_title("Which compound was tested in which size")
    fig.tight_layout()
    return fig


def condition_coverage(df):
    cols = [c for c in ["Load_N", "IP_kPa", "Speed_kph", "Tsurface_C"] if c in df]
    fig, axes = plt.subplots(1, len(cols), figsize=(3.0 * len(cols), 2.6))
    for ax, c in zip(np.atleast_1d(axes), cols):
        for s in _surfaces(df):
            v = df.loc[df.SurfaceType == s, c].dropna()
            ax.hist(v, bins=20, color=SURF_COL.get(s, MUTED), alpha=0.75, label=s)
        ax.set_title(c.replace("_", " "), fontsize=9.5)
        ax.set_yticks([])
    np.atleast_1d(axes)[0].legend()
    fig.suptitle("Range of test conditions covered", x=0.01, ha="left", fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------------
# Data quality
# ----------------------------------------------------------------------------
def qc_summary(flags):
    if not len(flags):
        return _empty("No data-quality issues found.")
    g = flags.groupby(["check", "severity"]).size().reset_index(name="n")
    order = {"error": 0, "warning": 1, "info": 2}
    g = g.sort_values(["severity", "n"], key=lambda s: s.map(order) if s.name == "severity" else s,
                      ascending=[False, True])
    fig, ax = plt.subplots(figsize=(8, 0.34 * len(g) + 1.3))
    ax.barh(g["check"].str.replace("_", " "), g["n"], color=[STATUS[s] for s in g["severity"]], height=0.6)
    for i, (n, sv) in enumerate(zip(g["n"], g["severity"])):
        ax.text(n + g["n"].max() * 0.01, i, f"{n}  ({sv})", va="center", fontsize=8.5, color=INK2)
    ax.set_xlim(0, g["n"].max() * 1.25)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("number of tests / files affected")
    ax.set_title("Data-quality findings by type")
    fig.tight_layout()
    return fig


def qc_examples(curves, flags, desc, max_n=6):
    """Raw curves of flagged tests next to a typical good test."""
    picks = []
    for chk in ["spikes", "high_noise", "no_lock_region", "master_curve_mismatch", "repeat_outlier",
                "poor_pacejka_fit", "slip_gap", "late_start"]:
        used = {p[1] for p in picks}
        ids = [i for i in flags.loc[flags.check == chk, "ID"].dropna().astype(int) if i in curves and i not in used]
        if ids:
            picks.append((chk, ids[0]))
        if len(picks) >= max_n - 1:
            break
    good = [i for i in desc.index if i in curves and i not in set(flags.ID.dropna().astype(int))]
    if good:
        picks.insert(0, ("typical (no flags)", good[len(good) // 2]))
    if not picks:
        return _empty("No curves to show.")
    n = len(picks)
    cols = min(3, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.4 * cols, 2.6 * rows), squeeze=False)
    for ax, (chk, sid) in zip(axes.flat, picks):
        sr, mu = curves[sid]
        ax.plot(sr, mu, "o", ms=2.5, color=CAT[0] if chk.startswith("typical") else STATUS["error"])
        ax.set_title(f"ID {sid}: {chk.replace('_', ' ')}", fontsize=9)
        ax.set_xlim(-0.02, 1.02)
        ax.set_xlabel("slip")
        ax.set_ylabel("µ")
    for ax in axes.flat[n:]:
        ax.axis("off")
    fig.tight_layout()
    return fig


def master_vs_curve(master, desc, tol=0.05):
    m = master.drop_duplicates(S.ID_COL, keep=False).set_index(S.ID_COL)
    ts = [t for t in S.MASTER_TARGETS if t in desc]
    j = m[ts].join(desc[ts], rsuffix="_c", how="inner")
    if not len(j):
        return _empty("No curves linked to master rows.")
    fig, axes = plt.subplots(1, len(ts), figsize=(3.6 * len(ts), 3.4))
    for ax, t in zip(np.atleast_1d(axes), ts):
        a, b = j[t + "_c"], j[t]
        lo = np.nanpercentile(np.r_[a, b], 0.5)
        hi = np.nanpercentile(np.r_[a, b], 99.5)
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        ax.fill_between([lo, hi], [lo - tol, hi - tol], [lo + tol, hi + tol], color=GRIDC, alpha=0.7, lw=0)
        ax.plot([lo, hi], [lo, hi], color=AXIS, lw=1)
        bad = (a - b).abs() > tol
        ax.plot(a[~bad], b[~bad], "o", ms=3, color=CAT[0], alpha=0.6)
        ax.plot(a[bad].clip(lo, hi), b[bad].clip(lo, hi), "o", ms=6, color=STATUS["error"],
                markeredgecolor=SURF, markeredgewidth=1.5)
        for sid in j.index[bad][:6]:
            ax.annotate(str(sid), (np.clip(a[sid], lo, hi), np.clip(b[sid], lo, hi)), fontsize=7.5,
                        xytext=(4, 4), textcoords="offset points", color=INK2)
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
        ax.set_xlabel("read from the curve file")
        ax.set_ylabel("stored in the master")
        ax.set_title(S.TARGET_LABELS[t], fontsize=9.5)
    fig.suptitle("Do the master landmarks agree with the curve files?", x=0.01, ha="left",
                 fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


def repeats_strip(df, target, flags, top=24):
    g = df.groupby("config_id")[target]
    n = g.transform("count")
    sub = df[n >= 3]
    if not len(sub):
        return _empty("No configuration was tested 3 or more times.")
    order = sub.groupby("config_id")[target].count().sort_values(ascending=False).index[:top]
    sub = sub[sub.config_id.isin(order)]
    med = sub.groupby("config_id")[target].median().loc[order].sort_values()
    out_ids = set(flags.loc[(flags.check == "repeat_outlier") & flags.detail.str.startswith(target), "ID"])
    fig, ax = plt.subplots(figsize=(9, 0.3 * len(med) + 1.5))
    rng = np.random.default_rng(0)
    for i, c in enumerate(med.index):
        v = sub[sub.config_id == c]
        jit = rng.uniform(-0.18, 0.18, len(v))
        bad = v[S.ID_COL].isin(out_ids).to_numpy()
        ax.plot(v[target][~bad], i + jit[~bad], "o", ms=3.5, color=CAT[0], alpha=0.7)
        ax.plot(v[target][bad], i + jit[bad], "o", ms=7, color=STATUS["error"], markeredgecolor=SURF,
                markeredgewidth=1.5)
        ax.plot([med[c]] * 2, [i - 0.3, i + 0.3], color=INK, lw=1.5)
    labels = [f"{c.split('|')[0]} · {c.split('|')[2]} · {c.split('|')[3]} (n={int((sub.config_id == c).sum())})"
              for c in med.index]
    ax.set_yticks(range(len(med)), labels, fontsize=7.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(S.TARGET_LABELS[target])
    ax.set_title(f"Repeat tests of the same configuration: {S.TARGET_LABELS[target]}")
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------
def distributions(df, Y, targets):
    fig, axes = plt.subplots(1, len(targets), figsize=(2.9 * len(targets), 2.7))
    for ax, t in zip(np.atleast_1d(axes), targets):
        for s in _surfaces(df):
            v = Y.loc[(df.SurfaceType == s).to_numpy(), t].dropna()
            if len(v):
                ax.hist(v, bins=24, color=SURF_COL.get(s, MUTED), alpha=0.75, label=s)
        ax.set_title(S.TARGET_LABELS.get(t, t), fontsize=9.5)
        ax.set_yticks([])
    np.atleast_1d(axes)[0].legend()
    fig.tight_layout()
    return fig


def ceiling_bars(ceil):
    c = ceil.dropna(subset=["ceiling_r2_test"])
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(c) + 1.4))
    y = np.arange(len(c))
    ax.barh(y + 0.17, c["ceiling_r2_test"], height=0.32, color=CAT[0], label="predicting single tests")
    ax.barh(y - 0.17, c["ceiling_r2_config"], height=0.32, color=CAT[1], label="predicting averages of repeats")
    for i, (a, b) in enumerate(zip(c["ceiling_r2_test"], c["ceiling_r2_config"])):
        ax.text(a + 0.01, i + 0.17, f"{a:.2f}", va="center", fontsize=8, color=INK2)
        ax.text(b + 0.01, i - 0.17, f"{b:.2f}", va="center", fontsize=8, color=INK2)
    ax.axvline(0.9, color=INK2, lw=1, ls=(0, (2, 2)))
    ax.text(0.9, -0.55, " target 0.9", fontsize=8, color=INK2)
    ax.set_yticks(y, [S.TARGET_LABELS.get(t, t) for t in c.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.08)
    ax.set_xlabel("best R² any model could reach (set by repeat scatter)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    ax.set_title("Accuracy ceiling from repeat-test scatter", pad=22)
    fig.tight_layout()
    return fig


LADDER_COLS = [("surface", "Dry / wet"), ("conditions", "Test conditions"), ("test_day", "Test day"),
               ("tyre", "Tyre (size + compound + pattern)"), ("configuration", "Tyre × condition specifics"),
               ("repeat_scatter", "Repeat scatter (unpredictable)")]


def ladder(lad):
    cols = [(c, l) for c, l in LADDER_COLS if c in lad.columns]
    colours = {"surface": CAT[1], "conditions": CAT[3], "test_day": CAT[4], "tyre": CAT[0],
               "configuration": CAT[2], "repeat_scatter": "#c3c2b7"}
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(lad) + 1.9))
    left = np.zeros(len(lad))
    for c, lab in cols:
        v = lad[c].fillna(0).to_numpy(float)
        ax.barh(range(len(lad)), v, left=left, color=colours[c], label=lab, height=0.6,
                edgecolor=SURF, linewidth=2)
        for i, (l0, vv) in enumerate(zip(left, v)):
            if vv >= 0.07:
                ax.text(l0 + vv / 2, i, f"{vv:.0%}", ha="center", va="center", fontsize=8,
                        color="white" if c in ("tyre", "surface") else INK)
        left += v
    ax.set_yticks(range(len(lad)), [S.TARGET_LABELS.get(t, t) for t in lad.index])
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.12), ncol=3)
    ax.set_title("Where the variation comes from")
    fig.tight_layout()
    return fig


def effects_plot(effects, targets):
    targets = [t for t in targets if t in effects and len(effects[t])]
    if not targets:
        return _empty("Not enough within-tyre variation to estimate condition effects.")
    fig, axes = plt.subplots(1, len(targets), figsize=(4.2 * len(targets), 3.0), squeeze=False)
    for ax, t in zip(axes.flat, targets):
        e = effects[t].dropna(subset=["effect"])
        labels = sorted(set(e["condition"] + " (" + e["label"] + ")"))
        ypos = {l: i for i, l in enumerate(labels)}
        for k, s in enumerate(_surfaces(e.rename(columns={"surface": "SurfaceType"}))):
            es = e[e.surface == s]
            y = np.array([ypos[c + " (" + l + ")"] for c, l in zip(es.condition, es.label)]) + (k - 0.5) * 0.25
            ax.errorbar(es.effect, y, xerr=[es.effect - es.lo, es.hi - es.effect], fmt="o", ms=5,
                        color=SURF_COL.get(s, MUTED), label=s, capsize=0, lw=1.5)
        ax.axvline(0, color=AXIS, lw=1)
        ax.set_yticks(range(len(labels)), [l.replace("_", " ") for l in labels], fontsize=8)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel(f"change in {S.TARGET_LABELS.get(t, t)}")
        ax.set_title(S.TARGET_LABELS.get(t, t), fontsize=9.5)
    axes.flat[-1].legend(loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.suptitle("Effect of each test condition within the same tyre (95% range)", x=0.01, ha="left",
                 fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


def correlation_map(R, distinct):
    R = R.dropna(how="all")
    if not len(R):
        return _empty("Not enough data for correlations.")
    R = R.loc[R.abs().max(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(1.3 * R.shape[1] + 3.5, 0.27 * R.shape[0] + 1.6))
    im = ax.imshow(R.to_numpy(float), cmap=DIV, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(R.shape[1]), [S.TARGET_LABELS.get(t, t) for t in R.columns], rotation=25, ha="right")
    ax.set_yticks(range(R.shape[0]), [f"{f}  ({distinct.get(f, 0)} values)" for f in R.index], fontsize=7.5)
    ax.grid(False)
    for (i, j), v in np.ndenumerate(R.to_numpy(float)):
        if np.isfinite(v):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                    color="white" if abs(v) > 0.6 else INK)
    fig.colorbar(im, ax=ax, shrink=0.6, label="Spearman correlation")
    ax.set_title("Which inputs move with which result (one point per configuration)")
    fig.tight_layout()
    return fig


def sessions_plot(df, Y, sess):
    if not sess.get("reference"):
        return _empty("No reference tyre detected (no tyre tested in 3+ separate blocks).")
    rb = sess["ref_blocks"]
    tab = sess["evidence_table"]
    surfs = list(sess["reference"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6), gridspec_kw=dict(width_ratios=[1.6, 1]))
    ax = axes[0]
    for k, s in enumerate(surfs):
        tyre = sess["reference"][s]
        on = (df.SurfaceType == s) & (df.tyre_id == tyre)
        ax.plot(df.loc[on, S.ID_COL], Y.loc[on.to_numpy(), "mu_peak"], "o", ms=3, alpha=0.5,
                color=SURF_COL.get(s, CAT[k]))
        r = rb[rb.surface == s].sort_values("mid")
        ax.plot(r["mid"], r["mu_peak"], "-", color=SURF_COL.get(s, CAT[k]), lw=2, label=f"{s} reference: block average")
    ax.set_xlabel("Sl No (test order)")
    ax.set_ylabel("peak µ of the reference tyre")
    ax.legend()
    ax.set_title("Reference tyre drifts from session to session")
    ax = axes[1]
    for k, s in enumerate(surfs):
        t = tab[tab.surface == s]
        ax.plot(t["ref_mu_peak"], t["mu_peak"], "o", ms=5, color=SURF_COL.get(s, CAT[k]), label=s,
                markeredgecolor=SURF, markeredgewidth=1)
    lo = np.nanmin([tab["ref_mu_peak"].min(), tab["mu_peak"].min()])
    hi = np.nanmax([tab["ref_mu_peak"].max(), tab["mu_peak"].max()])
    ax.set_xlabel("reference tyre level at that time")
    ax.set_ylabel("candidate tyre block average")
    r = sess["evidence"].get("mu_peak", np.nan)
    ax.set_title(f"Candidates move with it (r = {r:.2f})")
    if len(surfs) > 1:
        ax.legend()
    fig.tight_layout()
    return fig


def mean_curves(df, Bm):  # Bm: smoothed curves on the common grid
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    for s in _surfaces(df):
        on = (df.SurfaceType == s).to_numpy()
        M = Bm[on]
        if not np.isfinite(M).any():
            continue
        cnt = np.isfinite(M).sum(axis=0)
        ok = cnt >= max(3, 0.05 * on.sum())
        med = np.nanmedian(M[:, ok], axis=0)
        lo, hi = np.nanpercentile(M[:, ok], 10, axis=0), np.nanpercentile(M[:, ok], 90, axis=0)
        ax.fill_between(GRID[ok], lo, hi, color=SURF_COL.get(s, MUTED), alpha=0.12, lw=0)
        ax.plot(GRID[ok], med, color=SURF_COL.get(s, MUTED), label=f"{s} (median, 10-90% band)")
    ax.axvline(S.SLIDE_SLIP, color=AXIS, lw=1, ls=(0, (2, 2)))
    ax.text(S.SLIDE_SLIP, ax.get_ylim()[1] * 0.98, " lock-up zone", fontsize=8, color=INK2, va="top")
    ax.set_xlabel("slip ratio")
    ax.set_ylabel("µ")
    ax.legend(loc="lower center")
    ax.set_title("Typical measured µ-slip curves")
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------------
# Pacejka
# ----------------------------------------------------------------------------
def fit_quality(free, curves, P_params):
    from .pacejka import mf
    f = free.dropna(subset=["fit_rmse"]).sort_values("fit_rmse")
    picks = [("best", f.index[0]), ("typical", f.index[len(f) // 2]), ("worst", f.index[-1])]
    fig = plt.figure(figsize=(11, 3.2))
    gs = fig.add_gridspec(1, 4)
    ax = fig.add_subplot(gs[0])
    ax.hist(f["fit_rmse"], bins=30, color=CAT[0])
    ax.set_xlabel("fit error (RMSE in µ)")
    ax.set_yticks([])
    ax.set_title("Fit error, all curves", fontsize=9.5)
    s = np.linspace(0, S.SLIDE_SLIP, 200)
    for k, (lab, sid) in enumerate(picks):
        ax = fig.add_subplot(gs[k + 1])
        sr, mu = curves[sid]
        ax.plot(sr, mu, "o", ms=2.5, color=MUTED, label="measured")
        p = free.loc[sid, ["B", "C", "D", "E"]].to_numpy(float)
        ax.plot(s, mf(s, *p), color=CAT[0], label="Magic Formula (0-0.9)")
        ax.set_title(f"{lab}: ID {sid}, RMSE {free.loc[sid, 'fit_rmse']:.3f}", fontsize=9)
        ax.set_xlabel("slip")
        if k == 0:
            ax.legend(fontsize=7.5, loc="lower right")
    fig.tight_layout()
    return fig


def icc_bars(icc):
    order = [("free", "B"), ("free", "C"), ("free", "D"), ("free", "E"),
             ("stabilised", "B"), ("stabilised", "D"), ("stabilised", "E")]
    order += [k for k in icc.index if k[0] == "landmark"]
    order = [k for k in order if k in icc.index and np.isfinite(icc[k])]
    lab = {"free": "B,C,D,E fit", "stabilised": "fit with C fixed", "landmark": "landmark"}
    names = [f"{k[1] if k[0] != 'landmark' else S.TARGET_LABELS.get(k[1], k[1])}  ({lab[k[0]]})" for k in order]
    v = np.array([icc[k] for k in order])
    col = [CAT[0] if k[0] == "free" else CAT[2] if k[0] == "stabilised" else CAT[1] for k in order]
    fig, ax = plt.subplots(figsize=(8, 0.34 * len(order) + 1.3))
    ax.barh(range(len(order)), v, color=col, height=0.6)
    for i, x in enumerate(v):
        ax.text(max(x, 0) + 0.01, i, f"{x:.2f}", va="center", fontsize=8, color=INK2)
    ax.set_yticks(range(len(order)), names, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(min(0, v.min() - 0.05), 1.08)
    ax.axvline(0.75, color=INK2, lw=1, ls=(0, (2, 2)))
    ax.text(0.75, len(order) - 0.5, " good (0.75)", fontsize=8, color=INK2)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("repeatability: share of variation that is real, not repeat scatter")
    ax.set_title("Which numbers come out the same when a test is repeated?")
    fig.tight_layout()
    return fig


def param_tradeoff(free, df):
    f = free.join(df.set_index(S.ID_COL)["SurfaceType"])
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.3))
    for ax, (a, b) in zip(axes, (("C", "E"), ("B", "E"))):
        for s in _surfaces(f):
            g = f[f.SurfaceType == s]
            ax.plot(g[a], g[b], "o", ms=3, alpha=0.6, color=SURF_COL.get(s, MUTED), label=s)
        ax.set_xlabel(a)
        ax.set_ylabel(b)
        r = f[[a, b]].corr().iloc[0, 1]
        ax.set_title(f"{a} vs {b}: correlation {r:.2f}", fontsize=9.5)
    axes[0].legend()
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------------
# Splits, imbalance, models
# ----------------------------------------------------------------------------
def leak_bars(best, ceil_test, target="mu_peak", mode="raw"):
    rows = []
    for case in ("random", "A", "B", "C"):
        if (case, target, mode) in best.index:
            b = best.loc[(case, target, mode)]
            rows.append((case, b.r2, b.r2_config, b.model))
    if not rows:
        return _empty("No experiments.")
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    labels = {"random": "random split\n(leaky)", "A": "A: new\ncondition", "B": "B: new size,\nknown compound",
              "C": "C: new\ncompound"}
    x = np.arange(len(rows))
    ax.bar(x, [r[1] for r in rows], width=0.5, color=[CASE_COL[r[0]] for r in rows])
    for i, r in enumerate(rows):
        if r[1] > 0.15:
            ax.text(i, r[1] - 0.03, f"{r[1]:.2f}\n{r[3]}", ha="center", va="top", fontsize=8.5, color="white",
                    fontweight="bold")
        else:
            ax.text(i, max(r[1], 0) + 0.02, f"{r[1]:.2f}\n{r[3]}", ha="center", fontsize=8, color=INK2)
    if np.isfinite(ceil_test):
        ax.axhline(ceil_test, color=INK2, lw=1, ls=(0, (2, 2)))
        ax.text(len(rows) - 0.5, ceil_test, f" ceiling {ceil_test:.2f}", fontsize=8, color=INK2, va="bottom", ha="right")
    ax.set_xticks(x, [labels[r[0]] for r in rows], fontsize=8.5)
    ax.set_ylim(min(0, min(r[1] for r in rows) - 0.05), 1.12)
    ax.set_ylabel(f"R², {S.TARGET_LABELS[target]}")
    ax.grid(axis="x", visible=False)
    ax.set_title("Same data, different questions: score of the best model per case")
    fig.tight_layout()
    return fig


def imbalance_share(ov):
    ov = ov.sort_values("tests", ascending=False)
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(ov) + 1.5))
    y = np.arange(len(ov))
    ax.barh(y - 0.2, ov["share_tests"], height=0.38, color=CAT[0], label="share of tests")
    ax.barh(y + 0.2, ov["share_configs"], height=0.38, color=CAT[1], label="share of distinct configurations")
    ax.set_yticks(y, ov.index, fontsize=8.5)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right")
    ax.set_title("How much each size weighs in training: tests vs real information")
    fig.tight_layout()
    return fig


def imbalance_error(per_size, title):
    """per_size: {weighting: DataFrame[rmse, n] indexed by size}."""
    fig, ax = plt.subplots(figsize=(8, 3.6))
    names = {"none": "every test counts once", "balanced": "sizes re-weighted",
             "config_mean": "repeats averaged first"}
    for k, (w, t) in enumerate(per_size.items()):
        ax.plot(t["n"], t["rmse"], "o", ms=7, color=CAT[k], label=names.get(w, w),
                markeredgecolor=SURF, markeredgewidth=1.5, alpha=0.9)
    base = list(per_size.values())[0]
    for s, r in base.iterrows():
        ax.annotate(s.split(" ")[0], (r["n"], r["rmse"]), fontsize=7, xytext=(5, 3),
                    textcoords="offset points", color=INK2)
    ax.set_xscale("log")
    ax.set_xlabel("tests of this size in the data (log scale)")
    ax.set_ylabel("prediction error (RMSE, µ)")
    ax.legend()
    ax.set_title(title)
    fig.tight_layout()
    return fig


def leaderboard(ledger, target, mode, ceil=None):
    ok = ledger[(ledger.status == "ok") & (ledger.target == target) & (ledger["mode"] == mode)
                & (ledger.n_scored >= 20)]
    cases = [c for c in ("A", "B", "C") if c in set(ok.case)]
    if not cases:
        return _empty("No assessable case.")
    models = ["surface_mean", "ridge", "knn", "rf", "lgbm", "gp"]
    fig, axes = plt.subplots(1, len(cases), figsize=(3.8 * len(cases), 3.2), sharey=True, squeeze=False)
    for ax, c in zip(axes.flat, cases):
        sub = ok[ok.case == c].groupby("model")["r2"].max().reindex(models)
        col = [MUTED if m == "surface_mean" else CAT[0] for m in models]
        ax.bar(range(len(models)), sub.fillna(0), color=col, width=0.6)
        for i, v in enumerate(sub):
            if np.isfinite(v):
                ax.text(i, max(v, 0) + 0.02, f"{v:.2f}", ha="center", fontsize=7.5, color=INK2)
        if ceil is not None and np.isfinite(ceil):
            ax.axhline(ceil, color=INK2, lw=1, ls=(0, (2, 2)))
        ax.set_xticks(range(len(models)), ["baseline", "ridge", "kNN", "forest", "boosting", "GP"],
                      rotation=30, fontsize=8)
        ax.set_title(f"case {c}", fontsize=9.5)
        ax.grid(axis="x", visible=False)
        ax.set_ylim(min(-0.1, np.nanmin(sub.to_numpy()) - 0.05 if sub.notna().any() else 0), 1.1)
    axes.flat[0].set_ylabel(f"R², {S.TARGET_LABELS.get(target, target)}")
    fig.suptitle(f"{S.TARGET_LABELS.get(target, target)}: best score per model (dashed: ceiling)",
                 x=0.01, ha="left", fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


def parity(y, preds, halfwidths, title):
    """preds: {case: oof array}."""
    cases = [c for c in preds if np.isfinite(preds[c]).sum() > 5]
    fig, axes = plt.subplots(1, len(cases), figsize=(3.4 * len(cases), 3.3), squeeze=False)
    for ax, c in zip(axes.flat, cases):
        p = preds[c]
        ok = np.isfinite(p) & np.isfinite(y)
        lo, hi = np.nanpercentile(np.r_[y[ok], p[ok]], [0.5, 99.5])
        pad = (hi - lo) * 0.06
        lo, hi = lo - pad, hi + pad
        hw = halfwidths.get(c, np.nan)
        if np.isfinite(hw):
            ax.fill_between([lo, hi], [lo - hw, hi - hw], [lo + hw, hi + hw], color=CASE_COL[c], alpha=0.1, lw=0)
        ax.plot([lo, hi], [lo, hi], color=AXIS, lw=1)
        ax.plot(p[ok], y[ok], "o", ms=3, alpha=0.55, color=CASE_COL[c])
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
        ax.set_xlabel("predicted (tyre held out)")
        ax.set_title(f"case {c}", fontsize=9.5)
    axes.flat[0].set_ylabel("measured")
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


def route_bars(cs, mode):
    sub = cs[cs["mode"] == mode]
    cases = [c for c in ("random", "A", "B", "C") if c in set(sub.case)]
    if not cases:
        return _empty("No curve data.")
    routes = [r for r in ("L", "P", "F") if r in set(sub.route)]
    names = {"L": "landmarks → Magic Formula", "P": "B,D,E directly", "F": "curve shapes"}
    fig, ax = plt.subplots(figsize=(8, 3.1))
    w = 0.8 / len(routes)
    for k, r in enumerate(routes):
        v = [sub[(sub.case == c) & (sub.route == r)]["rmse_median"].min() for c in cases]
        ax.bar(np.arange(len(cases)) + (k - (len(routes) - 1) / 2) * w, v, width=w * 0.9, color=CAT[k],
               label=names[r])
    ax.set_xticks(range(len(cases)), [{"random": "random (leaky)"}.get(c, f"case {c}") for c in cases])
    ax.set_ylabel("typical curve error (median RMSE, µ)")
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.set_title("Whole-curve accuracy for held-out tyres, by method", pad=22)
    fig.tight_layout()
    return fig


def curve_examples(measured, pred, band, ids, raw_curves, title, n=6):
    r = pred["rmse"]
    ok = np.where(np.isfinite(r))[0]
    if not len(ok):
        return _empty("No predicted curves.")
    order = ok[np.argsort(r[ok])]
    pick = order[np.linspace(0, len(order) - 1, n).astype(int)]
    cols = 3
    rows = int(np.ceil(len(pick) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.7 * rows), squeeze=False)
    for ax, i in zip(axes.flat, pick):
        sid = ids[i]
        if sid in raw_curves:
            sr, mu = raw_curves[sid]
            ax.plot(sr, mu, "o", ms=2.3, color=MUTED, label="measured")
        c = pred["curves"][i]
        if band is not None:
            ax.fill_between(GRID, np.maximum(c - band, 0), c + band, color=CAT[0], alpha=0.12, lw=0, label="90% band")
        ax.plot(GRID, c, color=CAT[0], label="predicted")
        pct = (np.searchsorted(r[order], r[i]) + 0.5) / len(order)
        ax.set_title(f"ID {sid}: RMSE {r[i]:.3f} ({'best' if pct < 0.1 else 'worst' if pct > 0.9 else 'typical'})",
                     fontsize=9)
        ax.set_xlabel("slip")
    axes.flat[0].legend(fontsize=7.5, loc="lower right")
    for ax in axes.flat[len(pick):]:
        ax.axis("off")
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", fontsize=10.5)
    fig.tight_layout()
    return fig


def importance_bars(imp, title):
    imp = imp.sort_values("mean").tail(15)
    fig, ax = plt.subplots(figsize=(8, 0.3 * len(imp) + 1.3))
    ax.barh(range(len(imp)), imp["mean"], xerr=imp["sd"], color=CAT[0], height=0.6,
            error_kw=dict(ecolor=INK2, lw=1))
    ax.set_yticks(range(len(imp)), imp.index, fontsize=8.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("increase in prediction error when this input is scrambled")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def prediction_card(res):
    c = res["curve"]
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    if c["lo"].notna().any():
        ax.fill_between(c.slip, c.lo, c.hi, color=CAT[0], alpha=0.12, lw=0, label="90% band")
    ax.plot(c.slip, c.mu, color=CAT[0], label="predicted µ-slip curve")
    lm = res["landmarks"]
    if "mu_peak" in lm and "SR_at_mupeak" in lm:
        ax.plot([lm["SR_at_mupeak"]], [lm["mu_peak"]], "o", ms=7, color=CAT[1], markeredgecolor=SURF,
                markeredgewidth=1.5, label="peak")
    ax.set_xlabel("slip ratio")
    ax.set_ylabel("µ")
    ax.legend(loc="lower right")
    ax.set_title(f"{res['name']} (case {res['case']})")
    fig.tight_layout()
    return fig
