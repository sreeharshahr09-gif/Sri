"""Pydantic schemas for structured LLM output and trusted records.

The LLM produces *analysis* only. URLs, dates, identifiers and the weighted
score are attached by trusted code - never accepted from model output.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCORE_DIMS = ("relevance", "novelty", "evidence_quality", "impact", "actionability")


class SourceType(str, Enum):
    company = "company"
    supplier = "supplier"
    paper = "paper"
    patent = "patent"
    news = "news"
    conference = "conference"
    video = "video"
    reference = "reference"
    market_report = "market_report"
    unknown = "unknown"


class Maturity(str, Enum):
    commercial = "commercial"            # in production / on sale
    pilot = "pilot"                      # pilot line / field trial
    prototype = "prototype"              # physical prototype or lab demonstrator
    lab_result = "lab_result"            # bench-scale experimental result
    simulation = "simulation"            # modelling / FEA / ML prediction only
    patent_disclosure = "patent_disclosure"  # disclosure; not adoption, not FTO
    supplier_claim = "supplier_claim"    # vendor marketing claim without data
    speculation = "speculation"          # editorial speculation
    business_event = "business_event"    # investment, plant, M&A, partnership, launch event
    regulation = "regulation"            # law, standard or regulatory step (proposed/adopted)


class ConfidenceLabel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# --------------------------------------------------------------------------- #
# LLM structured outputs
# --------------------------------------------------------------------------- #

class PlannedQuery(BaseModel):
    query: str
    source_type: Literal["company", "supplier", "paper", "patent", "news", "conference", "video"]
    max_results: int = Field(default=8, ge=1, le=20)


class QueryPlan(BaseModel):
    queries: list[PlannedQuery] = Field(default_factory=list, max_length=24)


class DimensionScore(BaseModel):
    score: float = Field(ge=0, le=10)
    rationale: str = Field(max_length=400)


class DevelopmentAnalysis(BaseModel):
    """What the LLM may say about one source. No URLs, no dates, no totals."""
    is_new_development: bool
    content_limitation: str | None = None      # e.g. "abstract only", "paywalled"
    injection_flag: bool = False
    title: str = Field(max_length=300)
    organization: str | None = None
    category: str                              # validated against config topics
    application: str | None = None
    what_changed: str | None = None            # what is materially new
    technical_explanation: str | None = None
    claimed_benefit: str | None = None
    baseline_conditions: str | None = None     # baseline, test method, units
    maturity: Maturity
    limitations: str | None = None
    relevance_rationale: str | None = None
    suggested_followup: str | None = None
    confidence_label: ConfidenceLabel
    confidence_reasons: str | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=6)
    novelty_quote: str | None = None
    maturity_quote: str | None = None
    benefit_quote: str | None = None
    scores: dict[str, DimensionScore]

    @field_validator("scores")
    @classmethod
    def _all_dims_present(cls, v: dict[str, DimensionScore]) -> dict[str, DimensionScore]:
        missing = [d for d in SCORE_DIMS if d not in v]
        if missing:
            raise ValueError(f"missing score dimensions: {missing}")
        return v

    @field_validator("novelty_quote", "maturity_quote", "benefit_quote")
    @classmethod
    def _short_basis(cls, v):
        if v and len(v.split()) > 40:
            raise ValueError("claim-support quote too long (>40 words)")
        return v

    @field_validator("evidence_refs")
    @classmethod
    def _short_quotes(cls, v: list[str]) -> list[str]:
        for ref in v:
            if len(ref.split()) > 40:
                raise ValueError("evidence quote too long (>40 words)")
        return v


class ThemeOut(BaseModel):
    theme: str
    development_ids: list[str]


class ActionOut(BaseModel):
    action: str
    development_ids: list[str] = Field(default_factory=list)


class WeeklySynthesis(BaseModel):
    themes: list[ThemeOut] = Field(default_factory=list, max_length=5)
    team_actions: list[ActionOut] = Field(default_factory=list, max_length=6)
    weekly_rank: list[str] = Field(default_factory=list)  # suggested ordering of dev IDs


# --------------------------------------------------------------------------- #
# Trusted records (populated by code, persisted to SQLite)
# --------------------------------------------------------------------------- #

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None     # as stated by the provider; may be unknown
    source_hint: SourceType | None = None


class FetchedDocument(BaseModel):
    url: str                                 # canonical URL, set by trusted code
    final_url: str | None = None             # after validated redirects
    status: Literal["fetched", "paywalled", "blocked", "failed", "unsupported"]
    content_type: str | None = None
    title: str | None = None
    text: str = ""
    text_len: int = 0
    original_text_len: int = 0
    text_truncated: bool = False
    extraction_quality: Literal["full", "partial", "abstract_only", "ocr_low", "none"] = "none"
    published_at: datetime | None = None
    published_provenance: Literal["page_meta", "page_text", "feed", "api", "url", "provider",
                                  "unknown", "conflicting"] = "unknown"
    modified_at: datetime | None = None
    date_candidates: list[dict] = Field(default_factory=list)
    date_notes: list[str] = Field(default_factory=list)
    page_metadata: dict = Field(default_factory=dict)
    content_hash: str | None = None
    stable_ids: dict[str, str] = Field(default_factory=dict)  # doi / patent_number
    error: str | None = None


class ScoreBreakdown(BaseModel):
    relevance: float
    novelty: float
    evidence_quality: float
    impact: float
    actionability: float
    rationales: dict[str, str] = Field(default_factory=dict)


class Development(BaseModel):
    """Full trusted record for one development (DB row shape)."""
    id: str
    title: str
    organization: str | None = None
    category: str
    application: str | None = None
    source_type: SourceType
    canonical_url: str
    source_details: dict = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    published_provenance: str = "unknown"
    event_date: date | None = None
    discovered_at: datetime
    edition_date: date
    what_changed: str | None = None
    technical_explanation: str | None = None
    claimed_benefit: str | None = None
    baseline_conditions: str | None = None
    maturity: Maturity
    limitations: str | None = None
    relevance_rationale: str | None = None
    suggested_followup: str | None = None
    confidence_label: ConfidenceLabel
    confidence_reasons: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown | None = None
    final_score: float | None = None
    is_background: bool = False              # old material newly discovered
    injection_flag: bool = False
    model_version: str | None = None
    prompt_version: str | None = None
    rubric_version: str | None = None
    cluster_id: str | None = None
    status: Literal["active", "quarantined"] = "active"
    quarantine_reason: str | None = None


class CapabilityReport(BaseModel):
    model: str
    reachable: bool
    supports_tools: bool | None = None       # None = could not determine
    supports_structured_output: bool | None = None
    notes: list[str] = Field(default_factory=list)


class CoverageStatus(BaseModel):
    week_start: date
    expected: list[date]
    present: list[date]
    missing: list[date]
    complete: bool
