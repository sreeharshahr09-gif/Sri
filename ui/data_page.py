"""Data analysis page: upload a dataset and ask questions about it."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pandas as pd
import streamlit as st

from analyst_agent import (
    AgentConfig,
    AgentRun,
    DataAnalystAgent,
    LLMClient,
    LLMConfig,
    Sandbox,
    SandboxConfig,
)
from analyst_agent.agent import build_system_prompt
from analyst_agent.data import (
    SUPPORTED_EXTENSIONS,
    DataLoadError,
    Dataset,
    column_summary,
    describe_for_llm,
    list_sheets,
    load_dataset,
)
from analyst_agent.export import to_json, to_markdown, to_notebook
from ui.common import render_artifact, render_execution

STARTER_QUESTIONS = [
    "Give me an overview of this dataset and any data-quality issues.",
    "Which numeric variables are most strongly related to each other?",
    "Show the distributions of the key numeric columns and flag outliers.",
]

DEFAULT_AGENT = AgentConfig()
DEFAULT_SANDBOX = SandboxConfig()


# --------------------------------------------------------------------------- state


def init_state() -> None:
    defaults = {
        "runs": [],  # list[AgentRun]: the conversation
        "dataset": None,  # Dataset
        "dataset_key": None,
        "profile": None,
        "pending_question": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if "sandbox" not in st.session_state:
        st.session_state.sandbox = Sandbox(SandboxConfig())


@st.cache_data(show_spinner="Reading file…", max_entries=8)
def cached_load(raw: bytes, name: str, sheet: str | None) -> Dataset:
    return load_dataset(raw, name, sheet=sheet)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_sheets(raw: bytes, name: str) -> list[str]:
    return list_sheets(raw, name)


@st.cache_data(show_spinner="Profiling columns…", max_entries=8)
def cached_summary(key: str, _df: pd.DataFrame) -> pd.DataFrame:
    return column_summary(_df)


@st.cache_data(show_spinner="Profiling dataset…", max_entries=8)
def cached_profile(key: str, _dataset: Dataset, sample_rows: int) -> str:
    return describe_for_llm(_dataset, sample_rows=sample_rows)


# --------------------------------------------------------------------------- sidebar


def sidebar() -> tuple[LLMConfig, AgentConfig, SandboxConfig]:
    llm_cfg: LLMConfig = st.session_state.llm_cfg
    st.sidebar.header("📁 Data")
    uploaded = st.sidebar.file_uploader(
        "Upload a dataset",
        type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        help="CSV/TSV (delimiter and encoding auto-detected), Excel, Parquet or JSON.",
    )
    if uploaded is not None:
        handle_upload(uploaded)
    else:
        if st.session_state.dataset is not None:  # the file was removed: start over
            st.session_state.update(dataset=None, dataset_key=None, runs=[], profile=None)
        st.sidebar.info("Upload a file to get started.")

    with st.sidebar.expander("⚙️ Agent", expanded=False):
        max_steps = st.slider("Max reasoning steps", 1, 15, DEFAULT_AGENT.max_steps,
                              help="Code executions allowed before the agent must answer.")
        history_turns = st.slider("Conversation memory (turns)", 0, 10, DEFAULT_AGENT.history_turns)
        code_timeout = st.number_input("Code timeout (s)", 5, 1800, int(DEFAULT_SANDBOX.timeout_seconds), 5)
        memory_mb = st.number_input("Code memory limit (MB, 0 = none)", 0, 65536,
                                    DEFAULT_SANDBOX.memory_limit_mb, 512,
                                    help="Enforced on Linux/macOS only.")
    agent_cfg = replace(DEFAULT_AGENT, max_steps=int(max_steps), history_turns=int(history_turns))
    sandbox_cfg = replace(DEFAULT_SANDBOX, timeout_seconds=float(code_timeout), memory_limit_mb=int(memory_mb))

    return llm_cfg, agent_cfg, sandbox_cfg


def sidebar_session(dataset: Dataset, llm_cfg: LLMConfig, agent_cfg: AgentConfig,
                    sandbox_cfg: SandboxConfig) -> None:
    runs: list[AgentRun] = st.session_state.runs
    st.sidebar.header("🗂️ Session")
    if st.sidebar.button("New conversation", width="stretch", disabled=not runs):
        st.session_state.runs = []
        st.rerun()
    if runs:
        settings = {"llm": llm_cfg.__dict__ | {"api_key": "***" if llm_cfg.api_key else ""},
                    "agent": agent_cfg.__dict__, "sandbox": sandbox_cfg.__dict__}
        prompt = build_system_prompt(len(dataset.df), st.session_state.sandbox.allowed_modules,
                                     st.session_state.profile)
        stem = dataset.name.rsplit(".", 1)[0]
        st.sidebar.download_button(
            "⬇️ Reproducible notebook (.ipynb)", to_notebook(runs, dataset),
            file_name=f"{stem}_analysis.ipynb", mime="application/x-ipynb+json", width="stretch",
        )
        st.sidebar.download_button(
            "⬇️ Report (.md)", to_markdown(runs, dataset),
            file_name=f"{stem}_report.md", mime="text/markdown", width="stretch",
        )
        st.sidebar.download_button(
            "⬇️ Full transcript (.json)",
            to_json(runs, dataset, settings=settings, system_prompt=prompt),
            file_name=f"{stem}_transcript.json", mime="application/json", width="stretch",
        )


def handle_upload(uploaded) -> None:
    raw = uploaded.getvalue()
    sheet = None
    try:
        sheets = cached_sheets(raw, uploaded.name)
    except DataLoadError as exc:
        st.sidebar.error(str(exc))
        return
    if len(sheets) > 1:
        sheet = st.sidebar.selectbox("Sheet", sheets)
    elif sheets:
        sheet = sheets[0]

    key = f"{hashlib.sha256(raw).hexdigest()}:{sheet}"
    if key == st.session_state.dataset_key:
        return
    try:
        dataset = cached_load(raw, uploaded.name, sheet)
    except DataLoadError as exc:
        st.sidebar.error(f"❌ {exc}")
        return
    had_runs = bool(st.session_state.runs)
    st.session_state.update(dataset=dataset, dataset_key=key, runs=[], profile=None)
    if had_runs:
        st.toast("New dataset loaded: the conversation was reset.", icon="🔄")


# --------------------------------------------------------------------------- rendering


def render_step(step, run_idx: int) -> None:
    title = f"**Step {step.index + 1}**"
    if step.result is not None:
        r = step.result
        if r.ok:
            title += f" · ✅ ran in {r.duration_s:.1f}s"
        elif r.rejected:
            title += " · 🚫 rejected by policy"
        else:
            title += f" · ❌ {r.error_type}"
    elif step.note == "repeated":
        title += " · ↩️ duplicate code skipped"
    elif step.note == "truncated":
        title += " · ✂️ reply truncated"
    elif step.note == "premature answer":
        title += " · ↩️ answered without evidence, sent back to verify"
    elif step.code is None:
        title += " · 📝 answer"
    if step.llm_latency_s:
        title += f" · model {step.llm_latency_s:.1f}s"
    st.markdown(title)
    if step.thought:
        st.markdown(step.thought)
    if step.code:
        st.code(step.code, language="python")
    if step.result is not None:
        render_execution(step.result, key=f"trace-{run_idx}-{step.index}")


def render_run(run: AgentRun, run_idx: int) -> None:
    if run.status == "llm_error":
        st.error(f"Model error: {run.error}")
    elif run.status == "error":
        st.error(f"The analysis stopped unexpectedly: {run.error}")
    if run.answer:
        st.markdown(run.answer)

    if run.answer and not run.executed:
        st.warning("This answer is not backed by any executed code. Treat it as unverified.", icon="⚠️")
    elif run.status == "step_limit":
        st.warning("The step limit was reached before the agent finished; the answer may be incomplete.",
                   icon="⏱️")
    g = run.grounding
    if g and g.checked and run.executed:
        if g.fully_traced:
            st.caption(f"🔎 All {len(g.checked)} numbers in the answer trace back to executed output.")
        else:
            shown = ", ".join(f"`{u}`" for u in g.untraced[:8])
            st.caption(
                f"🔎 {g.traced_count}/{len(g.checked)} numbers trace back to executed output. "
                f"Could not trace: {shown}. These may be derived or rounded; check them against the trace."
            )

    for i, art in enumerate(run.report_artifacts()):
        render_artifact(art, key=f"report-{run_idx}-{i}")

    if run.steps:
        n_code = sum(1 for s in run.steps if s.code)
        label = (f"🔬 Analysis trace · {n_code} code step{'s' * (n_code != 1)} · "
                 f"{run.duration_s:.1f}s total")
        if run.total_tokens:
            label += f" · {run.total_tokens:,} tokens"
        with st.expander(label):
            for step in run.steps:
                render_step(step, run_idx)
                st.divider()


def render_data_tab(dataset: Dataset, profile: str) -> None:
    df = dataset.df
    total_cells = df.size or 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", f"{len(df):,}")
    c2.metric("Columns", f"{df.shape[1]:,}")
    c3.metric("Missing cells", f"{100 * df.isna().sum().sum() / total_cells:.1f}%")
    try:
        dupes = int(df.duplicated().sum())
    except TypeError:
        dupes = 0
    c4.metric("Duplicate rows", f"{dupes:,}")
    for note in dataset.notes:
        st.caption(f"ℹ️ {note}")

    st.subheader("Columns")
    st.dataframe(
        cached_summary(st.session_state.dataset_key, df),
        width="stretch",
        hide_index=True,
        column_config={
            "null_pct": st.column_config.ProgressColumn("missing %", min_value=0, max_value=100, format="%.1f%%"),
        },
    )
    st.subheader("Preview")
    st.dataframe(df.head(200), width="stretch")
    with st.expander("What the model sees (dataset profile)"):
        st.code(profile, language=None)


# --------------------------------------------------------------------------- main


def run_question(question: str, llm_cfg: LLMConfig, agent_cfg: AgentConfig, sandbox_cfg: SandboxConfig) -> None:
    dataset: Dataset = st.session_state.dataset
    sandbox: Sandbox = st.session_state.sandbox
    sandbox.config = sandbox_cfg
    agent = DataAnalystAgent(LLMClient(llm_cfg), sandbox, dataset, config=agent_cfg,
                             profile=st.session_state.profile)

    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.status("Analyzing…", expanded=True)

        def on_event(kind: str, payload) -> None:
            if kind == "llm_call":
                status.update(label=f"Thinking (step {payload + 1})…")
            elif kind == "executing":
                status.update(label=f"Running code (step {payload.index + 1})…")
                status.code(payload.code, language="python")
            elif kind == "step":
                r = payload.result
                if r is None:
                    status.caption(f"Step {payload.index + 1}: {payload.note}")
                elif r.ok:
                    status.caption(f"✅ Step {payload.index + 1} succeeded in {r.duration_s:.1f}s")
                else:
                    status.caption(f"❌ Step {payload.index + 1}: {r.error_type}: {(r.error_message or '')[:200]}")

        run = agent.run(question, history=st.session_state.runs, on_event=on_event)
        state = "error" if run.status in ("llm_error", "error") else "complete"
        status.update(label=f"Done in {run.duration_s:.1f}s", state=state, expanded=False)
    st.session_state.runs.append(run)
    # Re-render from state so the sidebar exports and history include this run.
    st.rerun()


def page() -> None:
    init_state()
    llm_cfg, agent_cfg, sandbox_cfg = sidebar()

    st.title("📊 Data Analyst Agent")
    dataset: Dataset | None = st.session_state.dataset
    if dataset is None:
        st.markdown(
            "Upload a dataset in the sidebar, then ask questions in plain language. The agent "
            "writes and runs Python against your data in a sandbox, checks its own results, and "
            "reports answers with the evidence and code behind them."
        )
        st.chat_input("Upload a dataset first", disabled=True)
        return

    if st.session_state.profile is None:
        st.session_state.profile = cached_profile(st.session_state.dataset_key, dataset, agent_cfg.sample_rows)
    sidebar_session(dataset, llm_cfg, agent_cfg, sandbox_cfg)
    st.caption(f"**{dataset.name}**" + (f" · sheet {dataset.sheet}" if dataset.sheet else "")
               + f" · {dataset.shape_text}")

    chat_tab, data_tab = st.tabs(["💬 Analysis", "🗂️ Data"])
    with data_tab:
        render_data_tab(dataset, st.session_state.profile)

    with chat_tab:
        runs: list[AgentRun] = st.session_state.runs
        for idx, run in enumerate(runs):
            with st.chat_message("user"):
                st.markdown(run.question)
            with st.chat_message("assistant"):
                render_run(run, idx)

        if not runs:
            st.markdown("**Try asking:**")
            cols = st.columns(len(STARTER_QUESTIONS))
            for col, q in zip(cols, STARTER_QUESTIONS):
                if col.button(q, width="stretch"):
                    st.session_state.pending_question = q

        question = st.chat_input("Ask a question about your data")
        question = question or st.session_state.pending_question
        st.session_state.pending_question = None
        if question and question.strip():
            run_question(question.strip(), llm_cfg, agent_cfg, sandbox_cfg)
