"""OpenAI-compatible provider (llama.cpp llama-server, LM Studio) client tests.

No network access: httpx get/post are monkeypatched with canned responses,
so the request payloads and the repair/validation behaviour are what is
actually under test.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tire_scout.llm import (FixtureLLMClient, LLMContractError, OpenAICompatClient,
                            _ProbeSchema, _salvage_json, build_llm)

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResp:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"http {self.status_code}", request=None, response=None)

    def json(self):
        return self._payload


def _chat_payload(obj) -> dict:
    return {"choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(obj)}}]}


def _client(cfg, **llm_over) -> OpenAICompatClient:
    cfg.llm.provider = "openai_compat"
    cfg.llm.base_url = "http://localhost:8080/"
    cfg.llm.model = "qwen3-30b-a3b-instruct"
    for k, v in llm_over.items():
        setattr(cfg.llm, k, v)
    return OpenAICompatClient(cfg)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

def test_dispatch_returns_openai_client(cfg):
    cfg.llm.provider = "openai_compat"
    assert isinstance(build_llm(cfg), OpenAICompatClient)


def test_fixture_path_still_wins(cfg):
    cfg.llm.provider = "openai_compat"
    client = build_llm(cfg, fixture_path=str(FIXTURES / "llm" / "analyses.json"))
    assert isinstance(client, FixtureLLMClient)


# --------------------------------------------------------------------------- #
# structured calls
# --------------------------------------------------------------------------- #

def test_structured_success_json_schema_mode(cfg, monkeypatch):
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append((url, json))
        return _FakeResp(_chat_payload({"ok": True}))

    monkeypatch.setattr(httpx, "post", fake_post)
    out = _client(cfg)._structured(_ProbeSchema, "sys", "human")
    assert isinstance(out, _ProbeSchema) and out.ok is True
    url, payload = seen[0]
    assert url == "http://localhost:8080/v1/chat/completions"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "_ProbeSchema"
    assert payload["response_format"]["json_schema"]["schema"] == _ProbeSchema.model_json_schema()
    assert payload["temperature"] == cfg.llm.temperature
    assert payload["max_tokens"] == cfg.llm.max_output_tokens
    assert payload["model"] == "qwen3-30b-a3b-instruct"


def test_fallback_mode_uses_json_object_and_embeds_schema(cfg, monkeypatch):
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append(json)
        return _FakeResp(_chat_payload({"ok": True}))

    monkeypatch.setattr(httpx, "post", fake_post)
    out = _client(cfg, allow_json_mode_fallback=True)._structured(_ProbeSchema, "sys", "h")
    assert out.ok is True
    assert seen[0]["response_format"] == {"type": "json_object"}
    system_msg = seen[0]["messages"][0]["content"]
    assert "JSON Schema" in system_msg and '"ok"' in system_msg


def test_repair_after_invalid_output(cfg, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResp({"choices": [{"message": {"content": "I think ok is true"}}]})
        return _FakeResp(_chat_payload({"ok": True}))

    monkeypatch.setattr(httpx, "post", fake_post)
    out = _client(cfg)._structured(_ProbeSchema, "sys", "human")
    assert out.ok is True
    assert len(calls) == 2
    assert "failed validation" in calls[1]["messages"][1]["content"]


def test_contract_error_after_repair_budget(cfg, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return _FakeResp({"choices": [{"message": {"content": "still not json"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(LLMContractError):
        _client(cfg)._structured(_ProbeSchema, "sys", "human")
    assert len(calls) == 1 + cfg.llm.repair_attempts


def test_http_error_propagates(cfg, monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _FakeResp({"error": "server busy"}, status=503)

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        _client(cfg)._structured(_ProbeSchema, "sys", "human")


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #

def test_capabilities_unreachable(cfg, monkeypatch):
    def fake_get(url, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    report = _client(cfg).capabilities()
    assert report.reachable is False
    assert any("unreachable" in n for n in report.notes)


def test_capabilities_probe_success(cfg, monkeypatch):
    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout=None: _FakeResp({"data": [{"id": "qwen3"}]}))
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None: _FakeResp(_chat_payload({"ok": True})))
    report = _client(cfg).capabilities()
    assert report.reachable is True
    assert report.supports_structured_output is True
    assert report.supports_tools is None
    assert any("openai_compat" in n for n in report.notes)


def test_capabilities_probe_failure_notes_risk(cfg, monkeypatch):
    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout=None: _FakeResp({"data": []}))
    monkeypatch.setattr(httpx, "post",
                        lambda url, json=None, timeout=None:
                        _FakeResp({"choices": [{"message": {"content": "no json"}}]}))
    report = _client(cfg).capabilities()
    assert report.reachable is True
    assert report.supports_structured_output is False
    assert any("allow_json_mode_fallback" in n for n in report.notes)


# --------------------------------------------------------------------------- #
# json salvage helper
# --------------------------------------------------------------------------- #

def test_salvage_json_plain_and_fenced():
    assert json.loads(_salvage_json('{"ok": true}')) == {"ok": True}
    assert json.loads(_salvage_json('```json\n{"ok": true}\n```')) == {"ok": True}
    assert json.loads(_salvage_json('Here you go:\n{"ok": true}\nDone.')) == {"ok": True}
