"""Graph state definitions. Explicit typed states and routing - the model never
decides whether mail is sent."""
from __future__ import annotations

from typing import Any, TypedDict


class DailyState(TypedDict, total=False):
    edition_date: str
    mode: str                     # preview | production
    job_id: str
    retrospective: bool           # catch-up run for a past date
    already_done: bool            # completed edition re-run: skip research
    queries: list[dict]
    search_results: list[dict]
    discovery_strategy: dict
    fetched: list[dict]
    kept: list[dict]              # fetched docs surviving dedup (new / follow-up)
    developments: list[dict]      # full Development records
    included_ids: list[str]
    background_ids: list[str]
    unverified_ids: list[str]
    excluded: list[dict]
    quarantined: list[dict]
    status: str                   # research_complete | no_updates | failed | partial
    coverage_summary: dict
    limitations: list[str]
    render_paths: dict
    send_state: str
    send_identity: str
    errors: list[str]
    budgets: dict


class WeeklyState(TypedDict, total=False):
    week_start: str
    week_end: str
    mode: str
    job_id: str
    allow_partial: bool           # explicit operator override only
    coverage: dict
    missing_dates: list[str]
    recovered: list[str]
    developments: list[dict]
    priority_ids: list[str]
    additional_ids: list[str]
    themes: list[dict]
    team_actions: list[dict]
    status: str                   # complete | partial_blocked | partial_sent
    render_paths: dict
    send_state: str
    send_identity: str
    limitations: list[str]
    errors: list[str]
