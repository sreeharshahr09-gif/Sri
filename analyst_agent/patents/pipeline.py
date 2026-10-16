"""The landscape pipeline: export + plan -> enriched patents and a report (as blocks)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from . import report as rb
from .data import PatentCorpus, build_corpus
from .plan import AnalysisPlan

EventHandler = Callable[[str, Any], None]


@dataclass
class PipelineOptions:
    sample_limit: int = 0  # analyse at most this many patents (0 = all), for a quick trial run
    seed: int = 42


@dataclass
class LandscapeResult:
    plan: AnalysisPlan
    corpus: PatentCorpus
    blocks: list[rb.Block] = field(default_factory=list)
    enriched: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_s: float = 0.0
    status: str = "complete"  # complete | cancelled | error
    error: str | None = None
    dataset_name: str = ""
    dataset_sha256: str = ""


class Cancelled(Exception):
    pass


def run_landscape(
    raw: pd.DataFrame,
    mapping: dict[str, str | None],
    plan: AnalysisPlan,
    llm=None,
    options: PipelineOptions | None = None,
    on_event: EventHandler | None = None,
    should_stop: Callable[[], bool] | None = None,
    dataset_name: str = "",
    dataset_sha256: str = "",
) -> LandscapeResult:
    options = options or PipelineOptions()
    emit = on_event or (lambda *_: None)
    started = time.perf_counter()

    def checkpoint(label: str) -> None:
        if should_stop is not None and should_stop():
            raise Cancelled()
        emit("phase", label)

    checkpoint("Cleaning the export")
    corpus = build_corpus(raw, mapping, years=plan.years)
    if options.sample_limit and len(corpus) > options.sample_limit:
        keep = corpus.std.sample(options.sample_limit, random_state=options.seed).index.sort_values()
        removed = len(corpus) - len(keep)
        corpus.std, corpus.raw = corpus.std.loc[keep], corpus.raw.loc[keep]
        from .data import FlowStep

        corpus.flow.append(FlowStep(f"Trial run: random sample of {options.sample_limit:,}", len(keep), removed))
    result = LandscapeResult(plan=plan, corpus=corpus, dataset_name=dataset_name, dataset_sha256=dataset_sha256)
    result.warnings.extend(corpus.notes)

    try:
        result.blocks.extend(corpus_blocks(corpus, plan))
    except Cancelled:
        result.status = "cancelled"
    result.duration_s = time.perf_counter() - started
    return result


def corpus_blocks(corpus: PatentCorpus, plan: AnalysisPlan) -> list[rb.Block]:
    std = corpus.std
    years = std["year"].dropna()
    span = f"{int(years.min())}–{int(years.max())}" if len(years) else "n/a"
    blocks = [
        rb.heading(f"Patent landscape: {plan.title}", level=1),
        rb.metrics([
            ("Patents analysed", f"{len(corpus):,}"),
            ("Years", span),
            ("Assignees", f"{std['assignee'].nunique():,}"),
            ("Patent offices", f"{std['country'].nunique():,}"),
        ]),
        rb.table(
            pd.DataFrame({
                "Step": [f.step for f in corpus.flow],
                "Removed": pd.array([f.removed if i else None for i, f in enumerate(corpus.flow)], dtype="Int64"),
                "Remaining": [f.remaining for f in corpus.flow],
            }),
            title="How the patent set was built",
            caption=f"Year basis: {corpus.year_basis}.",
        ),
    ]
    return blocks
