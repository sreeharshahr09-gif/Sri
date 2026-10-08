"""Minimal client for OpenAI-compatible chat completion servers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import requests

from .config import LLMConfig


class LLMError(RuntimeError):
    """Raised when the model server cannot produce a completion."""


@dataclass
class Completion:
    content: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0


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

        last_error: Exception | None = None
        for attempt in range(cfg.max_retries + 1):
            if attempt:
                time.sleep(min(2**attempt, 10))
            start = time.perf_counter()
            try:
                resp = self.session.post(
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
            except requests.ConnectionError as exc:
                last_error = exc
                continue

            if resp.status_code >= 500 or resp.status_code == 429:
                last_error = LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")
                continue
            if resp.status_code != 200:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")

            try:
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"Unexpected response from model server: {resp.text[:500]}") from exc

            usage = data.get("usage") or {}
            return Completion(
                content=content,
                finish_reason=choice.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                latency_s=time.perf_counter() - start,
            )

        if isinstance(last_error, requests.ConnectionError):
            raise LLMError(
                f"Cannot reach model server at {self._base}. Is llama-server running?"
            ) from last_error
        raise LLMError(str(last_error)) from last_error

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
