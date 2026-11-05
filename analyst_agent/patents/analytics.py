"""Landscape analytics: trends, players, technology mix, gaps. Computed from the data, not written by the model.

Each exhibit holds report blocks and a compact fact sheet (numbers only) that the narrative is
written from, so every number in the text can be traced to a table or chart.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from . import report as rb
from .data import normalize_assignee
from .plan import AnalysisPlan, focus_names

UNCLASSIFIED = "Unclassified"
INSUFFICIENT = "insufficient data"
NOT_STATED_LABEL = "Not stated"
NOT_SPECIFIED = {"Not specified", "not stated", ""}
_PALETTE = ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3",
            "#1b9e77", "#d95f02"]
TEMPLATE = "plotly_white"


@dataclass
class Exhibit:
    key: str
    section: str  # summary | visuals | clusters | players | process | gaps | custom (brief-specific)
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
    incomplete_years: list[int]  # the last years: their patents are not all published yet (publication lag)
    years: tuple[int, int] | None
    has_details: bool  # the model's per-patent columns are present
    has_clusters: bool
    # The first years, when the export only holds publications from `window_start` on (a search limited to
    # recent publications): inventions filed then appear only if published late, e.g. as grants.
    early_years: list[int] = field(default_factory=list)
    window_start: float | None = None

    @property
    def partial_years(self) -> list[int]:
        """Years whose counts are not comparable: cut short at the start or at the end."""
        return sorted(set(self.early_years) | set(self.incomplete_years))


# --------------------------------------------------------------------------- the analysis set


def build_frame(std: pd.DataFrame, details: pd.DataFrame | None, fields) -> pd.DataFrame:
    """One row per patent with standard column names: corpus columns, model columns (by field key), clusters."""
    frame = pd.DataFrame({
        "id": std["id"], "title": std["title"], "year": std["year"], "assignee": std["assignee"],
        "country": std["country"], "first_claim": std.get("first_claim", ""),
        "cited_by": pd.to_numeric(std.get("cited_by"), errors="coerce") if "cited_by" in std else np.nan,
        "published": std.get("published", np.nan),
    }, index=std.index)
    if "alive" in std and std["alive"].astype(str).ne("").any():
        frame["alive"] = std["alive"]  # Yes / No / Unknown, from the export's legal status
    if details is not None:
        for f in fields:
            frame[f.key] = details[f.column]
    if "cluster_label" in std:
        frame["cluster"] = std["cluster_label"]
        frame["cluster_group"] = std["cluster_tech_group"]
    if "tech_group" not in frame or frame["tech_group"].fillna("").eq("").all():
        frame["tech_group"] = frame.get("cluster_group", np.nan)
    # As objects first: without the model's answers the column is all-NaN floats, which cannot hold a label.
    frame["tech_group"] = frame["tech_group"].astype(object).replace("", np.nan).fillna(UNCLASSIFIED)
    return frame


def in_scope(frame: pd.DataFrame) -> pd.Series:
    """Patents the model did not judge off-topic (Relevance: Low)."""
    if "relevance" not in frame:
        return pd.Series(True, index=frame.index)
    return frame["relevance"].fillna("") != "Low"


def screen(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[pd.Series, pd.Series]:
    """(patents analysed, patents set aside for a manual check). Off-topic patents (Relevance: Low) are in
    neither; with the plan's `check_partial`, Partial patents are set aside instead of counted."""
    keep = in_scope(frame)
    if not plan.check_partial or "relevance" not in frame:
        return keep, pd.Series(False, index=frame.index)
    check = frame["relevance"].fillna("") == "Partial"
    return keep & ~check, check


def fractional_year(day: date) -> float:
    return day.year + (day.timetuple().tm_yday - 1) / 365.25


def cut_off_at_start(frame: pd.DataFrame, year_basis: str, span: tuple[int, int] | None) -> tuple[list[int], float | None]:
    """(years cut short, first publication) when the export starts at a publication date well after its first
    priority/filing years, as with a database search limited to recent publications. The patents of those years
    were published years later than usual (grants, late family members) - the sign that the rest of them are
    missing. An export that is not cut shows usual delays for its first years too."""
    if not span or "published" not in frame or not re.search(r"priority|filing", year_basis):
        return [], None
    dated = frame.dropna(subset=["year", "published"])
    if len(dated) < 20:
        return [], None
    start = float(dated["published"].min())
    first_complete = math.ceil(start - 1.5)  # filed then, published from about 18 months later: all inside
    if first_complete - span[0] < 2:
        return [], None
    delay = dated["published"] - dated["year"]
    early, later = delay[dated["year"] < first_complete], delay[dated["year"] >= first_complete]
    if len(early) < 3 or len(later) < 3 or early.median() < later.median() + 1.5:
        return [], None
    return list(range(span[0], first_complete)), start


def month_year(fractional: float) -> str:
    year = int(fractional)
    month = min(12, int((fractional - year) * 12) + 1)
    return f"{date(year, month, 1):%B %Y}"


def make_context(frame: pd.DataFrame, plan: AnalysisPlan, year_basis: str, ref_year: float | None = None) -> Context:
    """`ref_year`: when the export was made, as a fractional year (2026.04 = mid-January 2026); default
    today. It sets the publication-lag zone."""
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
        ref = ref_year or fractional_year(date.today())
        if re.search(r"priority|filing", year_basis):
            # Filings of the last ~18 months before the export are not all published yet.
            first_incomplete = math.floor(ref - 1.5)
        else:  # publication dates: only a year that had not ended at the export is partial
            first_incomplete = math.floor(ref) + (1 if ref % 1 > 0.97 else 0)
        incomplete = [y for y in range(span[0], span[1] + 1) if y >= first_incomplete]
    early, start = cut_off_at_start(frame, year_basis, span)
    return Context(
        plan=plan, year_basis=year_basis, groups=groups, levels=[c.name for c in plan.complexity_levels],
        problem_label=plan.problem_label or "Problem", colors=colors, incomplete_years=incomplete, years=span,
        has_details="relevance" in frame, has_clusters="cluster" in frame, early_years=early, window_start=start,
    )


def early_years_text(ctx: Context) -> str:
    """Why the first years are left out of comparisons, for captions and facts."""
    if not ctx.early_years:
        return ""
    first, last = ctx.early_years[0], ctx.early_years[-1]
    span = f"{first}–{last}" if last > first else str(first)
    return (f"The export holds publications from {month_year(ctx.window_start)} on, so inventions first filed in "
            f"{span} appear only when published late (e.g. as grants): those years are incomplete and left out of "
            "growth and trend comparisons.")


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


def growth(complete: pd.Series) -> tuple[str, str]:
    """(caption text, fact) for the growth of patents per year over complete years.

    With six or more complete years, compares the average of the first three with the last three, so one
    unusual year at either end does not decide the rate; at fewer than 2 patents a year at the start, a
    rate would mislead and the averages are reported instead.
    """
    complete = complete.sort_index()
    years = list(complete.index)
    if len(years) >= 6:
        first, last = complete.iloc[:3], complete.iloc[-3:]
        a, b = first.mean(), last.mean()
        span = f"{years[0]}–{years[2]}", f"{years[-3]}–{years[-1]}"
        averages = f"average {a:.1f} patents a year in {span[0]}, {b:.1f} in {span[1]}"
        if a < 2:
            text = f"{averages}; too few patents at the start for a meaningful growth rate"
            # Worded as a level, not a growth: "average 3.3" was read as "growth of 3.3 patents a year".
            return text, (f"Filing level, complete years: {a:.1f} patents a year on average in {span[0]} and {b:.1f} "
                          f"a year in {span[1]} (patents per year, not a growth rate); too few patents at the start "
                          "for a meaningful growth rate.")
        rate = (b / a) ** (1 / (years[-2] - years[1])) - 1
        text = f"{averages}: {rate * 100:+.1f} % a year"
        return text, f"Growth (3-year averages, complete years): {text}."
    rate, y0, y1 = cagr(complete)
    if rate is None or complete.get(y0, 0) < 5:
        return "too few complete years or patents for a growth rate", "Growth: too few complete years or patents for a growth rate."
    text = f"CAGR {y0}–{y1} {rate * 100:+.1f} % a year"
    return text, f"Growth (complete years): {text}."


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
    if ctx.early_years:
        fig.add_vrect(x0=min(ctx.early_years) - 0.5, x1=max(ctx.early_years) + 0.5, fillcolor="#999",
                      opacity=0.12, line_width=0, annotation_text="incomplete (export starts later)",
                      annotation_position="top left", annotation_font_size=11)
    fig.update_xaxes(dtick=1)
    _layout(fig, f"Patents per year by technology group ({ctx.year_basis})")
    complete = total.drop(ctx.partial_years, errors="ignore")
    growth_text, growth_fact = growth(complete)
    peak = int(complete.idxmax()) if len(complete) and complete.max() > 0 else None
    caption = (f"Bars: patents per year and technology group; line: all patents. Year basis: {ctx.year_basis}. "
               + ("Shaded at the end: years incomplete because applications publish about 18 months after filing. "
                  if ctx.incomplete_years else "")
               + (f"Shaded at the start: {early_years_text(ctx)} " if ctx.early_years else "")
               + f"Growth, on complete years only: {growth_text}.")
    # Shares compared between the halves of the complete years: cut-short years would tilt them.
    full = dated[~dated["year"].isin(ctx.partial_years)]
    if full["year"].nunique() >= 2:
        dated, lo, hi = full, int(full["year"].min()), int(full["year"].max())
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
    if ctx.early_years:
        facts.append(early_years_text(ctx) + " A lower count in those years is not a lower activity.")
    facts.append(growth_fact)
    if peak is not None:
        facts.append(f"Peak complete year: {peak} ({int(complete.max())} patents).")
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
    partial = set(ctx.partial_years)
    labels = [(f"{p0}–{p1}" if p1 > p0 else str(p0)) + ("*" if partial & set(range(p0, p1 + 1)) else "")
              for p0, p1 in periods]
    period_of = {y: lab for (p0, p1), lab in zip(periods, labels) for y in range(p0, p1 + 1)}
    grid = pd.crosstab(dated["tech_group"], dated["year"].map(period_of)).reindex(columns=labels, fill_value=0)
    grid = grid.reindex([g for g in ctx.groups if g in grid.index])
    grid.index.name = "Technology group"
    grid.columns.name = None
    star = ""
    if any(lab.endswith("*") for lab in labels):
        star = "* includes years with incomplete counts. " + " ".join(
            ([("At the end: the last years are not all published yet (publication lag), so a lower count there "
               "is not (yet) a decline.")] if ctx.incomplete_years else [])
            + ([(f"At the start: the export holds publications from {month_year(ctx.window_start)} on, so the first "
                 "years are only partly included and their lower counts are not lower activity.")]
               if ctx.early_years else []))
    blocks.append(rb.table(grid.reset_index(), title="Patents per technology group and period", caption=star))
    facts.append("Patents per technology group and period: " + "; ".join(
        f"{g}: " + ", ".join(f"{p} {int(v)}" for p, v in row.items()) for g, row in grid.iterrows()) + "."
        + (f" {star}" if star else ""))
    complete = dated[~dated["year"].isin(ctx.partial_years)]
    if ctx.has_clusters and complete["year"].nunique() >= 4:
        last = int(complete["year"].max())
        recent_from = last - 2  # the last three complete years
        window = f"{recent_from}–{last}"
        base = (complete["year"] >= recent_from).mean()
        n_recent = int((complete["year"] >= recent_from).sum())
        rows = []
        for label, part in complete.groupby("cluster"):
            if len(part) < 5:
                continue
            k = int((part["year"] >= recent_from).sum())
            momentum = round((k / len(part)) / base, 2) if base else np.nan
            rows.append({"Topic cluster": label, "Patents (complete years)": len(part),
                         "First year": int(part["year"].min()),
                         f"Patents in {window}": k, "Momentum": momentum,
                         "Reading": ("growing faster than the field" if momentum >= 1.2 and k >= 3 else
                                     "slowing" if momentum <= 0.8 else "about as the field"),
                         "Technology group": part["tech_group"].mode().iat[0]})
        if rows:
            mom = pd.DataFrame(rows).sort_values("Momentum", ascending=False)
            few = n_recent < 30
            blocks.append(rb.table(mom, title="Topic clusters: momentum", caption=(
                f"Momentum = the cluster's share of its patents in {window} (the last three complete years) divided "
                f"by that share for all patents ({base * 100:.0f} %, {n_recent} patents). Above 1: growing faster "
                "than the field; below 1: slowing. Growing needs at least 1.2 and 3 recent patents. Clusters with "
                "at least 5 patents." + (" Based on few patents: indicative only." if few else ""))))
            growing = mom[mom["Reading"] == "growing faster than the field"]
            slowing = mom[mom["Reading"] == "slowing"]
            def listing(frame):
                return "; ".join(f"{r['Topic cluster']} (momentum {r['Momentum']}, {r[f'Patents in {window}']} of "
                                 f"{r['Patents (complete years)']} patents in {window})" for _, r in frame.iterrows())
            facts.append(f"Topic cluster momentum over {window}, the last three complete years ({n_recent} patents"
                         + ("; few patents, indicative only" if few else "") + "). Growing faster than the field: "
                         + (listing(growing) or "none") + ". Slowing (momentum below 0.8): " + (listing(slowing) or "none")
                         + ". A momentum below 1 means slowing, never growth.")
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
    label = "concentrated" if hhi > 2500 else "moderately concentrated" if hhi > 1500 else "fragmented"
    facts = ["Top assignees: " + "; ".join(f"{n} {int(c)} ({_pct(c, total)})" for n, c in top.head(10).items()) + ".",
             f"Top-5 share {top5 * 100:.0f} %, HHI {hhi:,.0f}, which means {label} (below 1,500 fragmented, "
             "1,500-2,500 moderately concentrated, above 2,500 concentrated)."]
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


def _no_categories(ctx: Context, key: str, section: str, title: str, what: str) -> Exhibit:
    label = ctx.problem_label.lower()
    text = (f"Not done: the plan defines no {label} categories, so each patent's {label} is free text and {what} "
            f"cannot be counted. Add {label} categories in the plan (step 3) and run again; the model then reads "
            "the patents again with those categories.")
    return Exhibit(key, section, title, [rb.note(text)], text)


def problem_solution(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    if "problem" not in a:
        return None
    if not ctx.plan.problems:
        return _no_categories(ctx, "problem_solution", "visuals", f"{ctx.problem_label} vs. solutions",
                              f"a {ctx.problem_label.lower()} × solution map")
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
    only_other = int((a["problem"].fillna("") == "Other").sum())
    if only_other > 0.4 * len(a):
        warn = (f"{only_other} of {len(a)} patents fit none of the plan's {ctx.problem_label.lower()} categories "
                f"(counted as Other): the categories do not cover this data well, so this map shows little. Add "
                "categories in the plan (step 3) and run again.")
        blocks.insert(0, rb.note(warn))
    else:
        warn = ""
    facts = ([warn] if warn else []) + [
        f"{ctx.problem_label} counts: " + ", ".join(f"{p} {int(c)}" for p, c in grid.sum(axis=1).items()) + ".",
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


# Dimensions that describe the same thing (categories "lighter reinforcement", "low-density compounds" next to
# groups "reinforcement materials", "rubber compounds"): when their names overlap this much and the counts are
# this strongly associated, combinations off the natural pairing are empty by definition, not gaps. A strong
# association alone is not enough: "belt" and "lighter reinforcement" go together for a reason worth knowing.
LINKED_V = 0.6
LINKED_NAMES = 0.4
_GENERIC_WORDS = {"other", "material", "materials", "design", "system", "systems", "technology", "technologies",
                  "solution", "solutions", "based", "novel", "process", "processes", "general"}


def _name_words(name: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) >= 4 and w not in _GENERIC_WORDS]
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words}


def shared_names(problems: list[str], groups: list[str]) -> float:
    """Share of the problem categories whose name shares a word with a technology group's name."""
    group_words = set().union(*(_name_words(g) for g in groups)) if groups else set()
    return sum(bool(_name_words(p) & group_words) for p in problems) / len(problems) if problems else 0.0


def cramers_v(grid: pd.DataFrame) -> float | None:
    """Association between the rows and columns of a count table: 0 = independent, 1 = one determines the other."""
    grid = grid.loc[grid.sum(axis=1) > 0, grid.sum(axis=0) > 0]
    n, k = grid.values.sum(), min(grid.shape) - 1
    if n == 0 or k < 1:
        return None
    expected = np.outer(grid.sum(axis=1), grid.sum(axis=0)) / n
    chi2 = float((((grid.values - expected) ** 2) / expected).sum())
    return float(np.sqrt(chi2 / (n * k)))


def white_space(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    if "problem" not in a:
        return None
    if not ctx.plan.problems:
        return _no_categories(ctx, "white_space", "gaps", "White space", "under-explored combinations")
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
    v = cramers_v(grid)
    linked = v is not None and v >= LINKED_V and shared_names(list(grid.index), list(grid.columns)) >= LINKED_NAMES
    label = ctx.problem_label.lower()
    linked_note = (f"{ctx.problem_label} and technology group describe much the same thing here (their categories "
                   f"share names, and Cramér's V is {v:.2f}): each {label} mostly comes with one technology group by "
                   "definition, so the other combinations are not flagged as gaps. Plan categories that answer a "
                   "different question (e.g. which component, against which kind of solution) make this analysis "
                   "meaningful." if linked else "")
    focus = focus_names(ctx.plan)
    considered = [p for p in grid.index if not focus or p in focus]
    least = ctx.plan.white_space_support

    def thin(support: int) -> str:
        """The gap text when the cells around a combination hold too few patents to judge it (the plan's rule)."""
        return f"{INSUFFICIENT}: {support} patents in its row and column together (the rule needs {least})"

    for p in considered if not linked else []:
        for g in grid.columns:
            expected = row_tot[p] * col_tot[g] / n if n else 0
            observed = int(grid.loc[p, g])
            if observed > 0.5 * expected or row_tot[p] == 0:
                continue
            support = int(row_tot[p] + col_tot[g] - 2 * observed)  # the other cells of its row and column
            if least and support < least:
                rows.append({ctx.problem_label: p, "Technology group": g, "Patents": observed,
                             "Expected": round(expected, 1), "Gap": thin(support)})
            elif expected >= 1.5:
                rows.append({ctx.problem_label: p, "Technology group": g, "Patents": observed,
                             "Expected": round(expected, 1), "Gap": "few patents for this combination"})
    for p in considered:
        if row_tot[p] == 0:
            rows.append({ctx.problem_label: p, "Technology group": "any", "Patents": 0, "Expected": np.nan,
                         "Gap": f"{INSUFFICIENT}: no patents in this set" if least else "no patents at all in this set"})
    if "complexity" in a and ctx.levels:
        top_level = ctx.levels[-1]
        cplx = prob[prob["complexity"] == top_level]["problem"].value_counts()
        level_total = int((prob["complexity"] == top_level).sum())
        for p in considered:
            if row_tot[p] >= 3 and cplx.get(p, 0) == 0:
                support = int(row_tot[p] + level_total)
                gap = thin(support) if least and support < least else f"no {top_level.lower()} solution patented yet"
                rows.append({ctx.problem_label: p, "Technology group": f"{top_level} solutions", "Patents": 0,
                             "Expected": np.nan, "Gap": gap})
    scope = (f" Only the focus {label} categories ({', '.join(focus)}) are assessed." if focus else "")
    rule = (f" A combination counts as white space only when the other cells of its row and column hold at least "
            f"{least} patents together; otherwise it is marked “{INSUFFICIENT}”, never an opening." if least else "")
    if not rows:
        text = linked_note or (f"No clear white space: every {label} × technology group combination has about as "
                               "many patents as its row and column totals predict." + scope)
        return Exhibit("white_space", "gaps", "White space", [rb.note(text)],
                       linked_note or "No clear white-space combinations." + scope)
    table = pd.DataFrame(rows)
    table = (table.assign(_thin=table["Gap"].str.startswith(INSUFFICIENT))
             .sort_values(["_thin", "Patents", "Expected"], ascending=[True, True, False], na_position="last")
             .drop(columns="_thin"))
    caption = linked_note or (
        "Expected = (row total × column total) / all, i.e. the count if solutions were spread evenly across the "
        f"{label} categories. Cells with at most half the expected count are flagged. Few patents can mean an opening, or a "
        "combination that does not make technical sense: check before acting." + scope + rule)
    leads = table[~table["Gap"].str.startswith(INSUFFICIENT)]
    thin_rows = table[table["Gap"].str.startswith(INSUFFICIENT)]
    facts = ("White space: " + ("; ".join(
        f"{r[ctx.problem_label]} × {r['Technology group']}: {r['Patents']} patents"
        + (f" (expected {r['Expected']})" if not pd.isna(r["Expected"]) else "") + f", {r['Gap']}"
        for _, r in leads.head(8).iterrows()) or "no combination meets the rule") + "."
        + (f" {linked_note}" if linked_note else ""))
    if len(thin_rows):
        facts += (" Insufficient data (not white space, too few patents nearby to judge): "
                  + "; ".join(f"{r[ctx.problem_label]} × {r['Technology group']}" for _, r in thin_rows.head(8).iterrows())
                  + "." + scope)
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
    complete_years = a["year"].dropna()
    complete_years = complete_years[~complete_years.isin(ctx.partial_years)]
    last = int(complete_years.max()) if len(complete_years) else None
    window = f"{last - 2}–{last}" if last else ""
    rows, jewels, facts, pooled = [], [], [], []
    for comp, hits in chosen:
        part = a[a["assignee"].isin(hits)]
        recent = int(part["year"].between(last - 2, last).sum()) if last else 0
        row = {"Competitor": comp, "Patents": len(part), "Share": _pct(len(part), total),
               "First–last year": (f"{int(part['year'].min())}–{int(part['year'].max())}"
                                   if part["year"].notna().any() else "n/a"),
               f"In {window}" if last else "Recent": recent,
               "Main technology group": part["tech_group"].mode().iat[0] if len(part) else ""}
        if len(hits) > 1 or (hits and hits[0].lower() != comp.lower()):
            pooled.append(f"{comp} = " + " + ".join(hits))
        if "problem" in part:
            probs = _explode(part, "problem")["problem"].value_counts()
            named = probs.drop("Other", errors="ignore")  # "Other" only when nothing else is named
            row[f"Main {ctx.problem_label.lower()}"] = (named if len(named) else probs).index[0] if len(probs) else ""
        if ctx.has_clusters and len(part):
            row["Largest topic cluster"] = part["cluster"].mode().iat[0]
        rows.append(row)
        facts.append(f"{comp}: {len(part)} patents ({row['Share']}), mainly {row['Main technology group']}, "
                     f"{recent} in {window}, the last three complete years"
                     + (f" (counted together: {' + '.join(hits)})" if len(hits) > 1 else ""))
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
    watch_caption = (f"Competitors: {source}. Recent activity counts the last three complete years ({window}); "
                     "later years are still incomplete because of the publication lag." if last else
                     f"Competitors: {source}.")
    if pooled:
        watch_caption += " Assignees counted together: " + "; ".join(pooled) + "."
    blocks = [rb.table(pd.DataFrame(rows), title="Competitor watch", caption=watch_caption),
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


_UNSTATED_VALUES = {"", "not stated", "none", "n/a", "na", "not specified", "unknown", "nan"}
_NUMBER_UNIT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|kg|g|db|hz|mm|phr)(?![a-z])", re.IGNORECASE)


def _patents_per_value(stated: pd.Series) -> tuple[pd.DataFrame, pd.Series] | None:
    """For a column that often lists several values per patent: (one row per patent and value, patents per value),
    if a few values cover most mentions; else None."""
    if stated.str.contains(r"[;,]").mean() < 0.2:
        return None
    values = stated.str.lower().str.split(r"\s*[;,]\s*").explode().str.strip().str.rstrip(".")
    pairs = pd.DataFrame({"pid": values.index, "Value": values.values})
    pairs = pairs[pairs["Value"].ne("") & ~pairs["Value"].isin(_UNSTATED_VALUES)].drop_duplicates()
    counts = pairs["Value"].value_counts()
    if not len(counts) or counts.iloc[0] < 2 or counts.head(10).sum() < 0.5 * len(pairs):
        return None
    return pairs, counts


def extra_columns(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    """One view per extra column of the plan: how many patents state it, and what they state."""
    import plotly.express as px

    from .enrich import build_fields

    specs = [f for f in build_fields(ctx.plan) if f.key.startswith("x_") and f.key in a]
    if not specs:
        return None
    blocks, facts = [], []
    for spec in specs:
        if spec.choices:  # fixed values: counted per value, and against the problem dimension
            part_blocks, part_facts = _choice_column(a, ctx, spec)
            blocks += [rb.heading(spec.column, level=4)] + part_blocks
            facts.append(part_facts)
            continue
        values = a[spec.key].fillna("").astype(str).str.strip()
        stated = values[~values.str.lower().isin(_UNSTATED_VALUES)]
        n, k = len(values), len(stated)
        blocks.append(rb.heading(spec.column, level=4))
        if not k:
            blocks.append(rb.note(f"No patent states a value for “{spec.column}”."))
            facts.append(f"{spec.column}: no patent states a value.")
            continue
        found = stated.str.extract(_NUMBER_UNIT)
        found.columns = ["number", "unit"]
        found = found.dropna()
        found["unit"] = found["unit"].str.lower()
        unit = found["unit"].mode().iat[0] if len(found) else None
        numeric = found[found["unit"] == unit] if unit else found
        normal = stated.str.lower().str.rstrip(".")
        top = normal.value_counts()
        if unit and len(numeric) >= max(5, 0.5 * k):
            numbers = numeric["number"].astype(float)
            frame = pd.DataFrame({"Value": numbers, "tech_group": a.loc[numbers.index, "tech_group"]})
            fig = px.box(frame, x="tech_group", y="Value", points="all", color="tech_group",
                         color_discrete_map=ctx.colors, category_orders={"tech_group": ctx.groups},
                         labels={"tech_group": "Technology group", "Value": f"{spec.column} ({unit})"})
            _layout(fig, f"{spec.column} ({unit}) by technology group", height=420)
            fig.update_layout(showlegend=False)
            summary = frame.groupby("tech_group")["Value"].agg(["count", "median", "min", "max"]).round(2)
            summary = summary.reindex([g for g in ctx.groups if g in summary.index]).reset_index()
            summary.columns = ["Technology group", "Patents", "Median", "Min", "Max"]
            blocks += [rb.chart(fig, caption=f"{k} of {n} patents state a value; {len(numeric)} as a number in {unit}. "
                                             "Values as stated in the patents: test conditions and baselines differ."),
                       rb.table(summary, title=f"{spec.column}: values in {unit} by technology group")]
            facts.append(f"{spec.column}: {k} of {n} patents state a value, {len(numeric)} in {unit}; median "
                         f"{numbers.median():.1f} {unit} (range {numbers.min():g}–{numbers.max():g}); by technology group: "
                         + "; ".join(f"{r['Technology group']} median {r['Median']:g} ({r['Patents']} patents)"
                                     for _, r in summary.iterrows()) + ".")
        elif (per_value := _patents_per_value(stated)) is not None:
            # Several values per patent ("durability; handling"): count the patents naming each value, so a value
            # is not split over every combination it appears in.
            pairs, counts_by_value = per_value
            keep = counts_by_value.head(10).index
            pairs = pairs[pairs["Value"].isin(keep)].assign(tech_group=lambda d: a.loc[d["pid"], "tech_group"].values)
            counts = pairs.groupby(["Value", "tech_group"]).size().rename("Patents").reset_index()
            fig = px.bar(counts, y="Value", x="Patents", color="tech_group", orientation="h",
                         color_discrete_map=ctx.colors, category_orders={"Value": list(keep), "tech_group": ctx.groups},
                         labels={"tech_group": "Technology group", "Value": ""})
            _layout(fig, f"{spec.column}: most frequent values", height=max(320, 30 * len(keep) + 140))
            blocks.append(rb.chart(fig, caption=f"{k} of {n} patents state a value; a patent can name several, so each "
                                                "bar counts the patents naming that value (spelling as written by the "
                                                "model)."))
            facts.append(f"{spec.column}: {k} of {n} patents state a value; patents naming each value (a patent can "
                         "name several): " + "; ".join(f"{v} {c}" for v, c in counts_by_value.head(8).items()) + ".")
        elif len(top) and top.head(10).sum() >= 0.5 * k and top.iloc[0] >= 2:
            keep = top.head(10).index
            frame = pd.DataFrame({"Value": normal[normal.isin(keep)], "tech_group": a.loc[normal[normal.isin(keep)].index, "tech_group"]})
            counts = frame.groupby(["Value", "tech_group"]).size().rename("Patents").reset_index()
            fig = px.bar(counts, y="Value", x="Patents", color="tech_group", orientation="h",
                         color_discrete_map=ctx.colors, category_orders={"Value": list(keep), "tech_group": ctx.groups},
                         labels={"tech_group": "Technology group", "Value": ""})
            _layout(fig, f"{spec.column}: most frequent values", height=max(320, 30 * len(keep) + 140))
            blocks.append(rb.chart(fig, caption=f"{k} of {n} patents state a value; the 10 most frequent values "
                                                f"cover {top.head(10).sum()} of them (spelling as written by the model)."))
            facts.append(f"{spec.column}: {k} of {n} patents state a value; most frequent: "
                         + "; ".join(f"{v} {c}" for v, c in top.head(6).items()) + ".")
        else:
            examples = a.loc[stated.index[:10]]
            blocks.append(rb.table(pd.DataFrame({"Patent ID": examples["id"], "Title": examples["title"].map(_short),
                                                 spec.column: stated.iloc[:10].values}),
                                   title=f"{spec.column}: examples",
                                   caption=f"{k} of {n} patents state a value; the values vary too much to count, so "
                                           "examples are shown. Ask a follow-up question to group them."))
            facts.append(f"{spec.column}: {k} of {n} patents state a value; values vary (e.g. "
                         + "; ".join(stated.iloc[:3].map(lambda v: _short(v, 60))) + ").")
    return Exhibit("extra_columns", "custom", "Your extra columns", blocks, " ".join(facts))


def _values(a: pd.DataFrame, col: str, multi: bool) -> pd.DataFrame:
    """One row per patent and value ('_row': the patent's index); empty or unstated values become "Not stated"."""
    values = a[col].fillna("").astype(str).str.strip()
    if multi:
        values = values.str.split(r"\s*;\s*")
    out = pd.DataFrame({"_row": a.index, "value": values.values}).explode("value")
    out["value"] = out["value"].fillna("").astype(str).str.strip()
    out.loc[out["value"].str.lower().isin(_UNSTATED_VALUES), "value"] = NOT_STATED_LABEL
    return out.drop_duplicates()


def _ordered(values: pd.Series, first: list[str]) -> list[str]:
    """The values present, those in `first` in its order, then the rest by frequency, "Not stated" last."""
    present = values.value_counts()
    order = [v for v in first if v in present.index and v != NOT_STATED_LABEL]
    order += [v for v in present.index if v not in order and v != NOT_STATED_LABEL]
    return order + ([NOT_STATED_LABEL] if NOT_STATED_LABEL in present.index else [])


def _choice_column(a: pd.DataFrame, ctx: Context, spec) -> tuple[list[rb.Block], str]:
    """A fixed-value extra column: patents per value and, unless it repeats the problem categories, a map of its
    values against the problem dimension (e.g. coverage × objective)."""
    import plotly.express as px

    vals = _values(a, spec.key, spec.multi)
    order = _ordered(vals["value"], spec.choices)
    counts = vals["value"].value_counts().reindex(order)
    n = len(a)
    facts = (f"{spec.column} (n = {n}): " + ", ".join(f"{v} {int(c)} ({_pct(c, n)})" for v, c in counts.items()) + ".")
    problems = [c.name for c in ctx.plan.problems]
    same_dimension = sum(c in problems for c in spec.choices) >= 0.5 * len(spec.choices)
    caption = ("A patent can have several values. " if spec.multi else "") + (
        f"Values as read by the model from each patent; “{NOT_STATED_LABEL}” is kept as its own category."
        + (" " + "; ".join(f"{k}: {v}" for k, v in spec.notes.items()) + "." if spec.notes else ""))
    if "problem" not in a or not problems or same_dimension:
        frame = counts.rename_axis(spec.column).rename("Patents").reset_index()
        fig = px.bar(frame, x="Patents", y=spec.column, orientation="h", text="Patents",
                     category_orders={spec.column: order})
        _layout(fig, f"{spec.column}: patents per value (n = {n})", height=max(300, 32 * len(order) + 140))
        return [rb.chart(fig, caption=caption)], facts
    prob = _explode(a.assign(problem=a["problem"].fillna("")), "problem")
    pairs = prob[["_row", "problem"]].merge(vals, on="_row")
    cols = [p for p in problems + ["Other"] if p in set(pairs["problem"])]
    grid = pd.crosstab(pairs["value"], pairs["problem"]).reindex(index=order, columns=cols, fill_value=0)
    if grid.empty or not cols:
        return [rb.note(f"No patent has both a {spec.column.lower()} and a {ctx.problem_label.lower()}.")], facts
    fig = _heatmap(grid, list(grid.columns), list(grid.index), "YlOrRd")
    _layout(fig, f"{spec.column} × {ctx.problem_label} (n = {n} patents)", height=max(340, 36 * len(grid) + 170))
    fig.update_xaxes(title_text=ctx.problem_label, tickangle=-30)
    fig.update_yaxes(title_text=spec.column)
    cells = grid.stack().sort_values(ascending=False)
    facts += (f" {spec.column} × {ctx.problem_label}, densest: "
              + "; ".join(f"{v} × {p} {int(c)}" for (v, p), c in cells[cells > 0].head(8).items()) + ".")
    shares = []
    for value in [v for v in order if v != NOT_STATED_LABEL][:4]:
        row = grid.loc[value]
        if row.sum():
            shares.append(f"{value}: " + ", ".join(f"{p} {int(c)}" for p, c in row.sort_values(ascending=False)
                                                   .head(3).items() if c))
    if shares:
        facts += f" Main {ctx.problem_label.lower()}s per {spec.column.lower()}: " + "; ".join(shares) + "."
    total_col = f"Patents with this {spec.column.lower()}"
    table = grid.assign(**{total_col: vals["value"].value_counts().reindex(grid.index).fillna(0).astype(int)})
    table = table.rename_axis(index=spec.column, columns=None).reset_index()
    return [rb.chart(fig, caption=caption + f" Columns: the {ctx.problem_label.lower()}s each patent addresses (a "
                                            "patent with several counts in each)."),
            rb.table(table, title=f"{spec.column} × {ctx.problem_label}: patents")], facts


def focus_set(a: pd.DataFrame, ctx: Context) -> Exhibit | None:
    """The patents whose problem includes a focus category: size, players (with alive counts when the export has
    a legal status), the split by every fixed-value column, and the most relevant patents with their claimed
    parameters. The executive summary leads with these."""
    from .enrich import build_fields

    names = focus_names(ctx.plan)
    if not names or "problem" not in a:
        return None
    label = ctx.problem_label.lower()
    in_focus = a["problem"].fillna("").str.split(r"\s*;\s*").map(lambda vals: any(v in names for v in vals))
    f = a[in_focus]
    n, total = len(f), len(a)
    title = "Focus set: " + ", ".join(names)
    head = (f"Focus set (patents whose {label} includes {' or '.join(names)}): {n} of {total} analysed patents "
            f"({_pct(n, total)}).")
    if not n:
        return Exhibit("focus", "summary", title, [rb.note(head + " No patent in this set addresses the focus.")], head)
    facts = [head]
    per = {name: int(f["problem"].str.split(r"\s*;\s*").map(lambda vals, name=name: name in vals).sum())
           for name in names}
    facts.append("Focus patents per category (a patent can address several): "
                 + ", ".join(f"{k} {v}" for k, v in per.items()) + ".")
    has_alive = "alive" in f
    if has_alive:
        alive = f["alive"].value_counts()
        facts.append(f"Legal status of the focus patents: {int(alive.get('Yes', 0))} alive, {int(alive.get('No', 0))} "
                     f"dead, {int(alive.get('Unknown', 0))} unknown.")
    named = f[f["assignee"].fillna("").ne("")]
    top = named["assignee"].value_counts().head(10)
    players = pd.DataFrame({"Assignee": top.index, "Patents": top.values})
    if has_alive:
        players["Alive"] = [int(((named["assignee"] == x) & (named["alive"] == "Yes")).sum()) for x in top.index]
    facts.append("Leading assignees in the focus set: " + "; ".join(
        f"{r.Assignee} {r.Patents}" + (f" ({r.Alive} alive)" if has_alive else "") for r in players.head(6).itertuples())
        + ".")
    splits = []
    dims = [(spec.column, spec.key, spec.choices, spec.multi) for spec in build_fields(ctx.plan)
            if spec.key.startswith("x_") and spec.choices and spec.key in f]
    dims += [("Technology group", "tech_group", ctx.groups, False)]
    if "complexity" in f:
        dims.append(("Complexity", "complexity", ctx.levels, False))
    if "segment" in f:
        dims.append(("Segment", "segment", ctx.plan.segments, True))
    for column, key, first, multi in dims:
        vals = _values(f, key, multi)
        counts = vals["value"].value_counts().reindex(_ordered(vals["value"], first))
        splits += [{"Dimension": column, "Value": v, "Patents": int(c), "Share of focus set": _pct(c, n)}
                   for v, c in counts.items()]
        facts.append(f"{column} in the focus set: " + ", ".join(f"{v} {int(c)} ({_pct(c, n)})" for v, c in counts.items())
                     + ".")
    if "segment" in a:
        unstated = int(_values(a, "segment", True).groupby("_row")["value"].apply(
            lambda v: set(v) <= {NOT_STATED_LABEL, "Not specified"}).sum())
        facts.append(f"Segment not stated: {unstated} of {total} analysed patents ({_pct(unstated, total)}).")
    rank = pd.DataFrame(index=f.index)
    rank["relevance"] = f["relevance"].map({"High": 0, "Partial": 1}).fillna(2) if "relevance" in f else 0
    rank["alive"] = f["alive"].map({"Yes": 0, "Unknown": 1}).fillna(2) if has_alive else 0
    level = {lv: i for i, lv in enumerate(ctx.levels)}
    rank["level"] = -f["complexity"].map(level).fillna(-1) if "complexity" in f else 0
    rank["year"] = -f["year"].fillna(0)
    best = f.loc[rank.sort_values(["relevance", "alive", "level", "year"]).index].head(15)
    extra_cols = [(column, key) for column, key, _, _ in dims if key.startswith("x_")]
    rows = []
    for r in best.itertuples():
        row = {"Patent ID": r.id, "Year": "" if pd.isna(r.year) else int(r.year), "Assignee": r.assignee}
        if has_alive:
            row["Alive"] = r.alive
        for column, key in extra_cols:
            row[column] = getattr(r, key, "")
        row["Complexity"] = getattr(r, "complexity", "")
        row["Key parameters"] = _short(getattr(r, "key_parameters", ""), 160)
        row["Core claim"] = _short(getattr(r, "core_claim", "") or r.first_claim, 200)
        rows.append(row)
    best_df = pd.DataFrame(rows)
    best_df = best_df.loc[:, [c for c in best_df.columns if best_df[c].astype(str).str.strip().ne("").any()]]
    ranked = "alive first, then " if has_alive else ""
    facts.append(f"Most relevant focus patents (high relevance, {ranked}most complex claims, newest first): " + "; ".join(
        f"{r['Patent ID']} ({r['Assignee']}, {r['Year']}" + (f", alive {r['Alive']}" if has_alive else "")
        + "".join(f", {r[c]}" for c, _ in extra_cols[:2] if r.get(c)) + ")" for r in rows[:6]) + ".")
    windows = [f"{r['Patent ID']}: {r['Key parameters']}" for r in rows
               if str(r.get("Key parameters", "")).strip().lower() not in _UNSTATED_VALUES][:6]
    if windows:
        facts.append("Claimed parameter windows in these patents (verbatim): " + "; ".join(windows) + ".")
    blocks = [
        rb.metrics([("Focus patents", f"{n:,}"), ("Share of analysed set", _pct(n, total))]
                   + ([("Alive", f"{int((f['alive'] == 'Yes').sum()):,}")] if has_alive else [])),
        rb.table(players, title="Leading assignees in the focus set",
                 caption=("Alive: from the export's legal status (pending applications count as alive)."
                          if has_alive else "Map a legal-status column to count alive patents.")),
        rb.table(pd.DataFrame(splits), title="How the focus set divides",
                 caption=f"Each dimension counts the {n} focus patents; “{NOT_STATED_LABEL}” is kept as a category."),
        rb.table(best_df, title="Most relevant focus patents",
                 caption=f"Up to 15 patents: high relevance, {ranked}most complex claims, newest first. Key "
                         "parameters are the claimed windows as read by the model; check them in the claims."),
    ]
    return Exhibit("focus", "summary", title, blocks, " ".join(facts))


EXHIBITS = [focus_set, trend, geography, segments, problem_solution, players, competitors, complexity,
            process_insights, roadmap, white_space, extra_columns]


def run_analytics(frame: pd.DataFrame, plan: AnalysisPlan, year_basis: str, ref_year: float | None = None,
                  ctx: Context | None = None) -> tuple[list[Exhibit], list[str]]:
    """All exhibits that the data supports; returns (exhibits, notes on skipped ones).

    `ctx` is the context from make_context(frame, plan, year_basis, ref_year), if the caller has it already.
    """
    if ctx is None:
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


