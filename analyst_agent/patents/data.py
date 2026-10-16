"""Patent export loading and cleaning.

Works with exports from any patent database (Derwent, PatSnap, Orbit, Lens, Espacenet,
Google Patents, ...): columns are recognised by name, the user can correct the mapping, and
every filtering step is recorded so the report can state exactly how the corpus was built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Canonical fields and the column names databases use for them (compared after normalising:
# lower case, punctuation removed). Order matters: earlier synonyms are preferred.
ROLE_SYNONYMS: dict[str, list[str]] = {
    "id": ["record number", "publication number", "display key", "patent number", "publication no", "pub number",
           "document number", "doc number", "lens id", "patent id", "publication id", "pn", "number"],
    "title": ["title", "title english", "invention title", "dwpi title", "title en", "original title"],
    "abstract": ["abstract", "abstract english", "dwpi abstract", "abstract en", "original abstract"],
    "claims": ["claims", "first claim", "independent claims", "claim 1", "claims english", "main claim"],
    "assignee": ["assignee", "applicant", "applicants", "current assignee", "original assignee",
                 "assignee applicant", "applicant name", "assignee name", "patent owner", "owner",
                 "standardized assignee", "assignees"],
    "priority_date": ["earliest priority date", "priority date", "earliest priority", "priority"],
    "filing_date": ["application date", "filing date", "application filing date", "app date"],
    "pub_date": ["publication date", "publication issue date", "issue date", "pub date",
                 "date published", "publication"],
    "cpc": ["cpc", "cpc classification", "cpc classifications", "cpc codes", "cooperative patent classification",
            "cpc current"],
    "ipc": ["ipc", "ipc classification", "ipc classifications", "ipc codes", "international patent classification",
            "ipcr"],
    "family": ["family id", "docdb family id", "inpadoc family id", "simple family id", "patent family id",
               "family number", "simple family", "inpadoc family", "family"],
    "country": ["country", "jurisdiction", "publication country", "authority", "country code"],
    "problem": ["problem being solved ai sum", "problem being solved", "technical problem", "problem"],
    "method": ["method used ai sum", "method used", "technical solution", "solution", "dwpi novelty", "novelty"],
    "advantage": ["advantages ai sum", "advantages", "advantage", "dwpi advantage", "benefit", "effect"],
    "inventors": ["inventor", "inventors", "inventor name"],
    "cited_by": ["cited by count", "forward citations", "times cited", "cited by", "citing patents count",
                 "count of citing patents"],
    "legal_status": ["legal status", "status", "patent status"],
}
REQUIRED_ANY = ("title", "abstract")
ROLE_LABELS = {
    "id": "Patent / record number", "title": "Title", "abstract": "Abstract", "claims": "Claims",
    "assignee": "Assignee / applicant", "priority_date": "Priority date", "filing_date": "Filing date",
    "pub_date": "Publication date", "cpc": "CPC codes", "ipc": "IPC codes", "family": "Family ID",
    "country": "Country / jurisdiction", "problem": "Problem (summary)", "method": "Method / solution (summary)",
    "advantage": "Advantage (summary)", "inventors": "Inventors", "cited_by": "Forward citations",
    "legal_status": "Legal status",
}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


_DATE_ROLES = ("priority_date", "filing_date", "pub_date")


def _matches(role: str, syn: str, name: str, exact: bool) -> bool:
    if exact:
        return name == syn
    if role in _DATE_ROLES and re.search(r"\bnumbers?\b|\bcountry\b", name):
        return False  # "Priority numbers" is not a date
    if len(syn) <= 3:  # short codes such as "cpc" only as the first word ("CPC Classifications")
        return name.startswith(syn + " ")
    return re.search(rf"\b{re.escape(syn)}\b", name) is not None


def detect_columns(columns: list[str]) -> dict[str, str | None]:
    """Best guess of which export column holds each canonical field (None when absent)."""
    normalised = {col: _norm(col) for col in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    # Pass 1: exact matches; pass 2: the column name contains the synonym as whole words.
    for exact in (True, False):
        for role, synonyms in ROLE_SYNONYMS.items():
            if mapping.get(role):
                continue
            for syn in synonyms:
                hit = next((c for c, n in normalised.items() if c not in used and _matches(role, syn, n, exact)), None)
                if hit:
                    mapping[role] = hit
                    used.add(hit)
                    break
    return {role: mapping.get(role) for role in ROLE_SYNONYMS}


# --------------------------------------------------------------------------- assignees

# Known name variants of tyre makers, OEMs and material suppliers -> one canonical name.
ASSIGNEE_ALIASES: list[tuple[str, str]] = [
    (r"michelin", "Michelin"),
    (r"bridgestone|firestone", "Bridgestone"),
    (r"goodyear", "Goodyear"),
    (r"continental (reifen|ag|tire|tyre|automotive|teves)|^continental$|conti tech|contitech", "Continental"),
    (r"pirelli", "Pirelli"),
    (r"sumitomo rubber|^srl dunlop|sumitomo electric industries", "Sumitomo Rubber"),
    (r"yokohama (rubber|tire|tyre)|^yokohama$", "Yokohama"),
    (r"toyo (tire|tyre|rubber)|^toyo$", "Toyo Tire"),
    (r"hankook", "Hankook"),
    (r"kumho", "Kumho"),
    (r"nexen", "Nexen"),
    (r"cheng shin|maxxis", "Cheng Shin (Maxxis)"),
    (r"\bgiti\b", "Giti"),
    (r"linglong", "Linglong"),
    (r"sailun", "Sailun"),
    (r"zhongce|zc rubber", "Zhongce (ZC Rubber)"),
    (r"triangle (tyre|tire)", "Triangle"),
    (r"double ?coin|shanghai huayi", "Double Coin"),
    (r"doublestar", "Doublestar"),
    (r"aeolus", "Aeolus"),
    (r"apollo tyres?", "Apollo Tyres"),
    (r"\bmrf\b|madras rubber", "MRF"),
    (r"\bceat\b", "CEAT"),
    (r"\bjk tyre", "JK Tyre"),
    (r"nokian", "Nokian"),
    (r"cooper tire", "Cooper Tire"),
    (r"balkrishna|\bbkt\b", "BKT"),
    (r"prometeon", "Prometeon"),
    (r"trelleborg", "Trelleborg"),
    (r"kenda", "Kenda"),
    (r"titan (tire|international)", "Titan"),
    (r"toyota", "Toyota"),
    (r"honda", "Honda"),
    (r"ford global|ford motor", "Ford"),
    (r"general motors|gm global", "General Motors"),
    (r"hyundai", "Hyundai"),
    (r"\bkia\b", "Kia"),
    (r"nissan", "Nissan"),
    (r"volkswagen|\bvw\b", "Volkswagen"),
    (r"daimler|mercedes", "Mercedes-Benz / Daimler"),
    (r"\bbmw\b|bayerische motoren", "BMW"),
    (r"tesla", "Tesla"),
    (r"\bbyd\b", "BYD"),
    (r"evonik", "Evonik"),
    (r"solvay|rhodia", "Solvay"),
    (r"cabot", "Cabot"),
    (r"orion engineered", "Orion"),
    (r"lanxess", "Lanxess"),
    (r"arlanxeo", "Arlanxeo"),
    (r"\bzeon\b", "Zeon"),
    (r"\bjsr\b", "JSR"),
    (r"asahi kasei", "Asahi Kasei"),
    (r"kuraray", "Kuraray"),
    (r"trinseo", "Trinseo"),
    (r"synthos", "Synthos"),
    (r"sinopec|china petroleum", "Sinopec"),
    (r"exxon", "ExxonMobil"),
    (r"\bbasf\b", "BASF"),
    (r"shin etsu", "Shin-Etsu"),
    (r"momentive", "Momentive"),
    (r"bosch", "Bosch"),
]
_LEGAL_WORDS = (
    "co", "corp", "corporation", "company", "inc", "incorporated", "ltd", "limited", "llc", "plc",
    "gmbh", "ag", "kg", "sa", "spa", "srl", "bv", "nv", "kk", "oy", "oyj", "ab", "as", "sas", "se",
    "pte", "pvt", "private", "holding", "holdings", "group", "the", "kabushiki", "kaisha", "mfg",
)
_LEGAL = re.compile(r"\b(" + "|".join(_LEGAL_WORDS) + r")\b")


def split_assignees(value) -> list[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    parts = re.split(r"\s*(?:;;|;|\||\n)\s*", str(value))
    return [p.strip(" ,.") for p in parts if p and p.strip(" ,.")]


def normalize_assignee(name: str) -> str:
    """Canonical company name: known aliases first, otherwise legal suffixes removed."""
    if not name:
        return "Unknown"
    simple = _norm(name.replace("&", " and "))
    for pattern, canonical in ASSIGNEE_ALIASES:
        if re.search(pattern, simple):
            return canonical
    cleaned = re.sub(r"\([^)]*\)", " ", name)
    tokens = [t for t in re.findall(r"[^\s,.;]+", cleaned) if t.lower().strip("&") not in _LEGAL_WORDS]
    while tokens and tokens[-1].lower() in ("&", "and", "-"):
        tokens.pop()
    if not tokens:
        return name.strip()
    core = " ".join(tokens)
    if core.isupper() or core.islower():  # shouting or flat case: make it readable
        small = {"of", "and", "for", "de", "du", "la", "le", "et", "und", "der"}
        core = " ".join(t.lower() if t.lower() in small and i else t.capitalize() for i, t in enumerate(tokens))
    return core


# --------------------------------------------------------------------------- dates, countries, text

_COUNTRY_PREFIX = re.compile(r"^\s*([A-Z]{2})[\s-]?\d")


def parse_year(series: pd.Series) -> pd.Series:
    """Year from dates in any common format, including yyyymmdd integers."""
    s = series.copy()
    as_str = s.astype(str).str.strip()
    compact = as_str.str.fullmatch(r"\d{8}(\.0)?")
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if compact.any():
        out[compact] = pd.to_numeric(as_str[compact].str[:4], errors="coerce")
    rest = ~compact
    if rest.any():
        parsed = pd.to_datetime(s[rest], errors="coerce", format="mixed")
        out[rest] = parsed.dt.year
        bare = out[rest].isna() & as_str[rest].str.fullmatch(r"(19|20)\d{2}(\.0)?")
        out.loc[bare[bare].index] = pd.to_numeric(as_str[bare[bare].index].str[:4], errors="coerce")
    out[(out < 1900) | (out > 2100)] = np.nan
    return out


def country_of(pub_number: str) -> str | None:
    match = _COUNTRY_PREFIX.match(str(pub_number or "").upper())
    return match.group(1) if match else None


def first_claim(claims: str, limit: int = 900) -> str:
    """The first claim of a claims block (patent claim 1 is usually the main independent claim)."""
    text = " ".join(str(claims or "").split())
    if not text:
        return ""
    match = re.search(r"\s(?:2\s*[.)]|claim\s+2\b|\[2\])\s", text, flags=re.IGNORECASE)
    text = text[: match.start()] if match and match.start() > 40 else text
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _clean_text(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return " ".join(str(value).split())


# --------------------------------------------------------------------------- corpus


@dataclass
class FlowStep:
    step: str
    remaining: int
    removed: int = 0
    note: str = ""


@dataclass
class PatentCorpus:
    """Cleaned patents. `std` holds canonical columns; `raw` the original export columns (same index)."""

    std: pd.DataFrame
    raw: pd.DataFrame
    mapping: dict[str, str | None]
    flow: list[FlowStep] = field(default_factory=list)
    assignee_map: pd.DataFrame = field(default_factory=pd.DataFrame)
    year_basis: str = ""
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.std)


def build_corpus(
    raw: pd.DataFrame,
    mapping: dict[str, str | None],
    years: tuple[int, int] | None = None,
) -> PatentCorpus:
    """Clean an export into a corpus, recording each step's effect on the record count."""
    if not any(mapping.get(r) for r in REQUIRED_ANY):
        raise ValueError("The export needs at least a Title or an Abstract column.")
    raw = raw.reset_index(drop=True)
    flow = [FlowStep("Records in the export", len(raw))]
    notes: list[str] = []

    def col(role: str) -> pd.Series:
        name = mapping.get(role)
        if name and name in raw.columns:
            return raw[name]
        return pd.Series([None] * len(raw), index=raw.index, dtype=object)

    std = pd.DataFrame(index=raw.index)
    std["id"] = col("id").map(_clean_text)
    missing_id = std["id"] == ""
    std.loc[missing_id, "id"] = [f"row-{i + 2}" for i in std.index[missing_id]]  # Excel row number
    for role in ("title", "abstract", "claims", "problem", "method", "advantage", "cpc", "ipc", "family",
                 "inventors", "legal_status"):
        std[role] = col(role).map(_clean_text)
    std["first_claim"] = std["claims"].map(first_claim)
    std["cited_by"] = pd.to_numeric(col("cited_by"), errors="coerce")

    # Assignees: keep all names, use the first (normalised) as the primary owner.
    all_names = col("assignee").map(split_assignees)
    std["assignee_raw"] = all_names.map(lambda names: names[0] if names else "")
    std["assignee"] = std["assignee_raw"].map(lambda n: normalize_assignee(n) if n else "Unknown")
    std["co_assignees"] = all_names.map(lambda names: "; ".join(normalize_assignee(n) for n in names[1:]))

    # Year: the earliest available kind of date, stated in the methodology.
    std["year"] = np.nan
    for role, label in (("priority_date", "earliest priority date"), ("filing_date", "filing date"),
                        ("pub_date", "publication date")):
        if mapping.get(role):
            years_found = parse_year(col(role))
            if years_found.notna().mean() >= 0.5:
                std["year"] = years_found
                year_basis = label
                break
    else:
        year_basis = "not available"
        notes.append("No usable date column: year-based charts are skipped.")

    country_col = col("country").map(_clean_text).str.upper().str[:2]
    std["country"] = np.where(country_col.str.fullmatch(r"[A-Z]{2}"), country_col, std["id"].map(country_of))
    std["country"] = std["country"].fillna("??")

    # Text used for embeddings: title + abstract + start of claims (as in the reference notebook).
    std["text"] = (std["title"] + ". " + std["abstract"] + " " + std["claims"].str[:1000]).str.strip(" .")

    # 1. Records without any text.
    keep = (std["title"] != "") | (std["abstract"] != "")
    _record(keep, flow, "Removed: no title or abstract")
    std, raw = std[keep], raw[keep]

    # 2. Duplicates: one record per patent family when the export has family IDs.
    if (std["family"] != "").mean() >= 0.5:
        key = std["family"].where(std["family"] != "", std["id"])
        basis = "patent family ID"
    else:
        key = std["id"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        basis = "publication number"
    order = std.assign(_y=std["year"].fillna(9999)).sort_values("_y").index  # keep the earliest member
    dup = key.loc[order].duplicated()
    keep = ~dup.reindex(std.index)
    _record(keep, flow, f"Removed: duplicates (same {basis})")
    std, raw = std[keep], raw[keep]
    if basis == "publication number":
        notes.append("No family ID column: duplicates were removed by publication number only, so "
                     "members of the same family filed in several countries are counted separately.")

    # 3. Time window.
    if years and std["year"].notna().any():
        lo, hi = years
        keep = std["year"].between(lo, hi)
        _record(keep, flow, f"Removed: outside {lo}–{hi} ({year_basis}) or no date")
        std, raw = std[keep], raw[keep]

    assignee_map = (
        std.groupby(["assignee_raw", "assignee"]).size().reset_index(name="patents")
        .sort_values(["assignee", "patents"], ascending=[True, False])
    )
    assignee_map = assignee_map[assignee_map["assignee_raw"] != ""]
    return PatentCorpus(std=std.copy(), raw=raw.copy(), mapping=dict(mapping), flow=flow,
                        assignee_map=assignee_map.reset_index(drop=True), year_basis=year_basis, notes=notes)


def _record(keep: pd.Series, flow: list[FlowStep], label: str) -> None:
    removed = int((~keep).sum())
    flow.append(FlowStep(label, int(keep.sum()), removed))
