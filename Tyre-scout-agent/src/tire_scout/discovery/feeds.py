"""RSS 2.0 / Atom / RDF parsing and feed autodiscovery, without extra dependencies.

Feed items are the most reliable dated source for daily news: the publisher
states the publication time for every item.
"""
from __future__ import annotations

import html as html_lib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def parse_feed_date(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    raw = str(raw).strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                from dateutil import parser
                dt = parser.parse(raw)
            except (ValueError, OverflowError, TypeError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def strip_html(fragment: str) -> str:
    if not fragment:
        return ""
    if "<" not in fragment:
        return " ".join(html_lib.unescape(fragment).split())
    soup = BeautifulSoup(fragment, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _item_from_element(item, base_url: str) -> dict:
    out = {"title": "", "link": "", "summary": "", "content": "", "published": None,
           "updated": None, "source": ""}
    for child in item:
        name = _local(child.tag)
        if name == "title":
            out["title"] = strip_html(_text(child))
        elif name == "link":
            href = child.get("href")
            rel = (child.get("rel") or "alternate").lower()
            if href and rel == "alternate":
                out["link"] = urljoin(base_url, href)
            elif href and not out["link"]:
                out["link"] = urljoin(base_url, href)
            elif not href and _text(child):
                out["link"] = _text(child)
        elif name in ("guid", "id") and not out["link"]:
            value = _text(child)
            if value.startswith("http") and child.get("isPermaLink", "true") != "false":
                out["_guid"] = value
        elif name in ("description", "summary"):
            out["summary"] = strip_html(_text(child))
        elif name in ("encoded", "content"):
            out["content"] = strip_html(_text(child))
        elif name in ("pubdate", "published", "issued", "date"):
            out["published"] = out["published"] or parse_feed_date(_text(child))
        elif name in ("updated", "modified"):
            out["updated"] = parse_feed_date(_text(child))
        elif name == "source":
            out["source"] = _text(child)
    if not out["link"] and out.get("_guid"):
        out["link"] = out["_guid"]
    out.pop("_guid", None)
    return out


def parse_feed(body: bytes | str, base_url: str = "") -> list[dict]:
    """Return items with title, link, summary, content, published (aware dt)."""
    raw = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    raw = raw.lstrip("﻿ \r\n\t")
    try:
        root = ET.fromstring(raw.encode("utf-8"))
    except ET.ParseError:
        return _parse_feed_lenient(raw, base_url)
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    return [i for i in (_item_from_element(el, base_url) for el in items) if i["link"]]


def _parse_feed_lenient(raw: str, base_url: str) -> list[dict]:
    """Fallback for feeds with undeclared HTML entities or stray markup."""
    # html.parser treats <link> as a void element; rename it to keep its text.
    raw = re.sub(r"<(/?)link(\s|>|/)", r"<\1feedlink\2", raw, flags=re.IGNORECASE)
    soup = BeautifulSoup(raw, "html.parser")
    out = []
    for item in soup.find_all(["item", "entry"]):
        def first(*names):
            for n in names:
                el = item.find(n)
                if el is not None:
                    return el
            return None
        link_el = first("feedlink")
        link = ""
        if link_el is not None:
            link = link_el.get("href") or link_el.get_text(strip=True)
        title_el = first("title")
        summary_el = first("description", "summary")
        content_el = first("content:encoded", "content")
        date_el = first("pubdate", "published", "dc:date", "issued")
        out.append({
            "title": strip_html(title_el.get_text() if title_el else ""),
            "link": urljoin(base_url, link) if link else "",
            "summary": strip_html(summary_el.get_text() if summary_el else ""),
            "content": strip_html(content_el.get_text() if content_el else ""),
            "published": parse_feed_date(date_el.get_text() if date_el else None),
            "updated": None,
            "source": (first("source").get_text(strip=True) if first("source") else ""),
        })
    return [i for i in out if i["link"]]


def looks_like_feed(body: bytes | str, content_type: str = "") -> bool:
    ctype = (content_type or "").lower()
    if any(t in ctype for t in ("rss", "atom", "xml")):
        return True
    head = (body[:600].decode("utf-8", errors="replace") if isinstance(body, bytes) else body[:600]).lower()
    return "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def discover_feed_links(html: str, base_url: str) -> list[str]:
    """<link rel="alternate" type="application/rss+xml"> autodiscovery."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("link"):
        rel = " ".join(tag.get("rel") or []).lower() if isinstance(tag.get("rel"), list) else str(tag.get("rel") or "").lower()
        ctype = str(tag.get("type") or "").lower()
        href = tag.get("href")
        if href and "alternate" in rel and ("rss" in ctype or "atom" in ctype):
            url = urljoin(base_url, href)
            # Comment feeds are not news.
            if "comments" not in url.lower():
                links.append(url)
    return list(dict.fromkeys(links))
