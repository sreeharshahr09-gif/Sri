"""The landscape pipeline: export + plan -> enriched patents and a report (as blocks)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..llm import is_openrouter
from . import analytics as an
from . import enrich
from . import narrative as nar
from . import report as rb
from . import semantic as sem
from .data import FlowStep, PatentCorpus, build_corpus
from .html_report import report_html
from .plan import AnalysisPlan

EventHandler = Callable[[str, Any], None]


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


MIN_PATENTS_FOR_MAP = 8


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
    semantic: sem.SemanticResult | None = None
    extraction: enrich.ExtractionResult | None = None
    analysis_frame: pd.DataFrame | None = None  # the patents the analytics are computed on
    exhibits: list[an.Exhibit] = field(default_factory=list)
    sections: list[nar.Section] = field(default_factory=list)
    facts_by_kind: dict[str, str] = field(default_factory=dict)  # what the text was written from
    question_runs: list = field(default_factory=list)  # (question, AgentRun) for the brief-specific questions
    all_facts: str = ""
    model_name: str = ""
    stop_reason: str = ""  # why the run stopped early, if not at the user's request
    methods: dict[str, str] = field(default_factory=dict)  # method -> description, for the methodology


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
    corpus = build_corpus(raw, mapping, years=plan.years)
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
            semantic_stage(result, llm, options, checkpoint, progress)
        if options.extract_details:
            checkpoint("Reading every patent")
            extraction_stage(result, llm, options, emit, should_stop)
        checkpoint("Computing the landscape analytics")
        analytics_stage(result)
        collect_facts(result)
        if options.answer_questions and result.plan.analysis_questions:
            questions_stage(result, llm, emit, checkpoint)
        if options.write_report:
            checkpoint("Writing the report")
            narrative_stage(result, llm, emit)
    except Cancelled:
        result.status = "cancelled"
    cfg = getattr(llm, "config", None)
    result.model_name = (cfg.model + (" via OpenRouter" if is_openrouter(cfg.base_url) else "")) if cfg else ""
    result.enriched = enriched_table(result)
    result.blocks = assemble_blocks(result, options)
    result.duration_s = time.perf_counter() - started
    return result


def analytics_stage(result: LandscapeResult) -> None:
    corpus, ext = result.corpus, result.extraction
    frame = an.build_frame(corpus.std, ext.table if ext else None, ext.fields if ext else [])
    keep = an.in_scope(frame)
    if (~keep).any():
        corpus.flow.append(FlowStep("Removed: judged off-topic by the model (Relevance: Low)", int(keep.sum()),
                                    int((~keep).sum())))
    result.analysis_frame = frame[keep]
    result.exhibits, notes = an.run_analytics(result.analysis_frame, result.plan, corpus.year_basis,
                                              ref_year=export_date(corpus))
    result.warnings.extend(notes)


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


def questions_stage(result: LandscapeResult, llm, emit, checkpoint) -> None:
    """The data agent answers each brief-specific question with code run on the enriched patent table."""
    if llm is None:
        result.warnings.append("No model connected: the brief-specific questions were not answered.")
        return
    from ..agent import DataAnalystAgent
    from ..config import AgentConfig, SandboxConfig
    from ..sandbox import Sandbox
    from .followup import agent_client, analysis_dataset, landscape_profile, profile_budget

    result.enriched = enriched_table(result)
    dataset = analysis_dataset(result)
    questions = result.plan.analysis_questions[:MAX_QUESTIONS]
    answers = []
    client = agent_client(llm)
    with Sandbox(SandboxConfig()) as sandbox:
        agent = DataAnalystAgent(client, sandbox, dataset, config=AgentConfig(),
                                 profile=landscape_profile(result, dataset, max_chars=profile_budget(client)))
        for i, question in enumerate(questions):
            checkpoint(f"Answering the brief's questions ({i + 1} of {len(questions)})")
            run = agent.run(question)
            result.question_runs.append((question, run))
            if run.answer:
                answers.append(f"[Brief question] {question} Answer: {run.answer[:700]}")
    if answers:
        text = "\n".join(answers)
        result.facts_by_kind["custom"] = result.facts_by_kind.get("custom", "") + text + "\n"
        result.all_facts += " " + text
    answered = sum(1 for _, run in result.question_runs if run.answer and run.executed)
    result.methods["Brief-specific questions"] = (
        f"{answered} of {len(questions)} answered by the data agent with code run on the enriched patent table "
        f"(Python sandbox, up to {AgentConfig().max_steps} steps each); numbers in the answers are traced to the "
        "code output.")


def narrative_stage(result: LandscapeResult, llm, emit) -> None:
    sections = nar.plan_sections(result.plan.report_sections)
    by_kind, all_facts = result.facts_by_kind, result.all_facts
    nar.write_sections(sections, by_kind, all_facts, result.plan.brief, result.plan.answers, llm,
                       on_progress=lambda done, total: emit("progress", (done, total)))
    result.sections = sections
    if llm is not None:
        written = sum(1 for sec in sections if sec.text)
        flagged = sum(len(sec.flagged) for sec in sections)
        result.methods["Report text"] = (f"Written by {result_model(llm)} for {written} of {len(sections)} sections "
                                         f"from the computed facts only; {flagged} figures could not be matched to "
                                         "them and are flagged in the text.")


def brief_specific_blocks(result: LandscapeResult) -> list[rb.Block]:
    parts: list[rb.Block] = []
    for ex in (e for e in result.exhibits if e.section == "custom"):
        parts.append(rb.heading(ex.title, level=3))
        parts.extend(ex.blocks)
    for question, run in result.question_runs:
        parts.append(rb.heading(question, level=3))
        parts.extend(agent_run_blocks(run))
    if not parts:
        return []
    return [rb.heading("🎯 Brief-specific analyses"),
            rb.caption("Your extra columns, and your plan's own questions answered by the data agent with Python "
                       "code run on the enriched patent table. The code behind each answer is shown.")] + parts


def agent_run_blocks(run) -> list[rb.Block]:
    """An AgentRun as report blocks: answer, number check, charts and tables, code."""
    blocks: list[rb.Block] = []
    if run.status in ("llm_error", "error"):
        blocks.append(rb.note(f"Not answered: {run.error or 'the analysis stopped'}"))
    if run.answer:
        blocks.append(rb.markdown(run.answer))
        if not run.executed:
            blocks.append(rb.note("This answer is not backed by any executed code; treat it as unverified."))
        elif run.status == "step_limit":
            blocks.append(rb.note("The step limit was reached before the agent finished; the answer may be "
                                  "incomplete."))
    g = run.grounding
    if g and g.checked and run.executed:
        everything = ("The number in the answer traces back to the code output." if len(g.checked) == 1 else
                      f"All {len(g.checked)} numbers in the answer trace back to the code output.")
        blocks.append(rb.caption(everything if g.fully_traced else
                                 f"{g.traced_count} of {len(g.checked)} numbers trace back to the code output; "
                                 f"not traced: {', '.join(g.untraced[:8])}."))
    for art in run.report_artifacts():
        block = artifact_block(art)
        if block is not None:
            blocks.append(block)
    code = "\n\n".join(run.successful_code)
    if code:
        blocks.append(rb.code(code))
    return blocks


def artifact_block(art) -> rb.Block | None:
    import base64

    try:
        if art.kind == "plotly":
            return rb.chart(art.to_plotly())
        if art.kind == "table":
            df = art.to_dataframe()
            if not pd.api.types.is_integer_dtype(df.index):  # labelled index (e.g. value_counts): keep it
                df = df.reset_index()
            return rb.table(df, title=art.title or "")
        if art.kind == "image":
            return rb.image(base64.b64decode(art.payload["png_b64"]), title=art.title or "")
        text = str(art.payload.get("text", art.summary))
        if art.kind == "text":
            return rb.markdown(text)
        return rb.metrics([(art.title or "Result", text)]) if len(text) <= 40 else rb.markdown(f"`{text}`")
    except (AttributeError, KeyError, TypeError, ValueError):  # a broken output must not break the report
        return None


def result_model(llm) -> str:
    return getattr(getattr(llm, "config", None), "model", "") or type(llm).__name__


def assemble_blocks(result: LandscapeResult, options: PipelineOptions | None = None) -> list[rb.Block]:
    options = options or PipelineOptions()
    blocks = title_blocks(result)
    sections = result.sections or nar.plan_sections(result.plan.report_sections)
    for sec in sections:
        parts = nar.section_blocks(sec)
        if "summary" in sec.kinds:
            plan = result.plan
            scope = " ".join(x for x in [plan.objective, plan.in_scope] if x)
            if scope:
                parts.append(rb.markdown(f"**Scope of this study:** {scope}"))
        if "clusters" in sec.kinds and result.semantic is not None:
            parts.extend(semantic_blocks(result.corpus, result.semantic, analysed_topics(result),
                                         analysed_index(result)))
        for ex in (e for e in result.exhibits if e.section in sec.kinds):
            parts.append(rb.heading(ex.title, level=3))
            parts.extend(ex.blocks)
        if parts:
            blocks.append(rb.heading(sec.heading))
            blocks.extend(parts)
    blocks.extend(brief_specific_blocks(result))
    blocks.extend(nar.methodology_blocks(result))
    if result.warnings:
        blocks.append(rb.markdown("**Notes from this run**\n\n" + "\n".join(f"- {w}" for w in result.warnings)))
    blocks.extend(table_blocks(result, options))
    blocks.append(html_download(result, blocks))
    return blocks


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


def title_blocks(result: LandscapeResult) -> list[rb.Block]:
    corpus, plan = result.corpus, result.plan
    std = result.analysis_frame if result.analysis_frame is not None else corpus.std
    years = std["year"].dropna()
    span = f"{int(years.min())}–{int(years.max())}" if len(years) else "n/a"
    return [
        rb.heading(f"Patent landscape: {plan.title}", level=1),
        rb.metrics([
            ("Patents analysed", f"{len(std):,}"),
            ("Years", span),
            ("Assignees", f"{std['assignee'].nunique():,}"),
            ("Patent offices", f"{std['country'].nunique():,}"),
        ]),
    ]


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


OFF_TOPIC = "Off-topic (left out)"


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
    return topics.sort_values(["patents", "all_patents"], ascending=False, kind="stable")


def semantic_blocks(corpus: PatentCorpus, res: sem.SemanticResult, topics: pd.DataFrame | None = None,
                    analysed: pd.Index | None = None) -> list[rb.Block]:
    """The map shows every patent (off-topic ones in grey); the table and treemap count the analysed set."""
    import plotly.express as px

    std = corpus.std
    topics = res.topics.assign(all_patents=res.topics["patents"]) if topics is None else topics
    analysed = std.index if analysed is None else analysed
    off = ~std.index.isin(analysed)
    n, n_off = int((~off).sum()), int(off.sum())
    names = {cid: f"{cid} · {_short(label, 48)}" for cid, label in zip(topics["id"], topics["label"])}
    order = [names[cid] for cid in sorted(topics["id"])] + ([OFF_TOPIC] if n_off else [])
    frame = pd.DataFrame({
        "x": std["map_x"], "y": std["map_y"], "Topic": std["cluster_id"].map(names).where(~off, OFF_TOPIC),
        "Title": std["title"].map(_short), "Assignee": std["assignee"].map(lambda a: _short(a, 40)),
        "Year": std["year"].astype("Int64").astype(str).replace("<NA>", "n/a"), "ID": std["id"],
    })
    palette = px.colors.qualitative.Dark24 + px.colors.qualitative.Light24
    fig = px.scatter(
        frame, x="x", y="y", color="Topic", category_orders={"Topic": order},
        hover_data={"x": False, "y": False, "Title": True, "Assignee": True, "Year": True, "ID": True},
        color_discrete_sequence=palette, color_discrete_map={OFF_TOPIC: "#c4c4c4"}, render_mode="webgl", height=640,
    )
    fig.update_traces(marker={"size": 6, "opacity": 0.75})
    # A numbered tag at each cluster's centre; the legend gives the number's name.
    centres = frame.assign(cid=std["cluster_id"].values).groupby("cid")[["x", "y"]].median()
    for cid, row in centres.iterrows():
        fig.add_annotation(x=row["x"], y=row["y"], yshift=16, text=f"<b>{cid}</b>", showarrow=False,
                           font={"size": 11}, bgcolor="rgba(255,255,255,0.85)", bordercolor="#888", borderwidth=1,
                           borderpad=1)
    fig.update_layout(
        xaxis={"visible": False}, yaxis={"visible": False}, legend_title_text="Topic cluster",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        title=f"Patent map: {n + n_off:,} patents, {len(topics)} topic clusters"
              + (f" ({n_off:,} off-topic, in grey)" if n_off else ""),
    )
    blocks = [
        rb.heading("Patent map and topic clusters", level=3),
        rb.chart(fig, title="Patent map", caption=(
            "Each dot is one patent; patents with similar text sit close together. Colours are topic clusters, "
            "numbered as in the table below"
            + ("; grey dots are patents judged off-topic, which the analytics leave out. " if n_off else ". ")
            + 
            f"Text embedding: {res.info['embedding']} Map: {res.info['map']}. Clusters: {res.info['clustering']}."
        )),
    ]
    has_groups = "tech_group" in topics and topics["tech_group"].astype(bool).any()
    table = pd.DataFrame({
        "#": topics["id"],
        "Topic cluster": topics["label"],
        "Patents": topics["patents"],
        "Share": (topics["patents"] / max(n, 1) * 100).round(1).astype(str) + " %",
        "Off-topic (left out)": (topics["all_patents"] - topics["patents"]) if n_off else "",
        "Technology group": topics.get("tech_group", ""),
        "Relevance": topics.get("relevance", ""),
        "Key terms": topics["keywords"].map(lambda k: ", ".join(k[:6])),
        "What these patents do": topics.get("description", ""),
    })
    table = table[[c for c in table.columns if table[c].astype(str).str.strip().astype(bool).any()]]
    caption = res.info["naming"] + (f" Patents and share: the {n:,} analysed patents, as in the rest of the "
                                    "report; the clusters were found on all patents in the export." if n_off else "")
    blocks.append(rb.table(table, title="Topic clusters", caption=caption))
    if has_groups:
        tree = topics[topics["patents"] > 0].assign(tech_group=lambda t: t["tech_group"].replace("", "Unassigned"))
        fig2 = px.treemap(tree, path=[px.Constant("All patents"), "tech_group", "label"], values="patents",
                          color="tech_group", color_discrete_sequence=px.colors.qualitative.Set2, height=520)
        fig2.update_traces(root_color="#f4f4f4", textinfo="label+value+percent root")
        fig2.update_layout(margin={"l": 5, "r": 5, "t": 40, "b": 5}, title="Topic clusters by technology group")
        blocks.append(rb.chart(fig2, title="Topic hierarchy",
                               caption="Box size is the number of patents. Groups come from your plan; the model "
                                       "assigned each cluster to one."))
    return blocks


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


BULKY_TEXT_CHARS = 10_000
KEY_COLUMNS = ["Patent ID", "Title", "Year", "Assignee (normalised)", "Topic", "Segment", "Technology group",
               "Complexity", "Performance target", "Design lever", "Relevance"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "patent_landscape"


def table_blocks(result: LandscapeResult, options: PipelineOptions | None = None) -> list[rb.Block]:
    options = options or PipelineOptions()
    table = result.enriched
    blocks = [rb.heading("Patent table and downloads")]
    res = result.extraction
    if res is not None:
        rel = res.table["Relevance"].value_counts()
        read = res.stats["extracted"] + res.stats["from cache"]
        blocks.append(rb.metrics([
            ("Read by the model", f"{read:,} of {res.stats['patents']:,}"),
            ("High relevance", f"{int(rel.get('High', 0)):,}"),
            ("Partial", f"{int(rel.get('Partial', 0)):,}"),
            ("Low (off-topic)", f"{int(rel.get('Low', 0)):,}"),
        ]))
    problem_col = next((f.column for f in res.fields if f.key == "problem"), None) if res is not None else None
    key = [c for c in KEY_COLUMNS[:6] + ([problem_col] if problem_col else []) + KEY_COLUMNS[6:] if c in table]
    caption = (f"Key columns shown; the CSV below has all {len(table.columns)} columns, including Component, "
               "Mechanism, Core claim, Key parameters, Trade-offs, Confidence, the topic clusters and every column "
               "of your export." if res is not None else "Patent details were not extracted in this run.")
    blocks.append(rb.table(table[key], title="Every patent with the study's columns", caption=caption))
    slug = _slug(result.plan.title)
    # Full-text columns such as a description (up to 32,767 characters per cell) would make the file
    # several times larger without helping the analysis; they stay in the user's own export.
    bulky = [c for c in table.columns
             if table[c].dtype == object or pd.api.types.is_string_dtype(table[c])
             if table[c].astype(str).str.len().mean() > BULKY_TEXT_CHARS]
    csv_table = table.drop(columns=bulky)
    note = (f" Left out to keep the file small: {', '.join(bulky)} (over {BULKY_TEXT_CHARS:,} characters per "
            "patent on average); they stay in your export, matched by Patent ID." if bulky else "")
    blocks.append(rb.download(
        "Download the enriched patent table (CSV)", csv_table.to_csv(index=False).encode("utf-8-sig"),
        f"{slug}_patents.csv",
        caption=f"{len(csv_table):,} patents × {len(csv_table.columns)} columns. Opens directly in Excel.{note}",
    ))
    if res is not None:
        checked = [f.column for f in res.fields if f.key in ("topic", "segment", "problem", "tech_group", "complexity",
                                                              "design_lever", "core_claim", "relevance", "confidence")]
        sample = nar.validation_sample(table.assign(Abstract=result.corpus.std["abstract"].values), checked,
                                       n=options.validation_size, seed=options.seed)
        if not sample.empty:
            blocks.append(rb.download(
                "Download a validation sample (CSV)", sample.to_csv(index=False).encode("utf-8-sig"),
                f"{slug}_validation_sample.csv",
                caption=f"{len(sample)} random patents with the model's answers and empty Correct? / Comment columns. "
                        "Have an expert mark them to measure the extraction accuracy, and quote it with the "
                        "findings.",
            ))
    return blocks


def html_download(result: LandscapeResult, blocks: list[rb.Block]) -> rb.Block:
    frame = result.analysis_frame if result.analysis_frame is not None else result.corpus.std
    years = frame["year"].dropna()
    subtitle = (f"{len(frame):,} patents" + (f" · {int(years.min())}–{int(years.max())}" if len(years) else "")
                + f" · {result.dataset_name or 'uploaded export'} · generated "
                + datetime.fromisoformat(result.started_at).strftime("%d %b %Y")
                + (f" with {result.model_name}" if result.model_name else ""))
    if result.status == "cancelled":
        subtitle += " · stopped before the end: partial results"
    page = report_html(blocks, f"Patent landscape: {result.plan.title}", subtitle)
    return rb.download("Download the full report (HTML)", page, f"{_slug(result.plan.title)}_report.html",
                       mime="text/html", caption="One file with every chart (interactive), table and text; opens in "
                                                 "any browser, also offline. Print it to PDF from the browser if needed.")
