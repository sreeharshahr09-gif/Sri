"""Loopback-only client for an already running llama.cpp ``llama-server``.

The server exposes an OpenAI-compatible API, but nothing here talks to OpenAI or
any other remote service: URLs other than localhost/127.0.0.1/[::1] are refused.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
DEFAULT_CONTEXT = 8192


class LLMError(RuntimeError):
    """A local-model failure with a message the user can act on."""


def safe_server(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("For privacy, only a local HTTP inference server "
                         "(localhost / 127.0.0.1 / [::1]) is allowed.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Please supply only a local server URL, e.g. http://127.0.0.1:8080/v1")
    return base if base.endswith("/v1") else base + "/v1"


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Remove Qwen-style reasoning that leaked into the answer text."""
    text = _THINK_BLOCK.sub("", text or "")
    if "</think>" in text.lower():
        # Template opened <think> itself; keep only what follows the last close tag.
        text = re.split(r"</think>", text, flags=re.I)[-1]
    if text.lstrip().lower().startswith("<think>"):
        # Reasoning never finished: there is no answer in this reply.
        return ""
    return text.strip()


def parse_json_object(answer: str) -> dict[str, Any]:
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(), flags=re.I)
    try:
        obj = json.loads(answer)
    except json.JSONDecodeError:
        start, end = answer.find("{"), answer.rfind("}")
        if start == -1 or end < start:
            raise ValueError("reply was not JSON")
        try:
            obj = json.loads(answer[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"reply was not valid JSON ({exc.msg})") from exc
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


@dataclass
class UsageStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    json_retries: int = 0

    def summary(self) -> str:
        return (f"{self.calls} model calls · {self.prompt_tokens:,} prompt + "
                f"{self.completion_tokens:,} output tokens · {self.seconds:.0f}s")


class LocalLLM:
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", model: str = "",
                 disable_thinking: bool = True, temperature: float = 0.2,
                 timeout: float = 900):
        self.base_url = safe_server(base_url)
        self.root_url = self.base_url[:-len("/v1")]
        self.model = model.strip()
        self.disable_thinking = disable_thinking
        self.temperature = temperature
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False  # never route localhost through corporate proxies
        self.stats = UsageStats()
        self.last_finish_reason = ""
        self._json_mode_ok = True
        self._n_ctx: int | None = None

    def models(self) -> list[str]:
        r = self.session.get(self.base_url + "/models", timeout=8)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", []) if m.get("id")]

    def context_size(self) -> int:
        """Per-request context window reported by llama-server (/props)."""
        if self._n_ctx is None:
            n_ctx = 0
            try:
                r = self.session.get(self.root_url + "/props", timeout=8)
                r.raise_for_status()
                data = r.json()
                n_ctx = int((data.get("default_generation_settings") or {}).get("n_ctx")
                            or data.get("n_ctx") or 0)
            except Exception:
                n_ctx = 0
            self._n_ctx = n_ctx if n_ctx >= 1024 else DEFAULT_CONTEXT
        return self._n_ctx

    def _model_id(self) -> str:
        if not self.model:
            ids = self.models()
            if not ids:
                raise LLMError("No model is advertised by the local server. Start llama-server "
                               "with your GGUF model, then retry.")
            self.model = ids[0]
        return self.model

    def chat(self, system: str, user: str, max_tokens: int = 2000,
             json_mode: bool | None = None) -> str:
        if json_mode is None:
            json_mode = "json" in system.lower()
        payload: dict[str, Any] = {
            "model": self._model_id(),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.temperature, "max_tokens": int(max_tokens), "stream": False,
        }
        if self.disable_thinking:
            # Qwen3-family chat templates (with llama-server --jinja) honour this switch.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if json_mode and self._json_mode_ok:
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        response = self._post(payload)
        if response.status_code == 400 and "response_format" in payload and \
                "context" not in response.text.lower():
            # Older builds reject response_format; prompts still ask for JSON.
            self._json_mode_ok = False
            payload.pop("response_format")
            response = self._post(payload)
        if response.status_code >= 400:
            detail = response.text[:300]
            if "context" in detail.lower() or "too long" in detail.lower():
                raise LLMError("The prompt is larger than the llama-server context window. "
                               "Restart llama-server with a larger --ctx-size (16384 recommended) "
                               f"or shorten the inputs. Server said: {detail}")
            raise LLMError(f"llama-server returned HTTP {response.status_code}: {detail}")

        data = response.json()
        self.stats.calls += 1
        self.stats.seconds += time.monotonic() - started
        usage = data.get("usage") or {}
        self.stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        choice = (data.get("choices") or [{}])[0]
        self.last_finish_reason = str(choice.get("finish_reason") or "")
        message = choice.get("message") or {}
        content = strip_thinking(message.get("content") or "")
        if not content:
            if message.get("reasoning_content") or self.last_finish_reason == "length":
                raise LLMError("The model spent its whole output budget thinking and returned no "
                               "answer. Keep 'Disable model thinking' on, or start llama-server "
                               "with --reasoning-budget 0.")
            raise LLMError("The local model returned an empty answer.")
        return content

    def _post(self, payload: dict[str, Any]) -> requests.Response:
        try:
            return self.session.post(self.base_url + "/chat/completions", json=payload,
                                     timeout=(15, self.timeout))
        except requests.ConnectionError as exc:
            raise LLMError(f"Cannot reach llama-server at {self.base_url}. Is it running?") from exc
        except requests.Timeout as exc:
            raise LLMError("llama-server did not answer in time. It may be busy serving "
                           "another app; try again.") from exc


def ask_json(llm: Any, system: str, prompt: str, max_tokens: int,
             validate: Callable[[dict[str, Any]], None] | None = None,
             retries: int = 1) -> dict[str, Any]:
    """Ask for one JSON object; on malformed/invalid output retry with the error shown.

    ``validate`` raises ValueError to reject a structurally wrong object.
    """
    last_error = ""
    request = prompt
    for attempt in range(retries + 1):
        answer = llm.chat(system, request, max_tokens=max_tokens, json_mode=True)
        try:
            obj = parse_json_object(answer)
            if validate:
                validate(obj)
            return obj
        except ValueError as exc:
            last_error = str(exc)
            stats = getattr(llm, "stats", None)
            if stats is not None and attempt < retries:
                stats.json_retries += 1
            if getattr(llm, "last_finish_reason", "") == "length":
                max_tokens = int(max_tokens * 1.5)
            request = (prompt + "\n\nIMPORTANT: your previous reply could not be used ("
                       + last_error + "). Reply again with exactly ONE valid JSON object "
                       "in the requested shape and nothing else.")
    raise LLMError(f"The local model did not return usable JSON after {retries + 1} attempts "
                   f"({last_error}).")


def stats_dict(llm: Any) -> dict[str, Any]:
    stats = getattr(llm, "stats", None)
    return asdict(stats) if stats is not None else {}
