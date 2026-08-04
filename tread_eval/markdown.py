"""A very small Markdown-to-HTML converter, just enough for GUIDE.md.

The reader's guide lives in ``GUIDE.md`` at the repo root so it can be read on
GitHub, and it is also embedded in the report as a tab.  Rather than keep two
copies in sync -- which they never stay -- the report converts the Markdown at
build time.  That makes ``GUIDE.md`` the single source.

Supported: ATX headings, paragraphs, unordered and ordered lists, pipe tables,
fenced and inline code, bold, italic, links, horizontal rules, and blockquotes.
Deliberately not a general Markdown implementation -- if a construct is not in
that list, it is not in the guide either.
"""

from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """Escape, then apply inline formatting.

    Order matters: escaping first means source text can never inject markup,
    and code spans are substituted before emphasis so ``**`` inside backticks
    survives.
    """
    out = html.escape(text, quote=False)
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = _INLINE_CODE.sub(stash, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = _LINK.sub(r'<a href="\2">\1</a>', out)
    for i, code in enumerate(codes):
        out = out.replace(f"\x00{i}\x00", f"<code>{code}</code>")
    return out


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(markdown: str) -> str:
    """Convert Markdown to an HTML fragment (no <html> wrapper)."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code
        if stripped.startswith("```"):
            i += 1
            body: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(body), quote=False) + "</code></pre>")
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # table: a header row, a separator row of dashes, then body rows
        if _is_table_row(stripped) and i + 1 < n and re.fullmatch(r"\|[\s:\-|]+\|", lines[i + 1].strip()):
            header = _split_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and _is_table_row(lines[i].strip()):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
            continue

        # blockquote
        if stripped.startswith(">"):
            body = []
            while i < n and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(body))}</blockquote>")
            continue

        # lists
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        number = re.match(r"^\d+\.\s+(.*)$", stripped)
        if bullet or number:
            tag = "ul" if bullet else "ol"
            pattern = r"^[-*+]\s+(.*)$" if bullet else r"^\d+\.\s+(.*)$"
            items: list[str] = []
            while i < n:
                s = lines[i].strip()
                m2 = re.match(pattern, s)
                if m2:
                    items.append(m2.group(1))
                    i += 1
                elif s and not re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|\||>|```)", s) and items:
                    items[-1] += " " + s  # continuation line of the previous item
                    i += 1
                else:
                    break
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            continue

        # paragraph
        body = []
        while i < n:
            s = lines[i].strip()
            if not s or re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|\||>|```|-{3,})", s):
                break
            body.append(s)
            i += 1
        out.append(f"<p>{_inline(' '.join(body))}</p>")

    return "\n".join(out)
