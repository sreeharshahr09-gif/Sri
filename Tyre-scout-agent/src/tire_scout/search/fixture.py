"""Fixture adapter for offline tests and dry development. Reads canned results
from a JSON file; never touches the network."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..schemas import SearchResult, SourceType


class FixtureSearchAdapter:
    name = "fixture"

    def __init__(self, cfg):
        path = cfg.search.fixture_path
        if not path:
            raise ValueError("search.provider=fixture requires search.fixture_path")
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def search(self, query: str, *, max_results: int, recency_days: int | None = None,
               include_domains: list[str] | None = None) -> list[SearchResult]:
        hits = []
        for item in self.data.get("results", []):
            for q in item.get("match_queries", [query]):
                if q.lower() in query.lower() or query.lower() in q.lower():
                    hits.append(item)
                    break
        out = []
        for h in hits[:max_results]:
            out.append(SearchResult(
                title=h["title"], url=h["url"], snippet=h.get("snippet", ""),
                published_at=datetime.fromisoformat(h["published_at"]) if h.get("published_at") else None,
                source_hint=SourceType(h["source_hint"]) if h.get("source_hint") else None))
        return out
