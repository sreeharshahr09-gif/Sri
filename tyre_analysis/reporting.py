"""Comparison tables and Plotly figures built from the long results dataframe.

Pure functions (no Streamlit) so they can be unit-tested and reused. The UI in
``app.py`` only arranges these outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from shapely.geometry import Polygon, MultiPolygon

# Palette by component (stable colours across every chart).
_PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#B279A2", "#EECA3B", "#9D755D", "#BAB0AC", "#FF9DA6",
]


def component_colors(components) -> dict[str, str]:
    comps = list(dict.fromkeys(components))
    return {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(comps)}


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def tire_summary(df: pd.DataFrame, axis_x_by_file: dict | float) -> pd.DataFrame:
    """One row per file: total weight, total polar MOI, CG offset from axis."""
    rows = []
    for file, g in df.groupby("file", sort=False):
        total_w = g["weight_g"].sum()
        # Weight-weighted centroid distance = overall CG offset from axis.
        cg_offset = (
            (g["centroid_distance_mm"] * g["weight_g"]).sum() / total_w
            if total_w
            else 0.0
        )
        rows.append(
            {
                "file": file,
                "total_weight_g": total_w,
                "total_polar_MOI": g["polar_MOI"].sum(),
                "cg_offset_mm": cg_offset,
                "n_components": len(g),
            }
        )
    return pd.DataFrame(rows)


def comparison_table(df: pd.DataFrame, baseline: str, metric: str = "weight_g") -> pd.DataFrame:
    """Components x files matrix for one metric, plus delta-vs-baseline columns."""
    wide = df.pivot_table(index="component", columns="file", values=metric, sort=False)
    # Keep baseline first, then other files in their original order.
    files = [baseline] + [f for f in df["file"].unique() if f != baseline]
    files = [f for f in files if f in wide.columns]
    wide = wide[files]
    for f in files:
        if f == baseline:
            continue
        wide[f"Δ {f}"] = wide[f] - wide[baseline]
    return wide


def build_delta_frame(
    df: pd.DataFrame, baseline: str, centroid_tol_mm: float = 1.0
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Assemble the component-level comparison frame the UI styles.

    Returns ``(frame, weight_delta_cols, centroid_shift_cols)`` so the styler
    knows which columns get the magnitude heatmap and which get amber flags.
    """
    weight = comparison_table(df, baseline, "weight_g")
    dist = comparison_table(df, baseline, "centroid_distance_mm")

    files = [baseline] + [f for f in df["file"].unique() if f != baseline]
    frame = pd.DataFrame(index=weight.index)
    weight_delta_cols: list[str] = []
    centroid_shift_cols: list[str] = []
    for f in files:
        frame[(f, "weight_g")] = weight.get(f)
        frame[(f, "cdist_mm")] = dist.get(f)
        if f != baseline:
            wd = f"Δw {f}"
            cd = f"Δcg {f}"
            frame[wd] = weight[f] - weight[baseline]
            frame[cd] = dist[f] - dist[baseline]
            weight_delta_cols.append(wd)
            centroid_shift_cols.append(cd)
    frame.columns = [c if isinstance(c, str) else f"{c[0]} · {c[1]}" for c in frame.columns]
    return frame, weight_delta_cols, centroid_shift_cols


def style_delta_frame(
    frame: pd.DataFrame,
    weight_delta_cols: list[str],
    centroid_shift_cols: list[str],
    good_direction: dict[str, int] | None = None,
    centroid_tol_mm: float = 1.0,
):
    """Return a pandas Styler.

    * Weight-delta columns get a magnitude-scaled heatmap. "Good" (green) vs
      "bad" (red) is configurable per component via *good_direction*: -1 means
      weight-down is good (default), +1 means weight-up is good (e.g. a bead
      reinforcement that was intentionally increased).
    * Centroid-shift columns are flagged amber when |shift| exceeds the
      tolerance, regardless of any weight change -- a balance/uniformity flag.
    """
    good_direction = good_direction or {}
    styler = frame.style.format("{:.1f}", na_rep="–")

    def weight_delta_style(col: pd.Series):
        out = []
        max_abs = col.abs().max() or 1.0
        for comp, val in col.items():
            if pd.isna(val):
                out.append("")
                continue
            direction = good_direction.get(comp, -1)  # -1: down is good
            good = (val * direction) < 0  # e.g. dir -1 & val<0 -> good
            if val == 0:
                out.append("")
                continue
            intensity = min(abs(val) / max_abs, 1.0)
            alpha = 0.15 + 0.55 * intensity
            color = f"rgba(46,160,67,{alpha:.2f})" if good else f"rgba(228,87,86,{alpha:.2f})"
            out.append(f"background-color: {color}")
        return out

    for col in weight_delta_cols:
        styler = styler.apply(weight_delta_style, subset=[col])

    def centroid_style(col: pd.Series):
        return [
            "background-color: rgba(240,180,41,0.55)" if (not pd.isna(v) and abs(v) > centroid_tol_mm) else ""
            for v in col
        ]

    for col in centroid_shift_cols:
        styler = styler.apply(centroid_style, subset=[col])

    return styler


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def _ring_xy(geom):
    polys = [geom.polygon] if isinstance(geom.polygon, Polygon) else list(geom.polygon.geoms)
    for p in polys:
        x, y = p.exterior.xy
        yield list(x), list(y)


def overlay_figure(geoms_by_file: dict, baseline: str, axis_x: float | dict) -> go.Figure:
    """All profiles on one axes: baseline as solid outline, iterations filled."""
    fig = go.Figure()
    all_components = [g.layer for gs in geoms_by_file.values() for g in gs]
    colors = component_colors(all_components)

    for file, geoms in geoms_by_file.items():
        is_base = file == baseline
        for g in geoms:
            for xs, ys in _ring_xy(g):
                fig.add_trace(
                    go.Scatter(
                        x=xs, y=ys, mode="lines",
                        name=f"{g.layer} ({file})",
                        legendgroup=file,
                        line=dict(color=colors[g.layer], width=2.5 if is_base else 1.2,
                                  dash="solid" if is_base else "dot"),
                        fill=None if is_base else "toself",
                        fillcolor=None if is_base else _rgba(colors[g.layer], 0.18),
                        hovertemplate=f"{g.layer} · {file}<extra></extra>",
                    )
                )
    ax = axis_x if isinstance(axis_x, (int, float)) else list(axis_x.values())[0]
    fig.add_vline(x=ax, line=dict(color="#888", dash="dash"), annotation_text="rotation axis")
    fig.update_layout(
        title="Cross-section overlay (baseline solid, iterations dotted+filled)",
        xaxis_title="x (mm)", yaxis_title="y (mm)",
        yaxis_scaleanchor="x", yaxis_scaleratio=1, height=650, legend=dict(font=dict(size=9)),
    )
    return fig


def centroid_map_figure(df: pd.DataFrame, geoms_by_file: dict, baseline: str) -> go.Figure:
    """Per-component centroid markers (size=weight) with shift arrows vs baseline."""
    fig = go.Figure()
    colors = component_colors(df["component"].unique())

    # baseline outlines for context
    for g in geoms_by_file.get(baseline, []):
        for xs, ys in _ring_xy(g):
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", showlegend=False,
                                     line=dict(color="#ccc", width=1),
                                     hoverinfo="skip"))

    base = df[df["file"] == baseline].set_index("component")
    wmax = df["weight_g"].max() or 1.0
    for file, g in df.groupby("file", sort=False):
        fig.add_trace(go.Scatter(
            x=g["centroid_x"], y=g["centroid_y"], mode="markers", name=file,
            marker=dict(size=8 + 30 * (g["weight_g"] / wmax),
                        color=[colors[c] for c in g["component"]],
                        line=dict(width=2 if file != baseline else 0, color="#333")),
            text=g["component"],
            hovertemplate="%{text} · " + file + "<br>x=%{x:.1f} y=%{y:.1f}<extra></extra>",
        ))
        if file != baseline:
            for _, row in g.iterrows():
                if row["component"] in base.index:
                    b = base.loc[row["component"]]
                    fig.add_annotation(
                        x=row["centroid_x"], y=row["centroid_y"],
                        ax=b["centroid_x"], ay=b["centroid_y"],
                        xref="x", yref="y", axref="x", ayref="y",
                        showarrow=True, arrowhead=3, arrowwidth=1.5,
                        arrowcolor=colors[row["component"]],
                    )
    fig.update_layout(title="Centroid map (marker size = weight; arrows = shift from baseline)",
                      xaxis_title="x (mm)", yaxis_title="y (mm)",
                      yaxis_scaleanchor="x", yaxis_scaleratio=1, height=650)
    return fig


def polar_moi_bar(df: pd.DataFrame) -> go.Figure:
    """Grouped bar of polar MOI per component per file (kept separate from weight)."""
    fig = go.Figure()
    colors = component_colors(df["component"].unique())
    for file, g in df.groupby("file", sort=False):
        fig.add_trace(go.Bar(x=g["component"], y=g["polar_MOI"], name=file,
                             marker_line_width=0))
    fig.update_layout(title="Polar moment of inertia by component (r³-driven; diverges from weight)",
                      xaxis_title="component", yaxis_title="polar MOI (g·mm²)",
                      barmode="group", height=480)
    return fig


def weight_waterfall(df: pd.DataFrame, baseline: str, final: str) -> go.Figure:
    """Component-by-component weight change from baseline to *final*, summing to Δtotal."""
    b = df[df["file"] == baseline].set_index("component")["weight_g"]
    f = df[df["file"] == final].set_index("component")["weight_g"]
    comps = list(dict.fromkeys(list(b.index) + list(f.index)))
    deltas = [(f.get(c, 0.0) - b.get(c, 0.0)) for c in comps]

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative"] * len(comps) + ["total"],
        x=comps + ["NET Δ"],
        y=deltas + [0],
        connector=dict(line=dict(color="#bbb")),
        decreasing=dict(marker=dict(color="#2EA043")),  # weight down = green
        increasing=dict(marker=dict(color="#E45756")),
        totals=dict(marker=dict(color="#4C78A8")),
        text=[f"{d:+.0f}" for d in deltas] + [f"{sum(deltas):+.0f}"],
        textposition="outside",
    ))
    fig.update_layout(title=f"Weight change waterfall: {baseline} → {final} (g)",
                      yaxis_title="Δ weight (g)", height=500)
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
