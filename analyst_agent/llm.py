"""Minimal client for OpenAI-compatible chat completion servers."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from .config import LLMConfig


class LLMError(RuntimeError):
    """Raised when the model server cannot produce a completion."""


class ReplyCutOff(LLMError):
    """The model used the whole reply budget before writing an answer (usually while thinking)."""


class RequestLimitReached(LLMError):
    """The service's request quota is used up for longer than is worth waiting (e.g. a daily limit)."""


# OPENROUTER_BASE_URL: the same API behind another address (a company gateway, a test double).
OPENROUTER_URL = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
# OpenRouter's free models (ids ending in ":free") accept 20 requests a minute per account; requests start
# at most about 17 a minute, so that delays between pacing and sending never push a minute over 20.
FREE_MODEL_INTERVAL_S = 3.5
MAX_RATE_LIMIT_WAITS = 4
MAX_WAIT_S = 90.0


def is_openrouter(base_url: str) -> bool:
    url = (base_url or "").lower().rstrip("/")
    return "openrouter.ai" in url or (bool(url) and url == OPENROUTER_URL.lower().rstrip("/"))


def is_free_model(cfg: LLMConfig) -> bool:
    return is_openrouter(cfg.base_url) and cfg.model.strip().endswith(":free")


# Start time of the last request per service, shared by all clients in this process (the app's
# pages and background jobs), to keep a free model under its per-minute limit.
_pace_lock = threading.Lock()
_last_start: dict[str, float] = {}
# Models that refused to switch their reasoning off: not asked again.
_reasoning_required: set[str] = set()


def _pace(cfg: LLMConfig) -> None:
    if not is_free_model(cfg):
        return
    with _pace_lock:
        key = cfg.base_url.rstrip("/").lower()
        wait = _last_start.get(key, 0.0) + FREE_MODEL_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[key] = time.monotonic()


def _rate_limit_wait(resp) -> float | None:
    """Seconds until the service accepts requests again, if it says so."""
    headers = getattr(resp, "headers", None) or {}
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    reset = headers.get("X-RateLimit-Reset")
    if reset:
        try:
            value = float(reset)
            value = value / 1000 if value > 1e11 else value  # epoch milliseconds or seconds
            return max(0.0, value - time.time())
        except ValueError:
            pass
    return None


def _context_message(resp, cfg: LLMConfig) -> str:
    sizes = ""
    try:
        err = resp.json().get("error") or {}
        if err.get("n_prompt_tokens") and err.get("n_ctx"):
            sizes = f" ({int(err['n_prompt_tokens']):,} tokens for a window of {int(err['n_ctx']):,})"
    except (ValueError, AttributeError, TypeError):
        pass
    fix = ("Use a model with a larger context window." if is_openrouter(cfg.base_url) else
           "Start llama-server with a larger -c (and set the same value under 🧠 Model server).")
    return f"The request is longer than the model's context window{sizes}. {fix}"


def _limit_message(resp, cfg: LLMConfig) -> str:
    text = resp.text[:300]
    if is_free_model(cfg):
        return ("OpenRouter's daily limit for free models is used up (50 requests a day, or 1,000 once the account "
                "has bought at least $10 of credits; it resets at midnight UTC). Patents read so far are kept: run "
                "the analysis again after the reset, or use the paid model id (without \":free\"). "
                f"Service reply: {text}")
    return f"The model service's request limit is reached. Service reply: {text}"


@dataclass
class Completion:
    content: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0


@dataclass
class _Retry:
    """A response after which the request is sent again."""

    error: Exception  # reported if no later attempt succeeds
    rate_limited: bool = False  # a per-minute limit: waited out without using up a retry
    wait: float | None = None  # seconds the service asked to wait (None: not said)
    drop_reasoning: bool = False  # ask again without the reasoning switch the model refused


class ChatModel(Protocol):
    """Anything that can turn a message list into a completion (real server or test fake)."""

    def chat(self, messages: list[dict[str, str]]) -> Completion: ...


class LLMClient:
    def __init__(self, config: LLMConfig | None = None, session: requests.Session | None = None):
        self.config = config or LLMConfig()
        self.session = session or requests.Session()

    @property
    def _base(self) -> str:
        base = self.config.base_url.rstrip("/")
        return base[: -len("/v1")] if base.endswith("/v1") else base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def chat(self, messages: list[dict[str, str]]) -> Completion:
        """Send the conversation and return the model's reply, retrying what is worth retrying.

        Connection failures, server errors (5xx) and providers' transient errors use up the retries;
        waiting out a per-minute rate limit and asking again without the reasoning switch do not.
        """
        cfg = self.config
        payload = self._payload(messages)
        last_error: Exception | None = None
        attempt = waits = 0
        while attempt <= cfg.max_retries:
            if attempt:
                time.sleep(min(2**attempt, 10))
            attempt += 1
            _pace(cfg)
            start = time.perf_counter()
            try:
                resp = self._post(payload)
            except requests.ConnectionError as exc:
                last_error = exc
                continue
            outcome = self._outcome(resp, payload, start)
            if isinstance(outcome, Completion):
                return outcome
            last_error = outcome.error
            if outcome.drop_reasoning:
                payload = {k: v for k, v in payload.items() if k != "reasoning"}
                attempt -= 1  # the same request without the switch, not a retry
            elif outcome.rate_limited and waits < MAX_RATE_LIMIT_WAITS:
                waits += 1
                attempt -= 1  # a per-minute limit: wait it out, without using up a retry
                time.sleep(outcome.wait if outcome.wait is not None else 10.0 * waits)

        if isinstance(last_error, requests.ConnectionError):
            if is_openrouter(cfg.base_url):
                raise LLMError("Cannot reach openrouter.ai. Check the internet connection.") from last_error
            raise LLMError(
                f"Cannot reach model server at {self._base}. Is llama-server running?"
            ) from last_error
        raise LLMError(str(last_error)) from last_error

    def _payload(self, messages: list[dict[str, str]]) -> dict:
        cfg = self.config
        payload: dict = {
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "stream": False,
        }
        if cfg.seed is not None:
            payload["seed"] = cfg.seed
        if cfg.no_think:
            if not is_openrouter(cfg.base_url):
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            elif cfg.model not in _reasoning_required:
                payload["reasoning"] = {"enabled": False}
        return payload

    def _post(self, payload: dict) -> requests.Response:
        cfg = self.config
        try:
            return self.session.post(
                f"{self._base}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=(cfg.connect_timeout, cfg.read_timeout),
            )
        except requests.Timeout as exc:
            # A read timeout on a long generation will most likely recur; don't retry it.
            raise LLMError(
                f"Model server timed out after {cfg.read_timeout:.0f}s. "
                "Increase the timeout or reduce max tokens."
            ) from exc

    def _outcome(self, resp: requests.Response, payload: dict, start: float) -> Completion | _Retry:
        """What a response means: the completion, a reason to send the request again, or (raised) an error."""
        cfg = self.config
        if resp.status_code == 429:
            wait = _rate_limit_wait(resp)
            if "per-day" in resp.text or (wait is not None and wait > MAX_WAIT_S):
                raise RequestLimitReached(_limit_message(resp, cfg))
            return _Retry(LLMError(f"HTTP 429 (too many requests): {resp.text[:500]}"), rate_limited=True, wait=wait)
        if resp.status_code >= 500:
            return _Retry(LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}"))
        if (resp.status_code == 400 and "reasoning" in payload and "reasoning" in resp.text.lower()
                and cfg.model not in _reasoning_required):
            # A model whose reasoning cannot be switched off: ask again without the switch.
            _reasoning_required.add(cfg.model)
            return _Retry(LLMError(f"HTTP 400: {resp.text[:500]}"), drop_reasoning=True)
        if resp.status_code == 400 and ("exceed_context_size" in resp.text or "context size" in resp.text
                                        or "context length" in resp.text):
            raise LLMError(_context_message(resp, cfg))
        if resp.status_code in (401, 403) and is_openrouter(cfg.base_url):
            raise LLMError(f"OpenRouter refused the API key (HTTP {resp.status_code}). Check the key under "
                           f"🧠 Model server. Service reply: {resp.text[:300]}")
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        return self._completion(resp, start)

    def _completion(self, resp: requests.Response, start: float) -> Completion | _Retry:
        """The completion in a 200 reply, which may still carry a provider's error instead."""
        cfg = self.config
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                # Services such as OpenRouter can report a provider's failure inside a 200 reply.
                err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
                error = LLMError(f"The model service reported an error: {err.get('message', err)}")
                if "per-day" in str(err.get("message", "")):
                    raise RequestLimitReached(_limit_message(resp, cfg))
                if str(err.get("code")) in ("429", "502", "503", "504", "408"):
                    return _Retry(error)
                raise error
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            if not content.strip():
                # Servers started with --jinja may move tool calls out of the text into a separate
                # field; turn them back into text the agent's parser understands.
                content = _tool_calls_as_text(choice["message"].get("tool_calls")) or content
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response from model server: {resp.text[:500]}") from exc

        usage = data.get("usage") or {}
        if not content.strip() and choice.get("finish_reason") == "length":
            raise ReplyCutOff(
                f"The model used all {cfg.max_tokens:,} reply tokens without writing an answer; it was "
                "probably still thinking. "
                + ("Raise the max tokens per reply." if is_openrouter(cfg.base_url) else
                   "Turn thinking off (llama-server --reasoning-budget 0) or raise the max tokens.")
            )
        return Completion(
            content=content,
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_s=time.perf_counter() - start,
        )

    def model_details(self) -> dict | None:
        """The service's entry for the configured model (id, context_length, ...): {} if the service does not
        list it, None if the service cannot be reached."""
        try:
            resp = self.session.get(f"{self._base}/v1/models", headers=self._headers(), timeout=(5, 20))
            entries = resp.json().get("data", []) if resp.status_code == 200 else None
        except (requests.RequestException, ValueError, AttributeError):
            return None
        if not isinstance(entries, list):
            return None
        return next((m for m in entries if isinstance(m, dict) and m.get("id") == self.config.model), {})

    def key_details(self) -> dict | None:
        """OpenRouter: what the service knows about the API key (is_free_tier, limit, usage, ...), or
        {"error": ...} if it refuses the key; None if the service cannot be reached."""
        for path in ("/v1/key", "/v1/auth/key"):
            try:
                resp = self.session.get(f"{self._base}{path}", headers=self._headers(), timeout=(5, 20))
            except requests.RequestException:
                return None
            if resp.status_code == 404:
                continue
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            try:
                data = resp.json().get("data")
            except (ValueError, AttributeError):
                data = None
            return data if isinstance(data, dict) else {}
        return {}

    def context_size(self) -> int | None:
        """The server's context window in tokens, if it reports one (llama-server /props)."""
        try:
            resp = self.session.get(f"{self._base}/props", headers=self._headers(), timeout=(3, 5))
            data = resp.json() if resp.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        settings = data.get("default_generation_settings") or {}
        for value in (settings.get("n_ctx"), data.get("n_ctx")):
            if isinstance(value, int) and value > 0:
                return value
        return None

    def models(self) -> list[str] | None:
        """Model ids the server reports as loaded, or None if it cannot be reached."""
        try:
            resp = self.session.get(f"{self._base}/v1/models", headers=self._headers(), timeout=(3, 5))
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        try:
            return [str(m.get("id", "?")) for m in resp.json().get("data", [])]
        except (ValueError, AttributeError):
            return []

    def health(self) -> tuple[bool, str]:
        """Return (ok, detail) describing whether the server is reachable."""
        try:
            resp = self.session.get(
                f"{self._base}/v1/models", headers=self._headers(), timeout=(3, 5)
            )
        except requests.RequestException as exc:
            return False, f"Unreachable: {exc.__class__.__name__}"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        try:
            models = [m.get("id", "?") for m in resp.json().get("data", [])]
        except (ValueError, AttributeError):
            models = []
        return True, ", ".join(models) if models else "connected"


def _tool_calls_as_text(tool_calls) -> str:
    """OpenAI-style tool_calls as <tool_call> blocks (the format Qwen itself writes)."""
    if not isinstance(tool_calls, list):
        return ""
    blocks = []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        if not fn.get("name"):
            continue
        args = fn.get("arguments") or {}
        if not isinstance(args, str):
            args = json.dumps(args)
        blocks.append(f'<tool_call>\n{{"name": {json.dumps(fn["name"])}, "arguments": {args}}}\n</tool_call>')
    return "\n".join(blocks)
