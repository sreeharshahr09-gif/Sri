"""Operator checks that need no model and send no email.

check_feeds:      does every configured feed load, and is it current?
discover_preview: which fresh candidates would today's run analyse?
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from ..db import Database
from ..fetch import Fetcher
from ..sources import SourceRegistry
from ..timeutil import get_tz
from .channels import collect_feeds, collect_news_rss, collect_papers
from .http import DiscoveryHttp
from .run import discover_candidates
from .window import edition_window


def _window(cfg, db, edition: date):
    return edition_window(edition, lookback_days=cfg.discovery.lookback_days,
                          max_catchup_days=cfg.discovery.max_catchup_days,
                          last_success=db.last_successful_edition(edition) if db else None)


def check_feeds(cfg, *, http: DiscoveryHttp | None = None, edition: date | None = None,
                include_search: bool = True) -> list[dict]:
    tz = get_tz(cfg.general.timezone)
    edition = edition or datetime.now(tz).date()
    registry = SourceRegistry(cfg.source_registry_path)
    http = http or DiscoveryHttp(policy=Fetcher())
    window = _window(cfg, None, edition)
    _, report = collect_feeds(registry.feeds, http, window, tz)
    if include_search and cfg.discovery.news_search.enabled:
        ns = cfg.discovery.news_search
        for provider in ns.providers:
            _, rep = collect_news_rss(ns.queries[:1], [provider], http, window, tz, market=ns.market,
                                      hl=ns.hl, gl=ns.gl, max_google_resolutions=3)
            report += rep
    if include_search and cfg.discovery.papers.enabled:
        from ..config import get_secret
        _, rep = collect_papers(cfg.discovery.papers, http, window, tz,
                                api_key=get_secret(cfg.discovery.papers.api_key_env))
        report += rep
    for w in registry.warnings:
        report.append({"name": "registry", "status": "warning", "error": w})
    return report


def discover_preview(cfg, *, edition: date | None = None, http: DiscoveryHttp | None = None,
                     use_web_search: bool = False) -> dict:
    tz = get_tz(cfg.general.timezone)
    edition = edition or datetime.now(tz).date()
    registry = SourceRegistry(cfg.source_registry_path)
    db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
    tools = None
    try:
        if use_web_search:
            from ..runners import assemble
            db.close()
            db, tools, _ = assemble(cfg)
        http = http or DiscoveryHttp(policy=Fetcher())
        window = _window(cfg, db, edition)
        result = discover_candidates(cfg=cfg, registry=registry, http=http, tools=tools, db=db,
                                     window=window, tz=tz)
    finally:
        db.close()
    rows = []
    for status, cands in (("analyse", result["chosen"]), ("deferred", result["deferred"]),
                          ("dropped", result["dropped"])):
        for c in cands:
            rows.append({"status": status, "reason": c.skip_reason or "",
                         "published": c.published_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if c.published_at else "",
                         "date_from": c.date_provenance, "channel": c.channel, "source": c.source_name,
                         "relevance": round(c.relevance, 1), "title": c.title, "url": c.url})
    outdir = Path(cfg.general.data_dir) / "reports" / "discovery"
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / f"discovery-{edition}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["status"])
        writer.writeheader()
        writer.writerows(rows)
    json_path = outdir / f"discovery-{edition}.json"
    json_path.write_text(json.dumps({"edition": str(edition), "stats": result["stats"],
                                     "sources": result["sources"], "rows": rows}, indent=2, default=str),
                         encoding="utf-8")
    return {"stats": result["stats"], "sources": result["sources"], "rows": rows,
            "csv": str(csv_path), "json": str(json_path)}
