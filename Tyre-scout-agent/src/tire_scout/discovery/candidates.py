"""Candidate records, URL dates, and cheap relevance triage before fetching."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

# Publication-date provenance that counts as a stated publication date.
# "provider" is only produced by news-mode search indexes.
DATED_PROVENANCE = ("page_meta", "page_text", "feed", "api", "url", "provider")

TIRE_TERMS = re.compile(
    r"\b(?:tyres?|tires?|tyremaker\w*|tiremaker\w*|retread\w*|rubber|elastomer\w*|treads?|"
    r"treadwear|vulcani[sz]\w*|devulcani[sz]\w*|carbon black|silica|TPMS|sidewall|bead wire|"
    r"steel cord|tire cord|tyre cord|pneumatic|end-of-life tyres?|end-of-life tires?)\b",
    re.IGNORECASE)

URL_DATE = (
    re.compile(r"/(20\d{2})[/-](0[1-9]|1[0-2])[/-](0[1-9]|[12]\d|3[01])(?:/|-|$)"),
    re.compile(r"/(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?:/|-|$)"),
)


@dataclass
class Candidate:
    url: str
    title: str
    channel: str                       # feed | news_rss | papers | web_search
    source_name: str = ""             # feed or publisher name
    source_type: str = "news"         # channel-derived type, not a planner guess
    snippet: str = ""
    text: str = ""                    # full text when the channel provides it (paper abstract)
    published_at: datetime | None = None
    date_provenance: str = "unknown"
    tire_specific: bool = False       # trade source: everything is tire-related
    canonical_url: str = ""
    relevance: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    skip_reason: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        return d


def url_date(url: str) -> datetime | None:
    path = urlsplit(url).path
    for pattern in URL_DATE:
        m = pattern.search(path)
        if m:
            try:
                return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def clean_title(title: str, source_name: str = "") -> str:
    title = " ".join((title or "").split())
    # News aggregators append " - Publisher"; it would defeat title dedup.
    if source_name and title.endswith(" - " + source_name):
        title = title[: -len(source_name) - 3]
    return title.strip()


def score_relevance(cand: Candidate, topics, extra_terms: list[str] | None = None) -> None:
    """Keyword triage on title + summary. Cheap and deterministic; the model
    still judges every candidate that survives."""
    text = f"{cand.title} {cand.snippet} {cand.text[:1500]}"
    weight = {"high": 3.0, "medium": 2.0, "low": 1.0}
    score, matched = 0.0, []
    lowered = text.lower()
    for topic in topics:
        for kw in topic.keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", lowered):
                score += weight.get(topic.priority, 1.0)
                matched.append(kw)
    for term in extra_terms or []:
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lowered):
            score += 1.0
            matched.append(term)
    has_tire = bool(TIRE_TERMS.search(text))
    if has_tire:
        score += 2.0
    if cand.tire_specific:
        score += 2.0
    cand.relevance = score
    cand.matched_terms = list(dict.fromkeys(matched))[:12]
    if not has_tire and not cand.tire_specific:
        cand.skip_reason = "not tire-related (no tire/rubber terms in title or summary)"
