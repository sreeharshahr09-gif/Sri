"""Source-first discovery: dated feeds, news search, papers, freshness gate,
permanent seen-link memory, syndication merge, analysis cap, weekly by
publication week. All offline: fake DNS and fake HTTP transports."""
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pytest
import yaml

from tire_scout.discovery.candidates import Candidate, score_relevance, url_date
from tire_scout.discovery.channels import (collect_feeds, collect_news_rss, collect_papers,
                                           date_gate, decode_google_news_link, unwrap_bing_link)
from tire_scout.discovery.feeds import discover_feed_links, parse_feed
from tire_scout.discovery.http import DiscoveryHttp
from tire_scout.discovery.run import discover_candidates
from tire_scout.discovery.window import EditionWindow, edition_window
from tire_scout.fetch import FetchResponse, Fetcher
from tire_scout.sources import FeedSpec, SourceRegistry
from tire_scout.timeutil import get_tz

TZ = get_tz("Asia/Kolkata")
EDITION = date(2026, 10, 14)
WINDOW = EditionWindow(date(2026, 10, 12), EDITION)


def rfc822(d: date, hour=9):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link>"
        + (f"<pubDate>{pub}</pubDate>" if pub else "")
        + f"<description><![CDATA[<p>{desc}</p>]]></description></item>"
        for t, link, pub, desc in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


class FakeWeb:
    """GET/POST transports keyed by URL; any other address is unreachable."""

    def __init__(self, pages=None, posts=None):
        self.pages = pages or {}
        self.posts = posts or {}
        self.calls = []

    def get(self, url, headers):
        self.calls.append(url)
        if url not in self.pages:
            raise ConnectionError(f"no route to {url}")
        body, ctype = self.pages[url]
        if isinstance(body, int):
            return FetchResponse(body, {"content-type": "text/html"}, b"", url)
        return FetchResponse(200, {"content-type": ctype}, body, url)

    def post(self, url, data, headers):
        self.calls.append(("POST", url))
        return FetchResponse(200, {}, self.posts[url], url)


def public_dns(host):
    return ["93.184.216.99"]


def http_for(web):
    return DiscoveryHttp(policy=Fetcher(resolve=public_dns), get_transport=web.get, post_transport=web.post)


# --------------------------------------------------------------------------- #
# Parsing and dates
# --------------------------------------------------------------------------- #

def test_rss_atom_and_malformed_feeds_parse_with_dates():
    items = parse_feed(rss([("A &amp; B", "https://t.example.com/a", rfc822(EDITION), "Body")]))
    assert items[0]["title"] == "A & B" and items[0]["published"].date() == EDITION
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom item</title>
      <link rel="alternate" href="/news/1"/><published>2026-10-13T08:00:00Z</published>
      <summary>Short</summary></entry></feed>"""
    item = parse_feed(atom, "https://t.example.com/")[0]
    assert item["link"] == "https://t.example.com/news/1" and item["published"].day == 13
    broken = b"<rss><channel><item><title>Bad&nbsp;entity</title><link>https://t.example.com/b</link>" \
             b"<pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"
    item = parse_feed(broken)[0]
    assert item["link"] == "https://t.example.com/b" and item["published"].day == 13


def test_feed_autodiscovery_from_homepage_link_tag():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed/">' \
           '<link rel="alternate" type="application/rss+xml" href="/comments/feed/"></head></html>'
    assert discover_feed_links(html, "https://www.trade.example.com/") == ["https://www.trade.example.com/feed/"]
    web = FakeWeb({
        "https://www.trade.example.com/": (html.encode(), "text/html"),
        "https://www.trade.example.com/feed/": (rss([("Tyre plant opens", "https://www.trade.example.com/n/1",
                                                       rfc822(EDITION), "x")]), "application/rss+xml")})
    cands, report = collect_feeds([FeedSpec(name="Trade", site="https://www.trade.example.com/",
                                            tire_specific=True)], http_for(web), WINDOW, TZ)
    assert report[0]["status"] == "ok" and report[0]["feed_url"] == "https://www.trade.example.com/feed/"
    assert cands[0].skip_reason is None and cands[0].date_provenance == "feed"


@pytest.mark.parametrize("published,url,expected", [
    (datetime(2026, 10, 13, 9, tzinfo=timezone.utc), "https://x.example.com/a", None),
    (datetime(2026, 10, 11, 9, tzinfo=timezone.utc), "https://x.example.com/a", "before the edition window"),
    (datetime(2026, 10, 15, 9, tzinfo=timezone.utc), "https://x.example.com/a", "after the edition date"),
    (None, "https://x.example.com/a", "undated"),
    (None, "https://x.example.com/2026/10/13/tyre-news", None),           # dated URL
    (None, "https://x.example.com/2019/05/02/tyre-news", "before the edition window"),
])
def test_date_gate(published, url, expected):
    cand = Candidate(url=url, title="t", channel="feed", published_at=published)
    reason = date_gate(cand, WINDOW, TZ)
    assert (reason is None) if expected is None else (expected in reason)


def test_ist_late_evening_utc_counts_as_next_local_day():
    # 20:00 UTC on 11 Oct is 01:30 IST on 12 Oct: inside the window.
    cand = Candidate(url="https://x.example.com/a", title="t", channel="feed",
                     published_at=datetime(2026, 10, 11, 20, tzinfo=timezone.utc))
    assert date_gate(cand, WINDOW, TZ) is None


def test_edition_window_catches_up_after_missed_days_but_is_capped():
    assert edition_window(EDITION, lookback_days=2, max_catchup_days=7, last_success=date(2026, 10, 13)) \
        == EditionWindow(date(2026, 10, 12), EDITION)
    assert edition_window(EDITION, lookback_days=2, max_catchup_days=7, last_success=date(2026, 10, 9)) \
        == EditionWindow(date(2026, 10, 9), EDITION)
    assert edition_window(EDITION, lookback_days=2, max_catchup_days=7, last_success=date(2026, 9, 1)) \
        == EditionWindow(date(2026, 10, 7), EDITION)


def test_url_date_patterns():
    assert url_date("https://a.example.com/news/2026/10/13/x").date() == date(2026, 10, 13)
    assert url_date("https://a.example.com/news/20261013-x").date() == date(2026, 10, 13)
    assert url_date("https://a.example.com/news/x-12345") is None


# --------------------------------------------------------------------------- #
# News search and papers
# --------------------------------------------------------------------------- #

def test_bing_links_unwrap_to_publisher():
    link = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=1&url="
            + quote("https://www.tyrepress.com/2026/10/plant/", safe="") + "&c=1&mkt=en-in")
    assert unwrap_bing_link(link) == "https://www.tyrepress.com/2026/10/plant/"


def test_google_news_link_resolution_and_unresolved_items_are_dropped():
    art = "CBMiTEFVX3lxTE1hYmNkZWZn"
    html = b'<c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1760400000"></div></c-wiz>'
    reply = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", json.dumps(
        ["garturlres", "https://www.tirebusiness.com/news/new-plant", 1])]])
    web = FakeWeb({f"https://news.google.com/rss/articles/{art}": (html, "text/html")},
                  {"https://news.google.com/_/DotsSplashUi/data/batchexecute": reply.encode()})
    http = http_for(web)
    assert decode_google_news_link(f"https://news.google.com/rss/articles/{art}?oc=5", http) \
        == "https://www.tirebusiness.com/news/new-plant"
    feed = rss([("Tyre maker opens plant - Tire Business", f"https://news.google.com/rss/articles/{art}?oc=5",
                 rfc822(EDITION), "x"),
                ("Unresolvable tyre story - Elsewhere", "https://news.google.com/rss/articles/ZZZ?oc=5",
                 rfc822(EDITION), "x")])
    from tire_scout.discovery.channels import google_news_url
    query = "tyre"
    web.pages[google_news_url(query, WINDOW, "en-IN", "IN")] = (feed, "application/rss+xml")
    cands, report = collect_news_rss([query], ["google_rss"], http, WINDOW, TZ, market="en-IN",
                                     hl="en-IN", gl="IN", max_google_resolutions=5)
    ok = [c for c in cands if not c.skip_reason]
    assert [c.url for c in ok] == ["https://www.tirebusiness.com/news/new-plant"]
    assert report[0]["unresolved"] == 1


def test_openalex_papers_use_abstract_as_text(cfg):
    work = {"title": "Silica dispersion in tire tread compounds", "publication_date": "2026-10-13",
            "doi": "https://doi.org/10.1000/xyz", "primary_location": {
                "landing_page_url": "https://journal.example.com/a/1", "source": {"display_name": "Polymers"}},
            "abstract_inverted_index": {"Silica": [0], "dispersion": [1], "improved": [2]}}
    no_abstract = {**work, "title": "Untitled tire note", "abstract_inverted_index": None,
                   "primary_location": {"landing_page_url": "https://journal.example.com/a/2"}}
    reply = json.dumps({"results": [work, no_abstract]}).encode()
    http = DiscoveryHttp(policy=Fetcher(resolve=public_dns),
                         get_transport=lambda url, headers: FetchResponse(
                             200, {"content-type": "application/json"}, reply, url))
    cands, report = collect_papers(cfg.discovery.papers, http, WINDOW, TZ)
    good = [c for c in cands if not c.skip_reason]
    assert len(good) == 1, [(c.url, c.skip_reason) for c in cands]
    assert "Silica dispersion improved" in good[0].text
    assert good[0].date_provenance == "api" and good[0].source_name == "Polymers"
    assert report[0]["no_abstract"] == 1


def test_relevance_triage_requires_tire_terms_for_general_sources(cfg):
    general = Candidate(url="https://r.example.com/1", title="Glove maker expands latex capacity",
                        channel="feed", snippet="Medical gloves.")
    score_relevance(general, cfg.topics)
    assert general.skip_reason and "not tire-related" in general.skip_reason
    trade = Candidate(url="https://t.example.com/1", title="Company appoints new CEO", channel="feed",
                      tire_specific=True)
    score_relevance(trade, cfg.topics)
    assert trade.skip_reason is None
    silane = Candidate(url="https://t.example.com/2", title="New silane for silica tyre treads", channel="feed")
    score_relevance(silane, cfg.topics)
    assert silane.relevance > trade.relevance


# --------------------------------------------------------------------------- #
# Orchestration: exclusions, memory, syndication, cap
# --------------------------------------------------------------------------- #

@pytest.fixture()
def live_cfg(cfg, tmp_path):
    reg = {"feeds": [{"name": "Trade Weekly", "url": "https://www.trade.example.com/feed/",
                      "source_type": "news", "tire_specific": True},
                     {"name": "Rubber General", "url": "https://rubber.example.com/rss",
                      "source_type": "news", "tire_specific": False}],
           "exclude_domains": ["openpr.com"],
           "source_types": {"news": {"domains": ["trade.example.com"]}}}
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(reg), encoding="utf-8")
    cfg.source_registry_path = str(path)
    cfg.search.provider = "tavily"
    cfg.discovery.lookback_days = 2
    cfg.discovery.news_search.enabled = False
    cfg.discovery.papers.enabled = False
    cfg.discovery.web_search_max_queries = 0
    return cfg


def feed_pages():
    trade = rss([
        ("Evonik launches silane for EV tyre treads", "https://www.trade.example.com/n/silane", rfc822(EDITION), "x" * 300),
        ("Tyre plant opens in Chennai", "https://www.trade.example.com/n/plant", rfc822(date(2026, 10, 13)),
         "Apollo Tyres opened a new radial tyre plant in Chennai with capacity of 10,000 tyres per day. " * 4),
        ("Old feature resurfaced", "https://www.trade.example.com/n/old", rfc822(date(2026, 9, 1)), "x"),
        ("Undated item", "https://www.trade.example.com/n/undated", None, "x"),
        ("Tyre market to reach USD 400 billion by 2032", "https://www.trade.example.com/n/market", rfc822(EDITION), "x"),
        ("Press release on tyres", "https://www.openpr.com/news/123", rfc822(EDITION), "x"),
    ])
    general = rss([
        ("Evonik launches silane for EV tyre treads (report)", "https://rubber.example.com/a/silane",
         rfc822(EDITION), "Same announcement, syndicated."),
        ("Glove maker expands capacity", "https://rubber.example.com/a/gloves", rfc822(EDITION), "Latex gloves"),
    ])
    return {"https://www.trade.example.com/feed/": (trade, "application/rss+xml"),
            "https://rubber.example.com/rss": (general, "application/rss+xml")}


def test_discovery_drops_old_undated_market_excluded_offtopic_and_syndicated(live_cfg, db):
    registry = SourceRegistry(live_cfg.source_registry_path)
    out = discover_candidates(cfg=live_cfg, registry=registry, http=http_for(FakeWeb(feed_pages())),
                              tools=None, db=db, window=WINDOW, tz=TZ)
    chosen = {c.url for c in out["chosen"]}
    assert chosen == {"https://www.trade.example.com/n/silane", "https://www.trade.example.com/n/plant"}
    reasons = {c.url: c.skip_reason for c in out["dropped"]}
    assert "before the edition window" in reasons["https://www.trade.example.com/n/old"]
    assert reasons["https://www.trade.example.com/n/undated"].startswith("undated")
    assert "market" in reasons["https://www.trade.example.com/n/market"].lower()
    assert "excluded source" in reasons["https://www.openpr.com/news/123"]
    assert "not tire-related" in reasons["https://rubber.example.com/a/gloves"]
    assert reasons["https://rubber.example.com/a/silane"].startswith("same story")


def test_seen_links_are_skipped_and_cap_defers_without_forgetting(live_cfg, db):
    registry = SourceRegistry(live_cfg.source_registry_path)
    db.mark_seen([("https://trade.example.com/n/plant", "analysed")], EDITION)
    live_cfg.discovery.max_analyses = 0
    out = discover_candidates(cfg=live_cfg, registry=registry, http=http_for(FakeWeb(feed_pages())),
                              tools=None, db=db, window=WINDOW, tz=TZ)
    reasons = {c.url: c.skip_reason for c in out["dropped"]}
    assert reasons["https://www.trade.example.com/n/plant"].startswith("already handled")
    assert [c.url for c in out["deferred"]] == ["https://www.trade.example.com/n/silane"]
    # A transient fetch failure is retried once, then remembered.
    db.mark_seen([("https://x.example.com/f", "fetch_failed")], EDITION)
    assert db.seen_urls(["https://x.example.com/f"]) == {}
    db.mark_seen([("https://x.example.com/f", "fetch_failed")], EDITION)
    assert "https://x.example.com/f" in db.seen_urls(["https://x.example.com/f"])


# --------------------------------------------------------------------------- #
# Full daily run driven by feeds
# --------------------------------------------------------------------------- #

def _graph_deps(live_cfg, db, deps, page_overrides):
    from tire_scout.fetch import Fetcher as PageFetcher
    from conftest import fake_transport
    deps.cfg = live_cfg
    deps.db = db
    deps.discovery_http = http_for(FakeWeb(feed_pages()))
    deps.tools.fetcher = PageFetcher(resolve=public_dns, transport=fake_transport(page_overrides))
    return deps


def test_daily_run_from_feeds_reports_only_fresh_items_and_remembers_them(live_cfg, db, deps):
    from tire_scout.graphs.daily import build_daily_graph
    from tire_scout.schemas import DevelopmentAnalysis
    pages = {
        # Feed says today, but the page itself states 2019: not news.
        "https://www.trade.example.com/n/silane": FetchResponse(
            200, {"content-type": "text/html"},
            b'<html><head><title>Silane</title><meta property="article:published_time" '
            b'content="2019-03-01T09:00:00Z"></head><body><article>Old text about silane.</article></body></html>',
            "https://www.trade.example.com/n/silane"),
        # Page blocked: the publisher's feed summary is analysed instead.
        "https://www.trade.example.com/n/plant": FetchResponse(403, {}, b"", "https://www.trade.example.com/n/plant"),
    }
    _graph_deps(live_cfg, db, deps, pages)
    seen_meta = []

    def analyze(*, doc_meta, doc_text, **kwargs):
        seen_meta.append(doc_meta)
        quote = "Apollo Tyres opened a new radial tyre plant in Chennai"
        return DevelopmentAnalysis.model_validate({
            "is_new_development": True, "title": "Apollo Tyres opens Chennai radial plant",
            "organization": "Apollo Tyres", "category": "Industry and business",
            "what_changed": "A new radial tyre plant opened.", "maturity": "business_event",
            "technical_explanation": "Adds radial capacity of 10,000 tyres per day.",
            "confidence_label": "medium", "confidence_reasons": "Single trade-press report.",
            "evidence_refs": [quote], "novelty_quote": quote, "maturity_quote": quote,
            "scores": {k: {"score": 7, "rationale": "r"} for k in
                       ("relevance", "novelty", "evidence_quality", "impact", "actionability")}})
    deps.llm.analyze = analyze
    graph = build_daily_graph(deps)
    final = graph.invoke({"edition_date": str(EDITION), "mode": "preview"},
                         config={"configurable": {"thread_id": "feeds"}})
    assert final["status"] == "research_complete", final.get("errors")
    devs = db.developments_for_edition(EDITION)
    assert [d["title"] for d in devs] == ["Apollo Tyres opens Chennai radial plant"]
    assert devs[0]["id"] in final["included_ids"]
    assert devs[0]["published_provenance"] == "feed"
    assert seen_meta[0]["window_start"] == "2026-10-12" and seen_meta[0]["publisher"] == "Trade Weekly"
    reasons = {e["ref"]: e["reason"] for e in final["excluded"]}
    assert "page states publication date 2019-03-01" in reasons["https://trade.example.com/n/silane"]
    coverage = json.loads(db.daily_edition(EDITION)["coverage_summary"])
    assert coverage["window"] == {"window_start": "2026-10-12", "window_end": "2026-10-14"}
    assert {s["name"] for s in coverage["discovery_sources"]} == {"Trade Weekly", "Rubber General"}
    html = open(final["render_paths"]["html"], encoding="utf-8").read()
    assert "News published 2026-10-12 to 2026-10-14" in html and "publisher feed" in html
    # Every handled link is remembered; tomorrow nothing is re-analysed.
    seen = db.seen_urls(["https://trade.example.com/n/plant", "https://trade.example.com/n/silane",
                         "https://trade.example.com/n/old"])
    assert set(seen) == {"https://trade.example.com/n/plant", "https://trade.example.com/n/silane",
                         "https://trade.example.com/n/old"}
    seen_meta.clear()
    graph.invoke({"edition_date": str(EDITION + timedelta(days=1)), "mode": "preview"},
                 config={"configurable": {"thread_id": "feeds-2"}})
    assert seen_meta == []


def test_every_source_failing_is_a_failed_run_not_a_quiet_day(live_cfg, db, deps):
    from tire_scout.graphs.daily import build_daily_graph
    _graph_deps(live_cfg, db, deps, {})
    deps.discovery_http = http_for(FakeWeb({}))
    final = build_daily_graph(deps).invoke({"edition_date": str(EDITION), "mode": "preview"},
                                           config={"configurable": {"thread_id": "offline"}})
    assert final["status"] == "failed"


# --------------------------------------------------------------------------- #
# Scope and weekly
# --------------------------------------------------------------------------- #

def test_business_and_regulatory_topics_are_added_to_old_configs(cfg):
    assert "Industry and business" in cfg.topic_names
    assert "Regulation and standards" in cfg.topic_names


@pytest.mark.parametrize("quote", [
    "Apollo Tyres announced an investment of Rs 500 crore in its Chennai plant.",
    "The European Commission adopted the tyre abrasion limits under Euro 7.",
    "Bridgestone and Kordsa signed a supply agreement for aramid cord.",
])
def test_business_and_regulatory_events_count_as_novelty(quote):
    import re
    from tire_scout.quality import NOVELTY_SIGNAL
    assert re.search(NOVELTY_SIGNAL, quote, re.I)


def test_weekly_digest_holds_items_published_that_week_including_monday_catchup(cfg, db):
    from conftest import make_development, seed_edition
    from tire_scout.quality import weekly_items
    ws = date(2026, 10, 5)
    sunday_item = make_development("sun", ws + timedelta(days=7), 8, "Sunday tyre news")
    sunday_item.published_at = datetime(2026, 10, 11, 8, tzinfo=timezone.utc)    # found Monday
    early = make_development("prev", ws, 8, "Previous week's Saturday news")
    early.published_at = datetime(2026, 10, 3, 8, tzinfo=timezone.utc)          # found in this week's Monday run
    for dev in (sunday_item, early):
        db.insert_development(dev)
    ids = [d["id"] for d in weekly_items(db, cfg, ws)]
    assert ids == ["sun"]
    # Coverage: a Monday run whose window starts Saturday covers the weekend.
    for i in range(5):
        seed_edition(db, ws + timedelta(days=i))
    db.upsert_daily_edition(ws + timedelta(days=7), "research_complete",
                            {"window": {"window_start": "2026-10-10", "window_end": "2026-10-12"}}, {}, {}, {})
    assert db.edition_coverage(ws, 2)["complete"]


def test_temporary_drop_reasons_are_not_remembered():
    from tire_scout.graphs.daily import _permanent
    assert _permanent("published 2026-09-01, before the edition window (2026-10-12 to 2026-10-14)")
    assert _permanent("undated: no publication date from the source")
    assert not _permanent("Google News link not resolved (daily resolution cap reached)")
    assert not _permanent("publication date 2026-10-15 is after the edition date")
    assert not _permanent("already handled on 2026-10-13")


def test_missing_backup_search_key_does_not_stop_discovery(cfg, monkeypatch):
    from tire_scout.runners import assemble
    cfg.search.provider = "tavily"
    monkeypatch.delenv(cfg.search.api_key_env, raising=False)
    db, tools, _ = assemble(cfg, llm_fixture=None)
    try:
        assert tools.search.name == "unavailable"
    finally:
        db.close()


def test_wordpress_feed_pages_are_followed_until_the_window_start():
    def page(day_offsets, start):
        return rss([(f"Tyre story {start + i}", f"https://www.trade.example.com/n/{start + i}",
                     rfc822(EDITION - timedelta(days=d)), "x") for i, d in enumerate(day_offsets)])
    web = FakeWeb({
        "https://www.trade.example.com/feed/": (page([0] * 10, 0), "application/rss+xml"),
        "https://www.trade.example.com/feed/?paged=2": (page([1] * 10, 10), "application/rss+xml"),
        "https://www.trade.example.com/feed/?paged=3": (page([1] * 5 + [5] * 5, 20), "application/rss+xml"),
        "https://www.trade.example.com/feed/?paged=4": (page([9] * 10, 30), "application/rss+xml"),
    })
    cands, report = collect_feeds([FeedSpec(name="Trade", url="https://www.trade.example.com/feed/",
                                            tire_specific=True)], http_for(web), WINDOW, TZ)
    assert report[0]["pages"] == 3                      # page 3 already reaches before the window
    assert "https://www.trade.example.com/feed/?paged=4" not in web.calls
    assert report[0]["items"] == 30 and report[0]["in_window"] == 25


def test_switched_off_feeds_are_not_requested():
    web = FakeWeb({})
    cands, report = collect_feeds([FeedSpec(name="Blocked", url="https://blocked.example.com/feed",
                                            enabled=False)], http_for(web), WINDOW, TZ)
    assert cands == [] and report == [] and web.calls == []


def test_blocked_article_is_analysed_from_headline_and_summary(live_cfg, db, deps):
    from tire_scout.graphs.daily import build_daily_graph
    pages = {"https://www.trade.example.com/n/plant": FetchResponse(403, {}, b"", "https://www.trade.example.com/n/plant"),
             "https://www.trade.example.com/n/silane": FetchResponse(403, {}, b"", "https://www.trade.example.com/n/silane")}
    _graph_deps(live_cfg, db, deps, pages)
    texts = {}

    def analyze(*, doc_meta, doc_text, **kwargs):
        texts[doc_meta["canonical_url"]] = (doc_text, doc_meta["extraction_quality"])
        raise ValueError("stop after capturing the input")
    deps.llm.analyze = analyze
    build_daily_graph(deps).invoke({"edition_date": str(EDITION), "mode": "preview"},
                                   config={"configurable": {"thread_id": "blocked"}})
    text, quality = texts["https://trade.example.com/n/plant"]
    assert text.startswith("Tyre plant opens in Chennai\n\nApollo Tyres opened") and quality == "partial"
    # The other blocked item's 300-character feed text also clears the minimum.
    assert "https://trade.example.com/n/silane" in texts
