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
