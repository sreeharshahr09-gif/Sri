"""Landscape analytics: trends, players, technology mix, gaps. Computed from the data, not written by the model.

Each exhibit holds report blocks and a compact fact sheet (numbers only) that the narrative is
written from, so every number in the text can be traced to a table or chart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from . import report as rb
from .data import normalize_assignee
from .plan import AnalysisPlan

UNCLASSIFIED = "Unclassified"
NOT_SPECIFIED = {"Not specified", "not stated", ""}
_PALETTE = ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3",
            "#1b9e77", "#d95f02"]
TEMPLATE = "plotly_white"


@dataclass
class Exhibit:
    key: str
    section: str  # summary | visuals | clusters | players | process | gaps
    title: str
    blocks: list[rb.Block] = field(default_factory=list)
    facts: str = ""


@dataclass
class Context:
    plan: AnalysisPlan
    year_basis: str
    groups: list[str]  # technology groups in plan order (+ Unclassified when present)
    levels: list[str]  # complexity levels, simple to complex
    problem_label: str
    colors: dict[str, str]
    incomplete_years: list[int]
    years: tuple[int, int] | None
    has_details: bool  # the model's per-patent columns are present
    has_clusters: bool


# --------------------------------------------------------------------------- the analysis set


def build_frame(std: pd.DataFrame, details: pd.DataFrame | None, fields) -> pd.DataFrame:
    """One row per patent with standard column names: corpus columns, model columns (by field key), clusters."""
    frame = pd.DataFrame({
        "id": std["id"], "title": std["title"], "year": std["year"], "assignee": std["assignee"],
        "country": std["country"], "first_claim": std.get("first_claim", ""),
        "cited_by": pd.to_numeric(std.get("cited_by"), errors="coerce") if "cited_by" in std else np.nan,
    }, index=std.index)
    if details is not None:
        for f in fields:
            frame[f.key] = details[f.column]
    if "cluster_label" in std:
        frame["cluster"] = std["cluster_label"]
        frame["cluster_group"] = std["cluster_tech_group"]
    if "tech_group" not in frame or frame["tech_group"].fillna("").eq("").all():
        frame["tech_group"] = frame.get("cluster_group", np.nan)
    frame["tech_group"] = frame["tech_group"].replace("", np.nan).fillna(UNCLASSIFIED)
    return frame


def in_scope(frame: pd.DataFrame) -> pd.Series:
    """Patents the model did not judge off-topic (Relevance: Low)."""
    if "relevance" not in frame:
        return pd.Series(True, index=frame.index)
    return frame["relevance"].fillna("") != "Low"


def make_context(frame: pd.DataFrame, plan: AnalysisPlan, year_basis: str, ref_year: int | None = None) -> Context:
    """`ref_year`: the year the export was made (default: this year), for the publication-lag zone."""
    groups = [b.name for b in plan.tech_buckets]
    groups += [g for g in frame["tech_group"].dropna().unique() if g not in groups and g != UNCLASSIFIED]
    if (frame["tech_group"] == UNCLASSIFIED).any():
        groups.append(UNCLASSIFIED)
    colors = {g: _PALETTE[i % len(_PALETTE)] for i, g in enumerate(groups)}
    colors[UNCLASSIFIED] = "#cccccc"
    years = frame["year"].dropna()
    span = (int(years.min()), int(years.max())) if len(years) else None
    if plan.years and span:
        span = (max(span[0], plan.years[0]), min(span[1], plan.years[1]))
    incomplete: list[int] = []
    if span:
        # Applications publish about 18 months after priority/filing, so the last two years before the export
        # are incomplete; on publication dates only the export's own year is.
        ref = ref_year or date.today().year
        first_incomplete = ref - 1 if re.search(r"priority|filing", year_basis) else ref
        incomplete = [y for y in range(span[0], span[1] + 1) if y >= first_incomplete]
    return Context(
        plan=plan, year_basis=year_basis, groups=groups, levels=[c.name for c in plan.complexity_levels],
        problem_label=plan.problem_label or "Problem", colors=colors, incomplete_years=incomplete, years=span,
        has_details="relevance" in frame, has_clusters="cluster" in frame,
    )


def _explode(frame: pd.DataFrame, col: str) -> pd.DataFrame:
    """One row per value of a "; "-separated column, without unspecified values ("_row": the patent's index)."""
    out = frame.assign(_row=frame.index, **{col: frame[col].fillna("").str.split(r"\s*;\s*")}).explode(col)
    return out[~out[col].isin(NOT_SPECIFIED) & out[col].notna()].reset_index(drop=True)


def _pct(part, whole) -> str:
    return f"{100 * part / whole:.0f} %" if whole else "n/a"


def cagr(counts: pd.Series) -> tuple[float | None, int | None, int | None]:
    """Compound annual growth between the first and last years with patents."""
    counts = counts[counts > 0].sort_index()
    if len(counts) < 2 or counts.index[-1] == counts.index[0]:
        return None, None, None
    y0, y1 = int(counts.index[0]), int(counts.index[-1])
    return (counts.iloc[-1] / counts.iloc[0]) ** (1 / (y1 - y0)) - 1, y0, y1


def _layout(fig, title: str, height: int = 460):
    fig.update_layout(template=TEMPLATE, title=title, height=height, margin={"l": 10, "r": 10, "t": 60, "b": 10},
                      legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "left", "x": 0,
                              "title_text": ""})
    return fig


def _heatmap(grid: pd.DataFrame, x: list[str], y: list[str], scale: str):
    """Count heatmap with numbers in the cells; empty cells stay blank so the populated ones stand out."""
    import plotly.express as px

    fig = px.imshow(grid.values, x=x, y=y, aspect="auto", color_continuous_scale=scale, labels={"color": "Patents"})
    fig.update_traces(text=np.where(grid.values > 0, grid.values.astype(int).astype(str), ""), texttemplate="%{text}")
    fig.update_layout(coloraxis_showscale=False)
    return fig


def _short(text, limit: int = 40) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- exhibits


def trend(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    dated = a.dropna(subset=["year"]).assign(year=lambda d: d["year"].astype(int))
    if ctx.years:
        dated = dated[dated["year"].between(*ctx.years)]
    if dated.empty:
        return None
    lo, hi = ctx.years or (int(dated["year"].min()), int(dated["year"].max()))
    all_years = list(range(lo, hi + 1))
    counts = dated.groupby(["year", "tech_group"]).size().rename("Patents").reset_index()
    fig = px.bar(counts, x="year", y="Patents", color="tech_group", color_discrete_map=ctx.colors,
                 category_orders={"tech_group": ctx.groups}, labels={"year": "Year", "tech_group": "Technology group"})
    total = dated.groupby("year").size().reindex(all_years, fill_value=0)
    fig.add_scatter(x=total.index, y=total.values, mode="lines+markers", name="All patents",
                    line={"color": "#333", "width": 2})
    if ctx.incomplete_years:
        fig.add_vrect(x0=min(ctx.incomplete_years) - 0.5, x1=max(ctx.incomplete_years) + 0.5, fillcolor="#999",
                      opacity=0.12, line_width=0, annotation_text="incomplete (publication lag)",
                      annotation_position="top left", annotation_font_size=11)
    fig.update_xaxes(dtick=1)
    _layout(fig, f"Patents per year by technology group ({ctx.year_basis})")
    complete = total.drop(ctx.incomplete_years, errors="ignore")
    rate, y0, y1 = cagr(complete)
    peak = int(complete.idxmax()) if len(complete) and complete.max() > 0 else None
    caption = (f"Bars: patents per year and technology group; line: all patents. Year basis: {ctx.year_basis}. "
               "Shaded years are incomplete because applications publish about 18 months after filing")
    caption += (f"; growth is measured on complete years only: CAGR {y0}–{y1} {rate * 100:+.1f} % a year."
                if rate is not None else ".")
    half = (lo + hi) // 2
    first, second = dated[dated["year"] <= half], dated[dated["year"] > half]
    shifts = []
    for g in ctx.groups:
        s1 = (first["tech_group"] == g).mean() if len(first) else 0
        s2 = (second["tech_group"] == g).mean() if len(second) else 0
        shifts.append((g, s1, s2))
    facts = [f"Patents per year ({ctx.year_basis}): " + ", ".join(f"{y}: {int(c)}" for y, c in total.items()) + "."]
    if ctx.incomplete_years:
        facts.append(f"Incomplete years (publication lag): {', '.join(map(str, ctx.incomplete_years))}.")
    if rate is not None:
        facts.append(f"CAGR {y0}–{y1} (complete years): {rate * 100:+.1f} % a year. Peak complete year: {peak} "
                     f"({int(complete.max())} patents).")
    facts.append(f"Share of patents by technology group, {lo}–{half} vs {half + 1}–{hi}: " + "; ".join(
        f"{g} {s1 * 100:.0f} % → {s2 * 100:.0f} %" for g, s1, s2 in shifts) + ".")
    return Exhibit("trend", "visuals", "Filing trend", [rb.chart(fig, caption=caption)], " ".join(facts))


def roadmap(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    dated = a.dropna(subset=["year"]).assign(year=lambda d: d["year"].astype(int))
    if ctx.years:
        dated = dated[dated["year"].between(*ctx.years)]
    if dated["year"].nunique() < 3:
        return None
    share = (dated.groupby(["year", "tech_group"]).size() / dated.groupby("year").size()).rename("Share")
    share = share.reset_index()
    share["Share"] = share["Share"] * 100
    fig = px.area(share, x="year", y="Share", color="tech_group", color_discrete_map=ctx.colors,
                  category_orders={"tech_group": ctx.groups},
                  labels={"year": "Year", "Share": "Share of the year's patents (%)", "tech_group": "Technology group"})
    years = sorted(dated["year"].unique())
    fig.update_xaxes(tickvals=years, range=[years[0], years[-1]])
    fig.update_yaxes(range=[0, 100])
    _layout(fig, "Technology roadmap: how the mix of solutions shifts over time")
    fig.update_layout(margin={"r": 30})
    blocks = [rb.chart(fig, caption="Each year's patents split by technology group (100 % stacked). A growing band "
                                    "means that kind of solution takes a larger part of new filings.")]
    facts = []
    lo, hi = int(dated["year"].min()), int(dated["year"].max())
    edges = np.linspace(lo, hi + 1, num=min(4, hi - lo + 1) + 1).round().astype(int)
    periods = [(int(edges[i]), int(edges[i + 1]) - 1) for i in range(len(edges) - 1) if edges[i + 1] - 1 >= edges[i]]
    labels = [f"{p0}–{p1}" if p1 > p0 else str(p0) for p0, p1 in periods]
    period_of = {y: lab for (p0, p1), lab in zip(periods, labels) for y in range(p0, p1 + 1)}
    grid = pd.crosstab(dated["tech_group"], dated["year"].map(period_of)).reindex(columns=labels, fill_value=0)
    grid = grid.reindex([g for g in ctx.groups if g in grid.index])
    grid.index.name = "Technology group"
    grid.columns.name = None
    blocks.append(rb.table(grid.reset_index(), title="Patents per technology group and period"))
    facts.append("Patents per technology group and period: " + "; ".join(
        f"{g}: " + ", ".join(f"{p} {int(v)}" for p, v in row.items()) for g, row in grid.iterrows()) + ".")
    if ctx.has_clusters:
        recent_from = hi - 2  # the last three years
        recent = dated["year"] >= recent_from
        base = recent.mean()
        rows = []
        for label, part in dated.groupby("cluster"):
            if len(part) < 5:
                continue
            share_recent = (part["year"] >= recent_from).mean()
            rows.append({"Topic cluster": label, "Patents": len(part), "First year": int(part["year"].min()),
                         f"Share since {recent_from}": f"{share_recent * 100:.0f} %",
                         "Momentum": round(share_recent / base, 2) if base else np.nan,
                         "Technology group": part["tech_group"].mode().iat[0]})
        if rows:
            mom = pd.DataFrame(rows).sort_values("Momentum", ascending=False)
            rising, falling = mom.head(5), mom.tail(3).iloc[::-1]
            table = pd.concat([rising, falling[~falling["Topic cluster"].isin(rising["Topic cluster"])]])
            blocks.append(rb.table(table, title="Emerging and declining topic clusters", caption=(
                f"Momentum = the cluster's share of patents since {recent_from} divided by the share of all patents "
                f"since then ({base * 100:.0f} %). Above 1: growing faster than the field; below 1: slowing. "
                "Clusters with at least 5 patents.")))
            facts.append("Emerging clusters (momentum): " + "; ".join(
                f"{r['Topic cluster']} {r['Momentum']}" for _, r in rising.iterrows()) + ". Slowing: " + "; ".join(
                f"{r['Topic cluster']} {r['Momentum']}" for _, r in falling.iterrows()) + ".")
    return Exhibit("roadmap", "gaps", "Technology roadmap", blocks, " ".join(facts))


def _competitor_names(a: pd.DataFrame, plan: AnalysisPlan) -> list[tuple[str, list[str]]]:
    """(competitor as named in the plan, matching assignee names)."""
    names = a["assignee"].dropna().unique().tolist()
    out = []
    for comp in plan.competitors:
        target = normalize_assignee(comp).lower()
        hits = [n for n in names if target and (target in n.lower() or n.lower() in target)]
        out.append((comp, hits))
    return out


def players(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    named = a[a["assignee"].fillna("").ne("")]
    if named.empty:
        return None
    top = named["assignee"].value_counts().head(15)
    comp_hits = {h for _, hits in _competitor_names(a, ctx.plan) for h in hits}
    sub = named[named["assignee"].isin(top.index)]
    counts = sub.groupby(["assignee", "tech_group"]).size().rename("Patents").reset_index()
    counts["Assignee"] = counts["assignee"].map(lambda n: ("★ " if n in comp_hits else "") + _short(n, 32))
    order = [("★ " if n in comp_hits else "") + _short(n, 32) for n in top.index]
    fig = px.bar(counts, y="Assignee", x="Patents", color="tech_group", orientation="h",
                 color_discrete_map=ctx.colors,
                 category_orders={"Assignee": order, "tech_group": ctx.groups}, labels={"tech_group": "Technology group"})
    _layout(fig, "Top assignees by technology group", height=max(380, 28 * len(top) + 140))
    total = len(named)
    shares = named["assignee"].value_counts() / total
    hhi = float((shares**2).sum() * 10000)
    top5 = shares.head(5).sum()
    caption = (f"Assignee names are normalised (subsidiaries and spellings merged). ★ = competitor named in your plan. "
               f"Top 5 hold {top5 * 100:.0f} % of patents; HHI {hhi:,.0f} "
               f"({'concentrated' if hhi > 2500 else 'moderately concentrated' if hhi > 1500 else 'fragmented'}).")
    blocks = [rb.chart(fig, caption=caption)]
    facts = ["Top assignees: " + "; ".join(f"{n} {int(c)} ({_pct(c, total)})" for n, c in top.head(10).items()) + ".",
             f"Top-5 share {top5 * 100:.0f} %, HHI {hhi:,.0f}."]
    # player x technology matrix
    top12 = top.head(12).index
    grid = pd.crosstab(named.loc[named["assignee"].isin(top12), "assignee"],
                       named.loc[named["assignee"].isin(top12), "tech_group"])
    grid = grid.reindex(index=top12, columns=[g for g in ctx.groups if g in grid.columns], fill_value=0)
    if grid.shape[1] >= 2:
        fig2 = _heatmap(grid, list(grid.columns), [_short(n, 32) for n in grid.index], "Blues")
        _layout(fig2, "Who works on what: assignee × technology group", height=max(360, 30 * len(grid) + 140))
        blocks.append(rb.chart(fig2, caption="Number of patents per assignee and technology group."))
        focus = []
        for n, row in grid.head(5).iterrows():
            g = row.idxmax()
            focus.append(f"{n}: mostly {g} ({_pct(row.max(), row.sum())})")
        facts.append("Focus of the top assignees: " + "; ".join(focus) + ".")
    return Exhibit("players", "players", "Key players", blocks, " ".join(facts))


def geography(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    offices = a["country"].fillna("").replace("", "n/a").value_counts().head(12)
    if offices.empty or (len(offices) == 1 and offices.index[0] == "n/a"):
        return None
    focus = {r.upper()[:2] for r in ctx.plan.regions if len(r.strip()) >= 2}
    frame = offices.rename_axis("Office").rename("Patents").reset_index()
    frame["Focus"] = frame["Office"].map(lambda o: "Focus region" if o in focus else "Other")
    fig = px.bar(frame, x="Office", y="Patents", color="Focus",
                 color_discrete_map={"Focus region": "#d95f02", "Other": "#8da0cb"})
    _layout(fig, "Patents by patent office", height=380)
    if not focus:
        fig.update_layout(showlegend=False)
    total = int(offices.sum())
    caption = ("Office of the analysed publication (one per family after de-duplication). WO = PCT application, "
               "EP = European Patent Office.")
    facts = "Patents by office: " + ", ".join(f"{o} {int(c)} ({_pct(c, total)})" for o, c in offices.items()) + "."
    return Exhibit("geography", "visuals", "Geography", [rb.chart(fig, caption=caption)], facts)


def segments(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    if "segment" not in a:
        return None
    seg = _explode(a, "segment")
    if seg.empty or seg["segment"].nunique() < 1:
        return None
    counts = seg.groupby(["segment", "tech_group"]).size().rename("Patents").reset_index()
    fig = px.bar(counts, x="segment", y="Patents", color="tech_group", color_discrete_map=ctx.colors,
                 category_orders={"tech_group": ctx.groups, "segment": ctx.plan.segments or None},
                 labels={"segment": "Segment", "tech_group": "Technology group"}, barmode="group")
    _layout(fig, "Segments by technology group", height=400)
    unspecified = len(a) - seg["_row"].nunique()
    caption = f"A patent can cover several segments. {unspecified:,} patents do not name a segment."
    tot = seg["segment"].value_counts()
    facts = ("Patents per segment: " + ", ".join(f"{s} {int(c)}" for s, c in tot.items())
             + f"; {unspecified} patents name no segment.")
    return Exhibit("segments", "visuals", "Segments", [rb.chart(fig, caption=caption)], facts)


def problem_solution(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    if "problem" not in a:
        return None
    prob = _explode(a, "problem")
    if prob.empty:
        return None
    order = [p.name for p in ctx.plan.problems if p.name in set(prob["problem"])]
    order += [p for p in prob["problem"].value_counts().index if p not in order]
    grid = pd.crosstab(prob["problem"], prob["tech_group"]).reindex(
        index=order, columns=[g for g in ctx.groups if g in set(prob["tech_group"])], fill_value=0)
    fig = _heatmap(grid, list(grid.columns), list(grid.index), "YlOrRd")
    _layout(fig, f"{ctx.problem_label} × solution: where the patents are", height=max(360, 34 * len(grid) + 150))
    blocks = [rb.chart(fig, caption=f"Patents per {ctx.problem_label.lower()} (rows) and technology group "
                                    "(columns). A patent addressing several counts in each row.")]
    pairs = grid.stack().sort_values(ascending=False)
    facts = [f"{ctx.problem_label} counts: " + ", ".join(f"{p} {int(c)}" for p, c in grid.sum(axis=1).items()) + ".",
             "Densest combinations: " + "; ".join(f"{p} × {g} {int(c)}" for (p, g), c in pairs.head(6).items()) + "."]
    if ctx.has_clusters:
        top = prob["cluster"].value_counts().head(12).index
        grid2 = pd.crosstab(prob["problem"], prob["cluster"]).reindex(index=order, columns=top, fill_value=0)
        if grid2.shape[1] >= 2:
            fig2 = _heatmap(grid2, [_short(c, 28) for c in grid2.columns], list(grid2.index), "YlGnBu")
            _layout(fig2, f"{ctx.problem_label} × topic cluster", height=max(380, 34 * len(grid2) + 200))
            fig2.update_xaxes(tickangle=-35)
            blocks.append(rb.chart(fig2, caption="The 12 largest topic clusters from the patent map."))
    return Exhibit("problem_solution", "visuals", f"{ctx.problem_label} vs. solutions", blocks, " ".join(facts))


def _top_phrases(series: pd.Series, n: int = 3) -> list[str]:
    s = series.dropna().astype(str).str.strip().str.rstrip(".").str.lower()
    s = s[~s.isin({x.lower() for x in NOT_SPECIFIED} | {"none", "n/a"})]
    return [f"{p} ({c})" for p, c in s.value_counts().head(n).items()]


def complexity(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    import plotly.express as px

    if "complexity" not in a or a["complexity"].fillna("").eq("").all():
        return None
    levels = [lv for lv in ctx.levels if lv in set(a["complexity"])]
    sub = a[a["complexity"].isin(levels)]
    counts = sub.groupby(["complexity", "tech_group"]).size().rename("Patents").reset_index()
    fig = px.bar(counts, x="complexity", y="Patents", color="tech_group", color_discrete_map=ctx.colors,
                 category_orders={"complexity": levels, "tech_group": ctx.groups},
                 labels={"complexity": "Complexity (simple → complex)", "tech_group": "Technology group"})
    _layout(fig, "Solutions ranked by complexity", height=400)
    blocks = [rb.chart(fig, caption="; ".join(f"{c.name}: {c.description}" for c in ctx.plan.complexity_levels
                                              if c.description))]
    rows, facts = [], []
    total = len(sub)
    for lv in levels:
        part = sub[sub["complexity"] == lv]
        rank = part["relevance"].map({"High": 0, "Partial": 1}).fillna(2) if "relevance" in part else 0
        best = part.assign(_rank=rank).sort_values(["_rank", "year"], ascending=[True, False])
        examples = "; ".join(f"{r.id} {_short(r.title, 60)}" for r in best.head(2).itertuples())
        levers = _top_phrases(part["design_lever"]) if "design_lever" in part else []
        rows.append({"Complexity": lv, "Patents": len(part), "Share": _pct(len(part), total),
                     "Main technology group": part["tech_group"].mode().iat[0] if len(part) else "",
                     "Most frequent design levers": "; ".join(levers), "Examples": examples})
        facts.append(f"{lv}: {len(part)} patents ({_pct(len(part), total)}), mostly "
                     f"{rows[-1]['Main technology group']}; levers: {', '.join(levers) or 'n/a'}")
    blocks.append(rb.table(pd.DataFrame(rows), title="Complexity ranking"))
    dated = sub.dropna(subset=["year"])
    if dated["year"].nunique() >= 4 and len(levels) >= 2:
        mid = int(dated["year"].median())
        early = dated[dated["year"] <= mid]["complexity"].value_counts(normalize=True)
        late = dated[dated["year"] > mid]["complexity"].value_counts(normalize=True)
        facts.append(f"Share of {levels[-1]} solutions: {early.get(levels[-1], 0) * 100:.0f} % up to {mid}, "
                     f"{late.get(levels[-1], 0) * 100:.0f} % after")
    return Exhibit("complexity", "clusters", "Complexity ranking", blocks, ". ".join(facts) + ".")


def white_space(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    if "problem" not in a:
        return None
    prob = _explode(a, "problem")
    prob = prob[prob["problem"] != "Other"]
    groups = [g for g in ctx.groups if g != UNCLASSIFIED]
    prob = prob[prob["tech_group"].isin(groups)]
    if prob.empty or prob["problem"].nunique() < 2:
        return None
    problems = [p.name for p in ctx.plan.problems] or list(prob["problem"].unique())
    grid = pd.crosstab(prob["problem"], prob["tech_group"]).reindex(index=problems, columns=groups, fill_value=0)
    n = grid.values.sum()
    rows = []
    row_tot, col_tot = grid.sum(axis=1), grid.sum(axis=0)
    for p in grid.index:
        for g in grid.columns:
            expected = row_tot[p] * col_tot[g] / n if n else 0
            observed = int(grid.loc[p, g])
            if expected >= 1.5 and observed <= 0.5 * expected:
                rows.append({ctx.problem_label: p, "Technology group": g, "Patents": observed,
                             "Expected": round(expected, 1), "Gap": "few patents for this combination"})
    for p in grid.index:
        if row_tot[p] == 0:
            rows.append({ctx.problem_label: p, "Technology group": "any", "Patents": 0, "Expected": np.nan,
                         "Gap": "no patents at all in this set"})
    if "complexity" in a and ctx.levels:
        top_level = ctx.levels[-1]
        cplx = prob[prob["complexity"] == top_level]["problem"].value_counts()
        for p in grid.index:
            if row_tot[p] >= 3 and cplx.get(p, 0) == 0:
                rows.append({ctx.problem_label: p, "Technology group": f"{top_level} solutions", "Patents": 0,
                             "Expected": np.nan, "Gap": f"no {top_level.lower()} solution patented yet"})
    if not rows:
        return Exhibit("white_space", "gaps", "White space", [rb.note(
            f"No clear white space: every {ctx.problem_label.lower()} × technology group combination has about "
            "as many patents as its row and column totals predict.")], "No clear white-space combinations.")
    table = pd.DataFrame(rows).sort_values(["Patents", "Expected"], ascending=[True, False], na_position="last")
    caption = ("Expected = (row total × column total) / all, i.e. the count if solutions were spread evenly across "
               f"{ctx.problem_label.lower()}s. Cells with at most half the expected count are flagged. "
               "Few patents can mean an opening, or a combination that does not make technical sense: check before "
               "acting.")
    facts = "White space: " + "; ".join(
        f"{r[ctx.problem_label]} × {r['Technology group']}: {r['Patents']} patents"
        + (f" (expected {r['Expected']})" if not pd.isna(r["Expected"]) else "") + f", {r['Gap']}"
        for _, r in table.head(8).iterrows()) + "."
    return Exhibit("white_space", "gaps", "White space", [rb.table(table, title="Under-explored combinations",
                                                                     caption=caption)], facts)


def crown_jewel_weights(a: pd.DataFrame) -> dict[str, float]:
    """What the crown-jewel score is made of, normalised to 1."""
    cites = bool(a["cited_by"].notna().any())
    weights = {"forward citations": 0.5 if cites else 0.0,
               "relevance": (0.2 if cites else 0.35) if "relevance" in a else 0.0,
               "claim breadth": 0.2 if cites else 0.35, "recency": 0.1 if cites else 0.3}
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items() if v}


def crown_jewel_scores(a: pd.DataFrame) -> pd.Series:
    """Transparent 0–100 score: weighted percentiles of the parts in `crown_jewel_weights`."""
    parts = {
        "forward citations": lambda: a["cited_by"].fillna(0).rank(pct=True),
        "relevance": lambda: a["relevance"].map({"High": 1.0, "Partial": 0.5}).fillna(0),
        # a shorter independent claim has fewer limitations, so it is broader
        "claim breadth": lambda: (1 - a["first_claim"].fillna("").str.len().replace(0, np.nan).rank(pct=True)).fillna(0),
        "recency": lambda: a["year"].rank(pct=True).fillna(0),
    }
    score = sum(w * parts[k]() for k, w in crown_jewel_weights(a).items())
    return (score * 100).round(0)


def competitors(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    named = _competitor_names(a, ctx.plan)
    chosen = [(c, hits) for c, hits in named if hits]
    source = "named in your plan"
    if not chosen:
        top = a["assignee"].dropna().value_counts().head(5).index
        chosen = [(n, [n]) for n in top]
        source = "the five largest assignees (no competitor from your plan was found in the export)"
    if not chosen:
        return None
    a = a.assign(score=crown_jewel_scores(a))
    total = len(a)
    hi = int(a["year"].max()) if a["year"].notna().any() else None
    rows, jewels, facts = [], [], []
    for comp, hits in chosen:
        part = a[a["assignee"].isin(hits)]
        recent = int((part["year"] >= hi - 2).sum()) if hi else 0
        row = {"Competitor": comp, "Patents": len(part), "Share": _pct(len(part), total),
               "First–last year": (f"{int(part['year'].min())}–{int(part['year'].max())}"
                                   if part["year"].notna().any() else "n/a"),
               f"Since {hi - 2}" if hi else "Recent": recent,
               "Main technology group": part["tech_group"].mode().iat[0] if len(part) else ""}
        if "problem" in part:
            probs = _explode(part, "problem")["problem"].value_counts()
            row[f"Main {ctx.problem_label.lower()}"] = probs.index[0] if len(probs) else ""
        if ctx.has_clusters and len(part):
            row["Largest topic cluster"] = part["cluster"].mode().iat[0]
        rows.append(row)
        facts.append(f"{comp}: {len(part)} patents ({row['Share']}), mainly {row['Main technology group']}, "
                     f"{recent} in the last three years")
        for r in part.sort_values("score", ascending=False).head(3).itertuples():
            jewels.append({"Competitor": comp, "Patent ID": r.id, "Year": "" if pd.isna(r.year) else int(r.year),
                           "Title": _short(r.title, 90), "Topic": getattr(r, "topic", ""),
                           "Design lever": getattr(r, "design_lever", ""),
                           "Core claim": _short(getattr(r, "core_claim", "") or r.first_claim, 220),
                           "Score": int(r.score)})
    weights = crown_jewel_weights(a)
    caption = ("Score (0–100) = weighted percentiles of " + ", ".join(f"{k} {v * 100:.0f} %" for k, v in weights.items())
               + ". Claim breadth: a shorter first claim has fewer limitations.")
    if "forward citations" not in weights:
        caption += " Include forward citations in the export for a stronger measure."
    jewel_df = pd.DataFrame(jewels).dropna(axis=1, how="all")
    jewel_df = jewel_df.loc[:, [c for c in jewel_df.columns if jewel_df[c].astype(str).str.strip().ne("").any()]]
    blocks = [rb.table(pd.DataFrame(rows), title="Competitor watch", caption=f"Competitors: {source}."),
              rb.table(jewel_df, title="Crown jewels: each competitor's strongest patents", caption=caption)]
    best = pd.DataFrame(jewels).groupby("Competitor", sort=False).head(1) if jewels else pd.DataFrame()
    facts.append("Highest-scoring patent per competitor: " + "; ".join(
        f"{r['Competitor']} {r['Patent ID']} ({r['Title']}, score {r['Score']})" for _, r in best.iterrows()))
    return Exhibit("competitors", "players", "Competitors and crown jewels", blocks, ". ".join(facts) + ".")


def process_insights(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    group = next((g for g in ctx.groups if re.search(r"process|manufactur|production", g, re.IGNORECASE)), None)
    if group is None or "design_lever" not in a:
        return None
    part = a[a["tech_group"] == group]
    if part.empty:
        return Exhibit("process", "process", "Process and manufacturing", [rb.note(
            f"No patents in the {group} group in this set.")], f"No {group} patents.")
    levers = _top_phrases(part["design_lever"], 8)
    params = part["key_parameters"].dropna().astype(str) if "key_parameters" in part else pd.Series(dtype=str)
    params = params[~params.str.lower().isin({"none", "not stated", ""})]
    top = part["assignee"].value_counts().head(5)
    rows = [{"Patent ID": r.id, "Year": "" if pd.isna(r.year) else int(r.year), "Assignee": r.assignee,
             "Design lever": getattr(r, "design_lever", ""), "Key parameters": getattr(r, "key_parameters", ""),
             "Performance target": getattr(r, "performance_target", "")}
            for r in part.sort_values("year", ascending=False).head(12).itertuples()]
    share = _pct(len(part), len(a))
    blocks = [
        rb.metrics([("Patents", f"{len(part):,}"), ("Share of the set", share),
                    ("With claimed parameters", f"{len(params):,}"), ("Assignees", f"{part['assignee'].nunique():,}")]),
        rb.markdown("**Most frequent process levers:** " + (", ".join(levers) or "n/a") + "\n\n"
                    "**Most active:** " + ", ".join(f"{n} ({c})" for n, c in top.items())),
        rb.table(pd.DataFrame(rows), title=f"Latest {group} patents"),
    ]
    facts = (f"{group}: {len(part)} patents ({share}); levers: {', '.join(levers[:5]) or 'n/a'}; "
             f"{len(params)} state numeric parameters; most active: "
             + ", ".join(f"{n} {c}" for n, c in top.items()) + ".")
    return Exhibit("process", "process", "Process and manufacturing", blocks, facts)


EXHIBITS = [trend, geography, segments, problem_solution, players, competitors, complexity, process_insights,
            roadmap, white_space]


def run_analytics(frame: pd.DataFrame, plan: AnalysisPlan, year_basis: str,
                  ref_year: int | None = None) -> tuple[list[Exhibit], list[str]]:
    """All exhibits that the data supports; returns (exhibits, notes on skipped ones)."""
    ctx = make_context(frame, plan, year_basis, ref_year)
    exhibits, notes = [], []
    for fn in EXHIBITS:
        try:
            ex = fn(frame, ctx)
        except (KeyError, ValueError, TypeError, ZeroDivisionError, IndexError) as exc:  # one bad exhibit must not
            notes.append(f"{fn.__name__.replace('_', ' ').capitalize()} could not be computed ({exc}).")  # sink all
            continue
        if ex is not None:
            exhibits.append(ex)
    return exhibits, notes


def summary_facts(frame: pd.DataFrame, exhibits: list[Exhibit]) -> str:
    years = frame["year"].dropna()
    head = (f"{len(frame):,} relevant patents, {years.min():.0f}–{years.max():.0f}, {frame['assignee'].nunique():,} "
            f"assignees." if len(years) else f"{len(frame):,} relevant patents.")
    return head + " " + " ".join(f"[{ex.title}] {ex.facts}" for ex in exhibits if ex.facts)


