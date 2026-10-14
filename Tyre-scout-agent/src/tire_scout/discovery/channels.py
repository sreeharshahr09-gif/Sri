"""Discovery channels. Each returns dated Candidates plus a per-source report.

- feeds:    publisher RSS/Atom (trade press, company and supplier newsrooms)
- news_rss: Bing News / Google News RSS keyword sweeps (dated, recent-first)
- papers:   OpenAlex works published inside the window (title + abstract)
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, time
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

from .candidates import Candidate, clean_title, url_date
from .feeds import discover_feed_links, looks_like_feed, parse_feed
from .http import DiscoveryHttp, DiscoveryHttpError
from .window import EditionWindow, local_date

FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"


# --------------------------------------------------------------------------- #
# Shared date gate
# --------------------------------------------------------------------------- #

def date_gate(cand: Candidate, window: EditionWindow, tz) -> str | None:
    """Reason to drop a candidate on date grounds, or None if it is in window."""
    if cand.published_at is None:
        found = url_date(cand.url)
        if found is not None:
            cand.published_at, cand.date_provenance = found, "url"
    if cand.published_at is None:
        return "undated: no publication date from the source"
    day = local_date(cand.published_at, tz)
    if day > window.end:
        return f"publication date {day} is after the edition date"
    if day < window.start:
        return f"published {day}, before the edition window ({window.start} to {window.end})"
    return None


# --------------------------------------------------------------------------- #
# Publisher feeds
# --------------------------------------------------------------------------- #

def _load_feed(http: DiscoveryHttp, url: str) -> tuple[list[dict], str]:
    resp = http.get(url, accept=FEED_ACCEPT)
    ctype = resp.headers.get("content-type") or resp.headers.get("Content-Type") or ""
    if looks_like_feed(resp.body, ctype):
        return parse_feed(resp.body, resp.url or url), resp.url or url
    # A homepage or section page: follow its advertised feed links.
    html = resp.body.decode("utf-8", errors="replace")
    for link in discover_feed_links(html, resp.url or url)[:3]:
        try:
            sub = http.get(link, accept=FEED_ACCEPT)
        except DiscoveryHttpError:
            continue
        if looks_like_feed(sub.body, sub.headers.get("content-type", "")):
            return parse_feed(sub.body, sub.url or link), sub.url or link
    raise DiscoveryHttpError("no RSS/Atom feed found at this address")


def collect_feeds(feeds: list, http: DiscoveryHttp, window: EditionWindow, tz) -> tuple[list[Candidate], list[dict]]:
    candidates, report = [], []
    for spec in feeds:
        entry = {"name": spec.name, "configured": spec.url or spec.site, "status": "ok",
                 "items": 0, "in_window": 0, "undated": 0, "newest": None, "feed_url": None}
        items, last_error = None, None
        for address in [a for a in (spec.url, spec.site) if a]:
            try:
                items, entry["feed_url"] = _load_feed(http, address)
                break
            except DiscoveryHttpError as exc:
                last_error = f"{address}: {exc}"
        if items is None:
            entry.update(status="failed", error=last_error or "no feed address configured")
            report.append(entry)
            continue
        entry["items"] = len(items)
        dates = [i["published"] for i in items if i["published"]]
        entry["newest"] = max(dates).isoformat() if dates else None
        for item in items:
            cand = Candidate(url=item["link"], title=clean_title(item["title"]), channel="feed",
                             source_name=spec.name, source_type=spec.source_type,
                             snippet=(item["content"] or item["summary"])[:4000],
                             published_at=item["published"],
                             date_provenance="feed" if item["published"] else "unknown",
                             tire_specific=spec.tire_specific)
            reason = date_gate(cand, window, tz)
            if reason and reason.startswith("undated"):
                entry["undated"] += 1
            if not reason:
                entry["in_window"] += 1
            cand.skip_reason = reason
            candidates.append(cand)
        report.append(entry)
    return candidates, report


# --------------------------------------------------------------------------- #
# News search RSS (Bing News, Google News)
# --------------------------------------------------------------------------- #

def bing_news_url(query: str, window: EditionWindow, market: str) -> str:
    # interval 7 = past 24 hours, 8 = past 7 days; the date gate trims further.
    interval = quote('interval="7"' if window.days <= 1 else 'interval="8"')
    return (f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
            f"&setmkt={quote_plus(market)}&qft={interval}&count=50")


def google_news_url(query: str, window: EditionWindow, hl: str, gl: str) -> str:
    q = f"{query} when:{max(1, window.days)}d"
    return (f"https://news.google.com/rss/search?q={quote_plus(q)}"
            f"&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(gl + ':' + hl.split('-')[0])}")


def unwrap_bing_link(link: str) -> str:
    parts = urlsplit(link)
    if parts.hostname and parts.hostname.endswith("bing.com") and "apiclick" in parts.path:
        target = parse_qs(parts.query).get("url", [""])[0]
        if target.startswith("http"):
            return target
    return link


def _google_article_id(link: str) -> str | None:
    parts = urlsplit(link)
    if not (parts.hostname or "").endswith("news.google.com"):
        return None
    m = re.search(r"/(?:rss/)?articles/([^/?#]+)", parts.path)
    return m[1] if m else None


def decode_google_news_link(link: str, http: DiscoveryHttp) -> str | None:
    """Resolve a news.google.com article link to the publisher URL.

    Older ids embed the URL in base64; current ids need Google's own decoding
    endpoint. Best effort: an unresolved item is dropped, never guessed.
    """
    art_id = _google_article_id(link)
    if not art_id:
        return link if link.startswith("http") else None
    try:
        raw = base64.urlsafe_b64decode(art_id + "=" * (-len(art_id) % 4))
        m = re.search(rb"https?://[\x21-\x7e]+", raw)
        if m:
            return m.group(0).decode("ascii", errors="ignore")
    except (ValueError, TypeError):
        pass
    try:
        page = http.get(f"https://news.google.com/rss/articles/{art_id}", accept="text/html")
        html = page.body.decode("utf-8", errors="replace")
        sig = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not (sig and ts):
            return None
        inner = json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                                             None, None, None, None, None, 0, 1],
                                            "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                            art_id, int(ts[1]), sig[1]], separators=(",", ":"))
        payload = "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":")))
        resp = http.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", payload,
                         headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        text = resp.body.decode("utf-8", errors="replace")
        chunk = text.split("\n\n")[1] if "\n\n" in text else text
        data = json.loads(chunk)
        for row in data:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and row[2]:
                decoded = json.loads(row[2])
                if isinstance(decoded, list) and len(decoded) > 1 and str(decoded[1]).startswith("http"):
                    return decoded[1]
    except (DiscoveryHttpError, ValueError, IndexError, TypeError, KeyError):
        return None
    return None


def collect_news_rss(queries: list[str], providers: list[str], http: DiscoveryHttp,
                     window: EditionWindow, tz, *, market: str, hl: str, gl: str,
                     max_google_resolutions: int) -> tuple[list[Candidate], list[dict]]:
    candidates, report = [], []
    google_budget = max_google_resolutions
    for provider in providers:
        for query in queries:
            url = (bing_news_url(query, window, market) if provider == "bing_rss"
                   else google_news_url(query, window, hl, gl))
            entry = {"name": f"{provider}: {query}", "status": "ok", "items": 0, "in_window": 0,
                     "unresolved": 0}
            try:
                resp = http.get(url, accept=FEED_ACCEPT)
                items = parse_feed(resp.body, url)
            except DiscoveryHttpError as exc:
                entry.update(status="failed", error=str(exc))
                report.append(entry)
                continue
            entry["items"] = len(items)
            for item in items:
                publisher = item.get("source") or ""
                cand = Candidate(url=item["link"], title=clean_title(item["title"], publisher),
                                 channel="news_rss", source_name=publisher or provider,
                                 source_type="news", snippet=item["summary"][:2000],
                                 published_at=item["published"],
                                 date_provenance="feed" if item["published"] else "unknown")
                cand.skip_reason = date_gate(cand, window, tz)
                if provider == "bing_rss":
                    cand.url = unwrap_bing_link(cand.url)
                elif not cand.skip_reason:
                    # Resolution costs two requests; only spend them on in-window items.
                    if google_budget <= 0:
                        cand.skip_reason = "Google News link not resolved (daily resolution cap reached)"
                    else:
                        google_budget -= 1
                        resolved = decode_google_news_link(cand.url, http)
                        if resolved:
                            cand.url = resolved
                        else:
                            cand.skip_reason = "Google News link could not be resolved to the publisher"
                            entry["unresolved"] += 1
                if not cand.skip_reason:
                    entry["in_window"] += 1
                candidates.append(cand)
            report.append(entry)
    return candidates, report


# --------------------------------------------------------------------------- #
# Papers (OpenAlex)
# --------------------------------------------------------------------------- #

def openalex_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    return " ".join(w for _, w in sorted(positions))


def collect_papers(papers_cfg, http: DiscoveryHttp, window: EditionWindow, tz,
                   api_key: str | None = None) -> tuple[list[Candidate], list[dict]]:
    params = [
        "filter=" + quote(f"from_publication_date:{window.start},to_publication_date:{window.end},"
                          f"title_and_abstract.search:{papers_cfg.query}", safe=":,"),
        f"per-page={min(200, papers_cfg.max_results)}",
        "sort=publication_date:desc",
        "select=id,doi,title,publication_date,abstract_inverted_index,primary_location,type",
    ]
    if papers_cfg.mailto:
        params.append("mailto=" + quote(papers_cfg.mailto))
    if api_key:
        params.append("api_key=" + quote(api_key))
    url = "https://api.openalex.org/works?" + "&".join(params)
    entry = {"name": "OpenAlex papers", "status": "ok", "items": 0, "in_window": 0, "no_abstract": 0}
    try:
        resp = http.get(url, accept="application/json")
        data = json.loads(resp.body.decode("utf-8", errors="replace"))
    except (DiscoveryHttpError, ValueError) as exc:
        entry.update(status="failed", error=str(exc).replace(api_key or "\0", "[REDACTED]"))
        return [], [entry]
    results = data.get("results") or []
    entry["items"] = len(results)
    candidates = []
    for work in results:
        title = " ".join((work.get("title") or "").split())
        loc = work.get("primary_location") or {}
        venue = ((loc.get("source") or {}).get("display_name")) or "OpenAlex"
        link = loc.get("landing_page_url") or work.get("doi") or work.get("id")
        abstract = openalex_abstract(work.get("abstract_inverted_index"))
        published = None
        try:
            published = datetime.combine(datetime.fromisoformat(work["publication_date"]).date(),
                                         time(12), tzinfo=tz)
        except (KeyError, TypeError, ValueError):
            pass
        text = f"{title}\n\nJournal/venue: {venue}\nDOI: {work.get('doi') or 'not stated'}\n\nAbstract: {abstract}"
        cand = Candidate(url=link or "", title=title, channel="papers", source_name=venue,
                         source_type="paper", snippet=abstract[:1500],
                         text=text if abstract else "", published_at=published,
                         date_provenance="api" if published else "unknown")
        cand.skip_reason = date_gate(cand, window, tz)
        if not cand.skip_reason and not abstract:
            cand.skip_reason = "paper has no accessible abstract; title alone cannot support an analysis"
            entry["no_abstract"] += 1
        if not cand.skip_reason:
            entry["in_window"] += 1
        if cand.url:
            candidates.append(cand)
    return candidates, [entry]
