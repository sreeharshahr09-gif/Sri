"""Workspace page: point the assistant at a local folder; it explains, teaches and proposes edits.

The assistant itself never writes: in Edit mode it *proposes* changes, which are shown here as
diffs. Only the user's Apply click writes a file (with a backup and a journal entry), and Undo
restores the backup. Files are never deleted or renamed.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import PurePosixPath

import streamlit as st

from analyst_agent import AgentConfig, AgentRun, LLMClient, LLMConfig, Sandbox, SandboxConfig
from analyst_agent.config import context_budget_chars
from analyst_agent.editing import BackupStore, EditError, apply_change, undo_change
from analyst_agent.export import describe_tool_step, workspace_to_json, workspace_to_markdown
from analyst_agent.workspace import Workspace, WorkspaceError, is_local_url
from analyst_agent.workspace_agent import WorkspaceAgent
from ui.common import render_artifact, render_execution

STARTER_QUESTIONS = [
    "Give me an overview of this folder: what is in it and how is it organised?",
    "Which are the most important files here, and what does each one do?",
    "Explain the main script step by step.",
]
MODES = {"Answer": "answer", "Teach me": "teach", "Edit": "edit"}
LANGUAGES = {
    ".py": "python", ".m": "matlab", ".r": "r", ".jl": "julia", ".js": "javascript", ".ts": "typescript",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".java": "java", ".sh": "bash", ".sql": "sql", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml", ".html": "html",
    ".tex": "latex",
}
DEFAULT_SANDBOX = SandboxConfig()
ICONS = {"read_file": "📄", "search": "🔍", "list_files": "🗂️", "propose_edits": "✏️"}
STATUS_ICONS = {"pending": "🟡", "applied": "✅", "rejected": "⛔", "undone": "↩️", "conflict": "⚠️"}


def init_state() -> None:
    defaults = {"ws": None, "ws_runs": [], "ws_sandbox": None, "ws_pending": None, "ws_backups": None, "ws_flash": []}
    for key, value in defaults.items():
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
        ws_backups=BackupStore(ws),
    )


# --------------------------------------------------------------------------- change actions
# Button callbacks: they run before the page re-renders, so the new status shows immediately.


def _flash(kind: str, message: str) -> None:
    st.session_state.ws_flash.append((kind, message))


def _apply(run_idx: int, change_idx: int) -> None:
    change = st.session_state.ws_runs[run_idx].changes[change_idx]
    try:
        apply_change(st.session_state.ws, change, st.session_state.ws_backups)
        _flash("success", f"Applied changes to `{change.path}` (backup kept; Undo is available).")
    except EditError as exc:
        _flash("error", f"Could not apply `{change.path}`: {exc}")


def _apply_all(run_idx: int) -> None:
    for i, change in enumerate(st.session_state.ws_runs[run_idx].changes):
        if change.status == "pending":
            _apply(run_idx, i)


def _reject(run_idx: int, change_idx: int) -> None:
    st.session_state.ws_runs[run_idx].changes[change_idx].status = "rejected"


def _undo(run_idx: int, change_idx: int) -> None:
    change = st.session_state.ws_runs[run_idx].changes[change_idx]
    try:
        undo_change(st.session_state.ws, change, st.session_state.ws_backups)
        _flash("success", f"Undid the change to `{change.path}`.")
    except EditError as exc:
        _flash("error", f"Could not undo `{change.path}`: {exc}")


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
        st.sidebar.caption(f"🔒 Access limited to `{ws.root}`. The assistant never writes; only your Apply clicks do.")
    if not is_local_url(llm_cfg.base_url):
        st.sidebar.warning(
            f"The model server is not local ({llm_cfg.base_url}). File contents the assistant reads "
            "will be sent to it.",
            icon="🌐",
        )

    backups: BackupStore | None = st.session_state.ws_backups
    if ws is not None and backups is not None:
        history = backups.history(limit=30)
        with st.sidebar.expander(f"🕘 Change history ({len(history)})", expanded=False):
            if not history:
                st.caption("No changes applied to this folder yet.")
            for entry in history:
                icon = "✅" if entry.get("action") == "apply" else "↩️"
                st.caption(f"{icon} {entry.get('time', '')[:19].replace('T', ' ')} · {entry.get('action')} · "
                           f"`{entry.get('path')}`")
            st.caption(f"Backups and journal: `{backups.dir}`")

    with st.sidebar.expander("⚙️ Assistant", expanded=False):
        max_steps = st.slider("Max steps per question", 2, 30, 12, key="ws_max_steps",
                              help="Tool calls (search, read, …) allowed before it must answer.")
        history_turns = st.slider("Conversation memory (turns)", 0, 10, 4, key="ws_history")
        allow_python = st.checkbox("Allow Python analysis", value=True, key="ws_python",
                                   help="Lets the assistant run sandboxed code that reads (never writes) "
                                        "files in the folder, e.g. to analyse a data file.")
    cfg = replace(AgentConfig(), max_steps=int(max_steps), history_turns=int(history_turns),
                  max_context_chars=context_budget_chars(llm_cfg))

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


def render_changes(run: AgentRun, run_idx: int) -> None:
    if not run.changes:
        return
    pending = [c for c in run.changes if c.status == "pending"]
    st.markdown(f"**✏️ Proposed changes ({len(run.changes)} file{'s' * (len(run.changes) != 1)})** — "
                "nothing is written until you apply it.")
    if len(pending) > 1:
        st.button(f"Apply all {len(pending)} pending", key=f"ws-apply-all-{run_idx}", type="primary",
                  on_click=_apply_all, args=(run_idx,))
    for i, change in enumerate(run.changes):
        added, removed = change.stats()
        kind = "new file" if change.kind == "create" else "edit"
        header = (f"{STATUS_ICONS.get(change.status, '')} `{change.path}` · {kind} · "
                  f"+{added} −{removed} · {change.status}")
        with st.container(border=True):
            cols = st.columns([6, 1, 1])
            cols[0].markdown(header)
            if change.status in ("pending", "rejected", "undone", "conflict"):
                cols[1].button("Apply", key=f"ws-apply-{run_idx}-{i}", type="primary", width="stretch",
                               on_click=_apply, args=(run_idx, i))
            if change.status == "pending":
                cols[2].button("Reject", key=f"ws-reject-{run_idx}-{i}", width="stretch",
                               on_click=_reject, args=(run_idx, i))
            if change.status == "applied":
                cols[2].button("Undo", key=f"ws-undo-{run_idx}-{i}", width="stretch",
                               on_click=_undo, args=(run_idx, i))
            for w in change.warnings:
                st.warning(f"Check before applying: {w}", icon="⚠️")
            if change.status == "conflict" and change.error:
                st.error(change.error)
            diff = change.diff()
            with st.expander("Diff", expanded=change.status == "pending"):
                st.code(diff[:20000] + ("\n… (diff truncated)" if len(diff) > 20000 else ""), language="diff")


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
    render_changes(run, run_idx)
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
            "🛡️ **Safe by design:** in **Edit** mode it only *proposes* changes to text files; you review "
            "each diff and nothing is written until you click Apply (with backup and Undo). Files are "
            "never deleted or renamed, and credentials files (such as `.env` and private keys) are never read."
        )
        st.chat_input("Open a folder first", disabled=True)
        return

    top = st.columns([3, 2])
    top[0].caption(f"**{ws.root.name or ws.root}** · `{ws.root}`")
    mode_label = top[1].radio("Mode", list(MODES), horizontal=True, key="ws_mode", label_visibility="collapsed",
                              help="Teach me: step-by-step explanations with terms defined and check questions.")
    mode = MODES[mode_label]
    if mode == "edit":
        st.info("✏️ **Edit mode:** the assistant proposes changes to text files as diffs. Nothing is "
                "written until you click **Apply**; a backup is kept so you can **Undo**.", icon="🛡️")
    for kind, message in st.session_state.ws_flash:
        (st.success if kind == "success" else st.error)(message)
    st.session_state.ws_flash = []

    ask_tab, files_tab_ = st.tabs(["💬 Ask", "🗂️ Files"])
    with files_tab_:
        files_tab(ws)
    with ask_tab:
        runs: list[AgentRun] = st.session_state.ws_runs
        for idx, run in enumerate(runs):
            with st.chat_message("user"):
                tag = {"teach": "  \n_🎓 teach mode_", "edit": "  \n_✏️ edit mode_"}.get(run.mode, "")
                st.markdown(run.question + tag)
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
