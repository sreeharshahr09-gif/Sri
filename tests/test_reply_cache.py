"""Saved replies: a repeated run (after a crash, a stop, or with an unchanged plan) asks the model only what
changed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from analyst_agent import LLMClient, LLMConfig
from analyst_agent.llm import Completion, ReplyCache, reply_cache
from analyst_agent.patents import enrich
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.presets import preset_plan

from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM


class _ModelServer:
    """A local chat-completions server answering like FakeLLM, counting the requests it gets."""

    def __init__(self, model):
        self.requests = 0
        self.asked: list[list[dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.requests += 1
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.asked.append(body["messages"])
                text = model.chat(body["messages"]).content
                data = json.dumps({"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(200)
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


def test_only_complete_replies_are_kept(tmp_path):
    cache = ReplyCache(tmp_path)
    cache.put("a" * 64, Completion(content="answer", finish_reason="stop"))
    cache.put("b" * 64, Completion(content="   "))  # empty: asked again next time
    cache.put("c" * 64, Completion(content="half an ans", finish_reason="length"))  # cut off: asked again
    assert cache.get("a" * 64).content == "answer" and cache.hits == 1
    assert cache.get("b" * 64) is None and cache.get("c" * 64) is None and cache.saved == 1


def test_a_repeated_request_is_answered_from_the_cache(tmp_path):
    server = _ModelServer(type("Echo", (), {"chat": lambda self, m: Completion(content=m[-1]["content"].upper())})())
    try:
        client = LLMClient(LLMConfig(base_url=server.url, model="m", max_retries=0))
        with reply_cache(tmp_path) as cache:
            assert client.chat([{"role": "user", "content": "hi"}]).content == "HI"
            assert client.chat([{"role": "user", "content": "hi"}]).content == "HI"  # from the cache
            assert client.chat([{"role": "user", "content": "other"}]).content == "OTHER"
        assert server.requests == 2 and cache.hits == 1
        client.chat([{"role": "user", "content": "hi"}])  # outside the block: asked again
        assert server.requests == 3
        other_model = LLMClient(LLMConfig(base_url=server.url, model="m2", max_retries=0))
        with reply_cache(tmp_path):
            other_model.chat([{"role": "user", "content": "hi"}])  # another model: its own answer
        assert server.requests == 4
    finally:
        server.close()


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    return tmp_path


def test_a_repeated_run_asks_nothing_again(cache_dir):
    plan = preset_plan("Uneven wear")
    plan.analysis_questions = plan.analysis_questions[:2]
    server = _ModelServer(FakeLLM(plan))
    try:
        client = LLMClient(LLMConfig(base_url=server.url, model="fake", context_tokens=32768, max_retries=0))
        raw = make_export(50, seed=13)
        options = PipelineOptions(embedding_model="tfidf", notable_count=3)
        first = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=client, options=options)
        asked = server.requests
        assert first.status == "complete" and asked > 10 and "Saved answers" not in first.methods
        again = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=client, options=options)
        again_asked = [m[0]["content"][:70] + " | " + str(m[-1]["content"])[:300] for m in server.asked[asked:]]
        assert server.requests == asked, again_asked  # every step came from the saved replies
        assert again.status == "complete" and again.methods["Saved answers"].startswith(
            f"{asked - first.extraction.stats['model calls']} answers")
        assert [s.text for s in again.sections] == [s.text for s in first.sections]
        fresh = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=client,
                              options=PipelineOptions(embedding_model="tfidf", notable_count=3, reuse_replies=False))
        assert server.requests > asked and fresh.status == "complete"  # untick it for fresh text
    finally:
        server.close()
