"""Regression checks for actual provider date filters and date formats."""
from datetime import datetime, timezone

import httpx
import pytest

from tire_scout.search import tavily


@pytest.fixture
def search_call(cfg, monkeypatch):
    monkeypatch.setenv(cfg.search.api_key_env, "test-only-search-key")
    calls = []
    response = {"results": []}

    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json=response, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    return tavily.TavilyAdapter(cfg), calls, response


def test_recent_search_uses_news_index_with_day_lookback(search_call):
    adapter, calls, _ = search_call
    adapter.search("tire materials", max_results=30, recency_days=3)
    payload = calls[0]["json"]
    assert payload["topic"] == "news"
    assert payload["days"] == 3
    assert payload["max_results"] == 20
    # Parameters the provider does not document are not sent.
    assert not {"filter_by_published_date", "include_published_date"} & payload.keys()


def test_unbounded_search_does_not_silently_filter_dates(search_call):
    adapter, calls, _ = search_call
    adapter.search("tire materials", max_results=5)
    assert not {"start_date", "end_date", "days"} & calls[0]["json"].keys()


def test_preferred_domains_reach_provider_request(search_call):
    adapter, calls, _ = search_call
    adapter.search('tire compound', max_results=8, include_domains=['evonik.com', 'solvay.com'])
    assert calls[0]['json']['include_domains'] == ['evonik.com', 'solvay.com']


@pytest.mark.parametrize("value, expected", [
    ("2026-10-12T09:00:00+00:00", "2026-10-12T09:00:00+00:00"),
    ("Mon, 12 Oct 2026 09:00:00 GMT", "2026-10-12T09:00:00+00:00"),
    ("unknown", None), (None, None), (123, None),
])
def test_published_date_iso_rfc_or_unknown(search_call, value, expected):
    adapter, _, response = search_call
    response["results"] = [{"url": "https://example.org/tire", "title": "Tire", "published_date": value}]
    result = adapter.search("tire materials", max_results=5)[0]
    assert (result.published_at.isoformat() if result.published_at else None) == expected
