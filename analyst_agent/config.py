"""Configuration objects. Defaults can be overridden with environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class LLMConfig:
    """Settings for an OpenAI-compatible chat completions server (e.g. llama.cpp)."""

    base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://localhost:8080"))
    model: str = field(default_factory=lambda: _env("LLM_MODEL", "Qwen3-30B-A3B-Instruct"))
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 2048))
    connect_timeout: float = 10.0
    read_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 180.0))
    max_retries: int = 2
    seed: int | None = None
    # The server's context window (llama-server -c). The workspace agent sizes its memory from it.
    context_tokens: int = field(default_factory=lambda: _env_int("LLM_CONTEXT_TOKENS", 32768))
    # Ask the server to skip the model's "thinking" (chat_template_kwargs enable_thinking=false, which
    # llama-server --jinja, vLLM and others pass to the chat template). Used for structured tasks,
    # where thinking only uses up the reply budget. Templates without the switch ignore it.
    no_think: bool = False
    # The user's choice to let the model think before answering in every step, the structured ones included
    # (it then wins over no_think). Slower and uses more tokens. LLM_THINK=1 turns it on by default.
    think: bool = field(default_factory=lambda: _env("LLM_THINK", "").strip().lower() in ("1", "true", "yes", "on"))


# Extra reply tokens every request gets to think in when thinking is on (LLMClient adds them).
THINKING_TOKENS = 8_000


def reply_budget(llm: LLMConfig, tokens: int) -> int:
    """Reply tokens for an answer of about `tokens`, plus room to think first when thinking is on (up to half the
    context window, so the prompt keeps the other half)."""
    if not llm.think:
        return tokens
    return min(tokens + THINKING_TOKENS, max(tokens, llm.context_tokens // 2))


@dataclass
class SandboxConfig:
    """Limits for executing model-generated code."""

    timeout_seconds: float = field(default_factory=lambda: _env_float("SANDBOX_TIMEOUT", 60.0))
    # Address-space limit for the worker process (POSIX only). 0 disables it.
    memory_limit_mb: int = field(default_factory=lambda: _env_int("SANDBOX_MEMORY_MB", 4096))
    max_stdout_chars: int = 20_000
    max_table_rows: int = 5_000
    max_artifacts: int = 20


@dataclass
class AgentConfig:
    """Behaviour of the reasoning loop."""

    max_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_STEPS", 6))
    # How much of each execution result is fed back to the model.
    max_observation_chars: int = 6_000
    # Prior conversation turns included as context for follow-up questions.
    history_turns: int = 4
    sample_rows: int = 5
    # Total conversation size (characters) before the oldest tool outputs are shortened. None: from
    # context_budget_chars(), which follows AGENT_MAX_CONTEXT_CHARS or the model's context window.
    max_context_chars: int | None = None


# Used when nothing tells the context window (a test double, a client without settings): ~4 characters
# per token, 90k characters fit comfortably in a 32k-token context.
DEFAULT_CONTEXT_CHARS = 90_000


def context_budget_chars(llm: LLMConfig | None = None) -> int:
    """Characters of conversation the agents may keep before trimming old tool output.

    AGENT_MAX_CONTEXT_CHARS overrides it; otherwise it is derived from the model's context window,
    leaving room for the reply, at a conservative ~3 characters per token.
    """
    explicit = _env_int("AGENT_MAX_CONTEXT_CHARS", 0)
    if explicit > 0:
        return explicit
    if llm is None:
        return DEFAULT_CONTEXT_CHARS
    return max(20_000, (llm.context_tokens - reply_budget(llm, llm.max_tokens) - 1_500) * 3)
