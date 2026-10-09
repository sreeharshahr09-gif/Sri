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
    # Total conversation size (characters) before the oldest tool outputs are shortened.
    # ~4 characters per token: 90k characters fits comfortably in a 32k-token context.
    max_context_chars: int = field(default_factory=lambda: _env_int("AGENT_MAX_CONTEXT_CHARS", 90_000))
