"""Run every discovery channel and reduce the result to a ranked, capped list
of fresh, relevant, never-seen candidates. No page fetch or model call happens
before this point, so old, undated and off-topic items cost almost nothing.
"""
from __future__ import annotations

from ..config import get_secret
from ..dedup import _tokens, canonicalize_url, jaccard
from ..sources import discovery_background_reason
from .candidates import Candidate, score_relevance
from .channels import collect_feeds, collect_news_rss, collect_papers, date_gate
from .window import EditionWindow

CHANNEL_RANK = {"feed": 0, "papers": 1, "news_rss": 2, "web_search": 3}

# Simple keyword queries for the paid web-search backup (no boolean syntax).
WEB_BACKUP_QUERIES = ["tyre industry news", "tire technology development", "tyre regulation news",
                      "tire manufacturer announces", "tyre compound research"]


def _web_search_candidates(tools, queries, window: EditionWindow, tz, max_results: int) -> tuple[list[Candidate], list[dict]]:
    from ..tools import SearchIn
    out, report = [], []
    for query in queries:
        if not tools.budget.check("search"):
            report.append({"name": f"web search: {query}", "status": "skipped", "error": "search budget exhausted"})
            break
        entry = {"name": f"web search: {query}", "status": "ok", "items": 0, "in_window": 0}
        try:
            hits = tools.public_search(SearchIn(query=query, max_results=max_results,
                                                recency_days=max(1, window.days)))
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            report.append(entry)
            continue
        for h in hits:
            if "error" in h:
                entry.update(status="failed", error=h["error"])
                continue
            from datetime import datetime
            published = None
            if h.get("published_at"):
                try:
                    published = datetime.fromisoformat(str(h["published_at"]).replace("Z", "+00:00"))
                except ValueError:
                    published = None
            cand = Candidate(url=h["url"], title=h.get("title") or "", channel="web_search",
                             source_name="web search", source_type=(h.get("source_hint") or "news"),
                             snippet=h.get("snippet") or "", published_at=published,
                             date_provenance="provider" if published else "unknown")
            cand.skip_reason = date_gate(cand, window, tz)
            entry["items"] += 1
            entry["in_window"] += cand.skip_reason is None
            out.append(cand)
        report.append(entry)
    return out, report


def discover_candidates(*, cfg, registry, http, tools, db, window: EditionWindow, tz) -> dict:
    dcfg = cfg.discovery
    raw: list[Candidate] = []
    sources: list[dict] = []
    if cfg.search.provider == "fixture":
        # Offline tests and dry development: canned results stand in for every channel.
        found, rep = _web_search_candidates(tools, list(dcfg.news_search.queries) or ["tire"],
                                            window, tz, cfg.search.max_results_per_query)
        raw += found
        sources += rep
    else:
        if dcfg.feeds_enabled and registry.feeds:
            found, rep = collect_feeds(registry.feeds, http, window, tz)
            raw += found
            sources += rep
        if dcfg.news_search.enabled and dcfg.news_search.queries:
            ns = dcfg.news_search
            found, rep = collect_news_rss(ns.queries, list(ns.providers), http, window, tz,
                                          market=ns.market, hl=ns.hl, gl=ns.gl,
                                          max_google_resolutions=ns.max_google_resolutions)
            raw += found
            sources += rep
        if dcfg.papers.enabled:
            found, rep = collect_papers(dcfg.papers, http, window, tz,
                                        api_key=get_secret(dcfg.papers.api_key_env))
            raw += found
            sources += rep

    selected, dropped, deferred = _reduce(raw, cfg=cfg, registry=registry, db=db)

    # Paid web search is a backup for days when the free channels come up short.
    web_used = 0
    if (tools is not None and cfg.search.provider in ("tavily", "brave") and dcfg.web_search_max_queries > 0
            and len(selected) + len(deferred) < dcfg.web_search_min_candidates):
        found, rep = _web_search_candidates(tools, WEB_BACKUP_QUERIES[:dcfg.web_search_max_queries],
                                            window, tz, cfg.search.max_results_per_query)
        web_used = len(rep)
        sources += rep
        more, more_dropped, more_deferred = _reduce(found, cfg=cfg, registry=registry, db=db,
                                                    already=selected + deferred)
        selected += more
        dropped += more_dropped
        deferred += more_deferred

    ranked = sorted(selected, key=lambda c: (-c.relevance, CHANNEL_RANK.get(c.channel, 9),
                                             -(c.published_at.timestamp() if c.published_at else 0)))
    chosen = ranked[:dcfg.max_analyses]
    deferred += ranked[dcfg.max_analyses:]
    for c in deferred:
        c.skip_reason = c.skip_reason or f"deferred: daily analysis cap ({dcfg.max_analyses}) reached"
    stats = {
        "window": window.as_dict(),
        "raw_candidates": len(raw),
        "chosen_for_analysis": len(chosen),
        "deferred_by_cap": len(deferred),
        "dropped": _count_reasons(dropped),
        "by_channel": _count_channels(raw),
        "web_search_backup_queries": web_used,
        "discovery_requests": getattr(http, "requests", 0),
    }
    return {"chosen": chosen, "dropped": dropped, "deferred": deferred, "sources": sources, "stats": stats}


def _reduce(cands: list[Candidate], *, cfg, registry, db, already: list[Candidate] | None = None):
    """Gate, triage and deduplicate. Returns (eligible, dropped, deferred)."""
    eligible: list[Candidate] = []
    dropped: list[Candidate] = []
    by_url: dict[str, Candidate] = {c.canonical_url: c for c in (already or [])}
    seen = db.seen_urls([canonicalize_url(c.url) for c in cands if c.url]) if hasattr(db, "seen_urls") else {}
    for c in sorted(cands, key=lambda c: CHANNEL_RANK.get(c.channel, 9)):
        c.canonical_url = canonicalize_url(c.url) if c.url else ""
        if not c.skip_reason:
            if not c.url.startswith(("http://", "https://")):
                c.skip_reason = "no usable link"
            elif registry.excluded(c.url):
                c.skip_reason = "excluded source (market-research press release or video)"
            elif discovery_background_reason({"title": c.title, "url": c.url}):
                c.skip_reason = discovery_background_reason({"title": c.title, "url": c.url})
            elif c.canonical_url in seen:
                c.skip_reason = f"already handled on {seen[c.canonical_url][:10]}"
        if not c.skip_reason:
            score_relevance(c, cfg.topics)
        if not c.skip_reason and c.canonical_url in by_url:
            c.skip_reason = "same link already found through another source"
        if c.skip_reason:
            dropped.append(c)
            continue
        by_url[c.canonical_url] = c
        eligible.append(c)
    # Syndication: the same announcement in several outlets is analysed once.
    kept: list[Candidate] = []
    pool = list(already or [])
    for c in sorted(eligible, key=lambda c: (-c.relevance, CHANNEL_RANK.get(c.channel, 9))):
        tokens = _tokens(c.title)
        twin = next((k for k in pool if jaccard(tokens, _tokens(k.title)) >= 0.6), None)
        if twin is not None:
            c.skip_reason = f"same story as '{twin.title[:80]}' ({twin.source_name})"
            dropped.append(c)
            continue
        kept.append(c)
        pool.append(c)
    return kept, dropped, []


def _count_reasons(cands: list[Candidate]) -> dict:
    out: dict[str, int] = {}
    for c in cands:
        reason = (c.skip_reason or "other")
        key = reason.split(":")[0] if reason.startswith(("undated", "deferred")) else \
            ("before window" if "before the edition window" in reason else
             "already handled" if reason.startswith("already handled") else
             "same story" if reason.startswith("same story") else reason[:60])
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _count_channels(cands: list[Candidate]) -> dict:
    out: dict[str, int] = {}
    for c in cands:
        out[c.channel] = out.get(c.channel, 0) + 1
    return out
