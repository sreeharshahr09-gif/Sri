"""Document ingestion, phrase matching, section detection and context budgeting.

Everything here is deterministic and offline.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from pypdf import PdfReader


# --- matching ---------------------------------------------------------------

def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def exact_quote_in(quote: str, source: str) -> bool:
    return len(normalized(quote)) >= 5 and normalized(quote) in normalized(source)


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
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        raise ValueError("Upload a DOCX, text-based PDF, TXT or MD document.")
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text.strip()) < 50:
        raise ValueError("Too little extractable text. Scanned/image-only PDFs need OCR first; "
                         "or paste the text instead.")
    return text.strip()


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
    ("verbatim", ("publications", "publication", "selected publications", "journal articles",
                  "papers", "conference papers", "book chapters", "patents", "patent",
                  "presentations", "talks", "invited talks", "posters", "conferences",
                  "references", "referees", "grants", "funding", "awards", "honors", "honours",
                  "awards and honors", "certifications", "certificates", "licenses",
                  "licenses and certifications", "languages", "interests", "hobbies",
                  "memberships", "professional memberships", "affiliations", "thesis",
                  "dissertation", "citations")),
    ("summary", ("summary", "professional summary", "career summary", "executive summary",
                 "profile", "professional profile", "personal profile", "objective",
                 "career objective", "about me", "about", "personal statement", "overview")),
    ("skills", ("skills", "technical skills", "key skills", "core skills", "skill set",
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
PRESERVE_KINDS = {"education", "verbatim"}
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


def split_header(header: str) -> tuple[str, list[str], str]:
    """(name, contact lines, stray paragraph text) from the block above the first heading."""
    lines = [l.strip().lstrip("#").strip() for l in header.splitlines() if l.strip()]
    if not lines:
        return "", [], ""
    name = lines[0].strip("*_ ")
    contacts, prose = [], []
    for line in lines[1:]:
        (prose if len(line) > 120 else contacts).append(line)
    return name, contacts, "\n".join(prose)


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
