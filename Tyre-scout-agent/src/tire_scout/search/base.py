"""Search adapter protocol: the only way the agent reaches the public search
provider. Providers are external, but only approved queries leave the machine."""
from __future__ import annotations

from typing import Protocol

from ..schemas import SearchResult


class SearchAdapter(Protocol):
    name: str

    def search(self, query: str, *, max_results: int, recency_days: int | None = None,
               include_domains: list[str] | None = None) -> list[SearchResult]:
        ...


def build_search_adapter(cfg) -> SearchAdapter:
    if cfg.search.provider == "fixture":
        from .fixture import FixtureSearchAdapter
        return FixtureSearchAdapter(cfg)
    if cfg.search.provider == "tavily":
        from .tavily import TavilyAdapter
        return TavilyAdapter(cfg)
    if cfg.search.provider == "brave":
        from .brave import BraveAdapter
        return BraveAdapter(cfg)
    raise ValueError(f"unknown search provider: {cfg.search.provider}")
