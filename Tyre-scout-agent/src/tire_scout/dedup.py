"""Deduplication and development clustering.

Layers: canonical URL -> exact content hash -> stable identifier (DOI / patent
number) -> development-level text similarity. Syndicated copies of one event
cluster together and never count as independent corroboration; a genuine
follow-up stays separate only when it adds material evidence.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "spm",
}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PATENT_RE = re.compile(r"\b(?:US|WO|EP|CN|JP|KR|IN)\d{4,}[A-Z]?\d*[A-Z]?\d*\b")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return url.strip()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if k.lower() not in TRACKING_PARAMS]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(sorted(query)), ""))


def content_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_stable_ids(text: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    m = DOI_RE.search(text or "")
    if m:
        ids["doi"] = m.group(0).rstrip(".,);")
    m = PATENT_RE.search(text or "")
    if m:
        ids["patent_number"] = m.group(0)
    return ids


_STOP = set("the a an and or of for in on to with by from at as is are was were be new its it this that".split())


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity_key(title: str, what_changed: str | None) -> set[str]:
    return _tokens(f"{title} {what_changed or ''}")


class DedupDecision:
    def __init__(self, kind: str, cluster_id: str | None = None, reason: str = "", is_background: bool = False):
        self.kind = kind                    # new | duplicate | follow_up
        self.cluster_id = cluster_id
        self.reason = reason
        self.is_background = is_background

    def __repr__(self) -> str:  # pragma: no cover
        return f"DedupDecision({self.kind!r}, {self.reason!r})"


def decide(
    *,
    canonical_url: str,
    text_hash: str | None,
    stable_ids: dict[str, str],
    title: str,
    what_changed: str | None,
    published_at,
    history_window_days: int,
    prior: list[dict],
    similarity_threshold: float = 0.6,
    followup_requires_change: bool = True,
) -> DedupDecision:
    """Decide how a candidate relates to prior records.

    `prior` rows: dicts with canonical_url, content_hash, stable_ids (dict),
    title, what_changed, cluster_id, published_at, similarity tokens optional.
    """
    for row in prior:
        if row.get("canonical_url") == canonical_url:
            return DedupDecision("duplicate", row.get("cluster_id"), "same canonical URL")
    if text_hash:
        for row in prior:
            if row.get("content_hash") and row["content_hash"] == text_hash:
                return DedupDecision("duplicate", row.get("cluster_id"), "identical content hash")
    for key in ("doi", "patent_number"):
        if stable_ids.get(key):
            for row in prior:
                if (row.get("stable_ids") or {}).get(key) == stable_ids[key]:
                    return DedupDecision("duplicate", row.get("cluster_id"), f"same {key}")
    cand = _tokens(title)
    best, best_row = 0.0, None
    for row in prior:
        sim = jaccard(cand, row.get("tokens") or _tokens(row.get("title", "")))
        if sim > best:
            best, best_row = sim, row
    if best_row is not None and best >= similarity_threshold:
        wc_c = (what_changed or "").strip()
        wc_r = (best_row.get("what_changed") or "").strip()
        # a follow-up must add material change; when change info is unavailable
        # (e.g. title-level match at fetch time) high similarity == syndication
        changed = bool(wc_c and wc_r and wc_c != wc_r)
        if followup_requires_change and changed:
            return DedupDecision("follow_up", best_row.get("cluster_id"), f"similar story (J={best:.2f}) with material change")
        return DedupDecision("duplicate", best_row.get("cluster_id"), f"syndicated/repeated story (J={best:.2f})")
    background = False
    if published_at is not None and history_window_days > 0:
        from datetime import datetime, timedelta, timezone
        if isinstance(published_at, str):
            try:
                published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        if published_at is not None:
            age = datetime.now(timezone.utc) - (
                published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc))
            background = age > timedelta(days=history_window_days)
    return DedupDecision("new", None, "no match in history window", is_background=background)
