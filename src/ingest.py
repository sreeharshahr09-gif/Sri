"""
Ingest patent exports (CSV / XLSX / XML / PDF) into one normalized table.

Real-world patent exports never share a schema. Lens.org, Espacenet, PatBase,
Derwent and PatentsView all name their columns differently. Rather than hard-code
one vendor, we map a generous set of likely column names onto our canonical
schema (see config.CANONICAL_FIELDS) and warn about anything we could not map.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .config import CANONICAL_FIELDS

# Map many possible source column names -> our canonical field.
# Lowercased, stripped comparison is used, so list lowercase variants.
COLUMN_ALIASES = {
    "doc_id": [
        "publication number", "publication_number", "pub number", "lens id",
        "document number", "patent number", "id", "publication", "pubno",
        "display_key", "patent_id", "record number", "record no", "record_number",
    ],
    "title": ["title", "invention title", "patent title", "title (en)"],
    "abstract": ["abstract", "abstract (en)", "abstract_text"],
    "claims": ["claims", "claim", "claims (en)", "claims_text"],
    "description": [
        "description", "detailed description", "full text", "fulltext",
        "specification", "description (en)", "body",
    ],
    "assignee": [
        "assignee", "assignees", "applicant", "applicants", "current assignee",
        "owner", "assignee/applicant", "assignee_organization",
    ],
    "year": [
        "year", "publication year", "priority year", "publication date",
        "priority date", "earliest priority date", "date_publ", "filing date",
    ],
    "cpc": ["cpc", "cpc classifications", "cpc codes", "ipcr", "ipc", "classifications"],
    "ai_advantages": ["advantages (ai sum.)", "advantages (ai sum)", "advantages"],
    "ai_method": ["method used (ai sum.)", "method used (ai sum)", "method used", "method"],
    "ai_problem": [
        "problem being solved (ai sum.)", "problem being solved (ai sum)",
        "problem being solved", "problem",
    ],
    "legal_status": ["legal status (dead/alive)", "legal status", "status"],
}

# Tokens that mean "empty" in patent exports and should become blank strings.
_NULL_TOKENS = {"nan", "none", "null", "n/a", "na", ""}


def _build_reverse_alias() -> dict:
    rev = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            rev[a] = canonical
    return rev


_REVERSE_ALIAS = _build_reverse_alias()


def _coerce_year(value) -> int | None:
    """Pull a 4-digit year out of a date string / number."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def _normalize_frame(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    """Map an arbitrary patent-export DataFrame onto the canonical schema."""
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in _REVERSE_ALIAS:
            rename[col] = _REVERSE_ALIAS[key]
    df = df.rename(columns=rename)

    out = pd.DataFrame()
    for field in CANONICAL_FIELDS:
        if field == "source_file":
            out[field] = [Path(source_file).name] * len(df)
        elif field in df.columns:
            # If the export had duplicate-mapped columns, take the first.
            col = df[field]
            if isinstance(col, pd.DataFrame):
                col = col.iloc[:, 0]
            cleaned = col.astype(str).str.strip()
            # Blank out null-like tokens (PatSeer writes literal "None").
            cleaned = cleaned.where(~cleaned.str.lower().isin(_NULL_TOKENS), "")
            out[field] = cleaned
        else:
            out[field] = ""

    out["year"] = df["year"].map(_coerce_year) if "year" in df.columns else None
    return out


# ----------------------------------------------------------------------------
# Per-format readers
# ----------------------------------------------------------------------------
def _read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        # Lens/Espacenet CSVs sometimes have a banner row; try a couple of skips.
        for skip in (0, 1):
            try:
                df = pd.read_csv(path, skiprows=skip, dtype=str, on_bad_lines="skip")
                if df.shape[1] > 1:
                    break
            except Exception:
                df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
    return _normalize_frame(df, str(path))


def _read_xml(path: Path) -> pd.DataFrame:
    """Parse Espacenet/Lens-style XML. Tag names vary, so search broadly."""
    from lxml import etree

    tree = etree.parse(str(path))
    root = tree.getroot()

    def _localname(tag: str) -> str:
        return etree.QName(tag).localname.lower() if isinstance(tag, str) else ""

    def _text_for(node, names: list[str]) -> str:
        parts = []
        for el in node.iter():
            if _localname(el.tag) in names and el.text:
                parts.append(el.text.strip())
        return " ".join(parts)

    # Heuristic: a "record" is any element that contains a title-like child.
    records = []
    for node in root.iter():
        ln = _localname(node.tag)
        if ln in {"document", "patent", "record", "exchange-document", "result"}:
            rec = {
                "title": _text_for(node, ["invention-title", "title"]),
                "abstract": _text_for(node, ["abstract", "p"]),
                "doc_id": _text_for(node, ["doc-number", "publication-number", "id"]),
                "assignee": _text_for(node, ["applicant-name", "assignee", "name"]),
                "year": _text_for(node, ["date", "publication-date", "year"]),
                "cpc": _text_for(node, ["classification-cpc", "cpc", "ipc"]),
                "claims": _text_for(node, ["claim", "claims"]),
                "description": _text_for(node, ["description"]),
            }
            if rec["title"] or rec["abstract"]:
                records.append(rec)
    df = pd.DataFrame(records)
    return _normalize_frame(df, str(path)) if not df.empty else pd.DataFrame()


def _read_pdf(path: Path) -> pd.DataFrame:
    """One PDF == one patent. Extract full text; sectionize best-effort."""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    full = "\n".join(text_parts)

    # Best-effort sectioning by common headers.
    def _section(name_pattern: str) -> str:
        m = re.search(name_pattern, full, re.IGNORECASE)
        return full[m.start():] if m else ""

    title = full.split("\n", 1)[0][:300] if full else path.stem
    rec = {
        "doc_id": path.stem,
        "title": title,
        "abstract": _section(r"abstract")[:3000],
        "claims": _section(r"what is claimed|claims?\b")[:20000],
        "description": full,
        "assignee": "",
        "year": "",
        "cpc": "",
    }
    return _normalize_frame(pd.DataFrame([rec]), str(path))


def load_raw(raw_dir: str | Path) -> pd.DataFrame:
    """Read every supported file in raw_dir and concatenate to one table."""
    raw_dir = Path(raw_dir)
    frames = []
    readers = {
        ".csv": _read_tabular, ".tsv": _read_tabular,
        ".xlsx": _read_tabular, ".xls": _read_tabular,
        ".xml": _read_xml, ".pdf": _read_pdf,
    }
    files = sorted(p for p in raw_dir.glob("**/*") if p.suffix.lower() in readers)
    if not files:
        raise FileNotFoundError(
            f"No patent exports found in {raw_dir!s}. "
            "Drop CSV/XLSX/XML/PDF files there and re-run."
        )

    for path in files:
        try:
            df = readers[path.suffix.lower()](path)
            if df is not None and not df.empty:
                frames.append(df)
                print(f"  ingested {len(df):>5} records  <-  {path.name}")
        except Exception as exc:  # keep going; report bad files
            print(f"  WARNING: failed to read {path.name}: {exc}")

    if not frames:
        raise RuntimeError("All files failed to parse — check formats/encodings.")

    combined = pd.concat(frames, ignore_index=True)
    # De-duplicate on document id where present (patent families share numbers).
    before = len(combined)
    mask = combined["doc_id"].astype(str).str.strip() != ""
    deduped = pd.concat([
        combined[mask].drop_duplicates(subset="doc_id"),
        combined[~mask],
    ], ignore_index=True)
    print(f"  total {before} -> {len(deduped)} after de-duplication")
    return deduped
