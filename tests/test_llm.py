import pytest
import requests

from analyst_agent.config import LLMConfig
from analyst_agent.llm import LLMClient, LLMError


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, url, **kwargs):
        return self.post(url, **kwargs)


OK = FakeResponse(
    payload={
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2},
    }
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("analyst_agent.llm.time.sleep", lambda s: None)


def client(outcomes, **cfg):
    session = FakeSession(outcomes)
    return LLMClient(LLMConfig(base_url="http://srv:8080/v1/", **cfg), session=session), session


def test_success_and_payload():
    llm, session = client([OK], seed=3, api_key="k")
    completion = llm.chat([{"role": "user", "content": "x"}])
    assert completion.content == "hi" and completion.prompt_tokens == 7
    url, kwargs = session.posts[0]
    assert url == "http://srv:8080/v1/chat/completions"
    assert kwargs["json"]["seed"] == 3 and kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["timeout"][1] > 0


def test_retries_transient_failures():
    llm, session = client([requests.ConnectionError(), FakeResponse(503, text="busy"), OK])
    assert llm.chat([]).content == "hi"
    assert len(session.posts) == 3


def test_gives_up_with_clear_message():
    llm, _ = client([requests.ConnectionError()] * 3)
    with pytest.raises(LLMError, match="Cannot reach model server"):
        llm.chat([])


def test_client_errors_not_retried():
    llm, session = client([FakeResponse(400, text="bad request")])
    with pytest.raises(LLMError, match="HTTP 400"):
        llm.chat([])
    assert len(session.posts) == 1


def test_timeout_not_retried():
    llm, session = client([requests.ReadTimeout()])
    with pytest.raises(LLMError, match="timed out"):
        llm.chat([])
    assert len(session.posts) == 1


def test_malformed_response():
    llm, _ = client([FakeResponse(200, payload={"unexpected": True})])
    with pytest.raises(LLMError, match="Unexpected response"):
        llm.chat([])


def test_health():
    llm, _ = client([FakeResponse(200, payload={"data": [{"id": "qwen"}]})])
    assert llm.health() == (True, "qwen")
    llm, _ = client([requests.ConnectionError()])
    assert llm.health()[0] is False


def test_tool_calls_field_is_turned_back_into_text():
    from analyst_agent.parsing import parse_action

    reply = FakeResponse(payload={"choices": [{"message": {"content": "", "tool_calls": [
        {"type": "function", "function": {"name": "read_file", "arguments": '{"path": "src/fit.py", "start": 1}'}}
    ]}, "finish_reason": "tool_calls"}]})
    llm, _ = client([reply])
    content = llm.chat([]).content
    action = parse_action(content)
    assert (action.tool, action.args) == ("read_file", {"path": "src/fit.py", "start": 1})


def test_plain_content_is_not_touched_when_tool_calls_absent():
    llm, _ = client([OK])
    assert llm.chat([]).content == "hi"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"default_generation_settings": {"n_ctx": 65536}, "total_slots": 1}, 65536),
        ({"n_ctx": 131072}, 131072),
        ({"something": "else"}, None),
    ],
)
def test_context_size_from_server_props(payload, expected):
    llm, session = client([FakeResponse(200, payload=payload)])
    assert llm.context_size() == expected
    assert session.posts[0][0] == "http://srv:8080/props"


def test_context_size_unavailable():
    llm, _ = client([requests.ConnectionError()])
    assert llm.context_size() is None
    llm, _ = client([FakeResponse(404, text="not found")])
    assert llm.context_size() is None


def test_context_budget_follows_the_window(monkeypatch):
    from analyst_agent.config import context_budget_chars

    monkeypatch.delenv("AGENT_MAX_CONTEXT_CHARS", raising=False)
    small = context_budget_chars(LLMConfig(context_tokens=32768, max_tokens=2048))
    large = context_budget_chars(LLMConfig(context_tokens=65536, max_tokens=2048))
    assert 80_000 < small < 95_000 and 180_000 < large < 190_000
    assert context_budget_chars(LLMConfig(context_tokens=4096, max_tokens=2048)) == 20_000  # floor
    monkeypatch.setenv("AGENT_MAX_CONTEXT_CHARS", "123456")
    assert context_budget_chars(LLMConfig(context_tokens=65536)) == 123456
