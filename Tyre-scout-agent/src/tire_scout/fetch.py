"""Controlled URL fetching.

Defence in depth for outbound requests:
- http/https only; no credentials in URLs; no .local/.internal hosts
- DNS resolution checked against private/loopback/reserved ranges
- redirects are followed MANUALLY so every hop is re-validated (no DNS-rebind
  or redirect-to-internal bypass)
- size and time caps; content-type driven extraction with quality flags
Paywalls/auth are respected: a blocked page is recorded as such, never bypassed.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from .dedup import canonicalize_url, content_hash, extract_stable_ids
from .schemas import FetchedDocument

MAX_REDIRECTS = 5
MAX_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT_S = 20
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".lan", ".corp", "localhost")

DATE_META = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|dc\.date|'
    r'citation_publication_date|og:published_time)["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE)


@dataclass
class FetchResponse:
    status: int
    headers: dict
    body: bytes
    url: str


def _host_is_blocked(host: str) -> bool:
    h = host.lower().strip("[]")
    if h.endswith(BLOCKED_HOST_SUFFIXES):
        return True
    try:  # literal IP in the URL
        return _ip_blocked(ipaddress.ip_address(h))
    except ValueError:
        return False


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified)


def default_resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return sorted({i[4][0] for i in infos})


class Fetcher:
    """Injectable transport/resolver make the policy testable offline."""

    def __init__(self, *, timeout_s: int = DEFAULT_TIMEOUT_S, max_bytes: int = MAX_BYTES,
                 resolve=default_resolve, transport=None, allow_ocr: bool = False):
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.resolve = resolve
        self.transport = transport or self._httpx_transport
        self.allow_ocr = allow_ocr

    def _httpx_transport(self, url: str) -> FetchResponse:
        import httpx
        with httpx.Client(timeout=self.timeout_s, follow_redirects=False,
                          headers={"User-Agent": "tire-scout/0.1 (research bot)"}) as client:
            r = client.get(url)
        return FetchResponse(r.status_code, dict(r.headers), r.content, str(r.url))

    # -- policy -------------------------------------------------------------- #
    def validate_url(self, url: str) -> str | None:
        """Return an error string if the URL must not be fetched, else None."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return "unparseable URL"
        if parts.scheme not in ("http", "https"):
            return f"scheme not allowed: {parts.scheme or '(none)'}"
        if not parts.hostname:
            return "missing host"
        if parts.username or parts.password:
            return "credentials in URL are not allowed"
        if _host_is_blocked(parts.hostname):
            return f"blocked host: {parts.hostname}"
        try:
            ips = self.resolve(parts.hostname)
        except OSError as exc:
            return f"DNS resolution failed: {exc}"
        if not ips:
            return "DNS returned no addresses"
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return f"invalid resolved address: {ip_str}"
            if _ip_blocked(ip):
                return f"resolves to non-public address {ip_str}"
        return None

    # -- fetch ---------------------------------------------------------------- #
    def fetch(self, url: str) -> FetchedDocument:
        canon = canonicalize_url(url)
        doc = FetchedDocument(url=canon, status="failed")
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            err = self.validate_url(current)
            if err:
                doc.status = "blocked"
                doc.error = err
                return doc
            try:
                resp = self.transport(current)
            except Exception as exc:
                doc.error = f"transport error: {exc}"
                return doc
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location") or resp.headers.get("Location")
                if not location:
                    doc.error = "redirect without location"
                    return doc
                current = _absolutize(current, location)
                continue
            if resp.status in (401, 402, 403):
                doc.status = "paywalled" if resp.status in (401, 402) else "blocked"
                doc.error = f"HTTP {resp.status} (access restricted; not bypassed)"
                return doc
            if resp.status != 200:
                doc.error = f"HTTP {resp.status}"
                return doc
            if len(resp.body) > self.max_bytes:
                doc.error = f"response exceeds {self.max_bytes} bytes"
                return doc
            # Canonical identity is for deduplication only, never a fetch target.
            doc.final_url = resp.url or current
            return self._extract(doc, resp)
        doc.error = f"too many redirects (>{MAX_REDIRECTS})"
        return doc

    # -- extraction ------------------------------------------------------------- #
    def _extract(self, doc: FetchedDocument, resp: FetchResponse) -> FetchedDocument:
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        doc.content_type = ctype
        if ctype in ("application/pdf",) or resp.body[:5] == b"%PDF-":
            return self._extract_pdf(doc, resp.body)
        if ctype.startswith("text/html") or ctype in ("", "application/xhtml+xml"):
            return self._extract_html(doc, resp.body)
        if ctype.startswith("video") or ctype.startswith("audio"):
            doc.status = "unsupported"
            doc.error = f"media type {ctype} requires a transcript; not analyzed"
            return doc
        doc.status = "unsupported"
        doc.error = f"unsupported content type: {ctype or 'unknown'}"
        return doc

    def _extract_html(self, doc: FetchedDocument, body: bytes) -> FetchedDocument:
        html = body.decode("utf-8", errors="replace")
        title, text = _html_to_text(html)
        doc.title = title
        doc.text = text
        doc.text_len = len(text)
        doc.status = "fetched"
        from .page_metadata import extract_page_metadata
        metadata = extract_page_metadata(html, doc.final_url or doc.url)
        doc = FetchedDocument.model_validate({**doc.model_dump(), **metadata})
        if doc.text_len == 0:
            doc.extraction_quality = "none"
        elif doc.text_len < 1200 and re.search(r"\babstract\b", text[:1500], re.IGNORECASE):
            doc.extraction_quality = "abstract_only"
        elif doc.text_len < 400:
            doc.extraction_quality = "partial"
        else:
            doc.extraction_quality = "full"
        doc.content_hash = content_hash(text) if text else None
        doc.stable_ids = extract_stable_ids(text[:200000])
        return doc

    def _extract_pdf(self, doc: FetchedDocument, body: bytes) -> FetchedDocument:
        import io
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(body))
            pages = [(p.extract_text() or "") for p in reader.pages[:40]]
        except Exception as exc:
            doc.status = "failed"
            doc.error = f"PDF parse error: {exc}"
            return doc
        text = "\n".join(pages).strip()
        meta = getattr(reader, "metadata", None) or {}
        doc.title = str(meta.get("/Title") or "") or None
        doc.text = text
        doc.text_len = len(text)
        doc.status = "fetched"
        if doc.text_len >= 800:
            doc.extraction_quality = "full"
        elif doc.text_len > 0:
            doc.extraction_quality = "partial"
        else:
            # likely scanned images; OCR only if explicitly configured
            if self.allow_ocr:
                doc.extraction_quality = "ocr_low"
                doc.error = "scanned PDF; OCR attempted (low confidence)"
            else:
                doc.extraction_quality = "none"
                doc.error = "scanned PDF; OCR not enabled - content not analyzed"
        doc.content_hash = content_hash(text) if text else None
        doc.stable_ids = extract_stable_ids(text[:200000])
        return doc


def _absolutize(base: str, location: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, location)


def _html_to_text(html: str) -> tuple[str | None, str]:
    try:
        import trafilatura  # optional, higher quality
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            t = soup.title.string.strip() if soup.title and soup.title.string else None
            return t, text
    except ImportError:
        pass
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    return title, "\n".join(soup.stripped_strings)


def _parse_date(raw: str) -> datetime | None:
    from dateutil import parser as dp
    try:
        return dp.parse(raw)
    except (ValueError, OverflowError):
        return None
