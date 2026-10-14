"""Preferred discovery origins and conservative classification of fetched pages.

An origin is not proof of a document type, peer review, or a technical claim.
Planner hints are deliberately never used for final classification.
"""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
MARKET_REPORT = re.compile(r'\bmarket\b.*\b(?:size|forecasts?|reports?|growth|trends|analysis|cagr|to reach)\b')


def host_matches(host, domain):
    return host == domain or host.endswith('.' + domain)


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str | None = None
    site: str | None = None
    source_type: str = 'news'
    tire_specific: bool = False


class SourceRegistry:
    def __init__(self, path):
        self.domains, self.warnings, self.feeds, self.exclude_domains = {}, [], [], []
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
        for raw in data.get('feeds') or []:
            if not isinstance(raw, dict) or not raw.get('name') or not (raw.get('url') or raw.get('site')):
                self.warnings.append(f'Feed entry ignored (needs name and url or site): {raw}')
                continue
            self.feeds.append(FeedSpec(name=str(raw['name']), url=raw.get('url'), site=raw.get('site'),
                                       source_type=str(raw.get('source_type') or 'news'),
                                       tire_specific=bool(raw.get('tire_specific', False))))
        for raw in data.get('exclude_domains') or []:
            domain = str(raw).lower().strip().removeprefix('www.')
            if DOMAIN.fullmatch(domain):
                self.exclude_domains.append(domain)
            else:
                self.warnings.append(f'Invalid excluded domain ignored: {raw}')
        for kind, entry in data.get('source_types', {}).items():
            valid = []
            for raw in entry.get('domains', []):
                domain = str(raw).lower().strip().removeprefix('www.')
                if not DOMAIN.fullmatch(domain):
                    self.warnings.append(f'Invalid preferred domain ignored: {raw}')
                elif domain not in valid:
                    valid.append(domain)
            self.domains[kind] = valid

    def preferred(self, kind):
        return self.domains.get(kind, [])[:30]

    def excluded(self, url):
        host = (urlsplit(url).hostname or '').lower()
        return any(host_matches(host, d) for d in self.exclude_domains)

    def origin(self, url):
        host = (urlsplit(url).hostname or '').lower()
        for kind, domains in self.domains.items():
            if any(host_matches(host, d) for d in domains):
                return kind
        return None


def classify_source(rec, registry, channel_type=None):
    """channel_type is the type implied by the discovery channel (a news index
    returns news articles, a bibliographic API returns papers). It is used only
    when page signals are inconclusive; it is never a model's guess."""
    url = rec.get('final_url') or rec.get('url', '')
    host = (urlsplit(url).hostname or '').lower()
    path = urlsplit(url).path.lower()
    title = (rec.get('title') or rec.get('search_title') or '').lower()
    hints = rec.get('page_metadata') or {}
    types = set(hints.get('schema_types') or [])
    origin = registry.origin(url)
    kind, reason = 'unknown', 'No reliable document-type signal; planner hint not used'
    if host_matches(host, 'patents.google.com') and re.search(r'/patent/[A-Z]{2}\d', path, re.I):
        kind, reason = 'patent', 'Patent publication URL on patent repository'
    elif origin == 'patent' and hints.get('patent_publication_number'):
        kind, reason = 'patent', 'Patent repository and publication-number metadata'
    elif host_matches(host, 'wikipedia.org') or re.search(r'/(?:about(?:-us)?|learn|tires-101|wiki)(?:/|$)', path):
        kind, reason = 'reference', 'Reference, educational, or organization overview page'
    elif MARKET_REPORT.search(title) or re.search(r'/industry-reports/|/market-reports/', path):
        kind, reason = 'market_report', 'Market-report title or URL; not a technical research paper'
    elif (hints.get('citation_title') and (hints.get('citation_doi') or hints.get('citation_journal_title'))) or 'ScholarlyArticle' in types:
        kind, reason = 'paper', 'Scholarly article metadata (peer review not established)'
    elif origin == 'paper' and re.search(r'/article/|/articles/|/doi/|/abs/|/paper/', path):
        kind, reason = 'paper', 'Research publisher/repository article URL (peer review not established)'
    elif origin == 'video' or 'VideoObject' in types or (rec.get('content_type') or '').startswith('video/'):
        kind, reason = 'video', 'Video host, media type or video metadata'
    elif origin in ('company', 'supplier', 'news', 'conference'):
        kind, reason = origin, f'Configured {origin} origin; document content still requires evaluation'
    elif 'NewsArticle' in types:
        kind, reason = 'news', 'Page declares NewsArticle metadata; claims remain unverified'
    elif channel_type in ('news', 'company', 'supplier', 'paper', 'conference'):
        kind, reason = channel_type, f'Discovered through a {channel_type} channel; claims remain unverified'
    return {'source_type': kind, 'source_origin': origin or 'unregistered',
            'classification_reason': reason, 'interpretation_policy': 'claims-1.0'}


def discovery_background_reason(hit):
    title, path = hit.get('title', '').lower(), urlsplit(hit.get('url', '')).path.lower()
    if MARKET_REPORT.search(title):
        return 'Discovery excluded market forecast/analysis; targeted technical developments'
    if re.search(r'/(?:about(?:-us)?|wiki|tires-101)(?:/|$)', path):
        return 'Discovery excluded generic reference/organization overview'
    return None
