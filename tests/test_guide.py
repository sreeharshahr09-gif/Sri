"""The reader's guide and the small Markdown converter that embeds it."""

from __future__ import annotations

import os
import re

import pytest

from tread_eval import markdown as md
from tread_eval.report import GUIDE

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- converter ---------------------------------------------------------
def test_headings_and_paragraphs():
    out = md.render("# Title\n\nSome text.\n\n## Section\n\nMore text.")
    assert "<h1>Title</h1>" in out
    assert "<h2>Section</h2>" in out
    assert "<p>Some text.</p>" in out


def test_paragraph_lines_are_joined():
    out = md.render("one line\ncontinues here.\n\nnext para")
    assert "<p>one line continues here.</p>" in out
    assert "<p>next para</p>" in out


def test_inline_formatting():
    out = md.render("A **bold** and *italic* and `code` word.")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out


def test_bold_inside_code_is_left_alone():
    """Code spans are stashed before emphasis runs, so ** survives inside them."""
    out = md.render("`a ** b`")
    assert "<code>a ** b</code>" in out
    assert "<strong>" not in out


def test_lists():
    ul = md.render("- one\n- two\n- three")
    assert ul.count("<li>") == 3 and "<ul>" in ul
    ol = md.render("1. first\n2. second")
    assert ol.count("<li>") == 2 and "<ol>" in ol


def test_list_item_continuation_line():
    out = md.render("- a long item\n  that wraps\n- second")
    assert "<li>a long item that wraps</li>" in out
    assert out.count("<li>") == 2


def test_table():
    out = md.render("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
    assert "<th>A</th>" in out and "<th>B</th>" in out
    assert "<td>1</td>" in out and "<td>4</td>" in out
    assert out.count("<tr>") == 3  # header + two body rows


def test_blockquote_and_rule_and_fence():
    assert "<blockquote>" in md.render("> quoted text")
    assert "<hr>" in md.render("---")
    out = md.render("```\nraw <code> here\n```")
    assert "<pre><code>" in out
    assert "&lt;code&gt;" in out  # fenced content is escaped, not interpreted


def test_html_in_source_is_escaped_not_executed():
    """The guide is injected into the page, so its content must never be markup."""
    out = md.render("A <script>alert(1)</script> line and <b>tags</b>.")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>tags</b>" not in out


def test_link_rendering():
    assert '<a href="https://example.com">text</a>' in md.render("[text](https://example.com)")


def test_empty_input():
    assert md.render("") == ""


# --- the guide itself --------------------------------------------------
def test_guide_file_exists_and_is_substantial():
    assert os.path.exists(GUIDE), "GUIDE.md must sit at the repo root"
    text = open(GUIDE, encoding="utf-8").read()
    assert len(text) > 8000


def test_guide_covers_every_report_tab():
    """A tab with no section in the guide is a tab nobody can interpret."""
    text = open(GUIDE, encoding="utf-8").read().lower()
    for topic in [
        "pattern & patch", "contact & stiffness", "order content", "lean sweep",
        "zones", "grooves", "contact patch", "diagnostics",
    ]:
        assert topic in text, f"guide has no section for the {topic!r} tab"


def test_guide_explains_the_core_vocabulary():
    text = open(GUIDE, encoding="utf-8").read().lower()
    for term in ["contact patch", "land ratio", "pitch", "order", "nsd", "cov", "kx", "ky", "kz"]:
        assert term in text, f"guide never defines {term!r}"


def test_guide_renders_without_leftover_markdown():
    out = md.render(open(GUIDE, encoding="utf-8").read())
    assert len(out) > 10000
    # no stray markdown syntax survived into the HTML
    assert not re.search(r"\*\*[^<]", out)
    assert "](" not in out
    assert re.search(r"^\s*#{1,6}\s", out, re.M) is None


def test_guide_is_embedded_in_the_report():
    from tread_eval.contact_patch import CPParams
    from tread_eval.raster import make_grid, rasterise
    from tread_eval.report import build_payload, render_html
    from tread_eval.stiffness import DEFAULT_PARAMS
    from tread_eval.sweep import sweep
    from tread_eval.synthetic import SyntheticParams, generate_pattern

    pattern = generate_pattern(SyntheticParams(seed=1))
    pack = rasterise(pattern, make_grid(pattern, 1.2))
    results = sweep(pattern, pack, [0.0])
    payload = build_payload(pattern, pack, results, CPParams(), DEFAULT_PARAMS, theta_points=40)
    html = render_html(payload, plotly_mode="cdn")

    assert "<!--__GUIDE__-->" not in html  # the placeholder was substituted
    assert "How to read this report" in html
    assert 'id="tab-guide"' in html
