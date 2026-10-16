"""Patent landscape page: upload an export, paste a brief, review the plan, get the landscape.

Everything the analysis produces is appended to one long page; follow-up requests add further
sections below it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

import pandas as pd
import streamlit as st

from analyst_agent import LLMClient, LLMConfig
from analyst_agent.data import SUPPORTED_EXTENSIONS, DataLoadError, list_sheets, load_dataset
from analyst_agent.patents import enrich, semantic
from analyst_agent.patents import report as rb
from analyst_agent.patents.data import ROLE_LABELS, build_corpus, detect_columns
from analyst_agent.patents.pipeline import LandscapeResult, PipelineOptions, run_landscape
from analyst_agent.patents.plan import (
    AnalysisPlan,
    categories_from_text,
    categories_to_text,
    list_from_text,
    parse_brief,
)
from ui import jobs
from ui.common import remember

JOB = "pl_job"
MAPPED_ROLES = ["id", "title", "abstract", "claims", "assignee", "priority_date", "filing_date", "pub_date",
                "cpc", "ipc", "family", "country", "problem", "method", "advantage", "cited_by"]
STANDARD_COLUMNS = ("Topic, Segment, Problem, Technology group, Complexity, Component, Performance target, "
                    "Design lever, Mechanism, Core claim, Key parameters, Trade-offs, Relevance, Confidence")


@dataclass
class PlanReady:
    """Result of the "prepare plan" job."""

    plan: AnalysisPlan


def init_state() -> None:
    defaults = {"pl_raw": None, "pl_name": "", "pl_sha": "", "pl_mapping": {}, "pl_plan": None,
                "pl_plan_version": 0, "pl_outputs": [], "pl_error": None}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _sink(job: jobs.Job) -> None:
    out = job.run
    if isinstance(out, PlanReady):
        st.session_state.pl_plan = out.plan
        st.session_state.pl_plan_version += 1
    elif out is None:
        st.session_state.pl_error = job.error or "The job failed."
    else:
        st.session_state.pl_outputs.append(out)


jobs.JOB_SINKS[JOB] = _sink


def describe_event(kind: str, payload) -> tuple[str | None, tuple[str, str] | None]:
    if kind == "phase":
        return f"{payload}…", ("caption", f"▸ {payload}")
    if kind == "note":
        return None, ("caption", str(payload))
    return None, None


# --------------------------------------------------------------------------- step 1: the export


@st.cache_data(show_spinner="Reading the export…", max_entries=4)
def _load(raw: bytes, name: str, sheet: str | None) -> pd.DataFrame:
    return load_dataset(raw, name, sheet=sheet).df


def upload_step(busy: bool) -> None:
    st.subheader("1 · Patent export")
    uploaded = st.file_uploader(
        "Upload the export from your patent database (Excel or CSV)",
        type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS], disabled=busy, key="pl_upload",
        help="Any database export works (Derwent, PatSnap, Orbit, Lens, Espacenet, ...). Columns are recognised "
             "automatically and you can correct the mapping in the plan.",
    )
    if uploaded is not None and not busy:
        raw = uploaded.getvalue()
        sheets = []
        try:
            sheets = list_sheets(raw, uploaded.name)
        except DataLoadError as exc:
            st.error(str(exc))
        sheet = st.selectbox("Sheet", sheets, key="pl_sheet") if len(sheets) > 1 else (sheets[0] if sheets else None)
        sha = hashlib.sha256(raw).hexdigest() + f":{sheet}"
        if sha != st.session_state.pl_sha:
            try:
                df = _load(raw, uploaded.name, sheet)
            except DataLoadError as exc:
                st.error(f"Could not read the file: {exc}")
                return
            had_results = bool(st.session_state.pl_outputs)
            st.session_state.update(pl_raw=df, pl_name=uploaded.name, pl_sha=sha,
                                    pl_mapping=detect_columns(list(df.columns)), pl_outputs=[])
            if had_results:
                st.toast("New export loaded: earlier results were cleared.", icon="🔄")
    raw_df: pd.DataFrame | None = st.session_state.pl_raw
    if raw_df is not None:
        mapping = st.session_state.pl_mapping
        found = [ROLE_LABELS[r] for r in MAPPED_ROLES if mapping.get(r)]
        cols = st.columns([5, 1])
        cols[0].caption(f"📄 **{st.session_state.pl_name}** · {len(raw_df):,} records · recognised: " + ", ".join(found))
        if cols[1].button("Remove", disabled=busy, width="stretch"):
            st.session_state.update(pl_raw=None, pl_name="", pl_sha="", pl_mapping={}, pl_outputs=[])
            st.rerun()


# --------------------------------------------------------------------------- step 2: the brief


def brief_step(busy: bool, llm_cfg: LLMConfig) -> None:
    st.subheader("2 · Analysis brief")
    brief = st.text_area(
        "Paste your complete brief (role, scope, problem, timeframe, analysis dimensions, required charts, "
        "questions, output format)", height=240, key=remember("pl_brief", ""),
        placeholder="e.g. Conduct a deep-research-grade patent landscape on uneven wear in TBR and PCR tires, "
                    "2014–2024 …",
    )
    # Not disabled while the box is empty: pasted text only reaches the app when the box loses focus,
    # which happens on this very click, so a disabled button would swallow the first click.
    if st.button("🧭 Prepare analysis plan", type="primary", disabled=busy):
        text = brief.strip()
        if not text:
            st.warning("Paste your brief first.")
            return

        def work(on_event, should_stop):
            on_event("phase", "Reading your brief")
            return PlanReady(parse_brief(LLMClient(llm_cfg), text))

        st.session_state[JOB] = jobs.start_job("Prepare the analysis plan", "Patent landscape", work, describe_event)
        st.rerun()


# --------------------------------------------------------------------------- step 3: the plan


@st.cache_data(show_spinner=False, max_entries=16)
def _preview(sha: str, mapping_items: tuple, years: tuple | None, _raw: pd.DataFrame):
    corpus = build_corpus(_raw, dict(mapping_items), years=years)
    return corpus.flow, len(corpus), corpus.year_basis, corpus.notes


def plan_step(busy: bool, llm_cfg: LLMConfig) -> None:
    plan: AnalysisPlan = st.session_state.pl_plan
    raw_df: pd.DataFrame | None = st.session_state.pl_raw
    v = st.session_state.pl_plan_version
    st.subheader("3 · Analysis plan")
    if plan.source == "fallback":
        st.warning("The model did not prepare this plan, so it was set up from the brief's text by rules. "
                   "Review the categories carefully.", icon="⚠️")
    else:
        st.caption("Prepared by the model from your brief. Edit anything, then run.")
    for item in plan.not_possible:
        st.info(item, icon="ℹ️")

    with st.form(f"plan_form_{v}"):
        c1, c2 = st.columns([3, 2])
        title = c1.text_input("Title", plan.title)
        segments = c2.text_input("Segments", ", ".join(plan.segments), help="Comma-separated, e.g. TBR, PCR")
        objective = st.text_area("Objective", plan.objective, height=70)
        y1, y2, y3 = st.columns(3)
        lo = y1.number_input("From year (0 = no limit)", 0, 2100, plan.years[0] if plan.years else 0)
        hi = y2.number_input("To year (0 = no limit)", 0, 2100, plan.years[1] if plan.years else 0)
        problem_label = y3.text_input("Problem dimension is called", plan.problem_label)
        st.markdown("**Categories** (one per line, `Name: description`)")
        a, b, c = st.columns(3)
        problems = a.text_area(problem_label or "Problems", categories_to_text(plan.problems), height=200)
        buckets = b.text_area("Technology groups", categories_to_text(plan.tech_buckets), height=200)
        complexity = c.text_area("Complexity levels (simple → complex)", categories_to_text(plan.complexity_levels),
                                 height=200)
        in_scope = st.text_area("Relevant patents are…", plan.in_scope, height=70,
                                help="Used to mark each patent as in or out of scope.")
        d, e = st.columns(2)
        competitors = d.text_input("Competitors to watch", ", ".join(plan.competitors))
        regions = e.text_input("Regions / patent offices to focus on (empty = all)", ", ".join(plan.regions))
        extra = st.text_area(f"Extra columns for the CSV, beyond the standard ones ({STANDARD_COLUMNS})",
                             categories_to_text(plan.extra_fields), height=100)
        sections = st.text_area("Report sections (one per line)", "\n".join(plan.report_sections), height=130)

        answers = {}
        if plan.questions:
            st.markdown("**Questions from your brief**")
            for i, q in enumerate(plan.questions):
                answers[q] = st.text_input(q, plan.answers.get(q, ""), key=f"pl_q_{v}_{i}")

        mapping = dict(st.session_state.pl_mapping)
        if raw_df is not None:
            with st.expander("Column mapping (detected automatically)"):
                options = ["(none)"] + list(raw_df.columns)
                cols = st.columns(3)
                for i, role in enumerate(MAPPED_ROLES):
                    current = mapping.get(role)
                    choice = cols[i % 3].selectbox(ROLE_LABELS[role], options,
                                                   index=options.index(current) if current in options else 0,
                                                   key=f"pl_map_{v}_{role}")
                    mapping[role] = None if choice == "(none)" else choice
        sample = st.number_input("Trial run: analyse a random sample of N patents (0 = all)", 0, 100000, 0, 50,
                                 help="With a local model, try 50–100 patents first to check the plan.")
        with st.expander("Advanced settings"):
            st.checkbox("Read every patent with the model (fills the study's columns for the CSV)",
                        key=remember("pl_extract", True))
            st.checkbox("Build the patent map and topic clusters", key=remember("pl_semantic", True))
            st.text_input(
                "Text embedding model", key=remember("pl_embed_model", semantic.DEFAULT_MODEL),
                help="PatentSBERTa needs `pip install sentence-transformers` and downloads once (about 0.5 GB); "
                     "its embeddings are cached on disk. Without it, or if you type tfidf, TF-IDF + LSA is used.",
            )
        s1, s2 = st.columns(2)
        save = s1.form_submit_button("💾 Save plan", width="stretch", disabled=busy)
        run = s2.form_submit_button("▶ Save & run analysis", type="primary", width="stretch",
                                    disabled=busy or raw_df is None)

    if save or run:
        plan = replace(
            plan, title=title.strip() or plan.title, objective=objective.strip(), segments=list_from_text(segments),
            years=(int(min(lo, hi)), int(max(lo, hi))) if lo and hi else None,
            problem_label=problem_label.strip() or "Problem", problems=categories_from_text(problems),
            tech_buckets=categories_from_text(buckets) or plan.tech_buckets,
            complexity_levels=categories_from_text(complexity) or plan.complexity_levels,
            in_scope=in_scope.strip(), competitors=list_from_text(competitors), regions=list_from_text(regions),
            extra_fields=categories_from_text(extra),
            report_sections=[s.strip() for s in sections.splitlines() if s.strip()] or plan.report_sections,
            answers={q: a for q, a in answers.items() if a.strip()},
        )
        st.session_state.pl_plan = plan
        st.session_state.pl_mapping = mapping
        if run:
            start_run(plan, mapping, int(sample), llm_cfg)

    if raw_df is not None:
        m = st.session_state.pl_mapping
        try:
            flow, n, basis, notes = _preview(st.session_state.pl_sha, tuple(sorted((k, v) for k, v in m.items() if v)),
                                             plan.years, raw_df)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.caption(f"With this plan, **{n:,} patents** will be analysed (of {flow[0].remaining:,} in the export; "
                   f"year basis: {basis}).")
        if st.session_state.get("pl_extract", True) and n:
            size = enrich.batch_size_for(llm_cfg.context_tokens)
            st.caption(f"The model reads them in about {-(-n // size):,} requests of {size} patents (context window "
                       f"{llm_cfg.context_tokens:,} tokens). Answers are cached, so a stopped run continues where it "
                       "left off. For a first look, try a trial run of 50–100 patents.")
        for note in notes:
            st.caption(f"ℹ️ {note}")


def start_run(plan: AnalysisPlan, mapping: dict, sample: int, llm_cfg: LLMConfig) -> None:
    raw_df = st.session_state.pl_raw
    name, sha = st.session_state.pl_name, st.session_state.pl_sha

    options = PipelineOptions(
        sample_limit=sample, semantic_map=bool(st.session_state.get("pl_semantic", True)),
        extract_details=bool(st.session_state.get("pl_extract", True)),
        embedding_model=(st.session_state.get("pl_embed_model") or semantic.DEFAULT_MODEL).strip(),
    )

    def work(on_event, should_stop):
        return run_landscape(raw_df, mapping, plan, llm=LLMClient(llm_cfg), options=options, on_event=on_event,
                             should_stop=should_stop, dataset_name=name, dataset_sha256=sha.split(":")[0])

    st.session_state[JOB] = jobs.start_job(f"Landscape: {plan.title}", "Patent landscape", work, describe_event)
    st.rerun()


# --------------------------------------------------------------------------- results


def render_blocks(blocks: list[rb.Block], key: str) -> None:
    for i, block in enumerate(blocks):
        if block.kind == "heading":
            st.markdown(f"{'#' * max(1, min(block.level, 4))} {block.title}")
            continue
        if block.title and block.kind not in ("plotly", "download"):
            st.markdown(f"**{block.title}**")
        if block.kind == "markdown":
            st.markdown(block.text)
        elif block.kind == "note":
            st.info(block.text, icon="ℹ️")
        elif block.kind == "metrics":
            cols = st.columns(max(1, len(block.metrics)))
            for col, (label, value) in zip(cols, block.metrics):
                col.metric(label, value)
        elif block.kind == "table" and block.table is not None:
            if len(block.table) <= 30:  # report-style table: real text, readable and printable
                st.table(block.table.style.hide(axis="index").format(precision=2, na_rep="", thousands=","))
            else:  # long tables: scrollable, sortable grid
                st.dataframe(block.table, width="stretch", hide_index=True)
        elif block.kind == "plotly" and block.figure is not None:
            st.plotly_chart(block.figure, width="stretch", key=f"{key}-fig-{i}")
        elif block.kind == "download" and block.data is not None:
            st.download_button(block.title, block.data, file_name=block.file_name, mime=block.mime, type="primary",
                               icon=":material/download:", key=f"{key}-dl-{i}", on_click="ignore")
        if block.caption:
            st.caption(block.caption)


def render_result(result: LandscapeResult, idx: int) -> None:
    if result.status == "cancelled":
        st.info("The analysis was stopped at your request; what was finished is shown.", icon="⏹")
    for w in result.warnings:
        st.caption(f"ℹ️ {w}")
    render_blocks(result.blocks, key=f"pl-{idx}")


def page() -> None:
    init_state()
    llm_cfg: LLMConfig = st.session_state.llm_cfg
    busy = jobs.is_running(JOB)
    st.title("🔬 Patent Landscape")
    st.caption("Upload a patent export, paste your analysis brief, review the plan the model prepares, and run. "
               "Results appear below as one long report; ask for changes in the box at the end.")

    if st.session_state.pl_error:
        st.error(f"The last job failed: {st.session_state.pl_error}")
        st.session_state.pl_error = None
    upload_step(busy)
    brief_step(busy, llm_cfg)
    if st.session_state.pl_plan is not None:
        plan_step(busy, llm_cfg)

    outputs = st.session_state.pl_outputs
    if outputs or jobs.active(JOB) is not None:
        st.divider()
    for idx, out in enumerate(outputs):
        if isinstance(out, LandscapeResult):
            render_result(out, idx)
    if jobs.active(JOB) is not None:
        jobs.job_panel(JOB)
