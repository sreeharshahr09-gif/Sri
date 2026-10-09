"""Workspace page: point the assistant at a local folder and ask it to explain what is there.

Phase 1 is strictly read-only: the assistant can list, search and read files (and run
analysis code that reads them), but nothing on this page can create, change or delete a file.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import PurePosixPath

import streamlit as st

from analyst_agent import AgentConfig, AgentRun, LLMClient, LLMConfig, Sandbox, SandboxConfig
from analyst_agent.export import describe_tool_step, workspace_to_json, workspace_to_markdown
from analyst_agent.workspace import Workspace, WorkspaceError, is_local_url
from analyst_agent.workspace_agent import WorkspaceAgent
from ui.common import render_artifact, render_execution

STARTER_QUESTIONS = [
    "Give me an overview of this folder: what is in it and how is it organised?",
    "Which are the most important files here, and what does each one do?",
    "Explain the main script step by step.",
]
MODES = {"Answer": "answer", "Teach me": "teach"}
LANGUAGES = {
    ".py": "python", ".m": "matlab", ".r": "r", ".jl": "julia", ".js": "javascript", ".ts": "typescript",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".java": "java", ".sh": "bash", ".sql": "sql", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml", ".html": "html",
    ".tex": "latex",
}
DEFAULT_SANDBOX = SandboxConfig()
ICONS = {"read_file": "📄", "search": "🔍", "list_files": "🗂️"}


def init_state() -> None:
    for key, value in {"ws": None, "ws_runs": [], "ws_sandbox": None, "ws_pending": None}.items():
        st.session_state.setdefault(key, value)


def open_workspace(path: str) -> None:
    try:
        ws = Workspace(path)
    except WorkspaceError as exc:
        st.sidebar.error(str(exc))
        return
    old = st.session_state.ws_sandbox
    if old is not None:
        old.close()
    st.session_state.update(
        ws=ws,
        ws_runs=[],
        ws_sandbox=Sandbox(SandboxConfig(), workspace_root=str(ws.root)),
    )


# --------------------------------------------------------------------------- sidebar


def sidebar(llm_cfg: LLMConfig) -> tuple[AgentConfig, bool]:
    st.sidebar.header("📁 Workspace")
    path = st.sidebar.text_input(
        "Folder path",
        value=os.environ.get("WORKSPACE_DIR", ""),
        placeholder="e.g. C:\\Users\\me\\tyre-study or /home/me/project",
        key="ws_path_input",
        help="A folder on the machine running this app. The assistant can only read inside it.",
    )
    cols = st.sidebar.columns(2)
    if cols[0].button("Open", width="stretch", type="primary", disabled=not path.strip()):
        open_workspace(path.strip())
    if cols[1].button("Close", width="stretch", disabled=st.session_state.ws is None):
        if st.session_state.ws_sandbox is not None:
            st.session_state.ws_sandbox.close()
        st.session_state.update(ws=None, ws_runs=[], ws_sandbox=None)
        st.rerun()

    ws: Workspace | None = st.session_state.ws
    if ws is not None:
        st.sidebar.caption(f"🔒 Read-only access to `{ws.root}`")
    if not is_local_url(llm_cfg.base_url):
        st.sidebar.warning(
            f"The model server is not local ({llm_cfg.base_url}). File contents the assistant reads "
            "will be sent to it.",
            icon="🌐",
        )

    with st.sidebar.expander("⚙️ Assistant", expanded=False):
        max_steps = st.slider("Max steps per question", 2, 30, 12, key="ws_max_steps",
                              help="Tool calls (search, read, …) allowed before it must answer.")
        history_turns = st.slider("Conversation memory (turns)", 0, 10, 4, key="ws_history")
        allow_python = st.checkbox("Allow Python analysis", value=True, key="ws_python",
                                   help="Lets the assistant run sandboxed code that reads (never writes) "
                                        "files in the folder, e.g. to analyse a data file.")
    cfg = replace(AgentConfig(), max_steps=int(max_steps), history_turns=int(history_turns))

    runs: list[AgentRun] = st.session_state.ws_runs
    if ws is not None and runs:
        st.sidebar.header("🗂️ Session")
        if st.sidebar.button("New conversation", width="stretch", key="ws_new"):
            st.session_state.ws_runs = []
            st.rerun()
        settings = {"llm": llm_cfg.__dict__ | {"api_key": "***" if llm_cfg.api_key else ""}, "agent": cfg.__dict__}
        stem = ws.root.name or "workspace"
        st.sidebar.download_button("⬇️ Session notes (.md)", workspace_to_markdown(runs, str(ws.root)),
                                   file_name=f"{stem}_session.md", mime="text/markdown", width="stretch")
        st.sidebar.download_button("⬇️ Full transcript (.json)", workspace_to_json(runs, str(ws.root), settings),
                                   file_name=f"{stem}_transcript.json", mime="application/json", width="stretch")
    return cfg, bool(allow_python)


# --------------------------------------------------------------------------- rendering


def _numbered(lines: list[str], start: int, end: int) -> str:
    width = len(str(end))
    return "\n".join(f"{i:>{width}}| {lines[i - 1]}" for i in range(start, end + 1))


def render_sources(run: AgentRun, ws: Workspace | None, run_idx: int) -> None:
    report = run.citations
    if not report or not report.citations:
        if run.answer and run.executed:
            st.caption("📎 The answer cites no specific file lines.")
        return
    verified, unseen, invalid = (report.by_status(s) for s in ("verified", "unseen", "invalid"))
    summary = f"📎 {len(verified)} of {len(report.citations)} cited sources were opened and checked."
    st.caption(summary)
    if unseen:
        st.caption("❔ Cited but not opened while answering (may be from memory): "
                   + ", ".join(f"`{c.label}`" for c in unseen[:8]))
    if invalid:
        st.warning("These references do not exist: "
                   + "; ".join(f"`{c.label}` ({c.detail})" for c in invalid[:8]), icon="⚠️")
    if ws is None:
        return
    with st.expander(f"Sources ({len(verified) + len(unseen)})"):
        for c in (verified + unseen)[:12]:
            try:
                doc = ws.document(c.path)
            except WorkspaceError as exc:
                st.caption(f"`{c.label}`: {exc}")
                continue
            lo, hi = max(1, c.start - 2), min(len(doc.lines), c.end + 2)
            st.markdown(f"**`{c.label}`**" + ("" if c.status == "verified" else " _(not opened by the assistant)_"))
            st.code(_numbered(doc.lines, lo, hi), language=None)


def render_step(step, run_idx: int) -> None:
    title = f"**Step {step.index + 1}**"
    if step.tool:
        title += f" · {ICONS.get(step.tool, '🔧')} {describe_tool_step(step)}"
        if step.tool_error:
            title += " · ❌"
    elif step.result is not None:
        r = step.result
        title += " · 🐍 ran Python" + (f" ✅ {r.duration_s:.1f}s" if r.ok else f" ❌ {r.error_type}")
    elif step.note:
        title += f" · ↩️ {step.note}"
    else:
        title += " · 📝 answer"
    st.markdown(title)
    if step.thought:
        st.caption(step.thought[:500])
    if step.code:
        st.code(step.code, language="python")
    if step.observation:
        text = step.observation
        st.code(text[:4000] + ("\n…" if len(text) > 4000 else ""), language=None)
    if step.result is not None:
        render_execution(step.result, key=f"ws-trace-{run_idx}-{step.index}")


def render_run(run: AgentRun, run_idx: int) -> None:
    ws: Workspace | None = st.session_state.ws
    if run.status == "llm_error":
        st.error(f"Model error: {run.error}")
    elif run.status == "error":
        st.error(f"The assistant stopped unexpectedly: {run.error}")
    if run.answer:
        st.markdown(run.answer)
    if run.answer and not run.executed:
        st.warning("This answer is not based on any file the assistant opened. Treat it as unverified.", icon="⚠️")
    elif run.status == "step_limit":
        st.warning("The step limit was reached; the answer may be incomplete.", icon="⏱️")
    render_sources(run, ws, run_idx)
    for i, art in enumerate(run.report_artifacts()):
        render_artifact(art, key=f"ws-report-{run_idx}-{i}")
    if run.steps:
        n = sum(1 for s in run.steps if s.tool or s.code)
        label = f"🔬 How it worked · {n} step{'s' * (n != 1)} · {run.duration_s:.1f}s"
        if run.total_tokens:
            label += f" · {run.total_tokens:,} tokens"
        with st.expander(label):
            for step in run.steps:
                render_step(step, run_idx)
                st.divider()


@st.fragment
def files_tab(ws: Workspace) -> None:
    files = [ws.relative(p) for _, p in zip(range(5000), ws.iter_files())]
    if not files:
        st.info("This folder has no readable files.")
        return
    choice = st.selectbox("File", files, key="ws_file_choice")
    try:
        doc = ws.document(choice)
    except WorkspaceError as exc:
        st.error(str(exc))
        return
    st.caption(f"{doc.kind} · {len(doc.lines):,} lines" + (f" · {doc.note}" if doc.note else ""))
    shown = doc.lines[:3000]
    lang = LANGUAGES.get(PurePosixPath(choice).suffix.lower()) if doc.kind == "text" else None
    st.code("\n".join(shown), language=lang, line_numbers=True)
    if len(doc.lines) > len(shown):
        st.caption(f"Showing the first {len(shown):,} of {len(doc.lines):,} lines.")


# --------------------------------------------------------------------------- main


def run_question(question: str, mode: str, llm_cfg: LLMConfig, cfg: AgentConfig, allow_python: bool) -> None:
    ws: Workspace = st.session_state.ws
    sandbox: Sandbox | None = st.session_state.ws_sandbox if allow_python else None
    if sandbox is not None:
        sandbox.config = DEFAULT_SANDBOX
    agent = WorkspaceAgent(LLMClient(llm_cfg), ws, sandbox=sandbox, config=cfg, mode=mode)

    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.status("Reading the folder…", expanded=True)

        def on_event(kind: str, payload) -> None:
            if kind == "llm_call":
                status.update(label=f"Thinking (step {payload + 1})…")
            elif kind == "tool":
                status.update(label=f"{ICONS.get(payload.tool, '🔧')} {describe_tool_step(payload)}…")
            elif kind == "executing":
                status.update(label=f"🐍 Running Python (step {payload.index + 1})…")
            elif kind == "step":
                if payload.tool:
                    mark = "❌" if payload.tool_error else "✅"
                    status.caption(f"{mark} {describe_tool_step(payload)}")
                elif payload.result is not None:
                    status.caption("✅ Python ran" if payload.result.ok else f"❌ Python: {payload.result.error_type}")
                elif payload.note:
                    status.caption(f"↩️ {payload.note}")

        run = agent.run(question, history=st.session_state.ws_runs, on_event=on_event)
        state = "error" if run.status in ("llm_error", "error") else "complete"
        status.update(label=f"Done in {run.duration_s:.1f}s", state=state, expanded=False)
    st.session_state.ws_runs.append(run)
    st.rerun()


def page() -> None:
    init_state()
    llm_cfg: LLMConfig = st.session_state.llm_cfg
    cfg, allow_python = sidebar(llm_cfg)

    st.title("📁 Workspace Assistant")
    ws: Workspace | None = st.session_state.ws
    if ws is None:
        st.markdown(
            "Enter a folder path in the sidebar and press **Open**. The assistant can search and read "
            "the files in it (code, Word, PowerPoint, PDF, notebooks, spreadsheets) and explain them, "
            "citing the exact file and line for what it says.\n\n"
            "🔒 **Read-only:** nothing here can create, change or delete your files. Credentials files "
            "(such as `.env` and private keys) are never read."
        )
        st.chat_input("Open a folder first", disabled=True)
        return

    top = st.columns([3, 2])
    top[0].caption(f"**{ws.root.name or ws.root}** · `{ws.root}`")
    mode_label = top[1].radio("Mode", list(MODES), horizontal=True, key="ws_mode", label_visibility="collapsed",
                              help="Teach me: step-by-step explanations with terms defined and check questions.")
    mode = MODES[mode_label]

    ask_tab, files_tab_ = st.tabs(["💬 Ask", "🗂️ Files"])
    with files_tab_:
        files_tab(ws)
    with ask_tab:
        runs: list[AgentRun] = st.session_state.ws_runs
        for idx, run in enumerate(runs):
            with st.chat_message("user"):
                st.markdown(run.question + ("  \n_🎓 teach mode_" if run.mode == "teach" else ""))
            with st.chat_message("assistant"):
                render_run(run, idx)
        if not runs:
            st.markdown("**Try asking:**")
            cols = st.columns(len(STARTER_QUESTIONS))
            for col, q in zip(cols, STARTER_QUESTIONS):
                if col.button(q, width="stretch", key=f"ws-start-{q[:20]}"):
                    st.session_state.ws_pending = q
        question = st.chat_input("Ask about the files in this folder")
        question = question or st.session_state.ws_pending
        st.session_state.ws_pending = None
        if question and question.strip():
            run_question(question.strip(), mode, llm_cfg, cfg, allow_python)
