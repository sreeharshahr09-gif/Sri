"""Figures for the sidewall deflection analysis (matplotlib, static PNG)."""
from __future__ import annotations

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .analysis import Result  # noqa: E402

# Reference palette: entity colors are fixed across every panel.
INF, LOAD, CHANGE, ENERGY = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
NAIVE, INK, INK2, MUTED, GRID, SURF = "#898781", "#0b0b0b", "#52514e", "#a3a29c", "#e8e7e3", "#fcfcfb"
BAND = "#f0efec"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 11.5, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "axes.titlelocation": "left", "legend.frameon": False,
    "lines.linewidth": 2,
})


def _zones(ax, r: Result, label=True):
    for k, zr in enumerate(r.zones):
        lo, hi = sorted((zr["z_inflated_from"], zr["z_inflated_to"]))
        if k % 2 == 0:
            ax.axhspan(lo, hi, color=BAND, zorder=0, lw=0)
        if label:
            ax.text(1.0, (lo + hi) / 2, zr["zone"], transform=ax.get_yaxis_transform(),
                    ha="right", va="center", fontsize=8.5, color=INK2, rotation=90)


def cross_section(ax, r: Result):
    b, pi, pl, mt = r.band, r.inf, r.load, r.matched
    for p, col, lab in [(r.passes["inflated"], INF, "inflated"), (r.passes["loaded"], LOAD, "loaded")]:
        ax.plot(b.x_band - p.x, p.z, ".", ms=2.5, color=col, alpha=0.35, zorder=2)
    ax.plot(pi.u, pi.z, color=INF, label="inflated", zorder=3)
    ax.plot(pl.u, pl.z, color=LOAD, label="loaded", zorder=3)

    # rigid band (above the sidewall edge) in each scan
    ax.axhspan(b.edge_inflated, r.passes["inflated"].z.max(), color=INF, alpha=0.06, lw=0)
    ax.axhspan(b.edge_loaded, b.edge_inflated, color=LOAD, alpha=0.06, lw=0)
    ax.annotate("", xy=(-3, b.edge_loaded), xytext=(-3, b.edge_inflated),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2))
    ax.text(-4, (b.edge_loaded + b.edge_inflated) / 2, f"band moves\n{b.delta:.0f} mm down",
            ha="right", va="center", fontsize=8.5, color=INK)

    # material-point displacement arrows
    for s in np.arange(10, mt.s[-1], 18):
        i = int(np.searchsorted(mt.s, s))
        ax.annotate("", xy=(mt.u_load[i], mt.z_load[i]), xytext=(mt.u_inf[i], mt.z_inf[i]),
                    arrowprops=dict(arrowstyle="-|>", color=CHANGE, lw=1.1, shrinkA=0, shrinkB=0),
                    zorder=4)
    mw = r.metrics["max_width"]
    ax.plot([mw["inflated_u"]], [mw["inflated_z"]], "o", ms=8, mfc=INF, mec=SURF, mew=2, zorder=5)
    ax.plot([mw["loaded_u"]], [mw["loaded_z"]], "o", ms=8, mfc=LOAD, mec=SURF, mew=2, zorder=5)
    ax.annotate(f"max width\n+{mw['growth_mm']:.1f} mm", (mw["loaded_u"], mw["loaded_z"]),
                xytext=(8, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK)

    al = r.metrics["arc_length"]
    ax.annotate(f"{al['deficit_mm']:.0f} mm of arc\nout of view\nwhen loaded", (pi.u[0], pi.z[0]),
                xytext=(-10, 0), textcoords="offset points", ha="right", va="center",
                fontsize=8.5, color=INK2)
    ax.set_xlim(min(pi.u.min(), pl.u.min()) - 22, max(pi.u.max(), pl.u.max()) + 14)
    ax.set_xlabel("outward offset u (mm), 0 = rim band")
    ax.set_ylabel("stage height z (mm)")
    ax.set_title("Cross-section: each arrow follows one material point")
    ax.set_aspect("equal")
    ax.legend(loc="upper left")


def bulge(ax_u, ax_z, r: Result):
    mt = r.matched
    ax_u.plot(r.naive_du, r.naive_z, "--", color=NAIVE, lw=1.6, label="same stage height (naive)")
    ax_u.plot(mt.du, mt.z_inf, color=CHANGE, label="same material point")
    nv, ma = r.metrics["naive_same_z"], r.metrics["matched"]
    ax_u.annotate(f"{nv['peak_mm']:.1f} mm", (nv["peak_mm"], nv["at_z"]), xytext=(4, 0),
                  textcoords="offset points", va="center", fontsize=8.5, color=INK2)
    ax_u.annotate(f"{ma['peak_outward_mm']:.1f} mm", (ma["peak_outward_mm"], ma["inflated_z_of_that_point"]),
                  xytext=(4, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK)
    ax_u.axvline(0, color=MUTED, lw=0.8)
    _zones(ax_u, r, label=False)
    ax_u.set_xlabel("outward displacement (mm)")
    ax_u.set_ylabel("height in the inflated scan (mm)")
    ax_u.set_title("Bulge: naive delta vs material-matched")
    ax_u.legend(loc="upper right", fontsize=8.5)

    ax_z.plot(-mt.dz, mt.z_inf, color=CHANGE)
    ax_z.annotate(f"{-mt.dz[0]:.0f} mm", (-mt.dz[0], mt.z_inf[0]), xytext=(4, -4),
                  textcoords="offset points", fontsize=8.5, color=INK)
    ax_z.annotate(f"{-mt.dz[-1]:.0f} mm", (-mt.dz[-1], mt.z_inf[-1]), xytext=(4, 0),
                  textcoords="offset points", fontsize=8.5, color=INK)
    _zones(ax_z, r)
    ax_z.set_xlim(0, None)
    ax_z.set_xlabel("downward displacement (mm)")
    ax_z.set_title("Vertical drop of each material point")


def curvature(ax_k, ax_dk, ax_th, r: Result):
    mt, h = r.matched, r.metrics["assumptions"]["surface_offset_from_ply_mm"]
    ax_k.plot(1000 * mt.kappa_inf, mt.z_inf, color=INF, label="inflated")
    ax_k.plot(1000 * mt.kappa_load, mt.z_inf, color=LOAD, label="loaded")
    ax_k.axvline(0, color=MUTED, lw=0.8)
    _zones(ax_k, r, label=False)
    ax_k.set_xlabel("curvature κ (1/m),  + = convex")
    ax_k.set_ylabel("height in the inflated scan (mm)")
    ax_k.set_title("Curvature")
    ax_k.legend(loc="upper right", fontsize=8.5)

    ax_dk.plot(1000 * mt.dkappa, mt.z_inf, color=CHANGE)
    ax_dk.axvline(0, color=MUTED, lw=0.8)
    for hg in r.metrics["hinges"]:
        ax_dk.plot([hg["dkappa_per_m"]], [hg["z_inflated"]], "o", ms=8, mfc=CHANGE, mec=SURF, mew=2)
        ax_dk.annotate(f"≈{hg['surface_strain_pct']:.0f}% strain", (hg["dkappa_per_m"], hg["z_inflated"]),
                       xytext=(6 if hg["dkappa_per_m"] > 0 else -6, 0), textcoords="offset points",
                       ha="left" if hg["dkappa_per_m"] > 0 else "right", va="center",
                       fontsize=8.5, color=INK)
    _zones(ax_dk, r, label=False)
    w = r.metrics["assumptions"]["derivative_window_mm"]
    ax_dk.set_xlabel(f"Δκ (1/m), averaged over {w:g} mm")
    ax_dk.set_title(f"Curvature change → bending strain (h = {h:g} mm)")

    ax_th.plot(mt.dtheta, mt.z_inf, color=CHANGE)
    ax_th.axvline(0, color=MUTED, lw=0.8)
    _zones(ax_th, r)
    ax_th.set_xlabel("Δθ (deg),  + = tilts outward")
    ax_th.set_title("Rotation of the surface")


def zone_bars(ax, r: Result):
    names = [z["zone"] for z in r.zones]
    y = np.arange(len(names))
    a = [z["share_of_bulge_pct"] for z in r.zones]
    e = [z["share_of_bending_energy_pct"] for z in r.zones]
    hgt = 0.36
    ax.barh(y - hgt / 2 - 0.01, a, height=hgt, color=CHANGE, label="share of outward bulge")
    ax.barh(y + hgt / 2 + 0.01, e, height=hgt, color=ENERGY, label="share of bending energy ∫Δκ²")
    for yy, v in zip(y - hgt / 2, a):
        ax.text(v + 1, yy, f"{v:.0f}%", va="center", fontsize=8.5, color=INK)
    for yy, v in zip(y + hgt / 2, e):
        ax.text(v + 1, yy, f"{v:.0f}%", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlim(0, max(a + e) * 1.55)
    ax.set_xlabel("% of whole sidewall")
    ax.grid(axis="y", visible=False)
    ax.set_title("Where the deformation happens")
    ax.legend(loc="upper right", fontsize=8.5)


def overview(r: Result, path):
    fig = plt.figure(figsize=(17, 11.5), layout="constrained")
    gs = fig.add_gridspec(2, 4, width_ratios=[1.25, 1, 1, 1])
    cross_section(fig.add_subplot(gs[:, 0]), r)
    bulge(fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[0, 2]), r)
    zone_bars(fig.add_subplot(gs[0, 3]), r)
    curvature(fig.add_subplot(gs[1, 1]), fig.add_subplot(gs[1, 2]), fig.add_subplot(gs[1, 3]), r)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def data_quality(r: Result, path, x_all, z_all):
    from .analysis import split_passes
    s1, _ = split_passes(x_all, z_all)
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.8), layout="constrained")

    a = ax[0]
    idx = np.arange(len(x_all))
    bad = x_all >= 250
    xa = np.where(bad, np.nan, x_all)
    a.plot(idx[: s1.stop], xa[: s1.stop], color=INF, lw=1, label="inflated pass")
    a.plot(idx[s1.stop:], xa[s1.stop:], color=LOAD, lw=1, label="loaded pass")
    lo = np.nanmin(xa)
    a.plot(idx[bad], np.full(bad.sum(), lo - 2), "x", color=INK, ms=6, label="dropout (300.15)")
    a.set_xlabel("sample #")
    a.set_ylabel("laser reading x (mm)")
    a.set_title("Raw record: two passes, dropouts at the ends")
    a.legend(loc="upper center", fontsize=8.5)

    a = ax[1]
    for p, prof, col in [(r.passes["inflated"], r.inf, INF), (r.passes["loaded"], r.load, LOAD)]:
        a.plot(prof.resid, prof.resid_z, ".", ms=3, color=col, label=f"{p.name}: σ = {prof.sigma:.2f} mm")
    a.axvline(0, color=MUTED, lw=0.8)
    a.set_xlabel("raw − smoothed (mm)")
    a.set_ylabel("stage height z (mm)")
    a.set_title("Noise after cleaning")
    a.legend(loc="upper right", fontsize=8.5)

    a = ax[2]
    b = r.band
    pi, pl = r.passes["inflated"], r.passes["loaded"]
    a.plot(pi.z, pi.x, color=INF, lw=1.2, label="inflated")
    a.plot(pl.z + b.delta, pl.x, color=LOAD, lw=1.2, label=f"loaded, shifted +{b.delta:.1f} mm")
    a.set_xlim(b.edge_inflated - 30, pi.z.max() + 2)
    a.set_ylim(b.x_band - 15, b.x_band + 10)
    a.invert_yaxis()
    a.set_xlabel("stage height z (mm)")
    a.set_ylabel("laser reading x (mm)")
    a.set_title(f"Rigid band texture lines up (r = {b.texture_corr:.2f}, axial shift {b.x_offset:+.2f} mm)")
    a.legend(loc="lower left", fontsize=8.5)
    fig.savefig(path, dpi=110)
    plt.close(fig)
