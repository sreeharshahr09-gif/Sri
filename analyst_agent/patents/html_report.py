"""The report as one self-contained HTML file (charts stay interactive and work offline)."""

from __future__ import annotations

import html
import re

import pandas as pd

from . import report as rb

CSS = """
:root { --ink:#1f2933; --muted:#5f6b7a; --line:#e3e7ec; --accent:#c2410c; --soft:#f6f8fa; }
* { box-sizing: border-box; }
body { margin:0; font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color:var(--ink);
       background:#fff; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 30px; margin: 0 0 8px; } h2 { font-size: 23px; margin: 44px 0 10px; padding-top: 8px;
     border-top: 2px solid var(--line); } h3 { font-size: 18px; margin: 28px 0 8px; } h4 { font-size: 16px; }
p, li { max-width: 880px; } .caption { color: var(--muted); font-size: 13px; margin: 4px 0 18px; }
.note { background: #fff7ed; border-left: 4px solid var(--accent); padding: 10px 14px; margin: 12px 0; }
.metrics { display: flex; flex-wrap: wrap; gap: 12px; margin: 14px 0 22px; }
.metric { flex: 1 1 180px; border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; }
.metric .label { color: var(--muted); font-size: 13px; } .metric .value { font-size: 24px; font-weight: 600; }
table.data { border-collapse: collapse; width: 100%; font-size: 13px; margin: 6px 0; }
table.data th { background: var(--soft); text-align: left; font-weight: 600; }
table.data th, table.data td { border-bottom: 1px solid var(--line); padding: 6px 8px; vertical-align: top; }
.tablewrap { overflow-x: auto; max-height: 560px; overflow-y: auto; border: 1px solid var(--line); border-radius: 6px; }
.block-title { font-weight: 600; margin: 18px 0 4px; } nav.toc { background: var(--soft); border-radius: 8px;
       padding: 12px 18px; margin: 18px 0 8px; } nav.toc a { color: var(--ink); } .meta { color: var(--muted); }
details.code { margin: 8px 0 18px; } details.code summary { cursor: pointer; color: var(--muted); }
details.code pre { background: var(--soft); padding: 10px 12px; border-radius: 6px; overflow-x: auto; font-size: 12.5px; }
.cards { display: flex; flex-direction: column; gap: 0; margin: 8px 0 18px; }
.card { display: grid; grid-template-columns: 240px 1fr; gap: 18px; padding: 16px 0; border-bottom: 1px solid var(--line); }
.card-figs figure { margin: 0 0 10px; } .card-figs img { width: 100%; border: 1px solid var(--line); background: #fff; }
.card-figs figcaption { color: var(--muted); font-size: 12px; margin-top: 2px; }
.nofig { height: 150px; border: 1px dashed var(--line); border-radius: 6px; color: var(--muted); font-size: 12px;
         display: flex; align-items: center; justify-content: center; text-align: center; padding: 8px; }
.card-head { font-weight: 600; } .card-head a { color: var(--accent); }
.badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 1px 7px; margin-left: 6px; border-radius: 4px;
         border: 1px solid var(--line); color: var(--muted); } .badge.core { background: #4c1d95; color: #fff; border-color: #4c1d95; }
.card-title { font-weight: 700; text-transform: uppercase; margin: 4px 0 2px; } .card-meta { color: var(--muted); font-size: 13px; }
.card p { margin: 6px 0; } .why { background: #fff7ed; border-left: 4px solid var(--accent); padding: 8px 12px; margin: 8px 0; }
.check { color: #a16207; font-size: 13px; } .scores { color: var(--muted); font-size: 13px; }
@media (max-width: 640px) { .card { grid-template-columns: 1fr; } }
@media print { .tablewrap { max-height: none; overflow: visible; } h2 { break-before: page; } .card { break-inside: avoid; } }
"""


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)


_URL_CELL = re.compile(r"<td>(https?://[^<\s\"]+)</td>")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _table_cells(line: str) -> list[str]:
    """The cells of a Markdown table row. When the row has pipes with spaces around them, only those split cells, so
    a pipe inside a cell ("Peak |Fy|") stays; a compact row ("|a|b|") splits on every pipe. "\\|" is a literal pipe."""
    inner = line.strip().replace("\\|", "\x00")
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") else inner
    parts = re.split(r"(?<=\s)\|(?=\s)", inner) if re.search(r"\s\|\s", inner) else inner.split("|")
    return [part.strip().replace("\x00", "|") for part in parts]


def _markdown_table(header: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th scope="col">{_inline(cell)}</th>' for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row + [""] * (len(header) - len(row)))
                   + "</tr>" for row in rows)
    return (f'<div class="tablewrap markdown-table"><table class="data"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def markdown_to_html(text: str, heading_offset: int = 2) -> str:
    """The Markdown the report uses: headings, bullet and numbered lists, tables, paragraphs, bold, italics, code.
    Headings move down `heading_offset` levels (by default "#" becomes <h3>, under the report's own headings)."""
    out: list[str] = []
    para: list[str] = []
    stack: list[str] = []  # open lists: "ul" / "ol"

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists() -> None:
        while stack:
            out.append(f"</{stack.pop()}>")

    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if "|" in line and i < len(lines) and _TABLE_SEPARATOR.match(lines[i]):  # a table: header, then |---|
            flush_para()
            close_lists()
            header, rows = _table_cells(line), []
            i += 1
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_table_cells(lines[i]))
                i += 1
            out.append(_markdown_table(header, rows))
            continue
        m_head = re.match(r"^(#{1,6})\s+(.*)", line)
        m_item = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)", line)
        if not line.strip():
            flush_para()
            close_lists()
        elif m_head:
            flush_para()
            close_lists()
            level = max(1, min(len(m_head.group(1)) + heading_offset, 6))
            out.append(f"<h{level}>{_inline(m_head.group(2))}</h{level}>")
        elif m_item:
            flush_para()
            kind = "ol" if m_item.group(2)[0].isdigit() else "ul"
            depth = len(m_item.group(1).replace("\t", "    ")) // 2 + 1
            while len(stack) > depth:
                out.append(f"</{stack.pop()}>")
            if len(stack) == depth and stack[-1] != kind:  # bullets followed by a numbered list
                out.append(f"</{stack.pop()}>")
            while len(stack) < depth:
                stack.append(kind)
                out.append(f"<{kind}>")
            out.append(f"<li>{_inline(m_item.group(3))}</li>")
        elif stack:
            out[-1] = out[-1][:-5] + " " + _inline(line.strip()) + "</li>"  # continuation of a list item
        else:
            para.append(line.strip())
    flush_para()
    close_lists()
    return "\n".join(out)


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
    slug, i = base, 2
    while slug in used:
        slug, i = f"{base}-{i}", i + 1
    used.add(slug)
    return slug


def _cards(items: list[dict]) -> str:
    import base64

    out = []
    for item in items:
        figs = "".join(
            f'<figure><img alt="{html.escape(label)}" src="data:image/png;base64,{base64.b64encode(png).decode("ascii")}">'
            f"<figcaption>{html.escape(label)}</figcaption></figure>" for png, label in item.get("figures", []))
        if not figs:
            figs = f'<div class="nofig">{html.escape(item.get("note") or "No drawing")}</div>'
        number = html.escape(item.get("id", ""))
        link = item.get("link", "")
        if re.match(r"https?://", link):
            number = f'<a href="{html.escape(link)}">{number}</a>'
        badges = "".join(f'<span class="badge{" core" if b == "Core" else ""}">{html.escape(b)}</span>'
                         for b in item.get("badges", []))
        parts = [f'<div class="card-head">{number}{badges}</div>',
                 f'<div class="card-title">{html.escape(item.get("title", ""))}</div>',
                 f'<div class="card-meta">{html.escape(item.get("meta", ""))}</div>',
                 f"<p>{_inline(item.get('summary', ''))}</p>"]
        if item.get("why"):
            parts.append(f'<div class="why">{_inline(item["why"])}</div>')
        if item.get("check"):
            parts.append(f'<div class="check">Check: {_inline(item["check"])}</div>')
        if item.get("drawings"):
            parts.append(f'<div class="scores"><strong>Drawings:</strong> {_inline(item["drawings"])}</div>')
        if item.get("scores"):
            parts.append(f'<div class="scores">{_inline(item["scores"])}</div>')
        out.append(f'<div class="card"><div class="card-figs">{figs}</div><div class="card-body">{"".join(parts)}'
                   "</div></div>")
    return f'<div class="cards">{"".join(out)}</div>'


def _table(df) -> str:
    shown = df.head(500).copy()
    for i in range(shown.shape[1]):  # nullable integers print a missing value as "<NA>": leave the cell empty
        values = shown.iloc[:, i]
        if pd.api.types.is_extension_array_dtype(values) and values.isna().any():
            shown.isetitem(i, values.astype(object).where(values.notna(), ""))
    body = shown.to_html(index=False, classes="data", border=0, na_rep="", escape=True, float_format=lambda v: f"{v:,.2f}")
    body = _URL_CELL.sub(r'<td><a href="\1">open</a></td>', body)  # addresses as links, kept short
    more = f'<div class="caption">First 500 of {len(df):,} rows; the CSV has all.</div>' if len(df) > 500 else ""
    return f'<div class="tablewrap">{body}</div>{more}'


BRAND_CSS = """
.brand { display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap;
         padding: 0 0 14px; margin: 0 0 22px; border-bottom: 1px solid var(--line); }
.brand img { height: 46px; width: auto; } .brand-text { text-align: right; color: var(--muted); font-size: 13px; }
.brand-notice { display: inline-block; margin-top: 4px; padding: 2px 9px; border: 1.5px solid #b91c1c; border-radius: 4px;
                color: #b91c1c; font-weight: 700; font-size: 12px; letter-spacing: .02em; }
.brand-foot { margin-top: 48px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
.brand-foot strong { color: #b91c1c; }
/* On screen the report flows as usual and the footer comes once at the end; printed, the wrapper is a table whose
   footer row the browser repeats at the foot of every page, with room kept for it. */
.pagewrap, .pagewrap > tbody, .pagewrap > tfoot, .pagewrap > tbody > tr, .pagewrap > tfoot > tr,
.pagewrap > tbody > tr > td, .pagewrap > tfoot > tr > td { display: block; width: 100%; padding: 0; border: 0; }
@media print {
  .pagewrap { display: table; table-layout: fixed; border-collapse: collapse; }
  .pagewrap > tbody { display: table-row-group; } .pagewrap > tfoot { display: table-footer-group; }
  .pagewrap > tbody > tr, .pagewrap > tfoot > tr { display: table-row; }
  .pagewrap > tbody > tr > td, .pagewrap > tfoot > tr > td { display: table-cell; }
  .brand-foot { margin-top: 6px; padding-top: 4px; }
  .chartwrap, .metrics, .card, .note { break-inside: avoid; }  /* a chart split over two pages loses the footer */
  h2, h3, h4, .block-title { break-after: avoid; }  /* a heading stays with what follows it */
}
"""


def _brand_header(branding) -> tuple[str, str]:
    """(header, footer) HTML for a report's branding; empty strings without one."""
    if branding is None or branding.empty:
        return "", ""
    logo = (f'<img alt="{html.escape(branding.organisation or "Logo")}" src="{branding.logo_data_uri()}">'
            if branding.logo_shown else "<span></span>")
    notice = branding.notice.strip()
    text = (f"<div>{html.escape(branding.credit_on())}</div>" if branding.credit else "") + (
        f'<div class="brand-notice">{html.escape(notice)}</div>' if notice else "")
    header = f'<header class="brand">{logo}<div class="brand-text">{text}</div></header>'
    foot = " · ".join(x for x in [html.escape(branding.credit_on()) if branding.credit else "",
                                   f"<strong>{html.escape(notice)}</strong>" if notice else ""] if x)
    return header, f'<footer class="brand-foot">{foot}</footer>'


def _with_footer(body: str, footer: str) -> str:
    if not footer:
        return body
    return (f'<table class="pagewrap"><tbody><tr><td>{body}</td></tr></tbody>'
            f"<tfoot><tr><td>{footer}</td></tr></tfoot></table>")


def report_html(blocks: list[rb.Block], title: str, subtitle: str = "", extra_css: str = "",
                markdown_heading_offset: int = 2, branding=None) -> bytes:
    """The blocks as one offline HTML page. `extra_css` is added after the standard styles; Markdown headings move
    down `markdown_heading_offset` levels. `branding` (analyst_agent.branding.Branding) adds the logo, who generated
    the report and its notice at the top, and a footer that repeats on every printed page."""
    used: set[str] = set()
    toc, parts = [], []
    plotly_loaded = False
    for block in blocks:
        if block.kind == "heading":
            level = max(1, min(block.level, 4))
            anchor = _slug(block.title, used)
            if level == 2:
                toc.append(f'<li><a href="#{anchor}">{html.escape(block.title)}</a></li>')
            parts.append(f'<h{level} id="{anchor}">{html.escape(block.title)}</h{level}>')
            if level == 1 and subtitle:
                parts.append(f'<p class="meta">{html.escape(subtitle)}</p>')
                parts.append("<!--TOC-->")
            continue
        if block.title and block.kind not in ("plotly", "download", "code"):
            parts.append(f'<div class="block-title">{html.escape(block.title)}</div>')
        if block.kind == "markdown":
            parts.append(markdown_to_html(block.text, markdown_heading_offset))
        elif block.kind == "note":
            parts.append(f'<div class="note">{_inline(block.text)}</div>')
        elif block.kind == "caption":
            parts.append(f'<div class="caption">{_inline(block.text)}</div>')
        elif block.kind == "metrics":
            cards = "".join(f'<div class="metric"><div class="label">{html.escape(k)}</div>'
                            f'<div class="value">{html.escape(v)}</div></div>' for k, v in block.metrics)
            parts.append(f'<div class="metrics">{cards}</div>')
        elif block.kind == "table" and block.table is not None:
            parts.append(_table(block.table))
        elif block.kind == "plotly" and block.figure is not None:
            parts.append('<div class="chartwrap">'
                         + block.figure.to_html(full_html=False, include_plotlyjs=not plotly_loaded,
                                                config={"displaylogo": False, "responsive": True})
                         + "</div>")
            plotly_loaded = True
        elif block.kind == "image" and block.data:
            import base64

            src = base64.b64encode(block.data).decode("ascii")
            parts.append(f'<img alt="{html.escape(block.title or "chart")}" src="data:image/png;base64,{src}" '
                         'style="max-width:100%">')
        elif block.kind == "cards":
            parts.append(_cards(block.items))
        elif block.kind == "code":
            parts.append(f'<details class="code"><summary>{html.escape(block.title or "Code")}</summary>'
                         f"<pre><code>{html.escape(block.text)}</code></pre></details>")
        elif block.kind == "download":
            parts.append(f'<div class="caption">Download in the app: {html.escape(block.file_name)}</div>')
            continue
        if block.caption:
            parts.append(f'<div class="caption">{_inline(block.caption)}</div>')
    body = "\n".join(parts)
    toc_html = f'<nav class="toc"><strong>Contents</strong><ol>{"".join(toc)}</ol></nav>' if toc else ""
    body = body.replace("<!--TOC-->", toc_html, 1) if "<!--TOC-->" in body else toc_html + body
    header, footer = _brand_header(branding)
    author = (f'<meta name="author" content="{html.escape(branding.author)}">'
              if branding is not None and branding.author.strip() else "")
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{author}'
            f"<title>{html.escape(title)}</title><style>{CSS}{BRAND_CSS if header else ''}{extra_css}</style></head>"
            f"<body><main>{header}{_with_footer(body, footer)}</main></body></html>")
    return page.encode("utf-8")
