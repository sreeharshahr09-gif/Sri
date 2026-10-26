"""The landscape pipeline: export + plan -> enriched patents and a report.

run_landscape cleans the export, then runs these stages in order. Each reads what earlier stages wrote on
the LandscapeResult (all of them also add to `methods` and `warnings`):

  stage                reads                                  writes
  patent map           corpus                                 semantic; cluster and map columns of corpus.std
  reading the patents  corpus, plan                           extraction
  analytics            corpus, extraction, plan               analysis_frame, exhibits, incomplete/early years
  white-space check    exhibits, plan                         methods["White-space check"]
  facts                exhibits, semantic, analysis_frame     facts_by_kind, all_facts
  brief's questions    corpus, extraction, all_facts, plan    enriched, question_runs; the answers join the facts
  report text          facts, plan, question_runs             sections

A stage that fails is noted (status "error", the reason in the report's notes) and the later stages run on
what there is. Stop ends the run at the next stage, patent batch or agent step. report_blocks lays the
result out as the report.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np
import pandas as pd

from ..agent import DataAnalystAgent
from ..config import AgentConfig, SandboxConfig
from ..llm import is_openrouter
from ..runs import EventHandler
from ..sandbox import Sandbox
from . import analytics as an
from . import enrich
from . import narrative as nar
from . import semantic as sem
from .data import FlowStep, PatentCorpus, build_corpus
from .followup import agent_client, analysis_dataset, landscape_profile, profile_budget
from .plan import AnalysisPlan
from .report_blocks import assemble_blocks
from .result import LandscapeResult, PipelineOptions, analysed_index, analysed_topics

MIN_PATENTS_FOR_MAP = 8


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

    def progress(done: int, total: int) -> None:
        if should_stop is not None and should_stop():
            raise Cancelled()
        emit("progress", (done, total))

    checkpoint("Cleaning the export")
    corpus = build_corpus(raw, mapping, years=plan.years)  # without a corpus there is nothing to report
    if options.sample_limit and len(corpus) > options.sample_limit:
        keep = corpus.std.sample(options.sample_limit, random_state=options.seed).index.sort_values()
        removed = len(corpus) - len(keep)
        corpus.std, corpus.raw = corpus.std.loc[keep], corpus.raw.loc[keep]
        corpus.flow.append(FlowStep(f"Trial run: random sample of {options.sample_limit:,}", len(keep), removed))
    result = LandscapeResult(plan=plan, corpus=corpus, dataset_name=dataset_name, dataset_sha256=dataset_sha256)
    result.warnings.extend(corpus.notes)
    result.methods["Year basis"] = corpus.year_basis

    try:
        if options.semantic_map:
            _guarded(result, "The patent map", semantic_stage, result, llm, options, checkpoint, progress)
        if options.extract_details:
            checkpoint("Reading every patent")
            _guarded(result, "Reading the patents", extraction_stage, result, llm, options, emit, should_stop)
        checkpoint("Computing the landscape analytics")
        _guarded(result, "The landscape analytics", analytics_stage, result)
        if llm is not None and options.write_report:
            _guarded(result, "The white-space check", white_space_stage, result, llm)
        _guarded(result, "Collecting the facts", collect_facts, result)
        if options.answer_questions and result.plan.analysis_questions:
            _guarded(result, "Answering the brief's questions", questions_stage, result, llm, emit, checkpoint,
                     should_stop)
            add_answers_to_facts(result)  # the report text is written from them too
        if options.write_report:
            checkpoint("Writing the report")
            _guarded(result, "Writing the report text", narrative_stage, result, llm, emit)
    except Cancelled:
        result.status = "cancelled"
    cfg = getattr(llm, "config", None)
    result.model_name = (cfg.model + (" via OpenRouter" if is_openrouter(cfg.base_url) else "")) if cfg else ""
    if result.enriched is None:
        result.enriched = enriched_table(result)
    result.blocks = assemble_blocks(result, options)
    result.duration_s = time.perf_counter() - started
    return result


def _guarded(result: LandscapeResult, name: str, stage: Callable, *args) -> None:
    """Run one stage. If it fails, note why and carry on: the stages before it still reach the report."""
    try:
        stage(*args)
    except Cancelled:
        raise
    except Exception as exc:  # one failing stage must not lose the whole landscape
        message = f"{name} failed ({type(exc).__name__}: {exc}); the rest of the report was still produced."
        result.status = "error"
        result.error = result.error or message
        result.warnings.append(message)


def analytics_stage(result: LandscapeResult) -> None:
    corpus, ext = result.corpus, result.extraction
    frame = an.build_frame(corpus.std, ext.table if ext else None, ext.fields if ext else [])
    keep = an.in_scope(frame)
    if (~keep).any():
        corpus.flow.append(FlowStep("Removed: judged off-topic by the model (Relevance: Low)", int(keep.sum()),
                                    int((~keep).sum())))
    result.analysis_frame = frame[keep]
    ctx = an.make_context(result.analysis_frame, result.plan, corpus.year_basis, ref_year=export_date(corpus))
    result.incomplete_years, result.early_years = ctx.incomplete_years, ctx.early_years
    result.window_note = an.early_years_text(ctx)
    if result.window_note:
        result.warnings.append(result.window_note)
    result.exhibits, notes = an.run_analytics(result.analysis_frame, result.plan, corpus.year_basis, ctx=ctx)
    result.warnings.extend(notes)


def white_space_stage(result: LandscapeResult, llm) -> None:
    ex = next((e for e in result.exhibits if e.key == "white_space"), None)
    if ex is not None:
        note = nar.check_white_space(ex, result.plan, llm)
        if note:
            result.methods["White-space check"] = note


FACT_PRIORITY = ["trend", "players", "problem_solution", "white_space", "roadmap", "complexity", "competitors",
                 "process", "segments", "geography"]


def export_date(corpus: PatentCorpus) -> float | None:
    """When the export was made, judged by its latest publication date (as a fractional year)."""
    col = corpus.mapping.get("pub_date")
    if not col or col not in corpus.raw:
        return None
    values = corpus.raw[col]
    if not pd.api.types.is_datetime64_any_dtype(values):
        values = pd.to_datetime(values.where(values.map(lambda v: isinstance(v, str))), errors="coerce",
                                format="mixed")  # only text dates: a bare number is no publication date
    latest = values.max()
    if pd.isna(latest) or not 1990 <= latest.year <= 2100:
        return None
    return an.fractional_year(latest.date())


def collect_facts(result: LandscapeResult) -> None:
    """The fact sheets the report text (and the data agent's context) are written from."""
    by_kind: dict[str, str] = {}
    for ex in result.exhibits:
        by_kind[ex.section] = by_kind.get(ex.section, "") + f"[{ex.title}] {ex.facts}\n"
    topics = analysed_topics(result)
    clusters = nar.cluster_facts(None if topics is None else topics[topics["patents"] > 0],
                                 len(analysed_index(result)))
    if clusters:
        by_kind["clusters"] = f"[Topic clusters] {clusters}\n" + by_kind.get("clusters", "")
    frame = result.analysis_frame if result.analysis_frame is not None else result.corpus.std
    ordered = sorted(result.exhibits, key=lambda e: FACT_PRIORITY.index(e.key) if e.key in FACT_PRIORITY else 99)
    result.all_facts = an.summary_facts(frame, ordered[:1]) + (f" [Topic clusters] {clusters}" if clusters else "") \
        + " " + " ".join(f"[{ex.title}] {ex.facts}" for ex in ordered[1:])
    result.facts_by_kind = by_kind


MAX_QUESTIONS = 6


def answered_questions(result: LandscapeResult) -> list[str]:
    """The brief's questions the data agent answered for the report's own section."""
    return [q for q, run in result.question_runs if run.answer and not run.error]


def questions_stage(result: LandscapeResult, llm, emit: EventHandler, checkpoint: Callable[[str], None],
                    should_stop: Callable[[], bool] | None = None) -> None:
    """The data agent answers each brief-specific question with code run on the enriched patent table."""
    if llm is None:
        result.warnings.append("No model connected: the brief-specific questions were not answered.")
        return
    result.enriched = enriched_table(result)
    dataset = analysis_dataset(result)
    questions = result.plan.analysis_questions[:MAX_QUESTIONS]
    client = agent_client(llm)
    config = AgentConfig()
    with Sandbox(SandboxConfig()) as sandbox:
        agent = DataAnalystAgent(client, sandbox, dataset, config=config,
                                 profile=landscape_profile(result, dataset, max_chars=profile_budget(client)))
        for i, question in enumerate(questions):
            checkpoint(f"Answering the brief's questions ({i + 1} of {len(questions)})")
            # The agent's own events show its steps in the status bar; Stop reaches it between steps.
            run = agent.run(question, on_event=emit, should_stop=should_stop)
            if run.status == "cancelled":
                raise Cancelled()
            result.question_runs.append((question, run))
    answered = sum(1 for _, run in result.question_runs if run.answer and run.executed)
    result.methods["Brief-specific questions"] = (
        f"{answered} of {len(questions)} answered by the data agent with code run on the enriched patent table "
        f"(Python sandbox, up to {config.max_steps} steps each); numbers in the answers are traced to the "
        "code output.")


def add_answers_to_facts(result: LandscapeResult) -> None:
    """The answers to the brief's questions become facts that the report text is written from."""
    answers = [f"[Brief question] {question} Answer: {run.answer[:700]}"
               for question, run in result.question_runs if run.answer]
    if answers:
        text = "\n".join(answers)
        result.facts_by_kind["custom"] = result.facts_by_kind.get("custom", "") + text + "\n"
        result.all_facts += " " + text


def narrative_stage(result: LandscapeResult, llm, emit) -> None:
    sections = nar.plan_sections(result.plan.report_sections)
    by_kind, all_facts = result.facts_by_kind, result.all_facts
    nar.write_sections(sections, by_kind, all_facts, result.plan.brief, result.plan.answers, llm,
                       on_progress=lambda done, total: emit("progress", (done, total)),
                       answered_elsewhere=answered_questions(result))
    result.sections = sections
    if llm is not None:
        written = sum(1 for sec in sections if sec.text)
        flagged = sum(len(sec.flagged) for sec in sections)
        result.methods["Report text"] = (f"Written by {result_model(llm)} for {written} of {len(sections)} sections "
                                         f"from the computed facts only; {flagged} figures could not be matched to "
                                         "them and are flagged in the text.")


def result_model(llm) -> str:
    return getattr(getattr(llm, "config", None), "model", "") or type(llm).__name__


def extraction_stage(result: LandscapeResult, llm, options: PipelineOptions, emit, should_stop) -> None:
    if llm is None:
        result.warnings.append("No model connected: the patent details (topic, design lever, ...) were not extracted.")
        return
    res = enrich.extract_details(
        result.corpus.std, result.plan, llm, batch_size=options.batch_size, seed=options.seed,
        on_progress=lambda done, total: emit("progress", (done, total)), should_stop=should_stop,
    )
    result.extraction = res
    result.methods["Patent details"] = res.info
    if res.stats["failed"]:
        reasons = res.table.loc[res.table["Extraction"].str.startswith("failed"), "Extraction"].value_counts()
        result.warnings.append(f"The model could not read {res.stats['failed']:,} patents (most often: "
                               f"{reasons.index[0].removeprefix('failed: ')}). Run the analysis again to retry "
                               "them; finished patents are kept.")
    if res.stopped:
        result.stop_reason = res.stop_reason
        raise Cancelled()


def semantic_stage(result: LandscapeResult, llm, options: PipelineOptions, checkpoint, progress) -> None:
    corpus = result.corpus
    if len(corpus) < MIN_PATENTS_FOR_MAP:
        result.warnings.append(f"Too few patents for a semantic map (at least {MIN_PATENTS_FOR_MAP} needed).")
        return
    try:
        res = sem.semantic_map(
            corpus.std["text"].tolist(), corpus.std["title"].tolist(), result.plan, llm=llm, seed=options.seed,
            model_name=options.embedding_model, on_progress=progress, on_phase=checkpoint,
        )
    except ImportError as exc:  # scikit-learn missing: the rest of the landscape still works
        package = (exc.name or "scikit-learn").split(".")[0]
        package = {"sklearn": "scikit-learn", "umap": "umap-learn"}.get(package, package)
        result.warnings.append(f"The patent map and topic clusters were skipped: {package} is not installed. "
                               "Run `python -m pip install -r requirements.txt` once to add it.")
        return
    result.semantic = res
    attach_semantic(corpus, res)
    result.methods.update({
        "Text embedding": res.info["embedding"],
        "Patent map": res.info["map"],
        "Topic clusters": res.info["clustering"],
        "Cluster keywords": res.info["keywords"],
        "Cluster names": res.info["naming"],
    })


def attach_semantic(corpus: PatentCorpus, res: sem.SemanticResult) -> None:
    """Map position and topic cluster for every patent, as columns of the patent table."""
    labels = dict(zip(res.topics["id"], res.topics["label"]))
    groups = dict(zip(res.topics["id"], res.topics["tech_group"]))
    std = corpus.std
    std["map_x"] = np.round(res.xy[:, 0], 4)
    std["map_y"] = np.round(res.xy[:, 1], 4)
    std["cluster_id"] = res.clusters.astype(int)
    std["cluster_label"] = [labels[c] for c in res.clusters]
    std["cluster_tech_group"] = [groups.get(c, "") for c in res.clusters]
    std["cluster_outlier"] = res.outlier


def enriched_table(result: LandscapeResult) -> pd.DataFrame:
    """The patent table for the CSV: identifiers, the study's columns, clusters, then the original export."""
    std = result.corpus.std
    out = pd.DataFrame(index=std.index)
    out["Patent ID"] = std["id"]
    out["Title"] = std["title"]
    out["Year"] = std["year"].astype("Int64")
    out["Assignee (normalised)"] = std["assignee"]
    out["Co-assignees"] = std["co_assignees"]
    out["Office"] = std["country"]
    if result.extraction is not None:
        for col in result.extraction.table.columns:
            name = col if col not in out.columns else f"{col} (model)"
            out[name] = result.extraction.table[col]
    if "cluster_id" in std:
        out["Topic cluster"] = std["cluster_label"]
        out["Cluster no."] = std["cluster_id"]
        out["Cluster technology group"] = std["cluster_tech_group"]
        out["Map X"] = std["map_x"]
        out["Map Y"] = std["map_y"]
    out["First claim"] = std["first_claim"]
    raw = result.corpus.raw.loc[std.index]
    for col in raw.columns:
        name = str(col)
        if name.startswith("_"):
            continue
        out[name if name not in out.columns else f"{name} (export)"] = raw[col]
    return out.reset_index(drop=True)
