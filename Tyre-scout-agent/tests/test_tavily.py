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


def test_general_recency_uses_explicit_local_date_bounds(search_call, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC is still October 12; the configured IST date is October 13.
            return datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tavily, "datetime", FixedDateTime)
    adapter, calls, _ = search_call
    adapter.search("tire materials", max_results=30, recency_days=17)
    payload = calls[0]["json"]
    assert payload["topic"] == "general"
    assert payload["start_date"] == "2026-09-26"
    assert payload["end_date"] == "2026-10-14"
    assert payload["filter_by_published_date"] is True
    assert payload["include_published_date"] is True
    assert payload["max_results"] == 20
    assert "days" not in payload


def test_unbounded_search_does_not_silently_filter_dates(search_call):
    adapter, calls, _ = search_call
    adapter.search("tire materials", max_results=5)
    assert not {"start_date", "end_date", "filter_by_published_date", "days"} & calls[0]["json"].keys()


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
