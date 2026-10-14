"""Tavily search adapter (https://tavily.com). Free tier exists; cost/limits are
the user's configured choice. API key comes from the configured env var only."""
from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from ..config import get_secret
from ..schemas import SearchResult
from ..timeutil import get_tz


class TavilyAdapter:
    name = "tavily"

    def __init__(self, cfg):
        self.tz = get_tz(cfg.general.timezone)
        self.key = get_secret(cfg.search.api_key_env)
        if not self.key:
            raise ValueError(
                f"search API key missing: set env var {cfg.search.api_key_env}")

    def search(self, query: str, *, max_results: int, recency_days: int | None = None,
               include_domains: list[str] | None = None) -> list[SearchResult]:
        # Tavily is the backup channel for recent news, so it searches its news
        # index (whose results carry publication dates) rather than the
        # general web index, which surfaced years-old evergreen pages.
        payload: dict = {"query": query, "max_results": min(max_results, 20),
                         "search_depth": "basic", "include_answer": False,
                         "topic": "news"}
        if include_domains:
            payload["include_domains"] = include_domains
        if recency_days:
            # `days` is the news index's documented look-back; trusted code
            # re-checks every returned date against the edition window anyway.
            payload["days"] = int(recency_days)
        resp = httpx.post("https://api.tavily.com/search", json=payload,
                          headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("results", []):
            published = None
            if isinstance(r.get("published_date"), str) and r["published_date"]:
                try:
                    published = datetime.fromisoformat(r["published_date"])
                except (ValueError, TypeError):
                    try:
                        published = parsedate_to_datetime(r["published_date"])
                    except (ValueError, TypeError, OverflowError):
                        published = None
            out.append(SearchResult(title=r.get("title", ""), url=r.get("url", ""),
                                    snippet=r.get("content", ""), published_at=published))
        return out
