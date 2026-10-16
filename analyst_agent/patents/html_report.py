"""The report as one self-contained HTML file (charts stay interactive and work offline)."""

from __future__ import annotations

import html
import re

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
@media print { .tablewrap { max-height: none; overflow: visible; } h2 { break-before: page; } }
"""


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)


def markdown_to_html(text: str) -> str:
    """The Markdown the report uses: headings, bullet and numbered lists, paragraphs, bold, italics, code."""
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

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m_head = re.match(r"^(#{1,6})\s+(.*)", line)
        m_item = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)", line)
        if not line.strip():
            flush_para()
            close_lists()
        elif m_head:
            flush_para()
            close_lists()
            level = min(len(m_head.group(1)) + 2, 6)
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


def _table(df) -> str:
    shown = df.head(500)
    body = shown.to_html(index=False, classes="data", border=0, na_rep="", escape=True, float_format=lambda v: f"{v:,.2f}")
    more = f'<div class="caption">First 500 of {len(df):,} rows; the CSV has all.</div>' if len(df) > 500 else ""
    return f'<div class="tablewrap">{body}</div>{more}'


def report_html(blocks: list[rb.Block], title: str, subtitle: str = "") -> bytes:
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
        if block.title and block.kind not in ("plotly", "download"):
            parts.append(f'<div class="block-title">{html.escape(block.title)}</div>')
        if block.kind == "markdown":
            parts.append(markdown_to_html(block.text))
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
            parts.append(block.figure.to_html(full_html=False, include_plotlyjs=not plotly_loaded,
                                              config={"displaylogo": False, "responsive": True}))
            plotly_loaded = True
        elif block.kind == "download":
            parts.append(f'<div class="caption">Download in the app: {html.escape(block.file_name)}</div>')
            continue
        if block.caption:
            parts.append(f'<div class="caption">{_inline(block.caption)}</div>')
    body = "\n".join(parts)
    toc_html = f'<nav class="toc"><strong>Contents</strong><ol>{"".join(toc)}</ol></nav>' if toc else ""
    body = body.replace("<!--TOC-->", toc_html, 1) if "<!--TOC-->" in body else toc_html + body
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")
    return page.encode("utf-8")
