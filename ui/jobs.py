"""Background jobs: agent runs that survive page switches and other reruns.

An agent run used to execute inside the page's script. Any rerun (switching pages, clicking a
widget) stops that script with a Streamlit control exception, which discarded the run while the
in-flight model request carried on server-side. Now each question runs in a worker thread that
never calls Streamlit; the pages only display the job's progress and collect its result.

Jobs live in the browser session, so they survive page switches but not a browser refresh
(a refresh starts a new session).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import streamlit as st

from analyst_agent import AgentRun

# Session-state key for each page's job, the key of the conversation it belongs to, and its page title.
JOB_PAGES = {"data_job": ("runs", "Data analysis"), "ws_job": ("ws_runs", "Workspace")}
MAX_LINES = 300

Describe = Callable[[str, Any], tuple[str | None, tuple[str, str] | None]]


@dataclass
class Job:
    question: str
    page: str
    mode: str | None = None
    label: str = "Starting…"
    lines: list[tuple[str, str]] = field(default_factory=list)  # (kind, text), kind: "caption" | "code"
    run: AgentRun | None = None
    error: str | None = None
    step: int = 0
    max_steps: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return not self.done.is_set()

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


def start_job(question: str, page: str, work: Callable, describe: Describe, mode: str | None = None,
              max_steps: int = 0) -> Job:
    """Run `work(on_event, should_stop) -> AgentRun` in a worker thread.

    The worker must not call Streamlit; `describe(kind, payload)` turns agent events into a
    status label and/or a progress line using plain data only.
    """
    job = Job(question=question, page=page, mode=mode, max_steps=max_steps)

    def on_event(kind: str, payload: Any) -> None:
        if kind == "llm_call":
            job.step = int(payload) + 1
        label, line = describe(kind, payload)
        if label:
            job.label = label
        if line and len(job.lines) < MAX_LINES:
            job.lines.append(line)

    def target() -> None:
        try:
            job.run = work(on_event, job.cancel.is_set)
        except BaseException as exc:  # the worker must always finish and report
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()
            job.done.set()

    threading.Thread(target=target, name=f"agent-job-{page}", daemon=True).start()
    return job


def active(job_key: str) -> Job | None:
    job = st.session_state.get(job_key)
    return job if isinstance(job, Job) else None


def is_running(job_key: str) -> bool:
    job = active(job_key)
    return job is not None and job.running


def collect(job_key: str) -> AgentRun | None:
    """Move a finished job's result into its conversation. Returns the run, or None if not finished."""
    job = active(job_key)
    if job is None or job.running:
        return None
    runs_key, _ = JOB_PAGES[job_key]
    run = job.run
    if run is None:  # the worker itself failed; keep the question visible with the error
        run = AgentRun(question=job.question, status="error", error=job.error or "Unknown error",
                       duration_s=job.elapsed, mode=job.mode or "data")
    st.session_state.setdefault(runs_key, []).append(run)
    st.session_state[job_key] = None
    return run


def _request_stop(job_key: str) -> None:
    job = active(job_key)
    if job is not None:
        job.cancel.set()


@st.fragment(run_every=1.0)
def job_panel(job_key: str) -> None:
    """Live progress of the page's own job; folds the result into the conversation when done."""
    job = active(job_key)
    if job is None:
        return
    if not job.running:
        collect(job_key)
        st.rerun()  # full rerun so history, exports and inputs refresh
    with st.chat_message("user"):
        tag = {"teach": "  \n_🎓 teach mode_", "edit": "  \n_✏️ edit mode_"}.get(job.mode or "", "")
        st.markdown(job.question + tag)
    with st.chat_message("assistant"):
        stopping = job.cancel.is_set()
        label = "Stopping after the current step…" if stopping else job.label
        with st.status(f"{label} ({_clock(job.elapsed)})", expanded=True):
            for kind, text in job.lines[-40:]:
                if kind == "code":
                    st.code(text, language="python")
                else:
                    st.caption(text)
        st.caption("You can switch pages; this keeps running and the answer will be here when you return.")


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def any_active() -> bool:
    return any(active(key) is not None for key in JOB_PAGES)


@st.fragment(run_every=1.0)
def status_bar(current_page: str) -> None:
    """Run status shown at the top of every page while a question is being worked on."""
    for job_key, (_, page) in JOB_PAGES.items():
        job = active(job_key)
        if job is None:
            continue
        if not job.running:
            collect(job_key)
            if page != current_page:
                outcome = "stopped" if job.run is not None and job.run.status == "cancelled" else "finished"
                st.session_state.job_notice = f"{page} {outcome}: “{job.question[:60]}”. Open {page} to see it."
            st.rerun()
        stopping = job.cancel.is_set()
        icon = "📊" if job_key == "data_job" else "📁"
        steps = f"step {job.step} of up to {job.max_steps}" if job.max_steps else f"step {job.step}"
        activity = "Stopping after the current step…" if stopping else job.label
        where = "" if page == current_page else f" · on the {page} page"
        with st.container(border=True):
            cols = st.columns([6, 1])
            cols[0].markdown(f"{icon} **Working on:** “{job.question[:90]}”{where}")
            fraction = min(job.step / job.max_steps, 0.95) if job.max_steps else 0.0
            cols[0].progress(fraction, text=f"{activity} · {steps} · {_clock(job.elapsed)} elapsed")
            cols[1].button("⏹ Stop", key=f"stop-{job_key}", width="stretch", disabled=stopping,
                           on_click=_request_stop, args=(job_key,),
                           help="Stops after the current step (a model reply already being generated finishes first).")


def show_notice() -> None:
    notice = st.session_state.pop("job_notice", None)
    if notice:
        st.toast(notice, icon="✅")
