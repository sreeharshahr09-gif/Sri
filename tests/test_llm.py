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


def test_models_lists_loaded_model_ids():
    llm, _ = client([FakeResponse(200, payload={"data": [{"id": "Mistral-Small-24B-Q4_K_M.gguf"}]})])
    assert llm.models() == ["Mistral-Small-24B-Q4_K_M.gguf"]
    llm, _ = client([requests.ConnectionError()])
    assert llm.models() is None
    llm, _ = client([FakeResponse(503, text="loading")])
    assert llm.models() is None


def _reply(content, finish="stop"):
    return FakeResponse(payload={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def test_no_think_asks_the_template_to_skip_thinking():
    session = FakeSession([OK, OK])
    LLMClient(LLMConfig(no_think=True), session=session).chat([{"role": "user", "content": "x"}])
    LLMClient(LLMConfig(), session=session).chat([{"role": "user", "content": "x"}])
    assert session.posts[0][1]["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in session.posts[1][1]["json"]


def test_reply_budget_used_up_before_any_answer():
    from analyst_agent.llm import ReplyCutOff

    session = FakeSession([_reply("", "length")])
    with pytest.raises(ReplyCutOff, match="used all 2,048 reply tokens without writing an answer"):
        LLMClient(LLMConfig(), session=session).chat([{"role": "user", "content": "x"}])
    # a long but complete answer is not a cut-off
    assert LLMClient(LLMConfig(), session=FakeSession([_reply("ok", "length")])).chat([]).content == "ok"


def test_structured_calls_retry_a_cut_off_reply_with_more_room():
    from analyst_agent.patents.llmjson import ask_json

    session = FakeSession([_reply("", "length"), _reply('{"a": 1}')])
    client = LLMClient(LLMConfig(max_tokens=2000, context_tokens=16384), session=session)
    assert ask_json(client, "sys", "user", max_tokens=3000) == {"a": 1}
    first, second = session.posts[0][1]["json"], session.posts[1][1]["json"]
    assert first["max_tokens"] == 3000 and second["max_tokens"] == 6000
    assert first["chat_template_kwargs"] == {"enable_thinking": False}


def test_request_longer_than_the_context_window_is_explained():
    too_long = FakeResponse(400, payload={"error": {"code": 400, "message": "request (26082 tokens) exceeds the "
                                                    "available context size (16384 tokens), try increasing it",
                                          "type": "exceed_context_size_error", "n_prompt_tokens": 26082,
                                          "n_ctx": 16384}}, text="exceed_context_size_error")
    llm, session = client([too_long, OK])
    with pytest.raises(LLMError, match=r"longer than the model's context window \(26,082 tokens for a window of "
                                       r"16,384\)\. Start llama-server with a larger -c"):
        llm.chat([])
    assert len(session.posts) == 1  # not retried: it would fail the same way


def test_thinking_on_everywhere_asks_for_it_with_room_to_think(monkeypatch):
    from analyst_agent.config import THINKING_TOKENS, context_budget_chars, reply_budget

    session = FakeSession([OK])
    # The user's switch wins over a structured step's "no thinking".
    LLMClient(LLMConfig(think=True, no_think=True, max_tokens=2048), session=session).chat([{"role": "user", "content": "x"}])
    payload = session.posts[0][1]["json"]
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert payload["max_tokens"] == 2048 + THINKING_TOKENS
    assert reply_budget(LLMConfig(think=True, context_tokens=16384), 2048) == 8192  # at most half the window
    assert reply_budget(LLMConfig(), 2048) == 2048  # thinking off: nothing added
    monkeypatch.delenv("AGENT_MAX_CONTEXT_CHARS", raising=False)
    assert context_budget_chars(LLMConfig(think=True)) < context_budget_chars(LLMConfig())  # the room is kept free
    monkeypatch.setenv("LLM_THINK", "1")
    assert LLMConfig().think and not LLMConfig(think=False).think
