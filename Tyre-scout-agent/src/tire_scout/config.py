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


DEFAULT_NEWS_QUERIES = [
    '(tyre OR tire) (compound OR silica OR "natural rubber" OR "carbon black" OR elastomer OR resin)',
    '(tyre OR tire) (launch OR launches OR unveils OR introduces OR "new tyre" OR "new tire")',
    '(tyre OR tire) (plant OR factory OR capacity OR investment OR acquisition OR merger OR partnership)',
    '(tyre OR tire) (regulation OR "tyre label" OR "tire label" OR "Euro 7" OR abrasion OR 6PPD OR tariff OR "anti-dumping")',
    '(tyre OR tire) (recycling OR pyrolysis OR "end-of-life" OR retreading OR devulcanization OR circular)',
    '(tyre OR tire) (sensor OR "smart tyre" OR "smart tire" OR TPMS OR "digital twin" OR simulation)',
    '(tyre OR tire) (EV OR "electric vehicle") ("rolling resistance" OR wear OR noise OR grip)',
    '(Michelin OR Bridgestone OR Continental OR Goodyear OR Pirelli OR Hankook OR Yokohama OR Sumitomo) (tyre OR tire)',
    '("Apollo Tyres" OR "MRF" OR "CEAT" OR "JK Tyre" OR "Birla Tyres" OR "TVS Srichakra" OR "Balkrishna Industries")',
    # Publishers that refuse automated feed readers: their headlines via news search.
    '(tyre OR tire) (site:tirebusiness.com OR site:rubbernews.com OR site:tiretechnologyinternational.com OR site:tirereview.com OR site:european-rubber-journal.com)',
    '(Evonik OR Cabot OR "Birla Carbon" OR "Orion Engineered Carbons" OR Arlanxeo OR Lanxess OR Bekaert OR Kordsa OR Synthos OR Trinseo) (tyre OR tire)',
]

# Added to the configured topics when missing, so business and regulatory news
# has a valid category even with an older config file.
DEFAULT_EXTRA_TOPICS = [
    {"name": "Industry and business", "priority": "medium",
     "keywords": ["plant", "factory", "capacity", "investment", "acquisition", "merger",
                  "partnership", "joint venture", "launch", "new tyre", "new tire", "price increase",
                  "results", "expansion"]},
    {"name": "Regulation and standards", "priority": "high",
     "keywords": ["regulation", "tyre label", "tire label", "Euro 7", "tyre abrasion", "tire abrasion",
                  "6PPD", "REACH", "UNECE", "R117", "BIS", "NHTSA", "EPR", "anti-dumping", "tariff",
                  "standard"]},
]


class NewsSearchConfig(BaseModel):
    enabled: bool = True
    providers: list[Literal["bing_rss", "google_rss"]] = Field(
        default_factory=lambda: ["bing_rss", "google_rss"])
    queries: list[str] = Field(default_factory=lambda: list(DEFAULT_NEWS_QUERIES))
    market: str = "en-IN"                    # Bing market
    hl: str = "en-IN"                        # Google News language
    gl: str = "IN"                           # Google News edition
    max_google_resolutions: int = 40         # each costs two small requests


class PapersConfig(BaseModel):
    enabled: bool = True
    query: str = "(tire OR tyre OR tires OR tyres)"
    max_results: int = 50
    mailto: str = ""                         # optional: OpenAlex "polite pool" contact
    api_key_env: str = "TIRE_SCOUT_OPENALEX_KEY"


class DiscoveryConfig(BaseModel):
    lookback_days: int = 2                   # daily window: edition date and the N days before
    max_catchup_days: int = 7                # after missed runs, cover the gap up to this many days
    feeds_enabled: bool = True
    news_search: NewsSearchConfig = Field(default_factory=NewsSearchConfig)
    papers: PapersConfig = Field(default_factory=PapersConfig)
    # The configured search.provider (Tavily) is a backup only: used when the
    # free channels return fewer than this many fresh candidates.
    web_search_min_candidates: int = 10
    web_search_max_queries: int = 3
    max_analyses: int = 15                   # model analyses per run (each ~1-2 min locally)
    include_business_regulatory: bool = True
    show_background_section: bool = False    # old/context items in the daily report
    show_unverified_section: bool = False    # undated items are dropped by default


class AppConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    topics: list[Topic] = Field(default_factory=list)
    source_registry_path: str = "config/sources.registry.yaml"
    search: SearchConfig = Field(default_factory=SearchConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
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

    @model_validator(mode="after")
    def _business_regulatory_topics(self) -> "AppConfig":
        if self.discovery.include_business_regulatory:
            names = {t.name.lower() for t in self.topics}
            for extra in DEFAULT_EXTRA_TOPICS:
                if extra["name"].lower() not in names:
                    self.topics.append(Topic(**extra))
        return self

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
