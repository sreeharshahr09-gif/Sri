import threading
import time

from analyst_agent.runs import AgentRun
from ui.jobs import start_job


def describe(kind, payload):
    if kind == "llm_call":
        return f"Thinking (step {payload + 1})…", None
    if kind == "note":
        return None, ("caption", payload)
    return None, None


def wait(job, timeout=5):
    assert job.done.wait(timeout), "job did not finish"


def test_job_runs_in_background_and_reports_progress():
    release = threading.Event()

    def work(on_event, should_stop):
        on_event("llm_call", 0)
        on_event("note", "read a file")
        release.wait(5)
        on_event("llm_call", 1)
        return AgentRun(question="q", answer="done", status="answered")

    job = start_job("q", "Data analysis", work, describe, max_steps=7)
    time.sleep(0.1)
    assert job.running and job.step == 1 and job.label == "Thinking (step 1)…"
    assert job.lines == [("caption", "read a file")]
    release.set()
    wait(job)
    assert not job.running and job.step == 2 and job.run.answer == "done" and job.error is None


def test_stop_request_reaches_the_work():
    def work(on_event, should_stop):
        while not should_stop():
            time.sleep(0.01)
        return AgentRun(question="q", status="cancelled")

    job = start_job("q", "Workspace", work, describe)
    job.cancel.set()
    wait(job)
    assert job.run.status == "cancelled"


def test_worker_failures_are_captured_not_lost():
    def work(on_event, should_stop):
        raise RuntimeError("model server crashed")

    job = start_job("q", "Data analysis", work, describe)
    wait(job)
    assert job.run is None and job.error == "RuntimeError: model server crashed" and job.finished is not None


def test_counted_progress_gives_an_eta_and_ends_with_its_phase():
    gates = [threading.Event() for _ in range(4)]

    def work(on_event, should_stop):
        on_event("phase", "Embedding")
        on_event("progress", (0, 10))
        time.sleep(0.05)
        on_event("progress", (5, 10))
        gates[0].set()
        gates[1].wait(5)
        on_event("phase", "Clustering")
        gates[2].set()
        gates[3].wait(5)
        return AgentRun(question="q", status="answered")

    job = start_job("q", "Patent landscape", work, describe)
    assert gates[0].wait(5)
    assert (job.done_items, job.total_items) == (5, 10)
    assert job.eta is not None and 0 < job.eta < 5
    gates[1].set()
    assert gates[2].wait(5)
    assert (job.done_items, job.total_items) == (0, 0) and job.eta is None
    gates[3].set()
    wait(job)


def test_outcome_of_results_with_and_without_a_status():
    # A prepared plan has no status; reading one crashed the status bar when it finished on another page.
    from dataclasses import dataclass

    from ui.jobs import Job, outcome

    @dataclass
    class PlanReady:
        plan: object = None

    assert outcome(Job("q", "Patent landscape", run=PlanReady())) == "finished"
    assert outcome(Job("q", "Data analysis", run=AgentRun(question="q", status="cancelled"))) == "stopped"
    assert outcome(Job("q", "Data analysis", run=None)) == "finished"


def test_previews_are_kept_on_the_job_without_progress_lines():
    release = threading.Event()

    def work(on_event, should_stop):
        on_event("preview", ["first"])
        on_event("preview", ["second"])
        release.wait(5)
        return AgentRun(question="q", status="answered")

    job = start_job("q", "Patent landscape", work, describe)
    time.sleep(0.1)
    assert job.preview == ["second"] and job.preview_version == 2 and job.lines == []
    release.set()
    wait(job)
