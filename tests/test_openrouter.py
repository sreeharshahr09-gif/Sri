"""Cloud models through OpenRouter: thinking switch, rate limits, key and model checks."""

import time

import pytest

from analyst_agent import llm as llm_mod
from analyst_agent.config import LLMConfig
from analyst_agent.llm import (
    LLMClient,
    LLMError,
    RequestLimitReached,
    is_free_model,
    is_openrouter,
)
from analyst_agent.patents import enrich

from .test_llm import OK, FakeResponse, FakeSession
from .test_patent_enrich import FakeModel, cache_dir, plan, std  # noqa: F401  (fixtures)

URL = "https://openrouter.ai/api/v1"
FREE = "nvidia/nemotron-3.5-super-vl:free"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    sleeps = []
    monkeypatch.setattr("analyst_agent.llm.time.sleep", sleeps.append)
    monkeypatch.setattr(llm_mod, "_last_start", {})
    monkeypatch.setattr(llm_mod, "_reasoning_switch_refused", set())
    return sleeps


class Headed(FakeResponse):
    def __init__(self, status=200, payload=None, text="", headers=None):
        super().__init__(status, payload, text)
        self.headers = headers or {}


def cloud(outcomes, model=FREE, **cfg):
    session = FakeSession(outcomes)
    return LLMClient(LLMConfig(base_url=URL, model=model, api_key="sk-or-x", **cfg), session=session), session


def test_recognises_openrouter_and_free_models():
    assert is_openrouter(URL) and is_openrouter("https://OpenRouter.ai/api/v1/") and not is_openrouter("http://localhost:8080")
    assert is_free_model(LLMConfig(base_url=URL, model=FREE))
    assert not is_free_model(LLMConfig(base_url=URL, model="nvidia/nemotron-3.5-super-vl"))
    assert not is_free_model(LLMConfig(base_url="http://localhost:8080", model="x:free"))


def test_thinking_is_switched_off_with_openrouters_setting():
    client, session = cloud([OK], no_think=True)
    client.chat([{"role": "user", "content": "x"}])
    url, kwargs = session.posts[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["json"]["reasoning"] == {"enabled": False} and "chat_template_kwargs" not in kwargs["json"]
    assert kwargs["headers"]["Authorization"] == "Bearer sk-or-x"
    client, session = cloud([OK])
    client.chat([])
    assert "reasoning" not in session.posts[0][1]["json"]


def test_model_that_must_reason_is_asked_again_without_the_switch():
    refusal = FakeResponse(400, text='{"error": {"message": "Reasoning is mandatory for this endpoint and cannot be '
                                     'disabled."}}')
    client, session = cloud([refusal, OK, OK], no_think=True)
    assert client.chat([]).content == "hi"
    assert "reasoning" in session.posts[0][1]["json"] and "reasoning" not in session.posts[1][1]["json"]
    client.chat([])  # remembered: not asked to switch it off again
    assert "reasoning" not in session.posts[2][1]["json"]


def test_per_minute_limit_is_waited_out_without_using_retries(fresh_state):
    busy = Headed(429, text='{"error": {"message": "Rate limit exceeded: free-models-per-min."}}',
                  headers={"Retry-After": "7"})
    client, session = cloud([busy, busy, busy, OK], max_retries=0)
    assert client.chat([]).content == "hi"
    assert len(session.posts) == 4 and fresh_state.count(7.0) == 3


def test_daily_limit_stops_at_once_with_a_clear_message():
    daily = Headed(429, text='{"error": {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits to '
                             'unlock 1000 free model requests per day"}}')
    client, session = cloud([daily, OK])
    with pytest.raises(RequestLimitReached, match="50 requests a day, or 1,000") as exc:
        client.chat([])
    assert "midnight UTC" in str(exc.value) and len(session.posts) == 1  # failed requests count too: no retries


def test_a_far_reset_time_counts_as_a_used_up_limit():
    reset_ms = str(int((time.time() + 3600) * 1000))
    client, session = cloud([Headed(429, text="limit", headers={"X-RateLimit-Reset": reset_ms}), OK])
    with pytest.raises(RequestLimitReached):
        client.chat([])
    assert len(session.posts) == 1


def test_provider_error_inside_a_200_reply_is_retried():
    flaky = FakeResponse(payload={"error": {"message": "Provider returned error", "code": 502}})
    client, session = cloud([flaky, OK])
    assert client.chat([]).content == "hi" and len(session.posts) == 2
    client, _ = cloud([FakeResponse(payload={"error": {"message": "bad request", "code": 400}})])
    with pytest.raises(LLMError, match="bad request"):
        client.chat([])
    daily = FakeResponse(payload={"error": {"message": "Rate limit exceeded: free-models-per-day", "code": 429}})
    client, session = cloud([daily, OK])
    with pytest.raises(RequestLimitReached):
        client.chat([])
    assert len(session.posts) == 1


def test_refused_key_is_reported_as_such():
    client, _ = cloud([FakeResponse(401, text='{"error": {"message": "No auth credentials found"}}')])
    with pytest.raises(LLMError, match="refused the API key"):
        client.chat([])


def test_free_models_are_paced_under_twenty_requests_a_minute(fresh_state):
    client, _ = cloud([OK, OK])
    client.chat([])
    client.chat([])
    assert len(fresh_state) == 1 and 3.0 < fresh_state[0] <= llm_mod.FREE_MODEL_INTERVAL_S
    fresh_state.clear()
    paid, _ = cloud([OK, OK], model="nvidia/nemotron-3.5-super-vl")
    paid.chat([])
    paid.chat([])
    local = LLMClient(LLMConfig(), session=FakeSession([OK, OK]))
    local.chat([])
    local.chat([])
    assert fresh_state == []


def test_key_and_model_details():
    models = FakeResponse(payload={"data": [{"id": "other"}, {"id": FREE, "context_length": 1_000_000}]})
    key = FakeResponse(payload={"data": {"label": "sk-or-...x", "is_free_tier": True}})
    client, session = cloud([FakeResponse(404, text="not found"), key, models])
    assert client.key_details() == {"label": "sk-or-...x", "is_free_tier": True}
    assert [u for u, _ in session.posts[:2]] == [f"{URL}/key", f"{URL}/auth/key"]
    assert client.model_details()["context_length"] == 1_000_000
    client, _ = cloud([FakeResponse(401, text="bad key"), FakeResponse(payload={"data": [{"id": "other"}]})])
    assert client.key_details()["error"].startswith("HTTP 401") and client.model_details() == {}


class LimitedModel(FakeModel):
    """Answers until the service's daily limit is used up."""

    def __init__(self, limit=None):
        super().__init__()
        self.limit = limit

    def chat(self, messages):
        if self.limit is not None and len(self.calls) >= self.limit:
            raise RequestLimitReached("OpenRouter's daily limit for free models is used up")
        return super().chat(messages)


def test_reading_stops_at_the_daily_limit_and_resumes_from_the_cache(plan, std):  # noqa: F811
    res = enrich.extract_details(std, plan, LimitedModel(limit=2))
    assert res.stopped and "daily limit" in res.stop_reason and res.stats["extracted"] == 12
    assert (res.table["Extraction"] == "not run (request limit reached)").sum() == len(std) - 12
    again = enrich.extract_details(std, plan, LimitedModel())
    assert again.stats["from cache"] == 12 and again.stats["extracted"] == len(std) - 12


def test_pipeline_reports_why_it_stopped(plan):  # noqa: F811
    from analyst_agent.patents import pipeline
    from analyst_agent.patents.data import detect_columns

    from .patent_fixtures import make_export

    raw = make_export(30, seed=2)
    result = pipeline.run_landscape(
        raw, detect_columns(list(raw.columns)), plan, llm=LimitedModel(limit=2),
        options=pipeline.PipelineOptions(semantic_map=False, write_report=False, answer_questions=False),
    )
    assert result.status == "cancelled" and "daily limit" in result.stop_reason
    assert (result.enriched["Extraction"] == "not run (request limit reached)").sum() == len(result.enriched) - 12


def test_thinking_on_is_requested_and_dropped_for_a_model_without_it():
    refusal = FakeResponse(400, text='{"error": {"message": "reasoning is not supported for this model"}}')
    client, session = cloud([refusal, OK, OK], think=True, no_think=True)
    client.chat([])
    client.chat([])
    assert session.posts[0][1]["json"]["reasoning"] == {"enabled": True}
    assert "reasoning" not in session.posts[1][1]["json"] and "reasoning" not in session.posts[2][1]["json"]


class ClientModel(LLMClient):
    """An LLMClient, so its settings (model, thinking) count, that answers like the patent test model."""

    def chat(self, messages):
        return FakeModel().chat(messages)


def test_patents_read_with_thinking_are_cached_apart(plan, std):  # noqa: F811
    def read(think):
        return enrich.extract_details(std, plan, ClientModel(LLMConfig(base_url=URL, model=FREE, think=think),
                                                             session=FakeSession([])))

    first = read(think=False)
    assert first.stats["extracted"] == len(std)
    assert read(think=True).stats["from cache"] == 0  # not mixed with answers read without thinking
    assert read(think=True).stats["from cache"] == len(std)  # but cached for the next run with thinking
    assert read(think=False).stats["from cache"] == len(std)  # and the earlier cache is untouched
    assert "with thinking" in read(think=True).info
