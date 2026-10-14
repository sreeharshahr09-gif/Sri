"""Authoritative configuration loading and validation.

One YAML file holds every non-secret setting. Secrets arrive exclusively via
environment variables (names are configured here, values never are).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class GeneralConfig(BaseModel):
    timezone: str = "Asia/Kolkata"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"


class Topic(BaseModel):
    name: str
    priority: Literal["high", "medium", "low"] = "medium"
    keywords: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    provider: Literal["fixture", "tavily", "brave"] = "fixture"
    api_key_env: str = "TIRE_SCOUT_SEARCH_KEY"
    max_results_per_query: int = 8
    recency_windows: dict[str, int] = Field(default_factory=dict)
    retrieval_overlap_days: int = 3
    fixture_path: str | None = None  # fixture provider only


class LLMConfig(BaseModel):
    provider: Literal["ollama", "openai_compat"] = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:14b-instruct"
    temperature: float = 0.1
    num_ctx: int = 16384
    request_timeout_s: int = 240
    max_output_tokens: int = 4096
    allow_json_mode_fallback: bool = False
    repair_attempts: int = 2


class ScoringConfig(BaseModel):
    rubric_version: str = "rubric-1.0"
    weights: dict[str, float] = Field(default_factory=dict)
    inclusion_threshold: float = 5.0
    max_items_daily: int = 8

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ScoringConfig":
        if self.weights:
            total = sum(self.weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"scoring weights must sum to 1.0, got {total:.4f}")
        return self


class BudgetsConfig(BaseModel):
    max_search_calls: int = 24
    max_fetched_pages: int = 40
    max_tool_iterations: int = 12
    max_prompt_tokens: int = 12000
    wall_clock_minutes: int = 45
    concurrent_jobs: int = 1


class OneTimeOverride(BaseModel):
    job: Literal["daily", "weekly"]
    run_at: str  # ISO local datetime; dated and explicit - never recurring
    note: str = ""


class ScheduleConfig(BaseModel):
    daily_time: str = "10:00"  # confirmed with operator: 10:00 IST
    weekly_day: str = "monday"
    weekly_time: str = "11:00"
    catch_up_window_hours: int = 6
    one_time_overrides: list[OneTimeOverride] = Field(default_factory=list)


class RecipientsConfig(BaseModel):
    self_address: str = Field(alias="self")
    daily_to: list[str]
    weekly_to: list[str]
    weekly_cc: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("daily_to", "weekly_to", "weekly_cc")
    @classmethod
    def _nonempty_addrs(cls, v: list[str]) -> list[str]:
        for addr in v:
            if "@" not in addr:
                raise ValueError(f"invalid recipient address: {addr!r}")
        return v


class SmtpConfig(BaseModel):
    host: str = ""
    port: int = 465
    username_env: str = "TIRE_SCOUT_SMTP_USER"
    password_env: str = "TIRE_SCOUT_SMTP_PASSWORD"
    use_ssl: bool = True


class GraphMailConfig(BaseModel):
    tenant_id: str = ""
    client_id: str = ""
    client_secret_env: str = "TIRE_SCOUT_GRAPH_SECRET"


class EmailConfig(BaseModel):
    adapter: Literal["preview", "smtp", "outlook_com", "graph"] = "preview"
    from_address: str
    live_sending: bool = False
    daily_enabled: bool = False
    weekly_enabled: bool = False
    attach_weekly_pdf: bool = True
    daily_pdf: bool = False
    max_attachment_mb: int = 15
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    graph: GraphMailConfig = Field(default_factory=GraphMailConfig)


class SandboxLimits(BaseModel):
    timeout_s: int = 60
    memory_mb: int = 512
    cpus: float = 1.0
    pids: int = 64
    tmpfs_mb: int = 64
    output_total_kb: int = 2048
    output_file_kb: int = 512
    allowed_artifact_exts: list[str] = Field(default_factory=lambda: [".csv", ".json", ".txt", ".md", ".png"])


class SandboxConfig(BaseModel):
    enabled: bool = True
    backend: Literal["docker"] = "docker"
    image: str = "tire-scout-sandbox:py312"
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    retention_days: int = 7


class WeeklyConfig(BaseModel):
    max_priority_items: int = 10
    max_additional_items: int = 10
    recovery_enabled: bool = True
    max_recovery_dates: int = 2


class RetentionConfig(BaseModel):
    raw_artifacts_days: int = 90
    sandbox_outputs_days: int = 7
    preview_outbox_days: int = 30


class TelemetryConfig(BaseModel):
    enabled: bool = False


class AppConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    topics: list[Topic] = Field(default_factory=list)
    source_registry_path: str = "config/sources.registry.yaml"
    search: SearchConfig = Field(default_factory=SearchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    recipients: RecipientsConfig
    email: EmailConfig
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    weekly: WeeklyConfig = Field(default_factory=WeeklyConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    @property
    def topic_names(self) -> list[str]:
        return [t.name for t in self.topics]


def load_config(path: str | Path) -> AppConfig:
    """Load the single authoritative YAML configuration."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = AppConfig.model_validate(raw)
    cfg.general.data_dir = Path(cfg.general.data_dir)
    return cfg


def get_secret(env_name: str) -> str | None:
    """Secrets come only from the environment / OS secret store."""
    value = os.environ.get(env_name)
    return value if value else None


def apply_telemetry_policy(cfg: AppConfig) -> None:
    """Cloud inference and external tracing are disabled by default; when the
    user has not opted in, strip the env vars that would enable them."""
    if cfg.telemetry.enabled:
        return
    for var in (
        "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT", "LANGSMITH_ENDPOINT",
    ):
        os.environ.pop(var, None)
