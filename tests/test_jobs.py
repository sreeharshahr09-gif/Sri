import threading
import time

from analyst_agent.agent import AgentRun
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
