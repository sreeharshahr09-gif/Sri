"""Deduplication: URLs, hashes, stable IDs, syndication clustering, follow-ups,
old-material labelling."""
from datetime import datetime, timedelta, timezone

from tire_scout.dedup import (canonicalize_url, content_hash, decide,
                              extract_stable_ids, jaccard, similarity_key)


def test_canonicalize_strips_tracking_and_www():
    a = canonicalize_url("https://www.evonik.com/x/y/?utm_source=n&utm_medium=e&b=2&a=1")
    b = canonicalize_url("https://evonik.com/x/y?a=1&b=2")
    assert a == b == "https://evonik.com/x/y?a=1&b=2"


def test_stable_id_extraction():
    ids = extract_stable_ids("See DOI 10.1016/j.polymertesting.2023.108211 and US11245902B2.")
    assert ids["doi"] == "10.1016/j.polymertesting.2023.108211"
    assert ids["patent_number"].startswith("US")


def _decide(prior, **kw):
    defaults = dict(canonical_url="https://x.com/a", text_hash=None, stable_ids={},
                    title="New silica dispersion method for tire tread",
                    what_changed=None, published_at=None, history_window_days=45,
                    prior=prior)
    defaults.update(kw)
    return decide(**defaults)


def test_same_canonical_url_is_duplicate():
    prior = [{"canonical_url": "https://x.com/a", "title": "other", "cluster_id": "c1"}]
    d = _decide(prior)
    assert d.kind == "duplicate" and d.cluster_id == "c1"


def test_same_content_hash_is_duplicate():
    h = content_hash("identical body text")
    prior = [{"canonical_url": "https://other.com/z", "content_hash": h, "cluster_id": "c2"}]
    d = _decide(prior, text_hash=h)
    assert d.kind == "duplicate"


def test_same_doi_is_duplicate():
    prior = [{"canonical_url": "https://p.com/1", "stable_ids": {"doi": "10.1000/xyz"},
              "cluster_id": "c3"}]
    d = _decide(prior, stable_ids={"doi": "10.1000/xyz"})
    assert d.kind == "duplicate"


def test_syndicated_copy_clusters_not_corroborates():
    prior = [{"title": "Evonik introduces next-generation silane system for silica tire compounds",
              "canonical_url": "https://evonik.com/pr", "cluster_id": "c4"}]
    d = _decide(prior,
                canonical_url="https://rubbernews.com/x",
                title="Evonik unveils next-generation silane system for silica tire compounds")
    assert d.kind == "duplicate" and d.cluster_id == "c4"
    assert "syndicated" in d.reason


def test_genuine_followup_with_material_change_is_kept():
    prior = [{"title": "Startup pilot line for devulcanized rubber",
              "what_changed": "pilot line announced",
              "canonical_url": "https://a.com/1", "cluster_id": "c5"}]
    d = _decide(prior, title="Startup pilot line for devulcanized rubber",
                what_changed="commercial production started with published property data")
    assert d.kind == "follow_up" and d.cluster_id == "c5"


def test_old_publication_is_background_not_new():
    old = datetime.now(timezone.utc) - timedelta(days=400)
    d = _decide([], published_at=old)
    assert d.kind == "new" and d.is_background


def test_unknown_date_stays_not_background():
    d = _decide([], published_at=None)
    assert not d.is_background


def test_jaccard_basics():
    a = similarity_key("steel cord for EV tires", None)
    b = similarity_key("steel cord for EV tires", None)
    assert jaccard(a, b) == 1.0
    assert jaccard(a, set()) == 0.0
