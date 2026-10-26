"""What a landscape run is given (PipelineOptions) and what it produces (LandscapeResult).

The pipeline's stages fill a LandscapeResult in order (see pipeline.py for who writes what) and
report_blocks.py lays it out as the report. The helpers below are the views of a result that both need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ..agent import DataRun
from ..enums import StrEnum
from . import analytics as an
from . import enrich
from . import narrative as nar
from . import report as rb
from . import semantic as sem
from .data import PatentCorpus
from .plan import AnalysisPlan


@dataclass
class PipelineOptions:
    sample_limit: int = 0  # analyse at most this many patents (0 = all), for a quick trial run
    seed: int = 42
    embedding_model: str = sem.DEFAULT_MODEL  # "tfidf" skips the neural model
    semantic_map: bool = True
    extract_details: bool = True  # the model reads every patent and fills the study's columns
    batch_size: int = 0  # patents per extraction request (0 = from the context window)
    write_report: bool = True  # the model writes each section's text from the computed facts
    answer_questions: bool = True  # the data agent answers the plan's brief-specific questions
    validation_size: int = 30  # patents in the validation sample


class LandscapeStatus(StrEnum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"  # stopped by the user or a request limit: the finished part is reported
    ERROR = "error"  # a stage failed; the others still ran


@dataclass
class LandscapeResult:
    plan: AnalysisPlan
    corpus: PatentCorpus
    blocks: list[rb.Block] = field(default_factory=list)
    enriched: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_s: float = 0.0
    status: LandscapeStatus = LandscapeStatus.COMPLETE
    error: str | None = None  # the first failed stage and why
    dataset_name: str = ""
    dataset_sha256: str = ""
    semantic: sem.SemanticResult | None = None
    extraction: enrich.ExtractionResult | None = None
    analysis_frame: pd.DataFrame | None = None  # the patents the analytics are computed on
    exhibits: list[an.Exhibit] = field(default_factory=list)
    sections: list[nar.Section] = field(default_factory=list)
    facts_by_kind: dict[str, str] = field(default_factory=dict)  # what the text was written from
    question_runs: list[tuple[str, DataRun]] = field(default_factory=list)  # the brief-specific questions
    all_facts: str = ""
    model_name: str = ""
    stop_reason: str = ""  # why the run stopped early, if not at the user's request
    incomplete_years: list[int] = field(default_factory=list)  # years cut short by the publication lag
    early_years: list[int] = field(default_factory=list)  # first years cut short: the export starts later
    window_note: str = ""  # explains early_years
    methods: dict[str, str] = field(default_factory=dict)  # method -> description, for the methodology


def analysed_index(result: LandscapeResult) -> pd.Index:
    """The patents the report's analytics use: the corpus without those judged off-topic."""
    return result.analysis_frame.index if result.analysis_frame is not None else result.corpus.std.index


def analysed_topics(result: LandscapeResult) -> pd.DataFrame | None:
    """The topic clusters with their patents counted in the analysed set, as everywhere else in the report
    ('all_patents': the count over the whole export, off-topic patents included), largest first."""
    res = result.semantic
    if res is None or res.topics is None or res.topics.empty or "cluster_id" not in result.corpus.std:
        return None
    std = result.corpus.std
    counts = std.loc[std.index.isin(analysed_index(result)), "cluster_id"].value_counts()
    topics = res.topics.copy()
    topics["all_patents"] = topics["patents"]
    topics["patents"] = topics["id"].map(counts).fillna(0).astype(int)
    frame = result.analysis_frame
    if result.extraction is not None and frame is not None and "tech_group" in frame:
        # Every patent's group was read from the patent itself: the cluster takes its patents' most frequent
        # group, not the namer's guess from keywords (which put "Carcass ply architecture" under "Novel
        # architecture" for the user).
        groups = frame.loc[frame["tech_group"] != an.UNCLASSIFIED].assign(cid=std.loc[frame.index, "cluster_id"])
        majority = groups.groupby("cid")["tech_group"].agg(lambda g: g.value_counts().index[0])
        topics["tech_group"] = topics["id"].map(majority).fillna(topics.get("tech_group", ""))
    return topics.sort_values(["patents", "all_patents"], ascending=False, kind="stable")
