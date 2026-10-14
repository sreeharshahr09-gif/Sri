"""Brave Search API adapter (https://brave.com/search/api/). Has a free tier.
API key comes from the configured env var only."""
from __future__ import annotations

from datetime import datetime

import httpx

from ..config import get_secret
from ..schemas import SearchResult


class BraveAdapter:
    name = "brave"

    def __init__(self, cfg):
        self.key = get_secret(cfg.search.api_key_env)
        if not self.key:
            raise ValueError(
                f"search API key missing: set env var {cfg.search.api_key_env}")

    def search(self, query: str, *, max_results: int, recency_days: int | None = None,
               include_domains: list[str] | None = None) -> list[SearchResult]:
        if include_domains:
            query += " (" + " OR ".join("site:" + d for d in include_domains) + ")"
        params: dict = {"q": query, "count": min(max_results, 20)}
        if recency_days and recency_days <= 7:
            params["freshness"] = "pw"
        elif recency_days and recency_days <= 31:
            params["freshness"] = "pm"
        resp = httpx.get("https://api.search.brave.com/res/v1/web/search",
                         params=params, headers={"X-Subscription-Token": self.key,
                                                 "Accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("web", {}).get("results", []):
            published = None
            age = r.get("age") or r.get("page_age")
            if age:
                try:
                    published = datetime.fromisoformat(age.replace("Z", "+00:00"))
                except ValueError:
                    published = None
            out.append(SearchResult(title=r.get("title", ""), url=r.get("url", ""),
                                    snippet=r.get("description", ""), published_at=published))
        return out
