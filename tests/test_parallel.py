"""Requests at once on OpenRouter: patent batches in parallel waves, the brief's questions in parallel; a local
model server keeps one request at a time and its batch size."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from analyst_agent import LLMClient, LLMConfig
from analyst_agent.llm import reply_cache
from analyst_agent.patents import enrich
from analyst_agent.patents import pipeline as pl
from analyst_agent.patents.data import build_corpus, detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan
from analyst_agent.patents.presets import preset_plan

from .patent_fixtures import extract_reply, make_export
from .test_patent_pipeline import FakeLLM


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    return tmp_path


class _SlowServer:
    """Answers patent-reading requests after `delay` seconds and records how many were in flight at once.
    `limit_after`: from that request on, it answers 429 with a daily-limit message."""

    def __init__(self, delay: float = 0.3, limit_after: int | None = None):
        self.requests = self.in_flight = self.most_at_once = 0
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with outer.lock:
                    outer.requests += 1
                    n = outer.requests
                    outer.in_flight += 1
                    outer.most_at_once = max(outer.most_at_once, outer.in_flight)
                time.sleep(delay)
                with outer.lock:
                    outer.in_flight -= 1
                if limit_after is not None and n > limit_after:
                    data = json.dumps({"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}})
                    self.send_response(429)
                else:
                    text = extract_reply(body["messages"][0]["content"], body["messages"][-1]["content"])
                    data = json.dumps({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
                    self.send_response(200)
                data = data.encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    srv = _SlowServer()
    yield srv
    srv.close()


def _std(n=48):
    raw = make_export(n, seed=31)
    return build_corpus(raw, detect_columns(list(raw.columns))).std


def test_batch_sizes_local_unchanged_cloud_larger():
    assert enrich.batch_size_for(16384) == 6 and enrich.batch_size_for(32768) == 6  # a local server, as before
    assert enrich.batch_size_for(131072, cloud=True) == 10
    assert enrich.batch_size_for(131072, long=True, cloud=True) == 8
    assert enrich.batch_size_for(16384, cloud=True) == 8  # still within the context window


def test_parallel_only_on_openrouter():
    options = PipelineOptions(parallel_requests=4)
    assert pl.parallel_for(LLMClient(LLMConfig(base_url="https://openrouter.ai/api", model="m")), options) == 4
    assert pl.parallel_for(LLMClient(LLMConfig(base_url="http://localhost:8080", model="m")), options) == 1
    assert pl.parallel_for(FakeLLM(AnalysisPlan()), options) == 1
    assert pl.parallel_for(LLMClient(LLMConfig(base_url="https://openrouter.ai/api", model="m")),
                           PipelineOptions(parallel_requests=1)) == 1


def test_batches_go_out_together_with_the_same_results(server):
    std, plan = _std(), AnalysisPlan(title="Wear")
    client = LLMClient(LLMConfig(base_url=server.url, model="m", context_tokens=16384, max_retries=0))
    start = time.perf_counter()
    one = enrich.extract_details(std, plan, client, use_cache=False)
    one_time, one_requests = time.perf_counter() - start, server.requests
    assert server.most_at_once == 1  # one at a time, as for a local server
    server.most_at_once = 0
    start = time.perf_counter()
    four = enrich.extract_details(std, plan, client, use_cache=False, parallel=4)
    four_time = time.perf_counter() - start
    assert server.most_at_once >= 3 and server.requests - one_requests == one_requests
    assert four_time < one_time * 0.6  # 8 batches: two waves of 4 instead of 8 in a row
    assert four.table.drop(columns="Extraction").equals(one.table.drop(columns="Extraction"))
    assert four.stats["extracted"] == len(std) and four.stats["model calls"] == one.stats["model calls"]


def test_saved_answers_work_in_the_parallel_threads(server, tmp_path):
    std, plan = _std(30), AnalysisPlan(title="Wear")
    client = LLMClient(LLMConfig(base_url=server.url, model="m", context_tokens=16384, max_retries=0))
    with reply_cache(tmp_path / "replies"):
        enrich.extract_details(std, plan, client, use_cache=False, parallel=4)
        asked = server.requests
        again = enrich.extract_details(std, plan, client, use_cache=False, parallel=4)
    assert server.requests == asked and again.stats["extracted"] == len(std)


def test_a_daily_limit_in_a_wave_keeps_what_was_read():
    srv = _SlowServer(delay=0.05, limit_after=4)
    try:
        std, plan = _std(), AnalysisPlan(title="Wear")
        client = LLMClient(LLMConfig(base_url=srv.url, model="m", context_tokens=16384, max_retries=0))
        res = enrich.extract_details(std, plan, client, use_cache=False, parallel=4)
        assert res.stopped and "limit" in res.stop_reason.lower()
        assert res.stats["extracted"] == 4 * 6  # the first wave of 4 batches was read and kept
        assert (res.table["Extraction"] == "not run (request limit reached)").sum() == len(std) - 24
    finally:
        srv.close()


def test_questions_answered_at_once_keep_the_plans_order(monkeypatch):
    monkeypatch.setattr(pl, "parallel_for", lambda llm, options: 3)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = plan.analysis_questions[:3]
    raw = make_export(40, seed=5)
    previews = []
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=FakeLLM(plan),
                        options=PipelineOptions(semantic_map=False, notable_count=2),
                        on_event=lambda k, p: previews.append(p) if k == "preview" else None)
    assert [q for q, _ in res.question_runs] == plan.analysis_questions
    assert all(run.answer for _, run in res.question_runs)
    assert res.methods["Brief-specific questions"].startswith("3 of 3 answered")


def test_stop_reaches_questions_answered_at_once(monkeypatch):
    monkeypatch.setattr(pl, "parallel_for", lambda llm, options: 3)
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = plan.analysis_questions[:3]
    raw = make_export(30, seed=6)
    model = FakeLLM(plan)
    res = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                        options=PipelineOptions(semantic_map=False, write_report=False, notable_count=0),
                        should_stop=lambda: model.calls["agent"] >= 1)
    assert res.status == "cancelled"
