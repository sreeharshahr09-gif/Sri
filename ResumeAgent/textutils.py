"""Document ingestion, phrase matching, section detection and context budgeting.

Everything here is deterministic and offline.
"""
from __future__ import annotations

import difflib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from pypdf import PdfReader


# --- matching ---------------------------------------------------------------

def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


_DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})


def _compact(text: str) -> str:
    """Case-, whitespace-, dash- and quote-mark-insensitive form used to compare quotes.

    Removing whitespace makes PDF line wraps irrelevant ('cross-\ninstitutional')."""
    t = text.translate(_DASHES).replace("\xa0", " ").casefold()
    t = re.sub(r"page \d+ of \d+", "", t)
    return re.sub(r"[\s\"'`\u201c\u201d\u2018\u2019\u2022\u25cf\u25aa\u25e6\u2023\u00b7*]+", "", t)


def exact_quote_in(quote: str, source: str) -> bool:
    """True when ``quote`` occurs in ``source`` apart from layout differences.

    A quote shortened with '...' / '…' passes when each substantial piece occurs."""
    parts = [_compact(p.strip(" .,;:")) for p in re.split(r"\[?(?:\.\.\.|\u2026)\]?", quote)]
    parts = [p for p in parts if len(p) >= 4]
    if not parts or max(len(p) for p in parts) < 5:
        return False
    src = _compact(source)
    return all(p in src for p in parts)


def locate_quote(quote: str, source: str, threshold: float = 0.88) -> str:
    """The source text a model's quote refers to, or '' if it cannot be found.

    Exact (layout-insensitive) matches return the quote; near-verbatim quotes (a typo fixed,
    a word dropped) are snapped to the real source lines so the evidence shown is genuine."""
    quote = quote.strip()
    if exact_quote_in(quote, source):
        return quote
    q = _compact(quote)
    if len(q) < 15:
        return ""
    lines = [l for l in source.splitlines() if l.strip()]
    span = max(4, int(len(quote.split()) * 1.3) + 1)

    def candidates():
        for i in range(len(lines)):
            window = ""
            for j in range(i, min(i + 5, len(lines))):
                window = (window + "\n" + lines[j]) if window else lines[j]
                yield window
            words = lines[i].split()
            if len(words) > span:  # long paragraph line: slide a window of words over it
                for k in range(0, len(words) - span + 1, 2):
                    yield " ".join(words[k:k + span])

    best, best_precision = "", 0.0
    for window in candidates():
        w = _compact(window)
        if len(w) < 0.6 * len(q) or len(w) > 3 * len(q) + 40:
            continue
        matcher = difflib.SequenceMatcher(None, q, w, autojunk=False)
        if matcher.real_quick_ratio() * (len(q) + len(w)) / (2 * len(q)) < threshold:
            continue
        matched = sum(b.size for b in matcher.get_matching_blocks())
        # Must cover most of the quote; among such windows prefer the tightest one.
        if matched / len(q) >= threshold and matched / len(w) > best_precision:
            best, best_precision = window, matched / len(w)
    return best.strip()


def _phrase_pattern(phrase: str) -> str:
    return r"(?<!\w)" + re.escape(phrase.strip()).replace(r"\ ", r"\s+") + r"(?!\w)"


def contains_phrase(text: str, phrase: str) -> bool:
    if not phrase.strip():
        return False
    return re.search(_phrase_pattern(phrase), text, re.IGNORECASE) is not None


def count_phrase(text: str, phrase: str) -> int:
    if not phrase.strip():
        return 0
    return len(re.findall(_phrase_pattern(phrase), text, re.IGNORECASE))


def numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w])\d+(?:[.,]\d+)*(?:%|\+)?(?!\w)", text))


def novel_numbers(source: str, draft: str) -> list[str]:
    """Numbers in ``draft`` that never occur in ``source`` (1,200 == 1200; 40% == 40)."""
    def root(n: str) -> str:
        return n.rstrip("%+").replace(",", "")
    known = {root(x) for x in numeric_tokens(source)}
    return sorted({n for n in numeric_tokens(draft) if root(n) not in known})


YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s)>\]]+")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.&/-]*[A-Za-z0-9+#]|[A-Za-z0-9]")


def _norm_token(token: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", token.casefold())


def word_tokens(text: str) -> set[str]:
    """Normalised tokens of a text, plus the parts of slash/hyphen compounds."""
    out: set[str] = set()
    for tok in _TOKEN_RE.findall(text):
        out.add(_norm_token(tok))
        for part in re.split(r"[/-]", tok):
            if part:
                out.add(_norm_token(part))
    out.discard("")
    return out


def technical_terms(text: str) -> list[str]:
    """Acronyms, CamelCase names, versioned names and C++/C#-style tokens.

    These are the tokens most likely to be an invented tool, credential or system.
    """
    found = []
    for tok in _TOKEN_RE.findall(text):
        tok = tok.strip(".")
        if not tok or tok.isdigit():
            continue
        upper = sum(ch.isupper() for ch in tok)
        has_digit = any(ch.isdigit() for ch in tok)
        has_alpha = any(ch.isalpha() for ch in tok)
        if upper >= 2 or "+" in tok or "#" in tok or (has_digit and has_alpha):
            if YEAR_RE.fullmatch(tok):
                continue
            found.append(tok)
    return list(dict.fromkeys(found))


_ITEM_START = set("\n.!?:;-–—•*(\"'|#>/[")


def proper_nouns(text: str) -> list[str]:
    """Capitalised words in mid-sentence: tool, employer, place or title names.

    Sentence/bullet starts and bold category labels ('**Programming:**') are ignored.
    """
    text = re.sub(r"\*\*[^*\n]{1,40}:\*\*", " ", text).replace("**", " ")
    found = []
    for m in re.finditer(r"(?<![\w'’])[A-Z][a-z]{2,}(?![\w])", text):
        j = m.start() - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j < 0 or text[j] in _ITEM_START:
            continue
        found.append(m.group())
    return list(dict.fromkeys(found))


# --- ingestion --------------------------------------------------------------

def read_file(name: str, contents: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in (".txt", ".md"):
        text = contents.decode("utf-8-sig", errors="replace")
    elif suffix == ".docx":
        doc = Document(io.BytesIO(contents))
        parts = []
        for item in doc.iter_inner_content():  # body order: paragraphs and tables
            if hasattr(item, "rows"):
                for row in item.rows:
                    cells = list(dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip()))
                    parts.append(" | ".join(cells))
            else:
                parts.append(item.text)
        text = "\n".join(parts)
    elif suffix == ".pdf":
        reader = PdfReader(io.BytesIO(contents))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ValueError("The PDF is encrypted; upload a readable PDF or DOCX.") from exc
        text = _pdf_text(reader)
    else:
        raise ValueError("Upload a DOCX, text-based PDF, TXT or MD document.")
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text.strip()) < 50:
        raise ValueError("Too little extractable text. Scanned/image-only PDFs need OCR first; "
                         "or paste the text instead.")
    return text.strip()


def _page_fragments(page) -> list[tuple[float, float, str]]:
    frags: list[tuple[float, float, str]] = []

    def visit(text, cm, tm, _font, _size):
        if text and text.strip():
            frags.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5], text.replace("\xa0", " ")))
    page.extract_text(visitor_text=visit)
    return frags


def _lines_from(frags: list[tuple[float, float, str]]) -> list[str]:
    """Rebuild one column's lines; blank lines mark paragraph gaps; width-wrapped lines re-joined."""
    rows: list[list[tuple[float, float, str]]] = []
    for frag in sorted(frags, key=lambda f: (-f[1], f[0])):
        if rows and abs(rows[-1][0][1] - frag[1]) <= 2:
            rows[-1].append(frag)
        else:
            rows.append([frag])
    lines = [(r[0][1], re.sub(r"\s+", " ", " ".join(f[2].strip() for f in sorted(r))).strip()) for r in rows]
    lines = [(y, t) for y, t in lines if t and not re.fullmatch(r"Page \d+ of \d+", t)]
    if len(lines) < 3:
        return [t for _, t in lines]
    gaps = sorted(a[0] - b[0] for a, b in zip(lines, lines[1:]) if a[0] > b[0])
    typical = gaps[len(gaps) // 5] if gaps else 0  # tight in-paragraph line spacing
    widest = max(len(t) for _, t in lines)
    out: list[str] = [lines[0][1]]
    for (y0, prev), (y1, text) in zip(lines, lines[1:]):
        if typical and y0 - y1 > 1.3 * typical:
            out += ["", text]  # paragraph / item gap
        elif (len(prev) >= 0.75 * widest and not _BULLET_RE.match(text)
              and heading_kind(prev) is None and heading_kind(text) is None
              and not prev.endswith((".", ":", ";"))):
            glue = "" if prev.endswith(("-", "/")) and not prev.endswith(" -") else " "
            out[-1] = out[-1] + glue + text  # wrapped by the column width
        else:
            out.append(text)
    return out


def _column_split(frags: list[tuple[float, float, str]], width: float) -> float | None:
    """x where a second text column starts (LinkedIn-style sidebar layouts), else None."""
    counts: dict[int, int] = {}
    for x, _, _ in frags:
        if 0.25 * width <= x <= 0.6 * width:
            counts[round(x)] = counts.get(round(x), 0) + 1
    starts = sorted(x for x, n in counts.items() if n >= 3)
    if not starts:
        return None
    split = starts[0] - 2
    left = [f for f in frags if f[0] < split]
    right = [f for f in frags if f[0] >= split]
    if len(left) < 3 or len(right) < 3:
        return None
    left_rows = {round(f[1]) for f in left}
    shared = sum(1 for f in right if round(f[1]) in left_rows)
    return split if shared / len(right) < 0.35 else None


def _pdf_text(reader: PdfReader) -> str:
    """Two-column pages are read column by column: wider (main) column first, sidebar last."""
    try:
        pages = []
        for page in reader.pages:
            frags = _page_fragments(page)
            if frags and sum(len(f[2].strip()) for f in frags) / len(frags) < 3:
                raise ValueError("glyph-level PDF; use the default extractor")
            split = _column_split(frags, float(page.mediabox.width))
            if split is None:
                pages.append((_lines_from(frags), []))
            else:
                pages.append((_lines_from([f for f in frags if f[0] >= split]),
                              _lines_from([f for f in frags if f[0] < split])))
        if any(side for _, side in pages):
            main = [l for lines, _ in pages for l in lines]
            side = [l for _, lines in pages for l in lines]
            if sum(map(len, side)) > sum(map(len, main)):
                main, side = side, main
            return _clean_pdf_lines(main + [""] + side)
    except Exception:
        pass
    return _clean_pdf_lines("\n".join((p.extract_text() or "") for p in reader.pages).splitlines())


_CONNECTORS = {"and", "or", "of", "the", "to", "with", "in", "for", "using", "a", "an", "at", "on",
               "by", "from", "as", "via", "including", "such"}


def _clean_pdf_lines(lines: list[str]) -> str:
    """Drop page footers and re-join lines that the PDF wrapped mid-sentence."""
    lines = [l.replace("\xa0", " ").rstrip() for l in lines
             if not re.fullmatch(r"\s*Page \d+ of \d+\s*", l)]
    out: list[str] = []
    for line in lines:
        prev = out[-1] if out else ""
        stripped = line.strip()
        joinable = (prev.strip() and stripped and not _BULLET_RE.match(line)
                    and not re.match(r"^\d+[.)]\s", stripped)
                    and heading_kind(prev) is None and heading_kind(line) is None)
        if joinable and not re.match(r"^(www\.|https?://)|^\S+@\S+\.\w+", stripped):
            last_word = re.findall(r"[A-Za-z]+$", prev)
            if (stripped[0].islower() or prev.endswith(("&", "|", "/", "-", "("))
                    or stripped.startswith(("&", "|", ")")) or prev.count("(") > prev.count(")")
                    or (last_word and last_word[0].lower() in _CONNECTORS)):
                glue = "" if prev.endswith(("-", "/")) and not prev.endswith(" -") else " "
                out[-1] = prev + glue + stripped
                continue
        out.append(line)
    return "\n".join(out)


# --- context budgeting --------------------------------------------------------

@dataclass
class Budget:
    """Rough token budgeting for one llama-server slot (chars/3 is conservative)."""
    n_ctx: int = 8192
    chars_per_token: float = 3.0
    notes: list[str] = field(default_factory=list)

    def tokens(self, text: str) -> int:
        return int(len(text) / self.chars_per_token) + 1

    def chars_left(self, output_tokens: int, *fixed_texts: str, cap: int = 24000) -> int:
        used = sum(self.tokens(t) for t in fixed_texts) + output_tokens + 400
        return max(1200, min(cap, int((self.n_ctx - used) * self.chars_per_token)))

    def fit(self, text: str, max_chars: int, label: str) -> str:
        """Trim *supporting* context on a line boundary and record that it happened."""
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        cut = cut[:cut.rfind("\n")] if "\n" in cut[max_chars // 2:] else cut
        note = f"Context trimmed for {label}: {len(text):,} → {len(cut):,} characters."
        if note not in self.notes:
            self.notes.append(note)
        return cut + "\n[... trimmed to fit the model context ...]"


def budget_for(llm) -> Budget:
    size = getattr(llm, "context_size", None)
    try:
        n_ctx = int(size()) if callable(size) else 8192
    except Exception:
        n_ctx = 8192
    return Budget(n_ctx=max(2048, min(n_ctx, 65536)))


def chunk_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Split on paragraph, then line boundaries; never drop text."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        line_buf = ""
        for line in para.splitlines():
            while len(line) > max_chars:
                if line_buf:
                    pieces.append(line_buf)
                    line_buf = ""
                pieces.append(line[:max_chars])
                line = line[max_chars:]
            if len(line_buf) + len(line) + 1 > max_chars and line_buf:
                pieces.append(line_buf)
                line_buf = ""
            line_buf = (line_buf + "\n" + line) if line_buf else line
        if line_buf:
            pieces.append(line_buf)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n\n" + piece) if tail else piece
        else:
            current = (current + "\n\n" + piece) if current else piece
    if current:
        chunks.append(current)
    return chunks


# --- résumé sections ----------------------------------------------------------

# Order matters: the first matching vocabulary entry decides the kind.
SECTION_VOCAB: list[tuple[str, tuple[str, ...]]] = [
    ("contact", ("contact", "contact information", "contact details", "contact info",
                 "personal details", "personal information")),
    ("verbatim", ("publications", "publication", "selected publications", "journal articles",
                  "papers", "conference papers", "book chapters", "patents", "patent",
                  "presentations", "talks", "invited talks", "posters", "conferences",
                  "references", "referees", "grants", "funding", "awards", "honors", "honours",
                  "awards and honors", "honors awards", "honours awards", "awards and honours",
                  "honors and awards", "honours and awards", "certifications", "certificates", "licenses",
                  "licenses and certifications", "languages", "interests", "hobbies",
                  "memberships", "professional memberships", "affiliations", "thesis",
                  "dissertation", "citations")),
    ("summary", ("summary", "professional summary", "career summary", "executive summary",
                 "profile", "professional profile", "personal profile", "objective",
                 "career objective", "about me", "about", "personal statement", "overview")),
    ("skills", ("skills", "top skills", "technical skills", "key skills", "core skills", "skill set",
                "core competencies", "competencies", "areas of expertise", "expertise",
                "technical expertise", "tools", "technologies", "tools and technologies",
                "software", "technical proficiencies", "proficiencies", "computer skills",
                "skills and tools")),
    ("education", ("education", "academic background", "academic qualifications",
                   "education and training", "degrees", "academic history")),
    ("experience", ("experience", "professional experience", "work experience",
                    "employment", "employment history", "work history", "career history",
                    "relevant experience", "research experience", "industry experience",
                    "professional background", "positions", "academic appointments",
                    "appointments", "positions held", "career")),
    ("other", ("projects", "selected projects", "key projects", "research projects",
               "research", "research interests", "teaching", "teaching experience",
               "leadership", "volunteer", "volunteering", "volunteer experience",
               "activities", "extracurricular activities", "training", "courses",
               "professional development", "achievements", "key achievements",
               "accomplishments", "service", "supervision", "mentoring", "internships",
               "internship")),
]
_VOCAB_LOOKUP = {phrase: kind for kind, phrases in SECTION_VOCAB for phrase in phrases}
# Factual sections that are reproduced exactly; dates, titles and authorship are not rewritten.
PRESERVE_KINDS = {"education", "verbatim", "contact"}
_BULLET_RE = re.compile(r"^\s*(?:[-*•●▪◦‣–—·]|•)\s+")


@dataclass
class Section:
    title: str
    kind: str          # header | summary | skills | experience | education | other | verbatim | document
    body: str

    @property
    def rewritable(self) -> bool:
        return self.kind not in PRESERVE_KINDS and self.kind != "header"


def heading_kind(line: str) -> str | None:
    raw = line.strip()
    if not raw or len(raw) > 60:
        return None
    is_md = raw.startswith("#")
    text = raw.lstrip("#").strip().strip("*_").strip()
    ends_colon = text.endswith(":")
    canon = re.sub(r"[^a-z& ]+", " ", text.lower().replace("&", " and ")).strip()
    canon = re.sub(r"\s+", " ", canon)
    if not canon or len(canon.split()) > 6:
        return None
    if canon in _VOCAB_LOOKUP:
        return _VOCAB_LOOKUP[canon]
    if ":" in text.rstrip(":"):
        return None  # "SKILLS: Python, MATLAB" is content, not a heading
    letters = [c for c in text if c.isalpha()]
    shouty = bool(letters) and all(c.isupper() for c in letters)
    if is_md or shouty or ends_colon:
        words = canon.split()
        for size in (3, 2, 1):
            for i in range(len(words) - size + 1):
                kind = _VOCAB_LOOKUP.get(" ".join(words[i:i + size]))
                if kind:
                    return kind
    return None


def split_sections(text: str) -> list[Section]:
    """Header block + headed sections. Falls back to one 'document' section."""
    lines = text.splitlines()
    sections: list[Section] = []
    header: list[str] = []
    current: Section | None = None
    for line in lines:
        kind = heading_kind(line)
        if kind:
            if current:
                sections.append(current)
            title = line.strip().lstrip("#").strip().strip("*_").rstrip(":").strip()
            current = Section(title=title, kind=kind, body="")
        elif current is None:
            header.append(line)
        else:
            current.body += line + "\n"
    if current:
        sections.append(current)
    for s in sections:
        s.body = s.body.strip("\n")
    sections = [s for s in sections if s.body.strip()]
    if len(sections) < 2:
        return [Section("", "document", text.strip())]
    head = "\n".join(header).strip()
    return [Section("", "header", head)] + sections


def format_verbatim(body: str) -> str:
    """Original lines, Markdown-safe, with bullet glyphs normalised to '- '."""
    out = []
    for line in body.splitlines():
        if not line.strip():
            out.append("")
            continue
        if _BULLET_RE.match(line):
            out.append("- " + _BULLET_RE.sub("", line).strip())
        else:
            stripped = line.strip()
            out.append("\\" + stripped if stripped.startswith("#") else stripped)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_NOT_A_NAME = {"contact", "resume", "résumé", "curriculum vitae", "cv", "profile", "personal details"}
_DEGREE_SUFFIX = re.compile(r"^(ph\.?\s?d\.?|m\.?b\.?a\.?|p\.?e\.?|m\.?sc\.?|m\.?d\.?|cfa|pmp)$", re.I)


def split_header(header: str) -> tuple[str, list[str], str]:
    """(name, contact/headline lines, stray paragraph text) from the block above the first heading."""
    lines = [l.strip().lstrip("#").strip() for l in header.splitlines() if l.strip()]
    while lines and lines[0].strip("*_: ").casefold() in _NOT_A_NAME:
        lines.pop(0)
    if not lines:
        return "", [], ""
    name = lines.pop(0).strip("*_ ")
    while lines and _DEGREE_SUFFIX.match(lines[0]):
        name = name.rstrip(",") + ", " + lines.pop(0)
    contacts, prose = [], []
    for line in lines:
        (prose if len(line) > 300 else contacts).append(line)
    return name, contacts, "\n".join(prose)


def contact_lines(body: str) -> list[str]:
    """Lines of a 'Contact' sidebar, with wrapped URLs re-joined."""
    out: list[str] = []
    for line in (l.strip() for l in body.splitlines() if l.strip()):
        if out and out[-1].endswith(("-", "/")) and not out[-1].endswith(" -"):
            out[-1] += line
        else:
            out.append(line)
    return out


def role_lines(text: str) -> list[str]:
    """Non-bullet lines carrying a year: usually 'Title, Employer, 2019–2023' or a degree."""
    return [l.strip() for l in text.splitlines()
            if YEAR_RE.search(l) and not _BULLET_RE.match(l) and 8 <= len(l.strip()) <= 160]


def significant_words(line: str) -> list[str]:
    stop = {"and", "the", "of", "for", "in", "at", "to", "a", "an", "on", "with", "present",
            "current", "now", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
            "sept", "oct", "nov", "dec", "january", "february", "march", "april", "june",
            "july", "august", "september", "october", "november", "december"}
    words = re.findall(r"[A-Za-z][A-Za-z&+#.'-]*", line)
    return [w.strip(".'") for w in words if w.lower().strip(".'") not in stop and len(w) > 1]
