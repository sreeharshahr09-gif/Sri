"""PDF rendering with ReportLab: numbered sections, linked contents, outline
bookmarks, page numbers, metadata tables, long-URL wrapping. Layout is verified
in tests by parsing the generated pages."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                Spacer, Table, TableStyle, PageBreak)

ACCENT = colors.HexColor("#0b5fa5")
LIGHT = colors.HexColor("#f2f6fa")
BORDER = colors.HexColor("#cfd8e3")

S = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=17, leading=21,
                            textColor=ACCENT, spaceAfter=4),
    "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9, leading=12,
                          textColor=colors.HexColor("#555555")),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=16,
                         textColor=ACCENT, spaceBefore=14, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=13,
                         spaceBefore=8, spaceAfter=3),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13,
                           spaceAfter=4),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=11,
                            textColor=colors.HexColor("#444444")),
    "url": ParagraphStyle("url", fontName="Helvetica", fontSize=8, leading=10,
                          wordWrap="CJK", textColor=colors.HexColor("#0b3d6e")),
    "toc": ParagraphStyle("toc", fontName="Helvetica", fontSize=9.5, leading=14),
}


class _Doc(BaseDocTemplate):
    """Adds PDF outline bookmarks when a Paragraph carries `_bookmark`."""

    def afterFlowable(self, flowable):
        bm = getattr(flowable, "_bookmark", None)
        if bm:
            key, text, level = bm
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
            self.notify("TOCEntry", (level, text, self.page))


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(18 * mm, 10 * mm, "Tire Scout weekly digest - office circulation copy")
    canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _meta_table(rows):
    data = [[Paragraph(f"<b>{k}</b>", S["small"]), Paragraph(str(v), S["small"])] for k, v in rows]
    t = Table(data, colWidths=[34 * mm, 140 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _item_story(story, idx, item, anchor, detailed=True, level=1):
    title = Paragraph(f'<a name="{anchor}"/><font color="#0b5fa5">{idx}.</font> '
                      f'{_esc(item["title"])}', S["h3"])
    title._bookmark = (anchor, f"{idx}. {item['title'][:60]}", level)
    story.append(title)
    sb = item.get("score_breakdown") or {}
    story.append(_meta_table([
        ("Organization", _esc(item.get("organization") or "unknown")),
        ("Category / maturity", f"{_esc(item.get('category') or '')} / {_esc(item.get('maturity') or '')}"
         + (" (background, newly discovered)" if item.get("is_background") else "")),
        ("Score", f"{(item.get('final_score') or 0):.1f}/10 - rel {sb.get('relevance')}, "
                  f"nov {sb.get('novelty')}, ev {sb.get('evidence_quality')}, "
                  f"imp {sb.get('impact')}, act {sb.get('actionability')}"),
        ("Confidence", _esc(item.get("confidence_label") or "")),
        ("Published", f"{item.get('published_at') or 'unknown'}"),
    ]))
    story.append(Spacer(1, 3))
    if detailed:
        if item.get("what_changed"):
            story.append(Paragraph(f"<b>What changed:</b> {_esc(item['what_changed'])}", S["body"]))
        if item.get("technical_explanation"):
            story.append(Paragraph(f"<b>Technical detail:</b> {_esc(item['technical_explanation'])}", S["body"]))
        if item.get("claimed_benefit"):
            cond = f" <i>Conditions:</i> {_esc(item['baseline_conditions'])}" if item.get("baseline_conditions") else ""
            story.append(Paragraph(f"<b>Claimed benefit:</b> {_esc(item['claimed_benefit'])}{cond}", S["body"]))
        if item.get("limitations"):
            story.append(Paragraph(f'<font color="#7a4d00"><b>Limitations:</b> '
                                   f'{_esc(item["limitations"])}</font>', S["body"]))
        if item.get("suggested_followup"):
            story.append(Paragraph(f"<b>Follow-up:</b> {_esc(item['suggested_followup'])}", S["body"]))
    from html import escape
    source_url = (item.get('source_details') or {}).get('fetch_url') or item['canonical_url']
    story.append(Paragraph(f'Source: <a href="{escape(source_url, quote=True)}">'
                           f'{_esc(source_url)}</a>', S["url"]))
    story.append(Spacer(1, 10))


def build_weekly_pdf(path: Path, *, week_start, week_end, priority, additional,
                     themes, actions, appendix, partial, missing_dates, generated_at):
    doc = _Doc(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
               topMargin=16 * mm, bottomMargin=16 * mm, title=f"Tire Scout weekly {week_start}")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_footer)])

    story = [Paragraph(f"Tire Scout - Weekly Technology Digest", S["title"]),
             Paragraph(f"Week {week_start} to {week_end} &middot; generated {generated_at} IST",
                       S["sub"])]
    if partial:
        story.append(Paragraph(f'<font color="#cc0000"><b>PARTIAL ISSUE</b> - missing daily '
                               f'editions: {", ".join(missing_dates)}</font>', S["body"]))
    # linked contents
    toc_lines = []
    for i, it in enumerate(priority, 1):
        toc_lines.append(f'{i}. <a href="#p{i}">{_esc(it["title"])}</a> '
                         f'({(it.get("final_score") or 0):.1f})')
    if additional:
        toc_lines.append(f'{len(priority)+1}+. <a href="#add">Further updates ({len(additional)})</a>')
    toc_lines.append('<a href="#themes">Developing themes</a>')
    toc_lines.append('<a href="#actions">Practical team actions</a>')
    toc_lines.append('<a href="#appendix">Appendix: index by daily edition</a>')
    story.append(Paragraph("<b>Contents</b><br/>" + "<br/>".join(toc_lines), S["toc"]))

    h = Paragraph('<a name="priority"/>Priority developments', S["h2"])
    h._bookmark = ("priority", "Priority developments", 0)
    story.append(h)
    for i, it in enumerate(priority, 1):
        _item_story(story, i, it, f"p{i}", detailed=True)
    if not priority:
        story.append(Paragraph("No priority developments this week.", S["body"]))

    if additional:
        h = Paragraph('<a name="add"/>Further updates', S["h2"]); h._bookmark = ("add", "Further updates", 0)
        story.append(h)
        for j, it in enumerate(additional, len(priority) + 1):
            _item_story(story, j, it, f"a{j}", detailed=False)

    h = Paragraph('<a name="themes"/>Developing themes', S["h2"]); h._bookmark = ("themes", "Developing themes", 0)
    story.append(h)
    for t in themes:
        story.append(Paragraph(f'&bull; <b>{_esc(t["theme"])}</b> '
                               f'({len(t.get("development_ids", []))} development(s))', S["body"]))
    if not themes:
        story.append(Paragraph("No cross-cutting themes identified this week.", S["body"]))

    h = Paragraph('<a name="actions"/>Practical team actions', S["h2"]); h._bookmark = ("actions", "Practical team actions", 0)
    story.append(h)
    for i, a in enumerate(actions, 1):
        story.append(Paragraph(f"{i}. {_esc(a['action'])}", S["body"]))

    story.append(PageBreak())
    h = Paragraph('<a name="appendix"/>Appendix: index by daily edition', S["h2"])
    h._bookmark = ("appendix", "Appendix: index by daily edition", 0)
    story.append(h)
    rows = [[Paragraph("<b>Date</b>", S["small"]), Paragraph("<b>Status</b>", S["small"]),
             Paragraph("<b>Developments</b>", S["small"])]]
    for r in appendix:
        rows.append([Paragraph(r["date"], S["small"]), Paragraph(r["status"], S["small"]),
                     Paragraph(_esc("; ".join(r["titles"]) or "-"), S["small"])])
    t = Table(rows, colWidths=[26 * mm, 30 * mm, 118 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                           ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t)
    doc.build(story)


def build_daily_pdf(path: Path, *, edition_date, items, generated_at):
    doc = _Doc(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
               topMargin=16 * mm, bottomMargin=16 * mm, title=f"Tire Scout daily {edition_date}")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_footer)])
    story = [Paragraph("Tire Scout - Daily Technology Update", S["title"]),
             Paragraph(f"{edition_date} &middot; generated {generated_at} IST", S["sub"])]
    if items:
        toc = "<br/>".join(f'{i}. <a href="#d{i}">{_esc(it["title"])}</a>'
                           for i, it in enumerate(items, 1))
        story.append(Paragraph(f"<b>In this edition</b><br/>{toc}", S["toc"]))
        h = Paragraph("Developments", S["h2"])
        h._bookmark = ("developments", "Developments", 0)
        story.append(h)
        for i, it in enumerate(items, 1):
            _item_story(story, i, it, f"d{i}", detailed=True)
    else:
        story.append(Paragraph("Research completed - no qualifying new developments today.", S["body"]))
    doc.build(story)


def _esc(text) -> str:
    import html as _html
    return _html.escape(str(text if text is not None else ""), quote=False)
