"""The report's tables and the data behind its charts as one Excel workbook, one sheet each."""

from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

from . import report as rb

MAX_SHEETS = 120
MAX_POINTS = 5_000  # a chart with more points (the patent map) is a picture of the patents, not a table
_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _axis_title(fig, axis: str) -> str:
    try:
        return str(getattr(fig.layout, axis).title.text or "")
    except AttributeError:
        return ""


def figure_table(fig) -> pd.DataFrame | None:
    """The numbers a chart shows: a heatmap as its grid, bars and lines as one row per category and one column per
    series. None for charts without a table behind them (maps, box plots, treemaps)."""
    heat = [t for t in fig.data if t.type == "heatmap" and t.z is not None]
    if heat:
        t = heat[0]
        z = np.asarray(t.z)
        x = list(t.x) if t.x is not None else list(range(z.shape[1]))
        y = list(t.y) if t.y is not None else list(range(z.shape[0]))
        grid = pd.DataFrame(z, index=y, columns=x)
        return grid.rename_axis(_axis_title(fig, "yaxis") or "").reset_index()
    traces = [t for t in fig.data if t.type in ("bar", "scatter") and t.x is not None and t.y is not None]
    if not traces or sum(len(t.x) for t in traces) > MAX_POINTS:
        return None
    rows = []
    for t in traces:
        horizontal = t.type == "bar" and t.orientation == "h"
        cats, vals = (t.y, t.x) if horizontal else (t.x, t.y)
        rows += [{"category": c, "series": t.name or "Value", "value": v} for c, v in zip(cats, vals)]
    long = pd.DataFrame(rows)
    if long.empty:
        return None
    horizontal = traces[0].type == "bar" and traces[0].orientation == "h"
    cat_name = _axis_title(fig, "yaxis" if horizontal else "xaxis") or "Category"
    if long["series"].nunique() == 1:
        value_name = _axis_title(fig, "xaxis" if horizontal else "yaxis") or "Value"
        out = long[["category", "value"]].rename(columns={"category": cat_name, "value": value_name})
        return out.reset_index(drop=True)
    wide = long.pivot_table(index="category", columns="series", values="value", aggfunc="sum", sort=False)
    wide.columns.name = None
    return wide.rename_axis(cat_name).reset_index()


def _sheet_name(title: str, number: int, used: set[str]) -> str:
    base = _SHEET_CHARS.sub(" ", f"{number:02d} {title}").strip()[:31].rstrip("' ") or f"{number:02d}"
    name, i = base, 2
    while name.lower() in used:
        suffix = f" ({i})"
        name, i = base[: 31 - len(suffix)] + suffix, i + 1
    used.add(name.lower())
    return name


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Values Excel accepts: text without control characters, lists as text."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    out = out.loc[:, ~pd.Index(out.columns).duplicated()]
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(lambda v: ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else
                                    ", ".join(map(str, v)) if isinstance(v, (list, tuple, set)) else v)
    return out


def report_workbook(blocks: list[rb.Block], title: str) -> bytes | None:
    """Every table of the report and the data of every chart that has a table behind it, one sheet each, with a
    contents sheet first. None when there is nothing to put in it."""
    sheets: list[tuple[str, str, str, pd.DataFrame]] = []  # (section, title, kind, table)
    section, sub = "", ""
    for block in blocks:
        if block.kind == "heading":
            if block.level <= 2:
                section, sub = block.title, ""
            elif block.level == 3:
                sub = block.title
            continue
        name = block.title or sub or section
        if block.kind == "table" and block.table is not None and not block.table.empty:
            sheets.append((section, name, "table", block.table))
        elif block.kind == "cards" and block.items:
            sheets.append((section, name, "table", pd.DataFrame([{
                "Patent ID": i.get("id", ""), "Title": i.get("title", ""), "Details": i.get("meta", ""),
                "Badges": ", ".join(i.get("badges", [])), "Summary": i.get("summary", ""), "Why it matters": i.get("why", ""),
                "Check": i.get("check", ""), "Drawings": i.get("drawings", ""), "Scores": i.get("scores", ""),
                "Figures": "; ".join(label for _, label in i.get("figures", [])) or i.get("note", ""),
                "Link": i.get("link", "")} for i in block.items])))
        elif block.kind == "plotly" and block.figure is not None:
            data = figure_table(block.figure)
            if data is not None and not data.empty:
                chart_title = getattr(getattr(block.figure.layout, "title", None), "text", None) or name
                sheets.append((section, str(chart_title), "chart data", data))
    if not sheets:
        return None
    sheets = sheets[:MAX_SHEETS]
    used = {"contents"}
    names = [_sheet_name(t, i + 1, used) for i, (_, t, _, _) in enumerate(sheets)]
    contents = pd.DataFrame({"Sheet": names, "Section": [s for s, _, _, _ in sheets],
                             "Title": [t for _, t, _, _ in sheets], "Content": [k for _, _, k, _ in sheets],
                             "Rows": [len(d) for _, _, _, d in sheets]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _clean(contents).to_excel(writer, sheet_name="Contents", index=False, startrow=2)
        writer.sheets["Contents"].cell(row=1, column=1, value=title)
        for name, (_, _, _, data) in zip(names, sheets):
            _clean(data).to_excel(writer, sheet_name=name, index=False)
        for ws in writer.sheets.values():
            for column in ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)):
                width = max((len(str(c.value)) for c in column if c.value is not None), default=8)
                ws.column_dimensions[column[0].column_letter].width = min(60, max(8, width + 2))
            ws.freeze_panes = "A2" if ws.title != "Contents" else "A4"
    return buf.getvalue()
